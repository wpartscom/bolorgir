"""T4: differential campaign — kernel (ctypes) vs tests/reference.py.

For each random schema:
  1. Compilation classification: kernel (ZG_OK/INVALID_SCHEMA/
     UNSUPPORTED_FEATURE/UNSATISFIABLE_CONSTRAINT/RESOURCE_LIMIT) is compared
     with the reference exception class (InvalidSchema/UnsupportedFeature/
     UnsatisfiableConstraint).
  2. If both sides accept the schema — random walk: at each step the kernel
     mask == the Matcher reference mask, can_end agrees; an allowed token is
     accepted by both sides, a forbidden one is rejected with INVALID_TOKEN
     and does not change state (the mask afterwards is bitwise identical);
     kernel DEAD_END corresponds to an empty reference mask; finish is only
     possible at can_end, and the final document is valid per
     validate_document.

Any divergence -> reproducer in tests/fuzz/artifacts/ + test failure.
Scale: ZG_FUZZ_DIFF (default 600 schemas), seed: ZG_FUZZ_SEED.
"""

from __future__ import annotations

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fuzz_common as fc
import reference as ref
import schema_gen
import zg_ctypes

pytest.importorskip("zg_ctypes")

SEED = int(os.environ.get("ZG_FUZZ_SEED", "20260914"))
N_SCHEMAS = int(os.environ.get("ZG_FUZZ_DIFF", "600"))
MAX_STEPS = 20

_CLASS_BY_STATUS = {
    zg_ctypes.ZG_ERR_INVALID_SCHEMA: "InvalidSchema",
    zg_ctypes.ZG_ERR_UNSUPPORTED_FEATURE: "UnsupportedFeature",
    zg_ctypes.ZG_ERR_UNSATISFIABLE_CONSTRAINT: "UnsatisfiableConstraint",
    zg_ctypes.ZG_ERR_RESOURCE_LIMIT: "ResourceLimit",
}
_ORACLE_CLASSES = {
    "InvalidSchema", "UnsupportedFeature", "UnsatisfiableConstraint", "ResourceLimit",
}


def oracle_class(schema_text: str):
    """-> ('ok', Language) | (error class name, None)."""
    try:
        lang = ref.compile_schema_text(schema_text)
        return "ok", lang
    except (ref.InvalidSchema, ref.UnsupportedFeature,
            ref.UnsatisfiableConstraint, ref.ResourceLimit) as e:
        return type(e).__name__, None


def kernel_compile(ctx, schema_text: str):
    """-> (status, grammar|None)."""
    try:
        gh = zg_ctypes.compile_schema(ctx, schema_text)
        return zg_ctypes.ZG_OK, gh
    except zg_ctypes.CoreFailure as e:
        return e.status, None


def describe_mask(ids, spec):
    return sorted(ids)


def differential_walk(ctx, spec, schema_text, lang, gh, seed, counters):
    rng = random.Random(seed)
    oracle = ref.Matcher(lang)
    session = zg_ctypes.Session(ctx, gh)
    prefix = []
    try:
        for step in range(MAX_STEPS):
            status, words = session.fill_mask_words()
            omask = oracle.mask(b"", spec)
            if status == zg_ctypes.ZG_ERR_DEAD_END:
                if omask:
                    path = fc.save_artifact("deadend_vs_oracle", {
                        "seed": seed, "schema": schema_text, "prefix": prefix,
                        "oracle_mask": describe_mask(omask, spec)})
                    pytest.fail(f"kernel DEAD_END but oracle allows {len(omask)} tokens; repro {path}")
                counters["dead_ends"] += 1
                return
            if status != zg_ctypes.ZG_OK:
                path = fc.save_artifact("unexpected_fill_status", {
                    "seed": seed, "schema": schema_text, "prefix": prefix, "status": status})
                pytest.fail(f"fill_mask status {status}; repro {path}")
            kmask = fc.ids_from_words(words, spec.vocab_size)
            counters["mask_cmps"] += 1
            if kmask != omask:
                path = fc.save_artifact("mask_mismatch", {
                    "seed": seed, "schema": schema_text, "prefix": prefix, "step": step,
                    "kernel_only": describe_mask(kmask - omask, spec),
                    "oracle_only": describe_mask(omask - kmask, spec)})
                pytest.fail(f"mask mismatch at step {step}; repro {path}")
            k_ce = session.can_end()
            o_ce = oracle.can_end()
            if k_ce != o_ce:
                path = fc.save_artifact("can_end_mismatch", {
                    "seed": seed, "schema": schema_text, "prefix": prefix,
                    "kernel": k_ce, "oracle": o_ce})
                pytest.fail(f"can_end mismatch; repro {path}")

            eos = spec.eos_ids[0]
            ordinary = sorted(kmask - {eos})
            action = rng.random()
            if not ordinary and eos in kmask:
                action = 0.95  # EOS only
            if action < 0.70 and ordinary:
                tok = ordinary[rng.randrange(len(ordinary))]
                st = session.accept(tok)
                if st == zg_ctypes.ZG_ERR_RESOURCE_LIMIT:
                    # the reference must confirm the threads overflow
                    try:
                        oracle.feed(spec.tokens[tok])
                        path = fc.save_artifact("rl_vs_oracle", {
                            "seed": seed, "schema": schema_text, "prefix": prefix, "token": tok})
                        pytest.fail(f"kernel RESOURCE_LIMIT but oracle feeds fine; repro {path}")
                    except ref.ResourceLimit:
                        return
                if st != zg_ctypes.ZG_OK:
                    path = fc.save_artifact("allowed_rejected", {
                        "seed": seed, "schema": schema_text, "prefix": prefix,
                        "token": tok, "status": st})
                    pytest.fail(f"allowed token {tok} rejected with {st}; repro {path}")
                if not oracle.feed(spec.tokens[tok]):
                    path = fc.save_artifact("oracle_rejects_allowed", {
                        "seed": seed, "schema": schema_text, "prefix": prefix, "token": tok})
                    pytest.fail(f"oracle rejects kernel-allowed token {tok}; repro {path}")
                prefix.append(tok)
                counters["accepts"] += 1
            elif action < 0.82:
                # forbidden token: INVALID_TOKEN, state unchanged
                disallowed = [i for i in range(spec.vocab_size)
                              if i not in kmask and i not in spec.special_ids]
                if not disallowed:
                    continue
                tok = disallowed[rng.randrange(len(disallowed))]
                st = session.accept(tok)
                if st != zg_ctypes.ZG_ERR_INVALID_TOKEN:
                    path = fc.save_artifact("disallowed_status", {
                        "seed": seed, "schema": schema_text, "prefix": prefix,
                        "token": tok, "status": st})
                    pytest.fail(f"disallowed token {tok} gave status {st}; repro {path}")
                probe = oracle.clone()
                if tok != eos and probe.feed(spec.tokens[tok]):
                    path = fc.save_artifact("oracle_accepts_disallowed", {
                        "seed": seed, "schema": schema_text, "prefix": prefix, "token": tok})
                    pytest.fail(f"oracle accepts kernel-disallowed token {tok}; repro {path}")
                st2, words2 = session.fill_mask_words()
                if st2 != zg_ctypes.ZG_OK or words2 != words:
                    path = fc.save_artifact("partial_accept", {
                        "seed": seed, "schema": schema_text, "prefix": prefix, "token": tok})
                    pytest.fail(f"state changed after rejected token {tok}; repro {path}")
                counters["rejects"] += 1
            elif action < 0.90 and eos in kmask:
                st = session.accept(eos)
                if st != zg_ctypes.ZG_OK:
                    path = fc.save_artifact("eos_rejected", {
                        "seed": seed, "schema": schema_text, "prefix": prefix, "status": st})
                    pytest.fail(f"allowed EOS rejected with {st}; repro {path}")
                if session.finish() != zg_ctypes.ZG_OK:
                    path = fc.save_artifact("finish_failed", {
                        "seed": seed, "schema": schema_text, "prefix": prefix})
                    pytest.fail(f"finish after EOS failed; repro {path}")
                doc = b"".join(spec.tokens[t] for t in prefix)
                # canonical_check (Matcher) — exact membership check;
                # validate_document is unsuitable: json.loads yields inf for
                # huge exponents and the mini-validator rejects those.
                if not ref.canonical_check(doc, lang):
                    path = fc.save_artifact("invalid_document", {
                        "seed": seed, "schema": schema_text, "prefix": prefix,
                        "doc": doc.decode("utf-8", "replace")})
                    pytest.fail(f"finished document not in language; repro {path}")
                counters["finishes"] += 1
                return
            else:
                if k_ce and rng.random() < 0.5:
                    if session.finish() != zg_ctypes.ZG_OK:
                        path = fc.save_artifact("early_finish_failed", {
                            "seed": seed, "schema": schema_text, "prefix": prefix})
                        pytest.fail(f"finish with can_end=true failed; repro {path}")
                    counters["finishes"] += 1
                    return
                # otherwise a step without accept: the repeated mask must match
                st2, words2 = session.fill_mask_words()
                if st2 != zg_ctypes.ZG_OK or words2 != words:
                    path = fc.save_artifact("nondet_mask", {
                        "seed": seed, "schema": schema_text, "prefix": prefix})
                    pytest.fail(f"mask not deterministic; repro {path}")
    finally:
        session.destroy()


def test_differential_fuzz():
    spec = fc.make_fuzz_tokenizer()
    ctx = zg_ctypes.Context(spec, zg_ctypes.ZG_MODE_LAZY)
    rng = random.Random(SEED)
    counters = {"schemas": 0, "both_rejected": 0, "walks": 0, "mask_cmps": 0,
                "accepts": 0, "rejects": 0, "dead_ends": 0, "finishes": 0}
    try:
        for i in range(N_SCHEMAS):
            seed = rng.getrandbits(48)
            srng = random.Random(seed)
            schema_text = schema_gen.gen_schema(srng, must_compile=srng.random() < 0.75)
            counters["schemas"] += 1
            if i % 10 == 0:
                fc.save_corpus_schema(schema_text)
            status, gh = kernel_compile(ctx, schema_text)
            oclass, lang = oracle_class(schema_text)
            kclass = "ok" if status == zg_ctypes.ZG_OK else _CLASS_BY_STATUS.get(status, f"status_{status}")
            if kclass == "ResourceLimit":
                # kernel and reference limits need not agree — skip the schema
                continue
            if kclass != oclass:
                path = fc.save_artifact("classification_mismatch", {
                    "seed": seed, "schema": schema_text,
                    "kernel": kclass, "oracle": oclass})
                pytest.fail(f"classification mismatch kernel={kclass} oracle={oclass}; repro {path}")
            if kclass != "ok":
                counters["both_rejected"] += 1
                continue
            counters["walks"] += 1
            differential_walk(ctx, spec, schema_text, lang, gh, seed, counters)
            zg_ctypes.grammar_release(gh)
    finally:
        ctx.destroy()
    counters["seed"] = SEED
    fc.save_results("py_fuzz_differential.json", counters)
    print(f"\n[T4 differential] {counters}")
