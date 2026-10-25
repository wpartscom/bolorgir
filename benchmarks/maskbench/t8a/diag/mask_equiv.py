#!/usr/bin/env python3
"""Equivalence check of a candidate core against the recorded T8a streams.

Runs each listed corpus file in-process with the frozen protocol shape
(spec-v1 profile, value-preserving serializer, one thread, compact) and
compares, per test instance, ``consumed`` / ``rejected_at`` / ``n_ids`` with
the run's recorded stream. A mask that differs by one bit changes at least
one of those on some instance; identical streams over a slice mean the
candidate accepted and rejected exactly the same tokens there.

Not a replacement for the frozen harness: it is a fast local gate before a
new wheel and a new full run.

usage:
  PYTHONPATH=<pkg>:<t8a>:<mb> mask_equiv.py --files list.txt --out equiv.json
"""

import argparse
import json
import os
import sys
import time

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", required=True)
    ap.add_argument("--run", default=os.path.join(WORK, "out", "blg-spec-v1"))
    ap.add_argument("--out", default="")
    ap.add_argument("--max-tokens", type=int, default=0,
                    help="cap tokens per file (0 = no cap); capped files are reported")
    ap.add_argument("--allow-missing-stream", action="store_true",
                    help="run files that have no recorded stream (timeouts) and "
                         "report their timings only; no equivalence claim")
    args = ap.parse_args()

    names = [l.strip() for l in open(args.files) if l.strip()]
    tok = AutoTokenizer.from_pretrained(TOKDIR)
    eng = BlgEngine(profile="spec-v1", serializer=True)
    eng.tokenizer = tok
    eng.init()

    results, bad, capped = [], [], []
    all_us = []
    t_all = time.monotonic()
    for name in names:
        path = os.path.join(DATA, name)
        stream_path = os.path.join(args.run, "t8a_streams", name)
        has_stream = os.path.exists(stream_path)
        if not os.path.exists(path) or (not has_stream and not args.allow_missing_stream):
            bad.append({"file": name, "why": "missing corpus or stream"})
            continue
        data = json.load(open(path))
        want = json.load(open(stream_path)) if has_stream else None
        try:
            eng.compile_grammar(data["schema"])
        except Exception as e:
            bad.append({"file": name, "why": f"compile failed: {e!r}"})
            continue
        got, calls, mask_us, abort_us, aborts = [], 0, 0, 0, 0
        hit_cap = False
        for i, test in enumerate(data["tests"]):
            eng.reset()
            try:
                text = eng.serialize_instance(test["data"])
            except Exception:
                got.append({"i": i, "serialization_incompatible": True})
                continue
            ids = tok.encode(text, add_special_tokens=False)
            consumed, rejected_at, aborted = 0, None, None
            for k, tid in enumerate(ids):
                if args.max_tokens and calls >= args.max_tokens:
                    hit_cap = True
                    break
                t0 = time.monotonic()
                try:
                    eng.compute_mask()
                except Exception as e:
                    # a resource refusal is an outcome, not a crash: record the
                    # phase and the token so it can be compared with the run;
                    # the duration of the raised call is mask cost and must be
                    # counted, or a refusal looks free
                    dt = (time.monotonic() - t0) * 1e6
                    mask_us += dt
                    abort_us += dt
                    all_us.append(dt)
                    aborted = {"phase": "compute_mask", "at": consumed,
                               "error": type(e).__name__, "us": round(dt)}
                    aborts += 1
                    break
                try:
                    ok = eng.commit_token(tid)
                except Exception as e:
                    dt = (time.monotonic() - t0) * 1e6
                    mask_us += dt
                    abort_us += dt
                    all_us.append(dt)
                    aborted = {"phase": "commit_token", "at": consumed,
                               "error": type(e).__name__, "us": round(dt)}
                    aborts += 1
                    break
                mask_us_step = (time.monotonic() - t0) * 1e6
                mask_us += mask_us_step
                all_us.append(mask_us_step)
                calls += 1
                if not ok:
                    rejected_at = consumed
                    break
                consumed += 1
            got.append({"i": i, "n_ids": len(ids), "consumed": consumed,
                        "rejected_at": rejected_at, "aborted": aborted})
            if hit_cap:
                break
        if hit_cap:
            capped.append(name)
            continue
        # compare against the stream
        mism = []
        w = {} if want is None else {t["i"]: t for t in want["tests"]}
        for g in (got if want is not None else []):
            if g.get("serialization_incompatible"):
                if "serialization_incompatible" not in w.get(g["i"], {}):
                    mism.append({"i": g["i"], "why": "serialization mismatch"})
                continue
            t = w.get(g["i"])
            if t is None:
                mism.append({"i": g["i"], "why": "instance missing in the stream"})
                continue
            for key in ("n_ids", "consumed", "rejected_at"):
                if t[key] != g[key]:
                    mism.append({"i": g["i"], "field": key,
                                 "run": t[key], "candidate": g[key]})
            # an abort must land where the run's stream stopped
            if g.get("aborted") and t["consumed"] != g["consumed"]:
                mism.append({"i": g["i"], "field": "abort_at",
                             "run": t["consumed"], "candidate": g["aborted"]})
        results.append({"file": name, "instances": len(got), "calls": calls,
                        "mask_us": round(mask_us), "abort_us": round(abort_us),
                        "aborts": aborts, "mismatches": mism})
        if mism:
            bad.append({"file": name, "why": f"{len(mism)} instance mismatches",
                        "detail": mism[:5]})

    wall = time.monotonic() - t_all
    all_us.sort()
    n = len(all_us)
    q = {p: (all_us[min(n - 1, int(n * p / 100))] if n else None) for p in (50, 75, 90, 99)}
    tbm = {f"p{p}": (None if v is None else round(v / 1e3, 3)) for p, v in q.items()}
    if n:
        tbm["share_calls_ge_1s"] = round(sum(1 for x in all_us if x >= 1e6) / n, 4)
        tbm["share_time_ge_1s"] = round(sum(x for x in all_us if x >= 1e6) /
                                       max(1, sum(all_us)), 4)
    out = {"files": len(results), "mismatched_files": len([r for r in results if r["mismatches"]]),
           "errors": bad, "capped": capped, "wall_s": round(wall, 1),
           "mask_s": round(sum(r["mask_us"] for r in results) / 1e6, 1),
           "abort_s": round(sum(r["abort_us"] for r in results) / 1e6, 1),
           "aborts": sum(r["aborts"] for r in results),
           "calls": sum(r["calls"] for r in results), "tbm_ms": tbm,
           "per_file": results}
    if args.out:
        with open(args.out, "w") as f:
            json.dump(out, f, indent=1)
    print(f"files {out['files']}, calls {out['calls']}, "
          f"mask {out['mask_s']} s (aborts {out['aborts']}: {out['abort_s']} s), "
          f"wall {out['wall_s']} s")
    print("TBM ms:", json.dumps(tbm, sort_keys=True))
    print(f"mismatched files: {out['mismatched_files']}, errors: {len(bad)}, "
          f"capped: {len(capped)}")
    for b in bad[:10]:
        print("  MISMATCH", b["file"], "-", b["why"], b.get("detail", ""))
    return 0 if out["mismatched_files"] == 0 and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
