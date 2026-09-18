"""
Token table preparation for the core (zg_tokenizer_desc).

TokenizerBundle is an immutable description: exact bytes of every token,
eos_ids, special_ids, vocab_size. from_hf extracts bytes from HF
tokenizers of the supported families (FR-6):
- byte-level BPE (GPT-2 / Qwen / Llama-3 style);
- SentencePiece with byte_fallback=True (U+2581 -> space, <0xNN> -> byte).
Added tokens decode to their literal UTF-8 content. Any other scheme ->
UnsupportedTokenizerError.

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
import re
import struct
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ._core import InvalidArgumentError, UnsupportedTokenizerError

__all__ = ["TokenizerBundle"]

_ENTRY = struct.Struct("<IIQQ")  # zg_token_entry: id, reserved, offset, length
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
    the complete bytes_to_unicode alphabet — all 256 byte-chars as
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


def _decode_byte_level(token: str) -> Optional[bytes]:
    """Byte-level BPE token string -> exact bytes; None if the alphabet differs."""
    try:
        return bytes(_CHAR_TO_BYTE[c] for c in token)
    except KeyError:
        return None


def _hf_fingerprint(tokenizer: Any, vocab: Dict[str, int]) -> str:
    """Content fingerprint of the tokenizer identity (vocab + decoder settings)."""
    h = hashlib.sha256()
    h.update(repr(sorted(vocab.items())).encode("utf-8"))
    h.update(repr(bool(getattr(tokenizer, "byte_fallback", False))).encode())
    h.update(repr(getattr(tokenizer, "eos_token_id", None)).encode())
    h.update(repr(sorted(getattr(tokenizer, "all_special_ids", []) or [])).encode())
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
    ) -> None:
        if not token_bytes:
            raise InvalidArgumentError("token_bytes: пустой словарь")
        n = len(token_bytes)
        eos = sorted({int(i) for i in eos_ids})
        special = sorted({int(i) for i in special_ids})
        for name, ids in (("eos_ids", eos), ("special_ids", special)):
            for i in ids:
                if not 0 <= i < n:
                    raise InvalidArgumentError(f"{name}: id {i} вне [0, {n})")
        clash = set(eos) & set(special)
        if clash:
            raise InvalidArgumentError(
                f"special_ids пересекаются с eos_ids: {sorted(clash)}"
            )
        for i, b in enumerate(token_bytes):
            if not isinstance(b, (bytes, bytearray)):
                raise InvalidArgumentError(f"token_bytes[{i}]: ожидается bytes")
            if len(b) == 0 and i not in special:
                raise UnsupportedTokenizerError(
                    f"токен id={i} имеет нулевую длину (допустимо только для special)"
                )
        self._token_bytes = tuple(bytes(b) for b in token_bytes)
        self._eos_ids = tuple(eos)
        self._special_ids = tuple(special)
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
        (<0xNN>) decode to a single byte in any scheme. Added tokens
        (special or not) decode to their literal UTF-8 content, as HF
        decoding does; special non-EOS ids are excluded from documents.
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
                "TokenizerBundle.from_hf требует transformers: "
                "pip install zig-constraints[transformers]"
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
            raise UnsupportedTokenizerError("пустой словарь токенизатора")

        ids = sorted(vocab.values())
        n = len(ids)
        if ids != list(range(n)):
            raise UnsupportedTokenizerError(
                f"vocab ids не покрывают 0..{n - 1} ровно один раз"
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
                "схема токенизации не распознана: словарь не содержит полного "
                "байтового алфавита byte-level BPE и флаг byte_fallback не "
                "установлен. Поддерживаются byte-level BPE (GPT-2/Qwen/Llama-3 "
                "стиль) и SentencePiece с byte_fallback=True."
            )

        token_bytes: List[Optional[bytes]] = [None] * n
        for token, idx in vocab.items():
            data: Optional[bytes] = None
            # byte fallback first: <0xNN> is a single byte, not literal text
            m = _BYTE_FALLBACK_RE.fullmatch(token)
            if m is not None:
                data = bytes([int(m.group(1), 16)])
            if data is None and idx in added_decoder:
                # HF decodes added tokens to their literal content
                data = token.encode("utf-8") or b"\x00"
            if data is None:
                if scheme == _SCHEME_BYTE_LEVEL:
                    data = _decode_byte_level(token)
                else:
                    # SentencePiece: U+2581 -> space, rest is the literal
                    # UTF-8 of the piece; never the byte-level table (pieces
                    # may legitimately contain U+00A0..U+00FF chars).
                    data = token.replace(_SP_SPACE, " ").encode("utf-8")
            token_bytes[idx] = data

        undecoded = [i for i, b in enumerate(token_bytes) if b is None or len(b) == 0]
        if undecoded:
            raise UnsupportedTokenizerError(
                "не удалось извлечь байты токенов (id: "
                f"{undecoded[:5]}): неизвестная схема токенизации. "
                "Поддерживаются byte-level BPE (GPT-2/Qwen/Llama-3 стиль) и "
                "SentencePiece с byte_fallback=True."
            )

        return cls(
            token_bytes,  # type: ignore[arg-type]
            eos_ids=sorted(eos_ids),
            special_ids=special_only,
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
        )
