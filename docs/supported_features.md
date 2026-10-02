# supported_features.md - exact MVP support table

Document status: normative support table per spec FR-1 (§5), including the
schema acceptance rules of both profiles. "Rejected" means a normal compilation
error before the first forward pass; the engine never silently continues
unconstrained (spec FR-16).

## 1. JSON Schema profile (FR-1), canonical-v1 serialization profile

Input: UTF-8 JSON, Draft 2020-12 profile. Unknown validation keywords raise
`BLG_ERR_UNSUPPORTED_FEATURE` with a JSON Pointer in `blg_error.json_pointer`
and a byte offset in `blg_error.schema_offset`.

| Spec FR-1 element | MVP status | Behavior |
|---|---|---|
| `type` | Supported | Exactly one string: `object`, `array`, `string`, `integer`, `number`, `boolean`, `null`. Array of types - `UNSUPPORTED_FEATURE`. |
| `properties`, `required` | Supported | Both mandatory on every object node (`properties` may be `{}`, `required` may be `[]`). Names in `required` - only from `properties`, no duplicates, otherwise `INVALID_SCHEMA`. |
| `additionalProperties` | Supported | Explicit `false` is mandatory for `object`. Any other value/absence - `INVALID_SCHEMA`. |
| `items` | Supported | Mandatory for `array`; one supported schema for all elements. |
| `minItems`, `maxItems` | Supported | Non-negative integers, otherwise `INVALID_SCHEMA`; `min > max` - `UNSATISFIABLE_CONSTRAINT`. Missing `maxItems` - no semantic upper bound. |
| `minLength`, `maxLength` | Supported | Length of the decoded value in Unicode scalar values (see semantics.md §2.3); `min > max` - `UNSATISFIABLE_CONSTRAINT`. |
| `enum`, `const` | Supported | Scalar values; checked together with `type` and length; incompatible values are dropped, empty remainder - `UNSATISFIABLE_CONSTRAINT`. `enum` and `const` in the same schema - `INVALID_SCHEMA`. Numbers are compared exactly, without binary64; normalization - semantics.md §6 (scientific notation of non-integers with exponent is preserved, `-0` → `0`). Mixed enum without `type` - `UNSUPPORTED_FEATURE`. |
| `$defs`, `$ref` | Supported | Local references only: `#`, `#/$defs/<name>` (pointer with `~0`/`~1`). Acyclic graph: a cycle - `INVALID_SCHEMA` with pointer. Next to `$ref` only annotations are allowed; validation keywords next to it - `UNSUPPORTED_FEATURE`. |
| `$schema` | Supported | Draft 2020-12 identifier allowed (`https://json-schema.org/draft/2020-12/schema` with optional `#`); another dialect - `INVALID_SCHEMA`. |
| `title`, `description`, `$comment`, `examples`, `default` | Supported (annotations) | Do not affect the mask; `default` does not insert a value. |
| `anyOf`, `oneOf`, `allOf`, `not`, `if`/`then`/`else` | Outside MVP | `UNSUPPORTED_FEATURE` with pointer. |
| `minimum`, `maximum`, `multipleOf`, `exclusiveMinimum`, `exclusiveMaximum` | Outside MVP | `UNSUPPORTED_FEATURE` (not ignored). |
| `pattern`, `format`, `patternProperties` | Outside MVP | `UNSUPPORTED_FEATURE`. |
| `uniqueItems`, `contains`, `dependentSchemas`, `unevaluatedProperties` | Outside MVP | `UNSUPPORTED_FEATURE`. |
| `dependentRequired`, `propertyNames`, `minContains`, `maxContains`, `prefixItems`, `additionalItems`, `$id`, `$anchor`, `$dynamicRef`, `$recursiveRef`, `definitions`, `dependencies`, `contentEncoding`, `contentMediaType`, `contentSchema`, `readOnly`, `writeOnly`, `deprecated` | Outside MVP | `UNSUPPORTED_FEATURE`. |
| Any other unknown keyword | Outside MVP | `UNSUPPORTED_FEATURE`. |
| External references and network schema loading | Not supported | `UNSUPPORTED_FEATURE`. |
| Boolean schemas (`true`/`false` as a schema value) | Outside MVP | `UNSUPPORTED_FEATURE`. |

Additional acceptance rules:

- Every schema value - an object with a supported `type` or a pure local
  `$ref`.
- Duplicate keys in the schema JSON - `INVALID_SCHEMA`.
- Schema size > `schema_limit_bytes` - `RESOURCE_LIMIT`.
- Schema/expanded-grammar depth > `max_depth` - `RESOURCE_LIMIT`.
- Decimal enum/const literals with > 400 significant digits or |exponent|
  > 400 - `INVALID_SCHEMA` (semantics.md §6).

## 1a. JSON Schema profile spec-v1

Status: phases P1-P6 implemented (2026-09-22); the current oracle state is
given below. `spec-v1` is a separate profile selected at compile time; canonical-v1
(§1) is unchanged. Normative semantics: `docs/semantics-spec-v1.md`;
dialect x keyword rules: `docs/dialect-matrix.md` (draft-04, draft-06,
draft-07, 2019-09, 2020-12; absent `$schema` defaults to 2020-12; unknown
or custom metaschemas are refused). Unknown and extension keywords are
ignored (not errors); known-but-unimplemented keywords are refused with
`UNSUPPORTED_FEATURE` and a JSON pointer. Acceptance is measured by
the oracle harness (`tests/oracle/`, pinned Test Suite 23.2.0 + jsonschema
4.26.0): spec-v1 **1118 PASS / 99 SKIPPED / 1 KNOWN_GAP / 0 MISMATCH /
0 ENGINE_ERROR** after P6b (`tests/oracle/results/oracle-p6b-spec.json`);
canonical-v1 stays bit-for-bit frozen (95 PASS / 7 KNOWN_GAP,
`oracle-p6b-canonical.json`). **Current state (2026-09-30, strict
ADR-0005 D3):** 2020-12 spec-v1 **1069 PASS / 99 SKIPPED / 1 KNOWN_GAP /
0 MISMATCH / 49 ENGINE_ERROR**. The ENGINE_ERROR rows are
`RESOURCE_LIMIT` refusals: masks for states whose completion reachability
cannot be proven within the search budget fail instead of admitting an
unproven token (see "Mask and `accept`" below). They are pinned row by row
in `tests/oracle/engine_error_residual_spec_v1.json` (196 rows across the
five dialects); per-dialect counts are in docs/dialect-matrix.md section 9.
Oracle reports under `tests/oracle/results/` are local run outputs and are
not versioned; regenerate them with `tests/oracle/run_oracle.py`
(`tests/oracle/README.md`). Since the R6 work (2026-09-22) the harness
covers all five dialects of docs/dialect-matrix.md: the suite directory's
root `$schema` is injected for compilation, remotes of every dialect are
served from one registry snapshot, and per-dialect reports live in
`tests/oracle/results/oracle-r6-<draft>-spec.*` (draft4, draft6, draft7,
draft2019-09, draft2020-12); per-dialect PASS/SKIPPED/KNOWN_GAP counts and
the refusal classification are in docs/dialect-matrix.md section 9.

Corpus exact coverage (2026-09-22, release build `zig-out/lib/libbolorgir.so`,
`benchmarks/bench_jsb_coverage.py` with `BOLORGIR_COVERAGE_PROFILE=spec-v1`,
reports under `benchmarks/coverage/spec-v1/`; corpus hashes identical to the
canonical-v1 runs - repo `4c6c1d39…`, maskbench `9021735d…`):

| Corpus | Profile | compiled | invalid_schema | unsupported_feature | resource_limit | unsatisfiable |
|---|---|---:|---:|---:|---:|---:|
| repository snapshot (9,558) | canonical-v1 | 38 (0.4%) | 2,814 | 6,703 | 3 | n/a |
| repository snapshot (9,558) | spec-v1 | **9,251 (96.8%)** | 7 | 150 | 143 | 7 |
| MaskBench snapshot (11,306) | canonical-v1 | 624 (5.5%) | 2,993 | 7,688 | 1 | n/a |
| MaskBench snapshot (11,306) | spec-v1 | **10,794 (95.5%)** | 5 | 355 | 144 | 8 |

Both spec-v1 numbers clear the ROADMAP section-1 target of >= 95% exact
coverage; the remaining `unsupported_feature` files are the documented
refusals of the list below, each with a JSON pointer.

Implemented (P1): profile dispatch; tolerant reader and annotation
keywords; dialect normalization with preserved JSON pointers; extended
local `$ref` resolver (`#`, `#/$defs/...`, `#/definitions/...`, arbitrary
JSON pointers, `id`/`$id` scopes, `$anchor`);
AnyJSON and `true`/`false` schema values with the depth limit; open
objects (`additionalProperties` absent or `true`) with the dynamic key
set; primitive type unions (`type: [...]`, nullable forms, absent `type`);
structural `enum`/`const` of any JSON value with exact structural
equality (ROADMAP 4.2); absent `required`/`properties`.

Implemented (P2): `additionalProperties` as a schema; `patternProperties`
restricted to exactly one literal-substring pattern (no regex
metacharacters; several patterns and overlap with declared keys are
refused until P3); `propertyNames`; `minProperties`/
`maxProperties`; `dependencies` (draft-04..07), `dependentRequired`,
`dependentSchemas`; `prefixItems`, tuple `items`, `additionalItems`;
`contains` with `minContains`/`maxContains`; `uniqueItems` (candidate hash
plus exact structural equality).

Implemented (P3): `anyOf` (plain union), `oneOf`/`allOf`/`if`/`then`/`else`
(parallel branch parses, verdict at the confirmed value boundary), `not`
over the supported complement forms (`{type}`, `{enum}`/`{const}` over
scalars, `{required}`, nested `not`, De Morgan over `anyOf`/`allOf`,
`{type?:"object", properties}`).

Implemented (P4, `pattern`): the `pattern` keyword as an ECMA-262-subset
regex compiled to a DFA over Unicode scalar values (src/pattern.zig),
evaluated as an unanchored search over the decoded string value and
conjoined with the codepoint length bounds; `pattern` filters the string
values of `enum`/`const`; regex `patternProperties` (exactly one pattern;
several patterns and a declared-key overlap still need the intersection
and refuse). `format` stays an annotation (2020-12). Constructs outside
the regex subset refuse `UNSUPPORTED_FEATURE`, malformed regexes
`INVALID_SCHEMA`, both with the exact JSON pointer.

Implemented (P4, numbers): `minimum`/`maximum`/`exclusiveMinimum`/
`exclusiveMaximum` and `multipleOf` over `type: integer`/`type: number`,
in exact decimal arithmetic (never binary64; docs/semantics-spec-v1.md
§4.4). Comparison is by value (`1.0 = 1`, `5 >= 5.0`); the verdict is
delivered exactly at the confirmed end of the number token. Draft-04
boolean `exclusiveMinimum`/`exclusiveMaximum` are normalized to the
standalone numeric form (a `true` flag without its bound is dropped per
the dialect rules). The keywords filter numeric `enum`/`const` values and
conjoin with the type constraint; contradictory bounds are
`UNSATISFIABLE_CONSTRAINT` at the root, an empty arm in a union. A
`multipleOf` divisor with more than 10 significant decimal digits refuses
`UNSUPPORTED_FEATURE` with the pointer. Inside `not` these keywords stay
refused (complement of a numeric interval is outside the approved `not`
subset).

Implemented (P5, recursion; ADR-0008): recursive local `$ref` (`#`,
`#/...` pointers, plain-name fragments, all five dialects) by bounded
unrolling with a per-path budget of 8 (`REF_UNROLL_CAP`): with the
initial expansion a recursive target matches at most 1 + 8 nested
occurrences along any document path; deeper documents are outside the
byte language and the limit is mask-visible (the bottom position behaves
as `false`: a banned optional key, a dropped arm, a capped array).
Mutual recursion, recursion through anchors and combinators are covered.
Unproductive cycles (no base case) are the empty-language outcome
(`UNSATISFIABLE_CONSTRAINT` at compile). `$ref` fragments percent-decode
(`%XX`) before the `~0`/`~1` unescaping (RFC 6901 section 6). External
`$ref` resolves through the immutable registry snapshot (ADR-0006 D5,
ADR-0008); without a registry it stays refused.

Implemented (P6a, unevaluated*; ADR-0009): `unevaluatedProperties` and
`unevaluatedItems` (2019-09 and 2020-12 only) by compile-time scenario
synthesis: each acceptance hypothesis of the enclosing in-place
applicators (`allOf`, `anyOf` as every non-empty live subset, `oneOf`,
`if`/`then`/`else`, `dependentSchemas`, local `$ref`) carries the set of
keys/indices it evaluates, and the unevaluated* guard conjoins every
scenario. Scenarios with an identical eval set share one guard; shared
conjuncts hoist out of the disjunction in first-appearance order
(semantics-spec-v1 §4.3); open-object conjuncts of a scenario merge into
one object node. When the schema's behavior outside the guarded
container kinds is statically analyzable, the walk compiles masked to
the guard kinds and one top-level arm covers the outside. The inert
reductions apply (`unevaluated*: true`, a covering
`additionalProperties`/`items`).

Implemented (P6b; ADR-0010): `contentEncoding`, `contentMediaType` and
`contentSchema` are annotations in every dialect (2020-12 Validation
section 8) and compile away; RFC 3986 relative-URI resolution with an
in-document resource index - a `$ref`/`$dynamicRef` URI part resolves
against the enclosing base URI and jumps into an in-document `$id`
resource or the registry snapshot by the resolved absolute URI;
`$dynamicRef`/`$dynamicAnchor` (2020-12) with compile-time dynamic-scope
resolution (bookending requirement, outermost scope first) over the
ADR-0008 bounded unrolling; `$recursiveRef`/`$recursiveAnchor`
(2019-09); the 2019-09/2020-12 sibling rule - assertion/applicator
keywords next to a reference keyword conjoin with its expansion (an
`allOf` comb node) instead of refusing.

Correctness fixes after the 2026-09-22 review (CHANGELOG, items R1-R4;
regressions pinned in `tests/test_spec_v1_r2_r3.py`):

- `enum`/`const` values are filtered by every applicable sibling
  assertion and applicator - `properties`/`required`/
  `additionalProperties`, `patternProperties`, `propertyNames`,
  `minProperties`/`maxProperties`, `dependencies`/`dependentRequired`/
  `dependentSchemas`, `items`/`prefixItems`/`additionalItems`,
  `contains` (+`minContains`/`maxContains`), `uniqueItems`,
  `minItems`/`maxItems` - by exact compile-time evaluation of each
  constant value (a value that fails a sibling leaves the language; an
  empty remainder is `UNSATISFIABLE_CONSTRAINT`). A combination the
  static evaluation cannot decide (a `$ref` or a non-inert
  `unevaluated*` inside a sibling applicator) refuses
  `UNSUPPORTED_FEATURE` with a pointer instead of silently weakening the
  schema. `const` next to `enum` conjoins: the enum members equal to the
  const value survive (an empty intersection is `UNSATISFIABLE`).
- `oneOf`/`allOf` branches vote on values, not serializations (JSON
  Schema Core 4.2.2, 10.2.1): a const/enum value containing an object
  that more than one `oneOf` branch accepts is removed from the language
  at compile time (all-constant branch sets recompile exactly; the
  empty remainder is `UNSATISFIABLE_CONSTRAINT`; a duplicated value also
  accepted by a non-constant branch refuses `UNSUPPORTED_FEATURE`).
  Conjoined object schemas (`allOf`, the typed-core/combinator
  conjunction, the `$ref` sibling rule) whose declared-key orders
  contradict each other merge by value into one open object in
  first-appearance order (semantics-spec-v1 §4.3); a conflict involving
  a shape that cannot merge (a closed `additionalProperties: false`
  object, a pattern/propertyNames overlap) refuses
  `UNSUPPORTED_FEATURE` with a pointer.
- Mask and `accept` run the ADR-0005 residual-reachability filter for
  every grammar with deferred value verdicts (comb groups, pattern
  strings, bounded/excluded strings, value-constrained numbers), on any
  vocabulary, in both lazy and adaptive modes and with the fast path on
  or off (`src/complete.zig`): a token is admitted only when a
  completed answer stays reachable after it. States are first settled by
  an exact closed-form residual certification (achievable-value sets of
  the number machines, the length descriptor of the pattern DFA
  (`src/pattern.zig`), the grammar emptiness bitset for open-object
  required-key obligations, comb group doom rules including vote-linked
  `oneOf` duplicates and residual-equal `oneOf` branch pairs); undecided
  states fall back to a bounded iterative-deepening token search guided by
  completion-directed key hints. The search is bounded (depth tiers
  32/256/512 with a 256-state scratch pool charged to the context temp
  budget, per-tier expansion budgets, and a shared per-mask-call expansion
  budget `complete.FILL_SEARCH_BUDGET`). A state the search cannot settle
  within those bounds is UNKNOWN, not alive (ADR-0005 D3, strict form):
  the whole `fill_mask` fails and `blg_accept_token` refuses the token
  with `RESOURCE_LIMIT`, never silently admitting a token that may
  dead-end. A masked token is always an exact dead-end proof, an admitted
  token is provably reachable. No new compile-time refusal is introduced.

Refused with `UNSUPPORTED_FEATURE` and a pointer (phase in parentheses):

- External `$ref`/`$dynamicRef` whose resolved URI is in neither the
  in-document resource index nor the registry snapshot (P5/P6b);
  references to the 2020-12 metaschema URI (not in the snapshot).
- `$dynamicRef`/`$recursiveRef` next to unevaluated* (P6b: the scenario
  walk's dynamic scope is not the evaluation dynamic scope); a
  `$vocabulary` entry requiring an unimplemented vocabulary (core
  8.1.2).
- unevaluated* over a `oneOf`/`anyOf` branch that nests another
  combinator (directly or through in-place applicators and local `$ref`s);
  `unevaluatedItems` next to two or more conjunctive `contains`; an
  external `$ref` next to unevaluated*; more than 5 live `anyOf`
  branches, more than 32 scenarios, more than 4 `dependentSchemas`
  entries or more than 8 `patternProperties` patterns feeding one guard;
  conditional evaluation under a nested unevaluated* subschema (P6a,
  ADR-0009).
- `not` operand forms outside the supported complement list (P3);
  several `patternProperties` patterns and a pattern overlapping a
  declared key (intersection, P3+).
- `enum`/`const` next to a sibling applicator the compile-time value
  filter cannot decide exactly (a `$ref` or a non-inert unevaluated*
  inside it).
- `oneOf` where an object-valued `enum`/`const` member is also accepted
  by a non-constant branch (the overlap cannot be subtracted exactly);
  conjoined object schemas (`allOf`, the typed-core/`$ref`-sibling
  conjunction) with contradictory declared-key orders involving a shape
  that cannot merge by value, e.g. a closed object).

The remaining 99 SKIPPED oracle rows (of 1218;
`tests/oracle/results/oracle-p6b-spec.json`) are all documented refusals
with a JSON pointer and decompose as: `UNSATISFIABLE_CONSTRAINT` (15) and
`INVALID_SCHEMA` (8) by design - the suite's unsatisfiable and invalid
schemas; references to the draft/2020-12 metaschema URI, which is not in
the registry snapshot (21); `patternProperties` shapes needing the regex
intersection - several patterns or a declared-key overlap (23);
unevaluated* budget guards of ADR-0009 (23); `$dynamicRef`/external
`$ref` next to unevaluated* (2, ADR-0010 D5); `not` over an `anyOf` form
outside the complement subset (2); `$vocabulary` entries requiring a
custom metaschema vocabulary (5).

## 2. Serialization profiles

| Profile | Status |
|---|---|
| `canonical-v1` | The default (`profile = NULL` == canonical-v1). Normative semantics - docs/semantics.md; support table §1. |
| `spec-v1` | Implemented (P1-P6b, 2026-09-22); support table §1a. |
| Other `profile` values | `UNSUPPORTED_FEATURE`. |

## 3. Constraint formats (blg_constraint_kind)

| Format | Status |
|---|---|
| `BLG_CONSTRAINT_JSON_SCHEMA` | Supported (§1 table). |
| `BLG_CONSTRAINT_LITERAL_SET` | Supported (FR-3): non-empty JSON array of UTF-8 strings; common prefixes, empty string, Unicode; duplicates are removed. Not mixed with regex. |
| Arbitrary CFG/EBNF, user regex | Outside MVP (plan 1.1) | `UNSUPPORTED_FEATURE`. |

## 4. Computation modes (blg_mode)

| Mode | Status | Semantics |
|---|---|---|
| `BLG_MODE_LAZY` | Supported | Mask computed from the current state, no cache. The reference exact path. |
| `BLG_MODE_ADAPTIVE` | Supported | The same exact algorithm + an LRU mask cache with a hard byte budget and an admission policy: a computed mask is cached only if the state was seen >= `adaptive_min_hits` times (default 2) OR computing it took >= `adaptive_min_cost_ns` ns (default 50000); cheap one-off masks do not enter the cache (counted in `blg_stats.cache_adaptive_skips`). Masks match lazy bit-for-bit (parity - spec T2). |
| `BLG_MODE_PRECOMPUTE` | Experimental | Adaptive + warm-up at `blg_compile`: BFS over reachable parser states, computing and caching masks, at most `precompute_max_states` states (default 4096; counted in `blg_stats.precompute_states`). Exhausting the state/memory/work budget is not an error: the warm-up stops, generation continues lazily; only flag cancellation (`BLG_ERR_CANCELLED`) propagates out of the warm-up. The warm-up runs only with the cache enabled (`cache_limit_bytes != 0`). |

Disabling the cache (`cache_limit_bytes = 0` in adaptive) changes performance,
but not the language and not the masks.

## 5. Platforms and tokenizers

| Item | MVP status |
|---|---|
| Platform | Linux x86_64 (glibc). Other platforms - after separate checks; the design has no inherent x86 dependency. |
| Python | CPython 3.10+, regular GIL; extension via the Limited API (abi3, target `Py_LIMITED_API=0x030A0000`). |
| Tokenizers | Two families: byte-level BPE (GPT-2/Qwen/Llama-3 style) and SentencePiece with byte fallback. Byte images of tokens are determined by the ACTUAL backend decoder (`tokenizers.__getstate__`): `<0xNN>` - one byte only when ByteFallback is present in the decoder chain (and for added tokens too); in byte-level BPE it is literal text, as in HF decode. Added tokens pass through the same decoder as regular tokens (`Ġ/Ċ/▁` → space/newline); a fallback on a non-mappable character is at the level of the WHOLE token: a mixed `Ġhello🙂` stays UTF-8 as is, including `Ġ`); tests compare the full `hf.decode(sequence)` with the byte model. Only exact decoder sequences are supported, honoring order and multiplicity (ByteLevel; SP: Replace(▁→' ') [ByteFallback] [Fuse] Strip(' ', start=1, stop=0)); two Strips, Strip before Fuse, `Strip` without `Fuse` (stripping a space from EACH token), Metaspace and other chains - `UNSUPPORTED_TOKENIZER`, no approximations. The per-process `TokenizerBundle.from_hf` cache fingerprint includes the decoder configuration and added tokens, so a decoder change does not reuse an old bundle. Strip(' ', start=1) is modeled by the core (flags = `BLG_TOKENIZER_STRIP_LEAD_SPACE`): the language L is compiled as {t∈L without a leading space} ∪ {' '+t}, and the accepted byte stream matches the decoded text exactly. |
| Tokenizer coverage (coverage gate) | Spec 3.1: a token is allowed only if a finishing continuation of tokens exists after it. With a byte-complete vocabulary (all 256 single-byte tokens - GPT-2/Llama byte fallback) the mask is exact without extra checks. For finite literal languages (literals/enum/const/objects of them) with an incomplete vocabulary, masks are built with an exact reachability check `src/complete.zig` (state search with memoization, terminating on a finite language): a token leading to a dead end is not allowed, and `accept` of such a token - `INVALID_TOKEN` without changing state (FR-7). For other combinations (infinite language + incomplete vocabulary: `string`/`integer`/arrays etc.) termination is not proven, so such a pair is rejected at compile time (`BLG_ERR_UNSUPPORTED_TOKENIZER` with an explicit explanation); an uncoverable constraint - the same error code within the same step. Use a byte-complete vocabulary or a finite literal constraint. |
| Mask construction | By walking the token prefix tree (trie in `Tokenizer`), not by enumerating the vocabulary; EOS/special/empty tokens do not enter the trie (EOS are allowed via `can_end`, special non-EOS are forbidden). |
| Service tokens | BOS/PAD and other special non-EOS are forbidden inside an active document (`INVALID_TOKEN`). A regular token with an empty representation - `UNSUPPORTED_TOKENIZER` at preparation. EOS is handled per semantics.md §8. |

## 6. Default limits (FR-10, blg_context_config)

All limits are public and configurable. For every field except
`cache_limit_bytes`, the value 0 means the default. Special case -
`cache_limit_bytes`: 0 disables the cache entirely (masks are computed as in
lazy), while the 64 MiB default is requested by the explicit constant
`BLG_CACHE_DEFAULT` (`UINT64_MAX`). The cache budget is hard: actually accounted
bytes of the cache category (entries, states, masks, the HashMap table and
allocator overhead) never exceed it; when full, work continues via lazy mask
computation. The immutable compile-artifact cache (ADR-0004) gets a quarter of
this budget and is counted by actually retained bytes (grammar, handle, schema
bytes copy, capacity): a repeated `blg_compile` of the same
schema bytes and profile in one context returns a new reference to the already
built grammar (after content comparison), eviction (FIFO) frees outdated
entries and drops only the cache reference - live sessions and user handles are
unaffected. `blg_context_reset_cache` releases the whole cache without
destroying live handles/sessions. With `cache_limit_bytes=0` and in lazy mode
the artifact cache is off. An incompatible combination
(`cache_limit > memory_limit`) - `INVALID_ARGUMENT` at context creation. If the
budget is insufficient for the tokenizer, the context is not created.

| Limit | Default value |
|---|---|
| `memory_limit_bytes` - total core budget | 256 MiB |
| `cache_limit_bytes` - hard LRU cache budget | 0 = disabled; `BLG_CACHE_DEFAULT` = 64 MiB |
| `session_limit_bytes` - state and working data of one session | 8 MiB |
| `schema_limit_bytes` - input schema | 1 MiB |
| `max_depth` - structural depth | 64 |
| `max_threads_per_state` - parse branches in a state | 128 |
| `work_limit_ops` - work budget per `blg_compile`/`blg_fill_mask(s)` call in abstract operations | 0 = no limit |
| `adaptive_min_hits` - admission: minimum state hits | 2 |
| `adaptive_min_cost_ns` - admission: minimum mask computation cost, ns | 50000 |
| `precompute_max_states` - cap of warm-up states at compile | 4096 |

Reaching the total limit - `RESOURCE_LIMIT`; the schema is not simplified, the
mask is not weakened. Exceeding `max_threads_per_state` - `RESOURCE_LIMIT` (not
silent branch dropping: completeness wins).

Cancellation and work limit: `blg_cancel_flag_set(ctx, flag)` registers the
caller's atomic byte. The flag is checked at the entry of every
`blg_compile`/`blg_fill_mask(s)` call - even when the mask would be served from
the cache); a non-zero value - `BLG_ERR_CANCELLED`. From Python:
`Engine` accepts `work_limit_ops`, `adaptive_min_hits`, `adaptive_min_cost_ns`,
`precompute_max_states`, and cancellation is done via `CancelToken`
(`engine.cancel_token()` / `engine.register_cancel_token(token)`).
Exceeding `work_limit_ops` in compile/fill_mask - `BLG_ERR_RESOURCE_LIMIT`
(a partial mask is not returned). The precompute warm-up has its own work
accounting: it does not spend the call's `work_limit_ops`; its
bounds are `precompute_max_states`, temporary memory and the cancel flag (only
cancellation propagates out as `CANCELLED`).

`max_depth` and `max_threads_per_state` have hard implementation caps
(`MAX_DEPTH_CAP = 64`, `MAX_THREADS_CAP = 128`, src/parser.zig): limits can only
be lowered; a value above the cap is rejected with `INVALID_ARGUMENT` at context
creation. Wide alternations (`enum`, `oneOf`) no longer multiply parser threads:
choice alternatives advance lazily through a single choice frame and only the
alternatives matching the next byte fork a thread (src/parser.zig), so, for
example, Github_trivial/o48280 (enum of 255 strings) compiles, creates a
session and accepts a valid document under the default budgets.

## 7. Error codes (include/bolorgir.h)

| Code | Name | Typical causes |
|---|---|---|
| 0 | `BLG_OK` | Success. |
| 1 | `BLG_ERR_INVALID_ARGUMENT` | Invalid struct_size/version, cache_limit > memory_limit, null pointers, misaligned mask buffer. |
| 2 | `BLG_ERR_INVALID_SCHEMA` | Schema acceptance rule violation (§1): duplicate keys, `$ref` cycle, foreign `$schema`, non-integer min/max. |
| 3 | `BLG_ERR_UNSUPPORTED_FEATURE` | Keyword/construct outside the MVP; unknown profile. |
| 4 | `BLG_ERR_UNSATISFIABLE_CONSTRAINT` | min > max; enum empty after filtering. |
| 5 | `BLG_ERR_UNSUPPORTED_TOKENIZER` | Empty representation of a regular token; uncovered ids; uncertified decoding scheme; constraint not covered by the tokenizer (coverage gate at compile, §5). |
| 6 | `BLG_ERR_INVALID_TOKEN` | Token outside the vocab; forbidden token at accept; special non-EOS in the document. State unchanged. |
| 7 | `BLG_ERR_DEAD_END` | No token and no EOS is allowed by the state; mask invalid. |
| 8 | `BLG_ERR_RESOURCE_LIMIT` | Memory/depth/threads/schema-size/`work_limit_ops` limits exceeded; allocator failure. |
| 9 | `BLG_ERR_CANCELLED` | Cancellation via the `blg_cancel_flag_set` flag during compile/fill_mask(s), the coverage check or the precompute warm-up. |
| 10 | `BLG_ERR_BUSY` | Normal context destruction with live grammars/sessions. |
| 11 | `BLG_ERR_WRONG_STATE` | accept/fill_mask on a finished/aborted session; finish without can_end. |
| 12 | `BLG_ERR_BUFFER_TOO_SMALL` | Mask buffer smaller than `ceil(vocab_size/32)` words. |
| 13 | `BLG_ERR_INTERNAL` | Unexpected internal error; the session is marked aborted. |

Diagnostics are passed through the caller's `blg_error` (code, schema_offset,
message UTF-8 up to 256 bytes, json_pointer - RFC 6901 JSON Pointer to the
schema node on compilation errors, "" when not applicable); there is no global
last_error. No error crosses the ABI as a Zig error union or panic.

## 8. Measured support on JSONSchemaBench

A run over the full JSONSchemaBench corpus (repo guidance-ai/jsonschemabench,
commit `ba103c73756198dd9b149ddc7db7867da7a077f6`, 9558 schemas, 10 datasets;
pinned in benchmarks/manifest.json) was performed on 2026-09-14 with engine
0.1.0 (adaptive mode, canonical-v1 profile, gpt2 tokenizer rev 607a30d7).
Results - benchmarks/results/20260914T213241/ (support_report.json -
per-schema list, support_summary.md - summary).

| Compilation outcome | Schemas | Share |
|---|---:|---:|
| Compiled | 38 | 0.40% |
| `UNSUPPORTED_FEATURE` | 6703 | 70.13% |
| `INVALID_SCHEMA` | 2814 | 29.44% |
| `RESOURCE_LIMIT` | 3 | 0.03% |

The measured outcomes match the §1 table: the dominant rejection causes are
the object-node acceptance rules (`additionalProperties: false` mandatory,
`properties`/`required` mandatory) and keywords outside the MVP (`definitions`,
`$id`/draft-4 `id`, `oneOf`/`anyOf`/`allOf`, `patternProperties` etc.).
The 3 `RESOURCE_LIMIT` - schemas larger than `schema_limit_bytes` = 1 MiB.
No behavioral deviations from the §1 table were found on the corpus; the only
documented subtlety - the interaction of enum > 64 alternatives with the
`max_threads_per_state` cap (see §6 above).

Mask parity with xgrammar 0.2.6 and llguidance 1.8.0 on the language
intersection (52 schemas: 37 working of the 38 compiled + 15 control corpus;
13 740 prefixes, gpt2) - benchmarks/results/20260914T213241/parity_summary.md.
No mask deviations of our engine from its own canonical-v1 semantics
(oracle tests/reference.py) were found; all deviations from the competitors are
classified as profile differences or as limitations/defects on the
competitors' side.

## 9. Operational notes and known deviations

**Context memory retention across generation - fixed 2026-09-27.** Mask
and generation work used to retain memory inside a Context that
`blg_session_destroy` and `blg_grammar_release` did not reclaim, growing
with the number of generation steps; a long-lived process generating
documents for many schemas in one Context could eventually hit
`BLG_ERR_RESOURCE_LIMIT` on a later `blg_compile`/`blg_session_create`
(observed after ~500 heavy spec-v1 schemas under the default 256 MiB
limit). The cause was a leaked key copy in the completion memo
(`memoPut` in `src/complete.zig`, about 4.4 KB per `fill_mask` cycle,
charged to the context TEMP budget); it was fixed in the fix2 build, after
which the per-cycle TEMP accounting is flat (CHANGELOG, docs/RUN_HISTORY.md).
The semantic coverage harness still recreates its Context every 150
schemas, which remains a cheap safeguard for batch workloads.

**Crash on long runs - fixed 2026-09-27.** On very long host processes
(~12k schema compilations/generations) the engine previously died with
`panic: reached unreachable code` inside `blg_session_destroy`. Root
cause: the root allocator was `page_allocator`, which mmaps every
allocation separately; the process VMA count reached the
`vm.max_map_count` limit (65530), after which a `munmap` splitting a
merged VMA failed with ENOMEM and `std.posix.munmap` panicked on its
`unreachable` branch. The root allocator is now `smp_allocator`
(`src/c_api.zig` `root_allocator`), which packs small allocations into
per-thread slabs - O(slabs) VMAs instead of O(allocations). Verified by
re-running the previously crashing full-corpus coverage scan to
completion. Note the crash was an allocator-level resource exhaustion,
not a schema-dependent bug; no schema semantics changed.

**draft-04 integer spelling vs the jsonschema oracle.** The engine
applies the spec-v1 value-based integer semantics uniformly across
dialects (semantics-spec-v1, number keywords: `1.0`, `1e2`, `100.00`
are integers), while `jsonschema.Draft4Validator` applies the draft-04
lexical rule (no fraction/exponent part). For schemas that declare
`"$schema": "http://json-schema.org/draft-04/schema#"`, an instance
spelled as an integral float is therefore accepted by the engine and
rejected by the draft-04 oracle. This is the same family as the
whitelisted `ref.json` sibling-`id` deviation (uniform new-dialect
behavior, docs/dialect-matrix.md §9 "Still open"): the pinned Test
Suite does not cover integral-float spellings for draft-04 `integer`,
so the oracle gate stays green, but the semantic coverage harness
(jsonschema cross-check) reports the divergence - 22 of the
JSONSchemaBench repo-snapshot schemas in the 2026-09-26 measurement
(all `generated_schema_invalid` cases are this single shape; no other
error types were found among them).
