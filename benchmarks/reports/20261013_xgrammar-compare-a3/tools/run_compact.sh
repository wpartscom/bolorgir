#!/bin/bash
# run_compact.sh OUT_DIR [--labels L1 L2 L3 L4] [--repeats N] [--port N]
# run_compact.sh --check-only
#
# Standalone reproduction of the compact-JSON re-collection of the T10
# section-10 comparison (protocol amendments A3/A4, see PROTOCOL.md).
# The script resolves the repository root relative to itself, uses only
# the tools published next to it (xgrammar_common.py, xgrammar_bench2.py,
# xgrammar_report.py), takes the output directory as an argument and
# refuses anything but the pinned core by its full SHA-256.
#
# Environment: VLLM_DIR (default <root>/benchmarks/local/venv-vllm),
# HF_HOME (default <root>/benchmarks/local/hf-cache when present),
# BLG_AGENT_TRACE is set for the bolorgir sessions (traces land in
# OUT_DIR). One `vllm serve` per session on port 8021 (PORT/$--port);
# the xgrammar sessions must report disable_any_whitespace=True in the
# server log. For every session the EngineCore binding is captured
# twice: during the session, as soon as the first records are on disk,
# and after the client finished. Each scan records the PID, comm, the
# libbolorgir mapping line from /proc/<pid>/maps and the full SHA-256
# of the library; bolorgir sessions must show the mapping in both
# scans, xgrammar sessions must not (negative control).
#
# Files per session in OUT_DIR:
#   raw-<label>.jsonl, server-<label>.log, prov-<label>.txt,
#   engine-<label>.txt, trace-<label>.jsonl (bolorgir only);
# then manifest.json and the report pair REPORT-v2.md / summary-v2.json
# produced by the published report tool (a non-empty problem list fails
# the run). Exit: 0 all sessions recorded, manifest and report written;
# 2 refused setup, failed session, failed engine check or report
# problems.
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
CORE="$ROOT/python/bolorgir/_lib/libbolorgir.so.0.1.0"
CORE_SHA_EXPECTED="9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41"
MODEL="Qwen/Qwen2.5-0.5B-Instruct"
REV="7ae557604adf67be50417f59c2c2f167def9a775"
VLLM_DIR="${VLLM_DIR:-$ROOT/benchmarks/local/venv-vllm}"
VLLM="$VLLM_DIR/bin/vllm"
PY="$VLLM_DIR/bin/python"
PORT=8021
LABELS=(x1d b1d x2d b2d)
REPEATS=2
CHECK=0
OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check-only) CHECK=1; shift;;
    --labels) shift; IFS=' ' read -r -a LABELS <<< "${1:-}"; shift;;
    --repeats) REPEATS="${2:?--repeats needs a value}"; shift 2;;
    --port) PORT="${2:?--port needs a value}"; shift 2;;
    -h|--help) sed -n '2,33p' "$0"; exit 0;;
    *) OUT="$1"; shift;;
  esac
done
URL="http://127.0.0.1:$PORT"

refuse() { echo "refused: $*"; exit 2; }
[ -x "$VLLM" ] && [ -x "$PY" ] || refuse "no vLLM environment at $VLLM_DIR (set VLLM_DIR)"
[ -f "$SCRIPT_DIR/xgrammar_common.py" ] || refuse "xgrammar_common.py is not next to the script"
[ -f "$SCRIPT_DIR/xgrammar_bench2.py" ] || refuse "xgrammar_bench2.py is not next to the script"
[ -f "$SCRIPT_DIR/xgrammar_report.py" ] || refuse "xgrammar_report.py is not next to the script"
[ -f "$CORE" ] || refuse "core library not found: $CORE"
[ "$(sha256sum "$CORE" | cut -d' ' -f1)" = "$CORE_SHA_EXPECTED" ] \
  || refuse "core is not the pinned candidate $CORE_SHA_EXPECTED"
[ "${#LABELS[@]}" -eq 4 ] || refuse "need exactly four session labels"
if [ "$CHECK" = 1 ]; then
  echo "== setup checks passed (--check-only): venv, tools, core $CORE_SHA_EXPECTED"
  exit 0
fi
[ -n "$OUT" ] || refuse "usage: run_compact.sh OUT_DIR [--labels L1 L2 L3 L4] [--repeats N] [--port N]"
[ -e "$OUT" ] && refuse "output directory exists: $OUT"
mkdir -p "$OUT" || refuse "cannot create $OUT"
OUT="$(cd "$OUT" && pwd)"
export VLLM_USE_FLASHINFER_SAMPLER=0
[ -d "$ROOT/benchmarks/local/hf-cache" ] && export HF_HOME="${HF_HOME:-$ROOT/benchmarks/local/hf-cache}"

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
    echo "lib: $(sha256sum "$CORE" 2>/dev/null)"
    grep -o "StructuredOutputsConfig(backend='[a-z]*', disable_any_whitespace=[A-Za-z]*, disable_additional_properties=[A-Za-z]*" \
      "$OUT/server-$label.log" | head -1
  } > "$OUT/prov-$label.txt" 2>&1
  cat "$OUT/prov-$label.txt"
}

engine_scan() {  # label phase; appends one scan block to engine-<label>.txt
  local label="$1" phase="$2" pid
  {
    echo "== $(date -u +%FT%TZ) $label EngineCore scan ($phase)"
    echo "lib: $(sha256sum "$CORE" 2>/dev/null)"
    local found=0
    for pid in $(pgrep -f "EngineCore" 2>/dev/null); do
      echo "pid $pid comm=$(cat "/proc/$pid/comm" 2>/dev/null)"
      echo "argv: $(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-200)"
      local m
      m="$(grep -m1 'libbolorgir' "/proc/$pid/maps" 2>/dev/null || true)"
      if [ -n "$m" ]; then
        echo "mapped=1"
        echo "maps: $m"
        found=1
      else
        echo "mapped=0"
      fi
    done
    echo "bolorgir_mapped=$found"
  } >> "$OUT/engine-$label.txt"
}

engine_mapped_count() { grep -c '^bolorgir_mapped=1$' "$OUT/engine-$1.txt" 2>/dev/null || true; }

session() {  # label backend
  local label="$1" backend="$2" cfg rc i cpid nrec expected n
  : > "$OUT/engine-$label.txt"
  if [ "$backend" = xgrammar ]; then
    cfg='{"backend": "xgrammar", "disable_any_whitespace": true}'
    start_server "$label" "$cfg" || exit 2
    grep -q "disable_any_whitespace=True" "$OUT/server-$label.log" \
      || { echo "== $label: server does not run disable_any_whitespace=true"; exit 2; }
  else
    cfg='{"backend": "bolorgir"}'
    start_server "$label" "$cfg" PYTHONPATH="$ROOT/python" \
      "BLG_AGENT_TRACE=$OUT/trace-$label.jsonl" || exit 2
  fi
  prov "$label" || exit 2
  echo "== $label client start $(date -u +%FT%TZ)"
  ( cd "$ROOT" && exec "$PY" "$SCRIPT_DIR/xgrammar_bench2.py" --session "$label" \
      --backend "$backend" --out "$OUT/raw-$label.jsonl" --repeats "$REPEATS" ) &
  cpid=$!
  for i in $(seq 1 240); do
    [ -s "$OUT/raw-$label.jsonl" ] && break
    kill -0 "$cpid" 2>/dev/null || break
    sleep 0.5
  done
  engine_scan "$label" "mid-session"
  wait "$cpid"; rc=$?
  engine_scan "$label" "post-session"
  nrec="$(wc -l < "$OUT/raw-$label.jsonl" 2>/dev/null || echo 0)"
  stop_server "$label" || exit 2
  [ "$rc" = 0 ] || { echo "== $label client failed rc=$rc"; exit 2; }
  expected=$((REPEATS * 3 * 169))
  [ "$nrec" = "$expected" ] || { echo "== $label has $nrec records, expected $expected"; exit 2; }
  n="$(engine_mapped_count "$label")"
  if [ "$backend" = bolorgir ]; then
    [ "$n" -ge 2 ] || { echo "== $label: core not mapped in both EngineCore scans"; exit 2; }
  else
    [ "$n" -eq 0 ] || { echo "== $label: core mapped under xgrammar (negative control failed)"; exit 2; }
  fi
  echo "== $label done $(date -u +%FT%TZ)"
}

session "${LABELS[0]}" xgrammar
session "${LABELS[1]}" bolorgir
session "${LABELS[2]}" xgrammar
session "${LABELS[3]}" bolorgir

echo "== manifest"
"$PY" - "$ROOT" "$OUT" "$REPEATS" "$PY" "${LABELS[@]}" <<'EOF' || exit 2
import hashlib, json, os, re, subprocess, sys
root, out, repeats, py = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
labels = sys.argv[5:]
def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()
def outb(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout or r.stderr).strip()
def parse_engine(path):
    scans, cur = [], None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        m = re.match(r"^== .* scan \((.*)\)$", line)
        if m:
            cur = {"phase": m.group(1), "lib_sha256": None, "processes": [], "mapped": None}
            scans.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("lib: "):
            toks = line.split()
            cur["lib_sha256"] = toks[1] if len(toks) > 1 else None
        elif line.startswith("pid "):
            mm = re.match(r"pid (\d+) comm=(.*)$", line)
            cur["processes"].append({"pid": int(mm.group(1)), "comm": mm.group(2), "mapped": False})
        elif line.startswith("mapped=1"):
            if cur["processes"]:
                cur["processes"][-1]["mapped"] = True
        elif line.startswith("maps: "):
            if cur["processes"]:
                cur["processes"][-1]["maps_line"] = line[6:]
        elif line.startswith("bolorgir_mapped="):
            cur["mapped"] = int(line.split("=")[1])
    return scans
man = {
  "about": "Compact-JSON re-collection of the T10 section-10 comparison "
           "(tools/run_compact.sh, standalone; amendments A3/A4)",
  "sessions": labels,
  "repeats": repeats,
  "core": {"path": "python/bolorgir/_lib/libbolorgir.so.0.1.0",
           "sha256": sha(os.path.join(root, "python/bolorgir/_lib/libbolorgir.so.0.1.0"))},
  "adapter": {"path": "python/bolorgir/vllm.py",
              "sha256": sha(os.path.join(root, "python/bolorgir/vllm.py"))},
  "vllm_version": outb([py, "-c", "from importlib.metadata import version; print(version('vllm'))"]),
  "xgrammar_version": outb([py, "-c", "from importlib.metadata import version; print(version('xgrammar'))"]),
  "model": "Qwen/Qwen2.5-0.5B-Instruct@7ae557604adf67be50417f59c2c2f167def9a775",
  "patch": None, "server_argv": {}, "engine": {},
  "records_per_session": {},
}
for d in ("integrations/vllm",):
    for f in sorted(os.listdir(os.path.join(root, d))):
        if f.endswith(".patch"):
            man["patch"] = {"path": f"{d}/{f}", "sha256": sha(os.path.join(root, d, f))}
for s in labels:
    p = os.path.join(out, f"prov-{s}.txt")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            if line.startswith("argv:"):
                man["server_argv"][s] = line[5:].strip()
    e = os.path.join(out, f"engine-{s}.txt")
    if os.path.exists(e):
        man["engine"][s] = parse_engine(e)
    r = os.path.join(out, f"raw-{s}.jsonl")
    if os.path.exists(r):
        man["records_per_session"][s] = sum(1 for ln in open(r, encoding="utf-8") if ln.strip())
with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(man, f, indent=1)
    f.write("\n")
print(json.dumps({k: man[k] for k in ("sessions", "core", "vllm_version",
                                      "xgrammar_version", "records_per_session")}, indent=1))
EOF

echo "== report"
"$PY" "$SCRIPT_DIR/xgrammar_report.py" "$OUT"/raw-*.jsonl --repeats "$REPEATS" \
    --out-json "$OUT/summary-v2.json" --out-md "$OUT/REPORT-v2.md" || exit 2
echo "== run_compact done"
