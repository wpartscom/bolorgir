const std = @import("std");
const grammar = @import("grammar.zig");

pub const Error = error{ UnsupportedTokenizer, OutOfMemory };

pub const Entry = struct { id: u32, bytes: []const u8 };

pub const NO_TOKEN: u32 = std.math.maxInt(u32);

/// Flat byte-prefix trie of the vocabulary (FR-6). Node 0 is the root.
/// Edges of a node are a contiguous slice of edge_byte/edge_child, so no
/// per-node allocations exist. Distinct ids sharing one byte image are
/// chained through token_chain starting at node_token. max_stack is the
/// DFS stack depth a mask walk needs: one frame per branching (>= 2 edges)
/// ancestor, so single-child chains cost no stack.
pub const Trie = struct {
    node_edge_off: []u32,
    node_edge_len: []u32,
    node_token: []u32,
    edge_byte: []u8,
    edge_child: []u32,
    max_depth: u32,
    max_stack: u32,

    /// Child of `node` by byte; the edges of a node are sorted by byte, so
    /// the lookup is a binary search (the root has up to 256 children).
    pub fn child(self: *const Trie, node: u32, b: u8) ?u32 {
        const base = self.node_edge_off[node];
        var lo: u32 = 0;
        var hi: u32 = self.node_edge_len[node];
        while (lo < hi) {
            const mid = lo + (hi - lo) / 2;
            const eb = self.edge_byte[base + mid];
            if (eb < b) {
                lo = mid + 1;
            } else if (eb > b) {
                hi = mid;
            } else return self.edge_child[base + mid];
        }
        return null;
    }
};

/// Immutable tokenizer representation shared by the context.
/// bytes[id] is the exact byte image of the token (not via decode).
pub const Tokenizer = struct {
    vocab_size: u32,
    bytes: []const []const u8,
    is_eos: []bool,
    is_special: []bool,
    identity: u64,
    trie: Trie,
    token_chain: []u32,
    arena: std.heap.ArenaAllocator,
    /// True when every one of the 256 byte values has a usable
    /// single-byte token (ordinary, non-EOS, non-special). Byte-complete
    /// vocabularies make masks exact without any completion search: from
    /// any state that has a byte-wise completion, that completion can be
    /// fed one byte at a time (TZ 3.1), so no byte-legal token can dead
    /// end. The completion filter (src/complete.zig) is only needed when
    /// this flag is false and the grammar language is finite.
    byte_complete: bool,
    /// True when the decoder drops one leading space of the whole text
    /// (HF SentencePiece Strip(" ", start=1, stop=0)). Set by the C ABI
    /// from blg_tokenizer_desc.flags; the compile step then models the
    /// decoded text (see c_api.compileGrammar).
    strip_lead_space: bool = false,

    pub fn deinit(self: *Tokenizer) void {
        self.arena.deinit();
    }

    pub fn maskWords(self: *const Tokenizer) usize {
        return (@as(usize, self.vocab_size) + 31) / 32;
    }

    /// entries: id -> bytes; ids must cover 0..vocab_size-1 exactly once.
    /// A regular token with empty bytes is an error; EOS may have len==0.
    pub fn create(
        child_allocator: std.mem.Allocator,
        vocab_size: u32,
        entries: []const Entry,
        eos_ids: []const u32,
        special_ids: []const u32,
    ) Error!Tokenizer {
        if (vocab_size == 0) return error.UnsupportedTokenizer;
        if (entries.len != vocab_size) return error.UnsupportedTokenizer;

        var arena = std.heap.ArenaAllocator.init(child_allocator);
        errdefer arena.deinit();
        const a = arena.allocator();

        const bytes = try a.alloc([]const u8, vocab_size);
        const is_eos = try a.alloc(bool, vocab_size);
        const is_special = try a.alloc(bool, vocab_size);
        const seen = try a.alloc(bool, vocab_size);
        @memset(is_eos, false);
        @memset(is_special, false);
        @memset(seen, false);

        for (eos_ids) |id| {
            if (id >= vocab_size) return error.UnsupportedTokenizer;
            is_eos[id] = true;
        }
        for (special_ids) |id| {
            if (id >= vocab_size) return error.UnsupportedTokenizer;
            if (is_eos[id]) return error.UnsupportedTokenizer;
            is_special[id] = true;
        }

        var h = grammar.fnv1a64("zg-bbpe-v1");
        var vs_buf: [4]u8 = undefined;
        std.mem.writeInt(u32, &vs_buf, vocab_size, .little);
        h = grammar.fnv1a64Update(h, &vs_buf);

        for (entries) |e| {
            if (e.id >= vocab_size) return error.UnsupportedTokenizer;
            if (seen[e.id]) return error.UnsupportedTokenizer;
            seen[e.id] = true;
            if (e.bytes.len == 0 and !is_eos[e.id] and !is_special[e.id]) return error.UnsupportedTokenizer;
            bytes[e.id] = try a.dupe(u8, e.bytes);
            h = grammar.fnv1a64Update(h, e.bytes);
            h = grammar.fnv1a64Update(h, "\x00");
        }
        for (eos_ids) |id| {
            std.mem.writeInt(u32, &vs_buf, id, .little);
            h = grammar.fnv1a64Update(h, &vs_buf);
        }
        for (special_ids) |id| {
            std.mem.writeInt(u32, &vs_buf, id, .little);
            h = grammar.fnv1a64Update(h, &vs_buf);
        }

        const built = try buildTrie(a, vocab_size, bytes, is_eos, is_special);

        var have_byte = [_]bool{false} ** 256;
        var have_count: u32 = 0;
        for (bytes, 0..) |b, i| {
            if (is_eos[i] or is_special[i]) continue;
            if (b.len != 1) continue;
            if (!have_byte[b[0]]) {
                have_byte[b[0]] = true;
                have_count += 1;
            }
        }

        return .{
            .vocab_size = vocab_size,
            .bytes = bytes,
            .is_eos = is_eos,
            .is_special = is_special,
            .identity = h,
            .trie = built.trie,
            .token_chain = built.chain,
            .arena = arena,
            .byte_complete = have_count == 256,
        };
    }
};

const TmpEdge = struct { byte: u8, child: u32 };
const TmpNode = struct {
    edges: std.ArrayListUnmanaged(TmpEdge) = .{},
    token: u32 = NO_TOKEN,
};

fn tmpEdgeLess(_: void, x: TmpEdge, y: TmpEdge) bool {
    return x.byte < y.byte;
}

const BuiltTrie = struct { trie: Trie, chain: []u32 };

// EOS, special and empty tokens never enter the trie; they are handled
// outside of it (eos by can_end, special forbidden, empty impossible).
fn buildTrie(a: std.mem.Allocator, vocab_size: u32, bytes: []const []const u8, is_eos: []const bool, is_special: []const bool) !BuiltTrie {
    var tmp: std.ArrayListUnmanaged(TmpNode) = .{};
    try tmp.append(a, .{}); // root
    const chain = try a.alloc(u32, vocab_size);
    @memset(chain, NO_TOKEN);

    var max_depth: u32 = 0;
    var total_edges: usize = 0;
    var id: u32 = 0;
    while (id < vocab_size) : (id += 1) {
        if (is_eos[id] or is_special[id]) continue;
        const tb = bytes[id];
        if (tb.len == 0) continue;
        var cur: u32 = 0;
        for (tb) |b| {
            const node = &tmp.items[cur];
            var child: ?u32 = null;
            for (node.edges.items) |e| {
                if (e.byte == b) {
                    child = e.child;
                    break;
                }
            }
            if (child == null) {
                child = @intCast(tmp.items.len);
                try tmp.append(a, .{});
                try tmp.items[cur].edges.append(a, .{ .byte = b, .child = child.? });
                total_edges += 1;
            }
            cur = child.?;
        }
        const term = &tmp.items[cur];
        if (term.token == NO_TOKEN) {
            term.token = id;
        } else {
            // Same byte image under another id: append to the chain so the
            // mask sets every matching id, like the linear scan did.
            var tail = term.token;
            while (chain[tail] != NO_TOKEN) tail = chain[tail];
            chain[tail] = id;
        }
        if (tb.len > max_depth) max_depth = @intCast(tb.len);
    }

    const n = tmp.items.len;
    var trie: Trie = .{
        .node_edge_off = try a.alloc(u32, n),
        .node_edge_len = try a.alloc(u32, n),
        .node_token = try a.alloc(u32, n),
        .edge_byte = try a.alloc(u8, total_edges),
        .edge_child = try a.alloc(u32, total_edges),
        .max_depth = max_depth,
        .max_stack = 0,
    };
    var off: u32 = 0;
    for (tmp.items, 0..) |*tn, i| {
        // Sorted edges: mask generation only enumerates them, while the
        // coverage DP looks children up by byte (binary search).
        std.mem.sort(TmpEdge, tn.edges.items, {}, tmpEdgeLess);
        trie.node_edge_off[i] = off;
        trie.node_edge_len[i] = @intCast(tn.edges.items.len);
        trie.node_token[i] = tn.token;
        for (tn.edges.items) |e| {
            trie.edge_byte[off] = e.byte;
            trie.edge_child[off] = e.child;
            off += 1;
        }
        tn.edges.deinit(a);
    }
    tmp.deinit(a);
    trie.max_stack = try computeMaxStack(a, &trie);
    return .{ .trie = trie, .chain = chain };
}

// Max DFS frames needed by the mask walk: a frame per node on the current
// path that still has unexplored siblings, i.e. one per branching ancestor.
fn computeMaxStack(a: std.mem.Allocator, trie: *const Trie) !u32 {
    const frames = try a.alloc(u32, trie.node_token.len);
    defer a.free(frames);
    var stack: std.ArrayListUnmanaged(u32) = .{};
    defer stack.deinit(a);
    try stack.append(a, 0);
    frames[0] = 1;
    var max_stack: u32 = 1;
    while (stack.pop()) |node| {
        const off = trie.node_edge_off[node];
        const len = trie.node_edge_len[node];
        var i: u32 = 0;
        while (i < len) : (i += 1) {
            const child = trie.edge_child[off + i];
            frames[child] = frames[node] + @intFromBool(len >= 2);
            if (frames[child] > max_stack) max_stack = frames[child];
            try stack.append(a, child);
        }
    }
    return max_stack;
}

test "tokenizer create validates coverage and empties" {
    const alloc = std.testing.allocator;
    const entries = [_]Entry{
        .{ .id = 0, .bytes = "a" },
        .{ .id = 1, .bytes = "bc" },
        .{ .id = 2, .bytes = "" },
    };
    var t = try Tokenizer.create(alloc, 3, &entries, &[_]u32{2}, &[_]u32{});
    defer t.deinit();
    try std.testing.expectEqual(@as(usize, 1), t.maskWords());
    try std.testing.expect(t.is_eos[2]);
    try std.testing.expectError(error.UnsupportedTokenizer, Tokenizer.create(alloc, 3, &entries, &[_]u32{}, &[_]u32{}));
}

test "trie: shared prefixes, duplicate byte images chained, eos/special excluded" {
    const alloc = std.testing.allocator;
    const entries = [_]Entry{
        .{ .id = 0, .bytes = "ab" },
        .{ .id = 1, .bytes = "abc" },
        .{ .id = 2, .bytes = "ab" }, // duplicate of id 0
        .{ .id = 3, .bytes = "" }, // eos
        .{ .id = 4, .bytes = "zz" }, // special
    };
    var t = try Tokenizer.create(alloc, 5, &entries, &[_]u32{3}, &[_]u32{4});
    defer t.deinit();
    const tr = &t.trie;
    try std.testing.expectEqual(@as(u32, 3), tr.max_depth);
    // root has a single edge 'a'
    try std.testing.expectEqual(@as(u32, 1), tr.node_edge_len[0]);
    try std.testing.expectEqual(@as(u8, 'a'), tr.edge_byte[0]);
    const na = tr.edge_child[0];
    const nb = tr.edge_child[tr.node_edge_off[na]];
    // "ab" is terminal for id 0, chained to id 2
    try std.testing.expectEqual(@as(u32, 0), tr.node_token[nb]);
    try std.testing.expectEqual(@as(u32, 2), t.token_chain[0]);
    try std.testing.expectEqual(NO_TOKEN, t.token_chain[2]);
    // "abc" terminal id 1
    const nc = tr.edge_child[tr.node_edge_off[nb]];
    try std.testing.expectEqual(@as(u32, 1), tr.node_token[nc]);
    // no nodes for eos/special bytes
    try std.testing.expectEqual(@as(usize, 4), tr.node_token.len);
}

test "trie: byte-complete vocab builds 257 nodes" {
    const alloc = std.testing.allocator;
    var entries: [257]Entry = undefined;
    var bufs: [256][1]u8 = undefined;
    for (0..256) |i| {
        bufs[i][0] = @intCast(i);
        entries[i] = .{ .id = @intCast(i), .bytes = &bufs[i] };
    }
    entries[256] = .{ .id = 256, .bytes = "" };
    var t = try Tokenizer.create(alloc, 257, &entries, &[_]u32{256}, &[_]u32{});
    defer t.deinit();
    try std.testing.expectEqual(@as(usize, 257), t.trie.node_token.len);
    try std.testing.expectEqual(@as(u32, 1), t.trie.max_depth);
    try std.testing.expectEqual(@as(u32, 256), t.trie.node_edge_len[0]);
}
