# XGrammar comparison protocol (TZ section 10), pinned 2026-10-12 before the first collection

The TZ section 10 asks for a comparison report against XGrammar in the
same vLLM: TTFT, throughput, per-step costs, batch `1/8/32/128`, several
request schemas. Conditions, order, limits and the budget are pinned in
this file before any measurement; the raw records stay next to it.

## Stack (frozen for all sessions)

- vLLM 0.31.0 (wheel sha256 `39132b43…`), patched, port 8021.
- Model `Qwen/Qwen2.5-0.5B-Instruct`, revision `7ae557604a…`.
- RTX 3060 12 GiB, driver 580.178.04; `VLLM_USE_FLASHINFER_SAMPLER=0`.
- No speculative decoding (the spec/off comparison of the functional
  matrix is not repeated here; this report compares backends).
- Prefix caching is disabled in both sessions (`--no-enable-prefix-caching`)
  so repeated identical prompts cannot warm a cache between rounds.
- Bolorgir sessions: repo tree `python/` on `PYTHONPATH`, adapter
  `python/bolorgir/vllm.py` sha256 `7bbee811…`, candidate core
  `libbolorgir.so.0.1.0` sha256 `9a5baf3e…`, `BLG_AGENT_TRACE` per
  session (proof that the backend served the traffic).
- XGrammar sessions: the same server command with
  `--structured-outputs-config '{"backend": "xgrammar"}'` and without the
  repo `python/` on `PYTHONPATH` (the bolorgir backend is not importable
  there); the packaged xgrammar 0.2.7 of the serving venv is used.
- Repo HEAD `003f1b3`, clean tree.

## Workloads ("different request schemas")

- `w1_json_small`: `response_format` JSON schema, object `{action enum
  [reply, escalate], priority int 1..5}`, `max_tokens` 64.
- `w2_json_nested`: `response_format` JSON schema, object with a
  three-item array of `{name, count}` and a `total`, `max_tokens` 64.
- `w3_regex64`: `structured_outputs.regex = "[0-9]{64}"`,
  `max_tokens` 72. Fallback pinned in advance: should the xgrammar
  backend refuse this request class outright, the row is recorded as a
  capability difference and excluded from the perf table for both
  backends.

Prompts are a fixed deterministic list of 128 items per workload
(`xgrammar_bench.py:prompts`); every session uses the same list and the
same first-`B` items per batch size. `temperature=0.0`,
`stream=true`, `stream_options.include_usage=true`.

## Design, order, limits

- Batch sizes `1/8/32/128`; batch = that many concurrent requests.
- Sessions in the order `b1, x1, b2, x2` (bolorgir, xgrammar, bolorgir,
  xgrammar) - a second block lets drift be visible; each session runs
  the full matrix twice (`--repeats 2`), a discarded warmup of four
  `w1` requests runs first.
- One session = one server start; the server is stopped
  (SIGINT) when the session's records are written.
- Per-request timeout 180 s; a session is aborted when the server
  fails `/health`; total budget: four sessions, each bounded by 60 min.
- Metrics, client-side per request: TTFT (send → first non-empty
  content delta), TPOT (per output token after the first:
  `(e2e − TTFT)/(completion_tokens − 1)`), e2e, completion tokens
  (from the usage chunk). Throughput of a batch run: sum of completion
  tokens over the wall time of the batch. Reported per cell as the
  median over all runs of the four samples.
- Validity: every response is validated independently (`w1`/`w2`
  against their schema with `jsonschema`, `w3` by full regex match); a
  run with any invalid or failed request is excluded from the timing
  aggregation and listed in the report.
- No competing heavy processes run during any session (the T3
  acceptance run starts only after the last session).

## Evidence

`xgrammar_bench.py` writes one JSONL per session (`raw-<session>.jsonl`,
one record per request) and, with `--summarize`, `summary.json` and
`REPORT.md`. Server logs, traces (bolorgir sessions) and pod logs live in
this directory.

## Amendment A1 (2026-10-12, written before session x1)

The first b1 collection exposed a behaviour difference pinned here before
the comparison continues: for `{"type":"integer","minimum":1,
"maximum":5}` the greedy model can emit `0.5000…`. Bolorgir's certificate
keeps `0` alive because `0.5e1` (= 5) is a completing exponent spelling
(JSON numbers carry exponents); xgrammar 0.2.7 does not recognise that
completion and withholds `0` already at the first digit. Both backends
keep every *completed* document valid; the amendment only affects
counting:

- primary timing aggregates use every HTTP-200 request (`length` cuts
  are legal partial documents, not mask failures);
- a cell is excluded from the throughput aggregate and flagged when a
  request is invalid at `stop` (a constraint breach) or the HTTP status
  is not 200;
- the tables report `stop ok` / `length` / `breach` counts separately.

Session b1 is re-collected once with the `finish_reason` field captured
(the first b1 raw file lacked it); conditions are otherwise unchanged.

## Amendment A2 (2026-10-12, after x1, before re-collection)

Session x1 showed a second mask-language difference: the xgrammar backend
allows whitespace by default (`disable_any_whitespace=false`), the model
answer is pretty-printed and the 64-token cap cuts every `w2` document
(three items plus total do not fit); the bolorgir backend compiles a
compact JSON language (the adapter refuses `whitespace_pattern`, a
documented refusal), the same answers fit in ~39 tokens and stop. Both
languages complete only valid documents. For a fair `w2` timing row the
cap is raised:

- `w2_json_nested` `max_tokens` 64 -> **128** in both backends;
- all four sessions are re-collected under A2 (b1/x1 raw files are
  overwritten), in the order **x1, b1, x2, b2** (the two xgrammar and the
  two bolorgir sessions alternate; the earlier order note in A1 is
  superseded);
- `w1` keeps 64 (its length cuts are the model's own choice on a live
  path, see A1); `w3` keeps 72.

## Amendment A3 (2026-10-13, written before the compact re-collection)

Review of the first series: ADR-0016 Finding 4 requires
`disable_any_whitespace=True` for the comparison so that both backends
constrain the same language, but both xgrammar sessions (`x1`, `x2`) ran
with the vLLM default `disable_any_whitespace=False` (server logs:
`StructuredOutputsConfig(backend='xgrammar', disable_any_whitespace=False, ...)`);
their `w2` answers are pretty-printed (median 78 tokens against the
compact 39), so the `w2` timing rows of the first series compare
different languages, and A2's cap raise lifted the cut without removing
the format difference. The first series stays on record as collected
(the raw files and its report are kept; the report's language note
stands), and A3 adds a like-for-like series:

- the xgrammar sessions run with
  `--structured-outputs-config '{"backend": "xgrammar", "disable_any_whitespace": true}'`;
  bolorgir stays as pinned (`{"backend": "bolorgir"}`, compact by
  construction);
- sessions **x1c, b1c, x2c, b2c** in that order (the A2 alternation),
  same matrix, prompts, caps (`w2` 128) and metrics; one server start
  per session; records into `compact/` (`raw-<s>.jsonl`,
  `server-<s>.log`, `prov-<s>.txt`); the 2026-10-12 files stay
  untouched;
- `xgrammar_bench.py --summarize` prints the session list of the files
  it summarizes (header line only, no metric change);
- the wording fixes requested for the first series (the length cuts are
  legal partials, validity applies to completed documents; TPOT is the
  client-observed per-token time, not a profiled mask cost) are applied
  in the reports of both series.

## Amendment A4 (2026-10-13): re-run with per-session EngineCore binding

Review point (2026-10-13): the A3 pass recorded the EngineCore load
confirmation (the separate `engine_core_check.sh` run) but the manifest
of the pass itself keeps `engine_maps` empty - the provenance snapshot
was taken before the adapter loads the core. A confirmation of the same
configuration in another process is not a confirmation for the measured
sessions, so the compact series is re-run (nothing about the request
matrix changes; the pass runs after the A3 results were seen and
therefore gets its own identity):

- output directory `compact-r2/`; session labels **x1d, b1d, x2d, b2d**
  (same alternation xgrammar, bolorgir, xgrammar, bolorgir; one server
  start per session; port 8021); the re-run uses the standalone
  `tools/run_compact.sh`, which takes the output directory as an
  argument, resolves the repository root relative to itself, uses only
  the tools published in `tools/` and compares the full core SHA-256;
- per session **two EngineCore scans** (mid-session, after the first
  records are on disk, and post-session): PID, comm, argv excerpt, the
  `libbolorgir.so.0.1.0` mapping line from `/proc/<pid>/maps` and the
  full SHA-256 of the library file; a bolorgir session must show the
  mapping in both scans, an xgrammar session must not (negative
  control). The manifest grows a per-session `engine` block;
- wording correction: the A3 line above that calls the length cuts
  "legal partials" is superseded - a `length` record is not counted as
  a completed document and the label is reserved for a separately
  verified prefix check (review 2026-10-13, TZ §6.2); the v2 reports
  already state this;
- expected result: the completion categories and the aggregated numbers
  reproduce the A3 pass up to run-to-run spread; differences are
  reported, not smoothed.

The first pass (`compact/`, labels x1c/b1c/x2c/b2c) stays unchanged as
history; the pass whose manifest carries the per-session binding
(`compact-r2/`) is the canvas for the EngineCore claim, with A3 as the
repeated measurement.

