# Control run §10.6 / §13.2 - 2026-09-17 (third, perf-fix3)

Third control run after two waves of performance fixes:

1. **Wave 2** (run `20260917T184826_perf-fix2`): 0xAA fill of `undefined`
   in `parser.feedBytes` (per-call and per-iteration memset ~80 KiB, accept up to
   ~100 µs on multithread states) and byte-wise FNV in `parser.hashState`.
2. **Wave 3** (this run): two structural defects that remained formal reasons
   for NO-GO:
   - **`lit_trie` (ADR-0002)** - enum/const/FR-3/boolean compile not into a
     `choice` of `literal` nodes but into a shared prefix trie
     (`grammar.Node.lit_trie`); the common prefix of alternatives is advanced by
     a single parser thread. Previously: an enum of 64 values carried 64 threads,
     accept inside the common prefix cost ~2 µs (accept p95/p99 +143%/+126% vs
     xgrammar).
   - **Coverage (FR-6) without an O(vocab) index** (`src/coverage.zig`): the
     segmentation DP enumerates candidate tokens by walking the vocabulary trie
     (binary search over sorted edges) instead of building a "first byte →
     token list" index on **every** compilation (for the GPT-2 vocabulary this
     is ~815 µs out of ~1 ms of cold compilation).

- Configuration: seed 42; same machine and pinned versions
  (`environment.txt`: zig 0.15.2 ReleaseSafe, python 3.10.12, torch 2.14.0+cu130,
  transformers 5.17.0, tokenizers 0.23.2, xgrammar 0.2.6, llguidance 1.8.0,
  numpy 1.26.4; RTX 3060). Scenario and thresholds - from `manifest.json`
  (`control_scenario`), pinned 2026-09-15 and unchanged.
- Tests before the run: core 106/106 (Debug and ReleaseSafe, +4 `lit_trie` tests,
  +1 mask-equivalence test "choice ↔ lit_trie" on random vocabularies),
  Python 296/296 on two backends (`ctypes`, `package`); fuzz campaigns A-D
  passed (seed 424242 and the default one), see "Fuzz" below.

## Verdict §10.6

**GO.** All eight checks of the control scenario passed completely for the first
 time, including accept p95/p99 and cold compilation:

| Check | zig_adaptive | xgrammar (best) | Δ | Threshold | Outcome |
|---|---|---|---|---|---|
| **p99 fill_mask (main)** | **0.81 µs** | 2.96 µs | **-72.8%** | ≥ -20% | pass |
| p50 fill_mask | 0.45 µs | 0.81 µs | -44.6% | ≤ +10% | pass |
| p95 fill_mask | 0.68 µs | 2.75 µs | -75.4% | ≤ +10% | pass |
| p50 accept_token | 0.31 µs | 0.61 µs | -49.5% | ≤ +10% | pass |
| p95 accept_token | 0.40 µs | 0.86 µs | -53.7% | ≤ +10% | pass |
| p99 accept_token | 0.46 µs | 0.93 µs | -50.2% | ≤ +10% | pass |
| cold compile p50 | 1116 µs | 1211 µs | -7.8% | ≤ +10% | pass |
| first mask p50 | 502 µs | 459 µs | +9.6% | ≤ +10% | pass |

Dynamics across runs: main metric -2.4% → -49.9% → **-72.8%**; checks passed
1/8 → 5/8 → **8/8**.

**Caveat on the e2e guard B7.** The guard (tok/s regression of zig constrained
vs xgrammar constrained ≤ 5%) peaked at **+7.0%** in the main run (s1 b8); a
repeat run of the same protocol peaked at **+4.7%** (s1 b32); both sets are
published (`bench_e2e_constrained.json` and `bench_e2e_constrained_repeat.json`).
Between runs the spread of slow configurations is 3.2-7.0%, i.e. the guard sits
on the edge of GPU/scheduler noise, while the systematic part (~4-5% on s1
b8/b32) is the overhead of the Python processor `zig_constraints.transformers`
relative to the C++ xgrammar core (profile: the zig processor is ~1.2-1.5
ms/step more expensive at b8/b32; the core is faster - see B2 below). Fixing it
is a separate task of optimizing batch mask application in the HF processor.
All answers are valid: 738/738 rows in both runs.

## Item 2 - equal load (bench_compare_uniform.json)

Schema big_enum_64, shared trace `{"symbol":"sym_42","weight":0.5}` (14 GPT-2
tokens), ≥10k warm observations per engine, µs:

| engine | mask p50 | mask p95 | mask p99 | accept p50 | accept p95 | accept p99 | cold compile p50 | first mask p50 | warm compile p50 |
|---|---|---|---|---|---|---|---|---|---|
| zig_lazy | 8.70 | 250.41 | 258.29 | 0.34 | 0.54 | 0.58 | 1045.84 | 449.85 | 60.05 |
| zig_adaptive | **0.45** | **0.68** | **0.81** | **0.31** | **0.40** | **0.46** | 1116.13 | 502.36 | 61.14 |
| xgrammar | 0.81 | 2.75 | 2.96 | 0.61 | 0.86 | 0.93 | **1210.51** | **458.52** | **2.08** |
| llguidance | 17.03 | 41.63 | 44.45 | 0.76 | 1.77 | 1.95 | 758.04 | 8975.05 | 1.83 |

Notes:

- accept for zig no longer has a tail on multithread states: all percentiles are
  below xgrammar (was p99 2.07 µs vs 0.92 µs). Tokens inside the enum window
  (`":"`, `sym`, `_`, `42`) cost ~1-2 µs, now ~0.25-0.3 µs.
- zig mask p99 (0.81 µs) - cache hits on states that previously carried 64
  threads; hashing and state comparison got cheaper along with the representation
  (0.51 µs in the previous run on smaller states → 0.45 µs p50 with a smaller tail).
- cold compile p50 1116 µs - the distribution on this machine is noisy (min
  163 µs, p95 ~1.7 ms: looks like migration between P- and E-cores; the metric
  is measured with identical code for all engines). In isolated processes (B1,
  below) zig compilation is 0.17 ms, the best of the three.
- warm compile zig (61 µs) - the measured recompilation of the same schema in
  the same context; for competitors warm compile is a cache of the compiled
  grammar (2 µs); zig has none by design (masks are cached, not grammars).

## Item 3 - B1, 30 independent processes (bench_coldproc.json)

Schema big_enum_64, sequential separate processes, p50 of 30:

| engine | ok | compile, ms | first mask, ms | total cold, s | peak RSS, MiB |
|---|---|---|---|---|---|
| zig_adaptive | 30/30 | **0.17** | 0.50 | 2.48 | 759 |
| xgrammar | 30/30 | 1.18 | 0.43 | 2.74 | 762 |
| llguidance | 30/30 | 0.27 | 1.31 | 2.43 | 777 |

Dynamics: cold compile zig 28.37 ms (before wave 2) → 1.37 ms (perf-fix2) →
**0.17 ms** (perf-fix3) - better than both competitors for the first time.

## Item 4 - B7, constrained vs constrained-baseline (e2e, GPU)

Qwen2.5-1.5B-Instruct fp16, RTX 3060, greedy, 3 repeats, medians; baseline =
xgrammar constrained. Validity: 369/369 for both engines in each run.
tok/s regression (zig vs xg): main run / repeat run.

| schema | batch | main, % | repeat, % |
|---|---|---|---|
| s1 flat enum | 1 | +0.8 | +2.9 |
| s1 flat enum | 8 | **+7.0** | +3.2 |
| s1 flat enum | 32 | +5.0 | +4.7 |
| s2 nested arrays | 1 | +1.3 | +1.6 |
| s2 nested arrays | 8 | +1.9 | +0.8 |
| s2 nested arrays | 32 | -1.1 | -0.4 |
| s3 optional bounded | 1 | -5.9 | -5.1 |
| s3 optional bounded | 8 | -63.0 | -67.2 |
| s3 optional bounded | 32 | -106.4 | -100.4 |

(negative regression = zig is faster; on s3 zig is also more efficient in answer
length: 26..29 tokens vs 26..42 for xg at the same step count.)

## Secondary metrics

**B2/B3 (bench_masks.json, bench_schemas_tokenizers.json).**
closed_object_action_amount: mask p50/p95/p99 = 320/459/723 ns, accept
p50/p99 = 251/460 ns (in perf-fix2: 327/508/1008 and 261/538 - the core is
faster). B3 schema-switch phase: fill_mask p50 6.2 µs, p99 91.2 µs (was
101.5 µs), max 107.7 µs (was 270.0 µs) - cold masks on multithread states got
cheaper. Compilation of unique corpus schemas: lazy 30.5 µs, adaptive
32.2 µs (p50). JSB-100: 15 corpus schemas supported, 1 JSB schema ok,
84 unsupported, 15 invalid - unchanged.

**B5/B8 (bench_longrun.json).** 90 072 steps × 3 cache limits (64/128/256 MiB):
0 ResourceLimit/other errors, 11 112 cycles, p99 plateau ratio 0.95-0.99,
RSS ratio 1.0, cache hits 99 993, evictions 0 - plateau with no degradation.

**B6 (bench_schemas_tokenizers.json, tokenizers).**

| tokenizer | vocab | prepare, ms | compile, ms | mask p50, µs | core memory, MiB |
|---|---|---|---|---|---|
| gpt2 (byte-level BPE) | 50 257 | 49.4 | **0.1** | 0.50 | 34.3 |
| qwen2.5-1.5b (byte-level BPE) | 151 665 | 175.2 | **0.1** | 0.84 | 134.6 |
| tinyllama-sp (SentencePiece byte_fallback) | 32 000 | 24.7 | **0.1** | 0.43 | 18.5 |
| synthetic byte vocab | 32 768 | 0.0 | 0.1 | 0.44 | 39.6 |
| synthetic byte vocab | 131 072 | 0.0 | 0.1 | 0.79 | 134.5 |
| synthetic byte vocab | 262 144 | 0.0 | 0.1 | 1.08 | 287.0 |

SP shim certified: 400 pairs, 199 exact + 198 first-▁ + 3
`lossy_utf8_reference` (the reference decodes isolated invalid bytes to
U+FFFD; adapter bytes are exact), `bad: []`.

**GPU mask intervals (bench_gpu_mask_path.json), p50/p95 µs.**

| engine | build | H2D+unpack | apply | full |
|---|---|---|---|---|
| zig_adaptive | 5.2/5.8 | 27.3/38.0 | 20.5/24.1 | 53.0/67.7 |
| xgrammar | 5.8/6.8 | 16.5/18.6 | 31.1/35.8 | 53.6/61.0 |
| llguidance | 35.9/52.3 | 23.4/33.9 | 81.4/118.0 | 143.3/232.6 |

## Fuzz (T4)

`zig build fuzz -Drelease=true -- --seed 424242 --compile-iters 60000
--walk-sessions 6000 --cache-walks 400 --fail-schemas 60` and the default seed:
all campaigns A-D passed (compiler + allocator failure injections,
accept/mask walks over the C ABI, cache parity in 4 threads), 286 337 / 141 913
iterations respectively, invariants intact.

In the run with the new seed 424242, the "batch statuses diverge" invariant
fired for the first time (and was refined): under shared context memory limits,
identical batch rows legitimately diverge (`ok` for early rows, `resource_limit`
for late ones - service buffers of the mask walk consume the session memory
budget; for `dead_end` the strict-equality requirement is retained). It was
verified that this behavior existed before wave 3 as well (reproduced at commit
`1841e27`), i.e. it is not a regression; the artifact message now includes row
statuses.

## Reproduction

```sh
zig build -Drelease=true                       # ReleaseSafe core
PYTHONPATH=python:benchmarks python3 benchmarks/run_all.py --tag perf-fix3
PYTHONPATH=python:benchmarks python3 benchmarks/bench_gpu_mask_path.py \
    --document '{"action":"buy","amount":42}'
PYTHONPATH=python:benchmarks python3 benchmarks/bench_e2e_constrained.py \
    --out benchmarks/results/<timestamp>
python3 benchmarks/summarize_control.py benchmarks/results/<timestamp>
```

## Summary

- Verdict §10.6 on the control scenario: **GO** (8/8 checks).
- accept on multithread states: 2.07 µs → 0.46 µs (p99), below xgrammar.
- Cold compilation: 28.37 ms → 0.17 ms (B1), below xgrammar and llguidance.
- Open item: e2e guard B7 at the threshold edge (3.2-7.0% in two runs,
  5% threshold) due to Python-processor overhead at batch 8/32 - a task for
  optimizing the HF adapter (not the core).
