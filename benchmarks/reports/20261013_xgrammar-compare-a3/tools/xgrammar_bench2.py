#!/usr/bin/env python3
"""T10 section-10 comparison, collector v2 (A3 compact series).

Same requests, matrix, prompts, caps, metrics and warmup as the
2026-10-12 collector (`xgrammar_bench.py`, kept for the historical
series); additionally every record stores the answer text and the stream
end state so the report can re-validate answers independently and count
every completion category without losses (TZ 6.2). Timing fields keep
the v1 definitions.

Usage:
  python xgrammar_bench2.py --session x1c --backend xgrammar \
      --out compact/raw-x1c.jsonl [--repeats 2]
"""

import argparse
import asyncio
import json
import time

import httpx

from xgrammar_common import WORKLOADS, payload, prompts, validate

CHAT = "/v1/chat/completions"


async def one(client, url, body, workload, timeout=180.0):
    t0 = time.perf_counter()
    ttft = None
    tokens = None
    text = []
    status = None
    err = None
    finish = None
    final_event = False
    usage_present = False
    transport_error = False
    try:
        async with client.stream("POST", url, json=body, timeout=timeout) as r:
            status = r.status_code
            if status != 200:
                raw = await r.aread()
                err = raw.decode("utf-8", "replace")[:300]
            else:
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        final_event = True
                        break
                    ev = json.loads(data)
                    for ch in ev.get("choices") or []:
                        delta = ch.get("delta") or {}
                        c = delta.get("content")
                        if c:
                            if ttft is None:
                                ttft = time.perf_counter() - t0
                            text.append(c)
                        if ch.get("finish_reason"):
                            finish = ch["finish_reason"]
                    if ev.get("usage"):
                        tokens = ev["usage"].get("completion_tokens")
                        usage_present = True
    except Exception as exc:  # noqa: BLE001
        transport_error = True
        err = err or f"{type(exc).__name__}: {exc}"[:300]
    e2e = time.perf_counter() - t0
    joined = "".join(text)
    if status == 200:
        ok, why = validate(workload, joined)
    else:
        ok, why = False, err or "http error"
    tpot = None
    if ok and tokens and tokens > 1 and ttft is not None:
        tpot = (e2e - ttft) * 1000.0 / (tokens - 1)
    return {"status": status, "finish": finish,
            "ttft_ms": None if ttft is None else ttft * 1000.0,
            "e2e_ms": e2e * 1000.0, "tokens": tokens,
            "tpot_ms": tpot, "valid": ok, "invalid_reason": why,
            "text_len": len(joined), "text": joined,
            "final_event": final_event, "usage_present": usage_present,
            "finish_present": finish is not None,
            "transport_error": transport_error,
            "error_excerpt": (err or "")[:200]}


async def run_session(args):
    url = args.base_url.rstrip("/") + CHAT
    limits = httpx.Limits(max_connections=256, max_keepalive_connections=64)
    records = []
    with open(args.out, "w", encoding="utf-8") as f:
        async with httpx.AsyncClient(limits=limits) as client:
            # discarded warmup
            wl = "w1_json_small"
            await asyncio.gather(*[
                one(client, url, payload(wl, args.model, m), wl)
                for m in prompts(wl)[:4]])
            for w in WORKLOADS:
                plist = prompts(w)
                for batch in args.batches:
                    for rep in range(args.repeats):
                        bodies = [payload(w, args.model, plist[i])
                                  for i in range(batch)]
                        t0 = time.perf_counter()
                        res = await asyncio.gather(
                            *[one(client, url, b, w) for b in bodies])
                        wall = time.perf_counter() - t0
                        for i, r in enumerate(res):
                            rec = {"session": args.session,
                                   "backend": args.backend,
                                   "workload": w, "batch": batch,
                                   "repeat": rep, "idx": i,
                                   "wall_s": round(wall, 4), **r}
                            records.append(rec)
                            # incremental write: a failed session keeps
                            # every completed batch (TZ 6.1)
                            f.write(json.dumps(rec) + "\n")
                        f.flush()
                        toks = sum((r["tokens"] or 0) for r in res)
                        bad = sum(1 for r in res if not r["valid"])
                        fin = sum(1 for r in res if r["finish"] == "stop")
                        print(f"[{args.session}] {w} b={batch} rep={rep} "
                              f"wall={wall:.3f}s tokens={toks} invalid={bad} "
                              f"stop={fin}/{batch}", flush=True)
    print(f"[{args.session}] records={len(records)} -> {args.out}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8021")
    ap.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    ap.add_argument("--session", required=True)
    ap.add_argument("--backend", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--batches", type=int, nargs="+", default=[1, 8, 32, 128])
    return asyncio.run(run_session(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
