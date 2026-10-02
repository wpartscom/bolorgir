/*
 * bolorgir._core - thin CPython wrapper over the libbolorgir C ABI.
 *
 * Limited API (Py_LIMITED_API 0x030A0000) -> abi3 wheel for Python 3.10+.
 * No mask-building algorithms here: only Python objects, buffer ownership,
 * the GIL, and blg_status -> typed exception mapping.
 *
 * Ownership:
 *  - Context owns blg_context; Grammar/Session hold refs to Context (and
 *    Session to Grammar), so the native context always outlives its children
 *    and blg_context_destroy in tp_dealloc cannot return BUSY.
 *  - Session serializes ALL operations through a per-object
 *    PyThread_type_lock: the GIL is released (PyEval_SaveThread), the lock is
 *    taken blocking, the native call runs, the lock is released, the GIL is
 *    reacquired.
 *  - Buffers (token blob/index, mask) live on the Python side until the
 *    native call returns; the core copies data into context-owned memory.
 */

#define Py_LIMITED_API 0x030A0000
#include <Python.h>

#include <string.h>
#include "bolorgir.h"

_Static_assert(sizeof(blg_token_entry) == 24, "blg_token_entry layout");
_Static_assert(BLG_ERROR_MESSAGE_CAP == 256, "error cap");

/* ------------------------------------------------------------------ */
/* Exceptions                                                         */
/* ------------------------------------------------------------------ */

static PyObject *ExcBase; /* ZigConstraintsError */
static PyObject *ExcTable[14]; /* index = blg_status; [0] unused */

static const char *default_msg(blg_status st) {
    switch (st) {
    case BLG_ERR_INVALID_ARGUMENT: return "invalid argument";
    case BLG_ERR_INVALID_SCHEMA: return "invalid schema";
    case BLG_ERR_UNSUPPORTED_FEATURE: return "unsupported feature";
    case BLG_ERR_UNSATISFIABLE_CONSTRAINT: return "unsatisfiable constraint";
    case BLG_ERR_UNSUPPORTED_TOKENIZER: return "unsupported tokenizer";
    case BLG_ERR_INVALID_TOKEN: return "invalid token";
    case BLG_ERR_DEAD_END: return "dead end";
    case BLG_ERR_RESOURCE_LIMIT: return "resource limit";
    case BLG_ERR_CANCELLED: return "cancelled";
    case BLG_ERR_BUSY: return "busy";
    case BLG_ERR_WRONG_STATE: return "wrong state";
    case BLG_ERR_BUFFER_TOO_SMALL: return "buffer too small";
    default: return "internal error";
    }
}

static int raise_zg(blg_status st, const blg_error *err) {
    PyObject *exc;
    const char *msg;
    if (st == BLG_OK) return 0;
    exc = (st >= 1 && st <= 13) ? ExcTable[st] : ExcTable[BLG_ERR_INTERNAL];
    msg = (err && err->message[0]) ? err->message : default_msg(st);
    if (err && err->schema_offset != UINT32_MAX)
        PyErr_Format(exc, "%s (schema_offset=%u)", msg,
                     (unsigned)err->schema_offset);
    else
        PyErr_SetString(exc, msg);
    return -1;
}

static void init_zg_error(blg_error *err) {
    memset(err, 0, sizeof(*err));
    err->struct_size = (uint32_t)sizeof(blg_error);
    err->schema_offset = UINT32_MAX;
}

/* ------------------------------------------------------------------ */
/* Helper: uint32 list from a Python sequence                          */
/* ------------------------------------------------------------------ */

static PyObject *fast_item(PyObject *fast, Py_ssize_t i) {
    /* borrowed ref; PySequence_Fast returned a list or tuple */
    if (PyList_Check(fast)) return PyList_GetItem(fast, i);
    return PyTuple_GetItem(fast, i);
}

static uint32_t *seq_to_u32(PyObject *obj, size_t *out_n) {
    PyObject *fast = PySequence_Fast(obj, "expected a sequence of ints");
    Py_ssize_t n, i;
    uint32_t *buf;
    if (!fast) return NULL;
    n = PySequence_Size(fast);
    buf = PyMem_Malloc((size_t)(n ? n : 1) * sizeof(uint32_t));
    if (!buf) { Py_DECREF(fast); PyErr_NoMemory(); return NULL; }
    for (i = 0; i < n; i++) {
        unsigned long v = PyLong_AsUnsignedLong(fast_item(fast, i));
        if (v == (unsigned long)-1 && PyErr_Occurred()) {
            PyMem_Free(buf); Py_DECREF(fast); return NULL;
        }
        if (v > UINT32_MAX) {
            PyMem_Free(buf); Py_DECREF(fast);
            PyErr_SetString(PyExc_OverflowError, "id does not fit uint32");
            return NULL;
        }
        buf[i] = (uint32_t)v;
    }
    Py_DECREF(fast);
    *out_n = (size_t)n;
    return buf;
}

/* ------------------------------------------------------------------ */
/* Context                                                             */
/* ------------------------------------------------------------------ */

typedef struct {
    PyObject_HEAD
    blg_context *ctx;
    uint32_t vocab_size;
    uint32_t mask_words;
} ContextObject;

typedef struct {
    PyObject_HEAD
    blg_grammar *grammar;
    ContextObject *context; /* strong ref */
} GrammarObject;

typedef struct {
    PyObject_HEAD
    blg_session *session;
    ContextObject *context; /* strong ref */
    GrammarObject *grammar; /* strong ref */
    PyThread_type_lock lock;
} SessionObject;

static PyObject *ContextType;
static PyObject *GrammarType;
static PyObject *SessionType;

static int Context_init(ContextObject *self, PyObject *args, PyObject *kwds) {
    static char *kwlist[] = {
        "mode", "max_depth", "max_threads_per_state",
        "memory_limit_bytes", "cache_limit_bytes", "session_limit_bytes",
        "schema_limit_bytes", "vocab_size", "token_blob", "token_index",
        "eos_ids", "special_ids", "flags",
        "work_limit_ops", "adaptive_min_hits", "adaptive_min_cost_ns",
        "precompute_max_states", NULL
    };
    unsigned int mode = 0, max_depth = 0, max_threads = 0, vocab_size = 0;
    unsigned int flags = 0;
    unsigned long long work_limit_ops = 0, adaptive_min_hits = 0;
    unsigned long long adaptive_min_cost_ns = 0, precompute_max_states = 0;
    unsigned long long mem_limit = 0, cache_limit = 0, sess_limit = 0, schema_limit = 0;
    PyObject *blob_obj, *index_obj, *eos_obj, *special_obj;
    uint32_t *eos = NULL, *special = NULL, *entries = NULL;
    size_t eos_n = 0, special_n = 0, entry_count;
    blg_context_config cfg;
    blg_tokenizer_desc desc;
    blg_error err;
    blg_status st;
    PyThreadState *save;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "IIIKKKKISSOOIKKKK:Context", kwlist,
                                     &mode, &max_depth, &max_threads,
                                     &mem_limit, &cache_limit, &sess_limit,
                                     &schema_limit, &vocab_size,
                                     &blob_obj, &index_obj,
                                     &eos_obj, &special_obj, &flags,
                                     &work_limit_ops, &adaptive_min_hits,
                                     &adaptive_min_cost_ns, &precompute_max_states))
        return -1;
    if (vocab_size == 0) {
        PyErr_SetString(ExcTable[BLG_ERR_INVALID_ARGUMENT], "vocab_size must be > 0");
        return -1;
    }
    if (PyBytes_Size(index_obj) != (Py_ssize_t)vocab_size * 24) {
        PyErr_Format(ExcTable[BLG_ERR_INVALID_ARGUMENT],
                     "token_index must be vocab_size*24 bytes, got %zd",
                     PyBytes_Size(index_obj));
        return -1;
    }

    eos = seq_to_u32(eos_obj, &eos_n);
    if (!eos && PyErr_Occurred()) goto fail;
    special = seq_to_u32(special_obj, &special_n);
    if (!special && PyErr_Occurred()) goto fail;

    entry_count = (size_t)vocab_size;
    entries = PyMem_Malloc(entry_count * sizeof(blg_token_entry));
    if (!entries) { PyErr_NoMemory(); goto fail; }
    memcpy(entries, PyBytes_AsString(index_obj),
           entry_count * sizeof(blg_token_entry));

    memset(&cfg, 0, sizeof(cfg));
    cfg.struct_size = (uint32_t)sizeof(cfg);
    cfg.version = BLG_ABI_VERSION;
    cfg.mode = mode;
    cfg.max_depth = max_depth;
    cfg.max_threads_per_state = max_threads;
    cfg.memory_limit_bytes = (uint64_t)mem_limit;
    cfg.cache_limit_bytes = (uint64_t)cache_limit;
    cfg.session_limit_bytes = (uint64_t)sess_limit;
    cfg.schema_limit_bytes = (uint64_t)schema_limit;
    cfg.work_limit_ops = (uint64_t)work_limit_ops;
    cfg.adaptive_min_hits = (uint64_t)adaptive_min_hits;
    cfg.adaptive_min_cost_ns = (uint64_t)adaptive_min_cost_ns;
    cfg.precompute_max_states = (uint64_t)precompute_max_states;

    memset(&desc, 0, sizeof(desc));
    desc.struct_size = (uint32_t)sizeof(desc);
    desc.vocab_size = vocab_size;
    desc.entries = (const blg_token_entry *)entries;
    desc.entry_count = entry_count;
    desc.blob = (const uint8_t *)PyBytes_AsString(blob_obj);
    desc.blob_len = (size_t)PyBytes_Size(blob_obj);
    desc.eos_ids = eos;
    desc.eos_count = eos_n;
    desc.special_ids = special;
    desc.special_count = special_n;
    desc.flags = flags;

    init_zg_error(&err);
    save = PyEval_SaveThread();
    st = blg_context_create(&cfg, &desc, &self->ctx, &err);
    PyEval_RestoreThread(save);

    PyMem_Free(entries);
    PyMem_Free(eos);
    PyMem_Free(special);

    if (st != BLG_OK) { raise_zg(st, &err); return -1; }
    self->vocab_size = vocab_size;
    self->mask_words = (vocab_size + 31u) / 32u;
    return 0;

fail:
    PyMem_Free(eos);
    PyMem_Free(special);
    return -1;
}

static void Context_dealloc(ContextObject *self) {
    if (self->ctx) {
        PyThreadState *save = PyEval_SaveThread();
        /* Grammar/Session hold refs to the context, so it is not busy. */
        (void)blg_context_destroy(self->ctx);
        PyEval_RestoreThread(save);
        self->ctx = NULL;
    }
    PyTypeObject *tp = Py_TYPE(self);
    freefunc tp_free = (freefunc)PyType_GetSlot(tp, Py_tp_free);
    tp_free(self);
    Py_DECREF(tp);
}

static PyObject *Context_close(ContextObject *self, PyObject *Py_UNUSED(ign)) {
    if (self->ctx) {
        blg_error err;
        blg_status st;
        PyThreadState *save;
        init_zg_error(&err);
        save = PyEval_SaveThread();
        st = blg_context_destroy(self->ctx);
        PyEval_RestoreThread(save);
        if (st != BLG_OK) return raise_zg(st, &err), NULL;
        self->ctx = NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Context_compile(ContextObject *self, PyObject *args, PyObject *kwds) {
    static char *kwlist[] = {"kind", "data", "profile", NULL};
    unsigned int kind;
    PyObject *data_obj;
    const char *profile = NULL;
    blg_compile_request req;
    blg_error err;
    blg_grammar *g = NULL;
    blg_status st;
    GrammarObject *obj;
    PyThreadState *save;

    if (!self->ctx) {
        PyErr_SetString(ExcTable[BLG_ERR_WRONG_STATE], "context is closed");
        return NULL;
    }
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "IS|z:compile", kwlist,
                                     &kind, &data_obj, &profile))
        return NULL;

    memset(&req, 0, sizeof(req));
    req.struct_size = (uint32_t)sizeof(req);
    req.kind = kind;
    req.profile = profile;
    req.data = (const uint8_t *)PyBytes_AsString(data_obj);
    req.data_len = (size_t)PyBytes_Size(data_obj);

    init_zg_error(&err);
    save = PyEval_SaveThread();
    st = blg_compile(self->ctx, &req, &g, &err);
    PyEval_RestoreThread(save);
    if (st != BLG_OK) { raise_zg(st, &err); return NULL; }

    obj = (GrammarObject *)PyType_GenericAlloc((PyTypeObject *)GrammarType, 0);
    if (!obj) { blg_grammar_release(g); return NULL; }
    obj->grammar = g;
    Py_INCREF(self);
    obj->context = self;
    return (PyObject *)obj;
}

static PyObject *Context_create_session(ContextObject *self, PyObject *arg) {
    GrammarObject *g;
    blg_error err;
    blg_session *s = NULL;
    blg_status st;
    SessionObject *obj;
    PyThreadState *save;

    if (!self->ctx) {
        PyErr_SetString(ExcTable[BLG_ERR_WRONG_STATE], "context is closed");
        return NULL;
    }
    if (!PyObject_TypeCheck(arg, (PyTypeObject *)GrammarType)) {
        PyErr_SetString(PyExc_TypeError, "expected a Grammar");
        return NULL;
    }
    g = (GrammarObject *)arg;
    if (!g->grammar) {
        PyErr_SetString(ExcTable[BLG_ERR_WRONG_STATE], "grammar is closed");
        return NULL;
    }

    init_zg_error(&err);
    save = PyEval_SaveThread();
    st = blg_session_create(self->ctx, g->grammar, &s, &err);
    PyEval_RestoreThread(save);
    if (st != BLG_OK) { raise_zg(st, &err); return NULL; }

    obj = (SessionObject *)PyType_GenericAlloc((PyTypeObject *)SessionType, 0);
    if (!obj) { blg_session_destroy(s); return NULL; }
    obj->session = s;
    obj->lock = PyThread_allocate_lock();
    if (!obj->lock) {
        obj->session = NULL;
        blg_session_destroy(s);
        Py_DECREF(obj);
        return PyErr_NoMemory();
    }
    Py_INCREF(self);
    obj->context = self;
    Py_INCREF(g);
    obj->grammar = g;
    return (PyObject *)obj;
}

static PyObject *stats_to_dict(const blg_stats *st) {
    static const char *cats[8] = {"tokenizer", "grammar", "session", "cache",
                                  "temp", "total", "reserved6", "reserved7"};
    PyObject *d, *used, *peak;
    int i;
    d = PyDict_New();
    if (!d) return NULL;
#define SETU64(name, val) \
    do { \
        PyObject *v = PyLong_FromUnsignedLongLong((unsigned long long)(val)); \
        if (!v || PyDict_SetItemString(d, name, v) < 0) { Py_XDECREF(v); Py_DECREF(d); return NULL; } \
        Py_DECREF(v); \
    } while (0)
    SETU64("compile_ns", st->compile_ns);
    SETU64("tokenizer_prepare_ns", st->tokenizer_prepare_ns);
    SETU64("accept_ns_total", st->accept_ns_total);
    SETU64("mask_ns_total", st->mask_ns_total);
    SETU64("mask_calls", st->mask_calls);
    SETU64("tokens_accepted", st->tokens_accepted);
    SETU64("cache_hits", st->cache_hits);
    SETU64("cache_misses", st->cache_misses);
    SETU64("cache_evictions", st->cache_evictions);
#undef SETU64
    used = PyDict_New();
    peak = PyDict_New();
    if (!used || !peak) { Py_XDECREF(used); Py_XDECREF(peak); Py_DECREF(d); return NULL; }
    for (i = 0; i < 8; i++) {
        PyObject *u = PyLong_FromUnsignedLongLong(st->mem_used[i]);
        PyObject *p = PyLong_FromUnsignedLongLong(st->mem_peak[i]);
        if (!u || !p || PyDict_SetItemString(used, cats[i], u) < 0 ||
            PyDict_SetItemString(peak, cats[i], p) < 0) {
            Py_XDECREF(u); Py_XDECREF(p);
            Py_DECREF(used); Py_DECREF(peak); Py_DECREF(d);
            return NULL;
        }
        Py_DECREF(u); Py_DECREF(p);
    }
    if (PyDict_SetItemString(d, "mem_used", used) < 0 ||
        PyDict_SetItemString(d, "mem_peak", peak) < 0) {
        Py_DECREF(used); Py_DECREF(peak); Py_DECREF(d);
        return NULL;
    }
    Py_DECREF(used); Py_DECREF(peak);
    return d;
}

static PyObject *Context_stats(ContextObject *self, PyObject *Py_UNUSED(ign)) {
    blg_stats st;
    blg_status rc;
    if (!self->ctx) {
        PyErr_SetString(ExcTable[BLG_ERR_WRONG_STATE], "context is closed");
        return NULL;
    }
    memset(&st, 0, sizeof(st));
    st.struct_size = (uint32_t)sizeof(st);
    rc = blg_get_stats(self->ctx, &st);
    if (rc != BLG_OK) { raise_zg(rc, NULL); return NULL; }
    return stats_to_dict(&st);
}

static PyObject *Context_enter(ContextObject *self, PyObject *Py_UNUSED(ign)) {
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *Context_exit(ContextObject *self, PyObject *args) {
    PyObject *rc = Context_close(self, NULL);
    if (!rc) return NULL;
    Py_DECREF(rc);
    Py_RETURN_FALSE;
}

static PyObject *Context_set_cancel_flag(ContextObject *self, PyObject *arg) {
    unsigned long long addr = PyLong_AsUnsignedLongLong(arg);
    if (addr == (unsigned long long)-1 && PyErr_Occurred()) return NULL;
    blg_status rc = blg_cancel_flag_set(
        self->ctx, addr ? (const uint8_t *)(uintptr_t)addr : NULL);
    if (rc != BLG_OK) { raise_zg(rc, NULL); return NULL; }
    Py_RETURN_NONE;
}

static PyMethodDef Context_methods[] = {
    {"compile", (PyCFunction)(void *)Context_compile, METH_VARARGS | METH_KEYWORDS,
     "compile(kind, data, profile=None) -> Grammar"},
    {"create_session", (PyCFunction)Context_create_session, METH_O,
     "create_session(grammar) -> Session"},
    {"stats", (PyCFunction)Context_stats, METH_NOARGS, "stats() -> dict"},
    {"set_cancel_flag", (PyCFunction)Context_set_cancel_flag, METH_O,
     "set_cancel_flag(address) -> None; address from ctypes.addressof or 0"},
    {"close", (PyCFunction)Context_close, METH_NOARGS, "close()"},
    {"__enter__", (PyCFunction)Context_enter, METH_NOARGS, NULL},
    {"__exit__", (PyCFunction)Context_exit, METH_VARARGS, NULL},
    {NULL}
};

static PyObject *Context_get_vocab_size(ContextObject *self, void *Py_UNUSED(c)) {
    return PyLong_FromUnsignedLong(self->vocab_size);
}

static PyObject *Context_get_mask_words(ContextObject *self, void *Py_UNUSED(c)) {
    return PyLong_FromUnsignedLong(self->mask_words);
}

static PyGetSetDef Context_getset[] = {
    {"vocab_size", (getter)Context_get_vocab_size, NULL, NULL, NULL},
    {"mask_words", (getter)Context_get_mask_words, NULL, NULL, NULL},
    {NULL}
};

static PyType_Slot Context_slots[] = {
    {Py_tp_dealloc, (void *)Context_dealloc},
    {Py_tp_methods, (void *)Context_methods},
    {Py_tp_getset, (void *)Context_getset},
    {Py_tp_init, (void *)Context_init},
    {Py_tp_new, (void *)PyType_GenericNew},
    {0, NULL}
};

static PyType_Spec Context_spec = {
    "bolorgir._core.Context",
    sizeof(ContextObject),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    Context_slots
};

/* ------------------------------------------------------------------ */
/* Grammar                                                             */
/* ------------------------------------------------------------------ */

static void Grammar_dealloc(GrammarObject *self) {
    if (self->grammar) {
        PyThreadState *save = PyEval_SaveThread();
        blg_grammar_release(self->grammar);
        PyEval_RestoreThread(save);
        self->grammar = NULL;
    }
    Py_CLEAR(self->context);
    PyTypeObject *tp = Py_TYPE(self);
    freefunc tp_free = (freefunc)PyType_GetSlot(tp, Py_tp_free);
    tp_free(self);
    Py_DECREF(tp);
}

static PyObject *Grammar_close(GrammarObject *self, PyObject *Py_UNUSED(ign)) {
    if (self->grammar) {
        PyThreadState *save = PyEval_SaveThread();
        blg_grammar_release(self->grammar);
        PyEval_RestoreThread(save);
        self->grammar = NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Grammar_create_session(GrammarObject *self, PyObject *Py_UNUSED(ign)) {
    if (!self->context) {
        PyErr_SetString(ExcTable[BLG_ERR_WRONG_STATE], "grammar has no context");
        return NULL;
    }
    return Context_create_session(self->context, (PyObject *)self);
}

static PyObject *Grammar_enter(GrammarObject *self, PyObject *Py_UNUSED(ign)) {
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *Grammar_exit(GrammarObject *self, PyObject *args) {
    PyObject *rc = Grammar_close(self, NULL);
    if (!rc) return NULL;
    Py_DECREF(rc);
    Py_RETURN_FALSE;
}

static PyMethodDef Grammar_methods[] = {
    {"create_session", (PyCFunction)Grammar_create_session, METH_NOARGS,
     "create_session() -> Session"},
    {"close", (PyCFunction)Grammar_close, METH_NOARGS, "close()"},
    {"__enter__", (PyCFunction)Grammar_enter, METH_NOARGS, NULL},
    {"__exit__", (PyCFunction)Grammar_exit, METH_VARARGS, NULL},
    {NULL}
};

static PyType_Slot Grammar_slots[] = {
    {Py_tp_dealloc, (void *)Grammar_dealloc},
    {Py_tp_methods, (void *)Grammar_methods},
    {0, NULL}
};

static PyType_Spec Grammar_spec = {
    "bolorgir._core.Grammar",
    sizeof(GrammarObject),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    Grammar_slots
};

/* ------------------------------------------------------------------ */
/* Session                                                             */
/* ------------------------------------------------------------------ */

/* Serialization macro: the GIL is released BEFORE the blocking lock
 * acquire so it is not held while waiting on another thread. The session
 * pointer is re-checked under the lock (close may have won the race). */
#define SESSION_BEGIN(sobj) \
    blg_session *_sess = (sobj)->session; \
    PyThreadState *_save; \
    if (!_sess) { \
        PyErr_SetString(ExcTable[BLG_ERR_WRONG_STATE], "session is closed"); \
        return NULL; \
    } \
    _save = PyEval_SaveThread(); \
    PyThread_acquire_lock((sobj)->lock, 1); \
    _sess = (sobj)->session; \
    if (!_sess) { \
        PyThread_release_lock((sobj)->lock); \
        PyEval_RestoreThread(_save); \
        PyErr_SetString(ExcTable[BLG_ERR_WRONG_STATE], "session is closed"); \
        return NULL; \
    }

#define SESSION_END(sobj) \
    PyThread_release_lock((sobj)->lock); \
    PyEval_RestoreThread(_save);

static void Session_dealloc(SessionObject *self) {
    if (self->session) {
        PyThreadState *save = PyEval_SaveThread();
        blg_session_destroy(self->session);
        PyEval_RestoreThread(save);
        self->session = NULL;
    }
    if (self->lock) {
        PyThread_free_lock(self->lock);
        self->lock = NULL;
    }
    Py_CLEAR(self->grammar);
    Py_CLEAR(self->context);
    PyTypeObject *tp = Py_TYPE(self);
    freefunc tp_free = (freefunc)PyType_GetSlot(tp, Py_tp_free);
    tp_free(self);
    Py_DECREF(tp);
}

static PyObject *Session_fill_mask(SessionObject *self, PyObject *Py_UNUSED(ign)) {
    PyObject *out;
    blg_error err;
    blg_status st;
    size_t nbytes = (size_t)self->context->mask_words * 4;

    if (!self->session) {
        PyErr_SetString(ExcTable[BLG_ERR_WRONG_STATE], "session is closed");
        return NULL;
    }
    out = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)nbytes);
    if (!out) return NULL;
    SESSION_BEGIN(self);
    init_zg_error(&err);
    st = blg_fill_mask(_sess, (uint32_t *)PyBytes_AsString(out),
                      self->context->mask_words, &err);
    SESSION_END(self);
    if (st != BLG_OK) { Py_DECREF(out); raise_zg(st, &err); return NULL; }
    return out;
}

static PyObject *Session_accept_token(SessionObject *self, PyObject *arg) {
    unsigned long tok;
    blg_error err;
    blg_status st;

    tok = PyLong_AsUnsignedLong(arg);
    if (tok == (unsigned long)-1 && PyErr_Occurred()) return NULL;
    if (tok > UINT32_MAX) {
        PyErr_SetString(PyExc_OverflowError, "token_id does not fit uint32");
        return NULL;
    }
    SESSION_BEGIN(self);
    init_zg_error(&err);
    st = blg_accept_token(_sess, (uint32_t)tok, &err);
    SESSION_END(self);
    if (st != BLG_OK) { raise_zg(st, &err); return NULL; }
    Py_RETURN_NONE;
}

static PyObject *Session_can_end(SessionObject *self, PyObject *Py_UNUSED(ign)) {
    blg_status st;
    bool can = false;

    SESSION_BEGIN(self);
    st = blg_can_end(_sess, &can);
    SESSION_END(self);
    if (st != BLG_OK) { raise_zg(st, NULL); return NULL; }
    if (can) Py_RETURN_TRUE;
    Py_RETURN_FALSE;
}

static PyObject *Session_finish(SessionObject *self, PyObject *Py_UNUSED(ign)) {
    blg_error err;
    blg_status st;

    SESSION_BEGIN(self);
    init_zg_error(&err);
    st = blg_finish(_sess, &err);
    SESSION_END(self);
    if (st != BLG_OK) { raise_zg(st, &err); return NULL; }
    Py_RETURN_NONE;
}

static PyObject *Session_abort(SessionObject *self, PyObject *Py_UNUSED(ign)) {
    blg_status st;

    SESSION_BEGIN(self);
    st = blg_abort(_sess);
    SESSION_END(self);
    if (st != BLG_OK) { raise_zg(st, NULL); return NULL; }
    Py_RETURN_NONE;
}

static PyObject *Session_stats(SessionObject *self, PyObject *Py_UNUSED(ign)) {
    blg_stats st;
    blg_status rc;

    SESSION_BEGIN(self);
    memset(&st, 0, sizeof(st));
    st.struct_size = (uint32_t)sizeof(st);
    rc = blg_get_stats_session(_sess, &st);
    SESSION_END(self);
    if (rc != BLG_OK) { raise_zg(rc, NULL); return NULL; }
    return stats_to_dict(&st);
}

static PyObject *Session_close(SessionObject *self, PyObject *Py_UNUSED(ign)) {
    PyThreadState *save = PyEval_SaveThread();
    PyThread_acquire_lock(self->lock, 1);
    if (self->session) {
        blg_session_destroy(self->session);
        self->session = NULL;
    }
    PyThread_release_lock(self->lock);
    PyEval_RestoreThread(save);
    Py_RETURN_NONE;
}

static PyObject *Session_enter(SessionObject *self, PyObject *Py_UNUSED(ign)) {
    if (!self->session) {
        PyErr_SetString(ExcTable[BLG_ERR_WRONG_STATE], "session is closed");
        return NULL;
    }
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *Session_exit(SessionObject *self, PyObject *args) {
    PyObject *rc = Session_close(self, NULL);
    if (!rc) return NULL;
    Py_DECREF(rc);
    Py_RETURN_FALSE;
}

static PyMethodDef Session_methods[] = {
    {"fill_mask", (PyCFunction)Session_fill_mask, METH_NOARGS,
     "fill_mask() -> bytes (mask_words uint32 words, little-endian; bit 1 = allowed)"},
    {"accept_token", (PyCFunction)Session_accept_token, METH_O,
     "accept_token(token_id)"},
    {"can_end", (PyCFunction)Session_can_end, METH_NOARGS, "can_end() -> bool"},
    {"finish", (PyCFunction)Session_finish, METH_NOARGS, "finish()"},
    {"abort", (PyCFunction)Session_abort, METH_NOARGS, "abort()"},
    {"stats", (PyCFunction)Session_stats, METH_NOARGS, "stats() -> dict"},
    {"close", (PyCFunction)Session_close, METH_NOARGS, "close()"},
    {"__enter__", (PyCFunction)Session_enter, METH_NOARGS, NULL},
    {"__exit__", (PyCFunction)Session_exit, METH_VARARGS, NULL},
    {NULL}
};

static PyObject *Session_get_mask_words(SessionObject *self, void *Py_UNUSED(c)) {
    return PyLong_FromUnsignedLong(self->context->mask_words);
}

static PyObject *Session_get_vocab_size(SessionObject *self, void *Py_UNUSED(c)) {
    return PyLong_FromUnsignedLong(self->context->vocab_size);
}

static PyGetSetDef Session_getset[] = {
    {"mask_words", (getter)Session_get_mask_words, NULL, NULL, NULL},
    {"vocab_size", (getter)Session_get_vocab_size, NULL, NULL, NULL},
    {NULL}
};

static PyType_Slot Session_slots[] = {
    {Py_tp_dealloc, (void *)Session_dealloc},
    {Py_tp_methods, (void *)Session_methods},
    {Py_tp_getset, (void *)Session_getset},
    {0, NULL}
};

static PyType_Spec Session_spec = {
    "bolorgir._core.Session",
    sizeof(SessionObject),
    0,
    Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE,
    Session_slots
};

/* ------------------------------------------------------------------ */
/* Module functions                                                    */
/* ------------------------------------------------------------------ */

static PyObject *mod_abi_version(PyObject *Py_UNUSED(m), PyObject *Py_UNUSED(ign)) {
    return PyLong_FromUnsignedLong(blg_abi_version());
}

static PyObject *mod_mask_words(PyObject *Py_UNUSED(m), PyObject *arg) {
    unsigned long vocab = PyLong_AsUnsignedLong(arg);
    if (vocab == (unsigned long)-1 && PyErr_Occurred()) return NULL;
    if (vocab > UINT32_MAX) {
        PyErr_SetString(PyExc_OverflowError, "vocab_size does not fit uint32");
        return NULL;
    }
    return PyLong_FromUnsignedLong(((uint32_t)vocab + 31u) / 32u);
}

static void free_batch(PyObject **bufs, SessionObject **robjs, blg_session **sess,
                       uint32_t **masks, int32_t *statuses, Py_ssize_t n) {
    Py_ssize_t i;
    if (bufs) {
        for (i = 0; i < n; i++) Py_XDECREF(bufs[i]);
        PyMem_Free(bufs);
    }
    if (robjs) {
        for (i = 0; i < n; i++) Py_XDECREF(robjs[i]);
        PyMem_Free(robjs);
    }
    PyMem_Free(sess);
    PyMem_Free(masks);
    PyMem_Free(statuses);
}

/*
 * fill_masks_batch(sessions) -> list[(status_code:int, mask:bytes|None)]
 * Rows are independent; a row's status does not abort the others.
 *
 * Serialization (FR-11/13): per-session locks are taken without the GIL in
 * one global order - sessions are deduplicated by object identity and
 * sorted by native session pointer. This rules out lock-order inversion
 * between overlapping batches and a double acquire of a non-recursive lock
 * on duplicate entries. After all locks are held, every native session
 * pointer is re-validated: a session closed between the GIL-held argument
 * check and the lock acquire raises WrongState instead of dereferencing a
 * destroyed handle.
 */
static PyObject *mod_fill_masks_batch(PyObject *Py_UNUSED(m), PyObject *arg) {
    PyObject *fast, *result = NULL;
    Py_ssize_t n, i, j, nlocks = 0;
    PyObject **bufs = NULL;
    SessionObject **robjs = NULL; /* owned refs, one per row */
    SessionObject **locks = NULL; /* unique sessions, sorted by native ptr */
    blg_session **sess = NULL;
    uint32_t **masks = NULL;
    int32_t *statuses = NULL;
    size_t mask_words = 0;
    int closed = 0;
    blg_error err;
    PyThreadState *save;

    fast = PySequence_Fast(arg, "expected a sequence of Session");
    if (!fast) return NULL;
    n = PySequence_Size(fast);
    if (n == 0) {
        Py_DECREF(fast);
        return PyList_New(0);
    }
    bufs = PyMem_Calloc((size_t)n, sizeof(PyObject *));
    robjs = PyMem_Calloc((size_t)n, sizeof(SessionObject *));
    sess = PyMem_Malloc((size_t)n * sizeof(blg_session *));
    masks = PyMem_Malloc((size_t)n * sizeof(uint32_t *));
    statuses = PyMem_Malloc((size_t)n * sizeof(int32_t));
    if (!bufs || !robjs || !sess || !masks || !statuses) {
        PyErr_NoMemory();
        goto fail;
    }
    for (i = 0; i < n; i++) {
        PyObject *o = fast_item(fast, i);
        SessionObject *s;
        size_t mw;
        if (!PyObject_TypeCheck(o, (PyTypeObject *)SessionType)) {
            PyErr_SetString(PyExc_TypeError, "expected a sequence of Session");
            goto fail;
        }
        s = (SessionObject *)o;
        if (!s->session) {
            PyErr_SetString(ExcTable[BLG_ERR_WRONG_STATE], "session is closed");
            goto fail;
        }
        mw = s->context->mask_words;
        if (mask_words == 0) mask_words = mw;
        else if (mw != mask_words) {
            PyErr_SetString(ExcTable[BLG_ERR_INVALID_ARGUMENT],
                            "sessions have different mask_words");
            goto fail;
        }
        bufs[i] = PyBytes_FromStringAndSize(NULL, (Py_ssize_t)(mw * 4));
        if (!bufs[i]) goto fail;
        /* Strong ref: `fast` is not touched after the GIL is released. */
        Py_INCREF(s);
        robjs[i] = s;
        sess[i] = s->session;
        masks[i] = (uint32_t *)PyBytes_AsString(bufs[i]);
    }

    locks = PyMem_Malloc((size_t)n * sizeof(SessionObject *));
    if (!locks) { PyErr_NoMemory(); goto fail; }
    for (i = 0; i < n; i++) {
        for (j = 0; j < nlocks; j++)
            if (locks[j] == robjs[i]) break;
        if (j == nlocks) locks[nlocks++] = robjs[i];
    }
    /* Insertion sort by native session pointer (unique per live session). */
    for (i = 1; i < nlocks; i++) {
        SessionObject *s = locks[i];
        for (j = i - 1;
             j >= 0 && (uintptr_t)locks[j]->session > (uintptr_t)s->session;
             j--)
            locks[j + 1] = locks[j];
        locks[j + 1] = s;
    }

    save = PyEval_SaveThread();
    for (i = 0; i < nlocks; i++)
        PyThread_acquire_lock(locks[i]->lock, 1);
    /* close() may have won the race before we held every lock: re-check. */
    for (i = 0; i < nlocks; i++) {
        if (!locks[i]->session) { closed = 1; break; }
    }
    if (!closed) {
        init_zg_error(&err);
        (void)blg_fill_masks_batch(sess, masks, mask_words, statuses,
                                  (size_t)n, &err);
    }
    for (i = nlocks - 1; i >= 0; i--)
        PyThread_release_lock(locks[i]->lock);
    PyEval_RestoreThread(save);
    if (closed) {
        PyErr_SetString(ExcTable[BLG_ERR_WRONG_STATE], "session is closed");
        goto fail;
    }

    result = PyList_New(n);
    if (!result) goto fail;
    for (i = 0; i < n; i++) {
        PyObject *code = PyLong_FromLong(statuses[i]);
        PyObject *row;
        if (!code) goto fail;
        if (statuses[i] == BLG_OK) {
            row = PyTuple_Pack(2, code, bufs[i]);
        } else {
            row = PyTuple_Pack(2, code, Py_None);
        }
        Py_DECREF(code);
        if (!row) goto fail;
        if (PyList_SetItem(result, i, row) < 0) { Py_DECREF(row); goto fail; }
    }
    free_batch(bufs, robjs, sess, masks, statuses, n);
    PyMem_Free(locks);
    Py_DECREF(fast);
    return result;

fail:
    free_batch(bufs, robjs, sess, masks, statuses, n);
    PyMem_Free(locks);
    Py_XDECREF(result);
    Py_DECREF(fast);
    return NULL;
}

static PyMethodDef module_methods[] = {
    {"abi_version", (PyCFunction)mod_abi_version, METH_NOARGS,
     "abi_version() -> int"},
    {"mask_words_for", (PyCFunction)mod_mask_words, METH_O,
     "mask_words_for(vocab_size) -> int"},
    {"fill_masks_batch", (PyCFunction)mod_fill_masks_batch, METH_O,
     "fill_masks_batch(sessions) -> list[(status, bytes|None)]"},
    {NULL}
};

/* ------------------------------------------------------------------ */
/* Module init                                                         */
/* ------------------------------------------------------------------ */

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "bolorgir._core",
    "Thin wrapper over the libbolorgir C ABI (Limited API, abi3).",
    0,
    module_methods,
};

static int add_exception(PyObject *mod, PyObject **slot, int code,
                         const char *name, PyObject *base) {
    char fullname[128];
    PyObject *exc;
    PyOS_snprintf(fullname, sizeof(fullname), "bolorgir._core.%s", name);
    exc = PyErr_NewException(fullname, base, NULL);
    if (!exc) return -1;
    *slot = exc;
    if (code >= 0 && code < 14) {
        Py_INCREF(exc);
        ExcTable[code] = exc;
    }
    return PyModule_AddObject(mod, name, exc);
}

PyMODINIT_FUNC PyInit__core(void) {
    PyObject *mod;

    ContextType = PyType_FromSpec(&Context_spec);
    if (!ContextType) return NULL;
    GrammarType = PyType_FromSpec(&Grammar_spec);
    if (!GrammarType) { Py_DECREF(ContextType); return NULL; }
    SessionType = PyType_FromSpec(&Session_spec);
    if (!SessionType) { Py_DECREF(ContextType); Py_DECREF(GrammarType); return NULL; }

    mod = PyModule_Create(&moduledef);
    if (!mod) return NULL;

    if (add_exception(mod, &ExcBase, -1, "ZigConstraintsError", PyExc_Exception) < 0) goto fail;
    if (add_exception(mod, &ExcTable[1], 1, "InvalidArgumentError", ExcBase) < 0) goto fail;
    if (add_exception(mod, &ExcTable[2], 2, "InvalidSchemaError", ExcBase) < 0) goto fail;
    if (add_exception(mod, &ExcTable[3], 3, "UnsupportedFeatureError", ExcBase) < 0) goto fail;
    if (add_exception(mod, &ExcTable[4], 4, "UnsatisfiableConstraintError", ExcBase) < 0) goto fail;
    if (add_exception(mod, &ExcTable[5], 5, "UnsupportedTokenizerError", ExcBase) < 0) goto fail;
    if (add_exception(mod, &ExcTable[6], 6, "InvalidTokenError", ExcBase) < 0) goto fail;
    if (add_exception(mod, &ExcTable[7], 7, "DeadEndError", ExcBase) < 0) goto fail;
    if (add_exception(mod, &ExcTable[8], 8, "ResourceLimitError", ExcBase) < 0) goto fail;
    if (add_exception(mod, &ExcTable[9], 9, "CancelledError", ExcBase) < 0) goto fail;
    if (add_exception(mod, &ExcTable[10], 10, "BusyError", ExcBase) < 0) goto fail;
    if (add_exception(mod, &ExcTable[11], 11, "WrongStateError", ExcBase) < 0) goto fail;
    if (add_exception(mod, &ExcTable[12], 12, "BufferTooSmallError", ExcBase) < 0) goto fail;
    if (add_exception(mod, &ExcTable[13], 13, "InternalError", ExcBase) < 0) goto fail;

    if (PyModule_AddObject(mod, "Context", ContextType) < 0) goto fail;
    if (PyModule_AddObject(mod, "Grammar", GrammarType) < 0) goto fail;
    if (PyModule_AddObject(mod, "Session", SessionType) < 0) goto fail;

    if (PyModule_AddIntConstant(mod, "BLG_ABI_VERSION", BLG_ABI_VERSION) < 0) goto fail;
    if (PyModule_AddIntConstant(mod, "KIND_JSON_SCHEMA", BLG_CONSTRAINT_JSON_SCHEMA) < 0) goto fail;
    if (PyModule_AddIntConstant(mod, "KIND_LITERAL_SET", BLG_CONSTRAINT_LITERAL_SET) < 0) goto fail;
    if (PyModule_AddIntConstant(mod, "MODE_LAZY", BLG_MODE_LAZY) < 0) goto fail;
    if (PyModule_AddIntConstant(mod, "MODE_ADAPTIVE", BLG_MODE_ADAPTIVE) < 0) goto fail;
    if (PyModule_AddIntConstant(mod, "MODE_PRECOMPUTE", BLG_MODE_PRECOMPUTE) < 0) goto fail;

    return mod;

fail:
    Py_DECREF(mod);
    return NULL;
}
