#!/bin/bash
# build_native.sh: a diagnostic build for R11-A2, never a candidate: the
# source of the candidate 9a5baf3e (git archive $REV) built with the
# release recipe of the composition except the CPU target,
#   zig build -Drelease=true -Dstrip=true -Dtarget=x86_64-linux-gnu.2.28
#             -Dcpu=native --prefix out
# (the candidate: -Dcpu=baseline), into d4f/native-9a5baf3e with its own
# local and global cache - not zig-out, not the main tree, nothing in the
# run. Then: the library differs from the candidate, uses the host's
# vector instructions (ymm registers, popcnt; the baseline build has
# none), exports the same blg_* functions as the candidate. Records
# out/SHA256 and build.json. Address space 16 GiB, 20 min. Exit: 0 built
# and checked; 1 build failed; 3 a check failed; 2 refused.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/common.sh"
FLAGS="-Drelease=true -Dstrip=true -Dtarget=x86_64-linux-gnu.2.28 -Dcpu=native"
N="$D4F/native-9a5baf3e"
ulimit -v 16777216
[ -e "$N" ] && { echo "refused: $N exists"; exit 2; }
check_lib "$LIB_CAND" "$CAND_SHA" || exit 2
[ "$(git -C "$ROOT" rev-parse --verify "$REV^{commit}")" = "$REV" ] || { echo "refused: no commit $REV"; exit 2; }
"$ZIG" version || exit 2
mkdir "$N" && git -C "$ROOT" archive "$REV" | tar -x -C "$N" || exit 2
mkdir -p "$D4F/logs"
# shellcheck disable=SC2086
(cd "$N" && ZIG_GLOBAL_CACHE_DIR="$N/.zig-global" timeout 1200 "$ZIG" build $FLAGS --prefix out \
   --cache-dir "$N/.zig-cache") \
   > "$D4F/logs/native-build.log" 2>&1 || { echo "build failed"; tail "$D4F/logs/native-build.log"; exit 1; }
L="$N/out/lib/libbolorgir.so.0.1.0"
sha="$(sha256sum "$L" | cut -d' ' -f1)"
echo "$sha  out/lib/libbolorgir.so.0.1.0" > "$N/out/SHA256"
bad=0
[ "$sha" != "$CAND_SHA" ] || { echo "CHECK: the native build equals the candidate"; bad=1; }
vec() { objdump -d --no-show-raw-insn "$1" | grep -cE 'ymm|\bpopcnt\b'; }
nv="$(vec "$L")"; cv="$(vec "$LIB_CAND")"
echo "vector/popcnt instructions: native $nv, candidate $cv"
[ "$nv" -gt 0 ] && [ "$cv" = 0 ] || { echo "CHECK: expected host vector instructions in the native build only"; bad=1; }
cmp -s <(nm -D --defined-only "$L" | grep -o 'blg_[a-z_0-9]*' | sort) \
       <(nm -D --defined-only "$LIB_CAND" | grep -o 'blg_[a-z_0-9]*' | sort) \
  || { echo "CHECK: the exports differ from the candidate's"; bad=1; }
python3 - "$N/build.json" "$REV" "$FLAGS" "$sha" "$nv" "$CAND_SHA" "$cv" "$("$ZIG" version)" <<'EOF'
import json, sys
p, rev, flags, sha, nv, cand, cv, zv = sys.argv[1:]
json.dump({"about": "R11-A2 diagnostic build: the candidate 9a5baf3e's source with the host CPU target; not a candidate",
           "source": f"git archive {rev}", "flags": flags, "zig": zv, "library_sha256": sha,
           "vector_instructions": int(nv),
           "candidate": {"library_sha256": cand, "flags_cpu": "baseline", "vector_instructions": int(cv)}},
          open(p, "w"), indent=1)
EOF
echo "native build $sha"
[ "$bad" = 0 ] || exit 3
