# Control run d84f992-fix (eighth): two-collection protocol, D1/D3/D6/D2

Date: 2026-09-19. Code commit: `e4963ae` (wave `d84f992-fix`; same code in
both collections). Data: two full collections of one code
(`20260918T211014_d84f992-fix` and `20260918T212317_d84f992-fix2`), each -
12 `run_all` cases (including secondary runs), e2e ×2 with 4 repeats per
configuration (pool n=16), GPU path, `environment.txt` with machine
conditions. Merging - per the protocol pinned before the run
`manifest.json#control_scenario.acceptance_protocol`: both collections are merged
WITHOUT discarding (uniform - worst zig / best competitor; e2e - repeat
pool with medians; B1 - in each collection). Thresholds 20%/10%/5% unchanged.

## 1. Verdict: **NO-GO** - one marginal e2e case

| Check | Result |
|---|---|
| Primary metric: mask p99 vs xgrammar | **passed: +72.6%** (0.77 vs 2.82 µs; threshold +20%) |
| Other latencies ≤+10% vs the same competitor | **passed** (first mask p50 +9.7% - thin margin, p99 -0.5%; accept p99 -44%, cold -90%, warm -53%) |
| Secondary holdout (`secondary_geo_route`) | **passed**: mask p99 0.83 vs 357.37 µs for llguidance (+99.8%), others ≤+10% |
| e2e guard: matrix s1-s4 × b1/8/32, pool medians n=16 | **11/12**: `s1_flat_enum b8 +6.9% > 5%` - FAIL (see §7) |
| B1: 30 independent processes | 30/30 in all three engines |
| B5/B8 (90 072 steps; 64/128/256 MiB) | no degradation (plateau 0.998/0.996/0.943, RSS ratio 1.0) |
| Class observations (review 7cd81c3) | satisfied (the gate returned no INCONCLUSIVE) |

## 2. Two collections of one code - both published (protocol D2)

Unlike the seventh run (where the first collection was deemed corrupted and
repeated), here **both collections are scored and merged without discarding** -
this is exactly the rule pinned in `acceptance_protocol` before the run. No
collection was rejected based on measurement results; rejection is structural
only (status/files/observations). Machine conditions are comparable:

| | `d84f992-fix` | `d84f992-fix2` |
|---|---|---|
| date (UTC) | 2026-09-19T04:22:54Z | 2026-09-19T04:34:54Z |
| loadavg 1/5/15 | 1.66 / 1.86 / 1.41 | 1.98 / 2.50 / 1.96 |
| MemAvailable | 47 029 804 kB | 47 008 792 kB |
| uptime | 20 392 s | 21 113 s |

Collection 1: `benchmarks/results/20260918T211014_d84f992-fix/` (12/12 OK,
e2e ×2, GPU, environment.txt). Collection 2:
`benchmarks/results/20260918T212317_d84f992-fix2/` (same). Commit in both -
`e4963ae`, tree clean.

## 3. What changed since the seventh run (commit `e4963ae`)

The mask/accept hot path is unchanged. The wave closes four items of
review d84f992:

1. **D1 - missing decoder with fast HF.** `tokenizers.py` distinguishes
   `no_backend/missing/unreadable/ok`; a fast backend without a proven decoder
   (tokens are joined with spaces) or with unreadable state →
   `UnsupportedTokenizerError` before generation (a false `completed=True`
   is no longer possible). Tests +3.
2. **D3 - validation of every uniform source.** `summarize_control.py`
   checks every present uniform file (status/engines/metrics/
   raw observations) before merging; a repeat with empty `raw_ns` → INCONCLUSIVE
   (previously it kept GO). Tests +3.
3. **D6 - concurrent destroy in flight.** The test suspends `zg_compile`
   right after `active_calls += 1` and checks BUSY before and after
   completion (external reference); the BUSY contract in the header was fixed.
4. **D2 - two-collection protocol.** The number of collections (2), the merge
   rule (worst zig / best competitor across all sources, e2e - repeat pool) and
   objective rejection (structural only) are pinned in the manifest before
   the run.

Tests at the time of the wave: Zig 120/120 (Debug/ReleaseSafe), pytest 360/360 on
both backends (ctypes/package), C example exit 0, fuzzer A-D
(seed 424242, 890 397 iterations) - passed.

## 4. Uniform (big_enum_64, GPT-2 @607a30d7, ≥10k observations)

Merge worst zig / best competitor over the two collections (µs, except prepare):

| engine | mask p50 | mask p95 | mask p99 | accept p99 | cold p50 | warm p50 | first mask p50 | first mask p99 |
|---|---|---|---|---|---|---|---|---|
| **zig_adaptive** | 0.44 | 0.65 | **0.77** | **0.49** | **119.98** | **0.95** | 473.85 | 849.07 |
| zig_lazy | 8.86 | 252.0 | 267.31 | 0.85 | 119.13 | 51.75 | 421.9 | - |
| xgrammar | 0.83 | 2.84 | 2.82 | 0.88 | 1185.12 | 2.01 | 431.79 | 853.65 |
| llguidance | 16.83 | 40.7 | 43.51 | 1.88 | 671.75 | 1.79 | 8604.97 | 11091.99 |

Primary metric: mask p99 0.77 vs 2.82 µs for xgrammar (+72.6%).
Other latencies vs xgrammar: accept p99 0.49/0.88 (-44%), cold 119.98/1185.12,
warm 0.95/2.01, first mask p50 473.85/431.79 (+9.7%, threshold +10%),
first mask p99 849.07/853.65 (-0.5%). Trace = canonical document
`{"symbol":"sym_42","weight":0.5}` (14 tokens).

## 5. Secondary holdout (`secondary_geo_route`, GPT-2, trace 48 tokens)

Merge worst zig / best competitor (µs):

| engine | mask p50 | mask p99 | accept p99 | cold p50 | warm p50 | first mask p50 | first mask p99 |
|---|---|---|---|---|---|---|---|
| **zig_adaptive** | 0.48 | **0.83** | **0.44** | **84.90** | **0.91** | 458.60 | 611.87 |
| xgrammar | 0.78 | 2568.85 | 1.31 | 40725.26 | 1.43 | 575.72 | 681.52 |
| llguidance | 13.0 | 357.37 | 1.84 | 170.59 | 1.21 | 8388.55 | 10329.24 |

Best competitor by mask p99 is llguidance; primary metric +99.8%, other
latencies of zig_adaptive ≤ +10% vs it.

## 6. B1: 30 independent processes (in each collection)

| engine | ok | compile p50 ms | first mask p50 ms | total cold p50 s | peak RSS p50 MiB |
|---|---|---|---|---|---|
| **zig_adaptive** | 30 | **0.17** | 0.49 | **2.14** | 756 |
| xgrammar | 30 | 1.18 | 0.43 | 2.43 | 758 |
| llguidance | 30 | 0.29 | 1.38 | 2.30 | 772 |

## 7. B7 + guard (pool medians n=16; threshold 5%)

| case | zig | xg | Δ | threshold |
|---|---|---|---|---|
| s1 b1 | 0.23 | 0.23 | -0.9% | OK |
| **s1 b8** | **0.38** | **0.36** | **+6.9%** | **FAIL** |
| s1 b32 | 0.61 | 0.59 | +2.6% | OK |
| s2 b1 | 0.67 | 0.67 | +0.2% | OK |
| s2 b8 | 1.12 | 1.11 | +0.8% | OK |
| s2 b32 | 1.81 | 1.80 | +0.5% | OK |
| s3 b1 | 0.43 | 0.68 | -36.8% | OK |
| s3 b8 | 0.61 | 1.10 | -44.4% | OK |
| s3 b32 | 1.05 | 2.33 | -54.8% | OK |
| s4 b1 | 0.63 | 0.63 | -0.4% | OK |
| s4 b8 | 0.86 | 0.85 | +0.9% | OK |
| s4 b32 | 1.37 | 1.35 | +1.2% | OK |

Validity 100%, completed 100% in all configurations. The first request of each
pair (schema, batch) is published separately in the `warmups` field.

**The `s1 b8` failure is reproducible and structural.** File-level total_s
medians (zig / xg, s):

| file | zig | xg | Δ |
|---|---|---|---|
| fix/bench_e2e_constrained.json | 0.369 | 0.344 | +7.3% |
| fix/bench_e2e_constrained_repeat.json | 0.386 | 0.383 | +0.8% |
| fix2/bench_e2e_constrained.json | 0.357 | 0.344 | +3.8% |
| fix2/bench_e2e_constrained_repeat.json | 0.378 | 0.375 | +0.8% |

The picture is the same as in the seventh run: in the **first** e2e file of each
collection xgrammar runs at ~0.344 s, and the per-step overhead of the HF adapter's
Python processor for zig adds +3.8 to +7.3%; in the **repeat** file both engines slow
down (xg to 0.375-0.383 s), and the relative gap shrinks to +0.8%. The n=16 pool
median gives +6.9% - above the 5% threshold. Closing it requires moving mask
application into the core (outside this wave).

## 8. Other

- **GPU mask intervals** (p50, µs): zig build 5.04 / H2D+unpack 25.34 /
  apply 19.42 / **full 50.03**; xgrammar 5.99/16.09/30.48/52.64;
  llguidance 34.26/21.94/75.49/134.25.
- **B5/B8** (90 072 steps, 11 112 cycles): plateau p99 0.998/0.996/0.943,
  RSS ratio 1.0, cache hits 99 993, evictions 0, errors 0 - no degradation.
- **B3/B6**: JSB-100 - ok 1 / unsupported 84 / invalid 15; corpus schemas
  supported 16; session phase fill_mask p50 6.51 / p99 93.0 µs; tokenizers:
  gpt2 prepare 57.1 ms, qwen2.5-1.5b 207.3 ms, tinyllama-sp 26.1 ms
  (sp_shim 400 pairs: 199 exact + 198 first-space + 3 lossy-UTF-8, none
  rejected), synthetic up to 262 144 tokens - OK.

## 9. Published losses and weak spots

- **`s1 b8`: +6.9% (5% threshold failure)** - the only verdict blocker.
  Structural per-step overhead of the HF adapter's Python processor at batch 8
  vs the C++ xgrammar core (see §7); it flaps between runs in the
  +0.8 to +7.3% range depending on machine background load. It is the same case that gave +6.5%
  in the corrupted collection of the seventh run and +4.3% (pass) in the sixth.
- `s1 b32`: +2.6% - second-thinnest margin; it passed here.
- First mask p50 in the primary scenario: +9.7% (threshold +10%) - thin margin,
  published in full; p99 is -0.5%.
- `zig_lazy` on the holdout: mask p95/p99 ~80 ms - outside the scored mode
  (adaptive is 0.83 µs); the fact is published.

## 10. Reproduction

```bash
export PYTHONPATH=python:benchmarks HF_HUB_OFFLINE=1
python3 benchmarks/run_all.py --tag d84f992-fix       # collection 1: 12 cases
D=benchmarks/results/20260918T211014_d84f992-fix
python3 -u benchmarks/bench_e2e_constrained.py --out "$D"
python3 -u benchmarks/bench_e2e_constrained.py --out "$D/repeat_tmp"
mv "$D/repeat_tmp/bench_e2e_constrained.json" "$D/bench_e2e_constrained_repeat.json" && rmdir "$D/repeat_tmp"
python3 benchmarks/bench_gpu_mask_path.py > "$D/bench_gpu_mask_path.json"
# ...environment.txt (machine conditions + sha256 of artifacts/sources)...

python3 benchmarks/run_all.py --tag d84f992-fix2      # collection 2 (likewise)
D2=benchmarks/results/20260918T212317_d84f992-fix2
# ...e2e ×2, gpu_mask_path, environment.txt...

# combined gate (two directories, exit 1 = NO-GO):
python3 benchmarks/summarize_control.py "$D" "$D2"
```

## 11. Closing the d84f992 review items

- **D1** (false completed=True with decoder=None): closed - a fast backend without
  a proven decoder is rejected (`UnsupportedTokenizerError`); the review
  counterexample is rejected in all modes while the ByteLevel control passes.
- **D3** (the gate checked only the first uniform file): closed - every
  present source is validated; a repeat with empty `raw_ns` →
  INCONCLUSIVE.
- **D6** (the destroy test waited for compile completion): closed - the test exercises the
  `active_calls` branch (BUSY in flight and while the handle is held).
- **D2** (two-collection protocol): applied - the verdict is based on the union
  of both collections without discarding. Result - NO-GO due to `s1 b8` (+6.9% > 5%);
  thresholds unchanged, both collections published in full.

Left outside the wave (review recommendation, not a blocker): moving mask
application into the core for `s1 b8/b32` (closes the systematic per-step adapter overhead).
