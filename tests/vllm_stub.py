"""Stand-in for the vLLM 0.31.0 structured-output interfaces.

The classes mirror, member for member, the abstract interface of the
pinned upstream tag (`vllm/v1/structured_output/backend_types.py` and
`vllm/exceptions.py`): the same enum members, the same abstract methods
and the same dataclass fields of ``StructuredOutputBackend``. It exists
so the Bolorgir backend can be exercised on CPU without the vLLM stack;
`tests/test_vllm_backend.py` compares the member sets against the real
source when ``BLG_VLLM_SRC`` points at the pinned tree.

`install()` registers the stand-in modules in ``sys.modules`` only when
no real ``vllm`` package is importable.
"""

from __future__ import annotations

import enum
import importlib.util
import sys
import types
from abc import ABC, abstractmethod
from dataclasses import dataclass


class StructuredOutputOptions(enum.Enum):
    JSON = enum.auto()
    JSON_OBJECT = enum.auto()
    REGEX = enum.auto()
    GRAMMAR = enum.auto()
    CHOICE = enum.auto()
    STRUCTURAL_TAG = enum.auto()


class StructuredOutputGrammar(ABC):
    @abstractmethod
    def accept_tokens(self, request_id: str, tokens: list) -> bool: ...

    @abstractmethod
    def validate_tokens(self, tokens: list) -> list: ...

    @abstractmethod
    def rollback(self, num_tokens: int) -> None: ...

    @abstractmethod
    def fill_bitmask(self, bitmask, batch_index: int) -> None: ...

    @abstractmethod
    def is_terminated(self) -> bool: ...

    @abstractmethod
    def reset(self): ...


@dataclass
class StructuredOutputBackend(ABC):
    vllm_config: object
    tokenizer: object
    vocab_size: int

    @abstractmethod
    def compile_grammar(self, request_type, grammar_spec: str,
                        stop_token_ids=None) -> StructuredOutputGrammar: ...

    @abstractmethod
    def allocate_token_bitmask(self, max_num_seqs: int): ...

    @abstractmethod
    def destroy(self): ...


class VLLMValidationError(ValueError):
    def __init__(self, message: str, parameter: str | None = None,
                 value=None) -> None:
        super().__init__(message)
        self.parameter = parameter
        self.value = value


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


def install() -> bool:
    """Install the stand-in unless a real vllm is importable.

    Returns True when the stand-in is active.
    """
    existing = sys.modules.get("vllm")
    if existing is not None:
        return bool(getattr(existing, "_bolorgir_stub", False))
    if importlib.util.find_spec("vllm") is not None:
        return False
    vllm = _module("vllm")
    v1 = _module("vllm.v1")
    so = _module("vllm.v1.structured_output")
    _module(
        "vllm.v1.structured_output.backend_types",
        StructuredOutputOptions=StructuredOutputOptions,
        StructuredOutputGrammar=StructuredOutputGrammar,
        StructuredOutputBackend=StructuredOutputBackend,
    )
    _module("vllm.exceptions", VLLMValidationError=VLLMValidationError)
    vllm.v1 = v1
    vllm._bolorgir_stub = True
    v1.structured_output = so
    so.backend_types = sys.modules["vllm.v1.structured_output.backend_types"]
    return True
