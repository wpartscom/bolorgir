# ADR-0010: P6b - content* as annotations, URI resolution, dynamic references (spec-v1)

- Status: **accepted** (2026-09-22, ROADMAP revision 2 P6 item;
  `contentEncoding`/`contentMediaType`/`contentSchema`,
  `$dynamicRef`/`$dynamicAnchor` (2020-12),
  `$recursiveRef`/`$recursiveAnchor` (2019-09), the 2019-09/2020-12
  reference-sibling rule, RFC 3986 relative-URI resolution with an
  in-document resource index).
- Context: P5 (ADR-0008) resolves references by bounded unrolling with a
  per-path budget (`REF_UNROLL_CAP = 8`); external `$ref` resolves
  through the immutable registry snapshot (ADR-0006 D5) by exact
  base-URI match, with no relative resolution and no in-document `$id`
  resource index. The oracle long tail (1015 PASS after P6a) grouped the
  remaining spec-v1 refusals around exactly these shapes: 38 rows of
  `dynamicRef.json`, most of `anchor.json`/`id.json`/`ref.json`/
  `refRemote.json` relative references, 8 rows of `content.json`, and
  the 2019-09/2020-12 `$ref`-sibling refusals.

## Decisions

**D1. Content keywords are annotations, unconditionally.** The 2020-12
Validation specification (section 8) defines `contentEncoding`,
`contentMediaType` and `contentSchema` as annotations that never assert;
the pinned Test Suite expects exactly that (every `content.json` case is
valid). An *asserting* reading would need a second parser for the decoded
payload alphabet (base64 text, then a JSON document inside it), which is
outside the engine's byte-language model. The compiler therefore ignores
all three in every dialect (unknown keywords where the Content vocabulary
does not exist, annotations where it does), like `format`. canonical-v1
keeps refusing them (frozen).

**D2. Compile-time URI resolution and an in-document resource index.**
The anchor pre-scan of P1 became a resource pre-scan: every id/`$id`
subschema registers its absolute base URI (the id resolved against the
enclosing base per RFC 3986 sections 5.2/5.3, with dot-segment removal
per 5.2.4) and the resource opens a scope holding that base. A reference
with a URI part resolves it against the current base and addresses, in
order, (1) an in-document (or already collected registry) resource whose
absolute base URI matches, (2) the registry snapshot by the resolved URI,
(3) the documented refusal. This subsumes the P5 exact-match behavior
(absolute references resolve to themselves) and covers relative ids,
base-URI changes in subschemas, URN bases and registry documents whose
inner resources carry their own `$id`. URI comparison is exact - no
scheme or percent normalization; the main document's retrieval URI is
unknown, so a relative reference under a base-less root only matches an
equally relative in-document id (a documented limitation).

**D3. Dynamic references resolve over the compile-time expansion path.**
The dynamic scope of the specification (the resources entered to reach
the point of evaluation) is, under bounded unrolling, exactly the
resource chain of the compile-time expansion path. `$dynamicRef` with an
empty or pointer fragment is `$ref`. With a plain-name fragment it
resolves statically first (a `$dynamicAnchor` doubles as a plain anchor
for static resolution; a same-name `$anchor` in the same resource keeps
the static role); if the statically resolved resource carries a same-name
`$dynamicAnchor` (the bookending requirement), the target becomes the
first matching `$dynamicAnchor` of the dynamic scope, searched outermost
first - the evaluation order. `$recursiveRef` (2019-09, only `#` exists)
statically addresses the current resource root and, when that root has
`$recursiveAnchor: true`, retargets to the outermost dynamic-scope
resource with `$recursiveAnchor: true`. The expansion itself is the
ADR-0008 machinery unchanged: dynamic recursion shares the per-path
unroll budget, so the depth limits stay mask-visible and identical to
static recursion. Rejected alternative: runtime dynamic-scope frames in
the parser - a new per-thread store and new mask semantics (the ADR-0006
state model deliberately carries no such stack), bought only to model a
resolution the compile-time path already determines.

**D4. Reference siblings conjoin.** In 2019-09/2020-12 keywords next to
`$ref`/`$dynamicRef`/`$recursiveRef` apply alongside the reference. The
reference expansion and the sibling schema compile independently and
conjoin as an `allOf` comb node - the exact NFA intersection of ADR-0005
D2, no merge optimization. Independent compilation is not an
approximation: the referenced subschema does not see the siblings'
annotations (the suite's "ref creates new scope when adjacent to
keywords" pins this), so no cross-part annotation flow is lost.
draft-04/06/07 keep ignoring siblings.

**D5. Unevaluated* boundary.** `$dynamicRef`/`$recursiveRef` next to
unevaluated* refuse `UNSUPPORTED_FEATURE` with a pointer: the ADR-0009
scenario walk tracks evaluation hypotheses, not the evaluation dynamic
scope, and silently mis-counting the eval set is worse than a documented
refusal. (An external `$ref` next to unevaluated* already refused for
the analogous reason.)

## Consequences

- Oracle spec-v1: 1015 → 1118 PASS (0 MISMATCH, 0 ENGINE_ERROR);
  `dynamicRef.json` 38 → 2 refusals, `content.json` 8 → 0,
  `anchor.json` 9 → 3, `refRemote.json` 15 → 0, `ref.json` 36 → 4.
  canonical-v1 stays bit-for-bit frozen.
- The grammar identity is unchanged in shape: the registry bytes still
  join it (P5); in-document resource indexing is a pure function of the
  schema bytes.
- The parser, mask and cache are untouched; the ADR-0007 perf gate does
  not apply (compile-side change only).
- Remaining documented refusals in this area: the 2020-12 metaschema URI
  is not in the registry snapshot; `$dynamicRef`/`$recursiveRef` next to
  unevaluated*; unknown resolved URIs without a registry entry.
