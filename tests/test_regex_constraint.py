"""Standalone regex constraint (ADR-0015).

The reference is an ECMA-262 engine (`regress`, unicode mode, matching
over scalar values like src/pattern.zig); Python `re` is not a reference
(\\d takes U+0661, \\s misses U+FEFF, `.` takes U+2028 and \\r).

- differential: random patterns from the syntax both pattern.zig and the
  ECMA `u` mode accept, random strings; the engine accepts a string (bytes
  fed one by one, then can_end) exactly when ^(?:p)$ matches it, and every
  mask agrees with the accept of each byte;
- generative: random passes over the masks to EOS; every produced text
  matches, and a minimum number of passes must end in a document;
- table: constructs outside the intersection with the result the
  specification gives; unsupported constructs with the exact status and
  byte offset.

Only an empty language (UNSATISFIABLE_CONSTRAINT) is an expected compile
refusal of a generated pattern; any other refusal fails. Under
BLG_TEST_REQUIRE_BACKEND=1 a missing backend or a missing `regress`
fails instead of skipping. BLG_FUZZ_LONG=1 runs more patterns.

The reference runs in a worker process with a memory and time limit
(tests/ecma_reference.py): regress 2025.10.1 does not terminate on some
loops with a nullable body (pattern 86 of the differential seed,
`(\\(?[abc]{0,3})+?|...` on "a\\u0661.", took all the memory of the
machine). A case the reference cannot answer has no
expected result: the engine still runs on it (mask and accept must
agree), it is not counted as checked, it is listed in a warning, and at
most 1% of the compared cases (at least 5) may be such cases.
"""

from __future__ import annotations

import os
import random
import re
import warnings

import pytest

from conftest import backend_required, make_byte_tokenizer, skip_or_fail
from ecma_reference import EcmaReference

try:
    import regress  # noqa: F401  (availability; queries go through tests/ecma_reference.py)
except ImportError:
    if backend_required():
        raise
    pytest.skip("regress (ECMA-262 reference) not installed", allow_module_level=True)

LONG = os.environ.get("BLG_FUZZ_LONG") == "1"
N_PATTERNS = 400 if LONG else 120
N_STRINGS = 40 if LONG else 20
SEED = 20261007

ALPHABET = ["a", "b", "c", "0", "1", "9", " ", ".", "*", "(", "é", "١",
            "﻿", " ", "\r", "\n", "\U0001F600"]
LITERALS = ["a", "b", "c", "0", "1", "9", " ", "é", "\\.", "\\*", "\\(", "\\)"]
CLASSES = ["[abc]", "[a-c]", "[^a]", "[0-9é]", "\\d", "\\w", "\\s", "\\D", ".", "[^]"]
QUANTS = ["*", "+", "?", "{2}", "{1,2}", "{0,3}", "+?"]


def gen_pattern(rng, depth=0):
    parts = []
    for _ in range(rng.randint(1, 3)):
        r = rng.random()
        if r < 0.4:
            atom = rng.choice(LITERALS)
        elif r < 0.75:
            atom = rng.choice(CLASSES)
        elif depth < 2:
            inner = gen_pattern(rng, depth + 1)
            atom = rng.choice(["(?:%s)", "(%s)"]) % inner
        else:
            atom = rng.choice(LITERALS)
        if rng.random() < 0.4:
            atom += rng.choice(QUANTS)
        parts.append(atom)
    body = "".join(parts)
    if rng.random() < 0.25:
        body += "|" + gen_pattern(rng, depth + 1)
    if depth == 0 and rng.random() < 0.1:
        body = "^" + body
    if depth == 0 and rng.random() < 0.1:
        body = body + "$"
    return body


REF = None


@pytest.fixture(scope="module", autouse=True)
def _ecma_reference():
    global REF
    REF = EcmaReference()
    yield REF
    REF.close()


def ecma_full_match(pat, text):
    """True or False by the reference, None when it could not answer."""
    return REF.full_match(pat, text)


def reference_failures_bounded(since, compared):
    """The reference failures after index `since` of REF.failures: listed
    in a warning, at most 1% of the compared cases (at least 5)."""
    failed = REF.failures[since:]
    if failed:
        warnings.warn(f"ECMA reference could not answer {len(failed)} cases: "
                      + "; ".join(f"{p!r} on {t!r}: {r}" for p, t, r in failed[:10]))
    assert len(failed) <= max(5, compared // 100), (
        f"the reference failed on {len(failed)} cases ({compared} compared): {failed[:5]}")


_PKG_STATUS = {"InvalidSchemaError": "INVALID_SCHEMA",
               "UnsupportedFeatureError": "UNSUPPORTED_FEATURE",
               "UnsatisfiableConstraintError": "UNSATISFIABLE_CONSTRAINT"}


def refusal(e):
    """(status name, schema_offset or None) of a compile refusal of either
    backend, or None for any other exception."""
    status = getattr(e, "status", None)
    if isinstance(status, int) and hasattr(e, "schema_offset"):
        import blg_ctypes
        off = e.schema_offset
        return blg_ctypes.STATUS_NAMES.get(status), (None if off == 0xFFFFFFFF else off)
    name = _PKG_STATUS.get(type(e).__name__)
    if name is None:
        return None
    m = re.search(r"schema_offset=(\d+)", str(e))
    return name, (int(m.group(1)) if m else None)


class Rig:
    """Package or ctypes access to a regex constraint on the byte tokenizer."""

    def __init__(self, pat):
        self.spec = make_byte_tokenizer()
        self.eos = self.spec.eos_ids[0]
        kind = os.environ.get("BLG_TEST_BACKEND", "auto")
        self.pkg = None
        if kind in ("package", "auto"):
            try:
                import bolorgir
                from conftest import PackageBackend
            except ImportError:
                if kind == "package":
                    skip_or_fail("bolorgir package is not importable")
            else:
                self.pkg = bolorgir
                self.engine = bolorgir.Engine(mode="lazy")
                try:
                    bundle = PackageBackend._make_tokenizer(bolorgir, self.spec)
                    self.constraint = self.engine.compile_regex(pat, tokenizer=bundle)
                except BaseException:
                    self.engine.close()
                    raise
                return
        try:
            import blg_ctypes
        except ImportError as e:
            skip_or_fail(f"ctypes backend unavailable: {e}")
        self.zg = blg_ctypes
        self.ctx = blg_ctypes.Context(self.spec, blg_ctypes.BLG_MODE_LAZY)
        try:
            self.grammar = blg_ctypes.compile_regex(self.ctx, pat)
        except BaseException:
            self.ctx.destroy()
            raise

    def session(self):
        if self.pkg is not None:
            return PkgSession(self.constraint.create_session())
        return CtSession(self.zg.Session(self.ctx, self.grammar))

    def close(self):
        if self.pkg is not None:
            self.constraint.close()
            self.engine.close()
        else:
            self.zg.grammar_release(self.grammar)
            self.ctx.destroy()


class PkgSession:
    def __init__(self, s):
        self.s = s

    def mask(self):
        return set(self.s.allowed_token_ids())

    def accept(self, t):
        try:
            self.s.accept_token(t)
            return True
        except Exception as e:  # noqa: BLE001
            if type(e).__name__ != "InvalidTokenError":
                raise
            return False

    def can_end(self):
        return self.s.can_end()

    def close(self):
        self.s.close()


class CtSession:
    def __init__(self, s):
        self.s = s

    def mask(self):
        return self.s.fill_mask_ids()

    def accept(self, t):
        st = self.s.accept(t)
        if st not in (0, 6):
            raise AssertionError(f"accept status {st}")
        return st == 0

    def can_end(self):
        return self.s.can_end()

    def close(self):
        self.s.destroy()


def engine_matches(rig, text):
    s = rig.session()
    try:
        for b in text.encode("utf-8"):
            m = s.mask()
            ok = s.accept(b)
            assert ok == (b in m), f"mask and accept disagree on byte {b:#x}"
            if not ok:
                return False
        return s.can_end()
    finally:
        s.close()


def compiles(pat):
    """Whether the reference accepts the pattern (False when it failed)."""
    return REF.compiles(pat) is True


def test_differential_against_ecma_engine():
    rng = random.Random(SEED)
    checked, since = 0, len(REF.failures)
    for _ in range(N_PATTERNS):
        pat = gen_pattern(rng)
        if not compiles(pat):
            continue
        try:
            rig = Rig(pat)
        except Exception as e:  # noqa: BLE001
            if not empty_language(e, pat, rng):
                raise
            continue
        try:
            for _ in range(N_STRINGS):
                text = "".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 6)))
                want = ecma_full_match(pat, text)
                got = engine_matches(rig, text)
                if want is None:
                    continue
                assert got == want, f"pattern {pat!r}, text {text!r}: engine {got}, ECMA {want}"
                checked += 1
        finally:
            rig.close()
    assert checked > N_PATTERNS * N_STRINGS // 2
    reference_failures_bounded(since, checked)


def empty_language(e, pat, rng):
    """True when e is the expected refusal of a generated pattern: an
    empty language (UNSATISFIABLE_CONSTRAINT) that the ECMA engine does not
    contradict on sample strings. Any other refusal is unexpected."""
    r = refusal(e)
    if r is None or r[0] != "UNSATISFIABLE_CONSTRAINT":
        return False
    samples = ["".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, 4)))
               for _ in range(N_STRINGS)]
    hit = [t for t in samples if ecma_full_match(pat, t) is True]
    assert not hit, f"{pat!r} refused as empty, ECMA matches {hit[0]!r}"
    return True


GEN_PASSES = 5
GEN_STEPS = 64


def test_generated_texts_match():
    """Random passes over the masks; a pass that has not reached EOS after
    GEN_STEPS tokens is counted as unfinished (an unbounded language need
    not end), every finished one must match, and at least a quarter of all
    passes (and no fewer than 30) must finish."""
    rng = random.Random(SEED + 1)
    finished = unfinished = refused_empty = verified = 0
    since = len(REF.failures)
    for _ in range(N_PATTERNS // 2):
        pat = gen_pattern(rng)
        if not compiles(pat):
            continue
        try:
            rig = Rig(pat)
        except Exception as e:  # noqa: BLE001
            if not empty_language(e, pat, rng):
                raise
            refused_empty += 1
            continue
        try:
            for _ in range(GEN_PASSES):
                s = rig.session()
                out = bytearray()
                ended = False
                try:
                    for step in range(GEN_STEPS):
                        m = s.mask()
                        assert m, f"{pat!r}: empty mask after {bytes(out)!r}"
                        body = sorted(m - {rig.eos})
                        if rig.eos in m and (not body or step > 8 or rng.random() < 0.3):
                            ended = True
                            break
                        t = rng.choice(body)
                        assert s.accept(t)
                        out.append(t)
                    if ended:
                        assert s.can_end(), (pat, bytes(out))
                finally:
                    s.close()
                if not ended:
                    unfinished += 1
                    continue
                finished += 1
                want = ecma_full_match(pat, out.decode("utf-8"))
                if want is None:
                    continue
                assert want, (pat, bytes(out))
                verified += 1
        finally:
            rig.close()
    total = finished + unfinished
    assert finished >= max(30, total // 4), (
        f"{finished} of {total} passes finished ({unfinished} unfinished, "
        f"{refused_empty} patterns refused as empty)")
    reference_failures_bounded(since, verified)


def test_reference_runaway_is_contained():
    """The construct on which regress 2025.10.1 does not terminate ends the
    worker only (a small memory limit), gives no answer, and the next query
    is answered by a new worker. A fixed regress answers False."""
    ref = EcmaReference(memory_limit=256 << 20, timeout=20.0)
    try:
        got = ref.full_match("(?:a?)+?b", "ac")
        assert got in (None, False)
        assert (got is None) == bool(ref.failures), ref.failures
        assert ref.full_match("a+?b", "aab") is True
        assert ref.compiles("a(") is False
    finally:
        ref.close()


@pytest.mark.parametrize("pat,text,want", [
    ("\\d", "١", False),        # ECMA \d is [0-9]
    ("\\s", "﻿", True),         # U+FEFF is ECMA WhiteSpace
    (".", " ", False),          # line terminators are not `.`
    (".", "\r", False),
    (".", "\u0085", True),           # NEL is not an ECMA line terminator
    ("[^]", "\n", True),
    ("\\w", "é", False),        # \w is ASCII without the i/u case folding
    ("\\u{1F600}", "\U0001F600", True),
    ("\\uD83D\\uDE00", "\U0001F600", True),  # a surrogate pair escape is one scalar
    ("a+?", "aaa", True),            # a lazy quantifier does not change the language
    ("", "", True),
    ("", "a", False),
    ("a{,2}", "a{,2}", True),        # Annex B: `{` without a valid quantifier is a literal
])
def test_constructs_with_specified_results(pat, text, want):
    rig = Rig(pat)
    try:
        assert engine_matches(rig, text) is want
    finally:
        rig.close()


@pytest.mark.parametrize("pat,offset", [
    ("(?=a)a", 0),        # lookahead: the '(' of the group form
    ("a(?<!a)b", 1),      # lookbehind
    ("(a)\\1", 3),        # backreference: the '\\' of the escape
    ("\\p{L}", 0),        # Unicode property class
    ("a\\bb", 1),         # word boundary
    ("(?i)a", 0),         # inline flags
    ("[\\1]", 1),         # backreference form inside a class
    ("\u00e9\\1", 2),     # offsets count UTF-8 bytes
])
def test_unsupported_constructs_are_refused_with_an_offset(pat, offset):
    with pytest.raises(Exception) as ei:
        Rig(pat)
    assert refusal(ei.value) == ("UNSUPPORTED_FEATURE", offset), (
        f"{type(ei.value).__name__}: {ei.value}")


@pytest.mark.parametrize("pat", ["a(", "[z-a]", "a{2,1}"])
def test_malformed_patterns_are_invalid(pat):
    with pytest.raises(Exception) as ei:
        Rig(pat)
    r = refusal(ei.value)
    assert r is not None and r[0] == "INVALID_SCHEMA" and r[1] is not None, (
        f"{type(ei.value).__name__}: {ei.value}")


def test_empty_language_is_unsatisfiable():
    with pytest.raises(Exception) as ei:
        Rig("[]")
    assert refusal(ei.value) is not None and refusal(ei.value)[0] == "UNSATISFIABLE_CONSTRAINT", (
        f"{type(ei.value).__name__}: {ei.value}")
