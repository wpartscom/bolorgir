# ADR-0007 Decision 4: full functional matrix and stored warm acceptance

Artifact:
`a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6`
(1 268 464 bytes, `a87e2f71` checkpoint), the same file in both parts below.
Repository state: `bbaf3e1` on `main`, no tracked changes.

This is a combined conclusion. It has two parts with different origin:

1. the **new** full-scale functional matrix, run on 2026-10-24 for this
   report (six of the seven matrix rows, `--only` marker retained);
2. the **stored** warm acceptance of 2026-10-24, not re-measured, and its
   offline re-verification with the current validator.

The warm row is quoted from its stored measurement; it is not a result of
the functional run.

## 1. Functional matrix (new run)

Command:

```
matrix_adr0007.py --scale full \
  --only zig-three-way fastpath-curated fastpath-fuzz \
         differential-reference cache-onoff bench-gate \
  --lib benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0
```

Generated 2026-10-24T17:10:15Z. `matrix.md` carries the partial-run note:
"`--only` ran 6 of the matrix jobs; the Overall line below covers only
these rows, not the full Decision 4 set." The seventh row is
`warm-acceptance`, covered by part 2.

| job | status | result |
|---|---|---|
| `zig-three-way` | pass | 328/328 Zig tests, three-way fast/trie/brute |
| `fastpath-curated` | pass | 13 curated ABI cases, fast path on/off, lazy/adaptive, workers {1,2,8} |
| `fastpath-fuzz` | pass | 150 randomized schemas, fast path on/off equality |
| `differential-reference` | pass | 600 schemas, 593 walks, 5 412 mask comparisons, 3 257 accepts, 548 rejects, 491 finishes, 0 dead ends |
| `cache-onoff` | pass | 120 schemas, 120 traces, 5 808 mask comparisons, 7 744 accepts, threaded traces 120, 0 mismatches |
| `bench-gate` | pass | 4 probe states, 564 MaskBench input masks, 24 terminals |

Gate verdict (from the written JSON, run id
`bench-gate-69469-1792861803330482194`): `terminal_complete=true`, zero
fill, terminal-fill, input, completion, accept and validation errors,
trace `2ab0c91894b0fbee4e2abcd7bfba088834a2368cb847a248c92ca69ba5d9ab61`,
library SHA256 equal to the artifact above. The trace digest is the same
as in the earlier gate runs, so the terminal accounting changes did not
alter any real comparison.

Files: `matrix.json`
(`657129d8…4972`), `matrix.md` (`6bc490d0…316a`),
`artifacts/bench_gate.json` (`56ae5d60…7956`), `conclusion.json`.

## 2. Warm acceptance (stored measurement, not repeated)

Source package:
`benchmarks/maskbench/t8a/evidence/a87e2f71/terminal-warm-2026-10-24/warm-acceptance/`.

- Measurement window: 2026-10-24T16:28:06Z to 16:29:14Z.
- File: `artifacts/warm_acceptance.json`,
  SHA256 `a334ccff68f9d6e2dc771166d68822f3e82f2deaff4d0ee555f11669d516491f`;
  run id `warm-acceptance-37564-1792859274280580845`.
- Protocol: 5 repeats x 2000 observations per probe, two probes, ten
  `on` legs, threshold 1300 ns, CPU 7 pinned.
- Ten `warm_ext.on` means (ns): 1190.5, 1218.3, 1214.7, 1188.4, 1267.5,
  1126.7, 1264.1, 1254.7, 1167.2, 1085.0 (all <= 1300).
- p50 (ns): 1155, 1187, 1179, 1159, 1236, 1103, 1231, 1220, 1137, 1052.
- Conditions: `recorded=true`, `complete=true`, `ok=true`, requested CPU
  7, governor `powersave`, limits load1 <= 2.0 and CPU pressure avg10
  <= 0.5, 20 legs, start/end affinity and `cpu_now` equal 7, both mapped
  hashes equal to the artifact.
- Matrix row: `warm-acceptance` pass, verdict ok, in
  `warm-acceptance/matrix.json`
  (`20ea3881b2109cfea7158f3aa5c5bf4e5902b069e4c21f0c2fdbff384ed2e4c9`).

Offline re-verification, with the validator of `bbaf3e1` and the
expected protocol passed on the command line (repeats 5, observations
2000, CPU 7, threshold 1300 ns, load1 2.0, PSI 0.5, governor
`powersave`, `--lib` = the artifact):

- File: `benchmarks/maskbench/t8a/evidence/a87e2f71/gate-review-cc11475-2026-10-24/saved-warm-verify/verify-clean.json`,
  SHA256 `9890a6550a0b3e9f2e124923a74a74ee57b68a7ca5a68547896366ec9d97c5e7`.
- Result: `pass=true`, no problems, 10 legs; `source_sha256` equals the
  acceptance file hash above, `expected_lib_sha256` equals the artifact
  hash, `measured_now=false`.

## 3. What this supports, and what it does not

- The Decision 4 evidence named in the plan is complete for this
  artifact: six functional rows at full scale pass, and the warm guard
  passes under the fixed protocol and conditions. All parts name the same
  artifact SHA256.
- The functional run is **partial by construction** (6 of 7 rows); the
  seventh row is the stored warm measurement above, not a new one. The
  two parts were produced at different times on the same artifact.
- This package does not change the earlier performance findings. The warm
  guard (<= 1300 ns) is met by part 2; the ADR's MaskBench TBM p90 target
  (<= 1 ms) was measured at 1.42 ms in the mask fast-path work and remains
  unmet, and the cold permissive-state target has its own record. Closing
  ADR-0007 as a performance decision is therefore an owner decision, not a
  consequence of this report.
- The stored `2d982b60` FAIL is a different, older measurement and is
  not reinterpreted here.

## Provenance

| item | value |
|---|---|
| artifact | `a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6` |
| repository head | `bbaf3e1e55030b9b9b250c7f4f80f21bea6407ed` |
| functional matrix | `artifacts/bench_gate.json`, `matrix.json`, `matrix.md` in this directory |
| warm measurement | `terminal-warm-2026-10-24/warm-acceptance/` (stored 2026-10-24) |
| warm re-verification | `gate-review-cc11475-2026-10-24/saved-warm-verify/verify-clean.json` |
| combined facts | `conclusion.json` in this directory |
| self-check at head | 91/91 (`gate-review-1f819bc-2026-10-24/selfcheck/selfcheck.json`) |
