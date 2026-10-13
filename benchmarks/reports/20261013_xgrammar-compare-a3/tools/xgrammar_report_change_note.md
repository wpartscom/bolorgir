# xgrammar_report.py change note (revision 2 -> 3, 2026-10-13)

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
