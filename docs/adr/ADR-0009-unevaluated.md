# ADR-0009: P6a unevaluated* - compile-time scenario synthesis (spec-v1)

- Status: **accepted** (2026-09-21, ROADMAP revision 2 P6 item;
  `unevaluatedProperties`/`unevaluatedItems`, dialects 2019-09 and
  2020-12 only).
- Context: P1-P5 compile JSON Schema to a finite grammar tree
  (`src/schema.zig` -> `src/grammar.zig`); the parser is an NFA with at
  most `MAX_THREADS_CAP = 64` live threads (`src/parser.zig`). The
  unevaluated* keywords are *annotation-collecting* assertions: a property
  or element is "evaluated" if any in-place applicator that accepted the
  instance touched it, and the keyword then applies its subschema to every
  remaining property/element. A direct grammar lowering does not exist:
  which keys are evaluated is data-dependent (an `anyOf` branch may or may
  not match), so the set of still-open keys is not known at compile time.

## The spike: runtime annotation tracking vs compile-time scenarios

**Runtime tracking.** The parser threads would carry an annotation
bitset (seen keys / seen indices) that combiners union on success, and the
unevaluated* guard would read it at the container end. Cost: a new
per-thread side store with COW/ownership/accounting (ADR-0006 D2), new
comb semantics (`oneOf` must know *which* branch matched, not just that
exactly one did), mask and completion handling of the annotation state,
and a second key-set machinery next to the open-object one. The state
explosion is real: annotation sets multiply the semantic states.

**Compile-time scenarios (chosen).** The data dependency is enumerable at
compile time because the supported applicator set is finite. A *scenario*
is one acceptance hypothesis of the enclosing schema: a list of conjuncts
(grammar nodes that must all accept) plus the *eval set* (the object
keys / array indices evaluated under that hypothesis). The schema's
language is the disjunction of its scenarios, and a document accepted
under several scenarios is covered by the scenario with the unioned eval
set - which the synthesis constructs explicitly:

- `allOf` / `$ref` (local): the scenarios product in (both sides accept,
  eval sets union); cycles cut at the bounded re-expansion of ADR-0008.
- `anyOf`: every non-empty subset of live branches is one variant (a
  document matched by several branches is covered by their joint subset);
  capped at 5 live branches (31 subsets).
- `oneOf`: the exactly-one comb comb conjoins every scenario; each
  branch's scenarios are one variant (mutually exclusive by the comb's
  verdict).
- `if`/`then`/`else`: the success side is the product of the if- and
  then-scenarios, the failure side is `not(if)` conjoined with the
  else-scenarios.
- `dependentSchemas`: the trigger-key-present and trigger-key-absent
  variants.

The whole synthesis is capped (`UNEVAL_SCENARIO_CAP = 32` scenarios);
past the cap the schema refuses `UNSUPPORTED_FEATURE` with a pointer.

## Decision: scenario walk with guards, masks and merges

1. **Guard nodes.** `unevaluatedProperties: S` compiles to an open-object
   guard whose undeclared-key value schema is `S`, conjoined with every
   scenario; the guard's *declared* keys are the scenario's eval set
   (dynamic open-object key set, P1). `unevaluatedItems` symmetrically
   over tail indices (after `prefixItems`/tuple `items`/matched
   `contains`). Scenarios with an identical eval set share one guard
   (`(C1 /\ G) \/ (C2 /\ G) = (C1 \/ C2) /\ G`); conjuncts shared by
   every scenario hoist out of the disjunction, in the first-appearance
   order of the first live scenario (semantics-spec-v1 §4.3).
2. **Container masking.** When the schema's behavior outside the guarded
   container kinds is statically analyzable (`outsideAccept`: accept-all
   or accept-none), the scenario walk compiles restricted to the guard
   kinds (`uneval_mask`, consumed by the typed cores, one-shot `top_mask`)
   and one top-level arm covers the outside; otherwise the walk compiles
   fully wrapped. The analysis is three-valued; `unknown` falls back to
   the unmasked form.
3. **Merges.** Open-object conjuncts of one scenario merge into a single
   object node (`mergeOpenObj`): declared props union in first-appearance
   order, the undeclared-key value conjoins, guards fold in. The merge is
   refused (falls back to separate conjuncts) for shapes that would change
   the language: `propertyNames`, forbidden names, two patterns, or a
   pattern overlapping declared keys.
4. **Key tracking.** Guards run with `track_keys` so `extra_required`
   (required keys declared by *other* conjuncts) is checked against the
   seen-key set at dispatch; ban-dependencies dispatch unconditionally.
5. **Budget guards as compile-time refusals.** Two shapes are semantically
   covered by the model but exceed the parser thread cap at accept time;
   they are detected statically and refused `UNSUPPORTED_FEATURE` with a
   JSON pointer instead of failing at runtime:
   - a `oneOf`/`anyOf` branch that itself involves a combinator -
     directly or through the in-place applicators
     `allOf`/`if`/`then`/`else`/`dependentSchemas` and local `$ref`s
     (`branchInvolvesCombinator`; the pinned-suite case "dynamic
     evaluation inside nested refs");
   - `unevaluatedItems` next to two or more conjunctive `contains`
     subschemas (`adjacentContainsCount`; `contains` under conditional
     applicators does not count).

## Refusals (all `UNSUPPORTED_FEATURE` with a JSON pointer)

- External `$ref` next to unevaluated* (the eval walk would cross
  registry documents).
- More than 5 live `anyOf` branches or more than 32 scenarios.
- More than 4 `dependentSchemas` entries; more than 8 `patternProperties`
  patterns in a guard.
- Conditional (data-dependent) evaluation under a nested unevaluated*
  subschema - the static other-kind shortcut cannot transport it.
- The two runtime-budget guards of Decision 5.
- `$dynamicRef`/`$recursiveRef` stay refused here regardless (P6b
  implements them elsewhere; next to unevaluated* the walk's dynamic
  scope is not the evaluation dynamic scope, ADR-0010 D5).

## Consequences

- Oracle (pinned Test Suite 23.2.0 + jsonschema 4.26.0, profile spec-v1,
  draft2020-12): PASS 861 -> 1015 (+154), SKIPPED 356 -> 202, MISMATCH 0,
  ENGINE_ERROR 0; the only remaining unevaluated* refusals are the two
  budget-guard cases (23 rows). canonical-v1 is unchanged (95 PASS, same
  rows).
- The parser, grammar node set and mask pipeline are untouched; the whole
  feature lives in `src/schema.zig`.
- `zig build test` (244 tests) and `pytest tests/` (538, including
  `tests/test_spec_v1_p6a*.py`) pass.
