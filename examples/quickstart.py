import json
import random

from bolorgir import Engine, TokenizerBundle

# A byte-level vocabulary instead of a model's tokenizer: ids 0..255 are
# single bytes, id 256 is EOS. Nothing is downloaded.
tokens = [bytes([b]) for b in range(256)] + [b"<eos>"]
EOS = 256
bundle = TokenizerBundle.from_token_bytes(tokens, eos_ids=[EOS])

schema = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell"]},
        "amount": {"type": "integer"},
    },
    "required": ["action", "amount"],
    "additionalProperties": False,
}

rng = random.Random(7)  # stands in for the model: picks among allowed tokens
out = bytearray()
with Engine(tokenizer=bundle) as engine:
    with engine.compile(schema) as constraint:  # profile "canonical-v1" (default)
        with constraint.create_session() as session:
            while True:
                allowed = session.allowed_token_ids()  # tokens that keep the JSON valid
                if EOS in allowed and (len(allowed) == 1 or rng.random() < 0.5):
                    session.accept_token(EOS)
                    session.finish()
                    break
                token = rng.choice([t for t in allowed if t != EOS])
                session.accept_token(token)
                out += tokens[token]

doc = json.loads(out)
assert doc["action"] in ("buy", "sell") and isinstance(doc["amount"], int)
print(doc)
