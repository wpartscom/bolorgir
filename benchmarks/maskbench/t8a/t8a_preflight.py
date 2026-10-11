#!/usr/bin/env python3
"""Inputs of a T8a run checked before any install or run (run_t8a.sh).

- the tokenizer snapshot: its files are exactly the files the T3
  acceptance pins for that revision (benchmarks/t3/acceptance_pins.json,
  `hf.files`), each with the pinned sha256 - the snapshot the coverage
  runs used, wherever HF_HOME points;
- with --acceptance (a full run): the T3 review verdict this run is bound
  to is `admitted` and names the core inside the wheel. After the owner's
  D4 decisions that is the acceptance.json of the later, admitted review
  directory, not the first, not-admitted one.

A full run without --acceptance is refused: its report could never be
accepted (t8a_report.py), and the refusal comes before hours of installs
and runs, not after them. Exit 0 ok, 1 refused (reasons on stderr).
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t8a_identity import sha256_file, tree_files, wheel_core  # noqa: E402

LLAMA_REPO = "hub/models--unsloth--Meta-Llama-3.1-8B-Instruct/snapshots"


def tokenizer_problems(tokdir, revision, pins):
    prefix = f"{LLAMA_REPO}/{revision}/"
    want = {k[len(prefix):]: v for k, v in pins.get("hf", {}).get("files", {}).items()
            if k.startswith(prefix)}
    if not want:
        return [f"no pinned files for the tokenizer revision {revision} in the acceptance pins"]
    if not os.path.isdir(tokdir):
        return [f"tokenizer snapshot {tokdir} missing"]
    got = tree_files(tokdir, ["*"])
    out = []
    for name in sorted(set(want) | set(got)):
        if name not in got:
            out.append(f"tokenizer snapshot: pinned file {name} missing")
        elif name not in want:
            out.append(f"tokenizer snapshot: {name} is not a pinned file")
        elif got[name] != want[name]:
            out.append(f"tokenizer snapshot: {name} is {got[name][:16]}, pinned {want[name][:16]}")
    return out


def acceptance_problems(path, core):
    if not path:
        return ["a full run needs T8A_ACCEPTANCE, the admitted T3 review verdict of this core "
                "(after D4 decisions the acceptance.json of the later review directory)"]
    try:
        with open(path, encoding="utf-8") as f:
            acc = json.load(f)
        verdict = acc["acceptance"]["verdict"]
        lib = acc["inputs"]["candidate"]["library_sha256"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [f"T8A_ACCEPTANCE {path}: not a T3 review verdict ({e})"]
    out = []
    if verdict != "admitted":
        out.append(f"T8A_ACCEPTANCE {path}: verdict {verdict}, not admitted")
    if lib != core:
        out.append(f"T8A_ACCEPTANCE {path}: candidate core {str(lib)[:16]} is not the wheel's {core[:16]}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wheel", required=True)
    ap.add_argument("--tokenizer-dir", required=True)
    ap.add_argument("--tokenizer-revision", required=True)
    ap.add_argument("--pins", required=True)
    ap.add_argument("--full", action="store_true", help="a full run: --acceptance is required")
    ap.add_argument("--acceptance", default="")
    args = ap.parse_args(argv)
    with open(args.pins, encoding="utf-8") as f:
        pins = json.load(f)
    bad = tokenizer_problems(args.tokenizer_dir, args.tokenizer_revision, pins)
    core, _ = wheel_core(args.wheel)
    if args.full or args.acceptance:
        bad += acceptance_problems(args.acceptance, core)
    for b in bad:
        print(f"t8a_preflight: {b}", file=sys.stderr)
    if not bad:
        print(f"t8a_preflight: ok (core {core[:16]}, tokenizer {args.tokenizer_revision[:12]}"
              f"{', ' + os.path.basename(os.path.dirname(args.acceptance)) + ' admitted' if args.acceptance else ''})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
