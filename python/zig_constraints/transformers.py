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

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

try:
    import torch
    from transformers import LogitsProcessor
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "zig_constraints.transformers требует torch и transformers: "
        "pip install zig-constraints[transformers]"
    ) from e

from . import (
    Constraint,
    InvalidArgumentError,
    Session,
    UnsupportedModeError,
    ZigConstraintsError,
)

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
            allowed = torch.empty(rows, nwords * 32, dtype=torch.bool, device=device)
            bufs = (pinned, words_dev, bit_masks, allowed)
            self._bufs[key] = bufs
        return bufs

    def unpack(self, masks: Union[bytes, Sequence[bytes]], vocab_size: int, device: Any) -> Any:
        """Mask bytes -> bool tensor [rows, vocab_size] on device (True = allowed).

        The result views an internal buffer that is reused on the next
        unpack call with the same (rows, words, device): consume it before
        unpacking again.
        """
        if isinstance(masks, (bytes, bytearray)):
            masks = [bytes(masks)]
        nwords = (vocab_size + 31) // 32
        want = nwords * 4
        for m in masks:
            if len(m) != want:
                raise InvalidArgumentError(
                    f"маска {len(m)} байт не соответствует словарю "
                    f"{vocab_size} (ожидается {want} байт)"
                )
        pinned, words_dev, bit_masks, allowed = self._buffers(len(masks), nwords, device)
        host = torch.frombuffer(bytearray().join(masks), dtype=torch.int32)
        pinned.copy_(host)
        words_dev.copy_(pinned, non_blocking=True)
        hits = words_dev.view(len(masks), nwords, 1).bitwise_and(bit_masks)
        torch.ne(hits.view(len(masks), nwords * 32), 0, out=allowed)
        return allowed[:, :vocab_size]


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
    not see the last sampled token — constrained_generate accepts it
    after generate.

    A row that accepted EOS is finalized (session.finish) and
    deactivated: HF pads it for the rest of the batch, and those pads
    never reach accept_token.

    If the model's logit width (lm_head size) exceeds the constraint
    vocab (padded vocab, e.g. Qwen2.5: 151936 > 151665), the mask covers
    the first vocab_size positions and the tail always gets -inf: those
    are undecodable dummy lm_head positions. Real EOS/pad ids lie inside
    vocab_size and are unaffected.
    """

    def __init__(
        self,
        sessions: Sequence[Optional[Session]],
        prompt_len: int,
        eos_ids: Sequence[int] = (),
    ) -> None:
        if not sessions:
            raise InvalidArgumentError("sessions: пустой батч")
        self.sessions = list(sessions)
        self.prompt_len = int(prompt_len)
        self.eos_ids = frozenset(int(i) for i in eos_ids)
        self.seen = [0] * len(self.sessions)  # accepted constraint tokens
        self.active = [s is not None for s in self.sessions]
        self.dead_end = [False] * len(self.sessions)
        self.eos_done = [False] * len(self.sessions)  # row finished by EOS
        self._vocab_size = next(
            (s.vocab_size for s in self.sessions if s is not None), None
        )
        if self._vocab_size is None:
            raise InvalidArgumentError("sessions: все строки None")
        self._unpacker = MaskGpuUnpacker()
        self._forbid_buf: Optional[Any] = None

    def _sync_row(self, i: int, row_ids: Any, upto: int) -> None:
        """Accept tokens row_ids[prompt_len+seen : prompt_len+upto].

        After EOS the row is finished and deactivated; its further tokens
        (HF padding of finished rows) are skipped, not accepted.
        """
        sess = self.sessions[i]
        assert sess is not None
        while self.seen[i] < upto:
            tok = int(row_ids[self.prompt_len + self.seen[i]])
            self.seen[i] += 1
            if self.eos_done[i]:
                continue  # pad after a finished row
            sess.accept_token(tok)  # the core rejects EOS before document end
            if tok in self.eos_ids:
                # the mask permits EOS only at the document boundary
                sess.finish()
                self.eos_done[i] = True
                self.active[i] = False

    def __call__(self, input_ids: Any, scores: Any) -> Any:
        width = scores.shape[-1]
        v = self._vocab_size
        if width < v:
            raise ZigConstraintsError(
                f"ширина логитов {width} меньше словаря ограничений {v}"
            )
        # padded lm_head: mask by the constraint vocab, forbid the tail
        target = scores if width == v else scores[:, :v]
        device = scores.device
        total = input_ids.shape[1] - self.prompt_len
        rows: List[int] = []
        masks: List[bytes] = []
        for i, sess in enumerate(self.sessions):
            if not self.active[i] or sess is None:
                continue
            self._sync_row(i, input_ids[i], total)
            if not self.active[i]:
                continue  # row finished by EOS on this step
            masks.append(sess.fill_mask())  # DeadEndError propagates explicitly (FR-15)
            rows.append(i)
        if rows:
            # One compact H2D for the whole batch; bits unpack on the device.
            allowed = self._unpacker.unpack(masks, v, device)  # [rows, v] bool
            idx = torch.tensor(rows, dtype=torch.long, device=device)
            forbid = self._forbid_buf
            if (
                forbid is None
                or forbid.shape[0] != target.shape[0]
                or forbid.shape[1] != v
                or forbid.device != device
            ):
                forbid = torch.empty(target.shape[0], v, dtype=torch.bool, device=device)
                self._forbid_buf = forbid
            forbid.zero_()
            forbid.index_copy_(0, idx, torch.logical_not(allowed))
            target.masked_fill_(forbid, float("-inf"))
            row_logits = target.index_select(0, idx)
            bad = (
                (allowed & (torch.isnan(row_logits) | torch.isposinf(row_logits)))
                .any(dim=1)
                .tolist()
            )
            finite = torch.isfinite(row_logits).any(dim=1).tolist()
            for r, i in enumerate(rows):
                if bad[r]:
                    # invalid scores among constraint-allowed candidates are an
                    # explicit error, never a silent random sample (FR-15)
                    self.dead_end[i] = True
                    self.active[i] = False
                    raise ZigConstraintsError(
                        f"строка {i}: NaN/+inf среди разрешённых маской логитов "
                        "(некорректные вероятности кандидатов)"
                    )
                if not finite[r]:
                    # intersection with other settings left no candidates
                    self.dead_end[i] = True
                    self.active[i] = False
                    raise ZigConstraintsError(
                        f"строка {i}: после маскирования не осталось "
                        "конечных логитов (конфликт настроек генерации)"
                    )
        if width != v:
            scores[:, v:] = float("-inf")
        return scores

    def finish_row(self, i: int, row_ids: Any) -> "tuple[bool, str]":
        """Final sync of row i after generate -> (completed, stop_reason).

        The processor never saw the last sampled token; it is accepted
        here. Tokens after the first EOS (HF padding) are skipped.
        completed is True only when a permitted EOS was accepted and
        finish() succeeded (§3.3, FR-14); without EOS the row is not a
        completed answer, even when can_end() holds.
        """
        sess = self.sessions[i]
        if sess is None:
            return True, "eos"
        if self.eos_done[i]:
            # finished by EOS during generate; the tail is HF padding
            return True, "eos"
        total = len(row_ids) - self.prompt_len
        saw_eos = False
        try:
            while self.seen[i] < total:
                tok = int(row_ids[self.prompt_len + self.seen[i]])
                sess.accept_token(tok)
                self.seen[i] += 1
                if tok in self.eos_ids:
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


_GEN_CONFIG_KEYS = (
    "num_beams",
    "num_return_sequences",
    "forced_eos_token_id",
    "forced_decoder_ids",
    "eos_token_id",
)


def _effective_generation_config(model: Any, gen_kwargs: dict) -> dict:
    """model.generation_config merged with explicit gen_kwargs (kwargs win).

    An explicit None in gen_kwargs clears the generation_config value.
    """
    eff: Dict[str, Any] = {}
    gc = getattr(model, "generation_config", None)
    if gc is not None:
        for key in _GEN_CONFIG_KEYS:
            value = getattr(gc, key, None)
            if value is not None:
                eff[key] = value
    for key, value in gen_kwargs.items():
        if value is None:
            eff.pop(key, None)
        else:
            eff[key] = value
    return eff


def _check_generation_conflicts(eff: dict) -> None:
    """Reject unsupported modes before generation, by effective values (FR-14/15)."""
    if eff.get("num_beams", 1) != 1:
        raise UnsupportedModeError("num_beams>1 (beam search) не поддерживается")
    if eff.get("num_return_sequences", 1) != 1:
        raise UnsupportedModeError("num_return_sequences>1 не поддерживается")
    if eff.get("forced_eos_token_id") is not None:
        raise UnsupportedModeError(
            "forced_eos_token_id конфликтует с финализацией ограничений"
        )
    if eff.get("forced_decoder_ids"):
        raise UnsupportedModeError("forced_decoder_ids не поддерживается")
    if eff.get("logits_processor"):
        raise UnsupportedModeError(
            "пользовательские logits_processor конфликтуют с маской ограничений "
            "(порядок применения не доказан); передайте настройки через gen_kwargs"
        )


def _as_id_list(value: Any) -> List[int]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    return [int(value)]


def _collect_eos_ids(tokenizer: Any, eff: dict) -> List[int]:
    """Union of tokenizer.eos_token_id and the effective eos_token_id (int|list)."""
    ids = set(_as_id_list(getattr(tokenizer, "eos_token_id", None)))
    ids.update(_as_id_list(eff.get("eos_token_id")))
    return sorted(ids)


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

    constraint — one Constraint for the whole batch or a list per row.
    Unsupported modes are rejected before generate using the effective
    config: model.generation_config merged with gen_kwargs (explicit
    kwargs win; an explicit None clears a generation_config value). The
    processor's EOS set is the union of tokenizer.eos_token_id and the
    effective eos_token_id (int or list). After generate, the last
    sampled token of each row (invisible to the processor) is accepted
    and the row is finalized -> completed/stop_reason.
    """
    eff = _effective_generation_config(model, gen_kwargs)
    _check_generation_conflicts(eff)
    if inputs is None:
        raise InvalidArgumentError(
            "inputs обязателен (словарь с input_ids, как у model.generate)"
        )
    input_ids = inputs["input_ids"] if isinstance(inputs, dict) else inputs.input_ids
    if input_ids.dim() != 2:
        raise InvalidArgumentError("input_ids должен быть [batch, seq_len]")
    batch = input_ids.shape[0]
    prompt_len = input_ids.shape[1]

    if isinstance(constraint, Constraint):
        constraints: List[Constraint] = [constraint] * batch
    else:
        constraints = list(constraint)
        if len(constraints) != batch:
            raise InvalidArgumentError(
                f"constraint: список длины {len(constraints)} не совпадает с батчем {batch}"
            )

    sessions: List[Session] = [c.create_session() for c in constraints]
    processor = ConstraintLogitsProcessor(
        sessions, prompt_len, eos_ids=_collect_eos_ids(tokenizer, eff)
    )

    kwargs = dict(gen_kwargs)
    kwargs.pop("num_beams", None)
    kwargs.pop("num_return_sequences", None)
    try:
        out = model.generate(
            **(dict(inputs) if isinstance(inputs, dict) else {"input_ids": input_ids}),
            max_new_tokens=max_new_tokens,
            do_sample=do_sample,
            logits_processor=[processor],
            **kwargs,
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

    completed: List[bool] = []
    stop_reason: List[str] = []
    for i in range(batch):
        done, reason = processor.finish_row(i, sequences[i])
        completed.append(done)
        stop_reason.append(reason)

    return ConstrainedGenerateResult(
        sequences=sequences,
        completed=completed,
        stop_reason=stop_reason,
        sessions=sessions,
    )
