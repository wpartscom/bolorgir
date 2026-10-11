#!/bin/bash
# Processing of the saved results of a T3 candidate:
# the T2c triage of the candidate (benchmarks/triage_semantic.py), then the
# coverage control and acceptance verdict (tools/acceptance_compare.py)
# against dbc5eeb and 7345490f with the D4 list, the oracle reports of the
# candidate and of the lines (oracle_lines.json) and the protocol notes,
# an evidence bundle of every input with its sha256 and tools.json, the
# version of the review tools. Nothing is generated or run against a
# library; the corpora must be present for the triage.
#
# Usage: r6_review.sh <reports dir> <label> <out dir> [<oracle report> ...]
#   <reports dir>   coverage_<snapshot>.json[.gz] of the candidate with
#                   their .passes.json.gz sidecars (one or both corpora)
#   <label>         build name of the candidate, e.g. t3-50cbd80e
#   <out dir>       new or empty directory outside the protected paths
#   <oracle report> tests/oracle/run_oracle.py results of the candidate,
#                   spec-v1, one per dialect (all five for an acceptance)
#
# Env: R6_LIB (required: the candidate's core library; its sha256 must be
# the library_sha256 of every coverage report given - the triage imports
# the ctypes bindings, which load a library at import, and a tree's
# zig-out/lib is missing in a worktree or a copy and another build in the
# main tree), PY (main .venv python, read-only use), D4, ORACLE_LINES,
# PROTOCOL_NOTES (the tracked files under benchmarks/t3/ by default),
# R6_MEM_LIMIT_MB (16384: address-space limit of every process,
# blg_memory_limit in benchmarks/protected_paths.sh; 0 = none).
#
# Exit status: that of acceptance_compare.py (0 admitted, 1 not admitted,
# 3 intermediate) or 2 for unusable input, a failed triage run or a
# refused output directory. The last line is
# `R6_REVIEW_DONE verdict=<verdict> exit=<status> out=<dir>`.
set -u -o pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
. "$ROOT/benchmarks/protected_paths.sh"

REPORTS="${1:?reports dir}"; LABEL="${2:?label}"; OUT="${3:?out dir}"; shift 3
PY="${PY:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY=python3
D4="${D4:-$ROOT/benchmarks/t3/d4_proposals.json}"
ORACLE_LINES="${ORACLE_LINES:-$ROOT/benchmarks/t3/oracle_lines.json}"
PROTOCOL_NOTES="${PROTOCOL_NOTES:-$ROOT/benchmarks/t3/protocol_notes.json}"
export PYTHONDONTWRITEBYTECODE=1

die() { echo "r6_review: $*" >&2; echo "R6_REVIEW_DONE verdict=unusable exit=2 out=$OUT"; exit 2; }
blg_memory_limit "${R6_MEM_LIMIT_MB:-16384}" || die "memory limit"

blg_guard_new_dir "$ROOT" "$OUT" || die "output directory refused"
REPORTS="$(cd "$REPORTS" && pwd)" || die "no reports dir"

coverage_args=()
for snap in repo maskbench; do
  for f in "$REPORTS/coverage_$snap.json" "$REPORTS/coverage_$snap.json.gz"; do
    if [ -f "$f" ]; then
      [ -f "$REPORTS/coverage_$snap.passes.json.gz" ] || die "sidecar of $f missing"
      coverage_args+=(--coverage "$LABEL=$f")
      break
    fi
  done
done
[ "${#coverage_args[@]}" -gt 0 ] || die "no coverage report in $REPORTS"
[ -n "${R6_LIB:-}" ] && [ -f "$R6_LIB" ] || die "R6_LIB (the candidate's core library) not given or missing"
lib_sha="$(sha256sum -- "$R6_LIB" | cut -d' ' -f1)"
"$PY" - "$lib_sha" "${coverage_args[@]}" <<'EOF' || die "R6_LIB is not the library of the candidate's reports"
import gzip, json, sys
want, args = sys.argv[1], sys.argv[2:]
for path in [a.split("=", 1)[1] for a in args[1::2]]:
    with (gzip.open if path.endswith(".gz") else open)(path, "rt", encoding="utf-8") as f:
        got = (json.load(f).get("meta", {}).get("engine") or {}).get("library_sha256")
    if got != want:
        print(f"r6_review: {path} names the library {got}, R6_LIB is {want}", file=sys.stderr)
        sys.exit(1)
EOF

# Written only after the input checks above: a refusal leaves no output.
mkdir -p "$OUT/evidence/oracle" || die "mkdir failed"
OUT="$(cd "$OUT" && pwd)"
oracle_args=()
for f in "$@"; do
  [ -f "$f" ] || die "oracle report $f missing"
  oracle_args+=(--oracle "$LABEL=$f")
  gzip -n -c "$f" > "$OUT/evidence/oracle/$LABEL-$(basename "$f").gz" || die "copy failed"
done

printf '{\n "comment": "Class D entries of the T2c triage of %s.",\n "entries": []\n}\n' \
  "$LABEL" > "$OUT/deviations.json"
# The triage imports tests/generation_passes.py and the ctypes bindings
# (the draft-04 check itself runs jsonschema only): they load R6_LIB.
BLG_LIB_PATH="$R6_LIB" PYTHONPATH="$ROOT/python" "$PY" "$ROOT/benchmarks/triage_semantic.py" "${coverage_args[@]}" \
  --gate-build "$LABEL" --out-md "$OUT/triage.md" --out-json "$OUT/triage.json" \
  --deviations "$OUT/deviations.json" > "$OUT/triage.log" 2>&1
triage_rc=$?
echo "TRIAGE exit=$triage_rc"
# 0 gate passed, 1 gate failed (both write triage.json); anything else or
# a missing triage.json is a failed run.
[ -f "$OUT/triage.json" ] || die "triage did not finish (exit $triage_rc, see $OUT/triage.log)"

"$PY" "$ROOT/benchmarks/t3/tools/acceptance_compare.py" \
  --candidate "$LABEL=$REPORTS" \
  --line "dbc5eeb=$ROOT/benchmarks/coverage/spec-v1/baseline-dbc5eeb" \
  --line "7345490f=$ROOT/benchmarks/coverage/spec-v1" \
  --d4 "$D4" --triage "$OUT/triage.json" --oracle-lines "$ORACLE_LINES" \
  --protocol-notes "$PROTOCOL_NOTES" "${oracle_args[@]}" \
  --out-md "$OUT/acceptance.md" --out-json "$OUT/acceptance.json"
rc=$?
echo "ACCEPTANCE_COMPARE exit=$rc"
[ "$rc" = 2 ] && die "acceptance_compare: unusable input"

# Evidence bundle: the decision files and the line oracle reports used.
cp "$D4" "$OUT/evidence/d4.json" && cp "$ORACLE_LINES" "$OUT/evidence/oracle_lines.json" &&
  cp "$PROTOCOL_NOTES" "$OUT/evidence/protocol_notes.json" || die "copy failed"
# With the line reports: for an archive binding, the binding documents
# and the sha256 and mtime of the archive library.
# The tools of this review, apart from the revision of the run it reviews
# (identity/git_head of a publication): sha256 of each file, the git HEAD
# of this tree (null outside git) and the listed files that differ from it.
"$PY" - "$ROOT" "$OUT/tools.json" <<'EOF' || die "tools record failed"
import hashlib, json, os, subprocess, sys
root, out = sys.argv[1:3]
files = ["benchmarks/t3/r6_review.sh", "benchmarks/t3/r6_publish.sh",
         "benchmarks/t3/tools/acceptance_compare.py", "benchmarks/triage_semantic.py",
         "tests/generation_passes.py", "tests/semantic_outcomes.py", "tests/exact_numbers.py",
         "tests/blg_ctypes.py", "python/bolorgir/serializer.py", "benchmarks/protected_paths.sh"]
def git(*args):
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=60)
    except OSError:
        return None
    return r.stdout if r.returncode == 0 else None
top = git("rev-parse", "--show-toplevel")  # a copy inside another repository is no git tree
head = (git("rev-parse", "--verify", "HEAD")
        if top and os.path.realpath(top.strip()) == os.path.realpath(root) else None)
changed = git("diff", "--name-only", "HEAD", "--", *files) if head else None
rec = {"git_head": head.strip() if head else None,
       "changed_from_head": sorted(changed.split("\n")[:-1]) if changed is not None else None,
       "files": {f: hashlib.sha256(open(os.path.join(root, f), "rb").read()).hexdigest()
                 for f in files}}
with open(out, "w", encoding="utf-8") as f:
    json.dump(rec, f, indent=1)
    f.write("\n")
EOF
"$PY" - "$ORACLE_LINES" "$ROOT" "$OUT/evidence" <<'EOF' || die "line oracle copy failed"
import datetime, gzip, hashlib, json, os, shutil, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
root, ev = sys.argv[2], sys.argv[3]
for line in doc["lines"]:
    for dialect, r in (line.get("reports") or {}).items():
        src = os.path.join(root, r["path"])
        with open(src, "rb") as f, gzip.GzipFile(os.path.join(
                ev, "oracle", f"{line['name']}-{os.path.basename(src)}.gz"), "wb", mtime=0) as g:
            shutil.copyfileobj(f, g)
    b = line.get("binding")
    if not b:
        continue
    d = os.path.join(ev, "binding", line["name"])
    os.makedirs(d, exist_ok=True)
    for item in b.get("documents") or []:
        shutil.copyfile(os.path.join(root, item["path"]),
                        os.path.join(d, item["path"].replace("/", "__")))
    lib = os.path.join(root, b["library_path"])
    if os.path.isfile(lib):
        sha = hashlib.sha256(open(lib, "rb").read()).hexdigest()
        when = datetime.datetime.fromtimestamp(os.stat(lib).st_mtime, datetime.timezone.utc)
        with open(os.path.join(d, "library.txt"), "w", encoding="utf-8") as f:
            f.write(f"{b['library_path']}\nsha256 {sha}\nmtime {when:%Y-%m-%dT%H:%M:%SZ}\n")
EOF
(cd "$OUT" && find . -type f ! -name SHA256SUMS -print0 | LC_ALL=C sort -z | xargs -0 sha256sum) \
  > "$OUT/SHA256SUMS"
verdict="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["acceptance"]["verdict"])' \
  "$OUT/acceptance.json")"
echo "R6_REVIEW_DONE verdict=$verdict exit=$rc out=$OUT"
exit "$rc"
