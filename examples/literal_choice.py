#!/usr/bin/env python3
"""
Example: literal choice (FR-3) without Hugging Face.

Mini-tokenizer is assembled by hand: ids 0..255 are single-byte tokens,
id 256 is EOS. The "model" samples uniformly among allowed tokens with a
fixed seed; the core mask guarantees the answer is exactly one of the
literal strings.

Run (after building the extension, see python/setup.py):
    PYTHONPATH=python python3 examples/literal_choice.py
"""

import random
import sys

from bolorgir import Engine, TokenizerBundle

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
        "absolutely agree",
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
        print("ERROR: document is not one of the alternatives", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
