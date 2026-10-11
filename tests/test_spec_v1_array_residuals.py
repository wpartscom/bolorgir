"""Exact masks for the array residuals of ADR-0005 Decision 4.

contains and uniqueItems decide each element at its close (the tap reparse
against `contains`, the seen set), so a started element can already be
doomed: the only slot left must match `contains` but the element began with
a byte no matching value starts with, or every value the element can still
spell is a duplicate. The mask must exclude the byte that starts such an
element, and direct accept must refuse it (ADR-0005 Decision 1).

Every case runs in lazy/adaptive x fast path on/off on the byte tokenizer.
The module is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")


@pytest.fixture(params=[(zg.BLG_MODE_LAZY, zg.BLG_MASK_FAST_PATH_ON),
                        (zg.BLG_MODE_LAZY, zg.BLG_MASK_FAST_PATH_OFF),
                        (zg.BLG_MODE_ADAPTIVE, zg.BLG_MASK_FAST_PATH_ON),
                        (zg.BLG_MODE_ADAPTIVE, zg.BLG_MASK_FAST_PATH_OFF)],
                ids=["lazy+fast", "lazy", "adaptive+fast", "adaptive"])
def ctx(request, byte_tok):
    mode, fast = request.param
    c = zg.Context(byte_tok, mode, mask_fast_path=fast)
    yield c
    assert c.destroy() == zg.BLG_OK


ONE_FALSE = {"type": "array", "maxItems": 1, "contains": {"enum": [False]}}
ONE_FALSE_UNEVAL = dict(ONE_FALSE, unevaluatedItems={"type": "string"})
B_UNIQUE = {"type": "array", "minItems": 2, "maxItems": 3,
            "contains": {"const": "b"}, "unevaluatedItems": {"type": "number"},
            "uniqueItems": True}
AB_UNIQUE = {"type": "array", "items": {"enum": ["a", "b"]}, "uniqueItems": True}

# (schema, prefix, allowed bytes, EOS allowed)
MASKS = [
    (ONE_FALSE, b"[", b"f", False),
    (ONE_FALSE, b"[false", b"]", False),
    (ONE_FALSE_UNEVAL, b"[", b"f", False),
    # A string element must match contains (unevaluated items are numbers)
    # and so spell "b" again.
    (B_UNIQUE, b'["b",0.5,', b"-0123456789", False),
    (AB_UNIQUE, b'["a",', b'"', False),
    (AB_UNIQUE, b'["a","', b"b", False),
    (AB_UNIQUE, b'["a","b"', b"]", False),
]

DEAD = [
    (ONE_FALSE, b'["'),
    (ONE_FALSE_UNEVAL, b'["'),
    (B_UNIQUE, b'["b",0.5,"'),
    (AB_UNIQUE, b'["a","a'),
    (AB_UNIQUE, b'["a","b",'),
]


def _compile(ctx, schema):
    return zg.compile_schema(ctx, json.dumps(schema).encode(), profile=b"spec-v1")


def _session_after(ctx, grammar, prefix: bytes):
    s = zg.Session(ctx, grammar)
    for i, b in enumerate(prefix):
        assert s.accept(b) == zg.BLG_OK, f"prefix byte {i} of {prefix!r} refused"
    return s


@pytest.mark.parametrize("schema,prefix,allowed,eos", MASKS,
                         ids=[f"{i}" for i in range(len(MASKS))])
def test_mask_is_exact(ctx, schema, prefix, allowed, eos):
    g = _compile(ctx, schema)
    try:
        s = _session_after(ctx, g, prefix)
        try:
            got = s.fill_mask_ids()
            want = set(allowed) | ({256} if eos else set())
            assert got == want, (
                f"{prefix!r} under {schema}: extra {sorted(got - want)} "
                f"missing {sorted(want - got)}")
        finally:
            s.destroy()
    finally:
        zg.grammar_release(g)


@pytest.mark.parametrize("schema,prefix", DEAD, ids=[f"{i}" for i in range(len(DEAD))])
def test_dead_prefix_is_refused(ctx, schema, prefix):
    g = _compile(ctx, schema)
    try:
        s = _session_after(ctx, g, prefix[:-1])
        try:
            assert s.accept(prefix[-1]) != zg.BLG_OK, f"{prefix!r} under {schema}"
        finally:
            s.destroy()
    finally:
        zg.grammar_release(g)
