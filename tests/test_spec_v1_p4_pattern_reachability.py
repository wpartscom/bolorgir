"""ADR-0005 completion reachability for the spec-v1 P4 regex `pattern`.

Same contract as the P2/P3 modules: for every schema the engine now
compiles, every prefix of a valid document must reach a state from which
completion is still possible - feeding the prefix never dead-ends, and
after the prefix the state either accepts (can_end) or admits at least
one continuation token in the mask, in particular the document's own
next byte. The pattern DFA runs inside the string machine (search-mode
wrapping keeps a live DFA state for every content byte; a mismatch is
refused exactly at the closing quote), which is exactly where an
alive-but-uncompletable state would hide.

The module is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")


@pytest.fixture
def byte_ctx(byte_tok):
    ctx = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    yield ctx
    ctx.destroy()


CASES = [
    # Anchored and unanchored patterns, plain and with bounds.
    ({"type": "string", "pattern": "^a+$"}, ['"a"', '"aaaa"']),
    ({"type": "string", "pattern": "a.c"}, ['"xxayczz"']),
    ({"type": "string", "pattern": "^\\d{3}-\\d{4}$"}, ['"123-4567"']),
    ({"type": "string", "pattern": "^a+$", "minLength": 2, "maxLength": 3},
     ['"aa"', '"aaa"']),
    # Escapes and multi-byte codepoints on the accepted path.
    ({"type": "string", "pattern": "^a\\nb$"}, ['"a\\nb"']),
    ({"type": "string", "pattern": "^é.$"}, ['"éx"']),
    ({"type": "string", "pattern": "^[\\u0400-\\u04FF]+$"}, ['"\u041F\u0440\u0438\u0432\u0435\u0442"']),
    ({"type": "string", "pattern": "^..$"}, ['"😀x"']),
    # Pattern inside containers and combinators.
    ({"type": "object",
      "properties": {"k": {"type": "string", "pattern": "^a+$"}},
      "required": ["k"], "additionalProperties": False},
     ['{"k":"aaa"}']),
    ({"type": "array", "items": {"type": "string", "pattern": "^\\d+$"}},
     ['["1","23"]', "[]"]),
    ({"oneOf": [{"type": "string", "pattern": "^a+$"},
                {"type": "string", "pattern": "^b+$"}]},
     ['"aaa"', '"bbb"']),
    ({"allOf": [{"type": "string", "pattern": "a"},
                {"type": "string", "pattern": "b"}]},
     ['"xaybz"']),
    # Full-regex patternProperties.
    ({"type": "object",
      "patternProperties": {"^x": {"type": "integer"}},
      "additionalProperties": {"type": "string"}},
     ['{"x1":1}', '{"xy":1,"other":"s"}', '{}']),
    ({"patternProperties": {"^a\\nb$": {"type": "integer"}}},
     ['{"a\\nb":1}', "{}"]),
]


def test_p4_completion_reachability(byte_ctx):
    for schema, docs in CASES:
        g = zg.compile_schema(byte_ctx, json.dumps(schema).encode(),
                              profile=b"spec-v1")
        try:
            for doc in docs:
                data = doc.encode()
                s = zg.Session(byte_ctx, g)
                try:
                    for i, b in enumerate(data):
                        st = s.accept(b)
                        assert st == zg.BLG_OK, (
                            f"{schema} / {doc!r}: byte {i} dead-ends a live prefix")
                        if i + 1 < len(data):
                            nxt = data[i + 1]
                            ids = s.fill_mask_ids()
                            assert s.can_end() or ids, (
                                f"{schema} / {doc!r}: state after {data[:i+1]!r} "
                                f"has no completion")
                            assert nxt in ids, (
                                f"{schema} / {doc!r}: next byte {bytes([nxt])!r} "
                                f"not in mask after {data[:i+1]!r}")
                    assert s.can_end(), f"{schema} must accept {doc!r}"
                finally:
                    s.destroy()
        finally:
            zg.grammar_release(g)


def test_p4_rejected_documents_dead_end(byte_ctx):
    # Documents outside the language must hit a dead thread at or before
    # their last byte (never a late can_end surprise): a pattern mismatch
    # is refused exactly at the closing quote of the string.
    REJECTS = [
        ({"type": "string", "pattern": "^a+$"}, ['""', '"ab"', '"ba"']),
        ({"type": "string", "pattern": "a.c"}, ['"ac"', '"ab"']),
        ({"type": "string", "pattern": "^a+$", "minLength": 2, "maxLength": 3},
         ['"a"', '"aaaa"']),
        ({"type": "string", "pattern": "^a.b$"}, ['"a\\nb"']),
        ({"oneOf": [{"type": "string", "pattern": "^a+$"},
                    {"type": "string", "pattern": "^b+$"}]},
         ['"ab"', '""']),
        ({"allOf": [{"type": "string", "pattern": "a"},
                    {"type": "string", "pattern": "b"}]},
         ['"ax"', '"xy"']),
        ({"type": "object",
          "patternProperties": {"^x": {"type": "integer"}},
          "additionalProperties": {"type": "string"}},
         ['{"x1":"s"}', '{"other":1}']),
    ]
    for schema, docs in REJECTS:
        g = zg.compile_schema(byte_ctx, json.dumps(schema).encode(),
                              profile=b"spec-v1")
        try:
            for doc in docs:
                data = doc.encode()
                s = zg.Session(byte_ctx, g)
                try:
                    alive = True
                    for b in data:
                        if s.accept(b) != zg.BLG_OK:
                            alive = False
                            break
                    assert not alive or not s.can_end(), (
                        f"{schema} must reject {doc!r}")
                finally:
                    s.destroy()
        finally:
            zg.grammar_release(g)
