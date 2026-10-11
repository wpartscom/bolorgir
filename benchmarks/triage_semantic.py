#!/usr/bin/env python3
"""Triage table of the semantic correctness signals (classes A-E).

Inputs are evidence files, never live runs:

  --published      the semantic coverage reports whose counters are
                   rechecked (per-file records with failure kinds and
                   instance outcomes); a path or a git object REV:PATH;
                   repeatable
  --counters NAME=PATH
                   benchmarks/recheck_semantic_counters.py output for a
                   build (the last one given is the final build); without
                   it the published-counter section is left out (the
                   triage of one candidate's generatable outcomes)
  --residuals NAME=PATH
                   benchmarks/recheck_cert_residuals.py output (the
                   valid-rejects rows) for a build, same NAMEs
  --coverage NAME=PATH
                   generatable-mode coverage reports of a build
                   (bench_jsb_coverage.py --generatable); repeatable
  --doc-rechecks NAME=PATH
                   benchmarks/recheck_invalid_documents.py output: the
                   verdict of build NAME (the final one) on each invalid
                   document that the draft-04 rule does not explain
  --manual PATH    classes assigned by hand: {"<case key>": {"class",
                   "note"}}; a manual entry overrides the rules, except
                   on an invalid_document row of the generatable mode,
                   where it only adds its note to the class the evidence
                   gives. An entry {"exception": "<decision>"} without a
                   class records an accepted exception: the row stays
                   unclassified and shows the decision. Its
                   library_sha256, schema_sha256 and missing_seeds (the
                   seeds of the invalid_document passes without a
                   document) must match the evidence, or the exception
                   is not applied
  --gate-build NAME
                   the exit status checks the final build NAME: no row
                   of NAME and no published counter is unclassified, and
                   an unclassified row of an earlier build carries an
                   accepted exception. Without it every unclassified row
                   fails, accepted exceptions included

Class rules (the published symptom, confirmed by the reproduction on the
final build):

  D  the reference rejects a document or instance, the schema declares
     draft-04, and the same validator with integral values counted as
     integers accepts it: the documented draft-04 integer spelling
     (docs/supported_features.md section 9).
  B  a dead end, a refused masked token or a rejected valid instance
     whose reproduction completes (or accepts): a completeness defect,
     fixed; the reason names the first build where it is gone.
  C  a published RESOURCE_LIMIT (generation, compile, session), or a
     dead end / refused token whose reproduction ends in RESOURCE_LIMIT,
     CANCELLED or a budget: availability.

Generatable-mode outcomes (--coverage; the last NAME is the final build;
the sidecar next to each report supplies the pass documents): an
engine_defect of an earlier build is B when the same schema and tokenizer
is generatable or incomplete on the final build, or ends in
invalid_document there without any engine_defect pass, C when it is
engine_unavailable there; an invalid_document is D when every
invalid_document pass of the schema and tokenizer has its document in the
sidecar and the draft-04 rule above explains each of them; on an
earlier build it is A (fixed) when the final build accepts none of its
documents that the draft-04 rule does not explain (--doc-rechecks: each
is rejected or ends in an error status before its end, as an instance
that ends in RESOURCE_LIMIT below); it stays unclassified otherwise. Schema results generatable on the first --coverage
build and not on the final one are listed with the pass counts of both
builds (engine_unavailable and incomplete: class C), including results
whose main compile fails on the final build (RESOURCE_LIMIT: class C,
other refusals unclassified) or that are missing there.

Corpus instances in the same reports (MaskBench tests): an invalid
instance the engine accepts while the reference rejects it is D under the
draft-04 rule, A (fixed) when the final build no longer accepts it; a
valid instance the engine rejects is D when the reference rejects it too
(corpus label), B when the final build accepts it, C when it ends in
RESOURCE_LIMIT or CANCELLED there.
Anything else stays "unclassified" until a manual entry decides it.

Outputs a Markdown report (--out-md), the class-D entries
(--deviations, the machine-readable list used by the generative test)
and, with --out-json, every row with its class, the inputs with their
sha256 and the gate result (read by benchmarks/t3/tools/
acceptance_compare.py --triage).
Each entry names its deviations (DEVIATION_REASONS keys); a class-D
manual entry names its own in a `deviation` field.
"""

import argparse
import base64
import collections
import gzip
import hashlib
import json
import os
import subprocess
import sys

_BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BENCH_DIR)
sys.path.insert(0, os.path.join(_ROOT, "tests"))

UNAVAILABLE = ("RESOURCE_LIMIT", "CANCELLED")
FIX_REF = {
    "B": "fixed",
    "C": "availability (RESOURCE_LIMIT residual)",
    "D": "docs/supported_features.md section 9 (draft-04 integer spelling)",
}


DEVIATION_REASONS = {
    "draft04_integer_spelling": FIX_REF["D"],
    "corpus_label": "corpus label: the reference rejects the instance too",
    "reference_accepts": "valid under the reference (exact numbers, ECMA-262 patterns)",
}


def deviation_kind(note, manual_entry=None):
    """The documented deviation (DEVIATION_REASONS key) of a class-D row,
    from its note or the `deviation` field of its manual entry."""
    if manual_entry is not None:
        return manual_entry.get("deviation")
    if note.startswith(FIX_REF["D"]):
        return "draft04_integer_spelling"
    if note.startswith("corpus label"):
        return "corpus_label"
    if note.startswith("valid under the"):
        return "reference_accepts"
    return None


def named(pairs):
    out = []
    for p in pairs or []:
        name, _, path = p.partition("=")
        out.append((name, path))
    return out


def read_input(spec):
    """Bytes of an input: a file path, or a git object `REV:PATH` of this
    repository (a report the working tree has since replaced)."""
    if os.path.exists(spec) or ":" not in spec:
        with open(spec, "rb") as f:
            return f.read()
    return subprocess.run(["git", "-C", _ROOT, "show", spec], check=True,
                          capture_output=True).stdout


def load_json(path):
    """A JSON input (read_input), gzip-compressed when the name ends in .gz."""
    data = read_input(path)
    if path.endswith(".gz"):
        data = gzip.decompress(data)
    return json.loads(data.decode("utf-8"))


def sidecar_of(path):
    """The pass-record sidecar next to a coverage report (path or REV:PATH)."""
    base = path[: -len(".gz")] if path.endswith(".gz") else path
    return base[: -len(".json")] + ".passes.json.gz"


def load_sidecar(path):
    """{(dataset, file): {tokenizer: [pass row]}} of a coverage report's
    sidecar; {} when there is none."""
    side = sidecar_of(path)
    try:
        doc = load_json(side)
    except (OSError, subprocess.CalledProcessError):
        return {}
    return {(d, f): p for d, f, p in doc["rows"]}


def file_sha256(path):
    return hashlib.sha256(read_input(path)).hexdigest()


def jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def corpus_path(snapshot, dataset, fname):
    base = os.path.join(_BENCH_DIR, "external")
    if snapshot == "maskbench":
        return os.path.join(base, "jsonschemabench-maskbench", "maskbench", "data", fname)
    return os.path.join(base, "jsonschemabench", "data", dataset, fname)


def load_schema(snapshot, dataset, fname):
    """-> (schema bytes as compiled by the harness, parsed schema, tests)."""
    with open(corpus_path(snapshot, dataset, fname), "rb") as f:
        raw = f.read()
    if snapshot != "maskbench":
        return raw, json.loads(raw), []
    doc = json.loads(raw)
    data = json.dumps(doc.get("schema"), ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    return data, doc.get("schema"), doc.get("tests") or []


def _gp():
    import generation_passes as gp
    return gp


def is_draft04(schema):
    return _gp().is_draft04(schema)


def draft04_integer_case(schema_bytes, schema, value_text):
    """The documented draft-04 integer deviation explains the rejection of
    value_text (generation_passes.draft04_integer_spelling)."""
    return isinstance(schema, dict) and _gp().draft04_integer_spelling(
        schema_bytes.decode("utf-8"), value_text)


def reference_verdicts(schema_bytes, value_text):
    return _gp().reference_verdicts(schema_bytes.decode("utf-8"), value_text)


def document_text(doc):
    """A sidecar document (text, or {"base64": ...}) as text; None when it
    is missing or not UTF-8."""
    if isinstance(doc, dict):
        try:
            return base64.b64decode(doc["base64"]).decode("utf-8")
        except UnicodeDecodeError:
            return None
    return doc


def outcome_of_counter_row(row):
    """Short outcome string of a recheck_semantic_counters row."""
    now = row["now"]
    if isinstance(now, list):
        return f"{len(now)} invalid instance(s) accepted"
    if "harness_error" in now:
        return "harness error: " + now["harness_error"]
    if row["kind"] in ("compile_mismatch", "session_create_failed"):
        if now.get("compile") != "OK":
            return f"compile {now.get('compile')}"
        return f"session {now.get('session')}"
    g = now.get("generation")
    if g == "ok":
        ref = now.get("reference")
        return "valid document" if ref is True else "document rejected by the reference"
    return g


def classify_counter(row, schema_bytes, schema):
    """-> (class or None, reason) for one published signal, from its
    published symptom and its reproduction in `row`."""
    kind, now, reported = row["kind"], row["now"], row["reported"]
    if isinstance(now, list):
        if now and all(i.get("jsonschema") is False
                       and draft04_integer_case(schema_bytes, schema, i["instance"])
                       for i in now):
            return "D", FIX_REF["D"]
        if not now:
            return "B", "no longer accepted"
        return None, ""
    if "harness_error" in now:
        return None, ""
    if kind in ("compile_mismatch", "session_create_failed"):
        if any(u in reported for u in UNAVAILABLE):
            if now.get("compile") == "OK" and now.get("session") in ("OK", None):
                return "C", "RESOURCE_LIMIT of the accumulated harness context; succeeds in a fresh context"
            return "C", "RESOURCE_LIMIT in a fresh context"
        return None, ""
    g = now.get("generation")
    if g == "ok" and now.get("reference") is False:
        if now.get("reference_value_integers") is True and is_draft04(schema):
            return "D", FIX_REF["D"]
        return None, ""
    if kind == "generated_schema_invalid":
        return (None, "") if g != "ok" else ("D", "valid under the exact-number reference")
    if kind == "generation_error" and any(u in reported for u in UNAVAILABLE):
        return "C", "RESOURCE_LIMIT at generation"
    # a dead end or a refused masked token (completeness defects)
    if g == "ok":
        return "B", "fixed"
    if g == "budget" or (g or "").split(":")[-1] in UNAVAILABLE:
        return "C", ("the published dead end or refused token no longer reproduces; "
                     "the state is refused with RESOURCE_LIMIT or hits the budget")
    return None, ""


def valid_rejects_by_instance(published, residual_runs):
    """(file, k-th valid instance) -> {build: outcome} for the instances
    rejected in the published report."""
    rejected = {}
    for rep in published:
        for rec in rep["per_file"]:
            sem = rec.get("semantic") or {}
            k = 0
            for inst in sem.get("instances") or []:
                if not inst.get("valid"):
                    continue
                if inst.get("outcome") == "reject":
                    rejected[(rec["dataset"], rec["file"], k)] = {"published": inst.get("detail", "")}
                k += 1
    for build, rows in residual_runs:
        counter = {}
        for r in rows:
            if "doc" not in r or "file" not in r:
                continue
            k = counter.get(r["file"], 0)
            counter[r["file"]] = k + 1
            for key in rejected:
                if key[1] == r["file"] and key[2] == k:
                    rejected[key][build] = r["outcome"]
    return rejected


def gen_cases(build, reports):
    """New-format outcomes that need triage: invalid_document,
    validator_error, engine_defect. Each case carries the documents of
    all its invalid_document passes from the sidecar (None where the
    sidecar has none)."""
    out = []
    for snapshot, rep, side in reports:
        for rec in rep["per_file"]:
            for tok, g in (rec.get("generatable") or {}).items():
                if g["outcome"] not in ("invalid_document", "validator_error", "engine_defect"):
                    continue
                rows = (side.get((rec["dataset"], rec["file"])) or {}).get(tok) or []
                docs = [(r[5], document_text(r[6]) if len(r) > 6 else None)
                        for r in rows if r[0] == "invalid_document"]
                out.append({"build": build, "snapshot": snapshot, "dataset": rec["dataset"],
                            "file": rec["file"], "tokenizer": tok, "res": g,
                            "invalid_docs": docs})
    return out


def classify_gen(case, schema_bytes, schema, final_build, final_rec, rechecks):
    """-> (class or None, reason). final_rec is the result of the same
    schema and tokenizer on the final build (None when not run there);
    rechecks maps (snapshot, file, tokenizer, seed) to the verdict of the
    final build on that pass's document (recheck_invalid_documents.py)."""
    g = case["res"]
    final_outcome = final_rec["outcome"] if final_rec else None
    if g["outcome"] == "invalid_document":
        docs = case["invalid_docs"]
        n = len(docs)
        missing = sum(1 for _seed, d in docs if d is None)
        if missing or not n:
            why = "; ".join(
                f"pass {m['pass']} replays to {m['replay']['outcome']} at "
                f"{m['replay']['stage']}, step {m['replay']['step']}"
                for m in (g.get("completion") or {}).get("replay_mismatches", []))
            return None, (f"{missing} of {n} invalid documents not in the sidecar"
                          + (f" ({why})" if why else ""))
        other = [seed for seed, d in docs if not draft04_integer_case(schema_bytes, schema, d)]
        if not other:
            return "D", f"{FIX_REF['D']}; {n} of {n} invalid documents checked"
        if case["build"] != final_build:
            verdicts = [rechecks.get((case["snapshot"], case["file"], case["tokenizer"], seed))
                        for seed in other]
            rejects = sum(v == "reject" for v in verdicts)
            errors = collections.Counter(v for v in verdicts if v and v.startswith("error:"))
            if rejects + sum(errors.values()) == len(other):
                ends = "".join(f", ends {k} in {v[len('error:'):]} before their end"
                               for v, k in sorted(errors.items()))
                rest = (f"; the other {n - len(other)} of {n} are the draft-04 integer "
                        "spelling" if n > len(other) else "")
                return "A", (f"fixed on {final_build}: it accepts none of the {len(other)} "
                             f"invalid documents not explained by draft-04 (rejects "
                             f"{rejects}{ends}){rest}")
            return None, (f"{len(other)} of {n} invalid documents not explained by draft-04; "
                          f"{final_build} rejects {rejects}, accepts "
                          f"{sum(v == 'accept' for v in verdicts)}, not rechecked "
                          f"{sum(v is None for v in verdicts)}")
        return None, f"{len(other)} of {n} invalid documents not explained by draft-04"
    if g["outcome"] == "engine_defect" and case["build"] != final_build:
        if final_outcome in ("generatable", "incomplete"):
            return "B", f"fixed on {final_build} ({final_outcome})"
        if final_outcome == "invalid_document" and not final_rec["counts"].get("engine_defect"):
            return "B", (f"fixed on {final_build}: no engine_defect pass there; its "
                         "invalid_document passes are triaged in their own row")
        if final_outcome == "engine_unavailable":
            return "C", (f"the defect no longer reproduces on {final_build}; "
                         "the schema ends in RESOURCE_LIMIT or CANCELLED there")
    return None, ""


EXCEPTION_FACTS = ("library_sha256", "schema_sha256", "missing_seeds")


def apply_manual(entry, cls, note, gen_outcome=None, facts=None):
    """A manual entry on a row whose rules gave (cls, note) -> (class,
    note, accepted exception or None). facts holds what an exception
    entry is bound to (EXCEPTION_FACTS)."""
    if "exception" in entry:
        if cls is not None:
            return cls, f"{note}; manual exception not needed ({cls} by the rules)", None
        differ = [k for k in EXCEPTION_FACTS
                  if k in entry and entry[k] != (facts or {}).get(k)]
        if differ:
            return None, (f"{note}; manual exception not applied: "
                          f"{', '.join(differ)} differ from the evidence"), None
        return None, f"{note}; accepted exception: {entry['exception']}", entry["exception"]
    if gen_outcome is not None:
        return (*manual_gen_class(entry, gen_outcome, cls, note), None)
    return entry["class"], entry.get("note", ""), None


def manual_gen_class(entry, outcome, cls, note):
    """A manual decision on a generatable-mode row. For an invalid_document
    row the evidence decides: the entry only adds its note when it names
    the same class, and the row stays unclassified otherwise."""
    if outcome != "invalid_document":
        return entry["class"], entry.get("note", "")
    if cls is None:
        return None, f"evidence incomplete ({note}); manual class {entry['class']} not applied"
    if entry["class"] != cls:
        return None, f"manual class {entry['class']} disagrees with the evidence ({cls}: {note})"
    return cls, f"{note}; {entry.get('note', '')}"


def counts_text(counts):
    return ", ".join(f"{o} {n}" for o, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def transition_row(first, final, snap, rec, tok, g0, g1, triaged):
    """One schema result that is generatable on `first` and not on
    `final`, with its class and the pass evidence of both builds."""
    out = g1["outcome"]
    n = g1["counts"].get(out, 0) if out not in ("incomplete", "skipped") else None
    if out in ("invalid_document", "validator_error", "engine_defect"):
        cls = triaged["class"] if triaged else "unclassified"
        why = triaged["note"] if triaged else ""
    elif out in ("engine_unavailable", "incomplete"):
        cls = "C"
        why = (f"{g1['code']} at {g1['stage']} in {n} passes" if n is not None
               else f"no completed_valid pass ({g1['code']} at {g1['stage']} first)")
    elif out == "compile:resource_limit":
        cls = "C"
        why = f"main compile ends in {g1['code']}"
    elif out.startswith("compile:") or out == "missing":
        cls = "unclassified"
        why = f"{out}: {g1['code']}"
    else:
        cls = "-"
        why = f"{out}: {g1['code']}"
    return {"key": f"{snap}/{rec['file']}/{tok}", "corpus": snap, "file": rec["file"],
            "tokenizer": tok, "final": out, "stage": g1["stage"], "code": g1["code"],
            "first": counts_text(g0["counts"]), "last": counts_text(g1["counts"]),
            "class": cls, "note": why}


def refusal_outcome(rec):
    """Transition record of a result without a generatable outcome on the
    final build: its main compile failed there, or the result is missing."""
    if rec is not None and rec.get("error_class") != "compiled":
        return {"outcome": f"compile:{rec.get('error_class')}", "stage": rec.get("error_stage"),
                "code": rec.get("status"), "counts": {}}
    return {"outcome": "missing", "stage": None, "code": "no result", "counts": {}}


def instance_signal(inst):
    """The correctness signal of one corpus instance record, or None."""
    if not inst.get("valid") and inst.get("outcome") == "accept" and inst.get("validator") is False:
        return "invalid_instance_accepted"
    if inst.get("valid") and inst.get("outcome") == "reject":
        return "valid_instance_rejected"
    return None


def classify_instance(signal, build, final_build, final_outcome,
                      schema_bytes, schema, value):
    """-> (class or None, reason). final_outcome is the outcome of the same
    instance on the final build (None when not run there). The harness
    compares with plain jsonschema; the class follows the reference."""
    from bolorgir.serializer import serialize_value
    payload, _status = serialize_value(value, schema)
    strict, lenient = (reference_verdicts(schema_bytes, payload.decode("utf-8"))
                       if payload is not None else (None, None))
    if signal == "invalid_instance_accepted":
        if strict is True:
            return "D", "valid under the reference (exact numbers, ECMA-262 patterns)"
        if strict is False and lenient is True and is_draft04(schema):
            return "D", FIX_REF["D"]
        if build != final_build and final_outcome not in (None, "accept"):
            return "A", f"fixed on {final_build} ({final_outcome})"
        return None, ""
    if strict is False:
        return "D", "corpus label: the reference rejects this instance too"
    if build != final_build and final_outcome == "accept":
        return "B", f"fixed on {final_build}"
    if (final_outcome or "").split(":")[-1] in UNAVAILABLE:
        return "C", FIX_REF["C"]
    return None, ""


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--published", action="append", default=[])
    ap.add_argument("--counters", action="append", default=[])
    ap.add_argument("--residuals", action="append", default=[])
    ap.add_argument("--coverage", action="append", default=[])
    ap.add_argument("--doc-rechecks", action="append", default=[])
    ap.add_argument("--manual", default="")
    ap.add_argument("--gate-build", default="")
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--deviations", default="")
    ap.add_argument("--out-json", default="")
    args = ap.parse_args()

    manual = {}
    if args.manual and os.path.exists(args.manual):
        with open(args.manual, encoding="utf-8") as f:
            manual = json.load(f)

    published = []
    pub_label = "-"
    for p in args.published:
        published.append(load_json(p))
        pub_label = published[-1]["meta"]["engine"]["commit"][:7]
    counters = [(n, jsonl(p)) for n, p in named(args.counters)]
    residuals = [(n, jsonl(p)) for n, p in named(args.residuals)]
    builds = [n for n, _ in counters]
    final = builds[-1] if builds else None

    rows = []
    schema_cache = {}

    def schema_of(snapshot, dataset, fname):
        key = (snapshot, dataset, fname)
        if key not in schema_cache:
            schema_cache[key] = load_schema(snapshot, dataset, fname)
        return schema_cache[key]

    # 1. counters of the published report
    by_key = {}
    for build, data in counters:
        for r in data:
            if "summary" in r:
                continue
            by_key.setdefault((r["snapshot"], r["kind"], r["dataset"], r["file"]), {})[build] = r
    for (snap, kind, dataset, fname), per_build in sorted(by_key.items()):
        fin = per_build[final]
        sb, sch, _tests = schema_of(snap, dataset, fname)
        cls, note = classify_counter(fin, sb, sch)
        key = f"{snap}/{fname}/{kind}"
        exc = None
        if key in manual:
            cls, note, exc = apply_manual(manual[key], cls, note)
        elif cls == "B" and note == "fixed":
            gone = [b for b in builds if classify_counter(per_build[b], sb, sch)[0] == "B"]
            note = f"fixed on {gone[0]}" if gone else note
        rows.append({"key": key, "corpus": snap, "file": fname, "signal": kind,
                     "published": fin["reported"][:90],
                     "outcomes": {b: outcome_of_counter_row(per_build[b]) for b in builds},
                     "class": cls or "unclassified", "note": note, "exception": exc,
                     "schema_sha256": hashlib.sha256(sb).hexdigest()})

    # 2. valid instances rejected in the published report
    for (dataset, fname, k), outs in sorted(valid_rejects_by_instance(published, residuals).items()):
        key = f"maskbench/{fname}/valid_instance_rejected/{k}"
        fin = outs.get(final)
        if fin == "accept":
            cls, note = "B", "fixed on " + next(b for b in builds if outs.get(b) == "accept")
        elif fin and fin.split(":")[-1] in UNAVAILABLE:
            cls, note = "C", FIX_REF["C"]
        else:
            cls, note = None, ""
        exc = None
        if key in manual:
            cls, note, exc = apply_manual(manual[key], cls, note)
        sb, _sch, _t = schema_of("maskbench", dataset, fname)
        rows.append({"key": key, "corpus": "maskbench", "file": f"{fname} (valid #{k})",
                     "signal": "valid_instance_rejected", "published": outs["published"][:90],
                     "outcomes": {b: outs.get(b, "-") for b in builds},
                     "class": cls or "unclassified", "note": note, "exception": exc,
                     "schema_sha256": hashlib.sha256(sb).hexdigest()})

    doc_rechecks = {}
    for _name, path in named(args.doc_rechecks):
        for r in jsonl(path):
            if "verdict" in r:
                doc_rechecks[(r["snapshot"], r["file"], r["tokenizer"], r["seed"])] = r["verdict"]

    # 3. generatable-mode outcomes of the given coverage runs
    gen_rows = []
    gen_reports = []
    for build, path in named(args.coverage):
        rep = load_json(path)
        snap = "maskbench" if rep["meta"]["corpus"]["path"].startswith("maskbench") else "repo"
        gen_reports.append((build, snap, rep, load_sidecar(path)))
    final_gen = gen_reports[-1][0] if gen_reports else None
    final_outcomes = {}
    final_files = {}
    for build, snap, rep, _side in gen_reports:
        if build != final_gen:
            continue
        for rec in rep["per_file"]:
            final_files[(snap, rec["file"])] = rec
            for tok, g in (rec.get("generatable") or {}).items():
                final_outcomes[(snap, rec["file"], tok)] = g
    for build, snap, rep, side in gen_reports:
        for case in gen_cases(build, [(snap, rep, side)]):
            sb, sch, _t = schema_of(case["snapshot"], case["dataset"], case["file"])
            fin_rec = final_outcomes.get((case["snapshot"], case["file"], case["tokenizer"]))
            fin = fin_rec["outcome"] if fin_rec else None
            cls, note = classify_gen(case, sb, sch, final_gen, fin_rec, doc_rechecks)
            key = f"{build}/{case['snapshot']}/{case['file']}/{case['tokenizer']}"
            exc = None
            if key in manual:
                facts = {"library_sha256": rep["meta"]["engine"].get("library_sha256"),
                         "schema_sha256": hashlib.sha256(sb).hexdigest(),
                         "missing_seeds": sorted(seed for seed, d in case["invalid_docs"]
                                                 if d is None)}
                cls, note, exc = apply_manual(manual[key], cls, note, case["res"]["outcome"],
                                              facts)
            g = case["res"]
            gen_rows.append({"key": key, "build": build, "corpus": case["snapshot"],
                             "file": case["file"], "tokenizer": case["tokenizer"],
                             "outcome": g["outcome"], "stage": g["stage"], "code": g["code"],
                             "final": fin or "-",
                             "class": cls or "unclassified", "note": note, "exception": exc,
                             "schema_sha256": hashlib.sha256(sb).hexdigest(),
                             "invalid_documents": len(case["invalid_docs"]),
                             "documents_missing": sum(1 for _seed, d in case["invalid_docs"]
                                                      if d is None)})

    # 4. corpus instances of the same runs: invalid instances accepted while
    # the reference rejects them, valid instances rejected
    inst_rows = []
    final_inst = {}
    for build, snap, rep, _side in gen_reports:
        if build != final_gen:
            continue
        for rec in rep["per_file"]:
            for i, inst in enumerate((rec.get("semantic") or {}).get("instances") or []):
                final_inst[(snap, rec["file"], i)] = inst.get("outcome")
    for build, snap, rep, _side in gen_reports:
        for rec in rep["per_file"]:
            for i, inst in enumerate((rec.get("semantic") or {}).get("instances") or []):
                signal = instance_signal(inst)
                if signal is None:
                    continue
                sb, sch, tests = schema_of(snap, rec["dataset"], rec["file"])
                fin = final_inst.get((snap, rec["file"], i))
                cls, note = classify_instance(signal, build, final_gen, fin,
                                              sb, sch, tests[i].get("data"))
                key = f"{build}/{snap}/{rec['file']}/{signal}/{i}"
                exc = None
                if key in manual:
                    cls, note, exc = apply_manual(manual[key], cls, note)
                inst_rows.append({"key": key, "build": build, "corpus": snap,
                                  "file": f"{rec['file']} (test {i})", "signal": signal,
                                  "detail": inst.get("detail", ""), "final": fin or "-",
                                  "class": cls or "unclassified", "note": note, "exception": exc,
                                  "schema_sha256": hashlib.sha256(sb).hexdigest()})

    # 5. schema results generatable on the first coverage build and not
    # on the final one (the coverage control of T3 lists each)
    trans_rows = []
    gen_class = {r["key"]: r for r in gen_rows}
    first_gen = gen_reports[0][0] if gen_reports else None
    if first_gen is not None and first_gen != final_gen:
        for build, snap, rep, _side in gen_reports:
            if build != first_gen:
                continue
            final_snaps = {s for s, _f in final_files}
            for rec in rep["per_file"]:
                for tok, g0 in (rec.get("generatable") or {}).items():
                    if g0["outcome"] != "generatable" or snap not in final_snaps:
                        continue
                    g1 = final_outcomes.get((snap, rec["file"], tok))
                    if g1 is None:
                        g1 = refusal_outcome(final_files.get((snap, rec["file"])))
                    if g1["outcome"] == "generatable":
                        continue
                    row = gen_class.get(f"{final_gen}/{snap}/{rec['file']}/{tok}")
                    trans_rows.append(transition_row(first_gen, final_gen, snap, rec, tok,
                                                     g0, g1, row))

    def rel(path):
        if not os.path.exists(path):
            return path
        return os.path.relpath(os.path.abspath(path), _ROOT)

    def count(rs):
        c = {}
        for r in rs:
            c[r["class"]] = c.get(r["class"], 0) + 1
        return c

    lines = [
        "# Semantic triage (classes A-E)",
        "",
        "Generated by `benchmarks/triage_semantic.py` from rechecked evidence;",
        "class rules in the script docstring, manual decisions in the",
        "`--manual` file. A: correctness violation; B: completeness or",
        "consistency defect; C: availability (RESOURCE_LIMIT, CANCELLED,",
        "budgets); D: reference deviation; E: harness defect.",
        "",
        "Inputs:",
        "",
    ]
    lines += [f"- published report (build `{rep['meta']['engine']['commit'][:7]}`, sha256 "
              f"`{file_sha256(p)[:16]}...`): `{rel(p)}`"
              for p, rep in zip(args.published, published)]
    lines += [f"- counters `{n}`: `{rel(p)}`" for n, p in named(args.counters)]
    lines += [f"- residuals `{n}`: `{rel(p)}`" for n, p in named(args.residuals)]
    cov = {}
    for n, p in named(args.coverage):
        cov.setdefault(n, []).append(p)
    for n, ps in cov.items():
        lines.append(f"- generatable reports `{n}`:")
        lines += [f"  - `{rel(p)}` (sha256 `{file_sha256(p)[:16]}...`)" for p in ps]
    lines += [f"- invalid documents fed to `{n}`: `{rel(p)}` (sha256 `{file_sha256(p)[:16]}...`)"
              for n, p in named(args.doc_rechecks)]
    if args.manual:
        lines.append(f"- manual decisions: `{rel(args.manual)}`")
    accepted = [r for r in rows + gen_rows + inst_rows if r["exception"]]
    if accepted:
        lines += ["", "## Accepted exceptions", "",
                  "Rows that stay unclassified by a recorded decision (`exception` in the",
                  "manual file). They are not counted as triaged.", "",
                  "| row | decision |", "|---|---|"]
        lines += [f"| `{r['key']}` | {r['exception']} |" for r in accepted]
    if builds:
        lines += [
            "",
            f"## Published counters (build `{pub_label}`), rechecked on "
            + ", ".join(f"`{b}`" for b in builds),
            "",
            "| class | cases |",
            "|---|---:|",
        ]
        lines += [f"| {k} | {v} |" for k, v in sorted(count(rows).items())]
        lines += ["", "| corpus | file | signal | published detail | "
                  + " | ".join(builds) + " | class | fix or reason |",
                  "|" + "---|" * (6 + len(builds))]
        for r in rows:
            cells = [r["corpus"], r["file"], r["signal"], r["published"].replace("|", "\\|")]
            cells += [str(r["outcomes"][b]).replace("|", "\\|") for b in builds]
            cells += [r["class"], r["note"]]
            lines.append("| " + " | ".join(cells) + " |")
    if gen_rows:
        lines += ["", "## Generatable-mode outcomes", "",
                  "| class | schema results |", "|---|---:|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(count(gen_rows).items())]
        lines += ["", f"| build | corpus | file | tokenizer | outcome | stage | code | "
                  f"on {final_gen} | class | reason |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in gen_rows:
            lines.append("| " + " | ".join(str(r[k]) for k in (
                "build", "corpus", "file", "tokenizer", "outcome", "stage", "code",
                "final", "class", "note")) + " |")
    if inst_rows:
        lines += ["", "## Corpus instances in the same runs", "",
                  "| class | instances |", "|---|---:|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(count(inst_rows).items())]
        lines += ["", f"| build | corpus | file | signal | detail | on {final_gen} | class | reason |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in inst_rows:
            lines.append("| " + " | ".join(str(r[k]).replace("|", "\\|") for k in (
                "build", "corpus", "file", "signal", "detail", "final", "class", "note")) + " |")
    if trans_rows:
        lines += ["", f"## Generatable on `{first_gen}`, not generatable on `{final_gen}`", "",
                  "Every schema result whose outcome left `generatable`, with the pass",
                  "outcome counts of both builds. Class C (availability) goes to the",
                  "coverage control of T3; class D results end in the documented",
                  "reference deviation on the final build.", "",
                  "| class | schema results |", "|---|---:|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(count(trans_rows).items())]
        lines += ["", f"| corpus | file | tokenizer | on {final_gen} | stage | code | "
                  f"passes on {first_gen} | passes on {final_gen} | class | reason |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in trans_rows:
            lines.append("| " + " | ".join(str(r[k]).replace("|", "\\|") for k in (
                "corpus", "file", "tokenizer", "final", "stage", "code", "first", "last",
                "class", "note")) + " |")
    with open(args.out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    no_kind = []
    for r in rows + gen_rows + inst_rows:
        if r["class"] == "D":
            r["deviation"] = deviation_kind(r["note"], manual.get(r["key"]))
            if r["deviation"] not in DEVIATION_REASONS:
                no_kind.append(r["key"])
    if args.deviations:
        with open(args.deviations, encoding="utf-8") as f:
            dev = json.load(f)
        entries = {}
        for r in rows + gen_rows + inst_rows:
            if r["class"] != "D" or r["deviation"] not in DEVIATION_REASONS:
                continue
            e = entries.setdefault(r["schema_sha256"], {
                "class": "D", "corpus": r["corpus"], "file": r["file"].split(" ")[0],
                "schema_sha256": r["schema_sha256"], "deviations": [],
                "triage": os.path.relpath(args.out_md, _ROOT), "signals": []})
            if r["deviation"] not in e["deviations"]:
                e["deviations"].append(r["deviation"])
            sig = r.get("signal") or f"{r.get('outcome')} ({r.get('tokenizer')})"
            if sig not in e["signals"]:
                e["signals"].append(sig)
        for e in entries.values():
            e["deviations"].sort()
            e["reason"] = "; ".join(DEVIATION_REASONS[k] for k in e["deviations"])
        dev["entries"] = sorted(entries.values(), key=lambda e: (e["corpus"], e["file"]))
        with open(args.deviations, "w", encoding="utf-8") as f:
            json.dump(dev, f, ensure_ascii=False, indent=1)
            f.write("\n")

    open_rows = [r for r in rows + gen_rows + inst_rows if r["class"] == "unclassified"]
    unclassified = [r["key"] for r in open_rows]
    summary = {"published": count(rows), "generatable": count(gen_rows),
               "instances": count(inst_rows), "transitions": count(trans_rows),
               "unclassified": unclassified[:50], "n_unclassified": len(unclassified),
               "accepted_exceptions": [r["key"] for r in open_rows if r["exception"]],
               "class_d_without_deviation": no_kind[:50]}
    failed = bool(unclassified)
    if args.gate_build:
        gate = [r["key"] for r in open_rows
                if r.get("build", args.gate_build) == args.gate_build or not r["exception"]]
        summary["gate"] = {"build": args.gate_build, "unclassified": gate[:50]}
        failed = bool(gate)
    status = 1 if failed or no_kind else 0
    if args.out_json:
        inputs = [{"build": n, "path": rel(p), "sha256": file_sha256(p),
                   "library_sha256": rep["meta"]["engine"].get("library_sha256")}
                  for (n, p), (_b, _s, rep, _side) in zip(named(args.coverage), gen_reports)]
        with open(args.out_json, "w", encoding="utf-8") as f:
            json.dump({"inputs": {"coverage": inputs,
                                  "doc_rechecks": [{"build": n, "path": rel(p),
                                                    "sha256": file_sha256(p)}
                                                   for n, p in named(args.doc_rechecks)],
                                  "manual": ({"path": rel(args.manual),
                                              "sha256": file_sha256(args.manual)}
                                             if args.manual and os.path.exists(args.manual)
                                             else None)},
                       "final_build": final_gen, "gate_build": args.gate_build or None,
                       "summary": summary, "exit_status": status,
                       "generatable_rows": gen_rows, "instance_rows": inst_rows,
                       "transition_rows": trans_rows, "counter_rows": rows},
                      f, ensure_ascii=False, indent=1)
            f.write("\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return status


if __name__ == "__main__":
    sys.exit(main())
