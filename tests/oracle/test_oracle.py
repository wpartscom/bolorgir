"""pytest wiring for the oracle harness (ROADMAP section 5, P0 item 4; A8.3).

Runs tests/oracle/run_oracle.py over the pinned draft2020-12 Test Suite and
asserts the scaffold contracts:
- the harness runs and sees every compiled case to a verdict;
- no unexplained MISMATCH rows (documented gaps live in known_gaps.json);
- no stale known-gaps entries (a fixed gap must be removed from the list);
- the run is deterministic (same pins -> identical per-outcome counts, A8.2).

Skipped when the suite snapshot or the built core library is missing.
"""

from __future__ import annotations

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
    return run_oracle.run()


def test_harness_covers_the_suite(report):
    s = report["summary"]
    assert s["rows"] > 1000, "suite snapshot looks incomplete"
    assert s["by_outcome"].get("PASS", 0) > 0
    # every compiled case reached a verdict; every refusal carries a pointer
    for row in report["rows"]:
        if row["outcome"] == "SKIPPED":
            assert row["refusal"]["pointer"] is not None, row
        else:
            assert row["engine"] in ("accept", "reject") or \
                row["engine"].startswith("error:"), row


def test_no_unexplained_mismatches(report):
    mismatches = [r for r in report["rows"] if r["outcome"] == "MISMATCH"]
    assert not mismatches, (
        "oracle mismatches (add a documented entry to known_gaps.json "
        "or fix the engine):\n" + "\n".join(
            f"  {r['suite_file']} :: {r['case']} :: test {r['test_index']}"
            f" engine={r['engine']} suite={r['suite']}"
            f" validator={r['validator']} class={r['mismatch_class']}"
            for r in mismatches[:20]))


def test_no_stale_known_gaps(report):
    unused = report["summary"]["known_gaps_unused"]
    assert not unused, (
        "known_gaps.json entries no longer match any row (the gap is "
        "fixed or the suite moved - remove or update the entry): "
        f"{unused}")


def test_deterministic_counts(report):
    again = run_oracle.run()
    keys = ("rows", "cases", "by_outcome", "by_refusal_status",
            "by_mismatch_class", "by_file")
    for k in keys:
        assert again["summary"][k] == report["summary"][k], (
            f"non-deterministic summary field {k} (A8.2)")
