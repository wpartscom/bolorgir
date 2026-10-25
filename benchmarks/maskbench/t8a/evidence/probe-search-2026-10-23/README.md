# Temporary probe: the completion search (`stateAlive`)

Instrumented build on top of `dc415942` (source `3bb4fe2`), not shipped;
`src/complete.zig` was restored afterwards. The counters are repurposed
because the diagnostic struct layout is read by name from Python:

| counter | meaning |
|---|---|
| `content_word_loop` | `stateAlive` total ns |
| `refuse_byte_incomplete` | `search` entries (after the `canEnd` fast path) |
| `refuse_content_dep` | memo hits |
| `refuse_not_uniform` | stale-stamp drops |
| `refuse_name_state` | `certifyState` calls |
| `exact_walk_calls` | `certifyState` ns |
| `alive_dead` | token feed attempts in the search |
| `content_memcpy` | feeds pruned by Parse |
| `content_cache_hits` | unmemoized search nodes |
| `trie_terminals` | tier budget spent |

`refusal_o13129.json` is the four aborts of `Github_easy---o13129.json`
(replayed with `slice_calls.py`), `refusal_o13129_tiers.json` the same file
with a second mapping that separates the `capped` reasons and the
post-budget work:

| counter | meaning |
|---|---|
| `content_word_loop` | `stateAlive` total ns |
| `refuse_byte_incomplete` | `search` entries |
| `refuse_content_dep` | `search` entries with budget 0 |
| `refuse_not_uniform` | feeds attempted with budget 0 |
| `refuse_name_state` | `certifyState` calls |
| `exact_walk_calls` | `certifyState` ns |
| `alive_dead` | feeds |
| `content_memcpy` | feeds pruned by Parse |
| `content_cache_hits` | `capped`: budget |
| `trie_terminals` | `capped`: depth |
| `content_masks` | `capped`: scratch pool full |

`slice_filtered.json` covers the 83 calls of the 43-file slice that ran the
completion filter (first mapping); `certificate_keys.json` repeats those
calls with a key probe at the top-level certification:

| counter | meaning |
|---|---|
| `content_word_loop` | `stateAlive` ns |
| `refuse_name_state` | top-level `certifyState` calls |
| `exact_walk_calls` | `certifyState` ns |
| `trie_terminals` | full-key build ns |
| `alive_dead` | full-key builds |
| `refuse_byte_incomplete` | repeated full keys in the same fill |

The key is the existing `Cache.keyOf` (state byte image plus the
chunk-content blob), tracked per fill.

## The refusal: three tiers, then a doomed enumeration

One aborting call (`o13129`, first mask on the empty prefix) takes
**58.4 s**, and `stateAlive` accounts for all of it. Inside:

* three tiers ran: budget 32, then 512, then 8 192 (sum 8 736 spent), with
  the depth cap growing 32 -> 256 -> 512; the third tier hits
  `MAX_DEPTH_REACH` and returns `ResourceLimit`;
* 8 738 `certifyState` calls, all undecided, cost 8 ms in total;
* **52 712 083 `search` entries** and **69 275 302 token feeds** (16.6 M
  of them pruned by Parse), against a spent budget of 8 736;
* memo hits 1, stale drops 2, unmemoized nodes 0.

The second run separates the reasons: of the 52 712 083 entries,
**52 703 342 (99.98 %) returned at budget 0**; of the 69 275 302 feeds,
**69 219 288 (99.92 %) were attempted at budget 0**; the capped reasons are
budget 52 703 342, depth 5 and **scratch pool full 1 046 777 395**. The pool
is bounded at `max_states = 256`, below the last tier's cap of 512, so most
of the post-budget enumeration bounces off the pool boundary before any
feed - cheap per attempt, a billion times over.

So the time is not certification and not memoization: after a tier's budget
is gone, the DFS keeps enumerating the vocabulary at every remaining
branch, feeding tokens whose recursive `search` can only return `capped`.
Each budgeted state also re-scans the vocabulary up to six times (one pass
per ordering class). At 128 256 tokens that enumeration is the whole
58 s; the budget itself is spent in the first 8 736 expansions.

## The slice: certification, not search

On the 83 filtered calls of the 43-file slice the filter takes 114.4 ms of
the 162.3 ms step (70 %). `search` never runs (0 entries): every call is
settled by `certifyState`, which is called **45 583** times (about 549 per
call, 2.5 us each) and always decides without search.

Those certifications repeat heavily. Counting with per-row correction
against the ordinary profile of the same calls (`dc415942/profile/
slice_calls.json`) gives **45 514** certifications and key builds, of which
**41 896 (92.05 %) hit a full key already certified in that fill**; only
**3 618** keys are distinct. Building the full key (the existing
`Cache.keyOf`, state image plus the chunk-content blob) costs **0.051 us**,
against 2.495 us for the certificate. A certificate cache keyed by the full
key would therefore remove about 92 % of the 113.6 ms certification time on
this slice, and the key is 49x cheaper than the verdict it guards. The
instrumentation is saved as `probe_certificate_keys.patch`; three counters
(`content_word_loop`, `refuse_byte_incomplete`, `refuse_content_dep`) have
no native events on these calls, the other three are corrected per call
(`alive_dead`, `refuse_name_state`, `trie_terminals`).

## What this means for the next fix

Two different mechanisms, one per workload:

* slice: the per-terminal certification cost; a cheaper certificate or a
  cache of certification verdicts keyed by the state image would help;
* refusal: the post-budget continuation. Cutting it (return `capped` as
  soon as the tier budget is empty) would remove tens of millions of feeds,
  but it also removes the free `canEnd` witnesses found after exhaustion,
  so it can turn a completion that currently succeeds into a
  `ResourceLimit`. That is a semantics decision for the owner and needs a
  VE comparison of the refusal counts, not just a timing run.
