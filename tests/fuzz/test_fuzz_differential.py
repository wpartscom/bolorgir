"""T4: differential campaign - kernel (ctypes) vs tests/reference.py.

For each random schema:
  1. Compilation classification: kernel (BLG_OK/INVALID_SCHEMA/
     UNSUPPORTED_FEATURE/UNSATISFIABLE_CONSTRAINT/RESOURCE_LIMIT) is compared
     with the reference exception class (InvalidSchema/UnsupportedFeature/
     UnsatisfiableConstraint).
  2. If both sides accept the schema - random walk: at each step the kernel
     mask == the Matcher reference mask, can_end agrees; an allowed token is
     accepted by both sides, a forbidden one is rejected with INVALID_TOKEN
     and does not change state (the mask afterwards is bitwise identical);
     kernel DEAD_END corresponds to an empty reference mask; finish is only
     possible at can_end, and the final document is valid per
     validate_document.

Each generated schema and its walk seed are replayed in the four
configurations Decision 4 names - cache off/on (lazy/adaptive) x fast path
on/off - and every configuration publishes its own counters and a digest of
the executed trace (accepted/rejected tokens, finishes, dead ends). The
counters and trace digests must be identical across configurations, so the
same schemas and the same trace sequence are compared in every declared
configuration. Workers stay at the context default here; the curated suite
covers max_workers 1/2/8.

Any divergence -> reproducer in tests/fuzz/artifacts/ + test failure.
Scale: BLG_FUZZ_DIFF (default 600 schemas), seed: BLG_FUZZ_SEED.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fuzz_common as fc
import reference as ref
import schema_gen
import blg_ctypes

pytest.importorskip("blg_ctypes")

SEED = int(os.environ.get("BLG_FUZZ_SEED", "20260914"))
N_SCHEMAS = int(os.environ.get("BLG_FUZZ_DIFF", "600"))
MAX_STEPS = 20

# The four configurations Decision 4 names for this campaign: cache off/on
# (lazy/adaptive) crossed with the fast path on/off. The default fast path
# value is 0 (= on); ON/OFF are passed explicitly so a changed default
# cannot silently drop a configuration.
CONFIGS = (
    ("cache_off-fast_on", {"mode": blg_ctypes.BLG_MODE_LAZY,
                            "cache_limit_bytes": 0,
                            "mask_fast_path": blg_ctypes.BLG_MASK_FAST_PATH_ON}),
    ("cache_off-fast_off", {"mode": blg_ctypes.BLG_MODE_LAZY,
                             "cache_limit_bytes": 0,
                             "mask_fast_path": blg_ctypes.BLG_MASK_FAST_PATH_OFF}),
    ("cache_default-fast_on", {"mode": blg_ctypes.BLG_MODE_ADAPTIVE,
                                "cache_limit_bytes": blg_ctypes.BLG_CACHE_DEFAULT,
                                "mask_fast_path": blg_ctypes.BLG_MASK_FAST_PATH_ON}),
    ("cache_default-fast_off", {"mode": blg_ctypes.BLG_MODE_ADAPTIVE,
                                 "cache_limit_bytes": blg_ctypes.BLG_CACHE_DEFAULT,
                                 "mask_fast_path": blg_ctypes.BLG_MASK_FAST_PATH_OFF}),
)

_CLASS_BY_STATUS = {
    blg_ctypes.BLG_ERR_INVALID_SCHEMA: "InvalidSchema",
    blg_ctypes.BLG_ERR_UNSUPPORTED_FEATURE: "UnsupportedFeature",
    blg_ctypes.BLG_ERR_UNSATISFIABLE_CONSTRAINT: "UnsatisfiableConstraint",
    blg_ctypes.BLG_ERR_RESOURCE_LIMIT: "ResourceLimit",
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
        gh = blg_ctypes.compile_schema(ctx, schema_text)
        return blg_ctypes.BLG_OK, gh
    except blg_ctypes.CoreFailure as e:
        return e.status, None


def describe_mask(ids, spec):
    return sorted(ids)


def differential_walk(ctx, spec, schema_text, lang, gh, seed, counters,
                      label="default", trace=None):
    rng = random.Random(seed)
    oracle = ref.Matcher(lang)
    session = blg_ctypes.Session(ctx, gh)
    prefix = []
    if trace is None:
        trace = []

    def save(kind, payload):
        return fc.save_artifact(kind, dict(payload, config=label))

    try:
        for step in range(MAX_STEPS):
            status, words = session.fill_mask_words()
            omask = oracle.mask(b"", spec)
            if status == blg_ctypes.BLG_ERR_DEAD_END:
                if omask:
                    path = save("deadend_vs_oracle", {
                        "seed": seed, "schema": schema_text, "prefix": prefix,
                        "oracle_mask": describe_mask(omask, spec)})
                    pytest.fail(f"kernel DEAD_END but oracle allows {len(omask)} tokens; repro {path}")
                counters["dead_ends"] += 1
                trace.append(("dead",))
                return
            if status != blg_ctypes.BLG_OK:
                path = save("unexpected_fill_status", {
                    "seed": seed, "schema": schema_text, "prefix": prefix, "status": status})
                pytest.fail(f"fill_mask status {status}; repro {path}")
            kmask = fc.ids_from_words(words, spec.vocab_size)
            counters["mask_cmps"] += 1
            if kmask != omask:
                path = save("mask_mismatch", {
                    "seed": seed, "schema": schema_text, "prefix": prefix, "step": step,
                    "kernel_only": describe_mask(kmask - omask, spec),
                    "oracle_only": describe_mask(omask - kmask, spec)})
                pytest.fail(f"mask mismatch at step {step}; repro {path}")
            k_ce = session.can_end()
            o_ce = oracle.can_end()
            if k_ce != o_ce:
                path = save("can_end_mismatch", {
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
                if st == blg_ctypes.BLG_ERR_RESOURCE_LIMIT:
                    # the reference must confirm the threads overflow
                    try:
                        oracle.feed(spec.tokens[tok])
                        path = save("rl_vs_oracle", {
                            "seed": seed, "schema": schema_text, "prefix": prefix, "token": tok})
                        pytest.fail(f"kernel RESOURCE_LIMIT but oracle feeds fine; repro {path}")
                    except ref.ResourceLimit:
                        trace.append(("rl",))
                        return
                if st != blg_ctypes.BLG_OK:
                    path = save("allowed_rejected", {
                        "seed": seed, "schema": schema_text, "prefix": prefix,
                        "token": tok, "status": st})
                    pytest.fail(f"allowed token {tok} rejected with {st}; repro {path}")
                if not oracle.feed(spec.tokens[tok]):
                    path = save("oracle_rejects_allowed", {
                        "seed": seed, "schema": schema_text, "prefix": prefix, "token": tok})
                    pytest.fail(f"oracle rejects kernel-allowed token {tok}; repro {path}")
                prefix.append(tok)
                counters["accepts"] += 1
                trace.append(("a", tok))
            elif action < 0.82:
                # forbidden token: INVALID_TOKEN, state unchanged
                disallowed = [i for i in range(spec.vocab_size)
                              if i not in kmask and i not in spec.special_ids]
                if not disallowed:
                    trace.append(("skip",))
                    continue
                tok = disallowed[rng.randrange(len(disallowed))]
                st = session.accept(tok)
                if st != blg_ctypes.BLG_ERR_INVALID_TOKEN:
                    path = save("disallowed_status", {
                        "seed": seed, "schema": schema_text, "prefix": prefix,
                        "token": tok, "status": st})
                    pytest.fail(f"disallowed token {tok} gave status {st}; repro {path}")
                probe = oracle.clone()
                if tok != eos and probe.feed(spec.tokens[tok]):
                    path = save("oracle_accepts_disallowed", {
                        "seed": seed, "schema": schema_text, "prefix": prefix, "token": tok})
                    pytest.fail(f"oracle accepts kernel-disallowed token {tok}; repro {path}")
                st2, words2 = session.fill_mask_words()
                if st2 != blg_ctypes.BLG_OK or words2 != words:
                    path = save("partial_accept", {
                        "seed": seed, "schema": schema_text, "prefix": prefix, "token": tok})
                    pytest.fail(f"state changed after rejected token {tok}; repro {path}")
                counters["rejects"] += 1
                trace.append(("r", tok))
            elif action < 0.90 and eos in kmask:
                st = session.accept(eos)
                if st != blg_ctypes.BLG_OK:
                    path = save("eos_rejected", {
                        "seed": seed, "schema": schema_text, "prefix": prefix, "status": st})
                    pytest.fail(f"allowed EOS rejected with {st}; repro {path}")
                if session.finish() != blg_ctypes.BLG_OK:
                    path = save("finish_failed", {
                        "seed": seed, "schema": schema_text, "prefix": prefix})
                    pytest.fail(f"finish after EOS failed; repro {path}")
                doc = b"".join(spec.tokens[t] for t in prefix)
                # canonical_check (Matcher) - exact membership check;
                # validate_document is unsuitable: json.loads yields inf for
                # huge exponents and the mini-validator rejects those.
                if not ref.canonical_check(doc, lang):
                    path = save("invalid_document", {
                        "seed": seed, "schema": schema_text, "prefix": prefix,
                        "doc": doc.decode("utf-8", "replace")})
                    pytest.fail(f"finished document not in language; repro {path}")
                counters["finishes"] += 1
                trace.append(("eos",))
                return
            else:
                if k_ce and rng.random() < 0.5:
                    if session.finish() != blg_ctypes.BLG_OK:
                        path = save("early_finish_failed", {
                            "seed": seed, "schema": schema_text, "prefix": prefix})
                        pytest.fail(f"finish with can_end=true failed; repro {path}")
                    counters["finishes"] += 1
                    trace.append(("end",))
                    return
                # otherwise a step without accept: the repeated mask must match
                st2, words2 = session.fill_mask_words()
                if st2 != blg_ctypes.BLG_OK or words2 != words:
                    path = save("nondet_mask", {
                        "seed": seed, "schema": schema_text, "prefix": prefix})
                    pytest.fail(f"mask not deterministic; repro {path}")
                trace.append(("n",))
    finally:
        session.destroy()


def test_differential_fuzz():
    spec = fc.make_fuzz_tokenizer()
    contexts = [(name, blg_ctypes.Context(spec, **kw))
                for name, kw in CONFIGS]
    rng = random.Random(SEED)
    counters = {name: {"schemas": 0, "both_rejected": 0, "walks": 0,
                       "mask_cmps": 0, "accepts": 0, "rejects": 0,
                       "dead_ends": 0, "finishes": 0}
                for name, _ in CONFIGS}
    traces = {name: [] for name, _ in CONFIGS}
    try:
        for i in range(N_SCHEMAS):
            seed = rng.getrandbits(48)
            srng = random.Random(seed)
            schema_text = schema_gen.gen_schema(srng, must_compile=srng.random() < 0.75)
            if i % 10 == 0:
                fc.save_corpus_schema(schema_text)
            oclass, lang = oracle_class(schema_text)
            for name, ctx in contexts:
                c = counters[name]
                c["schemas"] += 1
                status, gh = kernel_compile(ctx, schema_text)
                kclass = "ok" if status == blg_ctypes.BLG_OK else _CLASS_BY_STATUS.get(status, f"status_{status}")
                if kclass == "ResourceLimit":
                    # kernel and reference limits need not agree - skip the schema
                    continue
                if kclass != oclass:
                    path = fc.save_artifact("classification_mismatch", {
                        "config": name, "seed": seed, "schema": schema_text,
                        "kernel": kclass, "oracle": oclass})
                    pytest.fail(f"[{name}] classification mismatch "
                                f"kernel={kclass} oracle={oclass}; repro {path}")
                if kclass != "ok":
                    c["both_rejected"] += 1
                    continue
                c["walks"] += 1
                differential_walk(ctx, spec, schema_text, lang, gh, seed, c,
                                  label=name, trace=traces[name])
                blg_ctypes.grammar_release(gh)
    finally:
        for _, ctx in contexts:
            ctx.destroy()

    rows = []
    for name, kw in CONFIGS:
        row = dict(counters[name])
        row.update({
            "config": name,
            "mode": kw["mode"],
            "cache_limit_bytes": kw["cache_limit_bytes"],
            "mask_fast_path": kw["mask_fast_path"],
            "max_workers": 0,
            "trace_sha256": hashlib.sha256(
                json.dumps(traces[name], separators=(",", ":")).encode(
                    "utf-8")).hexdigest()})
        rows.append(row)
    base = {k: rows[0][k] for k in counters[CONFIGS[0][0]]}
    diverged = [r["config"] for r in rows
                if {k: r[k] for k in base} != base or
                r["trace_sha256"] != rows[0]["trace_sha256"]]
    result = {"seed": SEED, "n_schemas": N_SCHEMAS, "max_steps": MAX_STEPS,
              "configs": rows, "equal_across_configs": not diverged}
    fc.save_results("py_fuzz_differential.json", result)
    print(f"\n[T4 differential] {json.dumps(result, indent=1)}")
    if diverged:
        path = fc.save_artifact("config_divergence", {
            "base": rows[0],
            "diverged": [r for r in rows if r["config"] in diverged]})
        pytest.fail(f"per-configuration counters or traces diverge: "
                    f"{diverged}; repro {path}")
