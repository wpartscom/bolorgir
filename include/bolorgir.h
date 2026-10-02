/*
 * Bolorgir - adaptive structured-generation engine.
 * C ABI, version 1. Fixed-width integer types; buffer sizes are size_t.
 * Every parameter struct starts with uint32_t struct_size.
 * Diagnostics travel through the caller-provided blg_error buffer;
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

#define BLG_ABI_VERSION 1u
#define BLG_ERROR_MESSAGE_CAP 256u
#define BLG_JSON_POINTER_CAP 256u
#define BLG_STATS_CATEGORY_COUNT 8u

/*
 * Versioning: parameter structs only grow at the tail. A caller built
 * against an older, smaller layout passes its smaller struct_size and gets
 * documented defaults for the fields it does not know about; the kernel
 * never writes beyond the caller's struct_size. Legacy sizes accepted:
 * blg_error 268, blg_context_config 56 (also 88, pre-ADR-0007),
 * blg_stats 208, blg_compile_request 32 (pre-P5, no registry).
 */

/* Memory categories in blg_stats.mem_used/mem_peak */
#define BLG_MEM_TOKENIZER 0u
#define BLG_MEM_GRAMMAR 1u
#define BLG_MEM_SESSION 2u
#define BLG_MEM_CACHE 3u
#define BLG_MEM_TEMP 4u
#define BLG_MEM_TOTAL 5u

typedef enum blg_status {
    BLG_OK = 0,
    BLG_ERR_INVALID_ARGUMENT = 1,
    BLG_ERR_INVALID_SCHEMA = 2,
    BLG_ERR_UNSUPPORTED_FEATURE = 3,
    BLG_ERR_UNSATISFIABLE_CONSTRAINT = 4,
    BLG_ERR_UNSUPPORTED_TOKENIZER = 5,
    BLG_ERR_INVALID_TOKEN = 6,
    BLG_ERR_DEAD_END = 7,
    BLG_ERR_RESOURCE_LIMIT = 8,
    BLG_ERR_CANCELLED = 9,
    BLG_ERR_BUSY = 10,
    BLG_ERR_WRONG_STATE = 11,
    BLG_ERR_BUFFER_TOO_SMALL = 12,
    BLG_ERR_INTERNAL = 13
} blg_status;

typedef struct blg_error {
    uint32_t struct_size; /* == sizeof(blg_error) */
    int32_t code;         /* blg_status */
    uint32_t schema_offset; /* byte offset in the schema, or UINT32_MAX */
    char message[BLG_ERROR_MESSAGE_CAP]; /* NUL-terminated, UTF-8 */
    /* RFC 6901 JSON Pointer to the failing schema node (e.g.
     * "/properties/k0/items"), NUL-terminated; "" when not applicable.
     * Overlong paths are truncated and end with U+2026. Absent in legacy
     * (268-byte) buffers. */
    char json_pointer[BLG_JSON_POINTER_CAP];
} blg_error;

typedef enum blg_mode {
    BLG_MODE_LAZY = 0,
    BLG_MODE_ADAPTIVE = 1,
    /* Experimental: bounded BFS mask warm-up at compile (see
     * precompute_max_states) plus the adaptive runtime policy. */
    BLG_MODE_PRECOMPUTE = 2
} blg_mode;

typedef enum blg_constraint_kind {
    BLG_CONSTRAINT_JSON_SCHEMA = 0,
    BLG_CONSTRAINT_LITERAL_SET = 1
} blg_constraint_kind;

/* Sentinel for blg_context_config.cache_limit_bytes: use the default budget.
 * The value 0 instead disables the mask cache entirely. */
#define BLG_CACHE_DEFAULT UINT64_MAX

typedef struct blg_context_config {
    uint32_t struct_size; /* == sizeof(blg_context_config) */
    uint32_t version;     /* BLG_ABI_VERSION */
    uint32_t mode;        /* blg_mode */
    uint32_t max_depth;            /* 0 => 64 */
    uint32_t max_threads_per_state; /* 0 => 128 */
    uint32_t reserved0;
    uint64_t memory_limit_bytes;  /* 0 => 256 MiB; total kernel budget */
    uint64_t cache_limit_bytes;   /* 0 => cache off; BLG_CACHE_DEFAULT => 64 MiB; hard cap, <= memory_limit */
    uint64_t session_limit_bytes; /* 0 => 8 MiB per session */
    uint64_t schema_limit_bytes;  /* 0 => 1 MiB per input schema */
    /* Per-call work budget in abstract ops (trie nodes, parser steps,
     * coverage/precompute iterations) for blg_compile and blg_fill_mask(s);
     * 0 => unlimited. Exceeding it returns BLG_ERR_RESOURCE_LIMIT, never a
     * partial mask. */
    uint64_t work_limit_ops;
    /* Adaptive cache admission (FR-9): a computed mask is stored when the
     * state was seen at least adaptive_min_hits times (0 => 2) or its
     * measured compute cost reached adaptive_min_cost_ns (0 => 50000). */
    uint64_t adaptive_min_hits;
    uint64_t adaptive_min_cost_ns;
    /* BLG_MODE_PRECOMPUTE: max parser states enumerated by the compile-time
     * BFS warm-up; 0 => 4096. Exhaustion is not an error: generation
     * continues lazily. */
    uint64_t precompute_max_states;
    /* ADR-0007 mask fast path kill switch: 0 => default (enabled),
     * 1 => enabled, 2 => disabled (the plain trie walk). The
     * BLG_MASK_FAST_PATH=0 environment variable forces disabled. The fast
     * path is an equivalence optimization: masks are bit-for-bit identical
     * either way; disabling only costs time. */
    uint64_t mask_fast_path;
    /* ADR-0007 parallelism: CPU workers for the mask classification;
     * 0/1 => single-threaded (default). Masks are bit-for-bit independent
     * of the worker count, and all worker steps charge the same
     * work_limit_ops counter. max_threads_per_state is NOT a CPU thread
     * count and is never repurposed as one. */
    uint64_t max_workers;
} blg_context_config;

typedef struct blg_token_entry {
    uint32_t id;
    uint32_t reserved;
    uint64_t offset; /* into blob */
    uint64_t length; /* bytes; 0 is forbidden for regular tokens */
} blg_token_entry;

/* Tokenizer flags (blg_tokenizer_desc.flags). */
/* The decoder drops one leading space of the whole generated text (HF
 * SentencePiece Strip(" ", start=1, stop=0), e.g. Meta/Llama decoders).
 * The kernel models this by compiling the constraint language
 * L as {t in L : t does not start with a space} u {" " + t : t in L},
 * so the accepted token-byte stream matches the decoded text exactly. */
#define BLG_TOKENIZER_STRIP_LEAD_SPACE 0x1u

/*
 * The token table is handed over once at context creation; the kernel copies
 * the data into context-owned storage. entries must cover every id in
 * 0..vocab_size-1 exactly once. eos_ids are the allowed terminating tokens;
 * special_ids are auxiliary non-EOS tokens (forbidden inside a document).
 */
typedef struct blg_tokenizer_desc {
    uint32_t struct_size; /* == sizeof(blg_tokenizer_desc) */
    uint32_t vocab_size;
    const blg_token_entry *entries;
    size_t entry_count;
    const uint8_t *blob;
    size_t blob_len;
    const uint32_t *eos_ids;
    size_t eos_count;
    const uint32_t *special_ids;
    size_t special_count;
    /* Tail-grown field (legacy struct_size accepted, flags default to 0);
     * see BLG_TOKENIZER_* defines. */
    uint32_t flags;
    uint32_t reserved1;
} blg_tokenizer_desc;

typedef struct blg_compile_request {
    uint32_t struct_size; /* == sizeof(blg_compile_request) */
    uint32_t kind;        /* blg_constraint_kind */
    const char *profile;  /* "canonical-v1", "spec-v1" or NULL (== canonical-v1) */
    const uint8_t *data;  /* schema JSON, or a JSON array of strings for LITERAL_SET */
    size_t data_len;
    /* Tail-grown (P5, ADR-0006 D5 / ADR-0008): optional external-$ref
     * registry snapshot, spec-v1 only. JSON object
     * {"version": <string, annotation>, "documents": {"<uri>": <schema>}}.
     * The snapshot is immutable by contract: its exact bytes join the
     * compile-artifact cache key (and the grammar identity), so a registry
     * change is a new snapshot and invalidates cached artifacts. A $ref
     * whose base URI exactly matches a "documents" key resolves inside
     * that document; no network access ever happens. Legacy 32-byte
     * requests (no registry fields) behave as registry_data == NULL:
     * every external $ref stays BLG_ERR_UNSUPPORTED_FEATURE.
     * The pointer is not retained. Passing a registry with any other
     * profile or constraint kind is BLG_ERR_UNSUPPORTED_FEATURE. */
    const uint8_t *registry_data;
    size_t registry_data_len;
} blg_compile_request;

typedef struct blg_stats {
    uint32_t struct_size; /* == sizeof(blg_stats) */
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
    uint64_t mem_used[BLG_STATS_CATEGORY_COUNT];
    uint64_t mem_peak[BLG_STATS_CATEGORY_COUNT];
    uint32_t mode;       /* effective blg_mode of the context */
    uint32_t reserved0;
    uint64_t errors_total;          /* failed public calls (context paths) */
    uint64_t errors_resource_limit;
    uint64_t errors_cancelled;      /* cancellation count */
    uint64_t cache_adaptive_skips;  /* computed masks not admitted by the policy */
    uint64_t precompute_states;     /* masks computed by compile-time warm-up */
    uint64_t work_ops_total;        /* ops charged against work_limit_ops */
} blg_stats;

typedef struct blg_context blg_context;
typedef struct blg_grammar blg_grammar;
typedef struct blg_session blg_session;

uint32_t blg_abi_version(void);

/*
 * A context binds the tokenizer, limits, mode and cache.
 * The context must outlive its grammars and sessions; destroying a busy
 * context returns BLG_ERR_BUSY. Busy = at least one live external grammar
 * reference (a user-held handle), a live session, or an in-flight kernel
 * call (compile/reset_cache) of this context. Concurrent destroy is safe
 * against calls already in the kernel, but the caller must serialize
 * destroy with calls that may start afterwards (destroying a context
 * another thread is about to use is undefined behavior).
 */
blg_status blg_context_create(const blg_context_config *config,
                            const blg_tokenizer_desc *tokenizer,
                            blg_context **out_context, blg_error *err);
blg_status blg_context_destroy(blg_context *ctx);

/*
 * Drops all cached compile artifacts: the cache releases its references to
 * grammars and the memory returns to the context accounting. Live grammar
 * handles and sessions keep working (they hold their own references);
 * subsequent compiles recompile on a miss. Useful to bound the retained
 * memory of long-lived contexts and to verify the "memory returns after
 * release" invariant.
 */
blg_status blg_context_reset_cache(blg_context *ctx);

/*
 * The schema is copied/compiled during the call; the data pointer is not
 * retained. Compilation also verifies that the tokenizer covers the
 * constraint (every literal segmentable into tokens, open classes
 * completable); insufficient coverage fails with
 * BLG_ERR_UNSUPPORTED_TOKENIZER.
 */
blg_status blg_compile(blg_context *ctx, const blg_compile_request *request,
                     blg_grammar **out_grammar, blg_error *err);
void blg_grammar_release(blg_grammar *grammar);

blg_status blg_session_create(blg_context *ctx, blg_grammar *grammar,
                            blg_session **out_session, blg_error *err);
void blg_session_destroy(blg_session *session);

/*
 * mask is a caller-owned buffer of mask_words uint32 words
 * (must be >= ceil(vocab_size/32)), reusable across steps.
 * Bit t: word t/32, bit t%32; 1 = allowed. On error the mask is invalid.
 * The call does not change the logical session state.
 */
blg_status blg_fill_mask(blg_session *session, uint32_t *mask, size_t mask_words,
                       blg_error *err);

/*
 * Sequential processing of an array of independent sessions. masks[i] is the
 * buffer for sessions[i]; statuses[i] receives the per-row blg_status
 * independently of the others. count==0 is a no-op. Returns BLG_OK when all
 * rows are OK, otherwise the code of the first failed row.
 */
blg_status blg_fill_masks_batch(blg_session *const *sessions,
                              uint32_t *const *masks, size_t mask_words_each,
                              int32_t *statuses, size_t count, blg_error *err);

/*
 * Accepting a token. A forbidden token yields BLG_ERR_INVALID_TOKEN and the
 * logical state is unchanged. On a finished/aborted session -
 * BLG_ERR_WRONG_STATE.
 */
blg_status blg_accept_token(blg_session *session, uint32_t token_id,
                          blg_error *err);

blg_status blg_can_end(const blg_session *session, bool *out_can_end);

/* finish requires can_end, otherwise BLG_ERR_WRONG_STATE. */
blg_status blg_finish(blg_session *session, blg_error *err);
blg_status blg_abort(blg_session *session);

blg_status blg_get_stats(const blg_context *ctx, blg_stats *out_stats);
blg_status blg_get_stats_session(const blg_session *session, blg_stats *out_stats);

/*
 * Registers a caller-owned cancellation flag: a single byte the kernel
 * reads atomically (acquire) between bounded work portions of blg_compile
 * and blg_fill_mask(s). Storing a non-zero value (release ordering) makes
 * the running and subsequent calls on this context return
 * BLG_ERR_CANCELLED; storing 0 clears the request. The byte must remain
 * valid until blg_cancel_flag_set(ctx, NULL) or blg_context_destroy(ctx).
 * NULL detaches the flag. Threads writing the flag should use C11
 * atomic_store_explicit(..., memory_order_release) or equivalent.
 */
blg_status blg_cancel_flag_set(blg_context *ctx, uint8_t *flag);

/*
 * Diagnostics, not part of the stable semantics: the calling thread's
 * ADR-0005 D3 undecided-reason histogram. Whenever a mask fill or accept
 * check fails with BLG_ERR_RESOURCE_LIMIT because a state could be
 * neither certified nor settled by the budgeted search, the state's
 * uncertified frames are classified into this histogram (indices follow
 * the UndReason enum order in src/complete.zig: fail causes first, then
 * per-frame-kind and open-object gate sub-reasons). It exists to
 * prioritize certificate families by measured impact.
 * blg_cert_stats copies up to cap entries into out and returns the full
 * histogram length; blg_cert_stats_reset zeroes it.
 */
size_t blg_cert_stats(uint64_t *out, size_t cap);
void blg_cert_stats_reset(void);

#ifdef __cplusplus
}
#endif

#endif /* ZIG_CONSTRAINTS_H */
