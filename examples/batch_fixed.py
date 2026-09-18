#!/usr/bin/env python3
"""
Пример: фиксированный батч независимых сессий (FR-11) без Hugging Face.

Каждая строка батча — своя сессия со своей схемой; маски запрашиваются
батчем (fill_masks_batch), строки завершаются независимо. Мини-токенизатор
— побайтовый (id 0..255) + EOS (id 256), собран вручную.

Запуск (после сборки расширения, см. python/setup.py):
    PYTHONPATH=python python3 examples/batch_fixed.py
"""

import random
import sys

from zig_constraints import Engine, TokenizerBundle, fill_masks_batch

EOS_ID = 256

SCHEMAS = [
    {
        "type": "object",
        "properties": {
            "city": {"type": "string", "enum": ["Oslo", "Berlin", "Tokyo"]},
            "temp": {"type": "integer"},
        },
        "required": ["city", "temp"],
        "additionalProperties": False,
    },
    {
        "type": "array",
        "items": {"type": "integer"},
        "minItems": 1,
        "maxItems": 4,
    },
]


def mini_tokenizer() -> TokenizerBundle:
    tokens = [bytes([b]) for b in range(256)] + [b"<eos>"]
    return TokenizerBundle.from_token_bytes(tokens, eos_ids=[EOS_ID], special_ids=[])


def allowed_ids(mask: bytes):
    out = []
    for wi in range(len(mask) // 4):
        w = int.from_bytes(mask[wi * 4 : wi * 4 + 4], "little")
        while w:
            bit = (w & -w).bit_length() - 1
            out.append(wi * 32 + bit)
            w &= w - 1
    return out


def main() -> int:
    rng = random.Random(20260914)
    with Engine(mode="adaptive") as engine:
        bundle = mini_tokenizer()
        constraints = [engine.compile(s, tokenizer=bundle) for s in SCHEMAS]
        sessions = [c.create_session() for c in constraints]
        docs = [bytearray() for _ in sessions]
        done = [False] * len(sessions)
        finished = [False] * len(sessions)

        for _step in range(8192):
            active = [i for i in range(len(sessions)) if not done[i]]
            if not active:
                break
            masks = fill_masks_batch([sessions[i] for i in active])
            for row, mask in zip(active, masks):
                session = sessions[row]
                allowed = [t for t in allowed_ids(mask) if t != EOS_ID]
                if session.can_end() and (not allowed or rng.random() < 0.3):
                    session.accept_token(EOS_ID)
                    session.finish()
                    done[row] = True
                    finished[row] = True
                    continue
                if not allowed:
                    session.abort()  # тупик (FR: маска пуста) — строка мертва
                    done[row] = True
                    continue
                tok = rng.choice(allowed)
                session.accept_token(tok)
                docs[row] += bytes([tok])

        for session in sessions:
            session.close()
        for constraint in constraints:
            constraint.close()

    for i, (raw, ok) in enumerate(zip(docs, finished)):
        print(f"row {i}: completed={ok} document={raw.decode('utf-8', errors='replace')}")
    return 0 if all(finished) else 1


if __name__ == "__main__":
    sys.exit(main())
