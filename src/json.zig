const std = @import("std");

pub const Error = error{ InvalidSchema, ResourceLimit, OutOfMemory };

pub const MAX_DEPTH: u32 = 512;

pub const Pair = struct {
    key: []const u8,
    key_offset: u32,
    value: *Value,
};

pub const Value = struct {
    offset: u32,
    v: union(enum) {
        object: []Pair,
        array: []*Value,
        string: []const u8,
        number: []const u8,
        boolean: bool,
        null_v: void,
    },
};

pub fn parse(arena: std.mem.Allocator, src: []const u8, err_offset: *u32) Error!*Value {
    err_offset.* = std.math.maxInt(u32);
    if (firstInvalidUtf8(src)) |bad| {
        err_offset.* = @intCast(bad);
        return error.InvalidSchema;
    }
    var p = Parser{ .src = src, .a = arena, .err_off = err_offset };
    p.skipWs();
    const v = try p.parseValue(1);
    p.skipWs();
    if (p.pos != src.len) return p.fail(p.pos);
    return v;
}

fn firstInvalidUtf8(src: []const u8) ?usize {
    var i: usize = 0;
    while (i < src.len) {
        const n = std.unicode.utf8ByteSequenceLength(src[i]) catch return i;
        if (i + n > src.len) return i;
        _ = std.unicode.utf8Decode(src[i .. i + n]) catch return i;
        i += n;
    }
    return null;
}

const Parser = struct {
    src: []const u8,
    a: std.mem.Allocator,
    err_off: *u32,
    pos: usize = 0,

    fn fail(self: *Parser, off: usize) Error {
        self.err_off.* = @intCast(off);
        return error.InvalidSchema;
    }

    fn skipWs(self: *Parser) void {
        while (self.pos < self.src.len) : (self.pos += 1) {
            switch (self.src[self.pos]) {
                ' ', '\t', '\n', '\r' => {},
                else => return,
            }
        }
    }

    fn peek(self: *Parser) ?u8 {
        if (self.pos >= self.src.len) return null;
        return self.src[self.pos];
    }

    fn parseValue(self: *Parser, depth: u32) Error!*Value {
        if (depth > MAX_DEPTH) {
            self.err_off.* = @intCast(self.pos);
            return error.ResourceLimit;
        }
        const c = self.peek() orelse return self.fail(self.pos);
        const v = try self.a.create(Value);
        v.offset = @intCast(self.pos);
        switch (c) {
            '{' => v.v = .{ .object = try self.parseObject(depth) },
            '[' => v.v = .{ .array = try self.parseArray(depth) },
            '"' => v.v = .{ .string = try self.parseString() },
            't' => {
                try self.expectLit("true");
                v.v = .{ .boolean = true };
            },
            'f' => {
                try self.expectLit("false");
                v.v = .{ .boolean = false };
            },
            'n' => {
                try self.expectLit("null");
                v.v = .{ .null_v = {} };
            },
            '-', '0'...'9' => v.v = .{ .number = try self.parseNumber() },
            else => return self.fail(self.pos),
        }
        return v;
    }

    fn expectLit(self: *Parser, lit: []const u8) Error!void {
        if (self.src.len - self.pos < lit.len) return self.fail(self.pos);
        if (!std.mem.eql(u8, self.src[self.pos .. self.pos + lit.len], lit))
            return self.fail(self.pos);
        self.pos += lit.len;
    }

    fn parseObject(self: *Parser, depth: u32) Error![]Pair {
        self.pos += 1;
        var pairs: std.ArrayListUnmanaged(Pair) = .{};
        var seen: std.StringHashMapUnmanaged(void) = .{};
        self.skipWs();
        if (self.peek() == @as(u8, '}')) {
            self.pos += 1;
            return pairs.toOwnedSlice(self.a);
        }
        while (true) {
            self.skipWs();
            if (self.peek() != @as(u8, '"')) return self.fail(self.pos);
            const koff = self.pos;
            const key = try self.parseString();
            const gop = try seen.getOrPut(self.a, key);
            if (gop.found_existing) return self.fail(koff);
            self.skipWs();
            if (self.peek() != @as(u8, ':')) return self.fail(self.pos);
            self.pos += 1;
            self.skipWs();
            const val = try self.parseValue(depth + 1);
            try pairs.append(self.a, .{ .key = key, .key_offset = @intCast(koff), .value = val });
            self.skipWs();
            switch (self.peek() orelse return self.fail(self.pos)) {
                ',' => self.pos += 1,
                '}' => {
                    self.pos += 1;
                    return pairs.toOwnedSlice(self.a);
                },
                else => return self.fail(self.pos),
            }
        }
    }

    fn parseArray(self: *Parser, depth: u32) Error![]*Value {
        self.pos += 1;
        var items: std.ArrayListUnmanaged(*Value) = .{};
        self.skipWs();
        if (self.peek() == @as(u8, ']')) {
            self.pos += 1;
            return items.toOwnedSlice(self.a);
        }
        while (true) {
            self.skipWs();
            const val = try self.parseValue(depth + 1);
            try items.append(self.a, val);
            self.skipWs();
            switch (self.peek() orelse return self.fail(self.pos)) {
                ',' => self.pos += 1,
                ']' => {
                    self.pos += 1;
                    return items.toOwnedSlice(self.a);
                },
                else => return self.fail(self.pos),
            }
        }
    }

    fn parseString(self: *Parser) Error![]const u8 {
        self.pos += 1;
        var out: std.ArrayListUnmanaged(u8) = .{};
        while (true) {
            const b = self.peek() orelse return self.fail(self.pos);
            if (b == '"') {
                self.pos += 1;
                return out.toOwnedSlice(self.a);
            }
            if (b == '\\') {
                self.pos += 1;
                const e = self.peek() orelse return self.fail(self.pos);
                self.pos += 1;
                switch (e) {
                    '"' => try out.append(self.a, '"'),
                    '\\' => try out.append(self.a, '\\'),
                    '/' => try out.append(self.a, '/'),
                    'b' => try out.append(self.a, 0x08),
                    'f' => try out.append(self.a, 0x0C),
                    'n' => try out.append(self.a, '\n'),
                    'r' => try out.append(self.a, '\r'),
                    't' => try out.append(self.a, '\t'),
                    'u' => {
                        const cp = try self.parseUnicodeEscape();
                        var buf: [4]u8 = undefined;
                        const n = std.unicode.utf8Encode(cp, &buf) catch
                            return self.fail(self.pos);
                        try out.appendSlice(self.a, buf[0..n]);
                    },
                    else => return self.fail(self.pos - 1),
                }
            } else if (b < 0x20) {
                return self.fail(self.pos);
            } else {
                try out.append(self.a, b);
                self.pos += 1;
            }
        }
    }

    fn parseHex4(self: *Parser) Error!u16 {
        if (self.src.len - self.pos < 4) return self.fail(self.pos);
        var r: u16 = 0;
        for (self.src[self.pos .. self.pos + 4]) |d| {
            const x: u16 = switch (d) {
                '0'...'9' => d - '0',
                'a'...'f' => d - 'a' + 10,
                'A'...'F' => d - 'A' + 10,
                else => return self.fail(self.pos),
            };
            r = r * 16 + x;
            self.pos += 1;
        }
        return r;
    }

    fn parseUnicodeEscape(self: *Parser) Error!u21 {
        const first = try self.parseHex4();
        if (first >= 0xD800 and first <= 0xDBFF) {
            if (self.src.len - self.pos < 2 or self.src[self.pos] != '\\' or self.src[self.pos + 1] != 'u')
                return self.fail(self.pos);
            self.pos += 2;
            const second = try self.parseHex4();
            if (second < 0xDC00 or second > 0xDFFF) return self.fail(self.pos - 4);
            const hi: u21 = first - 0xD800;
            const lo: u21 = second - 0xDC00;
            return 0x10000 + (hi << 10) + lo;
        }
        if (first >= 0xDC00 and first <= 0xDFFF) return self.fail(self.pos - 4);
        return first;
    }

    fn parseNumber(self: *Parser) Error![]const u8 {
        const start = self.pos;
        if (self.peek() == @as(u8, '-')) self.pos += 1;
        switch (self.peek() orelse return self.fail(self.pos)) {
            '0' => self.pos += 1,
            '1'...'9' => {
                self.pos += 1;
                while (self.peek()) |d| {
                    if (d < '0' or d > '9') break;
                    self.pos += 1;
                }
            },
            else => return self.fail(self.pos),
        }
        if (self.peek() == @as(u8, '.')) {
            self.pos += 1;
            var nd: usize = 0;
            while (self.peek()) |d| {
                if (d < '0' or d > '9') break;
                self.pos += 1;
                nd += 1;
            }
            if (nd == 0) return self.fail(self.pos);
        }
        if (self.peek()) |d| {
            if (d == 'e' or d == 'E') {
                self.pos += 1;
                if (self.peek()) |s| {
                    if (s == '+' or s == '-') self.pos += 1;
                }
                var nd: usize = 0;
                while (self.peek()) |dd| {
                    if (dd < '0' or dd > '9') break;
                    self.pos += 1;
                    nd += 1;
                }
                if (nd == 0) return self.fail(self.pos);
            }
        }
        return self.src[start..self.pos];
    }
};

test "parse nested document with escapes and surrogate pair" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    var off: u32 = 0;
    const v = try parse(arena.allocator(), "{\"a\":[1,-2.5e3,\"x\\u0041\\uD83D\\uDE00\",true,null],\"b\":{}}", &off);
    try std.testing.expectEqual(@as(u32, 0), v.offset);
    const pairs = v.v.object;
    try std.testing.expectEqual(@as(usize, 2), pairs.len);
    try std.testing.expectEqualStrings("a", pairs[0].key);
    const arr = pairs[0].value.v.array;
    try std.testing.expectEqual(@as(usize, 5), arr.len);
    try std.testing.expectEqualStrings("1", arr[0].v.number);
    try std.testing.expectEqualStrings("-2.5e3", arr[1].v.number);
    try std.testing.expectEqualStrings("xA\xF0\x9F\x98\x80", arr[2].v.string);
    try std.testing.expect(arr[3].v.boolean);
    try std.testing.expect(arr[4].v == .null_v);
    try std.testing.expectEqual(@as(usize, 0), pairs[1].value.v.object.len);
}

test "parse string escapes" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    var off: u32 = 0;
    const v = try parse(arena.allocator(), "\"a\\nb\\u0007\\t\\/\\\\\"", &off);
    try std.testing.expectEqualStrings("a\nb\x07\t/\\", v.v.string);
}

test "reject duplicate object keys" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    var off: u32 = 0;
    try std.testing.expectError(error.InvalidSchema, parse(arena.allocator(), "{\"a\":1,\"a\":2}", &off));
}

test "reject malformed JSON" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var off: u32 = 0;
    try std.testing.expectError(error.InvalidSchema, parse(a, "01", &off));
    try std.testing.expectError(error.InvalidSchema, parse(a, "1.", &off));
    try std.testing.expectError(error.InvalidSchema, parse(a, "1e", &off));
    try std.testing.expectError(error.InvalidSchema, parse(a, "[1] x", &off));
    try std.testing.expectError(error.InvalidSchema, parse(a, "{", &off));
    try std.testing.expectError(error.InvalidSchema, parse(a, "\"\\uD800\"", &off));
    try std.testing.expectError(error.InvalidSchema, parse(a, "\"\\uDC00\"", &off));
    try std.testing.expectError(error.InvalidSchema, parse(a, "\"\\q\"", &off));
    try std.testing.expectError(error.InvalidSchema, parse(a, "\"a\x01\"", &off));
    try std.testing.expectError(error.InvalidSchema, parse(a, "\"\xff\"", &off));
    try std.testing.expectError(error.InvalidSchema, parse(a, "", &off));
}
