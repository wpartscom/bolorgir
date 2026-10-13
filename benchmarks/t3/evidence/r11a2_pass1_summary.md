# R11-A2: pass 1 of the incomplete transition results of 9a5baf3e, six libraries

## Numbers

25 results, 3 rounds; same pass-1 document on every library: 25. Noise (median max/min - 1 over rounds): 0.0453; threshold 0.045.

| ratio (median over results) | median | min | max | results > 1 | difference |
|---|---|---|---|---|---|
| target | 1.071 | 1.046 | 1.100 | 25/25 | yes |
| code | 1.027 | 0.900 | 1.060 | 23/25 | no |
| cand/6e62e0d1 | 1.003 | 0.902 | 1.035 | 13/25 | no |
| cand/7345490f | 1.101 | 0.766 | 1.143 | 24/25 | yes |
| cand/dbc5eeb | 1.100 | 0.773 | 1.141 | 24/25 | yes |
| 50cbd80e/7345490f | 1.004 | 0.796 | 1.038 | 14/25 | no |

## Reading (heuristic, not proof)

Reading rule (pre-registered): the release CPU target (-Dcpu=baseline) as the leading hypothesis for the target ratio.

Limits: Pre-registered reading rule, kept as a diagnostic heuristic: target = M(cand)/M(native) and code = M(native)/M(50cbd80e) medians against the threshold max(2 %, noise). The threshold is not a confidence interval; a median inside it does not prove equivalence, one outside it does not prove a cause, and the rule cannot attribute the residual to run conditions - it only says the measured ratio difference is not separable from the round-to-round noise by this design. Same document = equal documents; token paths and masks were not recorded.

Complete under the 10 s budget on these times (per round): cand [24, 24, 24]; native [24, 24, 24]; prev [24, 24, 24]; 50cbd80e [24, 24, 24]; 7345490f [24, 24, 24]; dbc5eeb [24, 24, 24]

| result | tok | c* | M cand | M native | M prev | M 50cbd80e | M 7345490f | M dbc5eeb | target | code | path |
|---|---|---|---|---|---|---|---|---|---|---|---|
| maskbench/Github_easy---o1008.json | llama-3.1 | 16 | 8.14 | 7.74 | 8.19 | 7.54 | 7.52 | 7.47 | 1.051 | 1.027 | same |
| maskbench/Github_easy---o13688.json | llama-3.1 | 16 | 8.07 | 7.70 | 8.18 | 7.38 | 7.36 | 7.41 | 1.048 | 1.043 | same |
| maskbench/Github_easy---o48800.json | llama-3.1 | 16 | 8.15 | 7.62 | 8.22 | 7.57 | 7.59 | 7.71 | 1.070 | 1.007 | same |
| maskbench/Github_easy---o64825.json | gpt2 | 48 | 7.37 | 7.01 | 7.38 | 6.76 | 6.89 | 6.79 | 1.050 | 1.038 | same |
| maskbench/Github_easy---o68314.json | gpt2 | 32 | 7.56 | 7.17 | 7.31 | 6.90 | 6.83 | 6.98 | 1.055 | 1.038 | same |
| maskbench/Github_medium---o32493.json | llama-3.1 | 16 | 8.21 | 7.57 | 8.01 | 7.43 | 7.45 | 7.23 | 1.084 | 1.020 | same |
| maskbench/Github_trivial---o84263.json | llama-3.1 | 16 | 8.12 | 7.47 | 8.26 | 7.40 | 7.36 | 7.38 | 1.086 | 1.009 | same |
| maskbench/Glaiveai2K---calculate_distance_51b4a417.json | llama-3.1 | 16 | 8.06 | 7.71 | 8.30 | 7.58 | 7.30 | 7.52 | 1.046 | 1.017 | same |
| maskbench/Glaiveai2K---generate_resume_d9ba16a3.json | gpt2 | 48 | 8.79 | 8.11 | 8.65 | 7.89 | 7.96 | 8.14 | 1.084 | 1.027 | same |
| maskbench/Kubernetes---kb_1154_Normalized.json | llama-3.1 | 16 | 13.43 | 12.55 | 14.88 | 13.95 | 17.52 | 17.36 | 1.070 | 0.900 | same |
| maskbench/Kubernetes---kb_231_Normalized.json | llama-3.1 | 16 | 8.35 | 7.68 | 8.26 | 7.36 | 7.33 | 7.45 | 1.086 | 1.043 | same |
| maskbench/Kubernetes---kb_379_Normalized.json | llama-3.1 | 16 | 9.28 | 8.49 | 9.16 | 8.52 | 8.25 | 8.41 | 1.092 | 0.997 | same |
| maskbench/Kubernetes---kb_576_Normalized.json | llama-3.1 | 16 | 8.41 | 7.81 | 8.17 | 7.41 | 7.49 | 7.57 | 1.076 | 1.054 | same |
| maskbench/Kubernetes---kb_613_Normalized.json | llama-3.1 | 16 | 8.36 | 7.64 | 8.16 | 7.46 | 7.55 | 7.33 | 1.095 | 1.023 | same |
| repo/kb_1057_Normalized.json | llama-3.1 | 16 | 8.37 | 7.77 | 8.15 | 7.63 | 7.39 | 7.42 | 1.077 | 1.019 | same |
| repo/kb_1088_Normalized.json | llama-3.1 | 16 | 8.40 | 7.84 | 8.27 | 7.50 | 7.41 | 7.38 | 1.071 | 1.046 | same |
| repo/kb_1123_Normalized.json | llama-3.1 | 16 | 8.06 | 7.52 | 8.25 | 7.29 | 7.41 | 7.44 | 1.071 | 1.031 | same |
| repo/kb_157_Normalized.json | llama-3.1 | 16 | 8.22 | 7.60 | 8.30 | 7.56 | 7.46 | 7.44 | 1.081 | 1.006 | same |
| repo/kb_199_Normalized.json | llama-3.1 | 16 | 8.13 | 7.70 | 8.27 | 7.43 | 7.57 | 7.30 | 1.057 | 1.036 | same |
| repo/kb_396_Normalized.json | llama-3.1 | 16 | 8.12 | 7.67 | 8.24 | 7.40 | 7.42 | 7.66 | 1.059 | 1.035 | same |
| repo/kb_480_Normalized.json | llama-3.1 | 16 | 8.25 | 7.79 | 8.10 | 7.57 | 7.50 | 7.62 | 1.059 | 1.029 | same |
| repo/kb_576_Normalized.json | llama-3.1 | 16 | 8.24 | 7.87 | 8.36 | 7.43 | 7.43 | 7.41 | 1.046 | 1.060 | same |
| repo/o41283.json | gpt2 | 48 | 8.39 | 7.80 | 8.32 | 7.78 | 7.70 | 7.74 | 1.075 | 1.002 | same |
| repo/o44130.json | llama-3.1 | 16 | 8.36 | 7.60 | 8.12 | 7.45 | 7.31 | 7.43 | 1.100 | 1.020 | same |
| repo/search_product_67f0c7b7.json | llama-3.1 | 16 | 8.28 | 7.85 | 8.25 | 7.72 | 7.57 | 7.36 | 1.055 | 1.016 | same |
