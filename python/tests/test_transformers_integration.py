"""Regression tests for the HF integration (bugs B-1/B-2/B-3) without a real model.

ConstraintLogitsProcessor is called directly with factory input_ids/scores
(CPU, byte-wise mini tokenizer: vocab 257 = 256 bytes + EOS); model weights
are never downloaded. Masks come from the real core; under a stub core
(BLG_CORE_STUB=1, masks zeroed) mask-dependent tests are skipped.

Coverage:
- B-1: lm_head width exceeds the constraint vocab (padded vocab) - the logit
  tail is forbidden (-inf), the mask applies to the first vocab positions;
- B-3: a batch row finished with EOS before the others - HF pads after EOS
  never reach accept_token, fill_mask is not called for a finished row;
- B-2: Engine.__exit__ with an active exception does not mask it with a
  BusyError;
- D-1 (eighth run): unpack_forbid - inverted unpacking and its out= form
  match ~unpack (padded word buffer).

torch/transformers imports are inside tests only (collection must not pull
torch into sys.modules, see test_import_without_torch_numpy).
"""

import os

import pytest

CORE_IS_STUB = os.environ.get("BLG_CORE_STUB") == "1"

import bolorgir as zc
from bolorgir import Engine, TokenizerBundle

EOS_ID = 256
VOCAB = 257
PAD_ID = 0  # byte 0x00: outside any literal below, the HF "pad"

needs_core_masks = pytest.mark.skipif(
    CORE_IS_STUB, reason="stub core zeroes fill_mask masks"
)


def mini_bundle() -> TokenizerBundle:
    tokens = [bytes([b]) for b in range(256)] + [b"<eos>"]
    return TokenizerBundle.from_token_bytes(tokens, eos_ids=[EOS_ID])


def make_processor(zt, constraints, prompt_len):
    torch, processor_cls = zt
    sessions = [c.create_session() for c in constraints]
    proc = processor_cls(sessions, prompt_len, eos_ids=(EOS_ID,))
    return proc, sessions


@pytest.fixture
def zt():
    torch = pytest.importorskip("torch")
    from bolorgir.transformers import ConstraintLogitsProcessor

    return torch, ConstraintLogitsProcessor


# ----------------------------------------------------------------------
# B-1: padded lm_head (scores.shape[-1] > engine vocab)
# ----------------------------------------------------------------------

@needs_core_masks
def test_padded_lm_head_tail_masked(zt):
    torch, _ = zt
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a"], tokenizer=mini_bundle())
        proc, sessions = make_processor(zt, [c], prompt_len=1)
        ids = torch.zeros((1, 1), dtype=torch.long)
        scores = torch.zeros((1, 320))  # lm_head wider than the vocab (320 > 257)
        out = proc(ids, scores)
        finite = torch.isfinite(out[0]).nonzero().flatten().tolist()
        assert finite == [ord("a")]  # literal "a": only 'a' allowed at start
        assert torch.isinf(out[0, VOCAB:]).all()  # tail forbidden
        assert (out[0, VOCAB:] < 0).all()
        sessions[0].close()
        c.close()


@needs_core_masks
def test_exact_width_logits_regression(zt):
    torch, _ = zt
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a"], tokenizer=mini_bundle())
        proc, sessions = make_processor(zt, [c], prompt_len=1)
        ids = torch.zeros((1, 1), dtype=torch.long)
        scores = torch.zeros((1, VOCAB))  # width == vocab: the previous path
        out = proc(ids, scores)
        finite = torch.isfinite(out[0]).nonzero().flatten().tolist()
        assert finite == [ord("a")]
        sessions[0].close()
        c.close()


def test_narrower_than_vocab_logits_rejected(zt):
    torch, _ = zt
    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a"], tokenizer=mini_bundle())
        proc, sessions = make_processor(zt, [c], prompt_len=1)
        ids = torch.zeros((1, 1), dtype=torch.long)
        scores = torch.zeros((1, 100))  # narrower than the vocab - explicit error
        with pytest.raises(zc.ZigConstraintsError):
            proc(ids, scores)
        sessions[0].close()
        c.close()


# ----------------------------------------------------------------------
# D-1 (eighth run): fast forbid path of mask unpacking
# ----------------------------------------------------------------------

@needs_core_masks
def test_unpack_forbid_matches_unpack_inverted(zt):
    torch, _ = zt
    from bolorgir.transformers import MaskGpuUnpacker

    with Engine(mode="lazy") as engine:
        c = engine.compile_literals(["a", "b"], tokenizer=mini_bundle())
        sess = c.create_session()
        mask = sess.fill_mask()
        up = MaskGpuUnpacker()
        dev = torch.device("cpu")
        nwords = (VOCAB + 31) // 32
        allowed = up.unpack(mask, VOCAB, dev).clone()  # the buffer is reused
        forbid = up.unpack_forbid(mask, VOCAB, dev).clone()
        assert forbid.shape == (1, nwords * 32)
        assert bool((forbid[:, :VOCAB] == ~allowed).all())
        # the out= form writes into the passed buffer and returns it
        out = torch.zeros(1, nwords * 32, dtype=torch.bool)
        ret = up.unpack_forbid(mask, VOCAB, dev, out=out)
        assert ret is out
        assert bool((out[:, :VOCAB] == ~allowed).all())
        sess.close()
        c.close()


# ----------------------------------------------------------------------
# B-3: early EOS of one batch row; HF pads never reach accept_token
# ----------------------------------------------------------------------

@needs_core_masks
def test_batch_early_eos_row_skips_hf_padding(zt):
    torch, _ = zt
    with Engine(mode="lazy") as engine:
        bundle = mini_bundle()
        c0 = engine.compile_literals(["a"], tokenizer=bundle)
        c1 = engine.compile_literals(["ab"], tokenizer=bundle)
        proc, sessions = make_processor(zt, [c0, c1], prompt_len=1)

        # step 0: prompt only
        ids = torch.zeros((2, 1), dtype=torch.long)
        proc(ids, torch.zeros((2, VOCAB)))

        # step 1: both rows picked 'a'
        ids = torch.tensor([[0, ord("a")], [0, ord("a")]])
        proc(ids, torch.zeros((2, VOCAB)))

        # step 2: row 0 finished the document and picked EOS; row 1 picked 'b'
        ids = torch.tensor([[0, ord("a"), EOS_ID], [0, ord("a"), ord("b")]])
        proc(ids, torch.zeros((2, VOCAB)))
        assert proc.eos_done == [True, False]
        assert proc.active == [False, True]

        # step 3: HF padded the finished row 0, row 1 picked EOS.
        # Before the B-3 fix the pad reached accept_token -> InvalidTokenError.
        ids = torch.tensor(
            [[0, ord("a"), EOS_ID, PAD_ID], [0, ord("a"), ord("b"), EOS_ID]]
        )
        proc(ids, torch.zeros((2, VOCAB)))
        assert proc.eos_done == [True, True]

        done0 = proc.finish_row(0, ids[0].tolist())
        done1 = proc.finish_row(1, ids[1].tolist())
        assert done0 == (True, "eos")
        assert done1 == (True, "eos")
        for s in sessions:
            s.close()
        c0.close()
        c1.close()


# ----------------------------------------------------------------------
# B-2: __exit__ with an active exception does not mask it with a cleanup error
# ----------------------------------------------------------------------

def test_engine_exit_does_not_mask_active_exception():
    with pytest.raises(ValueError, match="boom"):
        with Engine(mode="lazy") as engine:
            c = engine.compile_literals(["a"], tokenizer=mini_bundle())
            s = c.create_session()
            _keep_alive = (c, s)  # live wrappers -> close() would raise BusyError
            raise ValueError("boom")


def test_engine_exit_raises_cleanup_error_without_active_exception():
    engine = Engine(mode="lazy")
    c = engine.compile_literals(["a"], tokenizer=mini_bundle())
    s = c.create_session()
    _keep_alive = (c, s)
    with pytest.raises(zc.BusyError):
        with engine:
            pass  # no exception: the cleanup error must be raised
    s.close()
    c.close()
    engine.close()
