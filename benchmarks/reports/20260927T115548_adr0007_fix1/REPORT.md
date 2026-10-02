# ADR-0007 mask fast path - acceptance on the final tree (fix1, after the VMA panic fix)

Date: 2026-09-27. Library: `/tmp/zigout-fix1/lib/libbolorgir.so`
(`zig build --release=fast --cache-dir /tmp/zigcache-fix1 --prefix /tmp/zigout-fix1`;
per build.zig the effective mode is ReleaseSafe with preserved symbols).
Raw data: `bench_adr0007_raw.json` (= `benchmarks/results/20260927T115548_adr0007_fix1.json`),
`maskbench_tbm_p90.json` (= `benchmarks/results/20260927T115548_maskbench_tbm_p90_fix1.json`).
Vocab: 128256 (Llama 3.1).

Difference from the final3 acceptance: the tree additionally contains a minLength
mask fix (`src/mask.zig` normalizeUniformString) and a replacement of the engine's
root allocator `page_allocator` -> `smp_allocator` (`src/c_api.zig` - fixes the
`munmap` ENOMEM panic on `vm.max_map_count` exhaustion during long runs).

## Reproducibility snapshot (audit R7)

- git HEAD: `c1cc2cb315c885ec8b4cd926d1a9b51cde9ad1a2` + uncommitted
  working tree (audit fix waves R1-R4, minLength, allocator;
  no commits made).
- SHA-256 libbolorgir.so: `e21846feca5c601709c261fff0794c33a1d2bedb650f26f104a995a1a2b28ef2`.
- Combined SHA-256 of all `src/**`, `include/**`, `build.zig`
  (sha256sum over the sorted files, then sha256sum of the list):
  `35e03a11878c28bbb361a5bfcbb3ad58888e5ab3ac2b5d4ca824f767a5b0e4f8`.
- zig 0.15.2, `--release=fast` (effectively ReleaseSafe), `.use_llvm = true`.
- Slice profile: `canonical-v1`, mode=adaptive, memory_limit 256 MB,
  sequential observations, machine free of bench processes.
- Tokenizer: `unsloth/Meta-Llama-3.1-8B-Instruct` @
  `a2856192dd7c25b842431f39c179a6c2c2f627d1` (128256 tokens).

## Slice composition

The same full pinned `MASKBENCH_SLICE` slice (all 129 BFCL_java candidates,
24 files compile under canonical-v1): **24 files / 564 masks**; the list is in
`maskbench_tbm_p90.json` (`files`). The composition matches final3 bit-for-bit;
differential gate: the same 425 maskbench states.

## Results

| Metric | OFF (baseline trie walk) | ON (fast path) | Verdict |
|---|---|---|---|
| Differential gate | n/a | **PASS** (4 probe + 425 maskbench states, 564 masks, 0 validation errors) | GO |
| Cold permissive_string_content p50 | 382.6 ms / 383 647 ops | **4.44 ms / 20 744 ops** | GO (~86x) |
| Cold permissive_string_in_object p50 | 384.1 ms / 383 662 ops | **4.45 ms / 20 759 ops** | GO (~86x) |
| Warm hit p50, C-ext (guard ≤ 1.3 µs) | off 0.887 / 0.967 µs | **0.826 / 0.871 µs** (p95 0.92 / 0.96, p99 1.20 / 1.16) | **MET** |
| Warm hit p50, ctypes (informational) | off 1.99 / 1.86 µs | 1.90 / 1.93 µs | ctypes Python floor; the control channel is C-ext |
| MaskBench TBM p50 | 166.1 µs | 151.6 µs | loaded states are not slowed |
| MaskBench TBM p90 (564 masks, 24 files) | 384 103 µs | **267.5 µs** | **MET (≤ 1 ms)** |
| MaskBench TBM p95 / p99 / max | 387 809 / 403 810 / 423 343 µs | 1 437 / 3 398 / 3 925 µs | tail = first (cold) visits of states |

TBM p90 progress from the pre-ADR-0007 starting point: 609 222 µs → 267.5 µs (~2300x).

## Notes

- The allocator swap (smp) did not worsen any metric: warm-ext p50 improved
  0.905/0.977 -> 0.826/0.871 µs, TBM p90 305.3 -> 267.5 µs relative to final3 -
  small allocations no longer cost two syscalls (mmap+munmap) each.
- The ctypes channel of the warm path is kept informational; the ADR-0007 guard
  refers to the C-ext channel.
- Reproduction command:
  `HF_HUB_OFFLINE=1 BLG_LIB_PATH=/tmp/zigout-fix1/lib/libbolorgir.so PYTHONPATH=python:benchmarks:tests python3 benchmarks/bench_adr0007.py --out benchmarks/results/20260927T115548_adr0007_fix1.json`
  (the full 129-candidate slice is the default; p90 was topped up by a wrapper
  script over the same `maskbench_traces`/`select_compiling`, see the raw json).
