"""ADR-0007 Decision 4 acceptance matrix.

Runs the correctness rows and the warm guard in the configurations
Decision 4/5 fix, and writes a machine-readable matrix plus a Markdown
report. Verdicts are invocation-bound: each row gets a unique
`BLG_RUN_ID`, and `check_verdict` requires that id, the expected status,
the row's counters, the warm protocol/conditions and the library SHA256
from the written JSON. A stale JSON or a SKIP exit cannot satisfy a row.
Failing masks and fuzz artifacts are copied to the artifacts directory.
`--scale quick` bounds the campaigns; `--scale full` uses the ADR scale
(600 differential schemas). See docs/adr/ADR-0007-mask-fast-path.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEFAULT_PY = os.path.join(ROOT, "benchmarks", "local", "t8a-work-9a5baf3e",
                          "venv-pinned", "bin", "python")
DEFAULT_ZIG = os.path.join(ROOT, "benchmarks", "local", "toolchain",
                           "zig-x86_64-linux-0.17.0", "zig")

# The suites read their campaign size from different variables; `full`
# leaves the fast-path and cache campaigns at the suites' own defaults and
# only fixes the differential campaign at the ADR's 600 schemas.
SCALE = {
    "quick": {"fuzz_diff": "40", "fuzz_fastpath": "40", "fuzz_cache": "40",
              "pytest_args": ["-x", "-q"]},
    "full": {"fuzz_diff": "600", "fuzz_fastpath": None, "fuzz_cache": None,
             "pytest_args": ["-q"]},
}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def jobs(scale, py, zig, artifacts, warm_cpu=None, warm_limits=None,
         warm_governor=None):
    sc = SCALE[scale]
    limits = {"max_load1": 1.0, "max_cpu_pressure": 1.0}
    limits.update(warm_limits or {})
    base_env = {"BLG_MASK_FAST_PATH": ""}
    lib_sha = None
    if os.environ.get("BLG_LIB_PATH"):
        lib_sha = _sha256(os.environ["BLG_LIB_PATH"])
    gate_checks = [
        {"path": ["gate", "maskbench_instances"], "kind": "positive"},
        {"path": ["gate", "maskbench_terminal_states"],
         "kind": "positive"},
        {"path": ["gate", "maskbench_terminal_complete"], "kind": "true"},
        {"path": ["gate", "maskbench_fill_errors"], "kind": "zero"},
        {"path": ["gate", "maskbench_terminal_fill_errors"],
         "kind": "zero"},
        {"path": ["gate", "maskbench_input_errors"], "kind": "zero"},
        {"path": ["gate", "maskbench_completion_errors"], "kind": "zero"},
        {"path": ["gate", "maskbench_validation_errors"], "kind": "zero"},
    ]
    warm_checks = [
        {"path": ["warm_acceptance", "protocol", "repeats"],
         "kind": "equals", "value": 5},
        {"path": ["warm_acceptance", "protocol", "observations"],
         "kind": "equals", "value": 2000},
        {"path": ["warm_acceptance", "protocol", "threshold_ns"],
         "kind": "equals", "value": 1300},
        {"path": ["warm_acceptance", "protocol", "cpu"],
         "kind": "equals", "value": warm_cpu},
        {"path": ["warm_acceptance", "conditions", "ok"],
         "kind": "true"},
        {"path": ["warm_acceptance", "conditions", "complete"],
         "kind": "true"},
        {"path": ["warm_acceptance", "conditions", "limits",
                  "max_load1"], "kind": "equals",
         "value": limits["max_load1"]},
        {"path": ["warm_acceptance", "conditions", "limits",
                  "max_cpu_pressure_avg10"], "kind": "equals",
         "value": limits["max_cpu_pressure"]},
    ]
    if warm_governor is not None:
        warm_checks.append(
            {"path": ["warm_acceptance", "conditions", "limits",
                      "expected_governor"], "kind": "equals",
             "value": warm_governor})
    warm_cmd = [py, "benchmarks/bench_adr0007.py", "--skip-maskbench",
                "--cold-repeats", "1", "--warm-observations", "2000",
                "--warm-repeats", "5", "--mask-artifacts", artifacts,
                "--warm-max-load1", str(limits["max_load1"]),
                "--warm-max-cpu-pressure", str(limits["max_cpu_pressure"]),
                "--out", os.path.join(artifacts, "warm_acceptance.json")]
    if warm_governor is not None:
        warm_cmd += ["--warm-governor", warm_governor]
    if warm_cpu is not None:
        warm_cmd[2:2] = ["--warm-cpu", str(warm_cpu)]
    return [
        {
            "id": "zig-three-way",
            "requirement": "Decision 4: Zig unit tests, three-way "
                           "fast/trie/brute comparison on random "
                           "vocabularies",
            # `zig test` builds and always runs the binary (`zig build
            # test` can report `run test cached`), with the same options
            # module `build.zig` gives its test module.
            "cmd": [zig, "test", "-O", "ReleaseSafe", "-fllvm", "--dep",
                    "build_options", "-Mroot=src/tests.zig",
                    "-Mbuild_options=" + os.path.join(artifacts, "matrix_build_options.zig")],
            "env": {},
            "options_file": os.path.join(artifacts, "matrix_build_options.zig"),
        },
        {
            "id": "fastpath-curated",
            "requirement": "Decision 4: curated ABI cases over fast path "
                           "on/off, lazy/adaptive and max_workers "
                           "{1,2,8} (long tokens, value boundaries, "
                           "enclosure scope, length limits, Unicode)",
            "cmd": [py, "-m", "pytest", "tests/test_mask_fastpath.py"]
                   + sc["pytest_args"],
            "env": base_env,
            "collect": [py, "-m", "pytest", "--collect-only", "-q",
                        "tests/test_mask_fastpath.py"],
        },
        {
            "id": "fastpath-fuzz",
            "requirement": "Decision 4: randomized fast-path on/off "
                           "equality (tests/test_mask_fastpath_fuzz.py)",
            "cmd": [py, "-m", "pytest", "tests/test_mask_fastpath_fuzz.py"]
                   + sc["pytest_args"],
            "env": dict(base_env, BLG_FUZZ_DIFF_FP=sc["fuzz_fastpath"])
                   if sc["fuzz_fastpath"] else base_env,
        },
        {
            "id": "differential-reference",
            "requirement": "Decision 4: kernel vs tests/reference.py "
                           "differential campaign over the same schemas "
                           "and walk seeds in four configurations "
                           "(cache off/on x fast path on/off), with "
                           "per-configuration counters and trace "
                           "digests",
            "cmd": [py, "-m", "pytest",
                    "tests/fuzz/test_fuzz_differential.py"]
                   + sc["pytest_args"],
            "env": dict(base_env, BLG_FUZZ_DIFF=sc["fuzz_diff"]),
        },
        {
            "id": "cache-onoff",
            "requirement": "Decision 4: every comparison repeated with the "
                           "session cache on and off "
                           "(tests/fuzz/test_fuzz_cache.py)",
            "cmd": [py, "-m", "pytest", "tests/fuzz/test_fuzz_cache.py"]
                   + sc["pytest_args"],
            "env": dict(base_env, BLG_FUZZ_CACHE=sc["fuzz_cache"])
                   if sc["fuzz_cache"] else base_env,
        },
        {
            "id": "bench-gate",
            "requirement": "Decision 4: on/off mask equality gate on the "
                           "pinned probe states AND the pinned MaskBench "
                           "slice (mandatory at every scale); timings only "
                           "after it",
            # No --skip-maskbench: the pinned trace-equality gate is part
            # of this row, not an optional extra. The verdict below also
            # requires its state count from the written JSON, so a
            # re-introduced skip cannot make the row green.
            # The warm guard has its own row with the fixed protocol; this
            # row must fail on mask divergence, not on timing noise from a
            # single exploratory round.
            "cmd": [py, "benchmarks/bench_adr0007.py", "--skip-warm-ext",
                    "--cold-repeats", "1", "--warm-observations", "200",
                    "--mask-artifacts", artifacts,
                    "--out", os.path.join(artifacts, "bench_gate.json")],
            "env": {},
            "verdict": {
                "json": os.path.join(artifacts, "bench_gate.json"),
                "path": ["gate", "maskbench_states"],
                "kind": "positive",
                "require_status": "OK",
                "lib_sha256": lib_sha,
                "checks": gate_checks,
            },
        },
        {
            "id": "warm-acceptance",
            "requirement": "ADR-0007 warm guard: five repeats x 2000 hits "
                           "per probe, alternating leg order, every "
                           "warm_ext.on mean <= threshold, hash and hits "
                           "mandatory, fixed CPU placement and recorded "
                           "frequency/load conditions",
            "cmd": warm_cmd,
            "env": {},
            "verdict": {
                "json": os.path.join(artifacts, "warm_acceptance.json"),
                "path": ["warm_acceptance", "pass"],
                "kind": "true",
                "require_status": "OK",
                "lib_sha256": lib_sha,
                "checks": warm_checks,
            },
        },
    ]


def _json_get(doc, path):
    node = doc
    for key in path:
        node = node[key]
    return node


def _check_value(doc, path, kind, value=None):
    label = ".".join(str(k) for k in path) or "(root)"
    try:
        node = _json_get(doc, path)
    except Exception as exc:
        return False, "%s: %s: %s" % (label, type(exc).__name__, exc)
    if kind == "true":
        ok = node is True
    elif kind == "positive":
        ok = type(node) is int and node > 0
    elif kind == "zero":
        ok = type(node) is int and node == 0
    elif kind == "equals":
        ok = node == value
    elif kind == "nonempty":
        ok = bool(node)
    else:
        return False, "%s: unknown verdict kind %r" % (label, kind)
    return ok, "%s = %r" % (label, node)


def check_verdict(job):
    """Read and validate a job's published JSON verdict (ADR-0007 D4).

    A zero exit code is not enough, and neither is an old report: the JSON
    must carry the run id this job was started with (`run_job` puts it in
    BLG_RUN_ID), the expected status, the primary path and every extra
    check. Extra checks cover the zero-error counters, the warm protocol
    parameters and the SHA256 of the library under test. A missing file,
    a missing key, a false boolean, a non-positive count or a stale run id
    fails the row on its own.
    """
    spec = job.get("verdict")
    if not spec:
        return None
    path = spec.get("json")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as exc:
        return {"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc),
                "checks": [], "json": path}
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    expect_run = spec.get("run_id")
    if expect_run is not None:
        got = doc.get("run_id")
        add("run_id", got == expect_run,
            "run_id = %r (expected %r)" % (got, expect_run))
    elif not spec.get("allow_unbound"):
        # A caller that did not go through run_job has no freshness
        # guarantee: an old file could satisfy every other check. Reject
        # it unless the caller explicitly opts out (`allow_unbound`).
        add("run_id", False,
            "no run id: the JSON was not produced by this invocation")
    want_status = spec.get("require_status")
    if want_status is not None:
        add("status", doc.get("status") == want_status,
            "status = %r (expected %r)" % (doc.get("status"), want_status))
    ok, detail = _check_value(doc, spec.get("path") or [],
                              spec.get("kind", "true"))
    add(".".join(spec.get("path") or []), ok, detail)
    for check in spec.get("checks") or []:
        ok, detail = _check_value(doc, check.get("path") or [],
                                  check.get("kind", "true"),
                                  check.get("value"))
        add(".".join(check.get("path") or []), ok, detail)
    want_lib = spec.get("lib_sha256")
    if want_lib:
        got = _json_get(doc, ["environment", "lib_sha256"])
        add("environment.lib_sha256", got == want_lib,
            "environment.lib_sha256 = %r (expected %r)" % (got, want_lib))
    return {"ok": all(c["ok"] for c in checks),
            "detail": "; ".join(("ok " if c["ok"] else "FAIL ") + c["detail"]
                                for c in checks),
            "checks": checks, "json": path}


def _skip_reason(text):
    """The reason a benchmark reported SKIP, or None.

    bench_common.skip prints one JSON object with status SKIP and exits 0;
    a matrix row must never read that as success (or fall back to an older
    output file).
    """
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("status") == "SKIP":
            return str(obj.get("reason") or "SKIP")
    return None


def run_job(job, log_dir):
    if job.get("options_file"):
        with open(job["options_file"], "w", encoding="utf-8") as f:
            f.write("pub const test_hooks = true;\n")
            f.write("pub const diag_counters = %s;\n"
                    % ("true" if os.environ.get(
                        "BLG_MATRIX_DIAG_COUNTERS", "1") != "0" else "false"))
    env = dict(os.environ)
    for k, v in (job.get("env") or {}).items():
        if v == "":
            env.pop(k, None)
        else:
            env[k] = v
    run_id = None
    if job.get("verdict"):
        # Freshness is bound to this invocation: the job must write the
        # file with exactly this id, so a retained JSON from an earlier
        # run can never satisfy the verdict.
        run_id = "%s-%d-%d" % (job["id"], os.getpid(), time.time_ns())
        job["verdict"]["run_id"] = run_id
        env["BLG_RUN_ID"] = run_id
    t0 = time.time()
    proc = subprocess.run(job["cmd"], cwd=ROOT, env=env,
                          capture_output=True, text=True)
    dur = time.time() - t0
    log = os.path.join(log_dir, job["id"] + ".log")
    with open(log, "w", encoding="utf-8") as f:
        f.write("$ " + " ".join(job["cmd"]) + "\n\n")
        f.write(proc.stdout)
        f.write("\n---- stderr ----\n")
        f.write(proc.stderr)
        f.write("\n---- exit %d, %.1f s ----\n" % (proc.returncode, dur))
    import re as _re
    out = proc.stdout + "\n" + proc.stderr  # zig test reports on stderr
    # pytest summary, or the Zig test summary ("326/326 tests passed").
    m = _re.search(r"(\d+)/(\d+) tests passed", out)
    if m:
        passed = int(m.group(2))
    else:
        m = _re.search(r"All (\d+) tests passed", out)
        if not m:
            m = _re.search(r"(\d+) passed", out)
        passed = int(m.group(1)) if m else 0
    m = _re.search(r"(\d+) failed", out)
    failed = int(m.group(1)) if m else 0
    skip_reason = _skip_reason(out)
    verdict = check_verdict(job)
    if verdict is not None:
        with open(log, "a", encoding="utf-8") as f:
            f.write("---- verdict %s (ok=%s) ----\n"
                    % (verdict["detail"], verdict["ok"]))
    collected = []
    if job.get("collect"):
        cp = subprocess.run(job["collect"], cwd=ROOT, env=env,
                            capture_output=True, text=True)
        collected = [ln.strip() for ln in cp.stdout.splitlines()
                     if "::" in ln]
        with open(os.path.join(log_dir, job["id"] + ".collected.txt"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(collected) + "\n")
    ok = (proc.returncode == 0 and skip_reason is None and
          (verdict is None or verdict["ok"]))
    return {
        "id": job["id"],
        "requirement": job["requirement"],
        "run_id": run_id,
        "status": "pass" if ok else "fail",
        "exit": proc.returncode,
        "duration_s": round(dur, 1),
        "skipped": skip_reason,
        "collected_cases": len(collected),
        "tests_passed": passed,
        "tests_failed": failed,
        "verdict": verdict,
        "collect_sample": collected[:5],
        "log": os.path.relpath(log, ROOT),
        "tail": (proc.stdout + proc.stderr).strip().splitlines()[-6:],
    }


def summarise_warm(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return d.get("warm_acceptance")


def snapshot_fuzz_artifacts():
    src = os.path.join(ROOT, "tests", "fuzz", "artifacts")
    if not os.path.isdir(src):
        return {}
    return {name: os.path.getmtime(os.path.join(src, name))
            for name in os.listdir(src)
            if os.path.isfile(os.path.join(src, name))}


def copy_fuzz_artifacts(artifacts, before):
    """Copy only artifacts this run produced (new or rewritten files)."""
    src = os.path.join(ROOT, "tests", "fuzz", "artifacts")
    if not os.path.isdir(src):
        return []
    dst = os.path.join(artifacts, "fuzz-artifacts")
    os.makedirs(dst, exist_ok=True)
    copied = []
    for name in sorted(os.listdir(src)):
        s = os.path.join(src, name)
        if not os.path.isfile(s):
            continue
        if before.get(name) == os.path.getmtime(s):
            continue
        shutil.copy2(s, os.path.join(dst, name))
        copied.append(name)
    return copied


def write_report(out_dir, results, warm, fuzz_artifacts, scale, lib, only=None,
                 warm_cpu=None, warm_limits=None, warm_governor=None):
    report = {"scale": scale, "lib": lib, "generated_utc": time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "jobs": results,
        "warm_acceptance": warm, "fuzz_artifacts": fuzz_artifacts,
        "warm_cpu": warm_cpu, "warm_limits": warm_limits,
        "warm_governor": warm_governor, "only": only}
    with open(os.path.join(out_dir, "matrix.json"), "w",
              encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    lines = ["# ADR-0007 Decision 4 acceptance matrix", "",
             "Scale: `%s`; library: `%s`; generated %s UTC" %
             (scale, lib or "zig-out build", report["generated_utc"]), ""]
    warm_ran = (warm is not None or
                any(r["id"] == "warm-acceptance" for r in results))
    if warm_cpu is not None and warm_ran:
        lines += ["Fixed warm CPU placement: `%d`" % warm_cpu, ""]
    if warm_limits and warm_ran:
        lines += ["Warm condition limits: load1 <= %s, CPU pressure "
                  "avg10 <= %s %%" % (warm_limits.get("max_load1"),
                                       warm_limits.get("max_cpu_pressure")),
                  ""]
    if warm_governor is not None and warm_ran:
        lines += ["Warm governor policy: `%s`" % warm_governor, ""]
    lines += ["| job | status | tests | parametrised | s | requirement |",
              "|---|---|---:|---:|---:|---|"]
    for r in results:
        lines.append("| `%s` | %s | %d | %d | %.1f | %s |" % (
            r["id"], r["status"], r["tests_passed"],
            r["collected_cases"], r["duration_s"], r["requirement"]))
    if only:
        lines += ["", "Selection: `--only` ran %d of the matrix jobs; the "
                  "Overall line below covers only these rows, not the full "
                  "Decision 4 set." % len(results)]
    failing = [r for r in results if r["status"] != "pass"]
    lines += ["", "Overall: **%s** (%d/%d %sjobs pass)" % (
        "PASS" if not failing else "FAIL", len(results) - len(failing),
        len(results), "selected " if only else "")]
    checked = [r for r in results if r.get("verdict")]
    if checked:
        lines += ["", "## JSON verdicts", ""]
        for r in checked:
            lines.append("- `%s`: %s (%s)" % (
                r["id"], "ok" if r["verdict"]["ok"] else "**FAIL**",
                r["verdict"]["detail"]))
    if warm:
        lines += ["", "## Warm guard", "",
                  "Threshold %d ns; on-leg means (ns): %s; on-leg p50 (ns): "
                  "%s; pass: **%s**" % (
                      warm["threshold_ns"],
                      ", ".join(str(int(x)) for x in warm["on_means_ns"]),
                      ", ".join(str(int(x)) for x in warm["on_p50_ns"]),
                      warm["pass"]), ""]
        if warm.get("problems"):
            lines += ["Problems:", ""] + ["- " + p for p in warm["problems"]]
    lines += ["", "Fuzz artifacts produced by this run: " +
              (", ".join("`%s`" % n for n in fuzz_artifacts)
               if fuzz_artifacts else "none")]
    with open(os.path.join(out_dir, "matrix.md"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", choices=sorted(SCALE), default="quick")
    ap.add_argument("--out", default=None,
                    help="report directory (default "
                         "benchmarks/local/matrix-adr0007-<utc>)")
    ap.add_argument("--artifacts", default=None,
                    help="failure artifacts (default <out>/artifacts)")
    ap.add_argument("--lib", default=os.environ.get("BLG_LIB_PATH"),
                    help="library under test (exported as BLG_LIB_PATH)")
    ap.add_argument("--python", default=DEFAULT_PY)
    ap.add_argument("--zig", default=DEFAULT_ZIG)
    ap.add_argument("--only", nargs="*", default=None,
                    help="run only these job ids")
    ap.add_argument("--warm-cpu", type=int,
                    default=int(os.environ["BLG_WARM_CPU"])
                    if os.environ.get("BLG_WARM_CPU") else None,
                    help="fixed CPU placement for the warm acceptance row "
                         "(also BLG_WARM_CPU); without it the warm row "
                         "refuses to accept a measurement")
    ap.add_argument("--warm-max-load1", type=float, default=1.0,
                    help="warm acceptance: max 1-minute load average in "
                         "each leg")
    ap.add_argument("--warm-max-cpu-pressure", type=float, default=1.0,
                    help="warm acceptance: max /proc/pressure/cpu "
                         "some.avg10 (percent) in each leg")
    ap.add_argument("--warm-governor", default=None,
                    help="warm acceptance: expected scaling governor of "
                         "the pinned CPU at the start and the end of "
                         "every leg")
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = args.out or os.path.join(ROOT, "benchmarks", "local",
                                       "matrix-adr0007-" + stamp)
    artifacts = args.artifacts or os.path.join(out_dir, "artifacts")
    os.makedirs(artifacts, exist_ok=True)
    if args.lib:
        os.environ["BLG_LIB_PATH"] = args.lib
    print("matrix: scale=%s out=%s" % (args.scale, out_dir),
          file=sys.stderr, flush=True)
    fuzz_before = snapshot_fuzz_artifacts()
    results = []
    warm_limits = {"max_load1": args.warm_max_load1,
                   "max_cpu_pressure": args.warm_max_cpu_pressure}
    for job in jobs(args.scale, args.python, args.zig, artifacts,
                    warm_cpu=args.warm_cpu, warm_limits=warm_limits,
                    warm_governor=args.warm_governor):
        if args.only and job["id"] not in args.only:
            continue
        print("job %s ..." % job["id"], file=sys.stderr, flush=True)
        r = run_job(job, out_dir)
        print("  %s in %.1f s" % (r["status"], r["duration_s"]),
              file=sys.stderr, flush=True)
        results.append(r)
    warm = summarise_warm(os.path.join(artifacts, "warm_acceptance.json"))
    fuzz_art = copy_fuzz_artifacts(artifacts, fuzz_before)
    rep = write_report(out_dir, results, warm, fuzz_art, args.scale,
                       args.lib, args.only, warm_cpu=args.warm_cpu,
                       warm_limits=warm_limits,
                       warm_governor=args.warm_governor)
    print("%s; report %s" % (
        "PASS" if all(r["status"] == "pass" for r in results) else "FAIL",
        os.path.join(out_dir, "matrix.md")), file=sys.stderr, flush=True)
    return 0 if all(r["status"] == "pass" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
