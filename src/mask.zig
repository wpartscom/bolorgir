const std = @import("std");
const grammar = @import("grammar.zig");
const tokenizer = @import("tokenizer.zig");
const parser = @import("parser.zig");
const work_mod = @import("work.zig");

pub const Error = error{ DeadEnd, ResourceLimit, Cancelled, OutOfMemory };

const StackFrame = struct { node: u32, next_edge: u32 };

/// Session-owned scratch for the trie walk: one parser state per branching
/// trie level plus a spare for chain descent. Allocated lazily on the first
/// fill and reused across mask calls.
pub const MaskBuf = struct {
    a: std.mem.Allocator,
    states: std.ArrayListUnmanaged(parser.State) = .{},
    frames: std.ArrayListUnmanaged(StackFrame) = .{},
    spare: parser.State = undefined,

    pub fn init(a: std.mem.Allocator) MaskBuf {
        return .{ .a = a };
    }

    pub fn deinit(self: *MaskBuf) void {
        self.states.deinit(self.a);
        self.frames.deinit(self.a);
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
/// by can_end, special tokens skipped, ResourceLimit anywhere fails the
/// mask, DeadEnd when nothing is allowed.
pub fn fillMask(g: *const grammar.Grammar, tok: *const tokenizer.Tokenizer, st: *const parser.State, out: []u32, w: *work_mod.Work, buf: *MaskBuf) Error!void {
    const mw = tok.maskWords();
    std.debug.assert(out.len >= mw);
    @memset(out[0..mw], 0);

    const trie = &tok.trie;
    try buf.ensure(trie.max_stack);
    buf.states.items.len = 0;
    buf.frames.items.len = 0;
    buf.states.appendAssumeCapacity(st.*);
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
            buf.states.items.len = buf.frames.items.len;
            continue;
        }
        const ei = trie.node_edge_off[node] + fr.next_edge;
        fr.next_edge += 1;
        const child = trie.edge_child[ei];
        if (trie.node_edge_len[node] == 1) {
            // Single edge: descend in place, the parent state is no longer
            // needed; feed through the spare so a failed feed leaves the
            // current state intact for the prune path.
            parser.feedBytes(g, &buf.states.items[top], &[1]u8{trie.edge_byte[ei]}, &buf.spare) catch |err| switch (err) {
                error.Parse => continue, // prune the subtree
                error.ResourceLimit => return error.ResourceLimit,
            };
            buf.states.items[top] = buf.spare;
            fr.* = .{ .node = child, .next_edge = 0 };
        } else {
            const dst = &buf.states.allocatedSlice()[top + 1];
            parser.feedBytes(g, &buf.states.items[top], &[1]u8{trie.edge_byte[ei]}, dst) catch |err| switch (err) {
                error.Parse => continue, // prune the subtree
                error.ResourceLimit => return error.ResourceLimit,
            };
            buf.states.items.len = top + 2;
            buf.frames.appendAssumeCapacity(.{ .node = child, .next_edge = 0 });
        }
        var id = trie.node_token[child];
        while (id != tokenizer.NO_TOKEN) : (id = tok.token_chain[id]) {
            setBit(out, id);
            any = true;
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

// Reference linear scan kept for equivalence tests only: every non-special
// token is fed whole, the first-byte filter is a pure optimization.
fn fillMaskBrute(g: *const grammar.Grammar, tok: *const tokenizer.Tokenizer, st: *const parser.State, out: []u32, scratch: *parser.State) error{ DeadEnd, ResourceLimit }!void {
    const mw = tok.maskWords();
    std.debug.assert(out.len >= mw);
    @memset(out[0..mw], 0);

    var first = [_]bool{false} ** 256;
    var b: usize = 0;
    while (b < 256) : (b += 1) {
        const byte = [1]u8{@as(u8, @intCast(b))};
        parser.feedBytes(g, st, &byte, scratch) catch |err| {
            switch (err) {
                error.Parse => continue,
                error.ResourceLimit => {
                    first[b] = true;
                    continue;
                },
            }
        };
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
        parser.feedBytes(g, st, bytes, scratch) catch |err| {
            switch (err) {
                error.Parse => continue,
                error.ResourceLimit => return error.ResourceLimit,
            }
        };
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
    const root = try b.addNode(.{ .object = props });
    return b.finish(arena, .json_schema, root, .{});
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

fn fill(a: std.mem.Allocator, g: *const grammar.Grammar, tk: *const tokenizer.Tokenizer, st: *const parser.State, out: []u32) Error!void {
    var w = work_mod.Work{};
    var buf = MaskBuf.init(a);
    defer buf.deinit();
    return fillMask(g, tk, st, out, &w, &buf);
}

test "mask: literal ab, eos only at end, multi-char token" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "ab");
    defer g.deinit();
    var tk = try makeTok(alloc, 5, &.{ "a", "b", "ab", "x", "" }, &.{4}, &.{});
    defer tk.deinit();
    var out = [_]u32{0};
    var st = try parser.initState(&g, 4);
    try fill(alloc, &g, &tk, &st, &out);
    try testing.expect(bit(&out, 0));
    try testing.expect(!bit(&out, 1));
    try testing.expect(bit(&out, 2));
    try testing.expect(!bit(&out, 3));
    try testing.expect(!bit(&out, 4));
    var st2: parser.State = undefined;
    try parser.feedBytes(&g, &st, "a", &st2);
    try fill(alloc, &g, &tk, &st2, &out);
    try testing.expect(!bit(&out, 0));
    try testing.expect(bit(&out, 1));
    try testing.expect(!bit(&out, 2));
    try testing.expect(!bit(&out, 4));
    var st3: parser.State = undefined;
    try parser.feedBytes(&g, &st, "ab", &st3);
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
    var st = try parser.initState(&g, 4);
    try fill(alloc, &g, &tk, &st, &out);
    try testing.expect(bit(&out, 25));
    try testing.expect(!bit(&out, 32));
    try testing.expectEqual(@as(u32, 0), out[1]);
    var st2: parser.State = undefined;
    try parser.feedBytes(&g, &st, "z", &st2);
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
    var st = try parser.initState(&g, 4);
    try fill(alloc, &g, &tk, &st, &out);
    try testing.expect(bit(&out, 0));
    try testing.expect(bit(&out, 1));
    try testing.expect(!bit(&out, 2));
    try testing.expect(!bit(&out, 3));
    try testing.expect(!bit(&out, 4));
    var st2: parser.State = undefined;
    try parser.feedBytes(&g, &st, "ab", &st2);
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
    var st = try parser.initState(&g, 4);
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
    var st = try parser.initState(&g, 4);
    try testing.expectError(error.DeadEnd, fill(alloc, &g, &tk, &st, &out));
    var tk2 = try makeTok(alloc, 2, &.{ "a", "c" }, &.{}, &.{});
    defer tk2.deinit();
    var st2: parser.State = undefined;
    try parser.feedBytes(&g, &st, "a", &st2);
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
    var st = try parser.initState(&g, 8);
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
    var st = try parser.initState(&g, 4);
    try fill(alloc, &g, &tk, &st, &out);
    try testing.expect(!bit(&out, 0));
    try testing.expect(bit(&out, 1));
    try testing.expect(!bit(&out, 2));
}

test "mask: object walk with multi-structural tokens" {
    const alloc = testing.allocator;
    var g = try objGrammar(alloc);
    defer g.deinit();
    var tk = try makeTok(alloc, 7, &.{ "{\"a\":", "\"x\"", "\"x\"}", ",\"b\":", "1}", "1", "" }, &.{6}, &.{});
    defer tk.deinit();
    var out = [_]u32{0};
    var st = try parser.initState(&g, 8);
    try fill(alloc, &g, &tk, &st, &out);
    try testing.expect(bit(&out, 0));
    try testing.expect(!bit(&out, 1));
    try testing.expect(!bit(&out, 6));
    var st2: parser.State = undefined;
    try parser.feedBytes(&g, &st, "{\"a\":", &st2);
    try fill(alloc, &g, &tk, &st2, &out);
    try testing.expect(!bit(&out, 0));
    try testing.expect(bit(&out, 1));
    try testing.expect(bit(&out, 2));
    var st3: parser.State = undefined;
    try parser.feedBytes(&g, &st2, "\"x\"", &st3);
    try fill(alloc, &g, &tk, &st3, &out);
    try testing.expect(bit(&out, 3));
    try testing.expect(!bit(&out, 6));
    var st4: parser.State = undefined;
    try parser.feedBytes(&g, &st3, ",\"b\":", &st4);
    try fill(alloc, &g, &tk, &st4, &out);
    try testing.expect(bit(&out, 4));
    try testing.expect(bit(&out, 5));
    try testing.expect(!bit(&out, 6));
    var st5: parser.State = undefined;
    try parser.feedBytes(&g, &st4, "1}", &st5);
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

    var case_i: usize = 0;
    while (case_i < 40) : (case_i += 1) {
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
        var st = try parser.initState(&g, 16);
        var step: usize = 0;
        while (step < 4) : (step += 1) {
            const out_trie = try alloc.alloc(u32, mw);
            defer alloc.free(out_trie);
            const out_brute = try alloc.alloc(u32, mw);
            defer alloc.free(out_brute);
            var scratch: parser.State = undefined;
            const e_trie = fill(alloc, &g, &tk, &st, out_trie);
            const e_brute = fillMaskBrute(&g, &tk, &st, out_brute, &scratch);
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
                parser.feedBytes(&g, &st, tk.bytes[id], &next) catch continue;
                st = next;
                advanced = true;
                break;
            }
            if (!advanced) break;
        }
    }
}
