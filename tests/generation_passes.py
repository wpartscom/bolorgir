"""Generation passes over the engine mask, classified into the outcome
records of semantic_outcomes.

Shared by benchmarks/bench_jsb_coverage.py (K-pass generatable metric)
and tests/fuzz/test_fuzz_generate_spec_v1.py. A pass starts a fresh
session, picks tokens from the mask until it takes EOS, finishes the
session and validates the document with the reference validator:
`jsonschema` (validator chosen by `$schema`) over exact numbers
(tests/exact_numbers.py) with ECMA-262 `pattern` semantics (the `regress`
engine in unicode mode, matching src/pattern.zig over code points).
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import re
import time

import numpy as np

import blg_ctypes as blg
import semantic_outcomes as so
from exact_numbers import Num, exact_dialects, exact_validator

CLOSING_BYTES = (b'"', b"}", b"]")


# ---------------------------------------------------------------------------
# Reference validator
# ---------------------------------------------------------------------------

class _EcmaRe:
    """Stand-in for the `re` module inside jsonschema: `search` runs the
    ECMA-262 engine; every other attribute is the real module."""

    def __init__(self):
        self._cache = {}

    def search(self, pattern, string, flags=0):
        return self._compile(pattern).find(string)

    def _compile(self, pattern):
        import regress

        r = self._cache.get(pattern)
        if r is None:
            try:
                r = regress.Regex(pattern, "u")
            except regress.RegressError:
                r = regress.Regex(pattern)
            self._cache[pattern] = r
        return r

    def __getattr__(self, name):
        return getattr(re, name)


_ECMA_RE = _EcmaRe()


@contextlib.contextmanager
def ecma_patterns():
    """Within the block jsonschema evaluates pattern, patternProperties and
    the pattern part of additionalProperties with ECMA-262 semantics."""
    from jsonschema import _keywords, _legacy_keywords, _utils

    mods = (_keywords, _legacy_keywords, _utils)
    saved = [m.re for m in mods]
    for m in mods:
        m.re = _ECMA_RE
    try:
        yield
    finally:
        for m, s in zip(mods, saved):
            m.re = s


def _reject_constant(name):
    raise ValueError(f"{name} is not JSON")


def parse_exact(text):
    """Strict JSON text -> value with non-integer numbers as Num."""
    return json.loads(text, parse_float=Num, parse_constant=_reject_constant)


class ReferenceValidator:
    """validate(doc_bytes) -> (json_ok, verdict, error): verdict True/False,
    or None when the validator raised (error: the exception type name)."""

    def __init__(self, schema_text):
        from jsonschema import validators

        if isinstance(schema_text, (bytes, bytearray)):
            schema_text = schema_text.decode("utf-8")
        plain = json.loads(schema_text)
        cls = validators.validator_for(plain)
        cls.check_schema(plain)
        self.cls = cls
        self.validator = exact_validator(schema_text, cls) if isinstance(plain, dict) else cls(plain)

    def validate(self, doc):
        try:
            value = parse_exact(doc.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return False, None, None
        try:
            with ecma_patterns(), exact_dialects():
                return True, bool(self.validator.is_valid(value)), None
        except Exception as e:  # noqa: BLE001 - no verdict, reported as validator_error
            return True, None, type(e).__name__


def make_reference_validator(schema_text):
    """-> (ReferenceValidator, None) or (None, reason) when the schema is
    unusable for the validator (check_schema/validator_for failed)."""
    try:
        return ReferenceValidator(schema_text), None
    except Exception as e:  # noqa: BLE001 - recorded as a skip reason
        return None, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Documented reference deviations (class D of the semantic triage)
# ---------------------------------------------------------------------------

def is_draft04(schema):
    """Some (sub)schema declares draft-04."""
    stack = [schema]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "draft-04" in str(node.get("$schema", "")):
                return True
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return False


_VERDICT_PAIRS = {}


def reference_verdicts(schema_text, doc_text):
    """-> (reference verdict, verdict of the same validator with integral
    values counted as integers); (None, None) when doc_text is not JSON."""
    from jsonschema import validators

    try:
        value = parse_exact(doc_text)
    except ValueError:
        return None, None
    pair = _VERDICT_PAIRS.get(schema_text)
    if pair is None:
        cls = validators.validator_for(json.loads(schema_text))
        pair = (exact_validator(schema_text, cls),
                exact_validator(schema_text, cls, value_integers=True))
        if len(_VERDICT_PAIRS) >= 64:
            _VERDICT_PAIRS.clear()
        _VERDICT_PAIRS[schema_text] = pair
    with ecma_patterns(), exact_dialects():
        strict = pair[0].is_valid(value)
    with ecma_patterns(), exact_dialects(value_integers=True):
        lenient = pair[1].is_valid(value)
    return bool(strict), bool(lenient)


def draft04_integer_spelling(schema_text, doc_text):
    """docs/supported_features.md section 9: the schema declares draft-04,
    the reference rejects the document, and the same validator accepts it
    once integral values (`1.0`, `1e2`) count as integers."""
    schema = json.loads(schema_text)
    if not isinstance(schema, dict) or not is_draft04(schema):
        return False
    strict, lenient = reference_verdicts(schema_text, doc_text)
    return strict is False and lenient is True


GENERATION_DEVIATIONS = {"draft04_integer_spelling": draft04_integer_spelling}


def is_known_deviation(kind, schema_text, document):
    """The rejected document (bytes or str) is explained by the documented
    deviation `kind` of tests/fuzz/known_deviations_spec_v1.json."""
    check = GENERATION_DEVIATIONS.get(kind)
    if check is None:
        return False
    if isinstance(document, (bytes, bytearray)):
        try:
            document = document.decode("utf-8")
        except UnicodeDecodeError:
            return False
    return check(schema_text, document)


# ---------------------------------------------------------------------------
# Mask access
# ---------------------------------------------------------------------------

class Vocab:
    """Per-tokenizer arrays: mask decoding, the closing-token bias and
    token lookup by spelling (hint segmentation)."""

    def __init__(self, spec):
        self.spec = spec
        self.size = spec.vocab_size
        self.words = spec.mask_words
        self.eos = np.zeros(self.size, dtype=bool)
        self.eos[list(spec.eos_ids)] = True
        special = set(spec.eos_ids) | set(spec.special_ids)
        self.closing = np.array(
            [bool(t) and t.endswith(CLOSING_BYTES) for t in spec.tokens], dtype=bool)
        self.closing &= ~self.eos
        self.by_bytes = {}
        for i, t in enumerate(spec.tokens):
            if t and i not in special:
                self.by_bytes.setdefault(t, i)
        self.max_token_len = max((len(t) for t in self.by_bytes), default=1)

    def allowed(self, buf):
        bits = np.unpackbits(buf.view(np.uint8), bitorder="little")[: self.size]
        return bits.astype(bool)

    def next_hint_token(self, rest, allowed):
        """Longest allowed token that spells a prefix of `rest`, or None."""
        for n in range(min(len(rest), self.max_token_len), 0, -1):
            t = self.by_bytes.get(bytes(rest[:n]))
            if t is not None and allowed[t]:
                return t
        return None


def schema_hints(schema, cap=256):
    """Spellings a biased pass may steer to: `"name":` for every declared
    or required property name and the JSON spelling of every string in an
    enum or const. Plain JSON escaping, as the compact language spells
    them."""
    out = []
    seen = set()

    def add(b):
        if b not in seen and len(out) < cap:
            seen.add(b)
            out.append(b)

    def spell(s):
        return json.dumps(s, ensure_ascii=False).encode("utf-8")

    stack = [schema]
    visited = 0
    while stack and visited < 20000:
        node = stack.pop()
        visited += 1
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                for k in props:
                    add(spell(k) + b":")
            req = node.get("required")
            if isinstance(req, list):
                for k in req:
                    if isinstance(k, str):
                        add(spell(k) + b":")
            for key in ("enum", "const"):
                vals = node.get(key)
                for v in (vals if key == "enum" and isinstance(vals, list) else [vals]):
                    if isinstance(v, str):
                        add(spell(v))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def fill_mask(session, buf):
    err = blg.new_error()
    return blg.LIB.blg_fill_mask(
        session.handle, buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint32)),
        buf.size, ctypes.byref(err))


def status_name(status):
    return "BLG_ERR_" + blg.STATUS_NAMES.get(status, f"STATUS_{status}") \
        if status != blg.BLG_OK else "BLG_OK"


# ---------------------------------------------------------------------------
# One pass
# ---------------------------------------------------------------------------

def run_pass(ctx, grammar, vocab, rng, *, seed, validator, policy="biased",
             hints=(), max_steps=2048, max_seconds=10.0, eos_after=24,
             probe_invalid=0.0, cancelled=None):
    """One generation pass -> (record, document bytes).

    policy "uniform": a token uniformly from the mask, EOS taken with
    probability 0.2 when allowed and always after `eos_after` steps.
    policy "biased": as uniform, plus (a) with probability 0.3 per step a
    random hint (schema_hints) starts and is spelled with the longest
    allowed tokens while the mask admits it; (b) after `eos_after` steps a
    closing token (ending with '"', '}' or ']') is taken with probability
    rising to 0.9. probe_invalid: per-step probability of offering a token
    outside the mask, which must be refused with INVALID_TOKEN and leave
    the mask unchanged. cancelled(): True when the caller's watchdog
    cancelled the current engine call, which turns CANCELLED into
    incomplete:time_limit.
    """
    doc = bytearray()
    hint = None

    def rec(outcome, stage=None, code=None, step=None):
        return so.make_record(outcome, stage, code, step, len(doc), seed)

    def failed(stage, status, step):
        code = status_name(status)
        if status == blg.BLG_ERR_CANCELLED and cancelled is not None and cancelled():
            return rec("incomplete:time_limit", stage, "TIME_LIMIT", step)
        return rec(so.classify_engine_failure(stage, code), stage, code, step)

    try:
        session = blg.Session(ctx, grammar)
    except blg.CoreFailure as e:
        return failed("session_create", e.status, 0), b""
    buf = np.zeros(vocab.words, dtype=np.uint32)
    t0 = time.monotonic()
    try:
        for step in range(max_steps):
            if max_seconds and time.monotonic() - t0 > max_seconds:
                return rec("incomplete:time_limit", "fill_mask", "TIME_LIMIT", step), bytes(doc)
            status = fill_mask(session, buf)
            if status != blg.BLG_OK:
                return failed("fill_mask", status, step), bytes(doc)
            allowed = vocab.allowed(buf)
            eos_ok = bool((allowed & vocab.eos).any())
            body = np.flatnonzero(allowed & ~vocab.eos)
            if eos_ok and (body.size == 0 or step >= eos_after or rng.random() < 0.2):
                status = session.finish()
                if status != blg.BLG_OK:
                    return failed("finish", status, step), bytes(doc)
                json_ok, verdict, error = validator.validate(bytes(doc))
                outcome = so.validation_outcome(json_ok, verdict)
                code = None if outcome == "completed_valid" else (
                    "NOT_JSON" if not json_ok else "REJECTED" if verdict is False else error)
                return rec(outcome, "validate", code, step), bytes(doc)
            if body.size == 0:
                return rec("engine_defect", "fill_mask", "BLG_ERR_DEAD_END", step), bytes(doc)
            if probe_invalid and rng.random() < probe_invalid:
                outside = np.flatnonzero(~allowed)
                if outside.size:
                    t = int(outside[rng.randrange(outside.size)])
                    before = buf.copy()
                    if session.accept(t) != blg.BLG_ERR_INVALID_TOKEN:
                        return rec("engine_defect", "accept", "OUT_OF_MASK_ACCEPTED", step), bytes(doc)
                    status = fill_mask(session, buf)
                    if status != blg.BLG_OK or not np.array_equal(before, buf):
                        return rec("engine_defect", "accept", "STATE_CHANGED_BY_REFUSAL", step), bytes(doc)
            t = None
            if policy == "biased":
                if hint is None and hints and rng.random() < 0.3:
                    hint = hints[rng.randrange(len(hints))]
                if hint is not None:
                    t = vocab.next_hint_token(hint, allowed)
                    hint = hint[len(vocab.spec.tokens[t]):] if t is not None else None
                    if not hint:
                        hint = None
            if t is None:
                pick = body
                if policy == "biased" and step >= eos_after:
                    closing = np.flatnonzero(allowed & vocab.closing)
                    p = min(0.9, 0.3 + 0.01 * (step - eos_after))
                    if closing.size and rng.random() < p:
                        pick = closing
                t = int(pick[rng.randrange(pick.size)])
            status = session.accept(t)
            if status != blg.BLG_OK:
                return failed("accept", status, step), bytes(doc)
            doc += vocab.spec.tokens[t]
        return rec("incomplete:step_limit", "fill_mask", "STEP_LIMIT", max_steps), bytes(doc)
    finally:
        session.destroy()
