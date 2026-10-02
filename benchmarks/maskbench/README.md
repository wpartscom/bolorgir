# maskbench/ - independent MaskBench runs for Bolorgir

MaskBench is the mask-computation benchmark of the
[jsonschemabench](https://github.com/guidance-ai/jsonschemabench)
repository (`maskbench/` subfolder): about 11,300 real-world JSON schemas
and 37,000 instances generated for them, mask computation measured in
isolation, TTFM (grammar compile) and TBM (per-mask time) reported as
percentiles.

This folder contains the Bolorgir adapter (`--blg`) for that harness, a
patch that packages it as an upstream pull request, and the results of
full-corpus local runs (2026-09-20).

## Contents

- `blg_engine.py` - the adapter implementing the MaskBench `Engine`
  interface on top of `bolorgir.Engine` / `Constraint` / `Session`.
- `bolorgir-engine.patch` - the same code as three git commits against
  `guidance-ai/jsonschemabench` at `ba103c7` (adds the adapter, the
  engine-agnostic `--compact` flag, README/requirements entries, and the
  `blg` entry in the results script).
- `results/` - `entries.txt` / `stats.txt` files produced by the
  MaskBench `scripts/maskbench_results.py` on this machine, plus the raw
  combined-table log.

## Run conditions

- jsonschemabench commit `ba103c7` (the snapshot of the published
  tables), full `maskbench/data/` corpus.
- Tokenizer: `unsloth/Meta-Llama-3.1-8B-Instruct` (the MaskBench
  default), 128,256 tokens.
- Engine: `bolorgir` 0.1.0 (`adaptive` mode); llguidance 0.7.10 (the
  MaskBench pin) for the paired run.
- This machine: 20 cores, 62 GiB RAM; 20 parallel runner processes;
  900 s per-schema time limit. No crashes, timeouts or OOM in any run.
- Bolorgir accepts only the canonical compact JSON form (no whitespace
  outside strings), so its runs use the new `--compact` flag. The
  llguidance run was repeated with `--compact` as well, so the paired
  table compares identical token streams.

## Results

### Paired run, compact serialization, full corpus

All times in microseconds.

| metric             | LLGuidance |  Bolorgir |
|:-------------------|-----------:|----------:|
| TBM avg            |         94 |   143,457 |
| TBM p25            |         37 |        27 |
| TBM p50            |         55 |       125 |
| TBM p75            |         74 |     1,767 |
| TBM p90            |        133 |   609,222 |
| TBM p95            |        233 |   649,598 |
| TBM p99            |        924 | 1,393,775 |
| TBM p99.9          |      3,370 | 1,711,274 |
| TBM p100           |     22,686 | 1,992,204 |
| TTFM avg           |      3,446 |       268 |
| TTFM p25           |      1,301 |       162 |
| TTFM p50           |      1,790 |       207 |
| TTFM p75           |      2,661 |       269 |
| TTFM p90           |      5,337 |       369 |
| TTFM p95           |      8,998 |       584 |
| TTFM p99           |     39,826 |     1,599 |
| TTFM p99.9         |     98,661 |     2,501 |
| TTFM p100          |    590,451 |     2,501 |
| tokens             |    917,768 |    15,670 |
| schemas            |     11,306 |    11,306 |
| passing            |      4,727 |       624 |
| compile error      |      2,377 |    10,682 |
| segmentation fault |          0 |         0 |
| out of memory      |          0 |         0 |
| timeout            |          0 |         0 |
| validation error   |      4,202 |         0 |
| invalidation error |          0 |         0 |

Reading of the table:

- Compile time (TTFM): Bolorgir is faster at every percentile of this
  run - p50 207 us vs 1,790 us (8.6x), p99 1.6 ms vs 39.8 ms (25x),
  worst case 2.5 ms vs 590 ms (236x). The two engines compile different
  schema sets (624 vs 8,929 files), so the rows are indicative, not a
  like-for-like intersection.
- Mask time (TBM): llguidance is faster overall under this protocol.
  The median (55 us vs 125 us) and especially the tail (p90 133 us vs
  609 ms) are decided by first-visit states: MaskBench opens a new
  session per instance and most files carry a single instance, so the
  engine's adaptive cache almost never serves a repeat. A first visit of
  a permissive state (string content) walks the whole token trie -
  0.6 s per state with a 128k-token vocabulary; a cache hit is 1.3 us.
  p25 (27 us vs 37 us) is the one TBM class where Bolorgir is ahead.
- Semantics: 0 validation and 0 invalidation errors on the 624 schemas
  that compiled (633 valid instances accepted, 77 invalid rejected).
- Coverage: 624 of 11,306 schemas compile (5.5%). BFCL function-call
  schemas are 586 of the 624; Github_trivial 18, Github_easy 17,
  Github_medium 2, Glaiveai2K 1. The other 10,682 files use constructs
  outside the canonical-v1 profile (draft-04/06/07 `$schema`,
  `definitions`, `$id`, `allOf`/`anyOf`/`oneOf`, `pattern`,
  `minProperties`, implicit `additionalProperties`, missing `required`,
  boolean schemas).

### Serializer run (`blg-ser`), post-fix build, full corpus (2026-09-26)

Re-measure of the published compact run with the value-preserving
serializer ON (`BOLORGIR_SERIALIZER=1`, engine id `blg-ser`), on the
post-audit build identified by its libbolorgir.so SHA-256
`d8f936924f4323481abe7e147422843747739d794445529b744eca313285e1c0`
(originally staged in a temporary build directory, no longer present;
canonical-v1 profile; includes the mask fast path and the 2026-09-26
audit fixes listed in CHANGELOG (R1-R4) plus the minLength
mask-normal-form fixes). Same corpus snapshot, same Llama
tokenizer, same `--multi --blg --compact` protocol as the paired run
above. All times in microseconds.

| metric             | Bolorgir (published, serializer off) | Bolorgir `blg-ser` (2026-09-26) |
|:-------------------|-------------------------------------:|--------------------------------:|
| TBM avg            |                              143,457 |                             262 |
| TBM p50            |                                  125 |                              14 |
| TBM p90            |                              609,222 |                             247 |
| TBM p95            |                              649,598 |                           3,045 |
| TBM p99            |                            1,393,775 |                           3,573 |
| TBM p100           |                            1,992,204 |                           7,107 |
| TTFM avg           |                                  268 |                              68 |
| TTFM p50           |                                  207 |                              50 |
| TTFM p100          |                                2,501 |                           1,106 |
| tokens             |                               15,670 |                          15,752 |
| schemas            |                               11,306 |                          11,306 |
| passing            |                                  624 |                             624 |
| compile error      |                               10,682 |                          10,682 |
| validation error   |                                    0 |                               0 |
| invalidation error |                                    0 |                               0 |
| serialization incompat |                                n/a |                               0 |

Reading of the table:

- Semantics: identical outcome counts to the published run (624 passing,
  633 valid instances accepted, 77 invalid rejected, 0 validation and 0
  invalidation errors) with the serializer in the path - the serializer
  preserves values exactly, including strings that look like numbers
  (`"1"` stays a string) and arbitrary text (CHANGELOG R4; 0 serialization
  incompatible across the full corpus).
- TBM: the tail collapse (p90 609 ms -> 247 us) is the ADR-0007 mask
  fast path plus the audit-wave fixes; the remaining p95+ entries are
  first (cold) visits of normalized states, not cache misses of repeats.
- tokens differ (+82) because the serializer reorders object keys to the
  schema declaration order, which retokenizes some instances.

### Default serialization (spaced instances), full corpus

Without `--compact` the instances carry spaces after `,` and `:`, which
is outside the canonical-v1 language: instances are rejected at the
first whitespace token. 9 of 11,306 schemas pass; see
`results/blg-spaced-entries.txt`. The compact form is not a workaround
for the measurement - the same values are serialized the only way this
engine accepts them.

### Precompute mode (sampled, not a full run)

A 15-file sample in `precompute` mode moved work into compile time
(40 ms to 110 s per schema) and still showed the 250 ms first-visit
spurts, so it is not a viable configuration for this protocol.

## Profile selection

The adapter compiles under an explicit JSON Schema profile: `canonical-v1`
(frozen default) or `spec-v1` (the full JSON Schema profile, implemented
in current engine builds - support table in
`docs/supported_features.md` section 1a, normative semantics in
`docs/semantics-spec-v1.md`). Selection, in priority order: the
`BlgEngine(profile=...)` constructor argument, then the
`BOLORGIR_PROFILE` environment variable, then the default:

```sh
BOLORGIR_PROFILE=spec-v1 python3 -u scripts/run_maskbench.py --blg --compact \
    --output tmp/out--blg-spec-v1 data/
```

An unknown profile name raises immediately (a typo must not silently
demote a run). As a compatibility fallback for pre-spec-v1 builds, the
adapter probes the installed build at startup
(`_engine_supports_profile`, once per process, outside every measured
section): a requested profile the build does not implement falls back to
canonical-v1 and the fallback is reported on stderr. On current builds
the probe passes and the requested profile is the effective one; every
run recorded in this README is canonical-v1. The effective profile is
recorded in the engine id: a supported non-default profile renames the
run to `blg-<profile>` (output directory and results-table row), so
MaskBench artifacts carry the profile they measured (ROADMAP A8.4).

## Value-preserving serializer (BOLORGIR_SERIALIZER)

The spec-v1 byte language fixes the declared key order by the schema,
while `--compact` keeps the data-determined order, so the adapter can
route instances through `bolorgir.serializer.serialize_value`
(semantics-spec-v1 section 6): declared object keys are reordered to the
schema declaration order, values are preserved exactly (integers spell
exactly, floats via the shortest round-trip repr), output is the compact
escape-table form. The runner
hands the hook the already-parsed `test["data"]`, and the value entry
point never re-parses a string instance as JSON text: the string `"1"`
stays the string `"1"` and `"hello"` serializes instead of failing. The
knob is opt-in: the `BlgEngine(serializer=...)` constructor argument,
then the `BOLORGIR_SERIALIZER=1` environment variable; default OFF, so
the recorded runs stay comparable bit-for-bit:

```sh
BOLORGIR_SERIALIZER=1 python3 -u scripts/run_maskbench.py --blg --compact \
    --output tmp/out--blg-ser data/
```

The engine id records the protocol that produced the artifacts: `blg`
for a default canonical-v1 run, `blg-<profile>` for a non-default
effective profile (e.g. `blg-spec-v1`), plus a `-ser` suffix when the
serializer is on (`blg-ser`, `blg-spec-v1-ser`). The id names the output
directory (`tmp/out--<id>`) and the row in the results table.

When the serializer cannot preserve a value (invalid JSON input,
duplicate object keys, non-finite floats, lone surrogates, nesting
beyond the depth limit), the adapter raises with
`serialization_incompatible = True` and the runner counts the file in
its own per-file bucket (`num_serialization_incompatible`, surfaced as
the `serialization incompat` row of the results table). Per
semantics-spec-v1 section 7 these schemas are a protocol artifact: they
count neither as coverage (passing) nor as an engine refusal (compile
error).

## Reproduction

```sh
git clone https://github.com/guidance-ai/jsonschemabench ~/jsb
cd ~/jsb && git checkout ba103c7
git am /path/to/benchmarks/maskbench/bolorgir-engine.patch

cd maskbench
# bolorgir from PyPI (once published) or from a source tree:
export PYTHONPATH=/path/to/bolorgir/python
python3 -m pip install --user -r requirements.txt

python3 -u scripts/run_maskbench.py --blg --compact \
    --output tmp/out--blg-compact data/
python3 -u scripts/run_maskbench.py --llg --compact \
    --output tmp/out--llg-compact data/
python3 scripts/maskbench_results.py tmp/out--blg-compact tmp/out--llg-compact
```

## Relation to the control series

The control series in `../reports/` measures the engine as deployed
(cold compile, warm masks, own corpus and protocol), and its Performance
section in the root README quotes those numbers. MaskBench measures a
different point of the design space: single-instance files where every
mask is a first visit. Both matter; the MaskBench run is the independent
protocol check, and it does not support a "fastest mask" claim on its
own.
