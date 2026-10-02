# Technical Specification

## Adaptive structured generation engine in Zig

| Field | Value |
|---|---|
| Working name | Bolorgir; verify name availability before publication |
| Spec version | 1.0 |
| Date | September 9, 2026 |
| Status | Project for implementation and hypothesis validation |
| Core language | Zig |
| Primary scenario | Local inference and services that need responses in a given structure |
| Artifacts | Native library with C ABI, Python package, Transformers adapter, tests, benchmarks |

---

## 1. Purpose and expected result

Build a library that, at each LLM generation step, determines the set of allowed next tokens per the given constraints. The library keeps parse state between steps and updates it after a token is accepted.

For supported constraints, the completed response must match the given structure. Main applications: JSON for programmatic processing, tool-call arguments, structured data extraction, and selection from a fixed value set.

Example: for a schema with an action field from the set buy/sell and an integer amount, the engine forbids continuations that cannot end in a valid document. Choosing the semantically correct action and number remains the model's task.

Deliverable: an independent implementation of efficient constrained decoding ideas in Zig. XGrammar and llguidance serve as architectural reference points and mandatory comparison participants. Running two libraries simultaneously with intersecting masks is not the target architecture.

### 1.1. Value hypothesis

Test whether it is possible to combine:

- low preparation cost for new schemas;
- reuse of computation for repeated schemas and states;
- controlled memory consumption;
- predictable mask-build latency;
- convenient embedding via C ABI and Python.

Changing the language is not an advantage by itself. Faster overall generation, lower memory, and superiority over existing engines must be confirmed by measurements.

### 1.2. Context of existing solutions

XGrammar uses precomputed masks; XGrammar-2 also describes a cross-grammar cache, compression of repeated states, and batch APIs. Comparison must account for these capabilities, not only the early XGrammar implementation. [XGrammar-2 description](https://blog.mlc.ai/2026/05/04/xgrammar-2-fast-customizable-structured-generation).

llguidance separates lexer and parser, uses the Earley algorithm, a token byte-representation tree, and token-group optimization - slicer. These are reference points for algorithm research, not a requirement to reproduce the internal architecture verbatim. [llguidance technical description](https://guidance-ai.github.io/llguidance/llg-go-brrr).

Performance figures published by the authors are not accepted as results of the new project and are not used for speedup claims.

---

## 2. Scope of responsibility

### 2.1. Inside the library

- validation and compilation of supported constraints;
- tokenizer representation needed to check token admissibility;
- incremental parsing with per-sequence state;
- construction of the allowed-token mask;
- memory-bounded cache;
- batch interface;
- diagnostics, resource limits, C ABI, and Python binding.

### 2.2. Outside the core

- model forward pass, weights, attention, and KV-cache;
- temperature, top-k, top-p, RNG, and sampling itself;
- model training and fine-tuning;
- validation of response truthfulness and usefulness;
- arbitrary Python constraint code;
- repair of a previously produced wrong response;
- a standalone inference server.

### 2.3. CPU/GPU boundary

The core receives constraints, tokens, and state; returns a mask. The logits matrix is not transferred from GPU to CPU for the engine.

For a GPU model, the mask is transferred to the logits device and applied by the adapter. In the MVP, ordinary PyTorch operations are acceptable; custom CUDA/Triton kernels are not required. Mask unpacking, data transfer, synchronizations, and application to logits are part of integration measurements.

For a CPU model, the mask is applied on CPU. A CPU mask does not automatically mean zero-copy for GPU.

---

## 3. Terms and correctness contract

| Term | Meaning |
|---|---|
| Constraint | Schema or grammar defining the allowed response language |
| Generation profile | Additional explicit serialization rules: key order, whitespace, number representation |
| State | Data sufficient to continue parsing without re-reading the entire history |
| Mask | Bit set of allowed token IDs; a set bit means allowed |
| Cold start | First request for a schema without its prepared data in the cache |
| Warm start | Reuse of a prepared schema in the same process |
| Reference interpreter | Independent slow implementation of the semantics for validating the core |
| Completed response | Document accepted by the grammar, after which an allowed EOS is accepted |

### 3.1. Next-token admissibility

A token is allowed if, after accepting it, a finite continuation of supported tokens exists that ends in a valid completed response. Absence of an immediate syntax error is not enough: dead-end prefixes must be excluded.

The entire byte fragment of the token is checked. A token may include several punctuation marks, cross a field boundary, or contain part of a multi-byte UTF-8 character.

To support this contract, the MVP accepts only tokenizers with a verified decoding model and sufficient byte/character coverage for the target language. An unsupported tokenizer is rejected before generation. Admissibility does not account for the remaining user limit max_new_tokens: reaching this limit may interrupt a valid prefix.

### 3.2. Two mandatory properties

1. Constraint safety: the engine does not allow a continuation that violates the accepted generation language.
2. Completeness relative to that language: the engine does not forbid an allowed continuation due to heuristics or caching.

If the generation profile narrows the set of representations, this is reflected in compile metadata. Completeness relative to all possible serializations of the original JSON Schema is not claimed.

### 3.3. Completion and interruption

- EOS is allowed only in an accepting state.
- With multiple EOS tokens, the same rules apply to all registered EOS IDs.
- Timeout, cancellation, token limit, or resource limit return an incomplete-response indicator and a reason.
- A valid prefix is not passed off as complete JSON.
- A textual model refusal is allowed only if explicitly included in the constraint language.
- Grammatical correctness does not imply data validity or tool-call safety.

---

## 4. Release scope

| Capability | MVP / 1.0 | After 1.0 |
|---|---|---|
| JSON per bounded Draft 2020-12 profile | Yes, section 5 | Expanded coverage |
| List of allowed literal strings | Yes | More complex union variants |
| Internal grammar and incremental state | Yes | Additional parsing strategies |
| Lazy and adaptive modes | Yes | Scheduling with background preparation |
| Full precomputation | Experimental bounded mode | Development based on measurement results |
| Arbitrary custom CFG/EBNF | No | 1.1, separate syntax specification |
| Custom regex | No | 1.1, precisely described dialect |
| Transformers, greedy/sampling, fixed batch | Yes | Expanded compatibility |
| Beam search, fork/rollback | No | 1.1 |
| Speculative decoding | No | After correct fork/rollback |
| Mixed free text and tool calling | No | After 1.0 |
| Linux x86_64, Python 3.10+ with a regular GIL | Yes | Other platforms |
| macOS arm64, Windows x86_64, Linux aarch64 | Design without inherent x86 assumptions | Wheels after separate checks |
| Mandatory runtime dependencies on XGrammar/llguidance | No | Only explicitly selected optional adapters |

MVP is considered complete per the listed features. Roadmap rows do not turn into 1.0 acceptance conditions.

---

## 5. Supported constraints

### FR-1. JSON Schema profile

Input: UTF-8 JSON, Draft 2020-12 profile. For the MVP, the following closed list applies. Unknown validation keywords raise UnsupportedFeature with a JSON Pointer to the error location.

| Element | MVP support |
|---|---|
| type | Single string: object, array, string, integer, number, boolean, null |
| properties, required | Yes; explicit required list is mandatory, unknown names in it are rejected |
| additionalProperties | For object, explicitly false is mandatory |
| items | One supported schema for all array elements |
| minItems, maxItems | Yes, non-negative integers; min > max contradiction is an error |
| minLength, maxLength | Yes, length of the decoded value in Unicode scalar values |
| enum, const | Scalar values; constraints are checked jointly with type and length |
| $defs, $ref | Local references within the document; acyclic graph only |
| $schema | Draft 2020-12 identifier is allowed; another dialect is an error |
| title, description, $comment, examples, default | Annotations; do not affect the mask, default does not insert a value |
| anyOf, oneOf, allOf, not, if/then/else | Outside the MVP |
| minimum, maximum, multipleOf and exclusive variants | Outside the MVP; not ignored |
| pattern, format, patternProperties | Outside the MVP |
| uniqueItems, contains, dependentSchemas, unevaluatedProperties | Outside the MVP |
| External references and network schema loading | Not supported |

Every object node must have properties and additionalProperties: false. Empty properties and required: [] are allowed. Optional fields may be skipped; a key must not repeat. items is mandatory for arrays. enum/const constraints apply to JSON values, including exact number comparison without binary64 rounding.

In the MVP, every schema value must be an object with a supported type or a plain local $ref. Boolean schemas and validation keywords next to $ref are rejected. Annotations next to a reference are allowed. Absence of a length bound means no semantic upper limit; the engine resource limit remains a separate mechanism.

### FR-2. JSON serialization profile

To bound MVP complexity, the named profile canonical-v1 is used:

- object keys follow their declaration order in properties; the order is preserved when reading the schema;
- optional keys keep their relative order when other keys are skipped;
- no whitespace outside strings;
- keys and string literals are encoded with a single documented JSON escaping;
- string values are UTF-8; quote, backslash, and control characters are escaped;
- one escaping table is defined for control characters: short escapes where they exist, otherwise lowercase u00xx;
- arbitrary alternative Unicode escapes outside the table are not accepted in this profile;
- incomplete UTF-8 is allowed only as an intermediate state between tokens; completed values must contain valid Unicode scalar values;
- integer is serialized in decimal notation without fraction or exponent; number follows JSON number syntax;
- NaN and infinities are not JSON numbers and are forbidden.

This is an explicit representation restriction. For example, exponential notation of an integer value may be valid for the source JSON Schema but not for the integer language in canonical-v1. Such differences are reflected in the compiler report and tests.

For enum/const numeric values, the compiler chooses a documented exact decimal serialization; equal JSON values are normalized without loss of precision. A source schema with duplicate keys is rejected.

The exact list of allowed escapes and numeric literal normalization are specified in semantics.md before implementing the optimized core.

### FR-3. Literal alternatives

Support a separate format: a non-empty list of UTF-8 strings, exactly one of which is chosen. Support a common prefix, an empty string as one option, Unicode, and tokens spanning several characters. Do not mix this format with arbitrary regex.

### FR-4. Diagnostics and impossible constraints

The compiler returns the profile, supported features, and diagnostics. For known contradictions, UnsatisfiableConstraint is returned. Structural resource constraints must not silently change the schema.

If no tokens are allowed during generation, DeadEnd is returned with state and reason for debugging. Automatically allowing the entire vocabulary or EOS is forbidden.

---

## 6. Architecture

~~~mermaid
flowchart TD
    A[Schema and profile] --> C[Constraint compiler]
    B[Tokenizer adapter] --> T[Shared token representation]
    C --> G[Immutable grammar]
    G --> S[Session state]
    T --> M[Mask builder]
    S --> M
    K[Bounded cache] <--> M
    M --> P[Mask transfer and application]
    F[Model forward on GPU] --> P
    P --> Q[Sampling on model device]
    Q --> U[Token ID acceptance]
    U --> S
~~~

### FR-5. Compiler and internal grammar

- The schema is transformed into an immutable internal representation.
- Array and string repetition is represented by counters/repetition nodes; a large maxItems must not be expanded into a proportional number of grammar copies.
- Separate lexical element handling from structure where this reduces check cost.
- The choice of a specific parser is documented in an ADR with measurements of time, memory, and maintenance complexity; Earley is a candidate under study, not a mandatory condition.
- Prepared data of one schema is shared between sessions without copying the entire grammar.
- Schema parse errors and profile incompatibility are detected before the first forward pass.

### FR-6. Tokenizer representation

- Pass the token table and special IDs once per tokenizer context.
- For supported tokenizers, use an exact byte representation; a single decode(token_id) is not considered a universal way to obtain bytes.
- Account for byte fallback, added tokens, partial UTF-8 sequences, and special tokens.
- BOS/PAD and other service tokens are forbidden inside an active document unless they have an explicitly described role.
- Regular tokens with an empty representation are rejected during tokenizer preparation; EOS is handled separately.
- Build a compact token tree with pruning of inadmissible subtrees.
- The tokenizer identity key includes the vocabulary, byte mapping, decoder settings, and special IDs.
- The tokenizer of an existing session must not be replaced.
- In the MVP, confirm two families: byte-level BPE and a tokenizer with byte fallback. Specific public tokenizer artifacts and revisions are fixed at stage 0.
- If decoding depends on context and is not expressed by the chosen adapter, return UnsupportedTokenizer instead of applying an approximation.

### FR-7. Session state

The session stores parser state, the incomplete lexical element/UTF-8, constraint counters, and statistics. The entire response history is not re-sent or re-parsed on every step.

Mandatory operations:

1. Create a session with an empty response prefix.
2. Build the mask without changing logical state.
3. Accept the chosen token ID.
4. Check whether completion is possible.
5. Finalize, abort, or destroy the session.

Lifecycle states: active, finished, aborted. After finished/aborted, tokens must not be accepted. Repeated mask requests in the same active state return the same result. Accepting a forbidden token returns InvalidToken without changing logical state.

The prompt is not part of the generated JSON. Continuing a prefilled JSON prefix is outside the MVP; it must not be implicitly derived from the end of the prompt.

For the bounded 1.0 profile, persistent state must store sufficient counters and context without accumulating the full text of an unbounded string. The size of state and working buffers is accounted separately in the limits.

### FR-8. Mask

- Primary format: uint32 array, size ceil(vocab_size / 32).
- For token ID t, word t / 32 and bit t % 32 are used; a set bit means allowed.
- Unused bits of the last word are zero.
- Special EOS is allowed per the rule in section 3.
- The input/output buffer belongs to the caller and is reused between steps.
- First provide an exact algorithm without cache; all optimizations must produce a bitwise-identical mask.
- Do not introduce approximate filters that could allow a forbidden token or drop an allowed one.

### FR-9. Computation modes and adaptivity

Mandatory modes:

- lazy: computation from the current state with minimal preparation;
- adaptive: the same exact algorithm with bounded caching of prepared fragments/masks based on actual reuse;
- precompute: experimental preparation of selected substructures within a given budget; full enumeration of all states is not required.

In the MVP, adaptivity means choosing what to keep and reuse in the cache. Switching the parser mid-session and training a separate strategy-selection model are not required.

The policy uses access counts, measured build cost, entry size, and available memory. Thresholds are configurable and recorded in the report. The cost of maintaining statistics is included in measurements.

Each mode must have an explicit user choice. Disabling the cache changes performance but not the language or the masks.

### FR-10. Cache and memory budget

- Separate the memory of the tokenizer, compiler, grammars, sessions, temporary buffers, and cache.
- All core allocations go through an accounting allocator; reserved memory is also accounted.
- The cache has a hard budget and an eviction policy; when full, work continues via lazy if the remaining limits allow.
- The total core limit includes temporary compile data and all live context objects, not just the cache payload.
- Reaching the total limit returns ResourceLimit; the schema is not simplified and the mask is not weakened.
- Cross-grammar reuse is allowed only for provably equivalent substructures.
- The cache key accounts for the profile, grammar semantics, tokenizer, format version, and context affecting admissibility. A single last token ID is not a sufficient state key.
- A hash collision is checked by comparing identity/content, not assumed to be a match.
- Eviction does not invalidate data used by an active session.
- On-disk cache and cross-process sharing are outside the MVP.

Initial defaults, revisable per stage 0 results: total core budget 256 MiB; cache up to 64 MiB; state and working data of one session up to 8 MiB; input schema up to 1 MiB; structural depth up to 64. All limits are public and configurable; an incompatible combination is detected at context creation. If the budget is insufficient for the tokenizer, the context is not created.

### FR-11. Batch and threads

- Process an array of independent sessions in one C ABI call.
- Support different schemas within a batch.
- Completed entries are not reprocessed by the core; the adapter keeps the row-to-session mapping.
- Masks are returned in the order of the passed sessions with a separate status per row. An error in one row must not hide the status of the others.
- Sequential execution is mandatory; an internal thread pool is an optimization after profiling.
- Independent sessions allow concurrent calls. Concurrent modification of one session is forbidden by contract and blocked by the Python binding.
- The result does not depend on the number of worker threads.
- New threads must not be created per token; the thread count is explicitly bounded so as not to compete with PyTorch uncontrollably.

---

## 7. C ABI and Python

### FR-12. C ABI

Provide a versioned header with opaque handles for context, grammar, and session. Parameter structs contain size and version; numeric fields use fixed-width types, buffer sizes use size_t.

Minimum set of exported operations:

~~~text
blg_abi_version
blg_context_create / blg_context_destroy
blg_compile / blg_grammar_release
blg_session_create / blg_session_destroy
blg_fill_mask / blg_fill_masks_batch
blg_accept_token
blg_can_end / blg_finish / blg_abort
blg_get_stats
~~~

Final signatures, message encodings, and the error code table are specified in include/bolorgir.h before Python integration starts.

Ownership contract:

- the schema and configuration are copied/compiled at creation; a pointer to a temporary Python buffer is not retained;
- tokenizer data is owned by the context after preparation;
- the context must outlive its grammars and sessions; a regular attempt to destroy a busy context returns Busy;
- the output mask is written into a provided buffer of sufficient size and alignment;
- mask filling does not retain a pointer to it after return;
- on error, the mask is considered invalid and is not applied;
- regular errors are passed via code and diagnostics; they do not cross the ABI as a Zig error union or panic;
- diagnostic strings have explicitly defined ownership and lifetime; a global mutable last_error is not used.

The core validates sizes, overflows, and parameters. The C client still must pass live valid pointers: an arbitrary dangling pointer cannot be made safe by a length check. The no-crash guarantee applies to correct ABI usage and handled data errors.

### FR-13. Python package

Preferred MVP scheme: a Zig core with C ABI and a small CPython extension in C that manages Python objects and the GIL. C code does not duplicate mask-building algorithms. If another bridge is chosen, document an ADR with measured call and packing cost.

- Use the Limited API with an abi3 target for Python 3.10+ if all required functions are available; this is verified by build and tests at stage 0.
- If a Limited API limitation is found, explicitly switch to per-version Python wheels and update the release matrix. An ordinary build must not be labeled abi3.
- Release the GIL during native computation; hold objects and buffers until the call completes.
- Working buffers are closed to concurrent writes from Python; one session serializes its operations.
- ABI errors are converted into typed Python exceptions.
- The core imports without torch. Transformers integration is enabled via a separate extra.
- Context managers ensure explicit resource release; a finalizer is additional protection.

Example target user API, refined without changing semantics:

~~~python
from bolorgir import Engine
from bolorgir.transformers import constrained_generate

schema = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell"]},
        "amount": {"type": "integer"},
    },
    "required": ["action", "amount"],
    "additionalProperties": False,
}

with Engine(mode="adaptive", memory_limit_mb=256) as engine:
    constraint = engine.compile(schema, tokenizer, profile="canonical-v1")
    result = constrained_generate(
        model,
        tokenizer=tokenizer,
        constraint=constraint,
        inputs=inputs,
        max_new_tokens=128,
    )
    # result contains sequences, per-row completed flags, and stop_reason.
~~~

---

## 8. Transformers integration

### FR-14. Supported path

- Decoder-only text models, one process, one model device: CPU or one CUDA GPU.
- Greedy and sampling, num_beams=1, num_return_sequences=1; fixed batch.
- Different prompt lengths with documented left padding.
- One pre-selected Transformers/PyTorch version range, fixed at stage 0; promising compatibility with all versions is forbidden.
- Prefer public logits processors/stopping criteria. If a wrapper around generate is needed for correct finalization, use a local wrapper, not a global monkey-patch.
- State is synchronized only with actually chosen tokens. The last token and EOS must be accounted for even if generate no longer calls the processor.
- For a result cut off by the length/time limit, return completed=false for unfinished rows.
- An unsupported mode is detected before generation.

The adapter does not promise to remove completed rows from the forward batch or to save their KV-cache: that is the inference engine's responsibility.

### FR-15. Masking order and conflicts

- Constraints must be applied before top-k/top-p so that candidate selection accounts for the allowed language.
- Subsequent operations must not re-allow a forbidden token ID.
- During integration, record the actual processor/warper order in the supported HF version and cover it with tests.
- A final defensive re-mask is allowed if needed to honor the contract; its cost is accounted.
- Forced EOS at max_length, user processors, and other conflicting settings are not accepted without a separately proven compatibility scheme.
- If the intersection of constraints with other settings leaves only negative infinities or produces invalid probabilities, return an explicit error; do not sample a random token.
- If the package is missing or the mode is unsupported, generation must not silently continue without constraints.

### FR-16. Fallback

The MVP returns an explicit UnsupportedFeature/UnsupportedMode error by default. Fallback to XGrammar or llguidance may be added only as a separate, explicitly enabled feature with a verified profile match.

Fallback means running another engine while preserving the constraint contract, not disabling validation. Switching after the response has started requires state transfer/restore and is outside the MVP.

### FR-17. CPU/GPU overlap

The basic synchronous path is mandatory. Computing the next mask in parallel with the forward pass is considered after its correct implementation.

When optimizing, explicitly account for the dependency on the previously chosen token, token ID transfer to CPU, mask transfer back, GPU operation launch time, and synchronization. Do not count parallelism as achieved merely by using a separate thread.

---

## 9. Non-functional requirements

### NFR-1. Correctness and determinism

- With the same tokenizer, profile, schema, and prefix, all modes produce the same mask.
- Cache eviction, thread count changes, and session recreation do not change the allowed language.
- RNG is not part of the core. Seed matching across different sampling implementations is not its obligation.
- Schema coverage metrics are separated from speed figures: unsupported schemas do not disappear from the report.

### NFR-2. Memory and error handling

- Memory allocations are explicit and accounted; memory return is verified after session/context destruction.
- No RSS growth is required during legitimate cache filling. A plateau under fixed load and no accumulation after create/destroy cycles are verified.
- After warmup, masks of one state must not allocate new large buffers each time. Absolute zero allocations for any new grammars/states is not required.
- Input errors, limit overruns, and cancellation are handled normally; a partially accepted token is inadmissible.
- If an internal error prevents guaranteeing state integrity, the session is marked aborted and not reused.
- Compilation and mask building accept a work limit and a cancellation signal. Checks run between bounded work portions; the maximum reaction interval is measured and fixed at stage 0. Exceeding the budget returns ResourceLimit/Cancelled, not a partial mask. This is protection against excessive cost, not a promise of a hard real-time deadline.

### NFR-3. Zig, build, and platforms

- At stage 0, fix the exact stable Zig version and toolchain checksum in the manifest; a floating master is not used in the release build.
- Debug/ReleaseSafe are used in checks. ReleaseSafe is the default distribution mode.
- ReleaseFast is allowed only after comparison and re-passing the checks; disabling runtime checks is not error handling. [Zig build modes](https://ziglang.org/documentation/master/#Build-Mode).
- The scalar path is mandatory. SIMD and CPU dispatch are added based on profiling results; a wheel must not require AVX-512 or build-machine instructions without checking on the target CPU.
- The C ABI does not depend on Zig runtime types. Optional dependencies and their versions are listed explicitly.
- The Linux x86_64 wheel is tested in a clean environment without Zig installed; the minimum glibc/manylinux platform is fixed in the manifest.
- Cross-compilation does not replace running tests on the target platform.

### NFR-4. Observability

Per context and session, report: compile time, tokenizer preparation time, accept/mask time, cache hits/misses/evictions, accounted and peak memory by category, selected mode, errors, and limits.

Detailed logging is off by default. Response texts and schemas do not enter logs automatically. For discrepancy investigations, an explicit trace mode with a minimal reproducer is provided.

### NFR-5. CI

- On every PR: zig fmt check, build, unit/property/regression tests of the core and CPU integration, C ABI smoke test, and wheel.
- Extended fuzz/fault-injection and long-running checks run on schedule and before release; results are stored as artifacts.
- GPU integration is mandatory before release on a pinned GPU runner or via a reproducible manual protocol with a published result.
- Threshold speed checks run on pinned hardware. Results from ordinary shared CI are used for diagnostics but not for accepting small percentage differences.

---

## 10. Benchmarks and utility criterion

### 10.1. Environment pinning

Before optimizations, create benchmarks/manifest.json with the following data:

- CPU, cores/threads, RAM, OS and kernel version;
- GPU, VRAM, driver, CUDA, and interconnect;
- versions of Zig, Python, torch, transformers, XGrammar, and llguidance, including commit SHA when built from source;
- build flags, thread count, affinity, cache modes;
- exact model/tokenizer revisions, dtype, attention backend, KV-cache, and compile state;
- corpus revision, seed, schema configurations and their hashes;
- warmup, repeat, timeout, and memory measurement rules.

Two environments are mandatory: CPU-only for the core/adapter and GPU end-to-end on one available machine. The specific hardware is pinned before comparative results are obtained. Absence of a GPU allows completing CPU checks but not claiming GPU acceptance.

### 10.2. Comparison participants

1. Current pinned XGrammar, including available XGrammar-2 capabilities.
2. Current pinned llguidance.
3. Bolorgir lazy.
4. Bolorgir adaptive.
5. Experimental precompute, if implemented.
6. A real user Python callback, if one exists; an artificially slow callback is not considered the primary comparison base.

All comparisons run on identical tokenizers and equivalent languages/profiles. If JSON profiles differ, a common grammar is defined for the microbenchmark or an explicitly marked intersection is used. A functionality limitation must not be passed off as a speed advantage.

### 10.3. Corpus

- Full list of schemas of the selected pinned JSONSchemaBench version with a support/rejection report.
- A common supported sample from this corpus for correct comparison.
- Own MVP corpus: closed objects, optional fields, arrays, Unicode, long strings, nesting, enum common prefixes, and large alternative sets.
- A stream of different schemas; repetition of one schema; a mixed stream with a controlled repetition share.
- Hard and degenerate cases: empty allowed values, contradictions, limit boundaries, long common prefixes, and dense/sparse masks.

Split the set into a heuristic-tuning set and a holdout set. Fix the split before tuning adaptive thresholds. Use [MaskBench](https://github.com/guidance-ai/jsonschemabench/tree/main/maskbench) as a reproducible reference; published tables do not replace a local run.

### 10.4. Measurement matrix

| Case | Conditions | Metrics |
|---|---|---|
| B1. Preparation | New process; separately first tokenizer and a new schema with a ready tokenizer | import/startup, tokenizer prepare, compile, first mask, peak memory |
| B2. Warm schema | Many sessions of one schema | session create, accept, mask p50/p95/p99/max |
| B3. Schema switching | Unique and repeated schemas in a fixed order | Latency, hit rate, eviction, memory |
| B4. Batch | 1, 8, 32, 128 sessions; identical and different schemas | Whole-batch time, throughput, threads |
| B5. Budget | 64, 128, 256 MiB total limit; allowed cache sizes within it | Sustained load, ResourceLimit, peak allocations |
| B6. Tokenizers | Real pinned vocabularies; synthetic 32k/128k/256k separately | Preparation/mask time, memory |
| B7. End-to-end | One 1-3B model, batch 1/8/32 within VRAM, max_new_tokens 512 | TTFT, request time, output tokens/s, p50/p99 inter-token latency |
| B8. Long run | At least 100k total steps and 10k create/destroy cycles | Memory plateau, correctness, latency stability |

In B7, the actual length of each response is recorded; the 512 limit does not mean all responses have that length. OOM and unsupported configurations are reflected in the table. Short responses must not create an illusion of speedup: lengths, tokens/s, and request time are published together.

### 10.5. Timing methodology

- A CPU timer measures compilation and the core separately from Python/C ABI.
- GPU microbenchmarks correctly synchronize the measured operations; end-to-end does not add extra per-token synchronization just for timing.
- Publish mask build time, its transfer, application, and the full path, without summing overlapping intervals as independent latency.
- Use identical pre-defined valid token traces for mask comparison.
- For end-to-end, compare the same generation configuration and several repeats; text identity is not assumed without proven sampling parity.
- Cold start: at least 30 independent repeats of the selected cases; warm measurements - at least 10k observations per load class for p99. Class aggregation rules are published.
- Show the median across schemas and the per-token distribution separately, so long simple responses do not hide complex schemas.
- Keep raw results and a dispersion estimate; timeouts and errors are not silently excluded.

### 10.6. Performance targets

Until stage 0 is complete, absolute microseconds and 10×/20× promises are not commitments.

Main hypothesis under test: on a pre-selected control scenario, adaptive provides at least one of the advantages relative to the better of the two external engines on the respective metric:

- p95 reduction of "new schema → first mask" time by at least 20%; or
- reduction of comparable peak process memory by at least 20% at equal load and functionality; or
- p99 reduction of mask build time by at least 20%.

Additional conditions: other measured latency figures on this scenario do not degrade by more than 10% relative to the same selected competitor; all differences against the other competitor are also published. For end-to-end, absence of regression above 5% relative to the selected constrained baseline is verified accounting for dispersion. These are design decision thresholds, not a forecast of achievable speed.

At stage 0, one main scenario and one main metric for the final decision are chosen; the rest remain secondary. Selecting only the winning example after tests is forbidden.

If the advantages are not confirmed, the result "correct prototype, performance hypothesis not confirmed" is acceptable. It is not called a successful acceptance of an optimized product. Continuing for embeddability reasons is formalized as a separate goal change with its own criteria.

---

## 11. Testing

### T1. Independent reference

Develop a simple reference implementation of the MVP language, separate from the optimized core. It must not use the same cache or the same transition code as the only source of truth.

On bounded grammars and small vocabularies, perform exhaustive enumeration of allowed completions and mask comparison. On large vocabularies, check predefined traces and generated states. Separately validate final JSON documents with an independent Draft 2020-12 validator and canonical-v1 checks.

Checking only the finished JSON is insufficient: it will not detect wrongly forbidden allowed tokens.

### T2. Parity

- Lazy/adaptive/precompute: bitwise mask equality across the entire corpus.
- With cache on/off, evictions, and different thread counts, the result is the same.
- Across engines: comparison on a common precisely defined language; profile differences are first classified, then either the test contract or the implementation is fixed.
- For every discrepancy, keep the schema, profile, tokenizer revision, prefix token IDs, and differing bits.

### T3. Edge cases

Mandatory: vocab not a multiple of 32, empty batch, invalid ID, small output buffer, repeated EOS, PAD in an active session, token with several structural characters, incomplete UTF-8, escapes at token boundaries, empty string, enum with common prefixes, optional keys, number before a separator, minimum/maximum lengths, empty continuation set.

Verify equivalence of full decoding and the adapter's byte model for registered tokenizers, including added/special tokens. Verify inadmissible sequences and rejection of unsupported decoder settings.

### T4. Fuzz and fault injection

- At least 1M total fuzz iterations over input schemas, tokens, and API call sequences on valid allocated buffers.
- Separate campaigns: compiler, accept/mask, cache, C/Python boundary.
- Allocator failure injection at every checked allocation point: regular error, correct release, and no partial token acceptance.
- Negative ABI tests do not dereference arbitrary addresses from numbers; invalid sizes are tested on real existing buffers.
- Keep corpus, crash reproducers, seed, and available coverage data. The iteration count is not considered proof of absence of all bugs.

### T5. Integration

Verify CPU and CUDA, greedy/sampling, mixed batch, left padding, early EOS, length limit, cancellation, release after an error, processor conflicts, missing module, and unsupported modes.

Especially verify that the ban is not lifted by a subsequent processor and that the final token is reflected in session state. Do not require equality of ordinary generation and constrained generation: they have different allowed distributions.

### T6. Packaging

Wheel install and import in a clean environment; supported CPython versions; native core working without torch; integration extra install; C example without Python; exported ABI checks.

---

## 12. Stages and results

| Stage | Work | Mandatory result |
|---|---|---|
| 0. Research and stand | Pin the toolchain, competitor versions, hardware, and tokenizers; verify the ABI/Python bridge; measure baselines; describe semantics | manifest, semantics.md, measurement protocol, ADR for MVP boundaries and the main metric |
| 1. Exact core | Subset compiler, tokenizer adapter, independent reference, lazy, state, masks, and errors | Verifiable CPU library with C ABI |
| 2. Adaptive memory | Cache, budgets, memory accounting, substructure reuse, batch API | Parity with lazy and comparison report |
| 3. Python and Transformers | Wrapper, processor order, mask transfer, finalization, CPU/CUDA | Working examples and end-to-end results |
| 4. Utility decision | Control corpus, comparison with both competitors, regression analysis | Go/No-Go per section 10.6; limitations and reproducible data |
| 5. Release 1.0 | Long-running checks, fuzz, wheel, documentation, and ABI | Installable package in the declared matrix |
| After 1.0 | CFG/regex, other platforms, fork/rollback, speculative decoding | Separate spec and acceptance updates |

Stage 0 guidance: 5-10 working days with a stand available. The timeline of the remaining stages is estimated after it from the actual complexity of tokenizers, semantics, and parser state. A calendar deadline is not grounds for cutting correctness checks.

Go/No-Go is the technical result of hypothesis validation. No-Go does not mean the absence of useful artifacts: prototype code, tests, and measurement results are preserved.

---

## 13. Acceptance

### 13.1. MVP engineering acceptance

1. Requirements marked MVP are implemented and an exact support table is published.
2. All masks pass reference and cross-mode checks; there are no known unexplained discrepancies.
3. All responses with completed=true on the corpus are valid per the source supported schema and profile.
4. UnsupportedFeature, ResourceLimit, and interruptions are not converted into silent unconstrained generation.
5. Memory accounting covers all core allocations; configured limits and lifecycle tests are sustained.
6. Fuzz, fault injection, and integration checks are passed; found bugs are fixed or the corresponding capability is explicitly excluded from the claimed support before release.
7. C ABI, Python wheel, and adapter work in the pinned matrix; a run example without Python exists.
8. Reproducible comparisons with both external engines are published, including lost cases, limitations, and raw data.

### 13.2. Product hypothesis acceptance

The section 10.6 criterion is fulfilled separately on the pre-selected control scenario. A positive engineering acceptance result without this criterion means a correct prototype, but not a confirmed advantage of the new implementation.

---

## 14. Risks and actions

| Risk | Action |
|---|---|
| Duplicating already available XGrammar-2/llguidance optimizations | Measure pinned current versions before extending the implementation |
| Differences between JSON Schema and the generation language | Explicit canonical-v1, exception table, independent validator, and completeness tests |
| Incorrect byte representation of tokens | Certify specific adapters, reject the rest |
| Cache speeds up the average but increases latency tail or memory | Hard budget, p99/max measurement, lazy as the exact path on a miss |
| Wrong cache key across schemas | Full identity context, collision check, cross-schema tests |
| CPU speedup does not affect response time | Measure the full GPU path and actual overlap |
| Complexity of Python/GIL and ABI | Small bridge, buffer owners, lifetime and concurrency tests |
| Changes in Zig or HF internal APIs | Pinned versions, separate updates with re-verification |
| Full rewrite turns out more expensive than the useful result | Early baseline and Go/No-Go; do not extend the roadmap before value validation |

---

## 15. Deliverables

- Zig core sources, C header, and Python binding.
- Documentation: semantics.md, supported_features.md, architecture.md, and ADRs.
- Build via build.zig and a Python build backend with a pinned toolchain.
- Linux x86_64 wheel per the release matrix.
- Examples: literal choice, JSON Schema, fixed batch, C client.
- Independent reference, regression/property/fuzz tests, and corpora.
- Benchmarks manifest, reproduction commands, raw results, and final report.
- List of third-party components, their versions, and the origin of borrowed code; required notices and attribution are preserved when borrowing.
- Documented limitations and the product hypothesis decision.

## 16. Sources and reference points

Sources were verified during spec preparation on September 9, 2026. References to main reflect architectural reference points; pinned revisions are required for testing.

- [XGrammar: repository and purpose](https://github.com/mlc-ai/xgrammar).
- [XGrammar-2: caching, repetitions, and batch APIs](https://blog.mlc.ai/2026/05/04/xgrammar-2-fast-customizable-structured-generation).
- [llguidance: repository](https://github.com/guidance-ai/llguidance).
- [llguidance: mask construction algorithms](https://guidance-ai.github.io/llguidance/llg-go-brrr).
- [llguidance: JSON Schema coverage and deviations](https://github.com/guidance-ai/llguidance/blob/main/docs/json_schema.md). Used as an example of the need for an explicit profile, not as a promise of identical coverage.
- [JSONSchemaBench / MaskBench](https://github.com/guidance-ai/jsonschemabench/tree/main/maskbench).
- [Zig: documentation](https://ziglang.org/documentation/).
- [CPython: Stable ABI and Limited API](https://docs.python.org/3/c-api/stable.html).
- [Transformers: logits processors](https://github.com/huggingface/transformers/blob/main/src/transformers/generation/logits_process.py).
