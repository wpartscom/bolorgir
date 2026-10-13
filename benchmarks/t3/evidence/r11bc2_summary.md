# R11-C2: the engine_unavailable passes of 9a5baf3e, replayed

| file | pass | end | seconds | as recorded | engine message, undecided reasons |
|---|---|---|---|---|---|
| Github_hard/Github_hard---o32934.json | 8 | engine_unavailable@38 | 2.9 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 18, "openobj_deps": 2, "openobj_gate": 2} |
| Github_hard/Github_hard---o32934.json | 13 | engine_unavailable@52 | 2.92 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 18, "openobj_deps": 2, "openobj_gate": 2} |
| JsonSchemaStore/JsonSchemaStore---bigquery-table.json | 5 | engine_unavailable@1109 | 5.5 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 3} |
| JsonSchemaStore/JsonSchemaStore---bigquery-table.json | 12 | engine_unavailable@2125 | 8.09 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 3} |
| JsonSchemaStore/JsonSchemaStore---cryproj.53.schema.json | 18 | engine_unavailable@662 | 1.01 | yes | mask computation limit exceeded {"fail_search": 1} |
| JsonSchemaStore/JsonSchemaStore---tye-schema.json | 4 | engine_unavailable@4926 | 2.22 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 7} |
| JsonSchemaStore/JsonSchemaStore---tye-schema.json | 8 | engine_unavailable@107 | 0.74 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 8} |
| JsonSchemaStore/JsonSchemaStore---tye-schema.json | 9 | engine_unavailable@581 | 1.28 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 8} |
| JsonSchemaStore/JsonSchemaStore---tye-schema.json | 13 | engine_unavailable@2082 | 6.04 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 8} |
| JsonSchemaStore/JsonSchemaStore---tye-schema.json | 16 | engine_unavailable@4433 | 1.77 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 8} |
| JsonSchemaStore/JsonSchemaStore---tye-schema.json | 19 | engine_unavailable@4866 | 3.9 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 8} |
| JsonSchemaStore/bigquery-table.json | 19 | engine_unavailable@1582 | 5.48 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 3} |
| JsonSchemaStore/cryproj.dev.schema.json | 10 | engine_unavailable@5725 | 3.18 | yes | mask computation limit exceeded {"fail_search": 1} |
| Github_hard/o31351.json | 8 | engine_unavailable@6225 | 2.4 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 3} |
| JsonSchemaStore/tye-schema.json | 3 | engine_unavailable@898 | 7.32 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 8} |
| JsonSchemaStore/tye-schema.json | 6 | engine_unavailable@567 | 3.69 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 8} |
| JsonSchemaStore/tye-schema.json | 7 | engine_unavailable@2644 | 2.78 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 5} |
| JsonSchemaStore/tye-schema.json | 11 | engine_unavailable@4658 | 2.84 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 8} |
| JsonSchemaStore/tye-schema.json | 15 | engine_unavailable@3015 | 4.7 | yes | mask computation limit exceeded {"fail_search": 1, "comb_group": 3} |

# R11-C2: the same passes on 50cbd80e (host CPU, before stages C and D)

| file | pass | end | seconds | the candidate's state |
|---|---|---|---|---|---|
| Github_hard/Github_hard---o32934.json | 8 | engine_unavailable@38 | 2.67 | yes |
| Github_hard/Github_hard---o32934.json | 13 | engine_unavailable@52 | 2.68 | yes |
| JsonSchemaStore/JsonSchemaStore---bigquery-table.json | 5 | engine_unavailable@1109 | 5.89 | yes |
| JsonSchemaStore/JsonSchemaStore---bigquery-table.json | 12 | engine_unavailable@2125 | 8.76 | yes |
| JsonSchemaStore/JsonSchemaStore---cryproj.53.schema.json | 18 | engine_unavailable@662 | 1.05 | yes |
| JsonSchemaStore/JsonSchemaStore---tye-schema.json | 4 | engine_unavailable@4926 | 2.23 | yes |
| JsonSchemaStore/JsonSchemaStore---tye-schema.json | 8 | engine_unavailable@107 | 0.65 | yes |
| JsonSchemaStore/JsonSchemaStore---tye-schema.json | 9 | engine_unavailable@581 | 1.19 | yes |
| JsonSchemaStore/JsonSchemaStore---tye-schema.json | 13 | engine_unavailable@2082 | 5.94 | yes |
| JsonSchemaStore/JsonSchemaStore---tye-schema.json | 16 | engine_unavailable@4433 | 1.85 | yes |
| JsonSchemaStore/JsonSchemaStore---tye-schema.json | 19 | engine_unavailable@4866 | 3.74 | yes |
| JsonSchemaStore/bigquery-table.json | 19 | engine_unavailable@1582 | 5.92 | yes |
| JsonSchemaStore/cryproj.dev.schema.json | 10 | engine_unavailable@5725 | 3.58 | yes |
| Github_hard/o31351.json | 8 | engine_unavailable@6225 | 2.58 | yes |
| JsonSchemaStore/tye-schema.json | 3 | engine_unavailable@898 | 7.15 | yes |
| JsonSchemaStore/tye-schema.json | 6 | engine_unavailable@567 | 3.54 | yes |
| JsonSchemaStore/tye-schema.json | 7 | engine_unavailable@2644 | 2.88 | yes |
| JsonSchemaStore/tye-schema.json | 11 | engine_unavailable@4658 | 3.06 | yes |
| JsonSchemaStore/tye-schema.json | 15 | engine_unavailable@3015 | 4.89 | yes |

# R11-B2: MaskBench bigquery-table on the lines and 50cbd80e

| library | budget | pass | end | seconds | the candidate's state |
|---|---|---|---|---|---|
| 50cbd80e | 10.0 | 5 | engine_unavailable@1109 | 5.75 | yes |
| 50cbd80e | 10.0 | 12 | engine_unavailable@2125 | 8.49 | yes |
| 7345490f | 300.0 | 5 | engine_unavailable@1109 | 54.84 | yes |
| 7345490f | 300.0 | 12 | engine_unavailable@2125 | 155.43 | yes |
| dbc5eeb | 300.0 | 5 | engine_unavailable@1109 | 54.67 | yes |
| dbc5eeb | 300.0 | 12 | engine_unavailable@2125 | 154.97 | yes |
