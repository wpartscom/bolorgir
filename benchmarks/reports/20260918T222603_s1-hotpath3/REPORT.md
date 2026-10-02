# Control run s1-hotpath (ninth): HF adapter hot path fusion, three-collection protocol

Date: 2026-09-19. Code commit: `49cd0a9` (wave `s1-hotpath`; the same code in all
collections). Data: three complete collections of the same code - `s1-hotpath`
(`20260918T220214`), `s1-hotpath2` (`20260918T221427`), `s1-hotpath3`
(`20260918T222603`), REPORT covers all three; each has 12 `run_all` cases
(including secondary runs), e2e ×2 with 4 repeats (pool n=24 per configuration),
GPU path, `environment.txt`. Aggregation - per the
`manifest.json#control_scenario.acceptance_protocol_v2` pinned BEFORE the run:
three collections without discarding (uniform - worst zig / best competitor;
e2e - repeat pool with medians; B1 - in each collection). Thresholds 20%/10%/5%
unchanged.

_Report updated 2026-09-19 following the `e6a87b5` recheck (analysis - §10):
numeric tables of §2-§4 recomputed machine-side with the same functions as the
gate; causality wording narrowed; the NO-GO verdict unchanged, raw data - no
edits._

## 1. Verdict: **NO-GO** - two classes of marginal checks

| Check | Result |
|---|---|
| Primary metric: mask p99 vs xgrammar | **passed: +70.4%** (0.83 vs 2.82 µs; threshold +20%) |
| Secondary holdout (`secondary_geo_route`) | **passed**: mask p99 0.83 vs 357.45 µs for llguidance (+99.8%); others ≤+10% |
| e2e guard (pool n=24, threshold 5%) | **11/12**: `s1 b8 -0.9%` (`s1 b8` closed - see §2), but `s1 b32 +5.7%` - FAIL (§3) |
| Other latencies of the primary scenario ≤+10% | **first mask p95 +20.9% and p99 +51.6% - FAIL** (§4); the rest passed: accept p99 0.49/0.91, cold 119.8/1184.9, warm 0.93/2.06, first mask p50 469.3/440.5 (+6.5%) |
| B1: 30 independent processes | 30/30 in each of the three collections |
| B5/B8 (90 072 steps; 64/128/256 MiB) | no degradation (plateau 1.002/1.059/1.003, RSS ratio 1.0, 0 errors, 0 evictions) |
| Per-class observations | satisfied (the gate did not emit INCONCLUSIVE) |

## 2. Closed: `s1 b8` - the original blocker of the eighth run

Pool n=24 (three collections × two files): **zig 0.362 vs xg 0.365 s = -0.9%**
(in the eighth run: +6.9% at n=16). Per-file medians:

| file | zig | xg | Δ |
|---|---|---|---|
| hotpath / e2e | 0.349 | 0.347 | +0.7% |
| hotpath / e2e_repeat | 0.384 | 0.382 | +0.4% |
| hotpath2 / e2e | 0.350 | 0.349 | +0.5% |
| hotpath2 / e2e_repeat | 0.379 | 0.373 | +1.5% |
| hotpath3 / e2e | 0.352 | 0.348 | +1.1% |
| hotpath3 / e2e_repeat | 0.378 | 0.370 | +2.0% |

(Table recomputed by `summarize_control.py --tables`.) All six files fit within
+0.4 to +2.0%, the pool median - level. Three factors combined: hot path fusion
(§5, fewer operations and allocations per step), the pool of 24 repeats
(single ±20 ms spikes hitting individual repeats no longer shift the median)
and, as in the sixth/seventh runs on a quiet machine, nominal behavior in the
+0.4 to +2.0% range.

## 3. e2e failure: `s1 b32` +5.7% (pool n=24)

Pool: zig 0.628 vs xg 0.594 s. Per-file medians:

| file | zig | xg | Δ |
|---|---|---|---|
| hotpath / e2e | 0.602 | 0.552 | **+9.1%** |
| hotpath / e2e_repeat | 0.673 | 0.654 | +2.9% |
| hotpath2 / e2e | 0.567 | 0.558 | +1.8% |
| hotpath2 / e2e_repeat | 0.662 | 0.651 | +1.7% |
| hotpath3 / e2e | 0.569 | 0.558 | +2.0% |
| hotpath3 / e2e_repeat | 0.649 | 0.638 | +1.7% |

(Recomputed by `summarize_control.py --tables`; the previous values in this
table were inaccurate - see §10.)

**Nature of the failure.** Four of six files ≤+2.5%, five of six ≤+5%
(the only large per-file gap is the first measurement of the first collection,
+9.1%). The pool-median failure is formed by two superimposed effects:

1. **Local window in the first file of the first collection** (+9.1%; all four
   PAIRED gaps within the file give +7.59 to +11.25% - the loss exists even without
   pooling across processes; in collections 2-3 the same file gives +1.8%/+2.0%) -
   the same phenomenon that flapped on `s1 b8` in the fifth-eighth runs
   (machine background windows, §7); here it caught `b32`. The causal
   attribution of the window is not established (§10).
2. **Cross-process drift**: the second e2e file of the first collection is slower
   than the first by +11.84% (zig) and +18.58% (xg) - the previous wording
   '+8 to 17%' did not cover the xg case. With a pool median of 12 'fast' and
   12 'slow' repeats (per engine), the median lands on the boundary of the
   two modes, and a small asymmetry of the gap is amplified when crossing the
   boundary: zig 0.628 vs xg 0.594 (+5.7%), although per-file gaps are
   five times smaller. The same data structure in the eighth run gave
   `s1 b8` +6.9% at n=16.

Both effects are consistent with the pool statistics and the measurement
environment; no core degradation was observed in this wave (the core was
unchanged, B2 metrics nominal), but the specific causal link of the window is
not proven (§10). Paired diagnostic recomputation (post hoc; methodology v3 is
NOT applied to the ninth verdict): median of paired zig/xg ratios over 24 blocks
1.0185 (+1.8%), 95% CI [1.0153; 1.0456] - the pool median is shifted by the
mode boundary more strongly than the intra-block gaps.

## 4. Other-latency failure: first mask p95/p99

Aggregation 'worst zig / best competitor' over the three collections:

| collection | zig p50/p95/p99, µs | xg p50/p95/p99, µs |
|---|---|---|
| hotpath | 469.3 / 542.0 / 661.7 | 440.5 / 811.2 / 1126.0 |
| hotpath2 | 466.2 / 555.7 / **972.7** | 439.1 / **602.2** / **641.6** |
| hotpath3 | 464.0 / **728.0** / 791.9 | 474.5 / 951.1 / 1050.8 |
| **aggregation** | **469.3 / 728.0 / 972.7** | **440.5 / 602.2 / 641.6** |
| result | **p95 +20.9%, p99 +51.6% > +10%** | |

(Recomputed by `summarize_control.py --tables`.)

**Nature of the failure.** p99 at n=30 is practically the sample maximum;
the rule 'worst zig / best competitor over all collections' compares extremes
from different collections. Also, the p99 loss **+51.6% exists already WITHIN
the second collection** (972.7 vs 641.6 µs) - it cannot be fully explained by
comparing extremes from different collections: the cross-collection rule adds a
separate effect to it (for xg the 'best' turned out to be the unusually clean
collection 2, while for zig the only outlier from collection 2 fired). Pairwise
per collection: p95 passes ALL three collections (-33.2%/-7.7%/-23.5%), p99
passes two of three (-41.2%/+51.6%/-24.6%). Across 90 raw observations of the
primary uniform file, the zig tail is **thinner** than xg's: values >600 µs -
**5 for zig vs 23 for xg** (the latter has outliers up to 1126 µs); pooling these
90 observations gives p95 661.7/812.4 µs, p99 791.9/1050.8 µs (zig better).
This is different statistics - it is published as diagnostics and does NOT
replace the pinned decision rule. In the eighth run the same check passed
narrowly (-3.8% and -0.5%) thanks to the 'thick' xg tail. The failure remains a
property of the conservative rule on small samples; the rule is pinned in the
manifest and unchanged - the failure is published as is. Robust tail estimation
is the subject of protocol v3 (1000 cold observations, p99 = 10th value of the
sample).

## 5. Wave `s1-hotpath`: what changed (commit `49cd0a9`)

Core unchanged; the HF adapter hot path (`transformers.py`) and the protocol changed.

- **Fusing the forbid unpack path**: `MaskGpuUnpacker.unpack_forbid` writes the
  FORBIDDEN bits with a single `eq` directly into a persistent CPU buffer (fast
  path for all active rows: no `logical_not_`, no mask-row copies, no
  `hits` allocations per step; partial path - the same cached buffer +
  `index_copy_`). The public `unpack` (allowed semantics) is preserved; a
  regression test '`unpack_forbid` == `~unpack`' added, including the `out=` form.
- **Protocol v2** (`acceptance_protocol_v2`, pinned before the run): three
  collections of the same code; aggregation and rejection rules and thresholds unchanged.
- Tests at the time of the wave: Zig 120/120 (Debug/ReleaseSafe), pytest 361/361 on
  both backends (ctypes/package), C example exit 0, fuzzer seed 424242
  (`ALL CAMPAIGNS PASSED`, 890 344 iterations), `zig fmt --check` clean.
- **Protocol v2 pinning order** (review e6a87b5 §4.4): the first collection
  started at 22:02:14, commit `49cd0a9` made at 22:14:06 - measurements ran on
  the same uncommitted bytes (hashes match, see environment.txt), but there is
  no independent git pinning BEFORE the first measurement. Fixed in protocol v3:
  code and protocol commit - before the run; revision recorded in environment.txt
  of each collection and checked by the gate.

## 6. Other (combined summary, from `summarize_control.py`)

- **B1** (30 processes in each collection): zig_adaptive compile p50 0.17 ms
  (best), first mask 0.49 ms, total 2.16 s, RSS 756 MiB; xg 1.17/0.43/
  2.43/758; lg 0.30/1.39/2.31/772.
- **B5/B8**: plateau p99 1.002/1.059/1.003, RSS ratio 1.0, cache hits 99 993,
  evictions 0, errors 0 - no degradation.
- **B3/B6**: JSB-100 - ok 1 / unsupported 84 / invalid 15; corpus schemas
  supported 16; session phase fill_mask p50 6.62 / p99 95.11 µs; tokenizers:
  gpt2 prepare 59.6 ms, qwen2.5-1.5b 205.3 ms, tinyllama-sp 26.1 ms
  (sp_shim 400 pairs: 199 exact + 198 first-space + 3 lossy-UTF-8), synthetic
  up to 262 144 tokens - nominal.
- **GPU mask intervals** (p50, µs): zig build 5.25 / H2D+unpack 26.32 /
  apply 20.26 / **full 52.43**; xgrammar 5.97/16.86/30.85/53.72;
  llguidance 36.83/23.06/79.6/141.11.

## 7. Environment conditions and transients

| | s1-hotpath | s1-hotpath2 | s1-hotpath3 |
|---|---|---|---|
| date (UTC) | 05:14:22Z | 05:25:57Z | 05:37:31Z |
| loadavg 1/5/15 | 1.97/2.29/2.02 | 2.10/2.14/2.12 | 2.33/2.29/2.12 |
| MemAvailable | 46.9 GB | 47.3 GB | 47.3 GB |
| uptime | 23 481 s | 24 176 s | 24 870 s |

Machine background - a working VS Code (renderer ~22%, gpu-process ~13%). Transient
diagnostics on `s1 b8` (outside scored CPU phases): rare +2 to +20% spikes
on individual zig repeats with flat xg; GC excluded (run with GC disabled -
spikes remain) and CPU phases excluded (`fill_mask`/`accept`/`unpack` in
spike repeats nominal); shown that E-core isolation slows BOTH engines down
almost twofold, i.e. generation is sensitive to CPU placement/background.
**Limits of these conclusions (review e6a87b5 §4.3):** the original timelines of
the GC/E-core/VS Code experiments are absent from the published artifacts; CPU
phase markers do not attribute asynchronous GPU execution; the specific `b32` window
is not causally attributed. Consistency with background windows is a hypothesis,
not proof; 'not fixable in code' is not asserted.

## 8. Reproduction

```bash
export PYTHONPATH=python:benchmarks HF_HUB_OFFLINE=1
for tag in s1-hotpath s1-hotpath2 s1-hotpath3; do
  python3 benchmarks/run_all.py --tag "$tag"          # 12 cases
  D=benchmarks/results/<timestamp>_$tag
  python3 -u benchmarks/bench_e2e_constrained.py --out "$D"
  python3 -u benchmarks/bench_e2e_constrained.py --out "$D/repeat_tmp"
  mv "$D/repeat_tmp/bench_e2e_constrained.json" "$D/bench_e2e_constrained_repeat.json"
  rmdir "$D/repeat_tmp"
  python3 benchmarks/bench_gpu_mask_path.py > "$D/bench_gpu_mask_path.json"
  # ...environment.txt (machine conditions + sha256)...
done
# combined gate (three directories, exit 1 = NO-GO):
python3 benchmarks/summarize_control.py <dir-hotpath> <dir-hotpath2> <dir-hotpath3>
```

## 9. Published losses and open questions

- `s1 b32` +5.7% (pool): five of six files within +2.5%; analysis - §3.
- First mask p95/p99: failure of the 'worst zig / best competitor' aggregation;
  the zig tail is thinner on raw data (§4).
- Machine transient windows (§7): the cause of the specific window is not established;
  consistency with background (§7) - a hypothesis. How to account for such windows -
  the acceptance owner's decision: the pool median mitigates single spikes (which is
  what closed `s1 b8`), but cross-process drift within a collection shifts the pool
  median (§3). Protocol v3 accounts for drift and engine order with paired AB/BA
  blocks and bootstrap CIs.
- `zig_lazy` on the holdout p95/p99 ~80 ms - outside the scored mode (published).

Review `d84f992` closures remain in force: D1 (missing decoder), D3 (validation of each
source), D6 (in-flight destroy), D2 (protocol pinned and applied - three collections
aggregated without discarding). The eighth NO-GO remains a historical fact; the ninth
is **NO-GO** with `s1 b8` closed and two new marginal failures analyzed above.

## 10. Corrections from the `e6a87b5` recheck (2026-09-19)

The recheck confirmed the NO-GO and pointed out inaccuracies in the report and the
gate (review e6a87b5). What changed in this document (the verdict was not changed,
raw data was not edited):

- **Numeric tables** of §2-§4 recomputed machine-side (`summarize_control.py
  --tables` - the same functions and statistics as the gate). Corrections: per-file
  median gaps for `b32` - +9.1/+2.9/+1.8/+1.7/+2.0/+1.7% (the ≤+2.5% rule
  is passed by 4/6 files, ≤+5% - by 5/6, not 5/6 and 6/6); local PAIRED gaps of
  the first file +7.59 to +11.25%; drift of the second file of the first collection
  +11.84% (zig) / +18.58% (xg) (the previous '+8 to 17%' range did not cover xg); the
  >600 µs tail over 90 observations of the primary uniform - 5 for zig vs 23 for
  xg (was '4 vs ~20'); the p99 loss +51.6% exists already within the second
  collection (972.7 vs 641.6 µs) and is not fully explained by the cross-collection rule.
- **Causality wording** narrowed (§3, §7, §9): drift and repeat instability
  are supported by data; the specific causal attribution of the `b32` window and
  'not fixable in code' are not proven (experiment timelines were not published,
  CPU markers do not attribute GPU execution; the wave changed Python/GPU bytes of
  the adapter, although the core body did not change).
- **Protocol v2 pinning order** (commit after the start of the first collection)
  disclosed in §5 and fixed in protocol v3: commit before the run + revision and
  protocol hash in environment.txt of each collection.
- **Tooling findings of the review** (E1: the gate did not check protocol composition;
  E2: null values crashed the summary) fixed in
  `benchmarks/summarize_control.py` and covered by tests
  (`python/tests/test_control_gate.py`). The ninth verdict remains NO-GO;
  methodology v3 (paired AB/BA blocks, 1000 cold observations) applies only to
  subsequent series and is NOT used retroactively.
