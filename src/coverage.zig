// Compile-time tokenizer-coverage check (TZ 3.1, FR-6): every literal the
// grammar can emit must be segmentable into a sequence of vocabulary tokens,
// and every open class (string/integer/number) plus structural syntax must
// have a producible completion. Otherwise the constraint is rejected at
// compile time with UNSUPPORTED_TOKENIZER instead of allowing dead-end
// tokens at generation time.
//
// The check is conservative: token boundaries are required to align with
// grammar-node boundaries. A vocabulary that covers a construct only via
// tokens spanning into the neighbouring construct is rejected as
// unsupported, per TZ 3.1 ("insufficient coverage is refused before
// generation"). Byte-complete vocabularies (all 256 single-byte tokens)
// always pass.

const std = @import("std");
const grammar = @import("grammar.zig");
const tokenizer = @import("tokenizer.zig");
const work_mod = @import("work.zig");

const MAX_STR_SAMPLE: u32 = 8192;

pub const Error = error{ OutOfMemory, Cancelled, ResourceLimit };

pub fn checkCoverage(a: std.mem.Allocator, g: *const grammar.Grammar, tok: *const tokenizer.Tokenizer, w: *work_mod.Work) Error!bool {
    var arena = std.heap.ArenaAllocator.init(a);
    defer arena.deinit();
    var c = Checker{ .a = arena.allocator(), .g = g, .tok = tok, .w = w };
    try c.indexTokens();
    return c.covered(g.root);
}

const Checker = struct {
    a: std.mem.Allocator,
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    w: *work_mod.Work,
    by_first: [256]std.ArrayListUnmanaged([]const u8) = .{@as(std.ArrayListUnmanaged([]const u8), .{})} ** 256,

    fn indexTokens(self: *Checker) !void {
        var id: u32 = 0;
        while (id < self.tok.vocab_size) : (id += 1) {
            if (self.tok.is_special[id] or self.tok.is_eos[id]) continue;
            const bytes = self.tok.bytes[id];
            if (bytes.len == 0) continue;
            try self.w.charge(1);
            try self.by_first[bytes[0]].append(self.a, bytes);
        }
    }

    // DP over the sample bytes: reachable[i] = sample[0..i] is an exact
    // concatenation of vocabulary tokens.
    fn segmentable(self: *Checker, sample: []const u8) !bool {
        const reach = try self.a.alloc(bool, sample.len + 1);
        @memset(reach, false);
        reach[0] = true;
        var i: usize = 0;
        while (i < sample.len) : (i += 1) {
            if (!reach[i]) continue;
            for (self.by_first[sample[i]].items) |t| {
                if (i + t.len <= sample.len and std.mem.eql(u8, sample[i .. i + t.len], t))
                    reach[i + t.len] = true;
            }
        }
        return reach[sample.len];
    }

    fn covered(self: *Checker, node_id: grammar.NodeId) Error!bool {
        try self.w.charge(1);
        switch (self.g.node(node_id).*) {
            .literal => |lit| return self.segmentable(self.g.literalBytes(lit)),
            .choice => |alts| {
                for (alts) |alt| {
                    if (!try self.covered(alt)) return false;
                }
                return true;
            },
            .seq => |children| {
                for (children) |ch| {
                    if (!try self.covered(ch)) return false;
                }
                return true;
            },
            .repeat => |rep| {
                if (!try self.segmentable("[") or !try self.segmentable("]")) return false;
                if (rep.max >= 2 and !try self.segmentable(",")) return false;
                if (rep.max >= 1 and !try self.covered(rep.item)) return false;
                return true;
            },
            .object => |props| {
                if (!try self.segmentable("{") or !try self.segmentable("}")) return false;
                if (props.len >= 2 and !try self.segmentable(",")) return false;
                for (props) |p| {
                    if (!try self.segmentable(self.g.literalBytes(p.key))) return false;
                    if (!try self.covered(p.value)) return false;
                }
                return true;
            },
            .str => |sc| return self.strCovered(sc.min_len, sc.max_len),
            .int_v, .num_v => {
                var buf: [1]u8 = undefined;
                var d: u8 = '0';
                while (d <= '9') : (d += 1) {
                    buf[0] = d;
                    if (try self.segmentable(&buf)) return true;
                }
                return false;
            },
        }
    }

    // A string is producible if a closing sample quote+content+quote is
    // segmentable for some content length in min..min+8 (bounded by max)
    // and some printable fill byte. For min_len above MAX_STR_SAMPLE the
    // capped length is used as a surrogate.
    fn strCovered(self: *Checker, min_len: u32, max_len: u32) !bool {
        var upper = min_len +| 8;
        if (max_len != grammar.UNBOUNDED and max_len < upper) upper = max_len;
        if (min_len > MAX_STR_SAMPLE) {
            upper = MAX_STR_SAMPLE;
        }
        var l: u32 = @min(min_len, MAX_STR_SAMPLE);
        while (l <= upper) : (l += 1) {
            const sample = try self.a.alloc(u8, @as(usize, l) + 2);
            sample[0] = '"';
            sample[l + 1] = '"';
            var f: u8 = 0x20;
            while (f < 0x7F) : (f += 1) {
                if (f == '"' or f == '\\') continue;
                @memset(sample[1 .. l + 1], f);
                if (try self.segmentable(sample)) return true;
            }
        }
        return false;
    }
};

const testing = std.testing;

fn cov(a: std.mem.Allocator, g: *const grammar.Grammar, tk: *const tokenizer.Tokenizer) Error!bool {
    var w = work_mod.Work{};
    return checkCoverage(a, g, tk, &w);
}

fn litGrammar(a: std.mem.Allocator, s: []const u8) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addLiteralNode(s);
    return b.finish(arena, .literal_set, root, .{});
}

fn choiceGrammar(a: std.mem.Allocator, alts: []const []const u8) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const ids = try aa.alloc(grammar.NodeId, alts.len);
    for (alts, 0..) |s, i| ids[i] = try b.addLiteralNode(s);
    const root = try b.addNode(.{ .choice = try b.copyNodeIds(ids) });
    return b.finish(arena, .literal_set, root, .{});
}

fn objGrammar(a: std.mem.Allocator) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const int_node = try b.addNode(.{ .int_v = {} });
    const props = try b.copyProps(&[_]grammar.Prop{
        .{ .key = try b.addLiteral("\"a\":"), .value = int_node, .required = true },
    });
    const root = try b.addNode(.{ .object = props });
    return b.finish(arena, .json_schema, root, .{});
}

fn strGrammar(a: std.mem.Allocator, min: u32, max: u32) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addNode(.{ .str = .{ .min_len = min, .max_len = max } });
    return b.finish(arena, .json_schema, root, .{});
}

fn makeTok(a: std.mem.Allocator, toks: []const []const u8, eos: []const u32) !tokenizer.Tokenizer {
    const entries = try a.alloc(tokenizer.Entry, toks.len);
    defer a.free(entries);
    for (toks, 0..) |t, i| entries[i] = .{ .id = @intCast(i), .bytes = t };
    return tokenizer.Tokenizer.create(a, @intCast(toks.len), entries, eos, &.{});
}

fn byteTok(a: std.mem.Allocator) !tokenizer.Tokenizer {
    const entries = try a.alloc(tokenizer.Entry, 257);
    defer a.free(entries);
    const bufs = try a.alloc([1]u8, 256);
    defer a.free(bufs);
    for (0..256) |i| {
        bufs[i][0] = @intCast(i);
        entries[i] = .{ .id = @intCast(i), .bytes = &bufs[i] };
    }
    entries[256] = .{ .id = 256, .bytes = "" };
    return tokenizer.Tokenizer.create(a, 257, entries, &.{256}, &.{});
}

test "coverage: audit A1 case — literal ab with vocab [a, eos] is rejected" {
    const a = testing.allocator;
    var g = try litGrammar(a, "ab");
    defer g.deinit();
    var tk = try makeTok(a, &.{ "a", "" }, &.{1});
    defer tk.deinit();
    try testing.expect(!(try cov(a, &g, &tk)));
}

test "coverage: literal segmentation with single and multi tokens" {
    const a = testing.allocator;
    var g = try litGrammar(a, "ab");
    defer g.deinit();
    var tk1 = try makeTok(a, &.{ "a", "b", "" }, &.{2});
    defer tk1.deinit();
    try testing.expect(try cov(a, &g, &tk1));
    var tk2 = try makeTok(a, &.{ "ab", "" }, &.{1});
    defer tk2.deinit();
    try testing.expect(try cov(a, &g, &tk2));
}

test "coverage: every choice alternative must be covered" {
    const a = testing.allocator;
    var g = try choiceGrammar(a, &.{ "ab", "c" });
    defer g.deinit();
    var tk = try makeTok(a, &.{ "c", "" }, &.{1});
    defer tk.deinit();
    try testing.expect(!(try cov(a, &g, &tk)));
    var tk2 = try makeTok(a, &.{ "a", "b", "c", "" }, &.{3});
    defer tk2.deinit();
    try testing.expect(try cov(a, &g, &tk2));
}

test "coverage: object requires structural tokens" {
    const a = testing.allocator;
    var g = try objGrammar(a);
    defer g.deinit();
    var tk = try makeTok(a, &.{ "\"a\":", "1", "" }, &.{2});
    defer tk.deinit();
    try testing.expect(!(try cov(a, &g, &tk)));
    var tk2 = try makeTok(a, &.{ "{", "}", "\"a\":", "1", "" }, &.{4});
    defer tk2.deinit();
    try testing.expect(try cov(a, &g, &tk2));
}

test "coverage: string content must be producible up to min_len" {
    const a = testing.allocator;
    var g0 = try strGrammar(a, 0, grammar.UNBOUNDED);
    defer g0.deinit();
    var tk = try makeTok(a, &.{ "\"", "" }, &.{1});
    defer tk.deinit();
    try testing.expect(try cov(a, &g0, &tk));
    var g2 = try strGrammar(a, 2, 3);
    defer g2.deinit();
    try testing.expect(!(try cov(a, &g2, &tk)));
    var tk2 = try makeTok(a, &.{ "\"", "aa", "aaa", "" }, &.{3});
    defer tk2.deinit();
    try testing.expect(try cov(a, &g2, &tk2));
}

test "coverage: integer needs a digit token" {
    const a = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addNode(.{ .int_v = {} });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var tk = try makeTok(a, &.{ "x", "" }, &.{1});
    defer tk.deinit();
    try testing.expect(!(try cov(a, &g, &tk)));
    var tk2 = try makeTok(a, &.{ "5", "" }, &.{1});
    defer tk2.deinit();
    try testing.expect(try cov(a, &g, &tk2));
}

test "coverage: byte-complete vocabulary always passes" {
    const a = testing.allocator;
    var tk = try byteTok(a);
    defer tk.deinit();
    var g1 = try objGrammar(a);
    defer g1.deinit();
    try testing.expect(try cov(a, &g1, &tk));
    var g2 = try strGrammar(a, 5, grammar.UNBOUNDED);
    defer g2.deinit();
    try testing.expect(try cov(a, &g2, &tk));
    var g3 = try litGrammar(a, "\xf0\x9f\x98\x80");
    defer g3.deinit();
    try testing.expect(try cov(a, &g3, &tk));
}
