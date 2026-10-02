# Contributing to Bolorgir

Thanks for your interest in the project! All project communication and content
is English-only: code, comments, issues, pull requests, and commit messages.

## Development setup

Requirements: Zig 0.15.2, Python 3.10+, a C toolchain (gcc/clang), `git`.

Build and test the core:

```sh
zig build -Drelease=true          # -> zig-out/lib/libbolorgir.so
zig build test --summary all      # core unit tests (Debug and ReleaseSafe)
zig fmt --check build.zig src     # formatting must stay clean
zig build example-c               # C example
LD_LIBRARY_PATH=zig-out/lib zig-out/bin/c_client
```

Build the Python extension and run the full test matrix:

```sh
cd python && ZIG=$(command -v zig) python3 setup.py build_ext --inplace && cd ..

PYTHONPATH=python BLG_TEST_BACKEND=ctypes  python3 -m pytest tests/ python/tests/ -q
PYTHONPATH=python BLG_TEST_BACKEND=package python3 -m pytest tests/ python/tests/ -q
```

Benchmarks have their own dependencies (numpy, transformers, torch; `xgrammar`
and `llguidance` for comparisons) - see `benchmarks/README.md`.

## Pull requests

- Keep changes focused; add regression tests for behavior changes.
- All checks above must pass; `zig fmt --check` must stay clean.
- Update `docs/` and `README.md` when public behavior changes.
- Commit messages: short imperative English summary
  (e.g. `Reject unsupported pattern keyword`).
- Core design rule: unsupported input is rejected at compile time with an
  explicit error. Never silently relax the constraints.

## Issues

Use the issue templates. For performance reports include: tokenizer, schema,
mode (`lazy`/`adaptive`/`precompute`), limits, and environment details.
