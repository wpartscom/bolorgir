# Final R6 of candidate 9a5baf3e (owner decisions applied)

The review re-run of `../review/` after the owner recorded the D4 decisions
of 2026-10-13 in `benchmarks/t3/d4_proposals.json` (34 entries, `accept`,
bound to the full candidate sha256; 67 transition results, 81 comparison
rows). The historical first review stays in `../review/` and is not
rewritten.

- `command.txt`, `console.txt` — the exact invocation and its console
  output, `R6_REVIEW_DONE verdict=admitted exit=0`.
- `acceptance.{json,md}` — the acceptance comparison of the run
  (`admitted`, no failing, pending or intermediate checks).
- `triage.{json,md,log}` — the T2c triage of the candidate (gate
  `17e25bba4f1c6144`, the same as the first review).
- `deviations.json` — the deviations list of the review.
- `evidence/` — the review inputs as the tool read them: the D4 list with
  the decisions (`d4.json`), `oracle_lines.json`, `protocol_notes.json`,
  the five candidate oracle reports and the line oracle reports for
  `7345490f`/`dbc5eeb`, plus the `binding/` sources.
- `tools.json` — identities of the tools used; `SHA256SUMS` — the
  complete list of the files of this directory, rebuilt and checked by
  `benchmarks/t3/r6_publish_layout.sh`.

Owner acceptance of the listed deviations does not turn the negative
results into successes; the outcomes (`invalid_document`, `incomplete`,
`engine_unavailable`) stay as recorded and are documented in
`docs/supported_features.md`, `docs/release/v0.2.0.md` and the README.
