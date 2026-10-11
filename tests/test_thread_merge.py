"""Threads that differ only in structurally equal nodes are merged after
each byte, and max_threads_per_state counts the distinct live threads
(parser.dedupThreads). Found by Github_ultra/o6366.json (skaffold): seven
untyped closed objects under anyOf each read a number with two machines,
and four such arrays beside each other exceeded the 64 threads of the
coverage harness at the first byte of a number element."""

from __future__ import annotations

import reference as ref
import blg_ctypes as blg

SPEC = ref.TokenizerSpec(tokens=tuple(bytes([i]) for i in range(256)) + (b"",),
                         eos_ids=(256,), special_ids=()).validate()

# Seven branches, each a closed object without "type": any number is valid
# in every branch.
ITEMS = {"anyOf": [{"additionalProperties": False, "properties": {"image": {"type": "string"}, k: {}}}
                   for k in ("docker", "bazel", "jib", "kaniko", "buildpack", "custom", "sync")]}


def run(schema, doc: bytes, cap: int):
    ctx = blg.Context(SPEC, max_threads_per_state=cap)
    try:
        g = blg.compile_schema(ctx, schema, profile=b"spec-v1")
        s = blg.Session(ctx, g)
        try:
            for b in doc:
                status, words = s.fill_mask_words()
                assert status == blg.BLG_OK, (doc, b)
                assert words[b // 32] >> (b % 32) & 1, (doc, b)
                assert s.accept(b) == blg.BLG_OK
            status, words = s.fill_mask_words()
            assert status == blg.BLG_OK
            return bool(words[256 // 32] >> (256 % 32) & 1)
        finally:
            s.destroy()
            blg.grammar_release(g)
    finally:
        ctx.destroy()


def test_equal_branches_of_an_untyped_anyof_share_threads():
    # 8 threads: the seven closed objects stay apart after `{`, while the
    # number, true/false/null and string readings of the branches merge.
    schema = {"type": "array", "items": ITEMS}
    assert run(schema, b'[-1.5e2,true,"s",7,{"image":"x","jib":1}]', 8)


def test_cap_counts_distinct_threads_beside_other_alternatives():
    # The o6366 shape: four alternatives of an object that each hold such an
    # array, its items under allOf with a required key. 32 threads hold the
    # 28 closed objects after `[{`; the number readings before the merge
    # needed more than 48.
    items = {"allOf": [ITEMS, {"required": ["image"]}]}
    alt = {"type": "object", "properties": {"artifacts": {"type": "array", "items": items}}}
    schema = {"anyOf": [dict(alt, required=[r]) for r in ("a", "b", "c", "d")]}
    assert run(schema, b'{"artifacts":[-1,null,{"image":"x"}],"a":0}', 32)
