#!/bin/bash
# Publish a finished T3 acceptance run (run_t3_accept.sh) into the
# versioned reports and review it: the reports, sidecars and identity
# record go to benchmarks/coverage/spec-v1/<label>/, then r6_review.sh
# writes the T2c triage, the coverage control with the acceptance verdict
# and the evidence bundle into <label>/review/. Nothing is generated again;
# the corpora must be present for the triage.
#
# Usage: r6_publish.sh <run dir> <label> <oracle report> ...
#   <label>          directory name under benchmarks/coverage/spec-v1/,
#                    e.g. t3-50cbd80e; an existing directory is refused
#   <oracle report>  tests/oracle/run_oracle.py results of the candidate,
#                    spec-v1, all five dialects
#
# A published directory is a report, not an acceptance: the verdict is
# review/acceptance.json `acceptance.verdict` (admitted only when every
# check of tools/acceptance_compare.py holds, the T2c gate included).
# Exit status: that of r6_review.sh (0 admitted, 1 not admitted, 2
# unusable run or input).
set -u -o pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN="${1:?run dir}"; LABEL="${2:?label}"; shift 2
RUN="$(cd "$RUN" && pwd)" || exit 2
DEST="$ROOT/benchmarks/coverage/spec-v1/$LABEL"
PY="${PY:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=python3
export PYTHONDONTWRITEBYTECODE=1

die() { echo "r6_publish: $*" >&2; exit 2; }

[ "$#" -eq 5 ] || die "expected the five oracle reports of the candidate, got $#"
last_done="$(grep '^RUN_DONE ' "$RUN/run.log" | tail -n 1)"
case "$last_done" in
  "RUN_DONE exit=0 "*) ;;
  *) die "the last run did not finish with exit 0: '${last_done:-no RUN_DONE}'" ;;
esac
for snap in repo maskbench; do
  for f in "coverage_$snap.json" "coverage_$snap.md" "coverage_$snap.passes.json.gz"; do
    [ -f "$RUN/out/$f" ] || die "missing $RUN/out/$f"
  done
done
lib="$(cut -d' ' -f1 "$RUN/identity/lib.sha256")"
for snap in repo maskbench; do
  "$PY" - "$RUN/out/coverage_$snap.json" "$lib" <<'EOF' || die "library hash of coverage_$snap.json differs from the run identity"
import json, sys
meta = json.load(open(sys.argv[1], encoding="utf-8"))["meta"]
sys.exit(0 if meta["engine"]["library_sha256"] == sys.argv[2] else 1)
EOF
done
[ ! -e "$DEST" ] || die "$DEST exists; publish under a new label"

mkdir -p "$DEST/identity" || die "mkdir failed"
for snap in repo maskbench; do
  gzip -n -c "$RUN/out/coverage_$snap.json" > "$DEST/coverage_$snap.json.gz" || die "gzip failed"
  cp "$RUN/out/coverage_$snap.md" "$RUN/out/coverage_$snap.passes.json.gz" "$DEST/" || die "copy failed"
done
if [ -f "$RUN/identity/manifest.json" ]; then
  # A run directory with its own venv and pinned corpora (run_t3_accept.sh,
  # t3_identity.py): the manifest and the per-input file lists.
  [ -f "$RUN/identity/.complete" ] || die "the identity record of $RUN is incomplete"
  for f in manifest.json git_head lib.sha256; do
    cp "$RUN/identity/$f" "$DEST/identity/" || die "identity $f missing"
  done
  mkdir -p "$DEST/identity/files" || die "mkdir failed"
  for f in "$RUN"/identity/files/*.list; do
    gzip -n -c "$f" > "$DEST/identity/files/$(basename "$f").gz" || die "identity list $f"
  done
else
  # The earlier layout (shared .venv, linked corpora): acc-50cbd80e.
  for f in git_head lib.sha256 pip_freeze.txt python_version corpus_index.sha256 \
           corpus_repo_revision corpus_maskbench_revision; do
    cp "$RUN/identity/$f" "$DEST/identity/" || die "identity $f missing"
  done
  sha256sum "$RUN/identity/tree.sha256" | cut -d' ' -f1 > "$DEST/identity/tree.sha256.sha256"
fi
grep -E '^RUN_(INIT|PREPARED|READY|START|DONE|ABORT)' "$RUN/run.log" > "$DEST/identity/run_events.txt"

# The run's library (identity/lib.sha256) for the triage's bindings.
R6_LIB="$RUN/lib/libbolorgir.so.0.1.0" PY="$PY" \
  bash "$ROOT/benchmarks/t3/r6_review.sh" "$DEST" "$LABEL" "$DEST/review" "$@"
rc=$?
(cd "$DEST" && find . -type f ! -name SHA256SUMS -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) \
  > "$DEST/SHA256SUMS"
echo "R6_PUBLISH_DONE exit=$rc dest=$DEST"
exit "$rc"
