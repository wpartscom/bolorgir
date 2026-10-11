"""Context memory does not depend on the process history: one schema
charges the same bytes in every create/compile/generate/release cycle and
in a fresh process (docs/RUN_HISTORY.md, process-history dependence)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import reference as ref
import blg_ctypes as blg

URI = ("^/(([a-zA-Z0-9._~!$&'()*+,;=:@-]|%[0-9a-fA-F]{2})+"
       "(/([a-zA-Z0-9._~!$&'()*+,;=:@-]|%[0-9a-fA-F]{2})*)*)?$")
N_REFS = 200


def pattern_refs_schema(n: int = N_REFS) -> dict:
    return {"type": "object",
            "definitions": {"path": {"type": "string", "pattern": URI}},
            "properties": {f"p{i}": {"$ref": "#/definitions/path"} for i in range(n)},
            "additionalProperties": False}


def byte_spec() -> ref.TokenizerSpec:
    tokens = tuple(bytes([i]) for i in range(256))
    return ref.TokenizerSpec(tokens=tokens + (b"",), eos_ids=(256,),
                             special_ids=()).validate()


def allowed(words, token_id: int) -> bool:
    return bool(words[token_id // 32] >> (token_id % 32) & 1)


def cycle() -> list[int]:
    """One cycle: context, compile, a document fed byte by byte with a mask
    before each byte, release. -> [grammar bytes after compile, peaks of
    every memory category]."""
    spec = byte_spec()
    ctx = blg.Context(spec, memory_limit_bytes=256 << 20)
    try:
        g = blg.compile_schema(ctx, pattern_refs_schema(), profile=b"spec-v1")
        grammar_used = ctx.get_stats().mem_used[blg.BLG_MEM_GRAMMAR]
        s = blg.Session(ctx, g)
        doc = json.dumps({f"p{i}": f"/a{i}/%2F" for i in range(0, N_REFS, 7)},
                         separators=(",", ":")).encode()
        for b in doc:
            status, words = s.fill_mask_words()
            assert status == blg.BLG_OK and allowed(words, b)
            assert s.accept(b) == blg.BLG_OK
        status, words = s.fill_mask_words()
        assert status == blg.BLG_OK and allowed(words, spec.eos_ids[0])
        s.destroy()
        blg.grammar_release(g)
        return [grammar_used, *ctx.get_stats().mem_peak]
    finally:
        ctx.destroy()


def test_repeated_pattern_is_compiled_once():
    # 200 $ref copies of one pattern built 200 DFAs and kept their
    # construction scratch in the grammar (19 MiB).
    ctx = blg.Context(byte_spec(), memory_limit_bytes=256 << 20)
    try:
        g = blg.compile_schema(ctx, pattern_refs_schema(), profile=b"spec-v1")
        assert ctx.get_stats().mem_used[blg.BLG_MEM_GRAMMAR] < 2 << 20
        blg.grammar_release(g)
    finally:
        ctx.destroy()


_FRESH = ("import json, sys; sys.path.insert(0, sys.argv[1]); "
          "import test_memory_history as t; print(json.dumps(t.cycle()))")


def test_cycles_charge_the_same_memory_as_a_fresh_process():
    first = cycle()
    for _ in range(5):
        assert cycle() == first
    here = os.path.dirname(os.path.abspath(__file__))
    env = dict(os.environ, BLG_LIB_PATH=blg.LIB._path)
    out = subprocess.run([sys.executable, "-c", _FRESH, here],
                         capture_output=True, text=True, env=env, check=True)
    assert json.loads(out.stdout) == first
