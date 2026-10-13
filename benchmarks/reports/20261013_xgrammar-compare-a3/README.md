# XGrammar comparison v2 — A3 compact series (T10 section 10)

Run: 2026-10-13, same box (RTX 3060), same vLLM instance settings for
both backends. License-relevant facts: no upstream code copied; the
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
  overheads and is **not** a profiled mask cost.

## Conditions (from `compact/manifest.json`)

| Item | Value |
|---|---|
| Core library | `python/bolorgir/_lib/libbolorgir.so.0.1.0`, sha256 `9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41` |
| vLLM adapter | `python/bolorgir/vllm.py`, sha256 `7bbee811a625cdb27d0dc341ef56dc121b1c0d6cd8dbfdd6f13a7efbae2740d4` |
| vLLM patch | `integrations/vllm/vllm-0.31.0-bolorgir.patch`, sha256 `d0b8ea80d3ad02946ceab71ee93da811ec6dc9250e63481aedc932b1a1aa065a` |
| vLLM | 0.31.0 |
| XGrammar | 0.2.7 (`importlib.metadata`; the `xgrammar.__version__` attribute does not exist in 0.2.7) |
| Model | `Qwen/Qwen2.5-0.5B-Instruct@7ae557604adf67be50417f59c2c2f167def9a775` |
| Sessions | `x1c`, `b1c`, `x2c`, `b2c` — one `vllm serve` per session on port 8021, stopped with SIGINT; the backend alternates so the four sessions stay inside one time window |

## Contents

| Path | What it is |
|---|---|
| `PROTOCOL.md` | measurement protocol; amendment A3 defines this series |
| `REPORT-v2.md`, `compact/summary-v2.json` | the v2 report and its JSON of record for the compact series (4056 records, 24 cells, `problems: []`) |
| `REPORT-v2-oldseries.md`, `report-v2-oldseries.json` | corrected accounting of the 2026-10-12 material under the same categories |
| `REPORT.md` | the original v1 report (kept for history; its conditions note points here) |
| `CONCLUSIONS.md` | conclusions, wording aligned with the tasks above |
| `compact/raw-*.jsonl` | raw records, 1014 per session; every record keeps the response text, so validity is re-checkable |
| `compact/server-*.log`, `compact/prov-*.txt` | server logs and per-session provenance (argv, library hash, parsed `StructuredOutputsConfig`) |
| `compact/engine-core-check/` | EngineCore load confirmation for the A3 server configuration (see below) |
| `tools/` | everything needed to reproduce: `run_compact.sh`, `xgrammar_bench2.py` (collector), `xgrammar_common.py` (schemas/prompts), `xgrammar_report.py` (report), `test_xgrammar_report.py` (9 unit tests) |
| `SHA256SUMS` | checksums of every versioned file in this directory |

Reproduce (from the repository root, with the venv and the model cache
of the recorded environment):

```sh
benchmarks/reports/20261013_xgrammar-compare-a3/tools/run_compact.sh   # refuses to run over an existing out dir
benchmarks/local/venv-vllm/bin/python benchmarks/reports/20261013_xgrammar-compare-a3/tools/xgrammar_report.py \
    <raw files...> --out-json summary-v2.json --out-md REPORT-v2.md
```

The collector refuses a core that is not `9a5baf3e…` and a server that
does not report `disable_any_whitespace=True` in its log.

Regeneration from the stored `compact/raw-*.jsonl` was re-checked on
2026-10-13: `summary-v2.json` and `REPORT-v2.md` reproduce exactly
apart from the input paths recorded in the header and the generation
timestamp (all measured values, categories, denominators and
`problems: []` are equal).

## Headline numbers (medians over the sessions; details in `REPORT-v2.md`)

- Batching≥32: xgrammar is faster — TPOT lower by 1.6–3.3× and tokens/s higher by 1.5–2.8× across `w1`/`w2`; at batch 1 both are at parity (`w1`: 191.5 vs 191.7 tok/s; `w3`: 221.9 vs 227.5).
- Output length: on the compact `w2` language bolorgir answers are shorter (39 tokens vs 50 for xgrammar); on `w1` 105/676 bolorgir answers (15.5%) hit the 64-token cap and are not completed documents, xgrammar finishes all.
- First-pass `w2` batch-1 outlier (`b1c` 10.3 s, `b2c` 9.6 s) repeats in both bolorgir sessions; the second repeat is 0.18–0.19 s. It is recorded as an observed outlier; no cause is claimed here.
- Independent re-validation over the stored texts: 4056/4056 agreement, 0 differences; 105 length cuts checked with the bounded suffix test, 0 of them provably completable — cuts are kept, not counted, and not called legal partials from these records.

## EngineCore load confirmation

The per-session `prov-*.txt` files were recorded before the first
structured request, i.e. before the bolorgir adapter loads the core
into the engine process; this is why `manifest.json` carries empty
`engine_maps`. The required confirmation (TZ 6.1) is collected
separately by `tools/engine_core_check.sh` under the same server
configuration: the three batch-1 requests of the A3 matrix are sent,
then every process matching `EngineCore` is inspected via
`/proc/<pid>/maps`. Result (`compact/engine-core-check/`): pid 500351,
`comm=VLLM::EngineCor`, argv `VLLM::EngineCore`, `libbolorgir.so.0.1.0`
mapped with 4 mapping lines, `FOUND=1`; the client records (3, all
`stop`/valid) and the server log are kept next to the scan.

## Traces (not versioned)

The bolorgir sessions also emitted agent traces (one JSONL per
session); they are 24.7 MB each and are kept as local artifacts:

- `benchmarks/local/t10-gpu-1/xgrammar-compare/compact/trace-b1c.jsonl` — sha256 `cc7831a40921e617b1fc833b052326c4d5743175d3c495a470780f7ac654f16e`
- `benchmarks/local/t10-gpu-1/xgrammar-compare/compact/trace-b2c.jsonl` — sha256 `27fc8aa8ed836486798bbe1373a949f4bee649f76f0400bbbb22c9b659150188`

They are diagnostic copies of the engine-side mask emissions; the
records needed for every number in `REPORT-v2.md` are in
`compact/raw-*.jsonl` (text included), and `tools/run_compact.sh`
re-creates the traces.
