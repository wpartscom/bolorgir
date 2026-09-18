"""Регрессионные тесты HF-интеграции (bugs B-1/B-2/B-3) без реальной модели.

ConstraintLogitsProcessor вызывается напрямую с фабричными input_ids/scores
(CPU, побайтовый мини-токенизатор: vocab 257 = 256 байт + EOS); веса моделей
не скачиваются. Маски — настоящего ядра; под stub-ядром (ZG_CORE_STUB=1,
маски занулены) маскозависимые тесты пропускаются.

Покрытие:
- B-1: ширина lm_head больше словаря ограничений (padded vocab) — хвост
  логитов запрещается (-inf), маска применяется к первым vocab позициям;
- B-3: строка батча завершилась EOS раньше остальных — pad'ы HF после EOS
  не попадают в accept_token, fill_mask для завершённой строки не зовётся;
- B-2: Engine.__exit__ при активном исключении не маскирует его BusyError.

Импорты torch/transformers — только внутри тестов (collection не должен
тащить torch в sys.modules, см. test_import_without_torch_numpy).
"""

import os

import pytest

CORE_IS_STUB = os.environ.get("ZG_CORE_STUB") == "1"

import zig_constraints as zc
from zig_constraints import Engine, TokenizerBundle

EOS_ID = 256
VOCAB = 257
PAD_ID = 0  # байт 0x00: вне любого literal'а ниже, «pad» HF

needs_core_masks = pytest.mark.skipif(
    CORE_IS_STUB, reason="stub-ядро зануляет маски fill_mask"
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
    from zig_constraints.transformers import ConstraintLogitsProcessor

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
        scores = torch.zeros((1, 320))  # lm_head шире словаря (320 > 257)
        out = proc(ids, scores)
        finite = torch.isfinite(out[0]).nonzero().flatten().tolist()
        assert finite == [ord("a")]  # literal "a": на старте разрешён только 'a'
        assert torch.isinf(out[0, VOCAB:]).all()  # хвост запрещён
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
        scores = torch.zeros((1, VOCAB))  # ширина == vocab: прежний путь
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
        scores = torch.zeros((1, 100))  # уже словаря — явная ошибка
        with pytest.raises(zc.ZigConstraintsError):
            proc(ids, scores)
        sessions[0].close()
        c.close()


# ----------------------------------------------------------------------
# B-3: ранний EOS одной строки батча; pad'ы HF не идут в accept_token
# ----------------------------------------------------------------------

@needs_core_masks
def test_batch_early_eos_row_skips_hf_padding(zt):
    torch, _ = zt
    with Engine(mode="lazy") as engine:
        bundle = mini_bundle()
        c0 = engine.compile_literals(["a"], tokenizer=bundle)
        c1 = engine.compile_literals(["ab"], tokenizer=bundle)
        proc, sessions = make_processor(zt, [c0, c1], prompt_len=1)

        # шаг 0: только промпт
        ids = torch.zeros((2, 1), dtype=torch.long)
        proc(ids, torch.zeros((2, VOCAB)))

        # шаг 1: обе строки выбрали 'a'
        ids = torch.tensor([[0, ord("a")], [0, ord("a")]])
        proc(ids, torch.zeros((2, VOCAB)))

        # шаг 2: строка 0 завершила документ и выбрала EOS; строка 1 — 'b'
        ids = torch.tensor([[0, ord("a"), EOS_ID], [0, ord("a"), ord("b")]])
        proc(ids, torch.zeros((2, VOCAB)))
        assert proc.eos_done == [True, False]
        assert proc.active == [False, True]

        # шаг 3: HF добил завершённую строку 0 pad'ом, строка 1 выбрала EOS.
        # До фикса B-3 pad попадал в accept_token -> InvalidTokenError.
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
# B-2: __exit__ при активном исключении не маскирует его ошибкой очистки
# ----------------------------------------------------------------------

def test_engine_exit_does_not_mask_active_exception():
    with pytest.raises(ValueError, match="boom"):
        with Engine(mode="lazy") as engine:
            c = engine.compile_literals(["a"], tokenizer=mini_bundle())
            s = c.create_session()
            _keep_alive = (c, s)  # живые обёртки -> close() был бы BusyError
            raise ValueError("boom")


def test_engine_exit_raises_cleanup_error_without_active_exception():
    engine = Engine(mode="lazy")
    c = engine.compile_literals(["a"], tokenizer=mini_bundle())
    s = c.create_session()
    _keep_alive = (c, s)
    with pytest.raises(zc.BusyError):
        with engine:
            pass  # исключения нет: ошибка очистки должна подниматься
    s.close()
    c.close()
    engine.close()
