"""Thin ctypes layer over zig-out/lib/libbolorgir.so.

Fallback path to the core (when the bolorgir package is not built) and
the basis for the T6 C ABI smoke test. Structs and codes follow
include/bolorgir.h exactly. Knows nothing about the Python package.

Importing this module fails with ImportError if the .so is missing/unloadable;
callers use that for pre-integration skips.
"""

from __future__ import annotations

import ctypes
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# --- blg_status codes ---
BLG_OK = 0
BLG_ERR_INVALID_ARGUMENT = 1
BLG_ERR_INVALID_SCHEMA = 2
BLG_ERR_UNSUPPORTED_FEATURE = 3
BLG_ERR_UNSATISFIABLE_CONSTRAINT = 4
BLG_ERR_UNSUPPORTED_TOKENIZER = 5
BLG_ERR_INVALID_TOKEN = 6
BLG_ERR_DEAD_END = 7
BLG_ERR_RESOURCE_LIMIT = 8
BLG_ERR_CANCELLED = 9
BLG_ERR_BUSY = 10
BLG_ERR_WRONG_STATE = 11
BLG_ERR_BUFFER_TOO_SMALL = 12
BLG_ERR_INTERNAL = 13

STATUS_NAMES = {
    BLG_OK: "OK",
    BLG_ERR_INVALID_ARGUMENT: "INVALID_ARGUMENT",
    BLG_ERR_INVALID_SCHEMA: "INVALID_SCHEMA",
    BLG_ERR_UNSUPPORTED_FEATURE: "UNSUPPORTED_FEATURE",
    BLG_ERR_UNSATISFIABLE_CONSTRAINT: "UNSATISFIABLE_CONSTRAINT",
    BLG_ERR_UNSUPPORTED_TOKENIZER: "UNSUPPORTED_TOKENIZER",
    BLG_ERR_INVALID_TOKEN: "INVALID_TOKEN",
    BLG_ERR_DEAD_END: "DEAD_END",
    BLG_ERR_RESOURCE_LIMIT: "RESOURCE_LIMIT",
    BLG_ERR_CANCELLED: "CANCELLED",
    BLG_ERR_BUSY: "BUSY",
    BLG_ERR_WRONG_STATE: "WRONG_STATE",
    BLG_ERR_BUFFER_TOO_SMALL: "BUFFER_TOO_SMALL",
    BLG_ERR_INTERNAL: "INTERNAL",
}

BLG_ABI_VERSION = 1
BLG_ERROR_MESSAGE_CAP = 256
BLG_JSON_POINTER_CAP = 256
BLG_STATS_CATEGORY_COUNT = 8
# BLG_CACHE_DEFAULT (include/bolorgir.h): UINT64_MAX = default budget
# (64 MiB); 0 disables the cache.
BLG_CACHE_DEFAULT = (1 << 64) - 1
BLG_MEM_TOKENIZER = 0
BLG_MEM_GRAMMAR = 1
BLG_MEM_SESSION = 2
BLG_MEM_CACHE = 3
BLG_MEM_TEMP = 4
BLG_MEM_TOTAL = 5

BLG_MODE_LAZY = 0
BLG_MODE_ADAPTIVE = 1
BLG_MODE_PRECOMPUTE = 2

# ADR-0007 blg_context_config.mask_fast_path values (0 is the default = on).
BLG_MASK_FAST_PATH_ON = 1
BLG_MASK_FAST_PATH_OFF = 2

BLG_CONSTRAINT_JSON_SCHEMA = 0
BLG_CONSTRAINT_LITERAL_SET = 1


def _find_lib() -> str:
    candidates = [
        os.path.join(_ROOT, "zig-out", "lib", "libbolorgir.so"),
        os.path.join(_ROOT, "zig-out", "lib", "libbolorgir.so.0.1.0"),
    ]
    env = os.environ.get("BLG_LIB_PATH")
    if env:
        candidates.insert(0, env)
    for path in candidates:
        if os.path.exists(path):
            return path
    raise ModuleNotFoundError(
        "libbolorgir.so not found (looked in zig-out/lib); "
        "the core is not built yet")


class ZgError(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("code", ctypes.c_int32),
        ("schema_offset", ctypes.c_uint32),
        ("message", ctypes.c_char * BLG_ERROR_MESSAGE_CAP),
        ("json_pointer", ctypes.c_char * BLG_JSON_POINTER_CAP),
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
        # ADR-0007 tail fields (legacy struct_size accepted, defaults:
        # fast path on, single worker).
        ("mask_fast_path", ctypes.c_uint64),
        ("max_workers", ctypes.c_uint64),
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
        # Tail-grown: legacy descriptors omit these (struct_size check).
        ("flags", ctypes.c_uint32),
        ("reserved1", ctypes.c_uint32),
    ]


class ZgCompileRequest(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("kind", ctypes.c_uint32),
        ("profile", ctypes.c_char_p),
        ("data", ctypes.POINTER(ctypes.c_uint8)),
        ("data_len", ctypes.c_size_t),
        # Tail-grown (P5): external-$ref registry snapshot bytes; empty/NULL
        # means "no registry" (the pre-P5 behavior).
        ("registry_data", ctypes.POINTER(ctypes.c_uint8)),
        ("registry_data_len", ctypes.c_size_t),
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
        ("mem_used", ctypes.c_uint64 * BLG_STATS_CATEGORY_COUNT),
        ("mem_peak", ctypes.c_uint64 * BLG_STATS_CATEGORY_COUNT),
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
    if not hasattr(lib, "blg_abi_version"):
        raise ModuleNotFoundError(f"{path}: no blg_abi_version export")

    void_p = ctypes.c_void_p
    lib.blg_abi_version.argtypes = []
    lib.blg_abi_version.restype = ctypes.c_uint32

    lib.blg_context_create.argtypes = [
        ctypes.POINTER(ZgContextConfig), ctypes.POINTER(ZgTokenizerDesc),
        ctypes.POINTER(void_p), ctypes.POINTER(ZgError)]
    lib.blg_context_create.restype = ctypes.c_int

    lib.blg_context_destroy.argtypes = [void_p]
    lib.blg_context_destroy.restype = ctypes.c_int

    lib.blg_context_reset_cache.argtypes = [void_p]
    lib.blg_context_reset_cache.restype = ctypes.c_int

    lib.blg_compile.argtypes = [
        void_p, ctypes.POINTER(ZgCompileRequest),
        ctypes.POINTER(void_p), ctypes.POINTER(ZgError)]
    lib.blg_compile.restype = ctypes.c_int

    lib.blg_grammar_release.argtypes = [void_p]
    lib.blg_grammar_release.restype = None

    lib.blg_session_create.argtypes = [
        void_p, void_p, ctypes.POINTER(void_p), ctypes.POINTER(ZgError)]
    lib.blg_session_create.restype = ctypes.c_int

    lib.blg_session_destroy.argtypes = [void_p]
    lib.blg_session_destroy.restype = None

    lib.blg_fill_mask.argtypes = [
        void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_size_t,
        ctypes.POINTER(ZgError)]
    lib.blg_fill_mask.restype = ctypes.c_int

    lib.blg_fill_masks_batch.argtypes = [
        ctypes.POINTER(void_p), ctypes.POINTER(ctypes.POINTER(ctypes.c_uint32)),
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_int32), ctypes.c_size_t,
        ctypes.POINTER(ZgError)]
    lib.blg_fill_masks_batch.restype = ctypes.c_int

    lib.blg_accept_token.argtypes = [void_p, ctypes.c_uint32, ctypes.POINTER(ZgError)]
    lib.blg_accept_token.restype = ctypes.c_int

    lib.blg_can_end.argtypes = [void_p, ctypes.POINTER(ctypes.c_bool)]
    lib.blg_can_end.restype = ctypes.c_int

    lib.blg_finish.argtypes = [void_p, ctypes.POINTER(ZgError)]
    lib.blg_finish.restype = ctypes.c_int

    lib.blg_abort.argtypes = [void_p]
    lib.blg_abort.restype = ctypes.c_int

    lib.blg_get_stats.argtypes = [void_p, ctypes.POINTER(ZgStats)]
    lib.blg_get_stats.restype = ctypes.c_int

    lib.blg_get_stats_session.argtypes = [void_p, ctypes.POINTER(ZgStats)]
    lib.blg_get_stats_session.restype = ctypes.c_int

    lib.blg_cancel_flag_set.argtypes = [void_p, ctypes.POINTER(ctypes.c_uint8)]
    lib.blg_cancel_flag_set.restype = ctypes.c_int

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
    """Non-OK blg_status where the test expected BLG_OK."""

    def __init__(self, status: int, where: str, err: ZgError | None = None):
        self.status = status
        msg = STATUS_NAMES.get(status, f"status={status}")
        detail = f" ({err_text(err)})" if err is not None and err.code != 0 else ""
        pointer = ""
        if err is not None:
            p = err.json_pointer.split(b"\0", 1)[0].decode("utf-8", "replace")
            if p:
                pointer = f" at {p}"
        super().__init__(f"{where}: {msg}{detail}{pointer}")


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
    desc.flags = 0
    return desc, (entries, blob_arr, eos_arr, sp_arr)


class Context:
    """Live core context + buffer keepalive. destroy() -> blg_status."""

    def __init__(self, spec, mode=BLG_MODE_LAZY, *, memory_limit_bytes=0,
                 cache_limit_bytes=0, session_limit_bytes=0,
                 schema_limit_bytes=0, max_depth=0, max_threads_per_state=0,
                 work_limit_ops=0, adaptive_min_hits=0, adaptive_min_cost_ns=0,
                 precompute_max_states=0, mask_fast_path=0, max_workers=0):
        config = ZgContextConfig()
        config.struct_size = ctypes.sizeof(ZgContextConfig)
        config.version = BLG_ABI_VERSION
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
        config.mask_fast_path = mask_fast_path
        config.max_workers = max_workers
        self._config = config
        self._desc, self._keep = make_tokenizer_desc(spec)
        self.spec = spec
        out = ctypes.c_void_p()
        err = new_error()
        status = LIB.blg_context_create(
            ctypes.byref(config), ctypes.byref(self._desc),
            ctypes.byref(out), ctypes.byref(err))
        if status != BLG_OK:
            raise CoreFailure(status, "blg_context_create", err)
        self.handle = out
        self._destroyed = False

    def destroy(self) -> int:
        if self._destroyed:
            return BLG_OK
        status = LIB.blg_context_destroy(self.handle)
        if status == BLG_OK:
            self._destroyed = True
        return status

    def reset_cache(self) -> int:
        """Resets the artifact cache; live handles/sessions are preserved."""
        return LIB.blg_context_reset_cache(self.handle)

    def get_stats(self) -> ZgStats:
        stats = ZgStats()
        stats.struct_size = ctypes.sizeof(ZgStats)
        stats.version = BLG_ABI_VERSION
        status = LIB.blg_get_stats(self.handle, ctypes.byref(stats))
        if status != BLG_OK:
            raise CoreFailure(status, "blg_get_stats")
        return stats


def compile_grammar(ctx: Context, data: bytes, kind: int, profile: bytes = b"canonical-v1",
                    registry: bytes | None = None) -> ctypes.c_void_p:
    req = ZgCompileRequest()
    req.struct_size = ctypes.sizeof(ZgCompileRequest)
    req.kind = kind
    req.profile = profile
    buf = (ctypes.c_uint8 * max(len(data), 1)).from_buffer_copy(data or b"\0")
    req.data = buf
    req.data_len = len(data)
    rbuf = None
    if registry is not None:
        rbuf = (ctypes.c_uint8 * max(len(registry), 1)).from_buffer_copy(registry or b"\0")
        req.registry_data = rbuf
        req.registry_data_len = len(registry)
    out = ctypes.c_void_p()
    err = new_error()
    status = LIB.blg_compile(ctx.handle, ctypes.byref(req),
                            ctypes.byref(out), ctypes.byref(err))
    if status != BLG_OK:
        e = CoreFailure(status, "blg_compile", err)
        e.schema_offset = err.schema_offset
        raise e
    return out  # caller need not keep buf/rbuf: the core copies the data


def compile_schema(ctx: Context, schema, profile: bytes = b"canonical-v1",
                   registry=None) -> ctypes.c_void_p:
    """registry (P5, spec-v1): None, raw bytes, or a mapping uri -> schema
    (wrapped into {"documents": ...}; an optional "version" key in a dict
    argument is passed through)."""
    import json as _json
    if isinstance(schema, (bytes, bytearray)):
        data = bytes(schema)
    elif isinstance(schema, str):
        data = schema.encode("utf-8")
    else:
        data = _json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    reg_bytes = None
    if registry is not None:
        if isinstance(registry, (bytes, bytearray)):
            reg_bytes = bytes(registry)
        elif isinstance(registry, str):
            reg_bytes = registry.encode("utf-8")
        else:
            docs = registry.get("documents") if "documents" in registry else registry
            snap = {"documents": docs}
            if isinstance(registry, dict) and "version" in registry:
                snap["version"] = registry["version"]
            reg_bytes = _json.dumps(snap, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")
    return compile_grammar(ctx, data, BLG_CONSTRAINT_JSON_SCHEMA, profile,
                           registry=reg_bytes)


def compile_literals(ctx: Context, strings) -> ctypes.c_void_p:
    import json as _json
    data = _json.dumps(list(strings), ensure_ascii=False).encode("utf-8")
    return compile_grammar(ctx, data, BLG_CONSTRAINT_LITERAL_SET)


def grammar_release(grammar) -> None:
    LIB.blg_grammar_release(grammar)


class Session:
    def __init__(self, ctx: Context, grammar):
        self.ctx = ctx
        out = ctypes.c_void_p()
        err = new_error()
        status = LIB.blg_session_create(ctx.handle, grammar,
                                       ctypes.byref(out), ctypes.byref(err))
        if status != BLG_OK:
            raise CoreFailure(status, "blg_session_create", err)
        self.handle = out
        self._destroyed = False

    def fill_mask_words(self, words: int | None = None):
        """-> (status, list[int] words). Too few words -> BUFFER_TOO_SMALL."""
        if words is None:
            words = self.ctx.spec.mask_words
        buf = (ctypes.c_uint32 * words)()
        err = new_error()
        status = LIB.blg_fill_mask(self.handle, buf, words, ctypes.byref(err))
        return status, list(buf)

    def fill_mask_ids(self) -> set[int]:
        status, words = self.fill_mask_words()
        if status != BLG_OK:
            raise CoreFailure(status, "blg_fill_mask")
        ids = set()
        for i in range(self.ctx.spec.vocab_size):
            if words[i // 32] >> (i % 32) & 1:
                ids.add(i)
        return ids

    def accept(self, token_id: int) -> int:
        err = new_error()
        return LIB.blg_accept_token(self.handle, token_id, ctypes.byref(err))

    def can_end(self) -> bool:
        out = ctypes.c_bool()
        status = LIB.blg_can_end(self.handle, ctypes.byref(out))
        if status != BLG_OK:
            raise CoreFailure(status, "blg_can_end")
        return bool(out.value)

    def finish(self) -> int:
        err = new_error()
        return LIB.blg_finish(self.handle, ctypes.byref(err))

    def abort(self) -> int:
        return LIB.blg_abort(self.handle)

    def get_stats(self) -> ZgStats:
        stats = ZgStats()
        stats.struct_size = ctypes.sizeof(ZgStats)
        stats.version = BLG_ABI_VERSION
        status = LIB.blg_get_stats_session(self.handle, ctypes.byref(stats))
        if status != BLG_OK:
            raise CoreFailure(status, "blg_get_stats_session")
        return stats

    def destroy(self):
        if not self._destroyed:
            LIB.blg_session_destroy(self.handle)
            self._destroyed = True


def fill_masks_batch(sessions: list[Session], words_each: int):
    """-> (overall_status, statuses list). Empty batch ([]) -> (BLG_OK, [])."""
    n = len(sessions)
    if n == 0:
        err = new_error()
        status = LIB.blg_fill_masks_batch(None, None, 0, None, 0, ctypes.byref(err))
        return status, []
    handles = (ctypes.c_void_p * n)(*(s.handle.value for s in sessions))
    mask_bufs = [(ctypes.c_uint32 * words_each)() for _ in sessions]
    mask_ptrs = (ctypes.POINTER(ctypes.c_uint32) * n)(*mask_bufs)
    statuses = (ctypes.c_int32 * n)()
    err = new_error()
    status = LIB.blg_fill_masks_batch(handles, mask_ptrs, words_each,
                                     statuses, n, ctypes.byref(err))
    return status, list(statuses)
