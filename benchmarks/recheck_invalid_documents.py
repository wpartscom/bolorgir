#!/usr/bin/env python3
"""Recheck of the invalid documents of generatable coverage reports.

Reads every invalid_document pass from the sidecar of the given reports
(benchmarks/bench_jsb_coverage.py --generatable). A document that the
documented draft-04 integer deviation explains
(generation_passes.draft04_integer_spelling) is only counted. Every other
document is fed byte-exactly to the given library (spec-v1, byte-level
tokenizer, the harness limits), and one JSONL row of evidence is written:
the case, the seed of the pass, the sha256 of the document and the
verdict (accept, reject or error:<STATUS>). A pass whose document is not
in the sidecar gets the verdict no_document. The last row is a summary
with the library sha256. benchmarks/triage_semantic.py --doc-rechecks
reads the rows; the script does not classify.

Usage:
  python3 benchmarks/recheck_invalid_documents.py --lib PATH \
      --report benchmarks/coverage/spec-v1/baseline-dbc5eeb/coverage_repo.json.gz ...
"""

import argparse
import base64
import hashlib
import json
import os
import sys

_BENCH_DIR = os.path.dirname(os.path.abspath(__file__))


def document_bytes(doc):
    if isinstance(doc, dict):
        return base64.b64decode(doc["base64"])
    return doc.encode("utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lib", required=True, help="libbolorgir.so that gets the documents")
    ap.add_argument("--report", action="append", required=True,
                    help="generatable coverage report (.json or .json.gz) with its sidecar")
    args = ap.parse_args()
    os.environ["BLG_LIB_PATH"] = os.path.abspath(args.lib)
    os.environ["BOLORGIR_COVERAGE_PROFILE"] = "spec-v1"
    sys.path.insert(0, _BENCH_DIR)
    import bench_jsb_coverage as h
    gp, _so = h._gen_mods()
    summary = {}
    for rpath in args.report:
        report, side = h.load_report(rpath)
        snap = "maskbench" if report["meta"]["corpus"]["path"].startswith("maskbench") else "repo"
        snapshot = h.SNAPSHOTS[snap]
        counts = summary.setdefault(snap, {"invalid_passes": 0, "draft04": 0, "fed": 0,
                                           "no_document": 0})
        for rec in report["per_file"]:
            per_tok = side.get((rec["dataset"], rec["file"])) or {}
            todo = []
            for tok, rows in per_tok.items():
                for k, row in enumerate(rows, 1):
                    if row[0] != "invalid_document":
                        continue
                    counts["invalid_passes"] += 1
                    doc = row[6] if len(row) > 6 else None
                    base = {"snapshot": snap, "dataset": rec["dataset"], "file": rec["file"],
                            "tokenizer": tok, "pass": k, "seed": row[5]}
                    if doc is None:
                        counts["no_document"] += 1
                        print(json.dumps(dict(base, verdict="no_document")), flush=True)
                        continue
                    todo.append((base, document_bytes(doc)))
            if not todo:
                continue
            path = (os.path.join(snapshot["data_dir"], rec["file"]) if snap == "maskbench"
                    else os.path.join(snapshot["data_dir"], rec["dataset"], rec["file"]))
            data, _schema_doc, _tests = h.load_schema_bytes(snapshot, path)
            text = data.decode("utf-8")
            fed = []
            for base, doc in todo:
                if gp.is_known_deviation("draft04_integer_spelling", text, doc):
                    counts["draft04"] += 1
                else:
                    fed.append((base, doc))
            if not fed:
                continue
            ctx = h.make_context(h.byte_tokenizer_spec())
            try:
                g = h.blg.compile_schema(ctx, data, profile=b"spec-v1")
                try:
                    for base, doc in fed:
                        s = h.blg.Session(ctx, g)
                        try:
                            verdict, detail = h.feed_document(s, doc)
                        finally:
                            s.destroy()
                        counts["fed"] += 1
                        print(json.dumps(dict(base, sha256=hashlib.sha256(doc).hexdigest(),
                                              verdict=verdict, detail=detail)), flush=True)
                finally:
                    h.blg.grammar_release(g)
            finally:
                ctx.destroy()
    print(json.dumps({"summary": summary, "library_sha256": h.library_sha256()}))


if __name__ == "__main__":
    main()
