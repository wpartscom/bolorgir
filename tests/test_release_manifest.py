"""Release line and gate on the tracked workflow and
composition, and on synthetic evidence; nothing is built or published.

- the workflow graph: a PyPI upload needs the gate, the line check and
  the wheel install on every supported CPython; the GitHub Release needs
  the PyPI upload and its install checks; a TestPyPI upload is manual and
  needs the line check and not the gate; an rc tag makes a draft GitHub
  pre-release after the line, the preview check and the wheel install on
  every supported CPython, with no index upload;
- the gate refuses a final release without the R6 verdict and the T8a
  report of the built core, and passes with them; the preview check needs
  the admitted R6 verdict of the built core, and the composition names
  the committed verdict of 6e62e0d1;
- the composition: on the release line the bindings call only base ABI
  functions; on the wave-2 tree they call exactly the later-line
  functions besides, so a package of the base core must take the base
  bindings; the sources check compares the tree with the per-file
  manifest of the core and the bindings and needs no git history: a
  changed pinned file, an extra file under the core paths, a missing
  file and a wave-2 path all fail it.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMP_PATH = os.path.join(ROOT, "docs", "release", "v0.2.0-composition.json")


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load("release_gate", ".github/scripts/release_gate.py")
COMP = json.load(open(COMP_PATH, encoding="utf-8"))


# --- workflow graph --------------------------------------------------------

def _jobs():
    yaml = pytest.importorskip("yaml")
    with open(os.path.join(ROOT, ".github", "workflows", "release.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["jobs"]


def _needs(jobs, name, seen=None):
    seen = set() if seen is None else seen
    n = jobs[name].get("needs") or []
    for dep in [n] if isinstance(n, str) else n:
        if dep not in seen:
            seen.add(dep)
            _needs(jobs, dep, seen)
    return seen


def _run_text(job):
    return "\n".join(s.get("run", "") for s in job.get("steps", []))


def test_pypi_upload_needs_the_gate_and_the_line():
    jobs = _jobs()
    assert {"gate", "line", "core", "wheel", "sdist-install", "wheel-smoke"} <= \
        _needs(jobs, "publish-pypi")
    assert "prerelease == 'false'" in jobs["publish-pypi"]["if"]
    assert "prerelease == 'false'" in jobs["gate"]["if"]
    assert "release_gate.py gate" in _run_text(jobs["gate"])
    assert "publish-pypi" in _needs(jobs, "github-release")


def test_testpypi_upload_is_manual_and_needs_the_line_not_the_gate():
    jobs = _jobs()
    deps = _needs(jobs, "publish-testpypi")
    assert "line" in deps and "gate" not in deps
    cond = jobs["publish-testpypi"]["if"]
    assert "inputs.publish == 'testpypi'" in cond and "push" not in cond
    text = _run_text(jobs["line"])
    assert "release_gate.py sources" in text and "abi --base-only" in text


def test_rc_tag_makes_a_draft_prerelease_without_an_index_upload():
    jobs = _jobs()
    job = jobs["github-prerelease"]
    deps = _needs(jobs, "github-prerelease")
    assert {"line", "core", "preview", "wheel", "sdist-install", "wheel-smoke"} <= deps
    assert not deps & {"gate", "publish-pypi", "publish-testpypi"}
    for name in ("github-prerelease", "preview"):
        assert "push" in jobs[name]["if"] and "prerelease == 'true'" in jobs[name]["if"]
    assert "release_gate.py preview" in _run_text(jobs["preview"])
    rel = [s for s in job["steps"] if str(s.get("uses", "")).startswith("softprops/")]
    assert len(rel) == 1
    assert rel[0]["with"]["draft"] is True and rel[0]["with"]["prerelease"] is True
    assert "dist/SHA256SUMS" in rel[0]["with"]["files"]
    text = _run_text(job)
    assert "core.sha256" in text and "GITHUB_SHA" in text and "releases" in text
    # The final Release still comes only after the PyPI upload.
    assert "publish-pypi" in _needs(jobs, "github-release")


def test_wheel_install_runs_on_every_supported_python():
    job = _jobs()["wheel-smoke"]
    assert job["strategy"]["matrix"]["python-version"] == ["3.12", "3.13", "3.14", "3.15"]
    assert "slim" in job["container"]
    text = _run_text(job)
    assert "smoke_release.py" in text and "examples/quickstart.py" in text
    assert "core.sha256" in text


def test_failed_smoke_checks_block_the_next_stage():
    """A failed (or skipped) wheel install blocks the PyPI upload; a failed
    (or skipped) PyPI install blocks the GitHub Release (needs semantics:
    in Release #2 of the rc preview the draft pre-release was skipped after
    the wheel job failed, although its if matched). smoke-pypi re-checks
    the version and the core of the installed package, as wheel-smoke
    does, and the final Release carries SHA256SUMS and the build
    identity."""
    jobs = _jobs()
    assert "wheel-smoke" in _needs(jobs, "publish-pypi")
    assert "smoke-pypi" in _needs(jobs, "github-release")
    for name in ("publish-pypi", "github-release"):
        cond = jobs[name].get("if", "")
        assert "always()" not in cond and "failure()" not in cond
    assert {"core", "publish-pypi"} <= _needs(jobs, "smoke-pypi")
    downloads = [s.get("with", {}).get("name") for s in jobs["smoke-pypi"]["steps"]
                 if "download-artifact" in str(s.get("uses", ""))]
    assert "core" in downloads
    text = _run_text(jobs["smoke-pypi"])
    assert "core.sha256" in text and "bolorgir.__version__" in text and "_lib" in text
    text = _run_text(jobs["github-release"])
    assert "SHA256SUMS" in text and "rev-parse HEAD" in text and "core/core.sha256" in text
    rel = [s for s in jobs["github-release"]["steps"]
           if str(s.get("uses", "")).startswith("softprops/")]
    assert len(rel) == 1 and "dist/SHA256SUMS" in rel[0]["with"]["files"]
    # The FILL check stays before any upload.
    assert "FILL:" in _run_text(jobs["version"])
    assert "FILL:" in _run_text(jobs["github-release"])


def test_every_build_checks_the_bindings_against_the_built_core():
    jobs = _jobs()
    text = _run_text(jobs["core"])
    assert "release_gate.py abi" in text and "--lib zig-out/lib/libbolorgir.so.0.1.0" in text
    assert "PRELIMINARY" in text
    assert "accepted_core.sha256" not in json.dumps(jobs)


# --- gate ------------------------------------------------------------------

CORE = "c" * 64


def _evidence(tmp_path, verdict="admitted", cand=CORE, t8a_ok=True, t8a_core=CORE, unset=()):
    (tmp_path / "ev").mkdir(exist_ok=True)
    acc = {"acceptance": {"verdict": verdict}, "inputs": {"candidate": {"library_sha256": cand}}}
    t8a = {"t8a": {"accepted": t8a_ok}, "core_sha256": t8a_core}
    (tmp_path / "ev" / "acceptance.json").write_text(json.dumps(acc))
    (tmp_path / "ev" / "t8a.json").write_text(json.dumps(t8a))
    comp = dict(COMP, gate={"r6_acceptance": None if "r6" in unset else "ev/acceptance.json",
                            "t8a_report": None if "t8a" in unset else "ev/t8a.json"})
    return comp


def test_gate_without_evidence_refuses_a_final_release():
    comp = dict(COMP, gate={"r6_acceptance": None, "t8a_report": None})
    reasons = gate.check_gate(comp, ROOT, CORE)
    assert len(reasons) == 2 and all("is not set" in r for r in reasons)


def test_gate_refuses_incomplete_or_foreign_evidence(tmp_path):
    cases = [{"unset": ("r6",)}, {"unset": ("t8a",)}, {"verdict": "not_admitted"},
             {"verdict": "intermediate"}, {"t8a_ok": False}, {"cand": "d" * 64},
             {"t8a_core": "d" * 64}]
    for kw in cases:
        reasons = gate.check_gate(_evidence(tmp_path, **kw), str(tmp_path), CORE)
        assert reasons, kw
    reasons = gate.check_gate(_evidence(tmp_path), str(tmp_path), "e" * 64)
    assert any("built core" in r for r in reasons)


def test_gate_passes_with_the_evidence_of_the_built_core(tmp_path):
    assert gate.check_gate(_evidence(tmp_path), str(tmp_path), CORE) == []


def test_gate_cli_refuses_a_missing_core_hash():
    assert gate.main(["gate", "--composition", COMP_PATH, "--tree", ROOT]) == 1


def test_preview_needs_the_admitted_r6_verdict_of_the_built_core(tmp_path):
    def preview(**kw):
        return dict(_evidence(tmp_path, **kw), preview={"r6_acceptance": "ev/acceptance.json"})

    assert gate.check_preview(preview(), str(tmp_path), CORE) == []
    for kw in ({"verdict": "not_admitted"}, {"verdict": "intermediate"}, {"cand": "d" * 64}):
        assert gate.check_preview(preview(**kw), str(tmp_path), CORE), kw
    assert any("built core" in r for r in gate.check_preview(preview(), str(tmp_path), "e" * 64))
    assert gate.check_preview(dict(COMP, preview={}), str(tmp_path), CORE)
    missing = dict(COMP, preview={"r6_acceptance": "ev/none.json"})
    assert any("not in the tree" in r for r in gate.check_preview(missing, str(tmp_path), CORE))
    assert gate.main(["preview", "--composition", COMP_PATH, "--tree", ROOT]) == 1


def test_composition_preview_is_the_committed_verdict_of_the_preview_core():
    want = "6e62e0d1526c6e5369457d2f7205675b7cb163e3374b96d8cb5115c28873e914"
    assert gate.check_preview(COMP, ROOT, want) == []
    assert gate.check_preview(COMP, ROOT, "e" * 64)
    # The preview needs no T8a; the final gate still does.
    assert gate.check_gate(COMP, ROOT, want)


# --- ABI of the line -------------------------------------------------------

def _declared():
    with open(os.path.join(ROOT, "include", "bolorgir.h"), encoding="utf-8") as f:
        return set(gate.functions_called(f.read()))


def test_header_is_the_base_or_the_later_line():
    base, later = set(COMP["abi"]["base_functions"]), set(COMP["abi"]["later_line_functions"])
    assert not base & later
    assert _declared() in (base, base | later)


def test_bindings_fit_the_line_of_the_tree():
    """On the release line the bindings call only base functions (a
    package of the base core needs no later symbol); on the wave-2 tree
    they call exactly the later-line functions besides, so the base-only
    check refuses them."""
    with open(os.path.join(ROOT, "python", "bolorgir", "_core.c"), encoding="utf-8") as f:
        text = f.read()
    declared = _declared()
    assert set(gate.functions_called(text)) <= declared
    reasons = gate.check_abi(COMP, text, base_only=True)
    if declared == set(COMP["abi"]["base_functions"]):
        assert reasons == []
    else:
        assert len(reasons) == 1
        for fn in COMP["abi"]["later_line_functions"]:
            assert fn in reasons[0]


def test_abi_check_against_exports():
    text = "blg_compile(x); blg_session_rollback(s, 1);"
    assert gate.check_abi(COMP, text, exports={"blg_compile", "blg_session_rollback"}) == []
    reasons = gate.check_abi(COMP, text, exports={"blg_compile"})
    assert reasons and "blg_session_rollback" in reasons[0]


def test_composition_paths_are_disjoint_and_present():
    release, later = set(COMP["release_paths"]), set(COMP["later_line_paths"])
    assert not release & later
    assert set(COMP["later_line_absent"]) <= later
    assert not set(COMP["bindings"]["manifest"]) & release
    for p in release:
        assert os.path.exists(os.path.join(ROOT, p)), p
    for p in list(COMP["core"]["manifest"]) + list(COMP["bindings"]["manifest"]):
        assert os.path.exists(os.path.join(ROOT, p)), p
    # The tree itself passes the real check: pinned files, no extra file
    # under the core paths, no wave-2 path present.
    assert gate.check_sources(COMP, ROOT) == []


# --- sources check on a git tree --------------------------------------------

def _git(tree, *args):
    subprocess.run(["git", "-C", str(tree), *args], check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


LINE_FILES = {"build.zig": "b\n", "src/a.zig": "a\n", "src/b.zig": "b2\n",
              "include/bolorgir.h": "h\n", "python/bolorgir/_core.c": "c\n",
              "python/bolorgir/tokenizers.py": "t\n"}


def _blobs(tree, paths):
    out = {}
    for p in paths:
        r = subprocess.run(["git", "-C", str(tree), "hash-object", "--", p],
                           capture_output=True, text=True, check=True)
        out[p] = r.stdout.strip()
    return out


def _line_tree(tmp_path, files=None):
    t = tmp_path / "repo"
    for p, text in (files or LINE_FILES).items():
        (t / p).parent.mkdir(parents=True, exist_ok=True)
        (t / p).write_text(text)
    _git(t, "init", "-q")
    _git(t, "add", "-A")
    _git(t, "commit", "-q", "-m", "line")
    return t


def _line_comp(tree, absent=()):
    return dict(COMP,
                core=dict(COMP["core"], paths=["build.zig", "src", "include"],
                          manifest=_blobs(tree, ["build.zig", "src/a.zig",
                                                 "src/b.zig", "include/bolorgir.h"])),
                bindings={"manifest": _blobs(tree, ["python/bolorgir/_core.c",
                                                    "python/bolorgir/tokenizers.py"])},
                later_line_absent=list(absent))


def test_sources_check_compares_the_tree_with_the_manifest(tmp_path):
    # A single commit: the check reads the manifest, not the history.
    if shutil.which("git") is None:
        pytest.skip("git not available")
    t = _line_tree(tmp_path)
    comp = _line_comp(t)
    assert gate.check_sources(comp, str(t)) == []
    (t / "src" / "a.zig").write_text("changed\n")
    assert any("src/a.zig" in r and "pinned" in r for r in gate.check_sources(comp, str(t)))
    (t / "src" / "a.zig").write_text("a\n")
    (t / "src" / "regex.zig").write_text("new\n")
    reasons = gate.check_sources(comp, str(t))
    assert any("extra" in r and "src/regex.zig" in r for r in reasons)
    (t / "src" / "regex.zig").unlink()
    (t / "src" / "b.zig").unlink()
    assert any("missing" in r and "src/b.zig" in r for r in gate.check_sources(comp, str(t)))


def test_sources_check_refuses_later_line_paths(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not available")
    t = _line_tree(tmp_path)
    comp = _line_comp(t, absent=["benchmarks/agent_eval"])
    assert gate.check_sources(comp, str(t)) == []
    (t / "benchmarks" / "agent_eval").mkdir(parents=True)
    (t / "benchmarks" / "agent_eval" / "x.py").write_text("wave2\n")
    reasons = gate.check_sources(comp, str(t))
    assert any("later-line path present" in r and "agent_eval" in r for r in reasons)


def test_composition_pins_the_release_recipe():
    core = COMP["core"]
    assert "-Dstrip=true" in core["flags"].split()
    assert all(any(p == r or p.startswith(r + "/") for r in core["paths"])
               for p in core["manifest"])
    assert all(re.fullmatch(r"[0-9a-f]{40}", b)
               for b in list(core["manifest"].values())
               + list(COMP["bindings"]["manifest"].values()))
    text = open(os.path.join(ROOT, ".github", "workflows", "release.yml"), encoding="utf-8").read()
    assert f"CORE_FLAGS: {core['flags']}" in text



def test_ci_sdist_builds_the_core_with_the_release_recipe():
    """The sdist of CI bundles a core built like the release one."""
    text = open(os.path.join(ROOT, ".github", "workflows", "ci.yml"), encoding="utf-8").read()
    builds = re.findall(r"^\s*zig build (-D.*)$", text, re.M)
    release_like = [b for b in builds if "-Dtarget=" in b]
    assert release_like, builds
    for b in release_like:
        assert b.split() == COMP["core"]["flags"].split(), b


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash needed")
@pytest.mark.parametrize("env, want", [
    ({"ISO_LINE": "release"}, None),  # None: the composition's core.flags
    ({"ISO_LINE": "wave2"}, "-Drelease=true"),
    ({"ISO_LINE": "release", "RELEASE_FLAGS": "-Dx=1"}, "-Dx=1"),
])
def test_isolated_tests_release_line_takes_the_recipe(env, want):
    """isolated_tests.sh with ISO_LINE=release builds the wheel's core with
    the composition flags by default (T8a runs on the wheel of the
    accepted core); the flag block of the script runs on its own."""
    text = open(os.path.join(ROOT, "benchmarks", "t3", "isolated_tests.sh"), encoding="utf-8").read()
    block = text[text.index('BASELINE_REV="${BASELINE_REV:-'):text.index("read -r -a RFLAGS")]
    script = f'ROOT="$1"\n{block}\nprintf "%s" "$RELEASE_FLAGS"\n'
    clean = {k: v for k, v in os.environ.items() if k not in ("RELEASE_FLAGS", "EXPECT_CORE_SHA256")}
    out = subprocess.run(["bash", "-c", script, "_", ROOT], env={**clean, **env},
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout == (want or COMP["core"]["flags"])


def test_isolated_tests_needs_an_hf_cache_before_any_step(tmp_path):
    """A git worktree has no benchmarks/local/hf-cache: the run names the
    main tree's with ISO_HF_CACHE and refuses a missing one before the
    copy, the venvs and the builds."""
    text = open(os.path.join(ROOT, "benchmarks", "t3", "isolated_tests.sh"), encoding="utf-8").read()
    block = text[text.index("# The HF cache the tests read"):text.index('LIB="$W/release/lib')]
    script = f'ROOT="$1"\n{block}\nprintf "%s" "$HF_SRC"\n'
    (tmp_path / "cache" / "hub").mkdir(parents=True)
    clean = {k: v for k, v in os.environ.items() if k != "ISO_HF_CACHE"}
    for root, cache, rc in ((str(tmp_path / "worktree"), None, 2),
                            (str(tmp_path / "worktree"), str(tmp_path / "cache"), 0),
                            (str(tmp_path / "worktree"), "cache", 0),
                            (str(tmp_path / "worktree"), str(tmp_path / "none"), 2)):
        env = dict(clean, **({"ISO_HF_CACHE": cache} if cache else {}))
        out = subprocess.run(["bash", "-c", script, "_", root], env=env, cwd=tmp_path,
                             capture_output=True, text=True)
        assert out.returncode == rc, (cache, out.stderr)
        if rc == 0:
            assert out.stdout == str(tmp_path / "cache")
        else:
            assert "set ISO_HF_CACHE" in out.stderr


def test_isolated_tests_refuses_a_malformed_expected_core():
    text = open(os.path.join(ROOT, "benchmarks", "t3", "isolated_tests.sh"), encoding="utf-8").read()
    block = text[text.index('BASELINE_REV="${BASELINE_REV:-'):text.index("read -r -a RFLAGS")]
    script = f'ROOT="$1"\n{block}\necho reached\n'
    for value, rc in (("xyz", 2), ("6e62e0d1", 2), ("a" * 64, 0)):
        out = subprocess.run(["bash", "-c", script, "_", ROOT],
                             env={**os.environ, "ISO_LINE": "release", "EXPECT_CORE_SHA256": value},
                             capture_output=True, text=True)
        assert out.returncode == rc, (value, out.stderr)


# --- README ------------------------------------------------------------------

def _read(*rel):
    with open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
        return f.read()


def test_readme_quickstart_is_the_checked_example():
    # The release workflow runs examples/quickstart.py from the built wheel on
    # every supported CPython (job wheel-smoke); the README shows that file.
    assert f"```python\n{_read('examples', 'quickstart.py')}```" in _read("README.md")


def test_readme_installs_the_wheel_of_this_version():
    version = re.search(r'^version\s*=\s*"([^"]+)"', _read("python", "pyproject.toml"), re.M)[1]
    # The install section shows the published preview assets, which are the
    # cp310-abi3 build; the prepared cp312-abi3 line is described separately.
    url = (f"https://github.com/wpartscom/bolorgir/releases/download/v{version}/"
           f"bolorgir-{version}-cp310-abi3-manylinux_2_28_x86_64.whl")
    readme = _read("README.md")
    assert f"pip install {url}" in readme
    assert "cp312-abi3" in readme
    assert f"git clone --branch v{version} " in readme
    assert f"CORE_FLAGS: {COMP['core']['flags']}" in _read(".github", "workflows", "release.yml")
    assert f"zig build {COMP['core']['flags']}\n" in readme
