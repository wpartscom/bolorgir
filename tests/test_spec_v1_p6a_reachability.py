"""ADR-0005 completion reachability for spec-v1 P6a unevaluated* (ADR-0009).

Same contract as the P3/P4/P5 modules: for every schema the engine
compiles, every prefix of a valid document must reach a state from which
completion is still possible - feeding the prefix never dead-ends, and
after the prefix the state either accepts (can_end) or admits at least
one continuation token in the mask, in particular the document's own
next byte. A document outside the language must die at or before its
last byte. Covers the fold path, the scenario path over the conditional
applicators, and nested unevaluated* subschemas. The module is skipped
until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

V20 = "https://json-schema.org/draft/2020-12/schema"

FOLD_OBJ = {"$schema": V20, "type": "object",
            "properties": {"a": {"type": "integer"}},
            "unevaluatedProperties": {"type": "string"}}

FOLD_ARR = {"$schema": V20, "prefixItems": [{"type": "integer"}],
            "unevaluatedItems": {"type": "string"}}

ANY_OF = {"$schema": V20, "type": "object",
          "properties": {"foo": {"type": "string"}},
          "anyOf": [{"properties": {"bar": {"const": "bar"}},
                     "required": ["bar"]},
                    {"properties": {"baz": {"const": "baz"}},
                     "required": ["baz"]}],
          "unevaluatedProperties": False}

ONE_OF = {"$schema": V20, "type": "object",
          "properties": {"foo": {"type": "string"}},
          "oneOf": [{"properties": {"bar": {"const": "bar"}},
                     "required": ["bar"]},
                    {"properties": {"baz": {"const": "baz"}},
                     "required": ["baz"]}],
          "unevaluatedProperties": False}

ALL_OF_REF = {"$schema": V20,
              "$defs": {"one": {"properties": {"a": True}},
                        "two": {"required": ["x"],
                                "properties": {"x": True}}},
              "allOf": [{"$ref": "#/$defs/one"},
                        {"properties": {"b": True}},
                        {"oneOf": [{"$ref": "#/$defs/two"},
                                   {"required": ["y"],
                                    "properties": {"y": True}}]}],
              "unevaluatedProperties": False}

CONTAINS = {"$schema": V20, "prefixItems": [True],
            "contains": {"type": "string"}, "unevaluatedItems": False}

NESTED = {"$schema": V20, "type": "object",
          "properties": {"foo": {"type": "object",
                                 "properties": {"bar": {"type": "string"}},
                                 "unevaluatedProperties": False}},
          "anyOf": [{"properties": {"foo": {"properties":
                                            {"faz": {"type": "string"}}}}}]}


@pytest.fixture
def byte_ctx(byte_tok):
    ctx = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    yield ctx
    ctx.destroy()


def _valid_cases():
    return [
        (FOLD_OBJ, '{}'),
        (FOLD_OBJ, '{"a":1,"b":"x"}'),
        (FOLD_ARR, '[]'),
        (FOLD_ARR, '[1,"a","b"]'),
        (ANY_OF, '{"foo":"a","bar":"bar"}'),
        (ANY_OF, '{"foo":"a","bar":"bar","baz":"baz"}'),
        (ONE_OF, '{"foo":"a","baz":"baz"}'),
        (ALL_OF_REF, '{"a":1,"b":2,"x":3}'),
        (ALL_OF_REF, '{"a":1,"b":2,"y":4}'),
        (CONTAINS, '[1,"foo"]'),
        (NESTED, '{"foo":{"bar":"test"}}'),
    ]


def _reject_cases():
    return [
        (FOLD_OBJ, '{"a":1,"b":2}'),
        (FOLD_OBJ, '{"b":1}'),
        (FOLD_ARR, '[1,2]'),
        (ANY_OF, '{"foo":"a"}'),
        (ANY_OF, '{"foo":"a","bar":"x"}'),
        (ONE_OF, '{"foo":"a","bar":"bar","baz":"baz"}'),
        (ALL_OF_REF, '{"a":1,"b":2}'),
        (ALL_OF_REF, '{"a":1,"b":2,"x":3,"z":5}'),
        (CONTAINS, '[1,2]'),
        (CONTAINS, '[1,2,"foo"]'),
        (NESTED, '{"foo":{"bar":"test","faz":"test"}}'),
    ]


def test_p6a_unevaluated_completion_reachability(byte_ctx):
    for schema, doc in _valid_cases():
        g = zg.compile_schema(byte_ctx, json.dumps(schema).encode(),
                              profile=b"spec-v1")
        try:
            data = doc.encode()
            s = zg.Session(byte_ctx, g)
            try:
                for i, b in enumerate(data):
                    st = s.accept(b)
                    assert st == zg.BLG_OK, (
                        f"{schema} / {doc!r}: byte {i} dead-ends a live prefix")
                    if i + 1 < len(data):
                        nxt = data[i + 1]
                        ids = s.fill_mask_ids()
                        assert s.can_end() or ids, (
                            f"{schema} / {doc!r}: state after {data[:i+1]!r} "
                            f"has no completion")
                        assert nxt in ids, (
                            f"{schema} / {doc!r}: next byte {bytes([nxt])!r} "
                            f"not in mask after {data[:i+1]!r}")
                assert s.can_end(), f"{schema} must accept {doc!r}"
            finally:
                s.destroy()
        finally:
            zg.grammar_release(g)


def test_p6a_unevaluated_out_of_language_dead_ends(byte_ctx):
    # Documents outside the language must hit a dead thread at or before
    # their last byte: either accept fails mid-stream or can_end is false.
    for schema, doc in _reject_cases():
        g = zg.compile_schema(byte_ctx, json.dumps(schema).encode(),
                              profile=b"spec-v1")
        try:
            data = doc.encode()
            s = zg.Session(byte_ctx, g)
            try:
                alive = True
                for i, b in enumerate(data):
                    if s.accept(b) != zg.BLG_OK:
                        alive = False
                        break
                assert not alive or not s.can_end(), (
                    f"{schema} must not accept {doc!r}")
            finally:
                s.destroy()
        finally:
            zg.grammar_release(g)
