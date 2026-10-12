#!/usr/bin/env python3
"""Observations of the serving stack for the agent evaluation (A1).

Sends the request matrix of ADR-0016 to a running OpenAI-compatible vLLM
server and records, per mode and repeat: the exact request with its
correlation id (`X-Request-Id`), the canonical sha256 of every schema the
client sent, the response, every tool call with its arguments, and
whether the arguments follow a deliberately improbable probe schema (an
enum of strings a model would not pick on its own).

An HTTP answer alone never shows that a constraint was applied: a model
may follow the probe by chance, and a backend may have masked a schema
other than the one sent. Without a backend trace every row stays an
observation and every mode `not verified`. With --backend-trace (JSONL
written by an instrumented backend, format TRACE_FORMAT in
benchmarks/agent_eval/PLAN.md, "Evidence format") a row is qualified by
the events of its request id. The trace is keyed by the id the server
returns to the client (the response body id): the server appends a random
suffix to the id it receives internally, and the patched manager passes
the exposed id explicitly (every event may also carry
`internal_request_id`). A row without a response falls back to the id
the client sent.

- `unknown` unless the trace of the request is complete and consistent:
  one start event first (format, backend), one decision event, one
  finish event last that counts the events before it, seq 0..n-1 in
  file order, every compile and mask event by the start's backend
  (and by --expect-backend when given), finish status ok, rollback
  events with a non-negative integer token count, one consistent
  non-empty internal_request_id, and no mask or accept after a terminal
  accept without an intervening rollback;
- `unconstrained` when the backend decided not to apply a constraint
  (decision applied false) and the trace has no compile or mask event;
- `constrained` when the backend decided to apply constraints (applied
  true), every declared constraint has a non-empty id and segment id and
  carries a schema hash the client sent, every declared constraint has
  exactly one successful compile of that hash (a cache hit names the
  request or key it was compiled for), every mask names a declared
  constraint and its non-empty segment, and at least one mask exists;
  in event order the decision precedes every compile and mask, and
  every mask follows the compile of its constraint;
- `unknown` for anything else: a mask without a compile or before it, a
  compile of another schema, a missing segment, a contradiction between
  the decision and the events.

  python3 benchmarks/agent_eval/qualify_stack.py --base-url http://127.0.0.1:8000/v1 \\
      --model MODEL --label xgrammar --stack-manifest stack.json --out obs.json \\
      [--backend-trace trace.jsonl [--expect-backend xgrammar]] [--server-log vllm.log]

Exit status: 0 when every request got HTTP 200 (and, with a trace, every
row is qualified constrained or unconstrained), 1 otherwise. Uses only
the standard library.
"""

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
import uuid

PROBE_VALUES = ["zq-17-wombat", "xv-03-quokka", "kp-88-axolotl"]
TRACE_FORMAT = "blg-agent-trace/2"


def canonical_sha256(schema):
    """sha256 of the canonical JSON text of a schema (sorted keys, no
    spaces, UTF-8); the instrumented backend reports the same form."""
    text = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tool(strict):
    fn = {
        "name": "set_code",
        "description": "Store the access code the user asked for.",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "enum": PROBE_VALUES}},
            "required": ["code"],
            "additionalProperties": False,
        },
    }
    if strict is not None:
        fn["strict"] = strict
    return {"type": "function", "function": fn}


MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant that uses tools when needed."},
    {"role": "user", "content": "Store the access code 1234 for me, please."},
]

MODES = [
    ("none", {"tools": [tool(None)], "tool_choice": "none"}),
    ("auto", {"tools": [tool(None)], "tool_choice": "auto"}),
    ("auto_strict", {"tools": [tool(True)], "tool_choice": "auto"}),
    ("required", {"tools": [tool(None)], "tool_choice": "required"}),
    ("named", {"tools": [tool(None)],
               "tool_choice": {"type": "function", "function": {"name": "set_code"}}}),
    ("response_format", {"response_format": {"type": "json_schema", "json_schema": {
        "name": "code", "strict": True, "schema": tool(None)["function"]["parameters"]}}}),
]


def sent_schemas(body):
    """{name: canonical sha256} of the schemas a request carries.

    With tools, also the fingerprint of the served structural tag's
    free-text constraint: the canonical hash of the sorted unique tool
    names, which the instrumented backend reports as the schema_sha256 of
    its ``structural_tag`` constraint (free-text and ambiguous-framing
    masks). The tag spec itself is built server-side from the tool list
    and is not part of the client request.
    """
    out = {}
    tools = body.get("tools") or []
    for t in tools:
        out[f"tool:{t['function']['name']}"] = canonical_sha256(t["function"]["parameters"])
    names = sorted({t["function"]["name"] for t in tools})
    if names:
        out["structural_tag"] = canonical_sha256(names)
    rf = body.get("response_format") or {}
    if rf.get("type") == "json_schema":
        out["response_format"] = canonical_sha256(rf["json_schema"]["schema"])
    return out


def post(url, body, timeout, request_id):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json",
                                          "X-Request-Id": request_id})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (r.status, json.loads(r.read().decode("utf-8")),
                    dict(r.headers), time.monotonic() - t0)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")}, dict(e.headers or {}), \
            time.monotonic() - t0
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, {"error": f"{type(e).__name__}: {e}"}, {}, time.monotonic() - t0


def observe(status, resp):
    out = {"http_status": status}
    if status != 200:
        out["outcome"] = "error"
        return out
    choice = resp["choices"][0]
    msg = choice["message"]
    out["finish_reason"] = choice.get("finish_reason")
    out["content"] = msg.get("content")
    calls = []
    for c in msg.get("tool_calls") or []:
        fn = c.get("function") or {}
        rec = {"id": c.get("id"), "name": fn.get("name"), "arguments": fn.get("arguments")}
        try:
            args = json.loads(fn.get("arguments") or "")
            rec["arguments_json"] = True
            rec["probe_followed"] = isinstance(args, dict) and args.get("code") in PROBE_VALUES
        except (ValueError, TypeError):
            rec["arguments_json"] = False
            rec["probe_followed"] = False
        calls.append(rec)
    out["tool_calls"] = calls
    if not calls and msg.get("content"):
        try:
            args = json.loads(msg["content"])
            out["content_probe_followed"] = isinstance(args, dict) and args.get("code") in PROBE_VALUES
        except (ValueError, TypeError):
            out["content_probe_followed"] = False
    out["outcome"] = "ok"
    return out


def load_trace(path):
    """({request id: [events in file order]}, numbers of malformed lines)
    of a backend trace (JSONL)."""
    out, bad = {}, []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if not isinstance(ev, dict):
                    raise ValueError("not an object")
            except ValueError:
                bad.append(n)
                continue
            out.setdefault(ev.get("request_id"), []).append(ev)
    return out, bad


def trace_problems(events, expect_backend=None):
    """Why the events of one request are not a complete, consistent
    trace; an empty list when they are."""
    probs = []
    kinds = [e.get("event") for e in events]
    if [e.get("seq") for e in events] != list(range(len(events))):
        probs.append("seq is not 0..n-1 in file order")
    if kinds[:1] != ["start"] or kinds.count("start") != 1:
        probs.append("the trace does not open with one start event")
    if kinds[-1:] != ["finish"] or kinds.count("finish") != 1:
        probs.append("the trace does not end with one finish event (incomplete)")
    elif events[-1].get("events") != len(events) - 1:
        probs.append(f"finish counts {events[-1].get('events')} events, "
                     f"the trace has {len(events) - 1}")
    elif events[-1].get("status") != "ok":
        probs.append(f"finish status {events[-1].get('status')!r}")
    if kinds.count("decision") != 1:
        probs.append(f"{kinds.count('decision')} decision events, expected one")
    start = events[0] if kinds[:1] == ["start"] else {}
    if start.get("format") != TRACE_FORMAT:
        probs.append(f"trace format {start.get('format')!r}, expected {TRACE_FORMAT}")
    backend = start.get("backend")
    if not backend:
        probs.append("the start event names no backend")
    elif expect_backend and backend != expect_backend:
        probs.append(f"backend {backend}, expected {expect_backend}")
    other = [e for e in events if e.get("event") in ("compile", "mask")
             and e.get("backend") != backend]
    if other:
        probs.append(f"{other[0].get('event')} by backend {other[0].get('backend')!r}, "
                     f"the trace is {backend!r}")
    # The engine-internal request id, when recorded, is the same on every
    # event and non-empty: it is the explicit client/internal mapping.
    iids = [e.get("internal_request_id") for e in events if "internal_request_id" in e]
    if any(not _named(v) for v in iids) or len(set(iids)) > 1:
        probs.append("internal_request_id is empty or differs between events")
    for i, e in enumerate(events):
        if e.get("event") != "rollback":
            continue
        tokens = e.get("tokens")
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            probs.append(f"rollback at seq {i} has no non-negative integer tokens")
        if "terminal_undone" in e and not isinstance(e.get("terminal_undone"), bool):
            probs.append(f"rollback at seq {i} has a non-boolean terminal_undone")
    # A terminal accept ends the live generation: masks or accepts after
    # it require an intervening rollback that undoes the terminal (vLLM
    # accepts draft terminals and rolls them back in the same step).
    open_terminal = False
    for i, e in enumerate(events):
        kind = e.get("event")
        if kind in ("accept", "mask") and open_terminal:
            probs.append(f"{kind} at seq {i} after a terminal accept without a rollback")
            break
        if kind == "accept" and e.get("terminal") is True:
            open_terminal = True
        elif kind == "rollback" and e.get("terminal_undone") is True:
            open_terminal = False
    return probs


def _named(value):
    return isinstance(value, str) and value != ""


def qualify(row, events, expect_backend=None):
    """`constrained`, `unconstrained` or `unknown`, with the reason. The
    position of an event is its seq (trace_problems checks seq 0..n-1)."""
    if not events:
        return "unknown", "no trace events for this request id"
    probs = trace_problems(events, expect_backend)
    if probs:
        return "unknown", "; ".join(probs)
    backend = events[0]["backend"]
    at_dec, dec = next((i, e) for i, e in enumerate(events) if e.get("event") == "decision")
    compiles = [(i, e) for i, e in enumerate(events) if e.get("event") == "compile"]
    masks = [(i, e) for i, e in enumerate(events) if e.get("event") == "mask"]
    if dec.get("applied") is False:
        if compiles or masks:
            return "unknown", ("the backend decided not to apply a constraint, but the trace "
                               "has compile or mask events")
        return "unconstrained", f"backend {backend} decided not to apply: {dec.get('reason')}"
    if dec.get("applied") is not True:
        return "unknown", "the decision event has no applied true or false"
    declared = dec.get("constraints") or []
    cons = {c.get("constraint_id"): c for c in declared}
    if not declared or not all(_named(cid) for cid in cons) or len(cons) != len(declared):
        return "unknown", "the decision declares no constraint, or ids are missing or repeated"
    sent = set(row["schemas_sha256"].values())
    for cid, c in cons.items():
        if not _named(c.get("segment_id")):
            return "unknown", f"constraint {cid}: the decision names no segment"
        if c.get("schema_sha256") not in sent:
            return "unknown", f"constraint {cid}: its schema hash is not one the client sent"
    for i, e in compiles + masks:
        if e.get("constraint_id") not in cons:
            return "unknown", f"{e.get('event')} of an undeclared constraint {e.get('constraint_id')!r}"
        if i < at_dec:
            return "unknown", f"{e.get('event')} at seq {i} before the decision at seq {at_dec}"
    hits, compiled_at = 0, {}
    for cid, c in cons.items():
        cc = [(i, e) for i, e in compiles if e.get("constraint_id") == cid]
        if len(cc) != 1:
            return "unknown", f"constraint {cid}: {len(cc)} compile events, expected one"
        i, e = cc[0]
        if e.get("status") != "ok":
            return "unknown", f"constraint {cid}: compile status {e.get('status')!r}"
        if e.get("schema_sha256") != c["schema_sha256"]:
            return "unknown", f"constraint {cid}: the compiled schema is not the declared one"
        if e.get("cache") not in ("miss", "hit"):
            return "unknown", f"constraint {cid}: cache {e.get('cache')!r}, expected miss or hit"
        if e.get("cache") == "hit":
            if not e.get("compiled_from"):
                return "unknown", f"constraint {cid}: a cache hit without its origin"
            hits += 1
        compiled_at[cid] = i
    for i, m in masks:
        cid = m["constraint_id"]
        if not _named(m.get("segment_id")):
            return "unknown", f"mask at seq {i} of constraint {cid} names no segment"
        if m["segment_id"] != cons[cid]["segment_id"]:
            return "unknown", (f"mask of constraint {cid} in segment {m['segment_id']!r}, "
                               f"declared {cons[cid]['segment_id']!r}")
        if i < compiled_at[cid]:
            return "unknown", (f"mask at seq {i} of constraint {cid} before its compile "
                               f"at seq {compiled_at[cid]}")
    if not masks:
        return "unknown", "constraints compiled, but no mask in the trace"
    used = sorted({m["constraint_id"] for _i, m in masks})
    return "constrained", (f"backend {backend}: {len(cons)} constraints compiled ({hits} from the "
                           f"cache), {len(masks)} masks of {', '.join(used)}")


def main(argv=None, post_fn=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", required=True, help="backend / mode label recorded in the output")
    ap.add_argument("--stack-manifest", required=True,
                    help="JSON with vLLM version, patch, model and revision, server flags")
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend-trace", help="JSONL trace of an instrumented backend")
    ap.add_argument("--expect-backend", help="with a trace: the backend every request must name")
    ap.add_argument("--server-log", help="vLLM log file; its tail after each request is recorded")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args(argv)
    post_fn = post_fn or post

    with open(args.stack_manifest, "rb") as f:
        manifest_bytes = f.read()
    stack = json.loads(manifest_bytes.decode("utf-8"))
    url = args.base_url.rstrip("/") + "/chat/completions"
    rows = []
    for name, extra in MODES:
        for rep in range(args.repeats):
            body = {"model": args.model, "messages": MESSAGES, "temperature": 1.0,
                    "seed": 1000 + rep, "max_tokens": 256, **extra}
            rid = f"qualify-{args.label}-{name}-{rep}-{uuid.uuid4().hex[:12]}"
            log_pos = None
            if args.server_log:
                with open(args.server_log, "rb") as f:
                    f.seek(0, 2)
                    log_pos = f.tell()
            status, resp, headers, secs = post_fn(url, body, args.timeout, rid)
            row = {"mode": name, "repeat": rep, "request_id": rid, "request": body,
                   "schemas_sha256": sent_schemas(body),
                   "response_id": resp.get("id") if isinstance(resp, dict) else None,
                   "response_request_id": {k.lower(): v for k, v in headers.items()}.get(
                       "x-request-id"),
                   "response": resp, "seconds": round(secs, 3), **observe(status, resp)}
            if log_pos is not None:
                with open(args.server_log, "rb") as f:
                    f.seek(log_pos)
                    row["server_log"] = f.read().decode("utf-8", "replace")[-4000:]
            rows.append(row)
            print(f"{name} #{rep}: {row['outcome']} tool_calls={len(row.get('tool_calls') or [])}",
                  flush=True)

    trace, malformed = load_trace(args.backend_trace) if args.backend_trace else (None, [])
    for r in rows:
        if trace is None:
            r["evidence"] = "observation"
            r["qualification"], r["qualification_reason"] = None, "no backend trace"
        elif malformed:
            r["evidence"] = "backend trace"
            r["qualification"], r["qualification_reason"] = (
                "unknown", f"the trace has {len(malformed)} malformed lines, e.g. line {malformed[0]}")
        else:
            # The trace is keyed by the id the server returns to the
            # client (the response body id): the engine appends a random
            # suffix to the id it receives, and the patched manager passes
            # the exposed id explicitly. Rows without a response (an HTTP
            # error) fall back to the id the client sent.
            r["evidence"] = "backend trace"
            key = r.get("response_id") or r["request_id"]
            r["trace_key"] = key
            r["qualification"], r["qualification_reason"] = qualify(
                r, trace.get(key), args.expect_backend)

    summary = {}
    for r in rows:
        s = summary.setdefault(r["mode"], {"n": 0, "errors": 0, "tool_calls": 0,
                                           "probe_followed": 0, "qualifications": {}})
        s["n"] += 1
        s["errors"] += r["outcome"] != "ok"
        s["tool_calls"] += len(r.get("tool_calls") or [])
        s["probe_followed"] += sum(bool(c["probe_followed"]) for c in r.get("tool_calls") or [])
        q = r["qualification"] or "not verified"
        s["qualifications"][q] = s["qualifications"].get(q, 0) + 1
    for s in summary.values():
        qs = s["qualifications"]
        s["verdict"] = next(iter(qs)) if len(qs) == 1 else "mixed"
    errors = sum(r["outcome"] != "ok" for r in rows)
    unmatched = sum(1 for r in rows if trace is not None and r["qualification"] == "unknown")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"label": args.label, "base_url": args.base_url, "model": args.model,
                   "stack_manifest": {"path": args.stack_manifest,
                                      "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                                      "content": stack},
                   "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "evidence": "backend trace" if trace is not None else "observation only",
                   "trace_format": TRACE_FORMAT if trace is not None else None,
                   "summary": summary, "http_errors": errors, "rows": rows}, f, indent=1)
        f.write("\n")
    print(json.dumps(summary, indent=1))
    return 1 if errors or unmatched else 0


if __name__ == "__main__":
    raise SystemExit(main())
