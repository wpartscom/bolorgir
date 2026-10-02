# ADR-0002. Python ↔ core bridge: CPython C extension with the Limited API (abi3)

- Status: accepted
- Date: 2026-09-14
- Context: spec FR-13, DESIGN.md §8

## Context

The Bolorgir core is a shared Zig library with a C ABI
(`include/bolorgir.h`). A Python bridge is needed that does not duplicate
mask construction algorithms, manages the GIL and buffer ownership correctly,
and packages into a wheel for Linux x86_64. Three options were considered.

## Options

### 1. CPython C extension with the Limited API (chosen)

`bolorgir/_core.c`, `Py_LIMITED_API=0x030A0000` → a single abi3 wheel
`cp310` for all Python ≥ 3.10 (verified by builds and tests; a regular
build cannot be labeled abi3 - controlled via `py_limited_api=True` and
`bdist_wheel --py-limited-api`).

- Call cost: a direct C call to `blg_*`, without marshaling arguments through
  foreign types; on the fill_mask/accept_token paths (the generation hot loop)
  overhead is minimal and measurable.
- GIL: released explicitly (`PyEval_SaveThread`) around all non-instantaneous
  native calls (context_create, compile, session_create, fill_mask(s),
  accept_token, destroy).
- Ownership: mask buffers - `PyBytes` filled in place (zero copies);
  the tokenizer table is copied by the core at `blg_context_create`; the
  Grammar→Context and Session→Context/Grammar references guarantee destroy order.
- Concurrency: a per-session `PyThread_type_lock` serializes operations
  of one session; the blocking acquisition is performed without the GIL.
- Errors: `blg_status` + `blg_error` (message, schema_offset) are mapped to a
  typed exception hierarchy on the C side.

### 2. ctypes

- No compilation step, but: struct marshaling (`blg_token_entry`,
  `blg_tokenizer_desc`) is described in Python and duplicates the header layout
  (risk of divergence when the ABI changes); callbacks and GIL management
  (ctypes releases the GIL only for the duration of a call and provides no
  per-session locks without custom C code) are weaker; packaging still requires
  shipping a .so. Call cost is higher (argument preparation in Python).

### 3. cffi

- More ABI-precise than ctypes (header parsing), but adds a runtime dependency
  on cffi and, in out-of-line mode, the same C compilation step; the GIL and
  buffer ownership are controlled less well than in a custom extension.

## Decision

A C extension with the Limited API, abi3 wheel `cp310`. If ABI evolution
uncovers a Limited API limitation, we switch to wheels for specific
Python versions and update the release matrix (explicitly, without a false abi3
label).

## Consequences

- Building requires a C compiler and Python 3.10+ headers (available on the
  target platform); the wheel requires neither.
- `_core.c` contains no core algorithms: only Python objects, the GIL,
  buffer ownership and error mapping.
- Finalizers (`tp_dealloc`) are an additional safety net; the main path is
  context managers (`close()`), see FR-13.
