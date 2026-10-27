# Bolorgir

[![CI](https://github.com/wpartscom/bolorgir/actions/workflows/ci.yml/badge.svg)](https://github.com/wpartscom/bolorgir/actions/workflows/ci.yml)
[![Zig 0.17.0](https://img.shields.io/badge/zig-0.17.0-orange.svg)](https://ziglang.org/download/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.12-3.15](https://img.shields.io/badge/python-3.12--3.15-blue.svg)](https://www.python.org/)

> **Development line; install from source.** No package is currently
> published. The current tree includes session rollback, draft validation,
> standalone regex constraints and a vLLM structured-output backend.
> Its own R6 acceptance and full T8a MaskBench run are still pending.
> See [Preview status and known limits](#preview-status-and-known-limits)
> and [Install](#install).

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

**Measurement status:** full-corpus coverage, mask latency and end-to-end
performance have not been measured for the current baseline build. See
[Performance](#performance) for the build identity and outstanding checks.

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
- **Benchmark tools.** Pinned protocols record build identity, tokenizer,
  input streams, errors and timing distributions. Results apply only to
  the artifact and workload they measured; see [Performance](#performance).

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

## Python bindings

The Python side is an abi3 package for CPython 3.12-3.15 (Linux x86_64,
glibc 2.28 or newer) plus a Hugging Face Transformers adapter. The wheel
and the sdist ship the built core; building the extension from a
source checkout needs a C compiler and Python 3.12 or newer.

### Install

No package is currently published. Install from a checkout; the result
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
pinned upstream patch (`integrations/vllm/`) and the `[vllm]` extra.
GPU verification on the current build is pending.

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

This source tree is under development. A preview or stable release needs
its own R6 verdict, a completed and accepted T8a run, and the owner's
release decision.

- **Profiles.** `canonical-v1` is the default, frozen subset. `spec-v1` is
  experimental and selected with `profile="spec-v1"`; see
  [Schema profiles and limitations](#schema-profiles-and-limitations).
- **Acceptance.** The current tree has no R6 verdict or full T8a run of
  its own. The release manifest's final gate fields remain unset.
- **Resource limits.** A compiled schema can still fail a mask or token
  operation with `RESOURCE_LIMIT` when completion cannot be proven
  within its search or memory budget. Completion search can be expensive;
  latency on the current build has not been characterized across the
  full corpus. A token is never admitted without a proof.
- **Memory limits are not time limits.** To bound work, combine an
  application deadline with `work_limit_ops` or a `CancelToken`
  (`engine.cancel_token()`). A spent work budget fails with
  `RESOURCE_LIMIT`; cancellation fails with `CancelledError`. Cancellation
  latency on difficult schemas has not been measured for this build.
- **Sessions and constraints.** Rollback, draft-token validation,
  standalone regex and structural tags are implemented. ADR-0014 through
  ADR-0016 remain `proposed`; implementation does not close acceptance.
- **Platforms.** Linux x86_64 with glibc 2.28 or newer; CPython 3.12-3.15
  through `cp312-abi3`. Install from source. macOS, Windows and Linux
  aarch64 are not built or tested.
- **vLLM.** CPU tests cover request compilation, masks, terminals,
  rollback, failure handling and structural tags. GPU verification and
  an end-to-end comparison on the current build are pending. See the
  [integration guide](docs/integrations/vllm.md).

Problems are welcome: see [Reporting a problem](#reporting-a-problem).

## Performance

The current local baseline build uses `zig build -Drelease=true
-Dcpu=baseline`. Its core library SHA256 is:

```text
372543ce6e2e2768edf1d4edaf734958454a65ee96eb9483fb4d13e5a86522e9
```

This identifies the local baseline library, not a release wheel or a
build made with other flags. Measure and record the library actually
loaded by each benchmark.

| Check | Status for this baseline build |
|---|---|
| Full-corpus MaskBench compile coverage and outcomes | Not measured; a new T8a run is required |
| Full-corpus compile time (TTFM) and per-mask time (TBM) | Not measured |
| Warm cache latency under the acceptance protocol | Not measured |
| GPU serving latency and comparison with other engines | Not measured |
| R6 acceptance and final release gate | Pending |

No latency, throughput or coverage advantage over another engine is
claimed for this build. Regression tests establish behavior on their
inputs; they do not establish full-corpus coverage or performance.

For new measurements, pin the core and wheel hashes, tokenizer, schema
profile, serializer, corpus, limits, concurrency and machine conditions.
Publish full-corpus outcomes separately from latency on the common set
of compiled schemas. First-visit masks, warm cache hits, compilation and
end-to-end generation are separate measurements.

The [benchmark guide](benchmarks/README.md) describes the tools;
[MaskBench](benchmarks/maskbench/README.md) and
[T8a](benchmarks/maskbench/t8a/README.md) describe paired runs and evidence
requirements. Build-specific reports remain in the archive for audit and
reproduction; their results are not current-build measurements.

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
  (ADR-0011). Runtime failures must still be handled by the caller:
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

**Integer semantics.** The engine applies value-based `integer`
semantics across dialects: `1.0`, `1e2` and `100.00` are integers.
A draft-04 validator can reject these spellings under a draft-04
`$schema`. This is a compatibility limit; see
[the supported-feature notes](docs/supported_features.md#9-operational-notes-and-known-deviations)
and [spec-v1 semantics](docs/semantics-spec-v1.md).

Current-build coverage and resource-refusal counts require the new
R6/T8a runs described under [Performance](#performance).

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
zig build test --summary all      # core tests, default (Debug)
zig build test -Drelease=true --summary all  # core tests, ReleaseSafe
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
