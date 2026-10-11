#!/usr/bin/env python3
"""Process-history dependence of a large compile (input to T3; a
diagnostic, not a scored tool; docs/RUN_HISTORY.md, first unresolved
limitation).

Default (rounds): in one process, N times: a fresh context with the
coverage limits, the schema compiled in it, one pass of the published
coverage run replayed (its seed from the report sidecar), the context
destroyed. Each round prints the pass outcome or the compile status, the
context memory in use right after the compile (grammar and total) and its
peak (total and temp, against the 256 MiB limit), and the process RSS,
virtual size, thread count and number of mappings.
--inorder instead runs every pass of the schema on one context, in
order, and compares each with its record.

    BOLORGIR_COVERAGE_PROFILE=spec-v1 PYTHONPATH=python \\
        python3 benchmarks/diag_process_history.py --rounds 12 --load 20

--load N keeps N busy-loop processes running during the rounds;
--compile-only skips the passes. Defaults: Github_hard/o21363.json,
pass 4, byte tokenizer, the published repository report.
"""

import argparse
import json
import os
import subprocess
import sys

os.environ.setdefault("BOLORGIR_COVERAGE_PROFILE", "spec-v1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_jsb_coverage as h  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_report():
    """The committed repository report (raw or gzip-compressed)."""
    p = os.path.join(_ROOT, "benchmarks/coverage/spec-v1/coverage_repo.json")
    return p if os.path.exists(p) else p + ".gz"


def proc_state():
    st = {}
    with open("/proc/self/status") as f:
        for line in f:
            k = line.split(":")[0]
            if k in ("VmRSS", "VmSize", "Threads"):
                st[k] = int(line.split()[1])
    with open("/proc/self/maps") as f:
        st["maps"] = sum(1 for _ in f)
    return st


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", default=default_report())
    ap.add_argument("--dataset", default="Github_hard")
    ap.add_argument("--file", default="o21363.json")
    ap.add_argument("--pass", dest="k", type=int, default=4)
    ap.add_argument("--rounds", type=int, default=12)
    ap.add_argument("--load", type=int, default=0)
    ap.add_argument("--compile-only", action="store_true")
    ap.add_argument("--inorder", action="store_true")
    ap.add_argument("--max-seconds", type=float, default=60.0)
    args = ap.parse_args()

    report, side = h.load_report(args.report)
    snap = h.SNAPSHOTS["maskbench" if report["meta"]["corpus"]["path"].startswith("maskbench")
                       else "repo"]
    rows = side[(args.dataset, args.file)]["byte"]
    sub = (args.dataset,) if snap["layout"] == "raw" else ()
    data, schema_doc, _ = h.load_schema_bytes(
        snap, os.path.join(snap["data_dir"], *sub, args.file))
    gp, _so = h._gen_mods()
    validator, _ = gp.make_reference_validator(data)
    plan = h.pass_plan(h.parsed_schema(data, schema_doc), validator)
    opts = {"passes": len(rows), "max_steps": 8192, "max_seconds": args.max_seconds,
            "schema_budget": 0.0}
    gt = h.GenTokenizer("byte")
    print(json.dumps({"library_sha256": h.library_sha256(), "profile": h.PROFILE,
                      "schema": f"{args.dataset}/{args.file}", "pass": args.k,
                      "load": args.load, "cpus": os.cpu_count()}), flush=True)

    burners = [subprocess.Popen([sys.executable, "-c", "while True: pass"])
               for _ in range(args.load)]
    failures, first = 0, None
    try:
        if args.inorder:
            ctx = h.make_context(gt.spec)
            grammar, failure = h.compile_for(gt, ctx, data, args.dataset, args.file)
            if failure is not None:
                print("compile", failure["code"], failure.get("detail", ""), flush=True)
                ctx.destroy()
                return 1
            same = 0
            for k, row in enumerate(rows, 1):
                rec, _doc = h.gen_pass(gt, ctx, grammar, k, row[5], plan, opts)
                ok = all(rec[f] == row[i] for i, f in enumerate(h.REPLAY_FIELDS))
                same += ok
                print(k, rec["outcome"], rec["stage"], rec["code"], rec["step"],
                      "recorded", row[0], row[3], "=" if ok else "X", flush=True)
            h.blg.grammar_release(grammar)
            ctx.destroy()
            print(json.dumps({"passes": len(rows), "reproduced": same}))
            return 0
        row = rows[args.k - 1]
        for i in range(args.rounds):
            ctx = h.make_context(gt.spec)
            grammar, failure = h.compile_for(gt, ctx, data, args.dataset, args.file)
            used = ctx.get_stats().mem_used
            if failure is not None:
                failures += 1
                first = i if first is None else first
                what = f"compile {failure['code']} {failure.get('detail', '')}"
            elif args.compile_only:
                h.blg.grammar_release(grammar)
                what = "compiled"
            else:
                rec, _doc = h.gen_pass(gt, ctx, grammar, args.k, row[5], plan, opts)
                h.blg.grammar_release(grammar)
                what = (f"{rec['outcome']} {rec['code']} step {rec['step']} "
                        f"(recorded {row[0]} step {row[3]})")
            peak = ctx.get_stats().mem_peak
            st = ctx.destroy()
            print(i, what, "destroy", st, json.dumps(dict(
                proc_state(), peak_total_mib=round(peak[5] / 2**20, 1),
                peak_temp_mib=round(peak[4] / 2**20, 1),
                compiled_grammar_mib=round(used[1] / 2**20, 1),
                compiled_total_mib=round(used[5] / 2**20, 1))), flush=True)
    finally:
        for p in burners:
            p.kill()
            p.wait()
    print(json.dumps({"rounds": args.rounds, "compile_failures": failures,
                      "first_failure": first}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
