#!/usr/bin/env python3
"""T8a report: MaskBench runs of several engines on the same token streams.

Reads MaskBench output directories (one per-file status JSON per schema,
meta.txt, log.txt, the t8a_streams/ records of paired_runner.py), the
identity manifest of the run (t8a_identity.py) and the reference streams
(t8a_streams.py); nothing is run.

  --manifest PATH    identity/manifest.json of the run: the corpus index
                     (the expected files), the core, the T3 binding
  --streams PATH     the reference streams (streams.json.gz)
  --run NAME=DIR     an output directory, repeatable; the names are the
                     participants of the manifest (t8a_identity.PARTICIPANTS)
  --bolorgir NAME    the Bolorgir run (the participant of module bolorgir)
  --t2c PATH         classes of the Bolorgir errors (optional):
                     {"core_sha256": ...,
                      "protocol": {"threads": 1, "time_limit": 900, ...},
                      "entries": {"<file>": {"class": "A".."E",
                                             "status": ..., "note": ...}}};
                     a class applies only with the full core sha256 of
                     the run, a complete protocol block (PROTOCOL_KEYS:
                     threads, time_limit, mem_limit, profile, serializer,
                     compact, smoke_files, tokenizer_revision,
                     wheel_sha256) that names every condition with its
                     type and the run's value (a missing or empty block,
                     a missing, null or unknown entry, a wrong type or
                     another value keeps every case open: a class is
                     bound to the protocol it was decided for), the file
                     and the exact outcome as status:
                     validation_error, invalidation_error, timeout (a
                     pending marker whose chunk ended with exit -14, the
                     runner's SIGALRM, while it ran this file, by log.txt)
                     or crash:<exit code> (another nonzero exit). A pending
                     marker without such a chunk end, an entry without a
                     status or with another one stays open, with the
                     reason in the report
  --out-md / --out-json

Per run, with the percentile rule of MaskBench scripts/maskbench_results.py:
TTFM and TBM avg and p25..p100, tokens, schemas, passing, compile error,
validation error, invalidation error, serialization incompat, crash or
timeout (a pending marker), plus the input problems: missing outputs,
malformed records, outputs outside the corpus index. The same table on
the schemas every run compiled (the intersection). The stream check
compares, per (schema, instance), the serialized bytes and the token ids
each run recorded with the reference, invalid instances included; the
step where an engine rejected an invalid instance may differ and is only
counted.

The report is written whenever the inputs are readable. Two lists
decide the result: `t8a.problems` (the manifest is inconsistent; a
participant of the manifest has no --run, or a --run is no participant;
a run's meta.txt names another engine id, module or module version than
the manifest; an expected output is missing or malformed in a run; an
output lies outside the corpus index; a stream differs from the
reference) and `t8a.conditions` (a smoke run, no admitted T3 acceptance
of the same core, a Bolorgir validation error, invalidation error or
crash or timeout without a T2c class for this core). `inputs_complete`
is true without problems; `accepted` is true without problems and
conditions. A report of a subset of the participants is a diagnostic
and never accepted.

Smoke: for a smoke manifest, `t8a.smoke.passed` is true
only without problems and when no run of any participant has a crash
or timeout (a pending marker, or a timeout line in log.txt): a timeout
that the full report would only count is a smoke failure. A smoke
report is never an acceptance.

Exit status: 0 accepted, 1 report written but not accepted, 2 unusable
input (also duplicate --run names).
"""

import argparse
import glob
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from t8a_identity import problems as manifest_problems  # noqa: E402

PS = (25, 50, 75, 90, 95, 99, 99.9, 100)
TIMEOUT_US = 900_000_000
STATUSES = ("passing", "compile_error", "validation_error", "invalidation_error",
            "crash_or_timeout")
T2C_CLASSES = ("A", "B", "C", "D", "E")
# The complete protocol of a T2c class file (--t2c): every key is required
# with this type and must equal the run's parameter of the manifest
# (wheel_sha256: wheel.sha256), so a class applies only to the protocol it
# was decided for and a missing limit cannot widen it.
PROTOCOL_KEYS = {"threads": int, "time_limit": int, "mem_limit": int,
                 "profile": str, "serializer": str, "compact": bool,
                 "smoke_files": int, "tokenizer_revision": str, "wheel_sha256": str}


class Unusable(Exception):
    pass


def named(spec):
    name, sep, path = spec.partition("=")
    if not sep or not name:
        raise Unusable(f"expected NAME=DIR, got {spec!r}")
    return name, path


def get_p(arr, p):
    idx = int(len(arr) * p / 100)
    return arr[min(idx, len(arr) - 1)] if arr else None


def load_json(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def load_run(directory, expected):
    """Outputs of one run: {file: record or a problem marker}."""
    if not os.path.isdir(directory):
        raise Unusable(f"{directory}: no such directory")
    files, malformed = {}, []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        name = os.path.basename(path)
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            if not isinstance(rec, dict):
                raise ValueError("not an object")
        except ValueError:
            malformed.append(name)
            continue
        files[name] = rec
    streams = {}
    for path in sorted(glob.glob(os.path.join(directory, "t8a_streams", "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                streams[os.path.basename(path)] = json.load(f)
        except ValueError:
            malformed.append(f"t8a_streams/{os.path.basename(path)}")
    meta = {}
    meta_path = os.path.join(directory, "meta.txt")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    timeouts, chunk_ends = 0, {}
    log_path = os.path.join(directory, "log.txt")
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        timeouts = sum(1 for line in lines if line.strip() == "Exit code: -14")
        chunk_ends = ended_on(lines)
    exp = set(expected)
    return {"dir": directory, "files": files, "streams": streams, "meta": meta,
            "timeouts": timeouts, "chunk_ends": chunk_ends, "malformed": malformed,
            "missing": sorted(exp - set(files) - set(malformed)),
            "extra": sorted(set(files) - exp)}


def ended_on(lines):
    """{file: exit code} of the chunks of log.txt that ended with a nonzero
    exit: the file the runner was on, the last of the chunk's own files it
    printed (maskbench.runner prints a file when it writes its pending
    marker; scripts/run_maskbench.py writes one chunk as "Running command:",
    its stderr and stdout, "Exit code: N")."""
    out, own, printed = {}, set(), []
    for line in lines:
        if line.startswith("Running command: "):
            own = {w for w in line.split()[2:] if w.endswith(".json")}
            printed = []
        elif line.strip() in own:
            printed.append(line.strip())
        elif line.startswith("Exit code: "):
            try:
                code = int(line[len("Exit code: "):].strip())
            except ValueError:
                code = None
            if code and printed:
                out[os.path.basename(printed[-1])] = code
            own, printed = set(), []
    return out


def outcome_of(status, name, chunk_ends):
    """The exact outcome a T2c entry must name: the status, or for a
    pending marker timeout / crash:<exit code> by the chunk end of log.txt,
    None when no chunk ended on the file."""
    if status != "crash_or_timeout":
        return status
    code = chunk_ends.get(name)
    if code is None:
        return None
    return "timeout" if code == -14 else f"crash:{code}"


def protocol_problem(t2c, manifest):
    """Why the class file does not apply to this run's protocol, or None
    when it does: the block must name every required condition with its
    type (PROTOCOL_KEYS) and the run's value (wheel_sha256: the wheel the
    run installed); a missing or empty block, a missing, null or unknown
    entry, a wrong type or another value keeps every case open."""
    if t2c is None:
        return None
    p = t2c.get("protocol")
    if not isinstance(p, dict) or not p:
        return "the class file names no protocol"
    params = dict(manifest.get("parameters") or {})
    params["wheel_sha256"] = (manifest.get("wheel") or {}).get("sha256")
    bad = []
    for k in sorted(set(p) - set(PROTOCOL_KEYS)):
        bad.append(f"{k} is not a protocol key")
    for k, typ in sorted(PROTOCOL_KEYS.items()):
        if k not in p:
            bad.append(f"the protocol block lacks {k}")
        elif p[k] is None:
            bad.append(f"{k} is null")
        elif type(p[k]) is not typ:
            bad.append(f"{k} {p[k]!r} is not of type {typ.__name__}")
        elif p[k] != params.get(k):
            bad.append(f"{k} {p[k]!r} is not the run's {params.get(k)!r}")
    if bad:
        return "the class file is for another protocol: " + "; ".join(bad)
    return None


def open_reason(entry, outcome, core, t2c_core, protocol=None):
    if t2c_core != core:
        return ("no class file" if t2c_core is None else
                f"the class file is for core {str(t2c_core)[:16]}")
    if protocol:
        return protocol
    if entry is None:
        return "no class entry for the file"
    if entry.get("class") not in T2C_CLASSES:
        return f"class {entry.get('class')!r} is not one of {', '.join(T2C_CLASSES)}"
    if "status" not in entry:
        return "the class entry names no status"
    if outcome is None:
        return "the pending marker is not the end of a chunk in log.txt"
    if entry["status"] != outcome:
        return f"the class entry is for {entry['status']!r}, the case is {outcome!r}"
    return None


def status_of(rec):
    if rec is None:
        return None
    if "num_tests" not in rec:
        return "crash_or_timeout"  # the pending marker of a file that never finished
    if "compile_error" in rec:
        return "compile_error"
    err = rec.get("validation_error")
    if err is None:
        return "passing"
    return "invalidation_error" if "should reject" in err else "validation_error"


def stats(run, names, timeouts=0):
    counts = dict.fromkeys(STATUSES, 0)
    ttfm, masks, tokens, ser = [], [], 0, 0
    for n in names:
        rec = run["files"].get(n)
        st = status_of(rec)
        if st is None:
            continue
        counts[st] += 1
        if st == "crash_or_timeout":
            continue
        ser += rec.get("num_serialization_incompatible", 0)
        if st == "compile_error":
            continue
        ttfm.append(rec["ttfm_us"])
        masks.extend(rec.get("all_mask_us") or [])
        tokens += rec.get("num_tokens", 0)
    ttfm += [TIMEOUT_US] * timeouts
    ttfm.sort()
    masks.sort()
    out = {"schemas": len(names), "tokens": tokens, "serialization incompat": ser,
           "timeout (log)": timeouts}
    out.update({k.replace("_", " "): v for k, v in counts.items()})
    out["TBM avg"] = sum(masks) // len(masks) if masks else None
    for p in PS:
        out[f"TBM p{p}"] = get_p(masks, p)
    out["TTFM avg"] = sum(ttfm) // len(ttfm) if ttfm else None
    for p in PS:
        out[f"TTFM p{p}"] = get_p(ttfm, p)
    return out


def stream_problems(run, ref):
    """Per-test differences between the run's records and the reference:
    (problems, prefix differences on invalid instances)."""
    out, rejected_steps = [], {}
    for name, rec in sorted(run["files"].items()):
        st = status_of(rec)
        if st in (None, "crash_or_timeout", "compile_error") or name not in ref:
            continue
        side = run["streams"].get(name)
        if side is None:
            out.append(f"{name}: no stream record")
            continue
        tests = side.get("tests") or []
        if len(tests) != len(ref[name]):
            out.append(f"{name}: {len(tests)} instances recorded, {len(ref[name])} expected")
            continue
        for got, want in zip(tests, ref[name]):
            i = want["i"]
            if "serialization_incompatible" in want:
                if "serialization_incompatible" not in got:
                    out.append(f"{name}#{i}: serialized, the reference refuses it")
                continue
            if "serialization_incompatible" in got:
                out.append(f"{name}#{i}: refused by the serializer, the reference serializes it")
                continue
            for k in ("instance_sha256", "ids_sha256", "n_ids"):
                if got.get(k) != want[k]:
                    out.append(f"{name}#{i}: {k} differs")
                    break
            if not want["valid"]:
                rejected_steps[(name, i)] = got.get("rejected_at")
    return out, rejected_steps


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--streams", required=True)
    ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--bolorgir", required=True)
    ap.add_argument("--t2c", default="")
    ap.add_argument("--out-md", required=True)
    ap.add_argument("--out-json", required=True)
    args = ap.parse_args(argv)
    try:
        manifest = load_json(args.manifest)
        ref_doc = load_json(args.streams)
        expected = sorted(manifest["corpus"]["index"])
        specs = [named(s) for s in args.run]
        dup = sorted({n for n, _d in specs if [m for m, _e in specs].count(n) > 1})
        if dup:
            raise Unusable(f"--run names given more than once: {', '.join(dup)}")
        runs = {n: load_run(d, expected) for n, d in specs}
        t2c = load_json(args.t2c) if args.t2c else None
    except (Unusable, OSError, KeyError, ValueError) as e:
        print(f"t8a_report: unusable input: {e}", file=sys.stderr)
        return 2
    names = list(runs)
    if args.bolorgir not in runs:
        print(f"t8a_report: --bolorgir {args.bolorgir!r} is not a --run name", file=sys.stderr)
        return 2
    ref = ref_doc["files"]
    core = manifest["wheel"]["core_sha256"]
    problems, conditions = [], []
    parts = manifest.get("participants") or {}
    for name in sorted(set(parts) - set(runs)):
        problems.append(f"participant {name} has no --run (a report of a subset is a diagnostic)")
    for name in sorted(set(runs) - set(parts)):
        problems.append(f"--run {name} is not a participant of the manifest")
    if (parts.get(args.bolorgir) or {}).get("module") != "bolorgir":
        problems.append(f"--bolorgir {args.bolorgir} is not the bolorgir participant of the manifest")

    if manifest["parameters"].get("smoke_files"):
        conditions.append(f"smoke run of {manifest['parameters']['smoke_files']} files")
    problems += [f"manifest: {p}" for p in manifest_problems(manifest)]
    if sorted(ref) != expected:
        problems.append("the reference streams do not cover the corpus index")
    if ref_doc.get("tokenizer") != manifest["tokenizer"]["revision"]:
        problems.append("the reference streams used another tokenizer snapshot")
    acc = manifest.get("t3_acceptance")
    if not acc:
        conditions.append("no T3 acceptance bound to this run")
    elif acc["verdict"] != "admitted" or acc["candidate_library_sha256"] != core:
        conditions.append(f"T3 acceptance {acc['verdict']} for "
                          f"{str(acc['candidate_library_sha256'])[:16]}, core {core[:16]}")

    full = {n: stats(r, expected, r["timeouts"]) for n, r in runs.items()}
    compiled_by_all = [f for f in expected
                       if all(status_of(r["files"].get(f)) not in (None, "compile_error",
                                                                   "crash_or_timeout")
                              for r in runs.values())]
    inter = {n: stats(r, compiled_by_all) for n, r in runs.items()}

    per_run = {}
    prefix_diff = {}
    crashes = {}
    for n, r in runs.items():
        sp, steps = stream_problems(r, ref)
        for k, v in steps.items():
            prefix_diff.setdefault(k, {})[n] = v
        want = parts.get(n) or {}
        meta_bad = [f"{k} {r['meta'].get(k)!r}, expected {want.get(w)!r}"
                    for k, w in (("id", "engine_id"), ("module", "module"),
                                 ("module_version", "version"))
                    if not want or r["meta"].get(k) != want.get(w)]
        crashes[n] = [f for f in expected if status_of(r["files"].get(f)) == "crash_or_timeout"]
        per_run[n] = {"dir": r["dir"], "meta": r["meta"], "missing": r["missing"],
                      "malformed": r["malformed"], "extra": r["extra"],
                      "stream_problems": sp, "meta_ok": not meta_bad, "meta_problems": meta_bad,
                      "crash_or_timeout": crashes[n], "timeout_lines": r["timeouts"]}
        for label, items in (("missing outputs", r["missing"]), ("malformed records", r["malformed"]),
                             ("outputs outside the corpus index", r["extra"]),
                             ("stream differences", sp)):
            if items:
                problems.append(f"{n}: {len(items)} {label}, e.g. {items[0]}")
        if meta_bad:
            problems.append(f"{n}: meta.txt is not the participant of the manifest: "
                            + "; ".join(meta_bad))
    prefix_rows = [{"file": f, "instance": i, "rejected_at": v}
                   for (f, i), v in sorted(prefix_diff.items()) if len(set(v.values())) > 1]

    blg = runs[args.bolorgir]["files"]
    entries = (t2c or {}).get("entries") or {}
    t2c_core = (t2c or {}).get("core_sha256")
    t2c_proto = protocol_problem(t2c, manifest)
    triage = []
    for f in expected:
        st = status_of(blg.get(f))
        if st not in ("validation_error", "invalidation_error", "crash_or_timeout"):
            continue
        oc = outcome_of(st, f, runs[args.bolorgir]["chunk_ends"])
        e = entries.get(f) if t2c_core == core else None
        why = open_reason(e, oc, core, t2c_core, t2c_proto)
        detail = (blg.get(f) or {}).get("validation_error")
        if st == "crash_or_timeout":
            code = runs[args.bolorgir]["chunk_ends"].get(f)
            detail = None if code is None else f"chunk exit {code}"
        triage.append({"file": f, "status": st, "outcome": oc, "detail": detail,
                       "class": None if why else e["class"], "open_reason": why,
                       "note": (e or {}).get("note")})
    open_rows = [t for t in triage if t["class"] is None]
    if open_rows:
        note = ""
        if t2c_core not in (None, core):
            note = f" (the class file is for {str(t2c_core)[:16]})"
        elif t2c_proto:
            note = f" ({t2c_proto})"
        conditions.append(f"{len(open_rows)} Bolorgir errors without a T2c class for this core"
                          + note)

    reasons = problems + conditions
    smoke = None
    if manifest["parameters"].get("smoke_files"):
        failures = list(problems)
        for n in names:
            if crashes[n]:
                failures.append(f"{n}: {len(crashes[n])} crash or timeout, e.g. {crashes[n][0]}")
            if runs[n]["timeouts"]:
                failures.append(f"{n}: {runs[n]['timeouts']} timeout lines in log.txt")
        smoke = {"passed": not failures, "failures": failures}
    out = {"t8a": {"accepted": not reasons, "inputs_complete": not problems,
                   "problems": problems, "conditions": conditions, "report_written": True,
                   "smoke": smoke},
           "core_sha256": core, "manifest": {"path": args.manifest},
           "runs": per_run, "full": full,
           "intersection": {"schemas": len(compiled_by_all), "stats": inter},
           "stream_check": {"reference_files": len(ref),
                            "invalid_instances_rejected_at_different_steps": len(prefix_rows),
                            "prefix_rows": prefix_rows[:200]},
           "bolorgir_errors": triage}
    with open(args.out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
        f.write("\n")

    def table(rows):
        keys = list(rows[names[0]])
        lines = ["| metric | " + " | ".join(names) + " |", "|---|" + "---:|" * len(names)]
        for k in keys:
            cells = []
            for n in names:
                v = rows[n].get(k)
                cells.append("-" if v is None else f"{v:,}" if isinstance(v, int) else str(v))
            lines.append(f"| {k} | " + " | ".join(cells) + " |")
        return lines

    md = ["# MaskBench, spec-v1: paired runs (T8a)", "",
          f"**T8a accepted: {'yes' if not reasons else 'no'}** (core `{core[:16]}`).", ""]
    if smoke is not None:
        md += [f"**Smoke passed: {'yes' if smoke['passed'] else 'no'}**"
               " (a smoke is never an acceptance).", ""]
        if smoke["failures"]:
            md += ["Smoke failures:", ""] + [f"- {r}" for r in smoke["failures"]] + [""]
    if reasons:
        md += ["Reasons:", ""] + [f"- {r}" for r in reasons] + [""]
    md += ["Generated by `benchmarks/maskbench/t8a/t8a_report.py` from the MaskBench",
           "output directories. Times in microseconds; percentiles as in MaskBench",
           "`scripts/maskbench_results.py`.", "", "## Runs", "",
           "| name | engine id | module version | missing | malformed | outside index | "
           "stream differences |", "|---|---|---|---:|---:|---:|---:|"]
    for n in names:
        p = per_run[n]
        md.append(f"| {n} | {p['meta'].get('id', '-')} | {p['meta'].get('module_version', '-')} | "
                  f"{len(p['missing'])} | {len(p['malformed'])} | {len(p['extra'])} | "
                  f"{len(p['stream_problems'])} |")
    md += ["", f"## Full corpus ({len(expected)} files of the corpus index)", ""] + table(full)
    md += ["", f"## Schemas every engine compiled ({len(compiled_by_all)})", ""] + table(inter)
    md += ["", "## Token streams", "",
           f"Every run's serialized bytes and token ids were compared with the reference for "
           f"{len(ref)} files, invalid instances included. Invalid instances rejected at "
           f"different steps by different engines: {len(prefix_rows)}.", ""]
    md += [f"## Bolorgir validation errors, invalidation errors, crashes ({len(triage)})", "",
           "Each case needs a T2c class for this core (audit-fix spec T8a acceptance).", ""]
    if triage:
        md += ["| file | status | outcome | class | detail |", "|---|---|---|---|---|"]
        md += [f"| {t['file']} | {t['status']} | {t['outcome'] or '-'} | "
               f"{t['class'] or 'OPEN: ' + t['open_reason']} | "
               f"{str(t['detail']).replace('|', '/')} |" for t in triage]
    with open(args.out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(json.dumps({"accepted": not reasons, "smoke": smoke, "reasons": reasons[:20],
                      "intersection": len(compiled_by_all)}, indent=1))
    return 0 if not reasons else 1


if __name__ == "__main__":
    sys.exit(main())
