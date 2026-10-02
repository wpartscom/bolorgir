const std = @import("std");
const grammar = @import("grammar.zig");
const json = @import("json.zig");
const pattern = @import("pattern.zig");

pub const MAX_DEPTH_CAP = 64;
// Comb branches are capped at 64 (the branch bitmask is a u64), so one
// nesting level of a max-arity oneOf/anyOf over a 2-branch allOf already
// needs 128 live threads; 64 was below that architectural minimum.
pub const MAX_THREADS_CAP = 128;

pub const StrState = enum(u8) { open, normal, escape, u0, u00, uhex1, uhex2 };
pub const NumState = enum(u8) { start, minus, zero_complete, int_digits, dot, frac_digits, exp, exp_sign, exp_digits };
pub const RepeatPhase = enum(u8) { open, body, body_after_comma, sep };
pub const ObjPhase = enum(u8) { open, key, key_after_comma, key_lit, value, sep };
pub const OpenObjPhase = enum(u8) { open, key, key_after_comma, key_str, colon, value, sep };

pub const StrFrame = struct { node: grammar.NodeId, count: u32, state: StrState, rem: u8, lo: u8, hi: u8 };
pub const NumFrame = struct { node: grammar.NodeId, st: NumState };

/// spec-v1 integer-by-value frame (grammar.Node.int_num): the JSON number
/// automaton of num_v plus enough mantissa/exponent tracking to decide at
/// completion whether the value is an integer: value = M x 10^(e - fl),
/// integer iff M == 0 or e >= fl - tz (tz = trailing zeros of M).
/// Counters saturate at u32 max; saturation cannot flip the verdict for
/// any input within the session byte budget.
pub const IntNumFrame = struct {
    node: grammar.NodeId,
    tz: u32,
    frac_len: u32,
    exp: u32,
    st: NumState,
    nz: bool,
    exp_neg: bool,
};

/// spec-v1 number-constant frame (grammar.Node.num_const): the instance
/// digit stream is matched incrementally against the canonical target
/// (neg, digits, exp10). m: leading = only mantissa zeros so far;
/// matching = consuming significant digits (j = next index into digits);
/// tail = significant digits exhausted, only '0' may follow (counted in
/// tail_z). Equality at completion: instance zero iff target zero;
/// otherwise sign equal and e - frac_len + tail_z == exp10.
pub const NumConstFrame = struct {
    node: grammar.NodeId,
    j: u32,
    frac_len: u32,
    tail_z: u32,
    exp: u32,
    st: NumState,
    m: enum(u8) { leading, matching, tail },
    exp_neg: bool,
    neg: bool,
};

/// Open-object frame (spec-v1). `seen` and `key` are handles into the
/// per-owner Side chunk store (0 = none): `seen` is the persistent set of
/// accepted keys (undeclared keys, or every dispatched key when the node
/// tracks keys for min/maxProperties or dependencies), `key` the raw
/// bytes of the key being read, retained through the key's value (the
/// completion synth reads it to merge dependency-schema constraints) and
/// released when the next key starts. `ann` is the ADR-0006 D4 reserved
/// slot; in P2 it carries the whole-object capture chunk (obj_buf) while
/// the node has schema-kind dependencies, 0 otherwise.
pub const OpenObjFrame = struct {
    node: grammar.NodeId,
    next_idx: u32,
    seen: u32,
    key: u32,
    ann: u32,
    phase: OpenObjPhase,
    // The key-capture string machine is live only in key_str phase; once
    // the key completes, the same bytes hold the dispatched value node
    // while the ':' is awaited (colon phase). Keeping Frame at 32 B.
    u: extern union {
        key: OpenObjKeyState,
        pending: grammar.NodeId,
    },
};

pub const OpenObjKeyState = extern struct { st: StrState, rem: u8, lo: u8, hi: u8 };

/// Boolean-combinator frame (spec-v1 P3, grammar.Node.comb). The branches
/// of one activation parse in parallel threads that share `inst` (a unique
/// per-owner id from Side.next_inst, never recycled, so live instances
/// never collide); `branch` is the branch index this thread carries. The
/// frame consumes no bytes: when its branch parse completes it stays on
/// top until the value boundary is confirmed (the next byte or canEnd),
/// where the group verdict of ADR-0005 D2 is evaluated over the accepting
/// branches of the instance.
pub const CombFrame = struct { node: grammar.NodeId, inst: u32, branch: u8 };

/// spec-v1 P3 string-minus-forbidden-set frame (grammar.Node.str_excl):
/// the str machine plus a walk through the trie of quoted forbidden
/// literals on the same raw bytes (`trie_dead` once off-trie). Completion
/// at a trie terminal spells a forbidden value and is rejected.
pub const StrExclFrame = struct {
    node: grammar.NodeId,
    count: u32,
    trie: u32,
    state: StrState,
    rem: u8,
    lo: u8,
    hi: u8,
    trie_dead: bool,
};

/// spec-v1 P4 regex-constrained string frame (grammar.Node.str_pat): the
/// str machine of StrFrame (canonical escape table, UTF-8 ranges, length
/// bounds in codepoints) plus the pattern DFA state (`dfa`), fed one
/// decoded codepoint per completed character. `cp` accumulates the scalar
/// value of a multi-byte UTF-8 sequence; escapes feed their decoded value.
/// A feed that lands in the DFA's dead state is a mid-string reject (no
/// continuation can match, so the thread dies mask-visibly, ADR-0005); the
/// closing quote requires an accepting DFA state.
pub const StrPatFrame = struct {
    node: grammar.NodeId,
    count: u32,
    dfa: pattern.RunState,
    cp: u32,
    state: StrState,
    rem: u8,
    lo: u8,
    hi: u8,
};

/// spec-v1 P3 number-minus-constants frame (grammar.Node.num_excl): the
/// JSON number automaton shared by up to two forbidden constants; j/tail_z
/// and the mantissa phase m (2 bits per constant: 0 leading, 1 matching,
/// 2 tail, 3 ruled out) track each constant's incremental match. j indexes
/// the constant's digits (at most 400 by the representability limit), so
/// u16 suffices; the frame stays within the 28-byte payload budget that
/// keeps Frame at 32 bytes (ADR-0006).
pub const NumExclFrame = struct {
    node: grammar.NodeId,
    tail_z: [2]u32,
    frac_len: u32,
    exp: u32,
    j: [2]u16,
    st: NumState,
    m: u8,
    exp_neg: bool,
    neg: bool,
};

/// spec-v1 P4 bounded-number frame (grammar.Node.num_range): the JSON
/// number automaton plus an incremental value comparison against the node's
/// canonical decimal bounds. The digit stream is compared against each
/// bound's digits aligned at the leading significant digit (j = next bound
/// digit index, cmp = 2 bits per bound: 0 equal-so-far, 1 less, 2 greater);
/// the order-of-magnitude tiebreak (int_len / frac leading zeros fz /
/// exponent) is applied at the boundary. flags: bit0 neg, bit1 exp_neg,
/// bit2 lead (a significant digit was seen), bit3 om_int (the first
/// significant digit was in the integer part). Counters saturate at u32
/// max; saturation cannot flip the verdict for any input within the
/// session byte budget (same argument as IntNumFrame). The exact verdict
/// is produced only at a confirmed value boundary (ADR-0005).
pub const NumRangeFrame = struct {
    node: grammar.NodeId,
    int_len: u32,
    fz: u32,
    exp: u32,
    j: [2]u16,
    st: NumState,
    cmp: u8,
    flags: u8,
};

/// spec-v1 P4 multipleOf frame (grammar.Node.num_mult): the JSON number
/// automaton plus the running residue of the mantissa modulo the divisor.
/// rem tracks the whole mantissa; rem_d is the residue at the last nonzero
/// digit (the canonical digit string D with trailing zeros stripped); tz
/// counts pending trailing zeros. At the boundary V is a multiple iff
/// V == 0, or s = e - frac_len + tz - div_exp10 >= 0 and
/// rem_d x 10^s == 0 (mod div) (see grammar.NumMult). exp saturates at
/// 2^31 (exp_sat): then |s| is provably past the divisor's 2/5 valuations
/// for any input within the session byte budget and divisibility reduces
/// to rem mod co == 0 (a negative saturated exponent rejects).
pub const NumMultFrame = struct {
    node: grammar.NodeId,
    rem: u32,
    rem_d: u32,
    frac_len: u32,
    tz: u32,
    exp: u32,
    st: NumState,
    flags: u8,
};

pub const Frame = union(enum) {
    literal: struct { node: grammar.NodeId, off: u32 },
    // Shared-prefix literal set (grammar.Node.lit_trie): gnode is the grammar
    // node holding the trie data, node is the current trie node. One frame
    // covers all alternatives of the set, so an enum of 64 values sharing a
    // prefix advances one thread instead of 64.
    lit_trie: struct { gnode: grammar.NodeId, node: u32 },
    str: StrFrame,
    int_v: NumFrame,
    num_v: NumFrame,
    int_num: IntNumFrame,
    num_const: NumConstFrame,
    seq: struct { node: grammar.NodeId, idx: u32 },
    // ann is reserved (ADR-0006 D4), always 0 in P1.
    // P2: `matched` counts contains matches; `tap` is the elem_buf chunk
    // capturing the current element's raw bytes (0 = none, armed only when
    // the node has uniqueItems/contains); `seen` is the seen_values chunk
    // of canonical element signatures for uniqueItems (0 = none).
    repeat: struct { node: grammar.NodeId, count: u32, phase: RepeatPhase, ann: u32, matched: u32, tap: u32, seen: u32 },
    // In key_lit phase, idx temporarily holds the byte offset into the
    // `"key":` literal of the selected property cur; the old idx is no
    // longer needed and is restored as cur+1 on entering sep.
    // ann is reserved (ADR-0006 D4), always 0 in P1.
    object: struct { node: grammar.NodeId, idx: u32, cur: u32, phase: ObjPhase, ann: u32 },
    open_obj: OpenObjFrame,
    comb: CombFrame,
    // Lazy alternation (grammar.Node.choice): the alternatives are not
    // spawned until the first byte arrives, then only the alternatives whose
    // language may begin with that byte (choiceAltMayStart) are spawned.
    // Alternatives whose language may contain the empty word
    // (choiceAltMaybeEmpty) are forked eagerly at push time instead, so a
    // live choice frame never completes without consuming a byte.
    choice: struct { node: grammar.NodeId },
    not_int_num: IntNumFrame,
    str_excl: StrExclFrame,
    str_pat: StrPatFrame,
    num_excl: NumExclFrame,
    num_range: NumRangeFrame,
    num_mult: NumMultFrame,
};

pub const Thread = struct { frames: [MAX_DEPTH_CAP]Frame, len: u16, tap_count: u16 };

/// Frame is in its birth state (as pushed by pushNode). Every birth-state
/// field is byte-driven and never recurs mid-parse: the state enums leave
/// .open/.start on the first byte and never return, progress counters only
/// advance, a completed child always advances a parent progress field (the
/// sole exception is the comb frame, whose completion leaves it on top),
/// and a live choice frame has never dispatched (it pops on its first
/// byte). An all-pristine stack above a comb frame therefore means the
/// branch has consumed no value bytes since the instance was born.
pub fn framePristine(f: *const Frame) bool {
    return switch (f.*) {
        .literal => |fr| fr.off == 0,
        .lit_trie => |fr| fr.node == 0,
        .str => |fr| fr.count == 0 and fr.state == .open,
        .str_excl => |fr| fr.count == 0 and fr.state == .open,
        .str_pat => |fr| fr.count == 0 and fr.state == .open,
        .int_v => |fr| fr.st == .start,
        .num_v => |fr| fr.st == .start,
        .int_num => |fr| fr.st == .start,
        .not_int_num => |fr| fr.st == .start,
        .num_const => |fr| fr.st == .start,
        .num_excl => |fr| fr.st == .start,
        .num_range => |fr| fr.st == .start,
        .num_mult => |fr| fr.st == .start,
        .seq => |fr| fr.idx == 0,
        .repeat => |fr| fr.count == 0 and fr.phase == .open,
        .object => |fr| fr.idx == 0 and fr.phase == .open,
        .open_obj => |fr| fr.next_idx == 0 and fr.phase == .open and fr.seen == 0,
        .comb => true,
        .choice => true,
    };
}
pub const State = struct {
    threads: [MAX_THREADS_CAP]Thread,
    n: u16,
    max_threads: u16,
};

pub const Error = error{ Parse, ResourceLimit, OutOfMemory };

pub const ChunkKind = enum(u8) { seen_keys, key_buf, seen_values, elem_buf, obj_buf };

/// Immutable-once-published side datum (ADR-0006 D2). Refcounted per
/// referencing frame; mutation with refs > 1 copies first (COW).
pub const Chunk = struct {
    refs: u32,
    kind: ChunkKind,
    payload: std.ArrayListUnmanaged(u8),
};

/// Per-owner store for side chunks. A State's frames carry 32-bit handles
/// into this store (0 = none); every State derived from one owner (a
/// session, a precompute run) shares the owner's store. The cache never
/// retains chunks, only their serialized content (chunk blob, D3), so a
/// store dies with its owner.
pub const Side = struct {
    a: std.mem.Allocator,
    chunks: std.ArrayListUnmanaged(?*Chunk) = .{},
    /// Id issued to the next comb activation (spec-v1 P3). Monotonic per
    /// owner, never recycled: live comb groups never share an id.
    next_inst: u32 = 1,

    pub fn init(a: std.mem.Allocator) Side {
        return .{ .a = a };
    }

    /// Frees every remaining chunk regardless of refcount: all States of
    /// the owner must be dead by the time the store dies.
    pub fn deinit(self: *Side) void {
        for (self.chunks.items) |mc| {
            if (mc) |c| {
                c.payload.deinit(self.a);
                self.a.destroy(c);
            }
        }
        self.chunks.deinit(self.a);
        self.* = undefined;
    }

    pub fn chunkAt(self: *const Side, h: u32) *Chunk {
        return self.chunks.items[h - 1].?;
    }

    fn retain(self: *const Side, h: u32) void {
        if (h != 0) self.chunkAt(h).refs += 1;
    }

    fn release(self: *Side, h: u32) void {
        if (h == 0) return;
        const c = self.chunkAt(h);
        c.refs -= 1;
        if (c.refs == 0) {
            c.payload.deinit(self.a);
            self.a.destroy(c);
            self.chunks.items[h - 1] = null;
        }
    }

    fn create(self: *Side, kind: ChunkKind) error{OutOfMemory}!u32 {
        for (self.chunks.items, 0..) |mc, i| {
            if (mc == null) {
                const c = try self.a.create(Chunk);
                c.* = .{ .refs = 1, .kind = kind, .payload = .{} };
                self.chunks.items[i] = c;
                return @intCast(i + 1);
            }
        }
        const c = try self.a.create(Chunk);
        c.* = .{ .refs = 1, .kind = kind, .payload = .{} };
        try self.chunks.append(self.a, c);
        return @intCast(self.chunks.items.len);
    }
};

/// Releases every chunk reference held by the state's threads and marks it
/// empty. Owners must call this before dropping or overwriting a state
/// that may carry chunk references (spec-v1 open objects); it is a cheap
/// no-op scan for chunk-free (canonical-v1) states.
pub fn releaseState(side: *Side, st: *State) void {
    for (st.threads[0..st.n]) |*t| releaseThread(side, t);
    st.n = 0;
}

fn releaseFrameChunks(side: *Side, f: *const Frame) void {
    switch (f.*) {
        .open_obj => |*of| {
            side.release(of.seen);
            side.release(of.key);
            side.release(of.ann);
        },
        .repeat => |*rf| {
            side.release(rf.tap);
            side.release(rf.seen);
        },
        else => {},
    }
}

fn releaseThread(side: *Side, t: *const Thread) void {
    for (t.frames[0..t.len]) |*f| releaseFrameChunks(side, f);
}

/// Pops the top frame of a LIVE state thread, releasing its chunk refs (and
/// balancing tap_count for tap-carrying chunks). Inspection copies made
/// without retainThread (cascadeComplete, combPopThrough) must decrement
/// `len` directly instead - they hold no references to release. A bare
/// `t.len -= 1` on a live thread leaks the popped frame's chunks into the
/// owner store (measured on maskbench o77317: ~45 seen_keys chunks per
/// accepted byte, ~9 KB/byte of session memory, spilling the 8 MiB session
/// budget mid-document).
fn popFrame(side: *Side, t: *Thread) void {
    const f = &t.frames[t.len - 1];
    switch (f.*) {
        .open_obj => |*of| {
            side.release(of.seen);
            side.release(of.key);
            if (of.ann != 0) {
                side.release(of.ann);
                t.tap_count -|= 1;
            }
        },
        .repeat => |*rf| {
            if (rf.tap != 0) {
                side.release(rf.tap);
                t.tap_count -|= 1;
            }
            side.release(rf.seen);
        },
        else => {},
    }
    t.len -= 1;
}

/// Truncates a live thread to `new_len`, releasing the dropped frames.
fn truncThread(side: *Side, t: *Thread, new_len: usize) void {
    while (t.len > new_len) popFrame(side, t);
}

fn retainThread(side: *Side, t: *const Thread) void {
    for (t.frames[0..t.len]) |*f| {
        switch (f.*) {
            .open_obj => |*of| {
                side.retain(of.seen);
                side.retain(of.key);
                side.retain(of.ann);
            },
            .repeat => |*rf| {
                side.retain(rf.tap);
                side.retain(rf.seen);
            },
            else => {},
        }
    }
}

/// Takes a chunk reference for every handle in the state (a state copy that
/// keeps the source alive must retain; a move must not).
pub fn retainState(side: *Side, st: *const State) void {
    for (st.threads[0..st.n]) |*t| retainThread(side, t);
}

fn copyThread(dst: *Thread, src: *const Thread) void {
    dst.len = src.len;
    dst.tap_count = src.tap_count;
    @memcpy(dst.frames[0..src.len], src.frames[0..src.len]);
}

/// A copied thread shares the source's chunk handles; the copy takes its
/// own references. Used when the source keeps living (thread spawn); when
/// the source is dropped or overwritten instead (a move), no retain happens
/// and the source slot must be discarded without releasing.
fn copyThreadRetain(side: *Side, dst: *Thread, src: *const Thread) void {
    copyThread(dst, src);
    retainThread(side, dst);
}

fn newFrame(t: *Thread) error{ResourceLimit}!*Frame {
    if (t.len >= MAX_DEPTH_CAP) return error.ResourceLimit;
    const f = &t.frames[t.len];
    @memset(std.mem.asBytes(f), 0);
    t.len += 1;
    return f;
}

fn spawnThread(side: *Side, st: *State, src_idx: usize) error{ResourceLimit}!usize {
    if (st.n >= st.max_threads) return error.ResourceLimit;
    const idx = st.n;
    copyThreadRetain(side, &st.threads[idx], &st.threads[src_idx]);
    st.n += 1;
    return idx;
}

// ---- lazy choice dispatch --------------------------------------------------

pub const ALT_ANALYSIS_DEPTH: u32 = 16;

/// Conservative ε-test (a superset of "the language of `id` contains the
/// empty word"): true whenever the analysis cannot rule out an empty
/// completion. Such choice alternatives must be spawned eagerly (they can
/// complete without consuming the dispatch byte), so a lazy choice frame
/// never carries one. Comb nodes are not analyzed (true).
pub fn choiceAltMaybeEmpty(g: *const grammar.Grammar, id: grammar.NodeId, depth: u32) bool {
    if (depth == 0) return true;
    return switch (g.node(id).*) {
        .literal => |lit| lit.len == 0,
        .lit_trie => |lt| lt.nodes[0].terminal,
        // Container and scalar machines all consume at least one byte.
        .str, .str_excl, .str_pat, .int_v, .num_v, .int_num, .not_int_num, .num_const, .num_excl, .num_range, .num_mult, .object, .open_obj, .repeat => false,
        .seq => |children| blk: {
            for (children) |c| {
                if (!choiceAltMaybeEmpty(g, c, depth - 1)) break :blk false;
            }
            break :blk true;
        },
        .choice => |alts| blk: {
            for (alts) |c| {
                if (choiceAltMaybeEmpty(g, c, depth - 1)) break :blk true;
            }
            break :blk false;
        },
        .comb => true,
    };
}

/// Conservative first-byte test (a superset of "some word of `id`'s
/// language begins with b"): a false positive only wastes a spawned thread
/// that dies on the byte; a false negative would drop a viable alternative,
/// so unanalyzed shapes (comb, over-depth) answer true.
fn choiceAltMayStart(g: *const grammar.Grammar, id: grammar.NodeId, b: u8, depth: u32) bool {
    if (depth == 0) return true;
    return switch (g.node(id).*) {
        .literal => |lit| lit.len == 0 or g.literalBytes(lit)[0] == b,
        .lit_trie => |lt| lt.nodes[0].terminal or lt.child(0, b) != null,
        .str, .str_excl, .str_pat => b == '"',
        .int_v, .num_v, .int_num, .not_int_num, .num_const, .num_excl, .num_range, .num_mult => b == '-' or (b >= '0' and b <= '9'),
        .object, .open_obj => b == '{',
        .repeat => b == '[',
        .seq => |children| blk: {
            for (children) |c| {
                if (choiceAltMayStart(g, c, b, depth - 1)) break :blk true;
                if (!choiceAltMaybeEmpty(g, c, depth - 1)) break :blk false;
            }
            break :blk true; // every child maybe-empty: the seq itself is nullable
        },
        .choice => |alts| blk: {
            for (alts) |c| {
                if (choiceAltMayStart(g, c, b, depth - 1)) break :blk true;
            }
            break :blk false;
        },
        .comb => true,
    };
}

fn pushNode(g: *const grammar.Grammar, side: *Side, st: *State, t_idx: usize, node_id: grammar.NodeId) Error!void {
    const t = &st.threads[t_idx];
    switch (g.node(node_id).*) {
        .literal => |lit| {
            if (lit.len == 0) return afterChild(g, side, st, t_idx);
            const f = try newFrame(t);
            f.* = .{ .literal = .{ .node = node_id, .off = 0 } };
        },
        .lit_trie => |lt| {
            const f = try newFrame(t);
            f.* = .{ .lit_trie = .{ .gnode = node_id, .node = 0 } };
            if (lt.nodes[0].terminal) {
                // An empty alternative of the set completes right away: a
                // forked copy pops the frame and continues below, while the
                // original thread stays for the longer alternatives.
                const ni = try spawnThread(side, st, t_idx);
                popFrame(side, &st.threads[ni]);
                try afterChild(g, side, st, ni);
            }
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
        .int_num => {
            const f = try newFrame(t);
            f.* = .{ .int_num = undefined };
            f.int_num.node = node_id;
            f.int_num.tz = 0;
            f.int_num.frac_len = 0;
            f.int_num.exp = 0;
            f.int_num.st = .start;
            f.int_num.nz = false;
            f.int_num.exp_neg = false;
        },
        .num_const => {
            const f = try newFrame(t);
            f.* = .{ .num_const = undefined };
            f.num_const.node = node_id;
            f.num_const.j = 0;
            f.num_const.frac_len = 0;
            f.num_const.tail_z = 0;
            f.num_const.exp = 0;
            f.num_const.st = .start;
            f.num_const.m = .leading;
            f.num_const.exp_neg = false;
            f.num_const.neg = false;
        },
        .comb => |cb| {
            // Absent (COMB_NONE) and empty-language branches are never
            // spawned. A group with no reachable verdict is born doomed and
            // dies here, before any spawn, so the rejection is mask-visible
            // at the byte that activated it.
            var live: u64 = 0;
            var first: ?u8 = null;
            for (cb.branches, 0..) |br, i| {
                if (br == grammar.COMB_NONE) continue;
                if (isEmptyLangNode(g, br)) continue;
                live |= @as(u64, 1) << @intCast(i);
                if (first == null) first = @intCast(i);
            }
            if (!combBornOk(&cb, live)) return error.Parse;
            const inst = side.next_inst;
            side.next_inst +%= 1;
            const f = try newFrame(t);
            f.* = .{ .comb = .{ .node = node_id, .inst = inst, .branch = first.? } };
            // Sibling threads copy the comb frame (same inst, own branch);
            // the primary thread keeps the first live branch.
            for (cb.branches, 0..) |br, i| {
                if (br == grammar.COMB_NONE or isEmptyLangNode(g, br)) continue;
                if (i == first.?) continue;
                const ni = try spawnThread(side, st, t_idx);
                st.threads[ni].frames[t.len - 1].comb.branch = @intCast(i);
                try pushNode(g, side, st, ni, br);
            }
            try pushNode(g, side, st, t_idx, cb.branches[first.?]);
        },
        .not_int_num => {
            const f = try newFrame(t);
            f.* = .{ .not_int_num = undefined };
            f.not_int_num.node = node_id;
            f.not_int_num.tz = 0;
            f.not_int_num.frac_len = 0;
            f.not_int_num.exp = 0;
            f.not_int_num.st = .start;
            f.not_int_num.nz = false;
            f.not_int_num.exp_neg = false;
        },
        .str_excl => {
            const f = try newFrame(t);
            f.* = .{ .str_excl = .{ .node = node_id, .count = 0, .trie = 0, .state = .open, .rem = 0, .lo = 0, .hi = 0, .trie_dead = false } };
        },
        .str_pat => |sp| {
            const f = try newFrame(t);
            f.* = .{ .str_pat = .{ .node = node_id, .count = 0, .dfa = sp.dfa.start(), .cp = 0, .state = .open, .rem = 0, .lo = 0, .hi = 0 } };
        },
        .num_excl => {
            const f = try newFrame(t);
            f.* = .{ .num_excl = undefined };
            f.num_excl.node = node_id;
            f.num_excl.j = .{ 0, 0 };
            f.num_excl.tail_z = .{ 0, 0 };
            f.num_excl.frac_len = 0;
            f.num_excl.exp = 0;
            f.num_excl.st = .start;
            f.num_excl.m = 0;
            f.num_excl.exp_neg = false;
            f.num_excl.neg = false;
        },
        .num_range => {
            const f = try newFrame(t);
            f.* = .{ .num_range = .{ .node = node_id, .int_len = 0, .fz = 0, .exp = 0, .j = .{ 0, 0 }, .st = .start, .cmp = 0, .flags = 0 } };
        },
        .num_mult => {
            const f = try newFrame(t);
            f.* = .{ .num_mult = .{ .node = node_id, .rem = 0, .rem_d = 0, .frac_len = 0, .tz = 0, .exp = 0, .st = .start, .flags = 0 } };
        },
        .seq => |children| {
            if (children.len == 0) return afterChild(g, side, st, t_idx);
            const f = try newFrame(t);
            f.* = .{ .seq = .{ .node = node_id, .idx = 0 } };
            try pushNode(g, side, st, t_idx, children[0]);
        },
        .choice => |alts| {
            if (alts.len == 0) return error.ResourceLimit;
            // Lazy alternation: alternatives that cannot complete empty are
            // not spawned now; a choice frame defers them to the first byte
            // (see the Frame.choice comment). This keeps wide value choices
            // (anyJSON towers, typeless schemas) from multiplying threads
            // against MAX_THREADS_CAP before a single byte is seen.
            var any_lazy = false;
            for (alts) |alt| {
                if (!choiceAltMaybeEmpty(g, alt, ALT_ANALYSIS_DEPTH)) {
                    any_lazy = true;
                    break;
                }
            }
            if (!any_lazy) {
                var i: usize = 1;
                while (i < alts.len) : (i += 1) {
                    const ni = try spawnThread(side, st, t_idx);
                    try pushNode(g, side, st, ni, alts[i]);
                }
                try pushNode(g, side, st, t_idx, alts[0]);
                return;
            }
            // Nullable alternatives complete without a dispatch byte, so
            // they are forked eagerly here.
            for (alts) |alt| {
                if (!choiceAltMaybeEmpty(g, alt, ALT_ANALYSIS_DEPTH)) continue;
                const ni = try spawnThread(side, st, t_idx);
                try pushNode(g, side, st, ni, alt);
            }
            const f = try newFrame(t);
            f.* = .{ .choice = .{ .node = node_id } };
        },
        .repeat => {
            const f = try newFrame(t);
            f.* = .{ .repeat = undefined };
            f.repeat.node = node_id;
            f.repeat.count = 0;
            f.repeat.phase = .open;
            f.repeat.ann = 0;
            f.repeat.matched = 0;
            f.repeat.tap = 0;
            f.repeat.seen = 0;
        },
        .object => {
            const f = try newFrame(t);
            f.* = .{ .object = undefined };
            f.object.node = node_id;
            f.object.idx = 0;
            f.object.cur = 0;
            f.object.phase = .open;
        },
        .open_obj => {
            const f = try newFrame(t);
            f.* = .{ .open_obj = .{
                .node = node_id,
                .next_idx = 0,
                .seen = 0,
                .key = 0,
                .ann = 0,
                .phase = .open,
                .u = .{ .key = .{ .st = .open, .rem = 0, .lo = 0, .hi = 0 } },
            } };
        },
    }
}

fn afterChild(g: *const grammar.Grammar, side: *Side, st: *State, t_idx: usize) Error!void {
    const t = &st.threads[t_idx];
    while (t.len > 0) {
        const f = &t.frames[t.len - 1];
        switch (f.*) {
            .seq => {
                const children = g.node(f.seq.node).seq;
                f.seq.idx += 1;
                if (f.seq.idx >= children.len) {
                    popFrame(side, t);
                    continue;
                }
                try pushNode(g, side, st, t_idx, children[f.seq.idx]);
                return;
            },
            .repeat => {
                try repeatElementDone(g, side, t, f);
                return;
            },
            .object => {
                if (f.object.phase == .value) {
                    f.object.phase = .sep;
                    f.object.idx = f.object.cur + 1;
                }
                return;
            },
            .open_obj => {
                if (f.open_obj.phase == .value) f.open_obj.phase = .sep;
                return;
            },
            else => return,
        }
    }
}

fn stepSpawned(g: *const grammar.Grammar, side: *Side, st: *State, dead: *[MAX_THREADS_CAP]bool, from: usize, b: u8) Error!void {
    var j = from;
    while (j < st.n) {
        const before = st.n;
        stepThread(g, side, st, dead, j, b) catch |err| switch (err) {
            error.Parse => dead[j] = true,
            else => |e| return e,
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

fn stepThread(g: *const grammar.Grammar, side: *Side, st: *State, dead: *[MAX_THREADS_CAP]bool, t_idx: usize, b: u8) Error!void {
    const t = &st.threads[t_idx];
    retry: while (true) {
        if (t.len == 0) return error.Parse;
        const f = &t.frames[t.len - 1];
        switch (f.*) {
            .literal => {
                const lit = g.node(f.literal.node).literal;
                const bytes = g.literalBytes(lit);
                if (b != bytes[f.literal.off]) return error.Parse;
                try tapAppend(g, side, t, b);
                f.literal.off += 1;
                if (f.literal.off == lit.len) {
                    popFrame(side, t);
                    try afterChild(g, side, st, t_idx);
                }
                return;
            },
            // The byte is always consumed by the literal set (walked through
            // the shared-prefix trie); completion of an alternative either
            // happens in place (last alternative on this path), via a fork
            // (shorter alternative while longer ones remain), or lazily on
            // the next byte (terminal node, byte not in the trie).
            .lit_trie => {
                const tr = g.node(f.lit_trie.gnode).lit_trie;
                const cur = f.lit_trie.node;
                if (tr.child(cur, b)) |ci| {
                    try tapAppend(g, side, t, b);
                    f.lit_trie.node = ci;
                    const nd = tr.nodes[ci];
                    if (nd.terminal) {
                        if (nd.edge_len == 0) {
                            popFrame(side, t);
                            try afterChild(g, side, st, t_idx);
                        } else {
                            const ni = try spawnThread(side, st, t_idx);
                            popFrame(side, &st.threads[ni]);
                            try afterChild(g, side, st, ni);
                        }
                    }
                    return;
                }
                if (tr.nodes[cur].terminal) {
                    popFrame(side, t);
                    const n0 = st.n;
                    try afterChild(g, side, st, t_idx);
                    try stepSpawned(g, side, st, dead, n0, b);
                    continue :retry;
                }
                return error.Parse;
            },
            .str => {
                switch (strFeed(g, &f.str, b)) {
                    .err => return error.Parse,
                    .consumed => {
                        try tapAppend(g, side, t, b);
                        return;
                    },
                    .done => {
                        try tapAppend(g, side, t, b);
                        popFrame(side, t);
                        try afterChild(g, side, st, t_idx);
                        return;
                    },
                }
            },
            .int_v => {
                switch (numFeed(&f.int_v, b, true)) {
                    .consumed => {
                        try tapAppend(g, side, t, b);
                        return;
                    },
                    .err => return error.Parse,
                    .reject => unreachable,
                    .complete_pop => {
                        popFrame(side, t);
                        const n0 = st.n;
                        try afterChild(g, side, st, t_idx);
                        try stepSpawned(g, side, st, dead, n0, b);
                        continue :retry;
                    },
                }
            },
            .num_v => {
                switch (numFeed(&f.num_v, b, false)) {
                    .consumed => {
                        try tapAppend(g, side, t, b);
                        return;
                    },
                    .err => return error.Parse,
                    .reject => unreachable,
                    .complete_pop => {
                        popFrame(side, t);
                        const n0 = st.n;
                        try afterChild(g, side, st, t_idx);
                        try stepSpawned(g, side, st, dead, n0, b);
                        continue :retry;
                    },
                }
            },
            .int_num => {
                switch (intNumFeed(&f.int_num, b)) {
                    .consumed => {
                        try tapAppend(g, side, t, b);
                        return;
                    },
                    .err => return error.Parse,
                    // A value-check failure at a confirmed boundary: the
                    // branch refuses a complete value (P3 comb groups).
                    .reject => {
                        try combBoundaryFail(g, side, st, dead, t_idx, b);
                        continue :retry;
                    },
                    .complete_pop => {
                        popFrame(side, t);
                        const n0 = st.n;
                        try afterChild(g, side, st, t_idx);
                        try stepSpawned(g, side, st, dead, n0, b);
                        continue :retry;
                    },
                }
            },
            .num_const => {
                switch (numConstFeed(g, &f.num_const, b)) {
                    .consumed => {
                        try tapAppend(g, side, t, b);
                        return;
                    },
                    .err => return error.Parse,
                    .reject => {
                        try combBoundaryFail(g, side, st, dead, t_idx, b);
                        continue :retry;
                    },
                    .complete_pop => {
                        popFrame(side, t);
                        const n0 = st.n;
                        try afterChild(g, side, st, t_idx);
                        try stepSpawned(g, side, st, dead, n0, b);
                        continue :retry;
                    },
                }
            },
            .not_int_num => {
                switch (notIntNumFeed(&f.not_int_num, b)) {
                    .consumed => {
                        try tapAppend(g, side, t, b);
                        return;
                    },
                    .err => return error.Parse,
                    .reject => {
                        try combBoundaryFail(g, side, st, dead, t_idx, b);
                        continue :retry;
                    },
                    .complete_pop => {
                        popFrame(side, t);
                        const n0 = st.n;
                        try afterChild(g, side, st, t_idx);
                        try stepSpawned(g, side, st, dead, n0, b);
                        continue :retry;
                    },
                }
            },
            .num_excl => {
                switch (numExclFeed(g, &f.num_excl, b)) {
                    .consumed => {
                        try tapAppend(g, side, t, b);
                        return;
                    },
                    .err => return error.Parse,
                    .reject => {
                        try combBoundaryFail(g, side, st, dead, t_idx, b);
                        continue :retry;
                    },
                    .complete_pop => {
                        popFrame(side, t);
                        const n0 = st.n;
                        try afterChild(g, side, st, t_idx);
                        try stepSpawned(g, side, st, dead, n0, b);
                        continue :retry;
                    },
                }
            },
            .num_range => {
                switch (numRangeFeed(g, &f.num_range, b)) {
                    .consumed => {
                        try tapAppend(g, side, t, b);
                        return;
                    },
                    .err => return error.Parse,
                    // A bound violation at a confirmed boundary: the value
                    // is complete and refused (P3 comb groups below).
                    .reject => {
                        try combBoundaryFail(g, side, st, dead, t_idx, b);
                        continue :retry;
                    },
                    .complete_pop => {
                        popFrame(side, t);
                        const n0 = st.n;
                        try afterChild(g, side, st, t_idx);
                        try stepSpawned(g, side, st, dead, n0, b);
                        continue :retry;
                    },
                }
            },
            .num_mult => {
                switch (numMultFeed(g, &f.num_mult, b)) {
                    .consumed => {
                        try tapAppend(g, side, t, b);
                        return;
                    },
                    .err => return error.Parse,
                    // Not a multiple at a confirmed boundary: final refusal.
                    .reject => {
                        try combBoundaryFail(g, side, st, dead, t_idx, b);
                        continue :retry;
                    },
                    .complete_pop => {
                        popFrame(side, t);
                        const n0 = st.n;
                        try afterChild(g, side, st, t_idx);
                        try stepSpawned(g, side, st, dead, n0, b);
                        continue :retry;
                    },
                }
            },
            .str_excl => {
                switch (strExclFeed(g, &f.str_excl, b)) {
                    .err => return error.Parse,
                    // A forbidden value (or too few chars) at the closing
                    // quote: a confirmed boundary rejection.
                    .reject => {
                        try combBoundaryFail(g, side, st, dead, t_idx, b);
                        continue :retry;
                    },
                    .consumed => {
                        try tapAppend(g, side, t, b);
                        return;
                    },
                    .done => {
                        try tapAppend(g, side, t, b);
                        popFrame(side, t);
                        try afterChild(g, side, st, t_idx);
                        return;
                    },
                }
            },
            .str_pat => {
                switch (strPatFeed(g, &f.str_pat, b)) {
                    .err => return error.Parse,
                    // The string completed well-formed but the pattern (or
                    // minLength) refuses it: a confirmed boundary rejection.
                    .reject => {
                        try combBoundaryFail(g, side, st, dead, t_idx, b);
                        continue :retry;
                    },
                    .consumed => {
                        try tapAppend(g, side, t, b);
                        return;
                    },
                    .done => {
                        try tapAppend(g, side, t, b);
                        popFrame(side, t);
                        try afterChild(g, side, st, t_idx);
                        return;
                    },
                }
            },
            // A comb frame on top means its branch parse completed on a
            // previous byte; b is the first byte past the branch's value.
            // The group verdict is evaluated over the accepting branches of
            // the instance (ADR-0005 D2), then b is handed to the parent
            // context like any container boundary byte.
            .comb => {
                const cb = &g.node(f.comb.node).comb;
                if (!combRuleOk(cb, combGroupMask(g, st, f.comb.inst))) return error.Parse;
                popFrame(side, t);
                const n0 = st.n;
                try afterChild(g, side, st, t_idx);
                try stepSpawned(g, side, st, dead, n0, b);
                continue :retry;
            },
            .repeat => {
                const rep = g.node(f.repeat.node).repeat;
                switch (f.repeat.phase) {
                    .open => {
                        if (b != '[') return error.Parse;
                        try tapAppend(g, side, t, b);
                        f.repeat.phase = .body;
                        return;
                    },
                    .body => {
                        if (f.repeat.count >= rep.max) {
                            if (b != ']') return error.Parse;
                            try tapAppend(g, side, t, b);
                            if (!repeatCanClose(f, &rep)) return error.Parse;
                            popFrame(side, t);
                            try afterChild(g, side, st, t_idx);
                            return;
                        }
                        if (b == ']') {
                            try tapAppend(g, side, t, b);
                            if (!repeatCanClose(f, &rep)) return error.Parse;
                            popFrame(side, t);
                            try afterChild(g, side, st, t_idx);
                            return;
                        }
                        try repeatStartElement(g, side, t, f, &rep);
                        const n0 = st.n;
                        try pushNode(g, side, st, t_idx, repeatItemNode(f, &rep));
                        try stepSpawned(g, side, st, dead, n0, b);
                        continue :retry;
                    },
                    .sep => {
                        if (b == ',') {
                            if (f.repeat.count >= rep.max) return error.Parse;
                            // The next element's language is empty: the
                            // comma could never be completed, so it dies
                            // here (mask-visible) instead of one byte
                            // later inside the element.
                            if (isEmptyLangNode(g, repeatItemNode(f, &rep))) return error.Parse;
                            try tapAppend(g, side, t, b);
                            f.repeat.phase = .body_after_comma;
                            return;
                        }
                        if (b == ']') {
                            try tapAppend(g, side, t, b);
                            if (!repeatCanClose(f, &rep)) return error.Parse;
                            popFrame(side, t);
                            try afterChild(g, side, st, t_idx);
                            return;
                        }
                        return error.Parse;
                    },
                    .body_after_comma => {
                        // After a comma an element is mandatory: ']' is
                        // forbidden (trailing comma is never allowed).
                        if (b == ']') return error.Parse;
                        try repeatStartElement(g, side, t, f, &rep);
                        const n0 = st.n;
                        try pushNode(g, side, st, t_idx, repeatItemNode(f, &rep));
                        try stepSpawned(g, side, st, dead, n0, b);
                        continue :retry;
                    },
                }
            },
            .object => {
                const props = g.node(f.object.node).object.props;
                switch (f.object.phase) {
                    .open => {
                        if (b != '{') return error.Parse;
                        try tapAppend(g, side, t, b);
                        f.object.phase = .key;
                        return;
                    },
                    .key, .key_after_comma => {
                        if (b == '}') {
                            // After a comma a key is mandatory: trailing
                            // comma is always forbidden.
                            if (f.object.phase == .key_after_comma) return error.Parse;
                            if (!noRequiredFrom(props, f.object.idx)) return error.Parse;
                            try tapAppend(g, side, t, b);
                            popFrame(side, t);
                            try afterChild(g, side, st, t_idx);
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
                            const ni = try spawnThread(side, st, t_idx);
                            const nf = &st.threads[ni].frames[t.len - 1];
                            nf.object.cur = first + k;
                            nf.object.idx = 0;
                            nf.object.phase = .key_lit;
                        }
                        f.object.cur = first;
                        f.object.idx = 0;
                        f.object.phase = .key_lit;
                        try stepSpawned(g, side, st, dead, n0, b);
                        continue :retry;
                    },
                    .key_lit => {
                        const kb = g.literalBytes(props[f.object.cur].key);
                        if (b != kb[f.object.idx]) return error.Parse;
                        try tapAppend(g, side, t, b);
                        f.object.idx += 1;
                        if (f.object.idx == kb.len) {
                            f.object.phase = .value;
                            try pushNode(g, side, st, t_idx, props[f.object.cur].value);
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
                            try tapAppend(g, side, t, b);
                            f.object.phase = .key_after_comma;
                            return;
                        }
                        if (b == '}') {
                            if (!noRequiredFrom(props, f.object.idx)) return error.Parse;
                            try tapAppend(g, side, t, b);
                            popFrame(side, t);
                            try afterChild(g, side, st, t_idx);
                            return;
                        }
                        return error.Parse;
                    },
                }
            },
            .open_obj => {
                const on = g.node(f.open_obj.node).open_obj;
                switch (f.open_obj.phase) {
                    .open => {
                        if (b != '{') return error.Parse;
                        if (on.capture) {
                            // Whole-object byte tap for schema-kind
                            // dependencies (ann slot, ADR-0006 D2/D4).
                            const ch = side.create(.obj_buf) catch return error.OutOfMemory;
                            f.open_obj.ann = ch;
                            t.tap_count += 1;
                        }
                        try tapAppend(g, side, t, b);
                        f.open_obj.phase = .key;
                        return;
                    },
                    .key, .key_after_comma => {
                        if (b == '}') {
                            // After a comma a key is mandatory: trailing
                            // comma is always forbidden.
                            if (f.open_obj.phase == .key_after_comma) return error.Parse;
                            try tapAppend(g, side, t, b);
                            try openObjClose(g, side, &f.open_obj, &on);
                            if (f.open_obj.ann != 0) {
                                side.release(f.open_obj.ann);
                                f.open_obj.ann = 0;
                                t.tap_count -= 1;
                            }
                            popFrame(side, t);
                            try afterChild(g, side, st, t_idx);
                            return;
                        }
                        if (b != '"') return error.Parse;
                        try tapAppend(g, side, t, b);
                        // Begin key capture: the chunk holds the raw key
                        // bytes (the canonical escape table makes raw bytes
                        // unique per decoded string).
                        side.release(f.open_obj.key); // previous key, kept through its value
                        const kh = side.create(.key_buf) catch return error.OutOfMemory;
                        f.open_obj.key = kh;
                        f.open_obj.phase = .key_str;
                        f.open_obj.u.key = .{ .st = .normal, .rem = 0, .lo = 0, .hi = 0 };
                        return;
                    },
                    .key_str => {
                        switch (try openKeyFeed(side, &f.open_obj, b)) {
                            .err => return error.Parse,
                            .consumed => {
                                try tapAppend(g, side, t, b);
                                return;
                            },
                            .done => {
                                try tapAppend(g, side, t, b);
                                const value_node = try openObjDispatch(g, side, &f.open_obj, &on);
                                // The key chunk stays live through
                                // colon/value/sep: the completion synth
                                // reads the in-flight key's name to merge
                                // dependency-schema value constraints. It
                                // is released when the next key starts or
                                // the frame dies.
                                f.open_obj.u.pending = value_node;
                                f.open_obj.phase = .colon;
                                return;
                            },
                        }
                    },
                    .colon => {
                        if (b != ':') return error.Parse;
                        try tapAppend(g, side, t, b);
                        f.open_obj.phase = .value;
                        try pushNode(g, side, st, t_idx, f.open_obj.u.pending);
                        return;
                    },
                    .value => return error.Parse,
                    .sep => {
                        if (b == ',') {
                            try tapAppend(g, side, t, b);
                            f.open_obj.phase = .key_after_comma;
                            return;
                        }
                        if (b == '}') {
                            try tapAppend(g, side, t, b);
                            try openObjClose(g, side, &f.open_obj, &on);
                            if (f.open_obj.ann != 0) {
                                side.release(f.open_obj.ann);
                                f.open_obj.ann = 0;
                                t.tap_count -= 1;
                            }
                            popFrame(side, t);
                            try afterChild(g, side, st, t_idx);
                            return;
                        }
                        return error.Parse;
                    },
                }
            },
            .seq => return error.Parse,
            // Lazy alternation dispatch: the byte selects the alternatives
            // whose language may begin with it; the rest die with the frame.
            // Nullable alternatives were forked off at push time, so every
            // alternative considered here consumes the byte. The first match
            // continues on this thread, the others fork.
            .choice => {
                const alts = g.node(f.choice.node).choice;
                var first_idx: ?usize = null;
                var matches: usize = 0;
                for (alts, 0..) |alt, i| {
                    if (choiceAltMaybeEmpty(g, alt, ALT_ANALYSIS_DEPTH)) continue;
                    if (!choiceAltMayStart(g, alt, b, ALT_ANALYSIS_DEPTH)) continue;
                    if (first_idx == null) first_idx = i;
                    matches += 1;
                }
                if (matches == 0) return error.Parse;
                popFrame(side, t);
                const n0 = st.n;
                for (alts, 0..) |alt, i| {
                    if (choiceAltMaybeEmpty(g, alt, ALT_ANALYSIS_DEPTH)) continue;
                    if (!choiceAltMayStart(g, alt, b, ALT_ANALYSIS_DEPTH)) continue;
                    if (i == first_idx.?) continue;
                    const ni = try spawnThread(side, st, t_idx);
                    try pushNode(g, side, st, ni, alt);
                }
                try pushNode(g, side, st, t_idx, alts[first_idx.?]);
                try stepSpawned(g, side, st, dead, n0, b);
                continue :retry;
            },
        }
    }
}

// ---- spec-v1 P3: boolean combinators (grammar.Node.comb) -----------------

/// Bit mask of all branch slots (branches are capped at 64).
fn combFullMask(n: usize) u64 {
    if (n >= 64) return std.math.maxInt(u64);
    return (@as(u64, 1) << @intCast(n)) - 1;
}

/// Group-doom check over the set of LIVE branches (bits of `live`): is any
/// verdict still reachable? Acceptance patterns are subsets of the live set
/// (a live branch may still die, a dead one never comes back).
fn combBornOk(cb: *const grammar.Comb, live: u64) bool {
    switch (cb.kind) {
        .oneof => return live != 0,
        .allof => return live == combFullMask(cb.branches.len),
        .ifelse => {
            const i_live = (live & 1) != 0;
            const t_ok = cb.branches[1] == grammar.COMB_NONE or (live & 2) != 0;
            const e_ok = cb.branches[2] == grammar.COMB_NONE or (live & 4) != 0;
            return (i_live and t_ok) or e_ok;
        },
    }
}

/// Final group verdict over the ACCEPTING branches (bits of `mask`) at a
/// confirmed value boundary: oneOf = exactly one, allOf = all,
/// if/then/else = (if /\ then) \/ (~if /\ else) with absent then/else
/// applicators counting as accepted.
fn combRuleOk(cb: *const grammar.Comb, mask: u64) bool {
    switch (cb.kind) {
        .oneof => return @popCount(mask) == 1,
        .allof => return mask == combFullMask(cb.branches.len),
        .ifelse => {
            const i_ok = (mask & 1) != 0;
            const t_ok = cb.branches[1] == grammar.COMB_NONE or (mask & 2) != 0;
            const e_ok = cb.branches[2] == grammar.COMB_NONE or (mask & 4) != 0;
            return (i_ok and t_ok) or (!i_ok and e_ok);
        },
    }
}

/// The residue above a comb frame failed to complete in place. Walk the
/// comb frames below the failure (nearest first): the first group whose
/// verdict still accepts pops through it (the failed branch simply does
/// not vote - combGroupMask computes every vote independently). Used when
/// a branch's rejection is final at a confirmed boundary.
fn combPopThrough(g: *const grammar.Grammar, st: *const State, t: *Thread, limit: usize) bool {
    var d = t.len;
    while (d > limit) {
        d -= 1;
        if (t.frames[d] == .comb) {
            const cb = &g.node(t.frames[d].comb.node).comb;
            if (combRuleOk(cb, combGroupMask(g, st, t.frames[d].comb.inst))) {
                t.len = d;
                return true;
            }
        }
    }
    return false;
}

/// Accepting-branch mask of one comb instance over the live threads: a
/// thread votes for its branch when the frames above its comb frame can
/// complete in place (eager branch completions leave the comb frame on top;
/// lazy number machines complete virtually). Cross-thread votes are safe:
/// an eager branch's value extent is self-delimiting, a lazy branch only
/// completes on a byte that cannot extend its value.
fn combGroupMask(g: *const grammar.Grammar, st: *const State, inst: u32) u64 {
    var mask: u64 = 0;
    for (st.threads[0..st.n]) |*t| {
        var d: usize = 0;
        while (d < t.len) : (d += 1) {
            const f = &t.frames[d];
            if (f.* == .comb and f.comb.inst == inst) {
                if (cascadeComplete(g, st, t, d + 1)) mask |= @as(u64, 1) << @intCast(f.comb.branch);
                break;
            }
        }
    }
    return mask;
}

/// A comb-carrying thread died on this byte. A oneOf group is unaffected
/// (its verdict is data-dependent, decided at the boundary), but an allOf
/// group is doomed the moment any branch is lost, and an if/then/else group
/// is doomed when neither (if /\ then) nor else can still complete. Kill
/// every thread of a doomed group; killing may doom nested groups, so
/// iterate to a fixpoint.
fn combSweep(g: *const grammar.Grammar, st: *State, dead: *[MAX_THREADS_CAP]bool) void {
    var changed = true;
    while (changed) {
        changed = false;
        for (st.threads[0..st.n], 0..) |*t, ti| {
            if (dead[ti]) continue;
            for (t.frames[0..t.len]) |*f| {
                if (f.* != .comb) continue;
                const cb = &g.node(f.comb.node).comb;
                if (cb.kind == .oneof) continue;
                const inst = f.comb.inst;
                var live: u64 = 0;
                for (st.threads[0..st.n], 0..) |*t2, t2i| {
                    if (dead[t2i]) continue;
                    for (t2.frames[0..t2.len]) |*f2| {
                        if (f2.* == .comb and f2.comb.inst == inst) {
                            live |= @as(u64, 1) << @intCast(f2.comb.branch);
                            break;
                        }
                    }
                }
                if (combBornOk(cb, live)) continue;
                for (st.threads[0..st.n], 0..) |*t2, t2i| {
                    if (dead[t2i]) continue;
                    for (t2.frames[0..t2.len]) |*f2| {
                        if (f2.* == .comb and f2.comb.inst == inst) {
                            dead[t2i] = true;
                            changed = true;
                            break;
                        }
                    }
                }
                break;
            }
        }
    }
}

/// A branch machine rejected a complete value at a confirmed boundary
/// byte b (a number-machine value check, a str_excl forbidden match). The
/// branch's rejection is final; resolve the comb groups below (nearest
/// first): the first group whose verdict accepts pops through and b
/// continues in the parent context, exactly as in the .comb step case.
fn combBoundaryFail(g: *const grammar.Grammar, side: *Side, st: *State, dead: *[MAX_THREADS_CAP]bool, t_idx: usize, b: u8) Error!void {
    const t = &st.threads[t_idx];
    var d = t.len - 1;
    while (d > 0) {
        d -= 1;
        if (t.frames[d] == .comb) {
            const cb = &g.node(t.frames[d].comb.node).comb;
            if (!combRuleOk(cb, combGroupMask(g, st, t.frames[d].comb.inst))) continue;
            truncThread(side, t, d);
            const n0 = st.n;
            try afterChild(g, side, st, t_idx);
            try stepSpawned(g, side, st, dead, n0, b);
            return;
        }
    }
    return error.Parse;
}

const OpenKeyResult = enum { consumed, done, err };

fn openKeyAppend(side: *Side, of: *OpenObjFrame, b: u8) error{OutOfMemory}!void {
    const h = of.key;
    const c = side.chunkAt(h);
    if (c.refs == 1) {
        try c.payload.append(side.a, b);
        return;
    }
    // COW: the chunk is shared with a copied state; materialize a private
    // copy before mutating (ADR-0006 D2).
    const nh = try side.create(c.kind);
    errdefer side.release(nh);
    const nc = side.chunkAt(nh);
    try nc.payload.appendSlice(side.a, c.payload.items);
    try nc.payload.append(side.a, b);
    side.release(h);
    of.key = nh;
}

/// String state machine for open-object keys: the canonical-v1 validation
/// (escape table, UTF-8 ranges, no length bound) plus raw-byte capture into
/// the key chunk. Raw bytes are unique per decoded string under this table
/// (escapes exist only where mandatory), so byte equality is key equality.
fn openKeyFeed(side: *Side, of: *OpenObjFrame, b: u8) error{OutOfMemory}!OpenKeyResult {
    switch (of.u.key.st) {
        .open => return .err,
        .normal => {
            if (of.u.key.rem > 0) {
                if (b < of.u.key.lo or b > of.u.key.hi) return .err;
                of.u.key.rem -= 1;
                if (of.u.key.rem > 0) {
                    of.u.key.lo = 0x80;
                    of.u.key.hi = 0xBF;
                }
                try openKeyAppend(side, of, b);
                return .consumed;
            }
            if (b == '"') return .done;
            if (b < 0x20) return .err;
            if (b == '\\') {
                try openKeyAppend(side, of, b);
                of.u.key.st = .escape;
                return .consumed;
            }
            if (b < 0x80) {
                try openKeyAppend(side, of, b);
                return .consumed;
            }
            if (b >= 0xC2 and b <= 0xDF) {
                of.u.key.rem = 1;
                of.u.key.lo = 0x80;
                of.u.key.hi = 0xBF;
            } else if (b == 0xE0) {
                of.u.key.rem = 2;
                of.u.key.lo = 0xA0;
                of.u.key.hi = 0xBF;
            } else if ((b >= 0xE1 and b <= 0xEC) or b == 0xEE or b == 0xEF) {
                of.u.key.rem = 2;
                of.u.key.lo = 0x80;
                of.u.key.hi = 0xBF;
            } else if (b == 0xED) {
                of.u.key.rem = 2;
                of.u.key.lo = 0x80;
                of.u.key.hi = 0x9F;
            } else if (b == 0xF0) {
                of.u.key.rem = 3;
                of.u.key.lo = 0x90;
                of.u.key.hi = 0xBF;
            } else if (b >= 0xF1 and b <= 0xF3) {
                of.u.key.rem = 3;
                of.u.key.lo = 0x80;
                of.u.key.hi = 0xBF;
            } else if (b == 0xF4) {
                of.u.key.rem = 3;
                of.u.key.lo = 0x80;
                of.u.key.hi = 0x8F;
            } else {
                return .err;
            }
            try openKeyAppend(side, of, b);
            return .consumed;
        },
        .escape => {
            switch (b) {
                '"', '\\', 'b', 'f', 'n', 'r', 't' => {
                    try openKeyAppend(side, of, b);
                    of.u.key.st = .normal;
                    return .consumed;
                },
                'u' => {
                    try openKeyAppend(side, of, b);
                    of.u.key.st = .u0;
                    return .consumed;
                },
                else => return .err,
            }
        },
        .u0 => {
            if (b != '0') return .err;
            try openKeyAppend(side, of, b);
            of.u.key.st = .u00;
            return .consumed;
        },
        .u00 => {
            if (b != '0') return .err;
            try openKeyAppend(side, of, b);
            of.u.key.st = .uhex1;
            return .consumed;
        },
        .uhex1 => {
            const v = lowerHexVal(b) orelse return .err;
            try openKeyAppend(side, of, b);
            of.u.key.lo = v;
            of.u.key.st = .uhex2;
            return .consumed;
        },
        .uhex2 => {
            const v = lowerHexVal(b) orelse return .err;
            const code: u8 = of.u.key.lo * 16 + v;
            if (code >= 0x20) return .err;
            if (code == 0x08 or code == 0x09 or code == 0x0A or code == 0x0C or code == 0x0D) return .err;
            try openKeyAppend(side, of, b);
            of.u.key.lo = 0;
            of.u.key.st = .normal;
            return .consumed;
        },
    }
}

// Seen-set payload: [count u32] then entries [len u32][raw key bytes]
// sorted by (len, bytes), so a set's serialization is canonical.
fn seenContains(side: *const Side, h: u32, key: []const u8) bool {
    if (h == 0) return false;
    const items = side.chunkAt(h).payload.items;
    if (items.len < 4) return false;
    const count = std.mem.readInt(u32, items[0..4], .little);
    var off: usize = 4;
    var i: u32 = 0;
    while (i < count) : (i += 1) {
        const len = std.mem.readInt(u32, items[off..][0..4], .little);
        off += 4;
        if (len == key.len and std.mem.eql(u8, items[off .. off + len], key)) return true;
        off += len;
    }
    return false;
}

fn seenAdd(side: *Side, h_ptr: *u32, key: []const u8) error{OutOfMemory}!void {
    const h = h_ptr.*;
    const old: []const u8 = if (h == 0) &.{} else side.chunkAt(h).payload.items;
    const count: u32 = if (old.len < 4) 0 else std.mem.readInt(u32, old[0..4], .little);
    var fresh: std.ArrayListUnmanaged(u8) = .{};
    errdefer fresh.deinit(side.a);
    var cb: [4]u8 = undefined;
    std.mem.writeInt(u32, &cb, count + 1, .little);
    try fresh.appendSlice(side.a, &cb);
    std.mem.writeInt(u32, &cb, @intCast(key.len), .little);
    var off: usize = 4;
    var inserted = false;
    var i: u32 = 0;
    while (i < count) : (i += 1) {
        const len = std.mem.readInt(u32, old[off..][0..4], .little);
        const entry = old[off .. off + 4 + len];
        if (!inserted and (len > key.len or (len == key.len and std.mem.order(u8, entry[4..], key) == .gt))) {
            try fresh.appendSlice(side.a, &cb);
            try fresh.appendSlice(side.a, key);
            inserted = true;
        }
        try fresh.appendSlice(side.a, entry);
        off += 4 + len;
    }
    if (!inserted) {
        try fresh.appendSlice(side.a, &cb);
        try fresh.appendSlice(side.a, key);
    }
    if (h != 0 and side.chunkAt(h).refs == 1) {
        const c = side.chunkAt(h);
        c.payload.deinit(side.a);
        c.payload = fresh;
        return;
    }
    const nh = try side.create(.seen_keys);
    const nc = side.chunkAt(nh);
    nc.payload = fresh;
    if (h != 0) side.release(h);
    h_ptr.* = nh;
}

/// Key completed: match a declared property (schema order enforced by
/// next_idx monotonicity) or fall back to the undeclared-key path with set
/// uniqueness. Returns the value node to parse next.
///
/// P2: ban dependencies reject the key outright; propertyNames constrains
/// undeclared keys; a single patternProperties entry (a literal substring
/// or, since P4, a full regex) reroutes the value schema; an
/// unsatisfiable additionalProperties schema rejects like `false`;
/// track_keys objects (min/maxProperties, dependencies) record every
/// dispatched key and enforce the count cap and the ADR-0005 D4 dependency
/// residual at dispatch time.
fn openObjDispatch(g: *const grammar.Grammar, side: *Side, of: *OpenObjFrame, on: *const grammar.OpenObjNode) Error!grammar.NodeId {
    const kb = side.chunkAt(of.key).payload.items;
    if (on.names_forbidden) return error.Parse; // propertyNames: false
    for (on.deps) |dep| {
        if (dep.kind == .ban and std.mem.eql(u8, g.literalBytes(dep.trigger), kb)) return error.Parse;
    }
    for (on.props, 0..) |p, i| {
        const kl = g.literalBytes(p.key);
        const inner = kl[1 .. kl.len - 2]; // strip the quotes and ':'
        if (!std.mem.eql(u8, inner, kb)) continue;
        if (i < of.next_idx) return error.Parse; // duplicate or out of order
        // Landing on prop i skips props[next_idx..i]; a skipped required
        // prop can never be satisfied, so this thread is a dead end.
        var j: usize = of.next_idx;
        while (j < i) : (j += 1) {
            if (on.props[j].required) return error.Parse;
        }
        if (on.track_keys) {
            if (seenCount(side, of.seen) >= on.max_props) return error.Parse;
            try seenAdd(side, &of.seen, kb);
        }
        of.next_idx = @intCast(i + 1);
        if (on.track_keys) try depsResidual(g, side, of, on);
        return p.value;
    }
    if (seenContains(side, of.seen, kb)) return error.Parse; // duplicate key
    if (on.prop_names) |pn| {
        if (!try keyMatchesSchema(g, side, pn, kb)) return error.Parse;
    }
    var value = on.value;
    if (on.pattern_lit) |pl| {
        // Literal-substring patternProperties: an unanchored literal
        // pattern matches iff the key contains it.
        if (try keyMatchesPattern(g, side, pl, kb)) value = on.pattern_value;
    }
    if (on.pattern_dfa) |pd| {
        // Full-regex patternProperties (P4): DFA search over the decoded key.
        if (try keyMatchesRegex(g, side, pd, kb)) value = on.pattern_value;
    }
    if (isEmptyLangNode(g, value)) return error.Parse; // additionalProperties unsatisfiable == false
    if (on.track_keys) {
        if (seenCount(side, of.seen) >= on.max_props) return error.Parse;
    }
    try seenAdd(side, &of.seen, kb);
    if (on.track_keys) try depsResidual(g, side, of, on);
    return value;
}

fn openObjCanClose(g: *const grammar.Grammar, side: *const Side, of: *const OpenObjFrame, on: *const grammar.OpenObjNode) bool {
    if (!noRequiredFrom(on.props, of.next_idx)) return false;
    for (on.extra_required) |lit| {
        if (!seenContains(side, of.seen, g.literalBytes(lit))) return false;
    }
    return true;
}

// ---- P2: byte taps, value equality, exact subschema matches -------------

/// Number of entries of a seen-keys chunk (0 when there is no chunk). With
/// node.track_keys the set holds every dispatched key, so this is the
/// object's property count for min/maxProperties.
fn seenCount(side: *const Side, h: u32) u32 {
    if (h == 0) return 0;
    const items = side.chunkAt(h).payload.items;
    if (items.len < 4) return 0;
    return std.mem.readInt(u32, items[0..4], .little);
}

/// Append one byte to a chunk, copy-on-write when shared (ADR-0006 D2).
fn chunkAppendByte(side: *Side, h_ptr: *u32, b: u8) error{OutOfMemory}!void {
    const h = h_ptr.*;
    const c = side.chunkAt(h);
    if (c.refs == 1) {
        try c.payload.append(side.a, b);
        return;
    }
    const nh = try side.create(c.kind);
    errdefer side.release(nh);
    const nc = side.chunkAt(nh);
    try nc.payload.appendSlice(side.a, c.payload.items);
    try nc.payload.append(side.a, b);
    side.release(h);
    h_ptr.* = nh;
}

/// Byte-tap echo (ADR-0005 D4 residuals, ADR-0006 D2): called at every
/// point where the thread's top frame CONSUMES byte b. Every armed tap
/// receives the byte: repeat element captures (elem_buf) and open-object
/// whole-object captures (obj_buf in the ann slot). A tap is armed exactly
/// while the bytes of the framed value stream through, so container
/// syntax consumed by the tap-owning frame itself never leaks in (repeat
/// taps are disarmed before ','/']'; object captures intentionally cover
/// the whole object including '{'/'}').
fn tapAppend(g: *const grammar.Grammar, side: *Side, t: *Thread, b: u8) error{OutOfMemory}!void {
    _ = g;
    if (t.tap_count == 0) return;
    for (t.frames[0..t.len]) |*f| {
        switch (f.*) {
            .repeat => |*rf| {
                if (rf.tap != 0) try chunkAppendByte(side, &rf.tap, b);
            },
            .open_obj => |*of| {
                if (of.ann != 0) try chunkAppendByte(side, &of.ann, b);
            },
            else => {},
        }
    }
}

/// True for the empty-language node shape (ADR-0006 D1): a string bound no
/// document satisfies. Undeclared keys dispatched to such a value schema
/// are rejected at dispatch, which keeps the mask exact (additionalProperties
/// whose schema is unsatisfiable behaves as `false`).
fn isEmptyLangNode(g: *const grammar.Grammar, node: grammar.NodeId) bool {
    return switch (g.node(node).*) {
        .str => |sc| sc.min_len > sc.max_len,
        else => false,
    };
}

/// Exact whole-value match: parse `bytes` as a standalone document against
/// the subschema `node` of the same grammar. Used for contains (element
/// match), propertyNames (key match) and dependentSchemas (object match).
/// The bytes always come from a value the main parse already accepted, so
/// a Parse error here only means "not in the subschema's language".
pub fn nodeMatchesBytes(g: *const grammar.Grammar, side: *Side, node: grammar.NodeId, bytes: []const u8) Error!bool {
    var st: State = undefined;
    st.n = 1;
    st.max_threads = MAX_THREADS_CAP;
    st.threads[0].len = 0;
    st.threads[0].tap_count = 0;
    try pushNode(g, side, &st, 0, node);
    defer releaseState(side, &st);
    feedWork(g, side, &st, bytes) catch |err| switch (err) {
        error.Parse => return false,
        else => |e| return e,
    };
    return canEnd(g, &st);
}

/// propertyNames check for an undeclared key: the raw canonical key bytes
/// re-quoted and matched against the compiled propertyNames node.
fn keyMatchesSchema(g: *const grammar.Grammar, side: *Side, node: grammar.NodeId, kb: []const u8) Error!bool {
    var buf: std.ArrayListUnmanaged(u8) = .{};
    defer buf.deinit(side.a);
    try buf.append(side.a, '"');
    try buf.appendSlice(side.a, kb);
    try buf.append(side.a, '"');
    return nodeMatchesBytes(g, side, node, buf.items);
}

/// Literal-substring patternProperties match (spec-v1 P2): the pattern is
/// stored decoded and the match applies to the key's VALUE, so the raw
/// canonical key bytes are unescaped first - otherwise a decoded literal
/// like '\' + 'n' would false-positive on the raw escape \n of a newline.
fn keyMatchesPattern(g: *const grammar.Grammar, side: *Side, pl: grammar.Literal, kb: []const u8) Error!bool {
    const needle = g.literalBytes(pl);
    if (std.mem.indexOfScalar(u8, kb, '\\') == null) {
        return std.mem.indexOf(u8, kb, needle) != null;
    }
    var arena = std.heap.ArenaAllocator.init(side.a);
    defer arena.deinit();
    const aa = arena.allocator();
    var buf: std.ArrayListUnmanaged(u8) = .{};
    try buf.append(aa, '"');
    try buf.appendSlice(aa, kb);
    try buf.append(aa, '"');
    var off: u32 = 0;
    const v = json.parse(aa, buf.items, &off) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        error.ResourceLimit => return error.ResourceLimit,
        error.InvalidSchema => return error.Parse, // unreachable: kb is engine-validated key content
    };
    return std.mem.indexOf(u8, v.v.string, needle) != null;
}

/// Full-regex patternProperties match (spec-v1 P4): the DFA runs in
/// unanchored-search mode over the key's VALUE, so the raw canonical key
/// bytes are unescaped first whenever they carry an escape (same decoding
/// as keyMatchesPattern).
fn keyMatchesRegex(g: *const grammar.Grammar, side: *Side, dfa: *const pattern.Dfa, kb: []const u8) Error!bool {
    _ = g;
    if (std.mem.indexOfScalar(u8, kb, '\\') == null) {
        // No escape: the raw bytes are the decoded key's UTF-8.
        return dfa.matchesUtf8(kb) catch false;
    }
    var arena = std.heap.ArenaAllocator.init(side.a);
    defer arena.deinit();
    const aa = arena.allocator();
    var buf: std.ArrayListUnmanaged(u8) = .{};
    try buf.append(aa, '"');
    try buf.appendSlice(aa, kb);
    try buf.append(aa, '"');
    var off: u32 = 0;
    const v = json.parse(aa, buf.items, &off) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        error.ResourceLimit => return error.ResourceLimit,
        error.InvalidSchema => return error.Parse, // unreachable: kb is engine-validated key content
    };
    return dfa.matchesUtf8(v.v.string) catch false;
}

/// Runtime canonical decimal form of a document number lexeme; the
/// compile-side twin lives in schema.zig (ADR-0006 D6). Lexemes beyond the
/// representability limit (400 significant digits, |exp| <= 400) are a
/// resource limit, per semantics-spec-v1 section 1.
fn canonDecimalRt(a: std.mem.Allocator, lexeme: []const u8) error{ OutOfMemory, ResourceLimit }!grammar.NumConst {
    var i: usize = 0;
    var neg = false;
    if (i < lexeme.len and lexeme[i] == '-') {
        neg = true;
        i += 1;
    }
    const int_start = i;
    while (i < lexeme.len and lexeme[i] >= '0' and lexeme[i] <= '9') i += 1;
    const int_part = lexeme[int_start..i];
    var frac_part: []const u8 = "";
    if (i < lexeme.len and lexeme[i] == '.') {
        i += 1;
        const fs = i;
        while (i < lexeme.len and lexeme[i] >= '0' and lexeme[i] <= '9') i += 1;
        frac_part = lexeme[fs..i];
    }
    var exp_val: i64 = 0;
    if (i < lexeme.len and (lexeme[i] == 'e' or lexeme[i] == 'E')) {
        i += 1;
        var exp_neg = false;
        if (i < lexeme.len and (lexeme[i] == '+' or lexeme[i] == '-')) {
            exp_neg = lexeme[i] == '-';
            i += 1;
        }
        while (i < lexeme.len and lexeme[i] >= '0' and lexeme[i] <= '9') : (i += 1) {
            exp_val = exp_val * 10 + (lexeme[i] - '0');
            if (exp_val > 400) return error.ResourceLimit;
        }
        if (exp_neg) exp_val = -exp_val;
    }
    var all: std.ArrayListUnmanaged(u8) = .{};
    try all.appendSlice(a, int_part);
    try all.appendSlice(a, frac_part);
    var digits: []const u8 = all.items;
    while (digits.len > 0 and digits[0] == '0') digits = digits[1..];
    if (digits.len == 0) return .{ .neg = false, .digits = &.{}, .exp10 = 0 };
    var z: usize = 0;
    while (digits[digits.len - 1 - z] == '0') z += 1;
    digits = digits[0 .. digits.len - z];
    if (digits.len > 400) return error.ResourceLimit;
    return .{
        .neg = neg,
        .digits = digits,
        .exp10 = exp_val - @as(i64, @intCast(frac_part.len)) + @as(i64, @intCast(z)),
    };
}

/// Canonical signature of a JSON value (semantics-spec-v1 section 2):
/// numbers by canonical decimal, objects with keys sorted (key order is
/// insignificant). Byte equality of signatures IS exact structural
/// equality; the stored hash only accelerates candidate lookup (ADR-0006
/// D6: the hash finds candidates, the exact comparison decides).
fn sigAppendValue(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, val: *const json.Value) error{ OutOfMemory, ResourceLimit }!void {
    var b4: [4]u8 = undefined;
    var b8: [8]u8 = undefined;
    switch (val.v) {
        .string => |s| {
            try out.append(a, 'S');
            std.mem.writeInt(u32, &b4, @intCast(s.len), .little);
            try out.appendSlice(a, &b4);
            try out.appendSlice(a, s);
        },
        .number => |lex| {
            const nc = try canonDecimalRt(a, lex);
            try out.append(a, 'N');
            try out.append(a, if (nc.neg) 1 else 0);
            std.mem.writeInt(u32, &b4, @intCast(nc.digits.len), .little);
            try out.appendSlice(a, &b4);
            try out.appendSlice(a, nc.digits);
            std.mem.writeInt(i64, &b8, nc.exp10, .little);
            try out.appendSlice(a, &b8);
        },
        .boolean => |b| try out.append(a, if (b) 'T' else 'F'),
        .null_v => try out.append(a, 'Z'),
        .array => |items| {
            try out.append(a, 'A');
            std.mem.writeInt(u32, &b4, @intCast(items.len), .little);
            try out.appendSlice(a, &b4);
            for (items) |it| try sigAppendValue(out, a, it);
        },
        .object => |pairs| {
            try out.append(a, 'O');
            std.mem.writeInt(u32, &b4, @intCast(pairs.len), .little);
            try out.appendSlice(a, &b4);
            const order = try a.alloc(usize, pairs.len);
            for (order, 0..) |*o, i| o.* = i;
            const Ctx = struct {
                pairs: []const json.Pair,
                fn less(c: @This(), x: usize, y: usize) bool {
                    return std.mem.lessThan(u8, c.pairs[x].key, c.pairs[y].key);
                }
            };
            std.mem.sort(usize, order, Ctx{ .pairs = pairs }, Ctx.less);
            for (order) |oi| {
                std.mem.writeInt(u32, &b4, @intCast(pairs[oi].key.len), .little);
                try out.appendSlice(a, &b4);
                try out.appendSlice(a, pairs[oi].key);
                try sigAppendValue(out, a, pairs[oi].value);
            }
        },
    }
}

// Seen-values payload (uniqueItems): [count u32] then per entry
// [hash u64][len u32][canonical signature bytes], in insertion order.
fn seenValContains(side: *const Side, h: u32, hash: u64, sig: []const u8) bool {
    if (h == 0) return false;
    const items = side.chunkAt(h).payload.items;
    if (items.len < 4) return false;
    const count = std.mem.readInt(u32, items[0..4], .little);
    var off: usize = 4;
    var i: u32 = 0;
    while (i < count) : (i += 1) {
        const eh = std.mem.readInt(u64, items[off..][0..8], .little);
        const len = std.mem.readInt(u32, items[off + 8 ..][0..4], .little);
        off += 12;
        if (eh == hash and len == sig.len and std.mem.eql(u8, items[off .. off + len], sig)) return true;
        off += len;
    }
    return false;
}

fn seenValAdd(side: *Side, h_ptr: *u32, hash: u64, sig: []const u8) error{OutOfMemory}!void {
    const h = h_ptr.*;
    var eb: std.ArrayListUnmanaged(u8) = .{};
    defer eb.deinit(side.a);
    var b8: [8]u8 = undefined;
    var b4: [4]u8 = undefined;
    std.mem.writeInt(u64, &b8, hash, .little);
    try eb.appendSlice(side.a, &b8);
    std.mem.writeInt(u32, &b4, @intCast(sig.len), .little);
    try eb.appendSlice(side.a, &b4);
    try eb.appendSlice(side.a, sig);
    if (h == 0) {
        const nh = try side.create(.seen_values);
        errdefer side.release(nh);
        const nc = side.chunkAt(nh);
        std.mem.writeInt(u32, &b4, 1, .little);
        try nc.payload.appendSlice(side.a, &b4);
        try nc.payload.appendSlice(side.a, eb.items);
        h_ptr.* = nh;
        return;
    }
    const c = side.chunkAt(h);
    if (c.refs == 1) {
        const count = std.mem.readInt(u32, c.payload.items[0..4], .little);
        std.mem.writeInt(u32, &b4, count + 1, .little);
        @memcpy(c.payload.items[0..4], &b4);
        try c.payload.appendSlice(side.a, eb.items);
        return;
    }
    const nh = try side.create(.seen_values);
    errdefer side.release(nh);
    const nc = side.chunkAt(nh);
    const count = std.mem.readInt(u32, c.payload.items[0..4], .little);
    std.mem.writeInt(u32, &b4, count + 1, .little);
    try nc.payload.appendSlice(side.a, &b4);
    try nc.payload.appendSlice(side.a, c.payload.items[4..]);
    try nc.payload.appendSlice(side.a, eb.items);
    side.release(h);
    h_ptr.* = nh;
}

/// uniqueItems for one completed element: canonicalize the captured bytes
/// and add them to the seen set; a duplicate is a hard element rejection
/// (semantics-spec-v1 section 2 equality: [1, 1.0] has a duplicate).
fn uniqNote(side: *Side, seen_ptr: *u32, bytes: []const u8) Error!void {
    var arena = std.heap.ArenaAllocator.init(side.a);
    defer arena.deinit();
    const aa = arena.allocator();
    var off: u32 = 0;
    const v = json.parse(aa, bytes, &off) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        error.ResourceLimit => return error.ResourceLimit,
        error.InvalidSchema => return error.Parse, // unreachable: bytes are engine-validated
    };
    var sig: std.ArrayListUnmanaged(u8) = .{};
    try sigAppendValue(&sig, aa, v);
    const h = grammar.fnv1a64(sig.items);
    if (seenValContains(side, seen_ptr.*, h, sig.items)) return error.Parse;
    try seenValAdd(side, seen_ptr, h, sig.items);
}

/// Element completed (afterChild resume of a repeat frame): run the tap
/// finalize (uniqueItems seen-set update, contains exact match count with
/// the ADR-0005 D4 maxContains residual), then advance the element count.
fn repeatElementDone(g: *const grammar.Grammar, side: *Side, t: *Thread, f: *Frame) Error!void {
    const rep = g.node(f.repeat.node).repeat;
    if (f.repeat.tap != 0) {
        const bytes = side.chunkAt(f.repeat.tap).payload.items;
        if (rep.unique) try uniqNote(side, &f.repeat.seen, bytes);
        if (rep.contains) |cn| {
            if (try nodeMatchesBytes(g, side, cn, bytes)) {
                f.repeat.matched +|= 1;
                // maxContains residual: more matches than allowed - dead now.
                if (f.repeat.matched > rep.max_contains) return error.Parse;
            }
        }
        side.release(f.repeat.tap);
        f.repeat.tap = 0;
        t.tap_count -= 1;
    }
    f.repeat.count += 1;
    f.repeat.phase = .sep;
}

/// Array close (']' consumed): element-count and contains-count bounds.
fn repeatCanClose(f: *const Frame, rep: *const grammar.Repeat) bool {
    if (f.repeat.count < rep.min) return false;
    if (rep.contains != null) {
        if (f.repeat.matched < rep.min_contains) return false;
        // matched > max_contains was already rejected at element completion.
    }
    return true;
}

/// Schema of the next element: tuple prefix by position, then the rest
/// schema (spec-v1 P2 prefixItems / tuple items).
fn repeatItemNode(f: *const Frame, rep: *const grammar.Repeat) grammar.NodeId {
    return if (f.repeat.count < rep.prefix.len) rep.prefix[f.repeat.count] else rep.item;
}

/// Element start (repeat .body / .body_after_comma, byte not consumed
/// yet): the ADR-0005 D4 residuals (remaining slots must cover the
/// minContains deficit; a finite element language minus the seen set must
/// stay non-empty for uniqueItems), then the byte tap is armed so the
/// element's raw bytes land in the elem_buf chunk.
fn repeatStartElement(g: *const grammar.Grammar, side: *Side, t: *Thread, f: *Frame, rep: *const grammar.Repeat) Error!void {
    // An element whose language is empty (a `false` subschema) can never
    // start: kill the thread before the dead frame is pushed.
    if (isEmptyLangNode(g, repeatItemNode(f, rep))) return error.Parse;
    if (rep.contains != null and rep.max != grammar.UNBOUNDED) {
        if (f.repeat.matched + (rep.max - f.repeat.count) < rep.min_contains) return error.Parse;
    }
    if (rep.unique and rep.uniq_finite > 0) {
        if (seenCount(side, f.repeat.seen) >= rep.uniq_finite) return error.Parse;
    }
    if (rep.unique or rep.contains != null) {
        const th = side.create(.elem_buf) catch return error.OutOfMemory;
        f.repeat.tap = th;
        t.tap_count += 1;
    }
}

/// Index of a declared property by its raw (unquoted) key bytes.
fn propIndexOf(g: *const grammar.Grammar, on: *const grammar.OpenObjNode, name: []const u8) ?usize {
    for (on.props, 0..) |p, i| {
        const kl = g.literalBytes(p.key);
        const inner = kl[1 .. kl.len - 2]; // strip the quotes and ':'
        if (std.mem.eql(u8, inner, name)) return i;
    }
    return null;
}

/// ADR-0005 D4 dependencies residual, evaluated after every key dispatch
/// on key-tracking objects: once the trigger was seen, a still-missing
/// required name must remain emittable - declared later in schema order,
/// or undeclared with a non-empty value schema and remaining
/// maxProperties capacity. Otherwise the thread is dead right now.
fn depsResidual(g: *const grammar.Grammar, side: *Side, of: *OpenObjFrame, on: *const grammar.OpenObjNode) Error!void {
    for (on.deps) |dep| {
        if (dep.kind != .required) continue;
        if (!seenContains(side, of.seen, g.literalBytes(dep.trigger))) continue;
        for (dep.names) |nm| {
            const name = g.literalBytes(nm);
            if (seenContains(side, of.seen, name)) continue;
            if (propIndexOf(g, on, name)) |pi| {
                // Declared: emittable only while not skipped by schema order.
                if (pi >= of.next_idx) continue;
                return error.Parse;
            }
            // Undeclared: emittable unless names are forbidden, the value
            // schema is empty, or the property count is exhausted.
            if (on.names_forbidden) return error.Parse;
            var value = on.value;
            if (on.pattern_lit) |pl| {
                if (try keyMatchesPattern(g, side, pl, name)) value = on.pattern_value;
            }
            if (on.pattern_dfa) |pd| {
                if (try keyMatchesRegex(g, side, pd, name)) value = on.pattern_value;
            }
            if (isEmptyLangNode(g, value)) return error.Parse;
            if (seenCount(side, of.seen) >= on.max_props) return error.Parse;
        }
    }
}

/// Object close ('}' consumed): required-key guards plus the P2 checks -
/// minProperties over the tracked key set, required-kind dependencies over
/// the seen set, and schema-kind dependencies by exact re-parse of the
/// captured object bytes (ADR-0005 D4: '}' is admitted only when every
/// triggered dependency is discharged).
fn openObjClose(g: *const grammar.Grammar, side: *Side, of: *OpenObjFrame, on: *const grammar.OpenObjNode) Error!void {
    if (!openObjCanClose(g, side, of, on)) return error.Parse;
    if (on.track_keys) {
        if (seenCount(side, of.seen) < on.min_props) return error.Parse;
        // max_props was enforced incrementally at dispatch.
    }
    for (on.deps) |dep| {
        switch (dep.kind) {
            .ban => {},
            .required => {
                if (!seenContains(side, of.seen, g.literalBytes(dep.trigger))) continue;
                for (dep.names) |nm| {
                    if (!seenContains(side, of.seen, g.literalBytes(nm))) return error.Parse;
                }
            },
            .schema => {
                if (!seenContains(side, of.seen, g.literalBytes(dep.trigger))) continue;
                const bytes = side.chunkAt(of.ann).payload.items;
                if (!try nodeMatchesBytes(g, side, dep.schema, bytes)) return error.Parse;
            },
        }
    }
}

/// `reject` is a value-check failure at a confirmed value boundary (the
/// byte cannot extend the number): the branch refuses a complete, well-
/// formed value. `err` is a malformed byte or a mid-value mismatch.
const NumFeedResult = enum { consumed, complete_pop, reject, err };

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

fn trackExpSat(exp: *u32, b: u8) void {
    exp.* = exp.* *| 10;
    exp.* +|= b - '0';
}

/// Mantissa digit of an int_num frame: track the trailing-zero run of the
/// mantissa (over int and frac parts together) and the frac length.
fn intNumMantissa(nf: *IntNumFrame, b: u8, is_frac: bool) void {
    if (is_frac) nf.frac_len +|= 1;
    if (b == '0') {
        nf.tz +|= 1;
    } else {
        nf.tz = 0;
        nf.nz = true;
    }
}

fn intNumValueOk(nf: *const IntNumFrame) bool {
    if (!nf.nz) return true; // zero is an integer (-0 == 0)
    const e: i64 = if (nf.exp_neg) -@as(i64, nf.exp) else @as(i64, nf.exp);
    return e >= @as(i64, nf.frac_len) - @as(i64, nf.tz);
}

/// Same acceptance syntax as numFeed(is_int=false); completion is gated on
/// the value being an integer. numFeed itself stays canonical-frozen.
fn intNumFeed(nf: *IntNumFrame, b: u8) NumFeedResult {
    return intNumFeedImpl(nf, b, false);
}

/// Complement acceptance of intNumFeed (grammar.Node.not_int_num): the same
/// number automaton; at a value boundary the value must NOT be an integer
/// (an integer value is rejected, the boundary byte unconsumed).
fn notIntNumFeed(nf: *IntNumFrame, b: u8) NumFeedResult {
    return intNumFeedImpl(nf, b, true);
}

fn intNumFeedImpl(nf: *IntNumFrame, b: u8, comptime flip: bool) NumFeedResult {
    switch (nf.st) {
        .start => {
            if (b == '-') {
                nf.st = .minus;
                return .consumed;
            }
            if (b == '0') {
                intNumMantissa(nf, b, false);
                nf.st = .zero_complete;
                return .consumed;
            }
            if (isDigit19(b)) {
                intNumMantissa(nf, b, false);
                nf.st = .int_digits;
                return .consumed;
            }
            return .err;
        },
        .minus => {
            if (b == '0' or isDigit19(b)) {
                intNumMantissa(nf, b, false);
                nf.st = if (b == '0') .zero_complete else .int_digits;
                return .consumed;
            }
            return .err;
        },
        .zero_complete, .int_digits => {
            if (nf.st == .int_digits and isDigit(b)) {
                intNumMantissa(nf, b, false);
                return .consumed;
            }
            if (b == '.') {
                nf.st = .dot;
                return .consumed;
            }
            if (b == 'e' or b == 'E') {
                nf.st = .exp;
                return .consumed;
            }
            return if (intNumValueOk(nf) != flip) .complete_pop else .reject;
        },
        .dot => {
            if (isDigit(b)) {
                intNumMantissa(nf, b, true);
                nf.st = .frac_digits;
                return .consumed;
            }
            return .err;
        },
        .frac_digits => {
            if (isDigit(b)) {
                intNumMantissa(nf, b, true);
                return .consumed;
            }
            if (b == 'e' or b == 'E') {
                nf.st = .exp;
                return .consumed;
            }
            return if (intNumValueOk(nf) != flip) .complete_pop else .reject;
        },
        .exp => {
            if (b == '+' or b == '-') {
                nf.exp_neg = b == '-';
                nf.st = .exp_sign;
                return .consumed;
            }
            if (isDigit(b)) {
                trackExpSat(&nf.exp, b);
                nf.st = .exp_digits;
                return .consumed;
            }
            return .err;
        },
        .exp_sign => {
            if (isDigit(b)) {
                trackExpSat(&nf.exp, b);
                nf.st = .exp_digits;
                return .consumed;
            }
            return .err;
        },
        .exp_digits => {
            if (isDigit(b)) {
                trackExpSat(&nf.exp, b);
                return .consumed;
            }
            return if (intNumValueOk(nf) != flip) .complete_pop else .reject;
        },
    }
}

/// Mantissa digit of a num_const frame, matched against the canonical
/// target digits. A mismatch is a hard error: no later byte can repair it.
fn numConstMantissa(nc: *const grammar.NumConst, nf: *NumConstFrame, b: u8, is_frac: bool) NumFeedResult {
    if (is_frac) nf.frac_len +|= 1;
    switch (nf.m) {
        .leading => {
            if (b == '0') return .consumed;
            // First significant digit: a nonzero digit against a zero
            // constant can never match.
            if (nc.digits.len == 0) return .err;
            if (b != nc.digits[0]) return .err;
            nf.j = 1;
            nf.m = if (nc.digits.len == 1) .tail else .matching;
            return .consumed;
        },
        .matching => {
            if (b != nc.digits[nf.j]) return .err;
            nf.j += 1;
            if (nf.j == nc.digits.len) nf.m = .tail;
            return .consumed;
        },
        .tail => {
            if (b != '0') return .err;
            nf.tail_z +|= 1;
            return .consumed;
        },
    }
}

fn numConstValueOk(nc: *const grammar.NumConst, nf: *const NumConstFrame) bool {
    if (nf.m == .leading) return nc.digits.len == 0; // instance is zero
    if (nf.m != .tail) return false; // significant digits incomplete
    if (nf.neg != nc.neg) return false;
    const e: i64 = if (nf.exp_neg) -@as(i64, nf.exp) else @as(i64, nf.exp);
    return e - @as(i64, nf.frac_len) + @as(i64, nf.tail_z) == nc.exp10;
}

/// Same acceptance syntax as numFeed(is_int=false); the digit stream must
/// spell the constant's value (ADR-0006 D6).
fn numConstFeed(g: *const grammar.Grammar, nf: *NumConstFrame, b: u8) NumFeedResult {
    const nc = &g.node(nf.node).num_const;
    switch (nf.st) {
        .start => {
            if (b == '-') {
                nf.neg = true;
                nf.st = .minus;
                return .consumed;
            }
            if (b == '0') {
                nf.st = .zero_complete;
                return numConstMantissa(nc, nf, b, false);
            }
            if (isDigit19(b)) {
                nf.st = .int_digits;
                return numConstMantissa(nc, nf, b, false);
            }
            return .err;
        },
        .minus => {
            if (b == '0' or isDigit19(b)) {
                nf.st = if (b == '0') .zero_complete else .int_digits;
                return numConstMantissa(nc, nf, b, false);
            }
            return .err;
        },
        .zero_complete, .int_digits => {
            if (nf.st == .int_digits and isDigit(b)) {
                return numConstMantissa(nc, nf, b, false);
            }
            if (b == '.') {
                nf.st = .dot;
                return .consumed;
            }
            if (b == 'e' or b == 'E') {
                nf.st = .exp;
                return .consumed;
            }
            return if (numConstValueOk(nc, nf)) .complete_pop else .reject;
        },
        .dot => {
            if (isDigit(b)) {
                nf.st = .frac_digits;
                return numConstMantissa(nc, nf, b, true);
            }
            return .err;
        },
        .frac_digits => {
            if (isDigit(b)) {
                return numConstMantissa(nc, nf, b, true);
            }
            if (b == 'e' or b == 'E') {
                nf.st = .exp;
                return .consumed;
            }
            return if (numConstValueOk(nc, nf)) .complete_pop else .reject;
        },
        .exp => {
            if (b == '+' or b == '-') {
                nf.exp_neg = b == '-';
                nf.st = .exp_sign;
                return .consumed;
            }
            if (isDigit(b)) {
                trackExpSat(&nf.exp, b);
                nf.st = .exp_digits;
                return .consumed;
            }
            return .err;
        },
        .exp_sign => {
            if (isDigit(b)) {
                trackExpSat(&nf.exp, b);
                nf.st = .exp_digits;
                return .consumed;
            }
            return .err;
        },
        .exp_digits => {
            if (isDigit(b)) {
                trackExpSat(&nf.exp, b);
                return .consumed;
            }
            return if (numConstValueOk(nc, nf)) .complete_pop else .reject;
        },
    }
}

/// Mantissa digit of a num_excl frame, matched against constant `i`. Unlike
/// num_const a mismatch is not a hard error: it only rules that constant
/// out (m = 3); the number itself may still be valid.
fn numExclMantissa(nc: *const grammar.NumConst, nf: *NumExclFrame, i: usize, b: u8) void {
    const shift: u3 = @intCast(2 * i);
    switch ((nf.m >> shift) & 3) {
        0 => { // leading: only mantissa zeros so far
            if (b == '0') return;
            if (nc.digits.len == 0 or b != nc.digits[0]) {
                nf.m |= @as(u8, 3) << shift;
                return;
            }
            nf.j[i] = 1;
            const nm: u8 = if (nc.digits.len == 1) 2 else 1;
            nf.m = (nf.m & ~(@as(u8, 3) << shift)) | (@as(u8, nm) << shift);
        },
        1 => { // matching: consuming significant digits
            if (b != nc.digits[nf.j[i]]) {
                nf.m |= @as(u8, 3) << shift;
                return;
            }
            nf.j[i] += 1;
            if (nf.j[i] == nc.digits.len) nf.m = (nf.m & ~(@as(u8, 3) << shift)) | (@as(u8, 2) << shift);
        },
        2 => { // tail: significant digits exhausted, only '0' may follow
            if (b != '0') {
                nf.m |= @as(u8, 3) << shift;
                return;
            }
            nf.tail_z[i] +|= 1;
        },
        else => {}, // ruled out
    }
}

/// The instance number spells constant `i`: same value equality as
/// numConstValueOk (instance zero matches a zero constant; otherwise sign
/// equal and e - frac_len + tail_z == exp10).
fn numExclConstMatches(nc: *const grammar.NumConst, nf: *const NumExclFrame, i: usize) bool {
    const shift: u3 = @intCast(2 * i);
    const m: u8 = (nf.m >> shift) & 3;
    if (m == 0) return nc.digits.len == 0;
    if (m != 2) return false;
    if (nf.neg != nc.neg) return false;
    const e: i64 = if (nf.exp_neg) -@as(i64, nf.exp) else @as(i64, nf.exp);
    return e - @as(i64, nf.frac_len) + @as(i64, nf.tail_z[i]) == nc.exp10;
}

fn numExclMatchAny(g: *const grammar.Grammar, nf: *const NumExclFrame) bool {
    const ne = &g.node(nf.node).num_excl;
    for (ne.consts, 0..) |*nc, i| {
        if (numExclConstMatches(nc, nf, i)) return true;
    }
    return false;
}

/// Same acceptance syntax as numConstFeed; completion is rejected when the
/// value spells one of the node's forbidden constants (grammar.Node.num_excl,
/// the numeric arm of a `not` over const/enum).
fn numExclFeed(g: *const grammar.Grammar, nf: *NumExclFrame, b: u8) NumFeedResult {
    const ne = &g.node(nf.node).num_excl;
    switch (nf.st) {
        .start => {
            if (b == '-') {
                nf.neg = true;
                nf.st = .minus;
                return .consumed;
            }
            if (b == '0' or isDigit19(b)) {
                nf.st = if (b == '0') .zero_complete else .int_digits;
                for (ne.consts, 0..) |*nc, i| numExclMantissa(nc, nf, i, b);
                return .consumed;
            }
            return .err;
        },
        .minus => {
            if (b == '0' or isDigit19(b)) {
                nf.st = if (b == '0') .zero_complete else .int_digits;
                for (ne.consts, 0..) |*nc, i| numExclMantissa(nc, nf, i, b);
                return .consumed;
            }
            return .err;
        },
        .zero_complete, .int_digits => {
            if (nf.st == .int_digits and isDigit(b)) {
                for (ne.consts, 0..) |*nc, i| numExclMantissa(nc, nf, i, b);
                return .consumed;
            }
            if (b == '.') {
                nf.st = .dot;
                return .consumed;
            }
            if (b == 'e' or b == 'E') {
                nf.st = .exp;
                return .consumed;
            }
            return if (numExclMatchAny(g, nf)) .reject else .complete_pop;
        },
        .dot => {
            if (isDigit(b)) {
                nf.st = .frac_digits;
                nf.frac_len +|= 1;
                for (ne.consts, 0..) |*nc, i| numExclMantissa(nc, nf, i, b);
                return .consumed;
            }
            return .err;
        },
        .frac_digits => {
            if (isDigit(b)) {
                nf.frac_len +|= 1;
                for (ne.consts, 0..) |*nc, i| numExclMantissa(nc, nf, i, b);
                return .consumed;
            }
            if (b == 'e' or b == 'E') {
                nf.st = .exp;
                return .consumed;
            }
            return if (numExclMatchAny(g, nf)) .reject else .complete_pop;
        },
        .exp => {
            if (b == '+' or b == '-') {
                nf.exp_neg = b == '-';
                nf.st = .exp_sign;
                return .consumed;
            }
            if (isDigit(b)) {
                trackExpSat(&nf.exp, b);
                nf.st = .exp_digits;
                return .consumed;
            }
            return .err;
        },
        .exp_sign => {
            if (isDigit(b)) {
                trackExpSat(&nf.exp, b);
                nf.st = .exp_digits;
                return .consumed;
            }
            return .err;
        },
        .exp_digits => {
            if (isDigit(b)) {
                trackExpSat(&nf.exp, b);
                return .consumed;
            }
            return if (numExclMatchAny(g, nf)) .reject else .complete_pop;
        },
    }
}

// ---- spec-v1 P4: bounded numbers and multipleOf -------------------------

// pub: the residual-reachability certification of src/complete.zig (ADR-0005)
// reads the same flag bits.
pub const RANGE_NEG: u8 = 1;
pub const RANGE_EXP_NEG: u8 = 2;
pub const RANGE_LEAD: u8 = 4;
pub const RANGE_OM_INT: u8 = 8;

/// One significant mantissa digit of a num_range frame: first significant
/// digit fixes the order-of-magnitude class; every significant digit is
/// compared against each present bound's digits (aligned at the leading
/// digit, zero-padded past the bound's length).
fn numRangeMantissa(g: *const grammar.Grammar, nf: *NumRangeFrame, b: u8, is_frac: bool) void {
    if (!is_frac) nf.int_len +|= 1;
    if (nf.flags & RANGE_LEAD == 0) {
        if (b == '0') {
            // A frac zero before the first significant digit shifts the
            // order of magnitude down (int part is "0" in this branch).
            if (is_frac) nf.fz +|= 1;
            return;
        }
        nf.flags |= RANGE_LEAD;
        if (!is_frac) nf.flags |= RANGE_OM_INT;
    }
    const nr = &g.node(nf.node).num_range;
    inline for (.{ nr.min, nr.max }, 0..) |mb, i| {
        if (mb) |nc| {
            const shift: u3 = @intCast(2 * i);
            if (((nf.cmp >> shift) & 3) == 0) {
                const bd: u8 = if (nf.j[i] < nc.digits.len) nc.digits[nf.j[i]] else '0';
                if (b < bd) {
                    nf.cmp |= @as(u8, 1) << shift;
                } else if (b > bd) {
                    nf.cmp |= @as(u8, 2) << shift;
                }
            }
            if (nf.j[i] < nc.digits.len) nf.j[i] += 1;
        }
    }
}

/// Order of the instance value against bound `nc` (compared by the instance
/// slot `idx`), from the frame state at a confirmed boundary.
fn numRangeBoundOrder(nf: *const NumRangeFrame, nc: grammar.NumConst, idx: usize) std.math.Order {
    if (nf.flags & RANGE_LEAD == 0) {
        // The instance is zero (sign dropped: -0 == 0).
        if (nc.digits.len == 0) return .eq;
        return if (nc.neg) .gt else .lt;
    }
    const neg = nf.flags & RANGE_NEG != 0;
    if (nc.digits.len == 0) return if (neg) .lt else .gt;
    if (neg != nc.neg) return if (neg) .lt else .gt;
    const e: i64 = if (nf.flags & RANGE_EXP_NEG != 0) -@as(i64, nf.exp) else @as(i64, nf.exp);
    const om_m: i64 = if (nf.flags & RANGE_OM_INT != 0)
        @as(i64, nf.int_len) - 1
    else
        -(@as(i64, nf.fz) + 1);
    const om_v: i64 = om_m + e;
    const om_b: i64 = nc.exp10 + @as(i64, @intCast(nc.digits.len)) - 1;
    var mag = std.math.order(om_v, om_b);
    if (mag == .eq) {
        const shift: u3 = @intCast(2 * idx);
        mag = switch ((nf.cmp >> shift) & 3) {
            1 => .lt,
            2 => .gt,
            // The instance's significant digits are a proper prefix of the
            // bound's: the bound's remaining tail ends in a nonzero digit
            // (canonical form), so the instance is smaller in magnitude.
            else => if (nf.j[idx] < nc.digits.len) .lt else .eq,
        };
    }
    return if (neg) grammar.flipOrder(mag) else mag;
}

fn numRangeValueOk(g: *const grammar.Grammar, nf: *const NumRangeFrame) bool {
    const nr = &g.node(nf.node).num_range;
    if (nr.min) |m| {
        const o = numRangeBoundOrder(nf, m, 0);
        if (o == .lt or (nr.min_excl and o == .eq)) return false;
    }
    if (nr.max) |m| {
        const o = numRangeBoundOrder(nf, m, 1);
        if (o == .gt or (nr.max_excl and o == .eq)) return false;
    }
    return true;
}

fn numRangeBoundary(g: *const grammar.Grammar, nf: *const NumRangeFrame) NumFeedResult {
    return if (numRangeValueOk(g, nf)) .complete_pop else .reject;
}

/// Same acceptance syntax as numConstFeed; the value verdict (grammar
/// .Node.num_range) is exact at the confirmed boundary.
fn numRangeFeed(g: *const grammar.Grammar, nf: *NumRangeFrame, b: u8) NumFeedResult {
    switch (nf.st) {
        .start => {
            if (b == '-') {
                nf.flags |= RANGE_NEG;
                nf.st = .minus;
                return .consumed;
            }
            if (b == '0' or isDigit19(b)) {
                nf.st = if (b == '0') .zero_complete else .int_digits;
                numRangeMantissa(g, nf, b, false);
                return .consumed;
            }
            return .err;
        },
        .minus => {
            if (b == '0' or isDigit19(b)) {
                nf.st = if (b == '0') .zero_complete else .int_digits;
                numRangeMantissa(g, nf, b, false);
                return .consumed;
            }
            return .err;
        },
        .zero_complete, .int_digits => {
            if (nf.st == .int_digits and isDigit(b)) {
                numRangeMantissa(g, nf, b, false);
                return .consumed;
            }
            if (b == '.') {
                nf.st = .dot;
                return .consumed;
            }
            if (b == 'e' or b == 'E') {
                nf.st = .exp;
                return .consumed;
            }
            return numRangeBoundary(g, nf);
        },
        .dot => {
            if (isDigit(b)) {
                nf.st = .frac_digits;
                numRangeMantissa(g, nf, b, true);
                return .consumed;
            }
            return .err;
        },
        .frac_digits => {
            if (isDigit(b)) {
                numRangeMantissa(g, nf, b, true);
                return .consumed;
            }
            if (b == 'e' or b == 'E') {
                nf.st = .exp;
                return .consumed;
            }
            return numRangeBoundary(g, nf);
        },
        .exp => {
            if (b == '+' or b == '-') {
                if (b == '-') nf.flags |= RANGE_EXP_NEG;
                nf.st = .exp_sign;
                return .consumed;
            }
            if (isDigit(b)) {
                trackExpSat(&nf.exp, b);
                nf.st = .exp_digits;
                return .consumed;
            }
            return .err;
        },
        .exp_sign => {
            if (isDigit(b)) {
                trackExpSat(&nf.exp, b);
                nf.st = .exp_digits;
                return .consumed;
            }
            return .err;
        },
        .exp_digits => {
            if (isDigit(b)) {
                trackExpSat(&nf.exp, b);
                return .consumed;
            }
            return numRangeBoundary(g, nf);
        },
    }
}

// pub: src/complete.zig residual certification (see RANGE_* above).
pub const MULT_NZ: u8 = 1;
pub const MULT_EXP_NEG: u8 = 2;
pub const MULT_EXP_SAT: u8 = 4;
/// Exponent saturation cap of the num_mult frame: past it the scaled
/// exponent s is provably >= the divisor's 2/5 valuations for any input
/// within the session byte budget, so divisibility reduces to the
/// 2/5-stripped residue class (see NumMultFrame).
const MULT_EXP_CAP: u32 = 1 << 31;

/// One mantissa digit of a num_mult frame: fold it into the running
/// residue; the residue at the last NONZERO digit is the canonical digit
/// string's residue (trailing zeros stripped), tz counts pending zeros.
fn numMultMantissa(g: *const grammar.Grammar, nf: *NumMultFrame, b: u8, is_frac: bool) void {
    const nm = &g.node(nf.node).num_mult;
    if (is_frac) nf.frac_len +|= 1;
    nf.rem = @intCast((@as(u64, nf.rem) * 10 + (b - '0')) % nm.div);
    if (b != '0') {
        nf.rem_d = nf.rem;
        nf.tz = 0;
        nf.flags |= MULT_NZ;
    } else {
        nf.tz +|= 1;
    }
}

fn numMultExp(nf: *NumMultFrame, b: u8) void {
    const v = @as(u64, nf.exp) * 10 + (b - '0');
    if (v >= MULT_EXP_CAP) {
        nf.exp = MULT_EXP_CAP;
        nf.flags |= MULT_EXP_SAT;
    } else {
        nf.exp = @intCast(v);
    }
}

fn numMultValueOk(g: *const grammar.Grammar, nf: *const NumMultFrame) bool {
    const nm = &g.node(nf.node).num_mult;
    if (nf.flags & MULT_NZ == 0) return true; // zero is a multiple
    if (nf.flags & MULT_EXP_SAT != 0) {
        // |e| >= 2^31: with a positive exponent s is provably >= the
        // divisor's 2/5 valuations (session byte budget), and divisibility
        // is decided by the 2/5-stripped residue class alone; a negative
        // one makes s < 0.
        if (nf.flags & MULT_EXP_NEG != 0) return false;
        return nf.rem % nm.co == 0;
    }
    const e: i64 = if (nf.flags & MULT_EXP_NEG != 0) -@as(i64, nf.exp) else @as(i64, nf.exp);
    const s: i64 = e - @as(i64, nf.frac_len) + @as(i64, nf.tz) - nm.div_exp10;
    if (s < 0) return false;
    const p = grammar.powmodU32(10 % nm.div, @intCast(s), nm.div);
    return (@as(u64, nf.rem_d) * p) % nm.div == 0;
}

fn numMultBoundary(g: *const grammar.Grammar, nf: *const NumMultFrame) NumFeedResult {
    return if (numMultValueOk(g, nf)) .complete_pop else .reject;
}

/// Same acceptance syntax as numConstFeed; the divisibility verdict
/// (grammar.Node.num_mult) is exact at the confirmed boundary.
fn numMultFeed(g: *const grammar.Grammar, nf: *NumMultFrame, b: u8) NumFeedResult {
    switch (nf.st) {
        .start => {
            if (b == '-') {
                nf.st = .minus;
                return .consumed;
            }
            if (b == '0' or isDigit19(b)) {
                nf.st = if (b == '0') .zero_complete else .int_digits;
                numMultMantissa(g, nf, b, false);
                return .consumed;
            }
            return .err;
        },
        .minus => {
            if (b == '0' or isDigit19(b)) {
                nf.st = if (b == '0') .zero_complete else .int_digits;
                numMultMantissa(g, nf, b, false);
                return .consumed;
            }
            return .err;
        },
        .zero_complete, .int_digits => {
            if (nf.st == .int_digits and isDigit(b)) {
                numMultMantissa(g, nf, b, false);
                return .consumed;
            }
            if (b == '.') {
                nf.st = .dot;
                return .consumed;
            }
            if (b == 'e' or b == 'E') {
                nf.st = .exp;
                return .consumed;
            }
            return numMultBoundary(g, nf);
        },
        .dot => {
            if (isDigit(b)) {
                nf.st = .frac_digits;
                numMultMantissa(g, nf, b, true);
                return .consumed;
            }
            return .err;
        },
        .frac_digits => {
            if (isDigit(b)) {
                numMultMantissa(g, nf, b, true);
                return .consumed;
            }
            if (b == 'e' or b == 'E') {
                nf.st = .exp;
                return .consumed;
            }
            return numMultBoundary(g, nf);
        },
        .exp => {
            if (b == '+' or b == '-') {
                if (b == '-') nf.flags |= MULT_EXP_NEG;
                nf.st = .exp_sign;
                return .consumed;
            }
            if (isDigit(b)) {
                numMultExp(nf, b);
                nf.st = .exp_digits;
                return .consumed;
            }
            return .err;
        },
        .exp_sign => {
            if (isDigit(b)) {
                numMultExp(nf, b);
                nf.st = .exp_digits;
                return .consumed;
            }
            return .err;
        },
        .exp_digits => {
            if (isDigit(b)) {
                numMultExp(nf, b);
                return .consumed;
            }
            return numMultBoundary(g, nf);
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

/// Walk the forbidden-literal trie of a str_excl frame by one raw byte.
/// Under the canonical escape table raw bytes are unique per decoded
/// string, so a byte-wise trie match is an exact forbidden-value match.
fn strExclTrieStep(tr: *const grammar.LitTrie, sf: *StrExclFrame, b: u8) void {
    if (sf.trie_dead) return;
    if (tr.child(sf.trie, b)) |ci| {
        sf.trie = ci;
    } else {
        sf.trie_dead = true;
    }
}

/// str_excl adds `reject` to the str result: the string completed
/// well-formed but is refused (a forbidden trie value, or too few
/// characters) - a confirmed boundary rejection for comb groups, unlike
/// `err` (malformed bytes, mid-value bound overflow).
const StrExclResult = enum { consumed, done, reject, err };

/// The str machine of strFeed (bounds from the str_excl node) plus the
/// forbidden-set trie walked on every consumed byte; completing the string
/// at a trie terminal spells a forbidden value and is rejected.
fn strExclFeed(g: *const grammar.Grammar, sf: *StrExclFrame, b: u8) StrExclResult {
    const se = &g.node(sf.node).str_excl;
    const tr = &se.trie;
    switch (sf.state) {
        .open => {
            if (b != '"') return .err;
            strExclTrieStep(tr, sf, b);
            sf.state = .normal;
            return .consumed;
        },
        .normal => {
            if (sf.rem > 0) {
                if (b < sf.lo or b > sf.hi) return .err;
                strExclTrieStep(tr, sf, b);
                sf.rem -= 1;
                if (sf.rem > 0) {
                    sf.lo = 0x80;
                    sf.hi = 0xBF;
                    return .consumed;
                }
                if (sf.count >= se.max_len) return .err;
                sf.count += 1;
                return .consumed;
            }
            if (b == '"') {
                if (sf.count < se.min_len) return .reject;
                strExclTrieStep(tr, sf, b);
                if (!sf.trie_dead and tr.nodes[sf.trie].terminal) return .reject;
                return .done;
            }
            if (b < 0x20) return .err;
            if (sf.count >= se.max_len) return .err;
            strExclTrieStep(tr, sf, b);
            if (b == '\\') {
                sf.state = .escape;
                return .consumed;
            }
            if (b < 0x80) {
                sf.count += 1;
                return .consumed;
            }
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
                    strExclTrieStep(tr, sf, b);
                    sf.state = .normal;
                    if (sf.count >= se.max_len) return .err;
                    sf.count += 1;
                    return .consumed;
                },
                'u' => {
                    strExclTrieStep(tr, sf, b);
                    sf.state = .u0;
                    return .consumed;
                },
                else => return .err,
            }
        },
        .u0 => {
            if (b != '0') return .err;
            strExclTrieStep(tr, sf, b);
            sf.state = .u00;
            return .consumed;
        },
        .u00 => {
            if (b != '0') return .err;
            strExclTrieStep(tr, sf, b);
            sf.state = .uhex1;
            return .consumed;
        },
        .uhex1 => {
            const v = lowerHexVal(b) orelse return .err;
            strExclTrieStep(tr, sf, b);
            sf.lo = v;
            sf.state = .uhex2;
            return .consumed;
        },
        .uhex2 => {
            const v = lowerHexVal(b) orelse return .err;
            const code: u8 = sf.lo * 16 + v;
            if (code >= 0x20) return .err;
            if (code == 0x08 or code == 0x09 or code == 0x0A or code == 0x0C or code == 0x0D) return .err;
            strExclTrieStep(tr, sf, b);
            sf.lo = 0;
            sf.state = .normal;
            if (sf.count >= se.max_len) return .err;
            sf.count += 1;
            return .consumed;
        },
    }
}

/// Feed one decoded codepoint of a str_pat character to the pattern DFA.
/// False means the DFA died: no continuation of the string can match, so
/// the thread is a dead end from this byte on (mask-visible, ADR-0005).
fn strPatDfaFeed(sp: *const grammar.StrPat, sf: *StrPatFrame, cp: u21) bool {
    sf.dfa = sp.dfa.feed(sf.dfa, cp);
    return !sp.dfa.dead(sf.dfa);
}

fn strPatCount(sp: *const grammar.StrPat, sf: *StrPatFrame) StrExclResult {
    if (sf.count >= sp.max_len) return .err;
    sf.count += 1;
    return .consumed;
}

/// The str machine of strFeed (bounds from the str_pat node) plus the
/// pattern DFA fed per decoded codepoint. `reject` has the str_excl
/// meaning: the string completed well-formed but is refused at the closing
/// quote (too few characters, or a non-accepting DFA state) - a confirmed
/// boundary rejection for comb groups.
fn strPatFeed(g: *const grammar.Grammar, sf: *StrPatFrame, b: u8) StrExclResult {
    const sp = &g.node(sf.node).str_pat;
    switch (sf.state) {
        .open => {
            if (b != '"') return .err;
            sf.state = .normal;
            return .consumed;
        },
        .normal => {
            if (sf.rem > 0) {
                if (b < sf.lo or b > sf.hi) return .err;
                sf.cp = (sf.cp << 6) | (b & 0x3F);
                sf.rem -= 1;
                if (sf.rem > 0) {
                    sf.lo = 0x80;
                    sf.hi = 0xBF;
                    return .consumed;
                }
                if (!strPatDfaFeed(sp, sf, @intCast(sf.cp))) return .err;
                return strPatCount(sp, sf);
            }
            if (b == '"') {
                if (sf.count < sp.min_len) return .reject;
                if (!sp.dfa.isAccept(sf.dfa)) return .reject;
                return .done;
            }
            if (b < 0x20) return .err;
            // Start of a new character: at count == max completing it would
            // exceed maxLength, so forbid it immediately (as strFeed).
            if (sf.count >= sp.max_len) return .err;
            if (b == '\\') {
                sf.state = .escape;
                return .consumed;
            }
            if (b < 0x80) {
                if (!strPatDfaFeed(sp, sf, b)) return .err;
                return strPatCount(sp, sf);
            }
            if (b >= 0xC2 and b <= 0xDF) {
                sf.cp = b & 0x1F;
                sf.rem = 1;
                sf.lo = 0x80;
                sf.hi = 0xBF;
                return .consumed;
            }
            if (b == 0xE0) {
                sf.cp = 0;
                sf.rem = 2;
                sf.lo = 0xA0;
                sf.hi = 0xBF;
                return .consumed;
            }
            if ((b >= 0xE1 and b <= 0xEC) or b == 0xEE or b == 0xEF) {
                sf.cp = b & 0x0F;
                sf.rem = 2;
                sf.lo = 0x80;
                sf.hi = 0xBF;
                return .consumed;
            }
            if (b == 0xED) {
                sf.cp = 0x0D;
                sf.rem = 2;
                sf.lo = 0x80;
                sf.hi = 0x9F;
                return .consumed;
            }
            if (b == 0xF0) {
                sf.cp = 0;
                sf.rem = 3;
                sf.lo = 0x90;
                sf.hi = 0xBF;
                return .consumed;
            }
            if (b >= 0xF1 and b <= 0xF3) {
                sf.cp = b & 0x07;
                sf.rem = 3;
                sf.lo = 0x80;
                sf.hi = 0xBF;
                return .consumed;
            }
            if (b == 0xF4) {
                sf.cp = 4;
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
                    const cp: u21 = switch (b) {
                        '"' => 0x22,
                        '\\' => 0x5C,
                        'b' => 0x08,
                        'f' => 0x0C,
                        'n' => 0x0A,
                        'r' => 0x0D,
                        else => 0x09, // 't'
                    };
                    if (!strPatDfaFeed(sp, sf, cp)) return .err;
                    sf.state = .normal;
                    return strPatCount(sp, sf);
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
            if (!strPatDfaFeed(sp, sf, code)) return .err;
            return strPatCount(sp, sf);
        },
    }
}

fn feedWork(g: *const grammar.Grammar, side: *Side, work: *State, bytes: []const u8) Error!void {
    var dead = [_]bool{false} ** MAX_THREADS_CAP;
    for (bytes) |b| {
        const n_start = work.n;
        var j: usize = 0;
        while (j < n_start) : (j += 1) {
            if (dead[j]) continue;
            stepThread(g, side, work, &dead, j, b) catch |err| switch (err) {
                error.Parse => {
                    dead[j] = true;
                },
                else => |e| return e,
            };
        }
        // A dead comb-carrying thread may doom its whole group (allOf,
        // if/then/else); sweep before compaction (spec-v1 P3, ADR-0005 D2).
        var comb_dead = false;
        for (0..work.n) |di| {
            if (!dead[di]) continue;
            for (work.threads[di].frames[0..work.threads[di].len]) |*f| {
                if (f.* == .comb) {
                    comb_dead = true;
                    break;
                }
            }
            if (comb_dead) break;
        }
        if (comb_dead) combSweep(g, work, &dead);
        var w: usize = 0;
        var r: usize = 0;
        while (r < work.n) : (r += 1) {
            if (dead[r]) {
                // A dead thread's chunk references die with it.
                releaseThread(side, &work.threads[r]);
                continue;
            }
            if (w != r) copyThread(&work.threads[w], &work.threads[r]); // move
            dead[w] = false;
            w += 1;
        }
        var k = w;
        while (k < work.n) : (k += 1) dead[k] = false;
        work.n = @intCast(w);
        if (w == 0) return error.Parse;
        dedupThreads(side, work);
    }
}

/// Chunk equality within one owner: identical handles trivially match;
/// different handles still match when a COW split left two chunks over the
/// same bytes (ADR-0006 D2).
fn chunkContentEq(side: *const Side, ha: u32, hb: u32) bool {
    if (ha == hb) return true;
    if (ha == 0 or hb == 0) return false;
    const ca = side.chunkAt(ha);
    const cb = side.chunkAt(hb);
    return ca.kind == cb.kind and std.mem.eql(u8, ca.payload.items, cb.payload.items);
}

/// Frame equality for dedup: raw bytes when they agree, otherwise field
/// values for the chunk-carrying frames (repeat, open_obj) with the chunks
/// compared by CONTENT - two COW copies of one logical chunk hold different
/// handles over equal bytes and must still count as duplicates. Every other
/// tag carries no side data, so a raw-byte mismatch is final.
fn framesEqual(side: *const Side, fa: *const Frame, fb: *const Frame) bool {
    if (std.mem.eql(u8, std.mem.asBytes(fa), std.mem.asBytes(fb))) return true;
    const tag = std.meta.activeTag(fa.*);
    if (tag != std.meta.activeTag(fb.*)) return false;
    switch (tag) {
        .repeat => {
            const ra = &fa.repeat;
            const rb = &fb.repeat;
            if (ra.node != rb.node or ra.count != rb.count or ra.ann != rb.ann or
                ra.matched != rb.matched or ra.phase != rb.phase) return false;
            return chunkContentEq(side, ra.tap, rb.tap) and chunkContentEq(side, ra.seen, rb.seen);
        },
        .open_obj => {
            const oa = &fa.open_obj;
            const ob = &fb.open_obj;
            if (oa.node != ob.node or oa.next_idx != ob.next_idx or oa.phase != ob.phase or
                !std.mem.eql(u8, std.mem.asBytes(&oa.u), std.mem.asBytes(&ob.u))) return false;
            return chunkContentEq(side, oa.seen, ob.seen) and
                chunkContentEq(side, oa.key, ob.key) and
                chunkContentEq(side, oa.ann, ob.ann);
        },
        else => return false,
    }
}

/// Two threads are duplicates when their tap counts agree and every frame
/// pair compares equal: both threads then parse the same prefix with the
/// same continuation and either one can stand for the other.
fn threadsEqual(side: *const Side, ta: *const Thread, tb: *const Thread) bool {
    if (ta.len != tb.len or ta.tap_count != tb.tap_count) return false;
    if (std.mem.eql(u8, std.mem.sliceAsBytes(ta.frames[0..ta.len]), std.mem.sliceAsBytes(tb.frames[0..tb.len]))) return true;
    for (ta.frames[0..ta.len], tb.frames[0..tb.len]) |*fa, *fb| {
        if (!framesEqual(side, fa, fb)) return false;
    }
    return true;
}

/// Drop duplicate threads, keeping the first occurrence (stable).
/// Sibling branches that converged on the same continuation - the canonical
/// case is the AnyJSON int_num/num_v pair completing the same integer-
/// valued element - are redundant parses of the same prefix; left in place
/// they double on every converging value and exhaust the thread budget
/// within a handful of array elements. Comb verdicts are branch masks over
/// the live threads, so removing a duplicate never changes a group outcome.
fn dedupThreads(side: *Side, work: *State) void {
    if (work.n < 2) return;
    var dup = [_]bool{false} ** MAX_THREADS_CAP;
    for (0..work.n) |i| {
        if (dup[i]) continue;
        var j = i + 1;
        while (j < work.n) : (j += 1) {
            if (dup[j]) continue;
            if (threadsEqual(side, &work.threads[i], &work.threads[j])) dup[j] = true;
        }
    }
    var w: usize = 0;
    for (0..work.n) |r| {
        if (dup[r]) {
            // A duplicate's chunk references die with it.
            releaseThread(side, &work.threads[r]);
            continue;
        }
        if (w != r) copyThread(&work.threads[w], &work.threads[r]); // move
        w += 1;
    }
    work.n = @intCast(w);
}

pub fn initState(g: *const grammar.Grammar, max_threads: u16, side: *Side) Error!State {
    if (max_threads == 0 or max_threads > MAX_THREADS_CAP) return error.ResourceLimit;
    var st: State = undefined;
    st.n = 1;
    st.max_threads = max_threads;
    st.threads[0].len = 0;
    st.threads[0].tap_count = 0;
    try pushNode(g, side, &st, 0, g.root);
    return st;
}

/// Feeds `bytes` to every live thread of `in` and collects the survivors
/// into `out` (`out` is fully overwritten). `work` is caller-provided
/// scratch (must alias neither `in` nor `out`), fully overwritten per call.
/// It used to be a `threadlocal` scratch, but a GD-model TLS access calls
/// __tls_get_addr, which is free to clobber argument registers (glibc's
/// implementation clobbers %rdx); the safe-mode codegen kept `in` in %rdx
/// across that call and read a wild pointer afterwards.
///
/// Chunk discipline (spec-v1 open objects): `in` keeps its references. On
/// success `out` owns fresh references (caller releases with releaseState);
/// on any error both `out` and `work` are left released (n == 0), so they
/// may be reused or dropped without another release. `work` is never read
/// on entry, only written.
pub fn feedBytes(g: *const grammar.Grammar, side: *Side, in: *const State, bytes: []const u8, out: *State, work: *State) Error!void {
    out.n = 0;
    out.max_threads = in.max_threads;
    work.max_threads = in.max_threads;
    // All input threads are fed together in one work state: threads of one
    // state are alternative parses of the same prefix and never interact -
    // except comb groups, whose verdict and sweep are evaluated over the
    // LIVE sibling threads of one instance (spec-v1 P3, ADR-0005 D2).
    // Isolating input threads would resolve every group over a single
    // branch (allOf would always fail, oneOf never would).
    for (0..in.n) |ti| {
        copyThread(&work.threads[ti], &in.threads[ti]);
        retainThread(side, &work.threads[ti]);
    }
    work.n = in.n;
    feedWork(g, side, work, bytes) catch |e| {
        releaseState(side, work);
        return e;
    };
    for (0..work.n) |wi| {
        if (out.n >= out.max_threads) {
            releaseState(side, work);
            releaseState(side, out);
            return error.ResourceLimit;
        }
        copyThread(&out.threads[out.n], &work.threads[wi]); // move
        out.n += 1;
    }
    // The survivors moved to `out`; `work` relinquishes them.
    work.n = 0;
    if (out.n == 0) return error.Parse;
}

pub fn canEnd(g: *const grammar.Grammar, st: *const State) bool {
    for (st.threads[0..st.n]) |*t| {
        if (cascadeComplete(g, st, t, 0)) return true;
    }
    return false;
}

/// Can `src` complete in place down to frame depth `limit`? Virtual
/// completion pops frames without consuming bytes: terminal lit_trie nodes,
/// complete number machines (with their value checks), finished seqs, and
/// comb frames whose group verdict holds over the live state (spec-v1 P3).
/// String/object/array frames need more bytes and fail the cascade. A
/// failure that is a FINAL branch rejection at the value boundary (a
/// number-machine value check, an unfinished seq, a refused comb verdict)
/// pops through an accepting comb group below (combPopThrough); a failure
/// that means the document is truncated mid-value does not.
fn cascadeComplete(g: *const grammar.Grammar, st: *const State, src: *const Thread, limit: usize) bool {
    var t: Thread = undefined;
    copyThread(&t, src);
    while (t.len > limit) {
        const f = &t.frames[t.len - 1];
        switch (f.*) {
            .lit_trie => {
                const tr = g.node(f.lit_trie.gnode).lit_trie;
                if (!tr.nodes[f.lit_trie.node].terminal) return false;
                t.len -= 1;
            },
            .int_v => {
                if (!numComplete(f.int_v.st)) return false;
                t.len -= 1;
            },
            .num_v => {
                if (!numComplete(f.num_v.st)) return false;
                t.len -= 1;
            },
            .int_num => {
                if (!numComplete(f.int_num.st)) return false;
                if (!intNumValueOk(&f.int_num)) {
                    if (!combPopThrough(g, st, &t, limit)) return false;
                    continue;
                }
                t.len -= 1;
            },
            .not_int_num => {
                if (!numComplete(f.not_int_num.st)) return false;
                if (intNumValueOk(&f.not_int_num)) {
                    if (!combPopThrough(g, st, &t, limit)) return false;
                    continue;
                }
                t.len -= 1;
            },
            .num_const => {
                if (!numComplete(f.num_const.st)) return false;
                if (!numConstValueOk(&g.node(f.num_const.node).num_const, &f.num_const)) {
                    if (!combPopThrough(g, st, &t, limit)) return false;
                    continue;
                }
                t.len -= 1;
            },
            .num_excl => {
                if (!numComplete(f.num_excl.st)) return false;
                if (numExclMatchAny(g, &f.num_excl)) {
                    if (!combPopThrough(g, st, &t, limit)) return false;
                    continue;
                }
                t.len -= 1;
            },
            .num_range => {
                if (!numComplete(f.num_range.st)) return false;
                if (!numRangeValueOk(g, &f.num_range)) {
                    if (!combPopThrough(g, st, &t, limit)) return false;
                    continue;
                }
                t.len -= 1;
            },
            .num_mult => {
                if (!numComplete(f.num_mult.st)) return false;
                if (!numMultValueOk(g, &f.num_mult)) {
                    if (!combPopThrough(g, st, &t, limit)) return false;
                    continue;
                }
                t.len -= 1;
            },
            .seq => {
                const children = g.node(f.seq.node).seq;
                f.seq.idx += 1;
                if (f.seq.idx >= children.len) {
                    t.len -= 1;
                } else if (!virtPushComplete(g, children[f.seq.idx])) {
                    if (!combPopThrough(g, st, &t, limit)) return false;
                    continue;
                }
            },
            .comb => {
                const cb = &g.node(f.comb.node).comb;
                if (!combRuleOk(cb, combGroupMask(g, st, f.comb.inst))) {
                    if (!combPopThrough(g, st, &t, limit)) return false;
                    continue;
                }
                t.len -= 1;
            },
            else => return false,
        }
    }
    return true;
}

fn virtPushComplete(g: *const grammar.Grammar, node_id: grammar.NodeId) bool {
    switch (g.node(node_id).*) {
        .literal => |lit| return lit.len == 0,
        .lit_trie => |lt| return lt.nodes[0].terminal,
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
/// to dominate the cache-hit fill_mask cost for such states. The hash is only an in-process cache key
/// and collisions are resolved by eqlStates, so the change of function is
/// not observable.
///
/// Chunk-carrying frames (open_obj) hash field values plus chunk CONTENT
/// (kind + payload) in spine order: handles are store-local and never
/// hashed, so equal configurations hash equal across owners (ADR-0006 D3).
pub const StateHash = struct {
    hash: u64,
    /// True when any frame references a side-store chunk (open_obj
    /// seen/key/ann or repeat tap/seen handle != 0); exactly the condition
    /// under which chunkBlobLen would produce a non-empty blob, so callers
    /// can skip the blob walk entirely for chunk-free states.
    has_chunks: bool,
};

pub fn hashState(st: *const State, side: *const Side) StateHash {
    var h = std.hash.Wyhash.init(0);
    var buf: [2]u8 = undefined;
    std.mem.writeInt(u16, &buf, st.n, .little);
    h.update(&buf);
    var has_chunks = false;
    for (st.threads[0..st.n]) |*t| {
        std.mem.writeInt(u16, &buf, t.len, .little);
        h.update(&buf);
        // Chunk-free frames hash as one bulk run of raw spine bytes (the
        // old per-frame switch issued a Wyhash update per field; the hash
        // is an in-process cache key, so the function is free to change).
        var run_start: u16 = 0;
        for (t.frames[0..t.len], 0..) |*f, i| {
            switch (f.*) {
                .open_obj => |*of| {
                    if (i > run_start) h.update(std.mem.sliceAsBytes(t.frames[run_start..i]));
                    run_start = @intCast(i + 1);
                    const tag: u8 = @intFromEnum(std.meta.activeTag(f.*));
                    h.update(&[_]u8{tag});
                    h.update(std.mem.asBytes(&of.node));
                    h.update(std.mem.asBytes(&of.next_idx));
                    h.update(&[_]u8{@intFromEnum(of.phase)});
                    h.update(std.mem.asBytes(&of.u));
                    hashChunk(&h, side, of.seen);
                    hashChunk(&h, side, of.key);
                    hashChunk(&h, side, of.ann);
                    has_chunks = has_chunks or of.seen != 0 or of.key != 0 or of.ann != 0;
                },
                .repeat => |*rf| {
                    if (i > run_start) h.update(std.mem.sliceAsBytes(t.frames[run_start..i]));
                    run_start = @intCast(i + 1);
                    const tag: u8 = @intFromEnum(std.meta.activeTag(f.*));
                    h.update(&[_]u8{tag});
                    h.update(std.mem.asBytes(&rf.node));
                    h.update(std.mem.asBytes(&rf.count));
                    h.update(std.mem.asBytes(&rf.ann));
                    h.update(std.mem.asBytes(&rf.matched));
                    h.update(&[_]u8{@intFromEnum(rf.phase)});
                    hashChunk(&h, side, rf.tap);
                    hashChunk(&h, side, rf.seen);
                    has_chunks = has_chunks or rf.tap != 0 or rf.seen != 0;
                },
                else => {},
            }
        }
        if (t.len > run_start) h.update(std.mem.sliceAsBytes(t.frames[run_start..t.len]));
    }
    return .{ .hash = h.final(), .has_chunks = has_chunks };
}

fn hashChunk(h: *std.hash.Wyhash, side: *const Side, handle: u32) void {
    if (handle == 0) {
        h.update(&[_]u8{0});
        return;
    }
    const c = side.chunkAt(handle);
    h.update(&[_]u8{ 1, @intFromEnum(c.kind) });
    var lb: [4]u8 = undefined;
    std.mem.writeInt(u32, &lb, @intCast(c.payload.items.len), .little);
    h.update(&lb);
    h.update(c.payload.items);
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

/// State equality across owners: spine bytes for chunk-free frames, field
/// values for open_obj frames, and chunk CONTENT via the blob of each side
/// (handles are store-local and prove nothing, ADR-0006 D2/D3).
pub fn eqlStatesBlobs(a: *const State, a_blob: []const u8, b: *const State, b_blob: []const u8) bool {
    if (a.n != b.n) return false;
    for (0..a.n) |i| {
        const ta = &a.threads[i];
        const tb = &b.threads[i];
        if (ta.len != tb.len) return false;
        // Fast path: neither thread carries a chunk-referencing frame, so
        // the whole spine compares as one raw byte slice (identical to the
        // per-frame tag + raw-bytes loop below for such frames).
        var any_chunks = false;
        for (ta.frames[0..ta.len]) |*f| {
            const tag = std.meta.activeTag(f.*);
            if (tag == .open_obj or tag == .repeat) {
                any_chunks = true;
                break;
            }
        }
        if (!any_chunks) {
            if (!std.mem.eql(u8, std.mem.sliceAsBytes(ta.frames[0..ta.len]), std.mem.sliceAsBytes(tb.frames[0..tb.len]))) return false;
            continue;
        }
        for (ta.frames[0..ta.len], tb.frames[0..tb.len]) |*fa, *fb| {
            const tag_a = std.meta.activeTag(fa.*);
            if (tag_a != std.meta.activeTag(fb.*)) return false;
            if (tag_a == .open_obj) {
                const oa = &fa.open_obj;
                const ob = &fb.open_obj;
                if (oa.node != ob.node or oa.next_idx != ob.next_idx or
                    oa.phase != ob.phase or !std.mem.eql(u8, std.mem.asBytes(&oa.u), std.mem.asBytes(&ob.u))) return false;
                if (!blobChunkEql(a_blob, oa.seen, b_blob, ob.seen)) return false;
                if (!blobChunkEql(a_blob, oa.key, b_blob, ob.key)) return false;
                if (!blobChunkEql(a_blob, oa.ann, b_blob, ob.ann)) return false;
                continue;
            }
            if (tag_a == .repeat) {
                const ra = &fa.repeat;
                const rb = &fb.repeat;
                if (ra.node != rb.node or ra.count != rb.count or ra.ann != rb.ann or
                    ra.matched != rb.matched or ra.phase != rb.phase) return false;
                if (!blobChunkEql(a_blob, ra.tap, b_blob, rb.tap)) return false;
                if (!blobChunkEql(a_blob, ra.seen, b_blob, rb.seen)) return false;
                continue;
            }
            if (!std.mem.eql(u8, std.mem.asBytes(fa), std.mem.asBytes(fb))) return false;
        }
    }
    return true;
}

fn blobFind(blob: []const u8, h: u32) ?[]const u8 {
    if (blob.len < 4) return null;
    const count = std.mem.readInt(u32, blob[0..4], .little);
    var off: usize = 4;
    var i: u32 = 0;
    while (i < count) : (i += 1) {
        const handle = std.mem.readInt(u32, blob[off..][0..4], .little);
        const len = std.mem.readInt(u32, blob[off + 4 ..][0..4], .little);
        const payload = blob[off + 8 .. off + 8 + len];
        if (handle == h) return payload;
        off += 8 + len;
    }
    return null;
}

fn blobChunkEql(a_blob: []const u8, ha: u32, b_blob: []const u8, hb: u32) bool {
    if (ha == 0 and hb == 0) return true;
    if (ha == 0 or hb == 0) return false;
    const pa = blobFind(a_blob, ha) orelse return false;
    const pb = blobFind(b_blob, hb) orelse return false;
    return std.mem.eql(u8, pa, pb);
}

/// Length of the chunk-content blob of a state: [count u32] then per
/// referenced chunk [handle u32][len u32][payload] in handle order. The
/// blob lets a foreign owner (the mask cache) compare chunk content
/// without retaining chunks (D3).
pub fn chunkBlobLen(st: *const State, side: *const Side) usize {
    var n: usize = 4;
    for (side.chunks.items, 0..) |mc, i| {
        if (mc == null) continue;
        const h: u32 = @intCast(i + 1);
        if (!stateRefsHandle(st, h)) continue;
        n += 8 + mc.?.payload.items.len;
    }
    return n;
}

pub fn writeChunkBlob(st: *const State, side: *const Side, out: []u8) void {
    std.debug.assert(out.len >= chunkBlobLen(st, side));
    var count: u32 = 0;
    var off: usize = 4;
    for (side.chunks.items, 0..) |mc, i| {
        if (mc == null) continue;
        const h: u32 = @intCast(i + 1);
        if (!stateRefsHandle(st, h)) continue;
        const c = mc.?;
        std.mem.writeInt(u32, out[off..][0..4], h, .little);
        std.mem.writeInt(u32, out[off + 4 ..][0..4], @intCast(c.payload.items.len), .little);
        @memcpy(out[off + 8 .. off + 8 + c.payload.items.len], c.payload.items);
        off += 8 + c.payload.items.len;
        count += 1;
    }
    std.mem.writeInt(u32, out[0..4], count, .little);
}

fn stateRefsHandle(st: *const State, h: u32) bool {
    for (st.threads[0..st.n]) |*t| {
        for (t.frames[0..t.len]) |*f| {
            switch (f.*) {
                .open_obj => |*of| {
                    if (of.seen == h or of.key == h or of.ann == h) return true;
                },
                .repeat => |*rf| {
                    if (rf.tap == h or rf.seen == h) return true;
                },
                else => {},
            }
        }
    }
    return false;
}

/// Exact byte length of the compact serialization produced by
/// `writeStateKey` (used as a completion-cache key in src/complete.zig).
pub fn stateKeyLen(st: *const State) usize {
    var n: usize = 2;
    for (st.threads[0..st.n]) |*t| n += 2 + @as(usize, t.len) * @sizeOf(Frame);
    return n;
}

/// Serializes the used part of a state into `out` (length `stateKeyLen`).
/// Frames are zero-filled when pushed (newFrame), so the byte image is
/// deterministic for equal states. Handles are raw: this key is only used
/// within a single owner (the completion cache), where handle identity is
/// meaningful.
pub fn writeStateKey(st: *const State, out: []u8) void {
    std.debug.assert(out.len >= stateKeyLen(st));
    var i: usize = 0;
    std.mem.writeInt(u16, out[i..][0..2], st.n, .little);
    i += 2;
    for (st.threads[0..st.n]) |*t| {
        std.mem.writeInt(u16, out[i..][0..2], t.len, .little);
        i += 2;
        const bytes = std.mem.sliceAsBytes(t.frames[0..t.len]);
        @memcpy(out[i .. i + bytes.len], bytes);
        i += bytes.len;
    }
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
    const root = try b.addNode(.{ .object = .{ .props = props } });
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
    const root = try b.addNode(.{ .object = .{ .props = props } });
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
    const root = try b.addNode(.{ .object = .{ .props = props } });
    return b.finish(arena, .json_schema, root, .{});
}

// Test helpers over a dummy side store: canonical test grammars never
// touch chunks, so the undefined allocator is a canary, not a hazard.
fn feedOk(g: *const grammar.Grammar, st: *State, bytes: []const u8) Error!void {
    var side = Side{ .a = undefined };
    var out: State = undefined;
    var work: State = undefined;
    try feedBytes(g, &side, st, bytes, &out, &work);
    st.* = out;
}

fn feedT(g: *const grammar.Grammar, in: *const State, bytes: []const u8, out: *State) Error!void {
    var side = Side{ .a = undefined };
    var work: State = undefined;
    try feedBytes(g, &side, in, bytes, out, &work);
}

fn initStateT(g: *const grammar.Grammar, max_threads: u16) Error!State {
    var side = Side{ .a = undefined };
    return initState(g, max_threads, &side);
}

test "literal: basic feed, partitions, tail byte rejected" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "hello");
    defer g.deinit();
    var st = try initStateT(&g, 4);
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
    const st = try initStateT(&g, 4);
    try testing.expect(canEnd(&g, &st));
    var st2 = st;
    try testing.expectError(error.Parse, feedOk(&g, &st2, "x"));
}

test "choice: common prefix a/ab" {
    const alloc = testing.allocator;
    var g = try choiceGrammar(alloc, &.{ "a", "ab" });
    defer g.deinit();
    var st = try initStateT(&g, 4);
    // Lazy alternation: one choice frame until the first byte arrives.
    try testing.expectEqual(@as(u16, 1), st.n);
    {
        var s2: State = undefined;
        try feedT(&g, &st, "ab", &s2);
        try testing.expect(canEnd(&g, &s2));
    }
    try feedOk(&g, &st, "a");
    // Both alternatives start with 'a': dispatched into two threads.
    try testing.expectEqual(@as(u16, 2), st.n);
    try testing.expect(canEnd(&g, &st));
    try feedOk(&g, &st, "b");
    try testing.expect(canEnd(&g, &st));
}

test "choice exceeding max_threads -> ResourceLimit" {
    const alloc = testing.allocator;
    // Lazy alternation defers spawning to the dispatch byte, so a wide
    // choice fits in a single thread at init; the cap is hit only when
    // more alternatives match a byte than there are threads left.
    var g = try choiceGrammar(alloc, &.{ "a", "ab", "ac" });
    defer g.deinit();
    var st = try initStateT(&g, 2);
    try testing.expectEqual(@as(u16, 1), st.n);
    try testing.expectError(error.ResourceLimit, feedOk(&g, &st, "a"));
    var g0 = try choiceGrammar(alloc, &.{ "a", "b", "c" });
    defer g0.deinit();
    try testing.expectError(error.ResourceLimit, initStateT(&g0, 0));
    var g1 = try litGrammar(alloc, "x");
    defer g1.deinit();
    try testing.expectError(error.ResourceLimit, initStateT(&g1, 0));
}

test "str: minLength/maxLength in symbols" {
    const alloc = testing.allocator;
    var g = try strGrammar(alloc, 2, 3);
    defer g.deinit();
    {
        var st = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"a\""));
    }
    {
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "\"ab\"");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "\"abc\"");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"abcd\""));
    }
}

test "str: escapes table" {
    const alloc = testing.allocator;
    var g = try strGrammar(alloc, 0, grammar.UNBOUNDED);
    defer g.deinit();
    {
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "\"a\\n\\t\\r\\b\\f\\\"\\\\\"");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"\\/\""));
    }
    {
        var st = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"a\x01\""));
    }
    {
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "\"\x7f\"");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "\"\\u001f\"");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"\\u0009\""));
    }
    {
        var st = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"\\u0041\""));
    }
    {
        var st = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"\\u001F\""));
    }
    {
        var st = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "\"\\'\""));
    }
}

test "str: escapes split across feedBytes calls" {
    const alloc = testing.allocator;
    var g = try strGrammar(alloc, 2, 2);
    defer g.deinit();
    var st = try initStateT(&g, 4);
    try feedOk(&g, &st, "\"a\\");
    try feedOk(&g, &st, "n\"");
    try testing.expect(canEnd(&g, &st));
    var g1 = try strGrammar(alloc, 1, 1);
    defer g1.deinit();
    var st1 = try initStateT(&g1, 4);
    try feedOk(&g1, &st1, "\"\\u0");
    try feedOk(&g1, &st1, "01f\"");
    try testing.expect(canEnd(&g1, &st1));
}

test "str: escape counts as one symbol" {
    const alloc = testing.allocator;
    var g = try strGrammar(alloc, 1, 1);
    defer g.deinit();
    var st = try initStateT(&g, 4);
    try feedOk(&g, &st, "\"\\n\"");
    try testing.expect(canEnd(&g, &st));
}

test "str: utf-8 split, validation, close with pending rem" {
    const alloc = testing.allocator;
    var g = try strGrammar(alloc, 1, 1);
    defer g.deinit();
    var st = try initStateT(&g, 4);
    try feedOk(&g, &st, "\"\xc3");
    try testing.expectError(error.Parse, feedOk(&g, &st, "\""));
    try feedOk(&g, &st, "\xa9\"");
    try testing.expect(canEnd(&g, &st));
    {
        var s2 = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &s2, "\"\x80\""));
    }
    {
        var s3 = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &s3, "\"\xc3\x41\""));
    }
    var g2 = try strGrammar(alloc, 2, 2);
    defer g2.deinit();
    {
        var s4 = try initStateT(&g2, 4);
        try testing.expectError(error.Parse, feedOk(&g2, &s4, "\"\xc3\xa9\""));
    }
    {
        var s5 = try initStateT(&g2, 4);
        try testing.expectError(error.Parse, feedOk(&g, &s5, "\"\xed\xa0\x80\""));
    }
}

test "int: forms, leading zero, root canEnd" {
    const alloc = testing.allocator;
    var g = try intGrammar(alloc);
    defer g.deinit();
    {
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "0");
        try testing.expect(canEnd(&g, &st));
        try testing.expectError(error.Parse, feedOk(&g, &st, "1"));
    }
    {
        var st = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "01"));
    }
    {
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "-12");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "-");
        try testing.expect(!canEnd(&g, &st));
        try testing.expectError(error.Parse, feedOk(&g, &st, "x"));
        try feedOk(&g, &st, "5");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 4);
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
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "1.5e+3");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "1.");
        try testing.expect(!canEnd(&g, &st));
        try feedOk(&g, &st, "5");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "2e");
        try testing.expect(!canEnd(&g, &st));
        try testing.expectError(error.Parse, feedOk(&g, &st, "."));
        try feedOk(&g, &st, "-");
        try testing.expect(!canEnd(&g, &st));
        try feedOk(&g, &st, "2");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, ".5"));
    }
    {
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "0E10");
        try testing.expect(canEnd(&g, &st));
    }
}

test "repeat: number before delimiter, min/max items" {
    const alloc = testing.allocator;
    var g = try intArrayGrammar(alloc, 1, 3);
    defer g.deinit();
    var st = try initStateT(&g, 4);
    try feedOk(&g, &st, "[12,");
    try testing.expect(!canEnd(&g, &st));
    try feedOk(&g, &st, "3]");
    try testing.expect(canEnd(&g, &st));
    {
        var gm = try intArrayGrammar(alloc, 2, 3);
        defer gm.deinit();
        var s1 = try initStateT(&gm, 4);
        try testing.expectError(error.Parse, feedOk(&gm, &s1, "[1]"));
        var s2 = try initStateT(&gm, 4);
        try feedOk(&gm, &s2, "[1,2]");
        try testing.expect(canEnd(&gm, &s2));
    }
    {
        var gx = try intArrayGrammar(alloc, 0, 2);
        defer gx.deinit();
        var s3 = try initStateT(&gx, 4);
        try feedOk(&gx, &s3, "[1,2");
        try testing.expectError(error.Parse, feedOk(&gx, &s3, ","));
        try feedOk(&gx, &s3, "]");
        try testing.expect(canEnd(&gx, &s3));
        var s4 = try initStateT(&gx, 4);
        try feedOk(&gx, &s4, "[]");
        try testing.expect(canEnd(&gx, &s4));
        var s5 = try initStateT(&gx, 4);
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
        var st = try initStateT(&g, 8);
        try feedOk(&g, &st, "ab12");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "abx"));
    }
    {
        var st = try initStateT(&g, 8);
        try feedOk(&g, &st, "5");
        try testing.expect(canEnd(&g, &st));
    }
}

test "object: order, optional skip, duplicate key, required guard" {
    const alloc = testing.allocator;
    var g = try objGrammar(alloc);
    defer g.deinit();
    {
        var st = try initStateT(&g, 8);
        try feedOk(&g, &st, "{\"a\":\"x\"}");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 8);
        try feedOk(&g, &st, "{\"a\":\"x\",\"b\":1}");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "{\"b\":1}"));
    }
    {
        var st = try initStateT(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "{}"));
    }
    {
        var st = try initStateT(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "{\"a\":\"x\",\"b\":1,\"b\":2}"));
    }
    {
        var st = try initStateT(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "{\"a\":\"x\",\"a\":\"y\"}"));
    }
    {
        var st = try initStateT(&g, 8);
        try feedOk(&g, &st, "{\"a\":\"x\"");
        try testing.expect(!canEnd(&g, &st));
    }
    {
        // DESIGN §1.4: ',' -> key phase; '}' in key phase is allowed when no
        // required properties remain, so {"a":"x",} is accepted per §1.4.
        var st = try initStateT(&g, 8);
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
        var st = try initStateT(&g, 8);
        try feedOk(&g, &st, "{\"b\":1}");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 8);
        try feedOk(&g, &st, "{\"a\":1,\"b\":2}");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "{\"a\":1}"));
    }
}

test "object: enum a/ab value, canEnd waits for separator" {
    const alloc = testing.allocator;
    var g = try enumObjGrammar(alloc);
    defer g.deinit();
    var st = try initStateT(&g, 8);
    try feedOk(&g, &st, "{\"x\":");
    {
        var s2: State = undefined;
        try feedT(&g, &st, "\"ab\"", &s2);
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
    var st = try initStateT(&g, 8);
    try feedOk(&g, &st, "{\"a\":\"x\",\"b\":12}");
    try testing.expect(canEnd(&g, &st));
}

test "hashState/eqlStates stable across feed partitions" {
    const alloc = testing.allocator;
    var g = try objGrammar(alloc);
    defer g.deinit();
    var s1 = try initStateT(&g, 8);
    try feedOk(&g, &s1, "{\"a\":\"x\"");
    try feedOk(&g, &s1, ",");
    var s2 = try initStateT(&g, 8);
    try feedOk(&g, &s2, "{\"a\":\"x\",");
    try testing.expect(eqlStates(&s1, &s2));
    {
        var side = Side{ .a = undefined };
        try testing.expectEqual(hashState(&s1, &side).hash, hashState(&s2, &side).hash);
    }
    try feedOk(&g, &s2, "\"b\":1}");
    try testing.expect(!eqlStates(&s1, &s2));
    try testing.expect(canEnd(&g, &s2));
}

fn litTrieGrammar(a: std.mem.Allocator, alts: []const []const u8) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const ids = try aa.alloc(grammar.NodeId, alts.len);
    for (alts, 0..) |s, i| ids[i] = try b.addLiteralNode(s);
    const root = if (alts.len == 1) ids[0] else try b.addNode(try b.alternativesNode(ids));
    return b.finish(arena, .literal_set, root, .{});
}

test "lit_trie: shared prefix, completion forks, canEnd" {
    const alloc = testing.allocator;
    var g = try litTrieGrammar(alloc, &.{ "a", "ab", "abc" });
    defer g.deinit();
    {
        var st = try initStateT(&g, 4);
        try testing.expect(!canEnd(&g, &st));
        try feedOk(&g, &st, "ab");
        try testing.expect(canEnd(&g, &st));
        try testing.expectError(error.Parse, feedOk(&g, &st, "x"));
    }
    {
        // 'a' completes while "ab"/"abc" remain: the completing thread and
        // the matching thread coexist.
        var st = try initStateT(&g, 4);
        try feedOk(&g, &st, "a");
        try testing.expect(canEnd(&g, &st));
        try feedOk(&g, &st, "b");
        try testing.expect(canEnd(&g, &st));
        try feedOk(&g, &st, "c");
        try testing.expect(canEnd(&g, &st));
        try testing.expectError(error.Parse, feedOk(&g, &st, "d"));
    }
    {
        var st = try initStateT(&g, 4);
        try testing.expectError(error.Parse, feedOk(&g, &st, "ac"));
    }
}

test "lit_trie: empty alternative completes immediately" {
    const alloc = testing.allocator;
    var g = try litTrieGrammar(alloc, &.{ "", "a" });
    defer g.deinit();
    var st = try initStateT(&g, 4);
    try testing.expect(canEnd(&g, &st));
    try feedOk(&g, &st, "a");
    try testing.expect(canEnd(&g, &st));
    try testing.expectError(error.Parse, feedOk(&g, &st, "z"));
    var st2 = try initStateT(&g, 4);
    try testing.expectError(error.Parse, feedOk(&g, &st2, "b"));
}

test "lit_trie: object value, enum with shared prefix" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const l0 = try b.addLiteralNode("\"sym_1\"");
    const l1 = try b.addLiteralNode("\"sym_12\"");
    const val = try b.addNode(try b.alternativesNode(&[_]grammar.NodeId{ l0, l1 }));
    const props = try b.copyProps(&[_]grammar.Prop{
        .{ .key = try b.addLiteral("\"x\":"), .value = val, .required = true },
    });
    const root = try b.addNode(.{ .object = .{ .props = props } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    {
        var st = try initStateT(&g, 8);
        try feedOk(&g, &st, "{\"x\":\"sym_1\"}");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 8);
        try feedOk(&g, &st, "{\"x\":\"sym_12\"}");
        try testing.expect(canEnd(&g, &st));
    }
    {
        var st = try initStateT(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "{\"x\":\"sym_2\"}"));
    }
    {
        var st = try initStateT(&g, 8);
        try testing.expectError(error.Parse, feedOk(&g, &st, "{\"x\":\"sym_1x\"}"));
    }
}

// --- open_obj (spec-v1) tests: real Side stores, chunks are exercised ---

fn feedS(g: *const grammar.Grammar, side: *Side, st: *State, bytes: []const u8) Error!void {
    var out: State = undefined;
    var work: State = undefined;
    try feedBytes(g, side, st, bytes, &out, &work);
    releaseState(side, st);
    st.* = out;
}

fn openObjGrammarA(a: std.mem.Allocator) !grammar.Grammar {
    // {"a": str (required), "b": int (optional), undeclared: int|str,
    //  extra_required: ["zz"]}
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const str_node = try b.addNode(.{ .str = .{ .min_len = 0, .max_len = grammar.UNBOUNDED } });
    const int_node = try b.addNode(.{ .int_v = {} });
    const val = try b.addNode(.{ .choice = try b.copyNodeIds(&[_]grammar.NodeId{ int_node, str_node }) });
    const props = try b.copyProps(&[_]grammar.Prop{
        .{ .key = try b.addLiteral("\"a\":"), .value = str_node, .required = true },
        .{ .key = try b.addLiteral("\"b\":"), .value = int_node, .required = false },
    });
    const extra = try aa.alloc(grammar.Literal, 1);
    extra[0] = try b.addLiteral("zz");
    const root = try b.addNode(.{ .open_obj = .{ .props = props, .extra_required = extra, .value = val } });
    return b.finish(arena, .json_schema, root, .{});
}

test "open_obj: interleave, uniqueness, required guards" {
    const alloc = testing.allocator;
    var g = try openObjGrammarA(alloc);
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    const cases = [_]Case{
        .{ .doc = "{\"u\":1,\"a\":\"s\",\"zz\":\"t\"}", .ok = true }, // undeclared first, interleave
        .{ .doc = "{\"a\":\"s\",\"zz\":1,\"u\":\"x\",\"b\":2}", .ok = true },
        .{ .doc = "{\"a\":\"s\",\"b\":2,\"zz\":1}", .ok = true },
        .{ .doc = "{\"zz\":1}", .ok = false }, // declared required "a" missing
        .{ .doc = "{\"a\":\"s\"}", .ok = false }, // extra_required "zz" missing
        .{ .doc = "{}", .ok = false },
        .{ .doc = "{\"a\":\"s\",\"zz\":1,\"u\":1,\"u\":2}", .ok = false }, // duplicate undeclared
        .{ .doc = "{\"a\":\"s\",\"a\":\"s\",\"zz\":1}", .ok = false }, // duplicate declared
        .{ .doc = "{\"b\":1,\"a\":\"s\",\"zz\":1}", .ok = false }, // declared out of order
        .{ .doc = "{\"b\":2,\"zz\":1}", .ok = false }, // landing on "b" skips required "a"
        .{ .doc = "{\"a\":\"s\",\"zz\":1,\"u\":[1]}", .ok = false }, // value type mismatch
        .{ .doc = "{\"a\":\"s\",\"zz\":1,}", .ok = false }, // trailing comma
        .{ .doc = "{\"a\":1,\"zz\":1}", .ok = false }, // declared value type mismatch
    };
    for (cases) |c| {
        var st = try initState(&g, 8, &side);
        if (c.ok) {
            try feedS(&g, &side, &st, c.doc);
            try testing.expect(canEnd(&g, &st));
        } else {
            try testing.expectError(error.Parse, feedS(&g, &side, &st, c.doc));
        }
        releaseState(&side, &st);
    }
}

fn openObjGrammarFree(a: std.mem.Allocator) !grammar.Grammar {
    // No declared props, no extra required; undeclared values int|str.
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const str_node = try b.addNode(.{ .str = .{ .min_len = 0, .max_len = grammar.UNBOUNDED } });
    const int_node = try b.addNode(.{ .int_v = {} });
    const val = try b.addNode(.{ .choice = try b.copyNodeIds(&[_]grammar.NodeId{ int_node, str_node }) });
    const root = try b.addNode(.{ .open_obj = .{ .props = &.{}, .extra_required = &.{}, .value = val } });
    return b.finish(arena, .json_schema, root, .{});
}

test "open_obj: COW seen-set across value-choice spawns" {
    const alloc = testing.allocator;
    var g = try openObjGrammarFree(alloc);
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    {
        // The value choice spawns a thread that shares the seen chunk; the
        // next key's seenAdd must copy-on-write. Two keys, mixed value types.
        var st = try initState(&g, 8, &side);
        try feedS(&g, &side, &st, "{\"k1\":1,\"k2\":\"x\"}");
        try testing.expect(canEnd(&g, &st));
        releaseState(&side, &st);
    }
    {
        var st = try initState(&g, 8, &side);
        try testing.expectError(error.Parse, feedS(&g, &side, &st, "{\"k1\":1,\"k1\":\"x\"}"));
        releaseState(&side, &st);
    }
    {
        var st = try initState(&g, 8, &side);
        try feedS(&g, &side, &st, "{}");
        try testing.expect(canEnd(&g, &st));
        releaseState(&side, &st);
    }
}

test "open_obj: cross-owner state equality and hash (ADR-0006 D2/D3)" {
    const alloc = testing.allocator;
    var g = try openObjGrammarFree(alloc);
    defer g.deinit();
    var side_a = Side.init(alloc);
    defer side_a.deinit();
    var side_b = Side.init(alloc);
    defer side_b.deinit();
    var sa = try initState(&g, 8, &side_a);
    var sb = try initState(&g, 8, &side_b);
    try feedS(&g, &side_a, &sa, "{\"k1\":1,\"k2\":");
    try feedS(&g, &side_b, &sb, "{\"k1\":1,\"k2\":");
    const blob_a = try alloc.alloc(u8, chunkBlobLen(&sa, &side_a));
    defer alloc.free(blob_a);
    writeChunkBlob(&sa, &side_a, blob_a);
    const blob_b = try alloc.alloc(u8, chunkBlobLen(&sb, &side_b));
    defer alloc.free(blob_b);
    writeChunkBlob(&sb, &side_b, blob_b);
    // Handles are store-local (sa's chunks get different handle numbers
    // only by accident); content equality must hold across owners.
    try testing.expect(eqlStatesBlobs(&sa, blob_a, &sb, blob_b));
    try testing.expectEqual(hashState(&sa, &side_a).hash, hashState(&sb, &side_b).hash);
    try feedS(&g, &side_b, &sb, "\"x\"");
    const blob_b2 = try alloc.alloc(u8, chunkBlobLen(&sb, &side_b));
    defer alloc.free(blob_b2);
    writeChunkBlob(&sb, &side_b, blob_b2);
    try testing.expect(!eqlStatesBlobs(&sa, blob_a, &sb, blob_b2));
    releaseState(&side_a, &sa);
    releaseState(&side_b, &sb);
}

test "open_obj: key escapes canonical, escaped declared key match" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const int_node = try b.addNode(.{ .int_v = {} });
    const props = try b.copyProps(&[_]grammar.Prop{
        .{ .key = try b.addLiteral("\"a\\nb\":"), .value = int_node, .required = true },
    });
    const root = try b.addNode(.{ .open_obj = .{ .props = props, .extra_required = &.{}, .value = int_node } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    {
        // Declared key written with the canonical escape matches.
        var st = try initState(&g, 8, &side);
        try feedS(&g, &side, &st, "{\"a\\nb\":1}");
        try testing.expect(canEnd(&g, &st));
        releaseState(&side, &st);
    }
    {
        // \uXXXX for a code >= 0x20 is outside the canonical table.
        var st = try initState(&g, 8, &side);
        try testing.expectError(error.Parse, feedS(&g, &side, &st, "{\"a\\u000ab\":1}"));
        releaseState(&side, &st);
    }
    {
        var st = try initState(&g, 8, &side);
        try testing.expectError(error.Parse, feedS(&g, &side, &st, "{\"a\\/b\":1}"));
        releaseState(&side, &st);
    }
}

fn intNumGrammar(a: std.mem.Allocator) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addNode(.{ .int_num = {} });
    return b.finish(arena, .json_schema, root, .{});
}

fn numConstGrammar(a: std.mem.Allocator, neg: bool, digits: []const u8, exp10: i64) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const root = try b.addNode(.{ .num_const = .{
        .neg = neg,
        .digits = try aa.dupe(u8, digits),
        .exp10 = exp10,
    } });
    return b.finish(arena, .json_schema, root, .{});
}

/// A document is valid when it feeds without a parse error AND the state
/// can end there: a prefix-valid non-integer ("1.5" against int_num) is
/// rejected only at the value check on completion.
fn docValid(g: *const grammar.Grammar, side: *Side, doc: []const u8) bool {
    var st = initState(g, 8, side) catch return false;
    defer releaseState(side, &st);
    feedS(g, side, &st, doc) catch return false;
    return canEnd(g, &st);
}

test "int_num: integer by value (semantics-spec-v1 4.4)" {
    const alloc = testing.allocator;
    var g = try intNumGrammar(alloc);
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    const cases = [_]Case{
        .{ .doc = "1", .ok = true },
        .{ .doc = "1.0", .ok = true },
        .{ .doc = "1e2", .ok = true },
        .{ .doc = "-0.0", .ok = true }, // -0 == 0
        .{ .doc = "0e-5", .ok = true }, // zero with any exponent
        .{ .doc = "1230e-1", .ok = true }, // trailing zero of the mantissa
        .{ .doc = "12.300e2", .ok = true },
        .{ .doc = "1.00e2", .ok = true },
        .{ .doc = "1.5", .ok = false },
        .{ .doc = "0.5", .ok = false },
        .{ .doc = "15e-1", .ok = false },
        .{ .doc = "1e-1", .ok = false },
        .{ .doc = "0.11e1", .ok = false },
        .{ .doc = "-1.5", .ok = false },
        .{ .doc = "1.", .ok = false },
        .{ .doc = ".5", .ok = false },
        .{ .doc = "01", .ok = false },
        .{ .doc = "1e", .ok = false },
    };
    for (cases) |c| {
        try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
    }
}

test "num_const: const by value (ADR-0006 D6)" {
    const alloc = testing.allocator;
    var side = Side.init(alloc);
    defer side.deinit();
    {
        // 1 = digits "1", exp10 0
        var g = try numConstGrammar(alloc, false, "1", 0);
        defer g.deinit();
        const Case = struct { doc: []const u8, ok: bool };
        const cases = [_]Case{
            .{ .doc = "1", .ok = true },
            .{ .doc = "1.0", .ok = true },
            .{ .doc = "1e0", .ok = true },
            .{ .doc = "10e-1", .ok = true },
            .{ .doc = "0.1e1", .ok = true },
            .{ .doc = "1.00", .ok = true },
            .{ .doc = "2", .ok = false },
            .{ .doc = "-1", .ok = false },
            .{ .doc = "1.5", .ok = false },
            .{ .doc = "10", .ok = false },
            .{ .doc = "0.5", .ok = false },
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
    {
        // 1.5 = digits "15", exp10 -1
        var g = try numConstGrammar(alloc, false, "15", -1);
        defer g.deinit();
        const Case = struct { doc: []const u8, ok: bool };
        const cases = [_]Case{
            .{ .doc = "1.5", .ok = true },
            .{ .doc = "1.50", .ok = true },
            .{ .doc = "15e-1", .ok = true },
            .{ .doc = "150e-2", .ok = true },
            .{ .doc = "0.15e1", .ok = true },
            .{ .doc = "0.015e2", .ok = true },
            .{ .doc = "-1.5", .ok = false },
            .{ .doc = "1.4", .ok = false },
            .{ .doc = "1.51", .ok = false },
            .{ .doc = "15", .ok = false },
            .{ .doc = "0.15", .ok = false },
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
    {
        // -2.5: sign must match
        var g = try numConstGrammar(alloc, true, "25", -1);
        defer g.deinit();
        try testing.expect(docValid(&g, &side, "-2.5"));
        try testing.expect(docValid(&g, &side, "-25e-1"));
        try testing.expect(!docValid(&g, &side, "2.5"));
    }
    {
        // zero constant: digits empty; any zero spelling matches, sign free
        var g = try numConstGrammar(alloc, false, "", 0);
        defer g.deinit();
        try testing.expect(docValid(&g, &side, "0"));
        try testing.expect(docValid(&g, &side, "-0"));
        try testing.expect(docValid(&g, &side, "0.0e5"));
        try testing.expect(!docValid(&g, &side, "1"));
        try testing.expect(!docValid(&g, &side, "0.1"));
        try testing.expect(!docValid(&g, &side, "10e-1"));
    }
}

fn numRangeGrammar(a: std.mem.Allocator, nr: grammar.NumRange) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    var n = nr;
    if (n.min) |*m| m.digits = try aa.dupe(u8, m.digits);
    if (n.max) |*m| m.digits = try aa.dupe(u8, m.digits);
    const root = try b.addNode(.{ .num_range = n });
    return b.finish(arena, .json_schema, root, .{});
}

fn numMultGrammar(a: std.mem.Allocator, nm: grammar.NumMult) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addNode(.{ .num_mult = nm });
    return b.finish(arena, .json_schema, root, .{});
}

test "num_range: inclusive bounds by value (semantics-spec-v1 4.4)" {
    const alloc = testing.allocator;
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    {
        // -2 <= V <= 3 (minimum -2, maximum 3.0: same canonical value)
        var g = try numRangeGrammar(alloc, .{
            .min = .{ .neg = true, .digits = "2", .exp10 = 0 },
            .max = .{ .neg = false, .digits = "3", .exp10 = 0 },
        });
        defer g.deinit();
        const cases = [_]Case{
            .{ .doc = "-2", .ok = true },
            .{ .doc = "-2.0", .ok = true }, // value equality with the bound
            .{ .doc = "-2e0", .ok = true },
            .{ .doc = "0", .ok = true },
            .{ .doc = "-0", .ok = true },
            .{ .doc = "3", .ok = true },
            .{ .doc = "3.0", .ok = true },
            .{ .doc = "2.6", .ok = true },
            .{ .doc = "-2.0001", .ok = false },
            .{ .doc = "-3", .ok = false },
            .{ .doc = "3.5", .ok = false },
            .{ .doc = "3.0001", .ok = false },
            .{ .doc = "30", .ok = false }, // order of magnitude over digit cmp
            .{ .doc = "-20", .ok = false },
            .{ .doc = "0.3e1", .ok = true }, // 3 by value
            .{ .doc = "299e-2", .ok = true }, // 2.99
            .{ .doc = "1e1", .ok = false }, // 10
            .{ .doc = "1e-3", .ok = true }, // 0.001
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
    {
        // minimum 1.1 only
        var g = try numRangeGrammar(alloc, .{
            .min = .{ .neg = false, .digits = "11", .exp10 = -1 },
        });
        defer g.deinit();
        const cases = [_]Case{
            .{ .doc = "1.1", .ok = true },
            .{ .doc = "1.10", .ok = true },
            .{ .doc = "11e-1", .ok = true },
            .{ .doc = "2.6", .ok = true },
            .{ .doc = "1.10001", .ok = true },
            .{ .doc = "1.0999", .ok = false },
            .{ .doc = "0.6", .ok = false },
            .{ .doc = "1.01", .ok = false }, // digit-prefix of the bound
            .{ .doc = "1.101", .ok = true },
            .{ .doc = "0.11e2", .ok = true }, // 11
            .{ .doc = "0.1", .ok = false },
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
    {
        // maximum 300 (digits "3", exp10 2)
        var g = try numRangeGrammar(alloc, .{
            .max = .{ .neg = false, .digits = "3", .exp10 = 2 },
        });
        defer g.deinit();
        const cases = [_]Case{
            .{ .doc = "300", .ok = true },
            .{ .doc = "300.0", .ok = true },
            .{ .doc = "3e2", .ok = true },
            .{ .doc = "299.97", .ok = true },
            .{ .doc = "300.5", .ok = false },
            .{ .doc = "300.0001", .ok = false },
            .{ .doc = "-1000000", .ok = true },
            .{ .doc = "4e2", .ok = false },
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
}

test "num_range: exclusive bounds and zero bound" {
    const alloc = testing.allocator;
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    {
        // V > 1.1
        var g = try numRangeGrammar(alloc, .{
            .min = .{ .neg = false, .digits = "11", .exp10 = -1 },
            .min_excl = true,
        });
        defer g.deinit();
        const cases = [_]Case{
            .{ .doc = "1.2", .ok = true },
            .{ .doc = "1.1", .ok = false },
            .{ .doc = "1.10", .ok = false }, // equal by value: excluded
            .{ .doc = "1.10001", .ok = true },
            .{ .doc = "0.6", .ok = false },
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
    {
        // V < 3.0
        var g = try numRangeGrammar(alloc, .{
            .max = .{ .neg = false, .digits = "3", .exp10 = 0 },
            .max_excl = true,
        });
        defer g.deinit();
        const cases = [_]Case{
            .{ .doc = "2.2", .ok = true },
            .{ .doc = "3", .ok = false },
            .{ .doc = "3.0", .ok = false },
            .{ .doc = "2.99999", .ok = true },
            .{ .doc = "-100", .ok = true },
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
    {
        // 0 < V <= 0 (empty? no: impossible) -- use 0 <= V < 1 instead
        var g = try numRangeGrammar(alloc, .{
            .min = .{ .neg = false, .digits = "", .exp10 = 0 },
            .max = .{ .neg = false, .digits = "1", .exp10 = 0 },
            .max_excl = true,
        });
        defer g.deinit();
        const cases = [_]Case{
            .{ .doc = "0", .ok = true },
            .{ .doc = "-0.0", .ok = true }, // zero spellings, sign dropped
            .{ .doc = "0.5", .ok = true },
            .{ .doc = "1", .ok = false },
            .{ .doc = "-0.5", .ok = false },
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
    {
        // negative exclusive: V < -0.5
        var g = try numRangeGrammar(alloc, .{
            .max = .{ .neg = true, .digits = "5", .exp10 = -1 },
            .max_excl = true,
        });
        defer g.deinit();
        const cases = [_]Case{
            .{ .doc = "-0.6", .ok = true },
            .{ .doc = "-0.5", .ok = false },
            .{ .doc = "-0.50", .ok = false },
            .{ .doc = "-0.4", .ok = false },
            .{ .doc = "0", .ok = false },
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
}

test "num_mult: exact decimal divisibility (multipleOf)" {
    const alloc = testing.allocator;
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    {
        // multipleOf 2
        var g = try numMultGrammar(alloc, .{ .div = 2, .div_exp10 = 0, .co = 1 });
        defer g.deinit();
        const cases = [_]Case{
            .{ .doc = "10", .ok = true },
            .{ .doc = "0", .ok = true },
            .{ .doc = "-0.0", .ok = true }, // zero is a multiple
            .{ .doc = "7", .ok = false },
            .{ .doc = "-4", .ok = true },
            .{ .doc = "1e3", .ok = true }, // 1000
            .{ .doc = "100.0", .ok = true },
            .{ .doc = "5e-1", .ok = false }, // 0.5
            .{ .doc = "4e-1", .ok = false }, // 0.4 / 2 = 0.2 is not an integer
            .{ .doc = "4.4e-1", .ok = false }, // 0.44 / 2 = 0.22
            .{ .doc = "4e0", .ok = true },
            .{ .doc = "3", .ok = false },
            .{ .doc = "1.5e1", .ok = false }, // 15
            .{ .doc = "0.02e2", .ok = true }, // 2
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
    {
        // multipleOf 1.5 (div 15, exp10 -1)
        var g = try numMultGrammar(alloc, .{ .div = 15, .div_exp10 = -1, .co = 3 });
        defer g.deinit();
        const cases = [_]Case{
            .{ .doc = "0", .ok = true },
            .{ .doc = "4.5", .ok = true },
            .{ .doc = "3", .ok = true },
            .{ .doc = "7.5", .ok = true },
            .{ .doc = "0.15e1", .ok = true }, // 1.5
            .{ .doc = "35", .ok = false },
            .{ .doc = "0.75", .ok = false },
            .{ .doc = "4.50", .ok = true }, // trailing zero of the mantissa
            .{ .doc = "-3", .ok = true },
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
    {
        // multipleOf 0.0001 (div 1, exp10 -4)
        var g = try numMultGrammar(alloc, .{ .div = 1, .div_exp10 = -4, .co = 1 });
        defer g.deinit();
        const cases = [_]Case{
            .{ .doc = "0.0075", .ok = true },
            .{ .doc = "0.00751", .ok = false },
            .{ .doc = "1", .ok = true },
            .{ .doc = "123.4567", .ok = true },
            .{ .doc = "123.45678", .ok = false },
            .{ .doc = "1e-4", .ok = true },
            .{ .doc = "1e-5", .ok = false },
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
    {
        // multipleOf 0.123456789 (9 significant digits, exp10 -9)
        var g = try numMultGrammar(alloc, .{ .div = 123456789, .div_exp10 = -9, .co = 123456789 });
        defer g.deinit();
        const cases = [_]Case{
            .{ .doc = "0.123456789", .ok = true },
            .{ .doc = "1e+308", .ok = false }, // 10^317 mod 123456789 != 0
            .{ .doc = "0.246913578", .ok = true },
            .{ .doc = "0.246913579", .ok = false },
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
    {
        // multipleOf 1e-8: every integer is a multiple
        var g = try numMultGrammar(alloc, .{ .div = 1, .div_exp10 = -8, .co = 1 });
        defer g.deinit();
        try testing.expect(docValid(&g, &side, "12391239123"));
        try testing.expect(!docValid(&g, &side, "1.000000001"));
    }
}

test "num_range/num_mult: boundary verdict feeds comb groups (P3 cascade)" {
    const alloc = testing.allocator;
    var side = Side.init(alloc);
    defer side.deinit();
    // allOf[num_range(>=5), num_mult(2)]: both verdicts must hold; a
    // failing branch is a confirmed-boundary rejection resolved through
    // the comb group (combBoundaryFail), not a mid-value error.
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const rn = try b.addNode(.{ .num_range = .{ .min = .{ .neg = false, .digits = "5", .exp10 = 0 } } });
    const mn = try b.addNode(.{ .num_mult = .{ .div = 2, .div_exp10 = 0, .co = 1 } });
    const root = try b.addNode(.{ .comb = .{
        .kind = .allof,
        .branches = try b.copyNodeIds(&[_]grammar.NodeId{ rn, mn }),
    } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    const cases = [_]Case{
        .{ .doc = "6", .ok = true },
        .{ .doc = "10", .ok = true },
        .{ .doc = "5", .ok = false }, // not a multiple of 2
        .{ .doc = "4", .ok = false }, // below the minimum
        .{ .doc = "3", .ok = false }, // both fail
        .{ .doc = "7", .ok = false }, // in range, not a multiple
        .{ .doc = "6.0", .ok = true },
        .{ .doc = "-6", .ok = false },
    };
    for (cases) |c| {
        try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
    }
}

test "int_num/num_const: frames fit the 32-byte frame budget" {
    try testing.expect(@sizeOf(IntNumFrame) <= 32);
    try testing.expect(@sizeOf(NumConstFrame) <= 32);
    try testing.expect(@sizeOf(NumRangeFrame) <= 32);
    try testing.expect(@sizeOf(NumMultFrame) <= 32);
    try testing.expectEqual(@as(usize, 32), @sizeOf(Frame));
}

// ---- spec-v1 P2: repeat prefix/contains/unique, open_obj deps/props ----

test "repeat: tuple prefix by position" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const str_node = try b.addNode(.{ .str = .{ .min_len = 0, .max_len = grammar.UNBOUNDED } });
    const int_node = try b.addNode(.{ .int_v = {} });
    const prefix = try b.copyNodeIds(&[_]grammar.NodeId{str_node});
    const root = try b.addNode(.{ .repeat = .{
        .item = int_node,
        .min = 0,
        .max = grammar.UNBOUNDED,
        .prefix = prefix,
    } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    const cases = [_]Case{
        .{ .doc = "[\"a\",1,2]", .ok = true },
        .{ .doc = "[\"a\"]", .ok = true },
        .{ .doc = "[]", .ok = true },
        .{ .doc = "[1]", .ok = false }, // prefix position takes the string schema
        .{ .doc = "[\"a\",\"b\"]", .ok = false }, // past the prefix: int schema
    };
    for (cases) |c| {
        try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
    }
}

test "repeat: contains with min/max contains" {
    const alloc = testing.allocator;
    const Case = struct { doc: []const u8, ok: bool };
    var side = Side.init(alloc);
    defer side.deinit();
    const mk = struct {
        fn go(a: std.mem.Allocator, min_c: u32, max_c: u32) !grammar.Grammar {
            var arena = std.heap.ArenaAllocator.init(a);
            const aa = arena.allocator();
            var b = grammar.Builder.init(aa);
            const str_node = try b.addNode(.{ .str = .{ .min_len = 0, .max_len = grammar.UNBOUNDED } });
            const int_node = try b.addNode(.{ .int_v = {} });
            const item = try b.addNode(.{ .choice = try b.copyNodeIds(&[_]grammar.NodeId{ int_node, str_node }) });
            const root = try b.addNode(.{ .repeat = .{
                .item = item,
                .min = 0,
                .max = grammar.UNBOUNDED,
                .contains = int_node,
                .min_contains = min_c,
                .max_contains = max_c,
            } });
            return b.finish(arena, .json_schema, root, .{});
        }
    }.go;
    {
        var g = try mk(alloc, 1, grammar.UNBOUNDED);
        defer g.deinit();
        const cases = [_]Case{
            .{ .doc = "[1]", .ok = true },
            .{ .doc = "[\"a\",1]", .ok = true },
            .{ .doc = "[\"a\",\"b\"]", .ok = false },
            .{ .doc = "[]", .ok = false },
        };
        for (cases) |c| {
            try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
        }
    }
    {
        var g = try mk(alloc, 2, grammar.UNBOUNDED);
        defer g.deinit();
        try testing.expect(docValid(&g, &side, "[1,\"a\",2]"));
        try testing.expect(!docValid(&g, &side, "[1,\"a\"]"));
    }
    {
        var g = try mk(alloc, 1, 1);
        defer g.deinit();
        try testing.expect(docValid(&g, &side, "[\"a\",1]"));
        try testing.expect(!docValid(&g, &side, "[1,2]")); // maxContains exceeded
        try testing.expect(!docValid(&g, &side, "[\"a\"]")); // minContains missed
    }
}

test "repeat: uniqueItems by canonical value" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const num_node = try b.addNode(.{ .num_v = {} });
    const root = try b.addNode(.{ .repeat = .{
        .item = num_node,
        .min = 0,
        .max = grammar.UNBOUNDED,
        .unique = true,
    } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    try testing.expect(docValid(&g, &side, "[1,2,3]"));
    try testing.expect(docValid(&g, &side, "[]"));
    try testing.expect(!docValid(&g, &side, "[1,1]"));
    try testing.expect(!docValid(&g, &side, "[1,1.0]")); // equal by value
    try testing.expect(!docValid(&g, &side, "[1,10e-1]"));
}

test "repeat: uniqueItems finite-set residual" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const one = try b.addLiteralNode("1");
    const two = try b.addLiteralNode("2");
    const item = try b.addNode(try b.alternativesNode(&[_]grammar.NodeId{ one, two }));
    const root = try b.addNode(.{ .repeat = .{
        .item = item,
        .min = 0,
        .max = grammar.UNBOUNDED,
        .unique = true,
        .uniq_finite = 2,
    } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    try testing.expect(docValid(&g, &side, "[1,2]"));
    try testing.expect(docValid(&g, &side, "[1]"));
    try testing.expect(!docValid(&g, &side, "[1,1]"));
    try testing.expect(!docValid(&g, &side, "[1,2,1]"));
}

fn openObjDepsGrammar(a: std.mem.Allocator) !grammar.Grammar {
    // Undeclared values int; min 1 max 2 properties; dependentRequired
    // a -> b; dependentSchemas c -> closed object {"d": int required}.
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const int_node = try b.addNode(.{ .int_v = {} });
    const d_prop = grammar.Prop{
        .key = try b.addLiteral("\"d\":"),
        .value = int_node,
        .required = true,
    };
    const dep_schema = try b.addNode(.{ .open_obj = .{
        .props = try b.copyProps(&[_]grammar.Prop{d_prop}),
        .extra_required = &.{},
        .value = int_node,
    } });
    const deps = try b.copyDeps(&[_]grammar.Dep{
        .{
            .trigger = try b.addLiteral("a"),
            .kind = .required,
            .names = try b.copyLiterals(&[_]grammar.Literal{try b.addLiteral("b")}),
        },
        .{ .trigger = try b.addLiteral("c"), .kind = .schema, .schema = dep_schema },
        .{ .trigger = try b.addLiteral("z"), .kind = .ban },
    });
    const root = try b.addNode(.{ .open_obj = .{
        .props = &.{},
        .extra_required = &.{},
        .value = int_node,
        .min_props = 1,
        .max_props = 3,
        .track_keys = true,
        .capture = true,
        .deps = deps,
    } });
    return b.finish(arena, .json_schema, root, .{});
}

test "open_obj: min/maxProperties and dependencies" {
    const alloc = testing.allocator;
    var g = try openObjDepsGrammar(alloc);
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    const cases = [_]Case{
        .{ .doc = "{\"b\":1}", .ok = true },
        .{ .doc = "{\"a\":1,\"b\":2}", .ok = true },
        .{ .doc = "{\"a\":1}", .ok = false }, // dependentRequired a -> b
        .{ .doc = "{\"c\":1,\"d\":2}", .ok = true }, // dependentSchemas c -> {d required}
        .{ .doc = "{\"c\":1}", .ok = false }, // schema dep unmet
        .{ .doc = "{\"c\":1,\"d\":\"s\"}", .ok = false }, // schema dep type mismatch
        .{ .doc = "{\"z\":1}", .ok = false }, // ban dep
        .{ .doc = "{}", .ok = false }, // minProperties 1
        .{ .doc = "{\"a\":1,\"b\":2,\"c\":3,\"d\":4}", .ok = false }, // maxProperties 3
    };
    for (cases) |c| {
        try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
    }
}

// ---- spec-v1 P3: boolean combinators and complement nodes ----

fn combGrammar(a: std.mem.Allocator, kind: grammar.CombKind, branch_nodes: []const grammar.Node, branch_ids: []const grammar.NodeId) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const ids = try aa.alloc(grammar.NodeId, branch_nodes.len);
    for (branch_nodes, 0..) |n, i| {
        ids[i] = try b.addNode(n);
    }
    var branches = try aa.alloc(grammar.NodeId, branch_ids.len);
    for (branch_ids, 0..) |bi, i| {
        branches[i] = if (bi == grammar.COMB_NONE) grammar.COMB_NONE else ids[bi];
    }
    const root = try b.addNode(.{ .comb = .{ .kind = kind, .branches = branches } });
    return b.finish(arena, .json_schema, root, .{});
}

test "comb oneOf: exactly one branch must accept (ADR-0005 D2)" {
    const alloc = testing.allocator;
    var g = try combGrammar(alloc, .oneof, &.{ .{ .num_v = {} }, .{ .int_num = {} } }, &.{ 0, 1 });
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    const cases = [_]Case{
        .{ .doc = "1.5", .ok = true }, // only number
        .{ .doc = "1e-1", .ok = true }, // 0.1: only number
        .{ .doc = "1", .ok = false }, // both accept
        .{ .doc = "15", .ok = false }, // both accept
        .{ .doc = "1e2", .ok = false }, // 100 is an integer by value: both
        .{ .doc = "\"x\"", .ok = false }, // neither
    };
    for (cases) |c| {
        try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
    }
}

test "comb allOf: every branch must accept" {
    const alloc = testing.allocator;
    var g = try combGrammar(alloc, .allof, &.{
        .{ .int_num = {} },
        .{ .num_const = .{ .neg = false, .digits = "5", .exp10 = 0 } },
    }, &.{ 0, 1 });
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    const cases = [_]Case{
        .{ .doc = "5", .ok = true },
        .{ .doc = "5.0", .ok = true }, // 5.0 == 5 and an integer by value
        .{ .doc = "6", .ok = false }, // not the constant
        .{ .doc = "5.5", .ok = false }, // neither
        .{ .doc = "\"5\"", .ok = false },
    };
    for (cases) |c| {
        try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
    }
}

test "comb if/then/else: (if /\\ then) \\/ (~if /\\ else)" {
    const alloc = testing.allocator;
    var g = try combGrammar(alloc, .ifelse, &.{
        .{ .num_const = .{ .neg = false, .digits = &.{}, .exp10 = 0 } }, // if: == 0
        .{ .num_const = .{ .neg = false, .digits = "1", .exp10 = 0 } }, // then: == 1
        .{ .num_v = {} }, // else: any number
    }, &.{ 0, 1, 2 });
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    const cases = [_]Case{
        .{ .doc = "0", .ok = false }, // if holds, then does not
        .{ .doc = "0.0", .ok = false }, // 0.0 == 0: same
        .{ .doc = "1", .ok = true }, // if fails, else accepts
        .{ .doc = "2.5", .ok = true },
        .{ .doc = "\"x\"", .ok = false }, // if fails, else does not accept
    };
    for (cases) |c| {
        try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
    }
}

test "comb if/then without else and born-doomed allOf" {
    const alloc = testing.allocator;
    // if 0 then 1 (no else): valid iff (0 -> ==1) i.e. anything but 0 -
    // but only documents a live branch can consume. "0" matches if, fails
    // then: reject. "1" fails if, and the vacuous else accepts.
    var g = try combGrammar(alloc, .ifelse, &.{
        .{ .num_const = .{ .neg = false, .digits = &.{}, .exp10 = 0 } },
        .{ .num_const = .{ .neg = false, .digits = "1", .exp10 = 0 } },
    }, &.{ 0, 1, grammar.COMB_NONE });
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    try testing.expect(!docValid(&g, &side, "0"));
    try testing.expect(docValid(&g, &side, "1"));
    // "2" matches neither branch; with no else branch there is no live
    // thread left to track the value extent, so the parser rejects. The
    // verdict itself (~if /\ true) would accept - the schema compiler
    // therefore always lowers an absent else to an AnyJSON carrier branch
    // instead of COMB_NONE.
    try testing.expect(!docValid(&g, &side, "2"));
    // An allOf with an empty-language branch is born doomed: the group
    // dies at activation, before consuming a byte.
    var g2 = try combGrammar(alloc, .allof, &.{
        .{ .int_num = {} },
        .{ .str = .{ .min_len = 1, .max_len = 0 } },
    }, &.{ 0, 1 });
    defer g2.deinit();
    try testing.expect(!docValid(&g2, &side, "5"));
    try testing.expect(!docValid(&g2, &side, "\"x\""));
}

test "comb sweep: a dead branch kills the allOf group mid-array" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const n_int = try b.addNode(.{ .int_num = {} });
    const n_five = try b.addNode(.{ .num_const = .{ .neg = false, .digits = "5", .exp10 = 0 } });
    const n_comb = try b.addNode(.{ .comb = .{ .kind = .allof, .branches = try b.copyNodeIds(&.{ n_int, n_five }) } });
    const root = try b.addNode(.{ .repeat = .{ .item = n_comb, .min = 0, .max = grammar.UNBOUNDED } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    try testing.expect(docValid(&g, &side, "[5]"));
    try testing.expect(docValid(&g, &side, "[5,5.0]"));
    try testing.expect(docValid(&g, &side, "[]"));
    try testing.expect(!docValid(&g, &side, "[1]")); // boundary resolution
    try testing.expect(!docValid(&g, &side, "[15]")); // const branch dies on '5', sweep kills the group
    try testing.expect(!docValid(&g, &side, "[5,6]"));
}

test "not_int_num: numbers whose value is not an integer" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addNode(.{ .not_int_num = {} });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    const cases = [_]Case{
        .{ .doc = "1.5", .ok = true },
        .{ .doc = "1e-1", .ok = true },
        .{ .doc = "-0.5", .ok = true },
        .{ .doc = "1", .ok = false },
        .{ .doc = "1.0", .ok = false },
        .{ .doc = "1e2", .ok = false },
        .{ .doc = "-0", .ok = false },
        .{ .doc = "\"1.5\"", .ok = false },
    };
    for (cases) |c| {
        try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
    }
}

fn strExclGrammar(a: std.mem.Allocator, forbidden: []const []const u8) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const lits = try aa.alloc(grammar.Literal, forbidden.len);
    for (forbidden, 0..) |s, i| {
        lits[i] = try b.addLiteral(s);
    }
    const root = try b.addNode(.{ .str_excl = .{
        .trie = try b.buildTrie(lits),
        .min_len = 0,
        .max_len = grammar.UNBOUNDED,
    } });
    return b.finish(arena, .json_schema, root, .{});
}

test "str_excl: string language minus a forbidden set" {
    const alloc = testing.allocator;
    var g = try strExclGrammar(alloc, &.{"\"ab\""});
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    const cases = [_]Case{
        .{ .doc = "\"ab\"", .ok = false },
        .{ .doc = "\"abc\"", .ok = true },
        .{ .doc = "\"a\"", .ok = true },
        .{ .doc = "\"\"", .ok = true },
        .{ .doc = "\"abx\"", .ok = true },
        .{ .doc = "\"xab\"", .ok = true },
        .{ .doc = "1", .ok = false }, // not a string at all
    };
    for (cases) |c| {
        try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
    }
    // Two forbidden strings sharing a prefix.
    var g2 = try strExclGrammar(alloc, &.{ "\"ab\"", "\"ac\"" });
    defer g2.deinit();
    try testing.expect(!docValid(&g2, &side, "\"ab\""));
    try testing.expect(!docValid(&g2, &side, "\"ac\""));
    try testing.expect(docValid(&g2, &side, "\"ad\""));
}

test "num_excl: number language minus up to two constants" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const root = try b.addNode(.{
        .num_excl = .{
            .consts = try b.copyNumConsts(&.{
                .{ .neg = false, .digits = "1", .exp10 = 0 }, // 1
                .{ .neg = false, .digits = "25", .exp10 = -1 }, // 2.5
            }),
        },
    });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    const cases = [_]Case{
        .{ .doc = "1", .ok = false },
        .{ .doc = "1.0", .ok = false },
        .{ .doc = "10e-1", .ok = false },
        .{ .doc = "2.5", .ok = false },
        .{ .doc = "2.50", .ok = false },
        .{ .doc = "3", .ok = true },
        .{ .doc = "1.5", .ok = true },
        .{ .doc = "-1", .ok = true },
        .{ .doc = "2.51", .ok = true },
        .{ .doc = "25", .ok = true },
    };
    for (cases) |c| {
        try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
    }
}

test "comb nested in an object property value" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const n_num = try b.addNode(.{ .num_v = {} });
    const n_int = try b.addNode(.{ .int_num = {} });
    const n_comb = try b.addNode(.{ .comb = .{ .kind = .oneof, .branches = try b.copyNodeIds(&.{ n_num, n_int }) } });
    const props = try aa.alloc(grammar.Prop, 1);
    props[0] = .{ .key = try b.addLiteral("\"k\":"), .value = n_comb, .required = false };
    const root = try b.addNode(.{ .open_obj = .{
        .props = props,
        .extra_required = &.{},
        .value = n_num,
    } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    try testing.expect(docValid(&g, &side, "{\"k\":1.5}"));
    try testing.expect(!docValid(&g, &side, "{\"k\":1}")); // both branches accept
    try testing.expect(docValid(&g, &side, "{}"));
    try testing.expect(docValid(&g, &side, "{\"k\":1.5,\"z\":2}"));
}

test "comb nested in a closed object property value" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const n_num = try b.addNode(.{ .num_v = {} });
    const n_int = try b.addNode(.{ .int_num = {} });
    const n_comb = try b.addNode(.{ .comb = .{ .kind = .oneof, .branches = try b.copyNodeIds(&.{ n_num, n_int }) } });
    const props = try aa.alloc(grammar.Prop, 1);
    props[0] = .{ .key = try b.addLiteral("\"k\":"), .value = n_comb, .required = true };
    const root = try b.addNode(.{ .object = .{ .props = props } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    try testing.expect(docValid(&g, &side, "{\"k\":1.5}"));
    try testing.expect(!docValid(&g, &side, "{\"k\":1}")); // both branches accept
    try testing.expect(!docValid(&g, &side, "{}")); // k is required
}

test "comb allOf as an array item" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const n_int = try b.addNode(.{ .int_num = {} });
    const n_five = try b.addNode(.{ .num_const = .{ .neg = false, .digits = "5", .exp10 = 0 } });
    const n_comb = try b.addNode(.{ .comb = .{ .kind = .allof, .branches = try b.copyNodeIds(&.{ n_int, n_five }) } });
    const root = try b.addNode(.{ .repeat = .{ .item = n_comb, .min = 0, .max = grammar.UNBOUNDED } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    try testing.expect(docValid(&g, &side, "[5,5]"));
    try testing.expect(docValid(&g, &side, "[]"));
    try testing.expect(docValid(&g, &side, "[5.0]"));
    try testing.expect(!docValid(&g, &side, "[5,6]"));
    try testing.expect(!docValid(&g, &side, "[6]"));
}

test "comb group resolution survives per-byte state hand-off (C ABI loop)" {
    const schema = @import("schema.zig");
    const alloc = testing.allocator;
    var diag: schema.Diagnostic = .{};
    var w = @import("work.zig").Work{};
    var g = try schema.compile(alloc, "{\"type\":\"array\",\"items\":{\"allOf\":[{\"type\":\"integer\"},{\"const\":5}]}}", 64, &diag, &w, false, .spec_v1, &.{});
    defer g.deinit();
    var side = Side.init(alloc);
    defer side.deinit();
    try testing.expect(docValid(&g, &side, "[5,5]"));
    try testing.expect(!docValid(&g, &side, "[5,6]"));
    // Per-byte round-trip through two ping-pong states (the C ABI session
    // loop): the group verdict is evaluated over the live sibling threads,
    // so the whole state must be fed together, one work state per byte.
    var sts: [2]State = .{
        try initState(&g, 8, &side),
        .{ .threads = undefined, .n = 0, .max_threads = 8 },
    };
    defer releaseState(&side, &sts[0]);
    defer releaseState(&side, &sts[1]);
    var spare: State = undefined;
    var cur: usize = 0;
    for ("[5,5]") |b| {
        releaseState(&side, &sts[cur ^ 1]);
        try feedBytes(&g, &side, &sts[cur], &[1]u8{b}, &sts[cur ^ 1], &spare);
        cur ^= 1;
    }
    try testing.expect(canEnd(&g, &sts[cur]));
    // Same hand-off, rejecting document: the group verdict (oneOf over
    // number/integer) must see both branches at the boundary byte.
    var w2 = @import("work.zig").Work{};
    var g2 = try schema.compile(alloc, "{\"type\":\"object\",\"properties\":{\"k\":{\"oneOf\":[{\"type\":\"number\"},{\"type\":\"integer\"}]}},\"required\":[\"k\"]}", 64, &diag, &w2, false, .spec_v1, &.{});
    defer g2.deinit();
    var sts2: [2]State = .{
        try initState(&g2, 8, &side),
        .{ .threads = undefined, .n = 0, .max_threads = 8 },
    };
    defer releaseState(&side, &sts2[0]);
    defer releaseState(&side, &sts2[1]);
    cur = 0;
    var alive = true;
    for ("{\"k\":1}") |b| {
        releaseState(&side, &sts2[cur ^ 1]);
        feedBytes(&g2, &side, &sts2[cur], &[1]u8{b}, &sts2[cur ^ 1], &spare) catch {
            alive = false;
            break;
        };
        cur ^= 1;
    }
    try testing.expect(!alive or !canEnd(&g2, &sts2[cur]));
}

test "spec-v1 AnyJSON arrays: converged duplicate threads are merged" {
    // Regression: an integer-valued element of an unconstrained array
    // (items: {}, anyOf: [{}]) completed on both the int_num and num_v
    // branches and the converged threads were never merged, doubling per
    // element until the thread budget rejected the 5th integer.
    const schema = @import("schema.zig");
    const alloc = testing.allocator;
    var diag: schema.Diagnostic = .{};
    var side = Side.init(alloc);
    defer side.deinit();
    const schemas = [_][]const u8{
        "{\"items\":{}}",
        "{\"items\":{\"anyOf\":[{}]}}",
        "{\"items\":{},\"uniqueItems\":true}",
    };
    const docs = [_][]const u8{
        "[1,2,3,4,5,6,7,8,9,10,11,12]",
        "[\"a\",\"b\",\"c\",\"d\",\"e\",\"f\",\"g\",\"h\"]",
        "[1,\"a\",2,\"b\",3,\"c\",4,\"d\",5,\"e\",6]",
        "[[1,2,3,4,5,6],[7,8,9,10,11,12]]",
    };
    for (schemas) |s| {
        var w = @import("work.zig").Work{};
        var g = try schema.compile(alloc, s, 64, &diag, &w, false, .spec_v1, &.{});
        defer g.deinit();
        // max_threads 8 (not the C ABI default 64): the merge keeps the
        // steady state at one thread per unconstrained element, so even a
        // small budget must now suffice.
        for (docs) |d| try testing.expect(docValid(&g, &side, d));
    }
    // The merge must not weaken the constraints the duplicates enforced.
    var w = @import("work.zig").Work{};
    var g = try schema.compile(alloc, "{\"items\":{},\"uniqueItems\":true}", 64, &diag, &w, false, .spec_v1, &.{});
    defer g.deinit();
    try testing.expect(!docValid(&g, &side, "[1,1.0,2,3,4,5]"));
    try testing.expect(!docValid(&g, &side, "[1,\"a\",1,\"b\",2,3]"));
    var w2 = @import("work.zig").Work{};
    var g2 = try schema.compile(alloc, "{\"items\":{},\"maxItems\":5}", 64, &diag, &w2, false, .spec_v1, &.{});
    defer g2.deinit();
    try testing.expect(docValid(&g2, &side, "[1,2,3,4,5]"));
    try testing.expect(!docValid(&g2, &side, "[1,2,3,4,5,6]"));
}

// ---- spec-v1 P4: regex-constrained strings (str_pat) ----

fn strPatGrammar(a: std.mem.Allocator, pat: []const u8, min_len: u32, max_len: u32) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const dfa = try aa.create(pattern.Dfa);
    dfa.* = try pattern.compileJsonSchema(aa, pat);
    const root = try b.addNode(.{ .str_pat = .{ .min_len = min_len, .max_len = max_len, .dfa = dfa } });
    return b.finish(arena, .json_schema, root, .{});
}

test "str_pat: regex-constrained string machine" {
    const alloc = testing.allocator;
    var side = Side.init(alloc);
    defer side.deinit();
    {
        // Anchored: the whole value must be a+.
        var g = try strPatGrammar(alloc, "^a+$", 0, grammar.UNBOUNDED);
        defer g.deinit();
        const Case = struct { doc: []const u8, ok: bool };
        const cases = [_]Case{
            .{ .doc = "\"a\"", .ok = true },
            .{ .doc = "\"aaa\"", .ok = true },
            .{ .doc = "\"\"", .ok = false },
            .{ .doc = "\"ab\"", .ok = false },
            .{ .doc = "\"ba\"", .ok = false },
        };
        for (cases) |c| try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
    }
    {
        // Unanchored search: the pattern may match anywhere.
        var g = try strPatGrammar(alloc, "a.c", 0, grammar.UNBOUNDED);
        defer g.deinit();
        const Case = struct { doc: []const u8, ok: bool };
        const cases = [_]Case{
            .{ .doc = "\"xxayczz\"", .ok = true },
            .{ .doc = "\"abc\"", .ok = true },
            .{ .doc = "\"ac\"", .ok = false },
            .{ .doc = "\"ab\"", .ok = false },
            // '.' excludes line terminators: an escaped newline is not '.'.
            .{ .doc = "\"a\\nc\"", .ok = false },
        };
        for (cases) |c| try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
    }
    {
        // The DFA sees the DECODED value: \n feeds U+000A, \\ feeds 0x5C.
        var g = try strPatGrammar(alloc, "^a\\nb$", 0, grammar.UNBOUNDED);
        defer g.deinit();
        try testing.expect(docValid(&g, &side, "\"a\\nb\""));
        try testing.expect(!docValid(&g, &side, "\"anb\""));
        var g2 = try strPatGrammar(alloc, "^x\\\\y$", 0, grammar.UNBOUNDED); // ^x\\y$ matches x\y
        defer g2.deinit();
        try testing.expect(docValid(&g2, &side, "\"x\\\\y\""));
        try testing.expect(!docValid(&g2, &side, "\"xy\""));
    }
    {
        // Non-ASCII codepoints: multi-byte UTF-8 feeds one scalar value.
        var g = try strPatGrammar(alloc, "^\xc3\xa9.$", 0, grammar.UNBOUNDED); // ^é.$
        defer g.deinit();
        try testing.expect(docValid(&g, &side, "\"\xc3\xa9x\""));
        try testing.expect(!docValid(&g, &side, "\"\xc3\xa9\""));
        try testing.expect(!docValid(&g, &side, "\"ex\""));
        // \u00XX escapes feed their decoded control codepoint.
        var g2 = try strPatGrammar(alloc, "^a\\u0001$", 0, grammar.UNBOUNDED);
        defer g2.deinit();
        try testing.expect(docValid(&g2, &side, "\"a\\u0001\""));
        try testing.expect(!docValid(&g2, &side, "\"a\\u0002\""));
    }
    {
        // Length bounds conjoin with the pattern (codepoint-counted).
        var g = try strPatGrammar(alloc, "^a+$", 2, 3);
        defer g.deinit();
        try testing.expect(docValid(&g, &side, "\"aa\""));
        try testing.expect(docValid(&g, &side, "\"aaa\""));
        try testing.expect(!docValid(&g, &side, "\"a\""));
        try testing.expect(!docValid(&g, &side, "\"aaaa\""));
    }
}

test "str_pat: pattern failure is an exact boundary reject (ADR-0005)" {
    const alloc = testing.allocator;
    var side = Side.init(alloc);
    defer side.deinit();
    var g = try strPatGrammar(alloc, "^ab", 0, grammar.UNBOUNDED);
    defer g.deinit();
    // Search-mode wrapping keeps a live (non-accepting) DFA state for every
    // content byte, so a mismatching string never hangs mid-value: it feeds
    // to the closing quote and is refused there exactly (the str minLength
    // shape). The verdict is exact; the content mask is conservative.
    var st = try initState(&g, 8, &side);
    defer releaseState(&side, &st);
    try feedS(&g, &side, &st, "\"ax");
    try testing.expect(!canEnd(&g, &st)); // the closing quote is still due
    // The closing quote of a non-matching string refuses at the boundary.
    try testing.expectError(error.Parse, feedS(&g, &side, &st, "b\""));
    // A matching document accepts.
    var st2 = try initState(&g, 8, &side);
    defer releaseState(&side, &st2);
    try feedS(&g, &side, &st2, "\"ab\"");
    try testing.expect(canEnd(&g, &st2));
    try testing.expect(!docValid(&g, &side, "\"axb\""));
    try testing.expect(!docValid(&g, &side, "\"xab\"")); // anchored at the start
}

test "str_pat: comb boundary rejection and sweep" {
    const alloc = testing.allocator;
    var side = Side.init(alloc);
    defer side.deinit();
    const dfa_a = try testing.allocator.create(pattern.Dfa);
    defer testing.allocator.destroy(dfa_a);
    dfa_a.* = try pattern.compileJsonSchema(testing.allocator, "^a+$");
    defer dfa_a.deinit();
    const dfa_b = try testing.allocator.create(pattern.Dfa);
    defer testing.allocator.destroy(dfa_b);
    dfa_b.* = try pattern.compileJsonSchema(testing.allocator, "^b+$");
    defer dfa_b.deinit();
    {
        // oneOf[^a+$, ^b+$]: the verdict is data-dependent; a branch that
        // completes but mismatches is refused at the boundary and simply
        // does not vote.
        var g = try combGrammar(alloc, .oneof, &.{
            .{ .str_pat = .{ .min_len = 0, .max_len = grammar.UNBOUNDED, .dfa = dfa_a } },
            .{ .str_pat = .{ .min_len = 0, .max_len = grammar.UNBOUNDED, .dfa = dfa_b } },
        }, &.{ 0, 1 });
        defer g.deinit();
        try testing.expect(docValid(&g, &side, "\"aaa\""));
        try testing.expect(docValid(&g, &side, "\"bbb\""));
        try testing.expect(!docValid(&g, &side, "\"ab\""));
        try testing.expect(!docValid(&g, &side, "\"\""));
    }
    {
        // allOf[a, b] (unanchored): the sweep kills the group the moment
        // one branch's DFA dies mid-string.
        const dfa_sa = try testing.allocator.create(pattern.Dfa);
        defer testing.allocator.destroy(dfa_sa);
        dfa_sa.* = try pattern.compileJsonSchema(testing.allocator, "a");
        defer dfa_sa.deinit();
        const dfa_sb = try testing.allocator.create(pattern.Dfa);
        defer testing.allocator.destroy(dfa_sb);
        dfa_sb.* = try pattern.compileJsonSchema(testing.allocator, "b");
        defer dfa_sb.deinit();
        var g = try combGrammar(alloc, .allof, &.{
            .{ .str_pat = .{ .min_len = 0, .max_len = grammar.UNBOUNDED, .dfa = dfa_sa } },
            .{ .str_pat = .{ .min_len = 0, .max_len = grammar.UNBOUNDED, .dfa = dfa_sb } },
        }, &.{ 0, 1 });
        defer g.deinit();
        try testing.expect(docValid(&g, &side, "\"xabx\""));
        try testing.expect(!docValid(&g, &side, "\"ax\""));
    }
}

test "str_pat: end-to-end through schema.compile" {
    const alloc = testing.allocator;
    const schema = @import("schema.zig");
    var diag: schema.Diagnostic = .{};
    var side = Side.init(alloc);
    defer side.deinit();
    var w = @import("work.zig").Work{};
    var g = try schema.compile(alloc, "{\"type\":\"object\",\"properties\":{\"k\":{\"type\":\"string\",\"pattern\":\"^a+$\"},\"e\":{\"enum\":[\"ab\",\"bbc\",\"dd\"],\"pattern\":\"b$\"}},\"required\":[\"k\",\"e\"],\"additionalProperties\":false}", 64, &diag, &w, false, .spec_v1, &.{});
    defer g.deinit();
    const Case = struct { doc: []const u8, ok: bool };
    const cases = [_]Case{
        .{ .doc = "{\"k\":\"aaa\",\"e\":\"ab\"}", .ok = true },
        .{ .doc = "{\"k\":\"a\",\"e\":\"ab\"}", .ok = true },
        .{ .doc = "{\"k\":\"ab\",\"e\":\"ab\"}", .ok = false }, // pattern on property value
        .{ .doc = "{\"k\":\"a\",\"e\":\"bbc\"}", .ok = false }, // pattern filters the enum (ends with b)
        .{ .doc = "{\"k\":\"a\",\"e\":\"dd\"}", .ok = false },
    };
    for (cases) |c| try testing.expectEqual(c.ok, docValid(&g, &side, c.doc));
}
