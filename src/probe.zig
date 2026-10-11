//! Compile-time generatability probe (ADR-0012): a bounded breadth-first
//! search over the parser states reachable by tokens the mask allows,
//! computing the mask of every visited state. A state whose mask fails
//! with ResourceLimit (an undecided reachability verdict, or a runtime
//! limit of the parser) is a counterexample: generation that reaches it
//! fails. The search ends `proven` only when every reachable state was
//! visited (the graph of exact state images is finite and closed) without
//! one; a spent budget is `inconclusive`, which guarantees nothing.

const std = @import("std");
const Arena = @import("alloc.zig").Arena;
const grammar = @import("grammar.zig");
const parser = @import("parser.zig");
const mask = @import("mask.zig");
const complete = @import("complete.zig");
const tokenizer = @import("tokenizer.zig");
const work_mod = @import("work.zig");

pub const Status = enum(u32) { not_run = 0, inconclusive = 1, proven = 2 };

pub const Found = struct {
    /// Bytes of the token path to the state whose mask fails.
    prefix: []const u8,
    /// Node of the undecided state's first thread top frame, when known.
    node: ?grammar.NodeId,
};

pub const Outcome = union(enum) {
    inconclusive,
    proven,
    found: Found,
};

pub const Limits = struct {
    max_states: u64,
    /// Work budget of the probe itself (0 = unlimited); it does not draw
    /// on the compile call's work_limit_ops.
    max_work: u64,
};

pub const Error = error{ Cancelled, OutOfMemory };

/// `a` holds the visited set and the frontier (temporary); `out_a`
/// receives the counterexample prefix.
pub fn run(
    a: std.mem.Allocator,
    out_a: std.mem.Allocator,
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    max_threads: u16,
    lim: Limits,
    cancel: ?*align(1) const u8,
) Error!Outcome {
    var arena = Arena.init(a);
    defer arena.deinit();
    const aa = arena.allocator();
    var side = parser.Side.init(aa);
    defer side.deinit();
    var w = work_mod.Work.init(lim.max_work, cancel);

    const mask_buf = try aa.alloc(u32, tok.maskWords());
    var buf = mask.MaskBuf.init(aa, aa);
    const st0 = try aa.create(parser.State);
    const cur = try aa.create(parser.State);
    const next = try aa.create(parser.State);
    const scratch = try aa.create(parser.State);

    var visited: std.StringHashMapUnmanaged(void) = .{};
    var queue: std.ArrayListUnmanaged([]const u8) = .empty;
    st0.* = parser.initState(g, max_threads, &side) catch return .inconclusive;
    try visited.put(aa, try imageOf(aa, st0, &side), {});
    try queue.append(aa, "");

    var complete_walk = true;
    var qi: usize = 0;
    while (qi < queue.items.len) : (qi += 1) {
        if (qi >= lim.max_states) {
            complete_walk = false;
            break;
        }
        w.charge(1) catch |e| switch (e) {
            error.Cancelled => return error.Cancelled,
            error.ResourceLimit => {
                complete_walk = false;
                break;
            },
        };
        const prefix = queue.items[qi];
        parser.feedBytes(g, &side, st0, prefix, cur, scratch) catch {
            complete_walk = false;
            continue;
        };
        defer parser.releaseState(&side, cur);
        complete.last_unknown_node = null;
        mask.fillMask(g, tok, cur, mask_buf, &w, &buf, &side) catch |e| switch (e) {
            error.Cancelled => return error.Cancelled,
            error.DeadEnd => continue,
            error.OutOfMemory => {
                complete_walk = false;
                break;
            },
            error.ResourceLimit => {
                // A spent probe budget is not a counterexample.
                if (lim.max_work != 0 and w.ops > lim.max_work) {
                    complete_walk = false;
                    break;
                }
                return .{ .found = .{ .prefix = try out_a.dupe(u8, prefix), .node = complete.last_unknown_node } };
            },
        };
        var id: u32 = 0;
        while (id < tok.vocab_size) : (id += 1) {
            if ((mask_buf[id / 32] >> @as(u5, @intCast(id % 32))) & 1 == 0) continue;
            if (tok.is_eos[id]) continue;
            const bytes = tok.bytes[id];
            parser.feedBytes(g, &side, cur, bytes, next, scratch) catch |e| switch (e) {
                error.Parse => continue,
                error.OutOfMemory => return error.OutOfMemory,
                // The thread cap overflows on an allowed token: accept fails
                // there at generation time.
                error.ResourceLimit => {
                    const p = try out_a.alloc(u8, prefix.len + bytes.len);
                    @memcpy(p[0..prefix.len], prefix);
                    @memcpy(p[prefix.len..], bytes);
                    return .{ .found = .{ .prefix = p, .node = null } };
                },
            };
            defer parser.releaseState(&side, next);
            const img = try imageOf(aa, next, &side);
            const gop = try visited.getOrPut(aa, img);
            if (gop.found_existing) continue;
            const p = try aa.alloc(u8, prefix.len + bytes.len);
            @memcpy(p[0..prefix.len], prefix);
            @memcpy(p[prefix.len..], bytes);
            try queue.append(aa, p);
        }
    }
    return if (complete_walk and qi == queue.items.len) .proven else .inconclusive;
}

/// Exact image of a state: the frames (parser.writeStateKey) and the
/// referenced side-store chunks (parser.writeChunkBlob). Equal images are
/// equal states; equal states with differently numbered chunks get
/// different images, which only costs extra visits.
fn imageOf(a: std.mem.Allocator, st: *const parser.State, side: *const parser.Side) error{OutOfMemory}![]const u8 {
    const kl = parser.stateKeyLen(st);
    const bl = parser.chunkBlobLen(st, side);
    const out = try a.alloc(u8, kl + bl);
    parser.writeStateKey(st, out[0..kl]);
    parser.writeChunkBlob(st, side, out[kl..]);
    return out;
}
