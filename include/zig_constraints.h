/*
 * zig-constraints — adaptive structured-generation engine.
 * C ABI, version 1. Fixed-width integer types; buffer sizes are size_t.
 * Every parameter struct starts with uint32_t struct_size.
 * Diagnostics travel through the caller-provided zg_error buffer;
 * there is no global last_error.
 */
#ifndef ZIG_CONSTRAINTS_H
#define ZIG_CONSTRAINTS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ZG_ABI_VERSION 1u
#define ZG_ERROR_MESSAGE_CAP 256u
#define ZG_JSON_POINTER_CAP 256u
#define ZG_STATS_CATEGORY_COUNT 8u

/*
 * Versioning: parameter structs only grow at the tail. A caller built
 * against an older, smaller layout passes its smaller struct_size and gets
 * documented defaults for the fields it does not know about; the kernel
 * never writes beyond the caller's struct_size. Legacy sizes accepted:
 * zg_error 268, zg_context_config 56, zg_stats 208.
 */

/* Memory categories in zg_stats.mem_used/mem_peak */
#define ZG_MEM_TOKENIZER 0u
#define ZG_MEM_GRAMMAR 1u
#define ZG_MEM_SESSION 2u
#define ZG_MEM_CACHE 3u
#define ZG_MEM_TEMP 4u
#define ZG_MEM_TOTAL 5u

typedef enum zg_status {
    ZG_OK = 0,
    ZG_ERR_INVALID_ARGUMENT = 1,
    ZG_ERR_INVALID_SCHEMA = 2,
    ZG_ERR_UNSUPPORTED_FEATURE = 3,
    ZG_ERR_UNSATISFIABLE_CONSTRAINT = 4,
    ZG_ERR_UNSUPPORTED_TOKENIZER = 5,
    ZG_ERR_INVALID_TOKEN = 6,
    ZG_ERR_DEAD_END = 7,
    ZG_ERR_RESOURCE_LIMIT = 8,
    ZG_ERR_CANCELLED = 9,
    ZG_ERR_BUSY = 10,
    ZG_ERR_WRONG_STATE = 11,
    ZG_ERR_BUFFER_TOO_SMALL = 12,
    ZG_ERR_INTERNAL = 13
} zg_status;

typedef struct zg_error {
    uint32_t struct_size; /* == sizeof(zg_error) */
    int32_t code;         /* zg_status */
    uint32_t schema_offset; /* byte offset in the schema, or UINT32_MAX */
    char message[ZG_ERROR_MESSAGE_CAP]; /* NUL-terminated, UTF-8 */
    /* RFC 6901 JSON Pointer to the failing schema node (e.g.
     * "/properties/k0/items"), NUL-terminated; "" when not applicable.
     * Overlong paths are truncated and end with U+2026. Absent in legacy
     * (268-byte) buffers. */
    char json_pointer[ZG_JSON_POINTER_CAP];
} zg_error;

typedef enum zg_mode {
    ZG_MODE_LAZY = 0,
    ZG_MODE_ADAPTIVE = 1,
    /* Experimental: bounded BFS mask warm-up at compile (see
     * precompute_max_states) plus the adaptive runtime policy. */
    ZG_MODE_PRECOMPUTE = 2
} zg_mode;

typedef enum zg_constraint_kind {
    ZG_CONSTRAINT_JSON_SCHEMA = 0,
    ZG_CONSTRAINT_LITERAL_SET = 1
} zg_constraint_kind;

/* Sentinel for zg_context_config.cache_limit_bytes: use the default budget.
 * The value 0 instead disables the mask cache entirely. */
#define ZG_CACHE_DEFAULT UINT64_MAX

typedef struct zg_context_config {
    uint32_t struct_size; /* == sizeof(zg_context_config) */
    uint32_t version;     /* ZG_ABI_VERSION */
    uint32_t mode;        /* zg_mode */
    uint32_t max_depth;            /* 0 => 64 */
    uint32_t max_threads_per_state; /* 0 => 64 */
    uint32_t reserved0;
    uint64_t memory_limit_bytes;  /* 0 => 256 MiB; total kernel budget */
    uint64_t cache_limit_bytes;   /* 0 => cache off; ZG_CACHE_DEFAULT => 64 MiB; hard cap, <= memory_limit */
    uint64_t session_limit_bytes; /* 0 => 8 MiB per session */
    uint64_t schema_limit_bytes;  /* 0 => 1 MiB per input schema */
    /* Per-call work budget in abstract ops (trie nodes, parser steps,
     * coverage/precompute iterations) for zg_compile and zg_fill_mask(s);
     * 0 => unlimited. Exceeding it returns ZG_ERR_RESOURCE_LIMIT, never a
     * partial mask. */
    uint64_t work_limit_ops;
    /* Adaptive cache admission (FR-9): a computed mask is stored when the
     * state was seen at least adaptive_min_hits times (0 => 2) or its
     * measured compute cost reached adaptive_min_cost_ns (0 => 50000). */
    uint64_t adaptive_min_hits;
    uint64_t adaptive_min_cost_ns;
    /* ZG_MODE_PRECOMPUTE: max parser states enumerated by the compile-time
     * BFS warm-up; 0 => 4096. Exhaustion is not an error: generation
     * continues lazily. */
    uint64_t precompute_max_states;
} zg_context_config;

typedef struct zg_token_entry {
    uint32_t id;
    uint32_t reserved;
    uint64_t offset; /* into blob */
    uint64_t length; /* bytes; 0 is forbidden for regular tokens */
} zg_token_entry;

/*
 * The token table is handed over once at context creation; the kernel copies
 * the data into context-owned storage. entries must cover every id in
 * 0..vocab_size-1 exactly once. eos_ids are the allowed terminating tokens;
 * special_ids are auxiliary non-EOS tokens (forbidden inside a document).
 */
typedef struct zg_tokenizer_desc {
    uint32_t struct_size; /* == sizeof(zg_tokenizer_desc) */
    uint32_t vocab_size;
    const zg_token_entry *entries;
    size_t entry_count;
    const uint8_t *blob;
    size_t blob_len;
    const uint32_t *eos_ids;
    size_t eos_count;
    const uint32_t *special_ids;
    size_t special_count;
} zg_tokenizer_desc;

typedef struct zg_compile_request {
    uint32_t struct_size; /* == sizeof(zg_compile_request) */
    uint32_t kind;        /* zg_constraint_kind */
    const char *profile;  /* "canonical-v1" or NULL (== canonical-v1) */
    const uint8_t *data;  /* schema JSON, or a JSON array of strings for LITERAL_SET */
    size_t data_len;
} zg_compile_request;

typedef struct zg_stats {
    uint32_t struct_size; /* == sizeof(zg_stats) */
    uint32_t version;
    uint64_t compile_ns;
    uint64_t tokenizer_prepare_ns;
    uint64_t accept_ns_total;
    uint64_t mask_ns_total;
    uint64_t mask_calls;
    uint64_t tokens_accepted;
    uint64_t cache_hits;
    uint64_t cache_misses;
    uint64_t cache_evictions;
    uint64_t mem_used[ZG_STATS_CATEGORY_COUNT];
    uint64_t mem_peak[ZG_STATS_CATEGORY_COUNT];
    uint32_t mode;       /* effective zg_mode of the context */
    uint32_t reserved0;
    uint64_t errors_total;          /* failed public calls (context paths) */
    uint64_t errors_resource_limit;
    uint64_t errors_cancelled;      /* cancellation count */
    uint64_t cache_adaptive_skips;  /* computed masks not admitted by the policy */
    uint64_t precompute_states;     /* masks computed by compile-time warm-up */
    uint64_t work_ops_total;        /* ops charged against work_limit_ops */
} zg_stats;

typedef struct zg_context zg_context;
typedef struct zg_grammar zg_grammar;
typedef struct zg_session zg_session;

uint32_t zg_abi_version(void);

/*
 * A context binds the tokenizer, limits, mode and cache.
 * The context must outlive its grammars and sessions; destroying a busy
 * context returns ZG_ERR_BUSY.
 */
zg_status zg_context_create(const zg_context_config *config,
                            const zg_tokenizer_desc *tokenizer,
                            zg_context **out_context, zg_error *err);
zg_status zg_context_destroy(zg_context *ctx);

/*
 * The schema is copied/compiled during the call; the data pointer is not
 * retained. Compilation also verifies that the tokenizer covers the
 * constraint (every literal segmentable into tokens, open classes
 * completable); insufficient coverage fails with
 * ZG_ERR_UNSUPPORTED_TOKENIZER.
 */
zg_status zg_compile(zg_context *ctx, const zg_compile_request *request,
                     zg_grammar **out_grammar, zg_error *err);
void zg_grammar_release(zg_grammar *grammar);

zg_status zg_session_create(zg_context *ctx, zg_grammar *grammar,
                            zg_session **out_session, zg_error *err);
void zg_session_destroy(zg_session *session);

/*
 * mask is a caller-owned buffer of mask_words uint32 words
 * (must be >= ceil(vocab_size/32)), reusable across steps.
 * Bit t: word t/32, bit t%32; 1 = allowed. On error the mask is invalid.
 * The call does not change the logical session state.
 */
zg_status zg_fill_mask(zg_session *session, uint32_t *mask, size_t mask_words,
                       zg_error *err);

/*
 * Sequential processing of an array of independent sessions. masks[i] is the
 * buffer for sessions[i]; statuses[i] receives the per-row zg_status
 * independently of the others. count==0 is a no-op. Returns ZG_OK when all
 * rows are OK, otherwise the code of the first failed row.
 */
zg_status zg_fill_masks_batch(zg_session *const *sessions,
                              uint32_t *const *masks, size_t mask_words_each,
                              int32_t *statuses, size_t count, zg_error *err);

/*
 * Accepting a token. A forbidden token yields ZG_ERR_INVALID_TOKEN and the
 * logical state is unchanged. On a finished/aborted session —
 * ZG_ERR_WRONG_STATE.
 */
zg_status zg_accept_token(zg_session *session, uint32_t token_id,
                          zg_error *err);

zg_status zg_can_end(const zg_session *session, bool *out_can_end);

/* finish requires can_end, otherwise ZG_ERR_WRONG_STATE. */
zg_status zg_finish(zg_session *session, zg_error *err);
zg_status zg_abort(zg_session *session);

zg_status zg_get_stats(const zg_context *ctx, zg_stats *out_stats);
zg_status zg_get_stats_session(const zg_session *session, zg_stats *out_stats);

/*
 * Registers a caller-owned cancellation flag: a single byte the kernel
 * reads atomically (acquire) between bounded work portions of zg_compile
 * and zg_fill_mask(s). Storing a non-zero value (release ordering) makes
 * the running and subsequent calls on this context return
 * ZG_ERR_CANCELLED; storing 0 clears the request. The byte must remain
 * valid until zg_cancel_flag_set(ctx, NULL) or zg_context_destroy(ctx).
 * NULL detaches the flag. Threads writing the flag should use C11
 * atomic_store_explicit(..., memory_order_release) or equivalent.
 */
zg_status zg_cancel_flag_set(zg_context *ctx, uint8_t *flag);

#ifdef __cplusplus
}
#endif

#endif /* ZIG_CONSTRAINTS_H */
