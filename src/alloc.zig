const std = @import("std");

pub const Category = enum(u8) { tokenizer, grammar, session, cache, temp };
pub const category_count = 5;

const Header = struct {
    size: u64,
    cat: Category,
};

const Layout = struct { header: usize, total: usize, alignment: std.mem.Alignment };

fn layoutFor(len: usize, alignment: std.mem.Alignment) ?Layout {
    const header_align = std.mem.Alignment.fromByteUnits(@alignOf(Header));
    const eff = if (alignment.order(header_align) == .gt) alignment else header_align;
    const header = std.mem.alignForward(usize, @sizeOf(Header), eff.toByteUnits());
    const total = std.math.add(usize, header, len) catch return null;
    return .{ .header = header, .total = total, .alignment = eff };
}

fn Impl(
    comptime Ctx: type,
    comptime chargeFn: fn (Ctx, u64) bool,
    comptime unchargeFn: fn (Ctx, u64) void,
    comptime backingFn: fn (Ctx) std.mem.Allocator,
) type {
    return struct {
        const vtable: std.mem.Allocator.VTable = .{
            .alloc = alloc,
            .resize = resize,
            .remap = std.mem.Allocator.noRemap,
            .free = free,
        };

        fn ctxOf(p: *anyopaque) Ctx {
            const c: *Ctx = @ptrCast(@alignCast(p));
            return c.*;
        }

        fn alloc(p: *anyopaque, len: usize, alignment: std.mem.Alignment, ret_addr: usize) ?[*]u8 {
            const ctx = ctxOf(p);
            const lay = layoutFor(len, alignment) orelse return null;
            if (!chargeFn(ctx, @intCast(lay.total))) return null;
            const raw = backingFn(ctx).rawAlloc(lay.total, lay.alignment, ret_addr) orelse {
                unchargeFn(ctx, @intCast(lay.total));
                return null;
            };
            const hdr: *Header = @ptrCast(@alignCast(raw));
            hdr.* = .{ .size = @intCast(lay.total), .cat = ctx.cat };
            return raw + lay.header;
        }

        fn resize(p: *anyopaque, memory: []u8, alignment: std.mem.Alignment, new_len: usize, ret_addr: usize) bool {
            const ctx = ctxOf(p);
            const old_lay = layoutFor(memory.len, alignment) orelse return false;
            const new_lay = layoutFor(new_len, alignment) orelse return false;
            const raw = memory.ptr - old_lay.header;
            const hdr: *Header = @ptrCast(@alignCast(raw));
            const old_total: usize = @intCast(hdr.size);
            if (new_lay.total > old_total) {
                const delta: u64 = @intCast(new_lay.total - old_total);
                if (!chargeFn(ctx, delta)) return false;
                if (!backingFn(ctx).rawResize(raw[0..old_total], old_lay.alignment, new_lay.total, ret_addr)) {
                    unchargeFn(ctx, delta);
                    return false;
                }
            } else {
                if (!backingFn(ctx).rawResize(raw[0..old_total], old_lay.alignment, new_lay.total, ret_addr))
                    return false;
                unchargeFn(ctx, @intCast(old_total - new_lay.total));
            }
            hdr.size = @intCast(new_lay.total);
            return true;
        }

        fn free(p: *anyopaque, memory: []u8, alignment: std.mem.Alignment, ret_addr: usize) void {
            const ctx = ctxOf(p);
            const lay = layoutFor(memory.len, alignment).?;
            const raw = memory.ptr - lay.header;
            const hdr: *Header = @ptrCast(@alignCast(raw));
            const total: usize = @intCast(hdr.size);
            unchargeFn(ctx, @intCast(total));
            backingFn(ctx).rawFree(raw[0..total], lay.alignment, ret_addr);
        }
    };
}

const AccCtx = struct { acc: *Accounting, cat: Category };

fn atomicMaxU64(a: *std.atomic.Value(u64), v: u64) void {
    var cur = a.load(.monotonic);
    while (cur < v) {
        cur = a.cmpxchgWeak(cur, v, .monotonic, .monotonic) orelse return;
    }
}

/// CAS-based charging against a limit: the counter never exceeds `limit`;
/// a failed CAS simply retries. No false refusals.
fn chargeCas(counter: *std.atomic.Value(u64), limit: u64, size: u64) bool {
    while (true) {
        const used = counter.load(.monotonic);
        if (size > limit -| used) return false;
        if (counter.cmpxchgWeak(used, used + size, .acq_rel, .monotonic) == null) return true;
    }
}

// Counters are atomic: compile/session create/destroy run from multiple
// threads without external locking (the zg_context mutex does not cover
// them), and FR-10 requires exact accounting under any thread count.

fn accCharge(ctx: AccCtx, size: u64) bool {
    const acc = ctx.acc;
    if (!chargeCas(&acc.total_used, acc.total_limit, size)) return false;
    const i = @intFromEnum(ctx.cat);
    const cat_used = acc.used_arr[i].fetchAdd(size, .monotonic) + size;
    atomicMaxU64(&acc.peak_arr[i], cat_used);
    atomicMaxU64(&acc.total_peak, acc.total_used.load(.monotonic));
    return true;
}

fn accUncharge(ctx: AccCtx, size: u64) void {
    const acc = ctx.acc;
    _ = acc.total_used.fetchSub(size, .monotonic);
    _ = acc.used_arr[@intFromEnum(ctx.cat)].fetchSub(size, .monotonic);
}

fn accBacking(ctx: AccCtx) std.mem.Allocator {
    return ctx.acc.parent;
}

const AccImpl = Impl(AccCtx, accCharge, accUncharge, accBacking);

const SessCtx = struct { sess: *SessionAccount, cat: Category };

fn sessCharge(ctx: SessCtx, size: u64) bool {
    const sess = ctx.sess;
    const acc = sess.parent;
    if (!chargeCas(&sess.used_bytes, sess.limit, size)) return false;
    if (!chargeCas(&acc.total_used, acc.total_limit, size)) {
        _ = sess.used_bytes.fetchSub(size, .monotonic);
        return false;
    }
    const i = @intFromEnum(Category.session);
    const cat_used = acc.used_arr[i].fetchAdd(size, .monotonic) + size;
    atomicMaxU64(&acc.peak_arr[i], cat_used);
    atomicMaxU64(&acc.total_peak, acc.total_used.load(.monotonic));
    return true;
}

fn sessUncharge(ctx: SessCtx, size: u64) void {
    const sess = ctx.sess;
    _ = sess.used_bytes.fetchSub(size, .monotonic);
    _ = sess.parent.total_used.fetchSub(size, .monotonic);
    _ = sess.parent.used_arr[@intFromEnum(Category.session)].fetchSub(size, .monotonic);
}

fn sessBacking(ctx: SessCtx) std.mem.Allocator {
    return ctx.sess.parent.parent;
}

const SessImpl = Impl(SessCtx, sessCharge, sessUncharge, sessBacking);

pub const Accounting = struct {
    parent: std.mem.Allocator,
    total_limit: u64,
    total_used: std.atomic.Value(u64),
    total_peak: std.atomic.Value(u64),
    used_arr: [category_count]std.atomic.Value(u64),
    peak_arr: [category_count]std.atomic.Value(u64),
    ctxs: [category_count]AccCtx = undefined,

    pub fn init(parent: std.mem.Allocator, total_limit: u64) Accounting {
        var self: Accounting = .{
            .parent = parent,
            .total_limit = total_limit,
            .total_used = std.atomic.Value(u64).init(0),
            .total_peak = std.atomic.Value(u64).init(0),
            .used_arr = undefined,
            .peak_arr = undefined,
        };
        for (0..category_count) |i| {
            self.used_arr[i] = std.atomic.Value(u64).init(0);
            self.peak_arr[i] = std.atomic.Value(u64).init(0);
        }
        return self;
    }

    pub fn allocator(self: *Accounting, cat: Category) std.mem.Allocator {
        const i = @intFromEnum(cat);
        self.ctxs[i] = .{ .acc = self, .cat = cat };
        return .{ .ptr = &self.ctxs[i], .vtable = &AccImpl.vtable };
    }

    /// Charge memory allocated outside the accounting allocators (e.g. the
    /// Context struct itself) so every kernel allocation counts (FR-10).
    pub fn chargeExternal(self: *Accounting, cat: Category, size: u64) bool {
        return accCharge(.{ .acc = self, .cat = cat }, size);
    }

    pub fn freeExternal(self: *Accounting, cat: Category, size: u64) void {
        accUncharge(.{ .acc = self, .cat = cat }, size);
    }

    pub fn used(self: *const Accounting, cat: Category) u64 {
        return self.used_arr[@intFromEnum(cat)].load(.monotonic);
    }

    pub fn peak(self: *const Accounting, cat: Category) u64 {
        return self.peak_arr[@intFromEnum(cat)].load(.monotonic);
    }

    pub fn totalUsed(self: *const Accounting) u64 {
        return self.total_used.load(.monotonic);
    }

    pub fn totalPeak(self: *const Accounting) u64 {
        return self.total_peak.load(.monotonic);
    }
};

const LimCtx = struct { lim: *Limited, cat: Category };

fn limCharge(ctx: LimCtx, size: u64) bool {
    return chargeCas(&ctx.lim.used, ctx.lim.limit, size);
}

fn limUncharge(ctx: LimCtx, size: u64) void {
    _ = ctx.lim.used.fetchSub(size, .monotonic);
}

fn limBacking(ctx: LimCtx) std.mem.Allocator {
    return ctx.lim.parent;
}

const LimImpl = Impl(LimCtx, limCharge, limUncharge, limBacking);

/// Hard-limit wrapper over any allocator: total bytes charged never exceed
/// `limit` (allocation fails instead). Used to give the mask cache a strict
/// byte budget that covers every allocation it makes, map growth included.
pub const Limited = struct {
    parent: std.mem.Allocator,
    limit: u64,
    used: std.atomic.Value(u64),
    ctx: LimCtx = undefined,

    pub fn init(parent: std.mem.Allocator, limit: u64) Limited {
        return .{ .parent = parent, .limit = limit, .used = std.atomic.Value(u64).init(0) };
    }

    pub fn allocator(self: *Limited, cat: Category) std.mem.Allocator {
        self.ctx = .{ .lim = self, .cat = cat };
        return .{ .ptr = &self.ctx, .vtable = &LimImpl.vtable };
    }

    pub fn usedBytes(self: *const Limited) u64 {
        return self.used.load(.monotonic);
    }
};

pub const SessionAccount = struct {
    parent: *Accounting,
    limit: u64,
    used_bytes: std.atomic.Value(u64),
    ctxs: [category_count]SessCtx = undefined,

    pub fn init(parent: *Accounting, limit: u64) SessionAccount {
        return .{ .parent = parent, .limit = limit, .used_bytes = std.atomic.Value(u64).init(0) };
    }

    pub fn allocator(self: *SessionAccount, cat: Category) std.mem.Allocator {
        const i = @intFromEnum(cat);
        self.ctxs[i] = .{ .sess = self, .cat = cat };
        return .{ .ptr = &self.ctxs[i], .vtable = &SessImpl.vtable };
    }

    pub fn usedBytes(self: *const SessionAccount) u64 {
        return self.used_bytes.load(.monotonic);
    }
};

test "accounting: alloc/free per category, used returns to zero" {
    var acc = Accounting.init(std.testing.allocator, 1 << 20);
    const tok = acc.allocator(.tokenizer);
    const cache_a = acc.allocator(.cache);

    const a = try tok.alloc(u8, 100);
    const b = try cache_a.alloc(u32, 16);
    try std.testing.expect(acc.used(.tokenizer) > 0);
    try std.testing.expect(acc.used(.cache) > 0);
    try std.testing.expectEqual(acc.used(.tokenizer) + acc.used(.cache), acc.totalUsed());
    try std.testing.expectEqual(@as(u64, 0), acc.used(.grammar));

    tok.free(a);
    try std.testing.expectEqual(@as(u64, 0), acc.used(.tokenizer));
    cache_a.free(b);
    try std.testing.expectEqual(@as(u64, 0), acc.totalUsed());
    try std.testing.expect(acc.peak(.cache) > 0);
}

test "accounting: peak tracks maximum" {
    var acc = Accounting.init(std.testing.allocator, 1 << 20);
    const a = acc.allocator(.temp);
    const x = try a.alloc(u8, 64);
    const peak1 = acc.peak(.temp);
    const y = try a.alloc(u8, 64);
    try std.testing.expect(acc.peak(.temp) > peak1);
    a.free(x);
    a.free(y);
    try std.testing.expectEqual(@as(u64, 0), acc.used(.temp));
    try std.testing.expect(acc.peak(.temp) > 0);
}

test "accounting: total limit refuses allocation" {
    var acc = Accounting.init(std.testing.allocator, 256);
    const a = acc.allocator(.grammar);
    const x = try a.alloc(u8, 128);
    try std.testing.expectError(error.OutOfMemory, a.alloc(u8, 128));
    a.free(x);
    try std.testing.expectEqual(@as(u64, 0), acc.totalUsed());
}

test "accounting: resize keeps counters consistent" {
    var acc = Accounting.init(std.testing.allocator, 1 << 20);
    const a = acc.allocator(.session);
    var buf = try a.alloc(u8, 32);
    const before = acc.used(.session);
    if (a.resize(buf, 64)) {
        buf = buf.ptr[0..64];
        try std.testing.expect(acc.used(.session) > before);
    }
    a.free(buf);
    try std.testing.expectEqual(@as(u64, 0), acc.totalUsed());
}

test "session account: own limit triggers before parent limit" {
    var acc = Accounting.init(std.testing.allocator, 1 << 20);
    var sess = SessionAccount.init(&acc, 128);
    const s = sess.allocator(.session);

    const x = try s.alloc(u8, 64);
    try std.testing.expect(sess.usedBytes() > 0);
    try std.testing.expectEqual(sess.usedBytes(), acc.totalUsed());
    try std.testing.expectError(error.OutOfMemory, s.alloc(u8, 64));
    s.free(x);
    try std.testing.expectEqual(@as(u64, 0), sess.usedBytes());
    try std.testing.expectEqual(@as(u64, 0), acc.totalUsed());
}

test "session account: parent limit also enforced for sessions" {
    var acc = Accounting.init(std.testing.allocator, 200);
    var sess = SessionAccount.init(&acc, 1 << 20);
    const s = sess.allocator(.session);
    const x = try s.alloc(u8, 100);
    try std.testing.expectError(error.OutOfMemory, s.alloc(u8, 100));
    s.free(x);
    try std.testing.expectEqual(@as(u64, 0), acc.totalUsed());
}

test "limited: hard cap on charged bytes" {
    var lim = Limited.init(std.testing.allocator, 128);
    const a = lim.allocator(.cache);
    const x = try a.alloc(u8, 64);
    const used1 = lim.usedBytes();
    try std.testing.expect(used1 >= 64);
    try std.testing.expect(used1 <= 128);
    try std.testing.expectError(error.OutOfMemory, a.alloc(u8, 128));
    a.free(x);
    try std.testing.expectEqual(@as(u64, 0), lim.usedBytes());
    const y = try a.alloc(u8, 96);
    try std.testing.expect(lim.usedBytes() <= 128);
    a.free(y);
    try std.testing.expectEqual(@as(u64, 0), lim.usedBytes());
}

test "accounting: header keeps category through raw interface" {
    var acc = Accounting.init(std.testing.allocator, 1 << 20);
    const a = acc.allocator(.cache);
    const Alignment = std.mem.Alignment;
    const p = std.mem.Allocator.rawAlloc(a, 40, .fromByteUnits(16), @returnAddress()) orelse
        return error.OutOfMemory;
    try std.testing.expect(acc.used(.cache) >= 40 + @sizeOf(Header));
    std.mem.Allocator.rawFree(a, p[0..40], Alignment.fromByteUnits(16), @returnAddress());
    try std.testing.expectEqual(@as(u64, 0), acc.used(.cache));
}
