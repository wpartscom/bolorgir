# Gate review of `1f819bc`: statusless terminal exceptions

Artifact unchanged:
`a87e2f711fb31d54fc06c67d194fd43237065f01ca0bc0cc977aa472504515e6`.
No Zig source was touched, no new timed measurement was made. This package
covers the fourth gate review, on top of `1f819bc`.

## What the review found

`maskbench_traces` recorded a `can_end`/`finish` exception as
`getattr(exc, "status", None)`. A plain `RuntimeError` or `MemoryError`
has no `status` attribute, so the field stayed `None` and the exception
was indistinguishable from "no exception". After an expected rejection
(`accept=6`) with `finish=11` both arms then reported
`completion_errors=0`, the benchmark exited 0 and the matrix row was
green. The same script showed the status-bearing variant already failed,
so the gap was exactly the optional attribute.

## What changed

- `_terminal_error(exc)` records the fact of the exception by type and
  keeps the numeric `status` when present: `RuntimeError`,
  `MemoryError`, or `RuntimeError(8)`. `maskbench_traces` uses it for the
  tail fill, `can_end` and `finish`; the completion fault is decided by
  "an exception occurred", never by the optional code.
- The terminal trace keeps the value, so a raised call is visible in the
  compared terminals and in the trace digest.
- The self-check adds `reject-then-can-end-no-status` (RuntimeError),
  `reject-then-can-end-memory` (MemoryError), `reject-then-finish-no-status`
  (RuntimeError) and two direct checks of the recorder: **91/91**.

## Results

| file | what it is |
|---|---|
| `selfcheck/selfcheck.json`, `console.txt` | **91/91**. The three statusless cases fail with `completion x2`, leave evidence and write no success JSON; `_terminal_error` records `PlainFault` and `PlainFault(8)`; all previous checks still pass. |
| `exception-review-after/review-results.json` | the reviewer's `exception-review/review.py` against this tree: `reject-can-end-no-status` and `reject-can-end-memory-error` exit non-zero with `completion x2` and a failing matrix verdict; `control`, `reject-control`, `cannot-end`, `tail-mask`, `finish-resource`, `reject-finish-resource` and `reject-can-end-error` behave as before. |
| `exception-review-before-1f819bc/review-results.json` | the same script in a worktree of `1f819bc`: both statusless cases exited 0 with `completion_errors=0` and `matrix_ok=true` (their success JSONs are kept there). |
| `real-gate/bench_gate.json` | real pinned slice, unchanged: 564 input masks, 24 terminals, `terminal_complete=true`, zero fill/terminal-fill/input/completion/validation errors, trace `2ab0c918…ab61`. |

Before/after for the reviewer's cases (`matrix_ok` is the matrix verdict
read from the written JSON):

| case | `1f819bc` | this tree |
|---|---|---|
| `reject-can-end-no-status` (RuntimeError) | exit 0, completion 0, matrix ok | exit 1, `completion x2`, matrix fail |
| `reject-can-end-memory-error` (MemoryError) | exit 0, completion 0, matrix ok | exit 1, `completion x2`, matrix fail |
| `reject-can-end-error` (status 8) | exit 1, matrix fail | exit 1, matrix fail |
| `reject-control` | exit 0, matrix ok | exit 0, matrix ok |

The saved warm acceptance was not re-run: it contains no exceptions, was
already re-judged by the current validator, and remains valid. The stored
`2d982b60` FAIL is untouched. The full-scale Decision 4 matrix is still
outstanding.

## Commands

```sh
python3 -B benchmarks/maskbench/t8a/gate_selfcheck.py --out <dir>

python3 -B /tmp/bolorgir-review-1f819bc-nn_0842q/exception-review/review.py

PYTHONPATH="$PWD/python:$PWD/benchmarks/maskbench/t8a:$PWD/benchmarks/external/jsonschemabench-maskbench/maskbench" \
HF_HOME="$PWD/benchmarks/local/hf-cache" HF_HUB_OFFLINE=1 \
benchmarks/local/t8a-work-9a5baf3e/venv-pinned/bin/python \
  benchmarks/maskbench/t8a/matrix_adr0007.py --scale quick --only bench-gate \
  --lib "$PWD/benchmarks/local/t8a-checkpoints/a87e2f71/libbolorgir.so.0.1.0" \
  --out <dir>

# negative control
git worktree add --detach /tmp/wt-1f819bc 1f819bc
ln -s "$PWD/benchmarks/local" /tmp/wt-1f819bc/benchmarks/local
(cd /tmp/wt-1f819bc && python3 -B <copy of review.py>)
```

The review script was run unmodified from a copy; its outputs for the
before side are in `exception-review-before-1f819bc/`.
