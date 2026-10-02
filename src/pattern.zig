//! pattern.zig - ECMA-262 regular-expression subset compiled to a DFA over
//! Unicode scalar values (spec-v1 P4; ROADMAP section 5 "pattern regex
//! subset"; docs/dialect-matrix.md section 5 row `pattern`: "ECMA-262 regex;
//! P4 subset, unsupported constructs refused with a pointer").
//!
//! This module is self-contained (only `std`). Integration into schema.zig /
//! parser.zig is a separate step; the contract here is what that step needs.
//!
//! MATCH DOMAIN
//! ------------
//! Matching operates on the VALUE of a JSON string: a sequence of Unicode
//! scalar values (codepoints, surrogates excluded). The parser decodes
//! `\uXXXX` escapes before feeding codepoints, exactly as it already does for
//! literal-substring patternProperties (parser.zig keyMatchesPattern); this
//! DFA never sees raw lexeme bytes. Feeding a surrogate or a value above
//! U+10FFFF is a contract violation and leads to the dead state.
//!
//! SUPPORTED SUBSET (grammar; `x` = repetition of the preceding element)
//! ---------------------------------------------------------------------
//!   pattern   := alt
//!   alt       := concat ('|' concat)*                 // including empty branches
//!   concat    := repeat*
//!   repeat    := atom quantifier? lazy?
//!   quantifier:= '*' | '+' | '?' | '{n}' | '{n,}' | '{n,m}'   (n <= m)
//!   lazy      := '?'                                  // accepted, ignored: a DFA
//!                                                     // only needs the language
//!   atom      := '(' alt ')' | '(?:' alt ')' | '(?<name>' alt ')'
//!              | '[' class ']' | '.' | '^' | '$' | '\' escape | literal-cp
//!   class     := '^'? class-atom ('-' class-atom)? ... ']'
//!   class-atom:= '\' class-escape | cp                // ranges, negation,
//!                                                     // '\d' etc. nest in classes
//!   escape    := 'd' 'D' 'w' 'W' 's' 'S' | 'n' 'r' 't' 'f' 'v' | '0'
//!              | 'x' HH | 'u' HHHH | 'u{' H+ '}' | 'c' letter
//!              | syntax-char / '/' / non-ASCII (identity escape)
//!
//! Semantics notes:
//! - `^` / `$` are begin/end-of-STRING assertions (no multiline flag exists in
//!   JSON Schema patterns). They may appear anywhere an atom may; a `^` in a
//!   position the match cannot reach simply never fires (e.g. `a^b` matches
//!   nothing), exactly as ECMA-262 without the `m` flag.
//! - `.` excludes the four line terminators U+000A U+000D U+2028 U+2029.
//! - `\d` = [0-9], `\w` = [0-9A-Za-z_] (ASCII, no `u` flag); `\s` is the
//!   ECMA-262 WhiteSpace + LineTerminator set (includes U+00A0, U+1680,
//!   U+2000..U+200A, U+2028, U+2029, U+202F, U+205F, U+3000, U+FEFF).
//! - `{` that does not form a valid braced quantifier is a literal, and a
//!   `{...}` following a quantified atom is a literal too (ECMA-262 Annex B).
//! - A quantifier on `^`/`$` is accepted and ignored (assertion repetition is
//!   the assertion; Annex B allows the form).
//! - Consecutive `\uD800-\uDBFF` `\uDC00-\uDFFF` escapes combine into one
//!   scalar value, as ECMA-262 does; a lone surrogate escape is Invalid.
//! - Empty classes `[]` are legal and match nothing; `[^]` matches any single
//!   scalar value (unlike `.` it includes line terminators).
//!
//! REFUSED CONSTRUCTS (compile-time; the integration maps these to
//! UNSUPPORTED_FEATURE / INVALID_SCHEMA with the pattern's JSON pointer):
//! - error.Unsupported: backreferences `\1`..`\9` and `\k<name>`, lookaround
//!   `(?= (?! (?<= (?<!`, inline-flag and other `(?...)` group forms,
//!   word boundaries `\b` / `\B` outside classes (they need adjacent-character
//!   context this streaming model does not carry), Unicode property classes
//!   `\p{...}` / `\P{...}` (u-flag feature), legacy octal escapes (`\0`
//!   followed by a digit, `\N` digits in classes), and any limit overflow
//!   (Limits below).
//! - error.Invalid: malformed syntax (unterminated class/group/escape, `z-a`
//!   or class-set range endpoints, `n > m` quantifiers, lone surrogates,
//!   quantifier without an atom, unknown ASCII-letter escapes).
//!
//! LIMITS (Defaults in Limits; every overflow is error.Unsupported)
//! ----------------------------------------------------------------
//! - pattern length <= max_pattern_bytes (4096)
//! - group nesting <= max_group_depth (255)
//! - quantifier counts <= max_repeat (1000)
//! - NFA states <= max_nfa_states (8192)  (bounds {n,m} expansion)
//! - DFA states <= max_dfa_states (4096)  (bounds determinization)
//! DFA memory is therefore bounded: <= 4096 states, transitions allocated
//! proportionally to the subset-construction result, all inside one arena.
//! Typical schema patterns compile in ~60-110 us (ReleaseSafe; ~0.4-1.4 ms
//! in Debug) and produce tables of a few dozen states / a few KB - measured
//! on email/uuid/date/ipv4-shaped patterns, Zig 0.15.2.
//!
//! API
//! ---
//!   compile(allocator, pattern_bytes, Options) Error!Dfa
//!   compileJsonSchema(allocator, pattern_bytes) Error!Dfa  // search mode
//!   Options.mode: .search (JSON Schema `pattern`: unanchored partial match)
//!   or .full (whole string must match).
//!   Dfa.start() RunState           initial runtime state
//!   Dfa.feed(state, cp) RunState   transition on one codepoint (dead on miss)
//!   Dfa.isAccept(state) bool       current string prefix matches
//!   Dfa.dead(state) bool           no continuation can ever match
//!   Dfa.ranges(state) RangeIter    allowed codepoint ranges out of `state`,
//!                                  skipping transitions into dead states
//!                                  (this is what token-mask generation needs)
//!   Dfa.matches(cps) / Dfa.matchesUtf8(bytes)   convenience full-string check
//!   Dfa.stateCount()               DFA table size, <= max_dfa_states
//!   RunState is a u32 POD (run_state_bytes = 4); writeRunState/readRunState
//!   serialize it little-endian for embedding into parser states/cache keys.
//!   Dfa.deinit() frees the whole arena.
//!
//! SEARCH-MODE WRAPPING (JSON Schema partial match)
//! ------------------------------------------------
//! `pattern` is not textual-wrapped in `.*`: `.` excludes line terminators,
//! and a textual wrapper would rebind `^`/`$`. Instead the NFA gets a wrapper
//! start state with a self-loop over the whole scalar domain and an epsilon
//! into the pattern (a match may start at any position), and the accept state
//! gets a sticky self-loop (once a substring matched, trailing input is
//! irrelevant). `^`-gated epsilon edges fire only in the initial closure,
//! `$`-gated edges only in the end-of-string accept check, so anchors keep
//! binding to the string edges. The accepted language is exactly Sigma* L
//! Sigma* over scalar values with L's anchors interpreted at the edges.

const std = @import("std");

pub const Error = error{ Invalid, Unsupported, OutOfMemory };

pub const Mode = enum {
    /// JSON Schema `pattern` semantics: unanchored partial match.
    search,
    /// The whole string must match the pattern.
    full,
};

pub const Limits = struct {
    max_pattern_bytes: usize = 4096,
    max_group_depth: u32 = 255,
    max_repeat: u32 = 1000,
    max_nfa_states: u32 = 8192,
    max_dfa_states: u32 = 4096,
};

pub const Options = struct {
    mode: Mode = .search,
    limits: Limits = .{},
};

/// Inclusive codepoint range, as yielded by RangeIter for mask generation.
pub const Range = struct { lo: u21, hi: u21 };

/// Runtime DFA state: a POD index into Dfa.states; 0 is the dead state.
pub const RunState = u32;
pub const run_state_bytes: usize = @sizeOf(RunState);

pub fn writeRunState(s: RunState, out: *[run_state_bytes]u8) void {
    std.mem.writeInt(u32, out, s, .little);
}

pub fn readRunState(in: *const [run_state_bytes]u8) RunState {
    return std.mem.readInt(u32, in, .little);
}

/// Compile a pattern. `pattern` is the raw UTF-8 of the regex source (for
/// JSON Schema: the decoded value of the `pattern` keyword string).
pub fn compile(child_allocator: std.mem.Allocator, pattern: []const u8, options: Options) Error!Dfa {
    const lim = &options.limits;
    if (pattern.len > lim.max_pattern_bytes) return error.Unsupported;
    var arena = std.heap.ArenaAllocator.init(child_allocator);
    errdefer arena.deinit();
    const a = arena.allocator();

    var p = Parser{ .a = a, .src = pattern, .lim = lim };
    const root = try p.parseRoot();

    var nb = NfaBuilder{ .a = a, .lim = lim };
    const entry = try nb.newState();
    const fin = try nb.newState();
    nb.states.items[fin].accept = true;
    try nb.emit(root, entry, fin);

    var seed: [1]u32 = undefined;
    switch (options.mode) {
        .full => seed[0] = entry,
        .search => {
            // See the header: unanchored-search wrapper, sticky accept.
            const wrap = try nb.newState();
            for (scalar_domain) |d| try nb.trans(wrap, d.lo, d.hi, wrap);
            try nb.eps(wrap, entry, .always);
            for (scalar_domain) |d| try nb.trans(fin, d.lo, d.hi, fin);
            seed[0] = wrap;
        },
    }

    const states = try determinize(a, nb.states.items, seed[0..1], lim);
    return .{ .arena = arena, .states = states };
}

/// JSON Schema `pattern` / `patternProperties`: ECMA-262 unanchored search
/// with the default limits. This is the entry point the schema compiler uses.
pub fn compileJsonSchema(child_allocator: std.mem.Allocator, pattern: []const u8) Error!Dfa {
    return compile(child_allocator, pattern, .{});
}

pub const Dfa = struct {
    arena: std.heap.ArenaAllocator,
    /// Index 0 is the dead state, index 1 is the start state.
    states: []const State,

    pub const State = struct {
        /// Sorted by lo, disjoint, covering only live transitions.
        trans: []const Trans,
        accept: bool,
    };
    pub const Trans = struct { lo: u32, hi: u32, to: u32 };

    pub fn deinit(self: *Dfa) void {
        self.arena.deinit();
    }

    pub fn start(self: *const Dfa) RunState {
        _ = self;
        return 1;
    }

    pub fn stateCount(self: *const Dfa) u32 {
        return @intCast(self.states.len);
    }

    pub fn isAccept(self: *const Dfa, s: RunState) bool {
        if (s >= self.states.len) return false;
        return self.states[s].accept;
    }

    /// True when no continuation of the input can ever reach an accept
    /// state. Note an accept state with no outgoing transitions is not dead:
    /// the string matches as it stands.
    pub fn dead(self: *const Dfa, s: RunState) bool {
        if (s >= self.states.len) return true;
        return self.states[s].trans.len == 0 and !self.states[s].accept;
    }

    pub fn feed(self: *const Dfa, s: RunState, cp: u21) RunState {
        if (s >= self.states.len) return 0;
        const c: u32 = cp;
        const ts = self.states[s].trans;
        var lo: usize = 0;
        var hi: usize = ts.len;
        while (lo < hi) {
            const mid = lo + (hi - lo) / 2;
            const t = ts[mid];
            if (c < t.lo) {
                hi = mid;
            } else if (c > t.hi) {
                lo = mid + 1;
            } else {
                return t.to;
            }
        }
        return 0;
    }

    /// Iterate the codepoint ranges admitted out of `state`, skipping
    /// transitions into dead states (dead ends are useless for masks).
    pub fn ranges(self: *const Dfa, s: RunState) RangeIter {
        if (s >= self.states.len) return .{ .dfa = self, .trans = &.{} };
        return .{ .dfa = self, .trans = self.states[s].trans };
    }

    pub fn matches(self: *const Dfa, cps: []const u21) bool {
        var s = self.start();
        for (cps) |cp| {
            s = self.feed(s, cp);
            if (self.dead(s)) return false;
        }
        return self.isAccept(s);
    }

    /// Matches the decoded value of a JSON string, given as UTF-8 bytes.
    pub fn matchesUtf8(self: *const Dfa, bytes: []const u8) error{Invalid}!bool {
        var s = self.start();
        var i: usize = 0;
        while (i < bytes.len) {
            const d = try decodeCpAt(bytes, i);
            s = self.feed(s, d.cp);
            i += d.len;
            if (self.dead(s)) return false;
        }
        return self.isAccept(s);
    }

    /// BFS over the DFA: the shortest input length (in codepoints) that
    /// reaches an accept state, or null when the DFA accepts nothing. When
    /// `path` is given, it receives one shortest witness input (the lowest
    /// codepoint of the taken range at every step). In search mode the
    /// accept states are sticky over the whole scalar domain, so the
    /// accepted length set is exactly [result, infinity) and a witness of
    /// any greater length is the path padded with any scalar value.
    /// Levels are bounded by the state count (a shortest path is simple).
    pub fn shortestAccept(self: *const Dfa, a: std.mem.Allocator, path: ?*std.ArrayListUnmanaged(u21)) error{OutOfMemory}!?u32 {
        const n = self.states.len;
        const start_state = self.start();
        if (self.isAccept(start_state)) return 0;
        const unvisited = std.math.maxInt(u32);
        const prev = try a.alloc(u32, n);
        defer a.free(prev);
        const via = try a.alloc(u21, n);
        defer a.free(via);
        @memset(prev, unvisited);
        prev[start_state] = start_state;
        var frontier: std.ArrayListUnmanaged(u32) = .{};
        defer frontier.deinit(a);
        var upcoming: std.ArrayListUnmanaged(u32) = .{};
        defer upcoming.deinit(a);
        try frontier.append(a, start_state);
        var depth: u32 = 0;
        while (frontier.items.len > 0 and depth < n) : (depth += 1) {
            for (frontier.items) |s| {
                for (self.states[s].trans) |t| {
                    if (prev[t.to] != unvisited) continue;
                    if (self.dead(t.to)) continue;
                    prev[t.to] = s;
                    via[t.to] = @intCast(t.lo);
                    if (self.isAccept(t.to)) {
                        if (path) |p| {
                            var cur = t.to;
                            while (cur != start_state) {
                                try p.append(a, via[cur]);
                                cur = prev[cur];
                            }
                            std.mem.reverse(u21, p.items);
                        }
                        return depth + 1;
                    }
                    try upcoming.append(a, t.to);
                }
            }
            const tmp = frontier;
            frontier = upcoming;
            upcoming = tmp;
            upcoming.clearRetainingCapacity();
        }
        return null;
    }
};

pub const RangeIter = struct {
    dfa: *const Dfa,
    trans: []const Dfa.Trans,
    idx: usize = 0,

    pub fn next(self: *RangeIter) ?Range {
        while (self.idx < self.trans.len) {
            const t = self.trans[self.idx];
            self.idx += 1;
            if (self.dfa.dead(t.to)) continue;
            return .{ .lo = @intCast(t.lo), .hi = @intCast(t.hi) };
        }
        return null;
    }
};

// ---------------------------------------------------------------------------
// spec-v1 (ADR-0005): exact per-state length-acceptance descriptors.
//
// The search-mode wrapper keeps a live state for every content byte even
// when no continuation can ever match (a `^`-anchored mismatch survives as
// the wrapper self-loop), so `dead()` underapproximates doom. The residual
// reachability filter (src/complete.zig) needs the exact answer: from DFA
// state s, for which continuation lengths L (in codepoints) is an accept
// state reachable in EXACTLY L steps. That set is computed exactly by
// evolving the reachable-subset sequence S_0 = {s}, S_{l+1} = succ(S_l)
// until a subset repeats: the evolution is deterministic over a finite
// powerset, so it is eventually periodic; the first repeat pins the
// preperiod and the period, and bit l of `bits` records
// "accept reachable in exactly l steps" for l < preperiod + period.
// ---------------------------------------------------------------------------

pub const LenDescr = struct {
    preperiod: u32,
    period: u32, // >= 1
    /// Bit l (l < preperiod + period): an accept state is reachable from the
    /// described state in exactly l codepoint steps.
    bits: []const u64,

    pub fn acceptsLen(self: *const LenDescr, l: u64) bool {
        const pp: u64 = self.preperiod;
        const p: u64 = self.period;
        const idx: u64 = if (l < pp + p) l else pp + (l - pp) % p;
        return (self.bits[@intCast(idx / 64)] >> @intCast(idx % 64)) & 1 != 0;
    }

    /// True when some continuation length L in [lo, hi] (hi = null means
    /// unbounded) reaches an accept state in exactly L steps.
    pub fn reachableIn(self: *const LenDescr, lo: u32, hi: ?u32) bool {
        const pp: u64 = self.preperiod;
        const p: u64 = self.period;
        const span: u64 = pp + p;
        const lo64: u64 = lo;
        const hi64: u64 = if (hi) |h| h else std.math.maxInt(u64);
        if (lo64 > hi64) return false;
        const direct_end = @min(hi64, span - 1); // inclusive
        var l: u64 = lo64;
        while (l <= direct_end) : (l += 1) {
            if (self.acceptsLen(l)) return true;
        }
        if (hi64 >= span) {
            var r: u64 = 0;
            while (r < p) : (r += 1) {
                if (!self.acceptsLen(pp + r)) continue;
                const base = pp + r;
                const first = if (base >= lo64) base else base + ((lo64 - base + p - 1) / p) * p;
                if (first <= hi64) return true;
            }
        }
        return false;
    }
};

/// Exact length-acceptance descriptors of every DFA state (index = RunState).
pub const PatLenDescr = struct {
    states: []const LenDescr,

    pub fn reachableIn(self: *const PatLenDescr, s: RunState, lo: u32, hi: ?u32) bool {
        if (s >= self.states.len) return false;
        return self.states[s].reachableIn(lo, hi);
    }
};

/// Analysis budget shared across the states of one DFA; when it runs out the
/// descriptor is not built (computeLenDescr returns null) and the caller must
/// treat the combination as undecided instead of guessing.
pub const LenBudget = struct {
    /// Total subset-evolution steps over all states of one DFA.
    steps: u64 = 1 << 17,
    /// Total descriptor bits over all states of one DFA (~512 KiB).
    bits: u64 = 1 << 22,
};

const SubsetKeyCtx = struct {
    pub fn hash(_: SubsetKeyCtx, key: []const u64) u64 {
        return std.hash.Wyhash.hash(0, std.mem.sliceAsBytes(key));
    }
    pub fn eql(_: SubsetKeyCtx, a: []const u64, b: []const u64) bool {
        return std.mem.eql(u64, a, b);
    }
};

fn computeStateLenDescr(a: std.mem.Allocator, dfa: *const Dfa, s0: RunState, budget: *LenBudget) error{OutOfMemory}!?LenDescr {
    const n = dfa.states.len;
    const words = (n + 63) / 64;
    var cur = try a.alloc(u64, words);
    @memset(cur, 0);
    cur[s0 / 64] |= @as(u64, 1) << @intCast(s0 % 64);
    var next = try a.alloc(u64, words);
    var seen: std.HashMapUnmanaged([]const u64, u32, SubsetKeyCtx, 75) = .{};
    var accept_bits: std.ArrayListUnmanaged(u64) = .{};
    var nbits: u64 = 0;
    var step: u32 = 0;
    while (true) : (step += 1) {
        if (budget.steps == 0) return null;
        budget.steps -= 1;
        const gop = try seen.getOrPut(a, cur);
        if (gop.found_existing) {
            const pre = gop.value_ptr.*;
            return .{ .preperiod = pre, .period = step - pre, .bits = accept_bits.items };
        }
        gop.key_ptr.* = cur;
        gop.value_ptr.* = step;
        // accept bit of this level
        var acc = false;
        for (dfa.states, 0..) |*ds, si| {
            if (ds.accept and (cur[si / 64] >> @intCast(si % 64)) & 1 != 0) {
                acc = true;
                break;
            }
        }
        if (step % 64 == 0) try accept_bits.append(a, 0);
        if (acc) accept_bits.items[step / 64] |= @as(u64, 1) << @intCast(step % 64);
        nbits += 1;
        if (nbits > budget.bits) return null;
        // successor subset
        @memset(next, 0);
        var empty = true;
        for (dfa.states, 0..) |*ds, si| {
            if ((cur[si / 64] >> @intCast(si % 64)) & 1 == 0) continue;
            for (ds.trans) |t| {
                next[t.to / 64] |= @as(u64, 1) << @intCast(t.to % 64);
                empty = false;
            }
        }
        cur = try a.dupe(u64, next);
        if (empty) {
            // The dead subset repeats itself forever: period 1, no accepts.
            // Append the (clear) bit of the empty level so the bitset covers
            // [0, preperiod + period).
            if ((step + 1) % 64 == 0) try accept_bits.append(a, 0);
            return .{ .preperiod = step + 1, .period = 1, .bits = accept_bits.items };
        }
    }
}

/// Computes the exact length-acceptance descriptor of every DFA state, or
/// null when the analysis budget is exhausted (an undecided combination,
/// never a silently wrong answer). All memory lives in `a` (the grammar
/// arena in production).
pub fn computeLenDescr(a: std.mem.Allocator, dfa: *const Dfa, budget: *LenBudget) error{OutOfMemory}!?*PatLenDescr {
    const states = try a.alloc(LenDescr, dfa.states.len);
    for (0..dfa.states.len) |s| {
        states[s] = (try computeStateLenDescr(a, dfa, @intCast(s), budget)) orelse return null;
    }
    const out = try a.create(PatLenDescr);
    out.* = .{ .states = states };
    return out;
}

// ---------------------------------------------------------------- internals

const Range32 = struct { lo: u32, hi: u32 }; // inclusive

/// The match domain: Unicode scalar values (surrogates excluded).
const scalar_domain = [_]Range32{
    .{ .lo = 0x0000, .hi = 0xD7FF },
    .{ .lo = 0xE000, .hi = 0x10FFFF },
};

const set_digit = [_]Range32{.{ .lo = 0x30, .hi = 0x39 }};
const set_word = [_]Range32{
    .{ .lo = 0x30, .hi = 0x39 },
    .{ .lo = 0x41, .hi = 0x5A },
    .{ .lo = 0x5F, .hi = 0x5F },
    .{ .lo = 0x61, .hi = 0x7A },
};
// ECMA-262 WhiteSpace + LineTerminator.
const set_space = [_]Range32{
    .{ .lo = 0x09, .hi = 0x0D },
    .{ .lo = 0x20, .hi = 0x20 },
    .{ .lo = 0xA0, .hi = 0xA0 },
    .{ .lo = 0x1680, .hi = 0x1680 },
    .{ .lo = 0x2000, .hi = 0x200A },
    .{ .lo = 0x2028, .hi = 0x2029 },
    .{ .lo = 0x202F, .hi = 0x202F },
    .{ .lo = 0x205F, .hi = 0x205F },
    .{ .lo = 0x3000, .hi = 0x3000 },
    .{ .lo = 0xFEFF, .hi = 0xFEFF },
};
// `.` : any scalar value except the four line terminators.
const set_any = [_]Range32{
    .{ .lo = 0x0000, .hi = 0x0009 },
    .{ .lo = 0x000B, .hi = 0x000C },
    .{ .lo = 0x000E, .hi = 0x2027 },
    .{ .lo = 0x202A, .hi = 0xD7FF },
    .{ .lo = 0xE000, .hi = 0x10FFFF },
};

const Decoded = struct { cp: u21, len: u8 };

/// Strict UTF-8 decode of one codepoint (rejects overlong forms, surrogates
/// and values above U+10FFFF: the input must be scalar values).
fn decodeCpAt(src: []const u8, pos: usize) error{Invalid}!Decoded {
    const b0 = src[pos];
    if (b0 < 0x80) return .{ .cp = b0, .len = 1 };
    const len: u8 = if (b0 & 0xE0 == 0xC0) 2 else if (b0 & 0xF0 == 0xE0) 3 else if (b0 & 0xF8 == 0xF0) 4 else return error.Invalid;
    if (pos + len > src.len) return error.Invalid;
    var cp: u32 = b0 & (@as(u8, 0x7F) >> @as(u3, @intCast(len)));
    for (src[pos + 1 .. pos + len]) |b| {
        if (b & 0xC0 != 0x80) return error.Invalid;
        cp = (cp << 6) | (b & 0x3F);
    }
    const min: u32 = switch (len) {
        2 => 0x80,
        3 => 0x800,
        else => 0x10000,
    };
    if (cp < min or cp > 0x10FFFF) return error.Invalid;
    if (cp >= 0xD800 and cp <= 0xDFFF) return error.Invalid;
    return .{ .cp = @intCast(cp), .len = len };
}

fn rangeLess(_: void, x: Range32, y: Range32) bool {
    return x.lo < y.lo;
}

/// Sort and merge overlapping or adjacent ranges.
fn normRanges(a: std.mem.Allocator, items: []Range32) Error![]Range32 {
    std.mem.sort(Range32, items, {}, rangeLess);
    var out: std.ArrayListUnmanaged(Range32) = .{};
    for (items) |r| {
        if (out.items.len > 0) {
            const last = &out.items[out.items.len - 1];
            if (r.lo <= last.hi + 1) {
                if (r.hi > last.hi) last.hi = r.hi;
                continue;
            }
        }
        try out.append(a, r);
    }
    return out.toOwnedSlice(a);
}

/// Complement of a normalized range set within the scalar-value domain.
fn complementRanges(a: std.mem.Allocator, rs: []const Range32) Error![]Range32 {
    var out: std.ArrayListUnmanaged(Range32) = .{};
    for (scalar_domain) |dom| {
        var cur = dom.lo;
        for (rs) |r| {
            if (r.hi < cur) continue;
            if (r.lo > dom.hi) break;
            if (r.lo > cur) try out.append(a, .{ .lo = cur, .hi = r.lo - 1 });
            if (r.hi >= dom.hi) {
                cur = dom.hi + 1;
                break;
            }
            cur = r.hi + 1;
        }
        if (cur <= dom.hi) try out.append(a, .{ .lo = cur, .hi = dom.hi });
    }
    return out.toOwnedSlice(a);
}

// --------------------------------------------------------------------- AST

const Node = union(enum) {
    empty, // empty branch / empty concatenation: matches the empty string
    char: u21,
    set: []const Range32, // normalized, within the scalar domain
    concat: []const *Node,
    alt: []const *Node,
    rep: Rep,
    bos, // ^ assertion
    eos, // $ assertion
};

const Rep = struct { child: *Node, min: u32, max: ?u32 }; // max null = unbounded

const Atomish = union(enum) { cp: u21, set: []const Range32 };

const Parser = struct {
    a: std.mem.Allocator,
    src: []const u8,
    pos: usize = 0,
    lim: *const Limits,
    group_depth: u32 = 0,

    fn node(self: *Parser, v: Node) Error!*Node {
        const n = try self.a.create(Node);
        n.* = v;
        return n;
    }

    fn peekByte(self: *const Parser) ?u8 {
        if (self.pos >= self.src.len) return null;
        return self.src[self.pos];
    }

    fn nextCp(self: *Parser) Error!u21 {
        const d = try decodeCpAt(self.src, self.pos);
        self.pos += d.len;
        return d.cp;
    }

    fn parseRoot(self: *Parser) Error!*Node {
        const n = try self.parseAlt();
        if (self.pos != self.src.len) return error.Invalid; // unmatched ')'
        return n;
    }

    fn parseAlt(self: *Parser) Error!*Node {
        var branches: std.ArrayListUnmanaged(*Node) = .{};
        while (true) {
            try branches.append(self.a, try self.parseConcat());
            if (self.peekByte() == '|') {
                self.pos += 1;
                continue;
            }
            break;
        }
        if (branches.items.len == 1) return branches.items[0];
        return self.node(.{ .alt = try branches.toOwnedSlice(self.a) });
    }

    fn parseConcat(self: *Parser) Error!*Node {
        var items: std.ArrayListUnmanaged(*Node) = .{};
        while (self.peekByte()) |c| {
            if (c == '|' or c == ')') break;
            try items.append(self.a, try self.parseRepeat());
        }
        return switch (items.items.len) {
            0 => self.node(.empty),
            1 => items.items[0],
            else => self.node(.{ .concat = try items.toOwnedSlice(self.a) }),
        };
    }

    fn parseRepeat(self: *Parser) Error!*Node {
        const atom = try self.parseAtom();
        var min: u32 = 1;
        var max: ?u32 = 1;
        var quantified = true;
        switch (self.peekByte() orelse 0) {
            '*' => {
                self.pos += 1;
                min = 0;
                max = null;
            },
            '+' => {
                self.pos += 1;
                min = 1;
                max = null;
            },
            '?' => {
                self.pos += 1;
                min = 0;
                max = 1;
            },
            '{' => {
                if (try self.tryBracedQuantifier()) |q| {
                    min = q.min;
                    max = q.max;
                } else {
                    quantified = false; // Annex B: not a quantifier, a literal '{'
                }
            },
            else => quantified = false,
        }
        if (!quantified) return atom;
        // Lazy suffix `?`: same accepted language, greediness is a capture
        // concern this DFA does not have.
        if (self.peekByte() == '?') self.pos += 1;
        switch (self.peekByte() orelse 0) {
            // A second quantifier ('a**', 'a*+', 'a???') is a syntax error.
            // ('{' is not: after a quantified atom it is an Annex B literal.)
            '*', '+', '?' => return error.Invalid,
            else => {},
        }
        switch (atom.*) {
            // Quantified assertions: the repetition of a zero-width assertion
            // is the assertion itself (Annex B permits the form).
            .bos, .eos => return atom,
            else => {},
        }
        return self.node(.{ .rep = .{ .child = atom, .min = min, .max = max } });
    }

    const Quant = struct { min: u32, max: ?u32 };

    /// Parses `{n}` / `{n,}` / `{n,m}`; returns null (consuming nothing) when
    /// the braces do not form a valid quantifier - it is then a literal '{'.
    fn tryBracedQuantifier(self: *Parser) Error!?Quant {
        const save = self.pos;
        self.pos += 1; // '{'
        const n = self.takeDecimal() orelse {
            self.pos = save;
            return null;
        };
        var q: Quant = .{ .min = n, .max = n };
        if (self.peekByte() == ',') {
            self.pos += 1;
            q.max = self.takeDecimal() orelse null;
        }
        if (self.peekByte() != '}') {
            self.pos = save;
            return null;
        }
        self.pos += 1;
        if (q.max) |m| {
            if (m < q.min) return error.Invalid; // {2,1} is a syntax error
            if (m > self.lim.max_repeat) return error.Unsupported;
        }
        if (q.min > self.lim.max_repeat) return error.Unsupported;
        return q;
    }

    fn takeDecimal(self: *Parser) ?u32 {
        var v: u64 = 0;
        var n: usize = 0;
        while (self.peekByte()) |c| {
            if (c < '0' or c > '9') break;
            v = v * 10 + (c - '0');
            if (v > std.math.maxInt(u32)) return null; // absurd count: literal
            n += 1;
            self.pos += 1;
        }
        if (n == 0) return null;
        return @intCast(v);
    }

    fn parseAtom(self: *Parser) Error!*Node {
        switch (self.peekByte() orelse return error.Invalid) {
            '(' => return self.parseGroup(),
            '[' => return self.parseClassNode(),
            '.' => {
                self.pos += 1;
                return self.node(.{ .set = &set_any });
            },
            '^' => {
                self.pos += 1;
                return self.node(.bos);
            },
            '$' => {
                self.pos += 1;
                return self.node(.eos);
            },
            '\\' => {
                self.pos += 1;
                const e = try self.parseEscape(false);
                return switch (e) {
                    .cp => |cp| self.node(.{ .char = cp }),
                    .set => |s| self.node(.{ .set = s }),
                };
            },
            // Quantifier without an atom. '|' and ')' are handled by the
            // concatenation loop and never reach here; ']' and '}' are Annex B
            // literals and fall through to the default case.
            '*', '+', '?' => return error.Invalid,
            else => return self.node(.{ .char = try self.nextCp() }),
        }
    }

    fn parseGroup(self: *Parser) Error!*Node {
        self.pos += 1; // '('
        if (self.peekByte() == '?') {
            self.pos += 1;
            switch (self.peekByte() orelse return error.Invalid) {
                ':' => self.pos += 1,
                '=', '!' => return error.Unsupported, // lookahead
                '<' => {
                    self.pos += 1;
                    switch (self.peekByte() orelse return error.Invalid) {
                        '=', '!' => return error.Unsupported, // lookbehind
                        else => {},
                    }
                    // Named capture (?<name>...): capture semantics are
                    // irrelevant for acceptance; the name is skipped.
                    var n: usize = 0;
                    while (self.peekByte()) |ch| {
                        if (ch == '>') break;
                        self.pos += 1;
                        n += 1;
                    }
                    if (n == 0 or self.peekByte() != '>') return error.Invalid;
                    self.pos += 1;
                },
                // Inline flags (?i) and every other (?...) form.
                else => return error.Unsupported,
            }
        }
        if (self.group_depth >= self.lim.max_group_depth) return error.Unsupported;
        self.group_depth += 1;
        defer self.group_depth -= 1;
        const inner = try self.parseAlt();
        if (self.peekByte() != ')') return error.Invalid;
        self.pos += 1;
        return inner;
    }

    fn parseClassNode(self: *Parser) Error!*Node {
        self.pos += 1; // '['
        var negate = false;
        if (self.peekByte() == '^') {
            negate = true;
            self.pos += 1;
        }
        var list: std.ArrayListUnmanaged(Range32) = .{};
        while (true) {
            const c = self.peekByte() orelse return error.Invalid; // unterminated
            if (c == ']') {
                self.pos += 1;
                break;
            }
            const lo_atom = try self.parseClassAtom();
            // A range needs a single-codepoint endpoint on both sides; '-' is
            // a literal at the edges of the class (ECMA-262 class rules).
            if (lo_atom == .cp and self.peekByte() == '-' and
                self.pos + 1 < self.src.len and self.src[self.pos + 1] != ']')
            {
                self.pos += 1; // '-'
                const hi_atom = try self.parseClassAtom();
                if (hi_atom != .cp) return error.Invalid; // e.g. [a-\d]
                if (hi_atom.cp < lo_atom.cp) return error.Invalid; // [z-a]
                try list.append(self.a, .{ .lo = lo_atom.cp, .hi = hi_atom.cp });
            } else switch (lo_atom) {
                .cp => |cp| try list.append(self.a, .{ .lo = cp, .hi = cp }),
                .set => |s| try list.appendSlice(self.a, s),
            }
        }
        var rs = try normRanges(self.a, list.items);
        if (negate) rs = try complementRanges(self.a, rs);
        return self.node(.{ .set = rs });
    }

    fn parseClassAtom(self: *Parser) Error!Atomish {
        if (self.peekByte() == '\\') {
            self.pos += 1;
            return self.parseEscape(true);
        }
        return .{ .cp = try self.nextCp() };
    }

    fn parseEscape(self: *Parser, in_class: bool) Error!Atomish {
        const c = self.peekByte() orelse return error.Invalid; // trailing '\'
        switch (c) {
            'd' => {
                self.pos += 1;
                return .{ .set = &set_digit };
            },
            'D' => {
                self.pos += 1;
                return .{ .set = try complementRanges(self.a, &set_digit) };
            },
            'w' => {
                self.pos += 1;
                return .{ .set = &set_word };
            },
            'W' => {
                self.pos += 1;
                return .{ .set = try complementRanges(self.a, &set_word) };
            },
            's' => {
                self.pos += 1;
                return .{ .set = &set_space };
            },
            'S' => {
                self.pos += 1;
                return .{ .set = try complementRanges(self.a, &set_space) };
            },
            'n' => {
                self.pos += 1;
                return .{ .cp = 0x0A };
            },
            'r' => {
                self.pos += 1;
                return .{ .cp = 0x0D };
            },
            't' => {
                self.pos += 1;
                return .{ .cp = 0x09 };
            },
            'f' => {
                self.pos += 1;
                return .{ .cp = 0x0C };
            },
            'v' => {
                self.pos += 1;
                return .{ .cp = 0x0B };
            },
            '0' => {
                self.pos += 1;
                if (self.peekByte()) |n2| {
                    if (n2 >= '0' and n2 <= '9') return error.Unsupported; // legacy octal
                }
                return .{ .cp = 0 };
            },
            'x' => {
                self.pos += 1;
                return .{ .cp = @intCast(try self.takeHex(2)) };
            },
            'u' => {
                self.pos += 1;
                return .{ .cp = try self.parseUnicodeEscape() };
            },
            'c' => {
                self.pos += 1;
                const l = self.peekByte() orelse return error.Invalid;
                if (!std.ascii.isAlphabetic(l)) return error.Invalid;
                self.pos += 1;
                return .{ .cp = @intCast(l & 0x1F) };
            },
            'b' => {
                self.pos += 1;
                // In a class: backspace. Outside: a word boundary, which needs
                // adjacent-character context this model does not carry.
                if (in_class) return .{ .cp = 0x08 };
                return error.Unsupported;
            },
            'B' => {
                if (in_class) return error.Invalid;
                self.pos += 1;
                return error.Unsupported; // non-word-boundary
            },
            '1'...'9' => return error.Unsupported, // backreference
            'k' => return error.Unsupported, // named backreference
            'p', 'P' => return error.Unsupported, // Unicode property classes
            else => {
                // Strict subset: an unknown ASCII-letter escape is a syntax
                // error; punctuation and non-ASCII are identity escapes.
                if (std.ascii.isAlphanumeric(c)) return error.Invalid;
                return .{ .cp = try self.nextCp() };
            },
        }
    }

    fn takeHex(self: *Parser, n: usize) Error!u32 {
        if (self.pos + n > self.src.len) return error.Invalid;
        var v: u32 = 0;
        for (self.src[self.pos .. self.pos + n]) |c| {
            v = v * 16 + (hexVal(c) orelse return error.Invalid);
        }
        self.pos += n;
        return v;
    }

    fn parseUnicodeEscape(self: *Parser) Error!u21 {
        if (self.peekByte() == '{') {
            self.pos += 1;
            var v: u32 = 0;
            var n: usize = 0;
            while (self.peekByte()) |ch| {
                if (ch == '}') break;
                v = v * 16 + (hexVal(ch) orelse return error.Invalid);
                if (v > 0x10FFFF) return error.Invalid;
                n += 1;
                self.pos += 1;
            }
            if (n == 0 or self.peekByte() != '}') return error.Invalid;
            self.pos += 1;
            if (v >= 0xD800 and v <= 0xDFFF) return error.Invalid;
            return @intCast(v);
        }
        const v = try self.takeHex(4);
        if (v >= 0xD800 and v <= 0xDBFF) {
            // Combine a surrogate pair, as ECMA-262 does for consecutive
            // \u escapes; a lone surrogate is not a scalar value.
            if (self.peekByte() == '\\' and self.pos + 1 < self.src.len and
                self.src[self.pos + 1] == 'u')
            {
                self.pos += 2;
                const lo = try self.takeHex(4);
                if (lo >= 0xDC00 and lo <= 0xDFFF) {
                    return @intCast(0x10000 + ((v - 0xD800) << 10) + (lo - 0xDC00));
                }
            }
            return error.Invalid;
        }
        if (v >= 0xDC00 and v <= 0xDFFF) return error.Invalid;
        return @intCast(v);
    }
};

fn hexVal(c: u8) ?u32 {
    return switch (c) {
        '0'...'9' => c - '0',
        'a'...'f' => c - 'a' + 10,
        'A'...'F' => c - 'A' + 10,
        else => null,
    };
}

// --------------------------------------------------------------------- NFA

const Cond = enum { always, bos, eos };

const NState = struct {
    trans: std.ArrayListUnmanaged(NTrans) = .{},
    eps: std.ArrayListUnmanaged(NEps) = .{},
    accept: bool = false,
};
const NTrans = struct { lo: u32, hi: u32, to: u32 };
const NEps = struct { to: u32, cond: Cond };

/// Thompson construction by endpoint sharing: emit(node, from, to) connects
/// `from` to `to` so that exactly L(node) is traversed.
const NfaBuilder = struct {
    a: std.mem.Allocator,
    lim: *const Limits,
    states: std.ArrayListUnmanaged(NState) = .{},

    fn newState(self: *NfaBuilder) Error!u32 {
        if (self.states.items.len >= self.lim.max_nfa_states) return error.Unsupported;
        try self.states.append(self.a, .{});
        return @intCast(self.states.items.len - 1);
    }

    fn eps(self: *NfaBuilder, from: u32, to: u32, cond: Cond) Error!void {
        try self.states.items[from].eps.append(self.a, .{ .to = to, .cond = cond });
    }

    fn trans(self: *NfaBuilder, from: u32, lo: u32, hi: u32, to: u32) Error!void {
        try self.states.items[from].trans.append(self.a, .{ .lo = lo, .hi = hi, .to = to });
    }

    fn emit(self: *NfaBuilder, n: *const Node, from: u32, to: u32) Error!void {
        switch (n.*) {
            .empty => try self.eps(from, to, .always),
            .char => |cp| try self.trans(from, cp, cp, to),
            .set => |rs| for (rs) |r| {
                try self.trans(from, r.lo, r.hi, to);
            },
            .bos => try self.eps(from, to, .bos),
            .eos => try self.eps(from, to, .eos),
            .alt => |bs| for (bs) |b| {
                try self.emit(b, from, to);
            },
            .concat => |items| {
                var cur = from;
                for (items[0 .. items.len - 1]) |it| {
                    const nxt = try self.newState();
                    try self.emit(it, cur, nxt);
                    cur = nxt;
                }
                try self.emit(items[items.len - 1], cur, to);
            },
            .rep => |r| try self.emitRep(r, from, to),
        }
    }

    /// {n,m} by expansion: n mandatory copies chained, then m-n optional
    /// copies in sequence (a{1,3} = a a? a? as a language), or a star tail
    /// when unbounded. Bounded by Limits.max_repeat and max_nfa_states.
    fn emitRep(self: *NfaBuilder, r: Rep, from: u32, to: u32) Error!void {
        var cur = from;
        var i: u32 = 0;
        while (i < r.min) : (i += 1) {
            const nxt = try self.newState();
            try self.emit(r.child, cur, nxt);
            cur = nxt;
        }
        if (r.max) |mx| {
            var j = i;
            while (j < mx) : (j += 1) {
                const nxt = try self.newState();
                try self.eps(cur, nxt, .always); // skip this optional copy
                try self.emit(r.child, cur, nxt); // or take it
                cur = nxt;
            }
            try self.eps(cur, to, .always);
        } else {
            // Star tail: bypass, plus one self-looping copy of the child.
            try self.eps(cur, to, .always);
            const loop = try self.newState();
            try self.eps(cur, loop, .always);
            try self.emit(r.child, loop, loop);
            try self.eps(loop, to, .always);
        }
    }
};

// -------------------------------------------------------- determinization

const SubsetCtx = struct {
    pub fn hash(_: SubsetCtx, k: []const u32) u64 {
        return std.hash.Wyhash.hash(0, std.mem.sliceAsBytes(k));
    }
    pub fn eql(_: SubsetCtx, x: []const u32, y: []const u32) bool {
        return std.mem.eql(u32, x, y);
    }
};

/// Epsilon-closure walker with generation-stamped visited marks (no
/// per-closure allocation).
const Det = struct {
    a: std.mem.Allocator,
    nfa: []const NState,
    mark: []u32,
    stack: std.ArrayListUnmanaged(u32) = .{},
    gen: u32 = 0,

    /// With `out` set: appends the sorted-unique closure (bos/eos select
    /// which conditional edges fire) and returns whether an accept state is
    /// in it. With `out == null`: stops at the first accept state found.
    fn walk(self: *Det, seed: []const u32, bos: bool, eos: bool, out: ?*std.ArrayListUnmanaged(u32)) Error!bool {
        self.gen +%= 1;
        const g = self.gen;
        self.stack.clearRetainingCapacity();
        for (seed) |s| {
            if (self.mark[s] != g) {
                self.mark[s] = g;
                try self.stack.append(self.a, s);
            }
        }
        var hit_accept = false;
        while (self.stack.pop()) |s| {
            if (self.nfa[s].accept) {
                if (out == null) return true;
                hit_accept = true;
            }
            if (out) |o| try o.append(self.a, s);
            for (self.nfa[s].eps.items) |e| {
                const ok = switch (e.cond) {
                    .always => true,
                    .bos => bos,
                    .eos => eos,
                };
                if (ok and self.mark[e.to] != g) {
                    self.mark[e.to] = g;
                    try self.stack.append(self.a, e.to);
                }
            }
        }
        return hit_accept;
    }
};

/// Subset construction over codepoint ranges. `^`-gated edges fire only in
/// the initial closure; accept means the end-of-string closure (always +
/// `$` edges) reaches the NFA accept state. State 0 is the empty subset
/// (dead); exceeding max_dfa_states is error.Unsupported. No minimization:
/// the state cap already bounds the table, and masks do not need a minimal
/// automaton (documented option of the task, deliberately skipped).
fn determinize(
    a: std.mem.Allocator,
    nfa: []const NState,
    seed: []const u32,
    lim: *const Limits,
) Error![]const Dfa.State {
    const DTmp = struct {
        trans: std.ArrayListUnmanaged(Dfa.Trans) = .{},
        accept: bool = false,
    };
    var map: std.HashMapUnmanaged([]const u32, u32, SubsetCtx, 75) = .{};
    var subsets: std.ArrayListUnmanaged([]const u32) = .{};
    var tmp: std.ArrayListUnmanaged(DTmp) = .{};
    var worklist: std.ArrayListUnmanaged(u32) = .{};

    var det = Det{
        .a = a,
        .nfa = nfa,
        .mark = try a.alloc(u32, nfa.len),
    };
    @memset(det.mark, 0);

    { // state 0: dead (the empty subset)
        const key = try a.dupe(u32, &.{});
        try map.put(a, key, 0);
        try subsets.append(a, key);
        try tmp.append(a, .{});
    }
    { // state 1: start, with the bos-gated edges enabled
        var out: std.ArrayListUnmanaged(u32) = .{};
        _ = try det.walk(seed, true, false, &out);
        std.mem.sort(u32, out.items, {}, std.sort.asc(u32));
        try map.put(a, out.items, 1);
        try subsets.append(a, out.items);
        try tmp.append(a, .{});
        try worklist.append(a, 1);
    }

    while (worklist.pop()) |id| {
        const subset = subsets.items[id];
        // Accept iff the end-of-string closure reaches the NFA accept state.
        const accept = try det.walk(subset, false, true, null);
        var trans: std.ArrayListUnmanaged(Dfa.Trans) = .{};
        var bounds: std.ArrayListUnmanaged(u32) = .{};
        for (subset) |s| {
            for (nfa[s].trans.items) |t| {
                try bounds.append(a, t.lo);
                try bounds.append(a, t.hi + 1);
            }
        }
        std.mem.sort(u32, bounds.items, {}, std.sort.asc(u32));
        // Partition the codepoint space by transition boundaries; segments
        // between consecutive boundaries have a constant target subset.
        var bi: usize = 0;
        while (bi < bounds.items.len) : (bi += 1) {
            const lo = bounds.items[bi];
            const hi_bound: u32 = if (bi + 1 < bounds.items.len) bounds.items[bi + 1] else 0x110000;
            if (hi_bound == lo) continue; // duplicate boundary
            const hi = hi_bound - 1;
            var raw: std.ArrayListUnmanaged(u32) = .{};
            for (subset) |s| {
                for (nfa[s].trans.items) |t| {
                    if (t.lo <= lo and lo <= t.hi) try raw.append(a, t.to);
                }
            }
            if (raw.items.len == 0) continue; // gap: feeding leads to dead
            var out: std.ArrayListUnmanaged(u32) = .{};
            _ = try det.walk(raw.items, false, false, &out);
            std.mem.sort(u32, out.items, {}, std.sort.asc(u32));
            const gop = try map.getOrPut(a, out.items);
            var target: u32 = undefined;
            if (gop.found_existing) {
                target = gop.value_ptr.*;
            } else {
                if (subsets.items.len >= lim.max_dfa_states) return error.Unsupported;
                target = @intCast(subsets.items.len);
                gop.value_ptr.* = target;
                try subsets.append(a, out.items);
                try tmp.append(a, .{});
                try worklist.append(a, target);
            }
            if (trans.items.len > 0) {
                const last = &trans.items[trans.items.len - 1];
                if (last.to == target and last.hi + 1 == lo) {
                    last.hi = hi; // merge adjacent segments with equal target
                    continue;
                }
            }
            try trans.append(a, .{ .lo = lo, .hi = hi, .to = target });
        }
        tmp.items[id] = .{ .trans = trans, .accept = accept };
    }

    const out = try a.alloc(Dfa.State, tmp.items.len);
    for (tmp.items, 0..) |*d, i| {
        out[i] = .{ .trans = try d.trans.toOwnedSlice(a), .accept = d.accept };
    }
    return out;
}

// ------------------------------------------------------------------- tests

fn expectTable(mode: Mode, pat: []const u8, yes: []const []const u8, no: []const []const u8) !void {
    var d = try compile(std.testing.allocator, pat, .{ .mode = mode });
    defer d.deinit();
    for (yes) |s| {
        if (!try d.matchesUtf8(s)) {
            std.debug.print("pattern {s}: expected match on \"{s}\"\n", .{ pat, s });
            return error.TestUnexpectedResult;
        }
    }
    for (no) |s| {
        if (try d.matchesUtf8(s)) {
            std.debug.print("pattern {s}: expected no match on \"{s}\"\n", .{ pat, s });
            return error.TestUnexpectedResult;
        }
    }
}

test "literal concatenation and dot" {
    try expectTable(.full, "abc", &.{"abc"}, &.{ "ab", "abcd", "xbc", "" });
    try expectTable(.full, "a.c", &.{ "abc", "axc", "a\xc3\xa9c" }, &.{ "ac", "a\nc", "a\rc", "a\xe2\x80\xa8c", "a\xe2\x80\xa9c" });
}

test "predefined classes: ASCII digit/word, ECMA space with Unicode spaces" {
    try expectTable(.full, "\\d+", &.{ "0", "42" }, &.{ "", "a", "\xd9\xa4\xd9\xa2" }); // Arabic-Indic digits are not \d
    try expectTable(.full, "\\w+", &.{"_aZ9"}, &.{ "", "-", "\xc3\xa9" });
    try expectTable(.full, "\\s", &.{ " ", "\t", "\n", "\xc2\xa0", "\xe1\x9a\x80", "\xe2\x80\x83", "\xe2\x80\xa8", "\xe2\x80\xaf", "\xe3\x80\x80", "\xef\xbb\xbf" }, &.{ "a", "_" });
    try expectTable(.full, "\\D", &.{"a"}, &.{"5"});
    try expectTable(.full, "\\W", &.{"-"}, &.{"a"});
    try expectTable(.full, "\\S", &.{"a"}, &.{ " ", "\xc2\xa0" });
}

test "character classes: ranges, negation, edge literals, nested escapes" {
    try expectTable(.full, "[a-c]+", &.{ "a", "abcba" }, &.{ "", "d", "abcd" });
    try expectTable(.full, "[^a-c]+", &.{ "xyz", "\xc3\xa9" }, &.{ "", "abc" });
    try expectTable(.full, "[-a]+", &.{"-a-a"}, &.{"b"});
    try expectTable(.full, "[a-]", &.{ "-", "a" }, &.{"b"});
    try expectTable(.full, "[]a]", &.{}, &.{ "", "a", "]", "a]" }); // empty class: the whole path is dead
    try expectTable(.full, "[]", &.{}, &.{ "", "a", "]" }); // empty class matches nothing
    try expectTable(.full, "[^]", &.{ "a", "\n", "]" }, &.{""}); // complement of nothing: any scalar
    try expectTable(.full, "[\\d.]+", &.{ "1.5", "00" }, &.{ "", "1a" });
    try expectTable(.full, "[^\\d]+", &.{"abc"}, &.{"a1"});
    try expectTable(.full, "[\\b]", &.{"\x08"}, &.{"b"}); // \b is backspace in a class
    try expectTable(.full, "[a-c-e]", &.{ "a", "b", "c", "-", "e" }, &.{"d"});
    try expectTable(.full, "[\\x41-\\x43]", &.{ "A", "B" }, &.{"D"});
    // Unicode range: Latin Extended U+0100..U+017F.
    try expectTable(.full, "[\xc4\x80-\xc5\xbf]+", &.{"\xc4\x80\xc5\xbf"}, &.{ "", "a" });
    try expectTable(.full, "[\\w-]+", &.{"a_-9"}, &.{"."});
}

test "quantifiers: exact, bounded, unbounded, lazy" {
    try expectTable(.full, "a{2,3}", &.{ "aa", "aaa" }, &.{ "", "a", "aaaa" });
    try expectTable(.full, "a{2}", &.{"aa"}, &.{ "a", "aaa" });
    try expectTable(.full, "a{2,}", &.{ "aa", "aaaaa" }, &.{ "", "a" });
    try expectTable(.full, "a{0}", &.{""}, &.{"a"});
    try expectTable(.full, "a{0,2}", &.{ "", "a", "aa" }, &.{"aaa"});
    try expectTable(.full, "(ab){2,3}", &.{ "abab", "ababab" }, &.{ "ab", "abababab", "aba" });
    try expectTable(.full, "ab?c", &.{ "ac", "abc" }, &.{ "abbc", "c" });
    try expectTable(.full, "a+?b", &.{ "ab", "aab" }, &.{"b"}); // lazy suffix accepted
    try expectTable(.full, "a{2}?b", &.{"aab"}, &.{"ab"});
}

test "groups, alternation, nesting, named groups, empty branches" {
    try expectTable(.full, "(ab|cd)e", &.{ "abe", "cde" }, &.{ "ace", "ab", "abee" });
    try expectTable(.full, "((a|b)c)+d", &.{ "acd", "bcd", "acbcbcd" }, &.{ "d", "a", "cdc", "", "acbccd" });
    try expectTable(.full, "(?:ab)+c", &.{ "abc", "ababc" }, &.{ "c", "ab" });
    try expectTable(.full, "(?<name>ab)c", &.{"abc"}, &.{"abd"});
    try expectTable(.full, "(a|)b", &.{ "ab", "b" }, &.{"a"});
    try expectTable(.full, "a|", &.{ "a", "" }, &.{"b"});
    try expectTable(.full, "^(a|b)$", &.{ "a", "b" }, &.{ "", "ab", "c" });
}

test "escapes: unicode, surrogate pairs, control, identity" {
    try expectTable(.full, "\\u{1F600}", &.{"\xf0\x9f\x98\x80"}, &.{"a"});
    try expectTable(.full, "\\uD83D\\uDE00", &.{"\xf0\x9f\x98\x80"}, &.{"\xc3\xa9"});
    try expectTable(.full, "\\x41\\x42", &.{"AB"}, &.{"ab"});
    try expectTable(.full, "\\n\\r\\t\\f\\v", &.{"\n\r\t\x0c\x0b"}, &.{"nrtfv"});
    try expectTable(.full, "\\.\\*\\+\\?\\(\\)\\[\\]\\{\\}\\|\\^\\$\\\\\\/", &.{".*+?()[]{}|^$\\/"}, &.{"a"});
    try expectTable(.full, "\\0", &.{"\x00"}, &.{"0"});
    try expectTable(.full, "\\cA", &.{"\x01"}, &.{"A"});
    try expectTable(.full, "h\xc3\xa9llo", &.{"h\xc3\xa9llo"}, &.{"hello"});
    try expectTable(.full, "\\u00e9", &.{"\xc3\xa9"}, &.{"e"});
}

test "braced literal forms (Annex B)" {
    try expectTable(.full, "a{", &.{"a{"}, &.{"a"});
    try expectTable(.full, "a{,5}", &.{"a{,5}"}, &.{"aaaaa"});
    try expectTable(.full, "a{2}{3}", &.{"aa{3}"}, &.{ "aa", "aaa" });
    try expectTable(.full, "a{b}", &.{"a{b}"}, &.{"ab"});
}

test "search mode is an unanchored partial match" {
    try expectTable(.search, "bc", &.{ "bc", "abc", "bcd", "abcde" }, &.{ "", "b", "ac" });
    try expectTable(.search, "b", &.{ "b", "ab", "ba", "aabaa" }, &.{ "", "aa" });
    try expectTable(.search, "ab", &.{"zabz"}, &.{"zbz"});
    try expectTable(.search, "a.c", &.{"xxaxcyy"}, &.{ "xac", "a\nc" });
    try expectTable(.search, "", &.{ "", "abc" }, &.{}); // empty pattern matches everywhere
}

test "anchors bind to string edges in search mode" {
    try expectTable(.search, "^ab", &.{ "ab", "abc" }, &.{ "", "xab", "ba", "aab" });
    try expectTable(.search, "b$", &.{ "b", "ab", "aab", "bab" }, &.{ "", "ba", "bax" });
    try expectTable(.search, "^$", &.{""}, &.{"a"});
    try expectTable(.search, "^a|b$", &.{ "ax", "xb", "ab" }, &.{ "xay", "x" });
    try expectTable(.search, "^", &.{ "", "abc" }, &.{});
    try expectTable(.search, "$", &.{ "", "abc" }, &.{});
    try expectTable(.search, "^abc$", &.{"abc"}, &.{ "xabc", "abcx", "ab" });
}

test "empty pattern in full mode matches only the empty string" {
    try expectTable(.full, "", &.{""}, &.{"a"});
}

test "shortestAccept: BFS length and witness" {
    const a = std.testing.allocator;
    const Case = struct { pat: []const u8, want: ?u32 };
    const cases = [_]Case{
        .{ .pat = "", .want = 0 },
        .{ .pat = "^", .want = 0 },
        .{ .pat = "a", .want = 1 },
        .{ .pat = "^a+$", .want = 1 },
        .{ .pat = "a.c", .want = 3 },
        .{ .pat = "^[x-z]{4}$", .want = 4 },
        .{ .pat = "a^b", .want = null }, // unreachable anchor: matches nothing
        .{ .pat = "[^]", .want = 1 },
    };
    for (cases) |c| {
        var d = try compileJsonSchema(a, c.pat);
        defer d.deinit();
        var path: std.ArrayListUnmanaged(u21) = .{};
        defer path.deinit(a);
        const got = try d.shortestAccept(a, &path);
        try std.testing.expectEqual(c.want, got);
        if (got) |l| {
            try std.testing.expectEqual(l, @as(u32, @intCast(path.items.len)));
            // The witness is genuinely accepted.
            var s = d.start();
            for (path.items) |cp| s = d.feed(s, cp);
            try std.testing.expect(d.isAccept(s));
        }
    }
    // Search-mode accepted lengths are [d_min, infinity): padding a witness
    // with any scalar value keeps it accepted (the sticky accept loop).
    var d = try compileJsonSchema(a, "^ab");
    defer d.deinit();
    var s = d.start();
    for ([_]u21{ 'a', 'b', 'z', 0x20, 0x10FFFF }) |cp| s = d.feed(s, cp);
    try std.testing.expect(d.isAccept(s));
}

test "invalid patterns are rejected with error.Invalid" {
    const cases = [_][]const u8{
        "[a",      "(a",          "a)",       "a\\",  "[z-a]",
        "a**",     "a*+",         "a???",     "\\q",  "a{2,1}",
        "\\u{}",   "\\u{110000}", "\\uD800x", "\\x4", "(?<>a)",
        "[a-\\d]", "\\uDC00",     "*a",
    };
    for (cases) |pat| {
        const res = compile(std.testing.allocator, pat, .{});
        if (res) |*d| {
            var dd = d.*;
            dd.deinit();
            std.debug.print("pattern {s}: expected error.Invalid\n", .{pat});
            return error.TestUnexpectedResult;
        } else |e| switch (e) {
            error.Invalid => {},
            else => {
                std.debug.print("pattern {s}: expected error.Invalid, got {s}\n", .{ pat, @errorName(e) });
                return error.TestUnexpectedResult;
            },
        }
    }
}

test "unsupported constructs are refused with error.Unsupported" {
    const cases = [_][]const u8{
        "(a)\\1",  "\\1",       "a(?=b)", "a(?!b)",  "(?<=a)b",
        "(?<!a)b", "\\p{L}",    "\\P{L}", "\\bword", "word\\B",
        "(?i)a",   "\\k<name>", "\\01",   "[\\1]",
    };
    for (cases) |pat| {
        const res = compile(std.testing.allocator, pat, .{});
        if (res) |*d| {
            var dd = d.*;
            dd.deinit();
            std.debug.print("pattern {s}: expected error.Unsupported\n", .{pat});
            return error.TestUnexpectedResult;
        } else |e| switch (e) {
            error.Unsupported => {},
            else => {
                std.debug.print("pattern {s}: expected error.Unsupported, got {s}\n", .{ pat, @errorName(e) });
                return error.TestUnexpectedResult;
            },
        }
    }
}

test "limits: repeat count, pattern length, DFA state budget" {
    try std.testing.expectError(error.Unsupported, compile(std.testing.allocator, "a{2000}", .{}));
    try std.testing.expectError(error.Unsupported, compile(std.testing.allocator, "abcde", .{ .limits = .{ .max_pattern_bytes = 4 } }));
    try std.testing.expectError(error.Unsupported, compile(std.testing.allocator, "(a|b)(c|d)(e|f)(g|h)", .{ .limits = .{ .max_dfa_states = 4 } }));
    // A typical schema pattern stays far below the budget.
    var d = try compile(std.testing.allocator, "^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$", .{});
    defer d.deinit();
    try std.testing.expect(d.stateCount() <= 4096);
    try std.testing.expect(try d.matchesUtf8("user@example.com"));
    try std.testing.expect(!try d.matchesUtf8("user@example.c"));
    try std.testing.expect(!try d.matchesUtf8("x user@example.com")); // full... no: search mode? compiled default search but anchored ^
}

test "DFA stepping: ranges iteration, feed, accept, dead" {
    var d = try compile(std.testing.allocator, "[a-cx-z]", .{ .mode = .full });
    defer d.deinit();
    const s0 = d.start();
    var it = d.ranges(s0);
    try std.testing.expectEqual(Range{ .lo = 'a', .hi = 'c' }, it.next().?);
    try std.testing.expectEqual(Range{ .lo = 'x', .hi = 'z' }, it.next().?);
    try std.testing.expect(it.next() == null);
    try std.testing.expect(d.dead(d.feed(s0, 'q')));
    const s1 = d.feed(s0, 'a');
    try std.testing.expect(d.isAccept(s1));
    try std.testing.expect(!d.dead(s1));
    try std.testing.expect(d.dead(d.feed(s1, 'a')));
    var it_dead = d.ranges(d.feed(s1, 'a'));
    try std.testing.expect(it_dead.next() == null);
}

test "run state serialization roundtrip" {
    var d = try compile(std.testing.allocator, "ab+c", .{ .mode = .full });
    defer d.deinit();
    var s = d.feed(d.start(), 'a');
    var buf: [run_state_bytes]u8 = undefined;
    writeRunState(s, &buf);
    s = readRunState(&buf);
    try std.testing.expect(!d.isAccept(s));
    s = d.feed(s, 'b');
    s = d.feed(s, 'c');
    try std.testing.expect(d.isAccept(s));
}

test "compileJsonSchema defaults to search mode" {
    var d = try compileJsonSchema(std.testing.allocator, "ell");
    defer d.deinit();
    try std.testing.expect(try d.matchesUtf8("hello"));
    try std.testing.expect(!try d.matchesUtf8("helo"));
}

test "compile frees everything under injected allocation failure" {
    var k: usize = 0;
    var done = false;
    while (k < 512 and !done) : (k += 1) {
        var fa = std.testing.FailingAllocator.init(std.testing.allocator, .{ .fail_index = k });
        const res = compile(fa.allocator(), "^(a|b[c-z]){2,3}d$", .{ .mode = .full });
        if (res) |d| {
            var dd = d;
            dd.deinit();
            done = true;
        } else |e| switch (e) {
            error.OutOfMemory => {},
            else => return e,
        }
        try std.testing.expectEqual(fa.allocated_bytes, fa.freed_bytes);
    }
    try std.testing.expect(done);
}

test "matching runs leak-free on the testing allocator" {
    var d = try compile(std.testing.allocator, "^(a|b[c-z]){2,3}d$", .{ .mode = .full });
    defer d.deinit();
    try std.testing.expect(try d.matchesUtf8("aad")); // reps: a, a
    try std.testing.expect(try d.matchesUtf8("abcd")); // reps: a, bc
    try std.testing.expect(try d.matchesUtf8("abcbdd")); // reps: a, bc, bd
    try std.testing.expect(!try d.matchesUtf8("ad")); // one repetition is too few
    try std.testing.expect(!try d.matchesUtf8("acd")); // 'c' starts no repetition
    try std.testing.expect(!try d.matchesUtf8("aadd")); // trailing input after the match
    const cps = [_]u21{ 'b', 'q', 'a', 'd' }; // two repetitions: "bq" then "a"
    try std.testing.expect(d.matches(&cps));
    try std.testing.expect(!d.matches(&[_]u21{ 'b', 'q', 'd' })); // one repetition is too few
}
