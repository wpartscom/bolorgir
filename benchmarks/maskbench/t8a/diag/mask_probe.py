#!/usr/bin/env python3
"""Per-step mask probe: cost vs the number of allowed tokens.

Runs the frozen T8a engine (spec-v1 profile, value-preserving serializer,
one thread) in-process on one corpus file and records, for every
``compute_mask``: the wall time, the number of allowed vocabulary tokens and
whether the commit was accepted.

The point is to separate the two regimes the run's records show:

* a *forced* step (one allowed token) - the mask is known by construction;
* an *open* step (many allowed tokens) - the full mask over the vocabulary
  has to be classified.

usage (venv-pinned, PYTHONPATH as for the T8a runner):
  mask_probe.py FILE [--max-steps 200] [--min-ms 50]
"""

import argparse
import json
import os
import sys
import time

import paired_runner  # noqa: F401  (the T8a protocol patches)
from maskbench import runner
from maskbench.blg_engine import BlgEngine
from transformers import AutoTokenizer

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.dirname(HERE)
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
DATA = os.path.join(ROOT, "benchmarks", "external", "jsonschemabench-maskbench",
                    "maskbench", "data")
TOKDIR = os.path.join(ROOT, "benchmarks", "local", "hf-cache", "hub",
                      "models--unsloth--Meta-Llama-3.1-8B-Instruct", "snapshots",
                      "a2856192dd7c25b842431f39c179a6c2c2f627d1")


def median(v):
    v = sorted(v)
    n = len(v)
    return 0.0 if not n else (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2)


def byte_vocab_run(data, args):
    """Same file, same states, 256 single-byte tokens instead of 128k.

    If the mask cost is the full-vocabulary classification, it drops by the
    vocabulary ratio (128k / 256); if it is a schema-dependent search, it
    does not scale with the vocabulary at all.
    """
    import bolorgir as blg
    from bolorgir.serializer import serialize_value

    bundle = blg.TokenizerBundle.from_token_bytes([bytes([b]) for b in range(256)])
    engine = blg.Engine(mode="adaptive", memory_limit_mb=256, tokenizer=bundle)
    t = time.monotonic()
    constraint = engine.compile(data["schema"], bundle, profile="spec-v1")
    compile_s = time.monotonic() - t
    rows = []
    steps = 0
    for i, test in enumerate(data["tests"]):
        payload, _st = serialize_value(test["data"], data["schema"])
        raw = payload
        session = constraint.create_session()
        for k, b in enumerate(raw):
            if steps >= args.max_steps:
                break
            t0 = time.monotonic()
            mask = session.fill_mask()
            ok = bool(mask[b >> 3] & (1 << (b & 7)))
            if ok:
                session.accept_token(b)
            us = (time.monotonic() - t0) * 1e6
            n_allowed = int.from_bytes(mask, "little").bit_count()
            rows.append({"i": i, "k": k, "token": chr(b) if 32 <= b < 127 else hex(b),
                         "us": round(us, 1), "allowed": n_allowed, "ok": ok})
            steps += 1
            if not ok:
                break
        session.close()
        if steps >= args.max_steps:
            break
    constraint.close()
    engine.close()
    us = [r["us"] for r in rows]
    print(f"{args.file} [byte vocab 256]: compile {compile_s:.2f} s, "
          f"{len(rows)} bytes probed")
    print(f"  per-step median {median(us) / 1e3:.3f} ms  max {max(us) / 1e3:.3f} ms  "
          f"total {sum(us) / 1e6:.2f} s")
    print("  steps with >= %.0f ms:" % args.min_ms)
    for r in rows:
        if r["us"] >= args.min_ms * 1e3:
            print(f"    t{r['i']} k{r['k']:<4d} {r['us'] / 1e3:9.3f} ms  "
                  f"allowed {r['allowed']:3d}  {r['token']!r}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--max-steps", type=int, default=200)
    ap.add_argument("--min-ms", type=float, default=50.0)
    ap.add_argument("--out", default="")
    ap.add_argument("--byte-vocab", action="store_true",
                    help="run with a 256-token byte vocabulary instead of the "
                         "HF tokenizer: cost proportional to the vocabulary size "
                         "then shows up as a proportional drop")
    ap.add_argument("--diag", action="store_true",
                    help="print the mask-path counters of the instrumented core")
    args = ap.parse_args()

    data = json.load(open(os.path.join(DATA, args.file)))
    dlib = None
    if args.diag:
        import mask_diag
        dlib = mask_diag.load()
        mask_diag.selftest(dlib)
    if args.byte_vocab:
        return byte_vocab_run(data, args)
    eng = BlgEngine(profile="spec-v1", serializer=True)
    eng.tokenizer = AutoTokenizer.from_pretrained(TOKDIR)
    eng.init()
    t = time.monotonic()
    eng.compile_grammar(data["schema"])
    compile_s = time.monotonic() - t

    rows = []
    steps = 0
    for i, test in enumerate(data["tests"]):
        eng.reset()
        try:
            text = eng.serialize_instance(test["data"])
        except Exception as e:
            rows.append({"i": i, "k": None, "event": f"serialize failed: {e!r}"})
            continue
        ids = eng.tokenizer.encode(text, add_special_tokens=False)
        for k, tid in enumerate(ids):
            if steps >= args.max_steps:
                break
            t0 = time.monotonic()
            eng.compute_mask()
            ok = eng.commit_token(tid)
            us = (time.monotonic() - t0) * 1e6
            n_allowed = int.from_bytes(eng.mask, "little").bit_count()
            rows.append({"i": i, "k": k, "token": eng.tokenizer.decode([tid]),
                         "us": round(us, 1), "allowed": n_allowed, "ok": bool(ok)})
            steps += 1
            if not ok:
                break
        if steps >= args.max_steps:
            break

    forced = [r["us"] for r in rows if r.get("allowed") == 1]
    open_ = [r["us"] for r in rows if (r.get("allowed") or 0) > 1]
    print(f"{args.file}: compile {compile_s:.2f} s, {len(rows)} steps probed")
    print(f"  forced (1 allowed):  n={len(forced):4d}  median {median(forced) / 1e3:9.3f} ms")
    print(f"  open   (>1 allowed): n={len(open_):4d}  median {median(open_) / 1e3:9.3f} ms")
    if open_:
        print(f"  open   max {max(open_) / 1e3:.1f} ms; allowed-token range "
              f"{min(r['allowed'] for r in rows if (r.get('allowed') or 0) > 1)}"
              f"..{max(r['allowed'] for r in rows)}")
    print("  steps with >= %.0f ms:" % args.min_ms)
    for r in rows:
        if r.get("us", 0) >= args.min_ms * 1e3:
            print(f"    t{r['i']} k{r['k']:<4d} {r['us'] / 1e3:9.1f} ms  "
                  f"allowed {r['allowed']:6d}  {r['token']!r}")
    try:
        st = eng.engine.stats()
        keep = ("mask_calls", "mask_ns_total", "cache_hits", "cache_misses",
                "cache_evictions", "cache_adaptive_skips", "work_ops_total",
                "precompute_states")
        print("  engine stats:", json.dumps({k: st.get(k) for k in keep if k in st}))
        if st.get("mask_calls"):
            print(f"    mask_ns_total / mask_calls = "
                  f"{st['mask_ns_total'] / st['mask_calls'] / 1e6:.3f} ms per call")
    except Exception as e:
        print(f"  engine stats unavailable: {e!r}")
    if dlib is not None:
        import mask_diag
        print("  mask diag:", json.dumps(mask_diag.read(dlib)))
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"file": args.file, "compile_s": compile_s, "rows": rows}, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
