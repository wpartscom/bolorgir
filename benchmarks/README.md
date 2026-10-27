# benchmarks/ - Bolorgir measurement infrastructure

These tools collect build-specific correctness, coverage and performance
evidence. The current baseline build has no full-corpus acceptance or
performance comparison; see [current status](../README.md#performance).

Pin the environment and protocol before collecting a new run. The saved
`manifest.json` belongs to the control protocol; verify its hardware,
versions and conditions before using it for a new artifact.

## Composition

```
manifest.json            machine facts: CPU/RAM/OS/GPU, versions, run rules
bench_common.py          shared utilities: SKIP guard, timers, percentiles, corpus
bench_prepare.py         B1: cold/warm compile, tokenizer prepare, first mask, peak RSS
bench_masks.py           B2: p50/p95/p99/max fill_mask and accept on a warm schema (>=10k observations)
bench_batch.py           B4: batches of 1/8/32/128 sessions
bench_memory.py          B5/B8: limits 64/128/256 MiB, RSS plateau over 10k create/destroy cycles
bench_compare_uniform.py identical load: one tokenizer/schema/trace for zig/xgrammar/llguidance
bench_coldproc.py        B1: 30 independent processes per engine
bench_e2e_constrained.py B7: GPU end-to-end zig vs constrained-baseline (xgrammar)
bench_gpu_mask_path.py   GPU mask intervals: build/H2D/apply/full
diag_proc_step.py        per-step diagnostics of HF processor phases (not a scored tool)
diag_e2e_spike.py        first-request e2e spike localization (diagnostic, not scored)
diag_cert_histogram.py   undecided-reason histogram of the strict-D3 residual
                         over oracle ENGINE_ERROR rows (diagnostic, not scored)
diag_process_history.py  repeated compiles of one schema in fresh contexts of
                         one process, with the context memory per round
                         (T3 input, diagnostic, not scored)
recheck_cert_residuals.py maintained recheck of the strict-D3 RESOURCE_LIMIT
                         residuals on MaskBench: the 26-case suite and the
                         126-instance valid-rejects suite with pinned counts
                         (see the module docstring)
bench_jsb_coverage.py    JSONSchemaBench coverage reports: compile buckets,
                         semantic checks, K-pass generatable outcomes
                         (coverage/spec-v1/)
recheck_semantic_counters.py replay of the correctness signals of a
                         coverage report on a given library (evidence rows)
recheck_invalid_documents.py the invalid generated documents of a
                         coverage report that the draft-04 deviation does not
                         explain, fed to a given library (evidence rows)
triage_semantic.py       class A-E triage of the rechecked signals and the
                         generatable outcomes (coverage/spec-v1/triage.md)
bench_longrun.py         B5/B8: fair long-run generation, ~100k steps/limit
bench_schemas_tokenizers.py B3 schema switching + B6 tokenizer matrix
summarize_control.py     control run summary + recompute of the §10.6 GO rule
run_all.py               runs all scripts, raw results in results/<timestamp>/
compare/xgrammar_runner.py    same B1/B2 via XGrammar
compare/llguidance_runner.py  same B1/B2 via llguidance
maskbench/               MaskBench adapter, upstream PR draft, full-corpus
                         results (see maskbench/README.md)
corpus/                  own MVP schema corpus + index.json
                         (including the secondary holdout secondary_geo_route for
                         secondary_guard)
external/jsonschemabench/     external JSONSchemaBench corpus (see below)
results/                 raw run results (not committed)
local/                   gitignored scratch area for one-off investigations
                         (not published)
```

## Methodology (spec §10.5)

- Timer: `time.perf_counter_ns`; RSS: `resource.getrusage(RUSAGE_SELF).ru_maxrss`.
- The CPU timer measures core calls separately from the wrapper: fill_mask and
  accept are measured over separate intervals; overlapping intervals are not summed.
- Warmup: 3 iterations before measurement (in scripts - a trace-generation run).
- Repeats: cold-start >= 30 independent repeats; warm load classes -
  >= 10k observations for p99 (`--min-observations` for mask/accept,
  `--min-warm-observations` for warm compiles). The gate checks the observation
  threshold for EVERY class whose p99 feeds the decision: mask, accept,
  warm compile - >= 10k warm; cold compile, first mask, tokenizer
  prepare - >= 30 cold; B1 - >= 30 processes. Insufficient
  observations/processes - INCONCLUSIVE, not a silent GO.
- Masks are compared on preset valid token traces:
  `bench_common.gen_trace` deterministically (seed=42) builds the trace; all
  measurements replay the same one.
- We publish p50/p95/p99/max/mean/count and peak RSS; raw observations and traces
  are saved to `results/<timestamp>/`; timeouts and errors are recorded in
  the results, never silently excluded.
- Per-schema distributions are shown separately (each schema - its own run),
  so long simple responses do not hide complex schemas.
- GPU microbenchmarks (when they appear): `torch.cuda.synchronize` around
  the measured operation; end-to-end without extra synchronization on each token.

## Reproduction

```sh
# from the repository root
python3 benchmarks/run_all.py                      # all cases, default schema
python3 benchmarks/run_all.py --schema mixed_records
python3 benchmarks/bench_prepare.py --schema big_enum_64 --repeats 30
python3 benchmarks/bench_masks.py --schema enum_common_prefixes --mode lazy
python3 benchmarks/bench_batch.py --batches 1,8,32,128
python3 benchmarks/bench_memory.py --limits-mb 64,128,256 --cycles 10000
python3 benchmarks/recheck_cert_residuals.py   # strict-D3 residual ratchet (MaskBench)
```

With no built `bolorgir` package, core scripts print
`{"status": "SKIP", ...}` and exit with code 0 - the `run_all.py` run does
not crash; the status is recorded in `results/<timestamp>/summary.json`.

## External comparisons: status

No comparison with XGrammar or llguidance has been measured for the
current baseline build. The runners in `compare/` require their optional
dependencies; record the installed versions in each run's manifest.

Use the same tokenizer and equivalent schema semantics. Publish
full-corpus refusals and errors separately from latency on the common
supported set. Bitwise mask equality is meaningful only when the token
vocabulary and accepted languages coincide.

## JSONSchemaBench

The corpus revision is pinned to
`ba103c73756198dd9b149ddc7db7867da7a077f6`. Prepare a checkout of that
revision rather than downloading the moving `main` branch:

```sh
git clone https://github.com/guidance-ai/jsonschemabench \
  benchmarks/external/jsonschemabench
git -C benchmarks/external/jsonschemabench checkout \
  ba103c73756198dd9b149ddc7db7867da7a077f6
```

For `spec-v1` coverage, the pinned HF tokenizers must be available in the
HF cache (`HF_HOME`); the harness loads them offline. Use a new work and
output directory for each build and protocol:

```sh
zig build -Drelease=true -Dcpu=baseline
BOLORGIR_COVERAGE_PROFILE=spec-v1 PYTHONPATH=python \
  python3 benchmarks/bench_jsb_coverage.py --snapshot both --semantic \
  --generatable --passes 20 --hf-sample-every 20 --jobs 8 --chunk-size 100 \
  --work-dir benchmarks/local/current-coverage-work \
  --out-dir benchmarks/local/current-coverage
```

The worker count is a protocol choice: check available resources before
starting. Every pass of every schema runs unless
`--schema-budget-seconds` caps it. `--complete-from` can collect missing
passes using the saved report; record that continuation in the run
history. See [coverage/README.md](coverage/README.md) for report layout
and integrity records.

`--jobs` resumes an interrupted run from the finished chunks in
`--work-dir`. A chunk is reused only when its identity record (engine
library, harness sources, parameters, corpus, packages) matches the run;
a run with other inputs needs a new `--work-dir`. The inputs of
`coverage/spec-v1/triage.md` are listed at its top; the class rules and
the options of `triage_semantic.py` are in its docstring. As a gate it
runs with `--gate-build final`: the final build has no unclassified row,
and an unclassified row of an earlier build passes only as an accepted
exception of `triage_manual.json`, listed at the top of `triage.md`.
Without that option the triage of the whole history exits 1 while such
an exception exists.

## Control run §10.6 / §13.2

Define the build, workloads, machine conditions, repetitions and decision
thresholds before measurement. Save raw observations and environment
records with each collection, and evaluate all required collections using
`summarize_control.py`. A diagnostic run or incomplete collection does
not close acceptance.

## ADR-0007 and full-corpus acceptance

`bench_adr0007.py` measures mask latency and validates the warm protocol.
`maskbench/t8a/matrix_adr0007.py` runs the functional matrix and benchmark
gates. The protocol and acceptance conditions are in
[ADR-0007](../docs/adr/ADR-0007-mask-fast-path.md); the paired full-corpus
procedure is in [T8a](maskbench/t8a/README.md).

Bind every result to the library actually loaded. Functional tests,
diagnostic slices, warm-cache measurements and the full T8a corpus have
different scopes and must be reported separately. The current baseline
still needs its own R6 verdict and full T8a run.

## Archived reports

[reports/](reports/README.md), `coverage/` and `maskbench/t8a/evidence/`
retain build-specific records for audit and reproduction. Their hashes,
inputs and verdicts remain part of those records; current-build results
must come from a new run bound to the current artifact.
