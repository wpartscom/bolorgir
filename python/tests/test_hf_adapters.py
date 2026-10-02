"""Regression tests for the HF adapter layer.

Covers:
- byte fallback tokens (<0xNN>) decode to a single byte, checked
  before the byte-level BPE table;
- unsupported schemes (SentencePiece without byte_fallback) are
  rejected with UnsupportedTokenizerError; legitimate families
  (byte-level BPE, byte fallback, SentencePiece+byte_fallback, literal
  added tokens) still work;
- scheme detection is vocab-level (full bytes_to_unicode alphabet ->
  byte-level BPE; else byte_fallback flag -> SentencePiece; else
  reject): SP pieces with U+00A0..U+00FF chars decode as literal UTF-8,
  not through the byte-level table; includes a ground-truth
  certification of the real TinyLlama vocab against backend.decode;
- the from_hf cache key includes a vocab fingerprint - two
  tokenizers with the same name_or_path and different vocabs get
  different bundles;
- finish_row without an accepted EOS -> (False, "length"), even
  when can_end() holds;
- NaN/+inf among constraint-allowed scores -> ZigConstraintsError;
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

CORE_IS_STUB = os.environ.get("BLG_CORE_STUB") == "1"

import bolorgir as zc
from bolorgir import Engine, TokenizerBundle
from bolorgir.tokenizers import _BYTE_TO_CHAR

EOS_ID = 256
VOCAB = 257

needs_core_masks = pytest.mark.skipif(
    CORE_IS_STUB, reason="stub core zeroes fill_mask masks"
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
    from bolorgir import tokenizers as _t

    _t._BUNDLE_CACHE.clear()
    yield
    _t._BUNDLE_CACHE.clear()


@pytest.fixture
def zt():
    torch = pytest.importorskip("torch")
    from bolorgir.transformers import ConstraintLogitsProcessor

    return torch, ConstraintLogitsProcessor


@pytest.fixture
def cg():
    torch = pytest.importorskip("torch")
    from bolorgir.transformers import constrained_generate

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


def test_byte_fallback_pattern_is_not_a_byte_without_the_decoder():
    # <0x41> means one byte 0x41 only when the decoder does byte fallback.
    # In a byte-level BPE vocab the characters map through the table, so
    # the byte image is the literal text (same as hf decode).
    vocab = byte_level_vocab("<0x41>", "<eos>")
    tok = FakeTokenizer(vocab, eos_token_id=vocab["<eos>"])
    b = TokenizerBundle.from_hf(tok, use_cache=False)
    assert b.token_bytes(vocab["<0x41>"]) == b"<0x41>"


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
    vocab = byte_level_vocab("日本語", "<eos>")
    tok = FakeTokenizer(
        vocab,
        eos_token_id=vocab["<eos>"],
        added={
            vocab["日本語"]: _AddedToken("日本語", special=False),
            vocab["<eos>"]: _AddedToken("<eos>", True),
        },
    )
    b = TokenizerBundle.from_hf(tok, use_cache=False)
    assert b.token_bytes(vocab["日本語"]) == "日本語".encode("utf-8")


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
    # flag set): the byte-level table applies - 'é' encodes byte 0xE9 and
    # '<0x41>' stays literal text (no ByteFallback decoder in this family).
    vocab = byte_level_vocab("iné", "Ġhello", "<0x41>", "<eos>")
    tok = FakeTokenizer(
        vocab, eos_token_id=vocab["<eos>"], byte_fallback=True
    )
    b = TokenizerBundle.from_hf(tok, use_cache=False)
    assert b.token_bytes(vocab["iné"]) == b"in\xe9"
    assert b.token_bytes(vocab["Ġhello"]) == b" hello"
    assert b.token_bytes(vocab["<0x41>"]) == b"<0x41>"


def test_unrecognized_scheme_rejected_even_if_tokens_look_decodable():
    # Neither the full byte-level alphabet nor byte_fallback: refuse, even
    # though 'hello' alone would be byte-level decodable.
    vocab = {"hello": 0, "<eos>": 1}
    tok = FakeTokenizer(vocab, eos_token_id=1)
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(tok, use_cache=False)


# ----------------------------------------------------------------------
# R2(a,c): the real backend decoder drives the byte image; unsupported
# decoder chains are refused and the decoder is part of the cache identity
# ----------------------------------------------------------------------

def _real_bytelevel_tokenizer():
    tokenizers = pytest.importorskip("tokenizers")
    tf = pytest.importorskip("transformers")
    from tokenizers import decoders, models

    vocab = {ch: i for i, ch in enumerate(_BYTE_TO_CHAR.values())}
    backend = tokenizers.Tokenizer(models.BPE(vocab=vocab, merges=[]))
    backend.decoder = decoders.ByteLevel()
    hf = tf.PreTrainedTokenizerFast(
        tokenizer_object=backend, clean_up_tokenization_spaces=False
    )
    hf.name_or_path = "review/decoder"
    hf.add_tokens([tokenizers.AddedToken("<0x41>", normalized=False)])
    return hf, decoders, vocab


def test_added_token_in_bytelevel_decoder_is_literal_text():
    # An added token <0x41> decodes to the literal text
    # '<0x41>' under a ByteLevel decoder - not to the byte 0x41.
    hf, _, _ = _real_bytelevel_tokenizer()
    tid = hf.convert_tokens_to_ids("<0x41>")
    b = TokenizerBundle.from_hf(hf, use_cache=False)
    assert b.token_bytes(tid) == b"<0x41>"
    assert hf.decode([tid], skip_special_tokens=False).encode("utf-8") == b"<0x41>"


def test_unsupported_decoder_chain_rejected_even_with_cache():
    # A nonstandard decoder chain (ByteLevel + Replace)
    # changes the decoded text; it must be refused, and the cached bundle
    # must not mask the change (decoder is part of the fingerprint).
    hf, decoders, vocab = _real_bytelevel_tokenizer()
    b0 = TokenizerBundle.from_hf(hf, use_cache=True)  # prime the cache
    hf.backend_tokenizer.decoder = decoders.Sequence(
        [decoders.ByteLevel(), decoders.Replace("a", "x")]
    )
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(hf, use_cache=True)
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(hf, use_cache=False)
    assert b0.token_bytes(vocab["a"]) == b"a"


def test_fast_backend_missing_decoder_rejected():
    # With a real PreTrainedTokenizerFast using BPE and
    # decoder=None, HF decoding joins tokens with spaces
    # ('"', 'a', 'b', '"' -> '" a b "'), so a full byte alphabet in the
    # vocab does NOT imply a ByteLevel decoder. The ByteLevel heuristic
    # must not kick in - refuse before generation.
    tokenizers = pytest.importorskip("tokenizers")
    tf = pytest.importorskip("transformers")
    from tokenizers import models

    vocab = {ch: i for i, ch in enumerate(_BYTE_TO_CHAR.values())}
    backend = tokenizers.Tokenizer(models.BPE(vocab=vocab, merges=[]))
    # decoder stays None (not set)
    hf = tf.PreTrainedTokenizerFast(
        tokenizer_object=backend, clean_up_tokenization_spaces=False
    )
    hf.name_or_path = "review/missing-decoder"
    hf.add_special_tokens({"eos_token": "<eos>"})
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(hf, use_cache=False)
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(hf, use_cache=True)


def test_fast_backend_unreadable_decoder_rejected():
    # A decoder whose state is unreadable is the same
    # "decoding semantics not proven" situation as decoder=None. A real
    # HF backend won't accept a foreign object as the decoder (the setter
    # only takes a Decoder), so classification and refusal are checked at
    # the _decoder_state/_decoder_profile level.
    from types import SimpleNamespace

    from bolorgir.tokenizers import (
        _DECODER_MISSING,
        _DECODER_NO_BACKEND,
        _DECODER_UNREADABLE,
        _SCHEME_BYTE_LEVEL,
        _decoder_profile,
        _decoder_state,
    )

    class BrokenDecoder:
        def __getstate__(self):
            raise RuntimeError("unreadable")

    # missing: backend present, decoder missing
    tok = SimpleNamespace(_tokenizer=SimpleNamespace(decoder=None))
    assert _decoder_state(tok) == (_DECODER_MISSING, None)
    assert _decoder_profile(_DECODER_MISSING, None, _SCHEME_BYTE_LEVEL) is None

    # unreadable: decoder present, state unreadable
    tok = SimpleNamespace(_tokenizer=SimpleNamespace(decoder=BrokenDecoder()))
    assert _decoder_state(tok) == (_DECODER_UNREADABLE, None)
    assert _decoder_profile(_DECODER_UNREADABLE, None, _SCHEME_BYTE_LEVEL) is None

    # no_backend: a fake without an inspectable backend stays allowed
    tok = SimpleNamespace()
    assert _decoder_state(tok) == (_DECODER_NO_BACKEND, None)
    assert _decoder_profile(_DECODER_NO_BACKEND, None, _SCHEME_BYTE_LEVEL) is not None


def test_missing_decoder_change_after_cache_reevaluated():
    # A decoder change after caching is not masked by
    # the cache (the decoder is part of the fingerprint), including the
    # switch to decoder=None.
    hf, decoders, vocab = _real_bytelevel_tokenizer()
    b0 = TokenizerBundle.from_hf(hf, use_cache=True)  # cache: ByteLevel
    hf.backend_tokenizer.decoder = None  # same name_or_path
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(hf, use_cache=True)
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(hf, use_cache=False)
    # Switching back to ByteLevel yields a valid bundle again (new fingerprint).
    hf.backend_tokenizer.decoder = decoders.ByteLevel()
    b1 = TokenizerBundle.from_hf(hf, use_cache=True)
    assert b1.token_bytes(vocab["a"]) == b"a"
    assert b0.token_bytes(vocab["a"]) == b"a"


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
        pytest.skip("no _tokenizer backend")
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


def _tinyllama_bundle():
    tf = pytest.importorskip("transformers")
    try:
        tok = tf.AutoTokenizer.from_pretrained("TinyLlama/TinyLlama-1.1B-Chat-v1.0")
    except Exception as e:
        pytest.skip(f"TinyLlama tokenizer unavailable: {e}")
    tok.byte_fallback = True  # same explicit shim as the benchmark
    bundle = TokenizerBundle.from_hf(tok, use_cache=False)
    return tok, bundle


def test_tinyllama_strip_decoder_flag_and_literal_space():
    # The SP decoder drops one leading space of the whole
    # text, so the literal " hello" must NOT complete on the stream
    # " hello" (the piece ▁hello alone decodes to "hello"); the correct
    # stream is ▁ + ▁hello (text " hello").
    tok, bundle = _tinyllama_bundle()
    assert bundle.strip_first_space is True
    v = tok.get_vocab()
    id_space, id_piece = v["▁"], v["▁hello"]
    assert bundle.token_bytes(id_space) == b" "
    assert bundle.token_bytes(id_piece) == b" hello"
    with zc.Engine(mode="lazy", tokenizer=bundle) as e:
        with e.compile_literals([" hello"]) as c:
            with c.create_session() as s:
                allowed = set(s.allowed_token_ids())
                assert id_space in allowed
                assert id_piece not in allowed  # decodes to "hello", not " hello"
                with pytest.raises(zc.InvalidTokenError):
                    s.accept_token(id_piece)
                s.accept_token(id_space)
                assert id_piece in set(s.allowed_token_ids())
                s.accept_token(id_piece)
                assert s.can_end()
                assert tok.decode([id_space, id_piece]) == " hello"


def test_tinyllama_decode_equals_modelled_bytes():
    # Verify actual HF decode against the bytes the core
    # uses on whole generations, including the document-start space rule.
    # For pieces without <0xNN> byte fallback the model is: text =
    # join(images) with one leading space dropped.
    import random

    tok, bundle = _tinyllama_bundle()
    inv = {i: t for t, i in tok.get_vocab().items()}
    plain = [i for i in range(bundle.vocab_size) if "<0x" not in inv[i]]
    rng = random.Random(7)
    bad = []
    for _ in range(300):
        seq = [rng.choice(plain) for _ in range(rng.randint(1, 4))]
        data = b"".join(bundle.token_bytes(i) for i in seq)
        expected = data[1:] if data.startswith(b" ") else data
        actual = tok.decode(seq, skip_special_tokens=False).encode("utf-8")
        if actual != expected:
            bad.append((seq, expected, actual))
    assert bad == []


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
        # Regression: {"type":"integer"}, row = [prompt, '1'], no EOS
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
        model = FakeModel(SimpleNamespace(eos_token_id=[EOS_ID]))
        result = constrained_generate(model, tok, c, inputs={"input_ids": ids})
        proc = model.calls[0]["logits_processor"][0]
        assert proc.eos_ids == frozenset({EOS_ID})
        result.sessions[0].close()
        c.close()


@needs_core_masks
def test_extra_generation_config_eos_excluded_with_warning(cg):
    # An EOS id from the effective config that the native tokenizer
    # did not register is not a kernel terminator; it is excluded from the
    # processor's stop set with a warning (the mask never allows it), so the
    # kernel-confirmed EOS remains the only way to finish the document.
    torch, constrained_generate = cg
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a"], tokenizer=mini_bundle())
        ids = torch.zeros((1, 1), dtype=torch.long)
        tok = SimpleNamespace(eos_token_id=EOS_ID)
        model = FakeModel(SimpleNamespace(eos_token_id=[200, EOS_ID]))
        with pytest.warns(UserWarning, match="200"):
            result = constrained_generate(model, tok, c, inputs={"input_ids": ids})
        proc = model.calls[0]["logits_processor"][0]
        assert proc.eos_ids == frozenset({EOS_ID})
        result.sessions[0].close()
        c.close()


@needs_core_masks
def test_generation_config_eos_all_unconfirmed_rejected(cg):
    # If nothing survives the filter, the run is refused before
    # generate - the kernel would never confirm document completion.
    torch, constrained_generate = cg
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a"], tokenizer=mini_bundle())
        ids = torch.zeros((1, 1), dtype=torch.long)
        tok = SimpleNamespace(eos_token_id=200)
        model = FakeModel(SimpleNamespace(eos_token_id=201))
        with pytest.warns(UserWarning, match="200"):
            with pytest.raises(zc.UnsupportedModeError):
                constrained_generate(model, tok, c, inputs={"input_ids": ids})
        assert model.calls == []
        c.close()


@needs_core_masks
def test_generation_config_object_is_preflighted(cg):
    # A separate generation_config object must not bypass the
    # unsupported-mode checks of the effective configuration.
    torch, constrained_generate = cg
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a"], tokenizer=mini_bundle())
        ids = torch.zeros((1, 1), dtype=torch.long)
        tok = SimpleNamespace(eos_token_id=EOS_ID)
        model = FakeModel(SimpleNamespace(num_beams=1))
        with pytest.raises(zc.UnsupportedModeError):
            constrained_generate(
                model, tok, c, inputs={"input_ids": ids},
                generation_config=SimpleNamespace(num_beams=4),
            )
        with pytest.raises(zc.UnsupportedModeError):
            constrained_generate(
                model, tok, c, inputs={"input_ids": ids},
                generation_config=SimpleNamespace(
                    num_beams=1, forced_eos_token_id=42
                ),
            )
        assert model.calls == []
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


# ----------------------------------------------------------------------
# Added tokens go through the backend decoder; decoder
# chains are exact (order + multiplicity); real hf.decode is the reference.
# ----------------------------------------------------------------------

def _modelled_text(bundle, ids):
    """Text the core builds from a token sequence (bytes + rule)."""
    wire = b"".join(bundle.token_bytes(int(i)) for i in ids).decode("utf-8", "replace")
    if bundle.strip_first_space and wire.startswith(" "):
        wire = wire[1:]
    return wire


def _sp_chain_tokenizer(chain=None, added=None):
    tokenizers = pytest.importorskip("tokenizers")
    tf = pytest.importorskip("transformers")
    from tokenizers import decoders, models

    vocab = {"a": 0, "\u2581b": 1, "\u2581\u2581b": 2, "<eos>": 3}
    backend = tokenizers.Tokenizer(
        models.BPE(vocab=vocab, merges=[], byte_fallback=True)
    )
    if chain is None:
        chain = [
            decoders.Replace("\u2581", " "),
            decoders.ByteFallback(),
            decoders.Fuse(),
            decoders.Strip(" ", 1, 0),
        ]
    backend.decoder = decoders.Sequence(chain)
    hf = tf.PreTrainedTokenizerFast(
        tokenizer_object=backend, clean_up_tokenization_spaces=False
    )
    hf.byte_fallback = True
    if added:
        hf.add_tokens([tokenizers.AddedToken(added, normalized=False)])
    hf.add_special_tokens({"eos_token": "<eos>"})
    return hf


@pytest.mark.parametrize(
    "family,added,tokens",
    [
        ("bl", "\u0120hello", ["\u0120hello"]),
        ("bl", "\u010a", ["\u010a"]),
        ("bl", "\u0120", ["\u0120"]),
        ("bl", "\u03ba\u03cc\u03c3\u03bc\u03b5", ["\u03ba\u03cc\u03c3\u03bc\u03b5"]),
        # Mixed added token (byte outside the table): the whole token's UTF-8
        # including Ġ; at the start and in the middle.
        ("bl", "\u0120hello\U0001F642", ["\u0120hello\U0001F642"]),
        ("bl", "\u0120\u03b3\u03b5\u03b9\u03ac",
         ["\u0120\u03b3\u03b5\u03b9\u03ac"]),
        ("bl", "\u0120hello\U0001F642", ["\u0120", "\u0120hello\U0001F642"]),
        ("bl", "\u0120hello\U0001F642", ["\u0120hello\U0001F642", "\u0120"]),
        ("sp", None, ["\u2581b"]),
        ("sp", None, ["\u2581\u2581b"]),
        ("sp", "\u2581hello", ["\u2581hello"]),
        ("sp", "<0x41>", ["<0x41>"]),
        ("sp", None, ["\u2581b", "\u2581\u2581b"]),
    ],
)
def test_bundle_bytes_match_real_hf_decode(family, added, tokens):
    # The text contract is verified against the real hf.decode -
    # added Ġ/Ċ/▁, byte fallback, two spaces, first/middle token.
    if family == "bl":
        hf, _, _ = _real_bytelevel_tokenizer()
        tokenizers = pytest.importorskip("tokenizers")
        if added:
            hf.add_tokens([tokenizers.AddedToken(added, normalized=False)])
    else:
        hf = _sp_chain_tokenizer(added=added)
    ids = [hf.convert_tokens_to_ids(t) for t in tokens]
    actual = hf.decode(ids, skip_special_tokens=False)
    bundle = TokenizerBundle.from_hf(hf, use_cache=False)
    assert _modelled_text(bundle, ids) == actual


def test_double_strip_and_reordered_chains_rejected():
    # Two Strips (or Strip before Fuse) means different
    # semantics; the chain must be rejected, not collapsed to one flag.
    from tokenizers import decoders

    double = [
        decoders.Replace("\u2581", " "),
        decoders.ByteFallback(),
        decoders.Fuse(),
        decoders.Strip(" ", 1, 0),
        decoders.Strip(" ", 1, 0),
    ]
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(_sp_chain_tokenizer(double), use_cache=False)
    strip_first = [
        decoders.Replace("\u2581", " "),
        decoders.Strip(" ", 1, 0),
        decoders.Fuse(),
    ]
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(_sp_chain_tokenizer(strip_first), use_cache=False)
    # Strip without Fuse trims spaces PER TOKEN - a
    # different transform than dropping one leading space of the stream.
    no_fuse = [
        decoders.Replace("\u2581", " "),
        decoders.ByteFallback(),
        decoders.Strip(" ", 1, 0),
    ]
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(_sp_chain_tokenizer(no_fuse), use_cache=False)


def test_strip_without_fuse_decode_diverges_and_is_rejected():
    # Counterexample: for ['a','▁b'] the real hf.decode
    # yields 'ab', while the strip_first_space model would build 'a b' - a
    # false completed=True. The chain must be rejected.
    from tokenizers import decoders

    hf = _sp_chain_tokenizer([
        decoders.Replace("\u2581", " "),
        decoders.ByteFallback(),
        decoders.Strip(" ", 1, 0),
    ])
    ids = [hf.convert_tokens_to_ids("a"), hf.convert_tokens_to_ids("\u2581b")]
    assert hf.decode(ids, skip_special_tokens=False) == "ab"
    with pytest.raises(zc.UnsupportedTokenizerError):
        TokenizerBundle.from_hf(hf, use_cache=False)


@needs_core_masks
@pytest.mark.parametrize("added,wrong_literals", [
    ("\u0120hello", ["\u0120hello"]),
    # Byte-wise model produced ' hello🙂' and a false completed=True;
    # such a literal is now rejected.
    ("\u0120hello\U0001F642", [" hello\U0001F642"]),
    ("\u0120\u03b3\u03b5\u03b9\u03ac",
     [" \u03b3\u03b5\u03b9\u03ac"]),
])
def test_finish_row_matches_decode_for_added_token(zt, added, wrong_literals):
    # completed=True only for text matching the
    # actually decoded one; literals of wrong models do not pass.
    torch, proc_cls = zt
    tokenizers = pytest.importorskip("tokenizers")
    hf, _, _ = _real_bytelevel_tokenizer()
    hf.add_tokens([tokenizers.AddedToken(added, normalized=False)])
    hf.add_special_tokens({"eos_token": "<eos>"})
    tid = int(hf.convert_tokens_to_ids(added))
    eos_id = int(hf.eos_token_id)
    ids = [tid]
    actual = hf.decode(ids, skip_special_tokens=False)
    bundle = TokenizerBundle.from_hf(hf, use_cache=False)
    assert _modelled_text(bundle, ids) == actual
    cases = [(actual, True)] + [(w, False) for w in wrong_literals]
    for literal, expect_done in cases:
        with Engine(mode="lazy", tokenizer=bundle) as engine:
            c = engine.compile_literals([literal])
            s = c.create_session()
            proc = proc_cls([s], 1, eos_ids=[eos_id])
            done, _ = proc.finish_row(0, [0, tid, eos_id])
            assert done is expect_done
            s.close()
            c.close()


# ----------------------------------------------------------------------
# One final config for preflight, stopping and generate
# ----------------------------------------------------------------------

def _tiny_gpt2():
    torch = pytest.importorskip("torch")
    tf = pytest.importorskip("transformers")
    torch.manual_seed(42)
    model = tf.GPT2LMHeadModel(
        tf.GPT2Config(
            vocab_size=257,
            n_positions=32,
            n_embd=8,
            n_layer=1,
            n_head=1,
            bos_token_id=256,
            eos_token_id=256,
            pad_token_id=256,
        )
    ).eval()
    return torch, model


def _capture_generate(model, captured):
    orig = model.generate

    def spy(**kwargs):
        captured["cfg"] = kwargs.get("generation_config")
        captured["kwargs"] = kwargs
        return orig(**kwargs)

    model.generate = spy


@needs_core_masks
def test_extra_eos_filtered_in_the_config_generate_gets(cg):
    # An excluded EOS must not remain in the HF stopping criteria -
    # otherwise the output is cut off at 'b' (literal "abc").
    torch, constrained_generate = cg
    torch, model = _tiny_gpt2()
    captured = {}
    _capture_generate(model, captured)
    with Engine(tokenizer=mini_bundle()) as engine:
        c = engine.compile_literals(["abc"])
        with pytest.warns(UserWarning, match="98"):
            r = constrained_generate(
                model,
                SimpleNamespace(eos_token_id=256),
                c,
                inputs={"input_ids": torch.tensor([[0]])},
                max_new_tokens=8,
                eos_token_id=[98, 256],
            )
        cfg = captured["cfg"]
        assert cfg is not None  # the same config went into generate
        assert [int(i) for i in cfg.eos_token_id] == [256]
        assert r.sequences[0].tolist() == [0, 97, 98, 99, 256]
        assert r.completed == [True]
        assert r.stop_reason == ["eos"]
        for s in r.sessions:
            s.close()
        c.close()


@needs_core_masks
def test_explicit_num_beams_one_wins_over_model_config(cg):
    # An explicit num_beams=1 used to be dropped from kwargs, and
    # generate took the beam path from model.generation_config.num_beams=4.
    torch, constrained_generate = cg
    torch, model = _tiny_gpt2()
    model.generation_config.num_beams = 4
    captured = {}
    _capture_generate(model, captured)
    with Engine(tokenizer=mini_bundle()) as engine:
        c = engine.compile_literals(["abc"])
        r = constrained_generate(
            model,
            SimpleNamespace(eos_token_id=256),
            c,
            inputs={"input_ids": torch.tensor([[0]])},
            max_new_tokens=8,
            num_beams=1,
        )
        assert int(captured["cfg"].num_beams) == 1
        assert r.completed == [True]
        for s in r.sessions:
            s.close()
        c.close()
