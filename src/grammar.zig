const std = @import("std");

pub const NodeId = u32;
pub const UNBOUNDED: u32 = std.math.maxInt(u32);

pub const GrammarKind = enum(u8) { json_schema, literal_set };

pub const Literal = struct { off: u32, len: u32 };

pub const Repeat = struct {
    item: NodeId,
    min: u32,
    max: u32,
};

pub const Prop = struct {
    key: Literal,
    value: NodeId,
    required: bool,
};

pub const Node = union(enum) {
    literal: Literal,
    str: struct { min_len: u32, max_len: u32 },
    int_v: void,
    num_v: void,
    choice: []const NodeId,
    seq: []const NodeId,
    repeat: Repeat,
    object: []const Prop,
};

/// Two independent 64-bit hashes of the grammar source bytes (FNV-1a and
/// Wyhash). Used in the cache key; a simultaneous collision of both is the
/// accepted residual risk (~2^-128), see cache.zig.
pub const Identity = struct { lo: u64 = 0, hi: u64 = 0 };

pub fn identityOf(kind: GrammarKind, bytes: []const u8) Identity {
    var lo = fnv1a64(&[_]u8{@intFromEnum(kind)});
    lo = fnv1a64Update(lo, bytes);
    var hi = std.hash.Wyhash.init(0);
    hi.update(&[_]u8{@intFromEnum(kind)});
    hi.update(bytes);
    return .{ .lo = lo, .hi = hi.final() };
}

pub const Grammar = struct {
    kind: GrammarKind,
    nodes: []const Node,
    literal_pool: []const u8,
    root: NodeId,
    id: u64,
    id_hi: u64,
    arena: std.heap.ArenaAllocator,

    pub fn deinit(self: *Grammar) void {
        self.arena.deinit();
    }

    pub fn node(self: *const Grammar, id: NodeId) *const Node {
        return &self.nodes[id];
    }

    pub fn literalBytes(self: *const Grammar, lit: Literal) []const u8 {
        return self.literal_pool[lit.off .. lit.off + lit.len];
    }
};

pub fn fnv1a64(bytes: []const u8) u64 {
    var h: u64 = 0xcbf29ce484222325;
    for (bytes) |b| {
        h ^= b;
        h *%= 0x100000001b3;
    }
    return h;
}

pub fn fnv1a64Update(h: u64, bytes: []const u8) u64 {
    var x = h;
    for (bytes) |b| {
        x ^= b;
        x *%= 0x100000001b3;
    }
    return x;
}

pub const Builder = struct {
    a: std.mem.Allocator,
    nodes: std.ArrayListUnmanaged(Node) = .{},
    pool: std.ArrayListUnmanaged(u8) = .{},

    pub fn init(a: std.mem.Allocator) Builder {
        return .{ .a = a };
    }

    fn alloc(self: *Builder) std.mem.Allocator {
        return self.a;
    }

    pub fn addNode(self: *Builder, n: Node) !NodeId {
        const a = self.alloc();
        const id: NodeId = @intCast(self.nodes.items.len);
        try self.nodes.append(a, n);
        return id;
    }

    pub fn addLiteral(self: *Builder, bytes: []const u8) !Literal {
        const a = self.alloc();
        const off: u32 = @intCast(self.pool.items.len);
        try self.pool.appendSlice(a, bytes);
        return .{ .off = off, .len = @intCast(bytes.len) };
    }

    pub fn addLiteralNode(self: *Builder, bytes: []const u8) !NodeId {
        return self.addNode(.{ .literal = try self.addLiteral(bytes) });
    }

    pub fn copyNodeIds(self: *Builder, ids: []const NodeId) ![]const NodeId {
        return self.alloc().dupe(NodeId, ids);
    }

    pub fn copyProps(self: *Builder, props: []const Prop) ![]const Prop {
        return self.alloc().dupe(Prop, props);
    }

    pub fn finish(self: *Builder, arena: std.heap.ArenaAllocator, kind: GrammarKind, root: NodeId, ident: Identity) !Grammar {
        return .{
            .kind = kind,
            .nodes = self.nodes.items,
            .literal_pool = self.pool.items,
            .root = root,
            .id = ident.lo,
            .id_hi = ident.hi,
            .arena = arena,
        };
    }
};
