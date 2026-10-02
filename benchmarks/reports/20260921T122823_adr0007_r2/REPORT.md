# ADR-0007 mask fast path - round 2: closing the warm-hit and MaskBench TBM p90 regressions

Date: 2026-09-21. Library: `/tmp/zigout-perf2/lib/libbolorgir.so` (isolated build,
`zig build --cache-dir /tmp/zigcache-perf2 --prefix /tmp/zigout-perf2`).
Raw data: `bench_adr0007_raw.json` (= `benchmarks/results/20260921T122823_adr0007_r3.json`),
`maskbench_tbm_p90.json`. Vocab: 128256 (Llama).

## Round tasks

1. Warm cache-hit: 4.33 µs (round 1 regression; pre-ADR-0007 baseline 0.849 µs; guard ≤ 1.3 µs).
2. MaskBench TBM p90 ≤ 1 ms (after round 1: 3731.6 µs on the slice; full p90 424 528 µs).

Constraints honored: canonical-v1 bit-for-bit unchanged, spec-v1 semantics unchanged,
Frame ≤ 32 bytes, ADR-0005 not violated, kill switch `BLG_MASK_FAST_PATH=0` works,
no git commits made.

## Results

| Metric | Before (off / round 1) | After (on, round 2) | Verdict |
|---|---|---|---|
| Warm hit p50 (C-ext helper) | 4.33 µs (r1), off-parity 1.95 µs | **1.90 µs** (p95 2.14) | -56% vs r1; the 1.3 guard is formally not met, see "Warm remainder" |
| Cold permissive_string_content p50 | 419.6 ms / 383 647 ops | **5.07 ms / 20 744 ops** | GO (82x) |
| Cold permissive_string_in_object p50 | 423.6 ms / 383 662 ops | **5.21 ms / 20 759 ops** | GO |
| MaskBench TBM p90 (473 masks, 20 files) | 424 528 µs | **481.0 µs** | **MET (≤ 1 ms)** |
| MaskBench TBM p95 / p99 | 427 838 / 436 110 µs | 1 432 / 4 015 µs | tail = first visits of normalized states |
| Masks > 1 ms | 120 / 473 | 37 / 473 | all 37 are cold visits, not repeat cache misses |
| Differential gate | n/a | **PASS** (4 probe + 411 maskbench states, 473 masks, 0 validation errors) | GO |
| Exact-class share of the corpus | n/a | 4.81% | GO (the fast branch covers the mass case) |

TBM p90 progress from the pre-ADR-0007 starting point: 609 222 µs → 481 µs (**1266x**).

## What was done

### Task 1 - warm hit (4.33 → 1.90 µs)

Bisection (scratch copy of the tree, cut levels in `fillMaskInner`) localized the sources:

- `mask_buf.blob.resize()` - first session allocation through the accounting allocator: **+2.7 µs** per hit.
- 16 KB mask memcpy: ~590 ns (compiler-rt in Debug; a manual u64 loop and `@Vector` were tested and are worse - left alone).
- ABI floor (Python/C-ext + 16 KB PyBytes + the call itself): ~576 ns.

Changes:

- `src/c_api.zig` (`fillMaskInner`): for chunk-free states the blob key is a 4-byte
  stack constant (zero count) instead of `resize`; the hit path completes under a single
  mutex (get + stats), with no second lock/unlock pair.
- `src/parser.zig`: `hashState` returns `StateHash{hash, has_chunks}`
  (`has_chunks` ⟺ empty blob, the conditions match exactly); bulk hashing of runs of
  chunk-free frames; `eqlStatesBlobs` - bulk `memcmp` of the spine when the thread has no
  open_obj/repeat frames (equivalence: different frame tags → differing tag byte → memcmp = false).
  All call sites updated to `.hash` (c_api.zig, precompute.zig ×3, 2 tests in parser.zig).

### Task 2 - TBM p90 ≤ 1 ms: normalized cache instead of chain batching

Chain batching was evaluated and rejected: an exact pruned walk is 16.7k edges × ~220 ns,
per-edge thread copies are small (only frame lens are copied), so the win ceiling is
≤ 1.6x - the target would not be met.

Implemented **normal form of a uniform string-content state (D2+)**:

- Lemma: with residual R ≥ r_cap (= max(cp_max, max_tok_bytes)) the resulting mask does not
  depend on `str.count` - the reach of any token is bounded by r_cap. Hence the state can be
  normalized (count → 0) and looked up / stored in the **ordinary** ctx cache under the
  normalized key: all positions of a long string share one cache entry.
- `src/precompute.zig`: `FastData` += field `r_cap` (computed at build).
- `src/mask.zig`: `normalizeUniformString(st, side, out)` - copies the threads with count
  zeroed + `parser.retainState` (out owns references like a live state).
- `src/c_api.zig` (`fillMaskInner`, adaptive branch): if fast active + the fillMaskFast guard
  (`tok.byte_complete or !g.finite_literal`) + `uniformStringResidual ≥ r_cap` → normalize into
  the **scratch slot** `s.states[s.cur ^ 1]` (a preceding `releaseState` - the same invariant
  as accept; destroy frees both slots). The rest of the path (hashState/blob/get/put/
  fillMaskFast/fillMask) then works with key_state = scratch. No allocations on the path (the
  first version with a lazy 128-KB allocation cost 26 µs on warm - replaced by the scratch slot).
  The lazy branch is untouched.

Normalization correctness control: `["ab","cd` vs `["ab","cdefgh` under `uniqueItems` - correctly
NOT a hit (the repeat.tap chunk differs); `maxLength=3` + 6 characters - accept correctly refuses.

### Interaction with the new feedBytes (P3)

The reworked feedBytes (threads advance together in one work-state) neither helped nor hurt
batching: per-edge cost is unchanged. Batching by ResourceLimit equivalence is still unsafe
at `in.n > 1` - hence the normalization path was chosen.

## Acceptance (all on the final build)

- `zig build test`: **229/229**.
- `pytest tests/ python/tests/ -q`: **609 passed, 22 skipped** (77.6 s), including 11/11
  `tests/test_mask_fastpath.py` (+2 new: cache-entry sharing across string positions;
  bounded string near the limit stays on the exact path).
- Oracle: spec-v1 **833 PASS / 0 MISMATCH / 0 ENGINE_ERROR**
  (raw output `oracle-20260921T122752Z.json`, not retained); canonical-v1 **95 PASS / 0 MISMATCH**
  (raw output `oracle-20260921T122753Z.json`, not retained).
- `bench_jsb_coverage --snapshot both`: status OK, maskbench compiled=624 (pinned number
  preserved), repo=38.

## Warm remainder: why 1.90 and not ≤ 1.3 µs

Breakdown of the 1.90 µs: ABI floor ~576 ns (C-ext + 16 KB PyBytes) + mask memcpy ~590 ns
(the 0.85 µs baseline had it too) + key+get under mutex ~500 ns + overhead. There is nothing
left to cut without changing the ABI or the mask format. Experiments: DynLib-libc memcpy and
`@setRuntimeSafety(false)` gave no win.

Control: the bundled library `python/bolorgir/_lib` on the same machine gives 0.86 µs - the gap
is explained by hit-path growth from the P1 chunk-blob redesign (`hashState(side)` + blob +
`eqlBlobs`), not by ADR-0007; on/off parity is preserved (1.95 / 1.90 µs).

## Changed files (no commits, as required)

- `src/c_api.zig` - stack blob, single mutex per hit, normalization into the scratch slot, config tail + env kill switch (r1).
- `src/parser.zig` - `StateHash{hash, has_chunks}`, bulk hashing, bulk `eqlStatesBlobs`.
- `src/mask.zig` - `fillMaskFast` (r1), `normalizeUniformString`.
- `src/precompute.zig` - `FastData` + cp validator (r1), `r_cap` field.
- `src/cache.zig` - widened cache (r1).
- `include/bolorgir.h` - config tail (r1).
- `tests/test_mask_fastpath.py` - 11 tests (+2 this round).
- `benchmarks/bench_adr0007.py` - bench/gate run.

## Caveats

- The 1.3 µs warm guard is formally not met (1.90 µs); the remainder is structural (ABI +
  memcpy + key); the round-1 regression is reduced by 56%.
- TBM p95 = 1.43 ms: the tail is the first visits of each normalized state per file
  (37 cold masks out of 473), not cache misses.
- p99 cold on (string_in_object) 7.58 ms - a single allocator outlier on first warmup.
