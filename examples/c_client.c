/*
 * c_client - minimal C99 client for Bolorgir.
 *
 * The vocabulary is byte-complete: ids 0..255 are single bytes, 256..259 are
 * handy structural tokens, 260 EOS, 261 PAD (special). Full byte coverage
 * provably makes completion reachable for any schema (spec 3.1, FR-7):
 * with a partial vocabulary and an infinite language the compiler returns
 * UNSUPPORTED_TOKENIZER and the example would be invalid.
 *
 * The action/amount schema from the spec example, a fill_mask -> accept loop
 * over a fixed trace, finish, then print stats.
 */
#include "bolorgir.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define VOCAB_SIZE 262u
#define EOS_ID 260u
#define PAD_ID 261u
#define MASK_WORDS ((VOCAB_SIZE + 31u) / 32u)

static int fail(const char *what, blg_status rc, const blg_error *err) {
    fprintf(stderr, "FAIL: %s -> status %d: %s\n", what, (int)rc, err->message);
    return 1;
}

#define CHECK(what, expr)                          \
    do {                                           \
        blg_status rc_ = (expr);                    \
        if (rc_ != BLG_OK) return fail(what, rc_, &err); \
    } while (0)

int main(void) {
    if (blg_abi_version() != BLG_ABI_VERSION) {
        fprintf(stderr, "FAIL: ABI version mismatch: %u\n", blg_abi_version());
        return 1;
    }

    static uint8_t blob[1024];
    static blg_token_entry entries[VOCAB_SIZE];
    size_t off = 0;
    /* ids 0..255: single bytes - full vocabulary coverage */
    for (uint32_t i = 0; i < 256u; i++) {
        blob[off] = (uint8_t)i;
        entries[i].id = i;
        entries[i].reserved = 0;
        entries[i].offset = off;
        entries[i].length = 1;
        off += 1;
    }
    /* 256..259: structural pieces of canonical JSON */
    static const char *const k_extra[] = {
        "{\"action\":", "\"amount\":", "\"buy\"", ","
    };
    for (uint32_t i = 0; i < 4u; i++) {
        size_t len = strlen(k_extra[i]);
        memcpy(blob + off, k_extra[i], len);
        entries[256u + i].id = 256u + i;
        entries[256u + i].reserved = 0;
        entries[256u + i].offset = off;
        entries[256u + i].length = len;
        off += len;
    }
    /* 260 EOS (empty bytes), 261 PAD (special, bytes not part of the document) */
    entries[EOS_ID].id = EOS_ID;
    entries[EOS_ID].reserved = 0;
    entries[EOS_ID].offset = 0;
    entries[EOS_ID].length = 0;
    {
        const char *pad = "<pad>";
        size_t len = strlen(pad);
        memcpy(blob + off, pad, len);
        entries[PAD_ID].id = PAD_ID;
        entries[PAD_ID].reserved = 0;
        entries[PAD_ID].offset = off;
        entries[PAD_ID].length = len;
        off += len;
    }

    const uint32_t eos_ids[] = {EOS_ID};
    const uint32_t special_ids[] = {PAD_ID};
    blg_tokenizer_desc tdesc;
    memset(&tdesc, 0, sizeof(tdesc));
    tdesc.struct_size = sizeof(tdesc);
    tdesc.vocab_size = VOCAB_SIZE;
    tdesc.entries = entries;
    tdesc.entry_count = VOCAB_SIZE;
    tdesc.blob = blob;
    tdesc.blob_len = off;
    tdesc.eos_ids = eos_ids;
    tdesc.eos_count = 1;
    tdesc.special_ids = special_ids;
    tdesc.special_count = 1;

    blg_context_config cfg;
    memset(&cfg, 0, sizeof(cfg));
    cfg.struct_size = sizeof(cfg);
    cfg.version = BLG_ABI_VERSION;
    cfg.mode = BLG_MODE_ADAPTIVE;
    cfg.cache_limit_bytes = BLG_CACHE_DEFAULT; /* 0 would disable the cache */

    blg_error err;
    memset(&err, 0, sizeof(err));
    err.struct_size = sizeof(err);

    blg_context *ctx = NULL;
    CHECK("blg_context_create", blg_context_create(&cfg, &tdesc, &ctx, &err));

    const char *schema_json =
        "{\"type\":\"object\","
        "\"properties\":{"
        "\"action\":{\"type\":\"string\",\"enum\":[\"buy\",\"sell\"]},"
        "\"amount\":{\"type\":\"integer\"}},"
        "\"required\":[\"action\",\"amount\"],"
        "\"additionalProperties\":false}";

    blg_compile_request req;
    memset(&req, 0, sizeof(req));
    req.struct_size = sizeof(req);
    req.kind = BLG_CONSTRAINT_JSON_SCHEMA;
    req.profile = "canonical-v1";
    req.data = (const uint8_t *)schema_json;
    req.data_len = strlen(schema_json);

    blg_grammar *gram = NULL;
    CHECK("blg_compile", blg_compile(ctx, &req, &gram, &err));

    blg_session *sess = NULL;
    CHECK("blg_session_create", blg_session_create(ctx, gram, &sess, &err));

    /* PAD (special non-EOS) must be rejected. */
    if (blg_accept_token(sess, PAD_ID, &err) != BLG_ERR_INVALID_TOKEN) {
        fprintf(stderr, "FAIL: PAD token was not rejected\n");
        return 1;
    }

    /* Trace: {"action":"buy","amount":12} EOS:
     * one structural token + digit/brace bytes from the full vocabulary. */
    const uint32_t trace[] = {256u, 258u, 259u, 257u, 49u, 50u, 125u, EOS_ID};
    const size_t trace_len = sizeof(trace) / sizeof(trace[0]);

    uint32_t mask[MASK_WORDS];
    for (size_t step = 0; step < trace_len; step++) {
        CHECK("blg_fill_mask", blg_fill_mask(sess, mask, MASK_WORDS, &err));
        uint32_t want = trace[step];
        if (!((mask[want / 32u] >> (want % 32u)) & 1u)) {
            fprintf(stderr, "FAIL: token %u (id) not allowed at step %zu\n",
                    want, step);
            return 1;
        }
        CHECK("blg_accept_token", blg_accept_token(sess, want, &err));
    }

    bool can_end = false;
    CHECK("blg_can_end", blg_can_end(sess, &can_end));
    if (!can_end) {
        fprintf(stderr, "FAIL: can_end is false after full document\n");
        return 1;
    }
    CHECK("blg_finish", blg_finish(sess, &err));

    blg_stats sstats;
    memset(&sstats, 0, sizeof(sstats));
    CHECK("blg_get_stats_session", blg_get_stats_session(sess, &sstats));

    blg_session_destroy(sess);
    blg_grammar_release(gram);

    blg_stats cstats;
    memset(&cstats, 0, sizeof(cstats));
    CHECK("blg_get_stats", blg_get_stats(ctx, &cstats));

    printf("OK\n");
    printf("mask_calls=%llu tokens_accepted=%llu cache_hits=%llu cache_misses=%llu\n",
           (unsigned long long)cstats.mask_calls,
           (unsigned long long)cstats.tokens_accepted,
           (unsigned long long)cstats.cache_hits,
           (unsigned long long)cstats.cache_misses);
    printf("session: mask_ns=%llu accept_ns=%llu mem_session=%llu\n",
           (unsigned long long)sstats.mask_ns_total,
           (unsigned long long)sstats.accept_ns_total,
           (unsigned long long)sstats.mem_used[BLG_MEM_SESSION]);
    printf("mem_total=%llu mem_peak_total=%llu\n",
           (unsigned long long)cstats.mem_used[BLG_MEM_TOTAL],
           (unsigned long long)cstats.mem_peak[BLG_MEM_TOTAL]);

    CHECK("blg_context_destroy", blg_context_destroy(ctx));
    return 0;
}
