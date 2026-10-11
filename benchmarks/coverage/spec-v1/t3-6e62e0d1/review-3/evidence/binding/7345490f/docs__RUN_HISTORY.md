# Acceptance history

This file is the durable record of the engine's acceptance evidence: build
identities, measurement conditions, outcomes and unresolved limitations.
Methodology and reproduction commands: `benchmarks/README.md`. Published
reports: `benchmarks/reports/`. Raw oracle results are written to
`tests/oracle/results/` by the oracle harness; they are local run outputs,
not versioned (see `tests/oracle/README.md`).
Historical (pre-translation) commit ids: `benchmarks/reports/README.md`.

## Control series, canonical-v1 (2026-09-15 to 2026-09-19)

The control series compares the engine against xgrammar and llguidance under
pinned acceptance protocols (thresholds 20%/10%/5%; primary metric p99 mask
latency vs xgrammar). Verdict history, reports under `benchmarks/reports/`:

- **Fifth run** (2026-09-18, report `20260918T162851_a855338-fix/`): NO-GO on
  one marginal case - `s1 b32 +5.4%` at the 5% threshold. Primary metric p99
  mask 0.74 vs 2.91 us, warm compile 1.03 vs 2.12 us, cold compile 1054 vs
  1182 us, GPU path 49.4 vs 52.7 us.
- **Sixth run** (2026-09-19, commit `7c8df82`, report
  `20260918T183010_a855338-fix2/`): GO. The per-step HF-processor tail was
  optimized (batched D2H of tokens, batch masks, single `amax` check):
  `s1 b32 +3.6%`, `s1 b8 +4.3%`. A secondary holdout corpus was added
  (+99.8% in p99 mask vs llguidance, all other latencies <= +10%). Cold
  compile 124 us on the main corpus and 84 us on the holdout after fixing a
  measurement artifact (deallocation of the previous tokenizer bundle fell
  into the measurement window; the core was unchanged). Published weak
  spots: `s1 b8 +4.3%` (thin margin), `zig_lazy` holdout p95/p99 83-85 ms
  (outside the scored mode).
- **Seventh run** (2026-09-19, commit `f1d62a4`, report
  `20260918T194406_7cd81c3-fix2/`): GO. Grammar ownership, exact HF decoder
  semantics, honest artifact-cache budget, a C example and a fuzzer; the
  gate was tightened (B1 >= 30 processes, >= 10k warm observations per p99
  class, >= 30 cold). The gate rejected the first collection of the same
  code for condition degradation inside the collection (published in full);
  the scored repeat gave GO with unchanged criteria and thresholds.
- **Eighth run** (2026-09-19, commit `6614bd8`, report
  `20260918T212317_d84f992-fix2/`): NO-GO. Two collections of the same code
  merged per the pinned protocol without discarding. Primary metric p99 mask
  0.77 vs 2.82 us (+72.6%), secondary holdout +99.8% (0.83 vs 357.37 us),
  first mask p50 +9.7%, B1 30/30, GPU path 50.03 vs 52.64 us. Blocker: e2e
  gate 11/12, `s1 b8 +6.9% > 5%` (structural per-step overhead of the Python
  HF adapter at batch 8).
- **Ninth run** (2026-09-19, commit `f21e9ad`, report
  `20260918T222603_s1-hotpath3/`): NO-GO, but the `s1 b8` blocker was closed
  by HF-adapter forbid-path fusion (-0.9% at pool n=24). Primary metric
  +70.4% (0.83 vs 2.82 us), holdout +99.8%, GPU path 52.43 us. Failures:
  `s1 b32 +5.7%` (a transient window plus inter-process drift of the second
  e2e file) and first mask p95/p99 +20.9%/+51.6% under the "worst zig / best
  competitor" merge rule on n=30 samples.
- **Tenth run** (2026-09-19, commit `b0ad006`, report
  `20260919T033356_v3-run3/`, including `tables.md` and `gate.txt`): **GO -
  the current verdict**. Protocol `acceptance_protocol_v3` pinned before the
  first collection: balanced AB/BA order and paired median of ratios in e2e,
  1000 cold first-mask observations, automatic composition (exactly three
  unique collections) and revision-identity checks; thresholds 20%/10%/5%
  unchanged. Primary metric +73.5% (0.81 vs 3.04 us), secondary holdout
  +99.7%, e2e 12/12 (`s1 b8 +1.0%`, `s1 b32 +1.7%`), first mask <= +10%
  (zig tail thinner: 131 vs 2131 observations above 600 us out of 6000
  across both corpora), B1 30/30 per collection, B5/B8 no degradation, GPU
  path 47.26 us.

## spec-v1 implementation (2026-09-20 to 2026-09-22)

The `spec-v1` profile (full JSON Schema track) was implemented alongside
`canonical-v1`, which stayed bit-for-bit frozen at 95 oracle PASS /
7 KNOWN_GAP. Oracle harness: pinned JSON Schema Test Suite 23.2.0 `95fe6ca`
plus jsonschema 4.26.0. spec-v1 oracle progression (draft 2020-12):
95 (baseline) -> 411 (P1 core) -> 631 (P2 objects/arrays) -> 726 (P3
combinators) -> 751 (P4 `pattern`) -> 833 (P4 numbers) -> 861 (P5 recursion
and references, ADR-0008 bounded unrolling with `REF_UNROLL_CAP=8`) -> 1015
(P6a `unevaluated*`, ADR-0009) -> 1118 PASS (P6b content* annotations and
dynamic references, ADR-0010). Final state: 1118 PASS / 99 SKIPPED /
1 KNOWN_GAP / 0 MISMATCH / 0 ENGINE_ERROR, with 0 MISMATCH and 0
ENGINE_ERROR at every step from P2 on. The 99 SKIPPED are documented
refusals with a JSON pointer (`docs/supported_features.md`).

Performance track (ADR-0007 mask fast path, fixed slice
`benchmarks/baseline_slice.json`):

- Baseline (`benchmarks/reports/20260920T022651_perf-baseline/`): cold
  permissive p50 244.5 ms, warm p50 0.85 us (10,000 verified cache hits),
  383,647 charged work ops per cold walk.
- Round 1 (`benchmarks/reports/20260921T050026_adr0007/`): cold p50
  244.5 -> 5.0 ms (the <= 50 ms target and the <= 10 ms stretch met),
  charged work ops 383,647 -> 20,744, exact-class share of the 128k
  vocabulary 4.81%. MaskBench TBM p90 609,222 -> 3,731.6 us (113x; the
  <= 1 ms target not met). The differential on/off gate (bit-identical
  masks) passed before any timing was accepted. Kill switches:
  `BLG_MASK_FAST_PATH=0`, config field `mask_fast_path`; `max_workers`
  parallelism with bit-identical output.
- Round 2 (`benchmarks/reports/20260921T122823_adr0007_r2/`): TBM p90
  609,222 -> 481 us (1266x; the <= 1 ms target met), cold p50 ~5.1 ms, warm
  cache hit 4.33 -> 1.90 us (the <= 1.3 us guard formally not met; the
  residual is structural: ABI call plus 16 KiB mask memcpy plus key).

Corpus compile coverage on the release build
(`zig build -Drelease=true -Dcpu=baseline`; reports `benchmarks/coverage/`):
repository snapshot 9,251 / 9,558 files (96.8%), MaskBench snapshot
10,794 / 11,306 files (95.5%). Both clear the >= 95% target; the
canonical-v1 baseline was reproduced exactly.

## Audit, fixes and fix2 acceptance (2026-09-22 to 2026-09-28)

An independent audit of the spec-v1 implementation (34 checks on build
`c2777552`; the checks have since been migrated into maintained pytest
tests under `tests/`) reopened acceptance with four defect classes:

- **R1**: the mask admitted dead-end tokens (`pattern`+`maxLength`,
  allOf/enum intersection, numeric bounds) - the ADR-0005 residual-language
  analysis was missing for the new nodes.
- **R2**: `compileEnumSpec` dropped adjacent container constraints
  (`const`+`required`, `const`+`uniqueItems`); `const` next to `enum` was
  refused instead of intersected.
- **R3**: combinators compared serializations instead of values, making
  `oneOf`/`allOf` outcomes dependent on object key order.
- **R4**: the MaskBench serializer treated a Python `str` value as JSON
  text.

R1-R4 were fixed, and the re-measurement exposed and fixed two more
defects: minLength continuation pruning in the mask normal form, and an
engine crash on long runs (`page_allocator` per-allocation mmap drove the
process to `vm.max_map_count`; the root allocator was switched to
`smp_allocator`). A TEMP leak in `memoPut` (`src/complete.zig`,
+4,466 B/cycle) was fixed via `getOrPut` (build "fix2"); confirmed flat at
968 B/cycle and 0 live bytes after `deinit()`.

fix2 acceptance gates (lib sha256 `bc25a03a...`, combined
src/include/build.zig sha256 `33cfeb8f...`):

- `zig build test` exit 0; all 34 audit checks green; pytest
  `tests/ python/tests/` 1003 passed / 25 skipped; C-extension tests
  123 passed / 25 skipped.
- Oracle 6/6 (draft4/6/7, 2019-09, 2020-12 spec-v1 plus canonical-v1,
  `tests/oracle/results/oracle-fix2-*`): rows and summary bit-for-bit
  identical to fix1. The 2 whitelisted ref.json sibling-`id` MISMATCHes
  per draft4/6/7 remain a documented deviation.
- Perf re-accepted (`benchmarks/reports/20260927T203000_adr0007_fix2/`):
  differential gate PASS, cold p50 4.40 ms, MaskBench TBM p90 274.3 us,
  warm C-ext p50 0.898/0.946 us.
- Exact corpus coverage with the semantic split (merged reports
  `benchmarks/coverage/spec-v1/coverage_{repo,maskbench}.{json,md}`):
  repository 9,558 files - compile 96.79%, session 99.88%, generation
  96.22% (96.34% of attempted), generated-document validity 99.70%;
  MaskBench 11,306 files - compile 95.44%, session 99.87%, generation
  96.35% (96.49% of attempted), validity 99.74%, valid-instance acceptance
  93.79%, invalid-instance rejection 94.38%, serialization incompatibility
  0.00%. Byte-context destroy failures: 0. The only 2 accepted invalid
  instances are the documented draft-04 integer divergence.

## fix3 acceptance: strict ADR-0005 D3 and oneOf residual certificates (2026-09-29/30)

Full report: `benchmarks/reports/20260929T_fix3_adr0007/`. Measured on a
worktree with uncommitted changes on top of `c1cc2cb`; final ReleaseSafe lib sha256 `11352e82...` (intermediate fix3
build: lib `2be5163a...`, combined sources `2950bd1d...`).

Two audit blockers were implemented:

- **Strict ADR-0005 Decision 3**: a budget-exhausted reachability proof is
  UNKNOWN, not alive - `fill_mask` fails and `blg_accept_token` refuses the
  token with RESOURCE_LIMIT instead of silently admitting a potentially
  dead-ending token (`src/mask.zig`, `src/complete.zig`, `src/c_api.zig`).
- **oneOf vote-link doom by exact residual equality** (`linkedThreads` in
  `src/complete.zig`): literal frames link by remaining suffix, lit_trie by
  subtrie language equality, str_pat by equal codepoint windows plus DFA
  right-language equality (bounded greatest fixpoint). Fill cost at the
  overlap counterexample state 6.3 -> 2.6 ms.

Strict D3 initially regressed oracle ENGINE_ERROR (RESOURCE_LIMIT) by +40
to +94 per draft on infinite-space states where no search budget can prove
aliveness. The accepted resolution kept strict D3 and landed eight
closed-form certificate families (`certOpenObjCounted`,
`repeatContainsDoom`, `emitRepeat` contains synthesis, dependentSchemas
virtual facets, `openObjDepValueDoom`, `emitNumMult`,
`emitPatSuffixMerged` restricted to same-allOf-instance siblings,
`numExpLockstep`), reducing spec-v1 ENGINE_ERROR from 338 to 196 rows.

Gates on the final build: zig 274/274; all 34 audit checks green; pytest
1025 passed / 25 skipped / 0 failed; the ADR-0005 R1 acceptance regression
24/24 in all four modes (lazy/adaptive x mask fast path on/off,
`tests/test_spec_v1_r1_residual.py`, `tests/test_lazy_choice_spec_v1.py`);
MISMATCH/KNOWN_GAP/canonical-v1 identical to fix2. Performance re-measured
on the final build (`benchmarks/reports/20260930T030906_fix3final_adr0007/`):
differential gate PASS, cold p50 8.73 ms (target and stretch met), TBM p90
357.7 us (<= 1 ms met), warm-ext p50 1.206/1.297 us on a quiet re-run (the
<= 1.3 us guard met with a 3 ns margin on that measurement; the first run
under load measured 1.313/1.394, above the guard).

## ADR-0011 numeric conjunctions (2026-10-02)

Audit-fix spec rev 2.2, task T1. Measured on a worktree with uncommitted
changes on top of `dbc5eeb`; ReleaseSafe lib sha256 `e74c8972...`
(`zig build --release`), test-hooks lib sha256 `0785bc38...`
(`zig build --release -Dtest_hooks=true --prefix zig-out/test-hooks`).

The joint certificate of ADR-0011 (`src/numcert.zig`) decides every
conjunction of `num_range`/`num_mult`/`int_num`/`num_excl`/`num_const`
machines over one lexeme in closed form; empty numeric conjunctions are
refused at compile time.

- Audit tables of T1 (13 schemas): 0 failures. Random walks to EOS,
  240 per schema (3 byte-complete vocabularies: bytes, bytes + `123`,
  bytes + `{"`/`":`/`",`/`"}`; lazy and adaptive; mask fast path on and
  off), every document valid under an exact-number `jsonschema`
  validator, `blg_cert_stats` `search_entered` = 0
  (`tests/test_spec_v1_p4_numbers_reachability.py`). Masks over the
  number bytes after the table prefixes equal the reference. On a
  vocabulary without full byte coverage all 13 schemas are refused at
  compile time (`UNSUPPORTED_TOKENIZER`).
- Certificate cross-check, fast mode (CI `num-cert`): 11 table + 12 random
  conjunctions, prefixes up to 4 bytes over `0159-.e+`, 0 failures.
- Oracle (reports `tests/oracle/results/oracle-adr0011-*`): spec-v1
  ENGINE_ERROR 196 -> 181 rows; the 15 rows that left (`allOf.json`
  "allOf simple types" tests 0/1 and `multipleOf.json` "float division =
  inf" test 0 in each dialect) are PASS; no other transition. 2020-12
  spec-v1 1072 PASS / 99 SKIPPED / 1 KNOWN_GAP / 0 MISMATCH /
  46 ENGINE_ERROR; canonical-v1 95 PASS / 1116 SKIPPED / 7 KNOWN_GAP,
  unchanged.
- Found and fixed on the way: the frozen-mantissa point test of
  `certNumRange` (typed digits that are a proper prefix of a bound's
  digits were treated as equal to it).
- Zig tests 276/276.

## Generative spec-v1 test and the defects it found (2026-10-02)

Audit-fix spec rev 2.2, task T2a. Measured on a worktree with
uncommitted changes on top of `dbc5eeb`: ReleaseSafe lib sha256
`7345490f...`, test-hooks lib sha256 `3a6eec20...`, combined
src/include/build.zig sha256 `01c7ea62...` (the final build). The
earlier candidates lacked the fixes listed last below: lib `9c8cb7d9...`
(src `05949010...`) the last three, lib `23fc8b08...` (src
`3ae4aa9f...`) the last one. The `dbc5eeb` reference library was built
with `zig build --release` in a worktree at that commit (sha256
`8ff970d4...`).

- `tests/fuzz/test_fuzz_generate_spec_v1.py`, fast mode (seed 20261002;
  13 table schemas of T1 and 40 random schemas per vocabulary; 6 passes
  each; vocabularies: bytes, bytes plus numeric tokens, bytes plus JSON
  pieces). The random objects vary their declared order, and one
  combinator form is a `oneOf` of two objects over the same names in
  opposite declared orders:
  - on `dbc5eeb`: bytes fails with 35 table-schema passes in
    `RESOURCE_LIMIT` (the T1 defect) and 7 `DEAD_END` passes; bytes plus
    numeric tokens aborts the process (the closed-object crash below);
    bytes plus JSON pieces fails with 35 `RESOURCE_LIMIT`, 7 `DEAD_END`
    and 4 invalid documents, all in the opposite-order `oneOf` form;
  - on the final build it passes with 0 failures. Schema outcomes
    (generatable / engine_unavailable): bytes 46 / 6, bytes plus numeric
    tokens 44 / 8, bytes plus JSON pieces 48 / 4, the same on both
    earlier candidates; `engine_unavailable` occurs only on random
    schemas, where it is reported and allowed. Wall time 69 s for the
    three vocabularies (lib `9c8cb7d9...`).
- Defects fixed on the way (see CHANGELOG):
  - three `DEAD_END` families: exact masks for `contains` and
    `uniqueItems` arrays (ADR-0005 Decision 4 residuals), the `\u`
    third-digit rule, and compile-time emptiness of disjoint-type
    conjunctions; regression cases in
    `tests/test_spec_v1_array_residuals.py` (12 cases in 4 modes) and
    the Zig test "complete: contains and uniqueItems element residuals";
  - a process abort in `fill_mask` on a closed object without declared
    properties next to object branches of a combinator, found by the
    T2b run on `dbc5eeb`; regression case in
    `tests/test_spec_v1_r1_residual.py`;
  - a soundness defect: a `oneOf` branch or an `if` condition whose
    declared keys arrived out of schema order did not vote, so documents
    matching two `oneOf` branches were accepted (found by the T2b run,
    `Github_trivial/o60144.json`, `o60145.json`,
    `Github_medium/o85194.json`). Value verdicts now use an order-free
    copy of their subgraph (semantics-spec-v1 4.3); regression cases in
    `tests/test_spec_v1_p3.py::test_value_verdicts_ignore_key_order`.
    On the 1454 repo schemas with `oneOf` or `if` (3 byte passes each,
    764 compared before the comparison was stopped) the generation
    outcome changed for 9: 2 moved from `engine_unavailable` to
    generatable, 3 from `engine_unavailable` to a time limit, one
    schema exceeded the node budget (fixed with a separate allowance
    for the copies), and 2 hit the repeated-compile limitation listed
    below;
  - a parser defect of both profiles: threads spawned while one byte
    was fed were stepped by index arithmetic, so one thread missed the
    byte and another saw it twice. Under `{"anyOf":[{},{},{}]}` the
    document `8""` was accepted (a soundness defect present since 0.1.0,
    found by the T2b run of `dbc5eeb` on `Snowplow/sp_343_Normalized.json`),
    and the first byte of an array element whose `allOf` holds two
    unions ended in `DEAD_END` (`Github_ultra/o62895.json`, `o62897.json`,
    `o62899.json`, found by the T2b run of the previous final build);
    regression cases: the Zig tests "comb siblings spawned on an array
    element's first byte see it once" and "untyped anyOf branches
    spawned on a number's first byte all see it", and
    `tests/test_spec_v1_r1_residual.py::test_r1_spawned_threads_and_closed_min_properties`;
  - a closed object whose `minProperties` only optional declared
    properties can meet was judged empty, so a parent requiring it was
    dead at its first byte (`Github_ultra/o84874.json`, same run);
    regression case in the same pytest test;
  - the residual-reachability search counted a branch past the parser
    thread or frame cap as dead, so a mask could drop a token with no
    `RESOURCE_LIMIT`. Under the harness cap of 64 threads eight valid
    MaskBench instances of the `Github_ultra/o6354.json` family were
    rejected at the `{` of their first array element (found by the T2b
    run of lib `23fc8b08...`). The branch is now undecided like a budget
    cut; regression case
    `tests/test_spec_v1_r1_residual.py::test_r1_thread_cap_never_drops_a_token`.
- Gates on the final build: Zig 281/281, `zig fmt --check` clean;
  pytest 1182 passed / 22 skipped on the ctypes and the package backend
  (2026-10-05, with the serializer fix and the harness and triage changes
  of the T2b/T2c section below; the release build reproduces lib
  `7345490f...`, and the triage with `--gate-build final` exits 0
  and leaves its outputs unchanged); certificate
  cross-check (fast mode) 0 failures over 23 conjunctions; the release
  library exports no `blg_test_*` symbol.
  Oracle (reports `tests/oracle/results/oracle-final-*`): every row of
  all five dialects and of canonical-v1 has the same outcome as in
  `oracle-adr0011-*`, including the two open `ref.json` sibling-`id`
  rows of draft-04/06/07 (`OPEN_DEVIATIONS`, dialect-matrix section 9).
  2020-12 spec-v1: 1072 PASS / 99 SKIPPED / 1 KNOWN_GAP / 46
  ENGINE_ERROR / 0 MISMATCH. canonical-v1: 95 PASS / 1116 SKIPPED / 7
  KNOWN_GAP. The ENGINE_ERROR residual stays at 181 rows.

## Semantic coverage runs and triage, T2b/T2c (2026-10-02 to 2026-10-05)

Audit-fix spec rev 2.2, tasks T2b and T2c. Harness:
`benchmarks/bench_jsb_coverage.py --semantic --generatable --passes 20
--hf-sample-every 20 --chunk-size 100` with
`BOLORGIR_COVERAGE_PROFILE=spec-v1`, at `nice 19`, with the limits in the
report headers (256 MiB per context, 64 parser threads per state). Every
compiled schema gets K = 20 generation passes on the byte-level synthetic
tokenizer; `unsloth/Meta-Llama-3.1-8B-Instruct` and GPT-2 (pinned
revisions) run on a fixed 1-in-20 sample of the compiled schemas. Budgets:
8192 steps and 10 s per pass; seed 20261002. Generated documents are
checked by the reference validator of the audit-fix spec rule 6
(jsonschema 4.26.0 over exact numbers, `pattern` by regress 2025.10.1,
ECMA-262). The earlier single greedy pass (plain jsonschema) and the
MaskBench instances (serialized with `python/bolorgir/serializer.py`) are
checked as before.

Runs (i7-12700F):

- baseline on `dbc5eeb` (lib sha256 `8ff970d4...`, with its own
  serializer), 2026-10-02 23:37Z to 2026-10-03 08:18Z, 8 jobs, with a
  schema budget of 30 s per schema and tokenizer;
- final build (lib sha256 `7345490f...`, the build of the T2a section),
  2026-10-03 14:20Z to 2026-10-04 03:28Z, same parameters. The machine
  restarted during the repository snapshot, and after the restart the
  pinned HF tokenizers were missing from the local cache, so the chunks
  run in that window recorded `tokenizer_unavailable` for them. Those 55
  chunks were discarded and run again with the tokenizers present; the
  41 chunks finished before the restart were kept;
- completion of both runs with `--complete-from`, 2026-10-04 09:30Z to
  2026-10-05 08:46Z, 9 jobs per build with both builds on the machine at
  once, over three sessions (the machine restarted twice; about 14 h of
  run time for the final build and 15.5 h for `dbc5eeb`). Every pass the
  schema budget had skipped ran, and every `invalid_document` pass was
  replayed by its seed for its document;
- two more completions on that result on 2026-10-05 (minutes each) for
  the replays that had not reproduced their pass (below).

Published: `benchmarks/coverage/spec-v1/coverage_{repo,maskbench}.{json,md}`
for the final build and `baseline-dbc5eeb/` (`.md`, gzip `.json`) for
`dbc5eeb`, every pass record in the `*.passes.json.gz` sidecars, with the
whole document of each `invalid_document` pass. Each report header
names the run it completes and the completions on top of it.

The review of the first reports (2026-10-04) found:

- R1: the sidecars held no document, and the reports kept the documents
  of the first 5 failed passes only, so 4,788 `invalid_document` passes
  had no document while the triage applied the draft-04 class to the
  whole schema; the generative test tolerated a listed schema by its hash
  alone;
- R2: 581 repository and 584 MaskBench byte-level results had fewer than
  20 passes because of the schema budget;
- R3: a failing `blg_finish` counted as `engine_defect` even for
  `RESOURCE_LIMIT` and `CANCELLED`;
- R4: `--jobs` reused a finished chunk by the existence of its report,
  whatever the inputs;
- R5: 87 results were `generatable` on `dbc5eeb` and not on the final
  build without an explanation.

Fixes: the harness attempts every pass by default and keeps every
invalid document in the sidecar, `--complete-from` runs skipped passes
and replays the invalid ones (R1, R2); `blg_finish` failures go through
the shared classification (R3); each chunk directory carries an identity
record (engine library, harness sources, parameters, corpus, packages)
and a chunk of other inputs is refused, as is a merge of chunks of
different engines or parameters (R4); the triage lists every
`generatable` result that changes outcome, with the pass counts of both
builds (R5). `tests/test_coverage_harness.py` covers these rules. An
`invalid_document` result is class D only when the sidecar holds every
invalid document of the schema and the draft-04 integer spelling
explains each one; the generative test tolerates a listed schema only
when the listed deviation explains the document at hand.

Replays. A replay must reproduce the recorded outcome, stage, code,
step and prefix length. Of the 9,770 replays of the final build, 396 did
not the first time: 305 ran out of the 10 s pass limit while both builds
loaded the machine, and 91 ended in `RESOURCE_LIMIT` in 11 large
`Github_hard` / `Github_ultra` schemas. The second completion replayed
them with 6 times the pass limit and, after an `engine_unavailable`, on
a fresh context; 6 passes of `Github_hard/o21363.json` were left. Near
the 256 MiB context limit the outcome of a pass depends on what the
process ran before it (the grammar of the same schema can take 146 to
190 MiB, see the first limitation below) and on timing (the `adaptive`
mask cache admits an entry when the mask was seen often enough or took
longer than a threshold to compute, so what it holds can differ with
load). The third completion runs
such a replay alone in a child process; every replay of the final
build then reproduced. Of the 8,053 replays of `dbc5eeb`, 212 did not
the first time; one is left: pass 19 of `Github_hard/o21284.json` (byte)
ends in `RESOURCE_LIMIT` at step 74 in every replay, also alone and on an
idle machine, while the recorded pass reached a document at step 975.
(The third completion of `dbc5eeb` was run twice: the first time, with
the final build's completion on the machine, 4 passes were left.)

| metric | repo, dbc5eeb | repo, final | MaskBench, dbc5eeb | MaskBench, final |
|---|---|---|---|---|
| compiled / files | 9247 / 9558 | 9247 / 9558 | 10788 / 11306 | 10788 / 11306 |
| byte: generatable | 7741 / 9243 (83.75%) | 8108 / 9243 (87.72%) | 9243 / 10783 (85.72%) | 9614 / 10783 (89.16%) |
| byte: invalid_document | 674 | 702 | 654 | 686 |
| byte: engine_defect | 266 | 0 | 269 | 0 |
| byte: validator_error | 0 | 0 | 0 | 0 |
| byte: engine_unavailable | 481 | 359 | 540 | 405 |
| byte: incomplete | 81 | 74 | 77 | 78 |
| Llama-3.1: generatable | 207 / 431 (48.03%) | 227 / 431 (52.67%) | 301 / 535 (56.26%) | 325 / 535 (60.75%) |
| Llama-3.1: invalid_document / engine_defect | 3 / 0 | 5 / 0 | 3 / 1 | 4 / 0 |
| GPT-2: generatable | 260 / 431 (60.32%) | 284 / 431 (65.89%) | 334 / 535 (62.43%) | 364 / 535 (68.04%) |
| GPT-2: invalid_document / engine_defect | 2 / 0 | 5 / 0 | 3 / 1 | 4 / 0 |
| invalid_document passes / with their document | 4740 / 4739 | 5222 / 5222 | 4558 / 4558 | 5128 / 5128 |
| greedy pass: dead end / engine error | 2 / 211 | 0 / 150 | 2 / 262 | 0 / 196 |
| greedy pass: document rejected by jsonschema | 24 | 42 | 24 | 42 |
| valid instances: rejected / engine error | - | - | 46 / 733 | 0 / 512 |
| invalid instances: accepted, jsonschema agrees invalid | - | - | 3 | 2 |

The percentage base is the compiled schemas minus `skipped`. Llama 3.1
and GPT-2 results are for the 1-in-20 sample only. The greedy pass
reaches a document on more schemas than on `dbc5eeb`; every one of its
42 rejections on the final build is a draft-04 integer spelling (class D
below). The compiled counts match, but the main compile of a few
`Github_ultra` schemas near the memory limit differs between the runs
(see "Repeated compile" in the limitations).

Triage: `benchmarks/coverage/spec-v1/triage.md`, generated by
`benchmarks/triage_semantic.py` (class rules in its docstring); manual
decisions with their reasons in
`benchmarks/coverage/spec-v1/triage_manual.json`; the rechecks of the
published counters, of the residual instances and of the invalid
documents in `benchmarks/coverage/spec-v1/triage-evidence/`
(`docs_dbc5eeb_on_final.jsonl`: the `dbc5eeb` documents the draft-04
rule does not explain, fed to the final build; `docs_final_on_final.jsonl`:
the count for the final build, where the rule explains all 10,350). On
the final build there is no unclassified row, no class A or B, no
`engine_defect` and no `validator_error`. Every remaining
`invalid_document` (1406 schema results over the three tokenizers) and
both accepted invalid instances are class D and listed in
`tests/fuzz/known_deviations_spec_v1.json` (1402 schemas, each with its
deviation). No harness defect (class E) was found.

| source | A | B | C | D | unclassified |
|---|---:|---:|---:|---:|---:|
| published counters of `c1cc2cb`, rechecked on `dbc5eeb` and the final build | 0 | 92 | 109 | 52 | 0 |
| generatable outcomes, `dbc5eeb` | 15 | 525 | 12 | 1323 | 1 (accepted exception) |
| generatable outcomes, final build | 0 | 0 | 0 | 1406 | 0 |
| MaskBench instances, `dbc5eeb` | 1 | 25 | 21 | 2 | 0 |
| MaskBench instances, final build | 0 | 0 | 0 | 2 | 0 |

- A (all on `dbc5eeb`, fixed): 14 schema results (10 files, four in both
  corpora) where a generated document matched two `oneOf` branches
  (value verdicts, T2a section), the
  `Snowplow/sp_343_Normalized.json` document of the parser spawn defect,
  and the invalid instance 2 of `JsonSchemaStore/codux.config.schema.json`
  (it matches both `oneOf` branches of `ConfiguredScript`). The final
  build accepts none of these 28 documents: it rejects 3 and ends 25 in
  `RESOURCE_LIMIT` before their end, as it ends the codux instance.
- B: 522 `DEAD_END` and 3 `CRASH` results of `dbc5eeb` (the defects of
  the T2a section) are generatable, incomplete or a draft-04 document on
  the final build. The 92 published signals (76 rejected valid instances,
  10 dead ends, 6 refused masked tokens) pass on the final build. Of the
  25 valid instances `dbc5eeb` rejected, 13 are accepted through the
  engine fixes and 12 through the serializer fix below. The audit-fix
  spec lists serializer differences under D; these 12 count as B because
  the serializer ships in the package and was fixed.
- C, passed to T3: 109 published signals (`RESOURCE_LIMIT` at
  generation, compile or session creation in the harness context, dead
  ends that now end in `RESOURCE_LIMIT` or a budget, 33 valid instances
  that end in `RESOURCE_LIMIT`); 12 `dbc5eeb` dead ends that end in
  `RESOURCE_LIMIT` on the final build; 21 valid instances of the
  `Github_ultra/o63xx` family that end in `RESOURCE_LIMIT` under the
  harness cap of 64 parser threads and pass with 128 (`dbc5eeb` dropped
  the `{` from the mask instead, the thread-cap defect).
- D: the draft-04 integer spelling of docs/supported_features.md
  section 9. Each failing document or instance passes the same validator
  once integral values count as integers.
- Unclassified, an accepted exception: `dbc5eeb/repo/o21284.json/byte`.
  14 of its 15 invalid documents are the draft-04 spelling; the document
  of pass 19 is not reproducible (above). On the final build the 20 pass
  records of this schema are equal to those of `dbc5eeb`, its 14
  documents are equal byte for byte, and pass 19 reaches a draft-04
  document at the same step 975. This does not prove the content of the
  missing document, so no class is assigned. Decision of the review of
  2026-10-05: a historical evidence gap on `dbc5eeb`, admitted at
  acceptance; class not established (an `exception` entry in
  `triage_manual.json`, bound to the `dbc5eeb` library hash, the schema
  hash and the seed of pass 19, and listed under "Accepted exceptions"
  in `triage.md`; it does not carry over to another build or pass and
  does not extend the deviation list). The T2c criterion of no unclassified case applies to the
  final build: `triage_semantic.py --gate-build final` exits 0 and lists
  the exception; the full-history triage without `--gate-build` exits 1.

Results `generatable` on `dbc5eeb` and not on the final build (R5): 49
with every pass run on both builds, against 87 in the reviewed reports.
23 end in a draft-04 document on the final build (class D); 26 are
class C for T3: 23 end in `RESOURCE_LIMIT` (19 byte-level, 3 GPT-2, 1
Llama 3.1) and 3 in the pass time limit (2 Llama 3.1, 1 GPT-2). The
section "Generatable on `dbc5eeb`, not generatable on `final`" of
`triage.md` lists each with its pass counts.

Handed to T3 (availability): the 26 class C transitions and the other
class C rows of `triage.md`; the `engine_unavailable` share of the final
build, against the T3 target of at most 1%: byte 359 / 9,243 (3.88%,
repository) and 405 / 10,783 (3.76%, MaskBench), Llama 3.1 38 / 431
(8.82%) and 57 / 535 (10.65%), GPT-2 43 / 431 (9.98%) and 66 / 535
(12.34%), the HF tokenizers on the 1-in-20 sample; and the
process-history dependence of a compile (the first limitation below,
reproduced by `benchmarks/diag_process_history.py`).

The engine defects these runs found are listed in the T2a section (the
`fill_mask` abort, the value verdicts, the parser spawn defect, the
closed-object `minProperties` emptiness and the thread cap). One more
was outside the engine: `python/bolorgir/serializer.py` emitted the keys
a `oneOf`/`anyOf` branch declares ahead of the outer object's own
declared keys, against the outer declared order the engine enforces, so
12 valid MaskBench instances were rejected (found by the run on lib
`23fc8b08...`). It now emits an order consistent with both the outer and
the branch order; `python/tests/test_serializer_combinators.py`.

README Quickstart (T16), checked 2026-10-04 in a clean venv (CPython
3.10.12): the system pip 22.0.2 failed with an `AssertionError` in its
resolver while installing the `transformers` extra; after
`python -m pip install --upgrade pip` (26.2.1) the install and the
Quickstart ran (transformers 5.18.0, tokenizers 0.23.2). The README
Install section now creates a venv and upgrades pip first.

## T3: runtime RESOURCE_LIMIT in spec-v1 (2026-10-05)

Audit-fix spec rev 2.4, task T3. Candidate build: lib sha256
`d4e703033df6ab9337bcbb71b66e50e0bc657a12cb7b8f98b0587943d673049d`
(working tree on top of `f87f6c8`, ReleaseSafe, Zig 0.15.2). This section
records the implementation checks and diagnostic replays on that build.
The full acceptance of the candidate (spec rule 9: K = 20 on both corpora
with the HF sample, the T2c triage and the T3 coverage control, run from a
separate directory) is still pending; the replay numbers below do not
replace it.

Checks on the candidate: `zig fmt --check`, `zig build test` 283/283,
pytest 1218 passed / 26 skipped on both backends (ctypes and the
package), with the `-Dtest_hooks=true` build present for the probe and
`fail_row` tests (`tests/test_probe.py`, `tests/probe_hooks_check.py`,
`python/tests/test_hf_adapters.py`); `python/tests` in a venv with torch
2.14.1+cpu and transformers 5.17.0, where the adapter tests run instead of
skipping: 166 passed, 0 skipped.

**Process-history dependence: cause found and fixed.** The dependence
recorded after the T2b/T2c runs (two entries of the unresolved
limitations had it) came from the engine's allocators, not from the
process. The grammar arena and the scratch arenas
grew a chunk in place when the address range after it happened to be free
and otherwise took a new chunk 1.5 times the previous size; the accounting
allocator charged the larger block at that moment. So the same compile
was charged 146 to 190 MiB depending on the address-space layout left by
earlier allocations, and one large chunk request could cross the 256 MiB
limit while 117 MiB were in use. `alloc.Arena` now never grows a chunk in
place (doubling chunks up to 1 MiB, large requests in their own chunk,
returned on free), the accounting allocator refuses growth past the pages
a block spans, a pattern shared by several `$ref` copies compiles to one
DFA, and the scratch of DFA construction and of the emptiness analysis is
released after the compile. Measured on the candidate with
`benchmarks/diag_process_history.py` (coverage limits, byte tokenizer,
one process, no load beside it):

| Run | Final build (`7345490f...`) | Candidate |
|---|---|---|
| `Github_hard/o21363.json`, 12 rounds, a fresh context each | grammar 145.8 to 189.6 MiB; every compile after 2 to 7 good ones failed in 13 of 16 runs | grammar 28.5 MiB in every round, context peak 52.4 to 53.1 MiB, no failure |
| o21363, the 20 recorded passes in order on one context | `RESOURCE_LIMIT` at `fill_mask` from the third pass on | 20 of 20 passes equal to their records |
| `Github_hard/o21339.json`, 8 compile rounds | repeated compiles could fail | 34.8 MiB in every round, no failure |
| `Github_hard/o21311.json`, 8 compile rounds | repeated compiles could fail | 29.5 MiB in every round, no failure |

Regression checks: the Zig test "accounting and arena: charges do not
depend on in-place growth" and `tests/test_memory_history.py` (repeated
create/compile/generate/release cycles and a fresh process charge the same
bytes).

**Oracle** (docs/dialect-matrix.md section 9, reports
`tests/oracle/results/oracle-t3-*`): ENGINE_ERROR 181 to 33 over the five
dialects, 2020-12 46 to 10 (target at most 10); the only transitions
against the final build are `ENGINE_ERROR -> PASS` (148 rows); MISMATCH
and canonical-v1 are unchanged row for row. The 33 pinned rows
(`tests/oracle/engine_error_residual_spec_v1.json`) are five families:
`oneOf.json` (15: "oneOf complex types", "oneOf with required", "oneOf with
missing optional property", object branches whose exactly-one vote is
decided only by later keys), `propertyNames.json` (6, pattern with an
open key), `unevaluatedProperties.json` (6: annotations from `if` without
`then`, and `not`), `unevaluatedItems.json` (4, with `if`/`then`/`else`)
and `dynamicRef.json` (2, multiple dynamic paths).

**Llama 3.1 on the MaskBench sample** (target: `engine_unavailable` at
most 1% of the compiled schemas of the fixed 1-in-20 sample, same limits
as T2b). Diagnostic replay of the 535 compiled schemas of the published
report with their recorded seeds (6 jobs, 10 s per pass, no completion of
time-limited passes): 373 generatable, 152 incomplete, 5 invalid_document,
5 engine_unavailable (0.93%) on `d4e70303`. The diagnostic repeat on
`50cbd80e` after the dependentRequired certificate fix (same sample and
limits; artifacts, selections and records in `benchmarks/t3/evidence.md`)
gives 375 generatable, 151 incomplete, 5 invalid_document, 4
engine_unavailable (0.75%). Against the `d4e70303` replay the changes are
`Github_medium---o32437.json` back to generatable (14 to 19
completed_valid), one timing-limited schema (`Glaiveai2K---calculate_area_c686757a.json`)
improving from incomplete to generatable, and
completed_valid/time_limit jitter inside the same outcome class; no
generatable-to-engine_unavailable transition and no new engine_defect row
appears. `Kubernetes---kb_1154_Normalized.json` stays incomplete; the
targeted repeat shows a pre-existing per-fill cost that times out on every
build including `dbc5eeb` and `7345490f` (R2b in `benchmarks/t3/evidence.md`).

**Class C transitions handed over by T2c** (26 results `generatable` on
`dbc5eeb` and not on the final build, `triage.md`). Each was replayed with
its recorded seeds on the final build, to capture the failing state and
the undecided-reason histogram, and on the candidate
(`benchmarks/local/t3/run_c26.sh`, 10 s per pass). Pass counts: cv
completed_valid, eu engine_unavailable, tl time limit, sl step limit.

| Corpus, file, tokenizer | `dbc5eeb` | final | candidate | Cause on the final build |
|---|---|---|---|---|
| repo o29388 byte | cv 9, tl 11 | cv 15, tl 4, eu 1 | cv 18, tl 1, sl 1 | open-object key in the middle of an escape under a comb (key escape certificate) |
| repo o58218 byte | cv 20 | cv 19, eu 1 | cv 20 | same |
| maskbench o58218 byte | cv 20 | cv 15, eu 5 | cv 20 | same |
| repo prometheus.rules byte | cv 20 | cv 19, eu 1 | cv 20 | same |
| repo o9887 gpt2 | cv 6, tl 14 | cv 10, tl 9, eu 1 | cv 19, tl 1 | same |
| maskbench cypress byte | cv 19, tl 1 | cv 19, eu 1 | cv 19, tl 1 | key in the middle of a UTF-8 character under `allOf`, whose shortest completion is a key the object has (key escape certificate, key extension) |
| repo o69965 gpt2 | cv 4, tl 16 | cv 8, tl 9, eu 3 | cv 15, tl 5 | numeric group undecided (numeric `oneOf`/`if` certificate) |
| repo o61624 gpt2 | cv 7, tl 13 | cv 11, tl 8, eu 1 | cv 16, tl 4 | same |
| repo o90905 byte | cv 14, tl 6 | cv 14, sl 5, eu 1 | cv 14, sl 6 | `propertyNames` gate on a key prefix (key prefix check) |
| repo, maskbench prometheus byte | cv 20 | cv 19, eu 1 | cv 20 | 18 `if`/`then` groups on a closed object (`if`/`then`/`else` certificates) |
| repo webhook byte | cv 20 | cv 10, eu 10 | cv 20 | `anyOf` of required keys that the closed object has passed in declared order (key-obligation doom per thread) |
| maskbench webhook byte | cv 20 | cv 7, eu 13 | cv 20 | same |
| repo sp_336 byte | cv 18, tl 2 | cv 16, tl 2, eu 2 | cv 19, tl 1 | `oneOf` at the start of an array element (vote-link certificates) |
| maskbench sp_336 byte | cv 19, tl 1 | cv 17, tl 1, eu 2 | cv 20 | same |
| maskbench o35155 byte | cv 19, tl 1 | cv 19, eu 1 | cv 19, tl 1 | required key that every open `anyOf` branch has passed in declared order (open objects block keys) |
| maskbench o6366 byte | cv 20 | cv 19, eu 1 | cv 20 | 65 parser threads over the harness cap of 64, equal number readings of untyped `anyOf` branches (thread merge) |
| maskbench o85188 byte | cv 1, tl 19 | cv 1, tl 18, eu 1 | cv 1, tl 19 | in-flight key whose short completions are keys the object has (key extension) |
| maskbench o69083 llama-3.1 | cv 4, tl 16 | cv 7, tl 12, eu 1 | cv 16, tl 4 | numeric group undecided (numeric `oneOf`/`if` certificate) |
| maskbench o69970 llama-3.1 | cv 1, tl 19 | tl 20 | cv 8, tl 12 | time limit only, no engine error; the final build itself reaches cv 6 in the replay, so the transition depends on the load of the run |
| maskbench o89639 llama-3.1 | cv 2, tl 18 | tl 20 | cv 4, tl 16 | same (final build in the replay: cv 4) |
| maskbench Glaive generate_invoice gpt2 | cv 1, tl 19 | tl 20 | cv 2, tl 18 | same (final build in the replay: cv 1) |
| repo tye-schema byte | cv 1, tl 19 | cv 2, tl 16, eu 2 | cv 10, tl 2, sl 1, **eu 7** | open: `oneOf` of eight open-object branches in the middle of the object |
| maskbench tye-schema byte | cv 3, tl 17 | cv 3, tl 15, eu 2 | cv 10, tl 2, sl 1, **eu 7** | same |
| maskbench o32934 byte | cv 19, tl 1 | cv 10, sl 1, eu 9 | cv 17, sl 1, **eu 2** | open: start of a key of an object under five `if`/`then` with closed `then` branches |
| maskbench cryproj.53 byte | cv 1, sl 15, tl 4 | cv 1, tl 12, sl 6, eu 1 | cv 1, sl 18, **eu 1** | open, a documented limit: in a `uniqueItems` element a number may not exceed \|exp\| 400 (semantics-spec-v1 section 1); after an exponent of 40 or more (the pass typed `744345424e+97`) the next digit cannot be decided |

On the candidate 22 of the 26 results are `generatable` again; 4 are
not (both tye-schema results, o32934, cryproj.53). The two tye-schema
results end in `engine_unavailable` in more passes than on the final
build, 7 against 2: the passes now get past the states that cut them off
by the time limit before (10 complete instead of 2) and reach the `oneOf`
states that remain undecided. Settling such a state needs a witness that
fails every rival branch but one; the candidate tries values of other
types for that (it settles the recorded state of the first failure), not
yet keys that fail a rival. These four go to the full acceptance run as
open class C results.

**Probe and adapters.** ADR-0012 (proposed): `BLG_COMPILE_PROBE` /
`BLG_COMPILE_PROBE_STRICT` search the reachable parser states at compile
time and end `found` (the compile fails with the token path and the
subschema pointer), `inconclusive` or `proven`; `proven` is reached only
for grammars with finitely many exact states. The verdict is cached with
the artifact under its budgets. On the oracle residual schemas the probe
yields `found` or `inconclusive`, never `proven`
(`tests/test_probe.py`). The transformers adapter takes
`on_constraint_error="fail_row"`: a row whose mask fails ends with EOS
only and is reported as `constraint_error`; the hook build forces an
undecided verdict for one row of a batch of two in
`python/tests/test_hf_adapters.py`.

## Unresolved limitations

- **Process-history dependence of a compile**: resolved on the T3
  candidate (cause, fix and measurements in the T3 section). Found
  2026-10-05 by the coverage replays: `Github_hard/o21363.json` held 145.8
  to 189.6 MiB of grammar memory from round to round in fresh contexts of
  one process and stopped compiling after 2 to 7 rounds; on the candidate
  it holds 28.5 MiB in every round. The `Github_ultra` compile statuses
  that moved between runs are re-measured by the full acceptance run.
- **Strict-D3 RESOURCE_LIMIT residual**: 33 pinned oracle rows on the T3
  candidate (draft4 3, draft6 5, draft7 5, 2019-09 10, 2020-12 10; 181
  since ADR-0011, 196 after fix3), each identified by (draft, suite file,
  case, test index) in `tests/oracle/engine_error_residual_spec_v1.json`
  and enforced by
  `test_oracle_dialects.py::test_engine_error_residual_pinned` and
  `test_spec_v1_no_engine_errors`. The five remaining families are listed
  in the T3 section; the earlier ones are documented with counterexample
  schemas in the fix3 report.
- **MaskBench recheck residuals** on the final build: 16 ok (15 accept
  and the reject of the invalid o55595 instance) / 10 RESOURCE_LIMIT of
  26 cases (14 / 12 since fix3); 87 accept / 39 RESOURCE_LIMIT of 126
  previously rejecting instances (75 / 50 / 1 on the fix3 build and on
  `dbc5eeb`; the former reject, a valid o55595 instance, passes with the
  serializer fix of 2026-10-03). Against the evidence of the build
  before the value verdicts, 10 instances moved from RESOURCE_LIMIT to
  accept and 2 (`Github_hard---o84330.json`, a `oneOf` of some twenty
  meter types) from accept to RESOURCE_LIMIT: branches that used to die
  on key order now stay alive until their values decide, and the group
  certificate runs out of search budget. Artifacts:
  `benchmarks/reports/20261002T_audit_fixes/` (fix3:
  `benchmarks/reports/20260929T_fix3_adr0007/`); maintained entry point
  `benchmarks/recheck_cert_residuals.py`.
- **Repeated compile of a large schema in one process**: resolved with
  the process-history dependence above. On `dbc5eeb` and the final build,
  compiling `Github_hard/o21339.json` or `o21311.json` again in a fresh
  context of the same process could fail with `RESOURCE_LIMIT` ("out of
  memory during compile"), and several `Github_ultra` schemas moved
  between `compiled` and `resource_limit` from run to run (20 repository
  and 23 MaskBench main compiles ended in "out of memory during compile"
  on the final build). On the T3 candidate o21339 and o21311 compile in 8
  of 8 rounds at 34.8 and 29.5 MiB; the corpus counts come with the full
  acceptance run.
- **Control-series weak spots**: `zig_lazy` on the holdout p95/p99 ~80 ms
  (outside the scored mode); historically thin e2e margins at
  `s1 b8`/`s1 b32`.
- **Soundness caveats**: the strict-D3 boundary is the cost of proof for
  the bounded ADR-0008 grammar, not undecidability; strict D3 keeps mask
  soundness and completeness for every call that returns successfully.
  ADR-0008 non-monotonicity: truncating a recursive tail to `false` inside
  `oneOf`/`not`/`if` can widen the language; the guarantee there is R1 with
  respect to the truncated grammar, not equivalence to the untruncated
  schema.
