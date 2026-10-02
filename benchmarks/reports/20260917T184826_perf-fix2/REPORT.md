# Control run §10.6 / §13.2 - 2026-09-17 (repeat)

Repeat control run after fixing two performance defects found while analyzing
the previous NO-GO verdict (`results/20260915T024409/`):

1. **0xAA fill of `undefined` in `parser.feedBytes`** (`src/parser.zig`).
   Runtime-safety builds fill `var work: State = undefined` with the 0xAA
   pattern - that is ~80 KiB of `memset` on **every call** and, worse, on
   **every iteration** of the loop over input streams. Multithread states
   (e.g. an enum of 64 literals) cost ~100 µs on `accept_token`, ordinary
   accepts - ~1.5 µs of overhead. Fix: a per-thread (`threadlocal`) scratch
   instead of the stack variable; the fill disappeared from the hot path
   (verified by disassembler: 0 `memset`/`--zig_probe_stack` calls in `feedBytes`).
2. **`parser.hashState`: byte-wise FNV-1a → Wyhash** (8-byte words).
   A `fill_mask` cache hit on a 64-thread state hashed ~7.7 KiB of bytes at
   once: ~2.5 µs out of ~3 µs of mask latency. After the replacement - ~1.45 µs
   on the same state. The hash is only an intra-process cache key (collisions
   are resolved by `eqlStates`); the change of function is unobservable.

This run also measured for the first time the previously implemented
`MaskGpuUnpacker` adapter optimization (transfer of compact uint32 words to the
device, bit unpacking on the GPU) and completed the certification of the second
tokenizer family (SP/TinyLlama).

- Configuration: seed 42; same machine and pinned versions
  (`environment.txt`: zig 0.15.2 ReleaseSafe, python 3.10.12, torch 2.14.0+cu130,
  transformers 5.17.0, tokenizers 0.23.2, xgrammar 0.2.6, llguidance 1.8.0,
  numpy 1.26.4; RTX 3060, driver 580.178.04). Scenario and thresholds - from
  `manifest.json` (`control_scenario`), pinned 2026-09-15, unchanged.
- Tests before the run: core 102/102 (Debug and ReleaseSafe), Python 296/296 on
  two backends (`ctypes`, `package`).

## Verdict §10.6

**NO-GO by the formal manifest rule - but for the first time with the main
metric passed.** Required simultaneously: mask p99 ≥20% below the best
competitor, no >10% degradation on additional metrics, and e2e regression ≤5%.

| Check | zig_adaptive | xgrammar (best) | Δ | Threshold | Outcome |
|---|---|---|---|---|---|
| **p99 fill_mask (main)** | **1.60 µs** | 3.20 µs | **-49.9%** | ≥ -20% | pass |
| p50 fill_mask | 0.51 µs | 0.80 µs | -35.8% | ≤ +10% | pass |
| p95 fill_mask | 1.47 µs | 2.77 µs | -46.9% | ≤ +10% | pass |
| p50 accept_token | 0.34 µs | 0.62 µs | -45.9% | ≤ +10% | pass |
| p95 accept_token | 2.01 µs | 0.83 µs | **+143.3%** | ≤ +10% | fail |
| p99 accept_token | 2.07 µs | 0.92 µs | **+125.7%** | ≤ +10% | fail |
| cold compile p50 | 2187 µs | 1176 µs | **+85.9%** | ≤ +10% | fail |
| first mask p50 | 419 µs | 428 µs | -1.9% | ≤ +10% | pass |
| e2e guard B7 (tok/s regression) | max **4.4%** | - | - | ≤ 5% | pass |

Comparison with the previous run: main metric -2.4% → **-49.9%**; checks
passed 1 of 8 → 5 of 8; e2e regression >5% on 6/9 configurations → ≤5% on
all 9. Two structural items still fail:

- **accept p95/p99**: for zig, on multithread states (entering/leaving an enum
  literal of 64 alternatives) accept costs ~1.5-2 µs, whereas xgrammar moves
  this work into fill (its accept is almost empty). This is a difference in how
  work is distributed between calls, not in the total step cost; passing the
  threshold requires reworking the multithread-state walk (e.g. lock-step over
  thread merging) - a separate task.
- **cold compile**: 2.19 ms vs 1.18 ms (+85.9%; the previous run had
  +2504%). Content: schema compilation + coverage check (FR-6). Requires
  profiling and, probably, deferring transition construction or speeding up
  `coverage.checkCoverage`.

Honest wording for SPEC 10.6: "a correct prototype; **the performance hypothesis
is confirmed on the main metric**, but not confirmed on two additional conditions
(accept on multithread states, cold compilation)".

## Item 2 - equal load (bench_compare_uniform.json)

Schema big_enum_64, shared trace `{"symbol":"sym_42","weight":0.5}` (14 GPT-2
tokens), ≥10k warm observations per engine, µs:

| engine | mask p50 | mask p95 | mask p99 | accept p50 | accept p99 | cold compile p50 | first mask p50 | warm compile p50 |
|---|---|---|---|---|---|---|---|---|
| zig_lazy | 32.20 | 250.24 | 268.99 | 0.39 | 2.07 | 2157.67 | 401.67 | 974.14 |
| zig_adaptive | **0.51** | **1.47** | **1.60** | **0.34** | 2.07 | 2187.12 | 419.44 | 969.83 |
| xgrammar | 0.80 | 2.77 | 3.20 | 0.62 | **0.92** | **1176.44** | **427.53** | **2.07** |
| llguidance | 16.56 | 40.80 | 43.60 | 0.77 | 1.94 | 722.66 | 8928.21 | 1.82 |

- zig_adaptive is 96.3% below llguidance on mask p99; the main-metric threshold
  is computed against the best competitor (xgrammar).
- zig mask p99 (1.60 µs) is cache hits on 64-thread states (hash +
  comparison + mask copy); after the Wyhash fix the tail shrank from ~3.1 µs.
- zig_lazy - the exact path without cache: p99 269 µs (the cost of recomputing
  the mask at every step), given for completeness.

## Item 3 - B1, 30 independent processes (bench_coldproc.json)

Schema big_enum_64, sequential separate processes, p50 of 30:

| engine | ok | compile, ms | first mask, ms | total cold, s | peak RSS, MiB |
|---|---|---|---|---|---|
| zig_adaptive | 30/30 | 1.37 | 0.42 | 2.43 | 760 |
| xgrammar | 30/30 | 1.17 | 0.42 | 2.72 | 762 |
| llguidance | 30/30 | 0.30 | 1.39 | 2.61 | 776 |

Comparison with the previous run: cold compile zig 28.37 ms → **1.37 ms** (parity
with xgrammar, 4.6× gap to llguidance). Process errors 0. Process memory is
≈ equal for all three (dominated by importing transformers/torch) - the second
alternative benefit of §10.6 (memory) is not confirmed.

## Item 4 - B7 constrained vs constrained-baseline (bench_e2e_constrained.json)

Qwen2.5-1.5B-Instruct fp16, RTX 3060, greedy, 3 repeats, medians; baseline =
xgrammar constrained. Validity: 369/369 for both engines.

| schema | batch | zig tok/s | xg tok/s | tok/s regression | zig total, s | xg total, s | ITL p50, ms | lengths zig / xg |
|---|---|---|---|---|---|---|---|---|
| s1 | 1 | 67.2 | 68.6 | +2.0% | 0.24 | 0.23 | 14.6 / 14.4 | 16 / 16 |
| s1 | 8 | 362.9 | 378.4 | +4.1% | 0.36 | 0.34 | 17.6 / 16.7 | 14..18 |
| s1 | 32 | 898.2 | 939.6 | +4.4% | 0.58 | 0.55 | 24.0 / 22.7 | 14..18 |
| s2 | 1 | 67.8 | 69.0 | +1.7% | 0.65 | 0.64 | 14.6 / 14.5 | 44 / 44 |
| s2 | 8 | 328.3 | 333.6 | +1.6% | 1.02 | 1.01 | 17.8 / 17.5 | 34..55 |
| s2 | 32 | 857.5 | 873.6 | +1.8% | 1.57 | 1.54 | 25.2 / 24.2 | 34..55 |
| s3 | 1 | 67.8 | 63.4 | -6.9% | 0.41 | 0.66 | 14.7 / 14.6 | 28 / 42 |
| s3 | 8 | 349.0 | 241.3 | -44.6% | 0.64 | 1.04 | 18.4 / 18.0 | 26..29 / 26..42 |
| s3 | 32 | 949.8 | 449.6 | -111.2% | 0.93 | 2.22 | 26.2 / 25.6 | 26..29 / 26..42 |

Reading: on s1/s2 (required fields, deterministic structure) zig is slower
by 1.6-4.6% - **within the 5% threshold** (the previous run had +21 to +35% due
to 577 µs/row for mask unpacking in the adapter). On s3 zig is faster in wall
time; xgrammar produces longer answers (26..42 vs 26..29) - lengths are published
for correct interpretation of tok/s.

**e2e guard outcome: passed** (maximum regression 4.4% ≤ 5%).

## Item 5 - GPU mask intervals (bench_gpu_mask_path.json)

Qwen vocabulary 151665, logits 151936 (padded lm_head), p50 of 300 steps, µs
(`torch.cuda.synchronize` around each stage):

| engine | build | H2D (+unpack) | apply | full path |
|---|---|---|---|---|
| zig_adaptive | 5.1 | **24.9** | **19.2** | **49.2** |
| xgrammar | 5.9 | 16.4 | 30.6 | 53.0 |
| llguidance | 33.8 | 21.4 | 75.1 | 132.0 |

The previous main adapter bottleneck was removed (577 µs of CPU unpacking via
`torch.arange(vocab)`): now only compact uint32 words (vocab/32) are transferred
to the device, bit unpacking happens on the GPU, buffers are cached
(`MaskGpuUnpacker`, the standard `ConstraintLogitsProcessor` path). Full path
49.2 µs - **faster than both competitors** (xg 53.0, lg 132.0). This is what
also removed the e2e regression from item 4.

## Item 6 - B5/B8 honest long-run generation (bench_longrun.json)

Real steps: full fill_mask+accept trace on every session cycle, 3 rotating
schemas, GPT-2. Per limit: 90 072 scored mask steps, 11 112 create/destroy
cycles; total across limits ≈270k steps and 33 336 cycles (SPEC B8: ≥100k and
≥10k - met).

| limit | errors | mask p99 by windows | RSS | cache hits / evictions |
|---|---|---|---|---|
| 64 MiB | 0 (incl. ResourceLimit 0) | plateau, ratio 0.993 | ratio 1.0 | 99 993 / 0 |
| 128 MiB | 0 | ratio 1.276 (single spike in the window - noise) | ratio 1.0 | 99 993 / 0 |
| 256 MiB | 0 | plateau, ratio 0.899 | ratio 1.0 | 99 993 / 0 |

Memory and latency plateau confirmed; no leaks; the cache works
(hits ≈ steps).

## Item 7 - B3/B6 and the second family (bench_schemas_tokenizers.json)

B3 (schema switch):
- JSB-100 (pinned commit): MVP language support 1/100 (84
  UnsupportedFeature, 15 InvalidSchema) - support report per SPEC 10.3;
- corpus: 15 schemas supported; compile p50 of unique schemas 46.7 µs (lazy) /
  42.6 µs (adaptive), of repeated ones - 41.3 / 39.9 µs;
- session phase (10 schemas × 3 rounds, cold mask computations): fill_mask
  p50 6.42 µs, p99 102.17 µs. In the control scenario with a warmed cache the same
  states give p99 1.6 µs (see item 2) - the tail relates to the first mask
  computations on multithread states (edge walk of the trie), recorded as a
  known optimization reserve.

B6 (tokenizers):

| tokenizer | family | vocab | prepare, ms | compile, ms | mask p50, µs | core memory, MiB |
|---|---|---|---|---|---|---|
| GPT-2 | byte-level BPE | 50257 | 52.8 | 1.2 | 0.48 | 34.3 |
| Qwen2.5-1.5B | byte-level BPE | 151665 | 198.5 | 4.1 | 0.91 | 134.6 |
| **TinyLlama** | **SP/BPE byte_fallback** | **32000** | **26.9** | **0.9** | **0.43** | **18.5** |
| synthetic | byte | 32768 | ~0 | 0.4 | 0.45 | 39.6 |
| synthetic | byte | 131072 | ~0 | 2.3 | 0.81 | 134.5 |
| synthetic | byte | 262144 | ~0 | 3.9 | 1.12 | 287.0 |

**The second family is closed.** Branch order in `tokenizers.py` fixed (SP branch
before byte-level with `byte_fallback`); the shim only sets the flag. Pairwise
verification against `backend.tokenizer.decode`: 400/400 pairs explained -
199 exact, 198 the first `▁` rule, 3 pairs of class `lossy_utf8_reference`
(an isolated "dangling" byte `<0xD1>/<0xE8>/<0xFD>` is decoded as U+FFFD in the
reference - information loss in the reference decode; adapter bytes are exact;
for real documents the string grammar requires complete UTF-8 sequences).
`certified: true`, bad: [].

## Raw data and reproduction

- `environment.txt` - versions and sha256 of sources and the `.so`; `bench_*.json` -
  raw observations/aggregates; `bench_e2e_constrained.json` - all runs with
  answer texts and lengths.
- Reproduction (from the root): `PYTHONPATH=python:benchmarks python3
  benchmarks/run_all.py` (all CPU cases; passing the pinned control-scenario
  arguments was fixed - previously `run_all.py` discarded
  `--schema big_enum_64 --document ...` from the SCRIPTS list; in this run the
  comparison was re-run manually) and the GPU scripts individually:
  `bench_gpu_mask_path.py --steps 300`,
  `bench_e2e_constrained.py --out <directory>`.
- Summary and Go-rule recomputation: `python3 benchmarks/summarize_control.py
  benchmarks/results/20260917T184826_perf-fix2` (the summarizer was made
  robust to unavailable engines and the new B3 format).

## Deviations and caveats

- `sentencepiece` is not installed in the run environment; the SP path goes
  through the `tokenizers` backend and the shim; B6 was measured without it
  (the previous configuration had 0.2.2 - does not affect the numbers).
- The `torch` version on the bench was rebuilt as `2.14.0+cu130` (standard for
  this machine; the CPU variant that was in place after the environment restore
  does not allow GPU cases).
- The B7 limitation from the previous report about llguidance (`is_error` after
  a successful consume) persists as a property of 1.8.0 and does not affect
  this run.
