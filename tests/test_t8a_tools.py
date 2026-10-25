"""T8a identity manifest, report and the T5b gate on synthetic inputs
(strict smoke, the five participants), and the memory limit of run_t8a.sh
against the one MaskBench sets; no engine and no MaskBench run is
loaded."""

import importlib.util
import json
import os
import resource
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T8A = os.path.join(ROOT, "benchmarks", "maskbench", "t8a")
sys.path.insert(0, T8A)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ident = _load("t8a_identity", os.path.join(T8A, "t8a_identity.py"))
report = _load("t8a_report", os.path.join(T8A, "t8a_report.py"))
t5b = _load("t5b_readme_numbers", os.path.join(ROOT, "benchmarks", "t5b_readme_numbers.py"))

CORE = b"core library bytes"
REV = "a2856192dd7c25b842431f39c179a6c2c2f627d1"
RUNS = ("blg-spec-v1", "llg-pinned", "xgr-pinned", "llg-current", "xgr-current")
VERSIONS = {"venv-pinned": {"bolorgir": "0.2.0rc1", "llguidance": "0.7.10", "xgrammar": "0.1.17"},
            "venv-current": {"bolorgir": "0.2.0rc1", "llguidance": "1.3.0", "xgrammar": "0.1.25"}}


# --- identity manifest -----------------------------------------------------

def _tree(tmp_path, core=CORE, serializer=b"def serialize_value(): ...", corpus_extra=b"",
          rev=REV):
    wheel = tmp_path / "bolorgir-0.2.0rc1-cp310-abi3-manylinux_2_28_x86_64.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("bolorgir/_lib/libbolorgir.so.0.1.0", CORE)
        z.writestr("bolorgir/serializer.py", b"def serialize_value(): ...")
    venvs = []
    for name in ("venv-pinned", "venv-current"):
        pkg = tmp_path / name / "lib" / "python3.10" / "site-packages" / "bolorgir"
        (pkg / "_lib").mkdir(parents=True, exist_ok=True)
        (pkg / "_lib" / "libbolorgir.so.0.1.0").write_bytes(core if name == "venv-pinned" else CORE)
        (pkg / "serializer.py").write_bytes(serializer if name == "venv-pinned"
                                            else b"def serialize_value(): ...")
        (pkg / "__init__.py").write_text("__version__ = '0.2.0rc1'\n")
        venvs.append(str(tmp_path / name))
    mb = tmp_path / "maskbench"
    (mb / "maskbench").mkdir(parents=True, exist_ok=True)
    (mb / "maskbench" / "runner.py").write_text("# runner\n")
    data = mb / "data"
    data.mkdir(exist_ok=True)
    (data / "a.json").write_bytes(b'{"schema": {}, "tests": []}' + corpus_extra)
    (data / "b.json").write_bytes(b'{"schema": {}, "tests": []}')
    tok = tmp_path / "snapshots" / rev
    tok.mkdir(parents=True, exist_ok=True)
    (tok / "tokenizer.json").write_text("{}")
    (tok / "config.json").write_text("{}")
    return ["record", "--wheel", str(wheel), "--venv", venvs[0], "--venv", venvs[1],
            "--maskbench", str(mb), "--data", str(data), "--tokenizer-dir", str(tok),
            "--tokenizer-revision", REV, "--threads", "20"]


def _versions_of(py, versions=VERSIONS):
    return dict(versions["venv-pinned" if "venv-pinned" in py else "venv-current"])


def _manifest(tmp_path, monkeypatch, versions=VERSIONS, **kw):
    monkeypatch.setattr(ident, "pip_freeze", lambda py: ["bolorgir==0.2.0rc1", "transformers==5.18.0"])
    monkeypatch.setattr(ident, "python_version", lambda py: "Python 3.10.12")
    monkeypatch.setattr(ident, "module_versions", lambda py: _versions_of(py, versions))
    argv = _tree(tmp_path, **kw)
    out = tmp_path / "manifest.json"
    rc = ident.main(argv + ["--out", str(out)])
    return rc, json.loads(out.read_text()), argv


def test_manifest_of_consistent_inputs(tmp_path, monkeypatch):
    rc, m, argv = _manifest(tmp_path, monkeypatch)
    assert rc == 0 and ident.problems(m) == []
    assert ident.main(["check", *argv[1:], "--manifest", str(tmp_path / "manifest.json")]) == 0
    parts = m["participants"]
    assert sorted(parts) == sorted(RUNS)
    assert parts["blg-spec-v1"] == {"venv": "venv-pinned", "module": "bolorgir",
                                    "engine_id": "blg-spec-v1-ser-t8a", "version": "0.2.0rc1"}
    assert parts["llg-current"]["version"] == "1.3.0"


def test_pinned_engine_with_another_version_or_missing_module_is_caught(tmp_path, monkeypatch):
    bad = {"venv-pinned": dict(VERSIONS["venv-pinned"], xgrammar="0.1.18"),
           "venv-current": dict(VERSIONS["venv-current"], llguidance=None)}
    rc, m, _argv = _manifest(tmp_path, monkeypatch, versions=bad)
    assert rc == 1
    probs = ident.problems(m)
    assert any("xgr-pinned" in p and "the pin is 0.1.17" in p for p in probs)
    assert any("llg-current" in p and "not installed" in p for p in probs)


def test_replaced_library_with_the_same_freeze_is_caught(tmp_path, monkeypatch):
    rc, m, argv = _manifest(tmp_path, monkeypatch)
    lib = tmp_path / "venv-pinned/lib/python3.10/site-packages/bolorgir/_lib/libbolorgir.so.0.1.0"
    lib.write_bytes(b"another build")
    new = ident.build(ident.argparse.Namespace(**_ns(argv)))
    assert any("package_digest" in d for d in ident.compare(m, new))
    assert any("installed core" in p for p in ident.problems(new))
    assert ident.main(["check", *argv[1:], "--manifest", str(tmp_path / "manifest.json")]) == 1


def _ns(argv):
    args = ident.argparse.ArgumentParser()
    for opt in ("--wheel", "--maskbench", "--data", "--tokenizer-dir", "--tokenizer-revision",
                "--files-list", "--acceptance", "--out", "--manifest"):
        args.add_argument(opt, default="")
    args.add_argument("--venv", action="append")
    for opt in ("--threads", "--time-limit", "--mem-limit", "--smoke-files"):
        args.add_argument(opt, type=int, default={"--time-limit": 900, "--mem-limit": 40}.get(opt, 0))
    return vars(args.parse_args(argv[1:]))


def test_changed_corpus_file_is_caught(tmp_path, monkeypatch):
    _rc, m, argv = _manifest(tmp_path, monkeypatch)
    with open(tmp_path / "maskbench" / "data" / "a.json", "ab") as f:
        f.write(b" ")
    new = ident.build(ident.argparse.Namespace(**_ns(argv)))
    assert any(d.startswith("corpus.digest") for d in ident.compare(m, new))


def test_other_tokenizer_revision_is_caught(tmp_path, monkeypatch):
    _rc, m, argv = _manifest(tmp_path, monkeypatch)
    other = tmp_path / "snapshots" / ("0" * 40)
    other.mkdir()
    (other / "tokenizer.json").write_text("{}")
    (other / "config.json").write_text("{}")
    ns = _ns(argv)
    ns["tokenizer_dir"] = str(other)
    new = ident.build(ident.argparse.Namespace(**ns))
    assert any(d.startswith("tokenizer.revision") for d in ident.compare(m, new))
    assert any("pinned" in p for p in ident.problems(new))


def test_changed_serializer_is_caught(tmp_path, monkeypatch):
    rc, m, _argv = _manifest(tmp_path, monkeypatch, serializer=b"def serialize_value(): 2")
    assert rc == 1
    assert any("serializer" in p for p in ident.problems(m))


# --- report ----------------------------------------------------------------

FILES = ("a.json", "b.json", "c.json")


def _ref():
    files = {}
    for n in FILES:
        files[n] = [{"i": 0, "valid": True, "instance_sha256": f"{n}-0", "ids_sha256": f"{n}-ids0",
                     "n_ids": 3},
                    {"i": 1, "valid": False, "instance_sha256": f"{n}-1", "ids_sha256": f"{n}-ids1",
                     "n_ids": 4}]
    return {"tokenizer": REV, "files": files}


# The complete protocol of the accepted class file of the synthetic runs
# (t8a_report.PROTOCOL_KEYS): every condition with the run's value.
PROTO = {"threads": 1, "time_limit": 900, "mem_limit": 40, "profile": "spec-v1",
         "serializer": "bolorgir", "compact": True, "smoke_files": 0,
         "tokenizer_revision": REV, "wheel_sha256": "w" * 64}


def _report_manifest(t3=("admitted", "c" * 64), smoke=0):
    venvs = {v: {"core_sha256": ["c" * 64], "serializer_sha256": "s", "package_digest": "p",
                 "modules": dict(mods)} for v, mods in VERSIONS.items()}
    return {"parameters": {"smoke_files": smoke, "tokenizer_revision": REV,
                           "threads": 1, "time_limit": 900, "mem_limit": 40,
                           "profile": "spec-v1", "serializer": "bolorgir", "compact": True},
            "wheel": {"core_sha256": "c" * 64, "sha256": "w" * 64}, "venvs": venvs,
            "participants": ident.participants(venvs),
            "tokenizer": {"revision": REV},
            "corpus": {"files": len(FILES), "index": {n: "h" for n in FILES}},
            "t3_acceptance": None if t3 is None else {"verdict": t3[0],
                                                       "candidate_library_sha256": t3[1]}}


def _meta_of(name):
    p = ident.participants({v: {"modules": m} for v, m in VERSIONS.items()})[name]
    return {"id": p["engine_id"], "module": p["module"], "module_version": p["version"]}


def _chunk_log(ends):
    """log.txt of scripts/run_maskbench.py: per file a chunk that ended on
    it with the given exit code, after a file it finished."""
    out = []
    for n, code in ends.items():
        done = "c.json" if n == "a.json" else "a.json"
        out += [f"Running command: python -m paired_runner --multi data/{done} data/{n}",
                "2 files, timeout 900s", f"data/{done}", f"data/{n}", "", f"Exit code: {code}", ""]
    return "\n".join(out) + "\n"


def _write_run(d, name, skip=(), pending=(), errors=None, streams_over=None, meta=None,
               timeouts=0, chunk_ends=None):
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.txt").write_text(json.dumps(meta or _meta_of(name)))
    if timeouts:
        (d / "log.txt").write_text("Exit code: -14\n" * timeouts)
    if chunk_ends:
        (d / "log.txt").write_text(_chunk_log(chunk_ends))
    (d / "t8a_streams").mkdir(exist_ok=True)
    ref = _ref()["files"]
    for n in FILES:
        if n in skip:
            continue
        if n in pending:
            (d / n).write_text(json.dumps({"pending_file": 1}))
            continue
        rec = {"id": n, "ttfm_us": 100, "num_tests": 2, "num_tokens": 7,
               "all_mask_us": [5, 6, 7], "num_valid_tests": 1, "num_invalid_tests": 1}
        if errors and n in errors:
            rec["validation_error"] = errors[n]
        (d / n).write_text(json.dumps(rec))
        tests = [dict(t, consumed=t["n_ids"] if t["valid"] else 1, rejected_at=None
                      if t["valid"] else 1) for t in ref[n]]
        if streams_over and n in streams_over:
            tests[streams_over[n][0]].update(streams_over[n][1])
        (d / "t8a_streams" / n).write_text(json.dumps({"id": n, "tests": tests}))


def _report(tmp_path, manifest=None, runs=None, t2c=None, names=RUNS, extra_argv=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(manifest or _report_manifest()))
    spath = tmp_path / "streams.json"
    spath.write_text(json.dumps(_ref()))
    argv = ["--manifest", str(mpath), "--streams", str(spath), "--bolorgir", "blg-spec-v1",
            "--out-md", str(tmp_path / "t8a.md"), "--out-json", str(tmp_path / "t8a.json"),
            *extra_argv]
    for name in names:
        d = tmp_path / "out" / name
        if runs is None or name not in runs:
            _write_run(d, name)
        else:
            runs[name](d, name)
        argv += ["--run", f"{name}={d}"]
    if t2c is not None:
        tp = tmp_path / "t2c.json"
        tp.write_text(json.dumps(t2c))
        argv += ["--t2c", str(tp)]
    rc = report.main(argv)
    if rc == 2:
        return rc, None
    return rc, json.loads((tmp_path / "t8a.json").read_text())


def test_complete_matching_runs_are_accepted(tmp_path):
    rc, out = _report(tmp_path)
    assert rc == 0 and out["t8a"]["accepted"] and out["t8a"]["inputs_complete"]
    assert out["t8a"]["smoke"] is None
    assert out["intersection"]["schemas"] == len(FILES)
    full = out["full"]["blg-spec-v1"]
    assert full["passing"] == len(FILES) and full["TTFM p50"] == 100 and full["TBM p50"] == 6


def test_empty_run_directories_are_not_accepted(tmp_path):
    def empty(d, name):
        d.mkdir(parents=True, exist_ok=True)
    rc, out = _report(tmp_path, runs={n: empty for n in RUNS})
    assert rc == 1 and not out["t8a"]["inputs_complete"]
    assert any("missing outputs" in p for p in out["t8a"]["problems"])


def test_file_missing_in_every_run_is_found(tmp_path):
    def without_c(d, name):
        _write_run(d, name, skip=("c.json",))
    rc, out = _report(tmp_path, runs={n: without_c for n in RUNS})
    assert rc == 1
    assert sum("1 missing outputs" in p for p in out["t8a"]["problems"]) == len(RUNS)


def test_pending_bolorgir_file_needs_a_class(tmp_path):
    runs = {"blg-spec-v1": lambda d, n: _write_run(d, n, pending=("b.json",),
                                                    chunk_ends={"b.json": -14})}
    rc, out = _report(tmp_path, runs=runs)
    assert rc == 1 and out["t8a"]["inputs_complete"]
    e = out["bolorgir_errors"][0]
    assert (e["status"], e["outcome"], e["detail"]) == ("crash_or_timeout", "timeout",
                                                        "chunk exit -14")
    assert e["class"] is None and e["open_reason"] == "no class file"
    rc, out = _report(tmp_path / "x", runs=runs, t2c={"core_sha256": "c" * 64,
                                                      "protocol": dict(PROTO),
                                                      "entries": {
        "b.json": {"class": "C", "status": "timeout", "note": "SIGALRM"}}})
    assert rc == 0 and out["bolorgir_errors"][0]["class"] == "C"


def _open_case(tmp_path, entry, pending_end=-14, errors=None, t2c_add=None):
    """The Bolorgir case of a report whose class file has the given entry
    for its file (b.json pending, or a.json with a validation error) and,
    unless t2c_add replaces it, the run's protocol."""
    if errors:
        runs = {"blg-spec-v1": lambda d, n: _write_run(d, n, errors=errors)}
        name = "a.json"
    else:
        ends = None if pending_end is None else {"b.json": pending_end}
        runs = {"blg-spec-v1": lambda d, n: _write_run(d, n, pending=("b.json",),
                                                        chunk_ends=ends)}
        name = "b.json"
    t2c = {"core_sha256": "c" * 64, "protocol": dict(PROTO), "entries": {name: entry}}
    if t2c_add is not None:
        t2c.pop("protocol", None)
        t2c.update(t2c_add)
    rc, out = _report(tmp_path, runs=runs, t2c=t2c)
    return rc, out["bolorgir_errors"][0]


def test_t2c_class_needs_the_exact_outcome(tmp_path):
    # a SIGSEGV is not the SIGALRM a class C was given for
    rc, e = _open_case(tmp_path / "segv", {"class": "C", "status": "timeout"}, pending_end=-11)
    assert rc == 1 and e["class"] is None and e["outcome"] == "crash:-11"
    assert e["open_reason"] == "the class entry is for 'timeout', the case is 'crash:-11'"
    rc, e = _open_case(tmp_path / "segv-ok", {"class": "C", "status": "crash:-11"},
                       pending_end=-11)
    assert rc == 0 and e["class"] == "C"
    # the general status string of the tables is no outcome
    rc, e = _open_case(tmp_path / "general", {"class": "C", "status": "crash_or_timeout"})
    assert rc == 1 and e["class"] is None and "'crash_or_timeout'" in e["open_reason"]
    # a pending marker that is not the end of a chunk in log.txt
    rc, e = _open_case(tmp_path / "nolog", {"class": "C", "status": "timeout"},
                       pending_end=None)
    assert rc == 1 and e["outcome"] is None and "not the end of a chunk" in e["open_reason"]
    # a validation error changed into an invalidation error
    err = {"a.json": "test #1: should reject but didn't"}
    rc, e = _open_case(tmp_path / "changed", {"class": "D", "status": "validation_error"},
                       errors=err)
    assert rc == 1 and e["class"] is None and e["outcome"] == "invalidation_error"
    rc, e = _open_case(tmp_path / "same", {"class": "D", "status": "invalidation_error"},
                       errors=err)
    assert rc == 0 and e["class"] == "D"


def test_t2c_entry_without_status_is_never_applied(tmp_path):
    rc, e = _open_case(tmp_path / "pending", {"class": "C", "note": "old entry"})
    assert rc == 1 and e["class"] is None and e["open_reason"] == "the class entry names no status"
    rc, e = _open_case(tmp_path / "error", {"class": "D"},
                       errors={"a.json": "test #0: should accept but didn't"})
    assert rc == 1 and e["class"] is None and e["open_reason"] == "the class entry names no status"


def test_t2c_class_names_the_protocol_of_the_run(tmp_path):
    # a missing block keeps every case open
    rc, e = _open_case(tmp_path / "none", {"class": "C", "status": "timeout"}, t2c_add={})
    assert rc == 1 and e["class"] is None
    assert e["open_reason"] == "the class file names no protocol"
    # a block without a condition is refused: with only threads named,
    # another time limit would slip through silently
    rc, e = _open_case(tmp_path / "part", {"class": "C", "status": "timeout"},
                       t2c_add={"protocol": {"threads": 1}})
    assert rc == 1 and e["class"] is None and "lacks" in e["open_reason"]
    # an unknown key is refused
    rc, e = _open_case(tmp_path / "key", {"class": "C", "status": "timeout"},
                       t2c_add={"protocol": dict(PROTO, banana=1)})
    assert rc == 1 and e["class"] is None and "is not a protocol key" in e["open_reason"]
    # the run's full protocol applies the class
    rc, e = _open_case(tmp_path / "ok", {"class": "C", "status": "timeout"},
                       t2c_add={"protocol": dict(PROTO)})
    assert rc == 0 and e["class"] == "C"


def test_t2c_protocol_requires_every_condition(tmp_path):
    """Every condition of the approved protocol is required, with its type
    and value: a missing or null entry, a wrong type or another value
    (e.g. the memory limit 40 -> 80 or the time limit 900 -> 1800, alone
    or with a partial block) keeps the case open."""
    case = {"class": "C", "status": "timeout"}
    changed = {"threads": 2, "time_limit": 1800, "mem_limit": 80,
               "profile": "canonical-v1", "serializer": "other", "compact": False,
               "smoke_files": 20, "tokenizer_revision": "0" * 40, "wheel_sha256": "x" * 64}
    wrong_type = {"threads": "1", "compact": 1, "time_limit": 900.0}
    for k in sorted(PROTO):
        block = {k2: v for k2, v in PROTO.items() if k2 != k}
        rc, e = _open_case(tmp_path / f"no-{k}", case, t2c_add={"protocol": block})
        assert rc == 1 and e["class"] is None, k
        assert f"lacks {k}" in e["open_reason"], k
        rc, e = _open_case(tmp_path / f"null-{k}", case,
                           t2c_add={"protocol": dict(PROTO, **{k: None})})
        assert rc == 1 and e["class"] is None, k
        assert f"{k} is null" in e["open_reason"], k
        rc, e = _open_case(tmp_path / f"chg-{k}", case,
                           t2c_add={"protocol": dict(PROTO, **{k: changed[k]})})
        assert rc == 1 and e["class"] is None, k
        assert "is not the run's" in e["open_reason"], k
    for k, v in sorted(wrong_type.items()):
        rc, e = _open_case(tmp_path / f"type-{k}", case,
                           t2c_add={"protocol": dict(PROTO, **{k: v})})
        assert rc == 1 and e["class"] is None, k
        assert "is not of type" in e["open_reason"], k


def test_chunk_end_is_the_last_file_the_runner_started():
    lines = ["Running command: python -m paired_runner data/a.json data/b.json data/c.json",
             "3 files, timeout 900s", "data/c.json", "data/a.json", "", "Exit code: -14",
             "Running command: python -m paired_runner data/b.json data/d.json",
             "2 files, timeout 900s", "data/b.json", "data/d.json", "", "Exit code: 0",
             "Running command: python -m paired_runner data/e.json", "data/a.json",
             "Error running command: boom"]
    assert report.ended_on(lines) == {"a.json": -14}


def test_unclassified_or_foreign_t2c_blocks(tmp_path):
    runs = {"blg-spec-v1": lambda d, n: _write_run(
        d, n, errors={"a.json": "test #1: should reject but didn't"})}
    rc, out = _report(tmp_path, runs=runs)
    assert rc == 1 and out["bolorgir_errors"][0]["class"] is None
    rc, out = _report(tmp_path / "x", runs=runs, t2c={"core_sha256": "d" * 64,
                                                      "protocol": dict(PROTO),
                                                      "entries": {
        "a.json": {"class": "D", "status": "invalidation_error"}}})
    assert rc == 1 and any("for dddd" in c for c in out["t8a"]["conditions"])
    e = out["bolorgir_errors"][0]
    assert e["class"] is None and e["open_reason"].startswith("the class file is for core dddd")


def test_same_length_different_streams_are_caught(tmp_path):
    runs = {"xgr-current": lambda d, n: _write_run(
        d, n, streams_over={"a.json": (1, {"ids_sha256": "other"})})}
    rc, out = _report(tmp_path, runs=runs)
    assert rc == 1
    assert any("stream differences" in p and "ids_sha256" in p for p in out["t8a"]["problems"])


def test_negative_or_foreign_t3_acceptance_blocks(tmp_path):
    rc, out = _report(tmp_path, manifest=_report_manifest(t3=("not_admitted", "c" * 64)))
    assert rc == 1 and out["t8a"]["inputs_complete"]
    rc, out = _report(tmp_path / "x", manifest=_report_manifest(t3=("admitted", "e" * 64)))
    assert rc == 1


def test_smoke_report_is_never_an_acceptance(tmp_path):
    rc, out = _report(tmp_path, manifest=_report_manifest(smoke=20))
    assert rc == 1 and out["t8a"]["inputs_complete"] and not out["t8a"]["accepted"]
    assert out["t8a"]["smoke"] == {"passed": True, "failures": []}


def test_smoke_fails_on_a_pending_file_of_any_participant(tmp_path):
    for name in ("blg-spec-v1", "xgr-current"):
        d = tmp_path / name
        runs = {name: lambda dd, n: _write_run(dd, n, pending=("b.json",),
                                                chunk_ends={"b.json": -14})}
        # a class for the Bolorgir case does not let the smoke pass
        rc, out = _report(d, manifest=_report_manifest(smoke=20), runs=runs, t2c={
            "core_sha256": "c" * 64, "protocol": dict(PROTO, smoke_files=20),
            "entries": {"b.json": {"class": "C", "status": "timeout"}}})
        assert rc == 1 and out["t8a"]["inputs_complete"], name
        if name == "blg-spec-v1":
            assert out["bolorgir_errors"][0]["class"] == "C"
        assert out["t8a"]["smoke"]["passed"] is False, name
        assert any(f.startswith(f"{name}: 1 crash or timeout") for f in out["t8a"]["smoke"]["failures"])


def test_smoke_fails_on_a_timeout_line(tmp_path):
    runs = {"llg-pinned": lambda d, n: _write_run(d, n, timeouts=1)}
    _rc, out = _report(tmp_path, manifest=_report_manifest(smoke=20), runs=runs)
    assert out["t8a"]["smoke"]["passed"] is False


def test_smoke_fails_on_input_problems(tmp_path):
    runs = {"xgr-pinned": lambda d, n: _write_run(d, n, skip=("c.json",))}
    _rc, out = _report(tmp_path, manifest=_report_manifest(smoke=20), runs=runs)
    assert out["t8a"]["smoke"]["passed"] is False


def test_a_subset_of_the_participants_is_never_accepted(tmp_path):
    for keep in (("blg-spec-v1",), RUNS[:4]):
        d = tmp_path / str(len(keep))
        rc, out = _report(d, names=keep)
        assert rc == 1 and not out["t8a"]["accepted"] and not out["t8a"]["inputs_complete"]
        missing = sorted(set(RUNS) - set(keep))
        assert all(any(f"participant {m} has no --run" in p for p in out["t8a"]["problems"])
                   for m in missing)
        _rc, out = _report(d / "smoke", names=keep, manifest=_report_manifest(smoke=20))
        assert out["t8a"]["smoke"]["passed"] is False


def test_another_engine_under_a_participant_name_is_caught(tmp_path):
    cases = {"xgr-pinned": _meta_of("llg-pinned"),                       # llguidance as xgr
             "llg-pinned": dict(_meta_of("llg-pinned"), module_version="1.3.0"),  # current as pinned
             "blg-spec-v1": dict(_meta_of("blg-spec-v1"), id="blg-ser-t8a")}      # canonical-v1
    for name, meta in cases.items():
        runs = {name: lambda d, n, meta=meta: _write_run(d, n, meta=meta)}
        rc, out = _report(tmp_path / name, runs=runs)
        assert rc == 1 and not out["t8a"]["accepted"], name
        assert any(p.startswith(f"{name}: meta.txt is not the participant") for p in
                   out["t8a"]["problems"]), name


def test_duplicate_runs_and_a_foreign_bolorgir_name_are_refused(tmp_path):
    d = tmp_path / "dup"
    d.mkdir()
    _write_run(d / "x", "llg-pinned")
    rc, _out = _report(d, extra_argv=("--run", f"llg-pinned={d / 'x'}"))
    assert rc == 2
    rc, out = _report(tmp_path / "foreign", extra_argv=("--bolorgir", "llg-pinned"))
    assert rc == 1
    assert any("not the bolorgir participant" in p for p in out["t8a"]["problems"])


# --- T5b gate --------------------------------------------------------------

def _t5b_inputs(tmp_path, verdict="admitted", cand="c" * 64, t8a_ok=True, core="c" * 64):
    acc = {"acceptance": {"verdict": verdict, "failing": [] if verdict == "admitted"
                          else ["oracle_pass:7345490f"]},
           "inputs": {"candidate": {"name": "t3-x", "library_sha256": cand}},
           "totals": {"repo": {"t3-x": {"outcomes": {"byte": {"generatable": 9, "base": 10}}}}}}
    t8a = {"t8a": {"accepted": t8a_ok, "problems": [], "conditions": [] if t8a_ok else ["x"]},
           "core_sha256": core, "full": {"blg": {"passing": 1}},
           "intersection": {"schemas": 1, "stats": {"blg": {"TTFM p50": 1}}}}
    a, t = tmp_path / "acc.json", tmp_path / "t8a.json"
    a.write_text(json.dumps(acc))
    t.write_text(json.dumps(t8a))
    return ["--acceptance", str(a), "--t8a", str(t)]


def test_t5b_needs_both_gates_of_one_candidate(tmp_path, capsys):
    assert t5b.main(_t5b_inputs(tmp_path)) == 0
    assert "release build" in capsys.readouterr().out
    for kw in ({"verdict": "not_admitted"}, {"t8a_ok": False}, {"core": "e" * 64}):
        assert t5b.main(_t5b_inputs(tmp_path, **kw)) == 1, kw
        assert t5b.main(_t5b_inputs(tmp_path, **kw) + ["--preview"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("**PRELIMINARY") and "release build" not in out


# --- memory limit of run_t8a.sh ---------------------------------------------
# The MaskBench runner sets RLIMIT_AS of its processes to its --mem-limit
# (40 GB), soft and hard; under a lower hard limit set by run_t8a.sh every
# chunk failed with "not allowed to raise maximum limit" (t8a-smoke-1).

MASKBENCH_LIMIT = 40 << 30


def _run_t8a(tmp_path, **env_extra):
    """run_t8a.sh --smoke 1 with a missing wheel: it stops at the memory
    limit or at a later input check, before any venv is made."""
    env = {k: v for k, v in os.environ.items() if k != "T8A_MEM_LIMIT_MB"}
    env.update(env_extra)
    r = subprocess.run(["bash", os.path.join(T8A, "run_t8a.sh"), "--smoke", "1",
                        str(tmp_path / "w"), str(tmp_path / "missing.whl"), "1"],
                       env=env, capture_output=True, text=True, timeout=60)
    assert not (tmp_path / "w" / "venv-pinned").exists()
    return r


def test_t8a_memory_limit_below_the_maskbench_limit_is_refused(tmp_path):
    r = _run_t8a(tmp_path, T8A_MEM_LIMIT_MB="16384")
    assert r.returncode == 1
    assert "T8A_ABORT memory limit" in r.stdout


def test_t8a_default_memory_limit_is_the_maskbench_limit(tmp_path):
    """The default limit is the 40 GB of MaskBench; a run that inherits a
    lower hard limit cannot set it and stops at the memory limit too."""
    hard = resource.getrlimit(resource.RLIMIT_AS)[1]
    r = _run_t8a(tmp_path)
    assert r.returncode == 1
    if hard == resource.RLIM_INFINITY or hard >= MASKBENCH_LIMIT:
        assert f"T8A_LIMIT address space per process: {MASKBENCH_LIMIT >> 10} kB" in r.stdout
        assert "memory limit" not in r.stdout
        assert "T8A_ABORT" in r.stdout  # a later check: the missing wheel
    else:
        assert "T8A_ABORT memory limit" in r.stdout


def test_embedded_smoke_uses_the_run_threads():
    """The smoke of a full run uses the run's runner processes: no fixed 4
    workers; 4 x 40 GB exceeds this machine, where a full run starts with
    one."""
    with open(os.path.join(T8A, "run_t8a.sh"), encoding="utf-8") as f:
        calls = [line for line in f if '--smoke 20 "$W/smoke"' in line]
    assert len(calls) == 1, calls
    assert calls[0].rstrip().rstrip("\\").rstrip().endswith('"$WHEEL" "$THREADS"'), calls


def test_maskbench_limit_is_settable_only_under_a_hard_limit_as_high():
    """The mechanism, on small numbers: under blg_memory_limit of N MB the
    MaskBench call setrlimit(RLIMIT_AS, (G, G)) works when G <= N MB."""
    script = ('. "$1/benchmarks/protected_paths.sh" && blg_memory_limit "$2" && "$3" -c '
              '"import resource, sys; g = int(sys.argv[1]) << 20; '
              'resource.setrlimit(resource.RLIMIT_AS, (g, g))" "$4"')

    def run(n_mb, g_mb):
        return subprocess.run(["bash", "-c", script, "_", ROOT, str(n_mb), sys.executable,
                               str(g_mb)], capture_output=True, text=True, timeout=60)

    assert run(1024, 1024).returncode == 0
    low = run(1024, 2048)
    assert low.returncode != 0 and "not allowed to raise maximum limit" in low.stderr


# --- preflight of run_t8a.sh -------------------------------------------------
# A full run is bound to the admitted T3 verdict of its core and to the
# tokenizer snapshot of the acceptance pins; both are checked before any
# install, not by the report hours later (after the D4 decisions the
# admitted verdict is that of a later review directory, and a worktree has
# no HF cache).

pre = _load("t8a_preflight", os.path.join(T8A, "t8a_preflight.py"))
SNAP = f"hub/models--unsloth--Meta-Llama-3.1-8B-Instruct/snapshots/{REV}"
TOK = {"tokenizer.json": b'{"model": {}}', "config.json": b'{"vocab_size": 1}'}
MB_CHECKOUT = os.path.join(ROOT, "benchmarks", "external", "jsonschemabench-maskbench", "maskbench")
HF_MAIN = os.path.join(ROOT, "benchmarks", "local", "hf-cache")


def _pin_set(files):
    return {"hf": {"files": {f"{SNAP}/{n}": ident.sha256_bytes(b) for n, b in files.items()}}}


def _snapshot(tmp_path, files):
    d = tmp_path / "hf" / SNAP
    d.mkdir(parents=True)
    for n, b in files.items():
        (d / n).write_bytes(b)
    return d


def _verdict(tmp_path, verdict, lib, name="acceptance.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"acceptance": {"verdict": verdict},
                             "inputs": {"candidate": {"library_sha256": lib}}}))
    return str(p)


def _wheel(tmp_path, core=CORE):
    p = tmp_path / "bolorgir-0.2.0rc1-cp310-abi3-manylinux_2_28_x86_64.whl"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("bolorgir/_lib/libbolorgir.so.0.1.0", core)
    return str(p)


def test_preflight_tokenizer_snapshot_is_exactly_the_pinned_one(tmp_path):
    pins = _pin_set(TOK)
    d = _snapshot(tmp_path, TOK)
    assert pre.tokenizer_problems(str(d), REV, pins) == []
    (d / "config.json").write_bytes(b"{}")
    (d / "extra.json").write_bytes(b"x")
    bad = pre.tokenizer_problems(str(d), REV, pins)
    assert any("config.json is" in b for b in bad), bad
    assert any("extra.json is not a pinned file" in b for b in bad), bad
    (d / "tokenizer.json").unlink()
    assert any("tokenizer.json missing" in b for b in pre.tokenizer_problems(str(d), REV, pins))
    assert "no pinned files" in pre.tokenizer_problems(str(d), "0" * 40, pins)[0]
    assert "missing" in pre.tokenizer_problems(str(tmp_path / "none"), REV, pins)[0]


def test_preflight_full_run_needs_the_admitted_verdict_of_the_wheel_core(tmp_path):
    core = ident.sha256_bytes(CORE)
    assert pre.acceptance_problems(_verdict(tmp_path, "admitted", core), core) == []
    assert "needs T8A_ACCEPTANCE" in pre.acceptance_problems("", core)[0]
    first = _verdict(tmp_path, "not_admitted", core, "first.json")
    assert "not admitted" in pre.acceptance_problems(first, core)[0]
    other = _verdict(tmp_path, "admitted", "d" * 64, "other.json")
    assert "is not the wheel's" in pre.acceptance_problems(other, core)[0]
    (tmp_path / "empty.json").write_text("{}")
    assert "not a T3 review verdict" in pre.acceptance_problems(str(tmp_path / "empty.json"), core)[0]
    assert "not a T3 review verdict" in pre.acceptance_problems(str(tmp_path / "none.json"), core)[0]


def test_preflight_main_needs_acceptance_only_for_a_full_run(tmp_path, monkeypatch):
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps(_pin_set(TOK)))
    d = _snapshot(tmp_path, TOK)
    base = ["--wheel", _wheel(tmp_path), "--tokenizer-dir", str(d),
            "--tokenizer-revision", REV, "--pins", str(pins)]
    assert pre.main(base) == 0
    assert pre.main(base + ["--full"]) == 1
    assert pre.main(base + ["--full", "--acceptance",
                            _verdict(tmp_path, "admitted", ident.sha256_bytes(CORE))]) == 0
    assert pre.main(base + ["--acceptance", _verdict(tmp_path, "not_admitted",
                                                     ident.sha256_bytes(CORE), "n.json")]) == 1


def _run_full(tmp_path, **env_extra):
    """A full run_t8a.sh; PY_BASE is /bin/false, so a run past the
    preflight stops at the first venv without installing anything.
    T8A_MEM_LIMIT_MB=0: run_t8a.sh sets no new limit and the inherited one
    stays (release_prep.sh and isolated_tests.sh run pytest under a 16 GiB
    hard limit, which the 40 GB default cannot be raised above); the
    limit itself is tested through _run_t8a."""
    if not os.path.isfile(os.path.join(MB_CHECKOUT, "maskbench", "blg_engine.py")):
        import pytest
        pytest.skip("no MaskBench checkout")
    env = {k: v for k, v in os.environ.items() if k not in ("T8A_MEM_LIMIT_MB", "T8A_ACCEPTANCE")}
    env.update(PY_BASE="/bin/false", T8A_MEM_LIMIT_MB="0", **env_extra)
    r = subprocess.run(["bash", os.path.join(T8A, "run_t8a.sh"), str(tmp_path / "w"),
                        _wheel(tmp_path), "1"], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 1
    assert not (tmp_path / "w" / "venv-pinned").exists()
    return r


def test_full_run_without_admitted_verdict_stops_before_any_install(tmp_path):
    _snapshot(tmp_path, TOK)  # names of a snapshot, not the pinned bytes
    r = _run_full(tmp_path, HF_HOME=str(tmp_path / "hf"))
    assert "T8A_ABORT preflight" in r.stdout, r.stdout
    assert "needs T8A_ACCEPTANCE" in r.stdout
    assert "tokenizer snapshot: tokenizer.json is" in r.stdout
    assert "pinned venv" not in r.stdout


def test_full_run_with_the_admitted_verdict_passes_the_preflight(tmp_path):
    """On a machine with the pinned snapshot in HF_HOME (isolated_tests.sh
    exports the cache it copied; release_prep.sh passes the main tree's) or
    in the main tree's cache."""
    hf = os.environ.get("HF_HOME") or HF_MAIN
    if not os.path.isdir(os.path.join(hf, SNAP)):
        import pytest
        pytest.skip(f"no pinned tokenizer snapshot in {hf}")
    acc = _verdict(tmp_path, "admitted", ident.sha256_bytes(CORE))
    r = _run_full(tmp_path, HF_HOME=hf, T8A_ACCEPTANCE=acc)
    assert "t8a_preflight: ok" in r.stdout, r.stdout
    assert "T8A_ABORT pinned venv" in r.stdout


# --- the work queue of paired_driver.py ------------------------------------
# Smoke t8a-smoke-1: the loop of scripts/run_maskbench.py gave the 20 files
# still running in one worker to the other three, and the driver counted the
# copies as attempts. Full run t8a-6e62e0d1 (2026-10-10): two chunks ended
# at a timeout, and a file neither of them had reached stopped the run as
# "attempted 2 times"; handing a file to a chunk is not an attempt, and the
# queue counts none. paired_driver.py runs here on a stand-in MaskBench
# whose chunks log their start and end and, in mode "cut", every file they
# start; a file named *timeout* ends its chunk as MaskBench's SIGALRM does.

t8aq = _load("t8a_queue", os.path.join(T8A, "t8a_queue.py"))
UPSTREAM_SCRIPT = os.path.join(ROOT, "benchmarks", "external", "jsonschemabench-maskbench",
                               "maskbench", "scripts", "run_maskbench.py")

FAKE_RUNNER = '''
import argparse, json, os, signal, sys, time


def setup_argparse():
    p = argparse.ArgumentParser()
    for flag in ("--blg", "--llg", "--xgr", "--compact", "--multi"):
        p.add_argument(flag, action="store_true")
    p.add_argument("--tokenizer")
    p.add_argument("--output")
    p.add_argument("--num-threads", type=int, default=1)
    p.add_argument("--time-limit", type=int, default=900)
    p.add_argument("--mem-limit", type=int, default=40)
    p.add_argument("files", nargs="*")
    return p


class Engine:
    tokenizer_model_id = "stand-in"
    def init(self): pass
    def compile_grammar(self, schema): pass
    def reset(self): pass
    def compute_mask(self): pass
    def commit_token(self, t): return True
    def get_id(self): return "stand-in"
    def get_name(self): return "stand-in"
    def get_module(self): return "bolorgir"
    def get_version(self): return "0"


def get_engine(args): return Engine()
def get_output(args): return args.output
def get_files(args): return list(args.files)
def process_file(engine, file, compact=False): return None


def journal(event, files):
    with open(os.environ["FAKE_MB_JOURNAL"], "a") as j:
        j.write(json.dumps({"event": event, "pid": os.getpid(), "files": files}) + "\\n")


def cut(args):
    # As maskbench.runner: the pending marker when a file starts ("x": a
    # file with an output never runs again), the result over it; at a
    # timeout the default SIGALRM action ends the process, marker kept.
    for f in sorted(args.files):
        out = os.path.join(args.output, os.path.basename(f))
        with open(out, "x") as o:
            o.write(json.dumps({"pending_file": 1}, indent=2))
        journal("process", [f])
        if "timeout" in f:
            journal("end", args.files)
            os.kill(os.getpid(), signal.SIGALRM)
        with open(out, "w") as o:
            o.write("{}")
    journal("end", args.files)


def main():
    args = setup_argparse().parse_args()
    journal("start", args.files)
    time.sleep(float(os.environ["FAKE_MB_SLEEP"]))
    mode = os.environ["FAKE_MB_MODE"]
    if mode == "cut":
        return cut(args)
    for f in args.files:
        if mode == "ok" or (mode == "partial" and not f.endswith("bad.json")):
            open(os.path.join(args.output, os.path.basename(f)), "w").write("{}")
    journal("end", args.files)
    sys.exit(1 if mode == "fail" else 0)
'''

FAKE_TRANSFORMERS = '''
class _Tokenizer:
    def encode(self, text, add_special_tokens=True):
        return list(text.encode("utf-8"))


class AutoTokenizer:
    @staticmethod
    def from_pretrained(name, **kw):
        return _Tokenizer()
'''

# What the driver takes from scripts/run_maskbench.py when the MaskBench
# checkout is absent: run_cmd, cmd and output_path.
STANDIN_SCRIPT = '''
import os, subprocess
output_path = "tmp/output/"
cmd = []


def run_cmd(file_list):
    r = subprocess.run(cmd + file_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, check=False)
    with open(os.path.join(output_path, "log.txt"), "a") as log:
        log.write(f"{r.stderr}{r.stdout}\\nExit code: {r.returncode}\\n")
'''


def _standin_maskbench(tmp_path):
    mb = tmp_path / "mb"
    for d in ("maskbench", "scripts", "transformers"):
        (mb / d).mkdir(parents=True)
    (mb / "maskbench" / "__init__.py").write_text("")
    (mb / "maskbench" / "runner.py").write_text(FAKE_RUNNER)
    (mb / "transformers" / "__init__.py").write_text(FAKE_TRANSFORMERS)
    script = STANDIN_SCRIPT
    if os.path.exists(UPSTREAM_SCRIPT):
        with open(UPSTREAM_SCRIPT, encoding="utf-8") as f:
            script = f.read()
    (mb / "scripts" / "run_maskbench.py").write_text(script)
    return mb


def _drive(tmp_path, files, threads, mode, sleep):
    mb = _standin_maskbench(tmp_path)
    journal = tmp_path / "journal.jsonl"
    env = dict(os.environ, PYTHONPATH=f"{T8A}:{mb}", PYTHONDONTWRITEBYTECODE="1",
               T8A_SERIALIZER="bolorgir", FAKE_MB_MODE=mode, FAKE_MB_SLEEP=str(sleep),
               FAKE_MB_JOURNAL=str(journal))
    r = subprocess.run([sys.executable, "-u", os.path.join(T8A, "paired_driver.py"), "--blg",
                        "--compact", "--tokenizer", "stand-in", "--num-threads", str(threads),
                        "--output", str(tmp_path / "out"), *files],
                       cwd=mb, env=env, capture_output=True, text=True, timeout=120)
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    handed, overlaps, running, chunks, peak = {}, [], set(), set(), 0
    for e in events:
        if e["event"] == "start":
            overlaps += [f for f in e["files"] if f in running]
            running.update(e["files"])
            for f in e["files"]:
                handed[f] = handed.get(f, 0) + 1
            chunks.add(e["pid"])
            peak = max(peak, len(chunks))
        elif e["event"] == "end":
            running.difference_update(e["files"])
            chunks.discard(e["pid"])
    return r, handed, overlaps, peak


def _processed(tmp_path):
    """How often each file was started (mode "cut")."""
    n = {}
    for line in (tmp_path / "journal.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e["event"] == "process":
            n[e["files"][0]] = n.get(e["files"][0], 0) + 1
    return n


def _files(n):
    return [f"data/f{i:03}.json" for i in range(n)]


def test_smoke_files_with_four_workers_run_once(tmp_path):
    files = _files(20)
    r, handed, overlaps, _ = _drive(tmp_path, files, 4, "ok", 1.0)
    assert r.returncode == 0, r.stderr
    assert overlaps == [] and handed == {f: 1 for f in files}
    assert "Total files: 20 of 20" in r.stdout and "20/20 done" in r.stdout
    assert "T8A_RUN_STOPPED" not in r.stderr


def test_four_slow_workers_share_files_without_copies(tmp_path):
    files = _files(200)
    r, handed, overlaps, peak = _drive(tmp_path, files, 4, "ok", 1.0)
    assert r.returncode == 0, r.stderr
    assert overlaps == [] and handed == {f: 1 for f in files}
    assert peak == 4
    assert "200/200 done" in r.stdout


def test_chunk_without_output_stops_the_run(tmp_path):
    r, handed, overlaps, _ = _drive(tmp_path, _files(200), 4, "fail", 0.5)
    assert r.returncode == 4
    assert "T8A_RUN_STOPPED a chunk of" in r.stderr and "produced no output" in r.stderr
    assert overlaps == [] and set(handed.values()) == {1} and len(handed) < 200


def test_file_without_output_goes_back_and_a_chunk_without_output_stops_the_run(tmp_path):
    # The 20 files go to one chunk; bad.json, without an output, goes back
    # and alone in the next chunk gives no output.
    files = _files(19) + ["data/bad.json"]
    r, handed, overlaps, _ = _drive(tmp_path, files, 4, "partial", 0.5)
    assert r.returncode == 4
    assert "T8A_RUN_STOPPED a chunk of 1 files produced no output" in r.stderr
    assert "attempted" not in r.stderr
    assert overlaps == []
    assert handed == {**{f: 1 for f in files}, "data/bad.json": 2}


def test_files_two_timeouts_cut_off_go_back_and_run_once(tmp_path):
    # Full run t8a-6e62e0d1: chunk 1 ends at b-timeout, chunk 2 at
    # c-timeout; the f files are in both without being started, then run
    # once in chunk 3. The markers of the timeouts stay and never run again.
    files = ["data/a.json", "data/b-timeout.json", "data/c-timeout.json"] + _files(10)
    r, handed, overlaps, _ = _drive(tmp_path, files, 1, "cut", 0.1)
    assert r.returncode == 0, r.stderr
    assert "T8A_RUN_STOPPED" not in r.stderr and "13/13 done" in r.stdout
    assert [line.split("; ")[0].split(" ", 1)[1] for line in r.stdout.splitlines()
            if " + " in line] == ["2 + 11", "1 + 10", "10 + 0"]
    assert _processed(tmp_path) == {f: 1 for f in files}
    assert handed == {"data/a.json": 1, "data/b-timeout.json": 1, "data/c-timeout.json": 2,
                      **{f: 3 for f in _files(10)}}
    assert overlaps == []
    for t in ("b-timeout.json", "c-timeout.json"):
        assert json.loads((tmp_path / "out" / t).read_text()) == {"pending_file": 1}
    log = (tmp_path / "out" / "log.txt").read_text()
    assert log.count("Exit code: -14") == 2 and log.count("Exit code: 0") == 1


def test_four_workers_with_timeouts_start_every_file_once(tmp_path):
    files = sorted(_files(196) + [f"data/f{i:03}-timeout.json" for i in (10, 60, 110, 160)])
    r, handed, overlaps, _ = _drive(tmp_path, files, 4, "cut", 0.3)
    assert r.returncode == 0, r.stderr
    assert "200/200 done" in r.stdout and "T8A_RUN_STOPPED" not in r.stderr
    assert overlaps == []
    assert _processed(tmp_path) == {f: 1 for f in files}
    for f in files:
        if "timeout" in f:
            assert json.loads((tmp_path / "out" / os.path.basename(f)).read_text()) \
                == {"pending_file": 1}


def _cut_runner(outputs, started, cut_at):
    """An in-memory chunk runner: starts the files of a chunk in order,
    each with an output, and ends the chunk at a file of cut_at."""
    def run(chunk):
        for f in chunk:
            assert f not in outputs, f"{f} runs again"
            outputs.add(f)
            started.append(f)
            if f in cut_at:
                return
    return run


def test_queue_hands_an_unreached_file_on_without_counting_it():
    # The same unreached file u in two chunks cut by timeouts, then started
    # once; no attempt limit stops the run.
    outputs, started = set(), []
    q = t8aq.FileQueue(["t1", "t2", "u", "v"], outputs.__contains__, shuffle=lambda l: None)
    failure = t8aq.run_queue(q, _cut_runner(outputs, started, {"t1", "t2"}), 1,
                             log=lambda *_: None)
    assert failure is None
    assert started == ["t1", "t2", "u", "v"] and q.finished == 4
    assert not q.pending and not q.running and not hasattr(q, "attempts")


def test_queue_stops_when_a_chunk_gives_no_file_an_output():
    outputs, chunks = set(), []

    def runner(chunk):
        chunks.append(list(chunk))
        if len(chunks) == 1:
            outputs.add(chunk[0])

    q = t8aq.FileQueue(["a", "b", "c"], outputs.__contains__, shuffle=lambda l: None)
    failure = t8aq.run_queue(q, runner, 1, log=lambda *_: None)
    assert failure == "a chunk of 2 files produced no output"
    assert chunks == [["a", "b", "c"], ["b", "c"]] and q.finished == 1


def test_queue_skips_files_with_output_and_stops_when_the_runner_raises():
    q = t8aq.FileQueue(["a", "b", "c", "d"], {"a", "b"}.__contains__)
    assert sorted(q.pending) == ["c", "d"] and q.finished == 2
    chunks = []

    def runner(chunk):
        chunks.append(sorted(chunk))
        raise OSError("no such file")

    failure = t8aq.run_queue(q, runner, 4, log=lambda *_: None)
    assert failure == "the chunk runner failed: OSError: no such file"
    assert chunks == [["c", "d"]] and not q.running and not q.pending


# --- compact XGrammar at the MaskBench pin ---------------------------------
# Smoke t8a-smoke-2: the preflight of xgr-pinned failed with "module
# 'xgrammar' has no attribute '__version__'" (xgrammar 0.1.17).

def test_compact_xgrammar_needs_no_version_attribute(tmp_path):
    mb = _standin_maskbench(tmp_path)
    (mb / "xgrammar").mkdir()
    (mb / "xgrammar" / "__init__.py").write_text("")  # like 0.1.17: no __version__
    code = """
import json, types
import paired_runner
from maskbench import runner

class Compiler:
    calls = []
    def compile_json_schema(self, schema, **kw):
        self.calls.append([schema, kw])
        return "grammar"

class XgrEngine(runner.Engine):
    def get_module(self):
        return "xgrammar"

base = XgrEngine()
base.xgr_compiler = Compiler()
paired_runner._get_engine = lambda args: base
engine = paired_runner.get_engine(types.SimpleNamespace(compact=True))
engine.compile_grammar({"type": "integer"})
print(json.dumps(Compiler.calls))
"""
    env = dict(os.environ, PYTHONPATH=f"{T8A}:{mb}", PYTHONDONTWRITEBYTECODE="1")
    env.pop("T8A_SERIALIZER", None)
    r = subprocess.run([sys.executable, "-c", code], cwd=mb, env=env, capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [['{"type": "integer"}', {
        "any_whitespace": False, "separators": [",", ":"], "strict_mode": True}]]


# --- abort-phase recording in the T8a runner -------------------------------

ABORT_RUNNER = '''
import json, os

output_path = None

def process_file(engine, file, compact=False):
    name = os.path.basename(file)
    out = os.path.join(output_path, name)
    if getattr(engine, "multi", False):
        with open(out, "w") as f:
            f.write(json.dumps({"pending_file": 1}))
    engine.compile_grammar({})
    engine.reset()
    for tid in engine.ids:
        engine.compute_mask()
        engine.commit_token(tid)
    with open(out, "w") as f:
        f.write(json.dumps({"num_tests": 1}))

def get_engine(args):
    raise SystemExit("unused")
'''

ABORT_SCRIPT = '''
import json, os, sys
import paired_runner
from maskbench import runner

class Engine:
    multi = True
    tokenizer = object()

    def __init__(self, phase):
        self.phase = phase
        self.ids = [7]
        self.t8a_tests = []

    def compile_grammar(self, schema):
        pass

    def reset(self):
        if self.phase == "reset":
            raise RuntimeError("boom-reset")

    def compute_mask(self):
        if self.phase == "compute_mask":
            raise RuntimeError("boom-mask")

    def commit_token(self, tid):
        if self.phase == "commit_token":
            raise RuntimeError("boom-commit")
        return True

out_dir, in_dir = sys.argv[1], sys.argv[2]
runner.output_path = out_dir
file = os.path.join(in_dir, "x.json")
result = {}
for phase in ("reset", "compute_mask", "commit_token"):
    engine = Engine(phase)
    paired_runner._install_recorders(engine)
    try:
        paired_runner.process_file(engine, file, compact=True)
    except RuntimeError:
        pass
    with open(os.path.join(out_dir, "x.json"), encoding="utf-8") as f:
        result[phase] = {"marker": json.load(f), "tests": engine.t8a_tests}
print(json.dumps(result))
'''

def test_abort_marker_names_the_phase_and_keeps_the_pending_status(tmp_path):
    """reset, compute_mask and commit_token failures leave a named cause.

    The result file stays a pending marker (status_of keeps classifying the
    file as crash_or_timeout), but it carries `aborted` with the phase, the
    instance and the token index; the instance record carries the same
    marker. This is the shape that lets the next run's diagnosis interpret a
    truncated instance instead of calling it corrupt.
    """
    mb = tmp_path / "mb"
    (mb / "maskbench").mkdir(parents=True)
    (mb / "maskbench" / "__init__.py").write_text("")
    (mb / "maskbench" / "runner.py").write_text(ABORT_RUNNER)
    out_dir = tmp_path / "out"
    in_dir = tmp_path / "in"
    out_dir.mkdir()
    in_dir.mkdir()
    (in_dir / "x.json").write_text('{"schema": {}, "tests": []}')
    script = tmp_path / "abort.py"
    script.write_text(ABORT_SCRIPT)
    env = dict(os.environ, PYTHONPATH=f"{T8A}:{mb}", PYTHONDONTWRITEBYTECODE="1")
    env.pop("T8A_SERIALIZER", None)
    r = subprocess.run([sys.executable, str(script), str(out_dir), str(in_dir)],
                       cwd=mb, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    for phase in ("reset", "compute_mask", "commit_token"):
        marker = got[phase]["marker"]
        assert marker["pending_file"] == 1
        assert marker["aborted"] == {"phase": phase, "at": 0,
                                     "error": "RuntimeError", "instance": 0}
        assert got[phase]["tests"][0]["aborted"] == marker["aborted"]
