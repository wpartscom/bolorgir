const std = @import("std");
const grammar = @import("grammar.zig");
const json = @import("json.zig");
const parser = @import("parser.zig");
const pattern = @import("pattern.zig");
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

/// Compile profile: canonical-v1 is the frozen baseline; spec-v1 enables
/// the extended spec-v1 semantics (ROADMAP rev-2 P1) without changing any
/// canonical-v1 behavior.
pub const Profile = enum { canonical_v1, spec_v1 };

/// JSON Schema dialect (dialect-matrix section 1): detected from the root
/// $schema in spec-v1, defaulting to 2020-12 when absent.
pub const Dialect = enum { draft04, draft06, draft07, d2019_09, d2020_12 };

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
/// spec-v1 P5 (ADR-0008): bounded unrolling of recursive `$ref`. A ref
/// whose target is already being expanded (a cycle) is expanded again
/// while this per-path budget lasts; at the bottom the position compiles
/// to the empty-language node, so the accepted language is exactly
/// "documents whose recursion nesting along any path stays within the
/// budget" and the limit is mask-visible (a banned key / a dropped arm),
/// never a runtime dead end.
const REF_UNROLL_CAP: u32 = 8;

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
    // spec-v1 P2 (dialect applicability is checked before matching).
    pattern_props: ?*const json.Value = null,
    prop_names: ?*const json.Value = null,
    min_props: ?*const json.Value = null,
    max_props: ?*const json.Value = null,
    dependencies: ?*const json.Value = null,
    dep_required: ?*const json.Value = null,
    dep_schemas: ?*const json.Value = null,
    prefix_items: ?*const json.Value = null,
    additional_items: ?*const json.Value = null,
    contains: ?*const json.Value = null,
    min_contains: ?*const json.Value = null,
    max_contains: ?*const json.Value = null,
    unique_items: ?*const json.Value = null,
    // spec-v1 P3: boolean combinators.
    any_of: ?*const json.Value = null,
    one_of: ?*const json.Value = null,
    all_of: ?*const json.Value = null,
    not_: ?*const json.Value = null,
    if_: ?*const json.Value = null,
    then_: ?*const json.Value = null,
    else_: ?*const json.Value = null,
    // spec-v1 P4: ECMA-262-subset regex over strings.
    pattern_: ?*const json.Value = null,
    // spec-v1 P6a: unevaluated* (2019-09/2020-12 only; dialect-filtered in
    // the scan loop, ignored in earlier drafts).
    uneval_props: ?*const json.Value = null,
    uneval_items: ?*const json.Value = null,
    // spec-v1 P6b: dynamic/recursive references ($dynamicRef is 2020-12,
    // $recursiveRef is 2019-09; dialect-filtered in the scan loop).
    dyn_ref: ?*const json.Value = null,
    rec_ref: ?*const json.Value = null,
    // spec-v1 P4: numeric range and divisibility. exclusive* carry the
    // dialect-normalized form: the draft-06+ standalone numbers in
    // excl_min/excl_max, the draft-04 boolean modifiers in
    // excl_min_bool/excl_max_bool (form-checked in the scan loop).
    minimum: ?*const json.Value = null,
    maximum: ?*const json.Value = null,
    multiple_of: ?*const json.Value = null,
    excl_min: ?*const json.Value = null,
    excl_max: ?*const json.Value = null,
    excl_min_bool: ?bool = null,
    excl_max_bool: ?bool = null,
};

fn isAnnotation(key: []const u8) bool {
    const list = [_][]const u8{ "title", "description", "$comment", "examples", "default" };
    for (list) |k| {
        if (std.mem.eql(u8, key, k)) return true;
    }
    return false;
}

/// Key membership of a parsed JSON object value (keys are unique -
/// src/json.zig rejects duplicates as InvalidSchema).
fn jsonObjectHasKey(pairs: []const json.Pair, name: []const u8) bool {
    for (pairs) |p| {
        if (std.mem.eql(u8, p.key, name)) return true;
    }
    return false;
}

/// Keywords that are pure annotations under every spec-v1 dialect (and so
/// are ignored there); canonical-v1 still refuses them.
fn isSpecV1Annotation(key: []const u8) bool {
    const list = [_][]const u8{ "deprecated", "readOnly", "writeOnly", "format" };
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
        "deprecated",       "minProperties",    "maxProperties",     "unevaluatedItems",
    };
    for (list) |k| {
        if (std.mem.eql(u8, key, k)) return true;
    }
    return false;
}

/// Applicator positions whose values are subschemas (used by the anchor
/// pre-scan so that data positions like enum/const/default are never
/// mistaken for schemas). `items` is handled separately (schema or tuple).
fn isApplicatorObjectValues(key: []const u8) bool {
    const list = [_][]const u8{ "$defs", "definitions", "properties", "patternProperties", "dependentSchemas", "dependencies" };
    for (list) |k| {
        if (std.mem.eql(u8, key, k)) return true;
    }
    return false;
}

fn isApplicatorArray(key: []const u8) bool {
    const list = [_][]const u8{ "allOf", "anyOf", "oneOf", "prefixItems" };
    for (list) |k| {
        if (std.mem.eql(u8, key, k)) return true;
    }
    return false;
}

fn isApplicatorSingle(key: []const u8) bool {
    const list = [_][]const u8{ "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames", "unevaluatedProperties", "unevaluatedItems", "additionalItems" };
    for (list) |k| {
        if (std.mem.eql(u8, key, k)) return true;
    }
    return false;
}

/// True when `key` is a known-but-unimplemented keyword of dialect `d`
/// (dialect-matrix sections 4-5): only then does it refuse; a keyword of
/// another dialect is unknown here and ignored (section 2, rule 2).
/// exclusiveMinimum/Maximum are handled separately (form check, rule 1).
fn keywordInDialect(key: []const u8, d: Dialect) bool {
    const all = [_][]const u8{ "anyOf", "oneOf", "allOf", "not", "minimum", "maximum", "multipleOf", "pattern", "patternProperties", "uniqueItems", "minProperties", "maxProperties" };
    for (all) |k| {
        if (std.mem.eql(u8, key, k)) return true;
    }
    const ord = @intFromEnum(d); // draft04 .. d2020_12 in declaration order
    const Group = struct { keys: []const []const u8, lo: usize, hi: usize };
    const groups = [_]Group{
        .{ .keys = &.{ "if", "then", "else" }, .lo = 2, .hi = 4 },
        .{ .keys = &.{ "contains", "propertyNames" }, .lo = 1, .hi = 4 },
        .{ .keys = &.{"dependencies"}, .lo = 0, .hi = 2 },
        .{ .keys = &.{ "minContains", "maxContains", "dependentRequired", "dependentSchemas", "unevaluatedProperties", "unevaluatedItems", "contentSchema" }, .lo = 3, .hi = 4 },
        .{ .keys = &.{"$recursiveRef"}, .lo = 3, .hi = 3 },
        .{ .keys = &.{ "$dynamicRef", "prefixItems" }, .lo = 4, .hi = 4 },
        .{ .keys = &.{"additionalItems"}, .lo = 0, .hi = 3 },
    };
    for (groups) |g| {
        for (g.keys) |k| {
            if (std.mem.eql(u8, key, k)) return ord >= g.lo and ord <= g.hi;
        }
    }
    return false;
}

/// $vocabulary support (dialect-matrix section 2, rule 4): the Core,
/// Applicator, Validation, Meta-Data vocabularies of the two newest
/// drafts, plus 2020-12 Format-Annotation. The assertion-bearing format
/// vocabularies (2019-09 format, 2020-12 Format-Assertion) and the
/// Content/Unevaluated vocabularies are not implemented.
fn vocabularySupported(uri: []const u8, d: Dialect) bool {
    const prefix = if (d == .d2019_09)
        "https://json-schema.org/draft/2019-09/vocab/"
    else
        "https://json-schema.org/draft/2020-12/vocab/";
    if (!std.mem.startsWith(u8, uri, prefix)) return false;
    const name = uri[prefix.len..];
    const common = [_][]const u8{ "core", "applicator", "validation", "meta-data" };
    for (common) |k| {
        if (std.mem.eql(u8, name, k)) return true;
    }
    return d == .d2020_12 and std.mem.eql(u8, name, "format-annotation");
}

/// RFC 3986 section 3.1 scheme: ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )
/// followed by ':'. Used to tell absolute URIs from relative references.
fn hasUriScheme(s: []const u8) bool {
    if (s.len == 0) return false;
    const c0 = s[0];
    if (!((c0 >= 'A' and c0 <= 'Z') or (c0 >= 'a' and c0 <= 'z'))) return false;
    for (s[1..]) |b| {
        if (b == ':') return true;
        const ok = (b >= 'A' and b <= 'Z') or (b >= 'a' and b <= 'z') or
            (b >= '0' and b <= '9') or b == '+' or b == '-' or b == '.';
        if (!ok) return false;
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

pub fn compile(child_allocator: std.mem.Allocator, schema_bytes: []const u8, max_depth: u32, diag: *Diagnostic, w: *work_mod.Work, strip_lead_space: bool, profile: Profile, registry_bytes: []const u8) CompileError!grammar.Grammar {
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
        .profile = profile,
    };
    try c.ref_stack.append(a, "#");
    if (profile == .spec_v1) {
        c.dialect = try c.detectDialect();
        try c.collectAnchors(root, root, "");
        try c.resource_stack.append(a, root);
        try c.base_stack.append(a, c.res_base.get(root) orelse "");
        try c.ref_ptrs.append(a, root);
        if (registry_bytes.len > 0) try c.loadRegistry(registry_bytes);
    } else if (registry_bytes.len > 0) {
        return c.fail(error.UnsupportedFeature, NO_OFFSET, "a registry snapshot requires the spec-v1 profile", .{});
    }
    var root_id = try c.compileSchema(root, 1);
    if (profile == .spec_v1 and c.isEmptyNode(root_id)) {
        // An unproductive recursion (a $ref cycle with no base case, e.g.
        // {"$ref":"#"}) bottoms out at the empty language everywhere:
        // the whole schema is unsatisfiable, not an empty-language node.
        return c.fail(error.UnsatisfiableConstraint, 0, "schema defines an empty language (unproductive recursive $ref)", .{});
    }
    if (strip_lead_space) {
        // Strip(" ", start=1, stop=0) drops one leading space of the whole
        // text: the byte stream may carry a space that the decoded text
        // does not have. Canonical JSON documents never start with a
        // space, so accepting {" " + L} u L matches the decoded text.
        const space = try c.addNode(.{ .literal = try c.builder.addLiteral(" ") });
        const seq_ids = try c.builder.copyNodeIds(&[_]grammar.NodeId{ space, root_id });
        const seq = try c.addNode(.{ .seq = seq_ids });
        root_id = try c.addNode(try c.builder.alternativesNode(&[_]grammar.NodeId{ seq, root_id }));
    }
    const ident = if (registry_bytes.len == 0)
        grammar.identityOf(.json_schema, schema_bytes)
    else
        // P5 (ADR-0006 D5, ADR-0008): the registry snapshot bytes join the
        // grammar identity, so mask-cache entries and artifacts are
        // invalidated by a registry swap.
        grammar.identityOf2(.json_schema, schema_bytes, registry_bytes);
    return c.builder.finish(arena, .json_schema, root_id, ident);
}

const Compiler = struct {
    /// Plain-name fragment definition (dialect-matrix section 3): `name`
    /// defined at subschema `target` inside the resource rooted at `res`.
    const Anchor = struct { res: *const json.Value, name: []const u8, target: *const json.Value };
    /// spec-v1 P6b: a schema resource indexed by its absolute base URI (no
    /// fragment), so that a $ref/$dynamicRef whose URI part resolves to it
    /// jumps into the same document instead of the registry.
    const Resource = struct { uri: []const u8, value: *const json.Value };
    /// External-$ref registry document (spec-v1 P5, ADR-0006 D5 / ADR-0008):
    /// an immutable snapshot entry. `dialect` and the anchor pre-scan are
    /// computed lazily on the first reference into the document.
    const RegDoc = struct {
        uri: []const u8,
        root: *const json.Value,
        dialect: ?Dialect = null,
        anchors_done: bool = false,
    };
    a: std.mem.Allocator,
    builder: grammar.Builder,
    root: *const json.Value,
    max_depth: u32,
    diag: *Diagnostic,
    w: *work_mod.Work,
    profile: Profile,
    dialect: Dialect = .d2020_12,
    defs: ?*const json.Value = null,
    ref_stack: std.ArrayListUnmanaged([]const u8) = .{},
    // spec-v1 $ref resolution state (dialect-matrix section 3): the stack
    // of enclosing resource roots (id/$id scopes; bottom is the document
    // root), the stack of ref targets for cycle detection by identity, and
    // the pre-collected plain-name anchors.
    resource_stack: std.ArrayListUnmanaged(*const json.Value) = .{},
    ref_ptrs: std.ArrayListUnmanaged(*const json.Value) = .{},
    // spec-v1 P5 (ADR-0008): remaining recursion-unroll budget along the
    // current expansion path (decremented at every cycle re-entry,
    // restored on return; < REF_UNROLL_CAP means "inside an unroll").
    recursion_left: u32 = REF_UNROLL_CAP,
    anchors: std.ArrayListUnmanaged(Anchor) = .{},
    // spec-v1 P6b: dynamic anchors ($dynamicAnchor, 2020-12) by resource,
    // in-document resources by absolute base URI, the base-URI stack running
    // in parallel with resource_stack, and the dynamic scope (the chain of
    // resources entered to reach the position under compile).
    dyn_anchors: std.ArrayListUnmanaged(Anchor) = .{},
    resources: std.ArrayListUnmanaged(Resource) = .{},
    res_base: std.AutoHashMapUnmanaged(*const json.Value, []const u8) = .{},
    base_stack: std.ArrayListUnmanaged([]const u8) = .{},
    dyn_stack: std.ArrayListUnmanaged(*const json.Value) = .{},
    // spec-v1 P5: external-$ref registry snapshot (empty = no registry).
    registry: []RegDoc = &.{},
    // spec-v1 P6a (ADR-0009): unevaluated* scenario synthesis - ref targets
    // already expanded along the current unevaluated* path (cycle cut).
    uneval_visited: std.ArrayListUnmanaged(*const json.Value) = .{},
    // ADR-0009: while an unevaluated* scenario walk runs, uneval_mask holds
    // the container kinds the guards govern (object/array); cores and
    // combinator branches compiled in this window may assume the instance is
    // of a masked kind (outside kinds are covered by a single top-level arm
    // of compileUnevalScenarios). top_mask is the one-shot carrier consumed
    // by the next compileTypedSpecCore entry.
    uneval_mask: u8 = 0,
    top_mask: u8 = 0,
    // Node ids produced by compileAnyJSON: the universal value language,
    // used by the object-merge shortcut (conjValues absorbs them).
    any_nodes: std.AutoHashMapUnmanaged(grammar.NodeId, void) = .{},
    // First undecidable position of the compile-time enum/const
    // value validator (the refusal carries its offset and keyword).
    static_unknown_kw: []const u8 = "",
    static_unknown_off: u32 = 0,
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

    /// Keyword that changes the base-URI scope in this dialect
    /// (dialect-matrix sections 3 and 8: draft-04 `id` is `$id`).
    fn idKeyword(self: *const Compiler) []const u8 {
        return if (self.dialect == .draft04) "id" else "$id";
    }

    /// The five known dialect identifiers (dialect-matrix section 1) with
    /// optional trailing '#' and http/https equivalence; null when unknown.
    fn matchDialectUri(s: []const u8) ?Dialect {
        var uri = s;
        if (uri.len > 0 and uri[uri.len - 1] == '#') uri = uri[0 .. uri.len - 1];
        // http/https equivalence: compare the part after the scheme.
        var rest = uri;
        if (std.mem.startsWith(u8, rest, "http://")) {
            rest = rest["http://".len..];
        } else if (std.mem.startsWith(u8, rest, "https://")) {
            rest = rest["https://".len..];
        }
        const map = .{
            .{ "json-schema.org/draft-04/schema", Dialect.draft04 },
            .{ "json-schema.org/draft-06/schema", Dialect.draft06 },
            .{ "json-schema.org/draft-07/schema", Dialect.draft07 },
            .{ "json-schema.org/draft/2019-09/schema", Dialect.d2019_09 },
            .{ "json-schema.org/draft/2020-12/schema", Dialect.d2020_12 },
        };
        inline for (map) |entry| {
            if (std.mem.eql(u8, rest, entry[0])) return entry[1];
        }
        return null;
    }

    /// Root $schema detection (dialect-matrix section 1): absent means
    /// 2020-12; anything else refuses with pointer /$schema.
    fn detectDialect(self: *Compiler) CompileError!Dialect {
        const pairs = switch (self.root.v) {
            .object => |p| p,
            else => return .d2020_12, // boolean root schema: no $schema
        };
        for (pairs) |p| {
            if (!std.mem.eql(u8, p.key, "$schema")) continue;
            const s = switch (p.value.v) {
                .string => |s| s,
                else => {
                    try self.path.append(self.a, "$schema");
                    defer _ = self.path.pop();
                    return self.fail(error.InvalidSchema, p.value.offset, "$schema must be a string", .{});
                },
            };
            if (matchDialectUri(s)) |d| return d;
            try self.path.append(self.a, "$schema");
            defer _ = self.path.pop();
            return self.fail(error.UnsupportedFeature, p.value.offset, "unsupported $schema dialect '{s}'", .{s});
        }
        return .d2020_12;
    }

    /// spec-v1 P5 (ADR-0006 D5, ADR-0008): parse the external-$ref registry
    /// snapshot - {"version": <annotation>, "documents": {"<uri>": <schema>}}.
    /// The snapshot is immutable by contract; its exact bytes already joined
    /// the artifact key and the grammar identity. Document dialects and
    /// anchors are computed lazily on the first reference into a document.
    fn loadRegistry(self: *Compiler, registry_bytes: []const u8) CompileError!void {
        try self.w.charge(@max(1, registry_bytes.len / 4096));
        var off: u32 = std.math.maxInt(u32);
        const rroot = json.parse(self.a, registry_bytes, &off) catch |e| {
            self.diag.offset = off;
            return reportJsonError(self.diag, e);
        };
        const rpairs = switch (rroot.v) {
            .object => |p| p,
            else => return self.fail(error.InvalidSchema, NO_OFFSET, "registry must be a JSON object", .{}),
        };
        var docs_val: ?*const json.Value = null;
        for (rpairs) |p| {
            if (std.mem.eql(u8, p.key, "documents")) docs_val = p.value;
            // "version" and unknown keys are annotations, ignored.
        }
        const dv = docs_val orelse
            return self.fail(error.InvalidSchema, NO_OFFSET, "registry must declare 'documents'", .{});
        const dpairs = switch (dv.v) {
            .object => |p| p,
            else => return self.fail(error.InvalidSchema, NO_OFFSET, "registry 'documents' must be an object", .{}),
        };
        const docs = try self.a.alloc(RegDoc, dpairs.len);
        for (dpairs, 0..) |p, i| {
            switch (p.value.v) {
                .object, .boolean => {},
                else => return self.fail(error.InvalidSchema, NO_OFFSET, "registry document '{s}' must be a schema", .{p.key}),
            }
            docs[i] = .{ .uri = p.key, .root = p.value };
        }
        self.registry = docs;
    }

    /// Pre-collect plain-name anchors (id/$id '#name' fragments per
    /// dialect, $anchor in 2019-09+, $dynamicAnchor in 2020-12) and index
    /// every id/$id resource by its absolute base URI (P6b), walking only
    /// applicator positions, so data inside enum/const/default is never
    /// mistaken for a subschema. `base` is the absolute base URI in effect
    /// ("" for a main document without a known retrieval URI).
    fn collectAnchors(self: *Compiler, v: *const json.Value, resroot: *const json.Value, base: []const u8) CompileError!void {
        const pairs = switch (v.v) {
            .object => |p| p,
            else => return, // boolean schema: no keywords, no children
        };
        var rr = resroot;
        var vb = base;
        const idk = self.idKeyword();
        for (pairs) |p| {
            if (std.mem.eql(u8, p.key, idk)) {
                const sv = switch (p.value.v) {
                    .string => |s| s,
                    else => continue,
                };
                rr = v; // any id/$id opens a resource scope
                // The base part of the id (before '#') resolves against the
                // enclosing base and indexes the resource (P6b).
                var idbase = sv;
                if (std.mem.indexOfScalar(u8, sv, '#')) |hi| idbase = sv[0..hi];
                vb = try self.resolveUriRef(base, idbase);
                try self.res_base.put(self.a, v, vb);
                try self.resources.append(self.a, .{ .uri = vb, .value = v });
                // draft-04 id / draft-06/07 $id define a plain name through
                // the URI fragment; in 2019-09+ $id may not carry one.
                if (self.dialect == .draft04 or self.dialect == .draft06 or self.dialect == .draft07) {
                    if (std.mem.indexOfScalar(u8, sv, '#')) |hi| {
                        const name = sv[hi + 1 ..];
                        if (name.len > 0) try self.addAnchor(rr, name, v, p.value.offset);
                    }
                }
            }
        }
        if (self.dialect == .d2019_09 or self.dialect == .d2020_12) {
            for (pairs) |p| {
                if (std.mem.eql(u8, p.key, "$anchor")) {
                    switch (p.value.v) {
                        .string => |name| try self.addAnchor(rr, name, v, p.value.offset),
                        else => {},
                    }
                }
            }
        }
        if (self.dialect == .d2020_12) {
            for (pairs) |p| {
                if (std.mem.eql(u8, p.key, "$dynamicAnchor")) {
                    const name = switch (p.value.v) {
                        .string => |s| s,
                        else => return self.fail(error.InvalidSchema, p.value.offset, "$dynamicAnchor must be a string", .{}),
                    };
                    // A dynamic anchor doubles as a plain anchor for a
                    // static $ref ("behaves like a normal $ref to an
                    // $anchor"); a same-name $anchor in the same resource
                    // keeps the static role.
                    try self.dyn_anchors.append(self.a, .{ .res = rr, .name = name, .target = v });
                    var static_taken = false;
                    for (self.anchors.items) |an| {
                        if (an.res == rr and std.mem.eql(u8, an.name, name)) {
                            static_taken = true;
                            break;
                        }
                    }
                    if (!static_taken) try self.addAnchor(rr, name, v, p.value.offset);
                }
            }
        }
        for (pairs) |p| {
            if (isApplicatorObjectValues(p.key)) {
                switch (p.value.v) {
                    .object => |op| for (op) |sp| {
                        try self.collectAnchors(sp.value, rr, vb);
                    },
                    else => {},
                }
            } else if (isApplicatorArray(p.key)) {
                switch (p.value.v) {
                    .array => |items| for (items) |it| {
                        try self.collectAnchors(it, rr, vb);
                    },
                    else => {},
                }
            } else if (isApplicatorSingle(p.key)) {
                try self.collectAnchors(p.value, rr, vb);
            } else if (std.mem.eql(u8, p.key, "items")) {
                // items: single schema (2019-09+ semantics) or tuple array.
                switch (p.value.v) {
                    .array => |items| for (items) |it| {
                        try self.collectAnchors(it, rr, vb);
                    },
                    else => try self.collectAnchors(p.value, rr, vb),
                }
            }
        }
    }

    fn addAnchor(self: *Compiler, res: *const json.Value, name: []const u8, target: *const json.Value, offset: u32) CompileError!void {
        // draft-04 id / draft-06/07 $id names are document-global (they
        // set the base URI fragment), so duplicates collide globally;
        // 2019-09+ $anchor is scoped to its resource.
        const global = self.dialect == .draft04 or self.dialect == .draft06 or self.dialect == .draft07;
        for (self.anchors.items) |an| {
            if ((global or an.res == res) and std.mem.eql(u8, an.name, name)) {
                return self.fail(error.InvalidSchema, offset, "duplicate anchor '{s}'", .{name});
            }
        }
        try self.anchors.append(self.a, .{ .res = res, .name = name, .target = target });
    }

    /// Plain-name lookup: innermost resource scope first, the document
    /// root last (it is the bottom of the resource stack). draft-04/06/07
    /// names are document-global: after the scopes, every anchor matches.
    fn lookupAnchor(self: *Compiler, name: []const u8) ?*const json.Value {
        const an = self.lookupAnchorEntry(name) orelse return null;
        return an.target;
    }

    /// Anchor-entry form of lookupAnchor (P6b): the carrying resource is
    /// part of the result because $dynamicRef bookending keys on it.
    fn lookupAnchorEntry(self: *Compiler, name: []const u8) ?Anchor {
        var i = self.resource_stack.items.len;
        while (i > 0) {
            i -= 1;
            const rr = self.resource_stack.items[i];
            for (self.anchors.items) |an| {
                if (an.res == rr and std.mem.eql(u8, an.name, name)) return an;
            }
        }
        if (self.dialect == .draft04 or self.dialect == .draft06 or self.dialect == .draft07) {
            for (self.anchors.items) |an| {
                if (std.mem.eql(u8, an.name, name)) return an;
            }
        }
        return null;
    }

    /// The $dynamicAnchor entry of `name` in resource `res`, if any (P6b).
    fn dynAnchorEntry(self: *Compiler, res: *const json.Value, name: []const u8) ?Anchor {
        for (self.dyn_anchors.items) |an| {
            if (an.res == res and std.mem.eql(u8, an.name, name)) return an;
        }
        return null;
    }

    /// 2019-09 $recursiveAnchor: true on a resource root (P6b).
    fn hasRecursiveAnchor(v: *const json.Value) bool {
        const pairs = switch (v.v) {
            .object => |p| p,
            else => return false,
        };
        for (pairs) |p| {
            if (std.mem.eql(u8, p.key, "$recursiveAnchor")) {
                return p.value.v == .boolean and p.value.v.boolean;
            }
        }
        return false;
    }

    /// RFC 3986 section 5.2/5.3 reference resolution (P6b), restricted to
    /// what schema identifiers need: `ref` carries no fragment (the caller
    /// splits it off); the result is the absolute URI of the reference
    /// against `base`. A scheme-less reference merges into the base path;
    /// dot segments are removed per section 5.2.4.
    fn resolveUriRef(self: *Compiler, base: []const u8, ref: []const u8) CompileError![]const u8 {
        if (ref.len == 0) return base;
        if (hasUriScheme(ref)) return ref;
        const scheme_end = std.mem.indexOfScalar(u8, base, ':') orelse {
            // No scheme in the base: plain relative resolution - join paths
            // ("" base keeps the reference relative).
            if (ref[0] == '/') return self.removeDotSegments(ref);
            const merged = try self.mergeUriPaths(base, ref);
            return self.removeDotSegments(merged);
        };
        const scheme = base[0..scheme_end];
        if (std.mem.startsWith(u8, ref, "//")) {
            return std.fmt.allocPrint(self.a, "{s}:{s}", .{ scheme, ref });
        }
        // authority = base after "scheme://" up to the next '/'.
        var auth: []const u8 = "";
        var base_path: []const u8 = "";
        const after_scheme = base[scheme_end + 1 ..];
        if (std.mem.startsWith(u8, after_scheme, "//")) {
            const rest = after_scheme[2..];
            const slash = std.mem.indexOfScalar(u8, rest, '/') orelse rest.len;
            auth = after_scheme[0 .. 2 + slash];
            base_path = rest[slash..];
        } else {
            base_path = after_scheme;
        }
        if (ref[0] == '/') {
            return std.fmt.allocPrint(self.a, "{s}:{s}{s}", .{ scheme, auth, try self.removeDotSegments(ref) });
        }
        const merged = try self.mergeUriPaths(base_path, ref);
        return std.fmt.allocPrint(self.a, "{s}:{s}{s}", .{ scheme, auth, try self.removeDotSegments(merged) });
    }

    /// RFC 3986 section 5.2.3 merge: the base path up to and including its
    /// last '/' plus the reference path; an authority-less base without '/'
    /// yields the reference itself.
    fn mergeUriPaths(self: *Compiler, base_path: []const u8, ref: []const u8) CompileError![]const u8 {
        if (std.mem.lastIndexOfScalar(u8, base_path, '/')) |slash| {
            return std.fmt.allocPrint(self.a, "{s}{s}", .{ base_path[0 .. slash + 1], ref });
        }
        return ref;
    }

    /// RFC 3986 section 5.2.4 dot-segment removal.
    fn removeDotSegments(self: *Compiler, path: []const u8) CompileError![]const u8 {
        if (std.mem.indexOfScalar(u8, path, '.') == null) return path;
        var out: std.ArrayListUnmanaged(u8) = .{};
        var in = path;
        while (in.len > 0) {
            if (std.mem.startsWith(u8, in, "../")) {
                in = in[3..];
            } else if (std.mem.startsWith(u8, in, "./")) {
                in = in[2..];
            } else if (std.mem.startsWith(u8, in, "/./")) {
                in = in[2..];
            } else if (std.mem.eql(u8, in, "/.")) {
                in = "/";
            } else if (std.mem.startsWith(u8, in, "/../")) {
                in = in[3..];
                if (out.items.len > 0) {
                    const slash = std.mem.lastIndexOfScalar(u8, out.items, '/') orelse 0;
                    out.shrinkRetainingCapacity(slash);
                }
            } else if (std.mem.eql(u8, in, "/..")) {
                in = "/";
                if (out.items.len > 0) {
                    const slash = std.mem.lastIndexOfScalar(u8, out.items, '/') orelse 0;
                    out.shrinkRetainingCapacity(slash);
                }
            } else if (std.mem.eql(u8, in, ".") or std.mem.eql(u8, in, "..")) {
                in = "";
            } else {
                const start: usize = if (in[0] == '/') 1 else 0;
                const next = std.mem.indexOfScalarPos(u8, in, start, '/') orelse in.len;
                try out.appendSlice(self.a, in[0..next]);
                in = in[next..];
            }
        }
        return out.toOwnedSlice(self.a);
    }

    /// Resolve a multi-segment JSON pointer (the part after '#') against
    /// the current resource root: object keys, array indices without
    /// leading zeros, '~0'/'~1' escapes per segment.
    fn resolvePointer(self: *Compiler, pointer: []const u8, ref_text: []const u8, offset: u32) CompileError!*const json.Value {
        var cur: *const json.Value = self.resource_stack.items[self.resource_stack.items.len - 1];
        var rest = pointer;
        while (rest.len > 0) {
            std.debug.assert(rest[0] == '/');
            const next = std.mem.indexOfScalarPos(u8, rest, 1, '/') orelse rest.len;
            const seg = try self.unescapeSegment(try self.percentDecode(rest[1..next], offset), offset);
            switch (cur.v) {
                .object => |pairs| {
                    var found: ?*const json.Value = null;
                    for (pairs) |p| {
                        if (std.mem.eql(u8, p.key, seg)) found = p.value;
                    }
                    cur = found orelse
                        return self.fail(error.InvalidSchema, offset, "unresolved $ref '{s}'", .{ref_text});
                },
                .array => |items| {
                    if (seg.len == 0 or (seg.len > 1 and seg[0] == '0')) {
                        return self.fail(error.InvalidSchema, offset, "unresolved $ref '{s}'", .{ref_text});
                    }
                    for (seg) |b| {
                        if (b < '0' or b > '9') {
                            return self.fail(error.InvalidSchema, offset, "unresolved $ref '{s}'", .{ref_text});
                        }
                    }
                    const idx = std.fmt.parseInt(u32, seg, 10) catch
                        return self.fail(error.InvalidSchema, offset, "unresolved $ref '{s}'", .{ref_text});
                    if (idx >= items.len) {
                        return self.fail(error.InvalidSchema, offset, "unresolved $ref '{s}'", .{ref_text});
                    }
                    cur = items[idx];
                },
                else => return self.fail(error.InvalidSchema, offset, "unresolved $ref '{s}'", .{ref_text}),
            }
            try self.path.append(self.a, seg);
            rest = rest[next..];
        }
        return cur;
    }

    /// $vocabulary check (section 2, rule 4): 2019-09/2020-12 only; every
    /// entry marked required must name an implemented vocabulary, entries
    /// marked false and unknown entries are ignored. In earlier drafts the
    /// keyword is unknown and ignored.
    fn checkVocabularySpec(self: *Compiler, vv: *const json.Value) CompileError!void {
        if (self.dialect != .d2019_09 and self.dialect != .d2020_12) return;
        const pairs = switch (vv.v) {
            .object => |p| p,
            else => return self.fail(error.InvalidSchema, vv.offset, "$vocabulary must be an object", .{}),
        };
        for (pairs) |p| {
            const required = switch (p.value.v) {
                .boolean => |b| b,
                else => return self.fail(error.InvalidSchema, p.value.offset, "$vocabulary entries must be booleans", .{}),
            };
            if (!required) continue;
            if (!vocabularySupported(p.key, self.dialect)) {
                return self.fail(error.UnsupportedFeature, p.key_offset, "required vocabulary '{s}' is not supported in spec-v1", .{p.key});
            }
        }
    }

    /// RFC 3986 percent-decoding of a URI-fragment part (spec-v1 P5): a
    /// `$ref` fragment is a URI fragment, so '%XX' decodes before the
    /// JSON-pointer '~0'/'~1' unescaping (RFC 6901 section 6). Malformed
    /// '%' sequences are INVALID_SCHEMA.
    fn percentDecode(self: *Compiler, s: []const u8, offset: u32) CompileError![]const u8 {
        if (std.mem.indexOfScalar(u8, s, '%') == null) return s;
        const hex = struct {
            fn val(b: u8) ?u8 {
                return switch (b) {
                    '0'...'9' => b - '0',
                    'a'...'f' => b - 'a' + 10,
                    'A'...'F' => b - 'A' + 10,
                    else => null,
                };
            }
        };
        var out: std.ArrayListUnmanaged(u8) = .{};
        var i: usize = 0;
        while (i < s.len) : (i += 1) {
            if (s[i] == '%') {
                if (i + 2 >= s.len) {
                    return self.fail(error.InvalidSchema, offset, "invalid '%' escape in $ref fragment", .{});
                }
                const hi = hex.val(s[i + 1]) orelse
                    return self.fail(error.InvalidSchema, offset, "invalid '%' escape in $ref fragment", .{});
                const lo = hex.val(s[i + 2]) orelse
                    return self.fail(error.InvalidSchema, offset, "invalid '%' escape in $ref fragment", .{});
                try out.append(self.a, hi * 16 + lo);
                i += 2;
            } else {
                try out.append(self.a, s[i]);
            }
        }
        return out.toOwnedSlice(self.a);
    }

    fn unescapeSegment(self: *Compiler, seg: []const u8, offset: u32) CompileError![]const u8 {
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

    fn compileSchema(self: *Compiler, v: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        if (depth > self.max_depth) {
            // ADR-0008: inside a recursive unroll the depth budget
            // truncates the position to the empty language (the same
            // bottom as the unroll budget) instead of refusing the whole
            // schema; outside recursion the refusal is unchanged.
            if (self.profile == .spec_v1 and self.recursion_left < REF_UNROLL_CAP) {
                return self.emptyLangNode();
            }
            return self.fail(error.ResourceLimit, v.offset, "schema depth exceeds max_depth {d}", .{self.max_depth});
        }
        const pairs = switch (v.v) {
            .object => |p| p,
            .boolean => |b| {
                if (self.profile != .spec_v1)
                    return self.fail(error.UnsupportedFeature, v.offset, "boolean schemas are not supported", .{});
                if (b) return self.compileAnyJSON(depth);
                // 'false' defines an empty language: recognized at the root
                // (ADR-0006 D1), an empty-language node in subschema position.
                if (depth == 1)
                    return self.fail(error.UnsatisfiableConstraint, v.offset, "schema 'false' defines an empty language", .{});
                return self.addNode(.{ .str = .{ .min_len = 1, .max_len = 0 } });
            },
            else => return self.fail(error.InvalidSchema, v.offset, "schema must be an object", .{}),
        };
        // spec-v1: a subschema carrying the dialect's id keyword opens a
        // resource scope for '#' and plain-name refs below it
        // (dialect-matrix section 3).
        var pushed_resource = false;
        if (self.profile == .spec_v1) {
            const idk = self.idKeyword();
            for (pairs) |p| {
                if (std.mem.eql(u8, p.key, idk) and p.value.v == .string) {
                    try self.resource_stack.append(self.a, v);
                    // P6b: the resource's absolute base URI runs in
                    // parallel (registered by the pre-scan).
                    try self.base_stack.append(self.a, self.res_base.get(v) orelse "");
                    pushed_resource = true;
                    break;
                }
            }
        }
        defer if (pushed_resource) {
            _ = self.resource_stack.pop();
            _ = self.base_stack.pop();
        };
        // spec-v1 P6b: the dynamic scope for $dynamicRef/$recursiveAnchor
        // resolution - the chain of resources entered to reach here
        // (consecutive duplicates collapse: lexical descent inside one
        // resource does not re-enter its scope).
        var pushed_dyn = false;
        if (self.profile == .spec_v1 and self.resource_stack.items.len > 0) {
            const res = self.resource_stack.items[self.resource_stack.items.len - 1];
            if (self.dyn_stack.items.len == 0 or self.dyn_stack.items[self.dyn_stack.items.len - 1] != res) {
                try self.dyn_stack.append(self.a, res);
                pushed_dyn = true;
            }
        }
        defer if (pushed_dyn) {
            _ = self.dyn_stack.pop();
        };
        const kw = try self.scanKeywords(pairs);
        if (self.profile != .spec_v1) {
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
        }
        if (kw.defs) |dv| {
            switch (dv.v) {
                .object => {},
                else => return self.fail(error.InvalidSchema, dv.offset, "$defs must be an object", .{}),
            }
            if (depth == 1) self.defs = dv;
        }
        if (self.profile == .spec_v1 and (kw.uneval_props != null or kw.uneval_items != null))
            return self.compileUnevaluated(v, kw, depth);
        if (kw.dyn_ref) |dv| return self.compileDynamicRef(v, kw, dv, depth);
        if (kw.rec_ref) |rv2| return self.compileRecursiveRef(v, kw, rv2, depth);
        if (kw.ref) |rv| return self.compileRef(v, kw, rv, depth);
        return self.compileTyped(v, kw, depth);
    }

    /// Node with an empty language (ADR-0006 D1): a string bound that no
    /// document can satisfy. Used for `false` subschemas and contradictory
    /// per-arm bounds in spec-v1 unions.
    fn emptyLangNode(self: *Compiler) CompileError!grammar.NodeId {
        return self.addNode(.{ .str = .{ .min_len = 1, .max_len = 0 } });
    }

    /// The keyword scan of a schema object (dialect-matrix section 2):
    /// annotations and keywords unknown to the active dialect are ignored,
    /// known-but-unimplemented keywords of the active dialect refuse with a
    /// pointer. Shared by compileSchema and the P6a unevaluated* scenario
    /// walk (which re-scans applicator branches).
    fn scanKeywords(self: *Compiler, pairs: []const json.Pair) CompileError!Keywords {
        var kw: Keywords = .{};
        for (pairs) |p| {
            if (isAnnotation(p.key)) continue;
            if (self.profile == .spec_v1) {
                // Matched keywords that do not belong to the active dialect
                // are unknown here: ignored (section 2, rule 2).
                if (std.mem.eql(u8, p.key, "const") and self.dialect == .draft04) continue;
                if (std.mem.eql(u8, p.key, "$defs") and self.dialect != .d2019_09 and self.dialect != .d2020_12) continue;
                // P2 keywords with dialect-scoped applicability (sections
                // 4-5): outside their dialects they are unknown, ignored.
                const d = self.dialect;
                const new_drafts = d == .d2019_09 or d == .d2020_12;
                if (std.mem.eql(u8, p.key, "propertyNames") or std.mem.eql(u8, p.key, "contains")) {
                    if (d == .draft04) continue;
                } else if (std.mem.eql(u8, p.key, "minContains") or std.mem.eql(u8, p.key, "maxContains") or
                    std.mem.eql(u8, p.key, "dependentRequired") or std.mem.eql(u8, p.key, "dependentSchemas"))
                {
                    if (!new_drafts) continue;
                } else if (std.mem.eql(u8, p.key, "prefixItems")) {
                    if (d != .d2020_12) continue;
                } else if (std.mem.eql(u8, p.key, "additionalItems")) {
                    if (d == .d2020_12) continue;
                } else if (std.mem.eql(u8, p.key, "dependencies")) {
                    if (new_drafts) continue;
                } else if (std.mem.eql(u8, p.key, "if") or std.mem.eql(u8, p.key, "then") or std.mem.eql(u8, p.key, "else")) {
                    // if/then/else are draft-07+ (dialect-matrix section
                    // 4): unknown in draft-04/06, ignored there.
                    if (d == .draft04 or d == .draft06) continue;
                } else if (std.mem.eql(u8, p.key, "unevaluatedProperties") or std.mem.eql(u8, p.key, "unevaluatedItems")) {
                    // P6a: unevaluated* are 2019-09/2020-12 (dialect-matrix
                    // section 4): unknown in earlier drafts, ignored there.
                    if (!new_drafts) continue;
                } else if (std.mem.eql(u8, p.key, "$dynamicRef") or std.mem.eql(u8, p.key, "$dynamicAnchor")) {
                    // P6b: $dynamic* are 2020-12 only (dialect-matrix
                    // section 4): unknown elsewhere, ignored there.
                    if (d != .d2020_12) continue;
                } else if (std.mem.eql(u8, p.key, "$recursiveRef") or std.mem.eql(u8, p.key, "$recursiveAnchor")) {
                    // P6b: $recursive* are 2019-09 only.
                    if (d != .d2019_09) continue;
                }
            }
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
            if (!matched and self.profile == .spec_v1) {
                // spec-v1 P2 keywords (dialect applicability was filtered
                // above); canonical-v1 keeps refusing them below.
                const map2 = .{
                    .{ "patternProperties", &kw.pattern_props },
                    .{ "propertyNames", &kw.prop_names },
                    .{ "minProperties", &kw.min_props },
                    .{ "maxProperties", &kw.max_props },
                    .{ "dependencies", &kw.dependencies },
                    .{ "dependentRequired", &kw.dep_required },
                    .{ "dependentSchemas", &kw.dep_schemas },
                    .{ "prefixItems", &kw.prefix_items },
                    .{ "additionalItems", &kw.additional_items },
                    .{ "contains", &kw.contains },
                    .{ "minContains", &kw.min_contains },
                    .{ "maxContains", &kw.max_contains },
                    .{ "uniqueItems", &kw.unique_items },
                    // spec-v1 P3 keywords (if/then/else were dialect-
                    // filtered above); canonical-v1 keeps refusing them.
                    .{ "anyOf", &kw.any_of },
                    .{ "oneOf", &kw.one_of },
                    .{ "allOf", &kw.all_of },
                    .{ "not", &kw.not_ },
                    .{ "if", &kw.if_ },
                    .{ "then", &kw.then_ },
                    .{ "else", &kw.else_ },
                    // spec-v1 P4 keyword; canonical-v1 keeps refusing it.
                    .{ "pattern", &kw.pattern_ },
                    // spec-v1 P4 numeric keywords (exclusive* are handled
                    // below: their dialect fixes the accepted form).
                    .{ "minimum", &kw.minimum },
                    .{ "maximum", &kw.maximum },
                    .{ "multipleOf", &kw.multiple_of },
                    // spec-v1 P6a keywords (dialect-filtered above);
                    // canonical-v1 keeps refusing them.
                    .{ "unevaluatedProperties", &kw.uneval_props },
                    .{ "unevaluatedItems", &kw.uneval_items },
                    // spec-v1 P6b keywords (dialect-filtered above);
                    // canonical-v1 keeps refusing them.
                    .{ "$dynamicRef", &kw.dyn_ref },
                    .{ "$recursiveRef", &kw.rec_ref },
                };
                inline for (map2) |entry| {
                    if (std.mem.eql(u8, p.key, entry[0])) {
                        entry[1].* = p.value;
                        matched = true;
                    }
                }
                if (!matched and
                    (std.mem.eql(u8, p.key, "exclusiveMinimum") or std.mem.eql(u8, p.key, "exclusiveMaximum")))
                {
                    // Section 2, rule 1: the dialect fixes the form
                    // (draft-04 boolean modifier, draft-06+ standalone
                    // number); a wrong form is INVALID_SCHEMA.
                    const want_bool = self.dialect == .draft04;
                    if ((p.value.v == .boolean) != want_bool) {
                        return self.fail(error.InvalidSchema, p.key_offset, "keyword '{s}' must be a {s} in this dialect", .{ p.key, if (want_bool) @as([]const u8, "boolean") else "number" });
                    }
                    const is_min = std.mem.eql(u8, p.key, "exclusiveMinimum");
                    if (want_bool) {
                        if (is_min) kw.excl_min_bool = p.value.v.boolean else kw.excl_max_bool = p.value.v.boolean;
                    } else {
                        if (is_min) kw.excl_min = p.value else kw.excl_max = p.value;
                    }
                    matched = true;
                }
            }
            if (!matched) {
                if (self.profile == .spec_v1) {
                    // spec-v1: annotation keywords and unknown keywords are
                    // ignored (dialect matrix); only keywords known to be
                    // unimplemented IN THE ACTIVE DIALECT refuse (section 2,
                    // rule 2), with a pointer.
                    if (isSpecV1Annotation(p.key)) continue;
                    // Addressing keywords are handled by the dialect-aware
                    // resolver (anchor pre-scan and resource scopes);
                    // 'definitions' is an annotation container; the content
                    // keywords never assert (2020-12 Validation section 8:
                    // contentEncoding/contentMediaType/contentSchema are
                    // annotations).
                    if (std.mem.eql(u8, p.key, "$id") or std.mem.eql(u8, p.key, "$anchor") or
                        std.mem.eql(u8, p.key, "$dynamicAnchor") or std.mem.eql(u8, p.key, "$recursiveAnchor") or
                        std.mem.eql(u8, p.key, "id") or std.mem.eql(u8, p.key, "definitions") or
                        std.mem.eql(u8, p.key, "contentEncoding") or std.mem.eql(u8, p.key, "contentMediaType") or
                        std.mem.eql(u8, p.key, "contentSchema")) continue;
                    if (std.mem.eql(u8, p.key, "$vocabulary")) {
                        try self.checkVocabularySpec(p.value);
                        continue;
                    }
                    if (isKnownUnsupported(p.key)) {
                        if (keywordInDialect(p.key, self.dialect)) {
                            return self.fail(error.UnsupportedFeature, p.key_offset, "keyword '{s}' is not supported in spec-v1", .{p.key});
                        }
                        continue; // a keyword of another dialect: ignored
                    }
                    continue;
                }
                if (isKnownUnsupported(p.key)) {
                    return self.fail(error.UnsupportedFeature, p.key_offset, "keyword '{s}' is outside the MVP profile", .{p.key});
                }
                return self.fail(error.UnsupportedFeature, p.key_offset, "unknown keyword '{s}'", .{p.key});
            }
        }
        return kw;
    }

    /// AnyJSON (ADR-0006 D1): every JSON value. Lowered by bounded unrolling
    /// a_0..a_D, a_i = choice(scalars, array(a_{i+1}), object(a_{i+1})),
    /// a_D = scalars, D = min(max_depth, MAX_DEPTH_CAP - 1) - k with k the
    /// structural nesting depth of the position. Deviation (documented): the
    /// accepted language is values whose structural depth is at most
    /// min(max_depth, MAX_DEPTH_CAP - 1); the -1 keeps the deepest value's
    /// scalar frame inside the thread frame budget.
    fn compileAnyJSON(self: *Compiler, depth: u32) CompileError!grammar.NodeId {
        const s_str = try self.addNode(.{ .str = .{ .min_len = 0, .max_len = grammar.UNBOUNDED } });
        const s_num = try self.addNode(.{ .num_v = {} });
        const s_true = try self.addNode(.{ .literal = try self.builder.addLiteral("true") });
        const s_false = try self.addNode(.{ .literal = try self.builder.addLiteral("false") });
        const s_null = try self.addNode(.{ .literal = try self.builder.addLiteral("null") });
        // The keyword literals share one trie thread: AnyJSON threads are
        // spawned at every unconstrained value position, and unevaluated*
        // scenario guards multiply them (ADR-0009).
        const s_kw = try self.addNode(try self.builder.alternativesNode(&.{ s_true, s_false, s_null }));
        const scalars = try self.addNode(.{ .choice = try self.builder.copyNodeIds(&.{ s_str, s_num, s_kw }) });
        var level = scalars;
        const d = @min(self.max_depth, parser.MAX_DEPTH_CAP - 1) -| (depth - 1);
        var i: u32 = 0;
        while (i < d) : (i += 1) {
            const arr = try self.addNode(.{ .repeat = .{ .item = level, .min = 0, .max = grammar.UNBOUNDED } });
            const obj = try self.addNode(.{ .open_obj = .{ .props = &.{}, .extra_required = &.{}, .value = level } });
            level = try self.addNode(.{ .choice = try self.builder.copyNodeIds(&.{ scalars, arr, obj }) });
        }
        try self.any_nodes.put(self.a, level, {});
        return level;
    }

    fn compileRef(self: *Compiler, v: *const json.Value, kw: Keywords, rv: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        if (self.profile == .spec_v1) return self.compileRefSpec(v, kw, rv, depth);
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

    /// Assertion/applicator keywords sitting next to a reference keyword
    /// (P6b): the reference-sibling rule keys on this set.
    fn hasRefSiblings(kw: Keywords) bool {
        return kw.ty != null or kw.properties != null or kw.required != null or kw.additional != null or
            kw.items != null or kw.min_items != null or kw.max_items != null or kw.min_length != null or
            kw.max_length != null or kw.enum_ != null or kw.const_ != null or
            kw.pattern_props != null or kw.prop_names != null or kw.min_props != null or
            kw.max_props != null or kw.dependencies != null or kw.dep_required != null or
            kw.dep_schemas != null or kw.prefix_items != null or kw.additional_items != null or
            kw.contains != null or kw.min_contains != null or kw.max_contains != null or
            kw.unique_items != null or kw.any_of != null or kw.one_of != null or
            kw.all_of != null or kw.not_ != null or kw.if_ != null or kw.then_ != null or
            kw.else_ != null or kw.pattern_ != null or kw.minimum != null or
            kw.maximum != null or kw.multiple_of != null or kw.excl_min != null or
            kw.excl_max != null or (kw.excl_min_bool orelse false) or (kw.excl_max_bool orelse false);
    }

    /// Conjunction of a reference expansion with its sibling assertions
    /// (2019-09/2020-12 sibling rule, P6b): the two parts compile
    /// independently and conjoin as an allOf comb node - the referenced
    /// subschema does not see the siblings' annotations (the suite's "ref
    /// creates new scope when adjacent to keywords" pins exactly this).
    fn compileRefWithSiblings(self: *Compiler, v: *const json.Value, kw: Keywords, ref_node: grammar.NodeId, depth: u32) CompileError!grammar.NodeId {
        var kwc = kw;
        kwc.ref = null;
        kwc.dyn_ref = null;
        kwc.rec_ref = null;
        const sib_node = try self.compileTypedSpec(v, kwc, depth);
        if (self.any_nodes.contains(sib_node)) return ref_node; // inert siblings
        for ([_]grammar.NodeId{ ref_node, sib_node }) |id| {
            if (self.isEmptyNode(id)) {
                return self.emptySubschema(depth, v.offset, "the conjunction defines an empty language", .{});
            }
        }
        return self.conjoinAllNodes(&[_]grammar.NodeId{ ref_node, sib_node }, v.offset, depth);
    }

    /// spec-v1 extended local resolver (dialect-matrix section 3): '#'
    /// and '#/...' pointers against the current resource root, plain-name
    /// fragments via the dialect's anchor keyword, cycles and external
    /// refs refused with a pointer.
    fn compileRefSpec(self: *Compiler, v: *const json.Value, kw: Keywords, rv: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        // Sibling rule (A7): draft-04/06/07 ignore assertion keywords next
        // to $ref; in 2019-09/2020-12 they apply alongside (P6b: conjoin).
        const new_drafts = self.dialect == .d2019_09 or self.dialect == .d2020_12;
        const ref_node = try self.compileRefOnly(rv, depth);
        if (new_drafts and hasRefSiblings(kw)) {
            return self.compileRefWithSiblings(v, kw, ref_node, depth);
        }
        return ref_node;
    }

    /// spec-v1 $ref resolution and expansion with no sibling handling
    /// (P6a: the unevaluated* path treats $ref as one in-place applicator
    /// among several and conjoins the expansion itself).
    fn compileRefOnly(self: *Compiler, rv: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        const s = switch (rv.v) {
            .string => |s| s,
            else => return self.fail(error.InvalidSchema, rv.offset, "$ref must be a string", .{}),
        };
        if (s.len == 0 or s[0] != '#') {
            return self.compileUriRef(rv, depth, s);
        }
        const path_len = self.path.items.len;
        defer self.path.shrinkRetainingCapacity(path_len);
        var target: *const json.Value = undefined;
        if (std.mem.eql(u8, s, "#")) {
            target = self.resource_stack.items[self.resource_stack.items.len - 1];
        } else if (s.len >= 2 and s[1] == '/') {
            target = try self.resolvePointer(s[1..], s, rv.offset);
        } else {
            const aname = try self.percentDecode(s[1..], rv.offset);
            target = self.lookupAnchor(aname) orelse
                return self.fail(error.InvalidSchema, rv.offset, "unresolved $ref '{s}'", .{s});
        }
        return self.expandRefTarget(target, rv, depth, s);
    }

    /// Shared cycle handling and expansion of a resolved $ref target.
    /// ADR-0008: a target already on the expansion stack (a cycle) is
    /// expanded once more while the per-path unroll budget lasts; at the
    /// bottom the position is the empty language - exactly the documents
    /// whose recursion nesting exceeds the limit are rejected, and the
    /// limit stays mask-visible (an optional recursive property becomes a
    /// banned key, a required one empties its arm; ADR-0005).
    fn expandRefTarget(self: *Compiler, target: *const json.Value, rv: *const json.Value, depth: u32, s: []const u8) CompileError!grammar.NodeId {
        _ = s;
        var cycle = false;
        for (self.ref_ptrs.items) |p| {
            if (p == target) {
                cycle = true;
                break;
            }
        }
        if (cycle) {
            if (self.recursion_left == 0) return self.emptyLangNode();
            self.recursion_left -= 1;
        }
        defer if (cycle) {
            self.recursion_left += 1;
        };
        try self.ref_ptrs.append(self.a, target);
        defer _ = self.ref_ptrs.pop();
        // A 'false' target of a root-level $ref is an empty-language root
        // (ADR-0006 D1): refuse instead of compiling an empty node.
        if (depth == 1) {
            switch (target.v) {
                .boolean => |b| {
                    if (!b) return self.fail(error.UnsatisfiableConstraint, rv.offset, "schema 'false' defines an empty language", .{});
                },
                else => {},
            }
        }
        return self.compileSchema(target, depth + 1);
    }

    /// spec-v1 P6b: a reference whose text does not start with '#' - split
    /// off the fragment, resolve the URI part against the current base URI
    /// (RFC 3986 section 5.2), then, in order: an in-document (or already
    /// collected registry) resource whose absolute base URI matches, else
    /// the registry snapshot, else a documented refusal. This subsumes the
    /// old exact-match external $ref: absolute references resolve to
    /// themselves.
    fn compileUriRef(self: *Compiler, rv: *const json.Value, depth: u32, s: []const u8) CompileError!grammar.NodeId {
        var base = s;
        var frag: []const u8 = "";
        if (std.mem.indexOfScalar(u8, s, '#')) |hi| {
            base = s[0..hi];
            frag = s[hi + 1 ..];
        }
        const cur_base = if (self.base_stack.items.len > 0)
            self.base_stack.items[self.base_stack.items.len - 1]
        else
            "";
        const abs = try self.resolveUriRef(cur_base, base);
        if (self.lookupResource(abs)) |res| {
            return self.compileResourceRef(res, rv, depth, s, frag);
        }
        return self.compileExternalRef(rv, rv, depth, s, abs, frag);
    }

    /// The in-document (or lazily collected registry) resource whose
    /// absolute base URI is `abs`, if any (P6b).
    fn lookupResource(self: *Compiler, abs: []const u8) ?*const json.Value {
        for (self.resources.items) |r| {
            if (std.mem.eql(u8, r.uri, abs)) return r.value;
        }
        return null;
    }

    /// A $ref jump into a resource of the same document (P6b): the resource
    /// opens its own scope (its anchors and its base URI), the fragment
    /// resolves inside it with the local rules.
    fn compileResourceRef(self: *Compiler, res: *const json.Value, rv: *const json.Value, depth: u32, s: []const u8, frag: []const u8) CompileError!grammar.NodeId {
        // Diagnostics raised below the jump carry pointers into the target
        // resource; re-anchor at the reference itself (same convention as
        // the registry crossing).
        const anchor_path_len = self.path.items.len;
        return self.compileResourceRefInner(res, rv, depth, s, frag) catch |e| {
            self.path.shrinkRetainingCapacity(anchor_path_len);
            self.renderPointer();
            self.diag.offset = rv.offset;
            return e;
        };
    }

    fn compileResourceRefInner(self: *Compiler, res: *const json.Value, rv: *const json.Value, depth: u32, s: []const u8, frag: []const u8) CompileError!grammar.NodeId {
        try self.resource_stack.append(self.a, res);
        defer _ = self.resource_stack.pop();
        try self.base_stack.append(self.a, self.res_base.get(res) orelse "");
        defer _ = self.base_stack.pop();
        const path_len = self.path.items.len;
        defer self.path.shrinkRetainingCapacity(path_len);
        var target: *const json.Value = res;
        if (frag.len > 0) {
            if (frag[0] == '/') {
                target = try self.resolvePointer(frag, s, rv.offset);
            } else {
                const aname = try self.percentDecode(frag, rv.offset);
                // Scoped to the jumped-to resource in 2019-09+; pre-2019
                // plain names are document-global (dialect-matrix s.3).
                target = self.anchorInResource(res, aname) orelse
                    if (self.dialect == .d2019_09 or self.dialect == .d2020_12)
                        return self.fail(error.InvalidSchema, rv.offset, "unresolved $ref '{s}'", .{s})
                    else
                        self.lookupAnchor(aname) orelse
                            return self.fail(error.InvalidSchema, rv.offset, "unresolved $ref '{s}'", .{s});
            }
        }
        return self.expandRefTarget(target, rv, depth, s);
    }

    /// The plain-name anchor of `name` scoped to exactly resource `res`
    /// (P6b): a 'uri#name' reference may not see anchors of other resources.
    fn anchorInResource(self: *Compiler, res: *const json.Value, name: []const u8) ?*const json.Value {
        for (self.anchors.items) |an| {
            if (an.res == res and std.mem.eql(u8, an.name, name)) return an.target;
        }
        return null;
    }

    /// spec-v1 P5 (ADR-0006 D5, ADR-0008): external $ref through the
    /// immutable registry snapshot. The resolved absolute URI must match a
    /// registry document URI exactly (no network); the fragment resolves
    /// inside that document with the local rules (pointer, anchor,
    /// percent-decoding), the recursion budget is shared with local cycles.
    fn compileExternalRef(self: *Compiler, v: *const json.Value, rv: *const json.Value, depth: u32, s: []const u8, abs: []const u8, frag: []const u8) CompileError!grammar.NodeId {
        _ = v;
        if (self.registry.len == 0) {
            return self.fail(error.UnsupportedFeature, rv.offset, "only local $ref is supported: '{s}'", .{s});
        }
        var doc: ?*RegDoc = null;
        for (self.registry) |*d| {
            if (std.mem.eql(u8, d.uri, abs)) {
                doc = d;
                break;
            }
        }
        const d = doc orelse
            return self.fail(error.UnsupportedFeature, rv.offset, "external $ref '{s}' is not in the registry snapshot", .{abs});
        // Failures raised inside a registry document carry offsets/pointers
        // that do not address the main schema: re-anchor the diagnostic at
        // the external $ref of the main document (the outermost crossing
        // wins when registry documents reference each other).
        const anchor_path_len = self.path.items.len;
        return self.compileExternalRefInner(d, rv, depth, s, frag) catch |e| {
            self.path.shrinkRetainingCapacity(anchor_path_len);
            self.renderPointer();
            self.diag.offset = rv.offset;
            return e;
        };
    }

    /// Lazy per-document init (P5/P6b): own dialect (own $schema, the
    /// 2020-12 default), then the anchor/resource pre-scan under that
    /// dialect with the document URI as the base.
    fn ensureRegDocInit(self: *Compiler, d: *RegDoc) CompileError!void {
        if (d.dialect == null) {
            const pairs = switch (d.root.v) {
                .object => |p| p,
                else => &.{},
            };
            var det: Dialect = .d2020_12;
            for (pairs) |p| {
                if (!std.mem.eql(u8, p.key, "$schema")) continue;
                const sv = switch (p.value.v) {
                    .string => |x| x,
                    else => return self.fail(error.InvalidSchema, NO_OFFSET, "registry document '{s}': $schema must be a string", .{d.uri}),
                };
                det = matchDialectUri(sv) orelse
                    return self.fail(error.UnsupportedFeature, NO_OFFSET, "registry document '{s}' declares unsupported $schema dialect '{s}'", .{ d.uri, sv });
            }
            d.dialect = det;
        }
        if (!d.anchors_done) {
            const saved_dialect = self.dialect;
            self.dialect = d.dialect.?;
            defer self.dialect = saved_dialect;
            try self.collectAnchors(d.root, d.root, d.uri);
            d.anchors_done = true;
        }
    }

    fn compileExternalRefInner(self: *Compiler, d: *RegDoc, rv: *const json.Value, depth: u32, s: []const u8, frag: []const u8) CompileError!grammar.NodeId {
        try self.ensureRegDocInit(d);
        const saved_dialect = self.dialect;
        self.dialect = d.dialect.?;
        defer self.dialect = saved_dialect;
        // The document opens its own resource scope: '#' and '#/...' below
        // resolve against it, not against the main document.
        const saved_stack = self.resource_stack;
        self.resource_stack = .{};
        defer self.resource_stack = saved_stack;
        const saved_base = self.base_stack;
        self.base_stack = .{};
        defer self.base_stack = saved_base;
        try self.resource_stack.append(self.a, d.root);
        try self.base_stack.append(self.a, self.res_base.get(d.root) orelse d.uri);
        const path_len = self.path.items.len;
        defer self.path.shrinkRetainingCapacity(path_len);
        var target: *const json.Value = d.root;
        if (frag.len > 0) {
            if (frag[0] == '/') {
                target = try self.resolvePointer(frag, s, rv.offset);
            } else {
                const aname = try self.percentDecode(frag, rv.offset);
                target = self.lookupAnchor(aname) orelse
                    return self.fail(error.InvalidSchema, rv.offset, "unresolved $ref '{s}'", .{s});
            }
        }
        return self.expandRefTarget(target, rv, depth, s);
    }

    /// spec-v1 P6b: $dynamicRef (2020-12). An empty or pointer fragment
    /// behaves exactly like $ref. A plain-name fragment first resolves
    /// statically like $ref; when the statically resolved resource carries a
    /// same-name $dynamicAnchor (the bookending requirement), the target is
    /// the first matching $dynamicAnchor of the dynamic scope - the
    /// outermost resource entered on the way here (2020-12 section 8.2.2).
    /// Sibling assertions conjoin (the 2020-12 sibling rule).
    fn compileDynamicRef(self: *Compiler, v: *const json.Value, kw: Keywords, dv: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        const s = switch (dv.v) {
            .string => |s| s,
            else => return self.fail(error.InvalidSchema, dv.offset, "$dynamicRef must be a string", .{}),
        };
        const ref_node = try self.compileDynamicRefOnly(dv, depth, s);
        if (hasRefSiblings(kw)) {
            return self.compileRefWithSiblings(v, kw, ref_node, depth);
        }
        return ref_node;
    }

    fn compileDynamicRefOnly(self: *Compiler, rv: *const json.Value, depth: u32, s: []const u8) CompileError!grammar.NodeId {
        var base = s;
        var frag: []const u8 = "";
        if (std.mem.indexOfScalar(u8, s, '#')) |hi| {
            base = s[0..hi];
            frag = s[hi + 1 ..];
        }
        if (frag.len == 0 or frag[0] == '/') {
            // No plain-name fragment: identical to $ref.
            if (base.len == 0) return self.compileRefOnly(rv, depth);
            return self.compileUriRef(rv, depth, s);
        }
        const name = try self.percentDecode(frag, rv.offset);
        var static: ?Anchor = null;
        if (base.len == 0) {
            static = self.lookupAnchorEntry(name) orelse
                return self.fail(error.InvalidSchema, rv.offset, "unresolved $dynamicRef '{s}'", .{s});
        } else {
            const cur_base = if (self.base_stack.items.len > 0)
                self.base_stack.items[self.base_stack.items.len - 1]
            else
                "";
            const abs = try self.resolveUriRef(cur_base, base);
            var uri_known = false;
            static = try self.lookupScopedAnchor(abs, name, &uri_known) orelse {
                if (!uri_known) {
                    return self.fail(error.UnsupportedFeature, rv.offset, "external $dynamicRef '{s}' is not in the registry snapshot", .{abs});
                }
                return self.fail(error.InvalidSchema, rv.offset, "unresolved $dynamicRef '{s}'", .{s});
            };
        }
        const sa = static.?;
        if (self.dynAnchorEntry(sa.res, name) != null) {
            for (self.dyn_stack.items) |rr| {
                if (self.dynAnchorEntry(rr, name)) |da| {
                    return self.expandRefTarget(da.target, rv, depth, s);
                }
            }
        }
        return self.expandRefTarget(sa.target, rv, depth, s);
    }

    /// The anchor of `name` inside the resource/document whose absolute
    /// base URI is `abs` (P6b): in-document and already collected resources
    /// first, then a registry document (lazily initialized so its anchors
    /// and inner resources exist). `uri_known` reports whether the URI
    /// addressed anything at all (an unknown URI is an UNSUPPORTED_FEATURE
    /// registry refusal; a missing anchor in a known URI is INVALID_SCHEMA).
    fn lookupScopedAnchor(self: *Compiler, abs: []const u8, name: []const u8, uri_known: *bool) CompileError!?Anchor {
        if (self.lookupResource(abs)) |res| {
            uri_known.* = true;
            for (self.anchors.items) |an| {
                if (an.res == res and std.mem.eql(u8, an.name, name)) return an;
            }
            return null;
        }
        for (self.registry) |*d| {
            if (std.mem.eql(u8, d.uri, abs)) {
                uri_known.* = true;
                try self.ensureRegDocInit(d);
                for (self.anchors.items) |an| {
                    if (an.res == d.root and std.mem.eql(u8, an.name, name)) return an;
                }
                return null;
            }
        }
        return null;
    }

    /// spec-v1 P6b: $recursiveRef (2019-09). Only the '#' form exists;
    /// statically it addresses the current resource root. When that root
    /// carries $recursiveAnchor: true (the bookend), the target is the
    /// outermost resource root of the dynamic scope with
    /// $recursiveAnchor: true. Sibling assertions conjoin.
    fn compileRecursiveRef(self: *Compiler, v: *const json.Value, kw: Keywords, rv: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        const s = switch (rv.v) {
            .string => |s| s,
            else => return self.fail(error.InvalidSchema, rv.offset, "$recursiveRef must be a string", .{}),
        };
        if (!std.mem.eql(u8, s, "#")) {
            return self.fail(error.InvalidSchema, rv.offset, "$recursiveRef must be '#' in draft 2019-09", .{});
        }
        var target: *const json.Value = self.resource_stack.items[self.resource_stack.items.len - 1];
        if (hasRecursiveAnchor(target)) {
            for (self.dyn_stack.items) |rr| {
                if (hasRecursiveAnchor(rr)) {
                    target = rr;
                    break;
                }
            }
        }
        const ref_node = try self.expandRefTarget(target, rv, depth, s);
        if (hasRefSiblings(kw)) {
            return self.compileRefWithSiblings(v, kw, ref_node, depth);
        }
        return ref_node;
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
        if (self.profile == .spec_v1) return self.compileTypedSpec(v, kw, depth);
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
        // Applicability (dialect-matrix section 5): a validation keyword
        // constrains only instances of its own type. canonical-v1 makes an
        // inapplicable keyword an error (frozen); spec-v1 ignores it.
        const app_err = self.profile != .spec_v1;
        switch (t) {
            .object => {
                if (app_err and (kw.items != null or kw.min_items != null or kw.max_items != null or
                    kw.min_length != null or kw.max_length != null))
                {
                    return self.fail(error.InvalidSchema, v.offset, "array/string keywords are not applicable to type object", .{});
                }
            },
            .array => {
                if (app_err and (kw.properties != null or kw.required != null or kw.additional != null or
                    kw.min_length != null or kw.max_length != null))
                {
                    return self.fail(error.InvalidSchema, v.offset, "object/string keywords are not applicable to type array", .{});
                }
                if (min_items != null and max_items != null and min_items.? > max_items.?) {
                    return self.fail(error.UnsatisfiableConstraint, v.offset, "minItems {d} > maxItems {d}", .{ min_items.?, max_items.? });
                }
            },
            .string => {
                if (app_err and (kw.properties != null or kw.required != null or kw.additional != null or
                    kw.items != null or kw.min_items != null or kw.max_items != null))
                {
                    return self.fail(error.InvalidSchema, v.offset, "object/array keywords are not applicable to type string", .{});
                }
                if (min_len != null and max_len != null and min_len.? > max_len.?) {
                    return self.fail(error.UnsatisfiableConstraint, v.offset, "minLength {d} > maxLength {d}", .{ min_len.?, max_len.? });
                }
            },
            else => {
                if (app_err and (kw.properties != null or kw.required != null or kw.additional != null or
                    kw.items != null or kw.min_items != null or kw.max_items != null or
                    kw.min_length != null or kw.max_length != null))
                {
                    return self.fail(error.InvalidSchema, v.offset, "structural keywords are not applicable to this type", .{});
                }
            },
        }
        if (has_enum) return self.compileEnum(v, t, values, min_len, max_len);
        switch (t) {
            .object => return self.compileObject(v, kw, depth),
            .array => {
                // Absent items constrains nothing (dialect-matrix section
                // 7): in spec-v1 the elements are AnyJSON.
                const item_id = if (kw.items) |items_v| blk: {
                    try self.path.append(self.a, "items");
                    defer _ = self.path.pop();
                    break :blk try self.compileSchema(items_v, depth + 1);
                } else if (self.profile == .spec_v1)
                    try self.compileAnyJSON(depth + 1)
                else
                    return self.fail(error.InvalidSchema, v.offset, "array requires 'items'", .{});
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
                return self.addNode(try self.builder.alternativesNode(&[_]grammar.NodeId{ t_id, f_id }));
            },
            .null_ => return self.addNode(.{ .literal = try self.builder.addLiteral("null") }),
        }
    }

    // ---- spec-v1 typed path (semantics-spec-v1 4) ----

    const ALL_TYPES: u8 = 0x7F;

    fn typeBit(t: SchemaType) u8 {
        return @as(u8, 1) << @intFromEnum(t);
    }

    fn readTypeSet(self: *Compiler, tv: *const json.Value) CompileError!u8 {
        switch (tv.v) {
            .string => |s| {
                const t = parseTypeName(s) orelse
                    return self.fail(error.InvalidSchema, tv.offset, "unknown type '{s}'", .{s});
                return typeBit(t);
            },
            .array => |items| {
                if (items.len == 0) {
                    return self.fail(error.InvalidSchema, tv.offset, "type list must not be empty", .{});
                }
                var mask: u8 = 0;
                for (items) |it| {
                    const s = switch (it.v) {
                        .string => |s| s,
                        else => return self.fail(error.InvalidSchema, it.offset, "type entries must be strings", .{}),
                    };
                    const t = parseTypeName(s) orelse
                        return self.fail(error.InvalidSchema, it.offset, "unknown type '{s}'", .{s});
                    const b = typeBit(t);
                    if (mask & b != 0) {
                        return self.fail(error.InvalidSchema, it.offset, "duplicate type '{s}'", .{s});
                    }
                    mask |= b;
                }
                return mask;
            },
            else => return self.fail(error.InvalidSchema, tv.offset, "type must be a string or an array of strings", .{}),
        }
    }

    /// Canonical decimal form of a schema number lexeme (ADR-0006 D6):
    /// value = digits x 10^exp10, digits with no leading/trailing zeros,
    /// zero as the empty digit string with the sign dropped (-0 == 0).
    /// The digits slice lives in the compile arena.
    fn canonicalDecimal(self: *Compiler, lexeme: []const u8, offset: u32) CompileError!grammar.NumConst {
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
        if (i < lexeme.len and (lexeme[i] == 'e' or lexeme[i] == 'E')) {
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
        if (digits.len == 0) return .{ .neg = false, .digits = &.{}, .exp10 = 0 };
        var z: usize = 0;
        while (digits[digits.len - 1 - z] == '0') z += 1;
        digits = digits[0 .. digits.len - z];
        if (digits.len > 400) {
            return self.fail(error.InvalidSchema, offset, "number has more than 400 significant digits", .{});
        }
        return .{
            .neg = neg,
            .digits = digits,
            .exp10 = exp_val - @as(i64, @intCast(frac_part.len)) + @as(i64, @intCast(z)),
        };
    }

    // ---- spec-v1 P4: numeric range and divisibility ----

    /// Resolved numeric assertion of a schema object (minimum/maximum/
    /// exclusiveMinimum/exclusiveMaximum after dialect normalization, and
    /// multipleOf). Bounds are canonical decimals; `divisor` the raw
    /// multipleOf value (> 0).
    const NumConstraint = struct {
        lo: ?grammar.NumConst = null,
        lo_excl: bool = false,
        hi: ?grammar.NumConst = null,
        hi_excl: bool = false,
        divisor: ?grammar.NumConst = null,
    };

    fn readNumKeyword(self: *Compiler, kv: *const json.Value, comptime name: []const u8) CompileError!grammar.NumConst {
        const lex = switch (kv.v) {
            .number => |l| l,
            else => return self.fail(error.InvalidSchema, kv.offset, name ++ " must be a number", .{}),
        };
        return self.canonicalDecimal(lex, kv.offset);
    }

    /// Combine a candidate bound with the present one keeping the tighter
    /// (JSON Schema: minimum and a standalone exclusiveMinimum conjoin).
    fn tightenBound(lo: *?grammar.NumConst, lo_excl: *bool, cand: grammar.NumConst, cand_excl: bool, comptime is_lo: bool) void {
        if (lo.* == null) {
            lo.* = cand;
            lo_excl.* = cand_excl;
            return;
        }
        const c = grammar.cmpNumConst(cand, lo.*.?);
        const tighter = if (is_lo) c == .gt else c == .lt;
        if (tighter) {
            lo.* = cand;
            lo_excl.* = cand_excl;
        } else if (c == .eq) {
            lo_excl.* = lo_excl.* or cand_excl;
        }
    }

    /// Read and normalize the numeric keywords (spec-v1 P4; dialect-matrix
    /// sections 5 and 8): draft-04 boolean exclusive* modify the bound they
    /// name (dropped when the bound is absent), draft-06+ exclusive* are
    /// standalone bounds conjoined with minimum/maximum. Returns null when
    /// no numeric keyword is present.
    fn readNumConstraint(self: *Compiler, kw: Keywords) CompileError!?NumConstraint {
        var nc: NumConstraint = .{};
        var any = false;
        if (kw.minimum) |mv| {
            any = true;
            try self.path.append(self.a, "minimum");
            defer _ = self.path.pop();
            const m = try self.readNumKeyword(mv, "minimum");
            nc.lo = m;
            nc.lo_excl = false;
        }
        if (kw.maximum) |mv| {
            any = true;
            try self.path.append(self.a, "maximum");
            defer _ = self.path.pop();
            const m = try self.readNumKeyword(mv, "maximum");
            nc.hi = m;
            nc.hi_excl = false;
        }
        if (self.dialect == .draft04) {
            // Boolean modifiers (section 8): true with the bound present
            // makes it exclusive; true without the bound is dropped.
            if (kw.excl_min_bool orelse false) {
                if (nc.lo != null) nc.lo_excl = true;
            }
            if (kw.excl_max_bool orelse false) {
                if (nc.hi != null) nc.hi_excl = true;
            }
        } else {
            if (kw.excl_min) |mv| {
                any = true;
                try self.path.append(self.a, "exclusiveMinimum");
                defer _ = self.path.pop();
                const m = try self.readNumKeyword(mv, "exclusiveMinimum");
                tightenBound(&nc.lo, &nc.lo_excl, m, true, true);
            }
            if (kw.excl_max) |mv| {
                any = true;
                try self.path.append(self.a, "exclusiveMaximum");
                defer _ = self.path.pop();
                const m = try self.readNumKeyword(mv, "exclusiveMaximum");
                tightenBound(&nc.hi, &nc.hi_excl, m, true, false);
            }
        }
        if (kw.multiple_of) |mv| {
            any = true;
            try self.path.append(self.a, "multipleOf");
            defer _ = self.path.pop();
            const m = try self.readNumKeyword(mv, "multipleOf");
            if (m.neg or m.digits.len == 0) {
                return self.fail(error.InvalidSchema, mv.offset, "multipleOf must be greater than zero", .{});
            }
            nc.divisor = m;
        }
        if (!any) return null;
        return nc;
    }

    /// The range of a resolved constraint is empty (compile-time arm drop,
    /// ADR-0006 D1): hi < lo, or hi == lo with either bound exclusive.
    fn numRangeEmpty(nc: NumConstraint) bool {
        if (nc.lo == null or nc.hi == null) return false;
        const c = grammar.cmpNumConst(nc.lo.?, nc.hi.?);
        if (c == .gt) return true;
        return c == .eq and (nc.lo_excl or nc.hi_excl);
    }

    /// The grammar NumMult of a resolved divisor; divisors with more than
    /// 10 significant digits are refused (the parser's residue is u32-wide;
    /// a refusal is never approximated, A6).
    fn numMultNode(self: *Compiler, mv: *const json.Value, d: grammar.NumConst) CompileError!grammar.NumMult {
        try self.path.append(self.a, "multipleOf");
        defer _ = self.path.pop();
        const div = std.fmt.parseInt(u32, d.digits, 10) catch
            return self.fail(error.UnsupportedFeature, mv.offset, "multipleOf divisors with more than 10 significant digits are not supported in spec-v1", .{});
        var co = div;
        while (co % 2 == 0) co /= 2;
        while (co % 5 == 0) co /= 5;
        return .{ .div = div, .div_exp10 = d.exp10, .co = co };
    }

    /// A number arm conjoined with the resolved numeric assertion: the
    /// bounds and the divisibility are separate exact machines combined
    /// through an allOf comb node (ADR-0005 D2 verdict at the confirmed
    /// boundary); a single part stands alone.
    fn numConstrainedNode(self: *Compiler, base: grammar.NodeId, kw: Keywords, nc: NumConstraint) CompileError!grammar.NodeId {
        var parts: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        try parts.append(self.a, base);
        if (nc.lo != null or nc.hi != null) {
            try parts.append(self.a, try self.addNode(.{ .num_range = .{
                .min = nc.lo,
                .min_excl = nc.lo_excl,
                .max = nc.hi,
                .max_excl = nc.hi_excl,
            } }));
        }
        if (nc.divisor) |d| {
            try parts.append(self.a, try self.addNode(.{ .num_mult = try self.numMultNode(kw.multiple_of.?, d) }));
        }
        if (parts.items.len == 1) return base;
        return self.addNode(.{ .comb = .{
            .kind = .allof,
            .branches = try self.builder.copyNodeIds(parts.items),
        } });
    }

    /// An empty-language position (ADR-0006 D1): a refusal at the root, an
    /// empty-language node in subschema position (the document may still be
    /// valid through other positions, e.g. an absent optional property).
    fn emptySubschema(self: *Compiler, depth: u32, offset: u32, comptime fmt: []const u8, args: anytype) CompileError!grammar.NodeId {
        if (depth == 1) return self.fail(error.UnsatisfiableConstraint, offset, fmt, args);
        return self.emptyLangNode();
    }

    /// spec-v1 typed path with boolean combinators (semantics-spec-v1 4,
    /// P3): every combinator keyword compiles to its own part; the parts
    /// and the typed core conjoin - a single part stands alone, several
    /// become an allOf comb node (exact NFA intersection, ADR-0005 D2; no
    /// merge optimization). A part with an empty language empties the
    /// whole conjunction.
    fn compileTypedSpec(self: *Compiler, v: *const json.Value, kw: Keywords, depth: u32) CompileError!grammar.NodeId {
        const has_comb = kw.any_of != null or kw.one_of != null or kw.all_of != null or
            kw.not_ != null or kw.if_ != null or kw.then_ != null or kw.else_ != null;
        const has_typed = !has_comb or kw.ty != null or kw.properties != null or kw.required != null or
            kw.additional != null or kw.items != null or kw.min_items != null or kw.max_items != null or
            kw.min_length != null or kw.max_length != null or kw.enum_ != null or kw.const_ != null or
            kw.pattern_props != null or kw.prop_names != null or kw.min_props != null or
            kw.max_props != null or kw.dependencies != null or kw.dep_required != null or
            kw.dep_schemas != null or kw.prefix_items != null or kw.additional_items != null or
            kw.contains != null or kw.min_contains != null or kw.max_contains != null or
            kw.unique_items != null or kw.pattern_ != null or kw.minimum != null or
            kw.maximum != null or kw.multiple_of != null or kw.excl_min != null or kw.excl_max != null;
        var parts: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        if (has_typed) {
            try parts.append(self.a, try self.compileTypedSpecCore(v, kw, depth));
        }
        if (kw.any_of) |av| {
            try parts.append(self.a, try self.compileAnyOf(av, depth));
        }
        if (kw.one_of) |ov| {
            try parts.append(self.a, try self.compileOneOf(ov, depth));
        }
        if (kw.all_of) |av| {
            try parts.append(self.a, try self.compileAllOf(av, depth));
        }
        if (kw.not_) |nv| {
            try self.path.append(self.a, "not");
            defer _ = self.path.pop();
            try parts.append(self.a, try self.compileComplement(nv, depth + 1));
        }
        if (try self.compileIfElse(kw, depth)) |id| {
            try parts.append(self.a, id);
        }
        for (parts.items) |id| {
            if (self.isEmptyNode(id)) {
                return self.emptySubschema(depth, v.offset, "the conjunction defines an empty language", .{});
            }
        }
        // then/else without if are inert: a combinator-only schema may end
        // up with no part at all, which is AnyJSON.
        if (parts.items.len == 0) return self.compileAnyJSON(depth);
        return self.conjoinAllNodes(parts.items, v.offset, depth);
    }

    /// spec-v1 typed path (semantics-spec-v1 4): `type` is a string, a
    /// list of strings (union), or absent (all seven types); inapplicable
    /// validation keywords are ignored; enum/const match by value.
    fn compileTypedSpecCore(self: *Compiler, v: *const json.Value, kw: Keywords, depth: u32) CompileError!grammar.NodeId {
        var types: u8 = ALL_TYPES;
        if (kw.ty) |tv| types = try self.readTypeSet(tv);
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
                // spec-v1: const next to enum conjoins - the two
                // keywords are independent assertions, so the enum members
                // equal to the const value survive (value equality,
                // semantics-spec-v1 2); an empty intersection is the
                // empty-language outcome below.
                var kept: std.ArrayListUnmanaged(*const json.Value) = .{};
                for (values) |ev| {
                    if (try self.constValueEqual(ev, cv)) try kept.append(self.a, ev);
                }
                values = kept.items;
            } else {
                const single = try self.a.alloc(*const json.Value, 1);
                single[0] = cv;
                values = single;
            }
            has_enum = true;
        }
        // spec-v1 P6a (ADR-0009): inside an unevaluated* scenario walk a
        // one-shot mask restricts this schema's top-level type set to the
        // guard's container kinds; the outside kinds are covered by the
        // walk's own top-level arm. enum/const values stay unmasked (their
        // outside acceptance is part of the scenario language).
        if (self.top_mask != 0) {
            const masked = types & self.top_mask;
            self.top_mask = 0;
            if (!has_enum and masked != 0) types = masked;
        }
        // spec-v1 P4: numeric range/divisibility applies to the integer
        // and number arms and filters numeric enum/const values; other
        // types are unaffected (applicability, dialect-matrix section 5).
        const nc = try self.readNumConstraint(kw);
        if (has_enum) return self.compileEnumSpec(v, types, values, kw, depth, nc);
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        inline for (std.meta.fields(SchemaType)) |fld| {
            const t: SchemaType = @enumFromInt(fld.value);
            if (types & typeBit(t) != 0) {
                if (try self.compileArm(t, v, kw, depth, nc)) |id| {
                    try arms.append(self.a, id);
                }
            }
        }
        if (arms.items.len == 0) {
            return self.emptySubschema(depth, v.offset, "no value satisfies the declared type and bounds", .{});
        }
        if (arms.items.len == 1) return arms.items[0];
        return self.addNode(try self.builder.alternativesNode(arms.items));
    }

    // ---- spec-v1 P3: boolean combinators (semantics-spec-v1 4.6) ----

    /// Branches of an array-form combinator: a non-empty array of at most
    /// 64 subschemas (64 is the thread/instance budget of the parser).
    fn compileCombBranches(self: *Compiler, av: *const json.Value, comptime name: []const u8, depth: u32) CompileError![]grammar.NodeId {
        const items = switch (av.v) {
            .array => |it| it,
            else => return self.fail(error.InvalidSchema, av.offset, name ++ " must be a non-empty array", .{}),
        };
        if (items.len == 0) {
            return self.fail(error.InvalidSchema, av.offset, name ++ " must be a non-empty array", .{});
        }
        if (items.len > 64) {
            return self.fail(error.ResourceLimit, av.offset, name ++ " has more than 64 branches", .{});
        }
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        for (items, 0..) |it, i| {
            try self.path.append(self.a, name);
            try self.path.append(self.a, try std.fmt.allocPrint(self.a, "{d}", .{i}));
            defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
            // ADR-0009: inside an unevaluated* scenario walk a branch may
            // assume the guard's container kinds (the outside kinds are
            // covered by the walk's top-level arm).
            const saved_mask = self.top_mask;
            self.top_mask = self.uneval_mask;
            const id = self.compileSchema(it, depth + 1) catch |e| {
                self.top_mask = saved_mask;
                return e;
            };
            self.top_mask = saved_mask;
            try arms.append(self.a, id);
        }
        return arms.toOwnedSlice(self.a);
    }

    /// anyOf: a plain union - branches compile independently, empty-
    /// language branches (including `false`) drop out, the survivors
    /// become a choice/lit_trie node.
    fn compileAnyOf(self: *Compiler, av: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        const branches = try self.compileCombBranches(av, "anyOf", depth);
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        for (branches) |id| {
            if (self.isEmptyNode(id)) continue;
            try arms.append(self.a, id);
        }
        if (arms.items.len == 0) {
            return self.emptySubschema(depth, av.offset, "no anyOf branch can accept any value", .{});
        }
        if (arms.items.len == 1) return arms.items[0];
        return self.addNode(try self.builder.alternativesNode(arms.items));
    }

    /// oneOf: exactly one branch accepts, evaluated at the confirmed value
    /// boundary over the parallel branch parses (comb node, ADR-0005 D2).
    /// The branches must vote on the VALUE, not on its
    /// serialization - a fixed-value branch (const/enum) carrying an object
    /// accepts exactly one key order, so an equal value in another branch
    /// would be miscounted; oneOfValueFilter detects such overlaps and
    /// removes the duplicated values (or refuses when it cannot).
    fn compileOneOf(self: *Compiler, ov: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        const effective = (try self.oneOfValueFilter(ov, depth)) orelse ov;
        const branches = try self.compileCombBranches(effective, "oneOf", depth);
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        for (branches) |id| {
            if (self.isEmptyNode(id)) continue;
            try arms.append(self.a, id);
        }
        if (arms.items.len == 0) {
            return self.emptySubschema(depth, ov.offset, "no oneOf branch can accept any value", .{});
        }
        // Exactly-one over a single live branch is the branch itself.
        if (arms.items.len == 1) return arms.items[0];
        return self.addNode(.{ .comb = .{
            .kind = .oneof,
            .branches = try self.builder.copyNodeIds(arms.items),
        } });
    }

    /// allOf: every branch accepts (comb node). An empty-language branch
    /// empties the whole conjunction at compile time. Object
    /// branches with contradictory declared-key orders would intersect to a
    /// byte language covering no serialization of otherwise valid values;
    /// conjoinAllNodes detects the conflict and merges the branches by
    /// value (or refuses exactly).
    fn compileAllOf(self: *Compiler, av: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        const effective = (try self.allOfValueFilter(av, depth)) orelse av;
        const branches = try self.compileCombBranches(effective, "allOf", depth);
        for (branches) |id| {
            if (self.isEmptyNode(id)) {
                return self.emptySubschema(depth, av.offset, "an allOf branch defines an empty language", .{});
            }
        }
        return self.conjoinAllNodes(branches, av.offset, depth);
    }

    /// A branch schema whose language is exactly a finite value set: only
    /// enum/const (plus inert annotation/addressing keywords). Returns null
    /// for every other shape (the comb counts such branches correctly only
    /// for values whose serializations are unique or shared).
    fn pureEnumBranch(self: *Compiler, bv: *const json.Value) CompileError!?[]const *const json.Value {
        const pairs = switch (bv.v) {
            .object => |p| p,
            else => return null,
        };
        var values: ?[]const *const json.Value = null;
        for (pairs) |p| {
            if (isAnnotation(p.key) or isSpecV1Annotation(p.key)) continue;
            if (std.mem.eql(u8, p.key, "$defs") or std.mem.eql(u8, p.key, "definitions") or
                std.mem.eql(u8, p.key, "$schema") or std.mem.eql(u8, p.key, "$id") or
                std.mem.eql(u8, p.key, "id") or std.mem.eql(u8, p.key, "$anchor")) continue;
            if (std.mem.eql(u8, p.key, "enum")) {
                switch (p.value.v) {
                    .array => |it| values = it,
                    else => return null, // the form error surfaces in compileCombBranches
                }
            } else if (std.mem.eql(u8, p.key, "const")) {
                // `const` is an unknown keyword in draft-04 (ignored there):
                // the branch is not a value set in that dialect.
                if (self.dialect == .draft04) return null;
                const single = try self.a.alloc(*const json.Value, 1);
                single[0] = p.value;
                values = single;
            } else {
                return null;
            }
        }
        return values;
    }

    /// Synthesized `{"enum": [...]}` schema over surviving branch values
    /// (arena-allocated; the value nodes are shared with the source
    /// schema).
    fn synthEnumSchema(self: *Compiler, vals: []const *const json.Value, offset: u32) CompileError!*json.Value {
        const elems = try self.a.alloc(*json.Value, vals.len);
        for (vals, 0..) |vv, i| elems[i] = @constCast(vv);
        const arrv = try self.a.create(json.Value);
        arrv.* = .{ .offset = offset, .v = .{ .array = elems } };
        const pairs = try self.a.alloc(json.Pair, 1);
        pairs[0] = .{ .key = "enum", .key_offset = offset, .value = arrv };
        const sv = try self.a.create(json.Value);
        sv.* = .{ .offset = offset, .v = .{ .object = pairs } };
        return sv;
    }

    /// Per JSON Schema Core 4.2.2 and 10.2.1, oneOf counts branches
    /// accepting the VALUE, while the comb counts branches accepting one
    /// SERIALIZATION. The two agree except for values containing an object
    /// (order-insensitive equality over an order-fixing serialization), so
    /// exactly those values are analyzed here: a value accepted by two or
    /// more branches (value-level) leaves the language. When every branch
    /// is a constant value set the filtered branches compile exactly; a
    /// duplicated value also accepted by a non-constant branch cannot be
    /// subtracted and refuses UNSUPPORTED_FEATURE with the oneOf pointer.
    /// Returns a rewritten oneOf array, or null when no analysis is needed.
    fn oneOfValueFilter(self: *Compiler, ov: *const json.Value, depth: u32) CompileError!?*const json.Value {
        const items = switch (ov.v) {
            .array => |it| it,
            else => return null, // the form error surfaces in compileCombBranches
        };
        if (items.len < 2 or items.len > 64) return null;
        const pures = try self.a.alloc(?[]const *const json.Value, items.len);
        var any_pure_obj = false;
        for (items, 0..) |bv, i| {
            pures[i] = try self.pureEnumBranch(bv);
            if (pures[i]) |vals| {
                for (vals) |vv| {
                    if (valueContainsObject(vv)) {
                        any_pure_obj = true;
                        break;
                    }
                }
            }
        }
        if (!any_pure_obj) return null;
        // Values (by structural equality) that more than one branch accepts.
        var blocked: std.ArrayListUnmanaged(*const json.Value) = .{};
        for (items, 0..) |bv, i| {
            _ = bv;
            const vals = pures[i] orelse continue;
            for (vals) |vv| {
                if (!valueContainsObject(vv)) continue;
                var already = false;
                for (blocked.items) |bv2| {
                    if (try self.constValueEqual(vv, bv2)) {
                        already = true;
                        break;
                    }
                }
                if (already) continue;
                var count: usize = 0;
                var via_nonpure = false;
                for (items, 0..) |ov2, j| {
                    if (j == i) continue;
                    if (pures[j]) |ovals| {
                        for (ovals) |x| {
                            if (try self.constValueEqual(vv, x)) {
                                count += 1;
                                break;
                            }
                        }
                    } else {
                        switch (try self.staticValidate(vv, ov2, depth + 1)) {
                            .pass => {
                                count += 1;
                                via_nonpure = true;
                            },
                            .fail => {},
                            .unknown => {
                                try self.path.append(self.a, "oneOf");
                                defer _ = self.path.pop();
                                return self.fail(error.UnsupportedFeature, self.static_unknown_off, "a oneOf branch cannot be evaluated by value against an object-valued enum/const member ('{s}')", .{self.static_unknown_kw});
                            },
                        }
                    }
                }
                if (count > 0) {
                    if (via_nonpure) {
                        try self.path.append(self.a, "oneOf");
                        defer _ = self.path.pop();
                        return self.fail(error.UnsupportedFeature, vv.offset, "a oneOf enum/const value is also accepted by a non-constant branch; exact value counting of that overlap is not supported", .{});
                    }
                    try blocked.append(self.a, vv);
                }
            }
        }
        if (blocked.items.len == 0) return null;
        // Rebuild the branches: every pure branch drops its blocked values
        // (an emptied branch is `false` and falls out of the comb).
        const new_items = try self.a.alloc(*json.Value, items.len);
        for (items, 0..) |bv, i| {
            const vals = pures[i] orelse {
                new_items[i] = bv;
                continue;
            };
            var kept: std.ArrayListUnmanaged(*const json.Value) = .{};
            for (vals) |vv| {
                var is_blocked = false;
                for (blocked.items) |bv2| {
                    if (try self.constValueEqual(vv, bv2)) {
                        is_blocked = true;
                        break;
                    }
                }
                if (!is_blocked) try kept.append(self.a, vv);
            }
            if (kept.items.len == 0) {
                const fv = try self.a.create(json.Value);
                fv.* = .{ .offset = bv.offset, .v = .{ .boolean = false } };
                new_items[i] = fv;
            } else {
                new_items[i] = try self.synthEnumSchema(kept.items, bv.offset);
            }
        }
        const arr = try self.a.create(json.Value);
        arr.* = .{ .offset = ov.offset, .v = .{ .array = new_items } };
        return arr;
    }

    /// allOf side: the intersection of the constant value sets of
    /// pure enum/const branches must be computed by VALUE - two branches
    /// spelling the same object with different key orders otherwise
    /// intersect to the empty byte language although the value is valid.
    /// Every value of the first pure branch is checked against all other
    /// branches (pure ones by structural equality, the rest by the static
    /// validator); the survivors replace all pure branches with a single
    /// synthesized enum schema (one serialization per surviving value).
    /// An empty intersection is UNSATISFIABLE; an undecidable non-constant
    /// branch refuses UNSUPPORTED_FEATURE with the allOf pointer. Returns
    /// the rewritten array, or null when no pure branch carries an object.
    fn allOfValueFilter(self: *Compiler, av: *const json.Value, depth: u32) CompileError!?*const json.Value {
        const items = switch (av.v) {
            .array => |it| it,
            else => return null, // the form error surfaces in compileCombBranches
        };
        if (items.len < 2 or items.len > 64) return null;
        const pures = try self.a.alloc(?[]const *const json.Value, items.len);
        var first_pure: ?usize = null;
        var any_pure_obj = false;
        for (items, 0..) |bv, i| {
            pures[i] = try self.pureEnumBranch(bv);
            if (pures[i]) |vals| {
                if (first_pure == null) first_pure = i;
                for (vals) |vv| {
                    if (valueContainsObject(vv)) {
                        any_pure_obj = true;
                        break;
                    }
                }
            }
        }
        if (!any_pure_obj) return null;
        const cand_vals = pures[first_pure.?].?;
        var survivors: std.ArrayListUnmanaged(*const json.Value) = .{};
        for (cand_vals) |vv| {
            var accepted = true;
            for (items, 0..) |bv, j| {
                if (j == first_pure.?) continue;
                if (pures[j]) |ovals| {
                    var hit = false;
                    for (ovals) |x| {
                        if (try self.constValueEqual(vv, x)) {
                            hit = true;
                            break;
                        }
                    }
                    if (!hit) {
                        accepted = false;
                        break;
                    }
                } else {
                    switch (try self.staticValidate(vv, bv, depth + 1)) {
                        .pass => {},
                        .fail => {
                            accepted = false;
                            break;
                        },
                        .unknown => {
                            try self.path.append(self.a, "allOf");
                            defer _ = self.path.pop();
                            return self.fail(error.UnsupportedFeature, self.static_unknown_off, "an allOf branch cannot be evaluated by value against an object-valued enum/const member ('{s}')", .{self.static_unknown_kw});
                        },
                    }
                }
            }
            if (accepted) {
                var dup = false;
                for (survivors.items) |sv| {
                    if (try self.constValueEqual(vv, sv)) {
                        dup = true;
                        break;
                    }
                }
                if (!dup) try survivors.append(self.a, vv);
            }
        }
        var new_items: std.ArrayListUnmanaged(*json.Value) = .{};
        if (survivors.items.len == 0) {
            // The intersection is empty: a `false` branch carries the
            // empty language into the usual allOf reduction (a refusal at
            // the root, an empty-language node in subschema position).
            const fv = try self.a.create(json.Value);
            fv.* = .{ .offset = av.offset, .v = .{ .boolean = false } };
            try new_items.append(self.a, fv);
        } else {
            const enum_schema = try self.synthEnumSchema(survivors.items, items[first_pure.?].offset);
            try new_items.append(self.a, enum_schema);
        }
        for (items, 0..) |bv, i| {
            if (pures[i] != null) continue;
            try new_items.append(self.a, bv);
        }
        const arr = try self.a.create(json.Value);
        arr.* = .{ .offset = av.offset, .v = .{ .array = new_items.items } };
        return arr;
    }

    // ---- spec-v1: allOf declared-key order conflicts ----

    /// Declared-key order constraints of one conjunction branch: each pair
    /// (i < j) of declared property literals fixes a before-edge. A closed
    /// object and an open object both dispatch declared keys in declaration
    /// order (semantics-spec-v1 4.3); a single-object-arm choice (the
    /// otherKindsWrap shape) contributes its arm's constraints; a nested
    /// allOf comb contributes the union. Multi-object choices and non-allOf
    /// combs are not statically analyzable here (`uncertain`).
    const OrderInfo = struct {
        edges: std.ArrayListUnmanaged([2]grammar.Literal) = .{},
        uncertain: bool = false,
    };

    fn collectOrderInfo(self: *Compiler, id: grammar.NodeId, info: *OrderInfo) CompileError!void {
        switch (self.builder.nodes.items[id]) {
            .object => |o| try self.propOrderEdges(o.props, info),
            .open_obj => |o| try self.propOrderEdges(o.props, info),
            .choice => |alts| {
                var found = false;
                var child: OrderInfo = .{};
                for (alts) |arm| {
                    var arm_info: OrderInfo = .{};
                    try self.collectOrderInfo(arm, &arm_info);
                    if (arm_info.edges.items.len > 0 or arm_info.uncertain or self.nodeIsObjectish(arm)) {
                        if (found) {
                            info.uncertain = true;
                            return;
                        }
                        found = true;
                        child = arm_info;
                    }
                }
                if (found) {
                    try info.edges.appendSlice(self.a, child.edges.items);
                    if (child.uncertain) info.uncertain = true;
                }
            },
            .comb => |cb| {
                if (cb.kind == .allof) {
                    for (cb.branches) |br| {
                        if (br == grammar.COMB_NONE) continue;
                        try self.collectOrderInfo(br, info);
                    }
                } else if (cb.branches.len > 0) {
                    var any_obj = false;
                    for (cb.branches) |br| {
                        if (br != grammar.COMB_NONE and self.nodeIsObjectish(br)) any_obj = true;
                    }
                    if (any_obj) info.uncertain = true;
                }
            },
            else => {},
        }
    }

    /// True for node kinds whose language contains multi-key objects
    /// (directly or through a choice/comb).
    fn nodeIsObjectish(self: *Compiler, id: grammar.NodeId) bool {
        return switch (self.builder.nodes.items[id]) {
            .object, .open_obj => true,
            .choice => |alts| blk: {
                for (alts) |arm| {
                    if (self.nodeIsObjectish(arm)) break :blk true;
                }
                break :blk false;
            },
            .comb => |cb| blk: {
                for (cb.branches) |br| {
                    if (br != grammar.COMB_NONE and self.nodeIsObjectish(br)) break :blk true;
                }
                break :blk false;
            },
            else => false,
        };
    }

    fn propOrderEdges(self: *Compiler, props: []const grammar.Prop, info: *OrderInfo) CompileError!void {
        for (props, 0..) |p, i| {
            for (props[i + 1 ..]) |q| {
                try info.edges.append(self.a, .{ p.key, q.key });
            }
        }
    }

    /// Cycle check over the declared-key before-edges: acyclic orders have a
    /// linear extension, so every value valid under all branches has a
    /// serialization accepted by every branch (undeclared keys of open
    /// objects interleave freely); a cycle means no serialization can carry
    /// the conflicting keys together.
    fn orderConflict(self: *Compiler, ids: []const grammar.NodeId) CompileError!bool {
        var info: OrderInfo = .{};
        for (ids) |id| try self.collectOrderInfo(id, &info);
        if (info.edges.items.len == 0) return false;
        // Kahn's algorithm over the small key set (literals compared by
        // bytes - appendEscaped is deterministic, so equal keys have equal
        // literal bytes).
        var keys: std.ArrayListUnmanaged(grammar.Literal) = .{};
        for (info.edges.items) |e| {
            for (e) |k| {
                var seen = false;
                for (keys.items) |x| {
                    if (self.litBytesEq(x, k)) seen = true;
                }
                if (!seen) try keys.append(self.a, k);
            }
        }
        var done = try self.a.alloc(bool, keys.items.len);
        @memset(done, false);
        var remaining = keys.items.len;
        while (remaining > 0) {
            var progressed = false;
            for (keys.items, 0..) |k, i| {
                if (done[i]) continue;
                var has_in = false;
                for (info.edges.items) |e| {
                    if (self.litBytesEq(e[1], k)) {
                        var src_done = false;
                        for (keys.items, 0..) |x, j| {
                            if (self.litBytesEq(x, e[0]) and done[j]) src_done = true;
                        }
                        if (!src_done) has_in = true;
                    }
                }
                if (!has_in) {
                    done[i] = true;
                    remaining -= 1;
                    progressed = true;
                }
            }
            if (!progressed) return true; // cycle
        }
        return false;
    }

    /// Conjunction of compiled parts with the declared-key order analysis:
    /// an acyclic branch set keeps the plain allOf comb (the value-complete
    /// serialization policy); a cyclic one flattens nested allOf combs and
    /// merges the bare open-object branches by value (first-appearance
    /// order, semantics-spec-v1 4.3); a conflict involving a branch shape
    /// that cannot merge refuses UNSUPPORTED_FEATURE instead of compiling
    /// an emptied/fragmented intersection.
    ///
    /// A oneOf/anyOf comb over bare open-object branches conjoined with own
    /// open objects is not cyclic under orderConflict (the comb is
    /// order-uncertain and contributes no edges), yet a branch whose
    /// declared order contradicts the own order makes every serialization
    /// carrying both key sets die at runtime: the own thread and the branch
    /// thread demand opposite key orders. distributeChoiceConflict detects
    /// exactly that shape and merges the own object into every branch
    /// (branch order leading - the serializer's canonical oneOf/anyOf
    /// order), turning the conjunction into the distributed comb.
    fn conjoinAllNodes(self: *Compiler, ids: []const grammar.NodeId, offset: u32, depth: u32) CompileError!grammar.NodeId {
        _ = depth;
        if (ids.len == 1) return ids[0];
        if (try self.distributeChoiceConflict(ids)) |d| return d;
        if (!try self.orderConflict(ids)) {
            return self.addNode(.{ .comb = .{
                .kind = .allof,
                .branches = try self.builder.copyNodeIds(ids),
            } });
        }
        var flat: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        try self.flattenAllof(ids, &flat);
        var merged: ?grammar.NodeId = null;
        var rest: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        for (flat.items) |id| {
            switch (self.builder.nodes.items[id]) {
                .open_obj => {
                    if (merged) |m| {
                        merged = try self.mergeOpenObj(m, id) orelse return self.orderConflictRefusal(offset);
                    } else merged = id;
                },
                else => try rest.append(self.a, id),
            }
        }
        const merged_id = merged orelse return self.orderConflictRefusal(offset);
        for (rest.items) |id| {
            // A surviving branch carrying its own order constraints (a
            // closed object, a wrapped object arm) or an unanalyzable
            // object language can still conflict with the merged order.
            var info: OrderInfo = .{};
            try self.collectOrderInfo(id, &info);
            if (info.edges.items.len > 0 or info.uncertain) return self.orderConflictRefusal(offset);
        }
        if (rest.items.len == 0) return merged_id;
        var parts = try std.ArrayListUnmanaged(grammar.NodeId).initCapacity(self.a, rest.items.len + 1);
        parts.appendAssumeCapacity(merged_id);
        try parts.appendSlice(self.a, rest.items);
        return self.addNode(.{ .comb = .{
            .kind = .allof,
            .branches = try self.builder.copyNodeIds(parts.items),
        } });
    }

    fn flattenAllof(self: *Compiler, ids: []const grammar.NodeId, out: *std.ArrayListUnmanaged(grammar.NodeId)) CompileError!void {
        for (ids) |id| {
            switch (self.builder.nodes.items[id]) {
                .comb => |cb| {
                    if (cb.kind == .allof) {
                        try self.flattenAllof(cb.branches, out);
                        continue;
                    }
                    try out.append(self.a, id);
                },
                else => try out.append(self.a, id),
            }
        }
    }

    fn orderConflictRefusal(self: *Compiler, offset: u32) CompileError {
        return self.fail(error.UnsupportedFeature, offset, "conjoined object schemas impose contradictory declared-key orders and cannot be merged by value", .{});
    }

    /// Distributive merge for the own-object ∧ oneOf/anyOf-of-objects shape
    /// (see conjoinAllNodes). A comb over object branches is order-uncertain
    /// under collectOrderInfo, so a branch whose declared-key order
    /// contradicts the own objects' joint order slips past orderConflict and
    /// then dies at runtime: no serialization can satisfy both orders. When
    /// the shape is exactly
    ///   - own conjuncts: bare open/closed objects, or choices with at most
    ///     one object arm (the remaining arms are non-object residuals),
    ///   - one oneOf/anyOf comb whose branches are bare open objects or
    ///     non-object nodes,
    /// and at least one object branch contradicts the own order, the own
    /// object part merges into every object branch (branch order leading -
    /// the serializer's canonical oneOf/anyOf key order), non-object
    /// branches are dropped from the distributed comb (the own object part
    /// is object-only, so they can never join a conjunction acceptance),
    /// and each own residual arm keeps a plain conjunction with the
    /// original comb (non-object values carry no key order, so no conflict
    /// exists there). Without a contradiction the plain allOf already
    /// accepts the canonical serialization. Null when the shape does not
    /// match or any merge is unsafe (the mergeOpenObj null shapes) - the
    /// caller then keeps the plain allOf.
    fn distributeChoiceConflict(self: *Compiler, ids: []const grammar.NodeId) CompileError!?grammar.NodeId {
        var own_objs: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        var residuals: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        var comb_id: ?grammar.NodeId = null;
        for (ids) |id| {
            switch (self.builder.nodes.items[id]) {
                .open_obj, .object => try own_objs.append(self.a, id),
                .comb => |cb| {
                    if (cb.kind == .allof or comb_id != null) return null;
                    for (cb.branches) |br| {
                        if (br == grammar.COMB_NONE) return null;
                        switch (self.builder.nodes.items[br]) {
                            .open_obj => {},
                            else => if (self.nodeIsObjectish(br)) return null,
                        }
                    }
                    comb_id = id;
                },
                .choice => |alts| {
                    var obj_arms: u32 = 0;
                    for (alts) |arm| {
                        switch (self.builder.nodes.items[arm]) {
                            .open_obj, .object => {
                                obj_arms += 1;
                                try own_objs.append(self.a, arm);
                            },
                            else => {
                                if (self.nodeIsObjectish(arm)) return null;
                                try residuals.append(self.a, arm);
                            },
                        }
                    }
                    // Two object arms of one choice disjoin; conjoining them
                    // into the branch merge would change the language.
                    if (obj_arms > 1) return null;
                },
                else => {
                    if (self.nodeIsObjectish(id)) return null;
                    try residuals.append(self.a, id);
                },
            }
        }
        const cid = comb_id orelse return null;
        if (own_objs.items.len == 0) return null;
        const cb = self.builder.nodes.items[cid].comb;
        var any_conflict = false;
        for (cb.branches) |br| {
            switch (self.builder.nodes.items[br]) {
                .open_obj => {},
                else => continue,
            }
            var check: std.ArrayListUnmanaged(grammar.NodeId) = .{};
            try check.appendSlice(self.a, own_objs.items);
            try check.append(self.a, br);
            if (try self.orderConflict(check.items)) {
                any_conflict = true;
                break;
            }
        }
        if (!any_conflict) return null;
        // Closed own objects join the merge as open objects whose
        // undeclared-value schema is the empty language.
        var own_merged: ?grammar.NodeId = null;
        for (own_objs.items) |id| {
            const open_id = switch (self.builder.nodes.items[id]) {
                .open_obj => id,
                .object => |o| try self.addNode(.{ .open_obj = .{
                    .props = o.props,
                    .extra_required = &.{},
                    .value = try self.emptyLangNode(),
                } }),
                else => unreachable,
            };
            own_merged = if (own_merged) |m|
                try self.mergeOpenObj(m, open_id) orelse return null
            else
                open_id;
        }
        var branches: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        for (cb.branches) |br| {
            switch (self.builder.nodes.items[br]) {
                .open_obj => try branches.append(self.a, try self.mergeOpenObj(br, own_merged.?) orelse return null),
                else => {}, // non-object branch: unreachable under the own object
            }
        }
        const distributed = try self.addNode(.{ .comb = .{
            .kind = cb.kind,
            .branches = try self.builder.copyNodeIds(branches.items),
        } });
        if (residuals.items.len == 0) return @as(?grammar.NodeId, distributed);
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        try arms.append(self.a, distributed);
        for (residuals.items) |r| {
            const pair = try self.a.alloc(grammar.NodeId, 2);
            pair[0] = r;
            pair[1] = cid;
            try arms.append(self.a, try self.addNode(.{ .comb = .{
                .kind = .allof,
                .branches = pair,
            } }));
        }
        const out = try self.addNode(.{ .choice = try self.builder.copyNodeIds(arms.items) });
        return @as(?grammar.NodeId, out);
    }

    /// if/then/else (draft-07+): the comb ifelse node over branches
    /// [if, then, else] with absent applicators as COMB_NONE (verdict
    /// (if /\ then) \/ (~if /\ else), ADR-0005 D2). Returns null when the
    /// combination places no constraint.
    fn compileIfElse(self: *Compiler, kw: Keywords, depth: u32) CompileError!?grammar.NodeId {
        const iv = kw.if_ orelse return null; // then/else without if are inert
        if (kw.then_ == null and kw.else_ == null) return null;
        try self.path.append(self.a, "if");
        defer _ = self.path.pop();
        const if_node = try self.compileSchema(iv, depth + 1);
        var then_node: grammar.NodeId = grammar.COMB_NONE;
        var else_node: grammar.NodeId = grammar.COMB_NONE;
        if (kw.then_) |tv| {
            try self.path.append(self.a, "then");
            defer _ = self.path.pop();
            then_node = try self.compileSchema(tv, depth + 1);
        }
        if (kw.else_) |ev| {
            try self.path.append(self.a, "else");
            defer _ = self.path.pop();
            else_node = try self.compileSchema(ev, depth + 1);
        } else {
            // An absent else accepts anything when the condition fails.
            // An AnyJSON branch expresses exactly that verdict and, unlike
            // a COMB_NONE slot, also CARRIES the group through values the
            // if/then branches cannot even parse (their rejection is final,
            // but the group's value extent must still be tracked).
            else_node = try self.compileAnyJSON(depth + 1);
        }
        const then_empty = then_node != grammar.COMB_NONE and self.isEmptyNode(then_node);
        const else_empty = else_node != grammar.COMB_NONE and self.isEmptyNode(else_node);
        if (then_empty and else_empty) {
            // (if /\ false) \/ (~if /\ false): nothing can ever be valid.
            return try self.emptySubschema(depth, iv.offset, "if/then/else can never accept: both applicators are empty", .{});
        }
        if (self.isEmptyNode(if_node)) {
            // The condition never holds: only the else applicator remains
            // (vacuous when absent). An empty else propagates as the empty
            // part and empties the conjunction in the caller.
            if (else_node == grammar.COMB_NONE) return null;
            return else_node;
        }
        const branches = try self.a.alloc(grammar.NodeId, 3);
        branches[0] = if_node;
        branches[1] = then_node;
        branches[2] = else_node;
        return try self.addNode(.{ .comb = .{ .kind = .ifelse, .branches = branches } });
    }

    /// Any-value arm of one JSON type (complement building block).
    fn anyTypeArm(self: *Compiler, t: SchemaType, depth: u32) CompileError!grammar.NodeId {
        switch (t) {
            .object => return self.addNode(.{ .open_obj = .{
                .props = &.{},
                .extra_required = &.{},
                .value = try self.compileAnyJSON(depth + 1),
            } }),
            .array => return self.addNode(.{ .repeat = .{
                .item = try self.compileAnyJSON(depth + 1),
                .min = 0,
                .max = grammar.UNBOUNDED,
            } }),
            .string => return self.addNode(.{ .str = .{ .min_len = 0, .max_len = grammar.UNBOUNDED } }),
            .integer => return self.addNode(.{ .int_num = {} }),
            .number => return self.addNode(.{ .num_v = {} }),
            .boolean => {
                const t_id = try self.addNode(.{ .literal = try self.builder.addLiteral("true") });
                const f_id = try self.addNode(.{ .literal = try self.builder.addLiteral("false") });
                return self.addNode(try self.builder.alternativesNode(&[_]grammar.NodeId{ t_id, f_id }));
            },
            .null_ => return self.addNode(.{ .literal = try self.builder.addLiteral("null") }),
        }
    }

    /// Complement of a type set: the arms of the remaining types. Numeric
    /// complement: excluding `number` removes every numeric value;
    /// excluding only `integer` leaves the non-integer numbers
    /// (not_int_num node).
    fn compileTypeComplement(self: *Compiler, tv: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        const types = try self.readTypeSet(tv);
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        inline for (std.meta.fields(SchemaType)) |fld| {
            const t: SchemaType = @enumFromInt(fld.value);
            if (types & typeBit(t) == 0 and t != .number and t != .integer) {
                try arms.append(self.a, try self.anyTypeArm(t, depth));
            }
        }
        if (types & typeBit(.number) == 0) {
            if (types & typeBit(.integer) != 0) {
                try arms.append(self.a, try self.addNode(.{ .not_int_num = {} }));
            } else {
                try arms.append(self.a, try self.addNode(.{ .num_v = {} }));
            }
        }
        if (arms.items.len == 0) {
            return self.emptySubschema(depth, tv.offset, "'not' of every type defines an empty language", .{});
        }
        if (arms.items.len == 1) return arms.items[0];
        return self.addNode(try self.builder.alternativesNode(arms.items));
    }

    /// Complement of an enum/const set over scalars: every value except
    /// the listed ones. Strings minus a finite set use the str_excl node
    /// (trie of the quoted forbidden literals), numbers the num_excl node
    /// (at most two constants); containers in the set are refused.
    fn compileEnumComplement(self: *Compiler, values: []const *const json.Value, offset: u32, depth: u32) CompileError!grammar.NodeId {
        var forbid_strs: std.ArrayListUnmanaged(grammar.Literal) = .{};
        var forbid_nums: std.ArrayListUnmanaged(grammar.NumConst) = .{};
        var forbid_true = false;
        var forbid_false = false;
        var forbid_null = false;
        for (values) |val| {
            switch (val.v) {
                .string => |s| {
                    var lit: std.ArrayListUnmanaged(u8) = .{};
                    try lit.append(self.a, '"');
                    try appendEscaped(&lit, self.a, s);
                    try lit.append(self.a, '"');
                    try forbid_strs.append(self.a, try self.builder.addLiteral(lit.items));
                },
                .number => |lex| {
                    const nc = try self.canonicalDecimal(lex, val.offset);
                    var dup = false;
                    for (forbid_nums.items) |e| {
                        if (e.neg == nc.neg and e.exp10 == nc.exp10 and std.mem.eql(u8, e.digits, nc.digits)) {
                            dup = true;
                            break;
                        }
                    }
                    if (!dup) try forbid_nums.append(self.a, nc);
                },
                .boolean => |b| {
                    if (b) forbid_true = true else forbid_false = true;
                },
                .null_v => forbid_null = true,
                .array, .object => {
                    return self.fail(error.UnsupportedFeature, val.offset, "containers in enum/const inside 'not' are not supported in spec-v1", .{});
                },
            }
        }
        if (forbid_nums.items.len > 2) {
            return self.fail(error.UnsupportedFeature, offset, "numeric complement over more than two constants is not supported in spec-v1", .{});
        }
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        try arms.append(self.a, try self.anyTypeArm(.object, depth));
        try arms.append(self.a, try self.anyTypeArm(.array, depth));
        if (forbid_strs.items.len == 0) {
            try arms.append(self.a, try self.anyTypeArm(.string, depth));
        } else {
            try arms.append(self.a, try self.addNode(.{ .str_excl = .{
                .trie = try self.builder.buildTrie(forbid_strs.items),
                .min_len = 0,
                .max_len = grammar.UNBOUNDED,
            } }));
        }
        if (forbid_nums.items.len == 0) {
            try arms.append(self.a, try self.anyTypeArm(.number, depth));
        } else {
            try arms.append(self.a, try self.addNode(.{ .num_excl = .{
                .consts = try self.builder.copyNumConsts(forbid_nums.items),
            } }));
        }
        if (!(forbid_true and forbid_false)) {
            if (forbid_true) {
                try arms.append(self.a, try self.addNode(.{ .literal = try self.builder.addLiteral("false") }));
            } else if (forbid_false) {
                try arms.append(self.a, try self.addNode(.{ .literal = try self.builder.addLiteral("true") }));
            } else {
                try arms.append(self.a, try self.anyTypeArm(.boolean, depth));
            }
        }
        if (!forbid_null) try arms.append(self.a, try self.anyTypeArm(.null_, depth));
        if (arms.items.len == 1) return arms.items[0];
        return self.addNode(try self.builder.alternativesNode(arms.items));
    }

    /// Complement of {required: [...]}: exactly the objects missing at
    /// least one listed key - a union of single-key ban objects (the
    /// trigger key may not appear; non-objects satisfy `required` and are
    /// therefore rejected by `not`).
    fn compileRequiredComplement(self: *Compiler, rv: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        const items = switch (rv.v) {
            .array => |it| it,
            else => return self.fail(error.InvalidSchema, rv.offset, "required must be an array", .{}),
        };
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        var seen: std.StringHashMapUnmanaged(void) = .{};
        for (items) |it| {
            const name = switch (it.v) {
                .string => |s| s,
                else => return self.fail(error.InvalidSchema, it.offset, "required entries must be strings", .{}),
            };
            const gop = try seen.getOrPut(self.a, name);
            if (gop.found_existing) {
                return self.fail(error.InvalidSchema, it.offset, "duplicate name '{s}' in required", .{name});
            }
            var deps = try self.a.alloc(grammar.Dep, 1);
            deps[0] = .{ .trigger = try self.rawKeyLit(name), .kind = .ban };
            try arms.append(self.a, try self.addNode(.{ .open_obj = .{
                .props = &.{},
                .extra_required = &.{},
                .value = try self.compileAnyJSON(depth + 1),
                .deps = deps,
            } }));
        }
        if (arms.items.len == 0) {
            // required: [] always holds: the complement is empty.
            return self.emptySubschema(depth, rv.offset, "'not' of an empty 'required' defines an empty language", .{});
        }
        if (arms.items.len == 1) return arms.items[0];
        return self.addNode(try self.builder.alternativesNode(arms.items));
    }

    /// Complement of {anyOf: [...]}: the conjunction of the branch
    /// complements (De Morgan).
    fn compileAnyOfComplement(self: *Compiler, av: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        const items = switch (av.v) {
            .array => |it| it,
            else => return self.fail(error.InvalidSchema, av.offset, "anyOf must be a non-empty array", .{}),
        };
        if (items.len == 0) {
            return self.fail(error.InvalidSchema, av.offset, "anyOf must be a non-empty array", .{});
        }
        if (items.len > 64) {
            return self.fail(error.ResourceLimit, av.offset, "anyOf has more than 64 branches", .{});
        }
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        for (items, 0..) |it, i| {
            try self.path.append(self.a, "anyOf");
            try self.path.append(self.a, try std.fmt.allocPrint(self.a, "{d}", .{i}));
            defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
            const c = try self.compileComplement(it, depth + 1);
            if (self.isEmptyNode(c)) {
                // One always-true branch makes the anyOf always true.
                return self.emptySubschema(depth, av.offset, "'not' of an always-true 'anyOf' defines an empty language", .{});
            }
            try arms.append(self.a, c);
        }
        if (arms.items.len == 1) return arms.items[0];
        return self.addNode(.{ .comb = .{
            .kind = .allof,
            .branches = try self.builder.copyNodeIds(arms.items),
        } });
    }

    /// Complement of {allOf: [...]}: the union of the branch complements
    /// (De Morgan); empty complements (always-true branches) drop out.
    fn compileAllOfComplement(self: *Compiler, av: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        const items = switch (av.v) {
            .array => |it| it,
            else => return self.fail(error.InvalidSchema, av.offset, "allOf must be a non-empty array", .{}),
        };
        if (items.len == 0) {
            return self.fail(error.InvalidSchema, av.offset, "allOf must be a non-empty array", .{});
        }
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        for (items, 0..) |it, i| {
            try self.path.append(self.a, "allOf");
            try self.path.append(self.a, try std.fmt.allocPrint(self.a, "{d}", .{i}));
            defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
            const c = try self.compileComplement(it, depth + 1);
            if (self.isEmptyNode(c)) continue;
            try arms.append(self.a, c);
        }
        if (arms.items.len == 0) {
            return self.emptySubschema(depth, av.offset, "'not' of an always-true 'allOf' defines an empty language", .{});
        }
        if (arms.items.len == 1) return arms.items[0];
        return self.addNode(try self.builder.alternativesNode(arms.items));
    }

    /// Complement of {type?:"object", properties: {...}}: with an explicit
    /// object type the complement includes every non-object; in both cases
    /// it includes, per property p, the objects carrying p with a value in
    /// the complement of its schema (a required-prop open object).
    fn compilePropsComplement(self: *Compiler, tv: ?*const json.Value, pv: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        const ppairs = switch (pv.v) {
            .object => |p| p,
            else => return self.fail(error.InvalidSchema, pv.offset, "properties must be an object", .{}),
        };
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        if (tv) |t| {
            const s = switch (t.v) {
                .string => |ss| ss,
                else => return self.fail(error.UnsupportedFeature, t.offset, "this form of 'type' inside 'not' is not supported in spec-v1", .{}),
            };
            if (std.mem.eql(u8, s, "object")) {
                try arms.append(self.a, try self.anyTypeArm(.array, depth));
                try arms.append(self.a, try self.anyTypeArm(.string, depth));
                try arms.append(self.a, try self.anyTypeArm(.number, depth));
                try arms.append(self.a, try self.anyTypeArm(.boolean, depth));
                try arms.append(self.a, try self.anyTypeArm(.null_, depth));
            } else if (parseTypeName(s) != null) {
                // properties is inapplicable to non-objects: the operand is
                // a plain type schema.
                return self.compileTypeComplement(t, depth);
            } else {
                return self.fail(error.InvalidSchema, t.offset, "unknown type '{s}'", .{s});
            }
        }
        for (ppairs) |pp| {
            try self.path.append(self.a, "properties");
            try self.path.append(self.a, pp.key);
            defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
            const comp = try self.compileComplement(pp.value, depth + 1);
            if (self.isEmptyNode(comp)) continue; // the key can never fail
            var lit: std.ArrayListUnmanaged(u8) = .{};
            try lit.append(self.a, '"');
            try appendEscaped(&lit, self.a, pp.key);
            try lit.appendSlice(self.a, "\":");
            var props = try self.a.alloc(grammar.Prop, 1);
            props[0] = .{
                .key = try self.builder.addLiteral(lit.items),
                .value = comp,
                .required = true,
            };
            try arms.append(self.a, try self.addNode(.{ .open_obj = .{
                .props = props,
                .extra_required = &.{},
                .value = try self.compileAnyJSON(depth + 1),
            } }));
        }
        if (arms.items.len == 0) {
            return self.emptySubschema(depth, pv.offset, "'not' of this object schema defines an empty language", .{});
        }
        if (arms.items.len == 1) return arms.items[0];
        return self.addNode(try self.builder.alternativesNode(arms.items));
    }

    /// Keys ignored inside a `not` operand, mirroring the subschema scan:
    /// annotations, addressing keywords and keywords unknown to the active
    /// dialect carry no assertion and drop out of the form match; so do
    /// keywords unknown to JSON Schema altogether.
    fn complementKeyIgnored(self: *Compiler, key: []const u8) bool {
        if (isAnnotation(key) or isSpecV1Annotation(key)) return true;
        if (std.mem.eql(u8, key, "$id") or std.mem.eql(u8, key, "$anchor") or
            std.mem.eql(u8, key, "$dynamicAnchor") or std.mem.eql(u8, key, "$recursiveAnchor") or
            std.mem.eql(u8, key, "id") or std.mem.eql(u8, key, "definitions") or
            std.mem.eql(u8, key, "$schema") or std.mem.eql(u8, key, "$vocabulary") or
            std.mem.eql(u8, key, "contentEncoding") or std.mem.eql(u8, key, "contentMediaType") or
            std.mem.eql(u8, key, "contentSchema")) return true;
        const d = self.dialect;
        const new_drafts = d == .d2019_09 or d == .d2020_12;
        if (std.mem.eql(u8, key, "const")) return d == .draft04;
        if (std.mem.eql(u8, key, "$defs")) return !new_drafts;
        if (std.mem.eql(u8, key, "if") or std.mem.eql(u8, key, "then") or std.mem.eql(u8, key, "else")) return d == .draft04 or d == .draft06;
        if (std.mem.eql(u8, key, "propertyNames") or std.mem.eql(u8, key, "contains")) return d == .draft04;
        if (std.mem.eql(u8, key, "minContains") or std.mem.eql(u8, key, "maxContains") or
            std.mem.eql(u8, key, "dependentRequired") or std.mem.eql(u8, key, "dependentSchemas")) return !new_drafts;
        if (std.mem.eql(u8, key, "prefixItems")) return d != .d2020_12;
        if (std.mem.eql(u8, key, "additionalItems")) return d == .d2020_12;
        if (std.mem.eql(u8, key, "dependencies")) return new_drafts;
        if (isKnownUnsupported(key)) return false; // an assertion: match a form or refuse
        const assertion = [_][]const u8{
            "type",              "properties",           "required",        "anyOf",         "oneOf",        "allOf",
            "not",               "if",                   "then",            "else",          "enum",         "const",
            "$ref",              "items",                "minItems",        "maxItems",      "minLength",    "maxLength",
            "patternProperties", "propertyNames",        "minProperties",   "maxProperties", "dependencies", "dependentRequired",
            "dependentSchemas",  "prefixItems",          "additionalItems", "contains",      "minContains",  "maxContains",
            "uniqueItems",       "additionalProperties",
        };
        for (assertion) |k| {
            if (std.mem.eql(u8, key, k)) return false;
        }
        return true; // unknown keyword: no assertion, ignored
    }

    /// Complement language of a subschema (spec-v1 P3 `not`): real grammar
    /// nodes, no runtime re-parse. Supported operand forms: booleans, {},
    /// {type}, {enum}/{const} over scalars, {required}, {not}, {anyOf},
    /// {allOf} and {type?:"object", properties}. Any other form refuses
    /// with a pointer into /not.
    fn compileComplement(self: *Compiler, nv: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        if (depth > self.max_depth) {
            return self.fail(error.ResourceLimit, nv.offset, "schema depth exceeds max_depth {d}", .{self.max_depth});
        }
        const pairs = switch (nv.v) {
            .boolean => |b| {
                // not true = the empty language; not false = AnyJSON.
                if (b) return self.emptySubschema(depth, nv.offset, "'not' of 'true' defines an empty language", .{});
                return self.compileAnyJSON(depth);
            },
            .object => |p| p,
            else => return self.fail(error.InvalidSchema, nv.offset, "schema must be an object", .{}),
        };
        var eff: std.ArrayListUnmanaged(json.Pair) = .{};
        for (pairs) |p| {
            if (self.complementKeyIgnored(p.key)) continue;
            try eff.append(self.a, p);
        }
        if (eff.items.len == 0) {
            // not {} (or only inert keywords): the empty language.
            return self.emptySubschema(depth, nv.offset, "'not' of an always-true schema defines an empty language", .{});
        }
        if (eff.items.len == 1) {
            const p = eff.items[0];
            if (std.mem.eql(u8, p.key, "type")) return self.compileTypeComplement(p.value, depth);
            if (std.mem.eql(u8, p.key, "enum")) {
                const values = switch (p.value.v) {
                    .array => |it| it,
                    else => return self.fail(error.InvalidSchema, p.value.offset, "enum must be an array", .{}),
                };
                if (values.len == 0) {
                    return self.fail(error.InvalidSchema, p.value.offset, "enum must not be empty", .{});
                }
                try self.path.append(self.a, "enum");
                defer _ = self.path.pop();
                return self.compileEnumComplement(values, p.value.offset, depth);
            }
            if (std.mem.eql(u8, p.key, "const")) {
                const single = try self.a.alloc(*const json.Value, 1);
                single[0] = p.value;
                try self.path.append(self.a, "const");
                defer _ = self.path.pop();
                return self.compileEnumComplement(single, p.value.offset, depth);
            }
            if (std.mem.eql(u8, p.key, "required")) return self.compileRequiredComplement(p.value, depth);
            if (std.mem.eql(u8, p.key, "not")) {
                // Double negation: the inner schema as is.
                try self.path.append(self.a, "not");
                defer _ = self.path.pop();
                return self.compileSchema(p.value, depth + 1);
            }
            if (std.mem.eql(u8, p.key, "anyOf")) return self.compileAnyOfComplement(p.value, depth);
            if (std.mem.eql(u8, p.key, "allOf")) return self.compileAllOfComplement(p.value, depth);
            if (std.mem.eql(u8, p.key, "properties")) return self.compilePropsComplement(null, p.value, depth);
            try self.path.append(self.a, p.key);
            defer _ = self.path.pop();
            return self.fail(error.UnsupportedFeature, p.key_offset, "keyword '{s}' inside 'not' is not supported in spec-v1", .{p.key});
        }
        if (eff.items.len == 2) {
            var tv: ?*const json.Value = null;
            var pv: ?*const json.Value = null;
            for (eff.items) |p| {
                if (std.mem.eql(u8, p.key, "type")) {
                    tv = p.value;
                } else if (std.mem.eql(u8, p.key, "properties")) {
                    pv = p.value;
                }
            }
            if (tv != null and pv != null) return self.compilePropsComplement(tv, pv.?, depth);
        }
        try self.path.append(self.a, eff.items[0].key);
        defer _ = self.path.pop();
        return self.fail(error.UnsupportedFeature, eff.items[0].key_offset, "this combination of keywords inside 'not' is not supported in spec-v1", .{});
    }

    /// One union arm; null when the arm's own bounds are contradictory
    /// (the arm contributes no value to the union).
    fn compileArm(self: *Compiler, t: SchemaType, v: *const json.Value, kw: Keywords, depth: u32, nc: ?NumConstraint) CompileError!?grammar.NodeId {
        switch (t) {
            .object => return try self.compileObjectSpec(v, kw, depth),
            .array => {
                var min_items: ?u32 = null;
                var max_items: ?u32 = null;
                if (kw.min_items) |mv| min_items = try self.readBound(mv, "minItems");
                if (kw.max_items) |mv| max_items = try self.readBound(mv, "maxItems");
                if (min_items != null and max_items != null and min_items.? > max_items.?) return null;
                // Tuple prefix: prefixItems (2020-12) or array-form items
                // (draft-04..2019-09). Element i < prefix.len takes
                // prefix[i], later elements take the rest schema.
                var prefix: std.ArrayListUnmanaged(grammar.NodeId) = .{};
                var rest_id: ?grammar.NodeId = null;
                if (kw.prefix_items) |piv| {
                    const items = switch (piv.v) {
                        .array => |it| it,
                        else => return self.fail(error.InvalidSchema, piv.offset, "prefixItems must be an array", .{}),
                    };
                    for (items, 0..) |it, i| {
                        try self.path.append(self.a, "prefixItems");
                        try self.path.append(self.a, try std.fmt.allocPrint(self.a, "{d}", .{i}));
                        defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
                        try prefix.append(self.a, try self.compileSchema(it, depth + 1));
                    }
                    // 2020-12 rest: schema-form items next to prefixItems.
                    if (kw.items) |items_v| {
                        if (items_v.v == .array) {
                            return self.fail(error.InvalidSchema, items_v.offset, "items as an array (tuple form) is not valid in 2020-12; use prefixItems", .{});
                        }
                        try self.path.append(self.a, "items");
                        defer _ = self.path.pop();
                        rest_id = try self.compileSchema(items_v, depth + 1);
                    }
                } else if (kw.items) |items_v| {
                    switch (items_v.v) {
                        .array => |items| {
                            if (self.dialect == .d2020_12) {
                                return self.fail(error.InvalidSchema, items_v.offset, "items as an array (tuple form) is not valid in 2020-12; use prefixItems", .{});
                            }
                            for (items, 0..) |it, i| {
                                try self.path.append(self.a, "items");
                                try self.path.append(self.a, try std.fmt.allocPrint(self.a, "{d}", .{i}));
                                defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
                                try prefix.append(self.a, try self.compileSchema(it, depth + 1));
                            }
                            // additionalItems constrains only the elements
                            // past a tuple (ignored next to schema-form
                            // items); absent means `true`.
                            if (kw.additional_items) |aiv| {
                                switch (aiv.v) {
                                    .boolean => |b| {
                                        if (!b) rest_id = try self.emptyLangNode();
                                    },
                                    else => {
                                        try self.path.append(self.a, "additionalItems");
                                        defer _ = self.path.pop();
                                        rest_id = try self.compileSchema(aiv, depth + 1);
                                    },
                                }
                            }
                        },
                        else => {
                            try self.path.append(self.a, "items");
                            defer _ = self.path.pop();
                            rest_id = try self.compileSchema(items_v, depth + 1);
                        },
                    }
                }
                // contains + minContains/maxContains (contains is draft-06+,
                // the counters 2019-09+; the counters apply only with
                // contains). The element match is exact at runtime (the
                // element bytes are re-parsed against the node). Compiled
                // before the rest schema: P6a folds unevaluatedItems into
                // the rest and needs the contains node for the union.
                var contains_node: ?grammar.NodeId = null;
                var min_contains: u32 = 1;
                var max_contains: u32 = grammar.UNBOUNDED;
                if (kw.contains) |cv| {
                    if (kw.min_contains) |mv| min_contains = try self.readBoundValue(mv, "minContains");
                    if (kw.max_contains) |mv| max_contains = try self.readBoundValue(mv, "maxContains");
                    if (min_contains > max_contains) return null;
                    try self.path.append(self.a, "contains");
                    defer _ = self.path.pop();
                    const cn = try self.compileSchema(cv, depth + 1);
                    if (self.isEmptyNode(cn)) {
                        // No element can ever match: only minContains: 0
                        // keeps the arm alive, and then contains is vacuous.
                        if (min_contains > 0) return null;
                    } else {
                        contains_node = cn;
                    }
                }
                const rest = rest_id orelse try self.compileUnevalRest(kw, contains_node, depth);
                var hi = max_items orelse grammar.UNBOUNDED;
                // An empty rest language caps the array at the prefix
                // length (additionalItems: false, items: false).
                if (self.isEmptyNode(rest)) {
                    const pl: u32 = @intCast(prefix.items.len);
                    hi = if (hi == grammar.UNBOUNDED) pl else @min(hi, pl);
                }
                if (contains_node != null and hi != grammar.UNBOUNDED and min_contains > hi) return null;
                // uniqueItems: completed elements are canonicalized and
                // compared by value at runtime. uniq_finite feeds the
                // ADR-0005 D4 residual when the (prefix-less) element
                // language is a finite literal set.
                var unique = false;
                if (kw.unique_items) |uv| {
                    unique = switch (uv.v) {
                        .boolean => |b| b,
                        else => return self.fail(error.InvalidSchema, uv.offset, "uniqueItems must be a boolean", .{}),
                    };
                }
                var uniq_finite: u32 = 0;
                if (unique and prefix.items.len == 0) {
                    uniq_finite = self.finiteLiteralCount(rest);
                }
                return try self.addNode(.{ .repeat = .{
                    .item = rest,
                    .min = min_items orelse 0,
                    .max = hi,
                    .prefix = try self.builder.copyNodeIds(prefix.items),
                    .contains = contains_node,
                    .min_contains = min_contains,
                    .max_contains = max_contains,
                    .unique = unique,
                    .uniq_finite = uniq_finite,
                } });
            },
            .string => {
                var min_len: ?u32 = null;
                var max_len: ?u32 = null;
                if (kw.min_length) |mv| min_len = try self.readBound(mv, "minLength");
                if (kw.max_length) |mv| max_len = try self.readBound(mv, "maxLength");
                if (min_len != null and max_len != null and min_len.? > max_len.?) return null;
                if (kw.pattern_) |pv| {
                    try self.path.append(self.a, "pattern");
                    defer _ = self.path.pop();
                    const dfa = try self.compilePatternKeyword(pv);
                    const lo = min_len orelse 0;
                    const hi = max_len orelse grammar.UNBOUNDED;
                    // No string within the bounds matches: an empty arm
                    // (like contradictory length bounds).
                    if (!try self.patternWithinBounds(dfa, lo, hi)) return null;
                    return try self.addNode(.{ .str_pat = .{
                        .min_len = lo,
                        .max_len = hi,
                        .dfa = dfa,
                    } });
                }
                return try self.addNode(.{ .str = .{
                    .min_len = min_len orelse 0,
                    .max_len = max_len orelse grammar.UNBOUNDED,
                } });
            },
            .integer => {
                if (nc) |c| {
                    if (numRangeEmpty(c)) return null;
                    return try self.numConstrainedNode(try self.addNode(.{ .int_num = {} }), kw, c);
                }
                return try self.addNode(.{ .int_num = {} });
            },
            .number => {
                if (nc) |c| {
                    if (numRangeEmpty(c)) return null;
                    return try self.numConstrainedNode(try self.addNode(.{ .num_v = {} }), kw, c);
                }
                return try self.addNode(.{ .num_v = {} });
            },
            .boolean => {
                const t_id = try self.addNode(.{ .literal = try self.builder.addLiteral("true") });
                const f_id = try self.addNode(.{ .literal = try self.builder.addLiteral("false") });
                return try self.addNode(try self.builder.alternativesNode(&[_]grammar.NodeId{ t_id, f_id }));
            },
            .null_ => return try self.addNode(.{ .literal = try self.builder.addLiteral("null") }),
        }
    }

    /// spec-v1 P6a: the rest schema of an array arm when unevaluatedItems
    /// folds into it (no items/additionalItems and no in-place applicator
    /// carries element evaluations): beyond the tuple prefix an element is
    /// evaluated iff it matched contains, so the rest schema is the union of
    /// the contains schema and the unevaluatedItems schema.
    fn compileUnevalRest(self: *Compiler, kw: Keywords, contains_node: ?grammar.NodeId, depth: u32) CompileError!grammar.NodeId {
        const uv = kw.uneval_items orelse return self.compileAnyJSON(depth + 1);
        const ui_node: grammar.NodeId = switch (uv.v) {
            .boolean => |b| if (b) return self.compileAnyJSON(depth + 1) else try self.emptyLangNode(),
            else => blk: {
                try self.path.append(self.a, "unevaluatedItems");
                defer _ = self.path.pop();
                break :blk try self.compileSchema(uv, depth + 1);
            },
        };
        if (contains_node) |cn| {
            return self.addNode(try self.builder.alternativesNode(&[_]grammar.NodeId{ cn, ui_node }));
        }
        return ui_node;
    }

    // ---- spec-v1 P6a: unevaluatedProperties/unevaluatedItems (ADR-0009) ----
    //
    // The evaluation set of a schema object (which properties/elements its
    // successful applicators touched) is compiled away, not tracked at
    // runtime: the annotation flow of the unevaluated vocabulary is a
    // monotone union over the in-place applicators that accepted the
    // instance, so the constraint "unevaluated X must satisfy S" is exact
    // as a disjunction over the applicators' acceptance combinations
    // (scenarios). Two key facts keep the scenario count small and the
    // construction exact:
    //   - A subschema carrying its own unevaluatedProperties (resp. Items)
    //     keyword evaluates EVERY property (element) of any instance it
    //     accepts: whatever the other applicators missed, the keyword
    //     itself validated. Its eval contribution is the full set and it
    //     compiles whole (recursion handles its own keyword).
    //   - `contains` evaluates exactly the elements it matched, so the
    //     per-element unevaluated constraint is the plain language union
    //     (contains-schema OR unevaluatedItems-schema).
    // Without in-place applicators unevaluatedProperties is exactly
    // additionalProperties over the locally evaluated keys (and
    // unevaluatedItems the items role) and folds into the object/array arm.
    // With applicators, each scenario conjoins the applicator branches it
    // assumes accepted with a guard: an open object (resp. repeat) whose
    // statically evaluated keys (prefix elements) take AnyJSON and the rest
    // the unevaluated schema, wrapped in a choice with the other-type arms
    // (the keyword is inert off its container type). `not` never
    // contributes evaluations; evaluations inside it are discarded.

    /// Per-type evaluation record of one acceptance scenario.
    const ObjEval = struct {
        all: bool = false, // additionalProperties / a nested unevaluated*
        props: std.ArrayListUnmanaged([]const u8) = .{}, // decoded names
        patterns: std.ArrayListUnmanaged([]const u8) = .{}, // regex sources
    };
    const ArrEval = struct {
        all: bool = false, // schema-form items / a nested unevaluated*
        prefix_len: u32 = 0,
        contains: std.ArrayListUnmanaged(*const json.Value) = .{},
    };
    const Eval = struct { obj: ObjEval = .{}, arr: ArrEval = .{} };
    /// One acceptance combination: the conjuncts (their intersection is the
    /// scenario's language) and what the scenario evaluates.
    const Scenario = struct {
        conjuncts: std.ArrayListUnmanaged(grammar.NodeId) = .{},
        eval: Eval = .{},
    };

    /// At most this many acceptance scenarios per unevaluated* keyword; the
    /// suite never exceeds 8. Excess is a documented refusal.
    const UNEVAL_SCENARIO_CAP: usize = 32;
    /// At most this many distinct patternProperties patterns union into one
    /// guard DFA.
    const UNEVAL_PATTERN_CAP: usize = 8;

    fn strSetEq(a: []const []const u8, b: []const []const u8) bool {
        if (a.len != b.len) return false;
        for (a) |s| {
            var found = false;
            for (b) |t| {
                if (std.mem.eql(u8, s, t)) found = true;
            }
            if (!found) return false;
        }
        return true;
    }

    /// Two evaluation sets are equal when they govern the same properties,
    /// patterns and elements (order-free); equal sets share one guard.
    fn evalEq(a: *const Eval, b: *const Eval) bool {
        if (a.obj.all != b.obj.all) return false;
        if (!strSetEq(a.obj.props.items, b.obj.props.items)) return false;
        if (!strSetEq(a.obj.patterns.items, b.obj.patterns.items)) return false;
        if (a.arr.all != b.arr.all) return false;
        if (a.arr.prefix_len != b.arr.prefix_len) return false;
        if (a.arr.contains.items.len != b.arr.contains.items.len) return false;
        for (a.arr.contains.items) |p| {
            var found = false;
            for (b.arr.contains.items) |q| {
                if (p == q) found = true;
            }
            if (!found) return false;
        }
        return true;
    }

    /// Three-valued answer to "which instances OUTSIDE the mask kinds does
    /// the schema (sans unevaluated*, which is inert there) accept?"
    /// (ADR-0009). `unknown` falls back to the fully wrapped compilation.
    const Outside = enum { all, none, unknown };

    fn outsideAnd(a: Outside, b: Outside) Outside {
        if (a == .none or b == .none) return .none;
        if (a == .unknown or b == .unknown) return .unknown;
        return .all;
    }

    fn outsideOr(a: Outside, b: Outside) Outside {
        if (a == .all or b == .all) return .all;
        if (a == .unknown or b == .unknown) return .unknown;
        return .none;
    }

    fn outsideNeg(a: Outside) Outside {
        return switch (a) {
            .all => .none,
            .none => .all,
            .unknown => .unknown,
        };
    }

    fn outsideAcceptValue(self: *Compiler, v: *const json.Value, mask: u8, visited: *std.ArrayListUnmanaged(*const json.Value)) CompileError!Outside {
        const pairs = switch (v.v) {
            .boolean => |b| return if (b) .all else .none,
            .object => |p| p,
            else => return self.fail(error.InvalidSchema, v.offset, "schema must be an object", .{}),
        };
        const kw = try self.scanKeywords(pairs);
        return self.outsideAccept(v, kw, mask, visited);
    }

    fn outsideAccept(self: *Compiler, v: *const json.Value, kw: Keywords, mask: u8, visited: *std.ArrayListUnmanaged(*const json.Value)) CompileError!Outside {
        _ = v;
        var acc: Outside = .all;
        if (kw.ty) |tv| {
            const ts = try self.readTypeSet(tv);
            acc = outsideAnd(acc, if (ts & ~mask == 0) .none else .unknown);
        }
        // enum/const: all listed values inside the mask means no outside
        // instance is accepted; a mix is unknown.
        var vals: []const *const json.Value = &.{};
        if (kw.enum_) |ev| {
            switch (ev.v) {
                .array => |items| vals = items,
                else => return self.fail(error.InvalidSchema, ev.offset, "enum must be an array", .{}),
            }
        }
        if (kw.const_) |cv| {
            const single = try self.a.alloc(*const json.Value, 1);
            single[0] = cv;
            vals = single;
        }
        if (vals.len > 0) {
            var all_in = true;
            for (vals) |val| {
                if (!try self.valueMatchesTypes(val, mask)) all_in = false;
            }
            acc = outsideAnd(acc, if (all_in) .none else .unknown);
        }
        // String and numeric assertions constrain outside kinds only
        // (masks cover object/array); object keywords constrain the outside
        // when the mask lacks object, array keywords when it lacks array.
        if (kw.min_length != null or kw.max_length != null or kw.pattern_ != null or
            kw.minimum != null or kw.maximum != null or kw.multiple_of != null or
            kw.excl_min != null or kw.excl_max != null or kw.excl_min_bool != null or
            kw.excl_max_bool != null)
        {
            acc = outsideAnd(acc, .unknown);
        }
        if (mask & typeBit(.object) == 0 and (kw.properties != null or kw.pattern_props != null or
            kw.required != null or kw.additional != null or kw.min_props != null or
            kw.max_props != null or kw.dep_required != null or kw.prop_names != null or
            kw.uneval_props != null))
        {
            acc = outsideAnd(acc, .unknown);
        }
        if (mask & typeBit(.array) == 0 and (kw.items != null or kw.prefix_items != null or
            kw.additional_items != null or kw.contains != null or kw.min_contains != null or
            kw.max_contains != null or kw.min_items != null or kw.max_items != null or
            kw.unique_items != null or kw.uneval_items != null))
        {
            acc = outsideAnd(acc, .unknown);
        }
        if (kw.not_) |nv| acc = outsideAnd(acc, outsideNeg(try self.outsideAcceptValue(nv, mask, visited)));
        if (kw.all_of) |av| {
            const items = switch (av.v) {
                .array => |it| it,
                else => return self.fail(error.InvalidSchema, av.offset, "allOf must be a non-empty array", .{}),
            };
            for (items) |it| acc = outsideAnd(acc, try self.outsideAcceptValue(it, mask, visited));
        }
        if (kw.any_of) |av| {
            const items = switch (av.v) {
                .array => |it| it,
                else => return self.fail(error.InvalidSchema, av.offset, "anyOf must be a non-empty array", .{}),
            };
            var u: Outside = .none;
            for (items) |it| u = outsideOr(u, try self.outsideAcceptValue(it, mask, visited));
            acc = outsideAnd(acc, u);
        }
        if (kw.one_of) |ov| {
            const items = switch (ov.v) {
                .array => |it| it,
                else => return self.fail(error.InvalidSchema, ov.offset, "oneOf must be a non-empty array", .{}),
            };
            var alls: usize = 0;
            var unknowns = false;
            for (items) |it| {
                switch (try self.outsideAcceptValue(it, mask, visited)) {
                    .all => alls += 1,
                    .none => {},
                    .unknown => unknowns = true,
                }
            }
            // No unknowns: every outside instance is accepted by exactly
            // the `.all` branches, so oneOf accepts all of them when
            // exactly one branch is `.all` and none otherwise.
            const u: Outside = if (unknowns)
                .unknown
            else if (alls == 1)
                .all
            else
                .none;
            acc = outsideAnd(acc, u);
        }
        if (kw.if_) |iv| {
            const iv_o = try self.outsideAcceptValue(iv, mask, visited);
            const then_o: Outside = if (kw.then_) |tv| try self.outsideAcceptValue(tv, mask, visited) else .all;
            const else_o: Outside = if (kw.else_) |ev| try self.outsideAcceptValue(ev, mask, visited) else .all;
            acc = outsideAnd(acc, outsideOr(outsideAnd(iv_o, then_o), outsideAnd(outsideNeg(iv_o), else_o)));
        }
        if (kw.dep_schemas) |dv| {
            const dpairs = switch (dv.v) {
                .object => |p| p,
                else => return self.fail(error.InvalidSchema, dv.offset, "dependentSchemas must be an object", .{}),
            };
            for (dpairs) |dp| {
                // An entry applies only when the trigger key is present, so
                // anything short of accept-all makes the outside unknown.
                if (try self.outsideAcceptValue(dp.value, mask, visited) != .all) acc = outsideAnd(acc, .unknown);
            }
        }
        if (kw.ref) |rv| {
            const s = switch (rv.v) {
                .string => |s| s,
                else => return self.fail(error.InvalidSchema, rv.offset, "$ref must be a string", .{}),
            };
            if (s.len == 0 or s[0] != '#') {
                acc = outsideAnd(acc, .unknown);
            } else {
                const path_len = self.path.items.len;
                defer self.path.shrinkRetainingCapacity(path_len);
                var target: *const json.Value = undefined;
                if (std.mem.eql(u8, s, "#")) {
                    target = self.resource_stack.items[self.resource_stack.items.len - 1];
                } else if (s.len >= 2 and s[1] == '/') {
                    target = try self.resolvePointer(s[1..], s, rv.offset);
                } else {
                    const aname = try self.percentDecode(s[1..], rv.offset);
                    target = self.lookupAnchor(aname) orelse
                        return self.fail(error.InvalidSchema, rv.offset, "unresolved $ref '{s}'", .{s});
                }
                var seen = false;
                for (visited.items) |p| {
                    if (p == target) seen = true;
                }
                if (seen) {
                    acc = outsideAnd(acc, .unknown);
                } else {
                    try visited.append(self.a, target);
                    defer _ = visited.pop();
                    acc = outsideAnd(acc, try self.outsideAcceptValue(target, mask, visited));
                }
            }
        }
        return acc;
    }

    /// The choice of the every-value arms of the kinds outside the mask.
    fn outsideArmNode(self: *Compiler, mask: u8, depth: u32) CompileError!grammar.NodeId {
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        inline for (std.meta.fields(SchemaType)) |fld| {
            const t: SchemaType = @enumFromInt(fld.value);
            if (t != .integer and mask & typeBit(t) == 0) {
                try arms.append(self.a, try self.anyTypeArm(t, depth));
            }
        }
        if (arms.items.len == 1) return arms.items[0];
        return self.addNode(try self.builder.alternativesNode(arms.items));
    }

    fn appendUniqueStr(self: *Compiler, list: *std.ArrayListUnmanaged([]const u8), s: []const u8) CompileError!void {
        for (list.items) |e| {
            if (std.mem.eql(u8, e, s)) return;
        }
        try list.append(self.a, s);
    }

    fn mergeEval(self: *Compiler, dst: *Eval, src: *const Eval) CompileError!void {
        dst.obj.all = dst.obj.all or src.obj.all;
        for (src.obj.props.items) |p| try self.appendUniqueStr(&dst.obj.props, p);
        for (src.obj.patterns.items) |p| try self.appendUniqueStr(&dst.obj.patterns, p);
        dst.arr.all = dst.arr.all or src.arr.all;
        dst.arr.prefix_len = @max(dst.arr.prefix_len, src.arr.prefix_len);
        for (src.arr.contains.items) |cv| {
            var dup = false;
            for (dst.arr.contains.items) |e| {
                if (e == cv) dup = true;
            }
            if (!dup) try dst.arr.contains.append(self.a, cv);
        }
    }

    /// The unconditional (core) evaluations of a schema: its own
    /// properties/patternProperties/additionalProperties and
    /// prefixItems/items/additionalItems/contains. Compile after the core
    /// conjunct (which validates forms); malformed shapes fail there first.
    fn coreEval(self: *Compiler, kw: Keywords, ev: *Eval) CompileError!void {
        if (kw.additional != null) ev.obj.all = true;
        if (kw.properties) |pv| {
            const ppairs = switch (pv.v) {
                .object => |p| p,
                else => return,
            };
            for (ppairs) |pp| try self.appendUniqueStr(&ev.obj.props, pp.key);
        }
        if (kw.pattern_props) |ppv| {
            const ppairs = switch (ppv.v) {
                .object => |p| p,
                else => return,
            };
            for (ppairs) |pp| try self.appendUniqueStr(&ev.obj.patterns, pp.key);
        }
        if (kw.items) |iv| {
            switch (iv.v) {
                // Tuple form (draft-04..2019-09): the prefix evaluates the
                // tuple positions, additionalItems everything beyond.
                .array => |items| {
                    ev.arr.prefix_len = @max(ev.arr.prefix_len, @as(u32, @intCast(items.len)));
                    if (kw.additional_items != null) ev.arr.all = true;
                },
                else => ev.arr.all = true,
            }
        }
        if (kw.prefix_items) |piv| {
            switch (piv.v) {
                .array => |items| ev.arr.prefix_len = @max(ev.arr.prefix_len, @as(u32, @intCast(items.len))),
                else => {},
            }
        }
        if (kw.contains) |cv| try ev.arr.contains.append(self.a, cv);
    }

    fn coreHasAssertions(kw: Keywords) bool {
        return kw.ty != null or kw.properties != null or kw.required != null or
            kw.additional != null or kw.items != null or kw.min_items != null or
            kw.max_items != null or kw.min_length != null or kw.max_length != null or
            kw.enum_ != null or kw.const_ != null or kw.pattern_props != null or
            kw.prop_names != null or kw.min_props != null or kw.max_props != null or
            kw.dependencies != null or kw.dep_required != null or
            kw.prefix_items != null or kw.additional_items != null or
            kw.contains != null or kw.min_contains != null or kw.max_contains != null or
            kw.unique_items != null or kw.pattern_ != null or kw.minimum != null or
            kw.maximum != null or kw.multiple_of != null or kw.excl_min != null or
            kw.excl_max != null;
    }

    /// The core conjunct of a scenario-bearing schema: the typed core plus
    /// the `not` part (which carries no evaluations). Null when the schema
    /// places no non-applicator constraint at all.
    fn compileUnevalCore(self: *Compiler, v: *const json.Value, kwc: Keywords, depth: u32) CompileError!?grammar.NodeId {
        var parts: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        if (coreHasAssertions(kwc)) {
            const saved_mask = self.top_mask;
            self.top_mask = self.uneval_mask;
            const core = self.compileTypedSpecCore(v, kwc, depth) catch |e| {
                self.top_mask = saved_mask;
                return e;
            };
            self.top_mask = saved_mask;
            try parts.append(self.a, core);
        }
        if (kwc.not_) |nv| {
            try self.path.append(self.a, "not");
            defer _ = self.path.pop();
            try parts.append(self.a, try self.compileComplement(nv, depth + 1));
        }
        if (parts.items.len == 0) return null;
        if (parts.items.len == 1) return parts.items[0];
        return try self.addNode(.{ .comb = .{
            .kind = .allof,
            .branches = try self.builder.copyNodeIds(parts.items),
        } });
    }

    /// The single node of a scenario list (the disjunction of the per-
    /// scenario conjunctions); the empty list is the empty language, a
    /// conjunct-free scenario makes the whole disjunction AnyJSON.
    fn nodeOfScenarios(self: *Compiler, scen: []const Scenario, depth: u32) CompileError!grammar.NodeId {
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        for (scen) |*sc| {
            var dead = false;
            for (sc.conjuncts.items) |id| {
                if (self.isEmptyNode(id)) dead = true;
            }
            if (dead) continue;
            if (sc.conjuncts.items.len == 0) return self.compileAnyJSON(depth);
            if (sc.conjuncts.items.len == 1) {
                try arms.append(self.a, sc.conjuncts.items[0]);
            } else {
                try arms.append(self.a, try self.addNode(.{ .comb = .{
                    .kind = .allof,
                    .branches = try self.builder.copyNodeIds(sc.conjuncts.items),
                } }));
            }
        }
        if (arms.items.len == 0) return self.emptyLangNode();
        if (arms.items.len == 1) return arms.items[0];
        return self.addNode(try self.builder.alternativesNode(arms.items));
    }

    fn cloneScenario(self: *Compiler, sc: *const Scenario) CompileError!Scenario {
        var out: Scenario = .{};
        try out.conjuncts.appendSlice(self.a, sc.conjuncts.items);
        try out.eval.obj.props.appendSlice(self.a, sc.eval.obj.props.items);
        try out.eval.obj.patterns.appendSlice(self.a, sc.eval.obj.patterns.items);
        try out.eval.arr.contains.appendSlice(self.a, sc.eval.arr.contains.items);
        out.eval.obj.all = sc.eval.obj.all;
        out.eval.arr.all = sc.eval.arr.all;
        out.eval.arr.prefix_len = sc.eval.arr.prefix_len;
        return out;
    }

    /// Cross product of two scenario lists (conjuncts concat, evals union),
    /// bounded by UNEVAL_SCENARIO_CAP.
    fn productScenarios(self: *Compiler, a: []const Scenario, b: []const Scenario) CompileError![]Scenario {
        var out: std.ArrayListUnmanaged(Scenario) = .{};
        for (a) |*sa| {
            for (b) |*sb| {
                var m = try self.cloneScenario(sa);
                try m.conjuncts.appendSlice(self.a, sb.conjuncts.items);
                try self.mergeEval(&m.eval, &sb.eval);
                try out.append(self.a, m);
                if (out.items.len > UNEVAL_SCENARIO_CAP) {
                    return self.fail(error.UnsupportedFeature, NO_OFFSET, "unevaluated* applicator combinations exceed the scenario budget of {d}", .{UNEVAL_SCENARIO_CAP});
                }
            }
        }
        return out.toOwnedSlice(self.a);
    }

    /// The acceptance scenarios of a subschema whose evaluations flow into
    /// an enclosing unevaluated* keyword. The list is exact: the disjunction
    /// of the per-scenario conjunctions is the schema's language, and a
    /// document accepted under several scenarios is covered by the scenario
    /// with the unioned eval set (anyOf enumerates every non-empty live
    /// subset; the other combinators are mutually exclusive).
    fn evalScenarios(self: *Compiler, v: *const json.Value, depth: u32) CompileError![]Scenario {
        const pairs = switch (v.v) {
            .boolean => |b| {
                if (!b) return &.{}; // never accepts: no scenarios
                const out = try self.a.alloc(Scenario, 1);
                out[0] = .{};
                return out;
            },
            .object => |p| p,
            else => return self.fail(error.InvalidSchema, v.offset, "schema must be an object", .{}),
        };
        const kw = try self.scanKeywords(pairs);
        return self.evalScenariosKw(v, kw, depth);
    }

    fn evalScenariosKw(self: *Compiler, v: *const json.Value, kw: Keywords, depth: u32) CompileError![]Scenario {
        if (depth > self.max_depth) {
            if (self.recursion_left < REF_UNROLL_CAP) return &.{}; // truncated expansion: no evals flow
            return self.fail(error.ResourceLimit, v.offset, "schema depth exceeds max_depth {d}", .{self.max_depth});
        }
        // P6b: dynamic/recursive references resolve against the evaluation
        // dynamic scope, which the scenario walk does not model - refuse
        // instead of silently mis-counting the evaluation set.
        if (kw.dyn_ref) |dv| {
            return self.fail(error.UnsupportedFeature, dv.offset, "$dynamicRef next to unevaluated* is not supported in spec-v1", .{});
        }
        if (kw.rec_ref) |rv| {
            return self.fail(error.UnsupportedFeature, rv.offset, "$recursiveRef next to unevaluated* is not supported in spec-v1", .{});
        }
        // A subschema with its own unevaluated* keyword evaluates every
        // property/element (of the keyword's container kind) of whatever it
        // accepts, and compiles whole; the other kind's unconditional
        // evaluations still flow out.
        if (kw.uneval_props != null or kw.uneval_items != null) {
            var ev: Eval = .{};
            ev.obj.all = kw.uneval_props != null;
            ev.arr.all = kw.uneval_items != null;
            try self.staticOtherEval(v, kw, &ev);
            const node = try self.compileSchema(v, depth);
            const out = try self.a.alloc(Scenario, 1);
            out[0] = .{};
            try out[0].conjuncts.append(self.a, node);
            out[0].eval = ev;
            return out;
        }
        for (self.uneval_visited.items) |p| {
            // A $ref cycle cut: the compile-side unroll budget bounds the
            // expansion; the eval fixed point adds nothing new here.
            if (p == v) {
                const out = try self.a.alloc(Scenario, 1);
                out[0] = .{};
                return out;
            }
        }
        try self.uneval_visited.append(self.a, v);
        defer _ = self.uneval_visited.pop();

        var scenarios: std.ArrayListUnmanaged(Scenario) = .{};
        var base: Scenario = .{};
        // The core: everything but the conditional in-place applicators and
        // $ref (dependentSchemas split below; `not` is a plain conjunct).
        var kwc = kw;
        kwc.all_of = null;
        kwc.any_of = null;
        kwc.one_of = null;
        kwc.if_ = null;
        kwc.then_ = null;
        kwc.else_ = null;
        kwc.ref = null;
        kwc.dep_schemas = null;
        if (try self.compileUnevalCore(v, kwc, depth)) |cn| {
            try base.conjuncts.append(self.a, cn);
        }
        try self.coreEval(kwc, &base.eval);
        try scenarios.append(self.a, base);
        var acc: []Scenario = scenarios.items;
        if (kw.ref) |rv| acc = try self.refScenarios(rv, acc, depth);
        if (acc.len == 0) return acc;
        if (kw.all_of) |av| acc = try self.allOfScenarios(av, acc, depth);
        if (acc.len == 0) return acc;
        if (kw.any_of) |av| acc = try self.anyOfScenarios(av, acc, depth);
        if (acc.len == 0) return acc;
        if (kw.one_of) |ov| acc = try self.oneOfScenarios(ov, acc, depth);
        if (acc.len == 0) return acc;
        if (kw.if_) |iv| acc = try self.ifElseScenarios(iv, kw.then_, kw.else_, acc, depth);
        if (acc.len == 0) return acc;
        if (kw.dep_schemas) |dv| acc = try self.depSchemaScenarios(dv, acc, depth);
        return acc;
    }

    /// The unconditional evaluations of the container kind NOT short-circuited
    /// by a nested unevaluated* keyword: core plus allOf branches and $ref
    /// targets (walked, nothing compiled). Conditional applicators
    /// (anyOf/oneOf/if/then/else, and dependentSchemas for objects) make the
    /// other kind's evaluation set dynamic, which this shortcut cannot
    /// transport: a documented refusal.
    fn staticOtherEval(self: *Compiler, v: *const json.Value, kw: Keywords, ev: *Eval) CompileError!void {
        _ = v;
        const need_obj = kw.uneval_props == null;
        if (kw.any_of != null or kw.one_of != null or kw.if_ != null or
            (need_obj and kw.dep_schemas != null))
        {
            return self.fail(error.UnsupportedFeature, NO_OFFSET, "conditional evaluation under a nested unevaluated* subschema is not supported in spec-v1", .{});
        }
        try self.coreEval(kw, ev);
        if (kw.all_of) |av| {
            const items = switch (av.v) {
                .array => |it| it,
                else => return, // the core conjunct compile reports the form
            };
            for (items) |it| {
                const pairs = switch (it.v) {
                    .object => |p| p,
                    else => continue,
                };
                const bkw = try self.scanKeywords(pairs);
                try self.staticOtherEval(it, bkw, ev);
            }
        }
        if (kw.ref) |rv| {
            const s = switch (rv.v) {
                .string => |s| s,
                else => return,
            };
            if (s.len == 0 or s[0] != '#') {
                return self.fail(error.UnsupportedFeature, rv.offset, "external $ref next to unevaluated* is not supported in spec-v1", .{});
            }
            var target: ?*const json.Value = null;
            const path_len = self.path.items.len;
            defer self.path.shrinkRetainingCapacity(path_len);
            if (std.mem.eql(u8, s, "#")) {
                target = self.resource_stack.items[self.resource_stack.items.len - 1];
            } else if (s.len >= 2 and s[1] == '/') {
                target = self.resolvePointer(s[1..], s, rv.offset) catch null;
            } else {
                const aname = self.percentDecode(s[1..], rv.offset) catch null;
                if (aname) |an| target = self.lookupAnchor(an);
            }
            if (target) |t| {
                for (self.uneval_visited.items) |p| {
                    if (p == t) return; // cycle: the fixed point adds nothing
                }
                try self.uneval_visited.append(self.a, t);
                defer _ = self.uneval_visited.pop();
                const pairs = switch (t.v) {
                    .object => |p| p,
                    else => return,
                };
                const tkw = try self.scanKeywords(pairs);
                try self.staticOtherEval(t, tkw, ev);
            }
        }
    }

    /// $ref as an in-place applicator: the target's scenarios product into
    /// the accumulator. A cycle cut conjoins the bounded expansion instead
    /// (the unroll budget owns the recursion); external refs are a
    /// documented refusal (their eval walk would cross registry documents).
    fn refScenarios(self: *Compiler, rv: *const json.Value, acc: []Scenario, depth: u32) CompileError![]Scenario {
        const s = switch (rv.v) {
            .string => |s| s,
            else => return self.fail(error.InvalidSchema, rv.offset, "$ref must be a string", .{}),
        };
        try self.path.append(self.a, "$ref");
        defer _ = self.path.pop();
        if (s.len == 0 or s[0] != '#') {
            return self.fail(error.UnsupportedFeature, rv.offset, "external $ref next to unevaluated* is not supported in spec-v1", .{});
        }
        const path_len = self.path.items.len;
        defer self.path.shrinkRetainingCapacity(path_len);
        var target: *const json.Value = undefined;
        if (std.mem.eql(u8, s, "#")) {
            target = self.resource_stack.items[self.resource_stack.items.len - 1];
        } else if (s.len >= 2 and s[1] == '/') {
            target = try self.resolvePointer(s[1..], s, rv.offset);
        } else {
            const aname = try self.percentDecode(s[1..], rv.offset);
            target = self.lookupAnchor(aname) orelse
                return self.fail(error.InvalidSchema, rv.offset, "unresolved $ref '{s}'", .{s});
        }
        for (self.uneval_visited.items) |p| {
            if (p == target) {
                // Cycle: conjoin the bounded expansion; the eval fixed point
                // of the target is already accounted at the outer level.
                const node = try self.compileRefOnly(rv, depth);
                const out = try self.a.alloc(Scenario, acc.len);
                for (acc, 0..) |*sa, i| {
                    out[i] = try self.cloneScenario(sa);
                    try out[i].conjuncts.append(self.a, node);
                }
                return out;
            }
        }
        const sub = try self.evalScenarios(target, depth + 1);
        return self.productScenarios(acc, sub);
    }

    /// Local-$ref resolution for the static budget guards below; mirrors
    /// refScenarios but never fails (the failing forms error out on the
    /// real compile path). Returns null for external or unresolvable refs.
    fn resolveLocalRef(self: *Compiler, rv: *const json.Value) ?*const json.Value {
        const s = switch (rv.v) {
            .string => |s| s,
            else => return null,
        };
        if (s.len == 0 or s[0] != '#') return null;
        const path_len = self.path.items.len;
        defer self.path.shrinkRetainingCapacity(path_len);
        if (std.mem.eql(u8, s, "#")) {
            return self.resource_stack.items[self.resource_stack.items.len - 1];
        } else if (s.len >= 2 and s[1] == '/') {
            return self.resolvePointer(s[1..], s, rv.offset) catch null;
        } else {
            const aname = self.percentDecode(s[1..], rv.offset) catch return null;
            return self.lookupAnchor(aname);
        }
    }

    /// Runtime-budget guard (ADR-0009): a oneOf/anyOf branch that itself
    /// involves a combinator - directly, or through the in-place applicators
    /// allOf/if/then/else/dependentSchemas and local $refs - nests oneOf-comb
    /// disjuncts inside the enclosing comb, which multiplies the live parser
    /// threads past MAX_THREADS_CAP at accept time. Refused at compile time
    /// instead. Subschemas behind property/items boundaries are NOT walked:
    /// they parse at a value position of their own and do not widen the
    /// enclosing walk.
    fn branchInvolvesCombinator(self: *Compiler, v: *const json.Value, visited: *std.ArrayListUnmanaged(*const json.Value)) CompileError!bool {
        const pairs = switch (v.v) {
            .object => |p| p,
            else => return false,
        };
        const kw = try self.scanKeywords(pairs);
        if (kw.one_of != null or kw.any_of != null) return true;
        for (visited.items) |p| {
            if (p == v) return false;
        }
        try visited.append(self.a, v);
        if (kw.all_of) |av| {
            switch (av.v) {
                .array => |items| {
                    for (items) |it| {
                        if (try self.branchInvolvesCombinator(it, visited)) return true;
                    }
                },
                else => {},
            }
        }
        const conds = [_]?*const json.Value{ kw.if_, kw.then_, kw.else_ };
        for (conds) |cv| {
            if (cv) |c| {
                if (try self.branchInvolvesCombinator(c, visited)) return true;
            }
        }
        if (kw.dep_schemas) |dv| {
            switch (dv.v) {
                .object => |dpairs| {
                    for (dpairs) |dp| {
                        if (try self.branchInvolvesCombinator(dp.value, visited)) return true;
                    }
                },
                else => {},
            }
        }
        if (kw.ref) |rv| {
            if (self.resolveLocalRef(rv)) |t| {
                if (try self.branchInvolvesCombinator(t, visited)) return true;
            }
        }
        return false;
    }

    /// Runtime-budget guard (ADR-0009): every conjunctive `contains` (local,
    /// allOf branches, local $ref targets) spawns its own live automaton
    /// alongside the unevaluatedItems guard; two or more exceed the parser
    /// thread cap at accept time, so the combination is refused at compile
    /// time. `contains` under conditional applicators (if/then/else,
    /// anyOf/oneOf) is not conjunctive and does not count.
    fn adjacentContainsCount(self: *Compiler, v: *const json.Value, visited: *std.ArrayListUnmanaged(*const json.Value)) CompileError!usize {
        const pairs = switch (v.v) {
            .object => |p| p,
            else => return 0,
        };
        for (visited.items) |p| {
            if (p == v) return 0;
        }
        try visited.append(self.a, v);
        const kw = try self.scanKeywords(pairs);
        var n: usize = if (kw.contains != null) 1 else 0;
        if (kw.all_of) |av| {
            switch (av.v) {
                .array => |items| {
                    for (items) |it| n += try self.adjacentContainsCount(it, visited);
                },
                else => {},
            }
        }
        if (kw.ref) |rv| {
            if (self.resolveLocalRef(rv)) |t| {
                n += try self.adjacentContainsCount(t, visited);
            }
        }
        return n;
    }

    fn combBranchItems(self: *Compiler, av: *const json.Value, comptime name: []const u8) CompileError![]*json.Value {
        const items = switch (av.v) {
            .array => |it| it,
            else => return self.fail(error.InvalidSchema, av.offset, name ++ " must be a non-empty array", .{}),
        };
        if (items.len == 0) {
            return self.fail(error.InvalidSchema, av.offset, name ++ " must be a non-empty array", .{});
        }
        if (items.len > 64) {
            return self.fail(error.ResourceLimit, av.offset, name ++ " has more than 64 branches", .{});
        }
        return items;
    }

    /// allOf: every branch accepts, so its scenarios product in.
    fn allOfScenarios(self: *Compiler, av: *const json.Value, acc: []Scenario, depth: u32) CompileError![]Scenario {
        const items = try self.combBranchItems(av, "allOf");
        var accm = acc;
        for (items, 0..) |it, i| {
            try self.path.append(self.a, "allOf");
            try self.path.append(self.a, try std.fmt.allocPrint(self.a, "{d}", .{i}));
            defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
            const sub = try self.evalScenarios(it, depth + 1);
            if (sub.len == 0) return &.{}; // a dead branch empties the conjunction
            accm = try self.productScenarios(accm, sub);
        }
        return accm;
    }

    /// anyOf: the accepted-language union over every non-empty subset of
    /// live branches; the subset's eval set is the union of its branches'
    /// (a document matched by several branches is covered by their joint
    /// subset scenario). Capped at 5 live branches (31 subsets).
    fn anyOfScenarios(self: *Compiler, av: *const json.Value, acc: []Scenario, depth: u32) CompileError![]Scenario {
        const items = try self.combBranchItems(av, "anyOf");
        var subs: std.ArrayListUnmanaged([]Scenario) = .{};
        for (items, 0..) |it, i| {
            try self.path.append(self.a, "anyOf");
            try self.path.append(self.a, try std.fmt.allocPrint(self.a, "{d}", .{i}));
            defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
            var comb_visited: std.ArrayListUnmanaged(*const json.Value) = .{};
            if (try self.branchInvolvesCombinator(it, &comb_visited)) {
                return self.fail(error.UnsupportedFeature, it.offset, "unevaluated* over an anyOf branch that nests another combinator exceeds the runtime thread budget in spec-v1", .{});
            }
            const sub = try self.evalScenarios(it, depth + 1);
            if (sub.len > 0) try subs.append(self.a, sub);
        }
        if (subs.items.len == 0) return &.{};
        if (subs.items.len > 5) {
            return self.fail(error.UnsupportedFeature, av.offset, "unevaluated* over anyOf with more than 5 live branches is not supported in spec-v1", .{});
        }
        const n = subs.items.len;
        var variants: std.ArrayListUnmanaged(Scenario) = .{};
        var mask: u32 = 1;
        while (mask < (@as(u32, 1) << @intCast(n))) : (mask += 1) {
            var combo: []const Scenario = &[1]Scenario{.{}}; // single empty scenario
            var bit: u5 = 0;
            while (bit < n) : (bit += 1) {
                if (mask & (@as(u32, 1) << bit) != 0) {
                    combo = try self.productScenarios(combo, subs.items[bit]);
                }
            }
            try variants.appendSlice(self.a, combo);
            if (variants.items.len > UNEVAL_SCENARIO_CAP) {
                return self.fail(error.UnsupportedFeature, av.offset, "unevaluated* applicator combinations exceed the scenario budget of {d}", .{UNEVAL_SCENARIO_CAP});
            }
        }
        return self.productScenarios(acc, variants.items);
    }

    /// oneOf: the exactly-one comb conjoins every scenario; the eval flow
    /// comes from the single accepting branch, so each branch's scenarios
    /// are one variant (mutually exclusive by the comb's verdict).
    fn oneOfScenarios(self: *Compiler, ov: *const json.Value, acc: []Scenario, depth: u32) CompileError![]Scenario {
        const items = try self.combBranchItems(ov, "oneOf");
        try self.path.append(self.a, "oneOf");
        defer _ = self.path.pop();
        const one_node = try self.compileOneOf(ov, depth);
        var variants: std.ArrayListUnmanaged(Scenario) = .{};
        for (items, 0..) |it, i| {
            try self.path.append(self.a, try std.fmt.allocPrint(self.a, "{d}", .{i}));
            defer _ = self.path.pop();
            var comb_visited: std.ArrayListUnmanaged(*const json.Value) = .{};
            if (try self.branchInvolvesCombinator(it, &comb_visited)) {
                return self.fail(error.UnsupportedFeature, it.offset, "unevaluated* over a oneOf branch that nests another combinator exceeds the runtime thread budget in spec-v1", .{});
            }
            const sub = try self.evalScenarios(it, depth + 1);
            try variants.appendSlice(self.a, sub);
            if (variants.items.len > UNEVAL_SCENARIO_CAP) {
                return self.fail(error.UnsupportedFeature, ov.offset, "unevaluated* applicator combinations exceed the scenario budget of {d}", .{UNEVAL_SCENARIO_CAP});
            }
        }
        if (variants.items.len == 0) return &.{};
        const out = try self.a.alloc(Scenario, acc.len);
        for (acc, 0..) |*sa, i| {
            out[i] = try self.cloneScenario(sa);
            try out[i].conjuncts.append(self.a, one_node);
        }
        return self.productScenarios(out, variants.items);
    }

    /// if/then/else (and a bare if): the success side is the product of the
    /// if- and then-scenarios (their evals flow when if accepts), the
    /// failure side is ¬if (an ifelse comb whose then slot is the empty
    /// language) conjoined with the else-scenarios.
    fn ifElseScenarios(self: *Compiler, iv: *const json.Value, thenv: ?*const json.Value, elsev: ?*const json.Value, acc: []Scenario, depth: u32) CompileError![]Scenario {
        try self.path.append(self.a, "if");
        const if_scen = try self.evalScenarios(iv, depth + 1);
        _ = self.path.pop();
        const if_node = try self.nodeOfScenarios(if_scen, depth + 1);
        var success = if_scen;
        if (thenv) |tv| {
            try self.path.append(self.a, "then");
            const ts = try self.evalScenarios(tv, depth + 1);
            _ = self.path.pop();
            success = try self.productScenarios(success, ts);
        }
        var fail_scen: []Scenario = try self.a.alloc(Scenario, 1);
        fail_scen[0] = .{}; // no else clause: the failed-if side is vacuous
        if (elsev) |ev| {
            try self.path.append(self.a, "else");
            fail_scen = try self.evalScenarios(ev, depth + 1);
            _ = self.path.pop();
        }
        var variants: std.ArrayListUnmanaged(Scenario) = .{};
        try variants.appendSlice(self.a, success);
        if (fail_scen.len > 0) {
            // ¬if: the ifelse verdict (if /\ empty) \/ (~if /\ AnyJSON).
            const not_if = if (self.isEmptyNode(if_node))
                try self.maskedAnyNode(depth + 1)
            else blk: {
                const branches = try self.a.alloc(grammar.NodeId, 3);
                branches[0] = if_node;
                branches[1] = try self.emptyLangNode();
                branches[2] = try self.maskedAnyNode(depth + 1);
                break :blk try self.addNode(.{ .comb = .{ .kind = .ifelse, .branches = branches } });
            };
            for (fail_scen) |*fs| {
                var v2 = try self.cloneScenario(fs);
                try v2.conjuncts.append(self.a, not_if);
                try variants.append(self.a, v2);
            }
        }
        if (variants.items.len == 0) return &.{};
        if (variants.items.len > UNEVAL_SCENARIO_CAP) {
            return self.fail(error.UnsupportedFeature, iv.offset, "unevaluated* applicator combinations exceed the scenario budget of {d}", .{UNEVAL_SCENARIO_CAP});
        }
        return self.productScenarios(acc, variants.items);
    }

    /// dependentSchemas next to unevaluatedProperties: each entry splits
    /// every scenario into the trigger-present side (the dependent schema
    /// conjoins, its evals flow) and the trigger-absent side (a ban object
    /// rejects the key). Both wrappers pass non-object instances through
    /// (the keyword is inert off objects). Capped at 4 entries.
    fn depSchemaScenarios(self: *Compiler, dv: *const json.Value, acc: []Scenario, depth: u32) CompileError![]Scenario {
        const dpairs = switch (dv.v) {
            .object => |p| p,
            else => return self.fail(error.InvalidSchema, dv.offset, "dependentSchemas must be an object", .{}),
        };
        if (dpairs.len > 4) {
            return self.fail(error.UnsupportedFeature, dv.offset, "unevaluated* over dependentSchemas with more than 4 entries is not supported in spec-v1", .{});
        }
        var accm = acc;
        for (dpairs) |dp| {
            try self.path.append(self.a, "dependentSchemas");
            try self.path.append(self.a, dp.key);
            defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
            switch (dp.value.v) {
                .boolean => |b| {
                    if (b) continue; // vacuous
                    const ban = try self.banKeyNode(dp.key, depth);
                    for (accm) |*sa| try sa.conjuncts.append(self.a, ban);
                },
                .object => |op| {
                    if (op.len == 0) continue; // {} is vacuous
                    const sub = try self.evalScenarios(dp.value, depth + 1);
                    const req = try self.reqKeyNode(dp.key, depth);
                    const ban = try self.banKeyNode(dp.key, depth);
                    if (sub.len == 0) {
                        // The dependent schema can never accept: the trigger
                        // is banned outright.
                        for (accm) |*sa| try sa.conjuncts.append(self.a, ban);
                        continue;
                    }
                    var out: std.ArrayListUnmanaged(Scenario) = .{};
                    for (accm) |*sa| {
                        var without = try self.cloneScenario(sa);
                        try without.conjuncts.append(self.a, ban);
                        try out.append(self.a, without);
                        for (sub) |*sb| {
                            var with = try self.cloneScenario(sa);
                            try with.conjuncts.append(self.a, req);
                            try with.conjuncts.appendSlice(self.a, sb.conjuncts.items);
                            try self.mergeEval(&with.eval, &sb.eval);
                            try out.append(self.a, with);
                            if (out.items.len > UNEVAL_SCENARIO_CAP) {
                                return self.fail(error.UnsupportedFeature, dv.offset, "unevaluated* applicator combinations exceed the scenario budget of {d}", .{UNEVAL_SCENARIO_CAP});
                            }
                        }
                    }
                    accm = try out.toOwnedSlice(self.a);
                },
                else => return self.fail(error.InvalidSchema, dp.value.offset, "dependentSchemas entries must be schemas", .{}),
            }
        }
        return accm;
    }

    /// Choice of a node with the every-value arms of the other container
    /// kinds: the wrapper passes instances the wrapped keyword is inert for.
    fn otherKindsWrap(self: *Compiler, node: grammar.NodeId, comptime skip: SchemaType, depth: u32) CompileError!grammar.NodeId {
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        try arms.append(self.a, node);
        inline for (std.meta.fields(SchemaType)) |fld| {
            const t: SchemaType = @enumFromInt(fld.value);
            if (t != skip and t != .integer) {
                // num_v already covers integer spellings.
                try arms.append(self.a, try self.anyTypeArm(t, depth));
            }
        }
        return self.addNode(try self.builder.alternativesNode(arms.items));
    }

    /// Objects carrying the key (AnyJSON values), non-objects pass.
    fn reqKeyNode(self: *Compiler, name: []const u8, depth: u32) CompileError!grammar.NodeId {
        const extra = try self.a.alloc(grammar.Literal, 1);
        extra[0] = try self.rawKeyLit(name);
        const obj = try self.addNode(.{
            .open_obj = .{
                .props = &.{},
                .extra_required = extra,
                .value = try self.compileAnyJSON(depth + 1),
                .track_keys = true, // extra_required is verified against the seen set
            },
        });
        if (self.uneval_mask == typeBit(.object)) return obj; // outside kinds: the walk's top-level arm
        return self.otherKindsWrap(obj, .object, depth);
    }

    /// Objects without the key (the ban dependency rejects it at dispatch),
    /// non-objects pass.
    fn banKeyNode(self: *Compiler, name: []const u8, depth: u32) CompileError!grammar.NodeId {
        const deps = try self.a.alloc(grammar.Dep, 1);
        deps[0] = .{ .trigger = try self.rawKeyLit(name), .kind = .ban };
        const obj = try self.addNode(.{ .open_obj = .{
            .props = &.{},
            .extra_required = &.{},
            .value = try self.compileAnyJSON(depth + 1),
            .deps = deps,
            .track_keys = true,
        } });
        if (self.uneval_mask == typeBit(.object)) return obj;
        return self.otherKindsWrap(obj, .object, depth);
    }

    fn litBytesEq(self: *Compiler, a: grammar.Literal, b: grammar.Literal) bool {
        const pa = self.builder.pool.items[a.off..][0..a.len];
        const pb = self.builder.pool.items[b.off..][0..b.len];
        return std.mem.eql(u8, pa, pb);
    }

    /// extra_required entry vs. the merged property set (mergeOpenObj): a
    /// required name that is also declared joins the declared prop as
    /// required; otherwise it stays an undeclared required key. Prop keys
    /// are the `"name":` dispatch literals, extra entries the raw name.
    fn absorbExtraReq(self: *Compiler, xl: grammar.Literal, props: []grammar.Prop, extra: *std.ArrayListUnmanaged(grammar.Literal)) CompileError!void {
        const rb = self.builder.pool.items[xl.off..][0..xl.len];
        for (props) |*p| {
            if (p.key.len != xl.len + 3) continue;
            const kb = self.builder.pool.items[p.key.off..][0..p.key.len];
            if (std.mem.eql(u8, kb[1 .. kb.len - 2], rb)) {
                p.required = true;
                return;
            }
        }
        try extra.append(self.a, xl);
    }

    /// Intersection of two value schemas inside a merged object guard
    /// (ADR-0009): the AnyJSON side absorbs, the empty language dominates,
    /// everything else is an allof pair.
    fn conjValues(self: *Compiler, a: grammar.NodeId, b: grammar.NodeId) CompileError!grammar.NodeId {
        if (a == b) return a;
        if (self.any_nodes.contains(a)) return b;
        if (self.any_nodes.contains(b)) return a;
        if (self.isEmptyNode(a)) return a;
        if (self.isEmptyNode(b)) return b;
        const br = try self.a.alloc(grammar.NodeId, 2);
        br[0] = a;
        br[1] = b;
        return self.addNode(.{ .comb = .{ .kind = .allof, .branches = br } });
    }

    /// Merge two open_obj nodes into their conjunction object (ADR-0009):
    /// property sets union in first-appearance order (semantics-spec-v1
    /// 4.3), per-key values and the undeclared-value schemas intersect,
    /// required sets and dependencies concatenate. Null when the shapes
    /// cannot merge safely: propertyNames, forbidden names, or a pattern
    /// entry on one side with declared keys on the other (the dispatch
    /// overlap is outside the merged-object order contract).
    fn mergeOpenObj(self: *Compiler, a_id: grammar.NodeId, b_id: grammar.NodeId) CompileError!?grammar.NodeId {
        const an = switch (self.builder.nodes.items[a_id]) {
            .open_obj => |*o| o.*,
            else => return null,
        };
        const bn = switch (self.builder.nodes.items[b_id]) {
            .open_obj => |*o| o.*,
            else => return null,
        };
        if (an.names_forbidden or bn.names_forbidden) return null;
        if (an.prop_names != null or bn.prop_names != null) return null;
        const a_pat = an.pattern_lit != null or an.pattern_dfa != null;
        const b_pat = bn.pattern_lit != null or bn.pattern_dfa != null;
        if (a_pat and b_pat) return null;
        if (a_pat and bn.props.len > 0) return null;
        if (b_pat and an.props.len > 0) return null;
        var props: std.ArrayListUnmanaged(grammar.Prop) = .{};
        for (an.props) |ap| {
            var value = try self.conjValues(ap.value, bn.value);
            var req = ap.required;
            for (bn.props) |bp| {
                if (self.litBytesEq(ap.key, bp.key)) {
                    value = try self.conjValues(ap.value, bp.value);
                    req = req or bp.required;
                }
            }
            try props.append(self.a, .{ .key = ap.key, .value = value, .required = req });
        }
        for (bn.props) |bp| {
            var dup = false;
            for (an.props) |ap| {
                if (self.litBytesEq(ap.key, bp.key)) dup = true;
            }
            if (!dup) {
                try props.append(self.a, .{
                    .key = bp.key,
                    .value = try self.conjValues(bp.value, an.value),
                    .required = bp.required,
                });
            }
        }
        // A name required by one side but declared by the other is a
        // required declared prop of the merge, not an extra_required entry:
        // extra_required holds only names absent from props (the parser
        // verifies them against the seen set, which a declared key enters
        // only under track_keys).
        var extra: std.ArrayListUnmanaged(grammar.Literal) = .{};
        for (an.extra_required) |xl| try self.absorbExtraReq(xl, props.items, &extra);
        for (bn.extra_required) |xl| try self.absorbExtraReq(xl, props.items, &extra);
        var deps: std.ArrayListUnmanaged(grammar.Dep) = .{};
        try deps.appendSlice(self.a, an.deps);
        try deps.appendSlice(self.a, bn.deps);
        var pattern_lit = an.pattern_lit;
        var pattern_dfa = an.pattern_dfa;
        var pattern_value = an.pattern_value;
        if (b_pat) {
            pattern_lit = bn.pattern_lit;
            pattern_dfa = bn.pattern_dfa;
            pattern_value = bn.pattern_value;
        }
        if (a_pat or b_pat) {
            const other_value = if (a_pat) bn.value else an.value;
            pattern_value = try self.conjValues(pattern_value, other_value);
        }
        return try self.addNode(.{ .open_obj = .{
            .props = try self.builder.copyProps(props.items),
            .extra_required = try self.builder.copyLiterals(extra.items),
            .value = try self.conjValues(an.value, bn.value),
            .min_props = @max(an.min_props, bn.min_props),
            .max_props = @min(an.max_props, bn.max_props),
            .track_keys = an.track_keys or bn.track_keys,
            .capture = an.capture or bn.capture,
            .deps = try self.builder.copyDeps(deps.items),
            .key_min_len = @max(an.key_min_len, bn.key_min_len),
            .key_max_len = @min(an.key_max_len, bn.key_max_len),
            .pattern_lit = pattern_lit,
            .pattern_value = pattern_value,
            .pattern_dfa = pattern_dfa,
            .ann_slots = @max(an.ann_slots, bn.ann_slots),
        } });
    }

    /// The object-side guard of one scenario: every instance key is either
    /// statically evaluated (declared name or a union of the contributing
    /// patternProperties patterns, AnyJSON value) or must satisfy the
    /// unevaluatedProperties schema. The node is bare (object-only); the
    /// caller wraps or merges it for the outside kinds.
    fn guardObjNode(self: *Compiler, ev: *const ObjEval, up_node: grammar.NodeId, any_node: grammar.NodeId, depth: u32) CompileError!grammar.NodeId {
        _ = depth;
        var props: std.ArrayListUnmanaged(grammar.Prop) = .{};
        for (ev.props.items) |name| {
            var lit: std.ArrayListUnmanaged(u8) = .{};
            try lit.append(self.a, '"');
            try appendEscaped(&lit, self.a, name);
            try lit.appendSlice(self.a, "\":");
            try props.append(self.a, .{
                .key = try self.builder.addLiteral(lit.items),
                .value = any_node,
                .required = false,
            });
        }
        var pd: ?*const pattern.Dfa = null;
        if (ev.patterns.items.len > UNEVAL_PATTERN_CAP) {
            return self.fail(error.UnsupportedFeature, NO_OFFSET, "more than {d} patternProperties patterns feed one unevaluatedProperties guard", .{UNEVAL_PATTERN_CAP});
        }
        if (ev.patterns.items.len == 1) {
            pd = try self.compilePatternText(ev.patterns.items[0], NO_OFFSET);
        } else if (ev.patterns.items.len > 1) {
            var union_src: std.ArrayListUnmanaged(u8) = .{};
            for (ev.patterns.items, 0..) |p, i| {
                if (i > 0) try union_src.append(self.a, '|');
                try union_src.appendSlice(self.a, "(?:");
                try union_src.appendSlice(self.a, p);
                try union_src.appendSlice(self.a, ")");
            }
            pd = try self.compilePatternText(union_src.items, NO_OFFSET);
        }
        const obj = try self.addNode(.{ .open_obj = .{
            .props = try self.builder.copyProps(props.items),
            .extra_required = &.{},
            .value = up_node,
            .pattern_dfa = pd,
            .pattern_value = any_node,
        } });
        return obj;
    }

    /// The array-side guard of one scenario: the statically evaluated prefix
    /// takes AnyJSON; every later element must satisfy the union of the
    /// contributing contains schemas and the unevaluatedItems schema (an
    /// element is evaluated exactly when some contains matched it).
    /// The node is bare (array-only); the caller wraps it for the outside
    /// kinds.
    fn guardArrNode(self: *Compiler, ev: *const ArrEval, ui_node: grammar.NodeId, any_node: grammar.NodeId, depth: u32) CompileError!grammar.NodeId {
        var item = ui_node;
        if (ev.contains.items.len > 0) {
            var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
            for (ev.contains.items) |cv| {
                try self.path.append(self.a, "contains");
                const cn = try self.compileSchema(cv, depth + 1);
                _ = self.path.pop();
                if (self.isEmptyNode(cn)) continue; // can never match: no evals
                try arms.append(self.a, cn);
            }
            if (!self.isEmptyNode(ui_node)) try arms.append(self.a, ui_node);
            if (arms.items.len == 0) {
                item = try self.emptyLangNode();
            } else if (arms.items.len == 1) {
                item = arms.items[0];
            } else {
                item = try self.addNode(try self.builder.alternativesNode(arms.items));
            }
        }
        const prefix = try self.a.alloc(grammar.NodeId, ev.prefix_len);
        for (prefix) |*p| p.* = any_node;
        var hi = grammar.UNBOUNDED;
        if (self.isEmptyNode(item)) hi = ev.prefix_len;
        const rep = try self.addNode(.{ .repeat = .{
            .item = item,
            .min = 0,
            .max = hi,
            .prefix = prefix,
        } });
        return rep;
    }

    /// The unevaluated* keyword schema, compiled once per compileUnevaluated
    /// and shared by every scenario guard. `true` is reduced away before
    /// this point; `false` is the empty-language node.
    fn compileUnevalSchema(self: *Compiler, uv: *const json.Value, comptime name: []const u8, depth: u32) CompileError!grammar.NodeId {
        switch (uv.v) {
            .boolean => |b| {
                if (b) return self.compileAnyJSON(depth + 1);
                return self.emptyLangNode();
            },
            else => {
                try self.path.append(self.a, name);
                defer _ = self.path.pop();
                return self.compileSchema(uv, depth + 1);
            },
        }
    }

    /// Any-value node scoped to the active unevaluated* mask (the outside
    /// kinds are covered by the scenario walk's top-level arm); plain
    /// AnyJSON outside a walk.
    fn maskedAnyNode(self: *Compiler, depth: u32) CompileError!grammar.NodeId {
        if (self.uneval_mask == 0) return self.compileAnyJSON(depth);
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        inline for (std.meta.fields(SchemaType)) |fld| {
            const t: SchemaType = @enumFromInt(fld.value);
            if (t != .integer and self.uneval_mask & typeBit(t) != 0) {
                try arms.append(self.a, try self.anyTypeArm(t, depth));
            }
        }
        if (arms.items.len == 1) return arms.items[0];
        return self.addNode(try self.builder.alternativesNode(arms.items));
    }

    /// True when same-schema array keywords already evaluate every element,
    /// leaving unevaluatedItems with nothing to govern: schema-form items
    /// (any dialect), tuple-form items (2019-09) completed by
    /// additionalItems, or prefixItems completed by items (2020-12).
    fn coreItemsEvaluateAll(self: *Compiler, kw: Keywords) bool {
        _ = self;
        if (kw.items) |iv| {
            if (iv.v != .array) return true;
            if (kw.additional_items != null) return true;
        }
        if (kw.prefix_items != null and kw.items != null) return true;
        return false;
    }

    /// Entry point of the unevaluated* compilation (compileSchema routes
    /// here whenever either keyword is present, spec-v1, 2019-09/2020-12).
    fn compileUnevaluated(self: *Compiler, v: *const json.Value, kw: Keywords, depth: u32) CompileError!grammar.NodeId {
        // P6b: dynamic/recursive references resolve against the evaluation
        // dynamic scope, which the scenario walk does not model - a
        // documented refusal with a pointer (mirrors the external-$ref one).
        if (kw.dyn_ref) |dv| {
            return self.fail(error.UnsupportedFeature, dv.offset, "$dynamicRef next to unevaluated* is not supported in spec-v1", .{});
        }
        if (kw.rec_ref) |rv| {
            return self.fail(error.UnsupportedFeature, rv.offset, "$recursiveRef next to unevaluated* is not supported in spec-v1", .{});
        }
        var up = kw.uneval_props;
        var ui = kw.uneval_items;
        // `true` evaluates everything and constrains nothing: inert.
        if (up) |x| {
            if (x.v == .boolean and x.v.boolean) up = null;
        }
        if (ui) |x| {
            if (x.v == .boolean and x.v.boolean) ui = null;
        }
        // A same-schema additionalProperties evaluates every property not
        // covered by properties/patternProperties, and a same-schema
        // schema-form items (or tuple items with additionalItems) every
        // element: the unevaluated keyword has nothing left to govern.
        if (up != null and kw.additional != null) up = null;
        if (ui != null and self.coreItemsEvaluateAll(kw)) ui = null;
        var kw2 = kw;
        kw2.uneval_props = null;
        kw2.uneval_items = null;
        if (up == null and ui == null) {
            if (kw2.ref) |rv| return self.compileRef(v, kw2, rv, depth);
            return self.compileTypedSpec(v, kw2, depth);
        }
        const has_applicators = kw.all_of != null or kw.any_of != null or kw.one_of != null or
            kw.if_ != null or kw.ref != null or (up != null and kw.dep_schemas != null) or
            kw.enum_ != null or kw.const_ != null;
        if (!has_applicators) {
            // Fold path: unevaluated* take the additionalProperties/items
            // role inside the object/array arm itself (compileObjectSpec /
            // compileUnevalRest); `not` carries no evaluations and conjoins.
            var kwf = kw2;
            kwf.uneval_props = up;
            kwf.uneval_items = ui;
            var parts: std.ArrayListUnmanaged(grammar.NodeId) = .{};
            try parts.append(self.a, try self.compileTypedSpecCore(v, kwf, depth));
            if (kwf.not_) |nv| {
                try self.path.append(self.a, "not");
                defer _ = self.path.pop();
                try parts.append(self.a, try self.compileComplement(nv, depth + 1));
            }
            for (parts.items) |id| {
                if (self.isEmptyNode(id)) {
                    return self.emptySubschema(depth, v.offset, "the conjunction defines an empty language", .{});
                }
            }
            if (parts.items.len == 1) return parts.items[0];
            return self.addNode(.{ .comb = .{
                .kind = .allof,
                .branches = try self.builder.copyNodeIds(parts.items),
            } });
        }
        return self.compileUnevalScenarios(v, kw2, up, ui, depth);
    }

    fn compileUnevalScenarios(self: *Compiler, v: *const json.Value, kw2: Keywords, up: ?*const json.Value, ui: ?*const json.Value, depth: u32) CompileError!grammar.NodeId {
        if (ui != null) {
            var contains_visited: std.ArrayListUnmanaged(*const json.Value) = .{};
            if (try self.adjacentContainsCount(v, &contains_visited) >= 2) {
                return self.fail(error.UnsupportedFeature, v.offset, "unevaluatedItems next to several conjunctive `contains` subschemas exceeds the runtime thread budget in spec-v1", .{});
            }
        }
        const up_node = if (up) |uv| try self.compileUnevalSchema(uv, "unevaluatedProperties", depth) else null;
        const ui_node = if (ui) |uv| try self.compileUnevalSchema(uv, "unevaluatedItems", depth) else null;
        const any_node = try self.compileAnyJSON(depth + 1);
        // Container masking (ADR-0009): when the schema's behavior outside
        // the guard kinds is analyzable (accept-all or accept-none), the
        // cores and combinator branches of the scenario walk compile
        // restricted to the mask kinds and one top-level arm covers the
        // outside. Otherwise the walk compiles fully wrapped.
        var want_mask: u8 = 0;
        if (up != null) want_mask |= typeBit(.object);
        if (ui != null) want_mask |= typeBit(.array);
        var visited_out: std.ArrayListUnmanaged(*const json.Value) = .{};
        const outside = self.outsideAccept(v, kw2, want_mask, &visited_out) catch .unknown;
        var out_arm: ?grammar.NodeId = null;
        const saved_uneval_mask = self.uneval_mask;
        const saved_top_mask = self.top_mask;
        defer {
            self.uneval_mask = saved_uneval_mask;
            self.top_mask = saved_top_mask;
        }
        if (outside != .unknown) {
            self.uneval_mask = want_mask;
            self.top_mask = 0;
            if (outside == .all) out_arm = try self.outsideArmNode(want_mask, depth);
        }
        const scenarios = try self.evalScenariosKw(v, kw2, depth);
        // Scenarios carrying an empty-language conjunct can never accept.
        var live: std.ArrayListUnmanaged(*const Scenario) = .{};
        for (scenarios) |*sc| {
            var dead = false;
            for (sc.conjuncts.items) |id| {
                if (self.isEmptyNode(id)) dead = true;
            }
            if (!dead) try live.append(self.a, sc);
        }
        if (live.items.len == 0) {
            return self.emptySubschema(depth, v.offset, "no unevaluated* applicator combination can accept any value", .{});
        }
        // (C1 /\ G) \/ (C2 /\ G) = (C1 \/ C2) /\ G: scenarios with an
        // identical evaluation set share one guard, which keeps the spawned
        // thread count under the parser cap (anyOf subsets collapse to a
        // single group because a plain `required` branch evaluates nothing).
        var groups: std.ArrayListUnmanaged(std.ArrayListUnmanaged(*const Scenario)) = .{};
        outer: for (live.items) |sc| {
            for (groups.items) |*g| {
                if (evalEq(&g.items[0].eval, &sc.eval)) {
                    try g.append(self.a, sc);
                    continue :outer;
                }
            }
            var g: std.ArrayListUnmanaged(*const Scenario) = .{};
            try g.append(self.a, sc);
            try groups.append(self.a, g);
        }
        // Conjuncts shared by every scenario hoist out of the disjunction
        // ((C /\ X) \/ (C /\ Y) = C /\ (X \/ Y)); the core conjunct is one
        // such, and the hoisting keeps the spawned thread count under the
        // parser cap.
        var common: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        for (live.items[0].conjuncts.items) |id| {
            // First-appearance order of the first scenario (semantics-spec-v1
            // 4.3: merged objects dispatch in schema order).
            var in_all = true;
            for (live.items[1..]) |sc| {
                var found = false;
                for (sc.conjuncts.items) |oid| {
                    if (oid == id) found = true;
                }
                if (!found) in_all = false;
            }
            if (in_all) {
                var dup = false;
                for (common.items) |cid| {
                    if (cid == id) dup = true;
                }
                if (!dup) try common.append(self.a, id);
            }
        }
        const isCommon = struct {
            list: []const grammar.NodeId,
            fn has(l: @This(), id: grammar.NodeId) bool {
                for (l.list) |c| {
                    if (c == id) return true;
                }
                return false;
            }
        }{ .list = common.items };
        // When the walk is masked (or the schema's own `type` already
        // restricts the instance to the guard's container kind), the guard's
        // other-kinds wrapper is dead: compile it bare.
        const obj_bare = (self.uneval_mask & typeBit(.object)) != 0 or typeIsOnly(kw2, "object");
        const arr_bare = (self.uneval_mask & typeBit(.array)) != 0 or typeIsOnly(kw2, "array");
        // Object conjuncts merge into one open_obj per scenario arm
        // (ADR-0009): the guard's unevaluated* schema becomes the merged
        // undeclared-value schema, which keeps the live thread count at key
        // dispatch within the parser cap. Common conjuncts that are open_obj
        // nodes merge into every arm instead of hoisting.
        var common_objs: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        var common_rest: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        for (common.items) |id| {
            switch (self.builder.nodes.items[id]) {
                .open_obj => try common_objs.append(self.a, id),
                else => try common_rest.append(self.a, id),
            }
        }
        var arms: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        for (groups.items) |g| {
            const ev = &g.items[0].eval;
            var guard_obj: ?grammar.NodeId = null;
            if (up_node) |un| {
                if (!ev.obj.all) guard_obj = try self.guardObjNode(&ev.obj, un, any_node, depth);
            }
            var guard_arr: ?grammar.NodeId = null;
            if (ui_node) |un| {
                if (!ev.arr.all) guard_arr = try self.guardArrNode(&ev.arr, un, any_node, depth);
            }
            var vacuous = false;
            var conj_alts: std.ArrayListUnmanaged(grammar.NodeId) = .{};
            for (g.items) |sc| {
                var rest: std.ArrayListUnmanaged(grammar.NodeId) = .{};
                var merged: ?grammar.NodeId = null;
                for (common_objs.items) |id| {
                    if (merged) |m| {
                        merged = try self.mergeOpenObj(m, id) orelse blk: {
                            try rest.append(self.a, id);
                            break :blk m;
                        };
                    } else merged = id;
                }
                for (sc.conjuncts.items) |id| {
                    if (isCommon.has(id)) continue;
                    switch (self.builder.nodes.items[id]) {
                        .open_obj => {
                            if (merged) |m| {
                                merged = try self.mergeOpenObj(m, id) orelse blk: {
                                    try rest.append(self.a, id);
                                    break :blk m;
                                };
                            } else merged = id;
                        },
                        else => try rest.append(self.a, id),
                    }
                }
                if (guard_obj) |gd| {
                    if (merged) |m| {
                        merged = try self.mergeOpenObj(m, gd) orelse blk: {
                            try rest.append(self.a, if (obj_bare) gd else try self.otherKindsWrap(gd, .object, depth));
                            break :blk m;
                        };
                    } else if (obj_bare) {
                        merged = gd;
                    } else {
                        try rest.append(self.a, try self.otherKindsWrap(gd, .object, depth));
                    }
                }
                if (guard_arr) |ga| {
                    try rest.append(self.a, if (arr_bare) ga else try self.otherKindsWrap(ga, .array, depth));
                }
                if (merged) |m| try rest.append(self.a, m);
                if (rest.items.len == 0) {
                    // The scenario is vacuous modulo the hoisted commons.
                    vacuous = true;
                    break;
                }
                if (rest.items.len == 1) {
                    try conj_alts.append(self.a, rest.items[0]);
                } else {
                    if (rest.items.len > 64) {
                        return self.fail(error.ResourceLimit, v.offset, "unevaluated* scenario has more than 64 conjuncts", .{});
                    }
                    try conj_alts.append(self.a, try self.addNode(.{ .comb = .{
                        .kind = .allof,
                        .branches = try self.builder.copyNodeIds(rest.items),
                    } }));
                }
            }
            if (vacuous) {
                if (guard_obj == null and guard_arr == null) continue; // nothing beyond the commons
                var gnodes: std.ArrayListUnmanaged(grammar.NodeId) = .{};
                if (guard_obj) |gd| try gnodes.append(self.a, if (obj_bare) gd else try self.otherKindsWrap(gd, .object, depth));
                if (guard_arr) |ga| try gnodes.append(self.a, if (arr_bare) ga else try self.otherKindsWrap(ga, .array, depth));
                if (gnodes.items.len == 1) {
                    try arms.append(self.a, gnodes.items[0]);
                } else {
                    try arms.append(self.a, try self.addNode(.{ .comb = .{
                        .kind = .allof,
                        .branches = try self.builder.copyNodeIds(gnodes.items),
                    } }));
                }
                continue;
            }
            if (conj_alts.items.len == 1) {
                try arms.append(self.a, conj_alts.items[0]);
            } else {
                try arms.append(self.a, try self.addNode(try self.builder.alternativesNode(conj_alts.items)));
            }
        }
        if (arms.items.len == 0) {
            // Every group reduced to the common conjuncts alone (common_objs
            // is empty here: a vacuous group requires it).
            if (common_rest.items.len == 0) return self.compileAnyJSON(depth);
            const inner = if (common_rest.items.len == 1)
                common_rest.items[0]
            else
                try self.addNode(.{ .comb = .{
                    .kind = .allof,
                    .branches = try self.builder.copyNodeIds(common_rest.items),
                } });
            if (out_arm) |oa| {
                const pair = try self.a.alloc(grammar.NodeId, 2);
                pair[0] = inner;
                pair[1] = oa;
                return self.addNode(try self.builder.alternativesNode(pair));
            }
            return inner;
        }
        var disj = arms.items[0];
        if (arms.items.len > 1) disj = try self.addNode(try self.builder.alternativesNode(arms.items));
        var inner = disj;
        if (common_rest.items.len > 0) {
            var all = try std.ArrayListUnmanaged(grammar.NodeId).initCapacity(self.a, common_rest.items.len + 1);
            try all.appendSlice(self.a, common_rest.items);
            try all.append(self.a, disj);
            inner = try self.addNode(.{ .comb = .{
                .kind = .allof,
                .branches = try self.builder.copyNodeIds(all.items),
            } });
        }
        if (out_arm) |oa| {
            const pair = try self.a.alloc(grammar.NodeId, 2);
            pair[0] = inner;
            pair[1] = oa;
            return self.addNode(try self.builder.alternativesNode(pair));
        }
        return inner;
    }

    /// True when the `type` keyword restricts instances to exactly the named
    /// type (string form, or a one-element array form).
    fn typeIsOnly(kw: Keywords, comptime name: []const u8) bool {
        const tv = kw.ty orelse return false;
        return switch (tv.v) {
            .string => |s| std.mem.eql(u8, s, name),
            .array => |items| items.len == 1 and items[0].v == .string and std.mem.eql(u8, items[0].v.string, name),
            else => false,
        };
    }

    /// Value-based type membership (semantics-spec-v1 4.4): a number is an
    /// integer iff its canonical decimal exponent is non-negative (zero
    /// included); 1.0 and 1e2 are integers.
    fn valueMatchesTypes(self: *Compiler, val: *const json.Value, types: u8) CompileError!bool {
        const bit: u8 = switch (val.v) {
            .string => typeBit(.string),
            .boolean => typeBit(.boolean),
            .null_v => typeBit(.null_),
            .array => typeBit(.array),
            .object => typeBit(.object),
            .number => |lex| {
                if (types & typeBit(.number) != 0) return true;
                if (types & typeBit(.integer) == 0) return false;
                const nc = try self.canonicalDecimal(lex, val.offset);
                return nc.digits.len == 0 or nc.exp10 >= 0;
            },
        };
        return types & bit != 0;
    }

    /// Structural enum/const: values are matched by value against the
    /// instance, filtered by the type set and (for strings) the length
    /// bounds, deduplicated by canonical form. The spec-v1 P4 numeric
    /// assertion filters the numeric values exactly like the type set does
    /// (applicability: it constrains numbers only). The sibling
    /// container keywords (properties/required/additionalProperties,
    /// propertyNames, min/maxProperties, dependencies/dependent*,
    /// items/prefixItems/contains/uniqueItems/min/maxItems) are exact
    /// assertions over the values as well and are decided statically per
    /// value; an undecidable combination refuses UNSUPPORTED_FEATURE rather
    /// than silently weakening the schema.
    fn compileEnumSpec(self: *Compiler, v: *const json.Value, types: u8, values: []const *const json.Value, kw: Keywords, depth: u32, nc: ?NumConstraint) CompileError!grammar.NodeId {
        var need_len = types & typeBit(.string) != 0;
        if (!need_len) {
            for (values) |val| {
                if (val.v == .string) {
                    need_len = true;
                    break;
                }
            }
        }
        var min_len: ?u32 = null;
        var max_len: ?u32 = null;
        if (need_len) {
            if (kw.min_length) |mv| min_len = try self.readBound(mv, "minLength");
            if (kw.max_length) |mv| max_len = try self.readBound(mv, "maxLength");
        }
        // pattern (P4) filters the string values of the enum/const set like
        // the length bounds do (semantics-spec-v1 4: the assertion applies
        // to every string instance).
        var pat_dfa: ?*const pattern.Dfa = null;
        if (kw.pattern_) |pv| {
            try self.path.append(self.a, "pattern");
            defer _ = self.path.pop();
            pat_dfa = try self.compilePatternKeyword(pv);
        }
        var alts: std.ArrayListUnmanaged(grammar.NodeId) = .{};
        var seen: std.StringHashMapUnmanaged(void) = .{};
        for (values) |val| {
            if (!try self.valueMatchesTypes(val, types)) continue;
            switch (val.v) {
                .string => |s| {
                    const n = countScalars(s);
                    if (min_len) |m| {
                        if (n < m) continue;
                    }
                    if (max_len) |m| {
                        if (n > m) continue;
                    }
                    if (pat_dfa) |d| {
                        // `s` is a decoded JSON string: valid UTF-8.
                        if (!(d.matchesUtf8(s) catch false)) continue;
                    }
                },
                .number => |lex| {
                    if (nc) |c| {
                        const ncv = try self.canonicalDecimal(lex, val.offset);
                        const nr: grammar.NumRange = .{
                            .min = c.lo,
                            .min_excl = c.lo_excl,
                            .max = c.hi,
                            .max_excl = c.hi_excl,
                        };
                        if (!grammar.numConstInRange(ncv, nr)) continue;
                        if (c.divisor) |d| {
                            const nm = try self.numMultNode(kw.multiple_of.?, d);
                            if (!grammar.numConstIsMultiple(ncv, nm)) continue;
                        }
                    }
                },
                else => {},
            }
            // The container assertions/applicators next to
            // enum/const constrain the values too, but the surviving values
            // compile to fixed-value nodes that never see them - so they
            // are decided here, exactly and statically. The type set, the
            // string bounds/pattern and the numeric assertion are already
            // filtered above; the boolean combinators conjoin as separate
            // parts in compileTypedSpec. A value the validator cannot
            // decide is neither dropped nor kept: the schema refuses.
            switch (try self.staticCheckKw(val, kw, depth, true)) {
                .pass => {},
                .fail => continue,
                .unknown => return self.fail(error.UnsupportedFeature, self.static_unknown_off, "enum/const values cannot be filtered exactly next to '{s}'", .{self.static_unknown_kw}),
            }
            var sig: std.ArrayListUnmanaged(u8) = .{};
            try self.canonicalSig(&sig, val);
            const gop = try seen.getOrPut(self.a, sig.items);
            if (gop.found_existing) continue;
            try alts.append(self.a, try self.compileConstValue(val, depth));
        }
        if (alts.items.len == 0) {
            return self.emptySubschema(depth, v.offset, "no enum/const value satisfies the sibling constraints", .{});
        }
        if (alts.items.len == 1) return alts.items[0];
        return self.addNode(try self.builder.alternativesNode(alts.items));
    }

    /// Canonical dedup signature of a JSON value: numbers by canonical
    /// decimal, objects with keys sorted (object key order is not
    /// significant for equality).
    fn canonicalSig(self: *Compiler, out: *std.ArrayListUnmanaged(u8), val: *const json.Value) CompileError!void {
        var b4: [4]u8 = undefined;
        var b8: [8]u8 = undefined;
        switch (val.v) {
            .string => |s| {
                try out.append(self.a, 'S');
                std.mem.writeInt(u32, &b4, @intCast(s.len), .little);
                try out.appendSlice(self.a, &b4);
                try out.appendSlice(self.a, s);
            },
            .number => |lex| {
                const nc = try self.canonicalDecimal(lex, val.offset);
                try out.append(self.a, 'N');
                try out.append(self.a, if (nc.neg) 1 else 0);
                std.mem.writeInt(u32, &b4, @intCast(nc.digits.len), .little);
                try out.appendSlice(self.a, &b4);
                try out.appendSlice(self.a, nc.digits);
                std.mem.writeInt(i64, &b8, nc.exp10, .little);
                try out.appendSlice(self.a, &b8);
            },
            .boolean => |b| try out.append(self.a, if (b) 'T' else 'F'),
            .null_v => try out.append(self.a, 'Z'),
            .array => |items| {
                try out.append(self.a, 'A');
                std.mem.writeInt(u32, &b4, @intCast(items.len), .little);
                try out.appendSlice(self.a, &b4);
                for (items) |it| try self.canonicalSig(out, it);
            },
            .object => |pairs| {
                try out.append(self.a, 'O');
                std.mem.writeInt(u32, &b4, @intCast(pairs.len), .little);
                try out.appendSlice(self.a, &b4);
                const order = try self.a.alloc(usize, pairs.len);
                for (order, 0..) |*o, i| o.* = i;
                const Ctx = struct {
                    pairs: []const json.Pair,
                    fn less(c: @This(), x: usize, y: usize) bool {
                        return std.mem.lessThan(u8, c.pairs[x].key, c.pairs[y].key);
                    }
                };
                std.mem.sort(usize, order, Ctx{ .pairs = pairs }, Ctx.less);
                for (order) |oi| {
                    std.mem.writeInt(u32, &b4, @intCast(pairs[oi].key.len), .little);
                    try out.appendSlice(self.a, &b4);
                    try out.appendSlice(self.a, pairs[oi].key);
                    try self.canonicalSig(out, pairs[oi].value);
                }
            },
        }
    }

    // ---- spec-v1: compile-time validation of enum/const values ----

    /// Three-valued result of the compile-time value validator: pass/fail
    /// are exact; unknown marks a combination the validator cannot decide
    /// (the caller refuses UNSUPPORTED_FEATURE instead of silently dropping
    /// or keeping the value).
    const Tri = enum { pass, fail, unknown };

    fn staticUnknown(self: *Compiler, offset: u32, kw: []const u8) Tri {
        if (self.static_unknown_kw.len == 0) {
            self.static_unknown_kw = kw;
            self.static_unknown_off = offset;
        }
        return .unknown;
    }

    fn triAnd(a: Tri, b: Tri) Tri {
        if (a == .fail or b == .fail) return .fail;
        if (a == .unknown or b == .unknown) return .unknown;
        return .pass;
    }

    /// Exact structural value equality (semantics-spec-v1 2): numbers by
    /// canonical decimal, arrays elementwise in order, objects by key set
    /// with order-insensitive values; different types never equal.
    fn constValueEqual(self: *Compiler, a: *const json.Value, b: *const json.Value) CompileError!bool {
        switch (a.v) {
            .null_v => return b.v == .null_v,
            .boolean => |x| return b.v == .boolean and b.v.boolean == x,
            .string => |s| return b.v == .string and std.mem.eql(u8, s, b.v.string),
            .number => |lex| {
                if (b.v != .number) return false;
                const ca = try self.canonicalDecimal(lex, a.offset);
                const cb = try self.canonicalDecimal(b.v.number, b.offset);
                return grammar.cmpNumConst(ca, cb) == .eq;
            },
            .array => |items| {
                if (b.v != .array) return false;
                const bi = b.v.array;
                if (items.len != bi.len) return false;
                for (items, bi) |x, y| {
                    if (!try self.constValueEqual(x, y)) return false;
                }
                return true;
            },
            .object => |pairs| {
                if (b.v != .object) return false;
                const bp = b.v.object;
                if (pairs.len != bp.len) return false;
                for (pairs) |pp| {
                    var found = false;
                    for (bp) |qq| {
                        if (std.mem.eql(u8, pp.key, qq.key)) {
                            if (!try self.constValueEqual(pp.value, qq.value)) return false;
                            found = true;
                            break;
                        }
                    }
                    if (!found) return false;
                }
                return true;
            },
        }
    }

    /// True when the value contains an object at any nesting depth: object
    /// equality is key-order-insensitive while the fixed-value serialization
    /// keeps one order, so only such values can diverge between value-level
    /// and serialization-level branch counting.
    fn valueContainsObject(v: *const json.Value) bool {
        return switch (v.v) {
            .object => true,
            .array => |items| blk: {
                for (items) |it| {
                    if (valueContainsObject(it)) break :blk true;
                }
                break :blk false;
            },
            else => false,
        };
    }

    /// Compile-time validation of a constant value against a full subschema
    /// (the nested level of the enum/const and combinator value analyses):
    /// boolean schemas and every
    /// keyword the compiler supports are decided exactly; the reference
    /// keywords and unevaluated* (the compileSchema diversions) have no
    /// static evaluation here and are unknown.
    fn staticValidate(self: *Compiler, val: *const json.Value, sv: *const json.Value, depth: u32) CompileError!Tri {
        if (depth > self.max_depth + 1) return self.staticUnknown(sv.offset, "schema depth");
        const pairs = switch (sv.v) {
            .boolean => |b| return if (b) .pass else .fail,
            .object => |p| p,
            else => return self.fail(error.InvalidSchema, sv.offset, "schema must be an object", .{}),
        };
        const kw = try self.scanKeywords(pairs);
        if (kw.ref) |rv| return self.staticUnknown(rv.offset, "$ref");
        if (kw.dyn_ref) |dv| return self.staticUnknown(dv.offset, "$dynamicRef");
        if (kw.rec_ref) |rv| return self.staticUnknown(rv.offset, "$recursiveRef");
        if (kw.uneval_props) |uv| {
            if (!(uv.v == .boolean and uv.v.boolean)) return self.staticUnknown(uv.offset, "unevaluatedProperties");
        }
        if (kw.uneval_items) |uv| {
            if (!(uv.v == .boolean and uv.v.boolean)) return self.staticUnknown(uv.offset, "unevaluatedItems");
        }
        return self.staticCheckKw(val, kw, depth, false);
    }

    /// The keyword checks of the static validator. At the top level of
    /// compileEnumSpec (`top`) the type set, enum/const membership, the
    /// string bounds/pattern and the numeric assertion are already filtered
    /// exactly by the caller and the boolean combinators conjoin as separate
    /// parts, so only the container keywords are decided there; nested
    /// levels decide everything.
    fn staticCheckKw(self: *Compiler, val: *const json.Value, kw: Keywords, depth: u32, top: bool) CompileError!Tri {
        if (!top) {
            if (kw.ty) |tv| {
                const types = try self.readTypeSet(tv);
                if (!try self.valueMatchesTypes(val, types)) return .fail;
            }
            if (kw.enum_) |ev| {
                const items = switch (ev.v) {
                    .array => |it| it,
                    else => return self.fail(error.InvalidSchema, ev.offset, "enum must be an array", .{}),
                };
                var hit = false;
                for (items) |ev2| {
                    if (try self.constValueEqual(val, ev2)) {
                        hit = true;
                        break;
                    }
                }
                if (!hit) return .fail;
            }
            if (kw.const_) |cv| {
                if (!try self.constValueEqual(val, cv)) return .fail;
            }
            switch (val.v) {
                .string => |s| {
                    const n = countScalars(s);
                    if (kw.min_length) |mv| {
                        if (n < try self.readBound(mv, "minLength")) return .fail;
                    }
                    if (kw.max_length) |mv| {
                        if (n > try self.readBound(mv, "maxLength")) return .fail;
                    }
                    if (kw.pattern_) |pv| {
                        try self.path.append(self.a, "pattern");
                        defer _ = self.path.pop();
                        const dfa = try self.compilePatternKeyword(pv);
                        // `s` is a decoded JSON string: valid UTF-8.
                        if (!(dfa.matchesUtf8(s) catch false)) return .fail;
                    }
                },
                .number => |lex| {
                    if (try self.readNumConstraint(kw)) |c| {
                        const ncv = try self.canonicalDecimal(lex, val.offset);
                        const nr: grammar.NumRange = .{
                            .min = c.lo,
                            .min_excl = c.lo_excl,
                            .max = c.hi,
                            .max_excl = c.hi_excl,
                        };
                        if (!grammar.numConstInRange(ncv, nr)) return .fail;
                        if (c.divisor) |d| {
                            const nm = try self.numMultNode(kw.multiple_of.?, d);
                            if (!grammar.numConstIsMultiple(ncv, nm)) return .fail;
                        }
                    }
                },
                else => {},
            }
        }
        var acc: Tri = switch (val.v) {
            .object => |pairs| try self.staticCheckObjectKw(val, pairs, kw, depth),
            .array => |items| try self.staticCheckArrayKw(val, items, kw, depth),
            else => .pass,
        };
        if (acc == .fail) return .fail;
        if (!top) {
            acc = triAnd(acc, try self.staticCheckCombinators(val, kw, depth));
        }
        return acc;
    }

    /// Object keyword checks of the static validator: mirrors
    /// compileObjectSpec semantics and its form/refusal rules over a
    /// constant object value.
    fn staticCheckObjectKw(self: *Compiler, val: *const json.Value, pairs: []const json.Pair, kw: Keywords, depth: u32) CompileError!Tri {
        if (kw.required) |rv| {
            const items = switch (rv.v) {
                .array => |it| it,
                else => return self.fail(error.InvalidSchema, rv.offset, "required must be an array", .{}),
            };
            for (items) |iv| {
                const name = switch (iv.v) {
                    .string => |s| s,
                    else => return self.fail(error.InvalidSchema, iv.offset, "required entries must be strings", .{}),
                };
                if (!jsonObjectHasKey(pairs, name)) return .fail;
            }
        }
        if (kw.min_props) |mv| {
            if (pairs.len < try self.readBoundValue(mv, "minProperties")) return .fail;
        }
        if (kw.max_props) |mv| {
            if (pairs.len > try self.readBoundValue(mv, "maxProperties")) return .fail;
        }
        // propertyNames: every key of the constant object is decidable.
        if (kw.prop_names) |pn| {
            switch (pn.v) {
                .boolean => |b| {
                    if (!b and pairs.len > 0) return .fail;
                },
                else => {
                    for (pairs) |pp| {
                        const kv = try self.a.create(json.Value);
                        kv.* = .{ .offset = pp.key_offset, .v = .{ .string = pp.key } };
                        const t = try self.staticValidate(kv, pn, depth + 1);
                        if (t == .fail) return .fail;
                        if (t == .unknown) return self.staticUnknown(pn.offset, "propertyNames");
                    }
                },
            }
        }
        // patternProperties: mirrors the engine limit (exactly one entry,
        // no declared-key overlap) - the static path never accepts a
        // combination the compiled path would refuse.
        var pattern_lit: ?[]const u8 = null;
        var pattern_dfa: ?*const pattern.Dfa = null;
        var pattern_schema: ?*const json.Value = null;
        if (kw.pattern_props) |ppv| {
            const ppairs = switch (ppv.v) {
                .object => |p| p,
                else => return self.fail(error.InvalidSchema, ppv.offset, "patternProperties must be an object", .{}),
            };
            if (ppairs.len > 1) {
                return self.fail(error.UnsupportedFeature, ppairs[1].key_offset, "multiple patternProperties entries need regex intersection (P3)", .{});
            }
            if (ppairs.len == 1) {
                const pat = ppairs[0].key;
                pattern_schema = ppairs[0].value;
                if (isLiteralPattern(pat)) {
                    pattern_lit = pat;
                } else {
                    pattern_dfa = try self.compilePatternText(pat, ppairs[0].key_offset);
                }
                if (kw.properties) |props_v| {
                    const prop_pairs = switch (props_v.v) {
                        .object => |p| p,
                        else => return self.fail(error.InvalidSchema, props_v.offset, "properties must be an object", .{}),
                    };
                    for (prop_pairs) |pp| {
                        const hit = if (pattern_dfa) |d| (d.matchesUtf8(pp.key) catch false) else (std.mem.indexOf(u8, pp.key, pattern_lit.?) != null);
                        if (hit) {
                            return self.fail(error.UnsupportedFeature, ppairs[0].key_offset, "patternProperties pattern overlaps a declared property (intersection is P3)", .{});
                        }
                    }
                }
            }
        }
        // properties / patternProperties / additionalProperties over the
        // constant key set.
        var acc: Tri = .pass;
        for (pairs) |pp| {
            var governed = false;
            if (kw.properties) |props_v| {
                const prop_pairs = switch (props_v.v) {
                    .object => |p| p,
                    else => return self.fail(error.InvalidSchema, props_v.offset, "properties must be an object", .{}),
                };
                for (prop_pairs) |sp| {
                    if (std.mem.eql(u8, sp.key, pp.key)) {
                        governed = true;
                        acc = triAnd(acc, try self.staticValidate(pp.value, sp.value, depth + 1));
                        break;
                    }
                }
            }
            if (!governed and pattern_schema != null) {
                const hit = if (pattern_dfa) |d| (d.matchesUtf8(pp.key) catch false) else (std.mem.indexOf(u8, pp.key, pattern_lit.?) != null);
                if (hit) {
                    governed = true;
                    acc = triAnd(acc, try self.staticValidate(pp.value, pattern_schema.?, depth + 1));
                }
            }
            if (!governed) {
                if (kw.additional) |add_v| {
                    switch (add_v.v) {
                        .boolean => |b| {
                            if (!b) return .fail;
                        },
                        else => acc = triAnd(acc, try self.staticValidate(pp.value, add_v, depth + 1)),
                    }
                }
            }
            if (acc == .fail) return .fail;
        }
        // dependencies / dependentRequired / dependentSchemas.
        const dep_tri = try self.staticCheckDeps(val, pairs, kw, depth);
        acc = triAnd(acc, dep_tri);
        return acc;
    }

    /// dependencies (draft-04..07: array or schema form) /
    /// dependentRequired / dependentSchemas over a constant object.
    fn staticCheckDeps(self: *Compiler, val: *const json.Value, pairs: []const json.Pair, kw: Keywords, depth: u32) CompileError!Tri {
        var acc: Tri = .pass;
        if (kw.dependencies) |dv| {
            const dpairs = switch (dv.v) {
                .object => |p| p,
                else => return self.fail(error.InvalidSchema, dv.offset, "dependencies must be an object", .{}),
            };
            for (dpairs) |dp| {
                if (!jsonObjectHasKey(pairs, dp.key)) continue;
                switch (dp.value.v) {
                    .array => |items| {
                        for (items) |it| {
                            const name = switch (it.v) {
                                .string => |s| s,
                                else => return self.fail(error.InvalidSchema, it.offset, "dependencies entries must be arrays of strings", .{}),
                            };
                            if (!jsonObjectHasKey(pairs, name)) return .fail;
                        }
                    },
                    .boolean => |b| {
                        if (!b) return .fail;
                    },
                    .object => acc = triAnd(acc, try self.staticValidate(val, dp.value, depth + 1)),
                    else => return self.fail(error.InvalidSchema, dp.value.offset, "dependencies entries must be schemas or arrays of strings", .{}),
                }
                if (acc == .fail) return .fail;
            }
        }
        if (kw.dep_required) |dv| {
            const dpairs = switch (dv.v) {
                .object => |p| p,
                else => return self.fail(error.InvalidSchema, dv.offset, "dependentRequired must be an object", .{}),
            };
            for (dpairs) |dp| {
                if (!jsonObjectHasKey(pairs, dp.key)) continue;
                const items = switch (dp.value.v) {
                    .array => |it| it,
                    else => return self.fail(error.InvalidSchema, dp.value.offset, "dependentRequired entries must be arrays of strings", .{}),
                };
                for (items) |it| {
                    const name = switch (it.v) {
                        .string => |s| s,
                        else => return self.fail(error.InvalidSchema, it.offset, "dependentRequired entries must be arrays of strings", .{}),
                    };
                    if (!jsonObjectHasKey(pairs, name)) return .fail;
                }
            }
        }
        if (kw.dep_schemas) |dv| {
            const dpairs = switch (dv.v) {
                .object => |p| p,
                else => return self.fail(error.InvalidSchema, dv.offset, "dependentSchemas must be an object", .{}),
            };
            for (dpairs) |dp| {
                if (!jsonObjectHasKey(pairs, dp.key)) continue;
                switch (dp.value.v) {
                    .boolean => |b| {
                        if (!b) return .fail;
                    },
                    .object => acc = triAnd(acc, try self.staticValidate(val, dp.value, depth + 1)),
                    else => return self.fail(error.InvalidSchema, dp.value.offset, "dependentSchemas entries must be schemas", .{}),
                }
                if (acc == .fail) return .fail;
            }
        }
        return acc;
    }

    /// Array keyword checks of the static validator: mirrors the
    /// array arm of compileArm over a constant array value.
    fn staticCheckArrayKw(self: *Compiler, val: *const json.Value, items: []const *json.Value, kw: Keywords, depth: u32) CompileError!Tri {
        _ = val;
        if (kw.min_items) |mv| {
            if (items.len < try self.readBound(mv, "minItems")) return .fail;
        }
        if (kw.max_items) |mv| {
            if (items.len > try self.readBound(mv, "maxItems")) return .fail;
        }
        var acc: Tri = .pass;
        // Tuple prefix: prefixItems (2020-12) or array-form items
        // (draft-04..2019-09); the dialect filter of scanKeywords has
        // already selected the applicable form.
        var prefix_len: usize = 0;
        var rest_schema: ?*const json.Value = null;
        var rest_closed = false;
        if (kw.prefix_items) |piv| {
            const prefix = switch (piv.v) {
                .array => |it| it,
                else => return self.fail(error.InvalidSchema, piv.offset, "prefixItems must be an array", .{}),
            };
            prefix_len = prefix.len;
            for (prefix, 0..) |ps, i| {
                if (i >= items.len) break;
                acc = triAnd(acc, try self.staticValidate(items[i], ps, depth + 1));
            }
            if (kw.items) |items_v| {
                if (items_v.v == .array) {
                    return self.fail(error.InvalidSchema, items_v.offset, "items as an array (tuple form) is not valid in 2020-12; use prefixItems", .{});
                }
                rest_schema = items_v;
            }
        } else if (kw.items) |items_v| {
            switch (items_v.v) {
                .array => |tuple| {
                    if (self.dialect == .d2020_12) {
                        return self.fail(error.InvalidSchema, items_v.offset, "items as an array (tuple form) is not valid in 2020-12; use prefixItems", .{});
                    }
                    prefix_len = tuple.len;
                    for (tuple, 0..) |ts, i| {
                        if (i >= items.len) break;
                        acc = triAnd(acc, try self.staticValidate(items[i], ts, depth + 1));
                    }
                    // additionalItems constrains only the elements past a
                    // tuple (ignored next to schema-form items); absent
                    // means `true`.
                    if (kw.additional_items) |aiv| {
                        switch (aiv.v) {
                            .boolean => |b| {
                                if (!b) rest_closed = true;
                            },
                            else => rest_schema = aiv,
                        }
                    }
                },
                else => rest_schema = items_v,
            }
        }
        if (acc == .fail) return .fail;
        if (items.len > prefix_len) {
            if (rest_closed) return .fail;
            if (rest_schema) |rs| {
                for (items[prefix_len..]) |it| {
                    acc = triAnd(acc, try self.staticValidate(it, rs, depth + 1));
                }
            }
        }
        if (acc == .fail) return .fail;
        // contains + minContains/maxContains (the counters apply only with
        // contains, mirroring compileArm).
        if (kw.contains) |cv| {
            var min_contains: u32 = 1;
            var max_contains: u32 = grammar.UNBOUNDED;
            if (kw.min_contains) |mv| min_contains = try self.readBoundValue(mv, "minContains");
            if (kw.max_contains) |mv| max_contains = try self.readBoundValue(mv, "maxContains");
            var hits: u64 = 0;
            var unknowns: u64 = 0;
            for (items) |it| {
                switch (try self.staticValidate(it, cv, depth + 1)) {
                    .pass => hits += 1,
                    .fail => {},
                    .unknown => unknowns += 1,
                }
            }
            if (hits > max_contains or hits + unknowns < min_contains) return .fail;
            if (unknowns > 0 and (hits < min_contains or hits + unknowns > max_contains)) {
                return self.staticUnknown(cv.offset, "contains");
            }
        }
        if (kw.unique_items) |uv| {
            const unique = switch (uv.v) {
                .boolean => |b| b,
                else => return self.fail(error.InvalidSchema, uv.offset, "uniqueItems must be a boolean", .{}),
            };
            if (unique) {
                for (items, 0..) |x, i| {
                    for (items[i + 1 ..]) |y| {
                        if (try self.constValueEqual(x, y)) return .fail;
                    }
                }
            }
        }
        return acc;
    }

    /// Boolean combinators of the static validator (nested levels only;
    /// at the compileEnumSpec top level they conjoin as separate parts).
    fn staticCheckCombinators(self: *Compiler, val: *const json.Value, kw: Keywords, depth: u32) CompileError!Tri {
        var acc: Tri = .pass;
        if (kw.all_of) |av| {
            const items = try self.staticCombArray(av, "allOf");
            for (items) |bv| {
                acc = triAnd(acc, try self.staticValidate(val, bv, depth + 1));
                if (acc == .fail) return .fail;
            }
        }
        if (acc == .fail) return .fail;
        if (kw.any_of) |av| {
            const items = try self.staticCombArray(av, "anyOf");
            var saw_unknown = false;
            var sub: Tri = .fail;
            for (items) |bv| {
                switch (try self.staticValidate(val, bv, depth + 1)) {
                    .pass => sub = .pass,
                    .fail => {},
                    .unknown => saw_unknown = true,
                }
                if (sub == .pass) break;
            }
            if (sub != .pass) sub = if (saw_unknown) .unknown else .fail;
            acc = triAnd(acc, sub);
        }
        if (kw.one_of) |av| {
            const items = try self.staticCombArray(av, "oneOf");
            var passes: u64 = 0;
            var saw_unknown = false;
            for (items) |bv| {
                switch (try self.staticValidate(val, bv, depth + 1)) {
                    .pass => passes += 1,
                    .fail => {},
                    .unknown => saw_unknown = true,
                }
            }
            var sub: Tri = .unknown;
            if (passes > 1) {
                sub = .fail;
            } else if (passes == 1) {
                sub = if (saw_unknown) .unknown else .pass;
            } else {
                sub = if (saw_unknown) .unknown else .fail;
            }
            acc = triAnd(acc, sub);
        }
        if (acc == .fail) return .fail;
        if (kw.not_) |nv| {
            switch (try self.staticValidate(val, nv, depth + 1)) {
                .pass => return .fail,
                .fail => {},
                .unknown => acc = .unknown,
            }
        }
        if (kw.if_) |iv| {
            const cond = try self.staticValidate(val, iv, depth + 1);
            switch (cond) {
                .pass => {
                    if (kw.then_) |tv| acc = triAnd(acc, try self.staticValidate(val, tv, depth + 1));
                },
                .fail => {
                    if (kw.else_) |ev| acc = triAnd(acc, try self.staticValidate(val, ev, depth + 1));
                },
                .unknown => {
                    // The branch is undecidable: the verdict is exact only
                    // when both applicators agree on the value.
                    const t: Tri = if (kw.then_) |tv| try self.staticValidate(val, tv, depth + 1) else .pass;
                    const e: Tri = if (kw.else_) |ev| try self.staticValidate(val, ev, depth + 1) else .pass;
                    acc = triAnd(acc, if (t == e) t else .unknown);
                },
            }
        }
        return acc;
    }

    fn staticCombArray(self: *Compiler, av: *const json.Value, comptime name: []const u8) CompileError![]*json.Value {
        const items = switch (av.v) {
            .array => |it| it,
            else => return self.fail(error.InvalidSchema, av.offset, name ++ " must be a non-empty array", .{}),
        };
        if (items.len == 0) {
            return self.fail(error.InvalidSchema, av.offset, name ++ " must be a non-empty array", .{});
        }
        if (items.len > 64) {
            return self.fail(error.ResourceLimit, av.offset, name ++ " has more than 64 branches", .{});
        }
        return items;
    }

    /// Grammar for exactly one JSON value (enum/const member): strings and
    /// numbers by value (any spelling of the number), arrays/objects
    /// structurally in document order (semantics-spec-v1 4.3).
    fn compileConstValue(self: *Compiler, val: *const json.Value, depth: u32) CompileError!grammar.NodeId {
        if (depth > self.max_depth) {
            return self.fail(error.ResourceLimit, val.offset, "schema depth exceeds max_depth {d}", .{self.max_depth});
        }
        switch (val.v) {
            .string => |s| {
                var lit: std.ArrayListUnmanaged(u8) = .{};
                try lit.append(self.a, '"');
                try appendEscaped(&lit, self.a, s);
                try lit.append(self.a, '"');
                return self.addNode(.{ .literal = try self.builder.addLiteral(lit.items) });
            },
            .number => |lex| {
                const nc = try self.canonicalDecimal(lex, val.offset);
                return self.addNode(.{ .num_const = nc });
            },
            .boolean => |b| {
                return self.addNode(.{ .literal = try self.builder.addLiteral(if (b) "true" else "false") });
            },
            .null_v => return self.addNode(.{ .literal = try self.builder.addLiteral("null") }),
            .array => |items| {
                if (items.len == 0) {
                    return self.addNode(.{ .literal = try self.builder.addLiteral("[]") });
                }
                var seq: std.ArrayListUnmanaged(grammar.NodeId) = .{};
                try seq.append(self.a, try self.addNode(.{ .literal = try self.builder.addLiteral("[") }));
                for (items, 0..) |it, i| {
                    if (i > 0) {
                        try seq.append(self.a, try self.addNode(.{ .literal = try self.builder.addLiteral(",") }));
                    }
                    try seq.append(self.a, try self.compileConstValue(it, depth + 1));
                }
                try seq.append(self.a, try self.addNode(.{ .literal = try self.builder.addLiteral("]") }));
                return self.addNode(.{ .seq = try self.builder.copyNodeIds(seq.items) });
            },
            .object => |pairs| {
                if (pairs.len == 0) {
                    return self.addNode(.{ .literal = try self.builder.addLiteral("{}") });
                }
                var props: std.ArrayListUnmanaged(grammar.Prop) = .{};
                for (pairs) |pp| {
                    var lit: std.ArrayListUnmanaged(u8) = .{};
                    try lit.append(self.a, '"');
                    try appendEscaped(&lit, self.a, pp.key);
                    try lit.appendSlice(self.a, "\":");
                    try props.append(self.a, .{
                        .key = try self.builder.addLiteral(lit.items),
                        .value = try self.compileConstValue(pp.value, depth + 1),
                        .required = true,
                    });
                }
                return self.addNode(.{ .object = .{ .props = try self.builder.copyProps(props.items) } });
            },
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
        const spec = self.profile == .spec_v1;
        // Keyword absence: canonical-v1 requires all three (messages and
        // their precedence are frozen); spec-v1 follows the dialect
        // defaults (properties {}, required [], additionalProperties true).
        var prop_pairs: []const json.Pair = &.{};
        if (kw.properties) |props_v| {
            prop_pairs = switch (props_v.v) {
                .object => |p| p,
                else => return self.fail(error.InvalidSchema, props_v.offset, "properties must be an object", .{}),
            };
        } else if (!spec) {
            return self.fail(error.InvalidSchema, v.offset, "object requires 'properties'", .{});
        }
        var req_items: []const *json.Value = &.{};
        if (kw.required) |req_v| {
            req_items = switch (req_v.v) {
                .array => |it| it,
                else => return self.fail(error.InvalidSchema, req_v.offset, "required must be an array", .{}),
            };
        } else if (!spec) {
            return self.fail(error.InvalidSchema, v.offset, "object requires 'required'", .{});
        }
        // additionalProperties: canonical-v1 requires exactly `false`.
        // spec-v1: absent or `true` is an open object (semantics-spec-v1
        // 4.3); a schema value is P2 and refused with a pointer.
        var open = false;
        if (kw.additional) |add_v| {
            switch (add_v.v) {
                .boolean => |b| {
                    if (b) {
                        if (!spec) return self.fail(error.InvalidSchema, add_v.offset, "additionalProperties must be exactly false", .{});
                        open = true;
                    }
                },
                else => {
                    if (!spec) return self.fail(error.InvalidSchema, add_v.offset, "additionalProperties must be exactly false", .{});
                    return self.fail(error.UnsupportedFeature, add_v.offset, "additionalProperties as a schema is not supported in spec-v1", .{});
                },
            }
        } else {
            if (!spec) return self.fail(error.InvalidSchema, v.offset, "object requires 'additionalProperties: false'", .{});
            open = true;
        }
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
        // Required names without a properties entry: canonical-v1 rejects;
        // spec-v1 treats them as required undeclared keys of an open object
        // (an undeclared key with any-JSON value). In a closed object the
        // combination is unsatisfiable: the key must and cannot appear.
        var extra: std.ArrayListUnmanaged(grammar.Literal) = .{};
        for (req_items) |rv| {
            const name = rv.v.string;
            var found = false;
            for (prop_pairs) |pp| {
                if (std.mem.eql(u8, pp.key, name)) found = true;
            }
            if (!found) {
                if (!spec) {
                    return self.fail(error.InvalidSchema, rv.offset, "required name '{s}' is not in properties", .{name});
                }
                if (!open) {
                    return self.fail(error.UnsatisfiableConstraint, rv.offset, "required name '{s}' is not in properties and additionalProperties is false", .{name});
                }
                var raw: std.ArrayListUnmanaged(u8) = .{};
                try appendEscaped(&raw, self.a, name);
                try extra.append(self.a, try self.builder.addLiteral(raw.items));
            }
        }
        if (!open) {
            return self.addNode(.{ .object = .{ .props = try self.builder.copyProps(props.items) } });
        }
        return self.addNode(.{ .open_obj = .{
            .props = try self.builder.copyProps(props.items),
            .extra_required = try self.builder.copyLiterals(extra.items),
            .value = try self.compileAnyJSON(depth + 1),
        } });
    }

    // ---- spec-v1 P2 object/array keywords (semantics-spec-v1 4.3) ----

    /// True for the empty-language node shape (ADR-0006 D1): a string
    /// bound no document satisfies (`false` subschemas, contradictory
    /// arms). Compile-side twin of the parser check.
    fn isEmptyNode(self: *Compiler, id: grammar.NodeId) bool {
        return switch (self.builder.nodes.items[id]) {
            .str => |sc| sc.min_len > sc.max_len,
            else => false,
        };
    }

    /// spec-v1 P4 (ROADMAP P4.1): a regex source string compiles to a DFA
    /// over Unicode scalar values (src/pattern.zig, the unanchored-search
    /// JSON Schema semantics). Constructs outside the supported ECMA-262
    /// subset refuse UNSUPPORTED_FEATURE, a malformed regex is
    /// INVALID_SCHEMA; the caller has pushed the keyword path, so both
    /// carry the exact JSON pointer. The DFA lives in the grammar arena
    /// and dies with the grammar.
    fn compilePatternText(self: *Compiler, pat: []const u8, offset: u32) CompileError!*const pattern.Dfa {
        const dfa = pattern.compileJsonSchema(self.a, pat) catch |e| switch (e) {
            error.Unsupported => return self.fail(error.UnsupportedFeature, offset, "pattern uses a construct outside the supported ECMA-262 subset", .{}),
            error.Invalid => return self.fail(error.InvalidSchema, offset, "pattern is not a valid regex", .{}),
            error.OutOfMemory => return error.OutOfMemory,
        };
        const slot = try self.a.create(pattern.Dfa);
        slot.* = dfa;
        return slot;
    }

    /// The `pattern` keyword: a string value compiled by compilePatternText.
    fn compilePatternKeyword(self: *Compiler, pv: *const json.Value) CompileError!*const pattern.Dfa {
        const pat = switch (pv.v) {
            .string => |s| s,
            else => return self.fail(error.InvalidSchema, pv.offset, "pattern must be a string", .{}),
        };
        return self.compilePatternText(pat, pv.offset);
    }

    /// True when the pattern DFA accepts at least one string within the
    /// codepoint length bounds: in search mode the accepted length set is
    /// [d_min, infinity) where d_min is the shortest accepting input.
    fn patternWithinBounds(self: *Compiler, dfa: *const pattern.Dfa, min_len: u32, max_len: u32) CompileError!bool {
        const d_min = (try dfa.shortestAccept(self.a, null)) orelse return false;
        return max_len == grammar.UNBOUNDED or @max(min_len, d_min) <= max_len;
    }

    /// P2 bound keywords (min/maxProperties, min/maxContains): JSON Schema
    /// reads "integer" by VALUE, so 1.0 and 2e0 are valid and 1.5 is not
    /// (section 1 value semantics); checked through the canonical decimal.
    fn readBoundValue(self: *Compiler, v: *const json.Value, what: []const u8) CompileError!u32 {
        const lex = switch (v.v) {
            .number => |l| l,
            else => return self.fail(error.InvalidSchema, v.offset, "{s} must be a non-negative integer", .{what}),
        };
        const nc = try self.canonicalDecimal(lex, v.offset);
        if (nc.neg and nc.digits.len > 0) {
            return self.fail(error.InvalidSchema, v.offset, "{s} must be a non-negative integer", .{what});
        }
        if (nc.exp10 < 0) {
            return self.fail(error.InvalidSchema, v.offset, "{s} must be an integer value", .{what});
        }
        var n: u64 = 0;
        for (nc.digits) |dgt| {
            n = n * 10 + (dgt - '0'); // n <= MAX_BOUND here, so no u64 overflow
            if (n > MAX_BOUND) {
                return self.fail(error.InvalidSchema, v.offset, "{s} is too large", .{what});
            }
        }
        var e: i64 = nc.exp10;
        while (e > 0) : (e -= 1) {
            n *= 10;
            if (n > MAX_BOUND) {
                return self.fail(error.InvalidSchema, v.offset, "{s} is too large", .{what});
            }
        }
        return @intCast(n);
    }

    /// Number of values of a finite literal-set element language (0 = not
    /// a finite literal set). Feeds the ADR-0005 D4 uniqueItems residual;
    /// overcounting (choice overlap) is safe, undercounting is not.
    fn finiteLiteralCount(self: *Compiler, id: grammar.NodeId) u32 {
        switch (self.builder.nodes.items[id]) {
            .literal => return 1,
            .lit_trie => |t| return @intCast(t.literals.len),
            .choice => |ids| {
                var n: u64 = 0;
                for (ids) |cid| {
                    const c = self.finiteLiteralCount(cid);
                    if (c == 0) return 0;
                    n += c;
                    if (n > std.math.maxInt(u32)) return std.math.maxInt(u32);
                }
                return @intCast(n);
            },
            else => return 0,
        }
    }

    /// Raw canonical key bytes (unquoted, escaped exactly as in the
    /// document) of a property name: the form the open-object key capture
    /// and the dep trigger/name literals compare against.
    fn rawKeyLit(self: *Compiler, name: []const u8) CompileError!grammar.Literal {
        var raw: std.ArrayListUnmanaged(u8) = .{};
        try appendEscaped(&raw, self.a, name);
        return self.builder.addLiteral(raw.items);
    }

    /// propertyNames compile-time check of a key: the decoded name is
    /// re-quoted and matched against the compiled propertyNames node with
    /// the parser's exact whole-value matcher over a temporary grammar
    /// view (the builder is not finished yet).
    fn pnAllowsKey(self: *Compiler, pn: grammar.NodeId, key: []const u8) CompileError!bool {
        var gv: grammar.Grammar = .{
            .kind = .json_schema,
            .nodes = self.builder.nodes.items,
            .literal_pool = self.builder.pool.items,
            .root = 0,
            .id = 0,
            .id_hi = 0,
            .finite_literal = false,
            .arena = undefined, // view only: never deinitialised
        };
        var side = parser.Side.init(self.a);
        defer side.deinit();
        var buf: std.ArrayListUnmanaged(u8) = .{};
        defer buf.deinit(self.a);
        try buf.append(self.a, '"');
        try appendEscaped(&buf, self.a, key);
        try buf.append(self.a, '"');
        return parser.nodeMatchesBytes(&gv, &side, pn, buf.items) catch |e| switch (e) {
            error.Parse => unreachable, // mapped to `false` inside
            else => |err| return err,
        };
    }

    /// Runtime context of an object arm for the dependency reductions:
    /// which keys can appear at all, and under which value schema.
    const DepCtx = struct {
        props: []const grammar.Prop,
        names_forbidden: bool,
        prop_names: ?grammar.NodeId,
        value_node: grammar.NodeId,
        pattern_lit: ?grammar.Literal,
        pattern_dfa: ?*const pattern.Dfa = null,
        pattern_value: grammar.NodeId,
    };

    /// Can `name` (decoded) appear as a key of this object at all?
    /// Declared: its value schema is non-empty. Undeclared: names are not
    /// forbidden, propertyNames admits it, and its effective value schema
    /// (pattern reroute, else additionalProperties) is non-empty.
    fn objKeyCanAppear(self: *Compiler, ctx: *const DepCtx, name: []const u8) CompileError!bool {
        var raw: std.ArrayListUnmanaged(u8) = .{};
        defer raw.deinit(self.a);
        try appendEscaped(&raw, self.a, name);
        for (ctx.props) |p| {
            const kl = self.builder.pool.items[p.key.off .. p.key.off + p.key.len];
            const inner = kl[1 .. kl.len - 2]; // strip the quotes and ':'
            if (std.mem.eql(u8, inner, raw.items)) return !self.isEmptyNode(p.value);
        }
        if (ctx.names_forbidden) return false;
        if (ctx.prop_names) |pn| {
            if (!try self.pnAllowsKey(pn, name)) return false;
        }
        var vnode = ctx.value_node;
        if (ctx.pattern_lit) |pl| {
            const pat = self.builder.pool.items[pl.off .. pl.off + pl.len];
            if (std.mem.indexOf(u8, name, pat) != null) vnode = ctx.pattern_value;
        }
        if (ctx.pattern_dfa) |pd| {
            if (pd.matchesUtf8(name) catch false) vnode = ctx.pattern_value;
        }
        return !self.isEmptyNode(vnode);
    }

    /// A ban dependency (the trigger key may not appear): dropped when the
    /// trigger can never appear anyway, an empty arm when the trigger is
    /// required.
    fn addBanDep(self: *Compiler, trigger: []const u8, ctx: *const DepCtx, req_set: *const std.StringHashMapUnmanaged(void), deps: *std.ArrayListUnmanaged(grammar.Dep), arm_dead: *bool) CompileError!void {
        if (!try self.objKeyCanAppear(ctx, trigger)) return; // vacuous
        if (req_set.contains(trigger)) {
            arm_dead.* = true;
            return;
        }
        try deps.append(self.a, .{ .trigger = try self.rawKeyLit(trigger), .kind = .ban });
    }

    /// dependentRequired / array-form dependencies entry: `trigger`
    /// requires every listed name. A name the object can never carry turns
    /// the entry into a trigger ban; an unemittable trigger makes it
    /// vacuous.
    fn readDepRequired(self: *Compiler, v: *const json.Value, trigger: []const u8, ctx: *const DepCtx, req_set: *const std.StringHashMapUnmanaged(void), deps: *std.ArrayListUnmanaged(grammar.Dep), arm_dead: *bool, what: []const u8) CompileError!void {
        const items = switch (v.v) {
            .array => |it| it,
            else => return self.fail(error.InvalidSchema, v.offset, "{s} entries must be arrays of strings", .{what}),
        };
        var names: std.ArrayListUnmanaged(grammar.Literal) = .{};
        var seen: std.StringHashMapUnmanaged(void) = .{};
        var all_can = true;
        for (items) |it| {
            const name = switch (it.v) {
                .string => |s| s,
                else => return self.fail(error.InvalidSchema, it.offset, "{s} entries must be strings", .{what}),
            };
            if (std.mem.eql(u8, name, trigger)) continue; // self-dependency is vacuous
            const gop = try seen.getOrPut(self.a, name);
            if (gop.found_existing) continue;
            if (all_can and !try self.objKeyCanAppear(ctx, name)) all_can = false;
            try names.append(self.a, try self.rawKeyLit(name));
        }
        if (names.items.len == 0) return;
        if (!all_can) {
            try self.addBanDep(trigger, ctx, req_set, deps, arm_dead);
            return;
        }
        if (!try self.objKeyCanAppear(ctx, trigger)) return; // vacuous
        try deps.append(self.a, .{
            .trigger = try self.rawKeyLit(trigger),
            .kind = .required,
            .names = try self.builder.copyLiterals(names.items),
        });
    }

    /// dependentSchemas / schema-form dependencies entry: when the trigger
    /// was seen, the whole object must match the subschema (exact re-parse
    /// of the captured object bytes at close). `false` (or an empty
    /// language) bans the trigger; `true`/`{}` is vacuous.
    fn addDepSchema(self: *Compiler, v: *const json.Value, trigger: []const u8, ctx: *const DepCtx, req_set: *const std.StringHashMapUnmanaged(void), deps: *std.ArrayListUnmanaged(grammar.Dep), arm_dead: *bool, what: []const u8, depth: u32) CompileError!void {
        switch (v.v) {
            .boolean => |b| {
                if (!b) try self.addBanDep(trigger, ctx, req_set, deps, arm_dead);
                return;
            },
            .object => |op| {
                if (op.len == 0) return; // trivially true
            },
            else => return self.fail(error.InvalidSchema, v.offset, "{s} entries must be schemas", .{what}),
        }
        if (!try self.objKeyCanAppear(ctx, trigger)) return; // vacuous
        try self.path.append(self.a, what);
        try self.path.append(self.a, trigger);
        defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
        const node = try self.compileSchema(v, depth + 1);
        if (self.isEmptyNode(node)) {
            try self.addBanDep(trigger, ctx, req_set, deps, arm_dead);
            return;
        }
        try deps.append(self.a, .{
            .trigger = try self.rawKeyLit(trigger),
            .kind = .schema,
            .schema = node,
        });
    }

    /// spec-v1 object arm (semantics-spec-v1 4.3, P2): declared properties
    /// in schema order; undeclared keys constrained by additionalProperties
    /// (boolean or schema), one patternProperties entry (a literal
    /// substring or, since P4, a full regex), and propertyNames;
    /// min/maxProperties and dependencies are tracked over the seen-key
    /// set at runtime (ADR-0005 D4, ADR-0006 D2).
    /// Returns null when the arm's own constraints are contradictory.
    fn compileObjectSpec(self: *Compiler, v: *const json.Value, kw: Keywords, depth: u32) CompileError!?grammar.NodeId {
        _ = v;
        var prop_pairs: []const json.Pair = &.{};
        if (kw.properties) |props_v| {
            prop_pairs = switch (props_v.v) {
                .object => |p| p,
                else => return self.fail(error.InvalidSchema, props_v.offset, "properties must be an object", .{}),
            };
        }
        var req_items: []const *json.Value = &.{};
        if (kw.required) |req_v| {
            req_items = switch (req_v.v) {
                .array => |it| it,
                else => return self.fail(error.InvalidSchema, req_v.offset, "required must be an array", .{}),
            };
        }
        // additionalProperties: absent or `true` is an open object; `false`
        // closes it; a schema constrains the undeclared values (P2).
        var open = false;
        var addl_node: ?grammar.NodeId = null;
        if (kw.additional) |add_v| {
            switch (add_v.v) {
                .boolean => |b| open = b,
                else => {
                    open = true;
                    try self.path.append(self.a, "additionalProperties");
                    defer _ = self.path.pop();
                    addl_node = try self.compileSchema(add_v, depth + 1);
                },
            }
        } else {
            open = true;
        }
        // propertyNames (P2, draft-06+): `false` forbids every key; a
        // schema constrains the name of every key that appears. Declared
        // keys are checked here, undeclared keys at dispatch.
        var names_forbidden = false;
        var prop_names_node: ?grammar.NodeId = null;
        if (kw.prop_names) |pn_v| {
            switch (pn_v.v) {
                .boolean => |b| {
                    if (!b) names_forbidden = true;
                },
                else => {
                    try self.path.append(self.a, "propertyNames");
                    defer _ = self.path.pop();
                    prop_names_node = try self.compileSchema(pn_v, depth + 1);
                },
            }
        }
        // patternProperties: exactly one entry. A pattern free of regex
        // metacharacters keeps the P2 literal-substring path (the decoded
        // key must contain it); anything else is a full ECMA-262-subset
        // regex (P4) matched by DFA search over the decoded key. Several
        // patterns and a declared-key overlap need a conjunction/
        // intersection (P3) and are refused with a pointer.
        var pattern_lit: ?grammar.Literal = null;
        var pattern_dfa: ?*const pattern.Dfa = null;
        var pattern_value: grammar.NodeId = 0;
        if (kw.pattern_props) |ppv| {
            const ppairs = switch (ppv.v) {
                .object => |p| p,
                else => return self.fail(error.InvalidSchema, ppv.offset, "patternProperties must be an object", .{}),
            };
            if (ppairs.len > 1) {
                try self.path.append(self.a, "patternProperties");
                defer _ = self.path.pop();
                return self.fail(error.UnsupportedFeature, ppairs[1].key_offset, "multiple patternProperties entries need regex intersection (P3)", .{});
            }
            if (ppairs.len == 1) {
                const pat = ppairs[0].key;
                try self.path.append(self.a, "patternProperties");
                try self.path.append(self.a, pat);
                defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
                if (isLiteralPattern(pat)) {
                    for (prop_pairs) |pp| {
                        if (std.mem.indexOf(u8, pp.key, pat) != null) {
                            return self.fail(error.UnsupportedFeature, ppairs[0].key_offset, "patternProperties literal overlaps a declared property (intersection is P3)", .{});
                        }
                    }
                } else {
                    const dfa = try self.compilePatternText(pat, ppairs[0].key_offset);
                    for (prop_pairs) |pp| {
                        // `pp.key` is a decoded JSON string: valid UTF-8.
                        if (dfa.matchesUtf8(pp.key) catch false) {
                            return self.fail(error.UnsupportedFeature, ppairs[0].key_offset, "patternProperties pattern overlaps a declared property (intersection is P3)", .{});
                        }
                    }
                    pattern_dfa = dfa;
                }
                const trivial = switch (ppairs[0].value.v) {
                    .boolean => |b| b,
                    .object => |op| op.len == 0,
                    else => false,
                };
                if (!trivial) {
                    pattern_value = try self.compileSchema(ppairs[0].value, depth + 1);
                    if (pattern_dfa == null) {
                        pattern_lit = try self.builder.addLiteral(pat); // stored decoded
                    }
                } else if (!(open and addl_node == null)) {
                    // A trivial value schema places no constraint, but the
                    // pattern still gates key ADMISSION against a
                    // restrictive additionalProperties (false or a schema):
                    // a matching key is governed by patternProperties, not
                    // by additionalProperties. Route it to AnyJSON.
                    pattern_value = try self.compileAnyJSON(depth + 1);
                    if (pattern_dfa == null) {
                        pattern_lit = try self.builder.addLiteral(pat);
                    }
                } else {
                    // Undeclared values are AnyJSON anyway: the entry is
                    // inert and drops out entirely.
                    pattern_dfa = null;
                }
            }
        }
        // min/maxProperties (P2), counted over the tracked seen-key set.
        var min_props: u32 = 0;
        var max_props: u32 = grammar.UNBOUNDED;
        if (kw.min_props) |mv| min_props = try self.readBoundValue(mv, "minProperties");
        if (kw.max_props) |mv| max_props = try self.readBoundValue(mv, "maxProperties");
        if (min_props > max_props) return null;
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
        // Declared properties, filtered through propertyNames: a key the
        // name schema rejects can never appear - a required one empties
        // the arm, an optional one is dropped.
        var props: std.ArrayListUnmanaged(grammar.Prop) = .{};
        var banned: std.ArrayListUnmanaged([]const u8) = .{};
        for (prop_pairs) |pp| {
            var allowed = !names_forbidden;
            if (allowed) {
                if (prop_names_node) |pn| allowed = try self.pnAllowsKey(pn, pp.key);
            }
            if (!allowed) {
                if (req_set.contains(pp.key)) return null;
                continue;
            }
            var lit: std.ArrayListUnmanaged(u8) = .{};
            try lit.append(self.a, '"');
            try appendEscaped(&lit, self.a, pp.key);
            try lit.appendSlice(self.a, "\":");
            try self.path.append(self.a, "properties");
            try self.path.append(self.a, pp.key);
            defer self.path.shrinkRetainingCapacity(self.path.items.len - 2);
            const child = try self.compileSchema(pp.value, depth + 1);
            if (self.isEmptyNode(child)) {
                // An optional property whose value language is empty may
                // not appear at all: a ban dependency rejects the key at
                // dispatch (mask-visible, ADR-0005) instead of letting the
                // parse die mid-value. A required one empties the arm.
                if (req_set.contains(pp.key)) return null;
                try banned.append(self.a, pp.key);
                continue;
            }
            try props.append(self.a, .{
                .key = try self.builder.addLiteral(lit.items),
                .value = child,
                .required = req_set.contains(pp.key),
            });
        }
        // The undeclared-value schema: an empty language when the object is
        // closed (additionalProperties: false or an unsatisfiable schema).
        // spec-v1 P6a: with no in-place applicator carrying property
        // evaluations (compileUnevaluated guarantees that on this path),
        // unevaluatedProperties folds into the undeclared-value schema -
        // exactly the additionalProperties role over locally unevaluated
        // keys.
        var uneval_node: ?grammar.NodeId = null;
        if (kw.uneval_props) |uv| {
            switch (uv.v) {
                .boolean => |b| {
                    if (!b) uneval_node = try self.emptyLangNode();
                },
                else => {
                    try self.path.append(self.a, "unevaluatedProperties");
                    defer _ = self.path.pop();
                    uneval_node = try self.compileSchema(uv, depth + 1);
                },
            }
        }
        const value_node = if (!open)
            try self.emptyLangNode()
        else
            addl_node orelse uneval_node orelse try self.compileAnyJSON(depth + 1);
        // Required names without a properties entry behave as required
        // undeclared keys (P1); they too must pass propertyNames, and when
        // their effective value language is empty the combination is
        // unsatisfiable (the P1 closed-object case).
        var extra: std.ArrayListUnmanaged(grammar.Literal) = .{};
        for (req_items) |rv| {
            const name = rv.v.string;
            var found = false;
            for (prop_pairs) |pp| {
                if (std.mem.eql(u8, pp.key, name)) found = true;
            }
            if (found) continue;
            if (names_forbidden) return null;
            if (prop_names_node) |pn| {
                if (!try self.pnAllowsKey(pn, name)) return null;
            }
            var eff = value_node;
            if (pattern_lit) |pl| {
                const pat = self.builder.pool.items[pl.off .. pl.off + pl.len];
                if (std.mem.indexOf(u8, name, pat) != null) eff = pattern_value;
            }
            if (pattern_dfa) |pd| {
                if (pd.matchesUtf8(name) catch false) eff = pattern_value;
            }
            if (self.isEmptyNode(eff)) {
                return self.fail(error.UnsatisfiableConstraint, rv.offset, "required name '{s}' is not in properties and additionalProperties is false", .{name});
            }
            try extra.append(self.a, try self.rawKeyLit(name));
        }
        // dependencies / dependentRequired / dependentSchemas (P2).
        var deps: std.ArrayListUnmanaged(grammar.Dep) = .{};
        var arm_dead = false;
        const dctx: DepCtx = .{
            .props = props.items,
            .names_forbidden = names_forbidden,
            .prop_names = prop_names_node,
            .value_node = value_node,
            .pattern_lit = pattern_lit,
            .pattern_dfa = pattern_dfa,
            .pattern_value = pattern_value,
        };
        // Optional properties with an empty value language (collected
        // above): the key is banned outright.
        for (banned.items) |bn| {
            try self.addBanDep(bn, &dctx, &req_set, &deps, &arm_dead);
        }
        if (kw.dependencies) |dv| {
            const dpairs = switch (dv.v) {
                .object => |p| p,
                else => return self.fail(error.InvalidSchema, dv.offset, "dependencies must be an object", .{}),
            };
            for (dpairs) |dp| {
                switch (dp.value.v) {
                    .array => try self.readDepRequired(dp.value, dp.key, &dctx, &req_set, &deps, &arm_dead, "dependencies"),
                    .object, .boolean => try self.addDepSchema(dp.value, dp.key, &dctx, &req_set, &deps, &arm_dead, "dependencies", depth),
                    else => return self.fail(error.InvalidSchema, dp.value.offset, "dependencies entries must be schemas or arrays of strings", .{}),
                }
            }
        }
        if (kw.dep_required) |dv| {
            const dpairs = switch (dv.v) {
                .object => |p| p,
                else => return self.fail(error.InvalidSchema, dv.offset, "dependentRequired must be an object", .{}),
            };
            for (dpairs) |dp| {
                try self.readDepRequired(dp.value, dp.key, &dctx, &req_set, &deps, &arm_dead, "dependentRequired");
            }
        }
        if (kw.dep_schemas) |dv| {
            const dpairs = switch (dv.v) {
                .object => |p| p,
                else => return self.fail(error.InvalidSchema, dv.offset, "dependentSchemas must be an object", .{}),
            };
            for (dpairs) |dp| {
                try self.addDepSchema(dp.value, dp.key, &dctx, &req_set, &deps, &arm_dead, "dependentSchemas", depth);
            }
        }
        if (arm_dead) return null;
        // Feasibility: when undeclared keys can never appear, the property
        // budget is the number of declared keys with a non-empty value.
        const undecl_possible = blk: {
            if (names_forbidden) break :blk false;
            if (!self.isEmptyNode(value_node)) break :blk true;
            if (pattern_lit != null and !self.isEmptyNode(pattern_value)) break :blk true;
            if (pattern_dfa != null and !self.isEmptyNode(pattern_value)) break :blk true;
            break :blk false;
        };
        if (!undecl_possible) {
            var budget: u32 = 0;
            for (props.items) |p| {
                if (!self.isEmptyNode(p.value)) budget += 1;
            }
            if (min_props > budget) return null;
        }
        // A closed object without P2 runtime state keeps the P1 closed
        // shape; everything else is an open object whose undeclared-value
        // schema is empty when the object is closed.
        const track_keys = deps.items.len > 0 or min_props > 0 or max_props != grammar.UNBOUNDED;
        const has_p2_runtime = track_keys or names_forbidden or prop_names_node != null or pattern_lit != null or pattern_dfa != null;
        if (!open and !has_p2_runtime) {
            return try self.addNode(.{ .object = .{ .props = try self.builder.copyProps(props.items) } });
        }
        var capture = false;
        for (deps.items) |dep| {
            if (dep.kind == .schema) capture = true;
        }
        return try self.addNode(.{ .open_obj = .{
            .props = try self.builder.copyProps(props.items),
            .extra_required = try self.builder.copyLiterals(extra.items),
            .value = value_node,
            .min_props = min_props,
            .max_props = max_props,
            .track_keys = track_keys,
            .capture = capture,
            .names_forbidden = names_forbidden,
            .deps = try self.builder.copyDeps(deps.items),
            .prop_names = prop_names_node,
            .pattern_lit = pattern_lit,
            .pattern_value = pattern_value,
            .pattern_dfa = pattern_dfa,
        } });
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
        return self.addNode(try self.builder.alternativesNode(alts.items));
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

/// Literal-substring patternProperties fast path (spec-v1 P2): a pattern
/// carrying no regex metacharacters is an unanchored literal search, which
/// the engine matches without a DFA. Anything else compiles as a full
/// ECMA-262-subset regex (P4, src/pattern.zig).
fn isLiteralPattern(pat: []const u8) bool {
    for (pat) |b| {
        switch (b) {
            '\\', '^', '$', '.', '|', '?', '*', '+', '(', ')', '[', ']', '{', '}' => return false,
            else => {},
        }
    }
    return true;
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
    return compile(std.testing.allocator, s, max_depth, &diag, &w, false, .canonical_v1, &.{});
}

fn compileDiag(s: []const u8, diag: *Diagnostic) CompileError!grammar.Grammar {
    var w = work_mod.Work{};
    return compile(std.testing.allocator, s, 64, diag, &w, false, .canonical_v1, &.{});
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
        .object => |p| p.props,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), props.len);
    try std.testing.expectEqualStrings("\"action\":", g.literalBytes(props[0].key));
    try std.testing.expect(props[0].required);
    // The enum compiles to a shared-prefix literal trie; literals keep the
    // schema order.
    const tr = switch (g.node(props[0].value).*) {
        .lit_trie => |lt| lt,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), tr.literals.len);
    try std.testing.expectEqualStrings("\"buy\"", g.literalBytes(tr.literals[0]));
    try std.testing.expectEqualStrings("\"sell\"", g.literalBytes(tr.literals[1]));
    try std.testing.expect(!tr.nodes[0].terminal);
    try std.testing.expect(g.node(props[1].value).* == .int_v);
}

test "enum with common prefixes" {
    const s = "{\"type\":\"string\",\"enum\":[\"foo\",\"foobar\",\"fob\"]}";
    var g = try compileT(s, 64);
    defer g.deinit();
    const tr = switch (g.node(g.root).*) {
        .lit_trie => |lt| lt,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 3), tr.literals.len);
    const expected = [_][]const u8{ "\"foo\"", "\"foobar\"", "\"fob\"" };
    for (tr.literals, expected) |lit, exp| {
        try std.testing.expectEqualStrings(exp, g.literalBytes(lit));
    }
    // Shared prefixes must be shared in the trie: the three quoted literals
    // span 18 bytes, the trie has fewer nodes, and three terminal nodes.
    var terminals: usize = 0;
    for (tr.nodes) |nd| {
        if (nd.terminal) terminals += 1;
    }
    try std.testing.expectEqual(@as(usize, 3), terminals);
    try std.testing.expect(tr.nodes.len < 18);
}

test "$defs and local $ref" {
    const s = "{\"$defs\":{\"positiveInt\":{\"type\":\"integer\"}},\"type\":\"object\",\"properties\":{\"count\":{\"$ref\":\"#/$defs/positiveInt\"}},\"required\":[\"count\"],\"additionalProperties\":false}";
    var g = try compileT(s, 64);
    defer g.deinit();
    const props = switch (g.node(g.root).*) {
        .object => |p| p.props,
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
        const tr = switch (g.node(g.root).*) {
            .lit_trie => |lt| lt,
            else => return error.TestUnexpectedResult,
        };
        try std.testing.expectEqual(@as(usize, 2), tr.literals.len);
        try std.testing.expectEqualStrings("100", g.literalBytes(tr.literals[0]));
        try std.testing.expectEqualStrings("20", g.literalBytes(tr.literals[1]));
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
        .object => |p| p.props,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqualStrings("\"a\\nb\":", g.literalBytes(props[0].key));
    const lit = switch (g.node(props[0].value).*) {
        .literal => |l| l,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqualStrings("null", g.literalBytes(lit));
}

fn compileSpec(s: []const u8, diag: *Diagnostic) CompileError!grammar.Grammar {
    var w = work_mod.Work{};
    return compile(std.testing.allocator, s, 64, diag, &w, false, .spec_v1, &.{});
}

test "spec-v1: type union compiles to a choice of arms" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"type\":[\"integer\",\"string\"]}", &diag);
    defer g.deinit();
    const alts = switch (g.node(g.root).*) {
        .lit_trie => return error.TestUnexpectedResult, // not all-literal
        .choice => |alts| alts,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), alts.len);
    // Arms are emitted in SchemaType declaration order, not list order.
    try std.testing.expect(g.node(alts[0]).* == .str);
    try std.testing.expect(g.node(alts[1]).* == .int_num);
}

test "spec-v1: integer arm is the value-based int_num node" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"type\":\"integer\"}", &diag);
    defer g.deinit();
    try std.testing.expect(g.node(g.root).* == .int_num);
}

test "spec-v1: const number compiles to canonical num_const" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"const\": 1.50}", &diag);
    defer g.deinit();
    const nc = switch (g.node(g.root).*) {
        .num_const => |nc| nc,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(!nc.neg);
    try std.testing.expectEqualStrings("15", nc.digits);
    try std.testing.expectEqual(@as(i64, -1), nc.exp10);
}

test "spec-v1: const -0 canonicalizes to unsigned zero" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"const\": -0.0}", &diag);
    defer g.deinit();
    const nc = switch (g.node(g.root).*) {
        .num_const => |nc| nc,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(!nc.neg);
    try std.testing.expectEqual(@as(usize, 0), nc.digits.len);
}

test "spec-v1: enum dedups values equal by canonical form" {
    var diag: Diagnostic = .{};
    // 1 and 1.0 are the same value; {"b":2,"a":1} equals {"a":1,"b":2}.
    var g = try compileSpec("{\"enum\":[1,1.0,{\"b\":2,\"a\":1},{\"a\":1,\"b\":2}]}", &diag);
    defer g.deinit();
    const alts = switch (g.node(g.root).*) {
        .choice => |alts| alts,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), alts.len);
    try std.testing.expect(g.node(alts[0]).* == .num_const);
    try std.testing.expect(g.node(alts[1]).* == .object);
}

test "spec-v1: enum values are filtered by the type set, empty root refuses" {
    var diag: Diagnostic = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"string\",\"enum\":[1,2]}", &diag));
    try std.testing.expectEqualStrings("", diag.pointerText());
}

test "spec-v1: integer type membership is value-based" {
    var diag: Diagnostic = .{};
    // 1e2 is an integer by value; 1.5 is not.
    var g = try compileSpec("{\"type\":\"integer\",\"enum\":[1e2,1.5]}", &diag);
    defer g.deinit();
    const nc = switch (g.node(g.root).*) {
        .num_const => |nc| nc,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqualStrings("1", nc.digits);
    try std.testing.expectEqual(@as(i64, 2), nc.exp10);
}

test "spec-v1: type list validation errors" {
    var diag: Diagnostic = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"type\":[]}", &diag));
    diag = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"type\":[\"integer\",\"integer\"]}", &diag));
    diag = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"type\":[\"foo\"]}", &diag));
    diag = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"type\":[1]}", &diag));
}

test "spec-v1: structural const object compiles to a closed object" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"const\":{\"a\":1,\"b\":[true,null]}}", &diag);
    defer g.deinit();
    const props = switch (g.node(g.root).*) {
        .object => |o| o.props,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), props.len);
    try std.testing.expect(props[0].required);
    try std.testing.expect(g.node(props[0].value).* == .num_const);
    const seq = switch (g.node(props[1].value).*) {
        .seq => |s| s,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 5), seq.len); // [ true , null ]
}

test "spec-v1: empty-language subschema is an empty node, root a refusal" {
    var diag: Diagnostic = .{};
    // Root: contradiction refuses.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"string\",\"minLength\":5,\"maxLength\":2}", &diag));
    // Subschema: an optional property whose value has an empty language is
    // banned outright (the key may not appear, rejected at dispatch).
    diag = .{};
    var g = try compileSpec("{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"string\",\"minLength\":5,\"maxLength\":2}}}", &diag);
    defer g.deinit();
    const on = switch (g.node(g.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 0), on.props.len);
    try std.testing.expectEqual(@as(usize, 1), on.deps.len);
    try std.testing.expect(on.deps[0].kind == .ban);
    try std.testing.expectEqualStrings("a", g.literalBytes(on.deps[0].trigger));
    // The same property required empties the arm (a refusal at the root).
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"string\",\"minLength\":5,\"maxLength\":2}},\"required\":[\"a\"]}", &diag));
}

test "spec-v1: inapplicable validation keywords are ignored" {
    var diag: Diagnostic = .{};
    // minLength with type integer: ignored, no error, and even a malformed
    // inapplicable keyword does not fail the compile.
    var g = try compileSpec("{\"type\":\"integer\",\"minLength\":\"bogus\",\"items\":{\"type\":\"string\"}}", &diag);
    defer g.deinit();
    try std.testing.expect(g.node(g.root).* == .int_num);
}

// ---- spec-v1 P2 keyword compilation ----

test "spec-v1 P2: tuple items compile to a prefix repeat" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"$schema\":\"http://json-schema.org/draft-07/schema\",\"type\":\"array\",\"items\":[{\"type\":\"string\"},{\"type\":\"integer\"}]}", &diag);
    defer g.deinit();
    const rep = switch (g.node(g.root).*) {
        .repeat => |r| r,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), rep.prefix.len);
    try std.testing.expect(g.node(rep.prefix[0]).* == .str);
    try std.testing.expect(g.node(rep.prefix[1]).* == .int_num);
    try std.testing.expect(rep.contains == null);
    try std.testing.expect(!rep.unique);
}

test "spec-v1 P2: prefixItems with items false caps the array" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"type\":\"array\",\"prefixItems\":[{\"type\":\"string\"}],\"items\":false}", &diag);
    defer g.deinit();
    const rep = switch (g.node(g.root).*) {
        .repeat => |r| r,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 1), rep.prefix.len);
    try std.testing.expectEqual(@as(u32, 1), rep.max);
}

test "spec-v1 P2: contains bounds and unsatisfiable forms" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"type\":\"array\",\"contains\":{\"type\":\"integer\"},\"minContains\":2,\"maxContains\":3}", &diag);
    defer g.deinit();
    const rep = switch (g.node(g.root).*) {
        .repeat => |r| r,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(rep.contains != null);
    try std.testing.expectEqual(@as(u32, 2), rep.min_contains);
    try std.testing.expectEqual(@as(u32, 3), rep.max_contains);
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"array\",\"contains\":{\"type\":\"integer\"},\"minContains\":3,\"maxContains\":2}", &diag));
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"array\",\"contains\":false}", &diag));
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"array\",\"contains\":{\"type\":\"integer\"},\"minContains\":2,\"maxItems\":1}", &diag));
}

test "spec-v1 P2: min/maxContains without contains are ignored" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"type\":\"array\",\"minContains\":5}", &diag);
    defer g.deinit();
    const rep = switch (g.node(g.root).*) {
        .repeat => |r| r,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(rep.contains == null);
}

test "spec-v1 P2: uniqueItems form and finite count" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"type\":\"array\",\"items\":{\"enum\":[\"a\",\"b\"]},\"uniqueItems\":true}", &diag);
    defer g.deinit();
    const rep = switch (g.node(g.root).*) {
        .repeat => |r| r,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(rep.unique);
    try std.testing.expectEqual(@as(u32, 2), rep.uniq_finite);
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"type\":\"array\",\"uniqueItems\":\"yes\"}", &diag));
}

test "spec-v1 P2: min/maxProperties are value-based integers" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"type\":\"object\",\"minProperties\":1.0,\"maxProperties\":2e0}", &diag);
    defer g.deinit();
    const on = switch (g.node(g.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(u32, 1), on.min_props);
    try std.testing.expectEqual(@as(u32, 2), on.max_props);
    try std.testing.expect(on.track_keys);
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"type\":\"object\",\"minProperties\":1.5}", &diag));
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"type\":\"object\",\"maxProperties\":-1}", &diag));
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"object\",\"minProperties\":3,\"maxProperties\":2}", &diag));
    // Closed object below the minimum: the arm is empty at the root.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"object\",\"properties\":{\"a\":{}},\"additionalProperties\":false,\"minProperties\":2}", &diag));
}

test "spec-v1 P2: propertyNames filters declared keys at compile time" {
    var diag: Diagnostic = .{};
    // The required declared key violates propertyNames: empty arm.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"object\",\"properties\":{\"abcd\":{}},\"required\":[\"abcd\"],\"propertyNames\":{\"maxLength\":3}}", &diag));
    // The optional declared key is dropped; the object arm is {}-only.
    var g = try compileSpec("{\"type\":\"object\",\"properties\":{\"abcd\":{}},\"additionalProperties\":false,\"propertyNames\":{\"maxLength\":3}}", &diag);
    defer g.deinit();
    const on = switch (g.node(g.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 0), on.props.len);
    // propertyNames false forbids every name.
    var g2 = try compileSpec("{\"type\":\"object\",\"propertyNames\":false}", &diag);
    defer g2.deinit();
    const on2 = switch (g2.node(g2.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(on2.names_forbidden);
}

test "spec-v1 P2: patternProperties literal subset and refusals" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"type\":\"object\",\"patternProperties\":{\"foo\":{\"type\":\"integer\"}}}", &diag);
    defer g.deinit();
    const on = switch (g.node(g.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(on.pattern_lit != null);
    try std.testing.expectEqualStrings("foo", g.literalBytes(on.pattern_lit.?));
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"patternProperties\":{\"a\":{},\"b\":{}}}", &diag));
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"properties\":{\"xa\":{}},\"patternProperties\":{\"a\":{}}}", &diag));
}

test "spec-v1 P4: pattern keyword and regex patternProperties" {
    var diag: Diagnostic = .{};
    // `pattern` on a string arm becomes a str_pat node carrying the DFA.
    var g = try compileSpec("{\"type\":\"string\",\"pattern\":\"^a+$\"}", &diag);
    defer g.deinit();
    switch (g.node(g.root).*) {
        .str_pat => |sp| {
            try std.testing.expectEqual(@as(u32, 0), sp.min_len);
            try std.testing.expect(try sp.dfa.matchesUtf8("aaa"));
            try std.testing.expect(!try sp.dfa.matchesUtf8("ab"));
        },
        else => return error.TestUnexpectedResult,
    }
    // Without `type` the pattern constrains only the string arm.
    var g2 = try compileSpec("{\"pattern\":\"x\"}", &diag);
    defer g2.deinit();
    // A regex patternProperties entry becomes pattern_dfa (not the literal
    // substring path); the value schema compiles as usual.
    var g3 = try compileSpec("{\"type\":\"object\",\"patternProperties\":{\"^x\":{\"type\":\"integer\"}}}", &diag);
    defer g3.deinit();
    const on3 = switch (g3.node(g3.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(on3.pattern_lit == null);
    try std.testing.expect(on3.pattern_dfa != null);
    // Refusals: non-string pattern value, invalid and unsupported regexes
    // (pointer to the keyword), a patternProperties regex overlapping a
    // declared property, and a never-matching pattern at the root.
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"type\":\"string\",\"pattern\":5}", &diag));
    try std.testing.expectEqualStrings("/pattern", diag.pointerText());
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"type\":\"string\",\"pattern\":\"(a\"}", &diag));
    try std.testing.expectEqualStrings("/pattern", diag.pointerText());
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"type\":\"string\",\"pattern\":\"a{1001}\"}", &diag));
    try std.testing.expectEqualStrings("/pattern", diag.pointerText());
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"type\":\"string\",\"pattern\":\"(?=a)b\"}", &diag));
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"properties\":{\"xa\":{}},\"patternProperties\":{\"^x\":{}}}", &diag));
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"string\",\"pattern\":\"a^b\"}", &diag));
    // Bounds contradictory to the pattern: empty arm at the root.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"string\",\"pattern\":\"^aaaa$\",\"maxLength\":3}", &diag));
    // pattern next to $ref is an assertion sibling (2020-12, P6b): the two
    // conjoin as an allOf comb.
    var g4 = try compileSpec("{\"$defs\":{\"d\":{\"type\":\"string\"}},\"$ref\":\"#/$defs/d\",\"pattern\":\"x\"}", &diag);
    defer g4.deinit();
    const cb4 = switch (g4.node(g4.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(cb4.kind == .allof);
    // canonical-v1 is frozen: `pattern` keeps refusing there.
    try std.testing.expectError(error.UnsupportedFeature, compileDiag("{\"type\":\"string\",\"pattern\":\"x\"}", &diag));
}

test "spec-v1 P2: dependencies normalization and reductions" {
    var diag: Diagnostic = .{};
    // dependentRequired: a -> b.
    var g = try compileSpec("{\"type\":\"object\",\"dependentRequired\":{\"a\":[\"b\"]}}", &diag);
    defer g.deinit();
    const on = switch (g.node(g.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 1), on.deps.len);
    try std.testing.expect(on.deps[0].kind == .required);
    try std.testing.expectEqualStrings("a", g.literalBytes(on.deps[0].trigger));
    try std.testing.expect(on.track_keys);
    // self-dependency is vacuous: no deps.
    var g2 = try compileSpec("{\"type\":\"object\",\"dependentRequired\":{\"a\":[\"a\"]}}", &diag);
    defer g2.deinit();
    const on2 = switch (g2.node(g2.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 0), on2.deps.len);
    // dependentSchemas false bans the trigger...
    var g3 = try compileSpec("{\"type\":\"object\",\"dependentSchemas\":{\"a\":false}}", &diag);
    defer g3.deinit();
    const on3 = switch (g3.node(g3.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 1), on3.deps.len);
    try std.testing.expect(on3.deps[0].kind == .ban);
    // ...and a banned required key empties the arm.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"object\",\"required\":[\"a\"],\"dependentSchemas\":{\"a\":false}}", &diag));
    // A schema-kind dep arms the whole-object capture.
    var g4 = try compileSpec("{\"type\":\"object\",\"dependentSchemas\":{\"a\":{\"properties\":{\"b\":{\"type\":\"integer\"}}}}}", &diag);
    defer g4.deinit();
    const on4 = switch (g4.node(g4.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(on4.deps[0].kind == .schema);
    try std.testing.expect(on4.capture);
    // Closed object: a required dep on an undeclared name bans the trigger.
    var g5 = try compileSpec("{\"type\":\"object\",\"properties\":{\"a\":{}},\"additionalProperties\":false,\"dependentRequired\":{\"a\":[\"zz\"]}}", &diag);
    defer g5.deinit();
    const on5 = switch (g5.node(g5.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 1), on5.deps.len);
    try std.testing.expect(on5.deps[0].kind == .ban);
}

test "spec-v1 P2: dependencies draft-04 form" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"$schema\":\"http://json-schema.org/draft-04/schema\",\"type\":\"object\",\"dependencies\":{\"a\":[\"b\"],\"c\":{\"properties\":{\"d\":{}}}}}", &diag);
    defer g.deinit();
    const on = switch (g.node(g.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), on.deps.len);
    try std.testing.expect(on.deps[0].kind == .required);
    try std.testing.expect(on.deps[1].kind == .schema);
}

test "spec-v1 P2: dialect-scoped keywords are ignored outside their dialect" {
    var diag: Diagnostic = .{};
    // propertyNames/contains are draft-06+: unknown in draft-04.
    var g = try compileSpec("{\"$schema\":\"http://json-schema.org/draft-04/schema\",\"propertyNames\":false,\"contains\":{}}", &diag);
    defer g.deinit();
    // prefixItems is 2020-12 only.
    var g2 = try compileSpec("{\"$schema\":\"http://json-schema.org/draft-07/schema\",\"prefixItems\":[false]}", &diag);
    defer g2.deinit();
    // dependentRequired/minContains are 2019-09+.
    var g3 = try compileSpec("{\"$schema\":\"http://json-schema.org/draft-07/schema\",\"dependentRequired\":{\"a\":[\"b\"]},\"minContains\":9}", &diag);
    defer g3.deinit();
    // dependencies is pre-2019-09 only.
    var g4 = try compileSpec("{\"dependencies\":{\"a\":[\"b\"]}}", &diag);
    defer g4.deinit();
    // additionalItems is unknown in 2020-12.
    var g5 = try compileSpec("{\"additionalItems\":false}", &diag);
    defer g5.deinit();
}

test "spec-v1 P2: additionalProperties schema constrains undeclared values" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"type\":\"object\",\"properties\":{\"a\":{}},\"additionalProperties\":{\"type\":\"integer\"}}", &diag);
    defer g.deinit();
    const on = switch (g.node(g.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(g.node(on.value).* == .int_num);
    // An unsatisfiable additionalProperties schema closes the object.
    var g2 = try compileSpec("{\"type\":\"object\",\"properties\":{\"a\":{}},\"additionalProperties\":false}", &diag);
    defer g2.deinit();
    try std.testing.expect(g2.node(g2.root).* == .object);
    var g3 = try compileSpec("{\"type\":\"object\",\"properties\":{\"a\":{}},\"additionalProperties\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":0}}", &diag);
    defer g3.deinit();
    const on3 = switch (g3.node(g3.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    // The undeclared-value schema is the empty-language node: rejected at
    // dispatch, so the object behaves as closed.
    const v3 = switch (g3.node(on3.value).*) {
        .str => |sc| sc,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(v3.min_len > v3.max_len);
}

test "spec-v1 P3: anyOf compiles to a plain union" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"anyOf\":[{\"type\":\"string\"},{\"type\":\"integer\"}]}", &diag);
    defer g.deinit();
    const alts = switch (g.node(g.root).*) {
        .choice => |a| a,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), alts.len);
    try std.testing.expect(g.node(alts[0]).* == .str);
    try std.testing.expect(g.node(alts[1]).* == .int_num);
    // Empty-language branches drop out of the union.
    var g2 = try compileSpec("{\"anyOf\":[false,{\"type\":\"integer\"}]}", &diag);
    defer g2.deinit();
    try std.testing.expect(g2.node(g2.root).* == .int_num);
    // All branches empty: the root refuses.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"anyOf\":[false]}", &diag));
    // anyOf must be a non-empty array.
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"anyOf\":[]}", &diag));
}

test "spec-v1 P3: oneOf and allOf compile to comb nodes" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"oneOf\":[{\"type\":\"number\"},{\"type\":\"integer\"}]}", &diag);
    defer g.deinit();
    const cb = switch (g.node(g.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(cb.kind == .oneof);
    try std.testing.expectEqual(@as(usize, 2), cb.branches.len);
    try std.testing.expect(g.node(cb.branches[0]).* == .num_v);
    try std.testing.expect(g.node(cb.branches[1]).* == .int_num);
    // Exactly-one over a single live branch is the branch itself.
    var g2 = try compileSpec("{\"oneOf\":[false,{\"type\":\"integer\"}]}", &diag);
    defer g2.deinit();
    try std.testing.expect(g2.node(g2.root).* == .int_num);
    // allOf keeps every branch.
    var g3 = try compileSpec("{\"allOf\":[{\"type\":\"integer\"},{\"const\":5}]}", &diag);
    defer g3.deinit();
    const cb3 = switch (g3.node(g3.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(cb3.kind == .allof);
    try std.testing.expectEqual(@as(usize, 2), cb3.branches.len);
    try std.testing.expect(g3.node(cb3.branches[0]).* == .int_num);
    try std.testing.expect(g3.node(cb3.branches[1]).* == .num_const);
    // An empty-language branch empties the whole conjunction.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"allOf\":[{\"type\":\"integer\"},{\"type\":\"string\",\"minLength\":1,\"maxLength\":0}]}", &diag));
}

// ---- spec-v1: enum/const sibling assertions and value-based combinator branches ----

test "spec-v1 R2: const next to enum intersects by value" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"enum\":[1,2,3],\"const\":2}", &diag);
    defer g.deinit();
    const nc = switch (g.node(g.root).*) {
        .num_const => |nc| nc,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqualStrings("2", nc.digits);
    // An empty intersection is the empty-language refusal, not a weakening.
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"enum\":[1,2],\"const\":3}", &diag));
    // Object values compare key-order-insensitively.
    diag = .{};
    var g2 = try compileSpec("{\"enum\":[{\"b\":2,\"a\":1}],\"const\":{\"a\":1,\"b\":2}}", &diag);
    defer g2.deinit();
    try std.testing.expect(g2.node(g2.root).* == .object);
}

test "spec-v1 R2: const object honours sibling object assertions" {
    var diag: Diagnostic = .{};
    // required names missing from the const value empty the language.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"const\":{\"a\":1},\"required\":[\"b\"]}", &diag));
    diag = .{};
    // ...while a satisfied required keeps the value.
    var g = try compileSpec("{\"const\":{\"a\":1},\"required\":[\"a\"]}", &diag);
    defer g.deinit();
    try std.testing.expect(g.node(g.root).* == .object);
    // maxProperties, additionalProperties and propertyNames all apply.
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"const\":{\"a\":1,\"b\":2},\"maxProperties\":1}", &diag));
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"const\":{\"a\":1},\"additionalProperties\":false}", &diag));
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"const\":{\"a\":1},\"propertyNames\":{\"enum\":[\"b\"]}}", &diag));
    diag = .{};
    // dependentRequired / dependentSchemas with a present trigger apply.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"const\":{\"a\":1},\"dependentRequired\":{\"a\":[\"b\"]}}", &diag));
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"const\":{\"a\":1},\"dependentSchemas\":{\"a\":{\"required\":[\"b\"]}}}", &diag));
    diag = .{};
    // properties constrains a present key's value.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"const\":{\"a\":\"x\"},\"properties\":{\"a\":{\"type\":\"integer\"}}}", &diag));
}

test "spec-v1 R2: enum arrays honour sibling array assertions" {
    var diag: Diagnostic = .{};
    // uniqueItems is value-based: [1,1.0] is a duplicate.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"const\":[1,1.0],\"uniqueItems\":true}", &diag));
    diag = .{};
    // min/maxItems filter the value set.
    var g = try compileSpec("{\"enum\":[[1],[1,2],[1,2,3]],\"minItems\":2,\"maxItems\":2}", &diag);
    defer g.deinit();
    const seq = switch (g.node(g.root).*) {
        .seq => |s| s,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 5), seq.len); // [ 1 , 2 ]
    // contains / items / prefixItems filter as well.
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"const\":[1,2],\"contains\":{\"const\":3}}", &diag));
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"enum\":[[1,\"x\"]],\"items\":{\"type\":\"integer\"}}", &diag));
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"const\":[1,\"x\"],\"prefixItems\":[{\"type\":\"integer\"},{\"type\":\"integer\"}]}", &diag));
    diag = .{};
    // additionalItems: false caps a tuple.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"$schema\":\"http://json-schema.org/draft-07/schema#\",\"const\":[1,2,3],\"items\":[{}],\"additionalItems\":false}", &diag));
}

test "spec-v1 R2: undecidable sibling combinations refuse with a pointer" {
    var diag: Diagnostic = .{};
    // A $ref inside a sibling applicator has no static evaluation.
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"$defs\":{\"p\":{\"type\":\"integer\"}},\"const\":{\"a\":1},\"properties\":{\"a\":{\"$ref\":\"#/$defs/p\"}}}", &diag));
    diag = .{};
    // Multiple patternProperties keep the compiled-path refusal.
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"const\":{\"a\":1},\"patternProperties\":{\"x\":{},\"y\":{}}}", &diag));
    diag = .{};
    // unevaluatedProperties inside a sibling applicator is undecidable here.
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"const\":{\"a\":{\"b\":1}},\"properties\":{\"a\":{\"unevaluatedProperties\":false}}}", &diag));
}

test "spec-v1 R3: oneOf counts equal const objects by value" {
    var diag: Diagnostic = .{};
    // Both branches accept the same value: no value satisfies exactly one.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"oneOf\":[{\"const\":{\"a\":1,\"b\":2}},{\"const\":{\"b\":2,\"a\":1}}]}", &diag));
    diag = .{};
    // A distinct third branch survives; the duplicated value drops out.
    var g = try compileSpec("{\"oneOf\":[{\"const\":{\"a\":1,\"b\":2}},{\"const\":{\"b\":2,\"a\":1}},{\"const\":{\"c\":3}}]}", &diag);
    defer g.deinit();
    try std.testing.expect(g.node(g.root).* == .object);
    // Scalar duplicates were and stay counted correctly by the comb.
    diag = .{};
    var g2 = try compileSpec("{\"oneOf\":[{\"const\":1},{\"const\":2}]}", &diag);
    defer g2.deinit();
    const cb = switch (g2.node(g2.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(cb.kind == .oneof);
    // A duplicated value also accepted by a non-constant branch cannot be
    // subtracted: exact refusal with the oneOf pointer.
    diag = .{};
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"oneOf\":[{\"const\":{\"a\":1,\"b\":2}},{\"const\":{\"b\":2,\"a\":1}},{\"type\":\"object\"}]}", &diag));
    try std.testing.expectEqualStrings("/oneOf", diag.pointerText());
    // A non-constant branch that REJECTS the duplicated value stays exact:
    // dropping the duplicate from the constant branches is the whole fix.
    diag = .{};
    var g3 = try compileSpec("{\"oneOf\":[{\"const\":{\"a\":1,\"b\":2}},{\"const\":{\"b\":2,\"a\":1}},{\"type\":\"object\",\"properties\":{\"c\":{}},\"required\":[\"c\"]}]}", &diag);
    defer g3.deinit();
    try std.testing.expect(g3.node(g3.root).* == .open_obj);
}

test "spec-v1 R3: allOf object branches with contradictory key orders merge by value" {
    var diag: Diagnostic = .{};
    // The two branches declare a,b in opposite orders: merged into one open
    // object in first-appearance order (semantics-spec-v1 4.3).
    var g = try compileSpec("{\"allOf\":[{\"type\":\"object\",\"properties\":{\"a\":{},\"b\":{}},\"required\":[\"a\",\"b\"]},{\"type\":\"object\",\"properties\":{\"b\":{},\"a\":{}},\"required\":[\"a\",\"b\"]}]}", &diag);
    defer g.deinit();
    const on = switch (g.node(g.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), on.props.len);
    try std.testing.expectEqualStrings("\"a\":", g.literalBytes(on.props[0].key));
    try std.testing.expectEqualStrings("\"b\":", g.literalBytes(on.props[1].key));
    try std.testing.expect(on.props[0].required and on.props[1].required);
    // Consistent orders keep the plain comb (no merge needed).
    diag = .{};
    var g2 = try compileSpec("{\"allOf\":[{\"type\":\"object\",\"properties\":{\"a\":{},\"b\":{}}},{\"type\":\"object\",\"properties\":{\"a\":{},\"b\":{}}}]}", &diag);
    defer g2.deinit();
    try std.testing.expect(g2.node(g2.root).* == .comb);
    // A conflict involving a closed object cannot merge: exact refusal.
    diag = .{};
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"allOf\":[{\"type\":\"object\",\"properties\":{\"a\":{},\"b\":{}},\"additionalProperties\":false},{\"type\":\"object\",\"properties\":{\"b\":{},\"a\":{}}}]}", &diag));
}

test "spec-v1 R3: allOf of equal const objects intersects by value" {
    var diag: Diagnostic = .{};
    // The same value spelled with two key orders: the intersection is the
    // value, compiled once in its first-spelling order.
    var g = try compileSpec("{\"allOf\":[{\"const\":{\"a\":1,\"b\":2}},{\"const\":{\"b\":2,\"a\":1}}]}", &diag);
    defer g.deinit();
    const props = switch (g.node(g.root).*) {
        .object => |o| o.props,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), props.len);
    try std.testing.expectEqualStrings("\"a\":", g.literalBytes(props[0].key));
    // A disjoint second const empties the intersection.
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"allOf\":[{\"const\":{\"a\":1,\"b\":2}},{\"const\":{\"c\":3}}]}", &diag));
    // A pure branch filtered against a non-constant branch.
    diag = .{};
    var g2 = try compileSpec("{\"allOf\":[{\"enum\":[{\"a\":1,\"b\":2},{\"c\":3}]},{\"type\":\"object\",\"required\":[\"c\"]}]}", &diag);
    defer g2.deinit();
    const cb = switch (g2.node(g2.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(cb.kind == .allof);
    // The first branch is the intersected survivor {"c":3}, a closed object.
    try std.testing.expect(g2.node(cb.branches[0]).* == .object);
}

test "spec-v1 P3: if/then/else compiles to a three-slot comb" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"if\":{\"const\":0},\"then\":{\"const\":1},\"else\":{\"type\":\"number\"}}", &diag);
    defer g.deinit();
    const cb = switch (g.node(g.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(cb.kind == .ifelse);
    try std.testing.expectEqual(@as(usize, 3), cb.branches.len);
    try std.testing.expect(g.node(cb.branches[0]).* == .num_const);
    try std.testing.expect(g.node(cb.branches[1]).* == .num_const);
    try std.testing.expect(g.node(cb.branches[2]).* == .num_v);
    // An absent else compiles to an AnyJSON carrier branch, never
    // COMB_NONE: the group needs a live thread tracking the extent of a
    // value the if/then branches cannot parse.
    var g2 = try compileSpec("{\"if\":{\"const\":0},\"then\":{\"const\":1}}", &diag);
    defer g2.deinit();
    const cb2 = switch (g2.node(g2.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(cb2.kind == .ifelse);
    try std.testing.expect(cb2.branches[2] != grammar.COMB_NONE);
    // then/else without if (and if alone) are inert: AnyJSON.
    var g3 = try compileSpec("{\"then\":{\"const\":1}}", &diag);
    defer g3.deinit();
    try std.testing.expect(g3.node(g3.root).* == .choice);
    var g4 = try compileSpec("{\"if\":{\"const\":0}}", &diag);
    defer g4.deinit();
    try std.testing.expect(g4.node(g4.root).* == .choice);
    // if/then/else are draft-07+: unknown (ignored) in draft-04.
    var g5 = try compileSpec("{\"$schema\":\"http://json-schema.org/draft-04/schema\",\"if\":false,\"then\":false}", &diag);
    defer g5.deinit();
    try std.testing.expect(g5.node(g5.root).* == .choice);
}

test "spec-v1 P3: not over type/const/enum/required" {
    var diag: Diagnostic = .{};
    // not {type:integer}: every type but integer; the number arm keeps
    // the non-integer values via not_int_num.
    var g = try compileSpec("{\"not\":{\"type\":\"integer\"}}", &diag);
    defer g.deinit();
    const alts = switch (g.node(g.root).*) {
        .choice => |a| a,
        else => return error.TestUnexpectedResult,
    };
    var has_not_int_num = false;
    for (alts) |id| {
        try std.testing.expect(g.node(id).* != .int_num);
        if (g.node(id).* == .not_int_num) has_not_int_num = true;
    }
    try std.testing.expect(has_not_int_num);
    // not {const:"ab"}: the string arm forbids exactly "ab".
    var g2 = try compileSpec("{\"not\":{\"const\":\"ab\"}}", &diag);
    defer g2.deinit();
    const alts2 = switch (g2.node(g2.root).*) {
        .choice => |a| a,
        else => return error.TestUnexpectedResult,
    };
    var has_str_excl = false;
    for (alts2) |id| {
        if (g2.node(id).* == .str_excl) has_str_excl = true;
    }
    try std.testing.expect(has_str_excl);
    // not {const:5}: the number arm forbids exactly 5.
    var g3 = try compileSpec("{\"not\":{\"const\":5}}", &diag);
    defer g3.deinit();
    const alts3 = switch (g3.node(g3.root).*) {
        .choice => |a| a,
        else => return error.TestUnexpectedResult,
    };
    var has_num_excl = false;
    for (alts3) |id| {
        if (g3.node(id).* == .num_excl) has_num_excl = true;
    }
    try std.testing.expect(has_num_excl);
    // not {required:["a"]}: exactly the objects without the key.
    var g4 = try compileSpec("{\"not\":{\"required\":[\"a\"]}}", &diag);
    defer g4.deinit();
    const o4 = switch (g4.node(g4.root).*) {
        .open_obj => |o| o,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 1), o4.deps.len);
    try std.testing.expect(o4.deps[0].kind == .ban);
    // Double negation returns the inner schema.
    var g5 = try compileSpec("{\"not\":{\"not\":{\"type\":\"integer\"}}}", &diag);
    defer g5.deinit();
    try std.testing.expect(g5.node(g5.root).* == .int_num);
    // not {} and not true are empty languages: refused at the root.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"not\":{}}", &diag));
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"not\":true}", &diag));
}

test "spec-v1 P3: not refusals are UNSUPPORTED_FEATURE with a pointer" {
    var diag: Diagnostic = .{};
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"not\":{\"pattern\":\"x\"}}", &diag));
    try std.testing.expectEqualStrings("/not/pattern", diag.pointerText());
    diag = .{};
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"not\":{\"enum\":[1,2,3]}}", &diag));
    try std.testing.expectEqualStrings("/not/enum", diag.pointerText());
    diag = .{};
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"not\":{\"enum\":[[1]]}}", &diag));
    try std.testing.expectEqualStrings("/not/enum", diag.pointerText());
    diag = .{};
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"not\":{\"type\":\"object\",\"minLength\":1}}", &diag));
    try std.testing.expectEqualStrings("/not/type", diag.pointerText());
}

test "spec-v1 P6b: assertion keywords next to $ref conjoin (2020-12 sibling rule)" {
    var diag: Diagnostic = .{};
    // 2020-12: $ref applies alongside its siblings - the ref expansion and
    // the sibling assertions compile independently and conjoin (allOf comb).
    const s = "{\"$defs\":{\"d\":{\"type\":\"integer\"}},\"$ref\":\"#/$defs/d\",\"anyOf\":[{\"type\":\"string\"}]}";
    var g = try compileSpec(s, &diag);
    defer g.deinit();
    const cb = switch (g.node(g.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(cb.kind == .allof);
    try std.testing.expectEqual(@as(usize, 2), cb.branches.len);
    // draft-07 ignores the siblings: the ref expansion stands alone.
    var g2 = try compileSpec("{\"$schema\":\"http://json-schema.org/draft-07/schema#\",\"$defs\":{\"d\":{\"type\":\"integer\"}},\"$ref\":\"#/definitions/d\",\"definitions\":{\"d\":{\"type\":\"integer\"}},\"anyOf\":[{\"type\":\"string\"}]}", &diag);
    defer g2.deinit();
    try std.testing.expect(g2.node(g2.root).* == .int_num);
}

test "spec-v1 P3: combinator parts conjoin with the typed core" {
    var diag: Diagnostic = .{};
    // type + oneOf: the parts become an allOf comb.
    var g = try compileSpec("{\"type\":\"number\",\"oneOf\":[{\"type\":\"integer\"},{\"const\":1.5}]}", &diag);
    defer g.deinit();
    const cb = switch (g.node(g.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(cb.kind == .allof);
    try std.testing.expectEqual(@as(usize, 2), cb.branches.len);
    try std.testing.expect(g.node(cb.branches[0]).* == .num_v);
    try std.testing.expect(g.node(cb.branches[1]).* == .comb);
    // An empty part empties the whole conjunction at compile time.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"integer\",\"not\":true}", &diag));
}

// ---- spec-v1 P4: numeric range and divisibility compilation ----

test "spec-v1 P4: minimum/maximum compile to a num_range arm" {
    var diag: Diagnostic = .{};
    // No type: every type arm; the numeric arms carry the range through a
    // comb, the other arms are unconstrained (applicability).
    var g = try compileSpec("{\"minimum\":-2,\"maximum\":3.0}", &diag);
    defer g.deinit();
    const alts = switch (g.node(g.root).*) {
        .choice => |c| c,
        else => return error.TestUnexpectedResult,
    };
    var ranged: u32 = 0;
    var plain: u32 = 0;
    for (alts) |id| {
        switch (g.node(id).*) {
            .comb => |cb| {
                try std.testing.expect(cb.kind == .allof);
                try std.testing.expectEqual(@as(usize, 2), cb.branches.len);
                const base = g.node(cb.branches[0]).*;
                try std.testing.expect(base == .int_num or base == .num_v);
                const nr = switch (g.node(cb.branches[1]).*) {
                    .num_range => |nr| nr,
                    else => return error.TestUnexpectedResult,
                };
                try std.testing.expect(nr.min != null and nr.min.?.neg);
                try std.testing.expectEqualStrings("2", nr.min.?.digits);
                try std.testing.expectEqualStrings("3", nr.max.?.digits);
                try std.testing.expect(!nr.min_excl and !nr.max_excl);
                ranged += 1;
            },
            else => plain += 1,
        }
    }
    try std.testing.expectEqual(@as(u32, 2), ranged); // integer and number arms
    try std.testing.expectEqual(@as(u32, 5), plain);
}

test "spec-v1 P4: exclusive bounds and dialect normalization" {
    var diag: Diagnostic = .{};
    // 2020-12 standalone exclusiveMinimum.
    var g = try compileSpec("{\"type\":\"number\",\"exclusiveMinimum\":1.1}", &diag);
    defer g.deinit();
    const cb = switch (g.node(g.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    const nr = switch (g.node(cb.branches[1]).*) {
        .num_range => |n| n,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(nr.min_excl);
    try std.testing.expectEqualStrings("11", nr.min.?.digits);
    try std.testing.expectEqual(@as(i64, -1), nr.min.?.exp10);
    // minimum conjoined with a standalone exclusiveMinimum: the tighter
    // (larger) bound wins; equal values keep the exclusive form.
    diag = .{};
    var g2 = try compileSpec("{\"type\":\"number\",\"minimum\":2,\"exclusiveMinimum\":1.5}", &diag);
    defer g2.deinit();
    const cb2 = switch (g2.node(g2.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    const nr2 = switch (g2.node(cb2.branches[1]).*) {
        .num_range => |n| n,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqualStrings("2", nr2.min.?.digits);
    try std.testing.expect(!nr2.min_excl);
    diag = .{};
    var g3 = try compileSpec("{\"type\":\"number\",\"minimum\":1.5,\"exclusiveMinimum\":1.5}", &diag);
    defer g3.deinit();
    const cb3 = switch (g3.node(g3.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    const nr3 = switch (g3.node(cb3.branches[1]).*) {
        .num_range => |n| n,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(nr3.min_excl);
    // draft-04: boolean modifier over minimum; without the bound it drops.
    diag = .{};
    var g4 = try compileSpec("{\"$schema\":\"http://json-schema.org/draft-04/schema#\",\"type\":\"number\",\"minimum\":3,\"exclusiveMinimum\":true}", &diag);
    defer g4.deinit();
    const cb4 = switch (g4.node(g4.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    const nr4 = switch (g4.node(cb4.branches[1]).*) {
        .num_range => |n| n,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(nr4.min_excl);
    try std.testing.expectEqualStrings("3", nr4.min.?.digits);
    diag = .{};
    var g5 = try compileSpec("{\"$schema\":\"http://json-schema.org/draft-04/schema#\",\"type\":\"number\",\"exclusiveMinimum\":true}", &diag);
    defer g5.deinit();
    try std.testing.expect(g5.node(g5.root).* == .num_v); // dropped: no constraint
}

test "spec-v1 P4: multipleOf compiles to a num_mult arm" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"type\":\"integer\",\"multipleOf\":2}", &diag);
    defer g.deinit();
    const cb = switch (g.node(g.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expect(cb.kind == .allof);
    try std.testing.expect(g.node(cb.branches[0]).* == .int_num);
    const nm = switch (g.node(cb.branches[1]).*) {
        .num_mult => |n| n,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(u32, 2), nm.div);
    try std.testing.expectEqual(@as(i64, 0), nm.div_exp10);
    // Decimal divisor: 1.5 -> div 15, exp10 -1, co 3.
    diag = .{};
    var g2 = try compileSpec("{\"multipleOf\":1.5}", &diag);
    defer g2.deinit();
    const alts = switch (g2.node(g2.root).*) {
        .choice => |c| c,
        else => return error.TestUnexpectedResult,
    };
    var found = false;
    for (alts) |id| {
        switch (g2.node(id).*) {
            .comb => |cb2| {
                const nm2 = switch (g2.node(cb2.branches[1]).*) {
                    .num_mult => |n| n,
                    else => return error.TestUnexpectedResult,
                };
                try std.testing.expectEqual(@as(u32, 15), nm2.div);
                try std.testing.expectEqual(@as(i64, -1), nm2.div_exp10);
                try std.testing.expectEqual(@as(u32, 3), nm2.co);
                found = true;
            },
            else => {},
        }
    }
    try std.testing.expect(found);
    // minimum + multipleOf: three-way comb.
    diag = .{};
    var g3 = try compileSpec("{\"type\":\"number\",\"minimum\":5,\"multipleOf\":2}", &diag);
    defer g3.deinit();
    const cb3 = switch (g3.node(g3.root).*) {
        .comb => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 3), cb3.branches.len);
    try std.testing.expect(g3.node(cb3.branches[1]).* == .num_range);
    try std.testing.expect(g3.node(cb3.branches[2]).* == .num_mult);
}

test "spec-v1 P4: numeric keyword validation errors carry a pointer" {
    var diag: Diagnostic = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"minimum\":\"5\"}", &diag));
    try std.testing.expectEqualStrings("/minimum", diag.pointerText());
    diag = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"multipleOf\":0}", &diag));
    try std.testing.expectEqualStrings("/multipleOf", diag.pointerText());
    diag = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"multipleOf\":-2}", &diag));
    diag = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"type\":\"object\",\"properties\":{\"a\":{\"maximum\":true}}}", &diag));
    try std.testing.expectEqualStrings("/properties/a/maximum", diag.pointerText());
    diag = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"$schema\":\"http://json-schema.org/draft-04/schema#\",\"exclusiveMaximum\":3}", &diag));
    // Divisors over 10 significant digits: refused with a pointer, never
    // approximated (A6).
    diag = .{};
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"multipleOf\":12345678901}", &diag));
    try std.testing.expectEqualStrings("/multipleOf", diag.pointerText());
}

test "spec-v1 P4: contradictory bounds empty the numeric arms" {
    var diag: Diagnostic = .{};
    // minimum > maximum: the numeric arms drop; with a numeric-only type
    // the root is a refusal, in a union the other arms survive.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"number\",\"minimum\":5,\"maximum\":3}", &diag));
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"integer\",\"minimum\":5,\"maximum\":5,\"exclusiveMaximum\":5}", &diag));
    diag = .{};
    var g = try compileSpec("{\"type\":[\"string\",\"number\"],\"minimum\":5,\"maximum\":3}", &diag);
    defer g.deinit();
    try std.testing.expect(g.node(g.root).* == .str); // only the string arm survives
}

test "spec-v1 P4: numeric assertion filters enum/const numbers" {
    var diag: Diagnostic = .{};
    // Range filter: 1 drops, 2.5 and "a" stay (inapplicable to strings).
    var g = try compileSpec("{\"enum\":[1,2.5,\"a\"],\"minimum\":2}", &diag);
    defer g.deinit();
    const alts = switch (g.node(g.root).*) {
        .choice => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), alts.len);
    // multipleOf filter.
    diag = .{};
    var g2 = try compileSpec("{\"enum\":[2,3,4],\"multipleOf\":2}", &diag);
    defer g2.deinit();
    const alts2 = switch (g2.node(g2.root).*) {
        .choice => |c| c,
        else => return error.TestUnexpectedResult,
    };
    try std.testing.expectEqual(@as(usize, 2), alts2.len);
    for (alts2) |id| try std.testing.expect(g2.node(id).* == .num_const);
    // Everything filtered: a refusal at the root.
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"enum\":[1,3],\"multipleOf\":2}", &diag));
    // const: kept when it satisfies the assertion.
    diag = .{};
    var g3 = try compileSpec("{\"const\":1.0,\"minimum\":1}", &diag);
    defer g3.deinit();
    try std.testing.expect(g3.node(g3.root).* == .num_const);
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"const\":0.5,\"minimum\":1}", &diag));
}

test "spec-v1 P4: canonical-v1 keeps refusing the numeric keywords" {
    var diag: Diagnostic = .{};
    var w = work_mod.Work{};
    try std.testing.expectError(error.UnsupportedFeature, compile(std.testing.allocator, "{\"minimum\":3}", 64, &diag, &w, false, .canonical_v1, &.{}));
    diag = .{};
    try std.testing.expectError(error.UnsupportedFeature, compile(std.testing.allocator, "{\"multipleOf\":2}", 64, &diag, &w, false, .canonical_v1, &.{}));
    diag = .{};
    try std.testing.expectError(error.UnsupportedFeature, compile(std.testing.allocator, "{\"exclusiveMaximum\":3}", 64, &diag, &w, false, .canonical_v1, &.{}));
}

test "spec-v1 P5: recursive local $ref compiles (bounded unrolling, ADR-0008)" {
    var diag: Diagnostic = .{};
    // Linked list: the root pointer cycle no longer refuses.
    var g = try compileSpec("{\"type\":\"object\",\"properties\":{\"v\":{\"type\":\"integer\"},\"next\":{\"$ref\":\"#\"}},\"required\":[\"v\"],\"additionalProperties\":false}", &diag);
    defer g.deinit();
    try std.testing.expect(g.node(g.root).* == .open_obj or g.node(g.root).* == .object);
    // Mutual recursion through $defs compiles too.
    diag = .{};
    var g2 = try compileSpec("{\"$defs\":{\"a\":{\"type\":\"object\",\"properties\":{\"b\":{\"$ref\":\"#/$defs/b\"}}},\"b\":{\"type\":\"object\",\"properties\":{\"a\":{\"$ref\":\"#/$defs/a\"}}}},\"$ref\":\"#/$defs/a\"}", &diag);
    defer g2.deinit();
    // Recursion through a plain-name anchor.
    diag = .{};
    var g3 = try compileSpec("{\"$anchor\":\"node\",\"type\":\"array\",\"items\":{\"$ref\":\"#node\"}}", &diag);
    defer g3.deinit();
    // canonical-v1 is frozen: a cycle stays INVALID_SCHEMA there.
    diag = .{};
    var w = work_mod.Work{};
    try std.testing.expectError(error.InvalidSchema, compile(std.testing.allocator, "{\"$defs\":{\"a\":{\"$ref\":\"#/$defs/b\"},\"b\":{\"$ref\":\"#/$defs/a\"}},\"$ref\":\"#/$defs/a\"}", 64, &diag, &w, false, .canonical_v1, &.{}));
}

test "spec-v1 P5: unproductive recursion is the empty-language refusal" {
    var diag: Diagnostic = .{};
    // A $ref cycle with no base case bottoms out at the empty language
    // everywhere: the whole schema is unsatisfiable.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"$ref\":\"#\"}", &diag));
    diag = .{};
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"$defs\":{\"a\":{\"$ref\":\"#/$defs/b\"},\"b\":{\"$ref\":\"#/$defs/a\"}},\"$ref\":\"#/$defs/a\"}", &diag));
    diag = .{};
    // A required recursive property can never be satisfied either.
    try std.testing.expectError(error.UnsatisfiableConstraint, compileSpec("{\"type\":\"object\",\"properties\":{\"next\":{\"$ref\":\"#\"}},\"required\":[\"next\"],\"additionalProperties\":false}", &diag));
}

test "spec-v1 P5: recursion truncation at the unroll limit is the empty-language node" {
    var diag: Diagnostic = .{};
    // At the bottom of the unroll budget the recursive position compiles
    // to the empty-language node shape (str with min > max, ADR-0006 D1).
    var g = try compileSpec("{\"type\":\"array\",\"items\":{\"$ref\":\"#\"}}", &diag);
    defer g.deinit();
    var found_empty = false;
    for (g.nodes) |n| {
        switch (n) {
            .str => |sc| {
                if (sc.min_len > sc.max_len) found_empty = true;
            },
            else => {},
        }
    }
    try std.testing.expect(found_empty);
}

test "spec-v1 P5: depth budget inside recursion truncates instead of refusing" {
    var diag: Diagnostic = .{};
    var w = work_mod.Work{};
    // max_depth 6 is exceeded by the unrolled recursion: the position
    // truncates to the empty language rather than failing RESOURCE_LIMIT.
    var g = try compile(std.testing.allocator, "{\"type\":\"object\",\"properties\":{\"next\":{\"$ref\":\"#\"}}}", 6, &diag, &w, false, .spec_v1, &.{});
    defer g.deinit();
    // Outside recursion the same overflow stays a refusal.
    diag = .{};
    w = work_mod.Work{};
    try std.testing.expectError(error.ResourceLimit, compile(std.testing.allocator, "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"object\",\"properties\":{\"b\":{\"type\":\"object\",\"properties\":{\"c\":{\"type\":\"object\",\"properties\":{\"d\":{\"type\":\"object\"}}}}}}}}}", 4, &diag, &w, false, .spec_v1, &.{}));
}

test "spec-v1 P5: URI-fragment percent-decoding in $ref" {
    var diag: Diagnostic = .{};
    // RFC 6901 section 6: '%XX' decodes before the '~' unescaping.
    var g = try compileSpec("{\"$defs\":{\"percent%field\":{\"type\":\"integer\"}},\"$ref\":\"#/$defs/percent%25field\"}", &diag);
    defer g.deinit();
    diag = .{};
    var g2 = try compileSpec("{\"$defs\":{\"a b\":{\"type\":\"string\"}},\"$ref\":\"#/$defs/a%20b\"}", &diag);
    defer g2.deinit();
    // A malformed '%' escape is INVALID_SCHEMA.
    diag = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"$defs\":{\"a\":{\"type\":\"string\"}},\"$ref\":\"#/$defs/a%2\"}", &diag));
    // An undecodable name stays unresolved.
    diag = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"$defs\":{\"a\":{\"type\":\"string\"}},\"$ref\":\"#/$defs/a%25b\"}", &diag));
}

test "spec-v1 P5: external $ref through the registry snapshot (ADR-0006 D5)" {
    var diag: Diagnostic = .{};
    var w = work_mod.Work{};
    const reg = "{\"version\":\"v1\",\"documents\":{\"http://ex.com/int.json\":{\"type\":\"integer\"},\"http://ex.com/sub.json\":{\"$defs\":{\"s\":{\"type\":\"string\"}},\"$anchor\":\"top\",\"type\":\"boolean\"}}}";
    // Whole document, fragment pointer and anchor all resolve.
    var g = try compile(std.testing.allocator, "{\"$ref\":\"http://ex.com/int.json\"}", 64, &diag, &w, false, .spec_v1, reg);
    defer g.deinit();
    diag = .{};
    w = work_mod.Work{};
    var g2 = try compile(std.testing.allocator, "{\"$ref\":\"http://ex.com/sub.json#/$defs/s\"}", 64, &diag, &w, false, .spec_v1, reg);
    defer g2.deinit();
    diag = .{};
    w = work_mod.Work{};
    var g3 = try compile(std.testing.allocator, "{\"$ref\":\"http://ex.com/sub.json#top\"}", 64, &diag, &w, false, .spec_v1, reg);
    defer g3.deinit();
    // The registry bytes join the grammar identity: a snapshot swap is a
    // different grammar for the mask cache and the artifact key.
    try std.testing.expect(g.id != g2.id or g.id_hi != g2.id_hi);
    diag = .{};
    w = work_mod.Work{};
    const reg2 = "{\"version\":\"v2\",\"documents\":{\"http://ex.com/int.json\":{\"type\":\"string\"}}}";
    var g4 = try compile(std.testing.allocator, "{\"$ref\":\"http://ex.com/int.json\"}", 64, &diag, &w, false, .spec_v1, reg2);
    defer g4.deinit();
    try std.testing.expect(g.id != g4.id or g.id_hi != g4.id_hi);
}

test "spec-v1 P5: registry refusals carry status and pointer" {
    var diag: Diagnostic = .{};
    var w = work_mod.Work{};
    const reg = "{\"documents\":{\"http://ex.com/int.json\":{\"type\":\"integer\"}}}";
    // Unknown base URI: UNSUPPORTED_FEATURE at the $ref pointer.
    try std.testing.expectError(error.UnsupportedFeature, compile(std.testing.allocator, "{\"properties\":{\"x\":{\"$ref\":\"http://ex.com/missing.json\"}}}", 64, &diag, &w, false, .spec_v1, reg));
    try std.testing.expectEqualStrings("/properties/x", diag.pointerText());
    // Malformed registry shapes: INVALID_SCHEMA.
    diag = .{};
    w = work_mod.Work{};
    try std.testing.expectError(error.InvalidSchema, compile(std.testing.allocator, "{\"type\":\"integer\"}", 64, &diag, &w, false, .spec_v1, "[]"));
    diag = .{};
    w = work_mod.Work{};
    try std.testing.expectError(error.InvalidSchema, compile(std.testing.allocator, "{\"type\":\"integer\"}", 64, &diag, &w, false, .spec_v1, "{\"version\":\"v1\"}"));
    // A registry with the canonical-v1 profile: UNSUPPORTED_FEATURE.
    diag = .{};
    w = work_mod.Work{};
    try std.testing.expectError(error.UnsupportedFeature, compile(std.testing.allocator, "{\"type\":\"integer\"}", 64, &diag, &w, false, .canonical_v1, reg));
}

test "spec-v1 P5: recursion across registry documents shares the unroll budget" {
    var diag: Diagnostic = .{};
    var w = work_mod.Work{};
    // The remote tree recurses through the registry; it compiles, and the
    // bottom of the budget is the empty-language node shape.
    const reg = "{\"documents\":{\"http://ex.com/tree.json\":{\"type\":\"object\",\"properties\":{\"l\":{\"$ref\":\"http://ex.com/tree.json\"}},\"additionalProperties\":false}}}";
    var g = try compile(std.testing.allocator, "{\"$ref\":\"http://ex.com/tree.json\"}", 64, &diag, &w, false, .spec_v1, reg);
    defer g.deinit();
    var found_empty = false;
    for (g.nodes) |n| {
        switch (n) {
            .str => |sc| {
                if (sc.min_len > sc.max_len) found_empty = true;
            },
            else => {},
        }
    }
    try std.testing.expect(found_empty);
    // A remote ref cycle with no base case: the empty-language refusal.
    diag = .{};
    w = work_mod.Work{};
    const reg_loop = "{\"documents\":{\"http://ex.com/a.json\":{\"$ref\":\"http://ex.com/b.json\"},\"http://ex.com/b.json\":{\"$ref\":\"http://ex.com/a.json\"}}}";
    try std.testing.expectError(error.UnsatisfiableConstraint, compile(std.testing.allocator, "{\"$ref\":\"http://ex.com/a.json\"}", 64, &diag, &w, false, .spec_v1, reg_loop));
}

test "spec-v1: unevaluatedProperties fold compiles" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"$schema\":\"https://json-schema.org/draft/2020-12/schema\",\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"integer\"}},\"unevaluatedProperties\":{\"type\":\"string\"}}", &diag);
    defer g.deinit();
}

test "spec-v1: unevaluated* inert reductions compile" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"$schema\":\"https://json-schema.org/draft/2020-12/schema\",\"properties\":{\"a\":true},\"unevaluatedProperties\":true}", &diag);
    defer g.deinit();
    var g2 = try compileSpec("{\"$schema\":\"https://json-schema.org/draft/2020-12/schema\",\"properties\":{\"a\":true},\"additionalProperties\":{\"type\":\"string\"},\"unevaluatedProperties\":false}", &diag);
    defer g2.deinit();
    var g3 = try compileSpec("{\"$schema\":\"https://json-schema.org/draft/2019-09/schema\",\"items\":[{\"type\":\"integer\"}],\"unevaluatedItems\":false}", &diag);
    defer g3.deinit();
}

test "spec-v1: unevaluated* ignored in draft-07" {
    var diag: Diagnostic = .{};
    var g = try compileSpec("{\"$schema\":\"http://json-schema.org/draft-07/schema#\",\"properties\":{\"a\":true},\"unevaluatedProperties\":false}", &diag);
    defer g.deinit();
}

test "spec-v1: unevaluated* over a nested combinator branch refuses with pointer" {
    var diag: Diagnostic = .{};
    const s = "{\"$schema\":\"https://json-schema.org/draft/2020-12/schema\",\"$defs\":{\"one\":{\"oneOf\":[{\"$ref\":\"#/$defs/two\"},{\"required\":[\"b\"],\"properties\":{\"b\":true}}]},\"two\":{\"oneOf\":[{\"required\":[\"c\"],\"properties\":{\"c\":true}},{\"required\":[\"d\"],\"properties\":{\"d\":true}}]}},\"oneOf\":[{\"$ref\":\"#/$defs/one\"},{\"required\":[\"a\"],\"properties\":{\"a\":true}}],\"unevaluatedProperties\":false}";
    try std.testing.expectError(error.UnsupportedFeature, compileSpec(s, &diag));
    try std.testing.expectEqualStrings("/oneOf/0", diag.pointerText());
}

test "spec-v1: unevaluatedItems with several conjunctive contains refuses" {
    var diag: Diagnostic = .{};
    const s = "{\"$schema\":\"https://json-schema.org/draft/2020-12/schema\",\"allOf\":[{\"contains\":{\"multipleOf\":2}},{\"contains\":{\"multipleOf\":3}}],\"unevaluatedItems\":{\"multipleOf\":5}}";
    try std.testing.expectError(error.UnsupportedFeature, compileSpec(s, &diag));
}

test "spec-v1: unevaluated* with external $ref refuses" {
    var diag: Diagnostic = .{};
    const s = "{\"$schema\":\"https://json-schema.org/draft/2020-12/schema\",\"$ref\":\"http://ex.com/other.json\",\"unevaluatedProperties\":false}";
    try std.testing.expectError(error.UnsupportedFeature, compileSpec(s, &diag));
}

test "spec-v1: unevaluated* anyOf branch budget refuses" {
    var diag: Diagnostic = .{};
    const s = "{\"$schema\":\"https://json-schema.org/draft/2020-12/schema\",\"anyOf\":[{\"required\":[\"a\"],\"properties\":{\"a\":true}},{\"required\":[\"b\"],\"properties\":{\"b\":true}},{\"required\":[\"c\"],\"properties\":{\"c\":true}},{\"required\":[\"d\"],\"properties\":{\"d\":true}},{\"required\":[\"e\"],\"properties\":{\"e\":true}},{\"required\":[\"f\"],\"properties\":{\"f\":true}}],\"unevaluatedProperties\":false}";
    try std.testing.expectError(error.UnsupportedFeature, compileSpec(s, &diag));
}

// ---- spec-v1 P6b: content*, relative-URI resources, $dynamicRef/$recursiveRef ----

test "spec-v1 P6b: content keywords are annotations (2020-12 section 8)" {
    var diag: Diagnostic = .{};
    // contentSchema next to contentEncoding/contentMediaType carries no
    // assertion in the validation specification: the schema compiles to the
    // unconstrained language and every instance stays valid.
    var g = try compileSpec("{\"contentMediaType\":\"application/json\",\"contentEncoding\":\"base64\",\"contentSchema\":{\"type\":\"object\",\"required\":[\"foo\"]}}", &diag);
    defer g.deinit();
    // In dialects without the Content vocabulary the keywords are unknown
    // and ignored just the same.
    diag = .{};
    var g2 = try compileSpec("{\"$schema\":\"http://json-schema.org/draft-07/schema#\",\"contentSchema\":{\"type\":\"object\"}}", &diag);
    defer g2.deinit();
    // canonical-v1 keeps refusing them (frozen profile).
    diag = .{};
    try std.testing.expectError(error.UnsupportedFeature, compileDiag("{\"contentSchema\":{\"type\":\"object\"}}", &diag));
}

test "spec-v1 P6b: relative $id resources and RFC 3986 resolution" {
    var diag: Diagnostic = .{};
    // anchor.json 'same $anchor with different base uri': 'child1#my_anchor'
    // resolves against the root base into the in-document resource; the
    // anchor of the child2 resource is not visible there.
    var g = try compileSpec("{\"$id\":\"http://localhost:1234/draft2020-12/foobar\",\"$defs\":{\"A\":{\"$id\":\"child1\",\"allOf\":[{\"$id\":\"child2\",\"$anchor\":\"my_anchor\",\"type\":\"number\"},{\"$anchor\":\"my_anchor\",\"type\":\"string\"}]}},\"$ref\":\"child1#my_anchor\"}", &diag);
    defer g.deinit();
    try std.testing.expect(g.node(g.root).* == .str);
    // Dot-segment removal: '../d.json' against .../a/b/c.json is
    // 'http://ex.com/a/d.json'.
    diag = .{};
    var g2 = try compileSpec("{\"$id\":\"http://ex.com/a/b/c.json\",\"$defs\":{\"d\":{\"$id\":\"../d.json\",\"type\":\"integer\"}},\"$ref\":\"http://ex.com/a/d.json\"}", &diag);
    defer g2.deinit();
    try std.testing.expect(g2.node(g2.root).* == .int_num);
    // A URN base: an absolute URN $ref jumps back into the document.
    diag = .{};
    var g3 = try compileSpec("{\"$id\":\"urn:uuid:deadbeef-1234-ffff-ffff-4321feebdaed\",\"minimum\":30}", &diag);
    defer g3.deinit();
    diag = .{};
    var g4 = try compileSpec("{\"$id\":\"urn:uuid:deadbeef-0000-0000-0000-4321feebdaed\",\"properties\":{\"foo\":{\"$ref\":\"urn:uuid:deadbeef-0000-0000-0000-4321feebdaed#/$defs/bar\"}},\"$defs\":{\"bar\":{\"type\":\"string\"}}}", &diag);
    defer g4.deinit();
    // An unknown resolved URI keeps the documented registry refusal.
    diag = .{};
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"$id\":\"http://ex.com/main.json\",\"$ref\":\"missing.json\"}", &diag));
}

test "spec-v1 P6b: relative $ref reaches the registry by resolved URI" {
    var diag: Diagnostic = .{};
    var w = work_mod.Work{};
    const reg = "{\"documents\":{\"http://ex.com/dir/int.json\":{\"type\":\"integer\"}}}";
    var g = try compile(std.testing.allocator, "{\"$id\":\"http://ex.com/dir/main.json\",\"$ref\":\"int.json\"}", 64, &diag, &w, false, .spec_v1, reg);
    defer g.deinit();
    try std.testing.expect(g.node(g.root).* == .int_num);
    // A registry document's own in-document $id resources resolve too.
    diag = .{};
    w = work_mod.Work{};
    const reg2 = "{\"documents\":{\"http://ex.com/outer.json\":{\"$defs\":{\"bar\":{\"$id\":\"http://ex.com/nested-id.json\",\"type\":\"string\"}},\"$ref\":\"http://ex.com/nested-id.json\"}}}";
    var g2 = try compile(std.testing.allocator, "{\"$ref\":\"http://ex.com/outer.json\"}", 64, &diag, &w, false, .spec_v1, reg2);
    defer g2.deinit();
    try std.testing.expect(g2.node(g2.root).* == .str);
}

test "spec-v1 P6b: $dynamicRef dynamic scope resolution compiles" {
    var diag: Diagnostic = .{};
    // The typical dynamic resolution shape: the root's $dynamicAnchor
    // overrides the bookending one of the 'list' resource.
    const s = "{\"$id\":\"https://test.json-schema.org/typical-dynamic-resolution/root\",\"$ref\":\"list\",\"$defs\":{\"foo\":{\"$dynamicAnchor\":\"items\",\"type\":\"string\"},\"list\":{\"$id\":\"list\",\"type\":\"array\",\"items\":{\"$dynamicRef\":\"#items\"},\"$defs\":{\"items\":{\"$dynamicAnchor\":\"items\"}}}}}";
    var g = try compileSpec(s, &diag);
    defer g.deinit();
    // Without a bookending $dynamicAnchor in the statically resolved
    // resource, the plain anchor target is used (behaves like $ref).
    diag = .{};
    const s2 = "{\"$id\":\"https://ex.com/root\",\"$ref\":\"list\",\"$defs\":{\"foo\":{\"$dynamicAnchor\":\"items\",\"type\":\"string\"},\"list\":{\"$id\":\"list\",\"type\":\"array\",\"items\":{\"$dynamicRef\":\"#items\"},\"$defs\":{\"items\":{\"$anchor\":\"items\"}}}}}";
    var g2 = try compileSpec(s2, &diag);
    defer g2.deinit();
    // A static $ref to a $dynamicAnchor name resolves like an $anchor.
    diag = .{};
    const s3 = "{\"type\":\"array\",\"items\":{\"$ref\":\"#items\"},\"$defs\":{\"foo\":{\"$dynamicAnchor\":\"items\",\"type\":\"string\"}}}";
    var g3 = try compileSpec(s3, &diag);
    defer g3.deinit();
}

test "spec-v1 P6b: $dynamicRef across a registry document" {
    var diag: Diagnostic = .{};
    var w = work_mod.Work{};
    // strict-extendible: the main document's $dynamicAnchor governs the
    // $dynamicRef evaluated inside the registry document.
    const reg = "{\"documents\":{\"http://ex.com/extendible.json\":{\"$id\":\"http://ex.com/extendible.json\",\"type\":\"object\",\"properties\":{\"elements\":{\"type\":\"array\",\"items\":{\"$dynamicRef\":\"#elements\"}}},\"$defs\":{\"elements\":{\"$dynamicAnchor\":\"elements\"}}}}}";
    const s = "{\"$id\":\"http://ex.com/strict.json\",\"$ref\":\"extendible.json\",\"$defs\":{\"elements\":{\"$dynamicAnchor\":\"elements\",\"properties\":{\"a\":true},\"required\":[\"a\"],\"additionalProperties\":false}}}";
    var g = try compile(std.testing.allocator, s, 64, &diag, &w, false, .spec_v1, reg);
    defer g.deinit();
}

test "spec-v1 P6b: $dynamicRef forms and dialect gating" {
    var diag: Diagnostic = .{};
    // Non-string value: INVALID_SCHEMA with a pointer.
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"items\":{\"$dynamicRef\":5}}", &diag));
    // Unresolvable anchor name: INVALID_SCHEMA.
    diag = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"items\":{\"$dynamicRef\":\"#nope\"}}", &diag));
    // Unknown external document: UNSUPPORTED_FEATURE (registry refusal).
    diag = .{};
    try std.testing.expectError(error.UnsupportedFeature, compileSpec("{\"items\":{\"$dynamicRef\":\"http://ex.com/missing.json#a\"}}", &diag));
    // Outside 2020-12 the keyword is unknown and ignored.
    diag = .{};
    var g = try compileSpec("{\"$schema\":\"https://json-schema.org/draft/2019-09/schema\",\"$dynamicRef\":\"#x\"}", &diag);
    defer g.deinit();
    // canonical-v1 keeps refusing it.
    diag = .{};
    try std.testing.expectError(error.UnsupportedFeature, compileDiag("{\"$dynamicRef\":\"#x\"}", &diag));
}

test "spec-v1 P6b: $recursiveRef and $recursiveAnchor (2019-09)" {
    var diag: Diagnostic = .{};
    // Bookended recursive ref: compiles; the outermost recursive-anchor
    // resource of the dynamic scope wins.
    const s = "{\"$schema\":\"https://json-schema.org/draft/2019-09/schema\",\"$id\":\"http://ex.com/root\",\"$recursiveAnchor\":true,\"$ref\":\"list\",\"$defs\":{\"list\":{\"$id\":\"list\",\"$recursiveAnchor\":true,\"type\":\"array\",\"items\":{\"$recursiveRef\":\"#\"}}}}";
    var g = try compileSpec(s, &diag);
    defer g.deinit();
    // Without the bookend the static target (the resource root) is used.
    diag = .{};
    const s2 = "{\"$schema\":\"https://json-schema.org/draft/2019-09/schema\",\"$id\":\"http://ex.com/root\",\"$ref\":\"list\",\"$defs\":{\"list\":{\"$id\":\"list\",\"type\":\"array\",\"items\":{\"$recursiveRef\":\"#\"}}}}";
    var g2 = try compileSpec(s2, &diag);
    defer g2.deinit();
    // Only '#' exists for $recursiveRef in 2019-09.
    diag = .{};
    try std.testing.expectError(error.InvalidSchema, compileSpec("{\"$schema\":\"https://json-schema.org/draft/2019-09/schema\",\"$recursiveRef\":\"#/$defs/x\"}", &diag));
    // In 2020-12 the keyword is unknown and ignored.
    diag = .{};
    var g3 = try compileSpec("{\"$recursiveRef\":\"#\"}", &diag);
    defer g3.deinit();
}

test "spec-v1 P6b: $dynamicRef next to unevaluated* refuses with a pointer" {
    var diag: Diagnostic = .{};
    const s = "{\"$defs\":{\"n\":{\"$dynamicAnchor\":\"node\",\"type\":\"object\"}},\"$dynamicRef\":\"#node\",\"unevaluatedProperties\":false}";
    try std.testing.expectError(error.UnsupportedFeature, compileSpec(s, &diag));
}
