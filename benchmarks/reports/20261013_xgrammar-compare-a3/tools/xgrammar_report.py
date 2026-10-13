#!/usr/bin/env python3
"""T10 section-10 comparison report (TZ 6.2/6.3) - tool revision 5.

Reads one or more raw JSONL files of the comparison collectors and
writes a reproducible summary with complete completion categories,
declared denominators and methods, per-request and batch-run
aggregation, and an independent re-validation of stored answers.

Two record formats are supported and validated per file (detected
from the fields present, or forced with `--format`):

  v2  every record exactly as emitted by `xgrammar_bench2.py`: 22 keys
      (session, backend, workload, batch, repeat, idx, wall_s, status,
      finish, ttft_ms, e2e_ms, tokens, tpot_ms, valid, invalid_reason,
      text_len, text, final_event, usage_present, finish_present,
      transport_error, error_excerpt). All keys are mandatory and
      typed; value consistency is checked (text_len against len(text),
      finish_present against finish, tokens against usage_present,
      tpot_ms against (e2e_ms - ttft_ms)/(tokens - 1) and against its
      definedness rule). `text` is always a string.
  v1  every record exactly as emitted by `xgrammar_bench.py` (the
      2026-10-12 series): the same keys without text, final_event,
      usage_present, finish_present and transport_error.

Counting rules (each request falls into exactly one category; the
report fails on a partition mismatch):

  failed_http_transport   HTTP status != 200 or a transport exception
  unfinished              HTTP 200 and no finish_reason received; in a
                          v2 file also a final stream event ([DONE])
                          that was not observed - a missing final_event
                          field counts as not observed
  stop_invalid            finish stop, answer fails validation
  stop_valid              finish stop, answer passes validation
  length                  finish length (cut by max_tokens; NOT counted
                          as a completed document; a bounded completion
                          check is applied when the text is stored)
  other_finish            any other finish_reason

Integrity checks (any problem fails the tool, exit 1; each of the
false-success modes was reproduced against an earlier revision before
it was closed):

  - the compared session set and the backend of every session must be
    declared before the report is built, via --expect or --manifest;
    the declaration itself is validated everywhere (the CLI and a
    direct build_report call alike): every session needs a non-empty
    admissible backend (xgrammar or bolorgir) and nothing else may be
    declared, and the manifest must carry `session_backends` as a
    mapping that covers `sessions` exactly - a missing block, null, an
    empty mapping, a wrong type, an incomplete coverage, a null value
    or an inadmissible name fails the run before the data is read; a
    declared session missing from the inputs, an undeclared session or
    a recorded-vs-declared backend mismatch is a problem, so a report
    over a subset of the sessions or with mislabeled records cannot
    pass;
  - matrix completeness: every session must carry the declared
    workloads x batches x repeats with run sizes equal to the batch;
  - request keys unique, idx inside [0, batch);
  - per-file record schema, types and value consistency as above; a
    file counts as v2 as soon as any record carries any v2 field, so a
    stripped field cannot hide behind the v1 format;
  - usage required on HTTP-200 finished records; a usage that was not
    observed is never counted as zero tokens (such runs leave the
    token metrics with the reason `usage missing`);
  - stored-vs-revalidated disagreements raise problems; an empty input
    set fails.

Timing samples and methods are named in the output: TTFT over HTTP-200
records with a first token; TPOT over `stop_valid` records only with
tokens > 1 and both timestamps, and the stored value is re-checked
against (e2e - TTFT)/(tokens - 1); p90 =
statistics.quantiles(n=10)[8] when n >= 10, otherwise the maximum,
named as such. Batch-run rows count a run (session, repeat) only when
it has no failed request and no invalid stop, like the historical
report; the report lists excluded runs and reasons. `length` records
stay in all samples where their fields are defined; they are never
silently dropped.

Usage:
  python xgrammar_report.py RAW... --out-json J --out-md M \
      (--expect "L1:backend,L2:backend,..." | --manifest PATH) \
      [--repeats 2] [--format auto|v1|v2] [--title T]
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


# Collector formats: every record must carry every key with the declared
# type. `text` is a string on v2 records (possibly empty), never None -
# the collector emits it on every record, so a null text is a violation.

V2_SCHEMA = {
    "session": "str", "backend": "str", "workload": "str",
    "batch": "int", "repeat": "int", "idx": "int",
    "wall_s": "num", "status": "int|null", "finish": "str|null",
    "ttft_ms": "num|null", "e2e_ms": "num", "tokens": "int|null",
    "tpot_ms": "num|null", "valid": "bool", "invalid_reason": "str",
    "text_len": "int", "text": "str", "final_event": "bool",
    "usage_present": "bool", "finish_present": "bool",
    "transport_error": "bool", "error_excerpt": "str",
}
V1_SCHEMA = {
    "session": "str", "backend": "str", "workload": "str",
    "batch": "int", "repeat": "int", "idx": "int",
    "wall_s": "num", "status": "int|null", "finish": "str|null",
    "ttft_ms": "num|null", "e2e_ms": "num", "tokens": "int|null",
    "tpot_ms": "num|null", "valid": "bool", "invalid_reason": "str",
    "text_len": "int", "error_excerpt": "str",
}
V2_MARKER_KEYS = ("text", "final_event", "usage_present", "finish_present",
                  "transport_error")


def _type_ok(x, spec):
    if spec == "str":
        return isinstance(x, str)
    if spec == "bool":
        return isinstance(x, bool)
    if spec == "int":
        return isinstance(x, int) and not isinstance(x, bool)
    if spec == "int|null":
        return x is None or (isinstance(x, int) and not isinstance(x, bool))
    if spec == "num":
        return isinstance(x, (int, float)) and not isinstance(x, bool)
    if spec == "num|null":
        return x is None or (isinstance(x, (int, float)) and not isinstance(x, bool))
    if spec == "str|null":
        return x is None or isinstance(x, str)
    raise AssertionError(f"unknown type spec {spec!r}")


def detect_format(recs):
    """v2 as soon as any record carries any v2 field (even with a null
    value); otherwise v1; `empty` when there are no records."""
    if not recs:
        return "empty"
    for r in recs:
        if any(k in r for k in V2_MARKER_KEYS):
            return "v2"
    return "v1"


def check_record(rec, fmt):
    """Format and value-consistency violations of one record."""
    out = []
    for k, spec in (V2_SCHEMA if fmt == "v2" else V1_SCHEMA).items():
        if k not in rec:
            out.append(f"missing {k}")
        elif not _type_ok(rec[k], spec):
            out.append(f"{k}: not {spec}")
    if out:
        return out  # type problems would cascade into consistency noise
    if fmt == "v2":
        if rec["finish_present"] != (rec["finish"] is not None):
            out.append("finish_present does not match finish")
        if not rec["usage_present"] and rec["tokens"] is not None:
            out.append("tokens set while usage_present is false")
        if rec["text_len"] != len(rec["text"]):
            out.append(f"text_len {rec['text_len']} != len(text) {len(rec['text'])}")
    if rec["tpot_ms"] is not None:
        if not (rec["valid"] and rec["ttft_ms"] is not None
                and rec["tokens"] is not None and rec["tokens"] > 1):
            out.append("tpot_ms set although it is not defined for this record")
        else:
            want = (rec["e2e_ms"] - rec["ttft_ms"]) / (rec["tokens"] - 1)
            if abs(rec["tpot_ms"] - want) > 1e-6 + 1e-9 * abs(want):
                out.append("tpot_ms != (e2e_ms - ttft_ms)/(tokens - 1)")
    elif (rec["valid"] and rec["ttft_ms"] is not None
          and rec["tokens"] is not None and rec["tokens"] > 1):
        out.append("tpot_ms missing although it is defined for this record")
    return out


def load_files(paths, fmt="auto"):
    """Load all files; returns (flat records, per-file formats, format
    problems). Each record is tagged with its file format in `_fmt`."""
    recs, formats, problems = [], {}, []
    for p in paths:
        file_recs = []
        with open(p, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if line.strip():
                    file_recs.append((json.loads(line), lineno))
        f_fmt = fmt if fmt in ("v1", "v2") else detect_format([r for r, _ in file_recs])
        formats[os.path.basename(p)] = f_fmt
        bad = []
        for r, lineno in file_recs:
            vio = check_record(r, f_fmt)
            if vio:
                bad.append(f"line {lineno}: " + "; ".join(vio))
            r["_fmt"] = f_fmt
            recs.append(r)
        if bad:
            shown = "; ".join(bad[:3])
            more = "" if len(bad) <= 3 else f" (+{len(bad) - 3} more)"
            problems.append(f"{os.path.basename(p)}: {len(bad)} record format "
                            f"violation(s) in the {f_fmt} format: {shown}{more}")
    return recs, formats, problems


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


def final_seen(rec):
    """v2: was the final stream event ([DONE]) observed? A missing
    final_event field in a v2 record counts as not observed (and is a
    format violation on top). v1 records do not record the stream end
    state at all."""
    fmt = rec.get("_fmt")
    is_v2 = fmt == "v2" or (fmt is None and any(k in rec for k in V2_MARKER_KEYS))
    if not is_v2:
        return True
    return rec.get("final_event") is True


def usage_ok(rec):
    """Was a usage observed and a token count stored? A v1 record has
    no usage_present field: its token count counts when present."""
    up = rec.get("usage_present")
    if up is None:
        return rec.get("tokens") is not None
    return bool(up) and rec.get("tokens") is not None


def category(rec):
    """The single completion category of a record (rules in the docstring)."""
    if rec.get("transport_error") or rec.get("status") != 200:
        return "failed_http_transport"
    if not finish_present(rec):
        return "unfinished"
    if not final_seen(rec):
        # v2 record: the stream did not reach its final event ([DONE])
        return "unfinished"
    if rec.get("finish") == "stop":
        return "stop_valid" if rec.get("valid") else "stop_invalid"
    if rec.get("finish") == "length":
        return "length"
    return "other_finish"


MATRIX_WORKLOADS = ("w1_json_small", "w2_json_nested", "w3_regex64")
MATRIX_BATCHES = (1, 8, 32, 128)
TOOL_REVISION = 5
# Admissible backend names of the compared sessions (the structured
# output backends this comparison covers). A declaration may name no
# other backend, and every declared session must name one.
KNOWN_BACKENDS = ("xgrammar", "bolorgir")


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
    """Batch runs: (session, repeat, workload, batch) -> row.

    A run is complete when it holds exactly `batch` records; an
    incomplete run is excluded from the throughput columns with its
    reason, and a usage that was not observed (usage_present false or
    no token count) never turns into zero tokens (such a run leaves
    the token metrics with the stated reason).
    """
    runs = {}
    for r in recs:
        k = (r["session"], r["repeat"], r["workload"], r["batch"])
        runs.setdefault(k, []).append(r)
    out = []
    for k, rs in sorted(runs.items()):
        cats = partition(rs)
        bad = None
        if cats["failed_http_transport"]:
            bad = "failed request"
        elif cats["stop_invalid"]:
            bad = "invalid stop"
        elif cats["unfinished"]:
            bad = "unfinished request"
        expected = k[3]
        complete = len(rs) == expected
        wall = max((r.get("wall_s") or 0) for r in rs)
        tokens_missing = any(not usage_ok(r) for r in rs)
        toks = sum(r["tokens"] for r in rs if r.get("tokens") is not None)
        included = complete and bad is None and wall > 0
        exclusion = None
        if bad is not None:
            exclusion = bad
        elif not complete:
            exclusion = f"incomplete run ({len(rs)} of {expected})"
        elif wall <= 0:
            exclusion = "no wall time"
        tok_included = included and not tokens_missing
        tok_exclusion = exclusion if exclusion is not None else (
            "usage missing" if tokens_missing else None)
        out.append({"session": k[0], "repeat": k[1], "workload": k[2], "batch": k[3],
                    "requests": len(rs), "expected": expected, "complete": complete,
                    "included": included, "exclusion": exclusion,
                    "tok_included": tok_included, "tok_exclusion": tok_exclusion,
                    "tokens": toks if not tokens_missing else None,
                    "wall_s": wall,
                    "tok_per_s": (toks / wall if tok_included else None),
                    "stop_valid_per_s": (cats["stop_valid"] / wall if included else None),
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
        "usage_missing": sum(1 for r in recs if not usage_ok(r)),
        "final_event_missing": sum(1 for r in recs if not final_seen(r)),
    }
    ttft = [r.get("ttft_ms") for r in recs if r.get("status") == 200 and r.get("ttft_ms") is not None]
    tpot = [r.get("tpot_ms") for r in recs
            if category(r) == "stop_valid" and r.get("tpot_ms") is not None]
    e2e = [r.get("e2e_ms") for r in recs if r.get("status") == 200 and r.get("e2e_ms") is not None]
    rec["ttft_ms_median"], rec["ttft_n"] = med(ttft), len(ttft)
    rec["tpot_ms_median"], rec["tpot_n"] = med(tpot), len(tpot)
    rec["tpot_excluded"] = n - len(tpot)
    rec["e2e_ms_median"], rec["e2e_n"] = med(e2e), len(e2e)
    rec["ttft_ms_p90"], rec["ttft_p90_method"] = p90(ttft)
    rec["tpot_ms_p90"], rec["tpot_p90_method"] = p90(tpot)
    runs = run_rows(recs)
    inc = [r for r in runs if r["included"]]
    tinc = [r for r in runs if r["tok_included"]]
    rec["runs"] = len(runs)
    rec["runs_included"] = len(inc)
    rec["tok_runs_included"] = len(tinc)
    rec["runs_excluded"] = [{"session": r["session"], "repeat": r["repeat"],
                             "workload": r["workload"], "batch": r["batch"],
                             "reason": r["exclusion"]} for r in runs if not r["included"]]
    rec["tok_runs_excluded"] = [{"session": r["session"], "repeat": r["repeat"],
                                 "workload": r["workload"], "batch": r["batch"],
                                 "reason": r["tok_exclusion"]}
                                for r in runs if not r["tok_included"]]
    rec["tok_per_s_median"] = med([r["tok_per_s"] for r in tinc])
    rec["stop_valid_per_s_median"] = med([r["stop_valid_per_s"] for r in inc])
    rec["stop_valid_share"] = (cats["stop_valid"] / n) if n else None
    rec["cold_warm"] = {}
    for rep in sorted({r["repeat"] for r in recs}):
        part = [r for r in recs if r["repeat"] == rep]
        rec["cold_warm"][f"rep{rep}"] = {
            "requests": len(part),
            "ttft_ms_median": med([r.get("ttft_ms") for r in part if r.get("status") == 200]),
            "tpot_ms_median": med([r.get("tpot_ms") for r in part
                                   if category(r) == "stop_valid"]),
        }
    return rec


def file_record_count(path):
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def check_declaration(expected):
    """Validate a session/backend declaration as a whole (review round
    4: the declaration is checked, not only its agreement with the
    inputs, and a direct build_report call is checked like the CLI).

    Returns (declared, problems). `declared` carries the usable
    label -> backend pairs; `problems` names every defect: no
    declaration at all, a malformed or empty mapping, an empty or
    non-string label, a backend that is missing, empty, not a string
    or not admissible. An absent backend is never a permission to
    skip: it is a problem and must fail the report."""
    if not expected:
        return {}, ["expected session set not declared "
                    '(pass --expect "L:backend,..." or --manifest PATH)']
    sess = expected.get("sessions")
    if not isinstance(sess, dict) or not sess:
        return {}, ["expected session declaration carries no sessions"]
    problems, declared = [], {}
    for lab, be in sess.items():
        if not isinstance(lab, str) or not lab:
            problems.append("expected session declaration carries an empty or "
                            "non-string session label")
            continue
        declared[lab] = be
        if not isinstance(be, str) or not be:
            problems.append(f"session {lab}: declared backend is missing or not "
                            "a non-empty string")
        elif be not in KNOWN_BACKENDS:
            problems.append(f"session {lab}: declared backend {be!r} is not an "
                            f"admissible backend (known: {', '.join(KNOWN_BACKENDS)})")
    return declared, problems


def build_report(paths, repeats=2, expected=None, fmt="auto"):
    recs, formats, problems = load_files(paths, fmt)
    if not recs:
        problems.append("no records loaded from the inputs")
    for p in paths:
        if file_record_count(p) == 0:
            problems.append(f"input file has no records: {os.path.basename(p)}")
    # declared session set: the declaration is validated as a whole
    # and can never disable a check (a None backend is a problem, not
    # a permission to skip the comparison)
    exp_source = (expected or {}).get("source")
    exp_sessions, decl_problems = check_declaration(expected)
    problems.extend(decl_problems)
    present = sorted({r["session"] for r in recs})
    missing_sessions, unexpected, backend_bad = [], [], []
    for lab in sorted(exp_sessions):
        bexp = exp_sessions[lab]
        if lab not in present:
            missing_sessions.append(lab)
            problems.append(f"expected session missing from the inputs: {lab}")
            continue
        bad = sorted({r.get("backend") for r in recs
                      if r["session"] == lab and r.get("backend") != bexp})
        if bad:
            backend_bad.append(lab)
            problems.append(f"session {lab}: backend {bad} does not match "
                            f"the declared {bexp}")
    backend_invalid = sorted(lab for lab, be in exp_sessions.items()
                             if not isinstance(be, str) or not be
                             or be not in KNOWN_BACKENDS)
    if exp_sessions:
        for lab in present:
            if lab not in exp_sessions:
                unexpected.append(lab)
                problems.append(f"unexpected session in the inputs: {lab}")
    # request-key uniqueness and idx range
    seen = {}
    for r in recs:
        k = (r.get("session"), r.get("workload"), r.get("batch"), r.get("repeat"), r.get("idx"))
        seen[k] = seen.get(k, 0) + 1
    dup = sum(1 for v in seen.values() if v > 1)
    if dup:
        problems.append(f"duplicate request keys: {dup}")
    idx_bad = sum(1 for r in recs
                  if not (isinstance(r.get("idx"), int) and isinstance(r.get("batch"), int)
                          and 0 <= r["idx"] < r["batch"]))
    if idx_bad:
        problems.append(f"request idx outside [0, batch): {idx_bad}")
    # matrix completeness per session
    matrix = {}
    for s in present:
        sess = [r for r in recs if r["session"] == s]
        runs = run_rows(sess)
        by = {(r["workload"], r["batch"], r["repeat"]): r for r in runs}
        missing, short, extra = [], [], []
        for w in MATRIX_WORKLOADS:
            for b in MATRIX_BATCHES:
                for rep in range(repeats):
                    row = by.get((w, b, rep))
                    if row is None:
                        missing.append(f"{w}|{b}|rep{rep}")
                    elif row["requests"] != b:
                        short.append(f"{w}|{b}|rep{rep}:{row['requests']}")
        for k in by:
            if (k[0] not in MATRIX_WORKLOADS or k[1] not in MATRIX_BATCHES
                    or not 0 <= k[2] < repeats):
                extra.append(f"{k[0]}|{k[1]}|rep{k[2]}")
        matrix[s] = {"records": len(sess),
                     "expected_records": sum(MATRIX_BATCHES) * len(MATRIX_WORKLOADS) * repeats,
                     "runs": len(runs),
                     "expected_runs": len(MATRIX_WORKLOADS) * len(MATRIX_BATCHES) * repeats,
                     "missing_runs": missing, "short_runs": short, "extra_runs": extra}
        if missing:
            problems.append(f"{s}: missing runs: {len(missing)}")
        if short:
            problems.append(f"{s}: runs with the wrong request count: {len(short)}")
        if extra:
            problems.append(f"{s}: runs outside the declared matrix: {len(extra)}")
    # stream end and usage on finished HTTP-200 records
    no_final = [r for r in recs
                if "final_event" in r and r.get("final_event") is not True
                and not r.get("transport_error") and r.get("status") == 200]
    if no_final:
        problems.append(f"final stream event ([DONE]) not observed: {len(no_final)}")
    no_usage = [r for r in recs
                if r.get("status") == 200 and finish_present(r) and r.get("tokens") is None]
    if no_usage:
        problems.append(f"usage missing on finished HTTP-200 records: {len(no_usage)}")
    backends = sorted({r["backend"] for r in recs})
    workloads = sorted({r["workload"] for r in recs})
    batches = sorted({r["batch"] for r in recs})
    cells = {}
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
    rv = revalidate(recs)
    if rv["valid_differ"]:
        problems.append(f"stored-vs-revalidated disagreements: {len(rv['valid_differ'])}")
    doc = {
        "about": "T10 section-10 comparison summary v2 (complete completion categories, "
                 "declared denominators; see the method block) - tool revision "
                 f"{TOOL_REVISION} with a declared session set, per-file input-format "
                 "validation and matrix/stream/usage integrity checks",
        "method": {
            "categories": "each request exactly one of " + ", ".join(CATEGORIES),
            "integrity": "problems (non-empty fails the tool) cover the declared session set "
                         "(a missing or malformed declaration, a declared session missing "
                         "from the inputs, an undeclared session, a declared backend that is "
                         "missing, empty or not admissible, a recorded-vs-declared backend "
                         "mismatch), per-file record formats (mandatory fields, types, value "
                         "consistency), matrix completeness, duplicate keys, idx range, "
                         "missing final stream event or usage on finished HTTP-200 records, "
                         "stored-vs-revalidated disagreements and empty inputs",
            "input_format": "records must match the collector that produced them: v2 = every "
                            "field emitted by xgrammar_bench2.py (all mandatory and typed; "
                            "text always a string; finish_present checked against finish, "
                            "tokens against usage_present, tpot_ms against (e2e_ms - "
                            "ttft_ms)/(tokens - 1)); v1 = every field emitted by "
                            "xgrammar_bench.py (no answer text); detected per file unless "
                            "--format forces one",
            "expected_sessions": "the session set and each session's backend are declared via "
                                 "--expect or --manifest before the report is built; the "
                                 "declaration must name every compared session with a "
                                 "non-empty admissible backend (xgrammar/bolorgir) and "
                                 "nothing else, and the manifest's session_backends mapping "
                                 "must cover its sessions exactly; every declared session "
                                 "must appear in the inputs, no undeclared session may "
                                 "appear and the recorded backend must match",
            "ttft": "seconds from send to the first content delta; median over HTTP-200 records "
                    "with a first token (n stated)",
            "tpot": "(e2e - ttft)/(completion_tokens - 1), client-observed per-token time "
                    "including server overheads, NOT a profiled mask cost; median over "
                    "stop_valid records only where it is defined (tokens > 1; the stored "
                    "value is re-checked against the formula; n and exclusions stated)",
            "throughput": "completion tokens / batch wall time, median over complete batch runs "
                          "with no failed request, no invalid stop, no unfinished request and a "
                          "recorded usage for every request (run counts stated; a missing "
                          "usage removes the run from this column, never zero tokens)",
            "stop_valid_per_s": "stop_valid documents / batch wall time, median over complete "
                                "runs with no failed/invalid/unfinished request",
            "p90": "statistics.quantiles(n=10)[8] when n >= 10, otherwise the maximum, "
                   "labeled max(n<10)",
            "cold_warm": "repeat 0 against the remaining repeats, both shown",
            "length_cuts": "kept in every sample where their fields are defined; not counted as "
                           "completed documents; a bounded completion check is applied when the "
                           "text is stored (declared suffix list, not a proof)",
        },
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expected_sessions": {"source": exp_source, "sessions": exp_sessions,
                              "missing": missing_sessions, "unexpected": unexpected,
                              "backend_mismatch": backend_bad,
                              "backend_invalid": backend_invalid},
        "formats": formats,
        "sessions": present,
        "matrix_repeats": repeats,
        "matrix": matrix,
        "records": len(recs),
        "inputs": [{"path": p, "sha256": sha256(p)} for p in paths],
        "tool": {"path": os.path.abspath(__file__), "sha256": sha256(os.path.abspath(__file__)),
                 "revision": TOOL_REVISION},
        "revalidation": rv,
        "problems": problems,
        "cells": cells,
    }
    return doc


def write_md(doc, out_md, title):
    md = [f"# {title}", "",
          f"Generated {doc['generated_utc']} by `xgrammar_report.py` "
          f"(revision {doc['tool'].get('revision', '?')}, sha256 `{doc['tool']['sha256'][:16]}…`) "
          f"from: "
          + ", ".join(f"`{os.path.basename(i['path'])}` (`{i['sha256'][:16]}…`)"
                      for i in doc["inputs"]) + ".", "",
          "Method (denominators and rules; abbreviated): " + "; ".join(
              f"**{k}**: {v}" for k, v in doc["method"].items()) + ".", ""]
    if "matrix" in doc:
        md += ["## Completeness (matrix, stream end, usage)", "",
               f"Declared matrix per session: {len(MATRIX_WORKLOADS)} workloads x batches "
               + "/".join(str(b) for b in MATRIX_BATCHES)
               + f" x {doc.get('matrix_repeats', '?')} repeats.", "",
               "| session | records | expected records | runs | expected runs | missing runs | "
               "short runs | extra runs |",
               "|---|---|---|---|---|---|---|---|"]
        for s, m in sorted(doc["matrix"].items()):
            md.append(f"| {s} | {m['records']} | {m['expected_records']} | {m['runs']} | "
                      f"{m['expected_runs']} | {len(m['missing_runs'])} | "
                      f"{len(m['short_runs'])} | {len(m['extra_runs'])} |")
        md.append("")
        es = doc.get("expected_sessions", {})
        sess_txt = ", ".join(f"{lab} ({be})" if be else lab
                             for lab, be in sorted((es.get("sessions") or {}).items())) \
            or "none declared"
        md += [f"Expected sessions (from {es.get('source') or 'no declaration'}): {sess_txt}; "
               f"missing: {', '.join(es.get('missing') or []) or 'none'}; "
               f"undeclared: {', '.join(es.get('unexpected') or []) or 'none'}.", "",
               "Input formats: "
               + ", ".join(f"{fn} {fv}" for fn, fv in sorted(doc.get("formats", {}).items()))
               + " (every record checked against its format: mandatory keys, types and "
                 "value consistency).", ""]
    backends = sorted({k.split("|")[0] for k in doc["cells"]})
    workloads = sorted({k.split("|")[1] for k in doc["cells"]})
    for w in workloads:
        md += [f"## {w}", "",
               "| batch | backend | req | failed | unfinished | stop_valid | stop_invalid | "
               "length | other | usage_missing | TTFT p50 (n) | TPOT p50 (n) | TPOT p90 (method) | "
               "tok/s (runs) | stop_valid/s (runs) | stop_valid share |",
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
                    f"({c['tok_runs_included']}/{c['runs']}) | {c['stop_valid_per_s_median']} "
                    f"({c['runs_included']}/{c['runs']}) | "
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
    ap.add_argument("--repeats", type=int, default=2,
                    help="repeats per (workload, batch) in the recorded matrix (default 2)")
    ap.add_argument("--format", choices=("auto", "v1", "v2"), default="auto",
                    help="record format; auto detects it per file (default)")
    ap.add_argument("--title", default="XGrammar comparison (T10 section 10)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--expect", metavar='"L1:backend,L2:backend,..."',
                   help="declare the compared sessions and their backends")
    g.add_argument("--manifest", metavar="PATH",
                   help="manifest whose `sessions` are covered exactly by its "
                        "mandatory `session_backends` mapping")
    args = ap.parse_args()
    expected = None
    if args.expect:
        sessions = {}
        for item in args.expect.split(","):
            item = item.strip()
            if not item:
                continue
            if ":" not in item:
                ap.error(f"--expect item {item!r} must be LABEL:BACKEND")
            lab, be = (x.strip() for x in item.split(":", 1))
            if not lab:
                ap.error("--expect: empty session label")
            if not be:
                ap.error(f"--expect: session {lab!r} carries no backend")
            if lab in sessions:
                ap.error(f"--expect: duplicate session {lab!r}")
            if be not in KNOWN_BACKENDS:
                ap.error(f"--expect: backend {be!r} of session {lab!r} is not "
                         f"admissible (known: {', '.join(KNOWN_BACKENDS)})")
            sessions[lab] = be
        if not sessions:
            ap.error("--expect declares no sessions")
        expected = {"source": f"--expect {args.expect}", "sessions": sessions}
    elif args.manifest:
        with open(args.manifest, encoding="utf-8") as f:
            man = json.load(f)
        sess = man.get("sessions")
        if not (isinstance(sess, list) and sess and all(isinstance(x, str) for x in sess)):
            ap.error(f"--manifest {args.manifest}: no usable session list")
        if any(not s for s in sess):
            ap.error(f"--manifest {args.manifest}: empty session label")
        if len(set(sess)) != len(sess):
            ap.error(f"--manifest {args.manifest}: duplicate sessions")
        # session_backends is mandatory and must cover `sessions` exactly
        # with non-empty admissible backend names
        sb = man.get("session_backends")
        if not isinstance(sb, dict):
            ap.error(f"--manifest {args.manifest}: session_backends must be a "
                     f"mapping label -> backend (missing, null or wrong type)")
        if set(sb) != set(sess):
            missing = sorted(set(sess) - set(sb))
            extra = sorted(set(sb) - set(sess))
            ap.error(f"--manifest {args.manifest}: session_backends must cover "
                     f"the sessions exactly (missing: {missing or 'none'}; "
                     f"unexpected: {extra or 'none'})")
        for lab in sess:
            be = sb[lab]
            if not isinstance(be, str) or not be:
                ap.error(f"--manifest {args.manifest}: backend of session {lab!r} "
                         f"must be a non-empty string")
            if be not in KNOWN_BACKENDS:
                ap.error(f"--manifest {args.manifest}: backend {be!r} of session "
                         f"{lab!r} is not admissible "
                         f"(known: {', '.join(KNOWN_BACKENDS)})")
        expected = {"source": f"manifest {os.path.abspath(args.manifest)} "
                              f"sha256 {sha256(args.manifest)}",
                    "sessions": {s: sb[s] for s in sess}}
    paths = []
    for p in args.raw:
        paths.extend(sorted(glob.glob(p)))
    doc = build_report(paths, repeats=args.repeats, expected=expected, fmt=args.format)
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
    write_md(doc, args.out_md, args.title)
    print(f"{doc['records']} records, {len(doc['cells'])} cells, "
          f"{len(doc['problems'])} problem(s) -> {args.out_json}, {args.out_md}")
    return 1 if doc["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
