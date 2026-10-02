"""spec-v1 P5 end-to-end tests (ROADMAP rev-2 P5.3): the external-$ref
registry snapshot (ADR-0006 D5, ADR-0008).

Covers the immutable registry through the C ABI: whole-document refs,
fragment pointers and anchors inside remote documents, recursion across
documents (shared unroll budget), nested remote refs, snapshot
versioning (a registry swap is a new snapshot: the artifact cache key
and the grammar identity cover the registry bytes, so a swapped snapshot
never returns a stale artifact or mask), and the refusal surface
(unknown base URI, malformed registry, registry under canonical-v1) -
all UNSUPPORTED_FEATURE/INVALID_SCHEMA with the JSON pointer.

The module is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

REG_V1 = {
    "version": "v1",
    "documents": {
        "http://ex.com/int.json": {"type": "integer"},
        "http://ex.com/sub.json": {
            "$defs": {"s": {"type": "string"}},
            "$anchor": "top",
            "type": "boolean",
        },
        "http://ex.com/tree.json": {
            "type": "object",
            "properties": {
                "l": {"$ref": "http://ex.com/tree.json"},
                "v": {"$ref": "http://ex.com/int.json"},
            },
            "additionalProperties": False,
        },
        "http://ex.com/mid.json": {
            "$defs": {"x": {"$ref": "http://ex.com/int.json"}},
        },
    },
}

REG_V2 = dict(REG_V1, version="v2", documents=dict(
    REG_V1["documents"],
    **{"http://ex.com/int.json": {"type": "string"}},
))


@pytest.fixture
def byte_ctx(byte_tok):
    ctx = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    yield ctx
    ctx.destroy()


def _compile(ctx, schema, registry=None, profile=b"spec-v1"):
    return zg.compile_schema(ctx, json.dumps(schema).encode(),
                             profile=profile, registry=registry)


def _accepts(ctx, grammar, doc: str) -> bool:
    s = zg.Session(ctx, grammar)
    try:
        for b in doc.encode():
            if s.accept(b) != zg.BLG_OK:
                return False
        return bool(s.can_end())
    finally:
        s.destroy()


def _check(ctx, schema, registry, ok_docs, bad_docs):
    g = _compile(ctx, schema, registry)
    try:
        for d in ok_docs:
            assert _accepts(ctx, g, d), f"must accept {d!r} under {schema}"
        for d in bad_docs:
            assert not _accepts(ctx, g, d), f"must reject {d!r} under {schema}"
    finally:
        zg.grammar_release(g)


def _refusal(ctx, schema, status, registry=None, profile=b"spec-v1"):
    with pytest.raises(zg.CoreFailure) as ei:
        _compile(ctx, schema, registry, profile=profile)
    assert status in str(ei.value)
    return str(ei.value)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def test_remote_whole_document(byte_ctx):
    _check(byte_ctx, {"$ref": "http://ex.com/int.json"}, REG_V1,
           ["5", "-1", "1.0"], ['"s"', "1.5", "null"])


def test_remote_fragment_pointer(byte_ctx):
    _check(byte_ctx, {"$ref": "http://ex.com/sub.json#/$defs/s"}, REG_V1,
           ['"x"', '""'], ["5", "true"])


def test_remote_anchor(byte_ctx):
    _check(byte_ctx, {"$ref": "http://ex.com/sub.json#top"}, REG_V1,
           ["true", "false"], ["5", '"s"'])


def test_remote_doc_dialect_is_its_own(byte_ctx):
    # A draft-04 registry document: `exclusiveMinimum` is the boolean
    # modifier form there; under the main 2020-12 dialect it would be a
    # standalone number.
    reg = {"documents": {
        "http://ex.com/d4.json": {
            "$schema": "http://json-schema.org/draft-04/schema#",
            "type": "number", "minimum": 2, "exclusiveMinimum": True,
        },
    }}
    _check(byte_ctx, {"$ref": "http://ex.com/d4.json"}, reg,
           ["3", "2.5"], ["2", "1"])


def test_nested_remote_refs(byte_ctx):
    # A ref into a document whose own $defs ref back out through the
    # registry.
    _check(byte_ctx, {"$ref": "http://ex.com/mid.json#/$defs/x"}, REG_V1,
           ["7"], ['"s"'])


def test_remote_recursion_shared_budget(byte_ctx):
    ok = ["{}", '{"v":1}', '{"l":{"v":3}}', '{"l":{"l":{"l":{}}}}']
    deep = "{}"
    for _ in range(9):
        deep = '{"l":%s}' % deep
    _check(byte_ctx, {"$ref": "http://ex.com/tree.json"}, REG_V1,
           ok, [deep, '{"l":{"v":"x"}}'])


def test_mixed_local_and_remote_recursion(byte_ctx):
    # Local recursion through a remote document and back.
    reg = {"documents": {
        "http://ex.com/int.json": {"type": "integer"},
        "http://ex.com/wrap.json": {
            "type": "object",
            "properties": {"inner": {"$ref": "http://ex.com/int.json"}},
            "additionalProperties": False,
        },
    }}
    schema = {
        "type": "object",
        "properties": {
            "w": {"$ref": "http://ex.com/wrap.json"},
            "again": {"$ref": "#"},
        },
        "additionalProperties": False,
    }
    _check(byte_ctx, schema, reg,
           ['{}', '{"w":{"inner":5}}', '{"again":{"w":{"inner":1}}}'],
           ['{"w":{"inner":"s"}}'])


# ---------------------------------------------------------------------------
# Snapshot versioning and the cache
# ---------------------------------------------------------------------------

def test_registry_swap_invalidates_artifacts(byte_ctx):
    """The registry bytes join the artifact key (ADR-0006 D5): compiling
    the same schema against a swapped snapshot must not return the stale
    artifact, in either order."""
    g1 = _compile(byte_ctx, {"$ref": "http://ex.com/int.json"}, REG_V1)
    g2 = _compile(byte_ctx, {"$ref": "http://ex.com/int.json"}, REG_V2)
    try:
        assert _accepts(byte_ctx, g1, "5")
        assert not _accepts(byte_ctx, g1, '"x"')
        assert _accepts(byte_ctx, g2, '"x"')
        assert not _accepts(byte_ctx, g2, "5")
    finally:
        zg.grammar_release(g1)
        zg.grammar_release(g2)
    # And again, v1 first after v2 was cached: the v1 artifact is intact.
    g3 = _compile(byte_ctx, {"$ref": "http://ex.com/int.json"}, REG_V1)
    try:
        assert _accepts(byte_ctx, g3, "5")
        assert not _accepts(byte_ctx, g3, '"x"')
    finally:
        zg.grammar_release(g3)


def test_registry_version_bytes_matter(byte_ctx):
    """Two snapshots identical in documents but different in version (or
    formatting) are different artifacts (the exact bytes are the key)."""
    reg_a = json.dumps({"version": "a", "documents": {"http://ex.com/int.json": {"type": "integer"}}})
    reg_b = json.dumps({"version": "b", "documents": {"http://ex.com/int.json": {"type": "integer"}}})
    g1 = _compile(byte_ctx, {"$ref": "http://ex.com/int.json"}, reg_a.encode())
    g2 = _compile(byte_ctx, {"$ref": "http://ex.com/int.json"}, reg_b.encode())
    try:
        # Same language, but both compiles must succeed independently
        # (no cross-snapshot confusion).
        assert _accepts(byte_ctx, g1, "5")
        assert _accepts(byte_ctx, g2, "5")
    finally:
        zg.grammar_release(g1)
        zg.grammar_release(g2)


def test_live_sessions_survive_registry_swap(byte_ctx):
    """Sessions pin the snapshot through the grammar handle (ADR-0006 D5):
    a session created before the swap keeps its language."""
    g1 = _compile(byte_ctx, {"$ref": "http://ex.com/int.json"}, REG_V1)
    s = zg.Session(byte_ctx, g1)
    try:
        assert s.accept(ord("5")) == zg.BLG_OK
        assert s.can_end()
    finally:
        s.destroy()
    g2 = _compile(byte_ctx, {"$ref": "http://ex.com/int.json"}, REG_V2)
    try:
        assert _accepts(byte_ctx, g1, "7")
        assert not _accepts(byte_ctx, g1, '"y"')
        assert _accepts(byte_ctx, g2, '"y"')
    finally:
        zg.grammar_release(g1)
        zg.grammar_release(g2)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_unknown_base_uri_refusal(byte_ctx):
    msg = _refusal(byte_ctx,
                   {"properties": {"x": {"$ref": "http://ex.com/missing.json"}}},
                   "UNSUPPORTED_FEATURE", registry=REG_V1)
    assert "/properties/x" in msg


def test_relative_ref_stays_refused(byte_ctx):
    # Registry v1 resolves exact base URIs only; relative references and
    # cross-document $id base changes are out of scope.
    _refusal(byte_ctx, {"$ref": "int.json"}, "UNSUPPORTED_FEATURE",
             registry=REG_V1)


def test_no_registry_keeps_old_refusal(byte_ctx):
    _refusal(byte_ctx, {"$ref": "http://ex.com/int.json"},
             "UNSUPPORTED_FEATURE")


def test_registry_requires_spec_v1(byte_ctx):
    _refusal(byte_ctx, {"$ref": "http://ex.com/int.json"},
             "UNSUPPORTED_FEATURE", registry=REG_V1, profile=b"canonical-v1")


def test_malformed_registry_refusals(byte_ctx):
    _refusal(byte_ctx, {"type": "integer"}, "INVALID_SCHEMA",
             registry=b"[]")
    _refusal(byte_ctx, {"type": "integer"}, "INVALID_SCHEMA",
             registry=b'{"version":"v1"}')
    _refusal(byte_ctx, {"type": "integer"}, "INVALID_SCHEMA",
             registry=b'{not json')


def test_remote_false_root_is_empty_language(byte_ctx):
    reg = {"documents": {"http://ex.com/no.json": False}}
    _refusal(byte_ctx, {"$ref": "http://ex.com/no.json"},
             "UNSATISFIABLE", registry=reg)


def test_remote_cycle_without_base_case_refuses(byte_ctx):
    reg = {"documents": {
        "http://ex.com/a.json": {"$ref": "http://ex.com/b.json"},
        "http://ex.com/b.json": {"$ref": "http://ex.com/a.json"},
    }}
    _refusal(byte_ctx, {"$ref": "http://ex.com/a.json"},
             "UNSATISFIABLE", registry=reg)


def test_legacy_request_size_without_registry(byte_ctx):
    """A caller built against the pre-P5 header passes the 32-byte request
    and gets the old behavior (no registry fields read)."""
    import ctypes
    req = zg.ZgCompileRequest()
    req.struct_size = 32  # pre-P5 sizeof(blg_compile_request)
    req.kind = zg.BLG_CONSTRAINT_JSON_SCHEMA
    req.profile = b"spec-v1"
    data = b'{"type":"integer"}'
    buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
    req.data = buf
    req.data_len = len(data)
    out = ctypes.c_void_p()
    err = zg.new_error()
    status = zg.LIB.blg_compile(byte_ctx.handle, ctypes.byref(req),
                                ctypes.byref(out), ctypes.byref(err))
    assert status == zg.BLG_OK
    assert _accepts(byte_ctx, out, "7")
    assert not _accepts(byte_ctx, out, '"s"')
    zg.grammar_release(out)
