# dialect-matrix.md - dialect x keyword matrix of the spec-v1 profile

Status: **normative document**, P1 input (ROADMAP section 5, P1 input 2; A7).
Fixes how the `spec-v1` profile treats each keyword in each supported
dialect. Implementational, not a tutorial: each cell says what spec-v1 does.
The authority for keyword meaning is the JSON Schema specification of the
respective draft; this file fixes detection, tolerance, normalization and
refusal rules. Value equality and serialization are in
docs/semantics-spec-v1.md; the canonical-v1 profile is unaffected by this
file.

Supported dialects (initial list, ROADMAP section 5): **draft-04, draft-06,
draft-07, 2019-09, 2020-12**.

## 1. Dialect detection

- The dialect is selected by the root `$schema` value, matched against the
  known identifiers with an optional trailing `#`; `http` and `https`
  spellings of the same identifier are equivalent:

  | Dialect | Identifier |
  |---|---|
  | draft-04 | `http://json-schema.org/draft-04/schema` |
  | draft-06 | `http://json-schema.org/draft-06/schema` |
  | draft-07 | `http://json-schema.org/draft-07/schema` |
  | 2019-09 | `https://json-schema.org/draft/2019-09/schema` |
  | 2020-12 | `https://json-schema.org/draft/2020-12/schema` |

- **`$schema` absent:** the documented default is **2020-12** (ROADMAP P1
  input 2). The default is a property of the profile, not a guess per file.
- **Unknown `$schema`:** refusal, `UNSUPPORTED_FEATURE` with the pointer
  `/$schema`. No fetching, no heuristic matching.
- **Custom metaschemas** (a `$schema` naming anything other than the five
  identifiers, including a metaschema that only extends a known one):
  rejected the same way (A7). spec-v1 never downloads or interprets
  metaschemas; the five dialects are built in.
- `$schema` at non-root positions is an annotation and ignored (dialect is a
  document-level property of the root schema resource).

## 2. General rules

1. **Keyword belongs to the active dialect** - its value is checked against
   the dialect's form; a wrong form is `INVALID_SCHEMA` with the JSON
   pointer (for example, a numeric `exclusiveMinimum` in draft-04).
2. **Keyword does not belong to the active dialect** - it is an unknown
   keyword and is **ignored** (tolerant reading), with a recorded diagnostic.
   This covers cross-draft keywords: `prefixItems` in a draft-07 schema,
   `const` in a draft-04 schema, `additionalItems` in a 2020-12 schema are
   ignored, never reinterpreted and never errors (JSON Schema core: unknown
   keywords are annotations).
3. **Tolerant reading is not tolerant semantics** (ROADMAP principle 5):
   ignoring an unknown keyword is exact behavior per the specs; weakening an
   implemented assertion is not. An implemented keyword is exact or refused.
4. **Vocabularies** (`$vocabulary`, 2019-09 and 2020-12 only): each entry
   marked required (`true`) must name a vocabulary spec-v1 implements;
   otherwise refusal with a pointer (core 8.1.2). Entries marked `false` and
   unknown entries may be ignored. The standard Core, Applicator, Validation,
   Meta-Data and Format-Annotation vocabularies of the two newest drafts are
   supported; the Unevaluated vocabulary is implemented (P6a, ADR-0009)
   and the Content vocabulary's keywords are annotations that compile
   away (P6b, ADR-0010); assertion-bearing format vocabularies (the
   2019-09 `format` vocabulary and the 2020-12 **Format-Assertion**
   vocabulary) are not implemented, so a schema that requires them is
   refused. In earlier drafts `$vocabulary` is
   an unknown keyword (rule 2).
5. **`format` is an annotation** in every supported dialect (the standard
   2020-12 default; P4 keeps it an annotation, ROADMAP P4 item 2). It never
   rejects an instance. Format-Assertion behavior exists only through rule 4
   and is a refusal while unimplemented.
6. **Original JSON pointers are preserved during normalization** (A7): when
   the tolerant reader rewrites a form (section 8), errors, refusals and
   diagnostics carry the pointer of the original spelling, including
   `definitions` (`#/definitions/...` never becomes `#/$defs/...` in
   diagnostics).
7. Key absence defaults follow the active dialect (section 7).

## 3. Reference and addressing keywords

| Keyword | draft-04 | draft-06 | draft-07 | 2019-09 | 2020-12 |
|---|---|---|---|---|---|
| `$ref` | siblings **ignored** | siblings **ignored** | siblings **ignored** | siblings **applied** alongside | siblings **applied** alongside |
| `id` | base-URI / scope change; defines plain-name fragments | unknown, ignored | unknown, ignored | unknown, ignored | unknown, ignored |
| `$id` | unknown, ignored | base-URI / scope change; `#name` defines a plain-name fragment | as draft-06 | base-URI change; may not carry a fragment | as 2019-09 |
| `$anchor` | unknown, ignored | unknown, ignored | unknown, ignored | defines a plain-name fragment | as 2019-09 |
| `$recursiveRef` / `$recursiveAnchor` | ignored | ignored | ignored | **supported** (P6b): `#` only; dynamic scope per semantics-spec-v1 §11 | ignored |
| `$dynamicRef` / `$dynamicAnchor` | ignored | ignored | ignored | ignored | **supported** (P6b): bookending + dynamic scope per semantics-spec-v1 §11 |
| `definitions` | annotation container; resolvable by pointer | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `$defs` | ignored | ignored | ignored | annotation container; resolvable by pointer | as 2019-09 |
| `$comment` | ignored | ignored | annotation, ignored | annotation, ignored | annotation, ignored |
| `$vocabulary` | ignored | ignored | ignored | rule 4 of section 2 | rule 4 of section 2 |

`$ref` handling in spec-v1 (all dialects):

- Local forms `#`, `#/...` JSON pointers (with `~0`/`~1` escaping),
  plain-name fragments (`#name` via `id`/`$id`/`$anchor` per the table) go
  through the single extended resolver of P1; the `id`/`$id` scope stack
  tracks base-URI changes.
- **Sibling rule** (A7): in draft-04/06/07 a keyword next to `$ref` is
  ignored (for example `maxLength` next to `$ref` does not constrain); in
  2019-09/2020-12 it applies alongside the referenced schema - since P6b
  the reference expansion and the sibling assertions compile
  independently and conjoin as an `allOf` comb node (the referenced
  subschema does not see the siblings' annotations). The same rule holds
  for `$dynamicRef` and `$recursiveRef`. This is a
  per-dialect semantic difference, not a normalization: the reader keeps
  both forms and the compiler applies the dialect's rule.
- External and network references: P5 through the immutable registry
  snapshot (ADR-0006 D5, ADR-0008); without a registry, refusal
  `UNSUPPORTED_FEATURE` with the pointer. Since P6b the URI part of a
  reference first resolves against the enclosing base URI (RFC 3986
  sections 5.2/5.3; dot-segment removal per 5.2.4) and may address an
  in-document `$id` resource directly (semantics-spec-v1 §11); the
  registry lookup matches the resolved absolute URI exactly.
  Reference cycles: **P5**,
  bounded unrolling (ADR-0008) - a productive recursive cycle compiles
  with the documented per-path depth budget (8 unrolls; the bottom
  position behaves as `false` and the limit is mask-visible); a cycle
  without a base case defines the empty language and refuses
  `UNSATISFIABLE_CONSTRAINT` at compile.
- `$ref` fragments are URI fragments: `%XX` percent-decoding applies
  before the `~0`/`~1` pointer unescaping (RFC 6901 section 6).

## 4. Applicator keywords

| Keyword | draft-04 | draft-06 | draft-07 | 2019-09 | 2020-12 |
|---|---|---|---|---|---|
| `items` (schema) | all elements | as draft-04 | as draft-04 | as draft-04 | elements after `prefixItems` |
| `items` (array = tuple) | tuple form; implemented (P2) | as draft-04 | as draft-04 | as draft-04 | invalid form, `INVALID_SCHEMA` |
| `additionalItems` | applies only when `items` is an array; ignored otherwise; default `true`; implemented (P2) | as draft-04 | as draft-04 | as draft-04 | unknown, ignored |
| `prefixItems` | unknown, ignored | unknown, ignored | unknown, ignored | unknown, ignored | tuple form; implemented (P2) |
| `contains` | unknown, ignored | at least one match (min=max=1 implied); implemented (P2) | as draft-06 | with `minContains`/`maxContains`; implemented (P2) | as 2019-09 |
| `minContains` / `maxContains` | ignored | ignored | ignored | defaults 1 / unbounded; ignored without `contains`; implemented (P2) | as 2019-09 |
| `properties` | default `{}`; P1 open objects when `additionalProperties` absent | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `patternProperties` | default `{}`; exactly one pattern: a literal-substring pattern (P2) or a full ECMA-262-subset regex (P4); several patterns and declared-key overlap refuse (intersection, P3+) | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `additionalProperties` | default `true`; absent/`true` = open object (P1); schema form implemented (P2) | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `propertyNames` | unknown, ignored | implemented (P2) | as draft-06 | as draft-06 | as draft-06 |
| `dependencies` | schema or string array; implemented (P2), normalized (section 8) | as draft-04 | as draft-04 | unknown, ignored | unknown, ignored |
| `dependentRequired` | unknown, ignored | unknown, ignored | unknown, ignored | string array; implemented (P2) | as 2019-09 |
| `dependentSchemas` | unknown, ignored | unknown, ignored | unknown, ignored | schema; implemented (P2) | as 2019-09 |
| `unevaluatedProperties` / `unevaluatedItems` | ignored | ignored | ignored | implemented (P6a, ADR-0009 scenario synthesis; budget-guard refusals with pointer) | as 2019-09 |
| `allOf` / `anyOf` / `oneOf` / `not` | non-empty arrays / schema; implemented (**P3**) under the exactness rules of ROADMAP section 5 | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `if` / `then` / `else` | unknown, ignored | unknown, ignored | implemented (**P3**) lowering; `then`/`else` ignored without `if` | as draft-07 | as draft-07 |

Phase notes: `oneOf` is exact exclusivity with subtype-aware disjointness
(`integer` subset of `number`); `allOf` merges only under proven equivalence;
no `anyOf` substitution anywhere. `contains` counters and
`dependentRequired`/`dependentSchemas` carry the residual-satisfiability
checks of ROADMAP 4.1.

## 5. Validation keywords

| Keyword | draft-04 | draft-06 | draft-07 | 2019-09 | 2020-12 |
|---|---|---|---|---|---|
| `type` | string or array of strings; absent = any type; unions **P1** | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `enum` | non-empty array; structural values **P1**, equality per semantics-spec-v1 §2 | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `const` | unknown, ignored | **P1** | **P1** | **P1** | **P1** |
| `multipleOf` | number > 0; implemented (**P4**) in exact decimal arithmetic (semantics-spec-v1 §4.4); divisors over 10 significant digits refused with a pointer | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `minimum` / `maximum` | number; implemented (**P4**), exact decimal boundaries compared by value | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `exclusiveMinimum` / `exclusiveMaximum` | **boolean** modifier of `minimum`/`maximum`; default `false`; meaningless (ignored) without the bound; implemented (**P4**), normalized (section 8) | **number**, standalone; implemented (**P4**) | as draft-06 | as draft-06 | as draft-06 |
| `minLength` / `maxLength` | non-negative integers; count Unicode scalar values; P1 | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `pattern` | ECMA-262 regex; implemented (**P4**) as the src/pattern.zig subset (unanchored search over the decoded value), unsupported constructs refused with a pointer | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `minItems` / `maxItems` | non-negative integers, defaults 0 / unbounded; P1 | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `uniqueItems` | boolean, default `false`; implemented (P2), candidate hash + exact structural equality | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `minProperties` / `maxProperties` | non-negative integers, defaults 0 / unbounded; implemented (P2) | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `required` | array of unique strings; absent = `[]`; P1 | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `format` | annotation, never asserts (section 2, rule 5) | as draft-04 | as draft-04 | as draft-04 | as draft-04 |
| `contentEncoding` / `contentMediaType` | unknown, ignored | unknown, ignored | unknown, ignored | annotation, ignored (P6b) | annotation, ignored (P6b) |
| `contentSchema` | unknown, ignored | unknown, ignored | unknown, ignored | annotation, ignored (P6b: 2020-12 Validation section 8 - content keywords never assert) | annotation, ignored (P6b) |

Notes:

- The draft-04 metaschema requires `required` and `enum` to be non-empty.
  spec-v1 does not enforce metaschema cardinality: an empty `required` is
  accepted in every dialect, equivalent to absence (identical semantics).
  Value-form checks of section 2 rule 1 still apply.
- Applicability by type: a validation keyword constrains only instances of
  its own type (`maxLength` ignores non-strings, `minimum` ignores
  non-numbers, etc.); applicability never makes a keyword an error.

## 6. Annotation keywords

`title`, `description`, `default` (all drafts), `examples` (draft-06+),
`readOnly` / `writeOnly` (draft-07+), `deprecated` (2019-09+): annotations,
ignored; any value. In earlier drafts they are unknown keywords (section 2
rule 2) with the same effect. Unknown and extension keywords are ignored in
spec-v1 (they are errors only in canonical-v1).

## 7. Key-absence defaults (all five dialects)

| Keyword | Absence default |
|---|---|
| `type` | any type (P1: the supported primitive union plus AnyJSON) |
| `required` | `[]` |
| `properties`, `patternProperties` | `{}` |
| `additionalProperties` | `true` (open object, P1) |
| `additionalItems` (draft-04..2019-09) | `true` |
| `items` | no constraint (draft-04..2019-09); no elements beyond `prefixItems` constrained (2020-12) |
| `uniqueItems` | `false` |
| `minLength`, `minItems`, `minProperties` | 0 |
| `maxLength`, `maxItems`, `maxProperties` | unbounded |
| `exclusiveMinimum` / `exclusiveMaximum` (draft-04) | `false` |
| `minContains` / `maxContains` | 1 / unbounded |
| `minimum`/`maximum`/`multipleOf`/`pattern`/`format` | no constraint |

## 8. Normalization mapping (tolerant reader)

The reader normalizes to a 2020-12-shaped internal form. Every rewrite keeps
the original JSON pointer for diagnostics (section 2, rule 6); normalization
never repairs an invalid schema (semantics-spec-v1 §3).

| Source form | Normalized form |
|---|---|
| draft-04 `{"minimum": m, "exclusiveMinimum": true}` | `{"exclusiveMinimum": m}` |
| draft-04 `exclusiveMinimum: false` or without `minimum` | dropped (no constraint) |
| (same for `maximum` / `exclusiveMaximum`) | symmetric |
| draft-04..07 `dependencies` with an array value | `dependentRequired` (pointer stays `/dependencies`) |
| draft-04..07 `dependencies` with a schema value | `dependentSchemas` (pointer stays `/dependencies`) |
| draft-04 `id` | `$id` (scope stack unchanged) |
| draft-04..2019-09 tuple `items` + `additionalItems` | `prefixItems` + `items` |
| `definitions` | kept in place; references to `#/definitions/...` resolve directly; diagnostics keep `/definitions/...` pointers |

Forms not listed are kept as written. Cross-draft keywords are ignored
before normalization (section 2, rule 2), so a draft-07 `prefixItems` never
reaches this table.

## 9. Oracle coverage results

Acceptance runs of `tests/oracle/run_oracle.py` (pinned Test Suite 23.2.0 +
jsonschema 4.26.0), profile `spec-v1`. The `tests/oracle/results/` reports
cited below are local run outputs and are not versioned; regenerate them
with the commands in `tests/oracle/README.md`.

**Current acceptance: the 2026-09-30 `fix3g` runs** (strict ADR-0005
Decision 3 plus the closed-form certificate families; build identities in
docs/RUN_HISTORY.md, "fix3 acceptance"). Reports:
`tests/oracle/results/oracle-fix3g-<draft>-{spec,canonical}.json|.md`.

| Dialect (suite dir) | Rows | PASS | SKIPPED | KNOWN_GAP | MISMATCH | ENGINE_ERROR |
|---|---:|---:|---:|---:|---:|---:|
| draft-04 (`draft4`) | 591 | 536 | 26 | 0 | 2 | 27 |
| draft-06 (`draft6`) | 794 | 702 | 54 | 1 | 2 | 35 |
| draft-07 (`draft7`) | 878 | 784 | 54 | 1 | 2 | 37 |
| 2019-09 (`draft2019-09`) | 1195 | 1051 | 95 | 1 | 0 | 48 |
| 2020-12 (`draft2020-12`) | 1218 | 1069 | 99 | 1 | 0 | 49 |

Frozen control: 2020-12 canonical-v1 **95 PASS / 1116 SKIPPED /
7 KNOWN_GAP**, unchanged.

Every ENGINE_ERROR row is `error:RESOURCE_LIMIT`: under strict D3 a state
whose completion reachability the bounded search cannot settle fails the
mask call instead of admitting a token that may dead-end. Compared with
the fix1 runs below, the SKIPPED, KNOWN_GAP and MISMATCH counts are
unchanged and the PASS count dropped by exactly the ENGINE_ERROR count.
The 196 rows are pinned one by one in
`tests/oracle/engine_error_residual_spec_v1.json` and enforced by
`tests/oracle/test_oracle_dialects.py::test_engine_error_residual_pinned`:
a new or moved row fails the gate, and fixing a family must shrink the
list. The residual families and their counterexample schemas are
documented in `benchmarks/reports/20260929T_fix3_adr0007/REPORT.md`.

### Previous acceptance: fix1 (2026-09-27, before strict ADR-0005 D3)

The 2026-09-27 `fix1` runs (final tree at the time: the
R1-R4 fixes of CHANGELOG + the minLength mask-normal-form fix + the `smp_allocator`
root-allocator switch for the `vm.max_map_count` munmap panic), release
`--release=fast`, libbolorgir.so SHA-256
`e21846feca5c601709c261fff0794c33a1d2bedb650f26f104a995a1a2b28ef2`
(engine commit `c1cc2cb` + working-tree fixes). Reports:
`tests/oracle/results/oracle-fix1-<draft>-{spec,canonical}.json|.md`.
The fix1 reports are bit-identical (modulo the report timestamp) to the
2026-09-26 `final4` runs (`oracle-final4-*`, SHA-256 `d8f93692...`):
the final4 -> fix1 engine change replaces the root allocator and does
not touch semantics. The table below is likewise bit-identical to the
earlier `final3` runs (`oracle-final3-*`, SHA-256 `c9f5fad1...`): the
final3 -> final4 engine change (the ADR-0007 D2+ normal form now clamps
the string counter at `min_len` instead of zeroing it, so quote-led
close-and-continue tokens are no longer wrongly pruned from the mask for
minLength string properties) touches mask content only and moves no
oracle row.

| Dialect (suite dir) | Rows | PASS | SKIPPED | KNOWN_GAP | MISMATCH | ENGINE_ERROR |
|---|---:|---:|---:|---:|---:|---:|
| draft-04 (`draft4`) | 591 | 563 | 26 | 0 | 2 | 0 |
| draft-06 (`draft6`) | 794 | 737 | 54 | 1 | 2 | 0 |
| draft-07 (`draft7`) | 878 | 821 | 54 | 1 | 2 | 0 |
| 2019-09 (`draft2019-09`) | 1195 | 1099 | 95 | 1 | 0 | 0 |
| 2020-12 (`draft2020-12`) | 1218 | 1118 | 99 | 1 | 0 | 0 |

Frozen control: 2020-12 canonical-v1 **95 PASS / 1116 SKIPPED /
7 KNOWN_GAP**, bit-identical outcome counts to `oracle-p6b-canonical.json`.

Every ENGINE_ERROR row of the r6 run (63-145 per dialect, all
`error:RESOURCE_LIMIT` in `fill_mask`) is resolved: the per-row transition
vs r6 is `{ENGINE_ERROR -> PASS}` plus the fixed `additionalItems`
MISMATCH rows, nothing else moved. SKIPPED counts are unchanged, so the
decomposition table below still applies verbatim. The 2 MISMATCH rows per
draft-04/06/07 remain the whitelisted `ref.json` sibling-`id` deviation
described under "Still open" below. The 2026-09-22 r6 tables are kept
under "Historical" for provenance.

### Historical: r6 run (2026-09-22, superseded by final4)

Acceptance runs of `tests/oracle/run_oracle.py` (pinned Test Suite 23.2.0 +
jsonschema 4.26.0), profile `spec-v1`, byte-level tokenizer. The harness
injects the suite directory's root `$schema` (section 1); the vendored
`remotes/` tree (including the `remotes/draft*/` subdirectories) is handed
to the engine as one immutable registry snapshot keyed by retrieval URI.
Reports: `tests/oracle/results/oracle-r6-<draft>-spec.json|.md`;
canonical-v1 is defined for 2020-12 only (a foreign `$schema` is
`INVALID_SCHEMA`, supported_features section 1), so the matrix below is
spec-v1-only.

Build: release `-Dcpu=baseline`, libbolorgir.so SHA-256
`07de48fb6c8fbc1af7ff71b2ab24c65e0a1c42cfd58ff0c00e792c747026c870`, taken
on the tree with the R1-R3 engine fixes (CHANGELOG) completed. The same
ENGINE_ERROR rows reproduce on a Debug build and in lazy/adaptive/precompute
modes, so they are
engine behavior, not a build artifact. Harness neutrality was A/B-verified:
the pre-R6 harness on the same build produces row-for-row identical
outcomes for draft2020-12 (both profiles).

| Dialect (suite dir) | Rows | PASS | SKIPPED | KNOWN_GAP | MISMATCH | ENGINE_ERROR |
|---|---:|---:|---:|---:|---:|---:|
| draft-04 (`draft4`) | 591 | 500 | 26 | 0 | 2 | 63 |
| draft-06 (`draft6`) | 794 | 662 | 54 | 1 | 2 | 75 |
| draft-07 (`draft7`) | 878 | 744 | 54 | 1 | 2 | 77 |
| 2019-09 (`draft2019-09`) | 1195 | 956 | 95 | 1 | 0 | 143 |
| 2020-12 (`draft2020-12`) | 1218 | 973 | 99 | 1 | 0 | 145 |

Frozen control: 2020-12 canonical-v1 **95 PASS / 1116 SKIPPED /
7 KNOWN_GAP**, bit-identical outcome counts to `oracle-p6b-canonical.json`.

### ENGINE_ERROR delta vs the p6b baseline (draft2020-12 spec-v1) - RESOLVED in final3

**Resolution (2026-09-26, final3): 0 ENGINE_ERROR rows on all five
dialects.** The RESOURCE_LIMIT mechanism described below was eliminated
by follow-up engine work (reachability search made budgeted with a
conservative fallback instead of an error, the memo `in_progress` leak
fixed, per-fill budgets and a state pool added); every former
ENGINE_ERROR row is a PASS in `oracle-final3-*-spec.json`. The gate
`test_oracle_spec_v1.py::test_spec_v1_no_engine_errors` is green. Strict
ADR-0005 D3 (2026-09-29/30) later reintroduced a smaller, deliberate
RESOURCE_LIMIT residual; see the current acceptance above. The historical
analysis is kept below.

All 145 ENGINE_ERROR rows are `error:RESOURCE_LIMIT` raised inside
`fill_mask` (88 at byte 0, 57 later), and every one of them was a PASS in
`oracle-p6b-spec.json` - the per-row transition table vs the baseline is
exactly `{PASS -> ENGINE_ERROR: 145}`, no other transitions. The cause is
the residual-language analysis (ADR-0005) added to the mask path:
its per-call expansion budget (src/complete.zig, `cache.budget = 1 << 16`)
is exhausted for combinator/object-applicator shapes
(unevaluatedProperties 47 rows, oneOf 18, minContains 12, allOf 11,
dynamicRef 10, dependentSchemas 10, additionalProperties 8,
dependentRequired 7, uniqueItems 5, maxProperties 5, propertyNames 4,
contains/if-then-else/unevaluatedItems 2 each, ref/multipleOf 1 each).
Raising the public limits (session memory, work_limit_ops) does not change
the outcome - the budget is internal and not configurable. This is the
"budget exhaustion returned as an error" arm of the R1 recommendation
surfacing at feed time; whether these shapes should instead refuse at
compile time (UNSUPPORTED_FEATURE) or get a configurable budget is an
engine-side follow-up, and the corresponding gate
(`test_oracle_spec_v1.py::test_spec_v1_no_engine_errors`) stays red until
then. The other dialects show the same mechanism (63-143 rows each).

### SKIPPED decomposition (every refusal carries a JSON pointer and a classification)

| Classification | draft-04 | draft-06 | draft-07 | 2019-09 | 2020-12 |
|---|---:|---:|---:|---:|---:|
| `by_design_unsatisfiable` (suite's unsatisfiable schemas, e.g. `false` boolean root) | 0 | 15 | 15 | 15 | 15 |
| `by_design_invalid_schema` (suite's invalid value forms, e.g. non-integer `maxLength`; invalid remote-ref targets) | 2 | 10 | 10 | 8 | 8 |
| `documented_refusal` (`UNSUPPORTED_FEATURE` per supported_features 1a: patternProperties intersection forms, unevaluated* budget guards, custom metaschemas / `$vocabulary`, external/metaschema `$ref` forms, `not` outside the complement subset) | 24 | 29 | 29 | 72 | 76 |
| `needs_review` (never silent) | 0 | 0 | 0 | 0 | 0 |

Notable per-dialect refusal shapes (suite file -> pointer, rows):

- draft-04: `patternProperties` single-pattern restriction (`/patternProperties`
  x10, `/patternProperties/f.o` overlap x8); `$ref` forms outside the
  resolver (definitions.json x2, ref.json x2, refRemote.json
  `/properties/list/definitions/baz/definitions/bar/items` x2).
- draft-06/07: same plus the invalid-form `minLength`/`maxLength`/
  `minItems`/`maxItems` rows (`INVALID_SCHEMA` x8) and boolean-root
  `UNSATISFIABLE_CONSTRAINT` (boolean_schema.json x9).
- 2019-09: unevaluated* budget guards (`unevaluatedProperties.json`
  `/oneOf/0` x21), custom metaschemas (`vocabulary.json` `$schema` x5),
  `$id`-carrying external/plain-name ref forms (`id.json` x13,
  `anchor.json` x3).
- 2020-12: additionally `unevaluatedItems.json` `/allOf/0/contains` x2,
  `dynamicRef.json` x2.

### KNOWN_GAP

`const with object` / "same object with different property order is valid"
(const.json, test 1, draft-06..2020-12 - draft-04 has no `const`): the
spec-v1 byte language fixes the schema spelling order of a const object
(semantics-spec-v1 4.3); the value-preserving serializer reorders only by
`properties`/`allOf`, not by `const`, so the swapped-order instance stays a
KNOWN_GAP (existing entry in known_gaps.json, unscoped - it matches every
dialect with the same case). Fix side: the engine value-filter work (CHANGELOG R3), not the
harness.

### Open deviations (MISMATCH, engine defects to fix - not profile gaps)

Fixed (2026-09-26): draft-04/06/07 `additionalItems.json` "when items is
schema, (boolean) additionalItems does nothing" (1 row each, class
`engine_vs_both`, engine rejected `[1,2,3,4,5]` at the fifth element).
`additionalItems` was a red herring - the root cause was a parser defect:
an integer-valued element under an unconstrained item schema (`{}`,
`anyOf: [{}]`, tuple `prefixItems` of `{}`) completed on both the
`int_num` and `num_v` branches of the AnyJSON choice, and the two
converged threads were never merged. The duplicates doubled per element
(2 after one integer, 16 after four) and the 5th integer hit the
64-thread budget, so a valid array was rejected no matter the element
count bounds. The parser now merges duplicate threads after every byte
(`parser.dedupThreads`, comparing frames and, for the chunk-carrying
repeat/open_obj frames, chunk content, so COW-split copies of one logical
chunk still count as duplicates); the steady state of an unconstrained
array is one thread per element, and uniqueItems/maxItems/contains keep
their exact verdicts (regression coverage: "spec-v1 AnyJSON arrays" in
src/parser.zig, test_anyjson_arrays_many_elements in
tests/test_spec_v1.py). The OPEN_DEVIATIONS whitelist entries were
removed; the rows pass in oracle-fix3-draft4-spec.

Still open: draft-04/06/07 `ref.json` "$ref prevents a sibling
`id`/`$id` from changing
the base uri" (2 rows each, class `engine_vs_both`, suite and
Draft4/6/7Validator agree): the engine resolves the `$ref` against the base
URI declared by the sibling `id`/`$id` instead of ignoring the sibling per
the section-3 sibling rule. Confirmed on the finished post-R1 tree with a
standalone repro (no harness involved): schema
`{"id": "http://localhost:1234/sibling_id/base/", "definitions": {"foo":
{"id": "http://localhost:1234/sibling_id/foo.json", "type": "string"},
"base_foo": {"id": "foo.json", "type": "number"}}, "allOf": [{"id":
"http://localhost:1234/sibling_id/", "$ref": "foo.json"}], "$schema":
"http://json-schema.org/draft-04/schema#"}` - the engine accepts `"a"` and
rejects `1` (resolved to the `string` branch), while draft-04 requires the
opposite (the sibling `id` is ignored, `foo.json` resolves against
`.../base/` to the `number` branch). In 2019-09/2020-12 the sibling rule
applies the `$id`, the suite expects the string branch there, and those
rows PASS - the engine implements the new-dialect behavior uniformly and
misses the draft-04..07 exception for `id`/`$id`. Whitelisted in
`tests/oracle/test_oracle_dialects.py` (`OPEN_DEVIATIONS`); the gate fails
both on a new mismatch and on a fixed-but-still-whitelisted entry.

### Exact-decimal layer

Rows exercising numeric boundaries with binary64-inexact lexemes
(`decimal.sensitive`): draft-04 17, draft-06/07/2019-09/2020-12 14 each.
Under spec-v1 the engine receives the suite's lexemes verbatim
(`decimal.engine_check = "exact decimal (lexeme-preserving schema and
feed)"`); the `jsonschema` oracle stays binary64 (`validator_check`), and
for the simple root-level shape an independent big-integer Decimal oracle
verdict is recorded (`decimal.oracle`), with 0 disagreements with the suite
in all five dialects. Rows fed through the canonical-compact fallback (see
below) are labelled binary64 and never counted as an independent decimal
check.

### Serializer boundary

Under spec-v1 the feed uses `python/bolorgir/serializer.py`
`serialize_value` (schema-driven key order per semantics-spec-v1 4.3,
verbatim numeric lexemes); the schema bytes fed to `blg_compile` are
serialized with the same value-preserving path. Explicitly fixed
boundaries:

- **const/enum object key order** stays a KNOWN_GAP (above) - the
  serializer does not canonicalize const/enum member order.
- **Local reference with in-place applicator siblings** (2019-09/2020-12
  sibling rule, section 3): the serializer resolves the `$ref` for the key
  order and drops the siblings' `properties`, so the harness feeds those
  cases canonical-compact in data order and marks the rows
  (`row["serializer"]`; 12 rows in 2020-12, 9 in 2019-09, counted as
  `summary.serializer_order_fallbacks`). In draft-04..07 siblings are
  ignored, the resolved reference IS the node's order, and no fallback
  triggers.
- `SERIALIZATION_INCOMPATIBLE` (lone surrogates, duplicate keys, ...): 0
  rows in all five dialects.
- **String values are never re-parsed** (CHANGELOG R4, fixed 2026-09-26):
  `serialize_value` emits a JSON string instance verbatim regardless of
  its content (`"1"` stays the string `"1"`, never the number `1`;
  arbitrary text like `"hello"` is a normal string, not a
  serialization incompatibility). Numeric lexeme preservation applies
  only to instances that are numbers.

### Keyword-interaction coverage

Covered by the suite and run per dialect (PASS/ENGINE_ERROR rows; the
ENGINE_ERROR budget failures above affect the feed, not the dialect
classification of the schema):

- `dependencies` (schema and array forms, incl. interactions with
  `required`/`properties`): draft-04 (5 cases), draft-06/07 (7 cases);
  `dependentRequired`/`dependentSchemas`: 2019-09/2020-12 (4 cases each).
- draft-04 boolean `exclusiveMinimum`/`exclusiveMaximum` modifiers
  (section 8 normalization): minimum.json/maximum.json, 8 cases, all fed
  rows PASS.
- `exclusiveMinimum`/`exclusiveMaximum` as standalone numbers
  (draft-06+): exclusiveMinimum.json/exclusiveMaximum.json.
- tuple `items` + `additionalItems` (draft-04..2019-09) vs
  `prefixItems` (2020-12): items.json/additionalItems.json per dialect.
- `boolean_schema` true/false roots (draft-06+), `if`/`then`/`else`
  (draft-07+), `contains` (draft-06+), `propertyNames` (draft-06+),
  `unknownKeyword` (draft-06/07/2019-09) - cross-draft keywords compile
  away per section 2 rule 2 and the rows PASS.
- `$ref` sibling semantics per dialect (ref.json, incl. the open
  deviations above), remote refs per dialect (refRemote.json against the
  shared registry snapshot).
