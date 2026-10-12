"""Session rollback and draft-token validation (ADR-0014).

Property checks on random walks over spec-v1 schemas, on the byte-level and
the dictionary tokenizer, in lazy and adaptive mode, through the backend
selected by BLG_TEST_BACKEND (package or ctypes):

- after k accepts and rollback(k) the mask and can_end equal the values
  before the accepts, bit for bit;
- a rollback past the records held is INVALID_ARGUMENT and changes nothing;
- validate_tokens equals consecutive accept_token calls on a separate
  session fed the same prefix, and leaves the session as it was;
- from a state whose mask fails with RESOURCE_LIMIT, rollback(1)
  restores the mask and can_end of the state before.

Under BLG_TEST_REQUIRE_BACKEND=1 an unavailable backend or a library
without the ADR-0014 exports fails instead of skipping.
"""

from __future__ import annotations

import os
import random

import pytest

from conftest import make_byte_tokenizer, make_dict_tokenizer, skip_or_fail

BLG_OK = 0
BLG_ERR_INVALID_ARGUMENT = 1
BLG_ERR_RESOURCE_LIMIT = 8

SCHEMAS = [
    {"type": "object",
     "properties": {"a": {"type": "integer", "minimum": 0, "maximum": 100},
                    "b": {"type": "string", "pattern": "^[a-z]+$"}},
     "required": ["a"], "additionalProperties": {"type": "boolean"}},
    {"type": "array", "items": {"type": "string", "maxLength": 3}, "uniqueItems": True,
     "maxItems": 4},
    {"anyOf": [{"type": "string", "maxLength": 4},
               {"type": "object", "properties": {"x": {"type": "number", "multipleOf": 0.5}}}]},
    {"oneOf": [{"type": "integer", "minimum": 5}, {"type": "integer", "maximum": 3}]},
    {"type": "object", "minProperties": 1, "maxProperties": 2,
     "propertyNames": {"enum": ["k0", "k1", "k2"]}},
]
# The walks above stay inside the resource limits of the engine. With
# uniqueItems over numbers or mixed values, or propertyNames with a
# pattern, a mask fails with RESOURCE_LIMIT on these walks, with and
# without rollback; the limit state itself is
# test_rollback_out_of_a_resource_limit.
LIMIT_SCHEMA = {"type": "array", "items": {"type": "integer"}, "uniqueItems": True,
                "maxItems": 5}
DEPTH = 4
WALKS = 6
MAX_STEPS = 40


class PackageRig:
    def __init__(self, spec, mode, schema):
        import bolorgir as zg
        from conftest import PackageBackend
        self.zg = zg
        self.engine = zg.Engine(mode=mode)
        self.bundle = PackageBackend._make_tokenizer(zg, spec)
        self.constraint = self.engine.compile(schema, tokenizer=self.bundle, profile="spec-v1")

    def session(self, depth):
        return PackageSessionRig(self.zg, self.constraint.create_session(max_rollback_tokens=depth))

    def close(self):
        self.constraint.close()
        self.engine.close()


class PackageSessionRig:
    def __init__(self, zg, s):
        self.zg, self.s = zg, s

    def mask(self):
        return frozenset(self.s.allowed_token_ids())

    def can_end(self):
        return self.s.can_end()

    def struct_segment(self):
        return self.s.struct_segment()

    def _status(self, fn, *args):
        from conftest import _status_code_of_exception
        try:
            out = fn(*args)
        except Exception as e:
            code = _status_code_of_exception(e)
            if code is None:
                raise
            return code, None
        return BLG_OK, out

    def accept(self, t):
        return self._status(self.s.accept_token, t)[0]

    def rollback(self, n):
        return self._status(self.s.rollback, n)[0]

    def validate(self, ids):
        return self._status(self.s.validate_tokens, ids)

    def close(self):
        self.s.close()


class CtypesRig:
    def __init__(self, spec, mode, schema):
        import blg_ctypes
        if not blg_ctypes.has_rollback():
            skip_or_fail("the loaded library predates ADR-0014")
        self.zg = blg_ctypes
        code = {"lazy": blg_ctypes.BLG_MODE_LAZY, "adaptive": blg_ctypes.BLG_MODE_ADAPTIVE}[mode]
        self.ctx = blg_ctypes.Context(spec, code)
        self.grammar = blg_ctypes.compile_schema(self.ctx, schema, profile=b"spec-v1")

    def session(self, depth):
        return CtypesSessionRig(self.zg.Session(self.ctx, self.grammar, max_rollback_tokens=depth))

    def close(self):
        self.zg.grammar_release(self.grammar)
        self.ctx.destroy()


class CtypesSessionRig:
    def __init__(self, s):
        self.s = s

    def mask(self):
        return frozenset(self.s.fill_mask_ids())

    def can_end(self):
        return self.s.can_end()

    def struct_segment(self):
        import blg_ctypes
        if not blg_ctypes.has_struct_segment():
            skip_or_fail("the loaded library predates blg_session_struct_segment")
        return self.s.struct_segment()

    def accept(self, t):
        return self.s.accept(t)

    def rollback(self, n):
        return self.s.rollback(n)

    def validate(self, ids):
        return self.s.validate_tokens(ids)

    def close(self):
        self.s.destroy()


def make_rig(spec, mode, schema):
    kind = os.environ.get("BLG_TEST_BACKEND", "auto")
    if kind in ("package", "auto"):
        try:
            return PackageRig(spec, mode, schema)
        except ImportError:
            if kind == "package":
                skip_or_fail("bolorgir package is not importable")
    try:
        return CtypesRig(spec, mode, schema)
    except ImportError as e:
        skip_or_fail(f"core unavailable: {e}")


def expected_prefix(rig, accepted, draft):
    r = rig.session(0)
    try:
        for t in accepted:
            assert r.accept(t) == BLG_OK
        n = 0
        for t in draft:
            if r.accept(t) != BLG_OK:
                break
            n += 1
        return n
    finally:
        r.close()


def make_draft(rig, accepted, rng, vocab):
    r = rig.session(0)
    try:
        for t in accepted:
            assert r.accept(t) == BLG_OK
        draft = []
        for _ in range(rng.randint(0, 5)):
            m = sorted(r.mask())
            if not m:
                break
            t = rng.choice(m)
            assert r.accept(t) == BLG_OK
            draft.append(t)
        draft.append(rng.randrange(vocab))
        return draft
    finally:
        r.close()


@pytest.mark.parametrize("schema_idx", range(len(SCHEMAS)))
@pytest.mark.parametrize("tok_name", ["byte", "dict"])
@pytest.mark.parametrize("mode", ["lazy", "adaptive"])
def test_rollback_restores_masks_and_validate_matches_accept(schema_idx, tok_name, mode):
    spec = make_byte_tokenizer() if tok_name == "byte" else make_dict_tokenizer()
    eos = set(spec.eos_ids)
    rig = make_rig(spec, mode, SCHEMAS[schema_idx])
    rng = random.Random(1000 * schema_idx + (7 if tok_name == "dict" else 0)
                        + (1 if mode == "adaptive" else 0))
    try:
        for _walk in range(WALKS):
            s = rig.session(DEPTH)
            try:
                accepted = []
                seen = [(s.mask(), s.can_end())]
                held = 0
                for _step in range(MAX_STEPS):
                    m = seen[-1][0]
                    assert m, f"empty mask after {accepted}"
                    body = sorted(m - eos)
                    if not body:
                        break
                    t = rng.choice(body)
                    assert s.accept(t) == BLG_OK
                    accepted.append(t)
                    held = min(DEPTH, held + 1)
                    seen.append((s.mask(), s.can_end()))

                    r = rng.random()
                    if r < 0.3 and held:
                        k = rng.randint(1, held)
                        assert s.rollback(k) == BLG_OK
                        del accepted[-k:]
                        del seen[-k:]
                        held -= k
                        assert (s.mask(), s.can_end()) == seen[-1]
                    elif r < 0.4:
                        before = (s.mask(), s.can_end())
                        assert s.rollback(held + 1) == BLG_ERR_INVALID_ARGUMENT
                        assert (s.mask(), s.can_end()) == before
                    elif r < 0.6:
                        draft = make_draft(rig, accepted, rng, len(spec.tokens))
                        status, prefix = s.validate(draft)
                        assert status == BLG_OK
                        assert prefix == expected_prefix(rig, accepted, draft), draft
                        assert (s.mask(), s.can_end()) == seen[-1]
            finally:
                s.close()
    finally:
        rig.close()


def _mask_status(s):
    """(BLG_OK, mask) or (status, None) when the mask fails, on either
    backend (ctypes CoreFailure.status, package exception class)."""
    from conftest import _status_code_of_exception
    try:
        return BLG_OK, s.mask()
    except Exception as e:  # noqa: BLE001
        code = _status_code_of_exception(e)
        if code is None:
            raise
        return code, None


@pytest.mark.parametrize("tok_name", ["byte", "dict"])
def test_rollback_out_of_a_resource_limit(tok_name):
    """The walk over LIMIT_SCHEMA with the seed of its random walk reaches
    a state whose mask fails with RESOURCE_LIMIT (byte tokenizer after 26
    tokens, dict after 17). rollback(1) from there restores the mask and
    can_end of the state before, bit for bit, and the same token leads to
    the same limit again."""
    spec = make_byte_tokenizer() if tok_name == "byte" else make_dict_tokenizer()
    eos = set(spec.eos_ids)
    rig = make_rig(spec, "lazy", LIMIT_SCHEMA)
    rng = random.Random(1000 + (7 if tok_name == "dict" else 0))
    try:
        s = rig.session(DEPTH)
        try:
            status, m = _mask_status(s)
            assert status == BLG_OK
            prev = (m, s.can_end())
            for _step in range(MAX_STEPS):
                body = sorted(prev[0] - eos)
                assert body, "the walk ended before reaching the limit"
                t = rng.choice(body)
                assert s.accept(t) == BLG_OK
                status, m = _mask_status(s)
                if status == BLG_OK:
                    prev = (m, s.can_end())
                    continue
                assert status == BLG_ERR_RESOURCE_LIMIT, status
                assert s.rollback(1) == BLG_OK
                assert _mask_status(s) == (BLG_OK, prev[0])
                assert s.can_end() == prev[1]
                assert s.accept(t) == BLG_OK
                assert _mask_status(s)[0] == BLG_ERR_RESOURCE_LIMIT
                return
            pytest.fail(f"no RESOURCE_LIMIT within {MAX_STEPS} tokens")
        finally:
            s.close()
    finally:
        rig.close()


def test_rollback_needs_a_depth_and_an_active_session():
    spec = make_byte_tokenizer()
    rig = make_rig(spec, "lazy", {"type": "integer", "minimum": 0, "maximum": 9})
    try:
        s = rig.session(0)
        try:
            assert s.accept(ord("5")) == BLG_OK
            assert s.rollback(1) == BLG_ERR_INVALID_ARGUMENT
            assert s.rollback(0) == BLG_OK
        finally:
            s.close()
        s = rig.session(2)
        try:
            assert s.accept(ord("5")) == BLG_OK
            eos = spec.eos_ids[0]
            assert s.accept(eos) == BLG_OK
            assert s.rollback(1) == BLG_OK
            assert s.can_end()
            assert s.rollback(1) == BLG_OK
            assert not s.can_end()
            assert s.validate([ord("7"), eos]) == (BLG_OK, 2)
            assert s.validate([ord("7"), ord("7")]) == (BLG_OK, 1)
        finally:
            s.close()
    finally:
        rig.close()


# ---------------------------------------------------------------------------
# BLG_CONSTRAINT_STRUCTURAL_TAG sessions through both backends (ADR-0016)
# ---------------------------------------------------------------------------

TAG_SPEC = {
    "type": "structural_tag",
    "format": {"type": "tags_with_separator", "separator": "", "at_least_one": True,
               "tags": [{"type": "tag", "begin": "<c>",
                         "content": {"type": "json_schema",
                                     "json_schema": {"type": "integer"}},
                         "end": "</c>"}]},
}


class PackageStructTagRig:
    def __init__(self, spec, mode, tag_spec):
        import bolorgir as zg
        from conftest import PackageBackend
        self.zg = zg
        self.engine = zg.Engine(mode=mode)
        self.bundle = PackageBackend._make_tokenizer(zg, spec)
        self.constraint = self.engine.compile_structural_tag(tag_spec, tokenizer=self.bundle)

    def session(self, depth):
        return PackageSessionRig(
            self.zg, self.constraint.create_session(max_rollback_tokens=depth))

    def close(self):
        self.constraint.close()
        self.engine.close()


class CtypesStructTagRig:
    def __init__(self, spec, mode, tag_spec):
        import blg_ctypes
        self.zg = blg_ctypes
        code = {"lazy": blg_ctypes.BLG_MODE_LAZY,
                "adaptive": blg_ctypes.BLG_MODE_ADAPTIVE}[mode]
        self.ctx = blg_ctypes.Context(spec, code)
        self.grammar = blg_ctypes.compile_structural_tag(self.ctx, tag_spec)

    def session(self, depth):
        return CtypesSessionRig(
            self.zg.Session(self.ctx, self.grammar, max_rollback_tokens=depth))

    def close(self):
        self.zg.grammar_release(self.grammar)
        self.ctx.destroy()


def make_tag_rig(spec, mode, tag_spec):
    kind = os.environ.get("BLG_TEST_BACKEND", "auto")
    if kind in ("package", "auto"):
        try:
            return PackageStructTagRig(spec, mode, tag_spec)
        except ImportError:
            if kind == "package":
                skip_or_fail("bolorgir package is not importable")
    try:
        return CtypesStructTagRig(spec, mode, tag_spec)
    except ImportError as e:
        skip_or_fail(f"core unavailable: {e}")


@pytest.mark.parametrize("tok_name", ["byte", "dict"])
@pytest.mark.parametrize("mode", ["lazy", "adaptive"])
def test_structural_tag_session_ops(tok_name, mode):
    """Masks, accepts, rollback and draft validation of a structural-tag
    session through the selected backend: no end before the tag, the
    content masks the arguments language, the end literal closes the tag,
    and a refused draft leaves the session bit-identical."""
    spec = make_byte_tokenizer() if tok_name == "byte" else make_dict_tokenizer()
    rig = make_tag_rig(spec, mode, TAG_SPEC)
    try:
        s = rig.session(8)
        try:
            assert not s.can_end()
            assert set(s.mask()) == {ord("<")}
            for t in b"<c>":
                assert s.accept(t) == BLG_OK
            assert not s.can_end()
            m = s.mask()
            assert ord("4") in m and ord("<") not in m
            # Feed and roll back: mask and can_end are restored.
            assert s.accept(ord("4")) == BLG_OK
            assert s.rollback(1) == BLG_OK
            assert s.mask() == m and not s.can_end()
            # Draft validation matches consecutive accepts and preserves state.
            assert s.validate([ord("4"), ord("<"), ord("/"), ord("c"), ord(">")]) == (BLG_OK, 5)
            assert s.validate([ord("4"), ord("x")]) == (BLG_OK, 1)
            assert s.mask() == m
            # The tag completes and the language can now end.
            for t in b"4</c>":
                assert s.accept(t) == BLG_OK
            assert s.can_end()
            # Roll the whole session back to the start.
            assert s.rollback(8) == BLG_OK
            assert set(s.mask()) == {ord("<")}
            assert not s.can_end()
        finally:
            s.close()
    finally:
        rig.close()


TAG_RANGE_SPEC = {
    "type": "structural_tag",
    "format": {"type": "tags_with_separator", "separator": "", "at_least_one": True,
               "tags": [{"type": "tag", "begin": "<c>",
                         "content": {"type": "json_schema",
                                     "json_schema": {"type": "integer",
                                                     "minimum": 10, "maximum": 42}},
                         "end": "</c>"}]},
}

TAG_OPTIONAL_SPEC = {
    "type": "structural_tag",
    "format": {"type": "tags_with_separator", "separator": "", "at_least_one": False,
               "tags": [{"type": "tag", "begin": "<c>",
                         "content": {"type": "json_schema",
                                     "json_schema": {"type": "integer"}},
                         "end": "</c>"}]},
}

TRIGGERED_TWO_TAG_SPEC = {
    "type": "structural_tag",
    "format": {"type": "triggered_tags", "triggers": ["<t>"],
               "tags": [{"type": "tag", "begin": "<t>aaa",
                         "content": {"type": "json_schema",
                                     "json_schema": {"type": "integer"}},
                         "end": "</t>"},
                        {"type": "tag", "begin": "<t>bbb",
                         "content": {"type": "json_schema",
                                     "json_schema": {"type": "integer"}},
                         "end": "</t>"}]},
}


def _spec_of(tok_name):
    return make_byte_tokenizer() if tok_name == "byte" else make_dict_tokenizer()


@pytest.mark.parametrize("tok_name", ["byte", "dict"])
@pytest.mark.parametrize("mode", ["lazy", "adaptive"])
def test_structural_tag_partial_begin_blocks_eos(tok_name, mode):
    """A started (partial) begin of a new tag is an unfinished tag: the
    mask withholds EOS, accept refuses it, the draft check cuts there, and
    rollback re-opens the boundary exactly."""
    spec = _spec_of(tok_name)
    eos = spec.eos_ids[0]
    rig = make_tag_rig(spec, mode, TAG_SPEC)
    try:
        s = rig.session(8)
        try:
            for t in b"<c>4</c>":
                assert s.accept(t) == BLG_OK
            assert s.can_end()
            m_boundary = s.mask()
            assert eos in m_boundary and ord("<") in m_boundary
            # The second tag starts: any partial begin byte blocks the end.
            assert s.accept(ord("<")) == BLG_OK
            assert not s.can_end()
            m_partial = s.mask()
            assert eos not in m_partial and ord("c") in m_partial
            assert s.accept(eos) != BLG_OK
            assert s.mask() == m_partial  # refused terminal, state intact
            assert s.validate([ord("c"), eos]) == (BLG_OK, 1)
            assert s.mask() == m_partial
            # Rollback of the partial begin re-opens the boundary.
            assert s.rollback(1) == BLG_OK
            assert s.can_end() and s.mask() == m_boundary
        finally:
            s.close()
    finally:
        rig.close()


@pytest.mark.parametrize("tok_name", ["byte", "dict"])
@pytest.mark.parametrize("mode", ["lazy", "adaptive"])
def test_structural_tag_optional_first_begin_blocks_eos(tok_name, mode):
    """The same rule for the first tag when tags are optional: the empty
    document may end, a partial begin of the first tag may not."""
    spec = _spec_of(tok_name)
    eos = spec.eos_ids[0]
    rig = make_tag_rig(spec, mode, TAG_OPTIONAL_SPEC)
    try:
        s = rig.session(8)
        try:
            assert s.can_end()
            assert eos in s.mask() and ord("<") in s.mask()
            assert s.accept(ord("<")) == BLG_OK
            assert not s.can_end()
            assert eos not in s.mask()
            assert s.accept(eos) != BLG_OK
            assert s.validate([ord("c"), eos]) == (BLG_OK, 1)
            assert s.rollback(1) == BLG_OK
            assert s.can_end() and eos in s.mask()
            # The tag still completes, and a second partial begin blocks.
            for t in b"<c>4</c>":
                assert s.accept(t) == BLG_OK
            assert s.can_end()
            assert s.accept(ord("<")) == BLG_OK
            assert not s.can_end() and eos not in s.mask()
        finally:
            s.close()
    finally:
        rig.close()


@pytest.mark.parametrize("tok_name", ["byte", "dict"])
@pytest.mark.parametrize("mode", ["lazy", "adaptive"])
def test_structural_tag_content_dead_end_prefixes_are_rejected(tok_name, mode):
    """The nested content applies the same completion filter as the
    standalone JSON constraint: under minimum 10 and maximum 42, '-' and
    '9' can never complete, so the mask withholds them, accept refuses
    them with the state intact, and the draft check cuts at them."""
    spec = _spec_of(tok_name)
    rig = make_tag_rig(spec, mode, TAG_RANGE_SPEC)
    try:
        s = rig.session(8)
        try:
            for t in b"<c>":
                assert s.accept(t) == BLG_OK
            m = s.mask()
            assert ord("4") in m and ord("0") in m
            assert ord("-") not in m and ord("9") not in m and ord("5") not in m
            assert s.accept(ord("-")) != BLG_OK
            assert s.mask() == m
            assert s.validate([ord("-"), ord("4")]) == (BLG_OK, 0)
            assert s.mask() == m
            assert s.accept(ord("4")) == BLG_OK
            m4 = s.mask()
            assert ord("2") in m4 and ord("3") not in m4
            assert s.validate([ord("2"), ord("3")]) == (BLG_OK, 1)
            for t in b"2</c>":
                assert s.accept(t) == BLG_OK
            assert s.can_end()
        finally:
            s.close()
    finally:
        rig.close()


@pytest.mark.parametrize("tok_name", ["byte", "dict"])
@pytest.mark.parametrize("mode", ["lazy", "adaptive"])
def test_structural_tag_active_segment_reports_the_variant(tok_name, mode):
    """The segment query follows the state machine: begin literal, JSON
    content (variant 0 for the single tag), then the tag boundary again."""
    spec = _spec_of(tok_name)
    rig = make_tag_rig(spec, mode, TAG_SPEC)
    try:
        s = rig.session(0)
        try:
            assert s.struct_segment() == (1, 0)
            for t in b"<c>":
                assert s.accept(t) == BLG_OK
            assert s.struct_segment() == (2, 0)
            for t in b"4</c>":
                assert s.accept(t) == BLG_OK
            assert s.struct_segment() == (1, 0)
        finally:
            s.close()
    finally:
        rig.close()


@pytest.mark.parametrize("tok_name", ["byte", "dict"])
@pytest.mark.parametrize("mode", ["lazy", "adaptive"])
def test_structural_tag_ambiguous_begin_prefix(tok_name, mode):
    """A begin prefix shared by several variants reports no single variant
    (0xFF); the first distinguishing byte pins the active one (the trace
    must not attribute the shared framing to one tool)."""
    spec = _spec_of(tok_name)
    rig = make_tag_rig(spec, mode, TRIGGERED_TWO_TAG_SPEC)
    try:
        s = rig.session(0)
        try:
            assert s.struct_segment() == (0, 0xFF)  # free text
            for t in b"<t>":
                assert s.accept(t) == BLG_OK
            assert s.struct_segment() == (1, 0xFF)  # both variants alive
            assert s.accept(ord("b")) == BLG_OK
            assert s.struct_segment() == (1, 1)  # only <t>bbb remains
            for t in b"bb":
                assert s.accept(t) == BLG_OK
            assert s.struct_segment() == (2, 1)
        finally:
            s.close()
    finally:
        rig.close()
