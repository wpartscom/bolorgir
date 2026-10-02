# Control run 7cd81c3-fix2 (seventh): grammar ownership, decoder semantics, artifact cache budget, C example and fuzzer

Date: 2026-09-19. Code commit: `b6878be` (wave 7cd81c3-fix; the same code in both
collections, see §2). Data: 12 `run_all` cases (including secondary runs), e2e ×2
with 4 repeats per configuration (pool n=8) on the s1-s4 matrix, GPU path; summary -
`summarize_control.py` (combined gate R7+A5+a855338, thresholds unchanged).

## 1. Verdict: **GO** - all checks passed

| Check | Result |
|---|---|
| Primary metric: mask p99 vs xgrammar | **passed: +72.7%** (0.79 vs 2.89 µs; threshold +20%) |
| Other latencies ≤+10% vs the same competitor | **passed**: cold compile 120.3 vs 1218.2 µs, warm 0.95 vs 2.05, first mask p99 712.1 vs 1535.7 (-53.6%), accept p99 0.48 vs 0.88 |
| **Secondary holdout** (item 5 of review a855338, schema `secondary_geo_route`) | **passed**: mask p99 0.79 vs 358.6 µs for llguidance (best competitor; +99.8%), others ≤+10%: cold 87.0 vs 134.6, warm 0.88 vs 1.24, first 468 vs 8314, accept p99 0.43 vs 1.92 |
| e2e guard: matrix s1-s4 × b1/8/32, medians n=8 | **12/12 within the 5% threshold**: s1 -0.3/+1.0/+3.0%, s2 +0.1/-0.4/-0.1%, s3 -36.5/-44.2/-56.4%, s4 -0.2/+2.0/+1.3% |
| B1: 30 independent processes | 30/30; compile p50 0.18 ms (best), total 2.18 s (best), RSS 756 MiB |
| B5/B8 (90 072 steps; 64/128/256 MiB) | no degradation: plateau 0.991/1.015/1.003, RSS ratio 1.0, errors 0, evictions 0 |
| Per-class observations (review 7cd81c3) | mask/accept/warm compile ≥10k warm, cold classes ≥30, B1 30 processes - satisfied (the gate did not emit INCONCLUSIVE) |

## 2. Two collection series: scored (fix2) and gate-rejected (fix) - both published

Per the pinned protocol, data was collected twice with **the same code**
(`b6878be`); criteria, thresholds and matrix unchanged.

**First collection** (`20260918T193132_7cd81c3-fix`, 19:31-19:43Z) was rejected by the
gate - **NO-GO**, two failures:

1. `first mask p99: +12.6%` (zig 842.3 vs xg 747.9 µs);
2. `s1_flat_enum b8: +6.5%` (medians n=8).

Evidence of degraded measurement conditions in the first collection:

- first-mask tails grew for **both** engines (p99 at p50: zig 842/465,
  xg 748/441 µs), whereas in the scored collection the zig tail is 712/467 and xg
  1536/487 µs - i.e. in the first collection noise hit zig, while in the scored one
  the picture is nominal (xg has a heavy p99 tail on this schema);
- in e2e **the second file** slowed down both engines: s1 b8 zig 0.403-0.407 s vs
  0.351-0.384 in the first file, xg 0.388 vs 0.351 (within-file spread ~1%);
  the same systematic across s1 b32 (file2 zig +22%);
- comparison with the sixth run: first-mask metrics for both engines in the first
  collection are elevated (both p50 and especially p99), while the primary mask metric
  stayed stable (0.78 vs 0.71 µs for zig; xg 3.08 vs 2.91 µs -
  a mild machine shift).

The first collection is preserved in full (`benchmarks/results/20260918T193132_7cd81c3-fix/`,
`environment.txt` added) and is published as is: its NO-GO is a fact, not a
hidden iteration. The second collection of the same code on a quiet machine was
recognized as scored; the decision was recorded here before the re-summary, the
protocol was not changed.

## 3. What changed since the sixth run (commit `b6878be`)

The wave closes four blockers of review `7cd81c3`. The mask/accept hot path was not
rewritten: F1/F3 change ownership and budgets around compilation, F2 - the HF
adapter token table.

1. **F1 - grammar ownership.** The context counts external references
   (`external_grammar_refs` - one per successful `zg_compile`), live sessions and
   in-flight calls (`active_calls`); the 'number of objects = number of cache refs'
   comparison removed. `e.close()` with a live `Constraint` no longer
   succeeds: BusyError; after `c.close()` the close succeeds. The concurrent-destroy
   contract is documented in the header.
2. **F2 - HF decoders.** ByteLevel fallback on a non-renderable character - at the
   level of the WHOLE token (`Ġhello🙂` stays `Ġhello🙂`; previously - ` hello🙂` and
   a false `completed=True`). The SP chain with `Strip` but without `Fuse` is rejected:
   without Fuse the space is stripped from every token (`'a','▁b' → 'ab'`), the core
   cannot express such a model. Tests compare the full `hf.decode`.
3. **F3 - honest artifact cache budget.** Entry cost is computed from actually
   retained bytes (GrammarMeter over the arena + handle + schema copy + list
   capacity); eviction happens before insertion, entries that do not fit are not
   cached; `zg_context_reset_cache` added. The review counterexample
   (600 schemas, memory_limit 262144, cache_limit 65536) after the fix: **15712 bytes
   retained against a 16384 budget** (was 255346), the big literal compiles.
4. **F4 - mandatory runs.** The C example switched to a byte-complete vocabulary
   (id 0..255 + structural tokens + EOS/PAD) and passes the full cycle;
   the fuzzer with the new invariant is green: campaigns A-D, seed 424242,
   `ALL CAMPAIGNS PASSED, total_iterations=890388`. The memory invariant replaced
   'zero memory' (did not distinguish a leak from cache retention) with a checkable
   one: after user references are dropped, retention ≤ artifact budget; after
   `reset_cache` - full return (grammar/session = 0).
5. **Gate (additional review items).** B1 requires ≥30 processes (a file with 1 process
   yields INCONCLUSIVE, not GO); e2e with `summary={status:ERROR}` without `total_s`
   yields a structured NO-GO (previously KeyError); the observation threshold is
   checked per class whose p99 participates in the decision (mask/accept/warm compile -
   ≥10k warm, cold classes - ≥30), warm compile accumulates ≥10k observations
   (`--min-warm-observations`). Gate regression tests: 20/20.

Tests at the time of the run: `zig build test [-Drelease=true]` - **119/119**;
`pytest tests/ python/tests/ -q` - **354/354** on both backends
(ctypes/package); `zig fmt --check` clean; C example exit 0; fuzzer - see above.

## 4. Uniform (big_enum_64, GPT-2 @607a30d7, ≥10k observations)

| engine | mask p50 | mask p95 | mask p99 | accept p50 | accept p99 | cold p50 | warm p50 | first mask p50 | prepare p50 |
|---|---|---|---|---|---|---|---|---|---|
| **zig_adaptive** | **0.47** | **0.72** | **0.79** | **0.31** | **0.48** | **120.3** | **0.95** | 466.8 | 60.3 ms |
| zig_lazy | 8.70 | 251.6 | 263.8 | 0.37 | 0.67 | 118.7 | 52.5 | 423.4 | 61.4 ms |
| xgrammar | 0.82 | 2.74 | 2.89 | 0.61 | 0.88 | 1218.2 | 2.05 | 487.2 | 89.2 ms |
| llguidance | 17.6 | 41.8 | 44.7 | 0.84 | 2.06 | 677.9 | 1.98 | 9968.1 | 288.3 ms |

µs (except prepare). Trace = canonical document `{"symbol":"sym_42","weight":0.5}` (14 tokens).
Other p99 vs xgrammar: accept 0.48/0.88, cold 265 vs 1819, warm
1.13 vs 2.41, first mask 712.1 vs 1535.7.

## 5. Secondary holdout (`secondary_geo_route`, GPT-2, 48-token trace)

| engine | mask p50 | mask p99 | accept p99 | cold p50 | warm p50 | first mask p50 | prepare p50 |
|---|---|---|---|---|---|---|---|
| **zig_adaptive** | **0.48** | **0.79** | **0.43** | **87.0** | **0.88** | 467.6 | 56.5 ms |
| zig_lazy | 8.75 | 80830 | 2.45 | 83.0 | 23.4 | 420.7 | 56.3 ms |
| xgrammar | 0.78 | 2570.3 | 1.27 | 40910.4 | 1.37 | 580.9 | 81.4 ms |
| llguidance | 13.0 | 358.6 | 1.92 | 134.6 | 1.24 | 8314.1 | 247.3 ms |

µs. Best competitor by mask p99 - llguidance; all other zig_adaptive latencies
≤ +10% vs it, primary metric +99.8%.

**Out-of-threshold observation (published):** for `zig_lazy` on the holdout mask p95/p99
≈ 79-81 ms - pathological no-cache states (adaptive closes them: 0.79 µs);
for xgrammar in the same place p95 1.16 ms, p99 2.57 ms. The scored engine is adaptive.

## 6. B1: 30 independent processes

| engine | ok | compile p50 ms | first mask p50 ms | total cold p50 s | peak RSS p50 MiB |
|---|---|---|---|---|---|
| **zig_adaptive** | 30 | **0.18** | 0.50 | **2.18** | 756 |
| xgrammar | 30 | 1.20 | 0.43 | 2.36 | 758 |
| llguidance | 30 | 0.28 | 1.33 | 2.15 | 772 |

## 7. B7 + guard (pool medians n=8; threshold 5%, worst zig / best xg)

| case | zig | xg | Δ | threshold |
|---|---|---|---|---|
| s1 b1 | 0.22 | 0.22 | -0.3% | OK |
| s1 b8 | 0.34 | 0.34 | +1.0% | OK |
| s1 b32 | 0.57 | 0.56 | +3.0% | OK |
| s2 b1 | 0.60 | 0.60 | +0.1% | OK |
| s2 b8 | 1.00 | 1.01 | -0.4% | OK |
| s2 b32 | 1.63 | 1.63 | -0.1% | OK |
| s3 b1 | 0.39 | 0.62 | -36.5% | OK |
| s3 b8 | 0.57 | 1.02 | -44.2% | OK |
| s3 b32 | 0.94 | 2.15 | -56.4% | OK |
| s4 b1 | 0.57 | 0.57 | -0.2% | OK |
| s4 b8 | 0.79 | 0.78 | +2.0% | OK |
| s4 b32 | 1.24 | 1.22 | +1.3% | OK |

Validity 100%, completed 100% in all configurations (per the matrix manifest).
The first request of each (schema, batch) pair is published separately in the `warmups` field.

## 8. Other

- **GPU mask intervals** (p50, µs): zig build 4.94 / H2D+unpack 24.07 / apply 18.76 /
  **full 47.75**; xgrammar 5.64/15.35/28.60/49.83; llguidance 32.09/20.92/71.88/126.09.
- **B5/B8** (90 072 steps, 11 112 cycles): plateau p99 0.991/1.015/1.003, RSS ratio 1.0,
  cache hits 99 993, evictions 0, errors 0 - no degradation.
- **B3/B6**: JSB-100 - ok 1 / unsupported 84 / invalid 15; corpus schemas supported 16;
  session phase fill_mask p50 6.34 / p99 89.66 µs; tokenizers: gpt2 prepare 54.5 ms,
  qwen2.5-1.5b 191.1 ms, tinyllama-sp 26.7 ms (SP-shim 400 pairs: 199 exact + 198
  first-space + 3 lossy-UTF-8, none rejected), synthetic up to 262144 tokens - nominal.

## 9. Published losses and weak spots

- `s1 b8`: scored collection +1.0%, but **the first collection of the same code gave
  +6.5% and failed the threshold** (§2) - the most background-sensitive case of the
  matrix (per-step tail of the Python processor vs the xgrammar C++ core; closing it
  requires moving mask application into the core - outside this wave). `s1 b32` +3.0% -
  second thinnest.
- `zig_lazy` on the holdout: mask p95/p99 79-81 ms - not the scored mode, but the fact
  is published together with the numbers.
- First mask: for zig p99 712 µs vs p50 467 (a tail), for xgrammar the tail is even
  heavier (1536 vs 487) - on this schema the first mask is tailed for both engines;
  the metric passes but is published in full.
- Secondary holdout: cold compilation zig 87.0 vs 134.6 µs for llguidance
  (best - zig), first mask 468 µs vs 8.3 ms for lg - but xgrammar has
  cold compilation 40.9 ms and prepare 81.4 ms; all metrics published.

## 10. Reproduction

```bash
export PYTHONPATH=python:benchmarks HF_HUB_OFFLINE=1
python3 benchmarks/run_all.py --tag 7cd81c3-fix2       # 12 cases, including *_secondary
D=benchmarks/results/20260918T194406_7cd81c3-fix2
python3 -u benchmarks/bench_e2e_constrained.py --out "$D"
python3 -u benchmarks/bench_e2e_constrained.py --out "$D/repeat_tmp"
mv "$D/repeat_tmp/bench_e2e_constrained.json" "$D/bench_e2e_constrained_repeat.json" && rmdir "$D/repeat_tmp"
python3 benchmarks/bench_gpu_mask_path.py > "$D/bench_gpu_mask_path.json"
python3 benchmarks/summarize_control.py "$D"           # exit 0 = GO
```

Tests at the time of the run: Zig 119/119 (Debug and ReleaseSafe); pytest 354/354 on
both backends; C example (exit 0); fuzzer seed 424242 - campaigns A-D passed.

## 11. Closing the review 7cd81c3 items

- **F1** (segfault due to the cache): closed - external references are counted separately;
  `e.close()` with a live `c` → BusyError, a second `c.close()` does not crash
  (regressions in Zig and Python, including concurrent compilation).
- **F2** (false completed=True): closed - mixed ByteLevel tokens and SP without
  Fuse; every `completed=True` is checked by tests against the actual
  `hf.decode`.
- **F3** (cache budget): closed - 15712/16384 bytes retained in the review
  counterexample, the big literal passes; `reset_cache` returns the memory
  (the fuzzer checks the invariant).
- **F4** (C example and fuzzer): closed, CI steps `Run C example` and `Fuzz campaign`
  pass (verified locally with the same commands).
- **Additional gate shortcomings**: closed (B1 ≥30 processes, e2e errors without
  `total_s`, per-class observations, ≥10k warm compile) - 20/20 gate tests.
- Remains outside the wave (review recommendation, not a blocker): moving mask
  application into the core for `s1 b8/b32`; checking s1 b8 robustness to background - §9.
