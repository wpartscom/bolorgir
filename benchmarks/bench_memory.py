#!/usr/bin/env python3
"""B5/B8. Бюджеты памяти и длительная работа.

- плато RSS при циклах session create/destroy (по умолчанию 10k циклов, ТЗ B8);
- поведение при общих лимитах ядра 64/128/256 MiB (ТЗ B5): поддерживаемая
  нагрузка и штатный ResourceLimit;
- дополнительно: циклы compile/release грамматики.

Проверяется плато при фиксированной нагрузке и отсутствие накопления после
циклов create/destroy (NFR-2), а не абсолютное отсутствие роста RSS.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_common as bc


def session_cycles(engine, constraint, cycles, sample_every):
    rss_samples = []
    errors = {"ResourceLimit": 0, "other": 0}
    for i in range(cycles):
        try:
            s = constraint.create_session()
            s.fill_mask()
            s.abort()
        except Exception as e:  # типизированные исключения пакета
            name = type(e).__name__
            if "ResourceLimit" in name:
                errors["ResourceLimit"] += 1
            else:
                errors["other"] += 1
        if (i + 1) % sample_every == 0:
            rss_samples.append(bc.peak_rss_bytes())
    return rss_samples, errors


def plateau_report(samples):
    if len(samples) < 4:
        return {"samples": samples}
    half = len(samples) // 2
    first = samples[:half]
    last = samples[half:]
    mean_first = sum(first) / len(first)
    mean_last = sum(last) / len(last)
    return {
        "samples_count": len(samples),
        "first_half_mean_bytes": mean_first,
        "last_half_mean_bytes": mean_last,
        "growth_ratio": (mean_last / mean_first) if mean_first else None,
        "max_bytes": max(samples),
        "min_bytes": min(samples),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="closed_object_action_amount")
    ap.add_argument("--corpus-dir", default=bc.CORPUS_DIR)
    ap.add_argument("--limits-mb", default="64,128,256")
    ap.add_argument("--cycles", type=int, default=10000)
    ap.add_argument("--sample-every", type=int, default=250)
    ap.add_argument("--compile-cycles", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=bc.SEED)
    args = ap.parse_args()

    zc = bc.require_core()
    entry, schema_bytes = bc.load_schema(args.schema, args.corpus_dir)
    schema = json.loads(schema_bytes)

    limits_report = {}
    for limit_mb in [int(x) for x in args.limits_mb.split(",")]:
        try:
            engine = zc.Engine(mode="adaptive", memory_limit_mb=limit_mb,
                               tokenizer=bc.make_byte_tokenizer(zc))
            constraint = engine.compile(schema)
            samples, errors = session_cycles(
                engine, constraint, args.cycles, args.sample_every)
            limits_report[str(limit_mb)] = {
                "status": "OK",
                "errors": errors,
                "rss": plateau_report(samples),
            }
        except Exception as e:
            limits_report[str(limit_mb)] = {
                "status": "ERROR",
                "error_type": type(e).__name__,
                "message": str(e)[:256],
            }

    # циклы compile/release на лимите по умолчанию
    engine = zc.Engine(mode="adaptive", memory_limit_mb=256,
                       tokenizer=bc.make_byte_tokenizer(zc))
    rss = []
    for i in range(args.compile_cycles):
        c = engine.compile(schema)
        del c
        if (i + 1) % max(1, args.compile_cycles // 20) == 0:
            rss.append(bc.peak_rss_bytes())

    bc.emit({
        "status": "OK", "case": "B5/B8", "schema": args.schema, "seed": args.seed,
        "cycles_per_limit": args.cycles,
        "limits_mb": limits_report,
        "compile_release_cycles": args.compile_cycles,
        "compile_release_rss": plateau_report(rss),
        "note": "рост RSS при легитимном заполнении кэша допустим; критерий — "
                "плато при фиксированной нагрузке (NFR-2)",
    })


if __name__ == "__main__":
    main()
