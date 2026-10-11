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

/// Bytes charged by the accounting allocator for a request of length len
/// (header and alignment included). Mirror computation for the budget
/// counters, which must match the actual charge.
pub fn chargedBytes(len: usize, alignment: std.mem.Alignment) usize {
    const lay = layoutFor(len, alignment) orelse return std.math.maxInt(usize);
    return lay.total;
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
                // Growth past the pages a block spans succeeds only if the
                // address range after it is free; refusing it keeps charges
                // and peaks independent of the process address layout.
                const page = std.heap.pageSize();
                if (old_total >= page and
                    std.mem.alignForward(usize, new_lay.total, page) > std.mem.alignForward(usize, old_total, page))
                    return false;
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
// threads without external locking (the blg_context mutex does not cover
// them), and the accounting must stay exact under any thread count.

fn accCharge(ctx: AccCtx, size: u64) bool {
    const acc = ctx.acc;
    if (!chargeCas(&acc.total_used, acc.total_limit, size)) {
        return false;
    }
    const i = @backingInt(ctx.cat);
    const cat_used = acc.used_arr[i].fetchAdd(size, .monotonic) + size;
    atomicMaxU64(&acc.peak_arr[i], cat_used);
    atomicMaxU64(&acc.total_peak, acc.total_used.load(.monotonic));
    return true;
}

fn accUncharge(ctx: AccCtx, size: u64) void {
    const acc = ctx.acc;
    _ = acc.total_used.fetchSub(size, .monotonic);
    _ = acc.used_arr[@backingInt(ctx.cat)].fetchSub(size, .monotonic);
}

fn accBacking(ctx: AccCtx) std.mem.Allocator {
    return ctx.acc.parent;
}

const AccImpl = Impl(AccCtx, accCharge, accUncharge, accBacking);

const SessCtx = struct { sess: *SessionAccount, cat: Category };

fn sessCharge(ctx: SessCtx, size: u64) bool {
    const sess = ctx.sess;
    const acc = sess.parent;
    if (!chargeCas(&sess.used_bytes, sess.limit, size)) {
        return false;
    }
    if (!chargeCas(&acc.total_used, acc.total_limit, size)) {
        _ = sess.used_bytes.fetchSub(size, .monotonic);
        return false;
    }
    const i = @backingInt(Category.session);
    const cat_used = acc.used_arr[i].fetchAdd(size, .monotonic) + size;
    atomicMaxU64(&acc.peak_arr[i], cat_used);
    atomicMaxU64(&acc.total_peak, acc.total_used.load(.monotonic));
    return true;
}

fn sessUncharge(ctx: SessCtx, size: u64) void {
    const sess = ctx.sess;
    _ = sess.used_bytes.fetchSub(size, .monotonic);
    _ = sess.parent.total_used.fetchSub(size, .monotonic);
    _ = sess.parent.used_arr[@backingInt(Category.session)].fetchSub(size, .monotonic);
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
        const i = @backingInt(cat);
        self.ctxs[i] = .{ .acc = self, .cat = cat };
        return .{ .ptr = &self.ctxs[i], .vtable = &AccImpl.vtable };
    }

    /// Charge memory allocated outside the accounting allocators (e.g. the
    /// Context struct itself) so every kernel allocation counts.
    pub fn chargeExternal(self: *Accounting, cat: Category, size: u64) bool {
        return accCharge(.{ .acc = self, .cat = cat }, size);
    }

    pub fn freeExternal(self: *Accounting, cat: Category, size: u64) void {
        accUncharge(.{ .acc = self, .cat = cat }, size);
    }

    pub fn used(self: *const Accounting, cat: Category) u64 {
        return self.used_arr[@backingInt(cat)].load(.monotonic);
    }

    pub fn peak(self: *const Accounting, cat: Category) u64 {
        return self.peak_arr[@backingInt(cat)].load(.monotonic);
    }

    pub fn totalUsed(self: *const Accounting) u64 {
        return self.total_used.load(.monotonic);
    }

    pub fn totalPeak(self: *const Accounting) u64 {
        return self.total_peak.load(.monotonic);
    }
};

const LimCtx = struct { lim: *Limited, cat: Category };

// One allocation layer = one header: when the limited allocator is also the
// accounting layer for its category, the same full cost (header included)
// is charged to both the local limit and the global counters. Stacking a
// separate Accounting layer would add a second header that the local limit
// does not see.
fn limCharge(ctx: LimCtx, size: u64) bool {
    if (!chargeCas(&ctx.lim.used, ctx.lim.limit, size)) {
        return false;
    }
    if (ctx.lim.acc) |a| {
        if (!accCharge(.{ .acc = a, .cat = ctx.lim.acc_cat }, size)) {
            _ = ctx.lim.used.fetchSub(size, .monotonic);
            return false;
        }
    }
    return true;
}

fn limUncharge(ctx: LimCtx, size: u64) void {
    _ = ctx.lim.used.fetchSub(size, .monotonic);
    if (ctx.lim.acc) |a| accUncharge(.{ .acc = a, .cat = ctx.lim.acc_cat }, size);
}

fn limBacking(ctx: LimCtx) std.mem.Allocator {
    return ctx.lim.parent;
}

const LimImpl = Impl(LimCtx, limCharge, limUncharge, limBacking);

/// Hard-limit wrapper over any allocator: total bytes charged never exceed
/// `limit` (allocation fails instead). Used to give the mask cache a strict
/// byte budget that covers every allocation it makes, map growth included.
/// With `acc` set the same total is also charged to the category counters
/// of the accounting, so the hard budget and the global accounting always
/// agree (single header).
pub const Limited = struct {
    parent: std.mem.Allocator,
    limit: u64,
    used: std.atomic.Value(u64),
    acc: ?*Accounting = null,
    acc_cat: Category = .cache,
    ctx: LimCtx = undefined,

    pub fn init(parent: std.mem.Allocator, limit: u64) Limited {
        return .{ .parent = parent, .limit = limit, .used = std.atomic.Value(u64).init(0) };
    }

    /// Limited over the raw backing allocator that also charges `limit`
    /// against the accounting's `cat` counters (parent must be the raw
    /// allocator, not an Accounting layer: see limCharge).
    pub fn initAccounting(parent: std.mem.Allocator, limit: u64, acc: *Accounting, cat: Category) Limited {
        return .{
            .parent = parent,
            .limit = limit,
            .used = std.atomic.Value(u64).init(0),
            .acc = acc,
            .acc_cat = cat,
        };
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
        const i = @backingInt(cat);
        self.ctxs[i] = .{ .sess = self, .cat = cat };
        return .{ .ptr = &self.ctxs[i], .vtable = &SessImpl.vtable };
    }

    pub fn usedBytes(self: *const SessionAccount) u64 {
        return self.used_bytes.load(.monotonic);
    }
};

/// Bump allocator for grammars and scratch work. Unlike
/// std.heap.ArenaAllocator it never asks the backing allocator to grow a
/// chunk in place: for page-backed chunks that succeeds only when the
/// address range after the chunk is free, so the bytes taken by the same
/// compile depended on the process history. Chunks double up to
/// max_chunk; a request above big_request gets a chunk of its own, which
/// free returns to the backing allocator.
pub const Arena = struct {
    child: std.mem.Allocator,
    first: ?*Chunk = null,
    cur: ?*Chunk = null,
    end: usize = 0,

    pub const max_chunk: usize = 1 << 20;
    pub const big_request: usize = 64 << 10;
    const min_chunk: usize = 1 << 10;
    const chunk_align: std.mem.Alignment = .of(Chunk);

    const Chunk = struct {
        prev: ?*Chunk,
        next: ?*Chunk,
        raw: [*]u8,
        raw_len: usize,

        fn data(c: *Chunk) usize {
            return @intFromPtr(c) + @sizeOf(Chunk);
        }

        fn limit(c: *Chunk) usize {
            return @intFromPtr(c.raw) + c.raw_len;
        }
    };

    const vtable: std.mem.Allocator.VTable = .{
        .alloc = alloc,
        .resize = resize,
        .remap = remap,
        .free = free,
    };

    pub fn init(child: std.mem.Allocator) Arena {
        return .{ .child = child };
    }

    pub fn allocator(self: *Arena) std.mem.Allocator {
        return .{ .ptr = self, .vtable = &vtable };
    }

    pub fn deinit(self: *Arena) void {
        var it = self.first;
        while (it) |c| {
            it = c.next;
            self.child.rawFree(c.raw[0..c.raw_len], chunk_align, @returnAddress());
        }
        self.* = .{ .child = self.child };
    }

    /// Bytes taken from the backing allocator.
    pub fn takenBytes(self: *const Arena) usize {
        var total: usize = 0;
        var it = self.first;
        while (it) |c| : (it = c.next) total += c.raw_len;
        return total;
    }

    fn own(len: usize, alignment: std.mem.Alignment) bool {
        return len +| alignment.toByteUnits() > big_request;
    }

    fn link(self: *Arena, c: *Chunk) void {
        c.prev = null;
        c.next = self.first;
        if (self.first) |f| f.prev = c;
        self.first = c;
    }

    fn alloc(p: *anyopaque, n: usize, alignment: std.mem.Alignment, ra: usize) ?[*]u8 {
        const self: *Arena = @ptrCast(@alignCast(p));
        const al = @max(alignment.toByteUnits(), @alignOf(Chunk));
        if (own(n, alignment)) {
            // The header sits right before the data, so free finds it.
            const raw_len = std.math.add(usize, @sizeOf(Chunk) + al, n) catch return null;
            const raw = self.child.rawAlloc(raw_len, chunk_align, ra) orelse return null;
            const data = std.mem.alignForward(usize, @intFromPtr(raw) + @sizeOf(Chunk), al);
            const c: *Chunk = @ptrFromInt(data - @sizeOf(Chunk));
            c.raw = raw;
            c.raw_len = raw_len;
            self.link(c);
            return @ptrFromInt(data);
        }
        if (self.cur) |c| {
            const addr = std.mem.alignForward(usize, c.data() + self.end, alignment.toByteUnits());
            if (addr + n <= c.limit()) {
                self.end = addr + n - c.data();
                return @ptrFromInt(addr);
            }
        }
        const prev_len = if (self.cur) |c| c.raw_len else 0;
        const need = @sizeOf(Chunk) + alignment.toByteUnits() + n;
        const raw_len = @max(need, @min(max_chunk, @max(min_chunk, prev_len * 2)));
        const raw = self.child.rawAlloc(raw_len, chunk_align, ra) orelse return null;
        const c: *Chunk = @ptrCast(@alignCast(raw));
        c.raw = raw;
        c.raw_len = raw_len;
        self.link(c);
        self.cur = c;
        const addr = std.mem.alignForward(usize, c.data(), alignment.toByteUnits());
        self.end = addr + n - c.data();
        return @ptrFromInt(addr);
    }

    fn isLast(self: *Arena, buf: []u8) bool {
        const c = self.cur orelse return false;
        return @intFromPtr(buf.ptr) + buf.len == c.data() + self.end;
    }

    fn resize(p: *anyopaque, buf: []u8, alignment: std.mem.Alignment, new_len: usize, ra: usize) bool {
        _ = ra;
        const self: *Arena = @ptrCast(@alignCast(p));
        if (own(buf.len, alignment)) return new_len <= buf.len and own(new_len, alignment);
        if (own(new_len, alignment)) return false;
        if (!self.isLast(buf)) return new_len <= buf.len;
        const c = self.cur.?;
        if (@intFromPtr(buf.ptr) + new_len > c.limit()) return false;
        self.end = @intFromPtr(buf.ptr) + new_len - c.data();
        return true;
    }

    fn remap(p: *anyopaque, buf: []u8, alignment: std.mem.Alignment, new_len: usize, ra: usize) ?[*]u8 {
        return if (resize(p, buf, alignment, new_len, ra)) buf.ptr else null;
    }

    fn free(p: *anyopaque, buf: []u8, alignment: std.mem.Alignment, ra: usize) void {
        const self: *Arena = @ptrCast(@alignCast(p));
        if (own(buf.len, alignment)) {
            const c: *Chunk = @ptrFromInt(@intFromPtr(buf.ptr) - @sizeOf(Chunk));
            if (c.prev) |pr| pr.next = c.next else self.first = c.next;
            if (c.next) |nx| nx.prev = c.prev;
            self.child.rawFree(c.raw[0..c.raw_len], chunk_align, ra);
            return;
        }
        if (self.isLast(buf)) self.end = @intFromPtr(buf.ptr) - self.cur.?.data();
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

/// Backing that grows a block in place like the page allocator: always
/// within the pages (or small size class) the block spans, past them only
/// when `room_after` says the address range after the block is free.
const LayoutBacking = struct {
    room_after: bool,
    caps: std.AutoHashMapUnmanaged(usize, usize) = .{},

    const vt: std.mem.Allocator.VTable = .{
        .alloc = lbAlloc,
        .resize = lbResize,
        .remap = std.mem.Allocator.noRemap,
        .free = lbFree,
    };

    fn allocator(self: *LayoutBacking) std.mem.Allocator {
        return .{ .ptr = self, .vtable = &vt };
    }

    fn span(len: usize) usize {
        const page = std.heap.pageSize();
        return if (len >= page) std.mem.alignForward(usize, len, page) else std.math.ceilPowerOfTwoAssert(usize, @max(len, 8));
    }

    fn lbAlloc(p: *anyopaque, len: usize, al: std.mem.Alignment, ra: usize) ?[*]u8 {
        const self: *LayoutBacking = @ptrCast(@alignCast(p));
        const cap = if (self.room_after) span(len) * 4 else span(len);
        const ptr = std.testing.allocator.rawAlloc(cap, al, ra) orelse return null;
        self.caps.put(std.testing.allocator, @intFromPtr(ptr), cap) catch {
            std.testing.allocator.rawFree(ptr[0..cap], al, ra);
            return null;
        };
        return ptr;
    }

    fn lbResize(p: *anyopaque, mem: []u8, al: std.mem.Alignment, new_len: usize, ra: usize) bool {
        _ = al;
        _ = ra;
        const self: *LayoutBacking = @ptrCast(@alignCast(p));
        const cap = self.caps.get(@intFromPtr(mem.ptr)).?;
        if (span(new_len) <= span(mem.len)) return true;
        return self.room_after and new_len <= cap;
    }

    fn lbFree(p: *anyopaque, mem: []u8, al: std.mem.Alignment, ra: usize) void {
        const self: *LayoutBacking = @ptrCast(@alignCast(p));
        const cap = self.caps.fetchRemove(@intFromPtr(mem.ptr)).?.value;
        std.testing.allocator.rawFree(mem.ptr[0..cap], al, ra);
    }
};

const Charges = struct { used: u64, peak: u64, temp_peak: u64 };

fn chargesFor(room_after: bool) !Charges {
    var lb = LayoutBacking{ .room_after = room_after };
    defer lb.caps.deinit(std.testing.allocator);
    var acc = Accounting.init(lb.allocator(), 1 << 30);
    var arena = Arena.init(acc.allocator(.grammar));
    const a = arena.allocator();
    var prng = std.Random.DefaultPrng.init(7);
    const r = prng.random();
    var list: std.ArrayListUnmanaged(u32) = .empty;
    for (0..4000) |i| {
        const n = if (i % 97 == 0) r.intRangeAtMost(usize, 70_000, 200_000) else r.intRangeAtMost(usize, 1, 3000);
        const buf = try a.alloc(u8, n);
        if (i % 5 == 0) a.free(buf);
        try list.append(a, @intCast(i));
    }
    const used = acc.used(.grammar);
    const peak = acc.peak(.grammar);
    arena.deinit();
    const t = acc.allocator(.temp);
    var buf = try t.alloc(u8, 10_000);
    for (0..200) |_| {
        const n = buf.len + 1000;
        if (t.resize(buf, n)) {
            buf = buf.ptr[0..n];
        } else {
            const nb = try t.alloc(u8, n);
            @memcpy(nb[0..buf.len], buf);
            t.free(buf);
            buf = nb;
        }
    }
    t.free(buf);
    try std.testing.expectEqual(@as(u64, 0), acc.totalUsed());
    return .{ .used = used, .peak = peak, .temp_peak = acc.peak(.temp) };
}

test "accounting and arena: charges do not depend on in-place growth" {
    // A compile in one process took 146 to 190 MiB depending on whether the
    // pages after std.heap.ArenaAllocator's chunk were free.
    try std.testing.expectEqual(try chargesFor(false), try chargesFor(true));
}

test "arena: last allocation resizes and frees in place, own chunks go back" {
    var acc = Accounting.init(std.testing.allocator, 1 << 30);
    var arena = Arena.init(acc.allocator(.grammar));
    const a = arena.allocator();
    const x = try a.alloc(u8, 100);
    const y = try a.alloc(u64, 10);
    try std.testing.expect(std.mem.isAligned(@intFromPtr(y.ptr), @alignOf(u64)));
    try std.testing.expect(a.resize(y, 20));
    try std.testing.expect(!a.resize(x, 200));
    try std.testing.expect(a.resize(x, 50));
    const end = arena.end;
    a.free(y.ptr[0..20]);
    try std.testing.expect(arena.end < end);
    const before = acc.used(.grammar);
    const big = try a.alignedAlloc(u8, .@"64", Arena.big_request + 1);
    try std.testing.expect(std.mem.isAligned(@intFromPtr(big.ptr), 64));
    try std.testing.expect(acc.used(.grammar) > before + Arena.big_request);
    try std.testing.expect(!a.resize(big, big.len + 1));
    a.free(big);
    try std.testing.expectEqual(before, acc.used(.grammar));
    for (0..3000) |_| _ = try a.alloc(u8, 1000);
    try std.testing.expect(arena.takenBytes() < 3000 * 1000 + Arena.max_chunk + 4096);
    arena.deinit();
    try std.testing.expectEqual(@as(u64, 0), acc.totalUsed());
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
