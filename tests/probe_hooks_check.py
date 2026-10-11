"""Compile-time probe (ADR-0012) checks that need the test hooks: runs
against a core built with `zig build -Dtest_hooks=true` (BLG_LIB_PATH points
at it). Exit status 1 on the first failed check; prints what ran.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import blg_ctypes as blg  # noqa: E402
import reference as ref  # noqa: E402

SPEC = ref.TokenizerSpec(tokens=tuple(bytes([i]) for i in range(256)) + (b"",),
                         eos_ids=(256,), special_ids=()).validate()
LIB = blg.LIB
LIB.blg_test_set_overrides.argtypes = [ctypes.c_uint64, ctypes.c_uint64, ctypes.c_int32, ctypes.c_uint64]
LIB.blg_test_set_overrides.restype = ctypes.c_uint32
LIB.blg_test_frame_tag.argtypes = [ctypes.c_char_p]
LIB.blg_test_frame_tag.restype = ctypes.c_int32


def force_unknown(kind: str | None):
    tag = -1 if kind is None else LIB.blg_test_frame_tag(kind.encode())
    assert kind is None or tag >= 0, kind
    assert LIB.blg_test_set_overrides(0, 0, tag, 0) == 0


def check_found():
    # Every state with a pattern string on top is undecided: the probe
    # must find the first one, name its subschema, and the witness prefix
    # must fail the mask of an ordinary session the same way.
    schema = {"type": "object", "properties": {"a": {"type": "string", "pattern": "^x"}},
              "required": ["a"], "additionalProperties": False}
    force_unknown("str_pat")
    try:
        ctx = blg.Context(SPEC, probe_max_states=1000)
        try:
            blg.compile_schema(ctx, schema, profile=b"spec-v1", flags=blg.BLG_COMPILE_PROBE)
            raise AssertionError("probe did not find the forced UNKNOWN")
        except blg.CoreFailure as e:
            assert e.status == blg.BLG_ERR_RESOURCE_LIMIT, e
            msg = blg.err_text(e.err)
            prefix = msg.split("prefix: ", 1)[1].encode()
            assert e.json_pointer.startswith("/properties/a"), e.json_pointer
            pointer = e.json_pointer
        g = blg.compile_schema(ctx, schema, profile=b"spec-v1")
        s = blg.Session(ctx, g)
        for b in prefix:
            assert s.accept(b) == blg.BLG_OK
        status, _ = s.fill_mask_words()
        assert status == blg.BLG_ERR_RESOURCE_LIMIT, status
        s.destroy()
        blg.grammar_release(g)
        ctx.destroy()
    finally:
        force_unknown(None)
    return {"found_prefix": prefix.decode(), "pointer": pointer}


def check_inconclusive():
    schema = {"type": "string", "pattern": "^a"}
    ctx = blg.Context(SPEC, probe_max_states=3)
    g = blg.compile_schema(ctx, schema, profile=b"spec-v1", flags=blg.BLG_COMPILE_PROBE)
    assert blg.probe_status(g) == blg.BLG_PROBE_INCONCLUSIVE
    blg.grammar_release(g)
    ctx.destroy()
    ctx = blg.Context(SPEC, probe_max_states=3)
    try:
        blg.compile_schema(ctx, schema, profile=b"spec-v1", flags=blg.BLG_COMPILE_PROBE_STRICT)
        raise AssertionError("STRICT accepted an inconclusive probe")
    except blg.CoreFailure as e:
        assert e.status == blg.BLG_ERR_RESOURCE_LIMIT
        assert "probe inconclusive" in blg.err_text(e.err)
    ctx.destroy()
    return {"inconclusive": "ok"}


def main():
    out = {}
    out.update(check_found())
    out.update(check_inconclusive())
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
