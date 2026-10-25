# Mask-path diagnostics of the T8a run

Tools that produced the numbers in
`../evidence/9a5baf3e/diag-2026-10-22/` and in
`docs/mask-fast-path-needs-reachability.md`. They read the recorded run under
`benchmarks/local/t8a-work-9a5baf3e-w12` (or any work directory with the same
layout) and never modify it.

| file | what it does |
|---|---|
| `mask_equiv.py` | replays corpus files through the recorded T8a protocol and compares `consumed` / `rejected_at` / `n_ids` per instance with the run's streams; measures every call, including the duration of a call that raised, and reports the refusal phase (`compute_mask` / `commit_token`), mask time, wall time and the TBM percentiles. `--allow-missing-stream` replays files whose stream the run never finished (timings only, no equivalence claim) |
| `mask_probe.py` | one corpus file per process, per-step mask cost against the number of allowed tokens, plus the engine counters (`mask_ns_total / mask_calls`); needs `paired_runner` on `PYTHONPATH`, i.e. `benchmarks/maskbench/t8a` |
| `mask_diag.py` | reads the mask-path counters of a loaded library (`blg_diag_counters`) |
| `slice_calls.py` | per-call profile of a file list: every `compute_mask` call with its step and fill time, allowed tokens, token and decoded tail, and the mask-path counters of that call (reset before the fill, read after it), so the slow calls can be attributed to states and reproduced from `(file, instance, k)` |
| `name_states.py` | full masks at property-name states: feeds a schema and a prefix through the run's tokenizer and through a byte-complete vocabulary, takes the mask with the fast path off and on in both, and compares each pair bit for bit; covers declared and reused names, `propertyNames`, `patternProperties`, `additionalProperties`, object capacity and tokens that cross the closing quote and colon. A case fails when its prefix is not accepted, an expected `ResourceLimitError` does not occur in every leg, a mask is missing unexpectedly, the pairs disagree, or the fast path does not match `--expect-fast before|after`; `after` checks the `name_used` counter, so a string-lemma hit cannot satisfy it |
| `compare_ve_meters.py` | strict comparison of two `mask_equiv.py` results: file sets, instances, calls, refusals, capped runs, errors (with the two corpus compile-limit files named apart), stream mismatches, TBM keys and the recorded library hashes; exits non-zero unless every check passes |
| `ve_lib_record.py` | records which library a package resolves (path, sha256, size, inode, mtime) before and after a run and refuses to annotate a result when the snapshots disagree |
| `measure_counters.sh` | alternates the on/off pair per corpus file over three repeats, keeps the full log and the exit code of every run |
| `counter_aggregate.py` | median, min-max spread and on/off ratio from those logs; a run without the summary line or with a non-zero exit is reported, not skipped |

Rules that the tools enforce or assume:

- a timed leg runs against a **pinned** library: copy the artifact, point
  `bolorgir/_lib/libbolorgir.so.0.1.0` at it through a symlink (a real copy
  loads the library twice and the counters read zero), and record its sha256;
- a leg whose library was already in use when the measurement started can
  only be recorded *during* the leg and after it; the report must say so
  instead of implying a before-start snapshot;
- diagnostics must not be able to change a mask, so there is no ablation flag:
  the counters are always on unless the build uses `-Ddiag_counters=false`.

`mask_equiv.py` and `mask_probe.py` locate the corpus and the tokenizer
snapshot from this directory; the default `--run` path of `mask_equiv.py`
follows the local work-directory layout, so pass `--run` explicitly when the
work directory is elsewhere.
