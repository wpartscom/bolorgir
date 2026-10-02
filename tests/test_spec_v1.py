"""spec-v1 profile end-to-end tests (ROADMAP rev-2 P1).

Covers the spec-v1 surface through the C ABI: value-based numbers, type
unions, structural enum/const, open objects, boolean schemas, the extended
local $ref resolver and dialect normalization. canonical-v1 behavior is
pinned by tests/test_parity_*; here only spec-v1 compiles are exercised.
The module is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

D4 = "http://json-schema.org/draft-04/schema"
D6 = "http://json-schema.org/draft-06/schema"
D7 = "http://json-schema.org/draft-07/schema"
V19 = "https://json-schema.org/draft/2019-09/schema"
V20 = "https://json-schema.org/draft/2020-12/schema"


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
# Numbers by value (semantics-spec-v1 4.4)
# ---------------------------------------------------------------------------

def test_integer_is_value_based(byte_ctx):
    _check(byte_ctx, {"type": "integer"},
           ["1", "1.0", "1e2", "-0.0", "1230e-1", "0", "-5", "1.00e2"],
           ["1.5", "0.5", "15e-1", '"1"', "true", "1e-1"])


def test_type_union(byte_ctx):
    _check(byte_ctx, {"type": ["integer", "string"]},
           ["1.0", '"x"', '""', "-3"],
           ["1.5", "true", "null", "[1]"])


def test_type_absent_is_any(byte_ctx):
    _check(byte_ctx, {},
           ["1.5", '"x"', "true", "null", "[1]", '{"a":1}'], [])


def test_const_number_by_value(byte_ctx):
    _check(byte_ctx, {"const": 1},
           ["1", "1.0", "1e0", "10e-1", "1.00", "0.1e1"],
           ["2", "1.5", "-1", '"1"'])


def test_const_negative_zero_is_zero(byte_ctx):
    _check(byte_ctx, {"const": -0.0}, ["0", "-0", "0.0", "0e5"], ["1", "-1"])


def test_const_fraction_by_value(byte_ctx):
    _check(byte_ctx, {"const": 1.5},
           ["1.5", "1.50", "15e-1", "0.15e1", "150e-2"],
           ["1.4", "1.51", "2"])


# ---------------------------------------------------------------------------
# Structural enum/const
# ---------------------------------------------------------------------------

def test_enum_dedups_equal_values(byte_ctx):
    _check(byte_ctx, {"enum": [1, 1.0, "a"]}, ["1", "1.0", '"a"'], ['"b"', "2"])


def test_enum_filtered_by_type(byte_ctx):
    _check(byte_ctx, {"type": "integer", "enum": [1e2, 1.5, "s"]},
           ["100", "1e2"], ["1.5", '"s"', "99"])


def test_structural_enum(byte_ctx):
    _check(byte_ctx, {"enum": [[1, 2], {"x": True}, None]},
           ["[1,2]", '{"x":true}', "null", "[1.0,2e0]"],
           ["[1]", '{"x":false}', "0"])


def test_const_object_schema_order(byte_ctx):
    # serialization policy 4.3: keys follow the schema spelling order.
    _check(byte_ctx, {"const": {"a": 1, "b": [1, 2]}},
           ['{"a":1,"b":[1,2]}', '{"a":1.0,"b":[1e0,2.0]}'],
           ['{"a":1}', '{"a":1,"b":[1,2],"c":3}', '{"a":1,"b":[2,1]}',
            '{"b":[1,2],"a":1}'])


def test_const_string_with_length_bounds(byte_ctx):
    _check(byte_ctx, {"type": "string", "const": "abc"}, ['"abc"'], ['"ab"', "1"])


def test_enum_all_filtered_root_refuses(byte_ctx):
    _refusal(byte_ctx, {"type": "string", "enum": [1, 2]}, "UNSATISFIABLE")


def test_enum_all_filtered_subschema_is_empty_language(byte_ctx):
    _check(byte_ctx,
           {"type": "object", "properties": {"a": {"enum": [1], "type": "string"}}},
           ["{}"], ['{"a":1}'])


def test_type_list_validation(byte_ctx):
    _refusal(byte_ctx, {"type": []}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"type": ["integer", "integer"]}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"type": ["foo"]}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"type": [1]}, "INVALID_SCHEMA")


# ---------------------------------------------------------------------------
# Open objects (semantics-spec-v1 4.3) and quick wins
# ---------------------------------------------------------------------------

def test_open_object_interleave_and_uniqueness(byte_ctx):
    schema = {"type": "object",
              "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
              "required": ["a"]}
    _check(byte_ctx, schema,
           ['{"a":"s"}', '{"u":1,"a":"s"}', '{"a":"s","b":2,"u":[1]}', '{"a":"s","u":{"x":null}}'],
           ['{"b":1}', '{"a":"s","a":"s"}', '{"a":"s","u":1,"u":2}',
            '{"b":1,"a":"s"}', '{"a":1}'])


def test_open_object_required_not_in_properties(byte_ctx):
    _check(byte_ctx, {"type": "object", "required": ["zz"]},
           ['{"zz":1}', '{"zz":1,"u":"s"}'],
           ["{}", '{"u":1}', '{"zz":1,"zz":2}'])


def test_closed_object_required_not_in_properties_unsat(byte_ctx):
    _refusal(byte_ctx,
             {"type": "object", "properties": {"a": {"type": "integer"}},
              "required": ["zz"], "additionalProperties": False},
             "UNSATISFIABLE")


def test_additional_properties_schema(byte_ctx):
    # P2: a schema value constrains the undeclared properties.
    _check(byte_ctx,
           {"type": "object", "properties": {"a": {"type": "string"}},
            "additionalProperties": {"type": "integer"}},
           ['{"a":"x"}', '{"a":"x","b":1}', '{"b":2}', "{}"],
           ['{"a":1}', '{"b":"s"}', '{"a":"x","b":1.5}'])
    # An unsatisfiable additionalProperties schema behaves as `false`.
    _check(byte_ctx,
           {"properties": {"a": {"type": "integer"}},
            "additionalProperties": {"type": "string", "minLength": 1, "maxLength": 0}},
           ['{"a":1}', "{}"],
           ['{"a":1,"b":"x"}'])


def test_absent_items_is_anyjson(byte_ctx):
    _check(byte_ctx, {"type": "array"}, ["[]", "[1]", '[1,"s",{"a":[true]}]'], ["1", '"s"'])


def test_boolean_schemas(byte_ctx):
    _check(byte_ctx, True, ["1", '"s"', "[1]", '{"a":1}', "null"], [])
    _refusal(byte_ctx, False, "UNSATISFIABLE")
    # false in subschema position: the property can never be present.
    _check(byte_ctx, {"properties": {"a": False}}, ["{}"], ['{"a":1}'])


def test_anyjson_nesting_depth(byte_ctx):
    _check(byte_ctx, {"properties": {"a": True}},
           ['{"a":[1,[2,[3]]]}', '{"a":{"x":{"y":[]}}}'],
           [])


def test_anyjson_arrays_many_elements(byte_ctx):
    # Regression: an integer-valued element under an unconstrained schema
    # completed on both the int_num and num_v branches and the converged
    # duplicate threads were never merged, doubling per element until the
    # thread budget rejected the 5th integer in the array.
    many = [
        "[1,2,3,4,5,6,7,8,9,10,11,12]",
        '["a","b","c","d","e","f","g","h"]',
        '[1,"a",2,"b",3,"c",4,"d",5,"e",6]',
        "[[1,2,3,4,5,6],[7,8,9,10,11,12]]",
    ]
    for schema in ({"items": {}}, {"items": {"anyOf": [{}]}}):
        _check(byte_ctx, schema, many, ["[1,2,3,4,5,]"])
    # Merged duplicates must not weaken the enforced constraints.
    _check(byte_ctx, {"items": {}, "uniqueItems": True},
           many, ["[1,1.0,2,3,4,5]", '[1,"a",1,"b",2,3]'])
    _check(byte_ctx, {"items": {}, "maxItems": 5},
           ["[1,2,3,4,5]", '["a","b","c","d","e"]'],
           ["[1,2,3,4,5,6]"])


# ---------------------------------------------------------------------------
# Extended local $ref (dialect-matrix section 3)
# ---------------------------------------------------------------------------

def test_ref_multi_segment_pointer(byte_ctx):
    schema = {"$defs": {"a": {"type": "object",
                              "properties": {"x": {"$defs": {"deep": {"type": "integer"}}}}}},
              "properties": {"p": {"$ref": "#/$defs/a/properties/x/$defs/deep"},
                             "q": {"$ref": "#/properties/p"}}}
    _check(byte_ctx, schema,
           ['{"p":1,"q":2}', '{"p":1.0}', "{}", '{"z":[1]}'],
           ['{"p":1.5}', '{"q":"s"}'])


def test_ref_pointer_escapes(byte_ctx):
    schema = {"$defs": {"a/b": {"type": "integer"}, "m~n": {"type": "string"}},
              "properties": {"x": {"$ref": "#/$defs/a~1b"}, "y": {"$ref": "#/$defs/m~0n"}}}
    _check(byte_ctx, schema, ['{"x":1,"y":"s"}'], ['{"x":"s"}', '{"y":1}'])


def test_ref_pointer_array_index(byte_ctx):
    schema = {"$defs": {"arr": [{"type": "string"}, {"type": "integer"}]},
              "properties": {"x": {"$ref": "#/$defs/arr/1"}}}
    _check(byte_ctx, schema, ['{"x":5}'], ['{"x":"s"}'])
    _refusal(byte_ctx, {"$defs": {"arr": [{"type": "string"}]}, "$ref": "#/$defs/arr/01"},
             "INVALID_SCHEMA")
    _refusal(byte_ctx, {"$defs": {"arr": [{"type": "string"}]}, "$ref": "#/$defs/arr/5"},
             "INVALID_SCHEMA")


def test_ref_anchor_2020_12(byte_ctx):
    schema = {"$schema": V20,
              "$defs": {"pos": {"$anchor": "posInt", "type": "integer"}},
              "properties": {"x": {"$ref": "#posInt"}}}
    _check(byte_ctx, schema, ['{"x":3}'], ['{"x":"s"}'])


def test_ref_anchor_draft07_and_draft04(byte_ctx):
    s7 = {"$schema": D7 + "#",
          "definitions": {"pos": {"$id": "#posInt", "type": "integer"}},
          "properties": {"x": {"$ref": "#posInt"}}}
    _check(byte_ctx, s7, ['{"x":3}'], ['{"x":"s"}'])
    s4 = {"$schema": D4,
          "definitions": {"pos": {"id": "#posInt", "type": "integer"}},
          "properties": {"x": {"$ref": "#posInt"}}}
    _check(byte_ctx, s4, ['{"x":3}'], ['{"x":"s"}'])


def test_ref_sibling_rule_per_dialect(byte_ctx):
    # draft-07: assertion keywords next to $ref are ignored.
    s7 = {"$schema": D7,
          "definitions": {"any": {"type": "integer"}},
          "properties": {"x": {"$ref": "#/definitions/any", "type": "string", "maxLength": 1}}}
    _check(byte_ctx, s7, ['{"x":3}'], ['{"x":"s"}'])
    # 2020-12 (P6b): they apply alongside - the ref expansion and the
    # sibling assertions conjoin (integer ∧ string is empty for "x").
    _check(byte_ctx,
           {"$defs": {"a": {"type": "integer"}},
            "properties": {"x": {"$ref": "#/$defs/a", "type": "string"}}},
           ["{}"], ['{"x":3}', '{"x":"s"}'])
    # A satisfiable conjunction: ref to integer ∧ minimum.
    _check(byte_ctx,
           {"$defs": {"a": {"type": "integer"}},
            "properties": {"x": {"$ref": "#/$defs/a", "minimum": 3}}},
           ['{"x":5}', "{}"], ['{"x":2}', '{"x":"s"}'])


def test_ref_cycles_and_unresolved(byte_ctx):
    # P5 (ADR-0008): recursive cycles compile under bounded unrolling -
    # covered by tests/test_spec_v1_p5.py. A cycle without a base case is
    # the empty-language root refusal.
    _refusal(byte_ctx, {"$ref": "#"}, "UNSATISFIABLE")
    _refusal(byte_ctx, {"$ref": "#/nonexistent"}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"$ref": "#noSuchAnchor"}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"$ref": "http://ex.com/other.json"}, "UNSUPPORTED_FEATURE")


def test_ref_to_false(byte_ctx):
    _refusal(byte_ctx, {"$defs": {"x": False}, "$ref": "#/$defs/x"}, "UNSATISFIABLE")
    _check(byte_ctx, {"$defs": {"no": False}, "properties": {"a": {"$ref": "#/$defs/no"}}},
           ["{}"], ['{"a":1}'])


# ---------------------------------------------------------------------------
# Dialect normalization (dialect-matrix sections 1-2)
# ---------------------------------------------------------------------------

def test_dialect_detection_forms(byte_ctx):
    for uri in (D4, D6 + "#", D7.replace("http://", "https://"), V19, V20):
        _check(byte_ctx, {"$schema": uri, "type": "integer"}, ["1.0"], ["1.5"])
    _refusal(byte_ctx, {"$schema": "https://example.com/custom/schema", "type": "integer"},
             "UNSUPPORTED_FEATURE")
    # absent $schema defaults to 2020-12: $defs works, prefixItems (P2)
    # compiles.
    _check(byte_ctx, {"$defs": {"i": {"type": "integer"}},
                      "properties": {"x": {"$ref": "#/$defs/i"}}},
           ['{"x":1}'], ['{"x":"s"}'])
    _check(byte_ctx, {"prefixItems": [{"type": "string"}]}, ['["a",1]', "[]"], ["[1]"])


def test_non_root_schema_is_annotation(byte_ctx):
    _check(byte_ctx, {"properties": {"x": {"$schema": D4, "type": "integer"}}},
           ['{"x":1}'], ['{"x":"s"}'])


def test_cross_draft_keywords_ignored(byte_ctx):
    _check(byte_ctx, {"$schema": D4, "const": 5, "type": "integer"}, ["1.0", "5"], ["1.5"])
    _check(byte_ctx, {"$schema": D7, "prefixItems": [{"type": "string"}]}, ['[1,"x"]', "5"], [])
    _check(byte_ctx, {"$schema": V20, "additionalItems": False, "type": "array"}, ["[1,2,3]"], [])
    _check(byte_ctx, {"$schema": D4, "contains": {"type": "integer"}, "type": "array"}, ["[1]"], [])
    _check(byte_ctx, {"$schema": V20, "dependencies": {"a": ["b"]}}, ['{"a":1}'], [])
    _check(byte_ctx, {"$schema": D7, "dependentRequired": {"a": ["b"]}}, ['{"a":1}'], [])
    _check(byte_ctx, {"$schema": D7, "unevaluatedItems": False, "type": "array"}, ["[1]"], [])
    _check(byte_ctx, {"$schema": D7, "$vocabulary": {"https://example.com/x": True},
                      "type": "integer"}, ["1"], [])
    _check(byte_ctx, {"$schema": V19, "$dynamicRef": "#", "type": "integer"}, ["1"], [])
    _check(byte_ctx, {"$schema": V20, "$recursiveRef": "#", "type": "integer"}, ["1"], [])
    _check(byte_ctx, {"$schema": D4, "if": {"type": "integer"}, "type": "number"}, ["1.5"], [])
    _check(byte_ctx, {"$schema": V20, "contentEncoding": "base64", "type": "string"}, ['"x="'], [])
    _check(byte_ctx, {"$schema": D7, "contentSchema": {"type": "string"}, "type": "string"},
           ['"x"'], [])


def test_dialect_keywords_refused(byte_ctx):
    # P6b: $recursiveRef (2019-09) and $dynamicRef (2020-12) are supported.
    # A bare self-recursive reference has no base case: UNSATISFIABLE.
    _refusal(byte_ctx, {"$schema": V19, "$recursiveRef": "#"}, "UNSATISFIABLE")
    _refusal(byte_ctx, {"$schema": V20, "$dynamicRef": "#"}, "UNSATISFIABLE")
    # ...and in the other dialect each is an unknown keyword, ignored.
    _check(byte_ctx, {"$schema": V19, "$dynamicRef": "#"}, ["1", '"x"'], [])
    _check(byte_ctx, {"$schema": V20, "$recursiveRef": "#"}, ["1", '"x"'], [])
    # P3: if/then/else are supported (draft-07+); `if` alone is inert.
    _check(byte_ctx, {"$schema": D7, "if": {"type": "integer"}}, ["1", '"x"'], [])
    # P6a: unevaluated* are supported (2019-09/2020-12).
    _check(byte_ctx, {"$schema": V19, "unevaluatedItems": False},
           ["[]", "{}", "1"], ["[1]"])
    # P6b: contentSchema is an annotation (2020-12 section 8): inert.
    _check(byte_ctx, {"$schema": V20, "contentSchema": {"type": "string"}},
           ["1", '"x"', "{}"], [])


def test_exclusive_minimum_form_per_dialect(byte_ctx):
    _refusal(byte_ctx, {"$schema": D4, "exclusiveMinimum": 3}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"$schema": D7, "exclusiveMinimum": True}, "INVALID_SCHEMA")
    # P4: the correct form compiles. draft-04 boolean modifier (normalized
    # to an exclusive bound); draft-07 standalone numeric keyword.
    _check(byte_ctx, {"$schema": D4, "exclusiveMinimum": True, "minimum": 3},
           ["4", "3.5"], ["3", "3.0", "2"])
    _check(byte_ctx, {"$schema": D4, "exclusiveMinimum": False, "minimum": 3},
           ["3", "4"], ["2"])
    # draft-04 modifier without its bound is dropped (no constraint).
    _check(byte_ctx, {"$schema": D4, "exclusiveMinimum": True},
           ["3", "-100", '"x"'], [])
    _check(byte_ctx, {"$schema": D7, "exclusiveMinimum": 3},
           ["4", "3.5"], ["3", "3.0", "2"])


def test_items_tuple_form(byte_ctx):
    _refusal(byte_ctx, {"$schema": V20, "items": [{"type": "string"}]}, "INVALID_SCHEMA")
    # P2: tuple items are supported in draft-04..2019-09.
    _check(byte_ctx, {"$schema": D7, "items": [{"type": "string"}, {"type": "integer"}]},
           ['["a",1]', '["a"]', "[]", '["a",1,true]'],
           ['[1]', '["a","b"]'])


def test_vocabulary_rule(byte_ctx):
    _check(byte_ctx,
           {"$schema": V20,
            "$vocabulary": {"https://json-schema.org/draft/2020-12/vocab/core": True,
                            "https://json-schema.org/draft/2020-12/vocab/validation": True,
                            "https://example.com/custom": False},
            "type": "integer"},
           ["1"], [])
    _refusal(byte_ctx,
             {"$schema": V20,
              "$vocabulary": {"https://json-schema.org/draft/2020-12/vocab/format-assertion": True}},
             "UNSUPPORTED_FEATURE")
    _refusal(byte_ctx,
             {"$schema": V20, "$vocabulary": {"https://example.com/custom": True}},
             "UNSUPPORTED_FEATURE")
    _refusal(byte_ctx,
             {"$schema": V19,
              "$vocabulary": {"https://json-schema.org/draft/2019-09/vocab/format": True}},
             "UNSUPPORTED_FEATURE")


# ---------------------------------------------------------------------------
# canonical-v1 stays frozen on the spec-v1 surface
# ---------------------------------------------------------------------------

def test_canonical_untouched(byte_ctx):
    canon = b"canonical-v1"
    _refusal(byte_ctx, {"type": ["integer", "string"]}, "", profile=canon)
    _refusal(byte_ctx, {"const": {"a": 1}}, "", profile=canon)
    _refusal(byte_ctx, {"type": "object"}, "", profile=canon)
    _refusal(byte_ctx, True, "", profile=canon)
    # canonical integer stays a lexeme class.
    g = zg.compile_schema(byte_ctx, b'{"type":"integer"}', profile=canon)
    try:
        assert _accepts(byte_ctx, g, "1")
        assert not _accepts(byte_ctx, g, "1.0")
    finally:
        zg.grammar_release(g)
