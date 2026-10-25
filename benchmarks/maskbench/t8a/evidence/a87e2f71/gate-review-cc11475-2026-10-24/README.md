# Gate review of `cc11475`: completion faults and saved warm data

Artifact unchanged:
`a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6`
(1 268 464 bytes, `a87e2f71` checkpoint). No Zig source was touched, no new
timed measurement was made. This package covers the third gate review, on
top of `cc11475`.

## What the review found

1. **A completion failure was hidden by a rejection.** In
   `maskbench_traces` the `elif rejected` branch ran before the
   terminal-error classification, so an expected-invalid document whose
   `accept` returned `BLG_ERR_INVALID_TOKEN` (6) and whose `finish` then
   returned 8 (resource limit), or whose `can_end` raised, produced
   `PASS` with `completion_errors=0`; the matrix row was green too.
2. **The warm verdict still accepted broken data.** A missing
   `loaded_end`, a `NaN` mean, and a leg holding one observation while
   the protocol claimed 2000 each produced `exit 0`,
   `warm_acceptance.pass=true` and a green matrix row.

The review also observed that an end governor of `performance` passed
because only the presence of a governor was checked, not a declared
policy, and corrected the `[2]` description in the previous package:
with vocabulary `[b'1', b'0', b'']` and EOS id 2, mask word 2 enables
token id 1 (the digit `0`) and excludes EOS.

## What changed

- Terminal execution faults are counted **independently of the semantic
  outcome**: a failed tail fill, a `can_end`/`finish` exception or a
  `finish` code outside `{BLG_OK, BLG_ERR_WRONG_STATE}` increments
  `completion_errors` even when the document was already rejected at the
  input stage. The semantic classification is decided separately, so a
  clean rejection still passes and an equal fault in both arms cannot be
  compared away.
- Warm acceptance fails closed on the data: both library hashes are
  mandatory, `mean` and `p50` must be finite and nonnegative,
  `observations == cache_hits == fill_wall_ns.count == 2000` per leg,
  the complete 20-leg matrix is required, `cpu_now` must equal the pinned
  CPU in both snapshots, and the governor must not change and must match
  the declared `--warm-governor` policy.
- `bench_adr0007.py --verify-warm-json <path> --lib <artifact>` re-judges
  a saved JSON with the expected protocol taken from the command line
  (repeats, observations, threshold, CPU, limits, governor, library
  hash), not from the file.
- The matrix passes `--warm-governor` to the warm row and re-reads
  `conditions.complete` and the declared policy from the written JSON.
- The self-check grew to **77 checks**: the two rejection-plus-terminal
  faults, the `reject-clean` control, the warm missing-end-hash, NaN-mean,
  one-observation and end-governor faults, and three saved-JSON
  verification checks.

## Results

| file | what it is |
|---|---|
| `selfcheck/selfcheck.json`, `console.txt` | **77/77**. Control passes with 410 masks + 1 terminal; `reject-then-finish-error` and `reject-then-can-end-error` fail with `completion x2`; `reject-clean` passes with zero completion/validation errors; warm control passes; missing-load, end-affinity, end-governor, missing-loaded-end, NaN mean and one-observation legs fail both acceptance and the matrix verdict; the saved control JSON re-verifies, the same JSON without `loaded_end` or with a NaN mean fails. |
| `review-after/review-results.json` | the reviewer's `review.py` against this code: `reject-finish-resource` and `reject-can-end-error` now exit non-zero with `completion x2` and a failing matrix verdict; `reject-control` passes; all eight warm injections (`missing-load`, `end-affinity`, `missing-end-hash`, `changed-end-hash`, `nan-mean`, `zero-observations`, `short-observations`, `changed-governor`) fail. |
| `review-before-cc11475/review-results.json` | the same script in a worktree of `cc11475` (artifact directory symlinked read-only): `reject-finish-resource`, `reject-can-end-error`, `missing-end-hash`, `nan-mean`, `zero-observations`, `short-observations` and `changed-governor` all passed there. |
| `review-after/eof-abi.json` | the real pinned ABI case: `accept(1)=0`, `can_end=false`, tail mask `[2]` (digit `0`, EOS excluded), `finish=11`; harness records `rejected:finish:11`, `validation_error=0`. |
| `saved-warm-verify/verify-clean.json` | the **saved** 2026-10-24 acceptance JSON re-judged offline: `pass=true`, 10 legs, means 1085-1267 ns, p50 1052-1236 ns, both hashes `a87e2f71`, `powersave`, `conditions.ok/complete=true`. No new timed run. |
| `saved-warm-verify/{missing-end,nan-mean,short-observations,end-governor}/` | the same saved JSON with one defect each; every `verify.json` has `pass=false`, exit 1, and names the fault. |
| `real-gate/bench_gate.json` | real pinned slice, unchanged: 564 input masks, 24 terminals, `terminal_complete=true`, zero fill/terminal-fill/input/completion/validation errors, trace `2ab0c918…ab61`. |
| `matrix-quick/` | `matrix_adr0007.py --scale quick` on the six functional rows (warm row excluded, as before): **6/6**, 328 Zig tests, 13 curated cases, three campaigns, and the bench-gate verdict. |

Before/after for the reviewer's own cases (`matrix_ok` is the matrix
verdict read from the written JSON):

| case | `cc11475` | this tree |
|---|---|---|
| `reject-finish-resource` | exit 0, matrix ok | exit 1, `completion x2`, matrix fail |
| `reject-can-end-error` | exit 0, matrix ok | exit 1, `completion x2`, matrix fail |
| `reject-control` | exit 0, matrix ok | exit 0, matrix ok |
| `warm-missing-end-hash` | pass, matrix ok | fail, matrix fail |
| `warm-nan-mean` | pass, matrix ok | fail, matrix fail |
| `warm-zero-observations` | pass, matrix ok | fail, matrix fail |
| `warm-short-observations` | pass, matrix ok | fail, matrix fail |
| `warm-changed-governor` | pass, matrix ok | fail, matrix fail |
| `warm-control` | pass, matrix ok | pass, matrix ok |

The saved acceptance data contain none of the injected defects: all 20
legs hold 2000 observations / 2000 hits / count 2000, both hashes are
`a87e2f71…15e6`, start/end affinity and `cpu_now` are 7, the governor is
`powersave` at both snapshots, and all ten means are 1085-1267 ns.
Re-verification therefore did not need a new timed attempt. The stored
`2d982b60` FAIL remains unchanged and is not reinterpreted. The
full-scale Decision 4 matrix is still outstanding.

## Textual correction

The previous package's README described the tail mask `[2]` as
`(EOS only)`. It enables token id 1 - the digit `0` - and excludes EOS;
that README line is corrected and its `SHA256SUMS` entry updated. The
evidence JSON and the first paragraph were already correct.

## Commands

```sh
python3 -B benchmarks/maskbench/t8a/gate_selfcheck.py --out <dir>

PYTHONPATH="$PWD/python:$PWD/benchmarks/maskbench/t8a:$PWD/benchmarks/external/jsonschemabench-maskbench/maskbench" \
HF_HOME="$PWD/benchmarks/local/hf-cache" HF_HUB_OFFLINE=1 \
benchmarks/local/t8a-work-9a5baf3e/venv-pinned/bin/python \
  benchmarks/maskbench/t8a/matrix_adr0007.py --scale quick \
  --only zig-three-way fastpath-curated fastpath-fuzz \
         differential-reference cache-onoff bench-gate \
  --lib "$PWD/benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0" \
  --out <dir>

python3 -B benchmarks/bench_adr0007.py \
  --verify-warm-json benchmarks/maskbench/t8a/evidence/a87e2f71/terminal-warm-2026-10-24/warm-acceptance/artifacts/warm_acceptance.json \
  --lib "$PWD/benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0" \
  --warm-repeats 5 --warm-observations 2000 --warm-cpu 7 \
  --warm-mean-threshold-ns 1300 --warm-max-load1 2.0 \
  --warm-max-cpu-pressure 0.5 --warm-governor powersave --out <dir>/verify.json

# negative control
git worktree add --detach /tmp/wt-cc11475 cc11475
ln -s "$PWD/benchmarks/local" /tmp/wt-cc11475/benchmarks/local
(cd /tmp/wt-cc11475 && python3 -B <review.py>)
```

The review scripts were run unmodified from a copy of the review
directory; the worktree only needed `benchmarks/local` (untracked
artifact store) symlinked.
