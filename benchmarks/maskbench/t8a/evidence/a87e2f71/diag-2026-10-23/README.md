# Certificate cache: every mask builder releases it; harness verdicts: artifact `a87e2f71`

Artifact: `a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6`,
1 268 464 bytes. Built with `zig build -Drelease=true -Dstrip=true`
(counters on). Source: the working tree on top of `1b99c3f`.

## What this artifact adds

The previous artifact wrapped the exact `fillMask` walk, but the two
builders called directly from `c_api.zig` were still outside the rule:

- `fillMaskFast` certifies its initial state and stores a certificate
  before `fillMaskExactPruned` asks for its state pool. On the reviewer's
  K1-eligible fixture (`properties.x` with pattern `^a*$`, prefix `{"`, a
  1 024-byte name token) the cache-off leg peaks at 1 313 938 B, the
  cache-on leg at 1 314 402 B; at the cache-off limit the cache-on fill
  returned `OutOfMemory` with one certificate held. `fillMaskFast` now
  releases the optional memory, restores the work counter and the
  per-fill search budget, and retries once;
- `structtag.fillMask` owns a separate `Pool` and content certificate
  cache, and its `pool.ensure`/parser feeds can fail while a retained
  certificate table is charged. It uses the same release-and-retry rule;
  `Cache.resetForVariant` clears entries on a content-variant switch
  without re-enabling a release, so the retry runs with the cache off.

The per-fill reset moved from the builder bodies into the wrappers, so a
retry cannot re-create the memory it just freed.

The new regressions are
`fast path: the certificate cache cannot fail a fast fill` (owner fixture,
word 7, 9 charged operations on both arms, cache empty afterwards) and
`structtag: the certificate cache cannot fail a mask fill` (64 retained
certified content states at the cache-off peak). Both fail with the retry
removed. `zig build test`: 328/328 (Debug and ReleaseSafe).

The acceptance harness now fails on a failed warm protocol: it writes
`status=FAIL`, publishes the problems and exits 1. The matrix re-reads
`warm_acceptance.pass` from the written JSON and fails the row on a
missing or false verdict. The `bench-gate` row no longer passes
`--skip-maskbench` at any scale; it runs the pinned MaskBench slice and
its verdict requires a positive `gate.maskbench_states`. Only the separate
warm row keeps the MaskBench skip.

## Measurements

| file | what it is |
|---|---|
| `names/name-states-after.json`, `names/README.md` | `name_states.py --expect-fast after`: 18 cases, `failures: []`, K1 on 9 |
| `equiv/slice.json` | the 43-file slice, `mask_equiv`: 684 calls, 0 mismatched files, 0 errors, mask 0.2 s, p50 0.006 ms, p90 **0.758 ms**, p99 4.147 ms |
| `equiv/fix_slice_lib.jsonl` | resolved library at the slice start (the checkpoint `a87e2f71...15e6`) |
| `equiv/ve_meter.json` | VE replay of the eight-file protocol (six mask-limit files plus the two expected compile-limit files): 1 515 completed calls and the same 19 refusals as ORIG, mask **1 302.3 s** (refusals 1 167.7 s), p50 0.013 ms, p75 1.357 ms, p90 34.713 ms, p99 58.7 s; 0 mismatched files, the 2 expected compile limits; the leg ran at load average ~2 |
| `equiv/ve_meter_compare_fix.json`, `.md` | strict comparison against ORIG `9a5baf3e...0c41`: all **15 checks `ok`**, exit 0; 1 876.1 s -> 1 302.3 s, 0 mismatched files. The previous artifact recorded 1 229.6 s on a quieter machine; the stream comparison is exact and the wall time is informational |
| `equiv/ve_console.txt` | console summary of the VE leg |
| `equiv/warm_acceptance_replay.json`, `equiv/warm_exit_replay.std{out,err}.txt` | the owner's read-only replay of the saved `2d982b60` warm data through the current harness: **no new timing**, exit 1, `status=FAIL`, five threshold problems, matrix row `fail` |
| `verdicts/matrix-verdict-check.txt` | matrix `run_job` checks: a `pass=false` warm JSON fails the row, the old `--skip-maskbench` gate JSON fails on the missing `maskbench_states`, the new gate JSON passes |
| `verdicts/matrix-overall-check.txt` | the same rows through `write_report`: gate pass + warm fail -> `Overall FAIL (1/2)`, failed verdict listed |
| `matrix-quick/` | `matrix_adr0007.py --scale quick` on this artifact (warm row excluded, no new measurement; the report marks the `--only` selection explicitly): 6/6 selected jobs pass, 328 Zig tests through `zig test`, 13 parametrised curated ABI cases, three property campaigns, benchmark gate with `maskbench_states=425`; see `matrix-quick/matrix.md` |

`zig build test`: 328/328 (Debug), `zig test -O ReleaseSafe`: 328/328.
The slice p90 0.758 ms is inside the previous artifact's 0.725-0.766 ms
band (three profile runs), and the mask step stays at 0.2 s.

Design and the full argument: section 19 of
`mask-fast-path-needs-reachability.md` next to this directory; the warm
acceptance protocol is fixed in ADR-0007 ("Warm acceptance protocol").

## Scope

The memory reproductions are unit tests of the scratch available to
completion (grammar and tokenizer allocations sit outside the limited
account), not public-ABI limit measurements. The slice and VE replay cover
their recorded inputs and prove those streams, not every allocation path.
The quick matrix bounds the property-based campaigns; the full-scale
matrix (600 differential schemas) is prepared via `--scale full` and still
has to be run. The warm guard is still **FAIL** under the fixed protocol;
its verdict is unchanged by this artifact and was not re-measured here.
