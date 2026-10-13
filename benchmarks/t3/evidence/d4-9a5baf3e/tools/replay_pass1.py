#!/usr/bin/env python3
"""Diagnostic (not a scored run): pass 1 - the greedy policy pass of the
acceptance harness (bench_jsb_coverage.gen_pass, k = 1:
generate_document over single-byte tokens, the 10 s budget checked at
steps 16, 32, 48, ...) - of the listed results, on the library
BLG_LIB_PATH, with the harness of a copy of the run's tree (--tree,
de7/tree-e: git archive fc44c37 with the run's corpora linked). The HF
tokenizers come from --specs (export_specs.py: loaded once as the run
loaded them), not from the bolorgir package, so the library under test
is the only libbolorgir in the process and every library gets the same
vocabulary. One context per tokenizer, the grammar compiled per result
as the harness does; nothing else runs in the process.

The pass runs with the budget --max-seconds (default 60, not the run's
10) so it reaches its end; every fill_mask is timed. Per result: the
end (status, stage, step), the document (sha256 and hex), the time of
each fill, the elapsed time at each budget check (steps 16, 32, ...)
and the outcome the run's 10 s budget gives on these times: a time limit
at the first check past 10 s before the end, else the end itself.

    BLG_LIB_PATH=<lib> python replay_pass1.py --tree de7/tree-e --items items.json \\
        --specs r11a_specs.json --label L --round R --out OUT.jsonl

items.json: [{"snapshot": "repo"|"maskbench", "dataset", "file", "tokenizer"}]
"""
import argparse
import hashlib
import json
import os
import sys
import time

os.environ.setdefault("BOLORGIR_COVERAGE_PROFILE", "spec-v1")
_tree = sys.argv[sys.argv.index("--tree") + 1] if "--tree" in sys.argv else None
if not _tree:
    sys.exit("--tree is required")
sys.path.insert(0, os.path.join(os.path.abspath(_tree), "benchmarks"))
import bench_jsb_coverage as h  # noqa: E402

RUN_BUDGET = 10.0
CHECK_EVERY = 16  # generate_document checks its budget at these steps


class TimedSession:
    """The harness session with every fill_mask_words call timed."""

    def __init__(self, session):
        self._s = session
        self.fills = []  # (start, end) wall times, time.time() as the policy uses

    def fill_mask_words(self):
        t = time.time()
        r = self._s.fill_mask_words()
        self.fills.append((t, time.time()))
        return r

    def __getattr__(self, name):
        return getattr(self._s, name)


def outcome_at(budget, checks, end_step, end_ok):
    """The outcome of the pass under `budget`: a time limit at the first
    check (step, elapsed) past the budget at or before the end step."""
    for step, elapsed in checks:
        if step <= end_step and elapsed > budget:
            return {"outcome": "incomplete:time_limit", "step": step}
    return {"outcome": "completed" if end_ok else "other", "step": end_step}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--items", required=True)
    ap.add_argument("--specs", required=True, help="export_specs.py output")
    ap.add_argument("--label", required=True)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-seconds", type=float, default=60.0)
    args = ap.parse_args()
    items = json.load(open(args.items, encoding="utf-8"))
    specs = json.load(open(args.specs, encoding="utf-8"))["tokenizers"]

    def spec_of(name, revision):
        for v in specs.values():
            if (v["name"], v["revision"]) == (name, revision):
                return h.TokenizerSpec(tokens=tuple(bytes.fromhex(t) for t in v["tokens_hex"]),
                                       eos_ids=tuple(v["eos_ids"]), special_ids=tuple(v["special_ids"]))
        raise LookupError(f"{name}@{revision} not in {args.specs}")
    h.hf_tokenizer_spec = spec_of
    gp, _so = h._gen_mods()
    head = {"label": args.label, "round": args.round, "library_sha256": h.library_sha256(),
            "specs_sha256": {k: v["tokens_sha256"] for k, v in specs.items()},
            "pid": os.getpid(), "affinity": sorted(os.sched_getaffinity(0)),
            "loadavg_start": os.getloadavg(), "max_seconds": args.max_seconds,
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    print(json.dumps(head), flush=True)
    toks = {}
    with open(args.out, "w", encoding="utf-8") as out:
        out.write(json.dumps(head) + "\n")
        for it in items:
            key = it["tokenizer"]
            if key not in toks:
                gt = h.GenTokenizer(key)
                if gt.spec is None:
                    sys.exit(f"tokenizer {key} unavailable: {gt.info.get('unavailable')}")
                toks[key] = (gt, h.make_context(gt.spec))
            gt, ctx = toks[key]
            snap = h.SNAPSHOTS[it["snapshot"]]
            # as iter_files: DATASET/FILE in the repository, FILE in MaskBench
            path = (os.path.join(snap["data_dir"], it["dataset"], it["file"])
                    if snap["layout"] == "raw" else os.path.join(snap["data_dir"], it["file"]))
            data, schema_doc, _ = h.load_schema_bytes(snap, path)
            validator, _ = gp.make_reference_validator(data)
            grammar, failure = h.compile_for(gt, ctx, data, it["dataset"], it["file"])
            if failure is not None:
                row = dict(it, error=f"compile: {failure}")
            else:
                try:
                    plan = h.pass_plan(h.parsed_schema(data, schema_doc), validator)
                    session = h.blg.Session(ctx, grammar)
                    ts = TimedSession(session)
                    where = {}
                    t0 = time.time()
                    try:
                        status, doc, detail = h.generate_document(
                            ts, 8192, plan["req_keys"], plan["str_cands"], args.max_seconds,
                            byte_ids=gt.byte_ids, where=where)
                    finally:
                        session.destroy()
                    total = time.time() - t0
                finally:
                    h.blg.grammar_release(grammar)
                checks = [(k, round(ts.fills[k][0] - t0, 4))
                          for k in range(CHECK_EVERY, len(ts.fills), CHECK_EVERY)]
                end_step = where.get("step")
                end_ok = status == "ok"
                json_ok, verdict, _err = validator.validate(doc) if end_ok else (None, None, None)
                row = dict(it, status=status, stage=where.get("stage"), step=end_step,
                           limit=where.get("limit"), detail=detail[:200],
                           valid=bool(json_ok and verdict) if end_ok else None,
                           seconds=round(total, 4), fills=len(ts.fills),
                           fill_ms=[round((b - a) * 1000, 2) for a, b in ts.fills],
                           checks=checks,
                           at_run_budget=outcome_at(RUN_BUDGET, checks, end_step or 0, end_ok),
                           document_sha256=hashlib.sha256(doc).hexdigest(),
                           document_hex=doc.hex())
            out.write(json.dumps(row) + "\n")
            out.flush()
            print(f"{it['file']} {it['tokenizer']}: {row.get('status')} step {row.get('step')} "
                  f"{row.get('seconds')} s -> {row.get('at_run_budget')}", flush=True)
        for gt, ctx in toks.values():
            ctx.destroy()
        tail = {"loadavg_end": os.getloadavg(),
                "ended_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        out.write(json.dumps(tail) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
