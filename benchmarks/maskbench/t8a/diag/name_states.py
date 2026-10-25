#!/usr/bin/env python3
"""Full masks at property-name states: fast path on vs off, same vocabulary.

The mask of a key state is what decides the next property name, so it is
measured where names are actually typed, not on a synthetic string. A case
is a schema, a prefix and its expectations. The prefix is fed through the
run's tokenizer and through a byte-complete vocabulary; the mask is taken
with the fast path off and on in both, and the pairs are compared bit for
bit. The byte vocabulary also carries tokens that spell a closing quote and
a colon, because those are the tokens a name state has to judge exactly.

A case fails when the prefix is not accepted in every leg, when an
expected mask outcome (a resource limit) does not occur in every leg - the
fast path may only fall back, never change the outcome -, when a mask is
missing unexpectedly, when the two fast-path settings disagree, or when the
fast path does not meet its expectation. `--expect-fast before` (default)
requires the exact path everywhere, `--expect-fast after` requires it for the
cases marked `fast_after_k1` through the `name_used` counter (so a
string-lemma hit cannot satisfy it) and keeps the rest exact.

usage:
  python3 name_states.py ZIG_ROOT [--case NAME] [--json OUT]
                        [--expect-fast before|after]

  ZIG_ROOT  tree whose zig-out/lib/libbolorgir.so.0.1.0 is loaded through
            LD_PRELOAD (the engine is the artifact, not an installed wheel)
"""
import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[4]
DATA = ROOT / "benchmarks/external/jsonschemabench-maskbench/maskbench/data"
PYTHON = ROOT / "benchmarks/local/t8a-work-9a5baf3e/venv-pinned/bin/python"
TOKDIR = ROOT / ("benchmarks/local/hf-cache/hub/"
                 "models--unsloth--Meta-Llama-3.1-8B-Instruct/snapshots/"
                 "a2856192dd7c25b842431f39c179a6c2c2f627d1")

# Byte-complete vocabulary plus multi-byte pieces that cross the closing
# quote and the colon; ids 0..255 are the single bytes.
VOCAB = ([bytes([n]) for n in range(256)] +
         [b'":', b'a":', b'x":', b'"', b'a"', b'"x', b'title":', b'options":'])

# Corpus schemas at the states the probe found slow, plus the shapes of
# interest: declared and reused names, propertyNames, patternProperties,
# additionalProperties, object capacity and the closing-quote/colon crossings.
# `fast_after_k1` says whether lemma K1 is expected to cover the state.
MAXPROPS = {"type": "object", "properties": {"a": {"type": "integer"}},
            "required": ["a"], "additionalProperties": True}

CASES = [
    # open object without capacity or name constraint: the target of K1
    {"name": "o34343-open-at-start", "source": "corpus:o34343",
     "prefix": '{"', "fast_after_k1": True},
    {"name": "o34343-open-declared-options", "source": "corpus:o34343",
     "prefix": '{"options', "fast_after_k1": True},
    {"name": "o34343-open-free-x", "source": "corpus:o34343",
     "prefix": '{"x', "fast_after_k1": True},
    {"name": "o64963-open-at-start", "source": "corpus:o64963",
     "prefix": '{"', "fast_after_k1": True},
    {"name": "o64963-open-declared-class", "source": "corpus:o64963",
     "prefix": '{"class', "fast_after_k1": True},
    # additionalProperties: false - only the next declared key, in order
    {"name": "o21292-closed-at-start", "source": "corpus:o21292",
     "prefix": '{"', "fast_after_k1": False},
    {"name": "o21292-closed-declared-a", "source": "corpus:o21292",
     "prefix": '{"a', "fast_after_k1": False},
    {"name": "o21292-closed-declared-analytics", "source": "corpus:o21292",
     "prefix": '{"analytics_', "fast_after_k1": False},
    # synthetic shapes
    {"name": "synthetic-any-name", "source": {"type": "object"},
     "prefix": '{"ab', "fast_after_k1": True},
    {"name": "synthetic-closed-names",
     "source": {"type": "object", "additionalProperties": False,
                "properties": {"alpha": {"type": "integer"},
                               "beta": {"type": "string"}}},
     "prefix": '{"al', "fast_after_k1": False},
    {"name": "synthetic-propertyNames",
     "source": {"type": "object",
                "propertyNames": {"pattern": "^[a-z]{1,4}$"},
                "additionalProperties": {"type": "integer"}},
     "prefix": '{"ab', "fast_after_k1": False,
     "error": "ResourceLimitError"},
    {"name": "synthetic-patternProperties",
     "source": {"type": "object",
                "patternProperties": {"^x[0-9]$": {"type": "integer"}},
                "additionalProperties": False},
     "prefix": '{"x1', "fast_after_k1": False},
    # a name already used once: closed objects skip it, open ones still offer
    # free names
    {"name": "synthetic-reuse-closed",
     "source": {"type": "object", "additionalProperties": False,
                "properties": {"alpha": {"type": "integer"},
                               "beta": {"type": "string"}},
                "required": ["alpha"]}, "prefix": '{"alpha":1,"',
     "fast_after_k1": False},
    {"name": "synthetic-reuse-open",
     "source": {"type": "object",
                "properties": {"alpha": {"type": "integer"}}},
     "prefix": '{"alpha":1,"', "fast_after_k1": True},
    # object capacity: a free name cannot be typed although the object is open
    {"name": "synthetic-maxProperties-at-start",
     "source": dict(MAXPROPS, maxProperties=1), "prefix": '{"',
     "fast_after_k1": False},
    {"name": "synthetic-maxProperties-inside",
     "source": dict(MAXPROPS, maxProperties=1), "prefix": '{"a',
     "fast_after_k1": False},
    # the same schema without the cap is in scope
    {"name": "synthetic-no-cap-at-start", "source": MAXPROPS,
     "prefix": '{"', "fast_after_k1": True},
    {"name": "synthetic-no-cap-inside", "source": MAXPROPS,
     "prefix": '{"a', "fast_after_k1": True},
]

CORPUS = {
    "o34343": "Github_hard---o34343.json",
    "o64963": "Github_hard---o64963.json",
    "o21292": "Github_hard---o21292.json",
}


def schema_for(case):
    source = case["source"]
    if isinstance(source, dict):
        return source
    _, key = source.split(":", 1)
    with open(DATA / CORPUS[key], encoding="utf-8") as f:
        return json.load(f)["schema"]


def child():
    import ctypes
    import time

    import bolorgir as b

    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    if spec.get("real"):
        # the vocabulary the run uses: the timing only means something there
        from transformers import AutoTokenizer
        hf = AutoTokenizer.from_pretrained(str(TOKDIR))
        tok = b.TokenizerBundle.from_hf(hf)
        ids = hf.encode(spec["prefix_text"], add_special_tokens=False)
    else:
        tok = b.TokenizerBundle.from_token_bytes(VOCAB)
        ids = list(bytes.fromhex(spec["prefix_hex"]))
    lib = ctypes.CDLL(None)
    lib.blg_diag_counters.argtypes = [ctypes.POINTER(ctypes.c_uint64),
                                      ctypes.c_size_t]
    lib.blg_diag_counters.restype = ctypes.c_size_t
    lib.blg_diag_reset()
    with b.Engine(tokenizer=tok, mode="adaptive") as e:
        c = e.compile(spec["schema"], tok, profile="spec-v1")
        s = c.create_session()
        # `accept_token` refuses by raising, not by a return value
        accepted = True
        try:
            for tid in ids:
                s.accept_token(tid)
        except Exception:
            accepted = False
        t0 = time.monotonic()
        err = None
        mask = None
        try:
            mask = s.fill_mask()
        except Exception as exc:
            # a refusal is an outcome: the mask did not fit the budget
            err = type(exc).__name__
        mask_us = (time.monotonic() - t0) * 1e6
        # positional layout of src/diag.zig: name_used and
        # refuse_name_state are appended after exact_tokens
        counters = (ctypes.c_uint64 * 18)()
        lib.blg_diag_counters(counters, 18)
        s.close()
        c.close()
    row = {"prefix_accepted": accepted,
           "mask_us": round(mask_us, 1),
           "fast_used": counters[2],
           "refuse_not_uniform": counters[3],
           "refuse_content_dep": counters[5],
           "state_alive_calls": counters[9],
           "trie_nodes": counters[7],
           "name_used": counters[16],
           "refuse_name_state": counters[17]}
    if err:
        row["error"] = err
    else:
        row["mask_hex"] = mask.hex()
        row["allowed"] = int.from_bytes(mask, "little").bit_count()
    print(json.dumps(row))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--case", action="append", default=[])
    ap.add_argument("--json", default="")
    ap.add_argument("--expect-fast", choices=("before", "after"),
                    default="before")
    args = ap.parse_args()
    root = pathlib.Path(args.root).resolve()
    lib = root / "zig-out/lib/libbolorgir.so.0.1.0"
    digest = hashlib.sha256(lib.read_bytes()).hexdigest()
    cases = [c for c in CASES if not args.case or c["name"] in args.case]
    if not cases:
        raise SystemExit("no cases selected")

    def run_child(spec, path, flag):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        env = os.environ.copy()
        # relative on purpose: the tree path contains spaces and LD_PRELOAD
        # splits on them
        env.update(BLG_MASK_FAST_PATH=flag,
                   LD_PRELOAD="./zig-out/lib/libbolorgir.so.0.1.0",
                   PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTHONPATH", None)
        proc = subprocess.run(
            [str(PYTHON), str(pathlib.Path(__file__).resolve()), path,
             "--child"],
            cwd=root, env=env, text=True, capture_output=True, timeout=300,
            check=False)
        if proc.returncode != 0:
            raise SystemExit(f"child failed: {proc.stderr.strip()[-800:]}")
        return json.loads(proc.stdout)

    rows, failures = [], []

    def fail(name, why):
        failures.append(f"{name}: {why}")

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "case.json")
        for case in cases:
            name, prefix = case["name"], case["prefix"]
            base = {"schema": schema_for(case),
                    "prefix_hex": prefix.encode().hex(),
                    "prefix_text": prefix}
            legs = {
                ("byte", "0"): run_child(base, path, "0"),
                ("byte", "1"): run_child(base, path, "1"),
                ("real", "0"): run_child(dict(base, real=True), path, "0"),
                ("real", "1"): run_child(dict(base, real=True), path, "1"),
            }
            expected_error = case.get("error")
            errors = {k: v.get("error") for k, v in legs.items()}
            accepted = all(v["prefix_accepted"] for v in legs.values())
            if not accepted:
                fail(name, "prefix not accepted in every leg")
            row = {
                "case": name, "prefix": prefix,
                "equal_byte": None, "equal_real": None,
                "expected_error": expected_error,
                "errors": {f"{k[0]}/{k[1]}": v for k, v in errors.items()},
                "prefix_accepted": accepted,
                "allowed_byte_exact": legs[("byte", "0")].get("allowed"),
                "allowed_real": legs[("real", "1")].get("allowed"),
                "mask_us_byte": legs[("byte", "0")]["mask_us"],
                "mask_us_real": legs[("real", "1")]["mask_us"],
                "fast_used_byte": legs[("byte", "1")]["fast_used"],
                "fast_used_real": legs[("real", "1")]["fast_used"],
                "name_used_byte": legs[("byte", "1")]["name_used"],
                "name_used_real": legs[("real", "1")]["name_used"],
                "refuse_name_state_real":
                    legs[("real", "1")]["refuse_name_state"],
                "refuse_content_dep_real":
                    legs[("real", "1")]["refuse_content_dep"],
                "state_alive_real": legs[("real", "1")]["state_alive_calls"],
                "trie_nodes_real": legs[("real", "1")]["trie_nodes"],
                "fast_after_k1": bool(case.get("fast_after_k1")),
            }
            if expected_error:
                # the outcome is part of the contract: the fast path may fall
                # back to the exact walk, never change what the mask reports
                for key in (("byte", "0"), ("byte", "1"),
                            ("real", "0"), ("real", "1")):
                    if errors[key] != expected_error:
                        fail(name, f"{key[0]}/{key[1]} leg: expected "
                                   f"{expected_error}, got {errors[key]}")
            elif any(errors.values()):
                fail(name, f"unexpected mask error {errors}")
            else:
                for family in ("byte", "real"):
                    a = bytes.fromhex(legs[(family, "0")]["mask_hex"])
                    bmask = bytes.fromhex(legs[(family, "1")]["mask_hex"])
                    row["equal_" + family] = a == bmask
                    if a != bmask:
                        fail(name, f"{family} masks differ between the legs")
                    if family == "byte":
                        row["extra"] = [
                            VOCAB[i].decode("latin-1")
                            for i in range(len(VOCAB))
                            if bmask[i // 8] & (1 << (i % 8))
                            and not a[i // 8] & (1 << (i % 8))]
                        row["missing"] = [
                            VOCAB[i].decode("latin-1")
                            for i in range(len(VOCAB))
                            if a[i // 8] & (1 << (i % 8))
                            and not bmask[i // 8] & (1 << (i % 8))]
            want_fast = (bool(case.get("fast_after_k1", False))
                         if args.expect_fast == "after" else False)
            row["want_fast"] = want_fast
            got_byte = row["fast_used_byte"] > 0
            got_real = row["fast_used_real"] > 0
            if want_fast:
                # the marked cases are name states, so the string lemma cannot
                # be what fired: require the K1 counter in both vocabularies
                k1_byte = row["name_used_byte"] > 0
                k1_real = row["name_used_real"] > 0
                if not (k1_byte and k1_real):
                    fail(name, f"K1 must fire, byte {k1_byte}, "
                               f"real {k1_real}")
            elif got_byte or got_real:
                fail(name, f"fast path must not fire, byte {got_byte}, "
                           f"real {got_real}")
            rows.append(row)

    bad = {f.split(":", 1)[0] for f in failures}
    print(f"hits {len(rows)} cases on core {digest} "
          f"(expect-fast {args.expect_fast})")
    print(f"{'case':36s} {'byte':>4s} {'real':>4s} {'fast':>4s} "
          f"{'k1':>4s} {'allowed':>8s} {'real ms':>8s} {'alive':>7s} "
          f"{'nodes':>7s}")

    def mark(value):
        if value is None:
            return "-"
        return "yes" if value else "NO"

    for r in rows:
        status = "FAIL" if r["case"] in bad else "ok"
        print(f"{r['case']:36s} {mark(r['equal_byte']):>4s} "
              f"{mark(r['equal_real']):>4s} {r['fast_used_real']:4d} "
              f"{r['name_used_real']:4d} {r['allowed_real'] or 0:8d} "
              f"{r['mask_us_real'] / 1e3:8.1f} "
              f"{r['state_alive_real']:7d} {r['trie_nodes_real']:7d} {status}")
        if r.get("extra") or r.get("missing"):
            print(f"   extra {r.get('extra')} missing {r.get('missing')}")
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for line in failures:
            print(f"  {line}")
    payload = {"core_sha256": digest, "expect_fast": args.expect_fast,
               "vocab": [v.decode("latin-1") for v in VOCAB],
               "cases": rows, "failures": failures}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
    return 0 if not failures else 1


if __name__ == "__main__":
    if sys.argv[1:] and sys.argv[-1] == "--child":
        child()
    else:
        raise SystemExit(main())
