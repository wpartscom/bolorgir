#!/usr/bin/env python3
"""Structured-output requests against a patched vLLM server (stdlib only).

Shows the four request kinds the Bolorgir backend serves and the
`reply` / `escalate` support-bot scenario. Start the server first (see
docs/integrations/vllm.md):

    vllm serve Qwen/Qwen2.5-0.5B-Instruct \
        --structured-outputs-config '{"backend": "bolorgir"}'

Then:

    python3 examples/vllm_structured_outputs.py [--base-url http://127.0.0.1:8000/v1]
"""

import argparse
import json
import urllib.request


def post(base_url, model, messages, extra=None):
    body = {"model": model, "messages": messages, "temperature": 0.0,
            "max_tokens": 64, **(extra or {})}
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp["choices"][0]["message"].get("content")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    ap.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    args = ap.parse_args()

    # 1. JSON Schema: a strict object with an enum and a bounded integer.
    schema = {
        "type": "object",
        "properties": {
            "action": {"enum": ["reply", "escalate"]},
            "priority": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        "required": ["action", "priority"],
        "additionalProperties": False,
    }
    text = post(args.base_url, args.model,
                [{"role": "user",
                  "content": "Classify: the customer asks for a refund. "
                             "Answer as JSON with action and priority."}],
                {"response_format": {"type": "json_schema",
                                     "json_schema": {"name": "triage", "strict": True,
                                                     "schema": schema}}})
    print("json_schema:", text)
    doc = json.loads(text)
    assert doc["action"] in ("reply", "escalate")

    # 2. Literal choice: exactly one of the two decisions, plain text.
    text = post(args.base_url, args.model,
                [{"role": "system", "content": "Answer with reply or escalate only."},
                 {"role": "user", "content": "I forgot my password."}],
                {"structured_outputs": {"choice": ["reply", "escalate"]}})
    print("choice:", text)
    assert text.strip() in ("reply", "escalate")

    # 3. Standalone regex: a build number like v12.
    text = post(args.base_url, args.model,
                [{"role": "user", "content": "Give a build number like v12."}],
                {"structured_outputs": {"regex": "v[0-9]{1,3}"}})
    print("regex:", text)
    assert text.startswith("v") and text[1:].isdigit()

    # 4. A format the backend refuses explicitly (never a silent fallback).
    #    Uncomment to see the request error:
    # post(args.base_url, args.model, [{"role": "user", "content": "hi"}],
    #      {"structured_outputs": {"grammar": "root ::= 'a'"}})

    print("all requests followed their constraint exactly")


if __name__ == "__main__":
    main()
