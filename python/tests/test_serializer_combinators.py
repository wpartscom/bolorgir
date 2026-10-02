"""
Serializer key order under oneOf/anyOf combinators (spec-v1 profile).

Regression: the value-preserving serializer used to keep the Python
object's key order under oneOf/anyOf, emitting payloads the spec-v1
engine rejects ("byte N not in mask") because the byte language fixes
the key order to the `properties` declaration order of the branch the
value belongs to. The serializer now picks the structurally compatible
branch (single fit for oneOf, first fit for anyOf) and orders keys by
that branch; ambiguous or unmatched combinations keep the data order.

The engine-side test goes through tests/blg_ctypes.py against the real
core in zig-out/lib (the same driver as the acceptance repro); it needs
a built core, so it is skipped when zig-out/lib/libbolorgir.so is
missing. The byte-output tests are pure stdlib and always run.

Run: python3 -m pytest python/tests/test_serializer_combinators.py
"""

import json
import os
import sys

import pytest

from bolorgir.serializer import serialize_value

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "tests"))

_CORE_AVAILABLE = os.path.exists(
    os.path.join(_REPO_ROOT, "zig-out", "lib", "libbolorgir.so"))

ONE_OF_SCHEMA = {
    "oneOf": [
        {"properties": {"id": {}, "params": {}}},
        {"type": "array"},
    ]
}


def serialize(value, schema) -> bytes:
    payload, status = serialize_value(value, schema)
    assert status.ok, f"unexpected refusal: {status}"
    assert isinstance(payload, bytes)
    return payload


# ----------------------------------------------------------------------
# Byte output of the serializer (no native calls)
# ----------------------------------------------------------------------

def test_oneof_branch_properties_order():
    # Data order differs from the declaration order of the only fitting
    # branch: the payload follows the branch, values stay untouched.
    assert serialize({"params": 1, "id": 1}, ONE_OF_SCHEMA) == \
        b'{"id":1,"params":1}'
    # A value of the non-object branch is unaffected.
    assert serialize([2, 1], ONE_OF_SCHEMA) == b"[2,1]"


def test_oneof_branch_undeclared_keys_after_declared():
    # Undeclared keys of an open branch keep their data order after the
    # declared ones, exactly like plain object schemas (section 4.3).
    assert serialize(
        {"extra2": 0, "params": 1, "extra1": 0, "id": 2}, ONE_OF_SCHEMA
    ) == b'{"id":2,"params":1,"extra2":0,"extra1":0}'


def test_oneof_string_value_not_reencoded():
    # Regression guard for the earlier "1" -> 1 defect: a str is a JSON
    # string value, kept byte-exact under a selected branch.
    schema = {"oneOf": [
        {"properties": {"id": {}, "params": {"type": "string"}}},
        {"type": "array"},
    ]}
    assert serialize({"params": "1", "id": 1}, schema) == \
        b'{"id":1,"params":"1"}'


def test_oneof_ambiguous_fit_keeps_data_order():
    # Both branches fit: no order is ever guessed.
    schema = {"oneOf": [
        {"properties": {"a": {}, "b": {}}},
        {"properties": {"b": {}, "a": {}}},
    ]}
    assert serialize({"b": 1, "a": 2}, schema) == b'{"b":1,"a":2}'


def test_anyof_first_fitting_branch_orders():
    schema = {"anyOf": [
        {"properties": {"x": {}, "y": {}}},
        {"properties": {"y": {}, "x": {}}},
    ]}
    assert serialize({"y": 1, "x": 2}, schema) == b'{"x":2,"y":1}'


def test_allof_nested_conjunction_flattens():
    # Regression: properties living in the allOf of an allOf member (a
    # $ref'd "common" base with its own allOf) were invisible, so the
    # serializer left such keys in data order. They join the merged
    # order; the member's own `properties` count as its first branch,
    # recursively, so `name`/`namespace` precede the nested branch keys.
    schema = {
        "allOf": [
            {"$ref": "#/$defs/common"},
            {"properties": {"specific": {}}},
        ],
        "$defs": {
            "common": {
                "allOf": [
                    {"properties": {"pip_url": {}, "executable": {}}},
                ],
                "properties": {"name": {}, "namespace": {}},
            },
        },
    }
    assert serialize(
        {"specific": 1, "name": 2, "pip_url": 3, "namespace": 4,
         "executable": 5}, schema
    ) == b'{"name":2,"namespace":4,"pip_url":3,"executable":5,"specific":1}'


def test_allof_sibling_properties_yield_to_branch_order():
    # Regression (Github o58926): own `properties` next to `allOf` count
    # as the first branch for disjoint keys, but a key shared with an
    # allOf subschema must keep the subschema's relative order - the
    # engine rejects a shared key emitted against it. The merged order
    # is the linearization consistent with every conjunct.
    schema = {
        "allOf": [{"$ref": "#/definitions/assembly"}],
        "properties": {"options": {"type": "object"}},
        "definitions": {
            "assembly": {
                "type": "object",
                "properties": {"priority": {"type": "number"},
                               "options": {"type": "object"}},
            },
        },
    }
    assert serialize({"options": {}, "priority": 1}, schema) == \
        b'{"priority":1,"options":{}}'


def test_allof_disjoint_keys_keep_first_appearance_order():
    # Without shared keys the first-appearance order is already a valid
    # linearization and stays untouched.
    schema = {
        "properties": {"a": {}, "b": {}},
        "allOf": [{"properties": {"c": {}, "d": {}}}],
    }
    assert serialize({"d": 1, "a": 2, "c": 3, "b": 4}, schema) == \
        b'{"a":2,"b":4,"c":3,"d":1}'


# ----------------------------------------------------------------------
# Engine acceptance of the serialized payload (real core)
# ----------------------------------------------------------------------

@pytest.mark.skipif(not _CORE_AVAILABLE,
                    reason="zig-out/lib/libbolorgir.so is not built")
def test_oneof_reordered_payload_accepted_by_engine():
    import blg_ctypes as blg
    from reference import TokenizerSpec

    spec = TokenizerSpec(
        tokens=tuple(bytes([i]) for i in range(256)) + (b"", b""),
        eos_ids=(256,), special_ids=(257,)).validate()
    value = {"params": 1, "id": 1}
    payload = serialize(value, ONE_OF_SCHEMA)
    assert payload == b'{"id":1,"params":1}'
    assert json.loads(payload) == value  # value preserved

    ctx = blg.Context(spec, mode=0, cache_limit_bytes=0)
    grammar = blg.compile_schema(ctx, ONE_OF_SCHEMA, profile=b"spec-v1")
    try:
        for doc in (payload, b'{"id":1,"params":1}'):
            session = blg.Session(ctx, grammar)
            try:
                for i, b in enumerate(doc):
                    status, words = session.fill_mask_words()
                    assert status == blg.BLG_OK
                    assert (words[b // 32] >> (b % 32)) & 1, \
                        f"byte {i} (0x{b:02x}) not in mask"
                    assert session.accept(b) == blg.BLG_OK
                assert session.can_end()
                assert session.finish() == blg.BLG_OK
            finally:
                session.destroy()
    finally:
        blg.grammar_release(grammar)
        ctx.destroy()
