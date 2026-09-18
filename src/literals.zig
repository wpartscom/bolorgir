const std = @import("std");
const grammar = @import("grammar.zig");
const json = @import("json.zig");
const schema = @import("schema.zig");
const work_mod = @import("work.zig");

pub const CompileError = schema.CompileError;
pub const Diagnostic = schema.Diagnostic;

pub fn compileLiterals(child_allocator: std.mem.Allocator, json_bytes: []const u8, max_depth: u32, diag: *Diagnostic, w: *work_mod.Work) CompileError!grammar.Grammar {
    _ = max_depth;
    diag.* = .{};
    var arena = std.heap.ArenaAllocator.init(child_allocator);
    errdefer arena.deinit();
    const a = arena.allocator();
    try w.charge(@max(1, json_bytes.len / 4096));
    var off: u32 = std.math.maxInt(u32);
    const root = json.parse(a, json_bytes, &off) catch |e| {
        diag.offset = off;
        return schema.reportJsonError(diag, e);
    };
    const items = switch (root.v) {
        .array => |it| it,
        else => {
            schema.report(diag, .invalid_schema, root.offset, "literal set must be a JSON array of strings", .{});
            return error.InvalidSchema;
        },
    };
    if (items.len == 0) {
        schema.report(diag, .invalid_schema, root.offset, "literal set must not be empty", .{});
        return error.InvalidSchema;
    }
    var builder = grammar.Builder.init(a);
    var alts: std.ArrayListUnmanaged(grammar.NodeId) = .{};
    var seen: std.StringHashMapUnmanaged(void) = .{};
    for (items, 0..) |it, i| {
        try w.charge(1);
        const s = switch (it.v) {
            .string => |s| s,
            else => {
                schema.report(diag, .invalid_schema, it.offset, "literal set elements must be strings", .{});
                schema.setPointerIndex(diag, i);
                return error.InvalidSchema;
            },
        };
        const gop = try seen.getOrPut(a, s);
        if (gop.found_existing) continue;
        try alts.append(a, try builder.addNode(.{ .literal = try builder.addLiteral(s) }));
    }
    const root_id = if (alts.items.len == 1)
        alts.items[0]
    else
        try builder.addNode(.{ .choice = try builder.copyNodeIds(alts.items) });
    return builder.finish(arena, .literal_set, root_id, grammar.identityOf(.literal_set, json_bytes));
}

fn compileT(json_bytes: []const u8) CompileError!grammar.Grammar {
    var diag: Diagnostic = .{};
    var w = work_mod.Work{};
    return compileLiterals(std.testing.allocator, json_bytes, 64, &diag, &w);
}

test "single literal compiles to literal node" {
    var g = try compileT("[\"only\"]");
    defer g.deinit();
    try std.testing.expectEqual(grammar.GrammarKind.literal_set, g.kind);
    const lit = switch (g.node(g.root).*) {
        .literal => |l| l,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqualStrings("only", g.literalBytes(lit));
}

test "multiple literals with common prefix and duplicates" {
    var g = try compileT("[\"foo\",\"foobar\",\"fob\",\"foo\"]");
    defer g.deinit();
    const alts = switch (g.node(g.root).*) {
        .choice => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 3), alts.len);
    const expected = [_][]const u8{ "foo", "foobar", "fob" };
    for (alts, expected) |id, exp| {
        const lit = switch (g.node(id).*) {
            .literal => |l| l,
            else => return error.TestUnexpectedResult,
        };
        try std.testing.expectEqualStrings(exp, g.literalBytes(lit));
    }
}

test "empty string is a valid alternative" {
    var g = try compileT("[\"\"]");
    defer g.deinit();
    const lit = switch (g.node(g.root).*) {
        .literal => |l| l,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(u32, 0), lit.len);
}

test "unicode and escaped input decode to raw bytes" {
    var g = try compileT("[\"a\\nb\",\"\\u00e9\"]");
    defer g.deinit();
    const alts = switch (g.node(g.root).*) {
        .choice => |c| c,
        else => return error.TestUnexpectedResult,
    };
    const l0 = switch (g.node(alts[0]).*) {
        .literal => |l| l,
        else => return error.TestUnexpectedResult,
    };
    const l1 = switch (g.node(alts[1]).*) {
        .literal => |l| l,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqualStrings("a\nb", g.literalBytes(l0));
    try std.testing.expectEqualStrings("\xc3\xa9", g.literalBytes(l1));
}

test "reject empty array, non-array and non-string elements" {
    try std.testing.expectError(error.InvalidSchema, compileT("[]"));
    try std.testing.expectError(error.InvalidSchema, compileT("{}"));
    try std.testing.expectError(error.InvalidSchema, compileT("[\"a\",1]"));
    try std.testing.expectError(error.InvalidSchema, compileT("[\"a\""));
}
