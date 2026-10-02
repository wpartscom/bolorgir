# Acceptance run v3 (tenth): protocol `acceptance_protocol_v3`, three collections - GO

Date: 2026-09-19. Code and protocol commit: `d6efa3c` (**pinned before the first
measurement**: commit `2026-09-19T02:35:11-07:00` = 09:35:11Z, first collection
started at 09:36:23Z; tree clean - `tree_status: clean` in environment.txt of
each collection). Data: three full collections of one code revision - `v3-run1`
(`20260919T023623`), `v3-run2` (`20260919T030501`), `v3-run3`
(`20260919T033356`); each has 12 `run_all` cases (first mask: **1000 cold
observations** per engine), e2e ×2 with 4 repeats in balanced AB/BA order
(**24 paired blocks** per configuration), GPU path,
`environment.txt` (commit, protocol_sha256, sources_sha256, loadavg/
MemAvailable/uptime, nvidia-smi, top processes). Combination - per
`manifest.json#control_scenario.acceptance_protocol_v3` pinned BEFORE launch;
thresholds **20%/10%/5% unchanged**. The gate automatically checked composition
(exactly three unique collections with run_tags) and code revision identity.

## 1. Verdict: **GO**

| Check | Result |
|---|---|
| Primary metric: mask p99 vs xgrammar | **passed: +73.5%** (0.81 vs 3.04 µs; threshold +20%) |
| Secondary holdout (`secondary_geo_route`) | **passed**: 1.02 vs 358.68 µs for llguidance (+99.7%); others ≤+10% |
| Other primary-scenario latencies ≤+10% | **passed**, including first mask: over 1000 cold observations the zig tail is consistently thinner than xg |
| e2e guard (paired median of AB/BA blocks, threshold 5%) | **12/12 OK** (maximum +1.7%; margin flag did not trigger) |
| B1: 30 independent processes | 30/30 in each of the three collections |
| B5/B8 (90 072 steps; 64/128/256 MiB) | no degradation (plateau 0.981/0.964/0.98, RSS ratio 1.0, 0 errors, 0 evictions) |
| Composition and revision identity | 3 unique collections; commit/protocol/sources_sha256 match in all |

## 2. Protocol v3: what was checked and how

Pinned by commit `d6efa3c` **before the series started** (review `e6a87b5`
E1-E4.4). Differences from v2 (thresholds unchanged):

1. **Composition and identity** (E1): the gate requires exactly `runs=3`
   unique collections with `run_tags` (missing/repeated/foreign collection →
   INCONCLUSIVE, exit 2; GO impossible); a single directory - "collection
   analysis" mode (exit 3, explicitly not acceptance). `commit`, `protocol`,
   `protocol_sha256`, `sources_sha256` from environment.txt of all collections
   are compared.
2. **E2 (incomplete data)**: a source is validated BEFORE combining and is
   excluded when incomplete; missing file → INCONCLUSIVE, present with
   `status != OK`/corrupted → NO-GO (both uniform and B1) - the summary no
   longer crashes on `null` values.
3. **e2e** (§5.3): balanced AB/BA order within each repeat (`order_pos`,
   `block_id`), decision - **median of paired ratios** zig/xg over "file ×
   repeat" blocks (24 blocks per configuration), 95% bootstrap CI (seed 42);
   the rule for an interval crossing the threshold is pinned in advance
   (`margin` flag, does not change the verdict); pooled medians are published
   for comparability, but no decision is made from them.
4. **First mask** (§5.2): 1000 cold observations per engine/collection (new
   bundle without cache + new engine + compile + first fill_mask, without
   warmup); at n=1000 p99 is the 10th sample value, not the maximum of 30
   points.
5. `capture_environment.py` records the revision, protocol, source hashes,
   loadavg/MemAvailable/uptime, nvidia-smi and top processes in every
   collection.

## 3. e2e: 12/12 within threshold (paired blocks)

| Case | Paired median zig/xg | CI95 | Pooled medians | Verdict |
|---|---:|---|---:|---|
| s1 b1 | 0.9987 | [0.9979; 1.0001] | -0.2% | OK |
| s1 b8 | 1.0103 | [1.0060; 1.0129] | +2.8% | OK |
| s1 b32 | 1.0165 | [1.0131; 1.0183] | +1.0% | OK |
| s2 b1 | 0.9985 | [0.9958; 1.0019] | -0.5% | OK |
| s2 b8 | 1.0051 | [1.0028; 1.0087] | +0.3% | OK |
| s2 b32 | 1.0014 | [0.9909; 1.0086] | +0.3% | OK |
| s3 b1 | 0.6324 | [0.6310; 0.6349] | -36.1% | OK |
| s3 b8 | 0.5543 | [0.5399; 0.5611] | -44.6% | OK |
| s3 b32 | 0.4312 | [0.4269; 0.4357] | -56.7% | OK |
| s4 b1 | 1.0027 | [0.9953; 1.0040] | +0.2% | OK |
| s4 b8 | 1.0096 | [1.0036; 1.0167] | +1.0% | OK |
| s4 b32 | 1.0138 | [1.0085; 1.0301] | +1.3% | OK |

Cases `s1 b8` and `s1 b32`, which flapped in the fifth-ninth runs, are stable
in all three collections: `s1 b8 +1.0%` (per collection 1.012/1.002/1.009),
`s1 b32 +1.7%` (1.016/1.019/1.014). No CI crosses the +5% threshold (margin
flag did not trigger). Answer validity and completion - in all 24 repeats of
each configuration (`valid_rows`/`completed_rows` complete, the gate issued
no FAIL).

## 4. First mask: 1000 cold observations per engine

p50/p95/p99, µs (primary uniform file; parentheses - share of observations >600 µs):

| collection | zig_adaptive | xgrammar |
|---|---|---|
| v3-run1 | 474.2 / 590.0 / 706.4 (4.2%) | 445.4 / 1010.0 / 1312.0 (32.2%) |
| v3-run2 | 471.7 / 588.4 / 691.9 (4.3%) | 458.6 / 989.8 / 1329.9 (35.8%) |
| v3-run3 | 471.6 / 580.2 / 627.8 (3.2%) | 442.0 / 897.1 / 1227.4 (19.8%) |
| combined primary corpus (n=3000) | p95 586.5 / p99 684.1; max 926.1 | p95 965.0 / p99 1288.6; max 1865.6 |
| combined secondary corpus (n=3000) | p95 517.0 / p99 571.0; max 841.6 | p95 935.4 / p99 1231.2; max 5086.0 |
| both corpora together (n=6000, mixed load) | p95 561.0 / p99 648.4; max 926.1 | p95 948.2 / p99 1273.9; max 5086.0 |

"Worst zig / best competitor" rule: p95 590.0 vs 897.1 (-34.2%), p99 706.4 vs
1227.4 (-42.4%) - with a large margin inside the +10% threshold. Increasing
the sample to 1000 revealed the main point: the xg tail is consistently
"thick" (>600 µs - 16-35% of primary-corpus observations), the zig tail is
thin (3-4%). The corpora are published separately (different loads, must not
be mixed): primary scenario - 117 vs 878 out of 3000 observations >600 µs;
secondary holdout - 14 vs 1253 out of 3000; the sum 131 vs 2131 out of 6000
applies only to the combination of the two corpora. The failure of the ninth
run (p95/p99 +20.9%/+51.6%) is most likely explained by unstable estimation
at n=30, where p99 ≈ the sample maximum, combined with the "worst/best" rule
across different collections; this is a corroborable hypothesis, not a proven
single sampling artifact (one event does not prove one cause: in the ninth run
+51.6% arose within the second collection).

## 5. Relative to the ninth run: only the methodology changed

- Core and HF adapter **unchanged** (wave d6efa3c - gate, benchmarks,
  protocol, documentation).
- Previously flapping cases are stable; the first mask is stable on a large
  sample.
- The ninth NO-GO remains a historical fact; methodology v3 is **not applied
  retroactively** to past data. Tables of the ninth REPORT were recomputed
  from its own raw data (review `e6a87b5` E3 corrections, §10 of that REPORT).

## 6. Other metrics (gate summary)

- **B1** (30/30 in each collection): zig_adaptive compile p50 0.17 ms, first
  mask 0.50 ms, total 2.00 s, RSS 756 MiB; xg 1.18/0.42/2.17/758;
  lg 0.27/1.31/2.13/772.
- **B5/B8**: plateau p99 0.981/0.964/0.98, RSS ratio 1.0, cache hits 99 993,
  evictions 0, errors 0.
- **B3/B6** (across three collections, values separated by "/" - collections
  1/2/3): JSB-100 - ok 1 / unsupported 84 / invalid 15 in all collections;
  corpus schemas supported: 16; session fill_mask phase p50 6.283/6.444/6.287
  µs, p99 90.867/89.970/90.634 µs; prepare tokenizers: gpt2
  52.49/53.44/52.42 ms, qwen2.5-1.5b 186.67/188.65/186.41 ms, tinyllama-sp
  24.90/24.36/25.42 ms (sp_shim 400 pairs: 199 exact + 198 first-space +
  3 lossy-UTF-8, bad: 0 - same in all collections), synthetic up to
  262 144 tokens - normal.
- **GPU mask intervals** (p50, µs): zig build 4.95 / H2D+unpack 23.62 /
  apply 18.73 / **full 47.26**; xgrammar 6.95/16.36/33.44/56.7;
  llguidance 31.23/20.34/72.41/126.26.

## 7. Environment conditions

| | v3-run1 | v3-run2 | v3-run3 |
|---|---|---|---|
| collection (UTC) | 09:36:23 → 10:04:49Z | 10:05:01 → 10:33:48Z | 10:33:56 → 11:02:40Z |
| loadavg 1/5/15 | 1.62/1.53/1.34 | 1.98/1.56/1.48 | 2.13/1.74/1.52 |
| MemAvailable | 46.8 GB | 46.7 GB | 46.5 GB |
| uptime | 40 908 s | 42 647 s | 44 379 s |
| GPU (nvidia-smi) | 1807 MHz, 64 °C, 0%, 57.8 W | 1807 MHz, 64 °C, 1%, 57.3 W | 1807 MHz, 63 °C, 0%, 56.9 W |

Commit `d6efa3c` was made ~1 minute before the first collection started; the
tree across all three measurements - no `dirty` changes recorded (`tree_status:
clean`).

## 8. Reproduction

```bash
export PYTHONPATH=python:benchmarks HF_HUB_OFFLINE=1
for tag in v3-run1 v3-run2 v3-run3; do
  python3 benchmarks/run_all.py --tag "$tag"          # 12 cases; uniform = 1000 cold
  D=benchmarks/results/<timestamp>_$tag
  python3 -u benchmarks/bench_e2e_constrained.py --out "$D"           # AB/BA, reps=4
  python3 -u benchmarks/bench_e2e_constrained.py --out "$D/repeat_tmp"
  mv "$D/repeat_tmp/bench_e2e_constrained.json" "$D/bench_e2e_constrained_repeat.json"
  rmdir "$D/repeat_tmp"
  python3 benchmarks/bench_gpu_mask_path.py > "$D/bench_gpu_mask_path.json"
  python3 benchmarks/capture_environment.py --tag "$tag" --out "$D/environment.txt"
done
# acceptance (composition + identity + gate; exit 0):
python3 benchmarks/summarize_control.py <run1> <run2> <run3>
# report tables (same functions as the gate):
python3 benchmarks/summarize_control.py --tables <run1> <run2> <run3>
# negative composition checks (GO unreachable):
python3 benchmarks/summarize_control.py <run3>               # exit 3 - single-collection analysis (NOT acceptance)
python3 benchmarks/summarize_control.py <run3> <run3> <run3> # exit 2 - duplicate directory
python3 benchmarks/summarize_control.py <run1> <run2>        # exit 2 - missing collection
```

## 9. Boundaries and open questions

- This is **one registered set of three collections**; robustness beyond it
  is not claimed ("repeat until first GO" was not applied: the series is
  single, launched on the committed protocol).
- Methodology v3 reduces the impact of transient windows (paired blocks
  instead of pooled medians) but does not eliminate them physically; machine
  conditions are recorded in environment.txt of every collection.
- The ninth NO-GO and previous verdicts remain history; the criterion and the
  20%/10%/5% thresholds were unchanged in any wave.
- `zig_lazy` on the holdout p95/p99 ~80 ms - outside the scored mode (published).

## 10. Corrections from recheck wave `7990d3b` (review 2026-09-19)

Review 7990d3b confirmed the GO of the series by independent recomputation
(624 aggregates, 93 hashes, 288 pairs, 7 872 responses) and required closing
three harness defects - without a new protocol and without new measurements.
Fixes delivered by commit `cd6d31c` (verification harness; measurement
scripts unchanged); review artifacts attached (review 7990d3b).

1. **Uniqueness of paired records (V1).** Before computing statistics the gate
   requires, for each pair (file, repeat): exactly one record per engine,
   repeats exactly from the pinned set {0..3}, positions {0,1}, conformance
   to the pinned AB/BA, positive sequential times, and consistency of
   `total_s` with `t_end_ns - t_start_ns`; a block with a duplicate is
   excluded from the decision. Duplicate/extra repeat/violation of positions
   or times → NO-GO; incomplete repeats/times → INCONCLUSIVE. Review
   counterexamples on temporary copies of these same collections (slow zig
   duplicates rep=0; xgrammar with zig positions - 0/0 and 1/1) now produce a
   structured NO-GO (exit 1) instead of the former GO
   (negative scenarios are covered by the gate test suite
   `python/tests/test_control_gate.py`).
2. **CLI on corrupted/incomplete JSON (V2).** Preliminary summary printing no
   longer crashes: files are classified in advance, sections do not crash the
   CLI, the verdict is always rendered by `acceptance_gate`. Subprocess tests:
   corrupted primary uniform → exit 1 without traceback; missing `protocol`
   block → exit 2, INCONCLUSIVE.
3. **Table corrections.** The row "combination (n=6000)" (§4) was replaced
   with separate corpus totals (117 vs 878 out of 3000 in the primary; 14 vs
   1253 out of 3000 in the secondary); §6 was recomputed from this series'
   files; the conclusion about the cause of the ninth failure was moved to
   corroborable-hypothesis status. `tables.md` was regenerated with the fixed
   `--tables` (diff - only the separate corpus totals). The mixed sum was also
   fixed in README, benchmarks/README and `manifest.verdict`. The margin flag
   remains informational, as pinned in v3.

Recheck of THESE SAME saved collections with the fixed gate
(`gate.txt` - evidence): **GO, exit 0**; no pair-validator warnings;
§1-§3 numbers unchanged. Protocol v3 unchanged (protocol_sha256
`02af30e0990fa53b...` matches), thresholds 20%/10%/5% unchanged, no new
measurements performed. Full pytest matrix after the fixes: 391/391 on each
of the two backends; gate tests 53/53.

## 11. Corrections from recheck wave `cd6d31c` (review 2026-09-19, second pass)

Re-review cd6d31c again confirmed the GO of the series by independent
recomputation (624 aggregates, 288 pairs, 7 872 responses) and required
closing the two remaining harness defects - without a new protocol and
without new measurements. Fixes delivered by commit `4d2a424` (verification
harness; measurement scripts unchanged):

1. **P2 (C1) - NaN produced a false GO.** `total_s` and uniform metrics must
   now be finite strictly positive numbers (`math.isfinite`, bool excluded)
   before being added to pooled/blocks and before comparisons. NaN/±∞/zero/
   negative records do not enter the statistics and produce a structural
   NO-GO; zero competitor times no longer reach the median division (there
   was a ZeroDivisionError). Review repro (NaN for zig in s1 b8 in all three
   collections): exit 1, NO-GO, message that no finite positive `total_s`
   exists, no traceback.
2. **P3 (C2) - corrupted nested fields.** Before aggregation the types of
   mandatory blocks are checked: `runs` - a list of objects (missing →
   INCONCLUSIVE; corrupted/non-list/non-object elements → NO-GO), `summary` -
   an object (otherwise NO-GO), `protocol` - an object (corrupted → NO-GO;
   value mismatch, as before, INCONCLUSIVE), `engines` uniform and B1 -
   structure. Review counterexamples (`runs:null`, `summary:null`,
   `protocol:[1]`) now produce structured verdicts without traceback
   (negative scenarios are covered by the gate test suite).

Recheck of THESE SAME saved collections with the fixed gate
(`gate.txt` - evidence): **GO, exit 0**. `tables.md` was regenerated with
the fixed `--tables` (byte-identical at that time; see §12 for the English
regeneration). Gate tests 64/64 (+11 negative, including subprocess CLI
checks), full pytest matrix 402/402 on each of the two backends. Protocol v3
(protocol_sha256 `02af30e0990fa53b...`) and thresholds 20%/10%/5% unchanged; no
new measurements performed.

## 12. Translation to English (2026-09-19)

The repository (code, comments, CLI messages, docs, this report) was
translated from Russian to English without changes to the protocol, its
thresholds, or any measured data. The `acceptance_protocol_v3` spec text was
translated as part of this pass; the gate accepts, alongside the recomputed
hash, the pre-translation spec hash `02af30e0990fa53b...` recorded in
`environment.txt` of the three collections (see manifest notes and
`benchmarks/README.md`). Evidence in this directory was regenerated in
English: `gate.txt` (acceptance run of this same saved series - **GO, exit
0**) and `tables.md` (numbers unchanged; headers/dividers translated). The
former `recheck_*` review logs were removed together with the review documents.
Gate tests after the change: 65/65; full pytest matrix 402/402 on each
backend.
