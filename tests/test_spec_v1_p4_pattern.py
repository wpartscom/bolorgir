"""spec-v1 P4 end-to-end tests (ROADMAP rev-2 P4.1): regex `pattern`.

Covers the ECMA-262-subset `pattern` keyword through the C ABI under the
spec-v1 profile: unanchored-search semantics, anchors, classes,
quantifiers, alternation, the product with minLength/maxLength, decoded
string values (escapes and multi-byte UTF-8 feed codepoints, not bytes),
enum/const filtering, full-regex patternProperties, and the refusal
surface (unsupported constructs UNSUPPORTED_FEATURE and malformed regexes
INVALID_SCHEMA, both with the exact JSON pointer). canonical-v1 is frozen
(it keeps refusing `pattern`). The module is skipped until the core is
built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")


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
# pattern: search semantics, anchors, classes, quantifiers
# ---------------------------------------------------------------------------

def test_pattern_unanchored_search(byte_ctx):
    # JSON Schema `pattern` is an unanchored search: the match may sit
    # anywhere in the decoded string value.
    _check(byte_ctx, {"type": "string", "pattern": "a.c"},
           ['"xxayczz"', '"abc"', '"a c"'],
           ['"ac"', '"ab"', '""'])


def test_pattern_anchors(byte_ctx):
    _check(byte_ctx, {"type": "string", "pattern": "^a+$"},
           ['"a"', '"aaa"'],
           ['""', '"ba"', '"ab"', '"aab"'])
    _check(byte_ctx, {"type": "string", "pattern": "b$"},
           ['"b"', '"ab"', '"aab"'],
           ['"ba"', '""'])
    _check(byte_ctx, {"type": "string", "pattern": "^abc$"},
           ['"abc"'],
           ['"xabc"', '"abcx"', '"ab"'])


def test_pattern_classes_and_quantifiers(byte_ctx):
    _check(byte_ctx, {"type": "string", "pattern": "^\\d{3}-\\d{4}$"},
           ['"123-4567"'],
           ['"12-4567"', '"123-45678"', '"12a-4567"'])
    _check(byte_ctx, {"type": "string", "pattern": "^[a-z_][a-z0-9_]{2,}$"},
           ['"abc"', '"_a1"'],
           ['"Abc"', '"ab"', '"1abc"'])
    _check(byte_ctx, {"type": "string", "pattern": "^[^xyz]+$"},
           ['"abc"', '""'.replace('""', '"q"')],
           ['"ax"', '"z"'])
    _check(byte_ctx, {"type": "string", "pattern": "^(foo|bar)+$"},
           ['"foo"', '"foobar"', '"barbarfoo"'],
           ['"fo"', '"baz"', '"fooo"'])


def test_pattern_dot_excludes_line_terminators(byte_ctx):
    # '.' excludes U+000A U+000D U+2028 U+2029; [^] includes them.
    _check(byte_ctx, {"type": "string", "pattern": "^a.b$"},
           ['"aXb"', '"a b"'],
           ['"a\\nb"', '"a\\rb"', '"ab"'])
    _check(byte_ctx, {"type": "string", "pattern": "^a[^]b$"},
           ['"aXb"', '"a\\nb"'],
           ['"ab"'])


def test_pattern_length_bounds_product(byte_ctx):
    # minLength/maxLength are counted in codepoints and conjoin with the
    # pattern (ROADMAP P4.1: the product with the length bounds).
    _check(byte_ctx, {"type": "string", "pattern": "^a+$", "minLength": 2, "maxLength": 3},
           ['"aa"', '"aaa"'],
           ['"a"', '"aaaa"', '"ab"'])
    # A pattern contradictory to the bounds is unsatisfiable at the root.
    _refusal(byte_ctx, {"type": "string", "pattern": "^aaaa$", "maxLength": 3},
             "UNSATISFIABLE")
    # A pattern matching nothing at all is an empty language at the root.
    _refusal(byte_ctx, {"type": "string", "pattern": "a^b"}, "UNSATISFIABLE")


def test_pattern_decoded_values_escapes(byte_ctx):
    # The pattern sees the DECODED value: \n feeds U+000A, \\ feeds 0x5C,
    # \u00XX feeds its control codepoint.
    _check(byte_ctx, {"type": "string", "pattern": "^a\\nb$"},
           ['"a\\nb"'], ['"anb"', '"a\\tb"'])
    _check(byte_ctx, {"type": "string", "pattern": "^x\\\\y$"},
           ['"x\\\\y"'], ['"xy"'])
    _check(byte_ctx, {"type": "string", "pattern": "^a\\u0001$"},
           ['"a\\u0001"'], ['"a\\u0002"', '"a"'])
    # \t in the pattern is the tab character; the document spells it \\t.
    _check(byte_ctx, {"type": "string", "pattern": "^\\w+\\t\\w+$"},
           ['"ab\\tcd"'], ['"abcd"'])


def test_pattern_unicode_codepoints(byte_ctx):
    # Multi-byte UTF-8 feeds one scalar value per character.
    _check(byte_ctx, {"type": "string", "pattern": "^é.$"},
           ['"éx"'], ['"é"', '"ex"', '"ééx"'])
    _check(byte_ctx, {"type": "string", "pattern": "^.$", "minLength": 1},
           ['"é"', '"😀"'.encode().decode()], ['""'])
    # A 4-byte emoji is ONE codepoint for both pattern and length bounds.
    _check(byte_ctx, {"type": "string", "pattern": "^..$"},
           ['"😀x"'], ['"😀"'])
    _check(byte_ctx, {"type": "string", "pattern": "^[\\u0400-\\u04FF]+$"},
           ['"\u041F\u0440\u0438\u0432\u0435\u0442"'], ['"Privet"'])


def test_pattern_with_enum(byte_ctx):
    # pattern filters the string values of enum/const like the length
    # bounds do.
    _check(byte_ctx, {"enum": ["ab", "bbc", "dd"], "pattern": "b$"},
           ['"ab"'], ['"bbc"', '"dd"', '"ac"'])
    _refusal(byte_ctx, {"const": "ab", "pattern": "^b$"}, "UNSATISFIABLE")


def test_pattern_in_containers(byte_ctx):
    schema = {"type": "object",
              "properties": {"k": {"type": "string", "pattern": "^a+$"}},
              "required": ["k"], "additionalProperties": False}
    _check(byte_ctx, schema,
           ['{"k":"aaa"}'],
           ['{"k":"ab"}', '{"k":""}', '{"k":1}'])
    arr = {"type": "array", "items": {"type": "string", "pattern": "^\\d+$"}}
    _check(byte_ctx, arr,
           ['["1","23"]', "[]"],
           ['["1","a"]', '["x"]'])


def test_pattern_without_type(byte_ctx):
    # Inapplicable to non-strings: only the string arm is constrained.
    _check(byte_ctx, {"pattern": "^a+$"},
           ['"a"', '"aaa"', "1", "1.5", "true", "null", "[1]", '{"x":1}'],
           ['"ab"', '""'])


def test_pattern_inside_combinators(byte_ctx):
    _check(byte_ctx,
           {"oneOf": [{"type": "string", "pattern": "^a+$"},
                      {"type": "string", "pattern": "^b+$"}]},
           ['"aaa"', '"bbb"'],
           ['"ab"', '""', '"aab"'])
    _check(byte_ctx,
           {"allOf": [{"type": "string", "pattern": "a"},
                      {"type": "string", "pattern": "b"}]},
           ['"ab"', '"xaybz"'],
           ['"ax"', '"bx"', '"xy"'])
    _check(byte_ctx,
           {"if": {"type": "string"}, "then": {"pattern": "^a"}},
           ['"abc"', "1", "true"],
           ['"bc"'])


# ---------------------------------------------------------------------------
# patternProperties: full regex (P4)
# ---------------------------------------------------------------------------

def test_pattern_properties_regex(byte_ctx):
    schema = {"type": "object",
              "patternProperties": {"^x": {"type": "integer"}},
              "additionalProperties": {"type": "string"}}
    _check(byte_ctx, schema,
           ['{"x1":1}', '{"xy":1,"other":"s"}', '{}'],
           ['{"x1":"s"}', '{"other":1}'])


def test_pattern_properties_regex_classes(byte_ctx):
    schema = {"patternProperties": {"^[A-Z][a-z]*$": {"type": "boolean"}}}
    _check(byte_ctx, schema,
           ['{"Name":true}', '{}'],
           ['{"Name":1}'])
    # A non-matching key falls back to additionalProperties (absent = any).
    _check(byte_ctx, schema, ['{"name":1}'], [])


def test_pattern_properties_decoded_key(byte_ctx):
    # The regex applies to the DECODED key value: a key containing a
    # newline (raw bytes \n) must match \n in the pattern, not the 'n'.
    schema = {"patternProperties": {"^a\\nb$": {"type": "integer"}}}
    _check(byte_ctx, schema,
           ['{"a\\nb":1}'],
           ['{"a\\nb":"s"}'])
    _check(byte_ctx, {"patternProperties": {"^anb$": {"type": "integer"}}},
           ['{"anb":1}', '{"a\\nb":"anything"}'],
           ['{"anb":"s"}'])


def test_pattern_properties_trivial_value_gates_key_admission(byte_ctx):
    # A trivial pattern value ({}) places no constraint, but the pattern
    # still decides which keys additionalProperties governs: a matching key
    # is NOT an additional property (JSON Schema 2020-12).
    schema = {"properties": {"foo": {}},
              "patternProperties": {"^v": {}},
              "additionalProperties": False}
    _check(byte_ctx, schema,
           ['{"foo":1}', '{"foo":1,"vroom":2}', '{"v":null}'],
           ['{"foo":1,"quux":"boom"}'])
    schema2 = {"patternProperties": {"^é": {}}, "additionalProperties": False}
    _check(byte_ctx, schema2,
           ['{"éx":2}', '{}'],
           ['{"ax":2}'])
    schema3 = {"patternProperties": {"^v": {}},
               "additionalProperties": {"type": "integer"}}
    _check(byte_ctx, schema3,
           ['{"v1":"anything"}', '{"other":1}'],
           ['{"other":"s"}'])


def test_pattern_properties_still_refusals(byte_ctx):
    # Several patterns and declared-key overlap need the intersection (P3).
    _refusal(byte_ctx, {"patternProperties": {"^a": {}, "b$": {}}},
             "UNSUPPORTED_FEATURE")
    _refusal(byte_ctx,
             {"properties": {"xa": {}}, "patternProperties": {"^x": {}}},
             "UNSUPPORTED_FEATURE")


# ---------------------------------------------------------------------------
# refusals: pointer-exact
# ---------------------------------------------------------------------------

def test_pattern_refusals_carry_the_pointer(byte_ctx):
    msg = _refusal(byte_ctx, {"type": "string", "pattern": "(?=a)b"},
                   "UNSUPPORTED_FEATURE")
    assert " at /pattern" in msg
    msg = _refusal(byte_ctx, {"type": "string", "pattern": "(a)\\1"},
                   "UNSUPPORTED_FEATURE")
    assert " at /pattern" in msg
    msg = _refusal(byte_ctx, {"type": "string", "pattern": "\\p{L}+"},
                   "UNSUPPORTED_FEATURE")
    assert " at /pattern" in msg
    msg = _refusal(byte_ctx, {"type": "string", "pattern": "^\\bword"},
                   "UNSUPPORTED_FEATURE")
    assert " at /pattern" in msg
    msg = _refusal(byte_ctx, {"type": "string", "pattern": "a{1001}"},
                   "UNSUPPORTED_FEATURE")
    assert " at /pattern" in msg
    # Malformed regexes are INVALID_SCHEMA with the pointer.
    msg = _refusal(byte_ctx, {"type": "string", "pattern": "(a"}, "INVALID_SCHEMA")
    assert " at /pattern" in msg
    msg = _refusal(byte_ctx, {"type": "string", "pattern": "[z-a]"}, "INVALID_SCHEMA")
    assert " at /pattern" in msg
    # A non-string pattern value is INVALID_SCHEMA.
    msg = _refusal(byte_ctx, {"type": "string", "pattern": 5}, "INVALID_SCHEMA")
    assert " at /pattern" in msg
    # The pointer tracks nesting.
    msg = _refusal(byte_ctx,
                   {"type": "object",
                    "properties": {"k": {"type": "string", "pattern": "(?=a)"}}},
                   "UNSUPPORTED_FEATURE")
    assert " at /properties/k/pattern" in msg
    msg = _refusal(byte_ctx, {"patternProperties": {"a(?=b)": {}}},
                   "UNSUPPORTED_FEATURE")
    assert " at /patternProperties/a(?=b)" in msg
    # pattern next to $ref is an assertion sibling (2020-12, P6b): the two
    # conjoin (string ∧ pattern).
    _check(byte_ctx,
           {"$defs": {"d": {"type": "string"}}, "$ref": "#/$defs/d", "pattern": "x"},
           ['"x"', '"xyz"'], ['"abc"', "5"])


def test_pattern_canonical_v1_frozen(byte_ctx):
    # canonical-v1 keeps refusing `pattern` (bit-for-bit frozen profile).
    _refusal(byte_ctx, {"type": "string", "pattern": "x"}, "UNSUPPORTED_FEATURE",
             profile=b"canonical-v1")
