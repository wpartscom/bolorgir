# Per-call profile of the slice on `69a24712`

`slice_calls.py` on the 43-file slice, fill/commit split included, two runs:
mask 0.219 s and 0.220 s, fill p90 0.661 ms and 0.737 ms, commit p90
0.002 ms, 0 errors. Against `dc415942` (mask 0.273 s, fill p90 0.608 ms,
p99 7.923 ms) the mask step and the tail fall while p90 stays inside the
run-to-run spread; after the cache the p90 call of the slice is an
unfiltered string mask, which is why the cache cannot move it.
