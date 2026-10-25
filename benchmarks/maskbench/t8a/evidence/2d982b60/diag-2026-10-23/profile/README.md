# Per-call profile of the slice on `2d982b60`

`slice_calls.py` on the 43-file slice, fill/commit split included, three
runs: mask 0.239 / 0.216 / 0.219 s, fill p90 0.766 / 0.734 / 0.742 ms,
p99 5.23 / 4.06 / 4.11 ms, 0 errors. The first run was taken while the
machine was busy; the other two sit where the previous artifact
(`2e863e86`) measured 0.212-0.220 s. The walk-level release cannot fire on
this workload: no fill runs out of memory and no filtered state needs the
search, so the cache keeps its whole win.
