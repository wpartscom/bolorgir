"""Общие утилиты GPU-бенчмарков (B7/T5) с реальной HF-моделью.

Исторически здесь жили workaround'ы bug B-1 (padded lm_head), B-3 (ранний
EOS в батче) и B-2 (BusyError маскирует исходную ошибку) — см. reproducers
в benchmarks/results/20260914T213127/. Фиксы перенесены в пакет
(python/zig_constraints/transformers.py, __init__.py); имена
VocabSafeConstraintProcessor / constrained_generate_safe сохранены как
алиасы штатных классов, чтобы скрипты бенчмарков не менялись.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

import torch
from transformers.generation.streamers import BaseStreamer

from zig_constraints.transformers import (
    ConstrainedGenerateResult,
    ConstraintLogitsProcessor,
    constrained_generate,
)

MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"

# алиасы штатного пути (фиксы B-1/B-3/B-2 — в пакете)
VocabSafeConstraintProcessor = ConstraintLogitsProcessor
constrained_generate_safe = constrained_generate


class StepStreamer(BaseStreamer):
    """Метки времени шагов генерации (put вызывается на каждом шаге).

    Только time.perf_counter_ns(), без cuda-синхронизации — не искажает
    конвейер (ТЗ 10.5). Первый put — сам промпт; токены начинаются со
    второго -> TTFT = ts[1] - t_start.
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
    """Число сгенерированных токенов строки до первого EOS включительно."""
    ids = seq[prompt_len:].tolist()
    if eos_id is not None and eos_id in ids:
        return ids.index(eos_id) + 1
    return len(ids)
