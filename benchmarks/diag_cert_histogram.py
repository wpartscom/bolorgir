#!/usr/bin/env python3
"""ADR-0005 D3 undecided-reason histogram over oracle ENGINE_ERROR rows.

Re-runs the ENGINE_ERROR rows of a stored oracle report (default: the
fix3 spec-v1 reports) against the current library and aggregates the
blg_cert_stats histogram the kernel fills on every ResourceLimit:
which frame kinds / certification paths stayed undecided. Rows that no
longer fail are counted separately, so the same script measures the
delta after each new certificate family.

Usage:
    python3 benchmarks/diag_cert_histogram.py [--results FILE ...]
        [--drafts draft7,draft2019-09] [--limit N]

Requires zig-out/lib/libbolorgir.so (tests/blg_ctypes.py loads it).
"""
from __future__ import annotations

import argparse
import ctypes
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tests", "oracle"))

import blg_ctypes as zg  # noqa: E402
import run_oracle as ro  # noqa: E402

# Must match UndReason in src/complete.zig (enum order).
UND_REASONS = [
    "fail_budget0", "fail_search", "fail_oom", "comb_group",
    "openobj_track", "openobj_deps", "openobj_prop_names",
    "openobj_key_bounds", "openobj_min_props", "openobj_max_props",
    "openobj_colon", "openobj_witness", "openobj_keystr", "openobj_keyesc",
    "choice", "seq", "object", "repeat",
    "num_range_sat", "num_const_sat", "int_num_sat",
    "no_nonempty", "openobj_gate", "num_und",
]

zg.LIB.blg_cert_stats.restype = ctypes.c_size_t
zg.LIB.blg_cert_stats.argtypes = [ctypes.POINTER(ctypes.c_uint64),
                                  ctypes.c_size_t]
zg.LIB.blg_cert_stats_reset.restype = None
zg.LIB.blg_cert_stats_reset.argtypes = []


def cert_stats() -> list[int]:
    n = zg.LIB.blg_cert_stats(None, 0)
    buf = (ctypes.c_uint64 * n)()
    zg.LIB.blg_cert_stats(buf, n)
    return list(buf)


def load_error_keys(paths) -> set[tuple]:
    keys = set()
    for p in paths:
        rep = json.load(open(p, "r", encoding="utf-8"))
        for row in rep["rows"]:
            if row["outcome"] == "ENGINE_ERROR":
                keys.add((row["draft"], row["suite_file"], row["case"],
                          row["test_index"]))
    return keys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", nargs="+", default=sorted(glob.glob(
        os.path.join(ROOT, "tests/oracle/results/oracle-fix3-*-spec.json"))),
        help="oracle reports whose ENGINE_ERROR rows are re-run")
    ap.add_argument("--drafts", default=None,
                    help="comma-separated draft keys to restrict to")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    keys = load_error_keys(args.results)
    if args.drafts:
        allow = set(args.drafts.split(","))
        keys = {k for k in keys if k[0] in allow}
    drafts = sorted({k[0] for k in keys})
    print(f"re-running {len(keys)} ENGINE_ERROR rows over drafts {drafts}",
          flush=True)

    value_serializer = ro.load_value_serializer()
    lexeme_parse, _ = ro.make_lexeme_parser(value_serializer)
    registry = ro.build_remote_registry()
    spec = ro.byte_tokenizer_spec()
    ctx = zg.Context(spec, zg.BLG_MODE_LAZY)

    hist = [0] * len(UND_REASONS)
    tallies = {"rows": 0, "still_error": 0, "now_ok": 0,
               "compile_refused": 0, "ser_incompat": 0}
    per_file: dict[str, dict] = {}
    done = set()
    try:
        for case in ro.iter_cases(drafts, lexeme_parse=lexeme_parse):
            wanted = [ti for ti in range(len(case["tests"]))
                      if (case["draft"], case["file"], case["case"], ti)
                      in keys]
            if not wanted:
                continue
            schema_eng, _ = ro.inject_dialect(case["schema"], case["draft"])
            schema_lex = None
            if isinstance(case["schema_lex"], dict):
                schema_lex, _ = ro.inject_dialect(case["schema_lex"],
                                                  case["draft"])
            order_risk = ro.serializer_order_risk(case["schema"],
                                                  case["draft"])
            if schema_lex is not None:
                data, sstat = value_serializer.serialize_value(schema_lex,
                                                               None)
                if not sstat.ok:
                    data = ro.canonical_serialize(schema_eng)
            else:
                data = ro.canonical_serialize(schema_eng)
            grammar, refusal = ro.compile_schema(zg, ctx, data, b"spec-v1",
                                                 registry=registry)
            if refusal is not None:
                tallies["compile_refused"] += len(wanted)
                continue
            for ti in wanted:
                test = case["tests"][ti]
                if not order_risk:
                    doc, sstat = value_serializer.serialize_value(
                        case["tests_lex"][ti]["data"], schema_lex)
                    if not sstat.ok:
                        tallies["ser_incompat"] += 1
                        continue
                else:
                    doc = ro.canonical_serialize(test["data"])
                zg.LIB.blg_cert_stats_reset()
                session = zg.Session(ctx, grammar)
                try:
                    verdict, detail = ro.feed_document(zg, session, doc)
                finally:
                    session.destroy()
                delta = cert_stats()
                hist = [a + b for a, b in zip(hist, delta)]
                tallies["rows"] += 1
                fkey = f"{case['draft']}:{case['file']}"
                pf = per_file.setdefault(fkey, {"rows": 0, "still_error": 0})
                pf["rows"] += 1
                if verdict.startswith("error:"):
                    tallies["still_error"] += 1
                    pf["still_error"] += 1
                else:
                    tallies["now_ok"] += 1
                done.add((case["draft"], case["file"], case["case"], ti))
                if args.limit and tallies["rows"] >= args.limit:
                    break
            if grammar is not None:
                zg.grammar_release(grammar)
            if args.limit and tallies["rows"] >= args.limit:
                break
    finally:
        ctx.destroy()

    missing = keys - done
    print(json.dumps({"tallies": tallies, "missing_rows": len(missing)},
                     indent=1))
    print("\n== undecided-reason histogram (delta over re-run rows) ==")
    for name, v in sorted(zip(UND_REASONS, hist), key=lambda kv: -kv[1]):
        if v:
            print(f"{v:10d}  {name}")
    print("\n== per suite file (still failing) ==")
    for k, v in sorted(per_file.items(), key=lambda kv: -kv[1]["still_error"]):
        print(f"{v['still_error']:4d}/{v['rows']:4d}  {k}")
    if missing:
        print("\n== rows not re-run (suite row not found) ==")
        for k in sorted(missing)[:20]:
            print("   ", k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
