//! Joint completion reachability of a conjunction of numeric value
//! machines that read one number lexeme (ADR-0011).
//!
//! The parser runs every conjunct of an allOf comb (int_num, num_range,
//! num_mult, num_excl, num_const, num_v) as its own frame over the same
//! bytes. Each frame keeps a compressed summary of the lexeme (comparison
//! state against its bounds, mantissa residue, trailing zeros, exponent),
//! and the per-frame certificates of src/complete.zig decide each conjunct
//! alone. A conjunction can be dead while every conjunct is alive (integer
//! <= 9 after `51`), and the budgeted token search cannot prove that: the
//! state has infinitely many continuations. termVerdict combines the
//! summaries of all conjuncts and decides the conjunction in closed form.
//!
//! The decision follows the case split of ADR-0011: zero; a value with no
//! significant digit yet (any magnitude of the sign); an extensible
//! mantissa (every order of magnitude t is reachable, the reachable values
//! at t form the interval J_t = [P x 10^k, (P+1) x 10^k), k = t - n + 1);
//! a frozen mantissa (one value per reachable exponent). Bounded
//! magnitudes reduce to finitely many orders of magnitude; unbounded ones
//! have a constructive witness. Exact decimal arithmetic only.

const std = @import("std");
const Arena = @import("alloc.zig").Arena;
const grammar = @import("grammar.zig");
const parser = @import("parser.zig");
const BigInt = std.math.big.int.Managed;

pub const Verdict = enum { alive, dead, undecided };

/// Placement of the leading significant digit after the appended mantissa
/// digits: an exact order of magnitude, or an exponent far beyond every
/// constant of the schema (`big`) or far below it (`small`).
pub const Place = union(enum) { om: i64, big, small };

/// Constructive continuation behind an `alive` verdict (test builds render
/// it against the fed prefix, src/test_hooks.zig).
pub const Witness = union(enum) {
    none,
    /// Unconstrained term: every syntactic completion satisfies it.
    any,
    zero,
    /// Spell exactly this value (canonical digits, value = digits x 10^exp10).
    value: struct { neg: bool, digits: []const u8, exp10: i64 },
    /// Append `x` zero-padded to `w` mantissa digits, then place the value.
    ext: struct { w: u32, x: u128, place: Place },
    /// Frozen mantissa: complete the exponent to exactly `e`.
    frozen: struct { e: i64 },
    /// Frozen mantissa with a saturated exponent: the typed spelling
    /// already completes.
    as_is,
};

const MAX_LEAVES = 32;
const MAX_EXCL = 32;
/// Scaled integers above this many decimal digits are not built; the
/// verdict is then undecided (the schema constants have at most 400
/// digits and |exponent| <= 400, so this needs an adversarial lexeme).
const MAX_BIG_DIGITS: i64 = 20000;
/// Exponent values at or above this are treated as saturated: the frames
/// agree exactly below it (num_mult caps at 2^31).
const EXP_SAT: u64 = 1 << 31;

const Bound = struct {
    c: grammar.NumConst,
    excl: bool,
    fr: *const parser.NumRangeFrame,
    slot: u1,

    fn om(b: Bound) i64 {
        return constOm(b.c);
    }
    fn cmpSt(b: Bound) u8 {
        return (b.fr.cmp >> (2 * @as(u3, b.slot))) & 3;
    }
    fn matched(b: Bound) u32 {
        return b.fr.j[b.slot];
    }
    fn dlen(b: Bound) u32 {
        return @intCast(b.c.digits.len);
    }
};

const Div = struct {
    a: u32,
    b: i64,
    co: u32,
    /// P mod a (P: the typed significant digits, trailing zeros included).
    rp: u32,
    /// P' mod a (P': P without trailing zeros).
    rpd: u32,
};

const Excl = struct {
    c: grammar.NumConst,
    m: u2,
    tail_z: u32,
};

const Info = struct {
    st: parser.NumState,
    neg: ?bool = null,
    lead: ?bool = null,
    om_m: ?i64 = null,
    frac_len: ?u32 = null,
    tz: ?u32 = null,
    exp: u64 = 0,
    exp_neg: bool = false,
    exp_sat: bool = false,
    ranges: [MAX_LEAVES]*const parser.NumRangeFrame = undefined,
    /// Per range: the range itself, or one bound inverted (Code).
    inv: [MAX_LEAVES]Code = undefined,
    n_ranges: usize = 0,
    mult: ?*const parser.NumMultFrame = null,
    n_int: usize = 0,
    /// The value must not be an integer (a negated integer leaf).
    nonint: bool = false,
    excl: [MAX_EXCL]Excl = undefined,
    n_excl: usize = 0,
    consts: [MAX_LEAVES]*const parser.NumConstFrame = undefined,
    n_consts: usize = 0,
    constrained: bool = false,

    fn sigDigits(self: *const Info) ?i64 {
        const om_m = self.om_m orelse return null;
        const fl = self.frac_len orelse return null;
        return om_m + @as(i64, fl) + 1;
    }

    fn excls(self: *const Info) []const Excl {
        return self.excl[0..self.n_excl];
    }
};

fn constOm(c: grammar.NumConst) i64 {
    return c.exp10 + @as(i64, @intCast(c.digits.len)) - 1;
}

fn absConst(c: grammar.NumConst) grammar.NumConst {
    return .{ .neg = false, .digits = c.digits, .exp10 = c.exp10 };
}

fn isFrozen(st: parser.NumState) bool {
    return st == .exp or st == .exp_sign or st == .exp_digits;
}

fn noteExp(info: *Info, e: u64, neg: bool) void {
    info.exp = @max(info.exp, e);
    info.exp_neg = neg;
    if (e >= EXP_SAT) info.exp_sat = true;
}

fn gather(g: *const grammar.Grammar, lits: []const Lit, info: *Info) bool {
    var have_st = false;
    for (lits) |l| {
        const f = l.f;
        const st: parser.NumState = switch (f.*) {
            .num_v => |x| x.st,
            inline .int_num, .not_int_num => |*x, tag| blk: {
                if ((tag == .int_num) == (l.code == .pos)) info.n_int += 1 else info.nonint = true;
                info.constrained = true;
                info.lead = x.nz;
                info.frac_len = x.frac_len;
                info.tz = x.tz;
                noteExp(info, x.exp, x.exp_neg);
                break :blk x.st;
            },
            .num_mult => |*x| blk: {
                if (info.mult) |m| {
                    const a = &g.node(m.node).num_mult;
                    const b = &g.node(x.node).num_mult;
                    if (a.div != b.div or a.div_exp10 != b.div_exp10) return false;
                }
                info.mult = x;
                info.constrained = true;
                info.lead = x.flags & parser.MULT_NZ != 0;
                info.frac_len = x.frac_len;
                info.tz = x.tz;
                noteExp(info, x.exp, x.flags & parser.MULT_EXP_NEG != 0);
                if (x.flags & parser.MULT_EXP_SAT != 0) info.exp_sat = true;
                break :blk x.st;
            },
            .num_range => |*x| blk: {
                if (info.n_ranges >= MAX_LEAVES) return false;
                info.ranges[info.n_ranges] = x;
                info.inv[info.n_ranges] = l.code;
                info.n_ranges += 1;
                info.constrained = true;
                info.neg = x.flags & parser.RANGE_NEG != 0;
                const lead = x.flags & parser.RANGE_LEAD != 0;
                info.lead = lead;
                if (lead) {
                    info.om_m = if (x.flags & parser.RANGE_OM_INT != 0)
                        @as(i64, x.int_len) - 1
                    else
                        -(@as(i64, x.fz) + 1);
                }
                noteExp(info, x.exp, x.flags & parser.RANGE_EXP_NEG != 0);
                break :blk x.st;
            },
            .num_excl => |*x| blk: {
                const ne = &g.node(x.node).num_excl;
                info.constrained = true;
                info.neg = x.neg;
                info.frac_len = x.frac_len;
                noteExp(info, x.exp, x.exp_neg);
                for (ne.consts, 0..) |c, i| {
                    if (info.n_excl >= MAX_EXCL) return false;
                    const m: u2 = @intCast((x.m >> @intCast(2 * i)) & 3);
                    info.excl[info.n_excl] = .{ .c = c, .m = m, .tail_z = x.tail_z[i] };
                    info.n_excl += 1;
                    if (info.lead == null) {
                        // State 0 = only zeros so far; 1/2/3 need a nonzero digit.
                        info.lead = m != 0;
                    }
                }
                break :blk x.st;
            },
            .num_const => |*x| blk: {
                if (info.n_consts >= MAX_LEAVES) return false;
                info.consts[info.n_consts] = x;
                info.n_consts += 1;
                info.constrained = true;
                info.neg = x.neg;
                info.frac_len = x.frac_len;
                noteExp(info, x.exp, x.exp_neg);
                if (info.lead == null) info.lead = x.m != .leading;
                break :blk x.st;
            },
            else => return false,
        };
        if (have_st and st != info.st) return false;
        info.st = st;
        have_st = true;
    }
    return have_st;
}

/// Combined divisor of the integer and multipleOf conjuncts: the least
/// common multiple A x 10^B with the typed mantissa residues modulo A.
fn divisorOf(g: *const grammar.Grammar, info: *const Info) ?Div {
    if (info.mult) |mf| {
        const nm = &g.node(mf.node).num_mult;
        var a: u32 = nm.div;
        var b: i64 = nm.div_exp10;
        if (info.n_int > 0 and b < 0) {
            // lcm(1, div x 10^-s) = div / gcd(div, 10^s).
            var s: i64 = -b;
            while (s > 0 and a % 10 == 0) : (s -= 1) a /= 10;
            var s2 = s;
            while (s2 > 0 and a % 2 == 0) : (s2 -= 1) a /= 2;
            var s5 = s;
            while (s5 > 0 and a % 5 == 0) : (s5 -= 1) a /= 5;
            b = 0;
        }
        return .{ .a = a, .b = b, .co = coprimePart(a), .rp = mf.rem % a, .rpd = mf.rem_d % a };
    }
    if (info.n_int > 0) return .{ .a = 1, .b = 0, .co = 1, .rp = 0, .rpd = 0 };
    return null;
}

fn coprimePart(a: u32) u32 {
    var co = a;
    while (co % 2 == 0) co /= 2;
    while (co % 5 == 0) co /= 5;
    return co;
}

fn constIsMultiple(c: grammar.NumConst, d: Div) bool {
    return grammar.numConstIsMultiple(c, .{ .div = d.a, .div_exp10 = d.b, .co = d.co });
}

fn digitsOf(a: u32) i64 {
    var n: i64 = 1;
    var v = a;
    while (v >= 10) : (v /= 10) n += 1;
    return n;
}

fn powmod10(e: u64, m: u32) u64 {
    return grammar.powmodU32(10 % m, e, m);
}

const MagBounds = struct { forbidden: bool = false, lo: ?Bound = null, hi: ?Bound = null };

fn tighter(cur: ?Bound, cand: Bound, comptime lower: bool) Bound {
    const c = cur orelse return cand;
    const o = grammar.cmpNumConst(cand.c, c.c);
    if (o == .eq) return if (cand.excl) cand else c;
    if (lower) return if (o == .gt) cand else c;
    return if (o == .lt) cand else c;
}

/// Magnitude bounds y in (lo, hi) of a nonzero value of the given sign
/// (value = -y for a negative sign); `forbidden` when no nonzero value of
/// that sign satisfies every range.
fn magBounds(g: *const grammar.Grammar, info: *const Info, neg: bool) MagBounds {
    var mb: MagBounds = .{};
    for (info.ranges[0..info.n_ranges], info.inv[0..info.n_ranges]) |fr, code| {
        const nr = &g.node(fr.node).num_range;
        switch (code) {
            .pos => {},
            // value < min (<= min when min is exclusive), same constant and
            // comparison slot as the bound it inverts.
            .inv_lo => {
                const mn = nr.min.?;
                const excl = !nr.min_excl;
                if (neg) {
                    if (mn.neg) mb.lo = tighter(mb.lo, .{ .c = absConst(mn), .excl = excl, .fr = fr, .slot = 0 }, true);
                } else {
                    if (mn.neg or mn.digits.len == 0) return .{ .forbidden = true };
                    mb.hi = tighter(mb.hi, .{ .c = mn, .excl = excl, .fr = fr, .slot = 0 }, false);
                }
                continue;
            },
            // value > max (>= max when max is exclusive).
            .inv_hi => {
                const mx = nr.max.?;
                const excl = !nr.max_excl;
                if (neg) {
                    if (!mx.neg or mx.digits.len == 0) return .{ .forbidden = true };
                    mb.hi = tighter(mb.hi, .{ .c = absConst(mx), .excl = excl, .fr = fr, .slot = 1 }, false);
                } else {
                    if (!mx.neg and mx.digits.len != 0) mb.lo = tighter(mb.lo, .{ .c = mx, .excl = excl, .fr = fr, .slot = 1 }, true);
                }
                continue;
            },
            .neg => unreachable,
        }
        if (neg) {
            if (nr.min) |mn| {
                if (!mn.neg) return .{ .forbidden = true };
                mb.hi = tighter(mb.hi, .{ .c = absConst(mn), .excl = nr.min_excl, .fr = fr, .slot = 0 }, false);
            }
            if (nr.max) |mx| {
                if (mx.neg) mb.lo = tighter(mb.lo, .{ .c = absConst(mx), .excl = nr.max_excl, .fr = fr, .slot = 1 }, true);
            }
        } else {
            if (nr.max) |mx| {
                if (mx.neg or mx.digits.len == 0) return .{ .forbidden = true };
                mb.hi = tighter(mb.hi, .{ .c = mx, .excl = nr.max_excl, .fr = fr, .slot = 1 }, false);
            }
            if (nr.min) |mn| {
                if (!mn.neg and mn.digits.len != 0)
                    mb.lo = tighter(mb.lo, .{ .c = mn, .excl = nr.min_excl, .fr = fr, .slot = 0 }, true);
            }
        }
    }
    if (mb.lo != null and mb.hi != null) {
        const o = grammar.cmpNumConst(mb.lo.?.c, mb.hi.?.c);
        if (o == .gt or (o == .eq and (mb.lo.?.excl or mb.hi.?.excl))) return .{ .forbidden = true };
    }
    return mb;
}

/// c satisfies the range, or its inverted bound (Code).
fn inRangeLit(c: grammar.NumConst, nr: grammar.NumRange, code: Code) bool {
    return switch (code) {
        .pos => grammar.numConstInRange(c, nr),
        .inv_lo => switch (grammar.cmpNumConst(c, nr.min.?)) {
            .lt => true,
            .eq => nr.min_excl,
            .gt => false,
        },
        .inv_hi => switch (grammar.cmpNumConst(c, nr.max.?)) {
            .gt => true,
            .eq => nr.max_excl,
            .lt => false,
        },
        .neg => unreachable,
    };
}

fn zeroAllowed(g: *const grammar.Grammar, info: *const Info) bool {
    const zero: grammar.NumConst = .{ .neg = false, .digits = "", .exp10 = 0 };
    if (info.nonint) return false;
    for (info.ranges[0..info.n_ranges], info.inv[0..info.n_ranges]) |fr, code| {
        if (!inRangeLit(zero, g.node(fr.node).num_range, code)) return false;
    }
    for (info.excls()) |e| {
        if (e.c.digits.len == 0) return false;
    }
    return true;
}

pub const Ctx = struct {
    g: *const grammar.Grammar,
    /// Scratch for exact arithmetic and witness digits; null disables the
    /// paths that need it (they answer undecided).
    a: ?std.mem.Allocator,
    wit: ?*Witness,
    /// Significant-digit count of the typed prefix, supplied by test hooks
    /// for witness rendering when no conjunct tracks it; never used for a
    /// verdict.
    n_hint: ?i64 = null,

    fn setWit(self: Ctx, w: Witness) void {
        if (self.wit) |p| p.* = w;
    }
};

/// Joint verdict of one conjunction of numeric leaf frames that consumed
/// the same bytes. Leaves of other kinds make the verdict undecided.
pub fn termVerdict(ctx: Ctx, leaves: []const *const parser.Frame) Verdict {
    var lits: [MAX_LEAVES * 2]Lit = undefined;
    if (leaves.len > lits.len) return .undecided;
    for (leaves, 0..) |f, i| lits[i] = .{ .f = f, .code = .pos };
    return litsVerdict(ctx, lits[0..leaves.len]);
}

/// A leaf of a conjunction, possibly negated (oneOf and if/then/else
/// groups in disjunctive normal form). inv_lo / inv_hi stand for a range
/// with one bound inverted.
pub const Code = enum { pos, neg, inv_lo, inv_hi };
pub const Lit = struct { f: *const parser.Frame, code: Code };

/// Joint verdict of a conjunction of literals. A negated number machine is
/// unsatisfiable; a negated range is the union of its inverted bounds; a
/// negated integer machine asks for a non-integer value. Negated multipleOf,
/// constant and excluded-constant leaves are not modeled: dropping them
/// only widens the term, so the verdict stands when it is dead and is
/// undecided otherwise.
pub fn termVerdictLits(ctx: Ctx, lits: []const Lit) Verdict {
    var buf: [MAX_LEAVES * 2]Lit = undefined;
    if (lits.len > buf.len) return .undecided;
    var n: usize = 0;
    var hard = false;
    for (lits, 0..) |l, i| {
        if (l.code != .neg) {
            buf[n] = l;
            n += 1;
            continue;
        }
        switch (l.f.*) {
            .num_v => return .dead,
            .int_num, .not_int_num => {
                buf[n] = l;
                n += 1;
            },
            .num_range => |x| {
                const nr = ctx.g.node(x.node).num_range;
                if (nr.min == null and nr.max == null) return .dead;
                if (nr.min != null and nr.max != null) {
                    var alt: [MAX_LEAVES * 2]Lit = undefined;
                    @memcpy(alt[0..lits.len], lits);
                    alt[i].code = .inv_lo;
                    const va = termVerdictLits(ctx, alt[0..lits.len]);
                    if (va == .alive) return .alive;
                    alt[i].code = .inv_hi;
                    const vb = termVerdictLits(ctx, alt[0..lits.len]);
                    if (vb == .alive) return .alive;
                    return if (va == .dead and vb == .dead) .dead else .undecided;
                }
                buf[n] = .{ .f = l.f, .code = if (nr.min != null) .inv_lo else .inv_hi };
                n += 1;
            },
            else => hard = true,
        }
    }
    const v = litsVerdict(ctx, buf[0..n]);
    return if (hard and v == .alive) .undecided else v;
}

fn litsVerdict(ctx: Ctx, lits: []const Lit) Verdict {
    var info: Info = .{ .st = .start };
    if (!gather(ctx.g, lits, &info)) return .undecided;
    if (info.nonint) {
        if (info.n_int > 0) return .dead;
        // Non-integers among multiples of a divisor, and witnesses for
        // them, are not modeled: only a dead verdict of the term without
        // the condition is kept.
        if (info.mult != null or ctx.wit != null) {
            var plain = info;
            plain.nonint = false;
            return if (infoVerdict(ctx, &plain) == .dead) .dead else .undecided;
        }
    }
    return infoVerdict(ctx, &info);
}

fn infoVerdict(ctx: Ctx, info: *const Info) Verdict {
    if (!info.constrained) {
        ctx.setWit(.any);
        return .alive;
    }
    if (info.n_consts > 0) return constCase(ctx, info);
    const lead = info.lead orelse return .undecided;
    const div = divisorOf(ctx.g, info);
    if (!lead and zeroAllowed(ctx.g, info)) {
        ctx.setWit(.zero);
        return .alive;
    }
    if (isFrozen(info.st)) {
        if (!lead) return .dead;
        return frozenCase(ctx, info, div, info.neg orelse false);
    }
    if (!lead) {
        if (info.st == .start) {
            const p = staticCase(ctx, info, div, false);
            if (p != .dead) return p;
            return staticCase(ctx, info, div, true);
        }
        return staticCase(ctx, info, div, info.neg orelse false);
    }
    return extCase(ctx, info, div, info.neg orelse false);
}

fn constCase(ctx: Ctx, info: *const Info) Verdict {
    const g = ctx.g;
    const c0 = g.node(info.consts[0].node).num_const;
    for (info.consts[0..info.n_consts]) |cf| {
        const c = g.node(cf.node).num_const;
        if (grammar.cmpNumConst(c, c0) != .eq) return .dead;
        switch (constReachable(&c, cf)) {
            .alive => {},
            .dead => return .dead,
            .undecided => return .undecided,
        }
    }
    for (info.ranges[0..info.n_ranges], info.inv[0..info.n_ranges]) |fr, code| {
        if (!inRangeLit(c0, g.node(fr.node).num_range, code)) return .dead;
    }
    if (divisorOf(g, info)) |d| {
        if (!constIsMultiple(c0, d)) return .dead;
    }
    if (info.nonint and constIsMultiple(c0, .{ .a = 1, .b = 0, .co = 1, .rp = 0, .rpd = 0 })) return .dead;
    for (info.excls()) |e| {
        if (grammar.cmpNumConst(e.c, c0) == .eq) return .dead;
    }
    ctx.setWit(.{ .value = .{ .neg = c0.neg, .digits = c0.digits, .exp10 = c0.exp10 } });
    return .alive;
}

/// num_const residual: can the typed spelling still reach the target value
/// (same rules as complete.certNumConst).
fn constReachable(nc: *const grammar.NumConst, nf: *const parser.NumConstFrame) Verdict {
    switch (nf.st) {
        .start => return .alive,
        .minus => return if (nc.neg or nc.digits.len == 0) .alive else .dead,
        .zero_complete, .int_digits, .dot, .frac_digits => return switch (nf.m) {
            .leading => if (nc.digits.len == 0 or nc.neg == nf.neg) .alive else .dead,
            .matching, .tail => if (nc.neg == nf.neg) .alive else .dead,
        },
        .exp, .exp_sign, .exp_digits => switch (nf.m) {
            .matching => return .dead,
            .leading => return if (nc.digits.len == 0) .alive else .dead,
            .tail => {
                if (nc.neg != nf.neg) return .dead;
                const e_need: i64 = nc.exp10 + @as(i64, nf.frac_len) - @as(i64, nf.tail_z);
                switch (nf.st) {
                    .exp => return .alive,
                    .exp_sign => return if (e_need == 0 or (e_need > 0) != nf.exp_neg) .alive else .dead,
                    else => {
                        if (nf.exp == std.math.maxInt(u32)) return .undecided;
                        if (e_need == 0) return if (nf.exp == 0) .alive else .dead;
                        if ((e_need > 0) == nf.exp_neg) return .dead;
                        const x: u64 = @intCast(if (e_need > 0) e_need else -e_need);
                        return if (nf.exp == 0 or decExtHas(nf.exp, x)) .alive else .dead;
                    },
                }
            },
        },
    }
}

/// x is in {p} union {p x 10^j + y : j >= 1, 0 <= y < 10^j} (p > 0).
fn decExtHas(p: u64, x: u64) bool {
    var v = x;
    while (v > p) v /= 10;
    return v == p;
}

// ---- no significant digit yet: every magnitude of the sign ---------------

/// Both magnitude bounds inclusive at one value: the only candidate.
fn pointBounds(mb: MagBounds) bool {
    const lo = mb.lo orelse return false;
    const hi = mb.hi orelse return false;
    return grammar.cmpNumConst(lo.c, hi.c) == .eq;
}

fn staticCase(ctx: Ctx, info: *const Info, div: ?Div, neg: bool) Verdict {
    const mb = magBounds(ctx.g, info, neg);
    if (mb.forbidden) return .dead;
    // A non-integer exists in every interval of positive length; a single
    // admissible value is left to the other analyses.
    if (info.nonint and pointBounds(mb)) return .undecided;
    const hi = mb.hi orelse {
        if (ctx.wit != null) return witnessAbove(ctx, info, div, neg, mb.lo);
        return .alive;
    };
    if (mb.lo == null and div == null) {
        if (ctx.wit != null) return witnessBelow(ctx, info, neg, hi);
        return .alive;
    }
    const zero: Dec = .{ .digits = "", .exp = 0 };
    const lo_end: End = if (mb.lo) |l| .{ .v = .{ .digits = l.c.digits, .exp = l.c.exp10 }, .incl = !l.excl } else .{ .v = zero, .incl = false };
    const hi_end: End = .{ .v = .{ .digits = hi.c.digits, .exp = hi.c.exp10 }, .incl = !hi.excl };
    var excl_buf: [MAX_EXCL]grammar.NumConst = undefined;
    var n_ex: usize = 0;
    for (info.excls()) |e| {
        if (e.c.digits.len == 0 or e.c.neg != neg) continue;
        excl_buf[n_ex] = absConst(e.c);
        n_ex += 1;
    }
    return goodPoint(ctx, lo_end, hi_end, div, excl_buf[0..n_ex], neg);
}

/// Witness for an unbounded-above magnitude with no significant digit typed:
/// the first multiple of the divisor (or the next integer) above the lower
/// bound and every excluded constant.
fn witnessAbove(ctx: Ctx, info: *const Info, div: ?Div, neg: bool, lo: ?Bound) Verdict {
    var floor_om: i64 = 0;
    if (lo) |l| floor_om = @max(floor_om, l.om() + 1);
    for (info.excls()) |e| {
        if (e.c.digits.len != 0) floor_om = @max(floor_om, constOm(e.c) + 1);
    }
    if (div) |d| floor_om = @max(floor_om, d.b + digitsOf(d.a));
    // A x 10^(floor_om + 1) is a multiple of the divisor above every bound.
    const a = ctx.a orelse return .alive;
    const dig = if (div) |d| std.fmt.allocPrint(a, "{d}", .{d.a}) catch return .alive else "1";
    const base_exp: i64 = if (div) |d| @max(d.b, floor_om + 1) else floor_om + 1;
    ctx.setWit(.{ .value = canonical(.{ .neg = neg, .digits = dig, .exp10 = base_exp }) });
    return .alive;
}

/// Witness for a magnitude with no lower bound and no divisor: a power of
/// ten below the upper bound and below every excluded constant.
fn witnessBelow(ctx: Ctx, info: *const Info, neg: bool, hi: Bound) Verdict {
    var om = hi.om() - 1;
    for (info.excls()) |e| {
        if (e.c.digits.len != 0) om = @min(om, constOm(e.c) - 1);
    }
    ctx.setWit(.{ .value = .{ .neg = neg, .digits = "1", .exp10 = om } });
    return .alive;
}

fn canonical(v: struct { neg: bool, digits: []const u8, exp10: i64 }) @FieldType(Witness, "value") {
    var d = v.digits;
    var e = v.exp10;
    while (d.len > 0 and d[0] == '0') d = d[1..];
    while (d.len > 0 and d[d.len - 1] == '0') {
        d = d[0 .. d.len - 1];
        e += 1;
    }
    if (d.len == 0) return .{ .neg = false, .digits = "", .exp10 = 0 };
    return .{ .neg = v.neg, .digits = d, .exp10 = e };
}

// ---- extensible mantissa with a significant digit -------------------------

fn extCase(ctx: Ctx, info: *const Info, div: ?Div, neg: bool) Verdict {
    const mb = magBounds(ctx.g, info, neg);
    if (mb.forbidden) return .dead;
    if (info.nonint and pointBounds(mb)) return .undecided;
    const hi = mb.hi orelse {
        // Unbounded above: a far order of magnitude holds a multiple of the
        // divisor above every bound and excluded constant.
        if (ctx.wit != null) {
            if (div) |d| {
                const w: u32 = @intCast(digitsOf(d.a) + 1);
                const r = (@as(u64, d.rp) * powmod10(w, d.a)) % d.a;
                const d0 = (d.a - r) % d.a;
                ctx.setWit(.{ .ext = .{ .w = w, .x = d0, .place = .big } });
            } else ctx.setWit(.{ .ext = .{ .w = 0, .x = 0, .place = .big } });
        }
        return .alive;
    };
    if (mb.lo == null and div == null) {
        ctx.setWit(.{ .ext = .{ .w = 0, .x = 0, .place = .small } });
        return .alive;
    }
    const t_hi = hi.om();
    const t_lo: i64 = if (mb.lo) |l| l.om() else div.?.b + digitsOf(div.?.a) - 1;
    if (t_lo > t_hi) return .dead;
    var t = t_hi;
    while (true) : (t -= 1) {
        const r = checkT(ctx, info, div, neg, mb, t);
        switch (r) {
            .good => return .alive,
            .undecided => return .undecided,
            .uncut_zero => return .dead,
            .bad => {},
        }
        if (t == t_lo) break;
    }
    return .dead;
}

const TResult = enum { good, bad, uncut_zero, undecided };

fn checkT(ctx: Ctx, info: *const Info, div: ?Div, neg: bool, mb: MagBounds, t: i64) TResult {
    var lo_cut: ?Bound = null;
    var hi_cut: ?Bound = null;
    if (mb.lo) |l| {
        if (t == l.om()) switch (l.cmpSt()) {
            1 => return .bad,
            0 => lo_cut = l,
            else => {},
        };
    }
    const h = mb.hi.?;
    if (t == h.om()) switch (h.cmpSt()) {
        2 => return .bad,
        0 => hi_cut = h,
        else => {},
    };
    const need_n = div != null or info.n_excl > 0;
    if (lo_cut == null and hi_cut == null) return uncutT(ctx, info, div, neg, t);
    if (!need_n) {
        // Ranges only: a cutting lower bound lies inside J_t or at its left
        // end, so only an exclusive upper bound equal to the left end
        // empties the interval.
        var good = true;
        if (hi_cut) |b| {
            if (b.excl and b.matched() >= b.dlen()) good = false;
        }
        if (ctx.wit != null) {
            const n = info.sigDigits() orelse ctx.n_hint orelse return .undecided;
            const r = cutT(ctx, info, div, neg, t, n, lo_cut, hi_cut);
            if ((r == .good) != good) return .undecided;
            return r;
        }
        return if (good) .good else .bad;
    }
    const n = info.sigDigits() orelse return .undecided;
    return cutT(ctx, info, div, neg, t, n, lo_cut, hi_cut);
}

/// Distinct excluded constants of the sign at order of magnitude t whose
/// significant digits extend P (num_excl state 1 or 2), optionally
/// restricted to multiples of the divisor.
fn exclAtT(info: *const Info, neg: bool, t: i64, div: ?Div, out: []grammar.NumConst) usize {
    var n: usize = 0;
    outer: for (info.excls()) |e| {
        if (e.c.digits.len == 0 or e.c.neg != neg) continue;
        if (e.m != 1 and e.m != 2) continue;
        if (constOm(e.c) != t) continue;
        if (div) |d| {
            if (!constIsMultiple(e.c, d)) continue;
        }
        const ac = absConst(e.c);
        for (out[0..n]) |o| {
            if (grammar.cmpNumConst(o, ac) == .eq) continue :outer;
        }
        out[n] = ac;
        n += 1;
    }
    return n;
}

fn uncutT(ctx: Ctx, info: *const Info, div: ?Div, neg: bool, t: i64) TResult {
    var ex_buf: [MAX_EXCL]grammar.NumConst = undefined;
    const ex_n = exclAtT(info, neg, t, div, &ex_buf);
    const n = info.sigDigits();
    const d = div orelse {
        // J_t has positive length: infinitely many values, finitely many
        // excluded.
        if (ctx.wit != null) {
            const nn = n orelse ctx.n_hint orelse return .undecided;
            witnessUncutFree(ctx, t, nn, ex_buf[0..ex_n]);
        }
        return .good;
    };
    const nn = n orelse return .undecided;
    const k = t - nn + 1;
    const count = countUncut(info, d, k);
    if (count == 0) return .uncut_zero;
    if (count <= ex_n) return .bad;
    if (ctx.wit != null) witnessUncutDiv(ctx, info, d, t, nn, k, ex_buf[0..ex_n]);
    return .good;
}

/// Number of multiples of the divisor in J_t = [P x 10^k, (P+1) x 10^k),
/// saturated at maxInt(u64).
fn countUncut(info: *const Info, d: Div, k: i64) u64 {
    if (k >= d.b) {
        const w: u64 = @intCast(k - d.b);
        // 10^w >= 10^10 > a: the interval holds at least floor(10^w / a).
        if (w >= 19) return std.math.maxInt(u64);
        const width = std.math.pow(u64, 10, w);
        const r = (@as(u64, d.rp) * powmod10(w, d.a)) % d.a;
        const d0 = (d.a - r) % d.a;
        if (d0 >= width) return 0;
        return (width - 1 - d0) / d.a + 1;
    }
    // Width below 10^B: the only candidate is P x 10^k itself, which needs
    // a x 10^(B-k) | P.
    const tz = info.tz orelse 0;
    const need: u64 = @intCast(d.b - k);
    if (tz < need) return 0;
    const shift = tz - need;
    return if ((@as(u64, d.rpd) * powmod10(shift, d.a)) % d.a == 0) 1 else 0;
}

/// Offset (in units of 10^u) of an excluded constant inside J_t when it
/// lies on that grid; null otherwise. P has n digits at order t.
fn exclOffset(c: grammar.NumConst, n: i64, u: i64, w: u32) ?u128 {
    // Digits of c beyond P sit at positions t - n .. ; with units 10^u and
    // w appended digits, c is on the grid iff its digits past index n + w
    // are absent (canonical form has no trailing zeros).
    const dl: i64 = @intCast(c.digits.len);
    if (dl > n + w) return null;
    if (c.exp10 < u) return null;
    var x: u128 = 0;
    var i: i64 = n;
    while (i < n + w) : (i += 1) {
        const dig: u128 = if (i < dl) c.digits[@intCast(i)] - '0' else 0;
        x = x * 10 + dig;
    }
    return x;
}

fn witnessUncutFree(ctx: Ctx, t: i64, n: i64, ex: []const grammar.NumConst) void {
    var w: u32 = 1;
    var cap: u64 = 10;
    while (cap <= ex.len + 1) : (cap *= 10) w += 1;
    const k = t - n + 1;
    const u = k - @as(i64, w);
    var x: u128 = 0;
    while (x <= ex.len) : (x += 1) {
        var hit = false;
        for (ex) |c| {
            if (exclOffset(c, n, u, w)) |o| {
                if (o == x) hit = true;
            }
        }
        if (!hit) break;
    }
    ctx.setWit(.{ .ext = .{ .w = w, .x = x, .place = .{ .om = t } } });
}

fn witnessUncutDiv(ctx: Ctx, info: *const Info, d: Div, t: i64, n: i64, k: i64, ex: []const grammar.NumConst) void {
    if (k < d.b) {
        // count == 1: P x 10^k itself.
        ctx.setWit(.{ .ext = .{ .w = 0, .x = 0, .place = .{ .om = t } } });
        return;
    }
    _ = info;
    const w: u32 = @intCast(@min(k - d.b, 36));
    const u = k - @as(i64, w);
    const width: u128 = std.math.pow(u128, 10, w);
    const r = (@as(u64, d.rp) * powmod10(w, d.a)) % d.a;
    var x: u128 = (d.a - r) % d.a;
    while (x < width) : (x += d.a) {
        var hit = false;
        for (ex) |c| {
            if (exclOffset(c, n, u, w)) |o| {
                if (o == x) hit = true;
            }
        }
        if (!hit) break;
    }
    ctx.setWit(.{ .ext = .{ .w = w, .x = x, .place = .{ .om = t } } });
}

/// J_t cut by a bound whose digits P matches: P is known, so the interval
/// endpoints are exact decimals.
fn cutT(ctx: Ctx, info: *const Info, div: ?Div, neg: bool, t: i64, n: i64, lo_cut: ?Bound, hi_cut: ?Bound) TResult {
    const src = lo_cut orelse hi_cut.?;
    const dl: i64 = src.dlen();
    const k = t - n + 1;
    var ex_buf: [MAX_EXCL]grammar.NumConst = undefined;
    const ex_n = exclAtT(info, neg, t, div, &ex_buf);
    // With a divisor every relevant point (bounds, multiples, excluded
    // constants) lies on the 10^g grid, so J_t's right end can move to
    // 10^(g-1) past its left end without changing the grid points inside.
    var kr = k;
    if (div) |d| {
        var g: i64 = @min(src.c.exp10, d.b);
        if (lo_cut) |b| g = @min(g, b.c.exp10);
        if (hi_cut) |b| g = @min(g, b.c.exp10);
        for (ex_buf[0..ex_n]) |c| g = @min(g, c.exp10);
        kr = @max(k, g - 1);
    }
    const p_len: usize = @intCast(@min(n, dl));
    const left: Dec = .{ .digits = src.c.digits[0..p_len], .exp = k + (n - @as(i64, @intCast(p_len))) };
    const a = ctx.a orelse return .undecided;
    var arena = Arena.init(a);
    defer arena.deinit();
    const al = arena.allocator();
    const right = decAddPow10(al, left, kr) catch return .undecided;
    var lo_end: End = .{ .v = left, .incl = true };
    var hi_end: End = .{ .v = right, .incl = false };
    if (lo_cut) |b| {
        const bv: Dec = .{ .digits = b.c.digits, .exp = b.c.exp10 };
        if (decCmp(bv, left) != .lt) lo_end = .{ .v = bv, .incl = !b.excl };
    }
    if (hi_cut) |b| {
        const bv: Dec = .{ .digits = b.c.digits, .exp = b.c.exp10 };
        if (decCmp(bv, right) == .lt) hi_end = .{ .v = bv, .incl = !b.excl };
    }
    // Excluded constants restricted to the cut interval.
    var in_n: usize = 0;
    for (ex_buf[0..ex_n]) |c| {
        const cv: Dec = .{ .digits = c.digits, .exp = c.exp10 };
        if (!endBelow(lo_end, cv) or !endAbove(hi_end, cv)) continue;
        ex_buf[in_n] = c;
        in_n += 1;
    }
    var sub = ctx;
    var wit_val: Witness = .none;
    if (ctx.wit != null) sub.wit = &wit_val;
    sub.a = al;
    const v = goodPoint(sub, lo_end, hi_end, div, ex_buf[0..in_n], neg);
    return switch (v) {
        .alive => blk: {
            if (ctx.wit) |p| p.* = dupWitness(a, wit_val) catch .none;
            break :blk .good;
        },
        .dead => .bad,
        .undecided => .undecided,
    };
}

fn dupWitness(a: std.mem.Allocator, w: Witness) !Witness {
    return switch (w) {
        .value => |v| .{ .value = .{ .neg = v.neg, .digits = try a.dupe(u8, v.digits), .exp10 = v.exp10 } },
        else => w,
    };
}

// ---- frozen mantissa ------------------------------------------------------

const ExpSet = union(enum) { any, ray: bool, ext: struct { neg: bool, e: u64 } };

fn frozenCase(ctx: Ctx, info: *const Info, div: ?Div, neg: bool) Verdict {
    const mb = magBounds(ctx.g, info, neg);
    if (mb.forbidden) return .dead;
    if (info.n_ranges > 0 and info.om_m == null) return .undecided;
    const om_m = info.om_m orelse 0;
    var lo_e: ?i64 = null;
    var hi_e: ?i64 = null;
    const e_l: ?i64 = if (mb.lo) |l| l.om() - om_m else null;
    const e_h: ?i64 = if (mb.hi) |h| h.om() - om_m else null;
    lo_e = e_l;
    hi_e = e_h;
    if (div) |d| {
        const fl = info.frac_len orelse return .undecided;
        const tz = info.tz orelse return .undecided;
        var s_min: ?i64 = null;
        var s: u64 = 0;
        while (s <= 64) : (s += 1) {
            if ((@as(u64, d.rpd) * powmod10(s, d.a)) % d.a == 0) {
                s_min = @intCast(s);
                break;
            }
        }
        const sm = s_min orelse return .dead;
        const e_div = sm + @as(i64, fl) - @as(i64, tz) + d.b;
        lo_e = if (lo_e) |x| @max(x, e_div) else e_div;
    }
    if (info.nonint) {
        // value = M x 10^(e - frac_len) is an integer iff e >= frac_len - tz.
        const fl = info.frac_len orelse return .undecided;
        const tz = info.tz orelse return .undecided;
        const e_int = @as(i64, fl) - @as(i64, tz);
        hi_e = if (hi_e) |h| @min(h, e_int - 1) else e_int - 1;
    }
    var ex_e: [MAX_EXCL]i64 = undefined;
    var ex_n: usize = 0;
    for (info.excls()) |e| {
        if (e.c.digits.len == 0 or e.c.neg != neg or e.m != 2) continue;
        const fl = info.frac_len orelse return .undecided;
        ex_e[ex_n] = e.c.exp10 + @as(i64, fl) - @as(i64, e.tail_z);
        ex_n += 1;
    }
    const set: ExpSet = switch (info.st) {
        .exp => .any,
        .exp_sign => .{ .ray = info.exp_neg },
        else => .{ .ext = .{ .neg = info.exp_neg, .e = info.exp } },
    };
    if (info.exp_sat and info.st == .exp_digits) {
        if (info.nonint) return .undecided;
        const lim: i64 = 1 << 30;
        if (!info.exp_neg) {
            if (hi_e) |h| return if (h >= lim) .undecided else .dead;
            if (lo_e) |l| if (l >= lim) return .undecided;
        } else {
            if (lo_e) |l| return if (l <= -lim) .undecided else .dead;
            if (hi_e) |h| if (h <= -lim) return .undecided;
        }
        ctx.setWit(.as_is);
        return .alive;
    }
    var cand: [MAX_EXCL + 4]i64 = undefined;
    const k = ex_n + 3;
    const nc = expCandidates(set, lo_e, hi_e, cand[0..k]);
    for (cand[0..nc]) |e| {
        if (mb.lo) |l| {
            if (e == e_l.? and !pointLo(l)) continue;
        }
        if (mb.hi) |h| {
            if (e == e_h.? and !pointHi(h)) continue;
        }
        var hit = false;
        for (ex_e[0..ex_n]) |x| {
            if (x == e) hit = true;
        }
        if (hit) continue;
        ctx.setWit(.{ .frozen = .{ .e = e } });
        return .alive;
    }
    return .dead;
}

/// Frozen mantissa at the bound's order of magnitude: is the value >= L
/// (> L when exclusive). cmp 0 with unmatched bound digits means P is a
/// proper prefix of the bound's digits, so the value is below the bound.
fn pointLo(b: Bound) bool {
    return switch (b.cmpSt()) {
        2 => true,
        1 => false,
        else => b.matched() >= b.dlen() and !b.excl,
    };
}

fn pointHi(b: Bound) bool {
    return switch (b.cmpSt()) {
        1 => true,
        2 => false,
        else => b.matched() < b.dlen() or !b.excl,
    };
}

/// Up to out.len elements of the exponent set within [lo, hi].
fn expCandidates(set: ExpSet, lo: ?i64, hi: ?i64, out: []i64) usize {
    var n: usize = 0;
    switch (set) {
        .any => {
            if (lo) |l| {
                var e = l;
                while (n < out.len and (hi == null or e <= hi.?)) : (e += 1) {
                    out[n] = e;
                    n += 1;
                }
            } else {
                var e: i64 = hi orelse 0;
                while (n < out.len) : (e -= 1) {
                    out[n] = e;
                    n += 1;
                }
            }
        },
        .ray => |neg| return expCandidates(.{ .ext = .{ .neg = neg, .e = 0 } }, lo, hi, out),
        .ext => |x| {
            // e = +-N, N in ext(E); enumerate N ascending in [na, nb].
            var na: i128 = 0;
            var nb: ?i128 = null;
            if (!x.neg) {
                if (lo) |l| na = @max(na, l);
                if (hi) |h| nb = h;
            } else {
                if (hi) |h| na = @max(na, -@as(i128, h));
                if (lo) |l| nb = -@as(i128, l);
            }
            if (nb != null and nb.? < na) return 0;
            var buf: [MAX_EXCL + 4]i128 = undefined;
            const m = extElements(x.e, na, nb, buf[0..out.len]);
            for (buf[0..m]) |v| {
                const e: i128 = if (x.neg) -v else v;
                if (e < std.math.minInt(i64) or e > std.math.maxInt(i64)) continue;
                out[n] = @intCast(e);
                n += 1;
            }
        },
    }
    return n;
}

/// Ascending elements of ext(p) = {p} u {p x 10^j + y} within [a, b]
/// (p == 0: every N >= 0).
fn extElements(p: u64, a: i128, b: ?i128, out: []i128) usize {
    var n: usize = 0;
    if (p == 0) {
        var v = a;
        while (n < out.len and (b == null or v <= b.?)) : (v += 1) {
            out[n] = v;
            n += 1;
        }
        return n;
    }
    const limit: i128 = std.math.pow(i128, 10, 30);
    var base: i128 = p;
    var width: i128 = 1;
    while (n < out.len and base < limit) {
        if (b != null and base > b.?) break;
        const lo = @max(base, a);
        var hi_b = base + width - 1;
        if (b) |bb| hi_b = @min(hi_b, bb);
        var v = lo;
        while (v <= hi_b and n < out.len) : (v += 1) {
            out[n] = v;
            n += 1;
        }
        base *= 10;
        width *= 10;
    }
    return n;
}

// ---- exact decimal intervals ----------------------------------------------

/// Non-negative decimal: value = int(digits) x 10^exp (digits ASCII, may
/// have leading zeros; empty = zero).
const Dec = struct { digits: []const u8, exp: i64 };
const End = struct { v: Dec, incl: bool };

fn decIsZero(d: Dec) bool {
    for (d.digits) |c| {
        if (c != '0') return false;
    }
    return true;
}

fn decCmp(a: Dec, b: Dec) std.math.Order {
    const az = decIsZero(a);
    const bz = decIsZero(b);
    if (az and bz) return .eq;
    if (az) return .lt;
    if (bz) return .gt;
    const ca = canonDec(a);
    const cb = canonDec(b);
    return grammar.cmpNumConst(.{ .neg = false, .digits = ca.digits, .exp10 = ca.exp }, .{ .neg = false, .digits = cb.digits, .exp10 = cb.exp });
}

fn canonDec(d: Dec) Dec {
    var s = d.digits;
    var e = d.exp;
    while (s.len > 0 and s[0] == '0') s = s[1..];
    while (s.len > 0 and s[s.len - 1] == '0') {
        s = s[0 .. s.len - 1];
        e += 1;
    }
    return .{ .digits = s, .exp = e };
}

/// lo end admits v (v >= lo, or > when exclusive).
fn endBelow(lo: End, v: Dec) bool {
    const o = decCmp(lo.v, v);
    return o == .lt or (o == .eq and lo.incl);
}

fn endAbove(hi: End, v: Dec) bool {
    const o = decCmp(v, hi.v);
    return o == .lt or (o == .eq and hi.incl);
}

fn decAddPow10(a: std.mem.Allocator, d: Dec, k: i64) !Dec {
    // Align to exponent min(d.exp, k) and add 1 at position k.
    const e0 = @min(d.exp, k);
    const pad_d: usize = @intCast(d.exp - e0);
    const pos: usize = @intCast(k - e0);
    const len = @max(d.digits.len + pad_d, pos + 1) + 1;
    if (@as(i64, @intCast(len)) > MAX_BIG_DIGITS) return error.TooBig;
    const buf = try a.alloc(u8, len);
    @memset(buf, '0');
    // Right-aligned copy of d followed by pad_d zeros.
    const start = len - pad_d - d.digits.len;
    @memcpy(buf[start .. start + d.digits.len], d.digits);
    var i: usize = len - 1 - pos;
    while (true) {
        if (buf[i] == '9') {
            buf[i] = '0';
            i -= 1;
        } else {
            buf[i] += 1;
            break;
        }
    }
    return .{ .digits = buf, .exp = e0 };
}

fn toBig(a: std.mem.Allocator, d: Dec, g0: i64) !BigInt {
    var r = try BigInt.init(a);
    const cd = canonDec(d);
    if (cd.digits.len == 0) return r;
    const shift = cd.exp - g0;
    if (shift < 0 or @as(i64, @intCast(cd.digits.len)) + shift > MAX_BIG_DIGITS) return error.TooBig;
    try r.setString(10, cd.digits);
    if (shift > 0) {
        var p = try BigInt.initSet(a, 10);
        try p.pow(&p, @intCast(shift));
        try r.mul(&r, &p);
    }
    return r;
}

fn minExp(d: Dec) i64 {
    const c = canonDec(d);
    return if (c.digits.len == 0) std.math.maxInt(i64) else c.exp;
}

/// Is there a value in the interval [lo, hi] that is a multiple of the
/// divisor (any finite decimal without one) and not an excluded constant.
/// `ex` holds the excluded magnitudes already restricted to the interval.
fn goodPoint(ctx: Ctx, lo: End, hi: End, div: ?Div, ex_all: []const grammar.NumConst, neg: bool) Verdict {
    const a = ctx.a orelse return .undecided;
    var arena = Arena.init(a);
    defer arena.deinit();
    const al = arena.allocator();
    var ex_buf: [MAX_EXCL]grammar.NumConst = undefined;
    var ex_n: usize = 0;
    for (ex_all) |c| {
        const cv: Dec = .{ .digits = c.digits, .exp = c.exp10 };
        if (!endBelow(lo, cv) or !endAbove(hi, cv)) continue;
        if (div) |d| {
            if (!constIsMultiple(c, d)) continue;
        }
        ex_buf[ex_n] = c;
        ex_n += 1;
    }
    const ex = ex_buf[0..ex_n];
    const o = decCmp(lo.v, hi.v);
    if (o == .gt) return .dead;
    if (o == .eq and !(lo.incl and hi.incl)) return .dead;
    var g0: i64 = @min(minExp(lo.v), minExp(hi.v));
    if (div) |d| g0 = @min(g0, d.b);
    for (ex) |c| g0 = @min(g0, c.exp10);
    if (g0 == std.math.maxInt(i64)) g0 = 0;
    if (div == null) {
        if (o == .eq) {
            if (ex.len > 0) return .dead;
            if (ctx.wit != null) setValueWit(ctx, a, lo.v, neg);
            return .alive;
        }
        // Positive length: infinitely many decimals, finitely many excluded.
        if (ctx.wit != null) {
            freeWitness(ctx, al, lo.v, hi.v, ex, neg, g0) catch return .undecided;
        }
        return .alive;
    }
    const d = div.?;
    var lo_b = toBig(al, lo.v, g0) catch return .undecided;
    var hi_b = toBig(al, hi.v, g0) catch return .undecided;
    var dv = toBig(al, .{ .digits = std.fmt.allocPrint(al, "{d}", .{d.a}) catch return .undecided, .exp = d.b }, g0) catch return .undecided;
    // first = smallest x with x*D in the interval, last = largest.
    var q = BigInt.init(al) catch return .undecided;
    var r = BigInt.init(al) catch return .undecided;
    q.divFloor(&r, &lo_b, &dv) catch return .undecided;
    var first = q.clone() catch return .undecided;
    if (!r.eqlZero() or !lo.incl) first.addScalar(&first, 1) catch return .undecided;
    q.divFloor(&r, &hi_b, &dv) catch return .undecided;
    var last = q.clone() catch return .undecided;
    if (r.eqlZero() and !hi.incl) last.addScalar(&last, -1) catch return .undecided;
    var cnt = BigInt.init(al) catch return .undecided;
    cnt.sub(&last, &first) catch return .undecided;
    cnt.addScalar(&cnt, 1) catch return .undecided;
    var need = BigInt.initSet(al, ex.len) catch return .undecided;
    if (cnt.order(need) != .gt) return .dead;
    _ = &need;
    if (ctx.wit != null) {
        var x = first.clone() catch return .undecided;
        var val = BigInt.init(al) catch return .undecided;
        var i: usize = 0;
        while (i <= ex.len) : (i += 1) {
            val.mul(&x, &dv) catch return .undecided;
            var hit = false;
            for (ex) |c| {
                var cb = toBig(al, .{ .digits = c.digits, .exp = c.exp10 }, g0) catch return .undecided;
                if (cb.order(val) == .eq) hit = true;
            }
            if (!hit) break;
            x.addScalar(&x, 1) catch return .undecided;
        }
        const s = val.toString(al, 10, .lower) catch return .undecided;
        setValueWit(ctx, a, .{ .digits = s, .exp = g0 }, neg);
    }
    return .alive;
}

fn setValueWit(ctx: Ctx, a: std.mem.Allocator, v: Dec, neg: bool) void {
    const c = canonDec(v);
    const digits = a.dupe(u8, c.digits) catch return;
    ctx.setWit(.{ .value = canonical(.{ .neg = neg, .digits = digits, .exp10 = c.exp }) });
}

/// A finite decimal strictly inside (lo, hi) that is not excluded:
/// lo + (hi - lo) / 2^j for the first such j.
fn freeWitness(ctx: Ctx, al: std.mem.Allocator, lo: Dec, hi: Dec, ex: []const grammar.NumConst, neg: bool, g0_in: i64) !void {
    const g0 = g0_in - 64;
    var lo_b = try toBig(al, lo, g0);
    var hi_b = try toBig(al, hi, g0);
    var diff = try BigInt.init(al);
    try diff.sub(&hi_b, &lo_b);
    var j: usize = 1;
    var cand = try BigInt.init(al);
    while (j <= ex.len + 1) : (j += 1) {
        var step = try diff.clone();
        try step.shiftRight(&step, j);
        try cand.add(&lo_b, &step);
        var hit = false;
        for (ex) |c| {
            var cb = try toBig(al, .{ .digits = c.digits, .exp = c.exp10 }, g0);
            if (cb.order(cand) == .eq) hit = true;
        }
        if (!hit) break;
    }
    const s = try cand.toString(al, 10, .lower);
    setValueWit(ctx, ctx.a.?, .{ .digits = s, .exp = g0 }, neg);
}

// ---- compile time: static satisfiability of numeric conjunctions ----------

const MAX_TERMS = 64;

fn isNumLeafNode(n: grammar.Node) bool {
    return switch (n) {
        .num_v, .int_num, .num_range, .num_mult, .num_excl, .num_const => true,
        else => false,
    };
}

/// The node accepts numbers only (so a conjunction with it is numeric).
pub fn numericOnly(g: *const grammar.Grammar, id: grammar.NodeId) bool {
    return switch (g.node(id).*) {
        .num_v, .int_num, .num_range, .num_mult, .num_excl, .num_const, .not_int_num => true,
        .comb => |cb| blk: {
            if (cb.kind != .allof) break :blk false;
            for (cb.branches) |b| {
                if (numericOnly(g, b)) break :blk true;
            }
            break :blk false;
        },
        .choice => |alts| blk: {
            for (alts) |b| {
                if (!numericOnly(g, b)) break :blk false;
            }
            break :blk alts.len > 0;
        },
        else => false,
    };
}

/// Nodes that never accept a number: arms of these kinds drop out of a
/// numeric conjunction. A literal can spell a number only when it starts
/// with '-' or a digit.
fn neverNumeric(g: *const grammar.Grammar, id: grammar.NodeId) bool {
    return switch (g.node(id).*) {
        .str, .str_pat, .str_excl, .open_obj, .object, .repeat => true,
        .literal => |lit| !mayStartNumber(g.literalBytes(lit)),
        .lit_trie => |lt| blk: {
            for (lt.literals) |lit| {
                if (mayStartNumber(g.literalBytes(lit))) break :blk false;
            }
            break :blk true;
        },
        else => false,
    };
}

fn mayStartNumber(bytes: []const u8) bool {
    return bytes.len == 0 or bytes[0] == '-' or (bytes[0] >= '0' and bytes[0] <= '9');
}

const Terms = struct {
    t: [MAX_TERMS][MAX_LEAVES]grammar.NodeId = undefined,
    len: [MAX_TERMS]u8 = undefined,
    n: usize = 0,
};

/// Disjunctive normal form of a numeric node over numeric leaf nodes
/// (choice = union, allOf = conjunction); error.Unknown for other shapes.
fn nodeDnf(g: *const grammar.Grammar, id: grammar.NodeId, depth: usize, out: *Terms) error{Unknown}!void {
    if (depth > 16) return error.Unknown;
    const n = g.node(id).*;
    if (isNumLeafNode(n)) {
        out.t[0][0] = id;
        out.len[0] = 1;
        out.n = 1;
        return;
    }
    switch (n) {
        .choice => |alts| {
            out.n = 0;
            for (alts) |b| {
                if (neverNumeric(g, b)) continue;
                var sub: Terms = .{};
                try nodeDnf(g, b, depth + 1, &sub);
                for (0..sub.n) |i| {
                    if (out.n >= MAX_TERMS) return error.Unknown;
                    out.t[out.n] = sub.t[i];
                    out.len[out.n] = sub.len[i];
                    out.n += 1;
                }
            }
        },
        .comb => |cb| {
            if (cb.kind != .allof) return error.Unknown;
            out.n = 1;
            out.len[0] = 0;
            for (cb.branches) |b| {
                var sub: Terms = .{};
                try nodeDnf(g, b, depth + 1, &sub);
                if (out.n * sub.n > MAX_TERMS) return error.Unknown;
                var next: Terms = .{};
                for (0..out.n) |i| {
                    for (0..sub.n) |j| {
                        const l: usize = @as(usize, out.len[i]) + sub.len[j];
                        if (l > MAX_LEAVES) return error.Unknown;
                        @memcpy(next.t[next.n][0..out.len[i]], out.t[i][0..out.len[i]]);
                        @memcpy(next.t[next.n][out.len[i]..l], sub.t[j][0..sub.len[j]]);
                        next.len[next.n] = @intCast(l);
                        next.n += 1;
                    }
                }
                out.* = next;
                if (out.n == 0) return;
            }
        },
        else => return error.Unknown,
    }
}

fn pristine(g: *const grammar.Grammar, id: grammar.NodeId) parser.Frame {
    return switch (g.node(id).*) {
        .num_v => .{ .num_v = .{ .node = id, .st = .start } },
        .int_num => .{ .int_num = .{ .node = id, .tz = 0, .frac_len = 0, .exp = 0, .st = .start, .nz = false, .exp_neg = false } },
        .num_range => .{ .num_range = .{ .node = id, .int_len = 0, .fz = 0, .exp = 0, .j = .{ 0, 0 }, .st = .start, .cmp = 0, .flags = 0 } },
        .num_mult => .{ .num_mult = .{ .node = id, .rem = 0, .rem_d = 0, .frac_len = 0, .tz = 0, .exp = 0, .st = .start, .flags = 0 } },
        .num_excl => .{ .num_excl = .{ .node = id, .tail_z = .{ 0, 0 }, .frac_len = 0, .exp = 0, .j = .{ 0, 0 }, .st = .start, .m = 0, .exp_neg = false, .neg = false } },
        .num_const => .{ .num_const = .{ .node = id, .j = 0, .frac_len = 0, .tail_z = 0, .exp = 0, .st = .start, .m = .leading, .exp_neg = false, .neg = false } },
        else => unreachable,
    };
}

/// Compile-time emptiness of a numeric-only node: .dead when no number
/// satisfies it (every DNF term is jointly unsatisfiable at the start of
/// the lexeme), .alive when some term is satisfiable, .undecided otherwise.
pub fn nodeVerdict(g: *const grammar.Grammar, a: std.mem.Allocator, id: grammar.NodeId) Verdict {
    if (!numericOnly(g, id)) return .undecided;
    var terms: Terms = .{};
    nodeDnf(g, id, 0, &terms) catch return .undecided;
    var any_undecided = false;
    for (0..terms.n) |i| {
        var frames: [MAX_LEAVES]parser.Frame = undefined;
        var ptrs: [MAX_LEAVES]*const parser.Frame = undefined;
        const l = terms.len[i];
        for (0..l) |k| {
            frames[k] = pristine(g, terms.t[i][k]);
            ptrs[k] = &frames[k];
        }
        switch (termVerdict(.{ .g = g, .a = a, .wit = null }, ptrs[0..l])) {
            .alive => return .alive,
            .undecided => any_undecided = true,
            .dead => {},
        }
    }
    return if (any_undecided) .undecided else .dead;
}
