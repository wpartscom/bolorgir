# ADR-0016: vLLM backend and agent capture, connection point (A1)

- Status: **proposed** (2026-10-11; the GPU stage of the T10 checks
  executed 2026-10-12). The connection point is implemented and
  functionally checked on CPU; runtime rows of the matrix below are
  "verified in-repo", "verified on CPU", "explicitly refused", "verified
  on GPU" (each such row names its evidence) or "not verified". This
  revision implements the structural-tag constraint kind (Finding 3) and
  the `blg-agent-trace/2` backend trace; the GPU stage then ran the
  server-side scenarios on the pinned stack, and the map of the earlier
  revision is kept by `git log`.
- Status note (2026-10-12, round 7): the review of the round-6 evidence
  was closed with the round-7 runs. Controlled resource-limit failures
  are modelled on the pinned stack (a per-call work budget that dies
  inside the mask fill during generation, and a session budget that
  rejects the structural-tag session allocation; both request-local,
  the regex neighbour completes, and the engine statistics show every
  released session), the forbidden high-score candidate is evidenced by
  a concrete token id with a measured pre-mask advantage and its
  exclusion after the mask in both speculative configurations, the
  batch row reorder is shown by the request-to-row mapping before and
  after the neighbour's completion with a replay audit of every applied
  mask, and the evidence is bound to the frozen candidate `9a5baf3e…`
  (run5 manifests plus live `maps` records of the EngineCore process).
  Full pytest runs are regression checks; the fresh T3/R6 acceptance of
  the current core (for a stable release an adopted T8a as well) and the
  XGrammar comparison report remain outstanding.
- Status note (2026-10-12, round 7b): two follow-ups of the intermediate
  review were closed. The resource-limit checks now also cover a failure
  while a healthy neighbour request is generating in the same engine
  batch (the limits re-runs pass 10/10 in both configurations: the
  stream keeps producing tokens after the failure, finishes with a valid
  constrained result and both sessions release through the next pump),
  and the batch-row-reorder verdict was moved from the per-request `seq`
  counters (which restart at zero per request) to the positions of the
  events in the shared trace JSONL, guarded by a built-in negative
  control; the saved traces were re-evaluated without new traffic
  (`reorder-nospec-run5b.json`, `reorder-spec-run5b.json`, mask audit
  2/2 + 25/25 in both modes).
- Context: the serving integration asks for a vLLM structured-output
  backend and for stage A1 of the agent evaluation: fix the stack, trace
  the tool schema path, define segment boundaries and the split between
  Zig, the C ABI and Python before the agent experiment. The facts below
  were checked against the pinned sources of the tag (git commit
  `db9527a46873454610df6dbedf79a36d6bf1a7f6`, tag `v0.31.0`).

## Pinned candidates

| Component | Candidate | Identity |
|---|---|---|
| vLLM | 0.31.0 | git tag `v0.31.0`, commit `db9527a46873454610df6dbedf79a36d6bf1a7f6` |
| OpenEnv (capture proxy, Harbor environment) | `huggingface/openenv` main | commit `7ccc57acc7b70fd6361ee08b2db0e7a7c44203dc` (recorded when the analysis was written; re-pin before A2) |
| Harbor | OpenEnv requirement | exact version fixed when the environment is installed |
| Agent | OpenCode, chat-completions dialect | version fixed through a Harbor harness profile before A3 |
| Model (agent experiment) | a Qwen3 or Qwen2.5 instruct model that fits the 12 GiB RTX 3060 of this machine, with the `hermes` tool parser | revision fixed in A1.3 |
| Model (serving technical checks) | `Qwen/Qwen2.5-0.5B-Instruct` | revision fixed in A2 |

OpenEnv's Harbor extra needs Python 3.12 or newer; the project venv
stays as it is and the stack gets its own environment.

## Finding 1: vLLM 0.31.0 has no registration point for a new backend

- The backend name is a closed `Literal["auto", "xgrammar", "guidance",
  "outlines", "lm-format-enforcer"]` (`vllm/config/structured_outputs.py`).
- `StructuredOutputManager.grammar_init` instantiates the backend in a
  fixed `if`/`elif` chain (`vllm/v1/structured_output/__init__.py`).
- Request validation and the `auto` choice are in
  `SamplingParams._validate_structured_outputs`
  (`vllm/sampling_params.py`).

A `bolorgir[vllm]` package therefore cannot register itself. The delivery
is a reproducible patch against the pinned tag
(`integrations/vllm/vllm-0.31.0-bolorgir.patch`, sha256 `d0b8ea80…`) that
adds the name `bolorgir` in the three places and imports the backend
class from `bolorgir.vllm`; the same change is the material for an
upstream PR. A monkeypatch from a general vLLM plugin is not equivalent:
the `Literal` is checked by pydantic before plugins could act.

Implemented interface (`python/bolorgir/vllm.py`):
`StructuredOutputBackend.compile_grammar(request_type, grammar_spec,
stop_token_ids, request_id)`, `allocate_token_bitmask(max_num_seqs)`,
`destroy()`;
`StructuredOutputGrammar.accept_tokens(request_id, tokens) -> bool`,
`validate_tokens(tokens) -> list[int]`, `rollback(num_tokens)`,
`fill_bitmask(bitmask, batch_index)`, `is_terminated()`, `reset()`.
Request types: `JSON`, `JSON_OBJECT`, `REGEX`, `CHOICE` and
`STRUCTURAL_TAG` (the accepted subset, Finding 3) are implemented;
`GRAMMAR` and structural-tag constructs outside the subset are refused
explicitly (never a fallback). The `request_id` keyword and the
`accepts_request_id` backend attribute are the patch's second addition:
the manager passes the id so the backend trace can correlate events.
The traffic between the id forms is explicit: the serving layer builds
the exposed id from `X-Request-Id`, `InputProcessor.assign_request_id`
keeps it in `external_req_id` and appends eight random characters to the
internal `request_id`, the request.py hunk carries `external_req_id` onto
the engine Request, and the manager passes both to `compile_grammar`;
the trace is keyed by the exposed id (the response id the collector
observes) and records the internal one on every event.

## Finding 2: how a tool schema reaches the backend

With a parser that declares a structural tag model (the `hermes` parser
does, `vllm/tool_parsers/hermes_tool_parser.py`) and the default
`VLLM_ENFORCE_STRICT_TOOL_CALLING=True` (`vllm/envs.py`):

| `tool_choice` | Tool `strict` / `--tool-strict-level` | Constraint sent to the backend |
|---|---|---|
| `none` | any | none |
| `auto` | no tool strict, level `auto` | none (`resolve_tool_strictness` returns None, `vllm/tool_parsers/structural_tag_registry.py`) |
| `auto` | a strict tool, or level `function`/`parameter` | `STRUCTURAL_TAG`: free text with `TriggeredTagsFormat(triggers=["<tool_call>"], tags=...)`; each tag is `begin` = `<tool_call>\n{"name": "<tool>", "arguments": ` (or without the newline), `content` = `JSONSchemaFormat(parameters)` for strict tools and the schema `true` (any JSON value) otherwise (`get_function_parameters`), `end` = `}\n</tool_call>` |
| `required` | any | `STRUCTURAL_TAG`: `TagsWithSeparatorFormat(at_least_one=True)` of the same tags |
| named tool | any | `STRUCTURAL_TAG`: the same with `stop_after_first=True` |

For parsers without a structural tag model, `required` and a named tool
fall back to a `JSON` request (`adjust_request`,
`vllm/tool_parsers/abstract_tool_parser.py`; schema from
`get_json_schema_from_tools`): a JSON array of `{name, parameters}`
objects, or the named tool's parameters.

Consequence: a `tools` field alone constrains nothing; the agent's own
`auto` constrains nothing unless the server raises the strict level or
the tools carry `strict: true`. The modes of the experiment must use one
server strict level and parser, recorded in the manifest, and the
qualification must show which of the modes actually reaches the backend
(`benchmarks/agent_eval/qualify_stack.py` sends the matrix and records
the answers and traces).

## Finding 3: the structural-tag contract, as implemented

Bolorgir has JSON Schema, literal sets and regex; the agent path needs
the `STRUCTURAL_TAG` shape: free text until a trigger, then a tagged
JSON segment, then free text again. The kind is now implemented in the
kernel (`BLG_CONSTRAINT_STRUCTURAL_TAG = 3`, additive; src/structtag.zig,
ADR-0016) with the following **accepted subset** — the shapes the pinned
`hermes` builder produces plus their legacy equivalents:

- `{"type": "structural_tag", "format": {...}}` with
  `triggered_tags` (free text with triggers; optional `at_least_one` and
  `stop_after_first`) or `tags_with_separator` (adjacent tags; the empty
  separator only; `at_least_one`, `stop_after_first`) or `any_text`;
- the legacy `{structures: [{begin, schema, end}], triggers: [...]}``
  shape;
- each tag is `{type: "tag", begin, content, end}` with `content` of
  type `json_schema` (or the raw-vLLM `schema` alias); the schema is a
  JSON Schema document compiled with the spec-v1 profile and the compact
  serialization (a boolean `true` means any JSON value);
- the pinned builder's serialization fields are tolerated on the same
  objects: every tag content may carry `style` (`null` or `"json"`;
  other styles are a different content language and are refused),
  `any_order` (`false`; `true` is refused) and `max_whitespace_cnt`
  (`null`; numeric caps are refused); `triggered_tags` and `any_text`
  may carry an `excludes` list (empty or `null`; a non-empty list would
  carve literals out of the free text and is refused); `any_text` may
  carry the null `max_tokens`/`max_chars` caps (numeric caps are
  refused).

Anything else — unknown fields, other format or content types (for
example `sequence`, per-tag `trigger`, a non-JSON content style), a
non-empty separator, overlapping triggers, overlapping begin literals,
non-empty byte requirements outside the caps — is refused with
`BLG_ERR_UNSUPPORTED_FEATURE` and the offset of the offending field,
never reinterpreted. The caps: 16 tag variants, 4 triggers (≤ 64 bytes,
non-overlapping), begin ≤ 512 bytes, end ≤ 63 bytes, a byte-complete
vocabulary (like the regex constraint, ADR-0015) and a schema resource
budget shared with the JSON kind.

Contract semantics (byte-exact, defined by this ADR for the subset):

- **Free text and triggers**: the text state carries one shift-and
  prefix mask per trigger, so a token that crosses a trigger boundary is
  handled byte by byte; a complete trigger commits to the tag (the
  trigger is matched as text, never by searching the decoded output).
- **Begin literal**: after a commit, the begin literals of the variants
  sharing the trigger are matched byte-exactly; a completion enters the
  content. Begin literals must be prefix-free (checked at compile); for
  the hermes shapes the distinct names end at the `"` after the name,
  so at most one chain is alive past a commit and one JSON state per
  session suffices.
- **Content**: the tool's parameters schema compiled by the JSON
  machinery (spec-v1, compact); the tag cannot end while the value is
  open, and a byte outside the value is rejected with the session state
  unchanged. A byte-legal but uncompletable prefix (a dead end, `-`
  under `minimum: 10` or `9` under the same bound) is refused exactly as
  in the standalone JSON constraint: the completion search of ADR-0005
  runs over the content state, so the mask withholds such bytes, the
  draft check cuts there and accept refuses them with the state
  unchanged. The filter covers the open content only: a partially
  matched end literal is alive regardless of the JSON continuation - the
  digit starting an end like `99` after `<c>42` is a dead-end JSON
  prefix (`429`), but the literal completes mechanically and the tag
  closes with the content state of the spawn point (`42`), so mask,
  draft check and accept take it (the ABI test feeds the same bytes as
  two tokens and as one, with rollback in between).
- **End literal**: a suffix-position set is spawned at every byte
  boundary where the value can complete, so a token crossing the
  content/end boundary is handled exactly; the tag completes when the set
  reaches the end literal's length.
- **Ending**: free text can end at any point (`auto`, `at_least_one`
  false); `at_least_one` forbids EOS until one tag completed;
  `tags_with_separator` requires the first tag before any end;
  `stop_after_first` closes the language after the first tag (the named
  tool path); tags may repeat when the format allows it. An unfinished
  begin cannot end: the first byte that advances any begin literal
  (including a partial begin of the next tag after a completed one, and
  the first optional tag with `at_least_one: false`) clears the endable
  flag, so mask, draft check, accept and rollback agree (the pinned
  xgrammar 0.2.7 blocks EOS at every partial begin of
  `tags_with_separator`). In triggered mode a partial *trigger* stays
  free text and may end - the pinned xgrammar allows the free-text tail
  to complete there - only a committed tag blocks EOS.
- **Session state**: segment, trigger masks, begin positions and the
  inner JSON state live in the kernel session, so rollback (ADR-0014)
  covers every segment boundary and `blg_validate_tokens` answers draft
  checks across them; a rejected token is fed through scratch slots and
  leaves the session byte-identical. `blg_session_struct_segment`
  answers the active phase (free text / begin / content / closed) and tag
  variant for diagnostics; the variant is 0xFF while free text or a
  begin prefix shared by several variants is in flight, so the backend
  trace never attributes shared framing to one tool.

The serialization risk recorded in the first revision is closed: the
exact JSON shape of xgrammar's `StructuralTag` models was confirmed
against the real builders by the GPU stage (2026-10-12) - content
carries `style: "json"`, `any_order: false` and `max_whitespace_cnt:
null`, `triggered_tags`/`any_text` add an empty `excludes` list and
`any_text` null `max_tokens`/`max_chars` caps (the kernel accepts
exactly these defaults and refuses every other value with the field
offset; the first GPU attempts failed because the defaults were being
refused, see the matrix row and the CHANGELOG). The subset stays pinned
to the constructors the vLLM 0.31.0 `hermes` registry uses
(`TriggeredTagsFormat`, `TagsWithSeparatorFormat`, `TagFormat`,
`JSONSchemaFormat`, `AnyTextFormat`); a serialization shape outside it
is refused explicitly.

## Finding 4: other differences, as implemented

- Whitespace: Bolorgir produces compact JSON only; xgrammar allows
  whitespace unless `disable_any_whitespace=True` (xgrammar and guidance
  only). The comparison run must use `disable_any_whitespace=True` so
  both backends constrain the same language; the backend refuses
  `whitespace_pattern`.
- Stop tokens: `compile_grammar` receives the request's stop token ids.
  The adapter masks them until the language can complete, then offers
  exactly that set as the terminal tokens (the set replaces the
  tokenizer EOS ids, like the reference backends' override semantics);
  an empty or absent set keeps the tokenizer EOS ids.
- Termination: the grammar is terminated after it accepts a terminal
  token; later tokens are ignored, and `rollback` of the terminal
  re-opens generation, like the reference backends.
- Speculative decoding: `rollback` and `validate_tokens` map to
  `blg_session_rollback` and `blg_validate_tokens` (ADR-0014); the
  session depth is the speculative token count plus one.
- Bitmask: the int32 `[batch, ceil(vocab/32)]` row with bit `t % 32` of
  word `t / 32` matches `blg_fill_mask`; rows are written zero-copy into
  the shared tensor with the GIL released. Vocabulary positions above the
  tokenizer table are never offered under a constraint.
- Failures: a failing mask fill or a rejected accepted token fails only
  that request via `accept_tokens -> False` (vLLM terminates it with an
  error); no unconstrained generation is produced and other requests of
  the batch continue. No exception escapes `fill_bitmask` or
  `validate_tokens`.

## Decision: split between Zig, the C ABI and Python

| Mechanism | Place | State |
|---|---|---|
| Schema, regex and structural-tag compilation, masks, reachability, segment transitions | Zig kernel | grammar artifacts, cached by the kernel per compile identity |
| Per-request session, rollback history, draft validation, termination flag, memory and work budgets | Zig kernel, C ABI | `blg_session`; the adapter holds one per request; structural-tag slots included |
| Tokenizer extraction (exact byte table) and fingerprinting | Python adapter | `TokenizerBundle.from_hf`, cached per process |
| Request mapping, terminals, tensor rows, failure policy, trace emission | Python adapter | `python/bolorgir/vllm.py`; no algorithm re-implemented |
| Registration of the backend name, request-id plumbing | upstream patch | `integrations/vllm/` |

## Matrix: verified / explicitly refused / not verified (2026-10-11; GPU stage 2026-10-12)

| Row | State | Evidence |
|---|---|---|
| Backend registration patch applies to the pinned tag | verified in-repo | `git apply --check` in `tests/test_vllm_backend.py` (with `BLG_VLLM_SRC`); patch sha256 recorded |
| Adapter member set matches the pinned interface source | verified in-repo | same test, parsed from `backend_types.py` |
| JSON, JSON_OBJECT, CHOICE, REGEX compile and mask | verified on CPU | `tests/test_vllm_backend.py` on a byte-complete vocabulary and on the pinned model tokenizer |
| Mask row layout, zero-copy fill, vocabulary padding | verified on CPU | same tests (numpy and a real torch tensor) |
| Stop/EOS terminal semantics, termination, rollback of the terminal | verified on CPU | same tests |
| Rollback and draft validation under a speculative depth | verified on CPU | same tests (ADR-0014 session machinery) |
| Structural tags of the accepted subset (hermes shapes, required/named, any_text) | verified on CPU and on GPU | `tests/test_vllm_backend.py`, `tests/test_session_rollback.py` (both backends) and the ABI test in `src/c_api.zig`; the GPU stage ran the hermes forced-tool matrix (`required`/`named`/strict `auto`) constrained in both speculative configurations (local run logs under `benchmarks/local/t10-gpu-1/`, not tracked) |
| Structural-tag constructs outside the subset | explicitly refused | compile refusals with the field offset; Zig unit table and adapter tests |
| Partial begin / partial trigger endability (pinned xgrammar parity: separator begins block EOS, triggered free-text tails do not) | verified on CPU | Zig unit tests, ABI tests and `tests/test_session_rollback.py` (both backends) |
| Nested content dead-end prefixes (the ADR-0005 completion filter, same as the standalone JSON constraint) | verified on CPU | `src/c_api.zig` ABI tests, `tests/test_session_rollback.py` (both backends) and the adapter tests |
| End-literal prefix vs the JSON filter (the same closing bytes as one token or two, with rollback) | verified on CPU | the ABI test in `src/c_api.zig` and `tests/test_session_rollback.py` (both backends) |
| Active-segment diagnostics (`blg_session_struct_segment`) | verified on CPU | ABI tests, `tests/test_session_rollback.py` (both backends), the trace tests of `tests/test_vllm_backend.py` |
| Failure policy: request-local error, no unconstrained step | verified on CPU; end to end on GPU | same tests (closed-session simulation); the round-7 limit runs die request-locally during generation (work budget) and at the session allocation (memory budget) while the neighbour answers 200 |
| Controlled resource-limit failures during the request (work budget inside the mask fill, session budget at the allocation) | verified on GPU | `benchmarks/local/t10-gpu-1/limits_checks.py` on servers started with `BLG_WORK_LIMIT_OPS=1110` / `BLG_SESSION_LIMIT_MB=1`, both speculative configurations: the JSON string request dies at its first fill (`mask computation limit exceeded`, HTTP 500, trace finish `error`), the forced-tool request at the session allocation (`session allocation failed`, HTTP 500, no trace events), the regex neighbour answers 200 before and after; the thresholds were measured by `calibrate_limits.py` on the pinned tokenizer (fill minima 256–297 ops for the neighbours, 1111 for the string schema); the round-7b re-run adds the concurrent isolation (scenario 5b, 10/10 per mode): with a healthy regex stream in flight (10 chunks received in the nospec run, 6 under ngram spec), the second request dies inside its fill (`mask_error_log_delta=1` in the log), the stream continues after the failure (117 more chunks nospec, 27 spec), finishes `stop` with a valid `[0-9]{128}` and both sessions release (`limits-{nospec,spec}-run5b.json`) |
| Session release after success, error and abort (engine statistics) | verified on GPU | `BLG_AGENT_STATS` snapshots of the limits runs: a `grammar_gc` per request id returns the session category to the baseline; the newest request's session is the only residual, freed when the next traffic advances; the aborted stream leaves no `finish` |
| Batch row reorder: request-to-row mapping and an applied-mask audit | verified on GPU | the extra scenario: the long request's tensor row moves `[1] → [0]` (draft rows visible under spec) when the short neighbour completes mid-generation; “before/after” is read from the positions of the events in the shared trace JSONL (per-request `seq` counters restart at zero), and a negative control (`rows_unchanged`, `increase_around_finish`, `decrease_before_finish`, `no_after_side`) must pass; `replay_masks.py` re-derives every mask of both requests from the traced accept/rollback history (including the vLLM stop set `{151643, 151645}`) and matches digest and allowed count (2/2 and 25/25 in both configurations); the saved traces were re-evaluated without new traffic in round 7b (`reorder-nospec-run5b.json`, `reorder-spec-run5b.json`: `a_finish_pos` 1334/1026, `rows_before=[1,1]` → `rows_after=[0,…]`) |
| Forbidden high-score candidate: concrete token id, advantage before the mask, exclusion after it | verified on GPU | `forbid_probe.py` in both configurations: the baseline greedy top-1 is the forbidden `2` (token id 17 for `v202`, advantage 5.375 nats over the best allowed candidate in the nospec run, 5.25 under ngram spec), the constrained request (`regex v20[01]`) samples `v201`, the forbidden token carries the masked-out sentinel (logprob −9999) and the text matches the regex |
| Evidence bound to the frozen candidate (run5 manifests, live EngineCore maps) | verified on GPU | `stack-manifest-{nospec,spec}-run5.json` and `stack-manifest-limits-{nospec,spec}-run5.json` (plus the round-7b limits re-run manifests `stack-manifest-limits-{nospec,spec}-run5b.json`) name core `9a5baf3e…`; live `/proc/<pid>/maps` records of the EngineCore process show `libbolorgir.so.0.1.0 sha256=9a5baf3e…`; the historical manifests are kept alongside |
| Compile-level refusals answer as request errors (HTTP 400 with the kernel's message and offset) | verified on CPU; end to end on GPU | the adapter compiles the refusing candidates (an unsupported construct, a bounded-work pattern, an external `$ref`) against a byte-complete placeholder engine (`tests/test_vllm_backend.py`); found by the GPU stage, where the refusals had surfaced as engine internal errors |
| `GRAMMAR` and `whitespace_pattern` | explicitly refused | request validation and compile refusal, with tests |
| Unsupported tokenizer family | explicitly refused at start | error names the family and the supported ones |
| Backend trace (`blg-agent-trace/2`) | implemented; qualification rules checked in-repo | events written when `BLG_AGENT_TRACE` is set, keyed by the exposed request id with the internal one recorded; per-segment attribution, rollback events and a deferred `finish` (vLLM rolls back draft-accepted terminals in the same step); the collector splits a request id into one stream per response variant (`{index}_{internal}`) and replays the accept/rollback history, so an inflated depth or a contradictory `terminal_undone` reads `unknown`; `tests/test_vllm_backend.py` (including an n = 2 pair of children) validates the traces with the A1 collector; the GPU runs qualify both configurations (the deferred `finish` of an idle server's last request is confirmed by a sentinel request); every `mask` event records the filled tensor row (`row`), which the round-7 reorder audit replays |
| Server start and the OpenAI request matrix on GPU | verified on GPU | the full matrix answered HTTP 200 in the nospec and ngram-spec runs on the pinned stack; forced tool modes and `response_format` qualify as `constrained`, non-strict `auto`/`none` stay `unknown` by design (vLLM does not call the backend there); local run logs under `benchmarks/local/t10-gpu-1/`, not tracked |
| Speculative decoding on/off on GPU, mask audit in the server | verified on GPU | both configurations ran the matrix, the extra scenarios (25/25 per run: streams, length cut, aborted stream with the engine alive, n = 2 with two trace streams, the batch row reorder with the applied-mask audit, the forbidden-candidate probe, refusals as 400) and the controlled limit scenarios (10/10 per run in the round-7b re-runs; 6/6 before the concurrent-isolation scenario existed); the spec runs use ngram with five draft tokens (V1 model runner) |
| Agent path (`auto` with a strict tool) end to end | verified on GPU | the strict `auto` row qualifies `constrained` 3/3 and its independent probe reports the mask was followed (`probe_followed` 3/3) in both configurations |

A skip in these checks means "not verified", never "passed". The GPU
stage of T10 ran the runtime scenarios on the pinned stack on
2026-10-12 (both speculative configurations; local run logs under
`benchmarks/local/t10-gpu-1/`, not tracked); the round-6 review findings
were closed on the same day by the round-7 runs row by row (controlled
resource limits with the release audit, the forbidden-candidate probe,
the batch row reorder with the mask replay, and the run5 manifests with
the EngineCore maps records), and two intermediate-review follow-ups
(concurrent resource-limit isolation, positional reorder analyzer with
the recomputed reorder evidence) closed by the round-7b re-runs. Full
pytest runs are regression checks and
do not replace a fresh T3/R6 acceptance of the current core (for a
stable release, an adopted T8a as well). The comparison report with
XGrammar (TTFT, throughput, batch scales) remains outstanding.
