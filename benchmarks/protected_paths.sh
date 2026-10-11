# Paths that checks and measurements must never write:
# the frozen candidate in zig-out, the acceptance run directories, the
# main .venv and the package in the main tree. Sourced by
# benchmarks/t3/isolated_tests.sh, benchmarks/t3/r6_review.sh and
# benchmarks/maskbench/t8a/run_t8a.sh.

# blg_memory_limit MB: an address-space limit (ulimit -v) of MB for this
# shell and every process it starts, each on its own; 0 = none. The limit
# bounds a runaway process (for example an ECMA regex reference inside one
# pytest process): it fails its step instead of taking all the memory of
# the machine and freezing it. A cgroup limit (systemd-run --scope) is
# not available inside the editor sandbox. Torch, the Zig compiler and
# the tests peak far below the default of 16384 MB.
blg_memory_limit() {
  local mb="$1"
  case "$mb" in ''|*[!0-9]*) echo "memory limit '$mb' is not a number of MB" >&2; return 2 ;; esac
  [ "$mb" = 0 ] && return 0
  ulimit -v $((mb * 1024)) || { echo "ulimit -v $((mb * 1024)) failed" >&2; return 2; }
}

# blg_protected_paths ROOT: one protected path per line.
blg_protected_paths() {
  local root="$1" d
  printf '%s\n' "$root/zig-out" "$root/.venv" "$root/python/bolorgir" "$root/python/build"
  for d in "$root"/benchmarks/local/t3/acc-*; do
    [ -e "$d" ] && printf '%s\n' "$d"
  done
  return 0
}

# blg_guard_out ROOT PATH: status 2 when PATH, after resolving symlinks,
# lies inside a protected path or contains one.
blg_guard_out() {
  local root="$1" out p rp
  out="$(realpath -m -- "$2")" || return 2
  while IFS= read -r p; do
    rp="$(realpath -m -- "$p")"
    case "$out/" in
      "$rp"/*) echo "refused: $2 resolves into the protected path $rp" >&2; return 2 ;;
    esac
    case "$rp/" in
      "$out"/*) echo "refused: $2 contains the protected path $rp" >&2; return 2 ;;
    esac
  done < <(blg_protected_paths "$root")
  return 0
}

# blg_guard_new_dir ROOT PATH: blg_guard_out, and PATH must not exist or
# be an empty directory.
blg_guard_new_dir() {
  blg_guard_out "$1" "$2" || return 2
  if [ -e "$2" ] && [ -n "$(ls -A -- "$2" 2>/dev/null)" ]; then
    echo "refused: $2 exists and is not empty" >&2
    return 2
  fi
  return 0
}

# blg_fingerprint ROOT: a digest of the file list, sizes, mtimes and link
# targets of every protected path, plus the content sha256 of the frozen
# candidate library. Two equal outputs mean nothing was written there.
blg_fingerprint() {
  local root="$1" p f
  while IFS= read -r p; do
    if [ -e "$p" ]; then
      printf '%s %s\n' "$(find "$p" -printf '%P %s %T@ %l\n' 2>/dev/null | LC_ALL=C sort \
        | sha256sum | cut -c1-64)" "${p#"$root"/}"
    else
      printf 'absent %s\n' "${p#"$root"/}"
    fi
  done < <(blg_protected_paths "$root")
  f="$root/zig-out/lib/libbolorgir.so.0.1.0"
  if [ -f "$f" ]; then
    printf '%s content:%s\n' "$(sha256sum "$f" | cut -c1-64)" "${f#"$root"/}"
  fi
  return 0
}
