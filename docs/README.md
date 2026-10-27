# Bolorgir documentation

Start with the [project README](../README.md) for installation, a
quickstart and [current measurement status](../README.md#performance).
The documents below go deeper.

## Using the library

| Document | Contents |
|---|---|
| [API.md](API.md) | C ABI and Python API reference, including the Transformers adapter and the spec-v1 serializer. |
| [supported_features.md](supported_features.md) | What each profile accepts and rejects, constraint formats, modes, tokenizers, default limits, error codes, known deviations. |
| [semantics.md](semantics.md) | Normative definition of the `canonical-v1` profile (the default): the exact JSON byte language the engine generates. |
| [semantics-spec-v1.md](semantics-spec-v1.md) | Normative definition of the `spec-v1` profile (broad JSON Schema coverage): value equality and serialization policy. |
| [dialect-matrix.md](dialect-matrix.md) | How `spec-v1` treats every keyword in draft-04, draft-06, draft-07, 2019-09 and 2020-12, with the oracle test results. |
| [integrations/vllm.md](integrations/vllm.md) | vLLM structured-output backend: pinned stack, patch, requests, semantics, limits, diagnostics. |

## Working on the library

| Document | Contents |
|---|---|
| [architecture.md](architecture.md) | Modules, their boundaries, and the memory and threading models. |
| [DESIGN.md](DESIGN.md) | Implementation notes and the invariants the code relies on. |
| [mask-fast-path-needs-reachability.md](mask-fast-path-needs-reachability.md) | The mask fast path: why reachability is needed, the scope correction, the `oneOf` refinement, the diagnostics and the measurements. |
| [adr/](adr/) | Architecture decision records (listed below). |
| [RUN_HISTORY.md](RUN_HISTORY.md) | Acceptance record: measured builds, outcomes, report links and unresolved limitations. |

The repository root also holds [SPEC.md](../SPEC.md) (the engine
specification), [ROADMAP.md](../ROADMAP.md) (the spec-v1 development plan
and progress tracking), [CHANGELOG.md](../CHANGELOG.md) and
[CONTRIBUTING.md](../CONTRIBUTING.md). Measurement methodology and
benchmark tools are under [benchmarks/](../benchmarks/README.md).
`RUN_HISTORY.md`, dated reports, CHANGELOG entries and evidence packages
are archival records of the builds they name, not current-build results.

## Architecture decision records

| ADR | Decision | Status |
|---|---|---|
| [0001](adr/ADR-0001-parser-choice.md) | Parser: deterministic stack automaton with a bounded thread set | accepted |
| [0002](adr/ADR-0002-literal-trie.md) | Shared prefix trie for literal alternatives (`lit_trie`) | accepted |
| [0002](adr/ADR-0002-python-bridge.md) | Python bridge: CPython C extension on the Limited API (abi3) | accepted |
| [0003](adr/ADR-0003-completion-and-decoder.md) | Completion reachability in masks; decoder modeling | accepted |
| [0004](adr/ADR-0004-compile-artifact-cache.md) | Cache of immutable compile artifacts | accepted |
| [0005](adr/ADR-0005-completion-reachability.md) | Completion reachability for product and union states (spec-v1) | accepted |
| [0006](adr/ADR-0006-state-model.md) | State model, ownership and equality (spec-v1) | accepted |
| [0007](adr/ADR-0007-mask-fast-path.md) | Mask fast path and its differential test plan | accepted |
| [0008](adr/ADR-0008-recursion.md) | Recursive `$ref` by bounded unrolling | accepted |
| [0009](adr/ADR-0009-unevaluated.md) | `unevaluated*` by compile-time scenario synthesis | accepted |
| [0010](adr/ADR-0010-dynamic-refs.md) | `content*` annotations, URI resolution, dynamic references | accepted |
| [0011](adr/ADR-0011-numeric-conjunction.md) | Closed-form certificates for numeric conjunctions | accepted |
| [0012](adr/ADR-0012-generatability-probe.md) | Compile-time generatability probe | proposed |
| [0014](adr/ADR-0014-session-rollback.md) | Session rollback and draft-token validation | proposed |
| [0015](adr/ADR-0015-regex-constraint.md) | Standalone regex constraint | proposed |
| [0016](adr/ADR-0016-vllm-agent-integration.md) | vLLM structured-output backend and agent integration | proposed |

Two records share the number 0002; other documents cite them as
"ADR-0002 (literal trie)" and "ADR-0002 (Python bridge)", or by context.
ADR-0013 (interop numbers) and ADR-0017 (bounded whitespace) belong to a
later line and do not exist yet (`release/v0.2.0-composition.json`,
`later_line_paths`).

## Reading notes

- **Profiles.** `canonical-v1` is a strict, frozen JSON Schema subset with a
  single canonical serialization. `spec-v1` is the broad JSON Schema
  profile, selected explicitly at compile time.
- **Labels.** "FR-n", "NFR-n", "Tn" and "spec §n" refer to
  [SPEC.md](../SPEC.md). "P1"-"P6" are the spec-v1 implementation phases
  and "PA" the design phase before them, all from
  [ROADMAP.md](../ROADMAP.md). "R1"-"R4" are correctness fixes listed in the
  [CHANGELOG](../CHANGELOG.md). "D1", "D2", ... are numbered decisions inside
  an ADR (for example "ADR-0005 D3").
- **Follow-up work.** T9 (session rollback) and the vLLM backend of T10
  are implemented in this tree; the T10 agent evaluation (A1-A3) and the
  formal acceptance of those records are not done: ADR-0014 to ADR-0016
  stay `proposed`, and an implementation does not change that status.
- **Build names.** "final3", "final4", "fix1", "fix2", "fix3" (with
  sub-runs such as "fix3g") identify successive measured builds; their
  identities and outcomes are in [RUN_HISTORY.md](RUN_HISTORY.md) and
  [dialect-matrix.md section 9](dialect-matrix.md#9-oracle-coverage-results).
- **Historical revision ids.** Commit ids of preparation stages (`711ad70`,
  `1f819bc`, `c770469`, ...) identify local historical revisions and are
  kept as provenance. The public `main` history squashes each publication
  into one commit, so a clean clone cannot check out those revisions:
  reproducing them needs the saved snapshots or patches with their
  checksums, and an id alone is not a reproduction recipe.
- **Local artifacts.** Some documents cite run outputs that are not
  versioned: oracle reports under `tests/oracle/results/` and raw benchmark
  data under `benchmarks/results/`. Regenerate them with the commands in
  `tests/oracle/README.md` and `benchmarks/README.md`. Published benchmark
  reports are versioned under `benchmarks/reports/`.
