#!/usr/bin/env python3
"""Per-call mask profile of a file list: which calls form the p90.

Replays the list with the frozen protocol (spec-v1, value-preserving
serializer, one thread) and records every ``compute_mask`` call: the fill
time, the commit time, the step time (fill + commit, the span `mask_equiv`
measures), the number of allowed tokens, the token id and the mask-path
counters of that call. The counters are reset right before the fill and
read right after it, so they describe the state that produced the call:
fast path or exact walk, refusal reason, trie nodes, completion checks.

The output JSON keeps every call, so a slow call can be reproduced from
(file, instance, k): feed the first k token ids of that instance and fill.
The console summary prints the TBM percentiles, the slowest calls with the
path they took, and a grouping by path signature. A `compute_mask` or
`commit_token` exception fails the run and is recorded in `errors`; a
normal `commit_token == False` is counted separately as `rejected`.

usage:
  PYTHONPATH=<pkg>:<t8a>:<mb> venv-pinned/bin/python slice_calls.py \
      --files equiv/slice.txt --out calls.json [--top 20]
"""

import argparse
import json
import os
import sys
import time

from maskbench.blg_engine import BlgEngine
from transformers import AutoTokenizer

import mask_diag

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.dirname(HERE)
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
DATA = os.path.join(ROOT, "benchmarks", "external", "jsonschemabench-maskbench",
                    "maskbench", "data")
TOKDIR = os.path.join(ROOT, "benchmarks", "local", "hf-cache", "hub",
                      "models--unsloth--Meta-Llama-3.1-8B-Instruct", "snapshots",
                      "a2856192dd7c25b842431f39c179a6c2c2f627d1")

# Counter fields that name the path of a call. The full counter dict is kept
# in every row; this list is only for the printed signature.
PATH_KEYS = ("fast_used", "name_used", "refuse_not_uniform",
             "refuse_byte_incomplete", "refuse_content_dep",
             "refuse_name_state")


def tbm(us):
    v = sorted(us)
    n = len(v)
    if not n:
        return {}
    out = {f"p{p}": round(v[min(n - 1, int(n * p / 100))] / 1e3, 3)
           for p in (50, 75, 90, 99)}
    out["share_calls_ge_1s"] = round(sum(1 for x in v if x >= 1e6) / n, 4)
    out["share_time_ge_1s"] = round(sum(x for x in v if x >= 1e6) /
                                    max(1, sum(v)), 4)
    return out


def signature(c):
    parts = []
    if c.get("fast_used"):
        parts.append("name" if c.get("name_used") else "string")
    else:
        parts.append("exact")
    for k in ("refuse_content_dep", "refuse_name_state",
              "refuse_byte_incomplete", "refuse_not_uniform"):
        if c.get(k):
            parts.append(k.replace("refuse_", ""))
    if c.get("state_alive_calls"):
        parts.append("alive")
    return "+".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()

    names = [l.strip() for l in open(args.files) if l.strip()]
    tok = AutoTokenizer.from_pretrained(TOKDIR)
    eng = BlgEngine(profile="spec-v1", serializer=True)
    eng.tokenizer = tok
    eng.init()
    lib = mask_diag.load()
    mask_diag.selftest(lib)

    rows, errors, instances = [], [], 0
    rejected = 0
    t_all = time.monotonic()
    for name in names:
        path = os.path.join(DATA, name)
        if not os.path.exists(path):
            errors.append({"file": name, "why": "missing corpus"})
            continue
        data = json.load(open(path))
        try:
            eng.compile_grammar(data["schema"])
        except Exception as e:
            errors.append({"file": name, "why": f"compile failed: {e!r}"})
            continue
        for i, test in enumerate(data["tests"]):
            eng.reset()
            instances += 1
            try:
                text = eng.serialize_instance(test["data"])
            except Exception:
                errors.append({"file": name, "i": i,
                               "why": "serialization_incompatible"})
                continue
            ids = tok.encode(text, add_special_tokens=False)
            for k, tid in enumerate(ids):
                lib.blg_diag_reset()
                t0 = time.monotonic()
                err = None
                commit_err = None
                ok = None
                try:
                    eng.compute_mask()
                except Exception as e:
                    err = type(e).__name__
                fill_us = (time.monotonic() - t0) * 1e6
                if err is None:
                    try:
                        ok = eng.commit_token(tid)
                    except Exception as e:
                        commit_err = type(e).__name__
                commit_us = (time.monotonic() - t0) * 1e6 - fill_us
                step_us = fill_us + commit_us
                c = mask_diag.read(lib)
                allowed = (int.from_bytes(eng.mask, "little").bit_count()
                           if err is None else None)
                tail = tok.decode(ids[max(0, k - 8):k])
                if err is not None:
                    errors.append({"file": name, "i": i, "k": k,
                                   "why": f"compute_mask raised {err}"})
                if commit_err is not None:
                    errors.append({"file": name, "i": i, "k": k,
                                   "why": f"commit_token raised {commit_err}"})
                if err is None and commit_err is None and ok is False:
                    # a normal rejection of an invalid document, not an error
                    rejected += 1
                rows.append({
                    "file": name, "i": i, "k": k, "token": tid,
                    "token_text": tok.decode([tid]),
                    "tail": tail,
                    "step_us": round(step_us, 1),
                    "fill_us": round(fill_us, 1),
                    "commit_us": round(commit_us, 1),
                    "allowed": allowed,
                    "ok": ok,
                    "error": err,
                    "commit_error": commit_err,
                    "counters": c,
                })
                if err is not None or commit_err is not None or not ok:
                    break
        print(f"  {name}: {len(rows)} calls so far", file=sys.stderr)

    wall = time.monotonic() - t_all
    step = [r["step_us"] for r in rows]
    total_step = sum(step)
    payload = {
        "files": len(names),
        "instances": instances,
        "calls": len(rows),
        "rejected": rejected,
        "wall_s": round(wall, 1),
        "mask_s": round(total_step / 1e6, 3),
        "tbm_ms": tbm(step),
        "tbm_fill_ms": tbm([r["fill_us"] for r in rows]),
        "tbm_commit_ms": tbm([r["commit_us"] for r in rows]),
        "errors": errors,
        "rows": rows,
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
    print(f"files {len(names)}, instances {instances}, calls {len(rows)}, "
          f"rejected {rejected}, mask {payload['mask_s']} s, wall {wall:.1f} s, "
          f"errors {len(errors)}")
    print("TBM ms (step = fill + commit):", json.dumps(payload["tbm_ms"],
                                                       sort_keys=True))
    if errors:
        print("errors:", errors[:5])

    # Slowest calls, with the path signature.
    print(f"\nslowest {args.top} calls:")
    hdr = f"{'file':38s} {'i':>2s} {'k':>5s} {'step ms':>9s} {'allowed':>8s} path / tail"
    print(hdr)
    for r in sorted(rows, key=lambda x: -x["step_us"])[:args.top]:
        c = r["counters"]
        tail = (r["tail"][-24:] + "|" + r["token_text"]).replace("\n", "\\n")
        print(f"{r['file']:38s} {r['i']:2d} {r['k']:5d} "
              f"{r['step_us'] / 1e3:9.3f} {str(r['allowed']):>8s} "
              f"{signature(c):28s} {tail!r}")

    # Group by path signature: calls, mask time, mean, p90.
    groups = {}
    for r in rows:
        s = signature(r["counters"])
        g = groups.setdefault(s, {"calls": 0, "us": 0, "max": 0})
        g["calls"] += 1
        g["us"] += r["step_us"]
        g["max"] = max(g["max"], r["step_us"])
    print(f"\npath groups (share of {total_step / 1e6:.3f} s):")
    for s, g in sorted(groups.items(), key=lambda kv: -kv[1]["us"]):
        print(f"  {s:34s} calls {g['calls']:4d} "
              f"time {g['us'] / 1e6:7.3f} s "
              f"({100 * g['us'] / max(1, total_step):5.1f}%) "
              f"mean {g['us'] / g['calls'] / 1e3:7.3f} ms "
              f"max {g['max'] / 1e3:8.3f} ms")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
