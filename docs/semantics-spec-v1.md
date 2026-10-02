# semantics-spec-v1.md - normative semantics of the spec-v1 profile

Status: **normative document**, P1 input (ROADMAP section 5, P1 input 1; A6).
Fixes the value domain, value equality and the serialization policy (the byte
language) of the `spec-v1` profile. Any divergence of the implementation from
this file is an implementation error; a semantics change is made by editing
this file before changing code.

Scope: this file is self-contained for `spec-v1`. The `canonical-v1` profile
stays governed by docs/semantics.md and is frozen; nothing here changes any
canonical-v1 behavior. Where spec-v1 reuses a canonical-v1 rule unchanged,
the rule is restated here, not referenced.

Decisions recorded in this file (each marked **Decision**): the serialization
policy (§4), the property order of open and merged objects (§4.3), the number
sublanguage (§4.4), and the serializer contract for benchmarks (§6).

## 1. Value domain and type relations

A value is a JSON value: object, array, string, number, `true`, `false`,
`null`.

- Numbers are exact decimals: a schema or document numeric lexeme is parsed
  into a rational number (mantissa + decimal exponent), never through
  binary64. The representability limit is the canonical-v1 one: at most 400
  significant digits and |written exponent| <= 400; overflow in a schema -
  InvalidSchema.
- Type relations are semantic, not syntactic (ROADMAP principle 4):
  `integer` is a subset of `number`. A number with a zero fractional part is
  an integer regardless of spelling: `1.0`, `1e2`, `100.00` are integers
  (JSON Schema validation 6.1.1). Disjointness and exclusivity proofs
  (`oneOf`, `not`) must use these relations: `{"type":"integer"}` and
  `{"type":"number"}` are not disjoint.
- The six remaining types are pairwise disjoint and disjoint from numbers:
  `true != 1`, `false != 0`, `"1" != 1`, `null` equals only `null`.

## 2. Value equality (const / enum / uniqueItems)

Equality is exact structural equality on the value domain of §1 (JSON Schema
core 4.2.2):

- numbers: equal iff equal as rational numbers (`1` = `1.0` = `1e0`);
- strings: equal iff the same sequence of Unicode scalar values;
- arrays: equal iff same length and pairwise equal elements in order;
- objects: equal iff the same key set and pairwise equal values; **key order
  is insignificant**: `{"a":1,"b":2}` = `{"b":2,"a":1}`;
- values of different types are never equal (`true != 1`, `"a" != ["a"]`).

Consequences:

- `[1, 1.0]` contains a duplicate: it violates `uniqueItems`, and as an
  `enum` it collapses to one value.
- `enum`/`const` matching is by value, not by lexeme: an instance `1.0`
  matches `{"const": 1}`.
- A set hash is used only to find candidates; the decision is always the
  exact structural comparison above (ROADMAP 4.2, item 6). A hash collision
  never decides equality.
- Duplicate values inside an `enum` are removed after normalization; the
  equality relation is the one above.
- The numeric range keywords (`minimum`/`maximum`/`exclusiveMinimum`/
  `exclusiveMaximum`) and `multipleOf` (P4) use the same exact decimal
  arithmetic, never binary64 (A6). Range comparison is by value
  (`1.0 = 1`, `5 >= 5.0`). `multipleOf` is exact for integer and decimal
  divisors; a divisor with more than 10 significant digits is refused with
  UnsupportedFeature and a pointer, never approximated.

## 3. Normalization invariants

- Dialect normalization (docs/dialect-matrix.md) rewrites schema forms, never
  values: it must not repair an invalid schema into a valid one. A schema
  invalid under its dialect stays `INVALID_SCHEMA` with the original JSON
  pointer; normalization may only translate, with the original pointers
  preserved (including `definitions`).
- Instance values are never normalized either: normalization applies to
  schemas at compile time. The engine never rewrites a document byte to make
  it valid.

## 4. Serialization policy (the byte language)

**Decision.** The spec-v1 byte language is a restricted serialization, as
permitted by ROADMAP P1 input 1 ("a canonical serialization that preserves
the value set"; accepting arbitrary JSON texts would widen the states and
the cold path and is rejected). Completeness and benchmarks are evaluated
relative to the language described here; this is not support for arbitrary
JSON texts.

### 4.1. Whitespace

Whitespace outside strings is absent entirely: no spaces, tabs or newlines
between structural tokens. Identical to canonical-v1.

### 4.2. Strings and escapes

The escape table and the UTF-8 rules are the canonical-v1 ones
(docs/semantics.md §2-§3), restated in brief: raw bytes >= 0x20 except `"`
and `\`; escapes exactly `\"` `\\` `\b` `\f` `\n` `\r` `\t`, other control
characters as `\u00xx` with two lowercase hex digits; `\/` and any `\uXXXX`
for codes >= 0x20 are forbidden; non-ASCII is written raw in UTF-8.

### 4.3. Objects: key order

**Decision.**

- Declared properties (in `properties`) follow the schema declaration order
  among themselves; optional keys may be skipped; a skipped key cannot be
  returned; a key occurs at most once.
- Open objects (an undeclared key set, `additionalProperties` absent or
  `true`): undeclared keys may appear in any order, interleaved freely with
  declared keys, each at most once. Rationale: undeclared keys are data,
  their order is not recoverable by the engine, and restricting it would
  reject instances valid under every supported dialect. Required declared
  keys can still always be emitted; where a deferred value verdict could
  nevertheless let a live prefix dead-end, mask and accept enforce
  dead-end-freedom explicitly via the ADR-0005 R1 residual-reachability
  filter (§4.4), superseding the purely constructive argument of
  docs/semantics.md §10 for this profile.
- Merged objects (an approved `allOf` equivalence merge, P3): the effective
  declaration order is the order of first appearance across the merged
  subschemas, taken in schema document order.

### 4.4. Numbers

**Decision.** The number sublanguage is value-based. Any JSON-valid number
spelling (grammar: `-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?`) whose
value satisfies the type is accepted: for `type: integer` the value must have
a zero fractional part (`1.0`, `1e2` accepted; `1.5` rejected); for
`type: number` any value is accepted. Lazy number completion and the
forbidden forms (`NaN`, `Infinity`, leading zeros, `+1`, `.5`, `1.`) are as
in canonical-v1. Rationale: the type system of §1 is value-based, and
benchmark instances carry data-determined spellings (`1.0`); refusing them
would be a silent weakening of the dialect semantics. The widening is
bounded: the number automaton already exists for `type: number`.

Schema-side enum/const literal generation keeps the canonical-v1 exact
decimal normalization of docs/semantics.md §6 (the lexeme of the schema is
normalized; `-0` becomes `0`), but matching at parse time is by value per
§2, so instance spellings equal in value match the literal.

Range keywords and `multipleOf` (P4) are value-based assertions over the
same sublanguage:

- Applicability: like the reference dialects, these keywords ignore
  non-number instances; they conjoin with `type: integer`/`type: number`
  (or with a numeric `enum`/`const`, whose values they filter exactly).
- Bounds comparison is exact decimal: the instance value is compared
  against the bound as rational numbers, aligned by order of magnitude
  (`09` is not a valid spelling; `5` and `5.0` compare equal; an exclusive
  bound rejects the equal value only). The verdict is delivered exactly at
  the confirmed end of the number token; because it is deferred that way, a
  live prefix could dead-end (e.g. `-` under `minimum: 1`), so mask and
  accept run the ADR-0005 R1 residual-reachability filter
  (src/complete.zig): the exact achievable-value set of the machine state
  is certified in closed form, and a token is admitted only when a
  completing continuation exists. States the closed form cannot decide go
  to a budgeted search; a state the search cannot resolve within its
  budget is undecided, never guessed (budget semantics of §4.5, strict
  form): the mask or accept call fails with RESOURCE_LIMIT.
- `multipleOf` is decided in exact decimal arithmetic by reducing the
  instance to an integer mantissa scaled by a power of ten; a value is a
  multiple of `d` iff that reduction is divisible by `d` (zero is a
  multiple of every divisor). Divisors up to 10 significant decimal digits
  are supported exactly; a larger divisor refuses UNSUPPORTED_FEATURE with
  the pointer of the `multipleOf` keyword.
- Contradictory bounds (`minimum > maximum`, or equal bounds with either
  side exclusive, or a range disjoint from an integer type) are an empty
  language: UNSATISFIABLE_CONSTRAINT at the root, an empty arm in a union.
- Inside `not`, numeric range/multiple keywords stay refused (the
  complement of a numeric interval needs machinery the approved `not`
  subset does not have).


### 4.5. String patterns (`pattern`, regex `patternProperties`)

**Decision.** The `pattern` keyword and a regex `patternProperties` key are
ECMA-262 regular expressions evaluated as an **unanchored search** over the
**value** of the string (JSON Schema 2020-12, validation 6.3.1): the match
domain is the sequence of Unicode scalar values the document string decodes
to under §4.2, never the raw byte spelling (an escaped newline `\n` is the
codepoint U+000A for the pattern; a multi-byte UTF-8 character is one
codepoint). Anchors `^`/`$` bind to the string edges (no multiline flag
exists). The supported subset, its limits and the refused constructs are
fixed by src/pattern.zig (ECMA-262 subset compiled to a DFA): constructs
outside the subset refuse UNSUPPORTED_FEATURE and malformed regexes
INVALID_SCHEMA, both with the exact JSON pointer of the keyword.

Consequences:

- The assertion conjoins with the length bounds: `minLength`/`maxLength`
  count codepoints of the same decoded value, and a pattern whose accepted
  lengths are disjoint from the bounds is an empty language (a refusal at
  the root, an empty arm in a union).
- `pattern` filters the string values of `enum`/`const` exactly like the
  length bounds do.
- A `patternProperties` regex reroutes the value schema of every undeclared
  key whose decoded name it matches (one pattern; several patterns and a
  declared-key overlap need the intersection machinery and stay refused).
- Acceptance and rejection are exact, and the byte mask inside a
  pattern-constrained string is exact too: the search-mode DFA alone would
  keep a live state for every content byte (a mismatch would otherwise
  surface only at the closing quote), so mask and accept run the ADR-0005
  R1 residual-reachability filter (src/complete.zig) over the exact
  length-acceptance descriptor of the DFA (src/pattern.zig): a content byte
  is admitted only when a continuation exists that completes within the
  length bounds on an accepting DFA state. A DFA whose descriptor analysis
  exceeds its budget is undecided, never guessed; a state the budgeted
  search cannot resolve surfaces as `RESOURCE_LIMIT` (budget semantics
  below).

**Reachability search budget (mask/accept failure semantics).** The
residual-reachability filter proves "a completion is reachable" (token
admitted) or "no completion is reachable" (token masked / accept refused)
either in closed form (per-frame certification) or by a bounded
depth-first search. The search is deliberately bounded so a pathological
grammar cannot stall generation: iterative-deepening depth tiers (32 /
256 / 512 tokens; the scratch pool holds at most 256 parser states, ~32
MB, charged to the context temp budget), per-tier expansion budgets
(32 / 128 / 128 expansions), and a per-mask-call expansion budget shared
by all token checks of one `fill_mask` (512 expansions,
`complete.FILL_SEARCH_BUDGET`; the accept filter gets the same fresh
budget per call). Any state the search cannot settle within these bounds
reports `ResourceLimit`, which is UNKNOWN, not alive (ADR-0005 Decision 3,
strict form): the mask/accept call sites propagate it, so the whole
`fill_mask` fails and `blg_accept_token` refuses the token with
`RESOURCE_LIMIT` - an unproven token is never silently admitted, since an
actually dead admitted token could dead-end the session irrecoverably.
The outcome is exact in both directions: a masked token is an exact
dead-end proof, an admitted token is provably reachable, and
`RESOURCE_LIMIT` means "unproven within budget" (or an engine-level
limit: parser thread/frame caps, work limit, memory budget).

### 4.6. Summary table

| Aspect | Policy |
|---|---|
| Whitespace | none outside strings |
| Escapes | canonical-v1 table only; `\/` and `\uXXXX` >= 0x20 forbidden |
| Key order, declared keys | schema declaration order, skippable optional keys |
| Key order, undeclared keys (open objects) | unrestricted, unique, interleaving allowed |
| Key order, merged objects | first appearance across subschemas in document order |
| Number spellings | any JSON-valid spelling; type checked by value (`1.0` is an integer) |
| Range keywords and `multipleOf` | exact decimal comparison by value; divisors limited to 10 significant digits |
| Content after the root value | forbidden |

## 5. `--compact` is not a canonicalizer

The MaskBench `--compact` flag serializes instances as
`json.dumps(data, ensure_ascii=False, separators=(",", ":"))`. It removes
whitespace only. It does **not** reorder keys and does **not** normalize
numeric spellings. Verified examples (2026-09-20, CPython 3.10):

- `{"b": 2, "a": 1}` serializes to `{"b":2,"a":1}` - the data key order is
  kept, not the schema order;
- `1.0` serializes to `1.0` - the fraction spelling is kept;
- `1e2` in data round-trips through Python floats to `100.0` - the spelling
  changes with the value preserved;
- control characters serialize as lowercase `\u00xx` (for example
  `\u0001`), `/` is not escaped, non-ASCII stays raw - the string output
  matches §4.2.

Consequences: `--compact` output satisfies §4.1 and §4.2 always; it
satisfies §4.3 only when the instance key order happens to conform to the
schema order; it satisfies §4.4 always (value-based acceptance covers
`1.0` and `100.0`).

## 6. Serializer contract for benchmarks

**Decision.** The byte language requires a value-preserving serializer for
benchmark runs; a demonstrated match between the ordinary compact serializer
and the byte language does not exist, because instance key order is
data-determined and `--compact` keeps it (§5), while §4.3 fixes the declared
key order by the schema. The value-preserving serializer (P1, feature map)
must:

- reorder object keys to the schema-driven order of §4.3 (declared keys in
  declaration order; undeclared keys of open objects keep their data order);
- preserve every value exactly: no numeric respelling beyond what the data
  already carries, no dropping or adding of keys;
- emit the compact whitespace-free form of §4.1-§4.2.

The serializer is implemented in `python/bolorgir/serializer.py`
(`serialize_value`, `serialize_json_text`; see docs/API.md section 3.7).
Runs made without it use `--compact` and record every instance rejected
solely on serialization grounds as a serialization/profile incompatibility
(§7), not as an engine failure.

Cross-engine timing comparisons use identical resulting token streams: all
engines in a paired run receive the same serialized instances (the same
serializer flag), as already done for the canonical-v1 paired run with
`--compact` (benchmarks/maskbench/README.md).

## 7. Error separation

Serialization/profile incompatibilities are recorded separately from
assertion (validation) errors:

- An instance outside the byte language of §4 (whitespace, escape, key-order
  or spelling mismatch) is a **serialization/profile incompatibility**, an
  artifact of the measurement protocol; it is tracked in its own counter in
  benchmark reports.
- An instance inside the byte language that violates a schema assertion is a
  genuine negative example; the engine must reject it, and accepting it is an
  engine correctness error.
- The two never share a bucket; mixing them would count protocol artifacts as
  semantic failures or hide real ones.

## 8. Differences between spec-v1 and full JSON / JSON Schema

spec-v1 accepts any schema of the supported dialects whose keywords are
implemented (docs/dialect-matrix.md), but the document language remains the
restricted serialization of §4. The following are valid per RFC 8259 and/or
the supported dialects but are **outside** the spec-v1 byte language:

1. Whitespace outside strings (`{ "a": 1 }`, indentation, newlines).
2. Alternative string escapes (`\/`, `\u0041`, uppercase hex, `\u0009` for
   tab); exactly the table of §4.2 is allowed.
3. A declared-key order different from the schema declaration order (§4.3),
   even though JSON Schema does not restrict key order.
4. `NaN`, `Infinity`, hexadecimal numbers, leading zeros, `+1`, `.5`, `1.` -
   not JSON numbers.
5. Schema keywords not implemented in the current phase are refused with
   UnsupportedFeature and a JSON pointer; a refusal is never counted as
   coverage, and no substitute with a different allowed set is used
   (ROADMAP principles 2-3).

Unlike canonical-v1, numeric spellings are **not** restricted beyond the
JSON grammar: `1.0` and `1e2` are in the integer language when their value
is integral (§4.4).

Completeness is claimed only relative to the language of §4: the engine does
not forbid any document of this language for a supported schema.
Completeness relative to all serializations the dialects allow is not
claimed; the boundary is exactly this file.

## 9. Recursion and references (P5, ADR-0008)

**Decision** (bounded unrolling). A local `$ref` cycle (`#`, `#/...`
pointers, plain-name fragments, in every supported dialect) compiles by
bounded unrolling: the recursive target is inlined again at every cycle
re-entry while a per-path budget of **8** (`REF_UNROLL_CAP`) lasts. With
the initial expansion the recursive target matches at most **1 + 8 nested
occurrences along any document path** (a linked list accepts at most 8
nested `next` links below its root object; a tree at most 8 nested levels
below its root); a document that nests the recursive construct deeper is
**outside the byte language**.

- The limit is a property of the compiled language, not a runtime error:
  at the bottom the recursive position behaves exactly as the subschema
  `false` (the ADR-0006 D1 empty language). An optional recursive
  property becomes a banned key at the boundary level, a required one
  makes its level unsatisfiable, a recursive array element caps the
  array. Masks stay exact for the truncated language; no live prefix of
  an in-language document dead-ends (ADR-0005), and feeding a token that
  would cross the limit fails like any out-of-language byte.
- **Correspondence caveat (non-monotone constructs).** The claim "the
  truncated language is exactly the original documents whose recursion
  nesting fits the budget" is a monotonicity argument: replacing the
  budget-exhausted tail with `false` only *narrows* the language where
  the cycle passes through monotone constructs (applicators,
  `properties`/`items`, `allOf`). Where the cycle passes through `oneOf`,
  `not` or `if`/`then`/`else` the substitution is non-monotone and can
  also *widen* the language - a document matched by two `oneOf` branches
  only through the recursive tail may, once the tail turns `false` in one
  branch, match exactly one branch and become acceptable. For cycles
  through those constructs the guarantee is ADR-0005 R1 exactness
  relative to the compiled bounded grammar; document-level equivalence
  with the untruncated schema within the depth budget is not claimed.
- Depth accounting: the unrolled copies consume the schema `max_depth`
  budget; inside a recursive unroll an exhausted depth budget truncates
  the position to the same empty-language bottom instead of refusing the
  schema. The grammar node budget (`MAX_NODES`) and the compile work
  budget still apply to the multiplied copies (a wide recursion may
  refuse with RESOURCE_LIMIT).
- **Unproductive recursion** (a `$ref` cycle with no base case:
  `{"$ref":"#"}`, pure `$defs` ref chains, a *required* recursive
  property) defines the empty language and is the empty-language outcome
  bucket: `UNSATISFIABLE_CONSTRAINT` at compile, recognized, never
  compiled.
- `$ref` fragments are URI fragments: `%XX` percent-decoding applies
  before the JSON-pointer `~0`/`~1` unescaping (RFC 6901 section 6), for
  pointer segments and plain-name fragments.
- `$recursiveRef`/`$recursiveAnchor` (2019-09) and
  `$dynamicRef`/`$dynamicAnchor` (2020-12) are supported per section 11
  (P6b): dynamic-scope resolution at compile time, sharing the bounded
  unrolling budget.
- External `$ref` resolves through the immutable registry snapshot of
  ADR-0006 D5 / ADR-0008 (no network); without a registry every external
  `$ref` stays an `UNSUPPORTED_FEATURE` refusal with the pointer. Since
  P6b the reference URI is first resolved against the enclosing base URI
  (section 11) - an absolute reference resolves to itself, so the P5
  behavior is subsumed.
- canonical-v1 is frozen: reference cycles there stay `INVALID_SCHEMA`.

## 10. unevaluated* (P6a, ADR-0009)

**Decision** (compile-time scenario synthesis). `unevaluatedProperties` /
`unevaluatedItems` (dialects 2019-09 and 2020-12; unknown and ignored in
earlier drafts) compile by enumerating the acceptance hypotheses
(*scenarios*) of the enclosing schema at compile time. A scenario is a
list of conjuncts (grammar nodes that must all accept the instance) plus
the *eval set*: the object keys / array indices that are *evaluated*
under that hypothesis. The schema's language is the disjunction of its
scenarios; a document accepted under several scenarios is covered by the
scenario whose eval set is the union of theirs, which the synthesis
constructs explicitly:

- `allOf` and local `$ref` product their scenarios in (both sides accept;
  the eval sets union). `$ref` cycles cut at the ADR-0008 bounded
  re-expansion; the eval fixed point of the target is accounted at the
  outer level.
- `anyOf` enumerates every non-empty subset of its live branches (a
  document matched by several branches is covered by their joint subset);
  more than 5 live branches refuse.
- `oneOf` conjoins the exactly-one comb into every scenario; the eval
  flow comes from the single accepting branch, so each branch's scenarios
  are one variant.
- `if`/`then`/`else`: the success side is the product of the if- and
  then-scenarios; the failure side is `not(if)` conjoined with the
  else-scenarios.
- `dependentSchemas` splits on the trigger key's presence.

The synthesis is capped at 32 scenarios; past the cap the schema refuses
`UNSUPPORTED_FEATURE` with a pointer.

**Guards and merges.** The keyword's subschema becomes a guard conjoined
with every scenario: for `unevaluatedProperties` an open-object guard
whose declared keys are the scenario's eval set and whose undeclared-key
values must match the subschema; for `unevaluatedItems` the symmetric
tail guard (indices after `prefixItems`/tuple `items`; an element matched
by `contains` counts as evaluated, per 2020-12 annotation rules).
Scenarios with an identical eval set share one guard; conjuncts shared by
every scenario hoist out of the disjunction in the first-appearance order
of the first live scenario, so the merged-object key order of §4.3 is
preserved. Open-object conjuncts of one scenario merge into a single
object node (declared props union in first-appearance order, values of
undeclared keys conjoin); the merge is declined for shapes that would
change the language (`propertyNames`, forbidden names, two patterns, a
pattern overlapping declared keys). Guards track seen keys so a required
key declared by another conjunct is recognized at dispatch.

**Container masking.** When the schema's behavior outside the guarded
container kinds is statically analyzable (a three-valued
accept-all/accept-none/unknown analysis over the schema, following local
`$ref`s), the scenario walk compiles restricted to the guard kinds and
one top-level arm covers the outside; `unknown` falls back to the fully
wrapped form.

**Inert reductions.** `unevaluated*: true` evaluates nothing new and
compiles away; an `additionalProperties`/`items` that already covers
every undeclared key / tail element makes the keyword inert as well.

**Limits (refusals with a JSON pointer).** Beyond the caps above: an
external `$ref` next to unevaluated*; more than 4 `dependentSchemas`
entries; more than 8 `patternProperties` patterns feeding one guard;
conditional (data-dependent) evaluation under a nested unevaluated*
subschema; and the two runtime-budget guards - a `oneOf`/`anyOf` branch
that nests another combinator (directly or through in-place applicators
and local `$ref`s), and `unevaluatedItems` next to two or more
conjunctive `contains` subschemas. These shapes are semantically covered
by the model but would exceed the parser thread cap at accept time, so
they refuse at compile time instead.

## 11. Content keywords, URI resolution and dynamic references (P6b, ADR-0010)

**Content keywords are annotations.** `contentEncoding`,
`contentMediaType` and `contentSchema` never assert (2020-12 Validation
specification section 8): they are ignored by the compiler in every
dialect (unknown keywords in the dialects without the Content
vocabulary, pure annotations where the vocabulary exists). The engine
validates the JSON document's byte language; whether a string *payload*
decodes as base64 or as a media type is a property of a different
alphabet and is out of scope - including `contentSchema`, whose asserted
form would require a parser for the decoded text. canonical-v1 keeps
refusing them.

**URI resolution.** Every subschema carrying the dialect's id keyword
(`$id`; `id` in draft-04) opens a resource whose *absolute base URI* is
the id resolved against the enclosing resource's base per RFC 3986
sections 5.2/5.3 (scheme, network-path, absolute-path and relative-path
references; dot-segment removal per section 5.2.4). The main document's
outermost base is its root id, or the empty URI when the root has none.
A reference whose text does not start with `#` splits off its fragment,
resolves the URI part against the current base, and then addresses, in
order:

1. an in-document (or already collected registry) resource whose
   absolute base URI matches - the compile jumps into it exactly as into
   a registry document (its anchors and its base become the scope);
2. the registry snapshot (exact URI match of the resolved form);
3. otherwise the documented `UNSUPPORTED_FEATURE` refusal with a
   pointer.

The fragment resolves inside the addressed resource with the local
rules: `#` and `#/...` pointers against the resource root, plain names
via the dialect's anchor keyword - scoped to the addressed resource in
2019-09/2020-12, document-global in draft-04/06/07. URI comparison is
exact (no scheme or percent normalization); the retrieval URI of the
main document is unknown, so a *relative* reference under a base-less
document only matches an equally relative in-document id.

**`$dynamicRef` / `$dynamicAnchor` (2020-12).** A `$dynamicRef` with an
empty or pointer fragment behaves exactly like `$ref`. With a
plain-name fragment it first resolves statically like `$ref` (a
`$dynamicAnchor` doubles as a plain anchor for this step; a same-name
`$anchor` in the same resource keeps the static role). When the
statically resolved resource carries a same-name `$dynamicAnchor` (the
bookending requirement), the target is instead the first matching
`$dynamicAnchor` of the *dynamic scope* - the chain of resources entered
to reach the position under compile, searched outermost first. At
compile time the dynamic scope is the resource chain of the expansion
path (lexical descent inside one resource does not re-enter it); the
expansion itself is the ADR-0008 bounded unrolling, so a dynamic
recursion shares the unroll budget with static cycles.

**`$recursiveRef` / `$recursiveAnchor` (2019-09).** Only the `#` form
exists (anything else is `INVALID_SCHEMA`). It statically addresses the
current resource root; when that root carries `$recursiveAnchor: true`,
the target is the outermost resource root of the dynamic scope with
`$recursiveAnchor: true`.

**Sibling rule.** In 2019-09/2020-12 assertion and applicator keywords
next to `$ref`/`$dynamicRef`/`$recursiveRef` apply alongside the
reference: the reference expansion and the sibling schema compile
independently and conjoin (an `allOf` comb node). The referenced
subschema does not see the siblings' annotations - the suite's "ref
creates new scope when adjacent to keywords" pins exactly this. In
draft-04/06/07 the siblings are ignored.

**Limits (refusals with a JSON pointer).** `$dynamicRef`/`$recursiveRef`
next to unevaluated* refuse (`UNSUPPORTED_FEATURE`): the dynamic scope
of the ADR-0009 scenario walk is not the evaluation dynamic scope.
A reference to the 2020-12 metaschema URI stays a registry refusal (the
metaschema is not in the snapshot). An unknown resolved URI refuses as
in P5; an unresolvable fragment in a known resource is `INVALID_SCHEMA`.
