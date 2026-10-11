# Coverage report - JSONSchemaBench MaskBench snapshot (maskbench/data/)

- corpus: https://github.com/guidance-ai/jsonschemabench revision `ba103c73756198dd9b149ddc7db7867da7a077f6` (`maskbench/data/`), sha256 `9021735d444e27cab2b4988ad5ee908bbbae55b9fb5921e0bc3f9a3f62ba85fa`
- engine commit: `dbc5eeb8886dc618919373af937962e84644b2a0+dirty` (bolorgir 0.1.0, ABI 1), profile `spec-v1`
- limits: mode=adaptive, memory_limit_bytes=268435456, cache_limit_bytes=67108864, session_limit_bytes=8388608, schema_limit_bytes=1048576, max_depth=64, max_threads_per_state=64, work_limit_ops=0
- tokenizer: openai-community/gpt2 @ 607a30d783dfa663caf39e06633721c8d4cfcd7e (50257 tokens)
- date: 2026-10-05T09:16:06Z
- library sha256: `7345490f7ddf5710ec438f0e4baf4bf98b330a61cb6663be045580da95fa56c8`

> `generatable` is the result of K generation passes on the listed tokenizers, not a proof for every continuation

K = 20; tokenizers: byte, llama-3.1, gpt2.

First blocker per file; a refusal is never coverage.

## Outcome buckets

| bucket | files |
|---|---|
| compiled | 10788 |
| invalid_schema | 4 |
| unsupported_feature | 359 |
| resource_limit | 147 |
| unsatisfiable_constraint | 8 |
| total | 11306 |

## Invalid-schema subclasses

| subclass | files |
|---|---|
| other | 4 |

## Unsupported-feature keyword histogram (first blocker)

| keyword | files |
|---|---|
| (other) | 330 |
| $ref | 29 |

## Per-dataset counts

| dataset | total | compiled | invalid_schema | unsupported_feature | resource_limit | valid ex. | invalid ex. |
|---|---|---|---|---|---|---|---|
| BFCL | 1043 | 1043 | 0 | 0 | 0 | 1043 | 0 |
| Github_easy | 1943 | 1933 | 0 | 8 | 2 | 2641 | 4611 |
| Github_hard | 1240 | 1154 | 3 | 31 | 46 | 1493 | 3405 |
| Github_medium | 1976 | 1945 | 0 | 18 | 13 | 3091 | 6119 |
| Github_trivial | 444 | 439 | 0 | 3 | 1 | 460 | 771 |
| Github_ultra | 164 | 118 | 0 | 17 | 29 | 160 | 302 |
| Glaiveai2K | 1707 | 1707 | 0 | 0 | 0 | 1634 | 1104 |
| Handwritten | 147 | 56 | 0 | 90 | 0 | 14 | 39 |
| JME | 100 | 100 | 0 | 0 | 0 | 100 | 0 |
| JsonSchemaStore | 492 | 414 | 1 | 40 | 37 | 679 | 1405 |
| Kubernetes | 1064 | 1045 | 0 | 0 | 19 | 1680 | 2908 |
| MCPspec | 45 | 45 | 0 | 0 | 0 | 44 | 44 |
| Snowplow | 403 | 400 | 0 | 3 | 0 | 670 | 1730 |
| Synthesized | 413 | 295 | 0 | 118 | 0 | 109 | 279 |
| WashingtonPost | 125 | 94 | 0 | 31 | 0 | 146 | 330 |

Examples: 13964 valid, 23047 invalid.

## Semantic coverage (split metrics)

Each metric has its own base; compile coverage alone is not exact
coverage. Semantic checks run on a byte-level synthetic tokenizer
(token id = byte value, eos=256) with the `spec-v1` profile;
instances are encoded with python/bolorgir/serializer.py serialize_value and cross-checked
with jsonschema==4.26.0; generation budgets: 8192 steps and 10s wall time per schema. The byte context is recycled every 150 checks (plus a retry on RESOURCE_LIMIT): 218 recycles this run - engine retains generation memory in a Context across session/grammar free (R5 finding). Context destroy() failures: 0.

| metric | n | base | % |
|---|---|---|---|
| compile coverage | 10788 | 11306 | 95.42% |
| explicit refusals (engine declines the schema) | 518 | 11306 | 4.58% |
| session success | 10784 | 10788 | 99.96% |
| generation success | 10397 | 10788 | 96.38% |
| generation success (of attempted: compiled minus oversize skips) | 10397 | 10782 | 96.43% |
| generated-doc schema validity (jsonschema) | 10352 | 10397 | 99.57% |
| valid-instance acceptance | 13100 | 13612 | 96.24% |
| invalid-instance rejection | 20465 | 22296 | 91.79% |
| serialization incompatibility | 0 | 35908 | 0.00% |

Detail counters:

| counter | n |
|---|---|
| compile mismatch (byte-context compile differs) | 3 |
| session create failed | 1 |
| generation: dead end | 0 |
| generation: step/time budget exhausted | 189 |
| generation: oversize skip (min document size estimate > step budget; not attempted) | 2 |
| generation: engine error | 196 |
| generated doc not JSON (finish accepted it) | 0 |
| generated doc rejected by jsonschema | 42 |
| generated doc validator error (no verdict) | 3 |
| schema unusable for jsonschema (validator_error) | 3 |
| valid instances: rejected | 0 |
| valid instances: engine error | 512 |
| valid instances: corpus label vs jsonschema disagree | 0 |
| invalid instances: accepted (jsonschema agrees invalid) | 2 |
| invalid instances: accepted (jsonschema says valid) | 842 |
| invalid instances: engine error | 987 |

## Semantic failure kinds (schemas that compile but fail a check)

| failure kind | count |
|---|---|
| engine_error_instance | 1499 |
| generation_error | 196 |
| generation_budget | 189 |
| generated_schema_invalid | 42 |
| compile_mismatch | 3 |
| invalid_instance_accepted | 2 |
| session_create_failed | 1 |

### generation_error (196)

| dataset | file | detail |
|---|---|---|
| Github_easy | Github_easy---o13129.json | error:RESOURCE_LIMIT: fill_mask at step 0 |
| Github_easy | Github_easy---o2231.json | error:RESOURCE_LIMIT: fill_mask at step 0 |
| Github_easy | Github_easy---o39084.json | error:RESOURCE_LIMIT: fill_mask at step 45 |
| Github_easy | Github_easy---o44463.json | error:RESOURCE_LIMIT: fill_mask at step 0 |
| Github_easy | Github_easy---o4834.json | error:RESOURCE_LIMIT: fill_mask at step 2 |
| Github_easy | Github_easy---o6187.json | error:RESOURCE_LIMIT: fill_mask at step 2 |
| Github_easy | Github_easy---o65440.json | error:RESOURCE_LIMIT: fill_mask at step 7 |
| Github_easy | Github_easy---o68297.json | error:RESOURCE_LIMIT: fill_mask at step 10 |
| Github_easy | Github_easy---o69914.json | error:RESOURCE_LIMIT: fill_mask at step 13 |
| Github_easy | Github_easy---o72111.json | error:RESOURCE_LIMIT: fill_mask at step 2 |
| Github_easy | Github_easy---o81526.json | error:RESOURCE_LIMIT: fill_mask at step 2 |
| Github_easy | Github_easy---o81530.json | error:RESOURCE_LIMIT: fill_mask at step 0 |
| Github_easy | Github_easy---o89710.json | error:RESOURCE_LIMIT: fill_mask at step 0 |
| Github_easy | Github_easy---o90246.json | error:RESOURCE_LIMIT: fill_mask at step 2 |
| Github_easy | Github_easy---o90287.json | error:RESOURCE_LIMIT: fill_mask at step 0 |

### engine_error_instance (1499, first 200)

| dataset | file | detail |
|---|---|---|
| Github_easy | Github_easy---o13129.json | valid instance: error:RESOURCE_LIMIT fill_mask at byte 0 |
| Github_easy | Github_easy---o13129.json | valid instance: error:RESOURCE_LIMIT fill_mask at byte 0 |
| Github_easy | Github_easy---o13129.json | invalid instance: error:RESOURCE_LIMIT fill_mask at byte 0 |
| Github_easy | Github_easy---o13129.json | invalid instance: error:RESOURCE_LIMIT fill_mask at byte 0 |
| Github_easy | Github_easy---o21847.json | valid instance: error:RESOURCE_LIMIT fill_mask at byte 16 |
| Github_easy | Github_easy---o21847.json | valid instance: error:RESOURCE_LIMIT fill_mask at byte 16 |
| Github_easy | Github_easy---o21847.json | invalid instance: error:RESOURCE_LIMIT fill_mask at byte 16 |
| Github_easy | Github_easy---o21847.json | invalid instance: error:RESOURCE_LIMIT fill_mask at byte 16 |
| Github_easy | Github_easy---o21847.json | invalid instance: error:RESOURCE_LIMIT fill_mask at byte 16 |
| Github_easy | Github_easy---o21848.json | valid instance: error:RESOURCE_LIMIT fill_mask at byte 184 |
| Github_easy | Github_easy---o2231.json | valid instance: error:RESOURCE_LIMIT fill_mask at byte 0 |
| Github_easy | Github_easy---o37786.json | valid instance: error:RESOURCE_LIMIT fill_mask at byte 2 |
| Github_easy | Github_easy---o37786.json | valid instance: error:RESOURCE_LIMIT fill_mask at byte 2 |
| Github_easy | Github_easy---o37786.json | invalid instance: error:RESOURCE_LIMIT fill_mask at byte 2 |
| Github_easy | Github_easy---o37786.json | invalid instance: error:RESOURCE_LIMIT fill_mask at byte 2 |

### generation_budget (189)

| dataset | file | detail |
|---|---|---|
| Github_easy | Github_easy---o13652.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o15726.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o21040.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o27844.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o28609.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o28614.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o33825.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o40228.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o40230.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o41033.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o41049.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o4354.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o44188.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o47923.json | step budget 8192 exhausted |
| Github_easy | Github_easy---o48547.json | step budget 8192 exhausted |

### generated_schema_invalid (42)

| dataset | file | detail |
|---|---|---|
| Github_easy | Github_easy---o17940.json | jsonschema rejects the generated document: {"arch":"","clientId":"00000000-0000-0000-0000-000000000000","device":"","locale":"","os":"","osversion":"","seq":-0,"v" |
| Github_easy | Github_easy---o36461.json | jsonschema rejects the generated document: {"value":0.0000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000 |
| Github_easy | Github_easy---o36463.json | jsonschema rejects the generated document: {"value":0.0000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000 |
| Github_easy | Github_easy---o44051.json | jsonschema rejects the generated document: {"action":"ingest","location":"https://","id":"","version":0.00000000000000000000000000000000000000000000000000000000000 |
| Github_easy | Github_easy---o57656.json | jsonschema rejects the generated document: {"NumberOfImages":0.0000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000 |
| Github_easy | Github_easy---o83684.json | jsonschema rejects the generated document: {"functionCode":"","usageUnits":-0,"division":0.000000000000000000000000000000000000000000000000000000000000000015000000 |
| Github_easy | Github_easy---o83685.json | jsonschema rejects the generated document: {"functionCode":"","usageUnits":-0,"division":0.000000000000000000000000000000000000000000000000000000000000000010000000 |
| Github_easy | Github_easy---o83687.json | jsonschema rejects the generated document: {"name":"","sex":"M","age":0.0000000000000000000000000000000000000000000000000000000000000000120000000000000000000000000 |
| Github_hard | Github_hard---o41192.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","skip-bids-validation":false,"task-id":"","echo-idx":"","n_cpus":-0,"mem_mb":-0,"anat |
| Github_hard | Github_hard---o41250.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{},"freesurfer_license":{}},"config":{"inputs":"","config":"","api_key":"", |
| Github_hard | Github_hard---o41251.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","skip-bids-validation":false,"task-id":"","echo-idx":"","anat-only":false,"error-on-a |
| Github_hard | Github_hard---o41252.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{}},"config":{"inputs":"","config":"","api_key":"","save_outputs":false,"fo |
| Github_hard | Github_hard---o41256.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{}},"config":{"inputs":"","config":"","api_key":"","ignore_aroma_denoising_ |
| Github_hard | Github_hard---o41261.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{}},"config":{"inputs":"","config":"","api_key":"","save_outputs":false,"fo |
| Github_hard | Github_hard---o41263.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{},"freesurfer_license":{}},"config":{"inputs":"","config":"","api_key":"", |

### invalid_instance_accepted (2)

| dataset | file | detail |
|---|---|---|
| Github_easy | Github_easy---o24544.json | engine and serializer accept an instance jsonschema rejects |
| Github_trivial | Github_trivial---o14485.json | engine and serializer accept an instance jsonschema rejects |

### compile_mismatch (3)

| dataset | file | detail |
|---|---|---|
| Github_hard | Github_hard---o21344.json | RESOURCE_LIMIT: blg_compile: RESOURCE_LIMIT (out of memory during compile) |
| Github_hard | Github_hard---o21412.json | RESOURCE_LIMIT: blg_compile: RESOURCE_LIMIT (out of memory during compile) |
| Github_hard | Github_hard---o21420.json | RESOURCE_LIMIT: blg_compile: RESOURCE_LIMIT (out of memory during compile) |

### session_create_failed (1)

| dataset | file | detail |
|---|---|---|
| JsonSchemaStore | JsonSchemaStore---ui5.yaml.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |

## Oversize generation skips (2)

Static minimum-document-size estimate above the step budget;
generation not attempted (a documented skip, not a failure).

| dataset | file | detail |
|---|---|---|
| Github_easy | Github_easy---o12622.json | estimated minimum document size 17440 bytes > step budget 8192; generation not attempted |
| Handwritten | Handwritten---string1.json | estimated minimum document size 10002 bytes > step budget 8192; generation not attempted |

## Generatable (K passes per schema and tokenizer)

K = 20. Pass 1 runs the greedy `generate_document` policy over single-byte tokens; passes 2..K run the biased random policy of `tests/generation_passes.py` (seed = sha256 of the base seed 20261002, file, tokenizer and pass index). Budgets per pass: 8192 steps and 10 s. Per schema and tokenizer: 30 s, after which the remaining passes are recorded as `skipped:schema_budget`. The run of 2026-10-04T03:28:04Z (`coverage_maskbench.json`, sha256 `5f167036bb83f88f...`) is completed with `--complete-from`: its passes recorded as `skipped:schema_budget` ran, its invalid_document and validator_error passes were replayed by seed for their documents, and every other record is that run's. The completed report of 2026-10-05T07:07:48Z (`coverage_maskbench.json`, sha256 `e15a7eaf98ac5c92...`) is completed again: its invalid_document and validator_error passes without a document were replayed with 60 s per pass. The completed report of 2026-10-05T09:00:59Z (`coverage_maskbench.json`, sha256 `81987a02a2b34832...`) is completed again: its invalid_document and validator_error passes without a document were replayed with 60 s per pass (a replay that ended in engine_unavailable again on a fresh context ran alone in a child process). Every document is validated by jsonschema==4.26.0 over exact numbers (tests/exact_numbers.py), `pattern` by regress==2025.10.1 (ECMA-262, unicode mode). Aggregation per schema: the first matching rule of `tests/semantic_outcomes.py` `aggregate_schema_outcome`; `skipped` is outside the percentage base. Every pass record is in `coverage_maskbench.passes.json.gz`.

| tokenizer | name | revision | vocab | single-byte tokens | schema sample |
|---|---|---|---|---|---|
| byte | byte-level synthetic (token id = byte value, eos=256, pad=257) | - | 258 | 256 | all |
| llama-3.1 | unsloth/Meta-Llama-3.1-8B-Instruct | a2856192dd7c25b842431f39c179a6c2c2f627d1 | 128256 | 256 | 1 in 20 |
| gpt2 | openai-community/gpt2 | 607a30d783dfa663caf39e06633721c8d4cfcd7e | 50257 | 256 | 1 in 20 |

A sampled tokenizer runs on the compiled schemas whose sha256(dataset/file) is 0 modulo the sample size; the others are `skipped` with code `not_sampled`.

| schema outcome | byte | llama-3.1 | gpt2 |
|---|---|---|---|
| generatable | 9614 (89.16%) | 325 (60.75%) | 364 (68.04%) |
| invalid_document | 686 (6.36%) | 4 (0.75%) | 4 (0.75%) |
| engine_defect | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| validator_error | 0 (0.00%) | 0 (0.00%) | 0 (0.00%) |
| engine_unavailable | 405 (3.76%) | 57 (10.65%) | 66 (12.34%) |
| incomplete | 78 (0.72%) | 149 (27.85%) | 101 (18.88%) |
| skipped | 5 | 10253 | 10253 |
| schemas with an outcome / compiled | 10788 / 10788 | 10788 / 10788 | 10788 / 10788 |
| percentage base (compiled minus skipped) | 10783 | 535 | 535 |
| incomplete passes inside generatable schemas | 8276 | 2617 | 2466 |
| unsuccessful results retried on a fresh context | 411 | 54 | 64 |
| results with passes skipped by the schema budget | 0 | 0 | 0 |
| invalid_document / validator_error passes with their document | 5078 / 5078 | 21 / 21 | 29 / 29 |
| passes run / replayed / replays not reproduced by --complete-from | 8435 / 4776 / 0 | 6325 / 17 / 0 | 4988 / 29 / 0 |
| replays not reproduced earlier and replayed again | 185 | 2 | 0 |

Pass outcomes:

| pass outcome | byte | llama-3.1 | gpt2 |
|---|---|---|---|
| completed_valid | 194060 | 4063 | 5046 |
| engine_unavailable | 4996 | 604 | 747 |
| incomplete:step_limit | 5379 | 1 | 1 |
| incomplete:time_limit | 6047 | 5991 | 4877 |
| invalid_document | 5078 | 21 | 29 |

### byte: schemas that are not generatable, by outcome / stage / code

| outcome / stage / code | schemas |
|---|---|
| invalid_document / validate / REJECTED | 686 |
| engine_unavailable / fill_mask / BLG_ERR_RESOURCE_LIMIT | 399 |
| incomplete / fill_mask / TIME_LIMIT | 47 |
| incomplete / fill_mask / STEP_LIMIT | 31 |
| engine_unavailable / compile_check / BLG_ERR_RESOURCE_LIMIT | 5 |
| skipped / None / validator_unusable | 3 |
| skipped / None / oversize | 2 |
| engine_unavailable / session_create / BLG_ERR_RESOURCE_LIMIT | 1 |

#### byte: invalid_document (686, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | Github_easy---o10295.json | validate | REJECTED | 95 | 95 | 7065544363529 |
| Github_easy | Github_easy---o10298.json | validate | REJECTED | 139 | 139 | 19948261902283 |
| Github_easy | Github_easy---o11170.json | validate | REJECTED | 997 | 997 | 48785983974132 |
| Github_easy | Github_easy---o11171.json | validate | REJECTED | 1374 | 1374 | 198922544674027 |
| Github_easy | Github_easy---o11787.json | validate | REJECTED | 6316 | 6316 | 61186469595903 |
| Github_easy | Github_easy---o11794.json | validate | REJECTED | 2194 | 2194 | 14651572689484 |
| Github_easy | Github_easy---o11796.json | validate | REJECTED | 797 | 797 | 126978175897975 |
| Github_easy | Github_easy---o11798.json | validate | REJECTED | 5227 | 5227 | 18099313467475 |
| Github_easy | Github_easy---o12214.json | validate | REJECTED | 30 | 30 | 157825964159208 |
| Github_easy | Github_easy---o12215.json | validate | REJECTED | 70 | 70 | 153405081714432 |
| Github_easy | Github_easy---o12218.json | validate | REJECTED | 38 | 38 | 124409418252866 |
| Github_easy | Github_easy---o12229.json | validate | REJECTED | 885 | 885 | 60619212390526 |
| Github_easy | Github_easy---o12235.json | validate | REJECTED | 929 | 929 | 190122307494805 |
| Github_easy | Github_easy---o12254.json | validate | REJECTED | 1918 | 1918 | 251877747759012 |
| Github_easy | Github_easy---o13435.json | validate | REJECTED | 206 | 206 | 31320061076480 |

#### byte: skipped (5)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | Github_easy---o12622.json | None | oversize | None | None | None |
| Github_easy | Github_easy---o66201.json | None | validator_unusable | None | None | None |
| Github_medium | Github_medium---o82255.json | None | validator_unusable | None | None | None |
| Github_ultra | Github_ultra---o15286.json | None | validator_unusable | None | None | None |
| Handwritten | Handwritten---string1.json | None | oversize | None | None | None |

#### byte: engine_unavailable (405, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | Github_easy---o13129.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 0 | 0 | 187336195817685 |
| Github_easy | Github_easy---o21847.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 154 | 154 | 134052174387442 |
| Github_easy | Github_easy---o21848.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 74 | 74 | 280746144553290 |
| Github_easy | Github_easy---o2231.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 0 | 0 | 140758565666124 |
| Github_easy | Github_easy---o37786.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 2 | 2 | 242476466080942 |
| Github_easy | Github_easy---o37788.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 2 | 2 | 203401124725459 |
| Github_easy | Github_easy---o39084.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 45 | 45 | 257025636853595 |
| Github_easy | Github_easy---o42523.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 10 | 10 | 120369330134841 |
| Github_easy | Github_easy---o44463.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 0 | 0 | 141931491386386 |
| Github_easy | Github_easy---o4834.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 2 | 2 | 78910500049642 |
| Github_easy | Github_easy---o6187.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 2 | 2 | 14932859465554 |
| Github_easy | Github_easy---o65361.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 9 | 9 | 266290047508696 |
| Github_easy | Github_easy---o65440.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 7 | 7 | 143309025140509 |
| Github_easy | Github_easy---o65451.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 125 | 125 | 53133273992331 |
| Github_easy | Github_easy---o68297.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 10 | 10 | 275677455028307 |

#### byte: incomplete (78, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | Github_easy---o40228.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 234472864953172 |
| Github_easy | Github_easy---o40230.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 280974108121024 |
| Github_easy | Github_easy---o41033.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 124940365104778 |
| Github_easy | Github_easy---o66294.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 200977297213973 |
| Github_hard | Github_hard---o13152.json | fill_mask | TIME_LIMIT | 6320 | 6320 | 130780259688334 |
| Github_hard | Github_hard---o16058.json | fill_mask | TIME_LIMIT | 2928 | 2928 | 64260236336773 |
| Github_hard | Github_hard---o16059.json | fill_mask | TIME_LIMIT | 2656 | 2656 | 38292091596630 |
| Github_hard | Github_hard---o16060.json | fill_mask | TIME_LIMIT | 2800 | 2800 | 147621833917581 |
| Github_hard | Github_hard---o16064.json | fill_mask | TIME_LIMIT | 2784 | 2784 | 154695909453773 |
| Github_hard | Github_hard---o17984.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 121896408959418 |
| Github_hard | Github_hard---o31337.json | fill_mask | STEP_LIMIT | 8192 | 8192 | 33462498533686 |
| Github_hard | Github_hard---o34341.json | fill_mask | TIME_LIMIT | 4288 | 4288 | 229266504332488 |
| Github_hard | Github_hard---o34342.json | fill_mask | TIME_LIMIT | 4112 | 4112 | 74773036034465 |
| Github_hard | Github_hard---o34343.json | fill_mask | TIME_LIMIT | 4272 | 4272 | 257846564309810 |
| Github_hard | Github_hard---o37717.json | fill_mask | TIME_LIMIT | 8160 | 8160 | 95372677101689 |

### llama-3.1: schemas that are not generatable, by outcome / stage / code

| outcome / stage / code | schemas |
|---|---|
| skipped / None / not_sampled | 10253 |
| incomplete / fill_mask / TIME_LIMIT | 149 |
| engine_unavailable / fill_mask / BLG_ERR_RESOURCE_LIMIT | 56 |
| invalid_document / validate / REJECTED | 4 |
| engine_unavailable / compile_check / BLG_ERR_RESOURCE_LIMIT | 1 |

#### llama-3.1: skipped (10253, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| BFCL | BFCL_java_0.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_1.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_10.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_11.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_12.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_13.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_14.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_15.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_16.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_17.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_18.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_19.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_20.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_21.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_22.json | None | not_sampled | None | None | None |

#### llama-3.1: incomplete (149, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | Github_easy---o11513.json | fill_mask | TIME_LIMIT | 16 | 16 | 93456696302922 |
| Github_easy | Github_easy---o11796.json | fill_mask | TIME_LIMIT | 16 | 16 | 6015212365216 |
| Github_easy | Github_easy---o11859.json | fill_mask | TIME_LIMIT | 32 | 32 | 129098185800644 |
| Github_easy | Github_easy---o21089.json | fill_mask | TIME_LIMIT | 16 | 16 | 87741409142501 |
| Github_easy | Github_easy---o26578.json | fill_mask | TIME_LIMIT | 32 | 32 | 242550304847931 |
| Github_easy | Github_easy---o33733.json | fill_mask | TIME_LIMIT | 16 | 16 | 247374824234572 |
| Github_easy | Github_easy---o42223.json | fill_mask | TIME_LIMIT | 16 | 16 | 29253513089210 |
| Github_easy | Github_easy---o50682.json | fill_mask | TIME_LIMIT | 16 | 16 | 11179701411932 |
| Github_easy | Github_easy---o53621.json | fill_mask | TIME_LIMIT | 16 | 16 | 259557324724252 |
| Github_easy | Github_easy---o60850.json | fill_mask | TIME_LIMIT | 16 | 16 | 90256691407634 |
| Github_easy | Github_easy---o60852.json | fill_mask | TIME_LIMIT | 16 | 16 | 48742659341223 |
| Github_easy | Github_easy---o64825.json | fill_mask | TIME_LIMIT | 32 | 32 | 228080257433821 |
| Github_easy | Github_easy---o65381.json | fill_mask | TIME_LIMIT | 16 | 16 | 89083714250477 |
| Github_easy | Github_easy---o68296.json | fill_mask | TIME_LIMIT | 6 | 6 | 273674702134605 |
| Github_easy | Github_easy---o68314.json | fill_mask | TIME_LIMIT | 16 | 16 | 128011680526508 |

#### llama-3.1: engine_unavailable (57, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | Github_easy---o20376.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 13 | 13 | 278110867846206 |
| Github_easy | Github_easy---o21209.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 11 | 20 | 244670483514627 |
| Github_easy | Github_easy---o21477.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 5 | 8 | 101103134248724 |
| Github_easy | Github_easy---o21848.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 13 | 13 | 139852771487129 |
| Github_easy | Github_easy---o42525.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 11 | 11 | 206812823135168 |
| Github_easy | Github_easy---o42792.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 12 | 12 | 247377700586805 |
| Github_easy | Github_easy---o44189.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 27 | 36 | 208993062315430 |
| Github_easy | Github_easy---o62138.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 8 | 8 | 139396969128455 |
| Github_easy | Github_easy---o82294.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 5 | 10 | 46689530903550 |
| Github_easy | Github_easy---o83719.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 6 | 6 | 154630159496627 |
| Github_hard | Github_hard---o17700.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 37 | 37 | 162034963631959 |
| Github_hard | Github_hard---o21230.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 13 | 13 | 254725872690962 |
| Github_hard | Github_hard---o21232.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 39 | 39 | 5188997833853 |
| Github_hard | Github_hard---o21262.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 13 | 13 | 208662197459472 |
| Github_hard | Github_hard---o21362.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 13 | 13 | 181307744632741 |

#### llama-3.1: invalid_document (4)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | Github_easy---o36463.json | validate | REJECTED | 209 | 209 | 229355188633081 |
| Github_hard | Github_hard---o56003.json | validate | REJECTED | 77 | 142 | 133877927315017 |
| Github_medium | Github_medium---o56014.json | validate | REJECTED | 43 | 103 | 223151567717911 |
| Github_medium | Github_medium---o56020.json | validate | REJECTED | 36 | 82 | 159903412412169 |

### gpt2: schemas that are not generatable, by outcome / stage / code

| outcome / stage / code | schemas |
|---|---|
| skipped / None / not_sampled | 10253 |
| incomplete / fill_mask / TIME_LIMIT | 101 |
| engine_unavailable / fill_mask / BLG_ERR_RESOURCE_LIMIT | 66 |
| invalid_document / validate / REJECTED | 4 |

#### gpt2: skipped (10253, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| BFCL | BFCL_java_0.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_1.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_10.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_11.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_12.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_13.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_14.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_15.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_16.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_17.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_18.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_19.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_20.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_21.json | None | not_sampled | None | None | None |
| BFCL | BFCL_java_22.json | None | not_sampled | None | None | None |

#### gpt2: incomplete (101, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | Github_easy---o11513.json | fill_mask | TIME_LIMIT | 48 | 48 | 96515232896756 |
| Github_easy | Github_easy---o11796.json | fill_mask | TIME_LIMIT | 48 | 48 | 45249671271443 |
| Github_easy | Github_easy---o11859.json | fill_mask | TIME_LIMIT | 48 | 48 | 145978449838499 |
| Github_easy | Github_easy---o26578.json | fill_mask | TIME_LIMIT | 48 | 48 | 89027836437513 |
| Github_easy | Github_easy---o33733.json | fill_mask | TIME_LIMIT | 48 | 48 | 163614546375303 |
| Github_easy | Github_easy---o50682.json | fill_mask | TIME_LIMIT | 32 | 32 | 259576959726404 |
| Github_easy | Github_easy---o53621.json | fill_mask | TIME_LIMIT | 48 | 48 | 58555529894923 |
| Github_easy | Github_easy---o60850.json | fill_mask | TIME_LIMIT | 32 | 32 | 41504074773146 |
| Github_easy | Github_easy---o60852.json | fill_mask | TIME_LIMIT | 48 | 48 | 30942804790766 |
| Github_easy | Github_easy---o68296.json | fill_mask | TIME_LIMIT | 16 | 16 | 236350524174851 |
| Github_easy | Github_easy---o69745.json | fill_mask | TIME_LIMIT | 48 | 48 | 197692508286538 |
| Github_easy | Github_easy---o76467.json | fill_mask | TIME_LIMIT | 32 | 32 | 29928845049028 |
| Github_easy | Github_easy---o83411.json | fill_mask | TIME_LIMIT | 48 | 48 | 264539259109639 |
| Github_easy | Github_easy---o8491.json | fill_mask | TIME_LIMIT | 48 | 48 | 51267382175709 |
| Github_hard | Github_hard---o13151.json | fill_mask | TIME_LIMIT | 48 | 48 | 111462258253370 |

#### gpt2: engine_unavailable (66, first 15)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | Github_easy---o20376.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 14 | 14 | 58404986388993 |
| Github_easy | Github_easy---o21209.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 8 | 21 | 192600057246238 |
| Github_easy | Github_easy---o21477.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 17 | 39 | 93531404661973 |
| Github_easy | Github_easy---o21848.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 14 | 14 | 98289594105516 |
| Github_easy | Github_easy---o42525.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 12 | 12 | 254899029868279 |
| Github_easy | Github_easy---o42540.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 36 | 95 | 262298502469687 |
| Github_easy | Github_easy---o42792.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 13 | 13 | 121072181957475 |
| Github_easy | Github_easy---o44189.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 23 | 38 | 108870986220623 |
| Github_easy | Github_easy---o62138.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 9 | 9 | 279594903044416 |
| Github_easy | Github_easy---o82294.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 5 | 9 | 229979608719971 |
| Github_easy | Github_easy---o83719.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 6 | 6 | 234193521444575 |
| Github_hard | Github_hard---o17700.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 38 | 38 | 83184427113880 |
| Github_hard | Github_hard---o21230.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 14 | 14 | 126550382374023 |
| Github_hard | Github_hard---o21232.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 40 | 40 | 97822734919352 |
| Github_hard | Github_hard---o21262.json | fill_mask | BLG_ERR_RESOURCE_LIMIT | 14 | 14 | 221905642927944 |

#### gpt2: invalid_document (4)

| dataset | file | stage | code | step | prefix | seed |
|---|---|---|---|---|---|---|
| Github_easy | Github_easy---o36463.json | validate | REJECTED | 209 | 209 | 127119421408073 |
| Github_hard | Github_hard---o56003.json | validate | REJECTED | 72 | 135 | 49281222120800 |
| Github_medium | Github_medium---o56014.json | validate | REJECTED | 37 | 86 | 57170434911741 |
| Github_medium | Github_medium---o56020.json | validate | REJECTED | 36 | 94 | 152399340019596 |

## Resource-limit files

| dataset | file | detail |
|---|---|---|
| Github_easy | Github_easy---o37787.json | out of memory during compile |
| Github_easy | Github_easy---o58839.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o13029.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o13457.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o19336.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o21072.json | out of memory during compile |
| Github_hard | Github_hard---o21073.json | out of memory during compile |
| Github_hard | Github_hard---o21074.json | out of memory during compile |
| Github_hard | Github_hard---o21075.json | out of memory during compile |
| Github_hard | Github_hard---o21076.json | out of memory during compile |
| Github_hard | Github_hard---o21156.json | out of memory during compile |
| Github_hard | Github_hard---o21157.json | out of memory during compile |
| Github_hard | Github_hard---o21172.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o21192.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o21283.json | out of memory during compile |
| Github_hard | Github_hard---o21287.json | out of memory during compile |
| Github_hard | Github_hard---o21303.json | out of memory during compile |
| Github_hard | Github_hard---o21327.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o21379.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o21400.json | out of memory during compile |
| Github_hard | Github_hard---o27039.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o35186.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o36010.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o36016.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o37789.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o39113.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o39210.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o44213.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o53084.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o62820.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o67291.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o69207.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o69208.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o69210.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o69211.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o69212.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o69214.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o69215.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o71528.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o71568.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o7573.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o82273.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o90832.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o90895.json | out of memory during compile |
| Github_hard | Github_hard---o90924.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o90925.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o91595.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o9882.json | out of memory during compile |
| Github_medium | Github_medium---o3626.json | grammar exceeds node budget of 100000 |
| Github_medium | Github_medium---o39217.json | grammar exceeds node budget of 100000 |
| Github_medium | Github_medium---o53100.json | grammar exceeds node budget of 100000 |
| Github_medium | Github_medium---o58661.json | grammar exceeds node budget of 100000 |
| Github_medium | Github_medium---o60170.json | grammar exceeds node budget of 100000 |
| Github_medium | Github_medium---o65012.json | grammar exceeds node budget of 100000 |
| Github_medium | Github_medium---o69991.json | grammar exceeds node budget of 100000 |
| Github_medium | Github_medium---o72177.json | grammar exceeds node budget of 100000 |
| Github_medium | Github_medium---o78957.json | grammar exceeds node budget of 100000 |
| Github_medium | Github_medium---o79622.json | grammar exceeds node budget of 100000 |
| Github_medium | Github_medium---o81108.json | grammar exceeds node budget of 100000 |
| Github_medium | Github_medium---o83086.json | grammar exceeds node budget of 100000 |
| Github_medium | Github_medium---o9802.json | out of memory during compile |
| Github_trivial | Github_trivial---o47165.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o18949.json | anyOf has more than 64 branches |
| Github_ultra | Github_ultra---o19343.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o21161.json | out of memory during compile |
| Github_ultra | Github_ultra---o21173.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o21193.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o21304.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o21307.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o21308.json | out of memory during compile |
| Github_ultra | Github_ultra---o21312.json | out of memory during compile |
| Github_ultra | Github_ultra---o21328.json | out of memory during compile |
| Github_ultra | Github_ultra---o21375.json | out of memory during compile |
| Github_ultra | Github_ultra---o21376.json | out of memory during compile |
| Github_ultra | Github_ultra---o21380.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o21764.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o38670.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o39449.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o44208.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o48403.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o48404.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o48661.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o50639.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o69206.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o69209.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o70036.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o78463.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o80208.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o80235.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o81.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o83932.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---accelerator.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---cargo-make.json | out of memory during compile |
| JsonSchemaStore | JsonSchemaStore---cityjson.min.schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---cloudformation.schema.json | schema exceeds schema_limit_bytes |
| JsonSchemaStore | JsonSchemaStore---codeship-steps.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---component_spec.json_schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---dart-test.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---detekt-1.22.0.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---dss-2.0.0.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---eslintrc.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---hayson-json-schema.json | out of memory during compile |
| JsonSchemaStore | JsonSchemaStore---jfrog-pipelines.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---jsone.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---jsonld.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---jx-schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---machine.schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---meta.schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---micro-syntax.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---minecraft-item-modifier.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---minecraft-loot-table.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---minecraft-predicate.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---opspec-io-0.1.7.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---pulumi.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---renovate-schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---sarif-2.1.0-rtm.2.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---sarif-2.1.0-rtm.3.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---sarif-2.1.0-rtm.4.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---sarif-2.1.0-rtm.5.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---sarif-2.1.0-rtm.6.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---sarif-2.1.0.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---semgrep.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---service-schema.json | anyOf has more than 64 branches |
| JsonSchemaStore | JsonSchemaStore---tmlanguage.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---utam-page-object.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---vega.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---web-types.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | JsonSchemaStore---yippee-ki-json_config_schema.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_1161_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_190_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_191_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_192_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_196_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_197_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_198_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_202_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_203_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_204_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_208_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_209_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_210_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_220_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_221_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_222_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_494_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_495_Normalized.json | grammar exceeds node budget of 100000 |
| Kubernetes | Kubernetes---kb_496_Normalized.json | grammar exceeds node budget of 100000 |
