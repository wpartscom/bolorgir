"""MaskBench serializer hook end-to-end: runner -> adapter -> serializer -> engine.

Regression coverage (2026-09-22):
the MaskBench runner hands BlgEngine.serialize_instance the already-parsed
test["data"]; the adapter must use the serializer's value entry point, so a
string instance is a JSON string value and is never re-parsed as JSON text
("1" stays the string "1", "hello" serializes instead of failing). Each test
runs the runner's own protocol on a byte tokenizer: serialize_instance hook,
one token per payload byte, commit_token against the mask, then can_end.

Needs the native library (BLG_LIB_PATH) and the bolorgir package (python/).
"""

import importlib.util
import json
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python"))

blg = pytest.importorskip("bolorgir", reason="bolorgir package/core unavailable")

from bolorgir.serializer import SerializationIncompatibleError  # noqa: E402


def _load_adapter_class():
    """Load benchmarks/maskbench/blg_engine.py with a stub maskbench.engine.

    The upstream `Engine` base class lives in the external jsonschemabench
    checkout; here a minimal stand-in provides __init__ and log_single.
    """
    maskbench_dir = os.path.join(ROOT, "benchmarks", "maskbench")
    pkg = types.ModuleType("maskbench")
    pkg.__path__ = [maskbench_dir]
    engine_mod = types.ModuleType("maskbench.engine")

    class Engine:
        def __init__(self):
            self.tokenizer = None

        def log_single(self, msg):
            pass

    engine_mod.Engine = Engine
    sys.modules["maskbench"] = pkg
    sys.modules["maskbench.engine"] = engine_mod
    spec = importlib.util.spec_from_file_location(
        "maskbench.blg_engine", os.path.join(maskbench_dir, "blg_engine.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["maskbench.blg_engine"] = mod
    spec.loader.exec_module(mod)
    return mod.BlgEngine


BlgEngine = _load_adapter_class()

STRING_SCHEMA = {"type": "string"}
NUMBER_SCHEMA = {"type": "number"}
AB_SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"],
    "additionalProperties": False,
}
INT_ARRAY_SCHEMA = {"type": "array", "items": {"type": "integer"}}


@pytest.fixture
def make_engine():
    """BlgEngine wired like the runner does, but with a byte tokenizer."""
    made = []

    def factory(schema, serializer=True):
        engine = BlgEngine(serializer=serializer)
        engine.bundle = blg.TokenizerBundle.from_token_bytes(
            [bytes([b]) for b in range(256)]
        )
        engine.engine = blg.Engine(mode="lazy", tokenizer=engine.bundle)
        engine.compile_grammar(schema)
        made.append(engine)
        return engine

    yield factory
    for engine in made:
        try:
            if engine.constraint is not None:
                engine.constraint.close()
            engine.engine.close()
        except Exception:
            pass


def run_instance(engine, data):
    """The runner's per-test loop (maskbench runner.process_file, --compact).

    Returns (payload, accepted, finished): payload is the instance string the
    runner would tokenize; accepted/finished are the token-stream verdict.
    """
    payload = engine.serialize_instance(data)
    assert payload is not None  # serializer on: the hook never falls back
    engine.reset()
    accepted = True
    for byte in payload.encode("utf-8"):
        engine.compute_mask()
        if not engine.commit_token(byte):
            accepted = False
            break
    finished = accepted and engine.session.can_end()
    return payload, accepted, finished


# ---------------------------------------------------------------------------
# Root strings, including JSON look-alikes: value and verdict preserved
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "hello", "1", "true", "[1]", '"x"', "", "null", "{}", "café",
])
def test_root_string_value_preserved(make_engine, value):
    engine = make_engine(STRING_SCHEMA)
    payload, accepted, finished = run_instance(engine, value)
    # R4: the payload is the compact serialization of the STRING value,
    # never the re-parsed JSON text ("1" must not become the number 1).
    assert payload == json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    decoded = json.loads(payload)
    assert decoded == value and type(decoded) is str
    # verdict: a string is valid against {"type": "string"} and completes.
    assert accepted and finished


@pytest.mark.parametrize("value", [1, 2.5, True, None, [1], {"a": 1}])
def test_non_string_kept_but_rejected_by_string_schema(make_engine, value):
    engine = make_engine(STRING_SCHEMA)
    payload, accepted, finished = run_instance(engine, value)
    # The value survives serialization unchanged...
    assert json.loads(payload) == value
    # ...and the invalid verdict comes from the engine, not the serializer.
    assert not (accepted and finished)


# ---------------------------------------------------------------------------
# Root numbers / booleans / null / objects / arrays
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [0, 1, -7, 2.5, -0.0, 1e2, 10**30])
def test_root_number_value_preserved(make_engine, value):
    engine = make_engine(NUMBER_SCHEMA)
    payload, accepted, finished = run_instance(engine, value)
    decoded = json.loads(payload)
    assert decoded == value and type(decoded) is type(value)
    assert accepted and finished


@pytest.mark.parametrize("value", [True, False])
def test_root_boolean_value_preserved(make_engine, value):
    engine = make_engine({"type": "boolean"})
    payload, accepted, finished = run_instance(engine, value)
    assert json.loads(payload) is value
    assert accepted and finished


def test_root_object_reordered_value_preserved(make_engine):
    engine = make_engine(AB_SCHEMA)
    payload, accepted, finished = run_instance(engine, {"b": 2, "a": 1})
    assert payload == '{"a":1,"b":2}'  # schema declaration order
    assert json.loads(payload) == {"b": 2, "a": 1}
    assert accepted and finished


def test_root_object_missing_required_rejected(make_engine):
    engine = make_engine(AB_SCHEMA)
    payload, accepted, finished = run_instance(engine, {"a": 1})
    assert json.loads(payload) == {"a": 1}  # value preserved
    assert not (accepted and finished)  # verdict from the engine


def test_root_array_value_preserved(make_engine):
    engine = make_engine(INT_ARRAY_SCHEMA)
    payload, accepted, finished = run_instance(engine, [1, 2])
    assert payload == "[1,2]"
    assert accepted and finished


def test_root_array_wrong_item_rejected(make_engine):
    engine = make_engine(INT_ARRAY_SCHEMA)
    payload, accepted, finished = run_instance(engine, [1, "x"])
    assert json.loads(payload) == [1, "x"]
    assert not (accepted and finished)


# ---------------------------------------------------------------------------
# Hook protocol: off-switch and the section 7 refusal bucket
# ---------------------------------------------------------------------------

def test_serializer_off_falls_back_to_runner(make_engine):
    engine = make_engine(STRING_SCHEMA, serializer=False)
    assert engine.serialize_instance("hello") is None


def test_unpreservable_value_raises_marked_refusal(make_engine):
    engine = make_engine(STRING_SCHEMA)
    with pytest.raises(SerializationIncompatibleError) as exc_info:
        engine.serialize_instance("a\ud800")
    assert exc_info.value.serialization_incompatible is True
    assert "surrogate" in exc_info.value.reason
