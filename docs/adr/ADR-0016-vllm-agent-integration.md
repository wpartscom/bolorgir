# ADR-0016: vLLM backend and agent capture, connection point (A1)

- Status: **proposed** (2026-10-11). The connection point is implemented
  and functionally checked on CPU; every runtime row of the matrix below
  is "verified in-repo", "explicitly refused" or "not verified", never
  "verified on GPU".
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
(`integrations/vllm/vllm-0.31.0-bolorgir.patch`, sha256 `38c0272a…`) that
adds the name `bolorgir` in the three places and imports the backend
class from `bolorgir.vllm`; the same change is the material for an
upstream PR. A monkeypatch from a general vLLM plugin is not equivalent:
the `Literal` is checked by pydantic before plugins could act.

Implemented interface (`python/bolorgir/vllm.py`):
`StructuredOutputBackend.compile_grammar(request_type, grammar_spec,
stop_token_ids)`, `allocate_token_bitmask(max_num_seqs)`, `destroy()`;
`StructuredOutputGrammar.accept_tokens(request_id, tokens) -> bool`,
`validate_tokens(tokens) -> list[int]`, `rollback(num_tokens)`,
`fill_bitmask(bitmask, batch_index)`, `is_terminated()`, `reset()`.
Request types: `JSON`, `JSON_OBJECT`, `REGEX`, `GRAMMAR`, `CHOICE`,
`STRUCTURAL_TAG`; the first four are implemented, `GRAMMAR` and
`STRUCTURAL_TAG` are refused explicitly (never a fallback).

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

## Finding 3: the missing contract is the structural tag

Bolorgir has JSON Schema, literal sets and regex. It has no constraint
for "free text until a trigger, then a tagged JSON segment, then free
text again" (the `STRUCTURAL_TAG` formats `TriggeredTags`,
`TagsWithSeparator`, `Tag`, `ConstString`, `AnyText`, `JSONSchema`). A
JSON-only backend serves `required`/named only for parsers without a
structural tag model, and nothing of `auto`, which is what the agent
uses. Until the kind exists, the backend refuses `STRUCTURAL_TAG` with a
clear request error and the agent path (`auto`) stays "not verified".

Required contract (input for the next specification revision, not
implemented):

- A new constraint kind compiled from the structural tag JSON subset the
  chosen parser emits (`hermes`: TriggeredTags with tags of ConstString
  begin, JSONSchema content, ConstString end; TagsWithSeparator with
  `at_least_one` and `stop_after_first`; AnyText).
- Free-text segments track the triggers with an automaton over bytes, so
  a token that crosses a trigger boundary is handled exactly; searching
  decoded text for a substring is not a mask.
- A JSON segment reuses the spec-v1 grammar of the tool's parameters
  (compiled per tool and cached); its end is the JSON value boundary
  followed by the end literal.
- EOS is allowed in free text (`auto`) and only after a completed tag
  sequence (`required`); every mask keeps the ADR-0005 reachability
  guarantee across segment changes.
- The C ABI gains the kind additively; session state (segment, trigger
  automaton state, inner JSON state) lives in the kernel, so rollback
  (ADR-0014) covers it.

Reproducer for the later integration stage: a chat request with one
`strict: true` tool and `tool_choice: "auto"` to a server with
`--tool-call-parser hermes` reaches `compile_grammar(STRUCTURAL_TAG,
...)`; until the kind exists, the backend must refuse it with an
explicit error, never serve it unconstrained. Estimate: one ADR and a
kernel change comparable to the ADR-0012 probe (new compile path,
session state, masks across segment boundaries, tests per format); the
JSON machinery is reused.

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
| Schema, regex and (later) structural-tag compilation, masks, reachability, segment transitions | Zig kernel | grammar artifacts, cached by the kernel per compile identity |
| Per-request session, rollback history, draft validation, termination flag, memory and work budgets | Zig kernel, C ABI | `blg_session`; the adapter holds one per request |
| Tokenizer extraction (exact byte table) and fingerprinting | Python adapter | `TokenizerBundle.from_hf`, cached per process |
| Request mapping, terminals, tensor rows, failure policy | Python adapter | `python/bolorgir/vllm.py`; no algorithm re-implemented |
| Registration of the backend name | upstream patch | `integrations/vllm/` |

## Matrix: verified / explicitly refused / not verified (2026-10-11)

| Row | State | Evidence |
|---|---|---|
| Backend registration patch applies to the pinned tag | verified in-repo | `git apply --check` in `tests/test_vllm_backend.py` (with `BLG_VLLM_SRC`); patch sha256 recorded |
| Adapter member set matches the pinned interface source | verified in-repo | same test, parsed from `backend_types.py` |
| JSON, JSON_OBJECT, CHOICE, REGEX compile and mask | verified on CPU | `tests/test_vllm_backend.py` on a byte-complete vocabulary and on the pinned model tokenizer |
| Mask row layout, zero-copy fill, vocabulary padding | verified on CPU | same tests (numpy and a real torch tensor) |
| Stop/EOS terminal semantics, termination, rollback of the terminal | verified on CPU | same tests |
| Rollback and draft validation under a speculative depth | verified on CPU | same tests (ADR-0014 session machinery) |
| Failure policy: request-local error, no unconstrained step | verified on CPU | same tests (closed-session simulation) |
| `GRAMMAR`, `STRUCTURAL_TAG`, `whitespace_pattern` | explicitly refused | request validation and compile refusal, with tests |
| Unsupported tokenizer family | explicitly refused at start | error names the family and the supported ones |
| Server start and the OpenAI request matrix on GPU | not verified | needs the patched stack and a GPU run |
| Speculative decoding on/off on GPU, mask audit in the server | not verified | same |
| Agent path (`auto` with a strict tool) | not verified | blocked by Finding 3 (structural tag kind) |
| Backend trace (`blg-agent-trace/2`) | not implemented | format and rules in `benchmarks/agent_eval/PLAN.md`; instrumentation is an integration-stage step |

A skip in these checks means "not verified", never "passed". The GPU
matrix and the trace instrumentation are the next steps of the agent
workstream.
