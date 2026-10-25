# Temporary probes: the p90 residual and the heavy refusals

All runs here come from temporary builds (not shipped) on top of the
committed prefix-skip source `e3b4ea7` / artifact `642ba1ae...7da4`. The
probe patches only instrumented the walk: masks are unchanged, the counters
are repurposed because the diagnostic struct layout is read by name from
Python, and the source was restored after each run. The checkpoint builds
are under `benchmarks/local/t8a-checkpoints/probe-*` (local, not tracked).

Probe timing used `std.Io.Clock.Timestamp.now(io_mod.io(), .awake)` with a
1000-pair calibration per walk; the pair cost (about 12 ns) was subtracted
from every timed region. Where a run has no calibration, the numbers are
raw and marked as such.

## Runs

| file | probe | repurposed counters |
|---|---|---|
| `p90_residual.json` | walk, feed, materialization, exact-bit timers | `content_word_loop`=walk ns, `refuse_byte_incomplete`=feed ns, `trie_terminals`=feeds, `exact_walk_calls`=mat ns, `content_memcpy`=mats, `content_cache_hits`=materialized frames, `alive_dead`=bit-set ns, `refuse_name_state`=bit-set calls, `refuse_not_uniform`=skipped pure edges, `refuse_content_dep`=calibration ns |
| `structure.json` | iteration structure, walk timer | `refuse_byte_incomplete`=exits, `refuse_content_dep`=pruned edges, `alive_dead`=pure multi-edge pushes, `refuse_name_state`=`setExactTokens` calls, `content_word_loop`=walk ns |
| `lifetime.json` | chunk-release and feed timers | `content_word_loop`=walk ns, `refuse_byte_incomplete`=release ns, `trie_terminals`=release calls, `alive_dead`=feed ns, `refuse_name_state`=feeds, `refuse_content_dep`=calibration ns |
| `fast_total.json` | `fillMaskFast` total vs walk | `content_word_loop`=`fillMaskFast` ns (defer), `exact_walk_calls`=walk ns |
| `state_copy.json` | whole-`State` copies | `content_word_loop`=single-edge `state = spare` ns, `trie_terminals`=copy count, `refuse_byte_incomplete`=materialization `state = st` ns, `refuse_not_uniform`=that count, `exact_walk_calls`=initial append ns, `refuse_content_dep`=calibration ns |
| `refusal_o13129.json` | completion filter, initial vs terminal | `refuse_byte_incomplete`=initial `stateAlive` calls, `refuse_content_dep`=initial ns, `alive_dead`=terminal `stateAlive` calls, `refuse_name_state`=terminal ns, `exact_walk_calls`=walk ns in `fill` |
| `refusal_o74466.json` | same | same |

`experiment_exact_children_*.json` are the withdrawn experiment below.

## The p90 string mask (69 unfiltered string calls, means)

* Step = fill + commit; commit is negligible (p90 0.003 ms), so the target
  lives entirely in the fill.
* Fill 1.491 ms: `fillMaskFast` 1.395 ms (94 %), engine/ctypes 0.096 ms;
  inside the fast path the pruned walk is 1.238 ms (83 % of fill, 89 % of
  the fast path) and classification/charges/checks are 0.157 ms.
* Inside the walk: 3 434 one-byte feeds cost 65 us (18.9 ns each), 945
  materializations cost 12 us (2 082 thread frames), 1 364 exact-bit nodes
  cost 43 us, 2 687 chunk releases cost 7.4 us (2.8 ns each). Together
  about 127 us. The bulk is the whole-`State` copy: `buf.states.items[top]
  = buf.spare` after a single-edge feed copies the fixed-size struct (128
  threads x 64 frames, **262 660 bytes**). There are 148 such copies per
  string mask, 5.3 us each, **787 us = 57 % of the 1.376 ms fill**; the
  initial `appendAssumeCapacity(st.*)` is another 15 us per call. The
  compact live-part copy that replaces them is section 17 of the design
  note.
* Structure: 16 780 frame iterations = 14 241 edge visits + 2 539 exits;
  8 893 edge visits (62 %) are pruned siblings (`!subtreeHasExact`), 5 348
  are useful edges (1 899 pure-content skips + 3 434 feeds).

## The heavy VE refusals

`Github_easy---o13129.json`, all four instances abort on the first mask:
the mask is computed on the empty prefix (k=0, tail `""`, the offending
token is the document's first `{"`), the fast path is refused, the plain
`fill` walk visits 124 nodes, and **exactly one terminal `stateAlive`
call** consumes 57.7-57.9 s before `ResourceLimitError`; the mask-level
initial state check never runs.

`Github_hard---o74466.json`, both aborts at k=148: 33 walk nodes, one
terminal `stateAlive` of 113.9 s / 114.8 s; initial check not run.

The normal slow calls of `o74466` show the same shape at scale: 266 077
terminal `stateAlive` calls take 3.31 s of a 4.50 s call (74 %), over
383 675 walk nodes. The refusals and this tail are a completion-filter
problem, not a walk problem.

## Withdrawn experiment: exact-class children index

The walk was changed to iterate a precomputed list of flagged children
(`exact_off`/`exact_edges` in `FastData`) instead of every edge, charging
the pruned siblings in one batch so the work budget and `trie_nodes` stayed
identical. Masks were bit for bit equal (319/319 tests, slice 0
mismatched files).

Result: removing 8 893 of 16 780 iterations changed the walk time by at
most ~0.15 ms (the two runs used different probe overhead; the paired
A/B medians of the slice p90 were 1.738 ms for `642ba1ae` and 1.614 ms for
the experiment, with overlapping ranges; `mask_equiv` totals 0.4-0.5 s for
both). A pruned edge costs a few nanoseconds, so the iteration count is
not the lever. The change was reverted and no artifacts were published.

## Next target

The p90 residual was the whole-`State` copy on the single-edge feed path
(57 % of the fill), now replaced by compact live-part copies (design note
section 17). The remaining p90 is the per-edge state-machine work of the
exact suffix; chain feeding and diagnostic batching are separate later
steps (they need terminal and budget-order guarantees). Reducing trie
traversal or `stateAlive` work does not move the p90.
