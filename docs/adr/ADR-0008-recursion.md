# ADR-0008: P5 recursion - bounded unrolling of recursive `$ref` (spec-v1)

- Status: **accepted** (2026-09-21, ROADMAP revision 2 P5 item 1;
  implements the choice ADR-0006 Decision 7 leaves open).
- Context: P1-P4 compile JSON Schema to a finite grammar tree
  (`src/schema.zig` -> `src/grammar.zig`); the parser is an NFA over fixed
  POD spines (`Thread = frames[64]`, `Frame` <= 32 B pin-test); masks and
  the ADR-0005 completion checks are computed from the compiled grammar.
  Until P5 a `$ref` cycle was refused: `INVALID_SCHEMA` "circular $ref"
  (canonical-v1, unchanged) and the same refusal in spec-v1
  (`src/schema.zig` `compileRefSpec`). The oracle suite pins the gap:
  `ref.json` "root pointer ref" refused, 2026-09-21 baseline.

## The spike: pushdown states vs bounded unrolling

ADR-0006 Decision 7 requires the state model to allow either and sets the
shared requirements: recursion state participates in semantic keys and
equality; mask/accept termination never relies on unbounded stacks; the
limit is an explicit deviation-list entry. The spike evaluated both
against the existing machinery:

**Pushdown states.** A recursive target stays one grammar node; a frame
entering it pushes a return continuation onto a new chunk kind of the
ADR-0006 Decision-2 side store. Cost: a new frame variant (inside the
32 B pin), the new chunk kind with COW/ownership/accounting, semantic-key
and equality extension, mask and completion-reachability handling of
cyclic configurations (visited-configuration memo charged to the session
budget per ADR-0006 D7), cache snapshot/restore of the new chunk kind.
Benefit: recursion depth is limited only by the session memory budget, not
by a compile-time constant. That benefit is mostly illusory here: the
parser spine still caps document nesting at `MAX_DEPTH_CAP = 64` frames,
and the language a pushdown automaton accepts beyond that cap cannot be
fed anyway - the "unlimited" depth is unreachable through the byte
interface.

**Bounded unrolling.** The cycle is lowered at compile time exactly like
AnyJSON (ADR-0006 D1): the recursive target is inlined again on every
cycle re-entry while a budget lasts; at the bottom the position compiles
to the empty-language node (ADR-0006 D1, `str` with `min_len > max_len`).
Only existing node kinds result; parser, mask, cache, completion
reachability and the coverage gates need no new machinery, and the hot
path carries no tax (the state model is untouched, so TBM p90 and warm
hits on non-recursive schemas are bit-identical). The existing
empty-language machinery makes the limit mask-visible for free: an
optional recursive property at the bottom becomes a ban dependency (the
key is rejected at dispatch, `src/schema.zig` `compileObjectSpec`), a
required one empties its arm, a recursive array-rest caps the array -
all ADR-0005-conform, no live prefix dead-ends.

## Decision: bounded unrolling

1. **Budget.** `REF_UNROLL_CAP = 8` (`src/schema.zig`): a `$ref` whose
   target is already on the expansion stack (a cycle, detected by target
   identity on `ref_ptrs`) is expanded again while the per-path counter
   lasts; the counter is decremented at cycle re-entry and restored on
   return, so the budget bounds the recursion *nesting depth along any
   document path*, not the total expansion count. With the initial
   expansion a recursive schema therefore matches at most 9 nested
   occurrences along a path; a document nesting the recursive construct
   deeper is rejected. Per-path budgeting means a wide recursion (a tree
   with several recursive properties) multiplies grammar nodes; the
   existing `MAX_NODES` grammar budget and the compile work budget cap
   that with the usual `RESOURCE_LIMIT` refusal.
2. **Bottom.** Budget exhaustion compiles the position to the
   empty-language node. The accepted language is exactly "documents whose
   recursion nesting along any path stays within the budget"; masks are
   exact for that language because the truncated grammar is finite and
   the limit reuses the empty-language machinery (above). Feeding a token
   that would cross the limit fails `accept` the same way any
   out-of-language byte does.
   *Caveat (2026-09-29):* the "exactly" claim is a
   monotonicity argument and holds where the cycle passes through
   monotone constructs only. Inside `oneOf`/`not`/`if` the bottom
   substitution can widen as well as narrow (a document matched by two
   `oneOf` branches only via the recursive tail may become exactly-one
   once truncation kills one branch). For cycles through those constructs
   the guaranteed property is ADR-0005 R1 exactness for the truncated
   grammar, not document-level equivalence with the untruncated schema
   within the depth budget (docs/semantics-spec-v1.md §9).
3. **Interaction with `max_depth`.** Schema compilation still counts
   depth; inside a recursive unroll (the counter is below the cap),
   exceeding `max_depth` truncates the position to the same empty-language
   bottom instead of refusing the whole schema with `RESOURCE_LIMIT`.
   Outside recursion the depth refusal is unchanged. Rationale: the
   deviation must be a property of the compiled language (ADR-0006 D1
   rejects accept-then-error), and a depth overflow caused purely by
   unrolling is the same event as budget exhaustion.
4. **Unproductive recursion.** A `$ref` cycle with no base case
   (`{"$ref":"#"}`, `$defs` a<->b with pure ref chains, a *required*
   recursive property) bottoms out at the empty language on every path;
   the whole schema is then the empty-language outcome bucket (ROADMAP
   section 1): `UNSATISFIABLE_CONSTRAINT` at compile, recognized, never
   compiled. This preserves the old "circular $ref" refusal class for
   exactly the schemas whose language is empty, and upgrades every
   productive cycle to support.
5. **Scope.** All five dialects (the cycle rule lived in the shared
   spec-v1 resolver). `$recursiveRef`/`$recursiveAnchor` (2019-09) and
   `$dynamicRef`/`$dynamicAnchor` (2020-12) stay refused per the dialect
   matrix (P6): their dynamic-scope semantics is not expressible by static
   unrolling. *(Superseded by ADR-0010, P6b: dynamic references resolve
   over the compile-time expansion path, sharing this unroll budget.)*
   canonical-v1 keeps refusing cycles bit-for-bit. External
   `$ref` resolution is the registry work item (below); recursion through
   registry documents uses the same budget.

## External `$ref`: immutable registry snapshot (implemented, ADR-0006 D5)

Fixed here per ROADMAP 4.2 item 5; implemented in this change set.

- A registry is an immutable snapshot: a set of (URI, document bytes)
   pairs plus a snapshot id = 128-bit dual hash (FNV-1a + Wyhash, the
   `grammar.Identity` pattern) over the sorted concatenation of the pairs,
   and a human-readable version string. Snapshots never mutate; any
   change is a new snapshot id.
- The C ABI gains an optional registry parameter on compile
  (`blg_compile_request` extension / a registry handle); every existing
  call pattern keeps working with no registry (tests/blg_ctypes.py,
  benchmarks/maskbench/blg_engine.py unchanged).
- Resolution (spec-v1 only): a `$ref` not starting with `#` is split into
  (base URI, fragment); the base must match a registry document URI
  exactly (no network, no fetch); the fragment resolves inside that
  document with the same rules as local refs (pointer, anchor,
  percent-decoding, recursion budget). Unknown base ->
  `UNSUPPORTED_FEATURE` with the pointer. Relative references and
  `$id`-induced base changes across documents stay refused until a
  dedicated decision.
- The snapshot id participates in the artifact key together with profile
  and dialect (`src/cache.zig`), and the referenced document bytes are
  hashed into the grammar identity, so mask-cache entries and compile
  artifacts are invalidated by a registry swap; live sessions pin the old
  snapshot through the grammar-handle refcount (ADR-0006 D5).

## Consequences

- spec-v1: recursive local `$ref` compiles (trees, linked lists, mutual
  recursion, recursion through anchors and combinators); the limit and
  its exact boundary are documented in docs/semantics-spec-v1.md and
  docs/supported_features.md; dialect-matrix section 3 drops the
  "cycles refused until P5" line.
- Oracle: ref.json "root pointer ref" rows move from SKIPPED to PASS and
  refRemote.json rows resolve through the suite remotes registry; no
  MISMATCH/ENGINE_ERROR movement elsewhere.
- Tests: tests/test_spec_v1_p5.py (accept/reject at/below/beyond the
  limit, mask-visible boundary, unproductive cycles, percent-decoding),
  tests/test_spec_v1_p5_reachability.py (ADR-0005 D6 contract on
  recursive schemas), zig unit tests in src/schema.zig.
- No changes to parser/mask/cache/state layout; the `Frame` 32 B pin and
  the canonical-v1 profile are untouched; perf gates (ADR-0007) are
  re-measured as acceptance.
- URI-fragment percent-decoding (RFC 6901 section 6) is part of the same
  resolver fix: `%XX` decodes before `~0`/`~1` unescaping, for pointer
  segments and plain-name fragments.
