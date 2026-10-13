#!/bin/bash
# engine_core_check.sh: confirms that the candidate core library is loaded
# inside the vLLM EngineCore process during a bolorgir structured-output
# session (TZ 6.1: the manifest must carry EngineCore load confirmation).
# The four A3 sessions recorded prov-*.txt before the first request, when
# the adapter had not yet loaded the core; this check starts the same
# server, issues the three batch-1 requests of the A3 matrix, then reads
# /proc/<EngineCore pid>/maps while the engine is alive. Nothing of the
# original series is modified. Exit: 0 confirmation found; 2 refused
# setup or the core was not found in any EngineCore process (the finding
# is still written to the out dir).
set -u
ROOT="/home/gm/Рабочий стол/сайты/LLM_trainer/zig"
D="$ROOT/benchmarks/local/t10-gpu-1/xgrammar-compare"
VLLM="$ROOT/benchmarks/local/venv-vllm/bin/vllm"
PY="$ROOT/benchmarks/local/venv-vllm/bin/python"
MODEL="Qwen/Qwen2.5-0.5B-Instruct"
REV="7ae557604adf67be50417f59c2c2f167def9a775"
PORT=8021
URL="http://127.0.0.1:$PORT"
OUT="$D/compact/engine-core-check"
[ -x "$VLLM" ] && [ -x "$PY" ] || { echo "refused: no venv-vllm"; exit 2; }
sha256sum "$ROOT/python/bolorgir/_lib/libbolorgir.so.0.1.0" | grep -q "^9a5baf3e" \
  || { echo "refused: python/bolorgir/_lib is not the candidate 9a5baf3e"; exit 2; }
[ -e "$OUT" ] && { echo "refused: $OUT exists"; exit 2; }
mkdir -p "$OUT" || exit 2
export VLLM_USE_FLASHINFER_SAMPLER=0
export HF_HOME="${HF_HOME:-$ROOT/benchmarks/local/hf-cache}"

health() { curl -sf "$URL/health" > /dev/null 2>&1; }

echo "== server start $(date -u +%FT%TZ) cfg=bolorgir"
cd "$ROOT" || exit 2
env PYTHONPATH="$ROOT/python" nohup "$VLLM" serve "$MODEL" --revision "$REV" \
    --structured-outputs-config '{"backend": "bolorgir"}' \
    --no-enable-prefix-caching --host 127.0.0.1 --port "$PORT" \
    > "$OUT/server.log" 2>&1 &
SPID=$!
echo "$SPID" > "$OUT/server.pid"
for i in $(seq 1 120); do
  health && break
  kill -0 "$SPID" 2>/dev/null || { echo "== server died"; tail -30 "$OUT/server.log"; exit 2; }
  sleep 5
done
health || { echo "== server did not become healthy"; tail -30 "$OUT/server.log"; exit 2; }
echo "== healthy (pid $SPID)"

echo "== client start $(date -u +%FT%TZ): w1/w2/w3 at batch 1"
( cd "$ROOT" && exec "$PY" "$D/xgrammar_bench2.py" --session echeck \
    --backend bolorgir --out "$OUT/raw-echeck.jsonl" --repeats 1 --batches 1 )
RC=$?
echo "== client rc=$RC"

echo "== engine scan $(date -u +%FT%TZ)"
{
  echo "== $(date -u +%FT%TZ) EngineCore scan after the first structured requests"
  echo "server argv: $(tr '\0' ' ' < "/proc/$SPID/cmdline" 2>/dev/null)"
  echo "lib: $(sha256sum "$ROOT/python/bolorgir/_lib/libbolorgir.so.0.1.0" 2>/dev/null)"
  echo "processes matching 'EngineCore':"
  FOUND=0
  for ec in $(pgrep -f "EngineCore" 2>/dev/null); do
    echo "  pid $ec comm=$(cat "/proc/$ec/comm" 2>/dev/null)"
    echo "    argv: $(tr '\0' ' ' < "/proc/$ec/cmdline" 2>/dev/null | cut -c1-300)"
    M="$(grep -m1 "libbolorgir.so.0.1.0" "/proc/$ec/maps" 2>/dev/null || true)"
    if [ -n "$M" ]; then
      echo "    maps: $M"
      FOUND=1
    else
      echo "    maps: libbolorgir.so.0.1.0 not mapped"
    fi
    grep -c "libbolorgir" "/proc/$ec/maps" 2>/dev/null | sed 's/^/    libbolorgir mapping lines: /'
  done
  echo "FOUND=$FOUND"
} | tee "$OUT/engine-core-maps.txt"
FOUND="$(grep -c '^FOUND=1$' "$OUT/engine-core-maps.txt")"

"$PY" - "$ROOT" "$OUT" "$RC" <<'EOF'
import hashlib, json, os, subprocess, sys
root, out, rc = sys.argv[1], sys.argv[2], int(sys.argv[3])
def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()
def outb(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout or r.stderr).strip()
man = {
  "about": "EngineCore load confirmation for the A3 compact series (engine_core_check.sh)",
  "purpose": "TZ 6.1: the manifest carries the full core hash and confirmation that the candidate core is loaded inside the vLLM EngineCore process; the per-session prov files were recorded before the adapter loaded the core, so this check repeats the same server configuration with batch-1 requests and scans /proc",
  "date_utc": outb(["date", "-u", "+%FT%TZ"]),
  "core": {"path": "python/bolorgir/_lib/libbolorgir.so.0.1.0",
           "sha256": sha(os.path.join(root, "python/bolorgir/_lib/libbolorgir.so.0.1.0"))},
  "client_rc": rc,
  "engine_scan": open(os.path.join(out, "engine-core-maps.txt"), encoding="utf-8").read(),
  "found": "FOUND=1" in open(os.path.join(out, "engine-core-maps.txt"), encoding="utf-8").read(),
}
with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
    json.dump(man, f, indent=1); f.write("\n")
print("found:", man["found"], "client_rc:", rc)
EOF

kill -INT "$SPID" 2>/dev/null
for i in $(seq 1 60); do
  kill -0 "$SPID" 2>/dev/null || break
  sleep 1
done
kill -0 "$SPID" 2>/dev/null && kill -TERM "$SPID" 2>/dev/null && sleep 3
echo "== server stopped"
[ "$RC" = 0 ] || { echo "== client failed"; exit 2; }
[ "$FOUND" = 1 ] || { echo "== core not found in EngineCore maps"; exit 2; }
echo "== engine_core_check ok"
