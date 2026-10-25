# ADR-0007 Decision 4: full functional matrix (four-configuration campaign) and stored warm acceptance

Artifact:
`a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6`
(1 268 464 bytes, `a87e2f71` checkpoint), the same file in both parts.
Repository state: `c770469` on `main` (the commit that extends the
differential campaign), no tracked changes.

This conclusion supersedes
`full-functional-bbaf3e1-2026-10-24/CONCLUSION.md` in two points: the
differential campaign now really replays fast/cache on/off, and the
performance paragraph uses the `a87e2f71` measurements.

## 1. Coverage: the differential campaign replays configurations

The previous differential row created one lazy context (cache off, fast
path at its default) for the whole campaign. It now generates each
schema and walk seed once and replays it in the four configurations
Decision 4 names. Each configuration publishes its own counters and a
SHA256 of the executed trace (accepted and rejected tokens, finishes,
dead ends, skipped steps). The counters and trace digests must be
identical; the row fails otherwise.

| configuration | mode | cache | fast path | schemas | walks | mask cmps | accepts | rejects | finishes | dead ends | trace |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `cache_off-fast_on` | lazy (0) | 0 | on (1) | 600 | 593 | 5 412 | 3 257 | 548 | 491 | 0 | `11133709…` |
| `cache_off-fast_off` | lazy (0) | 0 | off (2) | 600 | 593 | 5 412 | 3 257 | 548 | 491 | 0 | `11133709…` |
| `cache_default-fast_on` | adaptive (1) | default | on (1) | 600 | 593 | 5 412 | 3 257 | 548 | 491 | 0 | `11133709…` |
| `cache_default-fast_off` | adaptive (1) | default | off (2) | 600 | 593 | 5 412 | 3 257 | 548 | 491 | 0 | `11133709…` |

`equal_across_configs=true`; the full digest is
`11133709c850a4a9…` in all four rows. The counts equal the previous
single-configuration run (which was `cache_off-fast_on`), so the
replay did not change the workload, only its coverage. Artifact:
`artifacts/fuzz-artifacts/py_fuzz_differential.json`
(`conclusion.json` records its SHA256). Workers stay at the context
default in this row; `fastpath-curated` covers `max_workers` 1/2/8,
and `cache-onoff` covers the cache modes on its own 120-schema workload.

## 2. Functional matrix (new full-scale run)

```
--scale full \
--only zig-three-way fastpath-curated fastpath-fuzz \
       differential-reference cache-onoff bench-gate
```

| job | status | result |
|---|---|---|
| `zig-three-way` | pass | 328/328 Zig tests |
| `fastpath-curated` | pass | 13 curated cases, fast path on/off, lazy/adaptive, workers {1,2,8} |
| `fastpath-fuzz` | pass | 150 randomized schemas, fast path on/off |
| `differential-reference` | pass | 600 schemas x 4 configurations (table above) |
| `cache-onoff` | pass | 120 schemas, 5 808 mask comparisons, 7 744 accepts, 120 threaded traces, 0 mismatches |
| `bench-gate` | pass | 4 probe states, 564 MaskBench masks, 24 terminals |

`matrix.md` keeps the partial-run note: "`--only` ran 6 of the matrix
jobs; the Overall line below covers only these rows, not the full
Decision 4 set." The seventh row is the stored warm acceptance in part 3.

Gate verdict (written JSON, run id
`bench-gate-78227-1792862393428171120`): `terminal_complete=true`, zero
fill, terminal-fill, input, completion, accept and validation errors,
trace `2ab0c91894b0fbee4e2abcd7bfba088834a2368cb847a248c92ca69ba5d9ab61`,
library SHA256 equal to the artifact above.

## 3. Warm acceptance (stored measurement, not repeated)

Source:
`benchmarks/maskbench/t8a/evidence/a87e2f71/terminal-warm-2026-10-24/warm-acceptance/`.

- Measurement window 2026-10-24T16:28:06Z to 16:29:14Z; file
  `artifacts/warm_acceptance.json`, SHA256
  `a334ccff68f9d6e2dc771166d68822f3e82f2deaff4d0ee555f11669d516491f`;
  run id `warm-acceptance-37564-1792859274280580845`.
- Protocol: 5 repeats x 2000 observations, two probes, ten `on` legs,
  threshold 1300 ns, CPU 7 pinned.
- Ten `warm_ext.on` means (ns): 1190.5, 1218.3, 1214.7, 1188.4, 1267.5,
  1126.7, 1264.1, 1254.7, 1167.2, 1085.0; p50 (ns): 1155, 1187, 1179,
  1159, 1236, 1103, 1231, 1220, 1137, 1052.
- Conditions: `recorded/complete/ok=true`, CPU 7, `powersave`, load1
  <= 2.0, CPU pressure avg10 <= 0.5, 20 legs, start/end affinity and
  `cpu_now` 7, both mapped hashes equal to the artifact.
- Matrix row `warm-acceptance`: pass, verdict ok
  (`warm-acceptance/matrix.json`,
  `20ea3881b2109cfea7158f3aa5c5bf4e5902b069e4c21f0c2fdbff384ed2e4c9`).

Offline re-verification with the current validator and the expected
protocol on the command line (repeats 5, observations 2000, CPU 7,
threshold 1300 ns, load1 2.0, PSI 0.5, `powersave`, `--lib` = artifact):

- `gate-review-cc11475-2026-10-24/saved-warm-verify/verify-clean.json`,
  SHA256 `9890a6550a0b3e9f2e124923a74a74ee57b68a7ca5a68547896366ec9d97c5e7`.
- `pass=true`, no problems, 10 legs; `source_sha256` equals the acceptance
  file hash, `expected_lib_sha256` equals the artifact hash,
  `measured_now=false`.

## 4. Performance: corrected p90 statement

The 1.42 ms p90 in the previous conclusion belongs to artifact
`642ba1ae`; it is not an `a87e2f71` result. For `a87e2f71`:

- diagnostic 43-file slice
  (`diag-2026-10-23/equiv/slice.json`,
  SHA256 `1b333ab940fce722ccae0aaa75695f760bc885b10b216bcf29dd055ed6b8b6d2`):
  684 calls, p90 **0.758 ms**, p50 0.006 ms, p99 4.147 ms;
- pinned BFCL gate in this run: 564 on-call TBM samples, p50 138.778 us,
  **p95 796.543 us** (the earlier gate run recorded p95 907.087 us, also
  below 1 ms), so p90 of the same measured set is below 1 ms.

These are statements about the measured workloads (the 43-file
diagnostic slice and the pinned BFCL gate). They do not transfer to the
full T8a corpus or to the VE latency tail. The gate's cold probe values
are single observations from a functional run and are not a dedicated
performance campaign.

## 5. What is complete, what remains

- Decision 4 correctness evidence is complete for `a87e2f71`: the six
  functional rows at full scale, with the differential campaign now
  covering cache off/on x fast path on/off on identical schemas and
  traces, plus the stored warm acceptance and its offline verification.
- The functional report is partial by construction (6 of 7 rows); the
  seventh row is the stored warm measurement, not a new one.
- The ADR also requires the Decision 4 suite green in CI for its go/no-go
  verdicts. That CI confirmation and the formal closure decision are the
  remaining steps before R6/T8a; this package only supplies the local
  evidence.

## Provenance

| item | value |
|---|---|
| artifact | `a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6` |
| repository head | `c770469` (differential replay commit), no tracked changes |
| functional matrix | `matrix.json`, `matrix.md`, `artifacts/bench_gate.json`, `artifacts/fuzz-artifacts/` in this directory |
| warm measurement | `terminal-warm-2026-10-24/warm-acceptance/` (stored 2026-10-24) |
| warm re-verification | `gate-review-cc11475-2026-10-24/saved-warm-verify/verify-clean.json` |
| combined facts | `conclusion.json` in this directory |
| superseded report | `full-functional-bbaf3e1-2026-10-24/` (see its `CORRECTION.md`) |
| self-check at head | 91/91 (`gate-review-1f819bc-2026-10-24/selfcheck/selfcheck.json`) |
