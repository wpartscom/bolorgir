#!/usr/bin/env python3
"""Compare the VE replays: the run's wheel vs the corrected candidate.

Both sides use the fixed meter of mask_equiv.py: the duration of a call that
raised (the mask-limit refusal) is included in mask_s and in the TBM
percentiles. The comparison is strict: it fails unless both legs cover the
same files, instances and calls, refuse the same number of times, run no
capped file and report no error outside the two expected compile-limit cases.

usage: compare_ve_meters.py ORIG.json FIX.json [--out PREFIX]
                           [--allow-abort-diff FILE ...]
                           [--expect-orig-lib SHA256] [--expect-fix-lib SHA256]
"""
import argparse
import json

# The grammar node budget is exceeded in both legs: a property of the corpus,
# not a difference between the two cores, so these errors are named apart.
EXPECTED_COMPILE_LIMIT = {
    "Github_easy---o58839.json",
    "Github_hard---o90895.json",
}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Checks:
    """Collects named assertions, so one failure never hides the rest."""

    def __init__(self):
        self.rows = []
        self.ok = True

    def check(self, name, cond, detail=""):
        self.rows.append({"check": name,
                          "result": "ok" if cond else "FAIL",
                          "detail": detail})
        self.ok = self.ok and bool(cond)
        return bool(cond)


def split_errors(res):
    """Separate the corpus compile limits from anything unexpected."""
    expected, unexpected = [], []
    for err in res.get("errors", []):
        name, why = err.get("file", ""), err.get("why", "")
        if name in EXPECTED_COMPILE_LIMIT and why.startswith(
                "compile failed: ResourceLimitError"):
            expected.append(name)
        else:
            unexpected.append(err)
    return expected, unexpected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("orig")
    ap.add_argument("fix")
    ap.add_argument("--out", default="")
    ap.add_argument("--allow-abort-diff", action="append", default=[],
                    metavar="FILE",
                    help="file whose refusal count may differ between the legs")
    ap.add_argument("--expect-orig-lib", default=None, metavar="SHA256")
    ap.add_argument("--expect-fix-lib", default=None, metavar="SHA256")
    args = ap.parse_args()
    o, f = load(args.orig), load(args.fix)
    of = {r["file"]: r for r in o["per_file"]}
    ff = {r["file"]: r for r in f["per_file"]}
    shared = sorted(set(of) & set(ff))

    checks = Checks()
    checks.check("file sets equal", set(of) == set(ff),
                 f"orig {len(of)}, fix {len(ff)}; only orig "
                 f"{sorted(set(of) - set(ff))}, only fix "
                 f"{sorted(set(ff) - set(of))}")
    checks.check("files field matches the table",
                 o["files"] == f["files"] == len(of) == len(ff),
                 f"{o['files']} / {f['files']} / {len(of)} / {len(ff)}")

    bad = {n: [of[n].get("instances"), ff[n].get("instances")] for n in shared
           if of[n].get("instances") != ff[n].get("instances")}
    checks.check("instance counts equal", not bad, json.dumps(bad))

    bad = {n: [of[n].get("calls"), ff[n].get("calls")] for n in shared
           if of[n].get("calls") != ff[n].get("calls")}
    checks.check("call counts equal", not bad, json.dumps(bad))
    checks.check("call total matches the table",
                 o.get("calls") == f.get("calls")
                 == sum(r.get("calls", 0) for r in of.values())
                 == sum(r.get("calls", 0) for r in ff.values()),
                 f"{o.get('calls')} / {f.get('calls')}")

    bad = {n: [of[n].get("aborts", 0), ff[n].get("aborts", 0)] for n in shared
           if of[n].get("aborts", 0) != ff[n].get("aborts", 0)
           and n not in args.allow_abort_diff}
    checks.check("refusal counts equal", not bad, json.dumps(bad))

    bad = {n: [len(of[n].get("mismatches", [])), len(ff[n].get("mismatches", []))]
           for n in shared
           if of[n].get("mismatches") or ff[n].get("mismatches")}
    checks.check("no stream mismatches",
                 not bad and o.get("mismatched_files", 0) == 0
                 and f.get("mismatched_files", 0) == 0, json.dumps(bad))

    checks.check("no capped runs", not o.get("capped") and not f.get("capped"),
                 json.dumps({"orig": o.get("capped"), "fix": f.get("capped")}))

    exp_o, unexp_o = split_errors(o)
    exp_f, unexp_f = split_errors(f)
    checks.check("no unexpected errors", not unexp_o and not unexp_f,
                 json.dumps({"orig": unexp_o[:5], "fix": unexp_f[:5]}))
    checks.check("expected compile limits match",
                 set(exp_o) == set(exp_f) == EXPECTED_COMPILE_LIMIT,
                 f"orig {sorted(exp_o)}, fix {sorted(exp_f)}")

    tbm_keys = {"p50", "p75", "p90", "p99",
                "share_calls_ge_1s", "share_time_ge_1s"}
    checks.check("TBM percentiles present",
                 tbm_keys <= set(o.get("tbm_ms", {}))
                 and tbm_keys <= set(f.get("tbm_ms", {})),
                 json.dumps({"orig": sorted(o.get("tbm_ms", {})),
                             "fix": sorted(f.get("tbm_ms", {}))}))

    lib_o = (o.get("library") or {}).get("sha256")
    lib_f = (f.get("library") or {}).get("sha256")
    checks.check("library hash recorded on both sides", bool(lib_o and lib_f),
                 json.dumps({"orig": lib_o, "fix": lib_f}))
    checks.check("legs loaded different libraries", lib_o != lib_f,
                 json.dumps({"orig": lib_o, "fix": lib_f}))
    if args.expect_orig_lib:
        checks.check("orig library is the run wheel", lib_o == args.expect_orig_lib,
                     str(lib_o))
    if args.expect_fix_lib:
        checks.check("fix library is the checkpoint", lib_f == args.expect_fix_lib,
                     str(lib_f))

    rows = []
    for name in shared:
        a, b = of[name], ff[name]
        rows.append({
            "file": name,
            "instances": a.get("instances"),
            "calls": a["calls"],
            "orig_mask_s": round(a["mask_us"] / 1e6, 2),
            "orig_abort_s": round(a.get("abort_us", 0) / 1e6, 2),
            "orig_aborts": a.get("aborts", 0),
            "fix_mask_s": round(b["mask_us"] / 1e6, 2),
            "fix_abort_s": round(b.get("abort_us", 0) / 1e6, 2),
            "fix_aborts": b.get("aborts", 0),
            "delta_s": round((b["mask_us"] - a["mask_us"]) / 1e6, 2),
            "mismatches": len(b["mismatches"]),
        })
    total = {
        "orig_mask_s": o["mask_s"], "orig_abort_s": o.get("abort_s", 0),
        "orig_wall_s": o.get("wall_s"),
        "fix_mask_s": f["mask_s"], "fix_abort_s": f.get("abort_s", 0),
        "fix_wall_s": f.get("wall_s"),
        "calls": o["calls"], "aborts": [o.get("aborts", 0), f.get("aborts", 0)],
        "mismatched_files": f["mismatched_files"],
        "orig_tbm": o["tbm_ms"], "fix_tbm": f["tbm_ms"],
        "libraries": [lib_o, lib_f],
        "expected_compile_limit": sorted(EXPECTED_COMPILE_LIMIT),
    }

    lines = ["# VE replays: run wheel vs corrected candidate", "",
             "Both sides include the duration of the refusing call. All checks",
             "below must pass for the timings to mean anything.", "",
             "| check | result | detail |", "|---|---|---|"]
    for row in checks.rows:
        detail = (row["detail"] or "").replace("|", "\\|")
        lines.append(f"| {row['check']} | {row['result']} | {detail} |")
    lines += ["", "| file | instances | calls | orig mask s | orig aborts | "
                  "fix mask s | fix aborts | delta s |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(f"| {r['file']} | {r['instances']} | {r['calls']} | "
                     f"{r['orig_mask_s']} | {r['orig_abort_s']} "
                     f"({r['orig_aborts']}) | {r['fix_mask_s']} | "
                     f"{r['fix_abort_s']} ({r['fix_aborts']}) | "
                     f"{r['delta_s']:+} |")
    lines += ["",
              f"Totals: orig {total['orig_mask_s']} s of masks "
              f"({total['orig_abort_s']} s in {total['aborts'][0]} refusals, "
              f"wall {total['orig_wall_s']} s), fix {total['fix_mask_s']} s "
              f"({total['fix_abort_s']} s in {total['aborts'][1]} refusals, "
              f"wall {total['fix_wall_s']} s); mismatched files "
              f"{total['mismatched_files']}.",
              "",
              f"Libraries: orig `{lib_o}`, fix `{lib_f}`.", "",
              f"TBM orig: {json.dumps(total['orig_tbm'], sort_keys=True)}",
              f"TBM fix:  {json.dumps(total['fix_tbm'], sort_keys=True)}", "",
              "VERDICT: " + ("all checks passed" if checks.ok
                             else "CHECKS FAILED - timings are not comparable")]
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        with open(args.out + ".md", "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(args.out + ".json", "w", encoding="utf-8") as fh:
            json.dump({"checks": checks.rows, "rows": rows, "total": total,
                       "ok": checks.ok}, fh, indent=1)
    return 0 if checks.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
