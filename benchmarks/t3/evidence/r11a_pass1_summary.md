# R11-A: pass 1 of the incomplete transition results, five libraries

27 results, 3 rounds; same pass-1 document on every library: 27. Noise (median max/min - 1 over rounds): 0.0291; threshold 0.029.

| ratio (median over results) | median | min | max | results > 1 | difference |
|---|---|---|---|---|---|
| target | 1.096 | 1.024 | 1.126 | 27/27 | yes |
| code | 0.993 | 0.954 | 1.025 | 10/27 | no |
| cand/7345490f | 1.095 | 0.848 | 1.173 | 26/27 | yes |
| cand/dbc5eeb | 1.096 | 0.860 | 1.244 | 26/27 | yes |
| 50cbd80e/7345490f | 1.009 | 0.769 | 1.189 | 23/27 | no |

Attribution: the release CPU target (-Dcpu=baseline).

Complete under the 10 s budget on these times (per round): cand [26, 26, 26]; native [26, 26, 26]; 50cbd80e [26, 26, 26]; 7345490f [26, 26, 26]; dbc5eeb [26, 26, 26]

| result | tok | c* | M cand | M native | M 50cbd80e | M 7345490f | M dbc5eeb | target | code | path |
|---|---|---|---|---|---|---|---|---|---|---|
| repo/calculate_area_4c8e9fd1.json | byte | 48 | 7.62 | 7.44 | 7.72 | 6.49 | 6.12 | 1.024 | 0.963 | same |
| maskbench/Glaiveai2K---generate_invoice_1c946d60.json | gpt2 | 32 | 7.93 | 7.49 | 7.36 | 7.45 | 7.34 | 1.059 | 1.018 | same |
| maskbench/Glaiveai2K---generate_resume_d9ba16a3.json | gpt2 | 48 | 8.57 | 8.08 | 7.89 | 7.86 | 8.01 | 1.060 | 1.025 | same |
| repo/kb_1008_Normalized.json | gpt2 | 32 | 7.93 | 7.52 | 7.34 | 7.43 | 7.30 | 1.055 | 1.025 | same |
| repo/o41283.json | gpt2 | 48 | 8.34 | 7.62 | 7.66 | 7.61 | 7.63 | 1.094 | 0.996 | same |
| maskbench/Github_easy---o1008.json | llama-3.1 | 16 | 8.14 | 7.37 | 7.53 | 7.45 | 7.47 | 1.106 | 0.979 | same |
| maskbench/Github_easy---o13688.json | llama-3.1 | 16 | 8.07 | 7.56 | 7.41 | 7.39 | 7.59 | 1.067 | 1.020 | same |
| maskbench/Github_easy---o48800.json | llama-3.1 | 16 | 8.21 | 7.47 | 7.63 | 7.40 | 7.50 | 1.100 | 0.979 | same |
| maskbench/Github_medium---o32493.json | llama-3.1 | 16 | 8.10 | 7.30 | 7.47 | 7.40 | 7.36 | 1.110 | 0.977 | same |
| maskbench/Github_trivial---o84263.json | llama-3.1 | 16 | 8.30 | 7.43 | 7.43 | 7.38 | 7.31 | 1.117 | 1.000 | same |
| maskbench/Glaiveai2K---calculate_distance_51b4a417.json | llama-3.1 | 16 | 8.36 | 7.51 | 7.49 | 7.42 | 7.38 | 1.112 | 1.003 | same |
| maskbench/Kubernetes---kb_1154_Normalized.json | llama-3.1 | 16 | 14.84 | 13.63 | 13.46 | 17.50 | 17.26 | 1.089 | 1.012 | same |
| maskbench/Kubernetes---kb_231_Normalized.json | llama-3.1 | 16 | 8.23 | 7.31 | 7.46 | 7.45 | 7.38 | 1.126 | 0.979 | same |
| maskbench/Kubernetes---kb_379_Normalized.json | llama-3.1 | 16 | 9.11 | 8.39 | 8.40 | 8.21 | 8.29 | 1.085 | 0.999 | same |
| maskbench/Kubernetes---kb_576_Normalized.json | llama-3.1 | 16 | 8.20 | 7.31 | 7.67 | 7.31 | 7.40 | 1.122 | 0.954 | same |
| maskbench/Kubernetes---kb_613_Normalized.json | llama-3.1 | 16 | 7.89 | 7.41 | 7.48 | 7.29 | 7.42 | 1.065 | 0.991 | same |
| repo/kb_1057_Normalized.json | llama-3.1 | 16 | 8.14 | 7.33 | 7.68 | 7.34 | 7.37 | 1.111 | 0.955 | same |
| repo/kb_1088_Normalized.json | llama-3.1 | 16 | 8.09 | 7.55 | 7.53 | 7.44 | 7.52 | 1.071 | 1.003 | same |
| repo/kb_1123_Normalized.json | llama-3.1 | 16 | 8.08 | 7.19 | 7.54 | 7.38 | 7.37 | 1.123 | 0.954 | same |
| repo/kb_157_Normalized.json | llama-3.1 | 16 | 8.17 | 7.46 | 7.34 | 7.38 | 7.37 | 1.096 | 1.016 | same |
| repo/kb_199_Normalized.json | llama-3.1 | 16 | 8.10 | 7.49 | 7.63 | 7.42 | 7.36 | 1.081 | 0.981 | same |
| repo/kb_396_Normalized.json | llama-3.1 | 16 | 8.13 | 7.46 | 7.45 | 7.43 | 7.37 | 1.090 | 1.001 | same |
| repo/kb_480_Normalized.json | llama-3.1 | 16 | 8.32 | 7.46 | 7.62 | 7.30 | 7.37 | 1.115 | 0.979 | same |
| repo/kb_576_Normalized.json | llama-3.1 | 16 | 8.14 | 7.47 | 7.53 | 7.46 | 7.44 | 1.089 | 0.993 | same |
| repo/o43083.json | llama-3.1 | 16 | 8.09 | 7.33 | 7.54 | 7.38 | 7.52 | 1.103 | 0.972 | same |
| repo/o44130.json | llama-3.1 | 16 | 8.16 | 7.33 | 7.52 | 7.41 | 7.27 | 1.113 | 0.975 | same |
| repo/search_product_67f0c7b7.json | llama-3.1 | 16 | 8.18 | 7.43 | 7.42 | 7.39 | 7.47 | 1.101 | 1.001 | same |
