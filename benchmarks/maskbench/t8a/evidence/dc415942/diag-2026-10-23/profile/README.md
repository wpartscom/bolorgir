# Per-call profile of the compact-copy artifact

`slice_calls.json` is `slice_calls.py --files
benchmarks/maskbench/t8a/evidence/9a5baf3e/diag-2026-10-22/equiv/slice.txt`
against the pinned package `pkg-compact2`, library
`dc415942c5f3c673bd73fe8756f8bd0623d93878179c02c1685d3b31ebbd2330`; the
`before`/`after` snapshots in `slice_calls_lib.jsonl` agree. Unlike the
earlier profile, this one records fill and commit separately.

Totals: 43 files, 51 instances, 684 calls, 30 normal rejections, **0
errors**, mask 0.273 s, wall 0.5 s. Fill p50 0.004 ms, p75 0.090 ms,
**p90 0.608 ms**, p99 7.923 ms; commit p90 0.002 ms. Against `642ba1ae`
(same tool, paired runs) the fill p90 is 1.60 ms and the mask 0.4-0.5 s,
so the compact copy removes about 60 % of the p90.

| group | calls | fill total | mean |
|---|---:|---:|---:|
| `string` | 69 | 33.8 ms | 0.49 ms (was 1.35 ms) |
| `name+not_uniform` (K1) | 13 | 11.9 ms | 0.92 ms (was 6.16 ms) |
| `string+alive` | 12 | 93.7 ms | 7.81 ms (was 8.81 ms) |
| `exact+name_state+not_uniform` | 126 | 76.6 ms | 0.61 ms (was 0.77 ms) |
| `exact+name_state+not_uniform+alive` | 69 | 38.3 ms | 0.55 ms |
| `name+not_uniform+alive` | 2 | 16.6 ms | 8.3 ms |
| `exact` | 393 | 0.9 ms | 0.002 ms |

The unfiltered string and K1 masks are the two groups the whole-`State`
copy dominated: 148 copies of 262 660 bytes per string mask at 5.3 us each
(787 us of a 1.376 ms fill) before the change. The filtered groups move
little because their time is the completion filter, not the walk; they are
a separate target.

ADR-0007's TBM p90 target (<= 1 ms) is met on this slice (0.61 ms). The
warm path is untouched.
