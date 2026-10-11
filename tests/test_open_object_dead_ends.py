"""Open-object dead ends of the T3 acceptance of 50cbd80e (2026-10-08).

52 of the 140 byte-tokenizer passes of seven repository schemas ended in
BLG_ERR_DEAD_END: the mask admitted a key whose closing quote the dispatch
then refused. The exact open-object certificates of the completion filter
(src/complete.zig, ADR-0005) were wrong in two places:

- A: a patternProperties key was counted as a fresh completion whenever
  the pattern could still match ("the matching continuations are
  infinite"). A finite pattern language (`^line[1-3]$`, `^comment[1-9]$`,
  `^\\$schema$`) runs out once its keys are seen, and dispatch refuses a
  duplicate, so `"line2` after a seen `line2` had no continuation.
- B1: a missing dependency name declared after a pending required prop was
  declared unreachable (the dispatch window of the next key was used for
  eventual reachability): `favoriteTopic` -> `tags` behind the required
  `firstName` closed the key `"favoriteTopic` with an empty mask.
- B2: the dependencies a declared key fires by its own dispatch were not
  checked while that key was spelled: a trigger whose dependency was
  already skipped in schema order (`memswap_limit` -> `mem_limit`) was
  admitted and died at its closing quote.

The review of that fix found the same certificates wrong on the
fallback side and along dependencies:

- C1: an undeclared key that the key pattern does not match (the fallback
  value) was a witness only when the empty key was fresh or some extension
  provably left the pattern; a finite non-matching remnant (`""` and `"a"`
  beside `^([\\s\\S]{2,}|[^a])$`) or a literal pattern beside a declared
  `""` gave "no key", so the comma after `{"":0` was masked. Both sides
  are now counted exactly against the taken keys.
- C2: a name a dependency entry can never satisfy any more (a declared
  name behind the skip, an empty value, recursively along a chain of
  triggers) doomed the keys that fire it only one level deep and not on
  the mid-code-point path; a required name whose own dependency was
  skipped did not doom the object at all. In an open object nothing ran
  the filter (grammar needsReachability), so such prefixes were typable
  forever and never closed.

The second review found the count bound unchecked:

- D: in an open object with `maxProperties` nothing ran the filter, so
  after `{"x":1` under `maxProperties: 1` the comma was admitted and the
  next key could never close (false alive). Routed to the filter, a key
  typed while the remaining slots all belong to owed names (`required`,
  dependency names) had no dead rule and the search could not exhaust
  the key spellings (RESOURCE_LIMIT). The count certificate now counts
  the owed names (closed under the dependencies they fire) and the
  in-flight key exactly; the masks are checked from the document start.

Each case pins the exact mask after a prefix (or the bytes it must not
admit); walks over the masks must never dead-end and end in documents the
independent validator accepts. The independent check is
completion_reference: a byte the engine masks must have no completion
witness among its bounded, validator-judged candidates, and the walks
record how many of their steps such a witness confirms. The comparison
with the bounded search (a `maxProperties` gate) is a consistency check
only: the gated path still asks the same certificates for dead verdicts.
The module is skipped until the core is built.
"""

import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

zg = pytest.importorskip("blg_ctypes", reason="core library not built")

jsonschema = pytest.importorskip("jsonschema", reason="independent oracle")

import completion_reference as cref  # noqa: E402
from exact_numbers import exact_dialects, exact_validator  # noqa: E402
from exact_numbers import loads as exact_loads  # noqa: E402

D4 = "http://json-schema.org/draft-04/schema#"
S = {"type": "string"}

# Class A: finite patternProperties languages (closed objects).
PAT_CLOSED = {"type": "object", "properties": {}, "patternProperties": {"^k[12]$": S},
              "additionalProperties": False}
PAT_MULTIBYTE = {"type": "object", "properties": {},
                 "patternProperties": {"^ключ[12]$": S}, "additionalProperties": False}
PAT_FALLBACK = {"type": "object", "properties": {}, "patternProperties": {"^k[12]$": S},
                "additionalProperties": {"type": "integer"}}
PAT_REQUIRED = {"type": "object", "properties": {"x": S}, "required": ["k1"],
                "patternProperties": {"^k[1-3]$": S}, "additionalProperties": False}
PAT_TRIGGER = {"$schema": D4, "type": "object", "properties": {"b": S, "x": S},
               "patternProperties": {"^k[12]$": S}, "dependencies": {"k1": ["b"]},
               "additionalProperties": False}
ADDRESS = {"$schema": D4, "type": "object",
           "properties": {"city": S, "state": S, "zip": S, "country": S},
           "patternProperties": {"^line[1-3]$": S}, "additionalProperties": False,
           "required": ["city", "state", "zip", "country", "line1"]}
NOTE = {"$schema": D4, "type": "object", "properties": {"author": S, "title": S, "content": S},
        "patternProperties": {"^comment[1-9]$": S}, "additionalProperties": False,
        "required": ["author", "title", "content"]}
SCHEMA_KEY = {"type": "object", "properties": {"name": S},
              "patternProperties": {"^\\$schema$": {"const": "x"}},
              "additionalProperties": False, "required": ["name"]}
# Class B: array-form dependencies in schema order.
DEP_BEHIND = {"$schema": D4, "type": "object", "properties": {"b": S, "a": S},
              "dependencies": {"a": ["b"]}, "additionalProperties": False}
DEP_PAST_REQUIRED = {"$schema": D4, "type": "object", "properties": {"a": S, "r": S, "b": S},
                     "required": ["r"], "dependencies": {"a": ["b"]},
                     "additionalProperties": False}
PROFILE = {"$schema": D4, "type": "object",
           "properties": {"email": {"type": "string", "pattern": "^[a-z]+@[a-z]+\\.[a-z]{2,4}$"},
                          "favoriteTopic": S, "firstName": S, "lastName": S,
                          "tags": {"type": "array", "items": S}},
           "required": ["email", "firstName", "lastName"],
           "dependencies": {"favoriteTopic": ["tags"]}, "additionalProperties": False}
PROFILE_LATE = {"$schema": D4, "type": "object",
                "properties": {"email": S, "firstName": S, "lastName": S,
                               "tags": {"type": "array", "items": S}, "favoriteTopic": S},
                "required": ["email", "firstName", "lastName"],
                "dependencies": {"favoriteTopic": ["tags"]}, "additionalProperties": False}
SERVICES = {"$schema": D4, "type": "object",
            "patternProperties": {"^[a-z]+$": {"$ref": "#/definitions/service"}},
            "additionalProperties": False,
            "definitions": {"service": {
                "type": "object",
                "properties": {"image": S, "mem_limit": S, "memswap_limit": S},
                "dependencies": {"memswap_limit": ["mem_limit"]},
                "additionalProperties": False}}}

# C1: the fallback side of a key pattern, counted exactly.
I = {"type": "integer"}
FALLBACK_FINITE = {"type": "object", "properties": {"": I},
                   "patternProperties": {"^([\\s\\S]{2,}|[^a])$": False},
                   "additionalProperties": I}
FALLBACK_TAKEN = {"type": "object", "properties": {"": I, "a": I},
                  "patternProperties": {"^([\\s\\S]{2,}|[^a])$": False},
                  "additionalProperties": I}
FALLBACK_LITERAL = {"type": "object", "properties": {"": I},
                    "patternProperties": {"abc": False}, "additionalProperties": I}
FALLBACK_MULTIBYTE = {"type": "object", "properties": {"": I},
                      "patternProperties": {"^([\\s\\S]{2,}|[^\u00e9])$": False},
                      "additionalProperties": I}
# C2: dependency doom.
MIDCP_OWN_DEP = {"$schema": D4, "type": "object",
                 "properties": {"b": S, "x": S, "\u00e91": S, "z": S},
                 "dependencies": {"\u00e91": ["b"]}, "additionalProperties": False}
REQUIRED_OWN_DEP = {"$schema": D4, "type": "object", "properties": {"b": S, "x": S, "r": S},
                    "required": ["r"], "dependencies": {"r": ["b"]},
                    "additionalProperties": False}
REQUIRED_OWN_DEP_OPEN = {"$schema": D4, "type": "object",
                         "properties": {"b": S, "x": S, "r": S},
                         "required": ["r"], "dependencies": {"r": ["b"]}}
EXTRA_OWN_DEP = {"$schema": D4, "type": "object", "properties": {"b": S, "x": S},
                 "required": ["u"], "dependencies": {"u": ["b"]}}
DEP_CHAIN = {"$schema": D4, "type": "object",
             "properties": {"X": S, "y": S, "T": S, "D1": S},
             "dependencies": {"T": ["D1"], "D1": ["X"]}, "additionalProperties": False}
DEP_CYCLE = {"$schema": D4, "type": "object", "properties": {"a": S, "b": S},
             "dependencies": {"a": ["b"], "b": ["a"]}, "additionalProperties": False}
# D: count bounds.
MAX0 = {"type": "object", "maxProperties": 0}
MAX1 = {"type": "object", "maxProperties": 1}  # the review's example
MAX3 = {"type": "object", "maxProperties": 3, "additionalProperties": I}
MAX1_REQUIRED = {"type": "object", "maxProperties": 1, "required": ["a"],
                 "additionalProperties": I}
MAX2_DECLARED = {"type": "object", "maxProperties": 2,
                 "properties": {"a": I, "b": I, "c": I}, "required": ["c"],
                 "additionalProperties": I}
MAX2_EXTRA = {"type": "object", "maxProperties": 2, "required": ["a", "b"],
              "additionalProperties": I}
MAX_EQ_MIN = {"type": "object", "minProperties": 2, "maxProperties": 2,
              "additionalProperties": I}
MAX_DEP = {"$schema": D4, "type": "object", "maxProperties": 2,
           "dependencies": {"a": ["b"]}, "additionalProperties": S}
MAX_DEP_CHAIN = {"type": "object", "maxProperties": 2,
                 "dependentRequired": {"a": ["b"], "b": ["c"]}, "additionalProperties": I}
MAX_REQUIRED_DEP = {"type": "object", "maxProperties": 2, "required": ["r"],
                    "dependentRequired": {"r": ["s"]}, "additionalProperties": I}
MAX_DECLARED_DEP = {"type": "object", "maxProperties": 2,
                    "properties": {"a": I, "b": I, "c": I},
                    "dependentRequired": {"a": ["c"]}, "additionalProperties": False}
MAX_NESTED = {"type": "object", "maxProperties": 1,
              "additionalProperties": {"type": "object", "maxProperties": 1,
                                       "additionalProperties": I}}
MAX_PATTERN = {"type": "object", "maxProperties": 1, "patternProperties": {"^k[12]$": I},
               "additionalProperties": S}
MAX_ITEMS = {"type": "array", "items": {"type": "object", "maxProperties": 1,
                                        "required": ["a"], "additionalProperties": I}}
MAX_NAMES = {"type": "object", "maxProperties": 1, "propertyNames": {"maxLength": 2},
             "additionalProperties": I}

# (schema, prefix, exact allowed bytes after it, EOS allowed); str or bytes
MASKS = [
    (PAT_CLOSED, '{"k1":"","k', "2", False),
    (PAT_CLOSED, '{"k2":"","k', "1", False),
    (PAT_CLOSED, '{"k1":"","k2":""', "}", False),
    (PAT_MULTIBYTE, '{"ключ1":"","ключ', "2", False),
    (PAT_MULTIBYTE, '{"ключ1":"","ключ2":""', "}", False),
    (PAT_REQUIRED, '{"x":"","k1":"","k2":"","k3":""', "}", False),
    (PAT_TRIGGER, '{"x":"","k', "2", False),
    (PAT_TRIGGER, '{"b":"","k', "12", False),
    (ADDRESS, '{"city":"","line2":"","line1":"","state":"","line', "3", False),
    (NOTE, '{"author":"","comment3":"","comment', "12456789", False),
    (SCHEMA_KEY, '{"$schema":"x","name":""', "}", False),
    (DEP_BEHIND, '{"', "b", False),
    (DEP_BEHIND, '{"b":"","', "a", False),
    (DEP_PAST_REQUIRED, '{"a', '"', False),
    (DEP_PAST_REQUIRED, '{"a":"","r":""', ",", False),
    (PROFILE, '{"email":"a@b.cc","favoriteTopic', '"', False),
    (PROFILE, '{"email":"a@b.cc","favoriteTopic":"","firstName":"","lastName":""', ",", False),
    (PROFILE_LATE, '{"email":"","firstName":"","lastName":"","', "t", False),
    (SERVICES, '{"web":{"mem', "_", False),
    (SERVICES, '{"web":{"mem_limit":"","mem', "s", False),
    (FALLBACK_FINITE, '{"', '"a', False),
    (FALLBACK_FINITE, '{"":0', ",.Ee}", False),
    (FALLBACK_FINITE, '{"":0,"', "a", False),
    (FALLBACK_TAKEN, '{"":0,"a":0', ".Ee}", False),
    (FALLBACK_LITERAL, '{"":0', ",.Ee}", False),
    (FALLBACK_MULTIBYTE, b'{"', b'"\xc3', False),
    (FALLBACK_MULTIBYTE, b'{"":0,"\xc3', b"\xa9", False),
    (MIDCP_OWN_DEP, '{"x":"","', "z", False),
    (REQUIRED_OWN_DEP, '{"', "b", False),
    (REQUIRED_OWN_DEP, '{"b":"","', "rx", False),
    (DEP_CHAIN, '{"', "Xy", False),
    (DEP_CHAIN, '{"y":""', "}", False),
    (DEP_CHAIN, '{"X":"","y":"","', "DT", False),
    (DEP_CYCLE, '{"', "a", False),
    (DEP_CYCLE, '{"a":"","', "b", False),
    (MAX0, "{", "}", False),
    (MAX1, "{", '"}', False),
    (MAX1, '{"x":1', ".0123456789Ee}", False),
    (MAX1, '{"x":1}', "", True),
    (MAX3, '{"a":1,"b":2', ",.0123456789Ee}", False),
    (MAX3, '{"a":1,"b":2,"c":3', ".0123456789Ee}", False),
    (MAX1_REQUIRED, '{"', "a", False),
    (MAX1_REQUIRED, '{"a":1', ".0123456789Ee}", False),
    (MAX2_DECLARED, '{"a":1,"', "c", False),
    (MAX2_DECLARED, '{"c":1', ",.0123456789Ee}", False),
    (MAX2_EXTRA, '{"', "ab", False),
    (MAX2_EXTRA, '{"b":1,"', "a", False),
    (MAX_EQ_MIN, '{"a":1', ",.0123456789Ee", False),
    (MAX_EQ_MIN, '{"a":1,"b":2', ".0123456789Ee}", False),
    (MAX_DEP, '{"a":""', ",", False),
    (MAX_DEP, '{"a":"","', "b", False),
    (MAX_DEP_CHAIN, '{"b":1,"', "c", False),
    (MAX_REQUIRED_DEP, '{"', "rs", False),
    (MAX_REQUIRED_DEP, '{"s":1,"', "r", False),
    (MAX_DECLARED_DEP, '{"', "abc", False),
    (MAX_DECLARED_DEP, '{"a":1,"', "c", False),
    (MAX_DECLARED_DEP, '{"b":1,"c":1', ".0123456789Ee}", False),
    (MAX_NESTED, '{"a":{"b":1', ".0123456789Ee}", False),
    (MAX_NESTED, '{"a":{"b":1}', "}", False),
    (MAX_PATTERN, '{"k1":1', ".0123456789Ee}", False),
    (MAX_ITEMS, '[{"', "a", False),
    (MAX_ITEMS, '[{"a":1', ".0123456789Ee}", False),
    (MAX_NAMES, '{"ab":1', ".0123456789Ee}", False),
]

# Open objects: fresh keys keep every prefix typable, so the case pins the
# bytes the mask must refuse (the closing quote of a key whose dispatch
# dooms an obligation) and one it must admit. (schema, prefix, refused,
# admitted)
REFUSED = [
    (REQUIRED_OWN_DEP_OPEN, '{"x', '"', "y"),
    (REQUIRED_OWN_DEP_OPEN, '{"b":"","x":""', "}", ","),
    (EXTRA_OWN_DEP, '{"x', '"', "y"),
    (EXTRA_OWN_DEP, '{"u":"","x', '"', "y"),
    (EXTRA_OWN_DEP, '{"u":"","b":"","x', "", '"'),
    (MAX_DEP, '{"x":"","a', '"', "b"),
    (MAX_DEP_CHAIN, '{"a', '"', "b"),
    (MAX3, '{"a":1,"b":2,"c', "", '"'),
]

WALK_SCHEMAS = [PAT_CLOSED, PAT_MULTIBYTE, PAT_FALLBACK, PAT_REQUIRED, PAT_TRIGGER, ADDRESS,
                NOTE, SCHEMA_KEY, DEP_BEHIND, DEP_PAST_REQUIRED, PROFILE, PROFILE_LATE, SERVICES,
                FALLBACK_FINITE, FALLBACK_TAKEN, FALLBACK_LITERAL, FALLBACK_MULTIBYTE,
                MIDCP_OWN_DEP, REQUIRED_OWN_DEP, DEP_CHAIN, DEP_CYCLE]
# Open objects whose close needs one narrow key order: random walks rarely
# finish them, so only the absence of dead ends (and of false ones, by the
# reference) is checked there.
OPEN_WALK_SCHEMAS = [REQUIRED_OWN_DEP_OPEN, EXTRA_OWN_DEP]
# Count-bounded objects: the gated comparison below would not change them
# (they carry their own maxProperties), so they are walked and checked by
# the reference only. Every mask of a walk must be computed (status OK).
COUNT_WALK_SCHEMAS = [MAX1, MAX3, MAX1_REQUIRED, MAX2_DECLARED, MAX2_EXTRA, MAX_EQ_MIN,
                      MAX_DEP, MAX_DEP_CHAIN, MAX_REQUIRED_DEP, MAX_DECLARED_DEP,
                      MAX_NESTED, MAX_PATTERN, MAX_ITEMS]
ALL_WALK_SCHEMAS = WALK_SCHEMAS + COUNT_WALK_SCHEMAS + OPEN_WALK_SCHEMAS
STRUCT = b'",:}]{[0123456789'


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


@pytest.fixture
def lazy_ctx(byte_tok):
    c = zg.Context(byte_tok, zg.BLG_MODE_LAZY)
    yield c
    assert c.destroy() == zg.BLG_OK


def compile_(ctx, schema):
    return zg.compile_schema(ctx, json.dumps(schema).encode(), profile=b"spec-v1")


def feed(s, data):
    for i, b in enumerate(data):
        ids = s.fill_mask_ids()
        assert b in ids, f"byte {i} {bytes([b])!r} masked after {data[:i]!r}"
        assert s.accept(b) == zg.BLG_OK


def gated(node):
    """The same language with a maxProperties gate on every object schema:
    the completion filter then settles the object by its bounded search."""
    if isinstance(node, dict):
        out = {k: gated(v) for k, v in node.items()}
        if any(k in node for k in ("properties", "patternProperties", "additionalProperties",
                                   "dependencies", "required")):
            out.setdefault("maxProperties", 1000000)
        return out
    if isinstance(node, list):
        return [gated(v) for v in node]
    return node


def oracle_valid(schema, doc: bytes) -> bool:
    """The independent verdict over exact numbers (a walk may spell an
    integer such as 95e4596739, which float turns into inf)."""
    cls = jsonschema.Draft4Validator if schema.get("$schema") == D4 \
        else jsonschema.Draft202012Validator
    with exact_dialects():
        return exact_validator(schema, cls).is_valid(exact_loads(doc.decode("utf-8")))


@pytest.mark.parametrize("case", range(len(MASKS)))
def test_exact_mask_after_prefix(ctx, case):
    schema, prefix, allowed, eos = MASKS[case]
    g = compile_(ctx, schema)
    s = zg.Session(ctx, g)
    try:
        data = prefix if isinstance(prefix, bytes) else prefix.encode()
        want = allowed if isinstance(allowed, bytes) else allowed.encode()
        feed(s, data)
        ids = s.fill_mask_ids()
        assert ids, f"{prefix!r}: empty mask (dead end)"
        got = bytes(sorted(i for i in ids if i < 256))
        assert got == bytes(sorted(want)), f"{prefix!r}: mask {got!r}"
        assert (256 in ids) == eos
    finally:
        s.destroy()
        zg.grammar_release(g)


@pytest.mark.parametrize("case", range(len(REFUSED)))
def test_refused_bytes_after_prefix(ctx, case):
    schema, prefix, refused, admitted = REFUSED[case]
    g = compile_(ctx, schema)
    s = zg.Session(ctx, g)
    try:
        feed(s, prefix.encode())
        ids = s.fill_mask_ids()
        for b in refused.encode():
            assert b not in ids, f"{prefix!r}: {bytes([b])!r} admitted"
        for b in admitted.encode():
            assert b in ids, f"{prefix!r}: {bytes([b])!r} refused"
    finally:
        s.destroy()
        zg.grammar_release(g)


def test_reference_witnesses_the_review_counterexamples():
    # The independent reference finds the completions the masks of the
    # earlier builds refused (C1), and none where the masks rightly refuse.
    ref = cref.Reference(FALLBACK_FINITE)
    assert ref.witness(b'{"":0,')[0] == "found"
    assert ref.witness(b'{"":0,"a')[0] == "found"
    assert ref.witness(b'{"":0,"b')[0] == "none"
    assert cref.Reference(FALLBACK_LITERAL).witness(b'{"":0,')[0] == "found"
    assert cref.Reference(FALLBACK_TAKEN).witness(b'{"":0,"a":0,')[0] == "none"
    # C2: a declared key that skips the dependency name of a pending
    # obligation has no completion in the language (schema order); in an
    # open object the bounded candidate set is not exhausted, so only the
    # absence of a witness is asserted.
    assert cref.Reference(REQUIRED_OWN_DEP_OPEN).witness(b'{"x"')[0] != "found"
    assert cref.Reference(REQUIRED_OWN_DEP).witness(b'{"x"')[0] == "none"
    assert cref.Reference(DEP_CHAIN).witness(b'{"y":"",')[0] == "none"
    assert cref.Reference(DEP_CHAIN).witness(b'{"y":""}')[0] == "found"
    # D: the review's maxProperties example - the comma after the only
    # allowed member has no completion; the close has one.
    assert cref.Reference(MAX1).witness(b'{"x":1}')[0] == "found"
    assert cref.Reference(MAX1).witness(b'{"x":1,')[0] == "none"
    assert cref.Reference(MAX1_REQUIRED).witness(b'{"a')[0] == "found"
    assert cref.Reference(MAX1_REQUIRED).witness(b'{"b')[0] != "found"
    assert cref.Reference(MAX_REQUIRED_DEP).witness(b'{"s":1,"r')[0] == "found"


def test_extension_of_a_taken_pattern_key_takes_the_fallback(lazy_ctx):
    # `k1` is seen; `"k1` cannot close, but `k1x` is a fresh fallback key.
    g = compile_(lazy_ctx, PAT_FALLBACK)
    s = zg.Session(lazy_ctx, g)
    try:
        feed(s, b'{"k1":"","k1')
        ids = s.fill_mask_ids()
        assert ord('"') not in ids and ord("x") in ids
        feed(s, b'x":1}')
        assert s.can_end()
    finally:
        s.destroy()
        zg.grammar_release(g)


def walk(s, rnd, max_steps=400):
    doc = bytearray()
    for _ in range(max_steps):
        status, words = s.fill_mask_words()
        assert status == zg.BLG_OK, f"fill_mask {status} after {bytes(doc)!r}"
        ids = [i for i in range(258) if words[i // 32] >> (i % 32) & 1]
        bs = [i for i in ids if i < 256]
        assert ids, f"empty mask after {bytes(doc)!r}"
        if 256 in ids and (not bs or rnd.random() < 0.6):
            return bytes(doc)
        pref = [b for b in bs if b in STRUCT]
        b = rnd.choice(pref) if pref and rnd.random() < 0.6 else rnd.choice(bs)
        assert s.accept(b) == zg.BLG_OK
        doc.append(b)
    return None


@pytest.mark.parametrize("idx", range(len(ALL_WALK_SCHEMAS)))
def test_walks_never_dead_end(lazy_ctx, idx):
    schema = ALL_WALK_SCHEMAS[idx]
    min_done = 0 if schema in OPEN_WALK_SCHEMAS else 20
    ref = cref.Reference(schema)
    g = compile_(lazy_ctx, schema)
    done = 0
    try:
        for seed in range(40):
            s = zg.Session(lazy_ctx, g)
            try:
                doc = walk(s, random.Random(seed))
            finally:
                s.destroy()
            if doc is not None:
                done += 1
                assert oracle_valid(schema, doc), doc
                assert ref.in_language(doc), doc
    finally:
        zg.grammar_release(g)
    assert done >= min_done


@pytest.mark.parametrize("idx", range(len(WALK_SCHEMAS)))
def test_certificates_agree_with_the_search(lazy_ctx, idx):
    schema = WALK_SCHEMAS[idx]
    g1 = compile_(lazy_ctx, schema)
    g2 = compile_(lazy_ctx, gated(schema))
    compared = 0
    try:
        for seed in range(30):
            rnd = random.Random(1000 + seed)
            s1, s2 = zg.Session(lazy_ctx, g1), zg.Session(lazy_ctx, g2)
            follow = True
            doc = bytearray()
            try:
                for _ in range(300):
                    st1, w1 = s1.fill_mask_words()
                    assert st1 == zg.BLG_OK, f"fill_mask {st1} after {bytes(doc)!r}"
                    agreed = False
                    if follow:
                        st2, w2 = s2.fill_mask_words()
                        if st2 == zg.BLG_OK:
                            assert w1 == w2, f"masks differ after {bytes(doc)!r}"
                            compared += 1
                            agreed = True
                        elif st2 != zg.BLG_ERR_RESOURCE_LIMIT:
                            pytest.fail(f"gated fill_mask {st2} after {bytes(doc)!r}")
                    ids = [i for i in range(258) if w1[i // 32] >> (i % 32) & 1]
                    bs = [i for i in ids if i < 256]
                    if 256 in ids and (not bs or rnd.random() < 0.5):
                        break
                    pref = [b for b in bs if b in STRUCT]
                    b = rnd.choice(pref) if pref and rnd.random() < 0.6 else rnd.choice(bs)
                    assert s1.accept(b) == zg.BLG_OK
                    if follow and s2.accept(b) != zg.BLG_OK:
                        # Only a gated session whose mask was not computed
                        # (RESOURCE_LIMIT) may refuse a byte; it is then no
                        # longer compared.
                        assert not agreed, f"gated accept refused {bytes([b])!r} after {bytes(doc)!r}"
                        follow = False
                    doc.append(b)
            finally:
                s1.destroy()
                s2.destroy()
    finally:
        zg.grammar_release(g1)
        zg.grammar_release(g2)
    assert compared >= 100


def reference_walk(s, ref, rnd, max_steps=160):
    """One walk with the independent check at every step: no byte the mask
    refuses may have a completion witness (a false dead end). Returns the
    document (None at the step cap) and the numbers of steps whose chosen
    byte a witness confirmed and of those it did not (unverified)."""
    doc = bytearray()
    verified = unverified = 0
    for _ in range(max_steps):
        status, words = s.fill_mask_words()
        assert status == zg.BLG_OK, f"fill_mask {status} after {bytes(doc)!r}"
        ids = {i for i in range(258) if words[i // 32] >> (i % 32) & 1}
        cands = ref.candidates(bytes(doc))
        for b in cands:
            if b in ids:
                continue
            got, wit = ref.witness(bytes(doc) + bytes([b]))
            assert got != "found", f"{bytes([b])!r} masked after {bytes(doc)!r}; witness {wit!r}"
        bs = sorted(i for i in ids if i < 256)
        assert bs or 256 in ids, f"empty mask after {bytes(doc)!r}"
        if 256 in ids and (not bs or rnd.random() < 0.5):
            return bytes(doc), verified, unverified
        # Prefer bytes the reference can follow, so its checks stay live.
        near = [b for b in bs if b in cands]
        b = rnd.choice(near) if near and rnd.random() < 0.85 else rnd.choice(bs)
        if ref.witness(bytes(doc) + bytes([b]))[0] == "found":
            verified += 1
        else:
            unverified += 1
        assert s.accept(b) == zg.BLG_OK
        doc.append(b)
    return None, verified, unverified


@pytest.mark.parametrize("idx", range(len(ALL_WALK_SCHEMAS)))
def test_masked_bytes_have_no_completion_witness(lazy_ctx, idx):
    # The independent check (completion_reference): masks must never refuse
    # a byte that a validator-judged document of the language continues
    # with. A walk step whose chosen byte no witness confirms is counted
    # as unverified, not as agreement; most steps must be confirmed for
    # the check to say anything.
    schema = ALL_WALK_SCHEMAS[idx]
    ref = cref.Reference(schema)
    g = compile_(lazy_ctx, schema)
    verified = unverified = 0
    try:
        for seed in range(6):
            s = zg.Session(lazy_ctx, g)
            try:
                doc, v, u = reference_walk(s, ref, random.Random(2000 + seed))
            finally:
                s.destroy()
            verified += v
            unverified += u
            if doc is not None:
                assert ref.in_language(doc), doc
    finally:
        zg.grammar_release(g)
    assert verified >= 3 * unverified and verified >= 30, (verified, unverified)
