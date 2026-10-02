"""Negative tests of the performance gate (SPEC section 10.6).

They verify that the gate cannot yield GO on erroneous, incomplete or
invalid data, and that merging repeats picks the competitor's best
result (not the worst).
"""

import json
import pathlib
import subprocess
import sys

import pytest

BENCH = pathlib.Path(__file__).resolve().parents[2] / "benchmarks"
sys.path.insert(0, str(BENCH))

import summarize_control as sc  # noqa: E402

MANIFEST = sc.load_manifest()

SCHEMAS = MANIFEST["control_scenario"]["e2e_guard"]["schemas"]
BATCHES = MANIFEST["control_scenario"]["e2e_guard"]["batches"]
MIN_REPS = MANIFEST["control_scenario"]["e2e_guard"]["min_reps_per_file"]
MIN_OBS = MANIFEST["control_scenario"]["min_primary_observations"]
MIN_WARM = MANIFEST["control_scenario"].get("min_warm_observations", MIN_OBS)
MIN_COLD = MANIFEST["control_scenario"].get("min_cold_observations", 30)
MIN_PROC = MANIFEST["control_scenario"].get("min_coldproc_processes", 30)

_LATENCY_METRICS = (
    "fill_mask_ns",
    "accept_ns",
    "cold_compile_ns",
    "warm_compile_ns",
    "first_mask_ns",
    "tokenizer_prepare_ns",
)


def _metric(base):
    return {"p50": base, "p95": base, "p99": base, "count": MIN_OBS,
            "min": base, "max": base, "mean": base}


def _engine(name, p99, *, raw=MIN_OBS, raw_warm=MIN_WARM, raw_cold=MIN_COLD,
            warm=10):
    e = {m: _metric(warm) for m in _LATENCY_METRICS}
    e["fill_mask_ns"] = _metric(p99)
    e["accept_ns"] = _metric(warm)
    e["warm_compile_ns"] = _metric(warm)
    # p99 of every decision class must have enough
    # observations (mask/accept/warm - ≥10k, cold - ≥30).
    e["raw_ns"] = {
        "fill_mask_ns": [1] * raw,
        "accept_ns": [1] * raw_warm,
        "warm_compile_ns": [1] * raw_warm,
        "cold_compile_ns": [1] * raw_cold,
        "first_mask_ns": [1] * raw_cold,
        "tokenizer_prepare_ns": [1] * raw_cold,
    }
    return e


def _uniform_blob(zig_p99=50, xg_p99=100, lg_p99=100, raw=MIN_OBS, *, raw_warm=MIN_WARM,
                  raw_cold=MIN_COLD):
    return {
        "status": "OK",
        "engines": {
            "zig_adaptive": _engine("zig", zig_p99, raw=raw, raw_warm=raw_warm,
                                    raw_cold=raw_cold),
            "xgrammar": _engine("xgrammar", xg_p99, raw=raw, raw_warm=raw_warm,
                               raw_cold=raw_cold),
            "llguidance": _engine("llguidance", lg_p99, raw=raw, raw_warm=raw_warm,
                                 raw_cold=raw_cold),
        },
    }


def _write_uniform(d, zig_p99=50, xg_p99=100, lg_p99=100, raw=MIN_OBS, *,
                   secondary_zig_p99=50, secondary_xg_p99=100,
                   write_secondary=True, raw_warm=MIN_WARM, raw_cold=MIN_COLD,
                   repeats=MIN_PROC):
    (d / "bench_compare_uniform.json").write_text(
        json.dumps(_uniform_blob(zig_p99, xg_p99, lg_p99, raw,
                                 raw_warm=raw_warm, raw_cold=raw_cold)))
    if write_secondary:
        # Secondary holdout - required for GO.
        (d / "bench_compare_uniform_secondary.json").write_text(
            json.dumps(_uniform_blob(secondary_zig_p99, secondary_xg_p99,
                                     secondary_xg_p99, raw,
                                     raw_warm=raw_warm, raw_cold=raw_cold)))
    (d / "bench_coldproc.json").write_text(json.dumps({
        "status": "OK",
        "repeats": repeats,
        "engines": {
            "zig_adaptive": {"repeats_ok": repeats,
                              "peak_rss_bytes": {"p50": 100 * 2**20}},
        },
    }))


def _run(schema, batch, mode, rep, total_s, **over):
    summary = {
        "status": "OK",
        "total_s": total_s,
        "valid_rows": batch,
        "completed_rows": batch,
        "validity_rate": 1.0,
        "ttft_ms": 10.0,
    }
    summary.update(over)
    return {"schema": schema, "batch": batch, "mode": mode, "rep": rep,
            "summary": summary}


def _write_e2e(d, zig_s=1.0, xg_s=1.0, reps=MIN_REPS):
    runs = []
    for schema in SCHEMAS:
        for batch in BATCHES:
            for mode, base in (("zig_constrained", zig_s),
                               ("xgrammar_constrained", xg_s)):
                for rep in range(reps):
                    runs.append(_run(schema, batch, mode, rep, base))
    for name in ("bench_e2e_constrained.json",
                 "bench_e2e_constrained_repeat.json"):
        (d / name).write_text(json.dumps({"runs": list(runs)}))
    return runs


def _mutate(d, pred, **fields):
    for name in ("bench_e2e_constrained.json",
                 "bench_e2e_constrained_repeat.json"):
        p = d / name
        blob = json.loads(p.read_text())
        for r in blob["runs"]:
            if pred(r):
                r["summary"].update(fields)
        p.write_text(json.dumps(blob))


def _gate(d):
    return sc.control_gate(str(d), manifest=MANIFEST, out=lambda *a: None)


def test_gate_go_on_healthy_synthetic(tmp_path):
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    res = _gate(tmp_path)
    assert res["verdict"] == "GO", res
    assert sc.gate_exit_code(res) == 0


def test_gate_no_go_on_error_run(tmp_path):
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    _mutate(tmp_path, lambda r: r["schema"] == SCHEMAS[2] and r["batch"] == 8
            and r["mode"] == "zig_constrained", status="ERROR")
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO"
    assert any("status=ERROR" in m for m in res["fail"]), res
    assert sc.gate_exit_code(res) == 1


def test_gate_inconclusive_on_missing_case(tmp_path):
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    p = tmp_path / "bench_e2e_constrained.json"
    blob = json.loads(p.read_text())
    blob["runs"] = [r for r in blob["runs"]
                    if not (r["schema"] == SCHEMAS[1] and r["batch"] == 8)]
    p.write_text(json.dumps(blob))
    res = _gate(tmp_path)
    assert res["verdict"] == "INCONCLUSIVE"
    assert any("repeats" in m for m in res["inconclusive"]), res
    assert sc.gate_exit_code(res) == 2


def test_gate_inconclusive_on_insufficient_reps(tmp_path):
    _write_uniform(tmp_path)
    _write_e2e(tmp_path, reps=MIN_REPS - 1)
    res = _gate(tmp_path)
    assert res["verdict"] == "INCONCLUSIVE"
    assert any(f"<{MIN_REPS}" in m for m in res["inconclusive"]), res


def test_gate_no_go_on_invalid_rows(tmp_path):
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    _mutate(tmp_path, lambda r: r["schema"] == SCHEMAS[0] and r["batch"] == 32
            and r["mode"] == "zig_constrained",
            valid_rows=31, validity_rate=31 / 32)
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO"
    assert any("valid_rows" in m for m in res["fail"]), res


def test_gate_no_go_on_incomplete_documents(tmp_path):
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    _mutate(tmp_path, lambda r: r["schema"] == SCHEMAS[1] and r["batch"] == 1
            and r["mode"] == "xgrammar_constrained", completed_rows=0)
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO"
    assert any("completed_rows" in m for m in res["fail"]), res


def test_gate_inconclusive_on_missing_percentile(tmp_path):
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    p = tmp_path / "bench_compare_uniform.json"
    uni = json.loads(p.read_text())
    del uni["engines"]["zig_adaptive"]["accept_ns"]["p95"]
    p.write_text(json.dumps(uni))
    res = _gate(tmp_path)
    assert res["verdict"] == "INCONCLUSIVE"
    assert any("accept_ns.p95" in m for m in res["inconclusive"]), res


def test_gate_inconclusive_on_short_raw(tmp_path):
    _write_uniform(tmp_path, raw=MIN_OBS - 1)
    _write_e2e(tmp_path)
    res = _gate(tmp_path)
    assert res["verdict"] == "INCONCLUSIVE"
    assert any("raw observations" in m for m in res["inconclusive"]), res


def test_gate_inconclusive_on_short_warm_raw(tmp_path):
    # Warm compile used to run on 100 observations and pass.
    _write_uniform(tmp_path, raw_warm=MIN_WARM - 1)
    _write_e2e(tmp_path)
    res = _gate(tmp_path)
    assert res["verdict"] == "INCONCLUSIVE"
    assert any("warm_compile_ns" in m and "raw observations" in m
               for m in res["inconclusive"]), res


def test_gate_inconclusive_on_short_cold_raw(tmp_path):
    _write_uniform(tmp_path, raw_cold=MIN_COLD - 1)
    _write_e2e(tmp_path)
    res = _gate(tmp_path)
    assert res["verdict"] == "INCONCLUSIVE"
    assert any("cold_compile_ns" in m and "raw observations" in m
               for m in res["inconclusive"]), res


def test_gate_inconclusive_on_single_b1_process(tmp_path):
    # A minimal B1 file (repeats=1) used to keep GO.
    _write_uniform(tmp_path, repeats=1)
    _write_e2e(tmp_path)
    res = _gate(tmp_path)
    assert res["verdict"] == "INCONCLUSIVE"
    assert any("B1" in m and "processes" in m for m in res["inconclusive"]), res
    assert sc.gate_exit_code(res) == 2


def test_gate_no_go_on_e2e_error_without_total_s(tmp_path):
    # summary={status:ERROR,error:...} without total_s used to cause a
    # KeyError instead of a structured NO-GO.
    _write_uniform(tmp_path)
    runs = _write_e2e(tmp_path)
    p = tmp_path / "bench_e2e_constrained.json"
    blob = json.loads(p.read_text())
    blob["runs"][0]["summary"] = {"status": "ERROR", "error": "review fixture"}
    p.write_text(json.dumps(blob))
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO"
    assert any("status=ERROR" in m for m in res["fail"]), res


def test_gate_no_go_when_primary_win_too_small(tmp_path):
    # 85 vs 100 - a ~15% win, below the 20% threshold.
    _write_uniform(tmp_path, zig_p99=85, xg_p99=100)
    _write_e2e(tmp_path)
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO"
    assert any("primary metric" in m for m in res["fail"]), res


def test_gate_no_go_on_warm_compile_regression(tmp_path):
    _write_uniform(tmp_path)
    p = tmp_path / "bench_compare_uniform.json"
    uni = json.loads(p.read_text())
    uni["engines"]["zig_adaptive"]["warm_compile_ns"] = _metric(30)
    p.write_text(json.dumps(uni))
    _write_e2e(tmp_path)
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO"
    assert any("warm compile" in m for m in res["fail"]), res


def test_gate_no_go_on_e2e_regression(tmp_path):
    _write_uniform(tmp_path)
    _write_e2e(tmp_path, zig_s=1.0, xg_s=0.8)
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO"
    assert any("regression" in m for m in res["fail"]), res


def test_gate_no_go_on_b1_errors(tmp_path):
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    cp = json.loads((tmp_path / "bench_coldproc.json").read_text())
    cp["engines"]["zig_adaptive"]["repeats_ok"] = 29
    (tmp_path / "bench_coldproc.json").write_text(json.dumps(cp))
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO"
    assert any("B1" in m for m in res["fail"]), res


def test_gate_inconclusive_on_missing_secondary_corpus(tmp_path):
    # Without the secondary holdout the verdict cannot be GO.
    _write_uniform(tmp_path, write_secondary=False)
    _write_e2e(tmp_path)
    res = _gate(tmp_path)
    assert res["verdict"] == "INCONCLUSIVE"
    assert any("secondary" in m for m in res["inconclusive"]), res
    assert sc.gate_exit_code(res) == 2


def test_gate_no_go_on_secondary_weak_win(tmp_path):
    # Primary scenario passed, secondary failed: this is NO-GO, not a
    # pick of the convenient source (thresholds are the same for both).
    _write_uniform(tmp_path, secondary_zig_p99=85, secondary_xg_p99=100)
    _write_e2e(tmp_path)
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO"
    assert any("secondary" in m and "primary metric" in m
               for m in res["fail"]), res


def test_gate_no_go_on_secondary_extra_latency(tmp_path):
    _write_uniform(tmp_path)
    p = tmp_path / "bench_compare_uniform_secondary.json"
    uni = json.loads(p.read_text())
    uni["engines"]["zig_adaptive"]["first_mask_ns"] = _metric(150)
    p.write_text(json.dumps(uni))
    _write_e2e(tmp_path)
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO"
    assert any("secondary" in m and "mask" in m for m in res["fail"]), res


def test_combine_worst_picks_competitor_best(tmp_path):
    # Out of competitor p99 100/200, 100 is picked (best repeat),
    # zig takes the worst.
    _write_uniform(tmp_path, zig_p99=50, xg_p99=100)
    uni = json.loads((tmp_path / "bench_compare_uniform.json").read_text())
    uni2 = json.loads((tmp_path / "bench_compare_uniform.json").read_text())
    uni2["engines"]["xgrammar"]["fill_mask_ns"]["p99"] = 200
    uni2["engines"]["zig_adaptive"]["fill_mask_ns"]["p99"] = 40
    merged, n = sc._combine_worst(uni, uni2)
    assert n == 2
    assert merged["engines"]["xgrammar"]["fill_mask_ns"]["p99"] == 100
    assert merged["engines"]["zig_adaptive"]["fill_mask_ns"]["p99"] == 50


def _repeat_copy(tmp_path, base_name, *, mutate=None):
    src = tmp_path / (base_name + ".json")
    data = json.loads(src.read_text())
    if mutate:
        mutate(data)
    (tmp_path / (base_name + "_repeat.json")).write_text(json.dumps(data))
    return data


def test_gate_inconclusive_on_repeat_without_observations(tmp_path):
    # A repeat uniform with the same aggregates but empty
    # raw_ns used to take part in the comparison and keep GO. Every
    # present source must be validated before merging.
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    for base_name in ("bench_compare_uniform", "bench_compare_uniform_secondary"):
        _repeat_copy(tmp_path, base_name, mutate=lambda d: [
            e.__setitem__("raw_ns", {}) for e in d["engines"].values()
        ])
    res = _gate(tmp_path)
    assert res["verdict"] == "INCONCLUSIVE", res
    assert any("raw observations" in m for m in res["inconclusive"]), res
    assert sc.gate_exit_code(res) == 2


def test_gate_no_go_on_erroneous_repeat_source(tmp_path):
    # A present repeat source with status != OK must not
    # slip into the decision unnoticed.
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    _repeat_copy(tmp_path, "bench_compare_uniform", mutate=lambda d: d.__setitem__("status", "ERROR"))
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO", res
    assert any("invalid" in m for m in res["fail"]), res
    assert sc.gate_exit_code(res) == 1


def test_gate_merge_healthy_repeat_worst_zig_best_competitor(tmp_path):
    # A full repeat takes part in the merge: worst zig / best competitor.
    # zig 50->60 (60 is taken), xg 100->200 (100 is taken).
    _write_uniform(tmp_path, zig_p99=50, xg_p99=100, lg_p99=100)
    _write_e2e(tmp_path)
    _repeat_copy(tmp_path, "bench_compare_uniform", mutate=lambda d: (
        d["engines"]["zig_adaptive"]["fill_mask_ns"].update(_metric(60)),
        d["engines"]["xgrammar"]["fill_mask_ns"].update(_metric(200)),
        d["engines"]["llguidance"]["fill_mask_ns"].update(_metric(120)),
    ))
    _repeat_copy(tmp_path, "bench_compare_uniform_secondary")
    res = _gate(tmp_path)
    assert res["verdict"] == "GO", res
    assert res["publish"]["primary scenario"]["zig_ns"] == 60
    assert res["publish"]["primary scenario"]["competitor_ns"] == 100
    assert res["publish"]["primary scenario"]["n_rep"] == 2


# ---------- Protocol v3 composition/identity and incomplete data:
# ---------- balanced AB/BA, 1000 cold, paired blocks

_PROTO_NAME = sc.active_protocol_name(MANIFEST)
_PROTO = MANIFEST["control_scenario"][_PROTO_NAME]
_V3_COLD = int((_PROTO.get("uniform_requirements") or {}).get(
    "min_cold_observations", 1000))


def _uniform_blob_v3(zig_p99=50, xg_p99=100, lg_p99=100):
    blob = _uniform_blob(zig_p99, xg_p99, lg_p99, raw=MIN_OBS,
                         raw_warm=MIN_WARM, raw_cold=_V3_COLD)
    blob["protocol"] = {
        "cold_repeats": _V3_COLD,
        "min_observations": MIN_OBS,
        "min_warm_observations": MIN_WARM,
    }
    return blob


def _e2e_run_v3(schema, batch, mode, rep, total_s, order_pos,
                drop_order_pos=False, t_start_ns=None, t_end_ns=None):
    rec = {"schema": schema, "batch": batch, "mode": mode, "rep": rep,
           "summary": {"status": "OK", "total_s": total_s,
                       "valid_rows": batch, "completed_rows": batch,
                       "validity_rate": 1.0, "ttft_ms": 10.0}}
    if not drop_order_pos:
        rec["order_pos"] = order_pos
    if t_start_ns is not None:
        rec["t_start_ns"] = t_start_ns
    if t_end_ns is not None:
        rec["t_end_ns"] = t_end_ns
    return rec


def _write_e2e_v3(d, *, zig_by_rep=None, xg_by_rep=None, reps=4,
                  orders=None, drop_order_pos=False, no_times=False):
    zig_by_rep = zig_by_rep or {}
    xg_by_rep = xg_by_rep or {}
    runs = []
    clock = 1_000_000_000
    for schema in SCHEMAS:
        for batch in BATCHES:
            for rep in range(reps):
                first = "zig" if rep % 2 == 0 else "xg"
                if orders and rep in orders:
                    first = orders[rep]
                zpos = 0 if first == "zig" else 1
                vals = {"zig_constrained": zig_by_rep.get(rep, 1.0),
                        "xgrammar_constrained": xg_by_rep.get(rep, 1.0)}
                # Times are assigned in the actual block order: the gate
                # checks positions, order and total_s.
                seq = (["zig_constrained", "xgrammar_constrained"]
                       if zpos == 0
                       else ["xgrammar_constrained", "zig_constrained"])
                times = {}
                for mode in seq:
                    t0 = clock
                    t1 = t0 + int(round(vals[mode] * 1e9))
                    times[mode] = (t0, t1)
                    clock = t1 + 1_000_000  # 1 ms between block launches
                for mode in ("zig_constrained", "xgrammar_constrained"):
                    t0, t1 = times[mode]
                    runs.append(_e2e_run_v3(
                        schema, batch, mode, rep, vals[mode],
                        zpos if mode == "zig_constrained" else 1 - zpos,
                        drop_order_pos,
                        None if no_times else t0,
                        None if no_times else t1))
    blob = {"runs": runs,
            "protocol": {"engine_order": "balanced_ab_ba", "reps": reps,
                         "warmed_mode": True}}
    for name in ("bench_e2e_constrained.json",
                 "bench_e2e_constrained_repeat.json"):
        (d / name).write_text(json.dumps(blob))


def _write_collection_v3(d):
    (d / "bench_compare_uniform.json").write_text(
        json.dumps(_uniform_blob_v3()))
    (d / "bench_compare_uniform_secondary.json").write_text(
        json.dumps(_uniform_blob_v3()))
    (d / "bench_coldproc.json").write_text(json.dumps({
        "status": "OK", "repeats": MIN_PROC,
        "engines": {"zig_adaptive": {
            "repeats_ok": MIN_PROC,
            "peak_rss_bytes": {"p50": 100 * 2**20}}},
    }))
    _write_e2e_v3(d)


def _write_env_v3(d, *, commit="a" * 40, src="b" * 64, protocol=None,
                  proto_hash=None):
    lines = [
        "# env fixture (review 4bf4ada E1)",
        "date: 2026-09-19T00:00:00Z",
        f"commit: {commit}",
        f"protocol: {protocol or _PROTO_NAME}",
        f"protocol_sha256: {proto_hash or sc.protocol_sha256(_PROTO)}",
        f"sources_sha256: {src}",
    ]
    (d / "environment.txt").write_text("\n".join(lines) + "\n")


def _v3_dirs(tmp_path, tags=None):
    dirs = []
    for i, tag in enumerate(tags or _PROTO["run_tags"]):
        d = tmp_path / f"20260919T0000{i:02d}_{tag}"
        d.mkdir()
        dirs.append(d)
    return dirs


def _full_v3_dirs(tmp_path, **e2e_kwargs):
    dirs = _v3_dirs(tmp_path)
    for d in dirs:
        _write_collection_v3(d)
        if e2e_kwargs:
            _write_e2e_v3(d, **e2e_kwargs)
        _write_env_v3(d)
    return dirs


def _acceptance(dirs):
    return sc.acceptance_gate([str(x) for x in dirs], manifest=MANIFEST,
                              out=lambda *a: None)


def test_resolve_single_dir_is_analysis():
    res = sc.resolve_acceptance_set(["x/20260919T000000_v3-run1"],
                                    manifest=MANIFEST)
    assert res["mode"] == "analysis"
    assert res["protocol"] == _PROTO_NAME


def test_resolve_duplicate_is_invalid():
    p = "x/20260919T000000_v3-run1"
    res = sc.resolve_acceptance_set([p, p, p], manifest=MANIFEST)
    assert res["mode"] == "invalid"
    assert any("passed twice" in r for r in res["reasons"]), res


def test_resolve_missing_collection_is_invalid():
    tags = _PROTO["run_tags"]
    res = sc.resolve_acceptance_set(
        [f"x/20260919T000000_{t}" for t in tags[:2]], manifest=MANIFEST)
    assert res["mode"] == "invalid"
    assert any("missing required collections" in r for r in res["reasons"])


def test_resolve_extraneous_collection_is_invalid():
    tags = list(_PROTO["run_tags"])[:2] + ["postoronniy"]
    res = sc.resolve_acceptance_set(
        [f"x/20260919T000000_{t}" for t in tags], manifest=MANIFEST)
    assert res["mode"] == "invalid"
    assert any("extraneous collections" in r for r in res["reasons"])


def test_resolve_full_set_is_acceptance():
    res = sc.resolve_acceptance_set(
        [f"x/20260919T000000_{t}" for t in _PROTO["run_tags"]],
        manifest=MANIFEST)
    assert res["mode"] == "acceptance"


def test_cli_exit_code_analysis_is_three():
    assert sc.cli_exit_code({"verdict": "GO"}, "analysis") == 3
    assert sc.cli_exit_code({"verdict": "GO"}, "acceptance") == 0
    assert sc.cli_exit_code({"verdict": "NO-GO"}, "acceptance") == 1


def test_acceptance_go_on_full_v3_set(tmp_path):
    dirs = _full_v3_dirs(tmp_path)
    res, mode = _acceptance(dirs)
    assert mode == "acceptance", res
    assert res["verdict"] == "GO", res
    assert sc.cli_exit_code(res, mode) == 0


def test_acceptance_requires_environment(tmp_path):
    dirs = _full_v3_dirs(tmp_path)
    (dirs[-1] / "environment.txt").unlink()
    res, mode = _acceptance(dirs)
    assert mode == "acceptance"
    assert res["verdict"] == "INCONCLUSIVE", res
    assert any("environment.txt" in m for m in res["inconclusive"]), res
    assert sc.cli_exit_code(res, mode) == 2


def test_acceptance_rejects_revision_mismatch(tmp_path):
    dirs = _full_v3_dirs(tmp_path)
    _write_env_v3(dirs[-1], commit="f" * 40)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "INCONCLUSIVE", res
    assert any("commit differs" in m for m in res["inconclusive"]), res


def test_acceptance_rejects_wrong_protocol_hash(tmp_path):
    dirs = _full_v3_dirs(tmp_path)
    _write_env_v3(dirs[0], proto_hash="0" * 64)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "INCONCLUSIVE", res
    assert any("protocol hash" in m for m in res["inconclusive"]), res


def test_acceptance_accepts_legacy_protocol_hash(tmp_path):
    # The v3 spec text moved to English; the recorded collections carry
    # the pre-translation spec hash (rules unchanged).
    dirs = _full_v3_dirs(tmp_path)
    legacy = sc.LEGACY_PROTOCOL_SHA256[_PROTO_NAME]
    for d in dirs:
        _write_env_v3(d, proto_hash=legacy)
    res, mode = _acceptance(dirs)
    assert mode == "acceptance"
    assert not any("protocol hash" in m for m in res["inconclusive"]), res
    assert res["verdict"] == "GO", res


def test_v3_paired_ratio_fails_on_uniform_drift(tmp_path):
    dirs = _full_v3_dirs(tmp_path, zig_by_rep={r: 1.06 for r in range(4)})
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "NO-GO", res
    assert any("paired median" in m for m in res["fail"]), res


def test_v3_paired_ratio_passes_with_margin_flag(tmp_path):
    # 12 blocks with ratio 1.0 and 12 with 1.08: median 1.04 (<=5%) passes,
    # the upper CI bound crosses the threshold -> margin flag (verdict
    # unchanged).
    dirs = _full_v3_dirs(tmp_path, zig_by_rep={0: 1.0, 1: 1.0,
                                               2: 1.08, 3: 1.08})
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "GO", res
    case = res["publish"]["e2e"]["cases"][0]
    assert case["margin"] is True, case
    assert abs(case["ratio_median"] - 1.04) < 1e-9, case
    assert case["n_blocks"] == 24, case


def test_v3_unbalanced_order_is_inconclusive(tmp_path):
    dirs = _full_v3_dirs(tmp_path,
                         orders={r: "zig" for r in range(4)})
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "INCONCLUSIVE", res
    assert any("not balanced" in m for m in res["inconclusive"]), res


def test_v3_missing_order_pos_is_inconclusive(tmp_path):
    dirs = _full_v3_dirs(tmp_path, drop_order_pos=True)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "INCONCLUSIVE", res
    assert any("order_pos" in m for m in res["inconclusive"]), res


def test_v3_short_cold_repeats_is_inconclusive(tmp_path):
    dirs = _full_v3_dirs(tmp_path)
    p = dirs[0] / "bench_compare_uniform.json"
    blob = json.loads(p.read_text())
    blob["protocol"]["cold_repeats"] = 30
    p.write_text(json.dumps(blob))
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "INCONCLUSIVE", res
    assert any("cold repeats" in m for m in res["inconclusive"]), res


def test_gate_no_go_on_erroneous_main_uniform(tmp_path):
    # A present required uniform with status=ERROR
    # -> NO-GO (previously INCONCLUSIVE), as the published rule promises.
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    p = tmp_path / "bench_compare_uniform.json"
    blob = json.loads(p.read_text())
    blob["status"] = "ERROR"
    p.write_text(json.dumps(blob))
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO", res
    assert any("status='ERROR'" in m for m in res["fail"]), res
    assert sc.gate_exit_code(res) == 1


def test_gate_inconclusive_on_missing_main_uniform(tmp_path):
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    (tmp_path / "bench_compare_uniform.json").unlink()
    res = _gate(tmp_path)
    assert res["verdict"] == "INCONCLUSIVE", res
    assert any("no uniform data" in m for m in res["inconclusive"]), res


def test_gate_structured_on_null_p99_in_repeat(tmp_path):
    # A null p99 in the repeat used to crash the summary
    # with TypeError; now the source is excluded, the result is structured.
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    _repeat_copy(tmp_path, "bench_compare_uniform",
                 mutate=lambda d: d["engines"]["zig_adaptive"]
                 ["first_mask_ns"].__setitem__("p99", None))
    res = _gate(tmp_path)
    assert res["verdict"] == "INCONCLUSIVE", res
    assert any("first_mask_ns.p99" in m for m in res["inconclusive"]), res


def test_gate_no_go_on_corrupt_required_file(tmp_path):
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    (tmp_path / "bench_compare_uniform.json").write_text("{ this is not JSON")
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO", res
    assert any("corrupted" in m for m in res["fail"]), res


def test_bootstrap_median_ci_bounds():
    ci = sc._bootstrap_median_ci([1.0] * 10 + [1.2] * 10, n=2000, seed=1)
    assert 1.0 <= ci[0] <= 1.1 <= ci[1] <= 1.2, ci


# ---------- Paired record validation and a safe CLI on
# ---------- corrupted/incomplete files


def _mutate_e2e(d, name, fn):
    p = d / name
    blob = json.loads(p.read_text())
    fn(blob)
    p.write_text(json.dumps(blob))


def _cli(dirs):
    return subprocess.run(
        [sys.executable, str(BENCH / "summarize_control.py"),
         *[str(x) for x in dirs]],
        capture_output=True, text=True)


def test_v3_duplicate_pair_record_is_no_go(tmp_path):
    # A record copy (file, engine, repeat) used to be
    # silently overwritten by a later one, and the collection kept GO.
    dirs = _full_v3_dirs(tmp_path)

    def _prepend_duplicate(blob):
        dup = None
        for r in blob["runs"]:
            if (r["schema"], r["batch"]) == (SCHEMAS[0], BATCHES[0]) \
                    and r["mode"] == "zig_constrained" and r["rep"] == 0:
                dup = json.loads(json.dumps(r))
                break
        assert dup is not None
        dup["summary"]["total_s"] = 100.0
        dup["t_end_ns"] = dup["t_start_ns"] + 100 * 10**9
        blob["runs"].insert(0, dup)

    _mutate_e2e(dirs[1], "bench_e2e_constrained.json", _prepend_duplicate)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "NO-GO", res
    assert any("duplicate" in m for m in res["fail"]), res
    assert sc.cli_exit_code(res, mode) == 1


def test_v3_same_position_records_is_no_go(tmp_path):
    # Positions 0/0 on even and 1/1 on odd repeats used
    # to pass the balance check and keep GO.
    dirs = _full_v3_dirs(tmp_path)

    def _sync_pos(blob):
        zpos = {}
        for r in blob["runs"]:
            if r["mode"] == "zig_constrained":
                zpos[(r["schema"], r["batch"], r["rep"])] = r["order_pos"]
        for r in blob["runs"]:
            if r["mode"] == "xgrammar_constrained":
                r["order_pos"] = zpos[(r["schema"], r["batch"], r["rep"])]

    for name in ("bench_e2e_constrained.json",
                 "bench_e2e_constrained_repeat.json"):
        _mutate_e2e(dirs[0], name, _sync_pos)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "NO-GO", res
    assert any("pair positions" in m for m in res["fail"]), res
    assert sc.cli_exit_code(res, mode) == 1


def test_v3_extra_repetition_is_no_go(tmp_path):
    # A repeat outside the fixed set {0..3} - the collection does not match
    # the measurement protocol; the observation cannot be silently dropped.
    dirs = _full_v3_dirs(tmp_path)

    def _add_extra(blob):
        extra = None
        for r in blob["runs"]:
            if (r["schema"], r["batch"]) == (SCHEMAS[0], BATCHES[0]) \
                    and r["mode"] == "zig_constrained" and r["rep"] == 0:
                extra = json.loads(json.dumps(r))
                break
        assert extra is not None
        extra["rep"] = 7
        extra["t_start_ns"] = extra["t_end_ns"] + 10**9
        extra["t_end_ns"] = extra["t_start_ns"] + int(1.0 * 1e9)
        blob["runs"].append(extra)

    _mutate_e2e(dirs[2], "bench_e2e_constrained.json", _add_extra)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "NO-GO", res
    assert any("outside the fixed set" in m for m in res["fail"]), res


def test_v3_missing_repetition_is_inconclusive(tmp_path):
    dirs = _full_v3_dirs(tmp_path)

    def _drop_rep3(blob):
        blob["runs"] = [
            r for r in blob["runs"]
            if not (r["mode"] == "zig_constrained" and r["rep"] == 3)
        ]

    _mutate_e2e(dirs[1], "bench_e2e_constrained_repeat.json", _drop_rep3)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "INCONCLUSIVE", res
    assert any("missing repeats" in m for m in res["inconclusive"]), res
    assert sc.cli_exit_code(res, mode) == 2


def test_v3_missing_times_is_inconclusive(tmp_path):
    dirs = _full_v3_dirs(tmp_path, no_times=True)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "INCONCLUSIVE", res
    assert any("t_start_ns" in m for m in res["inconclusive"]), res


def test_v3_block_time_order_violation_is_no_go(tmp_path):
    # Position 0 (zig, rep=0) must come first in time.
    dirs = _full_v3_dirs(tmp_path)

    def _move_zig_after(blob):
        z = x = None
        for r in blob["runs"]:
            if (r["schema"], r["batch"], r["rep"]) == (
                    SCHEMAS[0], BATCHES[0], 0):
                if r["mode"] == "zig_constrained":
                    z = r
                else:
                    x = r
        assert z is not None and x is not None and z["order_pos"] == 0
        dur = z["t_end_ns"] - z["t_start_ns"]
        z["t_start_ns"] = x["t_end_ns"] + 1_000_000
        z["t_end_ns"] = z["t_start_ns"] + dur

    _mutate_e2e(dirs[2], "bench_e2e_constrained.json", _move_zig_after)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "NO-GO", res
    assert any("not sequential" in m for m in res["fail"]), res


def test_v3_total_s_mismatch_is_no_go(tmp_path):
    dirs = _full_v3_dirs(tmp_path)

    def _bump_total(blob):
        for r in blob["runs"]:
            if (r["schema"], r["batch"], r["rep"], r["mode"]) == (
                    SCHEMAS[0], BATCHES[0], 1, "zig_constrained"):
                r["summary"]["total_s"] += 1.0

    _mutate_e2e(dirs[0], "bench_e2e_constrained.json", _bump_total)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "NO-GO", res
    assert any("not consistent" in m for m in res["fail"]), res


def test_gate_no_go_on_nonobject_json(tmp_path):
    # A valid JSON non-object also counts as a corrupted structure.
    _write_uniform(tmp_path)
    _write_e2e(tmp_path)
    (tmp_path / "bench_compare_uniform.json").write_text("[1, 2]")
    res = _gate(tmp_path)
    assert res["verdict"] == "NO-GO", res
    assert any("not an object" in m for m in res["fail"]), res


def test_cli_no_traceback_on_corrupt_json(tmp_path):
    # The CLI used to crash with a traceback on a
    # corrupted required file before calling acceptance_gate.
    dirs = _full_v3_dirs(tmp_path)
    (dirs[0] / "bench_compare_uniform.json").write_text("{ damaged")
    p = _cli(dirs)
    assert p.returncode == 1, (p.returncode, p.stdout, p.stderr)
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout
    assert "corrupted" in p.stdout


def test_cli_structured_on_missing_protocol_block(tmp_path):
    # Missing fields of the first dir: the summary degrades safely, the
    # gate gives the verdict (INCONCLUSIVE due to measurement parameters).
    dirs = _full_v3_dirs(tmp_path)
    p0 = dirs[0] / "bench_compare_uniform.json"
    blob = json.loads(p0.read_text())
    del blob["protocol"]
    p0.write_text(json.dumps(blob))
    p = _cli(dirs)
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout
    assert "cold repeats" in p.stdout


# ---------- Finiteness/positivity of reported times and structural
# ---------- validation of nested fields


def test_v3_invalid_total_s_variants_are_no_go(tmp_path):
    # NaN/±∞/zero/negative/bool used to pass
    # comparisons (`NaN > threshold` = False) and give a false GO.
    dirs = _full_v3_dirs(tmp_path)
    p_main = dirs[1] / "bench_e2e_constrained.json"
    orig = p_main.read_text()
    for bad in (float("nan"), float("inf"), float("-inf"), 0.0, -1.0, True):
        def _set_bad(blob, bad=bad):
            for r in blob["runs"]:
                if (r["schema"], r["batch"]) == (SCHEMAS[0], BATCHES[1]) \
                        and r["mode"] == "zig_constrained":
                    r["summary"]["total_s"] = bad
        _mutate_e2e(dirs[1], "bench_e2e_constrained.json", _set_bad)
        res, mode = _acceptance(dirs)
        assert res["verdict"] == "NO-GO", (bad, res)
        assert any("finite positive" in m
                   for m in res["fail"]), (bad, res)
        assert sc.cli_exit_code(res, mode) == 1
        p_main.write_text(orig)


def test_v3_zero_competitor_times_is_no_go(tmp_path):
    # Zero competitor times without stored times used to
    # reach median division - ZeroDivisionError.
    dirs = _full_v3_dirs(tmp_path)

    def _zero_xg(blob):
        for r in blob["runs"]:
            if (r["schema"], r["batch"]) == (SCHEMAS[0], BATCHES[0]) \
                    and r["mode"] == "xgrammar_constrained":
                r["summary"]["total_s"] = 0.0
                r.pop("t_start_ns", None)
                r.pop("t_end_ns", None)

    _mutate_e2e(dirs[0], "bench_e2e_constrained.json", _zero_xg)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "NO-GO", res
    assert any("finite positive" in m for m in res["fail"]), res


def test_v3_null_runs_is_no_go(tmp_path):
    # A null runs block used to crash the gate (iterating None).
    dirs = _full_v3_dirs(tmp_path)

    def _null_runs(blob):
        blob["runs"] = None

    _mutate_e2e(dirs[2], "bench_e2e_constrained.json", _null_runs)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "NO-GO", res
    assert any("not a list" in m for m in res["fail"]), res
    assert sc.cli_exit_code(res, mode) == 1


def test_v3_missing_runs_is_inconclusive(tmp_path):
    # A missing runs field - an incomplete file (not corrupted): verdict
    # INCONCLUSIVE on repeat count, without a crash.
    dirs = _full_v3_dirs(tmp_path)
    p = dirs[0] / "bench_e2e_constrained_repeat.json"
    blob = json.loads(p.read_text())
    del blob["runs"]
    p.write_text(json.dumps(blob))
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "INCONCLUSIVE", res
    assert any("0 repeats" in m for m in res["inconclusive"]), res
    assert sc.cli_exit_code(res, mode) == 2


def test_v3_null_summary_is_no_go(tmp_path):
    dirs = _full_v3_dirs(tmp_path)

    def _null_summary(blob):
        blob["runs"][0]["summary"] = None

    _mutate_e2e(dirs[1], "bench_e2e_constrained.json", _null_summary)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "NO-GO", res
    assert any("summary is not an object" in m for m in res["fail"]), res


def test_v3_non_dict_protocol_is_no_go(tmp_path):
    # A protocol block of [1] used to crash the gate; a corrupted
    # required block in paired mode - structural NO-GO.
    dirs = _full_v3_dirs(tmp_path)

    def _list_protocol(blob):
        blob["protocol"] = [1]

    _mutate_e2e(dirs[0], "bench_e2e_constrained.json", _list_protocol)
    res, mode = _acceptance(dirs)
    assert res["verdict"] == "NO-GO", res
    assert any("protocol corrupted" in m for m in res["fail"]), res
    assert sc.cli_exit_code(res, mode) == 1


def test_cli_no_traceback_on_nan_total_s(tmp_path):
    # Regression via the CLI: a NaN total_s used to give GO.
    dirs = _full_v3_dirs(tmp_path)

    def _nan_zig(blob):
        for r in blob["runs"]:
            if (r["schema"], r["batch"]) == (SCHEMAS[0], BATCHES[1]) \
                    and r["mode"] == "zig_constrained":
                r["summary"]["total_s"] = float("nan")

    _mutate_e2e(dirs[2], "bench_e2e_constrained.json", _nan_zig)
    p = _cli(dirs)
    assert p.returncode == 1, (p.returncode, p.stdout, p.stderr)
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout
    assert "finite positive" in p.stdout


def test_cli_no_traceback_on_null_runs(tmp_path):
    # runs:null does not crash the CLI summary (section note) or the gate
    # (NO-GO).
    dirs = _full_v3_dirs(tmp_path)
    _mutate_e2e(dirs[0], "bench_e2e_constrained.json",
                lambda b: b.__setitem__("runs", None))
    p = _cli(dirs)
    assert p.returncode == 1, (p.returncode, p.stdout, p.stderr)
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout
    assert "not printed" in p.stdout
    assert "not a list" in p.stdout


def test_cli_no_traceback_on_null_summary(tmp_path):
    # summary:null does not crash the CLI (previously a traceback); gate -
    # NO-GO.
    dirs = _full_v3_dirs(tmp_path)
    _mutate_e2e(dirs[1], "bench_e2e_constrained.json",
                lambda b: b["runs"][0].__setitem__("summary", None))
    p = _cli(dirs)
    assert p.returncode == 1, (p.returncode, p.stdout, p.stderr)
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout
    assert "summary is not an object" in p.stdout


def test_cli_no_traceback_on_list_protocol(tmp_path):
    # protocol:[1] does not crash the CLI (previously a traceback); gate -
    # NO-GO.
    dirs = _full_v3_dirs(tmp_path)
    _mutate_e2e(dirs[2], "bench_e2e_constrained.json",
                lambda b: b.__setitem__("protocol", [1]))
    p = _cli(dirs)
    assert p.returncode == 1, (p.returncode, p.stdout, p.stderr)
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout
    assert "protocol corrupted" in p.stdout


def test_cli_tables_no_traceback_on_damaged_fields(tmp_path):
    # --tables on corrupted nested fields: notes, no traceback.
    dirs = _full_v3_dirs(tmp_path)

    def _list_engines(blob):
        blob["engines"] = [1]

    _mutate_e2e(dirs[0], "bench_compare_uniform.json", _list_engines)
    p = subprocess.run(
        [sys.executable, str(BENCH / "summarize_control.py"), "--tables",
         *[str(x) for x in dirs]],
        capture_output=True, text=True)
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout
    assert "not printed" in p.stdout
