# common.sh: settings of the D4 round of the candidate 9a5baf3e (the T3
# acceptance run acc-9a5baf3e). Sourced by the d4f scripts. Read-only over
# the earlier rounds' material; nothing here rebuilds or edits the tree.
ROOT="/home/gm/Рабочий стол/сайты/LLM_trainer/zig"
DE8="$ROOT/benchmarks/local/t3/de8"
D4F="$DE8/d4f"
RUN="$ROOT/benchmarks/local/t3/acc-9a5baf3e"          # the candidate run: harness, venv, hf-cache, lib
RUN_OLD="$ROOT/benchmarks/local/t3/acc-6e62e0d1"      # the previous candidate 6e62e0d1 (T3 accepted)
ZIG="$ROOT/benchmarks/local/toolchain/zig-x86_64-linux-0.17.0/zig"
REV=003f1b3e603afdc0dad01dd7c2bd5ac5de4b0c64          # the run's git_head = the source of the candidate
CAND_SHA=9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41
PREV_SHA=6e62e0d1526c6e5369457d2f7205675b7cb163e3374b96d8cb5115c28873e914
LIB_CAND="$RUN/lib/libbolorgir.so.0.1.0"
LIB_PREV="$RUN_OLD/lib/libbolorgir.so.0.1.0"
LIB_NATIVE="$D4F/native-9a5baf3e/out/lib/libbolorgir.so.0.1.0"
LIB_50="$ROOT/benchmarks/local/t3/acc-50cbd80e/lib/libbolorgir.so.0.1.0"
SHA_50=50cbd80e874eac5d2b850637b6c34867315d5b3c00e8a751f6ec39623de91440
LIB_73="$ROOT/benchmarks/local/libs/final/libbolorgir.so.0.1.0"
SHA_73=7345490f7ddf5710ec438f0e4baf4bf98b330a61cb6663be045580da95fa56c8
LIB_DBC="$ROOT/benchmarks/local/libs/dbc5eeb/libbolorgir.so.0.1.0"
SHA_DBC=8ff970d46cce8b47248ab3e0b4ed5404bb127ec733b80e649bf1d46e80bb461f
VPY="$RUN/venv/bin/python"
TREE="$RUN/tree"
export PYTHONDONTWRITEBYTECODE=1

# check_lib PATH SHA256 (separate arguments: the paths hold spaces)
check_lib() {
  local got
  got="$(sha256sum -- "$1" 2>/dev/null | cut -d' ' -f1)"
  [ -n "$got" ] || { echo "refused: cannot read $1"; return 2; }
  [ "$got" = "$2" ] || { echo "refused: $1 is $got, not $2"; return 2; }
}
