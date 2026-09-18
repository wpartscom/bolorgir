"""
Smoke-тесты Python-машинерии zig_constraints.

Тесты делятся на две группы:
- машинерия без нативных вызовов (иерархия исключений, валидация аргументов,
  упаковка TokenizerBundle) — проходят всегда;
- вызовы через C ABI (context/compile/session create-destroy, fill_mask,
  маппинг ошибок accept/finish) — проходят и со stub-ядром, и с настоящим.
  Единственное расхождение поведения stub — занулённая маска fill_mask —
  ветвится по ZG_CORE_STUB=1 (см. test_fill_mask_shape_and_batch).

Запуск: python3 -m pytest python/tests/test_api_smoke.py             (настоящее ядро)
        ZG_CORE_STUB=1 python3 -m pytest python/tests/test_api_smoke.py (stub)
"""

import os
import struct

import pytest

CORE_IS_STUB = os.environ.get("ZG_CORE_STUB") == "1"

import zig_constraints as zc
from zig_constraints import Engine, TokenizerBundle

EOS_ID = 256
VOCAB = 257


def mini_bundle() -> TokenizerBundle:
    tokens = [bytes([b]) for b in range(256)] + [b"<eos>"]
    return TokenizerBundle.from_token_bytes(tokens, eos_ids=[EOS_ID])


# ----------------------------------------------------------------------
# Машинерия без нативных вызовов
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
    from zig_constraints.tokenizers import _decode_byte_level

    # 'Ġ' — каноническая кодировка пробела в byte-level BPE
    assert _decode_byte_level("Ġhello") == b" hello"
    assert _decode_byte_level("ĠĠ") == b"  "
    # кириллица не входит в алфавит byte-level BPE
    assert _decode_byte_level("привет") is None


def test_allowed_token_ids_parsing():
    with Engine(mode="lazy") as engine:
        engine.compile_literals(["x"], tokenizer=mini_bundle())
        assert engine.vocab_size == VOCAB
        assert engine.mask_words == (VOCAB + 31) // 32


# ----------------------------------------------------------------------
# Вызовы через C ABI (проходят и со stub-ядром, и с настоящим)
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
            assert s1.allowed_token_ids(mask) == []  # stub зануляет маску
        else:
            # literal "a": на старте разрешён только байт 'a'
            # (совпадает с oracle tests/reference.py)
            assert s1.allowed_token_ids(mask) == [ord("a")]
        masks = zc.fill_masks_batch([s1, s2])
        assert len(masks) == 2 and all(len(m) == len(mask) for m in masks)
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
