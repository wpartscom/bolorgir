# Coverage report - JSONSchemaBench repository snapshot (data/)

- corpus: https://github.com/guidance-ai/jsonschemabench revision `ba103c73756198dd9b149ddc7db7867da7a077f6` (`data/`), sha256 `4c6c1d3947936f6060d4a0c0d937b1f54c41b2af2f44c09f664ab1819e97112e`
- engine commit: `c1cc2cb315c885ec8b4cd926d1a9b51cde9ad1a2` (bolorgir 0.1.0, ABI 1), profile `spec-v1`
- limits: mode=adaptive, memory_limit_bytes=268435456, cache_limit_bytes=67108864, session_limit_bytes=8388608, schema_limit_bytes=1048576, max_depth=64, max_threads_per_state=64, work_limit_ops=0
- tokenizer: synthetic-byte-fallback @ - (257 tokens)
- date: 2026-09-28T23:13:38Z

First blocker per file; a refusal is never coverage.

## Outcome buckets

| bucket | files |
|---|---|
| compiled | 9251 |
| invalid_schema | 6 |
| unsupported_feature | 151 |
| resource_limit | 143 |
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
| Github_hard | 1240 | 1158 | 4 | 31 | 41 | 0 | 0 |
| Github_medium | 1976 | 1946 | 0 | 18 | 12 | 0 | 0 |
| Github_trivial | 444 | 439 | 0 | 3 | 1 | 0 | 0 |
| Github_ultra | 164 | 116 | 0 | 17 | 31 | 0 | 0 |
| Glaiveai2K | 1707 | 1707 | 0 | 0 | 0 | 0 | 0 |
| JsonSchemaStore | 492 | 412 | 2 | 40 | 38 | 0 | 0 |
| Kubernetes | 1064 | 1045 | 0 | 0 | 19 | 0 | 0 |
| Snowplow | 403 | 400 | 0 | 3 | 0 | 0 | 0 |
| WashingtonPost | 125 | 94 | 0 | 31 | 0 | 0 | 0 |

Examples: 0 valid, 0 invalid.

## Semantic coverage (split metrics)

Each metric has its own base; compile coverage alone is not exact
coverage. Semantic checks run on a byte-level synthetic tokenizer
(token id = byte value, eos=256) with the `spec-v1` profile;
instances are encoded with python/bolorgir/serializer.py serialize_value and cross-checked
with jsonschema==4.26.0; generation budgets: 8192 steps and 10s wall time per schema. The byte context is recycled every 150 checks (plus a retry on RESOURCE_LIMIT): 93 recycles this run - engine retains generation memory in a Context across session/grammar free (R5 finding). Context destroy() failures: 0.

| metric | n | base | % |
|---|---|---|---|
| compile coverage | 9251 | 9558 | 96.79% |
| explicit refusals (engine declines the schema) | 307 | 9558 | 3.21% |
| session success | 9240 | 9251 | 99.88% |
| generation success | 8901 | 9251 | 96.22% |
| generation success (of attempted: compiled minus oversize skips) | 8901 | 9239 | 96.34% |
| generated-doc schema validity (jsonschema) | 8874 | 8901 | 99.70% |
| valid-instance acceptance | 0 | 0 | n/a |
| invalid-instance rejection | 0 | 0 | n/a |
| serialization incompatibility | 0 | 0 | n/a |

Detail counters:

| counter | n |
|---|---|
| compile mismatch (byte-context compile differs) | 3 |
| session create failed | 8 |
| generation: dead end | 11 |
| generation: step/time budget exhausted | 308 |
| generation: oversize skip (min document size estimate > step budget; not attempted) | 1 |
| generation: engine error | 19 |
| generated doc not JSON (finish accepted it) | 0 |
| generated doc rejected by jsonschema | 24 |
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
| generation_budget | 308 |
| generated_schema_invalid | 24 |
| generation_error | 19 |
| generation_dead_end | 11 |
| session_create_failed | 8 |
| compile_mismatch | 3 |

### generation_budget (308, first 200)

| dataset | file | detail |
|---|---|---|
| Github_easy | o13129.json | time budget 10s exhausted at step 96 |
| Github_easy | o13652.json | step budget 8192 exhausted |
| Github_easy | o15726.json | step budget 8192 exhausted |
| Github_easy | o21040.json | step budget 8192 exhausted |
| Github_easy | o2231.json | time budget 10s exhausted at step 2320 |
| Github_easy | o27844.json | step budget 8192 exhausted |
| Github_easy | o28609.json | step budget 8192 exhausted |
| Github_easy | o28614.json | step budget 8192 exhausted |
| Github_easy | o33825.json | step budget 8192 exhausted |
| Github_easy | o36461.json | time budget 10s exhausted at step 4096 |
| Github_easy | o36463.json | time budget 10s exhausted at step 4064 |
| Github_easy | o39084.json | time budget 10s exhausted at step 2176 |
| Github_easy | o40228.json | step budget 8192 exhausted |
| Github_easy | o40230.json | step budget 8192 exhausted |
| Github_easy | o41033.json | step budget 8192 exhausted |

### generated_schema_invalid (24)

| dataset | file | detail |
|---|---|---|
| Github_easy | o17940.json | jsonschema rejects the generated document: {"arch":"","clientId":"00000000-0000-0000-0000-000000000000","device":"","locale":"","os":"","osversion":"","seq":-0,"v" |
| Github_hard | o41192.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","skip-bids-validation":false,"task-id":"","echo-idx":"","n_cpus":-0,"mem_mb":-0,"anat |
| Github_hard | o41250.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{},"freesurfer_license":{}},"config":{"inputs":"","config":"","api_key":"", |
| Github_hard | o41251.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","skip-bids-validation":false,"task-id":"","echo-idx":"","anat-only":false,"error-on-a |
| Github_hard | o41252.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{}},"config":{"inputs":"","config":"","api_key":"","save_outputs":false,"fo |
| Github_hard | o41256.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{}},"config":{"inputs":"","config":"","api_key":"","ignore_aroma_denoising_ |
| Github_hard | o41261.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{}},"config":{"inputs":"","config":"","api_key":"","save_outputs":false,"fo |
| Github_hard | o41263.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","api_key":{},"freesurfer_license":{}},"config":{"inputs":"","config":"","api_key":"", |
| Github_hard | o41265.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","skip-bids-validation":false,"task-id":"","echo-idx":"","anat-only":false,"error-on-a |
| Github_hard | o41291.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","DWIName":"","AnatomyRegDOF":0.000000000000000000000000000000000000000000000000000000 |
| Github_hard | o41300.json | jsonschema rejects the generated document: {"config":{"config":"","inputs":"","save-on-error":false,"dry-run":false,"fMRIName":"rfMRI_REST1_RL","BiasCorrection":"N |
| Github_hard | o41363.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","intensity_images":{},"mask_image":{}},"config":{"inputs":"","config":"","intensity_i |
| Github_hard | o41364.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","intensity_images":{},"mask_image":{}},"config":{"inputs":"","config":"","intensity_i |
| Github_hard | o41366.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","brain_template":{},"brain_probability_mask":{},"anatomical_image":{}},"config":{"inp |
| Github_hard | o41367.json | jsonschema rejects the generated document: {"inputs":{"inputs":"","config":"","segmentation_priors":{},"anatomical_image":{},"t1_registration_template":{},"brain_t |

### generation_dead_end (11)

| dataset | file | detail |
|---|---|---|
| Github_easy | o44051.json | fill_mask dead end at step 32 |
| Github_easy | o90314.json | fill_mask dead end at step 3 |
| Github_hard | o14404.json | fill_mask dead end at step 15 |
| Github_hard | o2070.json | fill_mask dead end at step 33 |
| Github_hard | o21334.json | fill_mask dead end at step 102 |
| Github_hard | o44713.json | fill_mask dead end at step 136 |
| Github_hard | o47658.json | fill_mask dead end at step 105 |
| Github_hard | o63309.json | fill_mask dead end at step 40 |
| Github_hard | o72527.json | fill_mask dead end at step 62 |
| Github_hard | o78043.json | fill_mask dead end at step 590 |
| JsonSchemaStore | task.json | fill_mask dead end at step 2 |

### generation_error (19)

| dataset | file | detail |
|---|---|---|
| Github_hard | o18948.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Github_hard | o19225.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Github_hard | o41072.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Github_medium | o70035.json | error:INVALID_TOKEN: accept at step 19 |
| Github_ultra | o17462.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Github_ultra | o21378.json | error:RESOURCE_LIMIT: fill_mask at step 90 |
| Github_ultra | o358.json | error:INVALID_TOKEN: accept at step 31 |
| Github_ultra | o360.json | error:INVALID_TOKEN: accept at step 31 |
| Github_ultra | o46322.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Github_ultra | o57502.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Github_ultra | o76155.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Glaiveai2K | calculate_area_95058385.json | error:INVALID_TOKEN: accept at step 26 |
| JsonSchemaStore | datahub_ingestion_schema.json | error:RESOURCE_LIMIT: fill_mask at step 19 |
| Snowplow | sp_261_Normalized.json | error:RESOURCE_LIMIT: fill_mask at step 1 |
| Snowplow | sp_333_Normalized.json | error:RESOURCE_LIMIT: fill_mask at step 22 |

### compile_mismatch (3)

| dataset | file | detail |
|---|---|---|
| Github_hard | o21160.json | RESOURCE_LIMIT: blg_compile: RESOURCE_LIMIT (out of memory during compile) |
| Github_hard | o21215.json | RESOURCE_LIMIT: blg_compile: RESOURCE_LIMIT (out of memory during compile) |
| Github_hard | o21256.json | RESOURCE_LIMIT: blg_compile: RESOURCE_LIMIT (out of memory during compile) |

### session_create_failed (8)

| dataset | file | detail |
|---|---|---|
| Github_medium | o69248.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| Github_medium | o76785.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| Github_trivial | o35155.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| JsonSchemaStore | jsconfig.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| JsonSchemaStore | minecraft-recipe.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| JsonSchemaStore | openrewrite.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| JsonSchemaStore | saucectl.schema.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |
| JsonSchemaStore | ui5.yaml.json | RESOURCE_LIMIT: blg_session_create: RESOURCE_LIMIT (parser state init limit exceeded) |

## Oversize generation skips (1)

Static minimum-document-size estimate above the step budget;
generation not attempted (a documented skip, not a failure).

| dataset | file | detail |
|---|---|---|
| Github_easy | o12622.json | estimated minimum document size 17440 bytes > step budget 8192; generation not attempted |

## Resource-limit files

| dataset | file | detail |
|---|---|---|
| Github_easy | o58839.json | grammar exceeds node budget of 100000 |
| Github_hard | o13029.json | grammar exceeds node budget of 100000 |
| Github_hard | o13457.json | grammar exceeds node budget of 100000 |
| Github_hard | o19336.json | grammar exceeds node budget of 100000 |
| Github_hard | o21072.json | out of memory during compile |
| Github_hard | o21073.json | out of memory during compile |
| Github_hard | o21074.json | out of memory during compile |
| Github_hard | o21075.json | out of memory during compile |
| Github_hard | o21076.json | out of memory during compile |
| Github_hard | o21172.json | grammar exceeds node budget of 100000 |
| Github_hard | o21192.json | grammar exceeds node budget of 100000 |
| Github_hard | o21284.json | out of memory during compile |
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
| Github_ultra | o21161.json | out of memory during compile |
| Github_ultra | o21173.json | grammar exceeds node budget of 100000 |
| Github_ultra | o21193.json | grammar exceeds node budget of 100000 |
| Github_ultra | o21219.json | out of memory during compile |
| Github_ultra | o21220.json | out of memory during compile |
| Github_ultra | o21304.json | out of memory during compile |
| Github_ultra | o21307.json | out of memory during compile |
| Github_ultra | o21308.json | out of memory during compile |
| Github_ultra | o21312.json | out of memory during compile |
| Github_ultra | o21328.json | out of memory during compile |
| Github_ultra | o21375.json | out of memory during compile |
| Github_ultra | o21376.json | out of memory during compile |
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
| JsonSchemaStore | cargo-make.json | out of memory during compile |
| JsonSchemaStore | cityjson.min.schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | cloudformation.schema.json | schema exceeds schema_limit_bytes |
| JsonSchemaStore | codeship-steps.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | component_spec.json_schema.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | config.json | schema exceeds schema_limit_bytes |
| JsonSchemaStore | dart-test.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | detekt-1.22.0.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | dss-2.0.0.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | eslintrc.json | grammar exceeds node budget of 100000 |
| JsonSchemaStore | hayson-json-schema.json | out of memory during compile |
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
