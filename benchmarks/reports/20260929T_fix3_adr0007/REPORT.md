# fix3 wave: strict ADR-0005 D3 + oneOf residual certificates (2026-09-29)

Build: lib sha256 `2be5163a...`, combined src/include/build.zig sha256
`2950bd1d...` (`{ find src include -type f; echo build.zig; } | LC_ALL=C
sort | xargs sha256sum | sha256sum`).

## Scope

1. Blocker (a) - strict ADR-0005 Decision 3: a budget-exhausted
   reachability proof is treated as UNKNOWN, not alive. `fill_mask` fails
   and `blg_accept_token` refuses the token with
   `RESOURCE_LIMIT` instead of silently admitting a token that may
   dead-end (`src/mask.zig` setNodeTokens/fillMaskBrute,
   `src/complete.zig` stateAlive, `src/c_api.zig` accept path).
   Regression: `oneOf` over `^(ab|c)$` / `^(ab|d)$` (the mask at `"`
   admits exactly `c`/`d`, never `a`) pinned in
   `tests/test_spec_v1_r1_residual.py` across lazy/adaptive x fast-path
   on/off - 24/24 pass.
2. Blocker (b) - oneOf vote-link doom by exact residual equality
   (`src/complete.zig` `linkedThreads`): literal frames link by
   remaining suffix, lit_trie frames by subtrie language equality
   (structural), str_pat frames by equal codepoint windows plus
   right-language equality of the DFA states (bounded greatest-fixpoint
   over the reachable state pairs, `RESIDUAL_EQ_CAP`). The overlap
   counterexample after `"a` is now certified dead in closed form: zig
   tests prove `certifyState == .dead` and `stateAlive` settling with
   `fill_budget = 1` (no search). Fill cost at the counterexample's `"`
   state: 6.3 ms -> 2.6 ms per fill (search eliminated; the remainder is
   the ordinary 256-token byte-walk).

## Gate results

- `zig build test`: PASS (incl. 2 new residual-doom tests).
- Audit repro `docs/roadmap_implementation_audit_20260922_repro.py`:
  34/34 checks, 0 violations, exit 0. (Command retired: the dated repro
  script is superseded by the maintained pytest modules
  `tests/test_spec_v1_r1_residual.py`, `tests/test_spec_v1_r2_r3.py`,
  `tests/test_audit_regressions.py` and
  `tests/test_maskbench_serializer_hook.py`.)
- `tests/test_spec_v1_r1_residual.py` + `tests/test_lazy_choice_spec_v1.py`:
  28/28 PASS.
- `tests/fuzz/test_fuzz_boundary.py`: stale invalid-value expectations
  for `max_threads_per_state` updated to the raised MAX_THREADS_CAP=128
  (65/1000 are valid now); PASS.
- Oracle (6 runs, `tests/oracle/results/oracle-fix3-*`): canonical-v1
  clean (identical outcome counts to fix2); spec-v1 REGRESSED vs fix2 by
  ENGINE_ERROR (RESOURCE_LIMIT from fill_mask): draft4 +40, draft6 +54,
  draft7 +56, 2019-09 +94, 2020-12 +94; PASS counts drop by the same
  amounts; MISMATCH/KNOWN_GAP unchanged. Bisected: identical failures
  with and without blocker (b) - the regression is the strict-D3
  semantics of blocker (a) itself, surfacing states the certificates do
  not cover. Failing families: oneOf/allOf numeric intersections,
  dependentSchemas/dependencies, maxProperties, contains/minContains/
  maxContains, propertyNames, additionalProperties+patternProperties,
  if-then-else, unevaluated*, dynamicRef/recursiveRef, multipleOf.
- 26-case MaskBench recheck (then `/tmp/recheck_all25.py` with a copy
  alongside this report; now maintained as
  `benchmarks/recheck_cert_residuals.py --suite cases26`): 14/26 cases
  processed before the 30-min timeout (the
  failing fills storm 7-105 s each): 4 ok / 10 bad, all bad =
  `error:RESOURCE_LIMIT` on big real schemas (o77317, o358, o360,
  o17072, v4-config, o1184). Pre-strict-D3 this list was 25/26 ok.
- Full pytest (`tests/ + python/tests/`, fuzz ignored, live oracle
  dialects deselected): 33 failed / 971 passed / 42 skipped. Breakdown:
  14 x `python/tests/test_hf_adapters.py` were ENVIRONMENT (system numpy
  1.21.5 broken after the reboot wiped `~/.local`; fixed by
  `pip install --user --force-reinstall numpy` -> 2.2.6, transformers
  5.17.0 imports again; rerun: 32 passed / 20 skipped, full
  `python/tests/`: 132 passed / 25 skipped - matches the fix2 gate). The
  remaining 19 failures are the strict-D3 regression on "must accept"
  valid documents: `test_spec_v1_p2.py` x8 (propertyNames,
  dependentSchemas, dependencies, contains x5),
  `test_spec_v1_p2_reachability.py` x2, `test_spec_v1_p3.py` x1
  (oneOf number vs integer), `test_spec_v1_p3_reachability.py` x2,
  `test_spec_v1_p4_numbers.py` x1, `test_spec_v1_p4_pattern.py` x2,
  `test_spec_v1_p4_pattern_reachability.py` x1,
  `python/oracle/test_oracle_spec_v1.py::test_spec_v1_no_engine_errors`,
  `python/test_edge_cases.py::test_artifact_cache_budget_and_reset`.
  Same failing families as the oracle regression above.

## Open decision

Strict D3 converts "mask may admit a dead token (DEAD_END later)" into
"valid documents cannot be generated at all" on common spec-v1 shapes:
the states are infinite-space (open-ended digit/key runs), so no search
budget fixes them - only closed-form certificates do. Options:

1. Keep strict D3 and implement the remaining certificate families
   (numeric range intersection for comb groups; certOpenObj for
   max_props/deps/track_keys/capture; contains counters; ...). Large,
   exact, keeps the no-dead-token guarantee.
2. Relax (a): permissive fallback (pre-R1) by default with strict mode
   behind a context config flag, plus a stats counter for unproven
   admissions.
3. Hybrid: strict D3 only for shapes whose compile-time analysis proves
   full certifier coverage, permissive otherwise.

---

# Step 3: closed-form certificate families for the strict-D3 residual (2026-09-30)

Build: lib sha256 `58a7f25e32660ca66b55263f5967561d9d434ae0c2f1c7a689868300a63689bf`
(Debug) / `bac528202cf7f8402bc5894cde671ca888587c86b35489a253196450ec1fde7c`
(ReleaseSafe, the gate build below), combined src/include/build.zig sha256
`cdbd8163c00db8cbcff07e442f591cea1330fbd77c191c09ca368aa56ac3f57f`.

## Certificate families landed (all closed-form; sim-verified synth candidates, failed candidates prove nothing)

| # | Family (measured oracle ENGINE_ERROR source) | Certificate | Where |
|---|-----------------------------------------------|-------------|-------|
| 1 | min/maxProperties-gated objects (`{"a":1,"b":2` + `,` poison next-token state; search diverged on unbounded junk-key spellings) | `certOpenObjCounted` + `seenCountC`: exact dead rules (pending/mandatory key with seenCount >= max_props; distinct-unsatisfied-required count exceeding max_props; min_props unachievable from the remaining declared/fresh-key supply). Alive verdicts of the delegated inner analyses are downgraded to undecided; alive goes through the synth. | src/complete.zig:2296 |
| 2 | contains/minContains/maxContains doom (`[1,"a",` with a numeric contains) | `repeatContainsDoom`: in-flight element rooted in a machine disjoint from a numeric-only contains node, deficit `matched + (max - count - 1) < min_contains` | src/complete.zig:1838 |
| 3 | contains synth | `emitRepeat` optimistic trial: spends the trial slot assuming the in-flight element matches the contains (closes immediately when the deficit clears) | src/synth.zig (emitRepeat) |
| 4 | dependentSchemas mid-object (`{"a":1,"b":` where dep `a -> {required/typed b}`) | Schema-dep virtual facets: the dep's open_obj node merges into the object's obligation set at synth time (`virtualizableDep`, `mkVirtualRef`, bounded 8-round fixpoint with collectObligations); parser retains the open_obj key chunk through colon/value/sep so pending intersections can dispatch virtual facets | src/synth.zig:2059/2107, src/parser.zig:1258/1684 |
| 5 | dep-value doom (`{"a":1,"b":` + `"`/`[`/`{` value starts) | `openObjDepValueDoom`: schema-dep in force whose constraint for the in-flight key is unreachable/numeric-only while the value root is disjoint -> dead | src/complete.zig:1924 |
| 6 | multipleOf mid-integer (`{"multipleOf": 0.5}` after `1` etc.) | `emitNumMult`: modular completion in int_digits states, k = digits10(div), x = (-rem * 10^k) mod div zero-padded, V = P*10^k + x == 0 (mod div) | src/synth.zig:535 |
| 7 | allOf of patterns (`allOf: [{pattern: a}, {pattern: b}]`, incl. mid-escape/mid-UTF-8) | `emitPatSuffixMerged`: BFS over the product DFA of the sibling in-flight str_pat frames (cap 4096 product states, <= 6 siblings), restricted to siblings coupled by the SAME allOf instance (`innerAllOfInst`); anything else falls back to the per-frame path | src/synth.zig:711/738 |
| 8 | oneOf{number, integer} `-0e` doom (zero mantissa x any exponent is integral -> both branches accept every exponent continuation -> verdict never "exactly one") | `numExpLockstep`: cross-kind vote-link in `linkedThreads` between a num_v frame and an all-zero-mantissa int_num frame in the same exponent state (the automata share the exponent transitions byte-for-byte and `intNumValueOk` never rejects when nz == false) | src/complete.zig:1469 |

Fix folded in during gating: `emitPatSuffixMerged` initially merged str_pat
siblings across ANY comb coupling, which completed both branches of
`oneOf: [{pattern: "^(ab|c)$"}, {pattern: "^(ab|d)$"}]` jointly and tied the
vote (`zig test` r1 pattern-overlap regression); the allOf-only restriction
above is the corrected rule.

## Gate results

- `zig build --release=safe` / `zig build test --release=safe`: PASS (277/277
  zig tests; the Debug build was green identically).
- Audit repro `docs/roadmap_implementation_audit_20260922_repro.py`: 34/34
  checks, 0 violations, on the ReleaseSafe lib. (Command retired: the dated
  repro script is superseded by the maintained pytest modules
  `tests/test_spec_v1_r1_residual.py`, `tests/test_spec_v1_r2_r3.py`,
  `tests/test_audit_regressions.py` and
  `tests/test_maskbench_serializer_hook.py`.)
- `python/` pytest: 132 passed / 25 skipped (fix2 gate parity).
- `tests/oracle/test_oracle_spec_v1.py::test_spec_v1_no_engine_errors` is
  converted into a ratchet over the pinned residual (exact-count match over
  the default draft2020-12 run - 24 case groups / 49 rows; shrinking the pin
  is forced when a family lands, growth fails as a regression): 4/4 PASS.
- Acceptance repro (ADR-0005 R1): `tests/test_spec_v1_r1_residual.py` 24/24
  PASS; explicitly, at the prefix `{"a":"ingest","b":"` the mask admits
  exactly `f`/`h` and never `i` in all four modes (lazy/adaptive x fast path
  on/off).
- Oracle spec-v1, ENGINE_ERROR (fix2 -> fix3 -> fix3g = this step):

  | dialect | fix2 | fix3 | fix3g |
  |---------|------|------|-------|
  | draft4      | 0 | 40 | 27 |
  | draft6      | 0 | 54 | 35 |
  | draft7      | 0 | 56 | 37 |
  | 2019-09     | 0 | 94 | 48 |
  | 2020-12     | 0 | 94 | 49 |

  MISMATCH and SKIPPED counts identical to fix2/fix3 everywhere; the
  canonical-v1 run is bit-identical (95 PASS / 0 ENGINE_ERROR) - no
  reachability flips outside the strict-D3 class.
  Fixed files (2020-12): contains 4->0, maxContains 10->0, minContains 17->0,
  maxProperties 5->0, dependentRequired 2->0, dependentSchemas 10->6,
  oneOf 18->15. (draft4: dependencies 12->6, maxProperties 4->0, oneOf 16->13.)
- 10-case MaskBench recheck (then `benchmarks/diag_cert_recheck.py`, since
  retired; the accept/RESOURCE_LIMIT outcomes are now pinned by
  `benchmarks/recheck_cert_residuals.py --suite cases26`, the
  undecided-reason histogram by `benchmarks/diag_cert_histogram.py`):
  5/10 accept
  (o77317t0 13s, o358 t0/t1 31s each, o360t0 28s, o1184t0 15s - same rows as
  fix3, comparable times); still RESOURCE_LIMIT: o360 t1 byte 129
  (oneOf vote-link across object branches), o17072 t0/t1 byte 84
  (dep-schema virtualization bail + openobj gate), v4-config t0/t1 bytes
  177/557 (comb_group over unevaluated*/$ref towers).
- Full 26-case recheck (then `recheck_all25.py` alongside this report, since
  retired; now maintained as `benchmarks/recheck_cert_residuals.py --suite
  cases26`, raw rows preserved in `recheck_out.log` beside this report;
  ReleaseSafe lib): 14 ok / 12 bad, all bad = RESOURCE_LIMIT (o360t1,
  o17072 t0/t1,
  v4-config t0/t1 - the known five - plus o35868 t0/t1, github-issue-forms t0,
  minecraft-biome t0/t1, o17529 t0/t1, which fix3 never reached inside its
  30-min timeout). fix2 was 25/26 ok, so the strict-D3 residual on big real
  schemas is still 12 rows; the accepting rows run 0.3-11 s under
  ReleaseSafe (o77317: 13 s Debug -> 2.8 s ReleaseSafe).

## Remaining known limitations (each with a counterexample; pinned in the ratchet)

1. oneOf vote-link across "any"/boolean branches: `{"oneOf": [true, true, true]}`
   (genuinely dead, not certified) and `{"oneOf": [{"type": "number"}, {}]}`.
2. oneOf/allOf numeric intersections need an intersection-aware emitter:
   `{"allOf": [{"maximum": 30}, {"minimum": 20}]}` (byte 0),
   `{"oneOf": [{"type": "integer"}, {"minimum": 2}]}`, mid-value after `2`.
3. additionalProperties:false + patternProperties key planning:
   `{"properties": {"foo": {}, "bar": {}}, "patternProperties": {"^v": {}}, "additionalProperties": false}`
   (fill at `{"`); also the non-ASCII pattern variant.
4. dependentSchemas with closed or comb-carrying dep schemas (virtualization
   bails): `{"properties": {"foo": {}}, "dependentSchemas": {"foo": {"properties": {"bar": {}}, "additionalProperties": false}}}`;
   typed-value deps mid-key (`single dependency`, fill at byte 18).
5. propertyNames-gated objects mid-key: `{"propertyNames": {"maxLength": 3}}`
   (fill at byte 12), `{"propertyNames": false}` after `{"`.
6. if/then/else numeric intersections: `{"if": {"exclusiveMaximum": 0}, "then": {"minimum": -10}}` (fill at byte 2).
7. unevaluatedProperties/unevaluatedItems over if/then/else, not, nested
   patternProperties, propertyNames annotations (5 case groups, both 2019-09
   and 2020-12).
8. dynamicRef/recursiveRef dynamic-scope states (3 case groups).
9. num_mult completion bails in frac/exp states and for divisors with
   div_exp10 > 0: `{"type": "integer", "multipleOf": 0.123456789}`
   (`float division = inf`).
10. Merged str_pat: product > 4096 states or > 6 siblings bails to per-frame;
    emitClosedObjMerged does not create virtual facets (deps on closed-object
    states stay undecided); the alt axis applies only to emitClosedObjMerged.

Performance note: the recheck rows cost 5-112 s each (synth + sim per fill on
big real schemas); no regression vs fix3 on the accepting rows.
