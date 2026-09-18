#!/usr/bin/env python3
"""Сравнительный раннер: те же сценарии B1/B2 через llguidance.

Участник сравнения по ТЗ 10.2 (закреплённая версия — benchmarks/manifest.json).
Токенизатор: openai-community/gpt2, revision закреплён (byte-level BPE).
Профиль JSON у llguidance отличается от canonical-v1; побитовое сравнение
масок — только на явно размеченном пересечении языков (ТЗ 10.2).
"""

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bench_common as bc

TOKENIZER_NAME = "openai-community/gpt2"
TOKENIZER_REVISION = "607a30d783dfa663caf39e06633721c8d4cfcd7e"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="closed_object_action_amount")
    ap.add_argument("--corpus-dir", default=bc.CORPUS_DIR)
    ap.add_argument("--repeats", type=int, default=30)
    ap.add_argument("--min-observations", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=bc.SEED)
    args = ap.parse_args()

    lg = bc.import_or_skip("llguidance")
    tf = bc.import_or_skip("transformers")
    np = bc.import_or_skip("numpy")
    if lg is None or tf is None or np is None:
        bc.skip("llguidance/transformers/numpy не установлены: сравнение pending "
                "(команда установки — benchmarks/README.md)")

    import llguidance.hf as lghf

    entry, schema_bytes = bc.load_schema(args.schema, args.corpus_dir)
    schema_str = schema_bytes.decode("utf-8")
    if entry["kind"] != "json_schema" or not entry["expect_support"]:
        bc.skip(f"схема {args.schema} не является поддерживаемой json_schema")

    # B1: подготовка
    t0 = bc.now_ns()
    hf_tok = tf.AutoTokenizer.from_pretrained(TOKENIZER_NAME,
                                              revision=TOKENIZER_REVISION)
    tok = lghf.from_tokenizer(hf_tok)
    t1 = bc.now_ns()
    tokenizer_prepare_ns = t1 - t0

    cold_compile, first_mask = [], []
    for _ in range(args.repeats):
        t0 = bc.now_ns()
        grm = lg.grammar_from("json_schema", schema_str)
        t1 = bc.now_ns()
        m = lg.LLMatcher(tok, grm)
        m.compute_bitmask()
        t2 = bc.now_ns()
        cold_compile.append(t1 - t0)
        first_mask.append(t2 - t1)

    # у llguidance нет межграмматического кэша в этом API: «тёплый» compile —
    # повторный grammar_from в том же процессе
    warm_compile = []
    for _ in range(max(args.repeats, 100)):
        t0 = bc.now_ns()
        lg.grammar_from("json_schema", schema_str)
        warm_compile.append(bc.now_ns() - t0)

    # B2: маска/accept на трассе
    rng = random.Random(args.seed)
    grm = lg.grammar_from("json_schema", schema_str)
    vocab_size = tok.vocab_size

    def allowed_tokens(m):
        bm = m.compute_bitmask()
        a = np.frombuffer(bm, dtype=np.uint32)
        bits = np.unpackbits(a.view(np.uint8), bitorder="little")[:vocab_size]
        return np.flatnonzero(bits).tolist()

    def gen_trace():
        m = lg.LLMatcher(tok, grm)
        trace = []
        for _ in range(4096):
            if m.is_stopped():
                return trace, True
            allowed = allowed_tokens(m)
            if not allowed:
                return trace, False
            t = allowed[rng.randrange(len(allowed))]
            if m.is_error() or not m.consume_token(t):
                return trace, False
            trace.append(t)
        return trace, False

    trace, completed = gen_trace()
    if not completed or not trace:
        bc.emit({"status": "ERROR", "engine": "llguidance", "case": "B2",
                 "reason": "не удалось сгенерировать валидную трассу",
                 "completed": completed, "trace_len": len(trace)})
        return

    mask_ns, accept_ns = [], []
    observations = 0
    while observations < args.min_observations:
        m = lg.LLMatcher(tok, grm)
        for t in trace:
            t0 = bc.now_ns()
            m.compute_bitmask()
            t1 = bc.now_ns()
            m.consume_token(t)
            t2 = bc.now_ns()
            mask_ns.append(t1 - t0)
            accept_ns.append(t2 - t1)
        observations += len(trace)

    bc.emit({
        "status": "OK", "engine": "llguidance", "engine_version": _version("llguidance"),
        "schema": args.schema, "seed": args.seed,
        "tokenizer": {"name": TOKENIZER_NAME, "revision": TOKENIZER_REVISION,
                      "vocab_size": vocab_size},
        "B1": {
            "tokenizer_prepare_ns": tokenizer_prepare_ns,
            "cold_compile_ns": bc.percentile_stats(cold_compile),
            "warm_compile_ns": bc.percentile_stats(warm_compile),
            "first_mask_ns": bc.percentile_stats(first_mask),
            "peak_rss_bytes": bc.peak_rss_bytes(),
        },
        "B2": {
            "trace_len": len(trace),
            "observations": {"mask": len(mask_ns), "accept": len(accept_ns)},
            "fill_mask_ns": bc.percentile_stats(mask_ns),
            "accept_ns": bc.percentile_stats(accept_ns),
        },
        "note": "профиль JSON llguidance != canonical-v1; сравнение задержек "
                "на эквивалентной схеме, не побитовое",
    })


def _version(pkg):
    try:
        import importlib.metadata as md
        return md.version(pkg)
    except Exception:
        return None


if __name__ == "__main__":
    main()
