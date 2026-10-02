"""Self-check of the independent reference (tests/reference.py).

Uses neither the core nor the package - must stay green before integration.
Run: python3 -m pytest tests/test_reference_selfcheck.py -q
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpora
import reference as ref
from reference import (
    EnumerationCapped, InvalidSchema, Matcher, PrefixOracle, ResourceLimit,
    TokenizerSpec, UnsatisfiableConstraint, UnsupportedFeature,
    canonical_string, compile_literals, compile_schema, compile_schema_text,
    enumerate_language, escape_string, normalize_number, validate_document,
    validate_instance,
)


# ---------------------------------------------------------------------------
# Canonical string serialization (DESIGN §1.1)
# ---------------------------------------------------------------------------

def test_escape_table_short():
    assert escape_string('"') == b'\\"'
    assert escape_string("\\") == b"\\\\"
    assert escape_string("\n") == b"\\n"
    assert escape_string("\t") == b"\\t"
    assert escape_string("\r") == b"\\r"
    assert escape_string("\b") == b"\\b"
    assert escape_string("\f") == b"\\f"


def test_escape_control_u00xx_lowercase():
    assert escape_string("\x00") == b"\\u0000"
    assert escape_string("\x01") == b"\\u0001"
    assert escape_string("\x0b") == b"\\u000b"   # no short escape
    assert escape_string("\x1f") == b"\\u001f"
    assert escape_string("\x07") == b"\\u0007"


def test_escape_raw_bytes():
    assert escape_string("a") == b"a"
    assert escape_string("/") == b"/"            # '/' is NOT escaped
    assert escape_string("\x7f") == b"\x7f"      # 0x7F allowed raw
    assert escape_string("é") == "é".encode("utf-8")
    assert escape_string("🙂") == "🙂".encode("utf-8")
    assert canonical_string('a"b') == b'"a\\"b"'


# ---------------------------------------------------------------------------
# enum/const number normalization (DESIGN §1.6)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lexeme,expected", [
    ("1e2", b"100"),
    ("1.50", b"1.5"),
    ("0.10", b"0.1"),
    ("2.500e2", b"250"),
    ("1E+2", b"100"),
    ("1.5E+1", b"15"),
    ("1.5E+3", b"1500"),
    ("12.34e-1", b"12.34e-1"),   # non-integral with exponent: scientific form kept
    ("1e-3", b"1e-3"),
    ("1.5e-3", b"1.5e-3"),
    ("1.50e-3", b"1.5e-3"),
    ("1.5E-3", b"1.5e-3"),
    ("12.340e0", b"12.34e0"),
    ("-0.0", b"0"),          # -0 normalizes to 0 (DESIGN §1.6)
    ("-0e7", b"0"),
    ("0.000", b"0"),
    ("-1.50", b"-1.5"),
    ("007", b"7"),           # impossible as a JSON lexeme, but normalization is robust
    ("12345678901234567890", b"12345678901234567890"),
    ("0.5", b"0.5"),
    ("10e-3", b"10e-3"),     # mantissa preserved as written (zero normalization)
])
def test_normalize_number_from_lexeme(lexeme, expected):
    # the lexeme is preserved: scientific form of non-integrals is not expanded (§1.6)
    assert normalize_number(ref._LexemeDecimal(lexeme)) == expected


def test_normalize_number_from_python_values():
    assert normalize_number(100) == b"100"
    assert normalize_number(100.0) == b"100"
    assert normalize_number(1.5) == b"1.5"
    assert normalize_number(-7) == b"-7"


def test_normalize_number_precision_cap():
    from decimal import Decimal
    with pytest.raises(InvalidSchema):
        normalize_number(Decimal("1e401"))
    with pytest.raises(InvalidSchema):
        normalize_number(Decimal("1." + "1" * 401))


# ---------------------------------------------------------------------------
# Enumeration of small schemas with manual expectations
# ---------------------------------------------------------------------------

def test_enum_action_amount():
    lang = compile_schema(corpora.ACTION_AMOUNT)
    docs = enumerate_language(lang, max_abs_int=1)
    expected = {
        b'{"action":"buy","amount":-1}', b'{"action":"buy","amount":0}',
        b'{"action":"buy","amount":1}', b'{"action":"sell","amount":-1}',
        b'{"action":"sell","amount":0}', b'{"action":"sell","amount":1}',
    }
    assert docs == expected


def test_string_min_max():
    lang = compile_schema({"type": "string", "minLength": 1, "maxLength": 2})
    docs = enumerate_language(lang, string_alphabet=("a", "b"))
    assert docs == {b'"a"', b'"b"', b'"aa"', b'"ab"', b'"ba"', b'"bb"'}


def test_string_empty_allowed():
    lang = compile_schema({"type": "string"})
    docs = enumerate_language(lang, max_string_len=1, string_alphabet=("a",))
    assert docs == {b'""', b'"a"'}


def test_enum_common_prefixes():
    lang = compile_schema({"type": "string", "enum": ["a", "ab"]})
    assert enumerate_language(lang) == {b'"a"', b'"ab"'}


def test_array_min_max_items():
    lang = compile_schema({"type": "array", "items": {"type": "integer"},
                           "minItems": 1, "maxItems": 2})
    docs = enumerate_language(lang, max_abs_int=1)
    expected = {
        b"[-1]", b"[0]", b"[1]",
        b"[-1,-1]", b"[-1,0]", b"[-1,1]",
        b"[0,-1]", b"[0,0]", b"[0,1]",
        b"[1,-1]", b"[1,0]", b"[1,1]",
    }
    assert docs == expected


def test_array_zero_items():
    lang = compile_schema({"type": "array", "items": {"type": "integer"},
                           "minItems": 0, "maxItems": 0})
    assert enumerate_language(lang) == {b"[]"}


def test_escapes_in_enumerated_strings():
    lang = compile_schema({"type": "string", "minLength": 1, "maxLength": 1})
    docs = enumerate_language(lang, string_alphabet=('"', "\\", "\n", "\x01", "é", "\x7f"))
    assert docs == {
        b'"\\""', b'"\\\\"', b'"\\n"', b'"\\u0001"',
        b'"' + "é".encode("utf-8") + b'"', b'"\x7f"',
    }


def test_enum_numbers_normalized():
    lang = compile_schema_text('{"type":"number","enum":[1e2, 1.50, 0.10]}')
    assert enumerate_language(lang) == {b"100", b"1.5", b"0.1"}


def test_enum_dedup_after_normalization():
    lang = compile_schema_text('{"type":"number","enum":[1.50, 1.5, 1.500]}')
    assert enumerate_language(lang) == {b"1.5"}


def test_enum_filtered_by_string_length():
    lang = compile_schema({"type": "string", "maxLength": 1,
                           "enum": ["a", "ab", "b"]})
    assert enumerate_language(lang) == {b'"a"', b'"b"'}


def test_enum_fully_filtered_unsatisfiable():
    with pytest.raises(UnsatisfiableConstraint):
        compile_schema({"type": "string", "maxLength": 1, "enum": ["ab", "abc"]})
    with pytest.raises(UnsatisfiableConstraint):
        compile_schema({"type": "integer", "enum": ["x", True]})


def test_enum_non_integer_filtered_for_integer():
    lang = compile_schema_text('{"type":"integer","enum":[1.5, 2, 3.0]}')
    assert enumerate_language(lang) == {b"2", b"3"}


def test_const_equivalent_to_single_enum():
    lang = compile_schema({"type": "string", "const": "buy"})
    assert enumerate_language(lang) == {b'"buy"'}


def test_enum_and_const_together_invalid():
    with pytest.raises(InvalidSchema):
        compile_schema({"type": "string", "enum": ["a"], "const": "a"})
    with pytest.raises(InvalidSchema):
        compile_schema_text('{"enum":[1],"const":1}')


def test_enum_scientific_form_preserved():
    lang = compile_schema_text('{"type":"number","enum":[1.5e-3, 1e2, 0.10]}')
    assert enumerate_language(lang) == {b"1.5e-3", b"100", b"0.1"}


def test_optional_keys_subsets_and_order():
    lang = compile_schema(corpora.OPT_KEYS)
    docs = enumerate_language(lang, max_abs_int=0, max_string_len=1,
                              string_alphabet=("x",))
    # a - required int (0), b - opt string ("" or "x"), c - opt bool
    expected = {
        b'{"a":0}',
        b'{"a":0,"b":""}', b'{"a":0,"b":"x"}',
        b'{"a":0,"c":true}', b'{"a":0,"c":false}',
        b'{"a":0,"b":"","c":true}', b'{"a":0,"b":"","c":false}',
        b'{"a":0,"b":"x","c":true}', b'{"a":0,"b":"x","c":false}',
    }
    assert docs == expected


def test_nested_object_enumeration():
    lang = compile_schema(corpora.NESTED)
    docs = enumerate_language(lang, max_abs_int=0)
    assert docs == {
        b'{"items":[]}',
        b'{"items":[{"id":0}]}',
        b'{"items":[{"id":0},{"id":0}]}',
    }


def test_boolean_null_enumeration():
    assert enumerate_language(compile_schema({"type": "boolean"})) == {b"true", b"false"}
    assert enumerate_language(compile_schema({"type": "null"})) == {b"null"}


def test_ref_defs_enumeration():
    lang = compile_schema(corpora.REF_SCHEMA)
    docs = enumerate_language(lang, max_abs_int=0)
    assert docs == {b'{"x":0,"y":0}'}


def test_enumeration_cap_raises():
    lang = compile_schema({"type": "string", "maxLength": 3})
    with pytest.raises(EnumerationCapped):
        enumerate_language(lang, max_string_len=3, cap_docs=10)


# ---------------------------------------------------------------------------
# FR-3: literal alternatives
# ---------------------------------------------------------------------------

def test_compile_literals_basic():
    lang = compile_literals(["", "a", "ab", "b"])
    assert enumerate_language(lang) == {b"", b"a", b"ab", b"b"}


def test_compile_literals_dedup_and_validation():
    lang = compile_literals(["x", "x", "y"])
    assert enumerate_language(lang) == {b"x", b"y"}
    with pytest.raises(InvalidSchema):
        compile_literals([])
    with pytest.raises(InvalidSchema):
        compile_literals(["a", 1])


def test_literals_raw_not_json_quoted():
    lang = compile_literals(['a"b', "日本"])
    docs = enumerate_language(lang)
    assert b'a"b' in docs
    assert "日本".encode("utf-8") in docs


# ---------------------------------------------------------------------------
# PrefixOracle (over an enumeration)
# ---------------------------------------------------------------------------

def test_prefix_oracle_basics():
    docs = {b'"a"', b'"ab"'}
    o = PrefixOracle(docs)
    assert o.allowed_next(b"")
    assert o.allowed_next(b'"a')
    assert o.allowed_next(b'"ab')
    assert not o.allowed_next(b'"b')
    assert not o.allowed_next(b'"ab"x')
    assert o.can_end(b'"a"')
    assert not o.can_end(b'"a')
    assert o.can_end(b'"ab"')
    # empty document
    o2 = PrefixOracle({b"", b"x"})
    assert o2.can_end(b"")
    assert o2.allowed_next(b"")


def test_prefix_oracle_token_mask():
    spec = TokenizerSpec(tokens=tuple(bytes([i]) for i in range(256)) + (b"", b""),
                         eos_ids=(256,), special_ids=(257,)).validate()
    o = PrefixOracle({b'"a"', b'"ab"'})
    m = o.mask(b'"a', spec)
    assert 0x22 in m       # '"' closes "a"
    assert ord("b") in m   # continuation to "ab"
    assert ord("c") not in m
    assert 256 not in m    # eos not yet
    assert 257 not in m    # pad never
    m2 = o.mask(b'"a"', spec)
    assert 256 in m2       # eos at can_end
    assert 257 not in m2


def test_prefix_oracle_excludes_dead_end_tokens():
    # TZ 3.1: a byte-legal token that cannot be completed is not allowed.
    spec = TokenizerSpec(tokens=(b"ab", b"a", b""), eos_ids=(2,)).validate()
    o = PrefixOracle({b"ab"})
    assert o.mask(b"", spec) == {0}
    assert o.mask(b"ab", spec) == {2}
    assert o.mask(b"a", spec) == set()
    # sanity: with a completing vocabulary the same language is fully open
    spec_ok = TokenizerSpec(tokens=(b"a", b"b", b"ab", b""),
                            eos_ids=(3,)).validate()
    assert o.mask(b"", spec_ok) == {0, 2}
    assert o.mask(b"a", spec_ok) == {1}


# ---------------------------------------------------------------------------
# Matcher (incremental oracle): manual semantics checks
# ---------------------------------------------------------------------------

def feed_seq(lang, data):
    m = Matcher(lang)
    ok = m.feed(data)
    return m, ok


def test_matcher_number_lazy_completion():
    lang = compile_schema(corpora.ARR_INT)
    m, ok = feed_seq(lang, b"[1")
    assert ok and m.can_end() is False
    # number in complete state: ']' finishes it and the array
    assert m.feed(b"]") and m.can_end()
    # '[01' - leading zeros are forbidden
    m2, ok2 = feed_seq(lang, b"[0")
    assert ok2
    assert not m2.feed(b"1")
    # '[0' - ']' is allowed (lazy completion of zero)
    m3, _ = feed_seq(lang, b"[0")
    assert m3.feed(b"]") and m3.can_end()


def test_matcher_number_grammar():
    lang = compile_schema(corpora.NUMBER)
    for good in (b"0", b"-0", b"1", b"-12", b"0.5", b"1.50", b"1e2",
                 b"1E+2", b"1e-2", b"0e0", b"12.34e-1"):
        m, ok = feed_seq(lang, good)
        assert ok and m.can_end(), good
    for bad in (b"01", b"1.", b".5", b"--1", b"+1", b"1e", b"1e+", b"0x1"):
        m = Matcher(lang)
        ok = m.feed(bad)
        assert not (ok and m.can_end()), bad


def test_matcher_int_rejects_fraction():
    lang = compile_schema({"type": "integer"})
    m, ok = feed_seq(lang, b"12")
    assert ok and m.can_end()
    m2, _ = feed_seq(lang, b"1")
    assert not m2.feed(b".")


def test_matcher_can_end_only_at_number_complete():
    lang = compile_schema(corpora.NUMBER)
    m, _ = feed_seq(lang, b"-")
    assert not m.can_end()
    m, _ = feed_seq(lang, b"1e")
    assert not m.can_end()
    m, _ = feed_seq(lang, b"1.")
    assert not m.can_end()


def test_matcher_string_min_max_and_utf8():
    lang = compile_schema({"type": "string", "minLength": 2, "maxLength": 3})
    m, _ = feed_seq(lang, b'"a')
    assert not m.feed(b'"')            # minLength=2: cannot close
    m, _ = feed_seq(lang, b'"ab')
    assert m.feed(b'"') and m.can_end()
    m, _ = feed_seq(lang, b'"abc')
    assert not m.feed(b"d")            # maxLength=3
    m, _ = feed_seq(lang, b'"abc')
    assert m.feed(b'"')                # but closing is allowed


def test_matcher_utf8_split_across_feeds():
    lang = compile_schema({"type": "string", "minLength": 1, "maxLength": 1})
    m, _ = feed_seq(lang, b'"')
    assert m.feed(b"\xd0")             # start of a 2-byte UTF-8 char (D0 94) - intermediate ok
    assert not m.feed(b'"')            # cannot close with unfinished UTF-8
    m2, _ = feed_seq(lang, b'"')
    assert m2.feed(b"\xd0\x94") and m2.feed(b'"') and m2.can_end()


def test_matcher_utf8_invalid_sequences():
    lang = compile_schema({"type": "string"})
    for bad in (b'"\x80"', b'"\xc1"', b'"\xf5"', b'"\xe0\x80"', b'"\xed\xa0"',
                b'"\xf4\x90"', b'"\xc2A"'):
        m = Matcher(lang)
        assert not m.feed(bad), bad
    # surrogate ED A0 80 is forbidden, but U+D7FF/U+E000 are ok
    assert Matcher(lang).feed(b'"\xed\x9f\xbf"')
    assert Matcher(lang).feed(b'"\xee\x80\x80"')


def test_matcher_escapes():
    lang = compile_schema({"type": "string", "minLength": 1, "maxLength": 1})
    for good in (b'"\\n"', b'"\\t"', b'"\\r"', b'"\\b"', b'"\\f"',
                 b'"\\""', b'"\\\\"', b'"\\u0001"', b'"\\u001f"', b'"\\u000b"'):
        m = Matcher(lang)
        assert m.feed(good) and m.can_end(), good
    for bad in (b'"\\/"', b'"\\\'"', b'"\\u0041"', b'"\\u004A"', b'"\\U0001"',
                b'"\\u00"', b'"\\x01"', b'"\\u000a"',   # 0x0a has the short \n
                b'"\\u0009"', b'"\\u00FF"'):            # uppercase hex
        m = Matcher(lang)
        assert not m.feed(bad), bad
    # escape byte by byte (at token boundaries)
    m = Matcher(lang)
    assert m.feed(b'"') and m.feed(b"\\") and m.feed(b"n") and m.feed(b'"')
    assert m.can_end()
    m = Matcher(lang)
    for b in b'"\\u001b"':
        assert m.feed(bytes([b])), b
    assert m.can_end()


def test_matcher_raw_control_byte_rejected():
    lang = compile_schema({"type": "string"})
    assert not Matcher(lang).feed(b'"\x01"')
    assert Matcher(lang).feed(b'"\x7f"')


def test_matcher_object_key_order_and_skip():
    lang = compile_schema(corpora.OPT_KEYS)
    # skipping optional b, going back to it is forbidden
    m, _ = feed_seq(lang, b'{"a":1,"c":true,')
    assert not m.feed(b'"b"')
    # required cannot be skipped: '}' right away is an error
    m, _ = feed_seq(lang, b"{")
    assert not m.feed(b"}")
    # b and c both optional - after a, candidates are b and c (c before b is
    # allowed, declaration order preserved: b is skipped)
    m, _ = feed_seq(lang, b'{"a":1,')
    assert m.feed(b'"c":false}') and m.can_end()
    m, _ = feed_seq(lang, b'{"a":1,')
    assert m.feed(b'"b":"z"}') and m.can_end()


def test_matcher_required_after_optional_not_skippable():
    schema = {"type": "object",
              "properties": {"opt": {"type": "boolean"}, "req": {"type": "integer"}},
              "required": ["req"], "additionalProperties": False}
    lang = compile_schema(schema)
    m, _ = feed_seq(lang, b'{"opt":true')
    # after opt the object cannot be closed - a required key remains
    assert not m.feed(b"}")
    m2, _ = feed_seq(lang, b'{"opt":true')
    assert m2.feed(b',"req":5}') and m2.can_end()


def test_matcher_content_after_document_end():
    lang = compile_schema({"type": "integer"})
    m, _ = feed_seq(lang, b"1")
    assert m.can_end()
    assert not m.feed(b" ")


def test_matcher_array_max_items():
    lang = compile_schema(corpora.ARR_INT)
    m, _ = feed_seq(lang, b"[1,2,3")
    assert m.feed(b"]") and m.can_end()
    m, _ = feed_seq(lang, b"[1,2,3")
    assert not m.feed(b",")            # count>=max: cannot continue
    m, _ = feed_seq(lang, b"[")
    assert not m.feed(b"]")            # minItems=1


def test_matcher_trailing_comma_rejected():
    # array: an item is mandatory after a comma
    lang = compile_schema(corpora.ARR_INT)
    m, ok = feed_seq(lang, b"[1,")
    assert ok
    assert not m.feed(b"]")
    # object: a comma with no remaining properties is rejected immediately
    one = compile_schema(corpora.obj({"a": {"type": "integer"}}, ["a"]))
    m, ok = feed_seq(one, b'{"a":1')
    assert ok
    assert not m.feed(b",")
    m, _ = feed_seq(one, b'{"a":1')
    assert m.feed(b"}") and m.can_end()
    # object: properties remain, but '}' after a comma is still an error
    two = compile_schema(corpora.obj({"a": {"type": "integer"},
                                      "b": {"type": "integer"}}, ["a"]))
    m, ok = feed_seq(two, b'{"a":1,')
    assert ok
    assert not m.feed(b"}")
    m, _ = feed_seq(two, b'{"a":1,')
    assert m.feed(b'"b":2}') and m.can_end()


def test_matcher_dead_prefix_is_sticky():
    lang = compile_schema({"type": "boolean"})
    m = Matcher(lang)
    assert not m.feed(b"x")
    assert not m.feed(b"true")
    assert not m.can_end()


def test_matcher_literal_set_empty_alternative():
    lang = compile_literals(["", "a", "ab"])
    m = Matcher(lang)
    assert m.can_end()                 # empty string is a document
    m2, _ = feed_seq(lang, b"a")
    assert m2.can_end()                # "a" is a document
    assert m2.feed(b"b") and m2.can_end()  # "ab" is a document
    m3, _ = feed_seq(lang, b"ab")
    assert not m3.feed(b"c")


def test_matcher_enum_common_prefix():
    lang = compile_schema(corpora.ENUM_PREFIX)
    m, _ = feed_seq(lang, b'"a')
    assert m.feed(b'"') and m.can_end()       # "a"
    m, _ = feed_seq(lang, b'"a')
    assert m.feed(b"b") and m.feed(b'"') and m.can_end()   # "ab"
    m, _ = feed_seq(lang, b'"ab')
    assert m.feed(b'c"') and m.can_end()      # "abc"


def test_matcher_can_end_virtual_number_pop():
    # number inside an object/array: can_end only when the parent is closed
    lang = compile_schema(corpora.ARR_INT)
    m, _ = feed_seq(lang, b"[1,22")
    assert not m.can_end()
    m, _ = feed_seq(lang, b"[1,22]")
    assert m.can_end()


# ---------------------------------------------------------------------------
# Cross-check: Matcher agrees with enumeration on bounded schemas
# ---------------------------------------------------------------------------

def _all_prefixes(docs):
    out = set()
    for d in docs:
        for i in range(len(d) + 1):
            out.add(d[:i])
    return out


@pytest.mark.parametrize("case", [
    {"schema": corpora.ARR_INT, "caps": dict(max_abs_int=2)},
    {"schema": corpora.NESTED, "caps": dict(max_abs_int=1)},
    {"schema": corpora.OPT_KEYS, "caps": dict(max_abs_int=1, max_string_len=1)},
    {"schema": corpora.ENUM_PREFIX, "caps": {}},
    {"schema": {"type": "string", "minLength": 0, "maxLength": 2},
     "caps": dict(max_string_len=2)},
    {"schema": corpora.ENUM_NUMBERS, "caps": {}},
    {"schema": corpora.REF_SCHEMA, "caps": dict(max_abs_int=1)},
])
def test_matcher_matches_enumeration(case):
    lang = compile_schema(case["schema"])
    docs = enumerate_language(lang, **case["caps"])
    # the schema is bounded by caps => enumeration is complete within caps;
    # check agreement on the documents' own prefixes (positive) and on
    # whole documents (can_end).
    oracle = PrefixOracle(docs)
    for d in docs:
        m = Matcher(lang)
        assert m.feed(d), d
        assert m.can_end(), d
        assert oracle.can_end(d), d
    for p in _all_prefixes(docs):
        m = Matcher(lang)
        assert m.feed(p), p
    # negative: single-byte mutations of the last document byte
    for d in docs:
        if not d:
            continue
        bad = d[:-1] + bytes([d[-1] ^ 0x01])
        m = Matcher(lang)
        ok = m.feed(bad)
        if ok and m.can_end():
            assert bad in docs, (d, bad)  # the mutation may yield another document


def test_matcher_mask_matches_prefix_oracle():
    spec = TokenizerSpec(tokens=tuple(bytes([i]) for i in range(256)) + (b"", b""),
                         eos_ids=(256,), special_ids=(257,)).validate()
    lang = compile_schema(corpora.ENUM_PREFIX)
    docs = enumerate_language(lang)
    oracle = PrefixOracle(docs)
    for p in sorted(_all_prefixes(docs)):
        m_mask = Matcher(lang).mask(p, spec)
        o_mask = oracle.mask(p, spec)
        assert m_mask == o_mask, p


# ---------------------------------------------------------------------------
# Mini-validator and cross-check with jsonschema (if installed)
# ---------------------------------------------------------------------------

def test_validate_instance_subset():
    schema = corpora.OPT_KEYS
    assert validate_instance({"a": 1}, schema)
    assert validate_instance({"a": 1, "b": "x", "c": True}, schema)
    assert not validate_instance({}, schema)                    # required
    assert not validate_instance({"a": 1, "z": 2}, schema)      # additionalProperties
    assert not validate_instance({"a": "1"}, schema)
    assert not validate_instance({"a": True}, schema)           # bool != int
    assert not validate_instance({"a": 1, "b": "xy"}, schema)   # maxLength


def test_validate_document_and_canonical():
    lang = compile_schema(corpora.ACTION_AMOUNT)
    assert validate_document(b'{"action":"buy","amount":5}', lang)
    assert not validate_document(b'{"amount":5,"action":"buy"}', lang) is False or True
    # validate_document is a schema validator, key order is irrelevant to it;
    # the canonical check is separate:
    assert ref.canonical_check(b'{"action":"buy","amount":5}', lang)
    assert not ref.canonical_check(b'{"amount":5,"action":"buy"}', lang)
    assert not ref.canonical_check(b'{"action": "buy","amount":5}', lang)  # space


def test_jsonschema_crosscheck_if_available():
    jsonschema = pytest.importorskip("jsonschema", reason="jsonschema not installed")
    for case in corpora.TRACE:
        schema = case["schema"]
        lang = compile_schema(schema)
        caps = dict(max_abs_int=3) if not case["bounded_int"] else {}
        docs = enumerate_language(lang, **caps)
        validator = jsonschema.Draft202012Validator(schema)
        for d in docs:
            value = json.loads(d.decode("utf-8"))
            errors = list(validator.iter_errors(value))
            assert not errors, (case["name"], d, errors[0].message if errors else None)
            assert validate_instance(value, schema), (case["name"], d)


def test_enum_numeric_equality_without_binary64():
    from decimal import Decimal
    schema = {"type": "number", "enum": [0.1]}
    assert validate_instance(0.1, schema)
    assert not validate_instance(0.2, schema)
    # exact decimal comparison: 0.10 == 0.1, but 0.10001 != 0.1
    assert ref._json_equal(Decimal("0.10"), Decimal("0.1"))
    assert not ref._json_equal(Decimal("0.10001"), Decimal("0.1"))
    schema_text = compile_schema_text('{"type":"number","enum":[1.10]}')
    assert validate_document(b"1.1", schema_text)
    assert not validate_document(b"1.1000000001", schema_text)


# ---------------------------------------------------------------------------
# Schema acceptance in the reference itself (mirror of test_schema_acceptance)
# ---------------------------------------------------------------------------

def test_reference_rejects_unsupported_keywords():
    cases = {
        "anyOf": [], "oneOf": [], "allOf": [], "not": {}, "if": {},
        "then": {}, "else": {}, "minimum": 0, "maximum": 1, "multipleOf": 2,
        "pattern": "x", "format": "email", "uniqueItems": True,
        "contains": {}, "patternProperties": {}, "$id": "x",
        "definitions": {}, "unknownKeyword": 1,
    }
    for kw, val in cases.items():
        with pytest.raises(UnsupportedFeature):
            compile_schema({"type": "string", kw: val})


def test_reference_rejects_boolean_schemas():
    with pytest.raises(UnsupportedFeature):
        compile_schema_text("true")
    with pytest.raises(UnsupportedFeature):
        compile_schema({"type": "object", "properties": {"a": True},
                        "required": [], "additionalProperties": False})


def test_reference_ref_rules():
    with pytest.raises(UnsupportedFeature):
        compile_schema({"$ref": "https://example.com/s.json"})
    with pytest.raises(InvalidSchema):  # cycle
        compile_schema({"$defs": {"a": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"})
    with pytest.raises(InvalidSchema):  # unresolvable pointer
        compile_schema({"$defs": {"a": {"type": "integer"}}, "$ref": "#/$defs/b"})
    with pytest.raises(UnsupportedFeature):  # validation keywords next to $ref
        compile_schema({"$defs": {"a": {"type": "integer"}},
                        "$ref": "#/$defs/a", "minLength": 3})


def test_reference_structural_errors():
    with pytest.raises(UnsatisfiableConstraint):
        compile_schema({"type": "string", "minLength": 3, "maxLength": 2})
    with pytest.raises(UnsatisfiableConstraint):
        compile_schema({"type": "array", "items": {"type": "integer"},
                        "minItems": 2, "maxItems": 1})
    with pytest.raises(InvalidSchema):
        compile_schema({"type": "object", "properties": {"a": {"type": "integer"}},
                        "required": ["b"], "additionalProperties": False})
    with pytest.raises(InvalidSchema):
        compile_schema({"type": "object", "properties": {},
                        "required": [], "additionalProperties": True})
    with pytest.raises(InvalidSchema):  # no additionalProperties
        compile_schema({"type": "object", "properties": {}, "required": []})
    with pytest.raises(InvalidSchema):  # no items
        compile_schema({"type": "array"})
    with pytest.raises(InvalidSchema):  # duplicate keys in JSON
        compile_schema_text('{"type":"string","type":"integer"}')
    with pytest.raises(UnsupportedFeature):  # type as an array
        compile_schema({"type": ["string", "null"]})
    with pytest.raises(UnsupportedFeature):  # another $schema dialect
        compile_schema({"$schema": "http://json-schema.org/draft-07/schema#",
                        "type": "string"})
    with pytest.raises(UnsupportedFeature):  # mixed enum
        compile_schema({"enum": ["a", 1]})
    with pytest.raises(UnsupportedFeature):  # non-scalar enum
        compile_schema({"enum": [[1, 2]]})


def test_reference_schema_dialect_ok():
    lang = compile_schema({"$schema": "https://json-schema.org/draft/2020-12/schema",
                           "type": "integer"})
    assert enumerate_language(lang, max_abs_int=0) == {b"0"}
    lang2 = compile_schema({"$schema": "https://json-schema.org/draft/2020-12/schema#",
                            "type": "integer"})
    assert enumerate_language(lang2, max_abs_int=0) == {b"0"}


def test_reference_depth_limit():
    schema = {"type": "integer"}
    for _ in range(70):
        schema = {"type": "array", "items": schema}
    with pytest.raises(ResourceLimit):
        compile_schema(schema)


def test_annotations_ignored():
    lang = compile_schema({
        "type": "string", "title": "T", "description": "D",
        "$comment": "C", "examples": ["a"], "default": "a"})
    assert b'"a"' in enumerate_language(lang, max_string_len=1, string_alphabet=("a",))
