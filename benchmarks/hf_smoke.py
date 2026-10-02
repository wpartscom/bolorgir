#!/usr/bin/env python3
"""Smoke: HF Transformers + bolorgir on CUDA (Qwen2.5-1.5B-Instruct).

Run from the project root:
    PYTHONPATH=python python3 benchmarks/hf_smoke.py
"""

import json
import sys

import torch

from bolorgir import Engine

from hf_common import (
    close_result,
    constrained_generate_safe,
    load_model,
    MODEL_ID as MODEL,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "enum": ["Oslo", "Berlin", "Tokyo"]},
        "temp_c": {"type": "integer"},
        "note": {"type": "string", "maxLength": 32},
    },
    "required": ["city", "temp_c"],
    "additionalProperties": False,
}

PROMPT = (
    "Answer with JSON only. Weather report: city is Tokyo, temperature 21 C.\n"
    "JSON:"
)


def main() -> int:
    assert torch.cuda.is_available(), "CUDA is not available"
    print(f"device: {torch.cuda.get_device_name(0)}")
    model, tokenizer = load_model()

    inputs = tokenizer(PROMPT, return_tensors="pt").to("cuda")

    engine = Engine(mode="adaptive", tokenizer=tokenizer)
    constraint = engine.compile(SCHEMA)
    res = constrained_generate_safe(
        model, tokenizer, constraint, inputs=inputs, max_new_tokens=128
    )
    close_result(res)
    constraint.close()
    engine.close()

    prompt_len = inputs["input_ids"].shape[1]
    text = tokenizer.decode(res.sequences[0][prompt_len:], skip_special_tokens=True)
    print(f"completed={res.completed} stop_reason={res.stop_reason}")
    print(f"output: {text!r}")
    doc = json.loads(text)
    assert doc["city"] in ("Oslo", "Berlin", "Tokyo")
    assert isinstance(doc["temp_c"], int)
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
