"""
zig_constraints — Python API of the adaptive structured-generation engine.

The core is the Zig library libzig_constraints (C ABI); the bridge is the C
extension zig_constraints._core (Limited API, abi3). The package imports
without torch/numpy/transformers; HF integration lives in
zig_constraints.transformers (extra [transformers]).

    from zig_constraints import Engine

    with Engine(mode="adaptive", memory_limit_mb=256) as engine:
        constraint = engine.compile(schema, tokenizer)
        with constraint.create_session() as session:
            mask = session.fill_mask()            # bytes, mask_words*4
            ids = session.allowed_token_ids()     # list[int]
            session.accept_token(ids[0])
            if session.can_end():
                session.finish()
"""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence, Union

from . import _core
from ._core import (
    BufferTooSmallError,
    BusyError,
    CancelledError,
    DeadEndError,
    InternalError,
    InvalidArgumentError,
    InvalidSchemaError,
    InvalidTokenError,
    ResourceLimitError,
    UnsatisfiableConstraintError,
    UnsupportedFeatureError,
    UnsupportedTokenizerError,
    WrongStateError,
    ZigConstraintsError,
    abi_version,
)
from .tokenizers import TokenizerBundle

__all__ = [
    "Engine",
    "Constraint",
    "Session",
    "TokenizerBundle",
    "UnsupportedModeError",
    "ZigConstraintsError",
    "InvalidArgumentError",
    "InvalidSchemaError",
    "UnsupportedFeatureError",
    "UnsatisfiableConstraintError",
    "UnsupportedTokenizerError",
    "InvalidTokenError",
    "DeadEndError",
    "ResourceLimitError",
    "CancelledError",
    "BusyError",
    "WrongStateError",
    "BufferTooSmallError",
    "InternalError",
    "abi_version",
    "fill_masks_batch",
]

__version__ = "0.1.0"


class UnsupportedModeError(ZigConstraintsError):
    """Incompatible generation settings (not a core zg_status code)."""


_MODES = {
    "lazy": _core.MODE_LAZY,
    "adaptive": _core.MODE_ADAPTIVE,
    "precompute": _core.MODE_PRECOMPUTE,
}

_MIB = 1024 * 1024

TokenizerLike = Union[TokenizerBundle, Any]


def _coerce_bundle(tokenizer: TokenizerLike) -> TokenizerBundle:
    if isinstance(tokenizer, TokenizerBundle):
        return tokenizer
    return TokenizerBundle.from_hf(tokenizer)


class Engine:
    """Core context: tokenizer, limits, mode, cache. Context manager.

    The tokenizer may be passed to the constructor or to the first
    compile/compile_literals call. When it is passed to the constructor,
    the native context (including the tokenizer tables) is built eagerly,
    so compile() measures grammar compilation only — mirroring engines
    whose compiler object is constructed before the compile call. With the
    lazy path (tokenizer first seen at compile) context creation is part
    of that compile call. The tokenizer of a live Engine cannot be
    changed.
    """

    def __init__(
        self,
        *,
        mode: str = "adaptive",
        memory_limit_mb: int = 256,
        cache_limit_mb: int = 64,
        session_limit_mb: int = 8,
        schema_limit_mb: int = 1,
        max_depth: int = 64,
        max_threads: int = 64,
        tokenizer: Optional[TokenizerLike] = None,
    ) -> None:
        if mode not in _MODES:
            raise InvalidArgumentError(
                f"неизвестный режим {mode!r}; допустимо: {sorted(_MODES)}"
            )
        for name, value in (
            ("memory_limit_mb", memory_limit_mb),
            ("cache_limit_mb", cache_limit_mb),
            ("session_limit_mb", session_limit_mb),
            ("schema_limit_mb", schema_limit_mb),
            ("max_depth", max_depth),
            ("max_threads", max_threads),
        ):
            if not isinstance(value, int) or value < 0:
                raise InvalidArgumentError(f"{name} должен быть неотрицательным int")
        self._mode = mode
        self._config = dict(
            mode=_MODES[mode],
            max_depth=max_depth,
            max_threads_per_state=max_threads,
            memory_limit_bytes=memory_limit_mb * _MIB,
            cache_limit_bytes=cache_limit_mb * _MIB,
            session_limit_bytes=session_limit_mb * _MIB,
            schema_limit_bytes=schema_limit_mb * _MIB,
        )
        self._tokenizer = tokenizer
        self._ctx: Optional[_core.Context] = None
        if tokenizer is not None:
            # Eager context: tokenizer table construction is a one-time
            # Engine cost, not part of any single compile.
            self._ensure_context(None)

    def _ensure_context(self, tokenizer: Optional[TokenizerLike]) -> _core.Context:
        if self._ctx is not None:
            if tokenizer is not None and tokenizer is not self._tokenizer:
                raise InvalidArgumentError(
                    "токенизатор привязан к контексту при первой компиляции; "
                    "создайте новый Engine для другого токенизатора"
                )
            return self._ctx
        tok = tokenizer if tokenizer is not None else self._tokenizer
        if tok is None:
            raise InvalidArgumentError(
                "требуется токенизатор: передайте tokenizer в Engine() или compile()"
            )
        bundle = _coerce_bundle(tok)
        # Keep the user-provided object for the binding check: the coerced
        # bundle may be a different object, and repeating the same tokenizer
        # at a later compile must not trip the "tokenizer is bound" error.
        self._tokenizer = tok
        self._ctx = _core.Context(**self._config, **bundle.to_core_kwargs())
        return self._ctx

    def compile(
        self,
        schema: Union[dict, str, bytes],
        tokenizer: Optional[TokenizerLike] = None,
        profile: str = "canonical-v1",
    ) -> "Constraint":
        """Compile a JSON Schema (dict | str | bytes) into a Constraint."""
        ctx = self._ensure_context(tokenizer)
        if isinstance(schema, dict):
            data = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        elif isinstance(schema, str):
            data = schema.encode("utf-8")
        elif isinstance(schema, (bytes, bytearray)):
            data = bytes(schema)
        else:
            raise InvalidArgumentError(
                f"schema: ожидается dict|str|bytes, получено {type(schema).__name__}"
            )
        grammar = ctx.compile(kind=_core.KIND_JSON_SCHEMA, data=data, profile=profile)
        return Constraint(self, grammar)

    def compile_literals(
        self,
        literals: Sequence[str],
        tokenizer: Optional[TokenizerLike] = None,
    ) -> "Constraint":
        """Compile a list of literal alternatives (FR-3) into a Constraint."""
        ctx = self._ensure_context(tokenizer)
        if not isinstance(literals, (list, tuple)) or not literals:
            raise InvalidArgumentError("literals: требуется непустой список строк")
        if not all(isinstance(s, str) for s in literals):
            raise InvalidArgumentError("literals: все элементы должны быть str")
        data = json.dumps(list(literals), ensure_ascii=False).encode("utf-8")
        grammar = ctx.compile(kind=_core.KIND_LITERAL_SET, data=data, profile=None)
        return Constraint(self, grammar)

    def stats(self) -> dict:
        """Context statistics (NFR-4)."""
        self._require_ctx()
        return self._ctx.stats()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def mask_words(self) -> int:
        self._require_ctx()
        return self._ctx.mask_words

    @property
    def vocab_size(self) -> int:
        self._require_ctx()
        return self._ctx.vocab_size

    def _require_ctx(self) -> None:
        if self._ctx is None:
            raise WrongStateError("контекст ещё не создан (не было compile)")

    def close(self) -> None:
        if self._ctx is not None:
            self._ctx.close()
            self._ctx = None

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        if exc_info[0] is not None:
            # Cleanup while an exception is already in flight is best-effort:
            # BusyError from live sessions referenced by traceback frames must
            # not mask the original error.
            try:
                self.close()
            except Exception:
                pass
            return False
        self.close()
        return False


class Constraint:
    """Compiled grammar shared by sessions. Context manager."""

    def __init__(self, engine: Engine, grammar: "_core.Grammar") -> None:
        self._engine = engine
        self._grammar = grammar

    def create_session(self) -> "Session":
        return Session(self._grammar.create_session())

    @property
    def mask_words(self) -> int:
        return self._engine.mask_words

    @property
    def vocab_size(self) -> int:
        return self._engine.vocab_size

    def close(self) -> None:
        # The core is idempotent to repeated close; operations on a closed
        # grammar raise WrongStateError from _core.
        if self._grammar is not None:
            self._grammar.close()

    def __enter__(self) -> "Constraint":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        if exc_info[0] is not None:
            try:
                self.close()
            except Exception:
                pass
            return False
        self.close()
        return False


class Session:
    """One generation. Operations are serialized by a per-session lock in _core.

    Context manager: exit releases the native session (destroy is valid from
    any state: active/finished/aborted).
    """

    def __init__(self, core_session: "_core.Session") -> None:
        self._s = core_session

    @property
    def mask_words(self) -> int:
        return self._s.mask_words

    @property
    def vocab_size(self) -> int:
        return self._s.vocab_size

    def fill_mask(self) -> bytes:
        """Bitmask of mask_words*4 bytes (uint32 LE; 1 = allowed)."""
        return self._s.fill_mask()

    def allowed_token_ids(self, mask: Optional[bytes] = None) -> list:
        """Allowed token ids from a mask (or a fresh fill_mask())."""
        if mask is None:
            mask = self.fill_mask()
        out = []
        for wi in range(len(mask) // 4):
            w = int.from_bytes(mask[wi * 4 : wi * 4 + 4], "little")
            while w:
                bit = (w & -w).bit_length() - 1
                out.append(wi * 32 + bit)
                w &= w - 1
        return out

    def mask_numpy(self, mask: Optional[bytes] = None):
        """Bool ndarray of length vocab_size. Requires numpy (extra [numpy])."""
        import numpy as np

        if mask is None:
            mask = self.fill_mask()
        bits = np.unpackbits(
            np.frombuffer(mask, dtype=np.uint8), bitorder="little"
        )
        return bits[: self.vocab_size].astype(bool)

    def accept_token(self, token_id: int) -> None:
        self._s.accept_token(token_id)

    def can_end(self) -> bool:
        return self._s.can_end()

    def finish(self) -> None:
        self._s.finish()

    def abort(self) -> None:
        self._s.abort()

    def stats(self) -> dict:
        return self._s.stats()

    def close(self) -> None:
        # Operations on a closed session raise WrongStateError from _core.
        if self._s is not None:
            self._s.close()

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        if exc_info[0] is not None:
            try:
                self.close()
            except Exception:
                pass
            return False
        self.close()
        return False


def fill_masks_batch(sessions: Sequence[Session]) -> list:
    """Masks for independent sessions -> list[bytes] in input order.

    The core processes rows independently; if any row fails, the typed
    exception of the first failing row is raised (by core status code).
    Duplicate sessions and overlapping concurrent batches are safe:
    _core deduplicates sessions and takes per-session locks in a single
    global order. An empty list is fine.
    """
    if not sessions:
        return []
    core_sessions = []
    for s in sessions:
        if not isinstance(s, Session):
            raise InvalidArgumentError("fill_masks_batch: ожидаются объекты Session")
        core_sessions.append(s._s)
    rows = _core.fill_masks_batch(core_sessions)
    masks = []
    for code, mask in rows:
        if code != 0:
            _raise_status(code)
        masks.append(mask)
    return masks


_STATUS_EXC = {
    1: InvalidArgumentError,
    2: InvalidSchemaError,
    3: UnsupportedFeatureError,
    4: UnsatisfiableConstraintError,
    5: UnsupportedTokenizerError,
    6: InvalidTokenError,
    7: DeadEndError,
    8: ResourceLimitError,
    9: CancelledError,
    10: BusyError,
    11: WrongStateError,
    12: BufferTooSmallError,
    13: InternalError,
}


def _raise_status(code: int) -> None:
    exc = _STATUS_EXC.get(code, InternalError)
    raise exc(f"fill_masks_batch: строка завершилась со статусом {code}")
