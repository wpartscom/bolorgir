#!/usr/bin/env python3
"""B7. End-to-end GPU benchmark: constrained vs unconstrained generation.

SPEC 10.4 B7: one 1-3B model, batch 1/8/32 within VRAM,
max_new_tokens=512. Per-configuration metrics: TTFT, request time,
output tokens/s, p50/p99 inter-token latency, actual length of every
answer (published together with tokens/s so short answers do not create
an illusion of speedup). OOM/unsupported configurations are shown in the table.

Constrained: bolorgir via constrained_generate_safe (workaround for
bug B-1, see hf_common.py). Baseline: the same prompts and batching without
logits_processor. Text equality is NOT required (SPEC T5: different
admissible distributions). Every constrained answer is validated with
jsonschema; target validity 100%.

Greedy decoding (do_sample=False) for reproducibility; the seed is fixed
before every run (it matters only for T5 sampling runs).

Run from the project root:
    PYTHONPATH=python:benchmarks python3 benchmarks/bench_e2e.py \
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

MAX_NEW_TOKENS = 512
SEED = 42

# Three schemas of different complexity (number minimums/maximums are outside the MVP - not used).
SCHEMAS = {
    # S1: flat object with an enum and a number
    "s1_flat_enum": {
        "schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
                "amount": {"type": "number"},
                "currency": {"type": "string", "enum": ["USD", "EUR", "JPY"]},
            },
            "required": ["action", "amount", "currency"],
            "additionalProperties": False,
        },
        "prompts": [
            "You are a trading API. Reply with one JSON object for the request.\nRequest: buy 150 shares priced in USD.\nJSON:",
            "You are a trading API. Reply with one JSON object for the request.\nRequest: sell 42.5 contracts in EUR.\nJSON:",
            "You are a trading API. Reply with one JSON object for the request.\nRequest: hold 1000 units, currency JPY.\nJSON:",
            "You are a trading API. Reply with one JSON object for the request.\nRequest: buy 7 bonds for 990.25 USD.\nJSON:",
        ],
    },
    # S2: nested object with an array of objects
    "s2_nested_arrays": {
        "schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "integer"},
                "customer": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "maxLength": 40},
                        "vip": {"type": "boolean"},
                    },
                    "required": ["name", "vip"],
                    "additionalProperties": False,
                },
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku": {"type": "string", "enum": ["A-1", "B-2", "C-3"]},
                            "qty": {"type": "integer"},
                        },
                        "required": ["sku", "qty"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["order_id", "customer", "items"],
            "additionalProperties": False,
        },
        "prompts": [
            "Return the order as JSON.\nOrder 5001: Anna (vip) bought 2x A-1 and 1x C-3.\nJSON:",
            "Return the order as JSON.\nOrder 5002: Ben (not vip) bought 5x B-2.\nJSON:",
            "Return the order as JSON.\nOrder 5003: Cara (vip) bought 1x A-1, 3x B-2 and 2x C-3.\nJSON:",
            "Return the order as JSON.\nOrder 5004: Dan (not vip) bought 12x C-3.\nJSON:",
        ],
    },
    # S3: optional fields and string length bounds
    "s3_optional_bounded": {
        "schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 24},
                "subtitle": {"type": "string", "maxLength": 48},
                "year": {"type": "integer"},
                "rating": {"type": "number"},
                "tags": {
                    "type": "array",
                    "maxItems": 4,
                    "items": {"type": "string", "maxLength": 12},
                },
            },
            "required": ["title", "year"],
            "additionalProperties": False,
        },
        "prompts": [
            "Describe the movie as JSON.\nMovie: 'Dune' (2021), rating 8.0, tags: sci-fi, epic.\nJSON:",
            "Describe the book as JSON.\nBook: 'The Hobbit' (1937), subtitle 'There and Back Again', rating 9.1, tags: fantasy, classic.\nJSON:",
            "Describe the album as JSON.\nAlbum: 'Kind of Blue' (1959), rating 9.8, tags: jazz.\nJSON:",
            "Describe the game as JSON.\nGame: 'Portal' (2007), rating 8.9, tags: puzzle, sci-fi, short.\nJSON:",
        ],
    },
    # S4: secondary holdout: a previously unused
    # schema for independent generalization checks; array of step objects,
    # four enum values for the step name, integer/number fields
    "s4_batch_jobs": {
        "schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer"},
                "status": {
                    "type": "string",
                    "enum": ["queued", "running", "done", "failed"],
                },
                "priority": {"type": "integer"},
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "enum": ["load", "map", "reduce", "store"],
                            },
                            "count": {"type": "integer"},
                        },
                        "required": ["name", "count"],
                        "additionalProperties": False,
                    },
                },
                "comment": {"type": "string", "maxLength": 24},
            },
            "required": ["job_id", "status", "priority", "steps"],
            "additionalProperties": False,
        },
        "prompts": [
            "You are a batch scheduler. Reply with one JSON object for the request.\nJob 17 is done: it stored 3 outputs after mapping 120 records.\nJSON:",
            "You are a batch scheduler. Reply with one JSON object for the request.\nJob 42 is running with priority 5: loading 1 shard.\nJSON:",
            "You are a batch scheduler. Reply with one JSON object for the request.\nJob 7 failed at priority 2: reducing 12 groups hit a limit.\nJSON:",
            "You are a batch scheduler. Reply with one JSON object for the request.\nJob 99 is queued: it will map 2000 records first.\nJSON:",
        ],
    },
}

BATCH_SIZES = [1, 8, 32]


def build_inputs(tokenizer, prompts, batch):
    chosen = [prompts[i % len(prompts)] for i in range(batch)]
    tokenizer.padding_side = "left"
    enc = tokenizer(chosen, padding=True, return_tensors="pt")
    return {k: v.to("cuda") for k, v in enc.items()}


def run_constrained(model, tokenizer, engine, constraint, inputs, prompt_len):
    torch.manual_seed(SEED)
    streamer = StepStreamer()
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    t0 = time.perf_counter_ns()
    res = constrained_generate_safe(
        model,
        tokenizer,
        constraint,
        inputs=inputs,
        max_new_tokens=MAX_NEW_TOKENS,
        do_sample=False,
        streamer=streamer,
    )
    torch.cuda.synchronize()
    t1 = time.perf_counter_ns()
    peak = torch.cuda.max_memory_allocated()
    eos_id = tokenizer.eos_token_id
    rows = []
    for i in range(res.sequences.shape[0]):
        n_new = row_new_tokens(res.sequences[i], prompt_len, eos_id)
        text = tokenizer.decode(
            res.sequences[i][prompt_len : prompt_len + n_new],
            skip_special_tokens=True,
        )
        rows.append(
            {
                "new_tokens": n_new,
                "completed": res.completed[i],
                "stop_reason": res.stop_reason[i],
                "text": text,
            }
        )
    close_result(res)
    return {
        "t_start_ns": t0,
        "t_end_ns": t1,
        "step_ts_ns": streamer.ts,
        "peak_vram_bytes": peak,
        "rows": rows,
    }


def run_baseline(model, tokenizer, inputs, prompt_len):
    torch.manual_seed(SEED)
    streamer = StepStreamer()
    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    t0 = time.perf_counter_ns()
    out = model.generate(
        **inputs,
        max_new_tokens=MAX_NEW_TOKENS,
        do_sample=False,
        streamer=streamer,
    )
    torch.cuda.synchronize()
    t1 = time.perf_counter_ns()
    peak = torch.cuda.max_memory_allocated()
    sequences = out.sequences if hasattr(out, "sequences") else out
    eos_id = tokenizer.eos_token_id
    rows = []
    for i in range(sequences.shape[0]):
        n_new = row_new_tokens(sequences[i], prompt_len, eos_id)
        rows.append({"new_tokens": n_new})
    del out, sequences
    return {
        "t_start_ns": t0,
        "t_end_ns": t1,
        "step_ts_ns": streamer.ts,
        "peak_vram_bytes": peak,
        "rows": rows,
    }


def summarize_run(run, schema=None):
    ts = run["step_ts_ns"]
    total_ns = run["t_end_ns"] - run["t_start_ns"]
    # the streamer's first put is the prompt itself; tokens start from the second
    tok_ts = ts[1:]
    ttft_ns = (tok_ts[0] - run["t_start_ns"]) if tok_ts else None
    itls = [b - a for a, b in zip(tok_ts, tok_ts[1:])]
    itls_sorted = sorted(itls)

    def pct(p):
        if not itls_sorted:
            return None
        k = min(len(itls_sorted) - 1, max(0, round(p * (len(itls_sorted) - 1))))
        return itls_sorted[k]

    n_tokens = sum(r["new_tokens"] for r in run["rows"])
    out = {
        "ttft_ms": ttft_ns / 1e6 if ttft_ns is not None else None,
        "total_s": total_ns / 1e9,
        "steps": len(tok_ts),
        "output_tokens": n_tokens,
        "tokens_per_s": n_tokens / (total_ns / 1e9) if total_ns else None,
        "itl_p50_ms": pct(0.50) / 1e6 if itls_sorted else None,
        "itl_p99_ms": pct(0.99) / 1e6 if itls_sorted else None,
        "peak_vram_mb": run["peak_vram_bytes"] / 2**20,
        "row_lengths": [r["new_tokens"] for r in run["rows"]],
    }
    if schema is not None:
        valid = 0
        for r in run["rows"]:
            try:
                jsonschema.validate(json.loads(r["text"]), schema)
                r["valid"] = True
                valid += 1
            except Exception:
                r["valid"] = False
        out["valid_rows"] = valid
        out["validity_rate"] = valid / len(run["rows"])
        out["completed_rows"] = sum(1 for r in run["rows"] if r["completed"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="results directory")
    ap.add_argument("--reps", type=int, default=3, help="repeats per configuration")
    ap.add_argument("--batches", type=int, nargs="*", default=BATCH_SIZES)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    assert torch.cuda.is_available(), "CUDA is not available"
    gpu_name = torch.cuda.get_device_name(0)
    vram_total = torch.cuda.get_device_properties(0).total_memory
    print(f"GPU: {gpu_name}, VRAM {vram_total / 2**30:.1f} GiB")

    model, tokenizer = load_model()
    engine = Engine(mode="adaptive", tokenizer=tokenizer)
    constraints = {
        name: (engine.compile(spec["schema"]), spec)
        for name, spec in SCHEMAS.items()
    }

    results = {
        "case": "bench_e2e",
        "model": MODEL_ID,
        "gpu": gpu_name,
        "vram_total_gb": vram_total / 2**30,
        "max_new_tokens": MAX_NEW_TOKENS,
        "decode": "greedy",
        "seed": SEED,
        "torch": torch.__version__,
        "runs": [],
    }

    # warm up CUDA and the mask cache
    print("warmup...")
    warm = build_inputs(tokenizer, SCHEMAS["s1_flat_enum"]["prompts"], 1)
    c0 = next(iter(constraints.values()))[0]
    r = constrained_generate_safe(
        model, tokenizer, c0, inputs=warm, max_new_tokens=16, do_sample=False
    )
    close_result(r)
    model.generate(**warm, max_new_tokens=16, do_sample=False)
    torch.cuda.synchronize()

    try:
        for schema_name, (constraint, spec) in constraints.items():
            for batch in args.batches:
                inputs = build_inputs(tokenizer, spec["prompts"], batch)
                prompt_len = inputs["input_ids"].shape[1]
                for mode in ("constrained", "baseline"):
                    for rep in range(args.reps):
                        label = f"{schema_name} b{batch} {mode} rep{rep}"
                        try:
                            if mode == "constrained":
                                run = run_constrained(
                                    model, tokenizer, engine, constraint,
                                    inputs, prompt_len,
                                )
                                summ = summarize_run(run, schema=spec["schema"])
                            else:
                                run = run_baseline(
                                    model, tokenizer, inputs, prompt_len
                                )
                                summ = summarize_run(run)
                            summ.update(status="OK")
                        except torch.cuda.OutOfMemoryError:
                            torch.cuda.empty_cache()
                            run, summ = None, {"status": "OOM"}
                        rec = {
                            "schema": schema_name,
                            "batch": batch,
                            "mode": mode,
                            "rep": rep,
                            "prompt_len": prompt_len,
                            "summary": summ,
                        }
                        if run is not None:
                            rec["rows"] = run["rows"]
                        results["runs"].append(rec)
                        if summ["status"] == "OK":
                            extra = (
                                f" valid={summ['valid_rows']}/{batch}"
                                if mode == "constrained"
                                else ""
                            )
                            print(
                                f"{label}: ttft={summ['ttft_ms']:.0f}ms "
                                f"total={summ['total_s']:.2f}s "
                                f"toks={summ['output_tokens']} "
                                f"tps={summ['tokens_per_s']:.1f}{extra}",
                                flush=True,
                            )
                        else:
                            print(f"{label}: {summ['status']}", flush=True)
    finally:
        for constraint, _ in constraints.values():
            constraint.close()
        engine.close()

    out_path = os.path.join(args.out, "bench_e2e.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"saved: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
