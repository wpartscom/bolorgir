"""T4 fuzz: random schema generator (JSON text, to preserve number lexemes).

Supported subset — docs/supported_features.md §1:
object/properties/required/additionalProperties:false, array/items/min/maxItems,
string min/maxLength, integer, number, boolean, null, enum/const of scalars,
$defs/$ref. must_compile=True avoids knowingly rejected forms
(unsat, mixed enum without type, etc.).
"""

from __future__ import annotations

import json
import random

_STRING_PIECES = ["a", "b", "x", "0", "1", '"', "\\", "\n", "é", " ", "/", "~"]
_INT_LEXEMES = ["0", "1", "-1", "42", "-7", "100", "-0",
                "9223372036854775807", "-9223372036854775808"]
_NUM_LEXEMES = ["0.5", "-0.5", "1e2", "1E+2", "1e-2", "2.50", "-0.0",
                "3.14159", "0e0", "10", "-3"]
_SCALAR_KINDS = ["string", "integer", "number", "boolean", "null"]


def _json_string(rng: random.Random, max_len: int = 4) -> str:
    n = rng.randint(0, max_len)
    s = "".join(rng.choice(_STRING_PIECES) for _ in range(n))
    return json.dumps(s, ensure_ascii=False)


def _scalar(rng: random.Random, kind: str) -> str:
    if kind == "string":
        return _json_string(rng)
    if kind == "integer":
        return rng.choice(_INT_LEXEMES)
    if kind == "number":
        return rng.choice(_NUM_LEXEMES)
    if kind == "boolean":
        return rng.choice(["true", "false"])
    return "null"


class SchemaGen:
    def __init__(self, rng: random.Random, must_compile: bool, max_depth: int = 5):
        self.rng = rng
        self.must_compile = must_compile
        self.max_depth = max_depth

    def node(self, depth: int = 0) -> str:
        rng = self.rng
        if depth == 0 and rng.random() < 0.12:
            ndefs = rng.randint(1, 2)
            defs = ",".join(f'"d{i}":{self.node(depth + 2)}' for i in range(ndefs))
            return '{"$defs":{%s},"$ref":"#/$defs/d%d"}' % (defs, rng.randrange(ndefs))
        w = rng.random()
        if depth >= self.max_depth:
            w = 0.35 + rng.random() * 0.65  # scalars/enum only
        if w < 0.20:
            nprops = rng.randint(0, 4)
            props = ",".join(f'"k{i}":{self.node(depth + 1)}' for i in range(nprops))
            required = ",".join(f'"k{i}"' for i in range(nprops) if rng.random() < 0.5)
            return ('{"type":"object","properties":{%s},"required":[%s],'
                    '"additionalProperties":false}' % (props, required))
        if w < 0.34:
            items = self.node(depth + 1)
            mn = rng.randint(0, 2)
            out = '{"type":"array","items":%s,"minItems":%d' % (items, mn)
            if self.must_compile or rng.random() < 0.9:
                out += ',"maxItems":%d' % (mn + rng.randint(0, 3))
            elif rng.random() < 0.5:
                out += ',"maxItems":%d' % rng.randint(0, 1)  # may be < min
            return out + "}"
        if w < 0.52:
            out = '{"type":"string"'
            if rng.random() < 0.5:
                mn = rng.randint(0, 2)
                out += ',"minLength":%d' % mn
                if self.must_compile or rng.random() < 0.5:
                    out += ',"maxLength":%d' % (mn + rng.randint(0, 3))
                else:
                    out += ',"maxLength":%d' % rng.randint(0, 1)
            return out + "}"
        if w < 0.62:
            return '{"type":"integer"}'
        if w < 0.72:
            return '{"type":"number"}'
        if w < 0.78:
            return '{"type":"boolean"}'
        if w < 0.82:
            return '{"type":"null"}'
        # enum / const
        kind = rng.choice(_SCALAR_KINDS)
        if self.must_compile:
            with_type = rng.random() < 0.5
        else:
            with_type = rng.random() < 0.6
        parts = []
        if with_type:
            parts.append('"type":"%s"' % kind)
        if rng.random() < 0.3:
            parts.append('"const":%s' % _scalar(rng, kind))
        else:
            vals = [_scalar(rng, kind) for _ in range(rng.randint(1, 4))]
            parts.append('"enum":[%s]' % ",".join(vals))
        if rng.random() < 0.1:
            parts.append('"title":"t"')
        return "{%s}" % ",".join(parts)


def gen_schema(rng: random.Random, must_compile: bool = True) -> str:
    return SchemaGen(rng, must_compile).node(0)


def gen_literals(rng: random.Random) -> str:
    vals = [_json_string(rng, 5) for _ in range(rng.randint(1, 6))]
    return "[%s]" % ",".join(vals)
