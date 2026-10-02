# Performance baseline for the spec-v1 perf track (ROADMAP rev 2, section 6)

Date: 2026-09-20. Commit: `c1cc2cb` (engine sources `src/`, `include/`,
`python/` clean; the tree carries concurrent uncommitted P0/PA work of
other agents, none of it touching the engine). Slice:
`../../baseline_slice.json` (pinned, changes never); measurement script:
`../../bench_baseline_slice.py`; raw observations (gitignored):
`../../results/20260920T022651_perf-baseline/`. This is the reference for
the section-6 targets: cold permissive <= 50 ms (stretch <= 10 ms),
MaskBench TBM p90 <= 1 ms, warm path not worse than 1.3 us. Baselines are
recorded per distribution (A8.5); the targets are NOT met today - this
report fixes the "before" numbers.

## 1. Environment

| | |
|---|---|
| CPU | 12th Gen Intel Core i7-12700F (8 P-cores + 4 E-cores, 20 hardware threads) |
| RAM | 62.1 GiB (MemTotal 65,175,400 kB) |
| OS | Linux 6.8.0-138-generic x86_64 |
| zig | 0.15.2 |
| python | 3.10.12 |
| tokenizer (128k) | `unsloth/Meta-Llama-3.1-8B-Instruct` @ `a2856192dd7c25b842431f39c179a6c2c2f627d1`, 128,256 tokens, BPE byte-fallback (the MaskBench default; local HF cache, HF_HUB_OFFLINE=1) |
| tokenizer (corpus) | synthetic byte vocabulary of `bench_common.make_byte_tokenizer` (257 tokens) - the B2/B3 control-series convention |
| engine | bolorgir 0.1.0, mode `adaptive`, memory_limit 256 MB, cache default (64 MiB), profile canonical-v1 |
| loadavg (1/5/15) | 0.74/0.77/0.75 (probe run 09:29:51Z), 0.82/0.66/0.70 (corpus run 09:35:06Z) |

## 2. Methodology (A8.5)

- **Mode**: `adaptive` everywhere (the deployed mode; `lazy` has no cache,
  `precompute` is not viable under the MaskBench protocol - see
  `../../maskbench/README.md`).
- **Parallelism**: 1. All observations strictly sequential; no other
  benchmark processes. This matters: the ROADMAP "~0.6 s cold" was
  recorded under the MaskBench protocol with 20 parallel runner processes
  on these 20 threads; section 4 reconciles the numbers.
- **Warmup**: cold observations have none by construction - every repeat
  is a fresh Engine + compile + new session, so the state is always a
  first visit. Warm observations: one populating `fill_mask` per state
  (discarded); every timed observation is a verified cache hit
  (`cache_hits` delta == observation count).
- **Observation counts**: cold 30 (SPEC 10.5); warm 10,000 per probe;
  corpus 10,000 per schema on seed-42 trace replay.
- **Aggregation**: `bench_common.percentile_stats`
  (count/min/p50/p95/p99/max/mean). Timer `time.perf_counter_ns` around
  `Session.fill_mask` (wall); the DFS cost model additionally uses the
  core-internal `mask_ns_total` / `work_ops_total` of `blg_stats` via the
  C ABI (`tests/blg_ctypes.py`, with `work_limit_ops` set - `Work.charge`
  only counts against a nonzero limit, `src/work.zig`).
- **Distributions kept separate**: the fixed baseline slice (probes + 6
  corpus schemas), the full available set (15 supported corpus schemas;
  the MaskBench full-corpus run quoted from `../../maskbench/`), and the
  phase slice (does not exist yet - P1+).

## 3. Results

### 3.1 Cold first visit of the permissive state (string content, 128k vocab)

State: after the opening quote of a JSON string - (almost) every token is
byte-legal, the mask DFS walks the whole trie. Two probes
(`baseline_slice.json#probes`): bare `{"type":"string"}` and the same
state as a string property value of a closed object (prefix `{"s":"`).

| probe | n | min | p50 | p95 | p99/max | mean |
|---|---:|---:|---:|---:|---:|---:|
| permissive_string_content | 30 | 238.6 ms | 244.5 ms | 267.7 ms | 281.9 ms | 246.6 ms |
| permissive_string_in_object | 30 | 234.7 ms | 244.5 ms | 266.6 ms | 280.6 ms | 246.8 ms |

Compile of the probe schemas: p50 74/92 us. **Target <= 50 ms: gap
~4.9x on p50 (stretch 10 ms: ~24x).**

### 3.2 Warm path (adaptive cache hit, same states)

| probe | observations | cache hits | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|---:|---:|
| permissive_string_content | 10,000 | 10,000 (all) | 0.849 us | 0.908 | 0.953 | 18.2 |
| permissive_string_in_object | 10,000 | 10,000 (all) | 0.813 us | 0.879 | 0.993 | 47.7 |

Core-internal per hit (`blg_stats.mask_ns_total` deltas): 1.5-1.7 us on
single C-ABI observations; the wall p50 above includes the Python binding.
**Target "not worse than 1.3 us": met (p50 0.81-0.85 us, p99 < 1 us).**

### 3.3 Mask DFS cost model (src/mask.zig)

C ABI, one cold `blg_fill_mask` at the permissive state,
`work_limit_ops = 2^60`:

| | string_content | string_in_object |
|---|---:|---:|
| work ops (charged DFS iterations) | 383,647 | 383,662 |
| core time (`mask_ns_total` delta) | 241.5 ms | 241.6 ms |
| wall time | 241.7 ms | 241.8 ms |
| ns per op | 629.4 | 629.6 |
| warm: ops / core ns / hit | 0 / 1,680 / yes | 0 / 1,482 / yes |

Vocabulary trie (pure-Python count, mirrors `src/tokenizer.zig` - eos and
special tokens have no trie nodes): **274,521 nodes** (274,520 edges) over
831,311 token bytes; 128,256 tokens, 1 eos, 255 special.

Reading: the ROADMAP "~400k nodes, ~1.5 us/node" is the op count and the
contended per-op price: measured 383,647 ops at 0.63 us/op sequential
(~1.5 us/op under the 20-way MaskBench load, see section 4). One op is one
DFS loop iteration (one edge descent or one frame pop), so ops exceed the
274,521 trie nodes; almost the whole trie survives pruning in the
permissive state.

### 3.4 Corpus warm path: fixed slice vs full available set (separate distributions)

Synthetic byte vocabulary, seed-42 trace replay, 10,000 observations per
schema after one warmup replay; ns per `fill_mask`:

| distribution | schemas | n | min | p50 | p95 | p99 | max | mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fixed baseline slice | 6 | 60,785 | 307 | 369 | 494 | 629 | 128,683 | 390.5 |
| full available set | 15 (14 measured) | 141,198 | 296 | 375 | 476 | 564 | 88,140 | 392.6 |

Per-schema p50/p99 (ns), slice: closed_object_action_amount 371/616,
nested_5_levels 370/501, array_min_max 368/536, unicode_strings 372/798,
big_enum_64 355/515, mixed_records 377/973 (trace 875 tokens). Full set
adds: defs_ref 364/522, empty_array 427/539, empty_object 418/526,
enum_common_prefixes 365/525, nested_3_levels 378/614, numbers_integers
370/549, optional_fields 378/569, string_length_bounds 374/545.

Known observation (not a regression, canonical-v1 frozen): `long_string`
(object with `minLength:1024` string + integer) raises `DeadEndError` in
the seed-42 random walk at step 1,073, right after the payload string may
close; the schema stays in the full set with an ERROR entry. Recorded for
the A3 completion-reachability work; trace generation simply truncates at
256 steps in B3, which is why the control series never surfaced it.

### 3.5 MaskBench distribution (pinned existing run, quoted)

Full MaskBench corpus (11,306 files @ `ba103c7`), `--compact`,
canonical-v1, the same tokenizer and machine, **20 parallel runner
processes** (`../../maskbench/results/`, run of 2026-09-20): TBM p50 125
us, **p90 609,222 us**, p99 1,393,775 us; TTFM p50 207 us; 624/11,306
schemas pass; 0 validation/invalidation errors. Under that protocol most
masks are first visits (one instance per file), so the p90 is the
contended cold walk of section 3.1. **Target TBM p90 <= 1 ms: gap ~609x.**

## 4. Reconciliation with the ROADMAP estimates

| ROADMAP rev 2 says | measured here | note |
|---|---|---|
| cold permissive ~0.6 s at 128k | 244.5 ms p50 sequential; 609 ms p90 under the 20-way MaskBench protocol | the 0.6 s is the contended number (20 runners / 20 threads: 8 P-cores with HT + 4 E-cores, ~2.4x slowdown); section-6 target <= 50 ms is defined on the fixed slice, i.e. the sequential distribution |
| ~400k trie nodes, ~1.5 us/node | 383,647 work ops, 0.63 us/op sequential; trie itself 274,521 nodes | "nodes" = charged DFS iterations; 1.5 us/op is again the contended price (609,222 us / 383,647 ops = 1.59 us/op) |
| cache hit 1.3 us | wall p50 0.85 us (10,000 verified hits); core-internal 1.5-1.7 us single observations | consistent; the warm-path target is a guard, not an optimization goal |

## 5. Gap summary against the section-6 targets

| target | baseline (this report) | gap |
|---|---|---|
| cold permissive <= 50 ms (stretch <= 10 ms), fixed slice | p50 244.5 ms, p95 267.7 ms | 4.9x (24x to stretch) |
| MaskBench TBM p90 <= 1 ms, pinned protocol/profile | 609,222 us | ~609x |
| warm path not worse than 1.3 us | p50 0.85 us, p99 0.99 us | met |

## 6. Reproduction

```bash
# full slice measurement (probes ~8 min + corpus ~20 min on this machine):
HF_HUB_OFFLINE=1 PYTHONPATH=python:benchmarks \
    python3 benchmarks/bench_baseline_slice.py
# parts: --skip-corpus / --skip-probes; observation counts are CLI-overridable
```

Raw JSON of the two runs behind this report:
`../../results/20260920T022651_perf-baseline/bench_baseline_slice_{probes,corpus}.json`
(gitignored). Verification after the change: `zig build` and
`zig build test` pass at `c1cc2cb` (exit 0).

## 7. Boundaries

- One machine, one run per distribution; latencies carry run-to-run noise
  of a few percent (the slice/full corpus p50 differ by ~6% between the
  two runs). The slice manifest pins schema identity and protocol, not
  absolute times - phase comparisons must rerun both sides.
- The MaskBench numbers are quoted from the pinned full-corpus run, not
  remeasured here; that run predates this commit by hours and used the
  same engine build configuration.
- The phase slice (spec-v1 schemas) does not exist yet; the full available
  set today is the 15 supported corpus schemas plus the 38 compilable
  files of the repository snapshot (pinned with sha256 in
  `baseline_slice.json`, compile-only selection - the P0 scanner owns the
  full first-blocker report).
