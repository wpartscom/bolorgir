# ADR-0007 Decision 4 acceptance matrix

Scale: `quick`; library: `/home/gm/Рабочий стол/сайты/LLM_trainer/zig/benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0`; generated 2026-10-24T16:29:19Z UTC

Fixed warm CPU placement: `7`

Warm condition limits: load1 <= 2.0, CPU pressure avg10 <= 0.5 %

| job | status | tests | parametrised | s | requirement |
|---|---|---:|---:|---:|---|
| `warm-acceptance` | pass | 0 | 0 | 84.8 | ADR-0007 warm guard: five repeats x 2000 hits per probe, alternating leg order, every warm_ext.on mean <= threshold, hash and hits mandatory, fixed CPU placement and recorded frequency/load conditions |

Selection: `--only` ran 1 of the matrix jobs; the Overall line below covers only these rows, not the full Decision 4 set.

Overall: **PASS** (1/1 selected jobs pass)

## JSON verdicts

- `warm-acceptance`: ok (ok run_id = 'warm-acceptance-37564-1792859274280580845' (expected 'warm-acceptance-37564-1792859274280580845'); ok status = 'OK' (expected 'OK'); ok warm_acceptance.pass = True; ok warm_acceptance.protocol.repeats = 5; ok warm_acceptance.protocol.observations = 2000; ok warm_acceptance.protocol.threshold_ns = 1300; ok warm_acceptance.protocol.cpu = 7; ok warm_acceptance.conditions.ok = True; ok warm_acceptance.conditions.complete = True; ok warm_acceptance.conditions.limits.max_load1 = 2.0; ok warm_acceptance.conditions.limits.max_cpu_pressure_avg10 = 0.5; ok environment.lib_sha256 = 'a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6' (expected 'a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6'))

## Warm guard

Threshold 1300 ns; on-leg means (ns): 1190, 1218, 1214, 1188, 1267, 1126, 1264, 1254, 1167, 1085; on-leg p50 (ns): 1155, 1187, 1179, 1159, 1236, 1103, 1231, 1220, 1137, 1052; pass: **True**


Fuzz artifacts produced by this run: none
