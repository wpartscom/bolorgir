"""T4 (cache campaign, T2): bitwise mask parity of lazy / adaptive /
adaptive with a tiny cache / precompute on identical traces, including a
multithreaded run (4 threads, shared adaptive context) vs single-threaded.

Scale: ZG_FUZZ_CACHE (default 120 schemas), seed: ZG_FUZZ_SEED+1.
"""

from __future__ import annotations

import os
import random
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fuzz_common as fc
import schema_gen
import zg_ctypes

pytest.importorskip("zg_ctypes")

SEED = int(os.environ.get("ZG_FUZZ_SEED", "20260914")) + 1
N_SCHEMAS = int(os.environ.get("ZG_FUZZ_CACHE", "120"))
MAX_STEPS = 16


def make_contexts(spec):
    """lazy, adaptive (64MiB), adaptive tiny cache (evictions), precompute."""
    ctxs = [
        zg_ctypes.Context(spec, zg_ctypes.ZG_MODE_LAZY),
        zg_ctypes.Context(spec, zg_ctypes.ZG_MODE_ADAPTIVE),
        zg_ctypes.Context(spec, zg_ctypes.ZG_MODE_ADAPTIVE,
                          memory_limit_bytes=64 << 20, cache_limit_bytes=500_000),
        zg_ctypes.Context(spec, zg_ctypes.ZG_MODE_PRECOMPUTE),
    ]
    return ctxs


def run_trace(ctxs, ghs, spec, seed, counters):
    """One trace in lockstep across all contexts. -> list[words] of all steps."""
    rng = random.Random(seed)
    sessions = [zg_ctypes.Session(ctx, gh) for ctx, gh in zip(ctxs, ghs)]
    trace = []
    try:
        for _step in range(MAX_STEPS):
            results = [s.fill_mask_words() for s in sessions]
            statuses = [st for st, _ in results]
            if len(set(statuses)) != 1:
                path = fc.save_artifact("cache_status_mismatch", {
                    "seed": seed, "statuses": statuses, "trace_len": len(trace)})
                pytest.fail(f"cache parity: statuses {statuses}; repro {path}")
            if statuses[0] != zg_ctypes.ZG_OK:
                break  # DEAD_END/RESOURCE_LIMIT — identical in all contexts
            words0 = results[0][1]
            for i, (_, w) in enumerate(results[1:], 1):
                counters["mask_cmps"] += 1
                if w != words0:
                    path = fc.save_artifact("cache_mask_mismatch", {
                        "seed": seed, "ctx": i, "trace_len": len(trace)})
                    pytest.fail(f"cache parity: ctx {i} mask differs; repro {path}")
            trace.append(words0)
            eos = spec.eos_ids[0]
            allowed = [i for i in range(spec.vocab_size)
                       if words0[i // 32] >> (i % 32) & 1 and i != eos]
            eos_allowed = bool(words0[eos // 32] >> (eos % 32) & 1)
            if not allowed and not eos_allowed:
                break
            finish_now = eos_allowed and (not allowed or rng.random() < 0.12)
            tok = eos if finish_now else allowed[rng.randrange(len(allowed))]
            for i, s in enumerate(sessions):
                st = s.accept(tok)
                if st != zg_ctypes.ZG_OK:
                    path = fc.save_artifact("cache_accept", {
                        "seed": seed, "ctx": i, "token": tok, "status": st})
                    pytest.fail(f"cache trace: allowed token {tok} rejected in ctx {i}; repro {path}")
                counters["accepts"] += 1
            if finish_now:
                for s in sessions:
                    assert s.finish() == zg_ctypes.ZG_OK
                break
    finally:
        for s in sessions:
            s.destroy()
    return trace


def test_cache_parity_fuzz():
    spec = fc.make_fuzz_tokenizer()
    rng = random.Random(SEED)
    counters = {"schemas": 0, "traces": 0, "mask_cmps": 0, "accepts": 0,
                "threaded_traces": 0, "threaded_mismatches": 0}
    ctxs = make_contexts(spec)
    jobs = []
    try:
        for i in range(N_SCHEMAS):
            seed = rng.getrandbits(48)
            srng = random.Random(seed)
            schema_text = schema_gen.gen_schema(srng, must_compile=True)
            counters["schemas"] += 1
            ghs = []
            for ctx in ctxs:
                try:
                    ghs.append(zg_ctypes.compile_schema(ctx, schema_text))
                except zg_ctypes.CoreFailure as e:
                    path = fc.save_artifact("cache_compile_rejected", {
                        "seed": seed, "schema": schema_text, "status": e.status})
                    pytest.fail(f"must_compile schema rejected: {e.status}; repro {path}")
            jobs.append((seed, schema_text, ghs))

        # phase 1: sequential, reference traces
        expected = []
        for seed, _schema, ghs in jobs:
            expected.append(run_trace(ctxs, ghs, spec, seed, counters))
            counters["traces"] += 1

        # phase 2: 4 threads on the same contexts, new sessions — same traces
        results = [None] * len(jobs)
        errors = []

        def worker(indices):
            try:
                for j in indices:
                    results[j] = run_trace(ctxs, jobs[j][2], spec, jobs[j][0], counters)
                    counters["threaded_traces"] += 1
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(list(range(t, len(jobs), 4)),))
                   for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]
        for j, (exp, got) in enumerate(zip(expected, results)):
            if exp != got:
                counters["threaded_mismatches"] += 1
                path = fc.save_artifact("cache_threaded_mismatch", {
                    "seed": jobs[j][0], "schema": jobs[j][1]})
                pytest.fail(f"threaded trace {j} differs from sequential; repro {path}")
    finally:
        for _seed, _schema, ghs in jobs:
            for gh in ghs:
                zg_ctypes.grammar_release(gh)
        for ctx in ctxs:
            assert ctx.destroy() == zg_ctypes.ZG_OK
    counters["seed"] = SEED
    fc.save_results("py_fuzz_cache.json", counters)
    print(f"\n[T4 cache parity] {counters}")
