"""Value-preserving serializer (spec-v1 byte language).

Tests python/bolorgir/serializer.py against the contract of
docs/semantics-spec-v1 sections 4-7: schema-driven key order (4.3),
verbatim values (numeric lexemes, escapes per 4.2), the --compact
round-trip examples of section 5, graceful fallback when the schema
gives no order, and the section 7 incompatibility bucket.

Pure stdlib: no core build needed. The bolorgir package itself is
imported from python/ (source tree) when importable.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "python"))

from bolorgir.serializer import (  # noqa: E402
    SerializationIncompatibleError,
    serialize_for_schema,
    serialize_json_text,
    serialize_value,
)


def ok(instance, schema):
    payload, status = serialize_for_schema(instance, schema)
    assert status.ok, f"unexpected refusal: {status}"
    assert isinstance(payload, bytes)
    return payload.decode("utf-8")


def incompatible(instance, schema):
    payload, status = serialize_for_schema(instance, schema)
    assert payload is None
    assert not status.ok
    assert status.reason
    return status


def ok_text(text, schema):
    payload, status = serialize_json_text(text, schema)
    assert status.ok, f"unexpected refusal: {status}"
    assert isinstance(payload, bytes)
    return payload.decode("utf-8")


def ok_value(value, schema):
    payload, status = serialize_value(value, schema)
    assert status.ok, f"unexpected refusal: {status}"
    assert isinstance(payload, bytes)
    return payload.decode("utf-8")


AB_SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"],
    "additionalProperties": False,
}

OPEN_SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
}


# ---------------------------------------------------------------------------
# Section 5 verified --compact examples
# ---------------------------------------------------------------------------

def test_compact_keeps_data_order_but_serializer_reorders():
    # {"b": 2, "a": 1} --compact -> {"b":2,"a":1} (data order kept);
    # the serializer emits the schema declaration order instead.
    assert json.dumps({"b": 2, "a": 1}, ensure_ascii=False,
                      separators=(",", ":")) == '{"b":2,"a":1}'
    assert ok('{"b": 2, "a": 1}', AB_SCHEMA) == '{"a":1,"b":2}'


def test_fraction_spelling_kept():
    assert ok('1.0', {"type": "number"}) == "1.0"


def test_exponent_spelling_vs_python_float():
    # Text input: the lexeme is preserved exactly. Object input: the
    # value already went through a Python float, so 1e2 arrives as
    # 100.0 and emits as 100.0 (section 5, spelling per data).
    assert ok('1e2', {"type": "number"}) == "1e2"
    assert ok(1e2, {"type": "number"}) == "100.0"


def test_control_escapes_slash_and_non_ascii():
    # Lowercase \u00xx for controls without a short form, short forms
    # for \b \f \n \r \t, "/" unescaped, non-ASCII raw (4.2).
    assert ok('"\\u0001\\u001f"', True) == '"\\u0001\\u001f"'
    assert ok('"\\b\\f\\n\\r\\t"', True) == '"\\b\\f\\n\\r\\t"'
    assert ok('"a/b"', True) == '"a/b"'
    assert ok('"café"', True) == '"café"'
    assert ok('"\\u00e9"', True) == '"é"'  # \uXXXX input -> raw output
    assert ok('"quote \\" backslash \\\\"', True) == '"quote \\" backslash \\\\"'


def test_compact_output_is_byte_language():
    # Every --compact example re-serializes to itself when the data
    # order already conforms to the schema order.
    assert ok('{"a":1,"b":2}', AB_SCHEMA) == '{"a":1,"b":2}'


# ---------------------------------------------------------------------------
# Key order (4.3)
# ---------------------------------------------------------------------------

def test_declared_keys_schema_order_skipped_optional():
    schema = {
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "z": {"type": "integer"},
        }
    }
    assert ok('{"z": 1, "x": 2}', schema) == '{"x":2,"z":1}'
    assert ok('{"y": 1}', schema) == '{"y":1}'


def test_open_object_undeclared_keys_keep_data_order():
    # Declared keys first in schema order; undeclared keys of an open
    # object (additionalProperties absent) keep their data order (4.3).
    assert ok('{"u2": 1, "b": 2, "u1": 3, "a": 4}', OPEN_SCHEMA) == \
        '{"a":4,"b":2,"u2":1,"u1":3}'


def test_closed_object_undeclared_keys_kept_not_dropped():
    # Invalid per the schema, but the serializer never drops data;
    # rejecting the value is the validator's job.
    assert ok('{"b": 1, "extra": 2}', AB_SCHEMA) == '{"b":1,"extra":2}'


def test_nested_reorder_and_arrays_keep_order():
    schema = {
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"},
                                   "name": {"type": "string"}},
                },
            },
            "meta": {
                "type": "object",
                "properties": {"a": {}, "b": {}},
            },
        }
    }
    instance = ('{"meta": {"b": 1, "a": 2},'
                ' "items": [{"name": "n", "id": 7}]}')
    assert ok(instance, schema) == \
        '{"items":[{"id":7,"name":"n"}],"meta":{"a":2,"b":1}}'


def test_tuple_items_and_prefix_items():
    tuple_schema = {"items": [{"properties": {"a": {}, "b": {}}},
                              {"properties": {"c": {}, "d": {}}}]}
    assert ok('[{"b": 1, "a": 2}, {"d": 3, "c": 4}]', tuple_schema) == \
        '[{"a":2,"b":1},{"c":4,"d":3}]'
    prefix_schema = {"prefixItems": [{"properties": {"a": {}, "b": {}}}],
                     "items": {"properties": {"c": {}, "d": {}}}}
    assert ok('[{"b": 1, "a": 2}, {"d": 3, "c": 4}]', prefix_schema) == \
        '[{"a":2,"b":1},{"c":4,"d":3}]'


def test_pattern_properties_do_not_order():
    # patternProperties keys are not declared: data order is kept.
    schema = {"properties": {"a": {}},
              "patternProperties": {"^x": {"type": "integer"}}}
    assert ok('{"x2": 1, "x1": 2, "a": 3}', schema) == '{"a":3,"x2":1,"x1":2}'


def test_allof_merged_first_appearance_order():
    # Merged objects: first appearance across subschemas in document
    # order (4.3); the own properties count as the first branch.
    schema = {
        "properties": {"b": {}},
        "allOf": [
            {"properties": {"c": {}, "a": {}}},
            {"properties": {"a": {}, "d": {}}},
        ],
    }
    assert ok('{"d": 1, "a": 2, "c": 3, "b": 4}', schema) == \
        '{"b":4,"c":3,"a":2,"d":1}'


def test_ref_through_ordering():
    schema = {
        "properties": {"wrap": {"$ref": "#/definitions/inner"}},
        "definitions": {
            "inner": {"properties": {"a": {}, "b": {}}}
        },
    }
    assert ok('{"wrap": {"b": 1, "a": 2}}', schema) == '{"wrap":{"a":2,"b":1}}'


def test_ref_chain_and_pointer_escapes():
    schema = {
        "$ref": "#/definitions/alias",
        "definitions": {
            "alias": {"$ref": "#/definitions/real"},
            "real": {"properties": {"a/b": {}, "a~b": {}, "z": {}}},
        },
    }
    assert ok('{"z": 1, "a~b": 2, "a/b": 3}', schema) == \
        '{"a/b":3,"a~b":2,"z":1}'


def test_fallback_no_reorder_when_schema_gives_no_order():
    # Boolean schemas, missing subschemas and non-local refs give no
    # order: full data order is kept.
    assert ok('{"b": 1, "a": 2}', True) == '{"b":1,"a":2}'
    assert ok('{"b": 1, "a": 2}', {}) == '{"b":1,"a":2}'
    # A uniquely fitting anyOf/oneOf branch supplies its declared order.
    assert ok('{"b": 1, "a": 2}',
              {"anyOf": [{"properties": {"a": {}, "b": {}}}]}) == '{"a":2,"b":1}'
    assert ok('{"b": 1, "a": 2}',
              {"oneOf": [{"properties": {"a": {}, "b": {}}}]}) == '{"a":2,"b":1}'
    assert ok('{"b": 1, "a": 2}',
              {"$ref": "http://example.com/s.json#/x"}) == '{"b":1,"a":2}'
    assert ok('{"b": 1, "a": 2}',
              {"$ref": "#/definitions/missing"}) == '{"b":1,"a":2}'
    # ...but values under a no-order node are still emitted, nested
    # declared nodes inside arrays still order.
    assert ok('[{"b": 1, "a": 2}]',
              {"items": {"properties": {"a": {}, "b": {}}}}) == '[{"a":2,"b":1}]'


def test_values_of_wrong_type_round_trip():
    # Negative examples are serialized like any value: no assertion
    # checking happens here (section 7).
    assert ok('{"a": "not an int", "b": [1, 2]}', AB_SCHEMA) == \
        '{"a":"not an int","b":[1,2]}'
    assert ok('[1, 2]', AB_SCHEMA) == '[1,2]'


# ---------------------------------------------------------------------------
# Numbers (4.4 / section 6: values untouched)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lexeme", [
    "1.0", "1e2", "1E2", "1e+2", "-0", "-0.0", "100.00", "0.001",
    "12345678901234567890123456789012345678901234567890",
    "1e-300", "-1.5e+17", "0", "42",
])
def test_numeric_lexemes_preserved_verbatim(lexeme):
    assert ok(lexeme, {"type": "number"}) == lexeme
    assert ok(lexeme, True) == lexeme
    assert ok(f'[{lexeme}, {lexeme}]', True) == f'[{lexeme},{lexeme}]'


def test_big_integer_object_input_exact():
    big = 2**80 + 12345
    assert ok(big, True) == str(big)
    assert ok({"n": big}, True) == '{"n":%d}' % big


def test_object_input_scalars():
    assert ok({"a": 1, "b": [True, False, None, "s", 2.5]}, True) == \
        '{"a":1,"b":[true,false,null,"s",2.5]}'
    assert ok(-0.0, True) == "-0.0"


# ---------------------------------------------------------------------------
# Section 7: the serialization-incompatible bucket
# ---------------------------------------------------------------------------

def test_bucket_duplicate_key_with_pointer():
    status = incompatible('{"a": 1, "b": {"c": 1, "c": 2}}', True)
    assert "duplicate" in status.reason
    assert status.pointer == "/b"


def test_bucket_non_finite_float():
    for bad in (float("nan"), float("inf"), float("-inf")):
        status = incompatible({"a": bad}, True)
        assert "non-finite" in status.reason
        assert status.pointer == "/a"


def test_bucket_non_string_key():
    status = incompatible({1: "x"}, True)
    assert "non-string" in status.reason


def test_bucket_invalid_json_text():
    status = incompatible('{"a": 1', True)
    assert "invalid JSON" in status.reason
    status = incompatible('{"a": 1} trailing', True)
    assert "invalid JSON" in status.reason


def test_bucket_lone_surrogate():
    status = incompatible('"\\ud800"', True)
    assert "surrogate" in status.reason


def test_bucket_marker_attribute_for_harness():
    # The runner duck-types on `serialization_incompatible` (§7).
    exc = SerializationIncompatibleError("some reason", "/p")
    assert exc.serialization_incompatible is True
    assert exc.reason == "some reason"
    assert exc.pointer == "/p"
    assert "/p" in str(exc)


# ---------------------------------------------------------------------------
# Round-trip: output parses back to the same value (2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("instance,schema", [
    ('{"b": 2.5, "a": [1e2, -0, "\\u00e9/\\u0001"], "u": {"z": null}}', OPEN_SCHEMA),
    ('[1.0, {"y": true, "x": "a\\nb"}]', True),
    ('{"deep": {"b": 1, "a": {"d": 2, "c": 3}}}',
     {"properties": {"deep": {"properties": {"a": {"properties": {"c": {}, "d": {}}}, "b": {}}}}}),
])
def test_value_round_trip(instance, schema):
    out = ok(instance, schema)
    assert json.loads(out) == json.loads(instance)
    assert " " not in out  # 4.1: no whitespace outside strings


# ---------------------------------------------------------------------------
# Explicit input kinds: serialize_json_text vs serialize_value (R4)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "hello", "1", "true", "[1]", '"x"', "", "null", "{}", '{"a": 1}',
    "  spaced  ", "1.5e3", "café",
])
def test_value_entry_never_parses_strings(value):
    # R4: a ready Python str is a JSON string VALUE, never JSON text.
    out = ok_value(value, {"type": "string"})
    decoded = json.loads(out)
    assert decoded == value and type(decoded) is str
    assert out == json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def test_value_entry_root_scalars_and_containers():
    assert ok_value(1, {"type": "number"}) == "1"
    assert ok_value(2.5, {"type": "number"}) == "2.5"
    assert ok_value(1e2, {"type": "number"}) == "100.0"  # section 5 repr policy
    assert ok_value(True, {"type": "boolean"}) == "true"
    assert ok_value(None, {"type": "null"}) == "null"
    assert ok_value([1, "x"], True) == '[1,"x"]'
    assert ok_value({"b": 2, "a": 1}, AB_SCHEMA) == '{"a":1,"b":2}'


def test_text_entry_parses_and_preserves_lexemes():
    assert ok_text('{"b": 2, "a": 1}', AB_SCHEMA) == '{"a":1,"b":2}'
    assert ok_text("1e2", {"type": "number"}) == "1e2"  # lexeme verbatim
    assert ok_text('"1"', {"type": "string"}) == '"1"'  # JSON string text


def test_text_entry_rejects_parsed_values():
    for not_text in (1, 2.5, True, None, [1], {"a": 1}):
        with pytest.raises(TypeError):
            serialize_json_text(not_text, True)


def test_value_entry_bytes_are_not_json_values():
    payload, status = serialize_value(b'"x"', True)
    assert payload is None and not status.ok
    assert "unsupported value type" in status.reason


def test_legacy_dual_mode_unchanged():
    # serialize_for_schema keeps the historical auto-detect: str/bytes is
    # JSON text, anything else is a parsed value.
    assert ok('{"b": 2, "a": 1}', AB_SCHEMA) == '{"a":1,"b":2}'
    assert ok(b'[1.0]', True) == "[1.0]"
    assert ok({"b": 2, "a": 1}, AB_SCHEMA) == '{"a":1,"b":2}'
    status = incompatible("hello", {"type": "string"})  # text mode: invalid JSON
    assert "invalid JSON" in status.reason
