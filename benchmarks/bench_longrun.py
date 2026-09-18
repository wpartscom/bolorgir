#!/usr/bin/env python3
"""B5/B8. Честная длительная генерация (аудит 2026-09-15, п.6).

Прежний опубликованный сценарий делал одну начальную маску + abort за цикл.
Здесь — реальные шаги генерации: на КАЖДОМ цикле сессия проходит полную
трассу (fill_mask + accept_token на каждом токене), сессия завершается
finish() и уничтожается. Итого --steps суммарных шагов маски (по умолчанию
100k, ТЗ B8) и ~steps/len(trace) циклов create/destroy (>= 10k, ТЗ B8).

Нагрузка повторяется при лимитах ядра 64/128/256 MiB (ТЗ B5): шаги делятся
между лимитами поровну; ResourceLimit фиксируется, а не исключается молча.

Устойчивость: p50/p99 маски/accept по окнам (окно = 1/10 шагов лимита),
RSS на границах окон, stats ядра (mem_used/mem_peak, cache hits/evictions)
до и после. Критерий плато (NFR-2): среднее второй половины окон p99 не
хуже первой более чем на 20% и RSS не растёт монотонно.

Запуск из корня проекта:
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
    rss_samples = []
    cycles = 0
    done = 0
    rng = random.Random(seed)
    while done < steps:
        name = names[rng.randrange(len(names))]
        trace = traces[name]
        session = None
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
        done += len(trace)
        if len(win_mask) >= per_window:
            win_reports.append({
                "mask_ns": bc.percentile_stats(win_mask),
                "accept_ns": bc.percentile_stats(win_accept),
            })
            rss_samples.append(bc.peak_rss_bytes())
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
    return {
        "status": "OK", "limit_mb": limit_mb,
        "mask_steps": sum(w["mask_ns"]["count"] for w in win_reports),
        "session_cycles": cycles, "errors": errors,
        "windows": win_reports,
        "mask_p99_plateau": plateau(p99),
        "rss_samples": rss_samples,
        "rss_plateau": plateau(rss_samples),
        "core_stats_before": stats0, "core_stats_after": stats1,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--steps", type=int, default=100000,
                    help="суммарных шагов маски на лимит (ТЗ B8: >= 100k)")
    ap.add_argument("--limits-mb", default="64,128,256")
    ap.add_argument("--windows", type=int, default=10)
    ap.add_argument("--seed", type=int, default=bc.SEED)
    args = ap.parse_args()

    zc = bc.require_core()
    tf = bc.import_or_skip("transformers")
    if tf is None:
        bc.skip("нужен transformers (HF-токенизатор GPT-2 из кэша)")
    hf_tok = tf.AutoTokenizer.from_pretrained(TOKENIZER_NAME,
                                              revision=TOKENIZER_REVISION)
    bundle = zc.TokenizerBundle.from_hf(hf_tok)

    # проверка приёма документов до старта длинного прогона
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
        bc.emit({"status": "ERROR", "reason": "нет валидных пар схема/документ"})
        return

    limits = {}
    for limit_mb in [int(x) for x in args.limits_mb.split(",")]:
        print(f"limit {limit_mb} MiB: {args.steps} шагов...", file=sys.stderr, flush=True)
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
