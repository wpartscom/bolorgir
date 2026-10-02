# ADR-0001: Parser choice - a deterministic stack automaton with a bounded thread set (NFA simulation)

- Status: **accepted**
- Date: 2026-09-14
- Context: spec FR-5 (parser choice is documented in an ADR), docs/DESIGN.md §3, src/parser.zig

## Context

The core needs an incremental parser for the canonical-v1 language: at each
generation step a mask of allowed tokens is built, and state is kept between
steps so parsing can continue without re-reading history. Requirements:

- exactness: a token is allowed ⟺ its bytes are accepted by the language and the prefix can be continued
  to a valid document (no approximate filters);
- predictable step cost (p99 matters more than the mean, spec §10);
- bounded and accounted state memory (FR-7, FR-10);
- determinism: a repeated mask query on the same state - the same result
  bit-for-bit; the result does not depend on the number of threads;
- ease of independent verification with an oracle (T1/T2).

## Decision

Use a **stack automaton with a bounded set of parallel threads** -
a nondeterministic automaton simulation (NFA simulation), where each thread is a
POD stack of frames of fixed capacity (`MAX_DEPTH_CAP = 64`), and state is an
array of threads of capacity `MAX_THREADS_CAP = 64`:

- `State` = { threads[64], n, max_threads }; `Thread` = { frames[64], len }.
  Copying is bitwise, with no per-step allocations.
- Nondeterminism arises only at choice points with common prefixes:
  `choice` (enum/const alternatives, FR-3 literal sets) and object key
  candidates (semantics.md §5). At these points one thread is spawned per
  alternative; each branch diverges from its siblings within a few bytes - as
  soon as the distinguishing byte is read, mismatching branches are dropped.
- The per-byte thread loop - deterministic transitions on the top of the stack
  (literal/str/numbers/repeat/object; lazy number completion; the
  after_child_done cascade). A byte mismatch drops the thread; no live
  threads - Parse.
- Exceeding `max_threads` - a ResourceLimit error, not silent branch
  dropping: language completeness wins (spec §3.2).
- `canEnd` - the existence of a thread completable without bytes (empty stack or
  virtual collapsing of a complete number).

## Rationale

1. **canonical-v1 is almost deterministic.** It is not an arbitrary CFG: the
   profile fixes key order and forbids whitespace and alternative
   serializations. At any document position the set of allowed next bytes is
   usually unambiguous; ambiguity is confined to choice points with common
   prefixes and is exhausted by a finite number of alternatives. A general
   arbitrary-context-free-grammar mechanism is overkill here.
2. **Ambiguity is resolved within a few bytes.** Branches live no longer than
   their prefixes coincide; the number of simultaneously live threads is bounded
   by the number of choice alternatives (enum, key candidates) and does not grow
   with input length. Hence predictable memory: state is O(threads × depth) POD,
   with no per-step heap.
3. **Complexity and maintenance cost.** A per-byte switch over the Frame union is
   a small amount of code, easily checked by an independent oracle (language
   enumeration + prefix oracle) and by fuzzing; no heap in the hot path
   simplifies memory accounting and fault injection.
4. **Predictable latency.** Step cost is proportional to the summed lengths of live
   threads (bounded), with no allocations after warm-up and no unpredictable
   phases (no closure/table building per token).

## Considered alternatives

| Alternative | Why rejected for the MVP |
|---|---|
| **Earley (like llguidance)** | A universal algorithm for arbitrary CFGs: O(n³)/O(n²) worst case in prefix length; incremental use requires storing a chart/sets of states; state between steps is heavier (item sets by position), serialization for the cache and collision checking are harder. For an almost deterministic language - excessive generality and worse p99 predictability. Remains an option to explore if the language grows to CFG. |
| **PDA with full GLR** | A general deterministic pushdown automaton for canonical-v1 does not exist because of choice points; GLR copes via a graph-structured stack, which allocates and branches on every ambiguity - the same NFA simulation, but with heap and garbage collection in the hot path. Loses on memory and simplicity at the same expressive power for our language. |
| **Precomputed DFA (like XGrammar)** | Full determinization/precomputation of masks by state gives minimal warm-step cost, but: the number of states for nested objects/arrays with counters and long strings is combinatorial; cold start and table memory contradict the goals of "small preparation for new schemas" and "controlled memory". The idea is partially carried over by the adaptive mode: an LRU mask cache keyed by state hash - a lazy, budgeted form of precomputation yielding the same result bit-for-bit. A separate precompute mode is implemented on top of adaptive as a bounded BFS mask warm-up at compile (supported_features.md §4; clarified 2026-09-15 - in the original ADR text it was listed as an experimental alias of adaptive). |

## Consequences

- Pros: an exact mask contract with no heuristics; state copies bitwise and
  serializes for the cache; no allocations on a warm step; directly verifiable
  by an oracle.
- Cons: step cost scales with the number of live threads (mitigated by walking
  the vocabulary trie when building the mask - a byte mismatch cuts the whole
  subtree - and by the cache); a language beyond canonical-v1 will require a
  revisit.
- Boundaries: `max_threads` and `max_depth` are public limits; exceeding them is
  a normal error, not a completeness degradation.

## Revisit criteria

The ADR is revisited if:

1. the language extension 1.1 (user CFG/EBNF or regex per spec §4)
   makes the set of simultaneously live branches unbounded by construction -
   then the bounded thread set stops covering the language without
   ResourceLimit, and Earley/GLR or regex-to-DFA compilation will be needed;
2. stage-2 measurements show that p99 mask construction is bounded by the number
   of threads on a real corpus even with a working cache;
3. constructs appear whose ambiguity is not resolvable within a few bytes
   (branches coinciding on arbitrarily long prefixes).

A revisit is documented as a new ADR with measurements of time, memory and
maintenance complexity (spec FR-5); a parser change must not alter canonical-v1
semantics (docs/semantics.md).
