#!/bin/bash
# r11a2.sh [--check-only]: R11-A2 - pass 1 of the 25 incomplete
# transition results of 9a5baf3e (items-r11a2.json; the pass-1 budget
# edge group including the two new GPT-2 results) on six libraries under
# the conditions fixed in README_PLAN.md before the run:
#   cand      9a5baf3e, the candidate (release recipe, -Dcpu=baseline)
#   native    the same source with -Dcpu=native (build_native.sh)
#   prev      6e62e0d1, the previous T3-accepted candidate (same recipe;
#             the column that shows what changed between the candidates)
#   50cbd80e  the candidate before stages C and D (host CPU)
#   7345490f  the final line, dbc5eeb the baseline line (host CPU)
# The harness of the run (RUN/tree), the run's Python environment; the HF
# tokenizers as the run loaded them (export_specs.py once into
# r11a2_specs.json; the replays do not import the bolorgir package, whose
# own library would share the process with the library under test).
# ROUNDS rounds (default 3); in round r the libraries run in the order
# rotated by r-1, one process per library and round, nothing else in
# parallel, pinned to CPU $R11_CPU (default 2) at nice 0. Before each
# process the 1-minute load average must be below $R11_LOAD_MAX (R11-A's
# 1.5; this round 2.5, amendment A1 of README_PLAN.md) - waits up to
# 10 min, then stops: exit 3. Each process: pass 1 of every item with a
# 60 s budget, every fill timed (replay_pass1.py). Output d4f/r11a2/:
# r<round>-<label>.jsonl and .log, manifest.json; then r11a2_summary.py.
# Exit: 0 done; the status of a failed process; 2 refused; 3 the machine
# did not become quiet.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/common.sh"
CHECK=0; [ "${1:-}" = "--check-only" ] && CHECK=1
ROUNDS="${R11_ROUNDS:-3}"; CPU="${R11_CPU:-2}"
OUT="$HERE/${R11A2_OUT:-r11a2}"
SPECS="$HERE/r11a2_specs.json"
ITEMS="$HERE/items-r11a2.json"
NATIVE="$LIB_NATIVE"
LABELS=(cand native prev 50cbd80e 7345490f dbc5eeb)
declare -A LIB SHA
LIB[cand]="$LIB_CAND";     SHA[cand]="$CAND_SHA"
LIB[native]="$NATIVE";     SHA[native]="$(cut -d' ' -f1 "$DE8/d4f/native-9a5baf3e/out/SHA256" 2>/dev/null)"
LIB[prev]="$LIB_PREV";     SHA[prev]="$PREV_SHA"
LIB[50cbd80e]="$LIB_50";   SHA[50cbd80e]="$SHA_50"
LIB[7345490f]="$LIB_73";   SHA[7345490f]="$SHA_73"
LIB[dbc5eeb]="$LIB_DBC";   SHA[dbc5eeb]="$SHA_DBC"
ulimit -v 16777216
unset PYTHONPATH
export HF_HOME="$RUN/hf-cache" HF_HUB_OFFLINE=1 BOLORGIR_COVERAGE_PROFILE=spec-v1
[ -n "${SHA[native]}" ] || { echo "refused: no native build (run build_native.sh first)"; exit 2; }
[ "${SHA[native]}" != "$CAND_SHA" ] || { echo "refused: the native build is the candidate"; exit 2; }
for l in "${LABELS[@]}"; do check_lib "${LIB[$l]}" "${SHA[$l]}" || exit 2; done
echo "libraries ok: $(for l in "${LABELS[@]}"; do printf '%s %s  ' "$l" "${SHA[$l]:0:8}"; done)"
[ -x "$VPY" ] && [ -d "$TREE/benchmarks" ] && [ -f "$ITEMS" ] || { echo "refused: run venv, tree or items missing"; exit 2; }
[ "$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$ITEMS")" = 25 ] \
  || { echo "refused: $ITEMS does not list the 25 results"; exit 2; }
taskset -c "$CPU" true || { echo "refused: CPU $CPU not usable"; exit 2; }
[ -e "$OUT" ] && { echo "refused: $OUT exists"; exit 2; }
if [ "$CHECK" = 1 ]; then echo "== r11a2 setup checks passed (--check-only, nothing run)"; exit 0; fi
if [ ! -f "$SPECS" ]; then
  echo "== tokenizer specs from the run's tree $(date -u +%FT%TZ)"
  PYTHONPATH="$RUN/tree/python" BLG_LIB_PATH="${LIB[cand]}" timeout 600 "$VPY" "$HERE/export_specs.py" \
    "$RUN" "$SPECS" || { echo "export_specs.py failed"; rm -f "$SPECS"; exit 1; }
fi
mkdir -p "$OUT" || exit 2
python3 - "$OUT/manifest.json" "$ITEMS" "$ROUNDS" "$CPU" "$VPY" "$TREE" "$SPECS" \
  "$(for l in "${LABELS[@]}"; do printf '%s=%s=%s\n' "$l" "${SHA[$l]}" "${LIB[$l]}"; done)" <<'EOF' || exit 2
import datetime, json, sys
import hashlib
p, items, rounds, cpu, vpy, tree, specs, libs = sys.argv[1:]
sp = json.load(open(specs))
json.dump({"about": "R11-A2: pass 1 of the incomplete transition results of 9a5baf3e on six libraries, same conditions",
           "started_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "items": json.load(open(items)), "rounds": int(rounds), "cpu": int(cpu), "python": vpy,
           "tree": tree, "specs": {"path": specs, "sha256": hashlib.sha256(open(specs, "rb").read()).hexdigest(),
                                   "tokens_sha256": {k: v["tokens_sha256"] for k, v in sp["tokenizers"].items()}},
           "libraries": {l.split("=")[0]: {"sha256": l.split("=")[1], "path": l.split("=", 2)[2]}
                         for l in libs.splitlines() if l}}, open(p, "w"), indent=1)
EOF
quiet() {
  local i
  for i in $(seq 1 20); do
    load1="$(cut -d' ' -f1 /proc/loadavg)"
    python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) < ${R11_LOAD_MAX:-1.5} else 1)" "$load1" && return 0
    echo "   load $load1, waiting"; sleep 30
  done
  return 1
}
for r in $(seq 1 "$ROUNDS"); do
  for i in "${!LABELS[@]}"; do
    l="${LABELS[$(( (i + r - 1) % ${#LABELS[@]} ))]}"
    quiet || { echo "STOP: the machine is not quiet (load $(cut -d' ' -f1-3 /proc/loadavg))"; exit 3; }
    echo "== round $r $l start $(date -u +%FT%TZ) load $(cut -d' ' -f1 /proc/loadavg)"
    BLG_LIB_PATH="${LIB[$l]}" taskset -c "$CPU" nice -n 0 timeout 3600 "$VPY" "$HERE/replay_pass1.py" \
      --tree "$TREE" --items "$ITEMS" --specs "$SPECS" --label "$l" --round "$r" --out "$OUT/r$r-$l.jsonl" \
      > "$OUT/r$r-$l.log" 2>&1
    rc=$?
    echo "== round $r $l exit $rc $(date -u +%FT%TZ)"
    [ "$rc" = 0 ] || { tail -20 "$OUT/r$r-$l.log"; exit "$rc"; }
  done
done
python3 "$HERE/r11a2_summary.py" "$OUT" || exit 1
echo "== r11a2 done"
