# Compact live-part state copies: artifact `dc415942`

Artifact: `dc415942c5f3c673bd73fe8756f8bd0623d93878179c02c1685d3b31ebbd2330`,
1 262 688 bytes (263 KB smaller than `642ba1ae`), built with
`zig build -Drelease=true -Dstrip=true` (counters on). Source: `3bb4fe2` on
top of the ASCII prefix skip. The exact walks copy only the live part of a
parser state instead of the fixed 262 660-byte struct; chunk ownership is
unchanged.

| file | what it is |
|---|---|
| `names/name-states-after.json`, `names/README.md` | `name_states.py --expect-fast after`: 18 cases, no failures, K1 on 9, 17/17 comparable masks bit for bit equal |
| `equiv/slice_compact.json` | the 43-file slice, `mask_equiv`: 684 calls, 0 mismatched files, 0 errors, mask 0.3 s, p50 0.005 ms, p90 **0.610 ms**, p99 7.844 ms |
| `equiv/compact_slice_lib.jsonl` | resolved library before and after the slice (both `dc415942...5b31`) |
| `equiv/warm_cache.json`, `equiv/warm_repeats/` | `bench_adr0007.py` on the pinned library with per-child provenance (`loaded.sha256` = `dc415942...5b31` verified in `/proc/self/maps`, mismatch fails the leg): warm C-extension hit on p50 1.061-1.326 us, mean 1.112-1.363 us over four runs; cold 16.2 / 17.5 ms off -> 4.37 / 4.31 ms on, work ops 383 647 -> 20 744 |
| `equiv/ve_meter_compact.json` | VE replay of the six mask-limit files: 1 515 completed calls plus 19 refusing calls, 0 outcome mismatches; mask 1 375.4 s (refusals 1 109.2 s), p50 0.009 ms, p75 7.502 ms, p90 49.631 ms, p99 56.0 s |
| `equiv/ve_meter_compare_compact.md`, `.json` | strict comparison against the ORIG leg `9a5baf3e...0c41`: all 15 checks `ok`, exit code 0 |
| `equiv/compact_ve_lib.jsonl` | resolved library before and after the VE leg (both `dc415942...5b31`) |
| `equiv/ve_console.txt` | console summaries and exit codes: `mask_equiv.py` exits 1 only on the two expected corpus compile limits, the strict comparator exits 0 |
| `profile/slice_calls.json`, `profile/README.md` | pinned per-call profile with the fill/commit split: fill p90 0.608 ms, commit p90 0.002 ms; `string` group 1.35 -> 0.49 ms, K1 6.16 -> 0.92 ms |

`zig build test`: 319/319.

Defect and fix: section 17 of `mask-fast-path-needs-reachability.md` next
to this directory. The spare swap after a single-edge feed copied the whole
`State` (128 threads x 64 frames) 148 times per string mask at 5.3 us each,
787 us of a 1.376 ms fill. `copyState`/`moveState`/`copyStateRetain`/
`initStateInto` replace it; the structural-tag walk had the same defect in
`feedByteInto` and the pool entry.

ADR-0007's TBM p90 target (<= 1 ms) is met on the 43-file slice
(fill p90 0.608 ms). On the six mask-limit files the p90 falls from 946.7 ms (run
wheel) and 83.7 ms (`642ba1ae`) to 49.6 ms, still above 1 ms: that corpus's
p90 is the filtered string and name states, whose cost is the completion
filter, not the state copy. The filter is a separate target.

VE details. Calls and refusals are equal on both sides (1 515 completed
plus 19 refusing calls), no stream mismatch, no capped file, the same two
corpus compile limits, and all 15 strict checks pass. Mask time falls from
1 876.1 s to 1 375.4 s (-26.7 %); the calls without a refusal from 714.2
to 266.2 s (-62.7 %); the refusals from 1 161.9 to 1 109.2 s (80.6 % of
the mask time). p75 830.8 -> 7.5 ms, p90 946.7 -> 49.6 ms, p99 stays in the
refusals (57.4 -> 56.0 s). Against `642ba1ae` the same corpus moves
1 490.4 -> 1 375.4 s and the p90 83.7 -> 49.6 ms. The largest per-file
gains: `o74466` -133.1 s, `o90904` -133.0 s, `o54553` -105.0 s, `o12278`
-79.2 s, `codeship-services` -36.3 s.

ADR-0007 warm guard, measured with `bench_adr0007.py` (HF offline, pinned
library) and bound to the artifact: the parent puts the exact file under
test into `LD_PRELOAD` through a space-free temporary directory (a
directory holding only `libbolorgir.so.0.1.0` does not satisfy the
`libbolorgir.so.0` SONAME the extension asks for), passes its SHA256 in
`BLG_EXPECT_LIB_SHA256`, and each child reads the mapped `libbolorgir` from
`/proc/self/maps`, hashes it and fails on a mismatch; the parent rejects a
leg whose `loaded.sha256` differs. All legs report the checkpoint path and
`dc415942...5b31`. The C-extension cache hit (`on`) takes p50 1.061 /
1.326 / 1.190 / 1.132 / 1.074 / 1.197 us and mean 1.112 / 1.363 / 1.229 /
1.179 / 1.102 / 1.230 us across the full run and three repeats (two probes
per run); the fast path off leg is p50 1.119-1.249 us. **Seven of the eight
on-legs pass the `<= 1.3 us` guard on both the median and the mean; one
leg, the in-object probe of the full run, is at p50 1.326 us and mean
1.363 us and exceeds it.** The result is at the threshold with one
exceedance: a hard-guard verdict needs an aggregation rule over repeats
fixed in advance (for example the median of per-run means), which is not
established here. The historical 0.85 us baseline is from the original
measurement machine. The cold probe measures 16.2 / 17.5 ms off against
4.37 / 4.31 ms on (stretch target <= 10 ms), work ops 383 647 -> 20 744.
