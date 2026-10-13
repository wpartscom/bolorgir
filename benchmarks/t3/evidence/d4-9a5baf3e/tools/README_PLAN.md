# D4 round of the candidate 9a5baf3e: plan and measurements (fixed before the run)

Set: `benchmarks/coverage/spec-v1/t3-9a5baf3e` (T3 acceptance run `acc-9a5baf3e`,
RUN_DONE exit=0 2026-10-13T06:38:39Z). 81 transition rows, 67 results:
42 `invalid_document` rows (34 results, D4-6), 27 `incomplete` rows
(25 results; 23 with an entry, the two GPT-2 results without one),
12 `engine_unavailable` rows (8 results, all with an entry).

This round re-verifies, on the candidate's own bytes, every cause the D4
list states; the earlier R11 measurements named the earlier candidate
6e62e0d1, and a hash swap is not evidence. One measurement at a time,
nothing else heavy running; the core stays frozen (`9a5baf3e`, HEAD
`003f1b3`, nothing rebuilt).

## 1. Recorded data (no replays needed)

- D4-6: the T2c triage of this candidate (review/triage.json, gate
  `17e25bba4f1c6144`): every invalid_document result is class D with all
  its invalid documents present and checked (68 documents over 34
  results). Checked against the review's transitions by the acceptance
  tool itself.
- The engine_unavailable passes of 9a5baf3e repeat the recorded steps of
  6e62e0d1 on all 8 results (pass sets and steps checked): the pass sets
  are the ones R11-C replayed.

## 2. R11-A2: pass 1 of the 25 incomplete results (`r11a2.sh`)

- Libraries: `cand` (9a5baf3e, release recipe -Dcpu=baseline), `native`
  (the same source, -Dcpu=native; `build_native.sh`), `prev` (6e62e0d1,
  the previous accepted candidate, same recipe), `50cbd80e`,
  `7345490f`, `dbc5eeb` (host builds).
- Items: `items-r11a2.json` - all 25 incomplete results with the
  candidate's recorded pass-1 limit step `c_star` (16/32/48). Pass 1 is
  deterministic (no seed); the documents must be equal on all libraries.
- Harness: the run's own tree (`acc-9a5baf3e/tree`), its Python 3.12.15
  venv, its HF snapshot offline; tokenizer specs exported once from the
  run (`export_specs.py` -> `r11a2_specs.json`).
- Conditions: one process per library and round on CPU 2, nice 0, load
  average below 1.5 before each; 3 rounds, order rotated per round.
  Time: M = elapsed at `c_star` (the end time when the pass ends before
  it); median over rounds; noise = median max/min - 1, threshold
  max(2 %, noise). 25 items x 6 libraries x 3 rounds ~ 100-110 min.

### Amendment A1 (2026-10-13, written before the first measurement process ran)

R11-A's gate (1-minute load average below 1.5 before each process)
cannot pass in this session: with no project or other heavy work running
(checked: no replays, builds or GPU work; only the editor session is
alive), the editor alone keeps the 1-minute average at 1.7-3.0. The
first launch waited ten minutes without one sample below 1.5 and was
stopped before any measurement. The gate is raised for this round to
`R11_LOAD_MAX=2.5` (the scripts' default stays 1.5; the launches pass
the variable); everything else is unchanged - CPU 2, nice 0, one process
at a time, three rounds, and every process records its `loadavg_start`
in its JSONL head (the summary of the round can therefore state the
conditions actually measured). A later quieter round can repeat the
replays under the original gate; the numbers to compare are the ratios
within one round, which the common conditions of a round carry.

### Amendment A1b (2026-10-13, before the remaining R11-C2/B2 replays)

The session's idle baseline (editor open, no project work) samples at
1.9-3.0 in the 1-minute average; with A1's 2.5 the R11-C2 gate sequence
stalled twice at ~2.6-2.8 and the first replays had to wait. A1b raises
the gate to `R11_LOAD_MAX=3.0` for the remaining R11-C2/B2 replays only
(the C replays compare outcome, stage, code, step and prefix with the
record; the B replays ask whether a 300 s budget reaches a recorded
state - criteria insensitive to a few percent of background load; no
pass-1 timing margins are measured in them). The R11-A2 series ran under
A1 (2.5) unchanged, and its 25x6x3 measurements are not affected by A1b.

- Decision rule (as R11-A for the group cause): target = M(cand)/M(native)
  beyond the threshold and code = M(native)/M(50cbd80e) not: the release
  CPU target; code beyond: the code of stages C/D; neither: the run's
  conditions. New column `cand/6e62e0d1` (context only): what changed
  between the two candidates on pass 1; with the recorded sidecar
  comparison (steps of the time-limited passes 2-20, same seeds) it
  separates a code regression from the budget-edge conditions.

## 3. R11-C2 / R11-B2: byte engine_unavailable passes (`r11bc2.sh`)

- C: every engine_unavailable pass of the 8 results on 9a5baf3e, 10 s,
  recorded seeds: must reproduce the record (outcome, stage, code, step,
  prefix length) and returns the engine message with the undecided
  histogram (the cause of D4-3/3m/4/5/10/11/11m/12). ~10 min.
- B: MaskBench bigquery-table passes 5 and 12 on dbc5eeb and 7345490f
  with 300 s and on 50cbd80e with 10 s: the lines must reach the
  candidate's state (same step and prefix) given the time. ~20 min.

## 4. The two results without an entry (owner question of 2026-10-13)

`Github_easy---o64825.json` (gpt2) and `Github_easy---o68314.json` (gpt2):
the line 7345490f completed pass 1 (cv@55 / cv@39), the candidate recorded
the 10 s limit at step 48 / 32, on 6e62e0d1 both completed (cv@55 / cv@39).
The recorded sidecars show the time-limited passes 2-20 running at the
same steps on both candidates; R11-A2 adds the quiet-core times of pass 1
on all six libraries. The proposal to the owner follows from these numbers
(accept as a documented budget-edge residual of the load, or a protocol
change: a reproducible pass-1 budget), not from the `C` label.

## 5. After the measurements

Update `benchmarks/t3/d4_proposals.json` for 9a5baf3e (candidate hash,
per-entry re-checked texts and rows, two new entries for the GPT-2
results, the retired entries named), copy the summaries into
`benchmarks/t3/evidence/` (`r11a2_pass1_summary.*`,
`r11bc2_summary.*`), and hand the owner the decision package
(`d4_package_9a5baf3e.md`: rows, cause, evidence, consequences per
proposed decision). owner_decision stays null everywhere.
