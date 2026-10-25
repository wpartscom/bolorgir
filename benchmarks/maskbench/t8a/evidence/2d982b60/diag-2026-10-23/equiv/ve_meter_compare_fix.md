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
| library hash recorded on both sides | ok | {"orig": "9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41", "fix": "2d982b60cec55f3328206570659c29307e2050e3453b97b956649aa48e45b94e"} |
| legs loaded different libraries | ok | {"orig": "9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41", "fix": "2d982b60cec55f3328206570659c29307e2050e3453b97b956649aa48e45b94e"} |
| orig library is the run wheel | ok | 9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41 |
| fix library is the checkpoint | ok | 2d982b60cec55f3328206570659c29307e2050e3453b97b956649aa48e45b94e |

| file | instances | calls | orig mask s | orig aborts | fix mask s | fix aborts | delta s |
|---|---:|---:|---:|---:|---:|---:|---:|
| Github_easy---o13129.json | 4 | 0 | 239.83 | 239.83 (4) | 224.03 | 224.03 (4) | -15.8 |
| Github_hard---o12278.json | 7 | 240 | 475.71 | 412.65 (7) | 392.42 | 392.27 (7) | -83.28 |
| Github_hard---o74466.json | 5 | 681 | 427.32 | 229.08 (2) | 219.21 | 215.75 (2) | -208.11 |
| Github_medium---o54553.json | 4 | 216 | 326.72 | 89.74 (1) | 186.01 | 82.54 (1) | -140.71 |
| Github_medium---o90904.json | 6 | 317 | 146.28 | 2.4 (2) | 13.5 | 1.56 (2) | -132.79 |
| JsonSchemaStore---codeship-services.json | 4 | 61 | 260.22 | 188.23 (3) | 194.45 | 183.46 (3) | -65.77 |

Totals: orig 1876.1 s of masks (1161.9 s in 19 refusals, wall 1876.4 s), fix 1229.6 s (1099.6 s in 19 refusals, wall 1229.9 s); mismatched files 0.

Libraries: orig `9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41`, fix `2d982b60cec55f3328206570659c29307e2050e3453b97b956649aa48e45b94e`.

TBM orig: {"p50": 0.007, "p75": 830.815, "p90": 946.654, "p99": 57398.151, "share_calls_ge_1s": 0.0926, "share_time_ge_1s": 0.8652}
TBM fix:  {"p50": 0.009, "p75": 1.278, "p90": 38.415, "p99": 55534.098, "share_calls_ge_1s": 0.0326, "share_time_ge_1s": 0.9828}

VERDICT: all checks passed
