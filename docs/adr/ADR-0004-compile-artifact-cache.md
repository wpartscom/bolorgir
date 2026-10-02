# ADR-0004: cache of immutable compile artifacts

Status: accepted (2026-09-18).
Context: warm compile remained ~28× slower than xgrammar (59.6 µs versus
2.14 µs, perf-fix4) and was a §10.6 blocker; profiling produced a measurable breakdown
(`dict→bytes` ≈7.1 µs, `compile(dict)` ≈57.4 µs, `compile(bytes)` ≈51.0 µs)
and cache requirements: a key of immutable data, content verification, memory
accounting, survival of eviction across live sessions, operation under cache=0 and cancellation.

## Decision 1: the compile artifact is cached inside the context

- Key: kind + normalized profile (null / canonical-v1) + exact schema
  bytes; tokenizer/decoder/max_depth/strip are pinned to the context, so
  they are not part of the key. The hash (Wyhash) is only an index: on a match the bytes
  are compared byte-by-byte (`std.mem.eql`); a collision cannot return a foreign
  grammar.
- A metadata dict cache by `id()` is forbidden (mutable input) - a repeated
  `json.dumps` remains an honest cost of the caller; the declared
  speedup scenario is `compile` of the same immutable bytes/profile.
- The grammar is returned as the same `*GrammarHandle` with +1 to refcount: sessions and
  user handles live independently, eviction drops only the cache reference
  (live sessions survive eviction - test in `c_api.zig`).
- Budget (refined in Decision 5): a quarter of
  `cache_limit_bytes`, counted by ACTUALLY retained bytes - the whole
  grammar (GrammarMeter over the arena), `GrammarHandle`, the schema bytes
  copy, accounting headers and the record-list capacity; eviction (FIFO)
  frees outdated artifacts before insertion and does not touch live references
  of the user/sessions. A record that does not fit the budget is simply not cached.
  `cache_limit_bytes=0` and the `lazy` mode disable the cache (pre-ADR behavior
  is preserved).
- Contracts: cancellation is checked before the cache lookup (charge(0)); an interrupted or
  failed compilation (including the precompute warm-up) leaves no record;
  `blg_context_destroy` drops the cache references itself and returns BUSY while
  EXTERNAL references are live (user handles, sessions, in-flight calls:
  the `external_grammar_refs`/`active_calls` counters), see Decision 5.
- Measurement after the change (big_enum_64, GPT-2, medians of 1000 calls, 10
  warm-ups): `dumps` 7.38 µs, `compile(dict)` 8.35 µs, `compile(bytes)`
  1.07 µs (was 51.0 µs; xgrammar reference ≈2.35 µs).

## Decision 2: unproven schema/tokenizer pairs are rejected (refinement of ADR-0003)

The completion filter is exact only with a byte-complete vocabulary or a finite
literal language. For other combinations (example: `{"type":"string"}`
with the vocabulary `['""','"a',EOS]`; an array of enum) `blg_compile` returns
`unsupported_tokenizer` with an explanation - before generation, rather than "DeadEndError"
after an accepted dead end. Extending the exact analysis to bounded repeat -
a separate task.

## Decision 3: token bytes follow the whole decoder (refinement of ADR-0003)

Added tokens go through the same decoder: ByteLevel maps `Ġ/Ċ/▁`
to space/newline, `<0xNN>` with ByteFallback - one byte (empirics of
`tokenizers`). Fallback on a non-mappable character is at the level of the WHOLE token
(`byte_level.rs`: `try_fold(...).unwrap_or_else(t.as_bytes())`): a mixed
added token `Ġhello🙂` stays UTF-8 as is, including `Ġ`, rather than
character by character. Decoder chains are validated as an exact
sequence with order and multiplicity; two `Strip`s, `Strip` before `Fuse`,
trailing components and **`Strip` without `Fuse`** (strips spaces per
token, not a single leading space of the stream) - UnsupportedTokenizerError.
Tests compare the full `hf.decode(sequence)` against the byte model.

## Decision 4: a single final GenerationConfig for checking and running

`constrained_generate` builds a deep copy of the effective config (the passed
`generation_config` replaces the model one, kwargs win, an explicit None removes),
filters EOS both in the processor and in the config (otherwise HF would cut the document at an excluded id),
preserves explicit `num_beams/num_return_sequences`, and
row terminators are checked against the core per row (`eos_ids_per_row`).

## Decision 5: external references, exact budget, reset

- Ownership: `live_grammars == cache.refs` did not distinguish "cache + user"
  (one object, two refcounts) - destroy freed the context with a live handle
  (SIGSEGV on a subsequent release). Now the context counts
  `external_grammar_refs` (one ref per successful `blg_compile` not
  released by `blg_grammar_release`), plus `live_sessions` and `active_calls`
  (compilation/reset in flight). Cache references are not external: destroy drops them
  itself. Concurrent destroy is safe for calls already entered (BUSY), and
  serialization with new calls is ensured by the caller (contract in
  the header).
- Artifact budget: the metadata estimate (`Entry + schema_bytes`) undercounted
  the grammar itself and service structures - with artifact_limit=16 KiB the cache
  retained 255 KiB of grammars and the next request got RESOURCE_LIMIT.
  Now the cost is actual bytes (GrammarMeter + handle + copy +
  capacity), eviction before insertion, what does not fit is not cached.
- `blg_context_reset_cache(ctx)` - a public reset of the artifact cache without
  destroying live handles/sessions: memory returns to accounting,
  subsequent compilations miss. Needed for long-lived contexts
  under memory pressure and for the verifiable memory-return invariant
  (campaign C fuzzer).

## Consequences

- Recompiling the same bytes becomes ~1 µs and no longer blocks
  §10.6 on warm compile; the price is retained artifacts (≤¼ of the cache budget,
  by actual bytes), freed on context reset or
  `blg_context_reset_cache`.
- The control run is to be repeated (the preceding fix round changed the adapter and the core);
  the perf-fix4 verdict remains a historical NO-GO.
