#!/usr/bin/env python3
"""B1. Cold start: N INDEPENDENT PROCESSES per engine.

Every repeat is a separate process (this same script with --one-shot) that
measures: engine import, HF tokenizer load (cache), prepare, compile of a
new schema, first mask, process peak RSS. The driver runs processes
SEQUENTIALLY (no CPU contention), collects each JSON and computes
p50/p95/p99/max over N repeats (SPEC 10.5: >= 30 cold repeats).

Run from the project root:
    PYTHONPATH=python:benchmarks python3 benchmarks/bench_coldproc.py \
        --repeats 30 --engines zig_adaptive,xgrammar,llguidance
"""

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_common as bc

TOKENIZER_NAME = "openai-community/gpt2"
TOKENIZER_REVISION = "607a30d783dfa663caf39e06633721c8d4cfcd7e"


def one_shot(engine, schema, schema_str):
    """One cold run in the current (fresh) process."""
    import time
    t_start = time.perf_counter_ns()
    res = {"engine": engine}

    from transformers import AutoTokenizer
    t0 = time.perf_counter_ns()
    hf_tok = AutoTokenizer.from_pretrained(TOKENIZER_NAME,
                                           revision=TOKENIZER_REVISION)
    res["hf_tokenizer_load_ns"] = time.perf_counter_ns() - t0

    if engine == "zig_adaptive" or engine == "zig_lazy":
        mode = engine.split("_", 1)[1]
        t0 = time.perf_counter_ns()
        import bolorgir as zc
        res["engine_import_ns"] = time.perf_counter_ns() - t0
        t0 = time.perf_counter_ns()
        bundle = zc.TokenizerBundle.from_hf(hf_tok, use_cache=False)
        res["tokenizer_prepare_ns"] = time.perf_counter_ns() - t0
        eng = zc.Engine(mode=mode, memory_limit_mb=256, tokenizer=bundle)
        t0 = time.perf_counter_ns()
        constraint = eng.compile(schema)
        res["compile_ns"] = time.perf_counter_ns() - t0
        t0 = time.perf_counter_ns()
        session = constraint.create_session()
        session.fill_mask()
        res["first_mask_ns"] = time.perf_counter_ns() - t0
        session.close()
        constraint.close()
        eng.close()
    elif engine == "xgrammar":
        t0 = time.perf_counter_ns()
        import xgrammar as xg
        res["engine_import_ns"] = time.perf_counter_ns() - t0
        t0 = time.perf_counter_ns()
        info = xg.TokenizerInfo.from_huggingface(hf_tok)
        res["tokenizer_prepare_ns"] = time.perf_counter_ns() - t0
        compiler = xg.GrammarCompiler(info, cache_enabled=False)
        t0 = time.perf_counter_ns()
        compiled = compiler.compile_json_schema(schema_str)
        res["compile_ns"] = time.perf_counter_ns() - t0
        t0 = time.perf_counter_ns()
        matcher = xg.GrammarMatcher(compiled)
        bitmask = xg.allocate_token_bitmask(1, info.vocab_size)
        matcher.fill_next_token_bitmask(bitmask)
        res["first_mask_ns"] = time.perf_counter_ns() - t0
    elif engine == "llguidance":
        t0 = time.perf_counter_ns()
        import llguidance as lg
        import llguidance.hf as lghf
        res["engine_import_ns"] = time.perf_counter_ns() - t0
        t0 = time.perf_counter_ns()
        tok = lghf.from_tokenizer(hf_tok)
        res["tokenizer_prepare_ns"] = time.perf_counter_ns() - t0
        t0 = time.perf_counter_ns()
        grm = lg.grammar_from("json_schema", schema_str)
        res["compile_ns"] = time.perf_counter_ns() - t0
        t0 = time.perf_counter_ns()
        m = lg.LLMatcher(tok, grm)
        m.compute_bitmask()
        res["first_mask_ns"] = time.perf_counter_ns() - t0
    else:
        raise SystemExit(f"unknown engine {engine}")

    res["total_cold_ns"] = time.perf_counter_ns() - t_start
    res["peak_rss_bytes"] = bc.peak_rss_bytes()
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="closed_object_action_amount")
    ap.add_argument("--corpus-dir", default=bc.CORPUS_DIR)
    ap.add_argument("--repeats", type=int, default=30)
    ap.add_argument("--engines", default="zig_adaptive,xgrammar,llguidance")
    ap.add_argument("--seed", type=int, default=bc.SEED)
    ap.add_argument("--one-shot", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--timeout", type=int, default=120,
                    help="timeout of one process, seconds")
    args = ap.parse_args()

    entry, schema_bytes = bc.load_schema(args.schema, args.corpus_dir)
    if entry["kind"] != "json_schema" or not entry["expect_support"]:
        bc.skip(f"schema {args.schema} is not a supported json_schema")
    schema = json.loads(schema_bytes)
    schema_str = schema_bytes.decode("utf-8")

    if args.one_shot:
        res = one_shot(args.one_shot, schema, schema_str)
        res["status"] = "OK"
        bc.emit(res)
        return

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    report = {}
    env = dict(os.environ)
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.join(root, "python"), here, env.get("PYTHONPATH", "")])

    for engine in engines:
        keys = ["hf_tokenizer_load_ns", "engine_import_ns", "tokenizer_prepare_ns",
                "compile_ns", "first_mask_ns", "total_cold_ns", "peak_rss_bytes"]
        samples = {k: [] for k in keys}
        errors = []
        for rep in range(args.repeats):
            cmd = [sys.executable, os.path.abspath(__file__),
                   "--one-shot", engine, "--schema", args.schema,
                   "--corpus-dir", args.corpus_dir]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=args.timeout, env=env)
                data = json.loads(proc.stdout.strip())
                if data.get("status") != "OK":
                    errors.append({"rep": rep, "data": data})
                    continue
                for k in keys:
                    samples[k].append(data[k])
            except Exception as e:
                errors.append({"rep": rep, "error": str(e)[:200],
                               "stderr": (proc.stderr[-500:] if "proc" in dir() else "")})
        report[engine] = {
            "repeats_ok": len(samples["compile_ns"]),
            "errors": errors,
            **{k: bc.percentile_stats(v) for k, v in samples.items()},
        }
        print(f"{engine}: ok={report[engine]['repeats_ok']}/{args.repeats} "
              f"errors={len(errors)}", file=sys.stderr, flush=True)

    bc.emit({
        "status": "OK", "case": "B1_cold_processes", "schema": args.schema,
        "seed": args.seed, "repeats_per_engine": args.repeats,
        "processes": "sequential independent processes",
        "tokenizer": {"name": TOKENIZER_NAME, "revision": TOKENIZER_REVISION},
        "engines": report,
    })


if __name__ == "__main__":
    main()
