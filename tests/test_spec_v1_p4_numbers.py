"""spec-v1 P4 end-to-end tests (ROADMAP rev-2 P4.3): numeric range and
divisibility.

Covers minimum/maximum/exclusiveMinimum/exclusiveMaximum and multipleOf
through the C ABI under the spec-v1 profile: value-based boundary
semantics (1.0 == 1, 5 >= 5.0) with exact decimal arithmetic (never
binary64), inclusive/exclusive bounds, decimal cases (0.1/0.3), negative
bounds, exponent spellings, integer and decimal multipleOf, dialect
normalization of the draft-04 boolean exclusive* modifiers, the
combinations with type/enum/const/union and the P3 combinators, and the
refusal surface (INVALID_SCHEMA with the exact JSON pointer;
UNSUPPORTED_FEATURE for divisors with more than 10 significant digits).
canonical-v1 is frozen (it keeps refusing all five keywords). The module
is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

D4 = "http://json-schema.org/draft-04/schema#"
D6 = "http://json-schema.org/draft-06/schema#"
D7 = "http://json-schema.org/draft-07/schema#"


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
# minimum / maximum: value-based inclusive bounds
# ---------------------------------------------------------------------------

def test_minimum_value_based(byte_ctx):
    _check(byte_ctx, {"minimum": 1.1},
           ["2.6", "1.1", "1.10", "11e-1", "1.10001", '"x"'],
           ["0.6", "1.0999", "1.01"])
    # Negative bound, value equality across spellings.
    _check(byte_ctx, {"minimum": -2},
           ["-1", "0", "-2", "-2.0", "-2e0", '"x"'],
           ["-2.0001", "-3", "-20"])


def test_maximum_value_based(byte_ctx):
    _check(byte_ctx, {"maximum": 3.0},
           ["2.6", "3.0", "3", "0.3e1", '"x"'],
           ["3.5", "3.0001", "30"])
    _check(byte_ctx, {"maximum": 300},
           ["299.97", "300", "300.0", "3e2", "-1000000"],
           ["300.5", "300.0001", "4e2"])


def test_minimum_maximum_combined(byte_ctx):
    _check(byte_ctx, {"minimum": -2, "maximum": 3},
           ["-2", "3", "0", "2.999", "-1.999"],
           ["-2.001", "3.001", "-3", "4"])


def test_exclusive_bounds(byte_ctx):
    _check(byte_ctx, {"exclusiveMinimum": 1.1},
           ["1.2", "1.10001", '"x"'],
           ["1.1", "1.10", "0.6"])  # equal by value: excluded
    _check(byte_ctx, {"exclusiveMaximum": 3.0},
           ["2.2", "2.99999", "-100", '"x"'],
           ["3.0", "3", "3.5"])
    # Negative exclusive bound.
    _check(byte_ctx, {"type": "number", "exclusiveMaximum": -0.5},
           ["-0.6", "-1", "-0.51"],
           ["-0.5", "-0.50", "-0.4", "0"])


def test_bounds_decimal_exactness(byte_ctx):
    # 0.1/0.3-style decimal cases: exact decimal comparison, never binary64.
    _check(byte_ctx, {"minimum": 0.1, "maximum": 0.3},
           ["0.1", "0.3", "0.2", "0.10", "0.30000"],
           ["0.09", "0.30000000000000004", "0.09999999999999999", "0.31"])
    # A value with more digits than binary64 distinguishes.
    _check(byte_ctx, {"maximum": 1.0000000000000002},
           ["1.0000000000000002", "1"],
           ["1.0000000000000003", "1.1"])


def test_bounds_exponent_spellings(byte_ctx):
    _check(byte_ctx, {"minimum": 1e2, "maximum": 1e3},
           ["100", "1000", "1e2", "10e2", "0.1e4", "100.0"],
           ["99", "1001", "1.0001e3", "99.999"])


# ---------------------------------------------------------------------------
# multipleOf: exact decimal divisibility
# ---------------------------------------------------------------------------

def test_multipleof_integer_divisor(byte_ctx):
    _check(byte_ctx, {"multipleOf": 2},
           ["10", "0", "-4", "1e3", "100.0", "0.02e2", '"foo"'],
           ["7", "3", "0.5", "5e-1", "15"])
    _check(byte_ctx, {"type": "integer", "multipleOf": 3},
           ["0", "3", "-9", "3.0", "30e-1"],
           ["1", "3.5", "4", "10"])


def test_multipleof_decimal_divisor(byte_ctx):
    # Reduced to exact integer divisibility through exp10.
    _check(byte_ctx, {"multipleOf": 1.5},
           ["0", "4.5", "3", "7.5", "0.15e1", "-3"],
           ["35", "0.75", "4.4"])
    _check(byte_ctx, {"multipleOf": 0.0001},
           ["0.0075", "1", "123.4567", "1e-4"],
           ["0.00751", "123.45678", "1e-5"])
    _check(byte_ctx, {"multipleOf": 0.01},
           ["0.01", "1", "2.34", "-0.99"],
           ["0.001", "2.345", "0.1e-2"])
    # Divisor with 9 significant digits (exact, no binary64).
    _check(byte_ctx, {"type": "integer", "multipleOf": 0.123456789},
           ["0"],
           ["1e+308"])
    _check(byte_ctx, {"multipleOf": 1e-08},
           ["12391239123", "0.00000001", "5"],
           ["0.000000001", "1.5e-8"])


def test_multipleof_zero_instance(byte_ctx):
    # Zero is a multiple of every divisor, in every spelling.
    _check(byte_ctx, {"multipleOf": 7},
           ["0", "-0", "0.0", "0e5", "-0.0e-3"],
           [])


# ---------------------------------------------------------------------------
# Applicability, type/enum/const/union combinations
# ---------------------------------------------------------------------------

def test_numeric_keywords_inapplicable_to_non_numbers(byte_ctx):
    # minimum constrains only numbers; every other type passes.
    _check(byte_ctx, {"minimum": 5},
           ["5", "6", '"x"', '"1"', "true", "null", "[1]", '{"a":1}'],
           ["4", "4.999", "-1"])


def test_numeric_keywords_with_union_type(byte_ctx):
    _check(byte_ctx, {"type": ["integer", "string"], "minimum": 2},
           ["2", "3", '"1"', '"abc"'],
           ["1", "0", "-5"])
    _check(byte_ctx, {"type": ["number", "boolean"], "multipleOf": 2},
           ["4", "2.4e1", "true", "false"],
           ["3", "2.5", "2.4"])


def test_numeric_keywords_filter_enum_const(byte_ctx):
    _check(byte_ctx, {"enum": [1, 2.5, "a"], "minimum": 2},
           ["2.5", '"a"'],
           ["1", '"b"', "2.4"])
    # enum numbers are matched by value: 2 stays valid as 2.0.
    _check(byte_ctx, {"enum": [2, 3, 4], "multipleOf": 2},
           ["2", "4", "2.0", "4e0"],
           ["3", "3.0"])
    _check(byte_ctx, {"const": 1.0, "minimum": 1},
           ["1", "1.0", "1e0"],
           ["0.999"])
    # Everything filtered: UNSATISFIABLE_CONSTRAINT at the root.
    _refusal(byte_ctx, {"enum": [1, 3], "multipleOf": 2}, "UNSATISFIABLE_CONSTRAINT")
    _refusal(byte_ctx, {"const": 0.5, "minimum": 1}, "UNSATISFIABLE_CONSTRAINT")


def test_numeric_keywords_contradictory_bounds(byte_ctx):
    _refusal(byte_ctx, {"type": "number", "minimum": 5, "maximum": 3},
             "UNSATISFIABLE_CONSTRAINT")
    _refusal(byte_ctx, {"type": "integer", "minimum": 5, "maximum": 5,
                        "exclusiveMaximum": 5},
             "UNSATISFIABLE_CONSTRAINT")
    # In a union only the contradictory arms drop.
    _check(byte_ctx, {"type": ["string", "number"], "minimum": 5, "maximum": 3},
           ['"x"', '""'],
           ["5", "1"])


def test_numeric_keywords_with_p3_combinators(byte_ctx):
    # allOf conjoins the range with the divisibility.
    _check(byte_ctx, {"allOf": [{"minimum": 5}, {"multipleOf": 2}]},
           ["6", "10", '"x"'],
           ["5", "4", "7"])
    # oneOf over two ranges; a non-number satisfies both branches (the
    # keywords are inapplicable to it), so oneOf rejects it.
    _check(byte_ctx, {"oneOf": [{"maximum": 0}, {"minimum": 10}]},
           ["-1", "0.0", "10", "15"],
           ["5", "0.0001", "9.999", '"s"'])
    # not over a supported complement keeps working next to numeric parts.
    _check(byte_ctx, {"type": "integer", "minimum": 2, "not": {"const": 5}},
           ["2", "3", "6"],
           ["5", "5.0", "1"])
    # A property constrained by a range.
    _check(byte_ctx,
           {"type": "object",
            "properties": {"k": {"type": "number", "minimum": 0, "maximum": 1}},
            "required": ["k"]},
           ['{"k":0.5}', '{"k":0}', '{"k":1}'],
           ['{"k":1.5}', '{"k":-0.1}', '{"k":"x"}'])


def test_numeric_keywords_in_array_items(byte_ctx):
    _check(byte_ctx, {"type": "array", "items": {"multipleOf": 2}},
           ["[]", "[2,4]", "[2,4.0]"],
           ["[3]", "[2,3]", "[2.5]"])


# ---------------------------------------------------------------------------
# Dialect normalization (draft-04 boolean modifiers)
# ---------------------------------------------------------------------------

def test_draft04_exclusive_modifiers(byte_ctx):
    # boolean modifier makes the named bound exclusive.
    _check(byte_ctx, {"$schema": D4, "minimum": 3, "exclusiveMinimum": True},
           ["4", "3.5"],
           ["3", "3.0", "2"])
    _check(byte_ctx, {"$schema": D4, "maximum": 3, "exclusiveMaximum": True},
           ["2", "2.999"],
           ["3", "4"])
    # false modifier: bound stays inclusive.
    _check(byte_ctx, {"$schema": D4, "minimum": 3, "exclusiveMinimum": False},
           ["3", "4"],
           ["2"])
    # Modifier without its bound is dropped (no constraint).
    _check(byte_ctx, {"$schema": D4, "exclusiveMinimum": True},
           ["3", "-100", '"x"'],
           [])
    # Wrong form for the dialect: INVALID_SCHEMA.
    _refusal(byte_ctx, {"$schema": D4, "exclusiveMinimum": 3}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"$schema": D7, "exclusiveMinimum": True}, "INVALID_SCHEMA")
    # draft-06 standalone numeric form.
    _check(byte_ctx, {"$schema": D6, "exclusiveMinimum": 3},
           ["4"], ["3"])


def test_minimum_and_exclusive_conjoin(byte_ctx):
    # draft-06+: minimum and a standalone exclusiveMinimum both assert;
    # the tighter bound wins, an equal exclusive bound stays exclusive.
    _check(byte_ctx, {"minimum": 2, "exclusiveMinimum": 1.5},
           ["2", "3"],
           ["1.9", "1.5"])
    _check(byte_ctx, {"minimum": 1.5, "exclusiveMinimum": 1.5},
           ["1.6"],
           ["1.5"])
    _check(byte_ctx, {"maximum": 2, "exclusiveMaximum": 3},
           ["2", "1"],
           ["2.5", "3"])


# ---------------------------------------------------------------------------
# Refusal surface and canonical-v1 freeze
# ---------------------------------------------------------------------------

def test_numeric_keyword_validation_errors(byte_ctx):
    _refusal(byte_ctx, {"minimum": "5"}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"multipleOf": 0}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"multipleOf": -2}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"multipleOf": "2"}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"maximum": True}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"type": "object", "properties": {"a": {"maximum": True}}},
             "INVALID_SCHEMA")


def test_numeric_keyword_refusal_pointers(byte_ctx):
    msg = _refusal(byte_ctx, {"minimum": "5"}, "INVALID_SCHEMA")
    assert "/minimum" in msg
    msg = _refusal(byte_ctx, {"multipleOf": 0}, "INVALID_SCHEMA")
    assert "/multipleOf" in msg
    msg = _refusal(byte_ctx, {"type": "object", "properties": {"a": {"maximum": True}}},
                   "INVALID_SCHEMA")
    assert "/properties/a/maximum" in msg
    # Divisors with more than 10 significant digits: UNSUPPORTED_FEATURE
    # with the pointer, never approximated through binary64 (A6).
    msg = _refusal(byte_ctx, {"multipleOf": 12345678901}, "UNSUPPORTED_FEATURE")
    assert "/multipleOf" in msg
    # 10 significant digits still compile exactly.
    _check(byte_ctx, {"multipleOf": 1234567891},
           ["1234567891", "0", "2469135782"],
           ["1", "1234567890"])


def test_canonical_v1_keeps_refusing(byte_ctx):
    canon = b"canonical-v1"
    _refusal(byte_ctx, {"type": "number", "minimum": 3}, "UNSUPPORTED_FEATURE", profile=canon)
    _refusal(byte_ctx, {"type": "number", "maximum": 3}, "UNSUPPORTED_FEATURE", profile=canon)
    _refusal(byte_ctx, {"type": "number", "multipleOf": 2}, "UNSUPPORTED_FEATURE", profile=canon)
    _refusal(byte_ctx, {"type": "number", "exclusiveMinimum": 3}, "UNSUPPORTED_FEATURE", profile=canon)
    _refusal(byte_ctx, {"type": "number", "exclusiveMaximum": 3}, "UNSUPPORTED_FEATURE", profile=canon)
    # Plain integer/number stay byte-identical to the frozen baseline.
    g = zg.compile_schema(byte_ctx, b'{"type":"integer"}', profile=canon)
    try:
        s = zg.Session(byte_ctx, g)
        try:
            for b in b"-12":
                assert s.accept(b) == zg.BLG_OK
            assert s.can_end()
        finally:
            s.destroy()
    finally:
        zg.grammar_release(g)
