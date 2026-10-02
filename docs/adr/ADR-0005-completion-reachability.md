# ADR-0005: completion reachability for product and union states (spec-v1)

Status: accepted (2026-09-20, ROADMAP revision 2 §4.1; PA deliverable 1).
Context: spec §3.1, ADR-0003 (the current completion contract),
docs/semantics.md §10 (the constructive completability argument),
`src/mask.zig:53` (`filtered = !tok.byte_complete and g.finite_literal`),
`src/complete.zig` (the token-level reachability search). This ADR governs
the `spec-v1` profile only; canonical-v1 paths keep their current behavior
bit for bit. It is the prerequisite for all P2/P3 mask work: no mask code
for the new node kinds exists before their check from Decision 2 exists.

## The problem

The byte-complete fast path of ADR-0003 relies on a constructive property
of the canonical-v1 grammar (semantics.md §10): every live byte prefix can
be completed to a finished document. New intersections do not inherit this
property. Counterexample (ROADMAP §4.1): intersect the string languages
`{ab, xy}` and `{ac, xy}`. The prefix `"a` is live in every branch, yet
there is no common completion; even all 256 single-byte tokens do not
remove the dead end (`b` kills the second branch, `c` kills the first,
every other byte kills both). The schema itself is satisfiable (`"xy"`),
so component-wise liveness is necessary but not sufficient, and a dead
prefix is not a proof of an empty schema.

## Decision 1: product states and the common-completion predicate

A session state for the new node kinds carries, per live branch, a
component state (branch states for unions, component states for
intersections). A product state is *component-live* when every component
state is non-error.

- `common_completion(P, V)` holds iff there exists a finite sequence of
  ordinary (non-EOS, non-special, non-empty) tokens from the vocabulary `V`
  that keeps every component of `P` live and ends in a state where every
  component accepts.
- Mask rule (spec-v1): a token is admitted at `P` iff the product state
  after feeding it has a common completion; EOS is admitted iff `P` is
  already all-accepting. This generalizes ADR-0003 Decision 1 and SPEC
  §3.1 from finite literal languages to every new node kind.
- The predicate exists at two levels. Token level (runtime, vocabulary
  `V`): the mask rule above. Language level (compile time, all 256 bytes):
  non-emptiness of the product language, used for whole-schema
  satisfiability (the `empty language` outcome bucket) and compile-time
  pruning. With a byte-complete vocabulary the levels coincide (any byte
  continuation is served by single-byte tokens, the ADR-0003 argument), so
  the unfiltered fast path of `src/mask.zig` remains valid exactly where
  the language-level predicate holds for the compiled grammar.
- Component-liveness without a common completion admits nothing: a mask
  call at such a state is a `DeadEnd` (existing rule, semantics.md §10),
  never a partial mask and never an `empty language` classification.

## Decision 2: per-node-kind checks, termination, supported combinations

The check is a budgeted graph search over product states with a visited-set
memo keyed by the state byte image (the PA state-model ADR defines the key
format; the current `parser.stateKeyLen`/`writeStateKey` mechanism is the
template). Verdicts: `REACHABLE`, `EMPTY` (proved), `BUDGET_EXHAUSTED`,
`UNDECIDED` (combination outside the supported set).

| Node kind | State composition | Common-completion check | Termination | Supported combinations |
|---|---|---|---|---|
| `anyOf` (union) | set of branch states | disjunction: some branch has a completion | per-branch checks terminate | branches whose own checks are supported; a branch with an undecided check refuses the schema at the branch pointer |
| `allOf` (intersection) | tuple of component states | product search (Decision 1) | component state spaces are finite under the session caps (depth/threads/counters), so the product is finite; the visited set bounds the search | components from: closed/open objects, finite literals/enum/const, bounded strings, bounded arrays, numeric ranges, pattern DFAs, supported `not`; intersections with an unbounded component (unbounded string/repeat) only where the component's own per-state continuation space is finite or a compile-time lemma decides it (e.g. unbounded array minus finite exclusions) |
| `oneOf` (exclusivity) | branch states + accept-status vector | product search whose accepting states are end-of-value states with exactly one accepting branch | as `allOf` | branch pairs with decidable exclusivity: disjoint types (subtype-aware: `integer` ⊂ `number`), disjoint enum/const sets, ordered numeric ranges, required-key exclusions; the `{"oneOf":[{"type":"number"},{"type":"integer"}]}` case is supported (`1` forbidden - both accept; `1.5` allowed); exclusivity that is not decidable is refused at compile with the `/oneOf` pointer, never replaced by `anyOf` |
| `if`/`then`/`else` | lowered to `(if ∧ then) ∨ (¬if ∧ else)` | union of two products | inherits the union and product rows | the `if` complement must be a supported `not` form |
| limited `not` | witness automaton of the complemented form | product search whose accepting states require the witness state (violation observed) | witness automata are finite | complements of `{const}`, `{enum}`, `{type}`, `{required}` (key-presence exclusions); every other complement refused at compile with a pointer |
| `contains` counters | array state + counter + remaining capacity | product search over element-schema × contains-schema per remaining slot, accepting states with the final counter in `[minContains, maxContains]` | finite when `maxItems` is finite; for `UNBOUNDED maxItems` a compile-time lemma decides: `minContains` is satisfiable iff the `contains` subschema is satisfiable (decided once, per its own check) and `maxContains` is absent | both forms |
| `pattern` DFA | DFA state (× length counter when bounded) | product of the DFA with the other components | the DFA is finite; with a finite `maxLength` the product with the counter is finite; without it, standard DFA-product emptiness (reachability over a finite graph) | the P4 ECMA-subset; unsupported constructs refused at compile |
| numeric constraints | numeric lexeme automaton + interval state | residual interval-prefix test (Decision 4) at every prefix; no search | O(1) per state, always terminates | exact decimal `minimum`/`maximum`/`exclusive*`; `multipleOf` exact for integers (modular tracking), decimals per P4 or refusal |

An `allOf` merge remains an optimization under the equivalence lemmas of
ROADMAP P3; the product construction above is the exact fallback and the
oracle the merge lemmas are proved against.

## Decision 3: budgets, undecided cases, and the empty-language bucket

- The analysis charges the per-call work budget (`work.Work`), plus an
  explicit visited-product-state budget from the context limits. Both are
  recorded in the coverage report when they fire.
- `UNDECIDED` maps to `BLG_ERR_UNSUPPORTED_FEATURE` with a JSON pointer to
  the node (compile time; at mask time an undecided combination cannot
  occur - the schema was refused at compile).
- `BUDGET_EXHAUSTED` maps to `BLG_ERR_RESOURCE_LIMIT` at compile; at mask
  time it fails the mask call with `ResourceLimit` (the existing
  `src/mask.zig` rule: a resource limit anywhere fails the mask, never a
  partial mask).
- `EMPTY` maps to the `empty language` outcome bucket only when the proof
  is at the whole-schema root product (the initial state). A dead prefix
  of a satisfiable schema - `EMPTY` from a non-initial state - is a
  `DeadEnd` at runtime and is never reclassified as schema-empty (ROADMAP
  §1, §4.1 item 2).
- Verdicts are memoized per session like `complete.Cache`; a cancelled or
  resource-limited search fails the call and leaves no in-progress entries
  (the existing cache discipline). The cache never changes a verdict:
  cache on/off equality is an acceptance criterion (Decision 6).

## Decision 4: residual satisfiability on live prefixes

Keywords whose verdict is fully known only at finished JSON get residual
predicates evaluated on live prefixes inside the common-completion search
(they prune the search and are part of the emptiness argument):

- `contains`: with `maxItems` finite, the remaining slots must cover the
  deficit: `count + remaining >= minContains` and `count <= maxContains`;
  `]` is admitted only when `minContains <= count <= maxContains`. With
  `UNBOUNDED maxItems`, the compile-time lemma of Decision 2.
- `dependentRequired`/`dependentSchemas` (and draft-07 `dependencies`):
  when the trigger key has been seen, a not-yet-seen required key must be
  among the remaining allowed keys (a closed object knows them statically;
  an open object always admits more keys); `}` is admitted only when every
  triggered dependency is discharged, and an applicable dependent schema
  must itself pass the product check.
- `uniqueItems`: the seen-value set (candidate hash plus exact structural
  equality, ROADMAP §4.2) must leave a completion: the element language
  minus the finite seen set must be non-empty for the remaining slots.
  Decidable for the supported element kinds: an unbounded language minus a
  finite set is always non-empty; a finite language is checked by
  enumeration.
- numeric constraints: at every numeric prefix (sign, integer digits,
  fraction, exponent) the exact decimal interval-prefix test decides
  whether some digit continuation lands inside the constraint interval;
  a prefix with no such continuation is dead even when the lexeme is
  well-formed. Binary64 arithmetic alone never decides a boundary
  (ROADMAP A8.3).

## Decision 5: the `{ab, xy} ∩ {ac, xy}` regression case

The counterexample is a permanent regression case, encoded now as a
standalone prototype of the Decision 1-3 algorithm:
`tests/test_completion_reachability_adr0005.py`. The prototype models
finite languages as prefix automata, builds the product, runs the budgeted
common-completion search, and asserts:

1. the prefix `"a` is component-live in every branch of the product;
2. the verdict from the `"a` product state is `EMPTY` (no common
   completion), while the verdict from the initial state is `REACHABLE`
   via `"xy"` - the whole-schema bucket is satisfiable, not empty language;
3. all 256 single-byte tokens from the `"a` state dead-end;
4. budget exhaustion maps to `RESOURCE_LIMIT` and an undecided combination
   to `UNSUPPORTED_FEATURE`, neither to the empty-language bucket;
5. token-level verification on a small finite product: every allowed token
   participates in a completion and EOS is admitted exactly in
   all-accepting states.

When P3 implements `allOf`, the same case is re-encoded against the real
engine: the prototype pins the semantics, the engine test pins the
implementation. The `{"oneOf":[{"type":"number"},{"type":"integer"}]}`
counterexample of ROADMAP P3 gets the same treatment at that phase.

## Decision 6: acceptance criteria (binding for P2/P3/P4)

For every new node kind, and for every supported combination of Decision 2
that a phase activates:

1. Finite small languages: exhaustive continuation enumeration. For every
   reachable state the allowed-token set computed by the engine equals the
   set computed by exhaustive enumeration of the language, including the
   EOS bit.
2. Every allowed token is verified: feeding it and continuing with allowed
   tokens reaches a completed document; the completed document validates
   against the schema under the pinned oracle (the P0 harness).
3. Cache on/off equality: masks are identical with the completion memo
   enabled and disabled.
4. Vocabulary coverage: each case runs on a byte-complete vocabulary and
   on the supported incomplete vocabularies (the segmentability-checked
   ones of ADR-0003/ADR-0004).
5. The Decision 5 regression case runs in the suite permanently.

## Consequences

- P3 mask work is gated on this contract: no `allOf`/`anyOf`/`oneOf`/
  `if`/`then`/`else`/`not` masks before the common-completion check for
  the node kind exists; P2 `contains`/`uniqueItems`/dependencies masks
  likewise require their Decision 4 residuals.
- canonical-v1 is unchanged: the `filtered` fast path (`src/mask.zig:53`)
  and `src/complete.zig` keep their semantics; the new analysis lives
  behind the `spec-v1` profile and its own product-state machinery.
- The completion-cache key extends to product states; the key format and
  the copying/ownership rules come from the PA state-model ADR (the byte
  image mechanism of `src/parser.zig:770` is the template).
- Compile-time language-level emptiness feeds the coverage report's
  `empty language` bucket; per ROADMAP §1 it is recognized, not compiled,
  and requires the whole-schema proof of Decision 3.
- Refusals keep their JSON pointers, so the Decision 2 support table
  translates directly into the deviation list of ROADMAP §9.
