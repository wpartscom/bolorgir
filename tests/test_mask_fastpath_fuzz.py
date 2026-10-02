"""ADR-0007 Decision 4: property-based fast-path on/off equality.

Driven by the T4 schema generator over the T4 fuzz tokenizer: for each
random schema a random walk compares the fast path (mask_fast_path on,
max_workers in {1, 2, 8}) against the plain trie walk (kill switch) at
every step - full mask words and error outcomes (DEAD_END on one side
must match the other), cache on (adaptive) and off (lazy). Deterministic
under BLG_FUZZ_SEED / BLG_FUZZ_DIFF_FP; any divergence saves a reproducer
into tests/fuzz/artifacts/ and fails.
"""

from __future__ import annotations

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fuzz"))

import fuzz_common as fc
import schema_gen
import blg_ctypes as blg

pytest.importorskip("blg_ctypes")

SEED = int(os.environ.get("BLG_FUZZ_SEED", "20260920"))
N_SCHEMAS = int(os.environ.get("BLG_FUZZ_DIFF_FP", "150"))
MAX_STEPS = 16


def _open_pair(spec, schema_text: str, mode: int, cache: int, workers_on: int):
    """Two contexts over the same spec: fast path on vs the kill switch."""
    ctx_on = blg.Context(spec, mode=mode, cache_limit_bytes=cache,
                         mask_fast_path=blg.BLG_MASK_FAST_PATH_ON,
                         max_workers=workers_on)
    ctx_off = blg.Context(spec, mode=mode, cache_limit_bytes=cache,
                          mask_fast_path=blg.BLG_MASK_FAST_PATH_OFF)
    g_on = blg.compile_schema(ctx_on, schema_text)
    g_off = blg.compile_schema(ctx_off, schema_text)
    return (ctx_on, g_on, blg.Session(ctx_on, g_on)), \
        (ctx_off, g_off, blg.Session(ctx_off, g_off))


def _close_pair(on, off):
    for ctx, g, s in (on, off):
        s.destroy()
        blg.grammar_release(g)
        ctx.destroy()


def _walk_compare(spec, schema_text: str, rng: random.Random,
                  mode: int, cache: int, workers_on: int) -> None:
    on, off = _open_pair(spec, schema_text, mode, cache, workers_on)
    try:
        s_on, s_off = on[2], off[2]
        for step in range(MAX_STEPS):
            st_on, w_on = s_on.fill_mask_words()
            st_off, w_off = s_off.fill_mask_words()
            if (st_on, w_on) != (st_off, w_off):
                fc.save_artifact("fastpath_divergence", {
                    "seed": SEED, "schema": schema_text, "step": step,
                    "mode": mode, "workers_on": workers_on,
                    "status_on": st_on, "status_off": st_off,
                    "words_on": w_on, "words_off": w_off})
                pytest.fail(f"fast-path divergence at step {step} "
                            f"(mode={mode}, workers={workers_on}): "
                            f"status {st_on} vs {st_off}")
            if st_on != blg.BLG_OK:
                assert st_on == blg.BLG_ERR_DEAD_END
                return  # dead end on both sides, identically
            ids = sorted(fc.ids_from_words(w_on, spec.vocab_size))
            non_eos = [i for i in ids if i not in spec.eos_ids]
            if not non_eos:
                return  # only EOS remains
            tid = non_eos[rng.randrange(len(non_eos))]
            assert s_on.accept(tid) == blg.BLG_OK
            assert s_off.accept(tid) == blg.BLG_OK
    finally:
        _close_pair(on, off)


def test_fastpath_on_off_property():
    spec = fc.make_fuzz_tokenizer()
    rng = random.Random(SEED)
    compared = 0
    for case in range(N_SCHEMAS):
        schema_text = schema_gen.gen_schema(rng, must_compile=True)
        mode, cache = ((blg.BLG_MODE_ADAPTIVE, blg.BLG_CACHE_DEFAULT)
                       if case % 2 == 0 else (blg.BLG_MODE_LAZY, 0))
        workers_on = (1, 2, 8)[case % 3]
        try:
            _walk_compare(spec, schema_text, rng, mode, cache, workers_on)
        except blg.CoreFailure as e:
            fc.save_artifact("fastpath_compile_failure", {
                "seed": SEED, "case": case, "schema": schema_text,
                "status": e.status, "error": str(e)})
            raise
        compared += 1
    assert compared == N_SCHEMAS
