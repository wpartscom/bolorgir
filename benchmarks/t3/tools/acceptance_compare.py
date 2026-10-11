#!/usr/bin/env python3
"""Coverage control and acceptance verdict of a T3 candidate.

Reads saved results only (bench_jsb_coverage.py --generatable reports
`coverage_<snapshot>.json[.gz]`, tests/oracle/run_oracle.py reports, the
T2c triage of benchmarks/triage_semantic.py --out-json); nothing is run.

  --candidate NAME=DIR    reports of the candidate (one or both corpora)
  --line NAME=DIR         a reference line, repeatable; the first one is the
                          baseline of the coverage control (dbc5eeb)
  --d4 PATH               owner decision list (benchmarks/t3/d4_proposals.json)
  --triage PATH           T2c triage of the candidate (triage_semantic.py
                          --coverage of the candidate's reports --gate-build
                          NAME --out-json PATH)
  --oracle NAME=PATH      one run_oracle.py report (one dialect) of build
                          NAME, repeatable; the candidate needs all five
  --oracle-lines PATH     oracle reports of the lines with their sha256
                          (benchmarks/t3/oracle_lines.json)
  --protocol-notes PATH   explained protocol differences
                          (benchmarks/t3/protocol_notes.json)
  --out-md / --out-json   the report

Checks:

  library:<build>          one library sha256 over the corpora of a build
  protocol:<line>:<corpus> K, seed, budgets, limits, corpus and tokenizer
                           revisions equal to the line, or the difference
                           explained in the protocol notes
  corpus:<line>:<corpus>   the same (dataset, file) set as the line
  hf_sample:<build>:<corpus>:<tokenizer>
                           the fixed 1-in-N sample: a compiled schema is
                           `skipped:not_sampled` exactly when it is outside
                           the sample rule of bench_jsb_coverage.in_sample
  goal_unavailable         MaskBench, Llama 3.1: engine_unavailable at most
                           1% of the candidate's results (base: not skipped)
  coverage_baseline:<corpus>:<tokenizer>
                           generatable count not below the baseline line
  no_defects               no engine_defect and no validator_error result
  transitions_described    every result generatable on a line and not on
                           the candidate has a D4 entry
  transitions_approved     every such result has an owner approval bound to
                           the candidate and the row (see d4_proposals.json
                           decision_format), with the T2c class where the
                           entry requires one
  d4_candidate             the D4 list names the candidate's library
  oracle_content:<build>   every oracle report of the build is evidence
                           about its library (oracle_content_problems):
                           rows, compiled schemas and engine verdicts, no
                           compile refusal of the call itself, rows and
                           summary that agree. A report that fails gives
                           no data: its build has no oracle data, and the
                           checks below that compare it fail
  oracle_dialects:<build>  all five spec-v1 dialects present
  oracle_identity          every oracle report of the candidate names the
                           candidate's library sha256
  oracle_inputs:<line>     same suite, validator, profile and test rows as
                           the line
  goal_oracle              oracle 2020-12 ENGINE_ERROR at most 10
  oracle_pass:<line>       PASS per dialect not below the line
  oracle_binding:<line>    every oracle report of the line belongs to the
                           line's library: its header names that
                           library_sha256, or (reports older than that
                           field) the archive binding of the oracle line
                           list holds; the list names the same library as
                           the line's coverage reports
  oracle_line:<line>       a line without oracle data: only with an owner
                           exception of the oracle line list
                           (decision_format) that covers the candidate
                           and names a control line with data
  oracle_lines             every line has oracle data or an approved
                           exception, and at least one line has data
  triage                   the T2c triage of the same candidate reports
                           passes its gate

A transition is any (corpus, file, tokenizer) generatable on a line and
not generatable on the candidate, including a schema whose main compile
now fails and a result that is missing on the candidate.

Verdict (`acceptance` in the JSON): `intermediate` when a corpus of the
candidate is missing (its checks are pending; transitions and goals of
the present corpus are reported), `admitted` when every check holds,
`not_admitted` otherwise. A written report is not an acceptance: only
the verdict is.

Exit status: 0 admitted, 1 not admitted, 2 unusable input, 3 intermediate.
"""

import argparse
import datetime
import gzip
import hashlib
import json
import os
import sys

OUTCOMES = ("generatable", "incomplete", "invalid_document", "engine_defect",
            "validator_error", "engine_unavailable", "skipped")
BUCKETS = ("compiled", "invalid_schema", "unsupported_feature", "resource_limit",
           "unsatisfiable_constraint", "harness_error")
SNAPSHOTS = ("repo", "maskbench")
GOAL_TOKENIZER = "llama-3.1"
GOAL_UNAVAILABLE_SHARE = 0.01
GOAL_ORACLE_2020_12 = 10
TRIAGE_OUTCOMES = ("invalid_document", "engine_defect", "validator_error")
DIALECTS = ("draft4", "draft6", "draft7", "draft2019-09", "draft2020-12")
ORACLE_OUTCOMES = ("PASS", "SKIPPED", "KNOWN_GAP", "ENGINE_ERROR", "MISMATCH",
                   "SERIALIZATION_INCOMPATIBLE")
# Compile refusals that describe the call, not the schema: blg_compile
# returns INVALID_ARGUMENT only for null pointers, a struct_size, flags or
# a kind it does not know - the harness and the core disagree on the ABI.
ORACLE_CALL_ERRORS = ("INVALID_ARGUMENT",)
ENGINE_VERDICTS = ("accept", "reject")
# summary.by_file counter of each row outcome (run_oracle.py)
ORACLE_FILE_KEYS = {"PASS": "pass", "MISMATCH": "mismatch", "KNOWN_GAP": "known_gap",
                    "ENGINE_ERROR": "engine_error",
                    "SERIALIZATION_INCOMPATIBLE": "serialization_incompatible",
                    "SKIPPED": "refused"}
EXIT_ADMITTED, EXIT_NOT_ADMITTED, EXIT_UNUSABLE, EXIT_INTERMEDIATE = 0, 1, 2, 3
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class Unusable(Exception):
    """Input that cannot be compared (exit status 2)."""


def named(spec):
    name, sep, path = spec.partition("=")
    if not sep or not name or not path:
        raise Unusable(f"expected NAME=PATH, got {spec!r}")
    return name, path


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def report_path(directory, snapshot):
    for suffix in (".json", ".json.gz"):
        p = os.path.join(directory, f"coverage_{snapshot}{suffix}")
        if os.path.exists(p):
            return p
    return None


def in_sample(dataset, fname, every):
    """Copy of bench_jsb_coverage.in_sample (the fixed HF sample);
    tests/test_t3_acceptance_tools.py keeps the two equal."""
    if every <= 1:
        return True
    h = hashlib.sha256(f"{dataset}/{fname}".encode("utf-8"))
    return int(h.hexdigest()[:12], 16) % every == 0


class Build:
    """Per-file results of one build: {snapshot: {(dataset, file): rec}}."""

    def __init__(self, name, directory):
        self.name = name
        self.directory = directory
        self.files = {}
        self.meta = {}
        self.paths = {}
        for snap in SNAPSHOTS:
            p = report_path(directory, snap)
            if p is None:
                continue
            rep = load_json(p)
            self.paths[snap] = p
            self.meta[snap] = rep["meta"]
            self.files[snap] = {(r["dataset"], r["file"]): r for r in rep["per_file"]}
        if not self.files:
            raise Unusable(f"{name}: no coverage_<snapshot>.json[.gz] in {directory}")
        self.libraries = {s: (m.get("engine") or {}).get("library_sha256")
                          for s, m in self.meta.items()}

    @property
    def library(self):
        """The library sha256 of the build, None unless every corpus
        names the same one."""
        libs = set(self.libraries.values())
        return libs.pop() if len(libs) == 1 and None not in libs else None

    def library_text(self):
        return ", ".join(f"{s} {v or '-'}" for s, v in sorted(self.libraries.items()))

    def tokenizers(self, snap):
        gen = self.meta[snap].get("generatable") or {}
        return list((gen.get("tokenizers") or {}).keys())


def protocol(meta):
    """The run parameters that must match between builds, flattened."""
    gen = meta.get("generatable") or {}
    out = {k: gen.get(k) for k in ("profile", "passes", "seed", "max_steps", "max_gen_seconds",
                                   "schema_budget_seconds", "context_recycle_every",
                                   "validator")}
    out["completed_from"] = bool(gen.get("completed_from"))
    out["crashes"] = len(gen.get("crashes") or [])
    for k, v in sorted((meta.get("limits") or {}).items()):
        out[f"limits.{k}"] = v
    for k in ("repo", "revision", "path", "sha256", "files"):
        out[f"corpus.{k}"] = (meta.get("corpus") or {}).get(k)
    for tok, info in sorted((gen.get("tokenizers") or {}).items()):
        for k in ("name", "revision", "vocab_size", "single_byte_tokens", "sample_every"):
            out[f"tokenizers.{tok}.{k}"] = (info or {}).get(k)
    return out


def same(a, b):
    """Equal values, a bool never equal to a number."""
    return isinstance(a, bool) == isinstance(b, bool) and a == b


def note_for(notes, param, line, snap, cand_value, line_value):
    for e in notes:
        if (e.get("parameter") == param and line in (e.get("lines") or [])
                and snap in (e.get("corpora") or SNAPSHOTS)
                and same(e.get("candidate"), cand_value) and same(e.get("line"), line_value)):
            return e
    return None


def protocol_diffs(cand, line, snap, notes):
    pc, pl = protocol(cand.meta[snap]), protocol(line.meta[snap])
    rows = []
    for k in sorted(set(pc) | set(pl)):
        if pc.get(k) == pl.get(k):
            continue
        e = note_for(notes, k, line.name, snap, pc.get(k), pl.get(k))
        rows.append({"line": line.name, "corpus": snap, "parameter": k,
                     "candidate": pc.get(k), "line_value": pl.get(k),
                     "explanation": e.get("explanation") if e else None})
    return rows


def sample_violations(build, snap):
    """{tokenizer: [files]} where the not_sampled marks break the rule."""
    out = {}
    gen = build.meta[snap].get("generatable") or {}
    for tok, info in (gen.get("tokenizers") or {}).items():
        every = (info or {}).get("sample_every") or 1
        if every <= 1:
            continue
        bad = []
        for (dataset, fname), rec in build.files[snap].items():
            if rec.get("error_class") != "compiled":
                continue
            g = (rec.get("generatable") or {}).get(tok)
            if g is None:
                bad.append(fname)
                continue
            marked = g["outcome"] == "skipped" and g.get("code") == "not_sampled"
            if marked == in_sample(dataset, fname, every):
                bad.append(fname)
        out[tok] = sorted(bad)
    return out


def bucket_counts(files):
    out = dict.fromkeys(BUCKETS, 0)
    for rec in files.values():
        k = rec.get("error_class") or "harness_error"
        out[k] = out.get(k, 0) + 1
    return out


def outcome_counts(files, tok):
    out = dict.fromkeys(OUTCOMES, 0)
    for rec in files.values():
        g = (rec.get("generatable") or {}).get(tok)
        if g is None:
            continue
        out[g["outcome"]] = out.get(g["outcome"], 0) + 1
    out["base"] = sum(v for k, v in out.items() if k != "skipped")
    return out


def pct(n, base):
    return f"{100.0 * n / base:.2f}%" if base else "-"


def counts_text(counts):
    return ", ".join(f"{k} {v}" for k, v in sorted((counts or {}).items(), key=lambda kv: -kv[1]))


def result_state(rec, tok):
    """(outcome or refusal label, stage, code, pass counts) of one result."""
    if rec is None:
        return "missing", None, None, {}
    if rec.get("error_class") != "compiled":
        return f"compile:{rec.get('error_class')}", rec.get("error_stage"), rec.get("status"), {}
    g = (rec.get("generatable") or {}).get(tok)
    if g is None:
        return "missing", None, None, {}
    return g["outcome"], g.get("stage"), g.get("code"), g.get("counts") or {}


def class_hint(state):
    if state in ("engine_unavailable", "incomplete", "compile:resource_limit"):
        return "C"
    if state in TRIAGE_OUTCOMES:
        return "T2c"
    if state.startswith("compile:"):
        return "compile refusal"
    return "check"


# --- D4 -----------------------------------------------------------------

def d4_load(path):
    """(candidate sha256 of the list, entries: specific files first, then
    rule entries with file "*")."""
    if not path:
        return None, []
    doc = load_json(path)
    entries = sorted(doc.get("entries", []), key=lambda e: e.get("file") == "*")
    return doc.get("candidate_library_sha256"), entries


def d4_entry(entries, snap, fname, tok, state):
    for e in entries:
        if e.get("corpus") not in (snap, "*") or e.get("file") not in (fname, "*"):
            continue
        if tok not in (e.get("tokenizers") or [tok]):
            continue
        if e.get("match_outcome") not in (None, state):
            continue
        return e
    return None


def triage_key(cand, row):
    return f"{cand}/{row['corpus']}/{row['file']}/{row['tokenizer']}"


def d4_approval(entry, row, cand_name, cand_lib, triage_rows):
    """(status, reason) of one transition row: approved, pending,
    rejected, not_applicable, not_listed, unverified or invalid."""
    if entry is None:
        return "pending", "no D4 entry"
    dec = entry.get("owner_decision")
    if dec is None:
        return "pending", f"{entry.get('id')}: owner decision pending"
    if not isinstance(dec, dict) or dec.get("decision") not in ("accept", "reject"):
        return "invalid", f"{entry.get('id')}: owner_decision is not in the decision format"
    if dec.get("decision") == "reject":
        return "rejected", f"{entry.get('id')}: rejected by the owner"
    if not cand_lib or dec.get("candidate_library_sha256") != cand_lib:
        return "not_applicable", (f"{entry.get('id')}: decided for "
                                  f"{str(dec.get('candidate_library_sha256'))[:16]}, "
                                  f"not this candidate")
    want = {"corpus": row["corpus"], "file": row["file"], "tokenizer": row["tokenizer"],
            "outcome": row["candidate"]}
    if not any(isinstance(r, dict) and r == want for r in dec.get("rows") or []):
        return "not_listed", f"{entry.get('id')}: the decision does not list this row"
    need = entry.get("requires_triage_class")
    if need:
        t = triage_rows.get(triage_key(cand_name, row))
        if t is None:
            return "unverified", f"{entry.get('id')}: no T2c row of the candidate"
        if t.get("class") != need:
            return "unverified", f"{entry.get('id')}: T2c class {t.get('class')}, needs {need}"
        if not t.get("invalid_documents") or t.get("documents_missing"):
            return "unverified", (f"{entry.get('id')}: T2c checked "
                                  f"{t.get('invalid_documents', 0) - t.get('documents_missing', 0)}"
                                  f" of {t.get('invalid_documents', 0)} invalid documents")
    return "approved", f"{entry.get('id')}: accepted by {dec.get('by', '?')} {dec.get('date', '')}"


def transitions(line, cand, d4, triage_rows):
    rows = []
    for snap, files in line.files.items():
        if snap not in cand.files:
            continue
        cfiles = cand.files[snap]
        for key, rec in sorted(files.items()):
            if rec.get("error_class") != "compiled":
                continue
            for tok, g in sorted((rec.get("generatable") or {}).items()):
                if g["outcome"] != "generatable":
                    continue
                state, stage, code, counts = result_state(cfiles.get(key), tok)
                if state == "generatable":
                    continue
                e = d4_entry(d4, snap, key[1], tok, state)
                row = {
                    "line": line.name, "corpus": snap, "dataset": key[0], "file": key[1],
                    "tokenizer": tok, "candidate": state, "stage": stage, "code": code,
                    "line_passes": counts_text(g.get("counts")),
                    "candidate_passes": counts_text(counts),
                    "class": class_hint(state),
                    "d4": e.get("id") if e else None,
                }
                row["approval"], row["approval_reason"] = d4_approval(
                    e, row, cand.name, cand.library, triage_rows)
                rows.append(row)
    return rows


def gains(line, cand):
    n = 0
    for snap, files in line.files.items():
        if snap not in cand.files:
            continue
        for key, crec in cand.files[snap].items():
            for tok, g in (crec.get("generatable") or {}).items():
                if g["outcome"] != "generatable":
                    continue
                state, *_ = result_state(files.get(key), tok)
                if state != "generatable":
                    n += 1
    return n


def triage_rows_of(cand):
    rows = []
    for snap, files in cand.files.items():
        for key, rec in sorted(files.items()):
            for tok, g in sorted((rec.get("generatable") or {}).items()):
                if g["outcome"] in TRIAGE_OUTCOMES:
                    rows.append({"corpus": snap, "file": key[1], "tokenizer": tok,
                                 "outcome": g["outcome"], "stage": g.get("stage"),
                                 "code": g.get("code"), "passes": counts_text(g.get("counts"))})
    return rows


# --- oracle -------------------------------------------------------------

def _counter(values):
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def oracle_content_problems(doc):
    """[problem] of one run_oracle.py report (one dialect), empty when the
    report is evidence about its library: rows of its dialect; at least one
    compiled schema and one engine verdict (accept or reject); no compile
    refusal of the call itself (ORACLE_CALL_ERRORS: a single one voids the
    report, whatever the other rows say); every row consistent (SKIPPED
    exactly when the schema did not compile and a refusal is given, a
    verdict behind PASS and MISMATCH, an error behind ENGINE_ERROR); and a
    summary that counts the rows (rows, outcomes, refusal statuses, the
    per-file and per-draft counters). Used by acceptance_compare, the R6
    oracle launcher and the line registration alike."""
    if not isinstance(doc, dict):
        return ["not a run_oracle.py report"]
    drafts = (doc.get("header") or {}).get("drafts") or []
    rows = doc.get("rows")
    if not isinstance(rows, list) or not rows:
        return ["no rows"]
    problems = []
    bad_rows = []
    call_errors = []
    compiled = verdicts = 0
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            bad_rows.append(f"row {i} is not an object")
            continue
        oc, comp, eng, ref = r.get("outcome"), r.get("schema_compiled"), r.get("engine"), r.get("refusal")
        status = ref.get("status") if isinstance(ref, dict) else None
        if status in ORACLE_CALL_ERRORS:
            call_errors.append(f"{status}: {ref.get('message')!r}")
        if len(drafts) == 1 and r.get("draft") != drafts[0]:
            bad_rows.append(f"row {i}: draft {r.get('draft')!r}")
        if oc not in ORACLE_OUTCOMES:
            bad_rows.append(f"row {i}: outcome {oc!r}")
        elif oc == "SKIPPED":
            if comp is not False or status is None or eng is not None:
                bad_rows.append(f"row {i}: SKIPPED with schema_compiled {comp!r}, "
                                f"refusal {status!r}, engine {eng!r}")
        elif comp is not True or ref is not None:
            bad_rows.append(f"row {i}: {oc} with schema_compiled {comp!r}, refusal {status!r}")
        elif oc in ("PASS", "MISMATCH") and eng not in ENGINE_VERDICTS:
            bad_rows.append(f"row {i}: {oc} without an engine verdict ({eng!r})")
        elif oc == "ENGINE_ERROR" and not str(eng).startswith("error:"):
            bad_rows.append(f"row {i}: ENGINE_ERROR without an engine error ({eng!r})")
        compiled += comp is True
        verdicts += comp is True and eng in ENGINE_VERDICTS
    if call_errors:
        problems.append(f"{len(call_errors)} compile refusals of the call itself, "
                        f"e.g. {call_errors[0]} (harness and core disagree)")
    if bad_rows:
        problems.append(f"{len(bad_rows)} inconsistent rows, e.g. {bad_rows[0]}")
    if not compiled:
        problems.append("no schema compiled")
    elif not verdicts:
        problems.append("no engine verdict")
    s = doc.get("summary") or {}
    outcomes = _counter(r.get("outcome") for r in rows if isinstance(r, dict))
    statuses = _counter(r["refusal"].get("status") for r in rows
                        if isinstance(r, dict) and isinstance(r.get("refusal"), dict))
    wrong = []
    if s.get("rows") != len(rows):
        wrong.append(f"rows {s.get('rows')!r} for {len(rows)}")
    if (s.get("by_outcome") or {}) != outcomes:
        wrong.append("by_outcome")
    if (s.get("by_refusal_status") or {}) != statuses:
        wrong.append("by_refusal_status")
    files = s.get("by_file")
    if not isinstance(files, dict):
        wrong.append("by_file missing")
    else:
        for oc, key in ORACLE_FILE_KEYS.items():
            if sum(f.get(key, 0) for f in files.values()) != outcomes.get(oc, 0):
                wrong.append(f"by_file {key}")
        if sum(f.get("compiled", 0) for f in files.values()) != compiled:
            wrong.append("by_file compiled")
    per_draft = (s.get("by_draft") or {}).get(drafts[0]) if len(drafts) == 1 else None
    if not isinstance(per_draft, dict) or per_draft.get("rows") != len(rows) \
            or (per_draft.get("by_outcome") or {}) != outcomes:
        wrong.append("by_draft")
    if wrong:
        problems.append("the summary does not count the rows: " + ", ".join(wrong))
    return problems


def oracle_load(path):
    """One run_oracle.py report -> {dialect, header, counts, rows,
    problems}; the counts come from the rows (a missing outcome is 0) and
    must agree with the report summary; problems: oracle_content_problems."""
    doc = load_json(path)
    h = doc["header"]
    drafts = h.get("drafts") or []
    if len(drafts) != 1 or drafts[0] not in DIALECTS:
        raise Unusable(f"{path}: expected one of {DIALECTS} per report, got {drafts}")
    if h.get("profile") != "spec-v1":
        raise Unusable(f"{path}: profile {h.get('profile')}, the T3 control uses spec-v1")
    rows = {}
    for r in doc.get("rows") or []:
        rid = (r.get("suite_file"), r.get("case"), r.get("test_index"))
        if rid in rows:
            raise Unusable(f"{path}: duplicate row {rid}")
        rows[rid] = r.get("outcome")
    counts = dict.fromkeys(ORACLE_OUTCOMES, 0)
    for oc in rows.values():
        counts[oc] = counts.get(oc, 0) + 1
    summ = (doc.get("summary") or {}).get("by_outcome") or {}
    for oc in set(counts) | set(summ):
        if counts.get(oc, 0) != summ.get(oc, 0):
            raise Unusable(f"{path}: {oc} {counts.get(oc, 0)} in the rows, "
                           f"{summ.get(oc, 0)} in the summary")
    return {"dialect": drafts[0], "path": path, "sha256": sha256_file(path), "header": h,
            "counts": counts, "rows": rows, "problems": oracle_content_problems(doc)}


def oracle_add(store, name, path):
    rep = oracle_load(path)
    per = store.setdefault(name, {})
    if rep["dialect"] in per:
        raise Unusable(f"{name}: two oracle reports for {rep['dialect']}")
    per[rep["dialect"]] = rep


def oracle_lines_load(path, store):
    """Add the reports of --oracle-lines to store; returns ({line: entry}
    of every listed line, doc). A file whose sha256 differs from the
    list is unusable input."""
    if not path:
        return {}, None
    doc = load_json(path)
    entries = {}
    for line in doc.get("lines", []):
        if line["name"] in entries:
            raise Unusable(f"{line['name']}: listed twice in the oracle line list")
        entries[line["name"]] = line
        if not line.get("reports"):
            continue
        for dialect, r in line["reports"].items():
            p = r["path"] if os.path.isabs(r["path"]) else os.path.join(ROOT, r["path"])
            if not os.path.exists(p):
                raise Unusable(f"{line['name']}: oracle report {r['path']} missing")
            if sha256_file(p) != r["sha256"]:
                raise Unusable(f"{line['name']}: {r['path']} differs from the sha256 "
                               "in the oracle line list")
            rep = oracle_load(p)
            if rep["dialect"] != dialect:
                raise Unusable(f"{line['name']}: {r['path']} is {rep['dialect']}, not {dialect}")
            per = store.setdefault(line["name"], {})
            if dialect in per:
                raise Unusable(f"{line['name']}: two oracle reports for {dialect}")
            per[dialect] = rep
    return entries, doc


def utc_seconds(text):
    return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc).timestamp()


def archive_binding(line_lib, reports, binding):
    """(ok, detail) of the archive binding of reports that predate the
    library_sha256 header field: every header names the binding's library
    path, that file has the line's sha256 and was last written before the
    first report, and every listed document has its sha256 and contains
    the quoted statements."""
    if not binding:
        return False, (f"{', '.join(sorted(reports))}: no library_sha256 in the header and "
                       "no archive binding in the oracle line list")
    if binding.get("kind") != "archive":
        return False, f"binding kind {binding.get('kind')!r}, expected 'archive'"
    rel = binding.get("library_path") or ""
    lib = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
    problems = []
    for d, r in sorted(reports.items()):
        hp = os.path.normpath(str(r["header"].get("library") or ""))
        if not rel or (hp != os.path.normpath(lib)
                       and not hp.endswith(os.sep + os.path.normpath(rel))):
            problems.append(f"{d}: header library {hp} is not {rel or '-'}")
    try:
        first = min(utc_seconds(r["header"]["date_utc"]) for r in reports.values())
    except (KeyError, TypeError, ValueError):
        first = None
        problems.append("a report without header date_utc")
    mtime = None
    if not os.path.isfile(lib):
        problems.append(f"archive library {rel or '-'} missing")
    else:
        if sha256_file(lib) != line_lib:
            problems.append(f"archive library {rel} is not {line_lib[:16]}")
        mtime = os.stat(lib).st_mtime
        if first is not None and mtime > first:
            problems.append(f"archive library {rel} was written after the first report")
    docs = binding.get("documents") or []
    if not docs:
        problems.append("no document in the archive binding")
    for doc in docs:
        dp = doc.get("path") or ""
        full = dp if os.path.isabs(dp) else os.path.join(ROOT, dp)
        if not os.path.isfile(full):
            problems.append(f"document {dp or '-'} missing")
            continue
        if sha256_file(full) != doc.get("sha256"):
            problems.append(f"document {dp} differs from its sha256 in the binding")
            continue
        with open(full, encoding="utf-8") as f:
            text = f.read()
        absent = [q for q in doc.get("contains") or [] if q not in text]
        if absent or not doc.get("contains"):
            problems.append(f"document {dp} does not state {absent[0] if absent else 'anything'!r}")
    if problems:
        return False, "archive binding: " + "; ".join(problems)
    when = datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return True, (f"archive binding: {', '.join(sorted(reports))} name {rel} "
                  f"({line_lib[:16]}, written {when}, before the first report), "
                  f"{len(docs)} documents")


def oracle_binding(name, line_lib, reports, entry):
    """(ok, detail): the oracle reports of a line belong to its library."""
    if line_lib is None:
        return False, f"{name} has no single library sha256 in its coverage reports"
    problems, notes = [], []
    if entry is not None and entry.get("library_sha256") != line_lib:
        problems.append(f"the oracle line list names {str(entry.get('library_sha256'))[:16]}, "
                        f"the coverage reports {line_lib[:16]}")
    unbound = {}
    for d, r in sorted(reports.items()):
        got = r["header"].get("library_sha256")
        if got is None:
            unbound[d] = r
        elif got != line_lib:
            problems.append(f"{d} report names library {got[:16]}, the line is {line_lib[:16]}")
    if len(unbound) < len(reports):
        notes.append(f"{len(reports) - len(unbound)} reports name {line_lib[:16]} in the header")
    if unbound:
        ok, detail = archive_binding(line_lib, unbound, (entry or {}).get("binding"))
        (notes if ok else problems).append(detail)
    if problems:
        return False, "; ".join(problems)
    return True, "; ".join(notes)


def oracle_exception(entry, cand_lib, with_data):
    """(approved, detail) of a line listed without oracle data. Approved
    only with an owner decision of the decision_format of the oracle line
    list that covers this candidate and names a control line with data."""
    reason = entry.get("missing_reason") or "no reason given"
    dec = entry.get("owner_decision")
    if not dec:
        return False, f"no oracle data ({reason}); no owner decision"
    problems = []
    if dec.get("decision") != "accept_without_oracle":
        problems.append(f"decision {dec.get('decision')!r}")
    if not cand_lib or cand_lib not in (dec.get("candidates") or []):
        problems.append("the decision does not cover this candidate")
    if dec.get("control_line") not in with_data:
        problems.append(f"control line {dec.get('control_line')!r} has no oracle data here")
    if not (dec.get("by") and dec.get("date")):
        problems.append("the decision has no author or date")
    if problems:
        return False, (f"no oracle data ({reason}); the owner decision does not apply: "
                       + "; ".join(problems))
    return True, (f"owner exception by {dec['by']} on {dec['date']}: control line "
                  f"{dec['control_line']} ({reason})")


def oracle_input_diffs(cand_rep, line_rep):
    out = []
    hc, hl = cand_rep["header"], line_rep["header"]
    for k in ("suite", "include_optional", "profile", "serializer"):
        if hc.get(k) != hl.get(k):
            out.append(k)
    if (hc.get("validator") or {}).get("actual") != (hl.get("validator") or {}).get("actual"):
        out.append("validator")
    if set(cand_rep["rows"]) != set(line_rep["rows"]):
        out.append(f"rows ({len(set(cand_rep['rows']) ^ set(line_rep['rows']))} differ)")
    return out


# --- triage -------------------------------------------------------------

def triage_load(path, cand):
    """T2c triage JSON of the candidate -> ({key: generatable row}, check
    detail, ok)."""
    if not path:
        return {}, "T2c triage of the candidate not given", False
    doc = load_json(path)
    ins = (doc.get("inputs") or {}).get("coverage") or []
    want = {sha256_file(p) for p in cand.paths.values()}
    got = {i.get("sha256") for i in ins if i.get("build") == cand.name}
    problems = []
    if doc.get("gate_build") != cand.name:
        problems.append(f"gate build {doc.get('gate_build')}, not {cand.name}")
    if got != want:
        problems.append("triage inputs are not the candidate's reports")
    if any(i.get("library_sha256") != cand.library for i in ins if i.get("build") == cand.name):
        problems.append("triage inputs name another library")
    if doc.get("exit_status") != 0:
        n = (doc.get("summary") or {}).get("n_unclassified")
        problems.append(f"triage gate failed (exit {doc.get('exit_status')}, "
                        f"{n} unclassified rows)")
    rows = {r["key"]: r for r in doc.get("generatable_rows") or []}
    if problems:
        return rows, "; ".join(problems), False
    return rows, f"gate passed on {cand.name} ({sha256_file(path)[:16]})", True


# --- main ---------------------------------------------------------------

def compare(args):
    cand = Build(*named(args.candidate))
    lines = [Build(*named(s)) for s in args.line]
    if any(b.name == cand.name for b in lines) or len({b.name for b in lines}) != len(lines):
        raise Unusable("build names must be distinct")
    baseline = lines[0]
    d4_cand, d4 = d4_load(args.d4)
    notes = load_json(args.protocol_notes).get("entries", []) if args.protocol_notes else []
    oracle = {}
    for spec in args.oracle:
        oracle_add(oracle, *named(spec))
    line_entries, _ol = oracle_lines_load(args.oracle_lines, oracle)
    triage_rows, triage_detail, triage_ok = triage_load(args.triage, cand)
    intermediate = [s for s in SNAPSHOTS if s not in cand.files]

    builds = lines + [cand]
    totals = {}
    for b in builds:
        for snap, files in b.files.items():
            totals.setdefault(snap, {})[b.name] = {
                "buckets": bucket_counts(files),
                "outcomes": {tok: outcome_counts(files, tok) for tok in b.tokenizers(snap)},
            }

    checks = []

    def check(name, status, detail):
        if status is True:
            status = "ok"
        elif status is False:
            status = "fail"
        checks.append({"check": name, "status": status, "detail": detail})

    # identity and protocol
    for b in builds:
        check(f"library:{b.name}", b.library is not None, b.library_text())
    proto_rows = []
    for line in lines:
        for snap in SNAPSHOTS:
            if snap not in line.files:
                check(f"protocol:{line.name}:{snap}", False, f"{line.name} has no {snap} report")
                continue
            if snap not in cand.files:
                check(f"protocol:{line.name}:{snap}", "pending", f"candidate has no {snap} report")
                check(f"corpus:{line.name}:{snap}", "pending", f"candidate has no {snap} report")
                continue
            diffs = protocol_diffs(cand, line, snap, notes)
            proto_rows += diffs
            open_diffs = [d["parameter"] for d in diffs if not d["explanation"]]
            check(f"protocol:{line.name}:{snap}", not open_diffs,
                  (f"unexplained: {', '.join(open_diffs)}" if open_diffs else
                   f"{len(diffs)} differences, each explained in the protocol notes"))
            ck, lk = set(cand.files[snap]), set(line.files[snap])
            check(f"corpus:{line.name}:{snap}", ck == lk,
                  f"{len(ck)} results; missing on the candidate {len(lk - ck)}, "
                  f"extra {len(ck - lk)}")
    sample = {}
    for b in builds:
        for snap in b.files:
            for tok, bad in sample_violations(b, snap).items():
                sample[(b.name, snap, tok)] = bad
                check(f"hf_sample:{b.name}:{snap}:{tok}", not bad,
                      (f"{len(bad)} compiled schemas break the sample rule, e.g. "
                       f"{', '.join(bad[:3])}") if bad else "fixed sample rule holds")

    # goals and coverage
    if "maskbench" in cand.files:
        o = totals["maskbench"][cand.name]["outcomes"].get(GOAL_TOKENIZER)
        if o is None:
            check("goal_unavailable", False, f"no {GOAL_TOKENIZER} results")
        else:
            share = o["engine_unavailable"] / o["base"] if o["base"] else 1.0
            check("goal_unavailable", share <= GOAL_UNAVAILABLE_SHARE,
                  f"maskbench {GOAL_TOKENIZER}: {o['engine_unavailable']}/{o['base']} "
                  f"({pct(o['engine_unavailable'], o['base'])}, goal <= 1%)")
    else:
        check("goal_unavailable", "pending", "maskbench report of the candidate missing")

    for snap in SNAPSHOTS:
        if snap not in cand.files:
            continue
        if snap not in baseline.files:
            check(f"coverage_baseline:{snap}", False, f"{baseline.name} has no {snap} report")
            continue
        ref_toks = totals[snap][baseline.name]["outcomes"]
        for tok in sorted(set(ref_toks) | set(totals[snap][cand.name]["outcomes"])):
            o = totals[snap][cand.name]["outcomes"].get(tok)
            ref = ref_toks.get(tok)
            if o is None or ref is None:
                check(f"coverage_baseline:{snap}:{tok}", False,
                      f"tokenizer {tok} missing on {cand.name if o is None else baseline.name}")
                continue
            check(f"coverage_baseline:{snap}:{tok}", o["generatable"] >= ref["generatable"],
                  f"{o['generatable']} on {cand.name} vs {ref['generatable']} on {baseline.name}")

    defects = sum(o.get("engine_defect", 0) + o.get("validator_error", 0)
                  for snap in cand.files for o in totals[snap][cand.name]["outcomes"].values())
    check("no_defects", defects == 0, f"engine_defect + validator_error results: {defects}")

    # transitions and D4
    trans = []
    for line in lines:
        trans += transitions(line, cand, d4, triage_rows)
    undescribed = sorted({(r["corpus"], r["file"], r["tokenizer"]) for r in trans if not r["d4"]})
    check("transitions_described", not undescribed,
          f"{len(trans)} transition rows, {len(undescribed)} results without a D4 entry")
    by_status = {}
    for r in trans:
        by_status[r["approval"]] = by_status.get(r["approval"], 0) + 1
    check("transitions_approved", by_status.get("approved", 0) == len(trans),
          ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())) or "no transitions")
    if not args.d4:
        check("d4_candidate", not trans, "D4 list not given"
              + ("" if trans else "; no transition needs one"))
    else:
        check("d4_candidate", bool(cand.library) and d4_cand == cand.library,
              f"D4 list for {str(d4_cand)[:16]}, candidate {str(cand.library)[:16]}")

    # oracle
    problems = {n: [f"{d}: {p}" for d, r in sorted(per.items()) for p in r["problems"]]
                for n, per in oracle.items()}
    for b in [cand] + lines:
        if b.name in oracle:
            pr = problems[b.name]
            check(f"oracle_content:{b.name}", not pr,
                  ("; ".join(pr[:3]) + (f"; {len(pr) - 3} more" if len(pr) > 3 else "")) if pr
                  else "rows, compiled schemas and engine verdicts in every report, "
                       "summaries agree")

    def usable(per, d):
        return d in per and not per[d]["problems"]

    unusable = [b.name for b in lines if b.name in oracle and problems[b.name]]
    with_data = [b.name for b in lines if b.name in oracle and not problems[b.name]]
    exceptions = {}
    for b in lines:
        e = line_entries.get(b.name)
        if b.name not in oracle and e is not None:
            exceptions[b.name] = oracle_exception(e, cand.library, with_data)
            check(f"oracle_line:{b.name}", exceptions[b.name][0], exceptions[b.name][1])
    bindings = {}
    for b in lines:
        if b.name in oracle:
            bindings[b.name] = oracle_binding(b.name, b.library, oracle[b.name],
                                              line_entries.get(b.name))
            check(f"oracle_binding:{b.name}", *bindings[b.name])
    for b in [cand] + lines:
        if b is not cand and exceptions.get(b.name, (False,))[0]:
            continue
        have = sorted(oracle.get(b.name, {}))
        absent = [d for d in DIALECTS if d not in have]
        check(f"oracle_dialects:{b.name}", not absent,
              f"missing {', '.join(absent)}" if absent else "all five dialects")
    co = oracle.get(cand.name, {})
    wrong = [d for d, r in sorted(co.items())
             if not cand.library or r["header"].get("library_sha256") != cand.library]
    check("oracle_identity", bool(co) and not wrong,
          (f"reports without the candidate's library sha256: {', '.join(wrong)}" if wrong
           else ("no oracle report of the candidate" if not co else
                 "every report names the candidate's library")))
    if usable(co, "draft2020-12"):
        ee = co["draft2020-12"]["counts"]["ENGINE_ERROR"]
        check("goal_oracle", ee <= GOAL_ORACLE_2020_12, f"2020-12 ENGINE_ERROR {ee} (goal <= 10)")
    elif "draft2020-12" in co:
        check("goal_oracle", False, "oracle 2020-12 report of the candidate is no evidence "
              f"(oracle_content:{cand.name})")
    else:
        check("goal_oracle", False, "oracle 2020-12 report of the candidate not given")
    undeclared = [b.name for b in lines if b.name not in oracle and b.name not in exceptions]
    unapproved = [n for n, (ok, _d) in sorted(exceptions.items()) if not ok]
    check("oracle_lines", bool(with_data) and not undeclared and not unapproved and not unusable,
          f"with data: {', '.join(with_data) or '-'}; without data: "
          + (", ".join(f"{n} ({'approved exception' if ok else 'no approved exception'})"
                       for n, (ok, _d) in sorted(exceptions.items())) or "-")
          + (f"; reports that are no evidence: {', '.join(unusable)}" if unusable else "")
          + (f"; neither data nor an entry: {', '.join(undeclared)}" if undeclared else ""))
    oracle_lost = []
    # A line or candidate report that is no evidence is never compared: its
    # dialect is missing from `both`, so oracle_inputs and oracle_pass fail.
    for name in with_data + unusable:
        lo = oracle[name]
        diffs, lower = [], []
        for d in DIALECTS:
            if not (usable(lo, d) and usable(co, d)):
                continue
            dd = oracle_input_diffs(co[d], lo[d])
            if dd:
                diffs.append(f"{d}: {', '.join(dd)}")
            if co[d]["counts"]["PASS"] < lo[d]["counts"]["PASS"]:
                lower.append(f"{d} {co[d]['counts']['PASS']} < {lo[d]['counts']['PASS']}")
            for rid, oc in sorted(lo[d]["rows"].items(), key=str):
                if oc == "PASS" and co[d]["rows"].get(rid) not in (None, "PASS"):
                    oracle_lost.append({"line": name, "dialect": d, "file": rid[0],
                                        "case": rid[1], "test": rid[2],
                                        "candidate": co[d]["rows"].get(rid)})
        both = [d for d in DIALECTS if usable(lo, d) and usable(co, d)]
        no_evidence = [d for d in DIALECTS if (d in lo and not usable(lo, d))
                       or (d in co and not usable(co, d))]
        unusable_note = (f"; no evidence in {', '.join(no_evidence)} (oracle_content)"
                         if no_evidence else "")
        check(f"oracle_inputs:{name}", len(both) == len(DIALECTS) and not diffs,
              ("; ".join(diffs) if diffs else
               (f"compared dialects: {', '.join(both) or '-'}")) + unusable_note)
        check(f"oracle_pass:{name}", len(both) == len(DIALECTS) and not lower,
              ("; ".join(lower) if lower else
               ("PASS per dialect (candidate/line) " + ", ".join(
                   f"{d} {co[d]['counts']['PASS']}/{lo[d]['counts']['PASS']}" for d in both))
               if both else "no dialect of the candidate to compare") + unusable_note)

    check("triage", triage_ok, triage_detail)

    failing = [c["check"] for c in checks if c["status"] == "fail"]
    pending = [c["check"] for c in checks if c["status"] == "pending"]
    if intermediate:
        verdict, status = "intermediate", EXIT_INTERMEDIATE
    elif failing or pending:
        verdict, status = "not_admitted", EXIT_NOT_ADMITTED
    else:
        verdict, status = "admitted", EXIT_ADMITTED

    d4_unused = [e for e in d4 if e.get("id") not in {r["d4"] for r in trans if r["d4"]}]
    pending_rows = [{"d4": r["d4"], "approval": r["approval"],
                     "row": {"corpus": r["corpus"], "file": r["file"],
                             "tokenizer": r["tokenizer"], "outcome": r["candidate"]}}
                    for r in trans if r["approval"] != "approved"]
    inputs = {
        "candidate": {"name": cand.name, "library_sha256": cand.library,
                      "libraries": cand.libraries,
                      "reports": {s: {"path": p, "sha256": sha256_file(p)}
                                  for s, p in cand.paths.items()}},
        "lines": [{"name": b.name, "library_sha256": b.library, "libraries": b.libraries,
                   "reports": {s: {"path": p, "sha256": sha256_file(p)}
                               for s, p in b.paths.items()}} for b in lines],
        "oracle": {n: {d: {"path": r["path"], "sha256": r["sha256"],
                           "library_sha256": r["header"].get("library_sha256")}
                       for d, r in sorted(per.items())} for n, per in sorted(oracle.items())},
        "oracle_line_exceptions": {n: {"approved": ok, "detail": d}
                                   for n, (ok, d) in sorted(exceptions.items())},
        "oracle_bindings": {n: {"ok": ok, "detail": d} for n, (ok, d) in sorted(bindings.items())},
    }
    for key, path in (("d4", args.d4), ("triage", args.triage), ("oracle_lines", args.oracle_lines),
                      ("protocol_notes", args.protocol_notes)):
        inputs[key] = {"path": path, "sha256": sha256_file(path)} if path else None
    return {
        "acceptance": {"verdict": verdict, "exit_status": status, "intermediate": intermediate,
                       "failing": failing, "pending": pending},
        "inputs": inputs,
        "intermediate": intermediate,
        "totals": totals,
        "checks": checks,
        "protocol_differences": proto_rows,
        "transitions": trans,
        "d4_pending_rows": pending_rows,
        "gains": {line.name: gains(line, cand) for line in lines},
        "triage_rows": triage_rows_of(cand),
        "oracle_counts": {n: {d: r["counts"] for d, r in sorted(per.items())}
                          for n, per in sorted(oracle.items())},
        "oracle_pass_lost": oracle_lost,
        "oracle_problems": {n: {d: r["problems"] for d, r in sorted(per.items()) if r["problems"]}
                            for n, per in sorted(oracle.items()) if problems[n]},
        "d4_entries_without_transition": [e.get("id") for e in d4_unused],
    }, builds, cand, baseline


def cell(v):
    return str(v if v is not None else "-").replace("|", "\\|")


def markdown(out, builds, cand, baseline):
    acc = out["acceptance"]
    md = ["# T3 coverage control and acceptance verdict", "",
          f"**Verdict: {acc['verdict']}** (exit status {acc['exit_status']}).", ""]
    if acc["intermediate"]:
        md += [f"Intermediate: the candidate has no report for {', '.join(acc['intermediate'])}; "
               "the checks of that corpus are pending and this is not an acceptance.", ""]
    if acc["failing"]:
        md += ["Failing checks: " + ", ".join(f"`{c}`" for c in acc["failing"]) + ".", ""]
    if acc["pending"]:
        md += ["Pending checks: " + ", ".join(f"`{c}`" for c in acc["pending"]) + ".", ""]
    md += ["Generated by `benchmarks/t3/tools/acceptance_compare.py` from saved reports;",
           "`generatable` is the result of K generation passes on the listed "
           "tokenizers, not a proof for every continuation.", "", "## Inputs", "",
           "| build | role | library sha256 | report | report sha256 |", "|---|---|---|---|---|"]
    ins = out["inputs"]
    for b in builds:
        role = "candidate" if b is cand else ("baseline" if b is baseline else "line")
        for s, p in b.paths.items():
            md.append(f"| {b.name} | {role} | `{(b.libraries.get(s) or '-')[:16]}` | `{p}` | "
                      f"`{sha256_file(p)[:16]}` |")
    for n, per in ins["oracle"].items():
        for d, r in per.items():
            md.append(f"| {n} | oracle {d} | `{(r['library_sha256'] or '-')[:16]}` | "
                      f"`{r['path']}` | `{r['sha256'][:16]}` |")
    for key in ("d4", "triage", "oracle_lines", "protocol_notes"):
        if ins[key]:
            md.append(f"| - | {key} | - | `{ins[key]['path']}` | `{ins[key]['sha256'][:16]}` |")
    md += ["", "## Checks", "", "| check | result | detail |", "|---|---|---|"]
    md += [f"| {c['check']} | {c['status']} | {cell(c['detail'])} |" for c in out["checks"]]
    if out["protocol_differences"]:
        md += ["", "## Protocol differences", "",
               "| line | corpus | parameter | candidate | line | explanation |",
               "|---|---|---|---|---|---|"]
        md += ["| " + " | ".join(cell(r[k]) for k in ("line", "corpus", "parameter", "candidate",
                                                     "line_value")) + " | "
               + cell(r["explanation"] or "NOT EXPLAINED") + " |"
               for r in out["protocol_differences"]]
    totals = out["totals"]
    for snap in SNAPSHOTS:
        if snap not in totals:
            continue
        names = [b.name for b in builds if b.name in totals[snap]]
        md += ["", f"## {snap}: main compile", "", "| bucket | " + " | ".join(names) + " |",
               "|---|" + "---:|" * len(names)]
        for k in BUCKETS:
            md.append(f"| {k} | " + " | ".join(
                str(totals[snap][n]["buckets"].get(k, 0)) for n in names) + " |")
        toks = []
        for n in names:
            for t in totals[snap][n]["outcomes"]:
                if t not in toks:
                    toks.append(t)
        for t in toks:
            md += ["", f"### {snap}, tokenizer {t}", "", "| outcome | " + " | ".join(names) + " |",
                   "|---|" + "---:|" * len(names)]
            for k in OUTCOMES + ("base",):
                cells = []
                for n in names:
                    o = totals[snap][n]["outcomes"].get(t)
                    if o is None:
                        cells.append("-")
                    elif k in ("generatable", "engine_unavailable"):
                        cells.append(f"{o[k]} ({pct(o[k], o['base'])})")
                    else:
                        cells.append(str(o.get(k, 0)))
                md.append(f"| {k} | " + " | ".join(cells) + " |")
    if out["oracle_counts"]:
        md += ["", "## Oracle, spec-v1", "", "| build | dialect | " + " | ".join(ORACLE_OUTCOMES)
               + " |", "|---|---|" + "---:|" * len(ORACLE_OUTCOMES)]
        for n, per in out["oracle_counts"].items():
            for d, c in per.items():
                md.append(f"| {n} | {d} | " + " | ".join(str(c.get(k, 0)) for k in ORACLE_OUTCOMES)
                          + " |")
        for n, per in out["oracle_problems"].items():
            md += ["", f"Oracle reports of {n} that are no evidence:", ""]
            md += [f"- {d}: {cell(p)}" for d, ps in per.items() for p in ps]
        if out["oracle_pass_lost"]:
            md += ["", "Rows PASS on a line and not on the candidate:", "",
                   "| line | dialect | file | case | test | candidate |", "|---|---|---|---|---|---|"]
            md += ["| " + " | ".join(cell(r[k]) for k in ("line", "dialect", "file", "case", "test",
                                                         "candidate")) + " |"
                   for r in out["oracle_pass_lost"]]
    md += ["", "## Transitions", "",
           "Results generatable on a line and not on the candidate, compile refusals",
           "included. Class C goes to the availability residuals, T2c rows to the",
           "triage; every row needs an owner approval bound to this candidate (D4).", "",
           "| line | corpus | file | tokenizer | candidate | stage | code | passes on line | "
           "passes on candidate | class | D4 | approval |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in out["transitions"]:
        md.append("| " + " | ".join(cell(r[k]) for k in (
            "line", "corpus", "file", "tokenizer", "candidate", "stage", "code", "line_passes",
            "candidate_passes", "class", "d4")) + f" | {r['approval']}: {cell(r['approval_reason'])} |")
    md += ["", "Results generatable on the candidate and not on a line: " + ("; ".join(
        f"{v} against {n}" for n, v in out["gains"].items()) or "-") + ".", ""]
    triage = out["triage_rows"]
    md += ["## Results to triage (T2c)", "",
           f"{len(triage)} candidate results end in invalid_document, engine_defect or "
           "validator_error; `benchmarks/triage_semantic.py` classifies them.", "",
           "| corpus | file | tokenizer | outcome | stage | code | passes |",
           "|---|---|---|---|---|---|---|"]
    for r in triage:
        md.append("| " + " | ".join(cell(r[k]) for k in (
            "corpus", "file", "tokenizer", "outcome", "stage", "code", "passes")) + " |")
    if out["d4_entries_without_transition"]:
        md += ["", "## D4 entries without a transition in this comparison", ""]
        md += [f"- {i}" for i in out["d4_entries_without_transition"]]
    return "\n".join(md) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--line", action="append", required=True)
    ap.add_argument("--d4", default="")
    ap.add_argument("--triage", default="")
    ap.add_argument("--oracle", action="append", default=[])
    ap.add_argument("--oracle-lines", default="")
    ap.add_argument("--protocol-notes", default="")
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--out-json", required=True)
    args = ap.parse_args(argv)
    try:
        out, builds, cand, baseline = compare(args)
    except (Unusable, OSError, KeyError, ValueError) as e:
        print(f"acceptance_compare: unusable input: {e}", file=sys.stderr)
        return EXIT_UNUSABLE
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    with open(args.out_md, "w", encoding="utf-8") as f:
        f.write(markdown(out, builds, cand, baseline))
    print(json.dumps({"acceptance": out["acceptance"], "transitions": len(out["transitions"])},
                     ensure_ascii=False, indent=1))
    return out["acceptance"]["exit_status"]


if __name__ == "__main__":
    sys.exit(main())
