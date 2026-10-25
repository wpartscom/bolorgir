"""Diagnostic counters of the mask paths, read from the loaded core.

Mirrors `src/diag.zig` (`Counters` / `order`) and the two C exports
`blg_diag_reset`, `blg_diag_counters`. The counters are process-global in
the shared library, so this only works when the process already loaded the
very same `libbolorgir.so` (the `bolorgir` package does); the CDLL handle
is expected to resolve to the same mapping, which `selftest()` checks by
resetting and requiring the read-back to be zero.
"""

import ctypes
import os

ORDER = (
    "fill_calls", "fast_calls", "fast_used",
    "refuse_not_uniform", "refuse_byte_incomplete", "refuse_content_dep",
    "exact_walk_calls", "trie_nodes", "trie_terminals",
    "state_alive_calls", "alive_dead", "content_masks",
    "content_memcpy", "content_word_loop", "content_cache_hits",
    "exact_tokens", "name_used", "refuse_name_state",
)


def load():
    import bolorgir
    pkg = os.path.dirname(os.path.abspath(bolorgir.__file__))
    # the exact soname the extension requests: glibc then resolves
    # the same mapping instead of loading a second copy
    path = os.path.join(pkg, "_lib", "libbolorgir.so.0")
    lib = ctypes.CDLL(path)
    lib.blg_diag_counters.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t]
    lib.blg_diag_counters.restype = ctypes.c_size_t
    lib.blg_diag_reset.restype = None
    return lib


def read(lib):
    buf = (ctypes.c_uint64 * len(ORDER))()
    n = lib.blg_diag_counters(buf, len(ORDER))
    if n != len(ORDER):
        raise RuntimeError(f"the core returned {n} counters, expected {len(ORDER)}: "
                           "the scratch package is not the instrumented build")
    return dict(zip(ORDER, buf))


def selftest(lib):
    lib.blg_diag_reset()
    z = read(lib)
    bad = {k: v for k, v in z.items() if v}
    if bad:
        raise RuntimeError("counters are not shared with the loaded core "
                           f"(non-zero right after reset: {bad})")
    return True
