#!/usr/bin/env python3
"""Full JSONSchemaBench corpus run: support/refusal report.

SPEC 10.3 ("Full inventory of schemas of the pinned JSONSchemaBench version
with a support/refusal report") and 13.1 §8.

For every schema from benchmarks/external/jsonschemabench/data/**.json our
engine compiles it (canonical-v1 profile, gpt2 tokenizer - as in manifest)
and the outcome is classified:

  compiled                  - the schema compiled;
  invalid_schema            - BLG_ERR_INVALID_SCHEMA;
  unsupported_feature       - BLG_ERR_UNSUPPORTED_FEATURE;
  unsatisfiable_constraint  - BLG_ERR_UNSATISFIABLE_CONSTRAINT;
  resource_limit            - BLG_ERR_RESOURCE_LIMIT;
  unsupported_tokenizer     - BLG_ERR_UNSUPPORTED_TOKENIZER;
  other_engine_error        - other engine errors;
  harness_error             - error outside the engine (file read etc.).

Output: <out-dir>/support_report.json (metadata, per-schema list, aggregates).
Raw schema bytes are passed to the engine as is (duplicate keys are caught by
the compiler itself as INVALID_SCHEMA).
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_common as bc

JSB_DATA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "external", "jsonschemabench", "data"
)
JSB_COMMIT = "ba103c73756198dd9b149ddc7db7867da7a077f6"
TOKENIZER_NAME = "openai-community/gpt2"
TOKENIZER_REVISION = "607a30d783dfa663caf39e06633721c8d4cfcd7e"


def iter_schemas(data_dir):
    for dataset in sorted(os.listdir(data_dir)):
        dpath = os.path.join(data_dir, dataset)
        if not os.path.isdir(dpath):
            continue
        for fname in sorted(os.listdir(dpath)):
            if not fname.endswith(".json"):
                continue
            yield dataset, fname[:-5], os.path.join(dpath, fname)


def classify(zc, engine, data):
    """-> (category, detail)."""
    try:
        engine.compile(data)
        return "compiled", ""
    except zc.InvalidSchemaError as e:
        return "invalid_schema", str(e)
    except zc.UnsupportedFeatureError as e:
        return "unsupported_feature", str(e)
    except zc.UnsatisfiableConstraintError as e:
        return "unsatisfiable_constraint", str(e)
    except zc.ResourceLimitError as e:
        return "resource_limit", str(e)
    except zc.UnsupportedTokenizerError as e:
        return "unsupported_tokenizer", str(e)
    except zc.ZigConstraintsError as e:
        return "other_engine_error", f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001 - record everything, lose nothing
        return "harness_error", f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=JSB_DATA)
    ap.add_argument("--out", required=True, help="path of support_report.json")
    ap.add_argument("--progress-every", type=int, default=500)
    args = ap.parse_args()

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    zc = bc.require_core()
    tf = bc.import_or_skip("transformers")
    if tf is None:
        bc.skip("transformers is not installed")

    hf_tok = tf.AutoTokenizer.from_pretrained(TOKENIZER_NAME,
                                              revision=TOKENIZER_REVISION)
    bundle = zc.TokenizerBundle.from_hf(hf_tok)
    engine = zc.Engine(mode="adaptive", tokenizer=bundle)

    per_schema = []
    agg = {}
    by_dataset = {}
    unsupported_keywords = {}
    compile_ns = []
    t_start = time.time()
    for dataset, name, path in iter_schemas(args.data_dir):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            per_schema.append({"dataset": dataset, "name": name,
                               "outcome": "harness_error", "detail": str(e)})
            continue
        t0 = bc.now_ns()
        outcome, detail = classify(zc, engine, data)
        dt = bc.now_ns() - t0
        compile_ns.append(dt)
        rec = {"dataset": dataset, "name": name, "outcome": outcome,
               "size_bytes": len(data), "compile_ns": dt}
        if detail:
            rec["detail"] = detail[:200]
        per_schema.append(rec)
        agg[outcome] = agg.get(outcome, 0) + 1
        d = by_dataset.setdefault(dataset, {})
        d[outcome] = d.get(outcome, 0) + 1
        if outcome == "unsupported_feature" and "keyword '" in detail:
            kw = detail.split("keyword '", 1)[1].split("'", 1)[0]
            unsupported_keywords[kw] = unsupported_keywords.get(kw, 0) + 1
        if len(per_schema) % args.progress_every == 0:
            print(f"... {len(per_schema)} schemas, {time.time() - t_start:.1f} s",
                  file=sys.stderr, flush=True)

    total = len(per_schema)
    report = {
        "meta": {
            "corpus": {
                "name": "JSONSchemaBench",
                "repo": "https://github.com/guidance-ai/jsonschemabench",
                "commit": JSB_COMMIT,
                "path": os.path.relpath(args.data_dir),
            },
            "tokenizer": {"name": TOKENIZER_NAME,
                          "revision": TOKENIZER_REVISION,
                          "vocab_size": bundle.vocab_size},
            "engine": {"package_version": zc.__version__,
                       "abi_version": zc.abi_version(),
                       "mode": "adaptive",
                       "profile": "canonical-v1"},
            "seed": bc.SEED,
            "date": time.strftime("%Y-%m-%d %H:%M:%S %z"),
            "wall_seconds": round(time.time() - t_start, 3),
        },
        "aggregate": {
            "total": total,
            "by_outcome": agg,
            "by_dataset": by_dataset,
            "unsupported_keywords": dict(sorted(
                unsupported_keywords.items(), key=lambda kv: -kv[1])),
            "compile_ns": bc.percentile_stats(compile_ns),
            "compiled_rate": agg.get("compiled", 0) / total if total else 0.0,
        },
        "per_schema": per_schema,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    bc.emit({"status": "OK", "total": total, "by_outcome": agg,
             "wall_seconds": report["meta"]["wall_seconds"], "out": args.out})


if __name__ == "__main__":
    main()
