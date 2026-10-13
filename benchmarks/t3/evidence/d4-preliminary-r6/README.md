# Preliminary R6 of candidate 9a5baf3e (archived review record)

Run 2026-10-13 from the repository root with the updated D4 revision
(`benchmarks/t3/d4_proposals.json`, every `owner_decision` still null).
This directory is the archived copy of the review output that was
produced at `benchmarks/local/t3/r6-9a5baf3e-d4/`.

- `command.txt` - the exact invocation and the expected failing check;
- `console.txt` - the console output of that run
  (`R6_REVIEW_DONE verdict=not_admitted exit=1`);
- `acceptance.{json,md}` - the acceptance comparison of the run;
- `triage.{json,md,log}` - the T2c triage of the candidate;
- `deviations.json` - the deviations list of the review;
- `evidence/` - the review inputs as the review tool read them:
  `d4.json` (copy of the D4 revision), `oracle_lines.json`,
  `protocol_notes.json`, the five candidate oracle reports and the
  line-binding oracle reports for `7345490f`/`dbc5eeb`, plus the
  `binding/` sources;
- `tools.json` - identities of the tools used.

Result: `verdict=not_admitted`, exit 1, with `transitions_approved` as
the only failing check (81 transitions pending a real owner decision);
`transitions_described` (81 rows, 0 without an entry), `d4_candidate`
and `triage` pass. A `not_admitted` record is kept as evidence of the
pre-decision state and needs no owner approval.

Integrity: `sha256sum -c SHA256SUMS` covers every file in this
directory - the commands (`command.txt`) and the console output
(`console.txt`) included - and passes from the tracked tree alone.
