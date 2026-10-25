# Per-call profile of the prefix-skip artifact

`slice_calls.json` is `slice_calls.py --files
benchmarks/maskbench/t8a/evidence/9a5baf3e/diag-2026-10-22/equiv/slice.txt`
run against the pinned package `pkg-prefix`, i.e. the library
`642ba1aea60e54766eace4120cbed41e7d788d24c326a065a9741a03247a7da4`; the
`before`/`after` snapshots in `slice_calls_lib.jsonl` agree.

| metric | `7eb8ed01` | `642ba1ae` |
|---|---:|---:|
| mask time, 684 calls | 0.730 s | 0.451 s |
| p50 | 0.007 ms | 0.006 ms |
| p90 | 5.312 ms | 1.627 ms |
| p99 | 12.836 ms | 8.793 ms |
| `string` group, 69 calls | 4.934 ms mean | 1.349 ms mean |
| `string+alive`, 12 calls | 12.704 ms mean | 8.805 ms mean |
| `name+not_uniform`, 13 calls | 6.162 ms mean | 6.421 ms mean |
| `exact+name_state+not_uniform`, 123-124 calls | 0.709 ms mean | 0.772 ms mean |

Both legs are the same tool on the same slice. The `name+not_uniform` and
`exact+name_state` differences are run noise: the prefix skip is disabled
for K1 and their counter footprint is unchanged. The `string+alive` group
is the completion-filter tail (per-terminal `stateAlive`); the prefix skip
removes the feeds, not that check.
