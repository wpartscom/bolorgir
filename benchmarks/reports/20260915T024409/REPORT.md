# Control run §10.6 / §13.2 - 2026-09-15

Closing of 7 items of the "Why the comparisons do not yet close §10 and §13.2"
section of review 2026-09-15.

- Configuration: seed 42; CPU i7-12700F, 62 GiB RAM; GPU RTX 3060 12 GB
  (driver 580.178.04, CUDA 13.0); Ubuntu 22.04, python 3.10.12, zig 0.15.2
  ReleaseSafe; torch 2.14.0+cu130, transformers 5.17.0, tokenizers 0.23.2,
  xgrammar 0.2.6, llguidance 1.8.0, sentencepiece 0.2.2.
- Tree without git (not a repository): sha256 snapshot of all sources and .so files -
  `environment.txt`.
- Comparison tokenizer: openai-community/gpt2 @607a30d7 (byte-level BPE,
  50257). B7 model: Qwen/Qwen2.5-1.5B-Instruct @989aa798, fp16, greedy,
  max_new_tokens=512. Second family: TinyLlama/TinyLlama-1.1B-Chat-v1.0
  @fe8a4ea1 (Llama, SP/BPE byte_fallback) - see item 7.
- The control scenario and the tuning/holdout split are pinned in
  `benchmarks/manifest.json` (control_scenario) BEFORE this run:
  schema big_enum_64 (holdout), trace `{"symbol":"sym_42","weight":0.5}`
  (14 GPT-2 tokens), metric p99 fill_mask, threshold -20% vs the best of
  {xgrammar, llguidance} with other latencies degraded ≤10% and
  e2e regression ≤5% vs the constrained baseline (xgrammar).

## Verdict §10.6: **NO-GO** - 'correct prototype, performance hypothesis not confirmed'

Grounds (control scenario, bench_compare_uniform.json):

- primary metric: p99(fill_mask) zig_adaptive = **3.11 µs** vs
  **2.87 µs** for the best competitor (xgrammar) - worse by 8.2% instead of a
  ≥20% reduction. Against llguidance (43.66 µs) the reduction is 92.9%, but the rule
  is evaluated against the best of the two external engines;
- additional conditions vs xgrammar are also not met:
  accept p50 +993%, accept p99 +10135%, cold compile +2504%, first mask
  +74%, mask p95 +12.5% (with a +10% threshold; mask p50 -32% - in zig's favor);
- e2e guard: tok/s regression vs the constrained baseline xgrammar >5% in
  6 of 9 configurations (s1/s2 at batch 8/32: +21 to +35%), zig ITL p50 worse
  in all configurations with batch ≥ 8 (see the B7 table).

All discrepancies with both competitors are published below (SPEC 10.6).

## Answers to the 7 review items

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | tuning/holdout split, control scenario, Go/No-Go metric | **closed** | manifest.json: `corpus.tuning_holdout_split` (even/odd over sorted names, pinned 2026-09-15), `control_scenario` (big_enum_64 schema from the holdout, mask p99 metric, thresholds 20%/10%/5%, e2e guard), reference to this run + verdict |
| 2 | Load comparability | **closed** | bench_compare_uniform.py: all three engines on the same tokenizer (GPT-2 via TokenizerBundle.from_hf), the same schema and ONE trace (acceptance of every token confirmed by accept/consume of all three); ≥10k warm observations per engine. Result: `bench_compare_uniform.json` |
| 3 | B1 - 30 independent processes | **closed** | bench_coldproc.py: 30 consecutive separate processes per engine, 30/30 OK for all three. `bench_coldproc.json` |
| 4 | B7 - constrained baseline | **closed** | bench_e2e_constrained.py: zig vs xgrammar constrained (NOT unconstrained), GPU, Qwen2.5-1.5B fp16, batch 1/8/32, 3 repeats; 369/369 valid responses for both. `bench_e2e_constrained.json` |
| 5 | GPU mask-interval matrix | **closed** | bench_gpu_mask_path.py: build/H2D+unpack/apply/full on the 151k Qwen vocabulary, cuda-synchronize around the stages, 500 steps per engine ×3 engines. `bench_gpu_mask_path.json` |
| 6 | B5/B8 - honest long-run generation | **closed** | bench_longrun.py: full trace (fill_mask+accept on every token) on every cycle; 90 072 counted mask steps × 3 limits (≈300k generated in total), 11 112 create/destroy cycles per limit; p99 over windows and RSS plateau; core stats before/after. `bench_longrun.json` |
| 7 | B3/B6 + second tokenizer family | **partial** | B3 closed (bench_schemas_tokenizers.py: schema switching, mask cache hit rate 100% on repeats, JSB-100 support report). B6: GPT-2/Qwen/synthetic 32k/128k/256k closed; **the second family (SentencePiece) is NOT closed - the adapter was rejected at certification** (see below). The CPython 3.10+ matrix is outside the benchmark scope |

## Item 2 - identical load (bench_compare_uniform.json)

Schema big_enum_64, 14 GPT-2 tokens trace, ≥10k observations/engine, µs:

| engine | mask p50 | mask p95 | mask p99 | accept p50 | accept p99 | cold compile p50 | first mask p50 | warm compile p50 |
|---|---|---|---|---|---|---|---|---|
| zig_lazy | 987.5 | 28119 | 29223 | 8.23 | 156.6 | 29569 | 798 | 990 |
| zig_adaptive | **0.57** | 3.03 | 3.11 | 6.73 | 107.5 | 30172 | 834 | 961 |
| xgrammar | 0.83 | **2.70** | **2.87** | **0.62** | **1.05** | **1159** | **478** | **2.12** |
| llguidance | 17.16 | 40.93 | 43.66 | 0.75 | 1.92 | 704 | 8128 | 1.78 |

Conclusions: zig_adaptive beats llguidance on the mask (p99 -92.9%) and beats everyone
on mask p50; loses to xgrammar on mask p95/p99 (within 8-12%) and
drastically on accept (6.7 µs vs 0.62 µs) and cold compile (30 ms vs
1.2 ms: compilation includes building transitions over the whole 50k vocabulary).
zig_lazy - the exact no-cache path: 29 ms p99 (plateau of the miss cost).

Noted property of llguidance 1.8.0 (important for interpreting its numbers): inside
forced literals its bitmask contains only the 'greedy' spelling
(after `'{"'` only the token `'action'` is allowed, although `consume_token('act')`
succeeds and continues with `'ion'`). Trace acceptance for llguidance is checked via
consume_token, not via the mask bit; the mask is measured as the stock production path.

## Item 3 - B1, 30 independent processes (bench_coldproc.json)

Schema big_enum_64, consecutive separate processes, p50 of 30:

| engine | ok | compile, ms | first mask, ms | total cold, s | peak RSS, MiB |
|---|---|---|---|---|---|
| zig_adaptive | 30/30 | 28.37 | 0.83 | 2.36 | 762 |
| xgrammar | 30/30 | 1.19 | 0.42 | 2.58 | 762 |
| llguidance | 30/30 | 0.29 | 1.35 | 2.49 | 777 |

Process memory is ≈ the same for all (dominated by the transformers/torch import):
there is no 20% peak-memory advantage for zig (the second §10.6 alternative
is also not confirmed). Process errors: 0.

## Item 4 - B7 constrained vs constrained baseline (bench_e2e_constrained.json)

Qwen2.5-1.5B-Instruct fp16, RTX 3060, greedy, 3 repeats, medians;
baseline = xgrammar constrained. Validity: 369/369 for both engines.

| schema | batch | mode | total, s | tok/s | ITL p50, ms | ITL p99, ms | lengths | tok/s regression |
|---|---|---|---|---|---|---|---|---|
| s1 | 1 | zig / xg | 0.23 / 0.23 | 69.1 / 69.7 | 14.1 / 13.5 | 15.5 / 16.0 | 16 / 16 | +0.9% |
| s1 | 8 | zig / xg | 0.44 / 0.33 | 295 / 398 | 22.4 / 15.8 | 29.3 / 17.3 | 14..18 / 14..18 | **+25.9%** |
| s1 | 32 | zig / xg | 0.79 / 0.54 | 658 / 955 | 37.2 / 21.2 | 43.0 / 23.1 | 14..18 / 14..18 | **+31.1%** |
| s2 | 1 | zig / xg | 0.68 / 0.59 | 64.7 / 74.8 | 14.8 / 13.2 | 20.0 / 13.7 | 44 / 44 | **+13.5%** |
| s2 | 8 | zig / xg | 1.19 / 0.94 | 282 / 356 | 20.4 / 16.2 | 23.0 / 18.8 | 34..55 / 34..55 | **+20.9%** |
| s2 | 32 | zig / xg | 2.17 / 1.40 | 620 / 957 | 37.6 / 22.1 | 49.2 / 33.4 | 34..55 / 34..55 | **+35.2%** |
| s3 | 1 | zig / xg | 0.42 / 0.62 | 66.4 / 67.8 | 14.7 / 13.4 | 17.8 / 21.5 | 28 / 42 | +2.0% |
| s3 | 8 | zig / xg | 0.68 / 0.99 | 327 / 252 | 21.7 / 16.9 | 24.0 / 57.2 | 26..29 / 26..42 | -29.7% (zig faster) |
| s3 | 32 | zig / xg | 1.37 / 2.13 | 646 / 470 | 40.9 / 23.7 | 52.1 / 190.9 | 26..29 / 26..42 | -37.6% (zig faster) |

Reading the table: on s1/s2 (required fields, deterministic structure)
zig is consistently slower per step at batch ≥ 8 - the bottleneck: the row-wise
Python loop of the adapter with CPU unpacking of the 151k-vocabulary mask per row per
step (see item 5: ~577 µs/row → ~18 ms at b32). On s3 xgrammar shows
ITL p99 spikes (57-191 ms) and longer responses - zig is faster wall-clock, although
its ITL p50 is still worse. TTFT and VRAM are equal for both (±10 ms, ±2 MiB).
e2e guard result: regression >5% in 6/9 configurations - **not passed**.

## Item 5 - GPU mask intervals (bench_gpu_mask_path.json)

Qwen vocabulary 151665, logits 151936 (padded lm_head), p50 of 500 steps, µs:

| engine | build | H2D (+unpack) | apply | full path |
|---|---|---|---|---|
| zig_adaptive | 16.3 | **576.7** | 96.1 | 694.0 |
| xgrammar | 5.7 | 15.0 | 28.3 | 49.2 |
| llguidance | 33.0 | 20.1 | 72.0 | 126.9 |

For zig the bottleneck is not the core (build 16 µs) but the adapter: uint32→bool
unpacking via `torch.arange(vocab)` on CPU + transfer - 577 µs/step
(stock path transformers.py:138-142). This is the source of the e2e regression.
For the competitors the bitmask is transferred compactly (int32 words) and applied by a
cuda kernel. Candidate optimization of the adapter (transfer words + bitwise
on GPU); the core does not need changes.

## Item 6 - B5/B8 honest long-run generation (bench_longrun.json)

Real steps: full trace fill_mask+accept on every session cycle,
3 rotating schemas, GPT-2. Per limit: ~100k generated steps
(90 072 counted across 9 full windows; the trailing incomplete window is not counted),
11 112 create/destroy cycles; across limits in total ≈300k steps and 33 336
cycles (SPEC B8: ≥100k and ≥10k - met).

| limit | errors | mask p99 over windows, µs | RSS | cache hits / evictions |
|---|---|---|---|---|
| 64 MiB | 0 (incl. ResourceLimit 0) | 0.71-0.75 (plateau, ratio 1.01) | stable byte-for-byte | 99 999 / 0 |
| 128 MiB | 0 | 0.72-1.02, one spike 1.62 (noise; ratio 1.35 because of it) | stable | 99 999 / 0 |
| 256 MiB | 0 | 0.73-0.89 (ratio 1.007) | stable | 99 999 / 0 |

The memory and latency plateau is confirmed; the core mem_used after the run
equals the initial value (no leaks), the cache works (hits ≈ steps).

## Item 7 - B3/B6 and the second family (bench_schemas_tokenizers.json)

B3 (byte-level vocabulary; the cache is about schemas, not the vocabulary):
- JSB-100 (pinned commit ba103c73): MVP language support 1/100
  (84 UnsupportedFeature, 15 InvalidSchema - raw JSB schemas are largely outside
  the MVP; this is the support report per SPEC 10.3, not a benchmark defect);
- switching 17 supported corpus schemas in a fixed order, 3 rounds:
  compile p50 ≈ 43-51 µs (lazy) / 41-43 µs (adaptive); session phase:
  round 0 - 359 misses (warmup), rounds 1-2 - **hit rate 100%**
  (429/429, evictions 0), mask p50 0.58 µs.

B6 (prepare / compile / mask / core memory):

| tokenizer | family | vocab | prepare, ms | compile, ms | mask p50, µs | core mem, MiB |
|---|---|---|---|---|---|---|
| GPT-2 | byte-level BPE | 50257 | 50.0 | 29.0 | 0.55 | 34.3 |
| Qwen2.5-1.5B | byte-level BPE | 151665 | 181.9 | 108.7 | 0.88 | 134.6 |
| synthetic | byte | 32768 | ~0 | 16.9 | 0.51 | 39.6 |
| synthetic | byte | 131072 | ~0 | 78.9 | 0.74 | 134.5 |
| synthetic | byte | 262144 | ~0 | 178.9 | 1.15 | 287.0 |
| TinyLlama | SP/BPE byte_fallback | 32000 | 34.3 | - | - | - |

**The second family is NOT closed - the adapter was rejected at certification.**
transformers 5.17 loads Llama tokenizers with the slow class without the
`byte_fallback` attribute; with an explicit flag (shim) the package builds a bundle, but
pairwise byte concatenation vs `backend.decode` gives 25/400
discrepancies (6.25%) not explained by the leading `▁` rule: tokens with
characters U+00A0-U+00FF (e.g. `iné` → `in\xE9` instead of UTF-8 `in\xC3\xA9`)
are decoded by the byte-level BPE branch before the SP branch
(python/zig_constraints/tokenizers.py: branch order in `_build_from_hf`).
Per the SPEC risk table ('wrong byte representation → reject')
the TinyLlama/SP adapter is rejected; measurements on it are not published as
certified. A package fix is required (SP branch before byte-level
under byte_fallback) - outside the benchmark scope. Also: for SP the first
token of a stream loses the leading `▁` (195/400 pairs) - recorded as a
document-start rule for future integration.

## Raw data and reproduction

- `environment.txt` - tree sha256, versions; `bench_*.json` - raw
  observations/aggregates; `bench_e2e_constrained.json` - all 54 runs
  with response texts and lengths.
- Reproduction (from the root): `PYTHONPATH=python:benchmarks python3
  benchmarks/run_all.py` (all CPU cases, ~15 min) and individually -
  commands in the scripts' docstrings; summarize_control.py prints the summary
  and recomputes the Go rule.
- smoke of the existing scripts after the edits: `run_all.py --tag smoke`
  (directory `../20260915T025723_smoke/`, 10/10 cases OK) - the old scripts are
  not broken (run_all.py edits: new SCRIPTS entries and timeout 300→1800 s
  for the 30×3 coldproc processes).

## Deviations and caveats

- The NO-GO verdict follows the pre-pinned metric; secondary results
  (win vs llguidance on the mask, vs xgrammar on e2e for s3, B5/B8
  stability) are published but do not change the outcome (SPEC 10.6: one cannot
  pick only the winning example after the tests).
- B3/B5/B8 and B6 synthetic use a byte-level vocabulary - these are cases about
  cache/memory/core durability; speed comparison with competitors
  - only items 2/4/5 on real vocabularies.
- Zig measurements include the package's Python bridge (the stock user path);
  the bare core is faster (build 16 µs vs 577 µs for the full adapter path
  on the 151k vocabulary) - visible in item 5 and the main optimization
  candidate.
- The second tokenizer family, the CPython 3.10+ matrix, and the full JSONSchemaBench
  (9558 schemas) are not closed (reasons above); the MaskBench benchmark was not run.
