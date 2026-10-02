const std = @import("std");
const pattern = @import("pattern.zig");
const witset = @import("witset.zig");

pub const NodeId = u32;
pub const UNBOUNDED: u32 = std.math.maxInt(u32);

pub const GrammarKind = enum(u8) { json_schema, literal_set };

pub const Literal = struct { off: u32, len: u32 };

pub const LitTrieNode = struct { edge_off: u32, edge_len: u32, terminal: bool };
pub const LitTrieEdge = struct { byte: u8, child: u32 };

/// Shared-prefix trie over the literal alternatives of a choice. All the
/// literals of the set are enumerated in `literals` (the coverage check
/// visits each of them); the parser walks `nodes`/`edges`, so alternatives
/// sharing a prefix advance as one thread instead of one thread per
/// literal - an enum of 64 values costs a single thread per byte through
/// the common prefix.
pub const LitTrie = struct {
    nodes: []const LitTrieNode,
    edges: []const LitTrieEdge,
    literals: []const Literal,

    /// Child of `node` by byte; edges of a node are sorted by byte, so the
    /// lookup is a binary search (high fan-out nodes like the root have
    /// hundreds of children).
    pub fn child(self: *const LitTrie, node: u32, b: u8) ?u32 {
        const base = self.nodes[node].edge_off;
        var lo: u32 = 0;
        var hi: u32 = self.nodes[node].edge_len;
        while (lo < hi) {
            const mid = lo + (hi - lo) / 2;
            const e = self.edges[base + mid];
            if (e.byte < b) {
                lo = mid + 1;
            } else if (e.byte > b) {
                hi = mid;
            } else return e.child;
        }
        return null;
    }
};

pub const Repeat = struct {
    item: NodeId,
    min: u32,
    max: u32,
    // spec-v1 P2: tuple prefix (prefixItems / draft-04..2019-09 tuple
    // items). Element i < prefix.len uses prefix[i], later elements use
    // `item`. Empty = homogeneous array.
    prefix: []const NodeId = &.{},
    // spec-v1 P2: contains + minContains/maxContains. The element match is
    // decided exactly: the raw bytes of every completed element are
    // re-parsed against `contains` (parser tap capture).
    contains: ?NodeId = null,
    min_contains: u32 = 1,
    max_contains: u32 = UNBOUNDED,
    // spec-v1 P2: uniqueItems. Completed elements are canonicalized
    // (numbers by value, object key order insignificant) and compared with
    // exact structural equality against the seen set.
    unique: bool = false,
    // Number of distinct values the element language allows when it is a
    // finite literal set and there is no prefix (0 = not finite/unknown).
    // Drives the ADR-0005 D4 uniqueItems residual: a fresh element is
    // emittable only while the seen set is smaller than this count.
    uniq_finite: u32 = 0,
    // Reserved for the unevaluated* annotation transport (ADR-0006 D4);
    // always 0 in P1.
    ann_slots: u16 = 0,
};

pub const Prop = struct {
    key: Literal,
    value: NodeId,
    required: bool,
};

/// Closed object: declared properties only, in schema order (canonical-v1).
pub const ObjectNode = struct {
    props: []const Prop,
    // Reserved (ADR-0006 D4); always 0 in P1.
    ann_slots: u16 = 0,
};

/// A dependentRequired/dependentSchemas (or draft-04..07 `dependencies`)
/// entry, normalized (dialect-matrix section 8). `trigger` and `names` are
/// raw canonical key bytes (unquoted), matching the open-object key
/// capture. Kind `required`: when the trigger was seen, every name must
/// appear before the object closes. Kind `schema`: when the trigger was
/// seen, the whole object (captured bytes) must be accepted by `schema`.
/// Kind `ban`: the trigger key may not appear at all (dependent schema
/// `false`, or a dependency a closed object can never discharge).
pub const DepKind = enum(u8) { required, schema, ban };
pub const Dep = struct {
    trigger: Literal,
    kind: DepKind,
    names: []const Literal = &.{},
    schema: NodeId = 0,
};

/// Open object (spec-v1): declared properties in schema order, undeclared
/// keys interleave freely, are unique and take `value` as their schema.
/// `extra_required` names required keys not declared in `props` (a
/// `required` entry without a matching `properties` entry); they behave
/// like undeclared keys that must appear at least once.
///
/// P2 additions: `pattern_lit`/`pattern_value` implement a single
/// literal-substring patternProperties entry; P4 adds `pattern_dfa` for a
/// full-regex entry (a key containing the literal / matching the regex
/// takes `pattern_value` instead of `value`).
/// `min_props`/`max_props` count keys; `track_keys` records every
/// dispatched key (declared and undeclared) in the seen set so the count
/// and the dependency triggers/members are plain set queries. `deps`
/// carries the normalized dependencies; `capture` arms the whole-object
/// byte tap needed by schema-kind deps (ADR-0006 D2 counters/capture
/// chunks). `prop_names` is the propertyNames node applied to undeclared
/// keys at dispatch (null when the schema places no constraint on names);
/// `key_min_len`/`key_max_len` are its length bounds, enforced
/// incrementally by the key machine. `names_forbidden` is propertyNames
/// `false`: only the empty object is valid.
pub const OpenObjNode = struct {
    props: []const Prop,
    extra_required: []const Literal,
    value: NodeId,
    min_props: u32 = 0,
    max_props: u32 = UNBOUNDED,
    track_keys: bool = false,
    capture: bool = false,
    names_forbidden: bool = false,
    deps: []const Dep = &.{},
    prop_names: ?NodeId = null,
    key_min_len: u32 = 0,
    key_max_len: u32 = UNBOUNDED,
    pattern_lit: ?Literal = null,
    pattern_value: NodeId = 0,
    // spec-v1 P4: a full-regex patternProperties entry (exactly one; the
    // literal-substring form above stays the fast path for patterns free
    // of regex metacharacters). Mutually exclusive with pattern_lit; the
    // decoded key is matched by DFA search at dispatch.
    pattern_dfa: ?*const pattern.Dfa = null,
    ann_slots: u16 = 0,
};

/// Canonical decimal form of a number constant (ADR-0006 D6): the value is
/// digits x 10^exp10 with `digits` carrying no leading or trailing zeros;
/// zero is the empty digit string (sign dropped: -0 == 0).
pub const NumConst = struct { neg: bool, digits: []const u8, exp10: i64 };

/// Sentinel branch slot of an ifelse comb node: the then/else applicator is
/// absent (verdict `true`).
pub const COMB_NONE: NodeId = std.math.maxInt(u32);
/// Boolean-combinator node kind (spec-v1 P3). `anyOf` needs no node: it is
/// a plain union and compiles to choice/lit_trie. The others parse every
/// branch in parallel NFA threads tagged with one instance id; the verdict
/// is evaluated at the confirmed value boundary (ADR-0005 D2):
/// oneOf = exactly one branch accepts, allOf = every branch accepts,
/// if/then/else = (if /\ then) \/ (~if /\ else) over branches [if,then,else].
pub const CombKind = enum(u8) { oneof, allof, ifelse };

pub const Comb = struct {
    kind: CombKind,
    /// oneOf/allOf: the branch nodes (1..64). ifelse: exactly 3 slots
    /// [if, then, else]; then/else may be COMB_NONE (absent, i.e. `true`).
    branches: []const NodeId,
    // Reserved for the unevaluated* annotation transport (ADR-0006 D4).
    ann_slots: u16 = 0,
};

/// String language minus a finite forbidden set (spec-v1 P3, `not` over
/// const/enum): the str machine of `str` plus a shared-prefix trie over the
/// QUOTED forbidden literals walked on the same raw bytes; completion at a
/// trie terminal is rejected (the string spelled a forbidden value).
pub const StrExcl = struct {
    trie: LitTrie,
    min_len: u32,
    max_len: u32,
};

/// String constrained by an ECMA-262-subset regex (spec-v1 P4, `pattern`):
/// the str machine of `str` (length bounds counted in codepoints) plus the
/// pattern DFA fed every decoded codepoint (JSON Schema `pattern` is an
/// unanchored search; the DFA's search-mode wrapping handles that). The
/// DFA lives in the grammar arena; it is never empty within the length
/// bounds (an empty arm is dropped at compile time).
pub const StrPat = struct {
    min_len: u32,
    max_len: u32,
    dfa: *const pattern.Dfa,
};

/// Number language minus up to two forbidden constants (spec-v1 P3, `not`
/// over numeric const/enum): the JSON number automaton with a per-constant
/// incremental match against the canonical target; completion spelling one
/// of the constants is rejected.
pub const NumExcl = struct {
    consts: []const NumConst,
};

/// Number constrained by value bounds (spec-v1 P4, minimum/maximum/
/// exclusiveMinimum/exclusiveMaximum after dialect normalization): the JSON
/// number automaton; the verdict is evaluated exactly at the confirmed value
/// boundary against the canonical decimal bounds (comparison by value:
/// 1.0 == 1). A null bound is absent; `*_excl` makes a present bound
/// exclusive. Compile guarantees the range is non-empty.
pub const NumRange = struct {
    min: ?NumConst = null,
    min_excl: bool = false,
    max: ?NumConst = null,
    max_excl: bool = false,
};

/// Number constrained to multiples of a decimal divisor (spec-v1 P4,
/// multipleOf). `div` is the divisor's canonical digit string as an
/// integer (divisors with more than 10 significant digits are refused at
/// compile time); `div_exp10` its canonical exponent: divisor =
/// div x 10^div_exp10 > 0. `co` is `div` with all factors 2 and 5 stripped
/// (the residue class that decides divisibility once the scaled exponent
/// dominates the 2/5 valuations). Divisibility is exact decimal arithmetic:
/// V is a multiple iff V == 0 or, with V = D x 10^p in canonical form and
/// s = p - div_exp10 >= 0, D x 10^s == 0 (mod div).
pub const NumMult = struct {
    div: u32,
    div_exp10: i64,
    co: u32,
};

pub fn flipOrder(o: std.math.Order) std.math.Order {
    return switch (o) {
        .lt => .gt,
        .gt => .lt,
        .eq => .eq,
    };
}

/// Exact decimal comparison of two canonical constants (spec-v1 P4):
/// compare by value, never through binary64. Zero is the empty digit
/// string; -0 == 0.
pub fn cmpNumConst(a: NumConst, b: NumConst) std.math.Order {
    const az = a.digits.len == 0;
    const bz = b.digits.len == 0;
    if (az and bz) return .eq;
    if (az) return if (b.neg) .gt else .lt;
    if (bz) return if (a.neg) .lt else .gt;
    if (a.neg != b.neg) return if (a.neg) .lt else .gt;
    // Same sign, both nonzero: magnitude by leading-digit position, then
    // the digit strings aligned at the leading digit (zero-padded).
    const oma: i64 = a.exp10 + @as(i64, @intCast(a.digits.len)) - 1;
    const omb: i64 = b.exp10 + @as(i64, @intCast(b.digits.len)) - 1;
    var mag = std.math.order(oma, omb);
    if (mag == .eq) {
        const n = @max(a.digits.len, b.digits.len);
        var i: usize = 0;
        while (i < n) : (i += 1) {
            const da: u8 = if (i < a.digits.len) a.digits[i] else '0';
            const db: u8 = if (i < b.digits.len) b.digits[i] else '0';
            if (da != db) {
                mag = if (da < db) .lt else .gt;
                break;
            }
        }
    }
    return if (a.neg) flipOrder(mag) else mag;
}

/// Range membership of a canonical constant (compile-time checks: enum/const
/// filtering, contradictory-bound detection, coverage witnesses).
pub fn numConstInRange(v: NumConst, nr: NumRange) bool {
    if (nr.min) |m| {
        const o = cmpNumConst(v, m);
        if (o == .lt or (nr.min_excl and o == .eq)) return false;
    }
    if (nr.max) |m| {
        const o = cmpNumConst(v, m);
        if (o == .gt or (nr.max_excl and o == .eq)) return false;
    }
    return true;
}

/// Exact divisibility of a canonical constant (compile-time/coverage
/// counterpart of the parser's num_mult verdict; see NumMult).
pub fn numConstIsMultiple(v: NumConst, nm: NumMult) bool {
    if (v.digits.len == 0) return true; // zero is a multiple
    const s: i64 = v.exp10 - nm.div_exp10;
    if (s < 0) return false;
    var rem: u64 = 0;
    for (v.digits) |d| rem = (rem * 10 + (d - '0')) % nm.div;
    const p = powmodU32(10 % nm.div, @intCast(s), nm.div);
    return (rem * p) % nm.div == 0;
}

/// base^exp mod m with m <= u32 max: the u64 intermediate is exact.
pub fn powmodU32(base: u32, exp: u64, m: u32) u64 {
    var r: u64 = 1;
    var b: u64 = base % m;
    var e = exp;
    while (e > 0) {
        if (e & 1 != 0) r = (r * b) % m;
        b = (b * b) % m;
        e >>= 1;
    }
    return r;
}

pub const Node = union(enum) {
    literal: Literal,
    lit_trie: LitTrie,
    str: struct { min_len: u32, max_len: u32 },
    int_v: void,
    num_v: void,
    // spec-v1 value-based integer: any JSON number spelling whose VALUE is
    // an integer (semantics-spec-v1 4.4: 1.0 and 1e2 are integers).
    int_num: void,
    // spec-v1 number constant matched by value, not by spelling.
    num_const: NumConst,
    // spec-v1 P3: boolean combinators (see Comb).
    comb: Comb,
    // spec-v1 P3: any JSON number whose VALUE is not an integer
    // (complement of {"type":"integer"} for `not`).
    not_int_num: void,
    // spec-v1 P3: string language minus a finite forbidden set.
    str_excl: StrExcl,
    // spec-v1 P4: string constrained by a regex `pattern` (DFA).
    str_pat: StrPat,
    // spec-v1 P3: number language minus up to two forbidden constants.
    num_excl: NumExcl,
    // spec-v1 P4: number constrained by value bounds (minimum/maximum/
    // exclusive*, dialect-normalized; exact decimal comparison).
    num_range: NumRange,
    // spec-v1 P4: number constrained to multiples of a decimal divisor
    // (multipleOf; exact decimal divisibility).
    num_mult: NumMult,
    choice: []const NodeId,
    seq: []const NodeId,
    repeat: Repeat,
    object: ObjectNode,
    open_obj: OpenObjNode,
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

/// Two-part identity (spec-v1 P5, ADR-0006 D5): the schema bytes plus an
/// external-$ref registry snapshot. Length prefixes keep the split
/// unambiguous; a registry swap is a different identity.
pub fn identityOf2(kind: GrammarKind, bytes: []const u8, extra: []const u8) Identity {
    var lenbuf: [8]u8 = undefined;
    std.mem.writeInt(u64, &lenbuf, bytes.len, .little);
    var lo = fnv1a64(&[_]u8{@intFromEnum(kind)});
    lo = fnv1a64Update(lo, &lenbuf);
    lo = fnv1a64Update(lo, bytes);
    lo = fnv1a64Update(lo, extra);
    var hi = std.hash.Wyhash.init(0);
    hi.update(&[_]u8{@intFromEnum(kind)});
    hi.update(&lenbuf);
    hi.update(bytes);
    hi.update(extra);
    return .{ .lo = lo, .hi = hi.final() };
}

pub const Grammar = struct {
    kind: GrammarKind,
    nodes: []const Node,
    literal_pool: []const u8,
    root: NodeId,
    id: u64,
    id_hi: u64,
    /// True when the language is a finite set of literal strings: only
    /// literal/lit_trie/choice/seq/object nodes (no str/int/num/repeat).
    /// For such grammars exact token-level completion reachability is
    /// decidable (the state space is finite), which lets masks stay exact
    /// on vocabularies without full byte coverage (src/complete.zig).
    finite_literal: bool,
    /// spec-v1 (ADR-0005): true when the grammar contains node kinds
    /// whose live prefixes can dead-end even on a byte-complete vocabulary
    /// (comb verdicts at value boundaries, pattern DFA states the search
    /// wrapper keeps alive past the last completable prefix, bounded
    /// str_excl strings, value-constrained number machines). Such grammars
    /// run the residual-reachability filter of src/complete.zig for every
    /// mask/accept; canonical-v1 grammars never set it and keep the
    /// unfiltered fast path bit for bit.
    /// Defaults cover the temporary grammar views built without
    /// Builder.finish (never used for mask/accept filtering).
    needs_reachability: bool = false,
    /// Per-node exact length-acceptance descriptors of pattern DFAs (index
    /// = NodeId; covers str_pat nodes and open_obj pattern_dfa entries, null
    /// for other kinds, and null for a node whose analysis exceeded
    /// LenBudget - the runtime then treats those frames as undecidable and
    /// the budgeted search answers or fails the mask call loudly, never
    /// guesses).
    pat_len: []const ?*const pattern.PatLenDescr = &.{},
    /// spec-v1: three-state language non-emptiness per node (index =
    /// NodeId; NE_YES / NE_NO / NE_UNKNOWN). Computed at finish when
    /// needs_reachability is set; the residual certifier (src/complete.zig)
    /// uses it to settle open_obj frames and their pending value dispatches
    /// without search. yes/no are exact; unknown defers to the budgeted
    /// search, never guesses.
    nonempty: []const u8 = &.{},
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

    /// Runtime structural equality of two nodes of this grammar (spec-v1):
    /// equal structure implies the same language. Used by the
    /// certifier's oneOf vote-link analysis (src/complete.zig
    /// linkedThreads) to recognize independently compiled but
    /// interchangeable subschema nodes (e.g. two "array of any" repeat
    /// nodes of oneOf branches). Allocations come from the caller and are
    /// released before return; a node cycle counts as not equivalent.
    pub fn nodesEquiv(self: *const Grammar, a: std.mem.Allocator, x: NodeId, y: NodeId) bool {
        var eqc = EqCtx{ .ns = self.nodes, .pool = self.literal_pool, .a = a };
        defer eqc.deinit();
        return eqc.equiv(x, y) catch false;
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

/// True when every node reachable from `root` produces a finite set of
/// literal strings: literal, lit_trie, choice, seq and object nodes only
/// (str/int_v/num_v/repeat make the language open). Memoized over the
/// node DAG; a node caught in a cycle conservatively counts as not
/// finite. Used by Grammar.finish to enable the exact completion filter
/// (src/complete.zig) on vocabularies without full byte coverage.
fn isFiniteLiteral(nodes: []const Node, root: NodeId, a: std.mem.Allocator) !bool {
    const state = try a.alloc(u8, nodes.len);
    defer a.free(state);
    @memset(state, 0); // 0 unknown, 1 yes, 2 no, 3 in progress

    const S = struct {
        fn visit(ns: []const Node, st: []u8, id: NodeId) bool {
            switch (st[id]) {
                1 => return true,
                2 => return false,
                3 => return false, // cycle: not a finite literal language
                else => {},
            }
            st[id] = 3;
            const v = switch (ns[id]) {
                .literal, .lit_trie => true,
                .str, .int_v, .num_v, .int_num, .num_const, .repeat, .open_obj, .comb, .not_int_num, .str_excl, .num_excl, .str_pat, .num_range, .num_mult => false,
                .choice => |alts| blk: {
                    for (alts) |c| {
                        if (!visit(ns, st, c)) break :blk false;
                    }
                    break :blk true;
                },
                .seq => |children| blk: {
                    for (children) |c| {
                        if (!visit(ns, st, c)) break :blk false;
                    }
                    break :blk true;
                },
                .object => |o| blk: {
                    for (o.props) |p| {
                        if (!visit(ns, st, p.value)) break :blk false;
                    }
                    break :blk true;
                },
            };
            st[id] = if (v) 1 else 2;
            return v;
        }
    };
    return S.visit(nodes, state, root);
}

/// True when a node reachable from the root defers part of its acceptance
/// verdict to the confirmed value boundary (spec-v1): a token mask derived
/// from pure prefix legality would then admit dead-end prefixes, so mask and
/// accept must run the completion-reachability filter (ADR-0005). These
/// grammars are never on the finite-literal fast path.
fn needsReachability(nodes: []const Node, root: NodeId, a: std.mem.Allocator) !bool {
    const state = try a.alloc(u8, nodes.len);
    defer a.free(state);
    @memset(state, 0); // 0 unvisited, 1 visited

    const S = struct {
        fn visit(ns: []const Node, st: []u8, id: NodeId) bool {
            if (st[id] != 0) return false;
            st[id] = 1;
            switch (ns[id]) {
                .literal, .lit_trie, .str, .int_v, .num_v, .num_excl => return false,
                .comb, .str_pat, .str_excl, .num_range, .num_mult, .num_const, .int_num, .not_int_num => return true,
                .choice, .seq => |ids| {
                    for (ids) |c| {
                        if (visit(ns, st, c)) return true;
                    }
                    return false;
                },
                .repeat => |r| {
                    for (r.prefix) |c| {
                        if (visit(ns, st, c)) return true;
                    }
                    if (visit(ns, st, r.item)) return true;
                    if (r.contains) |c| {
                        if (visit(ns, st, c)) return true;
                    }
                    return false;
                },
                .object => |o| {
                    for (o.props) |p| {
                        if (visit(ns, st, p.value)) return true;
                    }
                    return false;
                },
                .open_obj => |o| {
                    for (o.props) |p| {
                        if (visit(ns, st, p.value)) return true;
                    }
                    if (visit(ns, st, o.value)) return true;
                    if (o.pattern_lit != null or o.pattern_dfa != null) {
                        if (visit(ns, st, o.pattern_value)) return true;
                    }
                    if (o.prop_names) |pn| {
                        if (visit(ns, st, pn)) return true;
                    }
                    for (o.deps) |d| {
                        if (d.kind == .schema and visit(ns, st, d.schema)) return true;
                    }
                    return false;
                },
            }
        }
    };
    return S.visit(nodes, state, root);
}

/// Nonempty-verdict codes of Grammar.nonempty.
pub const NE_YES: u8 = 1; // the language contains at least one value
pub const NE_NO: u8 = 2; // the language is empty
pub const NE_UNKNOWN: u8 = 3; // not decided here; the search must answer

fn litEql(pool: []const u8, x: Literal, y: Literal) bool {
    return std.mem.eql(u8, pool[x.off .. x.off + x.len], pool[y.off .. y.off + y.len]);
}

fn litsEql(pool: []const u8, xs: []const Literal, ys: []const Literal) bool {
    if (xs.len != ys.len) return false;
    for (xs, ys) |x, y| {
        if (!litEql(pool, x, y)) return false;
    }
    return true;
}

fn numConstEql(x: NumConst, y: NumConst) bool {
    return x.neg == y.neg and x.exp10 == y.exp10 and std.mem.eql(u8, x.digits, y.digits);
}

fn optNumConstEql(x: ?NumConst, y: ?NumConst) bool {
    if ((x == null) != (y == null)) return false;
    if (x) |xc| return numConstEql(xc, y.?);
    return true;
}

/// Memoized structural equality of node pairs of the same grammar (spec-v1):
/// equal structure implies the same language, which lets
/// computeNonempty collapse comb branches that compile to interchangeable
/// subschemas (e.g. allOf/oneOf over independently built identical "any
/// value" nodes), where the intersection is trivially the shared language.
/// Anything not cheaply comparable (pattern DFA internals) compares by
/// identity; a node cycle counts as not equivalent. Compiled node towers
/// share substructures heavily, so results are memoized per ordered pair
/// (without the memo the walk is exponential in the tower depth).
const EqCtx = struct {
    ns: []const Node,
    pool: []const u8,
    a: std.mem.Allocator,
    memo: std.AutoHashMapUnmanaged(u64, u8) = .{}, // 1 equal, 2 not, 3 in progress

    fn deinit(self: *EqCtx) void {
        self.memo.deinit(self.a);
    }

    fn equiv(self: *EqCtx, a: NodeId, b: NodeId) error{OutOfMemory}!bool {
        if (a == b) return true;
        const key = (@as(u64, a) << 32) | b;
        if (self.memo.get(key)) |v| return v == 1; // in progress: cycle, not equivalent
        try self.memo.put(self.a, key, 3);
        const r = try self.equivInner(a, b);
        self.memo.getPtr(key).?.* = if (r) 1 else 2;
        return r;
    }

    fn idsEquiv(self: *EqCtx, xs: []const NodeId, ys: []const NodeId) error{OutOfMemory}!bool {
        if (xs.len != ys.len) return false;
        for (xs, ys) |x, y| {
            if (!try self.equiv(x, y)) return false;
        }
        return true;
    }

    fn optEquiv(self: *EqCtx, x: ?NodeId, y: ?NodeId) error{OutOfMemory}!bool {
        if ((x == null) != (y == null)) return false;
        if (x) |xc| return self.equiv(xc, y.?);
        return true;
    }

    fn propsEquiv(self: *EqCtx, xs: []const Prop, ys: []const Prop) error{OutOfMemory}!bool {
        if (xs.len != ys.len) return false;
        for (xs, ys) |x, y| {
            if (x.required != y.required or !litEql(self.pool, x.key, y.key)) return false;
            if (!try self.equiv(x.value, y.value)) return false;
        }
        return true;
    }

    fn equivInner(self: *EqCtx, a: NodeId, b: NodeId) error{OutOfMemory}!bool {
        const na = self.ns[a];
        const nb = self.ns[b];
        if (std.meta.activeTag(na) != std.meta.activeTag(nb)) return false;
        const pool = self.pool;
        return switch (na) {
            .literal => |x| litEql(pool, x, nb.literal),
            .lit_trie => |x| litsEql(pool, x.literals, nb.lit_trie.literals),
            .str => |x| blk: {
                const y = nb.str;
                break :blk x.min_len == y.min_len and x.max_len == y.max_len;
            },
            .int_v, .num_v, .int_num, .not_int_num => true,
            .num_const => |x| numConstEql(x, nb.num_const),
            .num_excl => |x| blk: {
                const y = nb.num_excl;
                if (x.consts.len != y.consts.len) break :blk false;
                for (x.consts, y.consts) |ca, cb| {
                    if (!numConstEql(ca, cb)) break :blk false;
                }
                break :blk true;
            },
            .str_excl => |x| blk: {
                const y = nb.str_excl;
                break :blk x.min_len == y.min_len and x.max_len == y.max_len and
                    litsEql(pool, x.trie.literals, y.trie.literals);
            },
            .str_pat => |x| blk: {
                const y = nb.str_pat;
                break :blk x.min_len == y.min_len and x.max_len == y.max_len and x.dfa == y.dfa;
            },
            .num_range => |x| blk: {
                const y = nb.num_range;
                break :blk optNumConstEql(x.min, y.min) and optNumConstEql(x.max, y.max) and
                    x.min_excl == y.min_excl and x.max_excl == y.max_excl;
            },
            .num_mult => |x| blk: {
                const y = nb.num_mult;
                break :blk x.div == y.div and x.div_exp10 == y.div_exp10 and x.co == y.co;
            },
            .comb => |x| blk: {
                const y = nb.comb;
                if (x.kind != y.kind or x.branches.len != y.branches.len) break :blk false;
                for (x.branches, y.branches) |ba, bb| {
                    if (ba == COMB_NONE or bb == COMB_NONE) {
                        if (ba != bb) break :blk false;
                    } else if (!try self.equiv(ba, bb)) break :blk false;
                }
                break :blk true;
            },
            .choice => |x| try self.idsEquiv(x, nb.choice),
            .seq => |x| try self.idsEquiv(x, nb.seq),
            .repeat => |x| blk: {
                const y = nb.repeat;
                if (x.min != y.min or x.max != y.max or
                    x.min_contains != y.min_contains or x.max_contains != y.max_contains or
                    x.unique != y.unique or x.uniq_finite != y.uniq_finite) break :blk false;
                break :blk (try self.idsEquiv(x.prefix, y.prefix)) and
                    (try self.equiv(x.item, y.item)) and
                    (try self.optEquiv(x.contains, y.contains));
            },
            .object => |x| try self.propsEquiv(x.props, nb.object.props),
            .open_obj => |x| blk: {
                const y = nb.open_obj;
                if (x.min_props != y.min_props or x.max_props != y.max_props or
                    x.track_keys != y.track_keys or x.capture != y.capture or
                    x.names_forbidden != y.names_forbidden or
                    x.key_min_len != y.key_min_len or x.key_max_len != y.key_max_len or
                    x.pattern_dfa != y.pattern_dfa) break :blk false;
                if (!try self.propsEquiv(x.props, y.props)) break :blk false;
                if (!litsEql(pool, x.extra_required, y.extra_required)) break :blk false;
                if (!try self.equiv(x.value, y.value)) break :blk false;
                if (x.deps.len != y.deps.len) break :blk false;
                for (x.deps, y.deps) |da, db| {
                    if (da.kind != db.kind or !litEql(pool, da.trigger, db.trigger) or
                        !litsEql(pool, da.names, db.names)) break :blk false;
                    if (da.kind == .schema and !try self.equiv(da.schema, db.schema)) break :blk false;
                }
                if (!try self.optEquiv(x.prop_names, y.prop_names)) break :blk false;
                if ((x.pattern_lit == null) != (y.pattern_lit == null)) break :blk false;
                if (x.pattern_lit) |pl| {
                    if (!litEql(pool, pl, y.pattern_lit.?)) break :blk false;
                }
                if (x.pattern_lit != null or x.pattern_dfa != null) {
                    if (!try self.equiv(x.pattern_value, y.pattern_value)) break :blk false;
                }
                break :blk true;
            },
        };
    }
};

fn computeNonempty(nodes: []const Node, pool: []const u8, a: std.mem.Allocator) ![]u8 {
    const st = try a.alloc(u8, nodes.len);
    @memset(st, 0); // 0 unvisited, then NE_*, 4 in progress
    var eqc = EqCtx{ .ns = nodes, .pool = pool, .a = a };
    defer eqc.deinit();

    const S = struct {
        fn visit(ns: []const Node, pl: []const u8, ne: []u8, eqx: *EqCtx, id: NodeId) error{OutOfMemory}!u8 {
            switch (ne[id]) {
                NE_YES, NE_NO, NE_UNKNOWN => return ne[id],
                4 => return NE_UNKNOWN, // node cycle
                else => {},
            }
            ne[id] = 4;
            const v: u8 = switch (ns[id]) {
                // Leaf value machines: compile guarantees a non-empty
                // language for each of these kinds.
                .literal, .lit_trie, .int_v, .num_v, .int_num, .not_int_num, .num_const, .num_excl, .num_range, .num_mult, .str_pat => NE_YES,
                // ...except the impossible-string sentinel (min > max), the
                // compiled form of `false` string positions such as
                // additionalProperties:false fallbacks (schema.zig).
                .str => |sc| if (sc.min_len > sc.max_len) NE_NO else NE_YES,
                // String minus a finite forbidden set: non-empty whenever a
                // positive length is allowed (finitely many forbidden
                // spellings cannot cover a length class); with max_len == 0
                // only "" is allowed and it may be forbidden.
                .str_excl => |se| if (se.max_len != 0) NE_YES else NE_UNKNOWN,
                .choice => |ids| blk: {
                    var unknown = false;
                    for (ids) |c| switch (try visit(ns, pl, ne, eqx, c)) {
                        NE_YES => break :blk NE_YES,
                        NE_UNKNOWN => unknown = true,
                        else => {},
                    };
                    break :blk if (unknown) NE_UNKNOWN else NE_NO;
                },
                .seq => |ids| blk: {
                    var unknown = false;
                    for (ids) |c| switch (try visit(ns, pl, ne, eqx, c)) {
                        NE_NO => break :blk NE_NO,
                        NE_UNKNOWN => unknown = true,
                        else => {},
                    };
                    break :blk if (unknown) NE_UNKNOWN else NE_YES;
                },
                .repeat => |r| blk: {
                    // [] validates when no element and no contains match is
                    // mandatory (prefix slots are positional, not mandatory:
                    // repeatCanClose only counts elements; min_contains is
                    // checked only when a contains node exists).
                    if (r.min == 0 and (r.contains == null or r.min_contains == 0)) break :blk NE_YES;
                    var unknown = false;
                    if (r.min_contains > 0) {
                        // A matching element must exist AND fit the element
                        // schema: only the empty-contains case is exact.
                        const c = r.contains orelse break :blk NE_UNKNOWN;
                        switch (try visit(ns, pl, ne, eqx, c)) {
                            NE_NO => break :blk NE_NO,
                            NE_UNKNOWN => unknown = true,
                            else => unknown = true,
                        }
                    }
                    if (r.unique and r.min > 0) {
                        if (r.prefix.len > 0) {
                            unknown = true; // cross-schema distinctness
                        } else if (r.uniq_finite > 0 and r.min > r.uniq_finite) {
                            break :blk NE_NO; // not enough distinct values
                        }
                    }
                    var i: u32 = 0;
                    while (i < r.min) : (i += 1) {
                        const el = if (i < r.prefix.len) r.prefix[i] else r.item;
                        switch (try visit(ns, pl, ne, eqx, el)) {
                            NE_NO => break :blk NE_NO,
                            NE_UNKNOWN => unknown = true,
                            else => {},
                        }
                    }
                    break :blk if (unknown) NE_UNKNOWN else NE_YES;
                },
                .object => |o| blk: {
                    // Optional props are skippable; required props must all
                    // carry a value.
                    var unknown = false;
                    for (o.props) |p| {
                        if (!p.required) continue;
                        switch (try visit(ns, pl, ne, eqx, p.value)) {
                            NE_NO => break :blk NE_NO,
                            NE_UNKNOWN => unknown = true,
                            else => {},
                        }
                    }
                    break :blk if (unknown) NE_UNKNOWN else NE_YES;
                },
                .open_obj => |o| blk: {
                    var has_req = o.min_props > 0 or o.extra_required.len > 0;
                    for (o.props) |p| {
                        if (p.required) has_req = true;
                    }
                    // The empty object validates regardless of deps,
                    // propertyNames and capture (all trigger on keys).
                    if (!has_req) break :blk NE_YES;
                    if (o.names_forbidden) break :blk NE_NO; // every key is rejected
                    if (o.deps.len != 0 or o.prop_names != null or
                        o.pattern_lit != null or o.pattern_dfa != null or
                        o.max_props != UNBOUNDED) break :blk NE_UNKNOWN;
                    var unknown = false;
                    for (o.props) |p| {
                        if (!p.required) continue;
                        switch (try visit(ns, pl, ne, eqx, p.value)) {
                            NE_NO => break :blk NE_NO,
                            NE_UNKNOWN => unknown = true,
                            else => {},
                        }
                    }
                    if (o.min_props > 0 or o.extra_required.len > 0) {
                        // Undeclared keys are needed: unbounded key length
                        // guarantees fresh keys; the fallback value schema
                        // must be non-empty.
                        if (o.key_max_len != UNBOUNDED) break :blk NE_UNKNOWN;
                        switch (try visit(ns, pl, ne, eqx, o.value)) {
                            NE_NO => break :blk NE_NO,
                            NE_UNKNOWN => unknown = true,
                            else => {},
                        }
                    }
                    break :blk if (unknown) NE_UNKNOWN else NE_YES;
                },
                .comb => |c| switch (c.kind) {
                    // oneOf accepts exactly one branch: non-empty iff
                    // precisely one branch is (certainly) non-empty; when
                    // every branch spells the same language a value matches
                    // all branches or none, and oneOf accepts neither.
                    .oneof => blk: {
                        var yes_count: usize = 0;
                        var unknown = false;
                        for (c.branches) |bid| switch (try visit(ns, pl, ne, eqx, bid)) {
                            NE_YES => yes_count += 1,
                            NE_UNKNOWN => unknown = true,
                            else => {},
                        };
                        if (unknown) break :blk NE_UNKNOWN;
                        if (yes_count == 1) break :blk NE_YES;
                        if (yes_count == 0) break :blk NE_NO;
                        var all_eq = true;
                        for (c.branches[1..]) |bid| {
                            if (!try eqx.equiv(c.branches[0], bid)) {
                                all_eq = false;
                                break;
                            }
                        }
                        break :blk if (all_eq) NE_NO else NE_UNKNOWN;
                    },
                    // An intersection of non-empty languages may still be
                    // empty; exact only when doomed, or when every branch
                    // spells the same language (the intersection is it).
                    .allof => blk: {
                        var unknown = false;
                        for (c.branches) |bid| switch (try visit(ns, pl, ne, eqx, bid)) {
                            NE_NO => break :blk NE_NO,
                            NE_UNKNOWN => unknown = true,
                            else => {},
                        };
                        if (unknown) break :blk NE_UNKNOWN;
                        var all_eq = c.branches.len > 0;
                        for (c.branches[1..]) |bid| {
                            if (!try eqx.equiv(c.branches[0], bid)) {
                                all_eq = false;
                                break;
                            }
                        }
                        break :blk if (all_eq) NE_YES else NE_UNKNOWN;
                    },
                    // (if /\ then) \/ (~if /\ else); COMB_NONE = `true`.
                    .ifelse => blk: {
                        const if_s = try visit(ns, pl, ne, eqx, c.branches[0]);
                        const then_none = c.branches[1] == COMB_NONE;
                        const else_s = if (c.branches[2] == COMB_NONE) NE_YES else try visit(ns, pl, ne, eqx, c.branches[2]);
                        const then_s = if (then_none) NE_YES else try visit(ns, pl, ne, eqx, c.branches[1]);
                        // Term 1 certainly non-empty only when it reduces to
                        // L_if (then absent); term 2 when L_if is empty and
                        // else is non-empty.
                        if ((if_s == NE_YES and then_none) or
                            (if_s == NE_NO and else_s == NE_YES)) break :blk NE_YES;
                        const t1_no = if_s == NE_NO or then_s == NE_NO;
                        if (t1_no and else_s == NE_NO) break :blk NE_NO;
                        break :blk NE_UNKNOWN;
                    },
                },
            };
            ne[id] = v;
            return v;
        }
    };
    for (0..nodes.len) |id| _ = try S.visit(nodes, pool, st, &eqc, @intCast(id));
    improveNonemptyFixpoint(nodes, pool, st, a);
    return st;
}

/// Second phase of computeNonempty: the DFS above returns NE_UNKNOWN for
/// every node on a reference cycle, however trivial the answer (an object
/// whose only cyclic prop is optional is non-empty regardless), and for
/// intersection-shaped nodes (allof of distinct languages, contains vs
/// slot). Three monotone steps, each exact:
///   1. monotone fixpoint passes upgrade UNKNOWN nodes from the children's
///      current verdicts (never overturning a settled verdict; the pass cap
///      only bounds compile time);
///   2. a value-level sweep (src/witset.zig) decides remaining UNKNOWNs by
///      exact certificates: a constructed witness verified against the node
///      (non-empty), a pairwise branch-disjointness proof (allof empty), a
///      contains-vs-slots disjointness proof (repeat empty), or a mandatory
///      key-set contradiction (open_obj empty);
///   3. another round of monotone passes to propagate the sweep's verdicts.
/// What is left is honestly NE_UNKNOWN; the budgeted search answers it.
fn improveNonemptyFixpoint(ns: []const Node, pool: []const u8, ne: []u8, a: std.mem.Allocator) void {
    monotonePasses(ns, ne);
    witnessSweep(ns, pool, ne, a);
    monotonePasses(ns, ne);
}

/// Least-fixpoint passes over the monotone structural rules; upgrades
/// NE_UNKNOWN nodes only, from the children's current verdicts.
fn monotonePasses(ns: []const Node, ne: []u8) void {
    var pass: usize = 0;
    while (pass < 64) : (pass += 1) {
        var changed = false;
        for (ns, 0..) |nd, id_us| {
            if (ne[id_us] != NE_UNKNOWN) continue;
            const v: u8 = switch (nd) {
                .choice => |ids| blk: {
                    var all_no = true;
                    for (ids) |c| {
                        if (ne[c] == NE_YES) break :blk NE_YES;
                        if (ne[c] != NE_NO) all_no = false;
                    }
                    break :blk if (all_no) NE_NO else 0;
                },
                .seq => |ids| blk: {
                    var all_yes = true;
                    for (ids) |c| {
                        if (ne[c] == NE_NO) break :blk NE_NO;
                        if (ne[c] != NE_YES) all_yes = false;
                    }
                    break :blk if (all_yes) NE_YES else 0;
                },
                .repeat => |r| blk: {
                    if (r.min == 0 and (r.contains == null or r.min_contains == 0)) break :blk NE_YES;
                    if (r.min_contains > 0) {
                        if (r.contains) |c| {
                            if (ne[c] == NE_NO) break :blk NE_NO;
                        }
                        // slot x contains intersection is not decided here.
                        break :blk 0;
                    }
                    if (r.unique and r.min > 0) {
                        if (r.prefix.len > 0) break :blk 0;
                        if (r.uniq_finite > 0 and r.min > r.uniq_finite) break :blk NE_NO;
                        if (r.uniq_finite > 0) break :blk 0; // freshness is content-dependent
                    }
                    var all_yes = true;
                    var i: u32 = 0;
                    while (i < r.min) : (i += 1) {
                        const el = if (i < r.prefix.len) r.prefix[i] else r.item;
                        if (ne[el] == NE_NO) break :blk NE_NO;
                        if (ne[el] != NE_YES) all_yes = false;
                    }
                    break :blk if (all_yes) NE_YES else 0;
                },
                .object => |o| blk: {
                    var all_yes = true;
                    for (o.props) |p| {
                        if (!p.required) continue;
                        if (ne[p.value] == NE_NO) break :blk NE_NO;
                        if (ne[p.value] != NE_YES) all_yes = false;
                    }
                    break :blk if (all_yes) NE_YES else 0;
                },
                .open_obj => |o| blk: {
                    var has_req = o.min_props > 0 or o.extra_required.len > 0;
                    for (o.props) |p| {
                        if (p.required) has_req = true;
                    }
                    if (!has_req) break :blk NE_YES;
                    if (o.names_forbidden) break :blk NE_NO;
                    if (o.deps.len != 0 or o.prop_names != null or
                        o.pattern_lit != null or o.pattern_dfa != null or
                        o.max_props != UNBOUNDED) break :blk 0;
                    var all_yes = true;
                    for (o.props) |p| {
                        if (!p.required) continue;
                        if (ne[p.value] == NE_NO) break :blk NE_NO;
                        if (ne[p.value] != NE_YES) all_yes = false;
                    }
                    if (o.min_props > 0 or o.extra_required.len > 0) {
                        if (o.key_max_len != UNBOUNDED) break :blk 0;
                        if (ne[o.value] == NE_NO) break :blk NE_NO;
                        if (ne[o.value] != NE_YES) break :blk 0;
                    }
                    break :blk if (all_yes) NE_YES else 0;
                },
                .comb => |c| switch (c.kind) {
                    .oneof => blk: {
                        var yes: usize = 0;
                        var no: usize = 0;
                        for (c.branches) |bid| switch (ne[bid]) {
                            NE_YES => yes += 1,
                            NE_NO => no += 1,
                            else => {},
                        };
                        if (no == c.branches.len) break :blk NE_NO;
                        // With exactly one non-empty branch every value
                        // votes at most for it, and its values do vote.
                        if (yes == 1 and no == c.branches.len - 1) break :blk NE_YES;
                        break :blk 0;
                    },
                    .allof => blk: {
                        for (c.branches) |bid| {
                            if (ne[bid] == NE_NO) break :blk NE_NO;
                        }
                        break :blk 0;
                    },
                    .ifelse => blk: {
                        const if_s = ne[c.branches[0]];
                        const then_none = c.branches[1] == COMB_NONE;
                        const else_none = c.branches[2] == COMB_NONE;
                        const then_s = if (then_none) NE_YES else ne[c.branches[1]];
                        const else_s = if (else_none) NE_YES else ne[c.branches[2]];
                        if ((if_s == NE_YES and then_none) or
                            (if_s == NE_NO and else_s == NE_YES)) break :blk NE_YES;
                        const t1_no = if_s == NE_NO or then_s == NE_NO;
                        if (t1_no and else_s == NE_NO) break :blk NE_NO;
                        break :blk 0;
                    },
                },
                else => 0, // leaves were settled by the DFS
            };
            if (v != 0) {
                ne[id_us] = v;
                changed = true;
            }
        }
        if (!changed) break;
    }
}

/// Exact value-level certificates for the nodes the structural rules leave
/// NE_UNKNOWN (src/witset.zig): a constructed and verified witness proves
/// NE_YES; disjointness and mandatory-key contradictions prove NE_NO. The
/// construction is heuristic but every verdict is verified exactly, so the
/// sweep is sound; undecided nodes keep NE_UNKNOWN. Runs once between
/// monotone pass rounds: its verdicts do not depend on ne[] and neither do
/// the witness verifications, so a single sweep suffices. Allocations are
/// compile-time scratch in one arena; OOM downgrades to NE_UNKNOWN.
fn witnessSweep(ns: []const Node, pool: []const u8, ne: []u8, a: std.mem.Allocator) void {
    var arena = std.heap.ArenaAllocator.init(a);
    defer arena.deinit();
    const aa = arena.allocator();
    const g = witset.View{ .ns = ns, .pool = pool };
    for (ns, 0..) |nd, id_us| {
        if (ne[id_us] != NE_UNKNOWN) continue;
        const id: NodeId = @intCast(id_us);
        const v: u8 = switch (nd) {
            .comb => |c| blk: {
                if (c.kind == .allof) {
                    // A pairwise-disjoint branch pair empties the intersection.
                    for (c.branches, 0..) |bi, i| {
                        for (c.branches[i + 1 ..]) |bj| {
                            if (bi == COMB_NONE or bj == COMB_NONE) continue;
                            if (witset.disjoint(g, aa, bi, bj) == .yes) break :blk NE_NO;
                        }
                    }
                }
                if (witset.witness(g, aa, id) != null) break :blk NE_YES;
                break :blk 0;
            },
            .repeat => |r| blk: {
                if (r.contains) |cn| {
                    if (r.min_contains > 0) {
                        if (r.max == 0) break :blk NE_NO;
                        // A contains match must sit in some usable slot;
                        // disjoint from every slot means none can.
                        var all_disj = true;
                        var i: usize = 0;
                        while (i < r.prefix.len and i < r.max) : (i += 1) {
                            if (witset.disjoint(g, aa, r.prefix[i], cn) != .yes) {
                                all_disj = false;
                                break;
                            }
                        }
                        if (all_disj and (r.max == UNBOUNDED or r.max > r.prefix.len)) {
                            if (witset.disjoint(g, aa, r.item, cn) != .yes) all_disj = false;
                        }
                        if (all_disj) break :blk NE_NO;
                    }
                }
                if (witset.witness(g, aa, id) != null) break :blk NE_YES;
                break :blk 0;
            },
            .open_obj => |o| blk: {
                if (witset.openObjEmpty(g, aa, &o)) break :blk NE_NO;
                if (witset.witness(g, aa, id) != null) break :blk NE_YES;
                break :blk 0;
            },
            .object, .choice, .seq => blk: {
                if (witset.witness(g, aa, id) != null) break :blk NE_YES;
                break :blk 0;
            },
            else => 0, // leaves were settled by the DFS
        };
        if (v != 0) ne[id_us] = v;
    }
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

    pub fn copyLiterals(self: *Builder, lits: []const Literal) ![]const Literal {
        return self.alloc().dupe(Literal, lits);
    }

    pub fn copyDeps(self: *Builder, deps: []const Dep) ![]const Dep {
        return self.alloc().dupe(Dep, deps);
    }

    pub fn copyNumConsts(self: *Builder, consts: []const NumConst) ![]const NumConst {
        return self.alloc().dupe(NumConst, consts);
    }

    const TmpTrieEdge = struct { byte: u8, child: u32 };
    const TmpTrieNode = struct {
        edges: std.ArrayListUnmanaged(TmpTrieEdge) = .{},
        terminal: bool = false,
    };

    fn tmpEdgeLess(_: void, x: TmpTrieEdge, y: TmpTrieEdge) bool {
        return x.byte < y.byte;
    }

    /// Shared-prefix trie over an explicit literal set (the `literals`
    /// enumeration order is preserved). Used by alternativesNode and by the
    /// spec-v1 P3 str_excl node (forbidden quoted strings of `not`).
    pub fn buildTrie(self: *Builder, lits: []const Literal) !LitTrie {
        const a = self.alloc();
        var tmp: std.ArrayListUnmanaged(TmpTrieNode) = .{};
        errdefer {
            for (tmp.items) |*tn| tn.edges.deinit(a);
            tmp.deinit(a);
        }
        try tmp.append(a, .{});
        for (lits) |lit| {
            var cur: u32 = 0;
            for (self.pool.items[lit.off .. lit.off + lit.len]) |b| {
                var child: ?u32 = null;
                for (tmp.items[cur].edges.items) |e| {
                    if (e.byte == b) {
                        child = e.child;
                        break;
                    }
                }
                if (child == null) {
                    child = @intCast(tmp.items.len);
                    try tmp.append(a, .{});
                    try tmp.items[cur].edges.append(a, .{ .byte = b, .child = child.? });
                }
                cur = child.?;
            }
            tmp.items[cur].terminal = true;
        }
        const n = tmp.items.len;
        var total: usize = 0;
        for (tmp.items) |tn| total += tn.edges.items.len;
        const nodes = try a.alloc(LitTrieNode, n);
        const edges = try a.alloc(LitTrieEdge, total);
        var off: u32 = 0;
        for (tmp.items, 0..) |*tn, i| {
            std.mem.sort(TmpTrieEdge, tn.edges.items, {}, tmpEdgeLess);
            nodes[i] = .{
                .edge_off = off,
                .edge_len = @intCast(tn.edges.items.len),
                .terminal = tn.terminal,
            };
            for (tn.edges.items) |e| {
                edges[off] = .{ .byte = e.byte, .child = e.child };
                off += 1;
            }
            tn.edges.deinit(a);
        }
        tmp.deinit(a);
        return .{ .nodes = nodes, .edges = edges, .literals = lits };
    }

    /// Node for a set of alternatives. A single alternative is returned as
    /// is; an all-literal set becomes a shared-prefix LitTrie; anything
    /// else stays a plain choice.
    pub fn alternativesNode(self: *Builder, ids: []const NodeId) !Node {
        for (ids) |id| switch (self.nodes.items[id]) {
            .literal => {},
            else => return .{ .choice = try self.copyNodeIds(ids) },
        };
        const a = self.alloc();
        const lits = try a.alloc(Literal, ids.len);
        for (ids, 0..) |id, i| {
            lits[i] = self.nodes.items[id].literal;
        }
        return .{ .lit_trie = try self.buildTrie(lits) };
    }

    pub fn finish(self: *Builder, arena: std.heap.ArenaAllocator, kind: GrammarKind, root: NodeId, ident: Identity) !Grammar {
        // The memoization buffer must come from the arena that is stored in
        // the grammar (allocations through `self.a` would land in the
        // caller's local arena copy and leak).
        var arena_mut = arena;
        const finite = try isFiniteLiteral(self.nodes.items, root, arena_mut.allocator());
        const reach = try needsReachability(self.nodes.items, root, arena_mut.allocator());
        const aa = arena_mut.allocator();
        const pat_len = try aa.alloc(?*const pattern.PatLenDescr, self.nodes.items.len);
        @memset(pat_len, null);
        var nonempty: []const u8 = &.{};
        if (reach) {
            nonempty = try computeNonempty(self.nodes.items, self.pool.items, aa);
            var by_dfa: std.AutoHashMapUnmanaged(usize, ?*const pattern.PatLenDescr) = .{};
            for (self.nodes.items, 0..) |n, id| {
                const dfa: *const pattern.Dfa = switch (n) {
                    .str_pat => |sp| sp.dfa,
                    // open_obj patternProperties DFAs need the same exact
                    // continuation-length descriptors: the key certifier
                    // (complete.zig certOpenObjKeyStr) answers mid-key
                    // residual questions with them.
                    .open_obj => |oo| oo.pattern_dfa orelse continue,
                    else => continue,
                };
                const gop = try by_dfa.getOrPut(aa, @intFromPtr(dfa));
                if (!gop.found_existing) {
                    var budget = pattern.LenBudget{};
                    gop.value_ptr.* = try pattern.computeLenDescr(aa, dfa, &budget);
                }
                pat_len[id] = gop.value_ptr.*;
            }
        }
        return .{
            .kind = kind,
            .nodes = self.nodes.items,
            .literal_pool = self.pool.items,
            .root = root,
            .id = ident.lo,
            .id_hi = ident.hi,
            .finite_literal = finite,
            .needs_reachability = reach,
            .pat_len = pat_len,
            .nonempty = nonempty,
            .arena = arena_mut,
        };
    }
};

// ---- spec-v1 P4: exact decimal helpers ----

test "cmpNumConst: exact decimal ordering by value" {
    const z: NumConst = .{ .neg = false, .digits = "", .exp10 = 0 };
    const one: NumConst = .{ .neg = false, .digits = "1", .exp10 = 0 };
    const one_frac: NumConst = .{ .neg = false, .digits = "1", .exp10 = 0 }; // 1.0 canonicalizes here
    const two: NumConst = .{ .neg = false, .digits = "2", .exp10 = 0 };
    const m_one: NumConst = .{ .neg = true, .digits = "1", .exp10 = 0 };
    const m_two: NumConst = .{ .neg = true, .digits = "2", .exp10 = 0 };
    const p11: NumConst = .{ .neg = false, .digits = "11", .exp10 = -1 }; // 1.1
    const p100001: NumConst = .{ .neg = false, .digits = "100001", .exp10 = -4 }; // 10.0001
    const p10: NumConst = .{ .neg = false, .digits = "1", .exp10 = 1 }; // 10
    const m20001: NumConst = .{ .neg = true, .digits = "20001", .exp10 = -4 }; // -2.0001
    const testing = std.testing;
    try testing.expectEqual(std.math.Order.eq, cmpNumConst(z, z));
    try testing.expectEqual(std.math.Order.eq, cmpNumConst(one, one_frac));
    try testing.expectEqual(std.math.Order.lt, cmpNumConst(z, one));
    try testing.expectEqual(std.math.Order.gt, cmpNumConst(z, m_one));
    try testing.expectEqual(std.math.Order.lt, cmpNumConst(m_two, m_one)); // -2 < -1
    try testing.expectEqual(std.math.Order.gt, cmpNumConst(m_one, m_two));
    try testing.expectEqual(std.math.Order.lt, cmpNumConst(m_one, z));
    try testing.expectEqual(std.math.Order.gt, cmpNumConst(p11, one)); // 1.1 > 1
    try testing.expectEqual(std.math.Order.gt, cmpNumConst(p100001, p10)); // 10.0001 > 10
    try testing.expectEqual(std.math.Order.lt, cmpNumConst(p10, p100001));
    try testing.expectEqual(std.math.Order.lt, cmpNumConst(m20001, m_two)); // -2.0001 < -2
    try testing.expectEqual(std.math.Order.lt, cmpNumConst(one, two));
    // Different order of magnitude beats the digit walk.
    const big: NumConst = .{ .neg = false, .digits = "1", .exp10 = 30 };
    const many: NumConst = .{ .neg = false, .digits = "999999999", .exp10 = 0 };
    try testing.expectEqual(std.math.Order.gt, cmpNumConst(big, many));
    try testing.expectEqual(std.math.Order.lt, cmpNumConst(m_one, many));
}

test "numConstInRange: inclusive/exclusive bounds by value" {
    const nr: NumRange = .{
        .min = .{ .neg = true, .digits = "2", .exp10 = 0 },
        .max = .{ .neg = false, .digits = "3", .exp10 = 0 },
    };
    const testing = std.testing;
    try testing.expect(numConstInRange(.{ .neg = false, .digits = "", .exp10 = 0 }, nr));
    try testing.expect(numConstInRange(.{ .neg = true, .digits = "2", .exp10 = 0 }, nr)); // -2 incl
    try testing.expect(!numConstInRange(.{ .neg = true, .digits = "20001", .exp10 = -4 }, nr));
    try testing.expect(numConstInRange(.{ .neg = false, .digits = "3", .exp10 = 0 }, nr));
    try testing.expect(!numConstInRange(.{ .neg = false, .digits = "3001", .exp10 = -3 }, nr));
    const excl: NumRange = .{ .min = .{ .neg = false, .digits = "11", .exp10 = -1 }, .min_excl = true };
    try testing.expect(!numConstInRange(.{ .neg = false, .digits = "11", .exp10 = -1 }, excl));
    try testing.expect(numConstInRange(.{ .neg = false, .digits = "111", .exp10 = -2 }, excl));
}

test "numConstIsMultiple: exact decimal divisibility" {
    const testing = std.testing;
    const two: NumMult = .{ .div = 2, .div_exp10 = 0, .co = 1 };
    try testing.expect(numConstIsMultiple(.{ .neg = false, .digits = "", .exp10 = 0 }, two)); // 0
    try testing.expect(numConstIsMultiple(.{ .neg = false, .digits = "1", .exp10 = 1 }, two)); // 10
    try testing.expect(!numConstIsMultiple(.{ .neg = false, .digits = "7", .exp10 = 0 }, two));
    try testing.expect(numConstIsMultiple(.{ .neg = true, .digits = "1", .exp10 = 3 }, two)); // -1000
    const p15: NumMult = .{ .div = 15, .div_exp10 = -1, .co = 3 }; // 1.5
    try testing.expect(numConstIsMultiple(.{ .neg = false, .digits = "45", .exp10 = -1 }, p15)); // 4.5
    try testing.expect(numConstIsMultiple(.{ .neg = false, .digits = "3", .exp10 = 0 }, p15)); // 3
    try testing.expect(!numConstIsMultiple(.{ .neg = false, .digits = "35", .exp10 = 0 }, p15)); // 35
    try testing.expect(!numConstIsMultiple(.{ .neg = false, .digits = "75", .exp10 = -2 }, p15)); // 0.75
    const tiny: NumMult = .{ .div = 1, .div_exp10 = -4, .co = 1 }; // 0.0001
    try testing.expect(numConstIsMultiple(.{ .neg = false, .digits = "75", .exp10 = -4 }, tiny)); // 0.0075
    try testing.expect(!numConstIsMultiple(.{ .neg = false, .digits = "751", .exp10 = -5 }, tiny)); // 0.00751
    const d: NumMult = .{ .div = 123456789, .div_exp10 = -9, .co = 123456789 };
    try testing.expect(!numConstIsMultiple(.{ .neg = false, .digits = "1", .exp10 = 308 }, d)); // 1e308
}
