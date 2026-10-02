# Coverage report - JSONSchemaBench MaskBench snapshot (maskbench/data/)

- corpus: https://github.com/guidance-ai/jsonschemabench revision `ba103c73756198dd9b149ddc7db7867da7a077f6` (`maskbench/data/`), sha256 `9021735d444e27cab2b4988ad5ee908bbbae55b9fb5921e0bc3f9a3f62ba85fa`
- engine commit: `c1cc2cb315c885ec8b4cd926d1a9b51cde9ad1a2` (bolorgir 0.1.0, ABI 1), profile `spec-v1`
- limits: mode=adaptive, memory_limit_bytes=268435456, cache_limit_bytes=67108864, session_limit_bytes=8388608, schema_limit_bytes=1048576, max_depth=64, max_threads_per_state=64, work_limit_ops=0
- tokenizer: synthetic-byte-fallback @ - (257 tokens)
- date: 2026-09-28T23:13:39Z

First blocker per file; a refusal is never coverage.

## Outcome buckets

| bucket | files |
|---|---|
| compiled | 10790 |
| invalid_schema | 4 |
| unsupported_feature | 359 |
| resource_limit | 145 |
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
| Github_easy | 1943 | 1934 | 0 | 8 | 1 | 2641 | 4611 |
| Github_hard | 1240 | 1157 | 3 | 31 | 43 | 1493 | 3405 |
| Github_medium | 1976 | 1946 | 0 | 18 | 12 | 3091 | 6119 |
| Github_trivial | 444 | 439 | 0 | 3 | 1 | 460 | 771 |
| Github_ultra | 164 | 115 | 0 | 17 | 32 | 160 | 302 |
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
with jsonschema==4.26.0; generation budgets: 8192 steps and 10s wall time per schema. The byte context is recycled every 150 checks (plus a retry on RESOURCE_LIMIT): 100 recycles this run - engine retains generation memory in a Context across session/grammar free (R5 finding). Context destroy() failures: 0.

| metric | n | base | % |
|---|---|---|---|
| compile coverage | 10790 | 11306 | 95.44% |
| explicit refusals (engine declines the schema) | 516 | 11306 | 4.56% |
| session success | 10776 | 10790 | 99.87% |
| generation success | 10396 | 10790 | 96.35% |
| generation success (of attempted: compiled minus oversize skips) | 10396 | 10774 | 96.49% |
| generated-doc schema validity (jsonschema) | 10369 | 10396 | 99.74% |
| valid-instance acceptance | 12759 | 13604 | 93.79% |
| invalid-instance rejection | 21041 | 22294 | 94.38% |
| serialization incompatibility | 0 | 35898 | 0.00% |

Detail counters:

| counter | n |
|---|---|
| compile mismatch (byte-context compile differs) | 6 |
| session create failed | 8 |
| generation: dead end | 20 |
| generation: step/time budget exhausted | 339 |
| generation: oversize skip (min document size estimate > step budget; not attempted) | 2 |
| generation: engine error | 19 |
| generated doc not JSON (finish accepted it) | 0 |
| generated doc rejected by jsonschema | 24 |
| generated doc validator error (no verdict) | 3 |
| schema unusable for jsonschema (validator_error) | 3 |
| valid instances: rejected | 109 |
| valid instances: engine error | 736 |
| valid instances: corpus label vs jsonschema disagree | 0 |
| invalid instances: accepted (jsonschema agrees invalid) | 2 |
| invalid instances: accepted (jsonschema says valid) | 807 |
| invalid instances: engine error | 444 |

## Semantic failure kinds (schemas that compile but fail a check)

| failure kind | count |
|---|---|
| engine_error_instance | 1180 |
| generation_budget | 339 |
| valid_instance_rejected | 109 |
| generated_schema_invalid | 24 |
| generation_dead_end | 20 |
| generation_error | 19 |
| session_create_failed | 8 |
| compile_mismatch | 6 |
| invalid_instance_accepted | 2 |

### engine_error_instance (1180, first 200)

| dataset | file | detail |
|---|---|---|
| Kubernetes | Kubernetes---kb_723_Normalized.json | valid instance: error:CANCELLED fill_mask at byte 839 |
| Kubernetes | Kubernetes---kb_723_Normalized.json | invalid instance: error:CANCELLED fill_mask at byte 838 |
| Kubernetes | Kubernetes---kb_723_Normalized.json | valid instance: error:CANCELLED fill_mask at byte 872 |
| Kubernetes | Kubernetes---kb_724_Normalized.json | valid instance: error:CANCELLED fill_mask at byte 911 |
| Kubernetes | Kubernetes---kb_724_Normalized.json | invalid instance: error:CANCELLED fill_mask at byte 911 |
| Kubernetes | Kubernetes---kb_724_Normalized.json | valid instance: error:CANCELLED fill_mask at byte 884 |
| Kubernetes | Kubernetes---kb_734_Normalized.json | valid instance: error:CANCELLED fill_mask at byte 726 |
| Kubernetes | Kubernetes---kb_734_Normalized.json | invalid instance: error:CANCELLED fill_mask at byte 704 |
| Kubernetes | Kubernetes---kb_760_Normalized.json | valid instance: error:CANCELLED fill_mask at byte 1004 |
| Kubernetes | Kubernetes---kb_760_Normalized.json | invalid instance: error:CANCELLED fill_mask at byte 1005 |
| Kubernetes | Kubernetes---kb_760_Normalized.json | invalid instance: error:CANCELLED fill_mask at byte 1005 |
| Kubernetes | Kubernetes---kb_762_Normalized.json | valid instance: error:CANCELLED fill_mask at byte 982 |
| Kubernetes | Kubernetes---kb_762_Normalized.json | invalid instance: error:CANCELLED fill_mask at byte 907 |
| Kubernetes | Kubernetes---kb_762_Normalized.json | invalid instance: error:CANCELLED fill_mask at byte 837 |
| Kubernetes | Kubernetes---kb_763_Normalized.json | valid instance: error:CANCELLED fill_mask at byte 869 |

### generation_budget (339, first 200)

| dataset | file | detail |
|---|---|---|
| Kubernetes | Kubernetes---kb_854_Normalized.json | step budget 8192 exhausted |
| Kubernetes | Kubernetes---kb_855_Normalized.json | step budget 8192 exhausted |
| Kubernetes | Kubernetes---kb_856_Normalized.json | step budget 8192 exhausted |
| Kubernetes | Kubernetes---kb_857_Normalized.json | step budget 8192 exhausted |
| Kubernetes | Kubernetes---kb_87_Normalized.json | step budget 8192 exhausted |
| Kubernetes | Kubernetes---kb_88_Normalized.json | step budget 8192 exhausted |
| Kubernetes | Kubernetes---kb_89_Normalized.json | step budget 8192 exhausted |
| Kubernetes | Kubernetes---kb_90_Normalized.json | step budget 8192 exhausted |
| Snowplow | Snowplow---sp_209_Normalized.json | time budget 10s exhausted at step 1248 |
| Snowplow | Snowplow---sp_236_Normalized.json | time budget 10s exhausted at step 528 |
| Snowplow | Snowplow---sp_252_Normalized.json | time budget 10s exhausted at step 400 |
| Snowplow | Snowplow---sp_253_Normalized.json | time budget 10s exhausted at step 400 |
| Snowplow | Snowplow---sp_254_Normalized.json | step budget 8192 exhausted |
| Snowplow | Snowplow---sp_255_Normalized.json | step budget 8192 exhausted |
| Snowplow | Snowplow---sp_315_Normalized.json | step budget 8192 exhausted |

### valid_instance_rejected (109)

| dataset | file | detail |
|---|---|---|
| MCPspec | MCPspec---CreateMessageRequest.json | byte 121 (0x22) not in mask |
| MCPspec | MCPspec---GetPromptResult.json | byte 125 (0x22) not in mask |
| MCPspec | MCPspec---ServerRequest.json | byte 54 (0x22) not in mask |
| Snowplow | Snowplow---sp_163_Normalized.json | byte 76 (0x74) not in mask |
| Snowplow | Snowplow---sp_336_Normalized.json | byte 20 (0x52) not in mask |
| Github_easy | Github_easy---o25419.json | byte 53 (0x2c) not in mask |
| WashingtonPost | WashingtonPost---wp_100_Normalized.json | byte 36 (0x74) not in mask |
| WashingtonPost | WashingtonPost---wp_100_Normalized.json | byte 36 (0x74) not in mask |
| WashingtonPost | WashingtonPost---wp_29_Normalized.json | byte 14 (0x2c) not in mask |
| WashingtonPost | WashingtonPost---wp_94_Normalized.json | byte 784 (0x2c) not in mask |
| Github_easy | Github_easy---o83258.json | byte 25 (0x2c) not in mask |
| Github_easy | Github_easy---o83258.json | byte 25 (0x2c) not in mask |
| Github_easy | Github_easy---o89710.json | byte 38 (0x22) not in mask |
| Github_easy | Github_easy---o90953.json | byte 9 (0x22) not in mask |
| Github_easy | Github_easy---o90953.json | byte 9 (0x22) not in mask |

### generation_error (19)

| dataset | file | detail |
|---|---|---|
| Snowplow | Snowplow---sp_261_Normalized.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Snowplow | Snowplow---sp_333_Normalized.json | error:RESOURCE_LIMIT: fill_mask at step 22 |
| Snowplow | Snowplow---sp_334_Normalized.json | error:RESOURCE_LIMIT: fill_mask at step 25 |
| Snowplow | Snowplow---sp_338_Normalized.json | error:RESOURCE_LIMIT: fill_mask at step 21 |
| Snowplow | Snowplow---sp_339_Normalized.json | error:RESOURCE_LIMIT: fill_mask at step 25 |
| Snowplow | Snowplow---sp_345_Normalized.json | error:RESOURCE_LIMIT: fill_mask at step 18 |
| Github_hard | Github_hard---o18948.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Github_hard | Github_hard---o19225.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Github_hard | Github_hard---o41072.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Github_medium | Github_medium---o70035.json | error:INVALID_TOKEN: accept at step 19 |
| Github_ultra | Github_ultra---o17462.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Github_ultra | Github_ultra---o21378.json | error:RESOURCE_LIMIT: fill_mask at step 90 |
| Github_ultra | Github_ultra---o358.json | error:INVALID_TOKEN: accept at step 31 |
| Github_ultra | Github_ultra---o360.json | error:INVALID_TOKEN: accept at step 31 |
| Github_ultra | Github_ultra---o46322.json | error:RESOURCE_LIMIT: fill_mask at step 1 |

### generated_schema_invalid (24)

| dataset | file | detail |
|---|---|---|
| Github_easy | Github_easy---o17940.json | jsonschema rejects the generated document: {"arch":"","clientId":"00000000-0000-0000-0000-000000000000","device":"","locale":"","os":"","osversion":"","seq":-0,"v" |
| Github_hard | Github_hard---o41192.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","skip-bids-validation":false,"task-id":"","echo-idx":"","n_cpus":-0,"mem_mb":-0,"anat |
| Github_hard | Github_hard---o41250.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{},"freesurfer_license":{}},"config":{"inputs":"","config":"","api_key":"", |
| Github_hard | Github_hard---o41251.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","skip-bids-validation":false,"task-id":"","echo-idx":"","anat-only":false,"error-on-a |
| Github_hard | Github_hard---o41252.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{}},"config":{"inputs":"","config":"","api_key":"","save_outputs":false,"fo |
| Github_hard | Github_hard---o41256.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{}},"config":{"inputs":"","config":"","api_key":"","ignore_aroma_denoising_ |
| Github_hard | Github_hard---o41261.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{}},"config":{"inputs":"","config":"","api_key":"","save_outputs":false,"fo |
| Github_hard | Github_hard---o41263.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{},"freesurfer_license":{}},"config":{"inputs":"","config":"","api_key":"", |
| Github_hard | Github_hard---o41265.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","skip-bids-validation":false,"task-id":"","echo-idx":"","anat-only":false,"error-on-a |
| Github_hard | Github_hard---o41291.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","DWIName":"","AnatomyRegDOF":0.000000000000000000000000000000000000000000000000000000 |
| Github_hard | Github_hard---o41300.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","save-on-error":false,"dry-run":false,"fMRIName":"rfMRI_REST1_RL","BiasCorrection":"N |
| Github_hard | Github_hard---o41363.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","intensity_images":{},"mask_image":{}},"config":{"inputs":"","config":"","intensity_i |
| Github_hard | Github_hard---o41364.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","intensity_images":{},"mask_image":{}},"config":{"inputs":"","config":"","intensity_i |
| Github_hard | Github_hard---o41366.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","brain_template":{},"brain_probability_mask":{},"anatomical_image":{}},"config":{"inp |
| Github_hard | Github_hard---o41367.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","segmentation_priors":{},"anatomical_image":{},"t1_registration_template":{},"brain_t |

### invalid_instance_accepted (2)

| dataset | file | detail |
|---|---|---|
| Github_easy | Github_easy---o24544.json | engine and serializer accept an instance jsonschema rejects |
| Github_trivial | Github_trivial---o14485.json | engine and serializer accept an instance jsonschema rejects |

### generation_dead_end (20)

| dataset | file | detail |
|---|---|---|
| Github_easy | Github_easy---o44051.json | fill_mask dead end at step 32 |
| Github_easy | Github_easy---o90314.json | fill_mask dead end at step 3 |
| Github_hard | Github_hard---o14404.json | fill_mask dead end at step 15 |
| Github_hard | Github_hard---o2070.json | fill_mask dead end at step 33 |
| Github_hard | Github_hard---o21334.json | fill_mask dead end at step 102 |
| Github_hard | Github_hard---o44713.json | fill_mask dead end at step 136 |
| Github_hard | Github_hard---o47658.json | fill_mask dead end at step 105 |
| Github_hard | Github_hard---o63309.json | fill_mask dead end at step 40 |
| Github_hard | Github_hard---o72527.json | fill_mask dead end at step 62 |
| Github_hard | Github_hard---o78043.json | fill_mask dead end at step 590 |
| Handwritten | Handwritten---oneofanyofitc1.json | fill_mask dead end at step 2 |
| Handwritten | Handwritten---oneofanyofitc2.json | fill_mask dead end at step 2 |
| Handwritten | Handwritten---oneofanyofitc3.json | fill_mask dead end at step 2 |
| Handwritten | Handwritten---oneofanyofitc4.json | fill_mask dead end at step 2 |
| Handwritten | Handwritten---oneofanyofitc5.json | fill_mask dead end at step 2 |

### compile_mismatch (6)

| dataset | file | detail |
|---|---|---|
| Github_hard | Github_hard---o21215.json | RESOURCE_LIMIT: blg_compile: RESOURCE_LIMIT (out of memory during compile) |
| Github_hard | Github_hard---o21244.json | RESOURCE_LIMIT: blg_compile: RESOURCE_LIMIT (out of memory during compile) |
| Github_hard | Github_hard---o21372.json | RESOURCE_LIMIT: blg_compile: RESOURCE_LIMIT (out of memory during compile) |
| Github_hard | Github_hard---o21396.json | RESOURCE_LIMIT: blg_compile: RESOURCE_LIMIT (out of memory during compile) |
| Github_hard | Github_hard---o21400.json | RESOURCE_LIMIT: blg_compile: RESOURCE_LIMIT (out of memory during compile) |
| Github_ultra | Github_ultra---o21437.json | RESOURCE_LIMIT: blg_compile: RESOURCE_LIMIT (out of memory during compile) |

### session_create_failed (8)

| dataset | file | detail |
|---|---|---|
| Github_medium | Github_medium---o69248.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| Github_medium | Github_medium---o76785.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| Github_trivial | Github_trivial---o35155.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| JsonSchemaStore | JsonSchemaStore---jsconfig.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| JsonSchemaStore | JsonSchemaStore---minecraft-recipe.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| JsonSchemaStore | JsonSchemaStore---openrewrite.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| JsonSchemaStore | JsonSchemaStore---saucectl.schema.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| JsonSchemaStore | JsonSchemaStore---ui5.yaml.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |

## Oversize generation skips (2)

Static minimum-document-size estimate above the step budget;
generation not attempted (a documented skip, not a failure).

| dataset | file | detail |
|---|---|---|
| Github_easy | Github_easy---o12622.json | estimated minimum document size 17440 bytes > step budget 8192; generation not attempted |
| Handwritten | Handwritten---string1.json | estimated minimum document size 10002 bytes > step budget 8192; generation not attempted |

## Resource-limit files

| dataset | file | detail |
|---|---|---|
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
| Github_hard | Github_hard---o21284.json | out of memory during compile |
| Github_hard | Github_hard---o21303.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o21327.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o21379.json | grammar exceeds node budget of 100000 |
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
| Github_hard | Github_hard---o90895.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o90924.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o90925.json | grammar exceeds node budget of 100000 |
| Github_hard | Github_hard---o91595.json | grammar exceeds node budget of 100000 |
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
| Github_trivial | Github_trivial---o47165.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o18949.json | anyOf has more than 64 branches |
| Github_ultra | Github_ultra---o19343.json | grammar exceeds node budget of 100000 |
| Github_ultra | Github_ultra---o21161.json | out of memory during compile |
| Github_ultra | Github_ultra---o21173.json | out of memory during compile |
| Github_ultra | Github_ultra---o21185.json | out of memory during compile |
| Github_ultra | Github_ultra---o21189.json | out of memory during compile |
| Github_ultra | Github_ultra---o21193.json | out of memory during compile |
| Github_ultra | Github_ultra---o21219.json | out of memory during compile |
| Github_ultra | Github_ultra---o21220.json | out of memory during compile |
| Github_ultra | Github_ultra---o21304.json | out of memory during compile |
| Github_ultra | Github_ultra---o21307.json | out of memory during compile |
| Github_ultra | Github_ultra---o21308.json | out of memory during compile |
| Github_ultra | Github_ultra---o21328.json | grammar exceeds node budget of 100000 |
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
