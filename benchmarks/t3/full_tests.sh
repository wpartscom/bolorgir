#!/bin/bash
# Full T3 integration run: Zig fmt and unit tests, release and test-hook
# builds, both pytest backends, and the two hook tests (probe, fail_row)
# required to run and not skip. Usage: full_tests.sh <log> [pytest args...]
#
# Env overrides: ZIG (default: zig from PATH), PY (default: .venv/bin/python
# when present, else python3), HF_HOME (default: benchmarks/local/hf-cache),
# HF_HUB_OFFLINE (default: 1).
#
# The first failing command makes the run exit with its status and
# suppresses the final success line.
#
# This runner builds into zig-out/ and python/bolorgir/ of this tree. It
# refuses to run while zig-out/lib holds the library of an acceptance run
# (benchmarks/local/t3/acc-*/identity/lib.sha256): checks of new code go
# through benchmarks/t3/isolated_tests.sh.
set -u -o pipefail
cd "$(dirname "$0")/../.." || exit 1

if [ -f zig-out/lib/libbolorgir.so.0.1.0 ]; then
  cur="$(sha256sum zig-out/lib/libbolorgir.so.0.1.0 | cut -d' ' -f1)"
  for f in benchmarks/local/t3/acc-*/identity/lib.sha256; do
    [ -f "$f" ] || continue
    if [ "$(cut -d' ' -f1 "$f")" = "$cur" ]; then
      echo "refused: zig-out/lib holds the frozen candidate of ${f%/identity/lib.sha256};" \
        "use benchmarks/t3/isolated_tests.sh" >&2
      exit 2
    fi
  done
fi

ZIG="${ZIG:-$(command -v zig || true)}"
if [ -z "$ZIG" ]; then echo "zig not found; set ZIG" >&2; exit 1; fi
PY="${PY:-}"
if [ -z "$PY" ]; then
  if [ -x "$PWD/.venv/bin/python" ]; then PY="$PWD/.venv/bin/python"; else PY="$(command -v python3 || true)"; fi
fi
if [ -z "$PY" ]; then echo "python not found; set PY" >&2; exit 1; fi
export HF_HOME="${HF_HOME:-$PWD/benchmarks/local/hf-cache}" HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"

L="${1:?usage: full_tests.sh <log>}"
shift || true
: > "$L"

step() {
  local name="$1"; shift
  echo "== $name" >> "$L"
  if "$@" >> "$L" 2>&1; then
    echo "== exit 0" >> "$L"
  else
    local rc=$?
    echo "== exit $rc" >> "$L"
    echo "== FAILED: $name (exit $rc)" | tee -a "$L"
    exit "$rc"
  fi
}

step fmt "$ZIG" fmt --check src build.zig
step zigtest timeout 5400 "$ZIG" build test --summary all
step release "$ZIG" build -Drelease=true
step hooks "$ZIG" build -Drelease=true -Dtest_hooks=true --prefix zig-out/test-hooks

echo "== build_ext" >> "$L"
if (cd python && BLG_SKIP_ZIG=1 "$PY" setup.py build_ext --inplace) >> "$L" 2>&1; then
  echo "== build_ext exit 0" >> "$L"
else
  rc=$?
  echo "== build_ext exit $rc" >> "$L"
  echo "== FAILED: build_ext (exit $rc)" | tee -a "$L"
  exit "$rc"
fi
step synced cmp zig-out/lib/libbolorgir.so.0.1.0 python/bolorgir/_lib/libbolorgir.so.0.1.0
echo "== sha256 $(sha256sum zig-out/lib/libbolorgir.so.0.1.0 | cut -d' ' -f1)" >> "$L"

export BLG_TEST_HOOKS_LIB="$PWD/zig-out/test-hooks/lib/libbolorgir.so.0.1.0"
export PYTHONPATH=python
J="$L.hooks.xml"
step "hooks tests" "$PY" -m pytest -q -p no:cacheprovider --junit-xml="$J" \
  "tests/test_probe.py::test_probe_found_and_inconclusive_with_hooks" \
  "python/tests/test_hf_adapters.py::test_fail_row_with_unknown_forced_by_the_test_hook"
step "hooks no-skips" "$PY" -c "import sys, xml.etree.ElementTree as ET; s = ET.parse(sys.argv[1]).getroot().find('testsuite'); print(dict(s.attrib)); assert int(s.get('tests', '0')) >= 2; assert s.get('skipped', '0') == '0'; assert s.get('failures', '0') == '0'; assert s.get('errors', '0') == '0'" "$J"

for b in ctypes package; do
  step "pytest $b" env BLG_TEST_BACKEND=$b timeout 5400 "$PY" -m pytest tests/ python/tests/ -q -p no:cacheprovider "$@"
done
echo "== done" >> "$L"
