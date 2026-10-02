const std = @import("std");
const grammar = @import("grammar.zig");
const tokenizer = @import("tokenizer.zig");
const parser = @import("parser.zig");
const complete = @import("complete.zig");
const work_mod = @import("work.zig");
const cache_mod = @import("cache.zig");
const precompute = @import("precompute.zig");

pub const Error = error{ DeadEnd, ResourceLimit, Cancelled, OutOfMemory };

const StackFrame = struct { node: u32, next_edge: u32 };

/// Mask-call scratch for the trie walk: one parser state per branching
/// trie level plus a spare for chain descent. Allocated lazily on the first
/// fill and reused across mask calls. Owned by the session but charged to
/// the context temp budget (see init): the pool is transient compute state,
/// and at MAX_THREADS_CAP 128 it runs ~256 KiB × max_stack, which would by
/// itself exhaust the 8 MiB default session budget.
pub const MaskBuf = struct {
    a: std.mem.Allocator,
    states: std.ArrayListUnmanaged(parser.State) = .{},
    frames: std.ArrayListUnmanaged(StackFrame) = .{},
    spare: parser.State = undefined,
    blob: std.ArrayListUnmanaged(u8) = .{},
    completion: complete.Cache,

    pub fn init(a: std.mem.Allocator, completion_a: std.mem.Allocator) MaskBuf {
        // The completion search scratch (one ~256 KB parser.State per
        // search depth, plus memoized state images) is transient compute
        // state like the precompute warm-up: it is charged to the context
        // temp budget, not the tight per-session one (8 MiB default fits
        // barely 30 pool states, which would cap every witness at ~30
        // tokens of depth).
        var self: MaskBuf = .{ .a = a, .completion = complete.Cache.init(completion_a) };
        self.spare.n = 0;
        return self;
    }

    pub fn deinit(self: *MaskBuf) void {
        self.states.deinit(self.a);
        self.frames.deinit(self.a);
        self.blob.deinit(self.a);
        self.completion.deinit();
        self.* = undefined;
    }

    fn ensure(self: *MaskBuf, stack: u32) !void {
        try self.states.ensureTotalCapacity(self.a, @as(usize, stack) + 1);
        try self.frames.ensureTotalCapacity(self.a, @as(usize, stack) + 1);
    }
};

/// Exact mask construction over the token trie (FR-6). Iterative DFS: each
/// trie edge feeds one byte to the parser; a Parse error prunes the whole
/// subtree, a terminal node sets the bit of every chained token id. A node
/// with a single edge descends in place, so the stack depth is the branching
/// depth, not the token length. Semantics match the former linear scan: EOS
/// by can_end, special tokens skipped, ResourceLimit from a feed or the
/// work budget fails the mask, DeadEnd when nothing is allowed. On a
/// vocabulary without full byte coverage and a finite literal grammar
/// (TZ 3.1), and on any vocabulary for a grammar with deferred value
/// verdicts (grammar.needs_reachability, ADR-0005), a token whose state
/// cannot reach a completed answer is filtered out (src/complete.zig); a
/// reachability check that exhausts its budget/depth/memory is UNKNOWN, not
/// alive, and fails the whole mask call with ResourceLimit (ADR-0005 D3).
pub fn fillMask(g: *const grammar.Grammar, tok: *const tokenizer.Tokenizer, st: *const parser.State, out: []u32, w: *work_mod.Work, buf: *MaskBuf, side: *parser.Side) Error!void {
    const mw = tok.maskWords();
    std.debug.assert(out.len >= mw);
    @memset(out[0..mw], 0);

    // Completion filter (TZ 3.1; ADR-0005): partial vocabularies over
    // finite literal languages, and any grammar with deferred value
    // verdicts (needs_reachability) on any vocabulary.
    const filtered = (!tok.byte_complete and g.finite_literal) or g.needs_reachability;
    if (g.needs_reachability) {
        buf.completion.reset();
        buf.completion.fill_budget = complete.FILL_SEARCH_BUDGET;
    }
    const trie = &tok.trie;
    try buf.ensure(trie.max_stack);
    buf.states.items.len = 0;
    buf.frames.items.len = 0;
    buf.states.appendAssumeCapacity(st.*);
    parser.retainState(side, &buf.states.items[0]);
    defer {
        for (buf.states.items) |*x| parser.releaseState(side, x);
        buf.states.items.len = 0;
        buf.frames.items.len = 0;
        parser.releaseState(side, &buf.spare);
    }
    buf.frames.appendAssumeCapacity(.{ .node = 0, .next_edge = 0 });

    var any = false;
    {
        var id = trie.node_token[0];
        while (id != tokenizer.NO_TOKEN) : (id = tok.token_chain[id]) {
            setBit(out, id);
            any = true;
        }
    }
    while (buf.frames.items.len > 0) {
        try w.charge(1);
        const top = buf.frames.items.len - 1;
        const fr = &buf.frames.items[top];
        const node = fr.node;
        if (fr.next_edge >= trie.node_edge_len[node]) {
            _ = buf.frames.pop();
            while (buf.states.items.len > buf.frames.items.len) {
                parser.releaseState(side, &buf.states.items[buf.states.items.len - 1]);
                buf.states.items.len -= 1;
            }
            continue;
        }
        const ei = trie.node_edge_off[node] + fr.next_edge;
        fr.next_edge += 1;
        const child = trie.edge_child[ei];
        if (trie.node_edge_len[node] == 1) {
            // Single edge: descend in place, the parent state is no longer
            // needed; feed through the spare so a failed feed leaves the
            // current state intact for the prune path.
            parser.feedBytes(g, side, &buf.states.items[top], &[1]u8{trie.edge_byte[ei]}, &buf.spare, &buf.states.allocatedSlice()[top + 1]) catch |err| switch (err) {
                error.Parse => continue, // prune the subtree
                else => |e| return e,
            };
            parser.releaseState(side, &buf.states.items[top]);
            buf.states.items[top] = buf.spare;
            buf.spare.n = 0;
            fr.* = .{ .node = child, .next_edge = 0 };
            try setNodeTokens(g, tok, filtered, &buf.states.items[top], child, out, &any, w, buf, side);
        } else {
            const dst = &buf.states.allocatedSlice()[top + 1];
            parser.feedBytes(g, side, &buf.states.items[top], &[1]u8{trie.edge_byte[ei]}, dst, &buf.spare) catch |err| switch (err) {
                error.Parse => continue, // prune the subtree
                else => |e| return e,
            };
            buf.states.items.len = top + 2;
            buf.frames.appendAssumeCapacity(.{ .node = child, .next_edge = 0 });
            try setNodeTokens(g, tok, filtered, &buf.states.items[top + 1], child, out, &any, w, buf, side);
        }
    }

    if (parser.canEnd(g, st)) {
        var id: u32 = 0;
        while (id < tok.vocab_size) : (id += 1) {
            if (tok.is_eos[id]) {
                setBit(out, id);
                any = true;
            }
        }
    }
    if (!any) return error.DeadEnd;
}

fn setBit(out: []u32, id: u32) void {
    out[id / 32] |= @as(u32, 1) << @as(u5, @intCast(id % 32));
}

// ---------------------------------------------------------------------------
// ADR-0007 Decision 1: the string-content fast class.
// ---------------------------------------------------------------------------

/// State condition of the string-content equivalence lemma: S is a uniform
/// string-content state when every live thread has a top frame `.str` with
/// `state == .normal` and `rem == 0` (inside an open string, not
/// mid-escape, not mid-codepoint). Returns the maximum residual length
/// budget R = max_len - count over the live threads (a token is allowed
/// when ANY thread accepts it and acceptance is monotone in R), or null
/// when the state is not uniform. O(live threads).
pub fn uniformStringResidual(g: *const grammar.Grammar, st: *const parser.State) ?u32 {
    if (st.n == 0) return null;
    var r_max: u32 = 0;
    for (st.threads[0..st.n]) |*t| {
        if (t.len == 0) return null;
        const f = &t.frames[t.len - 1];
        switch (f.*) {
            .str => |sf| {
                if (sf.state != .normal or sf.rem != 0) return null;
                const sc = g.node(sf.node).str;
                r_max = @max(r_max, sc.max_len - sf.count);
            },
            else => return null,
        }
    }
    return r_max;
}

/// Normal form of a uniform string-content state for the mask cache
/// (ADR-0007 D2+): copies S clamping the str-frame `count` of every live
/// thread at the node's min_len (state is already .normal and rem == 0 -
/// checked by uniformStringResidual). When R(S) >= FastData.r_cap the mask
/// of S equals the mask of its normal form: count gates the max_len
/// budget, and at R >= r_cap no single token's feed can cross it, while
/// the content class is fully allowed (r_cap >= cp_max); the only other
/// count dependence is the minLength close gate (parser.strFeed refuses
/// the closing quote below min_len), and clamping preserves it exactly:
/// count < min_len is kept verbatim, count >= min_len collapses to
/// min_len, and after any shared token spelling of k more characters the
/// two sides agree on count + k >= min_len again. The copy RETAINS the
/// chunk handles of lower frames: `out` owns its references like any
/// live state and must be released with parser.releaseState when done.
pub fn normalizeUniformString(g: *const grammar.Grammar, st: *const parser.State, side: *parser.Side, out: *parser.State) void {
    out.n = st.n;
    out.max_threads = st.max_threads;
    for (st.threads[0..st.n], 0..) |*t, i| {
        const dt = &out.threads[i];
        dt.len = t.len;
        dt.tap_count = t.tap_count;
        @memcpy(dt.frames[0..t.len], t.frames[0..t.len]);
        const sf = &dt.frames[dt.len - 1].str;
        sf.count = @min(sf.count, g.node(sf.node).str.min_len);
    }
    parser.retainState(side, out);
}

/// Fast-path mask construction (ADR-0007). Returns false when the state is
/// not covered by a proven lemma (the caller then runs the exact
/// `fillMask`); otherwise fills `out` bit-for-bit identically to
/// `fillMask` and returns true. The mask is split into token classes:
/// pure-content tokens are decided by the equivalence lemma
/// (cp(t) <= R(S)) via precomputed word bitsets, every other token by the
/// plain trie walk pruned to subtrees that may contain exact-class tokens.
/// `wc`/`mu` give access to the widened (equivalence-class) cache of
/// Decision 2; `max_workers` > 1 splits the classification over disjoint
/// word ranges (determinism is contractual: same bits for any worker
/// count).
pub fn fillMaskFast(
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    st: *const parser.State,
    out: []u32,
    w: *work_mod.Work,
    buf: *MaskBuf,
    side: *parser.Side,
    fast: *const precompute.FastData,
    wc: ?*cache_mod.Cache,
    mu: ?*std.Thread.Mutex,
    max_workers: u32,
) Error!bool {
    const r_max = uniformStringResidual(g, st) orelse return false;
    // The completion filter (TZ 3.1) needs per-token parser states, which
    // the lemma does not provide; such pairs stay on the exact walk. (A
    // uniform string-content state implies a str node, hence
    // !g.finite_literal - this is a guard, not a load-bearing check.) The
    // same holds for grammars with deferred value verdicts (ADR-0005):
    // the widened-content lemma knows nothing of boundary verdicts.
    if ((!tok.byte_complete and g.finite_literal) or g.needs_reachability) return false;

    const mw = tok.maskWords();
    std.debug.assert(out.len >= mw);

    // Content class: allowed iff cp(t) <= R (equivalence lemma). The class
    // bits depend only on R_capped = min(R, CP_MAX + 1), the lemma's
    // equivalence-class descriptor, so they are cacheable under it
    // (ADR-0007 D2); the exact-class bits of the same state are never
    // stored under the widened key.
    const r_cap: u64 = @min(@as(u64, r_max), @as(u64, fast.cp_max) + 1);
    var hit = false;
    if (wc) |c| {
        const wk: cache_mod.WKey = .{
            .grammar_id = g.id,
            .grammar_hi = g.id_hi,
            .tokenizer_id = tok.identity,
            .r_capped = r_cap,
        };
        if (mu) |m| m.lock();
        hit = c.getWidened(wk, out[0..mw]);
        if (mu) |m| m.unlock();
        if (!hit) {
            computeContentMask(tok, fast, r_max, out[0..mw], max_workers);
            if (mu) |m| m.lock();
            c.putWidened(wk, out[0..mw]);
            if (mu) |m| m.unlock();
        }
    } else {
        computeContentMask(tok, fast, r_max, out[0..mw], max_workers);
    }
    // The classification is charged once, identically on a widened hit, a
    // miss and for any worker count: the work accounting depends on the
    // state and the vocabulary only.
    try w.charge(@intCast(mw));
    var any = anyWords(out[0..mw]);

    // EOS is excluded: a str top frame can never end (threadCanEnd returns
    // false on it), so canEnd(S) is false in a uniform state.
    // Exact class: the plain walk, pruned to subtrees that may contain
    // exact-class tokens; everything else was decided by the lemma.
    try fillMaskExactPruned(g, tok, st, out, w, buf, side, fast, &any);
    if (!any) return error.DeadEnd;
    return true;
}

fn anyWords(words: []const u32) bool {
    for (words) |x| {
        if (x != 0) return true;
    }
    return false;
}

/// Content-class bits: token id allowed iff pure content and
/// cp(t) <= r_max. With r_max >= CP_MAX this is the precomputed
/// content_bits memcpy; otherwise a word loop (~vocab/32 word builds).
fn computeContentMask(tok: *const tokenizer.Tokenizer, fast: *const precompute.FastData, r_max: u32, out: []u32, max_workers: u32) void {
    const mw = tok.maskWords();
    if (r_max >= fast.cp_max) {
        @memcpy(out[0..mw], fast.content_bits);
        return;
    }
    if (max_workers > 1 and mw >= 256) {
        contentWordsParallel(tok, fast, r_max, out, max_workers);
        return;
    }
    contentWords(tok, fast, r_max, out, 0, mw);
}

fn contentWords(tok: *const tokenizer.Tokenizer, fast: *const precompute.FastData, r_max: u32, out: []u32, from: usize, to: usize) void {
    var wi = from;
    while (wi < to) : (wi += 1) {
        var word: u32 = 0;
        const base = wi * 32;
        var b: u32 = 0;
        while (b < 32) : (b += 1) {
            const id: u32 = @intCast(base + b);
            if (id >= tok.vocab_size) break;
            if (fast.cp_len[id] <= r_max) word |= @as(u32, 1) << @as(u5, @intCast(b));
        }
        out[wi] = word;
    }
}

/// Workers reduce over disjoint token-id (word) ranges; there is no
/// combination step, so the result is bit-for-bit independent of the
/// worker count (ADR-0007 D5). A spawn failure falls back to inline
/// computation of the remaining ranges.
fn contentWordsParallel(tok: *const tokenizer.Tokenizer, fast: *const precompute.FastData, r_max: u32, out: []u32, max_workers: u32) void {
    const mw = tok.maskWords();
    const n: usize = @intCast(@min(@as(u32, @intCast(mw)), max_workers));
    const chunk = (mw + n - 1) / n;
    var handles: [parser.MAX_THREADS_CAP]?std.Thread = .{null} ** parser.MAX_THREADS_CAP;
    var spawned: usize = 0;
    var range: usize = 0;
    while (range + 1 < n and spawned < handles.len) : (range += 1) {
        const from = range * chunk;
        const to = @min(from + chunk, mw);
        handles[spawned] = std.Thread.spawn(.{}, contentWords, .{ tok, fast, r_max, out, from, to }) catch break;
        spawned += 1;
    }
    while (range < n) : (range += 1) {
        const from = range * chunk;
        contentWords(tok, fast, r_max, out, from, @min(from + chunk, mw));
    }
    for (handles[0..spawned]) |h| h.?.join();
}

/// The exact class of the fast path: the plain trie walk of `fillMask`,
/// pruned to subtrees flagged by `fast.exact_subtree` (subtrees consisting
/// entirely of pure-content tokens are already decided by the lemma) and
/// setting only exact-class token ids at terminals. Parse pruning, work
/// charging and error propagation are identical to `fillMask` for every
/// node it visits.
fn fillMaskExactPruned(
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    st: *const parser.State,
    out: []u32,
    w: *work_mod.Work,
    buf: *MaskBuf,
    side: *parser.Side,
    fast: *const precompute.FastData,
    any: *bool,
) Error!void {
    const trie = &tok.trie;
    try buf.ensure(trie.max_stack);
    buf.states.items.len = 0;
    buf.frames.items.len = 0;
    buf.states.appendAssumeCapacity(st.*);
    parser.retainState(side, &buf.states.items[0]);
    defer {
        for (buf.states.items) |*x| parser.releaseState(side, x);
        buf.states.items.len = 0;
        buf.frames.items.len = 0;
        parser.releaseState(side, &buf.spare);
    }
    buf.frames.appendAssumeCapacity(.{ .node = 0, .next_edge = 0 });

    setExactTokens(tok, fast, 0, out, any);
    while (buf.frames.items.len > 0) {
        try w.charge(1);
        const top = buf.frames.items.len - 1;
        const fr = &buf.frames.items[top];
        const node = fr.node;
        if (fr.next_edge >= trie.node_edge_len[node]) {
            _ = buf.frames.pop();
            while (buf.states.items.len > buf.frames.items.len) {
                parser.releaseState(side, &buf.states.items[buf.states.items.len - 1]);
                buf.states.items.len -= 1;
            }
            continue;
        }
        const ei = trie.node_edge_off[node] + fr.next_edge;
        fr.next_edge += 1;
        const child = trie.edge_child[ei];
        if (!fast.subtreeHasExact(child)) continue; // pure-content subtree: the lemma decided it
        if (trie.node_edge_len[node] == 1) {
            parser.feedBytes(g, side, &buf.states.items[top], &[1]u8{trie.edge_byte[ei]}, &buf.spare, &buf.states.allocatedSlice()[top + 1]) catch |err| switch (err) {
                error.Parse => continue, // prune the subtree
                else => |e| return e,
            };
            parser.releaseState(side, &buf.states.items[top]);
            buf.states.items[top] = buf.spare;
            buf.spare.n = 0;
            fr.* = .{ .node = child, .next_edge = 0 };
            setExactTokens(tok, fast, child, out, any);
        } else {
            const dst = &buf.states.allocatedSlice()[top + 1];
            parser.feedBytes(g, side, &buf.states.items[top], &[1]u8{trie.edge_byte[ei]}, dst, &buf.spare) catch |err| switch (err) {
                error.Parse => continue, // prune the subtree
                else => |e| return e,
            };
            buf.states.items.len = top + 2;
            buf.frames.appendAssumeCapacity(.{ .node = child, .next_edge = 0 });
            setExactTokens(tok, fast, child, out, any);
        }
    }
}

/// Sets the bits of the exact-class ids of the token chain at `node`.
/// Content-class ids of the chain are skipped: the lemma already decided
/// them (a chain shares one byte image, hence one class, but the per-id
/// check keeps this independent of that invariant).
fn setExactTokens(tok: *const tokenizer.Tokenizer, fast: *const precompute.FastData, node: u32, out: []u32, any: *bool) void {
    var id = tok.trie.node_token[node];
    while (id != tokenizer.NO_TOKEN) : (id = tok.token_chain[id]) {
        if (fast.cp_len[id] == precompute.EXACT) {
            setBit(out, id);
            any.* = true;
        }
    }
}

/// Sets the mask bits of the token chain at `child`; with the completion
/// filter active (TZ 3.1) a token whose state cannot reach a completed
/// answer is skipped instead.
fn setNodeTokens(
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    filtered: bool,
    cur: *const parser.State,
    child: u32,
    out: []u32,
    any: *bool,
    w: *work_mod.Work,
    buf: *MaskBuf,
    side: *parser.Side,
) Error!void {
    // A budget-exhausted reachability search (ResourceLimit) is UNKNOWN, not
    // alive: ADR-0005 Decision 3 fails the whole mask call instead of
    // silently allowing a token that may dead-end (the permissive fallback
    // let dead bytes through, e.g. oneOf "^(ab|c)$"/"^(ab|d)$" admitted 'b'
    // after the prefix `"a` in every mode).
    if (filtered) {
        const alive = complete.stateAlive(g, tok, cur, &buf.completion, w, side) catch |err| switch (err) {
            error.ResourceLimit => return error.ResourceLimit,
            else => |e| return e,
        };
        if (!alive) return;
    }
    var id = tok.trie.node_token[child];
    while (id != tokenizer.NO_TOKEN) : (id = tok.token_chain[id]) {
        setBit(out, id);
        any.* = true;
    }
}

// Reference linear scan kept for equivalence tests only: every non-special
// token is fed whole, the first-byte filter is a pure optimization. It
// applies the same completion filter as fillMask, so both walks must agree
// on vocabularies without full byte coverage.
fn fillMaskBrute(g: *const grammar.Grammar, tok: *const tokenizer.Tokenizer, st: *const parser.State, out: []u32, scratch: *parser.State, work: *parser.State, side: *parser.Side, cache: *complete.Cache) error{ DeadEnd, ResourceLimit, OutOfMemory, Cancelled }!void {
    const mw = tok.maskWords();
    std.debug.assert(out.len >= mw);
    @memset(out[0..mw], 0);

    const filtered = (!tok.byte_complete and g.finite_literal) or g.needs_reachability;
    if (g.needs_reachability) {
        cache.reset();
        cache.fill_budget = complete.FILL_SEARCH_BUDGET;
    }
    var w = work_mod.Work{};

    var first = [_]bool{false} ** 256;
    var b: usize = 0;
    while (b < 256) : (b += 1) {
        const byte = [1]u8{@as(u8, @intCast(b))};
        parser.feedBytes(g, side, st, &byte, scratch, work) catch |err| {
            switch (err) {
                error.Parse => continue,
                error.ResourceLimit => {
                    first[b] = true;
                    continue;
                },
                error.OutOfMemory => return error.OutOfMemory,
            }
        };
        parser.releaseState(side, scratch);
        first[b] = true;
    }

    const can_end = parser.canEnd(g, st);
    var any = false;
    var id: u32 = 0;
    while (id < tok.vocab_size) : (id += 1) {
        if (tok.is_special[id]) continue;
        if (tok.is_eos[id]) {
            if (can_end) {
                setBit(out, id);
                any = true;
            }
            continue;
        }
        const bytes = tok.bytes[id];
        if (bytes.len == 0) continue;
        if (!first[bytes[0]]) continue;
        parser.feedBytes(g, side, st, bytes, scratch, work) catch |err| {
            switch (err) {
                error.Parse => continue,
                error.ResourceLimit => return error.ResourceLimit,
                error.OutOfMemory => return error.OutOfMemory,
            }
        };
        // Same strict policy as setNodeTokens (ADR-0005 D3): an unproven
        // token fails the mask call with ResourceLimit rather than staying
        // allowed on a guess.
        const alive = !filtered or (complete.stateAlive(g, tok, scratch, cache, &w, side) catch |err| switch (err) {
            error.ResourceLimit => return error.ResourceLimit,
            else => |e| return e,
        });
        parser.releaseState(side, scratch);
        if (!alive) continue;
        setBit(out, id);
        any = true;
    }
    if (!any) return error.DeadEnd;
}

const testing = std.testing;

fn litGrammar(a: std.mem.Allocator, s: []const u8) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addLiteralNode(s);
    return b.finish(arena, .literal_set, root, .{});
}

fn objGrammar(a: std.mem.Allocator) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const str_node = try b.addNode(.{ .str = .{ .min_len = 0, .max_len = grammar.UNBOUNDED } });
    const int_node = try b.addNode(.{ .int_v = {} });
    const props = try b.copyProps(&[_]grammar.Prop{
        .{ .key = try b.addLiteral("\"a\":"), .value = str_node, .required = true },
        .{ .key = try b.addLiteral("\"b\":"), .value = int_node, .required = false },
    });
    const root = try b.addNode(.{ .object = .{ .props = props } });
    return b.finish(arena, .json_schema, root, .{});
}

fn minLenStrObjGrammar(a: std.mem.Allocator, min_len: u32) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const str_node = try b.addNode(.{ .str = .{ .min_len = min_len, .max_len = grammar.UNBOUNDED } });
    const props = try b.copyProps(&[_]grammar.Prop{
        .{ .key = try b.addLiteral("\"a\":"), .value = str_node, .required = true },
        .{ .key = try b.addLiteral("\"b\":"), .value = str_node, .required = true },
    });
    const root = try b.addNode(.{ .object = .{ .props = props } });
    return b.finish(arena, .json_schema, root, .{});
}

test "cache normal form: minLength close gate is preserved (ADR-0007 D2+)" {
    const alloc = testing.allocator;
    var g = try minLenStrObjGrammar(alloc, 2);
    defer g.deinit();
    var tk = try makeTok(alloc, 7, &.{ "{\"a\":", "\"", "x", "\",\"", "b\":", "x\"}", "" }, &.{6}, &.{});
    defer tk.deinit();
    var fast = try precompute.FastData.build(alloc, &tk);
    defer fast.deinit();
    var side = parser.Side{ .a = undefined };

    var out_live = [_]u32{0};
    var out_norm = [_]u32{0};
    var out_fast = [_]u32{0};
    var st = try initStateT(&g, 8);
    try feedOkT(&g, &st, "{\"a\":\"");
    // The merged close-and-continue token id 3 (`","`): legal exactly at
    // count >= minLength, and the normal form (count clamped at minLength)
    // must give the same mask as the live state at every content count.
    var fed: u32 = 0;
    while (true) : (fed += 1) {
        try fill(alloc, &g, &tk, &st, &out_live);
        try testing.expectEqual(fed >= 2, bit(&out_live, 3));
        var norm: parser.State = undefined;
        normalizeUniformString(&g, &st, &side, &norm);
        try testing.expectEqual(@min(fed, 2), norm.threads[0].frames[norm.threads[0].len - 1].str.count);
        try fill(alloc, &g, &tk, &norm, &out_norm);
        try testing.expectEqual(out_live[0], out_norm[0]);
        // The fast path on the normal form (what c_api computes and caches
        // under the normalized key) must agree too.
        try testing.expect(try fillFast(alloc, &g, &tk, &norm, &out_fast, &fast, null, 1));
        try testing.expectEqual(out_live[0], out_fast[0]);
        parser.releaseState(&side, &norm);
        if (fed == 3) break;
        try feedOkT(&g, &st, "x");
    }
    // The admitted merged token genuinely completes the document.
    try feedOkT(&g, &st, "\",\"");
    try feedOkT(&g, &st, "b\":");
    try feedOkT(&g, &st, "\"");
    try feedOkT(&g, &st, "x");
    try feedOkT(&g, &st, "x\"}");
    try testing.expect(parser.canEnd(&g, &st));
}

fn makeTok(a: std.mem.Allocator, vocab: u32, toks: []const []const u8, eos: []const u32, special: []const u32) !tokenizer.Tokenizer {
    const entries = try a.alloc(tokenizer.Entry, vocab);
    defer a.free(entries);
    for (toks, 0..) |t, i| entries[i] = .{ .id = @intCast(i), .bytes = t };
    return tokenizer.Tokenizer.create(a, vocab, entries, eos, special);
}

fn bit(out: []const u32, id: u32) bool {
    return (out[id / 32] >> @as(u5, @intCast(id % 32))) & 1 == 1;
}

// Test grammars here never reference side chunks, so an allocator-less
// dummy store is enough for init/feed calls.
fn initStateT(g: *const grammar.Grammar, max_threads: u16) parser.Error!parser.State {
    var side = parser.Side{ .a = undefined };
    return parser.initState(g, max_threads, &side);
}

fn feedT(g: *const grammar.Grammar, st: *const parser.State, bytes: []const u8, out: *parser.State, work: *parser.State) parser.Error!void {
    var side = parser.Side{ .a = undefined };
    return parser.feedBytes(g, &side, st, bytes, out, work);
}

fn fill(a: std.mem.Allocator, g: *const grammar.Grammar, tk: *const tokenizer.Tokenizer, st: *const parser.State, out: []u32) Error!void {
    var w = work_mod.Work{};
    var buf = MaskBuf.init(a, a);
    defer buf.deinit();
    var side = parser.Side{ .a = undefined };
    return fillMask(g, tk, st, out, &w, &buf, &side);
}

test "mask: literal ab, eos only at end, multi-char token" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "ab");
    defer g.deinit();
    var tk = try makeTok(alloc, 5, &.{ "a", "b", "ab", "x", "" }, &.{4}, &.{});
    defer tk.deinit();
    var out = [_]u32{0};
    var st = try initStateT(&g, 4);
    try fill(alloc, &g, &tk, &st, &out);
    try testing.expect(bit(&out, 0));
    try testing.expect(!bit(&out, 1));
    try testing.expect(bit(&out, 2));
    try testing.expect(!bit(&out, 3));
    try testing.expect(!bit(&out, 4));
    var st2: parser.State = undefined;
    var work: parser.State = undefined;
    try feedT(&g, &st, "a", &st2, &work);
    try fill(alloc, &g, &tk, &st2, &out);
    try testing.expect(!bit(&out, 0));
    try testing.expect(bit(&out, 1));
    try testing.expect(!bit(&out, 2));
    try testing.expect(!bit(&out, 4));
    var st3: parser.State = undefined;
    try feedT(&g, &st, "ab", &st3, &work);
    try fill(alloc, &g, &tk, &st3, &out);
    try testing.expectEqual(@as(u32, 1) << 4, out[0]);
}

test "mask: vocab 33, tail bits of last word are zero" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "z");
    defer g.deinit();
    var toks: [33][]const u8 = undefined;
    var bufs: [32][1]u8 = undefined;
    for (0..32) |i| {
        bufs[i][0] = 'a' + @as(u8, @intCast(i % 26));
        toks[i] = &bufs[i];
    }
    toks[32] = "";
    var tk = try makeTok(alloc, 33, &toks, &.{32}, &.{});
    defer tk.deinit();
    try testing.expectEqual(@as(usize, 2), tk.maskWords());
    var out = [_]u32{ 0xFFFFFFFF, 0xFFFFFFFF };
    var st = try initStateT(&g, 4);
    try fill(alloc, &g, &tk, &st, &out);
    try testing.expect(bit(&out, 25));
    try testing.expect(!bit(&out, 32));
    try testing.expectEqual(@as(u32, 0), out[1]);
    var st2: parser.State = undefined;
    var work: parser.State = undefined;
    try feedT(&g, &st, "z", &st2, &work);
    try fill(alloc, &g, &tk, &st2, &out);
    try testing.expectEqual(@as(u32, 0), out[0]);
    try testing.expectEqual(@as(u32, 1), out[1]);
}

test "mask: special non-eos never allowed, multiple eos" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "ab");
    defer g.deinit();
    var tk = try makeTok(alloc, 6, &.{ "a", "ab", "a", "", "", "b" }, &.{ 3, 4 }, &.{2});
    defer tk.deinit();
    var out = [_]u32{0};
    var st = try initStateT(&g, 4);
    try fill(alloc, &g, &tk, &st, &out);
    try testing.expect(bit(&out, 0));
    try testing.expect(bit(&out, 1));
    try testing.expect(!bit(&out, 2));
    try testing.expect(!bit(&out, 3));
    try testing.expect(!bit(&out, 4));
    var st2: parser.State = undefined;
    var work: parser.State = undefined;
    try feedT(&g, &st, "ab", &st2, &work);
    try fill(alloc, &g, &tk, &st2, &out);
    try testing.expect(bit(&out, 3));
    try testing.expect(bit(&out, 4));
    try testing.expect(!bit(&out, 5));
}

test "mask: duplicate byte images set every chained id" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "ab");
    defer g.deinit();
    var tk = try makeTok(alloc, 4, &.{ "ab", "ab", "x", "" }, &.{3}, &.{});
    defer tk.deinit();
    var out = [_]u32{0};
    var st = try initStateT(&g, 4);
    try fill(alloc, &g, &tk, &st, &out);
    try testing.expect(bit(&out, 0));
    try testing.expect(bit(&out, 1));
    try testing.expect(!bit(&out, 2));
}

test "mask: DeadEnd when no token and no eos allowed" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "ab");
    defer g.deinit();
    var tk = try makeTok(alloc, 2, &.{ "x", "y" }, &.{}, &.{});
    defer tk.deinit();
    var out = [_]u32{0};
    var st = try initStateT(&g, 4);
    try testing.expectError(error.DeadEnd, fill(alloc, &g, &tk, &st, &out));
    var tk2 = try makeTok(alloc, 2, &.{ "a", "c" }, &.{}, &.{});
    defer tk2.deinit();
    var st2: parser.State = undefined;
    var work: parser.State = undefined;
    try feedT(&g, &st, "a", &st2, &work);
    try testing.expectError(error.DeadEnd, fill(alloc, &g, &tk2, &st2, &out));
}

test "mask: deterministic, extra words untouched" {
    const alloc = testing.allocator;
    var g = try objGrammar(alloc);
    defer g.deinit();
    var tk = try makeTok(alloc, 5, &.{ "{\"a\":", "\"x\"", "}", "{\"a\":\"x\"}", "" }, &.{4}, &.{});
    defer tk.deinit();
    var out1 = [_]u32{ 0, 0xDEADBEEF, 0x12345678 };
    var out2 = [_]u32{ 0, 0xDEADBEEF, 0x12345678 };
    var st = try initStateT(&g, 8);
    try fill(alloc, &g, &tk, &st, &out1);
    try fill(alloc, &g, &tk, &st, &out2);
    try testing.expectEqual(out1[0], out2[0]);
    try testing.expectEqual(@as(u32, 0xDEADBEEF), out1[1]);
    try testing.expectEqual(@as(u32, 0x12345678), out1[2]);
    try testing.expect(bit(&out1, 0));
    try testing.expect(!bit(&out1, 1));
    try testing.expect(!bit(&out1, 2));
    try testing.expect(bit(&out1, 3));
    try testing.expect(!bit(&out1, 4));
}

test "mask: first-byte filter is not the final decision" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "ab");
    defer g.deinit();
    var tk = try makeTok(alloc, 3, &.{ "ax", "a", "" }, &.{2}, &.{});
    defer tk.deinit();
    var out = [_]u32{0};
    var st = try initStateT(&g, 4);
    // "ax" fails mid-token (the first byte alone is not decisive) and "a"
    // is byte-legal but cannot be completed (no token provides "b"), so
    // under TZ 3.1 nothing is allowed and the mask is a dead end.
    try testing.expectError(error.DeadEnd, fill(alloc, &g, &tk, &st, &out));
}

test "mask: token leading to a dead end is not allowed (TZ 3.1)" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "ab");
    defer g.deinit();
    var tk = try makeTok(alloc, 3, &.{ "ab", "a", "" }, &.{2}, &.{});
    defer tk.deinit();
    var out = [_]u32{0};
    var st = try initStateT(&g, 4);
    try fill(alloc, &g, &tk, &st, &out);
    try testing.expect(bit(&out, 0)); // "ab" completes the literal
    try testing.expect(!bit(&out, 1)); // "a" would dead-end (no "b" token)
    try testing.expect(!bit(&out, 2)); // EOS only in an accepting state
    var st2: parser.State = undefined;
    var work: parser.State = undefined;
    try feedT(&g, &st, "ab", &st2, &work);
    try fill(alloc, &g, &tk, &st2, &out);
    try testing.expectEqual(@as(u32, 1) << 2, out[0]);
}

test "mask: completion filter keeps multi-token completions alive" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "ab");
    defer g.deinit();
    var tk = try makeTok(alloc, 4, &.{ "a", "b", "ab", "" }, &.{3}, &.{});
    defer tk.deinit();
    var out = [_]u32{0};
    var st = try initStateT(&g, 4);
    try fill(alloc, &g, &tk, &st, &out);
    try testing.expect(bit(&out, 0)); // "a" then "b" completes
    try testing.expect(!bit(&out, 1)); // "b" cannot start this literal
    try testing.expect(bit(&out, 2));
    var st2: parser.State = undefined;
    var work: parser.State = undefined;
    try feedT(&g, &st, "a", &st2, &work);
    try fill(alloc, &g, &tk, &st2, &out);
    try testing.expect(bit(&out, 1));
    try testing.expect(!bit(&out, 0));
}

test "mask: object walk with multi-structural tokens" {
    const alloc = testing.allocator;
    var g = try objGrammar(alloc);
    defer g.deinit();
    var tk = try makeTok(alloc, 7, &.{ "{\"a\":", "\"x\"", "\"x\"}", ",\"b\":", "1}", "1", "" }, &.{6}, &.{});
    defer tk.deinit();
    var out = [_]u32{0};
    var st = try initStateT(&g, 8);
    try fill(alloc, &g, &tk, &st, &out);
    try testing.expect(bit(&out, 0));
    try testing.expect(!bit(&out, 1));
    try testing.expect(!bit(&out, 6));
    var st2: parser.State = undefined;
    var work: parser.State = undefined;
    try feedT(&g, &st, "{\"a\":", &st2, &work);
    try fill(alloc, &g, &tk, &st2, &out);
    try testing.expect(!bit(&out, 0));
    try testing.expect(bit(&out, 1));
    try testing.expect(bit(&out, 2));
    var st3: parser.State = undefined;
    try feedT(&g, &st2, "\"x\"", &st3, &work);
    try fill(alloc, &g, &tk, &st3, &out);
    try testing.expect(bit(&out, 3));
    try testing.expect(!bit(&out, 6));
    var st4: parser.State = undefined;
    try feedT(&g, &st3, ",\"b\":", &st4, &work);
    try fill(alloc, &g, &tk, &st4, &out);
    try testing.expect(bit(&out, 4));
    try testing.expect(bit(&out, 5));
    try testing.expect(!bit(&out, 6));
    var st5: parser.State = undefined;
    try feedT(&g, &st4, "1}", &st5, &work);
    try fill(alloc, &g, &tk, &st5, &out);
    try testing.expect(parser.canEnd(&g, &st5));
    try testing.expectEqual(@as(u32, 1) << 6, out[0]);
}

// Equivalence harness: trie walk vs the reference linear scan on random
// small-alphabet vocabularies (heavy prefix sharing, duplicates, special
// ids) and random literal-set grammars, at several reachable states.
test "mask: trie walk equals linear scan on random vocabularies" {
    const alloc = testing.allocator;
    var rng = std.Random.DefaultPrng.init(0x5EED_F00D);
    const r = rng.random();
    const alphabet = "ab{}:,\"01";
    var cache = complete.Cache.init(alloc);
    defer cache.deinit();

    var case_i: usize = 0;
    while (case_i < 40) : (case_i += 1) {
        // The completion cache is keyed by the state byte image; each case
        // has its own grammar and tokenizer, so start it clean.
        cache.reset();
        const n_tok = 64 + r.uintLessThan(usize, 200);
        var toks: std.ArrayListUnmanaged([]const u8) = .{};
        defer {
            for (toks.items) |t| alloc.free(t);
            toks.deinit(alloc);
        }
        var j: usize = 0;
        while (j < n_tok) : (j += 1) {
            const len = 1 + r.uintLessThan(usize, 5);
            const t = try alloc.alloc(u8, len);
            for (t) |*b| b.* = alphabet[r.uintLessThan(usize, alphabet.len)];
            try toks.append(alloc, t);
        }
        const eos_id: u32 = @intCast(n_tok);
        try toks.append(alloc, try alloc.dupe(u8, ""));
        const vocab: u32 = @intCast(toks.items.len);
        var special: std.ArrayListUnmanaged(u32) = .{};
        defer special.deinit(alloc);
        if (r.boolean()) try special.append(alloc, r.uintLessThan(u32, vocab - 1));

        var tk = try makeTok(alloc, vocab, toks.items, &.{eos_id}, special.items);
        defer tk.deinit();

        // Random literal set over the same alphabet.
        const n_lit = 1 + r.uintLessThan(usize, 6);
        var arena = std.heap.ArenaAllocator.init(alloc);
        var b = grammar.Builder.init(arena.allocator());
        const ids = try arena.allocator().alloc(grammar.NodeId, n_lit);
        for (ids) |*nid| {
            const len = 1 + r.uintLessThan(usize, 8);
            const lit = try arena.allocator().alloc(u8, len);
            for (lit) |*lb| lb.* = alphabet[r.uintLessThan(usize, alphabet.len)];
            nid.* = try b.addLiteralNode(lit);
        }
        const root = if (n_lit == 1) ids[0] else try b.addNode(.{ .choice = try b.copyNodeIds(ids) });
        var g = try b.finish(arena, .literal_set, root, .{});
        defer g.deinit();

        const mw = tk.maskWords();
        var st = try initStateT(&g, 16);
        var step: usize = 0;
        while (step < 4) : (step += 1) {
            const out_trie = try alloc.alloc(u32, mw);
            defer alloc.free(out_trie);
            const out_brute = try alloc.alloc(u32, mw);
            defer alloc.free(out_brute);
            var scratch: parser.State = undefined;
            var work: parser.State = undefined;
            const e_trie = fill(alloc, &g, &tk, &st, out_trie);
            var side = parser.Side{ .a = undefined };
            const e_brute = fillMaskBrute(&g, &tk, &st, out_brute, &scratch, &work, &side, &cache);
            if (e_brute) |_| {
                const got = e_trie catch |err| {
                    std.debug.print("trie failed with {s} where brute succeeded (case {d}, step {d})\n", .{ @errorName(err), case_i, step });
                    return error.TestUnexpectedResult;
                };
                _ = got;
                try testing.expectEqualSlices(u32, out_brute, out_trie);
            } else |err_brute| {
                try testing.expectError(err_brute, e_trie);
                break;
            }
            // Advance along a random allowed token.
            const pick = r.uintLessThan(u32, vocab);
            var advanced = false;
            var k: u32 = 0;
            while (k < vocab) : (k += 1) {
                const id = (pick + k) % vocab;
                if (!bit(out_trie, id) or tk.is_eos[id]) continue;
                var next: parser.State = undefined;
                feedT(&g, &st, tk.bytes[id], &next, &work) catch continue;
                st = next;
                advanced = true;
                break;
            }
            if (!advanced) break;
        }
    }
}

// Strong equivalence: a literal set compiled as a shared-prefix LitTrie
// must produce exactly the masks of the same set compiled as a plain choice
// of literals at every reachable state. The trie changes the parser thread
// representation (fewer threads), not the accepted language.
test "mask: lit_trie grammar equals choice grammar on random vocabularies" {
    const alloc = testing.allocator;
    var rng = std.Random.DefaultPrng.init(0x1BADB002);
    const r = rng.random();
    const alphabet = "ab{}:,\"01";

    var case_i: usize = 0;
    while (case_i < 30) : (case_i += 1) {
        const n_tok = 64 + r.uintLessThan(usize, 200);
        var toks: std.ArrayListUnmanaged([]const u8) = .{};
        defer {
            for (toks.items) |t| alloc.free(t);
            toks.deinit(alloc);
        }
        var j: usize = 0;
        while (j < n_tok) : (j += 1) {
            const len = 1 + r.uintLessThan(usize, 5);
            const t = try alloc.alloc(u8, len);
            for (t) |*b| b.* = alphabet[r.uintLessThan(usize, alphabet.len)];
            try toks.append(alloc, t);
        }
        const eos_id: u32 = @intCast(n_tok);
        try toks.append(alloc, try alloc.dupe(u8, ""));
        const vocab: u32 = @intCast(toks.items.len);
        var tk = try makeTok(alloc, vocab, toks.items, &.{eos_id}, &.{});
        defer tk.deinit();

        // Random literal set (empty alternatives included).
        const n_lit = 2 + r.uintLessThan(usize, 4);
        var lit_store: [5][9]u8 = undefined;
        var lit_slices: [5][]const u8 = undefined;
        for (0..n_lit) |li| {
            const len = r.uintLessThan(usize, 9);
            for (lit_store[li][0..len]) |*lb| lb.* = alphabet[r.uintLessThan(usize, alphabet.len)];
            lit_slices[li] = lit_store[li][0..len];
        }

        var arena_a = std.heap.ArenaAllocator.init(alloc);
        const aa = arena_a.allocator();
        var ba = grammar.Builder.init(aa);
        const ids_a = try aa.alloc(grammar.NodeId, n_lit);
        for (0..n_lit) |li| ids_a[li] = try ba.addLiteralNode(lit_slices[li]);
        const root_a = try ba.addNode(.{ .choice = try ba.copyNodeIds(ids_a) });
        var g_a = try ba.finish(arena_a, .literal_set, root_a, .{});
        defer g_a.deinit();

        var arena_b = std.heap.ArenaAllocator.init(alloc);
        const ab = arena_b.allocator();
        var bb = grammar.Builder.init(ab);
        const ids_b = try ab.alloc(grammar.NodeId, n_lit);
        for (0..n_lit) |li| ids_b[li] = try bb.addLiteralNode(lit_slices[li]);
        const root_b = try bb.addNode(try bb.alternativesNode(ids_b));
        var g_b = try bb.finish(arena_b, .literal_set, root_b, .{});
        defer g_b.deinit();

        const mw = tk.maskWords();
        var st_a = try initStateT(&g_a, 16);
        var st_b = try initStateT(&g_b, 16);
        var step: usize = 0;
        while (step < 4) : (step += 1) {
            const out_a = try alloc.alloc(u32, mw);
            defer alloc.free(out_a);
            const out_b = try alloc.alloc(u32, mw);
            defer alloc.free(out_b);
            const e_a = fill(alloc, &g_a, &tk, &st_a, out_a);
            const e_b = fill(alloc, &g_b, &tk, &st_b, out_b);
            if (e_a) |_| {
                const got_b = e_b catch |err| {
                    std.debug.print("trie fill failed with {s} where choice succeeded (case {d}, step {d})\n", .{ @errorName(err), case_i, step });
                    return error.TestUnexpectedResult;
                };
                _ = got_b;
                try testing.expectEqualSlices(u32, out_a, out_b);
            } else |err_a| {
                try testing.expectError(err_a, e_b);
                break;
            }
            // Advance both states with the same random allowed token.
            const pick = r.uintLessThan(u32, vocab);
            var advanced = false;
            var k: u32 = 0;
            while (k < vocab) : (k += 1) {
                const id = (pick + k) % vocab;
                if (!bit(out_a, id) or tk.is_eos[id]) continue;
                var na: parser.State = undefined;
                var nb: parser.State = undefined;
                var wa: parser.State = undefined;
                var wb: parser.State = undefined;
                feedT(&g_a, &st_a, tk.bytes[id], &na, &wa) catch continue;
                feedT(&g_b, &st_b, tk.bytes[id], &nb, &wb) catch {
                    std.debug.print("trie feed failed where choice succeeded (case {d}, step {d}, token {d})\n", .{ case_i, step, id });
                    return error.TestUnexpectedResult;
                };
                st_a = na;
                st_b = nb;
                advanced = true;
                break;
            }
            if (!advanced) break;
        }
    }
}

// --- ADR-0007 fast-path tests ---

fn strGrammarT(a: std.mem.Allocator, min: u32, max: u32) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addNode(.{ .str = .{ .min_len = min, .max_len = max } });
    return b.finish(arena, .json_schema, root, .{});
}

fn fillFast(a: std.mem.Allocator, g: *const grammar.Grammar, tk: *const tokenizer.Tokenizer, st: *const parser.State, out: []u32, fast: *const precompute.FastData, wc: ?*cache_mod.Cache, max_workers: u32) Error!bool {
    var w = work_mod.Work{};
    var buf = MaskBuf.init(a, a);
    defer buf.deinit();
    var side = parser.Side{ .a = undefined };
    return fillMaskFast(g, tk, st, out, &w, &buf, &side, fast, wc, null, max_workers);
}

// Vocabulary mixing pure content, quotes, backslashes, controls, multi-byte
// UTF-8 (valid and trapped) and duplicate byte images.
fn fastTok(a: std.mem.Allocator) !tokenizer.Tokenizer {
    return makeTok(a, 12, &.{
        "a", // 0 content cp1
        "bc", // 1 content cp2
        "\xc3\xa9", // 2 content cp1 (é)
        "\xe4\xb8\xad", // 3 content cp1 (中)
        "a\xc3\xa9z", // 4 content cp3
        "\"", // 5 exact (quote)
        "a\"", // 6 exact
        "\\n", // 7 exact (backslash)
        "\xc3", // 8 exact (truncated UTF-8)
        "a", // 9 duplicate of 0
        "\x01", // 10 exact (control)
        "", // 11 eos
    }, &.{11}, &.{});
}

test "fast path: uniform string state equals the trie walk, R edges" {
    const alloc = testing.allocator;
    var tk = try fastTok(alloc);
    defer tk.deinit();
    var fast = try precompute.FastData.build(alloc, &tk);
    defer fast.deinit();
    try testing.expectEqual(@as(u32, 3), fast.cp_max);

    var out_trie = [_]u32{0};
    var out_fast = [_]u32{0};

    // max_len edges: R in {0, 1, CP_MAX-1, CP_MAX, CP_MAX+1, unbounded}.
    const maxes = [_]u32{ 1, 2, 3, 4, grammar.UNBOUNDED };
    for (maxes) |mx| {
        var g = try strGrammarT(alloc, 0, mx);
        defer g.deinit();
        var st = try initStateT(&g, 4);
        // Initial state (before the opening quote) is NOT uniform.
        {
            var tmp = [_]u32{0};
            try testing.expect(!try fillFast(alloc, &g, &tk, &st, &tmp, &fast, null, 1));
        }
        try feedOkT(&g, &st, "\"");
        var fed: u32 = 0;
        while (true) {
            try fill(alloc, &g, &tk, &st, &out_trie);
            const applied = try fillFast(alloc, &g, &tk, &st, &out_fast, &fast, null, 1);
            try testing.expect(applied);
            try testing.expectEqual(out_trie[0], out_fast[0]);
            // R = max_len - fed pure content tokens of cp 1.
            const r: u32 = if (mx == grammar.UNBOUNDED) mx else mx - fed;
            // "a" (cp1) allowed iff R >= 1; "bc" (cp2) iff R >= 2.
            try testing.expectEqual(r >= 1, bit(&out_fast, 0));
            try testing.expectEqual(r >= 2, bit(&out_fast, 1));
            try testing.expectEqual(r >= 3, bit(&out_fast, 4));
            try testing.expect(!bit(&out_fast, 11)); // EOS excluded
            if (mx == grammar.UNBOUNDED or fed >= mx) break;
            try feedOkT(&g, &st, "a");
            fed += 1;
        }
    }
}

test "fast path: non-uniform states are refused (escape, mid-codepoint, root)" {
    const alloc = testing.allocator;
    var g = try strGrammarT(alloc, 0, grammar.UNBOUNDED);
    defer g.deinit();
    var tk = try fastTok(alloc);
    defer tk.deinit();
    var fast = try precompute.FastData.build(alloc, &tk);
    defer fast.deinit();
    var out = [_]u32{0};

    var st = try initStateT(&g, 4);
    try testing.expectEqual(@as(?u32, null), uniformStringResidual(&g, &st));
    try feedOkT(&g, &st, "\"");
    try testing.expect(uniformStringResidual(&g, &st) != null);
    // Mid-escape: not uniform.
    var st2 = try initStateT(&g, 4);
    try feedOkT(&g, &st2, "\"\\");
    try testing.expectEqual(@as(?u32, null), uniformStringResidual(&g, &st2));
    try testing.expect(!try fillFast(alloc, &g, &tk, &st2, &out, &fast, null, 1));
    // Mid-codepoint: not uniform.
    var st3 = try initStateT(&g, 4);
    try feedOkT(&g, &st3, "\"\xc3");
    try testing.expectEqual(@as(?u32, null), uniformStringResidual(&g, &st3));
    try testing.expect(!try fillFast(alloc, &g, &tk, &st3, &out, &fast, null, 1));
}

test "fast path: string value inside an object (thread stack with parents)" {
    const alloc = testing.allocator;
    var g = try objGrammar(alloc);
    defer g.deinit();
    var tk = try makeTok(alloc, 9, &.{ "{\"a\":", "x\"", "x\"}", ",\"b\":", "1}", "x", "xy", "\"", "" }, &.{8}, &.{});
    defer tk.deinit();
    var fast = try precompute.FastData.build(alloc, &tk);
    defer fast.deinit();
    var out_trie = [_]u32{0};
    var out_fast = [_]u32{0};
    var st = try initStateT(&g, 8);
    try feedOkT(&g, &st, "{\"a\":\"");
    // Uniform string-content state with an object frame below.
    try testing.expect(uniformStringResidual(&g, &st) != null);
    try fill(alloc, &g, &tk, &st, &out_trie);
    try testing.expect(try fillFast(alloc, &g, &tk, &st, &out_fast, &fast, null, 1));
    try testing.expectEqual(out_trie[0], out_fast[0]);
    // Closing tokens live in the exact class and continue into the parent.
    try testing.expect(bit(&out_fast, 1)); // x" closes the string
    try testing.expect(bit(&out_fast, 2)); // x"} closes string and object
    try testing.expect(bit(&out_fast, 7)); // " closes the empty string
    try testing.expect(bit(&out_fast, 5)); // "x" content cp1
    try testing.expect(bit(&out_fast, 6)); // "xy" content cp2
    try testing.expect(!bit(&out_fast, 3)); // ,"b": premature
    try testing.expect(bit(&out_fast, 4)); // "1}" is pure string content (cp2)
}

test "fast path: widened cache shares class bits by capped residual" {
    const alloc = testing.allocator;
    var g = try strGrammarT(alloc, 0, grammar.UNBOUNDED);
    defer g.deinit();
    var tk = try fastTok(alloc);
    defer tk.deinit();
    var fast = try precompute.FastData.build(alloc, &tk);
    defer fast.deinit();
    var c = cache_mod.Cache.init(alloc, 1 << 20);
    defer c.deinit();

    var out_a = [_]u32{0};
    var out_b = [_]u32{0};
    var st = try initStateT(&g, 4);
    try feedOkT(&g, &st, "\"");
    try testing.expect(try fillFast(alloc, &g, &tk, &st, &out_a, &fast, &c, 1));
    try testing.expectEqual(@as(u64, 0), c.widenedHits());
    // A different uniform state with the same capped residual (unbounded)
    // must be served by the widened entry with identical bits.
    try feedOkT(&g, &st, "abc");
    try testing.expect(try fillFast(alloc, &g, &tk, &st, &out_b, &fast, &c, 1));
    try testing.expectEqual(@as(u64, 1), c.widenedHits());
    try testing.expectEqual(out_a[0], out_b[0]);
    // Same bits as without the widened cache.
    var out_c = [_]u32{0};
    try testing.expect(try fillFast(alloc, &g, &tk, &st, &out_c, &fast, null, 1));
    try testing.expectEqual(out_a[0], out_c[0]);
}

test "fast path: max_workers does not change the mask" {
    const alloc = testing.allocator;
    var g = try strGrammarT(alloc, 1, 2);
    defer g.deinit();
    var tk = try fastTok(alloc);
    defer tk.deinit();
    var fast = try precompute.FastData.build(alloc, &tk);
    defer fast.deinit();
    var st = try initStateT(&g, 4);
    try feedOkT(&g, &st, "\"");
    var ref_out = [_]u32{0};
    try testing.expect(try fillFast(alloc, &g, &tk, &st, &ref_out, &fast, null, 1));
    for ([_]u32{ 2, 8 }) |workers| {
        var out = [_]u32{0};
        try testing.expect(try fillFast(alloc, &g, &tk, &st, &out, &fast, null, workers));
        try testing.expectEqual(ref_out[0], out[0]);
    }
}

fn feedOkT(g: *const grammar.Grammar, st: *parser.State, bytes: []const u8) parser.Error!void {
    var side = parser.Side{ .a = undefined };
    var out: parser.State = undefined;
    var work: parser.State = undefined;
    try parser.feedBytes(g, &side, st, bytes, &out, &work);
    st.* = out;
}

// Three-way differential harness (ADR-0007 D4): fast path, trie walk and
// the brute linear scan on random vocabularies over an alphabet with
// quotes, backslashes, controls, valid and trapped UTF-8 pieces; grammars
// are strings with random min/max bounds (R sweeps through 0, 1,
// CP_MAX-1..CP_MAX+1 and unbounded across cases) and random objects with a
// string value. Errors (DeadEnd) must match exactly.
test "fast path: three-way differential on random string grammars" {
    const alloc = testing.allocator;
    var rng = std.Random.DefaultPrng.init(0xFA57_0007);
    const r = rng.random();
    const pieces = [_][]const u8{
        "a",    "b",            "z",                "\"",               "\\",
        "\x01", "\x1f",         "\x7f",             "\xc3\xa9",         "\xe4\xb8\xad",
        "\xc3", "\xed\xa0\x80", "\xf0\x9f\x98\x80", "\xf4\x90\x80\x80",
    };
    var comp_cache = complete.Cache.init(alloc);
    defer comp_cache.deinit();

    var case_i: usize = 0;
    while (case_i < 40) : (case_i += 1) {
        comp_cache.reset();
        const n_tok = 32 + r.uintLessThan(usize, 96);
        var toks: std.ArrayListUnmanaged([]const u8) = .{};
        defer {
            for (toks.items) |t| alloc.free(t);
            toks.deinit(alloc);
        }
        var j: usize = 0;
        while (j < n_tok) : (j += 1) {
            const n_pieces = 1 + r.uintLessThan(usize, 4);
            var t: std.ArrayListUnmanaged(u8) = .{};
            var p: usize = 0;
            while (p < n_pieces) : (p += 1) {
                try t.appendSlice(alloc, pieces[r.uintLessThan(usize, pieces.len)]);
            }
            try toks.append(alloc, try t.toOwnedSlice(alloc));
        }
        const eos_id: u32 = @intCast(n_tok);
        try toks.append(alloc, try alloc.dupe(u8, ""));
        const vocab: u32 = @intCast(toks.items.len);
        var tk = try makeTok(alloc, vocab, toks.items, &.{eos_id}, &.{});
        defer tk.deinit();
        var fast = try precompute.FastData.build(alloc, &tk);
        defer fast.deinit();

        const min_len = r.uintLessThan(u32, 3);
        const max_len = switch (r.uintLessThan(u32, 6)) {
            0 => grammar.UNBOUNDED,
            1 => min_len,
            2 => min_len + 1,
            3 => min_len + 2,
            4 => if (fast.cp_max > 0) fast.cp_max - 1 else 0,
            else => fast.cp_max + 1,
        };
        var g = try strGrammarT(alloc, min_len, max_len);
        defer g.deinit();

        const mw = tk.maskWords();
        var st = try initStateT(&g, 16);
        try feedOkT(&g, &st, "\"");
        var step: usize = 0;
        while (step < 5) : (step += 1) {
            const out_trie = try alloc.alloc(u32, mw);
            defer alloc.free(out_trie);
            const out_fast = try alloc.alloc(u32, mw);
            defer alloc.free(out_fast);
            const out_brute = try alloc.alloc(u32, mw);
            defer alloc.free(out_brute);
            var scratch: parser.State = undefined;
            var work: parser.State = undefined;
            var side = parser.Side{ .a = undefined };
            const e_brute = fillMaskBrute(&g, &tk, &st, out_brute, &scratch, &work, &side, &comp_cache);
            const e_trie = fill(alloc, &g, &tk, &st, out_trie);
            const e_fast = fillFast(alloc, &g, &tk, &st, out_fast, &fast, null, 1 + r.uintLessThan(u32, 8));
            if (e_brute) |_| {
                _ = try e_trie;
                const fe = e_fast catch |err| {
                    std.debug.print("fast path failed with {s} where brute succeeded (case {d}, step {d})\n", .{ @errorName(err), case_i, step });
                    return error.TestUnexpectedResult;
                };
                try testing.expect(fe);
                try testing.expectEqualSlices(u32, out_brute, out_trie);
                try testing.expectEqualSlices(u32, out_brute, out_fast);
            } else |err_b| {
                try testing.expectError(err_b, e_trie);
                try testing.expectError(err_b, e_fast);
                break;
            }
            // Advance along a random allowed pure-content or exact token.
            const pick = r.uintLessThan(u32, vocab);
            var advanced = false;
            var k: u32 = 0;
            while (k < vocab) : (k += 1) {
                const id = (pick + k) % vocab;
                if (!bit(out_trie, id) or tk.is_eos[id]) continue;
                var next: parser.State = undefined;
                feedT(&g, &st, tk.bytes[id], &next, &work) catch continue;
                st = next;
                advanced = true;
                break;
            }
            if (!advanced) break;
            // States reached after a closing token are not uniform; the
            // walk above only continues while the fast path applies.
            if (uniformStringResidual(&g, &st) == null) break;
        }
    }
}
