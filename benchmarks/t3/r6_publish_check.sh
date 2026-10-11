#!/bin/bash
# r6_publish_check.sh RUN LABEL WORK REPORT...: r6_publish.sh tried on a
# temporary set before the real publication into this tree
# (r6_publish_release.sh). WORK/tree is a copy of this working tree
# (tracked and untracked files, ignored ones excluded - so the registered
# oracle_lines.json is the one the real set will use); the ignored inputs
# are links inside real directories (benchmarks/external/*, each file of
# tests/oracle/results/, benchmarks/local/libs); the copy's package gets
# its own _core built against the run's library (R6_BUILD_PY: a python
# with the build backend of python/pyproject.toml; triage_semantic.py
# imports bolorgir.serializer). Then
#   r6_publish.sh RUN LABEL <the reports in the copy>
# from the copy - the set lands in WORK/tree/benchmarks/coverage/spec-v1/
# LABEL, never in a versioned tree - and r6_publish_layout.sh checks it
# against the run.
#
#   RUN      run directory of run_t3_accept.sh (RUN_DONE exit=0); its
#            identity/git_head must be the HEAD of this tree
#   LABEL    set name, e.g. t3-6e62e0d1
#   WORK     new or empty directory outside the protected paths
#   REPORT   the five candidate oracle reports, in tests/oracle/results/
#            of this tree
# Env: R6_BUILD_PY (required), PY (as in r6_oracle.sh).
# Exit: the status of r6_publish.sh (0 admitted, 1 not admitted, 2
# unusable) when the layout holds, 4 when a layout check fails, 2 a
# refused setup.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
. "$ROOT/benchmarks/protected_paths.sh"
RUN="$(cd "${1:?run dir}" && pwd)" || exit 2
LABEL="${2:?label}"; WORK="${3:?work dir}"; shift 3
[ "$#" = 5 ] || { echo "refused: expected the five candidate oracle reports, got $#"; exit 2; }
MAIN="$(dirname "$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)")"
PY="${PY:-}"
[ -n "$PY" ] || for p in "$ROOT/.venv/bin/python" "$MAIN/.venv/bin/python"; do
  [ -x "$p" ] && { PY="$p"; break; }
done
[ -x "$PY" ] || { echo "refused: no python (set PY)"; exit 2; }
BUILD_PY="${R6_BUILD_PY:-}"
[ -x "$BUILD_PY" ] || { echo "refused: R6_BUILD_PY (python with the package build backend) not given"; exit 2; }
LIB="$RUN/lib/libbolorgir.so.0.1.0"
[ -f "$LIB" ] || { echo "refused: $LIB missing"; exit 2; }
REV="$(git -C "$ROOT" rev-parse HEAD)" || exit 2
[ "$(cat "$RUN/identity/git_head" 2>/dev/null)" = "$REV" ] \
  || { echo "refused: the run measured $(cat "$RUN/identity/git_head" 2>/dev/null), this tree is at $REV"; exit 2; }
RES="$(realpath "$ROOT/tests/oracle/results")"
names=()
for f in "$@"; do
  [ -f "$f" ] && [ "$(realpath "$(dirname "$f")")" = "$RES" ] \
    || { echo "refused: $f is not a report in $ROOT/tests/oracle/results"; exit 2; }
  names+=("$(basename "$f")")
done
blg_guard_new_dir "$ROOT" "$WORK" || exit 2
export PYTHONDONTWRITEBYTECODE=1
blg_memory_limit "${R6_MEM_LIMIT_MB:-16384}" || exit 2
mkdir -p "$WORK/tree" && WORK="$(cd "$WORK" && pwd)" || exit 2
T="$WORK/tree"
git -C "$ROOT" ls-files -z -c -o --exclude-standard \
  | (cd "$ROOT" && tar --null -T - -cf -) | tar -xf - -C "$T" || { echo "copy failed"; exit 2; }
git -C "$ROOT" --no-optional-locks status --porcelain > "$WORK/tree.status"
echo "copy of $ROOT at $REV; changes: $(wc -l < "$WORK/tree.status") ($(tr '\n' ';' < "$WORK/tree.status"))"
mkdir -p "$T/benchmarks/external" "$T/tests/oracle/results" "$T/benchmarks/local" || exit 2
for c in "$ROOT"/benchmarks/external/*; do
  [ -e "$c" ] && { ln -s "$(realpath "$c")" "$T/benchmarks/external/$(basename "$c")" || exit 2; }
done
for f in "$ROOT"/tests/oracle/results/*; do
  ln -s "$(realpath "$f")" "$T/tests/oracle/results/$(basename "$f")" || exit 2
done
[ -e "$ROOT/benchmarks/local/libs" ] && { ln -s "$(realpath "$ROOT/benchmarks/local/libs")" "$T/benchmarks/local/libs" || exit 2; }
(cd "$T/python" && BLG_SKIP_ZIG=1 BLG_CORE_LIB="$RUN/lib" "$BUILD_PY" setup.py build_ext --inplace) \
  > "$WORK/build_ext.log" 2>&1 || { echo "build_ext failed"; tail "$WORK/build_ext.log"; exit 2; }
cmp "$T/python/bolorgir/_lib/libbolorgir.so.0.1.0" "$LIB" \
  || { echo "refused: the copy's package is not on the run's library"; exit 2; }
reports=()
for n in "${names[@]}"; do reports+=("$T/tests/oracle/results/$n"); done
(cd "$T" && PY="$PY" bash benchmarks/t3/r6_publish.sh "$RUN" "$LABEL" "${reports[@]}") > "$WORK/publish.log" 2>&1
rc=$?
tail -5 "$WORK/publish.log"
echo "r6_publish exit $rc"
O="$T/benchmarks/coverage/spec-v1/$LABEL"
bash "$ROOT/benchmarks/t3/r6_publish_layout.sh" "$RUN" "$O" "$REV"
lrc=$?
(cd "$O" 2>/dev/null && find . -type f | LC_ALL=C sort) > "$WORK/published.files"
echo "published files: $(wc -l < "$WORK/published.files")"
[ "$lrc" = 0 ] || exit 4
echo "== layout ok; verdict $("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["acceptance"]["verdict"])' "$O/review/acceptance.json")"
exit "$rc"
