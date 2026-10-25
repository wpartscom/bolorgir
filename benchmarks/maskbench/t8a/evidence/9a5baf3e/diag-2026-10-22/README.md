# Diagnostics of the T8a w12 run, 2026-10-22

Everything here is derived from the recorded run
(`benchmarks/local/t8a-work-9a5baf3e-w12`) or from control repeats whose
outputs live outside it. No file under the run's `out/` is newer than
2026-10-19; the run's results were not modified.

| file | what it is |
|---|---|
| `checkpoint-a4b1065.md` | checkpoint of this candidate: build parameters, source and binary hashes, what was verified, and the limits of the claim |
| `run_history.md` | segment table of the engines' run time, the watchdog kill and the resumed segments |
| `wrong_instances_report.json` | per-instance enumeration: 688 accepted_invalid split 617 format-only / 2 documented numeric / 69 unproven, plus 427 uninterpretable records on 109 files |
| `mask_profile.json` | per-call mask profile from the records (`all_mask_us`): per-file totals, percentiles, dataset attribution |
| `mask-fast-path-needs-reachability.md` | the core change: refusal reasons, the relaxation, the proof, the measurements, the tests, and the scope correction of 2026-10-22 |
| `equiv/slice.txt` | the fixed 43-file slice used for the equivalence gate |
| `equiv/orig.json` | that slice on the original wheel: 0 stream mismatches (the harness reproduces the run) |
| `equiv/fix.json` | the same slice on candidate `17294af1…488c` **before** the scope correction: 0 stream mismatches, mask time 25.7 s -> 13.6 s, no call >= 1 s. Superseded: that core admitted two masks the reviewer reproduced, so its outcomes matching says only that the recorded documents did not take the extra tokens |
| `equiv/ve_mask_limit_list.txt`, `equiv/ve_mask_limit_fix.json` | six files that hit `mask computation limit exceeded`, replayed on `17294af1…488c`: 0 mismatches, the refusal at the same token |
| `equiv/after_scope_fix.json` | the 43-file slice on the corrected core `a4b1065…60aa`: 0 stream mismatches, mask 15.9 s, p90 6.0 ms, p99 842 ms; the +2.5 s is `Github_trivial---o41679` (18 calls, now exact) |
| `equiv/repro_after_fix.json` | the reviewer's own reproducer, unchanged except the expected core hash, on the corrected core: 4/4 cases bit-for-bit equal, `fast_used == 0` in every case |
| `equiv/ve_after_scope_fix.json` | the six VE files on the corrected core: 0 mismatched files, the abort at the same token as the run; mask time 293.4 s -> 497.7 s on the same 1515 calls (the unsound acceleration had covered these states), 2 compile-limit files still fail |
| `equiv/timeout_o34343_capped.json` | `Github_hard---o34343` on the candidate, capped at 400 tokens: 25/25 masks refused as non-uniform, step median 947 ms - this timeout family is untouched by the fix |
| `equiv/ve_meter_orig.json`, `equiv/ve_meter_fix.json`, `equiv/ve_meter_compare.md` | the VE replay with the corrected meter: the run's wheel `9a5baf3e` and `a4b1`, 1 515 calls and 19 refusals on both sides, mask 1 876.1 s -> 1 621.3 s, calls without a refusal 714.2 s -> 501.6 s (-29.8 %); refusals are 1.24 % of the 1 534 measurements but 69.1 % of the mask time, which sets p99 and does not explain p90; each result carries the sha256 of the library that was loaded, recorded before and after the leg (for ORIG: during and after, see below); `ve_meter_compare.md` holds all 15 checks, all `ok` |
| `equiv/slice_oneof.json` | the 43-file slice on the `oneOf` candidate `4b567de3...ebbd3`: 0 mismatched files, 684 calls, mask 13.5 s, p50 0.008 ms, p90 6.07 ms, p99 841.8 ms |
| `equiv/repro_oneof.py`, `equiv/repro_oneof.json` | the same reproducer with the `oneOf` candidate's digest: 4/4 masks bit for bit equal, `fast_used == 0` in every case |
| `runner-abort-check/README.md`, `runner-abort-check/marker.json` | the ui5 reset crash replayed with the fixed runner: the pending marker names the phase (`reset`, instance 0, `ResourceLimitError`) while the file keeps its crash class |

Candidate core: `zig-out/lib/libbolorgir.so.0.1.0`, `ReleaseSafe` + strip.
Two artifacts are kept apart. The previous checkpoint `a4b1`
(`a4b10658eb393e347bc08762db5d0247e608be21f0a1aee078887b5d336e60aa`) is the
FIX leg of the VE comparison and the subject of `checkpoint-a4b1065.md`; it
was loaded from the saved copy, not from `zig-out`. The current candidate
with the `oneOf` rule
(`4b567de3b3d42ae5434c22a486ed3eb283edbc1a24965a944f073e900d5ebbd3`) owns
`equiv/slice_oneof.json` and `equiv/repro_oneof.json`; it is byte-identical
to the counter `on` variant. `zig build test`: 316/316 on the current tree.
The earlier `17294af1...488c` is superseded (two wrong masks, section 8 of the
note).

The library hash of every timed leg is recorded in its result: for the FIX
leg before and after it ran; for ORIG, whose library was in use while the
leg ran, during the leg and after it - there is no before-record for ORIG,
and the report says so. All records of one leg agree with each other.

The run-wide working triage is written in Russian. It is git-ignored
(`benchmarks/maskbench/t8a/evidence/**/triage.md`), so it is absent from the
committed package and from `SHA256SUMS`; the same file is also kept at
`benchmarks/local/t8a-work-9a5baf3e-w12/diag/triage.md` (repository content is
English-only). The mask-path part is carried in English by
`mask-fast-path-needs-reachability.md`.

Regenerate the checksums with `sha256sum -c SHA256SUMS` from this directory.
