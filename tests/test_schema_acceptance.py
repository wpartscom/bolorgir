"""FR-1 support table: positive and negative cases with exact blg_status.

Runs through a backend (bolorgir package -> exception mapped to a code;
ctypes fallback -> code directly). Expected codes follow DESIGN §1.7/§10 and
TZ FR-1.

Contested spec points (decisions fixed):
- $schema of another dialect -> UNSUPPORTED_FEATURE;
- type as an array -> UNSUPPORTED_FEATURE;
- required with an unknown name / additionalProperties != false /
  missing mandatory keys -> INVALID_SCHEMA;
- $defs next to $ref is allowed (otherwise a root $ref is inexpressible).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conftest import backend_available  # noqa: E402

_ok, _why = backend_available(os.environ.get("BLG_TEST_BACKEND", "auto"))
if not _ok:
    pytest.skip(f"core unavailable: {_why}", allow_module_level=True)

try:
    import blg_ctypes as zc
except ImportError:  # package-only access, no .so - only the codes are needed
    zc = None
    from conftest import _status_code_of_exception  # noqa: F401

OK = 0
INV = 2
UNSUP = 3
UNSAT = 4
RES = 8

STATUS_NAMES = {
    0: "OK", 1: "INVALID_ARGUMENT", 2: "INVALID_SCHEMA", 3: "UNSUPPORTED_FEATURE",
    4: "UNSATISFIABLE_CONSTRAINT", 5: "UNSUPPORTED_TOKENIZER", 6: "INVALID_TOKEN",
    7: "DEAD_END", 8: "RESOURCE_LIMIT", 9: "CANCELLED", 10: "BUSY",
    11: "WRONG_STATE", 12: "BUFFER_TOO_SMALL", 13: "INTERNAL",
}


def obj(props, required, **extra):
    s = {"type": "object", "properties": props, "required": required,
         "additionalProperties": False}
    s.update(extra)
    return s


INT = {"type": "integer"}

POSITIVE = [
    ("integer", INT),
    ("number", {"type": "number"}),
    ("boolean", {"type": "boolean"}),
    ("null", {"type": "null"}),
    ("string", {"type": "string"}),
    ("string_bounds", {"type": "string", "minLength": 1, "maxLength": 5}),
    ("array", {"type": "array", "items": INT}),
    ("array_bounds", {"type": "array", "items": INT, "minItems": 0, "maxItems": 3}),
    ("object_empty", obj({}, [])),
    ("object", obj({"a": INT, "b": {"type": "string"}}, ["a"])),
    ("enum_string", {"type": "string", "enum": ["buy", "sell"]}),
    ("enum_number", '{"type":"number","enum":[1e2, 1.50]}'),
    ("enum_no_type", {"enum": ["a", "ab"]}),
    ("const", {"type": "string", "const": "x"}),
    ("defs_ref", {"$defs": {"d": INT},
                  "type": "object",
                  "properties": {"x": {"$ref": "#/$defs/d"}},
                  "required": ["x"], "additionalProperties": False}),
    ("ref_with_annotation", {"$defs": {"d": INT},
                             "type": "object",
                             "properties": {"x": {"$ref": "#/$defs/d",
                                                  "description": "doc"}},
                             "required": ["x"], "additionalProperties": False}),
    ("schema_dialect", {"$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "integer"}),
    ("schema_dialect_hash", {"$schema": "https://json-schema.org/draft/2020-12/schema#",
                             "type": "integer"}),
    ("annotations", {"type": "string", "title": "t", "description": "d",
                     "$comment": "c", "examples": ["a"], "default": "a"}),
    ("nested", obj({"arr": {"type": "array", "items": obj({"x": INT}, ["x"])}}, ["arr"])),
    ("deep_64_within_limit", None),   # filled in below
]

NEGATIVE = [
    # known keywords outside the MVP -> UnsupportedFeature
    ("anyOf", obj({"a": dict(INT, anyOf=[INT])}, ["a"]), UNSUP),
    ("oneOf", dict(INT, oneOf=[INT]), UNSUP),
    ("allOf", dict(INT, allOf=[INT]), UNSUP),
    ("not", dict(INT, **{"not": INT}), UNSUP),
    ("if_then_else", dict(INT, **{"if": INT, "then": INT, "else": INT}), UNSUP),
    ("minimum", dict(INT, minimum=0), UNSUP),
    ("maximum", dict(INT, maximum=5), UNSUP),
    ("multipleOf", dict(INT, multipleOf=2), UNSUP),
    ("exclusiveMinimum", dict(INT, exclusiveMinimum=0), UNSUP),
    ("exclusiveMaximum", dict(INT, exclusiveMaximum=5), UNSUP),
    ("pattern", {"type": "string", "pattern": "^a"}, UNSUP),
    ("format", {"type": "string", "format": "email"}, UNSUP),
    ("patternProperties", obj({"a": INT}, ["a"], patternProperties={"^x": INT}), UNSUP),
    ("uniqueItems", {"type": "array", "items": INT, "uniqueItems": True}, UNSUP),
    ("contains", {"type": "array", "items": INT, "contains": INT}, UNSUP),
    ("dependentRequired", obj({"a": INT}, [], dependentRequired={"a": ["b"]}), UNSUP),
    ("dependentSchemas", obj({"a": INT}, [], dependentSchemas={"a": INT}), UNSUP),
    ("unevaluatedProperties", obj({"a": INT}, [], unevaluatedProperties=False), UNSUP),
    ("propertyNames", obj({"a": INT}, [], propertyNames={"type": "string"}), UNSUP),
    ("prefixItems", {"type": "array", "items": INT, "prefixItems": [INT]}, UNSUP),
    ("minContains", {"type": "array", "items": INT, "minContains": 1}, UNSUP),
    ("id", dict(INT, **{"$id": "http://x"}), UNSUP),
    ("anchor", dict(INT, **{"$anchor": "a"}), UNSUP),
    ("dynamicRef", {"$dynamicRef": "#x"}, UNSUP),
    ("definitions", dict(INT, definitions={}), UNSUP),
    ("dependencies", obj({"a": INT}, [], dependencies={"a": ["b"]}), UNSUP),
    ("readOnly", dict(INT, readOnly=True), UNSUP),
    ("unknown_keyword", dict(INT, somethingNew=1), UNSUP),
    # boolean schemas
    ("boolean_schema_true", "true", UNSUP),
    ("boolean_schema_false", "false", UNSUP),
    ("boolean_schema_nested", obj({"a": True}, []), UNSUP),
    # $ref
    ("ref_external", {"$ref": "https://example.com/s.json"}, UNSUP),
    ("ref_external_rel", {"$ref": "other.json#/x"}, UNSUP),
    ("ref_cycle", {"$defs": {"a": {"$ref": "#/$defs/a"}}, "$ref": "#/$defs/a"}, INV),
    ("ref_cycle2", {"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}},
                    "$ref": "#/$defs/a"}, INV),
    ("ref_unresolvable", {"$defs": {"a": INT}, "$ref": "#/$defs/nope"}, INV),
    ("ref_with_validation_keyword", {"$defs": {"a": INT},
                                     "$ref": "#/$defs/a", "minLength": 2}, UNSUP),
    # type
    ("type_array", {"type": ["string", "null"]}, UNSUP),
    ("type_unknown_string", {"type": "dict"}, INV),
    # structural errors -> InvalidSchema
    ("required_unknown_name", obj({"a": INT}, ["b"]), INV),
    ("required_duplicate", {"type": "object", "properties": {"a": INT},
                            "required": ["a", "a"], "additionalProperties": False}, INV),
    ("additionalProperties_true", {"type": "object", "properties": {},
                                   "required": [], "additionalProperties": True}, INV),
    ("additionalProperties_missing", {"type": "object", "properties": {},
                                      "required": []}, INV),
    ("properties_missing", {"type": "object", "required": [],
                            "additionalProperties": False}, INV),
    ("required_missing", {"type": "object", "properties": {},
                          "additionalProperties": False}, INV),
    ("items_missing", {"type": "array"}, INV),
    ("minLength_negative", {"type": "string", "minLength": -1}, INV),
    ("minLength_fractional", {"type": "string", "minLength": 1.5}, INV),
    ("maxItems_negative", {"type": "array", "items": INT, "maxItems": -1}, INV),
    ("duplicate_keys", '{"type":"object","properties":{"a":1,"a":2},'
                       '"required":[],"additionalProperties":false}', INV),
    ("schema_other_dialect", {"$schema": "http://json-schema.org/draft-07/schema#",
                              "type": "string"}, UNSUP),
    # unsatisfiable constraints -> UnsatisfiableConstraint
    ("min_gt_max_length", {"type": "string", "minLength": 3, "maxLength": 2}, UNSAT),
    ("min_gt_max_items", {"type": "array", "items": INT, "minItems": 2, "maxItems": 1}, UNSAT),
    ("enum_fully_filtered_type", {"type": "integer", "enum": ["a", "b"]}, UNSAT),
    ("enum_fully_filtered_length", {"type": "string", "maxLength": 1,
                                    "enum": ["ab", "abc"]}, UNSAT),
    # enum shape
    ("enum_mixed_types", {"enum": ["a", 1]}, UNSUP),
    ("enum_non_scalar", {"enum": [[1], {"a": 1}]}, UNSUP),
    # depth -> ResourceLimit (filled in below)
    ("depth_over_limit", None, RES),
]


def _deep_schema(n):
    s = {"type": "integer"}
    for _ in range(n):
        s = {"type": "array", "items": s}
    return s


POSITIVE = [(n, _deep_schema(60) if s is None else s) for n, s in POSITIVE]
NEGATIVE = [(n, _deep_schema(70) if s is None else s, c) for n, s, c in NEGATIVE]


def _compile_code(backend, schema):
    """-> blg_status code of the compilation (OK or an error code)."""
    if backend.name == "ctypes":
        try:
            g = backend.zg.compile_schema(backend.ctx, schema)
        except backend.zg.CoreFailure as e:
            return e.status
        backend.zg.grammar_release(g)
        return OK
    # package: exception -> code
    from conftest import _status_code_of_exception
    try:
        c = backend.compile(schema)
    except Exception as e:
        code = _status_code_of_exception(e)
        assert code is not None, (
            f"cannot map package exception to a blg_status code: "
            f"{type(e).__name__}: {e}")
        return code
    c.release()
    return OK


@pytest.mark.parametrize("name,schema", POSITIVE, ids=[n for n, _ in POSITIVE])
def test_schema_accepted(backend_factory, byte_tok, name, schema):
    backend = backend_factory(byte_tok, "lazy")
    code = _compile_code(backend, schema)
    assert code == OK, f"{name}: expected OK, got {STATUS_NAMES.get(code, code)}"


@pytest.mark.parametrize("name,schema,expected", NEGATIVE,
                         ids=[n for n, _, _ in NEGATIVE])
def test_schema_rejected_with_exact_code(backend_factory, byte_tok,
                                         name, schema, expected):
    backend = backend_factory(byte_tok, "lazy")
    code = _compile_code(backend, schema)
    assert code == expected, (
        f"{name}: expected {STATUS_NAMES[expected]}, "
        f"got {STATUS_NAMES.get(code, code)}")
