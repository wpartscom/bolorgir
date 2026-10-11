#!/usr/bin/env python3
"""Recheck of the correctness counters of a semantic coverage report.

Replays every case listed under the failure kinds below in a
benchmarks/bench_jsb_coverage.py --semantic report (by default the
committed spec-v1 reports) against a given library, and writes one JSONL
row of evidence per case. The class of each case (A-E) is assigned by a
person from this evidence; the script does not classify.

  generated_schema_invalid, generation_dead_end, generation_error
      the schema is compiled in a fresh byte-tokenizer context and the
      generate_document policy of the harness runs again (same budgets).
      A finished document is validated twice: by plain jsonschema (the
      validator of the report) and by the reference validator of
      tests/generation_passes.py (exact numbers, ECMA-262 `pattern`).
      A document the reference rejects is validated once more with
      integral values counted as integers in every draft
      (reference_value_integers): true isolates the draft-04 integer
      spelling (docs/supported_features.md section 9).
  invalid_instance_accepted
      every invalid corpus instance of the file is serialized and fed
      byte-exactly; rows where the engine accepts and jsonschema rejects
      are reported.
  compile_mismatch, session_create_failed
      compile and session creation in a fresh context with the harness
      limits.

Usage:
  python3 benchmarks/recheck_semantic_counters.py --lib PATH \
      [--report benchmarks/coverage/spec-v1/coverage_maskbench.json[.gz]] ...
"""

import argparse
import gzip
import json
import os
import sys
import time

_BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BENCH_DIR)

GEN_KINDS = ("generated_schema_invalid", "generation_dead_end", "generation_error")
KINDS = GEN_KINDS + ("invalid_instance_accepted", "compile_mismatch",
                     "session_create_failed")
DEFAULT_REPORTS = [
    os.path.join(_BENCH_DIR, "coverage", "spec-v1", "coverage_maskbench.json"),
    os.path.join(_BENCH_DIR, "coverage", "spec-v1", "coverage_repo.json"),
]


def open_report(path):
    """Open a coverage report; a missing .json falls back to .json.gz."""
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    if os.path.exists(path):
        return open(path, encoding="utf-8")
    return gzip.open(path + ".gz", "rt", encoding="utf-8")


def snapshot_of(report):
    path = report["meta"]["corpus"]["path"]
    return "maskbench" if path.startswith("maskbench") else "repo"


def file_path(h, snap, dataset, fname):
    d = h.SNAPSHOTS[snap]["data_dir"]
    return os.path.join(d, fname) if snap == "maskbench" else os.path.join(d, dataset, fname)


def recheck_generation(h, gp, data, schema_doc):
    ctx = h.make_context(h.byte_tokenizer_spec())
    try:
        try:
            g = h.blg.compile_schema(ctx, data, profile=b"spec-v1")
        except h.blg.CoreFailure as e:
            return {"compile": h.blg.STATUS_NAMES.get(e.status, e.status)}
        try:
            if schema_doc is None:
                schema_doc = json.loads(data.decode("utf-8"))
            s = h.blg.Session(ctx, g)
            where = {}
            wd = h.start_cancel_watchdog(ctx.handle, h.DEFAULT_MAX_GEN_SECONDS + 15.0)
            try:
                status, doc, detail = h.generate_document(
                    s, h.DEFAULT_MAX_STEPS, h.collect_key_candidates(schema_doc),
                    h.collect_string_candidates(schema_doc),
                    h.DEFAULT_MAX_GEN_SECONDS, where=where)
            finally:
                wd()
                s.destroy()
            row = {"compile": "OK", "generation": status, "detail": detail,
                   "stage": where.get("stage"), "step": where.get("step"),
                   "doc": doc[:400].decode("utf-8", "replace"), "doc_bytes": len(doc)}
            if status == "ok":
                plain, perr = h.make_jsonschema_validator(schema_doc)
                try:
                    value = json.loads(doc.decode("utf-8"))
                    row["plain_jsonschema"] = plain(value) if plain else f"unusable: {perr}"
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    row["plain_jsonschema"] = f"not JSON: {e}"
                ref, rerr = gp.make_reference_validator(data)
                if ref is None:
                    row["reference"] = f"unusable: {rerr}"
                else:
                    json_ok, verdict, error = ref.validate(doc)
                    row["reference"] = verdict if json_ok else "not JSON"
                    if error:
                        row["reference_error"] = error
                    if json_ok and verdict is False:
                        # Same dialect, integral values counted as integers:
                        # True isolates the draft-04 integer spelling.
                        from exact_numbers import exact_dialects, exact_validator, loads
                        v = exact_validator(data.decode("utf-8"), ref.cls, value_integers=True)
                        with gp.ecma_patterns(), exact_dialects(value_integers=True):
                            row["reference_value_integers"] = bool(
                                v.is_valid(loads(doc.decode("utf-8"))))
            return row
        finally:
            h.blg.grammar_release(g)
    finally:
        ctx.destroy()


def recheck_invalid_instances(h, data, schema_doc, tests, serializer):
    ctx = h.make_context(h.byte_tokenizer_spec())
    out = []
    try:
        g = h.blg.compile_schema(ctx, data, profile=b"spec-v1")
        try:
            plain, _ = h.make_jsonschema_validator(schema_doc)
            for i, t in enumerate(tests):
                if t.get("valid") is not False:
                    continue
                payload, sstat = serializer.serialize_value(t.get("data"), schema_doc)
                if not sstat.ok:
                    continue
                s = h.blg.Session(ctx, g)
                try:
                    verdict, detail = h.feed_document(s, payload)
                finally:
                    s.destroy()
                jv = plain(t.get("data")) if plain else None
                if verdict == "accept":
                    out.append({"test_index": i, "engine": verdict, "jsonschema": jv,
                                "instance": payload[:400].decode("utf-8", "replace")})
        finally:
            h.blg.grammar_release(g)
    finally:
        ctx.destroy()
    return out


def recheck_compile_session(h, data):
    ctx = h.make_context(h.byte_tokenizer_spec())
    try:
        try:
            g = h.blg.compile_schema(ctx, data, profile=b"spec-v1")
        except h.blg.CoreFailure as e:
            return {"compile": h.blg.STATUS_NAMES.get(e.status, e.status), "detail": str(e)}
        try:
            try:
                s = h.blg.Session(ctx, g)
                s.destroy()
                return {"compile": "OK", "session": "OK"}
            except h.blg.CoreFailure as e:
                return {"compile": "OK", "session": h.blg.STATUS_NAMES.get(e.status, e.status),
                        "detail": str(e)}
        finally:
            h.blg.grammar_release(g)
    finally:
        ctx.destroy()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lib", required=True, help="libbolorgir.so to recheck")
    ap.add_argument("--report", action="append", default=None,
                    help="semantic coverage report(s) listing the cases")
    ap.add_argument("--kinds", default=",".join(KINDS))
    args = ap.parse_args()
    os.environ["BLG_LIB_PATH"] = os.path.abspath(args.lib)
    os.environ.setdefault("BOLORGIR_COVERAGE_PROFILE", "spec-v1")
    sys.path.insert(0, _BENCH_DIR)
    import bench_jsb_coverage as h
    gp, _so = h._gen_mods()
    serializer = h.load_value_serializer()
    kinds = [k for k in args.kinds.split(",") if k]
    counts = {}
    for rpath in args.report or DEFAULT_REPORTS:
        with open_report(rpath) as f:
            report = json.load(f)
        snap = snapshot_of(report)
        failures = report["aggregate"]["semantic"]["failures"]
        for kind in kinds:
            for case in failures.get(kind, []):
                t0 = time.time()
                path = file_path(h, snap, case["dataset"], case["file"])
                data, schema_doc, tests = h.load_schema_bytes(h.SNAPSHOTS[snap], path)
                row = {"snapshot": snap, "kind": kind, "dataset": case["dataset"],
                       "file": case["file"], "reported": case["detail"]}
                try:
                    if kind in GEN_KINDS:
                        row["now"] = recheck_generation(h, gp, data, schema_doc)
                    elif kind == "invalid_instance_accepted":
                        row["now"] = recheck_invalid_instances(h, data, schema_doc,
                                                               tests, serializer)
                    else:
                        row["now"] = recheck_compile_session(h, data)
                except Exception as e:  # noqa: BLE001 - evidence row
                    row["now"] = {"harness_error": f"{type(e).__name__}: {e}"}
                row["seconds"] = round(time.time() - t0, 2)
                counts[(snap, kind)] = counts.get((snap, kind), 0) + 1
                print(json.dumps(row, ensure_ascii=False), flush=True)
    print(json.dumps({"summary": {f"{s}/{k}": n for (s, k), n in counts.items()},
                      "library_sha256": h.library_sha256()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
