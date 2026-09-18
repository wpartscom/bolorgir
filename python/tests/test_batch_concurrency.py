"""
Regression tests for batch serialization in fill_masks_batch (FR-11/13).

Covers the audit findings (docs/AUDIT_TZ_2026-09-15.md, FR-11/13 row):
- duplicate sessions in one batch (double acquire of a non-recursive lock);
- overlapping batches in concurrent threads (lock-order inversion);
- closing a session while a batch is being prepared (stale native pointer).

Run: python3 -m pytest python/tests/test_batch_concurrency.py
"""

import contextlib
import threading
import time

import pytest

import zig_constraints as zc
from zig_constraints import Engine, TokenizerBundle

EOS_ID = 256
VOCAB = 257

JOIN_TIMEOUT = 15.0


def mini_bundle() -> TokenizerBundle:
    tokens = [bytes([b]) for b in range(256)] + [b"<eos>"]
    return TokenizerBundle.from_token_bytes(tokens, eos_ids=[EOS_ID])


@contextlib.contextmanager
def engine_with_sessions(n):
    """Engine + constraint + n sessions; sessions are closed before the
    engine so that Engine.close() cannot fail with BusyError."""
    with Engine(mode="lazy") as engine:
        constraint = engine.compile({"type": "integer"}, tokenizer=mini_bundle())
        sessions = [constraint.create_session() for _ in range(n)]
        try:
            yield engine, constraint, sessions
        finally:
            for s in sessions:
                s.close()
            constraint.close()


def join_all(threads):
    deadline = time.monotonic() + JOIN_TIMEOUT
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    stuck = [t for t in threads if t.is_alive()]
    assert not stuck, "batch threads did not finish (deadlock?)"


def test_batch_duplicate_session():
    """A duplicated session in one batch neither hangs nor misbehaves:
    every row is filled independently, in input order."""
    with engine_with_sessions(2) as (_, constraint, sessions):
        s1, s2 = sessions
        masks = zc.fill_masks_batch([s1, s1, s2, s1])
        assert len(masks) == 4
        solo1, solo2 = s1.fill_mask(), s2.fill_mask()
        assert masks[0] == masks[1] == masks[3] == solo1
        assert masks[2] == solo2
        # Core-level duplicate rows report ZG_OK as well.
        rows = zc._core.fill_masks_batch([s1._s, s1._s])
        assert [code for code, _ in rows] == [0, 0]


def test_batch_overlapping_sessions_two_threads():
    """Two threads run overlapping batches with opposite session order;
    a single global lock order must prevent deadlock."""
    with engine_with_sessions(4) as (_, _, sessions):
        s1, s2, s3, s4 = sessions
        barrier = threading.Barrier(2)
        errors = []
        iterations = 300

        def worker(batch):
            try:
                barrier.wait(timeout=JOIN_TIMEOUT)
                for _ in range(iterations):
                    masks = zc.fill_masks_batch(batch)
                    assert len(masks) == len(batch)
            except Exception as exc:  # noqa: BLE001 - collected for assert
                errors.append(exc)

        # Opposite iteration order over the shared sessions s2/s3 is the
        # classic lock-order inversion setup.
        t1 = threading.Thread(target=worker, args=([s1, s2, s3],))
        t2 = threading.Thread(target=worker, args=([s4, s3, s2],))
        t1.start()
        t2.start()
        join_all([t1, t2])
        assert not errors, f"batch workers raised: {errors!r}"


def test_batch_closed_session_raises():
    """Closing a session before the batch is a typed error, not a crash."""
    with engine_with_sessions(2) as (_, _, sessions):
        s1, s2 = sessions
        s1.close()
        with pytest.raises(zc.WrongStateError):
            zc.fill_masks_batch([s1, s2])
        with pytest.raises(zc.WrongStateError):
            zc.fill_masks_batch([s2, s1])
        # The surviving session is unaffected.
        assert len(zc.fill_masks_batch([s2])) == 1


def test_batch_close_during_batch_no_crash():
    """A session closed concurrently with in-flight batches yields
    WrongStateError (or success if the batch won the race) — never a hang
    or a crash."""
    with engine_with_sessions(2) as (_, _, sessions):
        s1, s2 = sessions
        outcome = []

        def batcher():
            try:
                for _ in range(100_000):
                    zc.fill_masks_batch([s1, s2])
            except zc.WrongStateError:
                outcome.append("wrong-state")
                return
            outcome.append("no-error")

        t = threading.Thread(target=batcher)
        t.start()
        time.sleep(0.05)  # let the batcher get into its loop
        s1.close()
        join_all([t])
        # After close() returned, every batch touching s1 must fail fast;
        # the batcher may only ever observe WrongStateError.
        assert outcome == ["wrong-state"]
        with pytest.raises(zc.WrongStateError):
            zc.fill_masks_batch([s1, s2])
