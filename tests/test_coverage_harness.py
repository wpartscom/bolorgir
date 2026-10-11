"""Rules of the semantic coverage harness (benchmarks/bench_jsb_coverage.py)
that its reports depend on: classification of a failing blg_finish, the
known-deviation check of generated documents, chunk and merge identity,
the pass documents kept for triage and --complete-from.
"""

import json
import os
import random
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "benchmarks"))

pytest.importorskip("jsonschema")
pytest.importorskip("regress")
pytest.importorskip("numpy")

gp = pytest.importorskip("generation_passes", reason="core library not built")
bench = pytest.importorskip("bench_jsb_coverage", reason="core library not built")
blg = gp.blg
from reference import TokenizerSpec  # noqa: E402

D4 = "http://json-schema.org/draft-04/schema#"


@pytest.mark.parametrize("status,fired,want", [
    ("RESOURCE_LIMIT", False, "engine_unavailable"),
    ("CANCELLED", False, "engine_unavailable"),
    ("CANCELLED", True, "incomplete:time_limit"),
    ("INTERNAL", False, "engine_defect"),
])
def test_policy_pass_finish_failure(status, fired, want):
    rec = bench.policy_pass_record(f"error:{status}", b"{}", {"stage": "finish", "step": 3},
                                   None, 7, lambda: fired)
    assert rec["outcome"] == want
    if want != "incomplete:time_limit":
        assert (rec["stage"], rec["code"]) == ("finish", f"BLG_ERR_{status}")


class _FinishFails:
    status = blg.BLG_ERR_INTERNAL

    def __init__(self, ctx, grammar):
        self.handle = None

    def finish(self):
        return self.status

    def destroy(self):
        pass


@pytest.mark.parametrize("status,cancelled,want", [
    (blg.BLG_ERR_RESOURCE_LIMIT, False, "engine_unavailable"),
    (blg.BLG_ERR_CANCELLED, True, "incomplete:time_limit"),
    (blg.BLG_ERR_INTERNAL, False, "engine_defect"),
])
def test_run_pass_finish_failure(monkeypatch, status, cancelled, want):
    spec = TokenizerSpec(tokens=(b"a", b""), eos_ids=(1,)).validate()

    def eos_only(session, buf):
        buf[:] = 0
        buf[0] = 1 << 1
        return blg.BLG_OK

    session = type("S", (_FinishFails,), {"status": status})
    monkeypatch.setattr(gp.blg, "Session", session)
    monkeypatch.setattr(gp, "fill_mask", eos_only)
    rec, _doc = gp.run_pass(None, None, gp.Vocab(spec), random.Random(0), seed=1,
                            validator=None, policy="uniform", eos_after=0,
                            cancelled=lambda: cancelled)
    assert rec["outcome"] == want
    assert rec["stage"] == "finish"


def test_known_deviation_is_the_documented_one():
    text = json.dumps({"$schema": D4, "type": "object",
                       "properties": {"n": {"type": "integer"}}})
    kind = "draft04_integer_spelling"
    assert gp.is_known_deviation(kind, text, b'{"n":1.0}')
    assert gp.is_known_deviation(kind, text, '{"n":2e1}')
    assert not gp.is_known_deviation(kind, text, b'{"n":1.5}')
    assert not gp.is_known_deviation(kind, text, b'{"n":"1"}')
    assert not gp.is_known_deviation(kind, text, b'{"n":')
    assert not gp.is_known_deviation(kind, text, b'\xff')
    assert not gp.is_known_deviation(kind, json.dumps({"type": "integer"}), b"1.0")
    assert not gp.is_known_deviation("corpus_label", text, b'{"n":1.0}')


def test_chunk_identity(tmp_path):
    cdir = str(tmp_path / "c000000")
    bench.check_chunk_identity(cdir, {"library_sha256": "a"})
    bench.check_chunk_identity(cdir, {"library_sha256": "a"})
    with pytest.raises(SystemExit, match="other inputs .library_sha256."):
        bench.check_chunk_identity(cdir, {"library_sha256": "b"})
    with open(os.path.join(cdir, "coverage_repo.json"), "w") as f:
        f.write("{}")
    with pytest.raises(SystemExit, match="other inputs"):
        bench.run_chunk(None, "repo", 0, 1, str(tmp_path), {"library_sha256": "b"})
    os.remove(os.path.join(cdir, "identity.json"))
    with pytest.raises(SystemExit, match="no identity record"):
        bench.check_chunk_identity(cdir, {"library_sha256": "a"})


def _part(lib, passes=20, chunk=0):
    return {"meta": {
        "engine": {"commit": "c", "library_sha256": lib, "profile": "spec-v1"},
        "corpus": {"sha256": "s"},
        "semantic": {"max_files": 100, "skip_files": chunk, "engine_fault_list": f"c{chunk}"},
        "generatable": {"passes": passes, "crashes": [chunk], "passes_sidecar": f"c{chunk}"}}}


def test_merge_identity():
    lib = bench.library_sha256()
    bench.check_merge_parts(["a", "b"], [_part(lib), _part(lib, chunk=100)])
    with pytest.raises(SystemExit, match="differs from a in generatable"):
        bench.check_merge_parts(["a", "b"], [_part(lib), _part(lib, passes=10)])
    with pytest.raises(SystemExit, match="another engine library"):
        bench.check_merge_parts(["a"], [_part("0" * 64)])


def test_report_keeps_a_short_document_and_the_sidecar_all():
    long_doc = "[" + "1," * 6000 + "1]"
    recs = [{"outcome": "invalid_document", "stage": "validate", "code": "REJECTED",
             "step": 9, "prefix_length": len(long_doc), "seed": 5, "document": long_doc},
            {"outcome": "completed_valid", "stage": "validate", "code": None,
             "step": 2, "prefix_length": 2, "seed": 6}]
    report, rows = bench.gen_split_records({"byte": {"outcome": "invalid_document",
                                                     "records": recs}})
    shown = report["byte"]["failed_passes"][0]
    assert shown["document_truncated"] is True
    assert len(shown["document"].encode()) <= bench.GEN_DOCUMENT_CAP
    assert rows["byte"][0][6] == long_doc
    assert rows["byte"][1][6] is None


def _strip(records):
    return [{k: r.get(k) for k in bench.PASS_FIELDS} for r in records]


def test_complete_from_matches_a_full_run(monkeypatch):
    # schema_doc None, as the repository snapshot loader hands it over: the
    # completion must plan its passes from the same parsed schema (hints).
    monkeypatch.setattr(bench, "PROFILE", "spec-v1")
    schema = {"$schema": D4, "type": "object",
              "properties": {"n": {"type": "integer"},
                             "s": {"type": "string", "enum": ["ab", "cd"]}},
              "required": ["n"]}
    data = json.dumps(schema).encode()
    opts = {"passes": 8, "max_steps": 512, "max_seconds": 10.0, "schema_budget": 0.0}
    gt = bench.GenTokenizer("byte")
    try:
        full = bench.generatable_all([gt], data, None, "d", "h.json", opts)["byte"]
        capped = bench.generatable_all([gt], data, None, "d", "h.json",
                                       dict(opts, schema_budget=1e-9))["byte"]
        report, rows = bench.gen_split_records({"byte": capped})
        done = bench.generatable_complete(
            [gt], data, None, "d", "h.json", opts, report,
            {"byte": [row[:6] for row in rows["byte"]]})["byte"]
        invalid = [i for i, r in enumerate(full["records"]) if r["outcome"] == "invalid_document"]
        assert invalid, "the schema should yield a draft-04 invalid_document pass"
        full_report, full_rows = bench.gen_split_records({"byte": full})
        tampered = [list(r[:6]) for r in full_rows["byte"]]
        tampered[invalid[0]][3] += 1
        broken = bench.generatable_complete([gt], data, None, "d", "h.json", opts,
                                            full_report, {"byte": tampered})["byte"]
    finally:
        gt.destroy()
    assert capped["counts"]["skipped:schema_budget"] == opts["passes"] - 1
    assert _strip(done["records"]) == _strip(full["records"])
    assert done["completion"]["passes_run"] == opts["passes"] - 1
    assert done["completion"]["replay_mismatches"] == []
    assert done["outcome"] == full["outcome"]
    assert broken["completion"]["replayed"] == len(invalid)
    assert broken["records"][invalid[0]]["document"] is None
    assert [m["pass"] for m in broken["completion"]["replay_mismatches"]] == [invalid[0] + 1]
    assert all(r.get("document") for i, r in enumerate(broken["records"])
               if r["outcome"] == "invalid_document" and i != invalid[0])


def test_complete_again_replays_what_ran_out_of_time(monkeypatch):
    monkeypatch.setattr(bench, "PROFILE", "spec-v1")
    schema = {"$schema": D4, "type": "object",
              "properties": {"n": {"type": "integer"},
                             "s": {"type": "string", "enum": ["ab", "cd"]}},
              "required": ["n"]}
    data = json.dumps(schema).encode()
    opts = {"passes": 8, "max_steps": 512, "max_seconds": 10.0, "schema_budget": 0.0}
    gt = bench.GenTokenizer("byte")
    try:
        full = bench.generatable_all([gt], data, None, "d", "h.json", opts)["byte"]
        report, rows = bench.gen_split_records({"byte": full})
        bare = {"byte": [row[:6] for row in rows["byte"]]}
        first = bench.generatable_complete([gt], data, None, "d", "h.json",
                                           dict(opts, replay_max_seconds=1e-9),
                                           report, bare)["byte"]
        report1, rows1 = bench.gen_split_records({"byte": first})
        second = bench.generatable_complete([gt], data, None, "d", "h.json", opts,
                                            report1, rows1)["byte"]
    finally:
        gt.destroy()
    missed = [m["pass"] for m in first["completion"]["replay_mismatches"]]
    assert missed, "a replay under a 1 ns limit should run out of time"
    assert all(m["replay"]["outcome"] == "incomplete:time_limit"
               for m in first["completion"]["replay_mismatches"])
    assert _strip(second["records"]) == _strip(full["records"])
    assert second["completion"]["replay_mismatches"] == []
    assert second["completion"]["rereplayed"] == len(missed)
    assert second["completion"]["replayed"] == first["completion"]["replayed"]
    assert second["completion"]["passes_run"] == 0


def test_complete_retries_a_replay_on_a_fresh_context(monkeypatch):
    monkeypatch.setattr(bench, "PROFILE", "spec-v1")
    schema = {"$schema": D4, "type": "object",
              "properties": {"n": {"type": "integer"},
                             "s": {"type": "string", "enum": ["ab", "cd"]}},
              "required": ["n"]}
    data = json.dumps(schema).encode()
    opts = {"passes": 8, "max_steps": 512, "max_seconds": 10.0, "schema_budget": 0.0}
    gt = bench.GenTokenizer("byte")
    real, calls = bench.gen_pass, []

    def first_unavailable(gt_, ctx, grammar, k, seed, plan, opts_):
        calls.append(k)
        if len(calls) == 1:
            return {"outcome": "engine_unavailable", "stage": "fill_mask",
                    "code": "BLG_ERR_RESOURCE_LIMIT", "step": 1, "prefix_length": 1,
                    "seed": seed}, b""
        return real(gt_, ctx, grammar, k, seed, plan, opts_)

    try:
        full = bench.generatable_all([gt], data, None, "d", "h.json", opts)["byte"]
        report, rows = bench.gen_split_records({"byte": full})
        monkeypatch.setattr(bench, "gen_pass", first_unavailable)
        done = bench.generatable_complete([gt], data, None, "d", "h.json", opts, report,
                                          {"byte": [row[:6] for row in rows["byte"]]})["byte"]
    finally:
        gt.destroy()
    assert done["completion"]["retried_on_fresh_context"] is True
    assert done["completion"]["replay_mismatches"] == []
    assert _strip(done["records"]) == _strip(full["records"])


def test_complete_replays_alone_what_stays_unavailable(monkeypatch):
    monkeypatch.setattr(bench, "PROFILE", "spec-v1")
    schema = {"$schema": D4, "type": "object",
              "properties": {"n": {"type": "integer"},
                             "s": {"type": "string", "enum": ["ab", "cd"]}},
              "required": ["n"]}
    data = json.dumps(schema).encode()
    opts = {"passes": 8, "max_steps": 512, "max_seconds": 10.0, "schema_budget": 0.0}
    gt = bench.GenTokenizer("byte")

    def unavailable(gt_, ctx, grammar, k, seed, plan, opts_):
        return {"outcome": "engine_unavailable", "stage": "fill_mask",
                "code": "BLG_ERR_RESOURCE_LIMIT", "step": 1, "prefix_length": 1,
                "seed": seed}, b""

    try:
        full = bench.generatable_all([gt], data, None, "d", "h.json", opts)["byte"]
        report, rows = bench.gen_split_records({"byte": full})
        monkeypatch.setattr(bench, "gen_pass", unavailable)
        done = bench.generatable_complete([gt], data, None, "d", "h.json", opts, report,
                                          {"byte": [row[:6] for row in rows["byte"]]})["byte"]
    finally:
        gt.destroy()
    invalid = sum(r["outcome"] == "invalid_document" for r in full["records"])
    assert invalid
    assert done["completion"]["replayed_alone"] == invalid
    assert done["completion"]["replay_mismatches"] == []
    assert _strip(done["records"]) == _strip(full["records"])
