//! Exact value-level tools for the residual certifier and the
//! non-emptiness analysis (ADR-0005 D3 certificate coverage).
//!
//! Three primitives over the grammar's value-language fragment:
//!   - acceptsValue: does a node's language contain this exact JSON value
//!     (a mini-validator; exact for the fragment, unknown outside it);
//!   - witness: construct SOME value of a node's language, verified by
//!     acceptsValue before it is returned (construction is best-effort,
//!     verification is exact, so a returned witness is always sound);
//!   - disjoint: exact disjointness proofs for the simple shapes (type
//!     partition, const-key discrimination, finite sets).
//!
//! These serve: the non-emptiness fixpoint (grammar.zig), the contains
//! slot intersection (complete.zig certRepeat), and the comb group vote
//! certificates (complete.zig). Everything here is pure analysis: no
//! parser state, no search budget. Depth caps keep recursion bounded;
//! hitting one reports unknown/null, never a guess.

const std = @import("std");
const grammar = @import("grammar.zig");
const json = @import("json.zig");
const pattern = @import("pattern.zig");

pub const Tri = enum { yes, no, unknown };

/// The slice of a grammar these tools read. grammar.zig computes its
/// non-emptiness fixpoint before the Grammar value exists, so the view is
/// the node table plus the literal pool rather than *const Grammar.
pub const View = struct {
    ns: []const grammar.Node,
    pool: []const u8,

    pub fn node(self: View, id: grammar.NodeId) *const grammar.Node {
        return &self.ns[id];
    }

    pub fn literalBytes(self: View, lit: grammar.Literal) []const u8 {
        return self.pool[lit.off .. lit.off + lit.len];
    }
};

pub fn viewOf(g: *const grammar.Grammar) View {
    return .{ .ns = g.nodes, .pool = g.literal_pool };
}

pub const Entry = struct { key: []const u8, value: Val };

/// A JSON value. Numbers are canonical decimals (grammar.NumConst);
/// strings and object keys are decoded UTF-8 bytes.
pub const Val = union(enum) {
    nul,
    boolean: bool,
    num: grammar.NumConst,
    str: []const u8,
    arr: []const Val,
    obj: []const Entry,
};

const MAX_DEPTH: u32 = 32;
const MAX_SEEDS: usize = 128; // raw seed cap per comb
const MAX_VARIANTS: usize = 8; // optional-prop variants per open_obj seed
const MAX_CAND: usize = 64; // verified candidates kept per comb
const MAX_MERGES: usize = 256; // pairwise merge attempts per allof

/// Structural JSON equality: numbers by value, object key order
/// insignificant (both sides are duplicate-free by construction).
pub fn valEq(a: Val, b: Val) bool {
    switch (a) {
        .nul => return b == .nul,
        .boolean => |x| return b == .boolean and b.boolean == x,
        .num => |x| return b == .num and grammar.cmpNumConst(x, b.num) == .eq,
        .str => |x| return b == .str and std.mem.eql(u8, x, b.str),
        .arr => |xs| {
            if (b != .arr or b.arr.len != xs.len) return false;
            for (xs, b.arr) |x, y| {
                if (!valEq(x, y)) return false;
            }
            return true;
        },
        .obj => |xs| {
            if (b != .obj or b.obj.len != xs.len) return false;
            for (xs) |e| {
                const other = objGet(b.obj, e.key) orelse return false;
                if (!valEq(e.value, other)) return false;
            }
            return true;
        },
    }
}

pub fn objGet(entries: []const Entry, key: []const u8) ?Val {
    for (entries) |e| {
        if (std.mem.eql(u8, e.key, key)) return e.value;
    }
    return null;
}

/// The value is an integer (canonical decimal: zero, or no fractional part).
pub fn numIsInt(nc: grammar.NumConst) bool {
    return nc.digits.len == 0 or nc.exp10 >= 0;
}

/// numConst from a small integer (canonical form).
pub fn numFromI64(a: std.mem.Allocator, x: i64) !grammar.NumConst {
    if (x == 0) return .{ .neg = false, .digits = "", .exp10 = 0 };
    var buf: [24]u8 = undefined;
    const s = std.fmt.bufPrint(&buf, "{d}", .{@abs(x)}) catch unreachable;
    return numFromDigits(a, x < 0, s, 0);
}

/// Canonicalize sign/digits*10^exp10 (strip trailing zeros into exp10,
/// drop leading zeros, drop the sign of zero). digits must be ASCII
/// decimal without a sign.
pub fn numFromDigits(a: std.mem.Allocator, neg: bool, digits: []const u8, exp10: i64) !grammar.NumConst {
    var d = digits;
    var e = exp10;
    while (d.len > 0 and d[0] == '0') d = d[1..];
    while (d.len > 0 and d[d.len - 1] == '0') {
        d = d[0 .. d.len - 1];
        e += 1;
    }
    if (d.len == 0) return .{ .neg = false, .digits = "", .exp10 = 0 };
    const owned = try a.dupe(u8, d);
    return .{ .neg = neg, .digits = owned, .exp10 = e };
}

/// Increment a decimal digit string (assumed no leading zeros, non-empty).
fn addOneToDigits(a: std.mem.Allocator, d: []const u8) ![]const u8 {
    const out = try a.dupe(u8, d);
    var i = out.len;
    while (i > 0) {
        i -= 1;
        if (out[i] != '9') {
            out[i] += 1;
            return out;
        }
        out[i] = '0';
    }
    const grown = try a.alloc(u8, out.len + 1);
    grown[0] = '1';
    @memcpy(grown[1..], out);
    return grown;
}

/// Decrement a positive decimal digit string (result may gain a leading
/// zero; callers canonicalize via numFromDigits).
fn subOneFromDigits(a: std.mem.Allocator, d: []const u8) ![]const u8 {
    const out = try a.dupe(u8, d);
    var i = out.len;
    while (i > 0) {
        i -= 1;
        if (out[i] != '0') {
            out[i] -= 1;
            return out;
        }
        out[i] = '9';
    }
    return out; // was "0..0": all nines now; canonicalization drops it
}

/// Magnitude compare of two unsigned decimal digit strings (leading zeros
/// tolerated on either side).
fn cmpMagDigits(x0: []const u8, y0: []const u8) std.math.Order {
    var x = x0;
    var y = y0;
    while (x.len > 0 and x[0] == '0') x = x[1..];
    while (y.len > 0 and y[0] == '0') y = y[1..];
    if (x.len != y.len) return if (x.len < y.len) .lt else .gt;
    for (x, y) |cx, cy| {
        if (cx != cy) return if (cx < cy) .lt else .gt;
    }
    return .eq;
}

fn addMagDigits(a: std.mem.Allocator, x: []const u8, y: []const u8) ![]const u8 {
    const n = @max(x.len, y.len) + 1;
    const out = try a.alloc(u8, n);
    @memset(out, '0');
    var carry: u8 = 0;
    var i: usize = 0;
    while (i < n) : (i += 1) {
        const dx: u8 = if (i < x.len) x[x.len - 1 - i] - '0' else 0;
        const dy: u8 = if (i < y.len) y[y.len - 1 - i] - '0' else 0;
        const s = dx + dy + carry;
        out[n - 1 - i] = '0' + s % 10;
        carry = s / 10;
    }
    return out;
}

/// x - y for digit strings with x >= y (equal scale).
fn subMagDigits(a: std.mem.Allocator, x: []const u8, y: []const u8) ![]const u8 {
    const n = x.len;
    const out = try a.alloc(u8, n);
    var borrow: i16 = 0;
    var i: usize = 0;
    while (i < n) : (i += 1) {
        const dx: i16 = x[n - 1 - i] - '0';
        const dy: i16 = if (i < y.len) y[y.len - 1 - i] - '0' else 0;
        var d = dx - dy - borrow;
        if (d < 0) {
            d += 10;
            borrow = 1;
        } else borrow = 0;
        out[n - 1 - i] = @intCast('0' + @as(u8, @intCast(d)));
    }
    return out;
}

/// v + 10^k, exact, canonicalized. General signed decimal addition with
/// the operands aligned on a common scale.
pub fn numPlusPower(a: std.mem.Allocator, v: grammar.NumConst, k: i64) !grammar.NumConst {
    const e = @min(v.exp10, k);
    // Digits of v at scale e: v.digits followed by (v.exp10 - e) zeros.
    const vshift: usize = @intCast(v.exp10 - e);
    const vd = try a.alloc(u8, v.digits.len + vshift);
    defer a.free(vd);
    @memcpy(vd[0..v.digits.len], v.digits);
    @memset(vd[v.digits.len..], '0');
    // 10^k at scale e: "1" followed by (k - e) zeros.
    const pshift: usize = @intCast(k - e);
    const pd = try a.alloc(u8, 1 + pshift);
    defer a.free(pd);
    pd[0] = '1';
    @memset(pd[1..], '0');
    if (!v.neg) {
        return numFromDigits(a, false, try addMagDigits(a, vd, pd), e);
    }
    switch (cmpMagDigits(vd, pd)) {
        .gt => return numFromDigits(a, true, try subMagDigits(a, vd, pd), e),
        .lt => return numFromDigits(a, false, try subMagDigits(a, pd, vd), e),
        .eq => return .{ .neg = false, .digits = "", .exp10 = 0 },
    }
}

/// Smallest integer >= v (exact decimal ceiling).
pub fn ceilInt(a: std.mem.Allocator, v: grammar.NumConst) !grammar.NumConst {
    if (v.digits.len == 0) return .{ .neg = false, .digits = "", .exp10 = 0 };
    if (v.exp10 >= 0) return v; // already integral
    const frac: usize = @intCast(-v.exp10);
    if (frac >= v.digits.len) {
        // |v| < 1
        return if (v.neg) .{ .neg = false, .digits = "", .exp10 = 0 } else numFromI64(a, 1);
    }
    const ip = v.digits[0 .. v.digits.len - frac];
    const fp = v.digits[v.digits.len - frac ..];
    var has_frac = false;
    for (fp) |c| {
        if (c != '0') has_frac = true;
    }
    if (v.neg) return numFromDigits(a, true, ip, 0); // -i.ff ceilings to -i
    if (!has_frac) return numFromDigits(a, false, ip, 0);
    return numFromDigits(a, false, try addOneToDigits(a, ip), 0);
}

/// Largest integer <= v (exact decimal floor).
pub fn floorInt(a: std.mem.Allocator, v: grammar.NumConst) !grammar.NumConst {
    return negated(try ceilInt(a, negated(v)));
}

pub fn negated(v: grammar.NumConst) grammar.NumConst {
    if (v.digits.len == 0) return v;
    return .{ .neg = !v.neg, .digits = v.digits, .exp10 = v.exp10 };
}

/// Some integer within the (possibly exclusive) bounds, or null when the
/// bounded interval contains none. Unbounded sides are free.
pub fn intInRange(a: std.mem.Allocator, nr: grammar.NumRange) !?grammar.NumConst {
    var lo: ?grammar.NumConst = null;
    if (nr.min) |mn| {
        const c = try ceilInt(a, mn);
        lo = if (nr.min_excl and grammar.cmpNumConst(c, mn) == .eq)
            try numPlusPower(a, c, 0) // c + 1
        else
            c;
    }
    var hi: ?grammar.NumConst = null;
    if (nr.max) |mx| {
        const f = try floorInt(a, mx);
        hi = if (nr.max_excl and grammar.cmpNumConst(f, mx) == .eq)
            negated(try numPlusPower(a, negated(f), 0)) // f - 1
        else
            f;
    }
    if (lo != null and hi != null and grammar.cmpNumConst(lo.?, hi.?) == .gt) return null;
    if (lo) |l| return l;
    if (hi) |h| return h;
    return try numFromI64(a, 0);
}

/// Some number within the bounds (exclusive handled by a small decimal
/// step), or null when empty. Compile guarantees non-empty ranges, so null
/// here means the exact check disagrees - treated as unknown by callers.
pub fn numInRange(a: std.mem.Allocator, nr: grammar.NumRange) !?grammar.NumConst {
    const zero = try numFromI64(a, 0);
    if (grammar.numConstInRange(zero, nr)) return zero;
    if (nr.min) |mn| {
        if (!nr.min_excl and grammar.numConstInRange(mn, nr)) return mn;
        const up = try numPlusPower(a, mn, mn.exp10 - @as(i64, @intCast(mn.digits.len)) - 1);
        if (grammar.numConstInRange(up, nr)) return up;
    }
    if (nr.max) |mx| {
        if (!nr.max_excl and grammar.numConstInRange(mx, nr)) return mx;
        const dn = try numPlusPower(a, mx, mx.exp10 - @as(i64, @intCast(mx.digits.len)) - 1);
        if (grammar.numConstInRange(dn, nr)) return dn;
    }
    if (try intInRange(a, nr)) |iv| {
        if (grammar.numConstInRange(iv, nr)) return iv;
    }
    return null;
}

// ---------------------------------------------------------------------------
// Canonical spelling / literal parsing
// ---------------------------------------------------------------------------

/// The canonical quoted spelling of a decoded string (the schema compiler's
/// escape table: \" \\ \b \t \n \f \r, \u00XX for other C0 controls).
pub fn appendEscaped(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, s: []const u8) !void {
    const hex = "0123456789abcdef";
    for (s) |b| {
        switch (b) {
            '"' => try out.appendSlice(a, "\\\""),
            '\\' => try out.appendSlice(a, "\\\\"),
            0x08 => try out.appendSlice(a, "\\b"),
            0x09 => try out.appendSlice(a, "\\t"),
            0x0A => try out.appendSlice(a, "\\n"),
            0x0C => try out.appendSlice(a, "\\f"),
            0x0D => try out.appendSlice(a, "\\r"),
            else => {
                if (b < 0x20) {
                    try out.appendSlice(a, "\\u00");
                    try out.append(a, hex[b >> 4]);
                    try out.append(a, hex[b & 0xF]);
                } else {
                    try out.append(a, b);
                }
            },
        }
    }
}

/// Canonical spelling of a value (compact JSON, keys in entry order,
/// numbers in plain decimal notation). Literal nodes only ever carry
/// strings/booleans/null/empty containers/punctuation, so the number and
/// container forms exist for completeness of comparisons.
pub fn spell(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, v: Val) !void {
    switch (v) {
        .nul => try out.appendSlice(a, "null"),
        .boolean => |b| try out.appendSlice(a, if (b) "true" else "false"),
        .num => |nc| try spellNum(out, a, nc),
        .str => |s| {
            try out.append(a, '"');
            try appendEscaped(out, a, s);
            try out.append(a, '"');
        },
        .arr => |xs| {
            try out.append(a, '[');
            for (xs, 0..) |x, i| {
                if (i > 0) try out.append(a, ',');
                try spell(out, a, x);
            }
            try out.append(a, ']');
        },
        .obj => |xs| {
            try out.append(a, '{');
            for (xs, 0..) |e, i| {
                if (i > 0) try out.append(a, ',');
                try out.append(a, '"');
                try appendEscaped(out, a, e.key);
                try out.appendSlice(a, "\":");
                try spell(out, a, e.value);
            }
            try out.append(a, '}');
        },
    }
}

fn spellNum(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, nc: grammar.NumConst) !void {
    if (nc.digits.len == 0) return out.appendSlice(a, "0");
    if (nc.neg) try out.append(a, '-');
    const n = nc.digits.len;
    if (nc.exp10 >= 0) {
        try out.appendSlice(a, nc.digits);
        try out.appendNTimes(a, '0', @intCast(nc.exp10));
    } else if (-nc.exp10 < @as(i64, @intCast(n))) {
        const ip: usize = @intCast(@as(i64, @intCast(n)) + nc.exp10);
        try out.appendSlice(a, nc.digits[0..ip]);
        try out.append(a, '.');
        try out.appendSlice(a, nc.digits[ip..]);
    } else {
        try out.appendSlice(a, "0.");
        try out.appendNTimes(a, '0', @intCast(-nc.exp10 - @as(i64, @intCast(n))));
        try out.appendSlice(a, nc.digits);
    }
}

/// Parse a literal node's bytes into a value. Null when the literal is not
/// a self-contained value (punctuation of a const container seq) or the
/// canonical spelling of the value differs from the literal bytes (the
/// caller's verification catches that case anyway).
pub fn parseLiteral(a: std.mem.Allocator, bytes: []const u8) !?Val {
    if (std.mem.eql(u8, bytes, "null")) return Val.nul;
    if (std.mem.eql(u8, bytes, "true")) return Val{ .boolean = true };
    if (std.mem.eql(u8, bytes, "false")) return Val{ .boolean = false };
    if (std.mem.eql(u8, bytes, "[]")) return Val{ .arr = &.{} };
    if (std.mem.eql(u8, bytes, "{}")) return Val{ .obj = &.{} };
    if (bytes.len >= 2 and bytes[0] == '"' and bytes[bytes.len - 1] == '"') {
        return Val{ .str = try decodeEscaped(a, bytes[1 .. bytes.len - 1]) };
    }
    if (bytes.len > 0 and (bytes[0] == '-' or std.ascii.isDigit(bytes[0]))) {
        if (try parseNumBytes(a, bytes)) |nc| return Val{ .num = nc };
    }
    return null;
}

/// Parse a JSON number spelling into canonical decimal form. Null when the
/// bytes are not a JSON number.
pub fn parseNumBytes(a: std.mem.Allocator, bytes: []const u8) !?grammar.NumConst {
    var i: usize = 0;
    var neg = false;
    if (i < bytes.len and bytes[i] == '-') {
        neg = true;
        i += 1;
    }
    const int_start = i;
    while (i < bytes.len and std.ascii.isDigit(bytes[i])) i += 1;
    if (i == int_start) return null;
    var digits = std.ArrayListUnmanaged(u8){};
    defer digits.deinit(a);
    try digits.appendSlice(a, bytes[int_start..i]);
    var exp10: i64 = 0;
    if (i < bytes.len and bytes[i] == '.') {
        i += 1;
        const fs = i;
        while (i < bytes.len and std.ascii.isDigit(bytes[i])) i += 1;
        if (i == fs) return null;
        exp10 -= @intCast(i - fs);
        try digits.appendSlice(a, bytes[fs..i]);
    }
    if (i < bytes.len and (bytes[i] == 'e' or bytes[i] == 'E')) {
        i += 1;
        var eneg = false;
        if (i < bytes.len and (bytes[i] == '+' or bytes[i] == '-')) {
            eneg = bytes[i] == '-';
            i += 1;
        }
        const es = i;
        while (i < bytes.len and std.ascii.isDigit(bytes[i])) i += 1;
        if (i == es) return null;
        const ev = std.fmt.parseInt(i64, bytes[es..i], 10) catch return null;
        exp10 += if (eneg) -ev else ev;
    }
    if (i != bytes.len) return null;
    return try numFromDigits(a, neg, digits.items, exp10);
}

/// Decode a canonical-escaped string body (the inverse of appendEscaped).
pub fn decodeEscaped(a: std.mem.Allocator, s: []const u8) ![]const u8 {
    var out = std.ArrayListUnmanaged(u8){};
    var i: usize = 0;
    while (i < s.len) {
        const b = s[i];
        if (b != '\\') {
            try out.append(a, b);
            i += 1;
            continue;
        }
        if (i + 1 >= s.len) return error.Invalid;
        switch (s[i + 1]) {
            '"', '\\' => {
                try out.append(a, s[i + 1]);
                i += 2;
            },
            'b' => {
                try out.append(a, 0x08);
                i += 2;
            },
            'f' => {
                try out.append(a, 0x0C);
                i += 2;
            },
            'n' => {
                try out.append(a, 0x0A);
                i += 2;
            },
            'r' => {
                try out.append(a, 0x0D);
                i += 2;
            },
            't' => {
                try out.append(a, 0x09);
                i += 2;
            },
            'u' => {
                if (i + 6 > s.len or s[i + 2] != '0' or s[i + 3] != '0') return error.Invalid;
                const hi = std.fmt.charToDigit(s[i + 4], 16) catch return error.Invalid;
                const lo = std.fmt.charToDigit(s[i + 5], 16) catch return error.Invalid;
                try out.append(a, hi * 16 + lo);
                i += 6;
            },
            else => return error.Invalid,
        }
    }
    return out.items;
}

fn countCp(s: []const u8) u32 {
    var n: u32 = 0;
    var i: usize = 0;
    while (i < s.len) {
        const l = std.unicode.utf8ByteSequenceLength(s[i]) catch return std.math.maxInt(u32);
        if (i + l > s.len) return std.math.maxInt(u32);
        i += l;
        n += 1;
    }
    return n;
}

// ---------------------------------------------------------------------------
// acceptsValue: exact membership of a value in a node's language
// ---------------------------------------------------------------------------

pub fn acceptsValue(g: View, a: std.mem.Allocator, node: grammar.NodeId, v: Val) Tri {
    return acceptsDepth(g, a, node, v, 0);
}

fn litSpellingEq(g: View, a: std.mem.Allocator, lit: grammar.Literal, v: Val) bool {
    var out = std.ArrayListUnmanaged(u8){};
    defer out.deinit(a);
    spell(&out, a, v) catch return false;
    return std.mem.eql(u8, g.literalBytes(lit), out.items);
}

/// Trie membership of the value's canonical spelling (str_excl forbids the
/// quoted spellings of its values; lit_trie admits them).
fn trieSpells(trie: *const grammar.LitTrie, a: std.mem.Allocator, v: Val) bool {
    var out = std.ArrayListUnmanaged(u8){};
    defer out.deinit(a);
    spell(&out, a, v) catch return false;
    var cur: u32 = 0;
    for (out.items) |b| {
        cur = trie.child(cur, b) orelse return false;
    }
    return trie.nodes[cur].terminal;
}

fn mergeTri(x: Tri, y: Tri, comptime any_yes: bool) Tri {
    // any_yes: OR merge; else AND merge
    if (any_yes) {
        if (x == .yes or y == .yes) return .yes;
        if (x == .unknown or y == .unknown) return .unknown;
        return .no;
    }
    if (x == .no or y == .no) return .no;
    if (x == .unknown or y == .unknown) return .unknown;
    return .yes;
}

fn acceptsDepth(g: View, a: std.mem.Allocator, node: grammar.NodeId, v: Val, depth: u32) Tri {
    if (depth > MAX_DEPTH) return .unknown;
    switch (g.node(node).*) {
        .literal => |l| return if (litSpellingEq(g, a, l, v)) .yes else .no,
        .lit_trie => |t| {
            var t2 = t;
            return if (trieSpells(&t2, a, v)) .yes else .no;
        },
        .str => |sc| {
            if (v != .str) return .no;
            const n = countCp(v.str);
            if (n == std.math.maxInt(u32)) return .unknown;
            return if (n >= sc.min_len and n <= sc.max_len) .yes else .no;
        },
        // int_v is the lexical-integer machine (digits only, no fraction
        // or exponent); the canonical spelling of a value is integer-shaped
        // iff the value is an integer, so the value predicate is numIsInt.
        .int_v => return if (v == .num and numIsInt(v.num)) .yes else .no,
        .num_v => return if (v == .num) .yes else .no,
        .int_num => return if (v == .num and numIsInt(v.num)) .yes else .no,
        .not_int_num => return if (v == .num and !numIsInt(v.num)) .yes else .no,
        .num_const => |nc| return if (v == .num and grammar.cmpNumConst(nc, v.num) == .eq) .yes else .no,
        .num_range => |nr| return if (v == .num and grammar.numConstInRange(v.num, nr)) .yes else .no,
        .num_mult => |nm| return if (v == .num and grammar.numConstIsMultiple(v.num, nm)) .yes else .no,
        .num_excl => |ne| {
            if (v != .num) return .no;
            for (ne.consts) |c| {
                if (grammar.cmpNumConst(c, v.num) == .eq) return .no;
            }
            return .yes;
        },
        .str_pat => |sp| {
            if (v != .str) return .no;
            const n = countCp(v.str);
            if (n == std.math.maxInt(u32)) return .unknown;
            if (n < sp.min_len or n > sp.max_len) return .no;
            const m = sp.dfa.matchesUtf8(v.str) catch return .unknown;
            return if (m) .yes else .no;
        },
        .str_excl => |se| {
            if (v != .str) return .no;
            const n = countCp(v.str);
            if (n == std.math.maxInt(u32)) return .unknown;
            if (n < se.min_len or n > se.max_len) return .no;
            var t = se.trie;
            return if (trieSpells(&t, a, v)) .no else .yes;
        },
        .choice => |ids| {
            var r: Tri = .no;
            for (ids) |c| {
                r = mergeTri(r, acceptsDepth(g, a, c, v, depth + 1), true);
                if (r == .yes) return .yes;
            }
            return r;
        },
        .comb => |cb| {
            var mask: u64 = 0;
            for (cb.branches, 0..) |br, i| {
                const verdict: Tri = if (br == grammar.COMB_NONE) .yes else acceptsDepth(g, a, br, v, depth + 1);
                if (verdict == .unknown) return .unknown;
                if (verdict == .yes) mask |= @as(u64, 1) << @intCast(i);
            }
            const ok = switch (cb.kind) {
                .oneof => @popCount(mask) == 1,
                .allof => mask == ((@as(u64, 1) << @intCast(cb.branches.len)) - 1),
                .ifelse => blk: {
                    const i_ok = (mask & 1) != 0;
                    const t_ok = cb.branches[1] == grammar.COMB_NONE or (mask & 2) != 0;
                    const e_ok = cb.branches[2] == grammar.COMB_NONE or (mask & 4) != 0;
                    break :blk (i_ok and t_ok) or (!i_ok and e_ok);
                },
            };
            return if (ok) .yes else .no;
        },
        .seq => |ids| {
            // Const containers compile to ["[" v0 "," ... "]"] sequences;
            // only that exact shape is decidable as a value predicate.
            return acceptsSeqConst(g, a, ids, v, depth);
        },
        .object => |on| {
            if (v != .obj) return .no;
            var r: Tri = .yes;
            // Declared properties must appear in schema order, keys unique.
            var last_decl: ?usize = null;
            for (v.obj, 0..) |e, ei| {
                for (v.obj[0..ei]) |prev| {
                    if (std.mem.eql(u8, prev.key, e.key)) return .no;
                }
                const di = declIndex(g, on.props, e.key) orelse return .no; // closed
                if (last_decl != null and di <= last_decl.?) return .no;
                last_decl = di;
                r = mergeTri(r, acceptsDepth(g, a, on.props[di].value, e.value, depth + 1), false);
                if (r == .no) return .no;
            }
            for (on.props) |p| {
                if (p.required and objGet(v.obj, propName(g, p.key)) == null) return .no;
            }
            return r;
        },
        .open_obj => |on| return acceptsOpenObj(g, a, &on, v, depth),
        .repeat => |r| return acceptsRepeat(g, a, &r, v, depth),
    }
}

fn propName(g: View, key: grammar.Literal) []const u8 {
    const kl = g.literalBytes(key);
    return kl[1 .. kl.len - 2]; // strip quotes and ':'
}

fn declaredProp(g: View, props: []const grammar.Prop, name: []const u8) bool {
    return declIndex(g, props, name) != null;
}

fn declIndex(g: View, props: []const grammar.Prop, name: []const u8) ?usize {
    for (props, 0..) |p, i| {
        if (std.mem.eql(u8, propName(g, p.key), name)) return i;
    }
    return null;
}

/// seq acceptance, exact only when every element resolves to a fixed
/// const fragment (the const-container shape: literal punctuation and
/// num_const fillers, recursively). The language is then a single VALUE
/// (the num_const elements match by value, not spelling), so membership
/// is structural equality against the parsed template. Anything else is
/// not decidable as a value predicate here and stays unknown.
fn acceptsSeqConst(g: View, a: std.mem.Allocator, ids: []const grammar.NodeId, v: Val, depth: u32) Tri {
    const ev = constSeqValue(g, a, ids, depth) orelse return .unknown;
    return if (valEq(ev, v)) .yes else .no;
}

/// The single value of an all-const seq, or null when the seq is not of
/// that shape.
fn constSeqValue(g: View, a: std.mem.Allocator, ids: []const grammar.NodeId, depth: u32) ?Val {
    if (depth > MAX_DEPTH) return null;
    var bytes = std.ArrayListUnmanaged(u8){};
    defer bytes.deinit(a);
    if (!constSeqBytes(g, a, ids, &bytes, depth)) return null;
    var err_off: u32 = 0;
    const jv = json.parse(a, bytes.items, &err_off) catch return null;
    return valFromJson(a, jv) catch null;
}

/// Append the canonical byte string of an all-const seq (recursively);
/// false when any element is not a literal, num_const, or nested const seq.
fn constSeqBytes(g: View, a: std.mem.Allocator, ids: []const grammar.NodeId, out: *std.ArrayListUnmanaged(u8), depth: u32) bool {
    if (depth > MAX_DEPTH) return false;
    for (ids) |id| {
        switch (g.node(id).*) {
            .literal => |l| out.appendSlice(a, g.literalBytes(l)) catch return false,
            .num_const => |nc| {
                var tmp = std.ArrayListUnmanaged(u8){};
                defer tmp.deinit(a);
                spellNum(&tmp, a, nc) catch return false;
                out.appendSlice(a, tmp.items) catch return false;
            },
            .seq => |sub| if (!constSeqBytes(g, a, sub, out, depth + 1)) return false,
            else => return false,
        }
    }
    return true;
}

fn acceptsOpenObj(g: View, a: std.mem.Allocator, on: *const grammar.OpenObjNode, v: Val, depth: u32) Tri {
    if (v != .obj) return .no;
    if (on.names_forbidden and v.obj.len != 0) return .no;
    if (v.obj.len < on.min_props or v.obj.len > on.max_props) return .no;
    var r: Tri = .yes;
    // Keys are unique; declared props appear in increasing schema order.
    var last_decl: ?usize = null;
    for (v.obj, 0..) |e, ei| {
        for (v.obj[0..ei]) |prev| {
            if (std.mem.eql(u8, prev.key, e.key)) return .no;
        }
        if (declIndex(g, on.props, e.key)) |di| {
            if (last_decl != null and di <= last_decl.?) return .no;
            last_decl = di;
            r = mergeTri(r, acceptsDepth(g, a, on.props[di].value, e.value, depth + 1), false);
        } else {
            // undeclared: prop_names (with its key length bounds), then
            // pattern dispatch, else the fallback value schema
            if (on.prop_names) |pn| {
                r = mergeTri(r, acceptsDepth(g, a, pn, Val{ .str = e.key }, depth + 1), false);
            }
            const kl = countCp(e.key);
            if (kl == std.math.maxInt(u32)) return .unknown;
            if (kl < on.key_min_len or kl > on.key_max_len) return .no;
            var vnode = on.value;
            if (on.pattern_dfa) |pd| {
                const m = pd.matchesUtf8(e.key) catch return .unknown;
                if (m) vnode = on.pattern_value;
            } else if (on.pattern_lit) |pl| {
                if (std.mem.indexOf(u8, e.key, g.literalBytes(pl)) != null) vnode = on.pattern_value;
            }
            r = mergeTri(r, acceptsDepth(g, a, vnode, e.value, depth + 1), false);
        }
        if (r == .no) return .no;
    }
    // required declared props + extra required names
    for (on.props) |p| {
        if (p.required and objGet(v.obj, propName(g, p.key)) == null) return .no;
    }
    for (on.extra_required) |lit| {
        if (objGet(v.obj, g.literalBytes(lit)) == null) return .no;
    }
    // dependencies
    for (on.deps) |d| {
        const present = objGet(v.obj, g.literalBytes(d.trigger)) != null;
        switch (d.kind) {
            .ban => if (present) return .no,
            .required => {
                if (present) {
                    for (d.names) |n| {
                        if (objGet(v.obj, g.literalBytes(n)) == null) return .no;
                    }
                }
            },
            .schema => {
                if (present) {
                    r = mergeTri(r, acceptsDepth(g, a, d.schema, v, depth + 1), false);
                    if (r == .no) return .no;
                }
            },
        }
    }
    return r;
}

fn acceptsRepeat(g: View, a: std.mem.Allocator, r: *const grammar.Repeat, v: Val, depth: u32) Tri {
    if (v != .arr) return .no;
    if (v.arr.len < r.min or v.arr.len > r.max) return .no;
    var res: Tri = .yes;
    var contains_count: u32 = 0;
    for (v.arr, 0..) |el, i| {
        const slot = if (i < r.prefix.len) r.prefix[i] else r.item;
        res = mergeTri(res, acceptsDepth(g, a, slot, el, depth + 1), false);
        if (res == .no) return .no;
        if (r.contains) |cn| {
            switch (acceptsDepth(g, a, cn, el, depth + 1)) {
                .yes => contains_count += 1,
                .unknown => res = .unknown,
                .no => {},
            }
        }
        if (r.unique) {
            for (v.arr[0..i]) |prev| {
                if (valEq(prev, el)) return .no;
            }
        }
    }
    if (r.contains != null) {
        if (contains_count < r.min_contains or contains_count > r.max_contains) return .no;
    }
    return res;
}

// ---------------------------------------------------------------------------
// witness: construct a verified member of a node's language
// ---------------------------------------------------------------------------

/// Best-effort construction of a value in the node's language. The
/// construction is heuristic; every candidate is verified with
/// acceptsValue before it is returned, so a non-null result is always a
/// sound non-emptiness certificate, and null means "not found here",
/// never "empty".
pub fn witness(g: View, a: std.mem.Allocator, node: grammar.NodeId) ?Val {
    const w = witnessDepth(g, a, node, 0) catch return null;
    if (w == null) return null;
    if (acceptsValue(g, a, node, w.?) != .yes) return null;
    return w;
}

/// The unverified construction (diagnostics only): may be null where the
/// verified witness would still succeed, and is not guaranteed to belong
/// to the language.
pub fn witnessRaw(g: View, a: std.mem.Allocator, node: grammar.NodeId) ?Val {
    return witnessDepth(g, a, node, 0) catch null;
}

fn witnessDepth(g: View, a: std.mem.Allocator, node: grammar.NodeId, depth: u32) error{OutOfMemory}!?Val {
    if (depth > MAX_DEPTH) return null;
    switch (g.node(node).*) {
        .literal => |l| return parseLiteral(a, g.literalBytes(l)) catch null,
        .lit_trie => |t| {
            for (t.literals) |l| {
                if (parseLiteral(a, g.literalBytes(l)) catch null) |v| return v;
            }
            return null;
        },
        .str => |sc| {
            if (sc.min_len > sc.max_len) return null;
            const s = try a.alloc(u8, sc.min_len);
            @memset(s, 'a');
            return Val{ .str = s };
        },
        .int_v, .num_v, .int_num => return Val{ .num = try numFromI64(a, 0) },
        .not_int_num => return Val{ .num = try numFromDigits(a, false, "5", -1) },
        .num_const => |nc| return Val{ .num = nc },
        .num_range => |nr| return if (try numInRange(a, nr)) |v| Val{ .num = v } else null,
        .num_mult => return Val{ .num = try numFromI64(a, 0) },
        .num_excl => |ne| {
            var cand: i64 = 0;
            while (cand < 8) : (cand += 1) {
                const v = try numFromI64(a, cand);
                var banned = false;
                for (ne.consts) |c| {
                    if (grammar.cmpNumConst(c, v) == .eq) {
                        banned = true;
                        break;
                    }
                }
                if (!banned) return Val{ .num = v };
            }
            return null;
        },
        .str_excl => |se| {
            // Finitely many forbidden spellings: probe a few candidate
            // strings per allowed length and keep the first accepted.
            var l: u32 = se.min_len;
            const hi = @min(se.max_len, se.min_len + 4);
            while (l <= hi and l <= 64) : (l += 1) {
                const chars = [_]u8{ 'a', 'b', '0' };
                for (chars) |ch| {
                    const s = try a.alloc(u8, l);
                    @memset(s, ch);
                    const v = Val{ .str = s };
                    if (acceptsDepth(g, a, node, v, depth + 1) == .yes) return v;
                }
            }
            return null;
        },
        .str_pat => |sp| {
            var path = std.ArrayListUnmanaged(u21){};
            defer path.deinit(a);
            const d = (try sp.dfa.shortestAccept(a, &path)) orelse return null;
            var len: u32 = d;
            if (len < sp.min_len) {
                // Search-mode accept states are sticky over the whole scalar
                // domain, so any padding keeps acceptance.
                try path.appendNTimes(a, 'a', sp.min_len - len);
                len = sp.min_len;
            }
            if (len > sp.max_len) return null;
            var out = std.ArrayListUnmanaged(u8){};
            for (path.items) |cp| {
                var buf: [4]u8 = undefined;
                const n = std.unicode.utf8Encode(cp, &buf) catch return null;
                try out.appendSlice(a, buf[0..n]);
            }
            return Val{ .str = out.items };
        },
        .choice => |ids| {
            for (ids) |c| {
                if (try witnessDepth(g, a, c, depth + 1)) |v| {
                    if (acceptsDepth(g, a, node, v, depth + 1) == .yes) return v;
                }
            }
            return null;
        },
        .comb => {
            // Candidate members of the comb: seeds from every present
            // branch (choices contribute per-option seeds, open objects
            // discriminator variants, nested combs their own candidates),
            // each verified exactly against the vote rule; allof
            // additionally tries pairwise merges of object seeds.
            var cands = std.ArrayListUnmanaged(Val){};
            defer cands.deinit(a);
            try combCandidates(g, a, node, depth, &cands);
            if (cands.items.len > 0) return cands.items[0];
            return null;
        },
        .seq => |ids| return constSeqValue(g, a, ids, depth),
        .object => |on| {
            var entries = std.ArrayListUnmanaged(Entry){};
            for (on.props) |p| {
                if (!p.required) continue;
                const pv = (try witnessDepth(g, a, p.value, depth + 1)) orelse return null;
                try entries.append(a, .{ .key = propName(g, p.key), .value = pv });
            }
            return Val{ .obj = entries.items };
        },
        .open_obj => |on| return try witnessOpenObj(g, a, &on, depth, 0),
        .repeat => |r| return try witnessRepeat(g, a, &r, depth),
    }
}

/// Candidate seed values of a comb branch: raw (unverified against the
/// comb) constructions - a choice contributes each option's verified
/// witness, an open object its plain witness plus discriminator variants
/// (one or two optional props included), a nested comb its own verified
/// candidates. Bounded by MAX_SEEDS; the comb verifies every seed exactly
/// before keeping it.
fn collectSeeds(g: View, a: std.mem.Allocator, node: grammar.NodeId, depth: u32, out: *std.ArrayListUnmanaged(Val)) error{OutOfMemory}!void {
    if (depth > MAX_DEPTH or out.items.len >= MAX_SEEDS) return;
    switch (g.node(node).*) {
        .choice => |ids| {
            for (ids) |c| {
                if (try witnessDepth(g, a, c, depth + 1)) |v| {
                    if (acceptsDepth(g, a, c, v, depth + 1) == .yes) try out.append(a, v);
                    if (out.items.len >= MAX_SEEDS) return;
                }
            }
        },
        .comb => try combCandidates(g, a, node, depth + 1, out),
        .open_obj => |on| {
            if (try witnessDepth(g, a, node, depth + 1)) |v| {
                if (acceptsDepth(g, a, node, v, depth + 1) == .yes) try out.append(a, v);
            }
            // Discriminator variants: include one or two optional props
            // (e.g. oneOf branches split by an optional "type" const plus
            // its sibling "typeProperties").
            var nvar: usize = 0;
            var pi: usize = 0;
            while (pi < on.props.len and pi < 64 and nvar < MAX_VARIANTS) : (pi += 1) {
                if (on.props[pi].required) continue;
                const mi = @as(u64, 1) << @intCast(pi);
                if (try witnessOpenObj(g, a, &on, depth + 1, mi)) |v| {
                    try out.append(a, v);
                    nvar += 1;
                }
                if (out.items.len >= MAX_SEEDS) return;
                var pj: usize = pi + 1;
                while (pj < on.props.len and pj < 64 and nvar < MAX_VARIANTS) : (pj += 1) {
                    if (on.props[pj].required) continue;
                    const mij = mi | (@as(u64, 1) << @intCast(pj));
                    if (try witnessOpenObj(g, a, &on, depth + 1, mij)) |v| {
                        try out.append(a, v);
                        nvar += 1;
                    }
                    if (out.items.len >= MAX_SEEDS) return;
                }
            }
        },
        else => {
            if (try witnessDepth(g, a, node, depth + 1)) |v| {
                if (acceptsDepth(g, a, node, v, depth + 1) == .yes) try out.append(a, v);
            }
        },
    }
}

/// Verified candidate members of a comb node (bounded by MAX_CAND).
fn combCandidates(g: View, a: std.mem.Allocator, node: grammar.NodeId, depth: u32, out: *std.ArrayListUnmanaged(Val)) error{OutOfMemory}!void {
    if (depth > MAX_DEPTH) return;
    const cb = g.node(node).comb;
    var raw = std.ArrayListUnmanaged(Val){};
    defer raw.deinit(a);
    for (cb.branches) |br| {
        if (br == grammar.COMB_NONE) continue;
        try collectSeeds(g, a, br, depth + 1, &raw);
    }
    for (raw.items) |v| {
        if (out.items.len >= MAX_CAND) return;
        if (acceptsDepth(g, a, node, v, depth + 1) == .yes) try out.append(a, v);
    }
    if (cb.kind != .allof) return;
    // allof over object-family branches: the merged mandatory-key object,
    // reconciled with nested vote-comb candidates.
    try witnessAllOfObj(g, a, node, cb, depth, out);
    // allof: merge object seeds pairwise (both conflict resolutions) and
    // verify the merge against all branches.
    var merges: usize = 0;
    var i: usize = 0;
    while (i < raw.items.len and merges < MAX_MERGES) : (i += 1) {
        if (raw.items[i] != .obj) continue;
        var j: usize = i + 1;
        while (j < raw.items.len and merges < MAX_MERGES) : (j += 1) {
            if (raw.items[j] != .obj) continue;
            merges += 1;
            if (out.items.len >= MAX_CAND) return;
            if (try mergeAndVerify(g, a, node, raw.items[i], raw.items[j], depth)) |m| {
                try out.append(a, m);
            }
        }
    }
}

/// Overlay two object values: base's entry order, values taken from over
/// where the key exists in both, then over's fresh keys appended.
fn overlayObjs(a: std.mem.Allocator, base: Val, over: Val) error{OutOfMemory}!Val {
    var entries = std.ArrayListUnmanaged(Entry){};
    for (base.obj) |e| {
        const v = objGet(over.obj, e.key) orelse e.value;
        try entries.append(a, .{ .key = e.key, .value = v });
    }
    for (over.obj) |e| {
        if (objGet(base.obj, e.key) == null) try entries.append(a, e);
    }
    return Val{ .obj = entries.items };
}

/// A witness of every schema in the list: seed from each member, then
/// pairwise intersections, verified against all.
fn witnessAll(g: View, a: std.mem.Allocator, schemas: []const grammar.NodeId, depth: u32) error{OutOfMemory}!?Val {
    if (depth > MAX_DEPTH) return null;
    for (schemas) |s| {
        if (try witnessDepth(g, a, s, depth + 1)) |v| {
            var ok = true;
            for (schemas) |s2| {
                if (acceptsDepth(g, a, s2, v, depth + 1) != .yes) {
                    ok = false;
                    break;
                }
            }
            if (ok) return v;
        }
    }
    for (schemas, 0..) |s1, i| {
        for (schemas[i + 1 ..]) |s2| {
            if (try witnessBoth(g, a, s1, s2, depth + 1)) |v| {
                var ok = true;
                for (schemas) |s3| {
                    if (acceptsDepth(g, a, s3, v, depth + 1) != .yes) {
                        ok = false;
                        break;
                    }
                }
                if (ok) return v;
            }
        }
    }
    return null;
}

/// Object-aware candidate construction for an allof whose (flattened)
/// branches are object/open_obj nodes plus possibly nested vote combs:
/// build the object of every branch's mandatory keys with values accepted
/// by all branches' dispatches, then (when the vote combs reject it)
/// overlay verified candidates of the nested combs. Every result is
/// verified against the full allof before being appended.
fn witnessAllOfObj(g: View, a: std.mem.Allocator, node: grammar.NodeId, cb: grammar.Comb, depth: u32, out: *std.ArrayListUnmanaged(Val)) error{OutOfMemory}!void {
    if (depth + 2 > MAX_DEPTH or out.items.len >= MAX_CAND) return;
    // Flatten nested allof branches.
    var flat = std.ArrayListUnmanaged(grammar.NodeId){};
    defer flat.deinit(a);
    var stack = std.ArrayListUnmanaged(grammar.NodeId){};
    defer stack.deinit(a);
    for (cb.branches) |b| try stack.append(a, b);
    while (stack.pop()) |b| {
        if (b == grammar.COMB_NONE) continue;
        switch (g.node(b).*) {
            .comb => |c2| if (c2.kind == .allof) {
                for (c2.branches) |b2| try stack.append(a, b2);
                continue;
            },
            else => {},
        }
        try flat.append(a, b);
    }
    var objb = std.ArrayListUnmanaged(grammar.NodeId){};
    defer objb.deinit(a);
    var combs = std.ArrayListUnmanaged(grammar.NodeId){};
    defer combs.deinit(a);
    for (flat.items) |b| {
        switch (g.node(b).*) {
            .object, .open_obj => try objb.append(a, b),
            .comb => try combs.append(a, b),
            else => return, // leaf branches stay with the generic seed path
        }
    }
    if (objb.items.len == 0) return;
    // Union of the mandatory key sets.
    var keys = std.ArrayListUnmanaged([]const u8){};
    defer keys.deinit(a);
    for (objb.items) |b| {
        switch (g.node(b).*) {
            .object => |on| {
                for (on.props) |p| {
                    if (!p.required) continue;
                    const k = propName(g, p.key);
                    if (!hasName(keys.items, k)) try keys.append(a, k);
                }
            },
            .open_obj => |on| {
                if (on.names_forbidden) return; // only {} survives; handled below
                var mk = try mandatoryKeys(g, a, &on);
                defer mk.deinit(a);
                for (mk.items) |k| {
                    if (!hasName(keys.items, k)) try keys.append(a, k);
                }
            },
            else => unreachable,
        }
    }
    // A mandatory key a closed branch does not declare, or a ban trigger
    // in any branch, empties the intersection - no witness here.
    for (keys.items) |k| {
        for (objb.items) |b| {
            switch (g.node(b).*) {
                .object => |on| if (declIndex(g, on.props, k) == null) return,
                .open_obj => |on| {
                    for (on.deps) |d| {
                        if (d.kind == .ban and std.mem.eql(u8, g.literalBytes(d.trigger), k)) return;
                    }
                },
                else => unreachable,
            }
        }
    }
    // Per-key values accepted by every branch's dispatch.
    var entries = std.ArrayListUnmanaged(Entry){};
    for (keys.items) |k| {
        var schemas = std.ArrayListUnmanaged(grammar.NodeId){};
        defer schemas.deinit(a);
        for (objb.items) |b| {
            switch (g.node(b).*) {
                .object => |on| try schemas.append(a, on.props[declIndex(g, on.props, k).?].value),
                .open_obj => |on| try schemas.append(a, dispatchValue(g, &on, k) orelse return),
                else => unreachable,
            }
        }
        const v = (try witnessAll(g, a, schemas.items, depth + 1)) orelse return;
        try entries.append(a, .{ .key = k, .value = v });
    }
    // Pad to the largest min_props with fresh keys acceptable everywhere.
    var want: u32 = 0;
    for (objb.items) |b| {
        switch (g.node(b).*) {
            .open_obj => |on| want = @max(want, on.min_props),
            else => {},
        }
    }
    var tries: u32 = 0;
    while (keys.items.len < want and tries < 256) : (tries += 1) {
        const k = try std.fmt.allocPrint(a, "k{d}", .{tries});
        if (hasName(keys.items, k)) continue;
        var schemas = std.ArrayListUnmanaged(grammar.NodeId){};
        defer schemas.deinit(a);
        var ok = true;
        for (objb.items) |b| {
            switch (g.node(b).*) {
                .object => {
                    ok = false;
                    break;
                }, // closed branch: fresh keys impossible
                .open_obj => |on| {
                    if (declIndex(g, on.props, k) != null) {
                        ok = false;
                        break;
                    }
                    const kl = countCp(k);
                    if (kl < on.key_min_len or kl > on.key_max_len) {
                        ok = false;
                        break;
                    }
                    if (on.prop_names) |pn| {
                        if (acceptsDepth(g, a, pn, Val{ .str = k }, depth + 1) != .yes) {
                            ok = false;
                            break;
                        }
                    }
                    var triggers = false;
                    for (on.deps) |d| {
                        if (std.mem.eql(u8, g.literalBytes(d.trigger), k)) triggers = true;
                    }
                    if (triggers) {
                        ok = false;
                        break;
                    }
                    try schemas.append(a, dispatchValue(g, &on, k) orelse {
                        ok = false;
                        break;
                    });
                },
                else => unreachable,
            }
        }
        if (!ok) continue;
        const v = (try witnessAll(g, a, schemas.items, depth + 1)) orelse return;
        try keys.append(a, k);
        try entries.append(a, .{ .key = k, .value = v });
    }
    if (keys.items.len < want) return;
    // max_props caps
    for (objb.items) |b| {
        switch (g.node(b).*) {
            .open_obj => |on| if (keys.items.len > on.max_props) return,
            .object => |on| if (keys.items.len > on.props.len) return,
            else => unreachable,
        }
    }
    const base = Val{ .obj = entries.items };
    if (acceptsDepth(g, a, node, base, depth + 1) == .yes) {
        try out.append(a, base);
        return;
    }
    // The vote combs reject the plain object: overlay their verified
    // candidates (they carry the discriminating props) in both orders.
    for (combs.items) |cn| {
        var cands = std.ArrayListUnmanaged(Val){};
        defer cands.deinit(a);
        try combCandidates(g, a, cn, depth + 1, &cands);
        for (cands.items) |c| {
            if (c != .obj) continue;
            const m1 = try overlayObjs(a, base, c);
            if (acceptsDepth(g, a, node, m1, depth + 1) == .yes) {
                try out.append(a, m1);
                return;
            }
            const m2 = try overlayObjs(a, c, base);
            if (acceptsDepth(g, a, node, m2, depth + 1) == .yes) {
                try out.append(a, m2);
                return;
            }
            if (out.items.len >= MAX_CAND) return;
        }
    }
}

/// Merge two object values: entries of s1 in order, then s2's fresh keys;
/// keys present in both take either side's value (up to 3 conflicts, 2^k
/// variants). Returns the first variant verified against `node`.
fn mergeAndVerify(g: View, a: std.mem.Allocator, node: grammar.NodeId, s1: Val, s2: Val, depth: u32) error{OutOfMemory}!?Val {
    var cidx = std.ArrayListUnmanaged(usize){};
    defer cidx.deinit(a);
    for (s1.obj, 0..) |e1, ei| {
        if (objGet(s2.obj, e1.key) != null) try cidx.append(a, ei);
    }
    if (cidx.items.len > 3) return null;
    const nvar = @as(u32, 1) << @intCast(cidx.items.len);
    var mask: u32 = 0;
    while (mask < nvar) : (mask += 1) {
        var entries = std.ArrayListUnmanaged(Entry){};
        var ci: usize = 0;
        for (s1.obj, 0..) |e1, ei| {
            var val = e1.value;
            if (ci < cidx.items.len and cidx.items[ci] == ei) {
                if ((mask >> @intCast(ci)) & 1 == 1) val = objGet(s2.obj, e1.key).?;
                ci += 1;
            }
            try entries.append(a, .{ .key = e1.key, .value = val });
        }
        for (s2.obj) |e2| {
            if (objGet(s1.obj, e2.key) == null) try entries.append(a, e2);
        }
        const m = Val{ .obj = entries.items };
        if (acceptsDepth(g, a, node, m, depth + 1) == .yes) return m;
    }
    return null;
}

fn valFromJson(a: std.mem.Allocator, jv: *const json.Value) error{OutOfMemory}!?Val {
    switch (jv.v) {
        .null_v => return Val.nul,
        .boolean => |b| return Val{ .boolean = b },
        .number => |s| return if (try parseNumBytes(a, s)) |nc| Val{ .num = nc } else null,
        .string => |s| return Val{ .str = s },
        .array => |xs| {
            const out = try a.alloc(Val, xs.len);
            for (xs, 0..) |x, i| {
                out[i] = (try valFromJson(a, x)) orelse return null;
            }
            return Val{ .arr = out };
        },
        .object => |ps| {
            const out = try a.alloc(Entry, ps.len);
            for (ps, 0..) |p, i| {
                out[i] = .{ .key = p.key, .value = (try valFromJson(a, p.value)) orelse return null };
            }
            return Val{ .obj = out };
        },
    }
}

/// The value schema a key dispatches to: the declared prop, else the
/// pattern schema when the key matches, else the fallback.
fn dispatchValue(g: View, on: *const grammar.OpenObjNode, key: []const u8) ?grammar.NodeId {
    for (on.props) |p| {
        if (std.mem.eql(u8, propName(g, p.key), key)) return p.value;
    }
    if (on.pattern_dfa) |pd| {
        const m = pd.matchesUtf8(key) catch return null;
        if (m) return on.pattern_value;
    } else if (on.pattern_lit) |pl| {
        if (std.mem.indexOf(u8, key, g.literalBytes(pl)) != null) return on.pattern_value;
    }
    return on.value;
}

fn hasName(keys: []const []const u8, k: []const u8) bool {
    for (keys) |x| {
        if (std.mem.eql(u8, x, k)) return true;
    }
    return false;
}

/// The mandatory key set of an open object: declared required props plus
/// extra_required, closed under required-kind dependencies (a present
/// trigger forces its names). Null on OOM only.
fn mandatoryKeys(g: View, a: std.mem.Allocator, on: *const grammar.OpenObjNode) !std.ArrayListUnmanaged([]const u8) {
    var keys = std.ArrayListUnmanaged([]const u8){};
    for (on.props) |p| {
        if (!p.required) continue;
        const k = propName(g, p.key);
        if (!hasName(keys.items, k)) try keys.append(a, k);
    }
    for (on.extra_required) |lit| {
        const k = g.literalBytes(lit);
        if (!hasName(keys.items, k)) try keys.append(a, k);
    }
    var i: usize = 0;
    while (i < keys.items.len) : (i += 1) {
        for (on.deps) |d| {
            if (d.kind != .required) continue;
            if (!std.mem.eql(u8, g.literalBytes(d.trigger), keys.items[i])) continue;
            for (d.names) |n| {
                const k = g.literalBytes(n);
                if (!hasName(keys.items, k)) try keys.append(a, k);
            }
        }
    }
    return keys;
}

/// Exact emptiness proofs for open objects over the mandatory key set: a
/// mandatory key that is banned, fails prop_names or the key bounds, or a
/// mandatory set larger than max_props makes the language empty.
pub fn openObjEmpty(g: View, a: std.mem.Allocator, on: *const grammar.OpenObjNode) bool {
    var has_req = on.min_props > 0 or on.extra_required.len > 0;
    for (on.props) |p| {
        if (p.required) has_req = true;
    }
    if (on.names_forbidden) return has_req;
    var keys = mandatoryKeys(g, a, on) catch return false;
    defer keys.deinit(a);
    if (keys.items.len > on.max_props) return true;
    for (on.deps) |d| {
        if (d.kind != .ban) continue;
        if (hasName(keys.items, g.literalBytes(d.trigger))) return true;
    }
    for (keys.items) |k| {
        const kl = countCp(k);
        if (kl != std.math.maxInt(u32) and (kl < on.key_min_len or kl > on.key_max_len)) {
            // key bounds apply to undeclared keys only
            if (declIndex(g, on.props, k) == null) return true;
        }
        if (on.prop_names) |pn| {
            if (declIndex(g, on.props, k) == null) {
                if (acceptsValue(g, a, pn, Val{ .str = k }) == .no) return true;
            }
        }
    }
    return false;
}

/// extra_mask: also include those (optional) declared props (bit = prop
/// index) with constructed values - discriminator-prop variants of the
/// object, needed to seed oneOf branches whose discriminators are not
/// required.
fn witnessOpenObj(g: View, a: std.mem.Allocator, on: *const grammar.OpenObjNode, depth: u32, extra_mask: u64) error{OutOfMemory}!?Val {
    if (openObjEmpty(g, a, on)) return null;
    var keys = try mandatoryKeys(g, a, on);
    defer keys.deinit(a);
    // Pad to min_props with fresh keys that trigger no dependency.
    var tries: u32 = 0;
    while (keys.items.len < on.min_props and tries < 256) : (tries += 1) {
        const k = try std.fmt.allocPrint(a, "k{d}", .{tries});
        if (hasName(keys.items, k)) continue;
        if (declIndex(g, on.props, k) != null) continue;
        const kl = countCp(k);
        if (kl < on.key_min_len or kl > on.key_max_len) continue;
        if (on.prop_names) |pn| {
            if (acceptsDepth(g, a, pn, Val{ .str = k }, depth + 1) != .yes) continue;
        }
        var triggers = false;
        for (on.deps) |d| {
            if (std.mem.eql(u8, g.literalBytes(d.trigger), k)) {
                triggers = true;
                break;
            }
        }
        if (triggers) continue;
        try keys.append(a, k);
    }
    const extra_count: usize = @popCount(extra_mask);
    if (keys.items.len < on.min_props or keys.items.len + extra_count > on.max_props) return null;
    var entries = std.ArrayListUnmanaged(Entry){};
    // Declared props first, in schema order (required plus masked extras).
    for (on.props, 0..) |p, pi| {
        const is_extra = pi < 64 and (extra_mask >> @intCast(pi)) & 1 == 1;
        if (!p.required and !is_extra) continue;
        const pv = (try witnessDepth(g, a, p.value, depth + 1)) orelse return null;
        try entries.append(a, .{ .key = propName(g, p.key), .value = pv });
    }
    // Undeclared mandatory keys (extra_required, dep closure, padding).
    for (keys.items) |k| {
        if (declIndex(g, on.props, k) != null) continue; // already emitted
        const vnode = dispatchValue(g, on, k) orelse return null;
        const pv = (try witnessDepth(g, a, vnode, depth + 1)) orelse return null;
        try entries.append(a, .{ .key = k, .value = pv });
    }
    return Val{ .obj = entries.items };
}

/// A witness of both nodes' languages: seed from either side, verify
/// against the other.
fn witnessBoth(g: View, a: std.mem.Allocator, x: grammar.NodeId, y: grammar.NodeId, depth: u32) error{OutOfMemory}!?Val {
    if (try witnessDepth(g, a, x, depth + 1)) |v| {
        if (acceptsDepth(g, a, y, v, depth + 1) == .yes) return v;
    }
    if (try witnessDepth(g, a, y, depth + 1)) |v| {
        if (acceptsDepth(g, a, x, v, depth + 1) == .yes) return v;
    }
    return null;
}

/// Perturb a duplicate element until it is fresh and still accepted by its
/// slot (numbers step by 1, strings grow an 'a').
fn makeFresh(g: View, a: std.mem.Allocator, slot: grammar.NodeId, v0: Val, seen: []const Val, depth: u32) error{OutOfMemory}!?Val {
    var v = v0;
    var tries: u32 = 0;
    while (tries < 16) : (tries += 1) {
        var dup = false;
        for (seen) |s| {
            if (valEq(s, v)) {
                dup = true;
                break;
            }
        }
        if (!dup and acceptsDepth(g, a, slot, v, depth + 1) == .yes) return v;
        switch (v) {
            .num => |nc| v = Val{ .num = try numPlusPower(a, nc, 0) },
            .str => |s| v = Val{ .str = try std.mem.concat(a, u8, &.{ s, "a" }) },
            else => return null,
        }
    }
    return null;
}

fn witnessRepeat(g: View, a: std.mem.Allocator, r: *const grammar.Repeat, depth: u32) error{OutOfMemory}!?Val {
    var need: u32 = r.min;
    if (r.contains != null and r.min_contains > need) need = r.min_contains;
    if (need > r.max) return null;
    var els = std.ArrayListUnmanaged(Val){};
    var contains_left: u32 = if (r.contains != null) r.min_contains else 0;
    var i: u32 = 0;
    while (i < need) : (i += 1) {
        const slot = if (i < r.prefix.len) r.prefix[i] else r.item;
        var v: ?Val = null;
        if (contains_left > 0) {
            v = try witnessBoth(g, a, slot, r.contains.?, depth);
            if (v != null) contains_left -= 1;
        }
        if (v == null) v = try witnessDepth(g, a, slot, depth + 1);
        if (v == null) return null;
        if (r.unique) {
            v = try makeFresh(g, a, slot, v.?, els.items, depth);
            if (v == null) return null;
        }
        try els.append(a, v.?);
    }
    if (contains_left > 0) return null;
    return Val{ .arr = els.items };
}

// ---------------------------------------------------------------------------
// disjoint: exact disjointness proofs
// ---------------------------------------------------------------------------

/// Exact disjointness of two nodes' languages. yes = provably no common
/// value, no = a common value is exhibited (single-value nodes) or the
/// nodes are identical; unknown otherwise.
pub fn disjoint(g: View, a: std.mem.Allocator, x: grammar.NodeId, y: grammar.NodeId) Tri {
    return disjointDepth(g, a, x, y, 0);
}

fn disjointDepth(g: View, a: std.mem.Allocator, x: grammar.NodeId, y: grammar.NodeId, depth: u32) Tri {
    if (depth > MAX_DEPTH) return .unknown;
    if (x == y) return .no;
    // Single-value membership rule, in both directions: when one side is a
    // known constant, disjointness is exactly the other side's membership
    // verdict on it.
    if (constValueOf(g, a, x)) |v| {
        return switch (acceptsDepth(g, a, y, v, 0)) {
            .yes => .no,
            .no => .yes,
            .unknown => .unknown,
        };
    }
    if (constValueOf(g, a, y)) |v| {
        return switch (acceptsDepth(g, a, x, v, 0)) {
            .yes => .no,
            .no => .yes,
            .unknown => .unknown,
        };
    }
    // Type partition.
    const fx = familyOf(g, x);
    const fy = familyOf(g, y);
    if (fx != null and fy != null) {
        if (fx.? != fy.?) return .yes;
        if (fx.? == .obj) return objDisjoint(g, a, x, y, depth);
        if (fx.? == .num) return numDisjoint(g, a, x, y);
        if (fx.? == .str) return strDisjoint(g, a, x, y);
    }
    return .unknown;
}

/// The single value of a node's language, when the node is a known
/// constant shape.
fn constValueOf(g: View, a: std.mem.Allocator, node: grammar.NodeId) ?Val {
    return switch (g.node(node).*) {
        .literal => |l| parseLiteral(a, g.literalBytes(l)) catch null,
        .num_const => |nc| Val{ .num = nc },
        else => null,
    };
}

const Fam = enum { nul, boolean, num, str, arr, obj };

fn litFamily(b: []const u8) ?Fam {
    if (b.len == 0) return null;
    return switch (b[0]) {
        '"' => .str,
        't', 'f' => .boolean,
        'n' => .nul,
        '[' => .arr,
        '{' => .obj,
        '-', '0'...'9' => .num,
        else => null,
    };
}

/// The JSON type a node's values all belong to, when uniform.
fn familyOf(g: View, node: grammar.NodeId) ?Fam {
    switch (g.node(node).*) {
        .literal => |l| return litFamily(g.literalBytes(l)),
        .lit_trie => |t| {
            var f: ?Fam = null;
            for (t.literals) |l| {
                const lf = litFamily(g.literalBytes(l)) orelse return null;
                if (f == null) {
                    f = lf;
                } else if (f.? != lf) return null;
            }
            return f;
        },
        .str, .str_excl, .str_pat => return .str,
        .int_v, .num_v, .int_num, .not_int_num, .num_const, .num_excl, .num_range, .num_mult => return .num,
        .repeat => return .arr,
        .object, .open_obj => return .obj,
        .seq => return null, // byte-level composition; family varies
        .choice => |ids| {
            if (ids.len == 0) return null;
            var f: ?Fam = null;
            for (ids) |c| {
                const cf = familyOf(g, c) orelse return null;
                if (f == null) {
                    f = cf;
                } else if (f.? != cf) return null;
            }
            return f;
        },
        .comb => return null,
    }
}

/// Both sides are object families: disjointness by a mandatory key whose
/// value one side pins to a constant the other side rejects.
fn objDisjoint(g: View, a: std.mem.Allocator, x: grammar.NodeId, y: grammar.NodeId, depth: u32) Tri {
    if (objKeyClash(g, a, x, y, depth) == .yes) return .yes;
    if (objKeyClash(g, a, y, x, depth) == .yes) return .yes;
    return .unknown;
}

/// .yes when A has a mandatory key every B-value rejects (so A and B are
/// disjoint); .no when no such key was found conclusively; .unknown when a
/// verdict hit an undecided membership.
fn objKeyClash(g: View, a: std.mem.Allocator, A: grammar.NodeId, B: grammar.NodeId, depth: u32) Tri {
    const aprops: []const grammar.Prop = switch (g.node(A).*) {
        .object => |o| o.props,
        .open_obj => |o| o.props,
        else => return .no,
    };
    var unk = false;
    for (aprops) |p| {
        if (!p.required) continue;
        const v1 = constValueOf(g, a, p.value) orelse continue;
        const name = propName(g, p.key);
        switch (g.node(B).*) {
            .object => |bo| {
                if (declIndex(g, bo.props, name)) |bi| {
                    switch (acceptsDepth(g, a, bo.props[bi].value, v1, depth + 1)) {
                        .no => return .yes,
                        .unknown => unk = true,
                        .yes => {},
                    }
                } else return .yes; // closed B rejects the mandatory key
            },
            .open_obj => |bo| {
                if (bo.names_forbidden) return .yes;
                if (declIndex(g, bo.props, name)) |bi| {
                    switch (acceptsDepth(g, a, bo.props[bi].value, v1, depth + 1)) {
                        .no => return .yes,
                        .unknown => unk = true,
                        .yes => {},
                    }
                } else {
                    if (bo.prop_names) |pn| {
                        switch (acceptsDepth(g, a, pn, Val{ .str = name }, depth + 1)) {
                            .no => return .yes,
                            .unknown => unk = true,
                            .yes => {},
                        }
                    }
                    const vnode = dispatchValue(g, &bo, name) orelse return .unknown;
                    switch (acceptsDepth(g, a, vnode, v1, depth + 1)) {
                        .no => return .yes,
                        .unknown => unk = true,
                        .yes => {},
                    }
                }
            },
            else => return .no,
        }
    }
    return if (unk) .unknown else .no;
}

/// Both sides are number families: disjointness of bound envelopes.
/// Single constants were already handled by the membership rule in
/// disjointDepth; here both sides are range-shaped.
fn numDisjoint(g: View, a: std.mem.Allocator, x: grammar.NodeId, y: grammar.NodeId) Tri {
    _ = a;
    const xr = envelopeOf(g, x);
    const yr = envelopeOf(g, y);
    if (xr == null or yr == null) return .unknown;
    if (envAbove(xr.?, yr.?) or envAbove(yr.?, xr.?)) return .yes;
    return .unknown;
}

const Envelope = struct {
    lo: ?grammar.NumConst,
    lo_excl: bool,
    hi: ?grammar.NumConst,
    hi_excl: bool,
};

/// The (possibly open) bound envelope of a numeric node.
fn envelopeOf(g: View, node: grammar.NodeId) ?Envelope {
    return switch (g.node(node).*) {
        .num_range => |nr| .{
            .lo = nr.min,
            .lo_excl = nr.min_excl,
            .hi = nr.max,
            .hi_excl = nr.max_excl,
        },
        else => null,
    };
}

/// r lies strictly above s: r.lo > s.hi, or equal with either side open.
fn envAbove(r: Envelope, s: Envelope) bool {
    if (r.lo == null or s.hi == null) return false;
    return switch (grammar.cmpNumConst(r.lo.?, s.hi.?)) {
        .gt => true,
        .eq => r.lo_excl or s.hi_excl,
        .lt => false,
    };
}

/// Both sides are string families: length-window disjointness.
fn strDisjoint(g: View, a: std.mem.Allocator, x: grammar.NodeId, y: grammar.NodeId) Tri {
    _ = a;
    const xw = strWindow(g, x);
    const yw = strWindow(g, y);
    if (xw != null and yw != null) {
        if (xw.?.hi < yw.?.lo or yw.?.hi < xw.?.lo) return .yes;
    }
    return .unknown;
}

const StrWin = struct { lo: u32, hi: u32 };

fn strWindow(g: View, node: grammar.NodeId) ?StrWin {
    switch (g.node(node).*) {
        .str => |sc| return .{ .lo = sc.min_len, .hi = sc.max_len },
        .str_excl => |se| return .{ .lo = se.min_len, .hi = se.max_len },
        .str_pat => |sp| return .{ .lo = sp.min_len, .hi = sp.max_len },
        else => return null,
    }
}

// ---------------------------------------------------------------------------
// tests
// ---------------------------------------------------------------------------

const testing = std.testing;
const schema = @import("schema.zig");
const work_mod = @import("work.zig");

fn compileV1(a: std.mem.Allocator, src: []const u8) !grammar.Grammar {
    var diag: schema.Diagnostic = .{};
    var w0 = work_mod.Work{};
    return schema.compile(a, src, 64, &diag, &w0, false, .spec_v1, "");
}

fn numEq(a: std.mem.Allocator, x: grammar.NumConst, want: []const u8) !bool {
    const w = (try parseNumBytes(a, want)) orelse return false;
    return grammar.cmpNumConst(x, w) == .eq;
}

test "numPlusPower: exact signed decimal addition" {
    var arena = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const two = (try parseNumBytes(a, "2")).?;
    try testing.expect(try numEq(a, try numPlusPower(a, two, 0), "3"));
    try testing.expect(try numEq(a, try numPlusPower(a, two, -1), "2.1"));
    try testing.expect(try numEq(a, try numPlusPower(a, two, 2), "102"));
    const neg = (try parseNumBytes(a, "-1.5")).?;
    try testing.expect(try numEq(a, try numPlusPower(a, neg, 0), "-0.5"));
    try testing.expect(try numEq(a, try numPlusPower(a, neg, 1), "8.5"));
    const nine = (try parseNumBytes(a, "9.99")).?;
    try testing.expect(try numEq(a, try numPlusPower(a, nine, -2), "10"));
}

test "ceilInt / floorInt: exact decimal rounding" {
    var arena = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    try testing.expect(try numEq(a, try ceilInt(a, (try parseNumBytes(a, "2.1")).?), "3"));
    try testing.expect(try numEq(a, try ceilInt(a, (try parseNumBytes(a, "2")).?), "2"));
    try testing.expect(try numEq(a, try ceilInt(a, (try parseNumBytes(a, "-2.1")).?), "-2"));
    try testing.expect(try numEq(a, try floorInt(a, (try parseNumBytes(a, "2.9")).?), "2"));
    try testing.expect(try numEq(a, try floorInt(a, (try parseNumBytes(a, "-2.1")).?), "-3"));
    try testing.expect(try numEq(a, try ceilInt(a, (try parseNumBytes(a, "0.5")).?), "1"));
    try testing.expect(try numEq(a, try ceilInt(a, (try parseNumBytes(a, "-0.5")).?), "0"));
}

test "intInRange: bounds and exclusivity" {
    var arena = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    // [2, 4] -> 2
    try testing.expect(try numEq(a, (try intInRange(a, .{
        .min = (try parseNumBytes(a, "2")).?,
        .max = (try parseNumBytes(a, "4")).?,
    })).?, "2"));
    // (2, 4] -> 3
    try testing.expect(try numEq(a, (try intInRange(a, .{
        .min = (try parseNumBytes(a, "2")).?,
        .min_excl = true,
        .max = (try parseNumBytes(a, "4")).?,
    })).?, "3"));
    // (2, 3) -> none
    try testing.expect((try intInRange(a, .{
        .min = (try parseNumBytes(a, "2")).?,
        .min_excl = true,
        .max = (try parseNumBytes(a, "3")).?,
        .max_excl = true,
    })) == null);
    // [2.5, 2.9] -> none
    try testing.expect((try intInRange(a, .{
        .min = (try parseNumBytes(a, "2.5")).?,
        .max = (try parseNumBytes(a, "2.9")).?,
    })) == null);
    // unbounded -> 0
    try testing.expect(try numEq(a, (try intInRange(a, .{})).?, "0"));
}

test "acceptsValue: leaf and comb membership" {
    var arena = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var g = try compileV1(a, "{\"oneOf\":[{\"type\":\"integer\"},{\"minimum\":2}]}");
    defer g.deinit();
    const v = viewOf(&g);
    const root = g.root;
    const one = Val{ .num = try numFromI64(a, 1) };
    const two = Val{ .num = try numFromI64(a, 2) };
    const half = Val{ .num = try numFromDigits(a, false, "25", -1) };
    try testing.expectEqual(Tri.yes, acceptsValue(v, a, root, one)); // integer, < 2
    try testing.expectEqual(Tri.no, acceptsValue(v, a, root, two)); // both vote
    try testing.expectEqual(Tri.yes, acceptsValue(v, a, root, half)); // 2.5: only the range votes
    // "x" votes only the second branch: minimum ignores non-numbers.
    try testing.expectEqual(Tri.yes, acceptsValue(v, a, root, Val{ .str = "x" }));
}

test "acceptsValue: open object with required, fallback, prop bounds" {
    var arena = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var g = try compileV1(a,
        \\{"type":"object","properties":{"a":{"type":"integer"}},"required":["a"],"minProperties":1,"maxProperties":2}
    );
    defer g.deinit();
    const v = viewOf(&g);
    const good = Val{ .obj = @constCast(&[_]Entry{.{ .key = "a", .value = Val{ .num = try numFromI64(a, 7) } }}) };
    try testing.expectEqual(Tri.yes, acceptsValue(v, a, g.root, good));
    const missing = Val{ .obj = &.{} };
    try testing.expectEqual(Tri.no, acceptsValue(v, a, g.root, missing));
    const extra = Val{ .obj = @constCast(&[_]Entry{
        .{ .key = "a", .value = Val{ .num = try numFromI64(a, 7) } },
        .{ .key = "b", .value = Val{ .str = "s" } },
    }) };
    try testing.expectEqual(Tri.yes, acceptsValue(v, a, g.root, extra));
    const wrong_type = Val{ .obj = @constCast(&[_]Entry{.{ .key = "a", .value = Val{ .str = "s" } }}) };
    try testing.expectEqual(Tri.no, acceptsValue(v, a, g.root, wrong_type));
}

test "witness: verified members of common shapes" {
    var arena = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const cases = [_][]const u8{
        "{\"type\":\"integer\"}",
        "{\"type\":\"number\",\"minimum\":2}",
        "{\"type\":\"string\",\"minLength\":2}",
        "{\"enum\":[\"red\",\"green\"]}",
        "{\"const\":{\"x\":[1,2]}}",
        "{\"type\":\"array\",\"items\":{\"type\":\"integer\"},\"minItems\":2}",
        "{\"type\":\"array\",\"contains\":{\"type\":\"integer\"},\"minContains\":2}",
        "{\"allOf\":[{\"type\":\"integer\"},{\"minimum\":2}]}",
        "{\"oneOf\":[{\"type\":\"integer\"},{\"minimum\":2}]}",
        "{\"type\":\"object\",\"required\":[\"a\"],\"properties\":{\"a\":{\"type\":\"integer\"}}}",
        "{\"type\":\"object\",\"minProperties\":2}",
        "{\"if\":{\"type\":\"integer\"},\"then\":{\"minimum\":5}}",
        "{\"type\":\"string\",\"pattern\":\"^a+$\"}",
        "{\"multipleOf\":3}",
    };
    for (cases) |src| {
        var g = try compileV1(a, src);
        defer g.deinit();
        const v = viewOf(&g);
        const w = witness(v, a, g.root) orelse {
            std.debug.print("no witness for {s}\n", .{src});
            return error.TestUnexpectedResult;
        };
        try testing.expectEqual(Tri.yes, acceptsValue(v, a, g.root, w));
    }
}

test "disjoint: type partition, constants, discriminated objects" {
    var arena = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    // oneOf over discriminated objects: pairwise disjoint branches
    var g = try compileV1(a,
        \\{"oneOf":[
        \\{"type":"object","required":["t"],"properties":{"t":{"const":"rect"},"w":{"type":"integer"}}},
        \\{"type":"object","required":["t"],"properties":{"t":{"const":"circle"},"r":{"type":"integer"}}}
        \\]}
    );
    defer g.deinit();
    const v = viewOf(&g);
    const root = g.node(g.root).*;
    const cb = switch (root) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try testing.expectEqual(Tri.yes, disjoint(v, a, cb.branches[0], cb.branches[1]));
    // same branch is not disjoint from itself
    try testing.expectEqual(Tri.no, disjoint(v, a, cb.branches[0], cb.branches[0]));

    var g2 = try compileV1(a, "{\"allOf\":[{\"type\":\"integer\"},{\"type\":\"string\"}]}");
    defer g2.deinit();
    const v2 = viewOf(&g2);
    const cb2 = switch (g2.node(g2.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try testing.expectEqual(Tri.yes, disjoint(v2, a, cb2.branches[0], cb2.branches[1]));
}
