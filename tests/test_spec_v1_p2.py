"""spec-v1 P2 end-to-end tests (ROADMAP rev-2 P2).

Covers the P2 keyword surface through the C ABI under the spec-v1 profile:
additionalProperties as a schema, the literal-substring patternProperties
subset (full regex is P4, several patterns and declared-key overlap still
refuse with a pointer), propertyNames, min/maxProperties,
dependencies/dependentRequired/dependentSchemas, tuple items /
additionalItems / prefixItems, contains with min/maxContains, and
uniqueItems. canonical-v1 is frozen (it keeps refusing all of these).
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
# additionalProperties as a schema
# ---------------------------------------------------------------------------

def test_additional_properties_schema(byte_ctx):
    schema = {"type": "object",
              "properties": {"a": {"type": "string"}},
              "additionalProperties": {"type": "integer"}}
    _check(byte_ctx, schema,
           ['{"a":"x"}', '{"a":"x","b":1}', '{"b":2}', "{}", '{"b":1.0}'],
           ['{"a":1}', '{"b":"s"}', '{"a":"x","b":1.5}', '{"b":[1]}'])


def test_additional_properties_schema_nested(byte_ctx):
    schema = {"additionalProperties": {"type": "array", "items": {"type": "integer"}}}
    _check(byte_ctx, schema,
           ['{"x":[1,2]}', '{"x":[]}', "{}"],
           ['{"x":[1,"s"]}', '{"x":1}'])


def test_additional_properties_unsatisfiable_is_closed(byte_ctx):
    schema = {"properties": {"a": {"type": "integer"}},
              "additionalProperties": {"type": "string", "minLength": 1, "maxLength": 0}}
    _check(byte_ctx, schema, ['{"a":1}', "{}"], ['{"a":1,"b":"x"}', '{"b":1}'])


def test_additional_properties_true_is_open(byte_ctx):
    _check(byte_ctx, {"additionalProperties": True},
           ['{"a":1,"b":[true]}', "{}"], [])


# ---------------------------------------------------------------------------
# patternProperties: literal-substring subset, regex refuses
# ---------------------------------------------------------------------------

def test_pattern_properties_literal(byte_ctx):
    schema = {"patternProperties": {"foo": {"type": "integer"}}}
    _check(byte_ctx, schema,
           ['{"foo":1}', '{"xfoo":1}', '{"foox":1}', '{"bar":"s"}', "{}"],
           ['{"foo":"s"}', '{"xfoo":1.5}'])


def test_pattern_properties_with_additional_false(byte_ctx):
    schema = {"patternProperties": {"foo": {"type": "integer"}},
              "additionalProperties": False}
    _check(byte_ctx, schema,
           ['{"foo":1}', '{"xfoox":2}', "{}"],
           ['{"foo":"s"}', '{"bar":1}', '{"fo":1}'])


def test_pattern_properties_matches_decoded_key(byte_ctx):
    # The match applies to the decoded key value: pattern "n" must not
    # match the newline key (raw bytes \n contain 'n'), and must match the
    # escaped spelling of a key containing 'n'.
    schema = {"patternProperties": {"n": {"type": "integer"}}}
    _check(byte_ctx, schema,
           ['{"\\n":"s"}', '{"nn":1}'],
           ['{"nn":"s"}', '{"\\u006e":"s"}'])


def test_pattern_properties_trivial_schema_is_dropped(byte_ctx):
    _check(byte_ctx, {"patternProperties": {"foo": {}}},
           ['{"foo":1}', '{"foo":"s"}'], [])
    _check(byte_ctx, {"patternProperties": {"foo": True}},
           ['{"foo":1}'], [])


def test_pattern_properties_refusals(byte_ctx):
    # Regex patterns compile since P4; what still refuses is the
    # intersection machinery (P3): several patterns, or a declared key the
    # pattern also matches.
    _refusal(byte_ctx, {"patternProperties": {"a": {}, "b": {}}}, "UNSUPPORTED_FEATURE")
    _refusal(byte_ctx,
             {"properties": {"xa": {}}, "patternProperties": {"a": {"type": "integer"}}},
             "UNSUPPORTED_FEATURE")
    _refusal(byte_ctx,
             {"properties": {"xa": {}}, "patternProperties": {"^x": {"type": "integer"}}},
             "UNSUPPORTED_FEATURE")
    # A pattern outside the supported ECMA-262 subset refuses with a pointer.
    _refusal(byte_ctx, {"patternProperties": {"(?=x)a": {}}}, "UNSUPPORTED_FEATURE")


# ---------------------------------------------------------------------------
# propertyNames
# ---------------------------------------------------------------------------

def test_property_names_length(byte_ctx):
    _check(byte_ctx, {"propertyNames": {"maxLength": 3}},
           ['{"abc":1}', '{"":1}', "{}"], ['{"abcd":1}'])
    _check(byte_ctx, {"propertyNames": {"minLength": 2}},
           ['{"ab":1}', "{}"], ['{"a":1}'])


def test_property_names_false(byte_ctx):
    _check(byte_ctx, {"propertyNames": False}, ["{}", "5", '[{"a":1}]'], ['{"a":1}'])


def test_property_names_filters_declared(byte_ctx):
    # Optional declared key violating propertyNames can never appear.
    _check(byte_ctx,
           {"properties": {"abcd": {"type": "integer"}}, "propertyNames": {"maxLength": 3}},
           ["{}"], ['{"abcd":1}'])
    # A required declared key violating propertyNames empties the object arm.
    _refusal(byte_ctx,
             {"type": "object", "properties": {"abcd": {}}, "required": ["abcd"],
              "propertyNames": {"maxLength": 3}},
             "UNSATISFIABLE")


def test_property_names_enum(byte_ctx):
    _check(byte_ctx, {"propertyNames": {"enum": ["a", "b"]}},
           ['{"a":1}', '{"a":1,"b":2}'], ['{"c":1}'])


# ---------------------------------------------------------------------------
# min/maxProperties
# ---------------------------------------------------------------------------

def test_min_properties(byte_ctx):
    _check(byte_ctx, {"minProperties": 2},
           ['{"a":1,"b":2}', '{"a":1,"b":2,"c":3}', "5"],
           ['{"a":1}', "{}"])


def test_max_properties(byte_ctx):
    _check(byte_ctx, {"maxProperties": 1},
           ['{"a":1}', "{}", "5"],
           ['{"a":1,"b":2}'])


def test_min_max_properties_closed_object(byte_ctx):
    _check(byte_ctx,
           {"properties": {"a": {}, "b": {}}, "additionalProperties": False,
            "minProperties": 1, "maxProperties": 1},
           ['{"a":1}', '{"b":2}'],
           ["{}", '{"a":1,"b":2}'])


def test_min_max_properties_value_based_bounds(byte_ctx):
    _check(byte_ctx, {"minProperties": 1.0, "maxProperties": 2e0},
           ['{"a":1}', '{"a":1,"b":2}'], ["{}", '{"a":1,"b":2,"c":3}'])
    _refusal(byte_ctx, {"type": "object", "minProperties": 1.5}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"type": "object", "maxProperties": -1}, "INVALID_SCHEMA")
    _refusal(byte_ctx, {"type": "object", "minProperties": 3, "maxProperties": 2},
             "UNSATISFIABLE")


# ---------------------------------------------------------------------------
# dependencies / dependentRequired / dependentSchemas
# ---------------------------------------------------------------------------

def test_dependent_required(byte_ctx):
    schema = {"dependentRequired": {"a": ["b", "c"]}}
    _check(byte_ctx, schema,
           ["{}", '{"b":1}', '{"a":1,"b":2,"c":3}', '{"a":1,"c":3,"b":2}', "5"],
           ['{"a":1}', '{"a":1,"b":2}', '{"a":1,"c":3}'])


def test_dependent_required_declared_out_of_order(byte_ctx):
    # "b" is declared before "a": once "a" lands, "b" can never follow.
    schema = {"properties": {"b": {}, "a": {}},
              "dependentRequired": {"a": ["b"]}}
    _check(byte_ctx, schema,
           ["{}", '{"b":1}', '{"b":1,"a":2}'],
           ['{"a":1}', '{"a":1,"b":2}'])


def test_dependent_schemas(byte_ctx):
    schema = {"dependentSchemas": {"a": {"properties": {"b": {"type": "integer"}},
                                         "required": ["b"]}}}
    _check(byte_ctx, schema,
           ["{}", '{"a":1,"b":2}', '{"a":1,"b":2,"c":"s"}', '{"b":"s"}'],
           ['{"a":1}', '{"a":1,"b":"s"}'])


def test_dependent_schemas_false_bans_trigger(byte_ctx):
    _check(byte_ctx, {"dependentSchemas": {"a": False}},
           ["{}", '{"b":1}'], ['{"a":1}'])
    # A banned required key empties the object arm.
    _refusal(byte_ctx,
             {"type": "object", "required": ["a"], "dependentSchemas": {"a": False}},
             "UNSATISFIABLE")


def test_dependencies_draft07(byte_ctx):
    schema = {"$schema": D7,
              "dependencies": {"a": ["b"], "c": {"properties": {"d": {"type": "integer"}}}}}
    _check(byte_ctx, schema,
           ["{}", '{"a":1,"b":2}', '{"c":1,"d":2}', '{"c":1}'],
           ['{"a":1}', '{"c":1,"d":"s"}'])


def test_dependencies_closed_object_reduction(byte_ctx):
    # "zz" can never appear in a closed object: the trigger is banned.
    schema = {"properties": {"a": {}}, "additionalProperties": False,
              "dependentRequired": {"a": ["zz"]}}
    _check(byte_ctx, schema, ["{}"], ['{"a":1}'])


# ---------------------------------------------------------------------------
# Tuple items / additionalItems / prefixItems
# ---------------------------------------------------------------------------

def test_tuple_items_draft07(byte_ctx):
    schema = {"$schema": D7, "items": [{"type": "string"}, {"type": "integer"}]}
    _check(byte_ctx, schema,
           ['["a",1]', '["a"]', "[]", '["a",1,true,null]', "5"],
           ['[1]', '["a","b"]'])


def test_tuple_additional_items_false(byte_ctx):
    schema = {"$schema": D7, "items": [{"type": "string"}], "additionalItems": False}
    _check(byte_ctx, schema, ['["a"]', "[]"], ['["a",1]', '["a","b"]'])


def test_tuple_additional_items_schema(byte_ctx):
    schema = {"$schema": D7, "items": [{"type": "string"}],
              "additionalItems": {"type": "integer"}}
    _check(byte_ctx, schema,
           ['["a"]', '["a",1]', '["a",1,2]'],
           ['["a","b"]', '["a",1,"b"]'])


def test_additional_items_without_tuple_is_ignored(byte_ctx):
    _check(byte_ctx, {"$schema": D7, "items": {"type": "integer"},
                      "additionalItems": False},
           ["[1,2]", "[1]"], ['["a"]'])


def test_prefix_items(byte_ctx):
    schema = {"prefixItems": [{"type": "string"}, {"type": "integer"}]}
    _check(byte_ctx, schema,
           ['["a",1]', '["a"]', "[]", '["a",1,true]'],
           ['[1]', '["a","b"]'])


def test_prefix_items_with_items_schema(byte_ctx):
    schema = {"prefixItems": [{"type": "string"}], "items": {"type": "integer"}}
    _check(byte_ctx, schema, ['["a"]', '["a",1,2]', "[]"], ['["a","b"]', "[1]"])


def test_prefix_items_with_items_false(byte_ctx):
    schema = {"prefixItems": [{"type": "string"}], "items": False}
    _check(byte_ctx, schema, ['["a"]', "[]"], ['["a",1]', '["a",1,2]'])


def test_tuple_with_min_max_items(byte_ctx):
    schema = {"prefixItems": [{"type": "string"}], "minItems": 1, "maxItems": 2}
    _check(byte_ctx, schema, ['["a"]', '["a",9]'], ["[]", '["a",1,2]'])


# ---------------------------------------------------------------------------
# contains / minContains / maxContains
# ---------------------------------------------------------------------------

def test_contains_basic(byte_ctx):
    _check(byte_ctx, {"contains": {"type": "integer"}},
           ["[1]", '["a",1]', "[1,2]", "5"],
           ['["a","b"]', "[]", '[["x"]]'])


def test_contains_min(byte_ctx):
    _check(byte_ctx, {"contains": {"type": "integer"}, "minContains": 2},
           ["[1,2]", '[1,"a",2]'], ["[1]", '["a",1]', "[]"])


def test_contains_max(byte_ctx):
    # minContains defaults to 1, so the empty array is invalid here.
    _check(byte_ctx, {"contains": {"type": "integer"}, "maxContains": 1},
           ["[1]", '["a",1]'], ["[1,2]", '[1,"a",2]', "[]"])


def test_contains_zero_min(byte_ctx):
    _check(byte_ctx, {"contains": {"type": "integer"}, "minContains": 0},
           ["[]", '["a"]', "[1]"], [])
    # contains with an empty language is vacuous only at minContains: 0.
    _check(byte_ctx, {"contains": {"type": "string", "minLength": 1, "maxLength": 0},
                      "minContains": 0},
           ["[]", "[1]"], [])
    _refusal(byte_ctx, {"type": "array", "contains": False}, "UNSATISFIABLE")


def test_contains_bounds_refusals(byte_ctx):
    _refusal(byte_ctx, {"type": "array", "contains": {"type": "integer"},
                        "minContains": 3, "maxContains": 2},
             "UNSATISFIABLE")
    _refusal(byte_ctx, {"type": "array", "contains": {"type": "integer"},
                        "minContains": 2, "maxItems": 1},
             "UNSATISFIABLE")
    _refusal(byte_ctx, {"type": "array", "contains": {"type": "integer"},
                        "minContains": 1.5},
             "INVALID_SCHEMA")


def test_contains_ignores_counters_without_contains(byte_ctx):
    _check(byte_ctx, {"type": "array", "minContains": 5}, ["[]", "[1]"], [])


def test_contains_complex_schema(byte_ctx):
    schema = {"contains": {"type": "object", "properties": {"x": {"type": "integer"}},
                           "required": ["x"]}}
    _check(byte_ctx, schema,
           ['[{"x":1}]', '[1,{"x":1,"y":2}]'],
           ['[{"x":"s"}]', '[{"y":1}]', "[]"])


# ---------------------------------------------------------------------------
# uniqueItems
# ---------------------------------------------------------------------------

def test_unique_items_scalars(byte_ctx):
    _check(byte_ctx, {"uniqueItems": True},
           ["[1,2,3]", "[]", "[1]", '["a","b"]', "[true,false,null]", "5"],
           ["[1,1]", "[1,1.0]", '[1,"1",1]', '["a","a"]', "[true,true]", "[null,null]"])


def test_unique_items_numbers_by_value(byte_ctx):
    _check(byte_ctx, {"uniqueItems": True},
           ["[1,10]", "[0,-1]"],
           ["[1,1.0]", "[1,10e-1]", "[1.5,1.50]", "[0,-0]", "[0e5,0]"])


def test_unique_items_structural(byte_ctx):
    _check(byte_ctx, {"uniqueItems": True},
           ['[[1],[2]]', '[{"a":1},{"a":2}]', '[{"a":1,"b":2},{"b":2}]', '[[1,2],[2,1]]'],
           ['[[1],[1]]', '[{"a":1,"b":2},{"b":2,"a":1}]', '[[1,2],[1,2]]'])


def test_unique_items_false_is_noop(byte_ctx):
    _check(byte_ctx, {"uniqueItems": False}, ["[1,1]", '["a","a"]'], [])


def test_unique_items_with_items_schema(byte_ctx):
    _check(byte_ctx, {"items": {"type": "integer"}, "uniqueItems": True},
           ["[1,2]", "[]"], ["[1,1]", '["a"]', "[1,1.0]"])


def test_unique_items_form(byte_ctx):
    _refusal(byte_ctx, {"type": "array", "uniqueItems": "yes"}, "INVALID_SCHEMA")


# ---------------------------------------------------------------------------
# Dialect scoping of the P2 keywords
# ---------------------------------------------------------------------------

def test_p2_keywords_ignored_outside_their_dialect(byte_ctx):
    # propertyNames/contains are draft-06+: unknown in draft-04.
    _check(byte_ctx, {"$schema": D4, "propertyNames": False}, ['{"a":1}'], [])
    _check(byte_ctx, {"$schema": D4, "contains": {"type": "null"}}, ["[1]"], [])
    # prefixItems is 2020-12 only.
    _check(byte_ctx, {"$schema": D7, "prefixItems": [{"type": "null"}]}, ["[1]"], [])
    # additionalItems is unknown in 2020-12.
    _check(byte_ctx, {"additionalItems": False, "type": "array"}, ["[1,2]"], [])
    # dependencies is pre-2019-09 only.
    _check(byte_ctx, {"dependencies": {"a": ["b"]}}, ['{"a":1}'], [])
    # dependentRequired/dependentSchemas/minContains are 2019-09+.
    _check(byte_ctx, {"$schema": D7, "dependentRequired": {"a": ["b"]}}, ['{"a":1}'], [])
    _check(byte_ctx, {"$schema": D7, "minContains": 5, "type": "array"}, ["[]"], [])


def test_contains_draft06(byte_ctx):
    _check(byte_ctx, {"$schema": D6, "contains": {"type": "integer"}},
           ["[1]"], ['["a"]', "[]"])


def test_dependent_required_2019_09(byte_ctx):
    _check(byte_ctx, {"$schema": V19, "dependentRequired": {"a": ["b"]}},
           ["{}", '{"a":1,"b":2}'], ['{"a":1}'])


# ---------------------------------------------------------------------------
# canonical-v1 stays frozen
# ---------------------------------------------------------------------------

def test_canonical_still_refuses_p2(byte_ctx):
    canon = b"canonical-v1"
    _refusal(byte_ctx, {"uniqueItems": True}, "UNSUPPORTED_FEATURE", profile=canon)
    _refusal(byte_ctx, {"minProperties": 1}, "UNSUPPORTED_FEATURE", profile=canon)
    _refusal(byte_ctx, {"contains": {"type": "integer"}}, "UNSUPPORTED_FEATURE",
             profile=canon)
    _refusal(byte_ctx, {"dependentRequired": {"a": ["b"]}}, "UNSUPPORTED_FEATURE",
             profile=canon)
    _refusal(byte_ctx, {"prefixItems": [{"type": "integer"}]}, "UNSUPPORTED_FEATURE",
             profile=canon)
    _refusal(byte_ctx, {"propertyNames": {"type": "string"}}, "UNSUPPORTED_FEATURE",
             profile=canon)


# ---------------------------------------------------------------------------
# $ref interaction
# ---------------------------------------------------------------------------

def test_p2_keywords_next_to_ref(byte_ctx):
    # draft-07 ignores siblings of $ref; 2019-09+ conjoins them (P6b).
    s7 = {"$schema": D7,
          "definitions": {"any": {"type": "integer"}},
          "properties": {"x": {"$ref": "#/definitions/any", "uniqueItems": True}}}
    _check(byte_ctx, s7, ['{"x":3}'], ['{"x":"s"}'])
    _check(byte_ctx,
           {"$defs": {"a": {"type": "object"}},
            "properties": {"x": {"$ref": "#/$defs/a", "minProperties": 1}}},
           ['{"x":{"a":1}}', "{}"], ['{"x":{}}', '{"x":[]}', '{"x":3}'])


def test_p2_inside_ref_target(byte_ctx):
    schema = {"$defs": {"pair": {"prefixItems": [{"type": "string"}, {"type": "integer"}]}},
              "properties": {"p": {"$ref": "#/$defs/pair"}}}
    _check(byte_ctx, schema, ['{"p":["a",1]}'], ['{"p":[1]}'])
