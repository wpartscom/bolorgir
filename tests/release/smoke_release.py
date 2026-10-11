#!/usr/bin/env python3
"""Release smoke test of an installed bolorgir package.

Runs against the installed package only (no repository imports), so it can
run in a clean container after `pip install bolorgir==<version>`:

- every schema of the T1 tables compiles with profile spec-v1 and random
  passes over the masks reach EOS without RESOURCE_LIMIT; each document
  is checked against the schema;
- the T1 prefix checks: after the listed prefix the mask is non-empty;
- a schema with `anyOf` and `pattern` compiles and generates valid
  documents.

The walk mirrors the `biased` policy of tests/generation_passes.py: past
step 24 a pass prefers tokens that close a JSON string, array or object,
so an unbounded string, array or object (schemas without `type`, such as
{"minimum": 1, "maximum": 9}, accept them) cannot keep it from reaching
EOS.

The vocabulary is the byte-level one (256 single-byte tokens and EOS) plus
the multi-digit token `123` and the JSON pieces of the T1 acceptance
criteria. Documents are checked exactly (no float rounding) against the
keywords these schemas use; the patterns are ASCII, where Python `re`
and ECMA-262 agree. Exit status 0 on success.
"""

import json
import random
import re
import sys
from fractions import Fraction

from bolorgir import Engine, ResourceLimitError, TokenizerBundle

PASSES = 40
MAX_STEPS = 256
SEED = 20261007

T1_SCHEMAS = [
    {"type": "number", "minimum": 1, "maximum": 9},
    {"minimum": 1, "maximum": 9},
    {"type": "number", "maximum": 9},
    {"type": "integer", "enum": [1, 2, 3]},
    {"type": "integer", "minimum": 1, "maximum": 9},
    {"type": "integer", "maximum": 9},
    {"type": "number", "multipleOf": 1, "minimum": 1, "maximum": 9},
    {"type": "integer", "minimum": 0, "maximum": 1},
    {"type": "integer", "minimum": -9},
    {"type": "number", "multipleOf": 0.5, "maximum": 2},
    {"type": "integer", "not": {"const": 5}, "minimum": 1, "maximum": 9},
    {"type": "integer", "minimum": 1},
    {"type": "integer", "exclusiveMinimum": 0},
]

T1_PREFIXES = [
    ({"type": "integer", "minimum": -9}, b"-5"),
    ({"type": "number", "multipleOf": 0.5, "maximum": 2}, b"1"),
    ({"type": "integer", "not": {"const": 5}, "minimum": 1, "maximum": 9}, b""),
    ({"type": "integer", "minimum": -9}, b"-"),
    ({"type": "integer", "minimum": -9}, b"5"),
    ({"type": "integer", "minimum": 1}, b"5"),
    ({"type": "integer", "minimum": 1}, b"0"),
    ({"type": "integer", "minimum": 1}, b"0."),
    ({"type": "integer", "exclusiveMinimum": 0}, b""),
    ({"type": "integer", "maximum": 9}, b"-"),
]

ANYOF_PATTERN = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "pattern": "^[a-z]{2}[0-9]{2}$"},
        "value": {"anyOf": [
            {"type": "integer", "minimum": 0, "maximum": 100},
            {"type": "string", "pattern": "^v[0-9]+$"},
        ]},
    },
    "required": ["id", "value"],
    "additionalProperties": False,
}

EXTRA_TOKENS = [b"123", b'{"', b'":', b'",', b'"}']
# A pass past step 24 prefers tokens whose spelling closes a JSON string,
# array or object (the `biased` policy of tests/generation_passes.py).
CLOSING_BYTES = (b'"', b"}", b"]")


def make_bundle():
    tokens = [bytes([i]) for i in range(256)] + EXTRA_TOKENS + [b"<eos>"]
    eos = len(tokens) - 1
    return TokenizerBundle.from_token_bytes(tokens, eos_ids=[eos]), tokens, eos


class Dec:
    """Exact JSON number: (-1)^neg * int(digits) * 10^exp, no float rounding
    (spellings such as 1e400 stay finite)."""

    def __init__(self, text):
        neg = text.startswith("-")
        mant, _, e = text.lstrip("-").lower().partition("e")
        ip, _, fp = mant.partition(".")
        digits = (ip + fp).lstrip("0")
        stripped = digits.rstrip("0")
        self.exp = (int(e) if e else 0) - len(fp) + len(digits) - len(stripped)
        self.digits = stripped
        self.neg = neg and bool(stripped)

    def is_integer(self):
        return not self.digits or self.exp >= 0

    def is_half_multiple(self):
        return self.is_integer() or (self.exp == -1 and self.digits.endswith("5"))

    def cmp(self, bound):
        """Sign of self - bound for an integer bound with |bound| < 1000."""
        if not self.digits:
            return (0 > bound) - (0 < bound)
        sign = -1 if self.neg else 1
        om = self.exp + len(self.digits) - 1
        if om >= 3:
            return sign
        if om < -3:
            return sign if bound == 0 else (1 if bound < 0 else -1)
        value = Fraction(int(self.digits)) * Fraction(10) ** self.exp * sign
        return (value > bound) - (value < bound)


def parse(doc):
    return json.loads(doc, parse_float=Dec, parse_int=Dec)


def check_number(schema, v):
    if not isinstance(v, Dec):
        return schema.get("type") not in ("integer", "number") and "enum" not in schema
    if "enum" in schema and not any(v.cmp(x) == 0 for x in schema["enum"]):
        return False
    if "not" in schema and v.cmp(schema["not"]["const"]) == 0:
        return False
    if schema.get("type") == "integer" and not v.is_integer():
        return False
    if "minimum" in schema and v.cmp(schema["minimum"]) < 0:
        return False
    if "maximum" in schema and v.cmp(schema["maximum"]) > 0:
        return False
    if "exclusiveMinimum" in schema and v.cmp(schema["exclusiveMinimum"]) <= 0:
        return False
    m = schema.get("multipleOf")
    if m == 1 and not v.is_integer():
        return False
    if m == 0.5 and not v.is_half_multiple():
        return False
    return True


def check_document(schema, value):
    if schema.get("type") != "object":
        return check_number(schema, value)
    if not isinstance(value, dict):
        return False
    if set(value) - set(schema["properties"]) or set(schema["required"]) - set(value):
        return False
    if not isinstance(value["id"], str) or not re.fullmatch(r"[a-z]{2}[0-9]{2}", value["id"]):
        return False
    v = value["value"]
    if isinstance(v, str):
        return re.fullmatch(r"v[0-9]+", v) is not None
    return check_number(schema["properties"]["value"]["anyOf"][0], v)


def run_passes(engine, schema, tokens, eos, rng):
    with engine.compile(schema, profile="spec-v1") as constraint:
        for p in range(PASSES):
            doc = b""
            with constraint.create_session() as session:
                for step in range(MAX_STEPS):
                    allowed = session.allowed_token_ids()
                    if not allowed:
                        raise AssertionError(f"{schema}: empty mask after {doc!r}")
                    body = [t for t in allowed if t != eos]
                    if eos in allowed and (not body or step > 24 or rng.random() < 0.3):
                        session.finish()
                        break
                    pick = body
                    if step > 24:
                        closing = [t for t in body if tokens[t].endswith(CLOSING_BYTES)]
                        if closing and rng.random() < min(0.9, 0.3 + 0.01 * (step - 24)):
                            pick = closing
                    t = rng.choice(pick)
                    session.accept_token(t)
                    doc += tokens[t]
                else:
                    raise AssertionError(f"{schema}: no EOS within {MAX_STEPS} steps")
            if not check_document(schema, parse(doc)):
                raise AssertionError(f"{schema}: pass {p} produced an invalid document {doc!r}")


def run_prefix(engine, schema, prefix):
    with engine.compile(schema, profile="spec-v1") as constraint:
        with constraint.create_session() as session:
            for b in prefix:
                session.accept_token(b)
            if not session.allowed_token_ids():
                raise AssertionError(f"{schema}: empty mask after {prefix!r}")


def main():
    bundle, tokens, eos = make_bundle()
    rng = random.Random(SEED)
    failures = []
    with Engine(mode="lazy", tokenizer=bundle) as engine:
        for schema in T1_SCHEMAS + [ANYOF_PATTERN]:
            try:
                run_passes(engine, schema, tokens, eos, rng)
            except (AssertionError, ResourceLimitError) as e:
                failures.append(f"{json.dumps(schema)}: {type(e).__name__}: {e}")
        for schema, prefix in T1_PREFIXES:
            try:
                run_prefix(engine, schema, prefix)
            except (AssertionError, ResourceLimitError) as e:
                failures.append(f"{json.dumps(schema)} prefix {prefix!r}: {type(e).__name__}: {e}")
    import bolorgir
    print(f"bolorgir {bolorgir.__version__}, Python {sys.version.split()[0]}: "
          f"{len(T1_SCHEMAS) + 1} schemas x {PASSES} passes, {len(T1_PREFIXES)} prefixes")
    for f in failures:
        print("FAIL", f)
    if failures:
        return 1
    print("release smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
