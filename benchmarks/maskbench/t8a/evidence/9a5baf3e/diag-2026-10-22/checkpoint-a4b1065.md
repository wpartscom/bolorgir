# Checkpoint a4b1065: mask fast-path scope fix

Status: candidate checkpoint, not a release and not an acceptance result.

Result wording, to be used verbatim: **the two found mask errors are fixed;
the outcomes of the checked documents matched**. Matching outcomes on the
equivalence slice is not a proof of general correctness: that gate compares
`consumed` / `rejected_at` / `n_ids`, and mask bits are compared only by the
dedicated ABI test below.

## Artifact

- build: `zig build -Drelease=true -Dstrip=true`
- toolchain: `benchmarks/local/toolchain/zig-x86_64-linux-0.17.0/zig`
  (0.17.0)
- file: `zig-out/lib/libbolorgir.so.0.1.0`, 1 521 264 bytes
- sha256 `a4b10658eb393e347bc08762db5d0247e608be21f0a1aee078887b5d336e60aa`
- reproducible: a second build into a separate prefix produced the same
  bytes on 2026-10-22.

Local snapshot (outside the repository, `benchmarks/local/` is ignored):
`benchmarks/local/t8a-checkpoints/a4b10658/` with the library copy
(`lib.sha256`), `sources.tar.gz` (the files below plus this evidence
directory, Russian triage excluded), `tracked.diff` (modifications of
tracked files), `sources.sha256`, `build.txt`, `repro_after_fix.json` and a
copy of the package checksums (`evidence.sha256`).

## Sources in the checkpoint

| file | sha256 |
|---|---|
| `build.zig` | `a5026da9ff6c14e5f433ac8f50a22d58c5b22ae8d31670b67f42969c272e5ed2` |
| `.gitignore` | `e4e106bb013df942bcc6120d239d81dded9d20155f80772fc2c6e5c9532b53d1` |
| `CHANGELOG.md` | `ac84f367fd29ded892fe27868fcf03fc73766226496508e2a69d774cf858f8c1` |
| `src/mask.zig` | `e29fde3f3c2eb99a67c504a9d9479bcd557bd1ed7c20c59414b6245d12ac6390` |
| `src/diag.zig` | `b0721efc05fcc49122364f47816e79775e819920960c71c2056e7eecb42dc94c` |
| `src/c_api.zig` | `e6bd91e8cf5beb0bdcab4e00768020933abc66d75be71ec38fd8129104f55b79` |
| `docs/mask-fast-path-needs-reachability.md` | `5a24acccbaa8898ccfac5ab8fc7b86a99de0c6a2da2de05acfad907d83a03e46` |
| `docs/adr/ADR-0007-mask-fast-path.md` | `2c2fc1a477ffdf8faa0d093bc11c71ec6132b9f836aee1332497607ac3c1a975` |
| `benchmarks/maskbench/t8a/paired_runner.py` | `25084c73bdfaf319cbec7fb413325e7233be3b7ca9408518c1284d7e579721d0` |

`src/diag.zig` and `docs/mask-fast-path-needs-reachability.md` are new files
in this tree; everything else is a modification of a tracked file (see
`tracked.diff`). The abort-phase patch for `paired_runner.py` is present as a
separate file at this checkpoint and is not applied (see the note, section 7).

After the checkpoint was taken, the abort-phase fix was applied to the working
runner (`benchmarks/maskbench/t8a/paired_runner.py`, sha256
`09cc2c7e6679a258c5da2409aaccd43a1a77216d6187457900d2f4e5566c168d`), so the
patch file no longer exists in the tree; the checkpoint library and the
snapshot itself are unchanged.

## What the checkpoint contains

- the string-content fast path is refused when the enclosure can make the
  string content part of a parent verdict: an armed byte tap
  (`uniqueItems` / `contains` / schema-kind dependencies) or a `.comb` frame
  below the top `.str` frame;
- the two reviewer reproductions are refused by the exact path: a duplicate
  `a` under `uniqueItems` and `ab` over the shorter `allOf` length bound;
- `BLG_MASK_NO_FILTER` is removed; diagnostic counters are relaxed atomics;
- the counter formerly named `refuse_needs_reach` is now
  `refuse_content_dep` (`mask_diag.py` order updated).

## Verification on this artifact

- reviewer reproducer (byte-complete vocabulary, fast path on and off,
  `lazy` and `adaptive`): 4/4 masks bit for bit equal, `fast_used == 0` in
  every case (`equiv/repro_after_fix.json`);
- `zig build test`: exit 0; the test binary run directly prints
  `All 316 tests passed` (315 existing tests plus the new ABI scope test);
- equivalence slice, 43 files / 684 calls, 18 files carry 51 documents:
  0 mismatched outcomes, mask time 15.9 s, p90 6.0 ms, p99 842 ms
  (`equiv/after_scope_fix.json`);
- six VE files, 1515 calls: 0 mismatched outcomes, the abort at the same
  token as the run; mask time 497.7 s; two compile-limit files unchanged
  (`equiv/ve_after_scope_fix.json`);
- control repeats are unaffected (they run the pinned run core, not this
  candidate); the `single` round finished 6/6.

## Limits, stated explicitly

- ADR-0007 is not met: p90 is about 6.0 ms against the 1 ms target.
- The heavy families are untouched: property-name positions
  (`o34343`, `o64963`, `o21292`) and `pattern` strings (`o90249`); one
  `o90249` probe step reached 50.7 s.
- The scope fix costs 2.5 s on the slice, concentrated in
  `Github_trivial---o41679` (18 calls of `oneOf` of two closed objects).
- The run's own results do not confirm this core: a new wheel, a new work
  directory and a new R6/T8a run are required, and they are gated on the
  bitwise checks, the ADR-0007 target, improvement on the problem families
  and unambiguous refusal records.

## How to re-verify

```
zig build -Drelease=true -Dstrip=true
sha256sum zig-out/lib/libbolorgir.so.0.1.0
zig build test
python3 <review>/repro_after_fix.py <repo>
PYTHONPATH=<pkg>:benchmarks/maskbench/t8a:benchmarks/external/jsonschemabench-maskbench/maskbench \
  benchmarks/local/t8a-work-9a5baf3e/venv-pinned/bin/python \
  benchmarks/local/t8a-work-9a5baf3e-w12/diag/mask_equiv.py --files equiv/slice.txt --out J
```

The reviewer reproducer pins the expected core hash; update that constant
when checking another artifact.
