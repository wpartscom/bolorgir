# Oracle harness (spec-v1 functional-correctness gate)

P0 infrastructure (ROADMAP section 5, P0 item 4; P1 item 7; A8.3). Compares
bolorgir accept/reject verdicts against two independent oracles:

- the pinned **JSON Schema Test Suite** snapshot
  (`external/JSON-Schema-Test-Suite/`, release 23.2.0, commit
  `95fe6ca20a90a019f4538f3670b6dd49d91dfdee`; gitignored, see `pin.json`);
- the pinned **`jsonschema`** package (`4.26.0`,
  `pip install 'jsonschema==4.26.0'`).

No engine behavior change. The suite snapshot and run reports
(`results/`) are not committed; `pin.json` is the committed pin record.
Re-fetch the snapshot with `./fetch_suite.sh` (checks out `tests/` and,
for the P5 external-$ref registry of the spec-v1 profile, `remotes/` -
keyed as `http://localhost:1234/<relpath>` and handed to the engine as an
immutable registry snapshot, ADR-0006 D5 / ADR-0008; without `remotes/`
the refRemote rows stay compile-refusals as before).

## Running

```sh
zig build                                  # core library (zig-out/lib)
pip install 'jsonschema==4.26.0'           # pinned validator
tests/oracle/fetch_suite.sh                # only if external/ is missing

python3 tests/oracle/run_oracle.py                  # full draft2020-12 run
python3 tests/oracle/run_oracle.py --include 'type.json' --strict
python3 tests/oracle/run_oracle.py --optional       # include optional/
python3 -m pytest tests/oracle/ -q                  # gate: no MISMATCH rows
```

The runner writes a JSON report (header: suite commit, engine commit,
profile, validator versions, date; one row per test: suite file, case,
schema-compiled?, engine verdict, suite expectation, validator verdict,
mismatch class) to `results/oracle-<timestamp>.json` and prints a markdown
summary table (`--md PATH` to save it).

## How a case is evaluated

1. The schema is compiled through the C ABI (ctypes backend, byte-level
   tokenizer: 256 single-byte tokens + EOS/pad, profile `canonical-v1` -
   `--profile` is the spec-v1 hook). The dialect is selected by the root
   `$schema` (docs/dialect-matrix.md section 1): the harness injects the
   identifier of the suite directory's draft into root object schemas that
   do not carry one and records `dialect_source` per row (`harness`,
   `schema`, or `default` - the profile default covers draft2020-12
   schemas and boolean root schemas).
2. A compile refusal (`UNSUPPORTED_FEATURE`, `INVALID_SCHEMA`, ...) marks
   every test of the case **SKIPPED** with the refusal status and a JSON
   pointer. canonical-v1 reports `schema_offset` instead of a pointer; the
   harness derives the RFC 6901 pointer from the offset
   (`pointer_source: derived_from_schema_offset`). Every refusal also gets
   a `classification` against the documented refusal list of
   docs/supported_features.md section 1a (`by_design_unsatisfiable`,
   `by_design_invalid_schema`, `budget_guard`, `documented_refusal`,
   `needs_review` - the last one is never silent and always listed).
3. A compiled instance is serialized and fed byte-exactly: mask bit check +
   `accept` per byte, then `can_end` and `finish`. The serializer depends
   on the profile:
   - `canonical-v1`: canonical compact (whitespace-free UTF-8, key order
     preserved - the `--compact` convention), numeric lexemes normalized
     through binary64. This path is frozen.
   - `spec-v1`: the value-preserving serializer
     `python/bolorgir/serializer.py` (`serialize_value`) - schema-driven
     key order per semantics-spec-v1 section 4.3 and verbatim numeric
     lexemes (the suite file is re-parsed with parse_int/parse_float
     hooks). An instance the serializer cannot express (lone surrogates,
     duplicate keys, ...) is bucketed as **SERIALIZATION_INCOMPATIBLE**,
     a protocol artifact apart from verdicts.
4. The verdict is compared with the suite expectation and the pinned
   validator: **PASS**, **MISMATCH** (class `engine_vs_both` /
   `fed_path_vs_suite` / `validator_vs_suite` / `*_unverified`),
   **ENGINE_ERROR** (runtime resource limit), or **KNOWN_GAP** when the row
   matches a documented entry in `known_gaps.json`.

## Exact-decimal layer (R6)

Numeric boundary keywords (`minimum`/`maximum`/`multipleOf`/
`exclusiveMinimum`/`exclusiveMaximum`) are checked by the engine in exact
decimal arithmetic. The harness never presents a binary64 reserialization
as an independent decimal check:

- under spec-v1 both the schema bytes and the fed document keep the
  suite's numeric lexemes verbatim, so the engine check is genuinely
  decimal (`decimal.engine_check` per row);
- rows whose boundary or instance lexemes are not exactly representable
  in binary64 are marked `decimal.sensitive` (the `jsonschema` validator
  oracle works on floats there) and counted in
  `summary.decimal_sensitive_rows`;
- for the simple root-level shape (numeric instance vs root bounds) an
  independent Decimal oracle verdict is recorded as `decimal.oracle` and
  compared with the suite expectation
  (`summary.decimal_oracle_disagreements`; draft-04 boolean
  `exclusiveMinimum`/`exclusiveMaximum` modifiers handled per the
  dialect-matrix normalization).

Under canonical-v1 the fed document is re-serialized from the parsed
value, so original number lexemes (`1e2`) are normalized (`100.0`) - the
fed document is the canonical compact form of the same value, same as
MaskBench `--compact`.

## Known gaps

`known_gaps.json` lists documented canonical-v1 gaps (suite file + case +
optional test index and mismatch class + reason). Current entries are the
enum/const literal normalization and integer-lexeme typing gaps (DESIGN
section 1.6 vs JSON Schema instance equality, core 4.2.2) - value equality
is a spec-v1 item (ROADMAP 4.2, P1). `test_oracle.py` fails on a new
MISMATCH and on a stale gap entry (a fixed gap must be removed from the
list).

## Growth path (P1 item 7)

The semantic tests grow with every supported combination by adding data,
not code: drafts are the `DRAFTS` rows in `run_oracle.py` (all five matrix
dialects - draft4, draft6, draft7, draft2019-09, draft2020-12 - are wired
since R6; `--drafts` selects them), new profiles go through `--profile`,
and newly supported keywords simply move rows from SKIPPED to PASS.
Refusals keep their pointers; a refusal is never counted as coverage.

## Dialect runs (R6)

```sh
python3 tests/oracle/run_oracle.py --profile spec-v1 --drafts draft4 \
    --json results/oracle-r6-draft4-spec.json --md results/oracle-r6-draft4-spec.md
```

One report per dialect under `results/` (`oracle-r6-<draft>-spec.*`); a
multi-draft run (`--drafts draft4,draft6,...`) adds a per-draft outcome
table and prefixes per-file rows with the draft key. canonical-v1 is
defined for draft2020-12 only (a foreign `$schema` is INVALID_SCHEMA per
docs/supported_features.md section 1), so the dialect matrix runs use the
spec-v1 profile.
