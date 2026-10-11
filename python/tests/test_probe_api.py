"""Python surface of the compile-time probe (ADR-0012)."""

import os

import pytest

import bolorgir as zc
from bolorgir import Engine, TokenizerBundle

CORE_IS_STUB = os.environ.get("BLG_CORE_STUB") == "1"
pytestmark = pytest.mark.skipif(CORE_IS_STUB, reason="stub core has no probe")


def bundle():
    tokens = [bytes([b]) for b in range(256)] + [b"<eos>"]
    return TokenizerBundle.from_token_bytes(tokens, eos_ids=[256])


BOOL_OBJECT = {"type": "object", "properties": {"x": {"type": "boolean"}},
               "required": ["x"], "additionalProperties": False}


def test_probe_status_of_a_finite_grammar():
    with Engine(mode="lazy", tokenizer=bundle()) as engine:
        plain = engine.compile(BOOL_OBJECT, profile="spec-v1")
        assert plain.probe_status == "not_run"
        probed = engine.compile(BOOL_OBJECT, profile="spec-v1", probe="strict")
        assert probed.probe_status == "proven"
        plain.close()
        probed.close()


def test_probe_budget_and_strict_mode():
    schema = {"type": "string", "pattern": "^a"}
    with Engine(mode="lazy", tokenizer=bundle(), probe_max_states=3) as engine:
        c = engine.compile(schema, profile="spec-v1", probe="probe")
        assert c.probe_status == "inconclusive"
        c.close()
        with pytest.raises(zc.ResourceLimitError, match="probe inconclusive"):
            engine.compile(schema, profile="spec-v1", probe="strict")
        with pytest.raises(zc.InvalidArgumentError):
            engine.compile(schema, profile="spec-v1", probe="yes")
