"""
Token table preparation for the core (blg_tokenizer_desc).

TokenizerBundle is an immutable description: exact bytes of every token,
eos_ids, special_ids, vocab_size. from_hf extracts bytes from HF
tokenizers of the supported families (FR-6):
- byte-level BPE (GPT-2 / Qwen / Llama-3 style);
- SentencePiece with byte_fallback=True (U+2581 -> space, <0xNN> -> byte).
Added tokens go through the same decoder as regular tokens (Ġ/▁ ->
space; a mixed token containing a char outside the byte alphabet -
the UTF-8 of the whole token as-is), and the decoder chain is checked
for order, multiplicity and the mandatory Fuse before Strip. Any other
scheme -> UnsupportedTokenizerError.

The family (scheme) is detected once for the whole vocab, never per
token: token text alone is ambiguous (U+00A0..U+00FF chars are valid
both in byte-level BPE tokens and in SentencePiece pieces), and the
backend model type does not discriminate either (HF stores SP-style
Llama vocabs as BPE with byte_fallback). See _detect_scheme.

transformers is imported only inside from_hf: this module is safe to
import without the extras installed.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

from ._core import InvalidArgumentError, UnsupportedTokenizerError

__all__ = ["TokenizerBundle"]

_ENTRY = struct.Struct("<IIQQ")  # blg_token_entry: id, reserved, offset, length
_BYTE_FALLBACK_RE = re.compile(r"<0x([0-9A-Fa-f]{2})>")
_SP_SPACE = "\u2581"  # SentencePiece word-start marker

# Per-process bundle cache: (name_or_path, revision, fingerprint) -> bundle.
# The fingerprint covers the vocab and decoder settings, so tokenizers that
# share a name but not a vocab never share a bundle.
_BUNDLE_CACHE: Dict[Tuple[str, Optional[str], str], "TokenizerBundle"] = {}


def _bytes_to_unicode() -> Dict[int, str]:
    """Canonical byte-level BPE table (GPT-2 bytes_to_unicode)."""
    bs = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("¡"), ord("¬") + 1))
        + list(range(ord("®"), ord("ÿ") + 1))
    )
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, map(chr, cs)))


_BYTE_TO_CHAR = _bytes_to_unicode()
_CHAR_TO_BYTE = {c: b for b, c in _BYTE_TO_CHAR.items()}

_SCHEME_BYTE_LEVEL = "byte_level"
_SCHEME_SP_BYTE_FALLBACK = "sp_byte_fallback"


def _detect_scheme(vocab: Dict[str, int], byte_fallback: bool) -> Optional[str]:
    """Vocab-level scheme detection (one decision for the whole table).

    Discriminator: a byte-level BPE vocab (GPT-2/Qwen/Llama-3) contains
    the complete bytes_to_unicode alphabet - all 256 byte-chars as
    single-character tokens; a SentencePiece byte_fallback vocab
    (Llama-2/Gemma slow) does not (it uses U+2581 pieces and <0xNN>
    byte tokens instead). Verified on GPT-2, Qwen2.5, TinyLlama and
    hf-internal-testing/llama-tokenizer.
    """
    if all(c in vocab for c in _BYTE_TO_CHAR.values()):
        return _SCHEME_BYTE_LEVEL
    if byte_fallback:
        return _SCHEME_SP_BYTE_FALLBACK
    return None


class _DecoderProfile(NamedTuple):
    """What the backend decoder does to token text (FR-6).

    byte_level: the byte-level BPE reverse table applies to every token;
    byte_fallback: a token matching <0xNN> decodes to that single byte;
    sp_space: U+2581 decodes to a space;
    strip_first_space: the decoder drops one leading space of the whole
    decoded text (Strip(" ", start=1, stop=0)), modelled by the kernel
    via the corresponding tokenizer flag.
    """

    byte_level: bool = False
    byte_fallback: bool = False
    sp_space: bool = False
    strip_first_space: bool = False


# Three distinct reasons why the decoder state may be unavailable: they
# used to collapse into a single None, and a fast backend with
# decoder=None wrongly enabled the ByteLevel heuristic.
_DECODER_NO_BACKEND = "no_backend"  # no inspectable backend (slow / fake)
_DECODER_MISSING = "missing"        # fast backend present, decoder missing
_DECODER_UNREADABLE = "unreadable"  # decoder present, state unreadable
_DECODER_OK = "ok"                  # decoder state read


def _decoder_state(tokenizer: Any) -> Tuple[str, Optional[dict]]:
    """Structured backend decoder state (``__getstate__``).

    Returns (kind, data):
    - no_backend: no inspectable backend (slow tokenizer / test fake) -
      the inherited attribute-based rules apply;
    - missing: fast backend present, but no decoder set;
    - unreadable: decoder present, but its state could not be read;
    - ok: decoder state read and is a dict.
    """
    backend = getattr(tokenizer, "_tokenizer", None)
    if backend is None:
        return (_DECODER_NO_BACKEND, None)
    dec = getattr(backend, "decoder", None)
    if dec is None:
        return (_DECODER_MISSING, None)
    try:
        state = dec.__getstate__()
        if isinstance(state, (bytes, bytearray)):
            state = state.decode("utf-8")
        data = json.loads(state)
    except Exception:
        return (_DECODER_UNREADABLE, None)
    if isinstance(data, dict):
        return (_DECODER_OK, data)
    return (_DECODER_UNREADABLE, None)


def _decoder_profile(kind: str, state: Optional[dict], scheme: str) -> Optional[_DecoderProfile]:
    """Validates the backend decoder against the supported families.

    None means unsupported and the caller refuses the tokenizer (FR-6:
    no approximation for decoders whose output depends on context in
    ways the adapter does not model)."""
    if kind == _DECODER_NO_BACKEND:
        # No inspectable backend (slow tokenizer / test fake): the
        # inherited attribute-based rules. The generic permissive fallback
        # for fast backends is NOT applied here - a fast
        # backend without decoder is handled separately and rejected.
        return _DecoderProfile(
            byte_level=(scheme == _SCHEME_BYTE_LEVEL),
            byte_fallback=(scheme == _SCHEME_SP_BYTE_FALLBACK),
            sp_space=(scheme == _SCHEME_SP_BYTE_FALLBACK),
        )
    if kind != _DECODER_OK:
        # Fast backend without decoder (HF joins tokens with spaces) or
        # with unreadable state: decoding semantics not proven, refuse
        # before generation instead of the ByteLevel heuristic.
        return None
    assert state is not None
    kind = state.get("type")
    if scheme == _SCHEME_BYTE_LEVEL:
        # Only a plain ByteLevel decoder is exact for this family.
        if kind == "ByteLevel":
            return _DecoderProfile(byte_level=True)
        if kind == "Sequence":
            parts = state.get("decoders") or []
            if len(parts) == 1 and parts[0].get("type") == "ByteLevel":
                return _DecoderProfile(byte_level=True)
        return None
    # SentencePiece byte fallback: the exact verified sequence
    # Replace(▁->' ') [ByteFallback] [Fuse] [Strip(' ', start=1, stop=0)].
    # Order and multiplicity affect the result (two Strips, or Strip before
    # Fuse, means different semantics), so the chain is checked component
    # by component, not as a set. Strip without Fuse trims each
    # token separately (strip.rs: decode_chain is called per token), which
    # the kernel does not model.
    if kind != "Sequence":
        return None
    parts = state.get("decoders") or []
    n = len(parts)
    idx = 0
    if idx >= n or parts[idx].get("type") != "Replace":
        return None
    pat = parts[idx].get("pattern") or {}
    if pat.get("String") != _SP_SPACE or parts[idx].get("content") != " ":
        return None
    idx += 1
    prof = _DecoderProfile(sp_space=True)
    if idx < n and parts[idx].get("type") == "ByteFallback":
        prof = prof._replace(byte_fallback=True)
        idx += 1
    has_fuse = False
    if idx < n and parts[idx].get("type") == "Fuse":
        has_fuse = True
        idx += 1
    if idx < n and parts[idx].get("type") == "Strip":
        part = parts[idx]
        if part.get("content") != " " or part.get("start") != 1 or part.get("stop") != 0:
            return None
        # The kernel models dropping one leading space of the WHOLE text
        # (BLG_TOKENIZER_STRIP_LEAD_SPACE); without Fuse, Strip trims a
        # space from each token ('a', '▁b' -> 'ab', not 'a b').
        if not has_fuse:
            return None
        prof = prof._replace(strip_first_space=True)
        idx += 1
    if idx != n:
        return None
    return prof


def _decode_byte_level(token: str) -> bytes:
    """Token bytes with the exact decoders.ByteLevel semantics.

    Source: tokenizers 0.23 (pre_tokenizers/byte_level.rs, decode_chain):
    token bytes are a try_fold over the inverse bytes_to_unicode table, but
    if AT LEAST ONE token char is outside the table, the fallback is the
    UTF-8 of the WHOLE token (t.as_bytes()), including the chars that are
    in the table. A per-element fallback is wrong: the added token
    'Ġhello🙂' decodes to 'Ġhello🙂', not ' hello🙂'. All chars in the
    table (regular byte-level BPE tokens) map to their bytes.
    """
    buf = bytearray()
    for c in token:
        b = _CHAR_TO_BYTE.get(c)
        if b is None:
            return token.encode("utf-8")
        buf.append(b)
    return bytes(buf)


def _hf_fingerprint(tokenizer: Any, vocab: Dict[str, int]) -> str:
    """Content fingerprint of the tokenizer identity (vocab + decoder settings)."""
    h = hashlib.sha256()
    h.update(repr(sorted(vocab.items())).encode("utf-8"))
    h.update(repr(bool(getattr(tokenizer, "byte_fallback", False))).encode())
    h.update(repr(getattr(tokenizer, "eos_token_id", None)).encode())
    h.update(repr(sorted(getattr(tokenizer, "all_special_ids", []) or [])).encode())
    # The backend decoder changes the byte image of every token, so it is
    # part of the identity (FR-6: vocab + byte mapping + decoder settings).
    h.update(repr(_decoder_state(tokenizer)).encode("utf-8"))
    added = getattr(tokenizer, "added_tokens_decoder", {}) or {}
    h.update(
        repr(
            sorted(
                (i, str(getattr(t, "content", "")), bool(getattr(t, "special", False)))
                for i, t in added.items()
            )
        ).encode()
    )
    return h.hexdigest()


class TokenizerBundle:
    """Token table for a core context. Construct via from_* classmethods."""

    def __init__(
        self,
        token_bytes: Sequence[bytes],
        eos_ids: Sequence[int] = (),
        special_ids: Sequence[int] = (),
        strip_first_space: bool = False,
    ) -> None:
        if not token_bytes:
            raise InvalidArgumentError("token_bytes: empty vocab")
        n = len(token_bytes)
        eos = sorted({int(i) for i in eos_ids})
        special = sorted({int(i) for i in special_ids})
        for name, ids in (("eos_ids", eos), ("special_ids", special)):
            for i in ids:
                if not 0 <= i < n:
                    raise InvalidArgumentError(f"{name}: id {i} outside [0, {n})")
        clash = set(eos) & set(special)
        if clash:
            raise InvalidArgumentError(
                f"special_ids overlap eos_ids: {sorted(clash)}"
            )
        for i, b in enumerate(token_bytes):
            if not isinstance(b, (bytes, bytearray)):
                raise InvalidArgumentError(f"token_bytes[{i}]: bytes expected")
            if len(b) == 0 and i not in special:
                raise UnsupportedTokenizerError(
                    f"token id={i} has zero length (allowed for special tokens only)"
                )
        self._token_bytes = tuple(bytes(b) for b in token_bytes)
        self._eos_ids = tuple(eos)
        self._special_ids = tuple(special)
        self._strip_first_space = bool(strip_first_space)
        self._vocab_size = n
        self._blob: Optional[bytes] = None
        self._index: Optional[bytes] = None

    @classmethod
    def from_token_bytes(
        cls,
        token_bytes: Sequence[bytes],
        eos_ids: Sequence[int] = (),
        special_ids: Sequence[int] = (),
    ) -> "TokenizerBundle":
        """Manual table: token bytes for each id 0..vocab-1."""
        return cls(token_bytes, eos_ids=eos_ids, special_ids=special_ids)

    @classmethod
    def from_hf(cls, tokenizer: Any, *, use_cache: bool = True) -> "TokenizerBundle":
        """Extract exact token bytes from an HF tokenizer (FR-6).

        Supported families: byte-level BPE (GPT-2/Qwen/Llama-3 style; vocab
        strings map to bytes via the inverse bytes_to_unicode table) and
        SentencePiece with byte_fallback=True (U+2581 -> space, <0xNN> ->
        one byte, other pieces -> literal UTF-8). The family is detected
        once for the whole vocab (_detect_scheme); the byte-level table is
        never applied to a SentencePiece vocab. Byte fallback tokens
        (<0xNN>) decode to a single byte in any scheme (empirical: for
        added tokens too). Added tokens go through the same decoder
        (Ġ/▁ -> space; a mixed token containing a char outside the byte
        alphabet - the UTF-8 of the whole token as-is), matching how HF
        decoding handles them; special non-EOS
        ids are excluded from documents. Decoder chains are checked for
        component order and multiplicity; Strip without Fuse is rejected.
        Any other scheme -> UnsupportedTokenizerError (no approximation).

        Results are cached per process; the key includes name_or_path,
        revision and a fingerprint of the vocab, decoder flags and special
        ids, so tokenizers sharing a name but not a vocab never share a
        bundle.
        """
        try:
            import transformers  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "TokenizerBundle.from_hf requires transformers: "
                "pip install Bolorgir[transformers]"
            ) from e

        name = str(getattr(tokenizer, "name_or_path", "") or "")
        revision = getattr(tokenizer, "revision", None)
        vocab: Optional[Dict[str, int]] = None
        key: Optional[Tuple[str, Optional[str], str]] = None
        if use_cache and name:
            vocab = tokenizer.get_vocab()
            key = (name, revision, _hf_fingerprint(tokenizer, vocab))
            cached = _BUNDLE_CACHE.get(key)
            if cached is not None:
                return cached

        bundle = cls._build_from_hf(tokenizer, vocab)
        if key is not None:
            _BUNDLE_CACHE[key] = bundle
        return bundle

    @classmethod
    def _build_from_hf(
        cls, tokenizer: Any, vocab: Optional[Dict[str, int]] = None
    ) -> "TokenizerBundle":
        if vocab is None:
            vocab = tokenizer.get_vocab()  # str -> id, including added tokens
        if not vocab:
            raise UnsupportedTokenizerError("empty tokenizer vocab")

        ids = sorted(vocab.values())
        n = len(ids)
        if ids != list(range(n)):
            raise UnsupportedTokenizerError(
                f"vocab ids do not cover 0..{n - 1} exactly once"
            )

        added_decoder = getattr(tokenizer, "added_tokens_decoder", {}) or {}
        special_ids = {
            idx
            for idx, tok in added_decoder.items()
            if getattr(tok, "special", False)
        }
        special_ids.update(getattr(tokenizer, "all_special_ids", []) or [])

        eos_ids = set()
        eos_token_id = getattr(tokenizer, "eos_token_id", None)
        if eos_token_id is not None:
            eos_ids.add(int(eos_token_id))
        # special non-EOS ids are forbidden inside the document
        special_only = sorted(i for i in special_ids if i not in eos_ids)

        byte_fallback = bool(getattr(tokenizer, "byte_fallback", False))
        scheme = _detect_scheme(vocab, byte_fallback)
        if scheme is None:
            raise UnsupportedTokenizerError(
                "tokenization scheme not recognized: the vocab has neither "
                "the full byte-level BPE byte alphabet nor the byte_fallback "
                "flag set. Supported: byte-level BPE (GPT-2/Qwen/Llama-3 "
                "style) and SentencePiece with byte_fallback=True."
            )
        decoder_kind, decoder_state = _decoder_state(tokenizer)
        profile = _decoder_profile(decoder_kind, decoder_state, scheme)
        if profile is None:
            why = (
                "the fast backend has no decoder (tokens are joined with "
                "spaces) or its state is unreadable"
                if decoder_kind != _DECODER_NO_BACKEND
                else "token text depends on context (or is defined by a "
                "non-standard chain)"
            )
            raise UnsupportedTokenizerError(
                f"tokenizer decoder not supported: {why}, and the adapter "
                "does not model it (FR-6). Supported: byte-level ByteLevel "
                "and SentencePiece chains Replace(\u2581->' ') / ByteFallback / "
                "Fuse / Strip(' ', start=1, stop=0); Strip is allowed only "
                "after Fuse."
            )

        token_bytes: List[Optional[bytes]] = [None] * n
        for token, idx in vocab.items():
            data: Optional[bytes] = None
            # <0xNN> is a single byte only when the decoder does that;
            # empirical: ByteFallback applies to added tokens too.
            m = _BYTE_FALLBACK_RE.fullmatch(token)
            if m is not None and profile.byte_fallback:
                data = bytes([int(m.group(1), 16)])
            if data is None:
                # Added tokens go through the same decoder as regular ones:
                # ByteLevel/SP replace chars (Ġ/▁ -> space), so a literal
                # UTF-8 encoding is wrong.
                if profile.byte_level:
                    data = _decode_byte_level(token)
                else:
                    # SentencePiece: U+2581 -> space, rest is the literal
                    # UTF-8 of the piece; never the byte-level table (pieces
                    # may legitimately contain U+00A0..U+00FF chars).
                    piece = token.replace(_SP_SPACE, " ") if profile.sp_space else token
                    data = piece.encode("utf-8")
            token_bytes[idx] = data

        undecoded = [i for i, b in enumerate(token_bytes) if b is None or len(b) == 0]
        if undecoded:
            raise UnsupportedTokenizerError(
                "failed to extract token bytes (id: "
                f"{undecoded[:5]}): unknown tokenization scheme. "
                "Supported: byte-level BPE (GPT-2/Qwen/Llama-3 style) and "
                "SentencePiece with byte_fallback=True."
            )

        return cls(
            token_bytes,  # type: ignore[arg-type]
            eos_ids=sorted(eos_ids),
            special_ids=special_only,
            strip_first_space=profile.strip_first_space,
        )

    @property
    def vocab_size(self) -> int:
        return self._vocab_size

    @property
    def eos_ids(self) -> Tuple[int, ...]:
        return self._eos_ids

    @property
    def special_ids(self) -> Tuple[int, ...]:
        return self._special_ids

    @property
    def strip_first_space(self) -> bool:
        """True when the decoder drops one leading space of the whole
        text (HF SentencePiece Strip(" ", start=1, stop=0)); the kernel
        then models the decoded text (FR-6)."""
        return self._strip_first_space

    @property
    def mask_words(self) -> int:
        return (self._vocab_size + 31) // 32

    def token_bytes(self, token_id: int) -> bytes:
        return self._token_bytes[token_id]

    def to_core_kwargs(self) -> dict:
        """Key arguments for _core.Context (blob + packed index)."""
        if self._blob is None or self._index is None:
            blob_parts = []
            index_parts = []
            offset = 0
            for i, data in enumerate(self._token_bytes):
                blob_parts.append(data)
                index_parts.append(_ENTRY.pack(i, 0, offset, len(data)))
                offset += len(data)
            self._blob = b"".join(blob_parts)
            self._index = b"".join(index_parts)
        return dict(
            vocab_size=self._vocab_size,
            token_blob=self._blob,
            token_index=self._index,
            eos_ids=list(self._eos_ids),
            special_ids=list(self._special_ids),
            flags=1 if self._strip_first_space else 0,
        )
