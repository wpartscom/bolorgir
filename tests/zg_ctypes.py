"""Thin ctypes layer over zig-out/lib/libzig_constraints.so.

Fallback path to the core (when the zig_constraints package is not built) and
the basis for the T6 C ABI smoke test. Structs and codes follow
include/zig_constraints.h exactly. Knows nothing about the Python package.

Importing this module fails with ImportError if the .so is missing/unloadable;
callers use that for pre-integration skips.
"""

from __future__ import annotations

import ctypes
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# --- zg_status codes ---
ZG_OK = 0
ZG_ERR_INVALID_ARGUMENT = 1
ZG_ERR_INVALID_SCHEMA = 2
ZG_ERR_UNSUPPORTED_FEATURE = 3
ZG_ERR_UNSATISFIABLE_CONSTRAINT = 4
ZG_ERR_UNSUPPORTED_TOKENIZER = 5
ZG_ERR_INVALID_TOKEN = 6
ZG_ERR_DEAD_END = 7
ZG_ERR_RESOURCE_LIMIT = 8
ZG_ERR_CANCELLED = 9
ZG_ERR_BUSY = 10
ZG_ERR_WRONG_STATE = 11
ZG_ERR_BUFFER_TOO_SMALL = 12
ZG_ERR_INTERNAL = 13

STATUS_NAMES = {
    ZG_OK: "OK",
    ZG_ERR_INVALID_ARGUMENT: "INVALID_ARGUMENT",
    ZG_ERR_INVALID_SCHEMA: "INVALID_SCHEMA",
    ZG_ERR_UNSUPPORTED_FEATURE: "UNSUPPORTED_FEATURE",
    ZG_ERR_UNSATISFIABLE_CONSTRAINT: "UNSATISFIABLE_CONSTRAINT",
    ZG_ERR_UNSUPPORTED_TOKENIZER: "UNSUPPORTED_TOKENIZER",
    ZG_ERR_INVALID_TOKEN: "INVALID_TOKEN",
    ZG_ERR_DEAD_END: "DEAD_END",
    ZG_ERR_RESOURCE_LIMIT: "RESOURCE_LIMIT",
    ZG_ERR_CANCELLED: "CANCELLED",
    ZG_ERR_BUSY: "BUSY",
    ZG_ERR_WRONG_STATE: "WRONG_STATE",
    ZG_ERR_BUFFER_TOO_SMALL: "BUFFER_TOO_SMALL",
    ZG_ERR_INTERNAL: "INTERNAL",
}

ZG_ABI_VERSION = 1
ZG_ERROR_MESSAGE_CAP = 256
ZG_JSON_POINTER_CAP = 256
ZG_STATS_CATEGORY_COUNT = 8
ZG_MEM_TOKENIZER = 0
ZG_MEM_GRAMMAR = 1
ZG_MEM_SESSION = 2
ZG_MEM_CACHE = 3
ZG_MEM_TEMP = 4
ZG_MEM_TOTAL = 5

ZG_MODE_LAZY = 0
ZG_MODE_ADAPTIVE = 1
ZG_MODE_PRECOMPUTE = 2

ZG_CONSTRAINT_JSON_SCHEMA = 0
ZG_CONSTRAINT_LITERAL_SET = 1


def _find_lib() -> str:
    candidates = [
        os.path.join(_ROOT, "zig-out", "lib", "libzig_constraints.so"),
        os.path.join(_ROOT, "zig-out", "lib", "libzig_constraints.so.0.1.0"),
    ]
    env = os.environ.get("ZG_LIB_PATH")
    if env:
        candidates.insert(0, env)
    for path in candidates:
        if os.path.exists(path):
            return path
    raise ModuleNotFoundError(
        "libzig_constraints.so not found (looked in zig-out/lib); "
        "the core is not built yet")


class ZgError(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("code", ctypes.c_int32),
        ("schema_offset", ctypes.c_uint32),
        ("message", ctypes.c_char * ZG_ERROR_MESSAGE_CAP),
        ("json_pointer", ctypes.c_char * ZG_JSON_POINTER_CAP),
    ]


class ZgContextConfig(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("mode", ctypes.c_uint32),
        ("max_depth", ctypes.c_uint32),
        ("max_threads_per_state", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
        ("memory_limit_bytes", ctypes.c_uint64),
        ("cache_limit_bytes", ctypes.c_uint64),
        ("session_limit_bytes", ctypes.c_uint64),
        ("schema_limit_bytes", ctypes.c_uint64),
        ("work_limit_ops", ctypes.c_uint64),
        ("adaptive_min_hits", ctypes.c_uint64),
        ("adaptive_min_cost_ns", ctypes.c_uint64),
        ("precompute_max_states", ctypes.c_uint64),
    ]


class ZgTokenEntry(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("offset", ctypes.c_uint64),
        ("length", ctypes.c_uint64),
    ]


class ZgTokenizerDesc(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("vocab_size", ctypes.c_uint32),
        ("entries", ctypes.POINTER(ZgTokenEntry)),
        ("entry_count", ctypes.c_size_t),
        ("blob", ctypes.POINTER(ctypes.c_uint8)),
        ("blob_len", ctypes.c_size_t),
        ("eos_ids", ctypes.POINTER(ctypes.c_uint32)),
        ("eos_count", ctypes.c_size_t),
        ("special_ids", ctypes.POINTER(ctypes.c_uint32)),
        ("special_count", ctypes.c_size_t),
    ]


class ZgCompileRequest(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("kind", ctypes.c_uint32),
        ("profile", ctypes.c_char_p),
        ("data", ctypes.POINTER(ctypes.c_uint8)),
        ("data_len", ctypes.c_size_t),
    ]


class ZgStats(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("version", ctypes.c_uint32),
        ("compile_ns", ctypes.c_uint64),
        ("tokenizer_prepare_ns", ctypes.c_uint64),
        ("accept_ns_total", ctypes.c_uint64),
        ("mask_ns_total", ctypes.c_uint64),
        ("mask_calls", ctypes.c_uint64),
        ("tokens_accepted", ctypes.c_uint64),
        ("cache_hits", ctypes.c_uint64),
        ("cache_misses", ctypes.c_uint64),
        ("cache_evictions", ctypes.c_uint64),
        ("mem_used", ctypes.c_uint64 * ZG_STATS_CATEGORY_COUNT),
        ("mem_peak", ctypes.c_uint64 * ZG_STATS_CATEGORY_COUNT),
        ("mode", ctypes.c_uint32),
        ("reserved0", ctypes.c_uint32),
        ("errors_total", ctypes.c_uint64),
        ("errors_resource_limit", ctypes.c_uint64),
        ("errors_cancelled", ctypes.c_uint64),
        ("cache_adaptive_skips", ctypes.c_uint64),
        ("precompute_states", ctypes.c_uint64),
        ("work_ops_total", ctypes.c_uint64),
    ]


def _load():
    path = _find_lib()
    try:
        lib = ctypes.CDLL(path)
    except OSError as e:
        raise ModuleNotFoundError(f"cannot load {path}: {e}") from None
    if not hasattr(lib, "zg_abi_version"):
        raise ModuleNotFoundError(f"{path}: no zg_abi_version export")

    void_p = ctypes.c_void_p
    lib.zg_abi_version.argtypes = []
    lib.zg_abi_version.restype = ctypes.c_uint32

    lib.zg_context_create.argtypes = [
        ctypes.POINTER(ZgContextConfig), ctypes.POINTER(ZgTokenizerDesc),
        ctypes.POINTER(void_p), ctypes.POINTER(ZgError)]
    lib.zg_context_create.restype = ctypes.c_int

    lib.zg_context_destroy.argtypes = [void_p]
    lib.zg_context_destroy.restype = ctypes.c_int

    lib.zg_compile.argtypes = [
        void_p, ctypes.POINTER(ZgCompileRequest),
        ctypes.POINTER(void_p), ctypes.POINTER(ZgError)]
    lib.zg_compile.restype = ctypes.c_int

    lib.zg_grammar_release.argtypes = [void_p]
    lib.zg_grammar_release.restype = None

    lib.zg_session_create.argtypes = [
        void_p, void_p, ctypes.POINTER(void_p), ctypes.POINTER(ZgError)]
    lib.zg_session_create.restype = ctypes.c_int

    lib.zg_session_destroy.argtypes = [void_p]
    lib.zg_session_destroy.restype = None

    lib.zg_fill_mask.argtypes = [
        void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t,
        ctypes.POINTER(ZgError)]
    lib.zg_fill_mask.restype = ctypes.c_int

    lib.zg_fill_masks_batch.argtypes = [
        ctypes.POINTER(void_p), ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32)),
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_int32), ctypes.c_size_t,
        ctypes.POINTER(ZgError)]
    lib.zg_fill_masks_batch.restype = ctypes.c_int

    lib.zg_accept_token.argtypes = [void_p, ctypes.c_uint32, ctypes.POINTER(ZgError)]
    lib.zg_accept_token.restype = ctypes.c_int

    lib.zg_can_end.argtypes = [void_p, ctypes.POINTER(ctypes.c_bool)]
    lib.zg_can_end.restype = ctypes.c_int

    lib.zg_finish.argtypes = [void_p, ctypes.POINTER(ZgError)]
    lib.zg_finish.restype = ctypes.c_int

    lib.zg_abort.argtypes = [void_p]
    lib.zg_abort.restype = ctypes.c_int

    lib.zg_get_stats.argtypes = [void_p, ctypes.POINTER(ZgStats)]
    lib.zg_get_stats.restype = ctypes.c_int

    lib.zg_get_stats_session.argtypes = [void_p, ctypes.POINTER(ZgStats)]
    lib.zg_get_stats_session.restype = ctypes.c_int

    lib.zg_cancel_flag_set.argtypes = [void_p, ctypes.POINTER(ctypes.c_uint8)]
    lib.zg_cancel_flag_set.restype = ctypes.c_int

    lib._path = path
    return lib


LIB = _load()


def new_error() -> ZgError:
    err = ZgError()
    err.struct_size = ctypes.sizeof(ZgError)
    return err


def err_text(err: ZgError) -> str:
    return err.message.split(b"\0", 1)[0].decode("utf-8", "replace")


class CoreFailure(Exception):
    """Non-OK zg_status where the test expected ZG_OK."""

    def __init__(self, status: int, where: str, err: ZgError | None = None):
        self.status = status
        msg = STATUS_NAMES.get(status, f"status={status}")
        detail = f" ({err_text(err)})" if err is not None and err.code != 0 else ""
        super().__init__(f"{where}: {msg}{detail}")


def make_tokenizer_desc(spec):
    """TokenizerSpec (tests/reference.py) -> (desc, keepalive)."""
    blob = bytearray()
    entries = (ZgTokenEntry * spec.vocab_size)()
    for i, tok in enumerate(spec.tokens):
        entries[i].id = i
        entries[i].offset = len(blob)
        entries[i].length = len(tok)
        blob += tok
    blob_arr = (ctypes.c_uint8 * max(len(blob), 1)).from_buffer_copy(bytes(blob) or b"\0")
    eos_arr = (ctypes.c_uint32 * max(len(spec.eos_ids), 1))(*spec.eos_ids) if spec.eos_ids \
        else (ctypes.c_uint32 * 1)()
    sp_arr = (ctypes.c_uint32 * max(len(spec.special_ids), 1))(*spec.special_ids) if spec.special_ids \
        else (ctypes.c_uint32 * 1)()
    desc = ZgTokenizerDesc()
    desc.struct_size = ctypes.sizeof(ZgTokenizerDesc)
    desc.vocab_size = spec.vocab_size
    desc.entries = entries
    desc.entry_count = spec.vocab_size
    desc.blob = blob_arr
    desc.blob_len = len(blob)
    desc.eos_ids = eos_arr
    desc.eos_count = len(spec.eos_ids)
    desc.special_ids = sp_arr
    desc.special_count = len(spec.special_ids)
    return desc, (entries, blob_arr, eos_arr, sp_arr)


class Context:
    """Live core context + buffer keepalive. destroy() -> zg_status."""

    def __init__(self, spec, mode=ZG_MODE_LAZY, *, memory_limit_bytes=0,
                 cache_limit_bytes=0, session_limit_bytes=0,
                 schema_limit_bytes=0, max_depth=0, max_threads_per_state=0,
                 work_limit_ops=0, adaptive_min_hits=0, adaptive_min_cost_ns=0,
                 precompute_max_states=0):
        config = ZgContextConfig()
        config.struct_size = ctypes.sizeof(ZgContextConfig)
        config.version = ZG_ABI_VERSION
        config.mode = mode
        config.max_depth = max_depth
        config.max_threads_per_state = max_threads_per_state
        config.memory_limit_bytes = memory_limit_bytes
        config.cache_limit_bytes = cache_limit_bytes
        config.session_limit_bytes = session_limit_bytes
        config.schema_limit_bytes = schema_limit_bytes
        config.work_limit_ops = work_limit_ops
        config.adaptive_min_hits = adaptive_min_hits
        config.adaptive_min_cost_ns = adaptive_min_cost_ns
        config.precompute_max_states = precompute_max_states
        self._config = config
        self._desc, self._keep = make_tokenizer_desc(spec)
        self.spec = spec
        out = ctypes.c_void_p()
        err = new_error()
        status = LIB.zg_context_create(
            ctypes.byref(config), ctypes.byref(self._desc),
            ctypes.byref(out), ctypes.byref(err))
        if status != ZG_OK:
            raise CoreFailure(status, "zg_context_create", err)
        self.handle = out
        self._destroyed = False

    def destroy(self) -> int:
        if self._destroyed:
            return ZG_OK
        status = LIB.zg_context_destroy(self.handle)
        if status == ZG_OK:
            self._destroyed = True
        return status

    def get_stats(self) -> ZgStats:
        stats = ZgStats()
        stats.struct_size = ctypes.sizeof(ZgStats)
        stats.version = ZG_ABI_VERSION
        status = LIB.zg_get_stats(self.handle, ctypes.byref(stats))
        if status != ZG_OK:
            raise CoreFailure(status, "zg_get_stats")
        return stats


def compile_grammar(ctx: Context, data: bytes, kind: int) -> ctypes.c_void_p:
    req = ZgCompileRequest()
    req.struct_size = ctypes.sizeof(ZgCompileRequest)
    req.kind = kind
    req.profile = b"canonical-v1"
    buf = (ctypes.c_uint8 * max(len(data), 1)).from_buffer_copy(data or b"\0")
    req.data = buf
    req.data_len = len(data)
    out = ctypes.c_void_p()
    err = new_error()
    status = LIB.zg_compile(ctx.handle, ctypes.byref(req),
                            ctypes.byref(out), ctypes.byref(err))
    if status != ZG_OK:
        e = CoreFailure(status, "zg_compile", err)
        e.schema_offset = err.schema_offset
        raise e
    return out  # caller need not keep buf: the core copies data


def compile_schema(ctx: Context, schema, ) -> ctypes.c_void_p:
    import json as _json
    if isinstance(schema, (bytes, bytearray)):
        data = bytes(schema)
    elif isinstance(schema, str):
        data = schema.encode("utf-8")
    else:
        data = _json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return compile_grammar(ctx, data, ZG_CONSTRAINT_JSON_SCHEMA)


def compile_literals(ctx: Context, strings) -> ctypes.c_void_p:
    import json as _json
    data = _json.dumps(list(strings), ensure_ascii=False).encode("utf-8")
    return compile_grammar(ctx, data, ZG_CONSTRAINT_LITERAL_SET)


def grammar_release(grammar) -> None:
    LIB.zg_grammar_release(grammar)


class Session:
    def __init__(self, ctx: Context, grammar):
        self.ctx = ctx
        out = ctypes.c_void_p()
        err = new_error()
        status = LIB.zg_session_create(ctx.handle, grammar,
                                       ctypes.byref(out), ctypes.byref(err))
        if status != ZG_OK:
            raise CoreFailure(status, "zg_session_create", err)
        self.handle = out
        self._destroyed = False

    def fill_mask_words(self, words: int | None = None):
        """-> (status, list[int] words). Too few words -> BUFFER_TOO_SMALL."""
        if words is None:
            words = self.ctx.spec.mask_words
        buf = (ctypes.c_uint32 * words)()
        err = new_error()
        status = LIB.zg_fill_mask(self.handle, buf, words, ctypes.byref(err))
        return status, list(buf)

    def fill_mask_ids(self) -> set[int]:
        status, words = self.fill_mask_words()
        if status != ZG_OK:
            raise CoreFailure(status, "zg_fill_mask")
        ids = set()
        for i in range(self.ctx.spec.vocab_size):
            if words[i // 32] >> (i % 32) & 1:
                ids.add(i)
        return ids

    def accept(self, token_id: int) -> int:
        err = new_error()
        return LIB.zg_accept_token(self.handle, token_id, ctypes.byref(err))

    def can_end(self) -> bool:
        out = ctypes.c_bool()
        status = LIB.zg_can_end(self.handle, ctypes.byref(out))
        if status != ZG_OK:
            raise CoreFailure(status, "zg_can_end")
        return bool(out.value)

    def finish(self) -> int:
        err = new_error()
        return LIB.zg_finish(self.handle, ctypes.byref(err))

    def abort(self) -> int:
        return LIB.zg_abort(self.handle)

    def get_stats(self) -> ZgStats:
        stats = ZgStats()
        stats.struct_size = ctypes.sizeof(ZgStats)
        stats.version = ZG_ABI_VERSION
        status = LIB.zg_get_stats_session(self.handle, ctypes.byref(stats))
        if status != ZG_OK:
            raise CoreFailure(status, "zg_get_stats_session")
        return stats

    def destroy(self):
        if not self._destroyed:
            LIB.zg_session_destroy(self.handle)
            self._destroyed = True


def fill_masks_batch(sessions: list[Session], words_each: int):
    """-> (overall_status, statuses list). Empty batch ([]) -> (ZG_OK, [])."""
    n = len(sessions)
    if n == 0:
        err = new_error()
        status = LIB.zg_fill_masks_batch(None, None, 0, None, 0, ctypes.byref(err))
        return status, []
    handles = (ctypes.c_void_p * n)(*(s.handle.value for s in sessions))
    mask_bufs = [(ctypes.c_uint32 * words_each)() for _ in sessions]
    mask_ptrs = (ctypes.POINTER(ctypes.c_uint32) * n)(*mask_bufs)
    statuses = (ctypes.c_int32 * n)()
    err = new_error()
    status = LIB.zg_fill_masks_batch(handles, mask_ptrs, words_each,
                                     statuses, n, ctypes.byref(err))
    return status, list(statuses)
