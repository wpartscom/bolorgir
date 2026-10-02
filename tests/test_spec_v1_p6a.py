"""spec-v1 P6a end-to-end tests (ROADMAP rev-2 P6a, ADR-0009): unevaluated*.

Covers unevaluatedProperties/unevaluatedItems (2019-09 and 2020-12) through
the C ABI: the fold path (no conditional applicators), the inert reductions,
the scenario path over anyOf/oneOf/allOf/if-then-else/dependentSchemas/$ref,
nested unevaluated* subschemas, the dialect gate (unknown keyword in
draft-07 and earlier, and in canonical-v1), and the documented compile-time
refusals (combinator nested in a combinator branch, several conjunctive
`contains`, external $ref, the scenario budget). The module is skipped until
the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

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


def _check(ctx, schema, ok_docs, bad_docs, profile=b"spec-v1"):
    g = _compile(ctx, schema, profile=profile)
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
# Fold path: no conditional applicators
# ---------------------------------------------------------------------------

def test_unevaluated_properties_fold(byte_ctx):
    schema = {"$schema": V20, "type": "object",
              "properties": {"a": {"type": "integer"}},
              "unevaluatedProperties": {"type": "string"}}
    _check(byte_ctx, schema,
           ['{}', '{"a":1}', '{"b":"x"}', '{"a":1,"b":"x"}'],
           ['{"a":"x"}', '{"b":1}', '{"a":1,"b":2}'])


def test_unevaluated_properties_false_fold(byte_ctx):
    schema = {"$schema": V19, "properties": {"a": True},
              "unevaluatedProperties": False}
    _check(byte_ctx, schema,
           ['{}', '{"a":1}', '[]', '1'],
           ['{"b":1}', '{"a":1,"b":2}'])


def test_unevaluated_items_fold_2020(byte_ctx):
    schema = {"$schema": V20, "prefixItems": [{"type": "integer"}],
              "unevaluatedItems": {"type": "string"}}
    _check(byte_ctx, schema,
           ['[]', '[1]', '[1,"a"]', '[1,"a","b"]'],
           ['["a"]', '[1,2]', '[1,"a",2]'])


def test_unevaluated_items_fold_2019_tuple(byte_ctx):
    # 2019-09: tuple `items` evaluates the covered positions.
    schema = {"$schema": V19, "items": [{"type": "integer"}],
              "unevaluatedItems": False}
    _check(byte_ctx, schema, ['[]', '[1]', '{}'], ['["a"]', '[1,2]'])


def test_unevaluated_inert_reductions(byte_ctx):
    # unevaluatedProperties: true evaluates nothing new: inert.
    _check(byte_ctx, {"$schema": V20, "properties": {"a": True},
                      "unevaluatedProperties": True},
           ['{}', '{"a":1}', '{"b":2}'], [])
    # additionalProperties already covers every undeclared key: inert.
    _check(byte_ctx, {"$schema": V20, "properties": {"a": True},
                      "additionalProperties": {"type": "string"},
                      "unevaluatedProperties": False},
           ['{}', '{"a":1}', '{"b":"x"}'], ['{"b":2}'])
    # items (2020-12) covers every tail element: unevaluatedItems inert.
    _check(byte_ctx, {"$schema": V20, "items": {"type": "integer"},
                      "unevaluatedItems": False},
           ['[]', '[1,2]'], ['["a"]'])


def test_unevaluated_boolean_subschemas(byte_ctx):
    _check(byte_ctx, {"$schema": V20, "unevaluatedProperties": False},
           ['{}', '[]', '"s"'], ['{"a":1}'])
    _check(byte_ctx, {"$schema": V20, "unevaluatedItems": True},
           ['[]', '[1,2]', '{}'], [])


# ---------------------------------------------------------------------------
# Scenario path: conditional applicators
# ---------------------------------------------------------------------------

def test_unevaluated_properties_any_of(byte_ctx):
    # JSON-Schema-Test-Suite unevaluatedProperties.json case 10.
    schema = {"$schema": V20, "type": "object",
              "properties": {"foo": {"type": "string"}},
              "anyOf": [{"properties": {"bar": {"const": "bar"}},
                         "required": ["bar"]},
                        {"properties": {"baz": {"const": "baz"}},
                         "required": ["baz"]},
                        {"properties": {"quux": {"const": "quux"}},
                         "required": ["quux"]}],
              "unevaluatedProperties": False}
    _check(byte_ctx, schema,
           ['{"foo":"a","bar":"bar"}', '{"foo":"a","baz":"baz"}',
            '{"foo":"a","quux":"quux"}',
            '{"foo":"a","bar":"bar","baz":"baz"}'],
           ['{"foo":"a"}', '{"foo":"a","bar":"x"}',
            '{"foo":"a","bar":"bar","quux":1}'])


def test_unevaluated_properties_one_of(byte_ctx):
    # Suite case 11.
    schema = {"$schema": V20, "type": "object",
              "properties": {"foo": {"type": "string"}},
              "oneOf": [{"properties": {"bar": {"const": "bar"}},
                         "required": ["bar"]},
                        {"properties": {"baz": {"const": "baz"}},
                         "required": ["baz"]}],
              "unevaluatedProperties": False}
    _check(byte_ctx, schema,
           ['{"foo":"a","bar":"bar"}', '{"foo":"a","baz":"baz"}'],
           ['{"foo":"a"}', '{"foo":"a","bar":"bar","baz":"baz"}'])


def test_unevaluated_properties_all_of_ref(byte_ctx):
    # Suite case 31: $ref inside allOf / oneOf.
    schema = {"$schema": V20,
              "$defs": {"one": {"properties": {"a": True}},
                        "two": {"required": ["x"],
                                "properties": {"x": True}}},
              "allOf": [{"$ref": "#/$defs/one"},
                        {"properties": {"b": True}},
                        {"oneOf": [{"$ref": "#/$defs/two"},
                                   {"required": ["y"],
                                    "properties": {"y": True}}]}],
              "unevaluatedProperties": False}
    _check(byte_ctx, schema,
           ['{"a":1,"b":2,"x":3}', '{"a":1,"b":2,"y":4}'],
           ['{"a":1,"b":2}', '{"a":1,"b":2,"x":3,"y":4}',
            '{"a":1,"b":2,"x":3,"z":5}'])


def test_unevaluated_properties_cyclic_ref(byte_ctx):
    # Suite case 30: single cyclic ref.
    schema = {"$schema": V20, "type": "object",
              "properties": {"x": {"$ref": "#"}},
              "unevaluatedProperties": False}
    _check(byte_ctx, schema,
           ['{}', '{"x":{}}', '{"x":{"x":{}}}'],
           ['{"y":1}', '{"x":{"y":1}}'])


def test_unevaluated_properties_dependent_schemas(byte_ctx):
    # Suite case 16 shape: dependentSchemas contributes evaluations.
    schema = {"$schema": V20, "type": "object",
              "properties": {"a": True},
              "dependentSchemas": {"a": {"properties": {"b": True}}},
              "unevaluatedProperties": False}
    _check(byte_ctx, schema,
           ['{}', '{"a":1}', '{"a":1,"b":2}'],
           ['{"b":2}', '{"a":1,"c":3}'])


def test_unevaluated_items_if_then_contains(byte_ctx):
    # Suite case 21: contains under if/then chains.
    schema = {"$schema": V20,
              "if": {"contains": {"const": "a"}},
              "then": {"if": {"contains": {"const": "b"}},
                       "then": {"if": {"contains": {"const": "c"}}}},
              "unevaluatedItems": False}
    _check(byte_ctx, schema,
           ['[]', '["a"]', '["a","b"]', '["a","b","c"]'],
           ['["x"]', '["a","x"]', '["a","b","x"]'])


def test_unevaluated_items_adjacent_contains(byte_ctx):
    # Suite case 19.
    schema = {"$schema": V20, "prefixItems": [True],
              "contains": {"type": "string"}, "unevaluatedItems": False}
    _check(byte_ctx, schema,
           ['[1,"foo"]'],
           ['[1,2]', '[1,2,"foo"]'])


def test_unevaluated_nested_subschema(byte_ctx):
    # Suite case 27: unevaluated* inside a property value; evaluation in an
    # uncle schema is not significant for the nested keyword.
    schema = {"$schema": V20, "type": "object",
              "properties": {"foo": {"type": "object",
                                     "properties": {"bar": {"type": "string"}},
                                     "unevaluatedProperties": False}},
              "anyOf": [{"properties": {"foo": {"properties":
                                                {"faz": {"type": "string"}}}}}]}
    _check(byte_ctx, schema,
           ['{}', '{"foo":{"bar":"test"}}'],
           ['{"foo":{"bar":"test","faz":"test"}}', '{"foo":{"bar":1}}'])


# ---------------------------------------------------------------------------
# Dialect gate
# ---------------------------------------------------------------------------

def test_unevaluated_ignored_in_draft07(byte_ctx):
    # Unknown keyword in draft-07: ignored, extra keys/elements pass.
    _check(byte_ctx, {"$schema": D7, "properties": {"a": True},
                      "unevaluatedProperties": False},
           ['{"a":1}', '{"a":1,"b":2}'], [])


def test_unevaluated_refused_in_canonical(byte_ctx):
    # canonical-v1 is frozen: the keyword is outside the MVP profile.
    _refusal(byte_ctx, {"properties": {"a": True},
                        "unevaluatedProperties": False},
             "UNSUPPORTED_FEATURE", profile=b"canonical-v1")


# ---------------------------------------------------------------------------
# Documented refusals (ADR-0009)
# ---------------------------------------------------------------------------

def test_refusal_nested_combinator_branch(byte_ctx):
    # Suite case 32: dynamic evaluation inside nested refs. The runtime
    # thread budget is refused at compile time with a pointer.
    schema = {"$schema": V20,
              "$defs": {"one": {"oneOf": [{"$ref": "#/$defs/two"},
                                          {"required": ["b"],
                                           "properties": {"b": True}}]},
                        "two": {"oneOf": [{"required": ["c"],
                                           "properties": {"c": True}},
                                          {"required": ["d"],
                                           "properties": {"d": True}}]}},
              "oneOf": [{"$ref": "#/$defs/one"},
                        {"required": ["a"], "properties": {"a": True}}],
              "unevaluatedProperties": False}
    msg = _refusal(byte_ctx, schema, "UNSUPPORTED_FEATURE")
    assert "/oneOf/0" in msg


def test_refusal_multiple_conjunctive_contains(byte_ctx):
    # Suite case 20: unevaluatedItems depends on multiple nested contains.
    schema = {"$schema": V20,
              "allOf": [{"contains": {"multipleOf": 2}},
                        {"contains": {"multipleOf": 3}}],
              "unevaluatedItems": {"multipleOf": 5}}
    _refusal(byte_ctx, schema, "UNSUPPORTED_FEATURE")


def test_refusal_external_ref(byte_ctx):
    schema = {"$schema": V20, "$ref": "http://ex.com/other.json",
              "unevaluatedProperties": False}
    _refusal(byte_ctx, schema, "UNSUPPORTED_FEATURE")


def test_refusal_any_of_branch_budget(byte_ctx):
    # Six live anyOf branches exceed the subset budget (ADR-0009 cap 5).
    branches = [{"required": [k], "properties": {k: True}}
                for k in ("a", "b", "c", "d", "e", "f")]
    schema = {"$schema": V20, "anyOf": branches,
              "unevaluatedProperties": False}
    _refusal(byte_ctx, schema, "UNSUPPORTED_FEATURE")
