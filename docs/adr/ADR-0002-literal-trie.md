# ADR-0002: Shared prefix trie for literal alternatives (lit_trie)

- Status: **accepted**
- Date: 2026-09-17
- Context: performance requirements §10.6 (accept p95/p99, cold
  compilation), ADR-0001 (the parser remains an NFA simulation with a bounded
  thread set)

## Context

Before this decision, enum/const (DESIGN item 1.6) and FR-3 literal sets
compiled into a `choice` of `literal` nodes: at the choice point one
thread was spawned per alternative. For an enum of 64 values with a common
prefix (the `big_enum_64` scenario), the state carried up to 64 threads, and
accepting a token hitting the common prefix cost O(length × alternatives): the
same byte comparisons were repeated across 64 threads. On the §10.6 control run
this gave accept p95/p99 = 2.01/2.07 µs versus 0.83/0.92 µs for the best
competitor (+143%/+126% against a +10% threshold) - a formal NO-GO by the
additional conditions.

## Decision

Literal sets compile into a **shared prefix trie** (`grammar.Node.lit_trie`):
flat arrays of nodes and edges (a node's edges are sorted by byte), plus an
enumeration of the original literals for the coverage check. The parser carries
one frame {gnode, node} for the whole set - the common prefix is advanced by a
single thread instead of a thread per alternative. The language is preserved
bit-for-bit via the alternative-completion rules:

- **terminal node with children** - the byte completes the short alternative:
  a completing copy is forked (pop + afterChild), the original keeps matching
  longer alternatives (the byte is consumed by the literal in both);
- **terminal leaf** - completion in place (pop + afterChild);
- **terminal node, byte not found** - pop + reprocess the byte
  (the same trick as for a complete number);
- **terminal root** (the set contains the empty string) - on push_node a
  completing copy is forked.

Applicability: a `choice` whose alternatives are all `literal` (enum/const,
boolean, FR-3); otherwise the previous `choice` remains. A single alternative -
just a `literal` node, as before.

Incidentally, by the same change: the coverage-check DP (FR-6) enumerates
candidate tokens by walking the vocabulary trie (binary search over sorted
edges) instead of building a vocabulary index by first bytes on **every**
compilation (for the GPT-2 vocabulary that was ~815 µs of ~1 ms compilation).

## Rationale and consequences

- accept on an enum window: 2.0 µs → 0.27 µs (a per-token breakdown for `":"`,
  `sym`, `_`, `42` was not measured); the accept distribution tail no longer depends on the number
  of alternatives; cold compilation of big_enum_64: 1.26 ms → ≈0.15 ms.
- Cold masks are cheaper: the state holds fewer threads (hashState/eqlStates and
  the vocabulary trie walk, one byte per edge, run on smaller states).
- State/Thread remain POD and copy bitwise; hashState/eqlStates do not change
  conceptually (the `lit_trie` frame is 8 bytes, like `literal`).
- Thread order in the state may differ from before (forks at completion) - the
  language and masks do not change; verified by a dedicated "choice ↔ lit_trie"
  mask-equivalence test on random vocabularies and by the full parity set
  (296 Python tests on two backends, 106 Zig tests Debug/ReleaseSafe).
- `max_threads`: the thread peak is now ≤ the number of simultaneously completing
  alternatives, not the number of alternatives; ResourceLimit behavior becomes
  softer.
- ADR-0001 remains in force: this is a representation refinement inside
  `push_node`, not a parser change; canonical-v1 semantics (docs/semantics.md)
  do not change.

## Considered alternatives

| Alternative | Why rejected |
|---|---|
| **Lock-step thread merging without coalescing** | Saves only a constant factor (one byte pass for all threads); cost stays O(threads × bytes) - the accept p95/p99 threshold is not met; plus the ResourceLimit semantics change (capacity checked per byte instead of per token end). |
| **Full determinization/precomputation (DFA)** | See ADR-0001: combinatorial table growth, cold start, memory. |
| **Merging/sorting alternatives during parsing** | Extra work on every step versus a one-time compilation. |
| **Specialized literal comparison (fast path)** | Does not remove the O(alternatives) factor - only the constant; code branches on grammar shape. |

## Revisit criteria

1. Appearance of sets with many alternatives differing only in tails (the peak
   of simultaneously completing forks approaches |set|) - then it is useful to
   merge identical completion paths.
2. Language extension beyond canonical-v1 (regex/CFG, spec §4) - revisited
   within ADR-0001, not this decision.
