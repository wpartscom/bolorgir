# ASCII content prefix skip in the exact walk: artifact `642ba1ae`

Artifact: `642ba1aea60e54766eace4120cbed41e7d788d24c326a065a9741a03247a7da4`,
1 524 176 bytes, built with `zig build -Drelease=true -Dstrip=true`
(counters on). Source: `e3b4ea7` on top of `7eb8ed01`. Only `.str` states
skip ASCII content prefixes; K1 name states keep the plain walk.

| file | what it is |
|---|---|
| `names/name-states-after.json`, `names/README.md` | `name_states.py --expect-fast after`: 18/18, K1 on 9, masks bit for bit equal; name masks unchanged |
| `equiv/slice_prefix.json` | the 43-file slice on the pinned library: 0 mismatched files, 684 calls, mask 0.4 s, p50 0.005 ms, p90 1.416 ms, p99 8.522 ms |
| `equiv/prefix_slice_lib.jsonl` | resolved library before and after the slice (both `642ba1ae...7da4`) |
| `profile/slice_calls.json` | the pinned per-call profile: 684 calls, 30 rejections, mask 0.451 s, p50 0.006 ms, p90 1.627 ms, p99 8.793 ms (against 0.730 s / p90 5.312 / p99 12.836 on `7eb8ed01`) |
| `profile/slice_calls_lib.jsonl` | resolved library before and after the profile |
| `equiv/ve_meter_prefix.json` | VE replay of the six mask-limit files: 1 515 completed calls plus 19 refusing calls, 0 outcome mismatches (the replay compares `n_ids`, `consumed`, `rejected_at`, not full masks); mask 1 490.4 s (refusals 1 103.9 s), p50 0.009 ms, p75 8.628 ms, p90 83.729 ms, p99 55.4 s |
| `equiv/ve_meter_compare_prefix.md`, `.json` | strict comparison against the ORIG leg `9a5baf3e...0c41`: all 15 checks `ok`, exit code 0 |
| `equiv/prefix_ve_lib.jsonl` | resolved library before and after the VE leg (both `642ba1ae...7da4`) |
| `equiv/ve_console.txt` | console summaries and exit codes: `mask_equiv.py` exits 1 only on the two expected corpus compile limits, the strict comparator exits 0 |

`zig build test`: 319/319 with a fresh cache, including the per-thread
prefix unit test and the ABI `anyof-prefix-maxlength` case.

The mask_equiv slice improves from 0.9 s / p90 5.933 ms / p99 14.181 ms on
`7eb8ed01` to 0.4 s / p90 1.416 ms / p99 8.522 ms; the pinned per-call
profile of the same slice moves from 0.730 s / p90 5.312 / p99 12.836 to
0.451 s / p90 1.627 / p99 8.793. The `string` group falls from 4.93 to
1.35 ms in the same profile and `string+alive` from 12.70 to 8.81 ms.

VE details. Calls and refusals are equal on both sides (1 515 completed
plus 19 refusing calls per leg), no stream mismatch, no capped file, and
the same two corpus compile limits. The replay compares outcomes, not full
masks; the bit-for-bit mask equality on this artifact is shown by the
differential, ABI and name-state tests, not by the VE meter. Mask time
falls from 1 876.1 s to 1 490.4 s (-385.7 s, -20.6 %); the calls
without a refusal fall from 714.2 s to 386.5 s (-327.7 s, -45.9 %), while
the refusals stay at 1 103.9 s and are now 74.1 % of the mask time. p50 is
unchanged (0.007 -> 0.009 ms), p75 falls from 830.8 ms to 8.6 ms and p90
from 946.7 ms to 83.7 ms; p99 stays in the refusals (57.4 s -> 55.4 s), so
availability is unchanged. The largest per-file gains are `o90904` -117.1 s,
`o74466` -104.6 s, `o12278` -80.1 s, `o54553` -60.2 s, `o13129` -14.9 s and
`codeship-services` -8.8 s.

ADR-0007 is not met yet: the slice p90 is 1.42 ms against 1 ms, and the VE
p90 is still the filtered `string+alive` and K1 name calls.

Lemma and scope are in section 15 of
`mask-fast-path-needs-reachability.md` next to this directory.
