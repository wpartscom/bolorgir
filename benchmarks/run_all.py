#!/usr/bin/env python3
"""Run all benchmarks and record raw results.

Results: benchmarks/results/<timestamp>/{bench_prepare,bench_masks,
bench_batch,bench_memory,xgrammar,llguidance}.json + summary.json.
Every script prints SKIP with exit code 0 if its engine is unavailable -
the run continues.
"""

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

SCRIPTS = [
    ("bench_prepare", ["bench_prepare.py"]),
    ("bench_masks", ["bench_masks.py"]),
    ("bench_batch", ["bench_batch.py"]),
    ("bench_memory", ["bench_memory.py"]),
    ("bench_compare_uniform", ["bench_compare_uniform.py",
                               "--schema", "big_enum_64",
                               "--document", '{"symbol":"sym_42","weight":0.5}',
                               # Protocol v3: 1000 cold
                               # first-mask observations per engine/collection.
                               "--repeats", "1000"]),
    ("bench_coldproc", ["bench_coldproc.py", "--schema", "big_enum_64"]),
    ("bench_longrun", ["bench_longrun.py"]),
    ("bench_schemas_tokenizers", ["bench_schemas_tokenizers.py"]),
    ("xgrammar", [os.path.join("compare", "xgrammar_runner.py")]),
    ("llguidance", [os.path.join("compare", "llguidance_runner.py")]),
    # Secondary holdout corpus: a previously unused
    # schema for an independent generalization check; the gate requires the
    # same thresholds (secondary_guard in manifest). Files are written
    # separately (*_secondary.json).
    ("bench_masks_secondary", ["bench_masks.py",
                               "--schema", "secondary_geo_route"]),
    ("bench_compare_uniform_secondary", [
        "bench_compare_uniform.py",
        "--schema", "secondary_geo_route",
        "--document",
        '{"route_id":7,"active":true,"waypoints":[{"kind":"start",'
        '"label":"depot","distance_km":0},{"kind":"stop",'
        '"label":"north_gate","distance_km":12.5}]}',
        "--repeats", "1000",
    ]),
]


def run_one(name, script_rel, extra_args, timeout):
    cmd = [sys.executable, os.path.join(HERE, script_rel)] + extra_args
    t0 = time.perf_counter_ns()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        wall_ns = time.perf_counter_ns() - t0
        out = proc.stdout.strip()
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = {"status": "ERROR", "reason": "non-JSON output",
                    "stdout": out[-2000:], "stderr": proc.stderr[-2000:]}
        data["_runner"] = {"exit_code": proc.returncode, "wall_ns": wall_ns}
        if proc.returncode != 0 and data.get("status") not in ("SKIP",):
            data["status"] = "ERROR"
            data.setdefault("stderr", proc.stderr[-2000:])
        return data
    except subprocess.TimeoutExpired:
        return {"status": "TIMEOUT", "timeout_seconds": timeout}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="closed_object_action_amount")
    ap.add_argument("--timeout", type=int, default=1800,
                    help="timeout of one script, s (timeouts are recorded; "
                         "bench_coldproc runs 30x3 processes, >= 900 required)")
    ap.add_argument("--results-root", default=os.path.join(HERE, "results"))
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%dT%H%M%S") + (f"_{args.tag}" if args.tag else "")
    out_dir = os.path.join(args.results_root, stamp)
    os.makedirs(out_dir, exist_ok=True)

    summary = {"timestamp": stamp, "schema": args.schema, "cases": {}}
    for name, script in SCRIPTS:
        extra = ["--schema", args.schema] if name in (
            "bench_prepare", "bench_masks", "bench_batch", "bench_memory") else []
        # script may carry fixed args (e.g. the §10.6 control scenario for
        # bench_compare_uniform); they must be passed through as well.
        data = run_one(name, script[0], script[1:] + extra, args.timeout)
        with open(os.path.join(out_dir, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        summary["cases"][name] = data.get("status", "ERROR")
        print(f"{name}: {data.get('status')}")

    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"results: {out_dir}")
    sys.exit(0)


if __name__ == "__main__":
    main()
