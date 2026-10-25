# Abort-phase recording check: the ui5 reset crash

The T8a runner now records the phase of a call that raised.

- tool: `benchmarks/maskbench/t8a/paired_runner.py`, sha256
  `09cc2c7e6679a258c5da2409aaccd43a1a77216d6187457900d2f4e5566c168d`
- core: the run's wheel in `venv-pinned` (9a5baf3e), unchanged
- file: `JsonSchemaStore---ui5.yaml.json`, the parser-state crash of the run
- command shape: `diag_runner --multi --blg --compact` with the run's
  protocol (one thread, the run's tokenizer and environment)

Result: exit 1 (the crash is unchanged) and the pending marker carries the
cause:

```json
{
  "pending_file": 1,
  "aborted": {
    "phase": "reset",
    "at": 0,
    "error": "ResourceLimitError",
    "instance": 0
  }
}
```

The marker stays a marker, so `status_of` still classifies the file as
`crash_or_timeout`; the difference from the run is that the cause is named.
The tracked test
`tests/test_t8a_tools.py::test_abort_marker_names_the_phase_and_keeps_the_pending_status`
raises in each of `reset`, `compute_mask` and `commit_token` and asserts the
same marker shape and the instance-level copy.

Consequence for the old work dir: `diag.py` and `diag_repeat.py` compare the
current tool hashes with the run's manifest and now refuse; the old tool is
in git history (sha256 `25084c73bdfaf319cbec7fb413325e7233be3b7ca9408518c1284d7e579721d0`).
A new run records the new hash through `t8a_identity.py` and needs its own
work directory.
