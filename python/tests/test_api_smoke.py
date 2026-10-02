"""
Smoke tests for the bolorgir Python machinery.

The tests fall into two groups:
- machinery without native calls (exception hierarchy, argument validation,
  TokenizerBundle packing) - always pass;
- calls through the C ABI (context/compile/session create-destroy, fill_mask,
  accept/finish error mapping) - pass with both a stub core and the real one.
  The only stub behavior difference - a zeroed fill_mask mask - branches on
  BLG_CORE_STUB=1 (see test_fill_mask_shape_and_batch).

Run: python3 -m pytest python/tests/test_api_smoke.py             (real core)
     BLG_CORE_STUB=1 python3 -m pytest python/tests/test_api_smoke.py (stub)
"""

import os
import struct

import pytest

CORE_IS_STUB = os.environ.get("BLG_CORE_STUB") == "1"

import bolorgir as zc
from bolorgir import Engine, TokenizerBundle

EOS_ID = 256
VOCAB = 257


def mini_bundle() -> TokenizerBundle:
    tokens = [bytes([b]) for b in range(256)] + [b"<eos>"]
    return TokenizerBundle.from_token_bytes(tokens, eos_ids=[EOS_ID])


# ----------------------------------------------------------------------
# Machinery without native calls
# ----------------------------------------------------------------------

def test_exception_hierarchy():
    for name in (
        "InvalidArgumentError",
        "InvalidSchemaError",
        "UnsupportedFeatureError",
        "UnsatisfiableConstraintError",
        "UnsupportedTokenizerError",
        "InvalidTokenError",
        "DeadEndError",
        "ResourceLimitError",
        "CancelledError",
        "BusyError",
        "WrongStateError",
        "BufferTooSmallError",
        "InternalError",
        "UnsupportedModeError",
    ):
        exc = getattr(zc, name)
        assert issubclass(exc, zc.ZigConstraintsError)
        assert issubclass(exc, Exception)


def test_import_without_torch_numpy():
    import sys

    assert "torch" not in sys.modules
    assert "transformers" not in sys.modules


def test_engine_mode_validation():
    with pytest.raises(zc.InvalidArgumentError):
        Engine(mode="turbo")
    with pytest.raises(zc.InvalidArgumentError):
        Engine(memory_limit_mb=-1)


def test_compile_requires_tokenizer():
    with Engine(mode="lazy") as engine:
        with pytest.raises(zc.InvalidArgumentError):
            engine.compile({"type": "integer"})


def test_engine_eos_ids_memoizes_bundle(monkeypatch):
    # Regression (e2e TTFT): Engine.eos_ids used to rebuild
    # the tokenizer bundle on every access (~178 ms for Qwen); the EOS-subset
    # check in constrained_generate reads it once per batch row, which turned into
    # batch*~180 ms of pure overhead before generate.
    calls = {"n": 0}
    orig = zc._coerce_bundle

    def counting(tok):
        calls["n"] += 1
        return orig(tok)

    monkeypatch.setattr(zc, "_coerce_bundle", counting)
    with Engine(mode="lazy", tokenizer=mini_bundle()) as engine:
        first = engine.eos_ids
        for _ in range(5):
            assert engine.eos_ids == first
    assert calls["n"] == 1  # built once at Engine init, never on property read


def test_compile_schema_type_validation():
    with Engine(mode="lazy") as engine:
        with pytest.raises(zc.InvalidArgumentError):
            engine.compile(42, tokenizer=mini_bundle())
    with Engine(mode="lazy") as engine:
        with pytest.raises(zc.InvalidArgumentError):
            engine.compile_literals([], tokenizer=mini_bundle())
    with Engine(mode="lazy") as engine:
        with pytest.raises(zc.InvalidArgumentError):
            engine.compile_literals(["ok", 5], tokenizer=mini_bundle())


def test_bundle_validation():
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_token_bytes([b"a", b""], eos_ids=[])
    with pytest.raises(zc.InvalidArgumentError):
        TokenizerBundle.from_token_bytes([b"a"], eos_ids=[3])
    with pytest.raises(zc.InvalidArgumentError):
        TokenizerBundle.from_token_bytes([b"a", b"b"], eos_ids=[1], special_ids=[1])


def test_bundle_core_kwargs_layout():
    bundle = TokenizerBundle.from_token_bytes(
        [b"ab", b"c", b"<eos>"], eos_ids=[2], special_ids=[]
    )
    kw = bundle.to_core_kwargs()
    assert kw["vocab_size"] == 3
    assert kw["token_blob"] == b"abc<eos>"
    assert kw["eos_ids"] == [2]
    entry = struct.Struct("<IIQQ")
    assert len(kw["token_index"]) == 3 * entry.size
    id0, _, off0, len0 = entry.unpack_from(kw["token_index"], 0)
    id2, _, off2, len2 = entry.unpack_from(kw["token_index"], 2 * entry.size)
    assert (id0, off0, len0) == (0, 0, 2)
    assert (id2, off2, len2) == (2, 3, 5)
    assert bundle.mask_words == 1


def test_byte_level_decode():
    from bolorgir.tokenizers import _decode_byte_level

    # 'Ġ' is the canonical space encoding in byte-level BPE
    assert _decode_byte_level("Ġhello") == b" hello"
    assert _decode_byte_level("ĠĠ") == b"  "
    # Chars outside the byte alphabet come through as UTF-8
    # (empirical: decoders.ByteLevel), so CJK is literal bytes, not a
    # rejection: the added token "日本語" decodes to "日本語".
    assert _decode_byte_level("日本語") == "日本語".encode("utf-8")
    assert _decode_byte_level("Ċ") == b"\n"
    # The fallback on a NON-mappable char happens at the
    # whole-token level (byte_level.rs: unwrap_or_else(t.as_bytes())), not
    # per element: 'Ġhello🙂' stays as-is, including 'Ġ'.
    assert _decode_byte_level("Ġhello🙂") == "Ġhello🙂".encode("utf-8")
    assert _decode_byte_level("Ġ日本語") == "Ġ日本語".encode("utf-8")
    assert _decode_byte_level("Ġ🙂") == "Ġ🙂".encode("utf-8")
    assert _decode_byte_level("aĠb") == b"a b"  # pure alphabet - the table


def test_engine_close_busy_with_live_constraint():
    # engine.close() with a live Constraint must raise
    # BusyError (previously it passed in adaptive mode with a cache, and a
    # subsequent constraint.close() crashed with SIGSEGV); after the
    # references are dropped, close succeeds.
    bundle = TokenizerBundle.from_token_bytes([b"a", b"<eos>"], eos_ids=[1])
    engine = Engine(mode="adaptive", tokenizer=bundle)
    c = engine.compile_literals(["a"])
    with pytest.raises(zc.BusyError):
        engine.close()
    c.close()
    engine.close()
    engine.close()  # repeated close is a no-op


def test_partial_vocab_infinite_language_refused():
    # The string counterexample - vocab ["\"\"", "\"a", EOS] - gives
    # a dead-end prefix `"a` with an infinite {"type": "string"} language;
    # such a schema/tokenizer pair is rejected before generation. Control:
    # the same vocab with a finite literal constraint is accepted.
    bundle = TokenizerBundle.from_token_bytes([b'""', b'"a', b"<eos>"], eos_ids=[2])
    with Engine(mode="lazy", tokenizer=bundle) as engine:
        with pytest.raises(zc.UnsupportedTokenizerError) as exc:
            engine.compile({"type": "string"})
        assert "partial byte coverage" in str(exc.value)
        # a finite language remains available (exact completion filter)
        c = engine.compile_literals(['""'])
        c.close()
    # A one-item enum array is also infinite for the filter (repeat) - rejected.
    arr = TokenizerBundle.from_token_bytes(
        [b"[", b"]", b"ab", b"a", b'"', b"<eos>"], eos_ids=[5]
    )
    with Engine(mode="lazy", tokenizer=arr) as engine:
        with pytest.raises(zc.UnsupportedTokenizerError):
            engine.compile(
                {"type": "array", "items": {"enum": ["ab"]}, "minItems": 1, "maxItems": 1}
            )
    # a byte-complete vocab with the same infinite schema compiles.
    full = TokenizerBundle.from_token_bytes(
        [bytes([b]) for b in range(256)] + [b"<eos>"], eos_ids=[256]
    )
    with Engine(mode="lazy", tokenizer=full) as engine:
        c = engine.compile({"type": "string"})
        c.close()


def test_allowed_token_ids_parsing():
    with Engine(mode="lazy") as engine:
        engine.compile_literals(["x"], tokenizer=mini_bundle())
        assert engine.vocab_size == VOCAB
        assert engine.mask_words == (VOCAB + 31) // 32


# ----------------------------------------------------------------------
# Calls through the C ABI (pass with a stub core and the real one)
# ----------------------------------------------------------------------

def test_context_compile_session_lifecycle():
    with Engine(mode="adaptive") as engine:
        constraint = engine.compile(
            {"type": "object", "properties": {}, "required": [],
             "additionalProperties": False},
            tokenizer=mini_bundle(),
        )
        assert engine.vocab_size == VOCAB
        with constraint.create_session() as session:
            assert session.mask_words == (VOCAB + 31) // 32
            assert session.can_end() is False
        stats = engine.stats()
        assert "mem_used" in stats and "total" in stats["mem_used"]
        constraint.close()


def test_fill_mask_shape_and_batch():
    with Engine(mode="lazy") as engine:
        c1 = engine.compile_literals(["a"], tokenizer=mini_bundle())
        c2 = engine.compile({"type": "integer"})
        s1, s2 = c1.create_session(), c2.create_session()
        mask = s1.fill_mask()
        assert isinstance(mask, bytes)
        assert len(mask) == s1.mask_words * 4
        if CORE_IS_STUB:
            assert s1.allowed_token_ids(mask) == []  # stub zeroes the mask
        else:
            # literal "a": only byte 'a' is allowed at start
            # (matches the oracle tests/reference.py)
            assert s1.allowed_token_ids(mask) == [ord("a")]
        masks = zc.fill_masks_batch([s1, s2])
        assert len(masks) == 2 and all(len(m) == len(mask) for m in masks)
        assert zc.fill_masks_batch([]) == []
        s1.close()
        s2.close()
        c1.close()
        c2.close()


# ----------------------------------------------------------------------
# NFR-2: cancellation API and the work/adaptive budgets
# ----------------------------------------------------------------------

@pytest.mark.skipif(CORE_IS_STUB, reason="stub core does not compute masks")
def test_cancel_token_wins_even_on_cache_hit():
    with Engine(mode="adaptive", tokenizer=mini_bundle()) as engine:
        token = engine.cancel_token()
        with engine.compile_literals(["a"]) as constraint:
            with constraint.create_session() as session:
                first = session.allowed_token_ids()
                assert first == [ord("a")]
                session.allowed_token_ids()  # same state: the mask may come from the cache
                token.cancel()
                assert token.cancelled
                with pytest.raises(zc.CancelledError):
                    session.allowed_token_ids()
        engine.clear_cancel_tokens()
        with engine.compile_literals(["a"]) as constraint:
            with constraint.create_session() as session:
                assert session.allowed_token_ids() == [ord("a")]


@pytest.mark.skipif(CORE_IS_STUB, reason="stub core does not compute masks")
def test_cancelled_compile_fails_before_generation():
    with Engine(mode="lazy", tokenizer=mini_bundle()) as engine:
        token = engine.cancel_token()
        token.cancel()
        with pytest.raises(zc.CancelledError):
            engine.compile({"type": "boolean"})
        engine.clear_cancel_tokens()
        with engine.compile({"type": "boolean"}) as constraint:
            assert constraint is not None


def test_engine_accepts_work_and_adaptive_budgets():
    with Engine(
        mode="precompute",
        tokenizer=mini_bundle(),
        work_limit_ops=10**9,
        adaptive_min_hits=1,
        adaptive_min_cost_ns=1,
        precompute_max_states=64,
    ) as engine:
        if not CORE_IS_STUB:
            with engine.compile_literals(["ab"]) as constraint:
                with constraint.create_session() as session:
                    assert session.allowed_token_ids()
    for bad in (
        {"work_limit_ops": -1},
        {"adaptive_min_hits": -1},
        {"adaptive_min_cost_ns": -1},
        {"precompute_max_states": -1},
    ):
        with pytest.raises(zc.InvalidArgumentError):
            Engine(**bad)
    with pytest.raises(zc.InvalidArgumentError):
        Engine().register_cancel_token(object())
        assert zc.fill_masks_batch([]) == []
        s1.close()
        s2.close()
        c1.close()
        c2.close()


def test_error_mapping_invalid_token_and_wrong_state():
    with Engine(mode="lazy") as engine:
        constraint = engine.compile_literals(["a"], tokenizer=mini_bundle())
        session = constraint.create_session()
        with pytest.raises(zc.InvalidTokenError):
            session.accept_token(0)
        with pytest.raises(zc.WrongStateError):
            session.finish()
        session.close()
        with pytest.raises(zc.WrongStateError):
            session.fill_mask()
        constraint.close()


def test_token_id_validation():
    with Engine(mode="lazy") as engine:
        constraint = engine.compile_literals(["a"], tokenizer=mini_bundle())
        session = constraint.create_session()
        with pytest.raises((OverflowError, TypeError)):
            session.accept_token(-1)
        with pytest.raises(OverflowError):
            session.accept_token(2**40)
        session.close()
        constraint.close()


def test_constraint_and_session_context_managers():
    with Engine(mode="lazy") as engine:
        with engine.compile_literals(["a"], tokenizer=mini_bundle()) as c:
            with c.create_session() as s:
                s.abort()
            with pytest.raises(zc.WrongStateError):
                s.fill_mask()
        with pytest.raises(zc.WrongStateError):
            c.create_session()
