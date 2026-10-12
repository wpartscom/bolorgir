# API reference

This document describes the public interfaces of Bolorgir: the C ABI
(`include/bolorgir.h`, ABI version 1) and the Python package `bolorgir`
(0.1.0), including the Hugging Face Transformers adapter and the spec-v1
serializer.

Related documents:

- [supported_features.md](supported_features.md) - what each profile
  accepts, default limits, error-code causes.
- [semantics.md](semantics.md) and
  [semantics-spec-v1.md](semantics-spec-v1.md) - the document languages the
  engine generates and accepts.
- [DESIGN.md](DESIGN.md) - implementation notes for contributors.

The header `include/bolorgir.h` is the authoritative declaration of the C
ABI; this file explains how to use it.

## 1. Concepts

| Object | C type | Python type | Lifetime |
|---|---|---|---|
| Context | `blg_context` | `Engine` | Owns the tokenizer table, limits, mode and caches. Must outlive its grammars and sessions. |
| Grammar | `blg_grammar` | `Constraint` | A compiled constraint (JSON Schema or literal set). Immutable, reference-counted, shared by any number of sessions. |
| Session | `blg_session` | `Session` | One generation: the parse state of the document produced so far. |

A typical generation step:

1. `fill_mask` - get the set of token ids allowed at the current position.
2. Apply the mask to the logits and sample a token (outside the engine).
3. `accept_token` - advance the session with the sampled token.
4. When an EOS token is accepted, call `finish`.

**Mask layout.** A mask is an array of `ceil(vocab_size / 32)` `uint32`
words. Token `t` is bit `t % 32` of word `t / 32`; `1` means allowed.
Unused bits of the last word are always `0`. In Python the mask is
returned as `bytes` of `mask_words * 4` bytes in little-endian word order.

## 2. C ABI

### 2.1. Conventions

- **Versioned structs.** Every parameter struct starts with
  `uint32_t struct_size`; set it to `sizeof(...)`. Structs only grow at the
  tail: a caller compiled against an older, smaller layout passes its
  smaller `struct_size` and gets documented defaults for the fields it does
  not know about. The library never writes beyond the caller's
  `struct_size`. Accepted legacy sizes: `blg_error` 268,
  `blg_context_config` 56 to 104 (any earlier tail), `blg_stats` 208 and
  264, `blg_compile_request` 32 and 48.
- **ABI evolution.** `BLG_ABI_VERSION` is 1 and is checked for exact
  equality, so raising it breaks every existing client; changes are
  additive and keep the version. New struct fields go only to the tail of a
  struct with `struct_size`; the value 0 of a new field means the earlier
  behavior, and an unknown nonzero value (an unknown flag bit) is
  `INVALID_ARGUMENT`. New functions are new exported symbols; a client
  checks that they exist when it loads the library. Incompatible changes
  are not made within ABI version 1.
- **Diagnostics** go to a caller-provided `blg_error` (code, byte offset in
  the schema or `UINT32_MAX`, a NUL-terminated UTF-8 message of up to 256
  bytes, and an RFC 6901 `json_pointer` to the failing schema node, `""`
  when not applicable). There is no global "last error". `err` may be
  `NULL`; a non-`NULL` buffer with an unknown `struct_size` makes the call
  fail with `INVALID_ARGUMENT`.
- **No panics across the ABI.** Every exported function returns a
  `blg_status`; an unexpected internal error is reported as
  `BLG_ERR_INTERNAL`, and the affected session is marked aborted.
- **Input buffers are not retained.** Schema bytes, registry bytes and the
  tokenizer table are copied or compiled during the call.
- **Threads.** Calls on different sessions may run concurrently. A single
  session must not be used from two threads at once (the caller
  serializes; the Python wrapper does this for you). Destroying a context
  must be serialized by the caller with calls that may start afterwards.

### 2.2. Status codes

| Code | Name | Meaning |
|---|---|---|
| 0 | `BLG_OK` | Success. |
| 1 | `BLG_ERR_INVALID_ARGUMENT` | Bad struct size or version, null pointer, misaligned buffer, inconsistent limits. |
| 2 | `BLG_ERR_INVALID_SCHEMA` | The schema violates an acceptance rule. |
| 3 | `BLG_ERR_UNSUPPORTED_FEATURE` | A keyword or construct outside the selected profile; unknown profile. |
| 4 | `BLG_ERR_UNSATISFIABLE_CONSTRAINT` | The constraint admits no document. |
| 5 | `BLG_ERR_UNSUPPORTED_TOKENIZER` | The tokenizer table is invalid or cannot cover the constraint. |
| 6 | `BLG_ERR_INVALID_TOKEN` | The token is not allowed in the current state; the state is unchanged. |
| 7 | `BLG_ERR_DEAD_END` | No token and no EOS is allowed; the mask is invalid. |
| 8 | `BLG_ERR_RESOURCE_LIMIT` | A memory, depth, thread, size or work limit was reached. |
| 9 | `BLG_ERR_CANCELLED` | The call was cancelled through the cancellation flag. |
| 10 | `BLG_ERR_BUSY` | The context still has live grammars, sessions or calls. |
| 11 | `BLG_ERR_WRONG_STATE` | The operation is not valid in the session's state. |
| 12 | `BLG_ERR_BUFFER_TOO_SMALL` | The mask buffer is shorter than `ceil(vocab_size / 32)` words. |
| 13 | `BLG_ERR_INTERNAL` | Unexpected internal error. |

Typical causes per code: [supported_features.md section 7](supported_features.md#7-error-codes-includebolorgirh).

### 2.3. Context

```c
blg_status blg_context_create(const blg_context_config *config,
                              const blg_tokenizer_desc *tokenizer,
                              blg_context **out_context, blg_error *err);
blg_status blg_context_destroy(blg_context *ctx);
blg_status blg_context_reset_cache(blg_context *ctx);
uint32_t   blg_abi_version(void);   /* == BLG_ABI_VERSION */
```

**`blg_context_config`.** For every field except `cache_limit_bytes`, the
value `0` selects the default.

| Field | Default | Notes |
|---|---|---|
| `version` | - | Must be `BLG_ABI_VERSION`. |
| `mode` | `BLG_MODE_LAZY` (0) | `LAZY`: no mask cache. `ADAPTIVE`: mask cache with an admission policy. `PRECOMPUTE` (experimental): adaptive plus a bounded mask warm-up at compile time. All modes produce bit-identical masks. |
| `max_depth` | 64 | Structural depth. Hard cap 64; larger values are `INVALID_ARGUMENT`. |
| `max_threads_per_state` | 128 | Distinct live parse branches per state, counted after each byte (not CPU threads). Hard cap 128. |
| `memory_limit_bytes` | 256 MiB | Total budget of the context. |
| `cache_limit_bytes` | `0` = cache off | `BLG_CACHE_DEFAULT` (`UINT64_MAX`) selects 64 MiB. Must not exceed `memory_limit_bytes`. |
| `session_limit_bytes` | 8 MiB | Per session. |
| `schema_limit_bytes` | 1 MiB | Per input schema. |
| `work_limit_ops` | 0 = unlimited | Work budget per `blg_compile` / `blg_fill_mask(s)` call, in abstract operations. Exceeding it returns `RESOURCE_LIMIT`, never a partial result. |
| `adaptive_min_hits` | 2 | A computed mask is cached when its state was seen at least this many times... |
| `adaptive_min_cost_ns` | 50000 | ...or when computing it took at least this long. |
| `precompute_max_states` | 4096 | Warm-up bound in `PRECOMPUTE` mode. Exhausting it is not an error. |
| `mask_fast_path` | enabled | `1` enabled, `2` disabled. The environment variable `BLG_MASK_FAST_PATH=0` forces it off. Masks are identical either way. |
| `max_workers` | 1 | CPU workers for mask classification. Masks do not depend on the worker count. |
| `probe_max_states` | 256 | Visited states of the compile-time probe (section 2.4). |
| `probe_max_work` | 16M | Work ops of the probe; independent of `work_limit_ops`. The probe's memory counts against `memory_limit_bytes`. |

**`blg_tokenizer_desc`.** The token table is passed once and copied.

- `entries` must cover every id in `0..vocab_size-1` exactly once; each
  entry points at `length` bytes in `blob`. A regular token with
  `length == 0` is `UNSUPPORTED_TOKENIZER`.
- `eos_ids` are the tokens that may end a document; `special_ids` are
  auxiliary tokens (BOS, PAD, ...) that are never allowed inside a
  document. The two sets must not overlap.
- `flags`: `BLG_TOKENIZER_STRIP_LEAD_SPACE` declares a decoder that drops
  one leading space of the whole text (SentencePiece
  `Strip(" ", start=1, stop=0)`). The engine then compiles the language as
  `{t in L without a leading space} ∪ {" " + t}`, so the accepted bytes
  match the decoded text exactly.

**Destroying a context.** `blg_context_destroy` returns `BLG_ERR_BUSY` while
the context has a live grammar handle held by the caller, a live session,
or a call in progress. The context is left intact; release the remaining
objects and call destroy again.

**`blg_context_reset_cache`** drops all cached compile artifacts and returns
their memory to the context budget. Live grammars and sessions keep
working.

### 2.4. Compilation

```c
blg_status blg_compile(blg_context *ctx, const blg_compile_request *request,
                       blg_grammar **out_grammar, blg_error *err);
void       blg_grammar_release(blg_grammar *grammar);
blg_status blg_grammar_probe_status(const blg_grammar *grammar, uint32_t *out_status);
```

**`blg_compile_request`:**

| Field | Meaning |
|---|---|
| `kind` | `BLG_CONSTRAINT_JSON_SCHEMA`, `BLG_CONSTRAINT_LITERAL_SET`, `BLG_CONSTRAINT_REGEX` or `BLG_CONSTRAINT_STRUCTURAL_TAG`. |
| `profile` | `"canonical-v1"` (also `NULL`), or `"spec-v1"`. Any other value is `UNSUPPORTED_FEATURE`. Literal sets, regex and structural-tag constraints use `NULL` (a non-`NULL` profile with them is `INVALID_ARGUMENT`). |
| `data`, `data_len` | The schema as UTF-8 JSON, or a JSON array of strings for a literal set, or the UTF-8 pattern for a regex constraint, or the structural-tag document (see below). |
| `registry_data`, `registry_data_len` | Optional, `spec-v1` only: an immutable snapshot of external schemas, `{"version": "...", "documents": {"<uri>": <schema>, ...}}`. An external `$ref` whose base URI matches a `documents` key resolves inside that document; the engine never accesses the network. Without a registry every external `$ref` is `UNSUPPORTED_FEATURE`. |
| `flags` | `0`, or `BLG_COMPILE_PROBE` / `BLG_COMPILE_PROBE_STRICT` (below). Unknown bits are `INVALID_ARGUMENT`; the probe flags with a regex or structural-tag constraint are `UNSUPPORTED_FEATURE` (the probe is defined for JSON grammars). |

Behavior:

- Compilation errors fill `err->json_pointer` and `err->schema_offset`.
  Unsupported input is always rejected at compile time; the engine never
  falls back to unconstrained generation.
- A `BLG_CONSTRAINT_REGEX` request compiles the UTF-8 pattern into a DFA
  over Unicode scalar values and constrains the whole text to match it
  (ECMA-262 `^(?:p)$`). It needs a byte-complete vocabulary
  (`UNSUPPORTED_TOKENIZER` otherwise); unsupported constructs are
  `UNSUPPORTED_FEATURE` with the construct's first byte in
  `schema_offset`, malformed patterns are `INVALID_SCHEMA` with the offset
  where parsing stopped, and a pattern that matches nothing is
  `UNSATISFIABLE_CONSTRAINT`. The supported syntax is section 3.1 of
  [supported_features.md](supported_features.md#31-standalone-regex-constraint-blg_constraint_regex);
  the design is [ADR-0015](adr/ADR-0015-regex-constraint.md).
- A `BLG_CONSTRAINT_STRUCTURAL_TAG` request compiles a tool-call framing
document (free text with triggers, tags of a begin literal, a JSON
arguments value and an end literal). The accepted JSON subset and the
byte-exact semantics are section 3.2 of
[supported_features.md](supported_features.md#32-structural-tag-constraint-blg_constraint_structural_tag)
and Finding 3 of [ADR-0016](adr/ADR-0016-vllm-agent-integration.md). It
needs a byte-complete vocabulary (`UNSUPPORTED_TOKENIZER` otherwise);
constructs outside the subset are `UNSUPPORTED_FEATURE` with the byte
offset of the offending field in `schema_offset`, malformed documents
are `INVALID_SCHEMA`, and a language that can never match is
`UNSATISFIABLE_CONSTRAINT`.
- Compilation also checks that the tokenizer can produce every document of
  the constraint (the coverage gate); if not, the result is
  `UNSUPPORTED_TOKENIZER` with an explanation. See
  [supported_features.md section 5](supported_features.md#5-platforms-and-tokenizers).
- **Compile-artifact cache.** When the mask cache is enabled and the mode is
  not `LAZY`, compiling the same bytes with the same kind, profile and
  registry again in the same context returns a new reference to the
  already built grammar. The artifact cache uses at most a quarter of
  `cache_limit_bytes`; eviction never affects live grammars or sessions.
- `blg_grammar_release` drops the caller's reference. Sessions hold their own
  reference, so a grammar may be released while its sessions are alive.

**Compile-time probe** ([ADR-0012](adr/ADR-0012-generatability-probe.md)).
With `BLG_COMPILE_PROBE` the compile also searches, breadth first, the
parser states reachable by tokens the mask allows and computes each state's
mask on the context vocabulary, within `probe_max_states` and
`probe_max_work`:

| Outcome | Compile | `blg_grammar_probe_status` | Guarantee |
|---|---|---|---|
| found: a reachable state whose mask fails with `RESOURCE_LIMIT` | `RESOURCE_LIMIT`; the message carries the token path to the state, `json_pointer` the subschema of the undecided frame | (no handle) | a real counterexample: a session that accepts the path fails the next `fill_mask` |
| inconclusive: a budget ran out | succeeds; `RESOURCE_LIMIT` "probe inconclusive" with `BLG_COMPILE_PROBE_STRICT` | `BLG_PROBE_INCONCLUSIVE` | nothing about the states not visited |
| proven: every reachable state was visited | succeeds | `BLG_PROBE_PROVEN` | no completion check of this grammar is undecided for this vocabulary and these limits; memory and work limits and cancellation can still fail a call |

`proven` is reachable only when the set of exact parser states is finite:
literal and enum languages over strings, booleans and null, and closed
objects and fixed arrays of them. Unbounded strings, numbers, open objects
and unbounded arrays end `inconclusive`. The verdict is kept with the
cached artifact together with the budgets; a cache hit reuses it for equal
budgets and probes again otherwise. A grammar compiled without the flag
reports `BLG_PROBE_NOT_RUN` unless its cached artifact was probed.

### 2.5. Sessions

```c
blg_status blg_session_create(blg_context *ctx, blg_grammar *grammar,
                              blg_session **out_session, blg_error *err);
blg_status blg_session_create_ex(blg_context *ctx, blg_grammar *grammar,
                                 const blg_session_config *config,
                                 blg_session **out_session, blg_error *err);
void       blg_session_destroy(blg_session *session);

blg_status blg_fill_mask(blg_session *session, uint32_t *mask,
                         size_t mask_words, blg_error *err);
blg_status blg_fill_masks_batch(blg_session *const *sessions,
                                uint32_t *const *masks, size_t mask_words_each,
                                int32_t *statuses, size_t count, blg_error *err);
blg_status blg_accept_token(blg_session *session, uint32_t token_id,
                            blg_error *err);
blg_status blg_session_rollback(blg_session *session, uint32_t n_tokens);
blg_status blg_validate_tokens(const blg_session *session,
                               const uint32_t *ids, size_t n,
                               size_t *out_accepted_prefix);
blg_status blg_can_end(const blg_session *session, bool *out_can_end);
blg_status blg_session_struct_segment(const blg_session *session,
                                      uint8_t *out_phase, uint8_t *out_variant);
blg_status blg_finish(blg_session *session, blg_error *err);
blg_status blg_abort(blg_session *session);
```

A session is **active** until `blg_finish` (finished) or `blg_abort`
(aborted). `blg_session_destroy` is valid in any state.

**Rollback history** ([ADR-0014](adr/ADR-0014-session-rollback.md)).
`blg_session_create_ex` takes a `blg_session_config`:

| Field | Meaning |
|---|---|
| `struct_size` | `sizeof(blg_session_config)`. |
| `max_rollback_tokens` | Rollback depth, `0..BLG_MAX_ROLLBACK_TOKENS` (65,535). `0` disables the history (the `blg_session_create` behavior). |

The session records the state before each of its last
`max_rollback_tokens` successful accepts (EOS included); records are
charged to `session_limit_bytes`. An accept whose record does not fit
the budget fails with `RESOURCE_LIMIT` and leaves the state unchanged.

#### blg_session_rollback

Undoes the last `n_tokens` successful `blg_accept_token` calls: the mask
and `blg_can_end` are then exactly those before them. `n_tokens` above
the records held is `INVALID_ARGUMENT` with no change; `0` is a no-op.
`WRONG_STATE` unless the session is active. Statistics are not rolled
back.

#### blg_validate_tokens

Stores in `*out_accepted_prefix` the length of the longest prefix of
`ids[0..n)` that consecutive `blg_accept_token` calls would accept,
without changing the session. `RESOURCE_LIMIT` or `CANCELLED` ends the
call with that status and the tokens decided before the failing one in
`*out_accepted_prefix`. `WRONG_STATE` unless the session is active;
`NULL` ids with `n > 0` is `INVALID_ARGUMENT`. Calls on one session are
serialized like any other session call (the validator uses the session's
scratch storage).

#### blg_session_struct_segment

Diagnostics for structural-tag sessions
(`BLG_CONSTRAINT_STRUCTURAL_TAG`, [ADR-0016](adr/ADR-0016-vllm-agent-integration.md));
not part of the generation semantics. `*out_phase`: `0` free text,
`1` begin literal, `2` JSON content or end literal, `3` closed
(`stop_after_first`). `*out_variant`: the active tag variant index, or
`0xFF` when no single variant is active (free text, or a begin prefix
shared by several variants). Read-only. `WRONG_STATE` for another
constraint kind or an inactive session; `NULL` out pointers are
`INVALID_ARGUMENT`.

#### blg_fill_mask

- `mask` is a caller-owned buffer of `mask_words` words, at least
  `ceil(vocab_size / 32)` (otherwise `BUFFER_TOO_SMALL`) and 4-byte aligned
  (otherwise `INVALID_ARGUMENT`). It can be reused across steps.
- The call does not change the session state; repeating it on the same
  state yields the same mask bit for bit.
- EOS bits are set when the document can end here. Special non-EOS tokens
  are never set.
- If no token and no EOS is allowed, the result is `DEAD_END`; the engine
  never allows the whole vocabulary as a fallback.
- `RESOURCE_LIMIT` is returned when a limit is reached, and, for grammars
  that need a completion-reachability check (see
  [supported_features.md section 1a](supported_features.md#1a-json-schema-profile-spec-v1)),
  when the reachability of a completed document cannot be settled within
  the internal search budget. In both cases the mask is invalid; a token is
  never admitted without proof.
- On any error the contents of `mask` are unspecified.

#### blg_fill_masks_batch

Fills masks for independent sessions: `masks[i]` receives the mask of
`sessions[i]`, and `statuses[i]` always receives that row's status. Rows do
not affect each other: a finished or aborted session gets `WRONG_STATE` in
its row while the other rows are processed. Returns `BLG_OK` when every row
succeeded, otherwise the status of the first failing row, whose diagnostics
are copied into `err`. `count == 0` is a no-op returning `BLG_OK`.

#### blg_accept_token

- `token_id >= vocab_size`: `INVALID_TOKEN`.
- A special non-EOS token: `INVALID_TOKEN`.
- An EOS token: allowed only when the document can end
  (`blg_can_end` is true), otherwise `INVALID_TOKEN`. Accepting EOS does not
  change the state, so EOS may be accepted again; call `blg_finish` to close
  the session.
- Any other token is checked against the grammar from scratch: a previously
  returned mask is not trusted. A token the grammar rejects, or one after
  which no completed document is reachable, is `INVALID_TOKEN`. When
  reachability cannot be settled within the search budget, the token is
  refused with `RESOURCE_LIMIT`.
- On any error the session state is unchanged.
- On a finished or aborted session: `WRONG_STATE`.

#### blg_can_end, blg_finish, blg_abort

- `blg_can_end` reports whether the bytes accepted so far form a complete
  document.
- `blg_finish` requires `can_end`, otherwise `WRONG_STATE`. `blg_finish` and
  `blg_abort` on an already finished or aborted session return `BLG_OK`.
- `blg_fill_mask` and `blg_accept_token` on a finished or aborted session
  return `WRONG_STATE`.

### 2.6. Cancellation and work limits

```c
blg_status blg_cancel_flag_set(blg_context *ctx, uint8_t *flag);
```

Registers a caller-owned byte that the engine reads atomically (acquire)
between bounded portions of work in `blg_compile` and `blg_fill_mask(s)`.
Store a non-zero value (release ordering, for example C11
`atomic_store_explicit(..., memory_order_release)`) to make running and
subsequent calls on this context return `BLG_ERR_CANCELLED`; store `0` to
clear it. The check happens even when the mask would come from the cache.
The byte must stay valid until `blg_cancel_flag_set(ctx, NULL)` or
`blg_context_destroy(ctx)`.

`work_limit_ops` bounds the work of a single call; exceeding it returns
`RESOURCE_LIMIT` without a partial mask. The `PRECOMPUTE` warm-up has its
own bounds and is never charged against the call's budget; only
cancellation propagates out of it.

### 2.7. Statistics

```c
blg_status blg_get_stats(const blg_context *ctx, blg_stats *out_stats);
blg_status blg_get_stats_session(const blg_session *session, blg_stats *out_stats);
```

`blg_stats` reports compile, tokenizer-preparation, accept and mask times
(nanoseconds), mask calls, accepted tokens, cache hits/misses/evictions,
memory used and peak per category (`BLG_MEM_TOKENIZER`, `_GRAMMAR`,
`_SESSION`, `_CACHE`, `_TEMP`, `_TOTAL`), the effective mode, error
counters (`errors_total`, `errors_resource_limit`, `errors_cancelled`),
masks skipped by the cache admission policy, warm-up states, work
operations charged and probe searches run (`probe_runs`).

### 2.8. Diagnostics

```c
size_t blg_cert_stats(uint64_t *out, size_t cap);
void   blg_cert_stats_reset(void);
```

A per-thread histogram of the reasons why completion reachability could not
be settled (the cases that end in `RESOURCE_LIMIT`). It exists for profiling
and is **not** part of the stable interface: indices follow an internal
enumeration in `src/complete.zig` and may change between releases.

### 2.9. Example

[`examples/c_client.c`](../examples/c_client.c) is a complete client without
Python: it builds a byte-level tokenizer table, compiles a schema, and runs
mask/accept steps. Build and run it with `zig build example-c`.

## 3. Python API

```sh
pip install bolorgir                    # core (once the package is published)
pip install "bolorgir[transformers]"    # + Hugging Face adapter
```

Nothing is on PyPI yet: the two lines above are the target interface, and
until the release is published the package is installed from a checkout
(see the README install section; the sdist bundles the core library and
needs no Zig). The status of a local candidate and of the published
package are kept separate.

The package imports without torch, numpy or transformers. The bridge is a
CPython extension built against the Limited API (abi3, CPython 3.12+); the
GIL is released during native calls.

### 3.1. TokenizerBundle

```python
TokenizerBundle.from_hf(tokenizer, *, use_cache=True) -> TokenizerBundle
TokenizerBundle.from_token_bytes(token_bytes, eos_ids=(), special_ids=()) -> TokenizerBundle
TokenizerBundle(token_bytes, eos_ids=(), special_ids=(), strip_first_space=False)
```

Properties: `vocab_size`, `eos_ids`, `special_ids`, `strip_first_space`,
`mask_words`; method `token_bytes(token_id) -> bytes`.

`from_hf` extracts the exact byte image of every token from a Hugging Face
tokenizer (requires `transformers`):

- Supported families: byte-level BPE (GPT-2, Qwen, Llama 3 style) and
  SentencePiece with byte fallback.
- Byte images follow the tokenizer's actual decoder, not the token text.
  `<0xNN>` is one byte only when the decoder chain contains `ByteFallback`;
  in byte-level BPE it is literal text, as in `tokenizer.decode`. Added
  tokens go through the same decoder.
- Only exact decoder chains are accepted, in order and multiplicity:
  `ByteLevel`; or SentencePiece `Replace(▁ → " ")` with optional
  `ByteFallback`, `Fuse` and `Strip(" ", start=1, stop=0)`. `Strip` is
  accepted only after `Fuse`. `Strip(start=1)` sets `strip_first_space`.
  Any other chain raises `UnsupportedTokenizerError`; there is no
  approximation.
- Results are cached per process. The cache key includes the tokenizer
  name, revision and a fingerprint of the vocabulary, decoder configuration,
  added tokens and special ids. Pass `use_cache=False` to bypass it.

### 3.2. Engine

```python
Engine(*, mode="adaptive", memory_limit_mb=256, cache_limit_mb=64,
       session_limit_mb=8, schema_limit_mb=1, max_depth=64, max_threads=64,
       work_limit_ops=0, adaptive_min_hits=0, adaptive_min_cost_ns=0,
       precompute_max_states=0, probe_max_states=0, probe_max_work=0,
       tokenizer=None)
```

Wraps a context. Parameters map to `blg_context_config` (section 2.3);
`mode` is `"lazy"`, `"adaptive"` or `"precompute"`. Note the differences
from the C defaults: the Python default mode is `"adaptive"`,
`cache_limit_mb=64` requests a 64 MiB cache (`0` disables it), and
`max_threads` defaults to 64. Zero values of the `adaptive_*`,
`work_limit_ops` and `precompute_max_states` parameters select the core
defaults.

The tokenizer (a `TokenizerBundle` or a Hugging Face tokenizer) can be passed
to the constructor, which builds the native context immediately, or to the
first `compile` call. Once bound it cannot be changed; create a new `Engine`
for another tokenizer.

| Member | Description |
|---|---|
| `compile(schema, tokenizer=None, profile="canonical-v1", probe=None) -> Constraint` | `schema` is a `dict`, JSON `str` or `bytes`; `profile` is `"canonical-v1"` or `"spec-v1"`. `probe="probe"` or `"strict"` runs the compile-time probe of section 2.4; a found state raises `ResourceLimitError` with the token path and the subschema pointer in the message. |
| `compile_literals(literals, tokenizer=None) -> Constraint` | A non-empty list of strings; the answer is exactly one of them. |
| `compile_regex(pattern, tokenizer=None) -> Constraint` | A standalone regular expression: the whole generated text must match, ECMA-262 `^(?:pattern)$` over Unicode scalar values, with the JSON Schema `pattern` syntax (section 3.1 of supported_features.md). Needs a byte-complete vocabulary. `UnsupportedFeatureError` (with the byte offset) separates unsupported constructs from malformed ones (`InvalidSchemaError`). |
| `compile_structural_tag(spec, tokenizer=None) -> Constraint` | A structural-tag document (tool-call framing; section 3.2 of supported_features.md, ADR-0016): `dict`, `str` or `bytes`. Needs a byte-complete vocabulary. Constructs outside the accepted subset raise `UnsupportedFeatureError` with the byte offset of the offending field. |
| `stats() -> dict` | Context statistics: the timing and cache counters of section 2.7 plus `mem_used` / `mem_peak` dicts keyed by category name. |
| `mode`, `vocab_size`, `mask_words`, `eos_ids` | Read-only properties. |
| `cancel_token() -> CancelToken` | Creates and registers a cancellation token. |
| `register_cancel_token(token)`, `clear_cancel_tokens()` | Manage cancellation tokens. |
| `close()` | Frees the native context and the tokenizer table deterministically. Raises `BusyError` while a `Constraint` or `Session` is still open; close them and call again. |

`Engine`, `Constraint` and `Session` are context managers.

Not exposed in Python: the external-reference registry, `mask_fast_path`
(use the `BLG_MASK_FAST_PATH=0` environment variable) and `max_workers`.

### 3.3. Constraint and Session

```python
constraint.create_session(max_rollback_tokens=0) -> Session
constraint.probe_status    # "not_run" | "inconclusive" | "proven" (section 2.4)
constraint.close()

session.fill_mask() -> bytes                  # mask_words * 4 bytes, uint32 LE
session.fill_mask_into(buffer)                # zero-copy into a writable buffer
session.allowed_token_ids(mask=None) -> list[int]
session.mask_numpy(mask=None) -> numpy.ndarray  # bool[vocab_size], needs numpy
session.accept_token(token_id)
session.rollback(n_tokens)                    # needs a session created with a depth
session.validate_tokens(token_ids) -> int     # accepted prefix, state unchanged
session.can_end() -> bool
session.finish()
session.abort()
session.stats() -> dict
session.close()

bolorgir.fill_masks_batch(sessions) -> list[bytes]
```

Each method follows the C function of the same name (section 2.5) and
raises the exception that corresponds to the status code. Operations on one
session are serialized by a per-session lock. `fill_masks_batch` accepts
duplicate sessions and is safe against overlapping concurrent batches;
if any row fails it raises the exception of the first failing row.
`fill_mask_into` writes the mask directly into any object supporting the
writable buffer protocol (for example a `numpy` view of a tensor row) of
at least `mask_words * 4` bytes; the object must be 4-byte aligned and
C-contiguous.

### 3.4. Cancellation

```python
token = engine.cancel_token()
token.cancel()        # the next compile / fill_mask call raises CancelledError
token.cancelled       # -> bool
```

The engine keeps a reference to every registered token.

### 3.5. Exceptions

All exceptions derive from `ZigConstraintsError`. There is one class per
status code: `InvalidArgumentError`, `InvalidSchemaError`,
`UnsupportedFeatureError`, `UnsatisfiableConstraintError`,
`UnsupportedTokenizerError`, `InvalidTokenError`, `DeadEndError`,
`ResourceLimitError`, `CancelledError`, `BusyError`, `WrongStateError`,
`BufferTooSmallError`, `InternalError`. `UnsupportedModeError` (also a
subclass of `ZigConstraintsError`) reports incompatible generation settings
in the Transformers adapter. `bolorgir.abi_version()` returns the ABI
version of the loaded core.

### 3.6. Hugging Face Transformers adapter

```python
from bolorgir.transformers import constrained_generate

result = constrained_generate(model, tokenizer, constraint, inputs,
                              max_new_tokens=128, do_sample=False,
                              on_constraint_error="raise", **gen_kwargs)
result.sequences     # torch.Tensor [batch, total_len]
result.completed     # list[bool]
result.stop_reason   # list[str]: "eos" | "length" | "dead_end" | "error" | "constraint_error"
result.sessions      # list[Session | None]
result.errors        # list[str | None]: engine error text of a failed row
```

Supported setup: decoder-only models, a single process and device, greedy
decoding or sampling, a fixed batch with left padding. `constraint` is one
`Constraint` for the whole batch or a list with one per row.

- Masks are applied before top-k/top-p, so later processing cannot
  re-allow a forbidden token.
- `completed[i]` is `True` only when row `i` accepted an allowed EOS and
  `finish()` succeeded. A row that hit `max_new_tokens` without EOS reports
  `completed=False`, `stop_reason="length"`.
- Unsupported settings are rejected before generation with
  `UnsupportedModeError`: `num_beams > 1`, `num_return_sequences > 1`,
  `forced_eos_token_id`, `forced_decoder_ids`. The check uses the effective
  generation config (a passed `generation_config` replaces
  `model.generation_config`, keyword arguments override it, and an explicit
  `None` clears a value).
- The stop set is the union of `tokenizer.eos_token_id` and the effective
  `eos_token_id`, restricted to EOS ids registered in the core. Other ids
  are removed with a `UserWarning`; if none remain, `UnsupportedModeError`
  is raised.
- NaN or `+inf` among mask-allowed logits raises `ZigConstraintsError`
  instead of sampling silently.
- If the model's logit width exceeds the tokenizer vocabulary (a padded
  `lm_head`), the extra positions are always masked.
- `on_constraint_error="raise"` (default) propagates an engine error of any
  row (for example `ResourceLimitError` from a mask, see
  [supported_features.md section 6](supported_features.md)) and stops the
  batch. `"fail_row"` marks that row failed: from then on only its EOS is
  allowed, the other rows continue, and the result reports
  `completed=False`, `stop_reason="constraint_error"` and the error text in
  `errors[i]`. The tokens of a failed row are not a valid document.

Lower-level building blocks: `ConstraintLogitsProcessor(sessions,
prompt_len, eos_ids=(), eos_ids_per_row=None, on_constraint_error="raise")`
for use with `model.generate`, and `MaskGpuUnpacker`, which transfers
compact mask words to the device and expands them there.

### 3.7. spec-v1 serializer

```python
from bolorgir.serializer import serialize_value, serialize_json_text

payload, status = serialize_value(value, schema)        # parsed Python value
payload, status = serialize_json_text(text, schema)     # JSON text, lexemes kept
# status: SerializationStatus(ok: bool, reason: str, pointer: str)
```

Converts an existing JSON value into the `spec-v1` byte language (compact
form, schema-driven key order) without changing the value, for example to
feed reference documents to the engine. `payload` is UTF-8 `bytes`, or
`None` when the value cannot be represented, in which case `status.reason`
and `status.pointer` say why and where. `serialize_value` never parses a
`str` as JSON text: `"1"` stays the string `"1"`. `serialize_for_schema` is
a legacy entry point that dispatches on the argument type. The contract is
defined in [semantics-spec-v1.md sections 4-7](semantics-spec-v1.md#6-serializer-contract-for-benchmarks).
