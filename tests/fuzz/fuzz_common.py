"""T4 fuzz: shared utilities — tokenizer, artifacts, results.

Directories:
  tests/fuzz/artifacts/   — crash reproducers + campaign JSON reports;
  tests/fuzz/corpus/auto/ — saved schema corpus (sample).
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_TESTS = os.path.dirname(_HERE)
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

import reference as ref  # noqa: E402
import zg_ctypes  # noqa: E402

ARTIFACTS = os.path.join(_HERE, "artifacts")
CORPUS_AUTO = os.path.join(_HERE, "corpus", "auto")
os.makedirs(ARTIFACTS, exist_ok=True)
os.makedirs(CORPUS_AUTO, exist_ok=True)


def make_fuzz_tokenizer() -> ref.TokenizerSpec:
    """Fixed fuzz tokenizer: all 256 single bytes (byte-complete, so the
    compile-time coverage gate accepts any generated schema) + multi-char
    pieces (structural, numbers, escape, UTF-8). Vocab not a multiple of 32."""
    pieces = [
        b'{"', b'":"', b'",', b'"k0"', b'"k1"', b'"k2"',
        b"true", b"false", b"null", b"10", b"42", b"1e5", b"-0", b"0.5",
        b"\\n", b"\\u00", "é".encode("utf-8"), b'[1', b'"}',
    ]
    tokens = [bytes([i]) for i in range(256)] + pieces
    vocab = len(tokens) + 2
    assert vocab % 32 != 0
    return ref.TokenizerSpec(tokens=tuple(tokens) + (b"", b"<pad>"),
                             eos_ids=(vocab - 2,), special_ids=(vocab - 1,)).validate()


def ids_from_words(words: list[int], vocab_size: int) -> set[int]:
    ids = set()
    for i in range(vocab_size):
        if words[i // 32] >> (i % 32) & 1:
            ids.add(i)
    return ids


_artifact_seq = [0]


def save_artifact(kind: str, payload: dict) -> str:
    """Saves a reproducer into artifacts/. Returns the path."""
    _artifact_seq[0] += 1
    name = f"{kind}_{os.getpid()}_{_artifact_seq[0]}.json"
    path = os.path.join(ARTIFACTS, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, default=str)
    return path


def save_results(name: str, payload: dict) -> str:
    path = os.path.join(ARTIFACTS, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return path


_corpus_seq = [0]


def save_corpus_schema(schema_text: str) -> str:
    _corpus_seq[0] += 1
    path = os.path.join(CORPUS_AUTO, f"py_{_corpus_seq[0]:04d}.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(schema_text)
    return path
