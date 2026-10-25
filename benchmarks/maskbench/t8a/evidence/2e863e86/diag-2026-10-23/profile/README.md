# Per-call profile of the slice on `2e863e86`

`slice_calls.py` on the 43-file slice, fill/commit split included, three
runs: mask 0.216 / 0.262 / 0.212 s, fill p90 0.693 / 0.865 / 0.761 ms,
p99 4.06 / 5.25 / 3.90 ms, 0 errors. The middle run was taken while the
machine was busy; the interleaved comparison in `../equiv/ab/` (0.215 /
0.212 / 0.218 s here against 0.216 / 0.213 / 0.233 s for the pre-fix cache
artifact) is the number to compare, and it shows the memory rules cost
nothing: certification settles every filtered call, so the release before
the search never fires on this workload.
