#!/bin/bash
# Overhead of the atomic diagnostics counters: the same corpus file is run
# with counters on and off. The pair is repeated three times per file and the
# order inside the pair alternates (on-off, off-on, on-off), so a drift of the
# machine cannot favour one variant. Every run keeps its full log and its exit
# code; parsing happens afterwards in counter_aggregate.py, so a traceback can
# never be swallowed by a pipeline.
#
# usage: measure_counters.sh [PKG_ON PKG_OFF]
#
#   PKG_ON, PKG_OFF  two copies of the bolorgir package tree whose
#                    bolorgir/_lib/libbolorgir.so.0.1.0 resolves to the two
#                    artifacts of `zig build -Ddiag_counters=true|false`;
#                    default: the local diagnostic directory
#   BLG_PY           venv-pinned python, default
#                    benchmarks/local/t8a-work-9a5baf3e/venv-pinned/bin/python
#   BLG_DIAG_DIR     directory that holds pkg-new/pkg-off by default
#   BLG_OUT          directory for the logs, default <BLG_DIAG_DIR>/counter-runs
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$ROOT" || exit 1
MB="$ROOT/benchmarks/external/jsonschemabench-maskbench/maskbench"
T8A="$ROOT/benchmarks/maskbench/t8a"
PY="${BLG_PY:-$ROOT/benchmarks/local/t8a-work-9a5baf3e/venv-pinned/bin/python}"
PROBE="$ROOT/benchmarks/maskbench/t8a/diag/mask_probe.py"
LOCAL="${BLG_DIAG_DIR:-$ROOT/benchmarks/local/t8a-diag-9a5baf3e}"
ON="${1:-$LOCAL/pkg-new}"
OFF="${2:-$LOCAL/pkg-off}"
OUT="${BLG_OUT:-$LOCAL/counter-runs}"
rm -rf "$OUT"
mkdir -p "$OUT"
{
  echo "when $(date -u +%FT%TZ)"
  echo "python $("$PY" -V 2>&1)"
  for p in "$ON" "$OFF"; do
    echo "pkg $p"
    sha256sum "$(readlink -f "$p/bolorgir/_lib/libbolorgir.so.0.1.0")"
  done
} > "$OUT/manifest.txt"

fail=0
for spec in "BFCL_java_0.json 16" "Github_medium---o30212.json 60" "Github_trivial---o41679.json 30"; do
  set -- $spec
  file="$1"
  steps="$2"
  stem="${file%.json}"
  for r in 1 2 3; do
    if [ $((r % 2)) -eq 1 ]; then order="on off"; else order="off on"; fi
    for v in $order; do
      case "$v" in
        on) pkg="$ON" ;;
        off) pkg="$OFF" ;;
      esac
      log="$OUT/$stem.$v.r$r.log"
      exitf="$OUT/$stem.$v.r$r.exit"
      t0=$(date +%s%N)
      PYTHONPATH="$pkg:$T8A:$MB" "$PY" "$PROBE" "$file" --max-steps "$steps" > "$log" 2>&1
      code=$?
      t1=$(date +%s%N)
      wall=$(( (t1 - t0) / 1000000 ))
      echo "exit $code wall_ms $wall" > "$exitf"
      printf '%s r%s %-3s exit %s wall %s ms\n' "$stem" "$r" "$v" "$code" "$wall"
      if [ "$code" -ne 0 ]; then fail=$((fail + 1)); fi
    done
  done
done
echo "failures: $fail"
exit $(( fail > 0 ))
