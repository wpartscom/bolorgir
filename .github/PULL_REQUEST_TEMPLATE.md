# Pull request

## Summary

<!-- What does this change and why? Keep it short. -->

## Checklist

- [ ] Tests added/updated for behavior changes
- [ ] `zig fmt --check build.zig src` stays clean
- [ ] `zig build test --summary all` passes (Debug and ReleaseSafe)
- [ ] `PYTHONPATH=python BLG_TEST_BACKEND=ctypes python3 -m pytest tests/ python/tests/ -q` passes
- [ ] `PYTHONPATH=python BLG_TEST_BACKEND=package python3 -m pytest tests/ python/tests/ -q` passes
- [ ] Docs updated (`docs/`, `README.md`) if public behavior changed
- [ ] No new silent relaxation of constraints (unsupported input must be rejected explicitly)
