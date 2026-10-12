# vLLM integration

Bolorgir ships a structured-output backend for vLLM: the engine compiles
JSON Schema, literal-choice and regular-expression requests into the same
token masks it produces natively, and vLLM applies them to the logits of
each request. This document is the installation and usage guide; the
design decisions and the support matrix are in
[ADR-0016](../adr/ADR-0016-vllm-agent-integration.md).

> Status: the backend is implemented and functionally checked on CPU
> (request compilation, masks, terminals, rollback, structural tags,
> the backend trace), including on the tokenizer of the pinned model.
> End-to-end GPU serving checks have not run yet, and each row of the
> matrix marks "verified", "explicitly refused" or "not verified".

## Pinned stack

| Component | Pin |
|---|---|
| vLLM | tag `v0.31.0` (commit `db9527a46873454610df6dbedf79a36d6bf1a7f6`), patched |
| Python for the serving stack | as required by vLLM 0.31.x (independent of the kernel's abi3 support: the kernel and its bindings run on CPython 3.12-3.15; the GPU stack has its own requirements) |
| Model (technical checks) | `Qwen/Qwen2.5-0.5B-Instruct` |

vLLM 0.31.0 keeps its backend list closed, so the backend registers
through a small patch of the pinned tag. The patch, its sha256 and the
exact apply command live in
[`integrations/vllm/`](../../integrations/vllm/README.md). Without the
patch the server refuses the backend name at startup; there is no
runtime monkeypatch that could work (the configuration `Literal` is
checked before any plugin runs).

## Install

```bash
# 1. The patched serving engine
git clone --branch v0.31.0 --depth 1 https://github.com/vllm-project/vllm.git
cd vllm
git apply /path/to/bolorgir/integrations/vllm/vllm-0.31.0-bolorgir.patch
pip install -e .            # or the platform's build command

# 2. Bolorgir with the serving extra (from source until a package is
#    published; see the repository README for the current status)
pip install "/path/to/bolorgir/python[vllm]"

# 3. Run the server with the backend selected
vllm serve Qwen/Qwen2.5-0.5B-Instruct \
    --structured-outputs-config '{"backend": "bolorgir"}'
```

The server log prints one line when the backend is created:

```
bolorgir structured-output backend active: extension=.../bolorgir/_core.abi3.so
bolorgir=... mode=adaptive vocab=... tokenizer_vocab=... eos=(...) ...
```

That line (and the request-level errors below) is how a run shows which
backend actually applied masks; an HTTP 200 alone does not prove it.

## Requests

The backend serves the OpenAI-compatible request fields that vLLM maps
to its structured-output types:

| Request field | Backend type | Language |
|---|---|---|
| `response_format={"type": "json_schema", ...}` | `JSON` | the schema, `spec-v1` profile, `number_format=value`, compact serialization |
| `response_format={"type": "json_object"}` | `JSON_OBJECT` | any JSON object |
| `structured_outputs={"choice": [...]}` | `CHOICE` | one literal of the list |
| `structured_outputs={"regex": "..."}` | `REGEX` | full match of the pattern (ECMA-262 subset, ADR-0015) |
| `structured_outputs={"structural_tag": "..."}` | `STRUCTURAL_TAG` | tool-call framing over the accepted subset (ADR-0016): `triggered_tags` / `tags_with_separator` / `any_text` with `json_schema` tag contents, and the legacy `structures`/`triggers` shape |
| `structured_outputs={"grammar": "..."}` | `GRAMMAR` | **refused** (request error) |
| structural-tag constructs outside the subset | - | **refused** (request error naming the field); see ADR-0016 Finding 3 |
| `structured_outputs={"whitespace_pattern": ...}` | - | **refused**; the backend generates compact JSON only |

Examples:

```python
# JSON Schema: an integer inside a range and a required key
client.chat.completions.create(
    model="Qwen/Qwen2.5-0.5B-Instruct",
    messages=[{"role": "user", "content": "Return the order as JSON."}],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "order",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "minimum": 1},
                    "status": {"enum": ["new", "paid", "shipped"]},
                },
                "required": ["id", "status"],
                "additionalProperties": False,
            },
        },
    },
)

# Choice: one of two literals, exactly
client.chat.completions.create(
    model="Qwen/Qwen2.5-0.5B-Instruct",
    messages=[{"role": "system", "content": "Answer with reply or escalate."},
              {"role": "user", "content": "I forgot my password."}],
    extra_body={"structured_outputs": {"choice": ["reply", "escalate"]}},
)

# Regex: a plain-text document matching the pattern in full
client.chat.completions.create(
    model="Qwen/Qwen2.5-0.5B-Instruct",
    messages=[{"role": "user", "content": "Give me a build number like v12."}],
    extra_body={"structured_outputs": {"regex": "v[0-9]{1,3}"}},
)
```

A runnable request matrix (including the `reply`/`escalate` scenario and
an observation collector) is in
[`benchmarks/agent_eval/qualify_stack.py`](../../benchmarks/agent_eval/qualify_stack.py);
`examples/vllm_structured_outputs.py` shows the client calls with plain
`urllib`.

## Semantics and limits

- **Full match for regex**: the whole generated text must match the
  pattern (`^(?:pattern)$` over Unicode scalar values). The supported
  syntax table is section 3.1 of
  [supported_features.md](../supported_features.md); unsupported
  constructs fail the request with a byte offset, never by falling back.
- **Compact JSON**: no whitespace between tokens; trailing whitespace is
  not producible. If you need the model to emit whitespace-friendly
  output, this backend is not the one.
- **Stop tokens and EOS**: while the document cannot end, the request's
  stop ids are masked out; once it can end, the mask offers exactly that
  terminal set (the tokenizer EOS ids when vLLM passes no stops). A
  terminal token ends the constrained part; a sampled user stop still
  ends the request, and the result is validated as usual.
- **Speculative decoding**: supported through the session rollback and
  draft validation (`blg_session_rollback` / `blg_validate_tokens`,
  ADR-0014); the rollback depth is derived from the engine's speculative
configuration. Structural-tag sessions roll back across every segment
boundary (free text, begin literal, arguments value, end literal), and a
terminal token that cannot end the language cuts a draft instead of
being approved.
- **Structural tags**: the accepted subset and its byte-exact semantics
are ADR-0016 Finding 3: a full trigger commits to a tag; the begin
literal, the tool's parameters schema (spec-v1, compact) and the end
literal follow; `at_least_one` / `stop_after_first` control when the
document may end; tags repeat when the format allows it. The constraint
needs a byte-complete vocabulary, like the regex kind. Unsupported
constructs are refused with the field offset in the error message.
- **Backend trace**: with `BLG_AGENT_TRACE=<path>` the backend appends
`blg-agent-trace/2` JSON lines (start, decision, compile, mask, accept,
finish) for every request it compiles; `finish` is written when the
constrained generation ends through the backend (a terminal token or a
failure), so a request cut elsewhere leaves a visibly unfinished trace.
The qualification collector turns these events into `constrained` /
`unconstrained` / `unknown` verdicts.
- **Failures are request-local**: a failing mask computation or a
  rejected accepted token terminates exactly that request with an error;
  it never switches to another backend and never continues
  unconstrained. Other requests of the batch continue.
- **Tokenizer support**: byte-level BPE (GPT-2/Qwen/Llama-3 style) and
  SentencePiece with `byte_fallback=True`, matching the kernel's exact
  byte-table requirement. Other families (for example non-byte-level
  Mistral tokenizers) are refused when the backend starts, with the
  reason. `docs/supported_features.md` section 5 lists the tokenizer
  matrix of the kernel.
- **Vocabulary padding**: model vocabulary positions above the
  tokenizer table are never offered under a constraint.
- **One backend per server**: vLLM instantiates a single backend; the
  `--structured-outputs-config.backend` value applies to all requests.
- **Python support**: the serving stack's Python and GPU requirements
  are vLLM's; the kernel's own support statement (CPython 3.12-3.15,
  abi3) is independent and does not imply GPU-stack compatibility.

## Verifying that masks are actually applied

1. The startup log line above names the backend, the extension file and
   the tokenizer.
2. A request whose format is refused fails with the request error text
   from the backend; `GRAMMAR` and structural-tag constructs outside the
   subset are the expected refusals.
3. For a positive check, start the server with `BLG_AGENT_TRACE=<path>`
   in its environment and run the qualification collector
   (`benchmarks/agent_eval/qualify_stack.py`) with `--backend-trace
   <path>`; it records the answers and turns the backend events into a
   per-request verdict. A row is only "qualified" when the trace is
   complete and consistent (the compile of every declared constraint
   precedes its masks; a cache hit names its origin; the finish event
   counts the events before it). The trace format and its rules are in
   `benchmarks/agent_eval/PLAN.md`; traces are only as good as the run
   that produced them - an unfinished trace stays `unknown`, never
   `unconstrained`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Server start fails with `Unsupported structured output backend: bolorgir` | the vLLM source is unpatched | apply `integrations/vllm/vllm-0.31.0-bolorgir.patch` |
| `ModuleNotFoundError: bolorgir` at backend creation | Bolorgir is not installed in the server's environment | install the `[vllm]` extra into the same environment |
| Request fails with `unsupported tokenizer for the bolorgir backend` | tokenizer family outside the exact byte-table set | use an HF tokenizer of the supported families or another backend for this model |
| Request fails with `bolorgir cannot compile the REGEX request: ...` | unsupported construct or malformed pattern | see the message's byte offset and the syntax table |
| Request fails with `does not support GRAMMAR` | EBNF/Lark grammars are out of scope | use JSON, choice, json_object, regex or the structural-tag subset |
| Request fails with `UnsupportedFeatureError: unsupported structural-tag format/style/field ...` | the structural tag is outside the accepted subset | see ADR-0016 Finding 3; the error names the offset of the offending field |
