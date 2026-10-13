#!/bin/bash
# run_compact.sh [--check-only]: the compact-JSON re-collection of the T10
# section-10 comparison (protocol amendment A3, seen PROTOCOL.md).
# The xgrammar sessions run with disable_any_whitespace=true (ADR-0016
# Finding 4: the comparison must constrain the same compact language);
# the bolorgir sessions repeat so the alternation stays within one time
# window. Order x1c, b1c, x2c, b2c; one server start per session on port
# 8021, stopped with SIGINT when the session's records are written.
# Everything lands in xgrammar-compare/compact/ (raw-<s>.jsonl,
# server-<s>.log, prov-<s>.txt); the earlier sessions' files are not
# touched. Exit: 0 all four sessions recorded; 2 a refused setup or a
# failed session (the server log tail is printed).
set -u
ROOT="/home/gm/Рабочий стол/сайты/LLM_trainer/zig"
D="$ROOT/benchmarks/local/t10-gpu-1/xgrammar-compare"
VLLM="$ROOT/benchmarks/local/venv-vllm/bin/vllm"
PY="$ROOT/benchmarks/local/venv-vllm/bin/python"
MODEL="Qwen/Qwen2.5-0.5B-Instruct"
REV="7ae557604adf67be50417f59c2c2f167def9a775"
PORT=8021
URL="http://127.0.0.1:$PORT"
CHECK=0; [ "${1:-}" = "--check-only" ] && CHECK=1
OUT="$D/compact"
[ -x "$VLLM" ] && [ -x "$PY" ] || { echo "refused: no venv-vllm"; exit 2; }
[ -f "$D/xgrammar_bench.py" ] || { echo "refused: no bench script"; exit 2; }
sha256sum "$ROOT/python/bolorgir/_lib/libbolorgir.so.0.1.0" | grep -q "^9a5baf3e" \
  || { echo "refused: python/bolorgir/_lib is not the candidate 9a5baf3e"; exit 2; }
if [ "$CHECK" = 1 ]; then
  echo "== run_compact setup checks passed (--check-only, nothing run)"; exit 0; fi
[ -e "$OUT" ] && { echo "refused: $OUT exists"; exit 2; }
mkdir -p "$OUT" || exit 2
export VLLM_USE_FLASHINFER_SAMPLER=0
# The pinned model has no copy in the caches (checked 2026-10-13); fetch it
# into the project cache so the sessions and any re-run share it.
export HF_HOME="${HF_HOME:-$ROOT/benchmarks/local/hf-cache}"

health() { curl -sf "$URL/health" > /dev/null 2>&1; }

start_server() {  # label cfg extra-env...
  local label="$1" cfg="$2"; shift 2
  local log="$OUT/server-$label.log"
  echo "== $label server start $(date -u +%FT%TZ) cfg=$cfg"
  cd "$ROOT" || exit 2
  env "$@" nohup "$VLLM" serve "$MODEL" --revision "$REV" \
      --structured-outputs-config "$cfg" --no-enable-prefix-caching \
      --host 127.0.0.1 --port "$PORT" > "$log" 2>&1 &
  echo $! > "$OUT/server.pid"
  local pid; pid="$(cat "$OUT/server.pid")"
  local i
  for i in $(seq 1 120); do
    health && { echo "== $label healthy (pid $pid)"; return 0; }
    kill -0 "$pid" 2>/dev/null || { echo "== $label server died"; tail -30 "$log"; return 1; }
    sleep 5
  done
  echo "== $label did not become healthy"; tail -30 "$log"; return 1
}

stop_server() {  # label
  local label="$1" pid; pid="$(cat "$OUT/server.pid")"
  kill -INT "$pid" 2>/dev/null
  local i
  for i in $(seq 1 60); do
    kill -0 "$pid" 2>/dev/null || { echo "== $label server stopped"; return 0; }
    sleep 1
  done
  kill -TERM "$pid" 2>/dev/null; sleep 5
  kill -0 "$pid" 2>/dev/null && { echo "== $label server did not stop"; return 1; }
  echo "== $label server stopped (TERM)"; return 0
}

prov() {  # label
  local label="$1" pid; pid="$(cat "$OUT/server.pid")"
  {
    echo "== $(date -u +%FT%TZ) session $label"
    echo "argv: $(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)"
    echo "python: $("$PY" -V 2>&1)"
    echo "lib: $(sha256sum "$ROOT/python/bolorgir/_lib/libbolorgir.so.0.1.0" 2>/dev/null)"
    grep -o "StructuredOutputsConfig(backend='[a-z]*', disable_any_whitespace=[A-Za-z]*, disable_additional_properties=[A-Za-z]*" \
      "$OUT/server-$label.log" | head -1
    local ec
    for ec in $(pgrep -f "EngineCore" 2>/dev/null); do
      grep -m1 "libbolorgir.so.0.1.0" "/proc/$ec/maps" 2>/dev/null | head -1
    done
  } > "$OUT/prov-$label.txt" 2>&1
  cat "$OUT/prov-$label.txt"
}

session() {  # label backend extra-env...
  local label="$1" backend="$2"; shift 2
  local cfg
  if [ "$backend" = "xgrammar" ]; then
    cfg='{"backend": "xgrammar", "disable_any_whitespace": true}'
    start_server "$label" "$cfg" || exit 2
    grep -q "disable_any_whitespace=True" "$OUT/server-$label.log" \
      || { echo "refused: $label server does not run disable_any_whitespace=true"; exit 2; }
  else
    cfg='{"backend": "bolorgir"}'
    start_server "$label" "$cfg" \
      PYTHONPATH="$ROOT/python" \
      "BLG_AGENT_TRACE=$OUT/trace-$label.jsonl" || exit 2
  fi
  prov "$label" || exit 2
  echo "== $label client start $(date -u +%FT%TZ)"
  ( cd "$ROOT" && exec "$PY" "$D/xgrammar_bench2.py" --session "$label" \
      --backend "$backend" --out "$OUT/raw-$label.jsonl" --repeats 2 )
  local rc=$?
  stop_server "$label" || exit 2
  [ "$rc" = 0 ] || { echo "== $label client failed rc=$rc"; exit 2; }
  echo "== $label done $(date -u +%FT%TZ)"
}

session x1c xgrammar
session b1c bolorgir
session x2c xgrammar
session b2c bolorgir
echo "== manifest"
"$PY" - "$ROOT" "$OUT" <<'EOF' || exit 2
import hashlib, json, os, subprocess, sys
root, out = sys.argv[1:3]
def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()
def outb(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return (r.stdout or r.stderr).strip()
man = {
    "about": "A3 compact re-collection of the T10 section-10 comparison (run_compact.sh)",
    "sessions": ["x1c", "b1c", "x2c", "b2c"],
    "core": {"path": "python/bolorgir/_lib/libbolorgir.so.0.1.0",
             "sha256": sha(os.path.join(root, "python/bolorgir/_lib/libbolorgir.so.0.1.0"))},
    "adapter": {"path": "python/bolorgir/vllm.py",
                "sha256": sha(os.path.join(root, "python/bolorgir/vllm.py"))},
    # Package versions come from importlib.metadata: `xgrammar.__version__`
    # and `vllm.__version__` are not guaranteed to exist (xgrammar 0.2.7
    # raised AttributeError on the attribute probe).
    "vllm_version": outb([os.path.join(root, "benchmarks/local/venv-vllm/bin/python"),
                          "-c", "from importlib.metadata import version; print(version('vllm'))"]),
    "xgrammar_version": outb([os.path.join(root, "benchmarks/local/venv-vllm/bin/python"),
                              "-c", "from importlib.metadata import version; print(version('xgrammar'))"]),
    "model": "Qwen/Qwen2.5-0.5B-Instruct@7ae557604adf67be50417f59c2c2f167def9a775",
    "patch": None,
    "server_argv": {}, "engine_maps": {},
}
for d in ("integrations/vllm",):
    for f in sorted(os.listdir(os.path.join(root, d))):
        if f.endswith(".patch"):
            man["patch"] = {"path": f"{d}/{f}", "sha256": sha(os.path.join(root, d, f))}
for s in man["sessions"]:
    p = os.path.join(out, f"prov-{s}.txt")
    if os.path.exists(p):
        txt = open(p, encoding="utf-8").read()
        for line in txt.splitlines():
            if line.startswith("argv:"):
                man["server_argv"][s] = line[5:].strip()
            if "libbolorgir.so.0.1.0" in line and "sha256" in line:
                man["engine_maps"][s] = line.strip()
with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(man, f, indent=1)
    f.write("\n")
print(json.dumps(man, indent=1)[:1200])
EOF
echo "== summarize (report v2)"
( cd "$D" && exec "$PY" "$D/xgrammar_report.py" \
    "$OUT/raw-x1c.jsonl" "$OUT/raw-b1c.jsonl" "$OUT/raw-x2c.jsonl" "$OUT/raw-b2c.jsonl" \
    --out-json "$OUT/summary-v2.json" --out-md "$OUT/REPORT-v2.md" \
    --title "XGrammar comparison, A3 compact series (T10 section 10)" )
echo "== run_compact done"
