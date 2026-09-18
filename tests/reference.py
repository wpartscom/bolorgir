"""Independent reference for the MVP canonical-v1 language (TZ T1, DESIGN §9).

Implemented purely from docs/DESIGN.md §1 and the TZ (sections 3, 5).
No shared code with the core (src/*.zig) is allowed here.

Contents:
- compile_schema / compile_schema_text / compile_literals -> Language;
- enumerate_language -> full document set for bounded schemas;
- PrefixOracle (over a document set) and Matcher (incremental, exact for
  unbounded languages) with a common allowed_next/can_end/mask interface;
- a mini-validator of final documents against a schema (validate_instance);
- canonical-v1 serialization (escape table, number normalization).

Documents inside the reference are bytes (UTF-8). String inputs are encoded
as UTF-8; byte prefixes are needed because tokens may split mid-UTF-8.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from decimal import Decimal


# ---------------------------------------------------------------------------
# Reference errors (mirror zg_status from include/zig_constraints.h)
# ---------------------------------------------------------------------------

class ReferenceError(Exception):
    status_name = "INTERNAL"


class InvalidSchema(ReferenceError):
    status_name = "INVALID_SCHEMA"


class UnsupportedFeature(ReferenceError):
    status_name = "UNSUPPORTED_FEATURE"


class UnsatisfiableConstraint(ReferenceError):
    status_name = "UNSATISFIABLE_CONSTRAINT"


class UnsupportedTokenizer(ReferenceError):
    status_name = "UNSUPPORTED_TOKENIZER"


class ResourceLimit(ReferenceError):
    status_name = "RESOURCE_LIMIT"


class EnumerationCapped(ResourceLimit):
    """Enumeration exceeded cap_docs: the document set is incomplete."""


# ---------------------------------------------------------------------------
# canonical-v1 serialization (DESIGN §1.1, §1.6)
# ---------------------------------------------------------------------------

_SHORT_ESCAPES = {
    0x22: b'\\"',
    0x5C: b"\\\\",
    0x08: b"\\b",
    0x0C: b"\\f",
    0x0A: b"\\n",
    0x0D: b"\\r",
    0x09: b"\\t",
}


def escape_string(s: str) -> bytes:
    """String content (without quotes) per the DESIGN §1.1 table."""
    out = bytearray()
    for ch in s:
        cp = ord(ch)
        short = _SHORT_ESCAPES.get(cp)
        if short is not None:
            out += short
        elif cp < 0x20:
            out += b"\\u%04x" % cp
        else:
            out += ch.encode("utf-8")
    return bytes(out)


def canonical_string(s: str) -> bytes:
    return b'"' + escape_string(s) + b'"'


def _to_decimal(value) -> Decimal:
    if isinstance(value, bool):
        raise InvalidSchema("boolean is not a number")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidSchema("non-finite number in schema")
        d = Decimal(str(value))
    else:
        raise InvalidSchema(f"not a number: {value!r}")
    if not d.is_finite():
        raise InvalidSchema("non-finite number in schema")
    return d


class _LexemeDecimal(Decimal):
    """Decimal that remembers the source JSON number lexeme.

    Scientific form of non-integer enum/const numbers is preserved
    (DESIGN §1.6), so normalization needs the schema lexeme, not just the
    value: parse_float in compile_schema_text returns this type.
    """

    __slots__ = ("lexeme",)

    def __new__(cls, lexeme):
        obj = Decimal.__new__(cls, lexeme)
        obj.lexeme = lexeme
        return obj


def _number_lexeme(value) -> str:
    if isinstance(value, bool):
        raise InvalidSchema("boolean is not a number")
    if isinstance(value, _LexemeDecimal):
        return value.lexeme
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidSchema("non-finite number in schema")
        return repr(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise InvalidSchema("non-finite number in schema")
        return str(value)
    raise InvalidSchema(f"not a number: {value!r}")


def normalize_number(value) -> bytes:
    """Exact decimal normalization of an enum/const number (DESIGN §1.6).

    No binary64: works on the lexeme (mantissa+exponent). Integral value ->
    plain integer form; non-integral without exponent -> int.frac with
    leading zeros of the int part stripped (down to one digit) and trailing
    zeros of the frac part stripped; non-integral WITH exponent -> scientific
    form preserved: mantissa as in the schema with the same zero
    normalization (empty frac part is dropped together with the dot),
    exponent normalized ('E'->'e', no '+', no leading zeros).
    Zero in any notation (including -0) -> "0".
    Limit: <= 400 significant digits, |written exponent| <= 400 —
    beyond that -> InvalidSchema.
    """
    lex = _number_lexeme(value)
    i, n = 0, len(lex)
    neg = False
    if i < n and lex[i] == "-":
        neg = True
        i += 1
    s = i
    while i < n and "0" <= lex[i] <= "9":
        i += 1
    int_part = lex[s:i]
    frac_part = ""
    if i < n and lex[i] == ".":
        i += 1
        s = i
        while i < n and "0" <= lex[i] <= "9":
            i += 1
        frac_part = lex[s:i]
    exp_val = 0
    has_exp = False
    if i < n and lex[i] in ("e", "E"):
        has_exp = True
        i += 1
        exp_neg = False
        if i < n and lex[i] in ("+", "-"):
            exp_neg = lex[i] == "-"
            i += 1
        while i < n and "0" <= lex[i] <= "9":
            exp_val = exp_val * 10 + (ord(lex[i]) - 0x30)
            if exp_val > 400:
                raise InvalidSchema("number exponent exceeds |exp| <= 400")
            i += 1
        if exp_neg:
            exp_val = -exp_val
    digits = (int_part + frac_part).lstrip("0")
    if not digits:
        return b"0"     # 0, -0, 0.000, -0e7 -> "0"
    if len(digits) > 400:
        raise InvalidSchema("number has more than 400 significant digits")
    e10 = exp_val - len(frac_part)
    sign = "-" if neg else ""
    if e10 >= 0:
        return (sign + digits + "0" * e10).encode("ascii")
    need = -e10
    tz = len(digits) - len(digits.rstrip("0"))
    if tz >= need:
        return (sign + digits[: len(digits) - need]).encode("ascii")
    int_out = int_part.lstrip("0") or "0"
    frac_out = frac_part.rstrip("0")
    out = int_out
    if not has_exp:
        out += "." + frac_out
    else:
        if frac_out:
            out += "." + frac_out
        out += "e" + str(exp_val)
    return (sign + out).encode("ascii")


# ---------------------------------------------------------------------------
# Reference grammar nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Lit:
    data: bytes


@dataclass(frozen=True)
class Str:
    min_len: int = 0
    max_len: int | None = None


@dataclass(frozen=True)
class Int:
    pass


@dataclass(frozen=True)
class Num:
    pass


@dataclass(frozen=True)
class Choice:
    alts: tuple


@dataclass(frozen=True)
class Repeat:
    item: object
    min: int = 0
    max: int | None = None


@dataclass(frozen=True)
class Obj:
    # props: tuple of (key: str, node, required: bool) in declaration order
    props: tuple


@dataclass(frozen=True)
class Language:
    root: object
    kind: str          # "schema" | "literals"
    source: object     # source schema (dict) or list of strings


# ---------------------------------------------------------------------------
# JSON Schema -> Language compiler (FR-1, DESIGN §1.7)
# ---------------------------------------------------------------------------

SUPPORTED_KEYWORDS = {
    "type", "properties", "required", "additionalProperties", "items",
    "minItems", "maxItems", "minLength", "maxLength", "enum", "const",
    "$defs", "$ref", "$schema",
    "title", "description", "$comment", "examples", "default",
}
ANNOTATIONS = {"title", "description", "$comment", "examples", "default"}
SCHEMA_2020_12 = "https://json-schema.org/draft/2020-12/schema"
TYPES7 = {"object", "array", "string", "integer", "number", "boolean", "null"}
MAX_DEPTH_DEFAULT = 64


def _is_nonneg_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


class _Compiler:
    def __init__(self, document, max_depth: int):
        self.document = document
        self.max_depth = max_depth
        self.ref_stack: list[str] = []

    def compile(self) -> Language:
        root = self.node(self.document, "#", 1)
        return Language(root=root, kind="schema", source=self.document)

    def _resolve_ref(self, ref: str, pointer: str):
        if not isinstance(ref, str) or not ref.startswith("#"):
            raise UnsupportedFeature(f"external $ref {ref!r} at {pointer}")
        if ref == "#":
            return self.document, "#"
        if not ref.startswith("#/"):
            raise UnsupportedFeature(f"unsupported $ref form {ref!r} at {pointer}")
        cur = self.document
        for raw in ref[2:].split("/"):
            seg = raw.replace("~1", "/").replace("~0", "~")
            if not isinstance(cur, dict) or seg not in cur:
                raise InvalidSchema(f"unresolvable $ref {ref!r} at {pointer}")
            cur = cur[seg]
        return cur, ref

    def node(self, sch, pointer: str, depth: int):
        if depth > self.max_depth:
            raise ResourceLimit(f"schema depth > {self.max_depth} at {pointer}")
        if isinstance(sch, bool):
            raise UnsupportedFeature(f"boolean schema at {pointer}")
        if not isinstance(sch, dict):
            raise InvalidSchema(f"schema must be an object at {pointer}")

        for kw in sch:
            if kw not in SUPPORTED_KEYWORDS:
                raise UnsupportedFeature(f"keyword {kw!r} at {pointer}")

        if "$schema" in sch:
            v = sch["$schema"]
            if v not in (SCHEMA_2020_12, SCHEMA_2020_12 + "#"):
                raise UnsupportedFeature(f"$schema dialect {v!r} at {pointer}")

        if "$ref" in sch:
            # alongside $ref only annotations and $defs are allowed
            # (DESIGN §1.7: "only annotations next to $ref" — $defs is allowed
            # as the definition carrier, otherwise a root $ref is inexpressible)
            extra = set(sch) - {"$ref", "$defs"} - ANNOTATIONS
            if extra:
                raise UnsupportedFeature(
                    f"keywords alongside $ref at {pointer}: {sorted(extra)}")
            target, tptr = self._resolve_ref(sch["$ref"], pointer)
            if tptr in self.ref_stack:
                raise InvalidSchema(f"cyclic $ref at {pointer}: {sch['$ref']!r}")
            self.ref_stack.append(tptr)
            try:
                return self.node(target, tptr, depth + 1)
            finally:
                self.ref_stack.pop()

        typ = sch.get("type")
        if typ is not None:
            if not isinstance(typ, str):
                raise UnsupportedFeature(f"type as non-string at {pointer}")
            if typ not in TYPES7:
                raise InvalidSchema(f"unknown type {typ!r} at {pointer}")

        if typ is None and "enum" not in sch and "const" not in sch:
            raise InvalidSchema(f"schema without type at {pointer}")

        node = None
        if typ == "object":
            node = self._object(sch, pointer, depth)
        elif typ == "array":
            node = self._array(sch, pointer, depth)
        elif typ == "string":
            node = Str(self._bound(sch, "minLength", 0, pointer),
                       self._bound(sch, "maxLength", None, pointer))
            self._check_min_max(node.min_len, node.max_len, "Length", pointer)
        elif typ == "integer":
            node = Int()
        elif typ == "number":
            node = Num()
        elif typ == "boolean":
            node = Choice((Lit(b"true"), Lit(b"false")))
        elif typ == "null":
            node = Lit(b"null")

        if "enum" in sch or "const" in sch:
            node = self._enum(sch, typ, pointer)

        if node is None:
            raise InvalidSchema(f"cannot compile schema at {pointer}")
        return node

    def _bound(self, sch, key, default, pointer):
        if key not in sch:
            return default
        v = sch[key]
        if not _is_nonneg_int(v):
            raise InvalidSchema(f"{key} must be a non-negative integer at {pointer}")
        return v

    def _check_min_max(self, mn, mx, what, pointer):
        if mn is not None and mx is not None and mn > mx:
            raise UnsatisfiableConstraint(f"min{what} > max{what} at {pointer}")

    def _object(self, sch, pointer, depth):
        for kw in ("properties", "required", "additionalProperties"):
            if kw not in sch:
                raise InvalidSchema(f"object without {kw} at {pointer}")
        props = sch["properties"]
        if not isinstance(props, dict):
            raise InvalidSchema(f"properties must be an object at {pointer}")
        req = sch["required"]
        if not isinstance(req, list) or any(not isinstance(r, str) for r in req):
            raise InvalidSchema(f"required must be a list of strings at {pointer}")
        if len(set(req)) != len(req):
            raise InvalidSchema(f"duplicate names in required at {pointer}")
        unknown = [r for r in req if r not in props]
        if unknown:
            raise InvalidSchema(f"required names not in properties at {pointer}: {unknown}")
        if sch["additionalProperties"] is not False:
            raise InvalidSchema(f"additionalProperties must be exactly false at {pointer}")
        req_set = set(req)
        compiled = tuple(
            (k, self.node(sub, f"{pointer}/properties/{k}", depth + 1), k in req_set)
            for k, sub in props.items()
        )
        return Obj(compiled)

    def _array(self, sch, pointer, depth):
        if "items" not in sch:
            raise InvalidSchema(f"array without items at {pointer}")
        mn = self._bound(sch, "minItems", 0, pointer)
        mx = self._bound(sch, "maxItems", None, pointer)
        self._check_min_max(mn, mx, "Items", pointer)
        item = self.node(sch["items"], f"{pointer}/items", depth + 1)
        return Repeat(item, mn, mx)

    def _enum(self, sch, typ, pointer):
        if "const" in sch and "enum" in sch:
            raise InvalidSchema(f"const next to enum at {pointer}")
        if "enum" in sch:
            values = sch["enum"]
            if not isinstance(values, list):
                raise InvalidSchema(f"enum must be an array at {pointer}")
        else:
            values = [sch["const"]]

        mn = mx = None
        if typ == "string":
            mn = self._bound(sch, "minLength", 0, pointer)
            mx = self._bound(sch, "maxLength", None, pointer)
            self._check_min_max(mn, mx, "Length", pointer)

        def value_class(v):
            if isinstance(v, bool):
                return "boolean"
            if v is None:
                return "null"
            if isinstance(v, str):
                return "string"
            if isinstance(v, (int, float, Decimal)):
                return "number"
            return None

        def keep(v):
            cls = value_class(v)
            if cls is None:
                raise UnsupportedFeature(f"non-scalar enum value at {pointer}")
            if typ is None:
                return True
            if typ == "string":
                if cls != "string":
                    return False
                return (mn is None or len(v) >= mn) and (mx is None or len(v) <= mx)
            if typ == "integer":
                if cls != "number":
                    return False
                d = _to_decimal(v)
                return d == d.to_integral_value()
            if typ == "number":
                return cls == "number"
            return cls == typ

        if typ is None and values:
            classes = {value_class(v) for v in values}
            if None in classes:
                raise UnsupportedFeature(f"non-scalar enum value at {pointer}")
            if len(classes) > 1:
                raise UnsupportedFeature(f"enum of mixed types at {pointer}")

        seen = set()
        lits = []
        for v in values:
            if not keep(v):
                continue
            data = self._serialize_enum_value(v, pointer)
            if data not in seen:
                seen.add(data)
                lits.append(Lit(data))
        if not lits:
            raise UnsatisfiableConstraint(f"enum fully filtered out at {pointer}")
        return lits[0] if len(lits) == 1 else Choice(tuple(lits))

    def _serialize_enum_value(self, v, pointer) -> bytes:
        if isinstance(v, bool):
            return b"true" if v else b"false"
        if v is None:
            return b"null"
        if isinstance(v, str):
            return canonical_string(v)
        if isinstance(v, (int, float, Decimal)):
            return normalize_number(v)
        raise UnsupportedFeature(f"non-scalar enum value at {pointer}")


def compile_schema(schema: dict, *, max_depth: int = MAX_DEPTH_DEFAULT) -> Language:
    if not isinstance(schema, dict):
        raise InvalidSchema("schema must be a dict (use compile_schema_text for JSON text)")
    return _Compiler(schema, max_depth).compile()


class _DuplicateKeyError(ValueError):
    pass


def _no_dup_pairs(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise _DuplicateKeyError(k)
        obj[k] = v
    return obj


def compile_schema_text(text, *, max_depth: int = MAX_DEPTH_DEFAULT) -> Language:
    """Compile from JSON text: exact number lexemes (parse_float=
    _LexemeDecimal; the lexeme is needed for scientific-form normalization,
    DESIGN §1.6) and duplicate-key detection (DESIGN §1.7 -> InvalidSchema)."""
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    try:
        doc = json.loads(text, parse_float=_LexemeDecimal,
                         object_pairs_hook=_no_dup_pairs)
    except _DuplicateKeyError as e:
        raise InvalidSchema(f"duplicate key in schema JSON: {e}") from None
    except json.JSONDecodeError as e:
        raise InvalidSchema(f"invalid JSON: {e}") from None
    return _Compiler(doc, max_depth).compile()


def compile_literals(strings, *, max_depth: int = MAX_DEPTH_DEFAULT) -> Language:
    """FR-3: non-empty list of (already decoded) strings; exactly one is chosen."""
    if not isinstance(strings, (list, tuple)) or len(strings) == 0:
        raise InvalidSchema("literal set must be a non-empty list of strings")
    seen = set()
    lits = []
    for s in strings:
        if not isinstance(s, str):
            raise InvalidSchema(f"literal set entries must be strings: {s!r}")
        data = s.encode("utf-8")
        if data not in seen:
            seen.add(data)
            lits.append(Lit(data))
    root = lits[0] if len(lits) == 1 else Choice(tuple(lits))
    return Language(root=root, kind="literals", source=list(strings))


# ---------------------------------------------------------------------------
# Document enumeration (for bounded schemas)
# ---------------------------------------------------------------------------

DEFAULT_ALPHABET = ("a", "b", '"', "\\", "\n", "\x01", "é", "\x7f")
NUM_EXTRA_LITERALS = (
    b"0.5", b"-0.5", b"1e2", b"1E2", b"1e+2", b"1e-2", b"0e0", b"-0", b"2.50",
)


def enumerate_language(lang: Language, *, max_string_len: int = 3,
                       max_items_extra: int = 2, max_abs_int: int = 9,
                       string_alphabet=DEFAULT_ALPHABET,
                       cap_docs: int = 200_000) -> set[bytes]:
    """Full enumeration of documents of a bounded schema.

    Unbounded strings/numbers are bounded by parameters:
    strings — length <= max_string_len over string_alphabet, array items —
    at most min(max, min+max_items_extra), integers — |v| <= max_abs_int,
    number — the same integers plus NUM_EXTRA_LITERALS.
    Exceeding cap_docs -> EnumerationCapped (the set would be incomplete).
    """
    budget = [cap_docs]

    def take(n: int):
        budget[0] -= n
        if budget[0] < 0:
            raise EnumerationCapped(f"enumeration exceeded cap_docs={cap_docs}")

    def gen(node) -> list[bytes]:
        if isinstance(node, Lit):
            take(1)
            return [node.data]
        if isinstance(node, Str):
            cap = node.max_len if node.max_len is not None else max_string_len
            cap = min(cap, max_string_len)
            out = []
            for ln in range(node.min_len, cap + 1):
                for combo in _product(string_alphabet, ln):
                    take(1)
                    out.append(canonical_string("".join(combo)))
            return out
        if isinstance(node, Int):
            vals = [str(i).encode("ascii") for i in range(-max_abs_int, max_abs_int + 1)]
            take(len(vals))
            return vals
        if isinstance(node, Num):
            vals = [str(i).encode("ascii") for i in range(-max_abs_int, max_abs_int + 1)]
            vals += [e for e in NUM_EXTRA_LITERALS if e not in vals]
            take(len(vals))
            return vals
        if isinstance(node, Choice):
            out = []
            for alt in node.alts:
                out += gen(alt)
            return out
        if isinstance(node, Repeat):
            cap = node.max if node.max is not None else node.min + max_items_extra
            cap = min(cap, node.min + max_items_extra)
            item_docs = gen(node.item)
            out = []
            for n in range(node.min, cap + 1):
                for combo in _product(item_docs, n):
                    take(1)
                    out.append(b"[" + b",".join(combo) + b"]")
            return out
        if isinstance(node, Obj):
            choices = []  # list of (key, node) chosen in declaration order
            out = []

            def rec(i, chosen):
                if i == len(node.props):
                    take(1)
                    out.append(b"{" + b",".join(
                        b'"' + escape_string(k) + b'":' + d for k, d in chosen) + b"}")
                    return
                key, nd, required = node.props[i]
                if required:
                    for d in gen(nd):
                        rec_with(i, chosen, key, d)
                else:
                    rec(i + 1, chosen)
                    for d in gen(nd):
                        rec_with(i, chosen, key, d)

            def rec_with(i, chosen, key, d):
                rec(i + 1, chosen + [(key, d)])

            rec(0, [])
            return out
        raise TypeError(f"unknown node {node!r}")

    def _product(items, n):
        if n == 0:
            yield ()
            return
        for x in items:
            for rest in _product(items, n - 1):
                yield (x,) + rest

    docs = gen(lang.root)
    return set(docs)


# ---------------------------------------------------------------------------
# Tokenizer (test spec; C ABI conversion lives in zg_ctypes)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenizerSpec:
    # tokens[id] — token bytes; b"" for EOS/special
    tokens: tuple
    eos_ids: tuple = ()
    special_ids: tuple = ()

    @property
    def vocab_size(self) -> int:
        return len(self.tokens)

    @property
    def mask_words(self) -> int:
        return (self.vocab_size + 31) // 32

    def validate(self):
        eos = set(self.eos_ids)
        special = set(self.special_ids)
        if eos & special:
            raise UnsupportedTokenizer("eos ∩ special ≠ ∅")
        for i, t in enumerate(self.tokens):
            if i in eos or i in special:
                continue
            if len(t) == 0:
                raise UnsupportedTokenizer(f"ordinary token {i} with empty bytes")
        return self


def oracle_mask(prefix_valid, can_end: bool, spec: TokenizerSpec) -> set[int]:
    """Allowed token ids: an ordinary token is allowed iff its bytes are
    accepted (prefix_valid(prefix+bytes)); EOS iff can_end; special — never."""
    allowed = set()
    eos = set(spec.eos_ids)
    special = set(spec.special_ids)
    for i, tok in enumerate(spec.tokens):
        if i in eos:
            if can_end:
                allowed.add(i)
        elif i in special:
            continue
        elif prefix_valid(tok):
            allowed.add(i)
    return allowed


# ---------------------------------------------------------------------------
# Oracle over a document set (exact for fully enumerated languages)
# ---------------------------------------------------------------------------

def _to_bytes(x) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, str):
        return x.encode("utf-8")
    if isinstance(x, bytearray):
        return bytes(x)
    raise TypeError(x)


class PrefixOracle:
    """allowed_next(prefix): prefix is a prefix of some document;
    can_end(prefix): prefix ∈ docs; mask(prefix, spec): set of token ids."""

    def __init__(self, docs):
        self.docs = {_to_bytes(d) for d in docs}
        self.prefixes = set()
        for d in self.docs:
            for i in range(len(d) + 1):
                self.prefixes.add(d[:i])

    def allowed_next(self, prefix=b"") -> bool:
        return _to_bytes(prefix) in self.prefixes

    def can_end(self, prefix=b"") -> bool:
        return _to_bytes(prefix) in self.docs

    def mask(self, prefix, spec: TokenizerSpec) -> set[int]:
        p = _to_bytes(prefix)
        return oracle_mask(lambda tok: (p + tok) in self.prefixes,
                           self.can_end(p), spec)


# ---------------------------------------------------------------------------
# Incremental Matcher (exact oracle for unbounded languages too)
# ---------------------------------------------------------------------------
# Independent implementation of DESIGN §1.1–1.5 semantics: threads are frame
# stacks. Frames: LitF / StrF / NumF / RepF / ObjF. No Seq frame: an object
# manages the key/value alternation itself (phases open|key|value|sep).

@dataclass
class LitF:
    node: Lit
    off: int = 0


@dataclass
class StrF:
    node: Str
    mode: str = "open"     # open|normal|esc|u0|u00|uhex1|uhex2
    count: int = 0
    rem: int = 0
    lo: int = 0
    hi: int = 0
    hexv: int = 0


@dataclass
class NumF:
    node: object           # Int | Num
    state: str = "start"
    complete: bool = False


@dataclass
class RepF:
    node: Repeat
    count: int = 0
    phase: str = "open"    # open|body|body_ac|sep (_ac — after comma)


@dataclass
class ObjF:
    node: Obj
    idx: int = 0
    cur: int = 0
    phase: str = "open"    # open|key|key_ac|value|sep (_ac — after comma)


def _make_frame(node):
    if isinstance(node, Lit):
        return LitF(node)
    if isinstance(node, Str):
        return StrF(node)
    if isinstance(node, (Int, Num)):
        return NumF(node)
    if isinstance(node, Repeat):
        return RepF(node)
    if isinstance(node, Obj):
        return ObjF(node)
    raise TypeError(node)


def _copy_frame(f):
    cls = type(f)
    new = cls.__new__(cls)
    new.__dict__.update(f.__dict__)
    return new


def _copy_threads(threads):
    return [[_copy_frame(f) for f in t] for t in threads]


_HEX_LOWER = set(b"0123456789abcdef")
_SHORT_ESCAPABLE = set(b'"\\bfnrt')
# control codes having a short escape (their \u00xx form is forbidden)
_HAS_SHORT = {0x08, 0x09, 0x0A, 0x0C, 0x0D}


def _str_feed(f: StrF, b: int) -> str:
    """-> 'ok' | 'done' | 'err'. done — closing quote consumed."""
    node = f.node

    def char_done():
        f.count += 1
        if node.max_len is not None and f.count > node.max_len:
            return "err"
        return "ok"

    if f.mode != "normal":
        if f.mode == "open":
            if b == 0x22:
                f.mode = "normal"
                return "ok"
            return "err"
        if f.mode == "esc":
            if b in _SHORT_ESCAPABLE:
                f.mode = "normal"
                return char_done()
            if b == 0x75:  # 'u'
                f.mode = "u0"
                return "ok"
            return "err"
        if f.mode == "u0":
            if b == 0x30:
                f.mode = "u00"
                return "ok"
            return "err"
        if f.mode == "u00":
            if b == 0x30:
                f.mode = "uhex1"
                f.hexv = 0
                return "ok"
            return "err"
        if f.mode == "uhex1":
            if b in _HEX_LOWER:
                f.hexv = int(chr(b), 16)
                f.mode = "uhex2"
                return "ok"
            return "err"
        if f.mode == "uhex2":
            if b in _HEX_LOWER:
                f.hexv = f.hexv * 16 + int(chr(b), 16)
                if f.hexv < 0x20 and f.hexv not in _HAS_SHORT:
                    f.mode = "normal"
                    return char_done()
            return "err"
        return "err"

    # normal
    if f.rem > 0:
        if f.lo <= b <= f.hi:
            f.rem -= 1
            if f.rem > 0:
                f.lo, f.hi = 0x80, 0xBF
                return "ok"
            return char_done()
        return "err"
    if b == 0x22:  # closing quote
        if f.count >= node.min_len:
            return "done"
        return "err"
    if b < 0x20:
        return "err"
    # Start of a new character (escape, single byte, lead byte of a multibyte
    # sequence): at count == max finishing the character would exceed
    # maxLength — reject immediately, otherwise the mask would contain
    # dead-end tokens (DESIGN §3.1). Mirrors src/parser.zig strFeed.
    if node.max_len is not None and f.count >= node.max_len:
        return "err"
    if b == 0x5C:
        f.mode = "esc"
        return "ok"
    if b < 0x80:
        return char_done()
    # UTF-8 DFA (DESIGN §1.2)
    if 0xC2 <= b <= 0xDF:
        f.rem, f.lo, f.hi = 1, 0x80, 0xBF
    elif b == 0xE0:
        f.rem, f.lo, f.hi = 2, 0xA0, 0xBF
    elif b in (0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7, 0xE8, 0xE9,
               0xEA, 0xEB, 0xEC, 0xEE, 0xEF):
        f.rem, f.lo, f.hi = 2, 0x80, 0xBF
    elif b == 0xED:
        f.rem, f.lo, f.hi = 2, 0x80, 0x9F
    elif b == 0xF0:
        f.rem, f.lo, f.hi = 3, 0x90, 0xBF
    elif 0xF1 <= b <= 0xF3:
        f.rem, f.lo, f.hi = 3, 0x80, 0xBF
    elif b == 0xF4:
        f.rem, f.lo, f.hi = 3, 0x80, 0x8F
    else:
        return "err"
    return "ok"


def _num_feed(f: NumF, b: int) -> str:
    """-> 'consumed' | 'complete_pop' | 'err'."""
    is_int = isinstance(f.node, Int)
    s = f.state
    digit = 0x30 <= b <= 0x39
    d19 = 0x31 <= b <= 0x39

    def go(state, complete):
        f.state = state
        f.complete = complete
        return "consumed"

    if s == "start":
        if b == 0x2D:
            return go("minus", False)
        if b == 0x30:
            return go("zero", True)
        if d19:
            return go("digits", True)
        return "err"
    if s == "minus":
        if b == 0x30:
            return go("zero", True)
        if d19:
            return go("digits", True)
        return "err"
    if s == "zero":
        if not is_int and b == 0x2E:
            return go("dot", False)
        if not is_int and b in (0x65, 0x45):
            return go("e", False)
        return "complete_pop"   # digit after 0 is not a continuation (leading zeros)
    if s == "digits":
        if digit:
            return "consumed"
        if not is_int and b == 0x2E:
            return go("dot", False)
        if not is_int and b in (0x65, 0x45):
            return go("e", False)
        return "complete_pop"
    if is_int:
        return "err"
    if s == "dot":
        if digit:
            return go("frac", True)
        return "err"
    if s == "frac":
        if digit:
            return "consumed"
        if b in (0x65, 0x45):
            return go("e", False)
        return "complete_pop"
    if s == "e":
        if digit:
            return go("edigits", True)
        if b in (0x2B, 0x2D):
            return go("esign", False)
        return "err"
    if s == "esign":
        if digit:
            return go("edigits", True)
        return "err"
    if s == "edigits":
        if digit:
            return "consumed"
        return "complete_pop"
    return "err"


class Matcher:
    """Incremental prefix oracle for a Language.

    feed(bytes) -> False once the prefix stops being a language prefix
    (irreversible: further feeds always return False).
    can_end() -> True if the current prefix is a complete document.
    By DESIGN §1.9 (no dead ends in canonical-v1) "bytes accepted" ==
    "language prefix", which is what the token oracle relies on.
    """

    def __init__(self, lang: Language, max_threads: int = 64):
        self.lang = lang
        self.max_threads = max_threads
        self.threads: list[list] = self._push_node(lang.root, [])
        self.alive = bool(self.threads)

    def clone(self) -> "Matcher":
        m = Matcher.__new__(Matcher)
        m.lang = self.lang
        m.max_threads = self.max_threads
        m.threads = _copy_threads(self.threads)
        m.alive = self.alive
        return m

    # --- node expansion / cascades ---

    def _push_node(self, node, stack) -> list[list]:
        # stack is a frame list; copies are needed only when branching (choice)
        if isinstance(node, Choice):
            out = []
            for alt in node.alts:
                out += self._push_node(alt, [_copy_frame(f) for f in stack])
            return out
        if isinstance(node, Lit) and len(node.data) == 0:
            return self._after_child(stack)
        return [stack + [_make_frame(node)]]

    def _after_child(self, stack) -> list[list]:
        """The top child frame is already popped; notify the parent."""
        if not stack:
            return [stack]
        top = stack[-1]
        if isinstance(top, ObjF):
            if top.phase in ("key", "key_ac"):
                top.phase = "value"
                val = top.node.props[top.cur][1]
                return self._push_node(val, stack)
            if top.phase == "value":
                top.idx = top.cur + 1
                top.phase = "sep"
                return [stack]
            raise AssertionError("child in wrong object phase")
        if isinstance(top, RepF):
            top.count += 1
            top.phase = "sep"
            return [stack]
        raise AssertionError("frame cannot have children")

    # --- per-byte step ---

    def _step_thread(self, stack, b: int) -> list[list]:
        while True:
            if not stack:
                return []  # content past the end of the document
            f = stack[-1]
            if isinstance(f, LitF):
                if b != f.node.data[f.off]:
                    return []
                f.off += 1
                if f.off == len(f.node.data):
                    stack.pop()
                    return self._after_child(stack)
                return [stack]
            if isinstance(f, StrF):
                r = _str_feed(f, b)
                if r == "err":
                    return []
                if r == "done":
                    stack.pop()
                    return self._after_child(stack)
                return [stack]
            if isinstance(f, NumF):
                r = _num_feed(f, b)
                if r == "consumed":
                    return [stack]
                if r == "complete_pop":
                    stack.pop()
                    out = []
                    for s in self._after_child(stack):
                        out += self._step_thread(s, b)
                    return out
                return []
            if isinstance(f, RepF):
                node = f.node
                if f.phase == "open":
                    if b == 0x5B:  # '['
                        f.phase = "body"
                        return [stack]
                    return []
                if f.phase == "body":
                    if b == 0x5D:  # ']'
                        if f.count >= node.min:
                            stack.pop()
                            return self._after_child(stack)
                        return []
                    if node.max is not None and f.count >= node.max:
                        return []
                    out = []
                    for s in self._push_node(node.item, stack):
                        out += self._step_thread(s, b)
                    return out
                if f.phase == "body_ac":
                    # an item is mandatory after a comma: trailing comma
                    # is always forbidden (DESIGN §1.5)
                    if b == 0x5D:
                        return []
                    out = []
                    for s in self._push_node(node.item, stack):
                        out += self._step_thread(s, b)
                    return out
                # sep
                if b == 0x2C:  # ','
                    if node.max is not None and f.count >= node.max:
                        return []
                    f.phase = "body_ac"
                    return [stack]
                if b == 0x5D:
                    if f.count >= node.min:
                        stack.pop()
                        return self._after_child(stack)
                    return []
                return []
            if isinstance(f, ObjF):
                node = f.node
                n = len(node.props)
                if f.phase == "open":
                    if b == 0x7B:  # '{'
                        f.phase = "key"
                        return [stack]
                    return []
                if f.phase in ("key", "key_ac"):
                    if b == 0x22:  # '"'
                        out = []
                        for i in range(f.idx, n):
                            # candidate i is admissible if props[idx..i-1] are optional
                            if any(node.props[j][2] for j in range(f.idx, i)):
                                break
                            s2 = [_copy_frame(f) for f in stack]
                            o = s2[-1]
                            o.cur = i
                            keylit = Lit(canonical_string(node.props[i][0]) + b":")
                            out.append(s2 + [LitF(keylit, off=1)])
                        return out
                    if b == 0x7D:  # '}'
                        # a key is mandatory after a comma: trailing comma
                        # is always forbidden (DESIGN §1.4)
                        if f.phase == "key_ac":
                            return []
                        if all(not node.props[j][2] for j in range(f.idx, n)):
                            stack.pop()
                            return self._after_child(stack)
                        return []
                    return []
                if f.phase == "sep":
                    if b == 0x2C:
                        # a comma only makes sense if unvisited properties
                        # remain; otherwise it is a guaranteed trailing comma
                        if f.idx >= n:
                            return []
                        f.phase = "key_ac"
                        return [stack]
                    if b == 0x7D:
                        if all(not node.props[j][2] for j in range(f.idx, n)):
                            stack.pop()
                            return self._after_child(stack)
                        return []
                    return []
                # phase "value" must always have a child on top
                raise AssertionError("object value phase without child")
            raise TypeError(f)

    def feed(self, data) -> bool:
        data = _to_bytes(data)
        for b in data:
            new_threads = []
            for t in self.threads:
                new_threads += self._step_thread(t, b)
                if len(new_threads) > self.max_threads:
                    raise ResourceLimit(f"max_threads={self.max_threads} exceeded")
            self.threads = new_threads
            if not self.threads:
                self.alive = False
                return False
        self.alive = bool(self.threads)
        return self.alive

    # --- can_end: virtual completion without bytes (DESIGN §3 canEnd) ---

    def _push_virtual(self, node, stack) -> list[list]:
        if isinstance(node, Choice):
            out = []
            for alt in node.alts:
                out += self._push_virtual(alt, [_copy_frame(f) for f in stack])
            return out
        if isinstance(node, Lit) and len(node.data) == 0:
            return self._ac_virtual(stack)
        return [stack + [_make_frame(node)]]

    def _ac_virtual(self, stack) -> list[list]:
        if not stack:
            return [stack]
        top = stack[-1]
        if isinstance(top, ObjF):
            if top.phase in ("key", "key_ac"):
                top.phase = "value"
                val = top.node.props[top.cur][1]
                return self._push_virtual(val, stack)
            return []  # value->sep and other phases require a byte
        if isinstance(top, RepF):
            return []      # sep requires a byte
        return []

    def _reducible(self, stack) -> bool:
        worklist = [stack]
        while worklist:
            st = worklist.pop()
            if not st:
                return True
            f = st[-1]
            if isinstance(f, NumF) and f.complete:
                worklist += self._ac_virtual([_copy_frame(f) for f in st[:-1]])
            elif isinstance(f, LitF) and f.off == len(f.node.data):
                worklist += self._ac_virtual([_copy_frame(f) for f in st[:-1]])
        return False

    def can_end(self) -> bool:
        if not self.alive:
            return False
        return any(self._reducible(t) for t in self.threads)

    # --- common oracle interface (same as PrefixOracle) ---

    def allowed_next(self, prefix=b"") -> bool:
        m = self.clone()
        return m.feed(prefix)

    def mask(self, prefix, spec: TokenizerSpec) -> set[int]:
        m = self.clone()
        if not m.feed(prefix):
            return set()
        can_end = m.can_end()
        allowed = set()
        eos = set(spec.eos_ids)
        special = set(spec.special_ids)
        for i, tok in enumerate(spec.tokens):
            if i in eos:
                if can_end:
                    allowed.add(i)
            elif i in special:
                continue
            else:
                probe = m.clone()
                if probe.feed(tok):
                    allowed.add(i)
        return allowed


# ---------------------------------------------------------------------------
# Mini-validator of final documents against a schema (enumeration cross-check)
# ---------------------------------------------------------------------------

def _num_of(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        d = _to_decimal(v)
        return d
    return None


def _json_equal(a, b) -> bool:
    na, nb = _num_of(a), _num_of(b)
    if na is not None and nb is not None:
        return na == nb
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return type(a) is type(b) and a == b


def validate_instance(value, schema, root=None) -> bool:
    """Mini-validator for the FR-1 subset: type/properties/required/
    additionalProperties/items/min/max/enum/const/$ref."""
    if root is None:
        root = schema
    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, dict):
        return False
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#"):
            return False
        target = root if ref == "#" else None
        if ref.startswith("#/"):
            target = root
            for raw in ref[2:].split("/"):
                seg = raw.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or seg not in target:
                    return False
                target = target[seg]
        return validate_instance(value, target, root)

    typ = schema.get("type")
    if typ == "object":
        if not isinstance(value, dict):
            return False
        props = schema.get("properties", {})
        req = schema.get("required", [])
        if any(k not in value for k in req):
            return False
        if schema.get("additionalProperties", True) is False:
            if any(k not in props for k in value):
                return False
        for k, sub in props.items():
            if k in value and not validate_instance(value[k], sub, root):
                return False
    elif typ == "array":
        if not isinstance(value, list):
            return False
        if "minItems" in schema and len(value) < schema["minItems"]:
            return False
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return False
        if "items" in schema:
            for item in value:
                if not validate_instance(item, schema["items"], root):
                    return False
    elif typ == "string":
        if not isinstance(value, str):
            return False
        if "minLength" in schema and len(value) < schema["minLength"]:
            return False
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return False
    elif typ == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return False
    elif typ == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
    elif typ == "boolean":
        if not isinstance(value, bool):
            return False
    elif typ == "null":
        if value is not None:
            return False

    if "enum" in schema:
        if not any(_json_equal(value, e) for e in schema["enum"]):
            return False
    if "const" in schema:
        if not _json_equal(value, schema["const"]):
            return False
    return True


def validate_document(doc, lang: Language) -> bool:
    """Document (bytes) -> bool: JSON parse + mini-validator.
    For literal sets: membership in the set."""
    doc = _to_bytes(doc)
    if lang.kind == "literals":
        return doc in {s.encode("utf-8") for s in lang.source}
    try:
        value = json.loads(doc.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return validate_instance(value, lang.source)


def canonical_check(doc, lang: Language) -> bool:
    """Document belongs to the canonical-v1 language (Matcher)."""
    m = Matcher(lang)
    return m.feed(doc) and m.can_end()
