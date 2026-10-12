# Bolorgir

[![CI](https://github.com/wpartscom/bolorgir/actions/workflows/ci.yml/badge.svg)](https://github.com/wpartscom/bolorgir/actions/workflows/ci.yml)
[![Zig 0.17.0](https://img.shields.io/badge/zig-0.17.0-orange.svg)](https://ziglang.org/download/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.12-3.15](https://img.shields.io/badge/python-3.12--3.15-blue.svg)](https://www.python.org/)

> **Development line; no package is published right now.** The earlier
> `0.2.0rc1` pre-release was withdrawn on 2026-10-11, and nothing is on
> PyPI. The `main` line below adds session rollback and draft validation
> (ADR-0014), a standalone regex constraint (ADR-0015), a vLLM
> structured-output backend with a pinned source patch
> (`docs/integrations/vllm.md`) and the Zig 0.17.0 / CPython
> 3.12-3.15 preparation. These features change the core, so the next
> public preview (planned as `0.2.0rc2`) waits for a fresh acceptance of
> the new core; the rc1 acceptance verdict names an earlier build and is
> not carried over. Read
> [Preview status and known limits](#preview-status-and-known-limits) and
> install from source as shown in [Install](#install) until then.

**Structured-generation engine written in Zig, with a C ABI and Python
bindings.** Given a JSON Schema (one of two profiles: the frozen
`canonical-v1` subset, which is the default, or the wider, experimental
`spec-v1`) or a set of literal strings, Bolorgir computes the set of
allowed next tokens at every generation step - the model's output is
guaranteed to match the requested structure, while the choice of value
stays with the model.

**Design rules:** unsupported input is rejected at compile time with a JSON
Pointer (the engine never silently continues unconstrained); mask computation
runs under hard memory budgets; tokenizer semantics are verified against the
actual backend decoder.

Typical uses: tool-call arguments, JSON extraction for programmatic
processing, classification via fixed value sets, agentic pipelines.

**Historical measurements** (profile `canonical-v1`, September 2026, not
repeated for 0.2.0rc1; every number cites its report): schema compile time
(TTFM) on the independent MaskBench suite with engine 0.1.0: p50
**207 µs** vs **1,790 µs** for llguidance
(`benchmarks/maskbench/README.md`); warm mask p99 **0.81 µs** on own
workloads (code before the 0.1.0 release, cache-hit path, GPT-2
vocabulary) vs 3.04 µs for XGrammar
(`benchmarks/reports/20260919T033356_v3-run3/REPORT.md`); end-to-end
constrained generation on par with XGrammar, worst paired-median case
**+1.7%** (same report). They say nothing about `spec-v1`, whose masks can
be far slower. Conditions and caveats: [Performance](#performance).

## Features

- **Exact or explicit.** A strict JSON Schema subset plus literal sets;
  everything outside the profile is rejected with a JSON Pointer at compile
  time. For incomplete vocabularies, completion reachability is checked
  exactly on finite languages (`src/complete.zig`); dead-end tokens are
  neither masked nor accepted.
- **Serving-ready sessions.** Rollback of accepted tokens and draft-token
  validation for speculative decoding (`Session.rollback`,
  `Session.validate_tokens`; [ADR-0014](docs/adr/ADR-0014-session-rollback.md)),
  a standalone regex constraint (`Engine.compile_regex`,
  [ADR-0015](docs/adr/ADR-0015-regex-constraint.md)) and tool-call
  framing (`Engine.compile_structural_tag`, the accepted subset of the
  vLLM structural tags, [ADR-0016](docs/adr/ADR-0016-vllm-agent-integration.md));
  all live in the same C ABI as the JSON profiles.
- **Bounded.** Hard byte budgets for context, mask cache, session and schema;
  flag-based cancellation; per-call work limits; immutable compile-artifact
  cache.
- **Embeddable.** Zig core without runtime dependencies behind a versioned C
  ABI (`include/bolorgir.h`, opaque handles, `blg_status` error codes).
- **Python first-class.** CPython 3.12-3.15 (abi3), Hugging Face Transformers
  adapter, batched mask filling, GPU mask-unpacking path.
- **Servers.** A vLLM 0.31.0 structured-output backend (JSON Schema,
  `json_object`, choice, regex and structural-tag requests) with a pinned
  source patch, an explicit-refusal policy and an optional per-request
  backend trace (`BLG_AGENT_TRACE`)
  ([docs/integrations/vllm.md](docs/integrations/vllm.md),
  [ADR-0016](docs/adr/ADR-0016-vllm-agent-integration.md)).
- **Measured.** Performance claims come from a pinned acceptance protocol
  (three collections, AB/BA pairing, bootstrap CIs); reports are published in
  `benchmarks/reports/`.

## Build the core (Zig)

The engine is a Zig library behind a versioned C ABI; the Python bindings
and the C example build on top of it. The `main` line builds with Zig
0.17.0; two independent builds with the release flags are byte-identical,
and the next acceptance will name the core it ships:

```sh
git clone https://github.com/wpartscom/bolorgir
cd bolorgir
zig build -Drelease=true -Dstrip=true -Dtarget=x86_64-linux-gnu.2.28 -Dcpu=baseline
sha256sum zig-out/lib/libbolorgir.so.0.1.0   # the next acceptance names this hash
```

The earlier `v0.1.0` tag builds with Zig 0.15.2; the withdrawn `v0.2.0rc1`
release pair is no longer published.

## Python bindings

The Python side is an abi3 package for CPython 3.12-3.15 (Linux x86_64,
glibc 2.28 or newer) plus a Hugging Face Transformers adapter. The wheel
and the sdist ship the built core; building the extension from a
source checkout needs a C compiler and Python 3.12 or newer.

### Install

No package is currently published: the `0.2.0rc1` GitHub pre-release was
withdrawn and PyPI is not used yet. Install from a checkout; the result
is a local candidate, not a published release. The build needs a C
compiler and Python 3.12 or newer; the extension is abi3
(`cp312-abi3`), so one `_core` binary loads on CPython 3.12 through
3.15.

```sh
git clone https://github.com/wpartscom/bolorgir
cd bolorgir
zig build -Drelease=true -Dstrip=true -Dtarget=x86_64-linux-gnu.2.28 -Dcpu=baseline
python3 -m venv .venv && . .venv/bin/activate
python -m pip install --upgrade pip
cd python && BLG_SKIP_ZIG=1 python -m pip install .   # takes the core from zig-out/lib
```

The Hugging Face adapter is the `transformers` extra; it installs PyTorch
from PyPI, so for a CPU-only setup install `torch` from the PyTorch CPU
index first:

```sh
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install "bolorgir[transformers]"
```

The sdist built from a checkout bundles the same core library; installing
it builds the Python extension with a C compiler and needs no Zig.

For serving, the vLLM structured-output backend is documented in
[docs/integrations/vllm.md](docs/integrations/vllm.md); it needs the
pinned upstream patch (`integrations/vllm/`) and the `[vllm]` extra, and
its GPU checks have not run yet.

### Quickstart

Without a model or any download: a byte-level vocabulary stands in for the
tokenizer and a random choice among the allowed tokens for the model
(`examples/quickstart.py`):

```python
import json
import random

from bolorgir import Engine, TokenizerBundle

# A byte-level vocabulary instead of a model's tokenizer: ids 0..255 are
# single bytes, id 256 is EOS. Nothing is downloaded.
tokens = [bytes([b]) for b in range(256)] + [b"<eos>"]
EOS = 256
bundle = TokenizerBundle.from_token_bytes(tokens, eos_ids=[EOS])

schema = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell"]},
        "amount": {"type": "integer"},
    },
    "required": ["action", "amount"],
    "additionalProperties": False,
}

rng = random.Random(7)  # stands in for the model: picks among allowed tokens
out = bytearray()
with Engine(tokenizer=bundle) as engine:
    with engine.compile(schema) as constraint:  # profile "canonical-v1" (default)
        with constraint.create_session() as session:
            while True:
                allowed = session.allowed_token_ids()  # tokens that keep the JSON valid
                if EOS in allowed and (len(allowed) == 1 or rng.random() < 0.5):
                    session.accept_token(EOS)
                    session.finish()
                    break
                token = rng.choice([t for t in allowed if t != EOS])
                session.accept_token(token)
                out += tokens[token]

doc = json.loads(out)
assert doc["action"] in ("buy", "sell") and isinstance(doc["amount"], int)
print(doc)
```

It prints `{'action': 'sell', 'amount': 36281948}`: whatever the "model"
picks, the document is valid JSON for the schema.

#### With a Hugging Face model

Needs the `transformers` extra (see [Install](#install)) and downloads
GPT-2 (about 550 MB) from the Hugging Face Hub:

```python
from transformers import AutoModelForCausalLM, AutoTokenizer

from bolorgir import Engine, TokenizerBundle
from bolorgir.transformers import constrained_generate

name = "openai-community/gpt2"
tokenizer = AutoTokenizer.from_pretrained(name)
model = AutoModelForCausalLM.from_pretrained(name)

schema = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell"]},
        "amount": {"type": "integer"},
    },
    "required": ["action", "amount"],
    "additionalProperties": False,
}

inputs = dict(tokenizer("A trade order as JSON: ", return_tensors="pt"))
with Engine(tokenizer=TokenizerBundle.from_hf(tokenizer)) as engine:
    with engine.compile(schema) as constraint:
        result = constrained_generate(model, tokenizer, constraint, inputs, max_new_tokens=48)
        new = result.sequences[0, inputs["input_ids"].shape[1]:]
        print(result.stop_reason[0], tokenizer.decode(new, skip_special_tokens=True))
```

`stop_reason` is `eos` when the model finished a valid document and
`length` when `max_new_tokens` ran out first.

Literal choices work without a schema:

```python
with Engine(mode="lazy", tokenizer=bundle) as engine:
    with engine.compile_literals(["yes", "no", "maybe"]) as constraint:
        with constraint.create_session() as session:
            print(session.allowed_token_ids())
```

A dependency-free C example lives in `examples/c_client.c`
(`zig build example-c`).

## Preview status and known limits

The `0.2.0rc1` pre-release was withdrawn on 2026-10-11 (its notes remain
as a historical document, [docs/release/v0.2.0rc1.md](docs/release/v0.2.0rc1.md));
no package is currently published. The `main` line adds session rollback
and draft validation ([ADR-0014](docs/adr/ADR-0014-session-rollback.md)),
a standalone regex constraint ([ADR-0015](docs/adr/ADR-0015-regex-constraint.md))
and tool-call framing plus the vLLM backend with its backend trace
([ADR-0016](docs/adr/ADR-0016-vllm-agent-integration.md));
the next preview (planned as `0.2.0rc2`) waits for a fresh acceptance that
names the new core. What this line has and has not passed:

- **Profiles.** `canonical-v1` is the default profile, a frozen subset.
  `spec-v1` is experimental: it covers much more of JSON Schema and is
  selected with `profile="spec-v1"`
  ([Schema profiles and limitations](#schema-profiles-and-limitations)).
- **Acceptance.** The T3/R6 verdict admitted the earlier core build
  (sha256 `6e62e0d1...`); that verdict names that build and is not carried
  over to the current one. The current candidate's functional checks are
  listed with the features above; a full T3/R6 run and the T8a MaskBench
  run are pending and are the condition for publishing `0.2.0rc2`.
- **Slow masks, `RESOURCE_LIMIT`, long runs.** On some `spec-v1` schemas
  with a large vocabulary (128k tokens) a single mask takes about a
  second, and a file of a few documents can exceed 900 s. For one MaskBench
  schema (`Github_easy---o89710`) the first mask did not return within
  120 s; with a byte-level vocabulary the same schema ends in
  `RESOURCE_LIMIT` after about 13 s on our test host. A call fails with `RESOURCE_LIMIT`
  whenever completion cannot be proven within its search or memory budget;
  a token is never admitted without a proof.
- **Session rollback overhead.** With `max_rollback_tokens=0` the accept
  path measured within +2% of the pre-history build (median +0.45%, 95%
  interval [-0.18%, +1.76%], 100 paired rounds; ADR-0014).
- **Memory limits are not time limits.** `memory_limit_mb` and the other
  budgets bound memory, not the duration of a call. To bound time, use your
  own deadline together with `work_limit_ops` (a call that spends its work
  budget fails with `RESOURCE_LIMIT`) or a `CancelToken`
  (`engine.cancel_token()`; the core checks the flag between bounded
  portions of work and the call fails with `CancelledError`). How quickly
  either stops the slow cases above was not measured for this line.
- **Performance figures** in this README are historical (0.1.0 and the
  code before it, profile `canonical-v1`) and were not re-measured for
  this line.
- **Platforms.** Linux x86_64 with glibc 2.28 or newer; the extension is
  abi3 (`cp312-abi3`) covering CPython 3.12, 3.13, 3.14 and 3.15. No wheel
  is published right now; the source install above is the supported path.
  macOS, Windows and Linux aarch64 are not built or tested.
- **vLLM.** The backend is functionally checked on CPU (request
  compilation, masks, terminals, rollback, failure policy, structural
  tags, the backend trace) including on the pinned model tokenizer;
  end-to-end GPU serving checks have not run yet. See
  [docs/integrations/vllm.md](docs/integrations/vllm.md).

Problems are welcome: see [Reporting a problem](#reporting-a-problem).

## Comparison with alternatives

Bolorgir is an independent constrained-decoding engine (Zig core, C ABI,
Python bridge); it does not vendor or wrap the libraries below. Tables reflect
the state as of 2026-09 - check the linked projects for current data.
Measured comparison used XGrammar 0.2.6 and llguidance 1.8.0.

### Capabilities

| | **Bolorgir** (0.2.0rc1) | XGrammar | llguidance | Outlines | lm-format-enforcer |
|---|---|---|---|---|---|
| Core | Zig, C ABI, no runtime deps | C++ | Rust | Python + Rust core | Python |
| License | Apache-2.0 | Apache-2.0 | MIT | Apache-2.0 | MIT |
| Constraints | JSON Schema (profiles `canonical-v1` and `spec-v1`; `spec-v1` experimental), literal sets; no standalone regex or CFG | JSON Schema, regex, EBNF/GBNF, Lark | JSON Schema subset, regex, Lark-like CFG | JSON/Pydantic, regex, grammars | JSON Schema, JSON mode, regex |
| Unsupported input | rejected at compile time with a JSON Pointer | - | - | - | - |
| Reachability on incomplete vocabularies | exact (finite languages) or compile-time rejection | - | - | - | - |
| Hard memory budgets | yes (published defaults) | - | - | - | - |
| Integrations | HF Transformers adapter; C ABI | vLLM, SGLang, TensorRT-LLM, MLC-LLM, WebLLM | OpenAI Structured Outputs, llama.cpp, Chromium, vLLM, SGLang | vLLM, Ollama, transformers, API providers | vLLM, TensorRT-LLM, ExLlamaV2, LangChain/LlamaIndex/Haystack |
| Platforms | Linux x86_64 (glibc 2.28+); CPython 3.12-3.15 | Linux/macOS/Windows; CPU/GPU/TPU | Linux/macOS/Windows | cross-platform | cross-platform |

## Performance

Two measurement series are published in this repository. Both are
historical: they measured the `canonical-v1` profile of engine 0.1.0 and of
the code just before it, and were not repeated for 0.2.0rc1; they do not
cover `spec-v1` (see
[Preview status and known limits](#preview-status-and-known-limits)).

- the pinned control series on own workloads (protocol v3, 2026-09-19,
  code before the 0.1.0 release, default profile `canonical-v1`,
  CPU-only, single host, GPT-2 tokenizer, vocab 50,257): report
  `benchmarks/reports/20260919T033356_v3-run3/REPORT.md`;
- an independent MaskBench run over the jsonschemabench corpus
  (2026-09-20, engine 0.1.0, profile `canonical-v1`, tokenizer
  `unsloth/Meta-Llama-3.1-8B-Instruct`, 128,256 tokens): report `benchmarks/maskbench/README.md`, raw aggregates
  `benchmarks/maskbench/results/`.

XGrammar/llguidance JSON profiles differ from `canonical-v1`, so latency
is compared on equivalent schemas, not bitwise mask equality. Methodology:
`benchmarks/README.md`.

### Schema compile time and mask time on MaskBench (canonical-v1)

Paired compact-serialization run over all 11,306 schemas (engine 0.1.0,
adaptive mode; llguidance 0.7.10, the MaskBench pin). All times in
microseconds. Source: `benchmarks/maskbench/README.md`, section "Results";
raw percentiles: `benchmarks/maskbench/results/blg-compact-entries.txt`
and `llg-compact-entries.txt`.

| metric | llguidance | Bolorgir |
|---|---:|---:|
| TBM avg | 94 | 143,457 |
| TBM p25 | 37 | 27 |
| TBM p50 | 55 | 125 |
| TBM p75 | 74 | 1,767 |
| TBM p90 | 133 | 609,222 |
| TBM p95 | 233 | 649,598 |
| TBM p99 | 924 | 1,393,775 |
| TBM p99.9 | 3,370 | 1,711,274 |
| TBM p100 | 22,686 | 1,992,204 |
| TTFM avg | 3,446 | 268 |
| TTFM p25 | 1,301 | 162 |
| TTFM p50 | 1,790 | 207 |
| TTFM p75 | 2,661 | 269 |
| TTFM p90 | 5,337 | 369 |
| TTFM p95 | 8,998 | 584 |
| TTFM p99 | 39,826 | 1,599 |
| TTFM p99.9 | 98,661 | 2,501 |
| TTFM p100 | 590,451 | 2,501 |
| schemas | 11,306 | 11,306 |
| passing | 4,727 | 624 |
| compile error | 2,377 | 10,682 |
| validation error | 4,202 | 0 |
| invalidation error | 0 | 0 |

Reading of the table (per `benchmarks/maskbench/README.md`):

- Compile time (TTFM): Bolorgir is faster at every percentile of this
  run (p50 207 vs 1,790 µs; p99 1,599 vs 39,826 µs). The two engines
  compile different schema sets (624 vs 8,929 files), so the rows are
  indicative, not a like-for-like intersection.
- Mask time (TBM): llguidance is faster at every TBM percentile except
  p25 (27 vs 37 µs). MaskBench opens a new session per instance and most
  files carry a single instance, so the adaptive cache almost never
  serves a repeat: a first visit of a permissive state walks the whole
  token trie (0.6 s per state with a 128k-token vocabulary in this run),
  while a cache hit is 1.3 µs.
- Semantics: 0 validation and 0 invalidation errors on the 624 schemas
  that compiled (633 valid instances accepted, 77 invalid rejected).
- Coverage under canonical-v1: 624 of 11,306 schemas compile (5.5%); the
  rest use constructs outside that profile (draft-04/06/07 `$schema`,
  `definitions`, `allOf`/`anyOf`/`oneOf`, `pattern`, boolean schemas).
  The wider `spec-v1` profile is experimental; see
  [Schema profiles and limitations](#schema-profiles-and-limitations).

### Warm masks on own workloads (control series)

Primary metric of the pinned control scenario (`benchmarks/manifest.json`,
`control_scenario`): p99 of `fill_mask` on a repeated trace, >= 10,000
warm observations per engine, so the adaptive engine serves the cache-hit
path; GPT-2 vocabulary of 50,257 tokens. Bolorgir 0.81 µs vs XGrammar
0.2.6 3.04 µs; on the secondary holdout schema 1.02 µs vs llguidance 1.8.0
358.68 µs. Cold first mask (new engine, empty cache, n = 3000): p95/p99
586.5/684.1 µs vs XGrammar 965.0/1288.6 µs. Source:
`benchmarks/reports/20260919T033356_v3-run3/REPORT.md`, sections 1 and 4.

### End-to-end generation

Constrained generation through the Hugging Face adapter is on par with
XGrammar: all 12 measured configurations stay within the 5% threshold,
worst paired-median case +1.7%. Source:
`benchmarks/reports/20260919T033356_v3-run3/REPORT.md`, section 3.

Caveats: these are our workloads under our acceptance protocol plus one
independent corpus run; competitor-reported figures may differ (for
example, llguidance documents ~50 µs per token typical for a
128k-tokenizer JSON workload). The MaskBench protocol measures
first-visit masks and does not support a "fastest mask" claim on its own
(`benchmarks/maskbench/README.md`, "Relation to the control series"). For
neutral cross-checks see
[MaskBench / JSONSchemaBench](https://github.com/guidance-ai/jsonschemabench).
Reproduce locally with `python3 benchmarks/run_all.py`.

## Schema profiles and limitations

Two JSON Schema profiles are selected at compile time (`profile=...` in
Python, `blg_compile_request.profile` in C). The normative support tables
are in `docs/supported_features.md`.

### canonical-v1 (default, frozen)

- **JSON Schema:** `type`, `properties`/`required`,
  `additionalProperties: false`, `items`, `minItems`/`maxItems`,
  `minLength`/`maxLength`, `enum`/`const`, local `$defs`/`$ref`,
  annotations. Rejected with `UNSUPPORTED_FEATURE` and a JSON Pointer:
  `anyOf`/`oneOf`/`allOf`/`not`/`if`/`then`/`else`, `pattern`, `format`,
  `patternProperties`, numeric bounds (`minimum`/`maximum`/`multipleOf`),
  external references, boolean schemas.
- Output language: compact JSON (no whitespace outside strings), object
  keys in schema declaration order (`docs/semantics.md`).

### spec-v1 (experimental)

Implements, on top of canonical-v1 (`docs/supported_features.md` section
1a; normative semantics `docs/semantics-spec-v1.md`; dialects draft-04,
draft-06, draft-07, 2019-09, 2020-12):

- `anyOf`; `oneOf`/`allOf`/`if`/`then`/`else`; `not` over a fixed
  complement subset;
- `pattern` as an ECMA-262-subset regex compiled to a DFA
  (`src/pattern.zig`); regex `patternProperties` (a single pattern);
- numeric bounds `minimum`/`maximum`/`exclusiveMinimum`/
  `exclusiveMaximum` and `multipleOf` in exact decimal arithmetic;
- type unions, open objects, `additionalProperties` as a schema,
  `propertyNames`, `minProperties`/`maxProperties`,
  `dependencies`/`dependentRequired`/`dependentSchemas`,
  `prefixItems`/tuple `items`/`additionalItems`, `contains` with
  `minContains`/`maxContains`, `uniqueItems`, structural `enum`/`const`;
- recursive local `$ref` (bounded unrolling), external `$ref` through an
  explicit registry snapshot, `$dynamicRef`/`$recursiveRef`,
  `unevaluatedProperties`/`unevaluatedItems`.

Known limitations of spec-v1 (experimental):

- **Runtime `RESOURCE_LIMIT`.** A compiled schema can still fail
  `fill_mask` or `accept_token` with `BLG_ERR_RESOURCE_LIMIT` (Python
  `ResourceLimitError`) when completion reachability cannot be proven
  within the search budget; a token is never admitted without a proof.
  Numeric conjunctions (bounds, `multipleOf`, `integer`, excluded
  constants) and numeric `oneOf`/`if` groups are certified in closed form
  (ADR-0011). The current residual is pinned row by row in
  `tests/oracle/engine_error_residual_spec_v1.json` (33 oracle rows across
  the five dialects, 10 of them on 2020-12, 2026-10-05). To handle it:
  `constrained_generate(..., on_constraint_error="fail_row")` ends only the
  affected batch row with EOS and reports the error, and
  `engine.compile(schema, profile="spec-v1", probe="probe")` searches for
  such states before generation (`docs/supported_features.md` section 1a,
  [ADR-0012](docs/adr/ADR-0012-generatability-probe.md)).
- **Compact JSON only:** no whitespace outside strings.
- **Fixed key order:** object keys follow the schema declaration order;
  the `bolorgir.serializer` helper reorders object keys of a value to
  match the schema without changing the value. Inside the branches of a
  `oneOf` with two or more live branches, `if` conditions and `contains`
  under `maxContains` the engine accepts any key order, because those
  keywords decide on the value (semantics-spec-v1 section 4.3).

### Common limitations

- **Arbitrary CFG (EBNF/GBNF/Lark) is not supported** in any profile.
  Standalone constraints are the regex kind (`Engine.compile_regex`),
  literal sets and the structural-tag subset for tool-call framing; the
  `pattern` keyword above is a JSON Schema constraint on string values
  inside a schema.
- **Platforms:** Linux x86_64 with glibc 2.28 or newer; CPython
  3.12-3.15 through one abi3 wheel. macOS, Windows and Linux aarch64 are
  not built or tested.
- **Tokenizers:** byte-level BPE and SentencePiece with byte fallback, with
  exact decoder chains; other chains are rejected as `UNSUPPORTED_TOKENIZER`
  rather than approximated.
- **Integrations:** Hugging Face Transformers adapter, the C ABI, and the
  pinned vLLM 0.31.0 structured-output backend (`[vllm]` extra plus the
  reproducible patch); SGLang is not available yet.

Details: `docs/supported_features.md`.

## Repository layout

```
build.zig                  core, tests and C example build
include/bolorgir.h         versioned C ABI (opaque handles, blg_status)
src/                       compiler, grammar IR, parser, masks, accounting
                           allocator, caches, C ABI
examples/c_client.c        C client without Python
python/                    Python package (abi3) and HF adapter
tests/                     independent reference, parity/edge/fuzz tests
docs/                      API reference, support tables, profile semantics,
                           architecture, implementation notes, ADRs
benchmarks/                corpus, measurement scripts, acceptance protocol,
                           published reports (reports/), MaskBench runs
                           (maskbench/)
```

## Development

```sh
zig build test --summary all      # core tests (Debug and ReleaseSafe)
zig fmt --check build.zig src

cd python && ZIG=$(command -v zig) python3 setup.py build_ext --inplace && cd ..
PYTHONPATH=python BLG_TEST_BACKEND=ctypes  python3 -m pytest tests/ python/tests/ -q
PYTHONPATH=python BLG_TEST_BACKEND=package python3 -m pytest tests/ python/tests/ -q
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow, and
`docs/RUN_HISTORY.md` for the acceptance history behind the current verdict
(build identities, outcomes, report links and unresolved limitations).

## Reporting a problem

Open an [issue](https://github.com/wpartscom/bolorgir/issues/new?template=bug_report.yml)
with:

1. the version (`python -c "import bolorgir; print(bolorgir.__version__)"`)
   and how it was installed: the wheel or sdist of a release, or a commit;
2. the platform: OS and version, CPU architecture, glibc and Python
   versions;
3. the schema (or literal set) and the profile, `canonical-v1` or `spec-v1`;
4. the tokenizer: model name and revision, or how the `TokenizerBundle` was
   built;
5. a minimal script that reproduces it, with the engine mode and limits;
6. the observed result: the exception or status with its message, the
   generated text, or how long a call ran before you stopped it.

Vulnerabilities go privately through GitHub Security Advisories, see
[SECURITY.md](SECURITY.md).

## Documentation

- `docs/README.md` - index of the documentation.
- `docs/API.md` - C ABI and Python API reference.
- `docs/supported_features.md` - exact support tables, limits, error codes.
- `docs/semantics.md` - normative semantics of the `canonical-v1` profile.
- `docs/semantics-spec-v1.md`, `docs/dialect-matrix.md` - the `spec-v1`
  profile and its per-dialect keyword rules.
- `docs/architecture.md`, `docs/DESIGN.md` - module architecture and
  implementation notes for contributors.
- `SPEC.md` - the full engine specification.
- `ROADMAP.md` - developer plan for full JSON Schema coverage (spec-v1 profile).
- `benchmarks/README.md` - measurement methodology and reproduction commands.
- `benchmarks/maskbench/README.md` - independent MaskBench runs and the
  upstream adapter patch.

## License

Apache-2.0 - see [LICENSE](LICENSE).

Bolorgir is an independent implementation of constrained decoding.
