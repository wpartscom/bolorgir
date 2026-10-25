# Control repeats: run outcome vs repeat

repeats aggregated: 37

The repeat is a different condition from T8a-w12: one thread, the same per-file limits, but a fixed number of concurrent repeats instead of 12 workers. A successful repeat explains a historical timeout; it never replaces the recorded result.

## crash (1)

| file | run | repeat | verdict | repeat s | steps | mask s | ms/step | >900 ms | share slow | max ms | peak MB |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| JsonSchemaStore---ui5.yaml.json | crash_or_timeout | pending_marker | crash reproduced (no record written) | 4.018 | 0 | 0.0 | None | 0 |  | 0.0 | 633 |

verdicts: crash reproduced (no record written): 1

## resource_limit (10)

| file | run | repeat | verdict | repeat s | steps | mask s | ms/step | >900 ms | share slow | max ms | peak MB |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Github_easy---o13129.json | mask_computation_limit | mask_computation_limit | mask limit reproduced | 254.811 | 0 | 0.0 | None | 0 |  | 0.0 | 693 |
| Github_easy---o58839.json | resource_limit_node_budget | resource_limit_node_budget | node budget reproduced | 4.523 | 0 | 0.0 | None | 0 |  | 0 | 633 |
| Github_hard---o12278.json | mask_computation_limit | mask_computation_limit | mask limit reproduced | 506.996 | 240 | 64.7 | 269.7 | 63 | 90.3% | 1026.4 | 718 |
| Github_hard---o74466.json | mask_computation_limit | mask_computation_limit | mask limit reproduced | 465.208 | 681 | 219.5 | 322.31 | 118 | 93.8% | 4866.1 | 752 |
| Github_hard---o90895.json | resource_limit_node_budget | resource_limit_node_budget | node budget reproduced | 4.524 | 0 | 0.0 | None | 0 |  | 0 | 684 |
| Github_medium---o54553.json | mask_computation_limit | mask_computation_limit | mask limit reproduced | 347.191 | 216 | 250.1 | 1157.75 | 78 | 99.9% | 6232.8 | 728 |
| Github_medium---o90904.json | mask_computation_limit | mask_computation_limit | mask limit reproduced | 165.848 | 317 | 158.3 | 499.37 | 97 | 100.0% | 4790.5 | 669 |
| Github_ultra---o48661.json | resource_limit_node_budget | resource_limit_node_budget | node budget reproduced | 4.525 | 0 | 0.0 | None | 0 |  | 0 | 631 |
| JsonSchemaStore---codeship-services.json | mask_computation_limit | mask_computation_limit | mask limit reproduced | 283.854 | 61 | 78.6 | 1287.79 | 33 | 100.0% | 3976.3 | 703 |
| JsonSchemaStore---renovate-schema.json | resource_limit_node_budget | resource_limit_node_budget | node budget reproduced | 4.523 | 0 | 0.0 | None | 0 |  | 0 | 666 |

verdicts: mask limit reproduced: 6; node budget reproduced: 4

## single (6)

| file | run | repeat | verdict | repeat s | steps | mask s | ms/step | >900 ms | share slow | max ms | peak MB |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Github_easy---o13129.json | mask_computation_limit | mask_computation_limit | mask limit reproduced | 240.685 | 0 | 0.0 | None | 0 |  | 0.0 | 701 |
| Github_easy---o90249.json | ok | ok | stable at lower contention | 215.069 | 22 | 210.7 | 9576.86 | 7 | 100.0% | 52482.7 | 633 |
| Github_hard---o10421.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 903.361 | 247 | 898.8 | 3638.83 | 237 | 100.0% | 4565.4 | 705 |
| Github_medium---o10530.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 904.014 | 807 | 895.9 | 1110.1 | 470 | 91.6% | 4745.3 | 701 |
| JsonSchemaStore---codeship-services.json | mask_computation_limit | mask_computation_limit | mask limit reproduced | 266.34 | 61 | 72.5 | 1189.08 | 28 | 94.0% | 3868.1 | 703 |
| JsonSchemaStore---ui5.yaml.json | crash_or_timeout | pending_marker | crash reproduced (no record written) | 4.021 | 0 | 0.0 | None | 0 |  | 0.0 | 628 |

verdicts: crash reproduced (no record written): 1; mask limit reproduced: 2; stable at lower contention: 1; timeout reproduced at lower contention: 2

## timeouts (20)

| file | run | repeat | verdict | repeat s | steps | mask s | ms/step | >900 ms | share slow | max ms | peak MB |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Github_hard---o10421.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 905.748 | 252 | 897.5 | 3561.38 | 242 | 100.0% | 4623.3 | 696 |
| Github_hard---o21292.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 905.751 | 1997 | 899.2 | 450.27 | 788 | 82.9% | 1140.4 | 731 |
| Github_hard---o34343.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 905.671 | 1064 | 899.7 | 845.56 | 919 | 100.0% | 1119.0 | 702 |
| Github_hard---o46173.json | crash_or_timeout | ok | completed at lower contention | 779.22 | 1924 | 772.8 | 401.64 | 670 | 81.0% | 1125.3 | 691 |
| Github_hard---o58764.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 904.247 | 1043 | 899.9 | 862.76 | 889 | 100.0% | 1173.0 | 690 |
| Github_hard---o64963.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 904.814 | 442 | 889.4 | 2012.12 | 262 | 100.0% | 16681.2 | 694 |
| Github_hard---o73037.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 905.217 | 1057 | 899.0 | 850.56 | 917 | 100.0% | 1147.8 | 701 |
| Github_hard---o84134.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 905.282 | 868 | 899.7 | 1036.5 | 837 | 100.0% | 1247.1 | 687 |
| Github_medium---o10530.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 906.288 | 768 | 899.2 | 1170.87 | 519 | 99.1% | 5479.5 | 699 |
| Github_medium---o25768.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 906.283 | 268 | 896.6 | 3345.65 | 175 | 99.7% | 8510.0 | 702 |
| Github_medium---o46145.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 906.313 | 39 | 898.6 | 23041.1 | 26 | 100.0% | 38130.5 | 647 |
| Github_medium---o65503.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 906.331 | 996 | 899.5 | 903.12 | 885 | 99.1% | 1398.8 | 687 |
| Github_medium---o82710.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 905.391 | 1065 | 899.4 | 844.46 | 843 | 88.6% | 1127.5 | 699 |
| JsonSchemaStore---adonisrc.schema.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 905.365 | 718 | 899.4 | 1252.61 | 637 | 99.7% | 5272.3 | 709 |
| JsonSchemaStore---debugsettings.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 905.313 | 208 | 896.3 | 4309.08 | 197 | 100.0% | 6808.6 | 689 |
| JsonSchemaStore---minecraft-biome.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 905.321 | 802 | 29.2 | 36.38 | 31 | 99.7% | 982.8 | 712 |
| JsonSchemaStore---schema-app.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 905.499 | 142 | 764.8 | 5385.88 | 84 | 100.0% | 12918.1 | 1543 |
| Kubernetes---kb_134_Normalized.json | crash_or_timeout | ok | completed at lower contention | 621.479 | 942 | 615.9 | 653.78 | 651 | 100.0% | 1079.4 | 688 |
| Kubernetes---kb_630_Normalized.json | crash_or_timeout | timeout | timeout reproduced at lower contention | 905.423 | 958 | 899.2 | 938.65 | 934 | 99.9% | 1114.9 | 693 |
| Snowplow---sp_10_Normalized.json | crash_or_timeout | ok | completed at lower contention | 705.447 | 2307 | 699.7 | 303.31 | 716 | 95.2% | 1059.7 | 691 |

verdicts: completed at lower contention: 3; timeout reproduced at lower contention: 17

