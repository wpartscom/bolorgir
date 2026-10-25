# ADR-0007 Decision 4 acceptance matrix

Scale: `review-saved-data`; library: `zig-out build`; generated 2026-10-24T16:11:27Z UTC

| job | status | tests | parametrised | s | requirement |
|---|---|---:|---:|---:|---|
| `warm-acceptance` | fail | 0 | 0 | 0.1 | saved-data warm failure replay; no new timing |

Selection: `--only` ran 1 of the matrix jobs; the Overall line below covers only these rows, not the full Decision 4 set.

Overall: **FAIL** (0/1 selected jobs pass)

## JSON verdicts

- `warm-acceptance`: **FAIL** (ok run_id = 'warm-acceptance-26147-1792858287334839383' (expected 'warm-acceptance-26147-1792858287334839383'); FAIL warm_acceptance.pass = False)

## Warm guard

Threshold 1300 ns; on-leg means (ns): 1265, 1279, 1173, 1305, 1131, 1327, 1309, 1302, 1189, 1314; on-leg p50 (ns): 1221, 1240, 1135, 1266, 1096, 1280, 1276, 1283, 1138, 1259; pass: **False**

Problems:

- conditions: saved-data replay: measurement conditions were not recorded (no new measurement)
- permissive_string_content round 4 on: mean 1305.375 ns > 1300
- permissive_string_in_object round 1 on: mean 1327.0175 ns > 1300
- permissive_string_in_object round 2 on: mean 1309.821 ns > 1300
- permissive_string_in_object round 3 on: mean 1302.3275 ns > 1300
- permissive_string_in_object round 5 on: mean 1314.538 ns > 1300

Fuzz artifacts produced by this run: none
