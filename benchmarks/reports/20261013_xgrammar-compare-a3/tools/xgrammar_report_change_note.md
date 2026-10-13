# xgrammar_report.py change note (revisions 2 -> 5, 2026-10-13)

Review (point 2) demonstrated that revision 2 could report false success.
Failure modes reproduced on artificially damaged records and the fixes:

| Damage | Revision 2 | Revision 3 |
|---|---|---|
| response without the final stream event (`[DONE]`) | stayed `stop_valid` | `final_event == False` classifies as `unfinished` and raises a problem |
| a run holding 1 record instead of `batch` (128) | included in throughput | a run must hold exactly `batch` records; otherwise excluded with the stated reason and flagged |
| stored-vs-revalidated disagreement | `problems: []` | a problem (with the disagreeing records listed) |
| empty input set | no error | a problem; the tool exits 1 |
| missing usage | counted as 0 tokens | such runs leave the token metrics with the reason `usage missing`; a finished HTTP-200 record without usage is a problem |
| duplicate request key, `idx` outside `[0, batch)` | unchecked | problems |
| v2 record without stored text | unchecked | problem |

The matrix is declared (`--repeats`, default 2: 3 workloads x batches
1/8/32/128 x repeats per session) and every session is checked against
it; a "Completeness" section appears in the markdown report. Negative
tests were added to `test_xgrammar_report.py` (17 tests, including one
full-matrix positive that must produce zero problems).

Re-check of the stored data (2026-10-13) with revision 3:

- the A3 compact series (`compact/raw-*.jsonl`) regenerates with
  `0 problem(s)`; every measured cell value is unchanged (compared
  field by field against the revision-2 outputs committed in `c9c90b0`);
- the old-series re-accounting (`raw-{x1,b1,x2,b2}.jsonl`) regenerates
  with `0 problem(s)`; all cell values are unchanged. The v1 records
  carry no answer text, so re-validation stays at "not re-checkable"
  by design, not by oversight.

The original revision-2 outputs are preserved in commit `c9c90b0`;
this directory carries the regenerated revision-3 outputs (the report
header records the tool revision and sha256 of the run).

## Revision 3 -> 4 (review round 3)

The third review reproduced four false successes on damaged copies of
the full A4 matrix plus two defects of the standalone launcher:

| Damage / defect | Revision 3 | Revision 4 |
|---|---|---|
| a whole session removed from the inputs (`raw-b2d.jsonl`; or all bolorgir files) | `problems: []`, exit 0 | the expected session set and the backends are declared (`--manifest` or `--expect`); a declared session missing from the inputs, an undeclared session or a backend mismatch is a problem; a report without a declaration fails |
| `final_event` removed from one record | stayed `stop_valid`, no problem | format violation; in a v2 file the record counts as `unfinished` |
| `text` set to null | re-validation silently skipped | format violation (`text` must be a string on v2 records) |
| `finish` removed (with the flag) | no problem, the stored TPOT stayed in the median | mandatory-field violation; `unfinished`, removed from TPOT - TPOT is now taken over `stop_valid` records only, and the stored value is re-checked against `(e2e_ms - ttft_ms)/(tokens - 1)` |
| `usage_present=false` with a stored token count | counted as tokens | format violation; the run leaves the token metrics with the reason `usage missing` |
| `--labels x1e b1e x2e b2e` in `run_compact.sh` | only `x1e` parsed, the output directory overwritten by `b2e` | all four labels parsed (consumed up to the next option; exactly four, unique) |
| `--port N` in `run_compact.sh` | the server moved to N, the client stayed on 8021 | the client receives `--base-url http://127.0.0.1:N`; `--check-only` prints the resolved labels/URL/repeats without touching the GPU |

Both record formats are explicit and validated per file: v2 = the 22
mandatory typed fields emitted by `xgrammar_bench2.py`; v1 = the 17
emitted by `xgrammar_bench.py`. A file counts as v2 as soon as any
record carries any v2 field (even null), so a stripped field cannot
hide behind the v1 format; `--format` forces one. Value consistency
is checked: `text_len` against `len(text)`, `finish_present` against
`finish`, `tokens` against `usage_present`, `tpot_ms` against its
formula and its definedness rule. The negative tests now run on a
full correct matrix with a single corruption (33 tests in total).

Re-check of the stored data (2026-10-13) with revision 4:

- `compact/`, `compact-r2/` and the old-series re-accounting
  regenerate with `0 problem(s)` and every measured value unchanged
  (compared field by field against the revision-3 outputs committed
  in `22b2047`) - the TPOT `stop_valid` rule and the usage rule move
  no stored number because the stored records already satisfy them;
- the re-check commands declare the composition: `compact-r2` uses
  its `manifest.json` (`--manifest`), the other two the explicit
  `--expect` list;
- `compact-r2/manifest.json` was regenerated with the revision-4
  generator: it differs from the recorded file only by the added
  `session_backends` block (every other field, including the
  EngineCore PIDs and hashes, is identical);
- the review reproductions were replayed on copies (drop `b2d`, only
  the xgrammar sessions, each single-record corruption): intact inputs
  give `problems: []` and exit 0, every damaged copy fails with the
  messages above and the affected metrics drop out (`usage missing`,
  `unfinished`).

The revision-3 outputs are preserved in commit `22b2047`; the current
directory carries the revision-4 outputs.

## Revision 4 -> 5 (review round 4)

The fourth review confirmed the round-3 reproductions closed but found
one remaining defect: the manifest `session_backends` block was
effectively optional (absent -> `{}`, and a null value disabled the
check for its session), so three CLI reproductions still passed:
removing the block, setting `b1d: null`, and removing the block while
mislabeling the raw-b1d records as xgrammar.

| Reproduction (temporary A4 copies) | Revision 4 | Revision 5 |
|---|---|---|
| `session_backends` block removed, `sessions` intact | `problems: []`, exit 0 | refused before the data is read (exit 2): the block is mandatory |
| `b1d: null` | `problems: []`, exit 0 | refused: every value must be a non-empty string of an admissible backend |
| block removed + raw-b1d records marked xgrammar | `problems: []`, exit 0 | refused (missing block); with the block intact the mislabeled records fail the data check (`backend ['xgrammar'] does not match the declared bolorgir`) |

The contract is now: `session_backends` is a mandatory mapping whose
keys cover `sessions` exactly, every value a non-empty string out of
`xgrammar`/`bolorgir`. An absent, null, non-mapping, empty,
incomplete, extra, null-valued, empty-valued or inadmissible field all
fail the CLI (exit 2) before the report is built, and `build_report`
itself validates any declaration passed directly - a `None` backend is
a problem on its own, never a permission to skip the comparison; the
accepted declaration is revealed in `expected_sessions.backend_invalid`.

Tests: 12 new ones (6 unit on `build_report`/`check_declaration`, 6
CLI running the tool over temporary copies, the review reproductions
included); 45 tests in total, 16 subtests.

Re-check of the stored data (2026-10-13) with revision 5: `compact/`,
`compact-r2/` (via its manifest) and the old-series re-accounting all
regenerate with `0 problem(s)`; every measured value, category,
denominator and input hash is unchanged against the revision-4 outputs
committed in `469ca1f` - the only diffs are the tool revision and its
sha256, the generation timestamp, the new `backend_invalid` key and
the updated method/about wording. `SHA256SUMS` was rebuilt for the
new file set.

The revision-4 outputs are preserved in commit `469ca1f`; the current
directory carries the revision-5 outputs.
