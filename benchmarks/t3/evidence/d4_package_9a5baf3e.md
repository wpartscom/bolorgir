# D4 decision package for the candidate 9a5baf3e

Public decision package (audit-fix spec T3 item 2, TZ sections 3–4 of
2026-10-13). It accompanies the updated list `benchmarks/t3/d4_proposals.json`
(revision for 9a5baf3e, full candidate sha256
`9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41`) and the
preliminary R6 review rebuilt from the saved data.

**Nothing here is an approval.** `owner_decision` is `null` in every entry;
only the owner fills decisions, in the format below. The previous approvals
for `6e62e0d1` do not carry over to this candidate.

## State of the record (2026-10-13)

- T3 acceptance run `benchmarks/local/t3/acc-9a5baf3e`: `RUN_DONE exit=0`,
  2026-10-13T06:38:39Z; set published as `benchmarks/coverage/spec-v1/t3-9a5baf3e`.
- Preliminary R6 from the saved reports (`benchmarks/t3/r6_review.sh`, out dir
  `benchmarks/local/t3/r6-9a5baf3e-d4`, console and bundle copied to
  `benchmarks/t3/evidence/d4-preliminary-r6/`):

  `R6_REVIEW_DONE verdict=not_admitted exit=1` — the **only** failing check is
  `transitions_approved` (pending 81). `transitions_described` is **ok**
  (81 transition rows, 0 results without a D4 entry), `d4_candidate` is ok
  (the list names this candidate), `triage` is ok (gate `17e25bba4f1c6144`,
  identical to the published review's triage).

- Coverage matrix: `benchmarks/t3/evidence/d4_transitions_9a5baf3e.md` /
  `.json` — all 81 rows and the 67 unique results with the comparison line,
  the outcome on the previous candidate, the D4 entry and the evidence
  pointer; counters re-checked against the review
  (42 invalid_document / 34 results / 68 documents; 27 incomplete / 25;
  12 engine_unavailable / 8; 41 rows against dbc5eeb, 40 against 7345490f).

## How the causes were re-verified on this candidate

- **R11-A2** (2026-10-13): pass 1 of all 25 incomplete results on six
  libraries, three rounds, one process at a time, pinned to CPU 2, with the
  run's own tree, Python and HF snapshot. Libraries: candidate `9a5baf3e…`
  (release recipe `-Dcpu=baseline`), the same source with `-Dcpu=native`
  `26697a9bd7e25b5b3594d1940db7d50d333c3a4bc3622f626d2c1b79a7d4a719`
  (diagnostic, 4347 vector instructions against 0; never a candidate), the
  previous candidate `6e62e0d1526c6e53…`, `50cbd80e874eac5d…`,
  `7345490f7ddf5710…`, `dbc5eeb8ff970d4…` (host builds of their rounds).
  Summary: `benchmarks/t3/evidence/r11a2_pass1_summary.{md,json}`
  (raw replays and tools: `benchmarks/t3/evidence/d4-9a5baf3e/`).
  Headline: the pass-1 document is identical on every library in 25/25
  results; the candidate needs a median 7.1 % more time than its own
  host-target build at the deciding check (range 1.046–1.100; 25/25 above 1),
  the source's code ratio against 50cbd80e is 1.027 (inside the noise
  threshold 4.5 %), and the two candidates differ by 1.003. Round-to-round
  noise 4.53 %, so the pre-registered reading rule names the release CPU
  target (`-Dcpu=baseline`, no AVX2/POPCNT) as the **leading hypothesis**;
  the threshold is a heuristic, not a confidence interval, and the rule
  cannot establish the cause of individual flips.
- **R11-C2/B2** (2026-10-13): every recorded `engine_unavailable` pass of the
  candidate re-run with the recorded seed and 10 s — all 19 passes reproduce
  outcome, stage, code, step and prefix length **as recorded** (engine
  message "mask computation limit exceeded", undecided histograms per pass);
  the same passes on `50cbd80e` reach the same states; the MaskBench
  bigquery-table passes 5 and 12 reach the candidate's state on both lines
  within 300 s (54.7 s / 155.0 s on dbc5eeb, 54.8 s / 155.4 s on 7345490f)
  and on 50cbd80e within 10 s (5.8 s / 8.5 s). Summary:
  `benchmarks/t3/evidence/r11bc2_summary.{md,json}`.
- **Triage of the candidate**: `benchmarks/coverage/spec-v1/t3-9a5baf3e/review/triage.{md,json}`
  — all 34 `invalid_document` results of the transitions are class D (the
  draft-04 integer spelling, `docs/supported_features.md` section 9) with all
  68 invalid documents present and checked.

Conditions and limits (recorded in `benchmarks/local/t3/de8/d4f/README_PLAN.md`,
copied into `benchmarks/t3/evidence/d4-9a5baf3e/tools/`): amendment A1 raised
the pre-process load gate from R11-A's 1.5 to `R11_LOAD_MAX=2.5` for R11-A2
(the editor session keeps the 1-minute average at ~1.7–3.0 with no other
work; every process recorded `loadavg_start`), A1b raised it to 3.0 for the
remaining C2/B2 replays only (their criteria are state-based, not timing
margins). A single pre-process load sample does not prove the absence of CPU
contention during a measurement; the ratios within one round carry the
comparison. A same document hash proves equal documents; token paths and
masks were not recorded and no mask equality is claimed. Where the historical
record stores only the prefix *length*, byte equality is not claimed.

## Recommendations (summary)

| # | Group | Rows / results | Recommendation | Alternative | If accepted |
|---|---|---|---|---|---|
| 1 | D4-6 (class D invalid documents) | 42 / 34 | Accept as the documented class D deviation (`requires_triage_class: D`; the candidate's triage holds) | Fix the draft-04 integer-spelling deviation (new candidate + new acceptance) | These 34 results stay `invalid_document`; rare generated drafts spell integers with fraction/exponent and the draft-04 reference rejects them |
| 2 | pass1 budget edge (D4-13…D4-38) | 24 rows / 22 results | Accept as a timing transition of the portable release target (R11-A2 numbers in each entry) | Release a vectored target — a new candidate and a new R6 (owner's choice) | Under an 8-job run some first passes record a 10 s limit where the line completed; completeness of the semantic coverage for those results, not correctness |
| 3 | the two GPT-2 results (D4-39, D4-40) | 2 / 2 | Accept as the same budget-edge class **or** pick the protocol option (see below) — decided separately | Protocol change: a pass-1 budget/retry rule the loaded run can meet (checked against these replays); not a candidate fix | As row 2; the outcome is decided by run conditions at the check, hypothesis stated as such |
| 4 | engine_unavailable (D4-3…D4-12) | 12 / 8 | Accept as documented limitations (RESOURCE_LIMIT on byte-level oneOf/uniqueItems paths) | Group-certificate work (future scope) | Rare resource refusals on specific schemas; recorded and reproducible, not silent |
| 5 | D4-2 (kb_1154 expensive pass 1) | 1 / 1 | Accept as a documented availability residual | Same as row 2 (a faster target would need >17 s → still over budget on the lines' build) | One MaskBench result stays incomplete; cause measured, historical line completion unexplained |
| 6 | D4-15, D4-16, D4-24, D4-33 | 0 (retired) | No decision on this candidate (no transition) | — | None; kept for the record |
| 7 | D4-1, D4-2r, D4-7…D4-9 | 0 (record) | No decision; kept for the record | — | None |

## The two GPT-2 results in detail (TZ section 3.2)

`Github_easy---o64825.json` and `Github_easy---o68314.json` (MaskBench, GPT-2):

- recorded runs: the line 7345490f completed pass 1 at step 55 / 39 (its
  first run, 30 s schema budget); the previous candidate `6e62e0d1` completed
  the same pass at the same steps; the candidate `9a5baf3e` recorded the
  10 s limit at step 48 / 32 (the first budget check it failed). All other
  passes end in the time limit on every build; in the recorded runs the
  time-limited passes 2–20 of the two candidates reach the same steps (same
  seeds; median step ratio 1.000 over 19 passes — compatible with, but not proof
  of, equal speed; the historical record keeps the prefix length only, not
  its bytes).
- quiet-core replays (R11-A2, CPU 2, per-round times at the deciding check):

  | library | o64825 (c\*=48) | o68314 (c\*=32) |
  |---|---|---|
  | candidate 9a5baf3e | 7.86 / 7.37 / 7.29 s (spread 7.7 %) | 7.61 / 7.34 / 7.56 s (spread 3.7 %) |
  | native build | 7.01 / 6.79 / 7.22 s | 7.17 / 6.95 / 7.23 s |
  | 6e62e0d1 | 7.38 / 7.49 / 7.26 s | 7.25 / 7.31 / 7.31 s |
  | 50cbd80e | 6.76 / 6.89 / 6.75 s | 6.79 / 6.97 / 6.90 s |
  | 7345490f | 6.89 / 6.84 / 6.97 s | 6.90 / 6.65 / 6.83 s |
  | dbc5eeb | 6.79 / 6.79 / 7.16 s | 6.98 / 6.90 / 7.35 s |

  Every library completes the same pass-1 document well under the budget on a
  quiet core (≥ 1.9 s headroom even for the slowest round of the candidate);
  the terminal steps are 55 and 39 on every library.
- what this supports and what it does not: equal steps of passes 2–20 and a
  1.003 median ratio between the candidates are **compatible with** the
  budget-edge explanation (the loaded run decides the check), and the two
  recorded flips go in both directions across the round (four rows completed
  on 9a5baf3e that recorded a limit on 6e62e0d1: D4-15, D4-16, D4-24, D4-33).
  A small timing difference between the two candidates is **not excluded**,
  and the cause of the individual flips **is not established** beyond this.
  The entries state exactly that (hypothesis, limits).
- options for the owner: (a) accept as a documented budget-edge residual of
  the portable target (consistent with row 2; no further work for the release
  as planned); (b) ask for a protocol decision — a pass-1 rule the loaded run
  can meet reproducibly (e.g. a budget chosen from these quiet-core margins
  or a retry rule), checked in advance against these replays and applied to a
  next run; explicitly **not** a rescoring of the recorded results, which
  stay as measured.

## Engine-unavailable details (D4-3…D4-12)

Recorded passes and the re-run diagnostics (all as recorded, R11-C2):

| entry | result | passes (candidate) | undecided reasons (re-run histogram) |
|---|---|---|---|
| D4-3 | repo tye-schema (byte) | 3@898, 6@567, 7@2644, 11@4658, 15@3015 | fail_search 1 + comb_group 8 / 5 / 3 per pass |
| D4-3m | MaskBench tye-schema | 4@4926, 8@107, 9@581, 13@2082, 16@4433, 19@4866 | fail_search 1 + comb_group 7 / 8 |
| D4-4 | MaskBench o32934 | 8@38, 13@52 | fail_search 1, comb_group 18, openobj_deps 2, openobj_gate 2 |
| D4-5 | MaskBench cryproj.53 | 18@662 | fail_search 1 (the |exp| ≤ 400 limit inside uniqueItems) |
| D4-10 | repo o31351 | 8@6225 | fail_search 1, comb_group 3 |
| D4-11 | repo bigquery-table | 19@1582 | fail_search 1, comb_group 3 |
| D4-11m | MaskBench bigquery-table | 5@1109, 12@2125 | fail_search 1, comb_group 3 |
| D4-12 | repo cryproj.dev | 10@5725 | fail_search 1 |

The lines reach the same states given the time (R11-B2 and the earlier R8
replays); the repository results end exactly as 50cbd80e (R11-C2 C50 table).

## How to decide

Fill `owner_decision` in each entry of `benchmarks/t3/d4_proposals.json`
(format from `decision_format` in the file): `{"decision": "accept"|"reject",
"candidate_library_sha256": "9a5baf3e…", "rows": [{"corpus": …, "file": …,
"tokenizer": …, "outcome": …}, …], "by": …, "date": …, "note": …}`. A wildcard
is never a row; the list must name the exact rows being accepted (the
`candidate_rows` of each entry are the rows of this candidate). After the
decision, the R6 review is rebuilt from the saved data
(`r6_review.sh` with the decided list); the verdict becomes `admitted` only
if every row is described and properly approved. A rejected row keeps the
review closed.

## Evidence index (this round)

- `benchmarks/t3/d4_proposals.json` — updated list (43 entries, decisions null).
- `benchmarks/t3/evidence/d4_transitions_9a5baf3e.{md,json}` — coverage matrix.
- `benchmarks/t3/evidence/r11a2_pass1_summary.{md,json}` — pass-1 replays.
- `benchmarks/t3/evidence/r11bc2_summary.{md,json}` — engine_unavailable replays.
- `benchmarks/t3/evidence/d4-preliminary-r6/` — acceptance.json/md, triage,
  tools, console, SHA256SUMS of the preliminary review.
- `benchmarks/t3/evidence/d4-9a5baf3e/` — tools of the round, raw replay
  archives (`raw-r11a2.tar.gz`, `raw-r11bc2.tar.gz`), index README and
  SHA256SUMS.
- Historical (unchanged): `benchmarks/t3/evidence.md` section R11 (the
  6e62e0d1 round), `benchmarks/t3/evidence/r11a_pass1_summary.md`.
