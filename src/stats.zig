const std = @import("std");
const alloc = @import("alloc.zig");

pub const MEM_TOKENIZER: usize = 0;
pub const MEM_GRAMMAR: usize = 1;
pub const MEM_SESSION: usize = 2;
pub const MEM_CACHE: usize = 3;
pub const MEM_TEMP: usize = 4;
pub const MEM_TOTAL: usize = 5;

pub const Stats = struct {
    compile_ns: u64 = 0,
    tokenizer_prepare_ns: u64 = 0,
    accept_ns_total: u64 = 0,
    mask_ns_total: u64 = 0,
    mask_calls: u64 = 0,
    tokens_accepted: u64 = 0,
    cache_hits: u64 = 0,
    cache_misses: u64 = 0,
    cache_evictions: u64 = 0,
    errors_total: u64 = 0,
    errors_resource_limit: u64 = 0,
    errors_cancelled: u64 = 0,
    cache_adaptive_skips: u64 = 0,
    precompute_states: u64 = 0,
    work_ops_total: u64 = 0,

    pub fn recordError(self: *Stats, resource_limit: bool, cancelled: bool) void {
        self.errors_total += 1;
        if (resource_limit) self.errors_resource_limit += 1;
        if (cancelled) self.errors_cancelled += 1;
    }

    pub fn recordAdaptiveSkip(self: *Stats) void {
        self.cache_adaptive_skips += 1;
    }

    pub fn addPrecomputeStates(self: *Stats, n: u64) void {
        self.precompute_states +|= n;
    }

    pub fn addWorkOps(self: *Stats, n: u64) void {
        self.work_ops_total +|= n;
    }

    pub fn addCompileNs(self: *Stats, ns: u64) void {
        self.compile_ns +|= ns;
    }

    pub fn addTokenizerPrepareNs(self: *Stats, ns: u64) void {
        self.tokenizer_prepare_ns +|= ns;
    }

    pub fn recordAccept(self: *Stats, ns: u64) void {
        self.accept_ns_total +|= ns;
        self.tokens_accepted += 1;
    }

    pub fn recordMaskCall(self: *Stats, ns: u64) void {
        self.mask_ns_total +|= ns;
        self.mask_calls += 1;
    }

    pub fn recordCacheHit(self: *Stats) void {
        self.cache_hits += 1;
    }

    pub fn recordCacheMiss(self: *Stats) void {
        self.cache_misses += 1;
    }

    pub fn recordCacheEviction(self: *Stats, n: u64) void {
        self.cache_evictions += n;
    }

    pub fn reset(self: *Stats) void {
        self.* = .{};
    }
};

/// Fills mem_used/mem_peak by blg_stats indices:
/// tokenizer=0, grammar=1, session=2, cache=3, temp=4, total=5.
pub fn fillMemory(acc: *const alloc.Accounting, mem_used: []u64, mem_peak: []u64) void {
    std.debug.assert(mem_used.len > MEM_TOTAL and mem_peak.len > MEM_TOTAL);
    inline for (0..alloc.category_count) |i| {
        const cat: alloc.Category = @enumFromInt(i);
        mem_used[i] = acc.used(cat);
        mem_peak[i] = acc.peak(cat);
    }
    mem_used[MEM_TOTAL] = acc.totalUsed();
    mem_peak[MEM_TOTAL] = acc.totalPeak();
}

test "stats: counters accumulate" {
    var s: Stats = .{};
    s.addCompileNs(100);
    s.addCompileNs(50);
    s.addTokenizerPrepareNs(7);
    s.recordAccept(10);
    s.recordAccept(20);
    s.recordMaskCall(5);
    s.recordCacheHit();
    s.recordCacheMiss();
    s.recordCacheMiss();
    s.recordCacheEviction(3);
    try std.testing.expectEqual(@as(u64, 150), s.compile_ns);
    try std.testing.expectEqual(@as(u64, 7), s.tokenizer_prepare_ns);
    try std.testing.expectEqual(@as(u64, 30), s.accept_ns_total);
    try std.testing.expectEqual(@as(u64, 2), s.tokens_accepted);
    try std.testing.expectEqual(@as(u64, 5), s.mask_ns_total);
    try std.testing.expectEqual(@as(u64, 1), s.mask_calls);
    try std.testing.expectEqual(@as(u64, 1), s.cache_hits);
    try std.testing.expectEqual(@as(u64, 2), s.cache_misses);
    try std.testing.expectEqual(@as(u64, 3), s.cache_evictions);
    s.reset();
    try std.testing.expectEqual(@as(u64, 0), s.compile_ns);
}

test "stats: fillMemory maps categories to indices" {
    var acc = alloc.Accounting.init(std.testing.allocator, 1 << 20);
    const tok = acc.allocator(.tokenizer);
    const cache_a = acc.allocator(.cache);
    const x = try tok.alloc(u8, 32);
    const y = try cache_a.alloc(u8, 16);
    defer tok.free(x);
    defer cache_a.free(y);

    var used: [8]u64 = .{0} ** 8;
    var peak: [8]u64 = .{0} ** 8;
    fillMemory(&acc, &used, &peak);
    try std.testing.expectEqual(acc.used(.tokenizer), used[MEM_TOKENIZER]);
    try std.testing.expectEqual(acc.used(.cache), used[MEM_CACHE]);
    try std.testing.expectEqual(@as(u64, 0), used[MEM_GRAMMAR]);
    try std.testing.expectEqual(acc.totalUsed(), used[MEM_TOTAL]);
    try std.testing.expectEqual(acc.peak(.tokenizer), peak[MEM_TOKENIZER]);
    try std.testing.expectEqual(acc.totalPeak(), peak[MEM_TOTAL]);
    try std.testing.expectEqual(used[MEM_TOKENIZER] + used[MEM_CACHE], used[MEM_TOTAL]);
}
