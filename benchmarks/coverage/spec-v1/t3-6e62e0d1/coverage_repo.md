# Coverage report - JSONSchemaBench repository snapshot (data/)

- corpus: https://github.com/guidance-ai/jsonschemabench revision `ba103c73756198dd9b149ddc7db7867da7a077f6` (`data/`), sha256 `4c6c1d3947936f6060d4a0c0d937b1f54c41b2af2f44c09f664ab1819e97112e`
- engine commit: `t3-6e62e0d1` (bolorgir 0.2.0rc1, ABI 1), profile `spec-v1`
- limits: mode=adaptive, memory_limit_bytes=268435456, cache_limit_bytes=67108864, session_limit_bytes=8388608, schema_limit_bytes=1048576, max_depth=64, max_threads_per_state=64, work_limit_ops=0
- tokenizer: openai-community/gpt2 @ 607a30d783dfa663caf39e06633721c8d4cfcd7e (50257 tokens)
- date: 2026-10-09T22:54:33Z
- library sha256: `6e62e0d1526c6e5369457d2f7205675b7cb163e3374b96d8cb5115c28873e914`

> `generatable` is the result of K generation passes on the listed tokenizers, not a proof for every continuation

K = 20; tokenizers: byte, llama-3.1, gpt2.

First blocker per file; a refusal is never coverage.

## Outcome buckets

| bucket | files |
|---|---|
| compiled | 9264 |
| invalid_schema | 6 |
| unsupported_feature | 151 |
| resource_limit | 130 |
| unsatisfiable_constraint | 7 |
| total | 9558 |

## Invalid-schema subclasses

| subclass | files |
|---|---|
| other | 6 |

## Unsupported-feature keyword histogram (first blocker)

| keyword | files |
|---|---|
| (other) | 143 |
| $ref | 8 |

## Per-dataset counts

| dataset | total | compiled | invalid_schema | unsupported_feature | resource_limit | valid ex. | invalid ex. |
|---|---|---|---|---|---|---|---|
| Github_easy | 1943 | 1934 | 0 | 8 | 1 | 0 | 0 |
| Github_hard | 1240 | 1164 | 4 | 31 | 35 | 0 | 0 |
| Github_medium | 1976 | 1946 | 0 | 18 | 12 | 0 | 0 |
| Github_trivial | 444 | 439 | 0 | 3 | 1 | 0 | 0 |
| Github_ultra | 164 | 122 | 0 | 17 | 25 | 0 | 0 |
| Glaiveai2K | 1707 | 1707 | 0 | 0 | 0 | 0 | 0 |
| JsonSchemaStore | 492 | 413 | 2 | 40 | 37 | 0 | 0 |
| Kubernetes | 1064 | 1045 | 0 | 0 | 19 | 0 | 0 |
| Snowplow | 403 | 400 | 0 | 3 | 0 | 0 | 0 |
| WashingtonPost | 125 | 94 | 0 | 31 | 0 | 0 | 0 |

Examples: 0 valid, 0 invalid.

## Semantic coverage (split metrics)

Each metric has its own base; compile coverage alone is not exact
coverage. Semantic checks run on a byte-level synthetic tokenizer
(token id = byte value, eos=256) with the `spec-v1` profile;
instances are encoded with python/bolorgir/serializer.py serialize_value and cross-checked
with jsonschema==4.26.0; generation budgets: 8192 steps and 10s wall time per schema. The byte context is recycled every 150 checks (plus a retry on RESOURCE_LIMIT): 65 recycles this run - engine retains generation memory in a Context across session/grammar free (R5 finding). Context destroy() failures: 0.

| metric | n | base | % |
|---|---|---|---|
| compile coverage | 9264 | 9558 | 96.92% |
| explicit refusals (engine declines the schema) | 294 | 9558 | 3.08% |
| session success | 9263 | 9264 | 99.99% |
| generation success | 8999 | 9264 | 97.14% |
| generation success (of attempted: compiled minus oversize skips) | 8999 | 9262 | 97.16% |
| generated-doc schema validity (jsonschema) | 8953 | 8999 | 99.49% |
| valid-instance acceptance | 0 | 0 | n/a |
| invalid-instance rejection | 0 | 0 | n/a |
| serialization incompatibility | 0 | 0 | n/a |

Detail counters:

| counter | n |
|---|---|
| compile mismatch (byte-context compile differs) | 0 |
| session create failed | 1 |
| generation: dead end | 0 |
| generation: step/time budget exhausted | 199 |
| generation: oversize skip (min document size estimate > step budget; not attempted) | 1 |
| generation: engine error | 64 |
| generated doc not JSON (finish accepted it) | 0 |
| generated doc rejected by jsonschema | 43 |
| generated doc validator error (no verdict) | 3 |
| schema unusable for jsonschema (validator_error) | 3 |
| valid instances: rejected | 0 |
| valid instances: engine error | 0 |
| valid instances: corpus label vs jsonschema disagree | 0 |
| invalid instances: accepted (jsonschema agrees invalid) | 0 |
| invalid instances: accepted (jsonschema says valid) | 0 |
| invalid instances: engine error | 0 |

## Semantic failure kinds (schemas that compile but fail a check)

| failure kind | count |
|---|---|
| generation_budget | 199 |
| generation_error | 64 |
| generated_schema_invalid | 43 |
| session_create_failed | 1 |

### generation_error (64)

| dataset | file | detail |
|---|---|---|
| Github_easy | o13129.json | error:RESOURCE_LIMIT: fill_mask at step 0 |
| Github_easy | o44463.json | error:RESOURCE_LIMIT: fill_mask at step 0 |
| Github_easy | o6187.json | error:RESOURCE_LIMIT: fill_mask at step 2 |
| Github_easy | o68297.json | error:RESOURCE_LIMIT: fill_mask at step 34 |
| Github_easy | o81530.json | error:RESOURCE_LIMIT: fill_mask at step 0 |
| Github_easy | o89710.json | error:RESOURCE_LIMIT: fill_mask at step 0 |
| Github_easy | o90313.json | error:RESOURCE_LIMIT: fill_mask at step 25 |
| Github_easy | o90314.json | error:RESOURCE_LIMIT: fill_mask at step 0 |
| Github_hard | o12454.json | error:RESOURCE_LIMIT: fill_mask at step 0 |
| Github_hard | o21072.json | error:RESOURCE_LIMIT: fill_mask at step 26 |
| Github_hard | o21074.json | error:RESOURCE_LIMIT: fill_mask at step 562 |
| Github_hard | o27348.json | error:RESOURCE_LIMIT: fill_mask at step 547 |
| Github_hard | o4237.json | error:RESOURCE_LIMIT: fill_mask at step 0 |
| Github_hard | o4413.json | error:RESOURCE_LIMIT: fill_mask at step 12 |
| Github_hard | o55657.json | error:RESOURCE_LIMIT: fill_mask at step 2 |

### generation_budget (199)

| dataset | file | detail |
|---|---|---|
| Github_easy | o13652.json | step budget 8192 exhausted |
| Github_easy | o15726.json | step budget 8192 exhausted |
| Github_easy | o21040.json | step budget 8192 exhausted |
| Github_easy | o2231.json | time budget 10s exhausted at step 320 |
| Github_easy | o27844.json | step budget 8192 exhausted |
| Github_easy | o28609.json | step budget 8192 exhausted |
| Github_easy | o28614.json | step budget 8192 exhausted |
| Github_easy | o33825.json | step budget 8192 exhausted |
| Github_easy | o40228.json | step budget 8192 exhausted |
| Github_easy | o40230.json | step budget 8192 exhausted |
| Github_easy | o41033.json | step budget 8192 exhausted |
| Github_easy | o41049.json | step budget 8192 exhausted |
| Github_easy | o4354.json | step budget 8192 exhausted |
| Github_easy | o44188.json | step budget 8192 exhausted |
| Github_easy | o47923.json | step budget 8192 exhausted |

### generated_schema_invalid (43)

| dataset | file | detail |
|---|---|---|
| Github_easy | o17940.json | jsonschema rejects the generated document: {"arch":"","clientId":"00000000-0000-0000-0000-000000000000","device":"","locale":"","os":"","osversion":"","seq":-0,"v" |
| Github_easy | o36461.json | jsonschema rejects the generated document: {"value":0.0000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000 |
| Github_easy | o36463.json | jsonschema rejects the generated document: {"value":0.0000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000 |
| Github_easy | o44051.json | jsonschema rejects the generated document: {"action":"ingest","location":"https://","id":"","version":0.00000000000000000000000000000000000000000000000000000000000 |
| Github_easy | o57656.json | jsonschema rejects the generated document: {"NumberOfImages":0.0000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000 |
| Github_easy | o83684.json | jsonschema rejects the generated document: {"functionCode":"","usageUnits":-0,"division":0.000000000000000000000000000000000000000000000000000000000000000015000000 |
| Github_easy | o83685.json | jsonschema rejects the generated document: {"functionCode":"","usageUnits":-0,"division":0.000000000000000000000000000000000000000000000000000000000000000010000000 |
| Github_easy | o83687.json | jsonschema rejects the generated document: {"name":"","sex":"M","age":0.0000000000000000000000000000000000000000000000000000000000000000120000000000000000000000000 |
| Github_hard | o41192.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","skip-bids-validation":false,"task-id":"","echo-idx":"","n_cpus":-0,"mem_mb":-0,"anat |
| Github_hard | o41250.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{},"freesurfer_license":{}},"config":{"inputs":"","config":"","api_key":"", |
| Github_hard | o41251.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","skip-bids-validation":false,"task-id":"","echo-idx":"","anat-only":false,"error-on-a |
| Github_hard | o41252.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{}},"config":{"inputs":"","config":"","api_key":"","save_outputs":false,"fo |
| Github_hard | o41256.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{}},"config":{"inputs":"","config":"","api_key":"","ignore_aroma_denoising_ |
| Github_hard | o41261.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{}},"config":{"inputs":"","config":"","api_key":"","save_outputs":false,"fo |
| Github_hard | o41263.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{},"freesurfer_license":{}},"config":{"inputs":"","config":"","api_key":"", |

### session_create_failed (1)

| dataset | file | detail |
|---|---|---|
| JsonSchemaStore | ui5.yaml.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |

## Oversize generation skips (1)

Static minimum-document-size estimate above the step budget;
generation not attempted (a documented skip, not a failure).

| dataset | file | detail |
|---|---|---|
| Github_easy | o12622.json | estimated minimum document size 17440 bytes > step budget 8192; generation not attempted |

## Generatable (K passes per schema and tokenizer)

K = 20. Pass 1 runs the greedy `generate_document` policy over single-byte tokens; passes 2..K run the biased random policy of `tests/generation_passes.py` (seed = sha256 of the base seed 20261002, file, tokenizer and pass index). Budgets per pass: 8192 steps and 10 s. Every pass of every schema is attempted. Every document is validated by jsonschema==4.26.0 over exact numbers (tests/exact_numbers.py), `pattern` by regress==2025.10.1 (ECMA-262, unicode mode). Aggregation per schema: the first matching rule of `tests/semantic_outcomes.py` `aggregate_schema_outcome`; `skipped` is outside the percentage base. Every pass record is in `coverage_repo.passes.json.gz`.

| tokenizer | name | revision | vocab | single-byte tokens | schema sample |
|---|---|---|---|---|---|
| byte | byte-level synthetic (token id = byte value, eos=256, pad=257) | - | 258 | 256 | all |
| llama-3.1 | unsloth/Meta-Llama-3.1-8B-Instruct | a2856192dd7c25b842431f39c179a6c2c2f627d1 | 128256 | 256 | 1 in 20 |
| gpt2 | openai-community/gpt2 | 607a30d783dfa663caf39e06633721c8d4cfcd7e | 50257 | 256 | 1 in 20 |

A sampled tokenizer runs on the compiled schemas whose sha256(dataset/file) is 0 modulo the sample size; the others are `skipped` with code `not_sampled`.

| schema outcome | byte | llama-3.1 | gpt2 |
|---|---|---|---|
| generatable | 8313 (89.77%) | 246 (56.94%) | 319 (73.84%) |
| invalid_document | 744 (8.03%) | 6 (1.39%) | 5 (1.16%) |
| engine_defect | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| validator_error | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| engine_unavailable | 132 (1.43%) | 1 (0.23%) | 3 (0.69%) |
| incomplete | 71 (0.77%) | 179 (41.44%) | 105 (24.31%) |
| skipped | 4 | 8832 | 8832 |
| schemas with an outcome / compiled | 9264 / 9264 | 9264 / 9264 | 9264 / 9264 |
| percentage base (compiled minus skipped) | 9260 | 432 | 432 |
| incomplete passes inside generatable schemas | 7469 | 2133 | 2669 |
| unsuccessful results retried on a fresh context | 132 | 1 | 3 |
| results with passes skipped by the schema budget | 0 | 0 | 0 |
| invalid_document / validator_error passes with their document | 5661 / 5661 | 24 / 24 | 35 / 35 |

Pass outcomes:

| pass outcome | byte | llama-3.1 | gpt2 |
|---|---|---|---|
| completed_valid | 167849 | 2859 | 3776 |
| engine_unavailable | 1587 | 9 | 37 |
| incomplete:step_limit | 9182 | 1 | 1 |
| incomplete:time_limit | 921 | 5747 | 4791 |
| invalid_document | 5661 | 24 | 35 |

### byte: schemas that are not generatable, by outcome / stage / code

| outcome / stage / code | schemas |
|---|---|
| invalid_document / validate / REJECTED | 744 |
| engine_unavailable / fill_mask / BLG_ERR_RESOURCE_LIMIT | 131 |
| incomplete / fill_mask / STEP_LIMIT | 67 |
| incomplete / fill_mask / TIME_LIMIT | 4 |
| skipped / None / validator_unusable | 3 |
| engine_unavailable / session_create / BLG_ERR_RESOURCE_LIMIT | 1 |
| skipped / None / oversize | 1 |

#### byte: invalid_document (744, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | o10295.json | validate | REJECTED | 139 | 139 | 45091615433753 |
| Github_easy | o10298.json | validate | REJECTED | 122 | 122 | 150671217023881 |
| Github_easy | o11171.json | validate | REJECTED | 2160 | 2160 | 32347229860345 |
| Github_easy | o11173.json | validate | REJECTED | 724 | 724 | 89495875348764 |
| Github_easy | o11787.json | validate | REJECTED | 1778 | 1778 | 140323477944184 |
| Github_easy | o11794.json | validate | REJECTED | 4411 | 4411 | 252184730714197 |
| Github_easy | o11796.json | validate | REJECTED | 914 | 914 | 65401027560299 |
| Github_easy | o11798.json | validate | REJECTED | 2381 | 2381 | 58298215610278 |
| Github_easy | o12214.json | validate | REJECTED | 29 | 29 | 38038505282179 |
| Github_easy | o12215.json | validate | REJECTED | 87 | 87 | 231197905972954 |
| Github_easy | o12218.json | validate | REJECTED | 55 | 55 | 1279753347323 |
| Github_easy | o12229.json | validate | REJECTED | 665 | 665 | 206080511952768 |
| Github_easy | o12235.json | validate | REJECTED | 191 | 191 | 187086521492550 |
| Github_easy | o12254.json | validate | REJECTED | 1574 | 1574 | 73118666461981 |
| Github_easy | o1316.json | validate | REJECTED | 2438 | 2438 | 254564368036305 |

#### byte: skipped (4)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | o12622.json | None | oversize | None | None | None |
| Github_easy | o66201.json | None | validator_unusable | None | None | None |
| Github_medium | o82255.json | None | validator_unusable | None | None | None |
| Github_ultra | o15286.json | None | validator_unusable | None | None | None |

#### byte: engine_unavailable (132, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | o13129.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 0 | 0 | 170843361848204 |
| Github_easy | o37786.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 29 | 29 | 83726350626230 |
| Github_easy | o37787.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 9 | 9 | 168439545220510 |
| Github_easy | o37788.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 9 | 9 | 189787297863731 |
| Github_easy | o44463.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 0 | 0 | 53786771168157 |
| Github_easy | o4834.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 163 | 163 | 70245723132746 |
| Github_easy | o6187.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 2 | 2 | 155728708879068 |
| Github_easy | o68296.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 40 | 40 | 208516781515775 |
| Github_easy | o68297.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 34 | 34 | 45419672790479 |
| Github_easy | o81530.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 0 | 0 | 16847786382584 |
| Github_easy | o89710.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 0 | 0 | 63764522774438 |
| Github_easy | o90119.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 32 | 32 | 82108814055465 |
| Github_easy | o90216.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 28 | 28 | 211873085017733 |
| Github_easy | o90313.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 25 | 25 | 274358840484334 |
| Github_easy | o90314.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 0 | 0 | 202181488977012 |

#### byte: incomplete (71, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | o40228.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 49591121911542 |
| Github_easy | o40230.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 268514888274268 |
| Github_easy | o41033.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 163752859520778 |
| Github_hard | o13152.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 166989771426732 |
| Github_hard | o16058.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 55507655996951 |
| Github_hard | o16059.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 191120550729424 |
| Github_hard | o16060.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 246819743838162 |
| Github_hard | o16064.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 162501828892496 |
| Github_hard | o31337.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 66685453539506 |
| Github_hard | o34341.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 198362872818401 |
| Github_hard | o34342.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 9196068219455 |
| Github_hard | o34343.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 243683516226056 |
| Github_hard | o37717.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 112951401729417 |
| Github_hard | o40454.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 129252241647484 |
| Github_hard | o41273.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 81481271036098 |

### llama-3.1: schemas that are not generatable, by outcome / stage / code

| outcome / stage / code | schemas |
|---|---|
| skipped / None / not_sampled | 8831 |
| incomplete / fill_mask / TIME_LIMIT | 179 |
| invalid_document / validate / REJECTED | 6 |
| engine_unavailable / fill_mask / BLG_ERR_RESOURCE_LIMIT | 1 |
| skipped / None / validator_unusable | 1 |

#### llama-3.1: skipped (8832, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | o10008.json | None | not_sampled | None | None | None |
| Github_easy | o10009.json | None | not_sampled | None | None | None |
| Github_easy | o10011.json | None | not_sampled | None | None | None |
| Github_easy | o10012.json | None | not_sampled | None | None | None |
| Github_easy | o10013.json | None | not_sampled | None | None | None |
| Github_easy | o10014.json | None | not_sampled | None | None | None |
| Github_easy | o10016.json | None | not_sampled | None | None | None |
| Github_easy | o10030.json | None | not_sampled | None | None | None |
| Github_easy | o10059.json | None | not_sampled | None | None | None |
| Github_easy | o10065.json | None | not_sampled | None | None | None |
| Github_easy | o10077.json | None | not_sampled | None | None | None |
| Github_easy | o1008.json | None | not_sampled | None | None | None |
| Github_easy | o10086.json | None | not_sampled | None | None | None |
| Github_easy | o10088.json | None | not_sampled | None | None | None |
| Github_easy | o10091.json | None | not_sampled | None | None | None |

#### llama-3.1: incomplete (179, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | o1190.json | fill_mask | TIME_LIMIT | 16 | 16 | 230508166565878 |
| Github_easy | o14743.json | fill_mask | TIME_LIMIT | 16 | 16 | 133389109370474 |
| Github_easy | o16739.json | fill_mask | TIME_LIMIT | 16 | 16 | 218077443574175 |
| Github_easy | o17635.json | fill_mask | TIME_LIMIT | 32 | 32 | 3642724840449 |
| Github_easy | o21082.json | fill_mask | TIME_LIMIT | 16 | 16 | 130709022900867 |
| Github_easy | o21149.json | fill_mask | TIME_LIMIT | 48 | 48 | 224199008875043 |
| Github_easy | o21494.json | fill_mask | TIME_LIMIT | 16 | 16 | 1625357348631 |
| Github_easy | o25953.json | fill_mask | TIME_LIMIT | 16 | 16 | 260930228120305 |
| Github_easy | o27360.json | fill_mask | TIME_LIMIT | 16 | 16 | 56021707774389 |
| Github_easy | o29394.json | fill_mask | TIME_LIMIT | 16 | 16 | 176779163358233 |
| Github_easy | o33695.json | fill_mask | TIME_LIMIT | 32 | 32 | 31929118979552 |
| Github_easy | o36222.json | fill_mask | TIME_LIMIT | 16 | 16 | 42930846064126 |
| Github_easy | o40228.json | fill_mask | TIME_LIMIT | 16 | 16 | 56111114562083 |
| Github_easy | o42984.json | fill_mask | TIME_LIMIT | 16 | 16 | 52670508274298 |
| Github_easy | o43083.json | fill_mask | TIME_LIMIT | 16 | 16 | 273123062535398 |

#### llama-3.1: invalid_document (6)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | o36463.json | validate | REJECTED | 209 | 209 | 258987550090302 |
| Github_hard | o37075.json | validate | REJECTED | 35 | 69 | 148007283413041 |
| Github_hard | o48422.json | validate | REJECTED | 186 | 378 | 235013924011835 |
| Github_hard | o56023.json | validate | REJECTED | 69 | 121 | 109658511271609 |
| Github_hard | o9836.json | validate | REJECTED | 28 | 66 | 130871568283566 |
| Github_medium | o81496.json | validate | REJECTED | 31 | 81 | 32844880247059 |

#### llama-3.1: engine_unavailable (1)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | o90287.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 46 | 46 | 82344287676168 |

### gpt2: schemas that are not generatable, by outcome / stage / code

| outcome / stage / code | schemas |
|---|---|
| skipped / None / not_sampled | 8831 |
| incomplete / fill_mask / TIME_LIMIT | 105 |
| invalid_document / validate / REJECTED | 5 |
| engine_unavailable / fill_mask / BLG_ERR_RESOURCE_LIMIT | 3 |
| skipped / None / validator_unusable | 1 |

#### gpt2: skipped (8832, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | o10008.json | None | not_sampled | None | None | None |
| Github_easy | o10009.json | None | not_sampled | None | None | None |
| Github_easy | o10011.json | None | not_sampled | None | None | None |
| Github_easy | o10012.json | None | not_sampled | None | None | None |
| Github_easy | o10013.json | None | not_sampled | None | None | None |
| Github_easy | o10014.json | None | not_sampled | None | None | None |
| Github_easy | o10016.json | None | not_sampled | None | None | None |
| Github_easy | o10030.json | None | not_sampled | None | None | None |
| Github_easy | o10059.json | None | not_sampled | None | None | None |
| Github_easy | o10065.json | None | not_sampled | None | None | None |
| Github_easy | o10077.json | None | not_sampled | None | None | None |
| Github_easy | o1008.json | None | not_sampled | None | None | None |
| Github_easy | o10086.json | None | not_sampled | None | None | None |
| Github_easy | o10088.json | None | not_sampled | None | None | None |
| Github_easy | o10091.json | None | not_sampled | None | None | None |

#### gpt2: incomplete (105, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | o14743.json | fill_mask | TIME_LIMIT | 48 | 48 | 209787487969576 |
| Github_easy | o16739.json | fill_mask | TIME_LIMIT | 48 | 48 | 95218242639734 |
| Github_easy | o17635.json | fill_mask | TIME_LIMIT | 64 | 64 | 99801331076846 |
| Github_easy | o21494.json | fill_mask | TIME_LIMIT | 48 | 48 | 271897331766059 |
| Github_easy | o25953.json | fill_mask | TIME_LIMIT | 48 | 48 | 253747430852821 |
| Github_easy | o29394.json | fill_mask | TIME_LIMIT | 48 | 48 | 178490434728679 |
| Github_easy | o36222.json | fill_mask | TIME_LIMIT | 48 | 48 | 196331337641623 |
| Github_easy | o40228.json | fill_mask | TIME_LIMIT | 32 | 32 | 209222684698721 |
| Github_easy | o42984.json | fill_mask | TIME_LIMIT | 32 | 32 | 149049863356530 |
| Github_easy | o47038.json | fill_mask | TIME_LIMIT | 32 | 32 | 27936194007233 |
| Github_easy | o57230.json | fill_mask | TIME_LIMIT | 48 | 48 | 184377727378154 |
| Github_easy | o64816.json | fill_mask | TIME_LIMIT | 48 | 48 | 56714705441444 |
| Github_easy | o66327.json | fill_mask | TIME_LIMIT | 48 | 48 | 181105495173481 |
| Github_easy | o83392.json | fill_mask | TIME_LIMIT | 48 | 48 | 225875872256135 |
| Github_easy | o88584.json | fill_mask | TIME_LIMIT | 2064 | 2064 | 276804902561785 |

#### gpt2: invalid_document (5)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | o36463.json | validate | REJECTED | 209 | 209 | 228273049677326 |
| Github_hard | o47116.json | validate | REJECTED | 434 | 1210 | 158487500351454 |
| Github_hard | o48422.json | validate | REJECTED | 183 | 358 | 152118093737595 |
| Github_hard | o56023.json | validate | REJECTED | 77 | 216 | 268776840367015 |
| Github_hard | o9836.json | validate | REJECTED | 30 | 84 | 164780274673311 |

#### gpt2: engine_unavailable (3)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | o90287.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 46 | 46 | 15086727940877 |
| Github_hard | o12454.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 0 | 0 | 148953998852298 |
| Github_hard | o21074.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 32 | 114 | 14654035213448 |

## Resource-limit files

| dataset | file | detail |
|---|---|---|
| Github_easy | o58839.json | grammar exceeds node budget of 100000 |
| Github_hard | o13029.json | grammar exceeds node budget of 100000 |
| Github_hard | o13457.json | grammar exceeds node budget of 100000 |
| Github_hard | o19336.json | grammar exceeds node budget of 100000 |
| Github_hard | o21172.json | grammar exceeds node budget of 100000 |
| Github_hard | o21192.json | grammar exceeds node budget of 100000 |
| Github_hard | o21303.json | grammar exceeds node budget of 100000 |
| Github_hard | o21327.json | grammar exceeds node budget of 100000 |
| Github_hard | o21379.json | grammar exceeds node budget of 100000 |
| Github_hard | o27039.json | grammar exceeds node budget of 100000 |
| Github_hard | o35186.json | grammar exceeds node budget of 100000 |
| Github_hard | o36010.json | grammar exceeds node budget of 100000 |
| Github_hard | o36016.json | grammar exceeds node budget of 100000 |
| Github_hard | o37789.json | grammar exceeds node budget of 100000 |
| Github_hard | o39113.json | grammar exceeds node budget of 100000 |
| Github_hard | o39210.json | grammar exceeds node budget of 100000 |
| Github_hard | o44213.json | grammar exceeds node budget of 100000 |
| Github_hard | o53084.json | grammar exceeds node budget of 100000 |
| Github_hard | o62820.json | grammar exceeds node budget of 100000 |
| Github_hard | o67291.json | grammar exceeds node budget of 100000 |
| Github_hard | o69207.json | grammar exceeds node budget of 100000 |
| Github_hard | o69208.json | grammar exceeds node budget of 100000 |
| Github_hard | o69210.json | grammar exceeds node budget of 100000 |
| Github_hard | o69211.json | grammar exceeds node budget of 100000 |
| Github_hard | o69212.json | grammar exceeds node budget of 100000 |
| Github_hard | o69214.json | grammar exceeds node budget of 100000 |
| Github_hard | o69215.json | grammar exceeds node budget of 100000 |
| Github_hard | o71528.json | grammar exceeds node budget of 100000 |
| Github_hard | o71568.json | grammar exceeds node budget of 100000 |
| Github_hard | o7573.json | grammar exceeds node budget of 100000 |
| Github_hard | o82273.json | grammar exceeds node budget of 100000 |
| Github_hard | o90832.json | grammar exceeds node budget of 100000 |
| Github_hard | o90895.json | grammar exceeds node budget of 100000 |
| Github_hard | o90924.json | grammar exceeds node budget of 100000 |
| Github_hard | o90925.json | grammar exceeds node budget of 100000 |
| Github_hard | o91595.json | grammar exceeds node budget of 100000 |
| Github_medium | o3626.json | grammar exceeds node budget of 100000 |
| Github_medium | o39217.json | grammar exceeds node budget of 100000 |
| Github_medium | o53100.json | grammar exceeds node budget of 100000 |
| Github_medium | o58661.json | grammar exceeds node budget of 100000 |
| Github_medium | o60170.json | grammar exceeds node budget of 100000 |
| Github_medium | o65012.json | grammar exceeds node budget of 100000 |
| Github_medium | o69991.json | grammar exceeds node budget of 100000 |
| Github_medium | o72177.json | grammar exceeds node budget of 100000 |
| Github_medium | o78957.json | grammar exceeds node budget of 100000 |
| Github_medium | o79622.json | grammar exceeds node budget of 100000 |
| Github_medium | o81108.json | grammar exceeds node budget of 100000 |
| Github_medium | o83086.json | grammar exceeds node budget of 100000 |
| Github_trivial | o47165.json | grammar exceeds node budget of 100000 |
| Github_ultra | o18949.json | anyOf has more than 64 branches |
| Github_ultra | o19343.json | grammar exceeds node budget of 100000 |
| Github_ultra | o21173.json | grammar exceeds node budget of 100000 |
| Github_ultra | o21193.json | grammar exceeds node budget of 100000 |
| Github_ultra | o21304.json | grammar exceeds node budget of 100000 |
| Github_ultra | o21307.json | grammar exceeds node budget of 100000 |
| Github_ultra | o21308.json | grammar exceeds node budget of 100000 |
| Github_ultra | o21328.json | grammar exceeds node budget of 100000 |
| Github_ultra | o21380.json | grammar exceeds node budget of 100000 |
| Github_ultra | o21764.json | grammar exceeds node budget of 100000 |
| Github_ultra | o38670.json | grammar exceeds node budget of 100000 |
| Github_ultra | o39449.json | grammar exceeds node budget of 100000 |
| Github_ultra | o44208.json | grammar exceeds node budget of 100000 |
| Github_ultra | o48403.json | grammar exceeds node budget of 100000 |
| Github_ultra | o48404.json | grammar exceeds node budget of 100000 |
| Github_ultra | o48661.json | grammar exceeds node budget of 100000 |
| Github_ultra | o50639.json | grammar exceeds node budget of 100000 |
| Github_ultra | o69206.json | grammar exceeds node budget of 100000 |
| Github_ultra | o69209.json | grammar exceeds node budget of 100000 |
| Github_ultra | o70036.json | grammar exceeds node budget of 100000 |
| Github_ultra | o78463.json | grammar exceeds node budget of 100000 |
| Github_ultra | o80208.json | grammar exceeds node budget of 100000 |
| Github_ultra | o80235.json | grammar exceeds node budget of 100000 |
| Github_ultra | o81.json | grammar exceeds node budget of 100000 |
| Github_ultra | o83932.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | accelerator.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | cargo-make.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | cityjson.min.schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | cloudformation.schema.json | schema exceeds schema_limit_bytes |
| JsonSchemaStore | codeship-steps.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | component_spec.json_schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | config.json | schema exceeds schema_limit_bytes |
| JsonSchemaStore | dart-test.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | detekt-1.22.0.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | dss-2.0.0.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | eslintrc.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | jfrog-pipelines.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | jsone.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | jsonld.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | jx-schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | machine.schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | meta.schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | micro-syntax.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | minecraft-item-modifier.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | minecraft-loot-table.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | minecraft-predicate.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | opspec-io-0.1.7.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | pulumi.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | renovate-schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | sarif-2.1.0-rtm.2.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | sarif-2.1.0-rtm.3.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | sarif-2.1.0-rtm.4.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | sarif-2.1.0-rtm.5.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | sarif-2.1.0-rtm.6.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | sarif-2.1.0.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | semgrep.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | service-schema.json | anyOf has more than 64 branches |
| JsonSchemaStore | tmlanguage.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | utam-page-object.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | vega.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | web-types.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | yippee-ki-json_config_schema.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_1161_Normalized.json | schema exceeds schema_limit_bytes |
| Kubernetes | kb_190_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_191_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_192_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_196_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_197_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_198_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_202_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_203_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_204_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_208_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_209_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_210_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_220_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_221_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_222_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_494_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_495_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | kb_496_Normalized.json | grammar exceeds node budget of 100000 |
