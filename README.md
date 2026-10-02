# Bolorgir

[![CI](https://github.com/wpartscom/bolorgir/actions/workflows/ci.yml/badge.svg)](https://github.com/wpartscom/bolorgir/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

Embeddable structured-generation engine for LLMs: a small Zig core with a
versioned C ABI, Python bindings, and a Hugging Face Transformers adapter.
Given a JSON Schema (a strict Draft 2020-12 subset) or a set of literal
strings, Bolorgir computes the set of allowed next tokens at every generation
step - the model's output is guaranteed to match the requested structure,
while the choice of value stays with the model.

**Design rules:** unsupported input is rejected at compile time with a JSON
Pointer (the engine never silently continues unconstrained); mask computation
runs under hard memory budgets; tokenizer semantics are verified against the
actual backend decoder.

Typical uses: tool-call arguments, JSON extraction for programmatic
processing, classification via fixed value sets, agentic pipelines.

**Measured performance** (pinned control series, protocol v3; CPU, GPT-2
tokenizer, vocab 50,257): mask p99 **0.81 µs** vs XGrammar 0.2.6 3.04 µs
(primary scenario) and vs llguidance 1.8.0 358.68 µs (secondary holdout);
constrained generation through HF stays within **+1.7%** of XGrammar (worst
paired-median case at a 5% threshold). These are the lowest measured mask
latencies among the compared engines on these workloads - reports:
`benchmarks/reports/`, conditions and caveats: [Performance](#performance).

## Features

- **Exact or explicit.** A strict JSON Schema subset plus literal sets;
  everything outside the profile is rejected with a JSON Pointer at compile
  time. For incomplete vocabularies, completion reachability is checked
  exactly on finite languages (`src/complete.zig`); dead-end tokens are
  neither masked nor accepted.
- **Bounded.** Hard byte budgets for context, mask cache, session and schema;
  flag-based cancellation; per-call work limits; immutable compile-artifact
  cache.
- **Embeddable.** Zig core without runtime dependencies behind a versioned C
  ABI (`include/bolorgir.h`, opaque handles, `blg_status` error codes).
- **Python first-class.** CPython 3.10+ (abi3), Hugging Face Transformers
  adapter, batched mask filling, GPU mask-unpacking path.
- **Measured.** Performance claims come from a pinned acceptance protocol
  (three collections, AB/BA pairing, bootstrap CIs); reports are published in
  `benchmarks/reports/`.

## Install

From PyPI (wheels require no compiler; installing from the source
distribution requires a C toolchain, Zig is not required):

```sh
pip install bolorgir            # core engine
pip install "bolorgir[transformers]"   # + torch/transformers adapter
```

From source:

```sh
git clone https://github.com/wpartscom/bolorgir
cd bolorgir
zig build -Drelease=true        # -> zig-out/lib/libbolorgir.so
cd python && pip install .      # or: pip install ".[transformers]"
```

## Quickstart

```python
from transformers import AutoTokenizer

from bolorgir import Engine, TokenizerBundle

hf = AutoTokenizer.from_pretrained("openai-community/gpt2")
bundle = TokenizerBundle.from_hf(hf, use_cache=False)

schema = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell"]},
        "amount": {"type": "integer"},
    },
    "required": ["action", "amount"],
    "additionalProperties": False,
}

with Engine(mode="adaptive", tokenizer=bundle, memory_limit_mb=256) as engine:
    with engine.compile(schema, profile="canonical-v1") as constraint:
        with constraint.create_session() as session:
            allowed = session.allowed_token_ids()   # tokens that keep the JSON valid
            session.accept_token(allowed[0])
```

Literal choices work without a schema:

```python
with Engine(mode="lazy", tokenizer=bundle) as engine:
    with engine.compile_literals(["yes", "no", "maybe"]) as constraint:
        with constraint.create_session() as session:
            print(session.allowed_token_ids())
```

A dependency-free C example lives in `examples/c_client.c`
(`zig build example-c`).

## Comparison with alternatives

Bolorgir is an independent constrained-decoding engine (Zig core, C ABI,
Python bridge); it does not vendor or wrap the libraries below. Tables reflect
the state as of 2026-09 - check the linked projects for current data.
Measured comparison used XGrammar 0.2.6 and llguidance 1.8.0.

### Capabilities

| | **Bolorgir** (v0.1) | XGrammar | llguidance | Outlines | lm-format-enforcer |
|---|---|---|---|---|---|
| Core | Zig, C ABI, no runtime deps | C++ | Rust | Python + Rust core | Python |
| License | Apache-2.0 | Apache-2.0 | MIT | Apache-2.0 | MIT |
| Constraints | JSON Schema subset (`canonical-v1`), literal sets | JSON Schema, regex, EBNF/GBNF, Lark | JSON Schema subset, regex, Lark-like CFG | JSON/Pydantic, regex, grammars | JSON Schema, JSON mode, regex |
| Unsupported input | rejected at compile time with a JSON Pointer | - | - | - | - |
| Reachability on incomplete vocabularies | exact (finite languages) or compile-time rejection | - | - | - | - |
| Hard memory budgets | yes (published defaults) | - | - | - | - |
| Integrations | HF Transformers adapter; C ABI | vLLM, SGLang, TensorRT-LLM, MLC-LLM, WebLLM | OpenAI Structured Outputs, llama.cpp, Chromium, vLLM, SGLang | vLLM, Ollama, transformers, API providers | vLLM, TensorRT-LLM, ExLlamaV2, LangChain/LlamaIndex/Haystack |
| Platforms | Linux x86_64 (glibc); CPython 3.10+ | Linux/macOS/Windows; CPU/GPU/TPU | Linux/macOS/Windows | cross-platform | cross-platform |

## Performance

All numbers below are from the pinned control series (protocol v3, 2026-09-19,
CPU-only, single host, GPT-2 tokenizer, vocab 50,257). XGrammar/llguidance JSON
profiles differ from `canonical-v1`, so latency is compared on equivalent
schemas, not bitwise mask equality. Full reports:
`benchmarks/reports/`, methodology: `benchmarks/README.md`.

On these measurements Bolorgir has the lowest mask latency of the three
engines (both measured scenarios); end-to-end constrained generation is at
parity with XGrammar (last row).

| Metric | Bolorgir | XGrammar | llguidance |
|---|---|---|---|
| Mask build p99 (primary scenario) | 0.81 µs | 3.04 µs | - |
| Mask build p99 (secondary holdout) | 1.02 µs | - | 358.68 µs |
| First mask, cold, p95/p99 (n = 3000) | 586.5 / 684.1 µs | 965.0 / 1288.6 µs | - |
| Warm compile | ~1.0 µs | ~2.1 µs | - |
| Cold compile | ~120 µs | ~1.2 ms | - |
| GPU mask path (full) | 47.75 µs | 49.83 µs | 126.09 µs |
| Constrained generation e2e (HF, paired median) | worst case **+1.7%** vs XGrammar; 12/12 configurations within the 5% threshold | baseline | - |

Caveats: these are our workloads under our acceptance protocol - not a
cross-project benchmark suite. Competitor-reported figures may differ (for
example, llguidance documents ~50 µs per token typical for a 128k-tokenizer
JSON workload); for neutral cross-checks see
[MaskBench / JSONSchemaBench](https://github.com/guidance-ai/jsonschemabench).
Reproduce locally with `python3 benchmarks/run_all.py`.

## MVP scope and limitations

- **JSON Schema:** `type`, `properties`/`required`,
  `additionalProperties: false`, `items`, `minItems`/`maxItems`,
  `minLength`/`maxLength`, `enum`/`const`, local `$defs`/`$ref`, annotations.
  Outside the MVP (rejected explicitly): `anyOf`/`oneOf`/`allOf`/`not`/
  `if`/`then`/`else`, `pattern`, `format`, `patternProperties`, numeric bounds
  (`minimum`/`maximum`/`multipleOf`), external references, boolean schemas.
- **Regex and arbitrary CFG are not supported yet**; literal sets are.
- **Platforms:** Linux x86_64 (glibc); CPython 3.10-3.13 via abi3.
- **Tokenizers:** byte-level BPE and SentencePiece with byte fallback, with
  exact decoder chains; other chains are rejected as `UNSUPPORTED_TOKENIZER`
  rather than approximated.
- **Integrations:** Hugging Face Transformers adapter and the C ABI today;
  serving-engine wrappers (vLLM/SGLang) are not available yet.

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
