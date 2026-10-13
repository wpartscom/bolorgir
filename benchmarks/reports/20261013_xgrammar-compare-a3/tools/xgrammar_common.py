#!/usr/bin/env python3
"""Shared, frozen request definitions of the T10 section-10 comparison.

The prompts, schemas, payloads and validation of the 2026-10-12 series
(`xgrammar_bench.py`) verbatim, so the A3 compact series and the report
compute the same requests and the same validity. Imported by
`xgrammar_bench2.py` (collector, records the answer text and the stream
end state), `xgrammar_report.py` (independent re-validation and
counting) and the tests.
"""

import json
import re

import jsonschema

N_PROMPTS = 128

W1_SCHEMA = {
    "type": "object",
    "properties": {"action": {"enum": ["reply", "escalate"]},
                   "priority": {"type": "integer", "minimum": 1, "maximum": 5}},
    "required": ["action", "priority"],
    "additionalProperties": False,
}
W2_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "minItems": 3, "maxItems": 3,
                  "items": {"type": "object",
                            "properties": {
                                "name": {"type": "string", "minLength": 3,
                                         "maxLength": 24},
                                "count": {"type": "integer", "minimum": 0,
                                          "maximum": 999}},
                            "required": ["name", "count"],
                            "additionalProperties": False}},
        "total": {"type": "integer", "minimum": 0},
    },
    "required": ["items", "total"],
    "additionalProperties": False,
}

WORKLOADS = ("w1_json_small", "w2_json_nested", "w3_regex64")


def prompts(workload):
    out = []
    for k in range(N_PROMPTS):
        if workload == "w1_json_small":
            text = f"Ticket {k}: a customer cannot log in. Answer as JSON only."
        elif workload == "w2_json_nested":
            text = (f"Order sheet {k}: three items, each with a name and a "
                    "count, plus the total. Answer as JSON only.")
        else:
            text = f"Write exactly 64 digits in a row, nothing else. Line {k}."
        out.append([{"role": "user", "content": text}])
    return out


def payload(workload, model, messages):
    b = {"model": model, "messages": messages, "temperature": 0.0,
         "stream": True, "stream_options": {"include_usage": True}}
    if workload == "w1_json_small":
        b["max_tokens"] = 64
        b["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "triage", "strict": True,
                            "schema": W1_SCHEMA}}
    elif workload == "w2_json_nested":
        b["max_tokens"] = 128
        b["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "sheet", "strict": True,
                            "schema": W2_SCHEMA}}
    else:
        b["max_tokens"] = 72
        b["structured_outputs"] = {"regex": "[0-9]{64}"}
    return b


def validate(workload, text):
    if text is None:
        return False, "no text"
    try:
        if workload == "w1_json_small":
            jsonschema.validate(json.loads(text), W1_SCHEMA)
            return True, ""
        if workload == "w2_json_nested":
            jsonschema.validate(json.loads(text), W2_SCHEMA)
            return True, ""
        return (re.fullmatch(r"[0-9]{64}", text) is not None), ""
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"[:200]


def completion_check(workload, text):
    """Bounded completion test for a `length`-cut answer (TZ 6.2): try to
    append suffixes from a fixed list (single and two-element) so that the
    result parses and passes the workload's validation. Returns
    (True, suffix) when some bounded completion is valid, (False, None)
    otherwise. This is a bounded test with a declared suffix list, not a
    proof of continuability and not a schema check of the cut itself."""
    if text is None:
        return False, None
    suffixes = ["0", "1", "5", "9", ".0", ".5", "e0", "e1", "e2", "+0",
                '"', '"0"', "]", "}", '"}', "]}", "}", '"}', '0}']
    candidates = suffixes + [a + b for a in suffixes for b in suffixes[:8]]
    for s in candidates:
        ok, _ = validate(workload, text + s)
        if ok:
            return True, s
    return False, None
