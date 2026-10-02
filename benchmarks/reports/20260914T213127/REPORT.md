# B7 end-to-end GPU + T5 integration - report

- Model: **Qwen/Qwen2.5-1.5B-Instruct** (fp16), greedy decoding, seed 42
- GPU: **NVIDIA GeForce RTX 3060**, VRAM 11.6 GiB, torch 2.14.0+cu130, transformers 5.17.0
- max_new_tokens = 512; 3 repeats per configuration (the table shows the median)
- Constrained: zig_constraints (mode=adaptive) via `constrained_generate_safe` - workaround for bug B-1/B-3
  (see `benchmarks/hf_common.py` and the reproducers in this directory); the core is stock.
- Baseline: the same prompts/batching without constraints. Text equality is not required (SPEC T5).
- TTFT - from the generate start to the first token (per streamer timestamps); ITL - inter-token intervals of batch steps.
- Fast constrained requests are explained by short responses (early EOS): lengths are published alongside (SPEC 10.4).

## s1_flat_enum

| batch | mode | TTFT ms | request, s | output tok | tok/s | ITL p50 ms | ITL p99 ms | response lengths | valid | VRAM peak MiB |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | constrained | 17 | 0.23 | 16 | 70.8 | 13.9 | 14.1 | 16,16,16 | 3/3 = 100% | 2958 |
| 1 | baseline | 16 | 0.60 | 46 | 76.4 | 13.0 | 13.2 | 46,46,46 | - | 2957 |
| 8 | constrained | 59 | 0.40 | 130 | 321.4 | 20.5 | 22.2 | 14..18 (min..max, n=24) | 24/24 = 100% | 2982 |
| 8 | baseline | 54 | 9.80 | 4096 | 418.0 | 19.2 | 22.9 | 512..512 (min..max, n=24) | - | 3120 |
| 32 | constrained | 171 | 0.77 | 520 | 675.1 | 36.4 | 39.3 | 14..18 (min..max, n=96) | 96/96 = 100% | 3063 |
| 32 | baseline | 178 | 20.00 | 16384 | 819.2 | 38.7 | 58.1 | 512..512 (min..max, n=96) | - | 3573 |

## s2_nested_arrays

| batch | mode | TTFT ms | request, s | output tok | tok/s | ITL p50 ms | ITL p99 ms | response lengths | valid | VRAM peak MiB |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | constrained | 19 | 0.65 | 44 | 68.2 | 14.4 | 16.8 | 44,44,44 | 3/3 = 100% | 2959 |
| 1 | baseline | 18 | 3.39 | 244 | 72.1 | 13.8 | 14.7 | 244,244,244 | - | 2963 |
| 8 | constrained | 73 | 1.17 | 336 | 286.7 | 20.7 | 22.8 | 34..55 (min..max, n=24) | 24/24 = 100% | 2992 |
| 8 | baseline | 66 | 11.00 | 4072 | 370.1 | 21.8 | 26.9 | 509..509 (min..max, n=24) | - | 3122 |
| 32 | constrained | 237 | 2.16 | 1344 | 621.3 | 37.3 | 43.3 | 34..55 (min..max, n=96) | 96/96 = 100% | 3118 |
| 32 | baseline | 234 | 21.06 | 16288 | 773.3 | 41.3 | 59.7 | 509..509 (min..max, n=96) | - | 3577 |

## s3_optional_bounded

| batch | mode | TTFT ms | request, s | output tok | tok/s | ITL p50 ms | ITL p99 ms | response lengths | valid | VRAM peak MiB |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | constrained | 17 | 0.40 | 28 | 70.3 | 14.1 | 15.3 | 28,28,28 | 3/3 = 100% | 2958 |
| 1 | baseline | 17 | 0.58 | 43 | 73.6 | 13.5 | 13.6 | 43,43,43 | - | 2957 |
| 8 | constrained | 62 | 0.61 | 222 | 364.2 | 19.7 | 20.1 | 26..29 (min..max, n=24) | 24/24 = 100% | 2986 |
| 8 | baseline | 66 | 11.13 | 4096 | 368.1 | 21.7 | 26.7 | 512..512 (min..max, n=24) | - | 3123 |
| 32 | constrained | 232 | 1.30 | 888 | 685.7 | 38.0 | 42.6 | 26..29 (min..max, n=96) | 96/96 = 100% | 3117 |
| 32 | baseline | 217 | 21.38 | 16384 | 766.3 | 42.0 | 60.5 | 512..512 (min..max, n=96) | - | 3579 |

## T5 integration checks (GPU, same model)

| check | status |
|---|---|
| greedy_cuda_batched_left_padding | PASS |
| sampling_temperature_top_p | PASS |
| mixed_batch_different_schemas | PASS |
| early_eos_short_document | PASS |
| max_length_cutoff | PASS |
| error_recovery_invalid_and_unsupported_schema | PASS |
| processor_conflict_min_new_tokens_and_repetition_penalty | PASS |
| processor_conflict_additive_boost_banned_token | PASS |
| user_logits_processor_rejected_and_unsupported_modes | PASS |
| eos_reflected_in_session_state | PASS |
| cpu_small_run | PASS |

Evidence - `t5_checks.json` (response texts, stop_reason, tokens_accepted, etc.).

## Found integration bugs (reproducers in this directory)

- **B-1** `repro_vocab_mismatch.py`: ConstraintLogitsProcessor crashes when lm_head is wider than the tokenizer
  vocabulary (Qwen2.5: 151936 vs 151665) - masked_fill_ with a mask of length engine.vocab. Blocks
  stock constrained generation on a real model.
- **B-3** `repro_batch_eos.py`: a batch with an early EOS in one row - pads after EOS reach
  accept_token -> InvalidTokenError. Batch generation with mixed lengths is broken.
- **B-2** (minor) `repro_busy_on_error.py`: Engine.__exit__ masks the original generation error
  with its BusyError while a traceback holding Session wrappers is alive.

B7/T5 were run with local workarounds for B-1/B-3 in `benchmarks/hf_common.py`;
the package code was not modified. Raw data: `bench_e2e.json`, `t5_checks.json`.

## Fixes applied (2026-09-14, after the initial report)

B-1/B-3/B-2 are fixed in the stock package code (the workaround from
`benchmarks/hf_common.py` was moved into the package; `hf_common.py` is now -
aliases of the stock classes, the benchmark scripts run through the stock path).

- **B-1 (padded lm_head)** - `python/zig_constraints/transformers.py:119-153`
  (`ConstraintLogitsProcessor.__call__`): the mask is applied to the first
  `vocab_size` logit positions, the tail `scores[:, v:]` always gets
  `-inf` (padded vocab dummy positions are undecodable and forbidden; the tokenizer
  EOS/pad lie inside vocab and are not affected). Width smaller than the
  vocabulary - an explicit `ZigConstraintsError`.
- **B-3 (early EOS in a batch)** - `python/zig_constraints/transformers.py:91`
  (`eos_done` flag), `:98-117` (`_sync_row`: EOS -> `session.finish()`,
  row deactivation, HF pads skipped), `:119-153` (`__call__`:
  `fill_mask` not called for a row finished on this step),
  `:155-166` (`finish_row`: finished row -> `(True, "eos")`, the
  row_ids tail with pads is not accepted).
- **B-2 (exception masking)** - `python/zig_constraints/__init__.py:227`
  (Engine.__exit__), `:268` (Constraint.__exit__), `:348` (Session.__exit__):
  with an active exception, cleanup is best-effort, close errors are suppressed;
  without an active exception, cleanup errors are still raised.
  Also `transformers.py:269-275`: the except branch of
  `constrained_generate` now does `abort()` + `close()` of sessions.

Regression tests: `python/tests/test_transformers_integration.py`
(CPU, byte-level mini vocabulary 257, factory input_ids/scores; torch
is imported lazily inside the tests). Run:
`PYTHONPATH=python python3 -m pytest python/tests/ -q` - **20 passed**
(including the previous 13). Mask-dependent tests are skipped under ZG_CORE_STUB=1.

GPU re-verification via the stock path (Qwen2.5-1.5B-Instruct, RTX 3060), without
workarounds:
- `benchmarks/hf_smoke.py` - OK (`{"city":"Tokyo","temp_c":21}`, eos);
- all three reproducers of this directory: 'bug not reproduced' / no error;
- `bench_e2e.py --reps 3 --batches 8` (raw data - `postfix/bench_e2e.json`):
  constrained **72/72 = 100% valid**, metrics match the workaround run
  (ttft ~60 ms, ~300-350 tok/s warm, baseline unchanged);
- `t5_checks.py` re-run on the stock path: **11/11 PASS**
  (`t5_checks.json` updated).
