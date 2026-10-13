# Transition coverage matrix (candidate 9a5baf3e, TZ section 3.1)

81 rows, 67 results; lines: 7345490f 40, dbc5eeb 41. Outcomes: invalid_document 42 rows / 34 results / 68 invalid documents checked; incomplete 27 / 25; engine_unavailable 12 / 8. Undescribed rows: 0. The `on 6e62e0d1` column is the outcome of the previous candidate in its own run (sidecar records).

## Results (deduplicated)

| corpus | file | tok | outcome | entry | lines | on 6e62e0d1 | rows | triage |
|---|---|---|---|---|---|---|---|---|
| maskbench | Github_hard---o32934.json | byte | engine_unavailable | D4-4 | dbc5eeb | engine_unavailable | 1 | - |
| maskbench | JsonSchemaStore---bigquery-table.json | byte | engine_unavailable | D4-11m | 7345490f,dbc5eeb | engine_unavailable | 2 | - |
| maskbench | JsonSchemaStore---cryproj.53.schema.json | byte | engine_unavailable | D4-5 | dbc5eeb | engine_unavailable | 1 | - |
| maskbench | JsonSchemaStore---tye-schema.json | byte | engine_unavailable | D4-3m | dbc5eeb | engine_unavailable | 1 | - |
| repo | bigquery-table.json | byte | engine_unavailable | D4-11 | 7345490f,dbc5eeb | engine_unavailable | 2 | - |
| repo | cryproj.dev.schema.json | byte | engine_unavailable | D4-12 | 7345490f,dbc5eeb | engine_unavailable | 2 | - |
| repo | o31351.json | byte | engine_unavailable | D4-10 | 7345490f,dbc5eeb | engine_unavailable | 2 | - |
| repo | tye-schema.json | byte | engine_unavailable | D4-3 | dbc5eeb | engine_unavailable | 1 | - |
| maskbench | Github_easy---o64825.json | gpt2 | incomplete | D4-39 | 7345490f | generatable (no transition there) | 1 | - |
| maskbench | Github_easy---o68314.json | gpt2 | incomplete | D4-40 | 7345490f | generatable (no transition there) | 1 | - |
| maskbench | Glaiveai2K---generate_resume_d9ba16a3.json | gpt2 | incomplete | D4-34 | 7345490f | incomplete | 1 | - |
| maskbench | Github_easy---o1008.json | llama-3.1 | incomplete | D4-27 | 7345490f | incomplete | 1 | - |
| maskbench | Github_easy---o13688.json | llama-3.1 | incomplete | D4-28 | 7345490f | incomplete | 1 | - |
| maskbench | Github_easy---o48800.json | llama-3.1 | incomplete | D4-29 | 7345490f | incomplete | 1 | - |
| maskbench | Github_medium---o32493.json | llama-3.1 | incomplete | D4-30 | 7345490f | incomplete | 1 | - |
| maskbench | Github_trivial---o84263.json | llama-3.1 | incomplete | D4-31 | 7345490f | incomplete | 1 | - |
| maskbench | Glaiveai2K---calculate_distance_51b4a417.json | llama-3.1 | incomplete | D4-32 | 7345490f | incomplete | 1 | - |
| maskbench | Kubernetes---kb_1154_Normalized.json | llama-3.1 | incomplete | D4-2 | 7345490f | incomplete | 1 | - |
| maskbench | Kubernetes---kb_231_Normalized.json | llama-3.1 | incomplete | D4-35 | 7345490f,dbc5eeb | incomplete | 2 | - |
| maskbench | Kubernetes---kb_379_Normalized.json | llama-3.1 | incomplete | D4-36 | 7345490f | incomplete | 1 | - |
| maskbench | Kubernetes---kb_576_Normalized.json | llama-3.1 | incomplete | D4-37 | 7345490f | incomplete | 1 | - |
| maskbench | Kubernetes---kb_613_Normalized.json | llama-3.1 | incomplete | D4-38 | 7345490f | incomplete | 1 | - |
| repo | o41283.json | gpt2 | incomplete | D4-13 | 7345490f | incomplete | 1 | - |
| repo | kb_1057_Normalized.json | llama-3.1 | incomplete | D4-17 | 7345490f | incomplete | 1 | - |
| repo | kb_1088_Normalized.json | llama-3.1 | incomplete | D4-14 | 7345490f | incomplete | 1 | - |
| repo | kb_1123_Normalized.json | llama-3.1 | incomplete | D4-18 | 7345490f | incomplete | 1 | - |
| repo | kb_157_Normalized.json | llama-3.1 | incomplete | D4-19 | 7345490f | incomplete | 1 | - |
| repo | kb_199_Normalized.json | llama-3.1 | incomplete | D4-20 | 7345490f | incomplete | 1 | - |
| repo | kb_396_Normalized.json | llama-3.1 | incomplete | D4-21 | 7345490f | incomplete | 1 | - |
| repo | kb_480_Normalized.json | llama-3.1 | incomplete | D4-22 | 7345490f | incomplete | 1 | - |
| repo | kb_576_Normalized.json | llama-3.1 | incomplete | D4-23 | 7345490f | incomplete | 1 | - |
| repo | o44130.json | llama-3.1 | incomplete | D4-25 | 7345490f,dbc5eeb | incomplete | 2 | - |
| repo | search_product_67f0c7b7.json | llama-3.1 | incomplete | D4-26 | 7345490f | incomplete | 1 | - |
| maskbench | Github_hard---o21330.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 2/2 checked |
| maskbench | Github_hard---o21780.json | byte | invalid_document | D4-6 | 7345490f,dbc5eeb | invalid_document | 2 | class D, 3/3 checked |
| maskbench | Github_hard---o3905.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 2/2 checked |
| maskbench | Github_hard---o41323.json | byte | invalid_document | D4-6 | 7345490f,dbc5eeb | invalid_document | 2 | class D, 2/2 checked |
| maskbench | Github_hard---o54559.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| maskbench | Github_hard---o61027.json | byte | invalid_document | D4-6 | 7345490f,dbc5eeb | invalid_document | 2 | class D, 2/2 checked |
| maskbench | Github_hard---o68416.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| maskbench | Github_medium---o25781.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| maskbench | Github_medium---o55596.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| maskbench | Github_medium---o61004.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| maskbench | Github_medium---o62963.json | byte | invalid_document | D4-6 | 7345490f,dbc5eeb | invalid_document | 2 | class D, 3/3 checked |
| maskbench | Github_medium---o71266.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| maskbench | Github_trivial---o14485.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 7/7 checked |
| maskbench | Github_trivial---o66073.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| maskbench | JsonSchemaStore---vercel.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| maskbench | Github_ultra---o18644.json | gpt2 | invalid_document | D4-6 | 7345490f | invalid_document | 1 | class D, 3/3 checked |
| repo | bozr.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| repo | o14485.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 6/6 checked |
| repo | o21330.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| repo | o21780.json | byte | invalid_document | D4-6 | 7345490f,dbc5eeb | invalid_document | 2 | class D, 2/2 checked |
| repo | o41197.json | byte | invalid_document | D4-6 | 7345490f,dbc5eeb | invalid_document | 2 | class D, 2/2 checked |
| repo | o41278.json | byte | invalid_document | D4-6 | 7345490f | invalid_document | 1 | class D, 1/1 checked |
| repo | o41290.json | byte | invalid_document | D4-6 | 7345490f,dbc5eeb | invalid_document | 2 | class D, 1/1 checked |
| repo | o41303.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 4/4 checked |
| repo | o41800.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| repo | o5327.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| repo | o61004.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| repo | o61027.json | byte | invalid_document | D4-6 | 7345490f,dbc5eeb | invalid_document | 2 | class D, 1/1 checked |
| repo | o66073.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 2/2 checked |
| repo | o69733.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 3/3 checked |
| repo | o71266.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 2/2 checked |
| repo | o74556.json | byte | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 5/5 checked |
| repo | o47116.json | gpt2 | invalid_document | D4-6 | dbc5eeb | invalid_document | 1 | class D, 1/1 checked |
| repo | o48422.json | llama-3.1 | invalid_document | D4-6 | 7345490f | invalid_document | 1 | class D, 1/1 checked |

## All rows

| line | corpus | file | tok | outcome | entry | on 6e62e0d1 | line passes | candidate passes |
|---|---|---|---|---|---|---|---|---|
| dbc5eeb | repo | o44130.json | llama-3.1 | incomplete | D4-25 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| dbc5eeb | repo | o21330.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 19, invalid_document 1 |
| dbc5eeb | repo | o21780.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 17, completed_valid 3 | completed_valid 12, incomplete:time_limit 5, invalid_document 2, incomplete:step_limit 1 |
| dbc5eeb | repo | o31351.json | byte | engine_unavailable | D4-10 | engine_unavailable | incomplete:time_limit 19, completed_valid 1 | incomplete:step_limit 12, incomplete:time_limit 4, completed_valid 3, engine_unavailable 1 |
| dbc5eeb | repo | o41197.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 19, completed_valid 1 | incomplete:step_limit 17, invalid_document 2, completed_valid 1 |
| dbc5eeb | repo | o41290.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 19, completed_valid 1 | incomplete:step_limit 18, completed_valid 1, invalid_document 1 |
| dbc5eeb | repo | o41303.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 19, completed_valid 1 | incomplete:step_limit 14, invalid_document 4, completed_valid 2 |
| dbc5eeb | repo | o47116.json | gpt2 | invalid_document | D4-6 | invalid_document | completed_valid 18, incomplete:time_limit 2 | completed_valid 19, invalid_document 1 |
| dbc5eeb | repo | o61027.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 18, completed_valid 2 | completed_valid 11, incomplete:time_limit 5, incomplete:step_limit 3, invalid_document 1 |
| dbc5eeb | repo | o41800.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 13, incomplete:time_limit 7 | completed_valid 13, incomplete:step_limit 6, invalid_document 1 |
| dbc5eeb | repo | o5327.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 12, incomplete:time_limit 8 | completed_valid 16, incomplete:step_limit 3, invalid_document 1 |
| dbc5eeb | repo | o61004.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 19, invalid_document 1 |
| dbc5eeb | repo | o69733.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 19, completed_valid 1 | incomplete:step_limit 16, invalid_document 3, completed_valid 1 |
| dbc5eeb | repo | o71266.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 18, invalid_document 2 |
| dbc5eeb | repo | o74556.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 19, completed_valid 1 | incomplete:step_limit 14, invalid_document 5, completed_valid 1 |
| dbc5eeb | repo | o14485.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 14, invalid_document 6 |
| dbc5eeb | repo | o66073.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 18, invalid_document 2 |
| dbc5eeb | repo | bigquery-table.json | byte | engine_unavailable | D4-11 | engine_unavailable | completed_valid 15, incomplete:time_limit 5 | completed_valid 16, incomplete:time_limit 3, engine_unavailable 1 |
| dbc5eeb | repo | bozr.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 19, invalid_document 1 |
| dbc5eeb | repo | cryproj.dev.schema.json | byte | engine_unavailable | D4-12 | engine_unavailable | incomplete:step_limit 12, incomplete:time_limit 7, completed_valid 1 | incomplete:step_limit 18, completed_valid 1, engine_unavailable 1 |
| dbc5eeb | repo | tye-schema.json | byte | engine_unavailable | D4-3 | engine_unavailable | incomplete:time_limit 19, completed_valid 1 | completed_valid 10, engine_unavailable 5, incomplete:time_limit 4, incomplete:step_limit 1 |
| dbc5eeb | maskbench | Github_hard---o21330.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 18, invalid_document 2 |
| dbc5eeb | maskbench | Github_hard---o21780.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 17, completed_valid 3 | completed_valid 10, incomplete:time_limit 6, invalid_document 3, incomplete:step_limit 1 |
| dbc5eeb | maskbench | Github_hard---o32934.json | byte | engine_unavailable | D4-4 | engine_unavailable | completed_valid 19, incomplete:time_limit 1 | completed_valid 17, engine_unavailable 2, incomplete:step_limit 1 |
| dbc5eeb | maskbench | Github_hard---o3905.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 18, invalid_document 2 |
| dbc5eeb | maskbench | Github_hard---o41323.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 18, completed_valid 1, incomplete:step_limit 1 | incomplete:step_limit 17, invalid_document 2, completed_valid 1 |
| dbc5eeb | maskbench | Github_hard---o54559.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 19, invalid_document 1 |
| dbc5eeb | maskbench | Github_hard---o61027.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 17, completed_valid 3 | incomplete:time_limit 8, incomplete:step_limit 6, completed_valid 4, invalid_document 2 |
| dbc5eeb | maskbench | Github_hard---o68416.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 10, incomplete:time_limit 10 | completed_valid 10, incomplete:step_limit 9, invalid_document 1 |
| dbc5eeb | maskbench | Github_medium---o25781.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 18, incomplete:time_limit 2 | completed_valid 19, invalid_document 1 |
| dbc5eeb | maskbench | Github_medium---o55596.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 19, incomplete:time_limit 1 | completed_valid 19, invalid_document 1 |
| dbc5eeb | maskbench | Github_medium---o61004.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 19, invalid_document 1 |
| dbc5eeb | maskbench | Github_medium---o62963.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 17, incomplete:step_limit 2, completed_valid 1 | incomplete:step_limit 14, completed_valid 3, invalid_document 3 |
| dbc5eeb | maskbench | Github_medium---o71266.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 19, invalid_document 1 |
| dbc5eeb | maskbench | Github_trivial---o14485.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 13, invalid_document 7 |
| dbc5eeb | maskbench | Github_trivial---o66073.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 19, invalid_document 1 |
| dbc5eeb | maskbench | JsonSchemaStore---bigquery-table.json | byte | engine_unavailable | D4-11m | engine_unavailable | completed_valid 12, incomplete:time_limit 8 | completed_valid 13, incomplete:time_limit 5, engine_unavailable 2 |
| dbc5eeb | maskbench | JsonSchemaStore---cryproj.53.schema.json | byte | engine_unavailable | D4-5 | engine_unavailable | incomplete:step_limit 15, incomplete:time_limit 4, completed_valid 1 | incomplete:step_limit 18, completed_valid 1, engine_unavailable 1 |
| dbc5eeb | maskbench | JsonSchemaStore---tye-schema.json | byte | engine_unavailable | D4-3m | engine_unavailable | incomplete:time_limit 17, completed_valid 3 | completed_valid 8, engine_unavailable 6, incomplete:time_limit 5, incomplete:step_limit 1 |
| dbc5eeb | maskbench | JsonSchemaStore---vercel.json | byte | invalid_document | D4-6 | invalid_document | completed_valid 20 | completed_valid 19, invalid_document 1 |
| dbc5eeb | maskbench | Kubernetes---kb_231_Normalized.json | llama-3.1 | incomplete | D4-35 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | repo | o44130.json | llama-3.1 | incomplete | D4-25 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | repo | o21780.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 16, completed_valid 4 | completed_valid 12, incomplete:time_limit 5, invalid_document 2, incomplete:step_limit 1 |
| 7345490f | repo | o31351.json | byte | engine_unavailable | D4-10 | engine_unavailable | incomplete:time_limit 19, completed_valid 1 | incomplete:step_limit 12, incomplete:time_limit 4, completed_valid 3, engine_unavailable 1 |
| 7345490f | repo | o41197.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 14, incomplete:step_limit 5, completed_valid 1 | incomplete:step_limit 17, invalid_document 2, completed_valid 1 |
| 7345490f | repo | o41278.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 14, incomplete:step_limit 5, completed_valid 1 | incomplete:step_limit 18, completed_valid 1, invalid_document 1 |
| 7345490f | repo | o41290.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 14, incomplete:step_limit 5, completed_valid 1 | incomplete:step_limit 18, completed_valid 1, invalid_document 1 |
| 7345490f | repo | o48422.json | llama-3.1 | invalid_document | D4-6 | invalid_document | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 16, completed_valid 3, invalid_document 1 |
| 7345490f | repo | o61027.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 18, completed_valid 2 | completed_valid 11, incomplete:time_limit 5, incomplete:step_limit 3, invalid_document 1 |
| 7345490f | repo | o41283.json | gpt2 | incomplete | D4-13 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | repo | search_product_67f0c7b7.json | llama-3.1 | incomplete | D4-26 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | repo | bigquery-table.json | byte | engine_unavailable | D4-11 | engine_unavailable | completed_valid 15, incomplete:time_limit 5 | completed_valid 16, incomplete:time_limit 3, engine_unavailable 1 |
| 7345490f | repo | cryproj.dev.schema.json | byte | engine_unavailable | D4-12 | engine_unavailable | incomplete:time_limit 13, incomplete:step_limit 6, completed_valid 1 | incomplete:step_limit 18, completed_valid 1, engine_unavailable 1 |
| 7345490f | repo | kb_1057_Normalized.json | llama-3.1 | incomplete | D4-17 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | repo | kb_1088_Normalized.json | llama-3.1 | incomplete | D4-14 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | repo | kb_1123_Normalized.json | llama-3.1 | incomplete | D4-18 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | repo | kb_157_Normalized.json | llama-3.1 | incomplete | D4-19 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | repo | kb_199_Normalized.json | llama-3.1 | incomplete | D4-20 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | repo | kb_396_Normalized.json | llama-3.1 | incomplete | D4-21 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | repo | kb_480_Normalized.json | llama-3.1 | incomplete | D4-22 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | repo | kb_576_Normalized.json | llama-3.1 | incomplete | D4-23 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Github_easy---o1008.json | llama-3.1 | incomplete | D4-27 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Github_easy---o13688.json | llama-3.1 | incomplete | D4-28 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Github_easy---o48800.json | llama-3.1 | incomplete | D4-29 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Github_easy---o64825.json | gpt2 | incomplete | D4-39 | generatable | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Github_easy---o68314.json | gpt2 | incomplete | D4-40 | generatable | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Github_hard---o21780.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 16, completed_valid 4 | completed_valid 10, incomplete:time_limit 6, invalid_document 3, incomplete:step_limit 1 |
| 7345490f | maskbench | Github_hard---o41323.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 13, incomplete:step_limit 6, completed_valid 1 | incomplete:step_limit 17, invalid_document 2, completed_valid 1 |
| 7345490f | maskbench | Github_hard---o61027.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 18, completed_valid 2 | incomplete:time_limit 8, incomplete:step_limit 6, completed_valid 4, invalid_document 2 |
| 7345490f | maskbench | Github_medium---o32493.json | llama-3.1 | incomplete | D4-30 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Github_medium---o62963.json | byte | invalid_document | D4-6 | invalid_document | incomplete:time_limit 13, incomplete:step_limit 5, completed_valid 2 | incomplete:step_limit 14, completed_valid 3, invalid_document 3 |
| 7345490f | maskbench | Github_trivial---o84263.json | llama-3.1 | incomplete | D4-31 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Github_ultra---o18644.json | gpt2 | invalid_document | D4-6 | invalid_document | incomplete:time_limit 15, completed_valid 5 | incomplete:time_limit 10, completed_valid 7, invalid_document 3 |
| 7345490f | maskbench | Glaiveai2K---calculate_distance_51b4a417.json | llama-3.1 | incomplete | D4-32 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Glaiveai2K---generate_resume_d9ba16a3.json | gpt2 | incomplete | D4-34 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | JsonSchemaStore---bigquery-table.json | byte | engine_unavailable | D4-11m | engine_unavailable | completed_valid 12, incomplete:time_limit 8 | completed_valid 13, incomplete:time_limit 5, engine_unavailable 2 |
| 7345490f | maskbench | Kubernetes---kb_1154_Normalized.json | llama-3.1 | incomplete | D4-2 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Kubernetes---kb_231_Normalized.json | llama-3.1 | incomplete | D4-35 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Kubernetes---kb_379_Normalized.json | llama-3.1 | incomplete | D4-36 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Kubernetes---kb_576_Normalized.json | llama-3.1 | incomplete | D4-37 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |
| 7345490f | maskbench | Kubernetes---kb_613_Normalized.json | llama-3.1 | incomplete | D4-38 | incomplete | incomplete:time_limit 19, completed_valid 1 | incomplete:time_limit 20 |

Evidence per outcome class: invalid_document - `review/triage.json` of the set (class D rule, documents checked) and `docs/supported_features.md` section 9; incomplete - `benchmarks/t3/evidence/r11a2_pass1_summary.md`; engine_unavailable - `benchmarks/t3/evidence/r11bc2_summary.md`; the pass records of both candidate runs - the sets' `coverage_*.passes.json.gz` sidecars.
