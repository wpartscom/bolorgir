#!/usr/bin/env python3
"""T10 section-10 comparison, report v2 (TZ 6.2/6.3).

Reads one or more raw JSONL files of the comparison collector (`v1` of
2026-10-12 or `v2`; v2 records carry the answer text and the stream end
state) and writes a reproducible summary with complete completion
categories, declared denominators and methods, per-request and
batch-run aggregation, and an independent re-validation of v2 answers.

Counting rules (each request falls into exactly one category; the
report fails on a partition mismatch):

  failed_http_transport   HTTP status != 200 or a transport exception
  unfinished              HTTP 200, no finish_reason received
  stop_invalid            finish stop, answer fails validation
  stop_valid              finish stop, answer passes validation
  length                  finish length (cut by max_tokens; NOT counted
                          as a completed document; a bounded completion
                          check is applied when the text is stored)
  other_finish            any other finish_reason

Timing samples and methods are named in the output: TTFT over HTTP-200
records with a first token; TPOT over records where
(e2e - TTFT)/(tokens - 1) is defined (stop_valid only, tokens > 1);
p90 = statistics.quantiles(n=10)[8] when n >= 10, otherwise the maximum,
named as such. Batch-run rows count a run (session, repeat) only when
it has no failed request and no invalid stop, like the historical
report; the report lists excluded runs and reasons. `length` records
stay in all samples where their fields are defined; they are never
silently dropped.

Usage:
  python xgrammar_report.py RAW... --out-json J --out-md M [--title T]
"""

import argparse
import datetime
import glob
import hashlib
import json
import os
import statistics
import sys

from xgrammar_common import completion_check, validate


def load_records(paths):
    recs = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    recs.append(json.loads(line))
    return recs


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def finish_present(rec):
    fp = rec.get("finish_present")
    if fp is None:
        fp = rec.get("finish") is not None
    return fp


def category(rec):
    """The single completion category of a record (rules in the docstring)."""
    if rec.get("transport_error") or rec.get("status") != 200:
        return "failed_http_transport"
    if not finish_present(rec):
        return "unfinished"
    if rec.get("finish") == "stop":
        return "stop_valid" if rec.get("valid") else "stop_invalid"
    if rec.get("finish") == "length":
        return "length"
    return "other_finish"


CATEGORIES = ("failed_http_transport", "unfinished", "stop_invalid", "stop_valid",
              "length", "other_finish")


def partition(recs):
    counts = dict.fromkeys(CATEGORIES, 0)
    for r in recs:
        counts[category(r)] += 1
    return counts


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 4) if xs else None


def p90(xs):
    """p90 method: statistics.quantiles(n=10)[8] (exclusive, the Python
    default) when n >= 10; otherwise the maximum, labeled 'max(n<10)'."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return None, "n=0"
    if len(xs) >= 10:
        return round(statistics.quantiles(xs, n=10)[8], 4), f"q90(n={len(xs)})"
    return round(max(xs), 4), f"max(n={len(xs)})"


def run_rows(recs):
    """Batch runs: (session, repeat, workload, batch) -> row."""
    runs = {}
    for r in recs:
        k = (r["session"], r["repeat"], r["workload"], r["batch"])
        runs.setdefault(k, []).append(r)
    out = []
    for k, rs in sorted(runs.items()):
        cats = partition(rs)
        bad = cats["failed_http_transport"] or cats["stop_invalid"]
        toks = sum((r.get("tokens") or 0) for r in rs)
        wall = max((r.get("wall_s") or 0) for r in rs)
        out.append({"session": k[0], "repeat": k[1], "workload": k[2], "batch": k[3],
                    "requests": len(rs), "included": not bad,
                    "exclusion": ("failed request" if cats["failed_http_transport"]
                                  else "invalid stop" if cats["stop_invalid"] else None),
                    "tokens": toks, "wall_s": wall,
                    "tok_per_s": (toks / wall if wall and not bad else None),
                    "stop_valid_per_s": (cats["stop_valid"] / wall if wall and not bad else None),
                    "stop_valid_share": (cats["stop_valid"] / len(rs) if rs else None)})
    return out


def revalidate(recs):
    """Independent re-validation from stored text (v2 records)."""
    out = {"with_text": 0, "without_text": 0, "valid_agree": 0, "valid_differ": [],
           "length_checked": 0, "length_completable": 0, "length_completable_suffixes": [],
           "length_not_completable": 0, "differ_examples": []}
    for r in recs:
        if "text" not in r or r.get("text") is None:
            out["without_text"] += 1
            continue
        out["with_text"] += 1
        if r.get("status") == 200:
            ok, why = validate(r["workload"], r["text"])
            if ok == bool(r.get("valid")):
                out["valid_agree"] += 1
            else:
                out["valid_differ"].append({"workload": r["workload"], "status": r["status"],
                                            "stored": r.get("valid"), "revalidated": ok})
                if len(out["differ_examples"]) < 5:
                    out["differ_examples"].append({"workload": r["workload"], "reason": why[:120],
                                                   "text_len": r.get("text_len")})
        if r.get("finish") == "length":
            out["length_checked"] += 1
            okc, suffix = completion_check(r["workload"], r["text"])
            if okc:
                out["length_completable"] += 1
                if len(out["length_completable_suffixes"]) < 10:
                    out["length_completable_suffixes"].append(
                        {"workload": r["workload"], "suffix": suffix, "text_len": r.get("text_len")})
            else:
                out["length_not_completable"] += 1
    return out


def cell_summary(recs):
    cats = partition(recs)
    n = len(recs)
    total = sum(cats.values())
    rec = {
        "requests": n, "categories": cats,
        "partition_ok": total == n,
        "usage_missing": sum(1 for r in recs if not r.get("usage_present",
                                                           r.get("tokens") is not None)),
        "final_event_missing": sum(1 for r in recs if not r.get("final_event", True)),
    }
    ttft = [r.get("ttft_ms") for r in recs if r.get("status") == 200 and r.get("ttft_ms") is not None]
    tpot = [r.get("tpot_ms") for r in recs if r.get("tpot_ms") is not None]
    e2e = [r.get("e2e_ms") for r in recs if r.get("status") == 200 and r.get("e2e_ms") is not None]
    rec["ttft_ms_median"], rec["ttft_n"] = med(ttft), len(ttft)
    rec["tpot_ms_median"], rec["tpot_n"] = med(tpot), len(tpot)
    rec["tpot_excluded"] = n - len(tpot)
    rec["e2e_ms_median"], rec["e2e_n"] = med(e2e), len(e2e)
    rec["ttft_ms_p90"], rec["ttft_p90_method"] = p90(ttft)
    rec["tpot_ms_p90"], rec["tpot_p90_method"] = p90(tpot)
    runs = run_rows(recs)
    inc = [r for r in runs if r["included"]]
    rec["runs"] = len(runs)
    rec["runs_included"] = len(inc)
    rec["runs_excluded"] = [{"session": r["session"], "repeat": r["repeat"],
                             "reason": r["exclusion"]} for r in runs if not r["included"]]
    rec["tok_per_s_median"] = med([r["tok_per_s"] for r in inc])
    rec["stop_valid_per_s_median"] = med([r["stop_valid_per_s"] for r in inc])
    rec["stop_valid_share"] = (cats["stop_valid"] / n) if n else None
    rec["cold_warm"] = {}
    for rep in sorted({r["repeat"] for r in recs}):
        part = [r for r in recs if r["repeat"] == rep]
        rec["cold_warm"][f"rep{rep}"] = {
            "requests": len(part),
            "ttft_ms_median": med([r.get("ttft_ms") for r in part if r.get("status") == 200]),
            "tpot_ms_median": med([r.get("tpot_ms") for r in part]),
        }
    return rec


def build_report(paths):
    recs = load_records(paths)
    backends = sorted({r["backend"] for r in recs})
    workloads = sorted({r["workload"] for r in recs})
    batches = sorted({r["batch"] for r in recs})
    cells = {}
    problems = []
    for b in backends:
        for w in workloads:
            for bs in batches:
                cr = [r for r in recs if r["backend"] == b and r["workload"] == w
                      and r["batch"] == bs]
                if not cr:
                    continue
                s = cell_summary(cr)
                if not s["partition_ok"]:
                    problems.append(f"partition mismatch {b}|{w}|{bs}")
                cells[f"{b}|{w}|{bs}"] = s
    doc = {
        "about": "T10 section-10 comparison summary v2 (complete completion categories, "
                 "declared denominators; see the method block)",
        "method": {
            "categories": "each request exactly one of " + ", ".join(CATEGORIES),
            "ttft": "seconds from send to the first content delta; median over HTTP-200 records "
                    "with a first token (n stated)",
            "tpot": "(e2e - ttft)/(completion_tokens - 1), client-observed per-token time "
                    "including server overheads, NOT a profiled mask cost; median over records "
                    "where it is defined (stop_valid only, tokens > 1; n and exclusions stated)",
            "throughput": "completion tokens / batch wall time, median over batch runs with no "
                          "failed request and no invalid stop (run counts stated)",
            "stop_valid_per_s": "stop_valid documents / batch wall time, median over the same runs",
            "p90": "statistics.quantiles(n=10)[8] when n >= 10, otherwise the maximum, "
                   "labeled max(n<10)",
            "cold_warm": "repeat 0 against the remaining repeats, both shown",
            "length_cuts": "kept in every sample where their fields are defined; not counted as "
                           "completed documents; a bounded completion check is applied when the "
                           "text is stored (declared suffix list, not a proof)",
        },
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sessions": sorted({r["session"] for r in recs}),
        "records": len(recs),
        "inputs": [{"path": p, "sha256": sha256(p)} for p in paths],
        "tool": {"path": os.path.abspath(__file__), "sha256": sha256(os.path.abspath(__file__))},
        "revalidation": revalidate(recs),
        "problems": problems,
        "cells": cells,
    }
    return doc


def write_md(doc, out_md, title):
    md = [f"# {title}", "",
          f"Generated {doc['generated_utc']} by `xgrammar_report.py` "
          f"(sha256 `{doc['tool']['sha256'][:16]}…`) from: "
          + ", ".join(f"`{os.path.basename(i['path'])}` (`{i['sha256'][:16]}…`)"
                      for i in doc["inputs"]) + ".", "",
          "Method (denominators and rules; abbreviated): " + "; ".join(
              f"**{k}**: {v}" for k, v in doc["method"].items()) + ".", ""]
    backends = sorted({k.split("|")[0] for k in doc["cells"]})
    workloads = sorted({k.split("|")[1] for k in doc["cells"]})
    for w in workloads:
        md += [f"## {w}", "",
               "| batch | backend | req | failed | unfinished | stop_valid | stop_invalid | "
               "length | other | usage_missing | TTFT p50 (n) | TPOT p50 (n) | TPOT p90 (method) | "
               "tok/s (runs) | stop_valid/s | stop_valid share |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for bs in sorted({int(k.split("|")[2]) for k in doc["cells"] if k.split("|")[1] == w}):
            for b in backends:
                c = doc["cells"].get(f"{b}|{w}|{bs}")
                if not c:
                    continue
                cats = c["categories"]
                md.append(
                    f"| {bs} | {b} | {c['requests']} | {cats['failed_http_transport']} | "
                    f"{cats['unfinished']} | {cats['stop_valid']} | {cats['stop_invalid']} | "
                    f"{cats['length']} | {cats['other_finish']} | {c['usage_missing']} | "
                    f"{c['ttft_ms_median']} ({c['ttft_n']}) | {c['tpot_ms_median']} ({c['tpot_n']}) | "
                    f"{c['tpot_ms_p90']} ({c['tpot_p90_method']}) | {c['tok_per_s_median']} "
                    f"({c['runs_included']}/{c['runs']}) | {c['stop_valid_per_s_median']} | "
                    f"{c['stop_valid_share']:.3f} |" if c["stop_valid_share"] is not None else "|")
        md.append("")
    rv = doc["revalidation"]
    md += ["## Independent re-validation and limits", "",
           f"Records with stored text: {rv['with_text']}; without: {rv['without_text']}; "
           f"stored-vs-revalidated agreement: {rv['valid_agree']} "
           f"(differences: {len(rv['valid_differ'])}).",
           f"Length cuts checked: {rv['length_checked']}; completable by the bounded suffix "
           f"test: {rv['length_completable']}; not: {rv['length_not_completable']}.",
           "The v1 raw files of 2026-10-12 store no answer text; their validity cannot be "
           "re-checked and their `length` cuts cannot be called legal partials from the stored "
           "data (the earlier A1 observation is recorded in PROTOCOL.md, not re-verifiable).", ""]
    if doc["problems"]:
        md += ["Problems:", ""] + [f"- {p}" for p in doc["problems"]]
        md.append("")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("raw", nargs="+")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--title", default="XGrammar comparison (T10 section 10)")
    args = ap.parse_args()
    paths = []
    for p in args.raw:
        paths.extend(sorted(glob.glob(p)))
    doc = build_report(paths)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
    write_md(doc, args.out_md, args.title)
    print(f"{doc['records']} records, {len(doc['cells'])} cells -> {args.out_json}, {args.out_md}")
    return 1 if doc["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
