# Per-fill certificate cache: artifact `69a24712`

Artifact: `69a2471245699536e2b95d5b67f6c1070e0b8891542f0ef104b8f045daf426da`,
1 266 096 bytes (3 408 bytes larger than `dc415942`), built with
`zig build -Drelease=true -Dstrip=true` (counters on). Source: the
certificate cache on top of the compact copies. Only the top-level
`stateAlive` certification consults the cache, only proved `alive`/`dead`
verdicts are stored, the map is cleared at every `fill`, holds owned copies
of the full `Cache.keyOf` key and is capped at 4 096 entries; a full map or
a failed insert falls back to ordinary certification.

| file | what it is |
|---|---|
| `names/name-states-after.json`, `names/README.md` | `name_states.py --expect-fast after`: 18 cases, `failures: []`, K1 on 9 |
| `equiv/slice_cert.json` | the 43-file slice, `mask_equiv`: 684 calls, 0 mismatched files, 0 errors, mask 0.2 s, p50 0.007 ms, p90 **0.697 ms**, p99 4.166 ms |
| `equiv/cert_slice_lib.jsonl` | resolved library before and after the slice (both `69a24712...26da`) |
| `equiv/ab/compact2_run{1,2,3}.json`, `equiv/ab/cert_run{1,2,3}.json` | interleaved A/B of `dc415942` against this artifact on the same slice: mask 0.3 s vs 0.2 s, p90 0.739/0.729/0.787 vs 0.736/0.709/0.747 ms, p99 **8.292/8.180/9.066 vs 4.250/4.114/4.266 ms**, 0 mismatched files and 0 errors on both sides of every pair |
| `equiv/ve_meter_cert.json` | VE replay of the six mask-limit files: 1 515 completed calls plus the same 19 refusing calls as the ORIG leg, mask 1 273.1 s (refusals 1 147.4 s), p50 0.008 ms, p75 1.287 ms, p90 30.921 ms, p99 58.0 s |
| `equiv/ve_meter_compare_cert.md`, `.json` | strict comparison against the ORIG leg `9a5baf3e...0c41`: all 15 checks `ok`, exit code 0; orig 1 876.1 s -> fix 1 273.1 s with the same 19 refusals and 0 mismatched files |
| `equiv/cert_ve_lib.jsonl` | resolved library before and after the VE leg (both `69a24712...26da`) |
| `equiv/ve_console.txt` | console summaries and exit codes for the slice leg, the VE leg and the strict comparator |
| `equiv/warm_repeats/run{1,2,3}.json` | `bench_adr0007.py --cold-repeats 3 --skip-maskbench` on the pinned library: every child reports `loaded.sha256` = `69a24712...26da` from `/proc/self/maps`, so all warm numbers belong to this artifact. C-extension hit p50 1.053-1.287 us, mean 1.075-1.327 us; cold content 14.5-16.0 ms off -> 3.85-4.89 ms on, work ops 383 647 -> 20 744 |
| `profile/slice_calls_run{1,2}.json`, `profile/README.md` | pinned per-call profile with the fill/commit split: mask 0.219/0.220 s, fill p90 0.661/0.737 ms, 0 errors |

`zig build test`: 321/321 (the cache's hit, reset, cap and grammar-change
cases and the mask-level A/B with `cert_cache_off` included).

Design and measurements: section 19 of
`mask-fast-path-needs-reachability.md` next to this directory.

Correctness argument: a cache hit returns the verdict a fresh certification
of the same key would return, because only `.alive` and `.dead` are stored;
`undecided`, refusals and anything the bounded search touched are never
inserted. The key is the full `Cache.keyOf` string (state image plus the
chunk-content blob), copied into the map, so neither a recycled state slot
nor a new chunk handle can alias an old verdict; the map dies with the fill,
so a grammar change between fills cannot survive either.

Superseded: this artifact's cache can starve required scratch under a
session memory limit (a stored key, the key-building buffer and the table
retained by `reset` each reproduced `ResourceLimit` for a call that settles
alive without the cache). The release rules and the four boundary
regressions are in `evidence/2e863e86/diag-2026-10-23/`; the slice and VE
numbers above still describe this artifact and are re-measured there.
