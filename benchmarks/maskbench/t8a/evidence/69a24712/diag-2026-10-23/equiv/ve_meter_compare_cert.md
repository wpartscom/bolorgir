# VE replays: run wheel vs corrected candidate

Both sides include the duration of the refusing call. All checks
below must pass for the timings to mean anything.

| check | result | detail |
|---|---|---|
| file sets equal | ok | orig 6, fix 6; only orig [], only fix [] |
| files field matches the table | ok | 6 / 6 / 6 / 6 |
| instance counts equal | ok | {} |
| call counts equal | ok | {} |
| call total matches the table | ok | 1515 / 1515 |
| refusal counts equal | ok | {} |
| no stream mismatches | ok | {} |
| no capped runs | ok | {"orig": [], "fix": []} |
| no unexpected errors | ok | {"orig": [], "fix": []} |
| expected compile limits match | ok | orig ['Github_easy---o58839.json', 'Github_hard---o90895.json'], fix ['Github_easy---o58839.json', 'Github_hard---o90895.json'] |
| TBM percentiles present | ok | {"orig": ["p50", "p75", "p90", "p99", "share_calls_ge_1s", "share_time_ge_1s"], "fix": ["p50", "p75", "p90", "p99", "share_calls_ge_1s", "share_time_ge_1s"]} |
| library hash recorded on both sides | ok | {"orig": "9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41", "fix": "69a2471245699536e2b95d5b67f6c1070e0b8891542f0ef104b8f045daf426da"} |
| legs loaded different libraries | ok | {"orig": "9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41", "fix": "69a2471245699536e2b95d5b67f6c1070e0b8891542f0ef104b8f045daf426da"} |
| orig library is the run wheel | ok | 9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41 |
| fix library is the checkpoint | ok | 69a2471245699536e2b95d5b67f6c1070e0b8891542f0ef104b8f045daf426da |

| file | instances | calls | orig mask s | orig aborts | fix mask s | fix aborts | delta s |
|---|---:|---:|---:|---:|---:|---:|---:|
| Github_easy---o13129.json | 4 | 0 | 239.83 | 239.83 (4) | 236.9 | 236.9 (4) | -2.93 |
| Github_hard---o12278.json | 7 | 240 | 475.71 | 412.65 (7) | 411.58 | 411.43 (7) | -64.13 |
| Github_hard---o74466.json | 5 | 681 | 427.32 | 229.08 (2) | 223.52 | 220.21 (2) | -203.79 |
| Github_medium---o54553.json | 4 | 216 | 326.72 | 89.74 (1) | 184.51 | 85.05 (1) | -142.21 |
| Github_medium---o90904.json | 6 | 317 | 146.28 | 2.4 (2) | 13.28 | 1.55 (2) | -133.0 |
| JsonSchemaStore---codeship-services.json | 4 | 61 | 260.22 | 188.23 (3) | 203.35 | 192.3 (3) | -56.87 |

Totals: orig 1876.1 s of masks (1161.9 s in 19 refusals, wall 1876.4 s), fix 1273.1 s (1147.4 s in 19 refusals, wall 1273.4 s); mismatched files 0.

Libraries: orig `9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41`, fix `69a2471245699536e2b95d5b67f6c1070e0b8891542f0ef104b8f045daf426da`.

TBM orig: {"p50": 0.007, "p75": 830.815, "p90": 946.654, "p99": 57398.151, "share_calls_ge_1s": 0.0926, "share_time_ge_1s": 0.8652}
TBM fix:  {"p50": 0.008, "p75": 1.287, "p90": 30.921, "p99": 58013.229, "share_calls_ge_1s": 0.0326, "share_time_ge_1s": 0.9837}

VERDICT: all checks passed
