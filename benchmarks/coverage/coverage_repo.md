# Coverage report - JSONSchemaBench repository snapshot (data/)

- corpus: https://github.com/guidance-ai/jsonschemabench revision `ba103c73756198dd9b149ddc7db7867da7a077f6` (`data/`), sha256 `4c6c1d3947936f6060d4a0c0d937b1f54c41b2af2f44c09f664ab1819e97112e`
- engine commit: `c1cc2cb315c885ec8b4cd926d1a9b51cde9ad1a2` (bolorgir 0.1.0, ABI 1), profile `canonical-v1`
- limits: mode=adaptive, memory_limit_bytes=268435456, cache_limit_bytes=67108864, session_limit_bytes=8388608, schema_limit_bytes=1048576, max_depth=64, max_threads_per_state=64, work_limit_ops=0
- tokenizer: synthetic-byte-fallback @ - (257 tokens)
- date: 2026-09-27T06:36:42Z

First blocker per file; a refusal is never coverage.

## Outcome buckets

| bucket | files |
|---|---|
| compiled | 38 |
| invalid_schema | 2814 |
| unsupported_feature | 6703 |
| resource_limit | 3 |
| total | 9558 |

## Invalid-schema subclasses

| subclass | files |
|---|---|
| missing additionalProperties: false | 2120 |
| missing required | 609 |
| missing type | 70 |
| other | 15 |

## Unsupported-feature keyword histogram (first blocker)

| keyword | files |
|---|---|
| definitions | 2279 |
| draft-04 $schema | 1776 |
| id | 703 |
| $id | 414 |
| self | 399 |
| draft-07 $schema | 113 |
| oneOf | 98 |
| draft-06 $schema | 88 |
| name | 75 |
| anyOf | 74 |
| patternProperties | 51 |
| allOf | 44 |
| esriDocumentation | 41 |
| _uniqueItems | 40 |
| version | 35 |
| javaType | 30 |
| minProperties | 27 |
| _format | 25 |
| pattern | 24 |
| dependencies | 23 |
| links | 20 |
| example | 19 |
| claroline | 12 |
| format | 12 |
| references | 12 |
| copyright | 11 |
| _comment | 9 |
| additonalProperties | 9 |
| minimum | 9 |
| $license | 8 |
| targetType | 8 |
| $schema-location | 7 |
| additionalItems | 7 |
| javaName | 7 |
| longDescription | 7 |
| $async | 6 |
| propertiesOrder | 6 |
| $default | 5 |
| _copyright | 5 |
| claroIds | 5 |
| endpoint | 5 |
| not | 5 |
| _id | 4 |
| content-type | 4 |
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
| if | 2 |
| jupyter.lab.setting-icon-class | 2 |
| maxProperties | 2 |
| maximum | 2 |
| message | 2 |
| multipleOf | 2 |
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
| DomainDataSchemas | 1 |
| HypertyCommunicationDataObjectInstance | 1 |
| HypertyInterceptorConfiguration | 1 |
| Request001 | 1 |
| Response001 | 1 |
| RuntimeHypertyCapabilities | 1 |
| SyncObjectChild | 1 |
| Transaction | 1 |
| UserHypertyConfigurationData | 1 |
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
| content | 1 |
| data | 1 |
| datos | 1 |
| decsription | 1 |
| define | 1 |
| defs | 1 |
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
| Github_easy | 1943 | 17 | 275 | 1651 | 0 | 0 | 0 |
| Github_hard | 1240 | 0 | 142 | 1098 | 0 | 0 | 0 |
| Github_medium | 1976 | 2 | 377 | 1597 | 0 | 0 | 0 |
| Github_trivial | 444 | 18 | 39 | 387 | 0 | 0 | 0 |
| Github_ultra | 164 | 0 | 4 | 160 | 0 | 0 | 0 |
| Glaiveai2K | 1707 | 1 | 1693 | 13 | 0 | 0 | 0 |
| JsonSchemaStore | 492 | 0 | 3 | 487 | 2 | 0 | 0 |
| Kubernetes | 1064 | 0 | 281 | 782 | 1 | 0 | 0 |
| Snowplow | 403 | 0 | 0 | 403 | 0 | 0 | 0 |
| WashingtonPost | 125 | 0 | 0 | 125 | 0 | 0 | 0 |

Examples: 0 valid, 0 invalid.

## Semantic coverage (split metrics)

Each metric has its own base; compile coverage alone is not exact
coverage. Semantic checks run on a byte-level synthetic tokenizer
(token id = byte value, eos=256) with the `canonical-v1` profile;
instances are encoded with python/bolorgir/serializer.py serialize_value and cross-checked
with jsonschema==4.26.0; generation budgets: 8192 steps and 10s wall time per schema. The byte context is recycled every 150 checks (plus a retry on RESOURCE_LIMIT): 0 recycles this run - engine retains generation memory in a Context across session/grammar free (R5 finding).

| metric | n | base | % |
|---|---|---|---|
| compile coverage | 38 | 9558 | 0.40% |
| explicit refusals (engine declines the schema) | 9520 | 9558 | 99.60% |
| session success | 38 | 38 | 100.00% |
| generation success | 38 | 38 | 100.00% |
| generation success (of attempted: compiled minus oversize skips) | 38 | 38 | 100.00% |
| generated-doc schema validity (jsonschema) | 38 | 38 | 100.00% |
| valid-instance acceptance | 0 | 0 | n/a |
| invalid-instance rejection | 0 | 0 | n/a |
| serialization incompatibility | 0 | 0 | n/a |

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
| JsonSchemaStore | cloudformation.schema.json | schema exceeds schema_limit_bytes |
| JsonSchemaStore | config.json | schema exceeds schema_limit_bytes |
| Kubernetes | kb_1161_Normalized.json | schema exceeds schema_limit_bytes |
