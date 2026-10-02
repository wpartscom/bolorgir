// Experimental compile-time precompute (FR-9): bounded BFS over parser
// states reachable from the initial state, computing and caching masks
// ahead of time. Full enumeration is not required; exhausting the state
// budget, the temp memory or the work budget simply stops the walk and
// generation continues lazily. Only an explicit cancel signal propagates.

const std = @import("std");
const grammar = @import("grammar.zig");
const tokenizer = @import("tokenizer.zig");
const parser = @import("parser.zig");
const mask = @import("mask.zig");
const cache = @import("cache.zig");
const work_mod = @import("work.zig");

pub const Error = error{Cancelled};

// ---------------------------------------------------------------------------
// ADR-0007 Decision 1: precomputed token classification for the
// string-content fast class. Everything here is derived once per tokenizer
// from the decoded token byte images (tokenizer identity therefore covers
// it, ADR-0003); no per-state parsing is involved.
// ---------------------------------------------------------------------------

/// cp_len value marking a token that is NOT pure string content; such
/// tokens always stay in the exact class.
pub const EXACT: u16 = std.math.maxInt(u16);

/// Code-point count of a pure string-content token, or EXACT when the
/// token is not pure string content. Pure string content (ADR-0007): the
/// bytes form complete valid UTF-8 code points (strict: no overlongs, no
/// surrogates, <= U+10FFFF - the same acceptance as parser strFeed in
/// .normal with rem == 0) and contain no '"' (0x22), no '\' (0x5C) and no
/// byte < 0x20. Equivalence lemma: in a uniform string-content state such a
/// token is allowed iff its code-point count does not exceed the residual
/// length budget of the top str frame.
pub fn cpPureLen(bytes: []const u8) u16 {
    var cp: u16 = 0;
    var i: usize = 0;
    while (i < bytes.len) {
        const b = bytes[i];
        if (b < 0x80) {
            if (b < 0x20 or b == '"' or b == '\\') return EXACT;
            cp +|= 1;
            if (cp == EXACT) return EXACT;
            i += 1;
            continue;
        }
        var rem: u8 = undefined;
        var lo: u8 = undefined;
        var hi: u8 = undefined;
        if (b >= 0xC2 and b <= 0xDF) {
            rem = 1;
            lo = 0x80;
            hi = 0xBF;
        } else if (b == 0xE0) {
            rem = 2;
            lo = 0xA0;
            hi = 0xBF;
        } else if ((b >= 0xE1 and b <= 0xEC) or b == 0xEE or b == 0xEF) {
            rem = 2;
            lo = 0x80;
            hi = 0xBF;
        } else if (b == 0xED) {
            rem = 2;
            lo = 0x80;
            hi = 0x9F;
        } else if (b == 0xF0) {
            rem = 3;
            lo = 0x90;
            hi = 0xBF;
        } else if (b >= 0xF1 and b <= 0xF3) {
            rem = 3;
            lo = 0x80;
            hi = 0xBF;
        } else if (b == 0xF4) {
            rem = 3;
            lo = 0x80;
            hi = 0x8F;
        } else {
            return EXACT;
        }
        i += 1;
        while (rem > 0) {
            if (i >= bytes.len) return EXACT;
            const cb = bytes[i];
            if (cb < lo or cb > hi) return EXACT;
            rem -= 1;
            if (rem > 0) {
                lo = 0x80;
                hi = 0xBF;
            }
            i += 1;
        }
        cp +|= 1;
        if (cp == EXACT) return EXACT;
    }
    return cp;
}

/// Per-tokenizer fast-path tables (ADR-0007 Decision 3 item 3): all
/// per-token classification is precomputed at tokenizer load into flat
/// arrays and word bitsets, so the string-content mask of a uniform state
/// is a memcpy or a handful of word ops over vocab/32 words.
pub const FastData = struct {
    a: std.mem.Allocator,
    /// Per token id: code-point count for pure-content tokens, EXACT for
    /// the exact class. EOS and special tokens are always EXACT.
    cp_len: []u16,
    /// Largest cp_len among pure-content tokens (0 when there are none).
    cp_max: u32,
    /// Mask bitset (tok.maskWords() words) of every pure-content token id.
    content_bits: []u32,
    /// Bitset over trie nodes: the subtree of node n contains at least one
    /// terminal whose token chain is exact-class. The fast exact walk
    /// descends only into flagged subtrees; unflagged subtrees consist
    /// entirely of pure-content tokens already decided by the lemma.
    exact_subtree: []u32,
    /// Number of exact-class (non pure-content) regular tokens.
    n_exact: u32,
    /// Residual-length budget at which a uniform string-content state's
    /// mask becomes position-independent: feeding any single token consumes
    /// at most this many count units (a token is at most r_cap bytes and
    /// the count never grows by more than the fed byte count), and
    /// r_cap >= cp_max, so at R >= r_cap every content token passes the
    /// length check and every exact token feeds as it would at R = inf.
    r_cap: u32,

    pub fn build(a: std.mem.Allocator, tok: *const tokenizer.Tokenizer) error{OutOfMemory}!FastData {
        const vocab = tok.vocab_size;
        const mw = tok.maskWords();
        var cp_len = try a.alloc(u16, vocab);
        errdefer a.free(cp_len);
        var content_bits = try a.alloc(u32, mw);
        errdefer a.free(content_bits);
        @memset(content_bits, 0);

        var cp_max: u32 = 0;
        var n_exact: u32 = 0;
        var max_tok_bytes: u32 = 0;
        var id: u32 = 0;
        while (id < vocab) : (id += 1) {
            var cp: u16 = EXACT;
            if (!tok.is_eos[id] and !tok.is_special[id] and tok.bytes[id].len > 0) {
                cp = cpPureLen(tok.bytes[id]);
                if (tok.bytes[id].len > max_tok_bytes) max_tok_bytes = @intCast(tok.bytes[id].len);
            }
            cp_len[id] = cp;
            if (cp == EXACT) {
                if (!tok.is_eos[id] and !tok.is_special[id]) n_exact += 1;
            } else {
                content_bits[id / 32] |= @as(u32, 1) << @as(u5, @intCast(id % 32));
                if (cp > cp_max) cp_max = cp;
            }
        }

        const trie = &tok.trie;
        const n_nodes = trie.node_token.len;
        const n_words = (n_nodes + 31) / 32;
        var exact_subtree = try a.alloc(u32, n_words);
        errdefer a.free(exact_subtree);
        @memset(exact_subtree, 0);
        if (n_nodes > 0) {
            // flag[node] = subtree contains an exact-class terminal.
            var flag = try a.alloc(bool, n_nodes);
            defer a.free(flag);
            @memset(flag, false);
            var parent = try a.alloc(u32, n_nodes);
            defer a.free(parent);
            var order = try std.ArrayListUnmanaged(u32).initCapacity(a, n_nodes);
            defer order.deinit(a);
            parent[0] = 0;
            order.appendAssumeCapacity(0);
            var top: usize = 0;
            // Iterative DFS; order records parents before their children.
            while (top < order.items.len) : (top += 1) {
                const node = order.items[top];
                const t = trie.node_token[node];
                if (t != tokenizer.NO_TOKEN and cp_len[t] == EXACT) flag[node] = true;
                const off = trie.node_edge_off[node];
                var ei: u32 = 0;
                while (ei < trie.node_edge_len[node]) : (ei += 1) {
                    const child = trie.edge_child[off + ei];
                    parent[child] = node;
                    order.appendAssumeCapacity(child);
                }
            }
            // Reverse order: children before parents, propagate upwards.
            var ri: usize = order.items.len;
            while (ri > 0) {
                ri -= 1;
                const node = order.items[ri];
                if (node != 0 and flag[node] and !flag[parent[node]]) flag[parent[node]] = true;
            }
            for (flag, 0..) |f, n| {
                if (f) exact_subtree[n / 32] |= @as(u32, 1) << @as(u5, @intCast(n % 32));
            }
        }

        return .{
            .a = a,
            .cp_len = cp_len,
            .cp_max = cp_max,
            .content_bits = content_bits,
            .exact_subtree = exact_subtree,
            .n_exact = n_exact,
            .r_cap = @max(cp_max, max_tok_bytes),
        };
    }

    pub fn deinit(self: *FastData) void {
        self.a.free(self.cp_len);
        self.a.free(self.content_bits);
        self.a.free(self.exact_subtree);
        self.* = undefined;
    }

    /// True when the subtree of trie `node` may contain exact-class tokens.
    pub fn subtreeHasExact(self: *const FastData, node: u32) bool {
        return (self.exact_subtree[node / 32] >> @as(u5, @intCast(node % 32))) & 1 == 1;
    }
};

/// Returns the number of states whose masks were computed and offered to
/// the cache. `mu` serializes access to `c` (cache is not internally
/// synchronized). Work is charged to `w` together with the rest of compile.
pub fn run(
    tmp_a: std.mem.Allocator,
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    c: *cache.Cache,
    mu: *std.Thread.Mutex,
    max_threads: u16,
    max_states: u64,
    w: *work_mod.Work,
) Error!u64 {
    var arena = std.heap.ArenaAllocator.init(tmp_a);
    defer arena.deinit();
    const a = arena.allocator();

    const mask_buf = a.alloc(u32, tok.maskWords()) catch return 0;
    var buf = mask.MaskBuf.init(a, a);

    var visited: std.AutoHashMapUnmanaged(u64, void) = .{};
    var queue: std.ArrayListUnmanaged(parser.State) = .{};

    var side = parser.Side.init(a);
    defer side.deinit();

    const st0 = parser.initState(g, max_threads, &side) catch return 0;
    queue.append(a, st0) catch return 0;
    visited.put(a, parser.hashState(&st0, &side).hash, {}) catch return 0;

    var count: u64 = 0;
    var qi: usize = 0;
    const work = a.create(parser.State) catch return 0;
    walk: while (qi < queue.items.len and count < max_states) : (qi += 1) {
        w.charge(1) catch |e| switch (e) {
            error.Cancelled => return error.Cancelled,
            error.ResourceLimit => break,
        };
        const cur = queue.items[qi];
        mask.fillMask(g, tok, &cur, mask_buf, w, &buf, &side) catch |e| switch (e) {
            error.Cancelled => return error.Cancelled,
            error.DeadEnd => continue, // unreachable for covered grammars
            error.ResourceLimit, error.OutOfMemory => break,
        };
        count += 1;
        const key: cache.Key = .{
            .grammar_id = g.id,
            .grammar_hi = g.id_hi,
            .tokenizer_id = tok.identity,
            .state_hash = parser.hashState(&cur, &side).hash,
        };
        const bl = a.alloc(u8, parser.chunkBlobLen(&cur, &side)) catch break :walk;
        parser.writeChunkBlob(&cur, &side, bl);
        mu.lock();
        c.put(key, &cur, bl, mask_buf);
        mu.unlock();
        var id: u32 = 0;
        while (id < tok.vocab_size) : (id += 1) {
            if ((mask_buf[id / 32] >> @as(u5, @intCast(id % 32))) & 1 == 0) continue;
            if (tok.is_eos[id]) continue;
            var next: parser.State = undefined;
            parser.feedBytes(g, &side, &cur, tok.bytes[id], &next, work) catch continue;
            const h = parser.hashState(&next, &side).hash;
            const gop = visited.getOrPut(a, h) catch break :walk;
            if (gop.found_existing) continue;
            queue.append(a, next) catch break :walk;
        }
    }
    return count;
}

// --- ADR-0007 fast-path classification tests ---

const testing = std.testing;

test "fastpath: cpPureLen pure content and code-point counting" {
    try testing.expectEqual(@as(u16, 0), cpPureLen(""));
    try testing.expectEqual(@as(u16, 3), cpPureLen("abc"));
    try testing.expectEqual(@as(u16, 1), cpPureLen(" "));
    try testing.expectEqual(@as(u16, 1), cpPureLen("\x7f")); // DEL is content
    try testing.expectEqual(@as(u16, 1), cpPureLen("\xc3\xa9")); // é
    try testing.expectEqual(@as(u16, 1), cpPureLen("\xe4\xb8\xad")); // 中
    try testing.expectEqual(@as(u16, 1), cpPureLen("\xf0\x9f\x98\x80")); // emoji
    try testing.expectEqual(@as(u16, 3), cpPureLen("a\xc3\xa9z"));
    // Multi-code-point token: counted in code points, not bytes.
    try testing.expectEqual(@as(u16, 2), cpPureLen("\xc3\xa9\xc3\xa9"));
}

test "fastpath: cpPureLen rejects quotes, backslashes and controls" {
    try testing.expectEqual(EXACT, cpPureLen("\""));
    try testing.expectEqual(EXACT, cpPureLen("a\"b"));
    try testing.expectEqual(EXACT, cpPureLen("\\"));
    try testing.expectEqual(EXACT, cpPureLen("\\\\")); // two backslashes
    try testing.expectEqual(EXACT, cpPureLen("\\u00")); // escape pieces
    try testing.expectEqual(EXACT, cpPureLen("\n"));
    try testing.expectEqual(EXACT, cpPureLen("\t"));
    try testing.expectEqual(EXACT, cpPureLen("\x1f"));
    try testing.expectEqual(EXACT, cpPureLen("a\x00b"));
}

test "fastpath: cpPureLen strict UTF-8 traps" {
    try testing.expectEqual(EXACT, cpPureLen("\xc3")); // truncated lead
    try testing.expectEqual(EXACT, cpPureLen("a\xc3")); // truncated at end
    try testing.expectEqual(EXACT, cpPureLen("\x80")); // lone continuation
    try testing.expectEqual(EXACT, cpPureLen("\xc0\x80")); // overlong
    try testing.expectEqual(EXACT, cpPureLen("\xc1\xbf")); // overlong
    try testing.expectEqual(EXACT, cpPureLen("\xc2\x20")); // bad continuation
    try testing.expectEqual(EXACT, cpPureLen("\xe0\x80\x80")); // overlong 3-byte
    try testing.expectEqual(EXACT, cpPureLen("\xe0\xa0")); // truncated 3-byte
    try testing.expectEqual(@as(u16, 1), cpPureLen("\xe0\xa0\x80")); // U+0800 ok
    try testing.expectEqual(EXACT, cpPureLen("\xed\xa0\x80")); // surrogate
    try testing.expectEqual(EXACT, cpPureLen("\xed\xbf\xbf")); // surrogate
    try testing.expectEqual(@as(u16, 1), cpPureLen("\xed\x9f\xbf")); // U+D7FF ok
    try testing.expectEqual(EXACT, cpPureLen("\xf0\x80\x80\x80")); // overlong 4-byte
    try testing.expectEqual(@as(u16, 1), cpPureLen("\xf0\x90\x80\x80")); // U+10000 ok
    try testing.expectEqual(EXACT, cpPureLen("\xf4\x90\x80\x80")); // > U+10FFFF
    try testing.expectEqual(@as(u16, 1), cpPureLen("\xf4\x8f\xbf\xbf")); // U+10FFFF ok
    try testing.expectEqual(EXACT, cpPureLen("\xf5\x80\x80\x80")); // invalid lead
    try testing.expectEqual(EXACT, cpPureLen("\xe4\xb8\xad\x22")); // valid + quote
}

test "fastpath: FastData build on a handcrafted vocabulary" {
    const alloc = testing.allocator;
    // 0: "ab" content; 1: "abc" content; 2: "a\n" exact; 3: ""; 4: "\""
    // exact; 5: "ab" duplicate of 0 (content); 6: "zz" special.
    const entries = [_]tokenizer.Entry{
        .{ .id = 0, .bytes = "ab" },
        .{ .id = 1, .bytes = "abc" },
        .{ .id = 2, .bytes = "a\n" },
        .{ .id = 3, .bytes = "" },
        .{ .id = 4, .bytes = "\"" },
        .{ .id = 5, .bytes = "ab" },
        .{ .id = 6, .bytes = "zz" },
    };
    var tok = try tokenizer.Tokenizer.create(alloc, 7, &entries, &[_]u32{3}, &[_]u32{6});
    defer tok.deinit();
    var fd = try FastData.build(alloc, &tok);
    defer fd.deinit();

    try testing.expectEqual(@as(u16, 2), fd.cp_len[0]);
    try testing.expectEqual(@as(u16, 3), fd.cp_len[1]);
    try testing.expectEqual(EXACT, fd.cp_len[2]);
    try testing.expectEqual(EXACT, fd.cp_len[3]); // eos
    try testing.expectEqual(EXACT, fd.cp_len[4]);
    try testing.expectEqual(@as(u16, 2), fd.cp_len[5]);
    try testing.expectEqual(EXACT, fd.cp_len[6]); // special
    try testing.expectEqual(@as(u32, 3), fd.cp_max);
    try testing.expectEqual(@as(u32, 2), fd.n_exact); // ids 2 and 4
    // content bits: ids 0, 1, 5 only.
    try testing.expectEqual(@as(u32, (1 << 0) | (1 << 1) | (1 << 5)), fd.content_bits[0]);

    // Trie: root -> 'a' -> 'b' -> 'c'; root -> '"'; root -> (nothing else).
    // 'a' node subtree contains "a\n" (exact), so root/'a' are flagged; the
    // 'b'->'c' chain is pure content, unflagged; '"' node flagged.
    const tr = &tok.trie;
    const na = tr.child(0, 'a').?;
    const nab = tr.child(na, 'b').?;
    const nabc = tr.child(nab, 'c').?;
    const na_nl = tr.child(na, '\n').?;
    const nq = tr.child(0, '"').?;
    try testing.expect(fd.subtreeHasExact(0));
    try testing.expect(fd.subtreeHasExact(na));
    try testing.expect(!fd.subtreeHasExact(nab)); // "ab"/"abc" are content
    try testing.expect(!fd.subtreeHasExact(nabc));
    try testing.expect(fd.subtreeHasExact(na_nl));
    try testing.expect(fd.subtreeHasExact(nq));
}
