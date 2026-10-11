#!/usr/bin/env python3
"""Targeted recovery of full pass documents for selected schemas.

Runs the 20 recorded passes with their seeds on the library given via
BLG_LIB_PATH, keeps every pass record with its full document, and marks
whether the outcome fields match the recorded rows of the report sidecar.

Usage: save_documents.py --report R --tokenizer llama-3.1 \
           --select list:FILE --out OUT.jsonl [--max-seconds 10]
"""
import argparse
import json
import os
import sys

os.environ.setdefault("BOLORGIR_COVERAGE_PROFILE", "spec-v1")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))

import bench_jsb_coverage as h  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--report", required=True)
ap.add_argument("--tokenizer", default="byte")
ap.add_argument("--select", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--max-seconds", type=float, default=10.0)
args = ap.parse_args()

report, side = h.load_report(args.report)
snap = h.SNAPSHOTS["maskbench" if report["meta"]["corpus"]["path"].startswith("maskbench") else "repo"]
items = [tuple(l.strip().split("/", 1)) for l in open(args.select[5:]) if l.strip()]
gt = h.GenTokenizer(args.tokenizer)
gp, _so = h._gen_mods()
opts = {"passes": 20, "max_steps": 8192, "max_seconds": args.max_seconds, "schema_budget": 0.0}

out = open(args.out, "w")
print(json.dumps({"library_sha256": h.library_sha256(), "items": len(items),
                  "tokenizer": args.tokenizer}), flush=True)
for dataset, fname in items:
    data, schema_doc, _ = h.load_schema_bytes(snap, os.path.join(snap["data_dir"], fname))
    validator, _vreason = gp.make_reference_validator(data)
    ctx = h.make_context(gt.spec)
    grammar, failure = h.compile_for(gt, ctx, data, dataset, fname)
    if failure is not None:
        out.write(json.dumps({"dataset": dataset, "file": fname,
                              "compile_failure": failure}) + "\n")
        out.flush()
        ctx.destroy()
        continue
    plan = h.pass_plan(h.parsed_schema(data, schema_doc), validator)
    recorded = (side.get((dataset, fname)) or {}).get(args.tokenizer, [])
    for k in range(1, 21):
        seed = h.pass_seed(dataset, fname, gt.key, k)
        rec, _doc = h.gen_pass(gt, ctx, grammar, k, seed, plan, opts)
        row = {"dataset": dataset, "file": fname, "pass": k, "seed": seed,
               "outcome": rec["outcome"], "stage": rec["stage"], "code": rec["code"],
               "step": rec["step"], "prefix_length": rec.get("prefix_length"),
               "document_b64": rec.get("document")}
        if k <= len(recorded):
            prev = recorded[k - 1]
            row["recorded"] = {"outcome": prev[0], "stage": prev[1], "code": prev[2],
                               "step": prev[3], "prefix_length": prev[4]}
            row["match_recorded"] = (prev[0] == row["outcome"] and prev[1] == row["stage"] and
                                     prev[2] == row["code"] and prev[3] == row["step"] and
                                     prev[4] == row["prefix_length"])
        out.write(json.dumps(row) + "\n")
        out.flush()
        if rec["outcome"] != "completed_valid":
            print(f"{dataset}/{fname} pass {k}: {rec['outcome']} {rec['stage']} "
                  f"{rec['code']} step {rec['step']} match {row.get('match_recorded')}",
                  flush=True)
    h.blg.grammar_release(grammar)
    ctx.destroy()
out.close()
