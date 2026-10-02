# ADR-0007 re-measurement on the fix3-final build (2026-09-30)

Scope: performance re-acceptance of the mask fast path on the final fix3
build (strict ADR-0005 D3 + completion certificates). Raw data:
`adr0007.json`, `maskbench_tbm_p90.json`, `warm_ext_quiet.json` in this
directory. Build identity: lib sha256 `bac52820...` (the same sources were
rebuilt after removing three leftover debug tests and the unused
`synth.debugCandidates` helper; that final rebuild is identified by lib
sha256 `11352e82...` - see the fix3 report).

Methodology and thresholds are pinned in
`benchmarks/reports/20260927T203000_adr0007_fix2/REPORT.md`; only the
re-measured numbers are listed here.

## Results

- Differential gate: **PASS** (4 probe states + 425 maskbench states).
- Cold permissive p50: 4.40 ms -> **8.73 ms** (GO threshold <= 50 ms and
  stretch <= 10 ms both met).
- MaskBench TBM p90 over the pinned 24-file / 564-mask slice: 274.3 us ->
  **357.7 us** (the <= 1 ms target met).
- Warm cache-hit C-extension p50 on a quiet re-run
  (`warm_ext_quiet.json`): **1.206 / 1.297 us** (the <= 1.3 us guard met
  with a 3 ns margin on that specific measurement). The first run under
  load measured 1.313 / 1.394 us, above the guard - treat the guard as
  having no comfortable margin on this build.

The ~30-100% warm/cold delta vs fix2 is consistent with the strict-D3
certification cost but not proven to be it: a warm cache hit returns
before certification runs.
