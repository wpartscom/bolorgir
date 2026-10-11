# Residual recheck evidence (2026-10-02)

Saved output of `benchmarks/recheck_cert_residuals.py` (`--suite cases26`
and `--suite valid-rejects`) on the final build of the audit fixes
(working tree on `dbc5eeb`): lib sha256 `7345490f7ddf5710...`, combined
src/include/build.zig sha256 `01c7ea62f0721c1b...`
(`{ find src include -type f; echo build.zig; } | LC_ALL=C sort | xargs
sha256sum | sha256sum`). Instances are serialized by
`python/bolorgir/serializer.py` with the key-order fix of 2026-10-03. The
script diffs every run against these files by default. The timings were
taken while a coverage run shared the machine.

The earlier candidates (lib `9c8cb7d98e632abb...`, src
`059490107bbb4234...`; lib `23fc8b081e353f8f...`, src
`3ae4aa9f191879b9...`) gave the same outcome and detail on every row.
The final build adds the fixes for the stepping of spawned parser
threads, for the emptiness of closed objects with `minProperties` and for
the thread cap in the residual search. The serializer fix changed one
row: the valid `Github_medium---o55595.json` instance, rejected at byte
514 before, is accepted (the serializer used to emit the `oneOf` branch
keys ahead of the outer `enabled`, against the outer declared order).

| File | Suite | Result |
|---|---|---|
| `recheck_out.log` | cases26 | 15 accept / 10 RESOURCE_LIMIT / 1 reject |
| `recheck_valid_rejects_out.jsonl` | valid-rejects | 87 accept / 39 RESOURCE_LIMIT of 126 instances |

Compared with the fix3 evidence (`../20260929T_fix3_adr0007/`: cases26
13 / 12 / 1, valid-rejects 75 / 50 / 1):

- three instances with a bounded integer inside an array or an object
  moved from RESOURCE_LIMIT to accept: `Github_hard---o77403.json` #0
  and `Github_medium---o57617.json` #0 and #1 (ADR-0011,
  `tests/test_spec_v1_p4_numbers_reachability.py::test_num_conj_inside_containers`);
- with the value verdicts of semantics-spec-v1 4.3 (a `oneOf` branch
  votes on the value, whatever order its declared keys arrive in) ten
  more valid-rejects instances moved from RESOURCE_LIMIT to accept, two
  each of `Github_medium---o35868.json`, `Github_medium---o70035.json`,
  `Github_trivial---o25751.json`, `Github_ultra---o17072.json` and
  `JsonSchemaStore---livelyPropertiesSchema.json` (o17072 and o35868
  are also cases26 cases), and two moved
  from accept to RESOURCE_LIMIT: `Github_hard---o84330.json` #0 and #1,
  a `oneOf` of some twenty meter types whose branches now stay alive
  until their values decide; `Github_ultra---o360.json` #1 still ends in
  RESOURCE_LIMIT, now at byte 324 instead of 129.

The cases26 reject is the invalid `Github_medium---o55595.json` test 1,
rejected by the engine as it should be.

Rerun:

```sh
zig build --release
PYTHONPATH=python python3 benchmarks/recheck_cert_residuals.py --suite both
```
