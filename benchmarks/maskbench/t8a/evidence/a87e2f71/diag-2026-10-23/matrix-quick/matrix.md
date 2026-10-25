# ADR-0007 Decision 4 acceptance matrix

Scale: `quick`; library: `/home/gm/Рабочий стол/сайты/LLM_trainer/zig/zig-out/lib/libbolorgir.so.0.1.0`; generated 2026-10-24T02:37:43Z UTC

| job | status | tests | parametrised | s | requirement |
|---|---|---:|---:|---:|---|
| `zig-three-way` | pass | 328 | 0 | 39.3 | Decision 4: Zig unit tests, three-way fast/trie/brute comparison on random vocabularies |
| `fastpath-curated` | pass | 13 | 13 | 0.6 | Decision 4: curated ABI cases over fast path on/off, lazy/adaptive and max_workers {1,2,8} (long tokens, value boundaries, enclosure scope, length limits, Unicode) |
| `fastpath-fuzz` | pass | 1 | 0 | 0.2 | Decision 4: randomized fast-path on/off equality (tests/test_mask_fastpath_fuzz.py) |
| `differential-reference` | pass | 1 | 0 | 0.3 | Decision 4: kernel vs tests/reference.py differential campaign (fast path on/off, cache on/off) |
| `cache-onoff` | pass | 1 | 0 | 0.4 | Decision 4: every comparison repeated with the session cache on and off (tests/fuzz/test_fuzz_cache.py) |
| `bench-gate` | pass | 0 | 0 | 12.7 | Decision 4: on/off mask equality gate on the pinned probe states AND the pinned MaskBench slice (mandatory at every scale); timings only after it |

Selection: `--only` ran 6 of the matrix jobs; the Overall line below covers only these rows, not the full Decision 4 set.

Overall: **PASS** (6/6 selected jobs pass)

## JSON verdicts

- `bench-gate`: ok (gate.maskbench_states = 425)

Fuzz artifacts produced by this run: `py_fuzz_cache.json`, `py_fuzz_differential.json`
