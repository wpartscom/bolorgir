# T8a: the full MaskBench run, its evidence and its acceptance

`run_t8a.sh` runs, one after another, Bolorgir (`BOLORGIR_PROFILE=spec-v1`,
`BOLORGIR_SERIALIZER=1`, `--compact`) and llguidance and XGrammar at the
MaskBench pins and at current releases, on one corpus and one tokenizer;
`t8a_report.py` writes the tables and decides the acceptance. The five
participants, the pins and the tables are described in `../README.md`
(section "spec-v1 run and paired engines").

## Run

```sh
HF_HOME="<main tree>/benchmarks/local/hf-cache" \
  PY_BASE="<a python >= 3.12>" \
  T8A_ACCEPTANCE=benchmarks/coverage/spec-v1/t3-9a5baf3e/review-2/acceptance.json \
  T8A_T2C=benchmarks/maskbench/t8a/evidence/9a5baf3e/t2c.json \
  bash benchmarks/maskbench/t8a/run_t8a.sh <work dir> <bolorgir wheel> 1
```

- `<work dir>` is new (or the directory of an earlier run with the same
  inputs: a resume), outside any disposable git worktree and outside the
  protected paths. A full run first passes the embedded smoke of 20 files
  of the five participants at the same thread count, then runs the five
  full variants.
- One worker: every MaskBench process may take up to 40 GiB; the run sets
  `T8A_MEM_LIMIT_MB` (default and minimum 40960). `T8A_ACCEPTANCE` is the
  admitted T3 verdict of the core inside the wheel; before any install the
  preflight checks that verdict and the tokenizer snapshot against
  `benchmarks/t3/acceptance_pins.json`.
- `T8A_T2C` is the class file of the Bolorgir errors (below). It names
  the core `sha256`, the wheel `sha256` and the full protocol of the run;
  its classes are added only from the diagnosis of the first full run and
  decided by the owner, none is carried over - until then the entries are
  empty and every Bolorgir error stays open. The `evidence/6e62e0d1/`
  files (`t2c.json` for the first wheel, `t2c-run2.json` for the wheel
  rebuilt after the 2026-10-11 comment cleanup) cover the earlier core
  only: a class file for another core or protocol keeps every case open
  (keep the wheel file; a different wheel needs its own class file). The
  command above reproduces the `9a5baf3e` wheel: that acceptance verdict
  and `evidence/9a5baf3e/t2c.json` name that core and its wheel only. A
  wheel built from a later core, including the current publication tree,
  needs its own R6 verdict, its own acceptance JSON, its own class file
  and the protocol chosen for that run: none of these is carried over.
- `PY_BASE` creates both venvs and must be a Python 3.12 or newer (the
  wheel is `abi3` cp312 with `requires-python >= 3.12`; an older base
  fails the install). Nothing is installed into `PY_BASE` itself; the
  venv Python version is recorded in the manifest.
- Exit status: 0 accepted; 5 a written report that is not accepted; 1 a
  failed step, a failed smoke or changed inputs (the reasons are in
  `report/t8a.md` and `run.log`).

## The frozen protocol

The inputs of a run are recorded in `identity/manifest.json`
(`t8a_identity.py`): the wheel and the core inside it, the installed
package files and the serializer of both venvs, the corpus index, the
tokenizer files of the pinned revision, the parameters (one worker, 900 s
per file, the memory limit, spec-v1, bolorgir, compact) and the harness:
the eight files of this directory (`t8a_identity.TOOLS`).

- A resume compares every recorded field and aborts naming the changed
  one; changing a tool, the thread count or the wheel needs a new work
  dir. Finish tool changes before the run starts: `run_t8a.sh` and
  `t8a_report.py` are part of the pins.
- The wheel may be a release candidate; its core sha256 is checked against
  the admitted T3 verdict, and the wheel file sha256 is part of the
  protocol of the T2c classes (below).

## Evidence, stops and recovery

- Keep the whole work dir until the acceptance closes. It holds
  `run.log`, one log per engine (`<engine>.log`), the chunk logs
  (`out/<engine>/log.txt`), the output records and
  `identity/manifest.json`; a stop leaves everything in place.
- On a stop, read the reason first: `run.log` ends with
  `T8A_ABORT <engine> failed (exit N ...)`, and the driver writes
  `T8A_RUN_STOPPED <reason>` into `<engine>.log` (3 is the preflight of
  the driver, 4 a stopped chunk queue). Distill the evidence before any
  cleanup.
- The report is rebuilt from the work dir without rerunning anything (the
  Python of the run, `T8A_VENV_ROOT` if the venvs live elsewhere; `--t2c`
  as the `T8A_T2C` of the run):

  ```sh
  "<work dir>/venv-pinned/bin/python" benchmarks/maskbench/t8a/t8a_report.py \
    --manifest "<work dir>/identity/manifest.json" \
    --streams "<work dir>/identity/streams.json.gz" \
    --bolorgir blg-spec-v1 \
    --run "blg-spec-v1=<work dir>/out/blg-spec-v1" \
    --run "llg-pinned=<work dir>/out/llg-pinned" \
    --run "xgr-pinned=<work dir>/out/xgr-pinned" \
    --run "llg-current=<work dir>/out/llg-current" \
    --run "xgr-current=<work dir>/out/xgr-current" \
    --t2c benchmarks/maskbench/t8a/evidence/9a5baf3e/t2c.json \
    --out-md "<work dir>/report/t8a.md" \
    --out-json "<work dir>/report/t8a.json"
  ```
- Publish the distilled evidence under `evidence/<core>/` with a
  `SHA256SUMS` list (see `evidence/6e62e0d1/`); big raw outputs may stay
  compressed or in a permanent archive with its sha256, as long as the
  report rebuilds from the published set.
- A version of a past run without its preserved manifest is a
  reconstruction: commit dates and tool hashes narrow it down to the
  files of the last commit before the start, but uncommitted edits cannot
  be excluded. The full run of 2026-10-10 lost its work dir together with
  the worktree it lived in; its surviving evidence is the distilled
  `evidence/6e62e0d1/` set.

## T2c classes

An acceptance needs a class for every Bolorgir error: a validation error,
an invalidation error, a crash or a timeout. A class file (`T8A_T2C`)
names the core sha256 of the run, the file, the exact outcome
(`validation_error`, `invalidation_error`, `timeout` - a pending marker
whose chunk ended with exit -14 - or `crash:<exit code>`) and a complete
`protocol` block: `threads`, `time_limit`, `mem_limit`, `profile`,
`serializer`, `compact`, `smoke_files`, `tokenizer_revision`,
`wheel_sha256`, every entry with the value of the run it was decided for.
A missing block, a missing or null entry, an unknown key, a wrong type or
another value keeps the case open; a classified timeout still fails a
smoke. New classes are prepared with the diagnosis and decided by the
owner; the committed files are `evidence/6e62e0d1/t2c.json` and
`evidence/6e62e0d1/t2c-run2.json` for the earlier core (the 6e62e0d1
wheels) and `evidence/9a5baf3e/t2c.json` for the `9a5baf3e` wheel of that
historical line (class D for the four format-only instances of two files,
2026-10-13;
an entry applies by file and status, its descriptive instance list is
checked before the final acceptance; the timeouts stay open).
`evidence/9a5baf3e/t2c-w12.json` is the scaffold (no classes decided
yet) of the parallel protocol run (threads 12, work dir
`benchmarks/local/t8a-work-9a5baf3e-w12`): its classes will be decided
from the outcomes of that run only.
