"""spec-v1 P3 end-to-end tests (ROADMAP rev-2 P3).

Covers the boolean combinators through the C ABI under the spec-v1
profile: anyOf (plain union), oneOf/allOf/if-then-else (comb nodes,
verdict at the confirmed value boundary), and `not` over the supported
complement forms (type, const/enum over scalars, required, nested not,
De Morgan over anyOf/allOf). Refusals are UNSUPPORTED_FEATURE with a
JSON pointer into /not. canonical-v1 is frozen (it keeps refusing all
of these). The module is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

D4 = "http://json-schema.org/draft-04/schema"
D7 = "http://json-schema.org/draft-07/schema"


@pytest.fixture
def byte_ctx(byte_tok):
    ctx = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    yield ctx
    ctx.destroy()


def _compile(ctx, schema, profile=b"spec-v1"):
    return zg.compile_schema(ctx, json.dumps(schema).encode(), profile=profile)


def _accepts(ctx, grammar, doc: str) -> bool:
    s = zg.Session(ctx, grammar)
    try:
        for b in doc.encode():
            if s.accept(b) != zg.BLG_OK:
                return False
        return bool(s.can_end())
    finally:
        s.destroy()


def _check(ctx, schema, ok_docs, bad_docs):
    g = _compile(ctx, schema)
    try:
        for d in ok_docs:
            assert _accepts(ctx, g, d), f"must accept {d!r} under {schema}"
        for d in bad_docs:
            assert not _accepts(ctx, g, d), f"must reject {d!r} under {schema}"
    finally:
        zg.grammar_release(g)


def _refusal(ctx, schema, status, profile=b"spec-v1"):
    with pytest.raises(zg.CoreFailure) as ei:
        _compile(ctx, schema, profile=profile)
    assert status in str(ei.value)
    return str(ei.value)


# ---------------------------------------------------------------------------
# anyOf: a plain union
# ---------------------------------------------------------------------------

def test_anyof_union(byte_ctx):
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    _check(byte_ctx, schema,
           ['"x"', "1", "1.0", "-2"],
           ["1.5", "true", "null", "[1]", '{"a":1}'])


def test_anyof_empty_branch_drops_out(byte_ctx):
    _check(byte_ctx, {"anyOf": [False, {"type": "integer"}]},
           ["1", "2.0"], ['"x"', "1.5"])


def test_anyof_refusals(byte_ctx):
    _refusal(byte_ctx, {"anyOf": []}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"anyOf": [False]}, "UNSATISFIABLE_CONSTRAINT")
    _refusal(byte_ctx, {"anyOf": {"type": "integer"}}, "INVALID_SCHEMA")


# ---------------------------------------------------------------------------
# oneOf: exactly one branch accepts
# ---------------------------------------------------------------------------

def test_oneof_number_vs_integer(byte_ctx):
    schema = {"oneOf": [{"type": "number"}, {"type": "integer"}]}
    _check(byte_ctx, schema,
           ["1.5", "-0.5", "1e-1"],
           # An integer is accepted by BOTH branches: the group rejects.
           ["1", "2", "1.0", "1e2", '"x"', "true"])


def test_oneof_single_live_branch(byte_ctx):
    _check(byte_ctx, {"oneOf": [False, {"type": "integer"}]},
           ["1", "2.0"], ["1.5", '"x"'])


def test_oneof_refusals(byte_ctx):
    _refusal(byte_ctx, {"oneOf": []}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"oneOf": [False]}, "UNSATISFIABLE_CONSTRAINT")


# ---------------------------------------------------------------------------
# allOf: every branch accepts
# ---------------------------------------------------------------------------

def test_allof_intersection(byte_ctx):
    schema = {"allOf": [{"type": "integer"}, {"const": 5}]}
    _check(byte_ctx, schema,
           ["5", "5.0", "5e0", "50e-1"],
           ["6", "5.5", '"5"', "true"])


def test_allof_empty_branch(byte_ctx):
    _refusal(byte_ctx,
             {"allOf": [{"type": "integer"},
                        {"type": "string", "minLength": 1, "maxLength": 0}]},
             "UNSATISFIABLE_CONSTRAINT")


def test_allof_sweep_mid_array(byte_ctx):
    # The const branch dies on the second element; the allOf group is
    # doomed from there, so no document with a mismatching element passes.
    schema = {"type": "array",
              "items": {"allOf": [{"type": "integer"}, {"const": 5}]}}
    _check(byte_ctx, schema,
           ["[5,5]", "[]", "[5.0]"],
           ["[5,6]", "[6]", '["5"]'])


# ---------------------------------------------------------------------------
# if/then/else
# ---------------------------------------------------------------------------

def test_if_then_else(byte_ctx):
    schema = {"if": {"const": 0}, "then": {"const": 1}, "else": {"type": "number"}}
    _check(byte_ctx, schema,
           ["1", "2", "2.5", "-3"],
           # 0 matches if but not then; a string matches neither then-
           # branch nor the numeric else.
           ["0", "0.0", '"x"', "true"])


def test_if_then_without_else(byte_ctx):
    # Absent else = true, compiled to an AnyJSON carrier branch: values
    # the if/then branches cannot even parse still validate when the
    # condition fails.
    schema = {"if": {"const": 0}, "then": {"const": 1}}
    _check(byte_ctx, schema,
           ["1", "2", "2.5", '"x"', "[1]", "null"],
           ["0", "0.0", "0e0"])


def test_if_then_else_draft04_ignored(byte_ctx):
    schema = {"$schema": D4, "if": False, "then": False, "else": False}
    _check(byte_ctx, schema, ["1", '"x"', "[1]"], [])


def test_then_without_if_is_inert(byte_ctx):
    _check(byte_ctx, {"then": {"const": 1}}, ["1", "2", '"x"'], [])


# ---------------------------------------------------------------------------
# not
# ---------------------------------------------------------------------------

def test_not_type_integer(byte_ctx):
    schema = {"not": {"type": "integer"}}
    _check(byte_ctx, schema,
           ["1.5", '"x"', "true", "null", "[1]", '{"a":1}', "1e-1"],
           # Integers by VALUE: 1.0 and 1e2 are integers (spec-v1 4.4).
           ["1", "1.0", "1e2", "-3"])


def test_not_const_string(byte_ctx):
    schema = {"not": {"const": "ab"}}
    _check(byte_ctx, schema,
           ['"abc"', '"a"', '"abx"', "1", "true", "[1]"],
           ['"ab"'])


def test_not_enum_numbers(byte_ctx):
    schema = {"not": {"enum": [1, 2]}}
    _check(byte_ctx, schema,
           ["3", "1.5", '"1"', "null"],
           # Numeric equality is by value.
           ["1", "2", "1.0", "2e0", "10e-1"])


def test_not_required(byte_ctx):
    # Complement of required: exactly the objects missing the key;
    # non-objects satisfy `required`, so `not` rejects them.
    schema = {"not": {"required": ["a"]}}
    _check(byte_ctx, schema,
           ['{"b":1}', "{}", '{"a":1,"b":2}'.replace('"a":1,', "")],
           ['{"a":1}', '{"a":null}', "1", '"x"', "[1]"])


def test_not_double_negation(byte_ctx):
    _check(byte_ctx, {"not": {"not": {"type": "integer"}}},
           ["1", "2.0"], ["1.5", '"x"'])


def test_not_de_morgan(byte_ctx):
    # not anyOf = allOf of the complements.
    schema = {"not": {"anyOf": [{"type": "integer"}, {"type": "string"}]}}
    _check(byte_ctx, schema,
           ["1.5", "true", "null", "[1]"],
           ["1", "2.0", '"x"'])
    # not allOf = anyOf of the complements: integers other than 5, plus
    # every non-integer value.
    schema2 = {"not": {"allOf": [{"type": "integer"}, {"const": 5}]}}
    _check(byte_ctx, schema2,
           ["6", "1.5", '"x"', "true"],
           ["5", "5.0"])


def test_not_empty_language_forms(byte_ctx):
    _refusal(byte_ctx, {"not": {}}, "UNSATISFIABLE_CONSTRAINT")
    _refusal(byte_ctx, {"not": True}, "UNSATISFIABLE_CONSTRAINT")


def test_not_refusals_carry_pointer(byte_ctx):
    msg = _refusal(byte_ctx, {"not": {"pattern": "x"}}, "UNSUPPORTED_FEATURE")
    assert "/not/pattern" in msg
    msg = _refusal(byte_ctx, {"not": {"enum": [1, 2, 3]}}, "UNSUPPORTED_FEATURE")
    assert "/not/enum" in msg
    _refusal(byte_ctx, {"not": {"enum": [[1]]}}, "UNSUPPORTED_FEATURE")
    _refusal(byte_ctx, {"not": {"type": "object", "minLength": 1}},
             "UNSUPPORTED_FEATURE")


# ---------------------------------------------------------------------------
# Nesting and partial documents
# ---------------------------------------------------------------------------

def test_nested_combinators(byte_ctx):
    schema = {"oneOf": [
        {"allOf": [{"type": "integer"}, {"const": 5}]},
        {"not": {"type": "number"}}]}
    _check(byte_ctx, schema,
           # Only the allOf branch accepts 5; only the not branch accepts
           # non-numbers: exactly one branch in both cases.
           ["5", "5.0", '"x"', "true", "[1]"],
           # 6 satisfies neither branch.
           ["6", "1.5"])


def test_combinator_inside_object(byte_ctx):
    schema = {"type": "object",
              "properties": {"k": {"oneOf": [{"type": "number"},
                                             {"type": "integer"}]}},
              "required": ["k"]}
    _check(byte_ctx, schema,
           ['{"k":1.5}'],
           ['{"k":1}', '{"k":"x"}', "{}"])


def test_truncated_documents_reject(byte_ctx):
    g = _compile(byte_ctx, {"allOf": [{"type": "integer"}, {"const": 5}]})
    try:
        for doc in ["5e", "5.", "-", "5e+"]:
            assert not _accepts(byte_ctx, g, doc), f"must reject truncated {doc!r}"
    finally:
        zg.grammar_release(g)
    g = _compile(byte_ctx, {"type": "object",
                            "properties": {"k": {"not": {"const": "ab"}}}})
    try:
        for doc in ['{"k":"ab', '{"k"', '{"k":"']:
            assert not _accepts(byte_ctx, g, doc), f"must reject truncated {doc!r}"
    finally:
        zg.grammar_release(g)


def test_str_excl_mask_excludes_forbidden_close(byte_ctx):
    # After the live prefix "ab the closing quote would spell the
    # forbidden value: the mask must exclude it, while other
    # continuations stay.
    g = _compile(byte_ctx, {"not": {"const": "ab"}})
    try:
        s = zg.Session(byte_ctx, g)
        try:
            for b in b'"ab':
                assert s.accept(b) == zg.BLG_OK
            ids = s.fill_mask_ids()
            assert ord('"') not in ids
            assert ord("c") in ids
        finally:
            s.destroy()
        # "a is not forbidden: the quote closes a valid string.
        s = zg.Session(byte_ctx, g)
        try:
            for b in b'"a':
                assert s.accept(b) == zg.BLG_OK
            assert ord('"') in s.fill_mask_ids()
        finally:
            s.destroy()
    finally:
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# canonical-v1 is frozen; $ref siblings refuse
# ---------------------------------------------------------------------------

def test_canonical_v1_still_refuses_combinators(byte_ctx):
    for kw in ({"anyOf": [{"type": "string"}]},
               {"oneOf": [{"type": "string"}]},
               {"allOf": [{"type": "string"}]},
               {"not": {"type": "string"}},
               {"if": {"type": "string"}, "then": {}}):
        _refusal(byte_ctx, kw, "UNSUPPORTED_FEATURE", profile=b"canonical-v1")


def test_ref_sibling_combinator_conjoins(byte_ctx):
    # 2020-12 sibling rule (P6b): the $ref expansion and the anyOf sibling
    # conjoin - integer ∧ (integer ∨ string) is integer.
    schema = {"$defs": {"d": {"type": "integer"}},
              "$ref": "#/$defs/d",
              "anyOf": [{"type": "integer"}, {"type": "string"}]}
    _check(byte_ctx, schema, ["5", "-1"], ['"s"', "5.5", "null"])
    # A contradictory sibling compiles to the (runtime-empty) conjunction:
    # nothing is accepted.
    _check(byte_ctx,
           {"$defs": {"d": {"type": "integer"}},
            "$ref": "#/$defs/d",
            "anyOf": [{"type": "string"}]},
           [], ["5", '"s"'])
