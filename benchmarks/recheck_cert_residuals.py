#!/usr/bin/env python3
"""Maintained recheck of the strict-D3 RESOURCE_LIMIT residuals on MaskBench.

Consolidates the two one-off investigation scripts of the fix3 wave
(report benchmarks/reports/20260929T_fix3_adr0007/REPORT.md) into a single
documented entry point:

  cases26        the 26 engine-suspect MaskBench cases (schema file, test
                 index): compile with the spec-v1 profile, serialize the
                 corpus instance with the value-preserving serializer and
                 feed it byte-exactly through the engine (lazy mode, mask
                 cache disabled, byte-level synthetic tokenizer). Expected
                 residual of the fix3 build, pinned per case below:
                 13 accept + 1 reject (the single invalid document,
                 Github_medium---o55595.json test 1) + 12 RESOURCE_LIMIT.
                 Saved evidence: recheck_out.log beside the fix3 report.

  valid-rejects  all valid instances of the MaskBench files whose valid
                 instances were rejected in the spec-v1 semantic coverage
                 report (74 files, 126 instances). Expected residual of the
                 fix3 build: 75 accept / 50 RESOURCE_LIMIT / 1 reject (the
                 single reject is the o55595 serializer key-order defect,
                 not an engine defect). Saved evidence:
                 recheck_valid_rejects_out.jsonl beside the fix3 report.

The pins above are a ratchet like tests/oracle/engine_error_residual_spec_v1.json:
when a new certificate family lands, the RESOURCE_LIMIT counts must shrink and
this file's pins are updated deliberately in the same commit; growth is a
regression. With --expect (the default) every row is additionally diffed
case-by-case against the saved evidence logs (outcome and detail; timings
are measurement facts and are not compared).

Inputs (all explicit; nothing is read from /tmp or personal directories):

  - library: --lib PATH, else the BLG_LIB_PATH environment variable, else
    zig-out/lib/libbolorgir.so.0 (relative to the repository root);
  - corpus: the MaskBench snapshot of JSONSchemaBench at
    benchmarks/external/jsonschemabench-maskbench/maskbench/data/
    (fetch instructions in bench_jsb_coverage.py; override with
    --corpus-dir);
  - coverage report for the valid-rejects suite:
    benchmarks/coverage/spec-v1/coverage_maskbench.json
    (override with --coverage-report).

Usage (from anywhere; paths resolve against the repository root):

    python3 benchmarks/recheck_cert_residuals.py                 # both suites
    python3 benchmarks/recheck_cert_residuals.py --suite cases26
    python3 benchmarks/recheck_cert_residuals.py --suite valid-rejects \
        --lib /path/to/libbolorgir.so.0
    python3 benchmarks/recheck_cert_residuals.py --no-expect     # pins only

Output: one JSONL row per case/instance (same shape as the saved evidence),
then a JSON summary per suite. Rows carry "ok": whether the row matches its
pin. The valid-rejects summary reports two file counts: "files" is the number
of target files selected from the coverage report (before --limit-files is
applied) and "files_processed" is the number of target files this run
actually entered (after the limit); a file whose compilation fails still
counts as processed, since the field counts attempts, not successes. Only
"files" participates in the aggregate pin; "files_processed" is run-scope
metadata. Exit status: 0 when every pinned expectation holds (and the
--expect diff is clean), 1 on any mismatch or compile error, 2 on missing
input.
"""

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

DEFAULT_LIB = os.path.join(ROOT, "zig-out", "lib", "libbolorgir.so.0")
DEFAULT_COVERAGE_REPORT = os.path.join(
    HERE, "coverage", "spec-v1", "coverage_maskbench.json")
EVIDENCE_DIR = os.path.join(HERE, "reports", "20260929T_fix3_adr0007")
DEFAULT_EXPECT_CASES26 = os.path.join(EVIDENCE_DIR, "recheck_out.log")
DEFAULT_EXPECT_VALID_REJECTS = os.path.join(
    EVIDENCE_DIR, "recheck_valid_rejects_out.jsonl")

# The 26 engine-suspect MaskBench cases with the pinned fix3-build outcome:
# (file, test index, outcome, detail). 13 accept + 1 reject (o55595 test 1,
# the invalid document) + 12 RESOURCE_LIMIT.
CASES26 = [
    ("JsonSchemaStore---aiproj-1.1.json", 0, "accept", ""),
    ("JsonSchemaStore---aiproj-1.1.json", 1, "accept", ""),
    ("Github_medium---o55595.json", 1, "reject", "byte 70 (0x22) not in mask"),
    ("Github_hard---o77317.json", 0, "accept", ""),
    ("Github_ultra---o358.json", 0, "accept", ""),
    ("Github_ultra---o358.json", 1, "accept", ""),
    ("Github_ultra---o360.json", 0, "accept", ""),
    ("Github_ultra---o360.json", 1, "error:RESOURCE_LIMIT",
     "fill_mask at byte 129"),
    ("Github_ultra---o17072.json", 0, "error:RESOURCE_LIMIT",
     "fill_mask at byte 84"),
    ("Github_ultra---o17072.json", 1, "error:RESOURCE_LIMIT",
     "fill_mask at byte 84"),
    ("JsonSchemaStore---v4-config.schema.json", 0, "error:RESOURCE_LIMIT",
     "fill_mask at byte 177"),
    ("JsonSchemaStore---v4-config.schema.json", 1, "error:RESOURCE_LIMIT",
     "fill_mask at byte 557"),
    ("Github_hard---o1184.json", 0, "accept", ""),
    ("Github_ultra---o58926.json", 0, "accept", ""),
    ("Github_hard---o21819.json", 0, "accept", ""),
    ("Github_hard---o21819.json", 1, "accept", ""),
    ("Github_hard---o84330.json", 0, "accept", ""),
    ("Github_hard---o84330.json", 1, "accept", ""),
    ("Github_medium---o35868.json", 0, "error:RESOURCE_LIMIT",
     "fill_mask at byte 207"),
    ("Github_medium---o35868.json", 1, "error:RESOURCE_LIMIT",
     "fill_mask at byte 220"),
    ("JsonSchemaStore---github-issue-forms.json", 0, "error:RESOURCE_LIMIT",
     "fill_mask at byte 89"),
    ("JsonSchemaStore---minecraft-biome.json", 0, "error:RESOURCE_LIMIT",
     "fill_mask at byte 429"),
    ("JsonSchemaStore---minecraft-biome.json", 1, "error:RESOURCE_LIMIT",
     "fill_mask at byte 429"),
    ("Github_hard---o17529.json", 0, "error:RESOURCE_LIMIT",
     "fill_mask at byte 141"),
    ("Github_hard---o17529.json", 1, "error:RESOURCE_LIMIT",
     "fill_mask at byte 141"),
    ("JsonSchemaStore---discovery.schema.json", 0, "accept", ""),
]

# Pinned aggregate of the valid-rejects suite on the fix3 build.
EXPECTED_VALID_REJECTS = {
    "files": 74,
    "valid_instances_rechecked": 126,
    "summary": {"accept": 75, "error:RESOURCE_LIMIT": 50, "reject": 1},
}


def fail_input(msg):
    print(f"recheck_cert_residuals: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_engine(lib_path):
    """Import the ctypes layer and the coverage harness after the library
    path is fixed (both resolve BLG_LIB_PATH at import time)."""
    if not os.path.exists(lib_path):
        fail_input(f"library not found: {lib_path} "
                   "(use --lib or set BLG_LIB_PATH)")
    os.environ["BLG_LIB_PATH"] = lib_path
    profile = os.environ.get("BOLORGIR_COVERAGE_PROFILE", "spec-v1")
    if profile != "spec-v1":
        fail_input(f"unsupported BOLORGIR_COVERAGE_PROFILE={profile!r}: "
                   "the pins and saved evidence of this recheck are for "
                   "spec-v1 only; unset the variable to run")
    os.environ["BOLORGIR_COVERAGE_PROFILE"] = profile
    sys.path[:0] = [os.path.join(ROOT, "tests"),
                    os.path.join(ROOT, "python"), HERE]
    import blg_ctypes as blg
    import bench_jsb_coverage as b
    return blg, b


def load_expect_cases26(path):
    """-> {(file, test): (outcome, detail)} from a recheck_out.log-style
    evidence file (JSONL rows plus a trailing 'BAD=n of 26' line)."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            row = json.loads(line)
            out[(row["file"], row["test"])] = (row["outcome"], row["detail"])
    return out


def load_expect_valid_rejects(path):
    """-> sorted list of (file, doc, outcome, detail) evidence rows
    (the summary line is skipped). Compared as multisets: the doc field is
    a 200-character prefix, so (file, doc) is not guaranteed unique."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if "summary" in row:
                continue
            rows.append((row["file"], row["doc"], row["outcome"],
                         row["detail"]))
    return sorted(rows)


def diff_rows(got, want, key_of, label):
    """Case-by-case diff of (outcome, detail) between the run and the
    saved evidence. Returns the number of mismatches; prints one line per
    mismatch to stderr."""
    mismatches = 0
    for key in sorted(set(got) | set(want)):
        g, w = got.get(key), want.get(key)
        if g == w:
            continue
        mismatches += 1
        print(f"EXPECT-DIFF [{label}] {key_of(key)}: "
              f"evidence={w} run={g}", file=sys.stderr)
    return mismatches


def run_cases26(blg, b, snapshot, expect_path, limit=0):
    serializer = b.load_value_serializer()
    ctx = blg.Context(b.byte_tokenizer_spec(), mode=0, cache_limit_bytes=0)
    files = {fn: path for _ds, fn, path in b.iter_files(snapshot)}
    rows = {}
    n_bad = 0
    cases = CASES26[:limit] if limit else CASES26
    try:
        for fn, tidx, pin_outcome, pin_detail in cases:
            path = files.get(fn)
            if path is None:
                fail_input(f"corpus file missing: {fn} "
                           "(check --corpus-dir)")
            data, schema_doc, tests = b.load_schema_bytes(snapshot, path)
            t0 = time.time()
            try:
                g = blg.compile_schema(ctx, data,
                                       profile=b.PROFILE.encode("ascii"))
            except blg.CoreFailure as e:
                print(json.dumps({"file": fn, "test": tidx,
                                  "outcome": "compile_error",
                                  "detail": str(e)}, ensure_ascii=False))
                n_bad += 1
                continue
            t = tests[tidx]
            valid = bool(t.get("valid", True))
            payload, sstat = serializer.serialize_value(t.get("data"),
                                                        schema_doc)
            if not sstat.ok:
                outcome = "serialization_incompatible"
                detail = f"{sstat.reason} at {sstat.pointer or '/'}"
            else:
                s = blg.Session(ctx, g)
                try:
                    outcome, detail = b.feed_document(s, payload)
                finally:
                    s.destroy()
            dt = time.time() - t0
            ok = (outcome, detail) == (pin_outcome, pin_detail)
            row = {"file": fn, "test": tidx, "valid": valid,
                   "outcome": outcome, "detail": detail,
                   "ms": round(dt * 1000), "ok": ok}
            print(json.dumps(row, ensure_ascii=False), flush=True)
            rows[(fn, tidx)] = (outcome, detail)
            if not ok:
                n_bad += 1
                print(f"PIN-MISMATCH [cases26] {fn} test {tidx}: "
                      f"pinned=({pin_outcome!r}, {pin_detail!r}) "
                      f"run=({outcome!r}, {detail!r})", file=sys.stderr)
            blg.grammar_release(g)
    finally:
        ctx.destroy()
    summary = {"suite": "cases26", "cases": len(cases), "pin_mismatches": n_bad}
    if expect_path:
        want = load_expect_cases26(expect_path)
        if limit:
            # smoke mode: diff only the cases that were run
            want = {k: v for k, v in want.items() if k in rows}
        extra = set(want) - {(fn, tidx) for fn, tidx, _o, _d in CASES26}
        if extra:
            print(f"EXPECT-DIFF [cases26] evidence rows outside the "
                  f"26-case pin: {sorted(extra)}", file=sys.stderr)
        summary["expect_file"] = expect_path
        summary["expect_mismatches"] = diff_rows(
            rows, want, lambda k: f"{k[0]} test {k[1]}", "cases26")
    print(json.dumps(summary, ensure_ascii=False))
    return n_bad + summary.get("expect_mismatches", 0)


def run_valid_rejects(blg, b, snapshot, coverage_report, expect_path,
                      limit_files=0):
    if not os.path.exists(coverage_report):
        fail_input(f"coverage report not found: {coverage_report} "
                   "(use --coverage-report)")
    with open(coverage_report, encoding="utf-8") as f:
        rep = json.load(f)
    targets = {}  # file -> number of rejected valid instances
    for rec in rep["per_file"]:
        sem = rec.get("semantic") or {}
        n = sum(1 for i in (sem.get("instances") or [])
                if i.get("valid") and i.get("outcome") == "reject")
        if n:
            targets[rec["file"]] = n

    serializer = b.load_value_serializer()
    ctx = blg.Context(b.byte_tokenizer_spec(), mode=0, cache_limit_bytes=0)
    rows = []
    summary = {}
    seen = 0
    files_done = 0
    done_files = set()
    compile_errors = 0
    try:
        for _dataset, fname, path in b.iter_files(snapshot):
            if fname not in targets:
                continue
            if limit_files and files_done >= limit_files:
                break
            files_done += 1
            done_files.add(fname)
            data, schema_doc, tests = b.load_schema_bytes(snapshot, path)
            try:
                g = blg.compile_schema(ctx, data,
                                       profile=b.PROFILE.encode("ascii"))
            except blg.CoreFailure as e:
                compile_errors += 1
                print(json.dumps({"file": fname,
                                  "error": f"compile: {e}"},
                                 ensure_ascii=False))
                continue
            try:
                for t in tests:
                    if t.get("valid") is not True:
                        continue
                    payload, sstat = serializer.serialize_value(
                        t.get("data"), schema_doc)
                    if not sstat.ok:
                        outcome = "serialization_incompatible"
                        detail = (f"{sstat.reason} at "
                                  f"{sstat.pointer or '/'}")
                    else:
                        s = blg.Session(ctx, g)
                        try:
                            outcome, detail = b.feed_document(s, payload)
                        finally:
                            s.destroy()
                    row = {"file": fname,
                           "doc": payload.decode("utf-8", "replace")[:200]
                           if sstat.ok else None,
                           "outcome": outcome, "detail": detail[:120]}
                    print(json.dumps(row, ensure_ascii=False), flush=True)
                    rows.append((fname, row["doc"], outcome, row["detail"]))
                    summary[outcome] = summary.get(outcome, 0) + 1
                    seen += 1
            finally:
                blg.grammar_release(g)
    finally:
        ctx.destroy()
    total = {"files": len(targets), "files_processed": files_done,
             "valid_instances_rechecked": seen,
             "summary": summary}
    if compile_errors:
        total["compile_errors"] = compile_errors
    print(json.dumps(total, ensure_ascii=False))
    # A compile error is a failure in its own right, independent of the
    # pins and of whether an evidence diff is requested.
    n_bad = compile_errors
    if not limit_files:
        # The aggregate pin covers only the pinned keys; run-scope metadata
        # (files_processed, compile_errors) stays out of the comparison.
        pin_view = {k: total.get(k) for k in EXPECTED_VALID_REJECTS}
        if pin_view != EXPECTED_VALID_REJECTS:
            n_bad += 1
            print(f"PIN-MISMATCH [valid-rejects]: "
                  f"pinned={EXPECTED_VALID_REJECTS} run={pin_view}",
                  file=sys.stderr)
    out = {"suite": "valid-rejects", "pin_mismatches": n_bad}
    if expect_path:
        from collections import Counter
        got = Counter(rows)
        want = Counter(load_expect_valid_rejects(expect_path))
        if limit_files:
            # smoke mode: diff the evidence of exactly the files that were
            # run; rows of other files are out of scope, but every evidence
            # row for a selected file must be reproduced
            want = Counter({row: n for row, n in want.items()
                            if row[0] in done_files})
        mismatches = 0
        for row in sorted(set(got) | set(want)):
            if got[row] == want[row]:
                continue
            mismatches += 1
            print(f"EXPECT-DIFF [valid-rejects] {row[0]}: "
                  f"evidence x{want[row]} run x{got[row]}: {row[1:]!r}",
                  file=sys.stderr)
        out["expect_file"] = expect_path
        out["expect_mismatches"] = mismatches
        n_bad += mismatches
    print(json.dumps(out, ensure_ascii=False))
    return n_bad


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="See the module docstring for pins, inputs and evidence.")
    ap.add_argument("--suite", choices=["cases26", "valid-rejects", "both"],
                    default="both")
    ap.add_argument("--lib", default=None,
                    help="path to libbolorgir.so[.0]; else BLG_LIB_PATH, "
                         f"else {DEFAULT_LIB}")
    ap.add_argument("--corpus-dir", default=None,
                    help="MaskBench snapshot data directory "
                         "(default: benchmarks/external/"
                         "jsonschemabench-maskbench/maskbench/data)")
    ap.add_argument("--coverage-report", default=DEFAULT_COVERAGE_REPORT,
                    help="spec-v1 semantic coverage report the valid-rejects "
                         "file set is derived from")
    ap.add_argument("--expect", nargs="?", const="", default=None,
                    help="evidence JSONL/log to diff case-by-case; default: "
                         "the saved logs beside the fix3 report")
    ap.add_argument("--no-expect", action="store_true",
                    help="skip the evidence diff (pins still apply)")
    ap.add_argument("--limit", type=int, default=0,
                    help="cases26: run only the first N cases (smoke runs)")
    ap.add_argument("--limit-files", type=int, default=0,
                    help="valid-rejects: run only the first N target files; "
                         "the aggregate pin is skipped in this mode")
    args = ap.parse_args()

    lib_path = args.lib or os.environ.get("BLG_LIB_PATH") or DEFAULT_LIB
    blg, b = load_engine(lib_path)

    snapshot = dict(b.SNAPSHOTS["maskbench"])
    if args.corpus_dir:
        snapshot["data_dir"] = args.corpus_dir
    if not os.path.isdir(snapshot["data_dir"]):
        fail_input(f"corpus snapshot missing: {snapshot['data_dir']} "
                   "(fetch per bench_jsb_coverage.py, or use --corpus-dir)")

    expect_cases26 = expect_vr = None
    if not args.no_expect:
        expect_cases26 = args.expect or DEFAULT_EXPECT_CASES26
        expect_vr = args.expect or DEFAULT_EXPECT_VALID_REJECTS

    n_bad = 0
    if args.suite in ("cases26", "both"):
        n_bad += run_cases26(blg, b, snapshot, expect_cases26, args.limit)
    if args.suite in ("valid-rejects", "both"):
        n_bad += run_valid_rejects(blg, b, snapshot, args.coverage_report,
                                   expect_vr, args.limit_files)
    return 1 if n_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
