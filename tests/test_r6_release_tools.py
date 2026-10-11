"""The release procedure after an R6 run (benchmarks/t3/r6_*.sh,
r6_register_line_oracle.py, r6_compare_sets.py) and the release-line
tools (.github/scripts/compose_release.py, build_twice.sh), on stand-in
runs, sets and repositories: nothing here runs the oracle, the triage or
a build, and nothing is written into this tree."""

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T3 = os.path.join(ROOT, "benchmarks", "t3")
SCRIPTS = os.path.join(ROOT, ".github", "scripts")
DIALECTS = ("draft4", "draft6", "draft7", "draft2019-09", "draft2020-12")
LIB_BYTES = b"candidate core bytes"
LIB = hashlib.sha256(LIB_BYTES).hexdigest()
LINE_SHA = "8" * 64

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("git") is None,
                                reason="bash and git needed")


def _env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("R6_", "BLG_", "STUB_")) and k not in ("PY", "ZIG")}
    env.update(PYTHONDONTWRITEBYTECODE="1", **extra)
    return env


def _run(argv, cwd=None, **env):
    return subprocess.run(argv, cwd=cwd, env=_env(**env), capture_output=True, text=True,
                          timeout=300)


def _git_repo(path, files, ignore=()):
    """A repository at path with the given {relative path: bytes|str},
    committed, and a .gitignore."""
    os.makedirs(path, exist_ok=True)
    for rel, data in files.items():
        p = os.path.join(path, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data.encode() if isinstance(data, str) else data)
        if rel.endswith((".sh", ".py")):
            os.chmod(p, 0o755)
    with open(os.path.join(path, ".gitignore"), "w") as f:
        f.write("".join(f"{i}\n" for i in ignore))
    git = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
    return subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


def _src(*rel):
    with open(os.path.join(ROOT, *rel), "rb") as f:
        return f.read()


# --- r6_register_line_oracle.py ---------------------------------------------

REG = os.path.join(T3, "r6_register_line_oracle.py")
PREFIX = "oracle-line-dbc5eeb-88888888"


def _line_doc():
    return {"about": "x", "lines": [
        {"name": "7345490f", "library_sha256": "7" * 64, "reports": {"draft4": {"path": "p"}}},
        {"name": "dbc5eeb", "library_sha256": LINE_SHA, "reports": None,
         "missing_reason": "no oracle report", "owner_decision": None}]}


def _report(d, lib, kind="good"):
    """A run_oracle.py report of one dialect: PASS, ENGINE_ERROR and
    SKIPPED rows with the summary run_oracle.py writes. kind "call refused"
    is the first dbc5eeb run (every compile refused by the call itself),
    "empty" has no rows, "forged" a summary that does not count its rows,
    "one call refusal" one such refusal among rows that ran."""
    rows = []
    for i, oc in enumerate([] if kind == "empty" else ["PASS"] * 4 + ["ENGINE_ERROR", "SKIPPED"]):
        refused = kind == "call refused" or (kind == "one call refusal" and i == 2)
        row = {"draft": d, "suite_file": "f.json", "case": "c", "test_index": i}
        if refused or oc == "SKIPPED":
            row.update(outcome="SKIPPED", schema_compiled=False, engine=None, refusal={
                "status": "INVALID_ARGUMENT" if refused else "UNSUPPORTED_FEATURE",
                "message": "bad compile request struct_size" if refused else "not supported"})
        else:
            row.update(outcome=oc, schema_compiled=True,
                       engine="accept" if oc == "PASS" else "error:RESOURCE_LIMIT")
        rows.append(row)
    by, st, f = {}, {}, {}
    keys = {"PASS": "pass", "ENGINE_ERROR": "engine_error", "SKIPPED": "refused"}
    for r in rows:
        by[r["outcome"]] = by.get(r["outcome"], 0) + 1
        f[keys[r["outcome"]]] = f.get(keys[r["outcome"]], 0) + 1
        if r["schema_compiled"]:
            f["compiled"] = f.get("compiled", 0) + 1
        else:
            st[r["refusal"]["status"]] = st.get(r["refusal"]["status"], 0) + 1
    summary = {"rows": len(rows) + (2 if kind == "forged" else 0), "by_outcome": by,
               "by_refusal_status": st, "by_file": {"f.json": f} if rows else {},
               "by_draft": {d: {"rows": len(rows), "by_outcome": dict(by)}}}
    return {"header": {"library_sha256": lib, "profile": "spec-v1", "drafts": [d]},
            "summary": summary, "rows": rows}


def _write_reports(src, prefix, lib, kinds=None, marker=None):
    for d in DIALECTS:
        p = src / "tests/oracle/results" / f"{prefix}-{d}-spec.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = _report(d, lib, (kinds or {}).get(d, "good"))
        if marker:
            doc["header"]["date_utc"] = marker
        p.write_text(json.dumps(doc))


def _reg_setup(tmp_path, lib=LINE_SHA, kinds=None):
    src = tmp_path / "src"
    _write_reports(src, PREFIX, lib, kinds)
    trees = []
    for name in ("tree one", "tree two"):
        t = tmp_path / name
        (t / "benchmarks/t3").mkdir(parents=True)
        (t / "benchmarks/t3/oracle_lines.json").write_text(json.dumps(_line_doc(), indent=1))
        trees.append(t)
    return src, trees


def _reg(src, trees):
    return _run([sys.executable, REG, "--line", "dbc5eeb", "--library-sha256", LINE_SHA,
                 "--source", str(src), "--prefix", PREFIX, "--identity", "how they were made",
                 *map(str, trees)])


def _snapshot(trees):
    out = {}
    for t in trees:
        for d, _, names in os.walk(t):
            for n in names:
                p = os.path.join(d, n)
                out[p] = open(p, "rb").read()
    return out


def test_register_writes_every_tree(tmp_path):
    src, trees = _reg_setup(tmp_path)
    r = _reg(src, trees)
    assert r.returncode == 0, r.stderr
    for t in trees:
        doc = json.load(open(t / "benchmarks/t3/oracle_lines.json"))
        line = doc["lines"][1]
        assert "missing_reason" not in line and line["identity"] == "how they were made"
        assert line["owner_decision"] is None and sorted(line["reports"]) == sorted(DIALECTS)
        for d, rep in line["reports"].items():
            data = open(t / rep["path"], "rb").read()
            assert hashlib.sha256(data).hexdigest() == rep["sha256"]
        assert doc["lines"][0] == _line_doc()["lines"][0]
    again = _reg(src, trees)
    assert again.returncode == 2 and "already lists reports" in again.stderr


@pytest.mark.parametrize("case", ["conflict in the second tree", "second tree registered",
                                  "another library", "no line list", "wrong dialect"])
def test_register_refuses_before_any_write(tmp_path, case):
    src, trees = _reg_setup(tmp_path, lib="9" * 64 if case == "another library" else LINE_SHA)
    if case == "conflict in the second tree":
        p = trees[1] / "tests/oracle/results" / f"{PREFIX}-draft7-spec.json"
        p.parent.mkdir(parents=True)
        p.write_text("other bytes")
    elif case == "second tree registered":
        doc = _line_doc()
        doc["lines"][1]["reports"] = {"draft4": {"path": "x"}}
        (trees[1] / "benchmarks/t3/oracle_lines.json").write_text(json.dumps(doc))
    elif case == "no line list":
        (trees[1] / "benchmarks/t3/oracle_lines.json").unlink()
    elif case == "wrong dialect":
        p = src / "tests/oracle/results" / f"{PREFIX}-draft6-spec.json"
        p.write_text(json.dumps({"header": {"library_sha256": LINE_SHA, "profile": "spec-v1",
                                            "drafts": ["draft7"]}}))
    before = _snapshot(trees)
    r = _reg(src, trees)
    assert r.returncode == 2 and "refused, nothing written" in r.stderr, (r.returncode, r.stderr)
    assert _snapshot(trees) == before


@pytest.mark.parametrize("kinds, want", [
    (dict.fromkeys(DIALECTS, "call refused"), "compile refusals of the call itself"),
    (dict.fromkeys(DIALECTS, "empty"), "no rows"),
    ({"draft7": "empty"}, "draft7-spec.json is no evidence about the library: no rows"),
    ({"draft6": "forged"}, "the summary does not count the rows: rows 8 for 6"),
    ({"draft2019-09": "one call refusal"}, "1 compile refusals of the call itself"),
])
def test_register_refuses_a_report_that_is_no_evidence(tmp_path, kinds, want):
    src, trees = _reg_setup(tmp_path, kinds=kinds)
    before = _snapshot(trees)
    r = _reg(src, trees)
    assert r.returncode == 2 and "refused, nothing written" in r.stderr, r.stderr
    assert want in r.stderr, r.stderr
    assert _snapshot(trees) == before


NEW_PREFIX = f"{PREFIX}-r2"


def _old_registration(tmp_path):
    """Both trees list reports of the first dbc5eeb run (registered before
    the content check existed): every compile refused by the call itself.
    The second run's reports are in the source under NEW_PREFIX."""
    src, trees = _reg_setup(tmp_path)
    old = tmp_path / "old run"
    _write_reports(old, PREFIX, LINE_SHA, dict.fromkeys(DIALECTS, "call refused"))
    reports = {}
    for d in DIALECTS:
        rel = f"tests/oracle/results/{PREFIX}-{d}-spec.json"
        data = (old / rel).read_bytes()
        reports[d] = {"path": rel, "sha256": hashlib.sha256(data).hexdigest()}
        for t in trees:
            (t / rel).parent.mkdir(parents=True, exist_ok=True)
            (t / rel).write_bytes(data)
    for t in trees:
        doc = _line_doc()
        line = doc["lines"][1]
        del line["missing_reason"]
        line.update(identity="first run", reports=reports)
        (t / "benchmarks/t3/oracle_lines.json").write_text(json.dumps(doc, indent=1))
    _write_reports(src, NEW_PREFIX, LINE_SHA, marker="second run")
    return src, trees, {d: r["sha256"] for d, r in reports.items()}


def _replace(src, trees, expected, prefix=NEW_PREFIX, reason="the first run was no evidence"):
    argv = [sys.executable, REG, "--line", "dbc5eeb", "--library-sha256", LINE_SHA,
            "--source", str(src), "--prefix", prefix, "--identity", "second run"]
    for d, h in expected.items():
        argv += ["--replace", f"{d}={h}"]
    if reason is not None:
        argv += ["--reason", reason]
    return _run(argv + [str(t) for t in trees])


def test_replace_keeps_the_old_reports_in_the_history(tmp_path):
    src, trees, old = _old_registration(tmp_path)
    before = _snapshot(trees)
    r = _reg(src, trees)  # a first registration never replaces
    assert r.returncode == 2 and "already lists reports" in r.stderr
    r = _replace(src, trees, old)
    assert r.returncode == 0, r.stderr
    for t in trees:
        doc = json.load(open(t / "benchmarks/t3/oracle_lines.json"))
        line = doc["lines"][1]
        assert line["identity"] == "second run" and line["owner_decision"] is None
        assert {d: rep["path"] for d, rep in line["reports"].items()} == {
            d: f"tests/oracle/results/{NEW_PREFIX}-{d}-spec.json" for d in DIALECTS}
        for rep in line["reports"].values():
            assert hashlib.sha256((t / rep["path"]).read_bytes()).hexdigest() == rep["sha256"]
        (h,) = line["history"]
        assert h["reason"] == "the first run was no evidence" and h["identity"] == "first run"
        assert {d: rep["sha256"] for d, rep in h["reports"].items()} == old and h["replaced_utc"]
        for rep in h["reports"].values():  # the old files stay
            assert (t / rep["path"]).read_bytes() == before[str(t / rep["path"])]
        assert doc["lines"][0] == _line_doc()["lines"][0]
    again = _replace(src, trees, old)
    assert again.returncode == 2 and "not the expected" in again.stderr


def _second_tree_lists_other(trees, old):
    p = trees[1] / "benchmarks/t3/oracle_lines.json"
    doc = json.load(open(p))
    doc["lines"][1]["reports"]["draft7"]["sha256"] = "1" * 64
    p.write_text(json.dumps(doc))


@pytest.mark.parametrize("case, want", [
    ("wrong expected hash", "not the expected"),
    ("old report changed in the second tree", "not the registered bytes"),
    ("second tree lists other reports", "not the expected"),
    ("four dialects", "all five dialects are replaced together"),
    ("no reason", "--replace needs --reason"),
    ("new report at the old path", "the new report has the old one's path"),
    ("new report is no evidence", "is no evidence about the library"),
])
def test_replace_refuses_before_any_write(tmp_path, case, want):
    src, trees, old = _old_registration(tmp_path)
    expected, prefix, reason = dict(old), NEW_PREFIX, "the first run was no evidence"
    if case == "wrong expected hash":
        expected["draft6"] = "0" * 64
    elif case == "old report changed in the second tree":
        (trees[1] / f"tests/oracle/results/{PREFIX}-draft2020-12-spec.json").write_text("{}")
    elif case == "second tree lists other reports":
        _second_tree_lists_other(trees, old)
    elif case == "four dialects":
        del expected["draft7"]
    elif case == "no reason":
        reason = None
    elif case == "new report at the old path":
        prefix = PREFIX
        _write_reports(src, PREFIX, LINE_SHA, marker="second run")
    elif case == "new report is no evidence":
        _write_reports(src, NEW_PREFIX, LINE_SHA, {"draft4": "call refused"})
    before = _snapshot(trees)
    r = _replace(src, trees, expected, prefix=prefix, reason=reason)
    assert r.returncode == 2 and "refused, nothing written" in r.stderr, (case, r.stderr)
    assert want in r.stderr, (case, r.stderr)
    assert _snapshot(trees) == before, case


# --- r6_compare_sets.py ------------------------------------------------------

CMP = os.path.join(T3, "r6_compare_sets.py")
SET = "benchmarks/coverage/spec-v1/t3-cccccccc"


def _make_set(root, rel=SET):
    """A published set root/rel whose files name root (raw and JSON
    escaped), as the two publications from two trees do."""
    s = os.path.join(root, rel)
    os.makedirs(os.path.join(s, "review", "evidence", "oracle"))
    acc = {"acceptance": {"verdict": "not_admitted", "exit_status": 1, "pending": [],
                          "failing": ["transitions_described", "transitions_approved",
                                      "d4_candidate"]},
           "checks": [{"check": "triage", "status": "ok", "detail": "x"}],
           "transitions": [{"corpus": "repo", "file": f"o{i}.json", "tokenizer": "byte",
                            "candidate": "engine_unavailable", "d4": "D4-1",
                            "approval": "pending"} for i in range(3)],
           "d4_pending_rows": [{"d4": "D4-1", "row": {"file": "o0.json", "outcome": "x"}}],
           "triage_rows": [], "totals": {"repo": {"generatable": 10}},
           "inputs": {"d4": {"path": f"{root}/benchmarks/t3/d4_proposals.json",
                             "sha256": "a" * 64}}}
    with open(os.path.join(s, "review", "acceptance.json"), "w") as f:
        json.dump(acc, f)  # ensure_ascii: a non-ASCII root is \\u-escaped
    tri = {"summary": {"generatable": {"D": 1}}, "exit_status": 0,
           "generatable_rows": [{"file": "o5.json", "class": "D"}], "transition_rows": []}
    with open(os.path.join(s, "review", "triage.json"), "w") as f:
        json.dump(tri, f)
    with open(os.path.join(s, "review", "acceptance.md"), "w", encoding="utf-8") as f:
        f.write(f"# verdict\n\ninputs under {root}\n")
    with open(os.path.join(s, "review", "evidence", "oracle", "x-spec.json.gz"), "wb") as f:
        f.write(gzip.compress(json.dumps({"summary": {"PASS": 5}}).encode()))
    with open(os.path.join(s, "SHA256SUMS"), "w") as f:
        f.write(f"{root} differs anyway\n")
    return s


def _pair(tmp_path):
    ra, rb = str(tmp_path / "Рабочий стол A"), str(tmp_path / "tree B")
    return _make_set(ra), _make_set(rb), ra, rb


def _cmp(a, b, ra, rb):
    return _run([sys.executable, CMP, a, b, "--a-root", ra, "--b-root", rb])


def _edit_json(path, fn):
    with open(path) as f:
        doc = json.load(f)
    fn(doc)
    with open(path, "w") as f:
        json.dump(doc, f)


def test_compare_sets_equal_up_to_the_roots(tmp_path):
    a, b, ra, rb = _pair(tmp_path)
    assert open(os.path.join(a, "review/acceptance.json")).read() != \
        open(os.path.join(b, "review/acceptance.json")).read()
    r = _cmp(a, b, ra, rb)
    assert r.returncode == 0, r.stderr
    assert "transitions (3) equal" in r.stdout and "same content" in r.stdout


@pytest.mark.parametrize("name, mutate, want", [
    ("schema", lambda d: d["transitions"][0].update(file="o99.json"), "transitions[0].file"),
    ("outcome", lambda d: d["transitions"][1].update(candidate="incomplete"),
     "transitions[1].candidate"),
    ("D4 binding", lambda d: d["transitions"][2].update(d4="D4-2"), "transitions[2].d4"),
    ("order", lambda d: d["transitions"].reverse(), "transitions[0].file"),
    ("D4 row", lambda d: d["d4_pending_rows"][0]["row"].update(outcome="y"),
     "d4_pending_rows[0].row.outcome"),
    ("total", lambda d: d["totals"]["repo"].update(generatable=11), "totals.repo.generatable"),
    ("input hash", lambda d: d["inputs"]["d4"].update(sha256="b" * 64), "inputs.d4.sha256"),
    ("the other tree's path", lambda d: d["inputs"]["d4"].update(
        path=d["inputs"]["d4"]["path"].replace("tree B", "Рабочий стол A")), "inputs.d4.path"),
])
def test_compare_sets_catches_a_changed_review(tmp_path, name, mutate, want):
    """The same number of transitions, one thing changed: a difference."""
    a, b, ra, rb = _pair(tmp_path)
    _edit_json(os.path.join(b, "review/acceptance.json"), mutate)
    r = _cmp(a, b, ra, rb)
    assert r.returncode == 1, (name, r.stdout)
    assert want in r.stderr, (name, r.stderr)


def test_compare_sets_catches_t2c_evidence_and_missing_files(tmp_path):
    a, b, ra, rb = _pair(tmp_path)
    _edit_json(os.path.join(b, "review/triage.json"),
               lambda d: d["generatable_rows"][0].update({"class": "A"}))
    r = _cmp(a, b, ra, rb)
    assert r.returncode == 1 and "generatable_rows[0].class" in r.stderr
    a, b, ra, rb = _pair(tmp_path / "2")
    with open(os.path.join(b, "review/evidence/oracle/x-spec.json.gz"), "wb") as f:
        f.write(gzip.compress(json.dumps({"summary": {"PASS": 4}}).encode()))
    r = _cmp(a, b, ra, rb)
    assert r.returncode == 1 and "summary.PASS" in r.stderr
    os.remove(os.path.join(b, "review/acceptance.md"))
    r = _cmp(a, b, ra, rb)
    assert "review/acceptance.md: only in A" in r.stderr
    assert _cmp(a, str(tmp_path / "none"), ra, rb).returncode == 2


# --- r6_publish.sh and r6_publish_layout.sh on a stand-in run ----------------

LAYOUT = os.path.join(T3, "r6_publish_layout.sh")
STUB_REVIEW = """#!/bin/bash
# stub of r6_review.sh: the review files and their SHA256SUMS
out="$3"; mkdir -p "$out/evidence/oracle"
[ -f "$R6_LIB" ] || { echo "no R6_LIB" >&2; exit 2; }
echo '{"acceptance": {"verdict": "not_admitted"}}' > "$out/acceptance.json"
echo "# stub" > "$out/acceptance.md"; echo '{}' > "$out/triage.json"; echo "# t" > "$out/triage.md"
echo '{"git_head": null}' > "$out/tools.json"
(cd "$out" && find . -type f ! -name SHA256SUMS -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) > "$out/SHA256SUMS"
exit 1
"""


def _stand_in_run(path, rev):
    run = path
    for d in ("out", "identity/files", "lib"):
        os.makedirs(os.path.join(run, d), exist_ok=True)
    for s in ("repo", "maskbench"):
        with open(os.path.join(run, "out", f"coverage_{s}.json"), "w") as f:
            json.dump({"meta": {"engine": {"library_sha256": LIB}}, "snapshot": s}, f)
        with open(os.path.join(run, "out", f"coverage_{s}.md"), "w") as f:
            f.write(f"# {s}\n")
        with open(os.path.join(run, "out", f"coverage_{s}.passes.json.gz"), "wb") as f:
            f.write(gzip.compress(f'[{{"pass": "{s}"}}]'.encode(), mtime=0))
    files = {"identity/manifest.json": json.dumps({"git_head": rev}),
             "identity/git_head": rev + "\n", "identity/lib.sha256": f"{LIB}  lib/x\n",
             "identity/files/tree.list": "tree/a 1\n", "identity/files/venv.list": "venv/x 2\n",
             "identity/.complete": "done\n",
             "run.log": "RUN_INIT x\nnoise\nRUN_PREPARED t\nRUN_READY t\nRUN_START t\nRUN_DONE exit=0 t\n"}
    for rel, text in files.items():
        with open(os.path.join(run, rel), "w") as f:
            f.write(text)
    with open(os.path.join(run, "lib", "libbolorgir.so.0.1.0"), "wb") as f:
        f.write(LIB_BYTES)
    return run


def _published(tmp_path):
    """r6_publish.sh of this tree, in a stand-in tree with the stub review."""
    tree = tmp_path / "tree copy"
    (tree / "benchmarks/t3").mkdir(parents=True)
    shutil.copy(os.path.join(T3, "r6_publish.sh"), tree / "benchmarks/t3/r6_publish.sh")
    (tree / "benchmarks/t3/r6_review.sh").write_text(STUB_REVIEW)
    rev = "f" * 40
    run = _stand_in_run(str(tmp_path / "run dir"), rev)
    oracle = []
    for d in DIALECTS:
        p = tmp_path / f"oracle-{d}.json"
        p.write_text("{}")
        oracle.append(str(p))
    r = _run(["bash", str(tree / "benchmarks/t3/r6_publish.sh"), run, "t3-stand-in", *oracle],
             PY=sys.executable)
    assert r.returncode == 1 and "R6_PUBLISH_DONE exit=1" in r.stdout, (r.stdout, r.stderr)
    return run, str(tree / "benchmarks/coverage/spec-v1/t3-stand-in"), rev


def _sums(d):
    lines = []
    for p in sorted(os.path.relpath(os.path.join(x, n), d).encode()
                    for x, _, ns in os.walk(d) for n in ns if n != "SHA256SUMS"):
        p = p.decode()
        lines.append(f"{hashlib.sha256(open(os.path.join(d, p), 'rb').read()).hexdigest()}  ./{p}\n")
    with open(os.path.join(d, "SHA256SUMS"), "w") as f:
        f.writelines(lines)


def _layout(run, o, rev):
    return _run(["bash", LAYOUT, run, o, rev])


def test_layout_of_a_publication(tmp_path):
    run, o, rev = _published(tmp_path)
    r = _layout(run, o, rev)
    assert r.returncode == 0, r.stdout
    # a later review directory with its own list, the outer list recomputed
    os.makedirs(os.path.join(o, "review-2"))
    with open(os.path.join(o, "review-2", "acceptance.json"), "w") as f:
        f.write("{}")
    _sums(os.path.join(o, "review-2"))
    _sums(o)
    assert _layout(run, o, rev).returncode == 0
    with open(os.path.join(o, "review-2", "acceptance.json"), "w") as f:
        f.write('{"changed": 1}')
    _sums(o)  # the outer list recomputed, the review-2 list stale
    r = _layout(run, o, rev)
    assert r.returncode == 4 and "review-2/SHA256SUMS is not the complete list" in r.stdout


@pytest.mark.parametrize("name, mutate, want", [
    ("sidecar", lambda o: open(os.path.join(o, "coverage_repo.passes.json.gz"), "wb").write(
        gzip.compress(b"[]")), "coverage_repo.passes.json.gz differs"),
    ("report", lambda o: open(os.path.join(o, "coverage_maskbench.json.gz"), "wb").write(
        gzip.compress(b"{}")), "coverage_maskbench.json.gz is not the run's report"),
    ("lib.sha256", lambda o: open(os.path.join(o, "identity/lib.sha256"), "w").write("0  x\n"),
     "identity/lib.sha256 differs"),
    ("events", lambda o: open(os.path.join(o, "identity/run_events.txt"), "w").write(
        "RUN_DONE exit=0 t\n"), "run_events.txt differs"),
    ("list", lambda o: os.remove(os.path.join(o, "identity/files/venv.list.gz")),
     "published lists"),
])
def test_layout_catches_a_changed_publication(tmp_path, name, mutate, want):
    run, o, rev = _published(tmp_path)
    mutate(o)
    _sums(os.path.join(o, "review"))
    _sums(o)
    r = _layout(run, o, rev)
    assert r.returncode == 4 and want in r.stdout, (name, r.stdout)


def test_layout_catches_a_list_with_a_duplicate_entry(tmp_path):
    """The line count stays and sha256sum -c passes on a repeated entry."""
    run, o, rev = _published(tmp_path)
    for d in (o, os.path.join(o, "review")):
        with open(os.path.join(d, "SHA256SUMS")) as f:
            lines = f.readlines()
        with open(os.path.join(d, "SHA256SUMS"), "w") as f:
            f.writelines([lines[0]] + lines[:-1])
    r = _layout(run, o, rev)
    assert r.returncode == 4
    assert "LAYOUT: SHA256SUMS is not the complete list" in r.stdout
    assert "LAYOUT: review/SHA256SUMS is not the complete list" in r.stdout
    assert _layout(run, o, "0" * 40).returncode == 4


# --- r6_after_run.sh on stand-in steps -----------------------------------------

STUBS = {
    "r6_oracle.sh": 'exit "${STUB_ORACLE_RC:-0}"\n',
    "r6_register_line_oracle.py": "import os, sys\nsys.exit(int(os.environ.get('STUB_REGISTER_RC', '0')))\n",
    "r6_publish_check.sh": 'mkdir -p "$3/tree/$(dirname "$STUB_SET")"\n'
                           'cp -a "$STUB_A" "$3/tree/$STUB_SET"\nexit "${STUB_CHECK_RC:-1}"\n',
    "r6_publish_release.sh": 'd="$STUB_ROOT/$STUB_SET"; mkdir -p "$(dirname "$d")"; rm -rf "$d"\n'
                             'cp -a "$STUB_B" "$d"\n'
                             '[ -n "${STUB_MUTATE:-}" ] && (cd "$d" && python3 -c "$STUB_MUTATE")\n'
                             'exit "${STUB_REAL_RC:-1}"\n',
}


def _chain_setup(tmp_path):
    root = tmp_path / "release tree"
    _git_repo(root, {"benchmarks/t3/r6_after_run.sh": _src("benchmarks", "t3", "r6_after_run.sh"),
                     "benchmarks/t3/r6_compare_sets.py": _src("benchmarks", "t3",
                                                               "r6_compare_sets.py")},
              ignore=("benchmarks/coverage/",))
    tools = tmp_path / "tools"
    tools.mkdir()
    for n, text in STUBS.items():
        (tools / n).write_text(text)
    run = _stand_in_run(str(tmp_path / "run"), "f" * 40)
    work = tmp_path / "work"
    label_set = f"benchmarks/coverage/spec-v1/t3-{LIB[:8]}"
    pristine = {}
    for side, r in (("A", str(work / "tree")), ("B", str(root))):
        # made under the root it will be published in, then kept aside:
        # the stand-in steps copy it into place
        pristine[side] = str(tmp_path / f"set {side}")
        shutil.move(_make_set(r, label_set), pristine[side])
    shutil.rmtree(work)
    env = dict(R6_AFTER_TOOLS=str(tools), PY=sys.executable, STUB_SET=label_set,
               STUB_ROOT=str(root), STUB_A=pristine["A"], STUB_B=pristine["B"])
    return root, run, work, env


def _chain(tmp_path, **extra):
    root, run, work, env = _chain_setup(tmp_path)
    env.update(extra)
    return _run(["bash", str(root / "benchmarks/t3/r6_after_run.sh"), run, str(work), "dbc5eeb",
                 str(tmp_path / "line lib"), LINE_SHA], **env)


J = 'import json; p="review/acceptance.json"; a=json.load(open(p)); '
S = '; json.dump(a, open(p, "w"))'


@pytest.mark.parametrize("extra, rc, want", [
    ({}, 0, "R6_AFTER DONE"),
    ({"STUB_MUTATE": J + 'a["transitions"][0]["file"] = "o99.json"' + S}, 14,
     "the temporary and the real set differ"),
    ({"STUB_MUTATE": J + 'a["transitions"][0]["candidate"] = "incomplete"' + S}, 14,
     "transitions[0].candidate"),
    ({"STUB_MUTATE": J + 'a["transitions"][0]["d4"] = "D4-7"' + S}, 14, "transitions[0].d4"),
    ({"STUB_MUTATE": 'import json; p="review/triage.json"; a=json.load(open(p)); '
                     'a["generatable_rows"][0]["class"] = "A"; json.dump(a, open(p, "w"))'}, 14,
     "generatable_rows[0].class"),
    ({"STUB_MUTATE": J + 'a["acceptance"]["failing"].append("triage")' + S}, 14,
     "real review: unexpected checks"),
    ({"STUB_MUTATE": J + 'a["acceptance"]["pending"] = ["oracle_pass:dbc5eeb"]' + S}, 14,
     "real review: unexpected checks"),
    ({"STUB_CHECK_RC": "2"}, 13, "r6_publish_check.sh exit 2"),
    ({"STUB_REAL_RC": "4"}, 14, "r6_publish_release.sh exit 4"),
    ({"STUB_ORACLE_RC": "1"}, 11, "r6_oracle.sh exit 1"),
    ({"STUB_REGISTER_RC": "2"}, 12, "r6_register_line_oracle.py exit 2"),
])
def test_after_run_chain_stops_at_the_first_unexpected_status(tmp_path, extra, rc, want):
    r = _chain(tmp_path, **extra)
    out = r.stdout + r.stderr
    assert r.returncode == rc, out
    assert want in out, out


def test_after_run_refuses_an_unfinished_run(tmp_path):
    root, run, work, env = _chain_setup(tmp_path)
    with open(os.path.join(run, "run.log"), "a") as f:
        f.write("RUN_DONE exit=1 later\n")
    r = _run(["bash", str(root / "benchmarks/t3/r6_after_run.sh"), run, str(work), "dbc5eeb",
              "lib", LINE_SHA], **env)
    assert r.returncode == 10 and "did not finish with exit 0" in r.stdout


# --- r6_oracle.sh --check-only -------------------------------------------------

STUB_RUN_ORACLE = """import os, shutil, sys
a = sys.argv
d, out, md = a[a.index("--drafts") + 1], a[a.index("--json") + 1], a[a.index("--md") + 1]
shutil.copyfile(os.path.join(os.environ["STUB_REPORTS"], d + ".json"), out)
with open(md, "w") as f:
    f.write("| PASS | 4 |\\n")
"""


def _oracle_tree(tmp_path):
    """A clean repository with r6_oracle.sh, the real acceptance_compare.py
    and a stand-in tests/oracle/run_oracle.py that copies the report of its
    dialect from STUB_REPORTS."""
    tree = tmp_path / "oracle tree"
    _git_repo(tree, {"benchmarks/t3/r6_oracle.sh": _src("benchmarks", "t3", "r6_oracle.sh"),
                     "benchmarks/protected_paths.sh": _src("benchmarks", "protected_paths.sh"),
                     "benchmarks/t3/tools/acceptance_compare.py":
                         _src("benchmarks", "t3", "tools", "acceptance_compare.py"),
                     "tests/oracle/run_oracle.py": STUB_RUN_ORACLE},
              ignore=("tests/oracle/external/", "tests/oracle/results/", "benchmarks/local/"))
    (tree / "tests/oracle/external/JSON-Schema-Test-Suite").mkdir(parents=True)
    (tree / "tests/oracle/results").mkdir(parents=True)
    libs = tmp_path / "libs with spaces"
    libs.mkdir()
    (libs / "cand.so").write_bytes(LIB_BYTES)
    (libs / "line.so").write_bytes(b"line")
    return tree, libs


def _oracle(tree, *args):
    return _run(["bash", str(tree / "benchmarks/t3/r6_oracle.sh"), "--check-only", *args],
                PY=sys.executable)


def test_oracle_check_only(tmp_path):
    tree, libs = _oracle_tree(tmp_path)
    line_sha = hashlib.sha256(b"line").hexdigest()
    good = ["t3-cand", str(libs / "cand.so"), LIB, "line-x", str(libs / "line.so"), line_sha]
    r = _oracle(tree, *good)
    assert r.returncode == 0 and "setup checks passed" in r.stdout, r.stdout + r.stderr
    assert r.stdout.count("library ok") == 2
    bad_sha = good[:5] + [LIB]  # the line library with the candidate's sha256
    r = _oracle(tree, *bad_sha)
    assert r.returncode == 2 and f"is {line_sha}, not {LIB}" in r.stdout, r.stdout
    r = _oracle(tree, *good[:4], str(libs / "missing.so"), line_sha)
    assert r.returncode == 2 and "cannot read" in r.stdout, r.stdout
    assert _oracle(tree, *good[:5]).returncode == 2  # not TAG LIB SHA256 triples
    r = _oracle(tree, "a/b", *good[1:])
    assert r.returncode == 2 and "tag" in r.stdout
    (tree / "tests/oracle/results/oracle-line-x-draft7-spec.json").write_text("{}")
    r = _oracle(tree, *good)
    assert r.returncode == 2 and "exists" in r.stdout
    (tree / "tests/oracle/results/oracle-line-x-draft7-spec.json").unlink()
    (tree / "untracked.txt").write_text("x")
    r = _oracle(tree, *good)
    assert r.returncode == 2 and "is not clean" in r.stdout


@pytest.mark.parametrize("kind", ["good", "call refused", "empty", "forged", "one call refusal"])
def test_oracle_launch_stops_at_a_report_that_is_no_evidence(tmp_path, kind):
    """The launch checks every report as it is written: the first one that
    is no evidence (draft6 here) stops it with exit 1, kept for the record."""
    tree, libs = _oracle_tree(tmp_path)
    reports = tmp_path / "reports"
    reports.mkdir()
    for d in DIALECTS:
        (reports / f"{d}.json").write_text(json.dumps(_report(d, LIB, kind if d == "draft6"
                                                               else "good")))
    r = _run(["bash", str(tree / "benchmarks/t3/r6_oracle.sh"), "t3-cand", str(libs / "cand.so"),
              LIB], PY=sys.executable, STUB_REPORTS=str(reports))
    written = sorted(n for n in os.listdir(tree / "tests/oracle/results") if n.endswith(".json"))
    if kind == "good":
        assert r.returncode == 0 and "r6_oracle done" in r.stdout, r.stdout + r.stderr
        assert len(written) == 5
    else:
        assert r.returncode == 1 and "NO EVIDENCE" in r.stdout, r.stdout + r.stderr
        assert written == ["oracle-t3-cand-draft4-spec.json", "oracle-t3-cand-draft6-spec.json"]


# --- compose_release.py -------------------------------------------------------

COMPOSE = os.path.join(SCRIPTS, "compose_release.py")


def _compose_repos(tmp_path):
    comp = {"release_paths": ["a.txt", "d"], "partial_paths": {"p.txt": "by hand: keep X"}}
    main = tmp_path / "main"
    _git_repo(main, {"docs/release/v0.2.0-composition.json": json.dumps(comp),
                     "a.txt": "new a", "d/x": "x", "p.txt": "main p", "core.zig": "main core"},
              ignore=("d/ign",))
    (main / "d/y").write_text("untracked y")
    (main / "d/ign").write_text("ignored")
    w = tmp_path / "w"
    _git_repo(w, {"a.txt": "old a", "d/x": "x", "d/old": "gone in main", "p.txt": "w p",
                  "core.zig": "release core"})
    return main, w


def test_compose_release_copies_release_paths_only(tmp_path):
    main, w = _compose_repos(tmp_path)
    dry = _run([sys.executable, COMPOSE, str(main), str(w), "--dry-run"])
    assert dry.returncode == 0, dry.stderr
    assert "would copy a.txt" in dry.stdout and "would copy d/y" in dry.stdout
    assert "would remove d/old" in dry.stdout and "ignored, not copied: d/ign" in dry.stdout
    assert "partial, by hand: p.txt differs - rule: by hand: keep X" in dry.stdout
    assert (w / "a.txt").read_text() == "old a" and (w / "d/old").exists()
    r = _run([sys.executable, COMPOSE, str(main), str(w)])
    assert r.returncode == 0, r.stderr
    assert (w / "a.txt").read_text() == "new a" and (w / "d/y").read_text() == "untracked y"
    assert not (w / "d/old").exists() and not (w / "d/ign").exists()
    assert (w / "p.txt").read_text() == "w p" and (w / "core.zig").read_text() == "release core"
    again = _run([sys.executable, COMPOSE, str(main), str(w)])
    assert "done: 0 copied, 0 removed" in again.stdout


# --- build_twice.sh refusals (no build) ----------------------------------------

def test_build_twice_refuses_before_any_build(tmp_path):
    script = os.path.join(SCRIPTS, "build_twice.sh")
    for argv, want in ((["HEAD", "xyz", str(tmp_path / "w1")], "not a sha256"),
                       (["no-such-rev", "a" * 64, str(tmp_path / "w2")], "no commit")):
        r = _run(["bash", script, *argv], ZIG="/bin/true")
        assert r.returncode == 2 and want in r.stdout, (argv, r.stdout, r.stderr)
        assert not os.path.exists(argv[2])
    # "no composition": a stand-in repository without docs/release/, so the
    # refusal also holds in a shallow checkout that lacks old revisions.
    stand = tmp_path / "stand-in"
    (stand / ".github" / "scripts").mkdir(parents=True)
    (stand / "benchmarks").mkdir()
    shutil.copy(script, stand / ".github" / "scripts" / "build_twice.sh")
    shutil.copy(os.path.join(ROOT, "benchmarks", "protected_paths.sh"),
                stand / "benchmarks" / "protected_paths.sh")
    _git_repo(stand, {})
    w3 = str(tmp_path / "w3")
    r = _run(["bash", os.path.join(stand, ".github", "scripts", "build_twice.sh"),
              "HEAD", "a" * 64, w3], ZIG="/bin/true")
    assert r.returncode == 2 and "no composition" in r.stdout, (r.stdout, r.stderr)
    assert not os.path.exists(w3)
