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

/// JSON string-content spelling of one codepoint for a coverage sample:
/// the canonical escapes where mandatory, raw UTF-8 otherwise.
fn appendSampleCp(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, cp: u21) !void {
    const hex = "0123456789abcdef";
    switch (cp) {
        '"' => try out.appendSlice(a, "\\\""),
        '\\' => try out.appendSlice(a, "\\\\"),
        0x08 => try out.appendSlice(a, "\\b"),
        0x09 => try out.appendSlice(a, "\\t"),
        0x0A => try out.appendSlice(a, "\\n"),
        0x0C => try out.appendSlice(a, "\\f"),
        0x0D => try out.appendSlice(a, "\\r"),
        else => {
            if (cp < 0x20) {
                try out.appendSlice(a, "\\u00");
                try out.append(a, hex[cp >> 4]);
                try out.append(a, hex[cp & 0xF]);
            } else {
                var buf: [4]u8 = undefined;
                const n = std.unicode.utf8Encode(cp, &buf) catch unreachable; // cp is a scalar value
                try out.appendSlice(a, buf[0..n]);
            }
        },
    }
}

/// Canonical spelling of a number constant (the shortest JSON number with
/// the same value): plain decimal, no exponent, no redundant zeros.
fn numConstSample(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, nc: grammar.NumConst) !void {
    if (nc.digits.len == 0) {
        try out.append(a, '0');
        return;
    }
    if (nc.neg) try out.append(a, '-');
    if (nc.exp10 >= 0) {
        try out.appendSlice(a, nc.digits);
        try out.appendNTimes(a, '0', @intCast(nc.exp10));
        return;
    }
    const pp: i64 = @as(i64, @intCast(nc.digits.len)) + nc.exp10;
    if (pp > 0) {
        const p: usize = @intCast(pp);
        try out.appendSlice(a, nc.digits[0..p]);
        try out.append(a, '.');
        try out.appendSlice(a, nc.digits[p..]);
        return;
    }
    try out.appendSlice(a, "0.");
    try out.appendNTimes(a, '0', @intCast(-pp));
    try out.appendSlice(a, nc.digits);
}

/// A value strictly inside the range of `nr` (spec-v1 P4, coverage sample
/// for num_range): the compile-time contradiction checks guarantee the
/// range is non-empty, so one of the candidates always qualifies; the
/// candidates are tried in cheap-to-general order and each is verified
/// with the exact decimal membership test before it is sampled. Returns
/// null only when no candidate qualifies (defensive: an unproducible arm).
fn numRangeWitness(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, nr: grammar.NumRange) !?void {
    const zero: grammar.NumConst = .{ .neg = false, .digits = "", .exp10 = 0 };
    if (grammar.numConstInRange(zero, nr)) {
        try numConstSample(out, a, zero);
        return {};
    }
    // An inclusive bound is itself a witness.
    if (nr.min) |m| {
        if (grammar.numConstInRange(m, nr)) {
            try numConstSample(out, a, m);
            return {};
        }
    }
    if (nr.max) |m| {
        if (grammar.numConstInRange(m, nr)) {
            try numConstSample(out, a, m);
            return {};
        }
    }
    // An exclusive bound: nudge one tenth of its ulp toward the interior.
    if (nr.min) |m| {
        if (try nudgedInRange(out, a, m, nr, true)) return {};
    }
    if (nr.max) |m| {
        if (try nudgedInRange(out, a, m, nr, false)) return {};
    }
    // Two exclusive bounds too close for the nudges: the exact midpoint is
    // strictly inside.
    if (nr.min != null and nr.max != null) {
        try midpointSample(out, a, nr.min.?, nr.max.?);
        return {};
    }
    return null;
}

/// Candidate one tenth of a ulp inside an exclusive bound: value(bound) +
/// sign x 10^(exp10-1) where sign points toward the range interior
/// (up for a lower bound, down for an upper one). Samples and returns true
/// when the candidate is in range; clears the buffer and returns false
/// otherwise.
fn nudgedInRange(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, b: grammar.NumConst, nr: grammar.NumRange, lower: bool) !bool {
    if (b.digits.len == 0) {
        // Zero bound: 1 above a lower bound, -1 above an upper bound in
        // magnitude (value -1 below an upper bound of 0).
        const c: grammar.NumConst = .{ .neg = !lower, .digits = "1", .exp10 = 0 };
        if (!grammar.numConstInRange(c, nr)) return false;
        try numConstSample(out, a, c);
        return true;
    }
    // Interior direction in VALUE: up for a lower bound, down for an
    // upper. Raising the magnitude moves a positive value up, a negative
    // one down.
    const mag_up = (lower and !b.neg) or (!lower and b.neg);
    var digits: std.ArrayListUnmanaged(u8) = .{};
    defer digits.deinit(a);
    if (mag_up) {
        // digits x 10 + 1 at exp10 - 1 == value + 10^(exp10-1).
        try digits.appendSlice(a, b.digits);
        try digits.append(a, '1');
    } else {
        // digits x 10 - 1 at exp10 - 1: canonical digits never end in '0',
        // so decrement the last digit and append '9'; a leading zero of
        // the result (digits == "1") is dropped (value unchanged).
        try digits.appendSlice(a, b.digits[0 .. b.digits.len - 1]);
        try digits.append(a, b.digits[b.digits.len - 1] - 1);
        try digits.append(a, '9');
        if (digits.items[0] == '0') {
            std.mem.copyForwards(u8, digits.items[0 .. digits.items.len - 1], digits.items[1..]);
            digits.items.len -= 1;
        }
    }
    const c: grammar.NumConst = .{ .neg = b.neg, .digits = digits.items, .exp10 = b.exp10 - 1 };
    if (!grammar.numConstInRange(c, nr)) {
        out.clearRetainingCapacity();
        return false;
    }
    try numConstSample(out, a, c);
    return true;
}

/// Exact midpoint of two canonical constants (strictly between them for
/// lo < hi): S = lo' + hi' over a common scale, S/2 (x5 and one scale
/// lower when S is odd). Big-integer arithmetic at coverage time only.
fn midpointSample(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, lo: grammar.NumConst, hi: grammar.NumConst) !void {
    const Managed = std.math.big.int.Managed;
    const scale = @min(lo.exp10, hi.exp10);
    var s = try Managed.initSet(a, 0);
    defer s.deinit();
    inline for (.{ lo, hi }) |nc| {
        var digits: std.ArrayListUnmanaged(u8) = .{};
        defer digits.deinit(a);
        if (nc.digits.len > 0) {
            try digits.appendSlice(a, nc.digits);
            try digits.appendNTimes(a, '0', @intCast(nc.exp10 - scale));
        }
        var t = try Managed.initSet(a, 0);
        defer t.deinit();
        t.setString(10, digits.items) catch |e| switch (e) {
            error.OutOfMemory => return error.OutOfMemory,
            else => unreachable, // digits and '0' padding only
        };
        if (nc.neg) t.negate();
        try s.add(&s, &t);
    }
    var q = try Managed.initSet(a, 0);
    defer q.deinit();
    var r = try Managed.initSet(a, 0);
    defer r.deinit();
    var two = try Managed.initSet(a, 2);
    defer two.deinit();
    try Managed.divTrunc(&q, &r, &s, &two);
    var e = scale;
    if (!r.eqlZero()) {
        var five = try Managed.initSet(a, 5);
        defer five.deinit();
        try s.mul(&s, &five);
        e -= 1;
    } else {
        try s.copy(q.toConst());
    }
    const str = s.toString(a, 10, .lower) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        else => unreachable, // base 10 is valid
    };
    defer a.free(str);
    // Canonicalize the decimal string back to a NumConst for the sample.
    var neg = false;
    var d = str;
    if (d.len > 0 and d[0] == '-') {
        neg = true;
        d = d[1..];
    }
    while (d.len > 0 and d[0] == '0') d = d[1..];
    var z: i64 = 0;
    while (d.len > 0 and d[d.len - 1] == '0') {
        d = d[0 .. d.len - 1];
        z += 1;
    }
    try numConstSample(out, a, .{ .neg = neg and d.len > 0, .digits = d, .exp10 = e + z });
}

pub const Error = error{ OutOfMemory, Cancelled, ResourceLimit };

pub fn checkCoverage(a: std.mem.Allocator, g: *const grammar.Grammar, tok: *const tokenizer.Tokenizer, w: *work_mod.Work) Error!bool {
    var arena = std.heap.ArenaAllocator.init(a);
    defer arena.deinit();
    // Node coverage is a pure per-node property; memoizing keeps DAG-shaped
    // grammars (spec-v1 AnyJSON unrolling shares the inner levels) linear
    // instead of exponential in the sharing depth. Canonical tree grammars
    // are unaffected.
    const memo = try arena.allocator().alloc(?bool, g.nodes.len);
    @memset(memo, null);
    var c = Checker{ .a = arena.allocator(), .g = g, .tok = tok, .w = w, .memo = memo };
    return c.covered(g.root);
}

const Checker = struct {
    a: std.mem.Allocator,
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    w: *work_mod.Work,
    memo: []?bool,

    // DP over the sample bytes: reachable[i] = sample[0..i] is an exact
    // concatenation of vocabulary tokens. Tokens matching at a position are
    // enumerated through the vocabulary trie (the same flat trie the mask
    // walk uses) instead of per-first-byte candidate lists. The former
    // indexTokens pass scanned the whole vocabulary on every compile -
    // ~815 us of ~1 ms for the 50k-token GPT-2 vocabulary - while the trie
    // already exists from tokenizer preparation and lookups are binary
    // searches over sorted edges.
    fn segmentable(self: *Checker, sample: []const u8) !bool {
        const reach = try self.a.alloc(bool, sample.len + 1);
        @memset(reach, false);
        reach[0] = true;
        var i: usize = 0;
        while (i < sample.len) : (i += 1) {
            if (!reach[i]) continue;
            try self.w.charge(1);
            var node: u32 = 0;
            var j = i;
            while (j < sample.len) : (j += 1) {
                const child = self.tok.trie.child(node, sample[j]) orelse break;
                node = child;
                if (self.tok.trie.node_token[node] != tokenizer.NO_TOKEN) reach[j + 1] = true;
            }
        }
        return reach[sample.len];
    }

    fn covered(self: *Checker, node_id: grammar.NodeId) Error!bool {
        try self.w.charge(1);
        if (self.memo[node_id]) |r| return r;
        const r = try self.coveredInner(node_id);
        self.memo[node_id] = r;
        return r;
    }

    fn coveredInner(self: *Checker, node_id: grammar.NodeId) Error!bool {
        switch (self.g.node(node_id).*) {
            .literal => |lit| return self.segmentable(self.g.literalBytes(lit)),
            .lit_trie => |lt| {
                for (lt.literals) |lit| {
                    if (!try self.segmentable(self.g.literalBytes(lit))) return false;
                }
                return true;
            },
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
            .object => |o| {
                if (!try self.segmentable("{") or !try self.segmentable("}")) return false;
                if (o.props.len >= 2 and !try self.segmentable(",")) return false;
                for (o.props) |p| {
                    if (!try self.segmentable(self.g.literalBytes(p.key))) return false;
                    if (!try self.covered(p.value)) return false;
                }
                return true;
            },
            .open_obj => |o| {
                if (!try self.segmentable("{") or !try self.segmentable("}")) return false;
                if (!try self.segmentable(",")) return false;
                // An undeclared key is `"` content `":`; any producible
                // string extends to a key.
                if (!try self.keyCovered()) return false;
                for (o.props) |p| {
                    if (!try self.segmentable(self.g.literalBytes(p.key))) return false;
                    if (!try self.covered(p.value)) return false;
                }
                return self.covered(o.value);
            },
            .str => |sc| {
                // min > max is the empty-language node (a `false` subschema
                // in spec-v1): it produces nothing, so it is vacuously
                // covered.
                if (sc.max_len != grammar.UNBOUNDED and sc.min_len > sc.max_len) return true;
                return self.strCovered(sc.min_len, sc.max_len);
            },
            .int_v, .num_v, .int_num, .not_int_num, .num_excl => {
                var buf: [1]u8 = undefined;
                var d: u8 = '0';
                while (d <= '9') : (d += 1) {
                    buf[0] = d;
                    if (try self.segmentable(&buf)) return true;
                }
                return false;
            },
            .num_range => |nr| {
                // A producible number within the bounds: sample witnesses
                // in preference order (zero, each present inclusive bound,
                // a value nudged inside an exclusive bound, the midpoint of
                // two exclusive bounds); the first in-range candidate is
                // the sample, exactly like num_const's own-digits sample.
                var buf: std.ArrayListUnmanaged(u8) = .{};
                defer buf.deinit(self.a);
                const w = try numRangeWitness(&buf, self.a, nr);
                if (w == null) return false; // no producible value in range
                return self.segmentable(buf.items);
            },
            .num_mult => |nm| {
                // Zero is always a multiple.
                _ = nm;
                return self.segmentable("0");
            },
            .num_const => |nc| {
                // The constant's own digits must be segmentable: sampling a
                // generic digit would hide a dead-end inside the constant.
                var buf: std.ArrayListUnmanaged(u8) = .{};
                defer buf.deinit(self.a);
                try numConstSample(&buf, self.a, nc);
                return self.segmentable(buf.items);
            },
            .comb => |cb| {
                // Boolean combinators (spec-v1 P3): conservatively require
                // every present branch covered.
                for (cb.branches) |br| {
                    if (br == grammar.COMB_NONE) continue;
                    if (!try self.covered(br)) return false;
                }
                return true;
            },
            .str_excl => |se| return self.strCovered(se.min_len, se.max_len),
            .str_pat => |sp| {
                // The min > max shape cannot occur (compile drops empty
                // arms); keep it vacuously covered for consistency.
                if (sp.max_len != grammar.UNBOUNDED and sp.min_len > sp.max_len) return true;
                return self.strPatCovered(sp);
            },
        }
    }

    // A regex-constrained string is producible if a WITNESS the pattern
    // DFA accepts within the length bounds is segmentable (spec-v1 P4):
    // the shortest accepting input from a DFA BFS, padded to min_len with
    // 'a' (search-mode accept states are sticky over the whole scalar
    // domain, so any padding keeps the string accepted). No witness within
    // MAX_STR_SAMPLE means no producible string: not covered.
    fn strPatCovered(self: *Checker, sp: grammar.StrPat) !bool {
        var path: std.ArrayListUnmanaged(u21) = .{};
        const d_min = (try sp.dfa.shortestAccept(self.a, &path)) orelse return false;
        var l: u32 = @max(d_min, sp.min_len);
        if (sp.max_len != grammar.UNBOUNDED and l > sp.max_len) return false;
        if (l > MAX_STR_SAMPLE) l = MAX_STR_SAMPLE;
        if (l < d_min) return false;
        var sample: std.ArrayListUnmanaged(u8) = .{};
        try sample.append(self.a, '"');
        for (path.items) |cp| try appendSampleCp(&sample, self.a, cp);
        var i: u32 = d_min;
        while (i < l) : (i += 1) try sample.append(self.a, 'a');
        try sample.append(self.a, '"');
        return self.segmentable(sample.items);
    }

    // An undeclared open-object key is producible if some sample
    // `"` fill `":` is segmentable (same sampling as strCovered).
    fn keyCovered(self: *Checker) !bool {
        var l: u32 = 0;
        while (l <= 8) : (l += 1) {
            const sample = try self.a.alloc(u8, @as(usize, l) + 3);
            sample[0] = '"';
            sample[l + 1] = '"';
            sample[l + 2] = ':';
            var f: u8 = 0x20;
            while (f < 0x7F) : (f += 1) {
                if (f == '"' or f == '\\') continue;
                @memset(sample[1 .. l + 1], f);
                if (try self.segmentable(sample)) return true;
            }
        }
        return false;
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
    const root = try b.addNode(.{ .object = .{ .props = props } });
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

test "coverage: literal ab with vocab [a, eos] is rejected" {
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
