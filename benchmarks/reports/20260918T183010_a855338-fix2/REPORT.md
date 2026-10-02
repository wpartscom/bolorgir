# Control run a855338-fix2 (sixth): per-step HF-processor tail, secondary holdout, clean cold compile

Date: 2026-09-19. Code commit: `6fe7982` (plus a `docs/API.md` edit after the run -
documentation only, see environment.txt). Data: 12 run_all cases (including
secondary runs), e2e ×2 with 4 repeats per configuration (pool n=8) on the extended
s1-s4 matrix, GPU path; summary - `summarize_control.py` (unified gate R7+A5+a855338).

## 1. Verdict: **GO** - all checks passed

| Check | Result |
|---|---|
| Main metric: mask p99 vs xgrammar | **passed: +75.5%** (0.71 vs 2.91 µs; threshold +20%) |
| Other latencies ≤+10% vs the same competitor | **passed**, incl. cold compile 124 vs 1188 µs (-89.5%), warm 1.03 vs 2.18 (-52.8%), first mask 443 vs 500 (-11.4%), accept p99 0.48 vs 0.89 |
| **Secondary holdout** (review item 5, schema `secondary_geo_route`, previously unused) | **passed**: mask p99 0.74 vs 357.4 µs for llguidance (best competitor; +99.8%), others ≤+10%: cold 84 vs 248, warm 0.94 vs 1.21, first 449 vs 8429, prepare 55.7 vs 245.8 ms, accept p99 0.42 vs 1.86 |
| e2e guard: extended matrix s1-s4 × b1/8/32, medians n=8 | **12/12 within the 5% threshold**; former blocker `s1 b32` closed (+3.6%) |
| B1: 30 independent processes | 30/30; compile p50 0.18 ms (best), total 2.18 s (best), RSS 756 MiB |
| B5/B8 (90k steps; 64/128/256 MiB) | no degradation: plateau 0.972/0.976/1.012, RSS ratio 1.0, evictions 0 |

The previous verdict (fifth run, NO-GO because of `s1 b32 +5.4%`) is closed on the
merits: the gap was removed by optimizing the processor per-step path; the criterion
and thresholds were unchanged.

## 2. What changed since the fifth run (commit `6fe7982`)

1. **Per-step HF-processor tail** (`zig_constraints.transformers`):
   - new tokens - one batched D2H per step instead of ≤batch scalar `int(cuda tensor)`
     (on b32: ~926 → ~137 µs per step, measurement `diag_proc_step.py`);
   - row masks - one `fill_masks_batch` kernel call (a failing row is
     replayed with a single `fill_mask()`, the kernel message is preserved);
   - NaN/+inf check among allowed tokens - one `amax` reduction + one sync instead of
     a cascade of `isnan/isposinf/isfinite` with two syncs; without `zero_()` and
     `index_select` copies over the full vocabulary;
   - finalization - `finish_rows`: one batched D2H for the whole batch.
   Result: `s1 b32` +5.4% → +3.6%, `s1 b8` +3.6% → +4.3% (within the threshold; see §8).

2. **Secondary holdout corpus** (review item 5 of a855338): new schema
   `benchmarks/corpus/secondary_geo_route.json` (not involved in the optimizations),
   `bench_masks_secondary` and `bench_compare_uniform_secondary` runs in
   `run_all.py`, new e2e schema `s4_batch_jobs` in the mandatory matrix,
   `secondary_guard` in the gate. The threshold rules are the same; the check was
   added **stricter** (fixed in the manifest before the run).

3. **Clean cold compile - a measurement artifact removed.** The "strange ~1 ms" in
   `cold_compile` was not compilation: the measurement window (t2..t3 around
   `constraint = engine.compile(bytes)`) included the deallocation of the PREVIOUS
   `Constraint → Engine → TokenizerBundle` (~50k byte objects + blob,
   ~1 ms; the STORE_FAST chain `constraint` → old `Engine` held the bundle until
   deallocation). `Engine.close()` now releases `_bundle`/`_tokenizer` immediately
   (the `eos_ids` cache stays readable). The metric now measures compilation:
   **124.2 µs** (was 1047.7) on the main corpus, **84.0 µs** (was ~994) on the
   holdout; the core was unchanged (internal `compile_ns` ~60 µs, c_api revision
   untouched). Diagnostics: `benchmarks/diag_proc_step.py` (per-step timing that
   attributes the ~1 ms window to the teardown of the previous objects rather
   than to `compile`); the old spread "min 163 µs / p50 1046 µs" in the notes
   is the same artifact.

## 3. Uniform (big_enum_64, GPT-2 @607a30d7, ≥10k observations)

| engine | mask p50 | mask p95 | mask p99 | accept p50 | accept p99 | cold p50 | warm p50 | first mask p50 |
|---|---|---|---|---|---|---|---|---|
| **zig_adaptive** | **0.47** | **0.66** | **0.71** | **0.30** | **0.48** | **124.2** | **1.03** | 443.4 |
| zig_lazy | 8.44 | 245.9 | 256.3 | 0.34 | 0.60 | 125.7 | 56.3 | 427.0 |
| xgrammar | 0.78 | 2.71 | 2.91 | 0.59 | 0.89 | 1188.0 | 2.18 | 500.2 |
| llguidance | 16.4 | 41.4 | 44.4 | 0.76 | 1.91 | 745.7 | 1.79 | 8971.0 |

µs. Trace = the canonical document `{"symbol":"sym_42","weight":0.5}` (14 tokens).

## 4. Secondary holdout (`secondary_geo_route`, GPT-2, trace 48 tokens)

| engine | mask p50 | mask p99 | accept p99 | cold p50 | warm p50 | first mask p50 | prepare p50 |
|---|---|---|---|---|---|---|---|
| **zig_adaptive** | **0.46** | **0.74** | **0.42** | **84.0** | **0.94** | 449.1 | 55.7 ms |
| zig_lazy | 8.54 | 84756 | 2.65 | 81.4 | 24.7 | 413.5 | 55.7 ms |
| xgrammar | 0.77 | 2567.9 | 1.27 | 40892.8 | 1.44 | 588.7 | 81.9 ms |
| llguidance | 12.8 | 357.4 | 1.86 | 247.7 | 1.21 | 8428.5 | 245.8 ms |

µs. Best competitor by mask p99 - llguidance (xgrammar on this schema has
mask p95 1.15 ms); all other zig_adaptive latencies are ≤ +10% relative to it,
main metric +99.8%.

**Observation outside thresholds (published):** `zig_lazy` on the holdout shows
mask p95/p99 ≈ 83-85 ms - pathological states without cache (adaptive covers them:
0.74 µs); xgrammar there shows p95 1.15 ms. This is a property of the workload/modes,
not of the gate: the scored engine is adaptive.

## 5. B1: 30 independent processes

| engine | ok | compile p50 ms | first mask p50 ms | total cold p50 s | peak RSS p50 MiB |
|---|---|---|---|---|---|
| **zig_adaptive** | 30 | **0.18** | 0.50 | **2.18** | 756 |
| xgrammar | 30 | 1.17 | 0.43 | 2.39 | 758 |
| llguidance | 30 | 0.27 | 1.31 | 2.13 | 772 |

## 6. B7 + guard (pool medians n=8)

| case | zig s | xg s | Δ | threshold |
|---|---|---|---|---|
| s1 b1 | 0.23 | 0.23 | -0.3% | OK |
| s1 b8 | 0.38 | 0.36 | **+4.3%** | OK (thin margin) |
| s1 b32 | 0.60 | 0.58 | +3.6% | OK (was FAIL +5.4%) |
| s2 b1 | 0.64 | 0.64 | -0.1% | OK |
| s2 b8 | 1.08 | 1.07 | +1.1% | OK |
| s2 b32 | 1.72 | 1.75 | -1.6% | OK |
| s3 b1 | 0.40 | 0.63 | -37.2% | OK |
| s3 b8 | 0.57 | 1.03 | -44.6% | OK |
| s3 b32 | 0.97 | 2.18 | -55.4% | OK |
| s4 b1 | 0.57 | 0.57 | -0.1% | OK |
| s4 b8 | 0.79 | 0.78 | +1.2% | OK |
| s4 b32 | 1.24 | 1.22 | +2.0% | OK |

Validity 100%, completed 100% in all configurations; repeat spread follows the
warmup protocol (the first request of each (schema, batch) pair is published in
`warmups`). Example s1 b8: zig repeats 0.352-0.381 s, xg best 0.35 s.

## 7. Other

- **GPU mask intervals** (p50, µs): zig build 4.83 / H2D+unpack 24.27 / apply 18.56 /
  **full 47.6**; xgrammar 6.14/15.95/30.92/52.96; llguidance 32.88/20.62/72.41/128.44.
- **B5/B8** (90 072 steps, 11 112 cycles): p99 plateau 0.972/0.976/1.012, RSS ratio 1.0,
  cache hits 99 993, evictions 0 - no degradation.
- **B3/B6**: session-phase fill_mask p50 6.15 / p99 88.06 µs; corpus schemas
  supported 16 (including the new holdout); JSB-100 unchanged (ok 1 / unsupported 84 /
  invalid 15).

## 8. Published losses and weak spots

- `s1 b8 +4.3%` (5% threshold) - the thinnest margin in the matrix: the same per-step
  Python-processor tail against the C++ xgrammar core as in `s1 b32`; further closing
  is possible only by moving mask application into the core (outside this wave).
- `zig_lazy` mask p95/p99 on the holdout 83-85 ms (see §4) - not the scored mode,
  but the fact is published.
- zig `prepare` (56-62 ms) - second place after xgrammar (82-90 ms); llguidance is
  slower (246-284 ms). The table is complete, with no cherry-picking of convenient
  metrics.
- Warm compile on the holdout: 0.94 vs 1.21 µs for lg - parity at timer level;
  on the main corpus 1.03 vs 2.18 µs (xg).

## 9. Reproduction

```bash
export PYTHONPATH=python:benchmarks HF_HUB_OFFLINE=1
python3 benchmarks/run_all.py --tag a855338-fix2        # 12 cases, including *_secondary
D=benchmarks/results/20260918T183010_a855338-fix2
python3 -u benchmarks/bench_e2e_constrained.py --out "$D"
python3 -u benchmarks/bench_e2e_constrained.py --out "$D/repeat"
mv "$D/repeat/bench_e2e_constrained.json" "$D/bench_e2e_constrained_repeat.json" && rmdir "$D/repeat"
python3 benchmarks/bench_gpu_mask_path.py > "$D/bench_gpu_mask_path.json"
python3 benchmarks/summarize_control.py "$D"            # exit 0 = GO
```

Tests at the time of the run: `zig build test [-Drelease=true]` 116/116;
`pytest tests/ python/tests/ -q` 340/340 on both backends (ctypes/package).

## 10. Closing review items of a855338

- item 4 (s2 + protocol): closed in the fifth run, confirmed by the sixth
  (s2 -1.6 to +1.1% on all batches).
- item 5 (previously unused corpus): **closed** - secondary holdout and the new
  e2e schema s4 in the mandatory matrix; thresholds passed, including a main-metric
  win of +99.8%.
- Also found and fixed: the cold compile artifact (~1 ms of previous-bundle
  deallocation inside the measurement window) - the metric now reflects compilation
  (84-124 µs), the core was unchanged.
