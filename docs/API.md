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
  `blg_context_config` 56 and 88, `blg_stats` 208, `blg_compile_request` 32.
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
| `max_threads_per_state` | 128 | Parse branches per state (not CPU threads). Hard cap 128. |
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
```

**`blg_compile_request`:**

| Field | Meaning |
|---|---|
| `kind` | `BLG_CONSTRAINT_JSON_SCHEMA` or `BLG_CONSTRAINT_LITERAL_SET`. |
| `profile` | `"canonical-v1"` (also `NULL`), or `"spec-v1"`. Any other value is `UNSUPPORTED_FEATURE`. Literal sets use `NULL`. |
| `data`, `data_len` | The schema as UTF-8 JSON, or a JSON array of strings for a literal set. |
| `registry_data`, `registry_data_len` | Optional, `spec-v1` only: an immutable snapshot of external schemas, `{"version": "...", "documents": {"<uri>": <schema>, ...}}`. An external `$ref` whose base URI matches a `documents` key resolves inside that document; the engine never accesses the network. Without a registry every external `$ref` is `UNSUPPORTED_FEATURE`. |

Behavior:

- Compilation errors fill `err->json_pointer` and `err->schema_offset`.
  Unsupported input is always rejected at compile time; the engine never
  falls back to unconstrained generation.
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

### 2.5. Sessions

```c
blg_status blg_session_create(blg_context *ctx, blg_grammar *grammar,
                              blg_session **out_session, blg_error *err);
void       blg_session_destroy(blg_session *session);

blg_status blg_fill_mask(blg_session *session, uint32_t *mask,
                         size_t mask_words, blg_error *err);
blg_status blg_fill_masks_batch(blg_session *const *sessions,
                                uint32_t *const *masks, size_t mask_words_each,
                                int32_t *statuses, size_t count, blg_error *err);
blg_status blg_accept_token(blg_session *session, uint32_t token_id,
                            blg_error *err);
blg_status blg_can_end(const blg_session *session, bool *out_can_end);
blg_status blg_finish(blg_session *session, blg_error *err);
blg_status blg_abort(blg_session *session);
```

A session is **active** until `blg_finish` (finished) or `blg_abort`
(aborted). `blg_session_destroy` is valid in any state.

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
masks skipped by the cache admission policy, warm-up states and work
operations charged.

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
pip install bolorgir                    # core
pip install "bolorgir[transformers]"    # + Hugging Face adapter
```

The package imports without torch, numpy or transformers. The bridge is a
CPython extension built against the Limited API (abi3, CPython 3.10+); the
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
       precompute_max_states=0, tokenizer=None)
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
| `compile(schema, tokenizer=None, profile="canonical-v1") -> Constraint` | `schema` is a `dict`, JSON `str` or `bytes`; `profile` is `"canonical-v1"` or `"spec-v1"`. |
| `compile_literals(literals, tokenizer=None) -> Constraint` | A non-empty list of strings; the answer is exactly one of them. |
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
constraint.create_session() -> Session
constraint.close()

session.fill_mask() -> bytes                  # mask_words * 4 bytes, uint32 LE
session.allowed_token_ids(mask=None) -> list[int]
session.mask_numpy(mask=None) -> numpy.ndarray  # bool[vocab_size], needs numpy
session.accept_token(token_id)
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
                              max_new_tokens=128, do_sample=False, **gen_kwargs)
result.sequences     # torch.Tensor [batch, total_len]
result.completed     # list[bool]
result.stop_reason   # list[str]: "eos" | "length" | "dead_end" | "error"
result.sessions      # list[Session | None]
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

Lower-level building blocks: `ConstraintLogitsProcessor(sessions,
prompt_len, eos_ids=(), eos_ids_per_row=None)` for use with
`model.generate`, and `MaskGpuUnpacker`, which transfers compact mask words
to the device and expands them there.

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
