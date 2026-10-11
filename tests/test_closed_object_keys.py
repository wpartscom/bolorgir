"""A closed object reads a key on one parser thread: every declared
property still possible for the key bytes read shares it
(parser.nextKeyCandidate)."""

from __future__ import annotations

import pytest

import reference as ref
import blg_ctypes as blg


def byte_spec() -> ref.TokenizerSpec:
    tokens = tuple(bytes([i]) for i in range(256))
    return ref.TokenizerSpec(tokens=tokens + (b"",), eos_ids=(256,),
                             special_ids=()).validate()


def allowed(words, token_id: int) -> bool:
    return bool(words[token_id // 32] >> (token_id % 32) & 1)


def feed(schema, doc: bytes, profile: bytes, **ctx_kw) -> list[set[int]]:
    """Feed doc byte by byte; -> the allowed bytes before each byte and at
    the end (EOS as 256). Every mask must succeed and allow the next byte."""
    spec = byte_spec()
    ctx = blg.Context(spec, **ctx_kw)
    try:
        g = blg.compile_schema(ctx, schema, profile=profile)
        s = blg.Session(ctx, g)
        masks = []
        for b in doc + b"\0":
            status, words = s.fill_mask_words()
            assert status == blg.BLG_OK
            masks.append({i for i in range(257) if allowed(words, i)})
            if b:
                assert b in masks[-1], (doc, b)
                assert s.accept(b) == blg.BLG_OK
        assert 256 in masks[-1]
        s.destroy()
        blg.grammar_release(g)
        return masks
    finally:
        ctx.destroy()


@pytest.mark.parametrize("profile", [b"canonical-v1", b"spec-v1"])
def test_wide_closed_object_within_a_small_thread_cap(profile):
    # One thread per candidate property exceeded max_threads_per_state
    # right after '{' once more optional properties than the cap preceded
    # the first required one (RESOURCE_LIMIT at the first mask).
    schema = {"type": "object",
              "properties": {f"p{i}": {"type": "integer"} for i in range(300)},
              "required": [], "additionalProperties": False}
    masks = feed(schema, b'{"p1":1,"p10":2,"p100":3,"p299":4}', profile,
                 max_threads_per_state=8)
    after_p1 = masks[len(b'{"p1')]
    assert after_p1 == {ord('"')} | set(b"0123456789")


def test_first_candidate_with_an_empty_value_does_not_hide_the_next():
    # After '{"a' both "ab" and "ac" are possible; "ab" comes first and its
    # value language is empty (oneOf of equal branches), "ac" keeps the
    # object alive.
    schema = {"type": "object",
              "properties": {"ab": {"oneOf": [{"type": "string"}, {"type": "string"}]},
                             "ac": {"type": "string"}},
              "additionalProperties": False}
    masks = feed(schema, b'{"ac":"x"}', b"spec-v1")
    assert masks[len(b'{"')] == {ord("a")}
    assert masks[len(b'{"a')] == {ord("c")}
