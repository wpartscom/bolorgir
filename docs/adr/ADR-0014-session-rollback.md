# ADR-0014: session rollback and draft-token validation

- Status: **proposed** (2026-10-11).
- Context: serving engines with speculative decoding accept a chain of
  draft tokens, then roll the constraint state back by the number of
  tokens the target model rejected, and check a draft chain before
  accepting it (the vLLM `StructuredOutputGrammar.rollback` and
  `validate_tokens`). A session holds two parser states used ping-pong
  (`Session.states` in `src/c_api.zig`); each is a fixed array of
  `MAX_THREADS_CAP` (128) threads of `MAX_DEPTH_CAP` (64) frames of 32 B,
  about 256 KiB. Frames refer to side-store chunks by refcounted handles;
  a chunk referenced more than once is copied before a mutation
  (ADR-0006 D2).

## Decision 1: the history is a ring of compact snapshots

Each successful `blg_accept_token` on a session with rollback enabled
first records the state it is about to leave. The record holds only the
live part of the state: the thread count, per thread its length and tap
count, and the frames `0..len` of each thread in one array. The record
takes its own reference on every chunk handle its frames carry
(`parser.retainState` semantics) instead of copying chunk payloads; since
shared chunks are copied before mutation, the referenced content cannot
change while the record lives. A record costs 32 B per live frame, 4 B
per thread and a fixed header; a typical state of a few threads at depth
4 to 10 takes well under 2 KiB.

Rejected alternatives:

- Full `parser.State` copies: 256 KiB per token, so 32 tokens of depth
  exceed the default 8 MiB session budget.
- Replay from the start (keep the accepted token ids, rebuild by feeding
  them again): memory is minimal, but a rollback costs time linear in the
  document length on the decoding hot path.
- An undo log of frame changes: `parser.feedBytes` rebuilds the output
  state from the input state rather than editing it, so changes are not
  available as deltas without restructuring the parser.

## Decision 2: depth is fixed at session creation

`blg_session_create_ex` takes a `blg_session_config` (`struct_size`,
`max_rollback_tokens`). `max_rollback_tokens = 0`, and
`blg_session_create`, keep the earlier behavior: the accept path adds a
single comparison against zero and no record is taken. The upper bound is
`BLG_MAX_ROLLBACK_TOKENS` (65,535); a larger value is
`BLG_ERR_INVALID_ARGUMENT`. The ring array grows on demand up to the
depth, so a large depth does not reserve memory up front.

When the ring holds `max_rollback_tokens` records, the next accept drops
the oldest one. `blg_session_rollback(s, n)` with `n` greater than the
number of records held fails with `BLG_ERR_INVALID_ARGUMENT` and changes
nothing; `n = 0` is a no-op. A rollback of `n` drops the `n - 1` newest
records and moves the `n`-th newest into the current state; its chunk
references pass to the state.

## Decision 3: what one record undoes

Every successful accept adds one record, EOS included: an accepted EOS
leaves the parser state unchanged and its record is a copy of it. So
`rollback(n)` undoes exactly the last `n` successful `blg_accept_token`
calls. A failed accept adds nothing. Rollback is valid while the session
is active; after `blg_finish` or `blg_abort` it is `BLG_ERR_WRONG_STATE`.
Statistics (`tokens_accepted`, `accept_ns_total`) count work done and are
not rolled back.

## Decision 4: memory accounting

Records and the ring come from the session allocator and count against
`session_limit_bytes` (memory category `BLG_MEM_SESSION`). An accept
whose record does not fit fails with `BLG_ERR_RESOURCE_LIMIT` and leaves
the state and the history unchanged: the depth promised at creation is
never reduced silently. `blg_session_destroy` releases every record
before the side store.

## Decision 5: draft validation does not touch the session

`blg_validate_tokens(s, ids, n, &prefix)` runs the accept logic of every
token in order (id range, special tokens, EOS only when the document can
end, the byte feed, and the completion check where `blg_accept_token`
runs it) on a working copy and stores the length of the longest prefix
that the same sequence of `blg_accept_token` calls would accept. The copy
lives in the session's scratch state, which is free between calls, and
one temporary state from the context temp budget, used ping-pong. The
logical session state, the history and the statistics do not change; the
completion memo may gain entries, which are keyed by state content and
stay valid. `BLG_ERR_RESOURCE_LIMIT` or `BLG_ERR_CANCELLED` ends the call
with that status and `*prefix` holding the tokens decided before the
failing one. As with `blg_accept_token`, a token after an accepted EOS is
fed to the same state; the serving adapter stops a chain at EOS.

## Decision 6: why masks after a rollback are exact

The restored state is the exact frame image the session had, with the
same chunk contents. Every cache that can answer for it is keyed by
content: the mask cache by the state hash plus the chunk blob, the
completion memo by the state key. A mask after `k` accepts and a rollback
of `k` is therefore bit-for-bit the mask before the accepts, and
`blg_can_end` agrees (property test in `tests/test_session_rollback.py`).

## Decision 7: ABI

Additive under the general ABI rule: the new struct `blg_session_config`,
the new functions `blg_session_create_ex`, `blg_session_rollback` and
`blg_validate_tokens`; `BLG_ABI_VERSION` stays 1. Clients look the
symbols up when they load the library. Python:
`Constraint.create_session(max_rollback_tokens=0)`, `Session.rollback(n)`,
`Session.validate_tokens(ids)`.

## Verification

- Property test: for random walks on spec-v1 schemas, after `k` accepts
  and `rollback(k)` the mask and `can_end` equal the values before; a
  rollback past the held records changes nothing; `validate_tokens`
  equals sequential `accept_token` on a separate session fed the same
  prefix. Zig unit tests cover the ring (wrap, drop of the oldest,
  release of chunk references) and the session budget failure.
- Overhead measurement (`benchmarks/bench_rollback_overhead.py
  --check-bound`, 2026-10-11): library A = the kernel before this change
  (git `2523dd9`, built with Zig 0.17.0, sha256 `4783434d…`), library B =
  the new kernel (sha256 `1ed06363…`), both built with the same flags
  (`zig build -Drelease=true`), paired and interleaved, 100 rounds on
  byte-identical token streams (stream sha256 `10bb3a00…`, 8,285 tokens).
  Kernel accept time per token with `max_rollback_tokens = 0`: median
  ratio B/A 1.0045, bootstrap 95% interval [0.9982, 1.0176], bound 1.02:
  **within**. An earlier 20-round run before the zero-depth fast path was
  put in place showed a median of 1.0230 (overhead above the bound); the
  accept path then gated the record call on a non-zero depth, and this
  ADR's `max_rollback_tokens = 0` contract now adds one comparison per
  accept. The measurement is a candidate-local artifact; the full
  acceptance of the kernel (T3/R6) is a later, separate protocol.
