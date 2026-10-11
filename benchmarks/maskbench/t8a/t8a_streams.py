#!/usr/bin/env python3
"""Reference token streams of a T8a run.

For every corpus file and every test instance: the bytes the shared
serializer gives (`bolorgir.serializer.serialize_value` against the file's
schema, the hook every engine of a T8a run uses), or the serializer's
refusal, and the token ids of the pinned tokenizer for those bytes
(`encode(text, add_special_tokens=False)`, as the MaskBench runner calls
it). Invalid instances are included. The per-engine records written by
paired_runner.py during the run are compared with this file by
t8a_report.py: equal serialized bytes and equal token ids for every
(schema, instance), not only equal lengths.

  python3 t8a_streams.py --data DIR [--files-list LIST] --tokenizer SNAPSHOT_DIR \\
      --out streams.json.gz

Runs in a venv of the run (bolorgir and transformers of the run).
"""

import argparse
import gzip
import hashlib
import json
import os
import sys


def stream_record(i, test, schema, serialize_value, tok):
    rec = {"i": i, "valid": bool(test.get("valid"))}
    payload, status = serialize_value(test["data"], schema)
    if not status.ok:
        rec["serialization_incompatible"] = f"{status.reason} at {status.pointer}"
        return rec
    text = payload.decode("utf-8")
    ids = tok.encode(text, add_special_tokens=False)
    rec["instance_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    rec["ids_sha256"] = ids_digest(ids)
    rec["n_ids"] = len(ids)
    return rec


def ids_digest(ids):
    h = hashlib.sha256()
    for t in ids:
        h.update(int(t).to_bytes(4, "little"))
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--files-list", default="")
    ap.add_argument("--tokenizer", required=True, help="pinned tokenizer snapshot directory")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    from bolorgir.serializer import serialize_value
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(args.tokenizer)
    if args.files_list:
        with open(args.files_list, encoding="utf-8") as f:
            names = sorted(line.strip() for line in f if line.strip())
    else:
        names = sorted(n for n in os.listdir(args.data) if n.endswith(".json"))
    out = {"tokenizer": os.path.basename(os.path.normpath(args.tokenizer)), "files": {}}
    for n in names:
        with open(os.path.join(args.data, n)) as f:
            pos = json.loads(f.read())  # parsed as the MaskBench runner parses it
        out["files"][n] = [stream_record(i, t, pos["schema"], serialize_value, tok)
                           for i, t in enumerate(pos["tests"])]
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        json.dump(out, f)
    n_tests = sum(len(v) for v in out["files"].values())
    print(json.dumps({"files": len(names), "tests": n_tests}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
