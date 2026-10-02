# ADR-0007 mask fast path: before/after against the pinned baseline slice

Date: 2026-09-20 (run 2026-09-21T05:00:26Z). Baseline commit: `c1cc2cb`;
the measured tree carries the ADR-0007 implementation plus concurrent
uncommitted P1 work in `src/parser.zig` / `src/schema.zig` (chunk-blob
cache keys) - see section 4 for what that does to the comparison.
Reference: `../../baseline_slice.json` (pinned) and
`../20260920T022651_perf-baseline/REPORT.md` (the "before" numbers).
Measurement script: `../../bench_adr0007.py`; raw observations:
`../../results/20260921T050026_adr0007.json` (gitignored).

## 1. Verdicts against the ADR-0007 targets

| target (ADR-0007 / ROADMAP section 6) | baseline | fast path OFF (this tree) | fast path ON (this tree) | verdict |
|---|---|---|---|---|
| cold permissive fill p50 <= 50 ms (stretch <= 10 ms) | 244.5 ms | 420.0 ms | **5.00 ms** | **GO** (84x vs off; stretch met too) |
| DFS cost model work ops | 383,647 | 383,647 (identical) | **20,744** | 18.5x fewer charged ops |
| MaskBench TBM p90 <= 1 ms (pinned protocol) | 609,222 us (20-way load) | 421,213 us (sequential) | **3,731.6 us** (sequential) | **NOT MET** - 113x better, still 3.7x over |
| warm path not worse than 1.3 us (cache hit) | 0.849 us | 4.25 us | 4.20 us | fast path **neutral** (off == on); tree-level regression is NOT from ADR-0007 - section 4 |
| exact-class share of the 128k vocabulary (GO/NO-GO: NO-GO if > 10%) | n/a | n/a | **4.81%** (6,175 / 128,256) | **GO** |

Benchmark gate (ADR-0007 Decision 4): **PASS** - before any timing was
accepted, on/off mask equality was verified on every visited state: 4
probe states (off vs on vs on with `max_workers=8`, bit-identical) and
411 recorded MaskBench trace states (473 masks, identical trace
structure, zero diverging masks, 0 validation errors in both modes).

## 2. What was measured

Same machine as the baseline (i7-12700F, 20 threads, 62 GiB), same
tokenizer (`unsloth/Meta-Llama-3.1-8B-Instruct` @ `a2856192`,
128,256 tokens), engine mode `adaptive`, profile canonical-v1, strictly
sequential observations, Debug zig build (as the baseline). OFF = the
same library with the fast path disabled (`mask_fast_path=2` in the
context config); OFF is bit-identical to the plain trie walk - the
charged work ops of the cold probes (383,647 / 383,662) match the
baseline's exactly.

- **Cold probes** (30 observations each, fresh context + compile +
  session per observation, `benchmarks/baseline_slice.json` probes):

| probe | mode | fill wall p50 | p95 | work ops |
|---|---|---|---|---|
| permissive_string_content | off | 420.0 ms | 435.7 ms | 383,647 |
| permissive_string_content | on | **5.00 ms** | 5.45 ms | 20,744 |
| permissive_string_in_object | off | 419.2 ms | 432.1 ms | 383,662 |
| permissive_string_in_object | on | **5.08 ms** | 5.31 ms | 20,759 |

  Of the 20,744 ON ops, 4,008 (mask words) are the content-class
  classification, charged identically on widened-cache hit and miss; the
  remaining ~16.7k are the exact-class pruned walk (subtrees without
  exact-class tokens are skipped via the `exact_subtree` bitset).

- **Warm probes** (10,000 verified cache hits each; primary numbers
  through the `bolorgir._core` C extension like the baseline, ctypes
  numbers in the raw JSON agree within ctypes overhead):

| probe | off p50 | on p50 | on p95 |
|---|---|---|---|
| permissive_string_content | 4.25 us | 4.20 us | 4.73 us |
| permissive_string_in_object | 4.28 us | 4.46 us | 4.98 us |

- **MaskBench slice**: the first 20 compiling BFCL_java files of the
  pinned corpus snapshot (`BFCL_java_6, 10, 18, 22-25, 27, 32, 44, 47,
  49, 53, 57, 63, 73, 74, 76-78`; most BFCL files do not compile under
  canonical-v1 - empty `{}` subschemas - so the slice is selected
  deterministically), per-instance sessions, compact serialization,
  473 masks:

| mode | TBM p50 | TBM p90 | TBM p95 | TBM p99 | max | masks > 1 ms |
|---|---|---|---|---|---|---|
| off | 237.8 us | 421,213 us | 425,974 us | 440,938 us | 467,335 us | 120 / 473 |
| on | 233.6 us | **3,731.6 us** | 3,841.8 us | 4,054.6 us | 4,209.3 us | 119 / 473 |

## 3. Why TBM p90 stays at 3.7 ms (the remaining gap)

25% of the slice's masks (120/473) are first visits of a permissive
string-content state; the pinned MaskBench protocol opens a fresh
session per instance and every measured file carries one instance, so no
cache (session, adaptive, or widened) ever serves a repeat. Each such
mask costs one fast-path fill: the content-class half is a memcpy of the
precomputed class bits (the widened cache's word loop only pays off for
length-bounded strings; on this slice R is unbounded, so the class is
the constant `content_bits`), and the residual ~16.7k charged ops are
the exact-class pruned walk - 6,175 exact-class tokens (4.81% of the
vocabulary: byte fragments that are not clean UTF-8 string content)
still need per-state parser evaluation. ~3.7 ms sequential corresponds
to that walk; reaching 1 ms needs it roughly 4x cheaper or eliminated -
i.e. ADR-0007 Decision 3 items 1-2 (feedBytes chain batching, transition
tables), which live in `src/parser.zig` and were out of scope for this
change, or a cross-instance cache the pinned protocol does not permit.

## 4. Tree-level differences vs the baseline run (not ADR-0007 effects)

Two concurrent-work effects show up in the OFF numbers; both are outside
the ADR-0007 change set (`src/parser.zig` was frozen for this work) and
are reported for the P1 owner:

1. **Cold OFF wall is 420 ms vs the baseline's 244.5 ms at identical
   work ops (383,647).** The walk does exactly the same work; the
   per-op price rose from 0.63 us to ~1.1 us between `c1cc2cb` and the
   current tree (parser state representation changes).
2. **Warm cache-hit path is 4.2-4.3 us vs the 0.849 us baseline**, OFF
   and ON alike. Control on the same machine, minutes apart: the
   baseline pairing (the `python/bolorgir/_lib` build of `c1cc2cb` +
   `_core.abi3.so`) still measures **0.863 us**; swapping in the current
   library gives 4.2 us. The delta is the chunk-blob cache-key redesign
   (`parser.chunkBlobLen` + `writeChunkBlob` + `hashState(st, side)` +
   blob compare on every hit, `src/c_api.zig fillMaskInner`). The fast
   path adds nothing to the hit path (OFF == ON within noise), so the
   ADR-0007 warm guard is met in the only sense this change can affect
   it; the absolute 1.3 us guard fails tree-wide for reasons belonging
   to the concurrent work.

## 5. Environment

| | |
|---|---|
| CPU | 12th Gen Intel Core i7-12700F (8 P-cores + 4 E-cores, 20 hardware threads) |
| OS | Linux 6.8.0-138-generic x86_64 |
| zig | 0.15.2, Debug build (baseline convention), isolated `--cache-dir`/`--prefix` |
| python | 3.10.12, transformers 4.57.6 (installed for this run; the baseline's 128k section was SKIP-able without it), HF_HUB_OFFLINE=1 after the initial tokenizer fetch |
| tokenizer | `unsloth/Meta-Llama-3.1-8B-Instruct` @ `a2856192dd7c25b842431f39c179a6c2c2f627d1`, 128,256 tokens |
| engine | this tree, mode `adaptive`, memory_limit 512 MB (ctypes probes) / 256 MB (extension probes), cache default |
| loadavg | ~0.7-0.9 (idle apart from the measurement) |

## 6. ADR-0007 decision coverage

- **D1** string-content class + equivalence lemma: implemented
  (`src/precompute.zig` `cpPureLen`/`FastData`, `src/mask.zig`
  `uniformStringResidual` + `fillMaskFast`); verified by the benchmark
  gate here and the differential tests (`tests/test_mask_fastpath.py`,
  `tests/test_mask_fastpath_fuzz.py`, 7 zig-side three-way differential
  tests).
- **D2** widened cache (`WKey`, shared LRU budget, widened-first
  eviction): implemented in `src/cache.zig`. On this slice its payoff is
  small by construction (unbounded R collapses the classification to a
  memcpy); it exists for bounded-R vocabularies/states where the word
  loop is real.
- **D3** bit tricks + exact-subtree pruning: implemented
  (`content_bits`, `exact_subtree`, pruned walk). Chain batching in
  `feedBytes` and transition tables: **deferred** - they require
  `src/parser.zig`, frozen for this change (and batching was shown to
  diverge on ResourceLimit accounting for in.n > 1).
- **D4** correctness gates: zig unit/differential tests (155/155),
  curated ABI differential tests, 150-seed property fuzz, the benchmark
  gate above - all green.
- **D5** `max_workers` (disjoint word ranges, threshold 256 words, cap
  64), determinism (bit-identical output for 1/2/8 workers), kill
  switches (`mask_fast_path` config field, `BLG_MASK_FAST_PATH=0`
  environment): implemented and exercised by the gate and tests.
