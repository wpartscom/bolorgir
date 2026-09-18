"""T6: C ABI smoke test via the thin ctypes layer (tests/zg_ctypes.py).

ABI version, context create/destroy, stats struct filling, accounted memory
returning to zero after child objects are destroyed.
Skipped until the core is built.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("zg_ctypes", reason="libzig_constraints.so is not built")
import corpora  # noqa: E402
from conftest import make_byte_tokenizer  # noqa: E402

SCHEMA_TZ = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell"]},
        "amount": {"type": "integer"},
    },
    "required": ["action", "amount"],
    "additionalProperties": False,
}


@pytest.fixture(scope="module")
def spec():
    return make_byte_tokenizer()


def test_abi_version():
    assert zg.LIB.zg_abi_version() == zg.ZG_ABI_VERSION == 1


def test_context_create_destroy(spec):
    ctx = zg.Context(spec, zg.ZG_MODE_LAZY)
    assert ctx.handle
    assert ctx.destroy() == zg.ZG_OK


def test_all_modes_create(spec):
    for mode in (zg.ZG_MODE_LAZY, zg.ZG_MODE_ADAPTIVE, zg.ZG_MODE_PRECOMPUTE):
        ctx = zg.Context(spec, mode)
        assert ctx.destroy() == zg.ZG_OK


def test_stats_struct_filled(spec):
    ctx = zg.Context(spec, zg.ZG_MODE_ADAPTIVE)
    try:
        g = zg.compile_schema(ctx, SCHEMA_TZ)
        s = zg.Session(ctx, g)
        try:
            status, _ = s.fill_mask_words()
            assert status == zg.ZG_OK
            assert s.accept(ord("{")) == zg.ZG_OK
            stats = ctx.get_stats()
            assert stats.mask_calls >= 1
            assert stats.tokens_accepted >= 1
            assert stats.mem_used[zg.ZG_MEM_TOTAL] > 0
            assert stats.mem_used[zg.ZG_MEM_TOKENIZER] > 0
            assert stats.mem_peak[zg.ZG_MEM_TOTAL] >= stats.mem_used[zg.ZG_MEM_TOTAL]
            # compile_ns is measured (0 allowed on a very fast timer — not required)
            sstats = s.get_stats()
            assert sstats.tokens_accepted >= 1 or sstats.mask_calls >= 1
        finally:
            s.destroy()
            zg.grammar_release(g)
    finally:
        ctx.destroy()


def test_memory_returns_to_zero_after_children_destroyed(spec):
    """Memory accounting: after sessions are destroyed and grammars released,
    per-category used returns to zero; total — to the tokenizer baseline
    (DESIGN §6)."""
    ctx = zg.Context(spec, zg.ZG_MODE_ADAPTIVE)
    try:
        base = ctx.get_stats()
        base_total = base.mem_used[zg.ZG_MEM_TOTAL]
        assert base.mem_used[zg.ZG_MEM_GRAMMAR] == 0
        assert base.mem_used[zg.ZG_MEM_SESSION] == 0

        g = zg.compile_schema(ctx, SCHEMA_TZ)
        mid = ctx.get_stats()
        assert mid.mem_used[zg.ZG_MEM_GRAMMAR] > 0

        sessions = [zg.Session(ctx, g) for _ in range(3)]
        with_sessions = ctx.get_stats()
        assert with_sessions.mem_used[zg.ZG_MEM_SESSION] > 0
        for s in sessions:
            s.destroy()
        after_sessions = ctx.get_stats()
        assert after_sessions.mem_used[zg.ZG_MEM_SESSION] == 0, (
            f"session mem not freed: {after_sessions.mem_used[zg.ZG_MEM_SESSION]}")

        zg.grammar_release(g)
        final = ctx.get_stats()
        assert final.mem_used[zg.ZG_MEM_GRAMMAR] == 0
        assert final.mem_used[zg.ZG_MEM_SESSION] == 0
        assert final.mem_used[zg.ZG_MEM_TOTAL] == base_total, (
            f"mem leak: total {final.mem_used[zg.ZG_MEM_TOTAL]} != "
            f"baseline {base_total}")
        assert ctx.destroy() == zg.ZG_OK
    finally:
        ctx.destroy()


def test_full_cycle_tz_example(spec):
    """Schema from the TZ example: compile -> mask -> trace -> finish."""
    ctx = zg.Context(spec, zg.ZG_MODE_LAZY)
    try:
        g = zg.compile_schema(ctx, SCHEMA_TZ)
        s = zg.Session(ctx, g)
        try:
            doc = b'{"action":"buy","amount":42}'
            for b in doc:
                ids = s.fill_mask_ids()
                assert b in ids, f"byte {bytes([b])!r} not allowed"
                assert s.accept(b) == zg.ZG_OK
            assert s.can_end()
            assert spec.eos_ids[0] in s.fill_mask_ids()
            assert s.accept(spec.eos_ids[0]) == zg.ZG_OK
            assert s.finish() == zg.ZG_OK
            stats = s.get_stats()
            assert stats.tokens_accepted == len(doc) + 1
        finally:
            s.destroy()
            zg.grammar_release(g)
    finally:
        ctx.destroy()
