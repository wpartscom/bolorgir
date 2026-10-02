"""ADR-0005 completion reachability for spec-v1 P5 recursion (ADR-0008).

Same contract as the P3/P4 modules: for every recursive schema the engine
compiles, every prefix of a valid document must reach a state from which
completion is still possible - feeding the prefix never dead-ends, and
after the prefix the state either accepts (can_end) or admits at least
one continuation token in the mask, in particular the document's own
next byte. The unroll limit is mask-visible (the recursive position at
the bottom behaves as `false`: a banned key / a dropped arm), so a live
prefix of an in-language document must never die, and a document one
level beyond the limit must die at or before its last byte.

The module is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

UNROLL = 8  # ADR-0008 REF_UNROLL_CAP

LINKED_LIST = {
    "type": "object",
    "properties": {"v": {"type": "integer"}, "next": {"$ref": "#"}},
    "required": ["v"],
    "additionalProperties": False,
}

TREE = {
    "type": "object",
    "properties": {
        "val": {"type": "string"},
        "left": {"$ref": "#"},
        "right": {"$ref": "#"},
    },
    "additionalProperties": False,
}

MUTUAL = {
    "$defs": {
        "a": {"type": "object",
              "properties": {"b": {"$ref": "#/$defs/b"}},
              "additionalProperties": False},
        "b": {"type": "object",
              "properties": {"a": {"$ref": "#/$defs/a"}},
              "additionalProperties": False},
    },
    "$ref": "#/$defs/a",
}

NESTED_ARRAYS = {"$anchor": "node", "type": "array",
                 "items": {"$ref": "#node"}}


@pytest.fixture
def byte_ctx(byte_tok):
    ctx = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    yield ctx
    ctx.destroy()


def _list_doc(depth: int) -> str:
    doc = '{"v":%d}' % depth
    for i in range(depth - 1, -1, -1):
        doc = '{"v":%d,"next":%s}' % (i, doc)
    return doc


def _tree_doc(depth: int) -> str:
    return "{}" if depth == 0 else '{"left":%s}' % _tree_doc(depth - 1)


def _nest_array(depth: int) -> str:
    return "[]" if depth == 0 else "[%s]" % _nest_array(depth - 1)


def _valid_cases():
    cases = []
    for depth in (0, 1, 2, 5, UNROLL):
        cases.append((LINKED_LIST, _list_doc(depth)))
        cases.append((TREE, _tree_doc(depth)))
        cases.append((NESTED_ARRAYS, _nest_array(depth)))
    cases.append((TREE, '{"left":{"right":{"left":{}}}}'))
    deep = "{}"
    for _ in range(4):
        deep = '{"b":{"a":%s}}' % deep
    cases.append((MUTUAL, deep))
    return cases


def _reject_cases():
    cases = []
    for depth in (UNROLL + 1, UNROLL + 3):
        cases.append((LINKED_LIST, _list_doc(depth)))
        cases.append((TREE, _tree_doc(depth)))
        cases.append((NESTED_ARRAYS, _nest_array(depth)))
    cases.append((LINKED_LIST, '{"v":1,"next":null}'))
    cases.append((LINKED_LIST, '{"v":1,"next":{}}'))
    cases.append((TREE, '{"left":{"right":{"left":null}}}'))
    cases.append((NESTED_ARRAYS, "[[],1]"))
    return cases


def test_p5_recursion_completion_reachability(byte_ctx):
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


def test_p5_recursion_beyond_limit_dead_ends(byte_ctx):
    # Documents past the unroll limit (or otherwise outside the language)
    # must hit a dead thread at or before their last byte: either accept
    # fails mid-document or the final state does not accept.
    for schema, doc in _reject_cases():
        g = zg.compile_schema(byte_ctx, json.dumps(schema).encode(),
                              profile=b"spec-v1")
        try:
            data = doc.encode()
            s = zg.Session(byte_ctx, g)
            try:
                alive = True
                for b in data:
                    if s.accept(b) != zg.BLG_OK:
                        alive = False
                        break
                assert not alive or not s.can_end(), (
                    f"{schema} must not accept {doc!r}")
            finally:
                s.destroy()
        finally:
            zg.grammar_release(g)
