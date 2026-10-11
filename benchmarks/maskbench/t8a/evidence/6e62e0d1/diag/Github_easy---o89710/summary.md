# T8a timeout diagnosis: Github_easy---o89710.json

- exit -14 (timeout, SIGALRM), wall 903.467 s; outcome {"pending_file": 1}
- compile: {"us": 419572}
- steps 0, compute_mask 0.0 s, commit_token 0.0 s
- at the end: instance 0, token 0: compute_mask or commit_token (0.425 s after the file started)
- peak memory: ru_maxrss 838956 kB, sampled VmHWM 838956 kB
- run dir unchanged: True

| instance | tokens | steps | rejected at | mask s | max mask ms (token) | >100 ms | >900 ms | serialize |
|---:|---:|---:|---:|---:|---|---:|---:|---|
| 0 | 111 | 0 | None | 0.0 | 0 (None) | 0 | 0 | {"us": 2981, "bytes": 382} |
