# ADR-0007 mask fast path - acceptance on the final tree (fix2, after the memoPut leak fix)

Date: 2026-09-27. Library: `/tmp/zigout-fix2/lib/libbolorgir.so`
(`zig build --release=fast --cache-dir /tmp/zigcache-fix2 --prefix /tmp/zigout-fix2`;
per build.zig the effective mode is ReleaseSafe with preserved symbols).
Raw data: `bench_adr0007_raw.json` (= `benchmarks/results/20260927T203000_adr0007_fix2.json`),
`maskbench_tbm_p90.json` (= `benchmarks/results/20260927T203000_maskbench_tbm_p90_fix2.json`).
Vocab: 128256 (Llama 3.1).

Difference from the fix1 acceptance: the tree additionally contains a fix for the
duplicated-key leak in `memoPut` (`src/complete.zig`, getOrPut instead of
dupe-then-put; 4466 bytes per fill_mask cycle, charged against the context's
TEMP budget) and a clarified comment at `root_allocator` in `src/c_api.zig`.
The fix only changes memory freeing: the semantics of memo entries
(value updated, key retained) and all oracle verdicts match fix1 bit-for-bit.

## Reproducibility snapshot (audit R7)

- git HEAD: `c1cc2cb315c885ec8b4cd926d1a9b51cde9ad1a2` + uncommitted
  working tree (audit fix waves R1-R4, minLength, allocator,
  memoPut; no commits made).
- SHA-256 libbolorgir.so: `bc25a03aae48a908d523ed34b48b8c62c21084d147812765693015435e60db13`.
- Combined SHA-256 of all `src/**`, `include/**`, `build.zig`,
  exact command:
  `{ find src include -type f; echo build.zig; } | LC_ALL=C sort | xargs sha256sum | sha256sum`
  (equivalent to `rg --files src include build.zig | sort | xargs sha256sum | sha256sum`
  - there are no gitignore exclusions under src/include). Value:
  `33cfeb8f0a0a384956ab6bc61cc8220f61081e188b32e19ae2ab7cb14c9e57f0`.
  (The fix1 report quoted a different value because of a different folding
  command - build.zig was hashed as a separate line; here the command is
  pinned explicitly.)
- zig 0.15.2, `--release=fast` (effectively ReleaseSafe), `.use_llvm = true`.
- Slice profile: `canonical-v1`, mode=adaptive, memory_limit 256 MB,
  sequential observations, machine free of bench processes.
- Tokenizer: `unsloth/Meta-Llama-3.1-8B-Instruct` @
  `a2856192dd7c25b842431f39c179a6c2c2f627d1` (128256 tokens).

## Slice composition

The same full pinned `MASKBENCH_SLICE` slice (all 129 BFCL_java candidates,
24 files compile under canonical-v1): **24 files / 564 masks**; the list is in
`maskbench_tbm_p90.json` (`files`). The composition matches fix1 bit-for-bit;
differential gate: the same 425 maskbench states.

## Results

| Metric | OFF (baseline trie walk) | ON (fast path) | Verdict |
|---|---|---|---|
| Differential gate | n/a | **PASS** (4 probe + 425 maskbench states, 564 masks, 0 validation errors) | GO |
| Cold permissive_string_content p50 | 388.8 ms / 383 647 ops | **4.40 ms / 20 744 ops** | GO (~88x) |
| Cold permissive_string_in_object p50 | 386.8 ms / 383 662 ops | **4.43 ms / 20 759 ops** | GO (~87x) |
| Warm hit p50, C-ext (guard ≤ 1.3 µs) | off 0.839 / 0.950 µs | **0.898 / 0.946 µs** (p95 0.98 / 1.04, p99 1.17 / 1.33) | **MET** |
| Warm hit p50, ctypes (informational) | off 1.93 / 1.87 µs | 1.88 / 1.98 µs | ctypes Python floor; the control channel is C-ext |
| MaskBench TBM p50 | 164.1 µs | 154.0 µs | loaded states are not slowed |
| MaskBench TBM p90 (564 masks, 24 files) | 386 523 µs | **274.3 µs** | **MET (≤ 1 ms)** |
| MaskBench TBM p95 / p99 / max | 394 860 / 410 382 / 436 970 µs | 1 417 / 3 381 / 3 829 µs | tail = first (cold) visits of states |

TBM p90 progress from the pre-ADR-0007 starting point: 609 222 µs → 274.3 µs (~2200x).

## Notes

- Relative to fix1 (0.826/0.871 µs warm-ext, TBM p90 267.5 µs) the metrics are
  within ordinary run-to-run spread; both guards (≤ 1.3 µs, ≤ 1 ms) pass with
  margin. The memoPut fix does not affect the hot path: memo operates in
  completion search, not in the mask fast path.
- The ctypes channel of the warm path is kept informational; the ADR-0007 guard
  refers to the C-ext channel.
- Reproduction command:
  `HF_HUB_OFFLINE=1 BLG_LIB_PATH=/tmp/zigout-fix2/lib/libbolorgir.so PYTHONPATH=python:benchmarks:tests python3 benchmarks/bench_adr0007.py --out benchmarks/results/20260927T203000_adr0007_fix2.json`
  (the full 129-candidate slice is the default; p90 was topped up by a wrapper
  script over the same `maskbench_traces`/`select_compiling`, see the raw json).
