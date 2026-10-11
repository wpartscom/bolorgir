"""ADR-0011 certificate cross-check (tests/numconj_cert_check.py) in its
fast mode. Needs a core built with `zig build -Drelease=true
-Dtest_hooks=true --prefix zig-out/test-hooks` (or BLG_TEST_HOOKS_LIB);
skipped otherwise. CI runs the script in its own job.
"""

import json
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_HOOKS_LIB = os.environ.get("BLG_TEST_HOOKS_LIB") or os.path.join(
    _ROOT, "zig-out", "test-hooks", "lib", "libbolorgir.so.0.1.0")


@pytest.mark.skipif(not os.path.exists(_HOOKS_LIB), reason="core with test hooks not built")
def test_num_conj_certificate_cross_check():
    env = dict(os.environ, BLG_LIB_PATH=_HOOKS_LIB)
    proc = subprocess.run(
        [sys.executable, os.path.join(_HERE, "numconj_cert_check.py"), "--jobs", "4"],
        env=env, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["n_failures"] == 0
    assert out["stats"].get("alive", 0) > 0 and out["stats"].get("dead", 0) > 0
