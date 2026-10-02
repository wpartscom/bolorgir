"""spec-v1 profile end-to-end tests (ROADMAP rev-2 P6b).

Covers the P6b surface through the C ABI: content* keywords as
annotations, RFC 3986 relative-URI resolution with in-document $id
resources, $dynamicRef/$dynamicAnchor dynamic-scope resolution (2020-12),
$recursiveRef/$recursiveAnchor (2019-09) and the $ref sibling conjunction
of the two newest drafts. canonical-v1 behavior is pinned by
tests/test_parity_*; here only spec-v1 compiles are exercised.
The module is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

D7 = "http://json-schema.org/draft-07/schema"
V19 = "https://json-schema.org/draft/2019-09/schema"
V20 = "https://json-schema.org/draft/2020-12/schema"


@pytest.fixture
def byte_ctx(byte_tok):
    ctx = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    yield ctx
    ctx.destroy()


def _compile(ctx, schema, profile=b"spec-v1", registry=None):
    return zg.compile_schema(ctx, json.dumps(schema).encode(),
                             profile=profile, registry=registry)


def _accepts(ctx, grammar, doc: str) -> bool:
    s = zg.Session(ctx, grammar)
    try:
        for b in doc.encode():
            if s.accept(b) != zg.BLG_OK:
                return False
        return bool(s.can_end())
    finally:
        s.destroy()


def _check(ctx, schema, ok_docs, bad_docs, registry=None):
    g = _compile(ctx, schema, registry=registry)
    try:
        for d in ok_docs:
            assert _accepts(ctx, g, d), f"must accept {d!r} under {schema}"
        for d in bad_docs:
            assert not _accepts(ctx, g, d), f"must reject {d!r} under {schema}"
    finally:
        zg.grammar_release(g)


def _refusal(ctx, schema, status, profile=b"spec-v1", registry=None):
    with pytest.raises(zg.CoreFailure) as ei:
        _compile(ctx, schema, profile=profile, registry=registry)
    assert status in str(ei.value)
    return str(ei.value)


# ---------------------------------------------------------------------------
# content* keywords are annotations (2020-12 Validation section 8)
# ---------------------------------------------------------------------------

def test_content_keywords_are_annotations(byte_ctx):
    _check(byte_ctx, {"contentEncoding": "base64"},
           ['"aGVsbG8="', '"%not-base64"', "5"], [])
    _check(byte_ctx, {"contentMediaType": "application/json"},
           ['"{\\"a\\":1}"', '"not json"', "true"], [])
    # contentSchema next to them asserts nothing either.
    _check(byte_ctx,
           {"contentMediaType": "application/json", "contentEncoding": "base64",
            "contentSchema": {"type": "object", "required": ["foo"]}},
           ['"e30="', '"aXY"', "null"], [])
    # Alone, contentSchema is inert as well.
    _check(byte_ctx, {"contentSchema": {"type": "string"}}, ['"x"', "5", "{}"], [])
    # canonical-v1 stays frozen: contentSchema refuses there.
    _refusal(byte_ctx, {"contentSchema": {"type": "string"}}, "UNSUPPORTED_FEATURE",
             profile=b"canonical-v1")


# ---------------------------------------------------------------------------
# RFC 3986 relative-URI resolution and in-document $id resources
# ---------------------------------------------------------------------------

def test_relative_ref_into_indocument_resource(byte_ctx):
    # anchor.json 'same $anchor with different base uri': the anchor of the
    # child1 resource is the string schema, not child2's number.
    schema = {"$id": "http://localhost:1234/draft2020-12/foobar",
              "$defs": {"A": {"$id": "child1",
                              "allOf": [{"$id": "child2", "$anchor": "my_anchor",
                                         "type": "number"},
                                        {"$anchor": "my_anchor", "type": "string"}]}},
              "$ref": "child1#my_anchor"}
    _check(byte_ctx, schema, ['"s"'], ["1", "null"])


def test_absolute_uri_ref_into_indocument_resource(byte_ctx):
    schema = {"$ref": "http://localhost:1234/draft2020-12/bar#foo",
              "$defs": {"A": {"$id": "http://localhost:1234/draft2020-12/bar",
                              "$anchor": "foo", "type": "integer"}}}
    _check(byte_ctx, schema, ["3"], ['"s"', "3.5"])


def test_relative_id_resolution_against_nearest_base(byte_ctx):
    # ref.json '$id must be resolved against nearest parent': d.json
    # resolves against .../b/c.json, not against the root a.json.
    schema = {"$id": "http://example.com/a.json",
              "$defs": {"x": {"$id": "http://example.com/b/c.json",
                              "not": {"$defs": {"y": {"$id": "d.json",
                                                      "type": "number"}}}}},
              "allOf": [{"$ref": "http://example.com/b/d.json"}]}
    _check(byte_ctx, schema, ["3", "3.5"], ['"s"'])


def test_urn_base_self_ref_recursion(byte_ctx):
    schema = {"$id": "urn:uuid:deadbeef-1234-ffff-ffff-4321feebdaed",
              "minimum": 30,
              "properties": {"foo": {"$ref": "urn:uuid:deadbeef-1234-ffff-ffff-4321feebdaed"}}}
    _check(byte_ctx, schema,
           ["35", '{"foo":35}', '{"foo":{"foo":35}}'],
           ["25", '{"foo":25}', '{"foo":{"foo":25}}'])


def test_relative_ref_into_registry(byte_ctx):
    registry = {"documents": {"http://ex.com/dir/int.json": {"type": "integer"}}}
    _check(byte_ctx, {"$id": "http://ex.com/dir/main.json", "$ref": "int.json"},
           ["3"], ['"s"'], registry=json.dumps(registry).encode())
    _refusal(byte_ctx, {"$id": "http://ex.com/dir/main.json", "$ref": "missing.json"},
             "UNSUPPORTED_FEATURE", registry=json.dumps(registry).encode())


def test_registry_doc_nested_id_resource(byte_ctx):
    registry = {"documents": {
        "http://ex.com/outer.json": {
            "$defs": {"bar": {"$id": "http://ex.com/nested-id.json",
                              "type": "string"}},
            "$ref": "http://ex.com/nested-id.json"}}}
    _check(byte_ctx, {"$ref": "http://ex.com/outer.json"},
           ['"s"'], ["3"], registry=json.dumps(registry).encode())


# ---------------------------------------------------------------------------
# $dynamicRef / $dynamicAnchor (2020-12)
# ---------------------------------------------------------------------------

def test_dynamic_ref_same_resource_behaves_like_anchor_ref(byte_ctx):
    schema = {"$id": "https://ex.com/root", "type": "array",
              "items": {"$dynamicRef": "#items"},
              "$defs": {"foo": {"$dynamicAnchor": "items", "type": "string"}}}
    _check(byte_ctx, schema, ['["a","b"]', "[]"], ['["a",1]', "[1]"])


def test_dynamic_ref_typical_dynamic_resolution(byte_ctx):
    # The root's $dynamicAnchor overrides the bookending one of the 'list'
    # resource: the top-level array's items are strings.
    schema = {"$id": "https://ex.com/typical/root",
              "$ref": "list",
              "$defs": {"foo": {"$dynamicAnchor": "items", "type": "string"},
                        "list": {"$id": "list", "type": "array",
                                 "items": {"$dynamicRef": "#items"},
                                 "$defs": {"items": {"$dynamicAnchor": "items"}}}}}
    _check(byte_ctx, schema,
           ['["a","b"]', "[]"],
           ['["a",1]', "[1]", '"a"'])


def test_dynamic_ref_without_bookend_behaves_like_ref(byte_ctx):
    # No $dynamicAnchor 'items' in the list resource: the plain $anchor
    # target applies (an unconstrained schema), and the root's dynamic
    # anchor stays unused.
    schema = {"$id": "https://ex.com/no-bookend/root",
              "$ref": "list",
              "$defs": {"foo": {"$dynamicAnchor": "items", "type": "string"},
                        "list": {"$id": "list", "type": "array",
                                 "items": {"$dynamicRef": "#items"},
                                 "$defs": {"items": {"$anchor": "items"}}}}}
    _check(byte_ctx, schema, ['["a",1,true]', "[]"], ['"a"', "1"])


def test_dynamic_ref_pointer_fragment_is_plain_ref(byte_ctx):
    schema = {"$id": "https://ex.com/pointer/root",
              "$ref": "list",
              "$defs": {"foo": {"$dynamicAnchor": "items", "type": "string"},
                        "list": {"$id": "list", "type": "array",
                                 "items": {"$dynamicRef": "#/$defs/items"},
                                 "$defs": {"items": {"$dynamicAnchor": "items",
                                                     "type": "number"}}}}}
    _check(byte_ctx, schema, ['[1,2.5]', "[]"], ['["a"]', "1"])


def test_dynamic_ref_across_registry_document(byte_ctx):
    # strict-extendible: the main document's $dynamicAnchor governs the
    # $dynamicRef evaluated inside the registry document.
    registry = {"documents": {
        "http://ex.com/extendible.json": {
            "$id": "http://ex.com/extendible.json",
            "type": "object",
            "properties": {"elements": {"type": "array",
                                        "items": {"$dynamicRef": "#elements"}}},
            "required": ["elements"],
            "$defs": {"elements": {"$dynamicAnchor": "elements"}}}}}
    schema = {"$id": "http://ex.com/strict.json",
              "$ref": "extendible.json",
              "$defs": {"elements": {"$dynamicAnchor": "elements",
                                     "properties": {"a": True},
                                     "required": ["a"],
                                     "additionalProperties": False}}}
    reg = json.dumps(registry).encode()
    _check(byte_ctx, schema,
           ['{"elements":[]}', '{"elements":[{"a":1}]}',
            '{"elements":[{"a":1},{"a":"x"}]}'],
           ['{"elements":[{"b":1}]}', '{"elements":[{"a":1,"b":2}]}',
            "{}"],
           registry=reg)
    # Without the main-document override the bookend accepts anything.
    _check(byte_ctx, {"$ref": "http://ex.com/extendible.json"},
           ['{"elements":[{"b":1},[1]]}'], ["{}"],
           registry=reg)


def test_dynamic_ref_leaves_scope(byte_ctx):
    # The 'if' branch's scope is left by the time the 'then' branch runs:
    # the $dynamicRef resolves to the then-scope's anchor.
    schema = {"$id": "https://ex.com/leaving/main",
              "if": {"$id": "first_scope",
                     "$defs": {"thingy": {"$dynamicAnchor": "thingy",
                                          "type": "number"}}},
              "then": {"$id": "second_scope",
                       "$ref": "start",
                       "$defs": {"thingy": {"$dynamicAnchor": "thingy",
                                            "type": "null"}}},
              "$defs": {"start": {"$id": "start",
                                  "$dynamicRef": "inner_scope#thingy"},
                        "thingy": {"$id": "inner_scope",
                                   "$dynamicAnchor": "thingy",
                                   "type": "string"}}}
    _check(byte_ctx, schema, ["null"], ['"s"', "1"])


def test_dynamic_ref_sibling_conjunction(byte_ctx):
    # 2020-12 sibling rule applies to $dynamicRef as well.
    schema = {"$defs": {"n": {"$dynamicAnchor": "node", "type": "integer"}},
              "properties": {"x": {"$dynamicRef": "#node", "minimum": 3}}}
    _check(byte_ctx, schema, ['{"x":5}', "{}"], ['{"x":2}', '{"x":"s"}'])


def test_dynamic_ref_refusals(byte_ctx):
    _refusal(byte_ctx, {"items": {"$dynamicRef": 5}}, "INVALID_SCHEMA")
    msg = _refusal(byte_ctx, {"items": {"$dynamicRef": "#nope"}}, "INVALID_SCHEMA")
    assert " at /items" in msg
    _refusal(byte_ctx, {"items": {"$dynamicRef": "http://ex.com/missing.json#a"}},
             "UNSUPPORTED_FEATURE")
    # Next to unevaluated* the dynamic scope is not modelled (documented).
    _refusal(byte_ctx,
             {"$defs": {"n": {"$dynamicAnchor": "node", "type": "object"}},
              "$dynamicRef": "#node", "unevaluatedProperties": False},
             "UNSUPPORTED_FEATURE")
    # 2019-09 does not know $dynamicRef: ignored.
    _check(byte_ctx, {"$schema": V19, "$dynamicRef": "#x"}, ["1"], [])


# ---------------------------------------------------------------------------
# $recursiveRef / $recursiveAnchor (2019-09)
# ---------------------------------------------------------------------------

def test_recursive_ref_static(byte_ctx):
    # Without $recursiveAnchor the '#' target is the current resource root.
    schema = {"$schema": V19,
              "$defs": {"list": {"$id": "list", "type": "array",
                                 "items": {"$recursiveRef": "#"}}},
              "$ref": "list"}
    _check(byte_ctx, schema, ["[]", "[[]]", "[[[]]]"], ["[1]", '["a"]'])


def test_recursive_ref_dynamic_override(byte_ctx):
    # The outermost $recursiveAnchor resource of the dynamic scope wins:
    # the root (which additionally requires "name") overrides the tree
    # resource for the recursive items reference.
    schema = {"$schema": V19,
              "$id": "http://ex.com/root",
              "$recursiveAnchor": True,
              "$ref": "tree",
              "required": ["name"],
              "$defs": {"tree": {"$id": "tree",
                                 "$recursiveAnchor": True,
                                 "type": "object",
                                 "properties": {"children": {
                                     "type": "array",
                                     "items": {"$recursiveRef": "#"}}}}}}
    _check(byte_ctx, schema,
           ['{"name":"a"}', '{"name":"a","children":[{"name":"b"}]}',
            '{"name":"a","children":[]}'],
           ['{}', '{"children":[]}', '{"name":"a","children":[{}]}', '"s"'])

    # The same skeleton without the root $recursiveAnchor keeps the static
    # target (the tree resource): children items are plain trees.
    schema2 = {"$schema": V19,
               "$id": "http://ex.com/root2",
               "$ref": "tree",
               "$defs": {"tree": {"$id": "tree",
                                  "$recursiveAnchor": True,
                                  "type": "object",
                                  "properties": {"children": {
                                      "type": "array",
                                      "items": {"$recursiveRef": "#"}}}}}}
    _check(byte_ctx, schema2,
           ['{}', '{"children":[{}]}', '{"children":[{"children":[]}]}'],
           ['{"children":[5]}', '"s"', '{"children":{}}'])


def test_recursive_ref_forms(byte_ctx):
    _refusal(byte_ctx, {"$schema": V19, "$recursiveRef": "#/$defs/x"},
             "INVALID_SCHEMA")
    _refusal(byte_ctx, {"$schema": V19, "$recursiveRef": 5}, "INVALID_SCHEMA")
    # 2020-12 does not know $recursiveRef: ignored.
    _check(byte_ctx, {"$schema": V20, "$recursiveRef": "#"}, ["1"], [])
