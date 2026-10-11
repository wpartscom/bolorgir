"""Compile-time generatability probe (ADR-0012): flags, verdicts, the
artifact cache, and the checks that need the test hooks
(tests/probe_hooks_check.py)."""

from __future__ import annotations

import ctypes
import glob
import json
import os
import subprocess
import sys

import pytest

import reference as ref
import blg_ctypes as blg

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_HOOKS_LIB = os.environ.get("BLG_TEST_HOOKS_LIB") or os.path.join(
    _ROOT, "zig-out", "test-hooks", "lib", "libbolorgir.so.0.1.0")

SPEC = ref.TokenizerSpec(tokens=tuple(bytes([i]) for i in range(256)) + (b"",),
                         eos_ids=(256,), special_ids=()).validate()
BOOL_OBJECT = {"type": "object", "properties": {"x": {"type": "boolean"}},
               "required": ["x"], "additionalProperties": False}


def test_finite_state_space_is_proven():
    ctx = blg.Context(SPEC)
    g = blg.compile_schema(ctx, BOOL_OBJECT, profile=b"spec-v1", flags=blg.BLG_COMPILE_PROBE)
    assert blg.probe_status(g) == blg.BLG_PROBE_PROVEN
    blg.grammar_release(g)
    ctx.destroy()


def test_plain_compile_does_not_probe():
    ctx = blg.Context(SPEC)
    g = blg.compile_schema(ctx, BOOL_OBJECT, profile=b"spec-v1")
    assert blg.probe_status(g) == blg.BLG_PROBE_NOT_RUN
    assert ctx.get_stats().probe_runs == 0
    blg.grammar_release(g)
    ctx.destroy()


def test_cached_artifact_is_probed_on_request_once():
    # A cache hit never skips a requested probe,
    # and a verdict obtained under the same budgets is reused.
    ctx = blg.Context(SPEC, blg.BLG_MODE_ADAPTIVE, cache_limit_bytes=blg.BLG_CACHE_DEFAULT)
    g0 = blg.compile_schema(ctx, BOOL_OBJECT, profile=b"spec-v1")
    assert ctx.get_stats().probe_runs == 0
    g1 = blg.compile_schema(ctx, BOOL_OBJECT, profile=b"spec-v1", flags=blg.BLG_COMPILE_PROBE_STRICT)
    assert ctx.get_stats().probe_runs == 1
    assert blg.probe_status(g1) == blg.BLG_PROBE_PROVEN
    g2 = blg.compile_schema(ctx, BOOL_OBJECT, profile=b"spec-v1", flags=blg.BLG_COMPILE_PROBE)
    assert ctx.get_stats().probe_runs == 1
    for g in (g0, g1, g2):
        blg.grammar_release(g)
    ctx.destroy()


def test_unknown_flag_and_earlier_request_sizes():
    ctx = blg.Context(SPEC)
    with pytest.raises(blg.CoreFailure) as e:
        blg.compile_schema(ctx, BOOL_OBJECT, profile=b"spec-v1", flags=4)
    assert e.value.status == blg.BLG_ERR_INVALID_ARGUMENT
    data = json.dumps(BOOL_OBJECT).encode()
    for size in (32, 48):
        req = blg.ZgCompileRequest()
        req.struct_size = size
        req.kind = blg.BLG_CONSTRAINT_JSON_SCHEMA
        req.profile = b"spec-v1"
        buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        req.data = buf
        req.data_len = len(data)
        req.flags = blg.BLG_COMPILE_PROBE  # beyond the declared size: ignored
        out = ctypes.c_void_p()
        err = blg.new_error()
        assert blg.LIB.blg_compile(ctx.handle, ctypes.byref(req), ctypes.byref(out), ctypes.byref(err)) == blg.BLG_OK
        assert blg.probe_status(out) == blg.BLG_PROBE_NOT_RUN
        blg.grammar_release(out)
    ctx.destroy()


def _residual_schemas():
    pin = json.load(open(os.path.join(_HERE, "oracle", "engine_error_residual_spec_v1.json")))
    seen = {}
    for row in pin["rows"]:
        if row["draft"] != "draft2020-12":
            continue
        files = glob.glob(os.path.join(_HERE, "oracle", "external", "**", "tests", "draft2020-12",
                                       row["suite_file"]), recursive=True)
        if not files:
            continue
        case = next(c for c in json.load(open(files[0])) if c["description"] == row["case"])
        seen[row["case"]] = case["schema"]
    return list(seen.values())


def test_residual_schemas_are_never_proven():
    schemas = _residual_schemas()
    if not schemas:
        pytest.skip("oracle suite snapshot not fetched")
    for schema in schemas:
        ctx = blg.Context(SPEC, probe_max_states=64)
        try:
            g = blg.compile_schema(ctx, schema, profile=b"spec-v1", flags=blg.BLG_COMPILE_PROBE)
        except blg.CoreFailure as e:
            assert e.status == blg.BLG_ERR_RESOURCE_LIMIT  # found
        else:
            assert blg.probe_status(g) == blg.BLG_PROBE_INCONCLUSIVE, schema
            blg.grammar_release(g)
        ctx.destroy()


@pytest.mark.skipif(not os.path.exists(_HOOKS_LIB), reason="core with test hooks not built")
def test_probe_found_and_inconclusive_with_hooks():
    env = dict(os.environ, BLG_LIB_PATH=_HOOKS_LIB)
    out = subprocess.run([sys.executable, os.path.join(_HERE, "probe_hooks_check.py")],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stdout[-2000:] + out.stderr[-2000:]
