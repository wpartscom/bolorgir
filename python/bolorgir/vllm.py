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
  compact serialization), ``JSON_OBJECT``, ``CHOICE`` (literal set),
  ``REGEX`` (ADR-0015) and ``STRUCTURAL_TAG`` for the pinned agent path
  (tool-call framing over the accepted spec subset, ADR-0016). ``GRAMMAR``
  is refused explicitly, as is any structural-tag construct outside the
  subset; there is no fallback to another backend.
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
  ignored, like the reference backends. Draft validation obeys the same
  terminal rule: a terminal is accepted exactly at a completing state
  and cuts the prefix otherwise.
- rollback and draft validation map to the session history
  (``blg_session_rollback``) and the state-preserving
  ``blg_validate_tokens``; the session depth is the speculative token
  count plus one. A refused rollback leaves every observable flag as it
  was.
- traces: with ``BLG_AGENT_TRACE=<path>`` the backend appends
  ``blg-agent-trace/2`` JSON lines (start, decision, compile, mask,
  accept, rollback, finish) for every constrained request. The trace is
  keyed by the request id the client observes (the response id): the
  patched vLLM manager passes the external id together with the
  engine-internal one, which every event records explicitly when they
  differ. Masks and accepts name the active segment: the tool tag that
  owns the current bytes of a structural tag, or the ``structural_tag``
  constraint for free text and a begin prefix shared by several
  variants. A trace is written only for requests the backend actually
  compiled, and ``finish`` only when the constrained generation is
  confirmed over through the backend (a terminal token that no rollback
  undoes - vLLM rolls back draft-accepted terminals within the same
  step - or a failure), so a truncated trace stays visibly incomplete.
- failures: a failing mask fill or a rejected accepted token marks the
  grammar failed. ``accept_tokens`` then returns False, which makes vLLM
  terminate exactly that request with an error; no unconstrained
  generation is produced and other requests of the batch continue.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import weakref
from dataclasses import dataclass, field
from typing import Any, FrozenSet, List, Optional, Set, Tuple

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

#: Backend trace (benchmarks/agent_eval/PLAN.md, "Evidence format").
TRACE_FORMAT = "blg-agent-trace/2"
TRACE_BACKEND = "bolorgir"
TRACE_ENV = "BLG_AGENT_TRACE"


class BolorgirBackendError(RuntimeError):
    """Backend setup failure (tokenizer, vocabulary, configuration)."""


class BolorgirRequestError(ValueError):
    """A request the bolorgir backend does not support.

    Raised while compiling; vLLM carries it to the scheduler as the
    failing request's error, never falls back to another backend.
    """


def _canonical_sha256(value: Any) -> str:
    """sha256 of the canonical JSON text (sorted keys, no spaces, UTF-8).

    The qualification collector hashes client schemas the same way; the
    trace ``schema_sha256`` of a constraint is this value of the schema
    the backend actually compiled.
    """
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _spec_json(spec: Any) -> Any:
    return json.loads(spec) if isinstance(spec, (str, bytes, bytearray)) else spec


class _TraceWriter:
    """One append-only JSONL file for all requests of this process."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        # Traces whose request accepted a terminal token that no rollback
        # has undone yet: the finish is deferred until the request is
        # confirmed over (another engine step, release, shutdown), because
        # vLLM temporarily accepts speculative draft tokens during bitmask
        # fill and rolls them back in the same step.
        self._pending: "weakref.WeakSet[_RequestTrace]" = weakref.WeakSet()

    def write(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False) + "\n"
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)

    def pending_add(self, trace: "_RequestTrace") -> None:
        with self._lock:
            self._pending.add(trace)

    def pending_discard(self, trace: "_RequestTrace") -> None:
        with self._lock:
            self._pending.discard(trace)

    def flush_pending(self, exclude: Optional["_RequestTrace"] = None) -> None:
        """Confirms every deferred terminal except ``exclude`` (the
        request whose own operation is in flight)."""
        with self._lock:
            items = [t for t in self._pending if t is not exclude]
        for t in items:
            t.confirm_terminal()


class _RequestTrace:
    """The blg-agent-trace/2 events of one request, in write order.

    ``seq`` is assigned under the lock, so the file order is the event
    order; nothing is written after ``finish``. ``request_id`` is the
    correlation key the client observes (the response id / the id the
    patch passes as ``external_request_id``); ``internal_request_id``
    records the engine-internal id explicitly when the two differ.
    """

    def __init__(self, writer: _TraceWriter, request_id: str, backend_version: str,
                 internal_id: Optional[str] = None) -> None:
        self._writer = writer
        self.request_id = request_id
        self._internal_id = internal_id
        self._backend_version = backend_version
        self._lock = threading.Lock()
        self._seq = 0
        self._finished = False
        self._pending_terminal = False

    def _event(self, name: str, **fields: Any) -> None:
        with self._lock:
            if self._finished:
                return
            event = {"event": name, "request_id": self.request_id,
                     "seq": self._seq}
            if self._internal_id is not None:
                event["internal_request_id"] = self._internal_id
            event.update(fields)
            self._writer.write(event)
            self._seq += 1

    def start(self) -> None:
        self._event("start", format=TRACE_FORMAT, backend=TRACE_BACKEND,
                    backend_version=self._backend_version)

    def decision(self, constraints: List[dict]) -> None:
        self._event("decision", applied=True, constraints=constraints,
                    reason="structured output request compiled by the bolorgir backend")

    def compile_ok(self, constraint_id: str, schema_sha256: str) -> None:
        # The adapter compiles per request; the kernel artifact cache is
        # transparent here, so every constraint of a request is a miss.
        self._event("compile", backend=TRACE_BACKEND, constraint_id=constraint_id,
                    schema_sha256=schema_sha256, status="ok", cache="miss",
                    compiled_from=None)

    def mask(self, constraint_id: str, segment_id: str, step: int,
             allowed: int, mask_sha256: str) -> None:
        self._event("mask", backend=TRACE_BACKEND, constraint_id=constraint_id,
                    segment_id=segment_id, step=step, allowed=allowed,
                    mask_sha256=mask_sha256)

    def accept(self, constraint_id: str, step: int, token: int, terminal: bool) -> None:
        self._event("accept", constraint_id=constraint_id, step=step,
                    token=token, terminal=terminal)

    def rollback(self, num_tokens: int, terminal_undone: bool) -> None:
        """Records an engine rollback; a rolled-back terminal re-opens the
        trace, so later events are kept."""
        if terminal_undone:
            self.clear_terminal()
        self._event("rollback", tokens=num_tokens, terminal_undone=terminal_undone)

    def note_terminal(self) -> None:
        """A terminal token was accepted: defer the finish until the
        request is confirmed over (vLLM may roll the token back)."""
        with self._lock:
            if self._finished or self._pending_terminal:
                return
            self._pending_terminal = True
        self._writer.pending_add(self)

    def clear_terminal(self) -> None:
        """The terminal was undone (or generation continued): no finish."""
        with self._lock:
            self._pending_terminal = False
        self._writer.pending_discard(self)

    def confirm_terminal(self) -> None:
        """Writes ``finish ok`` when a terminal is still pending: the
        request is over. A no-op otherwise (no terminal ever accepted, a
        rollback undid it, or the trace already finished)."""
        with self._lock:
            if self._finished or not self._pending_terminal:
                return
        self.finish("ok")

    def finish(self, status: str) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._pending_terminal = False
            event = {"event": "finish", "request_id": self.request_id,
                     "seq": self._seq, "status": status, "events": self._seq}
            if self._internal_id is not None:
                event["internal_request_id"] = self._internal_id
            self._writer.write(event)
            self._seq += 1
        self._writer.pending_discard(self)


_trace_writer: Optional[_TraceWriter] = None
_trace_writer_checked = False


def _trace_writer_for_process() -> Optional[_TraceWriter]:
    """The process trace writer from ``BLG_AGENT_TRACE`` (checked once)."""
    global _trace_writer, _trace_writer_checked
    if not _trace_writer_checked:
        _trace_writer_checked = True
        path = os.environ.get(TRACE_ENV, "")
        if path:
            _trace_writer = _TraceWriter(path)
            logger.info("bolorgir agent trace enabled: %s", path)
    return _trace_writer


_TOOL_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]*)"')


def _structural_tags_of(spec: Any) -> List[dict]:
    """The tag items of an accepted structural-tag spec, best effort (the
    Zig compiler validates the spec; this only derives trace records)."""
    if not isinstance(spec, dict):
        return []
    if isinstance(spec.get("structures"), list):
        return [t for t in spec["structures"] if isinstance(t, dict)]
    fmt = spec.get("format")
    if isinstance(fmt, dict) and isinstance(fmt.get("tags"), list):
        return [t for t in fmt["tags"] if isinstance(t, dict)]
    return []


def _tag_content_schema(tag: dict) -> Any:
    content = tag.get("content")
    if not isinstance(content, dict):
        return None
    if "json_schema" in content:
        return content["json_schema"]
    return content.get("schema")


def _structural_trace_plan(spec: Any) -> Tuple[List[dict], List[Optional[str]], str]:
    """Trace plan of an accepted structural-tag spec.

    Returns ``(constraints, variant_ids, text_id)``: the declared
    constraints, one constraint id per tag variant index (``None`` falls
    back to the free-text constraint), and the id of the free-text /
    whole-language constraint.

    Constraints: one per named tag whose content is a schema document
    (``kind: "tool"``, the tag's content hash - the client's parameters
    schema for strict tools) plus one ``kind: "structural_tag"`` for the
    free text and any begin prefix shared by several variants
    (``constraint_id == segment_id == "structural_tag"``). The free-text
    constraint's hash is the canonical hash of the sorted unique tag-name
    set: both the adapter (from the compiled spec) and the qualification
    collector (from the request's tools) can compute it, so free-text and
    ambiguous-framing masks stay verifiable against the client request.
    """
    tags = _structural_tags_of(spec)
    out: List[dict] = []
    seen: dict = {}
    variant_ids: List[Optional[str]] = []
    names: List[str] = []
    for i, tag in enumerate(tags):
        m = _TOOL_NAME_RE.search(str(tag.get("begin", "")))
        label = m.group(1) if m else None
        cid: Optional[str] = None
        if label is not None:
            names.append(label)
            schema = _tag_content_schema(tag)
            if isinstance(schema, dict):
                digest = _canonical_sha256(schema)
                key = (label, digest)
                if key in seen:
                    cid = seen[key]
                else:
                    cid = label
                    if any(c["constraint_id"] == cid for c in out):
                        cid = f"{label}#{i}"
                    seen[key] = cid
                    out.append({"constraint_id": cid, "kind": "tool",
                                "segment_id": cid, "schema_sha256": digest})
        variant_ids.append(cid)
    text_id = "structural_tag"
    if names:
        text_hash = _canonical_sha256(sorted(set(names)))
    else:
        text_hash = _canonical_sha256(spec)
    if not out:
        # Nothing derivable (any_text, nameless tags): the whole-language
        # constraint is the only declared one.
        return ([{"constraint_id": text_id, "kind": "structural_tag",
                  "segment_id": text_id, "schema_sha256": text_hash}],
                variant_ids, text_id)
    out.append({"constraint_id": text_id, "kind": "structural_tag",
                "segment_id": text_id, "schema_sha256": text_hash})
    return out, variant_ids, text_id


def _trace_constraints(request_type: Any, grammar_spec: Any) -> List[dict]:
    """The constraints the backend declares for a request.

    ``schema_sha256`` is the canonical hash of the schema the backend
    compiled: the client's ``tool:<name>`` parameters schema for tool
    segments, the response schema for ``JSON`` requests, and the literal
    list / pattern for choice and regex requests (no client schema).
    """
    opts = StructuredOutputOptions
    if request_type == opts.JSON:
        schema = _spec_json(grammar_spec)
        return [{"constraint_id": "response_format", "kind": "response_format",
                 "segment_id": "response_format",
                 "schema_sha256": _canonical_sha256(schema)}]
    if request_type == opts.JSON_OBJECT:
        return [{"constraint_id": "response_format", "kind": "response_format",
                 "segment_id": "response_format",
                 "schema_sha256": _canonical_sha256({"type": "object"})}]
    if request_type == opts.CHOICE:
        return [{"constraint_id": "response_format", "kind": "response_format",
                 "segment_id": "response_format",
                 "schema_sha256": _canonical_sha256(_spec_json(grammar_spec))}]
    if request_type == opts.REGEX:
        return [{"constraint_id": "response_format", "kind": "response_format",
                 "segment_id": "response_format",
                 "schema_sha256": _canonical_sha256(str(grammar_spec))}]
    if request_type == opts.STRUCTURAL_TAG:
        return _structural_trace_plan(_spec_json(grammar_spec))[0]
    return []


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

    #: The patched vLLM manager passes the request id to ``compile_grammar``
    #: when the backend announces this attribute (trace correlation).
    accepts_request_id = True

    def compile_grammar(
        self,
        request_type: StructuredOutputOptions,
        grammar_spec: str,
        stop_token_ids: Optional[Any] = None,
        request_id: Optional[str] = None,
        external_request_id: Optional[str] = None,
    ) -> "BolorgirGrammar":
        """Compiles one request. ``request_id`` is the engine-internal id
        (vLLM appends a random suffix to the id it receives);
        ``external_request_id`` is the id the server exposes to the
        client (the response id). The backend trace is keyed by the
        external id when it is passed and records the internal one
        explicitly, so the qualification collector finds the events by an
        id it observes."""
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
        trace = None
        if request_id or external_request_id:
            writer = _trace_writer_for_process()
            if writer is not None:
                external = str(external_request_id) if external_request_id else None
                internal = str(request_id) if request_id else None
                key = external or internal
                record = internal if external and internal and internal != external else None
                trace = _RequestTrace(writer, key, bolorgir.__version__,
                                      internal_id=record)
        return BolorgirGrammar(
            backend=self,
            constraint=constraint,
            request_type=request_type,
            grammar_spec=grammar_spec,
            terminals=terminals,
            trace=trace,
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
                "requests; use JSON, choice, json_object, regex or the "
                "structural-tag subset")
        if request_type == opts.STRUCTURAL_TAG:
            if not isinstance(grammar_spec, str) or not grammar_spec.strip():
                raise BolorgirRequestError(
                    "STRUCTURAL_TAG spec must be a non-empty JSON document")
            return self._engine.compile_structural_tag(grammar_spec)
        raise BolorgirRequestError(f"unsupported request type {request_type!r}")

    def allocate_token_bitmask(self, max_num_seqs: int):
        import torch  # local import: torch is not a base dependency

        return torch.full(
            (int(max_num_seqs), self._model_words),
            -1,
            dtype=torch.int32,
        )

    def destroy(self) -> None:
        writer = _trace_writer_for_process()
        if writer is not None:
            # Shutdown: a deferred terminal of a request that will not run
            # another engine step is final; a generation cut elsewhere
            # leaves no pending terminal and stays visibly incomplete.
            writer.flush_pending()
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
        trace: Optional[_RequestTrace] = None,
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
        # Backend trace (blg-agent-trace/2): start, decision and one
        # compile event per declared constraint, all before any mask.
        self._trace: Optional[_RequestTrace] = None
        self._trace_primary: Optional[Tuple[str, str]] = None
        # Structural tags: constraint id per tag variant index (None ->
        # the free-text constraint) and the free-text constraint id.
        self._trace_variant_ids: Optional[List[Optional[str]]] = None
        self._trace_text_id: Optional[str] = None
        if trace is not None:
            try:
                if request_type == StructuredOutputOptions.STRUCTURAL_TAG:
                    constraints, variant_ids, text_id = _structural_trace_plan(
                        _spec_json(grammar_spec))
                else:
                    constraints = _trace_constraints(request_type, grammar_spec)
                    variant_ids, text_id = None, constraints[0]["constraint_id"]
            except Exception:  # noqa: BLE001 - tracing must never fail a request
                logger.exception(
                    "bolorgir: cannot derive the trace constraints; "
                    "tracing is off for this request")
            else:
                self._trace = trace
                self._trace_variant_ids = variant_ids
                self._trace_text_id = text_id
                self._trace_primary = (constraints[0]["constraint_id"],
                                       constraints[0]["segment_id"])
                trace.start()
                trace.decision(constraints)
                for c in constraints:
                    trace.compile_ok(c["constraint_id"], c["schema_sha256"])

    # -- StructuredOutputGrammar interface --------------------------------

    def accept_tokens(self, request_id: str, tokens: list) -> bool:
        self._flush_foreign_traces()
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
            cid, _seg = self._trace_segment()
            if tok in self._effective_terminals:
                if self._can_end():
                    self._terminated = True
                    if self._trace is not None:
                        self._trace.accept(cid, self.accepted_tokens + 1, tok, True)
                        # The finish is deferred: vLLM also accepts draft
                        # tokens (a terminal among them) while filling
                        # masks and rolls them back in the same step.
                        self._trace.note_terminal()
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
            if self._trace is not None:
                self._trace.accept(cid, self.accepted_tokens, tok, False)
        return True

    def validate_tokens(self, tokens: list) -> list:
        """Longest prefix consecutive accepts would take, without changing
        the session. Terminal tokens (the request's stop ids, or the
        tokenizer EOS ids when vLLM passes none) obey the same rule as
        ``accept_tokens``: a terminal is accepted exactly at a state
        where the language can end, ends the prefix there, and cuts the
        prefix at a state that cannot end. Never raises: a failing probe
        fails the grammar so that the next accept terminates exactly this
        request."""
        self._flush_foreign_traces()
        if self._terminated or self._failed:
            return []
        tokens = [int(t) for t in tokens]
        if not tokens:
            return []
        self.validations += 1
        # The kernel validator has no notion of the adapter-level terminal
        # set: cut the draft at the first token whose semantics differ,
        # validate the byte prefix through the kernel, then apply the
        # terminal rule at that position.
        cut = len(tokens)
        for i, tok in enumerate(tokens):
            if tok in self._effective_terminals or (
                    tok in self._default_eos
                    and tok not in self._effective_terminals):
                cut = i
                break
        head = tokens[:cut]
        if head:
            try:
                n = self._session.validate_tokens(head)
            except bolorgir.ZigConstraintsError as exc:
                self._fail(f"validate_tokens failed: {exc}")
                return []
            if n < len(head):
                return tokens[:n]
        if cut == len(tokens):
            return head
        tok = tokens[cut]
        if tok in self._effective_terminals:
            if self._can_end_after(head):
                return head + [tok]
            return head
        # A default EOS that an exclusive override set does not make
        # terminal: the mask clears it and the reference backends would
        # not accept it either.
        return head

    def rollback(self, num_tokens: int) -> None:
        """Undo the last ``num_tokens`` sampled tokens (a terminal counts
        as one; undoing it re-opens generation and the trace). An invalid
        depth raises before any observable state changes."""
        self._flush_foreign_traces()
        if num_tokens < 0:
            raise ValueError("num_tokens must be >= 0")
        was_terminated = self._terminated
        to_undo = int(num_tokens)
        if was_terminated:
            if to_undo == 0:
                return
            to_undo -= 1
        if to_undo > self._session_tokens:
            raise RuntimeError(
                f"rollback({num_tokens}) exceeds the accepted tokens "
                f"({self._session_tokens}); the session depth is "
                f"{self._depth}")
        if to_undo:
            # The kernel rollback either applies fully or raises with the
            # session unchanged; the adapter keeps the same order so a
            # refused rollback leaves every flag as it was.
            self._session.rollback(to_undo)
            self._session_tokens -= to_undo
            self.rolled_back_tokens += to_undo
        if was_terminated:
            self._terminated = False
        if self._trace is not None and (to_undo or was_terminated):
            self._trace.rollback(int(num_tokens), was_terminated)

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
            self._trace_mask(urow)
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
        self._trace_mask(urow)

    def _trace_mask(self, urow) -> None:
        """Records the applied mask row (blg-agent-trace/2) with the
        active segment of the current state."""
        if self._trace is None:
            return
        cid, seg = self._trace_segment()
        allowed = sum(int(w).bit_count() for w in urow)
        digest = hashlib.sha256(
            urow.astype("<i4", copy=False).tobytes()).hexdigest()
        self._trace.mask(cid, seg, self.mask_fills, allowed, digest)

    def is_terminated(self) -> bool:
        return self._terminated or self._failed

    def reset(self) -> None:
        self._flush_foreign_traces()
        self._session.close()
        self._session = self._constraint.create_session(
            max_rollback_tokens=self._depth)
        self._terminated = False
        self._failed = False
        self._session_tokens = 0

    def close(self) -> None:
        """Release the native session (called by tests and on destroy): a
        deferred terminal is confirmed first - the request is over."""
        self._finish_pending()
        if self._session is not None:
            self._session.close()
            self._session = None  # type: ignore[assignment]

    def __del__(self) -> None:  # pragma: no cover - GC path
        try:
            self._finish_pending()
        except Exception:  # noqa: BLE001 - never raise from __del__
            pass

    # -- internals ---------------------------------------------------------

    def _is_default_eos(self, tok: int) -> bool:
        return tok in self._backend.eos_ids

    def _trace_segment(self) -> Tuple[Optional[str], Optional[str]]:
        """``(constraint_id, segment_id)`` of the active segment: for
        structural-tag requests the tag variant that owns the current
        bytes; free text and a begin prefix shared by several variants
        (no single tag chosen yet) belong to the ``structural_tag``
        constraint. Other requests have a single constraint."""
        if self._trace is None:
            return (None, None)
        if self._trace_variant_ids is None:
            return self._trace_primary
        try:
            phase, variant = self._session.struct_segment()
        except Exception:  # noqa: BLE001 - diagnostics only
            return self._trace_primary
        if phase != 0 and variant != 0xFF and variant < len(self._trace_variant_ids):
            cid = self._trace_variant_ids[variant]
            if cid:
                return (cid, cid)
        return (self._trace_text_id, self._trace_text_id)

    def _flush_foreign_traces(self) -> None:
        """An engine step on this request confirms every other request's
        deferred terminal: vLLM accepts draft tokens (a terminal among
        them) and rolls them back within one bitmask-fill step, so a
        terminal that survived another request's step is final."""
        writer = (self._trace._writer if self._trace is not None
                  else _trace_writer_for_process())
        if writer is not None:
            writer.flush_pending(exclude=self._trace)

    def _finish_pending(self) -> None:
        if self._trace is not None:
            self._trace.confirm_terminal()

    def _can_end(self) -> bool:
        try:
            return self._session.can_end()
        except bolorgir.ZigConstraintsError:
            return False

    def _can_end_after(self, prefix: list) -> bool:
        """Whether the language can complete after the kernel-validated
        draft prefix, without consuming the prefix. Probed through the
        state-preserving validator: appending a tokenizer EOS is accepted
        exactly at completing states. Without a tokenizer EOS the
        completion state cannot be probed and a terminal is taken at face
        value."""
        probe = None
        for e in self._backend.eos_ids:
            probe = e
            break
        if probe is None:
            return True
        if not prefix:
            return self._can_end()
        try:
            return self._session.validate_tokens(
                list(prefix) + [probe]) == len(prefix) + 1
        except bolorgir.ZigConstraintsError:
            return False

    def _fail(self, reason: str) -> None:
        if not self._failed:
            logger.error(
                "bolorgir grammar failed (request type %s, spec sha256 %s): %s",
                self.request_type.name, self.spec_sha256[:16], reason)
        self._failed = True
        if self._trace is not None:
            self._trace.finish("error")

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
    st = getattr(so, "structural_tag", None)
    if st is not None:
        if not isinstance(st, str) or not st.strip():
            refuse("structured_outputs.structural_tag must be a non-empty "
                   "JSON document.")
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
