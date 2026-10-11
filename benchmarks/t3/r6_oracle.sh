#!/bin/bash
# The oracle of an R6 acceptance: tests/oracle/run_oracle.py
# of this tree, profile spec-v1, the five dialects, one process at a time,
# for each build in the order given - the candidate first, then the
# reference lines without oracle data (benchmarks/t3/oracle_lines.json).
#
# Usage: r6_oracle.sh [--check-only] TAG LIB SHA256 [TAG LIB SHA256 ...]
#   TAG     report name part: tests/oracle/results/oracle-TAG-<dialect>-spec.json
#           (t3-6e62e0d1 for the candidate, line-dbc5eeb-8ff970d4 for a line)
#   LIB     the core library (a path may hold spaces: each is one argument)
#   SHA256  its expected sha256; another file is refused
#   --check-only  the setup checks, then exit without an oracle run
#
# The tree must be clean (git status --porcelain empty): the reports come
# from its committed tools (tests/oracle/run_oracle.py, tests/blg_ctypes.py,
# the serializer). The suite snapshot tests/oracle/external/
# JSON-Schema-Test-Suite and tests/oracle/results/ are ignored inputs (in
# a worktree: links to the main tree's). A report that exists is never
# overwritten. Each report header must name its library_sha256 and
# spec-v1, and each report must be evidence about that library
# (oracle_content_problems of this tree's tools/acceptance_compare.py:
# rows, compiled schemas, engine verdicts, no compile refusal of the call
# itself, a summary that counts the rows); the first one that is not
# stops the launch, the report kept for the record.
#
# Env: PY (the validator's python, read-only use; default .venv/bin/python
# of this tree, else of the main tree of this git repository),
# R6_ORACLE_TIMEOUT (7200 s per dialect), R6_MEM_LIMIT_MB (16384,
# blg_memory_limit), R6_LOG_DIR (default benchmarks/local/r6-oracle-logs).
# Exit: 0 every report written, the status of the first failing run, 1 a
# report header that does not name its library or a report that is no
# evidence, 2 a refused setup.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
. "$ROOT/benchmarks/protected_paths.sh"
CHECK_ONLY=0
if [ "${1:-}" = "--check-only" ]; then CHECK_ONLY=1; shift; fi
[ "$#" -gt 0 ] && [ $(($# % 3)) = 0 ] || { echo "usage: r6_oracle.sh [--check-only] TAG LIB SHA256 ..." >&2; exit 2; }
MAIN="$(dirname "$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)")"
PY="${PY:-}"
[ -n "$PY" ] || for p in "$ROOT/.venv/bin/python" "$MAIN/.venv/bin/python"; do
  [ -x "$p" ] && { PY="$p"; break; }
done
[ -x "$PY" ] || { echo "refused: no python (set PY)"; exit 2; }
LOGS="${R6_LOG_DIR:-$ROOT/benchmarks/local/r6-oracle-logs}"
DIALECTS="draft4 draft6 draft7 draft2019-09 draft2020-12"
blg_memory_limit "${R6_MEM_LIMIT_MB:-16384}" || exit 2
export PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH
cd "$ROOT" || exit 2
head="$(git rev-parse HEAD)" || exit 2
[ -z "$(git --no-optional-locks status --porcelain)" ] || { echo "refused: $ROOT is not clean"; git status --short | head; exit 2; }
echo "tree $ROOT at $head; python $PY"
check_lib() {  # path sha256: separate arguments (the paths hold spaces)
  local got
  got="$(sha256sum -- "$1" 2>/dev/null | cut -d' ' -f1)"
  [ -n "$got" ] || { echo "refused: cannot read $1"; exit 2; }
  [ "$got" = "$2" ] || { echo "refused: $1 is $got, not $2"; exit 2; }
  echo "library ok: $1 $2"
}
args=("$@")
for ((i = 0; i < ${#args[@]}; i += 3)); do
  case "${args[i]}" in ''|*/*|*' '*) echo "refused: tag '${args[i]}'"; exit 2 ;; esac
  check_lib "${args[i + 1]}" "${args[i + 2]}"
  for d in $DIALECTS; do
    out="tests/oracle/results/oracle-${args[i]}-$d-spec.json"
    [ -e "$out" ] && { echo "refused: $out exists"; exit 2; }
  done
done
[ -d tests/oracle/external/JSON-Schema-Test-Suite ] || { echo "refused: no suite snapshot in $ROOT"; exit 2; }
[ -d tests/oracle/results ] || { echo "refused: no tests/oracle/results in $ROOT"; exit 2; }
if [ "$CHECK_ONLY" = 1 ]; then echo "== r6_oracle setup checks passed (--check-only, no oracle run)"; exit 0; fi
mkdir -p "$LOGS" || exit 2
run_one() {  # tag lib sha dialect
  local tag="$1" lib="$2" want="$3" d="$4"
  local out="tests/oracle/results/oracle-$tag-$d-spec.json"
  [ -e "$out" ] && { echo "refused: $out exists"; exit 2; }
  echo "== $tag $d start $(date -u +%FT%TZ)"
  BLG_LIB_PATH="$lib" BLG_TEST_BACKEND=ctypes timeout "${R6_ORACLE_TIMEOUT:-7200}" "$PY" tests/oracle/run_oracle.py \
    --profile spec-v1 --drafts "$d" --json "$out" --md "${out%.json}.md" \
    > "$LOGS/oracle-$tag-$d.log" 2>&1
  local rc=$?
  echo "== $tag $d exit $rc $(date -u +%FT%TZ)"
  grep -E '^\| (PASS|SKIPPED|ENGINE_ERROR|MISMATCH|KNOWN_GAP|SERIALIZATION)' "${out%.json}.md" 2>/dev/null | tr '\n' ' '
  echo
  [ "$rc" = 0 ] || { tail -20 "$LOGS/oracle-$tag-$d.log"; exit "$rc"; }
  "$PY" - "$out" "$want" "$d" "$ROOT/benchmarks/t3/tools/acceptance_compare.py" <<'EOF' || exit 1
import importlib.util, json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
h = doc["header"]
print("   header library_sha256", h.get("library_sha256"), "profile", h["profile"], "drafts", h["drafts"])
spec = importlib.util.spec_from_file_location("acceptance_compare", sys.argv[4])
ac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ac)
problems = ac.oracle_content_problems(doc)
for p in problems:
    print("   NO EVIDENCE:", p)
sys.exit(0 if h.get("library_sha256") == sys.argv[2] and h["profile"] == "spec-v1"
         and h["drafts"] == [sys.argv[3]] and not problems else 1)
EOF
  sha256sum "$out"
}
for ((i = 0; i < ${#args[@]}; i += 3)); do
  for d in $DIALECTS; do run_one "${args[i]}" "${args[i + 1]}" "${args[i + 2]}" "$d"; done
done
echo "== r6_oracle done"
