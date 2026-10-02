"""ADR-0005 completion reachability for the spec-v1 P2 constructs.

For every P2 keyword the engine now compiles, every prefix of a valid
document must reach a state from which completion is still possible:
feeding the prefix never dead-ends, and after the prefix the state either
accepts (can_end) or admits at least one continuation token in the mask -
in particular the document's own next byte. A state that is alive but has
no completion at all is the ADR-0005 violation this pins against.

The module is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

D7 = "http://json-schema.org/draft-07/schema"


@pytest.fixture
def byte_ctx(byte_tok):
    ctx = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    yield ctx
    ctx.destroy()


CASES = [
    # additionalProperties as a schema
    ({"properties": {"a": {"type": "string"}},
      "additionalProperties": {"type": "integer"}},
     ['{"a":"x","b":12}', '{"b":1}']),
    # patternProperties literal + additionalProperties false
    ({"patternProperties": {"foo": {"type": "integer"}},
      "additionalProperties": False},
     ['{"xfoox":12}', "{}"]),
    # propertyNames
    ({"propertyNames": {"minLength": 2, "maxLength": 3}},
     ['{"ab":1}', "{}"]),
    # min/maxProperties
    ({"minProperties": 1, "maxProperties": 2},
     ['{"a":1}', '{"a":1,"b":2}']),
    ({"properties": {"a": {}, "b": {}}, "additionalProperties": False,
      "minProperties": 1, "maxProperties": 1},
     ['{"a":1}']),
    # dependentRequired (declared, out-of-order, undeclared)
    ({"dependentRequired": {"a": ["b", "c"]}},
     ['{"a":1,"b":2,"c":3}', '{"b":1}']),
    ({"properties": {"b": {}, "a": {}}, "dependentRequired": {"a": ["b"]}},
     ['{"b":1,"a":2}', "{}"]),
    # dependentSchemas (capture + re-parse at close)
    ({"dependentSchemas": {"a": {"properties": {"b": {"type": "integer"}},
                                 "required": ["b"]}}},
     ['{"a":1,"b":2,"c":"s"}', '{"x":1}']),
    ({"dependentSchemas": {"a": False}}, ['{"b":1}', "{}"]),
    # dependencies (draft-07 mixed forms)
    ({"$schema": D7,
      "dependencies": {"a": ["b"], "c": {"properties": {"d": {"type": "integer"}}}}},
     ['{"a":1,"b":2}', '{"c":1,"d":2}']),
    # tuple items / additionalItems / prefixItems
    ({"$schema": D7, "items": [{"type": "string"}, {"type": "integer"}]},
     ['["a",1,"x"]', '["a"]']),
    ({"$schema": D7, "items": [{"type": "string"}], "additionalItems": False},
     ['["a"]', "[]"]),
    ({"prefixItems": [{"type": "string"}], "items": {"type": "integer"}},
     ['["a",1,2]', "[]"]),
    ({"prefixItems": [{"type": "string"}], "items": False},
     ['["a"]', "[]"]),
    # contains with min/maxContains
    ({"contains": {"type": "integer"}, "minContains": 2, "maxContains": 3},
     ['[1,"a",2]', '[1,2,3]', '[1,"a","b",2]']),
    ({"contains": {"type": "object", "properties": {"x": {"type": "integer"}},
                   "required": ["x"]}},
     ['[1,{"x":1}]']),
    # uniqueItems (scalars, numbers by value, structural)
    ({"uniqueItems": True},
     ['[1,2.5,"a",true,null]', '[{"a":1,"b":2},[1,2]]']),
    ({"items": {"enum": ["a", "b", "c"]}, "uniqueItems": True},
     ['["a","b"]', '["c"]']),
]


def test_p2_completion_reachability(byte_ctx):
    for schema, docs in CASES:
        g = zg.compile_schema(byte_ctx, json.dumps(schema).encode(),
                              profile=b"spec-v1")
        try:
            for doc in docs:
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


def test_p2_partial_constructs_keep_completion(byte_ctx):
    # Spot-check mid-construct states that exercised the ADR-0005 D4
    # residuals: a contains deficit that remaining slots can still cover,
    # and a dependency whose required name is still emittable.
    schema = {"contains": {"type": "integer"}, "minContains": 2, "maxItems": 3}
    g = zg.compile_schema(byte_ctx, json.dumps(schema).encode(), profile=b"spec-v1")
    try:
        s = zg.Session(byte_ctx, g)
        try:
            for b in b'[1,"a",':
                assert s.accept(b) == zg.BLG_OK
            # matched=1 of 2, one slot left: only an integer completes.
            ids = s.fill_mask_ids()
            assert ids, "contains deficit with one slot left must keep a completion"
            assert ord("2") in ids
            # ']' is exactly excluded by the count-based residual. A
            # non-integer element start is still admitted: deciding that a
            # string cannot match contains {"type":"integer"} is
            # language intersection (decidable exclusivity), which is P3
            # scope - the thread dies at the failing close instead.
            assert ord("]") not in ids
        finally:
            s.destroy()
    finally:
        zg.grammar_release(g)

    schema = {"dependentRequired": {"a": ["b"]}}
    g = zg.compile_schema(byte_ctx, json.dumps(schema).encode(), profile=b"spec-v1")
    try:
        s = zg.Session(byte_ctx, g)
        try:
            for b in b'{"a":1,':
                assert s.accept(b) == zg.BLG_OK
            # The trigger is seen: "b" must still be emittable, "}" must not.
            ids = s.fill_mask_ids()
            assert ord('"') in ids
            assert ord("}") not in ids
        finally:
            s.destroy()
    finally:
        zg.grammar_release(g)
