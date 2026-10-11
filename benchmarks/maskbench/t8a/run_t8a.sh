#!/bin/bash
# T8a: full MaskBench run of Bolorgir on spec-v1 and the paired llguidance and
# XGrammar runs on the same token streams, the MaskBench pins and the current
# releases (T8a). One run at a time.
#
# Usage: run_t8a.sh [--smoke N] <work dir> <bolorgir wheel> [threads]
#   --smoke N         only the first N corpus files (sorted names): the
#                     smoke of the five variants; its report is never an
#                     acceptance
#   <work dir>        new, or a directory of an earlier run with the same
#                     inputs (resume); outside the protected paths
#   <bolorgir wheel>  the wheel of the candidate; the same file is installed
#                     into both venvs
#   [threads]         MaskBench runner processes, default 20 as in the
#                     published runs (benchmarks/maskbench/README.md);
#                     each may take up to the 40 GB MaskBench limit, so a
#                     machine with less memory than threads x 40 GB starts
#                     with fewer (one); the embedded smoke uses the same
#
# Environment:
#   HF_HOME           default benchmarks/local/hf-cache; must hold the pinned
#                     Llama 3.1 snapshot (LLAMA_REVISION) with exactly the
#                     files and sha256 of benchmarks/t3/acceptance_pins.json
#                     (t8a_preflight.py); the run is offline. A worktree has
#                     no cache of its own: set it to the main tree's
#   LLAMA_REVISION    default a2856192dd7c25b842431f39c179a6c2c2f627d1 (the
#                     revision of the T2b/T3 coverage runs)
#   CURRENT_LLG, CURRENT_XGR
#                     versions of the "current" engines (default: newest on
#                     PyPI when the venv is created; recorded)
#   T8A_ACCEPTANCE    the admitted acceptance.json of the T3 review of this
#                     core (benchmarks/t3/r6_publish.sh; after the owner's
#                     D4 decisions the later, admitted review directory);
#                     recorded in the manifest. A full run refuses to start
#                     without it, or when it is not admitted or names
#                     another core than the wheel's (t8a_preflight.py)
#   T8A_T2C           T2c classes of the Bolorgir errors (t8a_report.py --t2c)
#   PY_BASE           python3 used to create the venvs (default python3);
#                     nothing is installed into it
#   T8A_MEM_LIMIT_MB  address-space limit of every process of the run
#                     (blg_memory_limit in benchmarks/protected_paths.sh;
#                     0 = none); default and minimum 40960, the 40 GB the
#                     MaskBench runner sets on its own processes
#   T8A_VENV_ROOT     directory of the two venvs (default <work dir>);
#                     outside the protected paths
#
# The venvs are created at their final paths and never moved: a venv
# keeps absolute paths in its scripts and pyvenv.cfg. pip
# runs as "<venv>/bin/python -m pip". A venv is used only after its
# readiness marker .complete exists, written (temporary file, rename)
# after every install and the import check; a venv directory without it
# is an interrupted install and the run stops. A completed venv is
# checked with the same imports when it is reused.
#
# A full run first runs the smoke (--smoke 20) in <work dir>/smoke with the
# same venvs and the same [threads], and stops unless the smoke passed: all five participants
# finished every smoke file without a crash or timeout, with complete
# records equal to the reference streams (t8a_report.py, t8a.smoke). The
# work dir keeps an identity manifest (identity/manifest.json,
# t8a_identity.py) with the five participants and their versions; a
# resume whose inputs differ in any recorded field aborts and names the
# field.
# Exit status: full run - 0 when every run finished and the report says
# accepted, 5 when the report is written but not accepted (reasons in
# report/t8a.md); smoke - 0 when the smoke passed; 1 when a step failed,
# the smoke did not pass or inputs changed.
set -u -o pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
. "$ROOT/benchmarks/protected_paths.sh"
SMOKE=0
if [ "${1:-}" = "--smoke" ]; then SMOKE="${2:?--smoke N}"; shift 2; fi
W="${1:?work dir}"; WHEEL="${2:?bolorgir wheel}"; THREADS="${3:-20}"
blg_guard_out "$ROOT" "$W" || exit 1
mkdir -p "$W" && W="$(cd "$W" && pwd)"
WHEEL="$(cd "$(dirname "$WHEEL")" && pwd)/$(basename "$WHEEL")"
MB="$ROOT/benchmarks/external/jsonschemabench-maskbench/maskbench"
T8A="$ROOT/benchmarks/maskbench/t8a"
LOG="$W/run.log"
PY_BASE="${PY_BASE:-python3}"
LLAMA_REVISION="${LLAMA_REVISION:-a2856192dd7c25b842431f39c179a6c2c2f627d1}"
export HF_HOME="${HF_HOME:-$ROOT/benchmarks/local/hf-cache}" HF_HUB_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1
TOKDIR="$HF_HOME/hub/models--unsloth--Meta-Llama-3.1-8B-Instruct/snapshots/$LLAMA_REVISION"
VENVS="${T8A_VENV_ROOT:-$W}"
blg_guard_out "$ROOT" "$VENVS" || exit 1
mkdir -p "$VENVS" && VENVS="$(cd "$VENVS" && pwd)"
log() { echo "$*" | tee -a "$LOG"; }
fail() { log "T8A_ABORT $*"; exit 1; }
# The MaskBench runner sets the address-space limit of its processes to
# its --mem-limit (MASKBENCH_MEM_LIMIT_GB, the default the manifest records
# as mem_limit), soft and hard, and cannot raise a hard limit set here: a
# lower limit fails every chunk with "not allowed to raise maximum limit",
# so it is refused before any install.
MASKBENCH_MEM_LIMIT_GB=40
T8A_MEM_LIMIT_MB="${T8A_MEM_LIMIT_MB:-$((MASKBENCH_MEM_LIMIT_GB * 1024))}"
blg_memory_limit "$T8A_MEM_LIMIT_MB" || fail "memory limit"
[ "$T8A_MEM_LIMIT_MB" = 0 ] || [ "$T8A_MEM_LIMIT_MB" -ge $((MASKBENCH_MEM_LIMIT_GB * 1024)) ] \
  || fail "memory limit: T8A_MEM_LIMIT_MB=$T8A_MEM_LIMIT_MB is below the MaskBench limit of ${MASKBENCH_MEM_LIMIT_GB} GB, which its runner sets itself"
log "T8A_LIMIT address space per process: $(ulimit -v) kB"

[ -f "$MB/maskbench/blg_engine.py" ] || fail "MaskBench checkout with the Bolorgir patch missing at $MB"
[ "$(git -C "$MB" rev-parse HEAD)" = "ba103c73756198dd9b149ddc7db7867da7a077f6" ] \
  || fail "MaskBench checkout is not at ba103c7"
[ -f "$WHEEL" ] || fail "wheel $WHEEL missing"
[ -f "$TOKDIR/tokenizer.json" ] && [ -f "$TOKDIR/config.json" ] \
  || fail "pinned tokenizer snapshot $TOKDIR missing (tokenizer.json, config.json)"
# Before any install: the snapshot of the acceptance pins and, for a full
# run, the admitted T3 verdict of the wheel's core (system python3, stdlib).
pre=(--wheel "$WHEEL" --tokenizer-dir "$TOKDIR" --tokenizer-revision "$LLAMA_REVISION"
  --pins "$ROOT/benchmarks/t3/acceptance_pins.json" --acceptance "${T8A_ACCEPTANCE:-}")
[ "$SMOKE" = 0 ] && pre+=(--full)
python3 "$T8A/t8a_preflight.py" "${pre[@]}" 2>&1 | tee -a "$LOG"
[ "${PIPESTATUS[0]}" = 0 ] || fail "preflight: inputs refused before any install (see above)"

# --- venvs: built in place, usable only after the readiness marker ---------
# make_venv NAME ENGINE_PKGS... : creates <VENVS>/venv-NAME at its final
# path, installs into it, checks the imports, then writes .complete.
# CONSTRAINTS (a file) pins the shared packages to the versions of the
# first venv.
IMPORTS="import bolorgir, bolorgir.serializer, transformers, tokenizers, llguidance, xgrammar"
make_venv() {
  local name="$1"; shift
  local v="$VENVS/venv-$name"
  if [ -f "$v/.complete" ]; then
    "$v/bin/python" -c "$IMPORTS" || fail "$v: the imports fail in a completed venv"
    return 0
  fi
  [ -e "$v" ] && fail "$v exists without .complete (an interrupted install); remove it"
  "$PY_BASE" -m venv "$v" || return 1
  local py="$v/bin/python" c=()
  [ -n "${CONSTRAINTS:-}" ] && c=(-c "$CONSTRAINTS")
  "$py" -m pip install --disable-pip-version-check --upgrade "pip>=24" || return 1
  "$py" -m pip install --disable-pip-version-check "${c[@]}" torch \
    --index-url https://download.pytorch.org/whl/cpu || return 1
  "$py" -m pip install --disable-pip-version-check "${c[@]}" transformers huggingface_hub \
    tokenizers numpy "$WHEEL" "$@" || return 1
  "$py" -c "$IMPORTS" || return 1
  "$py" -m pip freeze > "$v/.freeze" || return 1
  { date -u +%FT%TZ; sha256sum "$WHEEL"; cat "$v/.freeze"; } > "$v/.complete.tmp" &&
    mv -f "$v/.complete.tmp" "$v/.complete"
}

manifest_args() {
  local files_args=()
  [ -f "$W/files.txt" ] && files_args=(--files-list "$W/files.txt")
  printf '%s\0' --wheel "$WHEEL" --venv "$VENVS/venv-pinned" --venv "$VENVS/venv-current" \
    --maskbench "$MB" --data "$MB/data" "${files_args[@]}" --tokenizer-dir "$TOKDIR" \
    --tokenizer-revision "$LLAMA_REVISION" --threads "$THREADS" --smoke-files "$SMOKE"
  [ -n "${T8A_ACCEPTANCE:-}" ] && printf '%s\0' --acceptance "$T8A_ACCEPTANCE"
  return 0
}

if [ ! -f "$W/identity/manifest.json" ]; then
  log "T8A_INIT $(date -u +%FT%TZ) smoke=$SMOKE"
  if [ "$SMOKE" != 0 ]; then
    (cd "$MB/data" && ls -1 -- *.json | LC_ALL=C sort | head -n "$SMOKE") > "$W/files.txt"
    [ "$(wc -l < "$W/files.txt")" = "$SMOKE" ] || fail "fewer than $SMOKE corpus files"
  fi
  make_venv pinned llguidance==0.7.10 xgrammar==0.1.17 || fail "pinned venv"
  # The shared packages of the current venv are those of the pinned one;
  # bolorgir is the same wheel file in both.
  if [ ! -f "$VENVS/venv-current/.complete" ]; then
    "$VENVS/venv-pinned/bin/python" -m pip freeze \
      | grep -i -E '^(transformers|tokenizers|huggingface[-_]hub|numpy|torch)==' \
      > "$VENVS/shared-constraints.txt" || fail "shared versions"
  fi
  CONSTRAINTS="$VENVS/shared-constraints.txt" make_venv current \
    "llguidance${CURRENT_LLG:+==$CURRENT_LLG}" "xgrammar${CURRENT_XGR:+==$CURRENT_XGR}" \
    || fail "current venv (shared versions: $VENVS/shared-constraints.txt)"
  mkdir -p "$W/identity"
  mapfile -d '' margs < <(manifest_args)
  "$VENVS/venv-pinned/bin/python" "$T8A/t8a_identity.py" record --out "$W/identity/manifest.json" \
    "${margs[@]}" 2>&1 | tee -a "$LOG"
  if [ "${PIPESTATUS[0]}" != 0 ]; then
    mv "$W/identity/manifest.json" "$W/identity/manifest.rejected.json" 2>/dev/null
    fail "inconsistent inputs"
  fi
  { echo "# packages that differ between the venvs (the compared engines and their deps)";
    diff <("$VENVS/venv-pinned/bin/python" -m pip freeze) \
      <("$VENVS/venv-current/bin/python" -m pip freeze); } \
    > "$W/identity/deps_diff.txt"
  log "T8A_STREAMS $(date -u +%FT%TZ)"
  files_args=()
  [ -f "$W/files.txt" ] && files_args=(--files-list "$W/files.txt")
  "$VENVS/venv-pinned/bin/python" "$T8A/t8a_streams.py" --data "$MB/data" "${files_args[@]}" \
    --tokenizer "$TOKDIR" --out "$W/identity/streams.json.gz" >> "$LOG" 2>&1 || fail "reference streams"
fi

mapfile -d '' margs < <(manifest_args)
"$VENVS/venv-pinned/bin/python" "$T8A/t8a_identity.py" check --manifest "$W/identity/manifest.json" \
  "${margs[@]}" >> "$LOG" 2>&1 || fail "inputs differ from identity/manifest.json (see $LOG); use a new work dir"
log "T8A_IDENTITY_OK $(date -u +%FT%TZ)"

if [ "$SMOKE" = 0 ] && [ ! -f "$W/smoke/.passed" ]; then
  log "T8A_SMOKE_START threads=$THREADS"
  T8A_ACCEPTANCE= T8A_T2C= T8A_VENV_ROOT="$VENVS" bash "$0" --smoke 20 "$W/smoke" "$WHEEL" "$THREADS" \
    >> "$LOG" 2>&1 || fail "smoke failed (see $W/smoke/run.log and $W/smoke/report/t8a.md)"
  touch "$W/smoke/.passed"
  log "T8A_SMOKE_PASSED"
fi

DATA_ARGS=(data/)
[ -f "$W/files.txt" ] && mapfile -t DATA_ARGS < <(sed 's|^|data/|' "$W/files.txt")

run_engine() {
  local name="$1" venv="$2" profile="$3"; shift 3
  local out="$W/out/$name"
  if [ -f "$out/.done" ]; then log "T8A_SKIP $name (done)"; return 0; fi
  mkdir -p "$out"
  log "T8A_START $name $(date -u +%FT%TZ)"
  (cd "$MB" && env PYTHONPATH="$T8A:$MB" T8A_SERIALIZER=bolorgir \
    BOLORGIR_PROFILE="$profile" BOLORGIR_SERIALIZER=1 nice -n 19 \
    "$VENVS/venv-$venv/bin/python" -u "$T8A/paired_driver.py" "$@" --compact \
    --tokenizer "$TOKDIR" --num-threads "$THREADS" --output "$out" "${DATA_ARGS[@]}") \
    >> "$W/$name.log" 2>&1
  local rc=$?
  log "T8A_DONE $name exit=$rc $(date -u +%FT%TZ)"
  [ "$rc" = 0 ] || fail "$name failed (exit $rc; 3 preflight, 4 stopped chunks; see $W/$name.log)"
  touch "$out/.done"
}

run_engine blg-spec-v1 pinned spec-v1 --blg
run_engine llg-pinned pinned - --llg
run_engine xgr-pinned pinned - --xgr
run_engine llg-current current - --llg
run_engine xgr-current current - --xgr

mkdir -p "$W/report"
t2c_args=()
[ -n "${T8A_T2C:-}" ] && t2c_args=(--t2c "$T8A_T2C")
"$VENVS/venv-pinned/bin/python" "$T8A/t8a_report.py" --bolorgir blg-spec-v1 \
  --manifest "$W/identity/manifest.json" --streams "$W/identity/streams.json.gz" \
  --run "blg-spec-v1=$W/out/blg-spec-v1" --run "llg-pinned=$W/out/llg-pinned" \
  --run "xgr-pinned=$W/out/xgr-pinned" --run "llg-current=$W/out/llg-current" \
  --run "xgr-current=$W/out/xgr-current" "${t2c_args[@]}" \
  --out-md "$W/report/t8a.md" --out-json "$W/report/t8a.json" | tee -a "$LOG"
rc="${PIPESTATUS[0]}"
[ "$rc" -le 1 ] || fail "report failed (exit $rc)"
if [ "$SMOKE" != 0 ]; then
  "$VENVS/venv-pinned/bin/python" -c 'import json, sys
smoke = json.load(open(sys.argv[1]))["t8a"]["smoke"]
sys.exit(0 if smoke and smoke["passed"] else 1)' "$W/report/t8a.json" \
    || fail "smoke did not pass: a crash or timeout, an incomplete record, a stream difference or a participant mismatch (report/t8a.md)"
  log "T8A_SMOKE_DONE passed $(date -u +%FT%TZ)"
  exit 0
fi
case "$rc" in
  0) log "T8A_RUN_DONE accepted $(date -u +%FT%TZ)"; exit 0 ;;
  *) log "T8A_RUN_DONE not_accepted $(date -u +%FT%TZ)"; exit 5 ;;
esac
