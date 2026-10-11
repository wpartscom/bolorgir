"""spec-v1 generative test: random schemas (schema_gen.SchemaGenV1) and the
numeric-conjunction tables of ADR-0011, K generation passes each on three
byte-complete vocabularies. Passes run through the shared driver
(tests/generation_passes.py) and are classified by tests/semantic_outcomes.py,
the same code as the coverage harness (benchmarks/bench_jsb_coverage.py).

Fails on: any engine_defect (including a refused masked token, an empty
mask, a failing blg_finish after EOS, a token outside the mask that is not
refused with INVALID_TOKEN or that changes the state); any validator_error;
an invalid_document unless tests/fuzz/known_deviations_spec_v1.json lists
the schema and one of its listed deviations explains that document
(generation_passes.is_known_deviation); engine_unavailable on a table
schema. Other engine_unavailable passes are
reported in tests/fuzz/artifacts/generate_spec_v1_<vocab>.json.

Fast mode (default) uses a fixed seed and fits the CI budget; a random
schema stops after its first engine_unavailable pass there.
BLG_FUZZ_LONG=1 runs more schemas and passes, all of them.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys
from collections import Counter

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

pytest.importorskip("numpy")
pytest.importorskip("jsonschema")
pytest.importorskip("regress")

import fuzz_common  # noqa: E402
import schema_gen  # noqa: E402

blg = fuzz_common.blg_ctypes
import generation_passes as gp  # noqa: E402
import semantic_outcomes as so  # noqa: E402
from reference import TokenizerSpec  # noqa: E402

LONG = os.environ.get("BLG_FUZZ_LONG") == "1"
N_SCHEMAS = 400 if LONG else 40
K = 20 if LONG else 6
SEED = int(os.environ.get("BLG_FUZZ_SEED", "20261002"))

VOCABS = {
    "bytes": (),
    "bytes+digits": (b"10", b"123", b"42", b"2024", b"0.5", b"1e5", b"-1", b"00"),
    "bytes+json": (b'{"', b'":', b'",', b'"}', b'":"', b"},", b"],", b"[{", b"true",
                   b"false", b"null"),
}

TABLE_SCHEMAS = [
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

KNOWN_DEVIATIONS_PATH = os.path.join(_HERE, "known_deviations_spec_v1.json")


def _known_deviations():
    with open(KNOWN_DEVIATIONS_PATH, encoding="utf-8") as f:
        return {e["schema_sha256"]: e["deviations"] for e in json.load(f)["entries"]}


def _tolerated(known, sha, text, rec, doc):
    return rec["outcome"] == "invalid_document" and any(
        gp.is_known_deviation(kind, text, doc) for kind in known.get(sha, ()))


def _vocab(extra) -> TokenizerSpec:
    tokens = tuple(bytes([i]) for i in range(256)) + tuple(extra) + (b"", b"")
    n = len(tokens)
    return TokenizerSpec(tokens=tokens, eos_ids=(n - 2,), special_ids=(n - 1,)).validate()


def _schemas(vocab_name):
    rng = random.Random(f"{SEED}/{vocab_name}")
    out = [(json.dumps(s), True) for s in TABLE_SCHEMAS]
    out += [(schema_gen.gen_schema_v1(rng), False) for _ in range(N_SCHEMAS)]
    return out


@pytest.mark.parametrize("vocab_name", sorted(VOCABS))
def test_generate_spec_v1(vocab_name):
    spec = _vocab(VOCABS[vocab_name])
    vocab = gp.Vocab(spec)
    known = _known_deviations()
    ctxs = [blg.Context(spec, blg.BLG_MODE_LAZY), blg.Context(spec, blg.BLG_MODE_ADAPTIVE)]
    compile_status = Counter()
    schema_outcomes = Counter()
    pass_outcomes = Counter()
    unavailable = []
    failures = []
    try:
        for i, (text, is_table) in enumerate(_schemas(vocab_name)):
            ctx = ctxs[i % 2]
            sha = hashlib.sha256(text.encode()).hexdigest()
            try:
                g = blg.compile_schema(ctx, text.encode(), profile=b"spec-v1")
            except blg.CoreFailure as e:
                compile_status[blg.STATUS_NAMES.get(e.status, e.status)] += 1
                if is_table:
                    failures.append({"schema": text, "issue": "table schema refused at compile",
                                     "status": blg.STATUS_NAMES.get(e.status, e.status)})
                continue
            compile_status["OK"] += 1
            try:
                validator, verr = gp.make_reference_validator(text)
                if validator is None:
                    schema_outcomes["skipped"] += 1
                    continue
                hints = gp.schema_hints(json.loads(text))
                outs = []
                for k in range(K):
                    seed = SEED * 1000003 + i * 101 + k
                    rec, doc = gp.run_pass(
                        ctx, g, vocab, random.Random(seed), seed=seed, validator=validator,
                        policy="uniform" if k % 2 == 0 else "biased", hints=hints,
                        max_steps=512, max_seconds=5.0, probe_invalid=0.05)
                    outs.append(rec["outcome"])
                    pass_outcomes[rec["outcome"]] += 1
                    case = {"schema": text, "schema_sha256": sha, "record": rec,
                            "document": doc.decode("utf-8", "replace")[:400]}
                    if rec["outcome"] == "engine_unavailable":
                        unavailable.append(case)
                        if is_table:
                            failures.append(dict(case, issue="engine_unavailable on a table schema"))
                        elif not LONG:
                            # Later passes mostly hit the same RESOURCE_LIMIT
                            # (about 1 s each); fast mode moves on.
                            break
                    elif rec["outcome"] == "engine_defect":
                        failures.append(dict(case, issue="engine_defect"))
                    elif rec["outcome"] in ("invalid_document", "validator_error") \
                            and not _tolerated(known, sha, text, rec, doc):
                        failures.append(dict(case, issue=rec["outcome"]))
                schema_outcomes[so.aggregate_schema_outcome(outs)] += 1
            finally:
                blg.grammar_release(g)
    finally:
        for c in ctxs:
            c.destroy()
    report = {
        "vocab": vocab_name, "seed": SEED, "long": LONG, "passes_per_schema": K,
        "compile_status": dict(compile_status), "schema_outcomes": dict(schema_outcomes),
        "pass_outcomes": dict(pass_outcomes), "engine_unavailable": unavailable[:50],
        "failures": failures[:50], "n_failures": len(failures),
    }
    fuzz_common.save_results(f"generate_spec_v1_{vocab_name}.json", report)
    assert compile_status["OK"] >= 0.8 * (N_SCHEMAS + len(TABLE_SCHEMAS)), compile_status
    assert not failures, json.dumps(failures[:5], ensure_ascii=False, indent=1)


def test_known_deviation_tolerance():
    text = json.dumps({"$schema": "http://json-schema.org/draft-04/schema#", "type": "integer"})
    sha = hashlib.sha256(text.encode()).hexdigest()
    known = {sha: ["draft04_integer_spelling"]}
    invalid = {"outcome": "invalid_document"}
    assert _tolerated(known, sha, text, invalid, b"1.0")
    assert not _tolerated(known, sha, text, invalid, b"1.5")
    assert not _tolerated(known, sha, text, invalid, b"true")
    assert not _tolerated({}, sha, text, invalid, b"1.0")
    assert not _tolerated({sha: ["corpus_label"]}, sha, text, invalid, b"1.0")
    assert not _tolerated(known, sha, text, {"outcome": "validator_error"}, b"1.0")
