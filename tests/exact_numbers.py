"""Exact JSON numbers for reference checks.

`Num` keeps a JSON number as a canonical decimal (sign, significant digits,
exponent), so comparisons, integrality and multipleOf are exact even for
spellings such as 1e400000 or 7e-900 that `float` turns into inf or 0.
Python `json` parses them through `parse_float=Num` (see `loads`).
"""

from __future__ import annotations

import contextlib
import json
import re
from fractions import Fraction

_NUM_RE = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?\Z")


class Num:
    """Canonical decimal: value = (-1)^neg * int(digits) * 10^exp.

    Zero is digits == "" (sign dropped). digits has no leading or
    trailing zeros.
    """

    __slots__ = ("neg", "digits", "exp")

    def __init__(self, text):
        if isinstance(text, Num):
            self.neg, self.digits, self.exp = text.neg, text.digits, text.exp
            return
        if isinstance(text, bool):
            raise TypeError("bool is not a number")
        if isinstance(text, int):
            text = str(text)
        elif isinstance(text, Fraction):
            text = _fraction_text(text)
        elif isinstance(text, float):
            text = repr(text)
        if not isinstance(text, str) or not _NUM_RE.match(text):
            raise ValueError(f"not a JSON number: {text!r}")
        neg = text.startswith("-")
        body = text[1:] if neg else text
        mant, _, e = body.replace("E", "e").partition("e")
        ip, _, fp = mant.partition(".")
        exp = (int(e) if e else 0) - len(fp)
        digits = (ip + fp).lstrip("0")
        stripped = digits.rstrip("0")
        exp += len(digits) - len(stripped)
        self.digits = stripped
        self.exp = exp if stripped else 0
        self.neg = neg and bool(stripped)

    @classmethod
    def make(cls, neg: bool, digits: str, exp: int) -> "Num":
        n = cls.__new__(cls)
        d = digits.lstrip("0")
        s = d.rstrip("0")
        n.digits = s
        n.exp = (exp + len(d) - len(s)) if s else 0
        n.neg = bool(neg and s)
        return n

    # -- predicates -------------------------------------------------------
    def is_zero(self) -> bool:
        return not self.digits

    def om(self) -> int:
        """Order of magnitude of the leading digit (zero: undefined)."""
        return self.exp + len(self.digits) - 1

    def is_integer(self) -> bool:
        return self.is_zero() or self.exp >= 0

    def is_multiple_of(self, d: "Num") -> bool:
        """Exact divisibility by a positive decimal d."""
        d = _num(d)
        if d.neg or d.is_zero():
            raise ValueError("divisor must be positive")
        if self.is_zero():
            return True
        a = int(d.digits)
        s = self.exp - d.exp
        if s < 0:
            return False
        return (int(self.digits) % a) * pow(10, s, a) % a == 0

    def to_fraction(self, max_exp: int = 100000) -> Fraction:
        if abs(self.exp) > max_exp:
            raise OverflowError("exponent too large for a Fraction")
        v = Fraction(int(self.digits or "0")) * Fraction(10) ** self.exp
        return -v if self.neg else v

    # -- ordering -----------------------------------------------------------
    def _cmp(self, other) -> int:
        b = _num(other)
        a = self
        if a.is_zero() and b.is_zero():
            return 0
        if a.is_zero():
            return 1 if b.neg else -1
        if b.is_zero():
            return -1 if a.neg else 1
        if a.neg != b.neg:
            return -1 if a.neg else 1
        mag = (a.om() > b.om()) - (a.om() < b.om())
        if mag == 0:
            n = max(len(a.digits), len(b.digits))
            da, db = a.digits.ljust(n, "0"), b.digits.ljust(n, "0")
            mag = (da > db) - (da < db)
        return -mag if a.neg else mag

    def __eq__(self, other):
        if not _comparable(other):
            return NotImplemented
        return self._cmp(other) == 0

    def __lt__(self, other):
        if not _comparable(other):
            return NotImplemented
        return self._cmp(other) < 0

    def __le__(self, other):
        if not _comparable(other):
            return NotImplemented
        return self._cmp(other) <= 0

    def __gt__(self, other):
        if not _comparable(other):
            return NotImplemented
        return self._cmp(other) > 0

    def __ge__(self, other):
        if not _comparable(other):
            return NotImplemented
        return self._cmp(other) >= 0

    def __hash__(self):
        if self.is_integer() and self.exp < 64:
            return hash(int(str(self)) if self.exp >= 0 else 0)
        return hash((self.neg, self.digits, self.exp))

    def __neg__(self):
        return Num.make(not self.neg, self.digits, self.exp)

    def __abs__(self):
        return Num.make(False, self.digits, self.exp)

    def __str__(self):
        if self.is_zero():
            return "0"
        s = "-" if self.neg else ""
        if 0 <= self.exp <= 64:
            return s + self.digits + "0" * self.exp
        return f"{s}{self.digits}e{self.exp}"

    def __repr__(self):
        return f"Num({str(self)!r})"


def _comparable(x) -> bool:
    return not isinstance(x, bool) and isinstance(x, (Num, int, float, Fraction))


def _num(x) -> Num:
    return x if isinstance(x, Num) else Num(x)


def _fraction_text(f: Fraction) -> str:
    den = f.denominator
    k = 0
    while den % 10 == 0:
        den //= 10
        k += 1
    twos = fives = 0
    while den % 2 == 0:
        den //= 2
        twos += 1
    while den % 5 == 0:
        den //= 5
        fives += 1
    if den != 1:
        raise ValueError(f"{f} is not a finite decimal")
    k += max(twos, fives)
    num = f.numerator * (10 ** k) // f.denominator
    return f"{num}e-{k}" if k else str(num)


def loads(text):
    """json.loads with every non-integer literal as an exact Num."""
    return json.loads(text, parse_float=Num)


def parse_number(text) -> Num:
    """A complete JSON number lexeme (bytes or str) as an exact Num."""
    if isinstance(text, (bytes, bytearray)):
        text = text.decode("ascii")
    return Num(text)


_EXACT_CLASSES = {}


def _exact_class(stock, value_integers=False):
    """The exact-number variant of a jsonschema validator class (cached;
    `_exact_stock` names the class it extends)."""
    key = (stock, value_integers)
    cls = _EXACT_CLASSES.get(key)
    if cls is not None:
        return cls
    from jsonschema import Draft3Validator, Draft4Validator, ValidationError, validators

    lexical_integer = stock in (Draft3Validator, Draft4Validator) and not value_integers

    def is_number(_checker, inst):
        return isinstance(inst, Num) or (isinstance(inst, int) and not isinstance(inst, bool))

    def is_integer(_checker, inst):
        if isinstance(inst, bool):
            return False
        if lexical_integer:
            return isinstance(inst, int)
        return isinstance(inst, int) or (isinstance(inst, Num) and inst.is_integer())

    def multiple_of(validator, d, instance, _schema):
        if not validator.is_type(instance, "number"):
            return
        if not Num(instance).is_multiple_of(Num(d)):
            yield ValidationError(f"{instance!r} is not a multiple of {d!r}")

    checker = stock.TYPE_CHECKER.redefine_many({"number": is_number, "integer": is_integer})
    cls = validators.extend(stock, validators={"multipleOf": multiple_of}, type_checker=checker)
    cls._exact_stock = stock
    _EXACT_CLASSES[key] = cls
    return cls


@contextlib.contextmanager
def exact_dialects(value_integers=False):
    """Within the block a subschema that declares its own `$schema` is
    validated by the exact variant of that dialect's validator (jsonschema
    otherwise switches to the stock class there and loses exact numbers)."""
    from jsonschema import validators

    stock_validator_for = validators.validator_for

    def validator_for(schema, *args, **kwargs):
        cls = stock_validator_for(schema, *args, **kwargs)
        return _exact_class(getattr(cls, "_exact_stock", cls), value_integers)

    validators.validator_for = validator_for
    try:
        yield
    finally:
        validators.validator_for = stock_validator_for


def exact_validator(schema, cls=None, value_integers=False):
    """A jsonschema validator over exact numbers.

    The schema and the documents are read with `loads` (non-integer
    literals become `Num`); "number", "integer" and multipleOf are decided
    exactly, ordering keywords use `Num` comparisons. "integer" follows
    the dialect: draft-03/04 accept only an integer literal (no fraction
    or exponent part), later drafts any integral value; value_integers
    applies the later rule to every draft (triage of draft-04 cases).
    `cls` defaults to Draft202012Validator. Validate inside
    `exact_dialects` when subschemas may declare their own `$schema`.
    """
    from jsonschema import Draft202012Validator

    if isinstance(schema, (str, bytes, bytearray)):
        schema = loads(schema)
    else:
        schema = loads(json.dumps(schema))
    return _exact_class(cls or Draft202012Validator, value_integers)(schema)


def exact_is_valid(schema, doc) -> bool:
    """Whether the JSON text `doc` (str or bytes) is valid per `schema`."""
    if isinstance(doc, (bytes, bytearray)):
        doc = doc.decode("utf-8")
    with exact_dialects():
        return exact_validator(schema).is_valid(loads(doc))
