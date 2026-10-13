# XGrammar comparison (T10 section 10)

Generated 2026-10-13T10:15:57Z by `xgrammar_report.py` (revision 3, sha256 `00fb3d18acd6e88d…`) from: `raw-b1d.jsonl` (`ef66aa7c7558442b…`), `raw-b2d.jsonl` (`b78293646aafb866…`), `raw-x1d.jsonl` (`ddfe6b295b3aaa18…`), `raw-x2d.jsonl` (`3761b746519dd88b…`).

Method (denominators and rules; abbreviated): **categories**: each request exactly one of failed_http_transport, unfinished, stop_invalid, stop_valid, length, other_finish; **integrity**: problems (non-empty fails the tool) cover matrix completeness, duplicate keys, idx range, missing final stream event or usage on finished HTTP-200 records, missing stored text on v2 records, stored-vs-revalidated disagreements and empty inputs; **ttft**: seconds from send to the first content delta; median over HTTP-200 records with a first token (n stated); **tpot**: (e2e - ttft)/(completion_tokens - 1), client-observed per-token time including server overheads, NOT a profiled mask cost; median over records where it is defined (stop_valid only, tokens > 1; n and exclusions stated); **throughput**: completion tokens / batch wall time, median over complete batch runs with no failed request, no invalid stop, no unfinished request and a recorded usage for every request (run counts stated; a missing usage removes the run from this column, never zero tokens); **stop_valid_per_s**: stop_valid documents / batch wall time, median over complete runs with no failed/invalid/unfinished request; **p90**: statistics.quantiles(n=10)[8] when n >= 10, otherwise the maximum, labeled max(n<10); **cold_warm**: repeat 0 against the remaining repeats, both shown; **length_cuts**: kept in every sample where their fields are defined; not counted as completed documents; a bounded completion check is applied when the text is stored (declared suffix list, not a proof).

## Completeness (matrix, stream end, usage)

Declared matrix per session: 3 workloads x batches 1/8/32/128 x 2 repeats.

| session | records | expected records | runs | expected runs | missing runs | short runs | extra runs |
|---|---|---|---|---|---|---|---|
| b1d | 1014 | 1014 | 24 | 24 | 0 | 0 | 0 |
| b2d | 1014 | 1014 | 24 | 24 | 0 | 0 | 0 |
| x1d | 1014 | 1014 | 24 | 24 | 0 | 0 | 0 |
| x2d | 1014 | 1014 | 24 | 24 | 0 | 0 | 0 |

## w1_json_small

| batch | backend | req | failed | unfinished | stop_valid | stop_invalid | length | other | usage_missing | TTFT p50 (n) | TPOT p50 (n) | TPOT p90 (method) | tok/s (runs) | stop_valid/s (runs) | stop_valid share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | bolorgir | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 13.9912 (4) | 4.0955 (4) | 4.3054 (max(n=4)) | 198.0462 (4/4) | 16.5039 (4/4) | 1.000 |
| 1 | xgrammar | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 16.5209 (4) | 4.445 (4) | 4.6411 (max(n=4)) | 191.3228 (4/4) | 12.7549 (4/4) | 1.000 |
| 8 | bolorgir | 32 | 0 | 0 | 28 | 0 | 4 | 0 | 0 | 42.2531 (32) | 4.9805 (28) | 5.761 (q90(n=28)) | 474.9642 (4/4) | 22.4645 (4/4) | 0.875 |
| 8 | xgrammar | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 37.7504 (32) | 5.3405 (32) | 6.0317 (q90(n=32)) | 1030.2603 (4/4) | 68.684 (4/4) | 1.000 |
| 32 | bolorgir | 128 | 0 | 0 | 88 | 0 | 40 | 0 | 0 | 108.9829 (128) | 12.634 (88) | 14.9316 (q90(n=88)) | 1788.4701 (4/4) | 43.5247 (4/4) | 0.688 |
| 32 | xgrammar | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 74.8861 (128) | 7.4839 (128) | 9.4887 (q90(n=128)) | 2288.5802 (4/4) | 152.572 (4/4) | 1.000 |
| 128 | bolorgir | 512 | 0 | 0 | 449 | 0 | 63 | 0 | 0 | 305.3211 (512) | 44.3264 (449) | 51.5397 (q90(n=449)) | 2117.4851 (4/4) | 101.7274 (4/4) | 0.877 |
| 128 | xgrammar | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 257.4793 (512) | 19.8564 (512) | 25.1419 (q90(n=512)) | 3191.2008 (4/4) | 212.7467 (4/4) | 1.000 |

## w2_json_nested

| batch | backend | req | failed | unfinished | stop_valid | stop_invalid | length | other | usage_missing | TTFT p50 (n) | TPOT p50 (n) | TPOT p90 (method) | tok/s (runs) | stop_valid/s (runs) | stop_valid share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | bolorgir | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 16.6456 (4) | 136.093 (4) | 268.1576 (max(n=4)) | 106.4146 (4/4) | 2.7286 (4/4) | 1.000 |
| 1 | xgrammar | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 54.2244 (4) | 4.6531 (4) | 4.8165 (max(n=4)) | 182.1698 (4/4) | 3.6434 (4/4) | 1.000 |
| 8 | bolorgir | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 43.2894 (32) | 49.1246 (32) | 101.6282 (q90(n=32)) | 673.1482 (4/4) | 17.8317 (4/4) | 1.000 |
| 8 | xgrammar | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 40.2847 (32) | 5.5577 (32) | 5.7985 (q90(n=32)) | 1250.4088 (4/4) | 24.2798 (4/4) | 1.000 |
| 32 | bolorgir | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 125.107 (128) | 13.0029 (128) | 13.8982 (q90(n=128)) | 1945.0619 (4/4) | 50.4801 (4/4) | 1.000 |
| 32 | xgrammar | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 116.0063 (128) | 8.8551 (128) | 9.4783 (q90(n=128)) | 2876.8064 (4/4) | 56.5808 (4/4) | 1.000 |
| 128 | bolorgir | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 311.4784 (512) | 46.527 (512) | 48.5907 (q90(n=512)) | 2288.1407 (4/4) | 58.6585 (4/4) | 1.000 |
| 128 | xgrammar | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 310.8096 (512) | 22.7162 (512) | 24.454 (q90(n=512)) | 4298.9286 (4/4) | 83.7481 (4/4) | 1.000 |

## w3_regex64

| batch | backend | req | failed | unfinished | stop_valid | stop_invalid | length | other | usage_missing | TTFT p50 (n) | TPOT p50 (n) | TPOT p90 (method) | tok/s (runs) | stop_valid/s (runs) | stop_valid share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | bolorgir | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 16.0192 (4) | 4.2363 (4) | 4.3599 (max(n=4)) | 226.0897 (4/4) | 3.4783 (4/4) | 1.000 |
| 1 | xgrammar | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 18.9798 (4) | 4.374 (4) | 4.4101 (max(n=4)) | 217.4728 (4/4) | 3.3457 (4/4) | 1.000 |
| 8 | bolorgir | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 39.3232 (32) | 4.8993 (32) | 5.4912 (q90(n=32)) | 1485.3398 (4/4) | 22.8514 (4/4) | 1.000 |
| 8 | xgrammar | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 38.8936 (32) | 5.0472 (32) | 5.1637 (q90(n=32)) | 1446.7007 (4/4) | 22.2569 (4/4) | 1.000 |
| 32 | bolorgir | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 106.877 (128) | 12.0435 (128) | 13.3176 (q90(n=128)) | 2424.1949 (4/4) | 37.2953 (4/4) | 1.000 |
| 32 | xgrammar | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 103.0525 (128) | 6.3696 (128) | 6.7207 (q90(n=128)) | 4077.1358 (4/4) | 62.7252 (4/4) | 1.000 |
| 128 | bolorgir | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 301.1958 (512) | 41.7265 (512) | 43.4595 (q90(n=512)) | 2760.2673 (4/4) | 42.4657 (4/4) | 1.000 |
| 128 | xgrammar | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 266.993 (512) | 12.4118 (512) | 14.0668 (q90(n=512)) | 7546.0671 (4/4) | 116.0933 (4/4) | 1.000 |

## Independent re-validation and limits

Records with stored text: 4056; without: 0; stored-vs-revalidated agreement: 4056 (differences: 0).
Length cuts checked: 107; completable by the bounded suffix test: 0; not: 107.
The v1 raw files of 2026-10-12 store no answer text; their validity cannot be re-checked and their `length` cuts cannot be called legal partials from the stored data (the earlier A1 observation is recorded in PROTOCOL.md, not re-verifiable).

