#!/usr/bin/env python3
"""Сопоставимое сравнение на ОДИНАКОВОЙ нагрузке (аудит 2026-09-15, п.2).

Все три движка (zig-constraints lazy/adaptive, XGrammar, llguidance):
- один и тот же токенизатор HF (по умолчанию openai-community/gpt2,
  revision закреплён в manifest.json);
- одна и та же схема из корпуса;
- одна и та же токенная трасса (заранее заданная валидная трасса, ТЗ 10.5):
  трасса генерируется движком zig (профиль canonical-v1 — самый строгий,
  компактный JSON), затем проверяется покрытие каждого токена трассы
  масками XGrammar и llguidance. Общая трасса = принята всеми тремя.

Метрики (ТЗ 10.4 B1/B2, 10.5): tokenizer prepare, cold compile (30 свежих
компиляторов), warm compile, первая маска, fill_mask/accept p50/p95/p99/max
на общей трассе (>= 10k наблюдений), peak RSS процесса.

Запуск из корня проекта:
    PYTHONPATH=python:benchmarks python3 benchmarks/bench_compare_uniform.py \
        --schema closed_object_action_amount
"""

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_common as bc

TOKENIZER_NAME = "openai-community/gpt2"
TOKENIZER_REVISION = "607a30d783dfa663caf39e06633721c8d4cfcd7e"


# ---------- общая трасса ----------
# Проверка приёма трассы — по факту accept/consume (это истина о допустимости),
# НЕ по биту маски: у llguidance 1.8.0 внутри форсированных литералов маска
# содержит только «жадное» написание (напр. после '{" только 'action', но
# 'act' принимается consume_token и продолжается 'ion'). Маска при этом —
# штатный продакшн-путь, её задержку и измеряем.

def zig_gen_trace(zc, constraint, vocab_size, rng, max_steps=256):
    session = constraint.create_session()
    trace, completed = bc.gen_trace(session, vocab_size, rng, max_steps=max_steps)
    session.abort()
    return trace, completed


def zig_check_trace(constraint, trace):
    session = constraint.create_session()
    try:
        for t in trace:
            session.accept_token(t)
        return session.can_end()
    except Exception:
        return False
    finally:
        session.abort()


def xg_check_trace(xg, np, compiled, trace):
    m = xg.GrammarMatcher(compiled)
    for t in trace:
        if not m.accept_token(t):
            return False
    return True


def lg_check_trace(lg, tok, grm, trace):
    m = lg.LLMatcher(tok, grm)
    for t in trace:
        if m.is_error() or not m.consume_token(t):
            return False
    return not m.is_error()


# ---------- измерения ----------

def bench_zig(zc, hf_tok, schema, schema_bytes, trace, args, mode):
    vocab_size = None
    prepare_ns, cold_compile, first_mask = [], [], []
    for _ in range(args.repeats):
        t0 = bc.now_ns()
        bundle = zc.TokenizerBundle.from_hf(hf_tok, use_cache=False)
        t1 = bc.now_ns()
        engine = zc.Engine(mode=mode, memory_limit_mb=256, tokenizer=bundle)
        t2 = bc.now_ns()
        constraint = engine.compile(schema)
        t3 = bc.now_ns()
        session = constraint.create_session()
        session.fill_mask()
        t4 = bc.now_ns()
        prepare_ns.append(t1 - t0)
        cold_compile.append(t3 - t2)
        first_mask.append(t4 - t3)
        vocab_size = engine.vocab_size
        session.close()
        constraint.close()
        engine.close()

    bundle = zc.TokenizerBundle.from_hf(hf_tok)
    engine = zc.Engine(mode=mode, memory_limit_mb=256, tokenizer=bundle)
    constraint = engine.compile(schema)
    warm_compile = []
    for _ in range(max(args.repeats, 100)):
        t0 = bc.now_ns()
        c = engine.compile(schema)
        warm_compile.append(bc.now_ns() - t0)
        c.close()

    for _ in range(3):  # прогрев (ТЗ 10.1: warmup_iterations)
        session = constraint.create_session()
        for t in trace:
            session.fill_mask()
            session.accept_token(t)
        session.abort()
        session.close()
    mask_ns, accept_ns = [], []
    observations = 0
    while observations < args.min_observations:
        session = constraint.create_session()
        for t in trace:
            t0 = bc.now_ns()
            session.fill_mask()
            t1 = bc.now_ns()
            session.accept_token(t)
            t2 = bc.now_ns()
            mask_ns.append(t1 - t0)
            accept_ns.append(t2 - t1)
        observations += len(trace)
        session.abort()
        session.close()
    constraint.close()
    engine.close()

    return {
        "tokenizer_prepare_ns": bc.percentile_stats(prepare_ns),
        "cold_compile_ns": bc.percentile_stats(cold_compile),
        "warm_compile_ns": bc.percentile_stats(warm_compile),
        "first_mask_ns": bc.percentile_stats(first_mask),
        "fill_mask_ns": bc.percentile_stats(mask_ns),
        "accept_ns": bc.percentile_stats(accept_ns),
        "observations": len(mask_ns),
        "vocab_size": vocab_size,
    }


def bench_xgrammar(xg, np, hf_tok, schema_str, trace, args):
    prepare_ns, cold_compile, first_mask = [], [], []
    vocab_size = None
    for _ in range(args.repeats):
        t0 = bc.now_ns()
        info = xg.TokenizerInfo.from_huggingface(hf_tok)
        t1 = bc.now_ns()
        compiler = xg.GrammarCompiler(info, cache_enabled=False)
        t2 = bc.now_ns()
        compiled = compiler.compile_json_schema(schema_str)
        t3 = bc.now_ns()
        matcher = xg.GrammarMatcher(compiled)
        bitmask = xg.allocate_token_bitmask(1, info.vocab_size)
        matcher.fill_next_token_bitmask(bitmask)
        t4 = bc.now_ns()
        prepare_ns.append(t1 - t0)
        cold_compile.append(t3 - t2)
        first_mask.append(t4 - t3)
        vocab_size = info.vocab_size

    info = xg.TokenizerInfo.from_huggingface(hf_tok)
    compiler = xg.GrammarCompiler(info)
    warm_compile = []
    for _ in range(max(args.repeats, 100)):
        t0 = bc.now_ns()
        compiler.compile_json_schema(schema_str)
        warm_compile.append(bc.now_ns() - t0)

    compiled = compiler.compile_json_schema(schema_str)
    bitmask = xg.allocate_token_bitmask(1, info.vocab_size)
    for _ in range(3):  # прогрев
        m = xg.GrammarMatcher(compiled)
        for t in trace:
            m.fill_next_token_bitmask(bitmask)
            m.accept_token(t)
    mask_ns, accept_ns = [], []
    observations = 0
    while observations < args.min_observations:
        m = xg.GrammarMatcher(compiled)
        for t in trace:
            t0 = bc.now_ns()
            m.fill_next_token_bitmask(bitmask)
            t1 = bc.now_ns()
            m.accept_token(t)
            t2 = bc.now_ns()
            mask_ns.append(t1 - t0)
            accept_ns.append(t2 - t1)
        observations += len(trace)

    return {
        "tokenizer_prepare_ns": bc.percentile_stats(prepare_ns),
        "cold_compile_ns": bc.percentile_stats(cold_compile),
        "warm_compile_ns": bc.percentile_stats(warm_compile),
        "first_mask_ns": bc.percentile_stats(first_mask),
        "fill_mask_ns": bc.percentile_stats(mask_ns),
        "accept_ns": bc.percentile_stats(accept_ns),
        "observations": len(mask_ns),
        "vocab_size": vocab_size,
    }


def bench_llguidance(lg, lghf, hf_tok, schema_str, trace, args):
    prepare_ns, cold_compile, first_mask = [], [], []
    for _ in range(args.repeats):
        t0 = bc.now_ns()
        tok = lghf.from_tokenizer(hf_tok)
        t1 = bc.now_ns()
        grm = lg.grammar_from("json_schema", schema_str)
        t2 = bc.now_ns()
        m = lg.LLMatcher(tok, grm)
        m.compute_bitmask()
        t3 = bc.now_ns()
        prepare_ns.append(t1 - t0)
        cold_compile.append(t2 - t1)
        first_mask.append(t3 - t2)

    tok = lghf.from_tokenizer(hf_tok)
    warm_compile = []
    for _ in range(max(args.repeats, 100)):
        t0 = bc.now_ns()
        lg.grammar_from("json_schema", schema_str)
        warm_compile.append(bc.now_ns() - t0)

    grm = lg.grammar_from("json_schema", schema_str)
    for _ in range(3):  # прогрев
        m = lg.LLMatcher(tok, grm)
        for t in trace:
            m.compute_bitmask()
            m.consume_token(t)
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

    return {
        "tokenizer_prepare_ns": bc.percentile_stats(prepare_ns),
        "cold_compile_ns": bc.percentile_stats(cold_compile),
        "warm_compile_ns": bc.percentile_stats(warm_compile),
        "first_mask_ns": bc.percentile_stats(first_mask),
        "fill_mask_ns": bc.percentile_stats(mask_ns),
        "accept_ns": bc.percentile_stats(accept_ns),
        "observations": len(mask_ns),
        "vocab_size": tok.vocab_size,
    }


def _version(pkg):
    try:
        import importlib.metadata as md
        return md.version(pkg)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="closed_object_action_amount")
    ap.add_argument("--corpus-dir", default=bc.CORPUS_DIR)
    ap.add_argument("--repeats", type=int, default=30)
    ap.add_argument("--min-observations", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=bc.SEED)
    ap.add_argument("--tokenizer", default=TOKENIZER_NAME)
    ap.add_argument("--tokenizer-revision", default=TOKENIZER_REVISION)
    ap.add_argument("--max-trace-attempts", type=int, default=20)
    ap.add_argument("--document", default=None,
                    help="явный валидный JSON-документ; трасса = его каноническая "
                         "BPE-токенизация (проверяется приёмом на всех движках)")
    args = ap.parse_args()

    zc = bc.require_core()
    xg = bc.import_or_skip("xgrammar")
    lg = bc.import_or_skip("llguidance")
    np = bc.import_or_skip("numpy")
    tf = bc.import_or_skip("transformers")
    if None in (xg, lg, np, tf):
        bc.skip("нужны xgrammar, llguidance, numpy, transformers")
    import llguidance.hf as lghf

    entry, schema_bytes = bc.load_schema(args.schema, args.corpus_dir)
    if entry["kind"] != "json_schema" or not entry["expect_support"]:
        bc.skip(f"схема {args.schema} не является поддерживаемой json_schema")
    schema = json.loads(schema_bytes)
    schema_str = schema_bytes.decode("utf-8")

    hf_tok = tf.AutoTokenizer.from_pretrained(args.tokenizer,
                                              revision=args.tokenizer_revision)

    # --- общая трасса: либо явный документ, либо генерация zig (строгий
    # профиль canonical-v1); приём подтверждают все три движка ---
    bundle = zc.TokenizerBundle.from_hf(hf_tok)
    engine = zc.Engine(mode="lazy", memory_limit_mb=256, tokenizer=bundle)
    constraint = engine.compile(schema)
    vocab_size = engine.vocab_size

    info = xg.TokenizerInfo.from_huggingface(hf_tok)
    xg_compiled = xg.GrammarCompiler(info).compile_json_schema(schema_str)
    xg_bitmask = xg.allocate_token_bitmask(1, info.vocab_size)
    lg_tok = lghf.from_tokenizer(hf_tok)
    lg_grm = lg.grammar_from("json_schema", schema_str)

    candidates = []
    if args.document is not None:
        try:
            import jsonschema
            jsonschema.validate(json.loads(args.document), schema)
        except Exception as e:
            bc.emit({"status": "ERROR", "case": "uniform",
                     "reason": f"--document не валиден по схеме: {e}"})
            return
        candidates.append(hf_tok.encode(args.document))
    else:
        rng = random.Random(args.seed)
        for _ in range(args.max_trace_attempts):
            trace, completed = zig_gen_trace(zc, constraint, vocab_size, rng)
            if completed and trace:
                candidates.append(trace)

    common_trace = None
    rejects = {}
    for trace in candidates:
        ok = True
        for name, check in (("zig", lambda: zig_check_trace(constraint, trace)),
                            ("xgrammar", lambda: xg_check_trace(xg, np, xg_compiled, trace)),
                            ("llguidance", lambda: lg_check_trace(lg, lg_tok, lg_grm, trace))):
            if not check():
                rejects[name] = rejects.get(name, 0) + 1
                ok = False
                break
        if ok:
            common_trace = trace
            break
    constraint.close()
    engine.close()

    if common_trace is None:
        bc.emit({"status": "ERROR", "case": "uniform",
                 "reason": "не найдена трасса, принимаемая всеми тремя движками",
                 "candidates": len(candidates), "rejects": rejects})
        return

    trace_text = hf_tok.decode(common_trace)
    engines = {}
    engines["zig_lazy"] = bench_zig(zc, hf_tok, schema, schema_bytes,
                                    common_trace, args, "lazy")
    engines["zig_adaptive"] = bench_zig(zc, hf_tok, schema, schema_bytes,
                                        common_trace, args, "adaptive")
    engines["xgrammar"] = bench_xgrammar(xg, np, hf_tok, schema_str,
                                         common_trace, args)
    engines["llguidance"] = bench_llguidance(lg, lghf, hf_tok, schema_str,
                                             common_trace, args)

    bc.emit({
        "status": "OK", "case": "uniform_load", "schema": args.schema,
        "seed": args.seed,
        "tokenizer": {"name": args.tokenizer, "revision": args.tokenizer_revision,
                      "vocab_size": vocab_size},
        "versions": {"xgrammar": _version("xgrammar"),
                     "llguidance": _version("llguidance"),
                     "zig_constraints": zc.__version__,
                     "transformers": _version("transformers")},
        "trace": {"ids": common_trace, "len": len(common_trace),
                  "text": trace_text},
        "engines": engines,
        "peak_rss_bytes": bc.peak_rss_bytes(),
        "note": "одинаковые токенизатор/схема/трасса; трасса из пересечения языков: "
                "приём каждого токена подтверждён accept/consume всех трёх "
                "движков (у llguidance 1.8.0 маска внутри форсированных "
                "литералов содержит только жадное написание — проверка по "
                "consume_token, см. REPORT)",
    })


if __name__ == "__main__":
    main()
