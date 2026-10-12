"""
bolorgir - Python API of the adaptive structured-generation engine.

The core is the Zig library libbolorgir (C ABI); the bridge is the C
extension bolorgir._core (Limited API, abi3). The package imports
without torch/numpy/transformers; HF integration lives in
bolorgir.transformers (extra [transformers]).

    from bolorgir import Engine

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

import ctypes
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
    "CancelToken",
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

__version__ = "0.2.0rc1"


class UnsupportedModeError(ZigConstraintsError):
    """Incompatible generation settings (not a core blg_status code)."""


_MODES = {
    "lazy": _core.MODE_LAZY,
    "adaptive": _core.MODE_ADAPTIVE,
    "precompute": _core.MODE_PRECOMPUTE,
}

_MIB = 1024 * 1024

TokenizerLike = Union[TokenizerBundle, Any]


class CancelToken:
    """Cooperative cancellation flag shared with the native context.

    ``cancel()`` sets the flag; the next compile/fill_mask call of the
    Engine that registered the token fails with ``CancelledError`` - the
    check also runs when the mask would be served from the cache. The
    token must stay alive while it is registered; the Engine keeps a
    reference.
    """

    def __init__(self) -> None:
        self._flag = ctypes.c_uint8(0)

    def cancel(self) -> None:
        self._flag.value = 1

    @property
    def cancelled(self) -> bool:
        return bool(self._flag.value)


def _coerce_bundle(tokenizer: TokenizerLike) -> TokenizerBundle:
    if isinstance(tokenizer, TokenizerBundle):
        return tokenizer
    return TokenizerBundle.from_hf(tokenizer)


class Engine:
    """Core context: tokenizer, limits, mode, cache. Context manager.

    The tokenizer may be passed to the constructor or to the first
    compile/compile_literals call. When it is passed to the constructor,
    the native context (including the tokenizer tables) is built eagerly,
    so compile() measures grammar compilation only - mirroring engines
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
        work_limit_ops: int = 0,
        adaptive_min_hits: int = 0,
        adaptive_min_cost_ns: int = 0,
        precompute_max_states: int = 0,
        probe_max_states: int = 0,
        probe_max_work: int = 0,
        tokenizer: Optional[TokenizerLike] = None,
    ) -> None:
        if mode not in _MODES:
            raise InvalidArgumentError(
                f"unknown mode {mode!r}; expected one of: {sorted(_MODES)}"
            )
        for name, value in (
            ("memory_limit_mb", memory_limit_mb),
            ("cache_limit_mb", cache_limit_mb),
            ("session_limit_mb", session_limit_mb),
            ("schema_limit_mb", schema_limit_mb),
            ("max_depth", max_depth),
            ("max_threads", max_threads),
            ("work_limit_ops", work_limit_ops),
            ("adaptive_min_hits", adaptive_min_hits),
            ("adaptive_min_cost_ns", adaptive_min_cost_ns),
            ("precompute_max_states", precompute_max_states),
            ("probe_max_states", probe_max_states),
            ("probe_max_work", probe_max_work),
        ):
            if not isinstance(value, int) or value < 0:
                raise InvalidArgumentError(f"{name} must be a non-negative int")
        self._mode = mode
        self._config = dict(
            mode=_MODES[mode],
            max_depth=max_depth,
            max_threads_per_state=max_threads,
            memory_limit_bytes=memory_limit_mb * _MIB,
            cache_limit_bytes=cache_limit_mb * _MIB,
            session_limit_bytes=session_limit_mb * _MIB,
            schema_limit_bytes=schema_limit_mb * _MIB,
            work_limit_ops=work_limit_ops,
            adaptive_min_hits=adaptive_min_hits,
            adaptive_min_cost_ns=adaptive_min_cost_ns,
            precompute_max_states=precompute_max_states,
            probe_max_states=probe_max_states,
            probe_max_work=probe_max_work,
        )
        self._tokenizer = tokenizer
        self._cancel_tokens: list = []
        self._ctx: Optional[_core.Context] = None
        self._bundle: Optional[TokenizerBundle] = None
        self._eos_ids: tuple = ()
        if tokenizer is not None:
            # Eager context: tokenizer table construction is a one-time
            # Engine cost, not part of any single compile.
            self._ensure_context(None)

    def _ensure_context(self, tokenizer: Optional[TokenizerLike]) -> _core.Context:
        if self._ctx is not None:
            if tokenizer is not None and tokenizer is not self._tokenizer:
                raise InvalidArgumentError(
                    "tokenizer is bound to the context on first compile; "
                    "create a new Engine for a different tokenizer"
                )
            return self._ctx
        tok = tokenizer if tokenizer is not None else self._tokenizer
        if tok is None:
            raise InvalidArgumentError(
                "tokenizer required: pass tokenizer to Engine() or compile()"
            )
        bundle = _coerce_bundle(tok)
        # Keep the user-provided object for the binding check: the coerced
        # bundle may be a different object, and repeating the same tokenizer
        # at a later compile must not trip the "tokenizer is bound" error.
        self._tokenizer = tok
        self._bundle = bundle  # memo: eos_ids must not rebuild the table
        self._eos_ids = tuple(bundle.eos_ids)
        self._ctx = _core.Context(**self._config, **bundle.to_core_kwargs())
        return self._ctx

    def register_cancel_token(self, token: CancelToken) -> None:
        """Registers a cancellation flag on the native context.

        Once ``token.cancel()`` is called, the next compile/mask call of
        this Engine fails with ``CancelledError``; a cache hit does not
        bypass the check. The Engine keeps a reference to the token, the
        flag buffer must not be freed while the token is registered.
        """
        if not isinstance(token, CancelToken):
            raise InvalidArgumentError("expected CancelToken")
        ctx = self._ensure_context(None)
        ctx.set_cancel_flag(ctypes.addressof(token._flag))
        self._cancel_tokens.append(token)

    def cancel_token(self) -> CancelToken:
        """Creates, registers and returns a new ``CancelToken``."""
        token = CancelToken()
        self.register_cancel_token(token)
        return token

    def clear_cancel_tokens(self) -> None:
        """Unregisters all cancellation flags of this Engine."""
        if self._ctx is not None:
            self._ctx.set_cancel_flag(0)
        self._cancel_tokens.clear()

    def compile(
        self,
        schema: Union[dict, str, bytes],
        tokenizer: Optional[TokenizerLike] = None,
        profile: str = "canonical-v1",
        probe: Optional[str] = None,
    ) -> "Constraint":
        """Compile a JSON Schema (dict | str | bytes) into a Constraint.

        probe: None (default), "probe" or "strict" runs the compile-time
        generatability probe (ADR-0012) within the Engine's
        probe_max_states / probe_max_work budgets (0 = 256 states, 16M
        ops). A reachable state whose mask is undecided fails the compile
        with ResourceLimitError naming the token path and the subschema;
        "strict" also fails when the budget runs out first. The verdict is
        Constraint.probe_status.
        """
        flags = {None: 0, "probe": _core.COMPILE_PROBE, "strict": _core.COMPILE_PROBE_STRICT}.get(probe)
        if flags is None:
            raise InvalidArgumentError(f"probe: {probe!r} is not None, 'probe' or 'strict'")
        ctx = self._ensure_context(tokenizer)
        if isinstance(schema, dict):
            data = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        elif isinstance(schema, str):
            data = schema.encode("utf-8")
        elif isinstance(schema, (bytes, bytearray)):
            data = bytes(schema)
        else:
            raise InvalidArgumentError(
                f"schema: expected dict|str|bytes, got {type(schema).__name__}"
            )
        grammar = ctx.compile(kind=_core.KIND_JSON_SCHEMA, data=data, profile=profile, flags=flags)
        return Constraint(self, grammar)

    def compile_literals(
        self,
        literals: Sequence[str],
        tokenizer: Optional[TokenizerLike] = None,
    ) -> "Constraint":
        """Compile a list of literal alternatives into a Constraint."""
        ctx = self._ensure_context(tokenizer)
        if not isinstance(literals, (list, tuple)) or not literals:
            raise InvalidArgumentError("literals: a non-empty list of strings is required")
        if not all(isinstance(s, str) for s in literals):
            raise InvalidArgumentError("literals: all elements must be str")
        data = json.dumps(list(literals), ensure_ascii=False).encode("utf-8")
        grammar = ctx.compile(kind=_core.KIND_LITERAL_SET, data=data, profile=None)
        return Constraint(self, grammar)

    def compile_regex(
        self,
        pattern: str,
        tokenizer: Optional[TokenizerLike] = None,
    ) -> "Constraint":
        """Compile a standalone regular expression (ADR-0015): the whole
        generated text must match, as ECMA-262 ``^(?:pattern)$`` over
        Unicode scalar values, with the syntax of the JSON Schema
        ``pattern`` keyword. Needs a byte-complete vocabulary. Unsupported
        constructs raise UnsupportedFeatureError, malformed patterns
        InvalidSchemaError, both naming the byte offset."""
        ctx = self._ensure_context(tokenizer)
        if not isinstance(pattern, str):
            raise InvalidArgumentError("pattern: str expected")
        grammar = ctx.compile(kind=_core.KIND_REGEX, data=pattern.encode("utf-8"), profile=None)
        return Constraint(self, grammar)

    def stats(self) -> dict:
        """Context statistics."""
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

    @property
    def eos_ids(self) -> tuple:
        """EOS ids registered in the core tokenizer of this Engine.

        Used by bolorgir.transformers to verify that the effective HF
        EOS set is one the kernel can actually finalize. The
        coerced bundle is memoized by _ensure_context: rebuilding the table
        here would cost a tokenizer prepare per call.
        """
        if self._bundle is not None:
            return tuple(self._bundle.eos_ids)
        return self._eos_ids

    def _require_ctx(self) -> None:
        if self._ctx is None:
            raise WrongStateError("context not created yet (no compile call)")

    def close(self) -> None:
        if self._ctx is not None:
            self._ctx.close()
            self._ctx = None
        # The token table (~50k byte objects + blob, ~1 ms to release) is
        # dropped right here on close, not on object deallocation: otherwise
        # its release is deferred until refcounts collect the Engine itself
        # and lands in unrelated measurements (the bench cold compile used
        # to pay for that deallocation). To compile again after
        # close, pass the tokenizer explicitly; the eos_ids cache stays
        # readable (the property behavior is unchanged).
        self._bundle = None
        self._tokenizer = None

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

    def create_session(self, max_rollback_tokens: int = 0) -> "Session":
        """New session. max_rollback_tokens > 0 keeps a record of the state
        before each of the last that many accepts (ADR-0014), charged to
        the session memory budget, so Session.rollback can undo them;
        0 (default) keeps no history."""
        if not isinstance(max_rollback_tokens, int) or not (
                0 <= max_rollback_tokens <= _core.MAX_ROLLBACK_TOKENS):
            raise InvalidArgumentError(
                f"max_rollback_tokens must be an int in 0..{_core.MAX_ROLLBACK_TOKENS}")
        return Session(self._grammar.create_session(max_rollback_tokens))

    @property
    def mask_words(self) -> int:
        return self._engine.mask_words

    @property
    def vocab_size(self) -> int:
        return self._engine.vocab_size

    @property
    def probe_status(self) -> str:
        """Probe verdict kept with the compiled artifact (ADR-0012):
        "not_run", "inconclusive" (nothing found within the budget,
        nothing guaranteed) or "proven" (every reachable state has a
        decided mask for this vocabulary and these limits)."""
        return ("not_run", "inconclusive", "proven")[self._grammar.probe_status()]

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

    def fill_mask_into(self, buffer) -> None:
        """Write the mask directly into a writable C-contiguous buffer of
        at least mask_words*4 bytes (uint32 LE; 1 = allowed), without an
        intermediate allocation. The buffer may be larger than needed;
        only the first mask_words words are written."""
        self._s.fill_mask_into(buffer)

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

    def rollback(self, n_tokens: int) -> None:
        """Undo the last n_tokens successful accept_token calls (EOS
        included); masks are then exactly those before them. Needs
        create_session(max_rollback_tokens=...); more than the records held
        raises InvalidArgumentError without a change (ADR-0014)."""
        self._s.rollback(n_tokens)

    def validate_tokens(self, token_ids: Sequence[int]) -> int:
        """Length of the longest prefix of token_ids that consecutive
        accept_token calls would accept; the session does not change."""
        return self._s.validate_tokens(list(token_ids))

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
            raise InvalidArgumentError("fill_masks_batch: Session objects expected")
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
    raise exc(f"fill_masks_batch: row failed with status {code}")
