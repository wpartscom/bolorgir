# architecture.md - Bolorgir architecture

Describes the modules, their boundaries and the data/memory/threading
models (SPEC.md §6). The public interface is documented in docs/API.md and
declared in include/bolorgir.h; language semantics are in docs/semantics.md
(canonical-v1) and docs/semantics-spec-v1.md (spec-v1); implementation
invariants for contributors are in docs/DESIGN.md.

## 1. Overview (spec §6)

~~~mermaid
flowchart TD
    A[Schema and profile] --> C[Constraint compiler]
    B[Tokenizer adapter] --> T[Common token representation]
    C --> G[Immutable grammar]
    G --> S[Session state]
    T --> M[Mask builder]
    S --> M
    K[Bounded cache] <--> M
    M --> P[Mask transfer and application]
    F[GPU model forward] --> P
    P --> Q[Sampling on the model device]
    Q --> U[Token ID acceptance]
    U --> S
~~~

Blocks C, G, S, T, M, K - the core (CPU). Blocks F, Q - model and sampling (the
model device). Block P - integration adapter (Python/Transformers), outside the
core.

## 2. Core modules

| Module | File | Responsibility |
|---|---|---|
| Schema JSON parser | `src/json.zig` | Parses input JSON into a value tree; rejects duplicate keys (INVALID_SCHEMA); schema size limit. |
| Schema compiler | `src/schema.zig` | Profile dispatch. canonical-v1: FR-1 profile check (supported_features.md §1 table), `$defs`/`$ref` expansion with acyclicity control, enum/const normalization (exact decimal, semantics.md §6). spec-v1: dialect normalization (dialect-matrix.md), extended reference resolution with bounded recursion (ADR-0008), combinators, value filters (supported_features.md §1a). IR construction. Schema errors - before the first forward pass. |
| Regex subset | `src/pattern.zig` | spec-v1 `pattern` and regex `patternProperties`: an ECMA-262 subset compiled to a DFA over Unicode scalar values. |
| FR-3 compiler | `src/literals.zig` | Literal string list → shared prefix trie (`lit_trie`); deduplication; empty string - an instantly completed literal; a single alternative - a plain literal. |
| Grammar IR | `src/grammar.zig` | Immutable nodes literal / lit_trie / str / int_v / num_v / choice / seq / repeat / object; arena inside Grammar; identity - two independent 64-bit hashes of the raw schema bytes + kind (FNV-1a `id` + Wyhash `id_hi`). Repetitions - counters, no maxItems expansion. `lit_trie` - shared prefix trie of literal alternatives (enum/const, FR-3): the shared prefix advances through a single parser thread instead of one thread per alternative (ADR-0002). |
| Parser | `src/parser.zig` | Incremental parsing: `State` - a bounded set of threads (NFA simulation, ADR-0001); each thread - a stack of POD frames with capacity max_depth. `feedBytes` byte by byte; a thread error - dropped; exceeding max_threads - RESOURCE_LIMIT. `canEnd`, `hashState`, `eqlStates`. |
| Mask builder | `src/mask.zig` | Zeroes out; EOS bits from canEnd; regular tokens - iterative DFS over the vocabulary trie (edge = byte into feedBytes, Parse prunes the subtree, a terminal sets the id-chain bits); special non-EOS tokens are skipped; empty mask - DeadEnd. A fast path (ADR-0007) classifies tokens of uniform states such as string content without walking each one; masks are bit-identical with it on or off. Traversal steps are charged against the work/cancellation budget (work.zig). A repeat call on the same state - bitwise identical result. |
| Completion reachability | `src/complete.zig`, `src/witset.zig`, `src/synth.zig` | Filters tokens after which no completed document is reachable (ADR-0003, ADR-0005): closed-form certificates first, then a bounded search; an unsettled state is a RESOURCE_LIMIT error, never a silently admitted token. |
| Tokenizer | `src/tokenizer.zig` | Table copied into context ownership; validation of id coverage, eos/special; identity = FNV-1a-64 (adapter tag, token bytes, eos, special, vocab_size); flat vocabulary prefix tree (trie) for mask traversal; matching byte images - an id chain. |
| Tokenizer coverage | `src/coverage.zig` | Check on `blg_compile`: literals are segmented into vocabulary tokens (DP over bytes; candidates enumerated by walking the vocabulary trie - no O(vocab) index is built); open classes and structural syntax have a producible completion; otherwise - UNSUPPORTED_TOKENIZER. Conservative (token boundaries = node boundaries); a byte-complete vocabulary always passes. |
| Work budget and cancellation | `src/work.zig` | Per-call `Work`: an op counter against `work_limit_ops` (0 = no limit) + reading the caller's cancel flag (acquire); Cancelled/ResourceLimit from the compile/mask/coverage/precompute loops. |
| Precompute prewarm | `src/precompute.zig` | Experimental BFS over reachable states at `blg_compile` computing masks into the cache, up to `precompute_max_states`; budget exhaustion - silent fallback to lazy; only Cancelled is surfaced. |
| Accounting allocator | `src/alloc.zig` | `Accounting` over std.mem.Allocator: used/peak per category {tokenizer, grammar, session, cache, temp} + total; total limit → allocation refused → RESOURCE_LIMIT; `SessionAccount` with a session limit. After context destroy used == 0 (checked by tests). |
| Statistics | `src/stats.zig` | compile/tokenizer_prepare/accept/mask timers (ns), cache counters, memory from Accounting, tokens_accepted, per-class error counters, cache_adaptive_skips, precompute_states, work_ops_total - fields mirror `blg_stats`. |
| LRU cache | `src/cache.zig` | Key {grammar_id, grammar_hi, tokenizer_id, state_hash} (128-bit grammar identity); value - a state copy (collision check via eqlStates) + a mask copy. Hard byte budget via a limiting allocator (including Entry and HashMap); LRU-tail eviction; budget==0 - disabled (lazy-equivalent). Admission (adaptive): put when seen >= adaptive_min_hits OR compute_ns >= adaptive_min_cost_ns. Never alters mask contents. |
| C ABI | `src/c_api.zig`, `src/root.zig` | Exports `blg_*` functions; ownership: context > grammars (refcount) > sessions; external references (handles, sessions, calls in flight) hold destroy at BUSY; `blg_context_reset_cache` releases the artifact cache without destroying live objects; Zig → blg_status error mapping; unexpected errors - INTERNAL + aborted session. |

### Python bridge and adapter (outside the core)

- `python/` - CPython C extension `bolorgir._core` on the Limited API
  (abi3), links against libbolorgir.so (rpath $ORIGIN). The GIL is
  released during native calls; a single session serializes its operations; ABI
  errors → typed exceptions (one class per blg_status).
- `bolorgir.transformers` (extra; torch is imported only inside) -
  LogitsProcessor + `constrained_generate`: greedy/sampling, fixed batch, left
  padding, final accept of the last tokens, per-row completed/stop_reason
  (completed=True only on an accepted allowed EOS; without EOS - "length").
  Unsupported modes per the effective generation_config (num_beams > 1 etc.) -
  UnsupportedModeError before generation. No global monkey-patching.

## 3. CPU/GPU boundary

- The core receives constraints, tokens and state; returns a mask (uint32
  array, ceil(vocab_size/32) words) into the caller's buffer. The logits matrix
  is never transferred from GPU to CPU for engine operation.
- For a GPU model the mask is transferred to the logits device and applied by
  the adapter (in the MVP - plain PyTorch ops; custom CUDA/Triton kernels are
  not required). Unpacking, transfer, synchronization and application are part
  of the integration measurements (B7).
- For a CPU model the mask is applied on CPU; a CPU mask does not imply
  zero-copy for GPU.
- Constraints apply before top-k/top-p; later operations cannot re-allow a
  forbidden token ID (FR-15).

## 4. Threading model

- MVP - sequential execution: one ABI call is handled on the caller's thread;
  `blg_fill_masks_batch` walks sessions sequentially, each row's status is
  independent.
- No internal thread pool; no new threads per token. A thread pool is a
  post-profiling optimization (the result must not depend on the thread
  count).
- Independent sessions allow concurrent calls from outside; concurrent
  modification of one session is forbidden by contract and blocked by the
  Python wrapper.
- Session states are not shared between threads; shared data (grammar,
  tokenizer table) is immutable after creation.

## 5. Memory model and limits

- All core allocations go through `Accounting` with categories; reserved memory
  is accounted for. The total limit covers compile temporaries and all live
  context objects.
- The grammar is an arena, immutable, shared by sessions via refcount; a schema
  is not copied per session.
- Session state - inline fixed-capacity arrays (threads × frames); the full
  text of an unbounded string is never accumulated; an unfinished lexical
  element and the UTF-8 automaton live within a thread.
- Default limits: core 256 MiB; cache 64 MiB; session 8 MiB; schema 1 MiB;
  depth 64; threads 128 (supported_features.md §6). When the cache fills, work
  continues in lazy if the other limits allow.
- Memory return is checked after destroy: used == 0 across all categories.
- Cross-grammar reuse - only for provably equivalent substructures; a cache
  hash collision is verified by content comparison (eqlStates), not assumed to
  be a match.

## 6. Determinism and observability

- With the same tokenizer, profile, schema and prefix, all modes
  (lazy/adaptive/precompute) produce a bitwise-identical mask; cache eviction
  and session re-creation do not change the language (NFR-1, T2). Precompute
  prewarm only fills the cache in advance and does not change results.
- No RNG in the core.
- `blg_get_stats` / `blg_get_stats_session`: compile/prepare/accept/mask times,
  cache hits/misses/evictions, accounted and peak memory per category. Verbose
  logging is off by default; response and schema texts never enter the logs
  automatically.
