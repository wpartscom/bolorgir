"""
Hugging Face Transformers integration (extra [transformers]).

Supported path (FR-14): decoder-only models, single process/device,
greedy and sampling (num_beams=1, num_return_sequences=1), fixed batch,
left padding. Constraints are applied BEFORE top-k/top-p: in HF,
LogitsProcessor runs before LogitsWarper (the order is fixed in the
supported transformers versions and covered by tests).

Tested with: torch 2.14, transformers 5.17 (and transformers 4.x).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

try:
    import torch
    from transformers import GenerationConfig, LogitsProcessor
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "bolorgir.transformers requires torch and transformers: "
        "pip install Bolorgir[transformers]"
    ) from e

from . import (
    Constraint,
    InvalidArgumentError,
    Session,
    UnsupportedModeError,
    ZigConstraintsError,
)
from . import _core as _zg_core

__all__ = [
    "ConstraintLogitsProcessor",
    "ConstrainedGenerateResult",
    "MaskGpuUnpacker",
    "constrained_generate",
]


class MaskGpuUnpacker:
    """Expands core bitmask words (uint32 LE, bit 1 = allowed) on a device.

    Only the compact words (vocab_size/32 int32 per row) cross the bus;
    bits are unpacked on the device. The pinned staging buffer, the device
    word buffer and the bit-mask vector are cached per (rows, words,
    device). This is the production path of ConstraintLogitsProcessor.
    """

    def __init__(self) -> None:
        self._bufs: Dict[tuple, tuple] = {}

    def _buffers(self, rows: int, nwords: int, device: Any) -> tuple:
        key = (rows, nwords, str(device))
        bufs = self._bufs.get(key)
        if bufs is None:
            pinned = torch.empty(
                rows * nwords,
                dtype=torch.int32,
                pin_memory=(device.type == "cuda"),
            )
            words_dev = torch.empty(rows * nwords, dtype=torch.int32, device=device)
            bit_masks = (1 << torch.arange(32, dtype=torch.int32, device=device)).to(
                torch.int32
            )
            # hits: mask words intersected with the bit masks (out=, no
            # per-step allocation); bits: the ne/eq result (bool).
            hits = torch.empty(rows, nwords, 32, dtype=torch.int32, device=device)
            bits = torch.empty(rows, nwords * 32, dtype=torch.bool, device=device)
            bufs = (pinned, words_dev, bit_masks, hits, bits)
            self._bufs[key] = bufs
        return bufs

    def _stage_words(self, masks, vocab_size: int, device: Any) -> tuple:
        """Validate and transfer mask words; returns (hits, bits, nwords)."""
        if isinstance(masks, (bytes, bytearray)):
            masks = [bytes(masks)]
        nwords = (vocab_size + 31) // 32
        want = nwords * 4
        for m in masks:
            if len(m) != want:
                raise InvalidArgumentError(
                    f"mask of {len(m)} bytes does not match vocab "
                    f"{vocab_size} (expected {want} bytes)"
                )
        pinned, words_dev, bit_masks, hits, bits = self._buffers(
            len(masks), nwords, device
        )
        host = torch.frombuffer(bytearray().join(masks), dtype=torch.int32)
        pinned.copy_(host)
        words_dev.copy_(pinned, non_blocking=True)
        torch.bitwise_and(
            words_dev.view(len(masks), nwords, 1), bit_masks, out=hits
        )
        return hits, bits, nwords

    def unpack(self, masks: Union[bytes, Sequence[bytes]], vocab_size: int, device: Any) -> Any:
        """Mask bytes -> bool tensor [rows, vocab_size] on device (True = allowed).

        The result views an internal buffer that is reused on the next
        unpack call with the same (rows, words, device): consume it before
        unpacking again.
        """
        hits, bits, nwords = self._stage_words(masks, vocab_size, device)
        torch.ne(hits.view(hits.shape[0], nwords * 32), 0, out=bits)
        return bits[:, :vocab_size]

    def unpack_forbid(
        self,
        masks: Union[bytes, Sequence[bytes]],
        vocab_size: int,
        device: Any,
        out: Any = None,
    ) -> Any:
        """Mask bytes -> bool tensor [rows, words*32] (True = FORBIDDEN).

        Fast path of ConstraintLogitsProcessor: inverted bits are written by
        a single `eq` straight into `out` ([rows, words*32]) when given -
        this removes logical_not_ and the whole mask-row copy on every step.
        Without `out` the result lives in the shared cached buffer (reused
        until the next call with the same (rows, words, device)). Returns
        the FULL padded buffer [rows, words*32] (not a vocab slice).
        """
        hits, bits, nwords = self._stage_words(masks, vocab_size, device)
        dest = bits if out is None else out
        torch.eq(hits.view(hits.shape[0], nwords * 32), 0, out=dest)
        return dest


@dataclass
class ConstrainedGenerateResult:
    """Result of constrained_generate.

    completed[i] is True only when row i accepted a permitted EOS and
    session.finish() succeeded (§3.3, FR-14). Rows stopped by the length
    limit without EOS report completed=False with stop_reason "length".
    """

    sequences: Any  # torch.Tensor [batch, total_len]
    completed: List[bool]
    stop_reason: List[str]  # "eos" | "length" | "dead_end" | "error"
    sessions: List[Optional[Session]] = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.sessions is None:
            self.sessions = [None] * len(self.completed)


class ConstraintLogitsProcessor(LogitsProcessor):
    """Applies the constraint mask to scores for each active batch row.

    HF calls processors BEFORE warpers (top-k/top-p/temperature), so
    candidate selection accounts for the allowed language; forbidden
    tokens get -inf and cannot be re-allowed by later operations.

    Accepted tokens are synced by input_ids growth: prompt_len is fixed
    at start, constraint tokens are input_ids[:, prompt_len:]; left
    padding is part of the prompt and never synced. The processor does
    not see the last sampled token - constrained_generate accepts it
    after generate.

    A row that accepted EOS is finalized (session.finish) and
    deactivated: HF pads it for the rest of the batch, and those pads
    never reach accept_token.

    If the model's logit width (lm_head size) exceeds the constraint
    vocab (padded vocab, e.g. Qwen2.5: 151936 > 151665), the mask covers
    the first vocab_size positions and the tail always gets -inf: those
    are undecodable dummy lm_head positions. Real EOS/pad ids lie inside
    vocab_size and are unaffected.

    Per-step cost (hot path, eighth run): new tokens are fetched from
    input_ids with ONE batched D2H per call (a per-row int(cuda_scalar)
    pattern costs ~0.9 ms per step at batch 32 vs ~0.07 ms batched); masks
    are filled by the core in one batched call; the unpacker writes the
    FORBIDDEN bits (True = -inf) directly - `eq` straight into the
    persistent buffer on the all-rows fast path (no logical_not_, no
    row-copy) or into its cached buffer before index_copy_ on the partial
    path (no per-step allocs); validity is one amax reduction + one D2H
    instead of a cascade of isnan/isposinf/isfinite checks with two syncs.
    """

    def __init__(
        self,
        sessions: Sequence[Optional[Session]],
        prompt_len: int,
        eos_ids: Sequence[int] = (),
        eos_ids_per_row: Optional[Sequence[Sequence[int]]] = None,
    ) -> None:
        if not sessions:
            raise InvalidArgumentError("sessions: empty batch")
        self.sessions = list(sessions)
        self.prompt_len = int(prompt_len)
        if eos_ids_per_row is not None:
            # Each row may live on its own engine: a row's stop
            # id must be confirmed by the kernel of THAT exact row.
            per_row = [frozenset(int(i) for i in ids) for ids in eos_ids_per_row]
            if len(per_row) != len(self.sessions):
                raise InvalidArgumentError(
                    "eos_ids_per_row: length does not match the number of rows"
                )
            self._row_eos: List[frozenset] = per_row
            self.eos_ids = frozenset().union(*per_row)
        else:
            shared = frozenset(int(i) for i in eos_ids)
            self._row_eos = [shared] * len(self.sessions)
            self.eos_ids = shared
        self.seen = [0] * len(self.sessions)  # accepted constraint tokens
        self.active = [s is not None for s in self.sessions]
        self.dead_end = [False] * len(self.sessions)
        self.eos_done = [False] * len(self.sessions)  # row finished by EOS
        self._vocab_size = next(
            (s.vocab_size for s in self.sessions if s is not None), None
        )
        if self._vocab_size is None:
            raise InvalidArgumentError("sessions: all rows are None")
        self._unpacker = MaskGpuUnpacker()
        self._forbid_buf: Optional[Any] = None
        self._forbid_rows: tuple = ()  # rows written in _forbid_buf last call
        # Batched CPU cache of constraint-token columns: one D2H per call
        # instead of one scalar sync per row (diagnostics diag_proc_step).
        self._pending: List[List[int]] = []
        self._pending_base = 0  # absolute index of _pending[..][0]
        self._fetched_upto = 0  # constraint tokens already fetched to CPU
        self._idx_cache: Dict[tuple, Any] = {}  # rows tuple -> device index tensor

    def _advance(self, i: int, upto: int) -> None:
        """Accept row i tokens [seen, upto) from the batched CPU cache.

        After EOS the row is finished and deactivated; its further tokens
        (HF padding of finished rows) are skipped, not accepted.
        """
        sess = self.sessions[i]
        assert sess is not None
        base = self._pending_base
        while self.seen[i] < upto:
            row_tokens = self._pending[i]
            k = self.seen[i] - base
            assert 0 <= k < len(row_tokens), "token cache out of sync"
            tok = row_tokens[k]
            self.seen[i] += 1
            if self.eos_done[i]:
                continue  # pad after a finished row
            sess.accept_token(tok)  # the core rejects EOS before document end
            if tok in self._row_eos[i]:
                # the mask permits EOS only at the document boundary
                sess.finish()
                self.eos_done[i] = True
                self.active[i] = False

    def _fill_masks(self, rows: List[int]) -> List[bytes]:
        """Masks of active rows in one core call (input order preserved).

        The batched entry point takes per-session locks without the GIL
        inside one C call. A failing row is replayed by a single
        fill_mask() so the raised exception stays the same typed error
        with the core's original message.
        """
        sessions = [self.sessions[i] for i in rows]
        raw = _zg_core.fill_masks_batch([s._s for s in sessions])
        masks: List[bytes] = []
        for pos, (code, mask) in enumerate(raw):
            if code != 0:
                sessions[pos].fill_mask()  # raises the original typed error
                raise ZigConstraintsError(
                    f"row {rows[pos]}: mask unavailable (status {code})"
                )
            masks.append(mask)
        return masks

    def _rows_index(self, rows: List[int], device: Any) -> Any:
        key = (tuple(rows), str(device))
        idx = self._idx_cache.get(key)
        if idx is None:
            idx = torch.tensor(rows, dtype=torch.long, device=device)
            self._idx_cache[key] = idx
        return idx

    def __call__(self, input_ids: Any, scores: Any) -> Any:
        width = scores.shape[-1]
        v = self._vocab_size
        if width < v:
            raise ZigConstraintsError(
                f"logits width {width} is smaller than constraint vocab {v}"
            )
        # padded lm_head: mask by the constraint vocab, forbid the tail
        target = scores if width == v else scores[:, :v]
        device = scores.device
        total = input_ids.shape[1] - self.prompt_len
        # New columns of input_ids are fetched by ONE batched .tolist() per
        # call: a per-row int(cuda scalar) sync costs ~0.9 ms/step at batch
        # 32, the batched copy ~0.07 ms (diag_proc_step, s1 b32).
        if total > self._fetched_upto:
            cols = input_ids[
                :, self.prompt_len + self._fetched_upto : self.prompt_len + total
            ]
            self._pending = cols.tolist()
            self._pending_base = self._fetched_upto
            self._fetched_upto = total
        rows: List[int] = []
        for i, sess in enumerate(self.sessions):
            if not self.active[i] or sess is None:
                continue
            self._advance(i, total)
            if self.active[i]:
                rows.append(i)
        if rows:
            # One compact H2D for the whole batch; bits unpack on the device
            # and land as FORBIDDEN flags directly in the persistent buffer
            # of the full-rows fast path. DeadEndError propagates explicitly
            # (FR-15).
            masks = self._fill_masks(rows)
            nwords = (v + 31) // 32
            forbid = self._forbid_buf
            if (
                forbid is None
                or forbid.shape[0] != target.shape[0]
                or forbid.shape[1] != nwords * 32
                or forbid.device != device
            ):
                forbid = torch.empty(
                    target.shape[0], nwords * 32, dtype=torch.bool, device=device
                )
                forbid.zero_()  # rows outside the active set must stay empty
                self._forbid_buf = forbid
                self._forbid_rows = ()
            rows_key = tuple(rows)
            full = len(rows) == forbid.shape[0]  # all rows active
            if full:
                # fast path until the first row finishes: eq writes straight
                # into the forbid buffer, no logical_not_/copies
                self._unpacker.unpack_forbid(masks, v, device, out=forbid)
            else:
                if self._forbid_rows and self._forbid_rows != rows_key:
                    now = set(rows_key)
                    stale = [r for r in self._forbid_rows if r not in now]
                    if stale:
                        forbid.index_fill_(
                            0,
                            torch.tensor(stale, dtype=torch.long, device=device),
                            False,
                        )
                bits = self._unpacker.unpack_forbid(masks, v, device)
                idx = self._rows_index(rows, device)
                forbid.index_copy_(0, idx, bits)
            self._forbid_rows = rows_key
            target.masked_fill_(forbid[:, :v], float("-inf"))
            # One reduction + one sync instead of isnan/isposinf/isfinite
            # cascade with two D2H round trips: forbidden positions are
            # exactly -inf and cannot dominate, NaN/+inf of an allowed
            # candidate is propagated by amax as is, all -inf means no
            # finite candidates left.
            row_max = target.amax(dim=1)  # [batch]
            if full:
                mx = row_max.tolist()
            else:
                mx = row_max.index_select(0, idx).tolist()
            for r, i in enumerate(rows):
                m = mx[r]
                if m != m or m == float("inf"):
                    # invalid scores among constraint-allowed candidates are an
                    # explicit error, never a silent random sample (FR-15)
                    self.dead_end[i] = True
                    self.active[i] = False
                    raise ZigConstraintsError(
                        f"row {i}: NaN/+inf among mask-allowed logits "
                        "(invalid candidate probabilities)"
                    )
                if m == float("-inf"):
                    # intersection with other settings left no candidates
                    self.dead_end[i] = True
                    self.active[i] = False
                    raise ZigConstraintsError(
                        f"row {i}: no finite logits left after masking "
                        "(generation settings conflict)"
                    )
        if width != v:
            scores[:, v:] = float("-inf")
        return scores

    def _finish_from(self, i: int, tail: "Sequence[int]") -> "tuple[bool, str]":
        """Finish a row from the CPU tail of tokens after self.seen[i].

        The processor did not see the last sampled token; it is accepted
        here. Tokens after the first EOS (HF padding) are skipped.
        completed is True only when a permitted EOS was accepted and
        finish() succeeded (§3.3, FR-14); without an EOS the row is not a
        completed response, even when can_end() is true.
        """
        sess = self.sessions[i]
        if sess is None:
            return True, "eos"
        if self.eos_done[i]:
            # finished by EOS during generate; the tail is HF padding
            return True, "eos"
        saw_eos = False
        try:
            for tok in tail:
                tok = int(tok)
                sess.accept_token(tok)
                self.seen[i] += 1
                if tok in self._row_eos[i]:
                    saw_eos = True
                    break
        except ZigConstraintsError:
            self.active[i] = False
            return False, "error"
        if self.dead_end[i]:
            return False, "dead_end"
        if not saw_eos:
            # length/timeout limit without an accepted EOS: not completed
            return False, "length"
        try:
            sess.finish()
        except ZigConstraintsError:
            self.active[i] = False
            return False, "error"
        self.active[i] = False
        return True, "eos"

    def finish_row(self, i: int, row_ids: Any) -> "tuple[bool, str]":
        """Final sync of row i after generate -> (completed, stop_reason).

        row_ids is the full row (prompt + generated); only the tail after
        the already accepted tokens is read, with a single .tolist() for
        tensors - per-row int(cuda scalar) calls cost ~30 µs each.
        """
        start = self.prompt_len + self.seen[i]
        if start < len(row_ids):
            tail = row_ids[start:]
            if hasattr(tail, "tolist"):
                tail = tail.tolist()  # one batched transfer per row
        else:
            tail = ()
        return self._finish_from(i, tail)

    def finish_rows(self, sequences: Any) -> "List[tuple[bool, str]]":
        """Batch finalization: one D2H for all rows, then a CPU loop.

        Only the tail after each row's accepted tokens is transferred (the
        slice starting at the minimum start across the batch), not the whole
        prompt: replaces ~520 scalar int(cuda scalar) calls (~15 ms at b32)
        with one batched .tolist(). Non-tensor inputs are handled row by row.
        """
        n = len(self.sessions)
        if n == 0:
            return []
        if not hasattr(sequences, "tolist"):
            return [self.finish_row(i, sequences[i]) for i in range(n)]
        starts = [self.prompt_len + self.seen[i] for i in range(n)]
        base = min(starts)
        length = int(sequences.shape[1])
        if base < length:
            tail_rows = sequences[:, base:].tolist()  # one sync per batch
        else:
            tail_rows = [[] for _ in range(n)]
        out: List[tuple] = []
        for i in range(n):
            out.append(self._finish_from(i, tail_rows[i][starts[i] - base :]))
        return out


_GEN_CONFIG_KEYS = (
    "num_beams",
    "num_return_sequences",
    "forced_eos_token_id",
    "forced_decoder_ids",
    "eos_token_id",
)


def _final_generation_config(
    model: Any, gen_kwargs: dict
) -> "tuple[Any, Dict[str, Any]]":
    """Single final GenerationConfig + runtime kwargs.

    Rules match model.generate: a passed ``generation_config`` replaces
    model.generation_config (HF deep-copy, not merge); remaining kwargs
    override fields of that base, an explicit None clears a value.
    Everything that is not a GenerationConfig field (streamer,
    attention_mask and other generate runtime arguments) is returned
    separately.

    The same config object is used by the preflight, the HF stopping
    criteria and the final generate call, so validation and the run can no
    longer diverge (previously EOS was filtered only in the processor, and
    explicit num_beams/num_return_sequences were dropped from kwargs).
    """
    import copy

    base = gen_kwargs.get("generation_config")
    if base is None:
        base = getattr(model, "generation_config", None)
    cfg = copy.deepcopy(base) if base is not None else GenerationConfig()
    runtime: Dict[str, Any] = {}
    for key, value in gen_kwargs.items():
        if key == "generation_config":
            continue
        if key in _GEN_CONFIG_KEYS or hasattr(cfg, key):
            setattr(cfg, key, value)
        else:
            runtime[key] = value
    return cfg, runtime


def _check_generation_conflicts(cfg: Any, runtime: Dict[str, Any]) -> None:
    """Reject unsupported modes before generation, by effective values (FR-14/15)."""

    def val(key: str, default: Any = None) -> Any:
        if key in runtime:
            return runtime[key]
        return getattr(cfg, key, default)

    num_beams = val("num_beams", 1)
    if num_beams is not None and int(num_beams) != 1:
        raise UnsupportedModeError("num_beams>1 (beam search) is not supported")
    num_return = val("num_return_sequences", 1)
    if num_return is not None and int(num_return) != 1:
        raise UnsupportedModeError("num_return_sequences>1 is not supported")
    if val("forced_eos_token_id") is not None:
        raise UnsupportedModeError(
            "forced_eos_token_id conflicts with constraint finalization"
        )
    if val("forced_decoder_ids"):
        raise UnsupportedModeError("forced_decoder_ids is not supported")
    if val("logits_processor"):
        raise UnsupportedModeError(
            "custom logits_processor conflicts with the constraint mask "
            "(application order not proven); pass settings via gen_kwargs"
        )


def _as_id_list(value: Any) -> List[int]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    return [int(value)]


def constrained_generate(
    model: Any,
    tokenizer: Any,
    constraint: Union[Constraint, Sequence[Constraint]],
    inputs: Optional[Any] = None,
    max_new_tokens: int = 128,
    do_sample: bool = False,
    **gen_kwargs: Any,
) -> ConstrainedGenerateResult:
    """Wrapper around model.generate applying constraints (no monkey-patch).

    constraint - one Constraint for the whole batch or a list per row.
    Unsupported modes are rejected before generate using ONE final config:
    the passed generation_config replaces model.generation_config (as in
    model.generate), remaining kwargs override its fields (an explicit
    None clears a value). That same config goes to model.generate, so the
    preflight and the run cannot diverge. The EOS set is the
    union of tokenizer.eos_token_id and the effective eos_token_id,
    restricted to ids the native tokenizer registered: extra ids (e.g. a
    base-model <|endoftext|> from generation_config) are excluded from
    BOTH the processor and the generation config with a warning - HF must
    not stop mid-document on an id the kernel cannot finalize. Per-row
    termination sets are checked against each row's own engine. After
    generate, the last sampled token of each row (invisible to the
    processor) is accepted and the row is finalized ->
    completed/stop_reason.
    """
    cfg, runtime = _final_generation_config(model, gen_kwargs)
    _check_generation_conflicts(cfg, runtime)
    if inputs is None:
        raise InvalidArgumentError(
            "inputs is required (a dict with input_ids, as in model.generate)"
        )
    input_ids = inputs["input_ids"] if isinstance(inputs, dict) else inputs.input_ids
    if input_ids.dim() != 2:
        raise InvalidArgumentError("input_ids must be [batch, seq_len]")
    batch = input_ids.shape[0]
    prompt_len = input_ids.shape[1]

    if isinstance(constraint, Constraint):
        constraints: List[Constraint] = [constraint] * batch
    else:
        constraints = list(constraint)
        if len(constraints) != batch:
            raise InvalidArgumentError(
                f"constraint: list of length {len(constraints)} does not match batch {batch}"
            )

    # The effective HF EOS set must be a subset of the EOS ids
    # the native tokenizer registered - an extra id is not a document
    # terminator for the kernel, so HF could stop mid-document (schema
    # violation). Standard configs (Qwen2.5: base <|endoftext|> + <|im_end|>
    # in generation_config) carry such extras routinely, so we exclude them
    # with a warning everywhere (config + processor); the mask then never
    # allows them and generation finalizes only on confirmed EOS.
    requested_eos = sorted(
        set(_as_id_list(getattr(tokenizer, "eos_token_id", None)))
        | set(_as_id_list(getattr(cfg, "eos_token_id", None)))
    )
    row_core: List[set] = [set(c._engine.eos_ids) for c in constraints]
    common_core: set = set.intersection(*row_core) if row_core else set()
    union_core: set = set().union(*row_core) if row_core else set()
    missing_eos = sorted(set(requested_eos) - union_core)
    if missing_eos:
        warnings.warn(
            f"effective EOS {missing_eos} are not registered in the core "
            f"(native tokenizer: {sorted(union_core)}); they are excluded "
            "from generation stopping (otherwise the document may be cut "
            "off). Add them to TokenizerBundle.eos_ids to allow stopping "
            "on them.",
            UserWarning,
            stacklevel=2,
        )
    # HF stopping is global: only ids confirmed by the kernel of EVERY row
    # are safe; each row gets its own subset (A3).
    global_eos = sorted(set(requested_eos) & common_core)
    if not global_eos:
        raise UnsupportedModeError(
            "after excluding unconfirmed EOS, no common id remains: "
            "the kernel cannot finalize the document. Add EOS to "
            "TokenizerBundle.eos_ids."
        )
    cfg.eos_token_id = global_eos
    row_eos = [sorted(set(requested_eos) & core) for core in row_core]

    sessions: List[Session] = [c.create_session() for c in constraints]
    processor = ConstraintLogitsProcessor(
        sessions, prompt_len, eos_ids_per_row=row_eos
    )

    cfg.max_new_tokens = int(max_new_tokens)
    cfg.do_sample = bool(do_sample)
    inputs_kwargs = dict(inputs) if isinstance(inputs, dict) else {"input_ids": input_ids}
    try:
        out = model.generate(
            **inputs_kwargs,
            generation_config=cfg,
            logits_processor=[processor],
            **runtime,
        )
    except Exception:
        for s in sessions:
            try:
                s.abort()
            finally:
                # abort does not free the native session; without close the
                # Engine stays busy until GC collects the wrappers
                s.close()
        raise

    sequences = out.sequences if hasattr(out, "sequences") else out

    rows = processor.finish_rows(sequences)
    completed: List[bool] = [r[0] for r in rows]
    stop_reason: List[str] = [r[1] for r in rows]

    return ConstrainedGenerateResult(
        sequences=sequences,
        completed=completed,
        stop_reason=stop_reason,
        sessions=sessions,
    )
