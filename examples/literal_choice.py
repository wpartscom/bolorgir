#!/usr/bin/env python3
"""
Пример: буквальный выбор (FR-3) без Hugging Face.

Мини-токенизатор собран вручную: id 0..255 — однобайтовые токены,
id 256 — EOS. «Модель» — равномерный выбор из разрешённых токенов
с фиксированным seed; маска ядра гарантирует, что ответ — ровно одна
из буквальных строк.

Запуск (после сборки расширения, см. python/setup.py):
    PYTHONPATH=python python3 examples/literal_choice.py
"""

import random
import sys

from zig_constraints import Engine, TokenizerBundle

EOS_ID = 256


def mini_tokenizer() -> TokenizerBundle:
    tokens = [bytes([b]) for b in range(256)] + [b"<eos>"]
    return TokenizerBundle.from_token_bytes(tokens, eos_ids=[EOS_ID], special_ids=[])


def sample_document(session, rng: random.Random, max_steps: int = 4096):
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


def main() -> int:
    answers = [
        "definitely yes",
        "probably not",
        "need more data",
        "абсолютно согласен",
    ]
    rng = random.Random(20260914)
    with Engine(mode="lazy") as engine:
        with engine.compile_literals(answers, tokenizer=mini_tokenizer()) as constraint:
            with constraint.create_session() as session:
                doc, ok = sample_document(session, rng)
    text = doc.decode("utf-8", errors="replace")
    print(f"completed={ok}")
    print(f"document: {text!r}")
    if not ok or text not in answers:
        print("ОШИБКА: документ не из списка альтернатив", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
