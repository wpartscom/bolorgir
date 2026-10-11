//! Test-only C exports. Compiled in with `zig build -Dtest_hooks=true`;
//! release builds do not contain these symbols (checked in CI).

const std = @import("std");
const Arena = @import("alloc.zig").Arena;
const c_api = @import("c_api.zig");
const parser = @import("parser.zig");
const complete = @import("complete.zig");
const numcert = @import("numcert.zig");
const grammar = @import("grammar.zig");

pub const VERDICT_DEAD: u32 = 0;
pub const VERDICT_ALIVE: u32 = 1;
pub const VERDICT_UNDECIDED: u32 = 2;
pub const VERDICT_NOT_GROUP: u32 = 3;
pub const VERDICT_REJECTED: u32 = 4;

const ST_OK: u32 = 0;
const ST_INVALID_ARGUMENT: u32 = 1;
const ST_RESOURCE_LIMIT: u32 = 8;
const ST_BUFFER_TOO_SMALL: u32 = 12;

fn feedPrefix(g: *const grammar.Grammar, side: *parser.Side, a: std.mem.Allocator, prefix: []const u8) !?*parser.State {
    const st = try a.create(parser.State);
    st.* = try parser.initState(g, parser.MAX_THREADS_CAP, side);
    if (prefix.len == 0) return st;
    const out = try a.create(parser.State);
    const work = try a.create(parser.State);
    parser.feedBytes(g, side, st, prefix, out, work) catch |e| switch (e) {
        error.Parse => return null,
        else => |x| return x,
    };
    return out;
}

/// Joint numeric-group verdict (ADR-0011) at the state after `prefix`, and
/// for an alive verdict the witness continuation rendered against the
/// prefix (`prefix ++ wit` is a complete number satisfying the group).
export fn blg_test_num_cert(
    gh: ?*const c_api.GrammarHandle,
    prefix: ?[*]const u8,
    len: usize,
    out_verdict: ?*u32,
    wit_buf: ?[*]u8,
    wit_cap: usize,
    wit_len: ?*usize,
) callconv(.c) u32 {
    const h = gh orelse return ST_INVALID_ARGUMENT;
    const ov = out_verdict orelse return ST_INVALID_ARGUMENT;
    const wl = wit_len orelse return ST_INVALID_ARGUMENT;
    const pre: []const u8 = if (len == 0) &.{} else (prefix orelse return ST_INVALID_ARGUMENT)[0..len];
    var arena = Arena.init(std.heap.page_allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var side = parser.Side.init(a);
    const st = (feedPrefix(&h.g, &side, a, pre) catch return ST_RESOURCE_LIMIT) orelse {
        ov.* = VERDICT_REJECTED;
        wl.* = 0;
        return ST_OK;
    };
    var wit: numcert.Witness = .none;
    const lex = Lexeme.parse(pre) orelse return ST_INVALID_ARGUMENT;
    const v = complete.numGroupVerdictHinted(&h.g, st, a, &wit, lex.sigDigits()) orelse {
        ov.* = VERDICT_NOT_GROUP;
        wl.* = 0;
        return ST_OK;
    };
    ov.* = switch (v) {
        .dead => VERDICT_DEAD,
        .alive => VERDICT_ALIVE,
        .undecided => VERDICT_UNDECIDED,
    };
    wl.* = 0;
    if (v != .alive) return ST_OK;
    var out: std.ArrayListUnmanaged(u8) = .empty;
    lex.render(a, wit, &out) catch return ST_INVALID_ARGUMENT;
    if (out.items.len > wit_cap) return ST_BUFFER_TOO_SMALL;
    if (out.items.len > 0) @memcpy((wit_buf orelse return ST_INVALID_ARGUMENT)[0..out.items.len], out.items);
    wl.* = out.items.len;
    return ST_OK;
}

/// Bounded depth-first search over number-byte continuations of `prefix`
/// (at most `max_len` bytes, at most `max_exp_digits` exponent digits in
/// total), memoized on a 128-bit hash of the parser state image; no
/// certificate is consulted. out_found = 1 with the continuation when the
/// parser can end after it. Returns ST_RESOURCE_LIMIT once `max_nodes`
/// states were expanded (0 selects DEFAULT_NODE_BUDGET).
export fn blg_test_bounded_search(
    gh: ?*const c_api.GrammarHandle,
    prefix: ?[*]const u8,
    len: usize,
    max_len: u32,
    max_exp_digits: u32,
    max_nodes: u64,
    out_found: ?*u32,
    wit_buf: ?[*]u8,
    wit_cap: usize,
    wit_len: ?*usize,
) callconv(.c) u32 {
    const h = gh orelse return ST_INVALID_ARGUMENT;
    const of = out_found orelse return ST_INVALID_ARGUMENT;
    const wl = wit_len orelse return ST_INVALID_ARGUMENT;
    const pre: []const u8 = if (len == 0) &.{} else (prefix orelse return ST_INVALID_ARGUMENT)[0..len];
    var arena = Arena.init(std.heap.page_allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var side = parser.Side.init(a);
    const st = (feedPrefix(&h.g, &side, a, pre) catch return ST_RESOURCE_LIMIT) orelse {
        of.* = 0;
        wl.* = 0;
        return ST_OK;
    };
    const lex = Lexeme.parse(pre) orelse return ST_INVALID_ARGUMENT;
    var s: Search = .{
        .g = &h.g,
        .side = &side,
        .a = a,
        .max_exp = max_exp_digits,
        .budget = if (max_nodes == 0) DEFAULT_NODE_BUDGET else max_nodes,
        .in_exp0 = lex.frozen(),
    };
    defer s.seen.deinit(std.heap.page_allocator);
    s.states = a.alloc(*parser.State, max_len + 3) catch return ST_RESOURCE_LIMIT;
    for (s.states) |*p| p.* = a.create(parser.State) catch return ST_RESOURCE_LIMIT;
    s.path = a.alloc(u8, max_len) catch return ST_RESOURCE_LIMIT;
    const found = s.dfs(st, 0, max_len, lex.exp_digits) catch return ST_RESOURCE_LIMIT;
    of.* = @intFromBool(found != null);
    wl.* = 0;
    if (found) |n| {
        if (n > wit_cap) return ST_BUFFER_TOO_SMALL;
        if (n > 0) @memcpy((wit_buf orelse return ST_INVALID_ARGUMENT)[0..n], s.path[0..n]);
        wl.* = n;
    }
    return ST_OK;
}

// 'E' and an exponent '+' reach the same parser states as 'e' and no sign.
const ALPHABET = "0123456789.e-";
const DEFAULT_NODE_BUDGET: u64 = 200_000;

const Search = struct {
    g: *const grammar.Grammar,
    side: *parser.Side,
    a: std.mem.Allocator,
    max_exp: u32,
    budget: u64,
    in_exp0: bool,
    states: []*parser.State = &.{},
    path: []u8 = &.{},
    kbuf: std.ArrayListUnmanaged(u8) = .empty,
    seen: std.AutoHashMapUnmanaged(u128, u32) = .{},
    expanded: u64 = 0,

    /// Two independent 64-bit hashes of the state image, the chunk payloads
    /// it references and the exponent bookkeeping.
    fn key(self: *Search, st: *const parser.State, exp_digits: u32, in_exp: bool) !u128 {
        const len = parser.stateKeyLen(st);
        const blob = parser.chunkBlobLen(st, self.side);
        try self.kbuf.resize(self.a, len + blob + 5);
        const k = self.kbuf.items;
        parser.writeStateKey(st, k[0..len]);
        parser.writeChunkBlob(st, self.side, k[len .. len + blob]);
        std.mem.writeInt(u32, k[len + blob ..][0..4], exp_digits, .little);
        k[len + blob + 4] = @intFromBool(in_exp);
        const h1 = std.hash.Wyhash.hash(0x9e3779b97f4a7c15, k);
        const h2 = std.hash.Wyhash.hash(0xc2b2ae3d27d4eb4f, k);
        return (@as(u128, h1) << 64) | h2;
    }

    /// Length of a completing continuation, or null.
    fn dfs(self: *Search, st: *const parser.State, depth: usize, rem: u32, exp_digits: u32) !?usize {
        if (parser.canEnd(self.g, st)) return depth;
        if (rem == 0) return null;
        const in_exp = self.in_exp0 or std.mem.indexOfScalar(u8, self.path[0..depth], 'e') != null;
        const k = try self.key(st, exp_digits, in_exp);
        const gop = try self.seen.getOrPut(std.heap.page_allocator, k);
        if (gop.found_existing and gop.value_ptr.* >= rem) return null;
        gop.value_ptr.* = rem;
        self.expanded += 1;
        if (self.expanded > self.budget) return error.Budget;
        for (ALPHABET) |b| {
            var ed = exp_digits;
            if (in_exp and b >= '0' and b <= '9') {
                if (ed >= self.max_exp) continue;
                ed += 1;
            }
            const next = self.states[depth + 1];
            parser.feedBytes(self.g, self.side, st, &[_]u8{b}, next, self.states[depth + 2]) catch |e| switch (e) {
                error.Parse, error.ResourceLimit => continue,
                else => |x| return x,
            };
            self.path[depth] = b;
            if (try self.dfs(next, depth + 1, rem - 1, ed)) |n| return n;
        }
        return null;
    }
};

/// A number prefix split into its parts (test rendering of witnesses).
const Lexeme = struct {
    st: parser.NumState,
    neg: bool,
    int: []const u8,
    frac: []const u8,
    has_dot: bool,
    exp_sign: ?u8,
    exp: []const u8,
    exp_digits: u32,

    fn parse(p: []const u8) ?Lexeme {
        var l: Lexeme = .{ .st = .start, .neg = false, .int = "", .frac = "", .has_dot = false, .exp_sign = null, .exp = "", .exp_digits = 0 };
        var i: usize = 0;
        if (i < p.len and p[i] == '-') {
            l.neg = true;
            l.st = .minus;
            i += 1;
        }
        const is = i;
        while (i < p.len and std.ascii.isDigit(p[i])) i += 1;
        l.int = p[is..i];
        if (l.int.len > 0) l.st = if (l.int[0] == '0') .zero_complete else .int_digits;
        if (i < p.len and p[i] == '.') {
            l.has_dot = true;
            l.st = .dot;
            i += 1;
            const fs = i;
            while (i < p.len and std.ascii.isDigit(p[i])) i += 1;
            l.frac = p[fs..i];
            if (l.frac.len > 0) l.st = .frac_digits;
        }
        if (i < p.len and (p[i] == 'e' or p[i] == 'E')) {
            l.st = .exp;
            i += 1;
            if (i < p.len and (p[i] == '+' or p[i] == '-')) {
                l.exp_sign = p[i];
                l.st = .exp_sign;
                i += 1;
            }
            const es = i;
            while (i < p.len and std.ascii.isDigit(p[i])) i += 1;
            l.exp = p[es..i];
            l.exp_digits = @intCast(l.exp.len);
            if (l.exp.len > 0) l.st = .exp_digits;
        }
        if (i != p.len) return null;
        return l;
    }

    fn mantissa(self: Lexeme, a: std.mem.Allocator) ![]u8 {
        return std.mem.concat(a, u8, &.{ self.int, self.frac });
    }

    /// Order of magnitude of the leading significant digit as typed.
    fn omTyped(self: Lexeme, extra_int: usize) ?i64 {
        for (self.int, 0..) |c, i| {
            if (c != '0') return @as(i64, @intCast(self.int.len + extra_int)) - 1 - @as(i64, @intCast(i));
        }
        for (self.frac, 0..) |c, i| {
            if (c != '0') return -(@as(i64, @intCast(i)) + 1);
        }
        return null;
    }

    fn significant(self: Lexeme, a: std.mem.Allocator) ![]const u8 {
        const m = try self.mantissa(a);
        var s: []const u8 = m;
        while (s.len > 0 and s[0] == '0') s = s[1..];
        return s;
    }

    fn sigDigits(self: Lexeme) ?i64 {
        var n: i64 = 0;
        var lead = false;
        for (self.int) |c| {
            if (c != '0') lead = true;
            if (lead) n += 1;
        }
        for (self.frac) |c| {
            if (c != '0') lead = true;
            if (lead) n += 1;
        }
        return if (lead) n else null;
    }

    fn frozen(self: Lexeme) bool {
        return self.st == .exp or self.st == .exp_sign or self.st == .exp_digits;
    }

    fn appendExp(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, e: i64) !void {
        try out.print(a, "e{d}", .{e});
    }

    /// Exponent digits to append so the exponent reaches `e`.
    fn renderFrozen(self: Lexeme, a: std.mem.Allocator, e: i64, out: *std.ArrayListUnmanaged(u8)) !void {
        const mag: u64 = @abs(e);
        switch (self.st) {
            .exp => try out.print(a, "{d}", .{e}),
            .exp_sign => {
                if ((e < 0) != (self.exp_sign.? == '-') and e != 0) return error.Mismatch;
                try out.print(a, "{d}", .{mag});
            },
            .exp_digits => {
                const neg = self.exp_sign != null and self.exp_sign.? == '-';
                if ((e < 0) != neg and e != 0) return error.Mismatch;
                var typed: []const u8 = self.exp;
                while (typed.len > 0 and typed[0] == '0') typed = typed[1..];
                const s = try std.fmt.allocPrint(a, "{d}", .{mag});
                if (typed.len == 0) {
                    try out.appendSlice(a, s);
                } else {
                    if (!std.mem.startsWith(u8, s, typed)) return error.Mismatch;
                    try out.appendSlice(a, s[typed.len..]);
                }
            },
            else => unreachable,
        }
    }

    fn render(self: Lexeme, a: std.mem.Allocator, w: numcert.Witness, out: *std.ArrayListUnmanaged(u8)) !void {
        switch (w) {
            .none => return error.NoWitness,
            .as_is => {},
            .any, .zero => switch (self.st) {
                .start, .minus, .dot, .exp, .exp_sign => try out.append(a, '0'),
                else => {},
            },
            .frozen => |f| try self.renderFrozen(a, f.e, out),
            .ext => |x| {
                const om_m = self.omTyped(0) orelse return error.Mismatch;
                const digits = try std.fmt.allocPrint(a, "{d}", .{x.x});
                if (digits.len > x.w and !(x.w == 0 and x.x == 0)) return error.Mismatch;
                if (x.w > 0) {
                    try out.appendNTimes(a, '0', x.w - digits.len);
                    try out.appendSlice(a, digits);
                } else if (self.st == .dot) {
                    try out.append(a, '0');
                }
                const in_int = self.st == .int_digits;
                const om_after = if (in_int) om_m + @as(i64, x.w) else om_m;
                const e: i64 = switch (x.place) {
                    .om => |t| t - om_after,
                    .big => 4000,
                    .small => -4000,
                };
                try appendExp(out, a, e);
            },
            .value => |v| {
                if (v.digits.len == 0) return self.render(a, .zero, out);
                const om_v = v.exp10 + @as(i64, @intCast(v.digits.len)) - 1;
                if (self.frozen()) {
                    const p = try self.significant(a);
                    var ps: []const u8 = p;
                    while (ps.len > 0 and ps[ps.len - 1] == '0') ps = ps[0 .. ps.len - 1];
                    if (!std.mem.eql(u8, ps, v.digits) or v.neg != self.neg) return error.Mismatch;
                    const om_m = self.omTyped(0) orelse return error.Mismatch;
                    return self.renderFrozen(a, om_v - om_m, out);
                }
                const p = try self.significant(a);
                if (p.len == 0) {
                    switch (self.st) {
                        .start => {
                            if (v.neg) try out.append(a, '-');
                            try out.appendSlice(a, v.digits);
                            return appendExp(out, a, om_v - (@as(i64, @intCast(v.digits.len)) - 1));
                        },
                        .minus => {
                            if (!v.neg) return error.Mismatch;
                            try out.appendSlice(a, v.digits);
                            return appendExp(out, a, om_v - (@as(i64, @intCast(v.digits.len)) - 1));
                        },
                        .zero_complete => {
                            if (v.neg != self.neg) return error.Mismatch;
                            try out.append(a, '.');
                            try out.appendSlice(a, v.digits);
                            return appendExp(out, a, om_v + 1);
                        },
                        .dot, .frac_digits => {
                            if (v.neg != self.neg) return error.Mismatch;
                            try out.appendSlice(a, v.digits);
                            return appendExp(out, a, om_v + @as(i64, @intCast(self.frac.len)) + 1);
                        },
                        else => return error.Mismatch,
                    }
                }
                if (v.neg != self.neg) return error.Mismatch;
                // v's digits, zero-padded, must extend the typed digits.
                const n = p.len;
                var padded: std.ArrayListUnmanaged(u8) = .empty;
                try padded.appendSlice(a, v.digits);
                while (padded.items.len < n) try padded.append(a, '0');
                if (!std.mem.eql(u8, padded.items[0..n], p)) return error.Mismatch;
                const rest = padded.items[n..];
                try out.appendSlice(a, rest);
                if (rest.len == 0 and self.st == .dot) try out.append(a, '0');
                const om_m = self.omTyped(0).?;
                const om_after = if (self.st == .int_digits) om_m + @as(i64, @intCast(rest.len)) else om_m;
                try appendExp(out, a, om_v - om_after);
            },
        }
    }
};

/// Text image of the parser state after `prefix` (diagnostics): one line
/// per thread, frames bottom to top with their node, kind and phase; comb
/// frames name the combinator, the instance and the branch.
export fn blg_test_dump_state(
    gh: ?*const c_api.GrammarHandle,
    prefix: ?[*]const u8,
    len: usize,
    buf: ?[*]u8,
    cap: usize,
    out_len: ?*usize,
) callconv(.c) u32 {
    const h = gh orelse return ST_INVALID_ARGUMENT;
    const ol = out_len orelse return ST_INVALID_ARGUMENT;
    const pre: []const u8 = if (len == 0) &.{} else (prefix orelse return ST_INVALID_ARGUMENT)[0..len];
    var arena = Arena.init(std.heap.page_allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var side = parser.Side.init(a);
    var out: std.ArrayListUnmanaged(u8) = .empty;
    var aw: std.Io.Writer.Allocating = .fromArrayListAligned(a, .of(u8), &out);
    const w = &aw.writer;
    const st = (feedPrefix(&h.g, &side, a, pre) catch return ST_RESOURCE_LIMIT) orelse {
        ol.* = 0;
        return ST_OK;
    };
    for (st.threads[0..st.n], 0..) |*t, ti| {
        w.print("t{d}:", .{ti}) catch return ST_RESOURCE_LIMIT;
        for (t.frames[0..t.len]) |*f| {
            (switch (f.*) {
                .comb => |c| w.print(" comb#{d}({s} i{d} b{d})", .{ c.node, @tagName(h.g.node(c.node).comb.kind), c.inst, c.branch }),
                .object => |o| w.print(" object#{d}({s} idx{d} cur{d})", .{ o.node, @tagName(o.phase), o.idx, o.cur }),
                .open_obj => |o| w.print(" open_obj#{d}({s} next{d})", .{ o.node, @tagName(o.phase), o.next_idx }),
                .repeat => |r| w.print(" repeat#{d}({s} n{d})", .{ r.node, @tagName(r.phase), r.count }),
                .seq => |q| w.print(" seq#{d}(i{d})", .{ q.node, q.idx }),
                .str_pat => |s| w.print(" str_pat#{d}({s} n{d} q{d})", .{ s.node, @tagName(s.state), s.count, s.dfa }),
                .str => |s| w.print(" str#{d}({s} n{d})", .{ s.node, @tagName(s.state), s.count }),
                else => w.print(" {s}", .{@tagName(f.*)}),
            }) catch return ST_RESOURCE_LIMIT;
        }
        w.writeByte('\n') catch return ST_RESOURCE_LIMIT;
    }
    out = aw.toArrayListAligned(.of(u8));
    if (out.items.len > cap) return ST_BUFFER_TOO_SMALL;
    if (out.items.len > 0) @memcpy((buf orelse return ST_INVALID_ARGUMENT)[0..out.items.len], out.items);
    ol.* = out.items.len;
    return ST_OK;
}

/// Test overrides of the reachability check (complete.TestOverrides):
/// per-fill search budget and first tier budget (0 = default), and a
/// forced UNKNOWN verdict for states with a top frame of kind `unknown_tag`
/// (blg_test_frame_tag; -1 = none) or with key hash `unknown_key` (0 =
/// none, see blg_test_state_key).
export fn blg_test_set_overrides(fill_budget: u64, tier_budget: u64, unknown_tag: i32, unknown_key: u64) callconv(.c) u32 {
    const Tag = std.meta.Tag(parser.Frame);
    var kind: ?Tag = null;
    if (unknown_tag >= 0) {
        kind = std.enums.fromInt(Tag, unknown_tag) orelse return ST_INVALID_ARGUMENT;
    }
    complete.test_overrides = .{
        .fill_budget = fill_budget,
        .tier_budget = tier_budget,
        .unknown_kind = kind,
        .unknown_key = unknown_key,
    };
    return ST_OK;
}

/// Ordinal of the parser frame kind `name` (e.g. "str"), -1 when unknown.
export fn blg_test_frame_tag(name: ?[*:0]const u8) callconv(.c) i32 {
    const n = std.mem.span(name orelse return -1);
    const tag = std.meta.stringToEnum(std.meta.Tag(parser.Frame), n) orelse return -1;
    return @backingInt(tag);
}

/// complete.stateKeyHash of the state after `prefix` (0 when the prefix
/// is refused).
export fn blg_test_state_key(gh: ?*const c_api.GrammarHandle, prefix: ?[*]const u8, len: usize, out: ?*u64) callconv(.c) u32 {
    const h = gh orelse return ST_INVALID_ARGUMENT;
    const o = out orelse return ST_INVALID_ARGUMENT;
    const pre: []const u8 = if (len == 0) &.{} else (prefix orelse return ST_INVALID_ARGUMENT)[0..len];
    var arena = Arena.init(std.heap.page_allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var side = parser.Side.init(a);
    const st = (feedPrefix(&h.g, &side, a, pre) catch return ST_RESOURCE_LIMIT) orelse {
        o.* = 0;
        return ST_OK;
    };
    o.* = complete.stateKeyHash(st, a) orelse return ST_RESOURCE_LIMIT;
    return ST_OK;
}
