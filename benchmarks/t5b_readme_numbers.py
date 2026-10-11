#!/usr/bin/env python3
"""README numbers for the T5b gate, from the saved reports only.

Reads the T3 acceptance verdict (benchmarks/t3/tools/acceptance_compare.py
--out-json, as written by benchmarks/t3/r6_publish.sh into
<label>/review/acceptance.json) and the T8a report
(benchmarks/maskbench/t8a/t8a_report.py --out-json) and prints a Markdown
fragment: the generatable share per corpus and tokenizer as the main
spec-v1 coverage number, and the MaskBench spec-v1 rows of every engine,
on the full corpus and on the intersection of compiled schemas. Every
number names the file it comes from; the fragment is pasted into
README.md by hand and cited in docs/RUN_HISTORY.md.

Release numbers need an `admitted` T3 verdict, an
accepted T8a report, and the same core in both (the T3 candidate's
library sha256 equals the T8a core). Otherwise the script prints the
reasons and exits 1. --preview prints the fragment anyway under a
heading that names it preliminary and never calls the build accepted.

  python3 benchmarks/t5b_readme_numbers.py \\
      --acceptance benchmarks/coverage/spec-v1/<label>/review/acceptance.json \\
      --t8a benchmarks/maskbench/results/<run>/t8a.json [--preview]
"""

import argparse
import json
import os
import sys

ROWS = ("TTFM p50", "TTFM p99", "TBM p50", "TBM p99", "passing", "compile error",
        "validation error", "invalidation error")


def rel(path):
    return os.path.relpath(os.path.abspath(path))


def gate(acc, t8a):
    """Reasons the reports cannot give release numbers (empty: they can)."""
    out = []
    verdict = (acc.get("acceptance") or {}).get("verdict")
    if verdict != "admitted":
        failing = (acc.get("acceptance") or {}).get("failing") or []
        out.append(f"T3 verdict {verdict!r}" + (f" (failing: {', '.join(failing[:5])})"
                                                 if failing else ""))
    t = t8a.get("t8a") or {}
    if not t.get("accepted"):
        out.append("T8a not accepted: " + "; ".join((t.get("problems") or [])
                                                     + (t.get("conditions") or []))[:300])
    cand = ((acc.get("inputs") or {}).get("candidate") or {}).get("library_sha256")
    if not cand or cand != t8a.get("core_sha256"):
        out.append(f"T3 candidate {str(cand)[:16]} and T8a core "
                   f"{str(t8a.get('core_sha256'))[:16]} differ")
    return out


def coverage_rows(acc, path, preview):
    cand = acc["inputs"]["candidate"]
    label = "candidate" if preview else "release build"
    out = [f"spec-v1 coverage of the {label} (`{cand['name']}`, lib "
           f"`{str(cand['library_sha256'])[:16]}`, `{rel(path)}`): share of compiled",
           "schemas whose K = 20 passes reach a valid document (`generatable`).", "",
           "| corpus | tokenizer | generatable | base |", "|---|---|---:|---:|"]
    for snap, builds in sorted(acc["totals"].items()):
        if cand["name"] not in builds:
            continue
        for tok, o in sorted(builds[cand["name"]]["outcomes"].items()):
            if not o["base"]:
                continue
            pct = 100.0 * o["generatable"] / o["base"]
            sample = "" if tok == "byte" else " (1-in-20 sample)"
            out.append(f"| {snap} | {tok}{sample} | {o['generatable']:,} ({pct:.2f}%) | "
                       f"{o['base']:,} |")
    return out


def maskbench_rows(t8a, path):
    names = list(t8a["full"].keys())
    out = ["", f"MaskBench, spec-v1, Llama 3.1 tokenizer, same token streams (`{rel(path)}`;",
           "microseconds):", "", "| metric | " + " | ".join(names) + " |",
           "|---|" + "---:|" * len(names)]
    for k in ROWS:
        out.append(f"| {k} | " + " | ".join(str(t8a["full"][n].get(k, "-")) for n in names) + " |")
    inter = t8a["intersection"]
    out += ["", f"On the {inter['schemas']:,} schemas every engine compiled:", "",
            "| metric | " + " | ".join(names) + " |", "|---|" + "---:|" * len(names)]
    for k in ROWS[:4]:
        out.append(f"| {k} | " + " | ".join(str(inter["stats"][n].get(k, "-")) for n in names)
                   + " |")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--acceptance", required=True)
    ap.add_argument("--t8a", required=True)
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args(argv)
    with open(args.acceptance, encoding="utf-8") as f:
        acc = json.load(f)
    with open(args.t8a, encoding="utf-8") as f:
        t8a = json.load(f)
    reasons = gate(acc, t8a)
    if reasons and not args.preview:
        print("t5b: no release numbers from these reports:", file=sys.stderr)
        for r in reasons:
            print(f"  - {r}", file=sys.stderr)
        return 1
    lines = []
    if reasons:
        lines = ["**PRELIMINARY - not release numbers.** The reports do not meet the T5b gate:", ""]
        lines += [f"- {r}" for r in reasons] + [""]
    lines += coverage_rows(acc, args.acceptance, bool(reasons)) + maskbench_rows(t8a, args.t8a)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
