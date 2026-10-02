# Implementation notes

These notes are for contributors. They describe how the engine is built and
the invariants the code relies on. They are not the public interface (see
[API.md](API.md)) and not the language definition (see
[semantics.md](semantics.md) and [semantics-spec-v1.md](semantics-spec-v1.md)).
For a module-level overview start with [architecture.md](architecture.md);
the reasoning behind individual decisions is recorded in the
[ADRs](adr/).

Section numbers are stable: source comments and tests cite them as
"DESIGN §N". If a change breaks an invariant described here, update this
file in the same change.

**Conventions**

- Toolchain: Zig 0.15.2 (the version pinned in CI), Python 3.10+, a C
  compiler for the Python extension. Formatting: `zig fmt`.
- Core sources live in `src/`; the C ABI is declared in
  `include/bolorgir.h`.
- Errors are Zig error sets inside the core and `blg_status` codes at the
  ABI boundary. No panic or error union crosses the ABI (§7, §10).
- The core is deterministic: there is no RNG, and a mask is a function of
  the grammar, the tokenizer and the parse state only. Caches, modes and
  worker counts never change results.

## 0. Source layout

| Path | Responsibility |
|---|---|
| `src/json.zig` | JSON reader for schemas: value tree, duplicate-key rejection, size limit. |
| `src/schema.zig` | JSON Schema to grammar compiler for both profiles: profile dispatch, dialect normalization, `$ref` resolution, value filters, compile-time diagnostics with JSON pointers. |
| `src/literals.zig` | Literal-set (FR-3) compiler. |
| `src/grammar.zig` | Grammar IR (§2). |
| `src/pattern.zig` | ECMA-262 regex subset compiled to a DFA over Unicode scalar values (`pattern`, regex `patternProperties`). |
| `src/parser.zig` | Incremental parser (§3). |
| `src/mask.zig` | Mask builder: vocabulary trie walk and the fast path (§4). |
| `src/complete.zig` | Completion-reachability filter: certificates and bounded search (§4). |
| `src/witset.zig`, `src/synth.zig` | Value-level tools and completion synthesis used by the reachability certificates. |
| `src/tokenizer.zig` | Token table, identity, vocabulary trie (§5). |
| `src/coverage.zig` | Compile-time tokenizer coverage gate (§6). |
| `src/alloc.zig`, `src/stats.zig`, `src/cache.zig`, `src/work.zig`, `src/precompute.zig` | Memory accounting, statistics, mask cache, per-call work budget and cancellation, precompute warm-up (§6). |
| `src/c_api.zig`, `src/root.zig` | C ABI implementation and library root (§7). |
| `src/tests.zig` | Registers the unit tests of all modules. |
| `src/fuzz_main.zig` | Fuzz and fault-injection runner (`zig build fuzz`). |
| `examples/c_client.c` | C client without Python. |
| `python/` | Python package, C extension and adapters (§8). |
| `tests/` | Reference implementation, parity, edge-case, spec-v1, oracle and fuzz tests (§9). |
| `benchmarks/` | Measurement scripts, acceptance protocol and published reports. |

## 1. canonical-v1 semantics

The canonical-v1 language is defined normatively in
[semantics.md](semantics.md), and its schema acceptance rules in
[supported_features.md section 1](supported_features.md#1-json-schema-profile-fr-1-canonical-v1-serialization-profile).
This section is kept so that the subsection numbers cited in code resolve;
each points to the normative text.

| § | Topic | Normative text |
|---|---|---|
| 1.1 | Strings and the escape table | semantics.md §2 |
| 1.2 | UTF-8 automaton inside strings | semantics.md §3 |
| 1.3 | Numbers and lazy number completion | semantics.md §4 |
| 1.4 | Objects: key order and the candidate rule | semantics.md §5 |
| 1.5 | Arrays | semantics.md §7 |
| 1.6 | `enum`/`const` normalization (exact decimal) | semantics.md §6 |
| 1.7 | Schema acceptance rules | supported_features.md §1 |
| 1.8 | Literal alternatives (FR-3) | semantics.md §9 |
| 1.9 | Completeness and dead ends | semantics.md §10 |

Names used in the parser for these rules:

- Numbers (§1.3): `NumState` runs `start`, `minus`, then `zero_complete`
  or `int_digits`, and for `number` continues through `dot`, `frac_digits`,
  `exp`, `exp_sign`, `exp_digits`. The complete states are
  `zero_complete`, `int_digits`, `frac_digits` and `exp_digits`. A digit
  after `zero_complete` is not a continuation (leading zeros are
  forbidden). In a complete state a byte that cannot continue the number
  pops the frame and is re-fed to the parent.
- Objects (§1.4): the frame keeps `idx` (first unresolved property) and
  `cur` (selected property), with phases `open`, `key`, `key_lit`,
  `value`, `sep` and `key_after_comma`. In `key_after_comma` a `}` is an
  error, which rules out trailing commas.
- Arrays (§1.5): phases `open`, `body`, `sep` and `body_after_comma`; `]`
  in `body_after_comma` is an error.

## 2. Grammar IR (`src/grammar.zig`)

- Node kinds used by canonical-v1: `literal`, `lit_trie`, `str`, `int_v`,
  `num_v`, `choice`, `seq`, `repeat`, `object`.
- Additional kinds used by spec-v1: `int_num` and `num_const` (numbers by
  value), `num_range`, `num_mult`, `num_excl`, `not_int_num`, `str_excl`,
  `str_pat` (regex DFA), `comb` (boolean combinators with a verdict at the
  value boundary) and `open_obj` (objects with a dynamic key set).
- A grammar is immutable after compilation, lives in its own arena and is
  shared by sessions through a reference count held by the C ABI layer.
- `lit_trie` is a shared-prefix trie of literal alternatives (`enum`,
  `const`, literal sets). Nodes and edges are contiguous arrays; a node's
  edges are sorted by byte and searched by binary search. The `literals`
  list enumerates all alternatives for the coverage gate. See ADR-0002
  (literal trie).
- Repetition is a counter (`repeat {item, min, max}`, `max` may be
  unbounded); a large `maxItems` is never expanded into copies.
- Recursive `$ref` is compiled by bounded unrolling (ADR-0008), so every
  grammar is a finite tree.
- Identity: two independent 64-bit hashes of the source bytes and the
  constraint kind (FNV-1a and Wyhash). Both enter the mask-cache key; a
  simultaneous collision (about 2^-128) is the accepted residual risk.

## 3. Parser (`src/parser.zig`)

The parser simulates a nondeterministic automaton with a bounded set of
deterministic threads (ADR-0001).

- A `State` holds up to `MAX_THREADS_CAP` (128) threads; a thread is a stack
  of up to `MAX_DEPTH_CAP` (64) POD frames. The runtime limits
  `max_threads_per_state` and `max_depth` can only lower these caps.
- Variable-size data needed by spec-v1 (the key set of an open object,
  `uniqueItems` signatures, captured element bytes) lives in reference-counted
  copy-on-write chunks in a per-owner side store (`Side`, ADR-0006). Frames
  carry 32-bit handles into it. The cache stores chunk content, never
  handles.
- `initState` expands the root node. `feedBytes(in, bytes, out)` never
  modifies `in`; each thread consumes the bytes, failing threads are
  dropped, and if none survives the result is `error.Parse`. Exceeding the
  thread cap is `error.ResourceLimit`: threads are never dropped silently,
  because that would make masks incomplete.
- Duplicate threads are merged after every byte (`dedupThreads`).
- A `choice` frame spawns its alternatives lazily on the first byte, and
  only those whose language may start with that byte; alternatives that may
  be empty are forked eagerly. A `lit_trie` frame covers all alternatives
  with one thread per trie position.
- A byte arriving when a thread's stack is empty is an error: nothing may
  follow the end of the document.
- `canEnd` reports whether some thread can complete without more bytes.
- `hashState` and `eqlStates` are deterministic and cover frames and chunk
  content; the mask cache uses them for keys and collision checks.

## 4. Masks (`src/mask.zig`, `src/complete.zig`)

- Output: `ceil(vocab_size / 32)` words, bit `t % 32` of word `t / 32`,
  unused trailing bits zero. EOS bits are set when `canEnd` holds; special
  non-EOS tokens are never set. A repeated call on the same state yields the
  same mask bit for bit.
- Trie walk: an iterative depth-first walk over the vocabulary trie (§5).
  Each edge feeds one byte to the parser; a parse error prunes the whole
  subtree; a terminal node sets the bits of every token id with that byte
  image. Nodes with a single edge do not grow the stack, so stack depth is
  the branching depth of the trie, not the token length. The scratch states
  live in a per-session `MaskBuf`.
- Fast path (ADR-0007): for states with uniform residual behavior, such as
  string content, tokens are classified without walking each one
  (`fillMaskFast`). It is an equivalence optimization, verified by a
  differential on/off test; `mask_fast_path` and `BLG_MASK_FAST_PATH=0`
  turn it off.
- Completion filter (ADR-0003, ADR-0005): a token is admitted only if a
  completed document stays reachable after it. The filter is active when
  the vocabulary is not byte-complete and the grammar's language is a finite
  set of literals, or when the grammar has deferred value verdicts
  (spec-v1 combinators, patterns, value-constrained numbers). States are
  first settled by closed-form certificates (`certifyState`), then by a
  bounded search (`stateAlive`) with per-call budgets. A state that cannot
  be settled within the budget is treated as unknown, not alive: the mask
  call fails with `RESOURCE_LIMIT`, and so does `accept` of such a token
  (strict ADR-0005 Decision 3).
- With a byte-complete vocabulary and a canonical-v1 grammar the filter is
  unnecessary: every live parse state can be completed byte by byte
  (semantics.md §10).
- An empty mask is `error.DeadEnd`; nothing is allowed as a fallback.
- All steps are charged to the call's `Work` (§6).
- `mask.zig` keeps a linear vocabulary scan as a reference for equivalence
  tests.

## 5. Tokenizer (`src/tokenizer.zig`)

- The table is copied into context-owned memory and validated: ids cover
  `0..vocab_size-1` exactly once, a regular token has non-empty bytes,
  EOS and special ids are in range and disjoint. Token bytes may be
  arbitrary, including partial UTF-8.
- Identity: FNV-1a-64 over the adapter tag (`"zg-bbpe-v1"`), all token
  bytes in id order, the EOS list, the special list and `vocab_size`; the
  strip-leading-space flag is mixed in, so grammars of contexts with
  different flags never share cache entries.
- Strip leading space: when the decoder drops one leading space of the
  text, the language `L` is compiled as
  `{t in L without a leading space} ∪ {" " + t}` (literals are rebuilt, a
  JSON Schema root is wrapped in `choice[seq(" ", root), root]`).
- Vocabulary trie: a flat prefix tree whose node edges are contiguous and
  sorted by byte. EOS, special and empty tokens are not in the trie.
  Different ids with the same byte image are linked into a chain, and the
  mask sets all of them. `byte_complete` records whether all 256
  single-byte tokens exist.

## 6. Memory, statistics, caches, budgets

- **Accounting** (`src/alloc.zig`): every core allocation goes through
  `Accounting`, with used and peak counters per category (`tokenizer`,
  `grammar`, `session`, `cache`, `temp`) and a total limit; exceeding it is
  `RESOURCE_LIMIT`. `SessionAccount` adds the per-session limit;
  `Limited` enforces the hard cache budget. After a context is destroyed,
  used bytes are zero in every category (checked by tests).
- **Root allocator**: `smp_allocator`. The page allocator was replaced
  because one mapping per allocation exhausted `vm.max_map_count` on long
  runs.
- **Statistics** (`src/stats.zig`) mirror `blg_stats`.
- **Mask cache** (`src/cache.zig`): key
  `{grammar_id, grammar_hi, tokenizer_id, state_hash}`; the value holds the
  serialized state for an exact equality check on hit, plus the mask. All
  of its memory, including table overhead, counts against the hard budget;
  the least recently used entries are evicted, and an entry larger than the
  budget is not stored. Admission in adaptive mode: a computed mask is
  stored when its state was seen at least `adaptive_min_hits` times or took
  at least `adaptive_min_cost_ns` to compute. The cache never changes mask
  contents; `cache_limit_bytes == 0` disables it.
- **Compile-artifact cache** (ADR-0004): repeated compilation of identical
  input returns the existing grammar after a byte comparison. Budget: a
  quarter of the cache limit, counted by actually retained bytes; FIFO
  eviction drops only the cache's reference.
- **Coverage gate** (`src/coverage.zig`): at compile time every grammar
  literal must be segmentable into vocabulary tokens, and open classes and
  structural syntax must have a producible completion; otherwise
  `UNSUPPORTED_TOKENIZER`. The check is conservative; a byte-complete
  vocabulary always passes.
- **Work budget and cancellation** (`src/work.zig`): one `Work` per public
  compile or mask call, holding the op budget and a pointer to the caller's
  cancellation byte (read with acquire ordering). Loops in the parser,
  trie walk, coverage gate and warm-up charge it. The mask call checks the
  flag before consulting the cache, so cancellation wins over a cache hit.
- **Precompute warm-up** (`src/precompute.zig`): a breadth-first walk over
  reachable states at compile time that fills the cache, bounded by
  `precompute_max_states`, temporary memory and cancellation. It has its own
  op counter; running out of budget silently stops the warm-up.

## 7. C ABI implementation (`src/c_api.zig`)

The public contract is in [API.md section 2](API.md#2-c-abi). Invariants
of the implementation:

- Ownership: context > grammars (reference-counted) > sessions. The context
  counts external grammar references, live sessions and calls in progress;
  destroying it while any are non-zero returns `BUSY` and leaves it intact.
- Tail-grown structs are read only up to the caller's `struct_size`, and
  output structs are never written beyond it.
- `blg_accept_token` re-runs the full grammar check; a previously issued
  mask is never trusted. On rejection the session state is unchanged.
- Every Zig error is mapped explicitly (§10); `unreachable` and panics are
  forbidden in exported paths. An unexpected error yields `INTERNAL`, and
  the session is marked aborted when its state can no longer be trusted.
- A session holds its current parse state, a scratch state, its status
  (active, finished, aborted), its `SessionAccount` and statistics.

## 8. Python bridge (`python/`)

- `bolorgir._core` is a C extension built against the Limited API
  (`Py_LIMITED_API=0x030A0000`, abi3). It links against `libbolorgir.so`
  shipped in the package (`rpath $ORIGIN/_lib`); see ADR-0002 (Python
  bridge).
- The GIL is released around native calls. Each session has a lock; a batch
  call deduplicates its sessions and takes their locks in one global order
  (by native pointer), then re-validates them under the locks.
- `setup.py` runs `zig build`, copies the library into the package and
  syncs the C header, so wheels and sdists build without the repository.
- The Python-level contracts of `TokenizerBundle`, `Engine`, the
  Transformers adapter and the serializer are documented in
  [API.md section 3](API.md#3-python-api).

## 9. Tests

- **Zig unit tests** live next to the code in each module and are
  registered in `src/tests.zig` (`zig build test`).
- **Reference implementation** (`tests/reference.py`): an independent
  canonical-v1 compiler and parser written from semantics.md. For bounded
  schemas it enumerates the whole document language; a prefix is admissible
  if and only if it is a prefix of some document.
- **Parity**: exhaustive token sequences up to a fixed depth on small
  vocabularies (`test_parity_exhaustive.py`) and traces on larger schemas
  (`test_parity_traces.py`) compare core masks with the reference bit for
  bit; lazy and adaptive modes must agree.
- **Edge cases** (`test_edge_cases.py`): vocabulary sizes not divisible by
  32, empty batches, invalid ids, small buffers, repeated EOS, special
  tokens, multi-character tokens, partial UTF-8, escapes split across
  tokens, empty strings, enums with shared prefixes, optional keys,
  dead ends.
- **spec-v1**: per-phase tests (`test_spec_v1_p*.py`), each with a
  reachability companion, plus regression suites for fixed defects.
- **Oracle** (`tests/oracle/`): runs the pinned JSON Schema Test Suite
  against the engine with `jsonschema` as the second opinion, across five
  dialects. Results are written to `tests/oracle/results/`, which is not
  versioned; see `tests/oracle/README.md`.
- **Fuzzing**: `tests/fuzz/` (Python) and `src/fuzz_main.zig`
  (`zig build fuzz`).
- Generated documents marked `completed=True` are validated with an
  independent validator.
- `BLG_TEST_BACKEND=ctypes|package` selects whether Python tests call the
  library directly or through the package.

## 10. Error mapping

Status codes are those of `include/bolorgir.h` ([API.md section
2.2](API.md#22-status-codes)). Inside the core:

| Zig error | Status |
|---|---|
| `Parse` in `accept` | `INVALID_TOKEN` |
| `DeadEnd` in a mask call | `DEAD_END` |
| `ResourceLimit`, `OutOfMemory` | `RESOURCE_LIMIT` |
| `Cancelled` | `CANCELLED` |
| Compile errors | `INVALID_SCHEMA`, `UNSUPPORTED_FEATURE`, `UNSATISFIABLE_CONSTRAINT` or `UNSUPPORTED_TOKENIZER`, with a JSON pointer |
| Anything unexpected | `INTERNAL` |

## 11. Commands

```sh
zig build -Drelease=true            # zig-out/lib/libbolorgir.so (ReleaseSafe)
zig build test --summary all        # core unit tests
zig build example-c                 # C example
zig build fuzz -- <args>            # fuzz campaigns
zig fmt --check build.zig src

cd python && ZIG=$(command -v zig) python3 setup.py build_ext --inplace && cd ..
PYTHONPATH=python BLG_TEST_BACKEND=ctypes  python3 -m pytest tests/ python/tests/ -q
PYTHONPATH=python BLG_TEST_BACKEND=package python3 -m pytest tests/ python/tests/ -q
```

Use Debug builds for development and ReleaseSafe for distribution and
measurements.
