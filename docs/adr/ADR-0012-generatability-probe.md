# ADR-0012: compile-time generatability probe

- Status: **proposed** (2026-10-05, audit-fix spec rev 2.4, task T3).
- Context: under the strict ADR-0005 D3 rule a mask fails with
  `RESOURCE_LIMIT` when the completion check of some token cannot be
  decided within its budget. The grammar compiled, so the failure shows up
  only during generation, possibly many tokens in. Callers asked for a way
  to learn about such states before generation: either a concrete
  counterexample, or a statement that none exists for the grammar they
  use.

## Decision 1: the probe is a bounded search over reachable parser states

`BLG_COMPILE_PROBE` in `blg_compile_request.flags` runs, after a
successful compile, a breadth-first search over the parser states reachable
from the start state by tokens the mask allows (`src/probe.zig`). Every
visited state gets its mask computed exactly as `blg_fill_mask` would
(same code, same per-fill budget). Successors are the states after each
allowed non-EOS token. Visited states are identified by their exact image:
the frame bytes (`parser.writeStateKey`) and the content of the side-store
chunks they reference (`parser.writeChunkBlob`).

Budgets come from the context: `probe_max_states` (visited states, default
256) and `probe_max_work` (work ops of the probe itself, default 16M; the
compile's `work_limit_ops` is not drawn on). The probe's memory is charged
to the temp category of the context.

## Decision 2: three outcomes, each with a stated guarantee

| Outcome | When | Compile | Guarantee |
|---|---|---|---|
| found | a visited state's mask fails with `RESOURCE_LIMIT` (an undecided completion check), or an allowed token overflows `max_threads_per_state` | `RESOURCE_LIMIT`; the message holds the token path to that state, `json_pointer` the subschema of the undecided frame | the path is a real counterexample: an ordinary session that accepts it fails the next `fill_mask` |
| inconclusive | a budget ran out first | succeeds (`RESOURCE_LIMIT` "probe inconclusive" with `BLG_COMPILE_PROBE_STRICT`) | none: states not visited may fail |
| proven | the search exhausted the reachable states | succeeds | for this grammar, vocabulary and limits no completion check is undecided; memory and work limits and cancellation can still fail a call |

`blg_grammar_probe_status` returns `BLG_PROBE_NOT_RUN`, `_INCONCLUSIVE` or
`_PROVEN` for a grammar handle; `found` never yields a handle.

## Decision 3: which grammars can be proven

`proven` requires the set of exact reachable states to be finite. Exact
images keep every counter (string length, array count, number digits and
exponent tracking, captured key bytes), so the set is finite exactly when
the language has finitely many prefixes up to these counters: literal and
enum languages over strings, booleans and null, closed objects and fixed
arrays of such values, nested in any combination. Unbounded strings,
numbers (spec-v1 compares numbers by value, so every numeric value has
infinitely many spellings), open objects and unbounded arrays never close
the search and end `inconclusive`. A normal form that would merge states
with equal futures (ADR-0007 D2+ covers uniform string states without
completion filtering) is not used here: an equivalence that ignores the
filter could hide an undecided state.

## Decision 4: caching (audit plan rule 5)

The verdict is stored with the compile artifact together with the budgets
it was obtained under. A compile that hits the artifact cache with a probe
requested reuses a stored `inconclusive` or `proven` verdict only when the
context's budgets are equal; otherwise the probe runs on the cached
grammar. `found` is not stored: the compile fails and the next request
probes again. `blg_stats.probe_runs` counts the searches run.

## Consequences

- The default compile is unchanged; the flags are tail-grown fields of the
  request (struct sizes of the two earlier layouts are still accepted).
- The subschema pointer comes from a per-node pointer table recorded only
  when a probe is requested (`schema.Options.record_pointers`). For a
  cached artifact compiled without it, the schema is compiled once more to
  recover the pointer.
- Test builds (`-Dtest_hooks=true`) can force an undecided verdict for a
  frame kind or a state key and set the search budgets
  (`blg_test_set_overrides`), which the found/inconclusive tests use.
