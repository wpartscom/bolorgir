"""ADR-0005 completion reachability for the spec-v1 P3 combinators.

Same contract as the P2 module: for every combinator the engine now
compiles, every prefix of a valid document must reach a state from which
completion is still possible - feeding the prefix never dead-ends, and
after the prefix the state either accepts (can_end) or admits at least
one continuation token in the mask, in particular the document's own
next byte. The comb-group machinery (parallel branch threads, verdict
at the confirmed boundary, the allOf/ifelse sweep) is exactly where an
alive-but-uncompletable state would hide.

The module is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")


@pytest.fixture
def byte_ctx(byte_tok):
    ctx = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    yield ctx
    ctx.destroy()


CASES = [
    # anyOf (plain union)
    ({"anyOf": [{"type": "string"}, {"type": "integer"}]},
     ['"abc"', "-12"]),
    # oneOf: the verdict is data-dependent - a branch dies only at the
    # confirmed boundary, so every live prefix must keep a completion.
    ({"oneOf": [{"type": "number"}, {"type": "integer"}]},
     ["1.5", "-0.25"]),
    ({"oneOf": [{"const": "ab"}, {"const": "ac"}, {"type": "integer"}]},
     ['"ab"', "12"]),
    # allOf: every branch must accept; the sweep kills a doomed group.
    ({"allOf": [{"type": "integer"}, {"const": 5}]},
     ["5", "5.0", "5e0"]),
    ({"type": "array", "items": {"allOf": [{"type": "integer"}, {"const": 5}]}},
     ["[5,5,5]", "[]"]),
    # if/then/else, including the AnyJSON carrier for an absent else.
    ({"if": {"const": 0}, "then": {"const": 1}, "else": {"type": "number"}},
     ["1", "2.5", "-3"]),
    ({"if": {"const": 0}, "then": {"const": 1}},
     ["1", "2", '"x"', "[1,true]"]),
    # not over the supported complement forms.
    ({"not": {"type": "integer"}}, ["1.5", '"x"', "[1]", '{"a":1}']),
    ({"not": {"const": "ab"}}, ['"abc"', '"a"', "1"]),
    ({"not": {"enum": [1, 2]}}, ["3", '"1"', "null"]),
    ({"not": {"required": ["a"]}}, ['{"b":1}', "{}"]),
    ({"not": {"not": {"type": "integer"}}}, ["1", "2.0"]),
    ({"not": {"anyOf": [{"type": "integer"}, {"type": "string"}]}},
     ["1.5", "true", "[1]"]),
    # Nesting: a comb group as an object property and inside oneOf.
    ({"type": "object",
      "properties": {"k": {"oneOf": [{"type": "number"}, {"type": "integer"}]}},
      "required": ["k"]},
     ['{"k":1.5}']),
    ({"oneOf": [{"allOf": [{"type": "integer"}, {"const": 5}]},
                {"not": {"type": "number"}}]},
     ["5", '"x"', "[1]"]),
]


def test_p3_completion_reachability(byte_ctx):
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


def test_p3_rejected_documents_dead_end(byte_ctx):
    # Documents outside the language must hit a dead thread at or before
    # their last byte (never a late can_end surprise): either accept
    # fails mid-document or the final state does not accept.
    REJECTS = [
        ({"oneOf": [{"type": "number"}, {"type": "integer"}]},
         ["1", "1.0", "1e2"]),  # accepted by both branches
        ({"allOf": [{"type": "integer"}, {"const": 5}]},
         ["6", "5.5", "[5]"]),
        ({"if": {"const": 0}, "then": {"const": 1}},
         ["0", "0.0"]),
        ({"not": {"type": "integer"}}, ["1", "1.0"]),
        ({"not": {"const": "ab"}}, ['"ab"']),
        ({"not": {"required": ["a"]}}, ['{"a":1}', "1"]),
    ]
    for schema, docs in REJECTS:
        g = zg.compile_schema(byte_ctx, json.dumps(schema).encode(),
                              profile=b"spec-v1")
        try:
            for doc in docs:
                data = doc.encode()
                s = zg.Session(byte_ctx, g)
                try:
                    alive = True
                    for b in data:
                        if s.accept(b) != zg.BLG_OK:
                            alive = False
                            break
                    assert not alive or not s.can_end(), (
                        f"{schema} must reject {doc!r}")
                finally:
                    s.destroy()
        finally:
            zg.grammar_release(g)


def test_p3_oneof_boundary_mask(byte_ctx):
    # oneOf[num_v, int_num] after "1": both branches are still live
    # (a boundary verdict is impossible mid-value), so digits and '.' are
    # admitted; a byte that cannot extend any branch is not.
    schema = {"oneOf": [{"type": "number"}, {"type": "integer"}]}
    g = zg.compile_schema(byte_ctx, json.dumps(schema).encode(),
                          profile=b"spec-v1")
    try:
        s = zg.Session(byte_ctx, g)
        try:
            for b in b"1":
                assert s.accept(b) == zg.BLG_OK
            ids = s.fill_mask_ids()
            assert ord(".") in ids
            assert ord("5") in ids
            assert ord("x") not in ids
            # "1" itself is complete under both branches (popCount 2):
            # the document may not end here.
            assert not s.can_end()
        finally:
            s.destroy()
    finally:
        zg.grammar_release(g)
