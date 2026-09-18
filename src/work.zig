const std = @import("std");

/// Per-call work budget and cancellation signal (NFR-2). One Work value is
/// created per public API call (compile/fill_mask); loops between bounded
/// work portions call charge(). The cancel flag is a caller-owned atomic
/// byte registered via zg_cancel_flag_set; it must outlive the call.
pub const Work = struct {
    limit: u64 = 0, // 0 = unlimited
    cancel: ?*align(1) const u8 = null,
    ops: u64 = 0,

    pub fn init(limit: u64, cancel: ?*align(1) const u8) Work {
        return .{ .limit = limit, .cancel = cancel };
    }

    pub fn charge(self: *Work, n: u64) error{ Cancelled, ResourceLimit }!void {
        if (self.cancel) |p| {
            if (@atomicLoad(u8, p, .acquire) != 0) return error.Cancelled;
        }
        if (self.limit != 0) {
            self.ops +|= n;
            if (self.ops > self.limit) return error.ResourceLimit;
        }
    }
};

test "work: cancel flag wins, limit counts ops, zero means unlimited" {
    var flag: u8 = 0;
    var w = Work.init(3, &flag);
    try w.charge(1);
    try w.charge(2);
    try std.testing.expectError(error.ResourceLimit, w.charge(1));
    @atomicStore(u8, &flag, 1, .release);
    try std.testing.expectError(error.Cancelled, w.charge(0));
    var unlim = Work{};
    var i: usize = 0;
    while (i < 1000) : (i += 1) try unlim.charge(1);
}
