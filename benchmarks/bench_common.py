"""Shared utilities for Bolorgir benchmarks.

Conventions (SPEC 10.5):
- fixed seed = 42;
- timer time.perf_counter_ns;
- peak RSS via resource.getrusage(RUSAGE_SELF).ru_maxrss;
- without the core (bolorgir package not built) every script prints
  {"status": "SKIP", ...} and exits with code 0.
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
    zc = import_or_skip("bolorgir")
    if zc is None:
        skip("bolorgir package is not installed: the core is not built yet")
    return zc


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=None))


def now_ns():
    return time.perf_counter_ns()


def peak_rss_bytes():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def current_rss_bytes():
    """Current process RSS (VmRSS); falls back to the historical peak without /proc.

    SPEC 10.5: the plateau needs current RSS, not ru_maxrss - an early
    peak otherwise masks later growth.
    """
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as f:
            rss_pages = int(f.read().split()[1])
        return rss_pages * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return peak_rss_bytes()


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
    """Iterate over corpus/index.json: (name, kind, expect_support, raw_bytes)."""
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
    """Tokenizer for core benchmarks.

    hf_name set -> TokenizerBundle.from_hf(AutoTokenizer.from_pretrained(...))
    (byte-level BPE or byte fallback family; the revision must be
    pinned in manifest).
    Otherwise - a synthetic byte vocabulary: 256 single-byte tokens
    + common byte pairs + EOS.
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
    """Allowed token indices from a mask (numpy array or bytes of uint32)."""
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
    """Deterministic valid trace: each step takes a random (by rng)
    allowed token until the state becomes accepting.

    Returns (trace, completed).
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
