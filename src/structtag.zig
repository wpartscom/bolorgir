//! Structural-tag constraint (BLG_CONSTRAINT_STRUCTURAL_TAG, ADR-0016):
//! tool-call framing for the pinned vLLM agent path. The compiled program
//! is a language over bytes with the shape
//!
//!   triggered_tags:      free text with triggers; a full trigger commits
//!                        to a tag: begin-literal, JSON arguments value,
//!                        end-literal; any number of tags; an optional
//!                        "at least one tag" and "stop after the first
//!                        tag" rule;
//!   tags_with_separator: adjacent tags without free text (the empty
//!                        separator only), same tag shape.
//!
//! The accepted spec subset is the JSON produced by the pinned vLLM
//! structural-tag builders for the `hermes` tool parser (ADR-0016): the
//! legacy `{structures, triggers}` shape and the `{type: "structural_tag",
//! format: ...}` shape with `triggered_tags` / `tags_with_separator` /
//! `any_text` formats and `tag` items whose content is a `json_schema`.
//! Anything else is refused with a diagnostic; unsupported constructs are
//! never reinterpreted (no silent fallback).
//!
//! The state machine is exact over bytes: text carries one shift-and
//! prefix tracker per trigger; a commit spawns one begin-literal match per
//! variant whose begin starts with the trigger; the first completion
//! enters the JSON content (a compiled schema grammar of this kernel) and
//! the end literal is matched as a suffix-position set, spawned at every
//! boundary where the JSON value can end. Begin literals must be
//! prefix-free (checked at compile): the pinning analysis for the hermes
//! shapes (distinct names terminated by `"`) shows at most one variant
//! chain is ever alive past a commit, which keeps one JSON state per
//! session. A structural-tag constraint requires a byte-complete
//! vocabulary, like the regex constraint (ADR-0015).

const std = @import("std");
const Arena = @import("alloc.zig").Arena;
const complete = @import("complete.zig");
const grammar = @import("grammar.zig");
const json = @import("json.zig");
const parser = @import("parser.zig");
const schema = @import("schema.zig");
const tokenizer = @import("tokenizer.zig");
const work_mod = @import("work.zig");

const NO_OFFSET: u32 = std.math.maxInt(u32);
const NO_VARIANT: u8 = 0xFF;
const module = @This();

pub const MAX_VARIANTS: usize = 16;
pub const MAX_TRIGGERS: usize = 4;
pub const MAX_BEGIN: usize = 512;
pub const MAX_END: usize = 63; // suffix position bitmask width
pub const MAX_TRIGGER: usize = 64; // shift-and window width

pub const Mode = enum { triggered_tags, tags_with_separator };

pub const Variant = struct {
    begin: []const u8,
    end: []const u8,
    content: grammar.Grammar,
};

pub const Program = struct {
    mode: Mode,
    at_least_one: bool,
    stop_after_first: bool,
    strip_lead_space: bool,
    triggers: []const []const u8,
    /// One byte -> u64 equality mask per trigger (bit k = trigger[k] == b).
    trigger_eq: []const u64,
    variants: []Variant,
    /// One byte -> u64 equality mask per variant end literal.
    end_eq: []const u64,
    a: std.mem.Allocator,

    inline fn triggerEq(self: *const Program, ti: usize, b: u8) u64 {
        return self.trigger_eq[ti * 256 + b];
    }

    inline fn endEq(self: *const Program, vi: usize, b: u8) u64 {
        return self.end_eq[vi * 256 + b];
    }

    pub fn deinit(self: *Program) void {
        for (self.variants) |*v| {
            v.content.deinit();
            self.a.free(v.begin);
            self.a.free(v.end);
        }
        self.a.free(self.variants);
        for (self.triggers) |t| self.a.free(t);
        self.a.free(self.triggers);
        self.a.free(self.trigger_eq);
        self.a.free(self.end_eq);
    }
};

pub const CompileError = error{ InvalidSchema, UnsupportedFeature, Unsatisfiable, ResourceLimit, Cancelled, OutOfMemory };

fn fail(diag: *schema.Diagnostic, code: schema.Diagnostic.Code, offset: u32, comptime fmt: []const u8, args: anytype) CompileError {
    schema.report(diag, code, offset, fmt, args);
    return switch (code) {
        .invalid_schema => error.InvalidSchema,
        .unsupported_feature => error.UnsupportedFeature,
        .unsatisfiable_constraint => error.Unsatisfiable,
        .resource_limit => error.ResourceLimit,
    };
}

fn isObject(v: *const json.Value) bool {
    return v.v == .object;
}

fn field(v: *const json.Value, name: []const u8) ?*json.Value {
    if (v.v != .object) return null;
    for (v.v.object) |p| {
        if (std.mem.eql(u8, p.key, name)) return p.value;
    }
    return null;
}

fn asString(v: *const json.Value) ?[]const u8 {
    return switch (v.v) {
        .string => |s| s,
        else => null,
    };
}

fn asBool(v: *const json.Value) ?bool {
    return switch (v.v) {
        .boolean => |b| b,
        else => null,
    };
}

fn asArray(v: *const json.Value) ?[]*json.Value {
    return switch (v.v) {
        .array => |items| items,
        else => null,
    };
}

/// Refuses keys outside `allowed` (an unknown field could carry semantics
/// this subset does not implement); returns the offending key pointer.
fn checkKeys(diag: *schema.Diagnostic, v: *const json.Value, allowed: []const []const u8, what: []const u8) CompileError!void {
    if (v.v != .object) return fail(diag, .invalid_schema, v.offset, "{s} must be a JSON object", .{what});
    for (v.v.object) |p| {
        var known = false;
        for (allowed) |k| {
            if (std.mem.eql(u8, p.key, k)) {
                known = true;
                break;
            }
        }
        if (!known)
            return fail(diag, .unsupported_feature, p.key_offset, "unsupported field \"{s}\" in {s}", .{ p.key, what });
    }
}

fn objectString(diag: *schema.Diagnostic, v: *const json.Value, name: []const u8, what: []const u8, required: bool) CompileError!?[]const u8 {
    const f = field(v, name) orelse {
        if (required) return fail(diag, .invalid_schema, v.offset, "{s} requires \"{s}\"", .{ what, name });
        return null;
    };
    const s = asString(f) orelse return fail(diag, .invalid_schema, f.offset, "{s}: \"{s}\" must be a string", .{ what, name });
    return s;
}

const RawTag = struct {
    begin: []const u8,
    end: []const u8,
    content: *json.Value,
    content_offset: u32,
};

const Raw = struct {
    mode: Mode,
    at_least_one: bool,
    stop_after_first: bool,
    triggers: []const []const u8,
    tags: []const RawTag,
};

const tag_keys = [_][]const u8{ "type", "begin", "content", "end", "trigger" };
const content_keys = [_][]const u8{ "type", "json_schema", "schema", "style" };
const triggered_keys = [_][]const u8{ "type", "triggers", "tags", "at_least_one", "stop_after_first" };
const separated_keys = [_][]const u8{ "type", "tags", "separator", "at_least_one", "stop_after_first" };
const legacy_root_keys = [_][]const u8{ "type", "structures", "triggers" };
const v2_root_keys = [_][]const u8{ "type", "format" };

/// Parses and validates the spec, returning views into the DOM; the
/// temporary lists live in the DOM arena `a`.
fn parseSpec(diag: *schema.Diagnostic, root: *json.Value, a: std.mem.Allocator) CompileError!Raw {
    if (!isObject(root))
        return fail(diag, .invalid_schema, root.offset, "the structural-tag spec must be a JSON object", .{});
    if (field(root, "structures") != null) {
        try checkKeys(diag, root, &legacy_root_keys, "the legacy structural-tag spec");
        if (field(root, "type")) |t| {
            const ts = asString(t) orelse return fail(diag, .invalid_schema, t.offset, "spec type must be a string", .{});
            if (!std.mem.eql(u8, ts, "structural_tag"))
                return fail(diag, .unsupported_feature, t.offset, "unsupported spec type \"{s}\"", .{ts});
        }
        const triggers_v = field(root, "triggers") orelse
            return fail(diag, .invalid_schema, root.offset, "the legacy spec requires \"triggers\"", .{});
        const triggers = try triggerList(diag, triggers_v, a);
        const structures_v = field(root, "structures").?;
        const structures = asArray(structures_v) orelse
            return fail(diag, .invalid_schema, structures_v.offset, "structures must be an array", .{});
        if (structures.len == 0)
            return fail(diag, .unsupported_feature, root.offset, "a structural tag without structures is not supported", .{});
        if (structures.len > MAX_VARIANTS)
            return fail(diag, .unsupported_feature, root.offset, "more than {d} tag variants are not supported", .{MAX_VARIANTS});
        const list = try a.alloc(RawTag, structures.len);
        for (structures, 0..) |s, i| {
            const struct_keys = [_][]const u8{ "begin", "schema", "end" };
            try checkKeys(diag, s, &struct_keys, "a legacy structure");
            const begin = (try objectString(diag, s, "begin", "a legacy structure", true)).?;
            const end = (try objectString(diag, s, "end", "a legacy structure", true)).?;
            const content = field(s, "schema") orelse
                return fail(diag, .invalid_schema, s.offset, "a legacy structure requires \"schema\"", .{});
            list[i] = .{ .begin = begin, .end = end, .content = content, .content_offset = content.offset };
        }
        return .{ .mode = .triggered_tags, .at_least_one = false, .stop_after_first = false, .triggers = triggers, .tags = list };
    }
    const type_v = field(root, "type") orelse
        return fail(diag, .unsupported_feature, root.offset, "unsupported structural-tag spec: neither \"structures\" nor \"type\" is present", .{});
    const type_s = asString(type_v) orelse return fail(diag, .invalid_schema, type_v.offset, "spec type must be a string", .{});
    if (!std.mem.eql(u8, type_s, "structural_tag"))
        return fail(diag, .unsupported_feature, type_v.offset, "unsupported spec type \"{s}\"", .{type_s});
    try checkKeys(diag, root, &v2_root_keys, "the structural-tag spec");
    const format = field(root, "format") orelse
        return fail(diag, .invalid_schema, root.offset, "the spec requires \"format\"", .{});
    if (!isObject(format))
        return fail(diag, .invalid_schema, format.offset, "format must be a JSON object", .{});
    const fmt_type_v = field(format, "type") orelse
        return fail(diag, .unsupported_feature, format.offset, "unsupported structural-tag format: no \"type\" field", .{});
    const fmt_type = asString(fmt_type_v) orelse return fail(diag, .invalid_schema, fmt_type_v.offset, "format type must be a string", .{});

    if (std.mem.eql(u8, fmt_type, "any_text")) {
        try checkKeys(diag, format, &[_][]const u8{"type"}, "the any_text format");
        return .{ .mode = .triggered_tags, .at_least_one = false, .stop_after_first = false, .triggers = &.{}, .tags = &.{} };
    }
    if (std.mem.eql(u8, fmt_type, "triggered_tags")) {
        try checkKeys(diag, format, &triggered_keys, "the triggered_tags format");
        const triggers_v = field(format, "triggers") orelse
            return fail(diag, .invalid_schema, format.offset, "triggered_tags requires \"triggers\"", .{});
        const triggers = try triggerList(diag, triggers_v, a);
        const tags = try tagList(diag, field(format, "tags"), format, true, a);
        var at_least_one = false;
        if (field(format, "at_least_one")) |v| {
            at_least_one = asBool(v) orelse
                return fail(diag, .invalid_schema, v.offset, "at_least_one must be a boolean", .{});
        }
        var stop_after_first = false;
        if (field(format, "stop_after_first")) |v| {
            stop_after_first = asBool(v) orelse
                return fail(diag, .invalid_schema, v.offset, "stop_after_first must be a boolean", .{});
        }
        return .{ .mode = .triggered_tags, .at_least_one = at_least_one, .stop_after_first = stop_after_first, .triggers = triggers, .tags = tags };
    }
    if (std.mem.eql(u8, fmt_type, "tags_with_separator")) {
        try checkKeys(diag, format, &separated_keys, "the tags_with_separator format");
        const sep = (try objectString(diag, format, "separator", "tags_with_separator", true)).?;
        if (sep.len != 0)
            return fail(diag, .unsupported_feature, field(format, "separator").?.offset, "only the empty separator is supported", .{});
        const tags = try tagList(diag, field(format, "tags"), format, true, a);
        var at_least_one = false;
        if (field(format, "at_least_one")) |v| {
            at_least_one = asBool(v) orelse
                return fail(diag, .invalid_schema, v.offset, "at_least_one must be a boolean", .{});
        }
        var stop_after_first = false;
        if (field(format, "stop_after_first")) |v| {
            stop_after_first = asBool(v) orelse
                return fail(diag, .invalid_schema, v.offset, "stop_after_first must be a boolean", .{});
        }
        if (at_least_one and tags.len == 0)
            return fail(diag, .unsatisfiable_constraint, format.offset, "tags_with_separator without tags can never match", .{});
        return .{ .mode = .tags_with_separator, .at_least_one = at_least_one, .stop_after_first = stop_after_first, .triggers = &.{}, .tags = tags };
    }
    return fail(diag, .unsupported_feature, fmt_type_v.offset, "unsupported structural-tag format \"{s}\"", .{fmt_type});
}

fn triggerList(diag: *schema.Diagnostic, v: *json.Value, a: std.mem.Allocator) CompileError![]const []const u8 {
    const items = asArray(v) orelse return fail(diag, .invalid_schema, v.offset, "triggers must be an array", .{});
    if (items.len == 0)
        return fail(diag, .unsupported_feature, v.offset, "an empty trigger list is not supported", .{});
    if (items.len > MAX_TRIGGERS)
        return fail(diag, .unsupported_feature, v.offset, "more than {d} triggers are not supported", .{MAX_TRIGGERS});
    const out = try a.alloc([]const u8, items.len);
    for (items, 0..) |it, i| {
        const s = asString(it) orelse return fail(diag, .invalid_schema, it.offset, "a trigger must be a string", .{});
        if (s.len == 0 or s.len > MAX_TRIGGER)
            return fail(diag, .unsupported_feature, it.offset, "a trigger must be 1..{d} bytes", .{MAX_TRIGGER});
        out[i] = s;
    }
    for (out, 0..) |x, i| {
        for (out[i + 1 ..]) |y| {
            if (std.mem.indexOf(u8, x, y) != null or std.mem.indexOf(u8, y, x) != null)
                return fail(diag, .unsupported_feature, v.offset, "overlapping triggers are not supported", .{});
        }
    }
    return out;
}

fn tagList(diag: *schema.Diagnostic, tags_v: ?*json.Value, owner: *json.Value, require_non_empty: bool, a: std.mem.Allocator) CompileError![]const RawTag {
    const tv = tags_v orelse return fail(diag, .invalid_schema, owner.offset, "the format requires \"tags\"", .{});
    const items = asArray(tv) orelse return fail(diag, .invalid_schema, tv.offset, "tags must be an array", .{});
    if (require_non_empty and items.len == 0)
        return fail(diag, .unsupported_feature, tv.offset, "an empty tag list is not supported", .{});
    if (items.len > MAX_VARIANTS)
        return fail(diag, .unsupported_feature, tv.offset, "more than {d} tag variants are not supported", .{MAX_VARIANTS});
    const out = try a.alloc(RawTag, items.len);
    for (items, 0..) |t, i| {
        try checkKeys(diag, t, &tag_keys, "a tag");
        if (field(t, "type")) |ty| {
            const ts = asString(ty) orelse return fail(diag, .invalid_schema, ty.offset, "tag type must be a string", .{});
            if (!std.mem.eql(u8, ts, "tag"))
                return fail(diag, .unsupported_feature, ty.offset, "unsupported tag type \"{s}\"", .{ts});
        }
        if (field(t, "trigger")) |tg| {
            if (tg.v != .null_v)
                return fail(diag, .unsupported_feature, tg.offset, "a per-tag trigger is not supported; use the format's triggers", .{});
        }
        const begin = (try objectString(diag, t, "begin", "a tag", true)).?;
        const end = (try objectString(diag, t, "end", "a tag", true)).?;
        if (begin.len == 0 or begin.len > MAX_BEGIN)
            return fail(diag, .unsupported_feature, t.offset, "a begin literal must be 1..{d} bytes", .{MAX_BEGIN});
        if (end.len == 0 or end.len > MAX_END)
            return fail(diag, .unsupported_feature, t.offset, "an end literal must be 1..{d} bytes", .{MAX_END});
        const content = field(t, "content") orelse
            return fail(diag, .invalid_schema, t.offset, "a tag requires \"content\"", .{});
        if (!isObject(content))
            return fail(diag, .invalid_schema, content.offset, "tag content must be a JSON object", .{});
        try checkKeys(diag, content, &content_keys, "a tag content");
        if (field(content, "type")) |ct| {
            const cs = asString(ct) orelse return fail(diag, .invalid_schema, ct.offset, "content type must be a string", .{});
            if (!std.mem.eql(u8, cs, "json_schema"))
                return fail(diag, .unsupported_feature, ct.offset, "unsupported tag content type \"{s}\"", .{cs});
        }
        if (field(content, "style")) |sv| {
            if (sv.v != .null_v)
                return fail(diag, .unsupported_feature, sv.offset, "a content style is not supported", .{});
        }
        const js = field(content, "json_schema");
        const sh = field(content, "schema");
        if (js != null and sh != null)
            return fail(diag, .invalid_schema, content.offset, "tag content must not carry both \"json_schema\" and \"schema\"", .{});
        const schema_value = js orelse sh orelse
            return fail(diag, .invalid_schema, content.offset, "tag content requires a schema", .{});
        out[i] = .{ .begin = begin, .end = end, .content = schema_value, .content_offset = schema_value.offset };
    }
    return out;
}

fn writeJsonString(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, s: []const u8) error{OutOfMemory}!void {
    try out.append(a, '"');
    for (s) |b| {
        switch (b) {
            '"' => try out.appendSlice(a, "\\\""),
            '\\' => try out.appendSlice(a, "\\\\"),
            '\n' => try out.appendSlice(a, "\\n"),
            '\r' => try out.appendSlice(a, "\\r"),
            '\t' => try out.appendSlice(a, "\\t"),
            0x08 => try out.appendSlice(a, "\\b"),
            0x0C => try out.appendSlice(a, "\\f"),
            0x00...0x07, 0x0B, 0x0E...0x1F => {
                try out.appendSlice(a, "\\u00");
                const hex = "0123456789abcdef";
                try out.append(a, hex[b >> 4]);
                try out.append(a, hex[b & 0xF]);
            },
            else => try out.append(a, b),
        }
    }
    try out.append(a, '"');
}

/// Serializes a parsed value back to JSON text (object order preserved,
/// number spellings kept verbatim): the content schema is compiled from
/// this text.
fn serializeValue(out: *std.ArrayListUnmanaged(u8), a: std.mem.Allocator, v: *const json.Value) error{OutOfMemory}!void {
    switch (v.v) {
        .object => |pairs| {
            try out.append(a, '{');
            for (pairs, 0..) |p, i| {
                if (i != 0) try out.append(a, ',');
                try writeJsonString(out, a, p.key);
                try out.append(a, ':');
                try serializeValue(out, a, p.value);
            }
            try out.append(a, '}');
        },
        .array => |items| {
            try out.append(a, '[');
            for (items, 0..) |it, i| {
                if (i != 0) try out.append(a, ',');
                try serializeValue(out, a, it);
            }
            try out.append(a, ']');
        },
        .string => |s| try writeJsonString(out, a, s),
        .number => |n| try out.appendSlice(a, n),
        .boolean => |b| try out.appendSlice(a, if (b) "true" else "false"),
        .null_v => try out.appendSlice(a, "null"),
    }
}

fn startsWith(hay: []const u8, needle: []const u8) bool {
    return hay.len >= needle.len and std.mem.eql(u8, hay[0..needle.len], needle);
}

/// Compiles the structural-tag spec. The returned program owns its
/// variant content grammars and literal copies; `diag` carries the
/// message and an offset into the spec bytes for refusals.
pub fn compile(ga: std.mem.Allocator, spec: []const u8, max_depth: u32, strip_lead_space: bool, diag: *schema.Diagnostic, w: *work_mod.Work) CompileError!Program {
    var dom = Arena.init(ga);
    defer dom.deinit();
    const da = dom.allocator();
    try w.charge(@max(1, spec.len / 4096));
    var off: u32 = NO_OFFSET;
    const root = json.parse(da, spec, &off) catch |e| {
        return switch (e) {
            error.InvalidSchema => fail(diag, .invalid_schema, off, "invalid JSON in the structural-tag spec at byte {d}", .{off}),
            error.ResourceLimit => fail(diag, .resource_limit, off, "the structural-tag spec exceeds the depth limit", .{}),
            error.OutOfMemory => error.OutOfMemory,
        };
    };
    const raw = try parseSpec(diag, root, da);

    // Copy the literals into ga storage and build the variants.
    const variants = try ga.alloc(Variant, raw.tags.len);
    var built: usize = 0;
    errdefer {
        for (variants[0..built]) |*v| {
            v.content.deinit();
            ga.free(v.begin);
            ga.free(v.end);
        }
        ga.free(variants);
    }
    for (raw.tags, 0..) |rt, i| {
        const begin = try ga.dupe(u8, rt.begin);
        errdefer ga.free(begin);
        const end = try ga.dupe(u8, rt.end);
        errdefer ga.free(end);
        var buf: std.ArrayListUnmanaged(u8) = .empty;
        defer buf.deinit(da);
        try serializeValue(&buf, da, rt.content);
        const content = schema.compileWith(ga, buf.items, max_depth, diag, w, false, .spec_v1, "", .{}) catch |e| {
            // The diagnostic points into the serialized content; shift it
            // to the spec offset of the schema value.
            if (diag.offset != NO_OFFSET) diag.offset +|= rt.content_offset;
            return switch (e) {
                error.InvalidSchema => error.InvalidSchema,
                error.UnsupportedFeature => error.UnsupportedFeature,
                error.UnsatisfiableConstraint => error.Unsatisfiable,
                error.ResourceLimit => error.ResourceLimit,
                error.Cancelled => error.Cancelled,
                error.OutOfMemory => error.OutOfMemory,
            };
        };
        variants[i] = .{ .begin = begin, .end = end, .content = content };
        built = i + 1;
    }

    // Preliminary validation: every trigger must be a prefix of at least
    // one begin (triggered mode), and in triggered mode every begin must
    // start with at least one trigger.
    if (raw.mode == .triggered_tags) {
        for (raw.triggers) |t| {
            var used = false;
            for (variants) |v| {
                if (startsWith(v.begin, t)) {
                    used = true;
                    break;
                }
            }
            if (!used)
                return fail(diag, .unsupported_feature, root.offset, "a trigger matches no tag begin", .{});
        }
        for (variants) |v| {
            var ok = raw.triggers.len == 0;
            for (raw.triggers) |t| {
                if (startsWith(v.begin, t)) {
                    ok = true;
                    break;
                }
            }
            if (!ok)
                return fail(diag, .unsupported_feature, root.offset, "a tag begin does not start with any trigger", .{});
        }
        if (variants.len == 0 and raw.triggers.len != 0)
            return fail(diag, .unsupported_feature, root.offset, "triggers without tag variants are not supported", .{});
    }
    // Prefix-freeness of the begin literals: the pinning analysis of the
    // state machine relies on it (at most one chain alive past a commit).
    for (variants, 0..) |a, i| {
        for (variants[i + 1 ..]) |b| {
            if (startsWith(b.begin, a.begin) or startsWith(a.begin, b.begin))
                return fail(diag, .unsupported_feature, root.offset, "overlapping tag begin literals are not supported", .{});
        }
    }

    const triggers = try ga.alloc([]const u8, raw.triggers.len);
    for (raw.triggers, 0..) |t, i| triggers[i] = try ga.dupe(u8, t);
    errdefer {
        for (triggers) |t| ga.free(t);
        ga.free(triggers);
    }
    const trigger_eq = try ga.alloc(u64, raw.triggers.len * 256);
    for (triggers, 0..) |t, ti| {
        for (0..256) |bi| {
            var m: u64 = 0;
            for (t, 0..) |tb, k| {
                if (tb == bi) m |= @as(u64, 1) << @intCast(k);
            }
            trigger_eq[ti * 256 + bi] = m;
        }
    }
    errdefer ga.free(trigger_eq);
    const end_eq = try ga.alloc(u64, variants.len * 256);
    for (variants, 0..) |v, vi| {
        for (0..256) |bi| {
            var m: u64 = 0;
            for (v.end, 0..) |eb, k| {
                if (eb == bi) m |= @as(u64, 1) << @intCast(k);
            }
            end_eq[vi * 256 + bi] = m;
        }
    }

    return .{
        .mode = raw.mode,
        .at_least_one = raw.at_least_one,
        .stop_after_first = raw.stop_after_first,
        .strip_lead_space = strip_lead_space,
        .triggers = triggers,
        .trigger_eq = trigger_eq,
        .variants = variants,
        .end_eq = end_eq,
        .a = ga,
    };
}

// ---------------------------------------------------------------------------
// The byte-level state machine
// ---------------------------------------------------------------------------

pub const Phase = enum(u8) { text, begin, json, final };

pub const BeginItem = struct { variant: u8, pos: u16 };

/// The state without the JSON content state: everything history records
/// copies of beyond the parser snapshot.
pub const SmallState = struct {
    phase: Phase = .text,
    started: bool = false,
    doc_started: bool = false,
    endable: bool = false,
    json_dead: bool = false,
    mt: u16 = 1,
    n_begin: u8 = 0,
    json_variant: u8 = NO_VARIANT,
    suffix_bits: u64 = 0,
    partials: [MAX_TRIGGERS]u64 = @splat(0),
    begin_items: [MAX_VARIANTS]BeginItem = @splat(.{ .variant = 0, .pos = 0 }),
};

pub const State = struct {
    small: SmallState = .{},
    /// The JSON content state; valid when `small.phase == .json` and
    /// `!small.json_dead`. Slots handed to the machine for the first time
    /// must carry `json.n == 0`.
    json: parser.State = undefined,
};

pub const FeedError = error{ Parse, ResourceLimit, OutOfMemory };

pub fn initState(p: *const Program, max_threads: u16) State {
    var st: State = undefined;
    st.small = .{ .mt = max_threads };
    st.json.n = 0;
    st.json.max_threads = max_threads;
    switch (p.mode) {
        .triggered_tags => {
            st.small.phase = .text;
            st.small.endable = !p.at_least_one;
        },
        .tags_with_separator => {
            st.small.phase = .begin;
            st.small.endable = !p.at_least_one;
            spawnAllPrefixes(&st.small, p);
        },
    }
    return st;
}

fn spawnAllPrefixes(small: *SmallState, p: *const Program) void {
    small.n_begin = @intCast(p.variants.len);
    for (p.variants, 0..) |_, vi| {
        small.begin_items[vi] = .{ .variant = @intCast(vi), .pos = 0 };
    }
}

pub fn canEnd(st: *const State) bool {
    return st.small.endable;
}

pub const ActiveSegment = struct { phase: Phase, variant: u8 };

/// The active segment of a state for trace attribution: the phase and, when
/// exactly one tag variant is alive (or the stream is inside its content),
/// that variant index; NO_VARIANT for free text and for a begin prefix
/// shared by several variants (no single tag has been chosen yet).
/// Read-only: no session state changes.
pub fn activeSegment(st: *const State) ActiveSegment {
    switch (st.small.phase) {
        .text => return .{ .phase = .text, .variant = NO_VARIANT },
        .begin => return .{
            .phase = .begin,
            .variant = if (st.small.n_begin == 1) st.small.begin_items[0].variant else NO_VARIANT,
        },
        .json, .final => return .{ .phase = st.small.phase, .variant = st.small.json_variant },
    }
}

/// Whether the program holds any content grammar with deferred value
/// verdicts (ADR-0005): only then the mask walk and the draft checks need
/// the completion filter at all.
fn needsReachability(p: *const Program) bool {
    for (p.variants) |v| {
        if (v.content.needs_reachability) return true;
    }
    return false;
}

/// Whether a structtag state can still reach a document completion
/// (ADR-0005 semantics, mirrored for the tag content). Outside an open
/// JSON content the remaining literals can always be fed and the program
/// can end or continue, so only content-open states are filtered: a
/// byte-legal but unfinished content like `-` under `minimum: 10` can
/// never satisfy the schema, and the whole tag can then never complete.
/// An end-literal prefix in progress (`suffix_bits != 0`) is alive
/// regardless of the JSON continuation: the spawn bit is only set where
/// the content could end (`canEnd`), and the remaining bytes of the
/// literal always match mechanically - a content parse failure along the
/// way just switches to the json_dead branch. Without this the JSON
/// check withheld the digit starting an end literal like `99` after
/// `<c>42`: as content `429` is a dead-end prefix, as an end-literal
/// start it completes the tag (and the content state at the spawn point,
/// `42`, is what the tag closes with).
/// The filter condition matches the standalone JSON constraint
/// (mask.zig / c_api.zig): partial vocabularies over finite literal
/// content, or any content with deferred value verdicts.
/// ResourceLimit is UNKNOWN, not alive: the callers propagate it.
pub fn stateAlive(p: *const Program, tok: *const tokenizer.Tokenizer, st: *const State, pool: *Pool, w: *work_mod.Work, side: *parser.Side) complete.Error!bool {
    if (st.small.phase != .json or st.small.json_dead) return true;
    if (st.small.suffix_bits != 0) return true;
    const vi = st.small.json_variant;
    const content = &p.variants[vi].content;
    const filtered = (!tok.byte_complete and content.finite_literal) or content.needs_reachability;
    if (!filtered) return true;
    return complete.stateAlive(content, tok, &st.json, pool.completionFor(vi), w, side);
}

/// Feeds one byte; `out` is fully written and may alias neither `in` nor
/// the scratch states. `jout`/`work` are caller scratch parser states,
/// left empty on return; on Parse both `in` and the logical state are
/// unchanged and `out` is left released. `out.json` must hold either no
/// references or a live state (all slots of the session/pool obey this).
pub fn feedByteInto(p: *const Program, side: *parser.Side, in: *const State, b: u8, jout: *parser.State, work: *parser.State, out: *State) FeedError!void {
    var small = in.small;
    var json_fed = false;
    var json_enter: u8 = NO_VARIANT;

    if (!small.doc_started) {
        small.doc_started = true;
        if (p.strip_lead_space and b == ' ') {
            // The decoder may drop one leading space of the whole text.
            out.small = small;
            parser.releaseState(side, &out.json);
            out.json.n = 0;
            return;
        }
    }
    switch (small.phase) {
        .final => {
            parser.releaseState(side, &out.json);
            out.json.n = 0;
            return error.Parse;
        },
        .text => {
            var committed: u8 = NO_VARIANT;
            for (p.triggers, 0..) |t, ti| {
                const m = ((small.partials[ti] << 1) | 1) & p.triggerEq(ti, b);
                small.partials[ti] = m;
                if ((m >> @intCast(t.len - 1)) & 1 == 1) committed = @intCast(ti);
            }
            if (committed != NO_VARIANT) {
                const t = p.triggers[committed];
                small.phase = .begin;
                small.n_begin = 0;
                small.partials = @splat(0);
                small.endable = false;
                var enter: u8 = NO_VARIANT;
                for (p.variants, 0..) |v, vi| {
                    if (startsWith(v.begin, t)) {
                        if (t.len == v.begin.len) {
                            // The trigger is the whole begin literal: the
                            // tag content starts at the next byte.
                            enter = @intCast(vi);
                        } else {
                            small.begin_items[small.n_begin] = .{ .variant = @intCast(vi), .pos = @intCast(t.len) };
                            small.n_begin += 1;
                        }
                    }
                }
                if (enter != NO_VARIANT) {
                    small.n_begin = 0;
                    small.phase = .json;
                    small.json_variant = enter;
                    small.json_dead = false;
                    small.suffix_bits = 0;
                    json_enter = enter;
                } else if (small.n_begin == 0) {
                    parser.releaseState(side, &out.json);
                    out.json.n = 0;
                    return error.Parse;
                }
            }
        },
        .begin => {
            var n: u8 = 0;
            var enter: u8 = NO_VARIANT;
            for (small.begin_items[0..small.n_begin]) |it| {
                const lit = p.variants[it.variant].begin;
                if (lit[it.pos] != b) continue;
                const np: u16 = it.pos + 1;
                if (@as(usize, np) == lit.len) {
                    enter = it.variant;
                } else {
                    small.begin_items[n] = .{ .variant = it.variant, .pos = np };
                    n += 1;
                }
            }
            small.n_begin = n;
            // Every byte that survives here advances at least one begin
            // literal (a byte matching none rejects the feed): from this
            // point the stream is inside the tag's begin and the document
            // cannot end until the tag completes. Without this a partial
            // begin in tags_with_separator (or after a completed tag)
            // kept the pre-tag endable flag and the mask offered EOS
            // inside an unfinished tag.
            small.endable = false;
            if (enter != NO_VARIANT) {
                // Prefix-free begins: no other item can be alive here.
                small.n_begin = 0;
                small.phase = .json;
                small.json_variant = enter;
                small.json_dead = false;
                small.suffix_bits = 0;
                small.endable = false;
                json_enter = enter;
            } else if (n == 0) {
                parser.releaseState(side, &out.json);
                out.json.n = 0;
                return error.Parse;
            }
        },
        .json => {
            const v = &p.variants[small.json_variant];
            const end = v.end;
            if (!small.json_dead) {
                const spawn: u64 = if (parser.canEnd(&v.content, &in.json) and end[0] == b) 1 else 0;
                small.suffix_bits = ((small.suffix_bits << 1) & p.endEq(small.json_variant, b)) | spawn;
                parser.feedBytes(&v.content, side, &in.json, &[1]u8{b}, jout, work) catch |e| switch (e) {
                    error.Parse => small.json_dead = true,
                    error.ResourceLimit, error.OutOfMemory => {
                        parser.releaseState(side, &out.json);
                        out.json.n = 0;
                        return e;
                    },
                };
                json_fed = !small.json_dead;
            } else {
                small.suffix_bits = (small.suffix_bits << 1) & p.endEq(small.json_variant, b);
            }
            const full: u64 = @as(u64, 1) << @intCast(end.len - 1);
            if (small.suffix_bits & full != 0) {
                if (json_fed) {
                    parser.releaseState(side, jout);
                    jout.n = 0;
                    json_fed = false;
                }
                small.suffix_bits = 0;
                small.started = true;
                small.json_dead = false;
                if (p.stop_after_first) {
                    small.phase = .final;
                } else if (p.mode == .triggered_tags) {
                    small.phase = .text;
                    small.partials = @splat(0);
                } else {
                    small.phase = .begin;
                    spawnAllPrefixes(&small, p);
                }
                small.endable = true;
            } else if (small.json_dead and small.suffix_bits == 0) {
                parser.releaseState(side, &out.json);
                out.json.n = 0;
                return error.Parse;
            }
        },
    }

    out.small = small;
    parser.releaseState(side, &out.json);
    if (json_fed) {
        out.json = jout.*;
        jout.n = 0;
    } else if (json_enter != NO_VARIANT) {
        out.json = parser.initState(&p.variants[json_enter].content, small.mt, side) catch |e| {
            out.json.n = 0;
            return e;
        };
    } else {
        out.json.n = 0;
    }
}

// ---------------------------------------------------------------------------
// Mask construction
// ---------------------------------------------------------------------------

const Frame = struct { node: u32, next_edge: u32 };

/// Trie-walk scratch for structtag masks: one machine state per token byte
/// depth, reused across mask calls (owned by the session, charged to the
/// context temp budget like the parser pool of `mask.MaskBuf`).
pub const Pool = struct {
    a: std.mem.Allocator = undefined,
    states: std.ArrayListUnmanaged(State) = .empty,
    frames: std.ArrayListUnmanaged(Frame) = .empty,
    inited: usize = 0,
    /// Completion-search memo and scratch for the JSON content of the
    /// active tag variant (ADR-0005 filter); `completion_variant` records
    /// which variant's content grammar the memo is keyed for, since the
    /// memo must not be shared between different grammars.
    completion: complete.Cache = undefined,
    completion_variant: u8 = NO_VARIANT,

    pub fn init(a: std.mem.Allocator) Pool {
        return .{ .a = a, .completion = complete.Cache.init(a) };
    }

    pub fn deinit(self: *Pool, side: *parser.Side) void {
        const a = self.a;
        for (self.states.items) |*s| parser.releaseState(side, &s.json);
        self.states.deinit(a);
        self.frames.deinit(a);
        self.completion.deinit();
        self.* = .{ .a = a };
        self.completion = undefined;
    }

    /// The completion cache prepared for the content grammar of variant
    /// `vi` (memoized verdicts are per grammar: switching variants drops
    /// them; the scratch pool is kept).
    fn completionFor(self: *Pool, vi: u8) *complete.Cache {
        if (self.completion_variant != vi) {
            self.completion.reset();
            self.completion_variant = vi;
        }
        return &self.completion;
    }

    fn ensure(self: *Pool, max_stack: u32) !void {
        try self.states.ensureTotalCapacity(self.a, @as(usize, max_stack) + 2);
        try self.frames.ensureTotalCapacity(self.a, @as(usize, max_stack) + 2);
        const cap = self.states.capacity;
        while (self.inited < cap) : (self.inited += 1) {
            self.states.allocatedSlice()[self.inited].json.n = 0;
        }
    }
};

pub const MaskError = error{ DeadEnd, ResourceLimit, Cancelled, OutOfMemory };

fn setBit(out: []u32, id: u32) void {
    out[id / 32] |= @as(u32, 1) << @as(u5, @intCast(id % 32));
}

pub fn fillMask(p: *const Program, tok: *const tokenizer.Tokenizer, st: *const State, out: []u32, w: *work_mod.Work, pool: *Pool, jout: *parser.State, work: *parser.State, side: *parser.Side) MaskError!void {
    const mw = tok.maskWords();
    std.debug.assert(out.len >= mw);
    @memset(out[0..mw], 0);
    if (needsReachability(p)) {
        pool.completion.reset();
        pool.completion.fill_budget = complete.fillBudget();
        pool.completion_variant = NO_VARIANT;
    }
    const trie = &tok.trie;
    try pool.ensure(trie.max_stack);
    pool.states.items.len = 0;
    pool.frames.items.len = 0;
    pool.states.appendAssumeCapacity(st.*);
    if (st.small.phase == .json and !st.small.json_dead)
        parser.retainState(side, &pool.states.items[0].json);
    pool.frames.appendAssumeCapacity(.{ .node = 0, .next_edge = 0 });
    defer {
        for (pool.states.items) |*x| parser.releaseState(side, &x.json);
        pool.states.items.len = 0;
        pool.frames.items.len = 0;
    }
    var any = false;
    while (pool.frames.items.len > 0) {
        try w.charge(1);
        const top = pool.frames.items.len - 1;
        const fr = &pool.frames.items[top];
        const node = fr.node;
        if (fr.next_edge >= trie.node_edge_len[node]) {
            _ = pool.frames.pop();
            while (pool.states.items.len > pool.frames.items.len) {
                parser.releaseState(side, &pool.states.items[pool.states.items.len - 1].json);
                pool.states.items.len -= 1;
            }
            continue;
        }
        const ei = trie.node_edge_off[node] + fr.next_edge;
        fr.next_edge += 1;
        const child = trie.edge_child[ei];
        const dst = &pool.states.allocatedSlice()[top + 1];
        feedByteInto(p, side, &pool.states.items[top], trie.edge_byte[ei], jout, work, dst) catch |err| switch (err) {
            error.Parse => continue,
            error.ResourceLimit => return error.ResourceLimit,
            error.OutOfMemory => return error.OutOfMemory,
        };
        pool.states.items.len = top + 2;
        pool.frames.appendAssumeCapacity(.{ .node = child, .next_edge = 0 });
        const id_head = trie.node_token[child];
        if (id_head != tokenizer.NO_TOKEN) {
            // Completion filter (ADR-0005): a token whose state cannot
            // reach a completed answer is left out of the mask exactly as
            // in the standalone JSON constraint - the mask, the draft
            // check and the accept path must agree.
            const alive = stateAlive(p, tok, dst, pool, w, side) catch |err| switch (err) {
                error.ResourceLimit => return error.ResourceLimit,
                error.Cancelled => return error.Cancelled,
                error.OutOfMemory => return error.OutOfMemory,
            };
            if (alive) {
                var id = id_head;
                while (id != tokenizer.NO_TOKEN) : (id = tok.token_chain[id]) {
                    if (!tok.is_eos[id] and !tok.is_special[id]) {
                        setBit(out, id);
                        any = true;
                    }
                }
            }
        }
    }
    if (canEnd(st)) {
        for (tok.is_eos, 0..) |e, id| {
            if (e) {
                setBit(out, @intCast(id));
                any = true;
            }
        }
    }
    if (!any) return error.DeadEnd;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

const testing = std.testing;

const SET_CODE_PARAMS =
    \\{"type":"object","properties":{"code":{"type":"string","enum":["zq-17-wombat"]}},"required":["code"],"additionalProperties":false}
;

const TRIGGERED_SPEC = "{\"type\":\"structural_tag\",\"format\":{\"type\":\"triggered_tags\",\"triggers\":[\"<tool_call>\"],\"tags\":[" ++
    "{\"type\":\"tag\",\"begin\":\"<tool_call>\\n{\\\"name\\\": \\\"set_code\\\", \\\"arguments\\\": \",\"content\":{\"type\":\"json_schema\",\"json_schema\":" ++ SET_CODE_PARAMS ++ "},\"end\":\"}\\n</tool_call>\"}," ++
    "{\"type\":\"tag\",\"begin\":\"<tool_call>{\\\"name\\\": \\\"set_code\\\", \\\"arguments\\\": \",\"content\":{\"type\":\"json_schema\",\"json_schema\":" ++ SET_CODE_PARAMS ++ "},\"end\":\"}</tool_call>\"}" ++
    "]}}";

const SEPARATED_SPEC = "{\"type\":\"structural_tag\",\"format\":{\"type\":\"tags_with_separator\",\"separator\":\"\",\"at_least_one\":true,\"tags\":[" ++
    "{\"type\":\"tag\",\"begin\":\"<c>\",\"content\":{\"type\":\"json_schema\",\"json_schema\":{\"type\":\"integer\"}},\"end\":\"</c>\"}]}}";

const SEPARATED_FIRST_SPEC = "{\"type\":\"structural_tag\",\"format\":{\"type\":\"tags_with_separator\",\"separator\":\"\",\"at_least_one\":true,\"stop_after_first\":true,\"tags\":[" ++
    "{\"type\":\"tag\",\"begin\":\"<c>\",\"content\":{\"type\":\"json_schema\",\"json_schema\":{\"type\":\"integer\"}},\"end\":\"</c>\"}]}}";

const SEPARATED_OPTIONAL_SPEC = "{\"type\":\"structural_tag\",\"format\":{\"type\":\"tags_with_separator\",\"separator\":\"\",\"at_least_one\":false,\"tags\":[" ++
    "{\"type\":\"tag\",\"begin\":\"<c>\",\"content\":{\"type\":\"json_schema\",\"json_schema\":{\"type\":\"integer\"}},\"end\":\"</c>\"}]}}";

/// Test driver that mirrors the session contract: three state slots, the
/// current one never overwritten during a feed; a failing byte releases
/// every scratch and leaves the logical state untouched.
const Fixture = struct {
    prog: Program,
    side: parser.Side,
    slots: []State,
    cur: u8,
    s0: parser.State,
    s1: parser.State,
    a: std.mem.Allocator,

    fn init(spec: []const u8) !Fixture {
        const a = testing.allocator;
        var diag: schema.Diagnostic = .{};
        var w = work_mod.Work{};
        const prog = try compile(a, spec, 64, false, &diag, &w);
        errdefer {
            var p = prog;
            p.deinit();
        }
        const slots = try a.alloc(State, 3);
        var f: Fixture = .{
            .prog = prog,
            .side = parser.Side.init(a),
            .slots = slots,
            .cur = 0,
            .s0 = undefined,
            .s1 = undefined,
            .a = a,
        };
        f.slots[0] = initState(&f.prog, 16);
        f.slots[1].json.n = 0;
        f.slots[2].json.n = 0;
        f.s0.n = 0;
        f.s1.n = 0;
        return f;
    }

    fn deinit(f: *Fixture) void {
        for (f.slots) |*s| parser.releaseState(&f.side, &s.json);
        f.a.free(f.slots);
        f.prog.deinit();
        f.side.deinit();
    }

    /// Feeds `text` as one token; false and no state change on rejection.
    fn feedToken(f: *Fixture, text: []const u8) bool {
        if (text.len == 0) return true;
        const orig = f.cur;
        const o1: u8 = (orig + 1) % 3;
        const o2: u8 = (orig + 2) % 3;
        var src: u8 = orig;
        var src_is_orig = true;
        var dst: u8 = o1;
        for (text) |b| {
            feedByteInto(&f.prog, &f.side, &f.slots[src], b, &f.s0, &f.s1, &f.slots[dst]) catch {
                // Out was released by the feed; the last good scratch (if
                // it is not the original) is released here; the logical
                // state and the original slot stay untouched.
                if (!src_is_orig) parser.releaseState(&f.side, &f.slots[src].json);
                return false;
            };
            if (!src_is_orig) parser.releaseState(&f.side, &f.slots[src].json);
            src = dst;
            src_is_orig = false;
            dst = if (dst == o1) o2 else o1;
        }
        parser.releaseState(&f.side, &f.slots[orig].json);
        f.cur = src;
        return true;
    }

    fn feedText(f: *Fixture, text: []const u8) bool {
        for (text) |b| {
            var one: [1]u8 = .{b};
            if (!f.feedToken(&one)) return false;
        }
        return true;
    }

    fn canEnd(f: *Fixture) bool {
        return module.canEnd(&f.slots[f.cur]);
    }
};

test "structtag: hermes-shaped triggered tags accept text, tag and text" {
    var f = try Fixture.init(TRIGGERED_SPEC);
    defer f.deinit();
    try testing.expect(f.canEnd());
    try testing.expect(f.feedToken("Hello "));
    try testing.expect(f.canEnd());
    // A full trigger commits: the begin literal must follow.
    try testing.expect(f.feedToken("<tool_call>"));
    try testing.expect(!f.canEnd());
    try testing.expect(!f.feedToken("Z"));
    // The other format continues right after the trigger.
    try testing.expect(f.feedToken("{\"name\": \"set_code\", \"arguments\": "));
    try testing.expect(!f.feedToken("X"));
    try testing.expect(f.feedToken("{\"code\":\"zq-17-wombat\"}"));
    try testing.expect(!f.canEnd()); // the tag is not closed yet
    try testing.expect(f.feedToken("}</tool_call>"));
    try testing.expect(f.canEnd());
    try testing.expect(f.feedToken("done"));
    try testing.expect(f.canEnd());
}

test "structtag: the JSON content gates the suffix and can end inside" {
    var f = try Fixture.init(TRIGGERED_SPEC);
    defer f.deinit();
    try testing.expect(f.feedToken("<tool_call>\n{\"name\": \"set_code\", \"arguments\": "));
    // A value outside the enum is rejected by the content grammar.
    try testing.expect(!f.feedToken("{\"code\":\"nope\"}"));
    try testing.expect(f.feedToken("{\"code\":\"zq-17-wombat\"}"));
    // The end literal continues the same byte stream.
    try testing.expect(f.feedToken("}\n</tool_call>"));
    try testing.expect(f.canEnd());
}

test "structtag: tags_with_separator requires adjacent tags and supports repetition" {
    var f = try Fixture.init(SEPARATED_SPEC);
    defer f.deinit();
    try testing.expect(!f.canEnd()); // at least one tag required
    try testing.expect(!f.feedToken("x")); // no free text
    try testing.expect(f.feedToken("<c>4"));
    try testing.expect(!f.canEnd());
    try testing.expect(f.feedToken("</c>"));
    try testing.expect(f.canEnd());
    const seg = module.activeSegment(&f.slots[f.cur]);
    try testing.expectEqual(Phase.begin, seg.phase);
    try testing.expectEqual(@as(u8, 0), seg.variant);
    try testing.expect(f.feedToken("<c>"));
    try testing.expect(f.feedToken("5"));
    try testing.expect(f.feedToken("</c>"));
    try testing.expect(f.canEnd());
}

test "structtag: a partial begin in tags_with_separator cannot end" {
    var f = try Fixture.init(SEPARATED_SPEC);
    defer f.deinit();
    try testing.expect(!f.canEnd()); // at least one tag required
    try testing.expect(f.feedToken("<c>4</c>"));
    try testing.expect(f.canEnd());
    // A second tag may start at the boundary, but once its first byte is
    // there the document cannot end until the tag completes (the pinned
    // xgrammar blocks EOS at every partial begin of tags_with_separator).
    try testing.expect(f.feedToken("<"));
    try testing.expect(!f.canEnd());
    try testing.expect(!f.feedToken("x")); // neither a begin continuation nor EOS
    try testing.expect(!f.canEnd());
    try testing.expect(f.feedToken("c>"));
    try testing.expect(!f.canEnd());
    try testing.expect(f.feedToken("5</c>"));
    try testing.expect(f.canEnd());
}

test "structtag: an optional tag can end only before its first byte" {
    var f = try Fixture.init(SEPARATED_OPTIONAL_SPEC);
    defer f.deinit();
    try testing.expect(f.canEnd()); // the empty document is valid
    try testing.expect(f.feedToken("<"));
    try testing.expect(!f.canEnd()); // the partial begin must complete
    try testing.expect(f.feedToken("c>4</c>"));
    try testing.expect(f.canEnd());
    try testing.expect(f.feedToken("<c"));
    try testing.expect(!f.canEnd());
    try testing.expect(f.feedToken(">4</c>"));
    try testing.expect(f.canEnd());
}

test "structtag: a partial trigger stays free text and can end" {
    // Triggered mode keeps the xgrammar behavior: text that ends inside a
    // trigger prefix is a free-text tail and may end; only a committed
    // tag blocks EOS (the separator begins above are the blocking case).
    var f = try Fixture.init(TRIGGERED_SPEC);
    defer f.deinit();
    try testing.expect(f.feedToken("hello <tool_"));
    try testing.expect(f.canEnd());
    try testing.expect(f.feedToken("call>")); // commits the trigger
    try testing.expect(!f.canEnd());
    try testing.expect(f.feedText("\n{\"name\": \"set_code\", \"arguments\": "));
    try testing.expect(!f.canEnd());
    try testing.expect(f.feedToken("{\"code\":\"zq-17-wombat\"}"));
    try testing.expect(f.feedToken("}\n</tool_call>"));
    try testing.expect(f.canEnd());
}

test "structtag: activeSegment names the variant or stays ambiguous" {
    var f = try Fixture.init(TRIGGERED_SPEC);
    defer f.deinit();
    var seg = module.activeSegment(&f.slots[f.cur]);
    try testing.expectEqual(Phase.text, seg.phase);
    try testing.expectEqual(NO_VARIANT, seg.variant);
    // The trigger is shared by both variants: the begin prefix is ambiguous.
    try testing.expect(f.feedToken("<tool_call>"));
    seg = module.activeSegment(&f.slots[f.cur]);
    try testing.expectEqual(Phase.begin, seg.phase);
    try testing.expectEqual(NO_VARIANT, seg.variant);
    // The newline picks one variant chain: the active tag is known.
    try testing.expect(f.feedToken("\n"));
    seg = module.activeSegment(&f.slots[f.cur]);
    try testing.expectEqual(Phase.begin, seg.phase);
    try testing.expectEqual(@as(u8, 0), seg.variant);
    try testing.expect(f.feedText("{\"name\": \"set_code\", \"arguments\": "));
    seg = module.activeSegment(&f.slots[f.cur]);
    try testing.expectEqual(Phase.json, seg.phase);
    try testing.expectEqual(@as(u8, 0), seg.variant);
    try testing.expect(f.feedToken("{\"code\":\"zq-17-wombat\"}"));
    try testing.expect(f.feedToken("}\n</tool_call>"));
    seg = module.activeSegment(&f.slots[f.cur]);
    try testing.expectEqual(Phase.text, seg.phase);
    try testing.expectEqual(NO_VARIANT, seg.variant);
}

test "structtag: stop_after_first closes the language" {
    var f = try Fixture.init(SEPARATED_FIRST_SPEC);
    defer f.deinit();
    try testing.expect(f.feedToken("<c>4</c>"));
    try testing.expect(f.canEnd());
    try testing.expect(!f.feedToken(" "));
    try testing.expect(!f.feedToken("<c>"));
}

test "structtag: any_text accepts anything" {
    var f = try Fixture.init("{\"type\":\"structural_tag\",\"format\":{\"type\":\"any_text\"}}");
    defer f.deinit();
    try testing.expect(f.canEnd());
    try testing.expect(f.feedToken("plain text <tool> {not json}"));
    try testing.expect(f.canEnd());
}

test "structtag: legacy structures shape" {
    const spec = "{\"type\":\"structural_tag\",\"structures\":[{\"begin\":\"<call>\",\"schema\":" ++ SET_CODE_PARAMS ++ ",\"end\":\"</call>\"}],\"triggers\":[\"<call>\"]}";
    var f = try Fixture.init(spec);
    defer f.deinit();
    try testing.expect(f.feedText("prefix "));
    // A partial trigger stays plain text and can end.
    try testing.expect(f.feedToken("<cal"));
    try testing.expect(f.canEnd());
    // The completing byte commits; the begin literal is the whole trigger,
    // so the content starts on the next byte.
    try testing.expect(f.feedToken("l>"));
    try testing.expect(!f.canEnd());
    try testing.expect(!f.feedToken("X"));
    try testing.expect(f.feedToken("{\"code\":\"zq-17-wombat\"}"));
    try testing.expect(f.feedToken("</call>"));
    try testing.expect(f.canEnd());
}

test "structtag: refusals name the unsupported construct" {
    const cases = [_]struct { spec: []const u8, want: CompileError }{
        .{ .spec = "{\"type\":\"structural_tag\",\"format\":{\"type\":\"sequence\"}}", .want = error.UnsupportedFeature },
        .{ .spec = "{\"type\":\"structural_tag\",\"format\":{\"type\":\"triggered_tags\",\"triggers\":[\"<t>\"],\"tags\":[]}}", .want = error.UnsupportedFeature },
        .{ .spec = "{\"type\":\"structural_tag\",\"format\":{\"type\":\"tags_with_separator\",\"separator\":\" \",\"at_least_one\":true,\"tags\":[{\"begin\":\"<\",\"content\":{\"json_schema\":{\"type\":\"integer\"}},\"end\":\">\"}]}}", .want = error.UnsupportedFeature },
        .{ .spec = "{\"type\":\"structural_tag\",\"format\":{\"type\":\"triggered_tags\",\"triggers\":[\"<t>\"],\"tags\":[{\"begin\":\"<t>{\",\"content\":{\"json_schema\":{\"type\":\"integer\"}},\"end\":\">\"},{\"begin\":\"<t>{\\\"x\",\"content\":{\"json_schema\":{\"type\":\"integer\"}},\"end\":\">\"}]}}", .want = error.UnsupportedFeature },
        .{ .spec = "{\"type\":\"structural_tag\",\"format\":{\"type\":\"triggered_tags\",\"triggers\":[\"<t>\"],\"tags\":[{\"begin\":\"<t>\",\"content\":{\"json_schema\":{\"type\":\"integer\"}},\"end\":\">\",\"style\":\"x\"}]}}", .want = error.UnsupportedFeature },
        .{ .spec = "{\"type\":\"structural_tag\",\"format\":{\"type\":\"triggered_tags\",\"triggers\":[\"<t>\",\"t>\"],\"tags\":[{\"begin\":\"<t>\",\"content\":{\"json_schema\":{\"type\":\"integer\"}},\"end\":\">\"}]}}", .want = error.UnsupportedFeature },
        .{ .spec = "{\"type\":\"structural_tag\",\"format\":{\"type\":\"triggered_tags\",\"triggers\":[\"<t>\"],\"tags\":[{\"begin\":\"<t>\",\"content\":{\"json_schema\":{\"type\":\"integer\"}},\"end\":\">\"}]},\"extra\":1}", .want = error.UnsupportedFeature },
        .{ .spec = "{\"type\":\"structural_tag\",\"format\":{\"type\":\"tags_with_separator\",\"separator\":\"\"}}", .want = error.InvalidSchema },
        .{ .spec = "not json", .want = error.InvalidSchema },
    };
    for (cases) |case| {
        var diag: schema.Diagnostic = .{};
        var w = work_mod.Work{};
        const result = compile(testing.allocator, case.spec, 64, false, &diag, &w);
        if (result) |got| {
            var prog = got;
            prog.deinit();
            std.debug.print("expected a refusal for: {s}\n", .{case.spec});
            return error.ExpectedRefusal;
        } else |e| {
            try testing.expectEqual(case.want, e);
            try testing.expect(diag.message_len > 0);
        }
    }
}

test "structtag: a failing byte leaves the source state untouched" {
    var f = try Fixture.init(TRIGGERED_SPEC);
    defer f.deinit();
    try testing.expect(f.feedToken("<tool_call>\n{\"name\": \"set_code\", \"arguments\": "));
    // Reject mid-token: the byte 'X' kills the JSON start, and the state
    // must still accept the correct continuation afterwards.
    try testing.expect(!f.feedText("X"));
    try testing.expect(f.feedToken("{\"code\":\"zq-17-wombat\"}"));
    try testing.expect(f.feedToken("}\n</tool_call>"));
    try testing.expect(f.canEnd());
}

test "structtag: a rejected token mid-way leaves the state byte-identical" {
    var f = try Fixture.init(TRIGGERED_SPEC);
    defer f.deinit();
    try testing.expect(f.feedToken("<tool_call>{\"name\": \"set_code\", \"arguments\": "));
    // Two good bytes then a bad one inside one token.
    try testing.expect(!f.feedToken("{\"cX"));
    try testing.expect(f.feedToken("{\"code\":\"zq-17-wombat\"}"));
    try testing.expect(f.feedToken("}</tool_call>"));
    try testing.expect(f.canEnd());
}
