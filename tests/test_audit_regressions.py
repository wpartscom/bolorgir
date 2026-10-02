"""Serializer -> engine composition regressions (2026-09-22).

The retired audit reproduction script combined the value-preserving
serializer with the engine in one check: an out-of-order instance of the
merged-allOf schema (contradictory declared key orders across branches)
must serialize to the first-appearance order, and that payload
must complete under the engine. The serializer-only half is pinned in
tests/test_serializer.py (test_allof_merged_first_appearance_order) and
the engine-only half in tests/test_spec_v1_r2_r3.py
(test_r3_allof_contradictory_key_orders_merge); this module pins the
composition across lazy/adaptive x fast path on/off, verdict-checked
against the independent jsonschema validator.

The module is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, "python"))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")
jsonschema = pytest.importorskip("jsonschema", reason="independent oracle")

from bolorgir.serializer import serialize_for_schema  # noqa: E402

MERGED_ALLOF = {"allOf": [
    {"type": "object", "properties": {"a": {}, "b": {}},
     "required": ["a", "b"]},
    {"type": "object", "properties": {"b": {}, "a": {}},
     "required": ["a", "b"]}]}


@pytest.fixture(params=[(zg.BLG_MODE_LAZY, zg.BLG_MASK_FAST_PATH_ON),
                        (zg.BLG_MODE_LAZY, zg.BLG_MASK_FAST_PATH_OFF),
                        (zg.BLG_MODE_ADAPTIVE, zg.BLG_MASK_FAST_PATH_ON),
                        (zg.BLG_MODE_ADAPTIVE, zg.BLG_MASK_FAST_PATH_OFF)],
                ids=["lazy+fast", "lazy", "adaptive+fast", "adaptive"])
def ctx(request, byte_tok):
    mode, fast = request.param
    c = zg.Context(byte_tok, mode, mask_fast_path=fast)
    yield c
    assert c.destroy() == zg.BLG_OK


def test_r3_allof_serialized_payload_completes(ctx):
    # The instance arrives out of order; the serializer must canonicalize
    # it to the merged first-appearance order a,b without losing values,
    # and the engine must accept the resulting byte stream.
    payload, status = serialize_for_schema('{"b":2,"a":1}', MERGED_ALLOF)
    assert status.ok, f"unexpected refusal: {status}"
    assert payload == b'{"a":1,"b":2}'
    assert jsonschema.Draft202012Validator(MERGED_ALLOF).is_valid(
        json.loads(payload))
    g = zg.compile_schema(ctx, json.dumps(MERGED_ALLOF).encode(),
                          profile=b"spec-v1")
    s = zg.Session(ctx, g)
    try:
        for i, b in enumerate(payload):
            ids = s.fill_mask_ids()
            assert b in ids, f"payload byte {bytes([b])!r}@{i} not in mask"
            assert s.accept(b) == zg.BLG_OK, f"accept failed at byte {i}"
        assert s.can_end()
        assert s.finish() == zg.BLG_OK
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_r3_allof_raw_out_of_order_stream_rejected(ctx):
    # The byte language admits only the merged order: feeding the raw
    # out-of-order serialization must not complete, while the value stays
    # valid per the independent oracle (order is a wire concern).
    assert jsonschema.Draft202012Validator(MERGED_ALLOF).is_valid(
        json.loads('{"b":2,"a":1}'))
    g = zg.compile_schema(ctx, json.dumps(MERGED_ALLOF).encode(),
                          profile=b"spec-v1")
    s = zg.Session(ctx, g)
    try:
        completed = True
        for b in b'{"b":2,"a":1}':
            try:
                allowed = s.fill_mask_ids()
            except zg.CoreFailure as exc:
                assert exc.status == zg.BLG_ERR_DEAD_END
                completed = False
                break
            if b not in allowed or s.accept(b) != zg.BLG_OK:
                completed = False
                break
        assert not (completed and s.can_end() and s.finish() == zg.BLG_OK)
    finally:
        s.destroy()
        zg.grammar_release(g)
