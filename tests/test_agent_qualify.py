"""The A1 observation collector (benchmarks/agent_eval/qualify_stack.py) on
synthetic HTTP answers and backend traces; no server is contacted. An
answer that follows the probe is never a qualification without a trace,
HTTP errors fail the run, every tool call is recorded, and a trace counts
only for the same request id and the same schema hash: an
incomplete or contradictory trace, a mask without a compile, a mask of
another constraint or segment, a cache hit without its origin and an
undecided request are `unknown`; so are a segment missing or
empty in the declaration and the mask, and a mask or compile out of order
(mask before the compile of its constraint, compile or mask before the
decision) with correct seq numbering."""

import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "qualify_stack", os.path.join(ROOT, "benchmarks", "agent_eval", "qualify_stack.py"))
q = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(q)


def _answer(calls=(), content=None):
    return {"id": "chatcmpl-1", "choices": [{"finish_reason": "tool_calls" if calls else "stop",
                                             "message": {"content": content, "tool_calls": [
                                                 {"id": f"c{i}", "function": {"name": "set_code",
                                                                              "arguments": a}}
                                                 for i, a in enumerate(calls)]}}]}


def _run(tmp_path, post_fn, trace_events=None, extra=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    manifest = tmp_path / "stack.json"
    manifest.write_text(json.dumps({"vllm": "0.31.0", "model": "m", "revision": "r"}))
    argv = ["--base-url", "http://x/v1", "--model", "m", "--label", "t", "--repeats", "1",
            "--stack-manifest", str(manifest), "--out", str(tmp_path / "obs.json"), *extra]
    if trace_events is not None:
        tp = tmp_path / "trace.jsonl"
        tp.write_text("")
        post_fn.trace = (tp, trace_events)
        argv += ["--backend-trace", str(tp)]
    rc = q.main(argv, post_fn=post_fn)
    return rc, json.loads((tmp_path / "obs.json").read_text())


class Fake:
    """The server: answers every request the same way and, with a trace,
    appends the backend events of each request to the trace file."""

    def __init__(self, answer, status=200):
        self.answer, self.status, self.trace = answer, status, None

    def __call__(self, url, body, timeout, rid):
        if self.trace is not None:
            path, events_for = self.trace
            with open(path, "a", encoding="utf-8") as f:
                for e in events_for(rid, body):
                    f.write(json.dumps(e) + "\n")
        answer = self.answer
        if isinstance(answer, dict) and isinstance(answer.get("id"), str):
            # The serving layer builds the response id from X-Request-Id
            # (chatcmpl-<client>); the trace is keyed by it.
            answer = {**answer, "id": f"chatcmpl-{rid}"}
        return self.status, answer, {"X-Request-Id": rid}, 0.01


def test_probe_followed_without_trace_is_only_an_observation(tmp_path):
    rc, out = _run(tmp_path, Fake(_answer(['{"code": "zq-17-wombat"}'])))
    assert rc == 0 and out["evidence"] == "observation only"
    assert all(r["qualification"] is None and r["evidence"] == "observation" for r in out["rows"])
    assert {s["verdict"] for s in out["summary"].values()} == {"not verified"}
    assert out["rows"][0]["response_request_id"] == out["rows"][0]["request_id"]


def test_http_errors_fail_the_run(tmp_path):
    rc, out = _run(tmp_path, Fake({"error": "boom"}, status=500))
    assert rc == 1 and out["http_errors"] == len(out["rows"])


def test_unreachable_server_fails_the_run(tmp_path):
    rc, out = _run(tmp_path, Fake({"error": "URLError"}, status=None))
    assert rc == 1 and all(r["outcome"] == "error" for r in out["rows"])


def test_every_tool_call_is_recorded(tmp_path):
    _rc, out = _run(tmp_path, Fake(_answer(['{"code": "zq-17-wombat"}', '{"code": "1234"}', "x"])))
    calls = out["rows"][0]["tool_calls"]
    assert [c["probe_followed"] for c in calls] == [True, False, False]
    assert [c["arguments_json"] for c in calls] == [True, True, False]


ABSENT = object()  # the field is not written
SAME = object()    # the mask names the declared segment


def _trace(schema_ok=True, masks=True, compile_ok=True, other_id=False, applied=True,
           finish=True, cache="miss", origin="req-0", segment="s0", mask_segment=SAME,
           mask_constraint="c0", mask_backend="bolorgir", compile_event=True, finish_count=None,
           seq_gap=False, decision=True, fmt=q.TRACE_FORMAT, order=("decision", "compile", "mask")):
    """Events of one request in the format of benchmarks/agent_eval/PLAN.md;
    each keyword breaks one property. `order` is the write order of the
    decision, compile and mask events; seq always numbers the written
    events 0..n-1."""
    def events_for(rid, body):
        sent = q.sent_schemas(body)
        rid = "chatcmpl-" + rid + ("-x" if other_id else "")
        h = next(iter(sent.values()))
        by_kind = {}
        if decision:
            con = {"constraint_id": "c0", "kind": "tool", "schema_sha256": h}
            if segment is not ABSENT:
                con["segment_id"] = segment
            by_kind["decision"] = {"event": "decision", "applied": applied, "reason": "tool_choice",
                                   "constraints": [con] if applied else []}
        if applied and compile_event:
            ev = {"event": "compile", "backend": "bolorgir", "constraint_id": "c0",
                  "schema_sha256": h if schema_ok else "0" * 64, "cache": cache,
                  "status": "ok" if compile_ok else "INVALID_SCHEMA"}
            if cache == "hit" and origin:
                ev["compiled_from"] = origin
            by_kind["compile"] = ev
        if applied and masks:
            ev = {"event": "mask", "backend": mask_backend, "constraint_id": mask_constraint,
                  "step": 0, "allowed": 3, "mask_sha256": "m" * 64}
            seg = segment if mask_segment is SAME else mask_segment
            if seg is not ABSENT:
                ev["segment_id"] = seg
            by_kind["mask"] = ev
        evs = [{"event": "start", "format": fmt, "backend": "bolorgir", "backend_version": "0.2.0"}]
        evs += [by_kind[k] for k in order if k in by_kind]
        if finish:
            evs.append({"event": "finish", "status": "ok",
                        "events": len(evs) if finish_count is None else finish_count})
        out = []
        for i, e in enumerate(evs):
            out.append({"request_id": rid, "seq": i + (1 if seq_gap and i > 1 else 0), **e})
        return out
    return events_for


PROBE = ['{"code": "zq-17-wombat"}']


def test_complete_trace_with_the_sent_schema_and_masks_qualifies(tmp_path):
    rc, out = _run(tmp_path, Fake(_answer(PROBE)), _trace())
    assert rc == 0 and out["trace_format"] == q.TRACE_FORMAT
    assert {r["qualification"] for r in out["rows"]} == {"constrained"}


def test_cache_hit_with_its_origin_qualifies(tmp_path):
    rc, out = _run(tmp_path, Fake(_answer(PROBE)), _trace(cache="hit"))
    assert rc == 0 and {r["qualification"] for r in out["rows"]} == {"constrained"}
    assert "1 from the cache" in out["rows"][0]["qualification_reason"]


def test_explicit_decision_not_to_apply_is_unconstrained(tmp_path):
    rc, out = _run(tmp_path, Fake(_answer(PROBE)), _trace(applied=False))
    assert rc == 0 and {r["qualification"] for r in out["rows"]} == {"unconstrained"}


BROKEN = {
    "another schema": {"schema_ok": False},
    "no mask": {"masks": False},
    "compile failed": {"compile_ok": False},
    "incomplete (no finish)": {"finish": False},
    "finish counts other events": {"finish_count": 1},
    "seq gap": {"seq_gap": True},
    "no decision": {"decision": False},
    "mask without a compile": {"compile_event": False},
    "cache hit without origin": {"cache": "hit", "origin": None},
    "cache state missing": {"cache": None},
    "mask of another segment": {"mask_segment": "s9"},
    "mask of an undeclared constraint": {"mask_constraint": "c9"},
    "mask by another backend": {"mask_backend": "xgrammar"},
    "old trace format": {"fmt": "blg-agent-trace/1"},
    # segments and event order
    "segment absent in the declaration and the mask": {"segment": ABSENT},
    "segment null in the declaration and the mask": {"segment": None},
    "segment empty in the declaration and the mask": {"segment": ""},
    "segment declared, absent in the mask": {"mask_segment": ABSENT},
    "segment declared, empty in the mask": {"mask_segment": ""},
    "mask before the compile of its constraint": {"order": ("decision", "mask", "compile")},
    "mask before its cache resolution": {"cache": "hit", "order": ("decision", "mask", "compile")},
    "compile before the decision": {"order": ("compile", "decision", "mask")},
    "compile and mask before the decision": {"order": ("compile", "mask", "decision")},
}


def test_incomplete_or_contradictory_traces_are_unknown(tmp_path):
    for i, (label, kw) in enumerate(BROKEN.items()):
        rc, out = _run(tmp_path / str(i), Fake(_answer(PROBE)), _trace(**kw))
        assert rc == 1, label
        assert {r["qualification"] for r in out["rows"]} == {"unknown"}, label


def test_out_of_order_traces_have_correct_numbering_and_count(tmp_path):
    """The order cases of BROKEN fail on the order alone: the trace itself
    is complete (seq 0..n-1, finish counts the events)."""
    for i, kw in enumerate(({"order": ("decision", "mask", "compile")},
                            {"order": ("compile", "decision", "mask")})):
        events = _trace(**kw)("r", {"tools": [q.tool(None)]})
        assert [e["seq"] for e in events] == list(range(len(events)))
        assert q.trace_problems(events) == []
        row = {"schemas_sha256": q.sent_schemas({"tools": [q.tool(None)]})}
        verdict, reason = q.qualify(row, events)
        assert verdict == "unknown" and "before" in reason, reason


def test_decision_not_to_apply_with_masks_is_unknown(tmp_path):
    def events_for(rid, body):
        evs = _trace(applied=False)(rid, body)
        mask = {"request_id": f"chatcmpl-{rid}", "event": "mask", "backend": "bolorgir", "constraint_id": "c0",
                "segment_id": "s0", "step": 0}
        evs = evs[:-1] + [mask, dict(evs[-1], events=len(evs))]
        return [dict(e, seq=i) for i, e in enumerate(evs)]
    rc, out = _run(tmp_path, Fake(_answer(PROBE)), events_for)
    assert rc == 1 and {r["qualification"] for r in out["rows"]} == {"unknown"}


def test_expected_backend_must_match(tmp_path):
    rc, out = _run(tmp_path, Fake(_answer(PROBE)), _trace(), extra=("--expect-backend", "xgrammar"))
    assert rc == 1 and {r["qualification"] for r in out["rows"]} == {"unknown"}


def test_malformed_trace_line_makes_every_row_unknown(tmp_path):
    def events_for(rid, body):
        return _trace()(rid, body)
    fake = Fake(_answer(PROBE))
    tp = tmp_path / "t"
    rc, out = _run(tp, fake, events_for)
    assert rc == 0
    with open(tp / "trace.jsonl", "a", encoding="utf-8") as f:
        f.write("{not json\n")
    fake.trace = None
    manifest = tp / "stack.json"
    rc = q.main(["--base-url", "http://x/v1", "--model", "m", "--label", "t", "--repeats", "1",
                 "--stack-manifest", str(manifest), "--out", str(tp / "obs2.json"),
                 "--backend-trace", str(tp / "trace.jsonl")], post_fn=fake)
    out = json.loads((tp / "obs2.json").read_text())
    assert rc == 1 and {r["qualification"] for r in out["rows"]} == {"unknown"}
    assert "malformed" in out["rows"][0]["qualification_reason"]


def test_trace_of_other_requests_does_not_qualify(tmp_path):
    rc, out = _run(tmp_path, Fake(_answer(PROBE)), _trace(other_id=True))
    assert rc == 1 and {r["qualification"] for r in out["rows"]} == {"unknown"}


def test_canonical_hash_ignores_key_order():
    a = {"type": "object", "properties": {"x": {"type": "string"}}}
    b = {"properties": {"x": {"type": "string"}}, "type": "object"}
    assert q.canonical_sha256(a) == q.canonical_sha256(b)


def _spliced(events, *extra):
    """``extra`` events inserted before the finish, seq renumbered and the
    finish count fixed."""
    evs = events[:-1] + list(extra) + [events[-1]]
    out = [dict(e, seq=i) for i, e in enumerate(evs)]
    if out[-1].get("event") == "finish":
        out[-1]["events"] = len(out) - 1
    return out


def test_sent_schemas_carries_the_tool_name_fingerprint():
    """The free-text constraint of a served structural tag is verifiable:
    the client's tool-name set canonicalizes to the hash the backend
    declares for it."""
    sent = q.sent_schemas({"tools": [q.tool(None), q.tool(True)]})
    assert sent["structural_tag"] == q.canonical_sha256(["set_code"])
    assert sent["tool:set_code"] == q.canonical_sha256(q.tool(None)["function"]["parameters"])
    assert q.sent_schemas({"tools": []}) == {}


def test_terminal_accept_needs_a_rollback_to_continue():
    """A terminal accept ends the live generation: masks or accepts after
    it are contradictions unless a rollback undid the terminal (vLLM's
    speculative draft cycle)."""
    body = {"tools": [q.tool(None)]}
    row = {"schemas_sha256": q.sent_schemas(body)}
    events = _trace()("r", body)
    term = {"request_id": "chatcmpl-r", "event": "accept", "constraint_id": "c0",
            "step": 1, "token": 258, "terminal": True}
    rb = {"request_id": "chatcmpl-r", "event": "rollback", "tokens": 1,
          "terminal_undone": True}
    mask2 = {"request_id": "chatcmpl-r", "event": "mask", "backend": "bolorgir",
             "constraint_id": "c0", "segment_id": "s0", "step": 1}
    acc2 = {"request_id": "chatcmpl-r", "event": "accept", "constraint_id": "c0",
            "step": 2, "token": 65, "terminal": False}

    verdict, reason = q.qualify(row, _spliced(events, term, mask2))
    assert verdict == "unknown" and "without a rollback" in reason, reason
    verdict, reason = q.qualify(row, _spliced(events, term, acc2))
    assert verdict == "unknown" and "without a rollback" in reason, reason
    # The rollback lifts the terminal; later events are kept.
    verdict, reason = q.qualify(row, _spliced(events, term, rb, mask2, acc2))
    assert verdict == "constrained", reason
    # A rollback that does not undo the terminal does not lift it.
    rb2 = dict(rb, terminal_undone=False)
    verdict, reason = q.qualify(row, _spliced(events, term, rb2, mask2))
    assert verdict == "unknown" and "without a rollback" in reason, reason


def test_rollback_event_shape_is_checked():
    body = {"tools": [q.tool(None)]}
    row = {"schemas_sha256": q.sent_schemas(body)}
    events = _trace()("r", body)
    bad_tokens = {"request_id": "chatcmpl-r", "event": "rollback", "tokens": -1}
    verdict, reason = q.qualify(row, _spliced(events, bad_tokens))
    assert verdict == "unknown" and "tokens" in reason, reason
    bad_flag = {"request_id": "chatcmpl-r", "event": "rollback", "tokens": 1,
                "terminal_undone": "yes"}
    verdict, reason = q.qualify(row, _spliced(events, bad_flag))
    assert verdict == "unknown" and "terminal_undone" in reason, reason


def test_internal_request_id_is_checked():
    """The explicit client/internal id mapping: every event carries the
    same non-empty internal id when it is recorded at all."""
    body = {"tools": [q.tool(None)]}
    row = {"schemas_sha256": q.sent_schemas(body)}
    events = _trace()("r", body)
    consistent = [dict(e, internal_request_id="chatcmpl-r-01") for e in events]
    verdict, reason = q.qualify(row, consistent)
    assert verdict == "constrained", reason
    mixed = [dict(e) for e in consistent]
    mixed[2]["internal_request_id"] = "chatcmpl-r-02"
    verdict, reason = q.qualify(row, mixed)
    assert verdict == "unknown" and "internal_request_id" in reason, reason
    empty = [dict(e, internal_request_id="") for e in events]
    verdict, reason = q.qualify(row, empty)
    assert verdict == "unknown" and "internal_request_id" in reason, reason
