# ADR-0007 Decision 4 acceptance matrix

Scale: `full`; library: `/home/gm/Рабочий стол/сайты/LLM_trainer/zig/benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0`; generated 2026-10-24T17:10:15Z UTC

| job | status | tests | parametrised | s | requirement |
|---|---|---:|---:|---:|---|
| `zig-three-way` | pass | 328 | 0 | 39.9 | Decision 4: Zig unit tests, three-way fast/trie/brute comparison on random vocabularies |
| `fastpath-curated` | pass | 13 | 13 | 0.5 | Decision 4: curated ABI cases over fast path on/off, lazy/adaptive and max_workers {1,2,8} (long tokens, value boundaries, enclosure scope, length limits, Unicode) |
| `fastpath-fuzz` | pass | 1 | 0 | 0.5 | Decision 4: randomized fast-path on/off equality (tests/test_mask_fastpath_fuzz.py) |
| `differential-reference` | pass | 1 | 0 | 2.0 | Decision 4: kernel vs tests/reference.py differential campaign (fast path on/off, cache on/off) |
| `cache-onoff` | pass | 1 | 0 | 0.9 | Decision 4: every comparison repeated with the session cache on and off (tests/fuzz/test_fuzz_cache.py) |
| `bench-gate` | pass | 0 | 0 | 12.6 | Decision 4: on/off mask equality gate on the pinned probe states AND the pinned MaskBench slice (mandatory at every scale); timings only after it |

Selection: `--only` ran 6 of the matrix jobs; the Overall line below covers only these rows, not the full Decision 4 set.

Overall: **PASS** (6/6 selected jobs pass)

## JSON verdicts

- `bench-gate`: ok (ok run_id = 'bench-gate-69469-1792861803330482194' (expected 'bench-gate-69469-1792861803330482194'); ok status = 'OK' (expected 'OK'); ok gate.maskbench_states = 564; ok gate.maskbench_instances = 24; ok gate.maskbench_terminal_states = 24; ok gate.maskbench_terminal_complete = True; ok gate.maskbench_fill_errors = 0; ok gate.maskbench_terminal_fill_errors = 0; ok gate.maskbench_input_errors = 0; ok gate.maskbench_completion_errors = 0; ok gate.maskbench_validation_errors = 0; ok environment.lib_sha256 = 'a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6' (expected 'a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6'))

Fuzz artifacts produced by this run: `py_fuzz_cache.json`, `py_fuzz_differential.json`
