#!/usr/bin/env python3
"""Diagnostic (not a scored run): the 20 passes of one schema on one
tokenizer of an R6 acceptance run, replayed with the harness of a copy of
the run's tree (--tree, e.g. de7/tree-e = git archive fc44c37 with the
run's corpora linked read-only) on the library BLG_LIB_PATH with the
recorded seeds, one fresh context, 10 s per pass. Per pass: outcome,
step, seconds and whether it equals the record of the run (--passes, the
run's coverage_<snapshot>.passes.json.gz; meaningful on the run's library
only); an engine_unavailable pass also gets the engine message with the
histogram of undecided certificate reasons, the tail and the hex prefix.
Generalized from replay_d4.py (50cbd80e).

    HF_HOME=<run>/hf-cache HF_HUB_OFFLINE=1 BLG_LIB_PATH=<run>/lib/libbolorgir.so.0.1.0 \
      python replay_e.py --tree de7/tree-e --passes <run>/out/coverage_repo.passes.json.gz \
        --snapshot repo --file DATASET/FILE --tokenizer byte --label t3-6e62e0d1 --out X.jsonl
"""
import argparse
import ctypes
import gzip
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
UND = ["fail_budget0", "fail_search", "fail_oom", "comb_group", "openobj_track", "openobj_deps",
       "openobj_prop_names", "openobj_key_bounds", "openobj_min_props", "openobj_max_props",
       "openobj_colon", "openobj_witness", "openobj_keystr", "openobj_keyesc", "choice", "seq",
       "object", "repeat", "num_range_sat", "num_const_sat", "int_num_sat", "no_nonempty",
       "openobj_gate", "num_und", "search_entered"]
LAST = {}


def und_hist():
    lib = h.blg.LIB
    n = lib.blg_cert_stats(None, 0)
    arr = (ctypes.c_uint64 * n)()
    lib.blg_cert_stats(arr, n)
    return {UND[i]: arr[i] for i in range(min(n, len(UND))) if arr[i] and UND[i] != "search_entered"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--passes", required=True, help="the run's coverage_<snapshot>.passes.json.gz")
    ap.add_argument("--snapshot", choices=("repo", "maskbench"), default="repo")
    ap.add_argument("--file", required=True, help="DATASET/FILE of the snapshot")
    ap.add_argument("--tokenizer", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="", help="comma list of pass numbers (default: all 20)")
    ap.add_argument("--max-seconds", type=float, default=10.0,
                    help="pass time budget (the acceptance run: 10)")
    args = ap.parse_args()
    only = {int(x) for x in args.only.split(",") if x}
    ds, f = args.file.split("/", 1)
    recorded = None
    for rds, rf, toks in json.load(gzip.open(args.passes))["rows"]:
        if (rds, rf) == (ds, f):
            recorded = toks.get(args.tokenizer)
    gp, _so = h._gen_mods()
    orig = gp.fill_mask

    def fill_mask(session, buf):
        err = gp.blg.new_error()
        st = gp.blg.LIB.blg_fill_mask(session.handle, buf.ctypes.data_as(
            gp.ctypes.POINTER(gp.ctypes.c_uint32)), buf.size, gp.ctypes.byref(err))
        LAST["err"] = gp.blg.err_text(err) if st else ""
        return st
    gp.fill_mask = fill_mask
    assert orig is not None
    gt = h.GenTokenizer(args.tokenizer)
    snap = h.SNAPSHOTS[args.snapshot]
    # as iter_files: DATASET/FILE in the repository, FILE in MaskBench
    path = (os.path.join(snap["data_dir"], ds, f) if snap["layout"] == "raw"
            else os.path.join(snap["data_dir"], f))
    data, schema_doc, _ = h.load_schema_bytes(snap, path)
    validator, _ = gp.make_reference_validator(data)
    opts = {"passes": 20, "max_steps": 8192, "max_seconds": args.max_seconds, "schema_budget": 0.0}
    head = {"label": args.label, "library_sha256": h.library_sha256(), "file": args.file,
            "snapshot": args.snapshot,
            "tokenizer": args.tokenizer, "max_seconds": args.max_seconds}
    print(json.dumps(head), flush=True)
    counts = {}
    ctx = h.make_context(gt.spec)
    with open(args.out, "w") as out:
        out.write(json.dumps(head) + "\n")
        try:
            grammar, failure = h.compile_for(gt, ctx, data, ds, f)
            assert failure is None, failure
            plan = h.pass_plan(h.parsed_schema(data, schema_doc), validator)
            for k in range(1, 21):
                if only and k not in only:
                    continue
                seed = h.pass_seed(ds, f, gt.key, k)
                LAST["err"] = ""
                h.blg.LIB.blg_cert_stats_reset()
                t0 = time.monotonic()
                rec, doc = h.gen_pass(gt, ctx, grammar, k, seed, plan, opts)
                dt = time.monotonic() - t0
                got = [rec["outcome"], rec.get("stage"), rec.get("code"), rec.get("step"),
                       rec.get("prefix_length"), rec.get("seed")]
                row = {"k": k, "outcome": rec["outcome"], "code": rec.get("code"),
                       "step": rec.get("step"), "seconds": round(dt, 2),
                       "s_per_step": round(dt / max(1, rec.get("step") or 1), 4),
                       "as_recorded": recorded is not None and got == recorded[k - 1][:6]}
                if recorded is not None:
                    row["recorded"] = recorded[k - 1][:5]
                if rec["outcome"] == "engine_unavailable":
                    row["msg"] = LAST["err"] + " " + json.dumps(und_hist())
                    text = doc.decode("utf-8", "replace") if doc else ""
                    row["tail"] = text[-80:]
                    row["prefix_hex"] = doc.hex() if doc else ""
                counts[rec["outcome"]] = counts.get(rec["outcome"], 0) + 1
                out.write(json.dumps(row) + "\n")
                out.flush()
            h.blg.grammar_release(grammar)
        finally:
            ctx.destroy()
        out.write(json.dumps({"counts": counts}) + "\n")
    print(json.dumps({"counts": counts}), flush=True)


if __name__ == "__main__":
    main()
