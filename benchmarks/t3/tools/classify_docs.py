#!/usr/bin/env python3
"""Classify recovered pass documents (T2c): decode, test the documented
draft-04 integer spelling deviation, and print the reference validator's
first error for every other document.

Usage: classify_docs.py <rows.jsonl>
"""
import base64
import json
import os
import sys

os.environ.setdefault("BOLORGIR_COVERAGE_PROFILE", "spec-v1")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))

import bench_jsb_coverage as h  # noqa: E402

gp, _so = h._gen_mods()
snap = h.SNAPSHOTS["maskbench"]
cache = {}
for line in open(sys.argv[1]):
    row = json.loads(line)
    field = row.get("document_b64")
    if not field:
        continue
    if isinstance(field, dict):
        doc = base64.b64decode(field["base64"])
    else:
        doc = field.encode("utf-8")
    key = (row["dataset"], row["file"])
    if key not in cache:
        data, schema_doc, _ = h.load_schema_bytes(snap, os.path.join(snap["data_dir"], row["file"]))
        validator, reason = gp.make_reference_validator(data)
        cache[key] = (data, validator, reason)
    data, validator, reason = cache[key]
    text = data.decode("utf-8")
    draft04 = gp.is_known_deviation("draft04_integer_spelling", text, doc)
    detail = ""
    try:
        instance = json.loads(doc.decode("utf-8"))
        err = next(iter(validator.iter_errors(instance)), None)
        if err is not None:
            detail = f"{list(err.absolute_path)}: {err.message}"[:200]
    except Exception as e:  # noqa: BLE001
        detail = f"parse: {type(e).__name__}: {e}"[:200]
    print(json.dumps({"dataset": row["dataset"], "file": row["file"], "pass": row["pass"],
                      "step": row["step"], "match": row.get("match_recorded"),
                      "draft04_spelling": bool(draft04), "detail": detail}))
