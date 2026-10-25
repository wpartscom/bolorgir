#!/usr/bin/env python3
"""Self-check for the ADR-0007 gates, with injected harness faults.

Runs the real gate, warm validator and matrix verdict against a synthetic
corpus and a stubbed engine. Modes cover mask/status/accept divergences,
shared faults in both arms, clean and faulty rejections, statusless
exceptions, the warm condition/data faults, saved-JSON verification and
the SKIP/stale-output cases. A fault shared by both arms must still fail.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import types

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BENCH = os.path.join(ROOT, "benchmarks", "bench_adr0007.py")
MATRIX = os.path.join(HERE, "matrix_adr0007.py")
MANIFEST = os.path.join(ROOT, "benchmarks", "baseline_slice.json")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def run_case(bench, mode, out_dir, data_dir, extra_args=None):
    """Run the real benchmark gate or warm harness in one injected mode."""
    global CURRENT_MODE, TOKEN_COUNT
    CURRENT_MODE = mode
    TOKEN_COUNT = 1 if mode in (
        "incomplete-number", "reject-clean", "reject-then-finish-error",
        "reject-then-can-end-error", "reject-then-can-end-no-status",
        "reject-then-can-end-memory",
        "reject-then-finish-no-status") else 410
    bench.MASKBENCH_DATA = data_dir
    out = os.path.join(out_dir, mode + ".json")
    artifacts = os.path.join(out_dir, "artifacts-" + mode)
    argv = ["bench_adr0007.py", "--manifest", MANIFEST,
            "--maskbench-files", "1",
            "--mask-artifacts", artifacts, "--out", out]
    argv += (extra_args if extra_args is not None else ["--skip-warm-ext"])
    old = sys.argv
    sys.argv = argv
    stdout, stderr = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            bench.main()
        except SystemExit as exc:
            code = exc.code if exc.code is not None else 0
    sys.argv = old
    data = None
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            data = json.load(f)
    artifacts_left = (sorted(os.listdir(artifacts))
                      if os.path.isdir(artifacts) else [])
    return {"mode": mode, "exit": code, "json": data,
            "path": out, "artifact_files": artifacts_left,
            "stdout": stdout.getvalue(), "stderr": stderr.getvalue()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.path.join(
        ROOT, "benchmarks", "local", "gate-selfcheck"))
    args = ap.parse_args()
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    bench = load("selfcheck_bench", BENCH)
    matrix = load("selfcheck_matrix", MATRIX)

    corpus = pathlib.Path(out_dir) / "synthetic-corpus"
    corpus.mkdir(exist_ok=True)
    (corpus / "case.json").write_text(json.dumps(
        {"schema": {}, "tests": [{"data": "test", "valid": True}]}),
        encoding="utf-8")
    number_corpus = pathlib.Path(out_dir) / "number-corpus"
    number_corpus.mkdir(exist_ok=True)
    (number_corpus / "case.json").write_text(json.dumps(
        {"schema": {}, "tests": [{"data": 1, "valid": False}]}),
        encoding="utf-8")

    global CURRENT_MODE, TOKEN_COUNT
    CURRENT_MODE = "control"
    TOKEN_COUNT = 410

    class Context:
        def __init__(self, fp):
            self.fp = fp

        def destroy(self):
            pass

    class Session:
        def __init__(self, ctx, grammar):
            self.fp = ctx.fp
            self.step = 0
            self.fault_done = False

        def fill_mask_words(self):
            if self.step >= TOKEN_COUNT:
                # Terminal (post-input) mask, not part of the timed steps.
                if CURRENT_MODE == "tail-mask":
                    return 0, ([0] if self.fp == 0 else [2])
                if CURRENT_MODE == "incomplete-number":
                    return 0, [2]
                return 0, [1]
            if CURRENT_MODE == "mask-401" and self.step == 401:
                return 0, ([3] if self.fp == 0 else [1])
            if (CURRENT_MODE in ("fill-both-409", "status-409")
                    and self.step == 409 and not self.fault_done):
                self.fault_done = True
                if CURRENT_MODE == "fill-both-409" or self.fp == 0:
                    return 5, []
            return 0, [1]

        def accept(self, tid):
            step = self.step
            self.step += 1
            if CURRENT_MODE == "accept-409" and self.fp == 0 \
                    and step == 409:
                return 5
            if CURRENT_MODE in ("reject-clean",
                                "reject-then-finish-error",
                                "reject-then-can-end-error",
                                "reject-then-can-end-no-status",
                                "reject-then-can-end-memory",
                                "reject-then-finish-no-status"):
                return 6
            return 0

        def can_end(self):
            if CURRENT_MODE == "cannot-end":
                return self.fp != 0
            if CURRENT_MODE in ("cannot-end-both", "incomplete-number"):
                return False
            if CURRENT_MODE == "reject-then-can-end-error":
                exc = RuntimeError("synthetic can_end failure")
                exc.status = 8
                raise exc
            if CURRENT_MODE == "reject-then-can-end-no-status":
                raise RuntimeError("synthetic can_end failure without status")
            if CURRENT_MODE == "reject-then-can-end-memory":
                raise MemoryError("synthetic allocation failure in can_end")
            return True

        def finish(self):
            if CURRENT_MODE in ("finish-error-both",
                                "reject-then-finish-error"):
                return 8
            if CURRENT_MODE in ("reject-then-can-end-no-status",
                                "reject-then-can-end-memory"):
                return 11
            if CURRENT_MODE == "reject-then-finish-no-status":
                raise RuntimeError("synthetic finish failure without status")
            return 0 if self.can_end() else 11

        def destroy(self):
            pass

    blg = types.SimpleNamespace(
        Session=Session, CoreFailure=type("CoreFailure", (Exception,), {}),
        BLG_OK=0, BLG_ERR_INVALID_TOKEN=6, BLG_ERR_DEAD_END=7,
        BLG_ERR_WRONG_STATE=11,
        BLG_CACHE_DEFAULT=0, BLG_MODE_ADAPTIVE=1,
        grammar_release=lambda grammar: None,
        Context=Context,
        compile_schema=lambda ctx, schema: None,
        LIB=types.SimpleNamespace(_path="synthetic; no library loaded"))
    sys.modules["blg_ctypes"] = blg
    hf = types.SimpleNamespace(encode=lambda *a, **kw: [0] * TOKEN_COUNT)
    bench.bc.require_core = lambda: object()
    bench.load_bundle = lambda *a: (hf, types.SimpleNamespace(vocab_size=2))
    bench.make_spec = lambda *a: None
    bench.prefix_token_ids = lambda *a: []
    bench.open_session = lambda blg_, spec, schema, fast_path: (
        Context(fast_path), None)
    bench.mask_at = lambda *a, **kw: {"status": 0, "words": [1]}
    bench.bench_probe_cold = lambda *a: {}
    bench.bench_probe_warm = lambda *a: {}
    bench.select_compiling = lambda *a: ["case"]

    results = []

    def check(name, ok, detail):
        results.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("ok   " if ok else "FAIL ") + name + ": " + detail)

    control = run_case(bench, "control", out_dir, str(corpus))
    gate = (control["json"] or {}).get("gate") or {}
    check("control passes", control["exit"] == 0 and gate.get("status") == "PASS",
          "exit %r, gate %r" % (control["exit"], gate.get("status")))
    check("control compares every mask", gate.get("maskbench_states") == 410,
          "maskbench_states = %r (old sampler: 401)" %
          gate.get("maskbench_states"))
    check("control runs every terminal check",
          gate.get("maskbench_terminal_states") == 1 and
          gate.get("maskbench_terminal_complete") is True,
          "terminals = %r, complete = %r" % (
              gate.get("maskbench_terminal_states"),
              gate.get("maskbench_terminal_complete")))
    check("control has zero errors",
          gate.get("maskbench_fill_errors") == 0 and
          gate.get("maskbench_terminal_fill_errors") == 0 and
          gate.get("maskbench_input_errors") == 0 and
          gate.get("maskbench_completion_errors") == 0 and
          gate.get("maskbench_validation_errors") == 0,
          "fill=%r terminal_fill=%r input=%r completion=%r validation=%r" % (
              gate.get("maskbench_fill_errors"),
              gate.get("maskbench_terminal_fill_errors"),
              gate.get("maskbench_input_errors"),
              gate.get("maskbench_completion_errors"),
              gate.get("maskbench_validation_errors")))
    check("control publishes a trace digest",
          bool(gate.get("maskbench_trace_sha256")),
          "trace_sha256 = %r" % gate.get("maskbench_trace_sha256"))

    expected = {
        "mask-401": "mask x1",
        "status-409": "fill x1",
        "accept-409": "input x1",
        "fill-both-409": "fill x2",
        "cannot-end": "terminal",
        "tail-mask": "terminal x1",
        "finish-error-both": "completion",
        "cannot-end-both": "validation",
    }
    for mode, needle in expected.items():
        case = run_case(bench, mode, out_dir, str(corpus))
        message = str(case["exit"])
        check(mode + " fails", case["exit"] not in (0, None),
              "exit = %s" % message)
        check(mode + " names the fault", needle in message,
              "message = %s" % message)
        check(mode + " leaves evidence", bool(case["artifact_files"]),
              "artifacts = %r" % case["artifact_files"])
        check(mode + " writes no success JSON", case["json"] is None,
              "json written = %s" % (case["json"] is not None))

    incomplete = run_case(bench, "incomplete-number", out_dir,
                          str(number_corpus))
    igate = (incomplete["json"] or {}).get("gate") or {}
    check("incomplete number is a correct rejection",
          incomplete["exit"] == 0 and igate.get("status") == "PASS",
          "exit %r, gate %r" % (incomplete["exit"],
                                 igate.get("status")))
    check("incomplete number has no false validation error",
          igate.get("maskbench_validation_errors") == 0 and
          igate.get("maskbench_completion_errors") == 0,
          "validation=%r completion=%r" % (
              igate.get("maskbench_validation_errors"),
              igate.get("maskbench_completion_errors")))
    check("incomplete number ran its terminal check",
          igate.get("maskbench_states") == 1 and
          igate.get("maskbench_terminal_states") == 1 and
          igate.get("maskbench_terminal_complete") is True,
          "states=%r terminals=%r complete=%r" % (
              igate.get("maskbench_states"),
              igate.get("maskbench_terminal_states"),
              igate.get("maskbench_terminal_complete")))
    check("incomplete number leaves no failure artifact",
          not incomplete["artifact_files"],
          "artifacts = %r" % incomplete["artifact_files"])

    clean = run_case(bench, "reject-clean", out_dir, str(number_corpus))
    cgate = (clean["json"] or {}).get("gate") or {}
    check("clean rejection passes",
          clean["exit"] == 0 and cgate.get("status") == "PASS",
          "exit %r, gate %r" % (clean["exit"], cgate.get("status")))
    check("clean rejection counts no completion fault",
          cgate.get("maskbench_completion_errors") == 0 and
          cgate.get("maskbench_validation_errors") == 0,
          "completion=%r validation=%r" % (
              cgate.get("maskbench_completion_errors"),
              cgate.get("maskbench_validation_errors")))

    for mode in ("reject-then-finish-error",
                 "reject-then-can-end-error",
                 "reject-then-can-end-no-status",
                 "reject-then-can-end-memory",
                 "reject-then-finish-no-status"):
        case = run_case(bench, mode, out_dir, str(number_corpus))
        message = str(case["exit"])
        check(mode + " fails", case["exit"] not in (0, None),
              "exit = %s" % message)
        check(mode + " counts a completion error", "completion" in message,
              "message = %s" % message)
        check(mode + " leaves evidence", bool(case["artifact_files"]),
              "artifacts = %r" % case["artifact_files"])
        check(mode + " writes no success JSON", case["json"] is None,
              "json written = %s" % (case["json"] is not None))

    class PlainFault(Exception):
        pass

    plain = PlainFault("synthetic")
    check("statusless exception is recorded by type",
          bench._terminal_error(plain) == "PlainFault",
          "recorded %r" % (bench._terminal_error(plain),))
    plain.status = 8
    check("exception status is kept next to the type",
          bench._terminal_error(plain) == "PlainFault(8)",
          "recorded %r" % (bench._terminal_error(plain),))

    # ---- warm conditions: control, missing load, end affinity --------
    warm_lib = os.path.join(out_dir, "warm-lib.so")
    with open(warm_lib, "wb") as f:
        f.write(b"gate-selfcheck-warm-lib\n")
    with open(warm_lib, "rb") as f:
        warm_sha = sha256_bytes(f.read())
    os.environ["BLG_LIB_PATH"] = warm_lib
    blg.LIB._path = warm_lib
    try:
        warm_cpu = min(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        warm_cpu = 0

    def warm_stub(fault):
        def run(manifest, pid, observations, lib_path, on_first=False,
                cpu=None):
            start = {
                "cpu_affinity": [warm_cpu], "cpu_now": warm_cpu,
                "extension_version": "synthetic-not-a-measurement",
                "cpu": {"governors": {str(warm_cpu): "powersave"}},
                "load": {"loadavg": [0.0, 0.0, 0.0],
                         "cpu_pressure_some": {"avg10": 0.0}}}
            end = copy.deepcopy(start)
            if fault == "missing-load":
                start.pop("load")
                end.pop("load")
            if fault == "end-affinity":
                end["cpu_affinity"] = [warm_cpu + 1]
                end["cpu_now"] = warm_cpu + 1
            if fault == "end-governor":
                end["cpu"]["governors"] = {str(warm_cpu): "performance"}
            leg = {"observations": 2000, "cache_hits": 2000,
                   "all_hits": True,
                   "loaded": {"sha256": warm_sha},
                   "loaded_end": {"sha256": warm_sha},
                   "expected_sha256": warm_sha,
                   "conditions": {"start": start, "end": end},
                   "fill_wall_ns": {"mean": 1000.0, "p50": 1000,
                                    "count": 2000}}
            if fault == "missing-loaded-end":
                leg.pop("loaded_end")
            if fault == "nan-mean":
                leg["fill_wall_ns"]["mean"] = float("nan")
            if fault == "short-observations":
                leg["observations"] = 1
                leg["cache_hits"] = 1
                leg["fill_wall_ns"]["count"] = 1
            return {"off": copy.deepcopy(leg), "on": copy.deepcopy(leg)}
        return run

    warm_args = ["--skip-maskbench", "--warm-repeats", "5",
                 "--warm-observations", "2000",
                 "--warm-cpu", str(warm_cpu),
                 "--warm-governor", "powersave"]
    warm_faults = ("control", "missing-load", "end-affinity",
                   "end-governor", "missing-loaded-end", "nan-mean",
                   "short-observations")
    warm_expect = {"control": (True, True)}
    for fault in warm_faults[1:]:
        warm_expect[fault] = (False, False)
    for fault in warm_faults:
        bench.warm_via_extension = warm_stub(fault)
        case = run_case(bench, "warm-" + fault, out_dir, str(corpus),
                        extra_args=warm_args + ["--run-id", "warm-" + fault])
        warm = (case["json"] or {}).get("warm_acceptance") or {}
        job = next(j for j in matrix.jobs(
            "quick", sys.executable, "zig", out_dir, warm_cpu=warm_cpu,
            warm_limits={"max_load1": 1.0, "max_cpu_pressure": 1.0},
            warm_governor="powersave")
            if j["id"] == "warm-acceptance")
        job["verdict"].update(json=case["path"], run_id="warm-" + fault)
        verdict = matrix.check_verdict(job)
        want_pass, want_matrix = warm_expect[fault]
        check("warm %s acceptance" % fault,
              (warm.get("pass") is True) == want_pass and
              (case["exit"] == 0) == want_pass,
              "exit %r, pass %r, problems %r" % (
                  case["exit"], warm.get("pass"),
                  (warm.get("problems") or [])[:3]))
        check("warm %s matrix verdict" % fault,
              bool(verdict and verdict["ok"]) == want_matrix,
              (verdict or {}).get("detail"))

    # ---- saved warm JSON re-judged by the same validator --------------
    control_json = os.path.join(out_dir, "warm-control.json")
    verify_args = ["--verify-warm-json", control_json, "--lib", warm_lib,
                   "--warm-repeats", "5", "--warm-observations", "2000",
                   "--warm-cpu", str(warm_cpu),
                   "--warm-max-load1", "1.0",
                   "--warm-max-cpu-pressure", "1.0",
                   "--warm-governor", "powersave"]
    saved = run_case(bench, "verify-control", out_dir, str(corpus),
                     extra_args=verify_args)
    srep = saved["json"] or {}
    check("saved acceptance re-verifies",
          saved["exit"] == 0 and srep.get("pass") is True,
          "exit %r, pass %r, problems %r" % (
              saved["exit"], srep.get("pass"),
              (srep.get("problems") or [])[:3]))

    def mutated(name, change):
        path = os.path.join(out_dir, "warm-" + name + ".json")
        with open(control_json, encoding="utf-8") as f:
            doc = json.load(f)
        change(doc)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return path

    def drop_end(doc):
        for entry in (doc.get("probes") or {}).values():
            for rd in entry.get("warm_ext_repeats") or []:
                for leg in ("off", "on"):
                    rd[leg].pop("loaded_end", None)

    def nan_mean(doc):
        for entry in (doc.get("probes") or {}).values():
            for rd in entry.get("warm_ext_repeats") or []:
                rd["on"]["fill_wall_ns"]["mean"] = float("nan")

    for name, change, needle in (("missing-end", drop_end, "after loop"),
                                 ("nan", nan_mean, "finite")):
        path = mutated(name, change)
        case = run_case(bench, "verify-" + name, out_dir, str(corpus),
                        extra_args=[path if a == control_json else a
                                    for a in verify_args])
        rep = case["json"] or {}
        check("saved JSON with %s fails" % name,
              case["exit"] not in (0, None) and rep.get("pass") is False,
              "exit %r, pass %r" % (case["exit"], rep.get("pass")))
        check("saved JSON with %s names the fault" % name,
              any(needle in p for p in rep.get("problems") or []),
              "problems = %r" % ((rep.get("problems") or [])[:3],))

    # ---- stale output and SKIP -----------------------------------------
    stale_dir = os.path.join(out_dir, "stale")
    os.makedirs(stale_dir, exist_ok=True)
    stale = os.path.join(stale_dir, "bench_gate.json")
    with open(stale, "w", encoding="utf-8") as f:
        json.dump({"status": "OK", "gate": {
            "probe_states": 4, "status": "PASS",
            "maskbench_states": 425}}, f, indent=1)
    with open(stale, "rb") as f:
        stale_hash = sha256_bytes(f.read())

    skip = os.path.join(stale_dir, "skip.py")
    with open(skip, "w", encoding="utf-8") as f:
        f.write('''import importlib.util, sys
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("bench", sys.argv[1])
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
def no_core():
    print('{"status": "SKIP", "reason": "synthetic missing dependency"}')
    raise SystemExit(0)
b.bc.require_core = no_core
sys.argv = ["bench_adr0007.py", "--skip-warm-ext", "--out", sys.argv[2]]
b.main()
''')
    job = {
        "id": "bench-gate",
        "requirement": "stale output must not satisfy a SKIP run",
        "cmd": [sys.executable, "-B", skip, BENCH, stale],
        "verdict": {
            "json": stale,
            "path": ["gate", "maskbench_states"],
            "kind": "positive",
            "require_status": "OK",
            "checks": [
                {"path": ["gate", "maskbench_fill_errors"], "kind": "zero"},
                {"path": ["gate", "maskbench_validation_errors"], "kind": "zero"},
            ],
        },
    }
    row = matrix.run_job(job, stale_dir)
    with open(stale, "rb") as f:
        stale_after = sha256_bytes(f.read())
    check("SKIP is rejected", row["status"] == "fail",
          "status = %r" % row["status"])
    check("SKIP reason is recorded", bool(row.get("skipped")),
          "skipped = %r" % row.get("skipped"))
    check("stale JSON is not accepted",
          row.get("verdict") is None or row["verdict"]["ok"] is False,
          "verdict = %r" % (row.get("verdict") or {}).get("detail"))
    check("stale JSON is left untouched", stale_after == stale_hash,
          "sha256 before/after = %s/%s" % (stale_hash[:12], stale_after[:12]))
    names = {c["check"] for c in (row.get("verdict") or {}).get("checks", [])}
    check("stale JSON fails the run id check",
          "run_id" in names and "status" in names,
          "checks = %r" % sorted(names))

    # A matching run id is accepted, so the check is not "always fail".
    fresh = os.path.join(stale_dir, "fresh_gate.json")
    doc = json.loads(open(stale, encoding="utf-8").read())
    doc.update({"run_id": "fresh-1", "status": "OK"})
    doc["gate"].update({"maskbench_fill_errors": 0,
                        "maskbench_validation_errors": 0})
    with open(fresh, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    good_job = dict(job, verdict=dict(job["verdict"], json=fresh,
                                      run_id="fresh-1"))
    good = matrix.check_verdict(good_job)
    bad_job = dict(job, verdict=dict(job["verdict"], json=fresh,
                                     run_id="fresh-2"))
    bad = matrix.check_verdict(bad_job)
    check("matching run id passes", bool(good and good["ok"]),
          (good or {}).get("detail"))
    check("different run id fails", bool(bad and not bad["ok"]),
          (bad or {}).get("detail"))

    report = {"ok": all(r["ok"] for r in results),
              "repo": ROOT, "bench": BENCH, "checks": results}
    with open(os.path.join(out_dir, "selfcheck.json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print("\n%s: %d/%d checks pass; report %s" % (
        "PASS" if report["ok"] else "FAIL",
        sum(1 for r in results if r["ok"]), len(results),
        os.path.join(out_dir, "selfcheck.json")))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
