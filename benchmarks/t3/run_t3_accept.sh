#!/bin/bash
# Full acceptance run of a frozen candidate (the R6 acceptance): K = 20 on
# both corpora with the fixed 1-in-20 HF sample, from a
# run directory that holds every input by content - the source tree of the
# candidate commit (git archive), the library, both corpus snapshots, the
# tokenizer snapshots and a venv of its own, created at its final path.
# After the preparation nothing of the source checkout is used: no shared
# .venv, no link to benchmarks/external or to the HF cache.
#
# Usage: run_t3_accept.sh [--prepare-only] <library> <run dir> [jobs]
#   --prepare-only  prepare (or verify) the run directory, then stop
#   <library>       the candidate core library (the stripped release build)
#   <run dir>       new or empty: prepared from this source checkout, which
#                   must be clean (`git status --porcelain` empty); or
#                   prepared: every input is checked again by content
#                   (benchmarks/t3/t3_identity.py check) before the run
#                   resumes; run it with the runner of the run's own copy
#                   (<run dir>/tree/benchmarks/t3/run_t3_accept.sh)
#   [jobs]          chunk subprocesses, default 8
#
# Environment (read at the preparation):
#   T3_PY_BASE       python3 that creates the venv (default python3);
#                    nothing is installed into it; its executable and
#                    standard library are recorded by content
#   T3_HF_SOURCE     HF_HOME the pinned tokenizer snapshots are copied from
#                    (default benchmarks/local/hf-cache)
#   T3_PIP_ARGS      extra pip install arguments, e.g. --no-index
#                    --find-links DIR for a local wheelhouse
#   T3_MEM_LIMIT_MB  address-space limit (ulimit -v) of every process of
#                    the run, default 16384, 0 = none
# The jobs and the memory limit are run parameters: recorded, and a resume
# with other values is refused.
#
# Pins, versioned in the tree: benchmarks/t3/acceptance_pins.json - the two
# corpus snapshots (file count and content digest; the MaskBench checkout
# also by revision and clean), the tokenizer snapshot files by content, the
# pip version and the import check; benchmarks/t3/acceptance-requirements.txt
# - every package of the venv as name==version, installed with --no-deps,
# then `pip check`, and `pip freeze --all` must equal the list.
#
# Preparation: the sources are checked against the pins; tree, corpora,
# tokenizer snapshots and library are copied; the venv is created and
# checked; the identity is recorded (identity/manifest.json,
# identity/files/*.list); the marker identity/.complete comes last. A run
# directory holding anything but run.log without that marker is an
# interrupted preparation: refused - remove it and start again.
#
# Exit status: 0 the run finished; that of bench_jsb_coverage.py when it
# failed (run.log: RUN_DONE exit=N); 2 refused (run.log: RUN_ABORT refused
# ...: dirty source, an input differing from its pin or from the manifest,
# an interrupted preparation, a runner other than the run's copy, a bad
# argument); 3 a preparation step failed (RUN_ABORT preparation ...).
set -u -o pipefail

SRC="$(cd "$(dirname "$0")/../.." && pwd)"
SELF="$SRC/benchmarks/t3/run_t3_accept.sh"
PREP_ONLY=0
if [ "${1:-}" = "--prepare-only" ]; then PREP_ONLY=1; shift; fi
LIB="${1:?library}"; D="${2:?run dir}"; JOBS="${3:-8}"
MEM="${T3_MEM_LIMIT_MB:-16384}"
PY_BASE="${T3_PY_BASE:-python3}"
HF_SOURCE="${T3_HF_SOURCE:-$SRC/benchmarks/local/hf-cache}"
IDTOOL="benchmarks/t3/t3_identity.py"
PINS="benchmarks/t3/acceptance_pins.json"
case "$JOBS" in ''|*[!0-9]*|0) echo "refused: jobs '$JOBS' is not a positive number" >&2; exit 2 ;; esac
case "$MEM" in ''|*[!0-9]*) echo "refused: T3_MEM_LIMIT_MB '$MEM' is not a number of MB" >&2; exit 2 ;; esac
mkdir -p "$D" || exit 3
D="$(cd "$D" && pwd)"
LOG="$D/run.log"
log() { echo "$*" | tee -a "$LOG"; }
refuse() { log "RUN_ABORT refused: $*"; exit 2; }
prep_fail() { log "RUN_ABORT preparation: $*"; exit 3; }
# The run directory may sit under benchmarks/local of the checkout (which
# git ignores), never elsewhere inside it.
case "$D/" in
  "$SRC"/benchmarks/local/?*) ;;
  "$SRC"/*) refuse "$D lies inside the source checkout outside benchmarks/local" ;;
esac
if [ "$MEM" != 0 ]; then
  ulimit -v $((MEM * 1024)) || prep_fail "ulimit -v $((MEM * 1024)) failed"
fi
PARAMS="{\"jobs\": $JOBS, \"passes\": 20, \"hf_sample_every\": 20, \"chunk_size\": 100,"
PARAMS="$PARAMS \"snapshot\": \"both\", \"profile\": \"spec-v1\", \"mem_limit_mb\": $MEM}"
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
unset PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONUSERBASE

# pins FIELD: one value per line from the pins file of the source checkout.
pins() {
  "$PY_BASE" -I -c 'import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8"))
f = sys.argv[2]
v = {"corpora": [c["path"] for c in p["corpora"].values()], "models": p["hf"]["models"],
     "pip": [p["pip"]] if p.get("pip") else [], "imports": p["imports"],
     "requirements": [p["requirements"]],
     "repository": [p["corpora"]["maskbench"]["repository"]]}[f]
print("\n".join(v))' "$SRC/$PINS" "$1"
}

prepare() {
  local head rc c m pipv req imports
  [ -z "$(git --no-optional-locks -C "$SRC" status --porcelain)" ] \
    || refuse "source checkout is dirty; commit the candidate first"
  head="$(git -C "$SRC" rev-parse HEAD)" || prep_fail "git rev-parse HEAD"
  log "RUN_INIT $D head=$head $(date -u +%FT%TZ)"
  "$PY_BASE" -I "$SRC/$IDTOOL" sources --pins "$SRC/$PINS" --root "$SRC" --hf "$HF_SOURCE" 2>&1 | tee -a "$LOG"
  rc="${PIPESTATUS[0]}"
  [ "$rc" = 2 ] && refuse "the sources differ from the pins (run.log)"
  [ "$rc" = 0 ] || prep_fail "source check failed (exit $rc)"
  touch "$D/.preparing" || prep_fail "marker"
  mkdir "$D/tree" "$D/lib" "$D/identity" "$D/hf-cache" "$D/hf-cache/hub" || prep_fail "mkdir"
  git -C "$SRC" archive HEAD | tar -x -C "$D/tree" || prep_fail "git archive HEAD"
  mapfile -t corpora < <(pins corpora) || prep_fail "pins"
  for c in "${corpora[@]}"; do
    mkdir -p "$D/tree/$c" && cp -R --preserve=mode,timestamps "$SRC/$c/." "$D/tree/$c/" \
      || prep_fail "copy of $c"
  done
  mapfile -t models < <(pins models) || prep_fail "pins"
  # Symlinks dereferenced: a cache may link a model's blobs into a shared
  # hub/blobs, which the copy does not hold.
  for m in "${models[@]}"; do
    cp -RL --preserve=mode,timestamps "$HF_SOURCE/hub/$m" "$D/hf-cache/hub/$m" || prep_fail "copy of $m"
  done
  cp "$LIB" "$D/lib/libbolorgir.so.0.1.0" || prep_fail "library copy"
  # The links of a zig-out/lib: the extension links -lbolorgir and loads
  # the SONAME libbolorgir.so.0.
  ln -s libbolorgir.so.0.1.0 "$D/lib/libbolorgir.so.0" && ln -s libbolorgir.so.0 "$D/lib/libbolorgir.so" \
    || prep_fail "library links"
  # The venv is created at its final path and never moved (a venv keeps
  # absolute paths in its scripts and pyvenv.cfg).
  "$PY_BASE" -m venv "$D/venv" >> "$LOG" 2>&1 || prep_fail "venv"
  local vpy="$D/venv/bin/python"
  local pip=("$vpy" -I -m pip --disable-pip-version-check --no-input)
  # shellcheck disable=SC2086
  "${pip[@]}" uninstall -y setuptools >> "$LOG" 2>&1 || prep_fail "setuptools removal"
  pipv="$(pins pip)" || prep_fail "pins"
  if [ -n "$pipv" ]; then
    # shellcheck disable=SC2086
    "${pip[@]}" install --no-deps --no-compile ${T3_PIP_ARGS:-} "pip==$pipv" >> "$LOG" 2>&1 \
      || prep_fail "pip==$pipv"
  fi
  req="$D/tree/$(pins requirements)" || prep_fail "pins"
  if grep -q -v -E '^[[:space:]]*(#|$)' "$req"; then
    # shellcheck disable=SC2086
    "${pip[@]}" install --no-deps --no-compile --require-virtualenv ${T3_PIP_ARGS:-} -r "$req" \
      >> "$LOG" 2>&1 || prep_fail "requirements"
  fi
  "${pip[@]}" check >> "$LOG" 2>&1 || prep_fail "pip check"
  imports="$(pins imports | paste -s -d, -)" || prep_fail "pins"
  if [ -n "$imports" ]; then
    (cd "$D" && "$vpy" -I -c "import $imports") >> "$LOG" 2>&1 || prep_fail "import check"
  fi
  { date -u +%FT%TZ; "${pip[@]}" freeze --all; } > "$D/venv/.complete.tmp" &&
    mv -f "$D/venv/.complete.tmp" "$D/venv/.complete" || prep_fail "venv marker"
  # The package of the tree copy with its own _core, built against the
  # run's library (the harness converts the HF tokenizers with
  # bolorgir.TokenizerBundle): python/bolorgir/_lib gets the same bytes.
  (cd "$D/tree/python" && BLG_SKIP_ZIG=1 BLG_CORE_LIB="$D/lib" "$vpy" setup.py build_ext --inplace) \
    >> "$LOG" 2>&1 || prep_fail "package build (setup.py build_ext)"
  (cd "$D/tree" && PYTHONPATH=python "$vpy" - "$D" <<'PYEOF'
import hashlib, os, sys
d = sys.argv[1]
import bolorgir, bolorgir._core as core
for mod in (bolorgir, core):
    assert os.path.realpath(mod.__file__).startswith(os.path.join(d, "tree", "python") + os.sep), mod.__file__
def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()
want = sha(os.path.join(d, "lib", "libbolorgir.so.0.1.0"))
got = sha(os.path.join(os.path.dirname(bolorgir.__file__), "_lib", "libbolorgir.so.0.1.0"))
assert got == want, (got, want)
print("T3_PACKAGE_OK", getattr(bolorgir, "__version__", "?"), "core", got[:16])
PYEOF
  ) 2>&1 | tee -a "$LOG"
  [ "${PIPESTATUS[0]}" = 0 ] || prep_fail "package identity: the package of the copy is not built on the run's library"
  "$PY_BASE" -I "$D/tree/$IDTOOL" record --run "$D" --params "$PARAMS" --git-head "$head" \
    --revision "$(git -C "$SRC/$(pins repository)" rev-parse HEAD)" 2>&1 | tee -a "$LOG"
  rc="${PIPESTATUS[0]}"
  [ "$rc" = 2 ] && prep_fail "the copies differ from the pins (run.log)"
  [ "$rc" = 0 ] || prep_fail "identity record failed (exit $rc)"
  # Files of the identity record other tools read (r6_publish.sh,
  # full_tests.sh).
  (cd "$D" && sha256sum lib/libbolorgir.so.0.1.0) > "$D/identity/lib.sha256" || prep_fail "lib.sha256"
  echo "$head" > "$D/identity/git_head" || prep_fail "git_head"
  rm -f "$D/.preparing"
  date -u +%FT%TZ > "$D/identity/.complete.tmp" &&
    mv -f "$D/identity/.complete.tmp" "$D/identity/.complete" || prep_fail "identity marker"
  log "RUN_PREPARED $(date -u +%FT%TZ)"
}

if [ ! -f "$D/identity/.complete" ]; then
  if [ -n "$(ls -A "$D" | grep -v -x -F run.log)" ]; then
    refuse "$D holds an interrupted preparation (no identity/.complete); remove it and start again"
  fi
  prepare
else
  # A resume runs the run's own runner: the checkout may have moved on.
  cmp -s "$SELF" "$D/tree/benchmarks/t3/run_t3_accept.sh" \
    || refuse "this runner differs from the run's copy; use $D/tree/benchmarks/t3/run_t3_accept.sh"
fi

"$PY_BASE" -I "$D/tree/$IDTOOL" check --run "$D" --params "$PARAMS" 2>&1 | tee -a "$LOG"
rc="${PIPESTATUS[0]}"
[ "$rc" = 2 ] && refuse "inputs differ from identity/manifest.json (run.log); use a new run directory"
[ "$rc" = 0 ] || prep_fail "identity check failed (exit $rc)"
cmp -s "$D/lib/libbolorgir.so.0.1.0" "$LIB" || refuse "$LIB is not the library of this run"

cd "$D/tree" || prep_fail "cd tree"
export BLG_LIB_PATH="$D/lib/libbolorgir.so.0.1.0" \
  BLG_ENGINE_LABEL="t3-$(cut -c1-8 "$D/identity/lib.sha256")" \
  BOLORGIR_COVERAGE_PROFILE=spec-v1 PYTHONPATH=python HF_HOME="$D/hf-cache" HF_HUB_OFFLINE=1
"$D/venv/bin/python" - <<'EOF' 2>&1 | grep -v 'PyTorch was not found' | tee -a "$LOG"
import sys
sys.path.insert(0, "benchmarks")
import bench_jsb_coverage as b
for name, rev in (("openai-community/gpt2", b.TOKENIZER_REVISION),
                  ("unsloth/Meta-Llama-3.1-8B-Instruct", b.LLAMA_REVISION)):
    print("tokenizer", name, rev, len(b.hf_tokenizer_spec(name, rev).tokens))
EOF
[ "${PIPESTATUS[0]}" = 0 ] || refuse "HF tokenizer unavailable from the run's copy"
if [ "$PREP_ONLY" = 1 ]; then
  log "RUN_READY $(date -u +%FT%TZ)"
  exit 0
fi

echo $$ > "$D/run.pid"
log "RUN_START $(date -u +%FT%TZ) lib=$(cut -c1-16 "$D/identity/lib.sha256") jobs=$JOBS"
nice -n 19 "$D/venv/bin/python" -u benchmarks/bench_jsb_coverage.py --snapshot both --semantic \
  --generatable --passes 20 --hf-sample-every 20 --jobs "$JOBS" --chunk-size 100 \
  --work-dir "$D/chunks" --out-dir "$D/out" >> "$LOG" 2>&1
rc=$?
log "RUN_DONE exit=$rc $(date -u +%FT%TZ)"
exit "$rc"
