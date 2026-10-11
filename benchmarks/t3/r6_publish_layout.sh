#!/bin/bash
# r6_publish_layout.sh RUN SET REV: a published R6 set SET (r6_publish.sh)
# checked against its run directory RUN (run_t3_accept.sh):
#   - both reports (gunzipped), summaries and pass sidecars byte-equal to
#     RUN/out;
#   - identity/manifest.json, git_head, lib.sha256 equal to RUN/identity,
#     and git_head = REV (the revision the run measured);
#   - identity/files/*.list.gz equal to the run's lists, none missing or
#     extra;
#   - identity/run_events.txt equal to the RUN_* events of RUN/run.log,
#     with RUN_DONE exit=0;
#   - the files of the first review present, tools.json (the version of
#     the review tools) among them;
#   - SHA256SUMS, and the own SHA256SUMS of review/ and of every later
#     review directory (review-2, ...; the outer list excludes them),
#     each equal, byte for byte, to the list rebuilt from the files the
#     way r6_publish.sh and r6_review.sh write it: no entry missing,
#     extra, duplicated or stale.
# Exit 0 when all hold, 4 otherwise (each failure printed as LAYOUT: ...),
# 2 for unusable arguments.
set -u
RUN="${1:?run dir}"; O="${2:?published set}"; REV="${3:?revision}"
[ -d "$RUN/identity" ] && [ -d "$O" ] || { echo "r6_publish_layout: no run identity or no set" >&2; exit 2; }
bad=0
need() { [ -e "$O/$1" ] || { echo "LAYOUT: missing $1"; bad=1; }; }
for s in repo maskbench; do
  need "coverage_$s.json.gz"; need "coverage_$s.md"; need "coverage_$s.passes.json.gz"
done
for f in identity/manifest.json identity/git_head identity/lib.sha256 identity/run_events.txt \
         review/acceptance.json review/acceptance.md review/triage.json review/triage.md \
         review/tools.json review/SHA256SUMS SHA256SUMS; do need "$f"; done
n_run="$(ls "$RUN"/identity/files/*.list 2>/dev/null | wc -l)"
n_pub="$(ls "$O"/identity/files/*.list.gz 2>/dev/null | wc -l)"
[ "$n_run" -gt 0 ] && [ "$n_run" = "$n_pub" ] || { echo "LAYOUT: $n_pub published lists for $n_run of the run"; bad=1; }
for f in "$RUN"/identity/files/*.list; do
  [ -e "$f" ] || continue
  g="$O/identity/files/$(basename "$f").gz"
  if [ ! -f "$g" ] || ! cmp -s <(gzip -dc "$g") "$f"; then echo "LAYOUT: $g differs from $f"; bad=1; fi
done
for f in manifest.json git_head lib.sha256; do
  cmp -s "$RUN/identity/$f" "$O/identity/$f" || { echo "LAYOUT: identity/$f differs from the run's"; bad=1; }
done
[ "$(cat "$O/identity/git_head" 2>/dev/null)" = "$REV" ] || { echo "LAYOUT: git_head is not $REV"; bad=1; }
cmp -s <(grep -E '^RUN_(INIT|PREPARED|READY|START|DONE|ABORT)' "$RUN/run.log") "$O/identity/run_events.txt" \
  || { echo "LAYOUT: run_events.txt differs from the events of the run's run.log"; bad=1; }
grep -q '^RUN_DONE exit=0 ' "$O/identity/run_events.txt" 2>/dev/null || { echo "LAYOUT: no RUN_DONE exit=0"; bad=1; }
# Every published report equal to its source in the run: the reports
# (gzipped by r6_publish.sh), the summaries and the pass sidecars (copied).
for s in repo maskbench; do
  cmp -s <(gzip -dc "$O/coverage_$s.json.gz" 2>/dev/null) "$RUN/out/coverage_$s.json" \
    || { echo "LAYOUT: coverage_$s.json.gz is not the run's report"; bad=1; }
  for f in "coverage_$s.md" "coverage_$s.passes.json.gz"; do
    cmp -s "$RUN/out/$f" "$O/$f" || { echo "LAYOUT: $f differs from the run's $f"; bad=1; }
  done
done
# The lists rebuilt exactly as written (every file but those named
# SHA256SUMS, sorted, sha256sum) and compared byte for byte with the
# saved ones. The outer list excludes the lists of the review
# directories, so each of those is checked on its own.
sums() { (cd "$1" && find . -type f ! -name SHA256SUMS -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum); }
dirs=("$O")
for r in "$O"/review "$O"/review-*; do [ -d "$r" ] && dirs+=("$r"); done
for dir in "${dirs[@]}"; do
  name="${dir#"$O"}"; name="${name#/}${name:+/}SHA256SUMS"
  if [ ! -f "$dir/SHA256SUMS" ]; then echo "LAYOUT: $name missing"; bad=1; continue; fi
  if ! cmp -s <(sums "$dir") "$dir/SHA256SUMS"; then
    echo "LAYOUT: $name is not the complete list of the files (rebuilt list differs):"
    diff <(sums "$dir") "$dir/SHA256SUMS" | grep '^[<>]' | head -6 | cut -c1-140 | sed 's/^/  /'
    bad=1
  fi
done
[ "$bad" = 0 ] || exit 4
echo "== layout ok: $O (lists: ${#dirs[@]})"
