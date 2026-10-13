# XGrammar comparison (T10 section 10)

Generated 2026-10-13T11:15:18Z by `xgrammar_report.py` (revision 5, sha256 `23b1359c5def8e06…`) from: `raw-x1.jsonl` (`43df407a10a212ef…`), `raw-b1.jsonl` (`59f1c1bbb728eb84…`), `raw-x2.jsonl` (`414a3460ef4277a9…`), `raw-b2.jsonl` (`b9a6b6b4c061c237…`).

Method (denominators and rules; abbreviated): **categories**: each request exactly one of failed_http_transport, unfinished, stop_invalid, stop_valid, length, other_finish; **integrity**: problems (non-empty fails the tool) cover the declared session set (a missing or malformed declaration, a declared session missing from the inputs, an undeclared session, a declared backend that is missing, empty or not admissible, a recorded-vs-declared backend mismatch), per-file record formats (mandatory fields, types, value consistency), matrix completeness, duplicate keys, idx range, missing final stream event or usage on finished HTTP-200 records, stored-vs-revalidated disagreements and empty inputs; **input_format**: records must match the collector that produced them: v2 = every field emitted by xgrammar_bench2.py (all mandatory and typed; text always a string; finish_present checked against finish, tokens against usage_present, tpot_ms against (e2e_ms - ttft_ms)/(tokens - 1)); v1 = every field emitted by xgrammar_bench.py (no answer text); detected per file unless --format forces one; **expected_sessions**: the session set and each session's backend are declared via --expect or --manifest before the report is built; the declaration must name every compared session with a non-empty admissible backend (xgrammar/bolorgir) and nothing else, and the manifest's session_backends mapping must cover its sessions exactly; every declared session must appear in the inputs, no undeclared session may appear and the recorded backend must match; **ttft**: seconds from send to the first content delta; median over HTTP-200 records with a first token (n stated); **tpot**: (e2e - ttft)/(completion_tokens - 1), client-observed per-token time including server overheads, NOT a profiled mask cost; median over stop_valid records only where it is defined (tokens > 1; the stored value is re-checked against the formula; n and exclusions stated); **throughput**: completion tokens / batch wall time, median over complete batch runs with no failed request, no invalid stop, no unfinished request and a recorded usage for every request (run counts stated; a missing usage removes the run from this column, never zero tokens); **stop_valid_per_s**: stop_valid documents / batch wall time, median over complete runs with no failed/invalid/unfinished request; **p90**: statistics.quantiles(n=10)[8] when n >= 10, otherwise the maximum, labeled max(n<10); **cold_warm**: repeat 0 against the remaining repeats, both shown; **length_cuts**: kept in every sample where their fields are defined; not counted as completed documents; a bounded completion check is applied when the text is stored (declared suffix list, not a proof).

## Completeness (matrix, stream end, usage)

Declared matrix per session: 3 workloads x batches 1/8/32/128 x 2 repeats.

| session | records | expected records | runs | expected runs | missing runs | short runs | extra runs |
|---|---|---|---|---|---|---|---|
| b1 | 1014 | 1014 | 24 | 24 | 0 | 0 | 0 |
| b2 | 1014 | 1014 | 24 | 24 | 0 | 0 | 0 |
| x1 | 1014 | 1014 | 24 | 24 | 0 | 0 | 0 |
| x2 | 1014 | 1014 | 24 | 24 | 0 | 0 | 0 |

Expected sessions (from --expect b1:bolorgir,b2:bolorgir,x1:xgrammar,x2:xgrammar): b1 (bolorgir), b2 (bolorgir), x1 (xgrammar), x2 (xgrammar); missing: none; undeclared: none.

Input formats: raw-b1.jsonl v1, raw-b2.jsonl v1, raw-x1.jsonl v1, raw-x2.jsonl v1 (every record checked against its format: mandatory keys, types and value consistency).

## w1_json_small

| batch | backend | req | failed | unfinished | stop_valid | stop_invalid | length | other | usage_missing | TTFT p50 (n) | TPOT p50 (n) | TPOT p90 (method) | tok/s (runs) | stop_valid/s (runs) | stop_valid share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | bolorgir | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 17.5584 (4) | 4.3285 (4) | 4.392 (max(n=4)) | 184.3531 (4/4) | 15.3628 (4/4) | 1.000 |
| 1 | xgrammar | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 12.8053 (4) | 4.4121 (4) | 4.537 (max(n=4)) | 198.5926 (4/4) | 10.4522 (4/4) | 1.000 |
| 8 | bolorgir | 32 | 0 | 0 | 28 | 0 | 4 | 0 | 0 | 51.8737 (32) | 4.9706 (28) | 5.1808 (q90(n=28)) | 446.1183 (4/4) | 21.1002 (4/4) | 0.875 |
| 8 | xgrammar | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 33.8669 (32) | 5.109 (32) | 5.4119 (q90(n=32)) | 1177.916 (4/4) | 61.9956 (4/4) | 1.000 |
| 32 | bolorgir | 128 | 0 | 0 | 90 | 0 | 38 | 0 | 0 | 109.5039 (128) | 12.1543 (90) | 13.6472 (q90(n=90)) | 1722.7928 (4/4) | 44.7173 (4/4) | 0.703 |
| 32 | xgrammar | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 97.4475 (128) | 7.3448 (128) | 8.9115 (q90(n=128)) | 2612.2101 (4/4) | 137.4847 (4/4) | 1.000 |
| 128 | bolorgir | 512 | 0 | 0 | 447 | 0 | 65 | 0 | 0 | 313.5809 (512) | 45.5577 (447) | 51.252 (q90(n=447)) | 2126.3944 (4/4) | 99.815 (4/4) | 0.873 |
| 128 | xgrammar | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 267.6258 (512) | 17.0106 (512) | 21.4616 (q90(n=512)) | 3964.8577 (4/4) | 208.6767 (4/4) | 1.000 |

## w2_json_nested

| batch | backend | req | failed | unfinished | stop_valid | stop_invalid | length | other | usage_missing | TTFT p50 (n) | TPOT p50 (n) | TPOT p90 (method) | tok/s (runs) | stop_valid/s (runs) | stop_valid share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | bolorgir | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 16.9939 (4) | 135.2416 (4) | 267.9816 (max(n=4)) | 97.3267 (4/4) | 2.4956 (4/4) | 1.000 |
| 1 | xgrammar | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 16.9914 (4) | 4.3183 (4) | 4.4614 (max(n=4)) | 221.6591 (4/4) | 2.9954 (4/4) | 1.000 |
| 8 | bolorgir | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 41.4629 (32) | 49.104 (32) | 100.8251 (q90(n=32)) | 663.191 (4/4) | 17.568 (4/4) | 1.000 |
| 8 | xgrammar | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 40.0018 (32) | 5.0639 (32) | 5.2714 (q90(n=32)) | 1429.7222 (4/4) | 17.7881 (4/4) | 1.000 |
| 32 | bolorgir | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 127.2849 (128) | 11.5004 (128) | 12.2382 (q90(n=128)) | 2095.9083 (4/4) | 54.2641 (4/4) | 1.000 |
| 32 | xgrammar | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 102.2449 (128) | 7.3749 (128) | 7.6794 (q90(n=128)) | 3645.3344 (4/4) | 46.4374 (4/4) | 1.000 |
| 128 | bolorgir | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 306.8599 (512) | 43.5749 (512) | 45.6173 (q90(n=512)) | 2405.9252 (4/4) | 61.6662 (4/4) | 1.000 |
| 128 | xgrammar | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 281.2735 (512) | 17.9875 (512) | 19.1074 (q90(n=512)) | 5625.4721 (4/4) | 72.3747 (4/4) | 1.000 |

## w3_regex64

| batch | backend | req | failed | unfinished | stop_valid | stop_invalid | length | other | usage_missing | TTFT p50 (n) | TPOT p50 (n) | TPOT p90 (method) | tok/s (runs) | stop_valid/s (runs) | stop_valid share |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | bolorgir | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 15.4494 (4) | 4.3645 (4) | 4.5623 (max(n=4)) | 220.1297 (4/4) | 3.3866 (4/4) | 1.000 |
| 1 | xgrammar | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 17.2914 (4) | 4.3597 (4) | 4.4922 (max(n=4)) | 219.0057 (4/4) | 3.3693 (4/4) | 1.000 |
| 8 | bolorgir | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 41.7525 (32) | 5.1793 (32) | 5.85 (q90(n=32)) | 1402.4089 (4/4) | 21.5755 (4/4) | 1.000 |
| 8 | xgrammar | 32 | 0 | 0 | 32 | 0 | 0 | 0 | 0 | 49.901 (32) | 4.8683 (32) | 5.0437 (q90(n=32)) | 1421.9966 (4/4) | 21.8769 (4/4) | 1.000 |
| 32 | bolorgir | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 109.9996 (128) | 11.6051 (128) | 12.564 (q90(n=128)) | 2486.4374 (4/4) | 38.2529 (4/4) | 1.000 |
| 32 | xgrammar | 128 | 0 | 0 | 128 | 0 | 0 | 0 | 0 | 107.0813 (128) | 6.0292 (128) | 6.6037 (q90(n=128)) | 4196.9332 (4/4) | 64.5682 (4/4) | 1.000 |
| 128 | bolorgir | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 301.3122 (512) | 40.5245 (512) | 42.2973 (q90(n=512)) | 2852.2522 (4/4) | 43.8808 (4/4) | 1.000 |
| 128 | xgrammar | 512 | 0 | 0 | 512 | 0 | 0 | 0 | 0 | 260.1402 (512) | 12.2099 (512) | 13.795 (q90(n=512)) | 7851.6584 (4/4) | 120.7947 (4/4) | 1.000 |

## Independent re-validation and limits

Records with stored text: 0; without: 4056; stored-vs-revalidated agreement: 0 (differences: 0).
Length cuts checked: 0; completable by the bounded suffix test: 0; not: 0.
The v1 raw files of 2026-10-12 store no answer text; their validity cannot be re-checked and their `length` cuts cannot be called legal partials from the stored data (the earlier A1 observation is recorded in PROTOCOL.md, not re-verifiable).

