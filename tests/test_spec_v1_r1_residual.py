"""ADR-0005 R1 residual reachability for spec-v1 value machines.

For grammars with deferred boundary verdicts (comb allOf/oneOf/ifelse,
pattern strings, str_excl, value-constrained numbers), a live prefix must
never dead-end: the mask and accept must admit a token only when a
completing continuation exists. This module pins the known repro cases
(regex, product and numeric) plus the merged-allOf
open-object case that exercises the open_obj residual certifier, across the
full mode x fast-path matrix, and sanity-checks that complete valid
documents still pass every mask step.

The module is skipped until the core is built.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

MODES = [zg.BLG_MODE_LAZY, zg.BLG_MODE_ADAPTIVE]
FAST = [zg.BLG_MASK_FAST_PATH_ON, zg.BLG_MASK_FAST_PATH_OFF]

# (name, schema, live prefix, forbidden next byte)
DEAD_PREFIX_CASES = [
    ("regex", {"type": "string", "pattern": "^a$", "maxLength": 1},
     b'"', ord("b")),
    ("product", {"allOf": [{"enum": ["ab", "xy"]}, {"enum": ["ac", "xy"]}]},
     b'"', ord("a")),
    ("numeric", {"type": "number", "minimum": 1}, b"", ord("-")),
    # Anchored alternation pattern on an object property value: after the
    # opening quote only 'h'/'f' start a match; 'i' dead-ends. The object
    # frame makes the alive tokens search-certified, which drains the shared
    # per-fill budget mid-walk - the budget gate must never downgrade the
    # exact residual certification of the remaining bytes.
    ("pattern_alt_prop", {
        "type": "object",
        "properties": {
            "a": {"enum": ["ingest", "publish", "ingest+publish"]},
            "b": {"type": "string", "pattern": "^(https|file)://"},
        },
        "required": ["a", "b"],
        "additionalProperties": False,
    }, b'{"a":"ingest","b":"', ord("i")),
]

PATTERN_ALT_PROP = DEAD_PREFIX_CASES[3][1]

MERGED_ALLOF = {"allOf": [
    {"type": "object", "properties": {"a": {}, "b": {}}, "required": ["a", "b"]},
    {"type": "object", "properties": {"b": {}, "a": {}}, "required": ["a", "b"]}]}

VALID_DOCS = [
    ({"type": "string", "pattern": "^a$", "maxLength": 1}, '"a"'),
    ({"allOf": [{"enum": ["ab", "xy"]}, {"enum": ["ac", "xy"]}]}, '"xy"'),
    ({"type": "number", "minimum": 1}, "1"),
    ({"type": "number", "minimum": 1}, "0.01e2"),
    ({"type": "integer"}, "1.0e1"),
    ({"type": "number", "multipleOf": 2}, "0.5e3"),
    (MERGED_ALLOF, '{"a":1,"b":2}'),
]


@pytest.fixture(params=MODES, ids=["lazy", "adaptive"])
def mode(request):
    return request.param


@pytest.fixture(params=FAST, ids=["fast-on", "fast-off"])
def fast(request):
    return request.param


@pytest.fixture
def ctx(byte_tok, mode, fast):
    c = zg.Context(byte_tok, mode, mask_fast_path=fast)
    yield c
    assert c.destroy() == zg.BLG_OK


def test_r1_dead_prefix_not_admitted(ctx):
    for name, schema, prefix, forbidden in DEAD_PREFIX_CASES:
        g = zg.compile_schema(ctx, json.dumps(schema).encode(), profile=b"spec-v1")
        s = zg.Session(ctx, g)
        try:
            for b in prefix:
                assert b in s.fill_mask_ids(), f"{name}: live prefix byte rejected"
                assert s.accept(b) == zg.BLG_OK
            ids = s.fill_mask_ids()
            assert forbidden not in ids, (
                f"{name}: mask admits dead-end byte {bytes([forbidden])!r}")
            assert s.accept(forbidden) != zg.BLG_OK, (
                f"{name}: accept admits dead-end byte {bytes([forbidden])!r}")
        finally:
            s.destroy()
            zg.grammar_release(g)


def test_r1_merged_allof_object_completes(ctx):
    # The merged allOf compiles to a single open object whose required
    # values sit under comb nodes; the residual certifier must settle every
    # mask step without exhausting the search budget.
    g = zg.compile_schema(ctx, json.dumps(MERGED_ALLOF).encode(), profile=b"spec-v1")
    s = zg.Session(ctx, g)
    try:
        doc = b'{"a":1,"b":2}'
        for i, b in enumerate(doc):
            ids = s.fill_mask_ids()
            assert b in ids, f"byte {bytes([b])!r}@{i} not in mask"
            assert s.accept(b) == zg.BLG_OK, f"accept failed at byte {i}"
        assert s.can_end()
        assert s.finish() == zg.BLG_OK
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_r1_merged_allof_out_of_order_key_is_dead(ctx):
    # Key "b" first would skip the required earlier key "a": closing the
    # '"b"' key dead-ends, so '"' must leave the mask after '{"b' (the
    # partial key can still grow into an undeclared key).
    g = zg.compile_schema(ctx, json.dumps(MERGED_ALLOF).encode(), profile=b"spec-v1")
    s = zg.Session(ctx, g)
    try:
        for b in b'{"b':
            assert b in s.fill_mask_ids()
            assert s.accept(b) == zg.BLG_OK
        ids = s.fill_mask_ids()
        assert ord('"') not in ids, "closing quote of a dead key still admitted"
        assert ord("x") in ids, "undeclared-key continuation wrongly blocked"
    finally:
        s.destroy()
        zg.grammar_release(g)


ONEOF_OVERLAP = {"oneOf": [
    {"type": "string", "pattern": "^(ab|c)$"},
    {"type": "string", "pattern": "^(ab|d)$"},
]}


def test_r1_oneof_overlap_mask_is_exact(ctx):
    # Expert counterexample for strict ADR-0005 D3: after '"' both branches
    # are individually live ("ab" fits both patterns, so neither dies on
    # 'a'), but no oneOf completion starts with 'a' - "ab" is accepted by
    # BOTH branches, violating exactly-one. Search-budget exhaustion must
    # surface as ResourceLimit, never as a falsely admitted byte, and the
    # budgeted search must still find the real witnesses 'c' and 'd'.
    g = zg.compile_schema(ctx, json.dumps(ONEOF_OVERLAP).encode(), profile=b"spec-v1")
    s = zg.Session(ctx, g)
    try:
        assert ord('"') in s.fill_mask_ids()
        assert s.accept(ord('"')) == zg.BLG_OK
        ids = s.fill_mask_ids()
        allowed = bytes(sorted(b for b in range(256) if b in ids))
        assert allowed == b"cd", f"mask at '\"' is {allowed!r}, want exactly b'cd'"
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_r1_oneof_overlap_accept_paths(ctx):
    g = zg.compile_schema(ctx, json.dumps(ONEOF_OVERLAP).encode(), profile=b"spec-v1")
    try:
        for doc, ok in [(b'"c"', True), (b'"d"', True),
                        (b'"ab"', False), (b'"ad"', False), (b'"x"', False)]:
            s = zg.Session(ctx, g)
            try:
                accepted = True
                for b in doc:
                    if b not in s.fill_mask_ids() or s.accept(b) != zg.BLG_OK:
                        accepted = False
                        break
                if ok:
                    assert accepted, f"{doc}: valid oneOf document refused"
                    assert s.can_end()
                    assert s.finish() == zg.BLG_OK
                else:
                    assert not accepted, f"{doc}: invalid document passed every mask step"
            finally:
                s.destroy()
    finally:
        zg.grammar_release(g)


def test_r1_valid_documents_pass_every_mask_step(ctx):
    for schema, doc in VALID_DOCS:
        g = zg.compile_schema(ctx, json.dumps(schema).encode(), profile=b"spec-v1")
        s = zg.Session(ctx, g)
        try:
            data = doc.encode()
            for i, b in enumerate(data):
                ids = s.fill_mask_ids()
                assert b in ids, f"{schema} / {doc!r}: byte {i} not in mask"
                assert s.accept(b) == zg.BLG_OK, (
                    f"{schema} / {doc!r}: accept failed at byte {i}")
            assert s.can_end(), f"{schema} must accept {doc!r}"
            assert s.finish() == zg.BLG_OK
        finally:
            s.destroy()
            zg.grammar_release(g)


# A closed object without declared properties admits only {}; its anyOf
# branches run as sibling threads of the same value.
CLOSED_NO_PROPS = {"type": "object", "additionalProperties": False, "anyOf": [
    {"properties": {"a": {"type": "string"}}},
    {"properties": {"b": {"type": "string"}}}]}
CLOSED_NO_PROPS_CASES = [
    (CLOSED_NO_PROPS, [(b"", b"{"), (b"{", b"}")], b"{}"),
    ({"type": "object", "properties": {"g": CLOSED_NO_PROPS}},
     [(b"{", b'"}'), (b'{"g":', b"{"), (b'{"g":{', b"}")], b'{"g":{}}'),
]


def test_r1_closed_object_without_properties_under_anyof(ctx):
    for schema, steps, doc in CLOSED_NO_PROPS_CASES:
        g = zg.compile_schema(ctx, json.dumps(schema).encode(), profile=b"spec-v1")
        try:
            for prefix, want in steps:
                s = zg.Session(ctx, g)
                try:
                    for b in prefix:
                        assert s.accept(b) == zg.BLG_OK, (schema, prefix)
                    got = bytes(sorted(t for t in s.fill_mask_ids() if t < 256))
                    assert got == want, (schema, prefix, got)
                finally:
                    s.destroy()
            s = zg.Session(ctx, g)
            try:
                for b in doc:
                    assert b in s.fill_mask_ids() and s.accept(b) == zg.BLG_OK, (schema, doc)
                assert s.can_end() and s.finish() == zg.BLG_OK
            finally:
                s.destroy()
        finally:
            zg.grammar_release(g)


# An array element whose combinator branches spawn sibling threads on the
# element's first byte: every spawned thread must see that byte once.
# Before, a lazy alternative was skipped and another stepped twice, so the
# mask after "[" refused "{" and "[{}]" was rejected.
_BANS2 = {"type": "object", "allOf": [{"not": {"required": ["a", "b"]}},
                                      {"not": {"required": ["a", "c"]}}]}
# minProperties of a closed object met by an optional declared property:
# the emptiness analysis asked for undeclared keys only and called the
# object empty, so a parent requiring it was dead at its first byte.
_MINPROPS_CLOSED = {"type": "object", "additionalProperties": False, "minProperties": 1,
                    "properties": {"p": {"type": "object", "properties": {
                        "s": {"type": "string", "pattern": "\\d{2}", "minLength": 2}}}}}
SPAWN_AND_COUNT_CASES = [
    ({"type": "array", "items": _BANS2},
     ['[{}]', '[{"a":1}]', '[{"b":1,"c":2}]', '[{},{"c":1}]'],
     ['[{"a":1,"b":2}]', '[{"c":1,"a":2}]', "[1]"]),
    ({"type": "array", "items": _BANS2, "minItems": 1},
     ['[{}]', '[{"b":1}]'],
     ["[]"]),
    ({"type": "object", "additionalProperties": False,
      "properties": {"x": _MINPROPS_CLOSED}, "required": ["x"]},
     ['{"x":{"p":{}}}', '{"x":{"p":{"s":"12"}}}'],
     ['{"x":{}}', '{}']),
    # Three untyped branches spawn on a number's first byte; a thread that
    # missed the "8" read '""' as the whole value and 8"" was accepted.
    ({"anyOf": [{}, {}, {}]},
     ['8', '"a"', '[8]', '{"a":[1,2]}'],
     ['8""', '8"a"']),
    ({"type": "array", "items": {"anyOf": [{}, {}, {}]}},
     ['[8]', '[8,"a",null,{}]'],
     ['[8""]', '[1,8""]']),
]


def test_r1_spawned_threads_and_closed_min_properties(ctx):
    for schema, ok_docs, bad_docs in SPAWN_AND_COUNT_CASES:
        g = zg.compile_schema(ctx, json.dumps(schema).encode(), profile=b"spec-v1")
        try:
            for doc in ok_docs:
                s = zg.Session(ctx, g)
                try:
                    for b in doc.encode():
                        assert b in s.fill_mask_ids() and s.accept(b) == zg.BLG_OK, (schema, doc)
                    assert s.can_end() and s.finish() == zg.BLG_OK, (schema, doc)
                finally:
                    s.destroy()
            for doc in bad_docs:
                s = zg.Session(ctx, g)
                try:
                    ok = True
                    for b in doc.encode():
                        if b not in s.fill_mask_ids() or s.accept(b) != zg.BLG_OK:
                            ok = False
                            break
                    assert not (ok and s.can_end()), (schema, doc)
                finally:
                    s.destroy()
        finally:
            zg.grammar_release(g)


# Four copies of one object branch, each an array of four closed-object
# alternatives (Github_ultra/o6354.json, reduced): from the first byte of
# an element the parse needs more threads than a cap of 48 or 64 allows.
# A branch past the thread cap is undecided, never dead: the mask keeps
# the byte or the call fails with RESOURCE_LIMIT. Before, the residual
# search counted such branches dead and dropped '{' from the mask.
_ARTIFACT = {"anyOf": [
    {"additionalProperties": False, "properties": {k: {} for k in keys}}
    for keys in (("context", "docker", "image", "sync"),
                 ("context", "image", "jib", "sync"),
                 ("context", "image", "kaniko", "sync"),
                 ("context", "custom", "image", "sync"))],
    "required": ["image"]}
_BUILD = {"properties": {"artifacts": {"items": {"$ref": "#/definitions/Artifact"}}}}
THREAD_CAP_SCHEMA = {
    "anyOf": [{"$ref": "#/definitions/SkaffoldConfig"}],
    "definitions": {
        "Artifact": _ARTIFACT,
        "BuildConfig": {"anyOf": [_BUILD, _BUILD, _BUILD, _BUILD]},
        "SkaffoldConfig": {"properties": {"build": {"$ref": "#/definitions/BuildConfig"}}},
    },
}


def test_r1_thread_cap_never_drops_a_token(byte_tok, mode, fast):
    prefix = b'{"build":{"artifacts":[{'
    seen = {}
    for cap in (16, 32, 48, 64, 96, 128):
        c = zg.Context(byte_tok, mode, mask_fast_path=fast, max_threads_per_state=cap)
        try:
            g = zg.compile_schema(c, json.dumps(THREAD_CAP_SCHEMA).encode(), profile=b"spec-v1")
            s = zg.Session(c, g)
            try:
                for b in prefix[:-1]:
                    assert s.accept(b) == zg.BLG_OK, cap
                try:
                    seen[cap] = "in" if prefix[-1] in s.fill_mask_ids() else "out"
                except zg.CoreFailure as e:
                    assert "RESOURCE_LIMIT" in str(e), (cap, str(e))
                    seen[cap] = "resource_limit"
            finally:
                s.destroy()
                zg.grammar_release(g)
        finally:
            assert c.destroy() == zg.BLG_OK
    assert "out" not in seen.values(), seen
    assert seen[128] == "in", seen
