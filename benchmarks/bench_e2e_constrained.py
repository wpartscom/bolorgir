#!/usr/bin/env python3
"""B7. End-to-end GPU: constrained vs CONSTRAINED-baseline (аудит 2026-09-15, п.4).

По ТЗ 10.6 сравнение end-to-end идёт против выбранного constrained-baseline,
а не против генерации без ограничений. Выбранный baseline — XGrammar 0.2.6
(закреплён в manifest; штатная HF-интеграция xgrammar.contrib.hf.LogitsProcessor).
llguidance при необходимости — опцией (--engines).

Одинаковая конфигурация генерации (ТЗ 10.5): та же модель, dtype, батч,
max_new_tokens=512, greedy, seed 42, reps повторов; равенство текстов НЕ
требуется (разные допустимые распределения, ТЗ T5). Все constrained-ответы
обоих движков валидируются jsonschema; публикуются длины ответов рядом с
tokens/s (короткие ответы не создают иллюзию ускорения, ТЗ 10.4 B7).

Запуск из корня проекта:
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

from zig_constraints import Engine

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
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--batches", type=int, nargs="*", default=[1, 8, 32])
    ap.add_argument("--engines", default="zig,xgrammar")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    assert torch.cuda.is_available(), "CUDA недоступна"
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
                for mode in modes:
                    for rep in range(args.reps):
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
                               "rep": rep, "prompt_len": prompt_len, "summary": summ}
                        if run is not None:
                            rec["rows"] = run["rows"]
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
