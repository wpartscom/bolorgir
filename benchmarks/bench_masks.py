#!/usr/bin/env python3
"""B2. Warm schema: p50/p95/p99/max of fill_mask and accept_token time.

>= 10k observations per load class (SPEC 10.5). A pre-generated valid token
trace is used (deterministic, seed=42): every run replays the same trace on
a fresh session.
"""

import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_common as bc


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="closed_object_action_amount")
    ap.add_argument("--corpus-dir", default=bc.CORPUS_DIR)
    ap.add_argument("--min-observations", type=int, default=10000)
    ap.add_argument("--mode", choices=["lazy", "adaptive", "precompute"],
                    default="adaptive")
    ap.add_argument("--seed", type=int, default=bc.SEED)
    args = ap.parse_args()

    zc = bc.require_core()
    entry, schema_bytes = bc.load_schema(args.schema, args.corpus_dir)
    import json as _json
    schema = _json.loads(schema_bytes)

    engine = zc.Engine(mode=args.mode, memory_limit_mb=256,
                       tokenizer=bc.make_byte_tokenizer(zc))
    constraint = engine.compile(schema)

    rng = random.Random(args.seed)
    probe = constraint.create_session()
    vocab = probe.vocab_size if hasattr(probe, "vocab_size") else None
    mask0 = probe.fill_mask()
    vocab_size = vocab or len(mask0) * (8 if isinstance(mask0, (bytes, bytearray)) else 1) * 32
    trace, completed = bc.gen_trace(probe, vocab_size, rng)
    probe.abort()
    if not completed or not trace:
        bc.emit({"status": "ERROR", "case": "B2", "schema": args.schema,
                 "reason": "failed to generate a valid trace",
                 "completed": completed, "trace_len": len(trace)})
        return

    mask_ns = []
    accept_ns = []
    observations = 0
    while observations < args.min_observations:
        session = constraint.create_session()
        for t in trace:
            t0 = bc.now_ns()
            session.fill_mask()
            t1 = bc.now_ns()
            session.accept_token(t)
            t2 = bc.now_ns()
            mask_ns.append(t1 - t0)
            accept_ns.append(t2 - t1)
        observations += len(trace)
        session.abort()

    bc.emit({
        "status": "OK", "case": "B2", "schema": args.schema, "mode": args.mode,
        "seed": args.seed, "trace_len": len(trace),
        "observations": {"mask": len(mask_ns), "accept": len(accept_ns)},
        "fill_mask_ns": bc.percentile_stats(mask_ns),
        "accept_ns": bc.percentile_stats(accept_ns),
        "trace": trace,
    })


if __name__ == "__main__":
    main()
