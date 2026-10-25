# Correction (2026-10-24)

This package documents the full functional run at `bbaf3e1`. Two
statements in its `CONCLUSION.md` are superseded; the run data and all
other numbers stand.

1. **Coverage.** The `differential-reference` row ran one lazy context
   (cache off, fast path at its default) for all 600 schemas, so the row
   did not exercise the fast/cache on/off configurations its description
   named. `../full-functional-c770469-2026-10-24/` contains the campaign
   that replays each generated schema and walk seed in four
   configurations with identical counters and trace digests, plus a full
   six-row rerun.
2. **Performance.** The 1.42 ms p90 belongs to `642ba1ae`, not to
   `a87e2f71`. For `a87e2f71` the 43-file diagnostic slice
   (`diag-2026-10-23/equiv/slice.json`) has p90 0.758 ms, and the pinned
   BFCL gate's on-call TBM p95 is 907.087 us in this package's gate run
   (796.543 us in the rerun), so p90 of that measured set is below 1 ms.
   These figures cover the measured workloads only and do not transfer to
   the full T8a corpus or the VE latency tail.

The combined, corrected record is
`../full-functional-c770469-2026-10-24/CONCLUSION.md`.
