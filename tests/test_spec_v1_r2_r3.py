"""spec-v1 value-filter and branch-vote regression tests (2026-09-22).

Value filter: enum/const values are filtered by ALL applicable sibling assertions and
applicators (properties/required/additionalProperties, propertyNames,
min/maxProperties, dependencies/dependentRequired/dependentSchemas,
items/prefixItems/additionalItems, contains, uniqueItems, min/maxItems) and
`const` next to `enum` intersects by value. A value failing a sibling leaves
the language; an empty remainder is UNSATISFIABLE_CONSTRAINT; a combination
the compile-time validator cannot decide exactly is UNSUPPORTED_FEATURE,
never a silent weakening.

Branch votes: oneOf/allOf branches vote on VALUES, not serializations (JSON Schema
Core 4.2.2, 10.2.2): equal const objects under oneOf leave the language
(UNSATISFIABLE when nothing else survives); allOf object branches with
contradictory declared-key orders merge by value (first-appearance order,
semantics-spec-v1 4.3) or refuse exactly.

Every row is checked by mask-guided feeding (fill_mask + accept + can_end +
finish) and by direct accept, in lazy/adaptive x fast path on/off, and
against the independent jsonschema validator. The module is skipped until
the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

jsonschema = pytest.importorskip("jsonschema", reason="independent oracle")

D7 = "http://json-schema.org/draft-07/schema"
D2020 = "https://json-schema.org/draft/2020-12/schema"

VALIDATORS = {D7: jsonschema.Draft7Validator,
              D2020: jsonschema.Draft202012Validator}


def oracle_valid(schema, doc: str) -> bool:
    """Independent verdict in the schema's own dialect."""
    cls = VALIDATORS.get(schema.get("$schema"), jsonschema.Draft202012Validator)
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
    """Mask-guided feed: every document byte must be mask-allowed, then
    can_end/finish must confirm the completion."""
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
    """The engine verdict (both feed styles) must equal the independent
    validator's; a refusal is exact only when the value is invalid or the
    combination is undecidable."""
    oracle = oracle_valid(schema, doc)
    try:
        g = _compile(ctx, schema)
    except zg.CoreFailure as exc:
        assert not oracle, (
            f"valid value refused under {schema}: {exc}")
        return "refusal"
    try:
        masked = feed_masked(ctx, g, doc)
        direct = accept_direct(ctx, g, doc)
    finally:
        zg.grammar_release(g)
    assert masked == direct == oracle, (
        f"{doc!r} under {schema}: masked={masked} direct={direct} "
        f"oracle={oracle}")
    return "fed"


def expect_refusal(ctx, schema, status: str):
    with pytest.raises(zg.CoreFailure) as ei:
        _compile(ctx, schema)
    assert status in str(ei.value)


# ---------------------------------------------------------------------------
# R2: const/enum x sibling object keywords
# ---------------------------------------------------------------------------

R2_OBJECT_MATRIX = [
    # (schema, valid docs, invalid docs) - invalid rows either feed-reject
    # or refuse the compile when no value survives.
    ({"const": {"a": 1}, "required": ["a"]}, ['{"a":1}'], []),
    ({"const": {"a": 1}, "required": ["b"]}, [], ['{"a":1}']),
    ({"const": {"a": 1, "b": 2}, "required": ["b", "a"]},
     ['{"a":1,"b":2}'], []),
    ({"const": {"a": "x"}, "properties": {"a": {"type": "string"}}},
     ['{"a":"x"}'], []),
    ({"const": {"a": "x"}, "properties": {"a": {"type": "integer"}}},
     [], ['{"a":"x"}']),
    ({"const": {"a": 1}, "properties": {"a": {}},
      "additionalProperties": False}, ['{"a":1}'], []),
    ({"const": {"a": 1, "b": 2}, "properties": {"a": {}},
      "additionalProperties": False}, [], ['{"a":1,"b":2}']),
    ({"const": {"a": 1}, "additionalProperties": False}, [], ['{"a":1}']),
    ({"const": {"a": 1}, "minProperties": 1}, ['{"a":1}'], []),
    ({"const": {"a": 1}, "minProperties": 2}, [], ['{"a":1}']),
    ({"const": {"a": 1, "b": 2}, "maxProperties": 1}, [],
     ['{"a":1,"b":2}']),
    ({"const": {"a": 1}, "propertyNames": {"enum": ["a"]}}, ['{"a":1}'], []),
    ({"const": {"a": 1}, "propertyNames": {"enum": ["b"]}}, [], ['{"a":1}']),
    ({"const": {"a": 1}, "propertyNames": {"maxLength": 1}}, ['{"a":1}'], []),
    ({"const": {"ab": 1}, "propertyNames": {"maxLength": 1}}, [],
     ['{"ab":1}']),
    ({"const": {}, "propertyNames": False}, ["{}"], []),
    ({"const": {"a": 1}, "propertyNames": False}, [], ['{"a":1}']),
    ({"const": {"x1": 1}, "patternProperties": {"^x": {"type": "integer"}}},
     ['{"x1":1}'], []),
    ({"const": {"x1": "s"}, "patternProperties": {"^x": {"type": "integer"}}},
     [], ['{"x1":"s"}']),
    ({"const": {"a": 1, "b": 2}, "dependentRequired": {"a": ["b"]}},
     ['{"a":1,"b":2}'], []),
    ({"const": {"a": 1}, "dependentRequired": {"a": ["b"]}}, [],
     ['{"a":1}']),
    ({"const": {"a": 1},
      "dependentSchemas": {"a": {"required": ["b"]}}}, [], ['{"a":1}']),
    ({"const": {"a": 1, "b": 2},
      "dependentSchemas": {"a": {"properties": {"b": {"const": 2}}}}},
     ['{"a":1,"b":2}'], []),
    # draft-07 `dependencies`: array and schema forms.
    ({"$schema": D7, "const": {"a": 1, "b": 2}, "dependencies": {"a": ["b"]}},
     ['{"a":1,"b":2}'], []),
    ({"$schema": D7, "const": {"a": 1}, "dependencies": {"a": ["b"]}},
     [], ['{"a":1}']),
    ({"$schema": D7, "const": {"a": 1},
      "dependencies": {"a": {"required": ["b"]}}}, [], ['{"a":1}']),
]


@pytest.mark.parametrize("schema,ok,bad", R2_OBJECT_MATRIX)
def test_r2_const_object_siblings(ctx, schema, ok, bad):
    for doc in ok:
        assert check_value(ctx, schema, doc) == "fed"
    for doc in bad:
        check_value(ctx, schema, doc)  # fed-rejected or exactly refused


# ---------------------------------------------------------------------------
# R2: const/enum x sibling array keywords
# ---------------------------------------------------------------------------

R2_ARRAY_MATRIX = [
    ({"const": [1, 2], "minItems": 2}, ["[1,2]"], []),
    ({"const": [1], "minItems": 2}, [], ["[1]"]),
    ({"const": [1, 2], "maxItems": 1}, [], ["[1,2]"]),
    ({"const": [1, 2], "uniqueItems": True}, ["[1,2]"], []),
    ({"const": [1, 1], "uniqueItems": True}, [], ["[1,1]"]),
    # uniqueItems equality is value-based: 1 and 1.0 are duplicates.
    ({"const": [1, 1.0], "uniqueItems": True}, [], ["[1,1.0]"]),
    ({"const": [1, 2], "contains": {"const": 2}}, ["[1,2]"], []),
    ({"const": [1, 2], "contains": {"const": 3}}, [], ["[1,2]"]),
    ({"const": [1, 2, 2], "contains": {"const": 2}, "maxContains": 1},
     [], ["[1,2,2]"]),
    ({"const": [1, 2, 2], "contains": {"const": 2}, "minContains": 2},
     ["[1,2,2]"], []),
    ({"enum": [[1, "x"]], "items": {"type": "integer"}}, [], ['[1,"x"]']),
    ({"enum": [[1, 2]], "items": {"type": "integer"}}, ["[1,2]"], []),
    ({"const": [1, "x"],
      "prefixItems": [{"type": "integer"}, {"type": "string"}]},
     ['[1,"x"]'], []),
    ({"const": [1, "x"],
      "prefixItems": [{"type": "integer"}, {"type": "integer"}]},
     [], ['[1,"x"]']),
    ({"$schema": D7, "const": [1, 2, 3], "items": [{}],
      "additionalItems": False}, [], ["[1,2,3]"]),
    ({"$schema": D7, "const": [1], "items": [{}], "additionalItems": False},
     ["[1]"], []),
    # Nested containers: the sibling assertions recurse.
    ({"const": {"list": [1, 1]},
      "properties": {"list": {"uniqueItems": True}}}, [],
     ['{"list":[1,1]}']),
    ({"const": [{"a": 1}], "items": {"required": ["a"]}}, ['[{"a":1}]'], []),
    ({"const": [{"a": 1}], "items": {"required": ["b"]}}, [], ['[{"a":1}]']),
]


@pytest.mark.parametrize("schema,ok,bad", R2_ARRAY_MATRIX)
def test_r2_const_array_siblings(ctx, schema, ok, bad):
    for doc in ok:
        assert check_value(ctx, schema, doc) == "fed"
    for doc in bad:
        check_value(ctx, schema, doc)


def test_r2_enum_partial_filtering(ctx):
    """A filtered enum keeps the surviving values and only them."""
    schema = {"enum": [[1], [1, 2], [1, 2, 3]], "minItems": 2}
    assert check_value(ctx, schema, "[1]") == "fed"     # oracle: invalid
    assert check_value(ctx, schema, "[1,2]") == "fed"   # oracle: valid
    assert check_value(ctx, schema, "[1,2,3]") == "fed"  # oracle: invalid
    schema = {"enum": [{"a": 1}, {"b": 2}], "required": ["b"]}
    assert check_value(ctx, schema, '{"a":1}') == "fed"  # invalid
    assert check_value(ctx, schema, '{"b":2}') == "fed"  # valid


def test_r2_empty_remainder_is_unsatisfiable(ctx):
    expect_refusal(ctx, {"const": {"a": 1}, "required": ["b"]},
                   "UNSATISFIABLE_CONSTRAINT")
    expect_refusal(ctx, {"const": [1, 1], "uniqueItems": True},
                   "UNSATISFIABLE_CONSTRAINT")
    expect_refusal(ctx, {"enum": [{"a": 1}, [1]], "required": ["b"],
                         "minItems": 5}, "UNSATISFIABLE_CONSTRAINT")


def test_r2_undecidable_sibling_refuses(ctx):
    # A $ref inside a sibling applicator has no static evaluation.
    expect_refusal(ctx,
                   {"$defs": {"p": {"type": "integer"}},
                    "const": {"a": 1},
                    "properties": {"a": {"$ref": "#/$defs/p"}}},
                   "UNSUPPORTED_FEATURE")
    # Multiple patternProperties keep the compiled-path refusal.
    expect_refusal(ctx,
                   {"const": {"a": 1},
                    "patternProperties": {"x": {}, "y": {}}},
                   "UNSUPPORTED_FEATURE")
    # unevaluatedProperties nested in a sibling applicator.
    expect_refusal(ctx,
                   {"const": {"a": {"b": 1}},
                    "properties": {"a": {"unevaluatedProperties": False}}},
                   "UNSUPPORTED_FEATURE")


def test_r2_const_next_to_enum_intersects(ctx):
    schema = {"enum": [1, 2], "const": 1}
    assert check_value(ctx, schema, "1") == "fed"
    assert check_value(ctx, schema, "2") == "fed"
    # Object members compare key-order-insensitively; the surviving member
    # keeps its schema spelling order in the byte language (4.3).
    schema = {"enum": [{"b": 2, "a": 1}, 5], "const": {"a": 1, "b": 2}}
    assert check_value(ctx, schema, '{"b":2,"a":1}') == "fed"
    assert check_value(ctx, schema, "5") == "fed"
    # An empty intersection is UNSATISFIABLE, not INVALID_SCHEMA.
    expect_refusal(ctx, {"enum": [1, 2], "const": 3},
                   "UNSATISFIABLE_CONSTRAINT")


# ---------------------------------------------------------------------------
# R3: oneOf/allOf over object values
# ---------------------------------------------------------------------------

def test_r3_oneof_equal_const_objects_unsatisfiable(ctx):
    schema = {"oneOf": [{"const": {"a": 1, "b": 2}},
                        {"const": {"b": 2, "a": 1}}]}
    expect_refusal(ctx, schema, "UNSATISFIABLE_CONSTRAINT")


def test_r3_oneof_equal_const_objects_nested(ctx):
    # The equal values hide inside arrays: still the same value.
    schema = {"oneOf": [{"const": [{"a": 1, "b": 2}]},
                        {"const": [{"b": 2, "a": 1}]}]}
    expect_refusal(ctx, schema, "UNSATISFIABLE_CONSTRAINT")


def test_r3_oneof_duplicate_drops_out(ctx):
    schema = {"oneOf": [{"const": {"a": 1, "b": 2}},
                        {"const": {"b": 2, "a": 1}},
                        {"const": {"c": 3}}]}
    # The duplicated value is accepted by two branches: invalid both ways.
    assert check_value(ctx, schema, '{"a":1,"b":2}') == "fed"
    assert check_value(ctx, schema, '{"b":2,"a":1}') == "fed"
    # The surviving branch accepts its value.
    assert check_value(ctx, schema, '{"c":3}') == "fed"


def test_r3_oneof_duplicate_with_rejecting_branch(ctx):
    # A non-constant branch that rejects the duplicated value keeps the
    # filtering exact.
    schema = {"oneOf": [{"const": {"a": 1, "b": 2}},
                        {"const": {"b": 2, "a": 1}},
                        {"type": "object", "properties": {"c": {}},
                         "required": ["c"]}]}
    assert check_value(ctx, schema, '{"a":1,"b":2}') == "fed"
    assert check_value(ctx, schema, '{"c":3}') == "fed"
    assert check_value(ctx, schema, '{"c":3,"a":1}') == "fed"


def test_r3_oneof_duplicate_accepted_by_plain_branch_refuses(ctx):
    # {"type":"object"} also accepts the duplicated value: the overlap
    # cannot be subtracted exactly.
    expect_refusal(ctx,
                   {"oneOf": [{"const": {"a": 1, "b": 2}},
                              {"const": {"b": 2, "a": 1}},
                              {"type": "object"}]},
                   "UNSUPPORTED_FEATURE")


def test_r3_oneof_scalars_still_counted(ctx):
    # Scalar duplicates were already value-correct through the comb.
    schema = {"oneOf": [{"const": 1}, {"const": 1.0}, {"const": 2}]}
    assert check_value(ctx, schema, "1") == "fed"    # two branches: invalid
    assert check_value(ctx, schema, "1.0") == "fed"  # same value: invalid
    assert check_value(ctx, schema, "2") == "fed"    # valid


def test_r3_allof_equal_const_objects_intersect(ctx):
    # Both branches accept the same value: the intersection is that value
    # (in its first-spelling serialization).
    schema = {"allOf": [{"const": {"a": 1, "b": 2}},
                        {"const": {"b": 2, "a": 1}}]}
    assert check_value(ctx, schema, '{"a":1,"b":2}') == "fed"
    schema = {"allOf": [{"const": {"a": 1, "b": 2}}, {"const": {"c": 3}}]}
    expect_refusal(ctx, schema, "UNSATISFIABLE_CONSTRAINT")


def test_r3_allof_pure_branch_filtered_by_plain_branch(ctx):
    schema = {"allOf": [{"enum": [{"a": 1, "b": 2}, {"c": 3}]},
                        {"type": "object", "required": ["c"]}]}
    assert check_value(ctx, schema, '{"c":3}') == "fed"
    assert check_value(ctx, schema, '{"a":1,"b":2}') == "fed"


def test_r3_allof_contradictory_key_orders_merge(ctx):
    schema = {"allOf": [
        {"type": "object", "properties": {"a": {}, "b": {}},
         "required": ["a", "b"]},
        {"type": "object", "properties": {"b": {}, "a": {}},
         "required": ["a", "b"]}]}
    # The value is valid; the merged object fixes the first-appearance
    # order a,b (semantics-spec-v1 4.3).
    assert check_value(ctx, schema, '{"a":1,"b":2}') == "fed"


def test_r3_allof_merged_rejects_invalid_values(ctx):
    schema = {"allOf": [
        {"type": "object", "properties": {"a": {"type": "integer"},
                                          "b": {}},
         "required": ["a", "b"]},
        {"type": "object", "properties": {"b": {}, "a": {}},
         "required": ["a", "b"]}]}
    g = _compile(ctx, schema)
    try:
        assert feed_masked(ctx, g, '{"a":1,"b":2}')
        # a must be an integer (branch-1 value constraint survives the
        # merge); a missing required key fails as well.
        assert not feed_masked(ctx, g, '{"a":"x","b":2}')
        assert not feed_masked(ctx, g, '{"a":1}')
    finally:
        zg.grammar_release(g)


def test_r3_allof_consistent_orders_keep_comb(ctx):
    schema = {"allOf": [
        {"type": "object", "properties": {"a": {}, "b": {}},
         "required": ["a"]},
        {"type": "object", "properties": {"a": {}, "b": {}, "c": {}},
         "required": ["b"]}]}
    # No order conflict: an undeclared-here key may interleave freely.
    g = _compile(ctx, schema)
    try:
        assert feed_masked(ctx, g, '{"a":1,"b":2}')
        assert feed_masked(ctx, g, '{"a":1,"z":9,"b":2}')
        assert not feed_masked(ctx, g, '{"b":2,"a":1}')
        assert not feed_masked(ctx, g, '{"a":1}')
    finally:
        zg.grammar_release(g)


def test_r3_allof_closed_object_conflict_refuses(ctx):
    expect_refusal(ctx,
                   {"allOf": [
                       {"type": "object",
                        "properties": {"a": {}, "b": {}},
                        "additionalProperties": False},
                       {"type": "object",
                        "properties": {"b": {}, "a": {}}}]},
                   "UNSUPPORTED_FEATURE")


def test_r3_allof_nested_conflict_flattens_and_merges(ctx):
    schema = {"allOf": [
        {"type": "object", "properties": {"a": {}, "b": {}},
         "required": ["a", "b"]},
        {"allOf": [
            {"type": "object", "properties": {"b": {}, "a": {}},
             "required": ["b"]}]}]}
    g = _compile(ctx, schema)
    try:
        assert feed_masked(ctx, g, '{"a":1,"b":2}')
        assert not feed_masked(ctx, g, '{"a":1}')
    finally:
        zg.grammar_release(g)


def test_r3_allof_conflict_with_sibling_core(ctx):
    # The typed core and an allOf branch impose contradictory orders:
    # compileTypedSpec conjoins through the same analysis.
    schema = {"type": "object",
              "properties": {"a": {}, "b": {}}, "required": ["a", "b"],
              "allOf": [{"type": "object",
                         "properties": {"b": {}, "a": {}},
                         "required": ["b"]}]}
    g = _compile(ctx, schema)
    try:
        assert feed_masked(ctx, g, '{"a":1,"b":2}')
        assert not feed_masked(ctx, g, '{"b":2,"a":1}')
    finally:
        zg.grammar_release(g)
