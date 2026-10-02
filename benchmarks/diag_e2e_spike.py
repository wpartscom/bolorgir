#!/usr/bin/env python3
"""e2e spike diagnostics: where exactly the first
request of a new constraint gets more expensive. Runs SEPARATELY from
acceptance measurements; the result is raw JSON observations in
benchmarks/results/.

    PYTHONPATH=python:benchmarks HF_HUB_OFFLINE=1 \
        python3 benchmarks/diag_e2e_spike.py --schema s2_nested_arrays \
        --batches 1 8 --reps 3 --warmup-schema s1_flat_enum

Instrumented: per-step timeline (StepStreamer), processor
(fill_mask / unpack+H2D / apply), per-step forward time, GC pauses, CUDA
allocations. On a spike the repeat is re-run under cProfile and the top is
printed.
"""

from __future__ import annotations

import argparse
import cProfile
import gc
import io
import json
import pstats
import time

import torch

import bolorgir as zc
from bolorgir import Engine
from bolorgir import transformers as zt

from bench_e2e import SCHEMAS, build_inputs
from hf_common import StepStreamer, close_result, constrained_generate_safe, load_model


def run_rep(model, tokenizer, constraint, inputs, prompt_len, capture_steps=False):
    torch.manual_seed(42)
    streamer = StepStreamer()
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    t0 = time.perf_counter_ns()
    res = constrained_generate_safe(
        model,
        tokenizer,
        constraint,
        inputs=inputs,
        max_new_tokens=512,
        do_sample=False,
        streamer=streamer,
    )
    torch.cuda.synchronize()
    t1 = time.perf_counter_ns()
    out = {
        "total_ms": (t1 - t0) / 1e6,
        "ttft_ms": ((streamer.ts[0] - t0) / 1e6) if streamer.ts else None,
        "seq_len": int(res.sequences.shape[1]),
        "peak_vram_mb": torch.cuda.max_memory_allocated() / 2**20,
    }
    if capture_steps and streamer.ts:
        out["steps_ms"] = [round((b - a) / 1e6, 2) for a, b in zip(streamer.ts, streamer.ts[1:])]
    close_result(res)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default="s2_nested_arrays")
    ap.add_argument("--warmup-schema", default="s1_flat_enum")
    ap.add_argument("--batches", type=int, nargs="*", default=[1, 8])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    model, tokenizer = load_model()
    engine = Engine(mode="adaptive", tokenizer=tokenizer)
    constraints = {
        name: engine.compile(SCHEMAS[name]["schema"]) for name in (args.warmup_schema, args.schema)
    }

    # The processor accumulates total phase times and call counts per rep here.
    phases = {"fill_ms": 0.0, "unpack_ms": 0.0, "calls": 0, "fill_calls": 0}
    orig_proc = zt.ConstraintLogitsProcessor.__call__

    def proc_timed(self, ids, scores):
        t = time.perf_counter()
        out = orig_proc(self, ids, scores)
        phases["unpack_ms"] += (time.perf_counter() - t) * 1e3
        phases["calls"] += 1
        return out

    zt.ConstraintLogitsProcessor.__call__ = proc_timed
    orig_fill = zc.Session.fill_mask

    def fill_timed(self):
        t = time.perf_counter()
        r = orig_fill(self)
        phases["fill_ms"] += (time.perf_counter() - t) * 1e3
        phases["fill_calls"] += 1
        return r

    zc.Session.fill_mask = fill_timed

    gc_stats = {"collections": 0, "pause_ms": 0.0}
    gc_phase = {"prev": 0.0}

    def gc_cb(phase, info):
        now = time.perf_counter()
        if phase == "start":
            gc_phase["prev"] = now
        else:
            gc_stats["collections"] += 1
            gc_stats["pause_ms"] += (now - gc_phase["prev"]) * 1e3

    gc.callbacks.append(gc_cb)

    result = {"schema": args.schema, "warmup_schema": args.warmup_schema, "reps": []}

    # Warmup as in e2e: one default constraint, batch 1.
    warm = build_inputs(tokenizer, SCHEMAS[args.warmup_schema]["prompts"], 1)
    constrained_generate_safe(
        model, tokenizer, constraints[args.warmup_schema], inputs=warm, max_new_tokens=8, do_sample=False
    )

    for batch in args.batches:
        inputs = build_inputs(tokenizer, SCHEMAS[args.schema]["prompts"], batch)
        prompt_len = inputs["input_ids"].shape[1]
        for rep in range(args.reps):
            for k in phases:
                phases[k] = 0.0 if isinstance(phases[k], float) else 0
            gc_stats["collections"] = 0
            gc_stats["pause_ms"] = 0.0
            r = run_rep(
                model,
                tokenizer,
                constraints[args.schema],
                inputs,
                prompt_len,
                capture_steps=(rep > 0),
            )
            r.update(
                batch=batch,
                rep=rep,
                fill_ms=round(phases["fill_ms"], 2),
                fill_calls=phases["fill_calls"],
                unpack_ms=round(phases["unpack_ms"], 2),
                proc_calls=phases["calls"],
                gc_collections=gc_stats["collections"],
                gc_pause_ms=round(gc_stats["pause_ms"], 2),
            )
            result["reps"].append(r)
            print(
                f"b{batch} rep{rep}: total={r['total_ms']:.0f}ms ttft={r['ttft_ms']:.0f}ms "
                f"fill={r['fill_ms']:.1f}ms/{r['fill_calls']} unpack={r['unpack_ms']:.1f}ms "
                f"gc={r['gc_collections']}x{r['gc_pause_ms']:.0f}ms steps={r.get('steps_ms')}"
            )
            if rep == 0 and r["total_ms"] > 900:
                # Spike localization: re-run under cProfile.
                pr = cProfile.Profile()
                pr.enable()
                run_rep(model, tokenizer, constraints[args.schema], inputs, prompt_len)
                pr.disable()
                buf = io.StringIO()
                pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(18)
                result["profile_top"] = buf.getvalue().splitlines()[:40]
                print("--- cProfile (first repeat, top-18) ---")
                print("\n".join(result["profile_top"]))

    for c in constraints.values():
        c.close()
    engine.close()

    out = args.out or f"/tmp/diag_e2e_spike_{int(time.time())}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("saved:", out)


if __name__ == "__main__":
    main()
