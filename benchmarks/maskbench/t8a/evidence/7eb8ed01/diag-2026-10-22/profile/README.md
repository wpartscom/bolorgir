# Per-call profile of the 43-file slice: what forms the p90

`slice_calls.json` is the per-call record of the slice on the pinned
artifact `7eb8ed0160e210e9cf6f797537c305557f984096bc2fc96d450bf1a00f0e3be0`
(`pkg-k1b`), produced by `benchmarks/maskbench/t8a/diag/slice_calls.py`.
Every `compute_mask` call carries its step time (fill + commit, the span the
slice runner measures), the allowed-token count, the token, the decoded
tail, and the mask-path counters of that call (reset before the fill, read
after it). The counters name the path: fast lemma, refusal reason, trie
nodes, completion checks.

Totals: 43 files, 51 instances, **684 calls**, 30 normal rejections,
**0 errors**, mask 0.730 s, wall 0.9 s. TBM (step): p50 0.007 ms,
p75 0.122 ms, **p90 5.312 ms**, **p99 12.836 ms**, no call >= 1 s. This
reproduces the slice result (0.9 s, p90 5.933 ms, p99 14.181 ms) within
run-to-run noise. The library was recorded before and after this run
(`k1b_profile_lib.jsonl`, both `7eb8ed01...3be0`). `compute_mask` and
`commit_token` exceptions fail the run; a normal `commit_token == False`
is the separate `rejected` count.

## Where the time goes

The mask time by path group (`not_uniform` means the string lemma did not
apply; `name_state` means K1 was tried; `alive` means the completion filter
ran on the call):

| group | calls | time | share | mean | max | extent per call |
|---|---:|---:|---:|---:|---:|---|
| `string` | 69 | 0.340 s | 46.6 % | 4.93 ms | 8.57 ms | 16.8k trie nodes, 1.39k exact terminals |
| `string+alive` | 12 | 0.152 s | 20.9 % | 12.70 ms | 13.18 ms | same extent, 1 394 alive checks |
| `exact+name_state+not_uniform` | 124 | 0.088 s | 12.0 % | 0.71 ms | 4.50 ms | 290 nodes, small masks |
| `name+not_uniform` | 13 | 0.080 s | 11.0 % | 6.16 ms | 6.85 ms | 16.8k nodes, 1.36k exact |
| `exact+name_state+not_uniform+alive` | 69 | 0.040 s | 5.5 % | 0.58 ms | 4.88 ms | small masks |
| `name+not_uniform+alive` | 2 | 0.028 s | 3.8 % | 13.83 ms | 14.09 ms | same extent, 1 364 alive |
| `exact` | 395 | 0.002 s | 0.2 % | 0.004 ms | 0.039 ms | cache hits, no fill |

## The states at the top

| file | inst | k | step | allowed | path | tail + token |
|---|---:|---:|---:|---:|---|---|
| `JME_17` | 0 | 1 | 14.09 ms | 123 189 | name+alive | `{"` + `data` |
| `JME_17` | 0 | 2 | 13.56 ms | 123 186 | name+alive | `{"data` + `":[` |
| `Github_trivial---o41679` | 0 | 3 | 13.18 ms | 123 172 | string+alive | `{"dog":"` + `B` |
| `Snowplow---sp_57` | 0 | 7 | 13.02 ms | 123 172 | string+alive | `..."name":"` + `Product` |
| `BFCL_java_0` | 0 | 8 | 13.00 ms | 123 219 | string+alive | `...controller":"` + `map` |
| `Github_trivial---o41679` | 0 | 4 | 12.92 ms | 123 172 | string+alive | `{"dog":"B` + `uddy` |
| `Snowplow---sp_57` | 0 | 8 | 12.84 ms | 123 172 | string+alive | `...Product` + ` A` |
| `Github_trivial---o41679` | 0 | 5 | 12.66 ms | 123 172 | string+alive | `{"dog":"Buddy` + `"}` |
| `BFCL_java_0` | 0 | 9 | 12.59 ms | 123 219 | string+alive | `...map` + `Controller` |
| `Snowplow---sp_57` | 0 | 9 | 12.58 ms | 123 172 | string+alive | `...Product A` + `"}` |

## Reading

* The p90 is a full-vocabulary string or name mask: 66 of 684 calls take
  >= 5.4 ms, and the p90 position (the 69th slowest) is a `string` mask in
  `Snowplow---sp_349` (instance 0, k 22) at 5.31 ms. The counter extent is
  about 16.8k trie nodes and 1.39k exact-class terminals (tokens with a
  closing quote, a backslash, a control byte or an invalid/truncated UTF-8
  sequence).
* `trie_nodes` counts frame-loop iterations, including the exit iteration
  and iterations before the subtree check; it is an upper bound on the
  nodes examined, **not** the number of `feedBytes` calls. The number of
  feeds is measured below.
* The `string+alive` and `name+alive` groups (14 calls, 24.7 % of the time)
  are the same masks on grammars that need the completion filter: 12.70 and
  13.83 ms against 4.93 and 6.16 ms in the unfiltered groups. The difference
  is a between-state comparison, not an isolated measurement of the filter;
  the probe below shows the alive checks run inside the walk.
* The `exact+name_state` groups (193 calls, 17.5 % of the time) have small
  masks (median 4-7 allowed tokens), mean 0.58-0.71 ms, and are not a
  target.

## Temporary probe: where inside the walk

A temporary probe build (not shipped; source restored afterwards) counted
the actual one-byte `feedBytes` calls of `fillMaskExactPruned`, timed the
walk per call, and counted the edges whose parent path is pure ASCII
content. Counters were repurposed for the probe: `content_word_loop` =
feeds, `refuse_byte_incomplete` = walk ns, `trie_terminals` = ASCII-prefix
feeds. On the same slice (684 calls):

| group | step | walk | feeds | ASCII-prefix feeds | ns/feed |
|---|---:|---:|---:|---:|---:|
| `string` | 6.26 ms | 6.07 ms | 5 227 | 4 450 (85.2 %) | 1 161 |
| `name` (K1) | 7.00 ms | 6.89 ms | 5 344 | 4 561 (85.3 %) | 1 290 |

* The walk is about 96 % of the call; the content classification, the
  terminal handling outside the walk and the commit are the rest.
* 85 % of the feeds are pure ASCII-content prefix edges. Skipping the
  ASCII prefix and feeding only from the first special byte therefore
  targets the dominant part of the walk. The exact gain depends on the
  cost of constructing the post-prefix state and on how many suffixes
  still need feeding; it must be measured on the implemented path.
* The filtered groups run their `stateAlive` checks inside this walk
  (1 394 calls for a string mask), which is why they sit about 8 ms above
  the unfiltered group.

## Candidate next optimization

Start with **ASCII prefixes of `.str` values**, as proposed in review: the
lemma must advance the code-point count of every live thread, drop the
threads that pass their own `maxLength`, and hand the exact path the state
at the first special byte (quote, backslash, control, invalid UTF-8).
Names (K1) need the full key bytes and buffer ownership; incomplete UTF-8
must start the suffix at the sequence lead; the quote and the suffix keep
the normal parse and the completion check. None of these are in the first
step. After the bit-for-bit mask comparison, the same slice and VE apply.

Notes: one run per build, same pinned library; run-to-run p90 spread on the
slice is about 0.5 ms. In the shipped build `content_word_loop` is not
instrumented, so the content-class path is inferred from `content_masks`,
`content_memcpy` and `content_cache_hits`; the probe numbers above come
from the temporary build only.
