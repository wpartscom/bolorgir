"""Shared utilities for GPU benchmarks (B7/T5) with a real HF model.

Historically this held workarounds for bug B-1 (padded lm_head), B-3 (early
EOS in a batch) and B-2 (BusyError masking the original error) - see
reproducers in benchmarks/results/20260914T213127/. The fixes moved into the
package (python/bolorgir/transformers.py, __init__.py); the names
VocabSafeConstraintProcessor / constrained_generate_safe are kept as aliases
of the stock classes so benchmark scripts did not change.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

import torch
from transformers.generation.streamers import BaseStreamer

from bolorgir.transformers import (
    ConstrainedGenerateResult,
    ConstraintLogitsProcessor,
    constrained_generate,
)

MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"

# aliases of the stock path (fixes B-1/B-3/B-2 live in the package)
VocabSafeConstraintProcessor = ConstraintLogitsProcessor
constrained_generate_safe = constrained_generate


class StepStreamer(BaseStreamer):
    """Timestamps of generation steps (put is called on every step).

    Only time.perf_counter_ns(), no cuda sync - does not distort the
    pipeline (SPEC 10.5). The first put is the prompt itself; tokens start
    from the second one -> TTFT = ts[1] - t_start.
    """

    def __init__(self) -> None:
        self.ts: List[int] = []

    def put(self, value: Any) -> None:
        self.ts.append(time.perf_counter_ns())

    def end(self) -> None:
        pass


def close_result(res: ConstrainedGenerateResult) -> None:
    for s in res.sessions:
        if s is not None:
            s.close()


def load_model(model_id: str = MODEL_ID):
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(model_id, dtype=torch.float16)
    model = model.to("cuda").eval()
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    return model, tokenizer


def row_new_tokens(seq: Any, prompt_len: int, eos_id: Optional[int]) -> int:
    """Number of generated tokens in the row up to and including the first EOS."""
    ids = seq[prompt_len:].tolist()
    if eos_id is not None and eos_id in ids:
        return ids.index(eos_id) + 1
    return len(ids)
