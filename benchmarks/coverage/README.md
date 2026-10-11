# Coverage reports

Full JSONSchemaBench coverage reports produced by
`benchmarks/bench_jsb_coverage.py`; the regeneration command and options
are in `benchmarks/README.md`, and each report has a summary `.md` beside
it.

## Layout

- `coverage_{maskbench,repo}.json.gz`, `*.md`, `*.passes.json.gz` - the
  first committed reports of the coverage line (superseded by `spec-v1/`).
- `spec-v1/` - the published spec-v1 reports: `coverage_*.json.gz` with
  summaries and pass sidecars; `triage.md` and `triage-evidence/` record
  the schema triage.
- `spec-v1/baseline-dbc5eeb/` - the v0.1.0 comparative baseline.
- `spec-v1/t3-6e62e0d1/` - the frozen T3/R6 release evidence for the
  0.2.0rc1 core; covered by its own `SHA256SUMS`.

`*.passes.json.gz` carries the per-pass rows; the replay tools
(`bench_jsb_coverage.load_report`, `diag_process_history.py`,
`recheck_semantic_counters.py`) read raw and gzip-compressed reports.

## Storage policy

Full reports are committed gzip-compressed. Compression is lossless:
`gzip -dc spec-v1/coverage_maskbench.json.gz | sha256sum` reproduces the
sha256 recorded in `spec-v1/t3-6e62e0d1/review*/acceptance.*` and in
`spec-v1/triage.md`. Regenerated raw `coverage_*.json` files stay
untracked (`.gitignore`).

Verify the frozen release evidence (from inside the directory):

```sh
cd spec-v1/t3-6e62e0d1 && sha256sum -c SHA256SUMS
```
