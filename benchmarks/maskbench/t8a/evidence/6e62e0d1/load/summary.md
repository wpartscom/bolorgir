# T8a static load estimate: blg-spec-v1, core 6e62e0d1526c6e53

**A scenario estimate, not a count of guaranteed timeouts; the ranges are no guaranteed bounds of the duration.** The engine was not run. Only the participant blg-spec-v1, one worker; the four other participants are not estimated.

## Inputs

- command: `benchmarks/maskbench/t8a/diag/t8a_load.py --run-dir benchmarks/local/t8a-6e62e0d1 --data benchmarks/external/jsonschemabench-maskbench/maskbench/data --tokenizer benchmarks/local/hf-cache/hub/models--unsloth--Meta-Llama-3.1-8B-Instruct/snapshots/a2856192dd7c25b842431f39c179a6c2c2f627d1 --t3 benchmarks/coverage/spec-v1/t3-6e62e0d1/coverage_maskbench.json.gz --calib benchmarks/local/t8a-6e62e0d1/smoke/out/blg-spec-v1 --calib benchmarks/local/t8a-6e62e0d1/out/blg-spec-v1 --out benchmarks/local/t8a-load-6e62e0d1/full-2` (cwd `benchmarks/local/release-0.2.0`)
- report: `t8a_load_report.py` db6d0f3d5d8adf109b10e6ef9502a6a9cc7ef6926694425d232c9aa56a7ba567
- t8a_load.py d320aae700e48d36adf02b5fb46f3e292e6f9112bb01a9355046083a0ca35aca; manifest 4d2e4eef8dce28e2119e8f5360ae7d51cef3298d4ba81ac946bf9b35e492cc99; reference streams 3640d156ee99413900064759ba05b778a7cf053e5eaf1fca916b823114c90f75; T3 coverage cea7561b2dd23793727a1063476d37d93f9b1c6641c3b4b4434803aeb11e86d2 (library 6e62e0d1526c6e5369457d2f7205675b7cb163e3374b96d8cb5115c28873e914, the manifest's core); wheel e5bb3f5bf7e5b19b21f96b1ff3ee4e74dad3490fcd95c7c9f082ccc9c39fd2b6; serializer 1140bfc5ccc23dee3d35a696ef4ffeb1844f4897faa4b965f885ae0cf1130dd9; tokenizer revision a2856192dd7c25b842431f39c179a6c2c2f627d1
- corpus files checked against the manifest: 11 306; without a T3 record: 0; calibration outputs: 40 (sha256 in inputs.json)
- outputs: files.jsonl.gz ac092c1de04771db1e1f0b256eb161c0125ba1f35f3aebb11bf3d2c8b21a0f74, calib.json 179cea26b01bf48e2ae5962e078e1a2db7f73fc9102d80e761bd82abdf5c84a7, problems: 0 (a problem is a stream that differs from the reference)

## Corpus

- files 11 306; compiled in T3 10 806, not compiled 500 (0 steps: compile_error assumed for the Llama tokenizer too); compiled without tests 1 236
- instances of compiled files: valid 13 627, invalid 22 343; serializer refusals: valid 0, invalid 0
- valid: 1 633 079 tokens, 1 605 492 steps (risk instances end at their T3 error); invalid: 3 194 103 tokens, 1 194 813 steps up to the rejection
- where the steps of an instance come from: {"t3 accept": 14328, "t3 reject byte": 21112, "t3 error": 522, "no t3 instance": 8}. Taking the T3 rejection byte for the Llama tokens is an assumption; on the calibration sample the predicted steps equal the measured ones for 75 of 75 instances, and the T3 compile status matches the T8a outcome for 42 of 42 files with a T8a result (pending markers: 2)
- risk files (an instance ended with RESOURCE_LIMIT or CANCELLED in T3): 142, instances 522 {"error:RESOURCE_LIMIT": 510, "error:CANCELLED": 12}, files with the error at byte 0: 13

| class | valid steps | invalid steps (to the rejection) |
|---|---:|---:|
| outside | 184 931 | 118 798 |
| key_closed | 197 937 | 189 406 |
| str_enum | 45 666 | 41 749 |
| key_open | 463 787 | 286 565 |
| str_free | 609 588 | 401 231 |
| str_pattern | 68 262 | 114 926 |
| str_format | 34 105 | 41 003 |
| str_maxlen | 1 216 | 1 135 |

## Calibration (measured steps)

36 calibration outputs with measured steps (of 40); a repeat is a step whose token prefix an earlier instance of the same file measured.

| class | steps | mean ms | > 100 ms |
|---|---:|---:|---:|
| key_closed | 338 | 0.06 | 0 |
| key_closed, repeat | 155 | 0.13 | 0 |
| key_open | 517 | 851.93 | 414 |
| key_open, repeat | 455 | 209.52 | 87 |
| outside | 239 | 1.59 | 0 |
| outside, repeat | 145 | 0.37 | 0 |
| str_enum | 64 | 0.12 | 0 |
| str_enum, repeat | 63 | 0.13 | 0 |
| str_free | 729 | 829.29 | 565 |
| str_free, repeat | 157 | 132.62 | 20 |
| str_pattern | 106 | 869.72 | 93 |
| str_pattern, repeat | 186 | 109.47 | 20 |

Slow steps (> 100 ms): median 1037.7 ms, p10 962.2, p90 1213.6, n 1199. outside, key_closed and str_enum were fast in the whole calibration sample. Open classes were about 1 s in some files and fast in others; repeats were mostly fast, but slow in Github_medium---o65503 (diagnosis); a fast repeat does not show a cache hit. str_format and str_maxlen have no calibration steps.

## Scenarios

- S1: every open-class step costs the median slow step
- S2: as S1, but exact prefix repeats of open classes cost the mean of the fast classes
- S3: every (class, repeat) costs its calibration mean (str_format, str_maxlen: the str_free means)

A file above 900 s is a timeout and costs 900 s; each chunk start costs 3.5 s (one per 100 files and one after each timeout). Risk files: "hang" = a timeout as o89710; "no hang" = the instance ends at its error step, and the time of that call is not counted.

| scenario | timeouts, no hang | timeouts, hang | days, no hang | days, hang |
|---|---:|---:|---:|---:|
| S1 | 487 | 620 | 20.38 | 21.47 |
| S2 | 235 | 373 | 13.64 | 14.89 |
| S3 | 189 | 326 | 12.62 | 13.88 |

### Validation on the calibration files

| scenario | files | Σpredicted / Σmeasured | predicted ≥ measured | median ratio (files > 1 s) | p10-p90 |
|---|---:|---:|---:|---:|---|
| S1 | 36 | 1.751 | 36 | 1.237 | [1.028, 2.425] |
| S2 | 36 | 1.102 | 32 | 1.048 | [0.998, 1.881] |
| S3 (leave one out) | 36 | 0.989 | 17 | 0.858 | [0.821, 1.563] |

Known timeouts of the run t8a-6e62e0d1 (seconds per scenario, no hang; None = risk file):

- Github_medium---o65503.json: {"S1": 1075.2, "S2": 522.3, "S3": 506.4, "risk_file": false}
- Github_easy---o89710.json: {"S1": null, "S2": null, "S3": null, "risk_file": true}

Files with more than 870 open-class steps: 485; of them not a timeout in S1: 0, in S2: 250, in S3: 296.

## Load per file (risk files hang)

| file time | S1 | S2 | S3 |
|---|---:|---:|---:|
| 0 s | 1736 | 1736 | 1736 |
| < 1 s | 345 | 345 | 345 |
| 1-10 s | 904 | 1364 | 1617 |
| 10-60 s | 3247 | 3728 | 3637 |
| 1-5 min | 2926 | 2863 | 2840 |
| 5-15 min | 1528 | 897 | 805 |
| > 900 s (timeout) | 620 | 373 | 326 |

| dataset | files | risk files | timeouts S1 | timeouts S3 | hours S1 | hours S3 |
|---|---:|---:|---:|---:|---:|---:|
| Github_medium | 1976 | 38 | 156 | 81 | 158.8 | 98.7 |
| Github_hard | 1240 | 40 | 237 | 114 | 131.2 | 91.8 |
| Kubernetes | 1064 | 0 | 34 | 8 | 57.1 | 31.4 |
| JsonSchemaStore | 492 | 29 | 88 | 56 | 47.6 | 34.4 |
| Github_easy | 1943 | 16 | 21 | 19 | 46.7 | 27.0 |
| Snowplow | 403 | 2 | 27 | 8 | 28.4 | 16.7 |
| Glaiveai2K | 1707 | 9 | 9 | 9 | 16.8 | 11.9 |
| Github_ultra | 164 | 3 | 41 | 25 | 13.6 | 11.2 |
| Github_trivial | 444 | 3 | 4 | 4 | 5.9 | 4.0 |
| WashingtonPost | 125 | 0 | 1 | 0 | 3.3 | 1.7 |
| BFCL | 1043 | 0 | 0 | 0 | 2.5 | 2.0 |
| JME | 100 | 2 | 2 | 2 | 1.7 | 1.5 |
| MCPspec | 45 | 0 | 0 | 0 | 0.8 | 0.5 |
| Synthesized | 413 | 0 | 0 | 0 | 0.2 | 0.1 |
| Handwritten | 147 | 0 | 0 | 0 | 0.0 | 0.0 |

Heaviest files in S3 (no hang, before the 900 s limit): Kubernetes---kb_47_Normalized.json 5791.2 s, Github_hard---o81117.json 5673.7 s, Github_hard---o21384.json 4939.5 s, Github_medium---o69760.json 4790.9 s, Github_ultra---o7578.json 4217.3 s, Github_ultra---o15627.json 3483.3 s, Github_easy---o40346.json 3286.0 s, JsonSchemaStore---sdf-schema-1.1.json 3190.7 s

## Limits

- The cost of an open-class step depends on the file and is bimodal; the mechanism in the core is not established. The scenarios are not a forecast.
- The calibration sample is small and biased (half of it the short BFCL_java files of the smoke).
- The rejection byte and the compile status come from T3 (byte tokenizer): an assumption, tested on the calibration sample only.
- Risk files: only o89710 was observed (no return of the first mask); "hang" assumes the same for every risk file. "No hang" leaves out the time of the call that ended with the error (o89710 counts 0 steps there, not a zero cost).
- Compile and serialization times are left out (milliseconds; 0.42 s for o89710).
- Every timeout needs its own T2c class for the acceptance.
