"""pytest wiring for the dialect-matrix oracle runs (spec-v1 profile;
see docs/dialect-matrix.md).

One run over all five suite drafts (draft4, draft6, draft7, draft2019-09,
draft2020-12) with the harness injecting the dialect's root $schema
(dialect-matrix section 1). Asserts the harness-level contracts:

- every draft directory is actually covered;
- every row reaches a verdict or a classified compile refusal
  (needs_review refusals are never silent);
- no SERIALIZATION_INCOMPATIBLE rows on the non-optional suite;
- the exact-decimal oracle never disagrees with the suite expectation;
- MISMATCH rows are limited to the explicitly documented open engine
  deviations below (each tracked in docs/dialect-matrix.md section 9);
  a new mismatch or a fixed-but-whitelisted deviation fails the gate.

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

ALL_DRAFTS = ("draft4", "draft6", "draft7", "draft2019-09", "draft2020-12")

# Open engine deviations observed in the dialect runs (see
# docs/dialect-matrix.md section 9). Each entry is (draft, suite file,
# case description). The draft-04..07 "$ref prevents a sibling id/$id from
# changing the base uri" cases: the engine resolves the $ref against the
# base URI declared by the sibling id/$id instead of ignoring the sibling
# per the dialect rule (dialect-matrix section 3). An entry that stops
# matching (the deviation is fixed) fails test_open_deviations_still_open
# and must be removed here and in the matrix.
OPEN_DEVIATIONS = {
    ("draft4", "ref.json",
     "$ref prevents a sibling id from changing the base uri"),
    ("draft6", "ref.json",
     "$ref prevents a sibling $id from changing the base uri"),
    ("draft7", "ref.json",
     "$ref prevents a sibling $id from changing the base uri"),
    # Fixed engine regression (kept no entries): the additionalItems
    # "items is schema" rows failed on a parser defect - converged duplicate
    # threads under an unconstrained items schema doubled per integer-valued
    # element until the thread budget rejected the 5th one. The parser now
    # merges duplicate threads (parser.dedupThreads), so these rows pass.
}


@pytest.fixture(scope="module")
def report():
    return run_oracle.run(drafts=ALL_DRAFTS, profile="spec-v1")


def test_all_dialects_covered(report):
    s = report["summary"]
    assert s["rows"] > 4000, "multi-draft suite snapshot looks incomplete"
    for d in ALL_DRAFTS:
        assert s["by_draft"].get(d, {}).get("rows", 0) > 0, d


def test_every_row_verdict_or_classified_refusal(report):
    for row in report["rows"]:
        if row["outcome"] == "SKIPPED":
            assert row["refusal"]["pointer"] is not None, row
            assert row["refusal"]["classification"] != "needs_review", (
                "unclassified refusal (extend DOCUMENTED_REFUSAL_KEYWORDS "
                "or document the refusal): "
                f"{row['draft']} {row['suite_file']} :: {row['case']} :: "
                f"{row['refusal']['status']} at {row['refusal']['pointer']}")
        elif row["outcome"] == "SERIALIZATION_INCOMPATIBLE":
            raise AssertionError(
                f"serialization-incompatible row: {row['draft']} "
                f"{row['suite_file']} :: {row['case']} :: {row['detail']}")
        else:
            assert row["engine"] in ("accept", "reject") or \
                row["engine"].startswith("error:"), row


def test_no_unexplained_mismatches(report):
    mismatches = [r for r in report["rows"] if r["outcome"] == "MISMATCH"]
    unexpected = [r for r in mismatches
                  if (r["draft"], r["suite_file"], r["case"])
                  not in OPEN_DEVIATIONS]
    assert not unexpected, (
        "dialect oracle mismatches outside the documented open deviations "
        "(fix the engine or document the deviation in "
        "docs/dialect-matrix.md section 9 and OPEN_DEVIATIONS):\n"
        + "\n".join(
            f"  {r['draft']} {r['suite_file']} :: {r['case']} :: test "
            f"{r['test_index']} engine={r['engine']} suite={r['suite']}"
            f" validator={r['validator']} class={r['mismatch_class']}"
            for r in unexpected[:20]))


def test_open_deviations_still_open(report):
    present = {(r["draft"], r["suite_file"], r["case"])
               for r in report["rows"] if r["outcome"] == "MISMATCH"}
    stale = {e for e in OPEN_DEVIATIONS if e not in present}
    assert not stale, (
        "whitelisted open deviations no longer mismatch (the engine is "
        "fixed - remove the entry from OPEN_DEVIATIONS and from "
        f"docs/dialect-matrix.md section 9): {stale}")


def test_decimal_oracle_agrees_with_suite(report):
    assert report["summary"]["decimal_oracle_disagreements"] == 0
    assert report["summary"]["decimal_sensitive_rows"] > 0, (
        "the suite exercises binary64-inexact decimal boundaries; zero "
        "marked rows means the exact-decimal layer stopped detecting them")


def test_no_stale_known_gaps(report):
    unused = report["summary"]["known_gaps_unused"]
    assert not unused, (
        "known_gaps.json entries no longer match any row of the "
        f"multi-draft spec-v1 run: {unused}")


def test_engine_error_residual_pinned(report):
    # Same exact-row pin as test_oracle_spec_v1.test_spec_v1_no_engine_errors,
    # applied to all five drafts of this run (the full 196-row residual).
    from test_oracle_spec_v1 import pinned_residual_rows
    pinned, observed = pinned_residual_rows(report, ALL_DRAFTS)
    for r in pinned:
        assert r["engine"] == "error:RESOURCE_LIMIT", (
            f"pin row carries a non-RESOURCE_LIMIT engine code: {r}")
    assert observed == pinned, (
        "multi-draft spec-v1 engine-error residual moved (ratchet: shrink "
        "the pin when a certificate family lands; growth, a moved "
        "test_index or a changed error code is a regression):\n"
        + "\n".join(
            f"  {('p' if side == 0 else 'o')}: {r}"
            for pair in zip(pinned, observed) if pair[0] != pair[1]
            for side, r in enumerate(pair))
        + "\n".join(
            f"  only in {'pinned' if len(pinned) > len(observed) else 'observed'}: {r}"
            for r in (pinned[len(observed):] if len(pinned) > len(observed)
                      else observed[len(pinned):])))
