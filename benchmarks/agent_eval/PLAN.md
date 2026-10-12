# Agent evaluation: experiment plan (stage A1 result)

Plan for stages A2 and A3 of `TZ_VLLM_AGENT_EVAL.md`. The connection
point, the missing structural-tag contract and the Zig/Python split are in
[ADR-0016](../../docs/adr/ADR-0016-vllm-agent-integration.md). Nothing in
this directory has run yet; no number below is a result.

## Preconditions

| Condition | State on 2026-10-11 | Blocks |
|---|---|---|
| T3 acceptance of the kernel candidate | open; the current kernel (spec-v1, Zig 0.17.0) has no accepted R6 yet | A2 acceptance, A3 |
| Packages (T7a) | workflows exist and ran for the withdrawn rc1; a fresh candidate build after the current changes is pending | T10 executable checks |
| T9 rollback and draft validation | implemented in `main` (ADR-0014 proposed); functional checks green; the overhead bound measurement is recorded with the candidate | speculative decoding rows of T10 |
| T12 standalone regex | implemented in `main` (ADR-0015 proposed); differential checks against an ECMA reference green on the byte vocabulary | regex requests in modes B/X |
| T10 vLLM backend | implemented (`python/bolorgir/vllm.py`) with the pinned patch (`integrations/vllm/`); CPU functional checks green; GPU serving checks pending | A2 |
| Structural-tag constraint kind | not implemented; the backend refuses `STRUCTURAL_TAG` explicitly (ADR-0016 Finding 3) | mode B for the agent's `auto` |
| Backend trace for the qualification | not written (format below); instrumentation is an A2 step | every "verified" row of ADR-0016 |
| Pins of Harbor, OpenCode, model, template, server settings | candidates only (ADR-0016) | A2 |

## Fixed configuration (to pin in A1.3, before any A3 measurement)

Nothing in this section is pinned yet; it lists what has to be.

- vLLM 0.31.0 from the sdist hash in ADR-0016, patched; xgrammar as
  shipped with that vLLM.
- Model: an instruct model with the `hermes` tool parser that fits the
  12 GiB GPU with the agent's context (candidates: a 4B model in BF16 or
  an 8B model in an AWQ build); weights and tokenizer revisions, dtype,
  quantization and max model length recorded.
- Server flags common to all modes: `--tool-call-parser hermes
  --enable-auto-tool-choice --return-tokens-as-token-ids --logprobs-mode
  processed_logprobs`, one `--tool-strict-level` value for B and X,
  `disable_any_whitespace=true` for X.
- Mode U: `--structured-outputs-config '{"backend": "xgrammar"}'` with
  `--tool-strict-level auto` and tools without `strict`, so by the source
  no constraint is sent; only a backend trace without a compile event for
  the request shows it (Evidence format below).
- Mode B: the patched backend `bolorgir`, spec-v1, `number_format=value`.
- Mode X: backend `xgrammar`, same strict level and parser as B.
- OpenEnv commit and Harbor version from ADR-0016, OpenCode pinned through
  a harness profile, sandbox backend `docker` on this machine.

## Task set

- At least 50 tasks with an automatic verifier that checks the result of
  the tool work, plus at least 10 separate diagnostic tasks for A2.
- Source candidates: Harbor datasets with data-agent or coding tasks; the
  selection rule, the licenses and the verifier hashes are written to
  `tasks.json` before any B or X run. The selection uses only a control
  run (reference solution through the verifier), never B/X outcomes.
- Seeds: three per task, fixed in the manifest: 450 attempts in total.

## Run order and limits

- Balanced order per task and seed, generated from the manifest seed
  (U, B, X permuted per pair), every attempt in a fresh sandbox.
- Limits fixed from the A2 diagnostics before A3: requests and tool calls
  per task, input and output tokens, task time, memory, parallelism
  (one agent at a time on this machine), total GPU hours.
- A resumed series continues only with identical inputs (manifest hash,
  lock files, weights, server flags); a technical retry links to the
  original attempt and repeats the capture for all three modes of that
  task and seed.

## Data and metrics

The record levels of spec section 5 (run, task, request, generation,
constraint, measurement) are written by the capture proxy plus the
adapter's mask trace (hashes of masks per step, segment ids, schema
hashes). Metrics and the paired task-level bootstrap (10,000 resamples,
fixed seed) follow spec section A3.3; every planned task/seed stays in
the denominator.

## Observations and qualification (A1.3)

`qualify_stack.py` sends the request matrix of ADR-0016 to a running
server and records, per mode and repeat, the request with a correlation
id, the canonical sha256 of every schema sent, the response, every tool
call with its arguments, and whether the arguments follow a deliberately
improbable probe schema (an enum of unusual strings). It exits non-zero
on any HTTP or connection error. Its output is an observation: a model
may follow the probe by chance, and an answer does not show which
schema, if any, the backend masked. Every mode stays "not verified"
until a backend trace qualifies it; `tests/test_agent_qualify.py`
covers the collector on synthetic answers and traces.

## Evidence format (to be produced by an instrumented backend)

Trace format `blg-agent-trace/2`; the serving backend does not write it yet
(instrumentation is an A2 step).
A qualification needs, per request:

1. Correlation: the client's `X-Request-Id` (also returned by the
   server) is the key of every record below; the stack manifest (vLLM
   version and patch hash, model and tokenizer revisions, chat template
   hash, server flags, backend versions) is recorded once per run with
   its sha256.
2. Schema before and after the transfer: the client records the
   canonical sha256 (sorted keys, no spaces, UTF-8) of each schema it
   sends; the backend records the same hash of each schema it applies,
   per tool or structural-tag segment.
3. Backend events, one JSON object per line, each with `request_id` and
   `seq` (0, 1, ... per request, in write order):
   - `{"event": "start", "format": "blg-agent-trace/2", "backend",
     "backend_version"}`, first;
   - `{"event": "decision", "applied": true|false, "reason",
     "constraints": [{"constraint_id", "kind": "tool"|"response_format"|
     "structural_tag", "segment_id", "schema_sha256"}]}`, exactly one:
     whether the backend applies constraints to this request and which;
     `constraint_id` and `segment_id` are non-empty strings;
   - `{"event": "compile", "backend", "constraint_id", "schema_sha256",
     "status", "cache": "miss"|"hit", "compiled_from"}`, one per declared
     constraint; a cache hit names in `compiled_from` the request id or
     cache key the grammar was compiled for;
   - `{"event": "mask", "backend", "constraint_id", "segment_id", "step",
     "allowed", "mask_sha256"}` and `{"event": "accept", "constraint_id",
     "step", "token"}`; the mask's `segment_id` is non-empty and equal
     to the declared one;
   - `{"event": "finish", "status", "events"}`, last, with the number of
     events written before it: a trace without it, or with another count,
     is incomplete.

   Event order, by `seq`: start, then the decision before every
   compile and mask, then for each constraint its compile (the cache
   resolution, for a hit) before every mask of that constraint, finish
   last. Consecutive numbers do not make an order valid: a mask written
   before the compile of its constraint, even one followed by that
   compile, was not shown to come from it.
4. The answer to the agent: every tool call (name, arguments) and the
   text, as the client received them.

The collector (`--backend-trace`, optionally `--expect-backend`) applies
these rules per request id:

- `unknown` unless the trace is complete and consistent: one start
  first, one decision, one finish last with status ok and the right
  count, seq 0..n-1, every compile and mask by the start's backend;
- `unconstrained` only with `applied: false` and no compile or mask;
- `constrained` only with `applied: true`, every declared constraint
  with a non-empty id and segment id, carrying a hash the client sent
  and having one successful compile of that hash (a cache hit with its
  origin), every mask naming a declared constraint and its non-empty
  segment, at least one mask, and the order above;
- `unknown` for everything else: a mask without a compile or before it,
  a compile or mask before the decision, a segment missing or empty on
  either side, a compile of another schema, masks after a decision not
  to apply, a malformed line anywhere in the trace.

Absent events are never read as "no constraint": a truncated trace, a
grammar reused from a cache without its origin or a request the backend
did not decide on stays `unknown`. `tests/test_agent_qualify.py` holds
synthetic negative cases for each rule. The backend instrumentation and
the GPU runs are later steps.
