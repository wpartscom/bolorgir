"""Общие утилиты бенчмарков zig-constraints.

Конвенции (ТЗ 10.5):
- фиксированный seed = 42;
- таймер time.perf_counter_ns;
- peak RSS через resource.getrusage(RUSAGE_SELF).ru_maxrss;
- без ядра (пакет zig_constraints не собран) каждый скрипт печатает
  {"status": "SKIP", ...} и завершается с кодом 0.
"""

import json
import os
import random
import resource
import sys
import time

SEED = 42

CORPUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus")


def import_or_skip(package):
    try:
        return __import__(package)
    except Exception:
        return None


def skip(reason):
    print(json.dumps({"status": "SKIP", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


def require_core():
    zc = import_or_skip("zig_constraints")
    if zc is None:
        skip("пакет zig_constraints не установлен: ядро ещё не собрано")
    return zc


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=None))


def now_ns():
    return time.perf_counter_ns()


def peak_rss_bytes():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def percentile_stats(samples):
    s = sorted(samples)
    n = len(s)
    if n == 0:
        return {"count": 0}
    def q(p):
        return s[min(n - 1, max(0, int(round(p * (n - 1)))))]
    return {
        "count": n,
        "min": s[0],
        "p50": q(0.50),
        "p95": q(0.95),
        "p99": q(0.99),
        "max": s[-1],
        "mean": sum(s) / n,
    }


def load_corpus(corpus_dir=CORPUS_DIR):
    """Итерация по corpus/index.json: (name, kind, expect_support, raw_bytes)."""
    with open(os.path.join(corpus_dir, "index.json"), encoding="utf-8") as f:
        index = json.load(f)
    for name, entry in index["entries"].items():
        with open(os.path.join(corpus_dir, entry["file"]), "rb") as f:
            yield name, entry["kind"], entry["expect_support"], entry["expect_error"], f.read()


def load_schema(name, corpus_dir=CORPUS_DIR):
    with open(os.path.join(corpus_dir, "index.json"), encoding="utf-8") as f:
        index = json.load(f)
    entry = index["entries"][name]
    with open(os.path.join(corpus_dir, entry["file"]), "rb") as f:
        return entry, f.read()


def make_byte_tokenizer(zc, hf_name=None, hf_revision=None):
    """Токенизатор для бенчмарков ядра.

    hf_name задан -> TokenizerBundle.from_hf(AutoTokenizer.from_pretrained(...))
    (семья byte-level BPE либо byte fallback; ревизия обязана быть
    закреплена в manifest).
    Иначе — синтетический побайтовый словарь: 256 однобайтовых токенов
    + типичные пары байтов + EOS.
    """
    if hf_name:
        from transformers import AutoTokenizer
        hf_tok = AutoTokenizer.from_pretrained(hf_name, revision=hf_revision)
        return zc.TokenizerBundle.from_hf(hf_tok)
    tokens = [bytes([b]) for b in range(256)]
    tokens += [b"{}", b"[]", b'",', b'":', b", ", b"true", b"false", b"null"]
    eos_id = len(tokens)
    tokens.append(b"<eos>")
    return zc.TokenizerBundle.from_token_bytes(tokens, eos_ids=[eos_id], special_ids=[])


def mask_bits(mask, vocab_size):
    """Индексы разрешённых токенов из маски (numpy array или bytes из uint32)."""
    if hasattr(mask, "tolist"):
        words = mask.tolist()
    else:
        words = [int.from_bytes(mask[i:i + 4], "little") for i in range(0, len(mask), 4)]
    out = []
    for t in range(vocab_size):
        if (words[t // 32] >> (t % 32)) & 1:
            out.append(t)
    return out


def gen_trace(session, vocab_size, rng, max_steps=4096):
    """Детерминированная валидная трасса: на каждом шаге случайный (по rng)
    разрешённый токен, пока состояние не станет принимающим.

    Возвращает (trace, completed).
    """
    trace = []
    for _ in range(max_steps):
        if session.can_end():
            return trace, True
        mask = session.fill_mask()
        allowed = mask_bits(mask, vocab_size)
        if not allowed:
            return trace, False
        t = allowed[rng.randrange(len(allowed))]
        session.accept_token(t)
        trace.append(t)
    return trace, False
