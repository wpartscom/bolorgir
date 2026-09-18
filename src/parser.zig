const std = @import("std");
const grammar = @import("grammar.zig");

pub const MAX_DEPTH_CAP = 64;
pub const MAX_THREADS_CAP = 64;

pub const StrState = enum(u8) { open, normal, escape, u0, u00, uhex1, uhex2 };
pub const NumState = enum(u8) { start, minus, zero_complete, int_digits, dot, frac_digits, exp, exp_sign, exp_digits };
pub const RepeatPhase = enum(u8) { open, body, body_after_comma, sep };
pub const ObjPhase = enum(u8) { open, key, key_after_comma, key_lit, value, sep };

pub const StrFrame = struct { node: grammar.NodeId, count: u32, state: StrState, rem: u8, lo: u8, hi: u8 };
pub const NumFrame = struct { node: grammar.NodeId, st: NumState };

pub const Frame = union(enum) {
    literal: struct { node: grammar.NodeId, off: u32 },
    str: StrFrame,
    int_v: NumFrame,
    num_v: NumFrame,
    seq: struct { node: grammar.NodeId, idx: u32 },
    repeat: struct { node: grammar.NodeId, count: u32, phase: RepeatPhase },
    // In key_lit phase, idx temporarily holds the byte offset into the
    // `"key":` literal of the selected property cur; the old idx is no
    // longer needed and is restored as cur+1 on entering sep.
    object: struct { node: grammar.NodeId, idx: u32, cur: u32, phase: ObjPhase },
};

pub const Thread = struct { frames: [MAX_DEPTH_CAP]Frame, len: u16 };
pub const State = struct {
    threads: [MAX_THREADS_CAP]Thread,
    n: u16,
    max_threads: u16,
};

pub const Error = error{ Parse, ResourceLimit };

fn copyThread(dst: *Thread, src: *const Thread) void {
    dst.len = src.len;
    @memcpy(dst.frames[0..src.len], src.frames[0..src.len]);
}

fn newFrame(t: *Thread) error{ResourceLimit}!*Frame {
    if (t.len >= MAX_DEPTH_CAP) return error.ResourceLimit;
    const f = &t.frames[t.len];
    @memset(std.mem.asBytes(f), 0);
    t.len += 1;
    return f;
}

fn spawnThread(st: *State, src_idx: usize) error{ResourceLimit}!usize {
    if (st.n >= st.max_threads) return error.ResourceLimit;
    const idx = st.n;
    copyThread(&st.threads[idx], &st.threads[src_idx]);
    st.n += 1;
    return idx;
}

fn pushNode(g: *const grammar.Grammar, st: *State, t_idx: usize, node_id: grammar.NodeId) error{ResourceLimit}!void {
    const t = &st.threads[t_idx];
    switch (g.node(node_id).*) {
        .literal => |lit| {
            if (lit.len == 0) return afterChild(g, st, t_idx);
            const f = try newFrame(t);
            f.* = .{ .literal = .{ .node = node_id, .off = 0 } };
        },
        .str => {
            const f = try newFrame(t);
            f.* = .{ .str = .{ .node = node_id, .count = 0, .state = .open, .rem = 0, .lo = 0, .hi = 0 } };
        },
        .int_v => {
            const f = try newFrame(t);
            f.* = .{ .int_v = undefined };
            f.int_v.node = node_id;
            f.int_v.st = .start;
        },
        .num_v => {
            const f = try newFrame(t);
            f.* = .{ .num_v = undefined };
            f.num_v.node = node_id;
            f.num_v.st = .start;
        },
        .seq => |children| {
            if (children.len == 0) return afterChild(g, st, t_idx);
            const f = try newFrame(t);
            f.* = .{ .seq = .{ .node = node_id, .idx = 0 } };
            try pushNode(g, st, t_idx, children[0]);
        },
        .choice => |alts| {
            if (alts.len == 0) return error.ResourceLimit;
            var i: usize = 1;
            while (i < alts.len) : (i += 1) {
                const ni = try spawnThread(st, t_idx);
                try pushNode(g, st, ni, alts[i]);
            }
            try pushNode(g, st, t_idx, alts[0]);
        },
        .repeat => {
            const f = try newFrame(t);
            f.* = .{ .repeat = undefined };
            f.repeat.node = node_id;
            f.repeat.count = 0;
            f.repeat.phase = .open;
        },
        .object => {
            const f = try newFrame(t);
            f.* = .{ .object = undefined };
            f.object.node = node_id;
            f.object.idx = 0;
            f.object.cur = 0;
            f.object.phase = .open;
        },
    }
}

fn afterChild(g: *const grammar.Grammar, st: *State, t_idx: usize) error{ResourceLimit}!void {
    const t = &st.threads[t_idx];
    while (t.len > 0) {
        const f = &t.frames[t.len - 1];
        switch (f.*) {
            .seq => {
                const children = g.node(f.seq.node).seq;
                f.seq.idx += 1;
                if (f.seq.idx >= children.len) {
                    t.len -= 1;
                    continue;
                }
                try pushNode(g, st, t_idx, children[f.seq.idx]);
                return;
            },
            .repeat => {
                f.repeat.count += 1;
                f.repeat.phase = .sep;
                return;
            },
            .object => {
                if (f.object.phase == .value) {
                    f.object.phase = .sep;
                    f.object.idx = f.object.cur + 1;
                }
                return;
            },
            else => return,
        }
    }
}

fn stepSpawned(g: *const grammar.Grammar, st: *State, dead: *[MAX_THREADS_CAP]bool, from: usize, b: u8) Error!void {
    var j = from;
    while (j < st.n) {
        const before = st.n;
        stepThread(g, st, dead, j, b) catch |err| switch (err) {
            error.Parse => dead[j] = true,
            error.ResourceLimit => return error.ResourceLimit,
        };
        j += 1 + (st.n - before);
    }
}

fn noRequiredFrom(props: []const grammar.Prop, idx: u32) bool {
    var i: usize = idx;
    while (i < props.len) : (i += 1) {
        if (props[i].required) return false;
    }
    return true;
}

fn stepThread(g: *const grammar.Grammar, st: *State, dead: *[MAX_THREADS_CAP]bool, t_idx: usize, b: u8) Error!void {
    const t = &st.threads[t_idx];
    retry: while (true) {
        if (t.len == 0) return error.Parse;
        const f = &t.frames[t.len - 1];
        switch (f.*) {
            .literal => {
                const lit = g.node(f.literal.node).literal;
                const bytes = g.literalBytes(lit);
                if (b != bytes[f.literal.off]) return error.Parse;
                f.literal.off += 1;
                if (f.literal.off == lit.len) {
                    t.len -= 1;
                    try afterChild(g, st, t_idx);
                }
                return;
            },
            .str => {
                switch (strFeed(g, &f.str, b)) {
                    .err => return error.Parse,
                    .consumed => return,
                    .done => {
                        t.len -= 1;
                        try afterChild(g, st, t_idx);
                        return;
                    },
                }
            },
            .int_v => {
                switch (numFeed(&f.int_v, b, true)) {
                    .consumed => return,
                    .err => return error.Parse,
                    .complete_pop => {
                        t.len -= 1;
                        const n0 = st.n;
                        try afterChild(g, st, t_idx);
                        try stepSpawned(g, st, dead, n0, b);
                        continue :retry;
                    },
                }
            },
            .num_v => {
                switch (numFeed(&f.num_v, b, false)) {
                    .consumed => return,
                    .err => return error.Parse,
                    .complete_pop => {
                        t.len -= 1;
                        const n0 = st.n;
                        try afterChild(g, st, t_idx);
                        try stepSpawned(g, st, dead, n0, b);
                        continue :retry;
                    },
                }
            },
            .repeat => {
                const rep = g.node(f.repeat.node).repeat;
                switch (f.repeat.phase) {
                    .open => {
                        if (b != '[') return error.Parse;
                        f.repeat.phase = .body;
                        return;
                    },
                    .body => {
                        if (f.repeat.count >= rep.max) {
                            if (b != ']') return error.Parse;
                            t.len -= 1;
                            try afterChild(g, st, t_idx);
                            return;
                        }
                        if (b == ']') {
                            if (f.repeat.count < rep.min) return error.Parse;
                            t.len -= 1;
                            try afterChild(g, st, t_idx);
                            return;
                        }
                        const n0 = st.n;
                        try pushNode(g, st, t_idx, rep.item);
                        try stepSpawned(g, st, dead, n0, b);
                        continue :retry;
                    },
                    .sep => {
                        if (b == ',') {
                            if (f.repeat.count >= rep.max) return error.Parse;
                            f.repeat.phase = .body_after_comma;
                            return;
                        }
                        if (b == ']') {
                            if (f.repeat.count < rep.min) return error.Parse;
                            t.len -= 1;
                            try afterChild(g, st, t_idx);
                            return;
                        }
                        return error.Parse;
                    },
                    .body_after_comma => {
                        // After a comma an element is mandatory: ']' is
                        // forbidden (trailing comma is never allowed).
                        if (b == ']') return error.Parse;
                        const n0 = st.n;
                        try pushNode(g, st, t_idx, rep.item);
                        try stepSpawned(g, st, dead, n0, b);
                        continue :retry;
                    },
                }
            },
            .object => {
                const props = g.node(f.object.node).object;
                switch (f.object.phase) {
                    .open => {
                        if (b != '{') return error.Parse;
                        f.object.phase = .key;
                        return;
                    },
                    .key, .key_after_comma => {
                        if (b == '}') {
                            // After a comma a key is mandatory: trailing
                            // comma is always forbidden.
                            if (f.object.phase == .key_after_comma) return error.Parse;
                            if (!noRequiredFrom(props, f.object.idx)) return error.Parse;
                            t.len -= 1;
                            try afterChild(g, st, t_idx);
                            return;
                        }
                        if (b != '"') return error.Parse;
                        const first = f.object.idx;
                        var cand: u32 = 0;
                        var i: u32 = first;
                        while (i < props.len) : (i += 1) {
                            cand += 1;
                            if (props[i].required) break;
                        }
                        if (cand == 0) return error.Parse;
                        const n0 = st.n;
                        var k: u32 = 1;
                        while (k < cand) : (k += 1) {
                            const ni = try spawnThread(st, t_idx);
                            const nf = &st.threads[ni].frames[t.len - 1];
                            nf.object.cur = first + k;
                            nf.object.idx = 0;
                            nf.object.phase = .key_lit;
                        }
                        f.object.cur = first;
                        f.object.idx = 0;
                        f.object.phase = .key_lit;
                        try stepSpawned(g, st, dead, n0, b);
                        continue :retry;
                    },
                    .key_lit => {
                        const kb = g.literalBytes(props[f.object.cur].key);
                        if (b != kb[f.object.idx]) return error.Parse;
                        f.object.idx += 1;
                        if (f.object.idx == kb.len) {
                            f.object.phase = .value;
                            try pushNode(g, st, t_idx, props[f.object.cur].value);
                        }
                        return;
                    },
                    .value => return error.Parse,
                    .sep => {
                        if (b == ',') {
                            // A comma only makes sense while unvisited
                            // properties remain; otherwise it is a
                            // guaranteed trailing comma.
                            if (f.object.idx >= props.len) return error.Parse;
                            f.object.phase = .key_after_comma;
                            return;
                        }
                        if (b == '}') {
                            if (!noRequiredFrom(props, f.object.idx)) return error.Parse;
                            t.len -= 1;
                            try afterChild(g, st, t_idx);
                            return;
                        }
                        return error.Parse;
                    },
                }
            },
            .seq => return error.Parse,
        }
    }
}

const NumFeedResult = enum { consumed, complete_pop, err };

fn numComplete(st: NumState) bool {
    return st == .zero_complete or st == .int_digits or st == .frac_digits or st == .exp_digits;
}

fn isDigit(b: u8) bool {
    return b >= '0' and b <= '9';
}

fn isDigit19(b: u8) bool {
    return b >= '1' and b <= '9';
}

fn numFeed(nf: *NumFrame, b: u8, is_int: bool) NumFeedResult {
    switch (nf.st) {
        .start => {
            if (b == '-') {
                nf.st = .minus;
                return .consumed;
            }
            if (b == '0') {
                nf.st = .zero_complete;
                return .consumed;
            }
            if (isDigit19(b)) {
                nf.st = .int_digits;
                return .consumed;
            }
            return .err;
        },
        .minus => {
            if (b == '0') {
                nf.st = .zero_complete;
                return .consumed;
            }
            if (isDigit19(b)) {
                nf.st = .int_digits;
                return .consumed;
            }
            return .err;
        },
        .zero_complete => {
            if (!is_int and b == '.') {
                nf.st = .dot;
                return .consumed;
            }
            if (!is_int and (b == 'e' or b == 'E')) {
                nf.st = .exp;
                return .consumed;
            }
            return .complete_pop;
        },
        .int_digits => {
            if (isDigit(b)) return .consumed;
            if (!is_int and b == '.') {
                nf.st = .dot;
                return .consumed;
            }
            if (!is_int and (b == 'e' or b == 'E')) {
                nf.st = .exp;
                return .consumed;
            }
            return .complete_pop;
        },
        .dot => {
            if (isDigit(b)) {
                nf.st = .frac_digits;
                return .consumed;
            }
            return .err;
        },
        .frac_digits => {
            if (isDigit(b)) return .consumed;
            if (b == 'e' or b == 'E') {
                nf.st = .exp;
                return .consumed;
            }
            return .complete_pop;
        },
        .exp => {
            if (b == '+' or b == '-') {
                nf.st = .exp_sign;
                return .consumed;
            }
            if (isDigit(b)) {
                nf.st = .exp_digits;
                return .consumed;
            }
            return .err;
        },
        .exp_sign => {
            if (isDigit(b)) {
                nf.st = .exp_digits;
                return .consumed;
            }
            return .err;
        },
        .exp_digits => {
            if (isDigit(b)) return .consumed;
            return .complete_pop;
        },
    }
}

const StrFeedResult = enum { consumed, done, err };

fn lowerHexVal(b: u8) ?u8 {
    if (b >= '0' and b <= '9') return b - '0';
    if (b >= 'a' and b <= 'f') return b - 'a' + 10;
    return null;
}

fn strIncCount(g: *const grammar.Grammar, sf: *StrFrame) StrFeedResult {
    const sc = g.node(sf.node).str;
    if (sf.count >= sc.max_len) return .err;
    sf.count += 1;
    return .consumed;
}

fn strFeed(g: *const grammar.Grammar, sf: *StrFrame, b: u8) StrFeedResult {
    switch (sf.state) {
        .open => {
            if (b != '"') return .err;
            sf.state = .normal;
            return .consumed;
        },
        .normal => {
            if (sf.rem > 0) {
                if (b < sf.lo or b > sf.hi) return .err;
                sf.rem -= 1;
                if (sf.rem > 0) {
                    sf.lo = 0x80;
                    sf.hi = 0xBF;
                    return .consumed;
                }
                return strIncCount(g, sf);
            }
            if (b == '"') {
                const sc = g.node(sf.node).str;
                if (sf.count < sc.min_len) return .err;
                return .done;
            }
            if (b < 0x20) return .err;
            // Start of a new character (escape, single byte, lead byte of a
            // multi-byte sequence): at count == max completing the character
            // would exceed maxLength, so forbid it immediately.
            if (sf.count >= g.node(sf.node).str.max_len) return .err;
            if (b == '\\') {
                sf.state = .escape;
                return .consumed;
            }
            if (b < 0x80) return strIncCount(g, sf);
            if (b >= 0xC2 and b <= 0xDF) {
                sf.rem = 1;
                sf.lo = 0x80;
                sf.hi = 0xBF;
                return .consumed;
            }
            if (b == 0xE0) {
                sf.rem = 2;
                sf.lo = 0xA0;
                sf.hi = 0xBF;
                return .consumed;
            }
            if ((b >= 0xE1 and b <= 0xEC) or b == 0xEE or b == 0xEF) {
                sf.rem = 2;
                sf.lo = 0x80;
                sf.hi = 0xBF;
                return .consumed;
            }
            if (b == 0xED) {
                sf.rem = 2;
                sf.lo = 0x80;
                sf.hi = 0x9F;
                return .consumed;
            }
            if (b == 0xF0) {
                sf.rem = 3;
                sf.lo = 0x90;
                sf.hi = 0xBF;
                return .consumed;
            }
            if (b >= 0xF1 and b <= 0xF3) {
                sf.rem = 3;
                sf.lo = 0x80;
                sf.hi = 0xBF;
                return .consumed;
            }
            if (b == 0xF4) {
                sf.rem = 3;
                sf.lo = 0x80;
                sf.hi = 0x8F;
                return .consumed;
            }
            return .err;
        },
        .escape => {
            switch (b) {
                '"', '\\', 'b', 'f', 'n', 'r', 't' => {
                    sf.state = .normal;
                    return strIncCount(g, sf);
                },
                'u' => {
                    sf.state = .u0;
                    return .consumed;
                },
                else => return .err,
            }
        },
        .u0 => {
            if (b != '0') return .err;
            sf.state = .u00;
            return .consumed;
        },
        .u00 => {
            if (b != '0') return .err;
            sf.state = .uhex1;
            return .consumed;
        },
        .uhex1 => {
            const v = lowerHexVal(b) orelse return .err;
            sf.lo = v;
            sf.state = .uhex2;
            return .consumed;
        },
        .uhex2 => {
            const v = lowerHexVal(b) orelse return .err;
            const code: u8 = sf.lo * 16 + v;
            if (code >= 0x20) return .err;
            if (code == 0x08 or code == 0x09 or code == 0x0A or code == 0x0C or code == 0x0D) return .err;
            sf.lo = 0;
            sf.state = .normal;
            return strIncCount(g, sf);
        },
    }
}

fn feedWork(g: *const grammar.Grammar, work: *State, bytes: []const u8) Error!void {
    var dead = [_]bool{false} ** MAX_THREADS_CAP;
    for (bytes) |b| {
        const n_start = work.n;
        var j: usize = 0;
        while (j < n_start) : (j += 1) {
            if (dead[j]) continue;
            stepThread(g, work, &dead, j, b) catch |err| switch (err) {
                error.Parse => dead[j] = true,
                error.ResourceLimit => return error.ResourceLimit,
            };
        }
        var w: usize = 0;
        var r: usize = 0;
        while (r < work.n) : (r += 1) {
            if (dead[r]) continue;
            if (w != r) copyThread(&work.threads[w], &work.threads[r]);
            dead[w] = false;
            w += 1;
        }
        var k = w;
        while (k < work.n) : (k += 1) dead[k] = false;
        work.n = @intCast(w);
        if (w == 0) return error.Parse;
    }
}

pub fn initState(g: *const grammar.Grammar, max_threads: u16) error{ResourceLimit}!State {
    if (max_threads == 0 or max_threads > MAX_THREADS_CAP) return error.ResourceLimit;
    var st: State = undefined;
    st.n = 1;
    st.max_threads = max_threads;
    st.threads[0].len = 0;
    try pushNode(g, &st, 0, g.root);
    return st;
}

// Per-thread scratch state for feedBytes. Deliberately a threadlocal
// instead of a stack local: a stack `var work: State = undefined;` is
// filled with the 0xAA undefined pattern in safe builds, and that fill
// (≈80 KiB) was emitted per call — and, worse, per loop iteration, so a
// 64-thread state (e.g. an enum of 64 literals) cost ~100 µs per accept
// (64 × 80 KiB memsets). feedBytes is not reentrant (the parser never
// calls back into it), so one scratch per thread is safe; the initial
// fill happens once per thread instead of once per call.
threadlocal var feed_work: State = undefined;

pub fn feedBytes(g: *const grammar.Grammar, in: *const State, bytes: []const u8, out: *State) Error!void {
    const work = &feed_work;
    out.n = 0;
    out.max_threads = in.max_threads;
    for (0..in.n) |ti| {
        work.max_threads = in.max_threads;
        work.n = 1;
        copyThread(&work.threads[0], &in.threads[ti]);
        feedWork(g, work, bytes) catch |err| switch (err) {
            error.Parse => continue,
            error.ResourceLimit => return error.ResourceLimit,
        };
        for (0..work.n) |wi| {
            if (out.n >= out.max_threads) return error.ResourceLimit;
            copyThread(&out.threads[out.n], &work.threads[wi]);
            out.n += 1;
        }
    }
    if (out.n == 0) return error.Parse;
}

pub fn canEnd(g: *const grammar.Grammar, st: *const State) bool {
    for (st.threads[0..st.n]) |*t| {
        if (threadCanEnd(g, t)) return true;
    }
    return false;
}

fn threadCanEnd(g: *const grammar.Grammar, src: *const Thread) bool {
    var t: Thread = undefined;
    copyThread(&t, src);
    while (t.len > 0) {
        const f = &t.frames[t.len - 1];
        switch (f.*) {
            .int_v => {
                if (!numComplete(f.int_v.st)) return false;
                t.len -= 1;
            },
            .num_v => {
                if (!numComplete(f.num_v.st)) return false;
                t.len -= 1;
            },
            .seq => {
                const children = g.node(f.seq.node).seq;
                f.seq.idx += 1;
                if (f.seq.idx >= children.len) {
                    t.len -= 1;
                } else if (!virtPushComplete(g, children[f.seq.idx])) return false;
            },
            else => return false,
        }
    }
    return true;
}

fn virtPushComplete(g: *const grammar.Grammar, node_id: grammar.NodeId) bool {
    switch (g.node(node_id).*) {
        .literal => |lit| return lit.len == 0,
        .choice => |alts| {
            for (alts) |a| {
                if (virtPushComplete(g, a)) return true;
            }
            return false;
        },
        .seq => |children| {
            for (children) |c| {
                if (!virtPushComplete(g, c)) return false;
            }
            return true;
        },
        else => return false,
    }
}

/// Cache-key hash of a parser state. Wyhash (8-byte chunks) instead of
/// byte-at-a-time FNV-1a: a 64-thread state (e.g. an enum of 64 literals)
/// hashes in a fraction of a microsecond instead of ~2.5 us, which used
/// to dominate the cache-hit fill_mask cost for such states (the audit's
/// §10.6 p95/p99 mask metric). The hash is only an in-process cache key
/// and collisions are resolved by eqlStates, so the change of function is
/// not observable.
pub fn hashState(st: *const State) u64 {
    var h = std.hash.Wyhash.init(0);
    var buf: [2]u8 = undefined;
    std.mem.writeInt(u16, &buf, st.n, .little);
    h.update(&buf);
    for (st.threads[0..st.n]) |*t| {
        std.mem.writeInt(u16, &buf, t.len, .little);
        h.update(&buf);
        h.update(std.mem.sliceAsBytes(t.frames[0..t.len]));
    }
    return h.final();
}

pub fn eqlStates(a: *const State, b: *const State) bool {
    if (a.n != b.n) return false;
    for (0..a.n) |i| {
        const ta = &a.threads[i];
        const tb = &b.threads[i];
        if (ta.len != tb.len) return false;
        if (!std.mem.eql(u8, std.mem.sliceAsBytes(ta.frames[0..ta.len]), std.mem.sliceAsBytes(tb.frames[0..tb.len]))) return false;
    }
    return true;
}

const testing = std.testing;

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

fn strGrammar(a: std.mem.Allocator, min: u32, max: u32) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addNode(.{ .str = .{ .min_len = min, .max_len = max } });
    return b.finish(arena, .json_schema, root, .{});
}

fn intGrammar(a: std.mem.Allocator) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addNode(.{ .int_v = {} });
    return b.finish(arena, .json_schema, root, .{});
}

fn numGrammar(a: std.mem.Allocator) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addNode(.{ .num_v = {} });
    return b.finish(arena, .json_schema, root, .{});
}

fn intArrayGrammar(a: std.mem.Allocator, min: u32, max: u32) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const item = try b.addNode(.{ .int_v = {} });
    const root = try b.addNode(.{ .repeat = .{ .item = item, .min = min, .max = max } });
    return b.finish(arena, .json_schema, root, .{});
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

fn optObjGrammar(a: std.mem.Allocator) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const int_node = try b.addNode(.{ .int_v = {} });
    const props = try b.copyProps(&[_]grammar.Prop{
        .{ .key = try b.addLiteral("\"a\":"), .value = int_node, .required = false },
        .{ .key = try b.addLiteral("\"b\":"), .value = int_node, .required = true },
    });
    const root = try b.addNode(.{ .object = props });
    return b.finish(arena, .json_schema, root, .{});
}

fn enumObjGrammar(a: std.mem.Allocator) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const la = try b.addLiteralNode("\"a\"");
    const lab = try b.addLiteralNode("\"ab\"");
    const val = try b.addNode(.{ .choice = try b.copyNodeIds(&[_]grammar.NodeId{ la, lab }) });
    const props = try b.copyProps(&[_]grammar.Prop{
        .{ .key = try b.addLiteral("\"x\":"), .value = val, .required = true },
    });
    const root = try b.addNode(.{ .object = props });
    return b.finish(arena, .json_schema, root, .{});
}

fn feedOk(g: *const grammar.Grammar, st: *State, bytes: []const u8) Error!void {
    var out: State = undefined;
    try feedBytes(g, st, bytes, &out);
    st.* = out;
}

test "literal: basic feed, partitions, tail byte rejected" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "hello");
    defer g.deinit();
    var st = try initState(&g, 4);
    try testing.expect(!canEnd(&g, &st));
    try feedOk(&g, &st, "hel");
    try testing.expect(!canEnd(&g, &st));
    try testing.expectError(error.Parse, feedOk(&g, &st, "x"));
    try feedOk(&g, &st, "lo");
    try testing.expect(canEnd(&g, &st));
    try testing.expectError(error.Parse, feedOk(&g, &st, "!"));
}

test "literal len 0 completes instantly" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "");
    defer g.deinit();
    const st = try initState(&g, 4);
    try testing.expect(canEnd(&g, &st));
    var st2 = st;
    try testing.expectError(error.Parse, feedOk(&g, &st2, "x"));
}

test "choice: common prefix a/ab" {
    const alloc = testing.allocator;
    var g = try choiceGrammar(alloc, &.{ "a", "ab" });
    defer g.deinit();
    var st = try initState(&g, 4);
    try testing.expectEqual(@as(u16, 2), st.n);
    {
        var s2: State = undefined;
        try feedBytes(&g, &st, "ab", &s2);
        try testing.expect(canEnd(&g, &s2));
    }
    try feedOk(&g, &st, "a");
    try testing.expect(canEnd(&g, &st));
    try feedOk(&g, &st, "b");
    try testing.expect(canEnd(&g, &st));
}

test "choice exceeding max_threads -> ResourceLimit" {
    const alloc = testing.allocator;
    var g = try choiceGrammar(alloc, &.{ "a", "b", "c" });
    defer g.deinit();
    try testing.expectError(error.ResourceLimit, initState(&g, 2));
    try testing.expectError(error.ResourceLimit, initState(&g, 0));
    var g1 = try litGrammar(alloc, "x");
    defer g1.deinit();
    try testing.expectError(error.ResourceLimit, initState(&g1, 0));
}

test "str: minLength/maxLength in symbols" {
    const alloc = testing.allocator;
    var g = try strGrammar(alloc, 2, 3);
    defer g.deinit();
    {
        var st = try initState(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"a\""));
    }
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "\"ab\"");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "\"abc\"");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"abcd\""));
    }
}

test "str: escapes table" {
    const alloc = testing.allocator;
    var g = try strGrammar(alloc, 0, grammar.UNBOUNDED);
    defer g.deinit();
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "\"a\\n\\t\\r\\b\\f\\\"\\\\\"");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"\\/\""));
    }
    {
        var st = try initState(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"a\x01\""));
    }
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "\"\x7f\"");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "\"\\u001f\"");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"\\u0009\""));
    }
    {
        var st = try initState(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"\\u0041\""));
    }
    {
        var st = try initState(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"\\u001F\""));
    }
    {
        var st = try initState(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"\\'\""));
    }
}

test "str: escapes split across feedBytes calls" {
    const alloc = testing.allocator;
    var g = try strGrammar(alloc, 2, 2);
    defer g.deinit();
    var st = try initState(&g, 4);
    try feedOk(&g, &st, "\"a\\");
    try feedOk(&g, &st, "n\"");
    try testing.expect(canEnd(&g, &st));
    var g1 = try strGrammar(alloc, 1, 1);
    defer g1.deinit();
    var st1 = try initState(&g1, 4);
    try feedOk(&g1, &st1, "\"\\u0");
    try feedOk(&g1, &st1, "01f\"");
    try testing.expect(canEnd(&g1, &st1));
}

test "str: escape counts as one symbol" {
    const alloc = testing.allocator;
    var g = try strGrammar(alloc, 1, 1);
    defer g.deinit();
    var st = try initState(&g, 4);
    try feedOk(&g, &st, "\"\\n\"");
    try testing.expect(canEnd(&g, &st));
}

test "str: utf-8 split, validation, close with pending rem" {
    const alloc = testing.allocator;
    var g = try strGrammar(alloc, 1, 1);
    defer g.deinit();
    var st = try initState(&g, 4);
    try feedOk(&g, &st, "\"\xc3");
    try testing.expectError(error.Parse, feedOk(&g, &st, "\""));
    try feedOk(&g, &st, "\xa9\"");
    try testing.expect(canEnd(&g, &st));
    {
        var s2 = try initState(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &s2, "\"\x80\""));
    }
    {
        var s3 = try initState(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &s3, "\"\xc3\x41\""));
    }
    var g2 = try strGrammar(alloc, 2, 2);
    defer g2.deinit();
    {
        var s4 = try initState(&g2, 4);
        try testing.expectError(error.Parse, feedOk(&g2, &s4, "\"\xc3\xa9\""));
    }
    {
        var s5 = try initState(&g2, 4);
        try testing.expectError(error.Parse, feedOk(&g, &s5, "\"\xed\xa0\x80\""));
    }
}

test "int: forms, leading zero, root canEnd" {
    const alloc = testing.allocator;
    var g = try intGrammar(alloc);
    defer g.deinit();
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "0");
        try testing.expect(canEnd(&g, &st));
        try testing.expectError(error.Parse, feedOk(&g, &st, "1"));
    }
    {
        var st = try initState(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "01"));
    }
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "-12");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "-");
        try testing.expect(!canEnd(&g, &st));
        try testing.expectError(error.Parse, feedOk(&g, &st, "x"));
        try feedOk(&g, &st, "5");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "12");
        try testing.expect(canEnd(&g, &st));
        try testing.expectError(error.Parse, feedOk(&g, &st, "-"));
    }
}

test "num: fraction and exponent" {
    const alloc = testing.allocator;
    var g = try numGrammar(alloc);
    defer g.deinit();
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "1.5e+3");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "1.");
        try testing.expect(!canEnd(&g, &st));
        try feedOk(&g, &st, "5");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "2e");
        try testing.expect(!canEnd(&g, &st));
        try testing.expectError(error.Parse, feedOk(&g, &st, "."));
        try feedOk(&g, &st, "-");
        try testing.expect(!canEnd(&g, &st));
        try feedOk(&g, &st, "2");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, ".5"));
    }
    {
        var st = try initState(&g, 4);
        try feedOk(&g, &st, "0E10");
        try testing.expect(canEnd(&g, &st));
    }
}

test "repeat: number before delimiter, min/max items" {
    const alloc = testing.allocator;
    var g = try intArrayGrammar(alloc, 1, 3);
    defer g.deinit();
    var st = try initState(&g, 4);
    try feedOk(&g, &st, "[12,");
    try testing.expect(!canEnd(&g, &st));
    try feedOk(&g, &st, "3]");
    try testing.expect(canEnd(&g, &st));
    {
        var gm = try intArrayGrammar(alloc, 2, 3);
        defer gm.deinit();
        var s1 = try initState(&gm, 4);
        try testing.expectError(error.Parse, feedOk(&gm, &s1, "[1]"));
        var s2 = try initState(&gm, 4);
        try feedOk(&gm, &s2, "[1,2]");
        try testing.expect(canEnd(&gm, &s2));
    }
    {
        var gx = try intArrayGrammar(alloc, 0, 2);
        defer gx.deinit();
        var s3 = try initState(&gx, 4);
        try feedOk(&gx, &s3, "[1,2");
        try testing.expectError(error.Parse, feedOk(&gx, &s3, ","));
        try feedOk(&gx, &s3, "]");
        try testing.expect(canEnd(&gx, &s3));
        var s4 = try initState(&gx, 4);
        try feedOk(&gx, &s4, "[]");
        try testing.expect(canEnd(&gx, &s4));
        var s5 = try initState(&gx, 4);
        try testing.expectError(error.Parse, feedOk(&gx, &s5, "[1,2,3]"));
    }
}

test "seq node and virtual canEnd cascade" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const lit = try b.addLiteralNode("ab");
    const num = try b.addNode(.{ .int_v = {} });
    const empty = try b.addLiteralNode("");
    const root1 = try b.addNode(.{ .seq = try b.copyNodeIds(&[_]grammar.NodeId{ lit, num }) });
    const root2 = try b.addNode(.{ .seq = try b.copyNodeIds(&[_]grammar.NodeId{ num, empty }) });
    const root = try b.addNode(.{ .choice = try b.copyNodeIds(&[_]grammar.NodeId{ root1, root2 }) });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    {
        var st = try initState(&g, 8);
        try feedOk(&g, &st, "ab12");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "abx"));
    }
    {
        var st = try initState(&g, 8);
        try feedOk(&g, &st, "5");
        try testing.expect(canEnd(&g, &st));
    }
}

test "object: order, optional skip, duplicate key, required guard" {
    const alloc = testing.allocator;
    var g = try objGrammar(alloc);
    defer g.deinit();
    {
        var st = try initState(&g, 8);
        try feedOk(&g, &st, "{\"a\":\"x\"}");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 8);
        try feedOk(&g, &st, "{\"a\":\"x\",\"b\":1}");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "{\"b\":1}"));
    }
    {
        var st = try initState(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "{}"));
    }
    {
        var st = try initState(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "{\"a\":\"x\",\"b\":1,\"b\":2}"));
    }
    {
        var st = try initState(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "{\"a\":\"x\",\"a\":\"y\"}"));
    }
    {
        var st = try initState(&g, 8);
        try feedOk(&g, &st, "{\"a\":\"x\"");
        try testing.expect(!canEnd(&g, &st));
    }
    {
        // DESIGN §1.4: ',' -> key phase; '}' in key phase is allowed when no
        // required properties remain, so {"a":"x",} is accepted per §1.4.
        var st = try initState(&g, 8);
        try feedOk(&g, &st, "{\"a\":\"x\",");
        try testing.expect(!canEnd(&g, &st));
        try feedOk(&g, &st, "\"b\":2}");
        try testing.expect(canEnd(&g, &st));
    }
}

test "object: optional key before required can be skipped" {
    const alloc = testing.allocator;
    var g = try optObjGrammar(alloc);
    defer g.deinit();
    {
        var st = try initState(&g, 8);
        try feedOk(&g, &st, "{\"b\":1}");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 8);
        try feedOk(&g, &st, "{\"a\":1,\"b\":2}");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initState(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "{\"a\":1}"));
    }
}

test "object: enum a/ab value, canEnd waits for separator" {
    const alloc = testing.allocator;
    var g = try enumObjGrammar(alloc);
    defer g.deinit();
    var st = try initState(&g, 8);
    try feedOk(&g, &st, "{\"x\":");
    {
        var s2: State = undefined;
        try feedBytes(&g, &st, "\"ab\"", &s2);
        try feedOk(&g, &s2, "}");
        try testing.expect(canEnd(&g, &s2));
    }
    try feedOk(&g, &st, "\"a\"");
    try testing.expect(!canEnd(&g, &st));
    try feedOk(&g, &st, "}");
    try testing.expect(canEnd(&g, &st));
}

test "single feed carrying many structural chars" {
    const alloc = testing.allocator;
    var g = try objGrammar(alloc);
    defer g.deinit();
    var st = try initState(&g, 8);
    try feedOk(&g, &st, "{\"a\":\"x\",\"b\":12}");
    try testing.expect(canEnd(&g, &st));
}

test "hashState/eqlStates stable across feed partitions" {
    const alloc = testing.allocator;
    var g = try objGrammar(alloc);
    defer g.deinit();
    var s1 = try initState(&g, 8);
    try feedOk(&g, &s1, "{\"a\":\"x\"");
    try feedOk(&g, &s1, ",");
    var s2 = try initState(&g, 8);
    try feedOk(&g, &s2, "{\"a\":\"x\",");
    try testing.expect(eqlStates(&s1, &s2));
    try testing.expectEqual(hashState(&s1), hashState(&s2));
    try feedOk(&g, &s2, "\"b\":1}");
    try testing.expect(!eqlStates(&s1, &s2));
    try testing.expect(canEnd(&g, &s2));
}
