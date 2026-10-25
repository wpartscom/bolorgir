# Gate hardening after the `a1a965b` review

Artifact unchanged:
`a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6`
(1 268 464 bytes, `a87e2f71` checkpoint). The Zig sources were not touched;
this package is the repaired harness around that artifact. `zig test`
through the quick matrix: **328/328** (ReleaseSafe).

## What was wrong and what changed

1. **MaskBench compared a sample, and not the outcomes.** The gate stored
   the first 400 successful masks and then only `step % 7 == 0`, skipped
   failed fills, and never compared accept codes or instance termination.
   It now records every visited state keyed by `(file, instance, step)`
   with the full mask, the fill status and the accept code, plus every
   instance's expected validity and termination. A structural, mask,
   status or accept divergence, a fill error or a validation mismatch
   fails the gate and writes the complete comparison to the artifacts
   directory. `gate.maskbench_states`, `maskbench_instances`,
   `maskbench_fill_errors`, `maskbench_validation_errors` and
   `maskbench_trace_sha256` are published.
2. **A stale JSON could turn a SKIP into PASS.** Every matrix row with a
   verdict now gets a unique `BLG_RUN_ID`; the benchmark writes it and
   `check_verdict` requires the run id, the expected `status`, zero error
   counters, the warm protocol values, `conditions.ok` and the library
   SHA256 from the file written by that invocation. `bench_common.skip`
   (`{"status": "SKIP"}`, exit 0) is explicitly rejected. The old file is
   left untouched, so it remains evidence and still cannot satisfy the
   row.
3. **Warm conditions were not fixed or recorded.** Each warm child is
   pinned to one CPU (`--warm-cpu` / `BLG_WARM_CPU`; acceptance refuses to
   run without one). Every leg records the CPU model, governor,
   current/max frequency, load average, `procs_running`,
   `/proc/pressure/cpu`, extension version and the mapped library SHA256
   before and after the timed loop. Acceptance fails on a placement
   mismatch, load above `--warm-max-load1` or CPU pressure above
   `--warm-max-cpu-pressure`. A saved-data replay is marked
   `conditions.recorded=false` and can never satisfy the matrix row.

## Files

| path | what it is |
|---|---|
| `selfcheck/selfcheck.json`, `console.txt` | `gate_selfcheck.py`: control + injected mask/status/accept/shared-fill/shared-validation faults + stale JSON/SKIP. **31/31**. The control compares **410** masks (the old sampler: 401); `mask-401` (a state the old sampler skipped) and the two step-409 faults fail, leave a gate artifact and write no success JSON. |
| `negative-control/` | the same self-check run against the pre-fix tree `a1a965b`: **3/31**; the old gate passes all injected faults and the stale JSON/SKIP row. This is the negative control for the checks themselves. |
| `real-gate/bench_gate.json` | the real pinned slice on `a87e2f71`: 24 files, **564 masks**, 24 instances, 0 fill/accept/validation errors, trace `1c72ad5d…f4a`. The old run compared 425. |
| `matrix-quick/` | `matrix_adr0007.py --scale quick --only` (the warm row excluded): **6/6** selected jobs, 328 Zig tests, 13 curated cases, three campaigns, `bench-gate` 564 states. The gate verdict shows the matching run id, `status=OK`, zero counters and the expected library SHA256. |
| `stale-review/` | the reviewer's own `review_verdicts.py` scenario rerun against this code: the saved-data warm replay exits 1 and its row is `fail`; the stale 425-state JSON plus a `{"status": "SKIP"}` process gives `fail`, records the SKIP reason and leaves the old file byte-identical (`verdict-results.json`, `stale-json/`). |
| `warm-pair/warm_pair.json` | exploratory base `2d982b60` / candidate `a87e2f71` pair, CPU 7, 5 rounds × 2000 observations, balanced library and leg order. **Not an acceptance run** (see below). |

## Warm pair: what it does and does not say

Conditions recorded in the JSON: CPU 7 pinned, governor `powersave` on all
CPUs, current frequency 0.8–4.7 GHz during the legs, load average
1.0–1.77, `/proc/pressure/cpu` `some.avg10 = 0.00`, 100 % cache hits, both
library hashes verified in every child. Limits for this exploratory pair
were `--max-load1 2.0 --max-cpu-pressure 0.5`, chosen before the run; this
is not the acceptance setup.

All ten `on`-leg means were below 1300 ns for both libraries:

- base `2d982b60`: 1 137.7–1 293.6 ns;
- candidate `a87e2f71`: 1 097.4–1 261.5 ns;
- paired candidate − base: mean −31.7 ns, median −13.3 ns, range
  −158.8…+56.2 ns; mean ratio 0.975.

This pair shows no candidate-specific slowdown under these fixed
conditions; it does not explain by itself the stored FAIL, which was
measured on the earlier `2d982b60` artifact without pinned placement. The
stored FAIL is unchanged and no new acceptance measurement was made. An
acceptance run still needs the fixed placement and the protocol
conditions chosen in advance, and must be judged by the 5 × 2000 / all ten
means rule.

## Commands

```sh
python3 -B benchmarks/maskbench/t8a/gate_selfcheck.py --out <dir>

PYTHONPATH="$PWD/python:$PWD/benchmarks/maskbench/t8a:$PWD/benchmarks/external/jsonschemabench-maskbench/maskbench" \
HF_HOME="$PWD/benchmarks/local/hf-cache" HF_HUB_OFFLINE=1 \
BLG_LIB_PATH="$PWD/benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0" \
benchmarks/local/t8a-work-9a5baf3e/venv-pinned/bin/python \
  benchmarks/bench_adr0007.py --skip-warm-ext --cold-repeats 1 \
  --warm-observations 200 --mask-artifacts <dir>/artifacts \
  --out <dir>/bench_gate.json

benchmarks/local/t8a-work-9a5baf3e/venv-pinned/bin/python \
  benchmarks/maskbench/t8a/matrix_adr0007.py --scale quick \
  --only zig-three-way fastpath-curated fastpath-fuzz \
  differential-reference cache-onoff bench-gate \
  --lib "$PWD/benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0" \
  --out <dir>

PYTHONPATH=... HF_HOME=... HF_HUB_OFFLINE=1 \
benchmarks/local/t8a-work-9a5baf3e/venv-pinned/bin/python \
  benchmarks/maskbench/t8a/warm_diag.py \
  --base benchmarks/local/t8a-checkpoints/2d982b60/libbolorgir.so.0.1.0 \
  --candidate benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0 \
  --cpu 7 --rounds 5 --observations 2000 \
  --max-load1 2.0 --max-cpu-pressure 0.5 --out <dir>/warm_pair.json
```

A full acceptance run now also needs `--warm-cpu` (or `BLG_WARM_CPU`) and
is not part of this package; the full-scale Decision 4 matrix has not been
run.
