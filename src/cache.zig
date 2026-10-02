const std = @import("std");

const parser = @import("parser.zig");
const alloc = @import("alloc.zig");

/// grammar_hi is a second, independent hash of the grammar source bytes
/// (see grammar.Identity). Residual risk: a simultaneous collision of both
/// 64-bit hashes is accepted (~2^-128); grammar bytes are not retained for
/// a full content comparison.
pub const Key = struct { grammar_id: u64, grammar_hi: u64, tokenizer_id: u64, state_hash: u64 };

/// Widened (equivalence-class) key of the ADR-0007 string-content class:
/// the lemma's descriptor, not the full state. r_capped is the residual
/// string-length budget capped at CP_MAX + 1; two uniform string-content
/// states with the same capped residual have the same content-class bits.
/// A widened entry stores ONLY its class's bits, never a full mask.
pub const WKey = struct { grammar_id: u64, grammar_hi: u64, tokenizer_id: u64, r_capped: u64 };

pub const WKeyContext = struct {
    pub fn hash(_: WKeyContext, k: WKey) u64 {
        var h = std.hash.Wyhash.init(0);
        h.update(std.mem.asBytes(&k.grammar_id));
        h.update(std.mem.asBytes(&k.grammar_hi));
        h.update(std.mem.asBytes(&k.tokenizer_id));
        h.update(std.mem.asBytes(&k.r_capped));
        return h.final();
    }
    pub fn eql(_: WKeyContext, a: WKey, b: WKey) bool {
        // Collision checking compares the full descriptor (ADR-0007 D2).
        return a.grammar_id == b.grammar_id and
            a.grammar_hi == b.grammar_hi and
            a.tokenizer_id == b.tokenizer_id and
            a.r_capped == b.r_capped;
    }
};

pub const KeyContext = struct {
    pub fn hash(_: KeyContext, k: Key) u64 {
        var h = std.hash.Wyhash.init(0);
        h.update(std.mem.asBytes(&k.grammar_id));
        h.update(std.mem.asBytes(&k.grammar_hi));
        h.update(std.mem.asBytes(&k.tokenizer_id));
        h.update(std.mem.asBytes(&k.state_hash));
        return h.final();
    }
    pub fn eql(_: KeyContext, a: Key, b: Key) bool {
        return a.grammar_id == b.grammar_id and
            a.grammar_hi == b.grammar_hi and
            a.tokenizer_id == b.tokenizer_id and
            a.state_hash == b.state_hash;
    }
};

/// eqlFn receives each state with its chunk-content blob (see
/// parser.chunkBlobLen/writeChunkBlob); chunk-free states carry an empty
/// blob and compare by spine bytes only.
pub fn MaskCache(comptime StateT: type, comptime eqlFn: fn (*const StateT, []const u8, *const StateT, []const u8) bool) type {
    return struct {
        const Self = @This();

        const Entry = struct {
            key: Key,
            state: *StateT,
            blob: []u8,
            mask: []u32,
            prev: ?*Entry = null,
            next: ?*Entry = null,
        };

        const Map = std.HashMapUnmanaged(Key, *Entry, KeyContext, std.hash_map.default_max_load_percentage);
        const SeenMap = std.HashMapUnmanaged(Key, u64, KeyContext, std.hash_map.default_max_load_percentage);

        // Widened entries (ADR-0007 D2): equivalence-class bits of the
        // string-content class, keyed by the lemma descriptor. They share
        // the same hard byte budget (`lim`) and LRU accounting as the exact
        // entries: all allocations go through the same limited allocator
        // and evictions are counted together. The LRU list is separate
        // because the entry payload differs.
        const WEntry = struct {
            key: WKey,
            mask: []u32,
            prev: ?*WEntry = null,
            next: ?*WEntry = null,
        };
        const WMap = std.HashMapUnmanaged(WKey, *WEntry, WKeyContext, std.hash_map.default_max_load_percentage);

        // Cheap pre-cache admission counters for the adaptive policy (FR-9):
        // how often a state had to be computed. Bounded and inside the same
        // hard budget as the entries themselves.
        const SEEN_MAX: u32 = 8192;

        // All entry/map allocations go through `lim`, so the budget is hard:
        // bytes charged never exceed it (FR-10). `a` is kept only for deinit
        // of the map backing storage via the same limited allocator.
        lim: alloc.Limited,
        budget: u64,
        map: Map = .{},
        seen: SeenMap = .{},
        head: ?*Entry = null,
        tail: ?*Entry = null,
        hits_n: u64 = 0,
        misses_n: u64 = 0,
        evictions_n: u64 = 0,
        wmap: WMap = .{},
        whead: ?*WEntry = null,
        wtail: ?*WEntry = null,
        w_hits_n: u64 = 0,
        w_misses_n: u64 = 0,

        pub fn init(a: std.mem.Allocator, byte_budget: u64) Self {
            return .{ .lim = alloc.Limited.init(a, byte_budget), .budget = byte_budget };
        }

        /// Like `init`, but the caller owns the limiter (e.g. a Limited that
        /// also charges the accounting category, so the hard budget and the
        /// global counters see the same full allocation cost).
        pub fn initLim(lim: alloc.Limited) Self {
            return .{ .lim = lim, .budget = lim.limit };
        }

        pub fn deinit(self: *Self) void {
            const la = self.limitedAlloc();
            var it = self.head;
            while (it) |e| {
                const next = e.next;
                self.destroyEntry(la, e);
                it = next;
            }
            var wit = self.whead;
            while (wit) |e| {
                const next = e.next;
                self.destroyWEntry(la, e);
                wit = next;
            }
            self.map.deinit(la);
            self.seen.deinit(la);
            self.wmap.deinit(la);
            self.* = undefined;
        }

        /// Counts one cache-miss access to `key` and returns the total.
        /// Returns 0 when the counter could not be tracked (budget off/full);
        /// callers must treat that as "rarely seen".
        pub fn bumpSeen(self: *Self, key: Key) u64 {
            if (self.budget == 0) return 0;
            if (self.seen.count() >= SEEN_MAX) self.seen.clearRetainingCapacity();
            const gop = self.seen.getOrPut(self.limitedAlloc(), key) catch return 0;
            if (!gop.found_existing) gop.value_ptr.* = 0;
            gop.value_ptr.* += 1;
            return gop.value_ptr.*;
        }

        pub fn usedBytes(self: *const Self) u64 {
            return self.lim.usedBytes();
        }

        pub fn get(self: *Self, key: Key, state: *const StateT, blob: []const u8, mask_out: []u32) bool {
            if (self.budget == 0) return false;
            const e = self.map.get(key) orelse {
                self.misses_n += 1;
                return false;
            };
            if (!eqlFn(state, blob, e.state, e.blob)) {
                self.misses_n += 1;
                return false;
            }
            std.debug.assert(mask_out.len >= e.mask.len);
            @memcpy(mask_out[0..e.mask.len], e.mask);
            self.moveToHead(e);
            self.hits_n += 1;
            return true;
        }

        pub fn put(self: *Self, key: Key, state: *const StateT, blob: []const u8, mask: []const u32) void {
            if (self.budget == 0) return;
            const la = self.limitedAlloc();
            if (self.map.get(key)) |old| {
                self.unlink(old);
                _ = self.map.remove(key);
                self.destroyEntry(la, old);
            }
            // On allocation failure evict the LRU tail and retry; give up
            // silently when nothing can be evicted (mask stays computable
            // via the lazy path).
            while (true) {
                const e = la.create(Entry) catch {
                    if (self.evictTail(la)) continue;
                    return;
                };
                const st = la.create(StateT) catch {
                    la.destroy(e);
                    if (self.evictTail(la)) continue;
                    return;
                };
                st.* = state.*;
                const bl = la.dupe(u8, blob) catch {
                    la.destroy(st);
                    la.destroy(e);
                    if (self.evictTail(la)) continue;
                    return;
                };
                const m = la.dupe(u32, mask) catch {
                    la.free(bl);
                    la.destroy(st);
                    la.destroy(e);
                    if (self.evictTail(la)) continue;
                    return;
                };
                e.* = .{ .key = key, .state = st, .blob = bl, .mask = m };
                self.map.put(la, key, e) catch {
                    la.free(m);
                    la.free(bl);
                    la.destroy(st);
                    la.destroy(e);
                    if (self.evictTail(la)) continue;
                    return;
                };
                self.linkHead(e);
                return;
            }
        }

        /// Widened lookup: copies the stored class bits into `out`.
        pub fn getWidened(self: *Self, key: WKey, out: []u32) bool {
            if (self.budget == 0) return false;
            const e = self.wmap.get(key) orelse {
                self.w_misses_n += 1;
                return false;
            };
            std.debug.assert(out.len >= e.mask.len);
            @memcpy(out[0..e.mask.len], e.mask);
            self.wMoveToHead(e);
            self.w_hits_n += 1;
            return true;
        }

        /// Widened store: the mask slice must carry only the class's bits.
        /// On allocation pressure the widened LRU tail is evicted first,
        /// then the exact LRU tail (same hard budget); the entry is dropped
        /// silently when nothing can be evicted.
        pub fn putWidened(self: *Self, key: WKey, mask_bits: []const u32) void {
            if (self.budget == 0) return;
            const la = self.limitedAlloc();
            if (self.wmap.get(key)) |old| {
                self.wUnlink(old);
                _ = self.wmap.remove(key);
                self.destroyWEntry(la, old);
            }
            while (true) {
                const e = la.create(WEntry) catch {
                    if (self.evictAny(la)) continue;
                    return;
                };
                const m = la.dupe(u32, mask_bits) catch {
                    la.destroy(e);
                    if (self.evictAny(la)) continue;
                    return;
                };
                e.* = .{ .key = key, .mask = m };
                self.wmap.put(la, key, e) catch {
                    la.free(m);
                    la.destroy(e);
                    if (self.evictAny(la)) continue;
                    return;
                };
                self.wLinkHead(e);
                return;
            }
        }

        pub fn widenedHits(self: *const Self) u64 {
            return self.w_hits_n;
        }

        pub fn widenedMisses(self: *const Self) u64 {
            return self.w_misses_n;
        }

        pub fn hits(self: *const Self) u64 {
            return self.hits_n;
        }

        pub fn misses(self: *const Self) u64 {
            return self.misses_n;
        }

        pub fn evictions(self: *const Self) u64 {
            return self.evictions_n;
        }

        fn limitedAlloc(self: *Self) std.mem.Allocator {
            return self.lim.allocator(.cache);
        }

        fn evictTail(self: *Self, la: std.mem.Allocator) bool {
            const victim = self.tail orelse return false;
            self.unlink(victim);
            _ = self.map.remove(victim.key);
            self.destroyEntry(la, victim);
            self.evictions_n += 1;
            return true;
        }

        // Eviction pressure relief for widened puts: widened tail first
        // (its bits are recomputable in O(vocab) word ops), then the exact
        // tail. One shared counter: the LRU accounting is common (D2).
        fn evictAny(self: *Self, la: std.mem.Allocator) bool {
            if (self.wtail) |victim| {
                self.wUnlink(victim);
                _ = self.wmap.remove(victim.key);
                self.destroyWEntry(la, victim);
                self.evictions_n += 1;
                return true;
            }
            return self.evictTail(la);
        }

        fn destroyWEntry(self: *Self, la: std.mem.Allocator, e: *WEntry) void {
            _ = self;
            la.free(e.mask);
            la.destroy(e);
        }

        fn wUnlink(self: *Self, e: *WEntry) void {
            if (e.prev) |p| p.next = e.next else self.whead = e.next;
            if (e.next) |n| n.prev = e.prev else self.wtail = e.prev;
            e.prev = null;
            e.next = null;
        }

        fn wLinkHead(self: *Self, e: *WEntry) void {
            e.prev = null;
            e.next = self.whead;
            if (self.whead) |h| h.prev = e else self.wtail = e;
            self.whead = e;
        }

        fn wMoveToHead(self: *Self, e: *WEntry) void {
            if (self.whead == e) return;
            self.wUnlink(e);
            self.wLinkHead(e);
        }

        fn destroyEntry(self: *Self, la: std.mem.Allocator, e: *Entry) void {
            _ = self;
            la.free(e.mask);
            la.free(e.blob);
            la.destroy(e.state);
            la.destroy(e);
        }

        fn unlink(self: *Self, e: *Entry) void {
            if (e.prev) |p| p.next = e.next else self.head = e.next;
            if (e.next) |n| n.prev = e.prev else self.tail = e.prev;
            e.prev = null;
            e.next = null;
        }

        fn linkHead(self: *Self, e: *Entry) void {
            e.prev = null;
            e.next = self.head;
            if (self.head) |h| h.prev = e else self.tail = e;
            self.head = e;
        }

        fn moveToHead(self: *Self, e: *Entry) void {
            if (self.head == e) return;
            self.unlink(e);
            self.linkHead(e);
        }
    };
}

pub const Cache = MaskCache(parser.State, parser.eqlStatesBlobs);

const TestState = struct { tag: u64, value: u64 };

fn testEql(a: *const TestState, _: []const u8, b: *const TestState, _: []const u8) bool {
    return a.tag == b.tag and a.value == b.value;
}

const TestCache = MaskCache(TestState, testEql);

fn testKey(id: u64) Key {
    return .{ .grammar_id = 1, .grammar_hi = 11, .tokenizer_id = 2, .state_hash = id };
}

test "cache: miss then hit, mask copied, counters" {
    var c = TestCache.init(std.testing.allocator, 4096);
    defer c.deinit();

    const st: TestState = .{ .tag = 1, .value = 42 };
    const mask = [_]u32{ 0xdead, 0xbeef, 0x1234 };
    var out = [_]u32{ 0, 0, 0, 0xaaaa };

    try std.testing.expect(!c.get(testKey(7), &st, "", &out));
    try std.testing.expectEqual(@as(u64, 1), c.misses());

    c.put(testKey(7), &st, "", &mask);
    try std.testing.expect(c.get(testKey(7), &st, "", &out));
    try std.testing.expectEqual(@as(u64, 1), c.hits());
    try std.testing.expectEqualSlices(u32, &mask, out[0..3]);
    try std.testing.expectEqual(@as(u32, 0xaaaa), out[3]);
}

test "cache: same key with different state is a miss (hash collision guard)" {
    var c = TestCache.init(std.testing.allocator, 4096);
    defer c.deinit();

    const st_a: TestState = .{ .tag = 1, .value = 1 };
    const st_b: TestState = .{ .tag = 1, .value = 2 };
    const mask = [_]u32{5};
    var out = [_]u32{0};

    c.put(testKey(9), &st_a, "", &mask);
    try std.testing.expect(!c.get(testKey(9), &st_b, "", &out));
    try std.testing.expectEqual(@as(u64, 1), c.misses());
    try std.testing.expect(c.get(testKey(9), &st_a, "", &out));
    try std.testing.expectEqual(@as(u32, 5), out[0]);
}

// Bytes actually charged for a cache holding `n` distinct entries, measured
// with an effectively unbounded budget.
fn measuredUsage(n: u64) u64 {
    var c = TestCache.init(std.testing.allocator, 1 << 30);
    defer c.deinit();
    const st: TestState = .{ .tag = 1, .value = 1 };
    const mask = [_]u32{ 1, 2 };
    var i: u64 = 0;
    while (i < n) : (i += 1) c.put(testKey(i + 1), &st, "", &mask);
    return c.usedBytes();
}

test "cache: budget eviction from LRU tail" {
    const budget = measuredUsage(2);
    var c = TestCache.init(std.testing.allocator, budget);
    defer c.deinit();

    const st_a: TestState = .{ .tag = 1, .value = 1 };
    const st_b: TestState = .{ .tag = 2, .value = 2 };
    const st_c: TestState = .{ .tag = 3, .value = 3 };
    const mask = [_]u32{ 1, 2 };
    var out = [_]u32{ 0, 0 };

    c.put(testKey(1), &st_a, "", &mask);
    c.put(testKey(2), &st_b, "", &mask);
    try std.testing.expectEqual(@as(u64, 0), c.evictions());
    c.put(testKey(3), &st_c, "", &mask);
    try std.testing.expectEqual(@as(u64, 1), c.evictions());
    try std.testing.expect(c.usedBytes() <= budget);

    try std.testing.expect(!c.get(testKey(1), &st_a, "", &out));
    try std.testing.expect(c.get(testKey(2), &st_b, "", &out));
    try std.testing.expect(c.get(testKey(3), &st_c, "", &out));
}

test "cache: get refreshes LRU order" {
    const budget = measuredUsage(2);
    var c = TestCache.init(std.testing.allocator, budget);
    defer c.deinit();

    const st_a: TestState = .{ .tag = 1, .value = 1 };
    const st_b: TestState = .{ .tag = 2, .value = 2 };
    const st_c: TestState = .{ .tag = 3, .value = 3 };
    const mask = [_]u32{ 1, 2 };
    var out = [_]u32{ 0, 0 };

    c.put(testKey(1), &st_a, "", &mask);
    c.put(testKey(2), &st_b, "", &mask);
    try std.testing.expect(c.get(testKey(1), &st_a, "", &out));
    c.put(testKey(3), &st_c, "", &mask);
    try std.testing.expectEqual(@as(u64, 1), c.evictions());

    try std.testing.expect(c.get(testKey(1), &st_a, "", &out));
    try std.testing.expect(!c.get(testKey(2), &st_b, "", &out));
    try std.testing.expect(c.get(testKey(3), &st_c, "", &out));
}

test "cache: entry bigger than budget is not stored" {
    var c = TestCache.init(std.testing.allocator, 128);
    defer c.deinit();

    const st: TestState = .{ .tag = 1, .value = 1 };
    const mask = [_]u32{0} ** 64;
    var out = [_]u32{0} ** 64;

    c.put(testKey(1), &st, "", &mask);
    try std.testing.expect(!c.get(testKey(1), &st, "", &out));
    try std.testing.expectEqual(@as(u64, 1), c.misses());
    try std.testing.expectEqual(@as(u64, 0), c.evictions());
}

test "cache: budget 0 disables the cache" {
    var c = TestCache.init(std.testing.allocator, 0);
    defer c.deinit();

    const st: TestState = .{ .tag = 1, .value = 1 };
    const mask = [_]u32{1};
    var out = [_]u32{0};

    c.put(testKey(1), &st, "", &mask);
    try std.testing.expect(!c.get(testKey(1), &st, "", &out));
    try std.testing.expectEqual(@as(u64, 0), c.hits());
    try std.testing.expectEqual(@as(u64, 0), c.misses());
    try std.testing.expectEqual(@as(u64, 0), c.evictions());
}

test "cache: seen counters count misses per key, bounded and budget-aware" {
    var c = TestCache.init(std.testing.allocator, 4096);
    defer c.deinit();
    try std.testing.expectEqual(@as(u64, 1), c.bumpSeen(testKey(1)));
    try std.testing.expectEqual(@as(u64, 2), c.bumpSeen(testKey(1)));
    try std.testing.expectEqual(@as(u64, 1), c.bumpSeen(testKey(2)));

    var off = TestCache.init(std.testing.allocator, 0);
    defer off.deinit();
    try std.testing.expectEqual(@as(u64, 0), off.bumpSeen(testKey(1)));
}

test "cache: put with same key replaces entry without eviction" {
    const budget = measuredUsage(1);
    var c = TestCache.init(std.testing.allocator, budget);
    defer c.deinit();

    const st: TestState = .{ .tag = 1, .value = 1 };
    const mask1 = [_]u32{ 1, 2 };
    const mask2 = [_]u32{ 3, 2 };
    var out = [_]u32{ 0, 0 };

    c.put(testKey(1), &st, "", &mask1);
    c.put(testKey(1), &st, "", &mask2);
    try std.testing.expectEqual(@as(u64, 0), c.evictions());
    try std.testing.expect(c.get(testKey(1), &st, "", &out));
    try std.testing.expectEqual(@as(u32, 3), out[0]);
}

test "cache: stored mask is an owned copy" {
    var c = TestCache.init(std.testing.allocator, 4096);
    defer c.deinit();

    const st: TestState = .{ .tag = 1, .value = 1 };
    var mask = [_]u32{ 1, 2 };
    c.put(testKey(1), &st, "", &mask);
    mask[0] = 0xffff;

    var out = [_]u32{ 0, 0 };
    try std.testing.expect(c.get(testKey(1), &st, "", &out));
    try std.testing.expectEqual(@as(u32, 1), out[0]);
    try std.testing.expectEqual(@as(u32, 2), out[1]);
}

// Regression A5: under an allocator that fails on every allocation index in
// turn, put must free everything it allocated (the map.put failure path used
// to leak the mask copy).
test "cache: no leak under injected allocation failure" {
    const st: TestState = .{ .tag = 1, .value = 1 };
    const mask = [_]u32{7};
    var out = [_]u32{0};

    var k: usize = 0;
    var successes: usize = 0;
    while (k < 16) : (k += 1) {
        var fa = std.testing.FailingAllocator.init(std.testing.allocator, .{ .fail_index = k });
        var c = TestCache.init(fa.allocator(), 1 << 20);
        c.put(testKey(1), &st, "", &mask);
        const stored = c.get(testKey(1), &st, "", &out);
        c.deinit();
        try std.testing.expectEqual(fa.allocated_bytes, fa.freed_bytes);
        if (stored) {
            successes += 1;
            break;
        }
    }
    try std.testing.expectEqual(@as(usize, 1), successes);
}

// Regression: budget 82400 with a parser.State-sized entry
// must never be exceeded by the bytes actually charged.
test "cache: hard budget never exceeded" {
    const grammar = @import("grammar.zig");
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addLiteralNode("a");
    var g = try b.finish(arena, .literal_set, root, .{});
    defer g.deinit();
    var side = parser.Side.init(std.testing.allocator);
    defer side.deinit();
    var st = try parser.initState(&g, 4, &side);

    var c = Cache.init(std.testing.allocator, 82400);
    defer c.deinit();
    const mask = [_]u32{1};
    var out = [_]u32{0};
    const key: Key = .{ .grammar_id = 1, .grammar_hi = 1, .tokenizer_id = 1, .state_hash = 1 };
    c.put(key, &st, "", &mask);
    try std.testing.expect(c.usedBytes() <= 82400);
    // The entry does not fit: it must be absent rather than over budget.
    try std.testing.expect(!c.get(key, &st, "", &out));
    try std.testing.expect(c.usedBytes() <= 82400);
}

fn testWKey(r: u64) WKey {
    return .{ .grammar_id = 1, .grammar_hi = 11, .tokenizer_id = 2, .r_capped = r };
}

test "cache: widened put/get, full-descriptor equality, counters" {
    var c = TestCache.init(std.testing.allocator, 4096);
    defer c.deinit();
    const m = [_]u32{ 0b101, 0b111 };
    var out = [_]u32{ 0, 0 };

    try std.testing.expect(!c.getWidened(testWKey(5), &out));
    try std.testing.expectEqual(@as(u64, 1), c.widenedMisses());

    c.putWidened(testWKey(5), &m);
    try std.testing.expect(c.getWidened(testWKey(5), &out));
    try std.testing.expectEqual(@as(u64, 1), c.widenedHits());
    try std.testing.expectEqualSlices(u32, &m, &out);

    // Every descriptor field participates: same hash class, different
    // field -> miss (collision checking compares the full descriptor).
    var other = testWKey(5);
    other.r_capped = 6;
    try std.testing.expect(!c.getWidened(other, &out));
    other = testWKey(5);
    other.grammar_hi = 12;
    try std.testing.expect(!c.getWidened(other, &out));
    other = testWKey(5);
    other.tokenizer_id = 3;
    try std.testing.expect(!c.getWidened(other, &out));

    // Same key replaces in place.
    const m2 = [_]u32{ 0b010, 0b001 };
    c.putWidened(testWKey(5), &m2);
    try std.testing.expect(c.getWidened(testWKey(5), &out));
    try std.testing.expectEqualSlices(u32, &m2, &out);
}

test "cache: widened entries share the hard budget and LRU accounting" {
    // Budget fits exactly one widened entry of 2 words: measure it.
    var probe = TestCache.init(std.testing.allocator, 1 << 30);
    const m = [_]u32{ 1, 2 };
    probe.putWidened(testWKey(1), &m);
    const one = probe.usedBytes();
    probe.deinit();

    var c = TestCache.init(std.testing.allocator, one);
    defer c.deinit();
    var out = [_]u32{ 0, 0 };
    c.putWidened(testWKey(1), &m);
    c.putWidened(testWKey(2), &m); // evicts the first widened entry
    try std.testing.expectEqual(@as(u64, 1), c.evictions());
    try std.testing.expect(c.usedBytes() <= one);
    try std.testing.expect(!c.getWidened(testWKey(1), &out));
    try std.testing.expect(c.getWidened(testWKey(2), &out));

    // An over-budget widened put falls back to evicting the exact tail.
    var c2 = TestCache.init(std.testing.allocator, one);
    defer c2.deinit();
    const st: TestState = .{ .tag = 1, .value = 1 };
    c2.put(testKey(1), &st, "", &m);
    const big = [_]u32{0} ** 64;
    c2.putWidened(testWKey(9), &big); // does not fit even after evictions
    try std.testing.expect(c2.usedBytes() <= one);
    try std.testing.expect(!c2.getWidened(testWKey(9), @constCast(&big)));
}

test "cache: widened disabled at budget 0" {
    var c = TestCache.init(std.testing.allocator, 0);
    defer c.deinit();
    const m = [_]u32{1};
    var out = [_]u32{0};
    c.putWidened(testWKey(1), &m);
    try std.testing.expect(!c.getWidened(testWKey(1), &out));
    try std.testing.expectEqual(@as(u64, 0), c.widenedHits());
    try std.testing.expectEqual(@as(u64, 0), c.widenedMisses());
}
