#!/bin/bash
# r6_publish_release.sh RUN LABEL REPORT...: the real R6 publication into
# this tree (the release worktree), after r6_publish_check.sh passed on a
# temporary set. The tree must be at the revision the run measured (its
# identity/git_head) with at most one change, the registered
# benchmarks/t3/oracle_lines.json (r6_register_line_oracle.py); the set
# benchmarks/coverage/spec-v1/LABEL must not exist. The package python/
# gets its own _core built against the run's library (ignored files;
# triage_semantic.py imports bolorgir.serializer), and the build must
# leave the tree's status as it was; then
#   r6_publish.sh RUN LABEL REPORT...
# and r6_publish_layout.sh checks the set against the run. Nothing is
# committed.
#
#   REPORT   the five candidate oracle reports, in tests/oracle/results/
# Env: R6_BUILD_PY (required: a python with the package build backend),
# PY (as in r6_oracle.sh), R6_LOG_DIR (default benchmarks/local/r6-publish-logs).
# Exit: the status of r6_publish.sh (0 admitted, 1 not admitted, 2
# unusable) when the layout holds, 4 when a layout check fails, 2 a
# refused setup.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
. "$ROOT/benchmarks/protected_paths.sh"
RUN="$(cd "${1:?run dir}" && pwd)" || exit 2
LABEL="${2:?label}"; shift 2
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
LOGS="${R6_LOG_DIR:-$ROOT/benchmarks/local/r6-publish-logs}"
O="$ROOT/benchmarks/coverage/spec-v1/$LABEL"
export PYTHONDONTWRITEBYTECODE=1
blg_memory_limit "${R6_MEM_LIMIT_MB:-16384}" || exit 2
cd "$ROOT" || exit 2
REV="$(git rev-parse HEAD)" || exit 2
[ "$(cat "$RUN/identity/git_head" 2>/dev/null)" = "$REV" ] \
  || { echo "refused: the run measured $(cat "$RUN/identity/git_head" 2>/dev/null), this tree is at $REV"; exit 2; }
dirty="$(git --no-optional-locks status --porcelain)"
case "$dirty" in
  ""|" M benchmarks/t3/oracle_lines.json") ;;
  *) echo "refused: this tree may differ from $REV only by the registered oracle_lines.json:"; echo "$dirty"; exit 2 ;;
esac
[ -e "$O" ] && { echo "refused: $O exists"; exit 2; }
reports=()
for f in "$@"; do
  [ -f "$f" ] && [ "$(realpath "$(dirname "$f")")" = "$(realpath tests/oracle/results)" ] \
    || { echo "refused: $f is not a report in tests/oracle/results"; exit 2; }
  reports+=("$(realpath -s "$f")")
done
mkdir -p "$LOGS" || exit 2
(cd python && BLG_SKIP_ZIG=1 BLG_CORE_LIB="$RUN/lib" "$BUILD_PY" setup.py build_ext --inplace) \
  > "$LOGS/$LABEL-build_ext.log" 2>&1 || { echo "build_ext failed"; tail "$LOGS/$LABEL-build_ext.log"; exit 2; }
cmp python/bolorgir/_lib/libbolorgir.so.0.1.0 "$LIB" \
  || { echo "refused: the package of this tree is not on the run's library"; exit 2; }
[ "$(git --no-optional-locks status --porcelain)" = "$dirty" ] \
  || { echo "refused: build_ext left tracked or unignored changes"; git status --porcelain; exit 2; }
PY="$PY" bash benchmarks/t3/r6_publish.sh "$RUN" "$LABEL" "${reports[@]}" > "$LOGS/$LABEL-publish.log" 2>&1
rc=$?
tail -5 "$LOGS/$LABEL-publish.log"
echo "r6_publish exit $rc"
bash benchmarks/t3/r6_publish_layout.sh "$RUN" "$O" "$REV" || exit 4
echo "== real publication in $O; verdict $("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["acceptance"]["verdict"])' "$O/review/acceptance.json")"
exit "$rc"
