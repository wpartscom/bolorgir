#!/usr/bin/env python3
"""Address tests of the v2 report rules (TZ 6.3): category partition,
excluded runs, denominators, p90 method, re-validation of stored text.
Synthetic records only; run: python3 test_xgrammar_report.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xgrammar_report as R  # noqa: E402


def rec(workload="w1_json_small", batch=1, rep=0, session="s1", backend="b",
        status=200, finish="stop", valid=True, ttft=10.0, e2e=100.0, tokens=10,
        text=None, transport_error=False, usage=True, final_event=True,
        finish_present=None, wall=1.0):
    r = {"session": session, "backend": backend, "workload": workload, "batch": batch,
         "repeat": rep, "idx": 0, "wall_s": wall, "status": status, "finish": finish,
         "ttft_ms": ttft, "e2e_ms": e2e, "tokens": tokens,
         "tpot_ms": ((e2e - ttft) / (tokens - 1)) if (valid and tokens and tokens > 1 and ttft) else None,
         "valid": valid, "invalid_reason": "", "text_len": len(text or ""),
         "final_event": final_event, "usage_present": usage, "finish_present": finish_present,
         "transport_error": transport_error, "error_excerpt": ""}
    if text is not None:
        r["text"] = text
    return r


class TestCategories(unittest.TestCase):
    def test_partition_covers_all(self):
        recs = [
            rec(),                                                        # stop_valid
            rec(valid=False, text='{"action":"reply","priority":9}'),     # stop_invalid
            rec(finish="length", valid=False, text='{"action":"reply","priority":0.5'),  # length
            rec(status=500, finish=None),                                 # failed
            rec(status=None, transport_error=True, finish=None),          # failed
            rec(finish=None, finish_present=False),                       # unfinished
            rec(finish="weird"),                                          # other
        ]
        cats = R.partition(recs)
        self.assertEqual(sum(cats.values()), len(recs))
        self.assertEqual(cats["stop_valid"], 1)
        self.assertEqual(cats["stop_invalid"], 1)
        self.assertEqual(cats["length"], 1)
        self.assertEqual(cats["failed_http_transport"], 2)
        self.assertEqual(cats["unfinished"], 1)
        self.assertEqual(cats["other_finish"], 1)

    def test_v1_record_without_finish_present_field(self):
        r = rec(finish=None)
        del r["finish_present"]
        self.assertEqual(R.category(r), "unfinished")


class TestRuns(unittest.TestCase):
    def test_run_excluded_on_failure_and_invalid_stop(self):
        # run A complete; run B has one http 500; run C has an invalid stop
        recs = [rec(session="A", rep=0, batch=8) for _ in range(8)]
        recs += [rec(session="B", rep=0, batch=8) for _ in range(7)] + [rec(session="B", rep=0, batch=8, status=500)]
        recs += [rec(session="C", rep=0, batch=8) for _ in range(7)] + [rec(session="C", rep=0, batch=8, valid=False)]
        rows = R.run_rows(recs)
        by = {r["session"]: r for r in rows}
        self.assertTrue(by["A"]["included"])
        self.assertFalse(by["B"]["included"])
        self.assertEqual(by["B"]["exclusion"], "failed request")
        self.assertFalse(by["C"]["included"])
        self.assertEqual(by["C"]["exclusion"], "invalid stop")
        self.assertIsNone(by["B"]["tok_per_s"])

    def test_length_does_not_exclude_run(self):
        recs = [rec(batch=8, finish="length", valid=False, text="x") for _ in range(4)]
        rows = R.run_rows(recs)
        self.assertTrue(rows[0]["included"])
        self.assertEqual(rows[0]["stop_valid_per_s"], 0.0)


class TestStats(unittest.TestCase):
    def test_p90_method_label(self):
        v, m = R.p90([1, 2, 3])
        self.assertEqual(v, 3)
        self.assertEqual(m, "max(n=3)")
        v, m = R.p90(list(range(1, 21)))
        self.assertIn("q90(n=20)", m)

    def test_cell_summary_denominators_and_exclusions(self):
        recs = [rec() for _ in range(9)] + [rec(status=500, finish=None, valid=False, tokens=0)]
        s = R.cell_summary(recs)
        self.assertEqual(s["requests"], 10)
        self.assertTrue(s["partition_ok"])
        self.assertEqual(s["ttft_n"], 9)          # failed request excluded from TTFT
        self.assertEqual(s["tpot_n"], 9)
        self.assertEqual(s["tpot_excluded"], 1)   # named, not silent
        self.assertEqual(s["runs_included"], 0)   # the only run has a failed request
        self.assertEqual(s["stop_valid_share"], 0.9)

    def test_usage_missing_flagged(self):
        recs = [rec(usage=False, tokens=None) for _ in range(2)]
        s = R.cell_summary(recs)
        self.assertEqual(s["usage_missing"], 2)


class TestRevalidation(unittest.TestCase):
    def test_length_cut_completable_and_not(self):
        # completable to a schema-valid document with the bounded suffix "}"
        good = '{"action":"reply","priority":3'
        # neither suffix makes a valid document (string value, out of range)
        bad = '{"action":"reply","priority":"x"'
        # the A1 family: completions are exponent/fraction spellings the
        # draft-04 integer semantics reject - not completable-valid here
        a1 = '{"action":"reply","priority":0.5'
        recs = [rec(finish="length", valid=False, text=good),
                rec(finish="length", valid=False, text=bad),
                rec(finish="length", valid=False, text=a1)]
        no_text = rec(text="x")
        del no_text["text"]
        recs.append(no_text)
        rv = R.revalidate(recs)
        self.assertEqual(rv["length_checked"], 3)
        self.assertEqual(rv["length_completable"], 1)
        self.assertEqual(rv["length_not_completable"], 2)
        self.assertEqual(rv["without_text"], 1)

    def test_valid_differ_detected(self):
        # stored valid=True but the text does not validate -> a difference, kept
        recs = [rec(valid=True, text='{"action":"reply","priority":9}')]
        rv = R.revalidate(recs)
        self.assertEqual(len(rv["valid_differ"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
