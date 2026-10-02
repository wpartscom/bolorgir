# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Release packaging: the sdist is now self-contained. The `sdist` command
  prepares `bolorgir/_lib` before archiving (zig build, or a ready library
  via `BLG_SKIP_ZIG=1`/`BLG_CORE_LIB`), and the C header ships inside the
  package as `bolorgir/include/bolorgir.h` (synced from the repository
  root by `setup.py`, git-ignored). The release workflow's
  `BLG_SKIP_ZIG=1 python -m build --no-isolation` previously failed:
  first with `libbolorgir.so* not found` (the git-ignored `_lib` binary
  never entered the archive), then with `bolorgir.h: No such file or
  directory` (the extension looked for the header outside the unpacked
  sdist). A wheel built from the unpacked sdist now needs neither zig
  nor the repository checkout (`python/setup.py`, `python/MANIFEST.in`,
  `.gitignore`).
- Release packaging: a direct wheel build (`pip wheel ./python`) now also
  ships the core library. setuptools runs `build_py` before `build_ext`,
  so the `_lib` payload used to be copied before the zig step populated
  it; the resulting wheel installed but failed at import with
  `libbolorgir.so.0: cannot open shared object file`. `setup.py` now
  prepares `_lib` in `build_py` as well (once per setup invocation)
  (`python/setup.py`).
- Reachability strictness wave (2026-09-29, follow-up review of the audit
  resolution):
  - Strict ADR-0005 Decision 3: a state the bounded reachability search
    cannot settle is UNKNOWN, not alive - `fill_mask` now fails and
    `blg_accept_token` refuses the token with `RESOURCE_LIMIT` instead of
    silently admitting a token that may dead-end (`src/mask.zig`
    `setNodeTokens`/`fillMaskBrute`, `src/complete.zig` `stateAlive`,
    `src/c_api.zig` accept path). Previously a budget-exhausted search
    kept the token admitted, which let dead bytes through (e.g.
    `oneOf` over `^(ab|c)$`/`^(ab|d)$` admitted `a` after the prefix `"`).
    Regression pinned across lazy/adaptive x fast-path on/off
    (`tests/test_spec_v1_r1_residual.py`).
  - `oneOf` vote-link doom now links branch threads by exact residual
    equality, not only frame identity (`src/complete.zig`
    `linkedThreads`): literal frames by remaining suffix, `lit_trie`
    frames by subtrie language equality (structural), `str_pat` frames
    by equal codepoint windows plus right-language equality of the DFA
    states (bounded greatest-fixpoint check over the reachable state
    pairs, capped at `RESIDUAL_EQ_CAP`). Overlapping oneOf branches such
    as `^(ab|c)$`/`^(ab|d)$` after `"a` are certified dead in closed
    form instead of draining the per-fill search budget.
- Audit wave (2026-09-22 audit findings, fixes verified 2026-09-26 on the
  `final4` build; regressions pinned in `tests/test_spec_v1_r1_residual.py`,
  `tests/test_spec_v1_r2_r3.py`, `tests/test_audit_regressions.py` and
  `tests/test_maskbench_serializer_hook.py`):
  - R1: token masks no longer offer dead-end tokens. A residual-language
    reachability analysis (ADR-0005; new `src/pattern.zig`) filters tokens
    from which no valid document can be completed (e.g. `^a$` with
    `maxLength: 1` no longer allows starting with `b`; the `{ab,xy}` ∩
    `{ac,xy}` intersection keeps only completable continuations). On
    search-budget exhaustion the strict D3 rule applies (see the
    2026-09-29 wave above: the call fails with `RESOURCE_LIMIT` rather
    than admitting an unproven token); budgets `FILL_SEARCH_BUDGET=512`,
    `MAX_DEPTH_REACH=512`, `Cache.max_states=256`
    (docs/semantics-spec-v1.md §4.4/4.5).
  - The reachability rollout no longer fails with `RESOURCE_LIMIT`
    storms at feed time (memo `in_progress` leak fixed, per-fill search
    budget, parser state pool): oracle ENGINE_ERROR rows went from
    63-145 per dialect to 0 on all five dialects.
  - Parser: duplicate threads spawned by the AnyJSON integer/number
    choice are merged after every byte (`dedupThreads`, content-aware
    chunk comparison) - an array of integers under an unconstrained item
    schema no longer hits the 64-thread budget at the fifth element;
    `uniqueItems` verdicts preserved.
  - R2: the static enum/const validator respects adjacent constraints
    (`{"const":[1,1],"uniqueItems":true}` and `required` next to `const`
    objects are enforced at compile time).
  - R3: `oneOf`/`allOf` branch value filters compare object values
    key-order invariantly (branches differing only in key order behave
    per JSON Schema value semantics).
  - R4: the MaskBench serializer never re-parses string instances -
    `"1"` stays the string `"1"`, arbitrary text like `"hello"`
    serializes instead of being reported serialization-incompatible
    (`python/bolorgir/serializer.py` `serialize_value`; full-corpus
    `blg-ser` re-measure: 0 serialization incompat, outcome counts
    identical to the published serializer-off run).
  - Mask cache normal form (ADR-0007 D2+): the uniform string-content
    counter is clamped at `min_len` instead of zeroed, so quote-led
    close-and-continue tokens (e.g. the merged `","`) are no longer
    wrongly pruned from the mask after `minLength`-bounded string values
    (3 MaskBench files; mask differential vs the previous build: tokens
    only gained, none lost, dead-end exclusions bit-identical).
  - Engine no longer crashes on long-running hosts: the root allocator
    was switched from `page_allocator` to `smp_allocator`
    (`src/c_api.zig` `root_allocator`). The per-allocation mmap/munmap
    of the page allocator drove the process VMA count to
    `vm.max_map_count` (65530) over ~12k schema compilations and
    generations, after which a `munmap` splitting a merged VMA failed
    with ENOMEM and `std.posix.munmap` panicked on its `unreachable`
    branch (`blg_session_destroy` -> `parser.Side.deinit`). Small
    allocations are now packed into per-thread slabs (O(slabs) VMAs
    instead of O(allocations)); verified on the previously crashing
    full-corpus coverage run, and warm-path metrics slightly improved
    (`benchmarks/reports/20260927T115548_adr0007_fix1/REPORT.md`).
  - Completion memo no longer leaks the duplicated key when a state is
    memoized twice: `memoPut` (`src/complete.zig`) used dupe-then-`put`,
    and `put` keeps the existing key on a collision, so the fresh copy
    was lost (4466 bytes per fill_mask cycle, charged to the context
    TEMP budget - the growth behind the "Context retains generation
    memory" finding). Fixed via `getOrPut` with an update-in-place on
    existing keys; the per-cycle TEMP accounting is now flat under the
    audit workload.

### Added

- spec-v1 P6b (ADR-0010): `contentEncoding`/`contentMediaType`/
  `contentSchema` are annotations in every dialect (2020-12 Validation
  section 8 - they never assert). RFC 3986 relative-URI resolution with
  an in-document `$id` resource index: a reference's URI part resolves
  against the enclosing base URI (dot-segment removal included) and
  jumps into an in-document resource or the registry snapshot by the
  resolved absolute URI. `$dynamicRef`/`$dynamicAnchor` (2020-12) and
  `$recursiveRef`/`$recursiveAnchor` (2019-09) resolve over the
  compile-time dynamic scope (bookending requirement, outermost scope
  first) on top of the ADR-0008 bounded unrolling. Keywords next to
  `$ref`/`$dynamicRef`/`$recursiveRef` in 2019-09/2020-12 conjoin with
  the reference expansion (sibling rule) instead of refusing.
  `$dynamicRef`/`$recursiveRef` next to unevaluated* stay a documented
  `UNSUPPORTED_FEATURE` refusal with a pointer. Oracle spec-v1:
  1015 → 1118 PASS, 0 mismatches, 0 engine errors; canonical-v1
  bit-for-bit unchanged.

- spec-v1 P6a: `unevaluatedProperties`/`unevaluatedItems` (dialects
  2019-09 and 2020-12) via compile-time scenario synthesis (ADR-0009).
  Every acceptance hypothesis of the enclosing in-place applicators
  (`allOf`, `anyOf`, `oneOf`, `if`/`then`/`else`, `dependentSchemas`,
  local `$ref`) carries the set of keys/indices it evaluates; the
  unevaluated* guard conjoins every scenario. Includes the inert
  reductions, container masking with an outside-language analysis, and
  open-object conjunct merges. Shapes that exceed the parser thread
  budget refuse at compile time with `UNSUPPORTED_FEATURE` and a JSON
  pointer: a combinator nested in a `oneOf`/`anyOf` branch, several
  conjunctive `contains` next to `unevaluatedItems`, an external `$ref`
  next to unevaluated*, and the branch/scenario caps (5 live `anyOf`
  branches, 32 scenarios, 4 `dependentSchemas` entries, 8 guard
  patterns). Oracle spec-v1: 861 → 1015 PASS, 0 mismatches, 0 engine
  errors; canonical-v1 unchanged.

- spec-v1 P5 (ADR-0008): recursive local `$ref` (`#`, `#/...` pointers,
  plain-name fragments, all five dialects; mutual recursion, anchors,
  combinators) by bounded unrolling with a per-path budget of 8
  (`REF_UNROLL_CAP`): the bottom position behaves as `false` and the
  limit is mask-visible. Unproductive cycles compile to
  `UNSATISFIABLE_CONSTRAINT`. `$ref` fragments percent-decode (`%XX`)
  before `~0`/`~1` unescaping. External `$ref` resolves through the
  immutable registry snapshot (ADR-0006 D5); without a registry it stays
  refused. Oracle spec-v1: 833 → 861 PASS, 0 mismatches; canonical-v1
  unchanged.

- spec-v1 P4: the `pattern` keyword as an ECMA-262-subset regex compiled
  to a DFA over Unicode scalar values, evaluated as an unanchored search
  and conjoined with the codepoint length bounds; unblocks regex
  `patternProperties` (exactly one pattern). Numeric
  `minimum`/`maximum`/`exclusiveMinimum`/`exclusiveMaximum` and
  `multipleOf` over `integer`/`number` in exact decimal arithmetic
  (never binary64); a `multipleOf` divisor with more than 10 significant
  decimal digits refuses `UNSUPPORTED_FEATURE`. Oracle spec-v1:
  726 → 751 (pattern) → 833 (numbers) PASS, 0 mismatches.

- spec-v1 P3: `anyOf` general union; `oneOf` with exact subtype-aware
  exclusivity; `allOf` as the exact intersection (comb node, no unsafe
  merges); `if`/`then`/`else` lowering; `not` over the supported
  complement forms (`{type}`, scalar `{enum}`/`{const}`, `{required}`,
  nested `not`, De Morgan over `anyOf`/`allOf`). Oracle spec-v1:
  631 → 726 PASS, 0 mismatches.

- Perf ADR-0007 round 2: normalized cache state for uniform
  string-content positions (all positions of a long string share one
  cache entry). MaskBench TBM p90 609,222 → 481 µs (1266x; the ≤ 1 ms
  target met); cold permissive fill p50 ~5.1 ms; warm cache-hit
  4.33 → 1.90 µs (the ≤ 1.3 µs guard formally not met - structural
  remainder documented in
  `benchmarks/reports/20260921T122823_adr0007_r2/REPORT.md`);
  differential gate PASS.

- `benchmarks/bench_jsb_coverage.py` accepts the env flag
  `BOLORGIR_COVERAGE_PROFILE` to select the compile profile; the spec-v1
  corpus coverage measured with it (release build): repo snapshot
  9,251 / 9,558 (96.8%), MaskBench snapshot 10,794 / 11,306 (95.5%)
  compiled-exact - the ROADMAP ≥ 95% target met on both
  (`benchmarks/coverage/spec-v1/`).

## [0.1.0] - 2026-09-19

Initial public release.

### Added

- Zig core (ReleaseSafe) compiling a JSON Schema subset (canonical-v1 profile)
  and literal sets into a DFA-like grammar; token bitmasks; parser state kept
  across generation steps.
- Strict input policy: unsupported keywords and unproven schema/tokenizer pairs
  are rejected at compile time with a JSON Pointer; the engine never silently
  continues unconstrained.
- Exact completion reachability for incomplete vocabularies on finite languages
  (`src/complete.zig`); dead-end tokens are neither masked nor accepted.
- Modes `lazy` / `adaptive` (LRU mask cache with adaptive admission) /
  `precompute` (experimental warm-up at compile time).
- Hard memory budgets (context, cache, session, schema), cancellation flags,
  per-call work limits, immutable compile-artifact cache (ADR-0004).
- Versioned C ABI (`include/bolorgir.h`, `blg_*`) with opaque handles and
  `blg_status` error codes.
- Python package (CPython abi3, 3.10+) with tokenizer validation by the actual
  backend decoder, Hugging Face Transformers adapter, batched mask filling,
  and a GPU mask-unpacking path.
- Benchmark harness and the control-series acceptance protocol with published
  reports (`benchmarks/README.md`, `benchmarks/reports/`).

### Notes

- MVP scope and limitations: see `docs/supported_features.md`
  (no `anyOf`/`oneOf`/`allOf`, `pattern`, `format`, numeric bounds, regex or
  arbitrary CFG yet; Linux x86_64 glibc).
- The project was previously developed under the working name `zig-constraints`;
  commit ids changed during the rename - see `benchmarks/reports/README.md`.
