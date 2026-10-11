# T8a timeout diagnosis: Github_medium---o65503.json

- exit -14 (timeout, SIGALRM), wall 903.556 s; outcome {"pending_file": 1}
- compile: {"us": 575}
- steps 972, compute_mask 899.5 s, commit_token 0.0 s
- at the end: instance 3, token 143: compute_mask or commit_token (899.556 s after the file started)
- peak memory: ru_maxrss 691644 kB, sampled VmHWM 692216 kB
- run dir unchanged: True

| instance | tokens | steps | rejected at | mask s | max mask ms (token) | >100 ms | >900 ms | serialize |
|---:|---:|---:|---:|---:|---|---:|---:|---|
| 0 | 281 | 281 | None | 253.9 | 1163 (51) | 243 | 243 | {"us": 2880, "bytes": 1038} |
| 1 | 281 | 281 | None | 265.0 | 1164 (37) | 258 | 258 | {"us": 365, "bytes": 1072} |
| 2 | 282 | 267 | 266 | 250.6 | 1113 (156) | 244 | 244 | {"us": 302, "bytes": 1072} |
| 3 | 281 | 143 | None | 130.1 | 1113 (50) | 126 | 126 | {"us": 296, "bytes": 1071} |
