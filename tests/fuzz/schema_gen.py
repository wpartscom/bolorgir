"""T4 fuzz: random schema generator (JSON text, to preserve number lexemes).

Supported subset - docs/supported_features.md §1:
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


# ---------------------------------------------------------------------------
# spec-v1 generator
# ---------------------------------------------------------------------------

# Patterns of the src/pattern.zig subset; validated with ECMA-262 semantics
# by tests/generation_passes.py.
_PATTERNS = ["^[a-c]+$", "^x{1,3}$", "ab", "^[0-9]{2}$", "^(foo|bar)$", "^\\d+$",
             "^[^\"\\\\]{0,4}$", "z$", "^.?$", "^\\w{2,}$"]
_NUM_CONSTS = ["0", "1", "2", "5", "9", "10", "-1", "-5", "0.5", "1.5", "2.5", "100",
               "-0.5", "0.25", "3"]
_DIVISORS = ["1", "2", "3", "5", "0.5", "0.25", "0.1", "2.5", "7"]


class SchemaGenV1:
    """Random spec-v1 schemas (JSON text) over the implemented keywords:
    combinators, pattern, numeric bounds and their conjunctions, $ref,
    unevaluated*, open objects, tuples, contains, structural enum/const."""

    def __init__(self, rng: random.Random, max_depth: int = 4):
        self.rng = rng
        self.max_depth = max_depth

    def node(self, depth: int = 0) -> str:
        rng = self.rng
        if depth == 0 and rng.random() < 0.15:
            ndefs = rng.randint(1, 2)
            defs = ",".join(f'"d{i}":{self.node(depth + 2)}' for i in range(ndefs))
            return '{"$defs":{%s},"$ref":"#/$defs/d%d"}' % (defs, rng.randrange(ndefs))
        if depth >= self.max_depth:
            return self.scalar()
        w = rng.random()
        if w < 0.18:
            return self.obj(depth)
        if w < 0.30:
            return self.arr(depth)
        if w < 0.45:
            return self.combinator(depth)
        if w < 0.65:
            return self.number()
        if w < 0.80:
            return self.string()
        return self.scalar()

    def number(self) -> str:
        rng = self.rng
        parts = ['"type":"%s"' % rng.choice(["integer", "number", "number"])]
        lo, hi = sorted([rng.choice(_NUM_CONSTS), rng.choice(_NUM_CONSTS)], key=float)
        if rng.random() < 0.7:
            parts.append('"%s":%s' % (rng.choice(["minimum", "minimum", "exclusiveMinimum"]), lo))
        if rng.random() < 0.7:
            parts.append('"%s":%s' % (rng.choice(["maximum", "maximum", "exclusiveMaximum"]), hi))
        if rng.random() < 0.4:
            parts.append('"multipleOf":%s' % rng.choice(_DIVISORS))
        if rng.random() < 0.25:
            vals = [rng.choice(_NUM_CONSTS) for _ in range(rng.randint(1, 2))]
            parts.append('"not":{"enum":[%s]}' % ",".join(vals))
        return "{%s}" % ",".join(parts)

    def string(self) -> str:
        rng = self.rng
        parts = ['"type":"string"']
        if rng.random() < 0.5:
            parts.append('"pattern":%s' % json.dumps(rng.choice(_PATTERNS)))
        if rng.random() < 0.4:
            mn = rng.randint(0, 2)
            parts.append('"minLength":%d,"maxLength":%d' % (mn, mn + rng.randint(1, 5)))
        if rng.random() < 0.15:
            parts.append('"enum":[%s]' % ",".join(_json_string(rng) for _ in range(rng.randint(1, 3))))
        return "{%s}" % ",".join(parts)

    def scalar(self) -> str:
        rng = self.rng
        kind = rng.choice(_SCALAR_KINDS)
        if rng.random() < 0.3:
            return '{"const":%s}' % _scalar(rng, kind)
        if rng.random() < 0.3:
            return '{"enum":[%s]}' % ",".join(_scalar(rng, rng.choice(_SCALAR_KINDS)) for _ in range(rng.randint(1, 3)))
        return '{"type":"%s"}' % kind

    def obj(self, depth: int, n: int | None = None) -> str:
        rng = self.rng
        if n is None:
            n = rng.randint(0, 3)
        # Declared order varies, so sibling branches over the same names
        # disagree on it (value verdicts must ignore declared order).
        keys = [f"k{i}" for i in range(n)]
        if n >= 2 and rng.random() < 0.4:
            rng.shuffle(keys)
        props = ",".join(f'"{k}":{self.node(depth + 1)}' for k in keys)
        req = ",".join(f'"{k}"' for k in keys if rng.random() < 0.5)
        parts = ['"type":"object"', '"properties":{%s}' % props, '"required":[%s]' % req]
        r = rng.random()
        if r < 0.35:
            parts.append('"additionalProperties":false')
        elif r < 0.5:
            parts.append('"additionalProperties":%s' % self.scalar())
        elif r < 0.6:
            parts.append('"patternProperties":{"^x":%s}' % self.scalar())
        elif r < 0.7:
            parts.append('"unevaluatedProperties":false')
        if rng.random() < 0.15:
            parts.append('"propertyNames":{"maxLength":3}')
        if rng.random() < 0.15:
            parts.append('"maxProperties":%d' % (n + rng.randint(0, 2)))
        if n >= 2 and rng.random() < 0.2:
            parts.append('"dependentRequired":{"k0":["k1"]}')
        return "{%s}" % ",".join(parts)

    def arr(self, depth: int) -> str:
        rng = self.rng
        mn = rng.randint(0, 2)
        parts = ['"type":"array"', '"minItems":%d' % mn, '"maxItems":%d' % (mn + rng.randint(0, 3))]
        r = rng.random()
        if r < 0.5:
            parts.append('"items":%s' % self.node(depth + 1))
        elif r < 0.75:
            parts.append('"prefixItems":[%s]' % ",".join(self.node(depth + 1) for _ in range(rng.randint(1, 2))))
            parts.append('"items":%s' % rng.choice(["false", self.scalar()]))
        else:
            parts.append('"contains":%s' % self.scalar())
            if rng.random() < 0.5:
                parts.append('"unevaluatedItems":%s' % self.scalar())
        if rng.random() < 0.2:
            parts.append('"uniqueItems":true')
        return "{%s}" % ",".join(parts)

    def combinator(self, depth: int) -> str:
        rng = self.rng
        k = rng.choice(["anyOf", "oneOf", "allOf", "not", "if", "oneOfObj"])
        if k == "oneOfObj":
            # Same names and value schemas in opposite declared orders: a
            # value with both keys matches both branches, whatever order
            # its keys arrive in.
            v0, v1 = self.scalar(), self.scalar()
            a = '{"type":"object","properties":{"k0":%s,"k1":%s},"required":["k0","k1"]}' % (v0, v1)
            b = '{"type":"object","properties":{"k1":%s,"k0":%s},"required":["k1"]}' % (v1, v0)
            return '{"oneOf":[%s,%s]}' % (a, b)
        if k == "not":
            return '{"not":%s}' % rng.choice(['{"type":"string"}', '{"const":1}',
                                             '{"enum":[null,true]}', '{"type":["object","array"]}'])
        if k == "if":
            return '{"if":{"type":"string"},"then":%s,"else":%s}' % (self.string(), self.number())
        n = rng.randint(2, 3)
        return '{"%s":[%s]}' % (k, ",".join(self.node(depth + 1) for _ in range(n)))


def gen_schema_v1(rng: random.Random) -> str:
    return SchemaGenV1(rng).node(0)
