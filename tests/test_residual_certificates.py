"""Closed-form residual certificates (ADR-0005 D3): states that used to
leave the reachability check undecided, so that the whole mask failed with
RESOURCE_LIMIT on vocabularies whose tokens end inside such a state."""

from __future__ import annotations

import json

import reference as ref
import blg_ctypes as blg


def spec_with(extra: list[bytes]) -> ref.TokenizerSpec:
    tokens = tuple(bytes([i]) for i in range(256)) + tuple(extra)
    return ref.TokenizerSpec(tokens=tokens + (b"",), eos_ids=(len(tokens),),
                             special_ids=()).validate()


def mask(schema, spec, prefix: bytes = b"") -> set[int]:
    ctx = blg.Context(spec)
    try:
        g = blg.compile_schema(ctx, json.dumps(schema).encode(), profile=b"spec-v1")
        s = blg.Session(ctx, g)
        for b in prefix:
            assert s.accept(b) == blg.BLG_OK
        status, words = s.fill_mask_words()
        s.destroy()
        blg.grammar_release(g)
        if status == blg.BLG_ERR_DEAD_END:
            return set()
        assert status == blg.BLG_OK
        return {i for i in range(spec.vocab_size) if words[i // 32] >> (i % 32) & 1}
    finally:
        ctx.destroy()


ESCAPES = [b'"\\', b'"\\u00', b'"\\u001']


def test_pattern_string_mid_escape():
    # The canonical escape table fixes what an unfinished escape can decode
    # to: \" \\ and U+0000..U+001F (\u00XX only where no short form exists).
    spec = spec_with(ESCAPES)
    esc, u00, u001 = (256 + i for i in range(3))
    assert mask({"type": "string", "pattern": "^[a-z]+$"}, spec) & {esc, u00, u001} == set()
    assert mask({"type": "string", "pattern": "^\\t?x$"}, spec) & {esc, u00, u001} == {esc}
    assert mask({"type": "string", "pattern": "^[\\x10-\\x1f]$"}, spec) & {esc, u00, u001} == {esc, u00, u001}
    assert mask({"type": "string", "pattern": "^\\x01$", "maxLength": 1}, spec) & {esc, u00, u001} == {esc, u00}


def test_string_minus_constants_mid_escape():
    spec = spec_with(ESCAPES)
    allowed = mask({"type": "string", "maxLength": 3, "not": {"enum": ["\n", "\t"]}}, spec)
    assert {256, 257, 258} <= allowed


def search_entered() -> int:
    n = blg.LIB.blg_cert_stats(None, 0)
    arr = (blg.ctypes.c_uint64 * n)()
    blg.LIB.blg_cert_stats(arr, n)
    return arr[n - 1]


def test_key_mid_codepoint_is_certified():
    # A key cut inside a UTF-8 sequence completes to a known code point
    # range; the key pattern decides it per DFA transition range.
    spec = spec_with([b'"\xd0', b'"a\xd0'])
    ascii_only = {"type": "object", "patternProperties": {"^[a-z]+$": {"type": "string"}},
                  "additionalProperties": False}
    cyrillic = {"type": "object", "patternProperties": {"^\u0436": {"type": "string"}},
                "additionalProperties": False}
    blg.LIB.blg_cert_stats_reset()
    assert mask(ascii_only, spec, b"{") & {256, 257} == set()
    assert mask(cyrillic, spec, b"{") & {256, 257} == {256}
    assert search_entered() == 0


def test_key_mid_escape_is_certified():
    spec = spec_with(ESCAPES)
    schema = {"type": "object", "patternProperties": {"^[a-z]+$": {"type": "string"}},
              "additionalProperties": False}
    blg.LIB.blg_cert_stats_reset()
    assert mask(schema, spec, b"{") & {256, 257, 258} == set()
    tab_key = {"type": "object", "patternProperties": {"^\\t": {"type": "string"}},
               "additionalProperties": False}
    assert mask(tab_key, spec, b"{") & {256, 257, 258} == {256}
    assert search_entered() == 0


def test_key_that_can_never_dispatch_is_refused():
    # The parser refuses only the closing quote of a key no property can
    # take; with no value filter on the object, every further key byte
    # stayed allowed and the document could never complete.
    spec = spec_with([])
    pattern_only = {"type": "object", "patternProperties": {"^[a-z]+$": {"type": "string"}},
                    "additionalProperties": False}
    allowed = mask(pattern_only, spec, b'{"')
    assert ord("Z") not in allowed and ord("a") in allowed
    assert ord('"') not in mask({"type": "object", "additionalProperties": False}, spec, b"{")


def allowed_bytes(schema, prefix: bytes = b"") -> set[int]:
    return mask(schema, spec_with([]), prefix) - {256}


def test_oneof_of_interchangeable_branches_is_dead():
    # Branches compiled from equal subschemas (true, {}, or untyped object
    # keywords that ignore scalars) vote in lockstep: after the first byte
    # of a scalar no completion matches exactly one branch.
    assert allowed_bytes({"oneOf": [True, True, True]}) == set()
    complex_types = {"oneOf": [{"properties": {"bar": {"type": "integer"}}, "required": ["bar"]},
                               {"properties": {"foo": {"type": "string"}}, "required": ["foo"]}]}
    assert allowed_bytes(complex_types) == {ord("{")}
    # Within a branch an integer reading beside a number reading does not
    # change the vote.
    number_or_any = allowed_bytes({"oneOf": [{"type": "number"}, {}]})
    assert ord("1") not in number_or_any and ord('"') in number_or_any


def test_if_then_else_shadowed_else_is_dead():
    # With the then path dead, the else path needs a value the if branch
    # rejects; an if branch that accepts whatever else accepts has none.
    schema = {"if": {}, "then": {"type": "null"}, "else": {}}
    assert allowed_bytes(schema) == {ord("n")}


def test_numeric_oneof_and_if_then_else():
    # oneOf {integer, minimum 2} after `2`: 2 x 10^k is either an integer
    # >= 2 or a non-integer < 2, so `e` is dead while `.` and digits are not.
    after_two = allowed_bytes({"oneOf": [{"type": "integer"}, {"minimum": 2}]}, b"2")
    assert ord("e") not in after_two and {ord("."), ord("5")} <= after_two
    ite = {"if": {"exclusiveMaximum": 0}, "then": {"minimum": -10}, "else": {"multipleOf": 2}}
    assert {ord("0"), ord("1"), ord(".")} <= allowed_bytes(ite, b"-1")


def test_triggered_dependent_schema_rejecting_the_prefix_is_dead():
    schema = {"dependentSchemas": {"bar": {"properties": {"foo": {"type": "integer"}}}}}
    assert ord('"') not in allowed_bytes(schema, b'{"foo":"quux","bar')
    assert ord('"') in allowed_bytes(schema, b'{"foo":1,"bar')


def test_dependent_required_keys_follow_the_dispatch_order():
    # The trigger fired and both names are still missing: typing "c" first
    # skips the declared "b" (parser: a prop before next_idx is out of
    # order), and a skipped optional prop never dispatches again, so the
    # dependency could never be satisfied. The certificate must decide
    # this without the bounded search, whose outcome depends on the
    # vocabulary (a failing state used to end the whole mask on Llama-3.1
    # tokens that end mid-key).
    schema = {"$schema": "http://json-schema.org/draft-04/schema#",
              "type": "object",
              "properties": {"a": {"type": "boolean", "enum": [True]},
                             "b": {"type": "integer"},
                             "c": {"type": "integer"}},
              "additionalProperties": False,
              "dependencies": {"a": ["c", "b"]}}
    spec = spec_with([b'"b', b'"c'])
    b_key, c_key = 256, 257
    blg.LIB.blg_cert_stats_reset()
    allowed = mask(schema, spec, b'{"a":true,')
    assert b_key in allowed and c_key not in allowed
    assert search_entered() == 0


def test_property_names_constrain_key_prefixes():
    assert allowed_bytes({"propertyNames": False}, b"{") == {ord("}")}
    assert ord("b") not in allowed_bytes({"propertyNames": {"maxLength": 3}}, b'{"foo')


def test_numeric_combinator_cross_check():
    import subprocess
    import sys
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, BLG_LIB_PATH=blg.LIB._path)
    out = subprocess.run([sys.executable, os.path.join(here, "numcomb_cert_check.py"),
                          "--schemas", "12", "--seed", "7", "--max-len", "3"],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stdout[-2000:]


def test_in_flight_key_dependencies_join_the_witness():
    # A key typed into a closed object whose dependencies require later
    # keys: the witness completes the key, then the required keys in
    # declared order, without the budgeted search.
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"isTest": {"enum": [True]}, "counts": {"type": "array"},
                             "tiebreak": {"type": "integer"}, "results": {"type": "object"}},
              "dependencies": {"isTest": ["results", "tiebreak", "counts"]}}
    spec = spec_with([b'"is'])
    blg.LIB.blg_cert_stats_reset()
    assert 256 in mask(schema, spec, b"{")
    assert search_entered() == 0


def test_alternatives_owing_keys_the_closed_object_has_passed():
    # anyOf {required content} / {required embeds} next to a closed object:
    # a key after embeds in declared order leaves neither obligation
    # reachable.
    schema = {"type": "object", "additionalProperties": False,
              "anyOf": [{"required": ["content"]}, {"required": ["embeds"]}],
              "properties": {"content": {"type": "string"}, "tts": {"type": "boolean"},
                             "embeds": {"type": "array"}, "allowed_mentions": {"type": "object"}}}
    allowed = allowed_bytes(schema, b'{"tts":false,"')
    assert ord("e") in allowed and ord("a") not in allowed


def test_witness_for_a_later_key_of_a_closed_object():
    # A token that closes one value and opens the next key: the witness
    # continues after the keys already typed instead of restarting at the
    # object's first property.
    schema = {"type": "object", "additionalProperties": False, "required": ["name"],
              "properties": {"name": {"type": "string"},
                             "ssn": {"type": "string", "pattern": "^[0-9]{3}-[0-9]{2}-[0-9]{4}$"},
                             "ein": {"type": "string", "pattern": "^[0-9]{2}-[0-9]{7}$"}},
              "oneOf": [{"required": ["ssn"]}, {"required": ["ein"]}]}
    spec = spec_with([b'","'])
    blg.LIB.blg_cert_stats_reset()
    assert 256 in mask(schema, spec, b'{"name":"')
    assert search_entered() == 0


def test_alternatives_owing_keys_beside_a_closed_object_with_its_own_oneof():
    # Github_hard/o90970 (GitLab CI job): allOf of a closed object that has
    # its own oneOf, and an anyOf of required keys. The oneOf threads do not
    # block a key themselves; they fail with the closed object's allOf.
    schema = {"allOf": [
        {"type": "object", "additionalProperties": False,
         "properties": {"script": {}, "extends": {}, "tags": {}, "when": {}, "start_in": {}},
         "oneOf": [{"properties": {"when": {"enum": ["delayed"]}}, "required": ["when", "start_in"]},
                   {"properties": {"when": {"not": {"enum": ["delayed"]}}}}]},
        {"anyOf": [{"required": ["script"]}, {"required": ["extends"]}]}]}
    allowed = allowed_bytes(schema, b'{"')
    assert ord("s") in allowed and ord("e") in allowed
    assert ord("t") not in allowed and ord("w") not in allowed


def test_required_key_an_open_branch_has_passed_in_declared_order():
    # Github_trivial/o35155: anyOf of open objects declaring "type" before
    # "value", next to required ["type", "value"]. Once "value" closes, no
    # branch accepts "type" any more (declared order), so the required key
    # can never arrive: the closing quote is dead, a longer key is not.
    schema = {"anyOf": [{"properties": {"type": {"enum": [t]},
                                        "value": {"type": "string", "pattern": t + "-\\d+"}}}
                        for t in ("A", "B", "C")],
              "required": ["type", "value"]}
    allowed = allowed_bytes(schema, b'{"value')
    assert ord('"') not in allowed and ord("X") in allowed
    assert ord('"') in allowed_bytes(schema, b'{"type":"A","value')


def test_key_mid_codepoint_whose_shortest_completion_is_a_seen_key():
    # JsonSchemaStore/cypress.schema.json: the in-flight key "x" + 0xC5 is
    # completed with the lowest continuation byte to "xŀ", a key the
    # object already has; the witness extends it instead of entering the
    # budgeted search.
    schema = {"type": "object", "allOf": [{"properties": {"u": {"type": "string"}}, "required": ["u"]},
                                          {"properties": {"b": {}}}]}
    blg.LIB.blg_cert_stats_reset()
    assert 0xC5 in allowed_bytes(schema, '{"xŀ":"","x'.encode())
    assert search_entered() == 0


def test_key_whose_short_completions_are_all_seen():
    # Github_medium/o85188: the in-flight key "xx" spells a key the object
    # has, and so does its first one-letter extension "xxa".
    schema = {"type": "object", "allOf": [{"properties": {"u": {"type": "string"}}, "required": ["u"]},
                                          {"properties": {"b": {}}}]}
    blg.LIB.blg_cert_stats_reset()
    assert ord("x") in allowed_bytes(schema, b'{"xx":"","xxa":"","x')
    assert search_entered() == 0


def test_oneof_witness_value_that_fails_a_rival_branch():
    # JsonSchemaStore/tye-schema.json: after "exe" the key "fn" is typed;
    # a string value would satisfy the "fn" branch beside the "exe" one,
    # while a value of another type leaves exactly one branch.
    def branch(k):
        return {"properties": {"name": {"type": "string"}, k: {"type": "string"}}, "required": ["name", k]}
    schema = {"type": "object", "oneOf": [branch("exe"), branch("fn"), branch("img")]}
    blg.LIB.blg_cert_stats_reset()
    assert ord('"') in allowed_bytes(schema, b'{"exe":"b","fn')
    assert search_entered() == 0
    assert ord(",") in allowed_bytes(schema, b'{"exe":"b","fn":1')


def test_if_then_objects_settle_without_the_search():
    # The completion witness did not handle objects under if/then/else, so
    # every token state of such an object reached the budgeted search
    # (147 of them for the mask after `{"` here).
    schema = {"type": "object", "required": ["type"], "properties": {"type": {"type": "string"}},
              "if": {"properties": {"type": {"enum": ["a"]}}}, "then": {"required": ["id"]}}
    for prefix in (b"{", b'{"', b'{"type":"'):
        blg.LIB.blg_cert_stats_reset()
        assert allowed_bytes(schema, prefix)
        assert search_entered() == 0, prefix


def test_closed_object_beside_required_alternatives_has_a_witness():
    # Github_hard/o32934 (devfile metadata): a closed object whose optional
    # props an anyOf of required names selects; the witness takes the value
    # of the chosen name from the closed object's declared schema.
    meta = {"type": "object", "additionalProperties": False,
            "anyOf": [{"required": ["name"]}, {"required": ["gen"]}],
            "properties": {"gen": {"type": "string", "minLength": 1},
                           "name": {"type": "string", "pattern": "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"}}}
    schema = {"type": "object", "properties": {"x": {"type": "array"}, "metadata": meta}, "required": ["metadata"]}
    blg.LIB.blg_cert_stats_reset()
    assert ord("]") in allowed_bytes(schema, b'{"x":[')
    assert search_entered() == 0
