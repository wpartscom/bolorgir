"""Coverage control and acceptance verdict of a T3 candidate
(benchmarks/t3/tools/acceptance_compare.py) and the compile-refusal
transitions of benchmarks/triage_semantic.py, on synthetic reports; no
engine is loaded. Rework spec C2 and C3: a partial candidate against full
lines, D4 decisions that do not approve, oracle and identity gaps that
must block the verdict."""

import ast
import hashlib
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


compare = _load("acceptance_compare", "benchmarks/t3/tools/acceptance_compare.py")
triage = _load("triage_semantic", "benchmarks/triage_semantic.py")

SAMPLE = 20
LIB_B, LIB_C = "b" * 64, "c" * 64
GOOD = ("generatable", {"completed_valid": 20})


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _names(prefix, n, sampled=True):
    out, i = [], 0
    while len(out) < n:
        name = f"{prefix}{i}.json"
        if compare.in_sample("D", name, SAMPLE) == sampled:
            out.append(name)
        i += 1
    return out


def _gen(outcome, counts):
    return {"outcome": outcome, "stage": "fill_mask" if outcome != "generatable" else "validate",
            "code": "BLG_ERR_RESOURCE_LIMIT" if outcome == "engine_unavailable" else None,
            "counts": counts}


def _rec(fname, byte=GOOD, llama=GOOD, error_class="compiled", dataset="D"):
    rec = {"dataset": dataset, "file": fname, "status": "OK" if error_class == "compiled"
           else "RESOURCE_LIMIT", "error_class": error_class, "error_stage": None}
    if error_class == "compiled":
        rec["generatable"] = {"byte": _gen(*byte)}
        if compare.in_sample(dataset, fname, SAMPLE):
            rec["generatable"]["llama-3.1"] = _gen(*llama)
        else:
            rec["generatable"]["llama-3.1"] = {"outcome": "skipped", "stage": None,
                                               "code": "not_sampled", "counts": {}}
    return rec


def _meta(snapshot, lib, **gen_over):
    gen = {"profile": "spec-v1", "passes": 20, "seed": 20261002, "max_steps": 8192,
           "max_gen_seconds": 10.0, "schema_budget_seconds": 0.0, "context_recycle_every": 150,
           "validator": "jsonschema", "tokenizers": {
               "byte": {"name": "byte", "revision": "-", "vocab_size": 258,
                        "single_byte_tokens": 256, "sample_every": 1},
               "llama-3.1": {"name": "llama", "revision": "r1", "vocab_size": 128256,
                             "single_byte_tokens": 256, "sample_every": SAMPLE}}}
    gen.update(gen_over)
    return {"engine": {"library_sha256": lib, "commit": lib[:8]},
            "corpus": {"path": "maskbench/data/" if snapshot == "maskbench" else "data/",
                       "repo": "jsb", "revision": "rev", "sha256": f"s-{snapshot}", "files": 0},
            "limits": {"mode": "adaptive", "max_depth": 64}, "generatable": gen}


def _write(directory, snapshot, lib, records, **gen_over):
    os.makedirs(directory, exist_ok=True)
    rep = {"meta": _meta(snapshot, lib, **gen_over), "aggregate": {}, "per_file": records}
    path = os.path.join(directory, f"coverage_{snapshot}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rep, f)
    return path


def _corpora(directory, lib, repo=None, maskbench=None):
    paths = []
    if repo is not None:
        paths.append(_write(directory, "repo", lib, repo))
    if maskbench is not None:
        paths.append(_write(directory, "maskbench", lib, maskbench))
    return paths


def _repo_records():
    return [_rec(n) for n in _names("r", 3) + _names("q", 3, sampled=False)]


def _mb_records(n_unavailable=0, n=100):
    names = _names("M---s", n)
    return [_rec(name, llama=("engine_unavailable", {"engine_unavailable": 20})
                 if i < n_unavailable else GOOD) for i, name in enumerate(names)]


def _oracle_row(d, i, oc):
    row = {"draft": d, "suite_file": "f.json", "case": "c", "test_index": i, "outcome": oc,
           "schema_compiled": oc != "SKIPPED", "engine": {"PASS": "accept",
                                                          "ENGINE_ERROR": "error:RESOURCE_LIMIT"
                                                          }.get(oc)}
    if oc == "SKIPPED":
        row["refusal"] = {"status": "UNSUPPORTED_FEATURE", "message": "not supported",
                          "classification": "documented_refusal"}
    return row


def _oracle_summary(d, rows):
    """The counters run_oracle.py writes, zero counts omitted."""
    by, st, f = {}, {}, {"cases": 1}
    keys = {"PASS": "pass", "MISMATCH": "mismatch", "KNOWN_GAP": "known_gap",
            "ENGINE_ERROR": "engine_error", "SERIALIZATION_INCOMPATIBLE":
            "serialization_incompatible", "SKIPPED": "refused"}
    for r in rows:
        oc = r["outcome"]
        by[oc] = by.get(oc, 0) + 1
        f[keys[oc]] = f.get(keys[oc], 0) + 1
        if oc == "SKIPPED":
            s = r["refusal"]["status"]
            st[s] = st.get(s, 0) + 1
        else:
            f["compiled"] = f.get("compiled", 0) + 1
    return {"rows": len(rows), "cases": 1, "by_outcome": by, "by_refusal_status": st,
            "by_file": {"f.json": f}, "by_draft": {d: {"rows": len(rows), "by_outcome": dict(by)}}}


def _oracle(directory, build, lib, pass_count=10, drop=(), engine_errors=0, header=None,
            edit=None):
    """Five spec-v1 dialect reports with the rows and summary of
    run_oracle.py (the summary omits zero counts). lib None: a report older
    than the library_sha256 header field (header gives its library path and
    date). edit(dialect, doc) changes a report before it is written."""
    os.makedirs(directory, exist_ok=True)
    paths = []
    for d in compare.DIALECTS:
        if d in drop:
            continue
        outcomes = ["PASS"] * pass_count + ["SKIPPED"] * 2
        if d == "draft2020-12":
            outcomes += ["ENGINE_ERROR"] * engine_errors
        rows = [_oracle_row(d, i, oc) for i, oc in enumerate(outcomes)]
        h = {"drafts": [d], "profile": "spec-v1", "library_sha256": lib,
             "suite": {"commit": "suite"}, "include_optional": False,
             "serializer": "value-preserving", "validator": {"actual": "4.26.0"}, **(header or {})}
        if lib is None:
            del h["library_sha256"]
        doc = {"header": h, "summary": _oracle_summary(d, rows), "rows": rows}
        if edit is not None:
            edit(d, doc)
        p = os.path.join(directory, f"{build}-{d}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        paths.append(p)
    return paths


def _triage(path, label, report_paths, lib, rows=(), exit_status=0, gate=None):
    doc = {"inputs": {"coverage": [{"build": label, "path": p, "sha256": _sha(p),
                                    "library_sha256": lib} for p in report_paths]},
           "final_build": label, "gate_build": gate or label, "exit_status": exit_status,
           "summary": {"n_unclassified": 0 if exit_status == 0 else 1},
           "generatable_rows": list(rows)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return str(path)


def _d4(path, lib, entries=()):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"candidate_library_sha256": lib, "entries": list(entries)}, f)
    return str(path)


def _run(tmp_path, argv):
    out_md, out_json = tmp_path / "out.md", tmp_path / "out.json"
    rc = compare.main([*argv, "--out-md", str(out_md), "--out-json", str(out_json)])
    if rc == compare.EXIT_UNUSABLE:
        return rc, None, None
    return rc, json.loads(out_json.read_text(encoding="utf-8")), out_md.read_text(encoding="utf-8")


def _check(result, name):
    return next(c for c in result["checks"] if c["check"] == name)


def _admitted_setup(tmp_path, cand_repo=None, cand_mb=None, line_oracle_pass=10,
                    cand_oracle_kw=None, d4_entries=(), triage_rows=()):
    """A baseline line `b` and a candidate `c` with both corpora, the five
    oracle reports of both, a D4 list and a passing T2c triage of c."""
    base, cand = tmp_path / "base", tmp_path / "cand"
    _corpora(base, LIB_B, _repo_records(), _mb_records())
    paths = _corpora(cand, LIB_C, cand_repo or _repo_records(), cand_mb or _mb_records())
    argv = ["--candidate", f"c={cand}", "--line", f"b={base}",
            "--d4", _d4(tmp_path / "d4.json", LIB_C, d4_entries),
            "--triage", _triage(tmp_path / "triage.json", "c", paths, LIB_C, triage_rows)]
    for p in _oracle(tmp_path / "oracle", "c", LIB_C, **(cand_oracle_kw or {})):
        argv += ["--oracle", f"c={p}"]
    for p in _oracle(tmp_path / "oracle", "b", LIB_B, pass_count=line_oracle_pass):
        argv += ["--oracle", f"b={p}"]
    return argv, base, cand


# --- verdicts ------------------------------------------------------------

def test_consistent_candidate_is_admitted(tmp_path):
    argv, _b, _c = _admitted_setup(tmp_path)
    rc, res, md = _run(tmp_path, argv)
    failing = [c for c in res["checks"] if c["status"] != "ok"]
    assert failing == []
    assert (rc, res["acceptance"]["verdict"]) == (0, "admitted")
    assert "**Verdict: admitted**" in md


def test_partial_candidate_against_full_lines_is_intermediate(tmp_path):
    """C2: both lines have both corpora, the candidate only repo (the state
    of acc-50cbd80e): no KeyError, both reports written, exit 3."""
    base, line, cand = tmp_path / "base", tmp_path / "line", tmp_path / "cand"
    _corpora(base, LIB_B, _repo_records(), _mb_records())
    _corpora(line, "e" * 64, _repo_records(), _mb_records())
    names = _names("r", 3)
    _corpora(cand, LIB_C, [_rec(names[0], byte=("engine_unavailable", {"engine_unavailable": 20}))]
             + [_rec(n) for n in names[1:] + _names("q", 3, sampled=False)])
    rc, res, md = _run(tmp_path, ["--candidate", f"c={cand}", "--line", f"b={base}",
                                  "--line", f"l={line}"])
    assert rc == compare.EXIT_INTERMEDIATE
    assert res["intermediate"] == ["maskbench"]
    assert res["acceptance"]["verdict"] == "intermediate"
    assert _check(res, "goal_unavailable")["status"] == "pending"
    assert _check(res, "protocol:b:maskbench")["status"] == "pending"
    assert {(r["line"], r["file"], r["tokenizer"]) for r in res["transitions"]} == {
        ("b", names[0], "byte"), ("l", names[0], "byte")}
    assert "Intermediate" in md and (tmp_path / "out.json").exists()


def test_compile_refusal_is_a_transition(tmp_path):
    base, cand = tmp_path / "base", tmp_path / "cand"
    x, y = _names("x", 2)
    _corpora(base, LIB_B, [_rec(x), _rec(y)])
    _corpora(cand, LIB_C, [_rec(x, error_class="resource_limit"), _rec(y)])
    rc, res, _md = _run(tmp_path, ["--candidate", f"c={cand}", "--line", f"b={base}"])
    assert rc == compare.EXIT_INTERMEDIATE
    rows = [(r["file"], r["tokenizer"], r["candidate"], r["class"]) for r in res["transitions"]]
    assert (x, "byte", "compile:resource_limit", "C") in rows
    assert _check(res, "transitions_described")["status"] == "fail"
    assert _check(res, "coverage_baseline:repo:byte")["status"] == "fail"


# --- D4 decisions ---------------------------------------------------------

def _transition_setup(tmp_path, decision, entry_extra=None, triage_rows=()):
    names = _names("r", 3)
    cand_repo = [_rec(names[0], byte=("engine_unavailable", {"engine_unavailable": 20}))] + [
        _rec(n) for n in names[1:] + _names("q", 3, sampled=False)]
    entry = {"id": "E", "corpus": "repo", "file": names[0], "tokenizers": ["byte"],
             "owner_decision": decision, **(entry_extra or {})}
    argv, _b, _c = _admitted_setup(tmp_path, cand_repo=cand_repo, d4_entries=[entry],
                                   triage_rows=triage_rows)
    return argv, names[0]


def _row(fname, outcome="engine_unavailable"):
    return {"corpus": "repo", "file": fname, "tokenizer": "byte", "outcome": outcome}


def test_pending_d4_decision_does_not_approve(tmp_path):
    argv, _f = _transition_setup(tmp_path, None)
    rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "transitions_described")["status"] == "ok"
    assert _check(res, "transitions_approved")["status"] == "fail"
    assert res["transitions"][0]["approval"] == "pending"
    assert res["d4_pending_rows"][0]["row"]["outcome"] == "engine_unavailable"
    assert rc == 1 and res["acceptance"]["verdict"] == "not_admitted"


def test_rejected_d4_decision_does_not_approve(tmp_path):
    argv, f = _transition_setup(tmp_path, {"decision": "reject", "candidate_library_sha256": LIB_C,
                                           "rows": [_row("x")]})
    rc, res, _md = _run(tmp_path, argv)
    assert res["transitions"][0]["approval"] == "rejected" and rc == 1


def test_decision_for_another_candidate_does_not_approve(tmp_path):
    argv, f = _transition_setup(tmp_path, {"decision": "accept",
                                           "candidate_library_sha256": "d" * 64, "rows": [_row("x")]})
    argv[argv.index("--d4") + 1] = _d4(tmp_path / "d4other.json", "d" * 64, json.load(
        open(argv[argv.index("--d4") + 1]))["entries"])
    rc, res, _md = _run(tmp_path, argv)
    assert res["transitions"][0]["approval"] == "not_applicable"
    assert _check(res, "d4_candidate")["status"] == "fail" and rc == 1


def test_decision_must_list_the_exact_row(tmp_path):
    """An accept of the right file with another outcome does not approve
    the transition."""
    argv, fname = _transition_setup(tmp_path, None)
    entries = json.load(open(argv[argv.index("--d4") + 1]))["entries"]
    entries[0]["owner_decision"] = {"decision": "accept", "candidate_library_sha256": LIB_C,
                                    "rows": [_row(fname, outcome="incomplete")], "by": "owner",
                                    "date": "2026-10-08"}
    argv[argv.index("--d4") + 1] = _d4(tmp_path / "d4row.json", LIB_C, entries)
    rc, res, _md = _run(tmp_path, argv)
    assert res["transitions"][0]["approval"] == "not_listed"
    assert _check(res, "transitions_approved")["status"] == "fail" and rc == 1


def test_accepted_listed_row_is_approved(tmp_path):
    argv, _f = _transition_setup(tmp_path, None)
    entries = json.load(open(argv[argv.index("--d4") + 1]))["entries"]
    fname = entries[0]["file"]
    entries[0]["owner_decision"] = {"decision": "accept", "candidate_library_sha256": LIB_C,
                                    "rows": [_row(fname)], "by": "owner", "date": "2026-10-08"}
    argv[argv.index("--d4") + 1] = _d4(tmp_path / "d4ok.json", LIB_C, entries)
    rc, res, _md = _run(tmp_path, argv)
    assert res["transitions"][0]["approval"] == "approved"
    assert _check(res, "transitions_approved")["status"] == "ok"
    # the coverage of byte dropped below the baseline, so the verdict stays negative
    assert _check(res, "coverage_baseline:repo:byte")["status"] == "fail" and rc == 1


def _wildcard_setup(tmp_path, triage_class, documents_missing=0):
    names = _names("r", 3)
    cand_repo = [_rec(names[0], byte=("invalid_document", {"completed_valid": 19,
                                                           "invalid_document": 1}))] + [
        _rec(n) for n in names[1:] + _names("q", 3, sampled=False)]
    entry = {"id": "D4-6", "corpus": "*", "file": "*", "match_outcome": "invalid_document",
             "requires_triage_class": "D",
             "owner_decision": {"decision": "accept", "candidate_library_sha256": LIB_C,
                                "rows": [_row(names[0], "invalid_document")]}}
    trow = {"key": f"c/repo/{names[0]}/byte", "class": triage_class, "invalid_documents": 1,
            "documents_missing": documents_missing}
    argv, _b, _c = _admitted_setup(tmp_path, cand_repo=cand_repo, d4_entries=[entry],
                                   triage_rows=[trow])
    return argv


def test_wildcard_d4_needs_class_d_of_the_candidate_triage(tmp_path):
    for cls in ("A", "B", "unclassified"):
        rc, res, _md = _run(tmp_path, _wildcard_setup(tmp_path, cls))
        assert res["transitions"][0]["approval"] == "unverified", cls
        assert rc == 1


def test_wildcard_d4_with_missing_documents_is_unverified(tmp_path):
    _rc, res, _md = _run(tmp_path, _wildcard_setup(tmp_path, "D", documents_missing=1))
    assert res["transitions"][0]["approval"] == "unverified"


def test_wildcard_d4_with_class_d_is_approved(tmp_path):
    _rc, res, _md = _run(tmp_path, _wildcard_setup(tmp_path, "D"))
    assert res["transitions"][0]["approval"] == "approved"


# --- oracle ---------------------------------------------------------------

def _replace_oracle(argv, build, paths):
    out = []
    i = 0
    while i < len(argv):
        if argv[i] == "--oracle" and argv[i + 1].startswith(f"{build}="):
            i += 2
            continue
        out.append(argv[i])
        i += 1
    for p in paths:
        out += ["--oracle", f"{build}={p}"]
    return out


def test_missing_draft2020_12_fails_the_oracle_goal(tmp_path):
    argv, _b, _c = _admitted_setup(tmp_path)
    argv = _replace_oracle(argv, "c", _oracle(tmp_path / "o2", "c", LIB_C, drop=("draft2020-12",)))
    rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "goal_oracle")["status"] == "fail"
    assert _check(res, "oracle_dialects:c")["status"] == "fail"
    assert rc == 1


def test_incomplete_dialect_set_fails(tmp_path):
    argv, _b, _c = _admitted_setup(tmp_path)
    argv = _replace_oracle(argv, "c", _oracle(tmp_path / "o2", "c", LIB_C, drop=("draft6",)))
    rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "oracle_dialects:c")["status"] == "fail"
    assert _check(res, "oracle_pass:b")["status"] == "fail" and rc == 1


def test_missing_line_oracle_fails(tmp_path):
    argv, _b, _c = _admitted_setup(tmp_path)
    argv = _replace_oracle(argv, "b", [])
    rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "oracle_lines")["status"] == "fail" and rc == 1


LIB_E = "e" * 64


def _two_lines(tmp_path, decision=None, reason="not kept"):
    """The admitted setup plus a second line `e` with coverage reports and
    no oracle data, listed in the oracle line list with a reason and the
    given owner decision."""
    argv, _b, _c = _admitted_setup(tmp_path)
    line = tmp_path / "line-e"
    _corpora(line, LIB_E, _repo_records(), _mb_records())
    manifest = tmp_path / "lines.json"
    manifest.write_text(json.dumps({"lines": [{"name": "e", "library_sha256": LIB_E,
                                               "reports": None, "missing_reason": reason,
                                               "owner_decision": decision}]}))
    return argv + ["--line", f"e={line}", "--oracle-lines", str(manifest)]


def _exception(**over):
    dec = {"decision": "accept_without_oracle", "control_line": "b", "candidates": [LIB_C],
           "by": "owner", "date": "2026-10-08", "note": "PASS control by line b"}
    dec.update(over)
    return dec


def test_line_without_oracle_and_without_owner_decision_blocks(tmp_path):
    rc, res, _md = _run(tmp_path, _two_lines(tmp_path))
    assert _check(res, "oracle_line:e")["status"] == "fail"
    assert "not kept" in _check(res, "oracle_line:e")["detail"]
    assert _check(res, "oracle_lines")["status"] == "fail"
    assert _check(res, "oracle_dialects:e")["status"] == "fail"
    assert rc == 1 and res["acceptance"]["verdict"] == "not_admitted"


def test_approved_oracle_exception_admits_with_the_control_line(tmp_path):
    rc, res, _md = _run(tmp_path, _two_lines(tmp_path, _exception()))
    assert _check(res, "oracle_line:e")["status"] == "ok"
    assert "oracle_dialects:e" not in {c["check"] for c in res["checks"]}
    assert res["inputs"]["oracle_line_exceptions"]["e"]["approved"]
    assert rc == 0 and res["acceptance"]["verdict"] == "admitted"


def test_oracle_exception_out_of_scope_does_not_apply(tmp_path):
    for i, over in enumerate(({"candidates": ["d" * 64]}, {"control_line": "e"},
                              {"decision": "accept"}, {"by": ""})):
        d = tmp_path / str(i)
        d.mkdir()
        rc, res, _md = _run(d, _two_lines(d, _exception(**over)))
        assert _check(res, "oracle_line:e")["status"] == "fail", over
        assert rc == 1, over


def test_oracle_lines_manifest_binds_report_bytes(tmp_path):
    argv, _b, _c = _admitted_setup(tmp_path)
    paths = _oracle(tmp_path / "ob", "b2", LIB_B)
    argv = _replace_oracle(argv, "b", [])
    reports = {d: {"path": p, "sha256": _sha(p)} for d, p in zip(compare.DIALECTS, paths)}
    manifest = tmp_path / "lines.json"
    manifest.write_text(json.dumps({"lines": [{"name": "b", "library_sha256": LIB_B,
                                               "reports": reports}]}))
    rc, res, _md = _run(tmp_path, argv + ["--oracle-lines", str(manifest)])
    assert rc == 0 and res["acceptance"]["verdict"] == "admitted"
    assert _check(res, "oracle_binding:b")["status"] == "ok"
    with open(paths[0], "a", encoding="utf-8") as f:
        f.write(" ")
    rc, _res, _md = _run(tmp_path, argv + ["--oracle-lines", str(manifest)])
    assert rc == compare.EXIT_UNUSABLE


def test_line_report_with_the_right_file_hash_and_another_library_fails(tmp_path):
    argv, _b, _c = _admitted_setup(tmp_path)
    paths = _oracle(tmp_path / "ob", "b2", "f" * 64)
    argv = _replace_oracle(argv, "b", [])
    reports = {d: {"path": p, "sha256": _sha(p)} for d, p in zip(compare.DIALECTS, paths)}
    manifest = tmp_path / "lines.json"
    manifest.write_text(json.dumps({"lines": [{"name": "b", "library_sha256": LIB_B,
                                               "reports": reports}]}))
    rc, res, _md = _run(tmp_path, argv + ["--oracle-lines", str(manifest)])
    assert _check(res, "oracle_binding:b")["status"] == "fail" and rc == 1
    # the list itself naming another library than the line's coverage reports
    manifest.write_text(json.dumps({"lines": [{"name": "b", "library_sha256": "f" * 64,
                                               "reports": reports}]}))
    rc, res, _md = _run(tmp_path, argv + ["--oracle-lines", str(manifest)])
    assert _check(res, "oracle_binding:b")["status"] == "fail" and rc == 1


def _archive_setup(tmp_path, lib_bytes=None, lib_mtime=None, doc_text=None, header_lib=None):
    """Line b with reports older than the library_sha256 header field and
    an archive binding: the library file, its mtime, one document."""
    libfile = tmp_path / "archive" / "libbolorgir.so.0.1.0"
    libfile.parent.mkdir(parents=True, exist_ok=True)
    libfile.write_bytes(lib_bytes or b"line library")
    os.utime(libfile, (lib_mtime or 1_700_000_000, lib_mtime or 1_700_000_000))
    line_lib = hashlib.sha256(b"line library").hexdigest()
    doc = tmp_path / "archive" / "history.md"
    doc.write_text(doc_text or "Oracle reports on lib `abc...` (the final build).\n")
    base, cand = tmp_path / "base", tmp_path / "cand"
    _corpora(base, line_lib, _repo_records(), _mb_records())
    paths = _corpora(cand, LIB_C, _repo_records(), _mb_records())
    argv = ["--candidate", f"c={cand}", "--line", f"b={base}",
            "--d4", _d4(tmp_path / "d4.json", LIB_C),
            "--triage", _triage(tmp_path / "triage.json", "c", paths, LIB_C)]
    for p in _oracle(tmp_path / "oracle", "c", LIB_C):
        argv += ["--oracle", f"c={p}"]
    lpaths = _oracle(tmp_path / "ob", "b", None, header={
        "library": str(header_lib or libfile), "date_utc": "2023-11-15T00:00:00Z"})
    reports = {d: {"path": p, "sha256": _sha(p)} for d, p in zip(compare.DIALECTS, lpaths)}
    binding = {"kind": "archive", "library_path": str(libfile),
               "documents": [{"path": str(doc), "sha256": _sha(doc),
                              "contains": ["Oracle reports on lib `abc...`"]}]}
    manifest = tmp_path / "lines.json"
    manifest.write_text(json.dumps({"lines": [{"name": "b", "library_sha256": line_lib,
                                               "binding": binding, "reports": reports}]}))
    return argv + ["--oracle-lines", str(manifest)], doc


def test_archive_binding_of_reports_without_the_header_hash(tmp_path):
    argv, _doc = _archive_setup(tmp_path)
    rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "oracle_binding:b")["status"] == "ok"
    assert "archive binding" in _check(res, "oracle_binding:b")["detail"]
    assert rc == 0 and res["acceptance"]["verdict"] == "admitted"


def test_archive_binding_that_does_not_hold_fails(tmp_path):
    cases = [
        {"lib_mtime": 1_800_000_000},                      # written after the reports
        {"lib_bytes": b"another library"},                 # not the line's bytes
        {"header_lib": "/elsewhere/libbolorgir.so.0.1.0"},  # the reports name another file
    ]
    for i, kw in enumerate(cases):
        d = tmp_path / str(i)
        d.mkdir()
        argv, _doc = _archive_setup(d, **kw)
        rc, res, _md = _run(d, argv)
        assert _check(res, "oracle_binding:b")["status"] == "fail", kw
        assert rc == 1, kw
    d = tmp_path / "doc"
    d.mkdir()
    argv, doc = _archive_setup(d)
    doc.write_text("edited\n")
    rc, res, _md = _run(d, argv)
    assert _check(res, "oracle_binding:b")["status"] == "fail" and rc == 1


def test_oracle_pass_drop_fails(tmp_path):
    argv, _b, _c = _admitted_setup(tmp_path, line_oracle_pass=11)
    rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "oracle_pass:b")["status"] == "fail" and rc == 1


def test_oracle_zero_counts_are_normalised(tmp_path):
    argv, _b, _c = _admitted_setup(tmp_path, cand_oracle_kw={"engine_errors": 0})
    _rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "goal_oracle")["detail"].startswith("2020-12 ENGINE_ERROR 0")
    argv, _b, _c = _admitted_setup(tmp_path / "x", cand_oracle_kw={"engine_errors": 11})
    _rc, res, _md = _run(tmp_path / "x", argv)
    assert _check(res, "goal_oracle")["status"] == "fail"


def test_oracle_report_of_another_library_fails(tmp_path):
    argv, _b, _c = _admitted_setup(tmp_path)
    argv = _replace_oracle(argv, "c", _oracle(tmp_path / "o2", "c", "f" * 64))
    rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "oracle_identity")["status"] == "fail" and rc == 1


# --- identity, protocol, sample, triage ------------------------------------

def _call_refused(d, doc, only=None):
    """Compile refusals of the call itself, as in the first dbc5eeb oracle
    run (full-size compile request, 2026-10-10): every row, or the rows
    with test_index in `only` (the summary still counts the rows)."""
    rows = []
    for r in doc["rows"]:
        if only is None or r["test_index"] in only:
            r = {k: r[k] for k in ("draft", "suite_file", "case", "test_index")}
            r.update(outcome="SKIPPED", schema_compiled=False, engine=None, validator=None,
                     refusal={"status": "INVALID_ARGUMENT", "pointer": None,
                              "message": "bad compile request struct_size",
                              "schema_offset": 4294967295, "classification": "needs_review"})
        rows.append(r)
    doc["rows"] = rows
    doc["summary"] = _oracle_summary(d, rows)


def _empty(d, doc):
    doc["rows"] = []
    doc["summary"] = _oracle_summary(d, [])


def _forged_summary(d, doc):
    doc["summary"]["rows"] += 3
    doc["summary"]["by_file"]["f.json"]["compiled"] += 3


@pytest.mark.parametrize("name, edit, dialects, want", [
    ("every compile refused by the call (the dbc5eeb run)", _call_refused, compare.DIALECTS,
     "compile refusals of the call itself"),
    ("empty reports", _empty, compare.DIALECTS, "no rows"),
    ("one empty dialect among four", _empty, ("draft7",), "no rows"),
    ("forged summary counters", _forged_summary, ("draft6",),
     "the summary does not count the rows: rows 15 for 12, by_file compiled"),
    ("one call refusal among rows that ran",
     lambda d, doc: _call_refused(d, doc, only={3}), ("draft2019-09",),
     "1 compile refusals of the call itself"),
    ("PASS without an engine verdict",
     lambda d, doc: doc["rows"][0].update(engine=None), ("draft4",),
     "inconsistent rows, e.g. row 0: PASS without an engine verdict"),
])
def test_line_oracle_report_that_is_no_evidence_blocks(tmp_path, name, edit, dialects, want):
    """A line report without evidence about its library counts as no data:
    oracle_content fails, the line is not `with data`, and oracle_inputs and
    oracle_pass fail for its dialects even though PASS is not lower."""
    argv, _b, _c = _admitted_setup(tmp_path)
    argv = _replace_oracle(argv, "b", _oracle(
        tmp_path / "o2", "b", LIB_B, edit=lambda d, doc: edit(d, doc) if d in dialects else None))
    rc, res, _md = _run(tmp_path, argv)
    assert rc == 1, name
    content = _check(res, "oracle_content:b")
    assert content["status"] == "fail" and want in content["detail"], (name, content)
    assert _check(res, "oracle_content:c")["status"] == "ok"
    lines = _check(res, "oracle_lines")
    assert lines["status"] == "fail" and "no evidence: b" in lines["detail"], lines
    for c in ("oracle_inputs:b", "oracle_pass:b"):
        assert _check(res, c)["status"] == "fail" and dialects[0] in _check(res, c)["detail"], c
    assert sorted(res["oracle_problems"]["b"]) == sorted(dialects)


def test_candidate_oracle_report_that_is_no_evidence_blocks(tmp_path):
    argv, _b, _c = _admitted_setup(tmp_path)
    argv = _replace_oracle(argv, "c", _oracle(
        tmp_path / "o2", "c", LIB_C, edit=lambda d, doc: _empty(d, doc)
        if d == "draft2020-12" else None))
    rc, res, _md = _run(tmp_path, argv)
    assert rc == 1
    assert _check(res, "oracle_content:c")["status"] == "fail"
    goal = _check(res, "goal_oracle")
    assert goal["status"] == "fail" and "no evidence" in goal["detail"]
    assert _check(res, "oracle_pass:b")["status"] == "fail"
    assert _check(res, "oracle_lines")["status"] == "ok"


def test_forged_outcome_counts_are_unusable(tmp_path):
    argv, _b, _c = _admitted_setup(tmp_path)

    def forge(d, doc):
        doc["summary"]["by_outcome"]["PASS"] += 1
    argv = _replace_oracle(argv, "b", _oracle(tmp_path / "o2", "b", LIB_B, edit=forge))
    rc, _res, _md = _run(tmp_path, argv)
    assert rc == compare.EXIT_UNUSABLE


def test_oracle_content_of_a_consistent_report():
    rows = [_oracle_row("draft7", i, oc)
            for i, oc in enumerate(["PASS", "SKIPPED", "ENGINE_ERROR", "PASS"])]
    doc = {"header": {"drafts": ["draft7"]}, "summary": _oracle_summary("draft7", rows),
           "rows": rows}
    assert compare.oracle_content_problems(doc) == []
    doc["rows"][2]["engine"] = "accept"
    assert "ENGINE_ERROR without an engine error" in compare.oracle_content_problems(doc)[0]
    only_errors = [_oracle_row("draft7", i, "ENGINE_ERROR") for i in range(2)]
    assert compare.oracle_content_problems(
        {"header": {"drafts": ["draft7"]}, "summary": _oracle_summary("draft7", only_errors),
         "rows": only_errors}) == ["no engine verdict"]


def test_different_libraries_in_the_two_corpora_fail(tmp_path):
    argv, _b, cand = _admitted_setup(tmp_path)
    _write(cand, "maskbench", "f" * 64, _mb_records())
    rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "library:c")["status"] == "fail" and rc == 1


def test_changed_hf_sample_fails(tmp_path):
    argv, _b, cand = _admitted_setup(tmp_path)
    recs = _repo_records()
    out = _names("q", 1, sampled=False)[0]
    for r in recs:
        if r["file"] == out:
            r["generatable"]["llama-3.1"] = _gen(*GOOD)
    _write(cand, "repo", LIB_C, recs)
    rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "hf_sample:c:repo:llama-3.1")["status"] == "fail" and rc == 1


def test_protocol_difference_needs_an_explanation(tmp_path):
    argv, _b, cand = _admitted_setup(tmp_path)
    _write(cand, "repo", LIB_C, _repo_records(), max_steps=4096)
    paths = [os.path.join(cand, "coverage_repo.json"), os.path.join(cand, "coverage_maskbench.json")]
    argv[argv.index("--triage") + 1] = _triage(tmp_path / "t2.json", "c", paths, LIB_C)
    rc, res, md = _run(tmp_path, argv)
    assert _check(res, "protocol:b:repo")["status"] == "fail" and rc == 1
    assert "NOT EXPLAINED" in md
    notes = tmp_path / "notes.json"
    notes.write_text(json.dumps({"entries": [{"parameter": "max_steps", "lines": ["b"],
                                              "corpora": ["repo"], "candidate": 4096,
                                              "line": 8192, "explanation": "test"}]}))
    rc, res, _md = _run(tmp_path, argv + ["--protocol-notes", str(notes)])
    assert _check(res, "protocol:b:repo")["status"] == "ok" and rc == 0


def test_missing_corpus_records_fail(tmp_path):
    argv, _b, cand = _admitted_setup(tmp_path)
    recs = _repo_records()[1:]
    _write(cand, "repo", LIB_C, recs)
    paths = [os.path.join(cand, "coverage_repo.json"), os.path.join(cand, "coverage_maskbench.json")]
    argv[argv.index("--triage") + 1] = _triage(tmp_path / "t2.json", "c", paths, LIB_C)
    rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "corpus:b:repo")["status"] == "fail" and rc == 1
    assert any(r["candidate"] == "missing" for r in res["transitions"])


def test_triage_of_other_reports_or_failed_gate_blocks(tmp_path):
    argv, _b, cand = _admitted_setup(tmp_path)
    paths = [os.path.join(cand, "coverage_repo.json")]
    argv[argv.index("--triage") + 1] = _triage(tmp_path / "t2.json", "c", paths, LIB_C)
    rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "triage")["status"] == "fail" and rc == 1
    paths = [os.path.join(cand, "coverage_repo.json"), os.path.join(cand, "coverage_maskbench.json")]
    argv[argv.index("--triage") + 1] = _triage(tmp_path / "t3.json", "c", paths, LIB_C,
                                               exit_status=1)
    rc, res, _md = _run(tmp_path, argv)
    assert "triage gate failed" in _check(res, "triage")["detail"] and rc == 1


def test_unavailable_goal_fails_above_one_percent(tmp_path):
    argv, _b, _c = _admitted_setup(tmp_path, cand_mb=_mb_records(n_unavailable=2))
    rc, res, _md = _run(tmp_path, argv)
    assert _check(res, "goal_unavailable")["status"] == "fail" and rc == 1


def test_in_sample_copy_matches_the_harness():
    src = open(os.path.join(ROOT, "benchmarks", "bench_jsb_coverage.py"), encoding="utf-8").read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)
              and n.name == "in_sample")
    ns = {"hashlib": hashlib}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "in_sample", "exec"), ns)
    for i in range(500):
        for every in (1, 20, 7):
            assert ns["in_sample"]("D", f"s{i}.json", every) == compare.in_sample("D", f"s{i}.json", every)


# --- triage_semantic -------------------------------------------------------

def test_triage_refusal_outcome():
    rec = {"error_class": "resource_limit", "error_stage": "compile", "status": "RESOURCE_LIMIT"}
    g1 = triage.refusal_outcome(rec)
    assert g1["outcome"] == "compile:resource_limit"
    row = triage.transition_row("a", "b", "repo", {"file": "x.json"}, "byte",
                                {"counts": {"completed_valid": 20}}, g1, None)
    assert (row["class"], row["final"]) == ("C", "compile:resource_limit")
    missing = triage.refusal_outcome(None)
    row = triage.transition_row("a", "b", "repo", {"file": "x.json"}, "byte",
                                {"counts": {"completed_valid": 20}}, missing, None)
    assert row["class"] == "unclassified"


def _review(tmp_path, env_extra, out="out"):
    """r6_review.sh on a reports dir of the library LIB_C; the library
    checks come before the triage."""
    import subprocess
    reports = tmp_path / "reports"
    _write(str(reports), "repo", LIB_C, _repo_records())
    (reports / "coverage_repo.passes.json.gz").write_bytes(b"")
    env = {k: v for k, v in os.environ.items() if k not in ("R6_LIB", "BLG_LIB_PATH")}
    env.update(env_extra)
    return subprocess.run(["bash", os.path.join(ROOT, "benchmarks/t3/r6_review.sh"), str(reports),
                           "t3-c", str(tmp_path / out)], env=env, capture_output=True,
                          text=True, timeout=120)


def test_r6_review_needs_the_candidate_library_for_the_triage(tmp_path):
    """The triage imports the ctypes bindings, which load a library at
    import: r6_review.sh takes it as R6_LIB and never what zig-out/lib of
    the tree holds (missing in a worktree, another build in the main tree)."""
    r = _review(tmp_path, {})
    assert r.returncode == 2 and "R6_LIB" in r.stderr, r.stderr
    assert "verdict=unusable exit=2" in r.stdout
    assert not (tmp_path / "out").exists()  # a refusal writes nothing
    other = tmp_path / "other.so"
    other.write_bytes(b"another build")
    r = _review(tmp_path, {"R6_LIB": str(other)}, out="out2")
    assert r.returncode == 2 and "names the library " + LIB_C in r.stderr, r.stderr
    assert "R6_LIB is not the library of the candidate's reports" in r.stderr
    assert not (tmp_path / "out2").exists()


def test_r6_publish_hands_the_run_library_to_the_review():
    text = open(os.path.join(ROOT, "benchmarks/t3/r6_publish.sh"), encoding="utf-8").read()
    assert 'R6_LIB="$RUN/lib/libbolorgir.so.0.1.0"' in text
    review = open(os.path.join(ROOT, "benchmarks/t3/r6_review.sh"), encoding="utf-8").read()
    triage_calls = [line for line in review.splitlines() if "triage_semantic.py" in line
                    and '"$PY"' in line and not line.lstrip().startswith("#")]
    assert triage_calls and all(line.startswith('BLG_LIB_PATH="$R6_LIB" ') for line in triage_calls)


def test_tracked_decision_files_are_consistent():
    """The tracked D4 list keeps every decision with the owner, the oracle
    line list names five dialects or a reason, the protocol notes bind
    values."""
    d4 = json.load(open(os.path.join(ROOT, "benchmarks/t3/d4_proposals.json"), encoding="utf-8"))
    assert all(e["owner_decision"] is None for e in d4["entries"])
    rule = [e for e in d4["entries"] if e.get("file") == "*"]
    assert rule and all(e.get("requires_triage_class") == "D" for e in rule)
    lines = json.load(open(os.path.join(ROOT, "benchmarks/t3/oracle_lines.json"), encoding="utf-8"))
    for line in lines["lines"]:
        assert len(line["library_sha256"]) == 64
        if line["reports"]:
            assert sorted(line["reports"]) == sorted(compare.DIALECTS)
            b = line.get("binding")
            assert b is None or (b["kind"] == "archive" and b["documents"])
        else:
            # an exception without data is the owner's decision, never pre-filled
            assert line["missing_reason"] and line["owner_decision"] is None
    notes = json.load(open(os.path.join(ROOT, "benchmarks/t3/protocol_notes.json"), encoding="utf-8"))
    for e in notes["entries"]:
        assert {"parameter", "lines", "candidate", "line", "explanation"} <= set(e)


if __name__ == "__main__":
    sys.exit(0)
