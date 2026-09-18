#!/usr/bin/env python3
"""B3 (смена схем) + B6 (матрица токенизаторов) — аудит 2026-09-15, п.7.

B3: уникальные и повторяющиеся схемы в фиксированном порядке (ТЗ 10.4):
- фаза U: K различных схем jsonschemabench (закреплённый commit в manifest)
  компилируются по одному разу — задержка compile, поддержка/отказы;
- фаза R: та же последовательность повторяется --rounds раз в фиксированном
  порядке — задержка, hit rate кэша, evictions, память (stats ядра, RSS).
Движки: zig lazy и adaptive (hit rate — свойство adaptive-кэша).

B6: матрица токенизаторов (ТЗ 10.4): реальные закреплённые словари
(GPT-2 byte-level BPE 50257; Qwen2.5 byte-level BPE 151665; TinyLlama
SentencePiece byte_fallback 32000 — вторая семья токенизаторов MVP) +
синтетические 32k/128k/256k отдельно. Метрики: prepare, compile, первая
маска, маска p50/p95 на трассе документа, память ядра (mem_used.tokenizer).

Запуск из корня проекта:
    PYTHONPATH=python:benchmarks python3 benchmarks/bench_schemas_tokenizers.py
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_common as bc

JSB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "external", "jsonschemabench")

REAL_TOKENIZERS = [
    {"id": "gpt2", "name": "openai-community/gpt2",
     "revision": "607a30d783dfa663caf39e06633721c8d4cfcd7e",
     "family": "byte-level BPE"},
    {"id": "qwen2.5-1.5b", "name": "Qwen/Qwen2.5-1.5B-Instruct",
     "revision": None, "family": "byte-level BPE"},
    {"id": "tinyllama-sp", "name": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
     "revision": None, "family": "SentencePiece byte_fallback (Llama)",
     "needs_sp_shim": True},
]

# transformers 5.17: Llama-family токенизатор грузится как медленный
# LlamaTokenizer без атрибута byte_fallback (fast-конверсия недоступна),
# хотя tokenizer.json модели — BPE с byte_fallback (vocab '▁'-стиля +
# <0xNN>). Пакет требует флаг byte_fallback для SP-ветки (tokenizers.py).
# Shim: выставляем флаг явно; корректность байтов подтверждается
# попарной конкатенацией против backend.decode (verify_sp_shim).
def verify_sp_shim(tok, bundle, pairs=400, seed=42):
    import random
    backend = getattr(tok, "_tokenizer", None)
    if backend is None:
        return {"status": "SKIP", "reason": "нет _tokenizer backend"}
    rng = random.Random(seed)
    ok, first_space_only, lossy_utf8, bad = 0, 0, 0, []
    for _ in range(pairs):
        i, j = rng.randrange(bundle.vocab_size), rng.randrange(bundle.vocab_size)
        ref = backend.decode([i, j], skip_special_tokens=False).encode("utf-8")
        got = bundle.token_bytes(i) + bundle.token_bytes(j)
        if got == ref:
            ok += 1
        elif got[1:] == ref and got[:1] == b" ":
            # правило SP: ▁ самого первого токена потока не рендерится
            first_space_only += 1
        else:
            # backend.decode декодирует байты как UTF-8 с заменой (U+FFFD):
            # изолированная пара с «висячим» lead/continuation-байтом
            # (напр. <0xD1> + пробел) как UTF-8 невалидна целиком, и
            # эталон лоссов. Байты адаптера обязаны совпасть с тем, что
            # ВИДЕЛ backend, поэтому сравниваем после той же замены; для
            # реальных документов грамматика строк требует полных
            # UTF-8-последовательностей (StrFrame: rem/lo/hi).
            nrm = got.decode("utf-8", errors="replace").encode("utf-8")
            if nrm == ref or (nrm[1:] == ref and nrm[:1] == b" "):
                lossy_utf8 += 1
            else:
                bad.append(i)
    return {"status": "OK", "pairs": pairs, "exact": ok,
            "first_token_space_rule": first_space_only,
            "lossy_utf8_reference": lossy_utf8, "bad": bad[:10]}


DOC = '{"action":"buy","amount":42}'


def synth_bundle(zc, vocab_size):
    """Синтетический побайтовый словарь заданного размера (детерминированный)."""
    tokens = [bytes([b]) for b in range(256)]
    i = 0
    while len(tokens) < vocab_size - 1:
        tokens.append(b"t" + str(i).encode() + b"_" + bytes([65 + i % 26]))
        i += 1
    tokens.append(b"<eos>")
    return zc.TokenizerBundle.from_token_bytes(tokens, eos_ids=[vocab_size - 1],
                                               special_ids=[])


def bench_engine_on_bundle(zc, bundle, schema, trace_steps, mask_replays,
                           limit_mb=256):
    engine = zc.Engine(mode="adaptive", memory_limit_mb=limit_mb, tokenizer=bundle)
    t0 = bc.now_ns()
    constraint = engine.compile(schema)
    t1 = bc.now_ns()
    compile_ns = t1 - t0
    session = constraint.create_session()
    session.fill_mask()
    t2 = bc.now_ns()
    first_mask_ns = t2 - t1
    session.close()
    vocab = engine.vocab_size

    # трасса: повторяем байты документа как id < 256 (синтетика) либо encode
    mask_ns = []
    for _ in range(mask_replays):
        session = constraint.create_session()
        for t in trace_steps:
            t0 = bc.now_ns()
            session.fill_mask()
            mask_ns.append(bc.now_ns() - t0)
            session.accept_token(t)
        session.abort()
        session.close()
    stats = engine.stats()
    constraint.close()
    engine.close()
    return {
        "vocab_size": vocab,
        "compile_ns": compile_ns,
        "first_mask_ns": first_mask_ns,
        "fill_mask_ns": bc.percentile_stats(mask_ns),
        "core_mem_tokenizer_bytes": stats["mem_used"]["tokenizer"],
        "core_mem_total_bytes": stats["mem_used"]["total"],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="closed_object_action_amount")
    ap.add_argument("--corpus-dir", default=bc.CORPUS_DIR)
    ap.add_argument("--b3-schemas", type=int, default=100,
                    help="число различных схем jsonschemabench для фазы U")
    ap.add_argument("--rounds", type=int, default=3,
                    help="повторов последовательности в фазе R")
    ap.add_argument("--mask-replays", type=int, default=50)
    ap.add_argument("--seed", type=int, default=bc.SEED)
    args = ap.parse_args()

    zc = bc.require_core()
    tf = bc.import_or_skip("transformers")

    entry, schema_bytes = bc.load_schema(args.schema, args.corpus_dir)
    schema = json.loads(schema_bytes)
    out = {"status": "OK", "seed": args.seed}

    # ---------------- B3: смена схем ----------------
    # отчёт поддержки на JSB-подмножестве (ТЗ 10.3: перечень с отчётом
    # поддержки/отказов); фазы смены схем — на ПОДДЕРЖИВАЕМЫХ схемах корпуса,
    # иначе кэш нечего измерять (MVP-язык покрывает малую долю сырых JSB).
    files = sorted(glob.glob(os.path.join(JSB_DIR, "**", "*.json"), recursive=True))
    files = files[: args.b3_schemas]
    b3 = {"jsb_dir": os.path.relpath(JSB_DIR), "jsb_files": len(files),
          "phases": {}}
    jsb_schemas = []
    for f in files:
        with open(f, "rb") as fh:
            try:
                jsb_schemas.append(json.loads(fh.read()))
            except Exception:
                pass

    corpus_schemas = []
    for name, kind, expect, _err, raw in bc.load_corpus(args.corpus_dir):
        if kind == "json_schema" and expect:
            corpus_schemas.append((name, json.loads(raw)))
    b3["corpus_supported_schemas"] = len(corpus_schemas)

    bundle = bc.make_byte_tokenizer(zc)  # B3 — про кэш схем, не про словарь
    engine = zc.Engine(mode="adaptive", memory_limit_mb=256, tokenizer=bundle)
    support = {"ok": 0, "unsupported": 0, "error": 0, "error_types": {}}
    for s in jsb_schemas:
        try:
            c = engine.compile(s)
            c.close()
            support["ok"] += 1
        except zc.UnsupportedFeatureError:
            support["unsupported"] += 1
        except Exception as e:
            support["error"] += 1
            t = type(e).__name__
            support["error_types"][t] = support["error_types"].get(t, 0) + 1
    b3["jsb_support"] = support
    engine.close()

    for mode in ("lazy", "adaptive"):
        engine = zc.Engine(mode=mode, memory_limit_mb=256, tokenizer=bundle)
        unique_ns = []
        for name, s in corpus_schemas:
            t0 = bc.now_ns()
            c = engine.compile(s)
            c.close()
            unique_ns.append(bc.now_ns() - t0)
        st0 = engine.stats()
        repeat_ns = []
        for _ in range(args.rounds):
            for name, s in corpus_schemas:
                t0 = bc.now_ns()
                c = engine.compile(s)
                c.close()
                repeat_ns.append(bc.now_ns() - t0)
        st1 = engine.stats()
        b3["phases"][mode] = {
            "unique_compile_ns": bc.percentile_stats(unique_ns),
            "repeat_compile_ns": bc.percentile_stats(repeat_ns),
            "cache": {"hits": st1["cache_hits"] - st0["cache_hits"],
                      "misses": st1["cache_misses"] - st0["cache_misses"],
                      "evictions": st1["cache_evictions"] - st0["cache_evictions"]},
            "core_mem_cache_bytes": st1["mem_used"]["cache"],
        }
        engine.close()

    # фаза сессий: фиксированный порядок схем, повторы; hit rate МАСКИ —
    # основная метрика кэша (ТЗ 10.4 B3). Для каждой схемы — своя заранее
    # сгенерированная валидная трасса (seed 42, побайтовый словарь).
    import random
    engine = zc.Engine(mode="lazy", memory_limit_mb=256, tokenizer=bundle)
    traces = {}
    for name, s in corpus_schemas:
        c = engine.compile(s)
        probe = c.create_session()
        trace, completed = bc.gen_trace(probe, 260, random.Random(args.seed),
                                        max_steps=256)
        probe.abort(); probe.close(); c.close()
        if completed and trace:
            traces[name] = trace
    engine.close()

    engine = zc.Engine(mode="adaptive", memory_limit_mb=256, tokenizer=bundle)
    constraints = {name: engine.compile(s) for name, s in corpus_schemas
                   if name in traces}
    st0 = engine.stats()
    sess_mask_ns, round_reports = [], []
    names = sorted(constraints)
    for rnd in range(args.rounds):
        h0, m0 = engine.stats()["cache_hits"], engine.stats()["cache_misses"]
        for name in names:
            session = constraints[name].create_session()
            for t in traces[name]:
                t0 = bc.now_ns()
                session.fill_mask()
                sess_mask_ns.append(bc.now_ns() - t0)
                session.accept_token(t)
            session.abort()
            session.close()
        st = engine.stats()
        round_reports.append({
            "round": rnd,
            "cache_hits": st["cache_hits"] - h0,
            "cache_misses": st["cache_misses"] - m0,
            "cache_evictions": st["cache_evictions"],
        })
    st1 = engine.stats()
    b3["session_phase"] = {
        "schemas": len(names), "rounds": args.rounds,
        "trace_lens": {n: len(t) for n, t in traces.items()},
        "fill_mask_ns": bc.percentile_stats(sess_mask_ns),
        "rounds_detail": round_reports,
        "cache_total": {"hits": st1["cache_hits"] - st0["cache_hits"],
                        "misses": st1["cache_misses"] - st0["cache_misses"],
                        "evictions": st1["cache_evictions"] - st0["cache_evictions"]},
        "core_mem_cache_bytes": st1["mem_used"]["cache"],
    }
    for c in constraints.values():
        c.close()
    engine.close()
    out["B3"] = b3

    # ---------------- B6: матрица токенизаторов ----------------
    b6 = {}
    if tf is not None:
        for spec in REAL_TOKENIZERS:
            try:
                t0 = bc.now_ns()
                hf_tok = tf.AutoTokenizer.from_pretrained(spec["name"],
                                                          revision=spec["revision"])
                load_ns = bc.now_ns() - t0
                shim = None
                if spec.get("needs_sp_shim"):
                    hf_tok.byte_fallback = True
                t0 = bc.now_ns()
                bundle = zc.TokenizerBundle.from_hf(hf_tok, use_cache=False)
                prepare_ns = bc.now_ns() - t0
                if spec.get("needs_sp_shim"):
                    shim = verify_sp_shim(hf_tok, bundle)
                trace = hf_tok.encode(DOC, add_special_tokens=False)
                adjustment = None
                b0 = bundle.token_bytes(trace[0])
                if b0[:1] == b" ":
                    # правило SP: ▁ первого токена потока не рендерится;
                    # ищем то же написание без ведущего пробела
                    want = b0[1:]
                    alt = next((j for j in range(bundle.vocab_size)
                                if bundle.token_bytes(j) == want), None)
                    if alt is not None:
                        trace = [alt] + trace[1:]
                        adjustment = ("dropped SP leading-space token: "
                                      "document-start rule")
                cat = b"".join(bundle.token_bytes(i) for i in trace)
                assert cat == DOC.encode(), f"трасса не собирает документ: {cat!r}"
                r = bench_engine_on_bundle(zc, bundle, schema, trace,
                                           args.mask_replays)
                if adjustment:
                    r["trace_adjustment"] = adjustment
                r.update(family=spec["family"], hf_load_ns=load_ns,
                         bundle_prepare_ns=prepare_ns, status="OK")
                if shim is not None:
                    r["sp_shim_verify"] = shim
                    if shim.get("status") == "OK" and shim.get("bad"):
                        # ТЗ риск-таблица: неверное байтовое представление ->
                        # адаптер отклоняется, замеры помечаются несертифицир.
                        r["certified"] = False
                        r["certification_note"] = (
                            "попарная конкатенация байтов расходится с "
                            "backend.decode сверх объяснимых правил "
                            "(первый ▁, lossy-замена U+FFFD для невалидного "
                            "в изоляции UTF-8): байтовое представление "
                            "части токенов неверно. "
                            "Адаптер отклонён; требуется исправление пакета.")
                    elif shim.get("status") == "OK":
                        r["certified"] = True
            except Exception as e:
                r = {"status": "ERROR", "family": spec["family"],
                     "error": f"{type(e).__name__}: {e}"[:300]}
            b6[spec["id"]] = r
            print(f"B6 {spec['id']}: {r.get('status')}", file=sys.stderr, flush=True)
    else:
        b6["_real"] = {"status": "SKIP", "reason": "transformers недоступен"}

    for size, limit_mb in ((32768, 256), (131072, 512), (262144, 1024)):
        t0 = bc.now_ns()
        bundle = synth_bundle(zc, size)
        build_ns = bc.now_ns() - t0
        # трасса из однобайтовых токенов документа
        trace = list(DOC.encode("utf-8"))
        try:
            r = bench_engine_on_bundle(zc, bundle, schema, trace,
                                       args.mask_replays, limit_mb)
            r.update(family="synthetic byte vocab", bundle_build_ns=build_ns,
                     engine_memory_limit_mb=limit_mb, status="OK")
        except Exception as e:
            r = {"status": "ERROR", "engine_memory_limit_mb": limit_mb,
                 "error": f"{type(e).__name__}: {e}"[:300]}
        b6[f"synthetic_{size}"] = r
        print(f"B6 synthetic_{size}: {r.get('status')}", file=sys.stderr, flush=True)
    out["B6"] = b6

    bc.emit(out)


if __name__ == "__main__":
    main()
