# XGrammar comparison (T10 section 10)

Generated 2026-10-13T10:36:44Z by `xgrammar_report.py` (revision 4, sha256 `a183a7182feb93c2…`) from: `raw-x1c.jsonl` (`f233655d8a295d06…`), `raw-b1c.jsonl` (`92920f212be9e883…`), `raw-x2c.jsonl` (`40bc5a127ed0f807…`), `raw-b2c.jsonl` (`d59f6c6cc57c41f5…`).

Method (denominators and rules; abbreviated): **categories**: each request exactly one of failed_http_transport, unfinished, stop_invalid, stop_valid, length, other_finish; **integrity**: problems (non-empty fails the tool) cover the declared session set (missing or undeclared session, backend mismatch), per-file record formats (mandatory fields, types, value consistency), matrix completeness, duplicate keys, idx range, missing final stream event or usage on finished HTTP-200 records, stored-vs-revalidated disagreements and empty inputs; **input_format**: records must match the collector that produced them: v2 = every field emitted by xgrammar_bench2.py (all mandatory and typed; text always a string; finish_present checked against finish, tokens against usage_present, tpot_ms against (e2e_ms - ttft_ms)/(tokens - 1)); v1 = every field emitted by xgrammar_bench.py (no answer text); detected per file unless --format forces one; **expected_sessions**: the session set and each session's backend are declared via --expect or --manifest before the report is built; every declared session must appear in the inputs, no undeclared session may appear and the recorded backend must match; **ttft**: seconds from send to the first content delta; median over HTTP-200 records with a first token (n stated); **tpot**: (e2e - ttft)/(completion_tokens - 1), client-observed per-token time including server overheads, NOT a profiled mask cost; median over stop_valid records only where it is defined (tokens > 1; the stored value is re-checked against the formula; n and exclusions stated); **throughput**: completion tokens / batch wall time, median over complete batch runs with no failed request, no invalid stop, no unfinished request and a recorded usage for every request (run counts stated; a missing usage removes the run from this column, never zero tokens); **stop_valid_per_s**: stop_valid documents / batch wall time, median over complete runs with no failed/invalid/unfinished request; **p90**: statistics.quantiles(n=10)[8] when n >= 10, otherwise the maximum, labeled max(n<10); **cold_warm**: repeat 0 against the remaining repeats, both shown; **length_cuts**: kept in every sample where their fields are defined; not counted as completed documents; a bounded completion check is applied when the text is stored (declared suffix list, not a proof).

## Completeness (matrix, stream end, usage)

Declared matrix per session: 3 workloads x batches 1/8/32/128 x 2 repeats.

| session | records | expected records | runs | expected runs | missing runs | short runs | extra runs |
|---|---|---|---|---|---|---|---|
| b1c | 1014 | 1014 | 24 | 24 | 0 | 0 | 0 |
| b2c | 1014 | 1014 | 24 | 24 | 0 | 0 | 0 |
| x1c | 1014 | 1014 | 24 | 24 | 0 | 0 | 0 |
| x2c | 1014 | 1014 | 24 | 24 | 0 | 0 | 0 |

Expected sessions (from --expect b1c:bolorgir,b2c:bolorgir,x1c:xgrammar,x2c:xgrammar): b1c (bolorgir), b2c (bolorgir), x1c (xgrammar), x2c (xgrammar); missing: none; undeclared: none.

Input formats: raw-b1c.jsonl v2, raw-b2c.jsonl v2, raw-x1c.jsonl v2, raw-x2c.jsonl v2 (every record checked against its format: mandatory keys, types and value consistency).

## w1_json_small

| batch | backend | req | failed | unfinished | stop_valid | stop_invalid | length | other | usage_missing | TTFT p50 (n) | TPOT p50 (n) | TPOT p90 (method) | tok/s (runs) | stop_valid/s (runs) | stop_valid share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | bolorgir | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 15.0929 (4) | 4.201 (4) | 4.3531 (max(n=4)) | 191.7011 (4/4) | 15.9751 (4/4) | 1.000 |
| 1 | xgrammar | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 13.9814 (4) | 4.4057 (4) | 4.5333 (max(n=4)) | 191.4618 (4/4) | 12.7641 (4/4) | 1.000 |
| 8 | bolorgir | 32 | 0 | 0 | 28 | 0 | 4 | 0 | 0 | 42.9988 (32) | 4.7592 (28) | 5.1532 (q90(n=28)) | 476.6995 (4/4) | 22.5466 (4/4) | 0.875 |
| 8 | xgrammar | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 36.2706 (32) | 5.0019 (32) | 5.5406 (q90(n=32)) | 1102.5185 (4/4) | 73.5012 (4/4) | 1.000 |
| 32 | bolorgir | 128 | 0 | 0 | 90 | 0 | 38 | 0 | 0 | 104.8893 (128) | 12.2263 (90) | 14.9353 (q90(n=90)) | 1789.4322 (4/4) | 44.7186 (4/4) | 0.703 |
| 32 | xgrammar | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 96.1442 (128) | 7.3539 (128) | 8.2744 (q90(n=128)) | 2352.8564 (4/4) | 156.8571 (4/4) | 1.000 |
| 128 | bolorgir | 512 | 0 | 0 | 449 | 0 | 63 | 0 | 0 | 293.9611 (512) | 44.0701 (449) | 51.5784 (q90(n=449)) | 2145.5699 (4/4) | 102.3355 (4/4) | 0.877 |
| 128 | xgrammar | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 253.2353 (512) | 18.6046 (512) | 24.3193 (q90(n=512)) | 3411.8429 (4/4) | 227.4562 (4/4) | 1.000 |

## w2_json_nested

| batch | backend | req | failed | unfinished | stop_valid | stop_invalid | length | other | usage_missing | TTFT p50 (n) | TPOT p50 (n) | TPOT p90 (method) | tok/s (runs) | stop_valid/s (runs) | stop_valid share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | bolorgir | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 16.1493 (4) | 128.2839 (4) | 271.1478 (max(n=4)) | 104.8258 (4/4) | 2.6878 (4/4) | 1.000 |
| 1 | xgrammar | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 53.151 (4) | 4.349 (4) | 4.362 (max(n=4)) | 189.2363 (4/4) | 3.7847 (4/4) | 1.000 |
| 8 | bolorgir | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 39.2495 (32) | 48.0473 (32) | 98.9685 (q90(n=32)) | 706.183 (4/4) | 18.7068 (4/4) | 1.000 |
| 8 | xgrammar | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 40.1804 (32) | 5.0239 (32) | 5.224 (q90(n=32)) | 1361.5871 (4/4) | 26.4386 (4/4) | 1.000 |
| 32 | bolorgir | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 126.4604 (128) | 11.4561 (128) | 12.9343 (q90(n=128)) | 2117.3623 (4/4) | 54.9968 (4/4) | 1.000 |
| 32 | xgrammar | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 99.0894 (128) | 8.1166 (128) | 8.8985 (q90(n=128)) | 3090.3159 (4/4) | 60.7804 (4/4) | 1.000 |
| 128 | bolorgir | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 315.2826 (512) | 42.6222 (512) | 46.2847 (q90(n=512)) | 2437.3428 (4/4) | 62.452 (4/4) | 1.000 |
| 128 | xgrammar | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 290.6645 (512) | 22.5862 (512) | 24.6673 (q90(n=512)) | 4362.2665 (4/4) | 84.9626 (4/4) | 1.000 |

## w3_regex64

| batch | backend | req | failed | unfinished | stop_valid | stop_invalid | length | other | usage_missing | TTFT p50 (n) | TPOT p50 (n) | TPOT p90 (method) | tok/s (runs) | stop_valid/s (runs) | stop_valid share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | bolorgir | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 17.6105 (4) | 4.2016 (4) | 4.3528 (max(n=4)) | 227.4939 (4/4) | 3.4999 (4/4) | 1.000 |
| 1 | xgrammar | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 17.175 (4) | 4.3187 (4) | 4.3608 (max(n=4)) | 221.9192 (4/4) | 3.4141 (4/4) | 1.000 |
| 8 | bolorgir | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 37.2629 (32) | 4.9222 (32) | 5.102 (q90(n=32)) | 1461.9155 (4/4) | 22.491 (4/4) | 1.000 |
| 8 | xgrammar | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 35.993 (32) | 4.8788 (32) | 5.0162 (q90(n=32)) | 1483.8319 (4/4) | 22.8282 (4/4) | 1.000 |
| 32 | bolorgir | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 106.2917 (128) | 11.9636 (128) | 12.4398 (q90(n=128)) | 2393.4317 (4/4) | 36.822 (4/4) | 1.000 |
| 32 | xgrammar | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 96.0464 (128) | 6.1605 (128) | 6.5545 (q90(n=128)) | 4208.9988 (4/4) | 64.7538 (4/4) | 1.000 |
| 128 | bolorgir | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 282.8692 (512) | 40.3767 (512) | 42.2684 (q90(n=512)) | 2869.5872 (4/4) | 44.1475 (4/4) | 1.000 |
| 128 | xgrammar | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 247.3866 (512) | 12.3754 (512) | 13.6875 (q90(n=512)) | 7825.208 (4/4) | 120.3878 (4/4) | 1.000 |

## Independent re-validation and limits

Records with stored text: 4056; without: 0; stored-vs-revalidated agreement: 4056 (differences: 0).
Length cuts checked: 105; completable by the bounded suffix test: 0; not: 105.
The v1 raw files of 2026-10-12 store no answer text; their validity cannot be re-checked and their `length` cuts cannot be called legal partials from the stored data (the earlier A1 observation is recorded in PROTOCOL.md, not re-verifiable).

