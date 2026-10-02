#!/usr/bin/env python3
"""B5/B8. Honest long-running generation.

The previously published scenario did one initial mask + abort per cycle.
Here - real generation steps: on EVERY cycle the session walks the full
trace (fill_mask + accept_token at each token), the session is finished via
finish() and destroyed. Total --steps aggregate mask steps (default 100k,
SPEC B8) and ~steps/len(trace) create/destroy cycles (>= 10k, SPEC B8).

The load is repeated under engine limits of 64/128/256 MiB (SPEC B5): steps
are split evenly between the limits; ResourceLimit is recorded, not silently
excluded.

Stability: p50/p99 of mask/accept per window (window = 1/10 of the limit's
steps), RSS at window boundaries, engine stats (mem_used/mem_peak, cache
hits/evictions) before and after. Plateau criterion (NFR-2): the mean of the
second half of the windows' p99 is not worse than the first by more than 20%
and RSS does not grow monotonically.

Run from the project root:
    PYTHONPATH=python:benchmarks python3 benchmarks/bench_longrun.py
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_common as bc

TOKENIZER_NAME = "openai-community/gpt2"
TOKENIZER_REVISION = "607a30d783dfa663caf39e06633721c8d4cfcd7e"

DEFAULT_DOCS = {
    "closed_object_action_amount": '{"action":"buy","amount":42}',
    "optional_fields": '{"name":"alice","age":30}',
    "nested_3_levels": '{"a":{"b":{"c":1}}}',
}


def run_limit(zc, hf_tok, bundle, docs, limit_mb, steps, windows, seed):
    import random
    engine = zc.Engine(mode="adaptive", memory_limit_mb=limit_mb, tokenizer=bundle)
    constraints = {}
    traces = {}
    errors = {"ResourceLimit": 0, "other": 0, "compile_errors": []}
    for name, doc in docs.items():
        try:
            _, schema_bytes = bc.load_schema(name)
            constraints[name] = engine.compile(json.loads(schema_bytes))
            traces[name] = hf_tok.encode(doc)
        except Exception as e:
            errors["compile_errors"].append(f"{name}: {type(e).__name__}: {e}"[:200])
    if not constraints:
        engine.close()
        return {"status": "ERROR", "errors": errors}

    names = sorted(constraints)
    stats0 = engine.stats()
    per_window = max(1, steps // windows)
    win_mask, win_accept = [], []
    win_reports = []
    rss_current_samples = []
    rss_peak_samples = []
    cycles = 0
    done = 0
    rng = random.Random(seed)
    while done < steps:
        name = names[rng.randrange(len(names))]
        trace = traces[name]
        session = None
        accepted = 0
        try:
            session = constraints[name].create_session()
            for t in trace:
                t0 = bc.now_ns()
                session.fill_mask()
                t1 = bc.now_ns()
                session.accept_token(t)
                t2 = bc.now_ns()
                win_mask.append(t1 - t0)
                win_accept.append(t2 - t1)
                accepted += 1
            if session.can_end():
                session.finish()
        except Exception as e:
            if "ResourceLimit" in type(e).__name__:
                errors["ResourceLimit"] += 1
            else:
                errors["other"] += 1
        finally:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
        cycles += 1
        # SPEC 10.5: after an error only the actually walked steps
        # are counted, not the full trace length.
        done += accepted
        if len(win_mask) >= per_window:
            win_reports.append({
                "mask_ns": bc.percentile_stats(win_mask),
                "accept_ns": bc.percentile_stats(win_accept),
            })
            rss_current_samples.append(bc.current_rss_bytes())
            rss_peak_samples.append(bc.peak_rss_bytes())
            win_mask, win_accept = [], []

    stats1 = engine.stats()
    for c in constraints.values():
        c.close()
    engine.close()

    def plateau(vals):
        if len(vals) < 4:
            return None
        half = len(vals) // 2
        f = sum(vals[:half]) / half
        l = sum(vals[half:]) / (len(vals) - half)
        return {"first_half_mean": f, "last_half_mean": l,
                "ratio": (l / f) if f else None}

    p99 = [w["mask_ns"]["p99"] for w in win_reports if w["mask_ns"].get("count")]
    err_total = errors["ResourceLimit"] + errors["other"]
    return {
        "status": "OK" if err_total == 0 else "DEGRADED",
        "clean": err_total == 0,
        "limit_mb": limit_mb,
        "mask_steps": sum(w["mask_ns"]["count"] for w in win_reports),
        "session_cycles": cycles, "errors": errors,
        "windows": win_reports,
        "mask_p99_plateau": plateau(p99),
        "rss_current_samples": rss_current_samples,
        "rss_peak_samples": rss_peak_samples,
        "rss_plateau": plateau(rss_current_samples),
        "rss_peak_plateau": plateau(rss_peak_samples),
        "core_stats_before": stats0, "core_stats_after": stats1,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--steps", type=int, default=100000,
                    help="aggregate mask steps per limit (SPEC B8: >= 100k)")
    ap.add_argument("--limits-mb", default="64,128,256")
    ap.add_argument("--windows", type=int, default=10)
    ap.add_argument("--seed", type=int, default=bc.SEED)
    args = ap.parse_args()

    zc = bc.require_core()
    tf = bc.import_or_skip("transformers")
    if tf is None:
        bc.skip("transformers required (HF GPT-2 tokenizer from cache)")
    hf_tok = tf.AutoTokenizer.from_pretrained(TOKENIZER_NAME,
                                              revision=TOKENIZER_REVISION)
    bundle = zc.TokenizerBundle.from_hf(hf_tok)

    # check document acceptance before the long run starts
    docs = {}
    for name, doc in DEFAULT_DOCS.items():
        entry, schema_bytes = bc.load_schema(name)
        if entry["kind"] != "json_schema" or not entry["expect_support"]:
            continue
        import jsonschema
        try:
            jsonschema.validate(json.loads(doc), json.loads(schema_bytes))
        except Exception:
            continue
        docs[name] = doc
    if not docs:
        bc.emit({"status": "ERROR", "reason": "no valid schema/document pairs"})
        return

    limits = {}
    for limit_mb in [int(x) for x in args.limits_mb.split(",")]:
        print(f"limit {limit_mb} MiB: {args.steps} steps...", file=sys.stderr, flush=True)
        limits[str(limit_mb)] = run_limit(zc, hf_tok, bundle, docs, limit_mb,
                                          args.steps, args.windows, args.seed)
        r = limits[str(limit_mb)]
        print(f"  -> {r.get('status')} steps={r.get('mask_steps')} "
              f"cycles={r.get('session_cycles')} errors={r.get('errors', {}).get('other')}",
              file=sys.stderr, flush=True)

    bc.emit({
        "status": "OK", "case": "B5/B8_longrun", "seed": args.seed,
        "steps_per_limit": args.steps, "windows": args.windows,
        "tokenizer": {"name": TOKENIZER_NAME, "revision": TOKENIZER_REVISION},
        "documents": docs,
        "limits": limits,
        "peak_rss_bytes": bc.peak_rss_bytes(),
    })


if __name__ == "__main__":
    main()
