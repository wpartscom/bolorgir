#!/usr/bin/env python3
"""Exploratory base/candidate warm pair under balanced order and fixed CPU.

Diagnosis only: no `warm_acceptance` block and no threshold verdict, so the
output cannot stand in for the stored acceptance. Each child reports the
SHA256 of the mapped library; a leg on another CPU fails the run.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "benchmarks"))

import bench_adr0007 as b  # noqa: E402


def _leg_row(which, round_no, probe, name, leg):
    st = leg.get("fill_wall_ns") or {}
    cond = (leg.get("conditions") or {}).get("start") or {}
    end = (leg.get("conditions") or {}).get("end") or {}
    return {
        "which": which,
        "round": round_no,
        "probe": probe,
        "leg": name,
        "mean_ns": st.get("mean"),
        "p50_ns": st.get("p50"),
        "p95_ns": st.get("p95"),
        "count": st.get("count"),
        "all_hits": leg.get("all_hits"),
        "cache_hits": leg.get("cache_hits"),
        "observations": leg.get("observations"),
        "loaded_sha256": (leg.get("loaded") or {}).get("sha256"),
        "loaded_end_sha256": (leg.get("loaded_end") or {}).get("sha256"),
        "cpu_affinity_start": cond.get("cpu_affinity"),
        "cpu_affinity_end": end.get("cpu_affinity"),
        "cpu_now_start": cond.get("cpu_now"),
        "cpu_now_end": end.get("cpu_now"),
        "extension_version": cond.get("extension_version"),
        "governors": (cond.get("cpu") or {}).get("governors"),
        "cur_freq_khz": (cond.get("cpu") or {}).get("cur_freq_khz"),
        "load_start": cond.get("load"),
        "load_end": end.get("load"),
    }


def _summary(rows, which):
    on = [r for r in rows if r["which"] == which and r["leg"] == "on"]
    means = [r["mean_ns"] for r in on if r["mean_ns"] is not None]
    p50s = [r["p50_ns"] for r in on if r["p50_ns"] is not None]
    return {
        "on_legs": len(on),
        "on_means_ns": [r["mean_ns"] for r in on],
        "on_p50_ns": [r["p50_ns"] for r in on],
        "mean_of_means_ns": (statistics.mean(means) if means else None),
        "min_mean_ns": min(means) if means else None,
        "max_mean_ns": max(means) if means else None,
        "median_mean_ns": statistics.median(means) if means else None,
        "all_hits": all(bool(r["all_hits"]) and
                        r["cache_hits"] == r["observations"] for r in on),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True,
                    help="baseline library (the old build)")
    ap.add_argument("--candidate", required=True,
                    help="candidate library (the artifact under review)")
    ap.add_argument("--manifest",
                    default=os.path.join(ROOT, "benchmarks",
                                         "baseline_slice.json"))
    ap.add_argument("--observations", type=int, default=2000)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--cpu", type=int, required=True,
                    help="fixed CPU placement for every child")
    ap.add_argument("--max-load1", type=float, default=1.0)
    ap.add_argument("--max-cpu-pressure", type=float, default=1.0)
    ap.add_argument("--threshold-ns", type=int, default=1300,
                    help="published for context only; never judged here")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    libs = {"base": os.path.abspath(args.base),
            "candidate": os.path.abspath(args.candidate)}
    for name, path in libs.items():
        if not os.path.isfile(path):
            raise SystemExit("%s library does not exist: %s" % (name, path))
    hashes = {name: b._sha256(path) for name, path in libs.items()}
    if hashes["base"] == hashes["candidate"]:
        raise SystemExit("base and candidate are the same file: %s"
                         % hashes["base"])

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    rows = []
    condition_problems = []
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for r in range(args.rounds):
        order = ("base", "candidate") if r % 2 == 0 else ("candidate", "base")
        on_first = (r % 2) == 1
        for which in order:
            for probe in manifest["probes"]:
                print("round %d/%d %s probe %s ..." % (
                    r + 1, args.rounds, which, probe["id"]),
                    file=sys.stderr, flush=True)
                legs = b.warm_via_extension(
                    manifest, probe["id"], args.observations, libs[which],
                    on_first=on_first, cpu=args.cpu)
                for name in ("off", "on"):
                    row = _leg_row(which, r + 1, probe["id"], name,
                                   legs[name])
                    rows.append(row)
                    if row["loaded_sha256"] != hashes[which]:
                        condition_problems.append(
                            "round %d %s %s: loaded %s, expected %s"
                            % (r + 1, which, name, row["loaded_sha256"],
                               hashes[which]))
                    loaded_end = row["loaded_end_sha256"]
                    if loaded_end is not None and loaded_end != hashes[which]:
                        condition_problems.append(
                            "round %d %s %s: loaded_end %s, expected %s"
                            % (r + 1, which, name, loaded_end,
                               hashes[which]))
                    for when, aff in (("start", row["cpu_affinity_start"]),
                                      ("end", row["cpu_affinity_end"])):
                        if aff != [args.cpu]:
                            condition_problems.append(
                                "round %d %s %s %s: affinity %r, "
                                "expected [%d]"
                                % (r + 1, which, name, when, aff,
                                   args.cpu))
                    for when, sample in (("start", row["load_start"]),
                                         ("end", row["load_end"])):
                        if not isinstance(sample, dict):
                            condition_problems.append(
                                "round %d %s %s %s: missing load snapshot"
                                % (r + 1, which, name, when))
                            continue
                        loadavg = sample.get("loadavg")
                        if (not isinstance(loadavg, (list, tuple)) or
                                len(loadavg) < 3 or
                                not all(b._finite_number(x)
                                        for x in loadavg[:3])):
                            condition_problems.append(
                                "round %d %s %s %s: missing or invalid "
                                "loadavg" % (r + 1, which, name, when))
                        elif loadavg[0] > args.max_load1:
                            condition_problems.append(
                                "round %d %s %s %s: load1 %.2f > %.2f"
                                % (r + 1, which, name, when, loadavg[0],
                                   args.max_load1))
                        psi = (sample.get("cpu_pressure_some") or {}).get(
                            "avg10")
                        if not b._finite_number(psi):
                            condition_problems.append(
                                "round %d %s %s %s: missing or invalid "
                                "cpu pressure avg10"
                                % (r + 1, which, name, when))
                        elif psi > args.max_cpu_pressure:
                            condition_problems.append(
                                "round %d %s %s %s: cpu pressure avg10 "
                                "%.2f > %.2f" % (r + 1, which, name, when,
                                                 psi, args.max_cpu_pressure))
                    if not (row["all_hits"] and
                            row["cache_hits"] == row["observations"]):
                        condition_problems.append(
                            "round %d %s %s: not all cache hits (%r/%r)"
                            % (r + 1, which, name, row["cache_hits"],
                               row["observations"]))
    rows.sort(key=lambda row: (row["round"], row["probe"], row["which"],
                               row["leg"]))
    base = _summary(rows, "base")
    cand = _summary(rows, "candidate")
    pairs = {}
    for row in rows:
        pairs.setdefault((row["round"], row["probe"], row["leg"]),
                         {})[row["which"]] = row
    deltas = []
    for key, pair in sorted(pairs.items()):
        if key[2] != "on":
            continue
        if "base" not in pair or "candidate" not in pair:
            continue
        bm = pair["base"]["mean_ns"]
        cm = pair["candidate"]["mean_ns"]
        if bm is None or cm is None:
            continue
        deltas.append({"round": key[0], "probe": key[1],
                       "base_ns": bm, "candidate_ns": cm,
                       "delta_ns": cm - bm,
                       "ratio": (cm / bm) if bm else None})
    dvals = [d["delta_ns"] for d in deltas]
    summary = {
        "base": base,
        "candidate": cand,
        "paired_on": deltas,
        "paired_delta_ns_mean": (statistics.mean(dvals) if dvals else None),
        "paired_delta_ns_median": (statistics.median(dvals) if dvals else None),
        "paired_delta_ns_min": min(dvals) if dvals else None,
        "paired_delta_ns_max": max(dvals) if dvals else None,
        "paired_ratio_mean": (statistics.mean(
            d["ratio"] for d in deltas if d["ratio"] is not None)
            if deltas else None),
        "threshold_ns": args.threshold_ns,
        "threshold_exceedances": {
            "base": [m for m in base["on_means_ns"]
                     if m is not None and m > args.threshold_ns],
            "candidate": [m for m in cand["on_means_ns"]
                          if m is not None and m > args.threshold_ns],
        },
    }
    conditions = {
        "requested_cpu": args.cpu,
        "limits": {"max_load1": args.max_load1,
                   "max_cpu_pressure_avg10": args.max_cpu_pressure},
        "parent": b.measurement_snapshot(),
        "ok": not condition_problems,
        "problems": condition_problems,
    }
    report = {
        "kind": "warm-pair-diagnostic",
        "acceptance": False,
        "note": ("Exploratory base/candidate pair only. It does not judge "
                 "the warm guard, does not replace the stored 2d982b60 "
                 "FAIL, and cannot serve as an acceptance measurement."),
        "generated_utc": started,
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base": {"path": libs["base"], "sha256": hashes["base"]},
        "candidate": {"path": libs["candidate"],
                      "sha256": hashes["candidate"]},
        "protocol": {
            "observations": args.observations,
            "rounds": args.rounds,
            "threshold_ns": args.threshold_ns,
            "cpu": args.cpu,
            "order": ("library order alternates per round; leg order "
                      "alternates off/on across rounds"),
            "probes": [p["id"] for p in manifest["probes"]],
        },
        "conditions": conditions,
        "rows": rows,
        "summary": summary,
    }
    out = args.out or os.path.join(ROOT, "benchmarks", "local",
                                   "warm-pair-%s.json" % time.strftime(
                                       "%Y%m%dT%H%M%SZ", time.gmtime()))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)

    print(json.dumps({k: summary[k] for k in (
        "paired_delta_ns_mean", "paired_delta_ns_median",
        "paired_delta_ns_min", "paired_delta_ns_max",
        "paired_ratio_mean", "threshold_exceedances")}, indent=1))
    print("conditions ok: %s" % conditions["ok"], file=sys.stderr)
    for problem in condition_problems:
        print("condition: " + problem, file=sys.stderr)
    print("diagnostic only; report %s" % out, file=sys.stderr)
    return 0 if conditions["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
