//! T4 fuzz / fault-injection runner (see TZ §11 T4).
//!
//! Campaigns:
//!   A. compile     — schema and literal-set compiler on random inputs
//!                    (structured + mutations + garbage), leak checking.
//!   B. injection   — allocator failure injection at every allocation point
//!                    of compile/tokenizer prepare: clean error, zero leaks.
//!   C. accept/mask — random sessions through the C ABI: masks, accept,
//!                    finish, abort, batch, harsh limits; mask invariants and
//!                    no partial token acceptance.
//!   D. cache       — lazy/adaptive (large/tiny budget)/precompute parity on
//!                    identical traces; rerun in 4 threads, bitwise checksum
//!                    comparison.
//!
//! Run: zig build fuzz -- [--seed N] [--compile-iters N] [--walk-sessions N]
//!      [--cache-walks N] [--fail-schemas N] [--report PATH]

const std = @import("std");
const schema = @import("schema.zig");
const literals = @import("literals.zig");
const grammar = @import("grammar.zig");
const tokenizer = @import("tokenizer.zig");
const parser = @import("parser.zig");
const mask = @import("mask.zig");
const work_mod = @import("work.zig");
const c_api = @import("c_api.zig");

const page = std.heap.page_allocator;
const FailingAllocator = std.testing.FailingAllocator;

const Config = struct {
    seed: u64 = 0x5EED_0001,
    compile_iters: u64 = 400_000,
    fail_schemas: u64 = 400,
    fail_tokenizers: u64 = 60,
    walk_sessions: u64 = 25_000,
    batch_walks: u64 = 2_000,
    cache_walks: u64 = 1_200,
    report: []const u8 = "tests/fuzz/artifacts/zig_fuzz_report.json",
    artifacts_dir: []const u8 = "tests/fuzz/artifacts",
    corpus_dir: []const u8 = "tests/fuzz/corpus/auto",
};

const Counters = struct {
    compile_iters: u64 = 0,
    compile_ok: u64 = 0,
    compile_err: u64 = 0,
    injected_failures: u64 = 0,
    injected_cases: u64 = 0,
    walk_sessions: u64 = 0,
    walk_api_calls: u64 = 0,
    walk_masks: u64 = 0,
    walk_accepts: u64 = 0,
    walk_dead_ends: u64 = 0,
    walk_resource_limits: u64 = 0,
    walk_compile_failures: u64 = 0,
    cache_walks: u64 = 0,
    cache_mask_cmps: u64 = 0,
    threaded_match: bool = false,
};

var counters: Counters = .{};
var cfg: Config = .{};

fn failInvariant(comptime fmt: []const u8, args: anytype) noreturn {
    std.debug.print("FUZZ INVARIANT VIOLATION: " ++ fmt ++ "\n", args);
    std.process.exit(2);
}

fn saveArtifact(name: []const u8, data: []const u8) void {
    std.fs.cwd().makePath(cfg.artifacts_dir) catch {};
    var path_buf: [512]u8 = undefined;
    const path = std.fmt.bufPrint(&path_buf, "{s}/{s}", .{ cfg.artifacts_dir, name }) catch return;
    std.fs.cwd().writeFile(.{ .sub_path = path, .data = data }) catch |e| {
        std.debug.print("cannot write artifact {s}: {s}\n", .{ path, @errorName(e) });
    };
}

fn artifactAndDie(comptime fmt: []const u8, args: anytype, art_name: []const u8, art_data: []const u8) noreturn {
    saveArtifact(art_name, art_data);
    failInvariant(fmt, args);
}

// ---------------------------------------------------------------------------
// Random input generators
// ---------------------------------------------------------------------------

const json_garbage_alphabet = "{}[]\",:0123456789truefalsn.-+eE \t\x00\xc3\xa9\xff";
const string_char_pieces = [_][]const u8{ "a", "b", "x", "0", "1", "\"", "\\", "\n", "\xc3\xa9", " ", "/", "~" };
const int_lexemes = [_][]const u8{ "0", "1", "-1", "42", "-7", "100", "-0", "9223372036854775807", "-9223372036854775808" };
const num_lexemes = [_][]const u8{ "0.5", "-0.5", "1e2", "1E+2", "1e-2", "2.50", "-0.0", "3.14159", "0e0", "10", "-3" };

fn writeJsonString(buf: *std.ArrayList(u8), a: std.mem.Allocator, s: []const u8) !void {
    try buf.append(a, '"');
    for (s) |c| {
        switch (c) {
            '"' => try buf.appendSlice(a, "\\\""),
            '\\' => try buf.appendSlice(a, "\\\\"),
            '\n' => try buf.appendSlice(a, "\\n"),
            '\r' => try buf.appendSlice(a, "\\r"),
            '\t' => try buf.appendSlice(a, "\\t"),
            else => {
                if (c < 0x20)
                    try buf.print(a, "\\u{x:0>4}", .{c})
                else
                    try buf.append(a, c);
            },
        }
    }
    try buf.append(a, '"');
}

fn genRandomString(buf: *std.ArrayList(u8), a: std.mem.Allocator, rng: std.Random, max_len: u32) !void {
    try buf.append(a, '"');
    const n = rng.uintLessThan(u32, max_len + 1);
    var i: u32 = 0;
    while (i < n) : (i += 1) {
        const piece = string_char_pieces[rng.uintLessThan(usize, string_char_pieces.len)];
        for (piece) |c| {
            switch (c) {
                '"' => try buf.appendSlice(a, "\\\""),
                '\\' => try buf.appendSlice(a, "\\\\"),
                '\n' => try buf.appendSlice(a, "\\n"),
                else => try buf.append(a, c),
            }
        }
    }
    try buf.append(a, '"');
}

const ScalarKind = enum { string, integer, number, boolean, null_v };

fn writeScalar(buf: *std.ArrayList(u8), a: std.mem.Allocator, rng: std.Random, kind: ScalarKind) !void {
    switch (kind) {
        .string => try genRandomString(buf, a, rng, 4),
        .integer => try buf.appendSlice(a, int_lexemes[rng.uintLessThan(usize, int_lexemes.len)]),
        .number => try buf.appendSlice(a, num_lexemes[rng.uintLessThan(usize, num_lexemes.len)]),
        .boolean => try buf.appendSlice(a, if (rng.boolean()) "true" else "false"),
        .null_v => try buf.appendSlice(a, "null"),
    }
}

fn typeNameOf(kind: ScalarKind) []const u8 {
    return switch (kind) {
        .string => "string",
        .integer => "integer",
        .number => "number",
        .boolean => "boolean",
        .null_v => "null",
    };
}

const SchemaGen = struct {
    rng: std.Random,
    a: std.mem.Allocator,
    buf: *std.ArrayList(u8),
    must_compile: bool,
    max_depth: u32 = 5,

    /// One schema node (JSON object) of the supported subset.
    fn node(self: *SchemaGen, depth: u32) std.mem.Allocator.Error!void {
        const rng = self.rng;
        const a = self.a;
        const b = self.buf;
        if (depth == 0 and rng.uintLessThan(u32, 100) < 12) {
            // $defs + $ref
            const ndefs = rng.intRangeAtMost(u32, 1, 2);
            try b.appendSlice(a, "{\"$defs\":{");
            var i: u32 = 0;
            while (i < ndefs) : (i += 1) {
                if (i > 0) try b.append(a, ',');
                try b.print(a, "\"d{d}\":", .{i});
                try self.node(depth + 2);
            }
            try b.print(a, "}},\"$ref\":\"#/$defs/d{d}\"}}", .{rng.uintLessThan(u32, ndefs)});
            return;
        }
        var w = rng.uintLessThan(u32, 100);
        if (depth >= self.max_depth) w = 30 + rng.uintLessThan(u32, 70); // scalars/enum only
        if (w < 20) {
            // object
            const nprops = rng.uintLessThan(u32, 5);
            try b.appendSlice(a, "{\"type\":\"object\",\"properties\":{");
            var i: u32 = 0;
            while (i < nprops) : (i += 1) {
                if (i > 0) try b.append(a, ',');
                try b.print(a, "\"k{d}\":", .{i});
                try self.node(depth + 1);
            }
            try b.appendSlice(a, "},\"required\":[");
            var first = true;
            i = 0;
            while (i < nprops) : (i += 1) {
                if (rng.boolean()) {
                    if (!first) try b.append(a, ',');
                    try b.print(a, "\"k{d}\"", .{i});
                    first = false;
                }
            }
            try b.appendSlice(a, "],\"additionalProperties\":false}");
        } else if (w < 34) {
            // array
            try b.appendSlice(a, "{\"type\":\"array\",\"items\":");
            try self.node(depth + 1);
            const mn = rng.uintLessThan(u32, 3);
            try b.print(a, ",\"minItems\":{d}", .{mn});
            if (self.must_compile or rng.uintLessThan(u32, 100) < 90) {
                try b.print(a, ",\"maxItems\":{d}", .{mn + rng.uintLessThan(u32, 4)});
            } else if (rng.boolean()) {
                try b.print(a, ",\"maxItems\":{d}", .{rng.uintLessThan(u32, 2)}); // may be < min
            }
            try b.append(a, '}');
        } else if (w < 52) {
            // string
            try b.appendSlice(a, "{\"type\":\"string\"");
            if (rng.boolean()) {
                const mn = rng.uintLessThan(u32, 3);
                try b.print(a, ",\"minLength\":{d}", .{mn});
                if (self.must_compile or rng.boolean())
                    try b.print(a, ",\"maxLength\":{d}", .{mn + rng.uintLessThan(u32, 4)})
                else
                    try b.print(a, ",\"maxLength\":{d}", .{rng.uintLessThan(u32, 2)});
            }
            try b.append(a, '}');
        } else if (w < 62) {
            try b.appendSlice(a, "{\"type\":\"integer\"}");
        } else if (w < 72) {
            try b.appendSlice(a, "{\"type\":\"number\"}");
        } else if (w < 78) {
            try b.appendSlice(a, "{\"type\":\"boolean\"}");
        } else if (w < 82) {
            try b.appendSlice(a, "{\"type\":\"null\"}");
        } else {
            // enum / const of same-type scalars
            const kind: ScalarKind = @enumFromInt(rng.uintLessThan(u8, 5));
            const with_type = self.must_compile and rng.boolean() or
                (!self.must_compile and rng.uintLessThan(u32, 100) < 60);
            const is_const = rng.uintLessThan(u32, 100) < 30;
            try b.append(a, '{');
            if (with_type) try b.print(a, "\"type\":\"{s}\",", .{typeNameOf(kind)});
            if (is_const) {
                try b.appendSlice(a, "\"const\":");
                try writeScalar(b, a, rng, kind);
            } else {
                try b.appendSlice(a, "\"enum\":[");
                const n = rng.intRangeAtMost(u32, 1, 4);
                var i: u32 = 0;
                while (i < n) : (i += 1) {
                    if (i > 0) try b.append(a, ',');
                    try writeScalar(b, a, rng, kind);
                }
                try b.append(a, ']');
            }
            // occasional annotations
            if (rng.uintLessThan(u32, 100) < 10)
                try b.appendSlice(a, ",\"title\":\"t\"");
            try b.append(a, '}');
        }
    }
};

fn genSchema(a: std.mem.Allocator, buf: *std.ArrayList(u8), rng: std.Random, must_compile: bool) !void {
    var gen = SchemaGen{ .rng = rng, .a = a, .buf = buf, .must_compile = must_compile };
    try gen.node(0);
}

fn genLiteralsJson(a: std.mem.Allocator, buf: *std.ArrayList(u8), rng: std.Random, valid: bool) !void {
    if (!valid and rng.uintLessThan(u32, 100) < 40) {
        // garbage instead of a string array
        const n = rng.uintLessThan(u32, 60);
        var i: u32 = 0;
        while (i < n) : (i += 1)
            try buf.append(a, json_garbage_alphabet[rng.uintLessThan(usize, json_garbage_alphabet.len)]);
        return;
    }
    try buf.append(a, '[');
    const n = rng.intRangeAtMost(u32, 1, 6);
    var i: u32 = 0;
    while (i < n) : (i += 1) {
        if (i > 0) try buf.append(a, ',');
        try genRandomString(buf, a, rng, 5);
    }
    try buf.append(a, ']');
}

fn genGarbage(a: std.mem.Allocator, buf: *std.ArrayList(u8), rng: std.Random) !void {
    switch (rng.uintLessThan(u32, 4)) {
        0 => {
            const n = rng.uintLessThan(u32, 120);
            var i: u32 = 0;
            while (i < n) : (i += 1)
                try buf.append(a, json_garbage_alphabet[rng.uintLessThan(usize, json_garbage_alphabet.len)]);
        },
        1 => {
            // valid schema cut at a random point
            var tmp: std.ArrayList(u8) = .{};
            try genSchema(a, &tmp, rng, true);
            const cut = rng.uintLessThan(usize, tmp.items.len + 1);
            try buf.appendSlice(a, tmp.items[0..cut]);
        },
        2 => {
            // valid schema + unsupported keyword
            var tmp: std.ArrayList(u8) = .{};
            try genSchema(a, &tmp, rng, true);
            const kw = [_][]const u8{ "\"pattern\":\"^a\",", "\"anyOf\":[],", "\"minimum\":3,", "\"format\":\"email\"," };
            try buf.append(a, '{');
            try buf.appendSlice(a, kw[rng.uintLessThan(usize, kw.len)]);
            try buf.appendSlice(a, tmp.items[1..]);
        },
        else => {
            // targeted mutations of the acceptance rules
            switch (rng.uintLessThan(u32, 5)) {
                0 => try buf.appendSlice(a, "{\"type\":\"string\",\"type\":\"integer\"}"),
                1 => try buf.appendSlice(a, "{\"type\":\"string\",\"minLength\":2.5}"),
                2 => try buf.appendSlice(a, "{\"type\":\"object\",\"properties\":{},\"required\":[],\"additionalProperties\":true}"),
                3 => try buf.appendSlice(a, "{\"type\":[\"string\",\"null\"]}"),
                else => try buf.appendSlice(a, "{\"type\":\"object\",\"properties\":{},\"required\":[\"zz\"],\"additionalProperties\":false}"),
            }
        },
    }
}

// ---------------------------------------------------------------------------
// Tokenizer
// ---------------------------------------------------------------------------

const tok_pool = [_][]const u8{
    "{",     "}",      "[",      "]",    ",",     ":",    "\"", "0",        "1",   "2",
    "3",     "-",      ".",      "e",    "E",     "+",    "t",  "r",        "u",   "f",
    "a",     "l",      "s",      "n",    " ",     "x",    "b",  "\xc3\xa9", "\\",  "\\n",
    "\\u00", "\"a\"",  "\"b\"",  "10",   "42",    "1e5",  "-0", "0.5",      "{\"", "\":\"",
    "\",",   "\"k0\"", "\"k1\"", "true", "false", "null", "[1", "\"k2\":",  "}]",
};

const TokSpec = struct {
    vocab: u32,
    tokens: [][]const u8,
    eos: []u32,
    special: []u32,
};

fn genTokenizer(a: std.mem.Allocator, rng: std.Random, full: bool) !TokSpec {
    // structural single chars are (almost) always included so walks progress;
    // `full` forces all of them (used where every valid schema must compile)
    var list: std.ArrayList([]const u8) = .{};
    const core = [_][]const u8{ "{", "}", "[", "]", ",", ":", "\"", "0", "1", "a", "t", "f", "n", "-", ".", "e", "u", "l", "s", "r" };
    for (core) |t| {
        if (full or rng.uintLessThan(u32, 100) < 85) try list.append(a, t);
    }
    var extra = rng.intRangeAtMost(u32, 2, 20);
    while (extra > 0) : (extra -= 1) {
        const t = tok_pool[rng.uintLessThan(usize, tok_pool.len)];
        var dup = false;
        for (list.items) |x| {
            if (std.mem.eql(u8, x, t)) {
                dup = true;
                break;
            }
        }
        if (!dup) try list.append(a, t);
    }
    if (list.items.len < 4) try list.appendSlice(a, &.{ "{", "}", "\"", "0" });
    const vocab: u32 = @intCast(list.items.len + 2);
    const eos_id: u32 = @intCast(list.items.len);
    const special_id: u32 = eos_id + 1;
    try list.append(a, ""); // eos
    try list.append(a, "<pad>"); // special
    const eos = try a.alloc(u32, 1);
    eos[0] = eos_id;
    const special = try a.alloc(u32, 1);
    special[0] = special_id;
    return .{ .vocab = vocab, .tokens = list.items, .eos = eos, .special = special };
}

const TokDescKeep = struct {
    desc: c_api.TokenizerDesc,
    entries: []c_api.TokenEntry,
    blob: []u8,
};

// Byte-complete tokenizer: all 256 single-byte tokens + EOS + special.
// Passes the compile-time coverage gate for every grammar.
fn genByteTokenizer(a: std.mem.Allocator) !TokSpec {
    var list: std.ArrayList([]const u8) = .{};
    const bufs = try a.alloc([1]u8, 256);
    for (0..256) |i| {
        bufs[i][0] = @intCast(i);
        try list.append(a, &bufs[i]);
    }
    try list.append(a, "");
    try list.append(a, "<pad>");
    const eos = try a.alloc(u32, 1);
    eos[0] = 256;
    const special = try a.alloc(u32, 1);
    special[0] = 257;
    return .{ .vocab = 258, .tokens = list.items, .eos = eos, .special = special };
}

fn descOf(a: std.mem.Allocator, spec: TokSpec) !TokDescKeep {
    var blob_len: usize = 0;
    for (spec.tokens) |t| blob_len += t.len;
    const blob = try a.alloc(u8, @max(blob_len, 1));
    const entries = try a.alloc(c_api.TokenEntry, spec.tokens.len);
    var off: u64 = 0;
    for (spec.tokens, 0..) |t, i| {
        @memcpy(blob[@intCast(off)..][0..t.len], t);
        entries[i] = .{ .id = @intCast(i), .reserved = 0, .offset = off, .length = t.len };
        off += t.len;
    }
    return .{
        .desc = .{
            .struct_size = @sizeOf(c_api.TokenizerDesc),
            .vocab_size = spec.vocab,
            .entries = entries.ptr,
            .entry_count = entries.len,
            .blob = blob.ptr,
            .blob_len = blob_len,
            .eos_ids = spec.eos.ptr,
            .eos_count = spec.eos.len,
            .special_ids = spec.special.ptr,
            .special_count = spec.special.len,
        },
        .entries = entries,
        .blob = blob,
    };
}

fn makeError() c_api.ZgError {
    var e: c_api.ZgError = .{
        .struct_size = @sizeOf(c_api.ZgError),
        .code = 0,
        .schema_offset = c_api.NO_OFFSET,
        .message = undefined,
        .json_pointer = undefined,
    };
    e.message[0] = 0;
    e.json_pointer[0] = 0;
    return e;
}

fn checkStatus(st: c_api.Status, where: []const u8) void {
    const v = @intFromEnum(st);
    if (v < 0 or v > 13)
        failInvariant("status code {d} out of range at {s}", .{ v, where });
}

fn maskBit(words: []const u32, id: u32) bool {
    return (words[id / 32] >> @as(u5, @intCast(id % 32))) & 1 == 1;
}

// ---------------------------------------------------------------------------
// Campaign A: compiler
// ---------------------------------------------------------------------------

fn campaignCompile(rng: std.Random) !void {
    var arena = std.heap.ArenaAllocator.init(page);
    defer arena.deinit();
    var corpus_saved: u64 = 0;
    var i: u64 = 0;
    while (i < cfg.compile_iters) : (i += 1) {
        _ = arena.reset(.retain_capacity);
        const a = arena.allocator();
        var buf: std.ArrayList(u8) = .{};
        const sel = rng.uintLessThan(u32, 100);
        const use_literals = sel >= 85;
        if (use_literals) {
            try genLiteralsJson(a, &buf, rng, rng.uintLessThan(u32, 100) < 60);
        } else if (sel < 55) {
            try genSchema(a, &buf, rng, rng.uintLessThan(u32, 100) < 70);
        } else {
            try genGarbage(a, &buf, rng);
        }
        const bytes = buf.items;

        var fa = FailingAllocator.init(page, .{});
        var diag: schema.Diagnostic = .{};
        var w = work_mod.Work{};
        const res: schema.CompileError!grammar.Grammar = if (use_literals)
            literals.compileLiterals(fa.allocator(), bytes, 64, &diag, &w)
        else
            schema.compile(fa.allocator(), bytes, 64, &diag, &w);
        if (res) |g| {
            var gg = g;
            counters.compile_ok += 1;
            // id determinism: recompiling the same bytes gives the same id
            if (i % 997 == 0) {
                var fa2 = FailingAllocator.init(page, .{});
                var diag2: schema.Diagnostic = .{};
                var w2 = work_mod.Work{};
                const res2: schema.CompileError!grammar.Grammar = if (use_literals)
                    literals.compileLiterals(fa2.allocator(), bytes, 64, &diag2, &w2)
                else
                    schema.compile(fa2.allocator(), bytes, 64, &diag2, &w2);
                if (res2) |g2| {
                    var gg2 = g2;
                    if (gg2.id != gg.id)
                        artifactAndDie("grammar id not deterministic (iter {d})", .{i}, "crash_nondet_id.json", bytes);
                    gg2.deinit();
                } else |_| {
                    artifactAndDie("second compile of same bytes failed (iter {d})", .{i}, "crash_second_compile.json", bytes);
                }
                if (fa2.allocated_bytes != fa2.freed_bytes)
                    artifactAndDie("leak in second compile (iter {d})", .{i}, "crash_leak2.json", bytes);
            }
            if (corpus_saved < 400 and counters.compile_ok % 500 == 0) {
                var name_buf: [64]u8 = undefined;
                const name = std.fmt.bufPrint(&name_buf, "schema_{d}.json", .{corpus_saved}) catch continue;
                std.fs.cwd().makePath(cfg.corpus_dir) catch {};
                var path_buf: [600]u8 = undefined;
                const path = std.fmt.bufPrint(&path_buf, "{s}/{s}", .{ cfg.corpus_dir, name }) catch continue;
                std.fs.cwd().writeFile(.{ .sub_path = path, .data = bytes }) catch {};
                corpus_saved += 1;
            }
            gg.deinit();
        } else |_| {
            counters.compile_err += 1;
        }
        if (fa.allocated_bytes != fa.freed_bytes)
            artifactAndDie("compile leak: allocated={d} freed={d} (iter {d})", .{ fa.allocated_bytes, fa.freed_bytes, i }, "crash_compile_leak.json", bytes);
        counters.compile_iters += 1;
    }
}

// ---------------------------------------------------------------------------
// Campaign B: allocator failure injection
// ---------------------------------------------------------------------------

fn campaignInjection(rng: std.Random) !void {
    var arena = std.heap.ArenaAllocator.init(page);
    defer arena.deinit();
    var s: u64 = 0;
    while (s < cfg.fail_schemas) : (s += 1) {
        _ = arena.reset(.retain_capacity);
        const a = arena.allocator();
        var buf: std.ArrayList(u8) = .{};
        const use_literals = rng.boolean();
        if (use_literals)
            try genLiteralsJson(a, &buf, rng, true)
        else
            try genSchema(a, &buf, rng, true);
        const bytes = buf.items;
        var k: usize = 0;
        while (true) {
            var fa = FailingAllocator.init(page, .{ .fail_index = k });
            var diag: schema.Diagnostic = .{};
            var w = work_mod.Work{};
            const res: schema.CompileError!grammar.Grammar = if (use_literals)
                literals.compileLiterals(fa.allocator(), bytes, 64, &diag, &w)
            else
                schema.compile(fa.allocator(), bytes, 64, &diag, &w);
            if (res) |g| {
                var gg = g;
                gg.deinit();
                if (fa.allocated_bytes != fa.freed_bytes)
                    artifactAndDie("leak after successful injected compile (schema {d}, k={d})", .{ s, k }, "crash_inj_ok_leak.json", bytes);
                break;
            } else |e| {
                if (e != error.OutOfMemory and e != error.ResourceLimit)
                    artifactAndDie("injected failure gave {s} instead of OutOfMemory (schema {d}, k={d})", .{ @errorName(e), s, k }, "crash_inj_wrong_err.json", bytes);
                if (fa.allocated_bytes != fa.freed_bytes)
                    artifactAndDie("leak under injected failure (schema {d}, k={d}): allocated={d} freed={d}", .{ s, k, fa.allocated_bytes, fa.freed_bytes }, "crash_inj_leak.json", bytes);
                counters.injected_failures += 1;
                k += 1;
                if (k > 5000)
                    artifactAndDie("compile needs >5000 allocations (schema {d})", .{s}, "crash_inj_runaway.json", bytes);
            }
        }
        counters.injected_cases += 1;
    }

    // injection into tokenizer preparation
    var t: u64 = 0;
    while (t < cfg.fail_tokenizers) : (t += 1) {
        _ = arena.reset(.retain_capacity);
        const a = arena.allocator();
        const spec = try genTokenizer(a, rng, false);
        const entries = try a.alloc(tokenizer.Entry, spec.tokens.len);
        for (spec.tokens, 0..) |tok, j| entries[j] = .{ .id = @intCast(j), .bytes = tok };
        var k: usize = 0;
        while (true) {
            var fa = FailingAllocator.init(page, .{ .fail_index = k });
            const res = tokenizer.Tokenizer.create(fa.allocator(), spec.vocab, entries, spec.eos, spec.special);
            if (res) |tk| {
                var tkk = tk;
                tkk.deinit();
                if (fa.allocated_bytes != fa.freed_bytes)
                    failInvariant("tokenizer leak after success (case {d}, k={d})", .{ t, k });
                break;
            } else |e| {
                if (e != error.OutOfMemory)
                    failInvariant("tokenizer injected failure gave {s} (case {d}, k={d})", .{ @errorName(e), t, k });
                if (fa.allocated_bytes != fa.freed_bytes)
                    failInvariant("tokenizer leak under injected failure (case {d}, k={d})", .{ t, k });
                counters.injected_failures += 1;
                k += 1;
                if (k > 5000) failInvariant("tokenizer needs >5000 allocations", .{});
            }
        }
        counters.injected_cases += 1;
    }
}

// ---------------------------------------------------------------------------
// Campaign C: accept/mask through the C ABI
// ---------------------------------------------------------------------------

fn genContextConfig(rng: std.Random, harsh: bool) c_api.ContextConfig {
    var c = std.mem.zeroes(c_api.ContextConfig);
    c.struct_size = @sizeOf(c_api.ContextConfig);
    c.version = c_api.ABI_VERSION;
    c.mode = rng.uintLessThan(u32, 3);
    if (harsh) {
        c.memory_limit_bytes = rng.intRangeAtMost(u64, 1 << 20, 16 << 20);
        c.session_limit_bytes = rng.intRangeAtMost(u64, 256 << 10, 4 << 20);
        c.schema_limit_bytes = rng.intRangeAtMost(u64, 4 << 10, 256 << 10);
        c.cache_limit_bytes = 1 + rng.uintLessThan(u64, c.memory_limit_bytes / 2);
        c.max_depth = rng.intRangeAtMost(u32, 4, 64);
        c.max_threads_per_state = rng.intRangeAtMost(u32, 1, 8);
    } else {
        if (rng.uintLessThan(u32, 100) < 30)
            c.max_threads_per_state = rng.intRangeAtMost(u32, 1, 64);
        if (rng.uintLessThan(u32, 100) < 20)
            c.max_depth = rng.intRangeAtMost(u32, 4, 64);
        if (rng.uintLessThan(u32, 100) < 50)
            c.cache_limit_bytes = c_api.CACHE_DEFAULT;
    }
    return c;
}

fn dumpRepro(a: std.mem.Allocator, spec: TokSpec, schema_bytes: []const u8) []u8 {
    var buf: std.ArrayList(u8) = .{};
    buf.appendSlice(a, "{\"schema\":") catch return schema_bytes;
    buf.appendSlice(a, schema_bytes) catch return schema_bytes;
    buf.appendSlice(a, ",\"tokens\":[") catch return schema_bytes;
    for (spec.tokens, 0..) |t, i| {
        if (i > 0) buf.append(a, ',') catch return schema_bytes;
        writeJsonString(&buf, a, t) catch return schema_bytes;
    }
    buf.print(a, "],\"eos\":{d},\"special\":{d}}}", .{ spec.eos[0], spec.special[0] }) catch return schema_bytes;
    return buf.items;
}

fn checkMaskInvariants(s: *c_api.Session, spec: TokSpec, m: []const u32, schema_bytes: []const u8) void {
    // tail bits of the last word must be zero
    var id: u32 = spec.vocab;
    while (id < m.len * 32) : (id += 1) {
        if (maskBit(m, id))
            artifactAndDie("tail bit {d} set (vocab={d})", .{ id, spec.vocab }, "crash_tail_bits.json", schema_bytes);
    }
    for (spec.special) |sp| {
        if (maskBit(m, sp))
            artifactAndDie("special token {d} allowed by mask", .{sp}, "crash_special_bit.json", schema_bytes);
    }
    var ce = false;
    const st = c_api.zg_can_end(s, &ce);
    if (st != .ok)
        artifactAndDie("can_end failed on active session: {s}", .{@tagName(st)}, "crash_can_end.json", schema_bytes);
    var any = false;
    for (spec.eos) |e| {
        if (maskBit(m, e) != ce)
            artifactAndDie("eos bit {d} != can_end {}", .{ e, ce }, "crash_eos_parity.json", schema_bytes);
    }
    id = 0;
    while (id < spec.vocab) : (id += 1) {
        if (maskBit(m, id)) {
            any = true;
            break;
        }
    }
    if (!any)
        artifactAndDie("ok mask has no allowed tokens", .{}, "crash_empty_mask.json", schema_bytes);
}

/// After a failed accept the state must be unchanged: same mask bitwise.
fn refillCompare(s: *c_api.Session, buf: []u32, prev: []const u32, err: *c_api.ZgError, schema_bytes: []const u8) void {
    counters.walk_api_calls += 1;
    counters.walk_masks += 1;
    const st = c_api.zg_fill_mask(s, buf.ptr, buf.len, err);
    if (st != .ok)
        artifactAndDie("refill after failed accept gave {s} (state changed?)", .{@tagName(st)}, "crash_refill.json", schema_bytes);
    if (!std.mem.eql(u32, buf, prev))
        artifactAndDie("mask changed after failed accept (partial acceptance)", .{}, "crash_partial_accept.json", schema_bytes);
}

fn runWalk(a: std.mem.Allocator, rng: std.Random, ctx: *c_api.Context, gh: *c_api.GrammarHandle, spec: TokSpec, schema_bytes: []const u8) !void {
    var err = makeError();
    var sess: ?*c_api.Session = null;
    counters.walk_api_calls += 1;
    var st = c_api.zg_session_create(ctx, gh, &sess, &err);
    checkStatus(st, "session_create");
    if (st != .ok) {
        counters.walk_resource_limits += 1;
        return;
    }
    counters.walk_sessions += 1;
    const s = sess.?;
    const words: usize = (@as(usize, spec.vocab) + 31) / 32;
    const buf = try a.alloc(u32, words + 1);
    const mbuf = buf[0..words];
    const prev = try a.alloc(u32, words);
    var done = false;
    var step: u32 = 0;
    while (step < 24 and !done) : (step += 1) {
        if (rng.uintLessThan(u32, 100) < 4) {
            counters.walk_api_calls += 1;
            st = c_api.zg_fill_mask(s, mbuf.ptr, words -| 1, &err);
            if (st != .buffer_too_small)
                artifactAndDie("short mask buffer gave {s}", .{@tagName(st)}, "crash_short_buf.json", schema_bytes);
        }
        if (rng.uintLessThan(u32, 100) < 3) {
            const mis: [*]align(1) u32 = @ptrFromInt(@intFromPtr(buf.ptr) + 1);
            counters.walk_api_calls += 1;
            st = c_api.zg_fill_mask(s, mis, words, &err);
            if (st != .invalid_argument)
                artifactAndDie("misaligned mask ptr gave {s}", .{@tagName(st)}, "crash_misaligned.json", schema_bytes);
        }
        counters.walk_api_calls += 1;
        counters.walk_masks += 1;
        st = c_api.zg_fill_mask(s, mbuf.ptr, words, &err);
        checkStatus(st, "fill_mask");
        if (st == .dead_end) {
            counters.walk_dead_ends += 1;
            break;
        }
        if (st == .resource_limit) {
            counters.walk_resource_limits += 1;
            break;
        }
        if (st != .ok)
            artifactAndDie("fill_mask gave {s}", .{@tagName(st)}, "crash_fill.json", schema_bytes);
        checkMaskInvariants(s, spec, mbuf, schema_bytes);
        @memcpy(prev, mbuf);

        // allowed regular tokens
        var allowed: std.ArrayList(u32) = .{};
        var id: u32 = 0;
        while (id < spec.vocab) : (id += 1) {
            if (maskBit(mbuf, id) and id != spec.eos[0]) try allowed.append(a, id);
        }
        const eos_allowed = maskBit(mbuf, spec.eos[0]);
        const action = rng.uintLessThan(u32, 100);

        if (action < 58) {
            // accept an allowed token
            const tok = if (allowed.items.len == 0) spec.eos[0] else allowed.items[rng.uintLessThan(usize, allowed.items.len)];
            counters.walk_api_calls += 1;
            counters.walk_accepts += 1;
            st = c_api.zg_accept_token(s, tok, &err);
            if (st == .resource_limit) {
                refillCompare(s, mbuf, prev, &err, schema_bytes);
                counters.walk_resource_limits += 1;
            } else if (st != .ok) {
                artifactAndDie("allowed token {d} rejected with {s}", .{ tok, @tagName(st) }, "crash_allowed_rejected.json", schema_bytes);
            }
        } else if (action < 70) {
            // EOS / finish path
            if (eos_allowed) {
                counters.walk_api_calls += 2;
                st = c_api.zg_accept_token(s, spec.eos[0], &err);
                if (st != .ok)
                    artifactAndDie("eos accept gave {s}", .{@tagName(st)}, "crash_eos_accept.json", schema_bytes);
                st = c_api.zg_finish(s, &err);
                if (st != .ok)
                    artifactAndDie("finish after eos gave {s}", .{@tagName(st)}, "crash_finish.json", schema_bytes);
                // post-finish wrong_state
                counters.walk_api_calls += 3;
                if (c_api.zg_accept_token(s, 0, &err) != .wrong_state)
                    artifactAndDie("accept after finish not wrong_state", .{}, "crash_post_finish.json", schema_bytes);
                if (c_api.zg_fill_mask(s, mbuf.ptr, words, &err) != .wrong_state)
                    artifactAndDie("fill after finish not wrong_state", .{}, "crash_post_finish.json", schema_bytes);
                if (c_api.zg_finish(s, &err) != .ok)
                    artifactAndDie("re-finish not ok", .{}, "crash_post_finish.json", schema_bytes);
                done = true;
            } else {
                counters.walk_api_calls += 2;
                st = c_api.zg_accept_token(s, spec.eos[0], &err);
                if (st != .invalid_token)
                    artifactAndDie("premature eos gave {s}", .{@tagName(st)}, "crash_premature_eos.json", schema_bytes);
                refillCompare(s, mbuf, prev, &err, schema_bytes);
                if (c_api.zg_finish(s, &err) != .wrong_state)
                    artifactAndDie("finish without can_end not wrong_state", .{}, "crash_finish_state.json", schema_bytes);
            }
        } else if (action < 84) {
            // a disallowed regular token (if any)
            var disallowed: ?u32 = null;
            id = 0;
            while (id < spec.vocab) : (id += 1) {
                const is_special = id == spec.special[0];
                if (!maskBit(mbuf, id) and id != spec.eos[0] and !is_special) {
                    disallowed = id;
                    break;
                }
            }
            if (disallowed) |tok| {
                counters.walk_api_calls += 1;
                st = c_api.zg_accept_token(s, tok, &err);
                if (st != .invalid_token)
                    artifactAndDie("disallowed token {d} gave {s}", .{ tok, @tagName(st) }, "crash_disallowed_accepted.json", schema_bytes);
                refillCompare(s, mbuf, prev, &err, schema_bytes);
            }
        } else if (action < 90) {
            // id outside vocab
            const tok = switch (rng.uintLessThan(u32, 3)) {
                0 => spec.vocab,
                1 => spec.vocab + rng.uintLessThan(u32, 1000),
                else => std.math.maxInt(u32),
            };
            counters.walk_api_calls += 1;
            st = c_api.zg_accept_token(s, tok, &err);
            if (st != .invalid_token)
                artifactAndDie("out-of-range token {d} gave {s}", .{ tok, @tagName(st) }, "crash_oob_token.json", schema_bytes);
        } else if (action < 94) {
            // special token inside the document
            counters.walk_api_calls += 1;
            st = c_api.zg_accept_token(s, spec.special[0], &err);
            if (st != .invalid_token)
                artifactAndDie("special token gave {s}", .{@tagName(st)}, "crash_special_accept.json", schema_bytes);
            refillCompare(s, mbuf, prev, &err, schema_bytes);
        } else if (action < 97) {
            // early finish when can_end
            if (eos_allowed) {
                counters.walk_api_calls += 1;
                st = c_api.zg_finish(s, &err);
                if (st != .ok)
                    artifactAndDie("early finish gave {s}", .{@tagName(st)}, "crash_early_finish.json", schema_bytes);
                done = true;
            }
        } else {
            // abort
            counters.walk_api_calls += 4;
            if (c_api.zg_abort(s) != .ok)
                artifactAndDie("abort not ok", .{}, "crash_abort.json", schema_bytes);
            if (c_api.zg_accept_token(s, 0, &err) != .wrong_state)
                artifactAndDie("accept after abort not wrong_state", .{}, "crash_post_abort.json", schema_bytes);
            if (c_api.zg_fill_mask(s, mbuf.ptr, words, &err) != .wrong_state)
                artifactAndDie("fill after abort not wrong_state", .{}, "crash_post_abort.json", schema_bytes);
            var ce = false;
            if (c_api.zg_can_end(s, &ce) != .wrong_state)
                artifactAndDie("can_end after abort not wrong_state", .{}, "crash_post_abort.json", schema_bytes);
            done = true;
        }
    }
    counters.walk_api_calls += 1;
    c_api.zg_session_destroy(s);
}

fn runBatchWalk(a: std.mem.Allocator, rng: std.Random, ctx: *c_api.Context, gh: *c_api.GrammarHandle, spec: TokSpec, schema_bytes: []const u8) !void {
    var err = makeError();
    var sess: [3]?*c_api.Session = .{ null, null, null };
    var created: usize = 0;
    var st: c_api.Status = .ok;
    for (0..3) |i| {
        counters.walk_api_calls += 1;
        st = c_api.zg_session_create(ctx, gh, &sess[i], &err);
        if (st != .ok) break;
        created += 1;
    }
    counters.walk_sessions += @intCast(created);
    if (created < 3) {
        for (0..created) |i| c_api.zg_session_destroy(sess[i]);
        counters.walk_resource_limits += 1;
        return;
    }
    const words: usize = (@as(usize, spec.vocab) + 31) / 32;
    var mask_bufs: [3][]u32 = undefined;
    var mask_ptrs: [3]?[*]align(1) u32 = undefined;
    for (0..3) |i| {
        mask_bufs[i] = try a.alloc(u32, words);
        mask_ptrs[i] = mask_bufs[i].ptr;
    }
    const single = try a.alloc(u32, words);
    var step: u32 = 0;
    while (step < 10) : (step += 1) {
        var sts: [3]c_int = .{ -1, -1, -1 };
        counters.walk_api_calls += 1;
        counters.walk_masks += 3;
        st = c_api.zg_fill_masks_batch(&sess, &mask_ptrs, words, &sts, 3, &err);
        checkStatus(st, "batch");
        if (st == .dead_end or st == .resource_limit) {
            for (sts) |x| {
                if (x != @intFromEnum(st))
                    artifactAndDie("batch statuses diverge", .{}, "crash_batch.json", schema_bytes);
            }
            break;
        }
        if (st != .ok)
            artifactAndDie("batch fill gave {s}", .{@tagName(st)}, "crash_batch.json", schema_bytes);
        // three identical states -> three identical masks, equal to the individual fill
        counters.walk_api_calls += 1;
        counters.walk_masks += 1;
        st = c_api.zg_fill_mask(sess[0], single.ptr, words, &err);
        if (st != .ok)
            artifactAndDie("individual fill after batch gave {s}", .{@tagName(st)}, "crash_batch.json", schema_bytes);
        for (0..3) |i| {
            if (!std.mem.eql(u32, mask_bufs[i], single))
                artifactAndDie("batch mask {d} != individual mask", .{i}, "crash_batch_mismatch.json", schema_bytes);
        }
        var allowed: std.ArrayList(u32) = .{};
        var id: u32 = 0;
        while (id < spec.vocab) : (id += 1) {
            if (maskBit(single, id) and id != spec.eos[0]) try allowed.append(a, id);
        }
        const tok = if (allowed.items.len == 0) spec.eos[0] else allowed.items[rng.uintLessThan(usize, allowed.items.len)];
        var all_ok = true;
        for (0..3) |i| {
            counters.walk_api_calls += 1;
            counters.walk_accepts += 1;
            st = c_api.zg_accept_token(sess[i], tok, &err);
            if (st != .ok) all_ok = false;
        }
        if (!all_ok) break;
        if (tok == spec.eos[0]) {
            for (0..3) |i| {
                counters.walk_api_calls += 1;
                if (c_api.zg_finish(sess[i], &err) != .ok)
                    artifactAndDie("batch finish failed", .{}, "crash_batch_finish.json", schema_bytes);
            }
            break;
        }
    }
    for (0..3) |i| {
        counters.walk_api_calls += 1;
        c_api.zg_session_destroy(sess[i]);
    }
}

fn campaignWalk(rng: std.Random) !void {
    var arena = std.heap.ArenaAllocator.init(page);
    defer arena.deinit();
    var i: u64 = 0;
    while (i < cfg.walk_sessions) : (i += 1) {
        _ = arena.reset(.retain_capacity);
        const a = arena.allocator();
        const spec = try genTokenizer(a, rng, false);
        const keep = try descOf(a, spec);
        var config = genContextConfig(rng, rng.uintLessThan(u32, 100) < 30);
        var err = makeError();
        var ctx: ?*c_api.Context = null;
        counters.walk_api_calls += 1;
        var st = c_api.zg_context_create(&config, &keep.desc, &ctx, &err);
        checkStatus(st, "context_create");
        if (st != .ok) {
            if (st != .invalid_argument and st != .resource_limit and st != .unsupported_tokenizer)
                failInvariant("context_create gave {s}", .{@tagName(st)});
            continue;
        }
        const context = ctx.?;
        var sbuf: std.ArrayList(u8) = .{};
        try genSchema(a, &sbuf, rng, rng.uintLessThan(u32, 100) < 85);
        var req: c_api.CompileRequest = .{
            .struct_size = @sizeOf(c_api.CompileRequest),
            .kind = 0,
            .profile = if (rng.uintLessThan(u32, 100) < 8) "canonical-v1" else null,
            .data = sbuf.items.ptr,
            .data_len = sbuf.items.len,
        };
        var gh: ?*c_api.GrammarHandle = null;
        counters.walk_api_calls += 1;
        st = c_api.zg_compile(context, &req, &gh, &err);
        checkStatus(st, "compile");
        if (st != .ok) {
            if (gh != null)
                artifactAndDie("out_grammar set on failed compile", .{}, "crash_compile_out.json", sbuf.items);
            counters.walk_compile_failures += 1;
            counters.walk_api_calls += 1;
            if (c_api.zg_context_destroy(context) != .ok)
                failInvariant("context destroy failed after failed compile", .{});
            continue;
        }
        const grammar_h = gh.?;
        if (rng.uintLessThan(u32, 100) < 12)
            try runBatchWalk(a, rng, context, grammar_h, spec, sbuf.items)
        else
            try runWalk(a, rng, context, grammar_h, spec, sbuf.items);

        // occasionally destroy with live children -> busy
        const busy_probe = rng.uintLessThan(u32, 100) < 6;
        if (busy_probe) {
            counters.walk_api_calls += 1;
            if (c_api.zg_context_destroy(context) != .busy)
                artifactAndDie("destroy with live grammar not busy", .{}, "crash_busy.json", sbuf.items);
        }
        counters.walk_api_calls += 1;
        c_api.zg_grammar_release(grammar_h);
        var stats = std.mem.zeroes(c_api.StatsC);
        stats.struct_size = @sizeOf(c_api.StatsC);
        counters.walk_api_calls += 1;
        st = c_api.zg_get_stats(context, &stats);
        if (st != .ok)
            artifactAndDie("get_stats gave {s}", .{@tagName(st)}, "crash_stats.json", sbuf.items);
        if (stats.mem_used[1] != 0 or stats.mem_used[2] != 0)
            artifactAndDie("memory leak: grammar_used={d} session_used={d}", .{ stats.mem_used[1], stats.mem_used[2] }, "crash_walk_leak.json", sbuf.items);
        counters.walk_api_calls += 1;
        if (c_api.zg_context_destroy(context) != .ok)
            artifactAndDie("context destroy failed", .{}, "crash_destroy.json", sbuf.items);
    }
}

// ---------------------------------------------------------------------------
// Campaign D: cache parity (lazy / adaptive / precompute), 1 and 4 threads
// ---------------------------------------------------------------------------

const NCTX = 4;

var cache_compile_mutex: std.Thread.Mutex = .{};

fn cacheContextConfigs() [NCTX]c_api.ContextConfig {
    var out: [NCTX]c_api.ContextConfig = undefined;
    for (0..NCTX) |i| {
        var c = std.mem.zeroes(c_api.ContextConfig);
        c.struct_size = @sizeOf(c_api.ContextConfig);
        c.version = c_api.ABI_VERSION;
        out[i] = c;
    }
    out[0].mode = 0; // lazy
    out[1].mode = 1; // adaptive, 64 MiB cache
    out[2].mode = 1; // adaptive, tiny cache (~5 entries) -> constant evictions
    out[2].memory_limit_bytes = 64 << 20;
    out[2].cache_limit_bytes = 500_000;
    out[3].mode = 2; // precompute (alias of adaptive)
    return out;
}

/// One walk: one schema, one token trace, synchronously in all NCTX
/// contexts. Masks and statuses must match bitwise. Returns the trace
/// checksum.
fn cacheWalk(a: std.mem.Allocator, walk_seed: u64, ctxs: *const [NCTX]*c_api.Context, spec: TokSpec) !u64 {
    var prng = std.Random.DefaultPrng.init(walk_seed);
    const rng = prng.random();
    var err = makeError();

    var sbuf: std.ArrayList(u8) = .{};
    try genSchema(a, &sbuf, rng, true);
    const schema_bytes = sbuf.items;

    var gh: [NCTX]?*c_api.GrammarHandle = .{ null, null, null, null };
    var sess: [NCTX]?*c_api.Session = .{ null, null, null, null };
    defer {
        for (0..NCTX) |i| {
            if (sess[i]) |s| c_api.zg_session_destroy(s);
            if (gh[i]) |g| c_api.zg_grammar_release(g);
        }
    }
    for (0..NCTX) |i| {
        var req: c_api.CompileRequest = .{
            .struct_size = @sizeOf(c_api.CompileRequest),
            .kind = 0,
            .profile = null,
            .data = schema_bytes.ptr,
            .data_len = schema_bytes.len,
        };
        cache_compile_mutex.lock();
        const st = c_api.zg_compile(ctxs[i], &req, &gh[i], &err);
        cache_compile_mutex.unlock();
        if (st != .ok)
            artifactAndDie("cache walk: must_compile schema rejected with {s}", .{@tagName(st)}, "crash_cache_compile.json", schema_bytes);
        const st2 = c_api.zg_session_create(ctxs[i], gh[i], &sess[i], &err);
        if (st2 != .ok)
            artifactAndDie("cache walk: session_create gave {s}", .{@tagName(st2)}, "crash_cache_session.json", schema_bytes);
    }

    const words: usize = (@as(usize, spec.vocab) + 31) / 32;
    var masks: [NCTX][]u32 = undefined;
    for (0..NCTX) |i| masks[i] = try a.alloc(u32, words);

    var h: u64 = 0xcbf29ce484222325;
    var step: u32 = 0;
    while (step < 24) : (step += 1) {
        var statuses: [NCTX]c_api.Status = undefined;
        for (0..NCTX) |i| {
            counters.walk_api_calls += 1;
            statuses[i] = c_api.zg_fill_mask(sess[i], masks[i].ptr, words, &err);
        }
        for (1..NCTX) |i| {
            if (statuses[i] != statuses[0])
                artifactAndDie("cache parity: status {s} vs {s} at step {d}", .{ @tagName(statuses[i]), @tagName(statuses[0]), step }, "crash_cache_status.json", schema_bytes);
        }
        if (statuses[0] != .ok) break; // dead_end: identical in all contexts
        for (1..NCTX) |i| {
            counters.cache_mask_cmps += 1;
            if (!std.mem.eql(u32, masks[i], masks[0]))
                artifactAndDie("cache parity: mask of ctx {d} differs at step {d}", .{ i, step }, "crash_cache_mask.json", schema_bytes);
        }
        checkMaskInvariants(sess[0].?, spec, masks[0], schema_bytes);
        h = grammar.fnv1a64Update(h, std.mem.sliceAsBytes(masks[0]));

        var allowed: std.ArrayList(u32) = .{};
        var id: u32 = 0;
        while (id < spec.vocab) : (id += 1) {
            if (maskBit(masks[0], id) and id != spec.eos[0]) try allowed.append(a, id);
        }
        const eos_allowed = maskBit(masks[0], spec.eos[0]);
        if (allowed.items.len == 0 and !eos_allowed) break;
        const finish_now = eos_allowed and (allowed.items.len == 0 or rng.uintLessThan(u32, 100) < 12);
        const tok = if (finish_now) spec.eos[0] else allowed.items[rng.uintLessThan(usize, allowed.items.len)];
        for (0..NCTX) |i| {
            counters.walk_api_calls += 1;
            const st = c_api.zg_accept_token(sess[i], tok, &err);
            if (st != .ok)
                artifactAndDie("cache walk: allowed token {d} rejected in ctx {d} with {s}", .{ tok, i, @tagName(st) }, "crash_cache_accept.json", schema_bytes);
        }
        h = grammar.fnv1a64Update(h, std.mem.asBytes(&tok));
        if (finish_now) {
            for (0..NCTX) |i| {
                const st = c_api.zg_finish(sess[i], &err);
                if (st != .ok)
                    artifactAndDie("cache walk: finish in ctx {d} gave {s}", .{ i, @tagName(st) }, "crash_cache_finish.json", schema_bytes);
            }
            break;
        }
    }
    counters.cache_walks += 1;
    return h;
}

fn cachePhase(a: std.mem.Allocator, seed_base: u64, walks: []const u64, checksums: []u64, ctxs: *const [NCTX]*c_api.Context, spec: TokSpec) !void {
    var arena = std.heap.ArenaAllocator.init(page);
    defer arena.deinit();
    _ = a;
    for (walks) |w| {
        _ = arena.reset(.retain_capacity);
        checksums[w] = try cacheWalk(arena.allocator(), seed_base +% w, ctxs, spec);
    }
}

const ThreadJob = struct {
    seed_base: u64,
    walks: []const u64,
    checksums: []u64,
    ctxs: *const [NCTX]*c_api.Context,
    spec: TokSpec,
    err: ?anyerror = null,
};

fn cacheThreadMain(job: *ThreadJob) void {
    var arena = std.heap.ArenaAllocator.init(page);
    defer arena.deinit();
    for (job.walks) |w| {
        _ = arena.reset(.retain_capacity);
        job.checksums[w] = cacheWalk(arena.allocator(), job.seed_base +% w, job.ctxs, job.spec) catch |e| {
            job.err = e;
            return;
        };
    }
}

fn campaignCache(rng: std.Random) !void {
    _ = rng;
    var arena = std.heap.ArenaAllocator.init(page);
    defer arena.deinit();
    const a = arena.allocator();

    // fixed campaign tokenizer, byte-complete so every valid schema passes
    // the compile-time coverage gate (lives until the end)
    const spec = try genByteTokenizer(a);
    const keep = try descOf(a, spec);
    const configs = cacheContextConfigs();
    const n_walks: u64 = cfg.cache_walks;
    const walks = try a.alloc(u64, n_walks);
    for (walks, 0..) |*w, i| w.* = i;
    const sums_single = try a.alloc(u64, n_walks);
    const sums_threaded = try a.alloc(u64, n_walks);

    // phase 1: single thread
    {
        var ctxs: [NCTX]*c_api.Context = undefined;
        var err = makeError();
        for (0..NCTX) |i| {
            var ctx: ?*c_api.Context = null;
            const st = c_api.zg_context_create(&configs[i], &keep.desc, &ctx, &err);
            if (st != .ok) failInvariant("cache ctx {d} create: {s}", .{ i, @tagName(st) });
            ctxs[i] = ctx.?;
        }
        try cachePhase(a, cfg.seed ^ 0xCACE_0001, walks, sums_single, &ctxs, spec);
        for (0..NCTX) |i| {
            if (c_api.zg_context_destroy(ctxs[i]) != .ok)
                failInvariant("cache ctx {d} destroy failed (live handles?)", .{i});
        }
    }

    // phase 2: 4 threads, same walk seeds
    {
        var ctxs: [NCTX]*c_api.Context = undefined;
        var err = makeError();
        for (0..NCTX) |i| {
            var ctx: ?*c_api.Context = null;
            const st = c_api.zg_context_create(&configs[i], &keep.desc, &ctx, &err);
            if (st != .ok) failInvariant("cache ctx {d} create (threaded): {s}", .{ i, @tagName(st) });
            ctxs[i] = ctx.?;
        }
        var jobs: [4]ThreadJob = undefined;
        var threads: [4]?std.Thread = .{ null, null, null, null };
        for (0..4) |t| {
            var tw: std.ArrayList(u64) = .{};
            var w: u64 = t;
            while (w < n_walks) : (w += 4) try tw.append(a, w);
            jobs[t] = .{
                .seed_base = cfg.seed ^ 0xCACE_0001,
                .walks = tw.items,
                .checksums = sums_threaded,
                .ctxs = &ctxs,
                .spec = spec,
            };
            threads[t] = std.Thread.spawn(.{}, cacheThreadMain, .{&jobs[t]}) catch null;
        }
        var spawned: usize = 0;
        for (threads) |t| {
            if (t) |th| {
                th.join();
                spawned += 1;
            }
        }
        if (spawned < 4) {
            // fallback: finish the rest sequentially
            for (0..4) |t| {
                if (threads[t] == null and jobs[t].err == null and jobs[t].walks.len > 0)
                    cacheThreadMain(&jobs[t]);
            }
        }
        for (0..4) |t| {
            if (jobs[t].err) |e| failInvariant("cache thread error: {s}", .{@errorName(e)});
        }
        for (0..NCTX) |i| {
            if (c_api.zg_context_destroy(ctxs[i]) != .ok)
                failInvariant("cache ctx {d} destroy failed (threaded)", .{i});
        }
    }

    for (0..n_walks) |w| {
        if (sums_single[w] != sums_threaded[w])
            failInvariant("threaded cache checksum mismatch at walk {d}: {x} vs {x}", .{ w, sums_single[w], sums_threaded[w] });
    }
    counters.threaded_match = true;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

fn argValue(args: [][:0]u8, i: *usize) ?[]const u8 {
    const arg = args[i.*];
    if (std.mem.indexOfScalar(u8, arg, '=')) |eq| return arg[eq + 1 ..];
    if (i.* + 1 < args.len) {
        i.* += 1;
        return args[i.*];
    }
    return null;
}

pub fn main() !void {
    var arena = std.heap.ArenaAllocator.init(page);
    const a = arena.allocator();
    const args = try std.process.argsAlloc(a);
    var i: usize = 1;
    while (i < args.len) : (i += 1) {
        const arg = args[i];
        const num = struct {
            fn get(as: [][:0]u8, ip: *usize) ?u64 {
                const v = argValue(as, ip) orelse return null;
                return std.fmt.parseInt(u64, v, 10) catch null;
            }
        }.get;
        if (std.mem.startsWith(u8, arg, "--seed")) {
            cfg.seed = num(args, &i) orelse cfg.seed;
        } else if (std.mem.startsWith(u8, arg, "--compile-iters")) {
            cfg.compile_iters = num(args, &i) orelse cfg.compile_iters;
        } else if (std.mem.startsWith(u8, arg, "--fail-schemas")) {
            cfg.fail_schemas = num(args, &i) orelse cfg.fail_schemas;
        } else if (std.mem.startsWith(u8, arg, "--fail-tokenizers")) {
            cfg.fail_tokenizers = num(args, &i) orelse cfg.fail_tokenizers;
        } else if (std.mem.startsWith(u8, arg, "--walk-sessions")) {
            cfg.walk_sessions = num(args, &i) orelse cfg.walk_sessions;
        } else if (std.mem.startsWith(u8, arg, "--batch-walks")) {
            cfg.batch_walks = num(args, &i) orelse cfg.batch_walks;
        } else if (std.mem.startsWith(u8, arg, "--cache-walks")) {
            cfg.cache_walks = num(args, &i) orelse cfg.cache_walks;
        } else if (std.mem.startsWith(u8, arg, "--report")) {
            cfg.report = argValue(args, &i) orelse cfg.report;
        } else if (std.mem.startsWith(u8, arg, "--artifacts")) {
            cfg.artifacts_dir = argValue(args, &i) orelse cfg.artifacts_dir;
        } else if (std.mem.startsWith(u8, arg, "--corpus")) {
            cfg.corpus_dir = argValue(args, &i) orelse cfg.corpus_dir;
        }
    }

    std.fs.cwd().makePath(cfg.artifacts_dir) catch {};
    std.fs.cwd().makePath(cfg.corpus_dir) catch {};

    var prng = std.Random.DefaultPrng.init(cfg.seed);
    const rng = prng.random();
    var timer = try std.time.Timer.start();

    std.debug.print("[T4] campaign A: compiler fuzz ({d} iters, seed {d})\n", .{ cfg.compile_iters, cfg.seed });
    try campaignCompile(rng);
    std.debug.print("[T4] campaign A done: ok={d} err={d} ({d} ms)\n", .{ counters.compile_ok, counters.compile_err, timer.read() / std.time.ns_per_ms });

    try campaignInjection(rng);
    std.debug.print("[T4] campaign B done: injected={d} cases={d} ({d} ms)\n", .{ counters.injected_failures, counters.injected_cases, timer.read() / std.time.ns_per_ms });

    std.debug.print("[T4] campaign C: accept/mask walks ({d} sessions)\n", .{cfg.walk_sessions});
    try campaignWalk(rng);
    std.debug.print("[T4] campaign C done: sessions={d} api_calls={d} masks={d} accepts={d} dead_ends={d} rl={d} ({d} ms)\n", .{ counters.walk_sessions, counters.walk_api_calls, counters.walk_masks, counters.walk_accepts, counters.walk_dead_ends, counters.walk_resource_limits, timer.read() / std.time.ns_per_ms });

    std.debug.print("[T4] campaign D: cache parity ({d} walks x2 phases)\n", .{cfg.cache_walks});
    try campaignCache(rng);
    std.debug.print("[T4] campaign D done: walks={d} mask_cmps={d} threaded_match={} ({d} ms)\n", .{ counters.cache_walks, counters.cache_mask_cmps, counters.threaded_match, timer.read() / std.time.ns_per_ms });

    const total = counters.compile_iters + counters.injected_failures + counters.walk_api_calls + counters.cache_mask_cmps;
    var rep: std.ArrayList(u8) = .{};
    try rep.print(a,
        \\{{"seed":{d},"compile_iters":{d},"compile_ok":{d},"compile_err":{d},"injected_failures":{d},"injected_cases":{d},"walk_sessions":{d},"walk_api_calls":{d},"walk_masks":{d},"walk_accepts":{d},"walk_dead_ends":{d},"walk_resource_limits":{d},"walk_compile_failures":{d},"cache_walks":{d},"cache_mask_cmps":{d},"threaded_match":{},"total_iterations":{d},"elapsed_ms":{d}}}
    , .{ cfg.seed, counters.compile_iters, counters.compile_ok, counters.compile_err, counters.injected_failures, counters.injected_cases, counters.walk_sessions, counters.walk_api_calls, counters.walk_masks, counters.walk_accepts, counters.walk_dead_ends, counters.walk_resource_limits, counters.walk_compile_failures, counters.cache_walks, counters.cache_mask_cmps, counters.threaded_match, total, timer.read() / std.time.ns_per_ms });
    try rep.append(a, '\n');
    std.fs.cwd().writeFile(.{ .sub_path = cfg.report, .data = rep.items }) catch |e| {
        std.debug.print("cannot write report {s}: {s}\n", .{ cfg.report, @errorName(e) });
    };
    std.debug.print("[T4] ALL CAMPAIGNS PASSED, total_iterations={d}, report={s}\n", .{ total, cfg.report });
}
