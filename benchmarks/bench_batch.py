#!/usr/bin/env python3
"""B4. Batch: 1/8/32/128 independent sessions in one call (SPEC 10.4 B4).

Metrics: whole-batch time and throughput (masks/s). If the package does not
provide a batch call, a sequential loop is used with the
sequential_fallback=true marker.
"""

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_common as bc


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="closed_object_action_amount")
    ap.add_argument("--corpus-dir", default=bc.CORPUS_DIR)
    ap.add_argument("--batches", default="1,8,32,128")
    ap.add_argument("--steps", type=int, default=64,
                    help="number of batch-mask steps per measurement")
    ap.add_argument("--repeats", type=int, default=30)
    ap.add_argument("--mode", choices=["lazy", "adaptive", "precompute"],
                    default="adaptive")
    ap.add_argument("--seed", type=int, default=bc.SEED)
    args = ap.parse_args()

    zc = bc.require_core()
    entry, schema_bytes = bc.load_schema(args.schema, args.corpus_dir)
    schema = json.loads(schema_bytes)

    engine = zc.Engine(mode=args.mode, memory_limit_mb=256,
                       tokenizer=bc.make_byte_tokenizer(zc))
    constraint = engine.compile(schema)
    batch_fill = getattr(zc, "fill_masks_batch", None)
    sequential_fallback = batch_fill is None

    results = {}
    for b in [int(x) for x in args.batches.split(",")]:
        sessions = [constraint.create_session() for _ in range(b)]
        times = []
        rng = random.Random(args.seed + b)
        for _ in range(args.repeats):
            t0 = bc.now_ns()
            for _ in range(args.steps):
                if sequential_fallback:
                    for s in sessions:
                        mask = s.fill_mask()
                        allowed = bc.mask_bits(mask, 10**9)
                        if allowed:
                            s.accept_token(allowed[rng.randrange(len(allowed))])
                else:
                    batch_fill(sessions)
            times.append(bc.now_ns() - t0)
            for s in sessions:
                s.abort()
            sessions = [constraint.create_session() for _ in range(b)]
        for s in sessions:
            s.abort()
        st = bc.percentile_stats(times)
        st["masks_per_second"] = (b * args.steps) / (st["mean"] / 1e9) if st.get("mean") else None
        results[str(b)] = st

    bc.emit({
        "status": "OK", "case": "B4", "schema": args.schema, "mode": args.mode,
        "seed": args.seed, "steps_per_measurement": args.steps,
        "repeats": args.repeats, "sequential_fallback": sequential_fallback,
        "batch_time_ns": results,
    })


if __name__ == "__main__":
    main()
