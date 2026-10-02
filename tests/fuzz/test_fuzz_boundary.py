"""T4: C/Python boundary - negative ABI fuzz over really existing buffers.

Rules (TZ T4): never dereference arbitrary addresses - only NULL or live
ctypes buffers; wrong sizes (struct_size, mask_words, data_len, entry_count)
are fed on really allocated structs/buffers.

Each iteration checks that the kernel returns the expected blg_status (rather
than crashing): any segfault kills the pytest process and counts as a
campaign failure.

Scale: BLG_FUZZ_BOUNDARY (default 4000 iterations), seed: BLG_FUZZ_SEED+2.
"""

from __future__ import annotations

import ctypes
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fuzz_common as fc
import schema_gen
import blg_ctypes
from blg_ctypes import (LIB, ZgCompileRequest, ZgContextConfig, ZgError, ZgStats,
                       ZgTokenEntry, ZgTokenizerDesc)

pytest.importorskip("blg_ctypes")

SEED = int(os.environ.get("BLG_FUZZ_SEED", "20260914")) + 2
N_ITERS = int(os.environ.get("BLG_FUZZ_BOUNDARY", "4000"))

OK = blg_ctypes.BLG_OK
INV_ARG = blg_ctypes.BLG_ERR_INVALID_ARGUMENT
INV_TOK = blg_ctypes.BLG_ERR_INVALID_TOKEN
UNSUP_TOK = blg_ctypes.BLG_ERR_UNSUPPORTED_TOKENIZER
UNSUP_FEAT = blg_ctypes.BLG_ERR_UNSUPPORTED_FEATURE
RES_LIMIT = blg_ctypes.BLG_ERR_RESOURCE_LIMIT
BUSY = blg_ctypes.BLG_ERR_BUSY
WRONG_STATE = blg_ctypes.BLG_ERR_WRONG_STATE
TOO_SMALL = blg_ctypes.BLG_ERR_BUFFER_TOO_SMALL

U32_MAX = 0xFFFFFFFF


def check_status(code, where):
    assert isinstance(code, int) and 0 <= code <= 13, f"{where}: status {code} out of range"


# ---------------------------------------------------------------------------
# Builders of valid objects (real buffers)
# ---------------------------------------------------------------------------

SPEC = fc.make_fuzz_tokenizer()


def valid_desc():
    return blg_ctypes.make_tokenizer_desc(SPEC)


def valid_config(mode=blg_ctypes.BLG_MODE_LAZY):
    cfg = ZgContextConfig()
    cfg.struct_size = ctypes.sizeof(ZgContextConfig)
    cfg.version = blg_ctypes.BLG_ABI_VERSION
    cfg.mode = mode
    return cfg


def create_ctx(cfg=None, desc=None):
    cfg = cfg or valid_config()
    desc = desc or valid_desc()[0]
    out = ctypes.c_void_p()
    err = blg_ctypes.new_error()
    st = LIB.blg_context_create(ctypes.byref(cfg), ctypes.byref(desc),
                               ctypes.byref(out), ctypes.byref(err))
    return st, out, err


def make_ctx_with_grammar(schema_text='{"type":"integer"}'):
    ctx = blg_ctypes.Context(SPEC, blg_ctypes.BLG_MODE_LAZY)
    gh = blg_ctypes.compile_schema(ctx, schema_text)
    return ctx, gh


def compile_raw(ctx_handle, req):
    out = ctypes.c_void_p()
    err = blg_ctypes.new_error()
    st = LIB.blg_compile(ctx_handle, ctypes.byref(req), ctypes.byref(out), ctypes.byref(err))
    return st, out, err


def valid_request(data: bytes):
    buf = (ctypes.c_uint8 * max(len(data), 1)).from_buffer_copy(data or b"\0")
    req = ZgCompileRequest()
    req.struct_size = ctypes.sizeof(ZgCompileRequest)
    req.kind = blg_ctypes.BLG_CONSTRAINT_JSON_SCHEMA
    req.profile = None
    req.data = buf
    req.data_len = len(data)
    return req, buf


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def sc_config(rng, counters):
    desc, keep = valid_desc()
    cfg = valid_config(mode=rng.randrange(3))
    choice = rng.randrange(8)
    if choice == 0:
        cfg.struct_size = rng.choice([0, 4, 8, ctypes.sizeof(ZgContextConfig) + 4, U32_MAX])
        expect = INV_ARG
    elif choice == 1:
        cfg.version = rng.choice([0, 2, 999, U32_MAX])
        expect = INV_ARG
    elif choice == 2:
        cfg.mode = rng.choice([3, 7, 100, U32_MAX])
        expect = INV_ARG
    elif choice == 3:
        cfg.memory_limit_bytes = 1 << 20
        cfg.cache_limit_bytes = 2 << 20
        expect = INV_ARG
    elif choice == 4:
        cfg.max_depth = rng.choice([65, 1000, U32_MAX])
        expect = INV_ARG
    elif choice == 5:
        # MAX_THREADS_CAP is 128 (lazy choice frames); 65/1000 are valid now.
        cfg.max_threads_per_state = rng.choice([129, 1000, U32_MAX])
        expect = INV_ARG
    elif choice == 6:
        # NULL config - allowed, defaults
        out = ctypes.c_void_p()
        err = blg_ctypes.new_error()
        st = LIB.blg_context_create(None, ctypes.byref(desc), ctypes.byref(out), ctypes.byref(err))
        check_status(st, "ctx null config")
        assert st == OK, f"null config gave {st}"
        assert LIB.blg_context_destroy(out) == OK
        counters["ctx_ok"] += 1
        return
    else:
        cfg.memory_limit_bytes = rng.randrange(1 << 16, 64 << 20)
        cfg.cache_limit_bytes = rng.randrange(0, cfg.memory_limit_bytes)
        expect = OK  # compatible limits
    st, out, err = create_ctx(cfg, desc)
    check_status(st, "ctx config mutation")
    assert st == expect, f"config mutation expect {expect} got {st}"
    if st == OK:
        assert LIB.blg_context_destroy(out) == OK
    counters["ctx"] += 1


def sc_desc(rng, counters):
    desc, keep = valid_desc()
    entries, blob_arr, eos_arr, sp_arr = keep
    choice = rng.randrange(10)
    if choice == 0:
        desc.struct_size = rng.choice([0, 4, ctypes.sizeof(ZgTokenizerDesc) + 8])
        expect = INV_ARG
    elif choice == 1:
        desc.entries = None
        expect = INV_ARG
    elif choice == 2:
        desc.blob = None
        expect = INV_ARG
    elif choice == 3:
        desc.eos_ids = None
        expect = INV_ARG
    elif choice == 4:
        desc.special_ids = None
        expect = INV_ARG
    elif choice == 5:
        entries[0].offset = desc.blob_len + rng.randrange(1, 1000)
        expect = INV_ARG
    elif choice == 6:
        entries[1].length = desc.blob_len + rng.randrange(1, 1000)
        expect = INV_ARG
    elif choice == 7:
        entries[0].id = entries[1].id  # duplicate id
        expect = UNSUP_TOK
    elif choice == 8:
        # empty representation of an ordinary token
        k = next(i for i in range(SPEC.vocab_size)
                 if i not in SPEC.eos_ids and i not in SPEC.special_ids)
        entries[k].length = 0
        expect = UNSUP_TOK
    else:
        desc.entry_count = desc.entry_count - 1  # uncovered id
        expect = UNSUP_TOK
    cfg = valid_config()
    st, out, err = create_ctx(cfg, desc)
    check_status(st, "ctx desc mutation")
    assert st == expect, f"desc mutation choice {choice}: expect {expect} got {st}"
    if st == OK:
        LIB.blg_context_destroy(out)
    counters["desc"] += 1


def sc_ctx_nulls(rng, counters):
    desc, keep = valid_desc()
    cfg = valid_config()
    err = blg_ctypes.new_error()
    out = ctypes.c_void_p()
    assert LIB.blg_context_create(ctypes.byref(cfg), None, ctypes.byref(out), ctypes.byref(err)) == INV_ARG
    assert LIB.blg_context_create(ctypes.byref(cfg), ctypes.byref(desc), None, ctypes.byref(err)) == INV_ARG
    bad_err = ZgError()
    bad_err.struct_size = rng.choice([0, 7, 100])
    st = LIB.blg_context_create(ctypes.byref(cfg), ctypes.byref(desc), ctypes.byref(out), ctypes.byref(bad_err))
    assert st == INV_ARG, f"bad err buf gave {st}"
    assert LIB.blg_context_destroy(None) == INV_ARG
    counters["ctx_nulls"] += 1


def sc_compile(rng, counters):
    ctx = blg_ctypes.Context(SPEC, blg_ctypes.BLG_MODE_LAZY)
    try:
        data = schema_gen.gen_schema(rng, must_compile=True).encode("utf-8")
        choice = rng.randrange(7)
        req, buf = valid_request(data)
        if choice == 0:
            req.struct_size = rng.choice([0, 8, ctypes.sizeof(ZgCompileRequest) + 4])
            expect = INV_ARG
        elif choice == 1:
            req.kind = rng.choice([2, 5, 100, U32_MAX])
            expect = INV_ARG
        elif choice == 2:
            req.profile = rng.choice([b"bogus", b"canonical-v2", b""])
            expect = UNSUP_FEAT
        elif choice == 3:
            req.data = None
            expect = INV_ARG
        elif choice == 4:
            # data_len beyond schema_limit on a real small buffer:
            # the kernel must reject before reading out of bounds
            req.data_len = 2 << 20
            expect = RES_LIMIT
        elif choice == 5:
            # truncated schema (real buffer, shorter length)
            req.data_len = rng.randrange(0, len(data))
            expect = None  # any regular code
        else:
            expect = OK
        st, out, err = compile_raw(ctx.handle, req)
        check_status(st, "compile mutation")
        if expect is not None:
            assert st == expect, f"compile choice {choice}: expect {expect} got {st}"
        if st == OK:
            assert out
            LIB.blg_grammar_release(out)
        else:
            assert not out, f"out_grammar set on failed compile (choice {choice})"
        # null arguments
        st, out, err2 = compile_raw(None, req)
        assert st == INV_ARG
        counters["compile"] += 1
    finally:
        ctx.destroy()


def sc_session(rng, counters):
    ctx, gh = make_ctx_with_grammar(schema_gen.gen_schema(rng, must_compile=True))
    ctx2, gh2 = make_ctx_with_grammar()
    try:
        out = ctypes.c_void_p()
        err = blg_ctypes.new_error()
        assert LIB.blg_session_create(None, gh, ctypes.byref(out), ctypes.byref(err)) == INV_ARG
        assert LIB.blg_session_create(ctx.handle, None, ctypes.byref(out), ctypes.byref(err)) == INV_ARG
        assert LIB.blg_session_create(ctx.handle, gh, None, ctypes.byref(err)) == INV_ARG
        # grammar of a foreign context
        assert LIB.blg_session_create(ctx.handle, gh2, ctypes.byref(out), ctypes.byref(err)) == INV_ARG
        assert not out
        counters["session"] += 1
    finally:
        blg_ctypes.grammar_release(gh2)
        ctx2.destroy()
        blg_ctypes.grammar_release(gh)
        ctx.destroy()


def sc_mask_accept(rng, counters):
    ctx, gh = make_ctx_with_grammar(schema_gen.gen_schema(rng, must_compile=True))
    s = blg_ctypes.Session(ctx, gh)
    words = SPEC.mask_words
    try:
        err = blg_ctypes.new_error()
        buf = (ctypes.c_uint32 * (words + 1))()
        assert LIB.blg_fill_mask(None, buf, words, ctypes.byref(err)) == INV_ARG
        assert LIB.blg_fill_mask(s.handle, None, words, ctypes.byref(err)) == INV_ARG
        # small but real buffer
        assert LIB.blg_fill_mask(s.handle, buf, rng.randrange(0, words), ctypes.byref(err)) == TOO_SMALL
        # misaligned pointer inside a real buffer
        mis = ctypes.cast(ctypes.byref(buf, 1), ctypes.POINTER(ctypes.c_uint32))
        assert LIB.blg_fill_mask(s.handle, mis, words, ctypes.byref(err)) == INV_ARG
        # accept: random ids, incl. outside vocab
        for _ in range(rng.randrange(1, 6)):
            tok = rng.choice([rng.randrange(SPEC.vocab_size + 100), U32_MAX,
                              SPEC.special_ids[0], SPEC.vocab_size])
            st = LIB.blg_accept_token(s.handle, tok, ctypes.byref(err))
            check_status(st, "accept random")
            if tok >= SPEC.vocab_size or tok in SPEC.special_ids:
                assert st == INV_TOK, f"accept({tok}) gave {st}"
        # can_end/finish/abort nulls
        ce = ctypes.c_bool()
        assert LIB.blg_can_end(None, ctypes.byref(ce)) == INV_ARG
        assert LIB.blg_can_end(s.handle, None) == INV_ARG
        assert LIB.blg_finish(None, ctypes.byref(err)) == INV_ARG
        assert LIB.blg_abort(None) == INV_ARG
        counters["mask_accept"] += 1
    finally:
        s.destroy()
        blg_ctypes.grammar_release(gh)
        ctx.destroy()


def sc_batch(rng, counters):
    ctx, gh = make_ctx_with_grammar(schema_gen.gen_schema(rng, must_compile=True))
    sessions = [blg_ctypes.Session(ctx, gh) for _ in range(3)]
    words = SPEC.mask_words
    try:
        err = blg_ctypes.new_error()
        # empty batch
        assert LIB.blg_fill_masks_batch(None, None, 0, None, 0, ctypes.byref(err)) == OK
        # null pointers with count>0
        assert LIB.blg_fill_masks_batch(None, None, words, None, 2, ctypes.byref(err)) == INV_ARG
        handles = (ctypes.c_void_p * 3)(*(s.handle.value for s in sessions))
        bufs = [(ctypes.c_uint32 * words)() for _ in sessions]
        ptrs = (ctypes.POINTER(ctypes.c_uint32) * 3)(*bufs)
        sts = (ctypes.c_int32 * 3)()
        # normal batch
        assert LIB.blg_fill_masks_batch(handles, ptrs, words, sts, 3, ctypes.byref(err)) == OK
        # small words_each on real buffers
        st = LIB.blg_fill_masks_batch(handles, ptrs, words - 1, sts, 3, ctypes.byref(err))
        assert st == TOO_SMALL and all(x == TOO_SMALL for x in sts)
        # null session in the middle
        handles[1] = None
        st = LIB.blg_fill_masks_batch(handles, ptrs, words, sts, 3, ctypes.byref(err))
        assert st == INV_ARG and sts[1] == INV_ARG and sts[0] == OK and sts[2] == OK
        counters["batch"] += 1
    finally:
        for s in sessions:
            s.destroy()
        blg_ctypes.grammar_release(gh)
        ctx.destroy()


def sc_stats(rng, counters):
    ctx, gh = make_ctx_with_grammar()
    try:
        st_obj = ZgStats()
        st_obj.struct_size = rng.choice([1, 4, ctypes.sizeof(ZgStats) + 8])
        assert LIB.blg_get_stats(ctx.handle, ctypes.byref(st_obj)) == INV_ARG
        st2 = ZgStats()  # struct_size=0 is allowed: the kernel fills it in
        assert LIB.blg_get_stats(ctx.handle, ctypes.byref(st2)) == OK
        assert st2.struct_size == ctypes.sizeof(ZgStats)
        assert LIB.blg_get_stats(None, ctypes.byref(st2)) == INV_ARG
        assert LIB.blg_get_stats(ctx.handle, None) == INV_ARG
        counters["stats"] += 1
    finally:
        blg_ctypes.grammar_release(gh)
        ctx.destroy()


def sc_busy(rng, counters):
    ctx, gh = make_ctx_with_grammar(schema_gen.gen_schema(rng, must_compile=True))
    s = blg_ctypes.Session(ctx, gh)
    assert LIB.blg_context_destroy(ctx.handle) == BUSY
    s.destroy()
    assert LIB.blg_context_destroy(ctx.handle) == BUSY
    blg_ctypes.grammar_release(gh)
    assert ctx.destroy() == OK
    counters["busy"] += 1


def sc_random_sequence(rng, counters):
    """Random but well-formed call sequence over live objects."""
    ctx, gh = make_ctx_with_grammar(schema_gen.gen_schema(rng, must_compile=True))
    live: list[blg_ctypes.Session] = []
    err = blg_ctypes.new_error()
    try:
        for _ in range(rng.randrange(10, 30)):
            op = rng.randrange(8)
            if op == 0 or not live:
                if len(live) < 5:
                    live.append(blg_ctypes.Session(ctx, gh))
                continue
            s = rng.choice(live)
            if op == 1:
                st, _w = s.fill_mask_words()
                check_status(st, "seq fill")
            elif op == 2:
                st = s.accept(rng.randrange(SPEC.vocab_size + 50))
                check_status(st, "seq accept")
            elif op == 3:
                st = s.finish()
                check_status(st, "seq finish")
            elif op == 4:
                assert s.abort() == OK
            elif op == 5:
                try:
                    s.can_end()
                except blg_ctypes.CoreFailure as e:
                    check_status(e.status, "seq can_end")
            elif op == 6:
                s.destroy()
                live.remove(s)
            else:
                stats = ctx.get_stats()
                assert stats.mem_used[blg_ctypes.BLG_MEM_SESSION] > 0 or not live
            counters["seq_ops"] += 1
        counters["sequence"] += 1
    finally:
        for s in live:
            s.destroy()
        blg_ctypes.grammar_release(gh)
        assert ctx.destroy() == OK


SCENARIOS = [
    (sc_config, 12), (sc_desc, 10), (sc_ctx_nulls, 6), (sc_compile, 14),
    (sc_session, 8), (sc_mask_accept, 14), (sc_batch, 8), (sc_stats, 6),
    (sc_busy, 6), (sc_random_sequence, 16),
]


def test_boundary_fuzz():
    rng = random.Random(SEED)
    counters = {name: 0 for name in
                ["ctx", "ctx_ok", "desc", "ctx_nulls", "compile", "session",
                 "mask_accept", "batch", "stats", "busy", "sequence", "seq_ops"]}
    fns, weights = zip(*SCENARIOS)
    for _ in range(N_ITERS):
        scenario = rng.choices(fns, weights=weights, k=1)[0]
        scenario(rng, counters)
    counters["seed"] = SEED
    counters["iters"] = N_ITERS
    fc.save_results("py_fuzz_boundary.json", counters)
    print(f"\n[T4 boundary] {counters}")
