"""spec-v1 P5 end-to-end tests (ROADMAP rev-2 P5, ADR-0008): recursion.

Covers recursive local `$ref` under bounded unrolling through the C ABI:
linked lists and trees at, below and beyond the documented unroll limit
(REF_UNROLL_CAP = 8 nested expansions along any path), mutual recursion,
recursion through anchors and combinators, the mask-visible behavior at
the limit (the recursive position behaves as `false`: a banned optional
key, a dropped arm - never a hung state), unproductive cycles as the
empty-language root refusal, and URI-fragment percent-decoding.
canonical-v1 is frozen (it keeps refusing cycles). The module is skipped
until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

# ADR-0008: a ref cycle is expanded at most this many times beyond the
# initial expansion along any document path.
UNROLL = 8

LINKED_LIST = {
    "type": "object",
    "properties": {"v": {"type": "integer"}, "next": {"$ref": "#"}},
    "required": ["v"],
    "additionalProperties": False,
}

TREE = {
    "type": "object",
    "properties": {
        "val": {"type": "string"},
        "left": {"$ref": "#"},
        "right": {"$ref": "#"},
    },
    "additionalProperties": False,
}


@pytest.fixture
def byte_ctx(byte_tok):
    ctx = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    yield ctx
    ctx.destroy()


def _compile(ctx, schema, profile=b"spec-v1"):
    return zg.compile_schema(ctx, json.dumps(schema).encode(), profile=profile)


def _accepts(ctx, grammar, doc: str) -> bool:
    s = zg.Session(ctx, grammar)
    try:
        for b in doc.encode():
            if s.accept(b) != zg.BLG_OK:
                return False
        return bool(s.can_end())
    finally:
        s.destroy()


def _check(ctx, schema, ok_docs, bad_docs, profile=b"spec-v1"):
    g = _compile(ctx, schema, profile=profile)
    try:
        for d in ok_docs:
            assert _accepts(ctx, g, d), f"must accept {d!r} under {schema}"
        for d in bad_docs:
            assert not _accepts(ctx, g, d), f"must reject {d!r} under {schema}"
    finally:
        zg.grammar_release(g)


def _refusal(ctx, schema, status, profile=b"spec-v1"):
    with pytest.raises(zg.CoreFailure) as ei:
        _compile(ctx, schema, profile=profile)
    assert status in str(ei.value)
    return str(ei.value)


def _list_doc(depth: int) -> str:
    """Linked list with `depth` nested 'next' links."""
    doc = '{"v":%d}' % depth
    for i in range(depth - 1, -1, -1):
        doc = '{"v":%d,"next":%s}' % (i, doc)
    return doc


def _tree_doc(depth: int) -> str:
    """Left spine of TREE with `depth` nested 'left' links."""
    if depth == 0:
        return "{}"
    return '{"left":%s}' % _tree_doc(depth - 1)


# ---------------------------------------------------------------------------
# Recursive linked lists / trees within the limit
# ---------------------------------------------------------------------------

def test_recursive_linked_list(byte_ctx):
    ok = ['{"v":1}', _list_doc(1), _list_doc(3), _list_doc(UNROLL)]
    bad = ['{"v":1,"next":null}', '{"v":1,"next":{"v":"x"}}',
           '{"v":1,"next":{}}', _list_doc(UNROLL + 1), _list_doc(UNROLL + 4)]
    _check(byte_ctx, LINKED_LIST, ok, bad)


def test_recursive_tree(byte_ctx):
    ok = ["{}", '{"val":"a"}', _tree_doc(1), _tree_doc(4), _tree_doc(UNROLL),
          '{"left":{"right":{"left":{}}}}']
    bad = [_tree_doc(UNROLL + 1), '{"left":null}', '{"left":{"val":1}}']
    _check(byte_ctx, TREE, ok, bad)


def test_mutual_recursion_defs(byte_ctx):
    schema = {
        "$defs": {
            "a": {"type": "object",
                  "properties": {"b": {"$ref": "#/$defs/b"}},
                  "additionalProperties": False},
            "b": {"type": "object",
                  "properties": {"a": {"$ref": "#/$defs/a"}},
                  "additionalProperties": False},
        },
        "$ref": "#/$defs/a",
    }
    deep = "{}"
    for _ in range(4):
        deep = '{"b":{"a":%s}}' % deep
    too_deep = '{"b":%s}' % deep
    for _ in range(4):
        too_deep = '{"a":{"b":%s}}' % too_deep
    _check(byte_ctx, schema, ["{}", '{"b":{}}', deep], [too_deep, '{"b":{"a":null}}'])


def test_recursion_through_anchor(byte_ctx):
    schema = {"$anchor": "node", "type": "array", "items": {"$ref": "#node"}}
    ok = ["[]", "[[]]", "[[[],[]],[]]"]
    bad = ["[1]", "[[],1]"]
    _check(byte_ctx, schema, ok, bad)


def test_recursion_with_array_items(byte_ctx):
    # Nested arrays: each level is an array of the same schema.
    schema = {"type": "array", "items": {"$ref": "#"}, "maxItems": 2}
    ok = ["[]", "[[]]", "[[],[[]]]", "[[[]]]"]
    bad = ["[[[[]]]]" if UNROLL < 3 else None, "[1]", "[[],[],[]]"]
    _check(byte_ctx, schema, ok, [d for d in bad if d])


def test_recursion_under_combinator(byte_ctx):
    # anyOf with a recursive arm: objects or arrays of them.
    schema = {
        "anyOf": [
            {"type": "string"},
            {"type": "array", "items": {"$ref": "#"}},
        ]
    }
    _check(byte_ctx, schema,
           ['"x"', '[]', '["a",[]]', '[["a"],"b"]'],
           ['[1]', '[[1]]'])


def test_recursion_exact_limit_boundary(byte_ctx):
    # The boundary is exact: UNROLL nested expansions accepted,
    # UNROLL+1 rejected, for both shapes.
    _check(byte_ctx, LINKED_LIST, [_list_doc(UNROLL)], [_list_doc(UNROLL + 1)])
    _check(byte_ctx, TREE, [_tree_doc(UNROLL)], [_tree_doc(UNROLL + 1)])


# ---------------------------------------------------------------------------
# Limit behavior is mask-visible (ADR-0005: no dead prefixes)
# ---------------------------------------------------------------------------

def _first_token_mask(ctx, grammar, prefix: bytes, tok) -> set:
    """Token ids admitted by the mask after feeding `prefix`."""
    s = zg.Session(ctx, grammar)
    try:
        for b in prefix:
            assert s.accept(b) == zg.BLG_OK
        return s.fill_mask_ids()
    finally:
        s.destroy()


def test_limit_mask_excludes_recursive_key(byte_ctx, byte_tok):
    """At the unroll bottom the optional recursive property is a banned
    key: right after the last value of the deepest object the mask
    excludes the ',' that would open another key (and accept() rejects
    it), while '}' stays admitted (mask-visible limit, ADR-0005: no live
    prefix dead-ends)."""
    g = _compile(byte_ctx, LINKED_LIST)
    try:
        doc = _list_doc(UNROLL).encode()
        cut = doc[: doc.rindex(b'"v":%d' % UNROLL) + 5]
        s = zg.Session(byte_ctx, g)
        try:
            for b in cut:
                assert s.accept(b) == zg.BLG_OK
            allowed = s.fill_mask_ids()
            assert ord(',') not in allowed
            assert ord('}') in allowed
            assert s.accept(ord(',')) != zg.BLG_OK
        finally:
            s.destroy()
    finally:
        zg.grammar_release(g)


def test_limit_mask_allows_shallower_levels(byte_ctx, byte_tok):
    """One level above the bottom the recursive key is still admitted and
    feeds to completion."""
    g = _compile(byte_ctx, LINKED_LIST)
    try:
        doc = _list_doc(UNROLL).encode()
        cut = doc[: doc.rindex(b'"v":%d' % (UNROLL - 1)) + 5] + b',"next":'
        s = zg.Session(byte_ctx, g)
        try:
            for b in cut:
                assert s.accept(b) == zg.BLG_OK, f"prefix {cut!r} must stay live"
        finally:
            s.destroy()
    finally:
        zg.grammar_release(g)


def test_recursive_mask_every_step_completable(byte_ctx, byte_tok):
    """ADR-0005 D6.2: every token the mask admits participates in a
    completion; feeding only admitted tokens reaches a finished document
    at several nesting depths."""
    g = _compile(byte_ctx, LINKED_LIST)
    try:
        for depth in (0, 1, 3, UNROLL):
            doc = _list_doc(depth).encode()
            s = zg.Session(byte_ctx, g)
            try:
                for i, b in enumerate(doc):
                    assert b in s.fill_mask_ids(), \
                        f"mask must admit the next byte {chr(b)!r} at {i} of {doc!r}"
                    assert s.accept(b) == zg.BLG_OK
                assert s.can_end()
            finally:
                s.destroy()
    finally:
        zg.grammar_release(g)


# ---------------------------------------------------------------------------
# Unproductive recursion and other refusals
# ---------------------------------------------------------------------------

def test_unproductive_cycles_refuse(byte_ctx):
    # A $ref cycle with no base case is the empty language (ADR-0006 D1):
    # recognized at the root, UNSATISFIABLE_CONSTRAINT, never compiled.
    _refusal(byte_ctx, {"$ref": "#"}, "UNSATISFIABLE")
    _refusal(byte_ctx,
             {"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}},
              "$ref": "#/$defs/a"}, "UNSATISFIABLE")
    # A required recursive property can never be discharged.
    _refusal(byte_ctx,
             {"type": "object", "properties": {"next": {"$ref": "#"}},
              "required": ["next"], "additionalProperties": False},
             "UNSATISFIABLE")


def test_canonical_v1_keeps_refusing_cycles(byte_ctx):
    _refusal(byte_ctx,
             {"$defs": {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}},
              "$ref": "#/$defs/a"}, "INVALID_SCHEMA", profile=b"canonical-v1")
    _refusal(byte_ctx,
             {"type": "object",
              "properties": {"next": {"$ref": "#"}},
              "required": ["next"],
              "additionalProperties": False},
             "INVALID_SCHEMA", profile=b"canonical-v1")


# ---------------------------------------------------------------------------
# URI-fragment percent-decoding (RFC 6901 section 6)
# ---------------------------------------------------------------------------

def test_ref_percent_decoding(byte_ctx):
    _check(byte_ctx,
           {"$defs": {"percent%field": {"type": "integer"}},
            "$ref": "#/$defs/percent%25field"},
           ["5"], ['"s"'])
    _check(byte_ctx,
           {"$defs": {"a b": {"type": "string"}},
            "$ref": "#/$defs/a%20b"},
           ['"x"'], ["1"])
    # Decoding happens before '~' unescaping: %7E decodes to '~', which
    # is then NOT a pointer escape (decode order per RFC 6901).
    _check(byte_ctx,
           {"$defs": {"x~y": {"type": "boolean"}},
            "$ref": "#/$defs/x~0y"},
           ["true"], ["1"])
    _refusal(byte_ctx,
             {"$defs": {"a": {"type": "string"}}, "$ref": "#/$defs/a%2"},
             "INVALID_SCHEMA")
    _refusal(byte_ctx,
             {"$defs": {"a": {"type": "string"}}, "$ref": "#/$defs/a%zz"},
             "INVALID_SCHEMA")
