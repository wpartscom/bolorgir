/*
 * c_client — минимальный C99-клиент zig-constraints.
 * Ручной токенизатор (~40 токенов), схема action/amount из примера ТЗ,
 * цикл fill_mask -> accept по заданной трассе, finish, печать stats.
 */
#include "zig_constraints.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define VOCAB_SIZE 40u
#define EOS_ID 38u
#define PAD_ID 39u
#define MASK_WORDS ((VOCAB_SIZE + 31u) / 32u)

static const char *const k_tokens[VOCAB_SIZE] = {
    /* 0..4 структурные */
    "{", "}", ",", ":", "\"",
    /* 5..7 короткие строки */
    "\"a\"", "\"b\"", "\"c\"",
    /* 8..11 ключи */
    "\"action\"", "\"action\":", "\"amount\"", "\"amount\":",
    /* 12..16 значения enum */
    "\"buy\"", "\"sell\"", "buy", "sell", "\":\"",
    /* 17..26 цифры */
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    /* 27..37 числа и прочие куски */
    "-", "10", "12", "{\"", "\"}", ", ", "true", "false", "null", " ", "amount",
    /* 38 EOS (пустые байты), 39 PAD (special) */
    "", "<pad>",
};

static int fail(const char *what, zg_status rc, const zg_error *err) {
    fprintf(stderr, "FAIL: %s -> status %d: %s\n", what, (int)rc, err->message);
    return 1;
}

#define CHECK(what, expr)                          \
    do {                                           \
        zg_status rc_ = (expr);                    \
        if (rc_ != ZG_OK) return fail(what, rc_, &err); \
    } while (0)

int main(void) {
    if (zg_abi_version() != ZG_ABI_VERSION) {
        fprintf(stderr, "FAIL: ABI version mismatch: %u\n", zg_abi_version());
        return 1;
    }

    static uint8_t blob[1024];
    static zg_token_entry entries[VOCAB_SIZE];
    size_t off = 0;
    for (uint32_t i = 0; i < VOCAB_SIZE; i++) {
        size_t len = strlen(k_tokens[i]);
        memcpy(blob + off, k_tokens[i], len);
        entries[i].id = i;
        entries[i].reserved = 0;
        entries[i].offset = off;
        entries[i].length = len;
        off += len;
    }

    const uint32_t eos_ids[] = {EOS_ID};
    const uint32_t special_ids[] = {PAD_ID};
    zg_tokenizer_desc tdesc;
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

    zg_context_config cfg;
    memset(&cfg, 0, sizeof(cfg));
    cfg.struct_size = sizeof(cfg);
    cfg.version = ZG_ABI_VERSION;
    cfg.mode = ZG_MODE_ADAPTIVE;
    cfg.cache_limit_bytes = ZG_CACHE_DEFAULT; /* 0 would disable the cache */

    zg_error err;
    memset(&err, 0, sizeof(err));
    err.struct_size = sizeof(err);

    zg_context *ctx = NULL;
    CHECK("zg_context_create", zg_context_create(&cfg, &tdesc, &ctx, &err));

    const char *schema_json =
        "{\"type\":\"object\","
        "\"properties\":{"
        "\"action\":{\"type\":\"string\",\"enum\":[\"buy\",\"sell\"]},"
        "\"amount\":{\"type\":\"integer\"}},"
        "\"required\":[\"action\",\"amount\"],"
        "\"additionalProperties\":false}";

    zg_compile_request req;
    memset(&req, 0, sizeof(req));
    req.struct_size = sizeof(req);
    req.kind = ZG_CONSTRAINT_JSON_SCHEMA;
    req.profile = "canonical-v1";
    req.data = (const uint8_t *)schema_json;
    req.data_len = strlen(schema_json);

    zg_grammar *gram = NULL;
    CHECK("zg_compile", zg_compile(ctx, &req, &gram, &err));

    zg_session *sess = NULL;
    CHECK("zg_session_create", zg_session_create(ctx, gram, &sess, &err));

    /* PAD (special non-EOS) обязан быть отклонён. */
    if (zg_accept_token(sess, PAD_ID, &err) != ZG_ERR_INVALID_TOKEN) {
        fprintf(stderr, "FAIL: PAD token was not rejected\n");
        return 1;
    }

    /* Трасса: { "action": "buy" , "amount": 1 } EOS */
    const uint32_t trace[] = {0u, 9u, 12u, 2u, 11u, 18u, 1u, EOS_ID};
    const size_t trace_len = sizeof(trace) / sizeof(trace[0]);

    uint32_t mask[MASK_WORDS];
    for (size_t step = 0; step < trace_len; step++) {
        CHECK("zg_fill_mask", zg_fill_mask(sess, mask, MASK_WORDS, &err));
        uint32_t want = trace[step];
        if (!((mask[want / 32u] >> (want % 32u)) & 1u)) {
            fprintf(stderr, "FAIL: token %u (\"%s\") not allowed at step %zu\n",
                    want, k_tokens[want], step);
            return 1;
        }
        CHECK("zg_accept_token", zg_accept_token(sess, want, &err));
    }

    bool can_end = false;
    CHECK("zg_can_end", zg_can_end(sess, &can_end));
    if (!can_end) {
        fprintf(stderr, "FAIL: can_end is false after full document\n");
        return 1;
    }
    CHECK("zg_finish", zg_finish(sess, &err));

    zg_stats sstats;
    memset(&sstats, 0, sizeof(sstats));
    CHECK("zg_get_stats_session", zg_get_stats_session(sess, &sstats));

    zg_session_destroy(sess);
    zg_grammar_release(gram);

    zg_stats cstats;
    memset(&cstats, 0, sizeof(cstats));
    CHECK("zg_get_stats", zg_get_stats(ctx, &cstats));

    printf("OK\n");
    printf("mask_calls=%llu tokens_accepted=%llu cache_hits=%llu cache_misses=%llu\n",
           (unsigned long long)cstats.mask_calls,
           (unsigned long long)cstats.tokens_accepted,
           (unsigned long long)cstats.cache_hits,
           (unsigned long long)cstats.cache_misses);
    printf("session: mask_ns=%llu accept_ns=%llu mem_session=%llu\n",
           (unsigned long long)sstats.mask_ns_total,
           (unsigned long long)sstats.accept_ns_total,
           (unsigned long long)sstats.mem_used[ZG_MEM_SESSION]);
    printf("mem_total=%llu mem_peak_total=%llu\n",
           (unsigned long long)cstats.mem_used[ZG_MEM_TOTAL],
           (unsigned long long)cstats.mem_peak[ZG_MEM_TOTAL]);

    CHECK("zg_context_destroy", zg_context_destroy(ctx));
    return 0;
}
