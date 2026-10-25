# ADR-0007 Decision 4 acceptance matrix

Scale: `quick`; library: `/home/gm/Рабочий стол/сайты/LLM_trainer/zig/benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0`; generated 2026-10-24T17:00:11Z UTC

| job | status | tests | parametrised | s | requirement |
|---|---|---:|---:|---:|---|
| `bench-gate` | pass | 0 | 0 | 12.4 | Decision 4: on/off mask equality gate on the pinned probe states AND the pinned MaskBench slice (mandatory at every scale); timings only after it |

Selection: `--only` ran 1 of the matrix jobs; the Overall line below covers only these rows, not the full Decision 4 set.

Overall: **PASS** (1/1 selected jobs pass)

## JSON verdicts

- `bench-gate`: ok (ok run_id = 'bench-gate-64197-1792861198666095812' (expected 'bench-gate-64197-1792861198666095812'); ok status = 'OK' (expected 'OK'); ok gate.maskbench_states = 564; ok gate.maskbench_instances = 24; ok gate.maskbench_terminal_states = 24; ok gate.maskbench_terminal_complete = True; ok gate.maskbench_fill_errors = 0; ok gate.maskbench_terminal_fill_errors = 0; ok gate.maskbench_input_errors = 0; ok gate.maskbench_completion_errors = 0; ok gate.maskbench_validation_errors = 0; ok environment.lib_sha256 = 'a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6' (expected 'a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6'))

Fuzz artifacts produced by this run: none
