# ADR-0007 mask fast path - final acceptance on the post-audit tree (final3)

Date: 2026-09-26. Library: `/tmp/zigout-final3/lib/libbolorgir.so`
(`zig build -Doptimize=ReleaseFast --cache-dir /tmp/zigcache-final3 --prefix /tmp/zigout-final3`).
Raw data: `bench_adr0007_raw.json` (= `benchmarks/results/20260926T190500_adr0007_final3.json`),
`maskbench_tbm_p90.json` (= `benchmarks/results/20260926T190500_maskbench_tbm_p90_final3.json`).
Vocab: 128256 (Llama 3.1).

## Reproducibility snapshot (audit R7)

- git HEAD: `c1cc2cb315c885ec8b4cd926d1a9b51cde9ad1a2` + uncommitted
  working tree (audit fix wave R1-R4; no commits made).
- SHA-256 libbolorgir.so: `c9f5fad11d17adba8a7078cd47f50071675de014fdf48608572e83996c935ef5`.
- Combined SHA-256 of all `src/**`, `include/**`, `build.zig`
  (sha256sum over the sorted files, then sha256sum of the list):
  `b152ada989dc294f56b04b633c921d940abfc86c51065f510a8a3ef11e8c74c4`.
- zig 0.15.2, ReleaseFast, `.use_llvm = true`.
- Slice profile: `canonical-v1`, mode=adaptive, memory_limit 256 MB,
  sequential observations, machine free of other bench processes.
- Tokenizer: `unsloth/Meta-Llama-3.1-8B-Instruct` @
  `a2856192dd7c25b842431f39c179a6c2c2f627d1` (128256 tokens).

## Slice composition (stated honestly, instead of "20 files")

Earlier reports quoted "TBM p90 = 481 µs" - that was a slice of **20 files /
473 masks** (round 2). This acceptance runs the **full pinned `MASKBENCH_SLICE`
slice (all 129 BFCL_java candidates, of which 24 files compile** under
canonical-v1; the rest are rejected by supported_features 1a - a rejection is
not a measurement): **24 files / 564 masks**; the file list is in
`maskbench_tbm_p90.json` (`files`). The sample is deterministic via
`select_compiling` and wider than before (24 vs 20: after the R4 fixes, 4
additional slice files compile).

## Results

| Metric | OFF (baseline trie walk) | ON (fast path) | Verdict |
|---|---|---|---|
| Differential gate | n/a | **PASS** (4 probe + 425 maskbench states, 564 masks, 0 validation errors) | GO |
| Cold permissive_string_content p50 | 380.7 ms / 383 647 ops | **4.35 ms / 20 744 ops** | GO (~87x, ops match the 20 744 model) |
| Cold permissive_string_in_object p50 | 389.1 ms / 383 662 ops | **4.42 ms / 20 759 ops** | GO (~88x) |
| Warm hit p50, C-ext (baseline was 0.85 µs) | off 0.998 / 1.046 µs | **0.905 / 0.977 µs** (p95 1.00 / 1.05, p99 1.14 / 1.29) | **MET (≤ 1.3 µs)** |
| Warm hit p50, ctypes (informational) | off 1.73 / 1.86 µs | 1.81 / 1.85 µs | above the guard due to the ~0.9 µs ctypes Python floor; the control channel is C-ext |
| MaskBench TBM p50 | 166.4 µs | 158.5 µs | loaded states outside the exact class are not slowed |
| MaskBench TBM p90 (564 masks, 24 files) | 402 747 µs | **305.3 µs** | **MET (≤ 1 ms)** |
| MaskBench TBM p95 / p99 / max | 408 219 / 419 249 / 444 010 µs | 1 496 / 3 652 / 4 891 µs | tail = first (cold) visits of states |
| Masks > 1 ms | 153 / 564 | 46 / 564 | all 46 are cold visits, not repeat cache misses |

TBM p90 progress from the pre-ADR-0007 starting point: 609 222 µs → 305.3 µs (~2000x).

## Notes

- The old 1.90 µs warm figure (round 2) does not carry over: on the current build
  warm-ext p50 = 0.905/0.977 µs, and the 1.3 µs guard passes with margin.
  The discrepancy with round 2 is the build/tree change, not the methodology
  (same C-ext channel, same manifest).
- The ctypes channel of the warm path (1.8 µs) mostly measures the Python floor;
  it is kept in the report as informational - the ADR-0007 guard refers to C-ext.
- After thread deduplication (the parser fix from the audit wave) no hot-path
  overhead is observed: TBM p50 on is 158.5 µs vs 220.6 µs in round 2
  (wider slice, newer build).
- Reproduction command:
  `HF_HUB_OFFLINE=1 BLG_LIB_PATH=/tmp/zigout-final3/lib/libbolorgir.so PYTHONPATH=python:benchmarks:tests python3 benchmarks/bench_adr0007.py --out benchmarks/results/20260926T190500_adr0007_final3.json`
  (the full 129-candidate slice is the default; p90 was topped up by a wrapper
  script over the same `maskbench_traces`/`select_compiling`, see the raw json).
