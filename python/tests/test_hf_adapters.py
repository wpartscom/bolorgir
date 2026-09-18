"""Regression tests for the audit findings in the HF adapter layer.

Covers (docs/AUDIT_TZ_2026-09-15.md):
- A2a: byte fallback tokens (<0xNN>) decode to a single byte, checked
  before the byte-level BPE table;
- A2b: unsupported schemes (SentencePiece without byte_fallback) are
  rejected with UnsupportedTokenizerError; legitimate families
  (byte-level BPE, byte fallback, SentencePiece+byte_fallback, literal
  added tokens) still work;
- scheme detection is vocab-level (full bytes_to_unicode alphabet ->
  byte-level BPE; else byte_fallback flag -> SentencePiece; else
  reject): SP pieces with U+00A0..U+00FF chars decode as literal UTF-8,
  not through the byte-level table; includes a ground-truth
  certification of the real TinyLlama vocab against backend.decode;
- A2c: the from_hf cache key includes a vocab fingerprint — two
  tokenizers with the same name_or_path and different vocabs get
  different bundles;
- A3: finish_row without an accepted EOS -> (False, "length"), even
  when can_end() holds;
- A4: NaN/+inf among constraint-allowed scores -> ZigConstraintsError;
- FR-14/15: generation conflicts are checked against the effective
  config (model.generation_config merged with gen_kwargs), and the
  processor's EOS set is the union of tokenizer and generation_config
  eos ids.

HF models/tokenizers are fakes; torch/transformers are imported only
inside tests (collection must not pull torch into sys.modules, see
test_import_without_torch_numpy in test_api_smoke.py).
"""

import os
import re
from types import SimpleNamespace

import pytest

CORE_IS_STUB = os.environ.get("ZG_CORE_STUB") == "1"

import zig_constraints as zc
from zig_constraints import Engine, TokenizerBundle
from zig_constraints.tokenizers import _BYTE_TO_CHAR

EOS_ID = 256
VOCAB = 257

needs_core_masks = pytest.mark.skipif(
    CORE_IS_STUB, reason="stub-ядро зануляет маски fill_mask"
)


def mini_bundle() -> TokenizerBundle:
    tokens = [bytes([b]) for b in range(256)] + [b"<eos>"]
    return TokenizerBundle.from_token_bytes(tokens, eos_ids=[EOS_ID])


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------

class _AddedToken:
    def __init__(self, content, special=False):
        self.content = content
        self.special = special


def byte_level_vocab(*extra):
    """Fake byte-level BPE vocab: the full 256-char alphabet + extras."""
    vocab = {c: i for i, c in enumerate(_BYTE_TO_CHAR.values())}
    for tok in extra:
        vocab[tok] = len(vocab)
    return vocab


class FakeTokenizer:
    def __init__(
        self,
        vocab,
        *,
        byte_fallback=False,
        eos_token_id=None,
        added=None,
        name_or_path="fake/model",
        revision=None,
    ):
        self._vocab = dict(vocab)
        self.byte_fallback = byte_fallback
        self.eos_token_id = eos_token_id
        self.name_or_path = name_or_path
        self.revision = revision
        self.added_tokens_decoder = dict(added or {})
        self.all_special_ids = sorted(
            i for i, t in self.added_tokens_decoder.items() if t.special
        )

    def get_vocab(self):
        return dict(self._vocab)


class FakeModel:
    def __init__(self, gen_config=None):
        self.generation_config = gen_config
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(sequences=kwargs["input_ids"])


@pytest.fixture(autouse=True)
def _clear_bundle_cache():
    from zig_constraints import tokenizers as _t

    _t._BUNDLE_CACHE.clear()
    yield
    _t._BUNDLE_CACHE.clear()


@pytest.fixture
def zt():
    torch = pytest.importorskip("torch")
    from zig_constraints.transformers import ConstraintLogitsProcessor

    return torch, ConstraintLogitsProcessor


@pytest.fixture
def cg():
    torch = pytest.importorskip("torch")
    from zig_constraints.transformers import constrained_generate

    return torch, constrained_generate


# ----------------------------------------------------------------------
# A2a: byte fallback <0xNN> decodes to a single byte
# ----------------------------------------------------------------------

def test_byte_fallback_token_decodes_to_single_byte():
    vocab = {"<0x41>": 0, "a": 1, "<eos>": 2}
    tok = FakeTokenizer(
        vocab,
        byte_fallback=True,
        eos_token_id=2,
        added={2: _AddedToken("<eos>", special=True)},
    )
    b = TokenizerBundle.from_hf(tok, use_cache=False)
    assert b.token_bytes(0) == b"A"
    assert b.token_bytes(1) == b"a"
    assert b.eos_ids == (2,)
    assert b.special_ids == ()  # EOS is excluded from special_only


def test_byte_fallback_pattern_matches_without_flag():
    # The <0xNN> pattern is unambiguous even when byte_fallback is False,
    # and takes priority over the byte-level table.
    vocab = byte_level_vocab("<0x41>", "<eos>")
    tok = FakeTokenizer(vocab, eos_token_id=vocab["<eos>"])
    b = TokenizerBundle.from_hf(tok, use_cache=False)
    assert b.token_bytes(vocab["<0x41>"]) == b"A"


# ----------------------------------------------------------------------
# A2b: unsupported schemes are rejected; supported families work
# ----------------------------------------------------------------------

def test_sentencepiece_without_byte_fallback_rejected():
    vocab = {"▁hello": 0, "▁": 1, "<eos>": 2}
    tok = FakeTokenizer(vocab, byte_fallback=False, eos_token_id=2)
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(tok, use_cache=False)


def test_sentencepiece_with_byte_fallback_supported():
    vocab = {"▁hello": 0, "▁": 1, "<0x0A>": 2, "<eos>": 3}
    tok = FakeTokenizer(vocab, byte_fallback=True, eos_token_id=3)
    b = TokenizerBundle.from_hf(tok, use_cache=False)
    assert b.token_bytes(0) == b" hello"
    assert b.token_bytes(1) == b" "
    assert b.token_bytes(2) == b"\x0a"


def test_byte_level_vocab_unchanged():
    # 'Ġ' is the byte-level BPE encoding of the space byte.
    vocab = byte_level_vocab("Ġhello", "<eos>")
    tok = FakeTokenizer(vocab, eos_token_id=vocab["<eos>"])
    b = TokenizerBundle.from_hf(tok, use_cache=False)
    assert b.token_bytes(vocab["Ġhello"]) == b" hello"
    assert b.token_bytes(vocab["a"]) == b"a"


def test_non_special_added_token_decodes_to_literal_content():
    vocab = byte_level_vocab("новый", "<eos>")
    tok = FakeTokenizer(
        vocab,
        eos_token_id=vocab["<eos>"],
        added={
            vocab["новый"]: _AddedToken("новый", special=False),
            vocab["<eos>"]: _AddedToken("<eos>", True),
        },
    )
    b = TokenizerBundle.from_hf(tok, use_cache=False)
    assert b.token_bytes(vocab["новый"]) == "новый".encode("utf-8")


def test_special_token_excluded_from_special_ids_only_if_eos():
    vocab = byte_level_vocab("<pad>", "<eos>")
    tok = FakeTokenizer(
        vocab,
        eos_token_id=vocab["<eos>"],
        added={
            vocab["<pad>"]: _AddedToken("<pad>", True),
            vocab["<eos>"]: _AddedToken("<eos>", True),
        },
    )
    b = TokenizerBundle.from_hf(tok, use_cache=False)
    assert b.special_ids == (vocab["<pad>"],)
    assert b.eos_ids == (vocab["<eos>"],)


# ----------------------------------------------------------------------
# Scheme detection is vocab-level, not per-token (TinyLlama finding of the
# 2026-09-15 benchmark run: SP pieces with U+00A0..U+00FF chars were
# mis-decoded through the byte-level BPE table)
# ----------------------------------------------------------------------

def test_sp_piece_with_latin1_char_decodes_as_literal_utf8():
    # An SP piece like 'iné' must NOT go through the byte-level table
    # (which would yield b'in\xe9' instead of the correct UTF-8).
    vocab = {"iné": 0, "▁hello": 1, "▁": 2, "<0xE9>": 3, "<eos>": 4}
    tok = FakeTokenizer(vocab, byte_fallback=True, eos_token_id=4)
    b = TokenizerBundle.from_hf(tok, use_cache=False)
    assert b.token_bytes(0) == "iné".encode("utf-8")  # b'in\xc3\xa9'
    assert b.token_bytes(1) == b" hello"
    assert b.token_bytes(2) == b" "
    assert b.token_bytes(3) == b"\xe9"


def test_byte_level_scheme_still_uses_byte_level_table():
    # Same token text in a byte-level BPE vocab (full alphabet present,
    # flag set): the byte-level table applies — 'é' encodes byte 0xE9.
    vocab = byte_level_vocab("iné", "Ġhello", "<0x41>", "<eos>")
    tok = FakeTokenizer(
        vocab, eos_token_id=vocab["<eos>"], byte_fallback=True
    )
    b = TokenizerBundle.from_hf(tok, use_cache=False)
    assert b.token_bytes(vocab["iné"]) == b"in\xe9"
    assert b.token_bytes(vocab["Ġhello"]) == b" hello"
    assert b.token_bytes(vocab["<0x41>"]) == b"A"  # <0xNN> before the table


def test_unrecognized_scheme_rejected_even_if_tokens_look_decodable():
    # Neither the full byte-level alphabet nor byte_fallback: refuse, even
    # though 'hello' alone would be byte-level decodable.
    vocab = {"hello": 0, "<eos>": 1}
    tok = FakeTokenizer(vocab, eos_token_id=1)
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(tok, use_cache=False)


_BYTE_FALLBACK_TEST_RE = re.compile(r"<0x([0-9A-Fa-f]{2})>")


def _hf_text_render(pieces_and_bytes):
    """Render a token sequence the way HF's ByteFallback decoder renders
    text: consecutive fallback bytes form a run; a run that is valid
    UTF-8 decodes to text, otherwise each byte becomes U+FFFD. One
    leading space of the whole stream is not rendered (Metaspace
    document-start rule). This is the text-level reference: the bundle
    is byte-exact by contract, so raw bytes are compared through this
    render.
    """
    out = []
    run = b""

    def flush():
        nonlocal run
        if run:
            try:
                out.append(run.decode("utf-8"))
            except UnicodeDecodeError:
                out.append("\ufffd" * len(run))
            run = b""

    for piece, data in pieces_and_bytes:
        if _BYTE_FALLBACK_TEST_RE.fullmatch(piece):
            run += data
            continue
        flush()
        out.append(data.decode("utf-8"))
    flush()
    text = "".join(out)
    if text.startswith(" "):
        text = text[1:]
    return text.encode("utf-8")


def test_tinyllama_sp_bytes_certified_against_backend():
    # Ground truth for the whole vocab: every token's bytes, rendered at
    # text level per HF ByteFallback semantics, must match backend.decode;
    # plus sampled token pairs (concatenation/runs across boundaries).
    tf = pytest.importorskip("transformers")
    try:
        tok = tf.AutoTokenizer.from_pretrained(
            "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
        )
    except Exception as e:
        pytest.skip(f"TinyLlama tokenizer unavailable: {e}")
    backend = getattr(tok, "_tokenizer", None)
    if backend is None:
        pytest.skip("нет _tokenizer backend")
    # transformers 5.17 loads the slow LlamaTokenizer without the
    # byte_fallback attribute; the bench shim sets it explicitly.
    tok.byte_fallback = True
    bundle = TokenizerBundle.from_hf(tok, use_cache=False)
    inv = {i: t for t, i in tok.get_vocab().items()}

    bad = [
        i
        for i in range(bundle.vocab_size)
        if _hf_text_render([(inv[i], bundle.token_bytes(i))])
        != backend.decode([i], skip_special_tokens=False).encode("utf-8")
    ]
    assert bad == []

    import random

    rng = random.Random(42)
    bad_pairs = []
    for _ in range(2000):
        i, j = rng.randrange(bundle.vocab_size), rng.randrange(bundle.vocab_size)
        got = _hf_text_render(
            [(inv[i], bundle.token_bytes(i)), (inv[j], bundle.token_bytes(j))]
        )
        ref = backend.decode([i, j], skip_special_tokens=False).encode("utf-8")
        if got != ref:
            bad_pairs.append((i, j))
    assert bad_pairs == []


# ----------------------------------------------------------------------
# A2c: cache key includes the vocab fingerprint
# ----------------------------------------------------------------------

def test_from_hf_cache_distinguishes_vocab():
    tok1 = FakeTokenizer(
        {"▁a": 0, "<eos>": 1}, byte_fallback=True, eos_token_id=1
    )
    tok2 = FakeTokenizer(
        {"▁x": 0, "<eos>": 1}, byte_fallback=True, eos_token_id=1
    )
    b1 = TokenizerBundle.from_hf(tok1)
    b2 = TokenizerBundle.from_hf(tok2)
    assert b1 is not b2
    assert b1.token_bytes(0) == b" a"
    assert b2.token_bytes(0) == b" x"
    assert TokenizerBundle.from_hf(tok1) is b1  # cache hit for the same content


def test_from_hf_cache_distinguishes_byte_fallback_flag():
    # A full byte-level alphabet vocab builds under either flag value
    # (the alphabet wins the scheme detection), so only the cache key differs.
    vocab = byte_level_vocab("<eos>")
    b1 = TokenizerBundle.from_hf(
        FakeTokenizer(vocab, eos_token_id=vocab["<eos>"])
    )
    b2 = TokenizerBundle.from_hf(
        FakeTokenizer(vocab, eos_token_id=vocab["<eos>"], byte_fallback=True)
    )
    assert b1 is not b2


# ----------------------------------------------------------------------
# A3: completed=True requires an accepted EOS
# ----------------------------------------------------------------------

@needs_core_masks
def test_finish_row_without_eos_not_completed(zt):
    _, proc_cls = zt
    with Engine(mode="lazy") as engine:
        c = engine.compile({"type": "integer"}, tokenizer=mini_bundle())
        sess = c.create_session()
        proc = proc_cls([sess], prompt_len=1, eos_ids=(EOS_ID,))
        # audit regression: {"type":"integer"}, row = [prompt, '1'], no EOS
        assert proc.finish_row(0, [0, ord("1")]) == (False, "length")
        sess.close()
        c.close()


@needs_core_masks
def test_finish_row_with_eos_completed(zt):
    _, proc_cls = zt
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["1"], tokenizer=mini_bundle())
        sess = c.create_session()
        proc = proc_cls([sess], prompt_len=1, eos_ids=(EOS_ID,))
        assert proc.finish_row(0, [0, ord("1"), EOS_ID]) == (True, "eos")
        sess.close()
        c.close()


# ----------------------------------------------------------------------
# A4: invalid scores among allowed candidates are an explicit error
# ----------------------------------------------------------------------

@needs_core_masks
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf")])
def test_invalid_score_among_allowed_tokens_rejected(zt, bad_value):
    torch, proc_cls = zt
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a", "b"], tokenizer=mini_bundle())
        sess = c.create_session()
        proc = proc_cls([sess], prompt_len=1, eos_ids=(EOS_ID,))
        ids = torch.zeros((1, 1), dtype=torch.long)
        scores = torch.zeros((1, VOCAB))
        scores[0, ord("a")] = bad_value  # 'a' is allowed by the mask
        with pytest.raises(zc.ZigConstraintsError):
            proc(ids, scores)
        assert proc.active == [False]
        sess.close()
        c.close()


@needs_core_masks
def test_nan_at_disallowed_position_is_masked_not_an_error(zt):
    torch, proc_cls = zt
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a", "b"], tokenizer=mini_bundle())
        sess = c.create_session()
        proc = proc_cls([sess], prompt_len=1, eos_ids=(EOS_ID,))
        ids = torch.zeros((1, 1), dtype=torch.long)
        scores = torch.zeros((1, VOCAB))
        scores[0, ord("c")] = float("nan")  # 'c' is forbidden -> overwritten
        out = proc(ids, scores)
        finite = torch.isfinite(out[0]).nonzero().flatten().tolist()
        assert finite == [ord("a"), ord("b")]
        sess.close()
        c.close()


# ----------------------------------------------------------------------
# FR-14/15: effective generation config and the EOS union
# ----------------------------------------------------------------------

@needs_core_masks
def test_generation_config_conflicts_detected_before_generate(cg):
    torch, constrained_generate = cg
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a"], tokenizer=mini_bundle())
        ids = torch.zeros((1, 2), dtype=torch.long)
        tok = SimpleNamespace(eos_token_id=EOS_ID)
        for gc in (
            SimpleNamespace(num_beams=4),
            SimpleNamespace(num_return_sequences=2),
            SimpleNamespace(forced_eos_token_id=EOS_ID),
            SimpleNamespace(forced_decoder_ids=[(1, 5)]),
        ):
            model = FakeModel(gc)
            with pytest.raises(zc.UnsupportedModeError):
                constrained_generate(model, tok, c, inputs={"input_ids": ids})
            assert model.calls == []  # rejected before generate
        c.close()


@needs_core_masks
def test_explicit_none_overrides_generation_config(cg):
    torch, constrained_generate = cg
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a"], tokenizer=mini_bundle())
        ids = torch.zeros((1, 1), dtype=torch.long)
        tok = SimpleNamespace(eos_token_id=EOS_ID)
        model = FakeModel(SimpleNamespace(forced_eos_token_id=EOS_ID))
        result = constrained_generate(
            model, tok, c, inputs={"input_ids": ids}, forced_eos_token_id=None
        )
        assert model.calls  # explicit None cleared the conflicting value
        assert result.completed == [False]
        result.sessions[0].close()
        c.close()


@needs_core_masks
def test_eos_ids_union_from_tokenizer_and_generation_config(cg):
    torch, constrained_generate = cg
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a"], tokenizer=mini_bundle())
        ids = torch.zeros((1, 1), dtype=torch.long)
        tok = SimpleNamespace(eos_token_id=EOS_ID)
        model = FakeModel(SimpleNamespace(eos_token_id=[200, EOS_ID]))
        result = constrained_generate(model, tok, c, inputs={"input_ids": ids})
        proc = model.calls[0]["logits_processor"][0]
        assert proc.eos_ids == frozenset({200, EOS_ID})
        result.sessions[0].close()
        c.close()


@needs_core_masks
def test_eos_ids_accept_single_int_generation_config(cg):
    torch, constrained_generate = cg
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a"], tokenizer=mini_bundle())
        ids = torch.zeros((1, 1), dtype=torch.long)
        tok = SimpleNamespace(eos_token_id=None)
        model = FakeModel(SimpleNamespace(eos_token_id=EOS_ID))
        result = constrained_generate(model, tok, c, inputs={"input_ids": ids})
        proc = model.calls[0]["logits_processor"][0]
        assert proc.eos_ids == frozenset({EOS_ID})
        result.sessions[0].close()
        c.close()
