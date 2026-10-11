#!/bin/bash
# build_twice.sh REV EXPECT_SHA256 WORK: the release core built twice from
# `git archive REV` (a committed release line), unpacked into two
# directories WORK/a and WORK/b, each with the release recipe of the
# composition in REV (core.flags of its newest
# docs/release/v*-composition.json) and its own local and global Zig
# cache:
#   zig build <core.flags> --prefix out
# Before the builds, the blob ids of the core and binding files in REV
# are compared with the composition's core.manifest and bindings.manifest
# pins. Then cmp of the two
# libraries, the comparison with EXPECT_SHA256 (the measured candidate:
# equal flags do not replace equal bytes), and the library checks of
# release.yml: no debug sections, no blg_test_* export, the glibc maximum,
# release_gate.py abi against the exports (with and without --base-only),
# every base function exported and no later-line function.
# Env: ZIG (default zig on PATH; the composition's Zig version is
# printed for comparison), BUILD_MEM_LIMIT_MB (16384).
# Exit: 0 all hold; 3 the builds differ from each other or from EXPECT;
# 4 an ABI, export or pin check failed; 1 a failed build; 2 a refused
# setup.
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
. "$ROOT/benchmarks/protected_paths.sh"
rev="${1:?revision}"; expect="${2:?expected sha256}"; WORK="${3:?work dir}"
[[ "$expect" =~ ^[0-9a-f]{64}$ ]] || { echo "refused: EXPECT_SHA256 is not a sha256"; exit 2; }
ZIG="${ZIG:-$(command -v zig || true)}"
[ -x "$ZIG" ] || { echo "refused: no zig (set ZIG)"; exit 2; }
full="$(git -C "$ROOT" rev-parse --verify "$rev^{commit}")" || { echo "refused: no commit $rev"; exit 2; }
comp="$(git -C "$ROOT" ls-tree --name-only "$full" docs/release/ | grep -E 'docs/release/v[0-9.]+-composition\.json$' | sort -V | tail -n 1)"
[ -n "$comp" ] || { echo "refused: no composition in $full"; exit 2; }
blg_guard_new_dir "$ROOT" "$WORK" || exit 2
blg_memory_limit "${BUILD_MEM_LIMIT_MB:-16384}" || exit 2
mkdir -p "$WORK" && WORK="$(cd "$WORK" && pwd)" || exit 2
git -C "$ROOT" show "$full:$comp" > "$WORK/composition.json" || exit 2
read -r -a FLAGS < <(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["core"]["flags"])' "$WORK/composition.json")
[ "${#FLAGS[@]}" -gt 0 ] || { echo "refused: no core.flags in $comp"; exit 2; }
echo "commit $full; $comp; flags ${FLAGS[*]}; $("$ZIG" version)"
python3 - "$WORK/composition.json" "$ROOT" "$full" <<'EOF' || exit 4
import json, subprocess, sys
comp, root, rev = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
pins = {**comp["core"]["manifest"], **comp["bindings"]["manifest"]}
bad = 0
for path, pin in sorted(pins.items()):
    got = subprocess.run(["git", "-C", root, "rev-parse", f"{rev}:{path}"], capture_output=True,
                         text=True).stdout.strip()
    print(f"pin {path} {got} {'ok' if got == pin else 'DIFFERS from ' + pin}")
    bad |= got != pin
sys.exit(1 if bad else 0)
EOF
for c in a b; do
  mkdir "$WORK/$c" && git -C "$ROOT" archive "$full" | tar -x -C "$WORK/$c" || exit 2
  (cd "$WORK/$c" && ZIG_GLOBAL_CACHE_DIR="$WORK/$c/.zig-global" \
     timeout 1200 "$ZIG" build "${FLAGS[@]}" --prefix out \
     --cache-dir "$WORK/$c/.zig-cache") \
     > "$WORK/build-$c.log" 2>&1 || { echo "build $c failed"; tail "$WORK/build-$c.log"; exit 1; }
  sha256sum "$WORK/$c/out/lib/libbolorgir.so.0.1.0"
done
A="$WORK/a/out/lib/libbolorgir.so.0.1.0"; B="$WORK/b/out/lib/libbolorgir.so.0.1.0"
if cmp "$A" "$B"; then echo "== the two builds are identical"; else echo "== the two builds DIFFER"; exit 3; fi
got="$(sha256sum "$A" | cut -d' ' -f1)"
if [ "$got" = "$expect" ]; then echo "== equal to the expected core $expect"
else echo "== DIFFERS from the expected core: $got != $expect"; exit 3; fi
if readelf -S "$A" | grep -q '\.debug_'; then echo "== debug sections present"; exit 4; fi
echo "== no debug sections"
if nm -D "$A" | grep blg_test_; then echo "== blg_test_* exported"; exit 4; fi
echo "== no blg_test_* export"
objdump -T "$A" | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -n 1 | sed 's/^/== glibc max /'
cd "$WORK/a" || exit 2
python3 .github/scripts/release_gate.py abi --composition "$comp" --lib "$A" || exit 4
python3 .github/scripts/release_gate.py abi --base-only --composition "$comp" --lib "$A" || exit 4
python3 - "$A" "$comp" <<'EOF' || exit 4
import json, subprocess, sys
comp = json.load(open(sys.argv[2]))
out = subprocess.run(["nm", "-D", "--defined-only", sys.argv[1]], capture_output=True, text=True,
                     check=True).stdout
exp = {f.split()[-1] for f in out.splitlines() if len(f.split()) >= 3 and f.split()[-1].startswith("blg_")}
base, later = set(comp["abi"]["base_functions"]), set(comp["abi"]["later_line_functions"])
print("== exports", len(exp), "base", len(base), "missing base:", sorted(base - exp),
      "later-line exported:", sorted(later & exp), "other:", sorted(exp - base - later))
sys.exit(0 if base <= exp and not later & exp else 1)
EOF
echo "== build_twice ok: $got"
