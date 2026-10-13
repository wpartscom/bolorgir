# XGrammar comparison: narrative and conclusions (TZ section 10)

Companion to `PROTOCOL.md` (conditions, pinned before collection) and
`REPORT.md` (tables generated from `summary.json`). This file interprets
the numbers; it does not replace the tables.

## What was compared

Two structured-output backends of the *same* patched vLLM 0.31.0 on
`Qwen/Qwen2.5-0.5B-Instruct` (RTX 3060, greedy, prefix caching off):

- **bolorgir** - the candidate core `9a5baf3e…` with the vLLM adapter
  `7bbee811…`, sessions `b1`, `b2`;
- **xgrammar** - the packaged xgrammar 0.2.7 of the serving venv,
  sessions `x1`, `x2`.

Four sessions × full matrix (3 workloads × batch 1/8/32/128 × 2 repeats)
= **4056 HTTP-200 records**, collected in the order `x1, b1, x2, b2`
under amendment A2 (see `PROTOCOL.md`; A1/A2 written before the
affected collections). Per-session evidence: `raw-<session>.jsonl`,
server logs `server-<session>.log`, bolorgir agent traces
`trace-<session>.jsonl`, provenance `prov-<session>.log`.

## Correctness side (the primary result)

- **0 constraint breaches** in the 4056 HTTP-200 records: every
  document that finished with `stop` is schema-valid (jsonschema for
  `w1`/`w2`, full regex match for `w3`), **0 non-200 HTTP responses**,
  **0 client errors** in either backend. A record cut by `max_tokens`
  (`finish=length`) is not a completed document and is not counted as
  one (counting rules and denominators: `REPORT-v2-oldseries.md`).
- bolorgir `w1`: 107/676 runs were cut by the 64-token cap
  (`finish=length`) and are **not counted as completed documents**. The
  stored records keep `text_len` only (84 on all 107) and no answer
  text, so a bounded completion check or a prefix byte comparison
  cannot be re-done from the record; the live-run observation behind
  A1 (the `0.5…` path: the certificate keeps `0` alive for
  `minimum: 1` because `0.5e1 = 5` is a completing spelling) is
  recorded in `PROTOCOL.md` as an observation, not as a stored check.
  xgrammar blocks `0` at the first digit, so its answers always stop
  (19 tokens median vs 12 for bolorgir - the compact language).
- **Language difference, not a defect**: xgrammar allows whitespace by
  default (pretty-printed answers, `w2` median 78 tokens), bolorgir
  compiles the compact JSON language (median 39 tokens; the adapter
  refuses `whitespace_pattern`, a documented refusal). Both languages
  only ever complete valid documents. Amendment A2 (raise `w2` cap to
  128) was written to compare timing on equal footing after the 64-cap
  proved to cut every pretty-printed `w2` answer of an honest run.

## Performance side (medians of the four sessions)

TTFT (p50, batch 1): `w1` 17.6 ms bolorgir vs 12.8 ms xgrammar,
`w2` 17.0 vs 17.0, `w3` 15.4 vs 17.3 - no systematic winner at
batch 1 (13–18 ms both). At batch 128 xgrammar is ahead by 8–15%
(e.g. `w1` 313.6 vs 267.6 ms).

Per-step cost (TPOT, p50) and throughput (completion tokens / batch
wall) - xgrammar-to-bolorgir ratio:

| workload | batch | tok/s ratio | TPOT ratio |
|---|---|---|---|
| `w1_json_small` | 1 | 1.08× | 0.98× |
| `w1_json_small` | 8 | 2.64× | 0.97× |
| `w1_json_small` | 32 | 1.52× | 1.65× |
| `w1_json_small` | 128 | 1.86× | 2.68× |
| `w2_json_nested` | 1 | 2.28× | 31.3×* |
| `w2_json_nested` | 8 | 2.16× | 9.70×* |
| `w2_json_nested` | 32 | 1.74× | 1.56× |
| `w2_json_nested` | 128 | 2.34× | 2.42× |
| `w3_regex64` | 1 | 0.99× | 1.00× |
| `w3_regex64` | 8 | 1.01× | 1.06× |
| `w3_regex64` | 32 | 1.69× | 1.92× |
| `w3_regex64` | 128 | 2.75× | 3.32× |

\* the two `w2` rows at batch 1/8 are dominated by the bolorgir
first-pass effect below, not by steady-state cost.

Reading:

- at batch 1–8 the backends are **equal in steps/s** for `w1`/`w3`
  (TPOT 4.3–5.2 ms both) - the constraint work is not the bottleneck
  (the `w2` first-pass caveat below applies);
- at batch ≥ 32 xgrammar gains: in this first series TPOT is 1.6–3.3×
  lower and batch throughput 1.5–2.8× higher; the like-for-like
  compact passes give the same direction with 1.4–3.4× / 1.3–2.7×
  (two passes; see `compact/`, `compact-r2/`). TPOT is the client-observed per-token
  time `(e2e − TTFT)/(tokens − 1)`; it includes engine and delivery
  overheads and is **not a profiled mask cost**, so the gap is
  reported as measured and its attribution to mask computation is left
  to a dedicated profiling step (not done here);
- in tokens terms the compact language partly compensates: a `w2`
  answer is 39 tokens (bolorgir) vs 78 (xgrammar), so end-to-end
  latency of one honest request stays comparable for small batches.

## Outlier: bolorgir `w2` first pass at small batches

Reproduced in all four bolorgir sessions of the two passes (narrow
spread within each pass ⇒ systematic, not noise): `w2` `rep=0` at
batch 1 costs ≈10 s (9.60–10.33 s wall; ≈262–268 ms/token), batch 8
≈3.5 s wall; `rep=1` is normal (0.18–0.19 s at batch 1; 4.5 / 5.2
ms/token). xgrammar shows no such first-pass cost
(4.3–5.2 ms/token in all four cells). At batch ≥ 32 both repeats are
normal (11.4 and 43.3 ms/token), so the effect is amortised/absorbed
under load. All these runs are valid; the report medians include them
by the pinned rule. A plausible mechanism (not proven; the agent trace
carries no timestamps) is the adaptive mask cache of the candidate core
filling up on the first traversal of the nested-grammar states. The
effect affects neither validity nor any acceptance check; a dedicated
investigation is noted as follow-up work.

## Scope limits

One model (0.5B), one GPU, greedy decoding, no speculative decoding
(for that comparison see the round-5/7 functional matrix, TZ 18), no
prefix cache. Absolute numbers do not transfer to other hardware; the
structural result (steady-state parity at batch 1–8 for `w1`/`w3`,
xgrammar ahead under high concurrency) is the transferable part.

## Bottom line for TZ section 10

The comparison report exists with the requested dimensions - TTFT,
throughput, per-step cost, batch 1/8/32/128 and three request schemas
(JSON schema small, JSON schema nested, regex) - measured in the same
vLLM, same model, same box, with per-session provenance. Of the 4056
HTTP-200 records, every completed (`stop`) document is valid in both
backends; 107 bolorgir `w1` answers were cut by the 64-token cap and
are kept as cuts, not completed documents. The 2026-10-12 series
compared different JSON languages (the xgrammar sessions ran with the
vLLM default `disable_any_whitespace=False`; the pretty-printed `w2`
median is 78 tokens against the compact 39). The like-for-like compact
series (amendments A3/A4, 2026-10-13; two passes, the repeat pass
with per-session EngineCore binding) and the corrected accounting of
both series are in `compact/`, `compact-r2/` (`REPORT-v2.md`,
`summary-v2.json`) and in `REPORT-v2-oldseries.md`. The repeat pass
reproduces the figures (categories identical except 105 → 107 `w1`
cuts; timing cells within run-to-run spread). Bolorgir is at parity
for latency-oriented small batches in steady state (its first `w2`
pass costs ≈10 s; see the outlier section) and slower under high
concurrency, and it provides the certificate-driven `0.5e1`
completion path that xgrammar does not take.
