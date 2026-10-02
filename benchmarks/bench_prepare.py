#!/usr/bin/env python3
"""B1. Prepare: cold/warm compile, tokenizer prepare, first mask,
peak RSS (SPEC 10.4 B1).

Cold start in this script - a fresh Engine on every repeat inside one process
(lower bound); the canonical cold-start - a separate process per repeat
(run_all.py runs the script with --mode cold-process).
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_common as bc


def measure_once(zc, schema_bytes, mode, engine=None):
    """One run of the prepare path. Returns a dict with ns and RSS."""
    rss0 = bc.peak_rss_bytes()
    res = {}
    t0 = bc.now_ns()
    tok = bc.make_byte_tokenizer(zc)
    t1 = bc.now_ns()
    res["tokenizer_prepare_ns"] = t1 - t0 if engine is None else 0
    if engine is None:
        engine = zc.Engine(mode="adaptive", memory_limit_mb=256, tokenizer=tok)
    t2 = bc.now_ns()
    res["engine_create_ns"] = t2 - t1 if engine is not None else 0
    constraint = engine.compile(json.loads(schema_bytes))
    t3 = bc.now_ns()
    res["compile_ns"] = t3 - t2
    session = constraint.create_session()
    session.fill_mask()
    t4 = bc.now_ns()
    res["first_mask_ns"] = t4 - t3
    session.abort()
    res["peak_rss_bytes"] = max(bc.peak_rss_bytes(), rss0)
    return res, engine


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="closed_object_action_amount")
    ap.add_argument("--corpus-dir", default=bc.CORPUS_DIR)
    ap.add_argument("--repeats", type=int, default=30,
                    help="cold repeats (SPEC 10.5: >= 30)")
    ap.add_argument("--warm-repeats", type=int, default=100)
    ap.add_argument("--mode", choices=["inproc", "cold-process"], default="inproc")
    ap.add_argument("--seed", type=int, default=bc.SEED)
    args = ap.parse_args()

    if args.mode == "cold-process":
        # helper mode: one cold run in a fresh process
        zc = bc.require_core()
        entry, schema_bytes = bc.load_schema(args.schema, args.corpus_dir)
        res, _ = measure_once(zc, schema_bytes, "cold")
        bc.emit({"status": "OK", "case": "B1", "mode": "cold-process",
                 "schema": args.schema, **res})
        return

    zc = bc.require_core()
    entry, schema_bytes = bc.load_schema(args.schema, args.corpus_dir)

    cold = {"tokenizer_prepare_ns": [], "engine_create_ns": [], "compile_ns": [],
            "first_mask_ns": [], "peak_rss_bytes": []}
    for _ in range(args.repeats):
        res, _ = measure_once(zc, schema_bytes, "cold")
        for k, v in res.items():
            cold[k].append(v)

    engine = zc.Engine(mode="adaptive", memory_limit_mb=256,
                       tokenizer=bc.make_byte_tokenizer(zc))
    warm_compile = []
    for _ in range(args.warm_repeats):
        t0 = bc.now_ns()
        engine.compile(json.loads(schema_bytes))
        warm_compile.append(bc.now_ns() - t0)

    bc.emit({
        "status": "OK", "case": "B1", "schema": args.schema, "seed": args.seed,
        "cold_repeats": args.repeats, "warm_repeats": args.warm_repeats,
        "cold": {k: bc.percentile_stats(v) for k, v in cold.items()},
        "warm_compile_ns": bc.percentile_stats(warm_compile),
        "note": "cold = fresh Engine in the same process; the per-process cold-start "
                "is measured via --mode cold-process from run_all.py",
    })


if __name__ == "__main__":
    main()
