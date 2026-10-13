# XGrammar comparison v2 — compact series (T10 section 10)

Runs: 2026-10-13, same box (RTX 3060), same vLLM instance settings for
both backends. Two passes of the identical matrix exist: `compact/`
(A3, sessions x1c/b1c/x2c/b2c) and `compact-r2/` (A4, x1d/b1d/x2d/b2d),
the second one with per-session EngineCore scans; the figures below
use the repeat pass (A4) as canonical and name the first pass where
the two differ. License-relevant facts: no upstream code copied; the
series calls `vllm serve` from `benchmarks/local/venv-vllm`.

Why this series exists (tasks TZ §6.1–§6.3 of the reviewer task):

- the 2026-10-12 series ran xgrammar with the vLLM default
  `disable_any_whitespace=False` while bolorgir constrains a compact
  language; ADR-0016 Finding 4 requires the comparison to constrain
  the same compact language, so the xgrammar sessions here run
  `--structured-outputs-config '{"backend": "xgrammar",
  "disable_any_whitespace": true}'` (verified from the server log and
  recorded per session in `compact/prov-*.txt` and
  `compact/manifest.json`);
- the v1 wording “all 4056 requests produced valid answers” is
  corrected: a record cut by `max_tokens` is not a completed document.
  The corrected accounting of the old material is
  `REPORT-v2-oldseries.md` / `report-v2-oldseries.json`; the v1 files
  without answer text cannot be re-checked, which is stated in them;
- “TPOT” here is the client-observed per-token time
  `(e2e − TTFT)/(tokens − 1)`; it includes engine and delivery
  overheads and is **not** a profiled mask cost;
- review of 2026-10-13 (point 3): the A3 manifest kept `engine_maps`
  empty because its provenance snapshot ran before the adapter loads
  the core into the engine process; the repeat pass (amendment A4)
  re-runs the same matrix and captures the EngineCore binding per
  session — PID, mapping line and full SHA-256, with the xgrammar
  sessions as the negative control (see the EngineCore section).

## Conditions (from the pass manifests)

| Item | Value |
|---|---|
| Core library | `python/bolorgir/_lib/libbolorgir.so.0.1.0`, sha256 `9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41` |
| vLLM adapter | `python/bolorgir/vllm.py`, sha256 `7bbee811a625cdb27d0dc341ef56dc121b1c0d6cd8dbfdd6f13a7efbae2740d4` |
| vLLM patch | `integrations/vllm/vllm-0.31.0-bolorgir.patch`, sha256 `d0b8ea80d3ad02946ceab71ee93da811ec6dc9250e63481aedc932b1a1aa065a` |
| vLLM | 0.31.0 |
| XGrammar | 0.2.7 (`importlib.metadata`; the `xgrammar.__version__` attribute does not exist in 0.2.7) |
| Model | `Qwen/Qwen2.5-0.5B-Instruct@7ae557604adf67be50417f59c2c2f167def9a775` |
| Sessions | pass A3: `x1c`, `b1c`, `x2c`, `b2c`; pass A4 (repeat): `x1d`, `b1d`, `x2d`, `b2d` — one `vllm serve` per session on port 8021, stopped with SIGINT; the backend alternates so the four sessions stay inside one time window |

## Contents

| Path | What it is |
|---|---|
| `PROTOCOL.md` | measurement protocol; amendment A3 defines the first pass, amendment A4 the repeat with per-session EngineCore binding |
| `compact-r2/REPORT-v2.md`, `compact-r2/summary-v2.json` | the v2 report and its JSON of record for the repeat pass (canonical; 4056 records, 24 cells, `problems: []`); the first pass carries its own pair in `compact/` |
| `REPORT-v2-oldseries.md`, `report-v2-oldseries.json` | corrected accounting of the 2026-10-12 material under the same categories |
| `REPORT.md` | the original v1 report (kept for history; its conditions note points here) |
| `CONCLUSIONS.md` | conclusions, wording aligned with the tasks above |
| `compact/raw-*.jsonl`, `compact-r2/raw-*.jsonl` | raw records, 1014 per session in each pass; every record keeps the response text, so validity is re-checkable |
| `compact/server-*.log`, `compact/prov-*.txt` (and the same in `compact-r2/`) | server logs and per-session provenance (argv, library hash, parsed `StructuredOutputsConfig`) |
| `compact-r2/engine-*.txt`, `compact-r2/manifest.json` | per-session EngineCore scans and the manifest with the parsed `engine` block (see below) |
| `compact/engine-core-check/` | supplementary standalone EngineCore check of the A3 configuration (kept as a quick re-check) |
| `tools/` | everything needed to reproduce: `run_compact.sh` (standalone), `run_compact_a3.sh` (historical A3 script), `engine_core_check.sh`, `xgrammar_bench2.py` (collector), `xgrammar_common.py` (schemas/prompts), `xgrammar_report.py` (report, revision 3), `test_xgrammar_report.py` (17 tests), `xgrammar_report_change_note.md` |
| `SHA256SUMS` | checksums of every versioned file in this directory |

Reproduce (any working directory; the script resolves the repository
root relative to itself and uses only the tools published in this
directory):

```sh
benchmarks/reports/20261013_xgrammar-compare-a3/tools/run_compact.sh OUT_DIR \
    [--labels x1e b1e x2e b2e] [--repeats 2] [--port 8021]
```

The script takes the output directory as an argument (refuses an
existing one), takes the vLLM environment from `VLLM_DIR` (default
`<root>/benchmarks/local/venv-vllm`) and refuses any core but
`9a5baf3e…` by its full SHA-256. Per session it records
`raw-<label>.jsonl`, `server-<label>.log`, `prov-<label>.txt` and the
EngineCore scans `engine-<label>.txt` (bolorgir must show the mapping
in both scans, xgrammar must not), then writes `manifest.json` and
produces the report pair `summary-v2.json` / `REPORT-v2.md` with the
published report tool; a non-empty problem list fails the run.

Regeneration from the stored raw files was re-checked on 2026-10-13
with report revision 3 (stricter integrity checks: matrix
completeness, stream end, usage, uniqueness, re-validation
agreement; see `tools/xgrammar_report_change_note.md`):
`summary-v2.json` and `REPORT-v2.md` reproduce from
`compact-r2/raw-*.jsonl` (and from `compact/raw-*.jsonl`) exactly
apart from the input list (paths and order) and the generation
timestamp; every measured value, category and denominator is equal,
with `problems: []`.

## Headline numbers (repeat pass A4; first pass named where they differ)

- At batch ≥ 32 xgrammar is faster in **every** measured cell, in both
  passes: TPOT 1.4–3.4× lower (first pass 1.41–3.26, repeat
  1.47–3.36) and tokens/s 1.3–2.7× higher (first 1.31–2.73, repeat
  1.28–2.73); the flagged `w2` batch-32 cell is 1.41× / 1.46× in the
  first pass and 1.47× / 1.48× in the repeat.
- At batch 1 `w1`/`w3` are at parity (first pass, bolorgir vs
  xgrammar: 191.7 / 191.5 and 227.5 / 221.9 tok/s); `w2` is at parity
  only in the steady state — every bolorgir session's first `w2` pass
  costs ≈10 s (outlier bullet below).
- Output length: on the compact `w2` language bolorgir answers are
  shorter (39 tokens vs 50 for xgrammar); on `w1` the 64-token cap cut
  105 bolorgir answers in the first pass and 107 in the repeat (of 676
  per pass; 15.5–15.8%), kept as cuts, not completed documents;
  xgrammar finishes all.
- First-pass `w2` batch-1 outlier is systematic in bolorgir — 10.33
  and 9.60 s in the first pass, 10.21 and 10.19 s in the repeat —
  against 0.18–0.19 s on the second repeat; xgrammar shows no such
  cost. Recorded as observed; no cause is claimed here.
- Categories are identical between the passes except the `w1`/bolorgir
  cuts (105 vs 107, i.e. 3951 vs 3949 completed documents); timing
  cells move by up to ≈10% between passes (run-to-run spread).
- Independent re-validation over the stored texts: 4056/4056 agreement
  in both passes, 0 differences; the length cuts (105 / 107) checked
  with the bounded suffix test, 0 of them provably completable — cuts
  are kept, not counted, and not called legal partials.

## EngineCore load confirmation (per measured session)

The requirement (TZ 6.1) is bound to the measured sessions by the
repeat pass (A4): each session's `engine-<label>.txt` carries two
scans — mid-session, as soon as the first records were on disk, and
post-session — each recording the `VLLM::EngineCore` PID, comm and
argv, the `libbolorgir.so.0.1.0` mapping line from `/proc/<pid>/maps`
and the full SHA-256 of the library file;
`compact-r2/manifest.json` stores the parsed `engine` block. Result:
the bolorgir sessions `b1d` (PID 518247) and `b2d` (PID 523447) show
the mapping in both scans with the pinned hash; the xgrammar sessions
`x1d`/`x2d` show `mapped=0` in both scans (negative control). The A3
pass's standalone check (`tools/engine_core_check.sh`,
`compact/engine-core-check/`) is kept as a supplementary re-check of
the same server configuration.

## Traces (not versioned)

The bolorgir sessions also emitted agent traces (one JSONL per
session); they are 24.7 MB each and are kept as local artifacts:

- `benchmarks/local/t10-gpu-1/xgrammar-compare/compact/trace-b1c.jsonl` — sha256 `cc7831a40921e617b1fc833b052326c4d5743175d3c495a470780f7ac654f16e`
- `benchmarks/local/t10-gpu-1/xgrammar-compare/compact/trace-b2c.jsonl` — sha256 `27fc8aa8ed836486798bbe1373a949f4bee649f76f0400bbbb22c9b659150188`
- `benchmarks/local/t10-gpu-1/xgrammar-compare/compact-r2/trace-b1d.jsonl` — sha256 `f42113096e0caa3a7ef16bbf18616967f2073226025971d9d246606f67707404`
- `benchmarks/local/t10-gpu-1/xgrammar-compare/compact-r2/trace-b2d.jsonl` — sha256 `5d8901bd7cc6ca075e5b249270fcf04275a333b6a0d90f38bc03810acfd357ad`

They are diagnostic copies of the engine-side mask emissions; the
records needed for every number in `REPORT-v2.md` are in
`compact/raw-*.jsonl` (text included), and `tools/run_compact.sh`
re-creates the traces.
