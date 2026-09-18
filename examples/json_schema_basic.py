#!/usr/bin/env python3
"""
Пример: JSON Schema (FR-1/FR-2) без Hugging Face.

Мини-токенизатор — побайтовый (id 0..255) + EOS (id 256), собран вручную.
«Модель» — случайный выбор из разрешённых токенов; сгенерированный
документ валидируется стандартным json.loads + проверками схемы.

Запуск (после сборки расширения, см. python/setup.py):
    PYTHONPATH=python python3 examples/json_schema_basic.py
"""

import json
import random
import sys

from zig_constraints import Engine, TokenizerBundle

EOS_ID = 256

SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell"]},
        "amount": {"type": "integer"},
        "note": {"type": "string", "maxLength": 24},
    },
    "required": ["action", "amount"],
    "additionalProperties": False,
}


def mini_tokenizer() -> TokenizerBundle:
    tokens = [bytes([b]) for b in range(256)] + [b"<eos>"]
    return TokenizerBundle.from_token_bytes(tokens, eos_ids=[EOS_ID], special_ids=[])


def sample_document(session, rng: random.Random, max_steps: int = 8192):
    out = bytearray()
    for _ in range(max_steps):
        allowed = session.allowed_token_ids()
        regular = [t for t in allowed if t != EOS_ID]
        if session.can_end() and (not regular or rng.random() < 0.3):
            session.accept_token(EOS_ID)
            session.finish()
            return bytes(out), True
        if not regular:
            return bytes(out), False
        tok = rng.choice(regular)
        session.accept_token(tok)
        out += bytes([tok])
    return bytes(out), False


def validate(doc: dict) -> None:
    assert doc["action"] in ("buy", "sell"), doc
    assert isinstance(doc["amount"], int), doc
    assert set(doc) <= {"action", "amount", "note"}, doc
    if "note" in doc:
        assert isinstance(doc["note"], str) and len(doc["note"]) <= 24, doc


def main() -> int:
    rng = random.Random(20260914)
    with Engine(mode="adaptive") as engine:
        with engine.compile(SCHEMA, tokenizer=mini_tokenizer()) as constraint:
            with constraint.create_session() as session:
                raw, ok = sample_document(session, rng)
    text = raw.decode("utf-8", errors="replace")
    print(f"completed={ok}")
    print(f"document: {text}")
    if not ok:
        return 1
    doc = json.loads(text)
    validate(doc)
    print("json.loads + проверки схемы: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
