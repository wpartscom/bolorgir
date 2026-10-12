"""vLLM structured-output backend adapter (ADR-0016).

Two layers of testing:

- functional: the adapter is driven through the vLLM interface on a
  byte-complete tokenizer (bit 1 = allowed, rows of an int32 tensor),
  including masks, terminals (EOS and stop tokens), rollback,
  draft validation, the failure policy and the vocabulary-padding tail;
  these run everywhere (``vllm_stub`` reproduces the upstream interface
  and numpy stands in for the tensor when torch is absent).
- integration identity: with ``BLG_VLLM_SRC`` pointing at the pinned
  v0.31.0 source tree, the adapter's member set is compared against the
  actual interface source and the tracked patch is checked with
  ``git apply --check`` against that tree.

The real HF tokenizer bridge (``TokenizerBundle.from_hf``) is covered
when transformers and tokenizers are installed; without them that test
skips, the functional tests keep running on a native byte table.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import subprocess
import types

import numpy as np
import pytest

from vllm_stub import install as install_vllm_stub

USING_STUB = install_vllm_stub()
bv = importlib.import_module("bolorgir.vllm")
import bolorgir  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PATH = os.path.join(ROOT, "integrations", "vllm", "vllm-0.31.0-bolorgir.patch")
PATCH_SHA256 = "d0b8ea80d3ad02946ceab71ee93da811ec6dc9250e63481aedc932b1a1aa065a"
VLLM_SRC = os.environ.get("BLG_VLLM_SRC")

EOS = 256
SPECIAL = 257
X = ord("x")


# ---------------------------------------------------------------------------
# Tokenizers and bitmask stand-ins
# ---------------------------------------------------------------------------

def _byte_bundle() -> bolorgir.TokenizerBundle:
    tokens = [bytes([i]) for i in range(256)]
    tokens += [b"\x00", b"\x00"]  # EOS and special bytes never enter a document
    return bolorgir.TokenizerBundle.from_token_bytes(
        tokens, eos_ids=[EOS], special_ids=[SPECIAL])


class _Row:
    def __init__(self, arr):
        self._arr = arr

    def numpy(self):
        return self._arr


class FakeBitmask:
    """Stands in for the torch int32 tensor: row i is bitmask[i]."""

    def __init__(self, rows: int, words: int):
        self.arr = np.full((rows, words), -1, dtype=np.int32)

    def __getitem__(self, i: int) -> _Row:
        return _Row(self.arr[i])


def _row_of(grammar, vocab_size: int, index: int = 0, rows: int = 1) -> np.ndarray:
    words = (vocab_size + 31) // 32
    bitmask = FakeBitmask(rows, words)
    grammar.fill_bitmask(bitmask, index)
    return bitmask.arr[index]


def _allowed(row: np.ndarray, vocab_size: int) -> set:
    bits = np.unpackbits(row.view(np.uint8), bitorder="little")
    return {i for i in range(vocab_size) if bits[i]}


def make_backend(monkeypatch, *, vocab_size: int | None = None,
                 num_spec_tokens: int = 0, bundle=None):
    if bundle is None:
        bundle = _byte_bundle()
        monkeypatch.setattr(bv, "tokenizer_bundle_of", lambda tok: bundle)
    speculative = None
    if num_spec_tokens:
        speculative = types.SimpleNamespace(num_speculative_tokens=num_spec_tokens)
    cfg = types.SimpleNamespace(speculative_config=speculative)
    return bv.BolorgirBackend(
        cfg, tokenizer=object(), vocab_size=vocab_size or bundle.vocab_size)


def json_backend(monkeypatch, schema, **kw):
    backend = make_backend(monkeypatch, **kw)
    grammar = backend.compile_grammar(bv.StructuredOutputOptions.JSON,
                                      json.dumps(schema))
    return backend, grammar


def accept_bytes(grammar, text: bytes, request_id: str = "r0") -> bool:
    return grammar.accept_tokens(request_id, list(text))


# ---------------------------------------------------------------------------
# Structural tags (ADR-0016): the pinned agent-path spec shapes
# ---------------------------------------------------------------------------

SET_CODE_PARAMS = {
    "type": "object",
    "properties": {"code": {"type": "string", "enum": ["zq-17-wombat"]}},
    "required": ["code"],
    "additionalProperties": False,
}


def hermes_structural_tag(name: str = "set_code", params=None, **fmt):
    """The spec the pinned vLLM hermes builder produces for ``auto``:
    a triggered tag per tool in both begin formats of the parser."""
    params = SET_CODE_PARAMS if params is None else params
    tags = []
    for begin_head, end in (('<tool_call>\n{"name": "', "}\n</tool_call>"),
                            ('<tool_call>{"name": "', "}</tool_call>")):
        tags.append({
            "type": "tag",
            "begin": f'{begin_head}{name}", "arguments": ',
            "content": {"type": "json_schema", "json_schema": params},
            "end": end,
        })
    return {"type": "structural_tag",
            "format": {"type": "triggered_tags", "triggers": ["<tool_call>"],
                       "tags": tags, **fmt}}


def required_structural_tag(name: str = "set_code", params=None, **fmt):
    spec = hermes_structural_tag(name, params)
    fmt = {"separator": "", "at_least_one": True, **fmt}
    spec["format"] = {"type": "tags_with_separator",
                      "tags": spec["format"]["tags"], **fmt}
    return spec


# ---------------------------------------------------------------------------
# Interface and artifact identity
# ---------------------------------------------------------------------------

def test_interface_matches_the_pinned_vllm_source():
    if not VLLM_SRC:
        pytest.skip("BLG_VLLM_SRC is not set: cannot compare with the pinned "
                    "vLLM source (the stand-in covers the runtime behavior)")
    src = open(os.path.join(VLLM_SRC, "vllm", "v1", "structured_output",
                            "backend_types.py"), encoding="utf-8").read()
    tree = ast.parse(src)

    def abstract_methods(cls_name):
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls_name:
                out = set()
                for item in node.body:
                    if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    if any(getattr(d, "id", getattr(d, "attr", "")) == "abstractmethod"
                           for d in item.decorator_list):
                        out.add(item.name)
                return out
        raise AssertionError(f"{cls_name} not found in the pinned source")

    real_grammar = abstract_methods("StructuredOutputGrammar")
    real_backend = abstract_methods("StructuredOutputBackend")
    ours = set(bv.BolorgirGrammar.__abstractmethods__) | {
        n for n in dir(bv.BolorgirGrammar) if not n.startswith("_")}
    # Every real abstract method must be implemented by the adapter.
    assert not (real_grammar - ours), real_grammar - ours
    backend_ours = set(dir(bv.BolorgirBackend))
    assert not (real_backend - backend_ours), real_backend - backend_ours

    # The enum members the adapter dispatches on.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "StructuredOutputOptions":
            members = {t.targets[0].id for t in node.body
                       if isinstance(t, ast.Assign)
                       and isinstance(t.targets[0], ast.Name)}
            assert members == {m.name for m in bv.StructuredOutputOptions}
            break
    else:
        raise AssertionError("StructuredOutputOptions not found in the pinned source")

    # The tracked patch must apply cleanly to the pinned tree.
    subprocess.run(["git", "-C", VLLM_SRC, "apply", "--check", PATCH_PATH],
                   check=True)


def test_tracked_patch_identity_and_targets():
    data = open(PATCH_PATH, "rb").read()
    assert hashlib.sha256(data).hexdigest() == PATCH_SHA256
    text = data.decode()
    for target in ("vllm/config/structured_outputs.py",
                   "vllm/sampling_params.py",
                   "vllm/v1/request.py",
                   "vllm/v1/structured_output/__init__.py"):
        assert f"diff --git a/{target} b/{target}" in text
    assert text.count('"bolorgir"') >= 1
    assert "from bolorgir.vllm import" in text
    # The explicit request-id mapping: the manager passes the exposed id
    # (the response id) next to the engine-internal one, carried on the
    # engine Request by the request.py hunk.
    assert "external_request_id=getattr(request, \"external_req_id\", None)" in text
    assert "external_req_id=request.external_req_id" in text


# ---------------------------------------------------------------------------
# Request validation and explicit refusals
# ---------------------------------------------------------------------------

def _params(**kw):
    base = dict(json=None, regex=None, choice=None, grammar=None,
                json_object=None, structural_tag=None, whitespace_pattern=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_validate_request_refusals():
    exc = bv.validate_bolorgir_request  # shorthand
    with pytest.raises(bv.VLLMValidationError):
        exc(_params(grammar="root ::= 'a'"))
    with pytest.raises(bv.VLLMValidationError):
        exc(_params(structural_tag="   "))
    with pytest.raises(bv.VLLMValidationError):
        exc(_params(json='{"type": "object"}', whitespace_pattern=" *"))
    with pytest.raises(bv.VLLMValidationError):
        exc(_params(json="   "))
    with pytest.raises(bv.VLLMValidationError):
        exc(_params(choice=[]))
    with pytest.raises(bv.VLLMValidationError):
        exc(_params(choice=["a", 1]))
    with pytest.raises(bv.VLLMValidationError):
        exc(_params(regex="a\x00b"))
    # The supported kinds pass; a structural tag is validated by the
    # engine compile (unsupported variants are request errors there).
    exc(_params(json={"type": "object"}))
    exc(_params(choice=["reply", "escalate"]))
    exc(_params(regex="[a-z]+"))
    exc(_params(structural_tag=json.dumps(hermes_structural_tag())))
    exc(types.SimpleNamespace(structured_outputs=_params(json={"type": "integer"})))


def test_compile_refuses_grammar_and_nonsense_structural_tag(monkeypatch):
    backend = make_backend(monkeypatch)
    with pytest.raises(bv.BolorgirRequestError):
        backend.compile_grammar(bv.StructuredOutputOptions.GRAMMAR, "root ::= 'a'")
    with pytest.raises(bv.BolorgirRequestError):
        backend.compile_grammar(bv.StructuredOutputOptions.STRUCTURAL_TAG, "{}")
    backend.destroy()


def test_backend_refuses_an_unsupported_tokenizer():
    class NotAnHFTokenizer:
        def get_vocab(self):
            return {"a": 0, "b": 1}

    cfg = types.SimpleNamespace(speculative_config=None)
    with pytest.raises(bv.BolorgirBackendError):
        bv.BolorgirBackend(cfg, tokenizer=NotAnHFTokenizer(), vocab_size=2)


def test_backend_refuses_model_vocab_smaller_than_tokenizer(monkeypatch):
    bundle = _byte_bundle()
    monkeypatch.setattr(bv, "tokenizer_bundle_of", lambda tok: bundle)
    cfg = types.SimpleNamespace(speculative_config=None)
    with pytest.raises(bv.BolorgirBackendError):
        bv.BolorgirBackend(cfg, tokenizer=object(), vocab_size=bundle.vocab_size - 1)


# ---------------------------------------------------------------------------
# JSON Schema: masks, completion, a controlled forbidden candidate
# ---------------------------------------------------------------------------

def test_json_schema_mask_completion_and_forbidden_candidate(monkeypatch):
    backend, grammar = json_backend(
        monkeypatch, {"type": "integer", "minimum": 10, "maximum": 42})
    vocab = backend.model_vocab_size

    row = _row_of(grammar, vocab)
    allowed = _allowed(row, vocab)
    assert ord("4") in allowed and ord("1") in allowed  # 10..42 can start here
    assert ord("9") not in allowed                     # 9 > 42
    assert EOS not in allowed                          # nothing complete yet

    # A controlled forbidden candidate with a dominant score never wins:
    # applying the mask removes it from the sampled set.
    forbidden = ord("9")
    logits = np.full(vocab, -3.0, dtype=np.float64)
    logits[forbidden] = 1000.0
    bits = np.zeros(vocab, dtype=bool)
    bits[list(allowed)] = True
    logits[~bits] = -np.inf
    assert int(np.argmax(logits)) != forbidden
    assert int(np.argmax(logits)) in allowed

    assert grammar.accept_tokens("r0", [ord("4")])
    allowed = _allowed(_row_of(grammar, vocab), vocab)
    assert ord("0") in allowed                          # 40
    assert ord("e") in allowed                          # 4e1 = 40
    assert ord("9") not in allowed                      # 49 > 42
    assert EOS not in allowed

    assert grammar.accept_tokens("r0", [ord("2")])
    allowed = _allowed(_row_of(grammar, vocab), vocab)
    assert EOS in allowed                               # 42 is complete
    assert ord("a") not in allowed

    # The forbidden candidate at the completing step: the mask keeps a
    # non-numeric byte out even with a dominant score.
    forbidden = ord("a")
    logits = np.full(vocab, -3.0, dtype=np.float64)
    logits[forbidden] = 1000.0
    logits[[i for i in range(vocab) if i not in allowed]] = -np.inf
    assert int(np.argmax(logits)) != forbidden

    assert not grammar.is_terminated()
    assert grammar.accept_tokens("r0", [EOS])
    assert grammar.is_terminated()
    # After termination the row is unrestricted and later tokens are ignored.
    row = _row_of(grammar, vocab)
    assert _allowed(row, vocab) == set(range(vocab))
    assert grammar.accept_tokens("r0", [ord("7")])
    backend.destroy()


def test_json_object_request(monkeypatch):
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(bv.StructuredOutputOptions.JSON_OBJECT, "")
    vocab = backend.model_vocab_size
    assert grammar.accept_tokens("r", [ord("{")])
    row = _row_of(grammar, vocab)
    assert ord('"') in _allowed(row, vocab)
    assert grammar.accept_tokens("r", [ord("}")])
    assert _allowed(_row_of(grammar, vocab), vocab) == {EOS}
    backend.destroy()


def test_choice_request(monkeypatch):
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.CHOICE, json.dumps(["reply", "escalate"]))
    vocab = backend.model_vocab_size
    row = _row_of(grammar, vocab)
    assert set(b"re") & _allowed(row, vocab) or ord("r") in _allowed(row, vocab)
    assert ord("r") in _allowed(row, vocab)
    assert accept_bytes(grammar, b"reply")
    assert _allowed(_row_of(grammar, vocab), vocab) == {EOS}
    assert grammar.accept_tokens("r", [EOS])
    assert grammar.is_terminated()
    backend.destroy()


def test_regex_request(monkeypatch):
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(bv.StructuredOutputOptions.REGEX, "a+b{2}")
    vocab = backend.model_vocab_size
    assert accept_bytes(grammar, b"aab")
    row = _row_of(grammar, vocab)
    assert _allowed(row, vocab) == {ord("b")}
    assert accept_bytes(grammar, b"b")
    assert _allowed(_row_of(grammar, vocab), vocab) == {EOS}
    assert grammar.accept_tokens("r", [EOS])
    backend.destroy()


def test_regex_refusal_is_a_request_error(monkeypatch):
    backend = make_backend(monkeypatch)
    with pytest.raises(bv.BolorgirRequestError):
        backend.compile_grammar(bv.StructuredOutputOptions.REGEX, "a(?=b)")
    backend.destroy()


# ---------------------------------------------------------------------------
# Terminal tokens (EOS and the request stop set)
# ---------------------------------------------------------------------------

def test_stop_tokens_replace_the_terminal_set(monkeypatch):
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.JSON,
        json.dumps({"type": "integer", "minimum": 10, "maximum": 42}),
        stop_token_ids={EOS, X})
    vocab = backend.model_vocab_size

    # Mid-generation the stop ids are masked out.
    assert grammar.accept_tokens("r", [ord("4")])
    allowed = _allowed(_row_of(grammar, vocab), vocab)
    assert ord("0") in allowed
    assert X not in allowed and EOS not in allowed

    # A terminal token before completion is refused (the caller ignored
    # the mask); the state is unchanged.
    assert not grammar.accept_tokens("r", [X])
    assert not grammar.is_terminated()

    # At completion the mask offers the terminal set next to the remaining
    # legal continuations ("420e-1" = 42 keeps digits alive).
    assert grammar.accept_tokens("r", [ord("2")])
    allowed = _allowed(_row_of(grammar, vocab), vocab)
    assert {EOS, X} <= allowed
    assert ord("a") not in allowed

    # The user stop token terminates the request.
    assert grammar.accept_tokens("r", [X])
    assert grammar.is_terminated()
    backend.destroy()


def test_mid_generation_mask_excludes_nonstop_terminal(monkeypatch):
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.REGEX, "a+", stop_token_ids={EOS, X})
    vocab = backend.model_vocab_size
    assert _allowed(_row_of(grammar, vocab), vocab) == {ord("a")}
    assert accept_bytes(grammar, b"a")
    allowed = _allowed(_row_of(grammar, vocab), vocab)
    assert allowed == {ord("a"), EOS, X}  # the language can end now
    backend.destroy()


def test_terminal_at_a_completing_state_is_accepted_by_drafts(monkeypatch):
    # A stop token at a state where the language can end: the mask offers
    # it, accept_tokens takes it, and draft validation must approve it —
    # without changing the session.
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.REGEX, "a+", stop_token_ids={EOS, X})
    vocab = backend.model_vocab_size
    assert accept_bytes(grammar, b"a")
    before = _row_of(grammar, vocab).copy()
    assert grammar.validate_tokens([X]) == [X]
    assert grammar.validate_tokens([X, ord("a")]) == [X]
    assert grammar.validate_tokens([EOS]) == [EOS]
    assert grammar.validate_tokens([ord("a"), X]) == [ord("a"), X]
    # State unchanged by the drafts, and the mask still offers the set.
    assert np.array_equal(_row_of(grammar, vocab), before)
    assert not grammar.is_terminated()
    assert grammar.accepted_tokens == 1
    backend.destroy()


def test_terminal_that_cannot_end_cuts_the_draft(monkeypatch):
    # A stop token whose bytes could continue the language is still a
    # terminal: while the language cannot end the mask clears it, accept
    # refuses it, and draft validation must cut the prefix there instead
    # of approving the byte reading. Here "a+x" with stop 'a' leaves no
    # legal token at all: the mask row is empty and the request fails on
    # its next accept.
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.REGEX, "a+x", stop_token_ids={EOS, ord("a")})
    vocab = backend.model_vocab_size
    assert _allowed(_row_of(grammar, vocab), vocab) == set()
    assert grammar.validate_tokens([ord("a"), ord("x")]) == []
    assert grammar.validate_tokens([ord("a")]) == []
    # Refusal leaves the state alone: no termination, empty row preserved.
    assert not grammar.accept_tokens("r", [ord("a")])
    assert not grammar.is_terminated()
    assert _allowed(_row_of(grammar, vocab), vocab) == set()
    backend.destroy()


def test_terminal_override_keeps_valid_drafts_working(monkeypatch):
    # With the stop set as a terminal override, the default EOS is not
    # terminal: a draft that would take it is cut, and after the override
    # token completes the language the EOS is re-offered.
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.REGEX, "a+", stop_token_ids={X})
    vocab = backend.model_vocab_size
    assert EOS not in _allowed(_row_of(grammar, vocab), vocab)
    assert grammar.validate_tokens([ord("a"), EOS]) == [ord("a")]
    assert accept_bytes(grammar, b"a")
    assert grammar.validate_tokens([EOS]) == []  # not terminal here
    assert grammar.validate_tokens([X]) == [X]
    assert grammar.validate_tokens([X, ord("a")]) == [X]
    backend.destroy()


# ---------------------------------------------------------------------------
# Structural tags (ADR-0016): the pinned hermes agent path
# ---------------------------------------------------------------------------

def test_structural_tag_triggered_flow(monkeypatch):
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.STRUCTURAL_TAG,
        json.dumps(hermes_structural_tag()))
    vocab = backend.model_vocab_size

    # Free text with a reachable end before any tag.
    allowed = _allowed(_row_of(grammar, vocab), vocab)
    assert ord("a") in allowed and EOS in allowed
    assert accept_bytes(grammar, b"Hello ")
    # A full trigger commits: only the two begin continuations survive.
    assert accept_bytes(grammar, b"<tool_call>")
    assert _allowed(_row_of(grammar, vocab), vocab) == {ord("\n"), ord("{")}
    # The arguments value is the compiled tool schema; the tag cannot end
    # while the content is open.
    assert accept_bytes(grammar, b"\n{\"name\": \"set_code\", \"arguments\": ")
    allowed = _allowed(_row_of(grammar, vocab), vocab)
    assert allowed == {ord("{")}
    assert accept_bytes(grammar, b"{\"code\":\"zq-17-wombat\"}")
    # Only the end literal continues (the content is complete).
    assert _allowed(_row_of(grammar, vocab), vocab) == {ord("}")}
    assert accept_bytes(grammar, b"}\n</tool_call>")
    # Text resumes: the document can end or another tag may start.
    allowed = _allowed(_row_of(grammar, vocab), vocab)
    assert ord("d") in allowed and EOS in allowed
    assert accept_bytes(grammar, b"done ")
    # A second tag in the other begin format; then the terminal token.
    assert accept_bytes(grammar, b"<tool_call>{\"name\": \"set_code\", \"arguments\": ")
    assert accept_bytes(grammar, b"{\"code\":\"zq-17-wombat\"}")
    assert accept_bytes(grammar, b"}</tool_call>")
    assert grammar.accept_tokens("r", [EOS])
    assert grammar.is_terminated()
    backend.destroy()


def test_structural_tag_wrong_argument_value_is_refused(monkeypatch):
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.STRUCTURAL_TAG,
        json.dumps(hermes_structural_tag()))
    vocab = backend.model_vocab_size
    assert accept_bytes(grammar, b"<tool_call>\n{\"name\": \"set_code\", \"arguments\": ")
    assert accept_bytes(grammar, b"{\"code\":")
    before = _row_of(grammar, vocab).copy()
    # One bad token inside the value is refused atomically (a whole token
    # that cannot be fed leaves the session alone).
    assert not grammar.accept_tokens("r", [ord("X")])
    assert np.array_equal(_row_of(grammar, vocab), before)
    # A value outside the enum dies the same way: feed the wrong literal
    # one token at a time and check the refusal on the first mismatch.
    assert accept_bytes(grammar, b"\"zq")
    assert not grammar.accept_tokens("r", [ord("X")])
    assert accept_bytes(grammar, b"-17-wombat\"}")
    backend.destroy()


def test_structural_tag_required_and_stop_after_first(monkeypatch):
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.STRUCTURAL_TAG,
        json.dumps(required_structural_tag()))
    forced = backend.compile_grammar(
        bv.StructuredOutputOptions.STRUCTURAL_TAG,
        json.dumps(required_structural_tag(stop_after_first=True)))
    vocab = backend.model_vocab_size
    # No free text before the first tag, and no end either.
    assert _allowed(_row_of(grammar, vocab), vocab) == {ord("<")}
    assert accept_bytes(grammar, b"<tool_call>{\"name\": \"set_code\", \"arguments\": ")
    assert accept_bytes(grammar, b"{\"code\":\"zq-17-wombat\"}")
    assert accept_bytes(grammar, b"}</tool_call>")
    allowed = _allowed(_row_of(grammar, vocab), vocab)
    assert EOS in allowed and ord("<") in allowed  # repeat or end

    assert accept_bytes(forced, b"<tool_call>{\"name\": \"set_code\", \"arguments\": ")
    assert accept_bytes(forced, b"{\"code\":\"zq-17-wombat\"}")
    assert accept_bytes(forced, b"}</tool_call>")
    allowed = _allowed(_row_of(forced, vocab), vocab)
    assert EOS in allowed and ord("<") not in allowed  # exactly one tag
    assert not forced.accept_tokens("r", [ord("<")])
    assert forced.accept_tokens("r", [EOS])
    assert forced.is_terminated()
    backend.destroy()


def test_structural_tag_rollback_and_draft_validation(monkeypatch):
    backend = make_backend(monkeypatch, num_spec_tokens=4)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.STRUCTURAL_TAG,
        json.dumps(hermes_structural_tag()))
    vocab = backend.model_vocab_size
    assert accept_bytes(grammar, b"<tool_call>{\"name\": \"set_code\", \"arguments\": ")
    before = _row_of(grammar, vocab).copy()
    # Drafts inside the arguments value; the state never moves.
    assert grammar.validate_tokens([ord("{"), ord("n")]) == [ord("{")]
    assert grammar.validate_tokens([ord("a")]) == []
    assert np.array_equal(_row_of(grammar, vocab), before)
    # Feed a few value bytes and roll them back: the mask is restored.
    assert accept_bytes(grammar, b"{\"co")
    grammar.rollback(4)
    assert np.array_equal(_row_of(grammar, vocab), before)
    # The whole value; a draft that completes the tag is approved and the
    # state stays put (the rollback depth is the speculative count).
    assert accept_bytes(grammar, b"{\"code\":\"zq-17-wombat\"}")
    value_mask = _row_of(grammar, vocab).copy()
    tail = list(b"}</tool_call>")
    assert grammar.validate_tokens(tail) == tail
    assert np.array_equal(_row_of(grammar, vocab), value_mask)
    # A refused rollback changes nothing observable.
    with pytest.raises(RuntimeError):
        grammar.rollback(100)
    assert np.array_equal(_row_of(grammar, vocab), value_mask)
    backend.destroy()


def test_structural_tag_unsupported_variant_is_a_request_error(monkeypatch):
    backend = make_backend(monkeypatch)
    with pytest.raises(bv.BolorgirRequestError) as exc:
        backend.compile_grammar(
            bv.StructuredOutputOptions.STRUCTURAL_TAG,
            json.dumps({"type": "structural_tag", "format": {"type": "sequence"}}))
    assert "UnsupportedFeatureError" in str(exc.value)
    with pytest.raises(bv.BolorgirRequestError):
        backend.compile_grammar(
            bv.StructuredOutputOptions.STRUCTURAL_TAG,
            json.dumps({"type": "structural_tag",
                        "format": {"type": "tags_with_separator", "separator": " ",
                                   "tags": required_structural_tag()["format"]["tags"]}}))
    backend.destroy()


# ---------------------------------------------------------------------------
# Backend trace (blg-agent-trace/2, benchmarks/agent_eval/PLAN.md)
# ---------------------------------------------------------------------------

def _load_qualify():
    """The A1 qualification collector, imported by path like the tests of
    test_agent_qualify.py do."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "qualify_stack", os.path.join(ROOT, "benchmarks", "agent_eval", "qualify_stack.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _enable_trace(monkeypatch, tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    monkeypatch.setenv(bv.TRACE_ENV, str(trace_path))
    monkeypatch.setattr(bv, "_trace_writer", None)
    monkeypatch.setattr(bv, "_trace_writer_checked", False)
    return trace_path


def _trace_events(path):
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def test_trace_of_a_json_request_qualifies(monkeypatch, tmp_path):
    q = _load_qualify()
    trace_path = _enable_trace(monkeypatch, tmp_path)
    backend = make_backend(monkeypatch)
    schema = {"type": "integer", "minimum": 10, "maximum": 42}
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.JSON, json.dumps(schema), request_id="req-json")
    vocab = backend.model_vocab_size

    _row_of(grammar, vocab)
    assert grammar.accept_tokens("req-json", [ord("4")])
    _row_of(grammar, vocab)
    assert grammar.accept_tokens("req-json", [ord("2")])
    _row_of(grammar, vocab)
    assert grammar.accept_tokens("req-json", [EOS])
    assert grammar.is_terminated()
    backend.destroy()

    events = _trace_events(trace_path)
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert q.trace_problems(events, "bolorgir") == []
    row = {"schemas_sha256": {"response_format": q.canonical_sha256(schema)}}
    qual, reason = q.qualify(row, events, "bolorgir")
    assert qual == "constrained", reason


def test_trace_of_a_tool_request_names_the_compiled_schema(monkeypatch, tmp_path):
    q = _load_qualify()
    trace_path = _enable_trace(monkeypatch, tmp_path)
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.STRUCTURAL_TAG,
        json.dumps(hermes_structural_tag()), request_id="req-tool")
    vocab = backend.model_vocab_size

    _row_of(grammar, vocab)
    assert accept_bytes(grammar, b"<tool_call>{\"name\": \"set_code\", \"arguments\": ",
                        "req-tool")
    _row_of(grammar, vocab)
    assert accept_bytes(grammar, b"{\"code\":\"zq-17-wombat\"}", "req-tool")
    _row_of(grammar, vocab)
    assert accept_bytes(grammar, b"}</tool_call>", "req-tool")
    _row_of(grammar, vocab)
    assert grammar.accept_tokens("req-tool", [EOS])
    backend.destroy()

    events = _trace_events(trace_path)
    row = {"schemas_sha256": {
        "tool:set_code": q.canonical_sha256(SET_CODE_PARAMS),
        "structural_tag": q.canonical_sha256(["set_code"])}}
    qual, reason = q.qualify(row, events, "bolorgir")
    assert qual == "constrained", reason
    # The declared constraints carry the client's schema hash exactly,
    # plus the free-text fingerprint both sides compute from the tool
    # names.
    decision = next(e for e in events if e["event"] == "decision")
    assert decision["constraints"] == [{
        "constraint_id": "set_code", "kind": "tool", "segment_id": "set_code",
        "schema_sha256": q.canonical_sha256(SET_CODE_PARAMS)}, {
        "constraint_id": "structural_tag", "kind": "structural_tag",
        "segment_id": "structural_tag",
        "schema_sha256": q.canonical_sha256(["set_code"])}]


def test_trace_of_a_speculative_terminal_rollback(monkeypatch, tmp_path):
    """vLLM accepts draft tokens (a terminal among them) while filling
    masks and rolls them back in the same step: the finish must not be
    written before the request is really over, and the events after the
    rollback must be kept."""
    q = _load_qualify()
    trace_path = _enable_trace(monkeypatch, tmp_path)
    backend = make_backend(monkeypatch, num_spec_tokens=2)
    schema = {"type": "integer", "minimum": 10, "maximum": 42}
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.JSON, json.dumps(schema), request_id="req-term")
    vocab = backend.model_vocab_size

    _row_of(grammar, vocab)
    assert grammar.accept_tokens("req-term", [ord("4"), ord("2")])
    # The draft terminal: accepted, then rolled back by the engine.
    assert grammar.accept_tokens("req-term", [EOS])
    assert grammar.is_terminated()
    grammar.rollback(1)
    assert not grammar.is_terminated()
    _row_of(grammar, vocab)
    # The generation continues and ends for real.
    assert grammar.accept_tokens("req-term", [EOS])

    # Another request's engine step confirms the deferred terminal.
    other = backend.compile_grammar(
        bv.StructuredOutputOptions.JSON, json.dumps(schema), request_id="req-other")
    _row_of(other, vocab)
    assert other.accept_tokens("req-other", [ord("1")])
    backend.destroy()

    events = [e for e in _trace_events(trace_path) if e["request_id"] == "req-term"]
    kinds = [e["event"] for e in events]
    assert kinds.count("finish") == 1 and kinds[-1] == "finish", kinds
    assert kinds.count("rollback") == 1
    rb = next(i for i, e in enumerate(events) if e["event"] == "rollback")
    assert events[rb]["tokens"] == 1 and events[rb]["terminal_undone"] is True
    # Events after the rollback are present: the terminal accept, then the
    # final one, and the finish that closes the trace.
    terminals = [i for i, e in enumerate(events)
                 if e["event"] == "accept" and e.get("terminal")]
    assert len(terminals) == 2 and terminals[0] < rb < terminals[1]
    assert q.trace_problems(events, "bolorgir") == []
    qual, reason = q.qualify(
        {"schemas_sha256": {"response_format": q.canonical_sha256(schema)}},
        events, "bolorgir")
    assert qual == "constrained", reason


def _two_tool_structural_tag(required: bool = False):
    """The pinned \"auto\"/\"required\" shape for two tools: first and
    second, each in both begin formats of the hermes parser."""
    spec = hermes_structural_tag("first")
    tags = list(spec["format"]["tags"])
    tags += list(hermes_structural_tag("second")["format"]["tags"])
    if required:
        return {"type": "structural_tag",
                "format": {"type": "tags_with_separator", "separator": "",
                           "at_least_one": True, "tags": tags}}
    return {"type": "structural_tag",
            "format": {"type": "triggered_tags", "triggers": ["<tool_call>"],
                       "tags": tags}}


def test_trace_attributes_calls_to_the_active_tool(monkeypatch, tmp_path):
    """The second tool is chosen: masks and accepts of its framing and
    arguments name ``second``; free text and the shared begin prefix name
    the structural-tag constraint, never the first tool."""
    q = _load_qualify()
    trace_path = _enable_trace(monkeypatch, tmp_path)
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.STRUCTURAL_TAG,
        json.dumps(_two_tool_structural_tag()), request_id="req-second")
    vocab = backend.model_vocab_size

    _row_of(grammar, vocab)  # free text
    assert accept_bytes(grammar, b"I will call ", "req-second")
    _row_of(grammar, vocab)
    assert accept_bytes(grammar, b"<tool_call>\n{\"name\": \"second\", \"arguments\": ",
                        "req-second")
    _row_of(grammar, vocab)
    assert accept_bytes(grammar, b"{\"code\":\"zq-17-wombat\"}", "req-second")
    _row_of(grammar, vocab)
    assert accept_bytes(grammar, b"}\n</tool_call>", "req-second")
    _row_of(grammar, vocab)
    assert grammar.accept_tokens("req-second", [EOS])
    backend.destroy()

    events = _trace_events(trace_path)
    accepts = [e for e in events if e["event"] == "accept"]
    masks = [e for e in events if e["event"] == "mask"]
    # The arguments and the rest of the chosen tag belong to second.
    assert any(e["constraint_id"] == "second" for e in accepts)
    assert any(e["constraint_id"] == "second" for e in masks)
    # Nothing is attributed to the tool that was not chosen.
    assert not any(e["constraint_id"] == "first" for e in accepts + masks)
    # Free text belongs to the structural_tag constraint, not to a tool.
    assert masks[0]["constraint_id"] == "structural_tag"
    assert masks[0]["segment_id"] == "structural_tag"
    # The chosen tool's masks agree with the declared segment.
    second_masks = [e for e in masks if e["constraint_id"] == "second"]
    assert all(e["segment_id"] == "second" for e in second_masks)
    row = {"schemas_sha256": {
        "tool:first": q.canonical_sha256(SET_CODE_PARAMS),
        "tool:second": q.canonical_sha256(SET_CODE_PARAMS),
        "structural_tag": q.canonical_sha256(["first", "second"])}}
    qual, reason = q.qualify(row, events, "bolorgir")
    assert qual == "constrained", reason


def test_trace_of_several_sequential_calls(monkeypatch, tmp_path):
    """Two sequential tool calls (the required mode): each call's events
    name its own tool, in order."""
    q = _load_qualify()
    trace_path = _enable_trace(monkeypatch, tmp_path)
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.STRUCTURAL_TAG,
        json.dumps(_two_tool_structural_tag(required=True)), request_id="req-seq")
    vocab = backend.model_vocab_size

    _row_of(grammar, vocab)
    assert accept_bytes(grammar, b"<tool_call>\n{\"name\": \"first\", \"arguments\": ",
                        "req-seq")
    assert accept_bytes(grammar, b"{\"code\":\"zq-17-wombat\"}", "req-seq")
    _row_of(grammar, vocab)
    assert accept_bytes(grammar, b"}\n</tool_call>", "req-seq")
    _row_of(grammar, vocab)
    assert accept_bytes(grammar, b"<tool_call>\n{\"name\": \"second\", \"arguments\": ",
                        "req-seq")
    assert accept_bytes(grammar, b"{\"code\":\"zq-17-wombat\"}", "req-seq")
    _row_of(grammar, vocab)
    assert accept_bytes(grammar, b"}\n</tool_call>", "req-seq")
    _row_of(grammar, vocab)
    assert grammar.accept_tokens("req-seq", [EOS])
    backend.destroy()

    events = _trace_events(trace_path)
    accept_cids = [e["constraint_id"] for e in events if e["event"] == "accept"]
    assert "first" in accept_cids and "second" in accept_cids
    # The first call's events come before the second call's.
    assert accept_cids.index("first") < len(accept_cids) - 1 - accept_cids[::-1].index("second")
    row = {"schemas_sha256": {
        "tool:first": q.canonical_sha256(SET_CODE_PARAMS),
        "tool:second": q.canonical_sha256(SET_CODE_PARAMS),
        "structural_tag": q.canonical_sha256(["first", "second"])}}
    qual, reason = q.qualify(row, events, "bolorgir")
    assert qual == "constrained", reason


def test_trace_carries_the_external_and_internal_request_ids(monkeypatch, tmp_path):
    """The patched vLLM passes the exposed request id (the response id)
    together with the engine-internal one: the trace is keyed by the id
    the collector observes and records the mapping explicitly."""
    q = _load_qualify()
    trace_path = _enable_trace(monkeypatch, tmp_path)
    backend = make_backend(monkeypatch)
    schema = {"type": "integer", "minimum": 10, "maximum": 42}
    client_id = "qualify-probe-1"
    external = f"chatcmpl-{client_id}"      # built by the serving layer from X-Request-Id
    internal = f"{external}-01234567"        # InputProcessor.assign_request_id appends 8 chars
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.JSON, json.dumps(schema),
        request_id=internal, external_request_id=external)
    vocab = backend.model_vocab_size
    _row_of(grammar, vocab)
    assert grammar.accept_tokens(internal, [ord("4"), ord("2")])
    assert grammar.accept_tokens(internal, [EOS])
    backend.destroy()

    trace, malformed = q.load_trace(str(trace_path))
    assert not malformed
    assert external in trace
    events = trace[external]
    assert all(e["request_id"] == external for e in events)
    assert all(e.get("internal_request_id") == internal for e in events)
    assert q.trace_problems(events, "bolorgir") == []
    # The collector joins by the id it observes (the response id).
    row = {"request_id": client_id, "response_id": external,
           "schemas_sha256": {"response_format": q.canonical_sha256(schema)}}
    qual, reason = q.qualify(row, trace.get(row["response_id"]), "bolorgir")
    assert qual == "constrained", reason
    # The bare client id resolves to nothing: the trace is not keyed by it.
    qual, reason = q.qualify(row, trace.get(row["request_id"]), "bolorgir")
    assert qual == "unknown", reason


def test_trace_of_parallel_children_stays_separate(monkeypatch, tmp_path):
    """n = 2: vLLM fans one client request out into child engine requests
    that share the exposed id and differ in the internal one
    (`{index}_{parent_internal}`, ParentRequest.get_child_info). The
    group must be split into streams, every variant qualified, and the
    row joined back to the response's choices; one corrupted variant
    must not ride along on the healthy one."""
    q = _load_qualify()
    trace_path = _enable_trace(monkeypatch, tmp_path)
    backend = make_backend(monkeypatch)
    schema = {"type": "integer", "minimum": 10, "maximum": 42}
    client_id = "qualify-parallel-1"
    external = f"chatcmpl-{client_id}"
    parent_internal = f"{external}-89abcdef"
    vocab = backend.model_vocab_size
    for idx in range(2):
        internal = f"{idx}_{parent_internal}"
        g = backend.compile_grammar(
            bv.StructuredOutputOptions.JSON, json.dumps(schema),
            request_id=internal, external_request_id=external)
        _row_of(g, vocab)
        assert g.accept_tokens(internal, [ord("4"), ord("2")])
        assert g.accept_tokens(internal, [EOS])
    backend.destroy()

    trace, malformed = q.load_trace(str(trace_path))
    assert not malformed and external in trace
    events = trace[external]
    streams = q.trace_streams(events)
    assert [sid for sid, _ in streams] == [f"0_{parent_internal}",
                                           f"1_{parent_internal}"]
    # Merged, the group is corrupt (two starts, repeated seq): splitting
    # is what makes the evidence readable.
    assert q.trace_problems(events, "bolorgir")
    row = {"request_id": client_id, "response_id": external,
           "response": {"choices": [{"index": 0}, {"index": 1}]},
           "schemas_sha256": {"response_format": q.canonical_sha256(schema)}}
    qual, reason, details = q.qualify_streams(row, streams, "bolorgir")
    assert qual == "constrained", reason
    assert sorted(details) == [f"0_{parent_internal}", f"1_{parent_internal}"]
    assert all(d["qualification"] == "constrained" for d in details.values())
    # A corrupted variant makes the whole row unknown, naming the stream.
    corrupted = []
    for e in events:
        e = dict(e)
        if e.get("internal_request_id") == f"1_{parent_internal}" and e["event"] == "mask":
            e["constraint_id"] = "other"
        corrupted.append(e)
    qual, reason, details = q.qualify_streams(row, q.trace_streams(corrupted), "bolorgir")
    assert qual == "unknown", reason
    assert f"1_{parent_internal}" in reason and "undeclared constraint" in reason, reason


def test_trace_without_a_terminal_stays_unknown(monkeypatch, tmp_path):
    q = _load_qualify()
    trace_path = _enable_trace(monkeypatch, tmp_path)
    backend = make_backend(monkeypatch)
    schema = {"type": "integer", "minimum": 10, "maximum": 42}
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.JSON, json.dumps(schema), request_id="req-cut")
    _row_of(grammar, backend.model_vocab_size)
    assert grammar.accept_tokens("req-cut", [ord("4")])
    backend.destroy()
    events = _trace_events(trace_path)
    # No finish event: the request did not end through the backend, and
    # an absent event is never read as "no constraint".
    qual, reason = q.qualify(
        {"schemas_sha256": {}}, events, "bolorgir")
    assert qual == "unknown", reason


def test_tracing_is_off_without_the_environment(monkeypatch):
    monkeypatch.delenv(bv.TRACE_ENV, raising=False)
    monkeypatch.setattr(bv, "_trace_writer", None)
    monkeypatch.setattr(bv, "_trace_writer_checked", False)
    assert bv._trace_writer_for_process() is None
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.REGEX, "a+", request_id="req-off")
    assert grammar._trace is None
    backend.destroy()


# ---------------------------------------------------------------------------
# Rollback and draft validation (speculative decoding)
# ---------------------------------------------------------------------------

def test_rollback_restores_the_mask_and_validate_keeps_the_state(monkeypatch):
    backend = make_backend(monkeypatch, num_spec_tokens=3)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.JSON,
        json.dumps({"type": "integer", "minimum": 10, "maximum": 42}))
    vocab = backend.model_vocab_size

    initial = _row_of(grammar, vocab).copy()
    assert grammar.accept_tokens("r", [ord("4")])
    after4 = _row_of(grammar, vocab).copy()
    assert grammar.accept_tokens("r", [ord("2")])
    assert not np.array_equal(_row_of(grammar, vocab), after4)

    grammar.rollback(1)
    assert np.array_equal(_row_of(grammar, vocab), after4)
    grammar.rollback(1)
    assert np.array_equal(_row_of(grammar, vocab), initial)
    with pytest.raises(RuntimeError):
        grammar.rollback(1)

    # Draft validation reports the accepted prefix and leaves the state.
    before = _row_of(grammar, vocab).copy()
    assert grammar.validate_tokens([ord("4"), ord("2"), EOS]) == [ord("4"), ord("2"), EOS]
    assert grammar.validate_tokens([ord("4"), X]) == [ord("4")]
    assert grammar.validate_tokens([ord("9")]) == []
    assert np.array_equal(_row_of(grammar, vocab), before)

    # Rolling back an accepted terminal re-opens generation.
    assert grammar.accept_tokens("r", [ord("4"), ord("2")])
    assert grammar.accept_tokens("r", [EOS])
    assert grammar.is_terminated()
    grammar.rollback(1)
    assert not grammar.is_terminated()
    assert EOS in _allowed(_row_of(grammar, vocab), vocab)
    backend.destroy()


# ---------------------------------------------------------------------------
# Failure policy, vocabulary padding, reset
# ---------------------------------------------------------------------------

def test_a_runtime_failure_fails_only_this_request(monkeypatch):
    backend = make_backend(monkeypatch)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.JSON, json.dumps({"type": "integer"}))
    vocab = backend.model_vocab_size
    assert accept_bytes(grammar, b"7")

    # Simulate a runtime kernel failure under the adapter.
    grammar._session.close()
    row = _row_of(grammar, vocab)  # must not raise
    assert _allowed(row, vocab) == set(range(vocab))
    assert grammar.is_terminated()
    assert not grammar.accept_tokens("r", [ord("7")])

    # A sibling request is unaffected.
    other = backend.compile_grammar(
        bv.StructuredOutputOptions.JSON, json.dumps({"type": "integer"}))
    assert _allowed(_row_of(other, vocab), vocab) != set(range(vocab))
    assert other.accept_tokens("r2", [ord("7")])

    # reset() recovers a fresh session for the failed grammar.
    grammar.reset()
    assert not grammar.is_terminated()
    assert _allowed(_row_of(grammar, vocab), vocab) != set(range(vocab))
    assert grammar.accept_tokens("r", [ord("7")])
    backend.destroy()


def test_model_vocabulary_padding_is_never_offered(monkeypatch):
    bundle = _byte_bundle()
    vocab = bundle.vocab_size + 33  # a padded model vocabulary
    backend = make_backend(monkeypatch, vocab_size=vocab)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.REGEX, "a+")
    row = _row_of(grammar, vocab)
    allowed = _allowed(row, vocab)
    assert ord("a") in allowed
    assert not any(i >= bundle.vocab_size for i in allowed)
    assert row.shape[0] == (vocab + 31) // 32
    backend.destroy()


def test_allocate_token_bitmask_with_torch(monkeypatch):
    torch = pytest.importorskip("torch")
    backend = make_backend(monkeypatch)
    bitmask = backend.allocate_token_bitmask(3)
    assert bitmask.dtype == torch.int32
    assert bitmask.shape == (3, (backend.model_vocab_size + 31) // 32)
    assert (bitmask.numpy() == -1).all()

    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.REGEX, "a+")
    grammar.fill_bitmask(bitmask, 1)
    row = bitmask[1].numpy()
    assert ord("a") in _allowed(row, backend.model_vocab_size)
    assert EOS not in _allowed(row, backend.model_vocab_size)
    # Other rows stay unrestricted.
    assert int(bitmask[0].numpy().min()) == -1 and int(bitmask[2].numpy().min()) == -1
    backend.destroy()


# ---------------------------------------------------------------------------
# The real HF tokenizer bridge
# ---------------------------------------------------------------------------

def _hf_byte_tokenizer():
    pytest.importorskip("tokenizers")
    pytest.importorskip("transformers")
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    bs = list(range(33, 127)) + list(range(161, 173)) + list(range(174, 256))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    table = dict(zip(bs, [chr(c) for c in cs]))
    vocab = {table[b]: b for b in range(256)}
    tok = Tokenizer(models.BPE(vocab=vocab, merges=[]))
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    return PreTrainedTokenizerFast(tokenizer_object=tok, eos_token="<eos>",
                                   unk_token="<unk>")


def test_hf_tokenizer_bridge_and_flow(monkeypatch):
    hf = _hf_byte_tokenizer()
    bundle = bolorgir.TokenizerBundle.from_hf(hf)
    assert bundle.vocab_size == 258 and tuple(bundle.eos_ids) == (EOS,)

    cfg = types.SimpleNamespace(speculative_config=None)
    backend = bv.BolorgirBackend(cfg, tokenizer=hf, vocab_size=bundle.vocab_size)
    grammar = backend.compile_grammar(
        bv.StructuredOutputOptions.JSON, json.dumps({"enum": ["reply", "escalate"]}))
    assert accept_bytes(grammar, b'"reply"')
    allowed = _allowed(_row_of(grammar, backend.model_vocab_size),
                       backend.model_vocab_size)
    assert allowed == {EOS}
    assert grammar.accept_tokens("r", [EOS])
    backend.destroy()


def test_pinned_model_tokenizer_when_requested(monkeypatch):
    """Optional: the tokenizer of the pinned serving model.

    Runs only with BLG_VLLM_MODEL set (network access to Hugging Face);
    a skip means "not verified", never "passed".
    """
    model = os.environ.get("BLG_VLLM_MODEL")
    if not model:
        pytest.skip("BLG_VLLM_MODEL is not set; the pinned model tokenizer "
                    "check needs the Hugging Face download")
    transformers = pytest.importorskip("transformers")
    hf = transformers.AutoTokenizer.from_pretrained(model)
    bundle = bolorgir.TokenizerBundle.from_hf(hf)
    cfg = types.SimpleNamespace(speculative_config=None)
    backend = bv.BolorgirBackend(cfg, tokenizer=hf, vocab_size=len(hf.get_vocab()))
    # JSON Schema, choice and regex all compile on the real vocabulary.
    json_g = backend.compile_grammar(
        bv.StructuredOutputOptions.JSON,
        json.dumps({"type": "object", "properties": {"a": {"type": "integer"}},
                    "required": ["a"], "additionalProperties": False}))
    choice_g = backend.compile_grammar(
        bv.StructuredOutputOptions.CHOICE, json.dumps(["reply", "escalate"]))
    regex_g = backend.compile_grammar(bv.StructuredOutputOptions.REGEX, "[a-z]{2,4}")
    for g in (json_g, choice_g, regex_g):
        row = _row_of(g, backend.model_vocab_size)
        assert any(_allowed(row, backend.model_vocab_size))
    backend.destroy()
