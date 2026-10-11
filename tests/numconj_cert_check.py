"""Cross-check of the ADR-0011 joint numeric certificate.

Runs against a core built with `zig build -Dtest_hooks=true` (BLG_LIB_PATH
points at it). For every schema and every number prefix up to MAX_LEN
bytes over a reduced alphabet:

- verdict `alive`: the certificate's witness continuation must complete the
  prefix to a JSON number whose exact value satisfies the conjunction
  (exact decimals, tests/exact_numbers.py), and the reference
  (tests/reference.py) must agree;
- verdict `dead`: the reference must agree, and a bounded parser search
  (no certificate involved, up to --search-len more bytes) must find no
  completion (sampled per schema; the search is exponential in its depth,
  so keep --search-len small);
- `undecided` is a failure for these conjunctions.

Prints a JSON summary; exit status 1 on any failure.
"""

from __future__ import annotations

import ctypes
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import blg_ctypes as zg  # noqa: E402
from exact_numbers import Num  # noqa: E402
from reference import NumConj, TokenizerSpec, numconj_reachable  # noqa: E402

DEAD, ALIVE, UNDECIDED, NOT_GROUP, REJECTED = range(5)

TABLE_SCHEMAS = [
    {"type": "number", "minimum": 1, "maximum": 9},
    {"type": "number", "maximum": 9},
    {"type": "integer", "minimum": 1, "maximum": 9},
    {"type": "integer", "maximum": 9},
    {"type": "number", "multipleOf": 1, "minimum": 1, "maximum": 9},
    {"type": "integer", "minimum": 0, "maximum": 1},
    {"type": "integer", "minimum": -9},
    {"type": "number", "multipleOf": 0.5, "maximum": 2},
    {"type": "integer", "not": {"const": 5}, "minimum": 1, "maximum": 9},
    {"type": "integer", "minimum": 1},
    {"type": "integer", "exclusiveMinimum": 0},
]

CONSTS = ["0", "1", "2", "5", "9", "10", "15", "25", "3", "0.5", "1.5",
          "0.25", "2.5", "100", "0.1", "12", "7.5"]
DIVISORS = [1, 2, 3, 5, 7, 10, 25, 0.5, 0.25, 1.5, 0.3, 0.2, 0.1, 4, 2.5]


def _const(rng):
    c = rng.choice(CONSTS)
    if c != "0" and rng.random() < 0.4:
        c = "-" + c
    return json.loads(c)


def random_schema(rng) -> dict:
    s = {"type": rng.choice(["integer", "number", "number"])}
    if rng.random() < 0.85:
        a, b = sorted([_const(rng), _const(rng)])
        if rng.random() < 0.8:
            s["exclusiveMinimum" if rng.random() < 0.3 else "minimum"] = a
        if rng.random() < 0.8:
            s["exclusiveMaximum" if rng.random() < 0.3 else "maximum"] = b
    if rng.random() < 0.55:
        s["multipleOf"] = rng.choice(DIVISORS)
    if rng.random() < 0.35:
        cs = [_const(rng) for _ in range(rng.randint(1, 2))]
        s["not"] = {"const": cs[0]} if len(cs) == 1 else {"enum": cs}
    if rng.random() < 0.08:
        s = {"type": s["type"], "const": _const(rng), "allOf": [s]}
    elif rng.random() < 0.12:
        s = {"type": s["type"], "allOf": [s, {"maximum": _const(rng)}]}
    return s


def _bind(lib):
    lib.blg_test_num_cert.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_char_p, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t)]
    lib.blg_test_num_cert.restype = ctypes.c_uint32
    lib.blg_test_bounded_search.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    lib.blg_test_bounded_search.restype = ctypes.c_uint32


def num_cert(grammar, prefix: bytes):
    verdict = ctypes.c_uint32()
    buf = ctypes.create_string_buffer(8192)
    wl = ctypes.c_size_t()
    st = zg.LIB.blg_test_num_cert(grammar, prefix, len(prefix), ctypes.byref(verdict),
                                  buf, len(buf), ctypes.byref(wl))
    if st != 0:
        raise RuntimeError(f"blg_test_num_cert status {st} at {prefix!r}")
    return verdict.value, buf.raw[: wl.value]


def bounded_search(grammar, prefix: bytes, max_len: int, max_exp: int, max_nodes: int = 0):
    """(found, continuation); found is None when the hook's node budget ran
    out before the search space was exhausted."""
    found = ctypes.c_uint32()
    buf = ctypes.create_string_buffer(256)
    wl = ctypes.c_size_t()
    st = zg.LIB.blg_test_bounded_search(grammar, prefix, len(prefix), max_len, max_exp, max_nodes,
                                        ctypes.byref(found), buf, len(buf), ctypes.byref(wl))
    if st == zg.BLG_ERR_RESOURCE_LIMIT:
        return None, b""
    if st != 0:
        raise RuntimeError(f"blg_test_bounded_search status {st}")
    return bool(found.value), buf.raw[: wl.value]


def byte_tokenizer() -> TokenizerSpec:
    tokens = tuple(bytes([i]) for i in range(256)) + (b"", b"")
    return TokenizerSpec(tokens=tokens, eos_ids=(256,), special_ids=(257,)).validate()


def check_schema(ctx, schema, *, alphabet: bytes, max_len: int, search_len: int,
                 search_exp: int, search_budget: int, search_samples: int, rng, stats,
                 failures):
    conj = NumConj.from_schema(schema)
    try:
        g = zg.compile_schema(ctx, schema, profile=b"spec-v1")
    except zg.CoreFailure as e:
        stats["compile_" + zg.STATUS_NAMES[e.status]] = stats.get("compile_" + zg.STATUS_NAMES[e.status], 0) + 1
        ref = numconj_reachable("", conj)
        if e.status != zg.BLG_ERR_UNSATISFIABLE_CONSTRAINT or ref[0]:
            failures.append({"schema": schema, "prefix": "", "issue": "compile refused",
                             "status": zg.STATUS_NAMES[e.status], "reference": bool(ref[0])})
        return
    dead_prefixes = []
    try:
        stack = [b""]
        while stack:
            p = stack.pop()
            try:
                v, wit = num_cert(g, p)
            except RuntimeError as ex:
                failures.append({"schema": schema, "prefix": p.decode(), "issue": str(ex)})
                continue
            stats["prefixes"] = stats.get("prefixes", 0) + 1
            ref = numconj_reachable(p, conj)
            if v == REJECTED:
                if ref is not None and ref[0]:
                    failures.append({"schema": schema, "prefix": p.decode(), "issue": "parser rejected a live prefix"})
                continue
            if ref is None:
                failures.append({"schema": schema, "prefix": p.decode(), "issue": "parser accepted a non-number prefix"})
                continue
            if v == UNDECIDED:
                failures.append({"schema": schema, "prefix": p.decode(), "issue": "undecided"})
                continue
            if v == ALIVE:
                stats["alive"] = stats.get("alive", 0) + 1
                if not ref[0]:
                    failures.append({"schema": schema, "prefix": p.decode(), "issue": "alive vs reference dead",
                                     "witness": wit.decode()})
                full = (p + wit).decode()
                try:
                    ok = conj.satisfies_num(Num(full))
                except ValueError as ex:
                    ok = False
                    full += f" ({ex})"
                if not ok:
                    failures.append({"schema": schema, "prefix": p.decode(), "issue": "witness fails",
                                     "witness": full})
            elif v == DEAD:
                stats["dead"] = stats.get("dead", 0) + 1
                if ref[0]:
                    failures.append({"schema": schema, "prefix": p.decode(), "issue": "dead vs reference alive",
                                     "reference_value": str(ref[1])})
                dead_prefixes.append(p)
                continue
            else:
                stats["not_group"] = stats.get("not_group", 0) + 1
            if len(p) < max_len:
                for b in alphabet:
                    stack.append(p + bytes([b]))
        for p in rng.sample(dead_prefixes, min(search_samples, len(dead_prefixes))):
            found, cont = bounded_search(g, p, search_len, search_exp, search_budget)
            key = "search_budget" if found is None else "searched"
            stats[key] = stats.get(key, 0) + 1
            if found:
                failures.append({"schema": schema, "prefix": p.decode(), "issue": "search found a completion",
                                 "continuation": cont.decode()})
    finally:
        zg.grammar_release(g)


_WORKER: dict = {}


def _worker_init():
    _bind(zg.LIB)
    _WORKER["ctx"] = zg.Context(byte_tokenizer(), zg.BLG_MODE_LAZY)


def _run_one(job):
    i, schema, opts = job
    stats: dict = {}
    failures: list = []
    t0 = time.monotonic()
    check_schema(_WORKER["ctx"], schema, rng=random.Random(opts["seed"] * 1000 + i),
                 stats=stats, failures=failures, **opts["check"])
    return i, schema, time.monotonic() - t0, stats, failures


def main(argv=None) -> int:
    import argparse
    import multiprocessing
    ap = argparse.ArgumentParser()
    ap.add_argument("--random", type=int, default=12)
    ap.add_argument("--seed", type=int, default=20261002)
    ap.add_argument("--max-len", type=int, default=4)
    ap.add_argument("--alphabet", default="0159-.e+")
    ap.add_argument("--search-len", type=int, default=8)
    ap.add_argument("--search-exp", type=int, default=2)
    ap.add_argument("--search-budget", type=int, default=0,
                    help="expanded-state budget per search (0: the hook default)")
    ap.add_argument("--search-samples", type=int, default=4)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--only", default=None,
                    help="comma-separated schema indices to run (default: all)")
    ap.add_argument("--verbose", action="store_true", help="per-schema timing on stderr")
    args = ap.parse_args(argv)
    if not hasattr(zg.LIB, "blg_test_num_cert"):
        print(json.dumps({"error": "library built without -Dtest_hooks=true"}))
        return 2
    rng = random.Random(args.seed)
    schemas = list(TABLE_SCHEMAS) + [random_schema(rng) for _ in range(args.random)]
    opts = {"seed": args.seed, "check": {
        "alphabet": args.alphabet.encode(), "max_len": args.max_len,
        "search_len": args.search_len, "search_exp": args.search_exp,
        "search_budget": args.search_budget, "search_samples": args.search_samples}}
    only = {int(x) for x in args.only.split(",")} if args.only else None
    jobs = [(i, s, opts) for i, s in enumerate(schemas) if only is None or i in only]
    stats: dict = {}
    failures: list = []

    def merge(res):
        i, s, dt, st, fl = res
        for k, v in st.items():
            stats[k] = stats.get(k, 0) + v
        failures.extend(fl)
        if args.verbose:
            print(f"[{i}] {dt:.2f}s {json.dumps(s)}", file=sys.stderr, flush=True)

    if args.jobs > 1:
        with multiprocessing.get_context("fork").Pool(args.jobs, initializer=_worker_init) as pool:
            for res in pool.imap_unordered(_run_one, jobs):
                merge(res)
    else:
        _worker_init()
        try:
            for job in jobs:
                merge(_run_one(job))
        finally:
            _WORKER["ctx"].destroy()
    stats["schemas"] = len(jobs)
    kinds: dict = {}
    for f in failures:
        kinds[f["issue"]] = kinds.get(f["issue"], 0) + 1
    print(json.dumps({"stats": stats, "failure_kinds": kinds, "failures": failures[:200],
                      "n_failures": len(failures)}, indent=1))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
