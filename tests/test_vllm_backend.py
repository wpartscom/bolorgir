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
PATCH_SHA256 = "38c0272a5cea02fd040258e85d2459c6a2c97acb2668c2155fa33c943b8cbf2b"
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
                   "vllm/v1/structured_output/__init__.py"):
        assert f"diff --git a/{target} b/{target}" in text
    assert text.count('"bolorgir"') >= 1
    assert "from bolorgir.vllm import" in text


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
        exc(_params(structural_tag='{"structures": []}'))
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
    # The supported kinds pass.
    exc(_params(json={"type": "object"}))
    exc(_params(choice=["reply", "escalate"]))
    exc(_params(regex="[a-z]+"))
    exc(types.SimpleNamespace(structured_outputs=_params(json={"type": "integer"})))


def test_compile_refuses_grammar_and_structural_tag(monkeypatch):
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
