#!/usr/bin/env python3
"""export_specs.py RUN OUT: the two pinned HF tokenizers of the R6 run as
the run loaded them - bench_jsb_coverage.hf_tokenizer_spec of the run's
tree (RUN/tree: its harness and its bolorgir package, built on the run's
library) from the run's snapshot (RUN/hf-cache, offline) - written to OUT
as JSON (per key: name, revision, the token bytes in hex, eos_ids,
special_ids, vocab_size, sha256 of the token list). R11-A replays read
this file instead of importing the package, so that the library under
test is the only libbolorgir in a replay process and every library gets
the same vocabulary. Run with the run's Python, PYTHONPATH=RUN/tree/python,
HF_HOME=RUN/hf-cache, HF_HUB_OFFLINE=1, BLG_LIB_PATH=RUN/lib/...; writes
nothing in RUN. Exit 0, 1 a tokenizer did not load, 2 OUT exists."""
import hashlib
import json
import os
import sys

run, out = sys.argv[1], sys.argv[2]
if os.path.exists(out):
    sys.exit(f"refused: {out} exists")
os.environ.setdefault("BOLORGIR_COVERAGE_PROFILE", "spec-v1")
sys.path.insert(0, os.path.join(run, "tree", "benchmarks"))
import bench_jsb_coverage as b  # noqa: E402

doc = {"about": "pinned HF tokenizers of the R6 run, as hf_tokenizer_spec of the run's tree loaded them",
       "run": os.path.abspath(run), "harness_library_sha256": b.library_sha256(), "tokenizers": {}}
for key, pin in sorted(b.GEN_TOKENIZERS.items()):
    if pin is None:
        continue
    spec = b.hf_tokenizer_spec(*pin)
    h = hashlib.sha256()
    for t in spec.tokens:
        h.update(len(t).to_bytes(4, "little") + t)
    doc["tokenizers"][key] = {"name": pin[0], "revision": pin[1], "vocab_size": spec.vocab_size,
                              "eos_ids": list(spec.eos_ids), "special_ids": list(spec.special_ids),
                              "tokens_sha256": h.hexdigest(), "tokens_hex": [t.hex() for t in spec.tokens]}
    print(f"{key}: {pin[0]} {pin[1]} vocab {spec.vocab_size} tokens {h.hexdigest()[:16]}")
if len(doc["tokenizers"]) != 2:
    sys.exit(1)
with open(out, "w", encoding="utf-8") as f:
    json.dump(doc, f)
    f.write("\n")
