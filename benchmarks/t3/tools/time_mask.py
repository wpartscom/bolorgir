#!/usr/bin/env python3
"""Diagnostic: time one fill_mask at a byte prefix (hex file) with the
Llama tokenizer; prints status, time, counters, search count and, with a
hook library, the thread count of the state.

Usage: time_mask.py <schema> <prefix.hex>
"""
import ctypes
import json
import os
import sys
import time

os.environ.setdefault("BOLORGIR_COVERAGE_PROFILE", "spec-v1")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))
sys.path.insert(0, os.path.join(ROOT, "benchmarks/local/t3"))

import bench_jsb_coverage as h  # noqa: E402
from replay_subset import und_hist  # noqa: E402

path, hexfile = sys.argv[1], sys.argv[2]
prefix = bytes.fromhex(open(hexfile).read().strip())

raw = json.load(open(path))
data = json.dumps(raw["schema"] if "schema" in raw and "tests" in raw else raw).encode()

gt = h.GenTokenizer("llama-3.1")
ctx = h.make_context(gt.spec)
g = h.blg.compile_schema(ctx, data, profile=b"spec-v1")

lib = h.blg.LIB
if hasattr(lib, "blg_test_dump_state"):
    lib.blg_test_dump_state.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
                                        ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    buf = ctypes.create_string_buffer(1 << 20)
    n = ctypes.c_size_t(0)
    rc = lib.blg_test_dump_state(g, prefix, len(prefix), buf, len(buf), ctypes.byref(n))
    text = buf.raw[:n.value].decode("utf-8", "replace")
    print("dump rc", rc, "threads", len(text.strip().split("\n")) if text.strip() else 0)
    print(text[:800])

s = h.blg.Session(ctx, g)
for b in prefix:
    r = s.accept(gt.byte_ids[b])
    if r:
        print("prefix rejected at byte", b, "status", r)
        s.destroy()
        ctx.destroy()
        sys.exit(1)
cert_n = h.blg.LIB.blg_cert_stats(None, 0)
arr = (h.blg.ctypes.c_uint64 * (cert_n + 2))()
h.blg.LIB.blg_cert_stats_reset()
t0 = time.monotonic()
st, words = s.fill_mask_words()
dt = time.monotonic() - t0
h.blg.LIB.blg_cert_stats(arr, cert_n)
print("fill status", st, f"{dt:.3f}s", "allowed", sum(bin(w).count("1") for w in words))
print("counters", und_hist())
print("search_entered", arr[cert_n - 1])
s.destroy()
ctx.destroy()
