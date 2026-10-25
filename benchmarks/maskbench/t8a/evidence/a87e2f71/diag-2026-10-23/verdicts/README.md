# Verdict checks

- `owner-fast-memory-test.zig`: the owner's read-only fast-path
  reproduction (appended to an isolated `mask.zig`); its fixture is the
  regression `fast path: the certificate cache cannot fail a fast fill`.
- `warm_exit_replay.py`: the owner's read-only replay of the saved
  `2d982b60` warm data through the current harness. It takes no new
  timing. `equiv/warm_exit_replay.stderr.txt` shows exit 1 and the five
  threshold problems; `equiv/warm_acceptance_replay.json` has
  `status=FAIL`, `warm_acceptance.pass=false`.
- `matrix-verdict-check.txt`: `matrix_adr0007.run_job` on three JSON
  files: the replayed warm verdict (`pass=false` -> row `fail`), the
  previous `--skip-maskbench` gate JSON (no `gate.maskbench_states` ->
  row `fail`) and the new gate JSON (425 states -> row `pass`).
- `matrix-overall-check.txt`: the same two rows through `write_report`:
  the gate row passes, the warm row fails, and Overall is **FAIL**
  (1/2 jobs pass), with the failed verdict listed under "JSON verdicts".
