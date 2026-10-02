#!/usr/bin/env python3
"""B7. End-to-end GPU: constrained vs CONSTRAINED-baseline.

Per SPEC 10.6 the end-to-end comparison runs against the chosen
constrained-baseline, not against unconstrained generation. The chosen
baseline is XGrammar 0.2.6 (pinned in manifest; the stock HF integration
xgrammar.contrib.hf.LogitsProcessor). llguidance is available via
option (--engines) if needed.

Identical generation configuration (SPEC 10.5): the same model, dtype, batch,
max_new_tokens=512, greedy, seed 42, reps repeats; text equality is NOT
required (different admissible distributions, SPEC T5). All constrained
answers from both engines are validated with jsonschema; answer lengths are
published next to tokens/s (short answers do not create an illusion of
speedup, SPEC 10.4 B7).

Run from the project root:
    PYTHONPATH=python:benchmarks python3 benchmarks/bench_e2e_constrained.py \
        --out benchmarks/results/<timestamp>
"""

from __future__ import annotations

import argparse
import json
import os
import time

import torch
import jsonschema

from bolorgir import Engine

from hf_common import (
    MODEL_ID,
    StepStreamer,
    close_result,
    constrained_generate_safe,
    load_model,
    row_new_tokens,
)
from bench_e2e import SCHEMAS, build_inputs, summarize_run

MAX_NEW_TOKENS = 512
SEED = 42


def run_zig(model, tokenizer, constraint, inputs, prompt_len):
    torch.manual_seed(SEED)
    streamer = StepStreamer()
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    t0 = time.perf_counter_ns()
    res = constrained_generate_safe(
        model, tokenizer, constraint, inputs=inputs,
        max_new_tokens=MAX_NEW_TOKENS, do_sample=False, streamer=streamer,
    )
    torch.cuda.synchronize()
    t1 = time.perf_counter_ns()
    peak = torch.cuda.max_memory_allocated()
    eos_id = tokenizer.eos_token_id
    rows = []
    for i in range(res.sequences.shape[0]):
        n_new = row_new_tokens(res.sequences[i], prompt_len, eos_id)
        text = tokenizer.decode(res.sequences[i][prompt_len: prompt_len + n_new],
                                skip_special_tokens=True)
        rows.append({"new_tokens": n_new, "completed": res.completed[i],
                     "stop_reason": res.stop_reason[i], "text": text})
    close_result(res)
    return {"t_start_ns": t0, "t_end_ns": t1, "step_ts_ns": streamer.ts,
            "peak_vram_bytes": peak, "rows": rows}


def run_xgrammar(model, tokenizer, xg_factory, schema_str, inputs, prompt_len):
    torch.manual_seed(SEED)
    streamer = StepStreamer()
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    t0 = time.perf_counter_ns()
    proc = xg_factory(schema_str, inputs["input_ids"].shape[0])
    out = model.generate(
        **inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False,
        streamer=streamer, logits_processor=[proc],
    )
    torch.cuda.synchronize()
    t1 = time.perf_counter_ns()
    peak = torch.cuda.max_memory_allocated()
    sequences = out.sequences if hasattr(out, "sequences") else out
    eos_id = tokenizer.eos_token_id
    rows = []
    for i in range(sequences.shape[0]):
        n_new = row_new_tokens(sequences[i], prompt_len, eos_id)
        text = tokenizer.decode(sequences[i][prompt_len: prompt_len + n_new],
                                skip_special_tokens=True)
        completed = n_new < MAX_NEW_TOKENS
        rows.append({"new_tokens": n_new, "completed": completed,
                     "stop_reason": "eos" if completed else "max_length",
                     "text": text})
    del out, sequences
    return {"t_start_ns": t0, "t_end_ns": t1, "step_ts_ns": streamer.ts,
            "peak_vram_bytes": peak, "rows": rows}


def make_xgrammar(tokenizer):
    import xgrammar as xg
    from xgrammar.contrib.hf import LogitsProcessor

    info = xg.TokenizerInfo.from_huggingface(tokenizer)
    compiler = xg.GrammarCompiler(info)

    def factory(schema_str, batch):
        compiled = compiler.compile_json_schema(schema_str)
        return LogitsProcessor(compiled)

    return factory


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=4)
    ap.add_argument("--batches", type=int, nargs="*", default=[1, 8, 32])
    ap.add_argument("--engines", default="zig,xgrammar")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    assert torch.cuda.is_available(), "CUDA is not available"
    gpu_name = torch.cuda.get_device_name(0)
    vram_total = torch.cuda.get_device_properties(0).total_memory
    print(f"GPU: {gpu_name}, VRAM {vram_total / 2**30:.1f} GiB")

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    model, tokenizer = load_model()

    engine = Engine(mode="adaptive", tokenizer=tokenizer)
    constraints = {
        name: (engine.compile(spec["schema"]), spec)
        for name, spec in SCHEMAS.items()
    }
    xg_factory = make_xgrammar(tokenizer) if "xgrammar" in engines else None

    results = {
        "case": "bench_e2e_constrained", "model": MODEL_ID, "gpu": gpu_name,
        "vram_total_gb": vram_total / 2**30, "max_new_tokens": MAX_NEW_TOKENS,
        "decode": "greedy", "seed": SEED, "torch": torch.__version__,
        "constrained_baseline": "xgrammar 0.2.6 (manifest)",
        "runs": [],
    }

    print("warmup...")
    warm = build_inputs(tokenizer, SCHEMAS["s1_flat_enum"]["prompts"], 1)
    c0 = next(iter(constraints.values()))[0]
    r = constrained_generate_safe(model, tokenizer, c0, inputs=warm,
                                  max_new_tokens=16, do_sample=False)
    close_result(r)
    if xg_factory:
        model.generate(**warm, max_new_tokens=16, do_sample=False,
                       logits_processor=[xg_factory(
                           json.dumps(SCHEMAS["s1_flat_enum"]["schema"]), 1)])
    torch.cuda.synchronize()

    # Predefined warmed mode: for the core the first
    # repeat of a new schema pays mask-cache misses (~2 ms/state on a 151k
    # vocabulary), for xgrammar it is a cold compile. Both engines are warmed
    # with one generation per EACH schema; the first request is published
    # separately (field "warmups"), and the scored repeats run on the warmed
    # state, alternating engines.
    results["protocol"] = {
        "warmed_mode": True,
        "note": "each (schema, batch) warmed once per engine before timed reps; "
                "first request published in 'warmups'",
        "engine_order": "balanced_ab_ba",
        "engine_order_note": "zig first on even reps, xgrammar first on odd "
                             "reps",
        "reps": args.reps,
        "batches": args.batches,
    }
    results["warmups"] = []
    print("batch warmups (first requests are published separately)...")

    try:
        for schema_name, (constraint, spec) in constraints.items():
            schema_str = json.dumps(spec["schema"])
            for batch in args.batches:
                inputs = build_inputs(tokenizer, spec["prompts"], batch)
                prompt_len = inputs["input_ids"].shape[1]
                modes = []
                if "zig" in engines:
                    modes.append("zig_constrained")
                if xg_factory:
                    modes.append("xgrammar_constrained")
                # Warmup at the target batch size: for the core the first rep
                # of a new (schema, batch) pays mask/buffer cache misses, for
                # xg it warms up the compiler; the first request is published
                # separately and is not part of the scored repeats.
                for mode in modes:
                    if mode == "zig_constrained":
                        w = run_zig(model, tokenizer, constraint, inputs, prompt_len)
                        summ_w = summarize_run(w, schema=spec["schema"])
                        results["warmups"].append({
                            "schema": schema_name, "batch": batch,
                            "mode": mode, "total_ms": round(summ_w["total_s"] * 1e3, 1),
                        })
                    elif xg_factory:
                        w = run_xgrammar(model, tokenizer, xg_factory, schema_str,
                                         inputs, prompt_len)
                        results["warmups"].append({
                            "schema": schema_name, "batch": batch,
                            "mode": mode,
                            "total_ms": round(
                                (w["t_end_ns"] - w["t_start_ns"]) / 1e6, 1),
                        })
                for rep in range(args.reps):
                    # Balanced AB/BA order: zig goes
                    # first on even reps, xgrammar on odd reps; paired
                    # comparison symmetrizes thermal and background drift
                    # between engines within a block (file x rep).
                    order = modes if rep % 2 == 0 else list(reversed(modes))
                    for order_pos, mode in enumerate(order):
                        label = f"{schema_name} b{batch} {mode} rep{rep}"
                        try:
                            if mode == "zig_constrained":
                                run = run_zig(model, tokenizer, constraint,
                                              inputs, prompt_len)
                            else:
                                run = run_xgrammar(model, tokenizer, xg_factory,
                                                   schema_str, inputs, prompt_len)
                            summ = summarize_run(run, schema=spec["schema"])
                            summ.update(status="OK")
                        except torch.cuda.OutOfMemoryError:
                            torch.cuda.empty_cache()
                            run, summ = None, {"status": "OOM"}
                        except Exception as e:
                            run, summ = None, {"status": "ERROR",
                                               "error": f"{type(e).__name__}: {e}"[:300]}
                        rec = {"schema": schema_name, "batch": batch, "mode": mode,
                               "rep": rep, "order_pos": order_pos,
                               "block_id": f"{schema_name}-b{batch}-rep{rep}",
                               "prompt_len": prompt_len, "summary": summ}
                        if run is not None:
                            rec["rows"] = run["rows"]
                            rec["t_start_ns"] = run["t_start_ns"]
                            rec["t_end_ns"] = run["t_end_ns"]
                        results["runs"].append(rec)
                        if summ["status"] == "OK":
                            print(f"{label}: ttft={summ['ttft_ms']:.0f}ms "
                                  f"total={summ['total_s']:.2f}s "
                                  f"toks={summ['output_tokens']} "
                                  f"tps={summ['tokens_per_s']:.1f} "
                                  f"valid={summ['valid_rows']}/{batch}",
                                  flush=True)
                        else:
                            print(f"{label}: {summ['status']} "
                                  f"{summ.get('error', '')}", flush=True)
    finally:
        for constraint, _ in constraints.values():
            constraint.close()
        engine.close()

    out_path = os.path.join(args.out, "bench_e2e_constrained.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
