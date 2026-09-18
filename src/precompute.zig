// Experimental compile-time precompute (FR-9): bounded BFS over parser
// states reachable from the initial state, computing and caching masks
// ahead of time. Full enumeration is not required; exhausting the state
// budget, the temp memory or the work budget simply stops the walk and
// generation continues lazily. Only an explicit cancel signal propagates.

const std = @import("std");
const grammar = @import("grammar.zig");
const tokenizer = @import("tokenizer.zig");
const parser = @import("parser.zig");
const mask = @import("mask.zig");
const cache = @import("cache.zig");
const work_mod = @import("work.zig");

pub const Error = error{Cancelled};

/// Returns the number of states whose masks were computed and offered to
/// the cache. `mu` serializes access to `c` (cache is not internally
/// synchronized). Work is charged to `w` together with the rest of compile.
pub fn run(
    tmp_a: std.mem.Allocator,
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    c: *cache.Cache,
    mu: *std.Thread.Mutex,
    max_threads: u16,
    max_states: u64,
    w: *work_mod.Work,
) Error!u64 {
    var arena = std.heap.ArenaAllocator.init(tmp_a);
    defer arena.deinit();
    const a = arena.allocator();

    const mask_buf = a.alloc(u32, tok.maskWords()) catch return 0;
    var buf = mask.MaskBuf.init(a);

    var visited: std.AutoHashMapUnmanaged(u64, void) = .{};
    var queue: std.ArrayListUnmanaged(parser.State) = .{};

    const st0 = parser.initState(g, max_threads) catch return 0;
    queue.append(a, st0) catch return 0;
    visited.put(a, parser.hashState(&st0), {}) catch return 0;

    var count: u64 = 0;
    var qi: usize = 0;
    walk: while (qi < queue.items.len and count < max_states) : (qi += 1) {
        w.charge(1) catch |e| switch (e) {
            error.Cancelled => return error.Cancelled,
            error.ResourceLimit => break,
        };
        const cur = queue.items[qi];
        mask.fillMask(g, tok, &cur, mask_buf, w, &buf) catch |e| switch (e) {
            error.Cancelled => return error.Cancelled,
            error.DeadEnd => continue, // unreachable for covered grammars
            error.ResourceLimit, error.OutOfMemory => break,
        };
        count += 1;
        const key: cache.Key = .{
            .grammar_id = g.id,
            .grammar_hi = g.id_hi,
            .tokenizer_id = tok.identity,
            .state_hash = parser.hashState(&cur),
        };
        mu.lock();
        c.put(key, &cur, mask_buf);
        mu.unlock();
        var id: u32 = 0;
        while (id < tok.vocab_size) : (id += 1) {
            if ((mask_buf[id / 32] >> @as(u5, @intCast(id % 32))) & 1 == 0) continue;
            if (tok.is_eos[id]) continue;
            var next: parser.State = undefined;
            parser.feedBytes(g, &cur, tok.bytes[id], &next) catch continue;
            const h = parser.hashState(&next);
            const gop = visited.getOrPut(a, h) catch break :walk;
            if (gop.found_existing) continue;
            queue.append(a, next) catch break :walk;
        }
    }
    return count;
}
