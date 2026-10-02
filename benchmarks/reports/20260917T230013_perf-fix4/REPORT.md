# Control run perf-fix4 (review `f631e68`, fixes R1-R7)

Date: 2026-09-17/18 (run 23:00-23:52 PDT, report 2026-09-18).
Commit: `46cc790` on top of `f631e68`; environment and hashes - `environment.txt`.
Data: 10 `run_all.py` cases + e2e ×2 (6 repeats per configuration) +
GPU path; summary - `summarize_control.py` (unified gate §10.6, review R7).

## 1. Verdict

**NO-GO by the formal §10.6 criterion.** The main metric passed with margin,
but the mandatory checks of the criterion are not met:

| Check | Result |
|---|---|
| Main criterion: mask p99 vs xgrammar | **passed, -75.5%** (0.74 µs vs 3.03 µs) |
| first mask p95 vs xgrammar | +25.2% - ≥20% present |
| Peak memory (B1) vs xgrammar | +0.3% - option does not pass |
| Other latencies ≤+10% | **fail**: warm compile p50/p95/p99 +2687%/+2879%/+320% (59.6/69.2/84.9 µs vs 2.14/2.32/20.2 µs) |
| e2e guard ≤5% (mandatory) | **fail 2 of 9**: s2 b1 +12.6%, s2 b8 +9.3% |

Wording for SPEC §10.6: "CPU speedup is confirmed on the control scenario
(mask p99 -75.5%); full product acceptance is not closed: warm compile remains
~28× slower than xgrammar, two e2e cases exceed the 5% threshold due to the
first-repeat spike and run-to-run spread (analysis - §5)".

Previously (perf-fix3) a GO was declared on this scenario - that run's criterion
did not include warm compile and the additional-latency check (review R7). On
perf-fix3 data the new gate also yields NO-GO.

## 2. What was fixed before the run (R1-R7)

- **R1** (§3.1, FR-7): mask and `accept` account for completion reachability
  (`src/complete.zig`); byte-complete vocabularies take the previous path.
- **R2** (FR-6): byte images of tokens follow the actual HF decoder
  (ByteFallback, added tokens, SP Substitute/Strip); `strip_first_space`
  is modeled by a core flag.
- **R3**: a single memory-limit layer (double header removed).
- **R4**: cancellation triggers on a cache hit too; budgets/CancelToken in Python;
  warmup does not consume the call's `work_limit_ops`.
- **R5**: preflight on the effective `generation_config`; EOS is limited to ids
  confirmed by the core (extra ones - with a warning).
- **R6**: `setup.py` looks for zig via ZIG→PATH→fallback path; CI: baseline
  build, `ZG_SKIP_ZIG`, matrix 3.10-3.13, fuzzer job.
- **R7**: a true unified criterion (see §4).

Transparency during the run: the first e2e pass showed TTFT growth up to
+178 ms per row - our own guard caught a regression in the R5 code
(`Engine.eos_ids` rebuilt the tokenizer table for every row of the batch).
Fixed by bundle memoization + a regression test; e2e data was restarted, and
the fixed data are included in the report. (Test: 32×`eos_ids` 5683.8 ms → 0.03 ms.)

## 3. Tests before the run

- Core: 114/114 Debug, 114/114 ReleaseSafe (`zig build test [-Drelease=true]`).
- Python: 310/310 on two backends (ctypes and package), `HF_HUB_OFFLINE=1`.
- Fuzzer - a separate scheduled CI job (not repeated in this run).

## 4. Criterion §10.6 (unified, R7) - how it is structured

Options of the main criterion (one is sufficient, ≥20% better than the competitor):

1. mask p99 vs xgrammar (worst zig repeat against the competitor's best);
2. first mask p95;
3. peak memory (B1, 30 processes).

For a passing option it is additionally required: all other measured latencies
(mask/accept/cold compile/warm compile/first mask by p50/p95/p99) not worse
than +10% of the same competitor. Separately mandatory is the **e2e guard**:
medians of `total_s` across repeats (pool of two runs) ≤5%. A GO verdict
requires the passing option + its checks + e2e. Failure of any part - NO-GO;
insufficient data - INCONCLUSIVE. Wins are published regardless of the verdict;
the criterion was not changed after the run.

## 5. Analysis of failures

### 5.1 Warm compile (blocks both latency options)

`warm compile` on this bench is compilation of a schema with the process caches
already warm: zig 59.6 µs (p50) vs 2.14 µs for xgrammar. This is a structural
~28× gap: xgrammar's profile focuses on recompilation, while in our core the
warm path runs the full compiler pass without reusing results of the previous
compilation of the same schema. Fixing it is separate work (see §7); in the
current state a GO verdict on the latency options is impossible by construction.

### 5.2 e2e guard: s2 b1 +12.6%, s2 b8 +9.3%

Repeat distributions (6 per configuration, two runs):

- `s2 b1` zig: [0.64, 0.64, 1.27] + [0.74, 0.73, 1.58] → median 0.74;
  xg: [0.64, 0.64, 0.75] + [0.65, 0.66, 0.79] → median 0.65.
- `s1 b32` (control): zig 0.58-0.59, xg 0.55-0.56 - +3..5% (threshold
  periphery, both runs pass).
- `s3` all batches: zig is **faster** by 36-59%.

Nature: a persistent first-repeat spike - exactly one generation step becomes
more expensive by 0.4-0.6 s (ITL p99 326-419 ms at p50 15-17 ms). The same spike
reproduces in **perf-fix3** data (rep0 1.24 s vs 0.65/0.64 for repeats 1-2) -
i.e. this is not an R1-R7 regression but a protocol artifact (first large step
of the process: allocator/clone/GC). The second run was overall ~0.1 s slower
than the first for zig on s2. The pool median of the two runs is what pushes
past 5%.

Protocol decision (at the owner's discretion, outside this report): either
warm the e2e repeat before scoring, or compute the median of the "best two of
three" - then the s2 configurations pass (+1.5%/+1.4% on medians of repeats
without the spike, as in perf-fix3). The criterion was not changed retroactively.

### 5.3 Peak memory

B1 (30 processes): zig 759 MiB vs 762 MiB for xgrammar (+0.3%) - the option
does not give ≥20%. The long-run RSS plateau is stable (see §6.4).

## 6. Measurements

### 6.1 Uniform load (big_enum_64, GPT-2, trace 14 steps)

| engine | mask p50 | mask p99 | accept p50 | accept p99 | cold p50 | first mask p50 | warm p50 |
|---|---|---|---|---|---|---|---|
| zig_lazy | 8.73 | 264.68 | 0.37 | 0.63 | 1124.38 | 458.46 | 60.5 |
| **zig_adaptive** | **0.44** | **0.74** | 0.32 | 0.47 | 1028.66 | 486.39 | 59.64 |
| xgrammar | 0.78 | 3.03 | 0.59 | 0.97 | 1196.31 | 445.56 | 2.14 |
| llguidance | 17.03 | 43.9 | 0.74 | 1.92 | 747.74 | 9137.11 | 1.9 |

(µs; for zig - worst repeat, for competitors - best.)

### 6.2 B1: 30 independent processes

| engine | ok | compile p50 ms | first mask p50 ms | total cold p50 s | peak RSS p50 MiB |
|---|---|---|---|---|---|
| zig_adaptive | 30 | 1.02 | 0.52 | 2.47 | 759 |
| xgrammar | 30 | 1.23 | 0.43 | 2.78 | 762 |
| llguidance | 30 | 0.31 | 1.43 | 2.61 | 776 |

### 6.3 B7: e2e Qwen2.5-1.5B (repeat medians)

| schema | batch | zig s | xg s | Δ | valid |
|---|---|---|---|---|---|
| s1_flat_enum | 1 | 0.25 | 0.23 | +4.7% | 1/1 |
| s1_flat_enum | 8 | 0.36 | 0.35 | +4.3% | 8/8 |
| s1_flat_enum | 32 | 0.58 | 0.55 | -0.8..+5% | 32/32 |
| s2_nested_arrays | 1 | 0.74 | 0.65 | +12.6% (guard FAIL) | 1/1 |
| s2_nested_arrays | 8 | 1.12 | 1.03 | +9.3% (guard FAIL) | 8/8 |
| s2_nested_arrays | 32 | 1.57 | 1.53 | +2.8% | 32/32 |
| s3_optional_bounded | 1 | 0.41 | 0.65 | **-36.3%** | 1/1 |
| s3_optional_bounded | 8 | 0.56 | 1.03 | **-36.3%** | 8/8 |
| s3_optional_bounded | 32 | 0.90 | 2.17 | **-58.6%** | 32/32 |

Validity: 100% of rows in all configurations, documents match the schemas.
ITL p50 matches xgrammar (±5%); TTFT after the regression fix - 18-21 ms on
all batches (xg has 18-190 ms).

### 6.4 B5/B8: long-run generation (90 072 steps, 3 budgets)

Errors 0; p99 plateau (second half vs first) 0.96-1.13; RSS plateau 1.00 in
all budgets; 99 993 cache hits, 0 evictions.

### 6.5 B6: tokenizers

| tokenizer | vocab | prepare | compile | mask p50 | core mem |
|---|---|---|---|---|---|
| gpt2 | 50257 | 55.7 ms | 0.1 ms | 0.51 µs | 34.3 MiB |
| qwen2.5-1.5b | 151665 | 195.8 ms | 0.2 ms | 0.90 µs | 134.6 MiB |
| tinyllama-sp | 32000 | 27.0 ms | 0.1 ms | 0.43 µs | 18.5 MiB |
| synthetic ×3 | 32768-262144 | 0.0 ms | 0.1 ms | 0.42-1.14 µs | 39.6-287 MiB |

### 6.6 GPU mask path (build / H2D+unpack / apply / full), p50 µs

| engine | build | H2D+unpack | apply | full |
|---|---|---|---|---|
| **zig_adaptive** | 5.15 | 24.83 | 19.45 | **49.52** |
| xgrammar | 6.92 | 18.23 | 34.72 | 60.05 |
| llguidance | 37.12 | 24.14 | 79.86 | 144.31 |

## 7. Other published discrepancies

- mask p99: zig 0.74 vs 3.03 (xg, -75.5%) and 43.9 (lg, -98.3%).
- first mask p95: +25.2% to xg (486 vs 446 µs), but lg is 9.1 ms.
- cold compile p50: zig 1028.7 µs - better than xg (1196.3) and worse than lg (747.7).
- s3 e2e: -36% to -59% (a document with bounded optional fields finishes
  earlier thanks to structured finalization).
- Generation speed: tok/s within ±5% of xg in all configurations.

## 8. What's next (review order, step 4)

1. Warm compile: reuse of recompilation artifacts
   (a cache of the compiled grammar keyed by (schema, tokenizer, profile)) or
   an honest narrowing of claims in docs. Without it a GO by the formal criterion
   is unreachable.
2. HF batch: the TTFT regression is fixed (bundle memoization); the residual
   gap on s2 is at the protocol level (see §5.2); optionally - warm the repeat
   in the e2e script and restart.
3. Peak memory B1: margin 0.3% - the ≥20% goal has not been addressed yet
   (the main reserve - tokenizer tables and the state cache).

## 9. Reproduction

```bash
zig build -Drelease=true -Dcpu=baseline
cd python && ZG_SKIP_ZIG=1 python3 setup.py build_ext --inplace && cd ..
PYTHONPATH=python:benchmarks python3 benchmarks/run_all.py --tag perf-fix4
PYTHONPATH=python:benchmarks HF_HUB_OFFLINE=1 python3 benchmarks/bench_e2e_constrained.py --out <dir>/bench_e2e_constrained.json
PYTHONPATH=python:benchmarks HF_HUB_OFFLINE=1 python3 benchmarks/bench_e2e_constrained.py --out <dir>/bench_e2e_constrained_repeat.json
PYTHONPATH=python:benchmarks python3 benchmarks/bench_gpu_mask_path.py > <dir>/bench_gpu_mask_path.json
PYTHONPATH=python:benchmarks python3 benchmarks/summarize_control.py <dir>
```

(for `bench_e2e_constrained.py` the `--out` key is a directory: the file is
written inside as `bench_e2e_constrained.json`; then rename it to
`..._repeat.json` for the second run.)
