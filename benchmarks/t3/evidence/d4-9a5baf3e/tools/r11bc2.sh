#!/bin/bash
# r11bc2.sh [--check-only]: R11-B2 and R11-C2 of the D4 analysis of
# 9a5baf3e, byte-level engine_unavailable passes, with replay_e.py (the
# harness of the run, RUN/tree; the run's Python; recorded seeds).
#   C  every engine_unavailable pass of a transition result on the
#      candidate 9a5baf3e (the run's library, 10 s as in the run): it
#      must reproduce its record (outcome, stage, code, step, prefix
#      length; as_recorded) and gives the engine message, the histogram
#      of the undecided certificate reasons, the tail and hex prefix -
#      checked against the cause the updated D4 entries state (D4-3,
#      D4-3m, D4-4, D4-5, D4-10, D4-11, D4-11m, D4-12);
#   B  MaskBench bigquery-table passes 5 and 12 on the lines dbc5eeb and
#      7345490f with 300 s (in the run both ended in the 10 s limit near
#      step 100): do they reach the candidate's state (same step and
#      prefix, RESOURCE_LIMIT) given the time; and on 50cbd80e (host CPU,
#      before stages C and D) with 10 s.
# One process at a time on CPU $R11_CPU (default 2), the load average
# below 1.5 before each (waits up to 10 min: exit 3). Output d4f/r11c2/
# and d4f/r11b2/ (refused if present), then r11bc2_summary.py. Exit: 0
# done; the status of a failed replay; 2 refused; 3 not quiet.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/common.sh"
CHECK=0; [ "${1:-}" = "--check-only" ] && CHECK=1
CPU="${R11_CPU:-2}"
ulimit -v 16777216
unset PYTHONPATH
export HF_HOME="$RUN/hf-cache" HF_HUB_OFFLINE=1 BOLORGIR_COVERAGE_PROFILE=spec-v1
check_lib "$LIB_CAND" "$CAND_SHA" && check_lib "$LIB_DBC" "$SHA_DBC" \
  && check_lib "$LIB_73" "$SHA_73" && check_lib "$LIB_50" "$SHA_50" || exit 2
[ -x "$VPY" ] && [ -d "$TREE/benchmarks" ] || { echo "refused: run venv or tree missing"; exit 2; }
for s in repo maskbench; do [ -f "$RUN/out/coverage_$s.passes.json.gz" ] || { echo "refused: no $s sidecar"; exit 2; }; done
taskset -c "$CPU" true || { echo "refused: CPU $CPU not usable"; exit 2; }
for d in r11c2 r11b2; do [ -e "$HERE/$d" ] && { echo "refused: $HERE/$d exists"; exit 2; }; done
# snapshot DATASET/FILE passes entry (the engine_unavailable passes of 9a5baf3e)
C_LIST="repo Github_hard/o31351.json 8 D4-10
repo JsonSchemaStore/bigquery-table.json 19 D4-11
repo JsonSchemaStore/cryproj.dev.schema.json 10 D4-12
repo JsonSchemaStore/tye-schema.json 3,6,7,11,15 D4-3
maskbench JsonSchemaStore/JsonSchemaStore---tye-schema.json 4,8,9,13,16,19 D4-3m
maskbench Github_hard/Github_hard---o32934.json 8,13 D4-4
maskbench JsonSchemaStore/JsonSchemaStore---cryproj.53.schema.json 18 D4-5
maskbench JsonSchemaStore/JsonSchemaStore---bigquery-table.json 5,12 D4-11m"
if [ "$CHECK" = 1 ]; then echo "== r11bc2 setup checks passed (--check-only, nothing run)"; exit 0; fi
mkdir -p "$HERE/r11c2" "$HERE/r11b2" || exit 2
quiet() {
  local i load1
  for i in $(seq 1 20); do
    load1="$(cut -d' ' -f1 /proc/loadavg)"
    python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) < ${R11_LOAD_MAX:-1.5} else 1)" "$load1" && return 0
    echo "   load $load1, waiting"; sleep 30
  done
  return 1
}
replay() {  # out label lib snapshot file passes seconds
  quiet || { echo "STOP: the machine is not quiet (load $(cut -d' ' -f1-3 /proc/loadavg))"; exit 3; }
  echo "== $2 $5 passes $6 budget $7 s start $(date -u +%FT%TZ)"
  BLG_LIB_PATH="$3" taskset -c "$CPU" nice -n 0 timeout 3600 "$VPY" "$HERE/replay_e.py" --tree "$TREE" \
    --passes "$RUN/out/coverage_$4.passes.json.gz" --snapshot "$4" --file "$5" --tokenizer byte \
    --label "$2" --only "$6" --max-seconds "$7" --out "$1" > "${1%.jsonl}.log" 2>&1
  local rc=$?
  tail -1 "${1%.jsonl}.log"
  [ "$rc" = 0 ] || { tail -20 "${1%.jsonl}.log"; exit "$rc"; }
}
while read -r snap file passes entry; do
  replay "$HERE/r11c2/$(basename "$file" .json).cand.jsonl" cand "$LIB_CAND" "$snap" "$file" "$passes" 10
  replay "$HERE/r11c2/$(basename "$file" .json).50cbd80e.jsonl" 50cbd80e "$LIB_50" "$snap" "$file" "$passes" 10
done <<< "$C_LIST"
BQ=JsonSchemaStore/JsonSchemaStore---bigquery-table.json
replay "$HERE/r11b2/bigquery-table-m.dbc5eeb.t300.jsonl" dbc5eeb "$LIB_DBC" maskbench "$BQ" 5,12 300
replay "$HERE/r11b2/bigquery-table-m.7345490f.t300.jsonl" 7345490f "$LIB_73" maskbench "$BQ" 5,12 300
replay "$HERE/r11b2/bigquery-table-m.50cbd80e.t10.jsonl" 50cbd80e "$LIB_50" maskbench "$BQ" 5,12 10
python3 "$HERE/r11bc2_summary.py" "$HERE" || exit 1
echo "== r11bc2 done"
