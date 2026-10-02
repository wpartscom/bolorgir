Bundled core library: libbolorgir.so.0.1.0 (Zig ReleaseSafe build).

This directory holds the copy of the core library that the bolorgir._core
extension loads via its RUNPATH ($ORIGIN/_lib), so a plain `import bolorgir`
works without LD_LIBRARY_PATH. It must match the repository build: after
`zig build`, run (from the repository root)

    (cd python && python3 setup.py build_ext --inplace)
    cmp zig-out/lib/libbolorgir.so.0.1.0 python/bolorgir/_lib/libbolorgir.so.0.1.0

which rebuilds the extension and re-copies the fresh .so here (setup.py
handles the copy itself; see _copy_core_lib there). Any source change,
including comment-only edits that shift embedded line info, changes the
library hash - always re-sync instead of trusting a recorded hash.
