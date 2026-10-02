"""Fixtures: mini tokenizers and backend access to the core.

Two backends: the bolorgir package (Engine/Constraint/Session) and a
direct ctypes fallback to zig-out/lib/libbolorgir.so (tests/blg_ctypes.py).
Selection: BLG_TEST_BACKEND = package | ctypes | auto (default auto).
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reference  # noqa: E402
from reference import TokenizerSpec  # noqa: E402


# ---------------------------------------------------------------------------
# Mini tokenizers
# ---------------------------------------------------------------------------

def make_byte_tokenizer() -> TokenizerSpec:
    """Byte-level: one token per byte (id 0..255), eos=256, pad=257.

    Vocab 258 is deliberately not a multiple of 32.
    """
    tokens = tuple(bytes([i]) for i in range(256)) + (b"", b"")
    return TokenizerSpec(tokens=tokens, eos_ids=(256,), special_ids=(257,)).validate()


def make_dict_tokenizer() -> TokenizerSpec:
    """Byte tokens plus multi-character JSON pieces.

    Covers multi-structural-char tokens (`{"`, `":"`, `",`), value chunks
    (`"buy"`, `"sell"`), two-digit numbers (`10`, `42`), a whole escape
    (`\\n`) alongside its byte halves (`\\` id 92, `n` id 110), a `\\u00`
    chunk and a whole UTF-8 chunk (`é`).
    Vocab 268 (not a multiple of 32): 256 bytes + 10 pieces + eos 266 + pad 267.
    """
    pieces = [
        b'{"', b'":"', b'",', b'"buy"', b'"sell"', b"10", b"42",
        b"\\n", b"\\u00", "é".encode("utf-8"),
    ]
    tokens = tuple(bytes([i]) for i in range(256)) + tuple(pieces) + (b"", b"")
    vocab = len(tokens)
    assert vocab == 268 and vocab % 32 != 0
    return TokenizerSpec(tokens=tokens, eos_ids=(vocab - 2,),
                         special_ids=(vocab - 1,)).validate()


@pytest.fixture(scope="session")
def byte_tok() -> TokenizerSpec:
    return make_byte_tokenizer()


@pytest.fixture(scope="session")
def dict_tok() -> TokenizerSpec:
    return make_dict_tokenizer()


# ---------------------------------------------------------------------------
# Backend abstraction
# ---------------------------------------------------------------------------

class BackendUnavailable(Exception):
    pass


def _status_code_of_exception(exc: BaseException) -> int | None:
    """Map a bolorgir package exception to a blg_status code (best-effort)."""
    import blg_ctypes
    for cls in type(exc).__mro__:
        name = cls.__name__
        for suffix in ("Error", "Exception"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        snake = []
        for ch in name:
            if ch.isupper() and snake:
                snake.append("_")
            snake.append(ch.upper())
        snake = "".join(snake)
        for code, sname in blg_ctypes.STATUS_NAMES.items():
            if sname == snake:
                return code
    for attr in ("code", "status", "blg_status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    return None


class CtypesBackend:
    name = "ctypes"

    def __init__(self, spec: TokenizerSpec, mode: str):
        import blg_ctypes
        mode_code = {"lazy": blg_ctypes.BLG_MODE_LAZY,
                     "adaptive": blg_ctypes.BLG_MODE_ADAPTIVE,
                     "precompute": blg_ctypes.BLG_MODE_PRECOMPUTE}[mode]
        self.zg = blg_ctypes
        self.ctx = blg_ctypes.Context(spec, mode_code)

    def compile(self, schema) -> "CtypesConstraint":
        grammar = self.zg.compile_schema(self.ctx, schema)
        return CtypesConstraint(self, grammar)

    def compile_literals(self, strings) -> "CtypesConstraint":
        grammar = self.zg.compile_literals(self.ctx, strings)
        return CtypesConstraint(self, grammar)

    def close(self):
        self.ctx.destroy()


class CtypesConstraint:
    def __init__(self, backend: CtypesBackend, grammar):
        self.backend = backend
        self.grammar = grammar

    def create_session(self) -> "CtypesSessionWrap":
        return CtypesSessionWrap(self.backend.zg.Session(self.backend.ctx, self.grammar))

    def release(self):
        self.backend.zg.grammar_release(self.grammar)


class CtypesSessionWrap:
    """Common session interface for tests (see PackageSession too)."""

    def __init__(self, raw):
        self.raw = raw

    def mask(self) -> set[int]:
        return self.raw.fill_mask_ids()

    def accept(self, token_id: int) -> int:
        return self.raw.accept(token_id)

    def can_end(self) -> bool:
        return self.raw.can_end()

    def finish(self) -> int:
        return self.raw.finish()

    def abort(self) -> int:
        return self.raw.abort()

    def close(self):
        self.raw.destroy()


class PackageBackend:
    """Backend via the bolorgir package (Engine/Constraint/Session)."""

    name = "package"

    def __init__(self, spec: TokenizerSpec, mode: str):
        import bolorgir as zg
        self.zg = zg
        self.spec = spec
        self.engine = zg.Engine(mode=mode)
        self.tokenizer = self._make_tokenizer(zg, spec)

    @staticmethod
    def _make_tokenizer(zg, spec: TokenizerSpec):
        # TokenizerBundle rejects empty bytes for non-special ids. EOS bytes
        # never enter the document, so an empty EOS gets a placeholder byte
        # (same convention as TokenizerBundle._build_from_hf).
        special = set(spec.special_ids)
        tokens = [
            bytes(t) if t or i in special else b"\x00"
            for i, t in enumerate(spec.tokens)
        ]
        return zg.TokenizerBundle.from_token_bytes(
            tokens, eos_ids=list(spec.eos_ids),
            special_ids=list(spec.special_ids))

    def compile(self, schema) -> "PackageConstraint":
        if not isinstance(schema, (dict, str, bytes, bytearray)):
            schema = json.dumps(schema)
        return PackageConstraint(self.engine.compile(
            schema, tokenizer=self.tokenizer, profile="canonical-v1"))

    def compile_literals(self, strings) -> "PackageConstraint":
        return PackageConstraint(self.engine.compile_literals(
            list(strings), tokenizer=self.tokenizer))

    def close(self):
        self.engine.close()


class PackageConstraint:
    def __init__(self, constraint):
        self.constraint = constraint

    def create_session(self) -> "PackageSession":
        return PackageSession(self.constraint.create_session())

    def release(self):
        self.constraint.close()


class PackageSession:
    """Same interface as CtypesSessionWrap; status codes via exception mapping."""

    def __init__(self, raw):
        self.raw = raw

    def mask(self) -> set[int]:
        return set(self.raw.allowed_token_ids())

    def _call(self, fname, *args) -> int:
        try:
            getattr(self.raw, fname)(*args)
        except Exception as e:
            code = _status_code_of_exception(e)
            if code is None:
                raise
            return code
        return 0

    def accept(self, token_id: int) -> int:
        return self._call("accept_token", token_id)

    def can_end(self) -> bool:
        return bool(self.raw.can_end())

    def finish(self) -> int:
        return self._call("finish")

    def abort(self) -> int:
        return self._call("abort")

    def close(self):
        self.raw.close()


def backend_available(kind: str) -> tuple[bool, str]:
    if kind in ("package", "auto"):
        try:
            import bolorgir  # noqa: F401
            return True, "package"
        except ImportError:
            if kind == "package":
                return False, "bolorgir package is not installed"
    if kind in ("ctypes", "auto"):
        try:
            import blg_ctypes  # noqa: F401
            return True, "ctypes"
        except ImportError as e:
            if kind == "ctypes":
                return False, str(e)
    return False, "neither bolorgir package nor libbolorgir.so available"


def make_backend(spec: TokenizerSpec, mode: str = "lazy", kind: str | None = None):
    """Backend factory; skips the test with a clear reason if the core is unavailable."""
    kind = kind or os.environ.get("BLG_TEST_BACKEND", "auto")
    if kind == "package":
        try:
            return PackageBackend(spec, mode)
        except ImportError:
            pytest.skip("bolorgir package is not importable")
        except BackendUnavailable as e:
            pytest.skip(f"bolorgir package is unusable for tests: {e}")
    if kind == "ctypes":
        try:
            return CtypesBackend(spec, mode)
        except ImportError as e:
            pytest.skip(f"ctypes backend unavailable: {e}")
    # auto: package -> ctypes
    try:
        return PackageBackend(spec, mode)
    except (ImportError, BackendUnavailable):
        pass
    try:
        return CtypesBackend(spec, mode)
    except ImportError as e:
        pytest.skip(f"core unavailable (neither package nor .so): {e}")


@pytest.fixture
def backend_factory():
    """make_backend(spec, mode) -> backend; closes backends after the test."""
    made = []

    def factory(spec, mode="lazy", kind=None):
        b = make_backend(spec, mode, kind)
        made.append(b)
        return b

    yield factory
    for b in made:
        try:
            b.close()
        except Exception:
            pass


@pytest.fixture(params=["lazy", "adaptive"])
def any_mode(request) -> str:
    return request.param


# ---------------------------------------------------------------------------
# Test utilities
# ---------------------------------------------------------------------------

def describe_ids(ids, spec: TokenizerSpec, limit: int = 16) -> str:
    """ids -> readable rendering for mismatch messages (TZ T2)."""
    parts = []
    for i in sorted(ids)[:limit]:
        if i in spec.eos_ids:
            parts.append(f"{i}<eos>")
        elif i in spec.special_ids:
            parts.append(f"{i}<special>")
        else:
            parts.append(f"{i}{spec.tokens[i]!r}")
    if len(ids) > limit:
        parts.append(f"...(+{len(ids) - limit})")
    return "{" + ", ".join(parts) + "}"


def assert_masks_equal(kernel_ids: set[int], oracle_ids: set[int], *,
                       schema_name: str, prefix: bytes, seq, spec: TokenizerSpec):
    if kernel_ids != oracle_ids:
        only_kernel = kernel_ids - oracle_ids
        only_oracle = oracle_ids - kernel_ids
        pytest.fail(
            f"mask mismatch\n"
            f"  schema: {schema_name}\n"
            f"  prefix bytes: {prefix!r}\n"
            f"  token ids prefix: {list(seq)}\n"
            f"  kernel-only: {describe_ids(only_kernel, spec)}\n"
            f"  oracle-only: {describe_ids(only_oracle, spec)}")
