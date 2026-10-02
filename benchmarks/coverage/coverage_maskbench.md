# Coverage report - JSONSchemaBench MaskBench snapshot (maskbench/data/)

- corpus: https://github.com/guidance-ai/jsonschemabench revision `ba103c73756198dd9b149ddc7db7867da7a077f6` (`maskbench/data/`), sha256 `9021735d444e27cab2b4988ad5ee908bbbae55b9fb5921e0bc3f9a3f62ba85fa`
- engine commit: `c1cc2cb315c885ec8b4cd926d1a9b51cde9ad1a2` (bolorgir 0.1.0, ABI 1), profile `canonical-v1`
- limits: mode=adaptive, memory_limit_bytes=268435456, cache_limit_bytes=67108864, session_limit_bytes=8388608, schema_limit_bytes=1048576, max_depth=64, max_threads_per_state=64, work_limit_ops=0
- tokenizer: synthetic-byte-fallback @ - (257 tokens)
- date: 2026-09-27T06:36:48Z

First blocker per file; a refusal is never coverage.

## Outcome buckets

| bucket | files |
|---|---|
| compiled | 624 |
| invalid_schema | 2993 |
| unsupported_feature | 7688 |
| resource_limit | 1 |
| total | 11306 |

## Invalid-schema subclasses

| subclass | files |
|---|---|
| missing additionalProperties: false | 2205 |
| missing required | 613 |
| missing type | 155 |
| other | 20 |

## Unsupported-feature keyword histogram (first blocker)

| keyword | files |
|---|---|
| definitions | 2329 |
| draft-04 $schema | 1776 |
| id | 703 |
| allOf | 508 |
| anyOf | 440 |
| $id | 417 |
| self | 399 |
| oneOf | 133 |
| draft-07 $schema | 114 |
| draft-06 $schema | 88 |
| name | 75 |
| patternProperties | 66 |
| esriDocumentation | 41 |
| _uniqueItems | 40 |
| version | 35 |
| javaType | 30 |
| comment | 29 |
| minProperties | 27 |
| pattern | 26 |
| _format | 25 |
| dependencies | 23 |
| links | 20 |
| example | 19 |
| claroline | 12 |
| format | 12 |
| references | 12 |
| copyright | 11 |
| not | 10 |
| $license | 9 |
| _comment | 9 |
| additonalProperties | 9 |
| minimum | 9 |
| targetType | 8 |
| $schema-location | 7 |
| additionalItems | 7 |
| javaName | 7 |
| longDescription | 7 |
| $async | 6 |
| if | 6 |
| propertiesOrder | 6 |
| $default | 5 |
| _copyright | 5 |
| claroIds | 5 |
| endpoint | 5 |
| _id | 4 |
| content-type | 4 |
| multipleOf | 4 |
| order | 4 |
| plural_title | 4 |
| $version | 3 |
| @context | 3 |
| collection | 3 |
| long-description | 3 |
| type | 3 |
| @type | 2 |
| additinalProperties | 2 |
| additionalproperties | 2 |
| alias | 2 |
| assertionType | 2 |
| comments | 2 |
| decription | 2 |
| descrption | 2 |
| generators | 2 |
| jupyter.lab.setting-icon-class | 2 |
| maxProperties | 2 |
| maximum | 2 |
| message | 2 |
| optional | 2 |
| preproccess | 2 |
| request | 2 |
| requires | 2 |
| resource_id | 2 |
| $file | 1 |
| $schemaODC | 1 |
| $target_version | 1 |
| (boolean schema) | 1 |
| AccessToken | 1 |
| Action | 1 |
| ConnectionDescription | 1 |
| CustomerVehicleServiceHistory | 1 |
| DomainDataSchemas | 1 |
| FuelInventoryReport | 1 |
| HypertyCommunicationDataObjectInstance | 1 |
| HypertyInterceptorConfiguration | 1 |
| LanguageLearning | 1 |
| LogisticsDashboard | 1 |
| Request001 | 1 |
| Response001 | 1 |
| RuntimeHypertyCapabilities | 1 |
| SyncObjectChild | 1 |
| Transaction | 1 |
| UserHypertyConfigurationData | 1 |
| WeatherUpdates | 1 |
| __comment_source | 1 |
| __tags | 1 |
| __version | 1 |
| _description | 1 |
| access | 1 |
| additionalFields | 1 |
| additionalProperties: | 1 |
| additionalPropeties | 1 |
| additional_properties | 1 |
| authorization | 1 |
| b2share | 1 |
| bolts | 1 |
| categories | 1 |
| category | 1 |
| configFile | 1 |
| contains | 1 |
| content | 1 |
| cropType | 1 |
| data | 1 |
| datos | 1 |
| decsription | 1 |
| define | 1 |
| defs | 1 |
| dependentSchemas | 1 |
| discriminator | 1 |
| encoding | 1 |
| errorMessage | 1 |
| faIcon | 1 |
| file | 1 |
| form | 1 |
| getParameters | 1 |
| idField | 1 |
| ids | 1 |
| image | 1 |
| inline | 1 |
| intl_string | 1 |
| intl_uri | 1 |
| key_fields | 1 |
| limited | 1 |
| line_endings | 1 |
| link | 1 |
| markdownDescription | 1 |
| memory_units | 1 |
| modules | 1 |
| notes | 1 |
| openscad | 1 |
| options | 1 |
| other_attributes | 1 |
| port | 1 |
| propertyNames | 1 |
| readOnly | 1 |
| readonly_attributes | 1 |
| recommended | 1 |
| require | 1 |
| required: | 1 |
| schemaType | 1 |
| team | 1 |
| typeName | 1 |
| uiSchema | 1 |
| unique_attributes | 1 |
| version_info | 1 |
| visible | 1 |
| x-otm-library | 1 |
| x-prompt | 1 |
| x-user-analytics | 1 |
| xml | 1 |

## Per-dataset counts

| dataset | total | compiled | invalid_schema | unsupported_feature | resource_limit | valid ex. | invalid ex. |
|---|---|---|---|---|---|---|---|
| BFCL | 1043 | 586 | 92 | 365 | 0 | 1043 | 0 |
| Github_easy | 1943 | 17 | 275 | 1651 | 0 | 2641 | 4611 |
| Github_hard | 1240 | 0 | 141 | 1099 | 0 | 1493 | 3405 |
| Github_medium | 1976 | 2 | 377 | 1597 | 0 | 3091 | 6119 |
| Github_trivial | 444 | 18 | 39 | 387 | 0 | 460 | 771 |
| Github_ultra | 164 | 0 | 4 | 160 | 0 | 160 | 302 |
| Glaiveai2K | 1707 | 1 | 1693 | 13 | 0 | 1634 | 1104 |
| Handwritten | 147 | 0 | 0 | 147 | 0 | 14 | 39 |
| JME | 100 | 0 | 89 | 11 | 0 | 100 | 0 |
| JsonSchemaStore | 492 | 0 | 2 | 489 | 1 | 679 | 1405 |
| Kubernetes | 1064 | 0 | 281 | 783 | 0 | 1680 | 2908 |
| MCPspec | 45 | 0 | 0 | 45 | 0 | 44 | 44 |
| Snowplow | 403 | 0 | 0 | 403 | 0 | 670 | 1730 |
| Synthesized | 413 | 0 | 0 | 413 | 0 | 109 | 279 |
| WashingtonPost | 125 | 0 | 0 | 125 | 0 | 146 | 330 |

Examples: 13964 valid, 23047 invalid.

## Semantic coverage (split metrics)

Each metric has its own base; compile coverage alone is not exact
coverage. Semantic checks run on a byte-level synthetic tokenizer
(token id = byte value, eos=256) with the `canonical-v1` profile;
instances are encoded with python/bolorgir/serializer.py serialize_value and cross-checked
with jsonschema==4.26.0; generation budgets: 8192 steps and 10s wall time per schema. The byte context is recycled every 150 checks (plus a retry on RESOURCE_LIMIT): 4 recycles this run - engine retains generation memory in a Context across session/grammar free (R5 finding).

| metric | n | base | % |
|---|---|---|---|
| compile coverage | 624 | 11306 | 5.52% |
| explicit refusals (engine declines the schema) | 10682 | 11306 | 94.48% |
| session success | 624 | 624 | 100.00% |
| generation success | 624 | 624 | 100.00% |
| generation success (of attempted: compiled minus oversize skips) | 624 | 624 | 100.00% |
| generated-doc schema validity (jsonschema) | 624 | 624 | 100.00% |
| valid-instance acceptance | 633 | 633 | 100.00% |
| invalid-instance rejection | 77 | 77 | 100.00% |
| serialization incompatibility | 0 | 710 | 0.00% |

Detail counters:

| counter | n |
|---|---|
| compile mismatch (byte-context compile differs) | 0 |
| session create failed | 0 |
| generation: dead end | 0 |
| generation: step/time budget exhausted | 0 |
| generation: oversize skip (min document size estimate > step budget; not attempted) | 0 |
| generation: engine error | 0 |
| generated doc not JSON (finish accepted it) | 0 |
| generated doc rejected by jsonschema | 0 |
| generated doc validator error (no verdict) | 0 |
| schema unusable for jsonschema (validator_error) | 0 |
| valid instances: rejected | 0 |
| valid instances: engine error | 0 |
| valid instances: corpus label vs jsonschema disagree | 0 |
| invalid instances: accepted (jsonschema agrees invalid) | 0 |
| invalid instances: accepted (jsonschema says valid) | 0 |
| invalid instances: engine error | 0 |

## Resource-limit files

| dataset | file | detail |
|---|---|---|
| JsonSchemaStore | JsonSchemaStore---cloudformation.schema.json | schema exceeds schema_limit_bytes |
