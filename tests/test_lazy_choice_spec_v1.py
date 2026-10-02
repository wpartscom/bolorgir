"""spec-v1 regressions: lazy choice frames and merged-open-object fixes.

Pins the MaskBench "engine-suspect" families fixed on 2026-09-29:

- lazy choice frames (src/parser.zig): a choice no longer spawns one parser
  thread per alternative eagerly, so wide/nested oneOf shapes (family 2-5:
  oneOf x allOf products well beyond 64 threads) compile and run under the
  default caps. MAX_THREADS_CAP is 128; the old eager spawn blew the cap at
  33 alternatives x 2 allOf conjuncts.
- distributeChoiceConflict (src/schema.zig): an object own-frame whose
  property order conflicts with a oneOf branch's required order no longer
  dead-ends in canonical serializer order (family 1, aiproj shape).
- mergeOpenObj extra_required absorption (src/schema.zig): a name listed as
  required by one allOf conjunct and declared as a property by another is a
  required declared property, not an extra_required ghost that makes the
  merged object unable to close (discovery.schema shape).

Each case feeds a complete valid document byte by byte and asserts every
mask step admits the next byte and accept succeeds.

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


def _one_of_wide(n: int) -> dict:
    # n branches, each a 2-conjunct allOf: eager choice spawning needs
    # 2*n threads at the top of the value, over the old cap of 64 for
    # n >= 33. The const values make exactly one branch match the doc.
    return {
        "oneOf": [
            {"allOf": [
                {"type": "object",
                 "properties": {"k%d" % i: {"const": i}},
                 "required": ["k%d" % i],
                 "additionalProperties": False},
                {"type": "object"},
            ]}
            for i in range(n)
        ]
    }


ONE_OF_WIDE = _one_of_wide(33)
ONE_OF_WIDE_DOC = '{"k7":7}'

# Family 1 (aiproj): own properties all optional in serializer order
# [Cookie, Form, Http, Type]; the oneOf branches require [Type, Cookie] /
# [Type, Form], so own order (Cookie first) conflicts with branch order
# (Type first). Canonical-order docs must be accepted.
AIPROJ_SHAPE = {
    "type": ["object", "null"],
    "properties": {
        "Cookie": {"type": "string"},
        "Form": {"type": "string"},
        "Http": {"type": "string"},
        "Type": {"type": "string"},
    },
    "additionalProperties": False,
    "oneOf": [
        {"type": "object",
         "properties": {"Type": {}, "Cookie": {}},
         "required": ["Type", "Cookie"]},
        {"type": "object",
         "properties": {"Type": {}, "Form": {}},
         "required": ["Type", "Form"]},
    ],
}
AIPROJ_DOC = '{"Type":"t","Cookie":"c"}'

# discovery.schema shape: the left conjunct requires names that the right
# conjunct declares as properties; the merge must mark them required
# declared properties instead of carrying them as extra_required, which
# left the merged open object unable to see the key in its seen-set.
EXTRA_REQUIRED_OVERLAP = {"allOf": [
    {"type": "object", "required": ["a", "b"]},
    {"type": "object",
     "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
     "additionalProperties": False},
]}
EXTRA_REQUIRED_OVERLAP_DOC = '{"a":1,"b":2}'

# Open object with undeclared keys holding arbitrary nested values: the
# anyJSON value machine must stay alive through nested containers.
ANY_VALUE_DOC = '{"a":1,"z":[1,{"k":null},"s"]}'
ANY_VALUE_SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}},
    "required": ["a"],
    "additionalProperties": True,
}

ACCEPT_CASES = [
    ("one_of_wide", ONE_OF_WIDE, ONE_OF_WIDE_DOC),
    ("aiproj_shape", AIPROJ_SHAPE, AIPROJ_DOC),
    ("extra_required_overlap", EXTRA_REQUIRED_OVERLAP, EXTRA_REQUIRED_OVERLAP_DOC),
    ("any_value_undeclared", ANY_VALUE_SCHEMA, ANY_VALUE_DOC),
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


def test_valid_document_passes_every_mask_step(ctx):
    for name, schema, doc in ACCEPT_CASES:
        g = zg.compile_schema(ctx, json.dumps(schema).encode(), profile=b"spec-v1")
        s = zg.Session(ctx, g)
        try:
            data = doc.encode()
            for i, b in enumerate(data):
                ids = s.fill_mask_ids()
                assert b in ids, f"{name}: byte {bytes([b])!r}@{i} not in mask"
                assert s.accept(b) == zg.BLG_OK, f"{name}: accept failed at byte {i}"
            assert s.can_end(), f"{name}: document complete but can_end is false"
            assert s.finish() == zg.BLG_OK, f"{name}: finish failed"
        finally:
            s.destroy()
            zg.grammar_release(g)
