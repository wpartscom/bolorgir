# ADR-0007: mask fast path conditions and the differential test plan

Status: accepted (proposed 2026-09-20 as PA deliverable 3, ROADMAP sections
4.3 / A5 / 6; adopted with the PA phase). Implemented and accepted on
measurements, see the "Perf" row of the ROADMAP section 9 tracking table and
docs/RUN_HISTORY.md.
Context: the measured problem (ROADMAP section 6) - a first visit of a
permissive state (string content) costs ~0.6 s at a 128k-token vocabulary, a
cache hit 1.3 us; the mask DFS in `src/mask.zig` feeds one byte per trie edge
to `parser.feedBytes` (~1.5 us per node, ~400k nodes for the Llama
vocabulary); MaskBench framing TBM p90 is 609 ms against 133 us for
llguidance under a protocol where most masks are first visits. Targets: cold
permissive state <= 50 ms on the fixed slice (stretch <= 10 ms), MaskBench
TBM p90 <= 1 ms, warm path not worse than the 1.3 us baseline.

## Corrected premise: the byte set does not determine the mask

The allowed-next-byte set of a state does NOT determine the full token mask.
Worked example (ROADMAP 4.3): after the opening quote, the states of
`{"type":"string","maxLength":1}` and `{"type":"string","maxLength":2}` accept
exactly the same next bytes (every string-content byte), yet the token `ab`
is allowed only in the second state - the decision needs the residual length
budget, not the byte set. Two further mechanisms break any byte-set shortcut:

- a token can close a construct and move to the parent: `"x"` closes the
  string and the following bytes must match the parent frames (`,` versus `}`
  of the object, a literal `":` etc.), so its legality depends on the whole
  thread stack, not on the top frame's byte set;
- intermediate transitions matter: UTF-8 continuation ranges
  (`src/parser.zig:518` `strFeed` `rem/lo/hi`), `\uXXXX` escape states,
  counters (`repeat`, string `count` against `max_len`) all make per-byte
  parser state decisive mid-token.

Consequence: any optimization that derives the mask from a per-state byte
set, or keys a shared structure on that set, is unsound and prohibited.

## Decision 1: the fast path is per token class, under a stated equivalence lemma

The mask of a state S is split into token classes; each class gets its mask
from the cheapest method whose correctness lemma covers it:

- **Exact class (default).** The plain trie walk of `fillMask`
  (`src/mask.zig:48`): every byte of the token is fed to `feedBytes`. Every
  token not assigned to a proven fast class is processed here. The exact
  class is always a superset-safe fallback: moving a token from a fast class
  back to the exact class is never a semantic change.
- **String-content class (the initial fast class).** A token t is *pure
  string content* when its bytes form complete valid UTF-8 code points (no
  truncated sequence) and contain no `"` (0x22), no `\` (0x5C) and no byte
  < 0x20. This is decidable per token once, at tokenizer load, by a
  standalone validator with the same acceptance as `strFeed` (strict UTF-8:
  no overlongs, no surrogates, <= U+10FFFF) - precomputed flags, not a
  per-state parse.

  State condition: S is a *uniform string-content state* when every live
  thread has a top frame `.str` with `state == .normal` and `rem == 0`
  (inside an open string, not mid-escape, not mid-codepoint).

  Equivalence lemma: in a uniform string-content state S, a pure-content
  token t is allowed iff `cp(t) <= R(S)`, where `cp(t)` is the code-point
  count of t and `R(S) = max_len - count` of the top str frame; the
  resulting state is again a uniform string-content state, identical to S
  except `count' = count + cp(t)`. Proof sketch: by induction over the code
  points of t, `strFeed` in `.normal` with `rem == 0` on a byte that is not
  `"`, `\`, < 0x20 either starts or continues a code point, never leaves
  `.normal`, and touches the state only through `strIncCount`, which fails
  exactly when `count == max_len`; frames below the top are never
  consulted. The induction over steps (token after token) holds because the
  resulting state is again uniform.

  Testable consequences, all decidable in O(live threads) per state and
  O(1) per token after precomputation: (i) the uniform-state check;
  (ii) the per-token class flag; (iii) the mask of the class is
  `content_bits AND len_le[R]` with `len_le` a table of 1 +
  max-cp-length precomputed bitsets; (iv) EOS is excluded because
  `canEnd(S)` is false (`threadCanEnd` returns false on a str top frame);
  (v) the content-class masks of two uniform states are equal whenever
  `min(R1, CP_MAX + 1) == min(R2, CP_MAX + 1)`, `CP_MAX` the largest
  `cp(t)` in the vocabulary - this capped residual is the equivalence-class
  descriptor for caching (Decision 2).

- **Further classes** (number digits in a stable phase, literal-chain
  descent) are admissible only after the same treatment: a stated lemma,
  its proof obligations, and its differential tests. P4 numeric ranges
  (`minimum`/`maximum`/`multipleOf`) change number-frame semantics, so no
  number fast class ships before P4 re-states the lemma. New node kinds of
  spec-v1 start in the exact class.

The fast path is semantics-preserving by construction (an equivalence
optimization, not a profile feature): it applies to canonical-v1 and
spec-v1 alike, guarded by a runtime kill switch (a context config flag;
off = the plain trie walk) that exists at least until the perf track
closes, and by the differential suite of Decision 4 running in CI for both
flag values. canonical-v1 masks stay bit-for-bit identical and its v3
protocol thresholds remain the regression gate.

## Decision 2: cache key rules - no raw byte set as a universal key

- The session mask cache key remains content-verified identity:
  `{grammar_id, grammar_hi, tokenizer_id, state_hash}` plus the
  `eqlStates` byte comparison on collision (`src/cache.zig:10`,
  `src/c_api.zig:628`). The hash is an index; equality decides. This does
  not change.
- A raw allowed-next-byte set is never a cache key, in any structure
  (Decision premise). Two states sharing a byte set can disagree on `ab`
  (the `maxLength` example) and on every closing token.
- Widened (equivalence-class) entries are allowed only for the token
  classes a proven lemma covers, and their key is the lemma's descriptor,
  not the full state: for the string-content class,
  `{grammar_id, grammar_hi, tokenizer_id, R_capped}` where
  `R_capped = min(R, CP_MAX + 1)`. A widened entry stores only its class's
  bits; the exact-class bits of the same state are never stored under a
  widened key. Tokenizer/decoder identity participates per ADR-0003
  (decoder configuration is part of the identity), because the class flags
  and bitsets are computed from decoded token byte images.
- Any future sharing across grammars or tokenizers requires transition
  equivalence proven for the shared fragment (same grammar node table for
  the frames involved, same token byte images), recorded in the key;
  unproven sharing is a bug class, not an optimization.
- Widened entries live in the same hard byte budget and LRU accounting as
  the existing cache; collision checking compares the full descriptor.

## Decision 3: exact-path improvements without semantic risk

The plain walk stays the reference; its constant factors may be improved
freely because the output contract (bit-for-bit mask equality with today's
walk, checked by Decision 4) is unchanged:

1. **Fewer state copies per node.** `feedBytes` (`src/parser.zig:683`)
   copies every live thread twice per byte (into the threadlocal
   `feed_work`, then into `out`), and `MaskBuf` holds one full `State` per
   branching trie level. Admissible: feed per thread in place with a
   small rollback journal (a str/literal frame transition touches O(1)
   frames), copy only the diverging thread on branch spawn, and keep the
   DFS stack as parent deltas instead of full states. A `State` is ~80 KiB
   of POD; live bytes are `n` threads x `len` frames, so the win is
   proportional to trie nodes visited.
2. **Transition tables keyed by (state, byte).** A budgeted memo of
   `(state_hash, byte) -> resulting state` for hot uniform states turns
   chain descent in the trie into table lookups. Entries store the full
   resulting state (or an arena reference), are collision-checked like the
   mask cache, and are eviction-safe: a miss falls back to `feedBytes`.
3. **Bit tricks for token chains.** All per-token classification
   (pure-content flag, cp-length, first-byte sets) is precomputed at
   tokenizer load into word bitsets; the string-content mask is then a
   handful of word ANDs over vocab/32 words (~4k words at 128k) plus the
   `len_le[R]` lookup, and duplicate byte images chained in
   `token_chain` are set by word operations, not per id.

## Decision 4: differential test plan against the plain trie walk

Reference: the existing `fillMask` trie walk with the fast path disabled
(kill switch). Oracle for semantic ground truth stays `tests/reference.py`
and the byte-level brute scan `fillMaskBrute` (`src/mask.zig:147`). All
comparisons are bit-for-bit over the full mask words, including error
outcomes (DeadEnd/ResourceLimit on one side must match the other), and are
repeated with the session cache on and off (the `tests/fuzz/
test_fuzz_cache.py` pattern).

Where the tests live:

- **Zig unit tests, next to the implementation** (`src/mask.zig` and the
  new fast-path module): extend the existing randomized equivalence
  harness ("trie walk equals linear scan") to a three-way comparison -
  fast path, trie walk, brute scan - on random small-alphabet
  vocabularies and literal-set grammars, at several reachable states.
- **`tests/test_mask_fastpath.py` (new)**: curated ABI-level cases,
  fast path on versus off, over the ctypes bridge:
  - long tokens: tokens longer than every string bound in the schema,
    tokens at the maximum vocabulary length, tokens whose code-point
    length equals R, R+1, R-1 at the moment of the query;
  - value boundaries: tokens that close a string and continue into the
    parent (`"x",`, `"x"}`, `"x"],"b":`), tokens ending exactly on a
    literal boundary, EOS at `can_end` transitions, tokens straddling two
    constructs;
  - length limits: `minLength`/`maxLength` edges (count 0, min-1, min,
    max-1, max), including the case where the closing `"` is refused at
    `count < min_len` while content bytes are still accepted;
  - Unicode: multi-byte code-point tokens, tokens ending mid-codepoint
    (must be refused everywhere), overlong/surrogate/>U+10FFFF byte
    traps, `\uXXXX` escapes split across token boundaries, combining
    sequences.
- **`tests/fuzz/test_fuzz_differential.py` (extended)**: property-based
  fast-path on/off equality driven by the existing schema generator,
  seeded and deterministic, with the failure artifact protocol of the
  fuzz suite.
- **Benchmark gate**: on the pinned MaskBench slices, the recorded run
  includes a fast-path on/off mask-equality pass before any timing number
  is accepted (same role as the cache on/off equality check).

Coverage targets of the plan: every fast class has at least one curated
boundary case per proof obligation of its lemma (for string content:
uniform check, cp-count arithmetic, R edge, UTF-8 validity, EOS
exclusion), and the randomized harness must generate states with R in
{0, 1, CP_MAX-1, CP_MAX, CP_MAX+1, unbounded}.

## Decision 5: parallelism rules

- `max_threads_per_state` keeps its current meaning - parse branches
  (NFA threads) inside one state (`src/parser.zig:55` `spawnThread`,
  `docs/supported_features.md` section 6). It is a semantic limit:
  exceeding it is `RESOURCE_LIMIT`, never silent branch dropping. Its
  meaning, default and cap do not change, and it is never repurposed as a
  CPU thread count.
- CPU parallelism (parallel trie subtrees, parallel word ranges of the
  bitset classification) gets its own config field (working name
  `max_workers`; 0/1 = single-threaded, the default until the perf track
  validates it) plus a shared budget: all worker allocations charge the
  existing context `memory_limit` categories and all worker steps charge
  the same `work_limit_ops` counter, so a parallel run cannot exceed the
  resources of the sequential one.
- Determinism is contractual: masks are bit-for-bit independent of
  `max_workers` (workers reduce over disjoint token-id ranges or disjoint
  subtrees; combination is a fixed-order OR of disjoint words). The
  differential suite of Decision 4 runs at `max_workers` in {1, 2, 8}.
- `feedBytes`'s threadlocal scratch (`src/parser.zig:681`) already makes
  worker-side parsing per-thread safe; sessions stay pinned to one worker
  for the sequential accept path.

## Go/no-go conditions for the perf targets

- **Cold permissive state <= 50 ms on the fixed slice (stretch <= 10
  ms).** GO when, on the fixed baseline slice: (i) the pure-content class
  covers >= 95% of vocabulary tokens (Llama/Qwen/GPT-2 BPE: expected -
  quote/backslash tokens are few hundred), so classification costs
  O(vocab/32) word ops, tens of microseconds; (ii) the residual exact
  class descends a trie subtree bounded by quote-bearing prefixes, which
  the transition tables of Decision 3 cap well under the budget; (iii)
  the uniform-state check holds for the slice's permissive states.
  NO-GO (target unreachable by this path alone, escalate back to PA) if
  the measured class distribution shows > 10% exact-class tokens on the
  slice, or if permissive states are frequently non-uniform (mid-escape,
  mid-codepoint at mask time).
- **MaskBench TBM p90 <= 1 ms under the pinned protocol and profile.**
  GO when the p90 state class of the protocol distribution is decided by
  the fast class plus table-backed exact descent within the budget, and
  the warm/cache path absorbs the repeated states (hit latency unchanged).
  The 609 ms -> 1 ms gap is ~600x, so both the bitset classification and
  the Decision 3 copy reductions must land; either one alone is
  insufficient. NO-GO if the protocol's p90 state is structural (most
  tokens exact-class) - then the target requires the precompute mode or a
  revised protocol distribution, which is a separate decision.
- **Warm path not worse than the 1.3 us baseline.** Hard constraint, not
  a stretch goal: the cache lookup stays ahead of any classification, the
  key/hash/equality path is untouched, and the baseline harness must
  measure <= 1.3 us after the change. Any regression is a blocker
  regardless of cold-path wins.
- All three verdicts require the Decision 4 suite green in CI (fast path
  on and off, cache on and off, `max_workers` in {1, 2, 8}) and the
  canonical-v1 v3 protocol unchanged.

## Consequences

- The fast path is an equivalence optimization with a kill switch; no
  profile semantics change, canonical-v1 masks bit-for-bit identical.
- Implementation order inside the perf track: Decision 3 (exact-path
  constants, zero semantic surface) first, then the string-content class
  with its lemma tests, then widened cache entries, then workers.
- Every new node kind (spec-v1 phases P2-P6) starts in the exact class; a
  fast class for it requires an ADR-0007-style lemma and tests, mirroring
  the 4.1 completion-reachability gate for new masks.
- P4 must re-state any number-class lemma after numeric ranges land;
  until then digits are exact-class.
