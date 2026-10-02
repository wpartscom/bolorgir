# Control run a855338-fix (fifth): A1-A5 + warm compile + e2e protocol

Date: 2026-09-18. Commits: work tree `9456aaa` + harness changes of this
run (see environment.txt). Data: 10 run_all cases, e2e ×2 with 4 repeats
per configuration (n=8 pool), GPU path; summary by `summarize_control.py`
(single gate, review R7+A5).

## 1. Verdict: NO-GO - one marginal failure

| Check | Result |
|---|---|
| Primary metric: mask p99 vs xgrammar | **passed: -74.5%** (0.74 vs 2.91 µs) |
| Other latencies ≤+10% (incl. warm compile) | **passed**: warm compile 1.03 µs vs 2.12 µs (-51%), cold 1054 vs 1182 µs |
| e2e guard (full matrix, n=8) | **8 of 9 passed**, all of s2 and s3; failure: `s1_flat_enum b32 +5.4% > 5%` |

The only blocker is `s1 b32`: zig 0.58 s vs xg 0.55 s (5% threshold).
The case is marginal historically too: perf-fix3 +5.2%, perf-fix4 (after the
e2e pair fix) -0.8%, now +5.4% - between-run spread ±3%, but within this
run the gap is stable (all 8 repeats 0.57-0.59 s vs 0.55-0.56 s).
The nature is the per-step tail of the HF adapter's Python processor (unpack+H2D+
`masked_fill_` over `[32, 151936]`), not the core: ITL p50 24.6 vs 22.9 ms at
equal tok/s ±5%. The primary metric of §10.6 is nevertheless passed with margin;
wording: "CPU speedup is confirmed; the product e2e threshold at b32
is not closed".

The previous verdict (perf-fix4, NO-GO due to warm compile and s2 spikes)
is substantively closed: warm compile is fixed (ADR-0004), s2 spikes are localized
and eliminated by the protocol (first-run mask cache misses are now published
separately in the `warmups` field, scoring is done in the warmed mode).

## 2. What changed since perf-fix4

- Correctness A1-A3 (commit c7a5d04): unproven schema/tokenizer pairs are
  rejected; added token bytes follow the actual decoder, decoder chains
  with order/multiplicity; a single final GenerationConfig.
- Build/gate A4-A5: `ZG_SKIP_ZIG` copies the fresh `_lib`; CI clean-build and
  HF-CPU job; the gate is structured, with the full matrix and the best competitor.
- Warm compile (9456aaa): cache of immutable artifacts - `compile(bytes)`
  1.03 µs vs 2.12 (xg) and 1.77 (lg); `dumps` (7.4 µs) stays with
  the caller, `warm_compile_dict_ns` = 8.93 µs is published separately.
- Harness: uniform measures warm compile on identically prepared input
  (bytes); e2e warms up each pair (schema, batch) on every engine, engines
  alternate, 4 repeats per file; first requests go to `warmups`.

## 3. Uniform (big_enum_64, GPT-2, ≥10k observations)

| engine | mask p99 µs | accept p99 µs | cold p50 µs | warm (bytes) p50 µs | first mask p50 µs |
|---|---|---|---|---|---|
| **zig_adaptive** | **0.74** | 0.47 | 1054 | **1.03** | 486 |
| zig_lazy | 253.5 | 0.63 | 1074 | 57.4 | 458 |
| xgrammar | 2.91 | 0.97 | 1182 | 2.12 | 446 |
| llguidance | 43.9 | 1.92 | 779 | 1.77 | 9137 |

## 4. B1: 30 independent processes

| engine | ok | compile p50 ms | first mask p50 ms | total cold p50 s | peak RSS p50 MiB |
|---|---|---|---|---|---|
| zig_adaptive | 30 | 1.01 | 0.49 | 2.27 | 759 |
| xgrammar | 30 | 1.19 | 0.42 | 2.44 | 761 |
| llguidance | 30 | 0.27 | 1.30 | 2.41 | 776 |

## 5. B7 + guard (n=8 pool medians)

| case | zig s | xg s | Δ | threshold |
|---|---|---|---|---|
| s1 b1 | 0.23 | 0.23 | +1.4% | OK |
| s1 b8 | 0.36 | 0.34 | +3.6% | OK |
| s1 b32 | 0.58 | 0.55 | **+5.4%** | **FAIL** |
| s2 b1 | 0.64 | 0.64 | +1.0% | OK |
| s2 b8 | 1.04 | 1.01 | +2.5% | OK |
| s2 b32 | 1.70 | 1.68 | +1.3% | OK |
| s3 b1 | 0.42 | 0.66 | **-36.2%** | OK |
| s3 b8 | 0.60 | 1.07 | **-44.0%** | OK |
| s3 b32 | 1.03 | 2.29 | **-54.9%** | OK |

Validity 100% in all configurations. First-run spikes (up to +0.6 to +3 s)
are eliminated by the protocol: after warmup the repeat spread is ≤2%. The "first request"
is published separately in `warmups` of each e2e file.

Analysis of s1 b32: the ~+5% gap is stable regardless of run index and engine
order; ITL p50 24.6 vs 22.9 ms (~+0.25 ms/step), while xg masks with a
34 µs bitmask and our adapter does unpack+H2D+a full `masked_fill_`.
This is adapter overhead; the fix is to optimize the per-step processor tail
(a separate task, also affecting s2 b32).

## 6. Other

- B5/B8 (90 072 steps): plateau p99 0.99-1.01, RSS 1.00, 0 errors, 99 993
  cache hits, 0 evictions (all budgets).
- B3: adaptive repeat compile p50 7.7 µs (including dumps), lazy 39.9 µs;
  the artifact core is 1.03 µs on prepared input.
- B6: prepare gpt2 52.8 ms; mask p50 0.42-1.1 µs across vocabularies up to 262k.
- GPU path (build/H2D/apply/full, p50 µs): zig **5.1/25.0/19.2/49.4**,
  xg 6.1/16.2/30.3/52.7, lg 34.6/21.2/77.2/135.8 - zig wins ~6% on the full
  interval and on H2D+apply vs xg.

## 7. Reproduction

```bash
zig build -Drelease=true -Dcpu=baseline && cd python && ZG_SKIP_ZIG=1 python3 setup.py build_ext --inplace && cd ..
PYTHONPATH=python:benchmarks python3 benchmarks/run_all.py --tag <t>
PYTHONPATH=python:benchmarks HF_HUB_OFFLINE=1 python3 benchmarks/bench_e2e_constrained.py --out <dir>/bench_e2e_constrained.json
PYTHONPATH=python:benchmarks HF_HUB_OFFLINE=1 python3 benchmarks/bench_e2e_constrained.py --out <dir>/bench_e2e_constrained_repeat.json
PYTHONPATH=python:benchmarks python3 benchmarks/bench_gpu_mask_path.py > <dir>/bench_gpu_mask_path.json
PYTHONPATH=python:benchmarks python3 benchmarks/summarize_control.py <dir>   # exit 0=GO, 1=NO-GO, 2=INCONCLUSIVE
```

Note: for e2e, `--out` is a directory; the file is placed inside as
`bench_e2e_constrained.json`, then renamed to `..._repeat.json`.

## 8. Next steps (to GO)

1. Optimize the per-step tail of the HF processor (b32: +5.4%, b8 +3.6%):
   reduce `masked_fill_` over the full vocabulary and extra per-step allocations;
   target - ITL parity with xgrammar on s1 b32.
2. A secondary unused corpus (review, item 5): the current control
   scenario has been used repeatedly during optimizations.
3. Repeat the control run after items 1-2 with the same pinned criterion.
