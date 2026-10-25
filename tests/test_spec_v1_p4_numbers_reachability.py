"""ADR-0005 completion reachability for the spec-v1 P4 numeric keywords.

Same contract as the P3 module: for every schema the engine now compiles,
every prefix of a valid document must reach a state from which completion
is still possible - feeding the prefix never dead-ends, and after the
prefix the state either accepts (can_end) or admits at least one
continuation token in the mask, in particular the document's own next
byte. The exact value verdict of the num_range/num_mult machines is
produced only at a confirmed boundary; a violated bound is a
mask-visible rejection there (the offending delimiter byte is simply not
admitted), so a live prefix of a valid document must never die.

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
    # Inclusive bounds; the value may recover mid-token (2 -> 25 -> 2.5).
    ({"minimum": 2},
     ["2", "25", "2.5", "2e0", "10", '"x"', "[1]"]),
    ({"minimum": -2, "maximum": 3},
     ["-2", "-2.0", "0", "3", "2.999", "-1.5"]),
    # Exclusive bounds.
    ({"exclusiveMinimum": 1.1},
     ["1.2", "1.10001", "12", '"s"']),
    ({"exclusiveMaximum": 3.0},
     ["2.2", "2.99999", "-100"]),
    # multipleOf: the verdict is exact at the boundary.
    ({"multipleOf": 2},
     ["10", "0", "-4", "100.0", "2e3", '"foo"']),
    ({"multipleOf": 1.5},
     ["0", "4.5", "3", "7.5", "-3"]),
    ({"multipleOf": 0.0001},
     ["0.0075", "1", "123.4567"]),
    # Range + divisibility through the typed core (allOf comb parts).
    ({"type": "integer", "minimum": 5, "multipleOf": 2},
     ["6", "10", "100", "6.0"]),
    ({"type": "number", "minimum": 0, "maximum": 1},
     ["0", "0.5", "1", "0.999"]),
    # P3 combinator shapes with numeric assertions.
    ({"allOf": [{"minimum": 5}, {"multipleOf": 2}]},
     ["6", "10", '"x"']),
    ({"oneOf": [{"maximum": 0}, {"minimum": 10}]},
     ["-1", "10", "15"]),
    ({"not": {"const": 5}, "type": "integer", "minimum": 2},
     ["2", "3", "6"]),
    # Inside containers.
    ({"type": "object",
      "properties": {"k": {"type": "number", "minimum": 0, "maximum": 1}},
      "required": ["k"]},
     ['{"k":0.5}', '{"k":1}']),
    ({"type": "array", "items": {"multipleOf": 2}},
     ["[]", "[2,4]", "[2,4.0]"]),
    ({"type": "array", "items": {"type": "integer", "minimum": 0}},
     ["[0,1,100]", "[]"]),
]


def test_p4_numbers_completion_reachability(byte_ctx):
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


def test_p4_numbers_rejected_documents_dead_end(byte_ctx):
    # Documents outside the language must hit a dead thread at or before
    # their last byte (never a late can_end surprise): either accept fails
    # mid-document or the final state does not accept.
    REJECTS = [
        ({"minimum": 2}, ["1", "1.999", "-5", "0.1e1"]),
        ({"maximum": 3}, ["4", "3.0001", "30", "1e1"]),
        ({"exclusiveMinimum": 1.1}, ["1.1", "1.10", "0.6"]),
        ({"exclusiveMaximum": 3.0}, ["3", "3.0", "3.5"]),
        ({"multipleOf": 2}, ["7", "0.5", "5e-1", "15"]),
        ({"multipleOf": 1.5}, ["35", "0.75", "4.4"]),
        ({"multipleOf": 0.0001}, ["0.00751", "1e-5"]),
        ({"type": "integer", "minimum": 5, "multipleOf": 2},
         ["5", "4", "7", "6.5", "5.0"]),
        ({"allOf": [{"minimum": 5}, {"multipleOf": 2}]}, ["5", "4", "7"]),
        ({"oneOf": [{"maximum": 0}, {"minimum": 10}]}, ["5", "0.0001", "9.999"]),
        ({"type": "array", "items": {"multipleOf": 2}}, ["[3]", "[2,3]"]),
        ({"type": "object",
          "properties": {"k": {"type": "number", "minimum": 0, "maximum": 1}},
          "required": ["k"]},
         ['{"k":1.5}', '{"k":-0.1}']),
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


def test_p4_numbers_boundary_mask(byte_ctx):
    # {"minimum": 2} after "1": the token can still recover (15, 1.5 are
    # in range), so digits and '.' are admitted; a delimiter is not -
    # ending the value at 1 would violate the bound (the rejection is
    # mask-visible at the boundary byte, ADR-0005).
    schema = {"type": "number", "minimum": 2}
    g = zg.compile_schema(byte_ctx, json.dumps(schema).encode(),
                          profile=b"spec-v1")
    try:
        s = zg.Session(byte_ctx, g)
        try:
            for b in b"1":
                assert s.accept(b) == zg.BLG_OK
            ids = s.fill_mask_ids()
            assert ord(".") in ids
            assert ord("5") in ids
            assert ord("e") in ids
            assert not s.can_end()
        finally:
            s.destroy()
        # After "2" the document may end (2 >= 2) or continue.
        s = zg.Session(byte_ctx, g)
        try:
            for b in b"2":
                assert s.accept(b) == zg.BLG_OK
            assert s.can_end()
            ids = s.fill_mask_ids()
            assert ord("5") in ids
            assert ord(".") in ids
        finally:
            s.destroy()
    finally:
        zg.grammar_release(g)


# Numeric conjunctions (ADR-0011): no runtime RESOURCE_LIMIT, no fallback
# search on byte-complete vocabularies, exact masks on number bytes.

from exact_numbers import exact_is_valid  # noqa: E402
from reference import NumConj, TokenizerSpec, numconj_reachable  # noqa: E402

NUM_CONJ_SCHEMAS = [
    {"type": "number", "minimum": 1, "maximum": 9},
    {"minimum": 1, "maximum": 9},
    {"type": "number", "maximum": 9},
    {"type": "integer", "enum": [1, 2, 3]},
    {"type": "integer", "minimum": 1, "maximum": 9},
    {"type": "integer", "maximum": 9},
    {"type": "number", "multipleOf": 1, "minimum": 1, "maximum": 9},
    {"type": "integer", "minimum": 0, "maximum": 1},
    {"type": "integer", "minimum": -9},
    {"type": "number", "multipleOf": 0.5, "maximum": 2},
    {"type": "integer", "not": {"const": 5}, "minimum": 1, "maximum": 9},
    {"type": "integer", "minimum": 1},
    {"type": "integer", "exclusiveMinimum": 0},
]

NUM_PREFIXES = [
    ({"type": "integer", "minimum": -9}, ["-5", "-", "5", ""]),
    ({"type": "number", "multipleOf": 0.5, "maximum": 2}, ["1", "", "1.", "1.5", "0.5e"]),
    ({"type": "integer", "not": {"const": 5}, "minimum": 1, "maximum": 9}, ["", "5", "5.", "0.5e"]),
    ({"type": "integer", "minimum": 1}, ["5", "0", "0.", "0.0"]),
    ({"type": "integer", "exclusiveMinimum": 0}, ["", "0.", "-"]),
    ({"type": "integer", "maximum": 9}, ["-", "5", "51", "9.0"]),
    ({"type": "integer", "minimum": 0, "maximum": 1}, ["", "0", "1", "1.0e"]),
    ({"type": "integer", "minimum": 1, "maximum": 9}, ["6", "60", "0.6e"]),
]

NUMBER_BYTES = b"0123456789.eE+-"
WS_BYTES = b" \t\n\r"


def _vocab(extra=()) -> TokenizerSpec:
    tokens = tuple(bytes([i]) for i in range(256)) + tuple(extra) + (b"", b"")
    n = len(tokens)
    return TokenizerSpec(tokens=tokens, eos_ids=(n - 2,), special_ids=(n - 1,)).validate()


BYTE_COMPLETE_VOCABS = {
    "bytes": (),
    "bytes+123": (b"123",),
    "bytes+json": (b'{"', b'":', b'",', b'"}'),
}


def _compile_num(ctx, schema):
    return zg.compile_schema(ctx, json.dumps(schema).encode(), profile=b"spec-v1")


def test_num_conj_prefix_masks_match_reference(byte_ctx):
    # Engine mask over the number bytes equals the exact reference verdict
    # of each one-byte extension (tests/reference.py, numconj_reachable).
    for schema, prefixes in NUM_PREFIXES:
        conj = NumConj.from_schema(schema)
        g = _compile_num(byte_ctx, schema)
        try:
            for prefix in prefixes:
                zg.cert_stats_reset()
                s = zg.Session(byte_ctx, g)
                try:
                    refused = None
                    for i, b in enumerate(prefix.encode()):
                        if s.accept(b) != zg.BLG_OK:
                            refused = prefix[: i + 1]
                            break
                    if refused is not None:
                        # A refused byte must start a dead prefix.
                        ref = numconj_reachable(refused, conj)
                        assert ref is None or not ref[0], (schema, refused)
                        continue
                    ids = s.fill_mask_ids()
                    got = {b for b in NUMBER_BYTES if b in ids}
                    want = set()
                    for b in NUMBER_BYTES:
                        ref = numconj_reachable(prefix + chr(b), conj)
                        if ref is not None and ref[0]:
                            want.add(b)
                    assert got == want, (schema, prefix, bytes(sorted(got)), bytes(sorted(want)))
                    assert zg.cert_stats()[zg.UND_SEARCH_ENTERED] == 0, (schema, prefix)
                finally:
                    s.destroy()
        finally:
            zg.grammar_release(g)


CLOSERS = (b'"', b"]", b"}", b'"}')


def _random_walk(ctx, g, spec, rng, max_steps=400) -> bytes:
    # Uniform over the mask, with EOS taken early sometimes and always
    # after 24 steps; past that point closing tokens are preferred so that
    # strings and containers (untyped schemas) finish.
    eos_ids = set(spec.eos_ids)
    s = zg.Session(ctx, g)
    try:
        doc = b""
        for step in range(max_steps):
            ids = s.fill_mask_ids()
            assert ids, f"dead end after {doc!r}"
            rest = sorted(ids - eos_ids)
            if ids & eos_ids and (not rest or step >= 24 or rng.random() < 0.2):
                assert s.can_end(), doc
                return doc
            body = [t for t in rest if spec.tokens[t] not in WS_BYTES] or rest
            closing = [t for t in rest if spec.tokens[t] in CLOSERS]
            if step >= 24 and closing and rng.random() < 0.7:
                t = rng.choice(closing)
            else:
                t = rng.choice(body if rng.random() < 0.95 else rest)
            assert s.accept(t) == zg.BLG_OK, (doc, spec.tokens[t])
            doc += spec.tokens[t]
        raise AssertionError(f"no EOS within {max_steps} steps: {doc!r}")
    finally:
        s.destroy()


WALKS_PER_CONFIG = 20  # x 3 vocabularies x 2 modes x 2 fast-path settings = 240 per schema


@pytest.mark.parametrize("vocab", sorted(BYTE_COMPLETE_VOCABS))
@pytest.mark.parametrize("mode", [zg.BLG_MODE_LAZY, zg.BLG_MODE_ADAPTIVE])
@pytest.mark.parametrize("fast_path", [zg.BLG_MASK_FAST_PATH_ON, zg.BLG_MASK_FAST_PATH_OFF])
def test_num_conj_random_walks_to_eos(vocab, mode, fast_path):
    import random
    spec = _vocab(BYTE_COMPLETE_VOCABS[vocab])
    ctx = zg.Context(spec, mode, mask_fast_path=fast_path)
    try:
        for i, schema in enumerate(NUM_CONJ_SCHEMAS):
            rng = random.Random(f"{vocab}/{mode}/{fast_path}/{i}")
            g = _compile_num(ctx, schema)
            try:
                zg.cert_stats_reset()
                for _ in range(WALKS_PER_CONFIG):
                    doc = _random_walk(ctx, g, spec, rng)
                    assert exact_is_valid(schema, doc), (schema, doc)
                assert zg.cert_stats()[zg.UND_SEARCH_ENTERED] == 0, schema
            finally:
                zg.grammar_release(g)
    finally:
        ctx.destroy()


def test_num_conj_incomplete_vocabulary():
    # A vocabulary without full byte coverage: the numeric schemas either
    # compile and generate valid documents, or are refused at compile time;
    # never a runtime RESOURCE_LIMIT. (spec-v1 numbers have infinitely many
    # spellings, so the core refuses them on such vocabularies.)
    import random
    tokens = tuple(bytes([b]) for b in NUMBER_BYTES) + (b"123", b"45", b"", b"")
    n = len(tokens)
    spec = TokenizerSpec(tokens=tokens, eos_ids=(n - 2,), special_ids=(n - 1,)).validate()
    ctx = zg.Context(spec, zg.BLG_MODE_LAZY)
    outcomes = {}
    try:
        for i, schema in enumerate(NUM_CONJ_SCHEMAS):
            try:
                g = _compile_num(ctx, schema)
            except zg.CoreFailure as e:
                assert e.status == zg.BLG_ERR_UNSUPPORTED_TOKENIZER, (schema, e.status)
                outcomes[i] = "refused"
                continue
            outcomes[i] = "compiled"
            try:
                rng = random.Random(i)
                for _ in range(40):
                    doc = _random_walk(ctx, g, spec, rng)
                    assert exact_is_valid(schema, doc), (schema, doc)
            finally:
                zg.grammar_release(g)
    finally:
        ctx.destroy()
    assert outcomes


NUM_CONJ_IN_CONTAINERS = [
    ({"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 63}},
     ["[6,7,8,9,10]", "[1,2,3,4,5,6,7,8,9]", "[63,0]"]),
    ({"type": "object", "properties": {
        "score": {"type": ["integer", "null"], "minimum": 0, "maximum": 990},
        "user": {"type": "string"}}},
     ['{"score":700,"user":"a"}', '{"score":990}', '{"score":null}']),
]


def test_num_conj_inside_containers(byte_ctx):
    # A bounded integer followed by an element or member delimiter: every
    # byte of each document is in the mask, never a RESOURCE_LIMIT.
    for schema, docs in NUM_CONJ_IN_CONTAINERS:
        g = _compile_num(byte_ctx, schema)
        try:
            for doc in docs:
                assert exact_is_valid(schema, doc), (schema, doc)
                s = zg.Session(byte_ctx, g)
                try:
                    for b in doc.encode():
                        assert b in s.fill_mask_ids(), (schema, doc)
                        assert s.accept(b) == zg.BLG_OK, (schema, doc)
                    assert s.can_end() and s.finish() == zg.BLG_OK, (schema, doc)
                finally:
                    s.destroy()
        finally:
            zg.grammar_release(g)
