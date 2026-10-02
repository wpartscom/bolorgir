"""ADR-0007: curated ABI-level differential tests of the mask fast path.

Every case compares the fast path (blg_context_config.mask_fast_path =
default/on) against the plain trie walk (mask_fast_path =
BLG_MASK_FAST_PATH_OFF) bit-for-bit over the full mask words, across
modes (lazy/adaptive = cache off/on), max_workers in {1, 2, 8} and error
outcomes. The kill switch (config field and BLG_MASK_FAST_PATH=0 env) is
verified directly.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reference as ref
import blg_ctypes as blg

pytest.importorskip("blg_ctypes")


def make_spec(extra_tokens=()) -> ref.TokenizerSpec:
    """All 256 single bytes (byte-complete) + extra tokens + eos + pad."""
    tokens = tuple(bytes([i]) for i in range(256)) + tuple(extra_tokens)
    vocab = len(tokens) + 2
    return ref.TokenizerSpec(
        tokens=tokens + (b"", b"<pad>"),
        eos_ids=(vocab - 2,), special_ids=(vocab - 1,)).validate()


def mask_at(spec, schema, prefix: bytes, **ctx_kw):
    """-> (status, words, work_ops) of fill_mask after accepting prefix."""
    ctx_kw.setdefault("work_limit_ops", 1 << 50)
    ctx = blg.Context(spec, **ctx_kw)
    try:
        g = blg.compile_schema(ctx, schema)
        s = blg.Session(ctx, g)
        for b in prefix:
            assert s.accept(b) == blg.BLG_OK
        s0 = ctx.get_stats()
        status, words = s.fill_mask_words()
        s1 = ctx.get_stats()
        s.destroy()
        blg.grammar_release(g)
        return status, words, s1.work_ops_total - s0.work_ops_total
    finally:
        ctx.destroy()


def ids_of(spec, words) -> set[int]:
    return {i for i in range(spec.vocab_size) if words[i // 32] >> (i % 32) & 1}


def assert_fastpath_equal(spec, schema, prefix: bytes):
    """Masks must be identical for fast path on/off, cache on/off,
    max_workers in {1, 2, 8}. Returns the reference (off) status/ids."""
    variants = []
    for mode, cache in ((blg.BLG_MODE_LAZY, 0),
                        (blg.BLG_MODE_ADAPTIVE, blg.BLG_CACHE_DEFAULT)):
        for fp in (0, blg.BLG_MASK_FAST_PATH_ON, blg.BLG_MASK_FAST_PATH_OFF):
            for workers in (1, 2, 8):
                status, words, _ = mask_at(
                    spec, schema, prefix, mode=mode,
                    cache_limit_bytes=cache, mask_fast_path=fp,
                    max_workers=workers)
                variants.append(((mode, fp, workers), status, words))
    tag0, st0, w0 = variants[0]
    for tag, st, w in variants[1:]:
        assert (st, w) == (st0, w0), f"mask diverges: {tag} vs {tag0}"
    return st0, ids_of(spec, w0)


# ---------------------------------------------------------------------------
# Length limits: minLength/maxLength edges, tokens at cp == R-1, R, R+1.
# ---------------------------------------------------------------------------

def test_length_bounds_residual_edges():
    spec = make_spec([b"ab", b"abc", b"abcd", "é".encode(), "éé".encode()])
    AB, ABC, ABCD, E1, E2 = 256, 257, 258, 259, 260
    schema = {"type": "string", "minLength": 2, "maxLength": 3}

    # count 0, R = 3: cp <= 3 allowed; closing " refused (minLength 2).
    st, ids = assert_fastpath_equal(spec, schema, b'"')
    assert st == blg.BLG_OK
    assert AB in ids and ABC in ids and ABCD not in ids
    assert E1 in ids and E2 in ids
    assert ord('"') not in ids

    # count 1, R = 2: cp == R allowed, cp == R+1 refused, close refused.
    st, ids = assert_fastpath_equal(spec, schema, b'"a')
    assert AB in ids and ABC not in ids and E2 in ids
    assert ord('"') not in ids

    # count 2, R = 1: close allowed, cp 2 refused.
    st, ids = assert_fastpath_equal(spec, schema, b'"ab')
    assert ord('"') in ids and AB not in ids and E1 in ids

    # count 3 == maxLength: every content token refused, close allowed.
    st, ids = assert_fastpath_equal(spec, schema, b'"abc')
    assert ord('"') in ids
    assert all(t not in ids for t in (ord('a'), AB, ABC, E1))


def test_tokens_longer_than_every_bound():
    long_tok = b"x" * 4096
    spec = make_spec([long_tok])
    schema = {"type": "string", "maxLength": 4095}
    st, ids = assert_fastpath_equal(spec, schema, b'"')
    assert 256 not in ids  # cp 4096 > R 4095
    schema = {"type": "string", "maxLength": 4096}
    st, ids = assert_fastpath_equal(spec, schema, b'"')
    assert 256 in ids  # cp == R exactly


# ---------------------------------------------------------------------------
# Value boundaries: tokens closing a string and continuing into the parent.
# ---------------------------------------------------------------------------

def test_closing_tokens_continue_into_parent():
    spec = make_spec([b'{"a":', b'x"', b'x",', b'x","b":', b'x"}', b'1}', b'1'])
    X_CLOSE, X_CLOSE_COMMA, X_CLOSE_KEY, X_CLOSE_OBJ, ONE_OBJ = 257, 258, 259, 260, 261
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    st, ids = assert_fastpath_equal(spec, schema, b'{"a":"')
    assert X_CLOSE in ids          # close, then , or } must follow
    assert X_CLOSE_COMMA in ids    # close + comma
    assert X_CLOSE_KEY in ids      # close + ,"b":
    assert X_CLOSE_OBJ not in ids  # "b" is required: cannot close the object
    assert ord('"') in ids         # empty content close
    assert ord('x') in ids         # plain content

    # After the full "a" value and "b": the integer state.
    st, ids = assert_fastpath_equal(spec, schema, b'{"a":"x","b":')
    assert ord('1') in ids and ONE_OBJ in ids


def test_minlength_close_gate_survives_normal_form():
    """minLength: at count >= minLength the close (and close-and-continue)
    tokens are mask-legal. The ADR-0007 D2+ normal form clamps the content
    count at minLength, so the adaptive cache must neither compute nor
    serve a count-0 mask (close refused) at a close-able position."""
    spec = make_spec([b'{"a":', b'","', b'"b":', b'x"}'])
    CLOSE_COMMA = 257
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string", "minLength": 2},
                       "b": {"type": "string", "minLength": 2}},
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    # count 1 < minLength 2: the close stays forbidden in every variant.
    st, ids = assert_fastpath_equal(spec, schema, b'{"a":"x')
    assert st == blg.BLG_OK
    assert ord('"') not in ids and CLOSE_COMMA not in ids
    # count 2 and beyond: close and close-and-continue are allowed.
    for prefix in (b'{"a":"xx', b'{"a":"xxx'):
        st, ids = assert_fastpath_equal(spec, schema, prefix)
        assert st == blg.BLG_OK
        assert ord('"') in ids and CLOSE_COMMA in ids
        assert ord('x') in ids  # content still allowed (unbounded maxLength)


def test_minlength_close_able_positions_share_one_cache_entry():
    """The clamped normal form still merges close-able positions: counts
    2 and 8 under minLength 2 share one adaptive cache entry, and the
    shared mask equals the plain walk at both positions."""
    spec = make_spec([b"bc", b"def", b"ghij"])
    schema = {"type": "object",
              "properties": {"s": {"type": "string", "minLength": 2}},
              "required": ["s"], "additionalProperties": False}
    prefix1, prefix2 = b'{"s":"ab', b'{"s":"abcdefgh'
    ctx = blg.Context(spec, mode=blg.BLG_MODE_ADAPTIVE,
                      cache_limit_bytes=blg.BLG_CACHE_DEFAULT,
                      work_limit_ops=1 << 50)
    try:
        g = blg.compile_schema(ctx, schema)
        masks = []
        hits = []
        for prefix in (prefix1, prefix2):
            s = blg.Session(ctx, g)
            for b in prefix:
                assert s.accept(b) == blg.BLG_OK
            s0 = ctx.get_stats()
            status, words = s.fill_mask_words()
            assert status == blg.BLG_OK
            s1 = ctx.get_stats()
            masks.append(words)
            hits.append(s1.cache_hits - s0.cache_hits)
            s.destroy()
        blg.grammar_release(g)
    finally:
        ctx.destroy()
    assert hits == [0, 1], hits
    assert masks[0] == masks[1]
    assert ord('"') in {i for i in range(spec.vocab_size)
                        if masks[0][i // 32] >> (i % 32) & 1}
    for prefix in (prefix1, prefix2):
        st, words, _ = mask_at(spec, schema, prefix,
                               mode=blg.BLG_MODE_ADAPTIVE,
                               cache_limit_bytes=blg.BLG_CACHE_DEFAULT,
                               mask_fast_path=blg.BLG_MASK_FAST_PATH_OFF)
        assert st == blg.BLG_OK
        assert words == masks[0]


def test_eos_at_can_end_transitions():
    spec = make_spec([b'{"a":', b'x"}'])
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    # Not at can_end yet: EOS refused.
    st, ids = assert_fastpath_equal(spec, schema, b'{"a":"')
    assert spec.eos_ids[0] not in ids
    # Document complete: EOS is the only allowed token class.
    st, ids = assert_fastpath_equal(spec, schema, b'{"a":"x"}')
    assert ids == {spec.eos_ids[0]}


# ---------------------------------------------------------------------------
# Unicode and escapes.
# ---------------------------------------------------------------------------

def test_unicode_codepoints_and_traps():
    e_acute = "é".encode()               # cp1
    cjk = "中".encode()                   # cp1
    emoji = "\U0001F600".encode()         # cp1 (4-byte)
    combining = "é".encode()    # e + U+0301, cp2
    truncated = b"\xc3"                   # mid-codepoint end
    overlong = b"\xc0\x80"
    surrogate = b"\xed\xa0\x80"
    too_high = b"\xf4\x90\x80\x80"        # > U+10FFFF
    spec = make_spec([e_acute, cjk, emoji, combining, truncated,
                      overlong, surrogate, too_high])
    E, CJK, EMOJI, COMB, TRUNC, OVER, SURR, HIGH = range(256, 264)
    schema = {"type": "string"}
    st, ids = assert_fastpath_equal(spec, schema, b'"')
    assert {E, CJK, EMOJI, COMB} <= ids
    # UTF-8 traps are refused by both paths (exact class, parse error).
    assert OVER not in ids and SURR not in ids and HIGH not in ids
    # A token ending mid-codepoint is consumed (the state carries rem>0);
    # it is exact-class, and both paths must agree on it.
    assert TRUNC in ids

    # cp counting: maxLength 1 admits é but not the combining pair.
    schema = {"type": "string", "maxLength": 1}
    st, ids = assert_fastpath_equal(spec, schema, b'"')
    assert E in ids and CJK in ids and EMOJI in ids and COMB not in ids


def test_escapes_split_across_token_boundaries():
    spec = make_spec([b"\\", b"\\u00", b"1f\"", b"n\"", b"\\n", b'\\"', b"ab\\"])
    BS, U00, HEX_CLOSE, N_CLOSE, ESC_N, ESC_QUOTE, AB_BS = range(256, 263)
    schema = {"type": "string"}

    st, ids = assert_fastpath_equal(spec, schema, b'"')
    assert BS in ids and U00 in ids and ESC_N in ids and ESC_QUOTE in ids
    assert AB_BS in ids                # content + escape start
    assert N_CLOSE in ids              # 'n' is content here, " closes
    assert HEX_CLOSE in ids            # '1','f' content, " closes

    # Mid-\uXXXX: only the matching hex continuation may proceed.
    st, ids = assert_fastpath_equal(spec, schema, b'"\\u00')
    assert HEX_CLOSE in ids            # 1f" completes the escape and closes
    assert N_CLOSE not in ids          # 'n' is not lowercase hex
    assert BS not in ids               # backslash is not hex
    assert ord('a') in ids             # 'a' is hex: content continuation

    # Mid-escape after "\: \" is the escaped quote (content, no string end).
    st, ids = assert_fastpath_equal(spec, schema, b'"\\')
    assert ESC_QUOTE in ids and ESC_N in ids
    assert ord('"') in ids              # \" is a valid escape continuation
    assert ord('x') not in ids          # \x is not in the escape table


def test_tokens_ending_mid_codepoint_refused_from_fast_class():
    # A truncated multi-byte token must never be decided by the lemma:
    # the on/off masks agree at every following state, including the state
    # that carries a pending UTF-8 continuation (non-uniform, exact path).
    spec = make_spec([b"\xc3", b"\xa9", b"\xc3\xa9"])
    schema = {"type": "string"}
    st, ids = assert_fastpath_equal(spec, schema, b'"\xc3')
    assert st == blg.BLG_OK
    assert 257 in ids                  # the continuation byte
    assert ord('"') not in ids         # cannot close mid-codepoint


# ---------------------------------------------------------------------------
# Kill switch.
# ---------------------------------------------------------------------------

def test_config_kill_switch_matches_default_and_env():
    # mask_fast_path=1 (explicit on) equals the default; =2 equals it too
    # semantically but must not build the tables (checked via work ops).
    spec = make_spec([b"ab", b"abc"])
    schema = {"type": "string"}
    _, _, ops_default = mask_at(spec, schema, b'"', mask_fast_path=0)
    _, _, ops_on = mask_at(spec, schema, b'"', mask_fast_path=blg.BLG_MASK_FAST_PATH_ON)
    _, _, ops_off = mask_at(spec, schema, b'"', mask_fast_path=blg.BLG_MASK_FAST_PATH_OFF)
    assert ops_default == ops_on
    assert ops_off > ops_on  # the plain walk charges the full trie DFS


_ENV_PROBE = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
import reference as ref
import blg_ctypes as blg
tokens = tuple(bytes([i]) for i in range(256)) + (b"ab", b"abc")
vocab = len(tokens) + 2
spec = ref.TokenizerSpec(tokens=tokens + (b"", b"<pad>"),
                         eos_ids=(vocab - 2,), special_ids=(vocab - 1,)).validate()
ctx = blg.Context(spec, mode=blg.BLG_MODE_ADAPTIVE,
                  cache_limit_bytes=blg.BLG_CACHE_DEFAULT,
                  work_limit_ops=1 << 50,
                  mask_fast_path=blg.BLG_MASK_FAST_PATH_ON)
g = blg.compile_schema(ctx, {"type": "string"})
s = blg.Session(ctx, g)
assert s.accept(ord('"')) == blg.BLG_OK
s0 = ctx.get_stats()
status, words = s.fill_mask_words()
s1 = ctx.get_stats()
print(status, s1.work_ops_total - s0.work_ops_total, " ".join(map(str, words)))
"""


def test_env_kill_switch():
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ)
    env["BLG_LIB_PATH"] = blg.LIB._path

    env["BLG_MASK_FAST_PATH"] = "0"
    out0 = subprocess.run([sys.executable, "-c", _ENV_PROBE, here],
                          capture_output=True, text=True, env=env, check=True)
    env["BLG_MASK_FAST_PATH"] = "1"
    out1 = subprocess.run([sys.executable, "-c", _ENV_PROBE, here],
                          capture_output=True, text=True, env=env, check=True)
    st0, ops0, mask0 = out0.stdout.split(" ", 2)
    st1, ops1, mask1 = out1.stdout.split(" ", 2)
    # Masks are bit-for-bit identical; the env var overrides the config's
    # explicit ON and forces the plain walk (more charged ops).
    assert st0 == st1 == str(blg.BLG_OK)
    assert mask0 == mask1
    assert int(ops0) > int(ops1)


# ---------------------------------------------------------------------------
# Normal-form mask cache (ADR-0007 D2+): every string-content position of a
# schema shares one cache entry when R >= r_cap, so the second position's
# fill is a cache hit; masks stay bit-for-bit identical to the plain walk.
# ---------------------------------------------------------------------------

def test_string_content_positions_share_one_cache_entry():
    spec = make_spec([b"bc", b"def", b"ghij"])
    for schema in ({"type": "string"},
                   {"type": "object", "properties": {"s": {"type": "string"}},
                    "required": ["s"], "additionalProperties": False}):
        prefix1 = b'{"s":"ab' if "properties" in schema else b'"ab'
        prefix2 = b'{"s":"abcdefgh' if "properties" in schema else b'"abcdefgh'
        ctx = blg.Context(spec, mode=blg.BLG_MODE_ADAPTIVE,
                          cache_limit_bytes=blg.BLG_CACHE_DEFAULT,
                          work_limit_ops=1 << 50)
        try:
            g = blg.compile_schema(ctx, schema)
            masks = []
            hits = []
            for prefix in (prefix1, prefix2):
                s = blg.Session(ctx, g)
                for b in prefix:
                    assert s.accept(b) == blg.BLG_OK
                s0 = ctx.get_stats()
                status, words = s.fill_mask_words()
                assert status == blg.BLG_OK
                s1 = ctx.get_stats()
                masks.append(words)
                hits.append(s1.cache_hits - s0.cache_hits)
                s.destroy()
            blg.grammar_release(g)
        finally:
            ctx.destroy()
        assert hits == [0, 1], (schema, hits)
        assert masks[0] == masks[1]
        # The shared mask equals the plain walk at both positions.
        for prefix in (prefix1, prefix2):
            st, words, _ = mask_at(spec, schema, prefix,
                                   mode=blg.BLG_MODE_ADAPTIVE,
                                   cache_limit_bytes=blg.BLG_CACHE_DEFAULT,
                                   mask_fast_path=blg.BLG_MASK_FAST_PATH_OFF)
            assert st == blg.BLG_OK
            assert words == masks[0]


def test_bounded_string_near_limit_stays_exact():
    """maxLength 4 with a token of 5+ bytes: R < r_cap near the limit, the
    normal form must not be used; the mask must still match the walk."""
    spec = make_spec([b"bcdefgh"])
    schema = {"type": "string", "maxLength": 4}
    for prefix in (b'"', b'"a', b'"abc', b'"abcd'):
        assert_fastpath_equal(spec, schema, prefix)
    st, ids = assert_fastpath_equal(spec, schema, b'"ab')
    assert st == blg.BLG_OK
    assert 256 not in ids  # 7 content bytes never fit the residual 2
