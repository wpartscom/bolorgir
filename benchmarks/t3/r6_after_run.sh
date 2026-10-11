#!/bin/bash
# r6_after_run.sh RUN WORK LINE LINE_LIB LINE_SHA256: the agreed order
# after an R6 run (run_t3_accept.sh), from the release worktree that the
# run measured, one step at a time, stopping at the first unexpected
# status:
#   1. r6_oracle.sh                the five dialects of the candidate
#                                  (RUN/lib, identity/lib.sha256), then of
#                                  the reference line LINE (LINE_LIB)
#   2. r6_register_line_oracle.py  the line's reports into
#                                  oracle_lines.json of this tree and of
#                                  the main tree
#   3. r6_publish_check.sh         r6_publish.sh on a temporary set in WORK
#                                  and its layout check
#   4. r6_publish_release.sh       the real set in this tree and its layout
#                                  check; then r6_compare_sets.py: the
#                                  real set says the same as the temporary
#                                  one, file by file, with only the tree
#                                  root of each replaced
# The candidate's label is t3-<first 8 hex of its sha256>; the line's
# reports are oracle-line-LINE-<8 hex>-<dialect>-spec.json.
#
# A review exit 1 (not admitted) passes as an intermediate result only
# when the failing checks are among these three, none pending:
#   transitions_described  a transition without a D4 entry: its analysis
#                          is missing (the executor's task, from the
#                          transitions this review reports);
#   d4_candidate           the D4 list is bound to another candidate's
#                          library (the executor rebinds it once every
#                          entry is described);
#   transitions_approved   no owner decision for this candidate (asked for
#                          with concrete proposals after the two above are
#                          closed and the review repeated).
# Any other failing or pending check stops the chain. Old decisions are
# never carried over. Nothing is committed. Run it under a memory guard.
#
# Env: R6_BUILD_PY (required by steps 3 and 4), PY (as in r6_oracle.sh),
# R6_LINE_NOTE (text added to the line's identity note, e.g. the owner
# decision that asked for the run), R6_AFTER_TOOLS (tests only: the
# directory of the four step commands).
# Exit: 0 every step done (verdict printed), 10+step the step that
# stopped the chain, 2 unusable arguments.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="$(cd "${1:?run dir}" && pwd)" || exit 2
WORK="${2:?work dir for the temporary set}"
LINE="${3:?line name}"; LINE_LIB="${4:?line library}"; LINE_SHA="${5:?line library sha256}"
TOOLS="${R6_AFTER_TOOLS:-$ROOT/benchmarks/t3}"
MAIN="$(dirname "$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)")"
PY="${PY:-}"
[ -n "$PY" ] || for p in "$ROOT/.venv/bin/python" "$MAIN/.venv/bin/python"; do
  [ -x "$p" ] && { PY="$p"; break; }
done
[ -x "$PY" ] || { echo "refused: no python (set PY)"; exit 2; }
export PY PYTHONDONTWRITEBYTECODE=1
EXPECTED="transitions_described transitions_approved d4_candidate"
say() { echo "== R6_AFTER $* $(date -u +%FT%TZ)"; }
stop() { say "STOP step $1: $2"; exit $((10 + $1)); }

last="$(grep '^RUN_DONE ' "$RUN/run.log" 2>/dev/null | tail -n 1)"
case "$last" in "RUN_DONE exit=0 "*) ;; *) stop 0 "the run did not finish with exit 0: '${last:-no RUN_DONE}'" ;; esac
CAND_LIB="$RUN/lib/libbolorgir.so.0.1.0"
CAND_SHA="$(cut -d' ' -f1 "$RUN/identity/lib.sha256" 2>/dev/null)"
[ "${#CAND_SHA}" = 64 ] || stop 0 "no library sha256 in $RUN/identity/lib.sha256"
LABEL="t3-${CAND_SHA:0:8}"
LTAG="line-$LINE-${LINE_SHA:0:8}"
SET="benchmarks/coverage/spec-v1/$LABEL"
DIALECTS="draft4 draft6 draft7 draft2019-09 draft2020-12"
say "run finished: $last; candidate $LABEL, line $LTAG"

# review_status FILE: the verdict and the failing/pending checks; exit 0
# when admitted, or not admitted for the expected reasons only.
review_status() {
  "$PY" - "$1" "$EXPECTED" <<'EOF'
import json, sys
a = json.load(open(sys.argv[1], encoding="utf-8"))
acc, expected = a["acceptance"], set(sys.argv[2].split())
print(f"verdict {acc['verdict']} exit {acc['exit_status']}")
print(f"failing: {acc['failing']}")
print(f"pending: {acc['pending']}")
print(f"transitions: {len(a['transitions'])}, rows without approval: {len(a['d4_pending_rows'])}")
if acc["verdict"] == "admitted":
    sys.exit(0)
other = sorted(set(acc["failing"]) - expected) + list(acc["pending"])
if acc["verdict"] != "not_admitted" or other:
    print(f"unexpected: {other or acc['verdict']}")
    sys.exit(1)
sys.exit(0)
EOF
}

say "step 1 oracle"
bash "$TOOLS/r6_oracle.sh" "$LABEL" "$CAND_LIB" "$CAND_SHA" "$LTAG" "$LINE_LIB" "$LINE_SHA" \
  || stop 1 "r6_oracle.sh exit $?"
say "step 2 registration"
trees=("$ROOT"); [ "$(realpath "$MAIN")" = "$(realpath "$ROOT")" ] || trees+=("$MAIN")
note="Oracle run of the reference library $LINE_LIB (sha256 ${LINE_SHA:0:8}...) with the tools of $(git -C "$ROOT" rev-parse --short HEAD) (tests/oracle/run_oracle.py, the same suite snapshot, validator and profile as the candidate $LABEL); each report header names library_sha256 ${LINE_SHA:0:8}...${R6_LINE_NOTE:+ $R6_LINE_NOTE}"
"$PY" "$TOOLS/r6_register_line_oracle.py" --line "$LINE" --library-sha256 "$LINE_SHA" --source "$ROOT" \
  --prefix "oracle-$LTAG" --identity "$note" "${trees[@]}" || stop 2 "r6_register_line_oracle.py exit $?"
reports=()
for d in $DIALECTS; do reports+=("$ROOT/tests/oracle/results/oracle-$LABEL-$d-spec.json"); done
say "step 3 temporary set"
bash "$TOOLS/r6_publish_check.sh" "$RUN" "$LABEL" "$WORK" "${reports[@]}"; rc=$?
case "$rc" in 0|1) ;; *) stop 3 "r6_publish_check.sh exit $rc (4 layout, 2 refused or unusable)" ;; esac
WORK="$(cd "$WORK" && pwd)"
review_status "$WORK/tree/$SET/review/acceptance.json" || stop 3 "temporary review: unexpected checks"
say "step 4 real set"
bash "$TOOLS/r6_publish_release.sh" "$RUN" "$LABEL" "${reports[@]}"; rc=$?
case "$rc" in 0|1) ;; *) stop 4 "r6_publish_release.sh exit $rc (4 layout, 2 refused or unusable)" ;; esac
review_status "$ROOT/$SET/review/acceptance.json" || stop 4 "real review: unexpected checks"
"$PY" "$ROOT/benchmarks/t3/r6_compare_sets.py" "$WORK/tree/$SET" "$ROOT/$SET" \
  --a-root "$WORK/tree" --b-root "$ROOT" || stop 4 "the temporary and the real set differ"
say "DONE"
exit 0
