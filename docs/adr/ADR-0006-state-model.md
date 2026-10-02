# ADR-0006: state model, ownership and equality for spec-v1

- Status: **accepted** (proposed 2026-09-20 as PA deliverable 2 of ROADMAP
  revision 2, sections 4.2 / A4+A6; adopted with the PA phase, see the
  ROADMAP section 9 tracking table). Implemented during P1-P6; ADR-0008
  settles the recursion choice that Decision 7 leaves open. The "current
  state" references below describe the code before P1.
- Context: ROADMAP 4.2 items 1-6; current state `src/parser.zig:15-38`
  (`Frame` union, `Thread = { frames[64], len }`, `State = { threads[64], n,
  max_threads }`); cache keys from the byte image of frames
  (`src/parser.zig:770` `hashState`, `eqlStates`, `writeStateKey`);
  `max_threads_per_state` counts parse branches, not CPU threads
  (`src/parser.zig:55` `spawnThread`, `docs/supported_features.md` section 6);
  mask cache key/equality (`src/cache.zig:10,108`); memory accounting
  (`src/alloc.zig`: `Accounting`, `SessionAccount`, `Limited`); artifact
  ownership (ADR-0004); measured sizes: `Frame` = 20 B, `Thread` = 1284 B,
  `State` = 82180 B (~80 KiB, copied per accepted token via the ping-pong
  pair in `src/c_api.zig:437`).

`canonical-v1` keeps its POD state, bitwise copying and byte-identity keys
unchanged. Everything below defines a new state model ("v2") used only by
the `spec-v1` profile.

## Decision 1: AnyJSON, `true`/`false`, depth limit

- `true` as a (sub)schema compiles to the AnyJSON production; `false`
  compiles to an empty-language node. `false` at the root is the "empty
  language" outcome bucket (ROADMAP section 1: recognized, not compiled);
  `false` in subschema position participates in the 4.1 analyses as a node
  with an empty language.
- AnyJSON is one grammar node kind with the language
  `value := null | true | false | number | string | [value*] | {string: value*}`.
  It is lowered at compile time by bounded unrolling to a chain
  `a_0 .. a_D`, `a_i = choice(scalars, array(a_{i+1}), object(a_{i+1}))`,
  `a_D = scalars`, where `D = max_depth - k` and `k` is the structural
  nesting depth of the AnyJSON position in the schema. Only existing node
  kinds result (`choice`, `seq`, `repeat` with `UNBOUNDED` max, `object`,
  `str`/`int_v`/`num_v`), so coverage, completion reachability and
  `UnprovenPartialVocab` gates need no new machinery (AnyJSON is an open
  class: it requires a byte-complete vocabulary).
- Exact behavior at the limit: the accepted language is "JSON values whose
  structural depth (scalars = 0, array/object = 1 + max of children) is at
  most `max_depth`" (default and hard cap 64, `MAX_DEPTH_CAP`). A token
  that would open a value deeper than the budget is masked out; feeding it
  under `accept` is `INVALID_TOKEN`. This is a documented depth limit
  (deviation list, P1 acceptance), not a refusal and not weakened
  semantics: the language is exactly specified and the masks stay exact
  for that language.
- Rejected alternative: accept-then-error at runtime. A runtime depth
  failure is a dead end the mask cannot see, which violates the 4.1
  contract; the limit must be a property of the compiled language.

## Decision 2: state representation - POD spine plus copy-on-write side tables

- The spine stays what it is: fixed arrays of frames and threads, caps
  `MAX_DEPTH_CAP = 64` / `MAX_THREADS_CAP = 64`, `max_threads` stays
  parse-branch accounting (4.3). `Frame` grows new variants for the
  dynamic features and may carry a 32-bit handle into a per-state side
  store; `Frame` stays <= 32 B.
- Side store: a per-state chunked store. Each dynamic datum is an
  immutable-once-published chunk `{ refcount, kind, len, payload }`:
  - seen keys: persistent set of accepted property names (open objects,
    dynamic key set, `propertyNames`/`min|maxProperties` support);
  - seen values: candidate list `(u64 hash, value offset)` for
    `uniqueItems` (payload values serialized under the semantics-spec-v1
    policy);
  - counters: `contains`/`min|maxContains`, `min|maxProperties`, repeat
    counters beyond the inline u32;
  - evaluation records (Decision 4);
  - recursion return stack (Decision 7).
- Copying: a state copy = bitwise spine copy + retain of each referenced
  chunk (O(spine + #chunks), no payload copy). Spawning a parse branch is
  the same operation as today's `spawnThread`. Mutation of a chunk with
  `refcount > 1` copies it first (copy-on-write); unique chunks mutate in
  place.
- Ownership: the grammar arena is owned by the refcounted `GrammarHandle`
  (ADR-0004) and outlives every state. A session owns its ping-pong states
  and their chunk refcounts. A cache entry retains its own reference to
  every chunk of the snapshot state; eviction drops only the cache
  references (live sessions survive, ADR-0004 Decision 5 pattern). A chunk
  is freed exactly when its last reference drops.
- Accounting of shared chunks: the allocating owner is charged at
  allocation; an owner that retains a foreign chunk applies a retention
  charge of the full chunk size to its own budget (session chunks ->
  `SessionAccount`, cache snapshots -> the cache `Limited`). Budgets stay
  hard upper bounds on what an owner would pay if forced to materialize
  every shared chunk; the charge is released when the reference drops.
- Equality and canonical form: after each byte step the threads of a v2
  state are sorted by spine bytes and exact duplicates removed (64 x 1284 B
  bounded), so identical configurations reached via different spawn orders
  have identical spine images. States are equal iff the canonical spine
  bytes are equal and the referenced chunks are equal by content
  (kind + len + payload). Handles are never compared; pointer identity
  proves nothing. Two independently built sessions in the same
  configuration must compare equal - cross-session cache hits depend on
  it. (v2 only; canonical-v1 thread order is untouched.)
- Cache storage: `cache.zig` stores deep snapshots today
  (`st.* = state.*`); with COW chunks the snapshot retains chunks instead
  of copying payloads. `eqlFn` compares content as above.

## Decision 3: semantic keys, collision checking, lifetimes, accounting, cancellation

- Semantic key: `serialize(state)` = canonical spine bytes + for each
  referenced chunk, in handle order, `kind u8 | len u32 | payload`.
  The Wyhash of the serialization is only a map index; a hit is confirmed
  by a full byte comparison of the serialization (the existing
  `cache.zig:108` `eqlFn` guard and the ADR-0004 byte-compare rule are the
  pattern). A collision never returns a foreign mask; the cache test
  "same key with different state is a miss" is the model.
- Grammar/artifact identity: the in-process mask cache keeps the dual
  64-bit grammar hashes (`cache.zig:6-9`, accepted residual ~2^-128). For
  anything that crosses call or process boundaries - the compile-artifact
  cache - hash-only keys are forbidden: the v2 artifact key is
  `kind + profile + dialect id + registry snapshot id (Decision 5) + exact
  schema bytes`, with retained bytes compared on hit (ADR-0004).
- Data lifetimes: schema bytes are copied into the artifact (ADR-0004);
  the grammar arena dies at the last handle release; state chunks die with
  the last owner reference; serialization scratch buffers are `temp`
  category and die with the call. No chunk may reference session-local
  scratch: payloads are self-contained.
- Memory accounting: every chunk allocation goes through
  `SessionAccount` (category `session`, hard per-session limit) or the
  cache `Limited` (category `cache`, hard budget); totals through
  `Accounting` (FR-10). COW materialization charges before copying; on
  failure the mutation does not happen and the call returns
  `RESOURCE_LIMIT` - never a silent drop (`docs/supported_features.md`
  section 6 contract). Session and cache peaks stay observable through the
  existing stats.
- Cancellation: the cancel flag is checked at the entry of every public
  call including cache hits (existing `charge(0)` contract), and inside
  serialization/equality loops over chunks so that a large state cannot
  postpone `CANCELLED`.
- Errors: state transitions are transactional - a new chunk is fully
  built, then swapped in; an error mid-copy or mid-mutation leaves the
  pre-call state intact; `errdefer` frees partial constructions. Every new
  allocation path gets a `FailingAllocator` leak test (the
  `cache.zig` "no leak under injected allocation failure" pattern) and a
  "used returns to zero" accounting test (the `alloc.zig` pattern).

## Decision 4: transport for `unevaluatedProperties`/`unevaluatedItems` (P6)

- Grammar object/array/combinator nodes reserve an `ann_slots: u16` field
  now (always 0 in P1-P3); every object/array frame carries an
  `ann: u32` chunk handle (0 = none). The chunk payload is an evaluation
  record: a bitset of evaluated properties (by compile-time property-name
  table index) or evaluated elements (by index), plus the applicator set
  that produced them. Records are chunks of the Decision-2 store, so
  copying, equality, keys and accounting already cover them.
- Write rule: when an applicator (`properties`, `patternProperties`,
  `additionalProperties`, `items`/`prefixItems`, `contains`, and the P3
  combinators/`if`/`then`/`else`) consumes a property or element, it sets
  the corresponding bit through the applicator's slot.
- Merge rule across parse branches: two threads may merge only when their
  canonical spines are equal; their records merge by bitwise union.
  Threads with unequal records stay separate. This fixes now how
  `allOf`/`anyOf` branches transport evaluations without reworking the IR
  in P6.
- Cost when unused: one zero u32 per frame; P1 behavior and masks are
  unaffected.

## Decision 5: external `$ref` registry

- A registry is an immutable, content-addressed snapshot:
  `snapshot id = 128-bit dual hash (FNV-1a + Wyhash, the
  `grammar.Identity` pattern) over the sorted concatenation of
  (uri, document bytes) pairs`, plus a human-readable version string.
  A snapshot never mutates; any change is a new snapshot id.
- The snapshot id participates in the artifact key together with profile
  and dialect (Decision 3). The snapshot bytes are retained by the
  artifact record and compared byte-wise on cache hit (same rule as the
  schema bytes, ADR-0004); the retained bytes are charged to the artifact
  quarter of the cache budget.
- Sessions pin the snapshot through the grammar-handle refcount: swapping
  the registry never affects live sessions or cached masks (mask-cache
  keys already carry the grammar identity, and the grammar identity of a
  v2 artifact covers the registry-resolved bytes).
- Resolution semantics (URI canonization, scopes, `$id` bases) is P5; this
  ADR fixes only identity, key participation, retention and lifetime.

## Decision 6: value equality for `const`/`enum`/`uniqueItems`

Equality is JSON Schema core section 4.2.2 instance equality, made total
and exact (validated by the prototype, see below):

- different tags are never equal: `true` differs from `1`, `null` from `0`;
- numbers compare by mathematical value via a canonical decimal form
  `(sign, digits, exp10)` with leading/trailing zeros stripped; exact at
  any precision, never binary64-only (A6, P4 oracle rule). Hence
  `1.0 = 1 = 1e0 = 10e-1`, `-0 = 0`, and `9007199254740993` differs from
  `9007199254740992.0` (binary64 would collapse them). `1.0` satisfies
  `type: integer` (canonical exponent >= 0 after stripping);
- strings compare by bytes of the unescaped UTF-8 (`src/json.zig` already
  decodes escapes, so byte equality is codepoint equality);
- arrays: equal length plus elementwise equality, order-sensitive;
- objects: equal pair count plus a bijection on equal keys with equal
  values - key order is irrelevant. Keys are unique within one value:
  `src/json.zig` already rejects duplicate keys as `InvalidSchema`
  (verified by its "reject duplicate object keys" test), so the bijection
  is a set match, not a multiset problem;
- candidate hash: numbers are hashed in canonical form, objects by a
  commutative combine of pair hashes - equal values always hash equal
  (required for bucketing). The hash only finds candidates; the decision
  is always exact structural equality. `const`/`enum`: compare against
  hash-selected candidates. `uniqueItems`: bucket element hashes, exact
  compare within a bucket; `[1, 1.0]` is a duplicate.
- Cost: number canonicalization is O(text length) in a scratch buffer;
  object equality is O(n^2) in pair count, bounded by a per-value size
  budget with `RESOURCE_LIMIT` beyond it (a sorted-key index can lower it
  where profitable; the semantics do not change).

The design was validated as a throwaway prototype before implementation
(a standalone spike file importing copies of the real `src/json.zig` and
`src/parser.zig`, deliberately not kept in the tree): 27/27 checks passed
with zig 0.15.2, including all ROADMAP 4.2(6) cases above and the
`1.0`-is-integer classification, and it measured the
`Frame`/`Thread`/`State` sizes cited in the context. The shipped
implementation of these equality rules lives in `src/parser.zig`
(seen-values canonicalization for `uniqueItems`/`contains`) and
`src/grammar.zig` (runtime structural equality of grammar nodes), with
the exact-compare tests in `src/parser.zig`.

## Decision 7: requirement for P5 recursion - the state model allows either

The P5 spike chooses pushdown states or bounded unrolling (own ADR); this
state model must not predetermine the choice:

- bounded unrolling lowers recursion like AnyJSON (Decision 1): D inlined
  copies of the referenced node, only existing frames - supported today;
- pushdown needs a return-continuation stack: it is a new chunk kind of
  the Decision-2 side store (frames carry only a handle), so copying,
  ownership, semantic keys, equality and accounting come from this ADR
  unchanged.

Requirements on P5 whichever is chosen: the recursion state participates
in semantic keys and equality like any other chunk; mask/accept
termination does not rely on unbounded stacks (pushdown cycles need a
visited-configuration memo charged to the session budget, exhaustion is
`RESOURCE_LIMIT`); the depth/budget accounting counts both models so the
accepted language differs only by the documented limit; the limit is an
explicit deviation-list entry. The state layout, keying and ownership
machinery of this ADR is not redesigned by P5.

## Consequences

- P1: AnyJSON lowering and `true`/`false` per Decision 1; the v2 state
  (COW side store, canonical thread order, content equality) behind the
  `spec-v1` profile; `ann` handle fields reserved per Decision 4; the
  value-equality module per Decision 6 (structural `enum`/`const`).
- P2: seen-keys/seen-values/counters land as new chunk kinds without
  touching the state skeleton; `uniqueItems` = candidate hash + exact
  equality.
- P3: thread merge with record union per Decision 4; oneOf/allOf branches
  transport evaluations through the same channel.
- P5: recursion realization is free within Decision 7; the registry
  snapshot joins the artifact key per Decision 5.
- P6: `unevaluated*` consume the reserved transport; no IR or state-layout
  rework.
- canonical-v1: no change to state layout, copying, keys, thread order or
  limits; its tests and the v3 protocol remain the regression gate.
- Cost accepted: one spine memcpy plus chunk retains per accepted token
  (today: one spine memcpy); side-store memory bounded by the session
  limit and cache budget with the existing refusal semantics.
