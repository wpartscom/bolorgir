# Terminal completion and fail-closed warm conditions

Artifact unchanged:
`a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6`
(1 268 464 bytes, `a87e2f71` checkpoint). The Zig sources were not touched;
this package covers the second gate review. `zig test` through the quick
matrix: 328/328.

## What the review found

1. The MaskBench harness treated consuming every input token as
   acceptance. It never called `can_end`/`finish` and never compared the
   post-input (EOS) mask. On the real `a87e2f71`, schema
   `{"type":"integer","enum":[10]}` with data `1`: the token is accepted,
   `can_end` is false, the tail mask allows only `0`, `finish` returns 11
   (`BLG_ERR_WRONG_STATE`), yet the harness recorded `accepted=true` and a
   false validation error. The `can_end` and tail-mask injections passed
   the gate.
2. Warm conditions failed open: only the **start** affinity was checked,
   and missing load/PSI values skipped the limit checks. Removing load from
   both snapshots or moving the **end** affinity to another CPU still
   produced `warm_acceptance.pass=true` and a green matrix verdict.

## What changed

- The gate records a terminal state per instance: the post-input mask, the
  `fill` status of that mask, `can_end`, `finish` and their errors. It
  compares them between the off/on arms, separately from the timed input
  states (`maskbench_states` stays 564; `maskbench_terminal_states` is 24).
  A document is accepted only when token consumption and completion both
  succeed. `can_end=false` with `finish=11` is a correct semantic
  rejection; a resource/other finish error, a failed `can_end` or a failed
  tail fill is a **completion error** and is never counted as expected
  invalidity. The JSON publishes `maskbench_terminal_complete`,
  `maskbench_terminal_fill_errors`, `maskbench_input_errors` and
  `maskbench_completion_errors`; the matrix requires the terminal count,
  completeness and zero errors.
- Warm acceptance now requires both start and end snapshots to contain
  finite `loadavg` and `cpu_pressure_some.avg10`, both affinities to equal
  the pinned CPU, a recorded governor and extension version, and the
  declared limits. The pre-loop snapshot is taken after tokenizer/session
  setup, immediately before the timed loop. The helper hashes the mapped
  library before **and after** the loop; `cpu_now` uses
  `os.sched_getcpu` or `/proc/self/stat` when that call is absent. The
  matrix requires `conditions.ok`, `conditions.complete` and the limits.
- The self-check now covers control, the reviewer's step-401/409 faults,
  asymmetric `can_end`, asymmetric tail mask/EOS, a shared finish resource
  error, a shared semantic rejection, the correct incomplete-number
  rejection, and the warm control/missing-load/end-affinity cases.

## Results

| file | what it is |
|---|---|
| `selfcheck/selfcheck.json`, `console.txt` | **54/54**. Control: 410 input masks and 1 terminal; the incomplete-number case passes with `validation=0`; `terminal`, `completion`, `input` and `validation` faults leave artifacts and write no success JSON; warm control passes, missing-load and end-affinity fail. |
| `negative-control-711ad70/` | the same self-check on the previous commit: **27/54**. The old gate passes `cannot-end`, `tail-mask`, `finish-error-both` and `cannot-end-both`; the incomplete number is a false failure; missing-load and end-affinity warm cases pass. |
| `real-gate/bench_gate.json` | real pinned slice: 564 input masks, **24 terminal states**, `terminal_complete=true`, zero fill/terminal-fill/input/completion/validation errors, trace `2ab0c918…ab61`. |
| `matrix-gate/` | `matrix_adr0007.py --only bench-gate`: pass; the verdict names the matching run id, `status=OK`, 564 states, 24 terminals, complete terminals, all zero counters and the expected library SHA256. |
| `matrix-quick/` | `matrix_adr0007.py --scale quick --only` (warm row excluded): **6/6** selected jobs, 328 Zig tests, 13 curated cases, three campaigns and the terminal-aware `bench-gate` row. |
| `review/review-results.json` | the reviewer's `review.py` against this code: gate control passes with 2 states + 1 terminal; `cannot-end` and `tail-mask` fail; warm control passes; missing-load and end-affinity fail with the expected reasons. |
| `review/eof-terminal.json` | the real enum/integer case: token accepted, `can_end=false`, tail mask `[2]` (enables token id 1, the digit `0`; EOS id 2 is excluded), `finish=11`; harness records `rejected:finish:11` and `validation_error=0`. |
| `warm-acceptance/` | the one new acceptance run (see below), with the matrix report and `artifacts/warm_acceptance.json`. |

## The one new acceptance

Conditions fixed before the run: CPU 7 pinned, `powersave`, load1 <= 2.0,
CPU pressure `some.avg10` <= 0.5. Exactly one run, 5 repeats x 2000
observations per probe, no retries:

- ten `warm_ext.on` means: 1190.5, 1218.3, 1214.7, 1188.4, 1267.5,
  1126.7, 1264.1, 1254.7, 1167.2, 1085.0 ns (all <= 1300);
- p50: 1155, 1187, 1179, 1159, 1236, 1103, 1231, 1220, 1137, 1052 ns;
- conditions: `recorded=true`, `complete=true`, `ok=true`; start and end
  affinity `[7]`, `cpu_now` start/end `7`, governor `powersave`, extension
  0.2.0rc1, mapped library SHA256 `a87e2f71…15e6` before and after;
- observed load1 1.27-1.45, CPU pressure `some.avg10` 0.00;
- matrix row: **pass** (protocol 5/2000/1300 ns/CPU 7, limits 2.0/0.5,
  library hash, run id).

The stored `2d982b60` FAIL is a different measurement: it remains
unchanged and is not reinterpreted. No extra acceptance attempt was made.
The full-scale Decision 4 matrix is still outstanding.

## Documentation boundary: the earlier diagnostic pair

The `warm-pair/warm_pair.json` in the previous evidence package predates
this fix. Its 40 `cpu_now` values are `null` (the platform's
`os.sched_getcpu` was unavailable) and it has no `loaded_end` hash; it
records the fixed affinity `[7]` and the before-loop hash only. Its
timings remain as recorded and are not reinterpreted. The helper and the
new acceptance above record `cpu_now` and the after-loop hash.

## Commands

```sh
python3 -B benchmarks/maskbench/t8a/gate_selfcheck.py --out <dir>

PYTHONPATH="$PWD/python:$PWD/benchmarks/maskbench/t8a:$PWD/benchmarks/external/jsonschemabench-maskbench/maskbench" \
HF_HOME="$PWD/benchmarks/local/hf-cache" HF_HUB_OFFLINE=1 \
benchmarks/local/t8a-work-9a5baf3e/venv-pinned/bin/python \
  benchmarks/maskbench/t8a/matrix_adr0007.py --scale quick --only bench-gate \
  --lib "$PWD/benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0" \
  --out <dir>

PYTHONPATH="$PWD/python:$PWD/benchmarks/maskbench/t8a:$PWD/benchmarks/external/jsonschemabench-maskbench/maskbench" \
HF_HOME="$PWD/benchmarks/local/hf-cache" HF_HUB_OFFLINE=1 \
benchmarks/local/t8a-work-9a5baf3e/venv-pinned/bin/python \
  benchmarks/maskbench/t8a/matrix_adr0007.py --scale quick --only warm-acceptance \
  --warm-cpu 7 --warm-max-load1 2.0 --warm-max-cpu-pressure 0.5 \
  --lib "$PWD/benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0" \
  --out <dir>
```
