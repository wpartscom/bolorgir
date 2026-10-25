# Counter overhead: atoms on vs off

Per corpus file the on/off pair is repeated three times and the order
inside the pair alternates (on-off, off-on, on-off).

| file | variant | n | median ms/call | min | max | spread | wall median s |
|---|---|---:|---:|---:|---:|---:|---:|
| BFCL_java_0 | on | 3 | 5.755 | 5.736 | 6.025 | 5.0 % | 3.9 |
| BFCL_java_0 | off | 3 | 5.615 | 5.562 | 5.618 | 1.0 % | 3.91 |
| Github_medium---o30212 | on | 3 | 1785.9 | 1730.801 | 1796.094 | 3.7 % | 110.94 |
| Github_medium---o30212 | off | 3 | 1759.369 | 1729.309 | 1769.973 | 2.3 % | 109.38 |
| Github_trivial---o41679 | on | 3 | 2.874 | 2.869 | 2.878 | 0.3 % | 3.86 |
| Github_trivial---o41679 | off | 3 | 2.847 | 2.761 | 2.902 | 5.0 % | 3.89 |

| file | on / off | extra us per call |
|---|---:|---:|
| BFCL_java_0 | 1.025 | +140.0 |
| Github_medium---o30212 | 1.015 | +26531.0 |
| Github_trivial---o41679 | 1.009 | +27.0 |
