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

## Unresolved limitations

- **Strict-D3 RESOURCE_LIMIT residual**: 196 pinned oracle rows (draft4 27,
  draft6 35, draft7 37, 2019-09 48, 2020-12 49), each identified by (draft,
  suite file, case, test index) in
  `tests/oracle/engine_error_residual_spec_v1.json` and enforced by
  `test_oracle_dialects.py::test_engine_error_residual_pinned` and
  `test_spec_v1_no_engine_errors`. All residual families are documented
  with counterexample schemas in the fix3 report.
- **MaskBench recheck residuals** on the fix3 final build: 14 ok /
  12 RESOURCE_LIMIT of 26 cases; 75 accept / 50 RESOURCE_LIMIT / 1 reject
  of 126 previously rejecting instances. Artifacts:
  `benchmarks/reports/20260929T_fix3_adr0007/recheck_out.log` and
  `recheck_valid_rejects_out.jsonl` (the recheck scripts have since been
  consolidated into the maintained entry point
  `benchmarks/recheck_cert_residuals.py`).
- **o55595 serializer key-order defect**: the single reject in the
  126-instance recheck is a MaskBench serializer defect, not an engine
  one - the serializer's key merge across a oneOf-$ref branch emits keys in
  an order that violates the schema's own `enabled`-before-`type`
  conjunction; the engine accepts the correctly ordered serialization.
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
