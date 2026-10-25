# ADR-0007: mask fast path conditions and the differential test plan

Status: accepted (proposed 2026-09-20 as PA deliverable 3, ROADMAP sections
4.3 / A5 / 6; adopted with the PA phase). Implemented and accepted on
measurements, see the "Perf" row of the ROADMAP section 9 tracking table and
docs/RUN_HISTORY.md. That acceptance names the fix2 and fix3-final builds.
The current re-verification is separate: the Decision 4 evidence of the
`a87e2f71` artifact (2026-10-24) covers the full functional matrix and the
stored warm acceptance, its CI confirmation and the formal closure decision
are still open, and the current tree has no Decision 4 evidence of its own.
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
  (inside an open string, not mid-escape, not mid-codepoint) **and the
  enclosure is content-independent**: no armed byte tap (`tap_count == 0`)
  and no combinator frame below the top `.str` frame whose verdict can
  still depend on the content. The one provable exception is a `oneOf`
  with exactly one live branch: a branch whose threads all died can never
  accept the value (threads never revive), so the group verdict is that
  branch's acceptance and the content decides only the branch's own
  string language. The branch set is counted over distinct
  `(inst, branch)` pairs across the live threads, not over threads:
  alternatives inside one branch and nested activations are not separate
  branches. `allOf`, `if/then/else` and a `oneOf` with another live
  branch are conjunctions of votes on the completed value, and the armed
  captures (`uniqueItems`, `contains`, schema-kind dependencies) re-parse
  or compare the captured bytes at the element or object close; there
  `max(R)` is not the intersection bound (`allOf[maxLength 1,
  maxLength 2]` would admit a two-codepoint token) and the content
  decides the parent verdict (in `["a","` with `maxLength: 1` the next
  `a` closes a duplicate element). Those stay in the exact class until a
  separate lemma covers them. The condition is checked once per state
  (`contentDependentEnclosure`).

  Equivalence lemma: in a uniform string-content state S, a pure-content
  token t is allowed iff `cp(t) <= R(S)`, where `cp(t)` is the code-point
  count of t and `R(S) = max_len - count` of the top str frame, with the
  maximum taken over ALTERNATIVE threads only (a lazy `.choice` union;
  every thread has to accept for itself) and never over combinator
  branches. The resulting state is again a uniform string-content state,
  identical to S except `count' = count + cp(t)`. Proof sketch: by
  induction over the code points of t, `strFeed` in `.normal` with
  `rem == 0` on a byte that is not `"`, `\`, < 0x20 either starts or
  continues a code point, never leaves `.normal`, and touches the state
  only through `strIncCount`, which fails exactly when
  `count == max_len`; frames below the top are not consulted by the feed,
  and their verdicts at the value boundary do not depend on the content
  by the scope condition. The induction over steps (token after token)
  holds because the resulting state is again uniform.

  Testable consequences, all decidable in O(live threads) per state and
  O(1) per token after precomputation: (i) the uniform-state check,
  which includes the content-independent-enclosure scope check;
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
  - enclosure scope: `uniqueItems`/`contains` element captures and
    schema-kind dependency captures, and every combinator kind except a
    `oneOf` with one live branch, must keep the exact path. The two
    mandatory reproductions: an array of two unique `maxLength: 1`
    strings at `["a","` must refuse `a`, and `allOf[maxLength 1,
    maxLength 2]` right after the opening quote must refuse a
    two-codepoint token; a `oneOf` of two closed objects is accelerated
    once the key dispatch leaves one live branch, while the same schema
    with both branches live, an inner `oneOf` with two live branches and
    an `allOf` branch must stay exact; nested captures, `oneOf` over
    overlapping length bounds, and length/Unicode edge cases around them
    complete the table;
  - length limits: `minLength`/`maxLength` edges (count 0, min-1, min,
    max-1, max), including the case where the closing `"` is refused at
    `count < min_len` while content bytes are still accepted;
  - Unicode: multi-byte code-point tokens, tokens ending mid-codepoint
    (must be refused everywhere), overlong/surrogate/>U+10FFFF byte
    traps, `\uXXXX` escapes split across token boundaries, combining
    sequences.
- **`tests/fuzz/test_fuzz_differential.py` (extended)**: property-based
  kernel-vs-reference campaign driven by the existing schema generator,
  seeded and deterministic, with the failure artifact protocol of the
  fuzz suite. Each generated schema and walk seed is replayed in four
  configurations - cache off/on (lazy/adaptive) x fast path on/off - and
  each configuration publishes its own counters and a digest of the
  executed trace; identical counters and digests across configurations
  are required, so the same schemas and the same trace sequence are
  compared in every configuration.
- **Benchmark gate**: on the pinned MaskBench slices, the recorded run
  includes a fast-path on/off mask-equality pass before any timing number
  is accepted (same role as the cache on/off equality check). The pass
  compares **every visited state**, not a sample: each state is keyed by
  `(file, instance, step)` and carries the full mask, the fill status and
  the accept return code, and each instance carries its expected validity
  and observed termination (`exhausted`, `fill:<status>`,
  `accept:<code>`). A missing/extra state, a different mask, status or
  accept code, a fill error or a validation mismatch fails the gate and
  writes the complete comparison (including the full word lists of the
  divergent masks) to the artifacts directory. The count and a SHA256 of
  the whole trace are published in `gate.maskbench_states` and
  `gate.maskbench_trace_sha256`.

  Input consumption is not document acceptance. After the input tokens the
  gate records the post-input (EOS/termination) mask, `can_end` and
  `finish`, and compares them between the arms; these terminal checks are
  counted separately from the timed input states. A document is accepted
  only when every input token is accepted **and** completion succeeds
  (`can_end == true`, `finish == BLG_OK`). `can_end == false` with
  `finish == BLG_ERR_WRONG_STATE` is a correct semantic rejection; a
  resource or other finish error, a failed `can_end`, or a failed tail fill
  is a completion error and fails the gate. Completion errors are never
  folded into expected invalidity: only a semantic rejection/completion
  outcome is compared with the test's `valid` flag. The two questions are
  accounted separately: an exception from `can_end`/`finish`, a finish
  code other than `BLG_OK`/`BLG_ERR_WRONG_STATE`, or a failed tail fill
  increments `completion_errors` even when the document was already
  rejected at the input stage, so equal broken arms cannot pass by
  sharing the fault. A raised exception is recorded by its type and, when
  present, its numeric `status`; an exception without `status`
  (`RuntimeError`, `MemoryError`) is still a fault, never `None`.

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

### Warm acceptance protocol (fixed 2026-10-23)

The warm guard needs an aggregation rule fixed before the acceptance
measurement, otherwise a lucky repeat can stand in for a pass. The rule:

1. **Five repeats** of the C-extension warm probe per probe definition -
   ten `warm_ext.on` measurements in total (two probes x five);
2. **2 000 cache hits per repeat**, with 100 % hits mandatory
   (`observations == cache_hits == fill_wall_ns.count == 2000`); a leg
   that holds fewer observations than the protocol declares fails even
   when every one of them was a cache hit;
3. **Alternating leg order** across repeats: round 1 runs off then on,
   round 2 on then off, and so on, so neither leg always runs on the same
   side of the machine's warm-up; a round or leg missing from the report
   is a failure, not a shorter sample;
4. **Every one of the ten means** must be finite, nonnegative and
   <= 1.3 us - the mean of each repeat, not the best repeat and not an
   average of averages; a `NaN` or negative mean fails acceptance;
5. **Both library hashes are mandatory**: each child reports the SHA256
   of the file it actually mapped from `/proc/self/maps` before and after
   the timed loop, and the run is void if either one is missing or
   differs from the artifact under test;
6. **p50 is published separately** and never replaces the mean in the
   verdict;
7. **No discards and no extra repeats**: every repeat is reported,
   failures included, and a failing run is not re-rolled before a passing
   result - a pass must come from the protocol as fixed here.
8. **The verdict is an exit status and a JSON field**: a failed protocol
   sets `status=FAIL`, writes `warm_acceptance.pass=false` and exits
   non-zero; `matrix_adr0007.py` re-reads that field from the written
   JSON and fails the row, so a swallowed exit code or a stale report
   cannot turn it green.
9. **The measurement conditions are fixed and recorded before the run**:
   every child is pinned to one CPU (`--warm-cpu` or `BLG_WARM_CPU`; an
   acceptance without a fixed placement is refused), the CPU model,
   scaling governor and current/max frequency of the measured CPUs are
   recorded, and each leg samples the load average, `procs_running` and
   `/proc/pressure/cpu` immediately before and after the timed loop. Both
   snapshots are mandatory: a missing or non-finite `loadavg` or
   `cpu_pressure_some.avg10`, a missing extension version or a missing
   governor for the pinned CPU fails acceptance, and both the start and
   end affinity must equal the declared CPU, as must the independently
   observed `cpu_now`. The governor must not change between the two
   snapshots, and when an expected policy is declared (`--warm-governor`)
   both snapshots must match it. The load and pressure must be
   at or below `--warm-max-load1` and `--warm-max-cpu-pressure`. The mapped
   library is hashed before and after the loop (a changed hash fails the
   leg), and `cpu_now` is recorded from `os.sched_getcpu` or, where that is
   unavailable, from `/proc/self/stat`. A saved JSON is re-judged from
   its own recorded conditions; data without conditions
   (`conditions.recorded=false`) can never satisfy the acceptance rows.
10. **The leg matrix is complete**: every probe, every round and both
    legs appear exactly once - two probes x five rounds x two legs = 20.
    A saved JSON can be re-judged offline with
    `bench_adr0007.py --verify-warm-json <path> --lib <artifact>`; the
    expected protocol comes from that command line (repeats,
    observations, threshold, CPU, limits, governor, library hash), not
    from the file, so a JSON that declares a shorter protocol cannot
    accept itself. The saved acceptance of 2026-10-24 was re-judged this
    way and passed every check.

The harness implements this as `bench_adr0007.py --warm-repeats 5` (leg
order alternating automatically) and writes the verdict into
`warm_acceptance` of its JSON output; `matrix_adr0007.py` runs it as one
row of the Decision 4 acceptance matrix with `--warm-cpu` and
`--warm-governor`, and the row re-reads `warm_acceptance.pass`, the
protocol parameters (5 repeats, 2000 observations, threshold 1300 ns,
the fixed CPU), `conditions.ok`, `conditions.complete`, the declared
governor policy and the library SHA256 from the written JSON. The
exploratory
`warm_diag.py` base/candidate pair uses the same fixed-placement rules
but writes no `warm_acceptance` block and judges no threshold: it is a
diagnosis, never an acceptance.

The acceptance measurement of 2026-10-24 fixed its conditions before the
run: CPU 7 pinned, `powersave`, load1 <= 2.0 and CPU pressure
`some.avg10` <= 0.5. The ten `warm_ext.on` means were 1085 to 1267 ns and
the run passed. The stored `2d982b60` FAIL was measured under different
conditions and remains a separate, unchanged record.

The functional rows of Decision 4 were run at full scale on 2026-10-24
(`--scale full --only` the six correctness rows: 328 Zig tests, 13
curated cases, 150 fast-path schemas, 600 differential schemas replayed
in four configurations, 120 cache schemas, and the gate over 564
MaskBench masks and 24 terminals) and passed. The report keeps the
partial-run marker; the seventh row is the stored warm acceptance above,
re-verified offline with the current validator, not a new measurement.
The combined record with hashes and provenance is
`benchmarks/maskbench/t8a/evidence/a87e2f71/full-functional-c770469-2026-10-24/CONCLUSION.md`.
This completes the Decision 4 correctness evidence for `a87e2f71`.

Measured TBM results for `a87e2f71`: the 43-file diagnostic slice
(`diag-2026-10-23/equiv/slice.json`, 684 calls) has p90 0.758 ms, and
the pinned BFCL gate's on-call TBM p95 is 0.907 ms in one run and
0.797 ms in the full functional rerun, so p90 of that measured set is
below 1 ms. The 1.42 ms p90 belongs to `642ba1ae` and is not an
`a87e2f71` result. These figures cover the measured workloads only; they
do not extend to the full T8a corpus or the VE latency tail. The ADR's
go/no-go verdicts still require the Decision 4 suite green in CI.

The matrix's `bench-gate` row runs the benchmark gate without
`--skip-maskbench` at every scale: the pinned MaskBench trace-equality
gate is mandatory, and the row's JSON verdict requires a positive
`gate.maskbench_states`, a positive and complete terminal count, and zero
fill, input, completion and validation errors. Only the separate warm row
keeps the MaskBench skip (its focus is the C-extension timing protocol).
### Verdict freshness (fixed 2026-10-24)

A zero exit code and a readable JSON are not a verdict. The matrix gives
every row a unique run id (`BLG_RUN_ID`), the harness records it in its
output, and `check_verdict` requires the id, the expected `status` and
the row's extra checks (zero error counters, warm protocol values,
`conditions.ok` and the library SHA256) from the file written by that
invocation. A retained JSON from an earlier run therefore cannot
satisfy a row, and a benchmark that prints `{"status": "SKIP"}` and
exits 0 (the `bench_common.skip` convention for a missing dependency) is
explicitly rejected instead of being read as a pass.

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
