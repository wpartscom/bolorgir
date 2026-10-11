#!/usr/bin/env python3
"""t8a_load_report.py DIR: the scenarios of a static T8a load estimate
(t8a_load.py output DIR: files.jsonl.gz, calib.json, inputs.json); writes
DIR/summary.json and DIR/summary.md. A scenario estimate, not a count of
guaranteed timeouts, and its ranges are no guaranteed bounds of the
duration.

Step costs per position class come from the calibration (the measured
all_mask_us of the Bolorgir outputs given to t8a_load.py --calib).
outside, key_closed and str_enum steps were fast in the whole calibration
sample; key_open, str_free, str_pattern, str_format and str_maxlen ("open"
classes) took about 1 s in some files and were fast in others; an exact
repeat of a prefix an earlier instance of the file measured was fast in
most calibration files and slow in Github_medium---o65503 (diagnosis). A
fast repeat does not show a cache hit; the mechanism is not established.
The scenarios:

  S1  every open-class step costs the median slow step (> 100 ms)
  S2  as S1, but exact prefix repeats of open classes cost the mean of
      the fast classes
  S3  every (class, repeat) costs its calibration mean; classes without
      calibration steps take the str_free means

A file's time is the sum of its steps (compile and serialization are
milliseconds and left out); above 900 s it is a timeout (the runner's
SIGALRM for the whole file) and costs 900 s. Files whose compile failed in
T3 on the same core count 0 steps (an assumption for the Llama tokenizer,
tested on the calibration files only). Risk files (an instance ended with
RESOURCE_LIMIT or CANCELLED in T3) are given both ways: "hang" (a timeout,
as Github_easy---o89710) and "no hang" (the instance ends at that step;
the time of the call that ended with the error is not counted, so
o89710 counts 0 steps there, which is not a zero cost). Chunk starts cost
RESTART_S each: one per 100 files and one after each timeout (the chunk
process ends with the file).

Validation: per calibration file, the scenario cost of its measured steps
against their measured sum; S3 leaves the file out of its own means.
"""

import gzip
import hashlib
import json
import math
import os
import statistics
import sys

OPEN = ("key_open", "str_free", "str_pattern", "str_format", "str_maxlen")
FAST = ("outside", "key_closed", "str_enum")
LIMIT_S = 900.0
RESTART_S = 3.5   # diag walls 903.556 and 903.467 s for a 900 s alarm
CHUNK = 100
SLOW_US = 100_000
KNOWN = ("Github_medium---o65503.json", "Github_easy---o89710.json")
BUCKETS = ("0 s", "< 1 s", "1-10 s", "10-60 s", "1-5 min", "5-15 min", "> 900 s (timeout)")


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))] if v else None


def costs_from(steps):
    """[steps, sum us, slow steps] per (class, repeat) and the slow steps."""
    agg = {}
    slow = []
    for _i, _k, cl, us, rep in steps:
        a = agg.setdefault((cl, bool(rep)), [0, 0, 0])
        a[0] += 1
        a[1] += us
        a[2] += us > SLOW_US
        if us > SLOW_US:
            slow.append(us)
    return agg, slow


def scenario_costs(agg, slow_median):
    means = {k: v[1] / v[0] for k, v in agg.items()}
    fast_mean = statistics.mean([means[(c, r)] for c in FAST for r in (False, True)
                                 if (c, r) in means] or [0])
    s = {"S1": {}, "S2": {}, "S3": {}}
    for c in OPEN + FAST:
        for r in (False, True):
            own = means.get((c, r), fast_mean) if c in FAST else None
            s["S1"][(c, r)] = slow_median if c in OPEN else own
            s["S2"][(c, r)] = (own if c in FAST else fast_mean if r else slow_median)
            if (c, r) in means:
                s["S3"][(c, r)] = means[(c, r)]
            else:
                s["S3"][(c, r)] = (means.get(("str_free", r), slow_median) if c in OPEN
                                   else fast_mean)
    return s


def file_time_s(rec, cost):
    t = 0.0
    for ins in rec["instances"]:
        rep = ins.get("counts_repeat") or {}
        for c, n in (ins.get("counts") or {}).items():
            nr = rep.get(c, 0)
            t += (n - nr) * cost[(c, False)] + nr * cost[(c, True)]
    return t / 1e6


def is_risk(rec):
    return any("risk" in ins for ins in rec["instances"])


def compiled(rec):
    return rec.get("t3_status") in ("OK", None)


def bucket(s):
    for lim, name in zip((0, 1, 10, 60, 300, LIMIT_S), BUCKETS):
        if s <= lim:
            return name
    return BUCKETS[-1]


def run(files, cost, hang):
    per = {}
    for r in files:
        if not compiled(r):
            per[r["file"]] = 0.0
        elif hang and is_risk(r):
            per[r["file"]] = math.inf
        else:
            per[r["file"]] = file_time_s(r, cost)
    timeouts = sum(1 for s in per.values() if s > LIMIT_S)
    busy = sum(min(s, LIMIT_S) for s in per.values())
    starts = math.ceil(len(files) / CHUNK) + timeouts
    total = busy + starts * RESTART_S
    return per, {"timeouts": timeouts, "seconds": round(total, 1),
                 "days": round(total / 86400, 2), "chunk_starts": starts}


def composition(files):
    comp = {"files": len(files), "t3_compile_ok": sum(compiled(r) for r in files),
            "t3_compile_failed": sum(not compiled(r) for r in files),
            "compiled_without_tests": sum(compiled(r) and not r["instances"] for r in files)}
    inst = {"valid": 0, "invalid": 0, "valid_refused": 0, "invalid_refused": 0}
    toks = {"valid_ids": 0, "invalid_ids": 0, "valid_steps": 0, "invalid_steps": 0}
    steps_from, cls = {}, {"valid": {}, "invalid": {}}
    risk = {"files": 0, "instances": 0, "files_at_byte_0": 0, "outcomes": {}}
    open_steps = {}
    for r in files:
        if not compiled(r):
            continue
        rf = False
        for ins in r["instances"]:
            v = "valid" if ins["valid"] else "invalid"
            inst[v] += 1
            if ins.get("serialization_incompatible"):
                inst[v + "_refused"] += 1
                continue
            toks[v + "_ids"] += ins["n_ids"]
            toks[v + "_steps"] += ins["steps"]
            steps_from[ins["steps_from"]] = steps_from.get(ins["steps_from"], 0) + 1
            for c, n in (ins.get("counts") or {}).items():
                cls[v][c] = cls[v].get(c, 0) + n
            if "risk" in ins:
                risk["instances"] += 1
                o = ins["risk"]["outcome"]
                risk["outcomes"][o] = risk["outcomes"].get(o, 0) + 1
                rf = True
        if rf:
            risk["files"] += 1
            risk["files_at_byte_0"] += any((ins.get("risk") or {}).get("byte") == 0
                                           for ins in r["instances"])
        open_steps[r["file"]] = sum(n for ins in r["instances"]
                                    for c, n in (ins.get("counts") or {}).items() if c in OPEN)
    return comp, inst, toks, steps_from, cls, risk, open_steps


def validate(calib, sc):
    val = {n: {"files": 0, "pred_s": 0.0, "meas_s": 0.0, "pred_ge_meas": 0, "ratios": []}
           for n in sc}
    rows = []
    good = [c for c in calib if not c["problem"] and c["steps"]]
    for c in good:
        meas = sum(s[3] for s in c["steps"]) / 1e6
        agg_lo, slow_lo = costs_from([s for o in good if o is not c for s in o["steps"]])
        loo = scenario_costs(agg_lo, pct(slow_lo, 0.5))["S3"]
        row = {"file": c["file"], "measured_s": round(meas, 3)}
        for n in sc:
            cost = loo if n == "S3" else sc[n]
            pred = sum(cost[(s[2], bool(s[4]))] for s in c["steps"]) / 1e6
            v = val[n]
            v["files"] += 1
            v["pred_s"] += pred
            v["meas_s"] += meas
            v["pred_ge_meas"] += pred >= meas
            if meas > 1:
                v["ratios"].append(pred / meas)
            row[n] = round(pred, 3)
        rows.append(row)
    for v in val.values():
        r_ = v.pop("ratios")
        v["ratio_of_sums"] = round(v["pred_s"] / v["meas_s"], 3) if v["meas_s"] else None
        v["ratio_median_files_over_1s"] = round(pct(r_, 0.5), 3) if r_ else None
        v["ratio_p10_p90"] = [round(pct(r_, 0.1), 3), round(pct(r_, 0.9), 3)] if r_ else None
        v["pred_s"], v["meas_s"] = round(v["pred_s"], 1), round(v["meas_s"], 1)
    return val, rows


def fmt(n):
    return f"{n:,}".replace(",", " ")


def write_md(path, out):
    s = out
    sc = s["scenarios"]
    cal = s["calibration"]
    L = ["# T8a static load estimate: blg-spec-v1, core "
         f"{s['inputs']['core_sha256'][:16]}", "",
         "**A scenario estimate, not a count of guaranteed timeouts; the ranges are no "
         "guaranteed bounds of the duration.** The engine was not run. Only the participant "
         "blg-spec-v1, one worker; the four other participants are not estimated.", "",
         "## Inputs", ""]
    i = s["inputs"]
    L += [f"- command: `{' '.join(i['argv'])}` (cwd `{i['cwd']}`)",
          f"- report: `t8a_load_report.py` {s['report_sha256']}",
          f"- t8a_load.py {i['script_sha256']}; manifest {i['manifest_sha256']}; reference "
          f"streams {i['streams_sha256']}; T3 coverage {i['t3_sha256']} (library "
          f"{i['t3_library_sha256']}, the manifest's core); wheel {i['wheel_sha256']}; "
          f"serializer {i['serializer_sha256']}; tokenizer revision {i['tokenizer_revision']}",
          f"- corpus files checked against the manifest: {fmt(i['corpus_files_checked'])}; "
          f"without a T3 record: {i['corpus_files_without_t3']}; calibration outputs: "
          f"{len(i['calibration'])} (sha256 in inputs.json)",
          f"- outputs: files.jsonl.gz {s['outputs']['files.jsonl.gz']}, calib.json "
          f"{s['outputs']['calib.json']}, problems: {s['problems']} "
          "(a problem is a stream that differs from the reference)", "",
          "## Corpus", ""]
    c, n, t, r = s["composition"], s["instances"], s["tokens"], s["risk"]
    L += [f"- files {fmt(c['files'])}; compiled in T3 {fmt(c['t3_compile_ok'])}, not compiled "
          f"{c['t3_compile_failed']} (0 steps: compile_error assumed for the Llama tokenizer "
          f"too); compiled without tests {fmt(c['compiled_without_tests'])}",
          f"- instances of compiled files: valid {fmt(n['valid'])}, invalid {fmt(n['invalid'])}; "
          f"serializer refusals: valid {n['valid_refused']}, invalid {n['invalid_refused']}",
          f"- valid: {fmt(t['valid_ids'])} tokens, {fmt(t['valid_steps'])} steps (risk "
          f"instances end at their T3 error); invalid: {fmt(t['invalid_ids'])} tokens, "
          f"{fmt(t['invalid_steps'])} steps up to the rejection",
          f"- where the steps of an instance come from: {json.dumps(s['steps_from'])}. "
          "Taking the T3 rejection byte for the Llama tokens is an assumption; on the "
          f"calibration sample the predicted steps equal the measured ones for "
          f"{s['assumption_checks']['instances_equal']} of "
          f"{s['assumption_checks']['instances']} instances, and the T3 compile status "
          f"matches the T8a outcome for {s['assumption_checks']['files_compile_consistent']} of "
          f"{s['assumption_checks']['files']} files with a T8a result (pending markers: "
          f"{s['assumption_checks']['files_pending']})",
          f"- risk files (an instance ended with RESOURCE_LIMIT or CANCELLED in T3): "
          f"{r['files']}, instances {r['instances']} {json.dumps(r['outcomes'])}, files with the "
          f"error at byte 0: {r['files_at_byte_0']}", "",
          "| class | valid steps | invalid steps (to the rejection) |", "|---|---:|---:|"]
    for k in FAST + OPEN:
        L.append(f"| {k} | {fmt(s['classes']['valid'].get(k, 0))} | "
                 f"{fmt(s['classes']['invalid'].get(k, 0))} |")
    L += ["", "## Calibration (measured steps)", "",
          f"{cal['files']} calibration outputs with measured steps (of "
          f"{len(s['inputs']['calibration'])}); a repeat is a step whose token prefix an earlier "
          "instance of the same file measured.", "",
          "| class | steps | mean ms | > 100 ms |", "|---|---:|---:|---:|"]
    for k, v in cal["per_class"].items():
        L.append(f"| {k} | {v['steps']} | {v['mean_ms']} | {v['slow']} |")
    ss = cal["slow_step_ms"]
    L += ["", f"Slow steps (> 100 ms): median {ss['median']} ms, p10 {ss['p10']}, p90 "
          f"{ss['p90']}, n {ss['n']}. outside, key_closed and str_enum were fast in the whole "
          "calibration sample. Open classes were about 1 s in some files and fast in others; "
          "repeats were mostly fast, but slow in Github_medium---o65503 (diagnosis); a fast "
          "repeat does not show a cache hit. str_format and str_maxlen have no calibration "
          "steps.", "",
          "## Scenarios", "",
          "- S1: every open-class step costs the median slow step",
          "- S2: as S1, but exact prefix repeats of open classes cost the mean of the fast "
          "classes",
          "- S3: every (class, repeat) costs its calibration mean (str_format, str_maxlen: the "
          "str_free means)", "",
          f"A file above {LIMIT_S:.0f} s is a timeout and costs {LIMIT_S:.0f} s; each chunk start "
          f"costs {RESTART_S} s (one per {CHUNK} files and one after each timeout). Risk files: "
          "\"hang\" = a timeout as o89710; \"no hang\" = the instance ends at its error step, "
          "and the time of that call is not counted.", "",
          "| scenario | timeouts, no hang | timeouts, hang | days, no hang | days, hang |",
          "|---|---:|---:|---:|---:|"]
    for k in ("S1", "S2", "S3"):
        a, b = sc[k], sc[k + "-hang"]
        L.append(f"| {k} | {a['timeouts']} | {b['timeouts']} | {a['days']} | {b['days']} |")
    L += ["", "### Validation on the calibration files", "",
          "| scenario | files | Σpredicted / Σmeasured | predicted ≥ measured | median ratio "
          "(files > 1 s) | p10-p90 |", "|---|---:|---:|---:|---:|---|"]
    for k, v in s["validation"].items():
        L.append(f"| {k}{' (leave one out)' if k == 'S3' else ''} | {v['files']} | "
                 f"{v['ratio_of_sums']} | {v['pred_ge_meas']} | "
                 f"{v['ratio_median_files_over_1s']} | {v['ratio_p10_p90']} |")
    L += ["", "Known timeouts of the run t8a-6e62e0d1 (seconds per scenario, no hang; "
          "None = risk file):", ""]
    for f, v in s["known_files"].items():
        L.append(f"- {f}: {json.dumps(v)}")
    over = s["files_over_870_open_steps"]
    L += ["", f"Files with more than 870 open-class steps: {over['files']}; of them not a "
          f"timeout in S1: {over['not_timeout_S1']}, in S2: {over['not_timeout_S2']}, in S3: "
          f"{over['not_timeout_S3']}.", "",
          "## Load per file (risk files hang)", "",
          "| file time | S1 | S2 | S3 |", "|---|---:|---:|---:|"]
    for b in BUCKETS:
        L.append(f"| {b} | " + " | ".join(str(s["histogram_hang"][k][b])
                                          for k in ("S1", "S2", "S3")) + " |")
    L += ["", "| dataset | files | risk files | timeouts S1 | timeouts S3 | hours S1 | hours S3 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for ds, x in sorted(s["datasets"].items(), key=lambda kv: -kv[1]["S1_hours"]):
        L.append(f"| {ds} | {x['files']} | {x['risk']} | {x['S1_timeouts']} | "
                 f"{x['S3_timeouts']} | {x['S1_hours']:.1f} | {x['S3_hours']:.1f} |")
    L += ["", "Heaviest files in S3 (no hang, before the 900 s limit): " + ", ".join(
          f"{h['file']} {h['seconds']} s" for h in s["heaviest_S3"][:8]), "",
          "## Limits", "",
          "- The cost of an open-class step depends on the file and is bimodal; the mechanism "
          "in the core is not established. The scenarios are not a forecast.",
          "- The calibration sample is small and biased (half of it the short BFCL_java files "
          "of the smoke).",
          "- The rejection byte and the compile status come from T3 (byte tokenizer): an "
          "assumption, tested on the calibration sample only.",
          "- Risk files: only o89710 was observed (no return of the first mask); \"hang\" "
          "assumes the same for every risk file. \"No hang\" leaves out the time of the call "
          "that ended with the error (o89710 counts 0 steps there, not a zero cost).",
          "- Compile and serialization times are left out (milliseconds; 0.42 s for o89710).",
          "- Every timeout needs its own T2c class for the acceptance."]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def main():
    d = sys.argv[1]
    files = [json.loads(ln) for ln in gzip.open(os.path.join(d, "files.jsonl.gz"), "rt",
                                                 encoding="utf-8")]
    calib = json.load(open(os.path.join(d, "calib.json"), encoding="utf-8"))
    inputs = json.load(open(os.path.join(d, "inputs.json"), encoding="utf-8"))
    compile_checks = json.load(open(os.path.join(d, "compile_checks.json"), encoding="utf-8"))
    problems = json.load(open(os.path.join(d, "problems.json"), encoding="utf-8"))
    by_file = {r["file"]: r for r in files}
    good = [c for c in calib if not c["problem"] and c["steps"]]
    agg, slow = costs_from([s for c in good for s in c["steps"]])
    slow_med = pct(slow, 0.5)
    sc = scenario_costs(agg, slow_med)
    comp, inst, toks, steps_from, cls, risk, open_steps = composition(files)

    results, per_s = {}, {}
    for name in sc:
        for hang in (False, True):
            per, res = run(files, sc[name], hang)
            results[name + ("-hang" if hang else "")] = res
            per_s[(name, hang)] = per
    hist = {n: {b: 0 for b in BUCKETS} for n in sc}
    for n in sc:
        for v in per_s[(n, True)].values():
            hist[n][bucket(v)] += 1
    by_ds = {}
    for r in files:
        x = by_ds.setdefault(r["dataset"], {"files": 0, "risk": 0, "S1_timeouts": 0,
                                            "S3_timeouts": 0, "S1_hours": 0.0, "S3_hours": 0.0})
        x["files"] += 1
        x["risk"] += compiled(r) and is_risk(r)
        for n in ("S1", "S3"):
            v = per_s[(n, True)][r["file"]]
            x[f"{n}_timeouts"] += v > LIMIT_S
            x[f"{n}_hours"] += min(v, LIMIT_S) / 3600
    over = [f for f, k in open_steps.items() if k > 870]
    over870 = {"files": len(over)}
    for n in sc:
        over870[f"not_timeout_{n}"] = sum(per_s[(n, False)][f] <= LIMIT_S for f in over)

    val, rows = validate(calib, sc)
    # the T3 assumptions on the calibration sample: predicted against measured
    # steps per instance, T3 compile status against the T8a outcome per file
    eq = tot = 0
    for c in calib:
        if c["problem"] or c["steps"] is None:
            continue
        meas = {}
        for i_, _k, _c, _us, _r in c["steps"]:
            meas[i_] = meas.get(i_, 0) + 1
        for ins in by_file[c["file"]]["instances"]:
            if "steps" not in ins:
                continue
            tot += 1
            eq += meas.get(ins["i"], 0) == ins["steps"]
    known = {}
    for f in KNOWN:
        if f in by_file:
            known[f] = {n: (None if math.isinf(per_s[(n, True)][f]) else
                            round(per_s[(n, False)][f], 1)) for n in sc}
            known[f]["risk_file"] = is_risk(by_file[f])
    heavy = sorted(((v, f) for f, v in per_s[("S3", False)].items()), reverse=True)[:15]
    out = {
        "kind": "scenario estimate, not a count of guaranteed timeouts; the ranges are no "
                "guaranteed bounds of the duration",
        "inputs": inputs, "report_sha256": sha(os.path.abspath(__file__)),
        "outputs": {n: sha(os.path.join(d, n)) for n in ("files.jsonl.gz", "calib.json",
                                                         "compile_checks.json")},
        "problems": len(problems),
        "composition": comp, "instances": inst, "tokens": toks, "steps_from": steps_from,
        "classes": cls, "risk": risk,
        "assumption_checks": {
            "instances": tot, "instances_equal": eq,
            "files": sum(x["t8a"] != "pending" for x in compile_checks),
            "files_compile_consistent": sum(
                x["t8a"] != "pending" and (x["t8a"] == "compile_error") ==
                (x["t3_status"] not in ("OK", None)) for x in compile_checks),
            "files_pending": sum(x["t8a"] == "pending" for x in compile_checks)},
        "calibration": {
            "files": len(good),
            "per_class": {f"{k}{', repeat' if r else ''}": {
                "steps": v[0], "mean_ms": round(v[1] / v[0] / 1e3, 2), "slow": v[2]}
                for (k, r), v in sorted(agg.items())},
            "slow_step_ms": {"median": round(slow_med / 1e3, 1),
                             "p10": round(pct(slow, 0.1) / 1e3, 1),
                             "p90": round(pct(slow, 0.9) / 1e3, 1), "n": len(slow)}},
        "scenario_costs_ms": {n: {f"{k}{', repeat' if r else ''}": round(v / 1e3, 3)
                                  for (k, r), v in sorted(cs.items())} for n, cs in sc.items()},
        "scenarios": results, "histogram_hang": hist, "datasets": by_ds,
        "files_over_870_open_steps": over870, "validation": val, "validation_rows": rows,
        "known_files": known,
        "heaviest_S3": [{"file": f, "seconds": round(v, 1)} for v, f in heavy],
        "constants": {"limit_s": LIMIT_S, "restart_s": RESTART_S, "chunk_files": CHUNK,
                      "slow_us": SLOW_US}}
    with open(os.path.join(d, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    write_md(os.path.join(d, "summary.md"), out)
    print(json.dumps({k: out[k] for k in ("scenarios", "validation", "known_files",
                                         "files_over_870_open_steps", "assumption_checks")},
                     indent=1))


if __name__ == "__main__":
    main()
