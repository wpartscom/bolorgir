"""T3: edge cases (mandatory list from TZ section 11).

Core access via the backend factory (bolorgir package or ctypes
fallback). Tests requiring exact blg_status codes / raw buffers use blg_ctypes
directly. The whole module is skipped until the core is built.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpora
import reference as ref
from conftest import backend_available, make_backend  # noqa: E402

_ok, _why = backend_available(os.environ.get("BLG_TEST_BACKEND", "auto"))
if not _ok:
    pytest.skip(f"core unavailable: {_why}", allow_module_level=True)

zg = pytest.importorskip("blg_ctypes", reason="direct C ABI needed for T3")


@pytest.fixture
def byte_ctx(byte_tok):
    ctx = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    yield ctx
    ctx.destroy()


def _schema_ctx_session(ctx, schema):
    g = zg.compile_schema(ctx, schema)
    s = zg.Session(ctx, g)
    return g, s


# ---------------------------------------------------------------------------
# vocab not a multiple of 32
# ---------------------------------------------------------------------------

def test_vocab_not_multiple_of_32_tail_bits_zero(byte_ctx, byte_tok):
    assert byte_tok.vocab_size % 32 != 0
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        status, words = s.fill_mask_words()
        assert status == zg.BLG_OK
        # unused tail bits of the last word == 0 (DESIGN §4)
        used = byte_tok.vocab_size % 32
        tail_mask = (0xFFFFFFFF << used) & 0xFFFFFFFF
        assert words[-1] & tail_mask == 0, hex(words[-1])
        # pad is never allowed
        assert not (words[257 // 32] >> (257 % 32)) & 1
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# empty batch
# ---------------------------------------------------------------------------

def test_empty_batch_ok():
    status, statuses = zg.fill_masks_batch([], 8)
    assert status == zg.BLG_OK
    assert statuses == []


def test_batch_with_finished_session(byte_ctx):
    g1, s1 = _schema_ctx_session(byte_ctx, corpora.BOOL)
    g2, s2 = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        for tid in b"true":
            assert s1.accept(tid) == zg.BLG_OK
        assert s1.finish() == zg.BLG_OK
        overall, statuses = zg.fill_masks_batch([s1, s2], byte_ctx.spec.mask_words)
        assert statuses[0] == zg.BLG_ERR_WRONG_STATE   # finished session
        assert statuses[1] == zg.BLG_OK                # the rest are processed
        assert overall == zg.BLG_ERR_WRONG_STATE       # code of the first failing row
    finally:
        s1.destroy()
        s2.destroy()
        zg.grammar_release(g1)
        zg.grammar_release(g2)


# ---------------------------------------------------------------------------
# invalid token id / small buffer
# ---------------------------------------------------------------------------

def test_invalid_token_id(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        assert s.accept(byte_tok.vocab_size) == zg.BLG_ERR_INVALID_TOKEN
        assert s.accept(byte_tok.vocab_size + 100) == zg.BLG_ERR_INVALID_TOKEN
        # state unchanged: the mask is still valid
        status, _ = s.fill_mask_words()
        assert status == zg.BLG_OK
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_buffer_too_small(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        status, _ = s.fill_mask_words(byte_tok.mask_words - 1)
        assert status == zg.BLG_ERR_BUFFER_TOO_SMALL
        # a correctly sized buffer is OK
        status, _ = s.fill_mask_words(byte_tok.mask_words)
        assert status == zg.BLG_OK
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# repeated EOS (API.md §blg_accept_token: EOS at canEnd does not change state)
# ---------------------------------------------------------------------------

def test_repeated_eos_ok(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        for tid in b"false":
            assert s.accept(tid) == zg.BLG_OK
        assert s.can_end()
        eos = byte_tok.eos_ids[0]
        assert s.accept(eos) == zg.BLG_OK      # first EOS
        assert s.accept(eos) == zg.BLG_OK      # repeated EOS: state unchanged, OK
        assert s.can_end()
        assert s.finish() == zg.BLG_OK
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_eos_before_can_end_invalid(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        # at document start can_end == False; EOS is only allowed
        # at canEnd -> INVALID_TOKEN
        assert s.accept(byte_tok.eos_ids[0]) == zg.BLG_ERR_INVALID_TOKEN
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# PAD/special in an active session
# ---------------------------------------------------------------------------

def test_pad_special_invalid_token(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        assert s.accept(byte_tok.special_ids[0]) == zg.BLG_ERR_INVALID_TOKEN
        for tid in b"tr":
            assert s.accept(tid) == zg.BLG_OK
        assert s.accept(byte_tok.special_ids[0]) == zg.BLG_ERR_INVALID_TOKEN
        # and it is never in the mask
        ids = s.fill_mask_ids()
        assert byte_tok.special_ids[0] not in ids
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# token with several structural characters (dict tokenizer)
# ---------------------------------------------------------------------------

def test_multi_structural_token(dict_tok):
    backend = make_backend(dict_tok, "lazy")
    constraint = backend.compile(corpora.ACTION_AMOUNT)
    session = constraint.create_session()
    try:
        t_open = dict_tok.tokens.index(b'{"')
        assert t_open in session.mask()
        assert session.accept(t_open) == 0
        # after `{"` a key is expected: byte 'a' (action) is allowed -
        # the quote is already consumed
        ids = session.mask()
        assert ord("a") in ids
        assert session.accept(ord("a")) == 0
    finally:
        session.close()
        constraint.release()


# ---------------------------------------------------------------------------
# unfinished UTF-8 between tokens
# ---------------------------------------------------------------------------

def test_unfinished_utf8_between_tokens(byte_ctx, byte_tok):
    schema = {"type": "string", "minLength": 1, "maxLength": 1}
    g, s = _schema_ctx_session(byte_ctx, schema)
    try:
        assert s.accept(0x22) == zg.BLG_OK          # '"'
        assert s.accept(0xC3) == zg.BLG_OK          # start of 'é' (C3 A9)
        ids = s.fill_mask_ids()
        assert 0xA9 in ids                          # continuation allowed
        assert 0x22 not in ids                      # cannot close (rem>0)
        assert not s.can_end()
        assert s.accept(0xA9) == zg.BLG_OK
        assert 0x22 in s.fill_mask_ids()
        assert s.accept(0x22) == zg.BLG_OK
        assert s.can_end()
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# escapes at token boundaries
# ---------------------------------------------------------------------------

def test_escape_split_across_tokens(byte_ctx, byte_tok):
    schema = {"type": "string", "minLength": 1, "maxLength": 1}
    g, s = _schema_ctx_session(byte_ctx, schema)
    try:
        assert s.accept(0x22) == zg.BLG_OK
        assert s.accept(0x5C) == zg.BLG_OK           # '\' - intermediate ok
        ids = s.fill_mask_ids()
        for allowed in b'"\\bfnrtu':
            assert allowed in ids
        for forbidden in b"/x'e":
            assert forbidden not in ids
        assert s.accept(0x6E) == zg.BLG_OK           # 'n' completes \n
        assert 0x22 in s.fill_mask_ids()
        assert s.accept(0x22) == zg.BLG_OK
        assert s.can_end()
        assert s.finish() == zg.BLG_OK
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# empty string: as a value and as a literal-set alternative
# ---------------------------------------------------------------------------

def test_empty_string_value(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, {"type": "string"})
    try:
        assert 0x22 in s.fill_mask_ids()
        assert s.accept(0x22) == zg.BLG_OK
        assert 0x22 in s.fill_mask_ids()            # close the empty string
        assert s.accept(0x22) == zg.BLG_OK
        assert s.can_end()
        assert s.finish() == zg.BLG_OK
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_empty_string_literal_alternative(byte_ctx, byte_tok):
    g = zg.compile_literals(byte_ctx, ["", "x"])
    s = zg.Session(byte_ctx, g)
    try:
        assert s.can_end()                          # empty string is a document
        assert byte_tok.eos_ids[0] in s.fill_mask_ids()
        assert ord("x") in s.fill_mask_ids()
        assert s.finish() == zg.BLG_OK               # finish without any token
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# enum with common prefixes
# ---------------------------------------------------------------------------

def test_enum_common_prefixes(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.ENUM_PREFIX)
    try:
        assert s.accept(0x22) == zg.BLG_OK
        assert s.accept(ord("a")) == zg.BLG_OK
        ids = s.fill_mask_ids()
        assert 0x22 in ids                          # "a" is a document
        assert ord("b") in ids                      # continuation to "ab"/"abc"
        assert ord("z") not in ids
        assert s.accept(ord("b")) == zg.BLG_OK
        ids = s.fill_mask_ids()
        assert 0x22 in ids                          # "ab"
        assert ord("c") in ids                      # "abc"
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# optional keys: skip, order, no going back
# ---------------------------------------------------------------------------

def test_optional_keys_skip_order_no_return(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.OPT_KEYS)
    try:
        for tid in b'{"a":1,"c":true':
            assert s.accept(tid) == zg.BLG_OK
        ids = s.fill_mask_ids()
        assert 0x7D in ids                          # '}' - may finish
        assert 0x2C not in ids                      # ',' - no: only the
                                                    # skipped b remains
        assert s.accept(0x7D) == zg.BLG_OK
        assert s.can_end()
        # going back to a skipped key is impossible - separate session
        g2, s2 = _schema_ctx_session(byte_ctx, corpora.OPT_KEYS)
        try:
            for tid in b'{"a":1,"c":true':
                assert s2.accept(tid) == zg.BLG_OK
            assert s2.accept(0x2C) == zg.BLG_ERR_INVALID_TOKEN
        finally:
            s2.destroy()
            zg.grammar_release(g2)
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_required_key_cannot_be_skipped(byte_ctx, byte_tok):
    schema = {"type": "object",
              "properties": {"opt": {"type": "boolean"},
                             "req": {"type": "integer"}},
              "required": ["req"], "additionalProperties": False}
    g, s = _schema_ctx_session(byte_ctx, schema)
    try:
        for tid in b'{"opt":true':
            assert s.accept(tid) == zg.BLG_OK
        ids = s.fill_mask_ids()
        assert 0x7D not in ids                      # '}' forbidden: a required key remains
        assert 0x2C in ids
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# number before a delimiter (lazy completion)
# ---------------------------------------------------------------------------

def test_number_before_delimiter(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.ARR_INT)
    try:
        for tid in b"[12":
            assert s.accept(tid) == zg.BLG_OK
        ids = s.fill_mask_ids()
        assert 0x5D in ids                          # ']' completes number and array
        assert 0x2C in ids                          # ',' - next item
        assert ord("3") in ids                      # number continuation
        assert not s.can_end()                      # array not closed
        assert s.accept(0x5D) == zg.BLG_OK
        assert s.can_end()
        # leading zeros are forbidden: no digit is allowed after '0'
        g2, s2 = _schema_ctx_session(byte_ctx, corpora.ARR_INT)
        try:
            for tid in b"[0":
                assert s2.accept(tid) == zg.BLG_OK
            assert ord("1") not in s2.fill_mask_ids()
            assert 0x5D in s2.fill_mask_ids()
        finally:
            s2.destroy()
            zg.grammar_release(g2)
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# min/max lengths of strings and arrays
# ---------------------------------------------------------------------------

def test_string_min_length_enforced(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, {"type": "string", "minLength": 2})
    try:
        assert s.accept(0x22) == zg.BLG_OK
        assert 0x22 not in s.fill_mask_ids()        # cannot close empty
        assert s.accept(ord("a")) == zg.BLG_OK
        assert 0x22 not in s.fill_mask_ids()        # minLength=2
        assert s.accept(ord("b")) == zg.BLG_OK
        assert 0x22 in s.fill_mask_ids()
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_string_max_length_enforced(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, {"type": "string", "maxLength": 1})
    try:
        for tid in b'"a':
            assert s.accept(tid) == zg.BLG_OK
        ids = s.fill_mask_ids()
        assert ord("b") not in ids
        assert 0x22 in ids
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_array_min_max_items_enforced(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.ARR_INT)  # min 1 max 3
    try:
        assert s.accept(0x5B) == zg.BLG_OK
        assert 0x5D not in s.fill_mask_ids()        # minItems=1
        for tid in b"1,2,3":
            assert s.accept(tid) == zg.BLG_OK
        ids = s.fill_mask_ids()
        assert 0x2C not in ids                      # maxItems=3
        assert 0x5D in ids
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# DeadEnd: per DESIGN §1.9 unreachable on valid traces in canonical-v1.
# Fixed by test: no valid trace yields an empty mask.
# ---------------------------------------------------------------------------

def test_dead_end_never_on_valid_traces(byte_ctx, byte_tok):
    eos = set(byte_tok.eos_ids)
    for case in corpora.SMALL:
        lang = ref.compile_schema(case["schema"])
        caps = dict(max_abs_int=2, max_string_len=1, max_items_extra=1)
        try:
            docs = ref.enumerate_language(lang, **caps)
        except ref.EnumerationCapped:
            continue
        g = zg.compile_schema(byte_ctx, case["schema"])
        try:
            for doc in sorted(docs)[:50]:
                s = zg.Session(byte_ctx, g)
                try:
                    matcher = ref.Matcher(lang)
                    prefix = b""
                    for b in doc:
                        status, words = s.fill_mask_words()
                        assert status == zg.BLG_OK, (
                            f"DEAD_END on valid trace: {case['name']} {prefix!r}")
                        ids = {i for i in range(byte_tok.vocab_size)
                               if words[i // 32] >> (i % 32) & 1}
                        assert ids, f"empty mask: {case['name']} {prefix!r}"
                        assert b in ids
                        assert s.accept(b) == zg.BLG_OK
                        assert matcher.feed(bytes([b]))
                        prefix += bytes([b])
                    status, _ = s.fill_mask_words()
                    assert status == zg.BLG_OK       # can_end => eos in the mask
                    assert s.can_end()
                finally:
                    s.destroy()
        finally:
            zg.grammar_release(g)


# ---------------------------------------------------------------------------
# WrongState / finish without can_end
# ---------------------------------------------------------------------------

def test_wrong_state_after_finish_and_abort(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        for tid in b"true":
            assert s.accept(tid) == zg.BLG_OK
        assert s.finish() == zg.BLG_OK
        assert s.accept(ord("x")) == zg.BLG_ERR_WRONG_STATE
        status, _ = s.fill_mask_words()
        assert status == zg.BLG_ERR_WRONG_STATE
        assert s.finish() == zg.BLG_OK               # finish is idempotent
        assert s.abort() == zg.BLG_OK                # abort is idempotent

        g2, s2 = _schema_ctx_session(byte_ctx, corpora.BOOL)
        try:
            assert s2.abort() == zg.BLG_OK
            assert s2.accept(ord("t")) == zg.BLG_ERR_WRONG_STATE
            status, _ = s2.fill_mask_words()
            assert status == zg.BLG_ERR_WRONG_STATE
        finally:
            s2.destroy()
            zg.grammar_release(g2)
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_finish_without_can_end_wrong_state(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.ARR_INT)
    try:
        for tid in b"[1,":
            assert s.accept(tid) == zg.BLG_OK
        assert not s.can_end()
        assert s.finish() == zg.BLG_ERR_WRONG_STATE
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_accept_forbidden_token_keeps_state(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        _, before = s.fill_mask_words()
        assert s.accept(ord("x")) == zg.BLG_ERR_INVALID_TOKEN
        _, after = s.fill_mask_words()
        assert before == after                      # state unchanged
        for tid in b"true":
            assert s.accept(tid) == zg.BLG_OK
        assert s.can_end()
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# TZ 3.1: dead-end tokens must not be allowed
# ---------------------------------------------------------------------------

def test_dead_end_token_excluded_on_partial_vocab():
    """[ab, a, <eos>] with literal "ab": "a" is byte-legal but nothing can
    finish the literal afterwards, so TZ 3.1 forbids it. The core mask must
    match the independent oracle at every step, accepting "a" must fail
    with invalid_token and leave the state unchanged. All modes."""
    spec = ref.TokenizerSpec(tokens=(b"ab", b"a", b""), eos_ids=(2,)).validate()
    oracle = ref.PrefixOracle({b"ab"})
    for mode in (zg.BLG_MODE_LAZY, zg.BLG_MODE_ADAPTIVE, zg.BLG_MODE_PRECOMPUTE):
        ctx = zg.Context(spec, mode)
        g = zg.compile_literals(ctx, ["ab"])
        s = zg.Session(ctx, g)
        try:
            assert s.fill_mask_ids() == oracle.mask(b"", spec)
            assert s.fill_mask_ids() == {0}         # only the whole "ab"
            assert s.accept(1) == zg.BLG_ERR_INVALID_TOKEN
            assert s.fill_mask_ids() == {0}         # state unchanged
            assert s.accept(0) == zg.BLG_OK
            assert s.fill_mask_ids() == oracle.mask(b"ab", spec)
            assert s.fill_mask_ids() == {2}         # EOS at the end
            assert s.can_end()
        finally:
            s.destroy()
            zg.grammar_release(g)
            ctx.destroy()


def test_dead_end_token_excluded_json_schema_object():
    """Finite literal JSON object on a partial vocabulary: tokens that
    cannot reach the closing brace are excluded (same TZ 3.1 rule)."""
    spec = ref.TokenizerSpec(
        tokens=(b'{"a":', b'"x"', b"}", b"{", b"\"a\":", b'"', b"x", b"a", b"", b""),
        eos_ids=(8,), special_ids=(9,),
    ).validate()
    schema = {"type": "object",
              "properties": {"a": {"type": "string", "const": "x"}},
              "required": ["a"], "additionalProperties": False}
    docs = {b'{"a":"x"}'}
    oracle = ref.PrefixOracle(docs)
    for mode in (zg.BLG_MODE_LAZY, zg.BLG_MODE_ADAPTIVE):
        ctx = zg.Context(spec, mode)
        g = zg.compile_schema(ctx, schema)
        s = zg.Session(ctx, g)
        try:
            step = b""
            for tid in (0, 1, 2):  # { " a " :, "x", }
                assert s.fill_mask_ids() == oracle.mask(step, spec), step
                assert tid in oracle.mask(step, spec), step
                assert s.accept(tid) == zg.BLG_OK, step
                step += spec.tokens[tid]
            assert s.fill_mask_ids() == {8}
            assert s.can_end()
        finally:
            s.destroy()
            zg.grammar_release(g)
            ctx.destroy()


# ---------------------------------------------------------------------------
# Busy / invalid struct_size / conflicting limits (via the ctypes layer)
# ---------------------------------------------------------------------------

def test_destroy_busy_context(byte_tok):
    ctx = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    g = zg.compile_schema(ctx, corpora.BOOL)
    s = zg.Session(ctx, g)
    try:
        assert ctx.destroy() == zg.BLG_ERR_BUSY
        s.destroy()
        assert ctx.destroy() == zg.BLG_ERR_BUSY      # the grammar is still alive
        zg.grammar_release(g)
        assert ctx.destroy() == zg.BLG_OK            # now it is allowed
    finally:
        ctx.destroy()


def test_invalid_struct_size(byte_tok):
    import ctypes
    # invalid struct_size of the context config
    config = zg.ZgContextConfig()
    config.struct_size = 999
    config.version = zg.BLG_ABI_VERSION
    desc, keep = zg.make_tokenizer_desc(byte_tok)
    out = ctypes.c_void_p()
    err = zg.new_error()
    status = zg.LIB.blg_context_create(ctypes.byref(config), ctypes.byref(desc),
                                      ctypes.byref(out), ctypes.byref(err))
    assert status == zg.BLG_ERR_INVALID_ARGUMENT
    # invalid struct_size of blg_error
    ctx = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    try:
        bad_err = zg.ZgError()
        bad_err.struct_size = 1
        req = zg.ZgCompileRequest()
        req.struct_size = ctypes.sizeof(zg.ZgCompileRequest)
        req.kind = zg.BLG_CONSTRAINT_JSON_SCHEMA
        data = b'{"type":"boolean"}'
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        req.data = buf
        req.data_len = len(data)
        g = ctypes.c_void_p()
        status = zg.LIB.blg_compile(ctx.handle, ctypes.byref(req),
                                   ctypes.byref(g), ctypes.byref(bad_err))
        assert status == zg.BLG_ERR_INVALID_ARGUMENT
    finally:
        ctx.destroy()


def test_cache_limit_above_memory_limit_invalid(byte_tok):
    with pytest.raises(zg.CoreFailure) as ei:
        zg.Context(byte_tok, zg.BLG_MODE_ADAPTIVE,
                   memory_limit_bytes=1024, cache_limit_bytes=2048)
    assert ei.value.status == zg.BLG_ERR_INVALID_ARGUMENT


# ---------------------------------------------------------------------------
# Grammar ownership and artifact cache budget
# ---------------------------------------------------------------------------

def test_context_busy_with_live_grammar_cached_and_lazy(byte_tok):
    # The old "cache + user" refcount gave a false equality of the
    # counters; destroy freed the context with a live handle (SIGSEGV on the
    # subsequent release). Now external references are counted separately:
    # destroy must return BUSY in every mode, and closing after BUSY and
    # dropped references must succeed.
    for mode, cache in ((zg.BLG_MODE_ADAPTIVE, zg.BLG_CACHE_DEFAULT),
                        (zg.BLG_MODE_LAZY, 0)):
        ctx = zg.Context(byte_tok, mode, cache_limit_bytes=cache)
        g1 = zg.compile_literals(ctx, ["a"])
        g2 = zg.compile_literals(ctx, ["a"])  # with adaptive this is a cache hit
        try:
            assert ctx.destroy() == zg.BLG_ERR_BUSY
            zg.grammar_release(g1)
            assert ctx.destroy() == zg.BLG_ERR_BUSY  # g2 (and the cache entry) is alive
            s = zg.Session(ctx, g2)
            zg.grammar_release(g2)
            assert ctx.destroy() == zg.BLG_ERR_BUSY  # the session holds the grammar
            s.destroy()
            # After all external references are dropped, destroy resets the cache itself.
            assert ctx.destroy() == zg.BLG_OK
        finally:
            ctx.destroy()


def test_artifact_cache_budget_and_reset():
    # The artifact cache budget counts actually retained memory
    # (grammar + handle + schema copy + capacity). Counterexample:
    # 600 unique schemas retained 255346 bytes with artifact_limit=16384 and
    # the next request got RESOURCE_LIMIT. Now retention ≤ the budget and a
    # large literal compiles; reset_cache returns the memory.
    spec = ref.TokenizerSpec(
        tokens=tuple(bytes([c]) for c in b"0123456789") + (b"<eos>",),
        eos_ids=(10,),
    ).validate()
    cache_limit = 65536
    artifact_limit = cache_limit // 4
    # memory_limit was 262144 in that counterexample, then 524288
    # after the parser Frame grew for spec-v1 open objects (ADR-0006 D2).
    # The lazy-alternation thread cap raise to 128 doubled the inline State
    # arrays, so a Session is now 788504 bytes (3 inline States of 262660 B:
    # ping-pong pair + mask spare) and needs 1 MiB. (Same as the Zig twin
    # test in src/c_api.zig.) cache_limit is unchanged, so artifact_limit
    # and every budget assertion below still exercise the same invariant.
    ctx = zg.Context(spec, zg.BLG_MODE_ADAPTIVE, cache_limit_bytes=cache_limit,
                     memory_limit_bytes=1048576)
    try:
        for i in range(600):
            g = zg.compile_literals(ctx, [str(i)])
            zg.grammar_release(g)
        st = ctx.get_stats()
        assert st.mem_used[1] <= artifact_limit
        g = zg.compile_literals(ctx, ["1" * 1000])  # before the fix - RESOURCE_LIMIT
        zg.grammar_release(g)
        assert ctx.get_stats().mem_used[1] <= artifact_limit
        # Testable reset: live handles keep working.
        g2 = zg.compile_literals(ctx, ["7"])
        before = ctx.get_stats().mem_used[1]
        assert before > 0
        assert ctx.reset_cache() == zg.BLG_OK
        after = ctx.get_stats().mem_used[1]
        assert after < before  # the cache released what it retained
        assert after > 0  # live user objects remain
        s = zg.Session(ctx, g2)  # the handle keeps working
        assert ctx.destroy() == zg.BLG_ERR_BUSY
        s.destroy()
        zg.grammar_release(g2)
        assert ctx.get_stats().mem_used[1] == 0
        assert ctx.destroy() == zg.BLG_OK
    finally:
        ctx.destroy()
