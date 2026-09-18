"""T3: edge cases (mandatory list from TZ section 11).

Core access via the backend factory (zig_constraints package or ctypes
fallback). Tests requiring exact zg_status codes / raw buffers use zg_ctypes
directly. The whole module is skipped until the core is built.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpora
import reference as ref
from conftest import backend_available, make_backend  # noqa: E402

_ok, _why = backend_available(os.environ.get("ZG_TEST_BACKEND", "auto"))
if not _ok:
    pytest.skip(f"core unavailable: {_why}", allow_module_level=True)

zg = pytest.importorskip("zg_ctypes", reason="direct C ABI needed for T3")


@pytest.fixture
def byte_ctx(byte_tok):
    ctx = zg.Context(byte_tok, zg.ZG_MODE_LAZY)
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
        assert status == zg.ZG_OK
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
    assert status == zg.ZG_OK
    assert statuses == []


def test_batch_with_finished_session(byte_ctx):
    g1, s1 = _schema_ctx_session(byte_ctx, corpora.BOOL)
    g2, s2 = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        for tid in b"true":
            assert s1.accept(tid) == zg.ZG_OK
        assert s1.finish() == zg.ZG_OK
        overall, statuses = zg.fill_masks_batch([s1, s2], byte_ctx.spec.mask_words)
        assert statuses[0] == zg.ZG_ERR_WRONG_STATE   # finished session
        assert statuses[1] == zg.ZG_OK                # the rest are processed
        assert overall == zg.ZG_ERR_WRONG_STATE       # code of the first failing row
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
        assert s.accept(byte_tok.vocab_size) == zg.ZG_ERR_INVALID_TOKEN
        assert s.accept(byte_tok.vocab_size + 100) == zg.ZG_ERR_INVALID_TOKEN
        # state unchanged: the mask is still valid
        status, _ = s.fill_mask_words()
        assert status == zg.ZG_OK
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_buffer_too_small(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        status, _ = s.fill_mask_words(byte_tok.mask_words - 1)
        assert status == zg.ZG_ERR_BUFFER_TOO_SMALL
        # a correctly sized buffer is OK
        status, _ = s.fill_mask_words(byte_tok.mask_words)
        assert status == zg.ZG_OK
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# repeated EOS (API.md §zg_accept_token: EOS at canEnd does not change state)
# ---------------------------------------------------------------------------

def test_repeated_eos_ok(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        for tid in b"false":
            assert s.accept(tid) == zg.ZG_OK
        assert s.can_end()
        eos = byte_tok.eos_ids[0]
        assert s.accept(eos) == zg.ZG_OK      # first EOS
        assert s.accept(eos) == zg.ZG_OK      # repeated EOS: state unchanged, OK
        assert s.can_end()
        assert s.finish() == zg.ZG_OK
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_eos_before_can_end_invalid(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        # at document start can_end == False; EOS is only allowed
        # at canEnd -> INVALID_TOKEN
        assert s.accept(byte_tok.eos_ids[0]) == zg.ZG_ERR_INVALID_TOKEN
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# PAD/special in an active session
# ---------------------------------------------------------------------------

def test_pad_special_invalid_token(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        assert s.accept(byte_tok.special_ids[0]) == zg.ZG_ERR_INVALID_TOKEN
        for tid in b"tr":
            assert s.accept(tid) == zg.ZG_OK
        assert s.accept(byte_tok.special_ids[0]) == zg.ZG_ERR_INVALID_TOKEN
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
        # after `{"` a key is expected: byte 'a' (action) is allowed —
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
        assert s.accept(0x22) == zg.ZG_OK          # '"'
        assert s.accept(0xD0) == zg.ZG_OK          # start of 'Д' (D0 94)
        ids = s.fill_mask_ids()
        assert 0x94 in ids                          # continuation allowed
        assert 0x22 not in ids                      # cannot close (rem>0)
        assert not s.can_end()
        assert s.accept(0x94) == zg.ZG_OK
        assert 0x22 in s.fill_mask_ids()
        assert s.accept(0x22) == zg.ZG_OK
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
        assert s.accept(0x22) == zg.ZG_OK
        assert s.accept(0x5C) == zg.ZG_OK           # '\' — intermediate ok
        ids = s.fill_mask_ids()
        for allowed in b'"\\bfnrtu':
            assert allowed in ids
        for forbidden in b"/x'e":
            assert forbidden not in ids
        assert s.accept(0x6E) == zg.ZG_OK           # 'n' completes \n
        assert 0x22 in s.fill_mask_ids()
        assert s.accept(0x22) == zg.ZG_OK
        assert s.can_end()
        assert s.finish() == zg.ZG_OK
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
        assert s.accept(0x22) == zg.ZG_OK
        assert 0x22 in s.fill_mask_ids()            # close the empty string
        assert s.accept(0x22) == zg.ZG_OK
        assert s.can_end()
        assert s.finish() == zg.ZG_OK
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
        assert s.finish() == zg.ZG_OK               # finish without any token
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# enum with common prefixes
# ---------------------------------------------------------------------------

def test_enum_common_prefixes(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.ENUM_PREFIX)
    try:
        assert s.accept(0x22) == zg.ZG_OK
        assert s.accept(ord("a")) == zg.ZG_OK
        ids = s.fill_mask_ids()
        assert 0x22 in ids                          # "a" is a document
        assert ord("b") in ids                      # continuation to "ab"/"abc"
        assert ord("z") not in ids
        assert s.accept(ord("b")) == zg.ZG_OK
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
            assert s.accept(tid) == zg.ZG_OK
        ids = s.fill_mask_ids()
        assert 0x7D in ids                          # '}' — may finish
        assert 0x2C not in ids                      # ',' — no: only the
                                                    # skipped b remains
        assert s.accept(0x7D) == zg.ZG_OK
        assert s.can_end()
        # going back to a skipped key is impossible — separate session
        g2, s2 = _schema_ctx_session(byte_ctx, corpora.OPT_KEYS)
        try:
            for tid in b'{"a":1,"c":true':
                assert s2.accept(tid) == zg.ZG_OK
            assert s2.accept(0x2C) == zg.ZG_ERR_INVALID_TOKEN
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
            assert s.accept(tid) == zg.ZG_OK
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
            assert s.accept(tid) == zg.ZG_OK
        ids = s.fill_mask_ids()
        assert 0x5D in ids                          # ']' completes number and array
        assert 0x2C in ids                          # ',' — next item
        assert ord("3") in ids                      # number continuation
        assert not s.can_end()                      # array not closed
        assert s.accept(0x5D) == zg.ZG_OK
        assert s.can_end()
        # leading zeros are forbidden: no digit is allowed after '0'
        g2, s2 = _schema_ctx_session(byte_ctx, corpora.ARR_INT)
        try:
            for tid in b"[0":
                assert s2.accept(tid) == zg.ZG_OK
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
        assert s.accept(0x22) == zg.ZG_OK
        assert 0x22 not in s.fill_mask_ids()        # cannot close empty
        assert s.accept(ord("a")) == zg.ZG_OK
        assert 0x22 not in s.fill_mask_ids()        # minLength=2
        assert s.accept(ord("b")) == zg.ZG_OK
        assert 0x22 in s.fill_mask_ids()
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_string_max_length_enforced(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, {"type": "string", "maxLength": 1})
    try:
        for tid in b'"a':
            assert s.accept(tid) == zg.ZG_OK
        ids = s.fill_mask_ids()
        assert ord("b") not in ids
        assert 0x22 in ids
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_array_min_max_items_enforced(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.ARR_INT)  # min 1 max 3
    try:
        assert s.accept(0x5B) == zg.ZG_OK
        assert 0x5D not in s.fill_mask_ids()        # minItems=1
        for tid in b"1,2,3":
            assert s.accept(tid) == zg.ZG_OK
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
                        assert status == zg.ZG_OK, (
                            f"DEAD_END on valid trace: {case['name']} {prefix!r}")
                        ids = {i for i in range(byte_tok.vocab_size)
                               if words[i // 32] >> (i % 32) & 1}
                        assert ids, f"empty mask: {case['name']} {prefix!r}"
                        assert b in ids
                        assert s.accept(b) == zg.ZG_OK
                        assert matcher.feed(bytes([b]))
                        prefix += bytes([b])
                    status, _ = s.fill_mask_words()
                    assert status == zg.ZG_OK       # can_end => eos in the mask
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
            assert s.accept(tid) == zg.ZG_OK
        assert s.finish() == zg.ZG_OK
        assert s.accept(ord("x")) == zg.ZG_ERR_WRONG_STATE
        status, _ = s.fill_mask_words()
        assert status == zg.ZG_ERR_WRONG_STATE
        assert s.finish() == zg.ZG_OK               # finish is idempotent
        assert s.abort() == zg.ZG_OK                # abort is idempotent

        g2, s2 = _schema_ctx_session(byte_ctx, corpora.BOOL)
        try:
            assert s2.abort() == zg.ZG_OK
            assert s2.accept(ord("t")) == zg.ZG_ERR_WRONG_STATE
            status, _ = s2.fill_mask_words()
            assert status == zg.ZG_ERR_WRONG_STATE
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
            assert s.accept(tid) == zg.ZG_OK
        assert not s.can_end()
        assert s.finish() == zg.ZG_ERR_WRONG_STATE
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_accept_forbidden_token_keeps_state(byte_ctx, byte_tok):
    g, s = _schema_ctx_session(byte_ctx, corpora.BOOL)
    try:
        _, before = s.fill_mask_words()
        assert s.accept(ord("x")) == zg.ZG_ERR_INVALID_TOKEN
        _, after = s.fill_mask_words()
        assert before == after                      # state unchanged
        for tid in b"true":
            assert s.accept(tid) == zg.ZG_OK
        assert s.can_end()
    finally:
        s.destroy()
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# Busy / invalid struct_size / conflicting limits (via the ctypes layer)
# ---------------------------------------------------------------------------

def test_destroy_busy_context(byte_tok):
    ctx = zg.Context(byte_tok, zg.ZG_MODE_LAZY)
    g = zg.compile_schema(ctx, corpora.BOOL)
    s = zg.Session(ctx, g)
    try:
        assert ctx.destroy() == zg.ZG_ERR_BUSY
        s.destroy()
        assert ctx.destroy() == zg.ZG_ERR_BUSY      # the grammar is still alive
        zg.grammar_release(g)
        assert ctx.destroy() == zg.ZG_OK            # now it is allowed
    finally:
        ctx.destroy()


def test_invalid_struct_size(byte_tok):
    import ctypes
    # invalid struct_size of the context config
    config = zg.ZgContextConfig()
    config.struct_size = 999
    config.version = zg.ZG_ABI_VERSION
    desc, keep = zg.make_tokenizer_desc(byte_tok)
    out = ctypes.c_void_p()
    err = zg.new_error()
    status = zg.LIB.zg_context_create(ctypes.byref(config), ctypes.byref(desc),
                                      ctypes.byref(out), ctypes.byref(err))
    assert status == zg.ZG_ERR_INVALID_ARGUMENT
    # invalid struct_size of zg_error
    ctx = zg.Context(byte_tok, zg.ZG_MODE_LAZY)
    try:
        bad_err = zg.ZgError()
        bad_err.struct_size = 1
        req = zg.ZgCompileRequest()
        req.struct_size = ctypes.sizeof(zg.ZgCompileRequest)
        req.kind = zg.ZG_CONSTRAINT_JSON_SCHEMA
        data = b'{"type":"boolean"}'
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        req.data = buf
        req.data_len = len(data)
        g = ctypes.c_void_p()
        status = zg.LIB.zg_compile(ctx.handle, ctypes.byref(req),
                                   ctypes.byref(g), ctypes.byref(bad_err))
        assert status == zg.ZG_ERR_INVALID_ARGUMENT
    finally:
        ctx.destroy()


def test_cache_limit_above_memory_limit_invalid(byte_tok):
    with pytest.raises(zg.CoreFailure) as ei:
        zg.Context(byte_tok, zg.ZG_MODE_ADAPTIVE,
                   memory_limit_bytes=1024, cache_limit_bytes=2048)
    assert ei.value.status == zg.ZG_ERR_INVALID_ARGUMENT
