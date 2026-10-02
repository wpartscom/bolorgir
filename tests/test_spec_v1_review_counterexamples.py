"""Engine regression tests for the retired 2026-09-20 review counterexamples.

Promotes the still-unique semantic checks of the retired review documents
(findings A2, A6, A7; repro script checks) to engine tests, each verified
against the independent jsonschema oracle in the schema's own dialect:

- A2: an `allOf` object branch's `additionalProperties:false` scopes to the
  keys declared in that same branch - merging `properties`/`required` with a
  single closed object would wrongly admit `{"a":1,"b":2}`.
- A6: value equality is structural and typed - `true` and `1` are different
  values, so `[true, 1]` does not violate `uniqueItems`.
- A6/A7: a validation keyword constrains only instances of its own type -
  `minLength` next to no `type` does not restrict numbers.
- A7: the `$ref` sibling rule is per-dialect - `maxLength` next to `$ref` is
  ignored in draft-07 and conjoined in 2020-12.

Every row is fed through the engine (direct accept and mask-guided feed) in
lazy/adaptive x fast path on/off; the engine verdict must equal the oracle's.
The module is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

jsonschema = pytest.importorskip("jsonschema", reason="independent oracle")

D7 = "http://json-schema.org/draft-07/schema"


def oracle_valid(schema, doc: str) -> bool:
    cls = (jsonschema.Draft7Validator
           if schema.get("$schema") == D7 else jsonschema.Draft202012Validator)
    return cls(schema).is_valid(json.loads(doc))


@pytest.fixture(params=[(zg.BLG_MODE_LAZY, zg.BLG_MASK_FAST_PATH_ON),
                        (zg.BLG_MODE_LAZY, zg.BLG_MASK_FAST_PATH_OFF),
                        (zg.BLG_MODE_ADAPTIVE, zg.BLG_MASK_FAST_PATH_ON),
                        (zg.BLG_MODE_ADAPTIVE, zg.BLG_MASK_FAST_PATH_OFF)],
                ids=["lazy+fast", "lazy", "adaptive+fast", "adaptive"])
def ctx(request, byte_tok):
    mode, fast = request.param
    c = zg.Context(byte_tok, mode, mask_fast_path=fast)
    yield c
    assert c.destroy() == zg.BLG_OK


def _compile(ctx, schema):
    return zg.compile_schema(ctx, json.dumps(schema).encode(),
                             profile=b"spec-v1")


def feed_masked(ctx, grammar, doc: str) -> bool:
    s = zg.Session(ctx, grammar)
    try:
        for b in doc.encode():
            try:
                allowed = s.fill_mask_ids()
            except zg.CoreFailure as exc:
                if exc.status == zg.BLG_ERR_DEAD_END:
                    return False
                raise
            if b not in allowed:
                return False
            if s.accept(b) != zg.BLG_OK:
                return False
        return bool(s.can_end()) and s.finish() == zg.BLG_OK
    finally:
        s.destroy()


def accept_direct(ctx, grammar, doc: str) -> bool:
    s = zg.Session(ctx, grammar)
    try:
        for b in doc.encode():
            if s.accept(b) != zg.BLG_OK:
                return False
        return bool(s.can_end()) and s.finish() == zg.BLG_OK
    finally:
        s.destroy()


def check_value(ctx, schema, doc: str):
    """Engine verdict (both feed styles) must equal the oracle's."""
    oracle = oracle_valid(schema, doc)
    g = _compile(ctx, schema)
    try:
        masked = feed_masked(ctx, g, doc)
        direct = accept_direct(ctx, g, doc)
    finally:
        zg.grammar_release(g)
    assert masked == direct == oracle, (
        f"{doc!r} under {schema}: masked={masked} direct={direct} "
        f"oracle={oracle}")


# ---------------------------------------------------------------------------
# A2: additionalProperties scopes to its own allOf branch
# ---------------------------------------------------------------------------

ALLOF_CLOSED_BRANCH = {"allOf": [
    {"type": "object", "properties": {"a": {"type": "integer"}},
     "required": ["a"], "additionalProperties": False},
    {"type": "object", "properties": {"b": {"type": "integer"}},
     "required": ["b"]},
]}


def test_a2_allof_closed_branch_rejects_merged_key(ctx):
    # The naive single-object merge (properties a,b; required a,b; one
    # additionalProperties:false) would accept this; exact scoping rejects
    # it because the first branch forbids `b`.
    check_value(ctx, ALLOF_CLOSED_BRANCH, '{"a":1,"b":2}')


def test_a2_allof_closed_branch_intersection_is_empty(ctx):
    # Branch 2 requires `b`, branch 1 forbids every undeclared key: no value
    # survives. Every probed document must be rejected.
    for doc in ('{"a":1}', '{"b":2}', '{"a":1,"b":2}'):
        check_value(ctx, ALLOF_CLOSED_BRANCH, doc)


# ---------------------------------------------------------------------------
# A6: value equality is structural and typed
# ---------------------------------------------------------------------------

def test_a6_unique_items_boolean_differs_from_number(ctx):
    schema = {"uniqueItems": True}
    check_value(ctx, schema, "[true,1]")
    check_value(ctx, schema, "[1,true]")
    check_value(ctx, schema, "[true,true]")


def test_a6_length_keyword_inapplicable_to_non_strings(ctx):
    # No `type` does not imply string: minLength ignores numbers, booleans,
    # arrays and objects.
    schema = {"minLength": 2}
    for doc in ('"ab"', "3", "true", "[1,2]", '{"a":1}'):
        check_value(ctx, schema, doc)
    check_value(ctx, schema, '"a"')


# ---------------------------------------------------------------------------
# A7: the $ref sibling rule is per-dialect
# ---------------------------------------------------------------------------

def test_a7_ref_sibling_maxlength_ignored_in_draft7(ctx):
    schema = {"$schema": D7,
              "definitions": {"s": {"type": "string"}},
              "$ref": "#/definitions/s", "maxLength": 1}
    check_value(ctx, schema, '"ab"')
    check_value(ctx, schema, '"a"')


def test_a7_ref_sibling_maxlength_applies_in_2020_12(ctx):
    schema = {"$defs": {"s": {"type": "string"}},
              "$ref": "#/$defs/s", "maxLength": 1}
    check_value(ctx, schema, '"ab"')
    check_value(ctx, schema, '"a"')
