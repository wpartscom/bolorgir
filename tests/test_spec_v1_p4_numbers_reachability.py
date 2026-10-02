"""ADR-0005 completion reachability for the spec-v1 P4 numeric keywords.

Same contract as the P3 module: for every schema the engine now compiles,
every prefix of a valid document must reach a state from which completion
is still possible - feeding the prefix never dead-ends, and after the
prefix the state either accepts (can_end) or admits at least one
continuation token in the mask, in particular the document's own next
byte. The exact value verdict of the num_range/num_mult machines is
produced only at a confirmed boundary; a violated bound is a
mask-visible rejection there (the offending delimiter byte is simply not
admitted), so a live prefix of a valid document must never die.

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
    # Inclusive bounds; the value may recover mid-token (2 -> 25 -> 2.5).
    ({"minimum": 2},
     ["2", "25", "2.5", "2e0", "10", '"x"', "[1]"]),
    ({"minimum": -2, "maximum": 3},
     ["-2", "-2.0", "0", "3", "2.999", "-1.5"]),
    # Exclusive bounds.
    ({"exclusiveMinimum": 1.1},
     ["1.2", "1.10001", "12", '"s"']),
    ({"exclusiveMaximum": 3.0},
     ["2.2", "2.99999", "-100"]),
    # multipleOf: the verdict is exact at the boundary.
    ({"multipleOf": 2},
     ["10", "0", "-4", "100.0", "2e3", '"foo"']),
    ({"multipleOf": 1.5},
     ["0", "4.5", "3", "7.5", "-3"]),
    ({"multipleOf": 0.0001},
     ["0.0075", "1", "123.4567"]),
    # Range + divisibility through the typed core (allOf comb parts).
    ({"type": "integer", "minimum": 5, "multipleOf": 2},
     ["6", "10", "100", "6.0"]),
    ({"type": "number", "minimum": 0, "maximum": 1},
     ["0", "0.5", "1", "0.999"]),
    # P3 combinator shapes with numeric assertions.
    ({"allOf": [{"minimum": 5}, {"multipleOf": 2}]},
     ["6", "10", '"x"']),
    ({"oneOf": [{"maximum": 0}, {"minimum": 10}]},
     ["-1", "10", "15"]),
    ({"not": {"const": 5}, "type": "integer", "minimum": 2},
     ["2", "3", "6"]),
    # Inside containers.
    ({"type": "object",
      "properties": {"k": {"type": "number", "minimum": 0, "maximum": 1}},
      "required": ["k"]},
     ['{"k":0.5}', '{"k":1}']),
    ({"type": "array", "items": {"multipleOf": 2}},
     ["[]", "[2,4]", "[2,4.0]"]),
    ({"type": "array", "items": {"type": "integer", "minimum": 0}},
     ["[0,1,100]", "[]"]),
]


def test_p4_numbers_completion_reachability(byte_ctx):
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


def test_p4_numbers_rejected_documents_dead_end(byte_ctx):
    # Documents outside the language must hit a dead thread at or before
    # their last byte (never a late can_end surprise): either accept fails
    # mid-document or the final state does not accept.
    REJECTS = [
        ({"minimum": 2}, ["1", "1.999", "-5", "0.1e1"]),
        ({"maximum": 3}, ["4", "3.0001", "30", "1e1"]),
        ({"exclusiveMinimum": 1.1}, ["1.1", "1.10", "0.6"]),
        ({"exclusiveMaximum": 3.0}, ["3", "3.0", "3.5"]),
        ({"multipleOf": 2}, ["7", "0.5", "5e-1", "15"]),
        ({"multipleOf": 1.5}, ["35", "0.75", "4.4"]),
        ({"multipleOf": 0.0001}, ["0.00751", "1e-5"]),
        ({"type": "integer", "minimum": 5, "multipleOf": 2},
         ["5", "4", "7", "6.5", "5.0"]),
        ({"allOf": [{"minimum": 5}, {"multipleOf": 2}]}, ["5", "4", "7"]),
        ({"oneOf": [{"maximum": 0}, {"minimum": 10}]}, ["5", "0.0001", "9.999"]),
        ({"type": "array", "items": {"multipleOf": 2}}, ["[3]", "[2,3]"]),
        ({"type": "object",
          "properties": {"k": {"type": "number", "minimum": 0, "maximum": 1}},
          "required": ["k"]},
         ['{"k":1.5}', '{"k":-0.1}']),
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


def test_p4_numbers_boundary_mask(byte_ctx):
    # {"minimum": 2} after "1": the token can still recover (15, 1.5 are
    # in range), so digits and '.' are admitted; a delimiter is not -
    # ending the value at 1 would violate the bound (the rejection is
    # mask-visible at the boundary byte, ADR-0005).
    schema = {"type": "number", "minimum": 2}
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
            assert ord("e") in ids
            assert not s.can_end()
        finally:
            s.destroy()
        # After "2" the document may end (2 >= 2) or continue.
        s = zg.Session(byte_ctx, g)
        try:
            for b in b"2":
                assert s.accept(b) == zg.BLG_OK
            assert s.can_end()
            ids = s.fill_mask_ids()
            assert ord("5") in ids
            assert ord(".") in ids
        finally:
            s.destroy()
    finally:
        zg.grammar_release(g)
