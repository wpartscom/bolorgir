# ADR-0003: completion reachability in masks and decoder modeling

Status: accepted (2026-09-17).
Context: spec §3.1 ("a token is allowed if a finite
continuation ending in a completed response exists after it"), FR-6 (decoder
effects), FR-7 (accepting a forbidden token), NFR-2.

## Decision 1: the mask proves completion reachability

- **Byte-complete vocabulary** (for each of the 256 bytes there is a regular
  single-byte token) - the mask stays as before (feedBytes success), because
  any byte continuation of a live state is served by single-byte
  tokens. This is the production-vocabulary path (GPT-2/Qwen, Llama byte fallback).
- **Finite literal language + incomplete vocabulary** - the completion filter
  engages (`src/complete.zig`): a token is allowed only if a state with
  `can_end` is reachable after it via tokens. The state search with memoization
  terminates: states are finite, each token consumes >= 1 byte. Under
  `accept` the same filter applies: a dead-end token - `INVALID_TOKEN` without
  changing state (FR-7).
- **Open classes + incomplete vocabulary** - the conservative compile-time
  segmentability check is kept (as it was); the limitation is recorded in
  `docs/supported_features.md`. Exact reachability for open classes
  requires a different analysis (post-MVP task).

Rejected alternatives: rejecting a schema for any incomplete vocabulary
(breaks legitimate partial vocabularies and the `examples/c_client.c` example);
the heuristic "a byte prefix is valid" (does not ensure §3.1 - example
`[ab, a, EOS]` + literal `ab`).

## Decision 2: byte images of tokens are determined by the decoder

- Bytes are taken from the actual HF decoder (`tokenizers.__getstate__`), not
  from the text form: `<0xNN>` - one byte only with ByteFallback in the chain; in
  byte-level BPE it is literal text. Added tokens - literal content.
- Only confirmed chains are supported (ByteLevel; SP
  Replace(▁→' ') [+ ByteFallback] [+ Fuse] [+ Strip(' ',1,0)]); anything else -
  `UNSUPPORTED_TOKENIZER` (no approximations, FR-6).
- `Strip(' ', start=1, stop=0)` is modeled by the core via a tokenizer flag
  (`BLG_TOKENIZER_STRIP_LEAD_SPACE`): the language L is compiled into
  {t∈L without a leading space} ∪ {" "+t}, so the accepted byte stream
  matches the decoded text (the literal `" hello"` is no longer
  confirmed by the token `▁hello`, which decodes to `"hello"`).
- The decoder configuration is part of the bundle-cache fingerprint and of the
  tokenizer identity: a decoder change does not reuse old data.

## Consequences

- Masks may differ from "byte-legal" only on vocabularies without
  full coverage and only by excluding dead ends - T2 parities
  are checked on byte-complete vocabularies, and separate regression tests were
  added for partial ones (`tests/test_edge_cases.py`, `src/c_api.zig`, `src/mask.zig`).
- The independent oracle (`tests/reference.py`) implements the same §3.1 rule;
  vocabularies with competing segmentations (`[ab, a]`) were added to the self-check.
- Cost: on incomplete vocabularies the mask performs a reachability search
  (memoized per session); on byte-complete ones - the price is unchanged.
