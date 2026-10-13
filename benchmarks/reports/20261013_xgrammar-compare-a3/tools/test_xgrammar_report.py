#!/usr/bin/env python3
"""Address tests of the report rules (TZ 6.3): category partition,
excluded runs, denominators, p90 method, re-validation of stored text,
and the revision-4 integrity checks (negative tests on a full correct
matrix with a single corruption: missing whole session, missing
mandatory fields, null text, stripped finish, usage flag against the
stored token count, broken tpot/text_len; plus the explicitly
supported v1 format). Synthetic records only; run:
python3 test_xgrammar_report.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xgrammar_report as R  # noqa: E402


def rec(workload="w1_json_small", batch=1, rep=0, session="s1", backend="b",
        status=200, finish="stop", valid=True, ttft=10.0, e2e=100.0, tokens=10,
        text="", transport_error=False, usage=True, final_event=True,
        finish_present=None, wall=1.0):
    tpot = ((e2e - ttft) / (tokens - 1)) if (valid and tokens and tokens > 1
                                             and ttft is not None) else None
    return {"session": session, "backend": backend, "workload": workload, "batch": batch,
            "repeat": rep, "idx": 0, "wall_s": wall, "status": status, "finish": finish,
            "ttft_ms": ttft, "e2e_ms": e2e, "tokens": tokens, "tpot_ms": tpot,
            "valid": valid, "invalid_reason": "", "text_len": len(text), "text": text,
            "final_event": final_event, "usage_present": usage,
            "finish_present": (finish is not None) if finish_present is None else finish_present,
            "transport_error": transport_error, "error_excerpt": ""}


def v1rec(**kw):
    """A record in the v1 (2026-10-12 collector) format: the v2-only
    fields are not part of it."""
    r = rec(**kw)
    for k in ("text", "final_event", "usage_present", "finish_present", "transport_error"):
        r.pop(k)
    return r


def exp(**sessions):
    """Expected-session declaration for build_report (label=backend)."""
    return {"source": "test", "sessions": dict(sessions)}


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
        self.assertEqual(R.category(v1rec(finish=None)), "unfinished")


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
        recs = [rec(batch=8, finish="length", valid=False, text="x") for _ in range(8)]
        rows = R.run_rows(recs)
        self.assertTrue(rows[0]["included"])
        self.assertEqual(rows[0]["stop_valid_per_s"], 0.0)

    def test_incomplete_run_is_excluded(self):
        rows = R.run_rows([rec(batch=128) for _ in range(1)])
        self.assertFalse(rows[0]["included"])
        self.assertEqual(rows[0]["exclusion"], "incomplete run (1 of 128)")
        self.assertIsNone(rows[0]["tok_per_s"])


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
                rec(finish="length", valid=False, text=a1),
                v1rec()]
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


def write_raw(tmpdir, records, name="raw.jsonl"):
    p = os.path.join(tmpdir, name)
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return p


VALID_TEXT = {
    "w1_json_small": '{"action":"reply","priority":3}',
    "w2_json_nested": ('{"items":[{"name":"abc","count":1},{"name":"abc","count":1},'
                       '{"name":"abc","count":1}],"total":3}'),
    "w3_regex64": "0" * 64,
}


def full_matrix(session="s1", repeats=2, backend="b"):
    out = []
    for w in R.MATRIX_WORKLOADS:
        for b in R.MATRIX_BATCHES:
            for rep in range(repeats):
                for i in range(b):
                    r = rec(workload=w, batch=b, rep=rep, session=session,
                            backend=backend, text=VALID_TEXT[w])
                    r["idx"] = i
                    out.append(r)
    return out


class TestIntegrity(unittest.TestCase):
    def test_full_matrix_has_no_problems(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_raw(d, full_matrix())
            doc = R.build_report([p], expected=exp(s1="b"))
        self.assertEqual(doc["problems"], [])
        self.assertEqual(doc["records"], 1014)
        self.assertEqual(doc["matrix"]["s1"]["runs"], 24)
        self.assertEqual(doc["revalidation"]["valid_agree"], 1014)
        self.assertEqual(doc["formats"], {"raw.jsonl": "v2"})

    def test_missing_whole_session_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = write_raw(d, full_matrix(session="s1"), name="raw-s1.jsonl")
            write_raw(d, full_matrix(session="s2", backend="b2"), name="raw-s2.jsonl")
            doc = R.build_report([p1], expected=exp(s1="b", s2="b2"))
        self.assertEqual(doc["problems"],
                         ["expected session missing from the inputs: s2"])
        self.assertEqual(doc["expected_sessions"]["missing"], ["s2"])

    def test_only_one_backend_fails(self):
        # review repro: leave only the XGrammar sessions in the inputs
        with tempfile.TemporaryDirectory() as d:
            p1 = write_raw(d, full_matrix(session="x1", backend="xgrammar"),
                           name="raw-x1.jsonl")
            p2 = write_raw(d, full_matrix(session="x2", backend="xgrammar"),
                           name="raw-x2.jsonl")
            doc = R.build_report([p1, p2], expected=exp(
                x1="xgrammar", b1="bolorgir", x2="xgrammar", b2="bolorgir"))
        self.assertEqual(doc["expected_sessions"]["missing"], ["b1", "b2"])
        self.assertEqual(len(doc["problems"]), 2)

    def test_undeclared_session_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p1 = write_raw(d, full_matrix(session="s1"), name="raw-s1.jsonl")
            p2 = write_raw(d, full_matrix(session="s2", backend="b2"),
                           name="raw-s2.jsonl")
            doc = R.build_report([p1, p2], expected=exp(s1="b"))
        self.assertEqual(doc["problems"], ["unexpected session in the inputs: s2"])

    def test_backend_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_raw(d, full_matrix())
            doc = R.build_report([p], expected=exp(s1="xgrammar"))
        self.assertTrue(any("does not match the declared" in x
                            for x in doc["problems"]))

    def test_missing_declaration_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_raw(d, full_matrix())
            doc = R.build_report([p])
        self.assertTrue(any("expected session set not declared" in x
                            for x in doc["problems"]))

    def test_no_final_event_is_unfinished_and_a_problem(self):
        self.assertEqual(R.category(rec(final_event=False)), "unfinished")
        with tempfile.TemporaryDirectory() as d:
            p = write_raw(d, [rec(final_event=False)])
            doc = R.build_report([p], expected=exp(s1="b"))
        self.assertTrue(any("final stream event" in x for x in doc["problems"]))

    def test_incomplete_run_is_a_problem(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_raw(d, [rec(batch=128, text=VALID_TEXT["w1_json_small"])])
            doc = R.build_report([p], expected=exp(s1="b"))
        self.assertTrue(any("wrong request count" in x for x in doc["problems"]))

    def test_duplicate_key_is_a_problem(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_raw(d, [rec(session="A"), rec(session="A")])
            doc = R.build_report([p], expected=exp(A="b"))
        self.assertTrue(any("duplicate request keys" in x for x in doc["problems"]))

    def test_usage_missing_is_never_zero_tokens(self):
        r = rec(tokens=None, usage=False)
        rows = R.run_rows([r])
        self.assertIsNone(rows[0]["tokens"])
        self.assertIsNone(rows[0]["tok_per_s"])
        self.assertEqual(rows[0]["tok_exclusion"], "usage missing")
        with tempfile.TemporaryDirectory() as d:
            p = write_raw(d, [r])
            doc = R.build_report([p], expected=exp(s1="b"))
        self.assertTrue(any("usage missing" in x for x in doc["problems"]))

    def test_revalidation_disagreement_is_a_problem(self):
        r = rec(valid=True, text='{"action":"reply","priority":9}')
        with tempfile.TemporaryDirectory() as d:
            p = write_raw(d, [r])
            doc = R.build_report([p], expected=exp(s1="b"))
        self.assertTrue(any("stored-vs-revalidated" in x for x in doc["problems"]))

    def test_empty_input_fails(self):
        doc = R.build_report([])
        self.assertTrue(any("no records" in x for x in doc["problems"]))
        with tempfile.TemporaryDirectory() as d:
            p = write_raw(d, [])
            doc = R.build_report([p])
        self.assertTrue(any("has no records" in x for x in doc["problems"]))


class TestSingleCorruption(unittest.TestCase):
    """One corruption in one record of an otherwise full and correct
    matrix must fail the report (review point 2: no false success)."""

    def _build(self, mutate):
        recs = full_matrix()
        mutate(recs[100])
        with tempfile.TemporaryDirectory() as d:
            p = write_raw(d, recs)
            return R.build_report([p], expected=exp(s1="b"))

    def test_missing_final_event(self):
        doc = self._build(lambda r: r.pop("final_event"))
        self.assertTrue(any("missing final_event" in x for x in doc["problems"]))
        r = full_matrix()[100]
        r.pop("final_event")
        self.assertEqual(R.category(r), "unfinished")

    def test_null_text(self):
        def mut(r):
            r["text"] = None
            r["text_len"] = 0
        doc = self._build(mut)
        self.assertTrue(any("text: not str" in x for x in doc["problems"]))
        self.assertEqual(doc["revalidation"]["without_text"], 1)

    def test_missing_finish_only(self):
        doc = self._build(lambda r: r.pop("finish"))
        self.assertTrue(any("missing finish" in x for x in doc["problems"]))
        r = full_matrix()[100]
        r.pop("finish")
        self.assertEqual(R.category(r), "other_finish")

    def test_missing_finish_and_flag_is_unfinished(self):
        def mut(r):
            r.pop("finish")
            r.pop("finish_present")
        doc = self._build(mut)
        self.assertTrue(any("missing finish" in x for x in doc["problems"]))
        r = full_matrix()[100]
        r.pop("finish")
        r.pop("finish_present")
        self.assertEqual(R.category(r), "unfinished")

    def test_usage_flag_false_with_tokens(self):
        doc = self._build(lambda r: r.__setitem__("usage_present", False))
        self.assertTrue(any("usage_present" in x for x in doc["problems"]))
        recs = full_matrix()
        recs[100]["usage_present"] = False
        key = (recs[100]["workload"], recs[100]["batch"], recs[100]["repeat"])
        rows = R.run_rows([r for r in recs
                           if (r["workload"], r["batch"], r["repeat"]) == key])
        self.assertFalse(rows[0]["tok_included"])
        self.assertEqual(rows[0]["tok_exclusion"], "usage missing")

    def test_tpot_value_breaks(self):
        doc = self._build(lambda r: r.__setitem__("tpot_ms", r["tpot_ms"] + 1.0))
        self.assertTrue(any("tpot_ms" in x for x in doc["problems"]))

    def test_text_len_mismatch(self):
        doc = self._build(lambda r: r.__setitem__("text_len", r["text_len"] + 3))
        self.assertTrue(any("text_len" in x for x in doc["problems"]))

    def test_tpot_counts_stop_valid_only(self):
        recs = [rec(), rec(), rec()]
        bad = rec()
        bad.pop("finish")
        bad.pop("finish_present")
        self.assertIsNotNone(bad["tpot_ms"])
        recs.append(bad)
        s = R.cell_summary(recs)
        self.assertEqual(s["tpot_n"], 3)
        self.assertEqual(s["tpot_excluded"], 1)


class TestFormats(unittest.TestCase):
    def test_detect_format(self):
        self.assertEqual(R.detect_format([]), "empty")
        self.assertEqual(R.detect_format([v1rec()]), "v1")
        self.assertEqual(R.detect_format([rec()]), "v2")
        r = v1rec()
        r["text"] = None
        self.assertEqual(R.detect_format([r]), "v2")

    def test_v1_file_supported_explicitly(self):
        recs = []
        for w in R.MATRIX_WORKLOADS:
            for b in R.MATRIX_BATCHES:
                for rep in range(2):
                    for i in range(b):
                        r = v1rec(workload=w, batch=b, rep=rep)
                        r["idx"] = i
                        recs.append(r)
        with tempfile.TemporaryDirectory() as d:
            p = write_raw(d, recs, name="raw-old.jsonl")
            doc = R.build_report([p], expected=exp(s1="b"))
        self.assertEqual(doc["problems"], [])
        self.assertEqual(doc["formats"], {"raw-old.jsonl": "v1"})
        self.assertEqual(doc["revalidation"]["without_text"], 1014)

    def test_v1_file_forced_v2_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_raw(d, [v1rec()], name="raw-old.jsonl")
            doc = R.build_report([p], expected=exp(s1="b"), fmt="v2")
        self.assertTrue(any("missing text" in x for x in doc["problems"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
