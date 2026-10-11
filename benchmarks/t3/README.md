# T3 integration and acceptance runners

Tracked companions to the local T3 material (`benchmarks/local/t3/`, ignored
by git). The isolation rules require the run commands to live in versioned
files, not only under `benchmarks/local/`.

- `full_tests.sh` - integration run for a finished T3 series in this
  tree: Zig format check and unit tests, release and test-hook builds into
  `zig-out/`, both pytest backends, and the two hook tests (probe,
  `fail_row`) that must run and not skip. The first failing command exits
  the script with its status and no success line is printed. It refuses to
  run while `zig-out/lib` holds the library of an acceptance run
  (`benchmarks/local/t3/acc-*/identity/lib.sha256`). Usage:
  `benchmarks/t3/full_tests.sh <log>`; `ZIG`, `PY`, `HF_HOME`,
  `HF_HUB_OFFLINE` override the defaults.
- `isolated_tests.sh` - the same checks for new code without touching the
  frozen candidate: a copy of the sources in a new work
  directory, builds under explicit prefixes there, a work venv that reads
  the dependencies of `.venv` without installing into it, a separate
  `build-venv` with the build backend of `python/pyproject.toml`
  (setuptools>=77, from PyPI or `BUILD_WHEELHOUSE`) checked with the test
  dependencies and the T9 arguments before any build, the wheel
  (`dist/`) and the in-place package built in the copy with that
  backend, both pytest backends with the required tool and
  T9/T12 tests run and not skipped (`BLG_TEST_REQUIRE_BACKEND=1`), the T9
  accept overhead against `c288703` built with the same flags, and the
  fingerprints of the protected paths (`benchmarks/protected_paths.sh`)
  compared before and after. Usage:
  `[ISO_LINE=wave2|release] benchmarks/t3/isolated_tests.sh <work dir> [pytest args]`;
  the hook test of the HF adapter needs torch, which the main `.venv`
  lacks: `BASE_PY="$PWD/benchmarks/local/venv-torch/bin/python"` (read
  only; its fingerprint is compared with the protected paths). The test
  dependencies are imported before any build and their versions written
  to `identity/test-env.json` with the differences from `.venv`; the
  protected paths are compared on every exit, a failure keeps its exit
  status; every process of the run has an address-space limit
  (`ISO_MEM_LIMIT_MB`, default 16384; `r6_review.sh` has
  `R6_MEM_LIMIT_MB`), so a runaway fails its step instead of freezing
  the machine; the last line is `== ISOLATED_DONE ok` only when
  everything passed.
- `run_t3_accept.sh` - full acceptance run of a frozen candidate: K = 20 of
  both corpora with the fixed 1-in-20 HF sample from a run directory that
  holds every input by content: the source tree of the candidate commit
  (`git archive`; the checkout must be clean), the library, both corpus
  snapshots, the pinned tokenizer snapshots and a venv of its own, created
  at its final path from `acceptance-requirements.txt` (`--no-deps`,
  `pip check`, the freeze must equal the list); the package of the copy
  gets its own `_core`, built against the run's library (its `_lib` must
  hold the same bytes). The corpora and tokenizer
  files are pinned by content in `acceptance_pins.json` (checked on the
  sources and on the copies); `t3_identity.py` records every file of every
  input (`identity/manifest.json`, `identity/files/*.list`) and checks all
  of them again, with the run parameters, on every resume, naming what
  differs. A run directory without `identity/.complete` but with content
  is an interrupted preparation and is refused; a resume runs the run's
  own copy of the runner. Exit: 0 finished, the harness status when it
  failed (`RUN_DONE exit=N`), 2 refused, 3 a failed preparation step.
  Usage: `benchmarks/t3/run_t3_accept.sh [--prepare-only] <library> <run dir> [jobs]`;
  `tests/test_t3_accept_runner.py` checks the isolation on stand-in inputs.
  The earlier layout (`acc-50cbd80e`: shared `.venv`, linked corpora) is
  refused by this runner as an incomplete preparation; it resumes only
  with the runner of `c288703`.
- `r6_review.sh` - processing of saved results of a candidate, one or both
  corpora: the T2c triage (`benchmarks/triage_semantic.py --out-json`),
  the coverage control with the acceptance verdict, and an evidence bundle
  (the oracle reports of the candidate and the lines, the D4 list, the
  oracle line list, the protocol notes, `SHA256SUMS`). It reads the
  reports only and writes into a new directory outside the protected
  paths. The triage imports the ctypes bindings, which load a core library
  at import: `R6_LIB` names the candidate's library and must have the
  `library_sha256` of every report given (a worktree or a copy has no
  `zig-out/lib`, the main tree another build). Usage:
  `R6_LIB=<library> benchmarks/t3/r6_review.sh <reports dir> <label> <out dir> [<oracle report> ...]`;
  exit 0 admitted, 1 not admitted, 3 intermediate, 2 unusable (a refusal
  writes nothing). The repo phase of `acc-50cbd80e` can be reviewed with
  `R6_LIB=benchmarks/local/t3/acc-50cbd80e/lib/libbolorgir.so.0.1.0 benchmarks/t3/r6_review.sh benchmarks/local/t3/acc-50cbd80e/out t3-50cbd80e <new dir>`
  (intermediate).
- `r6_publish.sh` - after a finished acceptance run: copies the reports,
  sidecars and identity record into `benchmarks/coverage/spec-v1/<label>/`
  (an existing label is refused) and runs `r6_review.sh` into
  `<label>/review/`. The five oracle reports of the candidate (spec-v1,
  one per dialect) are required. Usage:
  `benchmarks/t3/r6_publish.sh <run dir> <label> <five oracle reports>`.
  A published directory is a report; the acceptance is the verdict in
  `review/acceptance.json`. `r6_publish.sh` hands the run's library to the
  review as `R6_LIB`.
- After a finished run, from the release worktree the run measured, in
  this order (`r6_after_run.sh` runs the four steps and stops at the
  first unexpected status, exit 10+step):
  1. `r6_oracle.sh TAG LIB SHA256 [TAG LIB SHA256 ...]` - the five
     spec-v1 dialects of each library, the candidate first, then a line
     without oracle data; a clean tree, libraries checked by sha256,
     existing reports refused, `--check-only` for the setup alone; each
     report must be evidence about its library
     (`acceptance_compare.oracle_content_problems`: rows, compiled
     schemas, engine verdicts, no compile refusal of the call itself such
     as `bad compile request struct_size`, a summary that counts the
     rows), else the launch stops with exit 1;
  2. `r6_register_line_oracle.py --line --library-sha256 --source --prefix --identity TREE...` -
     the line's reports into `oracle_lines.json` of the worktree and of
     the main tree, every check before the first write (exit 2, nothing
     written), the same content check; `owner_decision` is not touched.
     `--replace DIALECT=SHA256 ... --reason TEXT` replaces registered
     reports: the expected sha256 of all five on the list and on disk in
     every tree, new paths (the old files stay), the old entry kept in
     the line's `history`;
  3. `r6_publish_check.sh <run> <label> <work dir> <five reports>` -
     `r6_publish.sh` on a temporary copy of the worktree, then
     `r6_publish_layout.sh`;
  4. `r6_publish_release.sh <run> <label> <five reports>` - the real set
     in the worktree (at the run's revision, only the registered
     `oracle_lines.json` changed), the same layout check, then
     `r6_compare_sets.py`: the two sets must say the same, file by file
     (transitions, D4 rows, T2c results, totals, input hashes, compressed
     evidence), with only the tree root of each replaced.
  `r6_publish_layout.sh <run> <set> <revision>` checks a set against its
  run: reports, sidecars, identity and events equal to the run's, the
  review files with `review/tools.json` (the version of the review tools,
  apart from the run's `identity/git_head`), and every `SHA256SUMS` (the outer one and those of `review/`, `review-2/`,
  ...) equal to the list rebuilt from the files. A review exit 1 passes
  the chain only when the failing checks are `transitions_described` (an
  undescribed transition: the executor's analysis), `d4_candidate` (the
  D4 list bound to another candidate: the executor rebinds it) and
  `transitions_approved` (the owner's decisions). Steps 3 and 4 need
  `R6_BUILD_PY`, a python with the build backend of
  `python/pyproject.toml`. Tests: `tests/test_r6_release_tools.py`.
- `tools/acceptance_compare.py` - the T3 goals, the transition list of a
  candidate against `dbc5eeb` and `7345490f` (main-compile refusals and
  missing results included), and the acceptance verdict: `admitted` only
  when one library covers both corpora, the protocol, corpus and HF sample
  match the lines (or a difference is explained in `protocol_notes.json`),
  the goals and the coverage control hold, every transition has an owner
  approval bound to the candidate and the row, all five oracle dialects
  name the candidate's library and keep PASS per dialect against the line
  oracle, the oracle reports of every line belong to the line's library
  (header hash or archive binding), every oracle report is evidence about
  its library (`oracle_content:<build>`; a report that is not counts as no
  data and fails the comparisons), a line without oracle data has an
  owner exception, and the T2c triage of the same reports passes its gate.
- `d4_proposals.json` - the owner decision list D4 with the proposals of
  `evidence.md`; `owner_decision` stays null until the owner decides, in
  the format of its `decision_format` field. The executor never fills it.
- `oracle_lines.json` - the oracle reports of the reference lines with
  their sha256 and library (7345490f: `oracle-final-*`, bound to their
  library by an archive binding because they predate the header hash);
  `dbc5eeb` has no report of its own library: the acceptance stays closed
  until the owner records `owner_decision` (an exception with a control
  line) or the oracle of `8ff970d4` is run. The executor never fills it.
- `protocol_notes.json` - explained protocol differences between the
  candidate and the lines (the schema budget and the completion of the
  historical runs, their recorded crashes).

The release gate does not use a hand-written hash: the final tag needs
the committed R6 verdict and T8a report named in
`docs/release/v0.2.0-composition.json` (`.github/scripts/release_gate.py`).

Supporting material for the R1-R6 work of the spec: `evidence.md`
(summary with build hashes, causes and decisions), `tools/` (targeted
document recovery, classification, replay comparison, mask timing),
`selections/` (schema lists for the targeted repeats) and `evidence/`
(compact run records and the slow-state prefix). The full reports and
sidecars are produced by the acceptance run into
`benchmarks/coverage/spec-v1/`.
