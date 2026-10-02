#!/usr/bin/env python3
"""Control-run summary: key numbers + Go-rule check §10.6.

Reads JSON results from results/<timestamp> and prints:
- uniform load table (mask/accept/compile per engine);
- Go-rule verdict from manifest.control_scenario;
- B1 cold-process, B7 e2e constrained summary (medians over repeats),
  B5/B8 plateau, B3/B6.

Modes: CLI checks run composition against the
FIXED protocol (manifest.control_scenario.active_acceptance_protocol):
- exactly one dir -> single-collection ANALYSIS mode: numbers are
  computed, but the result is explicitly marked as NOT acceptance, exit 3;
- complete unique set of registered collections of the same code
  (run_tags), with environment.txt of the same revision -> final acceptance
  (GO/NO-GO/INCONCLUSIVE, exit 0/1/2);
- missing, repeated (same dir twice) or extraneous collection ->
  composition does not match the protocol: INCONCLUSIVE, exit 2, GO impossible.
control_gate_multi() (legacy API for tests and rechecks) still computes
the verdict over the passed dirs without composition checks;
final acceptance goes through acceptance_gate().

The e2e gate validates pairs before computing statistics:
uniqueness (file, engine, repeat), exact repeat set, positions {0,1},
sequential times; the CLI preview print is safe for corrupted and
incomplete files. total_s and metrics are finite strictly positive
numbers (NaN/∞ filtered before statistics), nested fields are
structurally validated before aggregation.

Usage:
    python3 benchmarks/summarize_control.py <dir> [more dirs...]
    python3 benchmarks/summarize_control.py --tables <dir> ...   # tables
"""

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
from statistics import median


def load(d, name):
    """JSON from the dir (or None if the file is missing; corrupted - error)."""
    blob, problem = load_status(d, name)
    if problem == "no file":
        return None
    if problem:
        raise ValueError(f"{os.path.join(d, name)}: {problem}")
    return blob


def load_status(d, name):
    """(blob | None, problem | None): distinguishes missing and corrupted
    file (incomplete/corrupted data must produce a structured
    result, not a crash)."""
    p = os.path.join(d, name)
    if not os.path.exists(p):
        return None, "no file"
    try:
        with open(p, encoding="utf-8") as f:
            raw = f.read()
        try:
            blob = json.loads(raw)
        except ValueError:
            # output may carry a warning prefix before the JSON
            blob = json.loads(raw[raw.index("{"):])
    except Exception as e:  # noqa: BLE001 - any parse failure: structured
        return None, f"corrupted ({type(e).__name__}: {e})"
    if not isinstance(blob, dict):
        # A valid JSON non-object (list/number) is also a
        # corrupted structure, not a data source.
        return None, "corrupted (JSON root is not an object)"
    return blob, None


def _safe_load(d, name, out):
    """Load for the CLI preview print and tables:
    a corrupted file does not crash the summary - a note is printed, the
    section is skipped; the data verdict stays with the final gate."""
    blob, problem = load_status(d, name)
    if problem == "no file":
        return None
    if problem:
        out(f"  (section {name}: file {problem} - skipped; the final gate "
            "decides on it)")
        return None
    return blob


class _guarded:
    """Print section robust to non-structured data:
    an exception inside the section prints a note without cancelling the
    other sections and without overriding the gate verdict."""

    def __init__(self, label, out):
        self.label, self.out = label, out

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None and issubclass(exc_type, Exception):
            self.out(f"  (section {self.label} not printed: "
                     f"{exc_type.__name__}: {exc}; the final gate "
                     "decides)")
            return True
        return False


def _finite_positive(v):
    """Finite strictly positive number.

    NaN/∞/zero fail comparisons like `v > threshold` (NaN > x = False)
    and could silently let a regression pass the verdict."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    if isinstance(v, float) and not math.isfinite(v):
        return False
    return v > 0


def us(ns):
    return None if ns is None else round(ns / 1000.0, 2)


def _combine_worst(uni, uni2):
    """Worst zig / best competitor values across repeats (conservative).

    None values do not crash the merge: a defined value
    is taken if it is the only one; both None -> stays None.
    Invalid sources never reach here (validated beforehand)."""
    if not uni2:
        return uni, 1
    out = {}
    for name, e in (uni.get("engines") or {}).items():
        e2 = (uni2.get("engines") or {}).get(name)
        if not e2:
            out[name] = e
            continue
        is_zig = name.startswith("zig")
        merged = {}
        for m, stats in e.items():
            s2 = e2.get(m)
            if isinstance(stats, dict) and isinstance(s2, dict):
                ms = dict(stats)
                for k in ("p50", "p95", "p99"):
                    if k in stats and k in s2:
                        a, b = stats[k], s2[k]
                        if a is None and b is None:
                            ms[k] = None
                        elif a is None:
                            ms[k] = b
                        elif b is None:
                            ms[k] = a
                        else:
                            # zig - worst repeat, competitors - best
                            ms[k] = max(a, b) if is_zig else min(a, b)
                merged[m] = ms
            else:
                merged[m] = stats
        out[name] = merged
    return {**uni, "engines": out}, 2


def _e2e_regressions_legacy(d):
    """(schema, batch): worst total_s zig vs best total_s xg across repeats."""
    runs = []
    for name in ("bench_e2e_constrained.json",
                 "bench_e2e_constrained_repeat.json"):
        blob = load(d, name)
        if blob:
            runs += [r for r in blob.get("runs", [])
                     if r["summary"].get("status") == "OK"]
    cfg = {}
    for r in runs:
        cfg.setdefault((r["schema"], r["batch"]), []).append(r)
    out = []
    for (schema, batch), rs in sorted(cfg.items()):
        zig = [r["summary"]["total_s"] for r in rs
               if r["mode"] == "zig_constrained"]
        xg = [r["summary"]["total_s"] for r in rs
              if r["mode"] == "xgrammar_constrained"]
        if not zig or not xg:
            out.append({"schema": schema, "batch": batch, "reg": None})
            continue
        z, x = median(zig), median(xg)
        out.append({"schema": schema, "batch": batch,
                    "reg": (z - x) / x * 100,
                    "zig_median": z, "xg_median": x,
                    "zig_worst": max(zig), "xg_best": min(xg),
                    "n": (len(zig), len(xg))})
    return out


_OTHER_LATENCIES = (
    ("fill_mask_ns", "mask"),
    ("accept_ns", "accept"),
    ("cold_compile_ns", "cold compile"),
    ("warm_compile_ns", "warm compile"),
    ("first_mask_ns", "first mask"),
    ("tokenizer_prepare_ns", "tokenizer prepare"),
)


def _control_gate_legacy(d):
    """Single gate §10.6: PASS/FAIL/INCONCLUSIVE."""
    uni = load(d, "bench_compare_uniform.json")
    uni2 = load(d, "bench_compare_uniform_repeat.json")
    cp = load(d, "bench_coldproc.json")
    regs = e2e_regressions(d)
    print("\n## Gate §10.6 (single)")
    if not uni or uni.get("status") != "OK":
        print("VERDICT control scenario: INCONCLUSIVE (no uniform data)")
        return
    merged, n_rep = _combine_worst(uni, uni2)
    engines = merged["engines"]
    za = engines.get("zig_adaptive")
    comp = {k: v for k, v in engines.items()
            if k in ("xgrammar", "llguidance")}
    print(f"source: uniform, repeats {n_rep}; for zig the worst repeat, "
          "for the competitor the best (conservative)")

    def best_by(metric, pct):
        return min(comp, key=lambda k: comp[k][metric].get(pct, float("inf")))

    options = []  # (label, comparator, passed, detail)
    if za and comp:
        bz = best_by("fill_mask_ns", "p99")
        zb, cb = za["fill_mask_ns"]["p99"], comp[bz]["fill_mask_ns"]["p99"]
        g = (cb - zb) / cb * 100
        options.append(("mask p99", bz, g >= 20, f"{g:+.1f}%"))
        if za["first_mask_ns"].get("p95") and all(
                comp[k]["first_mask_ns"].get("p95") for k in comp):
            ba = best_by("first_mask_ns", "p95")
            zb2 = za["first_mask_ns"]["p95"]
            cb2 = comp[ba]["first_mask_ns"]["p95"]
            g2 = (cb2 - zb2) / cb2 * 100
            options.append(("first mask p95", ba, g2 >= 20, f"{g2:+.1f}%"))
        else:
            options.append(("first mask p95", None, None, "no p95"))
        mem = None
        if cp and cp.get("status") == "OK":
            ce = {k: v for k, v in cp["engines"].items()
                  if k in ("xgrammar", "llguidance")
                  and v.get("peak_rss_bytes", {}).get("p50")}
            zr = cp["engines"].get("zig_adaptive", {}) \
                .get("peak_rss_bytes", {}).get("p50")
            if ce and zr:
                bm = min(ce, key=lambda k: ce[k]["peak_rss_bytes"]["p50"])
                cb3 = ce[bm]["peak_rss_bytes"]["p50"]
                g3 = (cb3 - zr) / cb3 * 100
                mem = (bm, g3 >= 20, f"{g3:+.1f}%")
        options.append(("peak memory (B1)",
                        mem[0] if mem else None,
                        mem[1] if mem else None,
                        mem[2] if mem else "no B1 data"))

    def regressions_vs(comparator):
        bad = []
        for metric, label in _OTHER_LATENCIES:
            for pct in ("p50", "p95", "p99"):
                z = za[metric].get(pct)
                c = comp[comparator][metric].get(pct)
                if not z or not c:
                    continue
                deg = (z - c) / c * 100
                if deg > 10:
                    bad.append((f"{label} {pct}", deg, z, c))
        return bad

    chosen = None
    print("primary criterion options (need ≥20% in at least one and other "
          "latencies ≤+10% vs the same competitor):")
    for label, comparator, ok, detail in options:
        if ok is None:
            print(f"  {label}: INCONCLUSIVE ({detail})")
            continue
        print(f"  {label} vs {comparator}: {detail} - "
              + ("≥20% met" if ok else "no advantage"))
        if not ok:
            continue
        bad = regressions_vs(comparator)
        if bad:
            print(f"    extra latencies vs {comparator} >10%:")
            for lbl, deg, z, c in bad:
                print(f"      {lbl}: zig {us(z)} us vs {us(c)} us ({deg:+.1f}%)")
        else:
            print(f"    other latencies vs {comparator}: all ≤ +10% - OK")
            chosen = (label, comparator)

    e2e_ok = bool(regs) and all(r["reg"] is not None and r["reg"] <= 5
                                for r in regs)
    print("e2e guard (required: medians over repeats, threshold 5%):")
    if not regs:
        print("  no data - INCONCLUSIVE")
    for r in regs:
        if r["reg"] is None:
            print(f"  {r['schema']} b{r['batch']}: no baseline - INCONCLUSIVE")
        else:
            flag = "OK" if r["reg"] <= 5 else "FAIL"
            print(f"  {r['schema']} b{r['batch']}: {r['reg']:+.1f}% {flag}"
                  f" (zig {r['zig_median']:.2f}s vs xg {r['xg_median']:.2f}s)")

    if any(o[2] for o in options) and chosen and e2e_ok:
        verdict = "GO"
    elif not regs or not any(o[2] is not None for o in options):
        verdict = "INCONCLUSIVE"
    else:
        verdict = "NO-GO"
    if verdict == "GO":
        print(f"\nprimary metric: {chosen[0]} vs {chosen[1]}")
    print(f"\nVERDICT control scenario: {verdict}")
    if za and comp:
        for name in comp:
            zb4 = za["fill_mask_ns"]["p99"]
            cb4 = comp[name]["fill_mask_ns"]["p99"]
            print(f"(delta vs {name}: mask p99 zig {us(zb4)} vs "
                  f"{us(cb4)} us; {abs(cb4 - zb4) / cb4 * 100:.1f}% "
                  f"{'better' if zb4 < cb4 else 'worse'})")


def _manifest_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "manifest.json")


def load_manifest(path=None):
    with open(path or _manifest_path(), encoding="utf-8") as f:
        return json.load(f)


_PERCENTILES = ("p50", "p95", "p99")
_REQUIRED_ENGINES = ("zig_adaptive", "xgrammar", "llguidance")
_ZIG = "zig_adaptive"
_COMPETITORS = ("xgrammar", "llguidance")

# Observation classes for decision p99 values (spec 10.5):
# warm classes - ≥10k observations, cold start - ≥30
# independent repeats. min_observations was checked for the mask only and
# let 100 warm compile observations pass.
_DECISION_OBS = (
    ("fill_mask_ns", "primary"),
    ("accept_ns", "warm"),
    ("warm_compile_ns", "warm"),
    ("cold_compile_ns", "cold"),
    ("first_mask_ns", "cold"),
    ("tokenizer_prepare_ns", "cold"),
)


def _dir_label(d):
    return os.path.basename(os.path.normpath(d))


def _bootstrap_median_ci(values, n=10000, seed=42):
    """95% percentile bootstrap CI of the median (deterministic seed).

    The rule for an interval crossing the threshold is
    fixed in advance - the decision uses the point estimate (median of
    paired ratios), the CI is published, a threshold crossing is flagged
    as margin (does not change the verdict)."""
    if not values:
        return None
    rng = random.Random(seed)
    k = len(values)
    meds = sorted(median(rng.choices(values, k=k)) for _ in range(int(n)))
    lo = meds[max(0, int(round(0.025 * (n - 1))))]
    hi = meds[min(n - 1, int(round(0.975 * (n - 1))))]
    return [lo, hi]


def e2e_review(dirs, spec, ereq=None):
    """Strict check of the e2e matrix.

    ereq - active protocol requirements (acceptance_protocol_vN
    .e2e_requirements) or None (legacy mode: pooled medians):
      * balanced AB/BA engine order within a repeat (runs must carry rep
        and order_pos; run parameters are checked against the protocol);
      * decision by the PAIRED median of time ratios within time blocks
        (file x repeat): worst zig and best competitor are adjacent in
        time, the bootstrap CI is published; pooled medians stay in the
        report for comparability;
      * before statistics, records uniqueness (file,
        engine, repeat), the exact repeat set, pair positions {0,1},
        positive sequential times and total_s consistency with the stored
        times are checked;
      * total_s is a finite strictly positive number
        (NaN/∞/zero filtered before statistics), nested fields (runs/
        summary/protocol) are structurally validated before aggregation.

    Run errors, invalid/unassembled outputs, regression above the threshold,
    and structurally corrupted pairs (duplicate records or repeats,
    positions not {0,1}, contradictory times;
    non-numeric/non-positive times and corrupted nested fields) -> FAIL; missing/corrupted files, cases, incomplete
    pairs, too few repeats or unconfirmed order -> INCONCLUSIVE.
    Returns (status, reasons, cases)."""
    files = spec.get(
        "runs_files",
        ["bench_e2e_constrained.json", "bench_e2e_constrained_repeat.json"],
    )
    if ereq and ereq.get("files_per_collection"):
        files = list(ereq["files_per_collection"])
    blobs = []
    missing, corrupt = [], []
    for d in dirs:
        for name in files:
            blob, problem = load_status(d, name)
            if problem == "no file":
                missing.append(f"no file {name} in {_dir_label(d)}")
            elif problem:
                corrupt.append(f"{_dir_label(d)}/{name}: file {problem}")
            elif "runs" not in blob:
                # A missing field is an incomplete file:
                # an empty set gives INCONCLUSIVE on repeat count.
                blob["runs"] = []
                blobs.append((f"{_dir_label(d)}/{name}", blob))
            elif not isinstance(blob["runs"], list):
                # Present but invalid field - corrupted structure.
                corrupt.append(
                    f"{_dir_label(d)}/{name}: field runs corrupted "
                    "(not a list)")
            elif any(not isinstance(r, dict) for r in blob["runs"]):
                corrupt.append(
                    f"{_dir_label(d)}/{name}: runs elements are not objects")
            elif (ereq and "protocol" in blob
                  and not isinstance(blob["protocol"], dict)):
                # The protocol block, required in paired
                # mode, must be an object.
                corrupt.append(
                    f"{_dir_label(d)}/{name}: field protocol corrupted "
                    "(not an object)")
            else:
                blobs.append((f"{_dir_label(d)}/{name}", blob))
    if corrupt:
        return "FAIL", corrupt + missing, []
    if missing or not blobs:
        return "INCONCLUSIVE", missing or ["no e2e files"], []
    failures, incomplete, cases = [], [], []
    min_reps = int(spec.get("min_reps_per_file", 3))
    if ereq and ereq.get("reps_per_file"):
        min_reps = int(ereq["reps_per_file"])
    max_reg = float(spec.get("max_regression_pct", 5.0))
    if ereq and ereq.get("max_regression_pct") is not None:
        max_reg = float(ereq["max_regression_pct"])
    engines = spec.get(
        "engines", {"zig": "zig_constrained", "xgrammar": "xgrammar_constrained"}
    )
    require_completed = bool(spec.get("require_completed", True))
    expected_reps = None
    if ereq and ereq.get("reps_per_file"):
        expected_reps = set(range(int(ereq["reps_per_file"])))
    if ereq:
        want_order = ereq.get("engine_order")
        for label, blob in blobs:
            proto = blob.get("protocol")
            if not isinstance(proto, dict):
                # Measurement parameters must be readable;
                # a non-dict equals absence (INCONCLUSIVE below).
                proto = {}
            got = proto.get("engine_order")
            if want_order and got != want_order:
                incomplete.append(
                    f"{label}: engine_order={got!r}, required {want_order!r} "
                    "(measurement parameters do not match the protocol)"
                )
            reps_f = proto.get("reps")
            if ereq.get("reps_per_file") and reps_f != ereq.get("reps_per_file"):
                incomplete.append(
                    f"{label}: reps={reps_f!r}, required "
                    f"{ereq.get('reps_per_file')}"
                )
    for schema in spec.get("schemas", []):
        for batch in spec.get("batches", []):
            per_role = {}
            blocks = {}  # (file, repeat) -> paired engine times in the block
            missing_pos = 0
            missing_times = 0
            rep_seen = {}    # (file, role) -> accepted repeat numbers
            dup_reps = {}    # (file, role) -> repeats with a duplicate record
            extra_reps = {}  # (file, role) -> repeats outside the fixed set
            for role, mode in engines.items():
                per_file = {}
                pooled = []
                for name, blob in blobs:
                    runs = [
                        r for r in blob.get("runs", [])
                        if r.get("schema") == schema
                        and r.get("batch") == batch
                        and r.get("mode") == mode
                    ]
                    per_file[name] = runs
                    if len(runs) < min_reps:
                        incomplete.append(
                            f"{schema} b{batch} {mode}: {len(runs)} repeats "
                            f"in {name} (<{min_reps})"
                        )
                    for r in runs:
                        s = r.get("summary")
                        if not isinstance(s, dict):
                            # Summary must be an object
                            # (null/list used to crash the final gate).
                            failures.append(
                                f"{schema} b{batch} {mode} {name}: "
                                "run summary is not an object"
                            )
                            continue
                        st = s.get("status")
                        if st != "OK":
                            # Erroneous run (e.g. format f721568:
                            # summary={status:ERROR,...} without total_s) -
                            # structured FAIL, no KeyError in statistics
                            # collection.
                            err = s.get("error")
                            failures.append(
                                f"{schema} b{batch} {mode} {name}: status={st}"
                                + (f" ({err})" if err else "")
                            )
                            continue
                        total_s = s.get("total_s")
                        if not _finite_positive(total_s):
                            # NaN/∞/zero fail comparisons
                            # (`NaN > threshold` = False) and could let a
                            # regression pass silently - finite strictly
                            # positive times are required.
                            failures.append(
                                f"{schema} b{batch} {mode} {name}: "
                                "status=OK without finite positive "
                                f"total_s ({total_s!r})"
                            )
                            continue
                        pooled.append(total_s)
                        if s.get("valid_rows") != batch or s.get("validity_rate") != 1.0:
                            failures.append(
                                f"{schema} b{batch} {mode} {name}: valid_rows="
                                f"{s.get('valid_rows')}/{batch} (all expected)"
                            )
                        if require_completed and s.get("completed_rows") != batch:
                            failures.append(
                                f"{schema} b{batch} {mode} {name}: "
                                f"completed_rows={s.get('completed_rows')}/{batch}"
                            )
                        if ereq:
                            rep = r.get("rep")
                            pos = r.get("order_pos")
                            if not isinstance(rep, int):
                                incomplete.append(
                                    f"{schema} b{batch} {mode} {name}: "
                                    "run without repeat number rep"
                                )
                            elif rep in rep_seen.setdefault((name, role), set()):
                                # The key (file, engine,
                                # repeat) must be unique - a later record used
                                # to silently overwrite the earlier one, and
                                # extra observations vanished from the
                                # decision.
                                dup_reps.setdefault((name, role), set()).add(rep)
                                blocks.pop((name, rep), None)
                            elif (expected_reps is not None
                                  and rep not in expected_reps):
                                # Repeat outside the fixed set (v3: 0..3):
                                # the observation can be neither silently
                                # accepted nor dropped.
                                extra_reps.setdefault(
                                    (name, role), set()).add(rep)
                            else:
                                rep_seen[(name, role)].add(rep)
                                blk = blocks.setdefault((name, rep), {})
                                blk[role] = total_s
                                if isinstance(pos, int):
                                    blk.setdefault("order", {})[role] = pos
                                else:
                                    missing_pos += 1
                                ts_ns = r.get("t_start_ns")
                                te_ns = r.get("t_end_ns")
                                if ts_ns is None and te_ns is None:
                                    # No stored times - the actual time
                                    # order is not confirmed.
                                    missing_times += 1
                                elif (isinstance(ts_ns, int)
                                      and isinstance(te_ns, int)
                                      and ts_ns > 0 and te_ns > ts_ns):
                                    blk.setdefault("times", {})[role] = (
                                        ts_ns, te_ns)
                                    dur_s = (te_ns - ts_ns) / 1e9
                                    if abs(total_s - dur_s) > 1e-6:
                                        failures.append(
                                            f"{schema} b{batch} {mode} {name}: "
                                            f"total_s={total_s:.6f} not "
                                            "consistent with stored "
                                            f"times ({dur_s:.6f})"
                                        )
                                else:
                                    failures.append(
                                        f"{schema} b{batch} {mode} {name}: "
                                        "non-positive/non-numeric times "
                                        "t_start_ns/t_end_ns"
                                    )
                per_file_vals = {}
                for name, runs in per_file.items():
                    vals = []
                    for r in runs:
                        s = r.get("summary")
                        if (isinstance(s, dict)
                                and _finite_positive(s.get("total_s"))):
                            vals.append(s["total_s"])
                    per_file_vals[name] = vals
                per_role[role] = {
                    "pooled": pooled,
                    "per_file": per_file_vals,
                }
            if missing_pos:
                incomplete.append(
                    f"{schema} b{batch}: {missing_pos} runs without order_pos - "
                    "balanced order not confirmed"
                )
            if missing_times:
                incomplete.append(
                    f"{schema} b{batch}: {missing_times} runs without "
                    "stored t_start_ns/t_end_ns - actual time order "
                    "not confirmed"
                )
            if expected_reps is not None:
                for f_label, role_ in sorted(rep_seen):
                    miss = sorted(expected_reps - rep_seen[(f_label, role_)])
                    if miss:
                        incomplete.append(
                            f"{schema} b{batch} {f_label}: {role_} missing "
                            f"repeats {miss} from the fixed set "
                            f"{sorted(expected_reps)} (incomplete data)"
                        )
            for (f_label, role_), reps_ in sorted(dup_reps.items()):
                failures.append(
                    f"{schema} b{batch} {f_label}: duplicate records "
                    f"{role_} for repeats {sorted(reps_)} - a second "
                    "record would overwrite the earlier one in the "
                    "decision; blocks excluded"
                )
            for (f_label, role_), reps_ in sorted(extra_reps.items()):
                failures.append(
                    f"{schema} b{batch} {f_label}: repeats {sorted(reps_)} "
                    f"{role_} outside the fixed set "
                    f"{sorted(expected_reps or [])} - collection does not "
                    "match the measurement protocol"
                )
            zig_vals = per_role.get("zig", {}).get("pooled") or []
            xg_vals = per_role.get("xgrammar", {}).get("pooled") or []
            if not zig_vals or not xg_vals:
                incomplete.append(f"{schema} b{batch}: no repeats for one of the engines")
                continue
            zm, xm = median(zig_vals), median(xg_vals)
            pooled_reg = (zm - xm) / xm * 100
            per_file_stats = {}
            for name in per_role["zig"]["per_file"]:
                zn = per_role["zig"]["per_file"][name]
                xn = per_role.get("xgrammar", {}).get("per_file", {}).get(name, [])
                if not zn or not xn:
                    continue
                zmed, xmed = median(zn), median(xn)
                per_file_stats[name] = {
                    "zig_median": zmed, "xg_median": xmed,
                    "reg": (zmed - xmed) / xmed * 100 if xmed else None,
                    "n": (len(zn), len(xn)),
                }
            case = {
                "schema": schema, "batch": batch,
                "zig_median": zm, "xg_median": xm,
                "zig_worst": max(zig_vals), "xg_best": min(xg_vals),
                "n": (len(zig_vals), len(xg_vals)),
                "zig_per_file": per_role["zig"]["per_file"],
                "per_file": per_file_stats,
                "pooled_reg": pooled_reg,
            }
            if ereq and ereq.get("decision") == "paired_ratio_median":
                bad_positions, parity_bad, time_order_bad = [], [], []
                for (f_label, rep_), v in sorted(blocks.items()):
                    if "zig" not in v or "xgrammar" not in v:
                        continue
                    order_ = v.get("order") or {}
                    pz_, px_ = order_.get("zig"), order_.get("xgrammar")
                    if not (isinstance(pz_, int) and isinstance(px_, int)):
                        continue  # missing order_pos already accounted above
                    if {pz_, px_} != {0, 1}:
                        # Every pair must have positions
                        # {0,1}; 0/0 and 1/1 used to pass the balance check.
                        bad_positions.append(f"{f_label} rep={rep_}: "
                                             f"{pz_}/{px_}")
                        continue
                    if (pz_ == 0) != (rep_ % 2 == 0):
                        parity_bad.append(f"{f_label} rep={rep_}")
                    times_ = v.get("times") or {}
                    if "zig" in times_ and "xgrammar" in times_:
                        first_ = (times_["zig"] if pz_ == 0
                                  else times_["xgrammar"])
                        second_ = (times_["xgrammar"] if pz_ == 0
                                   else times_["zig"])
                        if first_[1] > second_[0]:
                            time_order_bad.append(f"{f_label} rep={rep_}")
                if bad_positions:
                    failures.append(
                        f"{schema} b{batch}: pair positions not {{0,1}} "
                        "(exactly one first and one second engine needed): "
                        + "; ".join(bad_positions)
                    )
                if time_order_bad:
                    failures.append(
                        f"{schema} b{batch}: stored block times not "
                        "sequential (second engine started before the "
                        "first ended): " + "; ".join(time_order_bad)
                    )
                if parity_bad:
                    incomplete.append(
                        f"{schema} b{batch}: repeat order does not "
                        "match the fixed AB/BA (zig goes first on even "
                        "repeats): " + "; ".join(parity_bad)
                    )
                complete = {k: v for k, v in blocks.items()
                            if "zig" in v and "xgrammar" in v
                            and v["xgrammar"] > 0}
                min_blocks = int(ereq.get("min_blocks_per_case", 1))
                if len(complete) < min_blocks:
                    incomplete.append(
                        f"{schema} b{batch}: complete paired blocks "
                        f"{len(complete)} < {min_blocks}"
                    )
                n_zf = sum(1 for v in complete.values()
                           if v.get("order", {}).get("zig") == 0)
                n_xf = len(complete) - n_zf
                if complete and abs(n_zf - n_xf) > 1:
                    incomplete.append(
                        f"{schema} b{batch}: engine order not balanced "
                        f"(zig first {n_zf}, xgrammar first {n_xf})"
                    )
                ratios = [v["zig"] / v["xgrammar"] for v in complete.values()]
                if ratios:
                    rmed = median(ratios)
                    rci = _bootstrap_median_ci(
                        ratios, n=int(ereq.get("bootstrap_n", 10000)),
                        seed=int(ereq.get("bootstrap_seed", 42)))
                    limit = 1.0 + max_reg / 100.0
                    by_col = {}
                    for (name, _rep), v in complete.items():
                        by_col.setdefault(name.split("/")[0], []).append(
                            v["zig"] / v["xgrammar"])
                    case["ratio_median"] = rmed
                    case["ratio_ci95"] = rci
                    case["ratio_per_collection"] = {
                        c: median(v) for c, v in sorted(by_col.items())}
                    case["n_blocks"] = len(complete)
                    case["reg"] = (rmed - 1.0) * 100.0
                    case["margin"] = bool(rci and rci[1] > limit
                                          and rmed <= limit)
                    if rmed > limit:
                        failures.append(
                            f"regression (paired median) {schema} b{batch}: "
                            f"{(rmed - 1.0) * 100.0:+.1f}% > {max_reg:g}%"
                        )
                else:
                    case["reg"] = None
                    incomplete.append(f"{schema} b{batch}: no paired blocks")
            else:
                case["reg"] = pooled_reg
                if pooled_reg > max_reg:
                    failures.append(
                        f"regression {schema} b{batch}: {pooled_reg:+.1f}% > {max_reg:g}%"
                    )
            cases.append(case)
    if failures:
        return "FAIL", failures + incomplete, cases
    if incomplete:
        return "INCONCLUSIVE", incomplete, cases
    return "OK", [], cases


def _validate_uniform_source(blob, primary_metric, min_obs, min_warm, min_cold,
                             cold_repeats=None):
    """Structural validation of one uniform source BEFORE merging.
    Returns a list of problems (empty = ok).
    A source with problems does NOT take part in number comparison and can
    never yield GO.
    cold_repeats (protocol v3) - required number of cold repeats fixed in
    the source's protocol block."""
    probs = []
    if cold_repeats is not None:
        proto = blob.get("protocol")
        if not isinstance(proto, dict):
            # Parameters must be readable; a non-dict
            # counts as absent (including the "cold repeats" problem).
            proto = {}
        cr = proto.get("cold_repeats")
        if not isinstance(cr, int) or cr < cold_repeats:
            probs.append(f"cold repeats {cr!r} < {cold_repeats}"
                         " (measurement parameters)")
        mo = proto.get("min_observations")
        if not isinstance(mo, int) or mo < min_obs:
            probs.append(f"parameter min_observations={mo!r} < {min_obs}")
        mw = proto.get("min_warm_observations")
        if not isinstance(mw, int) or mw < min_warm:
            probs.append(f"parameter min_warm_observations={mw!r} < {min_warm}")
    need = [(primary_metric[0], p) for p in _PERCENTILES] + [
        (m, p) for m, _ in _OTHER_LATENCIES for p in _PERCENTILES
    ]
    mins = {"primary": min_obs, "warm": min_warm, "cold": min_cold}
    obs_need = ((primary_metric[0], mins["primary"]),) + tuple(
        (m, mins[cls]) for m, cls in _DECISION_OBS if m != primary_metric[0]
    )
    engines = blob.get("engines")
    if not isinstance(engines, dict):
        # Field engines must be an object.
        engines = {}
    for name in _REQUIRED_ENGINES:
        e = engines.get(name)
        if not isinstance(e, dict):
            probs.append(f"missing engine {name}")
            continue
        missing, bad = [], []
        for m, p in need:
            stats = e.get(m)
            v = stats.get(p) if isinstance(stats, dict) else None
            if v is None:
                missing.append(f"{m}.{p}")
            elif not _finite_positive(v):
                # NaN/∞/zero/strings fail comparisons
                # (`NaN > threshold` = False) - the source is excluded.
                bad.append(f"{m}.{p}={v!r}")
        if missing:
            probs.append(f"{name}: missing fields {', '.join(missing)}")
        if bad:
            probs.append(
                f"{name}: non-positive/non-numeric values "
                f"{', '.join(bad)}")
        raw_by_metric = e.get("raw_ns")
        if not isinstance(raw_by_metric, dict):
            raw_by_metric = {}
        for metric, need_n in obs_need:
            raw = raw_by_metric.get(metric)
            if not isinstance(raw, list) or len(raw) < need_n:
                probs.append(
                    f"{name}: {metric}: raw observations "
                    f"{len(raw) if isinstance(raw, list) else 0} < {need_n}"
                )
    return probs


def _uniform_gate(dirs, files, label, primary_metric, min_obs, min_warm, min_cold,
                  max_extra, min_win, out, result, cold_repeats=None):
    """Check of a uniform source (primary scenario or secondary holdout)
    over one or more collections. Errors/threshold →
    result["fail"], missing data → result["inconclusive"], numbers →
    result["publish"][label].

    files = (required file, optional repeat in the same dir).

    Distinction (matching the published rule):
    missing required file → INCONCLUSIVE; PRESENT file with status != OK or
    corrupted → structural NO-GO; present but incomplete / with too few
    observations → INCONCLUSIVE and EXCLUDED from the merge (does not take
    part in number comparison).

    "worst-zig / best-competitor" applies to ALL valid sources (repeats and
    collections) equally; thresholds do not depend on the source.
    Returns n_rep (number of sources admitted to the merge) or None.
    cold_repeats (protocol v3) - required number of cold repeats."""
    sources = []  # (dir, file name, blob) - valid sources only
    excluded = 0
    for d, fname in ((d, f) for d in dirs for f in (files[0], files[1]) if f):
        required = fname == files[0]
        blob, problem = load_status(d, fname)
        if problem == "no file":
            if required:
                result["inconclusive"].append(
                    f"{label} ({_dir_label(d)}): no uniform data ({fname})"
                )
            continue
        if problem:
            result["fail"].append(
                f"{label} ({_dir_label(d)}): file {fname} {problem} - "
                "structural NO-GO"
            )
            continue
        if blob.get("status") != "OK":
            if required:
                result["fail"].append(
                    f"{label} ({_dir_label(d)}): required file {fname} "
                    f"present but status={blob.get('status')!r} - NO-GO"
                )
            else:
                result["fail"].append(
                    f"{label} ({_dir_label(d)}): repeat uniform "
                    f"source {fname} invalid "
                    f"(status={blob.get('status')!r}) - not included "
                    "in the decision"
                )
            continue
        probs = _validate_uniform_source(
            blob, primary_metric, min_obs, min_warm, min_cold,
            cold_repeats=cold_repeats)
        if probs:
            excluded += 1
            for p in probs:
                result["inconclusive"].append(
                    f"{label} ({_dir_label(d)}/{fname}) {p}"
                )
            continue
        sources.append((d, fname, blob))
    if not sources:
        return None

    merged = sources[0][2]
    for _, _, blob in sources[1:]:
        merged, _ = _combine_worst(merged, blob)
    n_rep = len(sources)
    engines = merged.get("engines", {})
    out(f"{label}: uniform, sources {n_rep}"
        + (f" (incomplete excluded: {excluded})" if excluded else "")
        + "; zig - worst source, competitors - best")
    za = engines.get(_ZIG)
    comp_names = [c for c in _COMPETITORS if c in engines]
    mm, pp = primary_metric
    comp_ready = [
        c for c in comp_names
        if isinstance(engines[c].get(mm), dict) and engines[c][mm].get(pp) is not None
    ]
    if za is None or not comp_ready or za.get(mm, {}).get(pp) is None:
        return n_rep
    best = min(comp_ready, key=lambda c: engines[c][mm][pp])
    z, c = za[mm][pp], engines[best][mm][pp]
    win = (c - z) / c * 100.0
    result["publish"][label] = {
        "metric": f"{mm}.{pp}", "zig_ns": z, "competitor": best,
        "competitor_ns": c, "win_pct": win, "n_rep": n_rep,
        "file": files[0],
    }
    out(f"{label}: primary metric {mm} {pp}: zig {us(z)} us vs {us(c)} us "
        f"for {best} ({win:+.1f}% better at threshold +{min_win:g}%)")
    if win < min_win:
        result["fail"].append(
            f"primary metric ({label}): win {win:+.1f}% < {min_win:g}%"
        )
    over = []
    for m, mlabel in _OTHER_LATENCIES:
        for p in _PERCENTILES:
            zv = za[m].get(p) if isinstance(za.get(m), dict) else None
            cv = engines[best][m].get(p) if isinstance(engines[best].get(m), dict) else None
            if zv is None or cv is None:
                continue
            deg = (zv - cv) / cv * 100.0
            if deg > max_extra:
                over.append((f"{mlabel} {p}", deg, zv, cv))
    if over:
        out(f"{label}: other latencies vs {best} above +{max_extra:g}%:")
        for lbl, deg, zv, cv in over:
            out(f"  {lbl}: zig {us(zv)} us vs {us(cv)} us ({deg:+.1f}%)")
            result["fail"].append(
                f"extra latency ({label}) {lbl}: {deg:+.1f}% > +{max_extra:g}%"
            )
    else:
        out(f"{label}: other latencies vs {best}: all ≤ +{max_extra:g}% - OK")
    return n_rep


def control_gate(d, manifest=None, out=print):
    """Single gate §10.6 over one collection (computational API for tests
    and rechecks; does NOT check protocol composition - final acceptance
    goes through acceptance_gate)."""
    return control_gate_multi([d], manifest=manifest, out=out)


def control_gate_multi(dirs, manifest=None, out=print, protocol=None):
    """Single gate §10.6:
    structured verdict over one or more collections of the same code.

    protocol - spec of the active fixed acceptance protocol
    (acceptance_protocol_vN) or None (legacy mode). Protocol requirements:
    number of cold repeats of the first mask, balanced
    AB/BA order and paired median of ratios in e2e.

    The primary metric is fixed in the manifest (primary_metric_key = mask
    p99) - there are no more "options"; memory is published as an extra
    fact, not as an alternative path to GO. The secondary holdout
    (secondary_guard) is additionally checked with the
    same thresholds. Error/invalidity of a required run, an incomplete
    matrix or too few repeats block GO; missing data give INCONCLUSIVE.

    Collection merge: uniform sources of all collections
    are merged by "worst zig / best competitor"; e2e repeats are pooled;
    B1 is checked in every collection. A collection is not dropped based on
    measurement results - an invalid required file gives NO-GO, missing
    data give INCONCLUSIVE. Returns dict
    verdict/fail/inconclusive/publish; the report is printed via out."""
    man = manifest if manifest is not None else load_manifest()
    cs = man.get("control_scenario", {})
    spec = cs.get("e2e_guard", {}) if isinstance(cs.get("e2e_guard"), dict) else {}
    primary_metric = tuple(cs.get("primary_metric_key", ["fill_mask_ns", "p99"]))
    min_obs = int(cs.get("min_primary_observations", 10000))
    min_warm = int(cs.get("min_warm_observations", 10000))
    min_cold = int(cs.get("min_cold_observations", 30))
    min_proc = int(cs.get("min_coldproc_processes", 30))
    max_extra = float(cs.get("max_extra_latency_pct", 10.0))
    min_win = float(cs.get("min_primary_win_pct", 20.0))
    ureq = (protocol or {}).get("uniform_requirements") or {}
    ereq = (protocol or {}).get("e2e_requirements") or None
    min_cold_eff = int(ureq.get("min_cold_observations", min_cold))
    cold_repeats = ureq.get("cold_repeats")
    result = {"verdict": "INCONCLUSIVE", "fail": [], "inconclusive": [], "publish": {}}

    out("\n## Gate §10.6 (single)")
    out(f"collections: {len(dirs)}; merge: worst zig / best competitor "
        "over all sources"
        + (f"; protocol: cold repeats ≥{cold_repeats}, "
           + ("e2e - balanced AB/BA order and paired median"
              if ereq else "") if protocol else ""))
    if not spec.get("schemas"):
        result["inconclusive"].append(
            "manifest.control_scenario.e2e_guard without the required matrix"
        )
    _uniform_gate(
        dirs, ("bench_compare_uniform.json", "bench_compare_uniform_repeat.json"),
        "primary scenario", primary_metric, min_obs, min_warm, min_cold_eff,
        max_extra, min_win, out, result,
        cold_repeats=cold_repeats,
    )
    sec = cs.get("secondary_guard")
    if not isinstance(sec, dict) or not sec.get("uniform_file"):
        result["inconclusive"].append(
            "manifest.control_scenario.secondary_guard missing"
        )
    else:
        sfile = str(sec["uniform_file"])
        srep = (
            sfile[: -len(".json")] + "_repeat.json"
            if sfile.endswith(".json")
            else sfile + "_repeat"
        )
        _uniform_gate(
            dirs, (sfile, srep), "secondary holdout", primary_metric,
            int(sec.get("min_primary_observations", min_obs)),
            int(sec.get("min_warm_observations", min_warm)),
            int(ureq.get("min_cold_observations",
                         sec.get("min_cold_observations", min_cold))),
            float(sec.get("max_extra_latency_pct", max_extra)),
            float(sec.get("min_primary_win_pct", min_win)),
            out, result,
            cold_repeats=cold_repeats,
        )
    # B1: the process threshold (spec 10.5) is checked in EVERY collection
    # (a collection without B1 makes the whole acceptance
    # INCONCLUSIVE).
    coldproc_rows = []
    for d in dirs:
        cp, problem = load_status(d, "bench_coldproc.json")
        if problem == "no file":
            result["inconclusive"].append(
                f"no B1 data (30 processes) in {_dir_label(d)}"
            )
            continue
        if problem:
            result["fail"].append(
                f"B1 ({_dir_label(d)}): file {problem} - structural NO-GO"
            )
            continue
        if cp.get("status") != "OK":
            result["fail"].append(
                f"B1 ({_dir_label(d)}): required file present, "
                f"but status={cp.get('status')!r} - NO-GO"
            )
            continue
        engines_cp = cp.get("engines")
        if not isinstance(engines_cp, dict):
            # Structure before aggregation.
            result["fail"].append(
                f"B1 ({_dir_label(d)}): field engines corrupted - "
                "structural NO-GO"
            )
            continue
        st = engines_cp.get(_ZIG)
        if not isinstance(st, dict):
            st = {}
        total = cp.get("repeats", st.get("repeats_ok"))
        if st.get("repeats_ok") != total:
            result["fail"].append(
                f"B1 {_ZIG} ({_dir_label(d)}): ok={st.get('repeats_ok')} of {total}"
            )
        elif not isinstance(total, int) or total < min_proc:
            # A minimal file with repeats=1 used to be silently accepted and
            # kept GO: enforce spec 10.5 (≥30 processes).
            result["inconclusive"].append(
                f"B1 ({_dir_label(d)}): processes {total} < {min_proc} (spec 10.5)"
            )
        rss = st.get("peak_rss_bytes")
        rss_p50 = rss.get("p50") if isinstance(rss, dict) else None
        coldproc_rows.append({
            "dir": _dir_label(d),
            "zig_ok": st.get("repeats_ok"), "repeats": total,
            "rss_p50_mib": round((rss_p50 or 0) / 2**20, 1),
        })
    result["publish"]["coldproc"] = coldproc_rows
    e2e_status, e2e_reasons, e2e_cases = e2e_review(dirs, spec, ereq)
    dec = (ereq or {}).get("decision", "pooled_median")
    out("e2e guard (required: full matrix from manifest, errors and "
        "invalid outputs block GO; "
        + ("decision - median of PAIRED ratios in blocks (file x repeat), "
           "zig and xg adjacent in time, AB/BA order, bootstrap CI):"
           if dec == "paired_ratio_median" else
           "threshold - medians of repeats of all collections):"))
    max_reg = float(spec.get("max_regression_pct", 5.0))
    if ereq and ereq.get("max_regression_pct") is not None:
        max_reg = float(ereq["max_regression_pct"])
    for case in e2e_cases:
        reg = case.get("reg")
        if reg is None:
            out(f"  {case['schema']} b{case['batch']}: no data for the decision")
            continue
        flag = "OK" if reg <= max_reg else "FAIL"
        out(f"  {case['schema']} b{case['batch']}: {reg:+.1f}% {flag} "
            f"(zig {case['zig_median']:.2f}s vs xg {case['xg_median']:.2f}s; "
            f"n={case['n'][0]}/{case['n'][1]})")
        if "ratio_median" in case:
            ci = case.get("ratio_ci95") or [0.0, 0.0]
            out(f"    paired blocks: n={case.get('n_blocks')}, median "
                f"zig/xg ratio={case['ratio_median']:.4f}, CI95=["
                f"{ci[0]:.4f}; {ci[1]:.4f}]"
                + (", at the threshold margin (margin)" if case.get("margin") else "")
                + f"; pooled medians: {case.get('pooled_reg'):+.1f}%; per collection: "
                + ", ".join(f"{k}: {v:.3f}" for k, v in
                            (case.get("ratio_per_collection") or {}).items()))
        out(f"    zig repeats per source: {case['zig_per_file']} "
            "(first request published separately)")
    for msg in e2e_reasons:
        target = result["fail"] if e2e_status == "FAIL" else result["inconclusive"]
        target.append("e2e: " + msg)
    result["publish"]["e2e"] = {"status": e2e_status, "cases": e2e_cases}

    if result["fail"]:
        result["verdict"] = "NO-GO"
    elif result["inconclusive"]:
        result["verdict"] = "INCONCLUSIVE"
    else:
        result["verdict"] = "GO"
    out("")
    for msg in result["fail"]:
        out(f"  FAIL: {msg}")
    for msg in result["inconclusive"]:
        out(f"  INCONCLUSIVE: {msg}")
    out(f"VERDICT control scenario: {result['verdict']}")
    return result


def gate_exit_code(result):
    return {"GO": 0, "NO-GO": 1, "INCONCLUSIVE": 2}[result["verdict"]]


def cli_exit_code(result, mode):
    """CLI exit code: 3 - single-collection analysis (NOT acceptance)."""
    return 3 if mode == "analysis" else gate_exit_code(result)


def _collection_tag(d):
    """Collection id = dir name suffix after '_'."""
    b = os.path.basename(os.path.normpath(d))
    return b.rsplit("_", 1)[-1] if "_" in b else None


def active_protocol_name(man):
    """Name of the active fixed acceptance protocol (or None)."""
    cs = man.get("control_scenario", {})
    name = cs.get("active_acceptance_protocol")
    if isinstance(name, str) and name in cs:
        return name
    names = sorted(k for k in cs if k.startswith("acceptance_protocol"))
    return names[-1] if names else None


def protocol_sha256(spec):
    """Canonical sha256 of the protocol spec (for environment.txt and the gate)."""
    payload = json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# Protocol spec hash recorded in environment.txt by the acceptance_protocol_v3
# collections BEFORE the 2026-09-19 English translation of the spec text
# (rules and thresholds unchanged). Accepted alongside the recomputed hash so
# the recorded collections still pass the identity check.
LEGACY_PROTOCOL_SHA256 = {
    "acceptance_protocol_v3":
        "02af30e0990fa53b3d3c2560484b8593578ffc1f46bf280c59cdfb4ed5e88383",
}


def resolve_acceptance_set(dirs, manifest=None):
    """Composition check mode.

    Returns dict: mode ∈ {"analysis", "acceptance", "invalid"},
    reasons (for invalid), protocol (name), spec (spec), tags.
    One dir - collection analysis (not acceptance); a complete unique set
    of run_tags of the active protocol - acceptance; otherwise - invalid."""
    man = manifest if manifest is not None else load_manifest()
    out = {"mode": "invalid", "reasons": [], "protocol": None, "spec": None,
           "tags": []}
    if not dirs:
        out["reasons"].append("no dirs passed")
        return out
    name = active_protocol_name(man)
    if name is None:
        out["reasons"].append(
            "manifest has no fixed acceptance protocol "
            "(acceptance_protocol* / active_acceptance_protocol)")
        return out
    spec = man["control_scenario"][name]
    out["protocol"] = name
    out["spec"] = spec
    canon = [os.path.realpath(d) for d in dirs]
    if len(set(canon)) != len(canon):
        out["reasons"].append(
            "same dir passed twice (collection identifiers not unique)")
        return out
    if len(dirs) == 1:
        out["mode"] = "analysis"
        out["tags"] = [_collection_tag(dirs[0])]
        return out
    tags = [_collection_tag(d) for d in dirs]
    out["tags"] = tags
    want = list(spec.get("run_tags") or [])
    runs = int(spec.get("runs", len(want) or 0))
    if not want:
        out["reasons"].append(f"protocol {name} without required run_tags")
        return out
    if len(dirs) != runs:
        out["reasons"].append(
            f"{len(dirs)} collections passed, protocol {name} requires {runs}")
    missing = [t for t in want if t not in tags]
    extra = [t for t in tags if t not in want]
    if missing:
        out["reasons"].append(
            "missing required collections: " + ", ".join(missing))
    if extra:
        out["reasons"].append(
            "extraneous collections (not in protocol): " + ", ".join(extra))
    if len(set(tags)) != len(tags):
        out["reasons"].append("collection identifiers repeat: "
                              + ", ".join(tags))
    if out["reasons"]:
        return out
    out["mode"] = "acceptance"
    return out


def _parse_environment(path):
    d = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"^([a-z_0-9]+):\s*(.+)$", line.strip())
                if m and m.group(1) not in d:
                    d[m.group(1)] = m.group(2).strip()
    except OSError:
        return {}
    return d


def check_collection_identity(dirs, spec, protocol_name):
    """Same code revision and protocol across all collections: reads
    each collection's environment.txt and checks
    commit, protocol, protocol_sha256 and sources_sha256. Returns a list of
    problems (empty = identity confirmed)."""
    problems = []
    want_proto = protocol_sha256(spec)
    accepted_proto = {want_proto}
    legacy = LEGACY_PROTOCOL_SHA256.get(protocol_name)
    if legacy:
        accepted_proto.add(legacy)
    values = {"commit": {}, "sources_sha256": {}, "protocol": {}}
    for d in dirs:
        env = _parse_environment(os.path.join(d, "environment.txt"))
        if not env:
            problems.append(f"{_dir_label(d)}: missing/empty environment.txt - "
                            "code revision not fixed")
            continue
        for key in ("commit", "sources_sha256", "protocol",
                    "protocol_sha256"):
            if not env.get(key):
                problems.append(f"{_dir_label(d)}: environment.txt without "
                                f"field {key}")
        if env.get("protocol") and env["protocol"] != protocol_name:
            problems.append(
                f"{_dir_label(d)}: environment.txt collected under protocol "
                f"{env['protocol']!r}, active - {protocol_name!r}")
        if (env.get("protocol_sha256")
                and env["protocol_sha256"] not in accepted_proto):
            problems.append(
                f"{_dir_label(d)}: protocol hash in environment.txt does "
                "not match manifest")
        for key in values:
            if env.get(key):
                values[key][_dir_label(d)] = env[key]
    for key, got in values.items():
        vals = sorted(set(got.values()))
        if len(vals) > 1:
            problems.append(
                f"{key} differs between collections: " + "; ".join(
                    f"{d}={v[:12]}" for d, v in sorted(got.items())))
    return problems


def acceptance_gate(dirs, manifest=None, out=print):
    """Final acceptance: composition is checked against
    the active protocol BEFORE the decision.

    - one dir -> single-collection analysis (exit 3; explicitly NOT acceptance);
    - missing/repeated/extraneous collection -> INCONCLUSIVE (exit 2),
      GO impossible;
    - complete unique set of run_tags + same code revision
      (environment.txt) -> merged gate per protocol.

    Returns (result, mode)."""
    man = manifest if manifest is not None else load_manifest()
    resolved = resolve_acceptance_set(dirs, man)
    mode = resolved["mode"]
    if mode == "invalid":
        out("\n## Run composition - protocol check")
        out(f"active protocol: {resolved['protocol']!r}")
        for r in resolved["reasons"]:
            out(f"  COMPOSITION: {r}")
        result = {"verdict": "INCONCLUSIVE", "mode": "invalid",
                  "fail": [],
                  "inconclusive": [f"composition/identity: {r}"
                                   for r in resolved["reasons"]],
                  "publish": {}}
        out(f"VERDICT control scenario: {result['verdict']}")
        return result, mode
    if mode == "analysis":
        out("\n## SINGLE-COLLECTION ANALYSIS MODE - this is NOT final acceptance")
        out(f"active protocol: {resolved['protocol']!r}; acceptance "
            "needs the complete fixed set of collections "
            f"{resolved['spec'].get('run_tags')}")
        result = control_gate_multi(dirs, manifest=man, out=out)
        result["mode"] = "analysis"
        out(f"VERDICT (single-collection analysis, NOT ACCEPTANCE): "
            f"{result['verdict']}")
        return result, mode
    spec = resolved["spec"]
    proto = dict(spec)
    out("\n## Final acceptance per the fixed protocol")
    out(f"protocol: {resolved['protocol']}; fixed_at: "
        f"{spec.get('fixed_at')}; collections: {resolved['tags']}")
    problems = []
    if spec.get("environment_required"):
        problems = check_collection_identity(dirs, spec, resolved["protocol"])
    if problems:
        for p in problems:
            out(f"  IDENTITY: {p}")
        result = {"verdict": "INCONCLUSIVE", "mode": "acceptance",
                  "fail": [],
                  "inconclusive": ["composition/identity: " + p
                                   for p in problems],
                  "publish": {}}
        out(f"VERDICT control scenario: {result['verdict']}")
        return result, "acceptance"
    result = control_gate_multi(dirs, manifest=man, out=out, protocol=proto)
    result["mode"] = "acceptance"
    result["protocol"] = resolved["protocol"]
    return result, "acceptance"


def _pctl(samples, p):
    """Percentile with the same index rule as bench_common.percentile_stats
    (report tables must not diverge from the published aggregates)."""
    s = sorted(samples)
    n = len(s)
    return s[min(n - 1, max(0, int(round(p * (n - 1)))))]


def report_tables(dirs, manifest=None, out=print):
    """Numeric report tables with the same data/functions as the gate:
    per-file e2e medians, paired blocks, first mask diagnostics from raw
    observations."""
    man = manifest if manifest is not None else load_manifest()
    cs = man.get("control_scenario", {})
    spec = cs.get("e2e_guard", {})
    files = spec.get("runs_files", [])
    sec = cs.get("secondary_guard") or {}
    unif_files = ["bench_compare_uniform.json"]
    if sec.get("uniform_file"):
        unif_files.append(str(sec["uniform_file"]))
    out("# tables (generated by summarize_control.py --tables)\n")
    for schema in spec.get("schemas", []):
        for batch in spec.get("batches", []):
            with _guarded(f"e2e {schema} b{batch}", out):
                out(f"\n### e2e {schema} b{batch}\n")
                out("| source | n zig | n xg | zig median, s | xg median, s | Δ |")
                out("|---|---|---|---|---|---|")
                ratios = []
                for d in dirs:
                    for name in files:
                        blob = _safe_load(d, name, out)
                        if blob is None:
                            continue

                        def _vals(mode):
                            vals = []
                            for r in (blob.get("runs") or []):
                                s = (r.get("summary")
                                     if isinstance(r, dict) else None)
                                if (isinstance(s, dict)
                                        and r.get("schema") == schema
                                        and r.get("batch") == batch
                                        and r.get("mode") == mode
                                        and s.get("status") == "OK"
                                        and _finite_positive(s.get("total_s"))):
                                    vals.append(s["total_s"])
                            return vals

                        zig = _vals("zig_constrained")
                        xg = _vals("xgrammar_constrained")
                        if not zig or not xg:
                            continue
                        z, x = median(zig), median(xg)
                        if not x:
                            continue
                        out(f"| {_dir_label(d)}/{name} | {len(zig)} | "
                            f"{len(xg)} | {z:.3f} | {x:.3f} | "
                            f"{(z - x) / x * 100:+.1f}% |")
                        by_rep = {}
                        dup_note = False
                        for r in (blob.get("runs") or []):
                            s = (r.get("summary")
                                 if isinstance(r, dict) else None)
                            if (isinstance(s, dict)
                                    and r.get("schema") == schema
                                    and r.get("batch") == batch
                                    and s.get("status") == "OK"
                                    and _finite_positive(s.get("total_s"))):
                                pair = by_rep.setdefault(r.get("rep"), {})
                                if r.get("mode") in pair:
                                    # Duplicate (file,
                                    # engine, repeat) - acceptance impossible.
                                    dup_note = True
                                pair[r.get("mode")] = s["total_s"]
                        if dup_note:
                            out(f"  WARNING: {_dir_label(d)}/{name} contains "
                                "duplicate records (file, engine, repeat) - "
                                "acceptance over it is impossible")
                        for pair in by_rep.values():
                            if ("zig_constrained" in pair
                                    and "xgrammar_constrained" in pair
                                    and pair["xgrammar_constrained"] > 0):
                                ratios.append(pair["zig_constrained"]
                                              / pair["xgrammar_constrained"])
                if ratios:
                    rmed = median(ratios)
                    ci = _bootstrap_median_ci(ratios)
                    out(f"\npaired blocks: n={len(ratios)}, ratio median "
                        f"zig/xg={rmed:.4f} ({(rmed - 1) * 100:+.1f}%), "
                        f"CI95=[{ci[0]:.4f}; {ci[1]:.4f}]")
    out("\n### first mask: aggregates and raw tails\n")
    for d in dirs:
        for f in unif_files:
            blob = _safe_load(d, f, out)
            if blob is None:
                continue
            with _guarded(f"first mask {_dir_label(d)}/{f}", out):
                out(f"\n{_dir_label(d)}/{f}")
                out("| engine | n | p50 µs | p95 µs | p99 µs | >600 µs |")
                out("|---|---|---|---|---|---|")
                for name, e in (blob.get("engines") or {}).items():
                    fm = e.get("first_mask_ns") or {}
                    raw = (e.get("raw_ns") or {}).get("first_mask_ns") or []
                    if not fm or not raw:
                        continue
                    over = sum(1 for v in raw if v > 600_000)
                    out(f"| {name} | {fm.get('count')} | {us(fm.get('p50'))} | "
                        f"{us(fm.get('p95'))} | {us(fm.get('p99'))} | {over} |")
    by_corpus = {}
    pooled = {}
    for d in dirs:
        for f in unif_files:
            blob = _safe_load(d, f, out)
            if blob is None:
                continue
            with _guarded(f"tails {_dir_label(d)}/{f}", out):
                for name, e in (blob.get("engines") or {}).items():
                    raw = (e.get("raw_ns") or {}).get("first_mask_ns") or []
                    vals = [v for v in raw if v]
                    by_corpus.setdefault((f, name), []).extend(vals)
                    pooled.setdefault(name, []).extend(vals)
    # Corpora are published separately (the sum "131 vs 2131
    # out of 6000" mixes the primary scenario and the secondary holdout -
    # different loads, they cannot be compared or summed for thresholds).
    if by_corpus:
        out("\nper corpus (all collections):")
        for (f, name), vals in sorted(by_corpus.items()):
            over = sum(1 for v in vals if v > 600_000)
            out(f"  {f} / {name}: n={len(vals)}, >600 µs: {over}, "
                f"p95={us(_pctl(vals, 0.95))} µs, "
                f"p99={us(_pctl(vals, 0.99))} µs, max={us(max(vals))} µs")
    if pooled:
        out("\npooled raw first_mask_ns observations "
            "(all collections, BOTH uniform files together - load mixing):")
        for name, vals in sorted(pooled.items()):
            over = sum(1 for v in vals if v > 600_000)
            out(f"  {name}: n={len(vals)}, p95={us(_pctl(vals, 0.95))} µs, "
                f"p99={us(_pctl(vals, 0.99))} µs, max={us(max(vals))} µs, "
                f">600 µs: {over}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dirs", nargs="+", metavar="results-dir")
    ap.add_argument("--tables", action="store_true",
                    help="print the numeric report tables (same "
                         "functions as the gate) and exit")
    ap.add_argument("--protocol", default=None,
                    help="explicit protocol name from the manifest "
                         "(default - active_acceptance_protocol)")
    args = ap.parse_args()
    dirs = args.dirs
    d = dirs[0]
    man = load_manifest()
    if args.protocol:
        cs = man.setdefault("control_scenario", {})
        if args.protocol not in cs:
            print(f"protocol {args.protocol!r} not found in manifest")
            sys.exit(2)
        cs["active_acceptance_protocol"] = args.protocol
    if args.tables:
        report_tables(dirs, manifest=man)
        sys.exit(0)
    print(f"# summary {' + '.join(dirs)}\n")

    uni = _safe_load(d, "bench_compare_uniform.json", print)
    if uni and uni.get("status") == "OK":
        with _guarded("uniform", print):
            print("## uniform load (same tokenizer/schema/trace)")
            print(f"schema={uni['schema']} trace={uni['trace']['text']!r} "
                  f"len={uni['trace']['len']}")
            hdr = ["engine", "mask p50 us", "mask p95 us", "mask p99 us",
                   "accept p50 us", "accept p99 us", "cold compile p50 us",
                   "first mask p50 us", "warm compile p50 us"]
            print("| " + " | ".join(hdr) + " |")
            print("|" + "---|" * len(hdr))
            for name, e in uni["engines"].items():
                print("| " + " | ".join(map(str, [
                    name,
                    us(e["fill_mask_ns"]["p50"]), us(e["fill_mask_ns"]["p95"]),
                    us(e["fill_mask_ns"]["p99"]),
                    us(e["accept_ns"]["p50"]), us(e["accept_ns"]["p99"]),
                    us(e["cold_compile_ns"]["p50"]),
                    us(e["first_mask_ns"]["p50"]),
                    us(e["warm_compile_ns"]["p50"]),
                ])) + " |")
            # The whole gate §10.6 (including e2e and warm compile) is
            # computed at the end of the report, see control_gate().

    cp = _safe_load(d, "bench_coldproc.json", print)
    if cp and cp.get("status") == "OK":
        with _guarded("B1", print):
            print("\n## B1: 30 independent processes")
            print("| engine | ok | compile p50 ms | first mask p50 ms | total cold p50 s | peak RSS p50 MiB |")
            print("|---|---|---|---|---|---|")
            for name, e in cp["engines"].items():
                if e.get("compile_ns", {}).get("count", 0) == 0:
                    # engine unavailable in this environment: no measurements
                    print(f"| {name} | {e['repeats_ok']} | - | - | - | - |")
                    continue
                print(f"| {name} | {e['repeats_ok']} | "
                      f"{e['compile_ns']['p50'] / 1e6:.2f} | "
                      f"{e['first_mask_ns']['p50'] / 1e6:.2f} | "
                      f"{e['total_cold_ns']['p50'] / 1e9:.2f} | "
                      f"{e['peak_rss_bytes']['p50'] / 2**20:.0f} |")

    e2e = _safe_load(d, "bench_e2e_constrained.json", print)
    if e2e:
        with _guarded("B7 e2e", print):
            print("\n## B7: constrained vs constrained-baseline (medians over repeats)")
            print("| schema | batch | mode | total s | tok/s | ITL p50 ms | valid | lens |")
            print("|---|---|---|---|---|---|---|")
            groups = {}
            for r in e2e["runs"]:
                if r["summary"].get("status") != "OK":
                    continue
                k = (r["schema"], r["batch"], r["mode"])
                groups.setdefault(k, []).append(r["summary"])
            for (schema, batch, mode), sums in sorted(groups.items()):
                tot = median(s["total_s"] for s in sums)
                tps = median(s["tokens_per_s"] for s in sums)
                itl = median(s["itl_p50_ms"] for s in sums if s["itl_p50_ms"])
                vr = sums[0].get("valid_rows")
                lens = sorted({l for s in sums for l in s["row_lengths"]})
                lens_s = f"{lens[0]}..{lens[-1]}" if len(lens) > 1 else str(lens[0])
                print(f"| {schema} | {batch} | {mode} | {tot:.2f} | {tps:.1f} | "
                      f"{itl:.1f} | {vr}/{batch} | {lens_s} |")
            # Repeat statistics rule: total_s medians over all
            # repeats of both runs, threshold 5%; extremes are published.
            print("\ne2e guard (total_s medians over repeats, threshold 5%):")
            for r in _e2e_regressions_legacy(d):
                if r["reg"] is None:
                    print(f"  {r['schema']} b{r['batch']}: no baseline - INCONCLUSIVE")
                else:
                    print(f"  {r['schema']} b{r['batch']}: zig {r['zig_median']:.2f}s "
                          f"vs xg {r['xg_median']:.2f}s ({r['reg']:+.1f}%; "
                          f"worst zig {r['zig_worst']:.2f}s, best xg "
                          f"{r['xg_best']:.2f}s; n={r['n']})")

    lr = _safe_load(d, "bench_longrun.json", print)
    if lr and lr.get("status") == "OK":
        with _guarded("B5/B8", print):
            print("\n## B5/B8: long-run generation")
            for lim, r in lr["limits"].items():
                if r.get("status") != "OK":
                    print(f"  {lim} MiB: {r.get('status')} {r.get('errors')}")
                    continue
                p = r["mask_p99_plateau"]
                rss = r["rss_plateau"]
                st = r["core_stats_after"]
                print(f"  {lim} MiB: steps={r['mask_steps']} cycles={r['session_cycles']} "
                      f"errors={r['errors']} p99 plateau ratio={p and round(p['ratio'], 3)} "
                      f"rss ratio={rss and round(rss['ratio'], 3)} "
                      f"cache hits={st['cache_hits']} evictions={st['cache_evictions']}")

    st = _safe_load(d, "bench_schemas_tokenizers.json", print)
    if st and st.get("status") == "OK":
        with _guarded("B3/B6", print):
            print("\n## B3: schema switch")
            b3 = st["B3"]
            jsb = b3.get("jsb_support", {})
            print(f"  JSB-100: ok {jsb.get('ok')}, unsupported {jsb.get('unsupported')}, "
                  f"invalid {jsb.get('error')}; corpus schemas supported: "
                  f"{b3.get('corpus_supported_schemas')}")
            for mode, ph in b3.get("phases", {}).items():
                c = ph.get("cache", {})
                print(f"  {mode}: unique compile p50 {us(ph['unique_compile_ns']['p50'])} us, "
                      f"repeat p50 {us(ph['repeat_compile_ns']['p50'])} us, "
                      f"cache hits/misses/evictions {c.get('hits')}/{c.get('misses')}/{c.get('evictions')}")
            sp = b3.get("session_phase", {})
            fm = sp.get("fill_mask_ns", {})
            if fm:
                print(f"  session phase: schemas {sp.get('schemas')}, rounds {sp.get('rounds')}, "
                      f"fill_mask p50 {us(fm.get('p50'))} us, p99 {us(fm.get('p99'))} us")
            print("\n## B6: tokenizers")
            for tid, r in st["B6"].items():
                if not isinstance(r, dict) or r.get("status") != "OK":
                    print(f"  {tid}: {r.get('status') if isinstance(r, dict) else r}")
                    continue
                print(f"  {tid} ({r['family']}): vocab={r['vocab_size']} "
                      f"prepare={r.get('bundle_prepare_ns', 0) / 1e6:.1f} ms "
                      f"compile={r['compile_ns'] / 1e6:.1f} ms "
                      f"mask p50={us(r['fill_mask_ns']['p50'])} us "
                      f"core tok mem={r['core_mem_tokenizer_bytes'] / 2**20:.1f} MiB")
                if "sp_shim_verify" in r:
                    print(f"    sp_shim_verify: {r['sp_shim_verify']}")

    gp = _safe_load(d, "bench_gpu_mask_path.json", print)
    if gp and gp.get("status") == "OK":
        with _guarded("GPU", print):
            print("\n## GPU mask intervals (build / H2D / apply / full), p50 us")
            print("| engine | build | H2D+unpack | apply | full |")
            print("|---|---|---|---|---|")
            for name, e in gp["engines"].items():
                print(f"| {name} | {us(e['build_ns']['p50'])} | "
                      f"{us(e['h2d_ns']['p50'])} | {us(e['apply_ns']['p50'])} | "
                      f"{us(e['full_ns']['p50'])} |")

    res, mode = acceptance_gate(dirs, manifest=man)
    sys.exit(cli_exit_code(res, mode))


if __name__ == "__main__":
    main()
