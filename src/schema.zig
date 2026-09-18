const std = @import("std");
const grammar = @import("grammar.zig");
const json = @import("json.zig");
const work_mod = @import("work.zig");

pub const CompileError = error{
    InvalidSchema,
    UnsupportedFeature,
    UnsatisfiableConstraint,
    ResourceLimit,
    Cancelled,
    OutOfMemory,
};

pub const POINTER_CAP = 256;

pub const Diagnostic = struct {
    pub const Code = enum { invalid_schema, unsupported_feature, unsatisfiable_constraint, resource_limit };
    code: Code = .invalid_schema,
    offset: u32 = std.math.maxInt(u32),
    message: [256]u8 = [_]u8{0} ** 256,
    message_len: u16 = 0,
    // RFC 6901 JSON Pointer to the failing schema node; empty when the
    // error is not tied to a node (e.g. malformed JSON). Overlong paths
    // are truncated and end with U+2026.
    pointer: [POINTER_CAP]u8 = [_]u8{0} ** POINTER_CAP,
    pointer_len: u16 = 0,

    pub fn text(self: *const Diagnostic) []const u8 {
        return self.message[0..self.message_len];
    }

    pub fn pointerText(self: *const Diagnostic) []const u8 {
        return self.pointer[0..self.pointer_len];
    }
};

pub fn report(diag: *Diagnostic, code: Diagnostic.Code, offset: u32, comptime fmt: []const u8, args: anytype) void {
    diag.code = code;
    diag.offset = offset;
    const s = std.fmt.bufPrint(&diag.message, fmt, args) catch diag.message[0..];
    diag.message_len = @intCast(s.len);
}

/// Points the diagnostic at an array element ("/<idx>"), used by the
/// literal-set compiler which has no object keys.
pub fn setPointerIndex(diag: *Diagnostic, idx: usize) void {
    const s = std.fmt.bufPrint(&diag.pointer, "/{d}", .{idx}) catch diag.pointer[0..];
    diag.pointer_len = @intCast(s.len);
}

pub fn reportJsonError(diag: *Diagnostic, err: json.Error) CompileError {
    const off = diag.offset;
    switch (err) {
        error.OutOfMemory => {
            report(diag, .resource_limit, off, "out of memory", .{});
            return error.OutOfMemory;
        },
        error.ResourceLimit => {
            report(diag, .resource_limit, off, "JSON nesting too deep at byte {d}", .{off});
            return error.ResourceLimit;
        },
        error.InvalidSchema => {
            report(diag, .invalid_schema, off, "invalid JSON at byte {d}", .{off});
            return error.InvalidSchema;
        },
    }
}

const MAX_NODES: u32 = 100_000;
const MAX_BOUND: u64 = std.math.maxInt(u32) - 1;
const NO_OFFSET: u32 = std.math.maxInt(u32);

const SchemaType = enum { object, array, string, integer, number, boolean, null_ };

const Keywords = struct {
    ty: ?*const json.Value = null,
    properties: ?*const json.Value = null,
    required: ?*const json.Value = null,
    additional: ?*const json.Value = null,
    items: ?*const json.Value = null,
    min_items: ?*const json.Value = null,
    max_items: ?*const json.Value = null,
    min_length: ?*const json.Value = null,
    max_length: ?*const json.Value = null,
    enum_: ?*const json.Value = null,
    const_: ?*const json.Value = null,
    defs: ?*const json.Value = null,
    ref: ?*const json.Value = null,
    schema_uri: ?*const json.Value = null,
};

fn isAnnotation(key: []const u8) bool {
    const list = [_][]const u8{ "title", "description", "$comment", "examples", "default" };
    for (list) |k| {
        if (std.mem.eql(u8, key, k)) return true;
    }
    return false;
}

fn isKnownUnsupported(key: []const u8) bool {
    const list = [_][]const u8{
        "anyOf",            "oneOf",            "allOf",             "not",
        "if",               "then",             "else",              "minimum",
        "maximum",          "multipleOf",       "exclusiveMinimum",  "exclusiveMaximum",
        "pattern",          "format",           "patternProperties", "uniqueItems",
        "contains",         "dependentSchemas", "dependentRequired", "unevaluatedProperties",
        "propertyNames",    "minContains",      "maxContains",       "prefixItems",
        "additionalItems",  "$id",              "$anchor",           "$dynamicRef",
        "$recursiveRef",    "definitions",      "dependencies",      "contentEncoding",
        "contentMediaType", "contentSchema",    "readOnly",          "writeOnly",
        "deprecated",
    };
    for (list) |k| {
        if (std.mem.eql(u8, key, k)) return true;
    }
    return false;
}

fn parseTypeName(s: []const u8) ?SchemaType {
    const map = .{
        .{ "object", SchemaType.object },
        .{ "array", SchemaType.array },
        .{ "string", SchemaType.string },
        .{ "integer", SchemaType.integer },
        .{ "number", SchemaType.number },
        .{ "boolean", SchemaType.boolean },
        .{ "null", SchemaType.null_ },
    };
    inline for (map) |entry| {
        if (std.mem.eql(u8, s, entry[0])) return entry[1];
    }
    return null;
}

pub fn compile(child_allocator: std.mem.Allocator, schema_bytes: []const u8, max_depth: u32, diag: *Diagnostic, w: *work_mod.Work) CompileError!grammar.Grammar {
    diag.* = .{};
    var arena = std.heap.ArenaAllocator.init(child_allocator);
    errdefer arena.deinit();
    const a = arena.allocator();
    try w.charge(@max(1, schema_bytes.len / 4096));
    var off: u32 = std.math.maxInt(u32);
    const root = json.parse(a, schema_bytes, &off) catch |e| {
        diag.offset = off;
        return reportJsonError(diag, e);
    };
    var c = Compiler{
        .a = a,
        .builder = grammar.Builder.init(a),
        .root = root,
        .max_depth = if (max_depth == 0) 64 else max_depth,
        .diag = diag,
        .w = w,
    };
    try c.ref_stack.append(a, "#");
    const root_id = try c.compileSchema(root, 1);
    return c.builder.finish(arena, .json_schema, root_id, grammar.identityOf(.json_schema, schema_bytes));
}

const Compiler = struct {
    a: std.mem.Allocator,
    builder: grammar.Builder,
    root: *const json.Value,
    max_depth: u32,
    diag: *Diagnostic,
    w: *work_mod.Work,
    defs: ?*const json.Value = null,
    ref_stack: std.ArrayListUnmanaged([]const u8) = .{},
    path: std.ArrayListUnmanaged([]const u8) = .{},
    nodes_left: u32 = MAX_NODES,

    fn addNode(self: *Compiler, n: grammar.Node) CompileError!grammar.NodeId {
        if (self.nodes_left == 0) {
            report(self.diag, .resource_limit, NO_OFFSET, "grammar exceeds node budget of {d}", .{MAX_NODES});
            return error.ResourceLimit;
        }
        self.nodes_left -= 1;
        try self.w.charge(1);
        return self.builder.addNode(n);
    }

    fn renderPointer(self: *Compiler) void {
        var len: usize = 0;
        var truncated = false;
        for (self.path.items) |seg| {
            if (len + 1 >= POINTER_CAP - 4) {
                truncated = true;
                break;
            }
            self.diag.pointer[len] = '/';
            len += 1;
            for (seg) |b| {
                // RFC 6901 escapes: '~' -> ~0, '/' -> ~1.
                const esc: ?[]const u8 = switch (b) {
                    '~' => "~0",
                    '/' => "~1",
                    else => null,
                };
                const need = if (esc) |e| e.len else 1;
                if (len + need >= POINTER_CAP - 4) {
                    truncated = true;
                    break;
                }
                if (esc) |e| {
                    @memcpy(self.diag.pointer[len..][0..e.len], e);
                    len += e.len;
                } else {
                    self.diag.pointer[len] = b;
                    len += 1;
                }
            }
            if (truncated) break;
        }
        if (truncated) {
            @memcpy(self.diag.pointer[len..][0..3], "\xe2\x80\xa6"); // U+2026
            len += 3;
        }
        self.diag.pointer_len = @intCast(len);
    }

    fn fail(self: *Compiler, e: CompileError, offset: u32, comptime fmt: []const u8, args: anytype) CompileError {
        self.renderPointer();
        const code: Diagnostic.Code = switch (e) {
            error.InvalidSchema => .invalid_schema,
            error.UnsupportedFeature => .unsupported_feature,
            error.UnsatisfiableConstraint => .unsatisfiable_constraint,
            error.ResourceLimit, error.Cancelled, error.OutOfMemory => .resource_limit,
        };
        report(self.diag, code, offset, fmt, args);
        return e;
    }

    fn compileSchema(self: *Compiler, v: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        if (depth > self.max_depth) {
            return self.fail(error.ResourceLimit, v.offset, "schema depth exceeds max_depth {d}", .{self.max_depth});
        }
        const pairs = switch (v.v) {
            .object => |p| p,
            .boolean => return self.fail(error.UnsupportedFeature, v.offset, "boolean schemas are not supported", .{}),
            else => return self.fail(error.InvalidSchema, v.offset, "schema must be an object", .{}),
        };
        var kw: Keywords = .{};
        for (pairs) |p| {
            if (isAnnotation(p.key)) continue;
            const map = .{
                .{ "type", &kw.ty },
                .{ "properties", &kw.properties },
                .{ "required", &kw.required },
                .{ "additionalProperties", &kw.additional },
                .{ "items", &kw.items },
                .{ "minItems", &kw.min_items },
                .{ "maxItems", &kw.max_items },
                .{ "minLength", &kw.min_length },
                .{ "maxLength", &kw.max_length },
                .{ "enum", &kw.enum_ },
                .{ "const", &kw.const_ },
                .{ "$defs", &kw.defs },
                .{ "$ref", &kw.ref },
                .{ "$schema", &kw.schema_uri },
            };
            var matched = false;
            inline for (map) |entry| {
                if (std.mem.eql(u8, p.key, entry[0])) {
                    entry[1].* = p.value;
                    matched = true;
                }
            }
            if (!matched) {
                if (isKnownUnsupported(p.key)) {
                    return self.fail(error.UnsupportedFeature, p.key_offset, "keyword '{s}' is outside the MVP profile", .{p.key});
                }
                return self.fail(error.UnsupportedFeature, p.key_offset, "unknown keyword '{s}'", .{p.key});
            }
        }
        if (kw.schema_uri) |sv| {
            const s = switch (sv.v) {
                .string => |s| s,
                else => return self.fail(error.InvalidSchema, sv.offset, "$schema must be a string", .{}),
            };
            const draft = "https://json-schema.org/draft/2020-12/schema";
            if (!std.mem.eql(u8, s, draft) and !std.mem.eql(u8, s, draft ++ "#")) {
                return self.fail(error.UnsupportedFeature, sv.offset, "unsupported $schema dialect '{s}'", .{s});
            }
        }
        if (kw.defs) |dv| {
            switch (dv.v) {
                .object => {},
                else => return self.fail(error.InvalidSchema, dv.offset, "$defs must be an object", .{}),
            }
            if (depth == 1) self.defs = dv;
        }
        if (kw.ref) |rv| return self.compileRef(v, kw, rv, depth);
        return self.compileTyped(v, kw, depth);
    }

    fn compileRef(self: *Compiler, v: *const json.Value, kw: Keywords, rv: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        if (kw.ty != null or kw.properties != null or kw.required != null or kw.additional != null or
            kw.items != null or kw.min_items != null or kw.max_items != null or kw.min_length != null or
            kw.max_length != null or kw.enum_ != null or kw.const_ != null)
        {
            return self.fail(error.UnsupportedFeature, v.offset, "assertion keywords next to $ref are not supported", .{});
        }
        const s = switch (rv.v) {
            .string => |s| s,
            else => return self.fail(error.InvalidSchema, rv.offset, "$ref must be a string", .{}),
        };
        if (s.len == 0 or s[0] != '#') {
            return self.fail(error.UnsupportedFeature, rv.offset, "only local $ref is supported: '{s}'", .{s});
        }
        var name: []const u8 = undefined;
        var target: *const json.Value = undefined;
        if (std.mem.eql(u8, s, "#")) {
            name = "#";
            target = self.root;
        } else if (std.mem.startsWith(u8, s, "#/$defs/")) {
            name = try self.unescapePointer(s["#/$defs/".len..], rv.offset);
            const dv = self.defs orelse
                return self.fail(error.InvalidSchema, rv.offset, "$ref '{s}' without root $defs", .{s});
            const def_pairs = dv.v.object;
            var found: ?*const json.Value = null;
            for (def_pairs) |dp| {
                if (std.mem.eql(u8, dp.key, name)) found = dp.value;
            }
            target = found orelse
                return self.fail(error.InvalidSchema, rv.offset, "unresolved $ref '{s}'", .{s});
        } else {
            return self.fail(error.UnsupportedFeature, rv.offset, "unsupported $ref target '{s}'", .{s});
        }
        for (self.ref_stack.items) |n| {
            if (std.mem.eql(u8, n, name)) {
                return self.fail(error.InvalidSchema, rv.offset, "circular $ref involving '{s}'", .{name});
            }
        }
        try self.ref_stack.append(self.a, name);
        defer _ = self.ref_stack.pop();
        const has_path = !std.mem.eql(u8, name, "#");
        if (has_path) {
            try self.path.append(self.a, "$defs");
            try self.path.append(self.a, name);
        }
        const res = self.compileSchema(target, depth + 1);
        if (has_path) self.path.shrinkRetainingCapacity(self.path.items.len - 2);
        return res;
    }

    fn unescapePointer(self: *Compiler, seg: []const u8, offset: u32) CompileError![]const u8 {
        for (seg) |b| {
            if (b == '/') {
                return self.fail(error.UnsupportedFeature, offset, "only single-segment '#/$defs/<name>' $ref is supported", .{});
            }
        }
        if (std.mem.indexOfScalar(u8, seg, '~') == null) return seg;
        var out: std.ArrayListUnmanaged(u8) = .{};
        var i: usize = 0;
        while (i < seg.len) : (i += 1) {
            if (seg[i] == '~') {
                if (i + 1 >= seg.len or (seg[i + 1] != '0' and seg[i + 1] != '1')) {
                    return self.fail(error.InvalidSchema, offset, "invalid '~' escape in $ref pointer", .{});
                }
                try out.append(self.a, if (seg[i + 1] == '0') '~' else '/');
                i += 1;
            } else {
                try out.append(self.a, seg[i]);
            }
        }
        return out.toOwnedSlice(self.a);
    }

    fn compileTyped(self: *Compiler, v: *const json.Value, kw: Keywords, depth: u32) CompileError!grammar.NodeId {
        var ty: ?SchemaType = null;
        if (kw.ty) |tv| {
            switch (tv.v) {
                .string => |s| {
                    ty = parseTypeName(s) orelse
                        return self.fail(error.InvalidSchema, tv.offset, "unknown type '{s}'", .{s});
                },
                .array => return self.fail(error.UnsupportedFeature, tv.offset, "type as a list is not supported; expected a single string", .{}),
                else => return self.fail(error.InvalidSchema, tv.offset, "type must be a string", .{}),
            }
        }
        var values: []const *const json.Value = &.{};
        var has_enum = false;
        if (kw.enum_) |ev| {
            has_enum = true;
            switch (ev.v) {
                .array => |items| values = items,
                else => return self.fail(error.InvalidSchema, ev.offset, "enum must be an array", .{}),
            }
        }
        if (kw.const_) |cv| {
            if (has_enum) {
                return self.fail(error.InvalidSchema, cv.offset, "const next to enum", .{});
            }
            has_enum = true;
            const single = try self.a.alloc(*const json.Value, 1);
            single[0] = cv;
            values = single;
        }
        if (ty == null) {
            if (!has_enum) {
                return self.fail(error.InvalidSchema, v.offset, "schema must declare 'type' or be a pure '$ref'", .{});
            }
            if (values.len == 0) {
                return self.fail(error.InvalidSchema, v.offset, "cannot infer type from an empty enum", .{});
            }
            ty = try self.inferType(values);
        }
        var min_len: ?u32 = null;
        var max_len: ?u32 = null;
        if (kw.min_length) |mv| min_len = try self.readBound(mv, "minLength");
        if (kw.max_length) |mv| max_len = try self.readBound(mv, "maxLength");
        var min_items: ?u32 = null;
        var max_items: ?u32 = null;
        if (kw.min_items) |mv| min_items = try self.readBound(mv, "minItems");
        if (kw.max_items) |mv| max_items = try self.readBound(mv, "maxItems");
        const t = ty.?;
        switch (t) {
            .object => {
                if (kw.items != null or kw.min_items != null or kw.max_items != null or
                    kw.min_length != null or kw.max_length != null)
                {
                    return self.fail(error.InvalidSchema, v.offset, "array/string keywords are not applicable to type object", .{});
                }
            },
            .array => {
                if (kw.properties != null or kw.required != null or kw.additional != null or
                    kw.min_length != null or kw.max_length != null)
                {
                    return self.fail(error.InvalidSchema, v.offset, "object/string keywords are not applicable to type array", .{});
                }
                if (min_items != null and max_items != null and min_items.? > max_items.?) {
                    return self.fail(error.UnsatisfiableConstraint, v.offset, "minItems {d} > maxItems {d}", .{ min_items.?, max_items.? });
                }
            },
            .string => {
                if (kw.properties != null or kw.required != null or kw.additional != null or
                    kw.items != null or kw.min_items != null or kw.max_items != null)
                {
                    return self.fail(error.InvalidSchema, v.offset, "object/array keywords are not applicable to type string", .{});
                }
                if (min_len != null and max_len != null and min_len.? > max_len.?) {
                    return self.fail(error.UnsatisfiableConstraint, v.offset, "minLength {d} > maxLength {d}", .{ min_len.?, max_len.? });
                }
            },
            else => {
                if (kw.properties != null or kw.required != null or kw.additional != null or
                    kw.items != null or kw.min_items != null or kw.max_items != null or
                    kw.min_length != null or kw.max_length != null)
                {
                    return self.fail(error.InvalidSchema, v.offset, "structural keywords are not applicable to this type", .{});
                }
            },
        }
        if (has_enum) return self.compileEnum(v, t, values, min_len, max_len);
        switch (t) {
            .object => return self.compileObject(v, kw, depth),
            .array => {
                const items_v = kw.items orelse
                    return self.fail(error.InvalidSchema, v.offset, "array requires 'items'", .{});
                try self.path.append(self.a, "items");
                defer _ = self.path.pop();
                const item_id = try self.compileSchema(items_v, depth + 1);
                return self.addNode(.{ .repeat = .{
                    .item = item_id,
                    .min = min_items orelse 0,
                    .max = max_items orelse grammar.UNBOUNDED,
                } });
            },
            .string => return self.addNode(.{ .str = .{
                .min_len = min_len orelse 0,
                .max_len = max_len orelse grammar.UNBOUNDED,
            } }),
            .integer => return self.addNode(.{ .int_v = {} }),
            .number => return self.addNode(.{ .num_v = {} }),
            .boolean => {
                const t_id = try self.addNode(.{ .literal = try self.builder.addLiteral("true") });
                const f_id = try self.addNode(.{ .literal = try self.builder.addLiteral("false") });
                return self.addNode(.{ .choice = try self.builder.copyNodeIds(&[_]grammar.NodeId{ t_id, f_id }) });
            },
            .null_ => return self.addNode(.{ .literal = try self.builder.addLiteral("null") }),
        }
    }

    fn inferType(self: *Compiler, values: []const *const json.Value) CompileError!SchemaType {
        var result: ?SchemaType = null;
        for (values) |val| {
            const k: SchemaType = switch (val.v) {
                .string => .string,
                .number => |lex| if (isPlainIntegerLexeme(lex)) SchemaType.integer else SchemaType.number,
                .boolean => .boolean,
                .null_v => .null_,
                .object, .array => return self.fail(error.UnsupportedFeature, val.offset, "non-scalar enum/const values are not supported", .{}),
            };
            if (result) |r| {
                if (r == k) continue;
                const numeric = (r == .integer or r == .number) and (k == .integer or k == .number);
                if (numeric) {
                    result = .number;
                } else {
                    return self.fail(error.UnsupportedFeature, val.offset, "mixed-type enum is not supported", .{});
                }
            } else {
                result = k;
            }
        }
        return result.?;
    }

    fn readBound(self: *Compiler, v: *const json.Value, what: []const u8) CompileError!u32 {
        const lex = switch (v.v) {
            .number => |l| l,
            else => return self.fail(error.InvalidSchema, v.offset, "{s} must be a non-negative integer", .{what}),
        };
        if (!isPlainIntegerLexeme(lex) or lex[0] == '-') {
            return self.fail(error.InvalidSchema, v.offset, "{s} must be a non-negative integer", .{what});
        }
        const n = std.fmt.parseInt(u64, lex, 10) catch
            return self.fail(error.InvalidSchema, v.offset, "{s} is too large", .{what});
        if (n > MAX_BOUND) {
            return self.fail(error.InvalidSchema, v.offset, "{s} is too large", .{what});
        }
        return @intCast(n);
    }

    fn compileObject(self: *Compiler, v: *const json.Value, kw: Keywords, depth: u32) CompileError!grammar.NodeId {
        const props_v = kw.properties orelse
            return self.fail(error.InvalidSchema, v.offset, "object requires 'properties'", .{});
        const req_v = kw.required orelse
            return self.fail(error.InvalidSchema, v.offset, "object requires 'required'", .{});
        const add_v = kw.additional orelse
            return self.fail(error.InvalidSchema, v.offset, "object requires 'additionalProperties: false'", .{});
        switch (add_v.v) {
            .boolean => |b| {
                if (b) return self.fail(error.InvalidSchema, add_v.offset, "additionalProperties must be exactly false", .{});
            },
            else => return self.fail(error.InvalidSchema, add_v.offset, "additionalProperties must be exactly false", .{}),
        }
        const prop_pairs = switch (props_v.v) {
            .object => |p| p,
            else => return self.fail(error.InvalidSchema, props_v.offset, "properties must be an object", .{}),
        };
        const req_items = switch (req_v.v) {
            .array => |it| it,
            else => return self.fail(error.InvalidSchema, req_v.offset, "required must be an array", .{}),
        };
        var req_set: std.StringHashMapUnmanaged(void) = .{};
        for (req_items) |rv| {
            const name = switch (rv.v) {
                .string => |s| s,
                else => return self.fail(error.InvalidSchema, rv.offset, "required entries must be strings", .{}),
            };
            const gop = try req_set.getOrPut(self.a, name);
            if (gop.found_existing) {
                return self.fail(error.InvalidSchema, rv.offset, "duplicate name '{s}' in required", .{name});
            }
        }
        var props: std.ArrayListUnmanaged(grammar.Prop) = .{};
        for (prop_pairs) |pp| {
            var lit: std.ArrayListUnmanaged(u8) = .{};
            try lit.append(self.a, '"');
            try appendEscaped(&lit, self.a, pp.key);
            try lit.appendSlice(self.a, "\":");
            try self.path.append(self.a, "properties");
            try self.path.append(self.a, pp.key);
            defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
            const child = try self.compileSchema(pp.value, depth + 1);
            try props.append(self.a, .{
                .key = try self.builder.addLiteral(lit.items),
                .value = child,
                .required = req_set.contains(pp.key),
            });
        }
        for (req_items) |rv| {
            const name = rv.v.string;
            var found = false;
            for (prop_pairs) |pp| {
                if (std.mem.eql(u8, pp.key, name)) found = true;
            }
            if (!found) {
                return self.fail(error.InvalidSchema, rv.offset, "required name '{s}' is not in properties", .{name});
            }
        }
        return self.addNode(.{ .object = try self.builder.copyProps(props.items) });
    }

    fn compileEnum(self: *Compiler, v: *const json.Value, t: SchemaType, values: []const *const json.Value, min_len: ?u32, max_len: ?u32) CompileError!grammar.NodeId {
        var alts: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        var seen: std.StringHashMapUnmanaged(void) = .{};
        for (values) |val| {
            const bytes = (try self.serializeEnumValue(t, val, min_len, max_len)) orelse continue;
            const gop = try seen.getOrPut(self.a, bytes);
            if (gop.found_existing) continue;
            try alts.append(self.a, try self.addNode(.{ .literal = try self.builder.addLiteral(bytes) }));
        }
        if (alts.items.len == 0) {
            return self.fail(error.UnsatisfiableConstraint, v.offset, "no enum/const value satisfies type and length constraints", .{});
        }
        if (alts.items.len == 1) return alts.items[0];
        return self.addNode(.{ .choice = try self.builder.copyNodeIds(alts.items) });
    }

    fn serializeEnumValue(self: *Compiler, t: SchemaType, val: *const json.Value, min_len: ?u32, max_len: ?u32) CompileError!?[]const u8 {
        switch (t) {
            .string => {
                const s = switch (val.v) {
                    .string => |s| s,
                    else => return null,
                };
                const n = countScalars(s);
                if (min_len) |m| {
                    if (n < m) return null;
                }
                if (max_len) |m| {
                    if (n > m) return null;
                }
                var lit: std.ArrayListUnmanaged(u8) = .{};
                try lit.append(self.a, '"');
                try appendEscaped(&lit, self.a, s);
                try lit.append(self.a, '"');
                const owned = try lit.toOwnedSlice(self.a);
                return owned;
            },
            .integer, .number => {
                const lex = switch (val.v) {
                    .number => |l| l,
                    else => return null,
                };
                const norm = try self.normalizeNumber(lex, val.offset);
                if (t == .integer and !isIntegerForm(norm)) return null;
                return norm;
            },
            .boolean => return switch (val.v) {
                .boolean => |b| if (b) "true" else "false",
                else => null,
            },
            .null_ => return switch (val.v) {
                .null_v => "null",
                else => null,
            },
            .object, .array => return null,
        }
    }

    fn normalizeNumber(self: *Compiler, lexeme: []const u8, offset: u32) CompileError![]const u8 {
        var i: usize = 0;
        var neg = false;
        if (i < lexeme.len and lexeme[i] == '-') {
            neg = true;
            i += 1;
        }
        const int_start = i;
        while (i < lexeme.len and lexeme[i] >= '0' and lexeme[i] <= '9') i += 1;
        const int_part = lexeme[int_start..i];
        var frac_part: []const u8 = "";
        if (i < lexeme.len and lexeme[i] == '.') {
            i += 1;
            const fs = i;
            while (i < lexeme.len and lexeme[i] >= '0' and lexeme[i] <= '9') i += 1;
            frac_part = lexeme[fs..i];
        }
        var exp_val: i64 = 0;
        var has_exp = false;
        if (i < lexeme.len and (lexeme[i] == 'e' or lexeme[i] == 'E')) {
            has_exp = true;
            i += 1;
            var exp_neg = false;
            if (i < lexeme.len and (lexeme[i] == '+' or lexeme[i] == '-')) {
                exp_neg = lexeme[i] == '-';
                i += 1;
            }
            while (i < lexeme.len and lexeme[i] >= '0' and lexeme[i] <= '9') : (i += 1) {
                exp_val = exp_val * 10 + (lexeme[i] - '0');
                if (exp_val > 400) {
                    return self.fail(error.InvalidSchema, offset, "number exponent exceeds |exp| <= 400", .{});
                }
            }
            if (exp_neg) exp_val = -exp_val;
        }
        var all: std.ArrayListUnmanaged(u8) = .{};
        try all.appendSlice(self.a, int_part);
        try all.appendSlice(self.a, frac_part);
        var digits: []const u8 = all.items;
        while (digits.len > 0 and digits[0] == '0') digits = digits[1..];
        if (digits.len == 0) return "0";
        if (digits.len > 400) {
            return self.fail(error.InvalidSchema, offset, "number has more than 400 significant digits", .{});
        }
        const e10: i64 = exp_val - @as(i64, @intCast(frac_part.len));
        var out: std.ArrayListUnmanaged(u8) = .{};
        if (e10 >= 0) {
            if (neg) try out.append(self.a, '-');
            try out.appendSlice(self.a, digits);
            try out.appendNTimes(self.a, '0', @intCast(e10));
            return out.toOwnedSlice(self.a);
        }
        const need: usize = @intCast(-e10);
        var tz: usize = 0;
        while (tz < digits.len and digits[digits.len - 1 - tz] == '0') tz += 1;
        if (tz >= need) {
            if (neg) try out.append(self.a, '-');
            try out.appendSlice(self.a, digits[0 .. digits.len - need]);
            return out.toOwnedSlice(self.a);
        }
        if (neg) try out.append(self.a, '-');
        var int_out = int_part;
        while (int_out.len > 1 and int_out[0] == '0') int_out = int_out[1..];
        var frac_out = frac_part;
        while (frac_out.len > 0 and frac_out[frac_out.len - 1] == '0') frac_out = frac_out[0 .. frac_out.len - 1];
        if (!has_exp) {
            try out.appendSlice(self.a, int_out);
            try out.append(self.a, '.');
            try out.appendSlice(self.a, frac_out);
            return out.toOwnedSlice(self.a);
        }
        try out.appendSlice(self.a, int_out);
        if (frac_out.len > 0) {
            try out.append(self.a, '.');
            try out.appendSlice(self.a, frac_out);
        }
        try out.append(self.a, 'e');
        var ebuf: [24]u8 = undefined;
        const es = std.fmt.bufPrint(&ebuf, "{d}", .{exp_val}) catch unreachable;
        try out.appendSlice(self.a, es);
        return out.toOwnedSlice(self.a);
    }
};

fn isPlainIntegerLexeme(lex: []const u8) bool {
    for (lex) |b| {
        switch (b) {
            '-', '0'...'9' => {},
            else => return false,
        }
    }
    return lex.len > 0;
}

fn isIntegerForm(s: []const u8) bool {
    return isPlainIntegerLexeme(s);
}

fn countScalars(s: []const u8) u32 {
    var n: u32 = 0;
    for (s) |b| {
        if (b & 0xC0 != 0x80) n += 1;
    }
    return n;
}

fn appendEscaped(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, s: []const u8) !void {
    const hex = "0123456789abcdef";
    for (s) |b| {
        switch (b) {
            '"' => try out.appendSlice(a, "\\\""),
            '\\' => try out.appendSlice(a, "\\\\"),
            0x08 => try out.appendSlice(a, "\\b"),
            0x09 => try out.appendSlice(a, "\\t"),
            0x0A => try out.appendSlice(a, "\\n"),
            0x0C => try out.appendSlice(a, "\\f"),
            0x0D => try out.appendSlice(a, "\\r"),
            else => {
                if (b < 0x20) {
                    try out.appendSlice(a, "\\u00");
                    try out.append(a, hex[b >> 4]);
                    try out.append(a, hex[b & 0xF]);
                } else {
                    try out.append(a, b);
                }
            },
        }
    }
}

fn compileT(s: []const u8, max_depth: u32) CompileError!grammar.Grammar {
    var diag: Diagnostic = .{};
    var w = work_mod.Work{};
    return compile(std.testing.allocator, s, max_depth, &diag, &w);
}

fn compileDiag(s: []const u8, diag: *Diagnostic) CompileError!grammar.Grammar {
    var w = work_mod.Work{};
    return compile(std.testing.allocator, s, 64, diag, &w);
}

test "diagnostics carry a JSON Pointer to the failing node" {
    var diag: Diagnostic = .{};
    const s = "{\"type\":\"object\",\"properties\":{\"k0\":{\"type\":\"array\",\"items\":{\"type\":\"string\",\"pattern\":\"x\"}}},\"required\":[\"k0\"],\"additionalProperties\":false}";
    try std.testing.expectError(error.UnsupportedFeature, compileDiag(s, &diag));
    try std.testing.expectEqualStrings("/properties/k0/items", diag.pointerText());
}

test "diagnostics pointer: root node, $defs target, literal-set style escaping" {
    var diag: Diagnostic = .{};
    try std.testing.expectError(error.UnsupportedFeature, compileDiag("{\"anyOf\":[]}", &diag));
    try std.testing.expectEqualStrings("", diag.pointerText());

    diag = .{};
    const s = "{\"$defs\":{\"d/e\":{\"pattern\":\"x\"}},\"$ref\":\"#/$defs/d~1e\"}";
    try std.testing.expectError(error.UnsupportedFeature, compileDiag(s, &diag));
    try std.testing.expectEqualStrings("/$defs/d~1e", diag.pointerText());
}

test "compile object schema from spec example" {
    const s = "{\"type\":\"object\",\"properties\":{\"action\":{\"type\":\"string\",\"enum\":[\"buy\",\"sell\"]},\"amount\":{\"type\":\"integer\"}},\"required\":[\"action\",\"amount\"],\"additionalProperties\":false}";
    var g = try compileT(s, 64);
    defer g.deinit();
    try std.testing.expectEqual(grammar.GrammarKind.json_schema, g.kind);
    try std.testing.expect(g.id != 0);
    const props = switch (g.node(g.root).*) {
        .object => |p| p,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), props.len);
    try std.testing.expectEqualStrings("\"action\":", g.literalBytes(props[0].key));
    try std.testing.expect(props[0].required);
    const alts = switch (g.node(props[0].value).*) {
        .choice => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), alts.len);
    const lit0 = switch (g.node(alts[0]).*) {
        .literal => |l| l,
        else => return error.TestUnexpectedResult,
    };
    const lit1 = switch (g.node(alts[1]).*) {
        .literal => |l| l,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqualStrings("\"buy\"", g.literalBytes(lit0));
    try std.testing.expectEqualStrings("\"sell\"", g.literalBytes(lit1));
    try std.testing.expect(g.node(props[1].value).* == .int_v);
}

test "enum with common prefixes" {
    const s = "{\"type\":\"string\",\"enum\":[\"foo\",\"foobar\",\"fob\"]}";
    var g = try compileT(s, 64);
    defer g.deinit();
    const alts = switch (g.node(g.root).*) {
        .choice => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 3), alts.len);
    const expected = [_][]const u8{ "\"foo\"", "\"foobar\"", "\"fob\"" };
    for (alts, expected) |id, exp| {
        const lit = switch (g.node(id).*) {
            .literal => |l| l,
            else => return error.TestUnexpectedResult,
        };
        try std.testing.expectEqualStrings(exp, g.literalBytes(lit));
    }
}

test "$defs and local $ref" {
    const s = "{\"$defs\":{\"positiveInt\":{\"type\":\"integer\"}},\"type\":\"object\",\"properties\":{\"count\":{\"$ref\":\"#/$defs/positiveInt\"}},\"required\":[\"count\"],\"additionalProperties\":false}";
    var g = try compileT(s, 64);
    defer g.deinit();
    const props = switch (g.node(g.root).*) {
        .object => |p| p,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 1), props.len);
    try std.testing.expectEqualStrings("\"count\":", g.literalBytes(props[0].key));
    try std.testing.expect(g.node(props[0].value).* == .int_v);
}

test "enum numbers normalized exactly without binary64" {
    {
        const s = "{\"type\":\"integer\",\"enum\":[1e2,100,2.0e1]}";
        var g = try compileT(s, 64);
        defer g.deinit();
        const alts = switch (g.node(g.root).*) {
            .choice => |c| c,
            else => return error.TestUnexpectedResult,
        };
        try std.testing.expectEqual(@as(usize, 2), alts.len);
        const l0 = switch (g.node(alts[0]).*) {
            .literal => |l| l,
            else => return error.TestUnexpectedResult,
        };
        const l1 = switch (g.node(alts[1]).*) {
            .literal => |l| l,
            else => return error.TestUnexpectedResult,
        };
        try std.testing.expectEqualStrings("100", g.literalBytes(l0));
        try std.testing.expectEqualStrings("20", g.literalBytes(l1));
    }
    {
        const s = "{\"type\":\"number\",\"enum\":[1.50]}";
        var g = try compileT(s, 64);
        defer g.deinit();
        const lit = switch (g.node(g.root).*) {
            .literal => |l| l,
            else => return error.TestUnexpectedResult,
        };
        try std.testing.expectEqualStrings("1.5", g.literalBytes(lit));
    }
    {
        const s = "{\"type\":\"number\",\"enum\":[1.5e-3]}";
        var g = try compileT(s, 64);
        defer g.deinit();
        const lit = switch (g.node(g.root).*) {
            .literal => |l| l,
            else => return error.TestUnexpectedResult,
        };
        try std.testing.expectEqualStrings("1.5e-3", g.literalBytes(lit));
    }
}

test "enum filtered by integer type is unsatisfiable" {
    const s = "{\"type\":\"integer\",\"enum\":[2.5]}";
    try std.testing.expectError(error.UnsatisfiableConstraint, compileT(s, 64));
}

test "enum filtered by minLength" {
    const s = "{\"type\":\"string\",\"minLength\":3,\"enum\":[\"ab\",\"abc\"]}";
    var g = try compileT(s, 64);
    defer g.deinit();
    const lit = switch (g.node(g.root).*) {
        .literal => |l| l,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqualStrings("\"abc\"", g.literalBytes(lit));
}

test "anyOf is unsupported" {
    const s = "{\"anyOf\":[{\"type\":\"string\"},{\"type\":\"integer\"}]}";
    try std.testing.expectError(error.UnsupportedFeature, compileT(s, 64));
}

test "min greater than max is unsatisfiable" {
    const s = "{\"type\":\"array\",\"items\":{\"type\":\"string\"},\"minItems\":3,\"maxItems\":2}";
    try std.testing.expectError(error.UnsatisfiableConstraint, compileT(s, 64));
}

test "circular $ref is invalid" {
    const s = "{\"$defs\":{\"a\":{\"$ref\":\"#/$defs/b\"},\"b\":{\"$ref\":\"#/$defs/a\"}},\"$ref\":\"#/$defs/a\"}";
    try std.testing.expectError(error.InvalidSchema, compileT(s, 64));
}

test "required with unknown name is invalid" {
    const s = "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"string\"}},\"required\":[\"b\"],\"additionalProperties\":false}";
    try std.testing.expectError(error.InvalidSchema, compileT(s, 64));
}

test "duplicate JSON key is invalid" {
    const s = "{\"type\":\"object\",\"type\":\"object\"}";
    try std.testing.expectError(error.InvalidSchema, compileT(s, 64));
}

test "boolean schemas are unsupported" {
    try std.testing.expectError(error.UnsupportedFeature, compileT("true", 64));
    const s = "{\"type\":\"object\",\"properties\":{\"x\":true},\"required\":[],\"additionalProperties\":false}";
    try std.testing.expectError(error.UnsupportedFeature, compileT(s, 64));
}

test "depth limit maps to resource limit" {
    const s = "{\"type\":\"array\",\"items\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}}}";
    try std.testing.expectError(error.ResourceLimit, compileT(s, 2));
    var g = try compileT(s, 3);
    defer g.deinit();
}

test "string key canonical escaping" {
    const s = "{\"type\":\"object\",\"properties\":{\"a\\nb\":{\"type\":\"null\"}},\"required\":[],\"additionalProperties\":false}";
    var g = try compileT(s, 64);
    defer g.deinit();
    const props = switch (g.node(g.root).*) {
        .object => |p| p,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqualStrings("\"a\\nb\":", g.literalBytes(props[0].key));
    const lit = switch (g.node(props[0].value).*) {
        .literal => |l| l,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqualStrings("null", g.literalBytes(lit));
}
