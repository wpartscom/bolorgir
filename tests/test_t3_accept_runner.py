"""Isolation of the T3 acceptance runner (benchmarks/t3/run_t3_accept.sh
with benchmarks/t3/t3_identity.py).

On stand-in inputs - a git checkout holding the runner, its identity tool,
pins and a stand-in harness; two corpora, one a git checkout at the pinned
revision; an HF cache with symlinked snapshots; a library - a prepared run
directory resumes only while every input is unchanged, refuses each
changed input by name, refuses an interrupted preparation, and passes the
harness exit status through. The venv of a run is created at its final
path by the interpreter under test; nothing is installed from an index.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(ROOT, "benchmarks", "t3", "run_t3_accept.sh")
TOOL = os.path.join(ROOT, "benchmarks", "t3", "t3_identity.py")

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                                reason="git and bash are needed")

STANDIN = '''"""Stand-in harness of the runner tests: records how it was run."""
import json
import os
import sys

TOKENIZER_REVISION = "g1"
LLAMA_REVISION = "l1"


class _Spec:
    tokens = (b"a", b"b")


def hf_tokenizer_spec(name, revision):
    path = os.path.join(os.environ["HF_HOME"], "hub", "models--" + name.replace("/", "--"),
                        "snapshots", revision, "tokenizer.json")
    with open(path, encoding="utf-8") as f:
        f.read()
    return _Spec()


if __name__ == "__main__":
    out = sys.argv[sys.argv.index("--out-dir") + 1]
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "run.json"), "w", encoding="utf-8") as f:
        json.dump({"argv": sys.argv[1:], "lib": os.environ["BLG_LIB_PATH"],
                   "hf": os.environ["HF_HOME"], "exe": sys.executable,
                   "path": sys.path}, f)
    sys.exit(int(os.environ.get("T3_STANDIN_EXIT", "0")))
'''

# Stand-in of python/setup.py build_ext --inplace: the core library of
# BLG_CORE_LIB into bolorgir/_lib (links as links), and a _core module.
STANDIN_SETUP = '''import os, shutil, sys
assert sys.argv[1:] == ["build_ext", "--inplace"], sys.argv
assert os.environ.get("BLG_SKIP_ZIG") == "1"
src = os.environ["BLG_CORE_LIB"]
dst = os.path.join("bolorgir", "_lib")
os.makedirs(dst, exist_ok=True)
for name in sorted(os.listdir(src)):
    sp, dp = os.path.join(src, name), os.path.join(dst, name)
    if os.path.islink(sp):
        os.symlink(os.readlink(sp), dp)
    else:
        shutil.copy2(sp, dp)
if os.environ.get("T3_STANDIN_BAD_LIB"):
    with open(os.path.join(dst, "libbolorgir.so.0.1.0"), "ab") as f:
        f.write(b"other")
with open(os.path.join("bolorgir", "_core.py"), "w") as f:
    f.write("BUILT = True\\n")
'''

MAXI = "benchmarks/external/jsonschemabench-maskbench"


def _git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


def _tool():
    sys.path.insert(0, os.path.dirname(TOOL))
    try:
        import t3_identity
    finally:
        sys.path.pop(0)
    return t3_identity


def _hf_model(hf, name, revision, content, shared=False):
    m = os.path.join(hf, "hub", "models--" + name.replace("/", "--"))
    if shared:
        # As the local cache keeps the Llama tokenizer: the model's blob is
        # a link into a hub-wide blobs directory outside the model.
        _write(os.path.join(hf, "hub", "blobs", "xx", "shared" + revision), content)
        os.makedirs(os.path.join(m, "blobs"))
        os.symlink(os.path.join("..", "..", "blobs", "xx", "shared" + revision),
                   os.path.join(m, "blobs", "b" + revision))
    else:
        _write(os.path.join(m, "blobs", "b" + revision), content)
    snap = os.path.join(m, "snapshots", revision)
    os.makedirs(snap)
    os.symlink(os.path.join("..", "..", "blobs", "b" + revision), os.path.join(snap, "tokenizer.json"))
    return os.path.join("hub", os.path.basename(m), "snapshots", revision, "tokenizer.json")


@pytest.fixture(scope="module")
def checkout(tmp_path_factory):
    base = tmp_path_factory.mktemp("t3run")
    src = str(base / "src")
    for path, dest in ((RUNNER, "benchmarks/t3/run_t3_accept.sh"), (TOOL, "benchmarks/t3/t3_identity.py")):
        _write(os.path.join(src, dest), open(path, "rb").read())
    _write(os.path.join(src, "benchmarks", "bench_jsb_coverage.py"), STANDIN)
    _write(os.path.join(src, "benchmarks", "t3", "acceptance-requirements.txt"),
           "# nothing besides pip: the stand-in harness needs the standard library only\n")
    _write(os.path.join(src, "python", "bolorgir", "__init__.py"), '__version__ = "0-test"\n')
    _write(os.path.join(src, "python", "setup.py"), STANDIN_SETUP)
    _write(os.path.join(src, ".gitignore"), "benchmarks/external/\n/benchmarks/local/\n")
    _write(os.path.join(src, "benchmarks/external/jsonschemabench/data/ds/a.json"), '{"type":"object"}')
    _write(os.path.join(src, "benchmarks/external/jsonschemabench/data/ds/b.json"), '{"type":"string"}')
    mb = os.path.join(src, MAXI)
    _write(os.path.join(mb, "maskbench/data/Ds---a.json"), '{"schema":{}}')
    _write(os.path.join(mb, "README"), "stand-in")
    _git(src, "init", "-q")
    _git(mb, "init", "-q")
    _git(mb, "add", "-A")
    _git(mb, "commit", "-q", "-m", "corpus")
    revision = _git(mb, "rev-parse", "HEAD")
    hf = os.path.join(src, "benchmarks", "local", "hf-cache")
    snaps = [_hf_model(hf, "openai-community/gpt2", "g1", "gpt2 tokenizer"),
             _hf_model(hf, "unsloth/Meta-Llama-3.1-8B-Instruct", "l1", "llama tokenizer", shared=True)]
    t = _tool()
    corpora = {"repo": {"path": "benchmarks/external/jsonschemabench/data"},
               "maskbench": {"path": MAXI + "/maskbench/data", "repository": MAXI,
                             "revision": revision}}
    for c in corpora.values():
        c.update(t.summary(t.file_list(os.path.join(src, c["path"]))))
    pins = {"requirements": "benchmarks/t3/acceptance-requirements.txt", "pip": None,
            "imports": ["json", "sqlite3"], "corpora": corpora,
            "hf": {"models": sorted(os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(s))))
                                    for s in snaps),
                   "files": {s: t.sha256_file(os.path.join(hf, s)) for s in snaps}}}
    _write(os.path.join(src, "benchmarks", "t3", "acceptance_pins.json"), json.dumps(pins, indent=1))
    _git(src, "add", "-A")
    _git(src, "commit", "-q", "-m", "candidate")
    lib = str(base / "libcandidate.so")
    _write(lib, b"\x7fELF candidate")
    return {"base": base, "src": src, "lib": lib, "revision": revision}


def run(checkout, d, *extra, runner=None, jobs="2", lib=None, env=None, timeout=300):
    e = dict(os.environ, T3_PY_BASE=sys.executable, T3_MEM_LIMIT_MB="0")
    e.pop("PYTHONPATH", None)
    e.update(env or {})
    argv = ["bash", runner or os.path.join(checkout["src"], "benchmarks/t3/run_t3_accept.sh"), *extra,
            lib or checkout["lib"], str(d), jobs]
    return subprocess.run(argv, capture_output=True, text=True, env=e, timeout=timeout)


def log_of(d):
    with open(os.path.join(str(d), "run.log"), encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def prepared(checkout):
    d = checkout["base"] / "run-a"
    r = run(checkout, d, "--prepare-only")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RUN_READY" in log_of(d) and "RUN_START" not in log_of(d)
    assert os.path.isfile(os.path.join(str(d), "identity", ".complete"))
    return d


def test_prepared_run_holds_every_input_and_resumes(checkout, prepared):
    d = str(prepared)
    for rel in ("tree/python/bolorgir/_core.py", "tree/python/bolorgir/_lib/libbolorgir.so.0",
                "lib/libbolorgir.so", "tree/benchmarks/external/jsonschemabench/data/ds/a.json",
                "tree/" + MAXI + "/maskbench/data/Ds---a.json",
                "hf-cache/hub/models--openai-community--gpt2/snapshots/g1/tokenizer.json",
                "lib/libbolorgir.so.0.1.0", "venv/.complete", "identity/manifest.json"):
        assert os.path.exists(os.path.join(d, rel)), rel
    # No link leaves the run directory but the venv's base interpreter;
    # the tokenizer snapshots are copied with their links resolved.
    for top, _dirs, files in os.walk(d):
        for name in files:
            p = os.path.join(top, name)
            if os.path.islink(p) and not p.startswith(os.path.join(d, "venv", "bin")):
                assert os.path.realpath(p).startswith(d + os.sep), p
            if p.startswith(os.path.join(d, "hf-cache")):
                assert not os.path.islink(p), p
    m = json.load(open(os.path.join(d, "identity", "manifest.json")))
    assert m["git_head"] == _git(checkout["src"], "rev-parse", "HEAD")
    assert m["corpora"]["maskbench"]["revision"] == checkout["revision"]
    assert m["params"]["jobs"] == 2 and m["venv"]["python"]["executable_sha256"]
    for attempt in range(2):  # a finished run resumes the same way
        r = run(checkout, d)
        assert r.returncode == 0, r.stdout + r.stderr
        rec = json.load(open(os.path.join(d, "out", "run.json")))
        assert rec["lib"] == os.path.join(d, "lib", "libbolorgir.so.0.1.0")
        assert rec["hf"] == os.path.join(d, "hf-cache")
        assert rec["exe"].startswith(os.path.join(d, "venv")), rec["exe"]
        assert not any(p.startswith(checkout["src"]) for p in rec["path"]), rec["path"]
        assert rec["argv"][rec["argv"].index("--jobs") + 1] == "2"
    assert log_of(d).count("RUN_DONE exit=0") == 2


@pytest.mark.parametrize("status", [1, 7])
def test_harness_exit_status_passes_through(checkout, prepared, status):
    r = run(checkout, prepared, env={"T3_STANDIN_EXIT": str(status)})
    assert r.returncode == status, r.stdout + r.stderr
    assert f"RUN_DONE exit={status}" in log_of(prepared)


def _site_packages(d):
    lib = os.path.join(d, "venv", "lib")
    return os.path.join(lib, os.listdir(lib)[0], "site-packages")


def _append(path, data=b"\n# changed\n"):
    with open(path, "ab") as f:
        f.write(data)


MUTATIONS = {
    "tree": (lambda d: _append(os.path.join(d, "tree", "benchmarks", "bench_jsb_coverage.py")),
             ["tree.digest", "changed benchmarks/bench_jsb_coverage.py"]),
    "tree_added": (lambda d: _write(os.path.join(d, "tree", "python", "extra.py"), "x = 1\n"),
                   ["tree.digest", "added python/extra.py"]),
    "lib": (lambda d: _append(os.path.join(d, "lib", "libbolorgir.so.0.1.0"), b"\0"),
            ["lib.sha256"]),
    "venv": (lambda d: _append(os.path.join(_site_packages(d), "pip", "__init__.py")),
             ["venv.digest", "changed lib/"]),
    "venv_added": (lambda d: _write(os.path.join(_site_packages(d), "extra.pth"), "/tmp\n"),
                   ["venv.digest", "added lib/"]),
    "corpus": (lambda d: _append(os.path.join(d, "tree/benchmarks/external/jsonschemabench/data/ds/a.json")),
               ["corpora.repo.digest", "changed ds/a.json"]),
    "corpus_removed": (lambda d: os.remove(os.path.join(d, "tree", MAXI, "maskbench/data/Ds---a.json")),
                       ["corpora.maskbench.files", "removed Ds---a.json"]),
    "hf": (lambda d: _append(os.path.join(d, "hf-cache/hub/models--openai-community--gpt2/snapshots/g1/tokenizer.json")),
           ["hf.digest", "hf.snapshots", "changed hub/models--openai-community--gpt2/snapshots/g1"]),
    "hf_shared_blob": (lambda d: _append(os.path.join(
        d, "hf-cache/hub/models--unsloth--Meta-Llama-3.1-8B-Instruct/snapshots/l1/tokenizer.json")),
        ["hf.snapshots", "changed hub/models--unsloth--Meta-Llama-3.1-8B-Instruct"]),
    "hf_blob": (lambda d: _append(os.path.join(d, "hf-cache/hub/models--openai-community--gpt2/blobs/bg1")),
                ["hf.digest", "changed hub/models--openai-community--gpt2/blobs/bg1"]),
}


@pytest.mark.parametrize("what", sorted(MUTATIONS))
def test_changed_input_is_refused_by_name(checkout, prepared, tmp_path, what):
    d = str(tmp_path / "run-copy")
    shutil.copytree(str(prepared), d, symlinks=True)
    mutate, names = MUTATIONS[what]
    mutate(d)
    before = len(log_of(d))
    r = run(checkout, d, runner=os.path.join(d, "tree/benchmarks/t3/run_t3_accept.sh"))
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    added = log_of(d)[before:]
    assert "inputs differ from identity/manifest.json" in added
    for name in names:
        assert name in out, (name, out)
    assert "RUN_START" not in added


@pytest.mark.parametrize("jobs,env,field", [("3", {}, "params.jobs"),
                                            ("2", {"T3_MEM_LIMIT_MB": "8192"}, "params.mem_limit_mb")])
def test_other_parameters_are_refused(checkout, prepared, jobs, env, field):
    before = os.listdir(str(prepared))
    r = run(checkout, prepared, jobs=jobs, env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    assert field in r.stdout + r.stderr
    assert os.listdir(str(prepared)) == before


def test_other_library_argument_is_refused(checkout, prepared, tmp_path):
    other = str(tmp_path / "other.so")
    _write(other, b"\x7fELF other")
    r = run(checkout, prepared, lib=other)
    assert r.returncode == 2 and "is not the library of this run" in r.stdout + r.stderr


def test_runner_other_than_the_copy_is_refused(checkout, prepared):
    runner = os.path.join(checkout["src"], "benchmarks/t3/run_t3_accept.sh")
    _append(runner)
    try:
        r = run(checkout, prepared)
        assert r.returncode == 2 and "differs from the run's copy" in r.stdout + r.stderr
    finally:
        _git(checkout["src"], "checkout", "--", "benchmarks/t3/run_t3_accept.sh")


def test_interrupted_preparation_is_refused(checkout, tmp_path):
    # The venv step of the preparation is held, the run is stopped there
    # (SIGTERM to its process group); the directory has no marker, and
    # the next start refuses it instead of resuming a partial copy.
    d = tmp_path / "run-cut"
    mark = str(tmp_path / "in-venv")
    slow = str(tmp_path / "slow-python")
    _write(slow, "#!/bin/sh\n"
                 f'if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then touch "{mark}"; sleep 120; fi\n'
                 f'exec "{sys.executable}" "$@"\n')
    os.chmod(slow, 0o755)
    e = dict(os.environ, T3_PY_BASE=slow, T3_MEM_LIMIT_MB="0")
    p = subprocess.Popen(["bash", os.path.join(checkout["src"], "benchmarks/t3/run_t3_accept.sh"),
                          checkout["lib"], str(d), "2"], env=e, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(600):
            if os.path.exists(mark) or p.poll() is not None:
                break
            time.sleep(0.1)
        assert os.path.exists(mark), "the preparation never reached the venv step"
        os.killpg(p.pid, signal.SIGTERM)
        p.wait(timeout=30)
    finally:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
    assert os.path.isdir(os.path.join(str(d), "tree"))
    assert not os.path.exists(os.path.join(str(d), "identity", ".complete"))
    listing = sorted(os.listdir(str(d)))
    r = run(checkout, d)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "interrupted preparation" in log_of(d)
    assert sorted(os.listdir(str(d))) == listing
    assert not os.path.exists(os.path.join(str(d), "out"))


@pytest.mark.parametrize("leftover", ["tree", "venv", "identity"])
def test_partial_directory_is_refused(checkout, tmp_path, leftover):
    d = tmp_path / "run-partial"
    os.makedirs(str(d / leftover))
    r = run(checkout, d)
    assert r.returncode == 2 and "interrupted preparation" in r.stdout + r.stderr


def test_package_built_on_another_library_is_refused(checkout, tmp_path):
    d = tmp_path / "run-package"
    r = run(checkout, d, "--prepare-only", env={"T3_STANDIN_BAD_LIB": "1"})
    assert r.returncode == 3, r.stdout + r.stderr
    assert "package identity" in log_of(d)
    assert not os.path.exists(os.path.join(str(d), "identity", ".complete"))


def test_dirty_checkout_is_refused(checkout, tmp_path):
    stray = os.path.join(checkout["src"], "stray.txt")
    _write(stray, "x")
    try:
        d = tmp_path / "run-dirty"
        r = run(checkout, d)
        assert r.returncode == 2 and "dirty" in r.stdout + r.stderr
        assert os.listdir(str(d)) == ["run.log"]
    finally:
        os.remove(stray)


def test_source_corpus_off_its_pin_is_refused_before_any_copy(checkout, tmp_path):
    f = os.path.join(checkout["src"], "benchmarks/external/jsonschemabench/data/ds/b.json")
    old = open(f, "rb").read()
    _append(f)
    try:
        d = tmp_path / "run-corpus"
        r = run(checkout, d)
        assert r.returncode == 2, r.stdout + r.stderr
        assert "corpus repo" in r.stdout + r.stderr and "differs from the pin" in r.stdout + r.stderr
        assert os.listdir(str(d)) == ["run.log"]
    finally:
        _write(f, old)


def test_maskbench_checkout_must_be_at_its_pinned_revision(checkout, tmp_path):
    mb = os.path.join(checkout["src"], MAXI)
    _write(os.path.join(mb, "NOTES"), "later commit, same data")
    _git(mb, "add", "-A")
    _git(mb, "commit", "-q", "-m", "later")
    try:
        d = tmp_path / "run-rev"
        r = run(checkout, d)
        assert r.returncode == 2 and "pinned " + checkout["revision"] in r.stdout + r.stderr
        assert os.listdir(str(d)) == ["run.log"]
    finally:
        _git(mb, "reset", "-q", "--hard", checkout["revision"])


def test_run_directory_inside_the_checkout_is_refused(checkout):
    d = os.path.join(checkout["src"], "python", "run")
    try:
        r = run(checkout, d)
        assert r.returncode == 2 and "inside the source checkout" in r.stdout + r.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)
