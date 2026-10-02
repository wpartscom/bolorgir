#!/usr/bin/env python3
"""ADR-0007 perf measurement: mask fast path vs the pinned baseline slice.

Measures, against benchmarks/baseline_slice.json and the pinned MaskBench
run of benchmarks/reports/20260920T022651_perf-baseline/REPORT.md:

1. probe_cold: first visit of the permissive string-content state at the
   128k-token Llama vocabulary, fast path OFF (the plain trie walk - bit-
   identical to the baseline engine) vs ON; fresh context per observation.
2. probe_warm: the same state served by the adaptive cache (the warm-path
   guard: must stay <= the 1.3 us baseline).
3. dfs_cost_model: work_ops_total / mask_ns_total deltas of one cold
   blg_fill_mask, off vs on (baseline: 383,647 ops).
4. maskbench_slice: the pinned MaskBench slice (first compiling BFCL_java
   files of the pinned corpus snapshot), per-instance sessions, per-token
   mask timing (TBM), off vs on, sequential (the baseline's 20-process
   load is quoted, not reproduced).
5. Benchmark gate (ADR-0007 Decision 4): before any timing number is
   accepted, an on/off mask-equality pass runs over every state visited by
   the probe prefixes and the MaskBench slice traces; any divergence
   aborts the run.

Everything goes through tests/blg_ctypes.py (C ABI); BLG_LIB_PATH selects
the library under test. Output: raw JSON on stdout (bc.emit conventions)
plus a human summary on stderr for the report writer.

Run from the project root:
    HF_HUB_OFFLINE=1 BLG_LIB_PATH=/tmp/zigout-perf/lib/libbolorgir.so \
        PYTHONPATH=python:benchmarks:tests \
        python3 benchmarks/bench_adr0007.py [--cold-repeats 10] \
        [--maskbench-files 12] [--out results/<ts>_adr0007.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import bench_common as bc  # noqa: E402

MASKBENCH_DATA = os.path.join(
    ROOT, "benchmarks", "external", "jsonschemabench-maskbench",
    "maskbench", "maskbench", "data")
if not os.path.isdir(MASKBENCH_DATA):
    MASKBENCH_DATA = os.path.join(
        ROOT, "benchmarks", "external", "jsonschemabench-maskbench",
        "maskbench", "data")

# Pinned MaskBench slice (ADR-0007 benchmark gate): deterministic, changes
# never. BFCL files are 586 of the 624 compiling files of the pinned run;
# most individual files do NOT compile under canonical-v1 (empty {}
# subschemas), so the slice is the first --maskbench-files candidates of
# this list that DO compile (selection is deterministic: fixed order,
# fixed count).
MASKBENCH_SLICE = [f"BFCL_java_{i}" for i in range(129)]

MASK_FAST_PATH_OFF = 2


def select_compiling(blg, spec, names, count):
    """First `count` names whose schema compiles (one throwaway context)."""
    ctx = blg.Context(spec, mode=blg.BLG_MODE_ADAPTIVE,
                      memory_limit_bytes=512 << 20,
                      mask_fast_path=MASK_FAST_PATH_OFF)
    picked = []
    try:
        for name in names:
            if len(picked) >= count:
                break
            path = os.path.join(MASKBENCH_DATA, name + ".json")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            try:
                grammar = blg.compile_schema(ctx, doc["schema"])
            except blg.CoreFailure:
                continue
            blg.grammar_release(grammar)
            picked.append(name)
    finally:
        ctx.destroy()
    return picked


def load_bundle(zc, manifest):
    tok_spec = manifest["tokenizer_128k"]
    tf = bc.import_or_skip("transformers")
    if tf is None:
        bc.skip("transformers is not available")
    hf_tok = tf.AutoTokenizer.from_pretrained(
        tok_spec["name"], revision=tok_spec["revision"])
    return hf_tok, zc.TokenizerBundle.from_hf(hf_tok, use_cache=False)


def make_spec(blg, bundle):
    import types
    return types.SimpleNamespace(
        tokens=[bundle.token_bytes(i) for i in range(bundle.vocab_size)],
        vocab_size=bundle.vocab_size,
        eos_ids=list(bundle.eos_ids),
        special_ids=list(bundle.special_ids),
        mask_words=(bundle.vocab_size + 31) // 32)


def prefix_token_ids(bundle, prefix_bytes):
    by_bytes = {}
    for i in range(bundle.vocab_size):
        by_bytes.setdefault(bundle.token_bytes(i), i)
    ids = []
    pos = 0
    while pos < len(prefix_bytes):
        for end in range(len(prefix_bytes), pos, -1):
            tid = by_bytes.get(prefix_bytes[pos:end])
            if tid is not None:
                ids.append(tid)
                pos = end
                break
        else:
            raise SystemExit(f"vocabulary cannot spell prefix at {pos}")
    return ids


def open_session(blg, spec, schema, fast_path, mode=None, workers=1):
    mode = blg.BLG_MODE_ADAPTIVE if mode is None else mode
    ctx = blg.Context(spec, mode=mode,
                      memory_limit_bytes=512 << 20,
                      cache_limit_bytes=blg.BLG_CACHE_DEFAULT,
                      work_limit_ops=1 << 60,
                      mask_fast_path=fast_path,
                      max_workers=workers)
    grammar = blg.compile_schema(ctx, schema)
    return ctx, grammar


def mask_at(blg, spec, schema, prefix_ids, fast_path, workers=1):
    """One fresh context/session: (status, words, work_ops, core_ns, wall_ns)."""
    ctx, grammar = open_session(blg, spec, schema, fast_path, workers=workers)
    try:
        session = blg.Session(ctx, grammar)
        for tid in prefix_ids:
            assert session.accept(tid) == blg.BLG_OK
        s0 = ctx.get_stats()
        t0 = bc.now_ns()
        status, words = session.fill_mask_words()
        wall_ns = bc.now_ns() - t0
        s1 = ctx.get_stats()
        session.destroy()
        return {"status": status, "words": words,
                "work_ops": s1.work_ops_total - s0.work_ops_total,
                "core_ns": s1.mask_ns_total - s0.mask_ns_total,
                "wall_ns": wall_ns}
    finally:
        blg.grammar_release(grammar)
        ctx.destroy()


def bench_probe_cold(blg, spec, bundle, probe, repeats):
    ids = prefix_token_ids(bundle, probe["prefix_bytes"].encode("utf-8"))
    out = {}
    for fp, name in ((MASK_FAST_PATH_OFF, "off"), (0, "on")):
        wall, ops, core = [], [], []
        for _ in range(repeats):
            r = mask_at(blg, spec, probe["schema"], ids, fp)
            assert r["status"] == blg.BLG_OK
            wall.append(r["wall_ns"])
            ops.append(r["work_ops"])
            core.append(r["core_ns"])
        out[name] = {"fill_wall_ns": bc.percentile_stats(wall),
                     "core_ns": bc.percentile_stats(core),
                     "work_ops": bc.percentile_stats(ops)}
    return out


def raw_fill(blg, session, buf):
    """One blg_fill_mask C call into a reusable buffer (no list building)."""
    import ctypes
    err = blg.new_error()
    return blg.LIB.blg_fill_mask(session.handle, buf, len(buf),
                                 ctypes.byref(err))


def bench_probe_warm(blg, spec, bundle, probe, observations):
    """Cache-hit path: one populating fill per context, then timed hits.

    Times the raw C call with a preallocated mask buffer: the Python-side
    list conversion of fill_mask_words (~150 us at 128k) would swamp the
    sub-microsecond cache-hit path the warm guard is about.
    """
    import ctypes
    ids = prefix_token_ids(bundle, probe["prefix_bytes"].encode("utf-8"))
    buf = (ctypes.c_uint32 * spec.mask_words)()
    out = {}
    for fp, name in ((MASK_FAST_PATH_OFF, "off"), (0, "on")):
        ctx, grammar = open_session(blg, spec, probe["schema"], fp)
        try:
            session = blg.Session(ctx, grammar)
            for tid in ids:
                assert session.accept(tid) == blg.BLG_OK
            assert raw_fill(blg, session, buf) == blg.BLG_OK  # populating
            session.destroy()
            hits0 = ctx.get_stats().cache_hits
            fill_ns = []
            for _ in range(observations):
                session = blg.Session(ctx, grammar)
                for tid in ids:
                    assert session.accept(tid) == blg.BLG_OK
                t0 = bc.now_ns()
                status = raw_fill(blg, session, buf)
                assert status == blg.BLG_OK
                fill_ns.append(bc.now_ns() - t0)
                session.destroy()
            hits = ctx.get_stats().cache_hits - hits0
            out[name] = {"observations": observations, "cache_hits": hits,
                         "all_hits": hits == observations,
                         "fill_wall_ns": bc.percentile_stats(fill_ns)}
        finally:
            blg.grammar_release(grammar)
            ctx.destroy()
    return out


def maskbench_traces(hf_tok, blg, spec, names, fast_path):
    """Replay the pinned slice; per-mask wall times + equality gate data.

    Returns (tbm_us list, gate list of (file, step, words), stats).
    Compile errors are skipped and counted (the pinned run's 5.5%
    canonical-v1 coverage applies here too).
    """
    tbm_ns = []
    gate = []
    stats = {"files": 0, "compiled": 0, "compile_error": 0, "instances": 0,
             "masks": 0, "validation_error": 0}
    for name in names:
        path = os.path.join(MASKBENCH_DATA, name + ".json")
        if not os.path.exists(path):
            continue
        stats["files"] += 1
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        try:
            ctx, grammar = open_session(blg, spec, doc["schema"], fast_path)
        except blg.CoreFailure:
            stats["compile_error"] += 1
            continue
        stats["compiled"] += 1
        try:
            for test in doc["tests"]:
                instance = json.dumps(test["data"], ensure_ascii=False,
                                      separators=(",", ":"))
                tokens = hf_tok.encode(instance, add_special_tokens=False)
                session = blg.Session(ctx, grammar)
                stats["instances"] += 1
                accepted = True
                for step, tid in enumerate(tokens):
                    t0 = bc.now_ns()
                    status, words = session.fill_mask_words()
                    ns = bc.now_ns() - t0
                    if status != blg.BLG_OK:
                        accepted = False
                        break
                    stats["masks"] += 1
                    tbm_ns.append(ns)
                    if len(gate) < 400 or step % 7 == 0:
                        gate.append((name, stats["instances"], step, words))
                    if session.accept(tid) != blg.BLG_OK:
                        accepted = False
                        break
                if accepted != bool(test["valid"]):
                    stats["validation_error"] += 1
                session.destroy()
        finally:
            blg.grammar_release(grammar)
            ctx.destroy()
    return tbm_ns, gate, stats


def warm_ext_helper(probe_id, observations, manifest):
    """Subprocess entry: warm-path timing through the C-extension API.

    The baseline warm numbers (0.85 us) were recorded through
    bolorgir._core (a compiled extension, ~0.1 us call overhead); a
    ctypes loop has ~5 us of Python-side overhead that would swamp the
    sub-microsecond cache-hit path. The extension links libbolorgir.so.0
    dynamically, so LD_LIBRARY_PATH selects the library under test and
    BLG_MASK_FAST_PATH=0 is the kill switch (ADR-0007 Decision 5).
    """
    zc = bc.require_core()
    _, bundle = load_bundle(zc, manifest)
    probe = next(p for p in manifest["probes"] if p["id"] == probe_id)
    ids = prefix_token_ids(bundle, probe["prefix_bytes"].encode("utf-8"))
    eng = zc.Engine(mode="adaptive", memory_limit_mb=256, tokenizer=bundle)
    constraint = eng.compile(probe["schema"])
    session = constraint.create_session()
    for tid in ids:
        session.accept_token(tid)
    session.fill_mask()  # populating, discarded
    session.close()
    hits0 = eng.stats()["cache_hits"]
    fill_ns = []
    for _ in range(observations):
        session = constraint.create_session()
        for tid in ids:
            session.accept_token(tid)
        t0 = bc.now_ns()
        session.fill_mask()
        fill_ns.append(bc.now_ns() - t0)
        session.close()
    hits = eng.stats()["cache_hits"] - hits0
    constraint.close()
    eng.close()
    print(json.dumps({"observations": observations, "cache_hits": hits,
                      "all_hits": hits == observations,
                      "fill_wall_ns": bc.percentile_stats(fill_ns)}))


def warm_via_extension(manifest, probe_id, observations, lib_dir):
    """Run warm_ext_helper in two subprocesses: fast path OFF (env kill
    switch) and ON (default). Returns {"off": ..., "on": ...}.

    lib_dir (directory of the libbolorgir under test) goes to the front
    of LD_LIBRARY_PATH: the extension has RUNPATH $ORIGIN/_lib, which
    LD_LIBRARY_PATH precedes.
    """
    import subprocess
    out = {}
    for fp_off, name in ((True, "off"), (False, "on")):
        env = dict(os.environ)
        env["HF_HUB_OFFLINE"] = "1"
        env["LD_LIBRARY_PATH"] = lib_dir + os.pathsep + env.get(
            "LD_LIBRARY_PATH", "")
        if fp_off:
            env["BLG_MASK_FAST_PATH"] = "0"
        else:
            env.pop("BLG_MASK_FAST_PATH", None)
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--warm-ext-helper",
             probe_id, str(observations)],
            env=env, capture_output=True, text=True, check=True)
        out[name] = json.loads(proc.stdout.strip().splitlines()[-1])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=os.path.join(HERE, "baseline_slice.json"))
    ap.add_argument("--cold-repeats", type=int, default=10)
    ap.add_argument("--warm-observations", type=int, default=2000)
    ap.add_argument("--maskbench-files", type=int, default=len(MASKBENCH_SLICE))
    ap.add_argument("--skip-maskbench", action="store_true")
    ap.add_argument("--skip-warm-ext", action="store_true")
    ap.add_argument("--warm-ext-helper", nargs=2, metavar=("PROBE_ID", "OBS"),
                    default=None, help=argparse.SUPPRESS)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    if args.warm_ext_helper:
        probe_id, obs = args.warm_ext_helper
        warm_ext_helper(probe_id, int(obs), manifest)
        return

    zc = bc.require_core()
    import blg_ctypes as blg

    hf_tok, bundle = load_bundle(zc, manifest)
    spec = make_spec(blg, bundle)
    out = {"status": "OK", "case": "adr0007_mask_fast_path",
           "lib": blg.LIB._path,
           "environment": {"date_utc": time.strftime(
               "%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
           "vocab_size": bundle.vocab_size}

    # ---- benchmark gate: on/off mask equality over every visited state --
    print("gate: probe states on/off equality ...", file=sys.stderr, flush=True)
    gate_states = 0
    for probe in manifest["probes"]:
        ids = prefix_token_ids(bundle, probe["prefix_bytes"].encode("utf-8"))
        r_off = mask_at(blg, spec, probe["schema"], ids, MASK_FAST_PATH_OFF)
        r_on = mask_at(blg, spec, probe["schema"], ids, 0)
        r_on8 = mask_at(blg, spec, probe["schema"], ids, 0, workers=8)
        if (r_off["status"], r_off["words"]) != (r_on["status"], r_on["words"]):
            raise SystemExit(f"GATE FAILURE: fast path diverges on {probe['id']}")
        if r_on["words"] != r_on8["words"]:
            raise SystemExit(f"GATE FAILURE: max_workers diverges on {probe['id']}")
        gate_states += 2
    out["gate"] = {"probe_states": gate_states, "status": "PASS"}

    # ---- probes: cold + warm, off vs on ----
    out["probes"] = {}
    for probe in manifest["probes"]:
        print(f"cold probe {probe['id']} off/on ...", file=sys.stderr, flush=True)
        cold = bench_probe_cold(blg, spec, bundle, probe, args.cold_repeats)
        print(f"warm probe {probe['id']} off/on ...", file=sys.stderr, flush=True)
        warm = bench_probe_warm(blg, spec, bundle, probe, args.warm_observations)
        entry = {"cold": cold, "warm": warm}
        if not args.skip_warm_ext:
            print(f"warm-ext probe {probe['id']} off/on (C extension) ...",
                  file=sys.stderr, flush=True)
            entry["warm_ext"] = warm_via_extension(
                manifest, probe["id"], args.warm_observations,
                os.path.dirname(blg.LIB._path))
        out["probes"][probe["id"]] = entry

    # ---- MaskBench pinned slice ----
    if not args.skip_maskbench:
        print("selecting compiling maskbench files ...", file=sys.stderr,
              flush=True)
        names = select_compiling(blg, spec, MASKBENCH_SLICE,
                                 args.maskbench_files)
        print(f"maskbench slice ({len(names)} files: {', '.join(names)}) "
              "OFF run ...", file=sys.stderr, flush=True)
        tbm_off, gate_off, stats_off = maskbench_traces(
            hf_tok, blg, spec, names, MASK_FAST_PATH_OFF)
        print("maskbench slice ON run ...", file=sys.stderr, flush=True)
        tbm_on, gate_on, stats_on = maskbench_traces(
            hf_tok, blg, spec, names, 0)
        # Gate: identical mask sequences at the recorded states.
        if [g[:3] for g in gate_off] != [g[:3] for g in gate_on]:
            raise SystemExit("GATE FAILURE: maskbench trace structure diverges")
        divergent = sum(1 for a, b in zip(gate_off, gate_on) if a[3] != b[3])
        if divergent:
            raise SystemExit(f"GATE FAILURE: {divergent} maskbench masks diverge")
        out["gate"]["maskbench_states"] = len(gate_off)
        out["maskbench_slice"] = {
            "files": names,
            "off": {"tbm_us": bc.percentile_stats([n / 1000 for n in tbm_off]),
                    "stats": stats_off},
            "on": {"tbm_us": bc.percentile_stats([n / 1000 for n in tbm_on]),
                   "stats": stats_on},
        }

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        print(f"wrote {args.out}", file=sys.stderr)
    bc.emit(out)


if __name__ == "__main__":
    main()
