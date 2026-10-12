#!/bin/bash
# Isolated checks of the working tree: a copy of the
# sources in a separate work directory, the release and test-hook builds
# under explicit prefixes there, the Python package and its wheel built
# inside the copy with the build backend of python/pyproject.toml, both
# pytest backends with the T9/T12 tests required to run and not skip, and
# the T9 accept overhead against the c288703 core built with the same
# flags. Nothing is written into zig-out, .venv, python/ of the main tree
# or a benchmarks/local/t3/acc-* run directory; their fingerprints are
# taken before and compared after.
#
# The Python environments and the T9 arguments are prepared and checked
# before any build, so a missing dependency stops the
# run in minutes, not after the Zig tests:
#   venv/        the test interpreter: no packages of its own, the
#                site-packages of BASE_PY on its path (read only)
#   build-venv/  the build backend: pip, setuptools and wheel as
#                [build-system] requires (setuptools>=77), installed
#                into this new venv only (PyPI, or BUILD_WHEELHOUSE)
#
# Usage: isolated_tests.sh <work dir> [pytest args...]
#   <work dir>   new or empty directory outside the protected paths
#                (benchmarks/protected_paths.sh), e.g. benchmarks/local/iso-1
#
# Env: ZIG (zig from PATH), BASE_PY (main .venv python: its site-packages
# are a read-only dependency source of the work venv, never installed
# into; it also creates both venvs; for the hook and adapter tests it
# must provide torch, e.g. benchmarks/local/venv-torch; a relative path
# is taken from the current directory), RELEASE_FLAGS (one string, several
# flags separated by spaces; default "-Drelease=true", with ISO_LINE=release
# the core.flags of the newest docs/release/v*-composition.json, the
# release recipe), EXPECT_CORE_SHA256 (optional: the release build must
# have this sha256, e.g. the accepted T3 candidate, so the wheel carries
# that core; a different build stops the run), BASELINE_REV (2523dd9, the
# Zig 0.17.0 state before the session history; library A of the T9 overhead
# check, protocol in ADR-0014), T9_ROUNDS (20), HF_HUB_OFFLINE (1),
# ISO_HF_CACHE (the HF cache copied into the work dir, default
# benchmarks/local/hf-cache of this tree; a git worktree has none of its
# own, so a run from the release worktree names the main tree's; checked
# before any step),
# ISO_MEM_LIMIT_MB (16384: address-space limit of every process of the
# run, blg_memory_limit in benchmarks/protected_paths.sh; 0 = none),
# BUILD_WHEELHOUSE (a directory with the wheels of pip, setuptools>=77 and
# wheel, installed with --no-index from there; default PyPI, which needs
# the network), ISO_LINE (wave2: the current working tree; release: the
# 0.2.0 line of docs/release/v0.2.0-composition.md, whose flags and
# expected core the release workflow uses. Both lines carry the session,
# regex and vLLM work: their tests are required and the accept-path
# overhead is measured in both modes).
#
# The test dependencies are imported, not only found, before any build:
# torch and transformers (the hook test of the HF adapter), tokenizers,
# the pytest plugins and data libraries (review 3).
#
# Outputs in <work dir>: logs/run.log (every command and its exit status),
# logs/*.xml (JUnit), identity/ (source list with sha256, git head and
# diff, library hashes, test and build venv versions, the differences of
# the test environment from the main .venv, wheel hash, fingerprints),
# dist/bolorgir-*.whl (the wheel of the copy, for the T8a smoke),
# t9_overhead.json. The protected paths, and the environment of BASE_PY
# when it is another venv, are fingerprinted before the first step and
# compared on every exit (EXIT trap): a failed run keeps its exit status
# and still records the comparison, a passed run with a changed path
# fails. The first failing step exits with its status; the last line is
# `== ISOLATED_DONE ok` only when every step passed and the protected
# paths are unchanged.
set -u -o pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
. "$ROOT/benchmarks/protected_paths.sh"

W="${1:?usage: isolated_tests.sh <work dir> [pytest args...]}"
shift
blg_guard_new_dir "$ROOT" "$W" || exit 2
case "$(realpath -m -- "$W")/" in
  "$ROOT"/benchmarks/local/*) ;;
  "$ROOT"/*) echo "refused: inside the repository only benchmarks/local/ may hold $W" >&2; exit 2 ;;
esac
mkdir -p "$W" || exit 2
W="$(cd "$W" && pwd)"

# Env paths are used after `cd "$W/src"`: a relative one is made absolute
# here (no symlink resolution: a venv python must stay the venv's).
abspath() { case "$1" in /*|"") printf '%s' "$1" ;; *) printf '%s/%s' "$PWD" "$1" ;; esac; }
ZIG="$(abspath "${ZIG:-$(command -v zig || true)}")"
[ -n "$ZIG" ] || { echo "zig not found; set ZIG" >&2; exit 2; }
BASE_PY="$(abspath "${BASE_PY:-$ROOT/.venv/bin/python}")"
[ -x "$BASE_PY" ] || { echo "BASE_PY $BASE_PY not found" >&2; exit 2; }
[ -n "${BUILD_WHEELHOUSE:-}" ] && BUILD_WHEELHOUSE="$(abspath "$BUILD_WHEELHOUSE")"
# The environment of BASE_PY, fingerprinted with the protected paths when
# it is a venv other than the main .venv (which is protected already).
BASE_ENV="$("$BASE_PY" -I -c 'import sys; print(sys.prefix if sys.prefix != sys.base_prefix else "")')" \
  || { echo "BASE_PY $BASE_PY does not run" >&2; exit 2; }
[ -n "$BASE_ENV" ] && [ "$(realpath -- "$BASE_ENV")" = "$(realpath -m -- "$ROOT/.venv")" ] && BASE_ENV=""
BASELINE_REV="${BASELINE_REV:-2523dd9}"
T9_ROUNDS="${T9_ROUNDS:-20}"
ISO_LINE="${ISO_LINE:-wave2}"
case "$ISO_LINE" in wave2|release) ;; *) echo "ISO_LINE must be wave2 or release" >&2; exit 2 ;; esac
git -C "$ROOT" cat-file -e "$BASELINE_REV^{commit}" 2>/dev/null \
  || { echo "BASELINE_REV $BASELINE_REV is not in the history of $ROOT; set BASELINE_REV" >&2; exit 2; }
if [ -z "${RELEASE_FLAGS:-}" ] && [ "$ISO_LINE" = release ]; then
  # The release recipe of the composition (the flags the acceptance
  # candidate and the release workflow are built with).
  comp="$(ls "$ROOT"/docs/release/v*-composition.json 2>/dev/null | sort -V | tail -n 1)"
  [ -n "$comp" ] || { echo "no docs/release/v*-composition.json for ISO_LINE=release" >&2; exit 2; }
  RELEASE_FLAGS="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["core"]["flags"])' "$comp")" \
    || { echo "no core.flags in $comp" >&2; exit 2; }
fi
RELEASE_FLAGS="${RELEASE_FLAGS:--Drelease=true}"
if [ -n "${EXPECT_CORE_SHA256:-}" ] && ! [[ "$EXPECT_CORE_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "EXPECT_CORE_SHA256 must be a sha256 (64 hex digits)" >&2; exit 2
fi
read -r -a RFLAGS <<< "$RELEASE_FLAGS"
# The HF cache the tests read, copied into the work dir by the copy step.
HF_SRC="${ISO_HF_CACHE:-$ROOT/benchmarks/local/hf-cache}"
case "$HF_SRC" in /*) ;; *) HF_SRC="$PWD/$HF_SRC" ;; esac
[ -d "$HF_SRC/hub" ] || { echo "no HF cache at $HF_SRC (hub/ missing); set ISO_HF_CACHE" >&2; exit 2; }
LIB="$W/release/lib/libbolorgir.so.0.1.0"
HOOKS_LIB="$W/hooks/lib/libbolorgir.so.0.1.0"
BASE_LIB="$W/base/lib/libbolorgir.so.0.1.0"
# One argv for the early parse check and for the measurement: a flags
# value starts with "-" and goes with "=".
T9_ARGS=(--check-bound --lib-a "$BASE_LIB" --lib-b "$LIB"
         "--flags-a=$RELEASE_FLAGS" "--flags-b=$RELEASE_FLAGS"
         --rounds "$T9_ROUNDS" --out "$W/t9_overhead.json")

mkdir -p "$W/logs" "$W/identity" || exit 2
L="$W/logs/run.log"
: > "$L"
export PYTHONDONTWRITEBYTECODE=1
ISO_MEM_LIMIT_MB="${ISO_MEM_LIMIT_MB:-16384}"
blg_memory_limit "$ISO_MEM_LIMIT_MB" || exit 2
echo "address space per process: $(ulimit -v) kB (ISO_MEM_LIMIT_MB=$ISO_MEM_LIMIT_MB)" \
  | tee "$W/identity/limits.txt" >> "$L"

step() {
  local name="$1"; shift
  echo "== $name: $*" >> "$L"
  if "$@" >> "$L" 2>&1; then
    echo "== exit 0" >> "$L"
  else
    local rc=$?
    echo "== exit $rc" >> "$L"
    echo "== FAILED: $name (exit $rc)" | tee -a "$L"
    exit "$rc"
  fi
}

# --- identity and fingerprints of the protected paths ----------------------
iso_fingerprint() {
  blg_fingerprint "$ROOT"
  if [ -n "$BASE_ENV" ]; then
    printf '%s base env:%s\n' "$(find "$BASE_ENV" -printf '%P %s %T@ %l\n' 2>/dev/null \
      | LC_ALL=C sort | sha256sum | cut -c1-64)" "$BASE_ENV"
  fi
  return 0
}
iso_fingerprint > "$W/identity/protected.before"
ISO_COMPLETE=0
# On every exit, the failing ones included: compare the fingerprints and
# keep the exit status of the failed step; only a passed run with
# unchanged paths ends with ISOLATED_DONE.
on_exit() {
  local rc=$?
  trap - EXIT
  iso_fingerprint > "$W/identity/protected.after"
  echo "== protected paths: diff identity/protected.before identity/protected.after" >> "$L"
  if diff "$W/identity/protected.before" "$W/identity/protected.after" >> "$L" 2>&1; then
    echo "== protected paths unchanged" | tee -a "$L"
    if [ "$rc" = 0 ] && [ "$ISO_COMPLETE" = 1 ]; then
      echo "== ISOLATED_DONE ok" | tee -a "$L"
    fi
  else
    echo "== FAILED: protected paths changed (identity/protected.before, .after)" | tee -a "$L"
    [ "$rc" = 0 ] && rc=1
  fi
  [ "$rc" = 0 ] && [ "$ISO_COMPLETE" != 1 ] && rc=1
  exit "$rc"
}
trap on_exit EXIT
git -C "$ROOT" rev-parse HEAD > "$W/identity/git_head"
git -C "$ROOT" status --porcelain > "$W/identity/git_status"
git -C "$ROOT" diff --binary | sha256sum > "$W/identity/git_diff.sha256"

# --- copy of the sources: tracked and untracked, ignored files excluded ----
copy_sources() {
  mkdir -p "$W/src" &&
  git -C "$ROOT" ls-files -z -c -o --exclude-standard \
    | (cd "$ROOT" && tar --null -T - -cf -) | tar -xf - -C "$W/src" &&
  (cd "$W/src" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) \
    > "$W/identity/sources.sha256" &&
  # Read-only external data the tests read (suite, corpora); HF cache copied.
  ln -s "$ROOT/tests/oracle/external" "$W/src/tests/oracle/external" &&
  ln -s "$ROOT/benchmarks/external" "$W/src/benchmarks/external" &&
  cp -a "$HF_SRC" "$W/hf-cache"
}
step copy copy_sources
export HF_HOME="$W/hf-cache" HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
cd "$W/src" || exit 2

# --- Python environments and dependencies, before any build ----------------
make_venv() {
  local sp base_sp
  "$BASE_PY" -m venv --without-pip "$W/venv" || return 1
  sp="$("$W/venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" || return 1
  base_sp="$("$BASE_PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" || return 1
  # A plain path entry: the .pth files of the base (its editable bolorgir
  # finder among them) are not processed.
  echo "$base_sp" > "$sp/_base_deps.pth"
}
step venv make_venv
PY="$W/venv/bin/python"
# Imported, not only found (review 3): torch and transformers for the
# hook test of the HF adapter, tokenizers and transformers for the
# metaspace tests; the versions of everything the test interpreter sees
# go to identity/test-env.json, its differences from the main .venv to
# identity/test-env-vs-main-venv.txt.
TEST_DEPS=(pytest numpy jsonschema yaml tomli packaging torch transformers tokenizers regress)
step "test deps" "$PY" - "$W/identity/test-env.json" "$BASE_PY" "${TEST_DEPS[@]}" <<'PYEOF'
import importlib, importlib.metadata as md, json, sys, traceback
out, base_py, mods = sys.argv[1], sys.argv[2], sys.argv[3:]
failed = {}
for m in mods:
    try:
        importlib.import_module(m)
    except Exception:
        failed[m] = traceback.format_exc(limit=1).strip().splitlines()[-1]
if "transformers" not in failed and "torch" not in failed:
    try:  # what bolorgir.transformers imports
        from transformers import GenerationConfig, LogitsProcessor  # noqa: F401
    except Exception:
        failed["transformers (GenerationConfig, LogitsProcessor)"] = \
            traceback.format_exc(limit=1).strip().splitlines()[-1]
dists = {}
for d in md.distributions():
    dists.setdefault(d.metadata["Name"].lower().replace("_", "-"), d.version)
json.dump({"base_py": base_py, "python": sys.version.split()[0], "prefix": sys.prefix,
           "imported": [m for m in mods if m not in failed], "failed": failed,
           "distributions": dict(sorted(dists.items()))}, open(out, "w"), indent=1)
for m in mods:
    print(f"{m}: {'FAILED ' + failed[m] if m in failed else 'ok'}")
for k in failed:
    if k not in mods:
        print(f"{k}: FAILED {failed[k]}")
sys.exit(1 if failed else 0)
PYEOF
test_env_vs_main() {
  local main="$ROOT/.venv/bin/python"
  [ -x "$main" ] || { echo "# no main .venv"; return 0; }
  "$main" -I - "$W/identity/test-env.json" <<'PYEOF'
import importlib.metadata as md, json, sys
test = json.load(open(sys.argv[1]))["distributions"]
main = {}
for d in md.distributions():
    main.setdefault(d.metadata["Name"].lower().replace("_", "-"), d.version)
print("# distribution: main .venv -> test environment (identity/test-env.json)")
for n in sorted(set(test) | set(main)):
    if test.get(n) != main.get(n):
        print(f"{n}: {main.get(n, '-')} -> {test.get(n, '-')}")
PYEOF
}
test_env_vs_main > "$W/identity/test-env-vs-main-venv.txt" 2>> "$L" \
  || { echo "== FAILED: test env vs main venv" | tee -a "$L"; exit 1; }

# The build backend of python/pyproject.toml in a venv of its own: the
# setuptools of BASE_PY (59.6 in the main .venv) is older than the
# required one and is not upgraded there. ensurepip gives this host's pip
# (22.0.2), so pip is upgraded inside the new venv first.
make_build_venv() {
  local bv="$W/build-venv" src=()
  [ -n "${BUILD_WHEELHOUSE:-}" ] && src=(--no-index --find-links "$BUILD_WHEELHOUSE")
  "$BASE_PY" -m venv "$bv" || return 1
  "$bv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir "${src[@]}" \
    "pip>=23" || return 1
  "$bv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir "${src[@]}" \
    "setuptools>=77" wheel
}
step "build venv" make_build_venv
BPY="$W/build-venv/bin/python"
check_build_reqs() {
  "$BPY" -c 'import importlib.metadata as m, json
print(json.dumps({d.metadata["Name"].lower(): d.version for d in m.distributions()}, sort_keys=True))' \
    > "$W/identity/build-venv.json" || return 1
  "$PY" - "$W/src/python/pyproject.toml" "$W/identity/build-venv.json" <<'PYEOF'
import json, sys, tomli
from packaging.requirements import Requirement
reqs = [Requirement(r) for r in tomli.load(open(sys.argv[1], "rb"))["build-system"]["requires"]]
have = json.load(open(sys.argv[2]))
bad = [f"{r} (build venv: {have.get(r.name.lower())})" for r in reqs
       if have.get(r.name.lower()) is None
       or not r.specifier.contains(have[r.name.lower()], prereleases=True)]
print("build-system requires", [str(r) for r in reqs], "->", "; ".join(bad) or "satisfied")
sys.exit(1 if bad else 0)
PYEOF
}
step "build requirements" check_build_reqs
step "t9 arguments" "$PY" "$W/src/benchmarks/bench_rollback_overhead.py" "${T9_ARGS[@]}" \
  --check-args

# --- Zig: format, unit tests, builds under explicit prefixes ---------------
step fmt "$ZIG" fmt --check src build.zig
step zigtest timeout 5400 "$ZIG" build test --summary all
step release "$ZIG" build "${RFLAGS[@]}" --prefix "$W/release"
step hooks "$ZIG" build "${RFLAGS[@]}" -Dtest_hooks=true --prefix "$W/hooks"
step no_test_exports bash -c '! nm -D --defined-only "$1" | grep -q " blg_test_"' _ "$LIB"
sha256sum "$LIB" "$HOOKS_LIB" > "$W/identity/libs.sha256"
echo "$RELEASE_FLAGS" > "$W/identity/release_flags.txt"
if [ -n "${EXPECT_CORE_SHA256:-}" ]; then
  step expected_core bash -c '[ "$(sha256sum "$1" | cut -d" " -f1)" = "$2" ] || {
    echo "the release build $(sha256sum "$1" | cut -c1-16) is not the expected core ${2:0:16}"; exit 1; }' \
    _ "$LIB" "$EXPECT_CORE_SHA256"
fi

# --- Python package: the wheel and the in-place build, both in the copy -----
# BLG_CORE_LIB names the release build; nothing reads zig-out.
build_wheel() {
  mkdir -p "$W/dist" &&
  env -C "$W/src/python" BLG_SKIP_ZIG=1 BLG_CORE_LIB="$W/release/lib" PIP_NO_CACHE_DIR=1 \
    "$BPY" -m pip wheel --disable-pip-version-check --no-deps --no-build-isolation --no-index \
    -w "$W/dist" "$W/src/python" &&
  [ "$(find "$W/dist" -name 'bolorgir-*.whl' | wc -l)" = 1 ] &&
  sha256sum "$W"/dist/bolorgir-*.whl > "$W/identity/wheel.sha256"
}
step wheel build_wheel
WHEEL="$(find "$W/dist" -name 'bolorgir-*.whl')"
step wheel_identity "$PY" -I - "$W" "$LIB" "$WHEEL" <<'PYEOF'
import hashlib, os, sys, zipfile
w, lib, wheel = os.path.realpath(sys.argv[1]), sys.argv[2], sys.argv[3]
want = hashlib.sha256(open(lib, "rb").read()).hexdigest()
d = os.path.join(w, "wheel-check")
with zipfile.ZipFile(wheel) as z:
    cores = {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()
             if n.startswith("bolorgir/_lib/libbolorgir") and not n.endswith("/")}
    assert cores and set(cores.values()) == {want}, (cores, want)
    z.extractall(d)
sys.path.insert(0, d)
import bolorgir, bolorgir._core as core
for mod in (bolorgir, core):
    assert os.path.realpath(mod.__file__).startswith(d + os.sep), mod.__file__
print("wheel", os.path.basename(wheel), "bolorgir", bolorgir.__version__, "core", want[:16])
PYEOF
step build_ext env -C "$W/src/python" BLG_SKIP_ZIG=1 BLG_CORE_LIB="$W/release/lib" \
  "$BPY" setup.py build_ext --inplace
export PYTHONPATH="$W/src/python" BLG_LIB_PATH="$LIB" BLG_TEST_HOOKS_LIB="$HOOKS_LIB" \
  BLG_TEST_REQUIRE_BACKEND=1
step package_identity "$PY" - "$W" "$LIB" <<'PYEOF'
import hashlib, os, sys
w, lib = os.path.realpath(sys.argv[1]), sys.argv[2]
import bolorgir, bolorgir._core as core
for mod in (bolorgir, core):
    assert os.path.realpath(mod.__file__).startswith(os.path.join(w, "src") + os.sep), mod.__file__
libdir = os.path.join(os.path.dirname(bolorgir.__file__), "_lib")
want = hashlib.sha256(open(lib, "rb").read()).hexdigest()
got = hashlib.sha256(open(os.path.join(libdir, "libbolorgir.so.0.1.0"), "rb").read()).hexdigest()
assert got == want, (got, want)
print("bolorgir", bolorgir.__version__, bolorgir.__file__, "core", got[:16])
PYEOF

# --- tests ------------------------------------------------------------------
junit_clean() {
  # junit_clean XML MIN [module prefix...]: no failure, error or skip in the
  # selected test cases (all when no prefix), and at least MIN of them.
  "$PY" - "$@" <<'PYEOF'
import sys, xml.etree.ElementTree as ET
path, need, prefixes = sys.argv[1], int(sys.argv[2]), sys.argv[3:]
cases = [c for c in ET.parse(path).getroot().iter("testcase")
         if not prefixes or any(c.get("classname", "").startswith(p) for p in prefixes)]
bad = [(c.get("classname"), c.get("name"), k.tag) for c in cases for k in c
       if k.tag in ("failure", "error", "skipped")]
print(f"{len(cases)} cases selected, {len(bad)} failed, errored or skipped")
for b in bad[:20]:
    print("  ", *b)
sys.exit(0 if len(cases) >= need and not bad else 1)
PYEOF
}
J="$W/logs/hooks.xml"
step "hooks tests" "$PY" -m pytest -q -p no:cacheprovider --junit-xml="$J" \
  "tests/test_probe.py::test_probe_found_and_inconclusive_with_hooks" \
  "python/tests/test_hf_adapters.py::test_fail_row_with_unknown_forced_by_the_test_hook"
step "hooks no-skips" junit_clean "$J" 2
REQUIRED=(tests.test_t3_acceptance_tools tests.test_t8a_tools tests.test_release_manifest
          tests.test_session_rollback tests.test_regex_constraint tests.test_agent_qualify
          tests.test_bench_rollback_args)
for b in ctypes package; do
  J="$W/logs/pytest-$b.xml"
  step "pytest $b" env BLG_TEST_BACKEND=$b timeout 5400 "$PY" -m pytest tests/ python/tests/ -q \
    -p no:cacheprovider -rs --junit-xml="$J" "$@"
  step "required tests $b" junit_clean "$J" 20 "${REQUIRED[@]}"
done

# --- T9: accept overhead with max_rollback_tokens = 0 -----------------------
build_baseline() {
  mkdir -p "$W/base-src" &&
  git -C "$ROOT" archive "$BASELINE_REV" | tar -xf - -C "$W/base-src" &&
  (cd "$W/base-src" && "$ZIG" build "${RFLAGS[@]}" --prefix "$W/base")
}
step "baseline $BASELINE_REV" build_baseline
sha256sum "$BASE_LIB" >> "$W/identity/libs.sha256"
step "t9 overhead" "$PY" "$W/src/benchmarks/bench_rollback_overhead.py" "${T9_ARGS[@]}"

# --- every step passed; the EXIT trap compares the protected paths ---------
ISO_COMPLETE=1
