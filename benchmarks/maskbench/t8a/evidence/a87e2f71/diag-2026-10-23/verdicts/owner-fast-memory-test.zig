
const ReviewFastLeg = struct { ok: bool, err: ?Error, peak: u64, word: u32, entries: usize, muted: bool, ops: u64 };
fn reviewFastLeg(limit: u64, off: bool) !ReviewFastLeg {
    const alloc = testing.allocator;
    const alloc_mod = @import("alloc.zig");
    const schema_mod = @import("schema.zig");
    var sdiag: schema_mod.Diagnostic = .{};
    var cw = work_mod.Work{};
    var g = try schema_mod.compile(alloc, "{\"type\":\"object\",\"properties\":{\"x\":{\"type\":\"string\",\"pattern\":\"^a*$\"}}}", 128, &sdiag, &cw, false, .spec_v1, &.{});
    defer g.deinit();
    try testing.expect(g.needs_reachability);
    const long: [1024]u8 = @splat('a');
    var tk = try makeTok(alloc, 4, &.{ "a", &long, "\":\"\"}", "" }, &.{3}, &.{});
    defer tk.deinit();
    var fast = try precompute.FastData.build(alloc, &tk);
    defer fast.deinit();
    var acc = alloc_mod.Accounting.init(alloc, limit);
    var side = parser.Side.init(acc.allocator(.session));
    defer side.deinit();
    var buf = MaskBuf.init(acc.allocator(.temp), acc.allocator(.temp));
    defer buf.deinit();
    var st = try parser.initState(&g, 8, &side);
    defer parser.releaseState(&side, &st);
    var st1: parser.State = undefined;
    var work: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st, "{\"", &st1, &work);
    defer parser.releaseState(&side, &st1);
    complete.test_overrides.cert_cache_off = off;
    defer complete.test_overrides.cert_cache_off = false;
    var out = [_]u32{0};
    var w = work_mod.Work.init(100000, null);
    const res = fillMaskFast(&g, &tk, &st1, &out, &w, &buf, &side, &fast, null, null, 1);
    var err: ?Error = null;
    var ok = false;
    if (res) |done| { ok = done; } else |e| err = e;
    return .{ .ok = ok, .err = err, .peak = acc.totalPeak(), .word = out[0], .entries = buf.completion.cert_map.count(), .muted = buf.completion.cert_muted, .ops = w.ops };
}
test "review fast mask memory boundary" {
    const plain = try reviewFastLeg(std.math.maxInt(u64), true);
    const on = try reviewFastLeg(std.math.maxInt(u64), false);
    const off_tight = try reviewFastLeg(plain.peak, true);
    const on_tight = try reviewFastLeg(plain.peak, false);
    std.debug.print("\nFAST plain={any}\non={any}\noff_tight={any}\non_tight={any}\n", .{plain,on,off_tight,on_tight});
    try testing.expect(plain.ok and on.ok and off_tight.ok);
    try testing.expect(on_tight.ok);
    try testing.expectEqual(off_tight.word, on_tight.word);
}
