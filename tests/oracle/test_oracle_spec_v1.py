"""pytest wiring for the oracle harness under the spec-v1 profile
(ROADMAP rev-2 P1 item 7).

Same scaffold contracts as test_oracle.py, but compiled with
--profile spec-v1: no unexplained MISMATCH rows (spec-v1 deviations live
in known_gaps.json with a "profile": "spec-v1" scope), no stale gaps.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_oracle  # noqa: E402

try:
    import blg_ctypes  # noqa: F401
    _LIB_ERROR = None
except ModuleNotFoundError as e:
    _LIB_ERROR = str(e)

pytestmark = pytest.mark.skipif(
    not run_oracle.suite_available() or _LIB_ERROR is not None,
    reason="oracle prerequisites missing (suite snapshot: "
           "tests/oracle/fetch_suite.sh; core: zig build)")


@pytest.fixture(scope="module")
def report():
    return run_oracle.run(profile="spec-v1")


def test_spec_v1_harness_covers_the_suite(report):
    s = report["summary"]
    assert s["rows"] > 1000, "suite snapshot looks incomplete"
    assert s["by_outcome"].get("PASS", 0) > 0
    for row in report["rows"]:
        if row["outcome"] == "SKIPPED":
            assert row["refusal"]["pointer"] is not None, row
        else:
            assert row["engine"] in ("accept", "reject") or \
                row["engine"].startswith("error:"), row


def test_spec_v1_no_unexplained_mismatches(report):
    mismatches = [r for r in report["rows"] if r["outcome"] == "MISMATCH"]
    assert not mismatches, (
        "spec-v1 oracle mismatches (add a documented profile-scoped entry "
        "to known_gaps.json or fix the engine):\n" + "\n".join(
            f"  {r['suite_file']} :: {r['case']} :: test {r['test_index']}"
            f" engine={r['engine']} suite={r['suite']}"
            f" validator={r['validator']} class={r['mismatch_class']}"
            for r in mismatches[:20]))


# The pytest fixture runs the default draft only (run_oracle.DEFAULT_DRAFTS
# = draft2020-12); the other dialects are covered live by
# test_oracle_dialects.py against the same pin.
# Pinned ADR-0005 D3 residual (2026-09-30, fix3g): exact ENGINE_ERROR rows
# whose reachability certificate families are not yet implemented in closed
# form. The pin lives in engine_error_residual_spec_v1.json and identifies
# every row by (draft, suite_file, case, test_index) together with the
# engine code, which must be exactly "error:RESOURCE_LIMIT" - a fixed test,
# a moved test_index or a different error inside the same case all fail the
# gate. The shapes behind every group are documented with counterexample
# schemas in benchmarks/reports/20260929T_fix3_adr0007/REPORT.md (step 3)
# and docs/supported_features.md.
_PIN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "engine_error_residual_spec_v1.json")


def load_engine_error_pin():
    with open(_PIN_PATH, encoding="utf-8") as f:
        return json.load(f)["rows"]


def pinned_residual_rows(report, drafts):
    pinned = [r for r in load_engine_error_pin() if r["draft"] in drafts]
    observed = [{k: r[k] for k in ("draft", "suite_file", "case",
                                   "test_index", "engine")}
                for r in report["rows"] if r["outcome"] == "ENGINE_ERROR"]
    key = lambda r: (r["draft"], r["suite_file"], r["case"],  # noqa: E731
                     r["test_index"], r["engine"])
    return sorted(pinned, key=key), sorted(observed, key=key)


def test_spec_v1_no_engine_errors(report):
    pinned, observed = pinned_residual_rows(report, ("draft2020-12",))
    for r in pinned:
        assert r["engine"] == "error:RESOURCE_LIMIT", (
            f"pin row carries a non-RESOURCE_LIMIT engine code: {r}")
    assert observed == pinned, (
        "spec-v1 engine-error residual moved (ratchet: shrink the pin when "
        "a certificate family lands; growth or a changed error code is a "
        "regression):\n"
        + "\n".join(
            f"  {('p' if side == 0 else 'o')}: {r}"
            for pair in zip(pinned, observed) if pair[0] != pair[1]
            for side, r in enumerate(pair))
        + "\n".join(
            f"  only in {'pinned' if len(pinned) > len(observed) else 'observed'}: {r}"
            for r in (pinned[len(observed):] if len(pinned) > len(observed)
                      else observed[len(pinned):])))


def test_spec_v1_no_stale_known_gaps(report):
    unused = report["summary"]["known_gaps_unused"]
    assert not unused, (
        "profile-scoped known_gaps.json entries no longer match any "
        "spec-v1 row (the gap is fixed or the suite moved - remove or "
        f"update the entry): {unused}")
