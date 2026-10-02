#!/usr/bin/env python3
"""T5. HF + bolorgir integration checks on GPU (SPEC §11 T5).

Checks (CPU and CUDA, greedy/sampling, mixed batch, left padding, early EOS,
length limit, recovery after an error, processor conflicts, reflection of the
final token in the session state, unsupported modes). Separately: a constraint
ban is NOT lifted by a following processor (MinNewTokens/RepetitionPenalty/
additive boost of a banned token); equality of constrained and unconstrained
texts is NOT required (SPEC T5).

Uses constrained_generate_safe / VocabSafeConstraintProcessor - workarounds
for bug B-1/B-3 (see hf_common.py and reproducers in results). The engine
(masks, sessions) is stock.

Run from the project root:
    PYTHONPATH=python:benchmarks python3 benchmarks/t5_checks.py \
        --out benchmarks/results/<timestamp>
"""

from __future__ import annotations

import argparse
import json
import os

import torch
import jsonschema
from transformers.generation.logits_process import (
    MinNewTokensLengthLogitsProcessor,
    RepetitionPenaltyLogitsProcessor,
)

from bolorgir import (
    Engine,
    InvalidSchemaError,
    UnsupportedFeatureError,
    UnsupportedModeError,
    ZigConstraintsError,
)

from hf_common import (
    MODEL_ID,
    VocabSafeConstraintProcessor,
    close_result,
    constrained_generate_safe,
    load_model,
    row_new_tokens,
)

SEED = 42

S1 = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
        "amount": {"type": "number"},
        "currency": {"type": "string", "enum": ["USD", "EUR", "JPY"]},
    },
    "required": ["action", "amount", "currency"],
    "additionalProperties": False,
}
S2 = {
    "type": "object",
    "properties": {
        "order_id": {"type": "integer"},
        "items": {
            "type": "array",
            "minItems": 1,
            "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "sku": {"type": "string", "enum": ["A-1", "B-2"]},
                    "qty": {"type": "integer"},
                },
                "required": ["sku", "qty"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["order_id", "items"],
    "additionalProperties": False,
}
S3 = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1, "maxLength": 24},
        "year": {"type": "integer"},
    },
    "required": ["title", "year"],
    "additionalProperties": False,
}
# short document for the early EOS check
S_TINY = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
# document with a forcibly long string for the length-limit check
S_LONG = {
    "type": "object",
    "properties": {"text": {"type": "string", "minLength": 400}},
    "required": ["text"],
    "additionalProperties": False,
}

PROMPTS = {
    "s1": "Reply with one JSON object.\nRequest: buy 150 shares in USD.\nJSON:",
    "s2": "Return the order as JSON.\nOrder 7: 2x A-1.\nJSON:",
    "s3": "Describe the movie as JSON.\nMovie: 'Dune' (2021).\nJSON:",
    "tiny": "Answer as JSON.\nIs the sky blue? yes -> true.\nJSON:",
    "long": "Reply with one JSON object with a long 'text' field.\nJSON:",
}

CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn

    return deco


def decode_rows(res, tokenizer, prompt_len, eos_id):
    rows = []
    for i in range(res.sequences.shape[0]):
        n = row_new_tokens(res.sequences[i], prompt_len, eos_id)
        text = tokenizer.decode(
            res.sequences[i][prompt_len : prompt_len + n], skip_special_tokens=True
        )
        rows.append({"n": n, "text": text})
    return rows


def all_valid(rows, schema):
    for r in rows:
        jsonschema.validate(json.loads(r["text"]), schema)
    return True


def make_inputs(tokenizer, prompts, device="cuda"):
    tokenizer.padding_side = "left"
    enc = tokenizer(prompts, padding=True, return_tensors="pt")
    return {k: v.to(device) for k, v in enc.items()}


# --- checks ---------------------------------------------------------------


@check("greedy_cuda_batched_left_padding")
def t_greedy(ctx):
    tok, model, engine = ctx["tokenizer"], ctx["model"], ctx["engine"]
    c = engine.compile(S1)
    inputs = make_inputs(tok, [PROMPTS["s1"], "JSON:", PROMPTS["s1"] + " Again: buy 1 in JPY."])
    res = constrained_generate_safe(model, tok, c, inputs=inputs, max_new_tokens=64)
    rows = decode_rows(res, tok, inputs["input_ids"].shape[1], tok.eos_token_id)
    close_result(res)
    c.close()
    ok = all(res.completed) and all(r == "eos" for r in res.stop_reason) and all_valid(rows, S1)
    return ok, {"completed": res.completed, "stop_reason": res.stop_reason,
                "texts": [r["text"] for r in rows]}


@check("sampling_temperature_top_p")
def t_sampling(ctx):
    tok, model, engine = ctx["tokenizer"], ctx["model"], ctx["engine"]
    c = engine.compile(S1)
    inputs = make_inputs(tok, [PROMPTS["s1"]] * 4)
    torch.manual_seed(SEED)
    res = constrained_generate_safe(
        model, tok, c, inputs=inputs, max_new_tokens=64,
        do_sample=True, temperature=0.8, top_p=0.95,
    )
    rows = decode_rows(res, tok, inputs["input_ids"].shape[1], tok.eos_token_id)
    close_result(res)
    c.close()
    ok = all(res.completed) and all_valid(rows, S1)
    return ok, {"stop_reason": res.stop_reason, "texts": [r["text"] for r in rows]}


@check("mixed_batch_different_schemas")
def t_mixed(ctx):
    tok, model, engine = ctx["tokenizer"], ctx["model"], ctx["engine"]
    cs = [engine.compile(s) for s in (S1, S2, S3, S1)]
    inputs = make_inputs(tok, [PROMPTS["s1"], PROMPTS["s2"], PROMPTS["s3"], PROMPTS["s1"]])
    res = constrained_generate_safe(model, tok, cs, inputs=inputs, max_new_tokens=96)
    rows = decode_rows(res, tok, inputs["input_ids"].shape[1], tok.eos_token_id)
    close_result(res)
    for c in cs:
        c.close()
    valid = True
    try:
        for r, s in zip(rows, (S1, S2, S3, S1)):
            jsonschema.validate(json.loads(r["text"]), s)
    except Exception:
        valid = False
    ok = all(res.completed) and valid
    return ok, {"completed": res.completed, "texts": [r["text"] for r in rows]}


@check("early_eos_short_document")
def t_early_eos(ctx):
    tok, model, engine = ctx["tokenizer"], ctx["model"], ctx["engine"]
    c = engine.compile(S_TINY)
    inputs = make_inputs(tok, [PROMPTS["tiny"]])
    res = constrained_generate_safe(model, tok, c, inputs=inputs, max_new_tokens=512)
    rows = decode_rows(res, tok, inputs["input_ids"].shape[1], tok.eos_token_id)
    close_result(res)
    c.close()
    ok = (
        res.completed == [True]
        and res.stop_reason == ["eos"]
        and rows[0]["n"] <= 8
        and all_valid(rows, S_TINY)
    )
    return ok, {"stop_reason": res.stop_reason, "n_tokens": rows[0]["n"],
                "text": rows[0]["text"]}


@check("max_length_cutoff")
def t_length(ctx):
    tok, model, engine = ctx["tokenizer"], ctx["model"], ctx["engine"]
    c = engine.compile(S_LONG)
    inputs = make_inputs(tok, [PROMPTS["long"]])
    res = constrained_generate_safe(model, tok, c, inputs=inputs, max_new_tokens=24)
    rows = decode_rows(res, tok, inputs["input_ids"].shape[1], tok.eos_token_id)
    close_result(res)
    c.close()
    # document cut off by the limit: completed=False, stop_reason="length",
    # the text is not required to be valid JSON
    ok = res.completed == [False] and res.stop_reason == ["length"] and rows[0]["n"] == 24
    return ok, {"completed": res.completed, "stop_reason": res.stop_reason,
                "n_tokens": rows[0]["n"], "text_prefix": rows[0]["text"][:60]}


@check("error_recovery_invalid_and_unsupported_schema")
def t_recovery(ctx):
    tok, model, engine = ctx["tokenizer"], ctx["model"], ctx["engine"]
    errs = []
    try:
        engine.compile({"type": "object"})  # no properties/required
    except InvalidSchemaError as e:
        errs.append("InvalidSchemaError")
    try:
        engine.compile({"type": "string", "pattern": "^a+$"})  # outside the MVP
    except UnsupportedFeatureError as e:
        errs.append("UnsupportedFeatureError")
    # engine is alive: compile a valid schema and generate after the errors
    c = engine.compile(S_TINY)
    inputs = make_inputs(tok, [PROMPTS["tiny"]])
    res = constrained_generate_safe(model, tok, c, inputs=inputs, max_new_tokens=16)
    rows = decode_rows(res, tok, inputs["input_ids"].shape[1], tok.eos_token_id)
    close_result(res)
    c.close()
    ok = errs == ["InvalidSchemaError", "UnsupportedFeatureError"] and all_valid(rows, S_TINY)
    return ok, {"errors": errs, "text_after": rows[0]["text"]}


def direct_generate(ctx, schema, prompts, extra_processors, max_new_tokens=48,
                    do_sample=False, **kw):
    """generate with a processor list: the constraint processor + extras after it."""
    tok, model, engine = ctx["tokenizer"], ctx["model"], ctx["engine"]
    c = engine.compile(schema)
    inputs = make_inputs(tok, prompts)
    prompt_len = inputs["input_ids"].shape[1]
    batch = len(prompts)
    sessions = [c.create_session() for _ in range(batch)]
    proc = VocabSafeConstraintProcessor(sessions, prompt_len, eos_ids=(int(tok.eos_token_id),))
    torch.manual_seed(SEED)
    out = model.generate(
        **inputs, max_new_tokens=max_new_tokens, do_sample=do_sample,
        logits_processor=[proc] + extra_processors(prompt_len),
        **kw,
    )
    sequences = out.sequences if hasattr(out, "sequences") else out
    fin = [proc.finish_row(i, sequences[i]) for i in range(batch)]
    rows = []
    for i in range(batch):
        n = row_new_tokens(sequences[i], prompt_len, tok.eos_token_id)
        rows.append({"n": n, "text": tok.decode(sequences[i][prompt_len:prompt_len + n],
                                                skip_special_tokens=True)})
    for s in sessions:
        s.close()
    c.close()
    return fin, rows


@check("processor_conflict_min_new_tokens_and_repetition_penalty")
def t_conflict_std(ctx):
    # (a) min_new=3 + RepetitionPenalty after the constraint processor:
    #     regular operation, the output is valid - subsequent processors do
    #     not lift the constraint ban.
    # (b) min_new=12 on a tiny schema: after the end of the document the mask
    #     allows only EOS, while MinNewTokens forbids EOS -> an explicit
    #     conflict error (FR-15), NOT a slip of an invalid token.
    tok = ctx["tokenizer"]

    def extra_ok(prompt_len):
        return [
            MinNewTokensLengthLogitsProcessor(prompt_len, 3, tok.eos_token_id,
                                              device="cuda"),
            RepetitionPenaltyLogitsProcessor(1.3),
        ]

    fin, rows = direct_generate(ctx, S1, [PROMPTS["s1"]], extra_ok,
                                max_new_tokens=64)
    ok_a = all(f[0] for f in fin)
    try:
        ok_a = ok_a and all_valid(rows, S1)
    except Exception:
        ok_a = False

    def extra_conflict(prompt_len):
        return [MinNewTokensLengthLogitsProcessor(prompt_len, 12, tok.eos_token_id,
                                                  device="cuda")]

    loud_error = None
    try:
        direct_generate(ctx, S_TINY, [PROMPTS["tiny"]], extra_conflict,
                        max_new_tokens=48)
    except ZigConstraintsError as e:
        loud_error = f"{type(e).__name__}: {str(e)[:80]}"
    ok_b = loud_error is not None
    return ok_a and ok_b, {
        "a_valid_output": rows[0]["text"],
        "b_loud_conflict_error": loud_error,
    }


@check("processor_conflict_additive_boost_banned_token")
def t_conflict_boost(ctx):
    # Additive boost of a banned token (+50 to the logit of an arbitrary
    # banned id): -inf + finite == -inf, the ban is not lifted.
    from transformers.generation.logits_process import LogitsProcessor

    tok = ctx["tokenizer"]
    boost_id = tok.encode("blah blah random text", add_special_tokens=False)[0]

    class BoostBanned(LogitsProcessor):
        def __call__(self, input_ids, scores):
            scores[:, boost_id] += 50.0
            return scores

    def extra(prompt_len):
        return [BoostBanned()]

    fin, rows = direct_generate(ctx, S1, [PROMPTS["s1"]], extra, max_new_tokens=64)
    completed = all(f[0] for f in fin)
    try:
        valid = all_valid(rows, S1)
    except Exception:
        valid = False
    return completed and valid, {"finish": fin, "text": rows[0]["text"],
                                 "boosted_id": boost_id}


@check("user_logits_processor_rejected_and_unsupported_modes")
def t_guards(ctx):
    tok, model, engine = ctx["tokenizer"], ctx["model"], ctx["engine"]
    from transformers.generation.logits_process import LogitsProcessor

    c = engine.compile(S_TINY)
    inputs = make_inputs(tok, [PROMPTS["tiny"]])
    got = []
    try:
        constrained_generate_safe(model, tok, c, inputs=inputs,
                                  logits_processor=[LogitsProcessor()])
    except UnsupportedModeError:
        got.append("user_processor")
    try:
        constrained_generate_safe(model, tok, c, inputs=inputs, num_beams=2)
    except UnsupportedModeError:
        got.append("num_beams")
    try:
        constrained_generate_safe(model, tok, c, inputs=inputs, num_return_sequences=2)
    except UnsupportedModeError:
        got.append("num_return_sequences")
    try:
        constrained_generate_safe(model, tok, c, inputs=inputs, forced_eos_token_id=1)
    except UnsupportedModeError:
        got.append("forced_eos")
    c.close()
    ok = got == ["user_processor", "num_beams", "num_return_sequences", "forced_eos"]
    return ok, {"rejected": got}


@check("eos_reflected_in_session_state")
def t_eos_state(ctx):
    tok, model, engine = ctx["tokenizer"], ctx["model"], ctx["engine"]
    c = engine.compile(S_TINY)
    inputs = make_inputs(tok, [PROMPTS["tiny"]])
    prompt_len = inputs["input_ids"].shape[1]
    res = constrained_generate_safe(model, tok, c, inputs=inputs, max_new_tokens=32)
    sess = res.sessions[0]
    stats = sess.stats()
    seq = res.sequences[0][prompt_len:].tolist()
    eos_pos = seq.index(tok.eos_token_id) if tok.eos_token_id in seq else None
    # tokens_accepted includes the final EOS: == EOS position + 1
    accepted_matches = eos_pos is not None and stats["tokens_accepted"] == eos_pos + 1
    state_err = None
    try:
        sess.accept_token(0)  # session finished -> WrongStateError
    except ZigConstraintsError as e:
        state_err = type(e).__name__
    close_result(res)
    c.close()
    ok = (
        res.completed == [True]
        and res.stop_reason == ["eos"]
        and accepted_matches
        and state_err == "WrongStateError"
    )
    return ok, {
        "stop_reason": res.stop_reason,
        "tokens_accepted": stats["tokens_accepted"],
        "eos_position": eos_pos,
        "accept_after_finish": state_err,
    }


@check("cpu_small_run")
def t_cpu(ctx):
    # CPU: a separate fp32 copy of the model, batch 1, short generation.
    from transformers import AutoModelForCausalLM

    tok = ctx["tokenizer"]
    engine = ctx["engine"]
    model_cpu = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.float32)
    model_cpu.eval()
    c = engine.compile(S_TINY)
    tokenizer_state = tok.padding_side
    inputs = make_inputs(tok, [PROMPTS["tiny"]], device="cpu")
    res = constrained_generate_safe(model_cpu, tok, c, inputs=inputs, max_new_tokens=8)
    rows = decode_rows(res, tok, inputs["input_ids"].shape[1], tok.eos_token_id)
    close_result(res)
    c.close()
    del model_cpu
    tok.padding_side = tokenizer_state
    ok = res.completed == [True] and all_valid(rows, S_TINY)
    return ok, {"stop_reason": res.stop_reason, "text": rows[0]["text"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    assert torch.cuda.is_available(), "CUDA is not available"
    model, tokenizer = load_model()
    engine = Engine(mode="adaptive", tokenizer=tokenizer)
    ctx = {"model": model, "tokenizer": tokenizer, "engine": engine}

    report = {"case": "t5_checks", "model": MODEL_ID,
              "gpu": torch.cuda.get_device_name(0), "seed": SEED, "checks": []}
    try:
        for name, fn in CHECKS:
            try:
                ok, evidence = fn(ctx)
                status = "PASS" if ok else "FAIL"
            except Exception as e:
                status, evidence = "FAIL", {"exception": f"{type(e).__name__}: {e}"}
            report["checks"].append({"name": name, "status": status,
                                     "evidence": evidence})
            print(f"{status}: {name}", flush=True)
    finally:
        engine.close()

    out_path = os.path.join(args.out, "t5_checks.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"saved: {out_path}")
    n_fail = sum(1 for c in report["checks"] if c["status"] != "PASS")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
