"""The reference validator of the generation passes and the outcome rules.

tests/generation_passes.py validates generated documents with jsonschema
over exact numbers and ECMA-262 `pattern` semantics; tests/semantic_outcomes.py
classifies passes and aggregates them per schema. Both are shared by the
generative test and the coverage harness, so their rules are pinned here.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

pytest.importorskip("jsonschema")
pytest.importorskip("regress")
pytest.importorskip("numpy")

import semantic_outcomes as so  # noqa: E402
from exact_numbers import exact_is_valid  # noqa: E402

gp = pytest.importorskip("generation_passes", reason="core library not built")

D4 = "http://json-schema.org/draft-04/schema#"


def _ref(schema):
    v, err = gp.make_reference_validator(json.dumps(schema))
    assert v is not None, err
    return v


@pytest.mark.parametrize("doc,ok", [("1", True), ("1.0", False), ("1e2", False), ("-0", True)])
def test_draft04_integer_is_lexical(doc, ok):
    assert _ref({"$schema": D4, "type": "integer"}).validate(doc.encode())[1] is ok


@pytest.mark.parametrize("doc,ok", [("1", True), ("1.0", True), ("1e2", True), ("1.5", False)])
def test_later_drafts_integer_is_by_value(doc, ok):
    assert _ref({"type": "integer"}).validate(doc.encode())[1] is ok


def test_nested_schema_keyword_keeps_exact_numbers():
    # jsonschema switches to the stock class of a subschema's own
    # `$schema`; the reference must stay exact and use that dialect's rule.
    v = _ref({"properties": {
        "n": {"$schema": "http://json-schema.org/draft-07/schema#",
              "type": "number", "maximum": 2},
        "i": {"$schema": D4, "type": "integer"}}})
    assert v.validate(b'{"n":1.5}')[1] is True
    assert v.validate(b'{"n":2.5}')[1] is False
    assert v.validate(b'{"n":1e400}')[1] is False
    assert v.validate(b'{"i":3}')[1] is True
    assert v.validate(b'{"i":3.0}')[1] is False


def test_numbers_are_exact():
    assert exact_is_valid({"maximum": 1}, "1.0000000000000000000001") is False
    assert exact_is_valid({"type": "number"}, "1e400000") is True
    assert exact_is_valid({"multipleOf": 0.1}, "0.3") is True


def test_pattern_uses_ecma_semantics():
    # ECMA-262 \d is [0-9]; Python's re \d also matches other Unicode digits.
    v = _ref({"type": "string", "pattern": "^\\d$"})
    assert v.validate(b'"7"')[1] is True
    assert v.validate('"٣"'.encode())[1] is False


def test_not_json_is_reported():
    json_ok, verdict, _ = _ref({}).validate(b"{")
    assert not json_ok and verdict is None
    assert so.validation_outcome(json_ok, verdict) == "invalid_document"


@pytest.mark.parametrize("outcomes,want", [
    (["completed_valid", "invalid_document", "engine_defect"], "invalid_document"),
    (["completed_valid", "engine_defect", "validator_error"], "engine_defect"),
    (["completed_valid", "validator_error", "engine_unavailable"], "validator_error"),
    (["completed_valid", "engine_unavailable"], "engine_unavailable"),
    (["skipped:oversize", "skipped:schema_budget"], "skipped"),
    (["incomplete:step_limit", "skipped:schema_budget"], "incomplete"),
    (["incomplete:time_limit", "completed_valid"], "generatable"),
])
def test_schema_aggregation(outcomes, want):
    assert so.aggregate_schema_outcome(outcomes) == want


def test_resource_limit_is_availability():
    assert so.classify_engine_failure("session_create", "BLG_ERR_RESOURCE_LIMIT") == "engine_unavailable"
    assert so.classify_engine_failure("fill_mask", "BLG_ERR_CANCELLED") == "engine_unavailable"
    assert so.classify_engine_failure("fill_mask", "BLG_ERR_DEAD_END") == "engine_defect"
    assert so.classify_engine_failure("accept", "BLG_ERR_INVALID_TOKEN") == "engine_defect"
