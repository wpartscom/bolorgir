# K1 candidate `7eb8ed01`, with the arena fix

Artifact: `7eb8ed0160e210e9cf6f797537c305557f984096bc2fc96d450bf1a00f0e3be0`,
1 522 800 bytes, built with `zig build -Drelease=true -Dstrip=true`
(counters on). Source: `644f96b` on top of `e5849a4`.

This is the fixed build. The previous candidate `7a4dcbd2` filled the
AnyJSON mark after `Builder.finish`, aliased `pat_len`, and failed the VE
replay on `Github_hard---o74466` (12 field-mismatch records across 5
instances); its package under `evidence/7a4dcbd2/` records the failure.
Here the bitset is allocated and filled before `finish`, and the regression
test compiles `{"type":"object"}` at depths 1, 4 and 64.

| file | what it is |
|---|---|
| `names/name-states-after.json`, `names/README.md` | `name_states.py --expect-fast after`: 18/18, K1 on 9, masks bit for bit equal; open name states 18.4-20.2 ms |
| `equiv/slice_k1b.json` | the 43-file slice on the pinned library: 0 mismatched files, 684 calls, mask 0.9 s, p50 0.011 ms, p90 5.933 ms, p99 14.181 ms |
| `equiv/k1b_slice_lib.jsonl` | resolved library before and after the slice (both `7eb8ed01...3be0`) |
| `equiv/ve_meter_k1b.json` | VE replay of the six mask-limit files: 1 515 calls, 19 refusals, 0 mismatches, mask 1 545.7 s, p50 0.009 ms, p75 12.974 ms, p90 89.263 ms, p99 58.4 s |
| `equiv/ve_meter_compare_k1b.md`, `.json` | strict comparison against the ORIG leg `9a5baf3e...0c41`: all 15 checks `ok`, exit code 0 |
| `equiv/k1b_ve_lib.jsonl` | resolved library before and after the VE leg (both `7eb8ed01...3be0`) |
| `equiv/ve_console.txt` | console summaries and exit codes: `mask_equiv.py` exits 1 only on the two expected corpus compile limits, the strict comparator exits 0 |
| `profile/slice_calls.json`, `profile/README.md` | per-call profile of the slice with the fixed tool (684 calls, 30 normal rejections, 0 errors, p90 5.312 ms, p99 12.836 ms): the p90 position is a full-vocabulary string mask; the temporary walk probe shows 5 227 feeds, 85.2 % of them ASCII-prefix edges, and the walk at about 96 % of the call |
| `profile/probe_walk.json` | the temporary probe run; counters are repurposed as documented in `profile/README.md` (`content_word_loop` = feeds, `refuse_byte_incomplete` = walk ns, `trie_terminals` = ASCII-prefix feeds) |

VE details. Calls and refusals are equal on both sides (1 515 and 19), no
stream mismatch, no capped file, and the same two corpus compile limits.
Mask time falls from 1 876.1 s to 1 545.7 s (-330.4 s, -17.6 %); the calls
without a refusal fall from 714.2 s to 390.1 s (-324.1 s, -45.4 %), while
the refusals stay at 1 155.6 s and are now 74.8 % of the mask time. p75
falls from 830.8 ms to 12.97 ms and p90 from 946.7 ms to 89.3 ms; p99 stays
in the refusals (57.4 s -> 58.4 s), so availability is unchanged. The
largest per-file gains are `o90904` -114.8 s, `o74466` -97.9 s, `o12278`
-62.5 s and `o54553` -55.5 s; `codeship-services` is +2.8 s.

`zig build test`: 318/318 (fresh cache), including the arena regression and
the K1 ABI table. Commit: `644f96b`.

Lemma and scope are in section 14 of
`mask-fast-path-needs-reachability.md` next to this directory.
