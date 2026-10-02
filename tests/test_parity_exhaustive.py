"""T2: exhaustive parity on small schemas and the byte-level tokenizer.

BFS over the prefix space up to N tokens deep (capped by state count).
At each prefix the kernel mask is compared bitwise with the reference oracle
(as sets of allowed ids, including eos); kernel sessions cannot fork, so each
prefix is reproduced by replaying token ids from the start.

The whole module is skipped until the core/package is built.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpora
import reference as ref
from conftest import assert_masks_equal, backend_available, make_backend  # noqa: E402

_ok, _why = backend_available(os.environ.get("BLG_TEST_BACKEND", "auto"))
if not _ok:
    pytest.skip(f"core unavailable: {_why}", allow_module_level=True)

try:
    import blg_ctypes
    BLG_OK = blg_ctypes.BLG_OK
except ImportError:  # package-only access, no .so
    blg_ctypes = None
    BLG_OK = 0

STATE_CAP = corpora.SMALL_STATE_CAP


def _exhaustive_case(backend, spec, case):
    from collections import deque
    lang = ref.compile_schema(case["schema"])
    depth = case["depth"]
    eos = set(spec.eos_ids)
    special = set(spec.special_ids)

    worklist = deque([()])   # FIFO: breadth-fair capping
    seen_states = 0
    seen_docs = 0
    truncated = False

    constraint = backend.compile(case["schema"])
    try:
        while worklist:
            if seen_states >= STATE_CAP:
                truncated = True
                break
            seq = worklist.popleft()
            seen_states += 1

            session = constraint.create_session()
            try:
                for tid in seq:
                    status = session.accept(tid)
                    assert status == BLG_OK, (
                        f"replay failed: schema={case['name']} seq={list(seq)} "
                        f"tid={tid} status={status}")

                kernel_mask = session.mask()
                prefix = b"".join(spec.tokens[t] for t in seq)
                matcher = ref.Matcher(lang)
                assert matcher.feed(prefix), (
                    f"oracle rejected kernel prefix: schema={case['name']} "
                    f"prefix={prefix!r} seq={list(seq)}")
                oracle_mask = matcher.mask(b"", spec)
                assert_masks_equal(kernel_mask, oracle_mask,
                                   schema_name=case["name"], prefix=prefix,
                                   seq=seq, spec=spec)

                kernel_can_end = session.can_end()
                assert kernel_can_end == matcher.can_end(), (
                    f"can_end mismatch: schema={case['name']} prefix={prefix!r} "
                    f"kernel={kernel_can_end} oracle={matcher.can_end()}")
                if kernel_can_end:
                    seen_docs += 1
                    assert ref.canonical_check(prefix, lang), (
                        f"completed prefix not in language: {prefix!r}")

                if len(seq) < depth:
                    for tid in sorted(kernel_mask - eos - special):
                        worklist.append(seq + (tid,))
            finally:
                session.close()
    finally:
        constraint.release()

    assert seen_states > 0
    assert seen_docs > 0, f"{case['name']}: no completed documents"
    return seen_states, truncated


@pytest.mark.parametrize("case", corpora.SMALL,
                         ids=[c["name"] for c in corpora.SMALL])
@pytest.mark.parametrize("mode", ["lazy", "adaptive"])
def test_parity_exhaustive(backend_factory, byte_tok, case, mode):
    backend = backend_factory(byte_tok, mode)
    states, truncated = _exhaustive_case(backend, byte_tok, case)
    # truncated=True is acceptable (depth/cap bounded), but report it
    print(f"\n{case['name']} [{mode}]: states={states} truncated={truncated}")


def test_parity_exhaustive_literals(backend_factory, byte_tok):
    for case in corpora.LITERALS:
        lang = ref.compile_literals(case["strings"])
        backend = backend_factory(byte_tok, "lazy")
        constraint = backend.compile_literals(case["strings"])
        eos = set(byte_tok.eos_ids)
        docs = {s.encode("utf-8") for s in case["strings"]}
        worklist = [()]
        seen = 0
        while worklist and seen < 5000:
            seq = worklist.pop()
            seen += 1
            session = constraint.create_session()
            try:
                for tid in seq:
                    assert session.accept(tid) == BLG_OK
                kernel_mask = session.mask()
                prefix = b"".join(byte_tok.tokens[t] for t in seq)
                matcher = ref.Matcher(lang)
                assert matcher.feed(prefix)
                assert_masks_equal(kernel_mask, matcher.mask(b"", byte_tok),
                                   schema_name=case["name"], prefix=prefix,
                                   seq=seq, spec=byte_tok)
                assert session.can_end() == (prefix in docs)
                for tid in sorted(kernel_mask - eos):
                    worklist.append(seq + (tid,))
            finally:
                session.close()
        constraint.release()


def test_lazy_vs_adaptive_bitwise(byte_tok):
    """Bitwise identical lazy/adaptive masks on every prefix of a trace."""
    lazy = make_backend(byte_tok, "lazy", kind="ctypes")
    adaptive = make_backend(byte_tok, "adaptive", kind="ctypes")
    case = corpora.SMALL[0]  # action_amount
    lang = ref.compile_schema(case["schema"])
    doc = b'{"action":"sell","amount":-12}'
    seq = list(doc) + [byte_tok.eos_ids[0]]

    cl, ca = lazy.compile(case["schema"]), adaptive.compile(case["schema"])
    sl, sa = cl.create_session(), ca.create_session()
    try:
        for tid in seq:
            _, wl = sl.raw.fill_mask_words()
            _, wa = sa.raw.fill_mask_words()
            assert wl == wa, (
                f"lazy/adaptive mask divergence at seq prefix, tid={tid}\n"
                f"  lazy:     {wl}\n  adaptive: {wa}")
            assert sl.accept(tid) == BLG_OK
            assert sa.accept(tid) == BLG_OK
        assert sl.can_end() and sa.can_end()
        assert sl.finish() == BLG_OK
        assert sa.finish() == BLG_OK
        m = ref.Matcher(lang)
        assert m.feed(doc) and m.can_end()
    finally:
        sl.close()
        sa.close()
        cl.release()
        ca.release()
        lazy.close()
        adaptive.close()
