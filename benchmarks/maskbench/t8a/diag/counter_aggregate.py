#!/usr/bin/env python3
"""Median and spread of the counter-overhead runs from measure_counters.sh.

Each run leaves a full log and an .exit file, so nothing has to be guessed
from a filtered stream: a traceback stays in the log and a non-zero exit code
is visible here. A run without the expected summary line is reported as a
problem, never skipped silently.

usage: counter_aggregate.py RUNS_DIR [--out PREFIX]
"""
import argparse
import json
import os
import re
import statistics

PER_CALL = re.compile(r"mask_ns_total / mask_calls = ([0-9.]+) ms per call")
COMPILE = re.compile(r"compile ([0-9.]+) s")
EXIT_WALL = re.compile(r"exit (\d+) wall_ms (\d+)")


def read_runs(root):
    rows = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".log"):
            continue
        stem, variant, run = name[: -len(".log")].rsplit(".", 2)
        with open(os.path.join(root, name), encoding="utf-8",
                  errors="replace") as f:
            text = f.read()
        per_call, compile_s = PER_CALL.search(text), COMPILE.search(text)
        code = wall = None
        exit_path = os.path.join(root, name[: -len(".log")] + ".exit")
        if os.path.exists(exit_path):
            with open(exit_path, encoding="utf-8") as f:
                m = EXIT_WALL.match(f.read().strip())
            if m:
                code, wall = int(m.group(1)), int(m.group(2))
        rows.append({"file": stem, "variant": variant, "run": int(run[1:]),
                     "ms_per_call": float(per_call.group(1)) if per_call else None,
                     "compile_s": float(compile_s.group(1)) if compile_s else None,
                     "exit": code, "wall_ms": wall, "log": name})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    rows = read_runs(args.runs)
    problems = [r for r in rows
                if r["exit"] != 0 or r["ms_per_call"] is None or r["wall_ms"] is None]

    groups = {}
    for r in rows:
        groups.setdefault((r["file"], r["variant"]), []).append(r)

    table = {}
    for (stem, variant), rs in sorted(groups.items()):
        values = [r["ms_per_call"] for r in rs if r["ms_per_call"] is not None]
        walls = [r["wall_ms"] for r in rs if r["wall_ms"] is not None]
        if not values:
            continue
        median = statistics.median(values)
        table.setdefault(stem, {})[variant] = {
            "n": len(values),
            "median": round(median, 3),
            "min": round(min(values), 3),
            "max": round(max(values), 3),
            "spread_pct": (round(100 * (max(values) - min(values)) / median, 1)
                           if median else None),
            "wall_median_s": (round(statistics.median(walls) / 1e3, 2)
                              if walls else None),
            "runs": values,
        }

    lines = ["# Counter overhead: atoms on vs off", "",
             "Per corpus file the on/off pair is repeated three times and the order",
             "inside the pair alternates (on-off, off-on, on-off).", "",
             "| file | variant | n | median ms/call | min | max | spread | wall median s |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for stem in sorted(table):
        for variant in ("on", "off"):
            row = table[stem].get(variant)
            if not row:
                continue
            lines.append(f"| {stem} | {variant} | {row['n']} | {row['median']} | "
                         f"{row['min']} | {row['max']} | {row['spread_pct']} % | "
                         f"{row['wall_median_s']} |")

    lines += ["", "| file | on / off | extra us per call |", "|---|---:|---:|"]
    ratios = {}
    for stem in sorted(table):
        on, off = table[stem].get("on"), table[stem].get("off")
        if not on or not off or not off["median"]:
            continue
        ratio = on["median"] / off["median"]
        ratios[stem] = {"ratio": round(ratio, 3),
                        "extra_us": round((on["median"] - off["median"]) * 1e3, 1)}
        lines.append(f"| {stem} | {ratios[stem]['ratio']} | "
                     f"{ratios[stem]['extra_us']:+} |")

    summary = {"runs": len(rows), "problems": problems, "groups": table,
               "ratios": ratios}
    if args.out:
        with open(args.out + ".md", "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        with open(args.out + ".json", "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=1)
    print("\n".join(lines))
    if problems:
        print(f"\nPROBLEMS: {len(problems)} run(s) failed or missed the summary line")
        for p in problems:
            print(f"  {p['file']} {p['variant']} r{p['run']}: exit {p['exit']}, "
                  f"ms_per_call {p['ms_per_call']} -> {p['log']}")
        return 1
    print(f"\nall {len(rows)} runs exited 0 and printed the summary line")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
