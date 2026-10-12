"""vLLM structured-output backend adapter (ADR-0016).

This module implements the ``StructuredOutputBackend`` /
``StructuredOutputGrammar`` interface of vLLM 0.31.0 on top of the
Bolorgir kernel. The backend registers through the pinned upstream patch
``integrations/vllm/vllm-0.31.0-bolorgir.patch``: vLLM 0.31.0 keeps a
closed backend list in its configuration and creates backends in a fixed
``if``/``elif`` chain, so ``bolorgir[vllm]`` alone cannot register a new
name. See ``docs/integrations/vllm.md`` for installation and limits.

Contract highlights (documented in full in the docs and ADR-0016):

- request types: ``JSON`` (spec-v1 profile, ``number_format=value``,
  compact serialization), ``JSON_OBJECT``, ``CHOICE`` (literal set) and
  ``REGEX`` (ADR-0015). ``GRAMMAR`` and ``STRUCTURAL_TAG`` are refused
  explicitly; there is no fallback to another backend.
- tokenizer: an HF tokenizer is extracted to an exact byte table by
  ``TokenizerBundle.from_hf`` (byte-level BPE or SentencePiece with byte
  fallback); any other tokenizer is refused at server start with the
  reason.
- masks: the int32 row layout of vLLM (bit ``t % 32`` of word ``t / 32``)
  matches ``blg_fill_mask``; rows are written zero-copy into the shared
  tensor row, with the GIL released during the native call.
- EOS and stop tokens: while the document cannot end, the request's stop
  ids are masked out; once the language can complete, the mask offers
  exactly the terminal set (the stop ids passed by vLLM, or the
  tokenizer EOS ids when vLLM passes none). A terminal token that the
  engine samples marks the grammar terminated; tokens after it are
  ignored, like the reference backends.
- rollback and draft validation map to the session history
  (``blg_session_rollback``) and the state-preserving
  ``blg_validate_tokens``; the session depth is the speculative token
  count plus one.
- failures: a failing mask fill or a rejected accepted token marks the
  grammar failed. ``accept_tokens`` then returns False, which makes vLLM
  terminate exactly that request with an error; no unconstrained
  generation is produced and other requests of the batch continue.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, FrozenSet, Optional

import bolorgir

try:
    from vllm.exceptions import VLLMValidationError
    from vllm.v1.structured_output.backend_types import (
        StructuredOutputBackend,
        StructuredOutputGrammar,
        StructuredOutputOptions,
    )
except ImportError as exc:  # pragma: no cover - exercised by tests separately
    raise ImportError(
        "bolorgir.vllm implements the vLLM structured-output backend "
        "interface and can only be imported inside vLLM 0.31.x; install "
        "the pinned stack and apply "
        "integrations/vllm/vllm-0.31.0-bolorgir.patch "
        "(see docs/integrations/vllm.md)"
    ) from exc

logger = logging.getLogger("bolorgir.vllm")

#: Request profile for JSON Schema requests (spec-v1, number_format=value,
#: compact serialization; the engine default stays canonical-v1, the
#: serving path selects spec-v1 explicitly).
SPEC_PROFILE = "spec-v1"

#: The only vLLM versions this adapter is written against; the patch
#: targets the exact upstream tag.
SUPPORTED_VLLM_TAG = "v0.31.0"


class BolorgirBackendError(RuntimeError):
    """Backend setup failure (tokenizer, vocabulary, configuration)."""


class BolorgirRequestError(ValueError):
    """A request the bolorgir backend does not support.

    Raised while compiling; vLLM carries it to the scheduler as the
    failing request's error, never falls back to another backend.
    """


def _hf_tokenizer_of(tokenizer: Any) -> Any:
    """The HF tokenizer behind a vLLM tokenizer object.

    In HF tokenizer mode vLLM passes a cached copy of the transformers
    tokenizer itself; a few paths wrap it in a proxy carrying the HF
    object as ``tokenizer``.
    """
    if hasattr(tokenizer, "get_vocab") and hasattr(tokenizer, "added_tokens_decoder"):
        return tokenizer
    inner = getattr(tokenizer, "tokenizer", None)
    if inner is not None and hasattr(inner, "get_vocab"):
        return inner
    return tokenizer


def tokenizer_bundle_of(tokenizer: Any) -> bolorgir.TokenizerBundle:
    """Extract the exact byte table of a vLLM tokenizer.

    The tokenizer bundle is cached per process by ``from_hf`` keyed by
    name, revision and a content fingerprint of the vocab and decoder, so
    concurrent requests share one table. Unsupported tokenizer families
    are refused with the underlying reason, naming the supported ones.
    """
    hf = _hf_tokenizer_of(tokenizer)
    try:
        return bolorgir.TokenizerBundle.from_hf(hf)
    except bolorgir.UnsupportedTokenizerError as exc:
        raise BolorgirBackendError(
            f"unsupported tokenizer for the bolorgir backend: {exc}. "
            "Supported families: byte-level BPE (GPT-2/Qwen/Llama-3 style) "
            "and SentencePiece with byte_fallback; see "
            "docs/integrations/vllm.md"
        ) from exc


def _terminal_ids_of(stop_token_ids: Optional[FrozenSet[int]], eos_ids: Any) -> Optional[FrozenSet[int]]:
    """The terminal token set of a request.

    vLLM passes the request's EOS and user stop ids; the set replaces the
    tokenizer default, like the reference backends' override semantics.
    ``None`` or an empty set keeps the tokenizer EOS ids.
    """
    if stop_token_ids:
        return frozenset(int(t) for t in stop_token_ids)
    return None


def _spec_text(spec: Any) -> str:
    if isinstance(spec, str):
        return spec
    return json.dumps(spec)


class _TokenFingerprint:
    """Diagnostics: what tokenizer table a grammar was compiled with."""

    __slots__ = ("vocab_size", "eos_ids")

    def __init__(self, bundle: bolorgir.TokenizerBundle) -> None:
        self.vocab_size = bundle.vocab_size
        self.eos_ids = tuple(bundle.eos_ids)


@dataclass
class BolorgirBackend(StructuredOutputBackend):
    """Engine-level backend. Constructed by the patched vLLM manager with
    ``(vllm_config, tokenizer=..., vocab_size=...)``."""

    # Extra knobs beyond the base fields; defaults match the serving build.
    mode: str = "adaptive"
    session_limit_mb: int = 8
    _engine: Any = field(init=False, repr=False, default=None)
    _bundle: Any = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        self._model_vocab_size = int(self.vocab_size)
        if self._model_vocab_size <= 0:
            raise BolorgirBackendError(f"invalid model vocab size {self.vocab_size}")
        bundle = tokenizer_bundle_of(self.tokenizer)
        if bundle.vocab_size > self._model_vocab_size:
            raise BolorgirBackendError(
                f"model vocabulary ({self._model_vocab_size}) is smaller than "
                f"the tokenizer table ({bundle.vocab_size}); refusing to serve")
        self._bundle = bundle
        self._fingerprint = _TokenFingerprint(bundle)
        self._engine = bolorgir.Engine(
            mode=self.mode,
            tokenizer=bundle,
            session_limit_mb=self.session_limit_mb,
        )
        self._model_words = (self._model_vocab_size + 31) // 32
        self._num_spec_tokens = 0
        speculative = getattr(self.vllm_config, "speculative_config", None)
        if speculative is not None:
            self._num_spec_tokens = int(
                getattr(speculative, "num_speculative_tokens", 0) or 0)
        # Rollback depth: one record per speculative token plus margin for
        # the accepted terminal token (ADR-0014).
        self._rollback_depth = self._num_spec_tokens + 1
        self._lock = threading.Lock()
        logger.info(
            "bolorgir structured-output backend active: extension=%s "
            "bolorgir=%s mode=%s vocab=%d tokenizer_vocab=%d eos=%s "
            "config_vocab=%d num_spec_tokens=%d",
            getattr(bolorgir._core, "__file__", "?"),
            bolorgir.__version__,
            self.mode,
            self._model_vocab_size,
            bundle.vocab_size,
            self._fingerprint.eos_ids,
            self._model_vocab_size,
            self._num_spec_tokens,
        )

    # -- engine-level interface -------------------------------------------

    def compile_grammar(
        self,
        request_type: StructuredOutputOptions,
        grammar_spec: str,
        stop_token_ids: Optional[Any] = None,
    ) -> "BolorgirGrammar":
        terminals = _terminal_ids_of(
            frozenset(stop_token_ids) if stop_token_ids else None,
            self._fingerprint.eos_ids,
        )
        if terminals is not None:
            for t in terminals:
                if t < 0 or t >= self._model_vocab_size:
                    raise BolorgirRequestError(
                        f"stop token id {t} is outside the model vocabulary "
                        f"({self._model_vocab_size})")
        try:
            constraint = self._compile(request_type, grammar_spec)
        except bolorgir.ZigConstraintsError as exc:
            raise BolorgirRequestError(
                f"bolorgir cannot compile the {request_type.name} request: "
                f"{type(exc).__name__}: {exc}") from exc
        return BolorgirGrammar(
            backend=self,
            constraint=constraint,
            request_type=request_type,
            grammar_spec=grammar_spec,
            terminals=terminals,
        )

    def _compile(self, request_type: StructuredOutputOptions, grammar_spec: str):
        opts = StructuredOutputOptions
        if request_type == opts.JSON:
            schema = grammar_spec
            if not isinstance(schema, (str, dict)):
                raise BolorgirRequestError("JSON request spec must be a schema document")
            return self._engine.compile(schema, profile=SPEC_PROFILE)
        if request_type == opts.JSON_OBJECT:
            return self._engine.compile({"type": "object"}, profile=SPEC_PROFILE)
        if request_type == opts.CHOICE:
            try:
                choices = json.loads(grammar_spec)
            except (TypeError, ValueError) as exc:
                raise BolorgirRequestError(f"CHOICE spec is not JSON: {exc}") from exc
            if not isinstance(choices, list) or not choices or not all(
                    isinstance(c, str) for c in choices):
                raise BolorgirRequestError(
                    "CHOICE spec must be a non-empty JSON array of strings")
            return self._engine.compile_literals(choices)
        if request_type == opts.REGEX:
            if not isinstance(grammar_spec, str) or not grammar_spec:
                raise BolorgirRequestError("REGEX spec must be a non-empty pattern")
            return self._engine.compile_regex(grammar_spec)
        if request_type == opts.GRAMMAR:
            raise BolorgirRequestError(
                "the bolorgir backend does not support GRAMMAR (EBNF/Lark) "
                "requests; use JSON, choice, json_object or regex")
        if request_type == opts.STRUCTURAL_TAG:
            raise BolorgirRequestError(
                "the bolorgir backend does not support STRUCTURAL_TAG "
                "(tool-call framing) requests yet; the structural-tag "
                "constraint kind is not implemented (ADR-0016 finding 3). "
                "This is an explicit refusal, not a fallback: set an "
                "explicit tool strictness that emits JSON arguments, or "
                "use another backend for this request")
        raise BolorgirRequestError(f"unsupported request type {request_type!r}")

    def allocate_token_bitmask(self, max_num_seqs: int):
        import torch  # local import: torch is not a base dependency

        return torch.full(
            (int(max_num_seqs), self._model_words),
            -1,
            dtype=torch.int32,
        )

    def destroy(self) -> None:
        engine, self._engine = self._engine, None
        if engine is not None:
            try:
                engine.close()
            except bolorgir.BusyError:
                # Live requests still hold sessions; their release (garbage
                # collection of the request objects) returns the memory.
                # Shutdown must not fail on a draining engine.
                logger.warning(
                    "bolorgir backend stopped with live sessions; their "
                    "memory returns when the requests are collected")
        logger.info("bolorgir structured-output backend stopped")

    # -- helpers used by the grammar --------------------------------------

    @property
    def model_vocab_size(self) -> int:
        return self._model_vocab_size

    @property
    def eos_ids(self) -> tuple:
        return self._fingerprint.eos_ids

    @property
    def rollback_depth(self) -> int:
        return self._rollback_depth


class BolorgirGrammar(StructuredOutputGrammar):
    """Request-level grammar: one Bolorgir session per constrained request."""

    def __init__(
        self,
        *,
        backend: BolorgirBackend,
        constraint: bolorgir.Constraint,
        request_type: StructuredOutputOptions,
        grammar_spec: str,
        terminals: Optional[FrozenSet[int]],
    ) -> None:
        import hashlib

        self._backend = backend
        self._constraint = constraint
        self.request_type = request_type
        self.grammar_spec = grammar_spec
        self.spec_sha256 = hashlib.sha256(
            _spec_text(grammar_spec).encode("utf-8")).hexdigest()
        self._terminals = terminals
        self._default_eos = frozenset(backend.eos_ids)
        # The effective terminal set: the request's stop ids when given,
        # else the tokenizer EOS ids.
        self._effective_terminals = (
            terminals if terminals is not None else self._default_eos)
        self._vocab_size = backend.model_vocab_size
        self._depth = backend.rollback_depth
        self._session = constraint.create_session(max_rollback_tokens=self._depth)
        self._terminated = False
        self._failed = False
        self._session_tokens = 0
        # Statistics for diagnostics and tests.
        self.mask_fills = 0
        self.accepted_tokens = 0
        self.rolled_back_tokens = 0
        self.validations = 0

    # -- StructuredOutputGrammar interface --------------------------------

    def accept_tokens(self, request_id: str, tokens: list) -> bool:
        if self._failed:
            return False
        if self._terminated:
            # Tokens after a terminal token are ignored, like the
            # reference backends; the request is already stopping.
            return True
        for tok in tokens:
            tok = int(tok)
            if tok < 0 or tok >= self._vocab_size:
                return False
            if tok in self._effective_terminals:
                if self._can_end():
                    self._terminated = True
                    return True
                # The mask only offers a terminal token when the language
                # can complete; reaching this point means the caller
                # ignored the mask.
                logger.error(
                    "bolorgir: terminal token %d for request %s at a state "
                    "that cannot end (spec %s)", tok, request_id, self.spec_sha256[:16])
                return False
            try:
                self._session.accept_token(tok)
            except bolorgir.InvalidTokenError:
                return False
            except bolorgir.ZigConstraintsError as exc:
                self._fail(f"accept_token({tok}) failed: {exc}")
                return False
            self._session_tokens += 1
            self.accepted_tokens += 1
        return True

    def validate_tokens(self, tokens: list) -> list:
        """Longest prefix consecutive accepts would take, without changing
        the session. Never raises: a failing probe fails the grammar so
        that the next accept terminates exactly this request."""
        if self._terminated or self._failed:
            return []
        tokens = [int(t) for t in tokens]
        if not tokens:
            return []
        self.validations += 1
        try:
            n = self._session.validate_tokens(tokens)
        except bolorgir.ZigConstraintsError as exc:
            self._fail(f"validate_tokens failed: {exc}")
            return []
        if n <= 0:
            return []
        accepted = tokens[:n]
        for i, tok in enumerate(accepted):
            if tok in self._effective_terminals:
                if self._can_end_at(i, accepted):
                    return accepted[: i + 1]
            elif tok in self._default_eos:
                # The kernel accepted an EOS that an exclusive override set
                # does not make terminal; the reference backends would not
                # accept it either.
                return accepted[:i]
        return accepted

    def rollback(self, num_tokens: int) -> None:
        if num_tokens < 0:
            raise ValueError("num_tokens must be >= 0")
        to_undo = int(num_tokens)
        if self._terminated:
            if to_undo == 0:
                return
            self._terminated = False
            to_undo -= 1
        if to_undo > self._session_tokens:
            raise RuntimeError(
                f"rollback({num_tokens}) exceeds the accepted tokens "
                f"({self._session_tokens}); the session depth is "
                f"{self._depth}")
        if to_undo:
            self._session.rollback(to_undo)
            self._session_tokens -= to_undo
            self.rolled_back_tokens += to_undo

    def fill_bitmask(self, bitmask, batch_index: int) -> None:
        """Fill one int32 row of the shared tensor (bit t%32 of word t/32;
        1 = allowed)."""
        import numpy as np

        row = bitmask[batch_index].numpy()
        if self._failed:
            # The request is failing on the next accept; keep the row
            # unrestricted so the failing step cannot corrupt the batch.
            row.fill(-1)
            return
        if self._terminated:
            row.fill(-1)
            return
        words = row.shape[0]
        ctx_words = self._session.mask_words
        if words > ctx_words:
            # Model-vocabulary padding beyond the tokenizer table is never
            # producible under the constraint.
            row[ctx_words:] = 0
        try:
            self._session.fill_mask_into(row[:ctx_words])
            can_end = self._session.can_end()
        except bolorgir.ZigConstraintsError as exc:
            self._fail(f"fill_mask failed: {exc}")
            row.fill(-1)
            return
        self.mask_fills += 1
        urow = row.view(np.uint32)

        def set_bit(t: int) -> None:
            urow[t >> 5] |= np.uint32(1 << (t & 31))

        def clear_bit(t: int) -> None:
            urow[t >> 5] &= np.uint32(0xFFFFFFFF ^ (1 << (t & 31)))

        if self._terminals is None:
            # No override set: the kernel mask already carries the
            # tokenizer EOS bits exactly at completing states.
            return
        if can_end:
            # The mask offers exactly the request's terminal set.
            for t in self._default_eos:
                clear_bit(t)
            for t in self._terminals:
                set_bit(t)
        else:
            for t in self._terminals:
                clear_bit(t)

    def is_terminated(self) -> bool:
        return self._terminated or self._failed

    def reset(self) -> None:
        self._session.close()
        self._session = self._constraint.create_session(
            max_rollback_tokens=self._depth)
        self._terminated = False
        self._failed = False
        self._session_tokens = 0

    def close(self) -> None:
        """Release the native session (called by tests and on destroy)."""
        if self._session is not None:
            self._session.close()
            self._session = None  # type: ignore[assignment]

    # -- internals ---------------------------------------------------------

    def _is_default_eos(self, tok: int) -> bool:
        return tok in self._backend.eos_ids

    def _can_end(self) -> bool:
        try:
            return self._session.can_end()
        except bolorgir.ZigConstraintsError:
            return False

    def _can_end_at(self, i: int, accepted: list) -> bool:
        """Whether the language can complete after the first i tokens.

        Probed through the state-preserving validator: appending the
        tokenizer EOS to the prefix is accepted exactly at completing
        states. Without a tokenizer EOS the completion state is unknown
        and a terminal token is taken at face value.
        """
        probe = None
        for e in self._backend.eos_ids:
            probe = e
            break
        if probe is None:
            return True
        try:
            return self._session.validate_tokens(list(accepted[:i]) + [probe]) == i + 1
        except bolorgir.ZigConstraintsError:
            return False

    def _fail(self, reason: str) -> None:
        if not self._failed:
            logger.error(
                "bolorgir grammar failed (request type %s, spec sha256 %s): %s",
                self.request_type.name, self.spec_sha256[:16], reason)
        self._failed = True

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (f"BolorgirGrammar({self.request_type.name}, "
                f"spec={self.spec_sha256[:16]}, "
                f"terminated={self._terminated}, failed={self._failed})")


def validate_bolorgir_request(params: Any) -> None:
    """Fail-fast request validation for the patched vLLM frontend.

    ``params`` is the request's ``StructuredOutputsParams`` (or a
    ``SamplingParams`` carrying one). Only structural refusals are checked
    here; the engine-side compile produces the precise diagnostics. Raises
    ``VLLMValidationError`` so the API returns a 400 for the request.
    """
    from vllm.exceptions import VLLMValidationError

    so = getattr(params, "structured_outputs", params)
    if so is None:
        return

    def refuse(message: str, parameter: str = "structured_outputs") -> None:
        raise VLLMValidationError(message, parameter=parameter)

    if getattr(so, "grammar", None) is not None:
        refuse("The bolorgir backend does not support EBNF/Lark grammar "
               "requests; use json, choice, json_object or regex.")
    if getattr(so, "structural_tag", None) is not None:
        refuse("The bolorgir backend does not support structural_tag "
               "(tool-call framing) requests yet; it refuses the request "
               "instead of falling back to another backend.")
    if getattr(so, "whitespace_pattern", None) is not None:
        refuse("The bolorgir backend generates compact JSON only and does "
               "not support whitespace_pattern.")
    if getattr(so, "json", None) is not None:
        spec = so.json
        if isinstance(spec, str) and not spec.strip():
            refuse("structured_outputs.json cannot be an empty string.")
        if not isinstance(spec, (str, dict)):
            refuse("structured_outputs.json must be a schema document.")
    if getattr(so, "choice", None) is not None:
        if not isinstance(so.choice, list) or not so.choice:
            refuse("structured_outputs.choice must be a non-empty list.")
        if not all(isinstance(c, str) for c in so.choice):
            refuse("structured_outputs.choice must contain only strings.")
    if getattr(so, "regex", None) is not None:
        if not isinstance(so.regex, str) or not so.regex:
            refuse("structured_outputs.regex must be a non-empty pattern.")
        if "\x00" in so.regex:
            refuse("structured_outputs.regex must not contain a NUL character.")
