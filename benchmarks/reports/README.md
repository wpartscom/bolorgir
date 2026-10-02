# Published benchmark reports

These are the text reports of the control series referenced from the main
README. They are copies of the reports published with each run of the
acceptance protocol (methodology and reproduction commands:
`../README.md`). Raw observations (`bench_*.json`, `environment.txt`) are not
versioned; the reports quote the aggregates.

| Run | Directory | Verdict |
|---|---|---|
| Pre-series prototype | `20260914T213127/` | early measurements |
| First | `20260915T024409/` | NO-GO |
| Second | `20260917T184826_perf-fix2/` | NO-GO |
| Third | `20260917T193735_perf-fix3/` | GO |
| Fourth | `20260917T230013_perf-fix4/` | NO-GO |
| Fifth | `20260918T162851_a855338-fix/` | NO-GO |
| Sixth | `20260918T183010_a855338-fix2/` | GO |
| Seventh | `20260918T194406_7cd81c3-fix2/` | GO |
| Eighth | `20260918T212317_d84f992-fix2/` | NO-GO |
| Ninth | `20260918T222603_s1-hotpath3/` | NO-GO |
| Tenth (protocol v3) | `20260919T033356_v3-run3/` | **GO** |
| Perf baseline (spec-v1 track, ROADMAP rev 2 section 6) | `20260920T022651_perf-baseline/` | reference numbers, not an acceptance run |
| ADR-0007 acceptance, post-audit tree (final3) | `20260926T190500_adr0007_final3/` | GO (gate PASS, warm-ext 0.905/0.977 us <= 1.3 us, TBM p90 305.3 us <= 1 ms) |
| ADR-0007 acceptance, final tree (fix1, VMA-panic fix) | `20260927T115548_adr0007_fix1/` | GO (gate PASS, warm-ext 0.826/0.871 us, TBM p90 267.5 us) |
| ADR-0007 acceptance, final tree (fix2, memoPut leak fix) | `20260927T203000_adr0007_fix2/` | GO (gate PASS, warm-ext 0.898/0.946 us <= 1.3 us, TBM p90 274.3 us <= 1 ms) |

The tenth run additionally includes `tables.md` and `gate.txt`
(the acceptance gate output of the scored three-collection set).

Corpus coverage reports (compile coverage plus the R5 semantic split
metrics - session/generation/instance execution checks per schema) live
outside this directory at `../coverage/`: `../coverage/spec-v1/` for the
spec-v1 profile and `../coverage/` itself for canonical-v1, each with
`coverage_repo.{json,md}` (JSONSchemaBench snapshot) and
`coverage_maskbench.{json,md}` (MaskBench snapshot). Regenerate with
`benchmarks/bench_jsb_coverage.py --semantic` (see ROADMAP.md, "Corpus
coverage" row).

## Commit ids in older reports

Reports up to 2026-09-19 describe the project under its former working name
`zig-constraints`. The repository history was rewritten on 2026-09-19 when
the commit messages were translated to English, so the commit ids recorded
in those reports (directory names, `environment.txt`, report text) no longer
exist. The map below gives the current id of each recorded commit (oldest
first); the later rename to Bolorgir is commit `4d5e35c`.

| Recorded id | Current id | Subject |
|---|---|---|
| `1841e27` | `9c3b105` | Initial commit: zig-constraints core, C ABI, Python package, tests, benchmarks |
| `9311298` | `3730605` | lit_trie and dict-trie coverage: accept p99 2.07 -> 0.46 us, compile 1.37 -> 0.17 ms |
| `f631e68` | `ff51e72` | Do not version generated fuzzer artifacts and pytest caches |
| `46cc790` | `973f5d9` | Audit R1-R7: completion reachability, decoder-derived bytes, memory limits, cancellation, HF config, CI, GO criterion |
| `a855338` | `817c860` | Control-run verdict perf-fix4: NO-GO by the single criterion; README and manifest sync |
| `c7a5d04` | `77c2001` | Audit a855338 A1-A5: correctness (unproven pairs, decoder, single HF config), clean build, strict gate |
| `9456aaa` | `c1d1ea5` | Warm compile: immutable compile-artifact cache; e2e protocol and spike localization (audit a855338 stages 3-4) |
| `287c9cc` | `1a05a37` | Fifth control run (a855338-fix): NO-GO on one marginal case s1 b32 |
| `6fe7982` | `7c8df82` | Sixth wave (a855338 items 4-5): per-step HF processor tail, secondary holdout, deterministic tokenizer release |
| `7cd81c3` | `f721568` | Sixth control run: GO (single criterion R7+A5+a855338, thresholds unchanged) |
| `b6878be` | `f1d62a4` | Wave 7cd81c3-fix: grammar ownership (F1), exact decoder semantics (F2), honest compile-artifact cache budget (F3), C example and fuzzer (F4) |
| `d84f992` | `2040396` | Seventh control run: GO (single criterion R7+A5+a855338 + 7cd81c3 gate tightenings, thresholds unchanged) |
| `e4963ae` | `6614bd8` | Wave d84f992-fix: D1 missing decoder rejected, D3 per-source uniform validation, D6 concurrent destroy in flight, two-collection protocol (D2) |
| `b4049c1` | `30758bb` | Eighth control run (wave d84f992-fix): NO-GO - s1 b8 +6.9% > 5% |
| `49cd0a9` | `f21e9ad` | Wave s1-hotpath: HF adapter forbid-path fusion + ninth-run protocol (3 collections) |
| `e6a87b5` | `4bf4ada` | Ninth control run (wave s1-hotpath): NO-GO - s1 b8 closed, s1 b32 +5.7% and first-mask p95/p99 |
| `d6efa3c` | `b0ad006` | Protocol v3 and fixes from audit e6a87b5: gate composition/incomplete data, AB/BA, 1000 cold observations |
| `7990d3b` | `9d1dba4` | Tenth control run (protocol v3): GO - 12/12 e2e, first mask stable |
| `cd6d31c` | `06dab7c` | Fixes from audit 7990d3b: paired-record validation, safe CLI, separate corpora |
| `4d2a424` | `848f111` | Audit cd6d31c: reject NaN/non-numeric times (P2) and structural validation of nested fields (P3) |
| `56a9fff` | `8568406` | Translate acceptance gate and its tests to English |
| `8b01e87` | `3f72141` | Translate code, tests, docs and benchmarks to English; drop audit docs, rename spec to SPEC.md, clean temp artifacts |
| `3c2a9ef` | `69416fe` | Translate manifest; accept legacy v3 spec hash after spec translation |
