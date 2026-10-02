//! Completion synthesis for the simulation-verified alive certificate
//! (spec-v1, ADR-0005 D3). certifyState's closed-form rules leave some
//! states undecided (mid-value comb groups, gated open_obj residuals, value
//! machines past their first byte). This module generates candidate
//! completing suffixes from a thread's frame stack - obligation-driven and
//! heuristic - and verifies each candidate by feeding it to a copy of the
//! real parser state and checking parser.canEnd. A verified candidate is a
//! concrete witness of reachability: the same suffix completes the whole
//! state under the real transition semantics, comb verdicts included. A
//! failed candidate proves nothing: the state stays undecided (never
//! dead), and the budgeted search / ResourceLimit path takes over. No
//! verdict is ever approximated.
//!
//! Candidate quality is the only heuristic part. The shapes covered:
//!  - pristine frames (birth state) via witset.witness of the node;
//!  - mid-value literals/tries/strings/numbers via minimal canonical
//!    completions, plus vote-splitting trial variants (".5"/"e1") for
//!    oneOf-over-number states;
//!  - mid-string pattern values via BFS over the DFA from the current run
//!    state to an accept;
//!  - open_obj bodies with remaining-obligation emission (required
//!    declared props, extra_required, required deps, min_props padding),
//!    merged across the allof sibling branches of the surrounding comb
//!    chain (discriminated-union shapes: allOf facets around a oneOf of
//!    object alternatives), with per-frame dispatch-order validation;
//!  - mid-flight closed objects above a comb chain, merged with their
//!    allof sibling facets (closed objects and open_objs): the union of
//!    remaining obligations, requiring every emitted key to be declared in
//!    and order-compatible with every closed facet; a oneOf-nested sibling
//!    branch contributes the alternative selected by the alt axis;
//!  - value boundaries: an open_obj whose value machine is still pristine
//!    emits a witness of the pending-schema intersection across the merged
//!    sibling set instead of one sibling's machine;
//!  - in-flight keys mid-escape or mid-UTF-8, completed through the
//!    canonical escape table;
//!  - closed objects, seq tails, repeat element obligations.
//! Anything else (mid-string str_excl, mid-value num_const, ifelse combs,
//! schema-kind deps in force, ...) fails the candidate, not the state.

const std = @import("std");
const grammar = @import("grammar.zig");
const parser = @import("parser.zig");
const pattern = @import("pattern.zig");
const witset = @import("witset.zig");

const MAX_SIMS: usize = 24;
const MAX_THREADS_TRIED: usize = 32;
const MAX_SYNTH_BYTES: usize = 1 << 12;
const MAX_EMIT_DEPTH: u32 = 24;
const MAX_SET: usize = 16; // merged object frames per emission
const MAX_GROUP: usize = 16; // group-sibling value constraints per boundary
const MAX_OBL: usize = 64; // obligation keys per object body
const MAX_TRIAL: u8 = 2;
const MAX_ALT: u8 = 8; // oneOf-nested sibling alternatives tried per candidate

const Ctx = struct {
    g: *const grammar.Grammar,
    st: *const parser.State,
    side: *const parser.Side,
    a: std.mem.Allocator,
    out: *std.ArrayListUnmanaged(u8),
    trial: u8,
    key_choice: u8 = 0,
    trial_spent: bool = false,
    /// Deepest open_obj .value frame of the thread whose (pristine) value
    /// machine is replaced by an intersection emission; null = none.
    value_boundary: ?usize = null,
    /// 0: intersect group-sibling constraints at a value boundary (exact
    /// when sibling groups are fresh there, over-constrained when a sibling
    /// oneOf is mid-outer-value); 1: merged-chain pendings only. Both are
    /// tried; the sim arbitrates.
    group_mode: u8 = 0,
    /// Selects which live alternative of a oneOf-nested allof sibling
    /// branch contributes obligations to a merged emission (0 = first).
    /// Out-of-range fails the candidate; the sim arbitrates rejection of
    /// the alternatives not merged.
    alt: u8 = 0,
    /// Set when an emission encountered a sibling branch with more than
    /// one live alternative (bounds the alt axis).
    alt_seen: bool = false,
    /// The walk's upper bound in the thread (value_boundary + 1, else
    /// t.len): frames above are emitted by the walk / skipped pristine.
    walk_top: usize = 0,
};

/// Heuristic thread progress for candidate ordering: typed keys / values /
/// chars the thread carries. The thread the payload actually drove deepest
/// is most likely to yield a verifiable completion, so it goes first.
/// Ordering never affects soundness (every candidate is sim-verified).
fn threadProgress(side: *const parser.Side, t: *const parser.Thread) u32 {
    var p: u32 = 0;
    for (t.frames[0..t.len]) |*f| {
        p += switch (f.*) {
            .literal => |fr| fr.off,
            .lit_trie => |fr| fr.node,
            .str => |fr| fr.count,
            .str_excl => |fr| fr.count,
            .str_pat => |fr| fr.count,
            .seq => |fr| fr.idx,
            .object => |fr| fr.idx,
            .repeat => |fr| fr.count,
            .open_obj => |fr| fr.next_idx + seenCount(side, fr.seen),
            else => 0,
        };
    }
    return p;
}

/// Attempt an exact alive certificate by concrete completion. True only
/// when a synthesized suffix was fed to a state copy and reached canEnd.
pub fn aliveBySim(g: *const grammar.Grammar, st: *const parser.State, side: *parser.Side) bool {
    var arena = std.heap.ArenaAllocator.init(side.a);
    defer arena.deinit();
    const a = arena.allocator();
    // Most-progressed stacks first, deepest as tiebreak: progress tracks
    // the branch the payload is actually validating against (specific
    // obligations), depth carries the full comb chain for the merge.
    var order: [parser.MAX_THREADS_CAP]u16 = undefined;
    var prog: [parser.MAX_THREADS_CAP]u32 = undefined;
    const n: usize = st.n;
    for (0..n) |i| {
        order[i] = @intCast(i);
        prog[i] = threadProgress(side, &st.threads[i]);
    }
    const Ctx2 = struct {
        s: *const parser.State,
        p: []const u32,
    };
    std.mem.sort(u16, order[0..n], Ctx2{ .s = st, .p = prog[0..n] }, struct {
        fn lt(c: Ctx2, x: u16, y: u16) bool {
            if (c.p[x] != c.p[y]) return c.p[x] > c.p[y];
            return c.s.threads[x].len > c.s.threads[y].len;
        }
    }.lt);
    var sims: usize = 0;
    var tried: usize = 0;
    var failed: std.ArrayListUnmanaged([]const u8) = .{};
    thread_loop: for (order[0..n]) |ti| {
        const t = &st.threads[ti];
        if (t.len == 0) continue;
        // Only candidate-producing threads count against the budget: a
        // thread whose synthesis fails outright is cheap and common (deep
        // oneOf variant branches with unmeetable obligations).
        var counted = false;
        const kc_max: u8 = if (hasKeyStrFrame(t)) 3 else 1;
        var gm: u8 = 0;
        while (gm <= 1) : (gm += 1) {
            if (gm == 1 and !hasCombFrame(t)) break;
            var trial: u8 = 0;
            while (trial <= MAX_TRIAL) : (trial += 1) {
                if (trial > 0 and !hasTrialFrame(g, t)) break;
                var kc: u8 = 0;
                while (kc < kc_max) : (kc += 1) {
                    var alt: u8 = 0;
                    while (alt <= MAX_ALT) : (alt += 1) {
                        if (alt > 0 and !hasCombFrame(t)) break;
                        var out: std.ArrayListUnmanaged(u8) = .{};
                        var ctx: Ctx = .{ .g = g, .st = st, .side = side, .a = a, .out = &out, .trial = trial, .key_choice = kc, .group_mode = gm, .alt = alt };
                        if (!synthThread(&ctx, t)) {
                            if (!ctx.alt_seen) break;
                            continue;
                        }
                        if (out.items.len == 0 or out.items.len > MAX_SYNTH_BYTES) continue;
                        if (!counted) {
                            if (tried >= MAX_THREADS_TRIED) break :thread_loop;
                            tried += 1;
                            counted = true;
                        }
                        // A byte-identical candidate fails the same way; skip it.
                        var dup = false;
                        for (failed.items) |fb| {
                            if (std.mem.eql(u8, fb, out.items)) {
                                dup = true;
                                break;
                            }
                        }
                        if (dup) {
                            if (!ctx.alt_seen) break;
                            continue;
                        }
                        if (sims >= MAX_SIMS) return false;
                        sims += 1;
                        if (simAccepts(g, st, side, out.items)) return true;
                        failed.append(a, out.items) catch return false;
                        if (!ctx.alt_seen) break;
                    }
                }
            }
        }
    }
    return false;
}

/// Feed the candidate to a state copy under the real semantics; true iff
/// the copy accepts (canEnd) after exactly the candidate bytes.
fn simAccepts(g: *const grammar.Grammar, st: *const parser.State, side: *parser.Side, bytes: []const u8) bool {
    var sim: parser.State = undefined;
    var work: parser.State = undefined;
    parser.feedBytes(g, side, st, bytes, &sim, &work) catch return false;
    defer parser.releaseState(side, &sim);
    return parser.canEnd(g, &sim);
}

/// Trial variants apply to completable number/string frames: they exist to
/// split comb votes (oneOf{integer, {minimum: 2}} after '2': the minimal
/// completion votes twice, ".5" only on the number branch).
fn hasTrialFrame(g: *const grammar.Grammar, t: *const parser.Thread) bool {
    for (t.frames[0..t.len], 0..) |*f, d| {
        switch (f.*) {
            .str => |fr| if (fr.state == .normal) return true,
            .num_v => |fr| if (trialNumState(fr.st)) return true,
            .num_range => |fr| if (trialNumState(fr.st)) return true,
            .num_excl => |fr| if (trialNumState(fr.st)) return true,
            .num_mult => |fr| if (trialNumState(fr.st)) return true,
            .int_num => |fr| if (trialNumState(fr.st)) return true,
            .not_int_num => |fr| if (trialNumState(fr.st)) return true,
            // A contains deficit with an element in flight gets the
            // optimistic in-flight-matches variant (emitRepeat).
            .repeat => |fr| {
                if (d + 1 < t.len) {
                    const rn = &g.node(fr.node).repeat;
                    if (rn.contains != null and fr.matched < rn.min_contains) return true;
                }
            },
            else => {},
        }
    }
    return false;
}

fn hasKeyStrFrame(t: *const parser.Thread) bool {
    for (t.frames[0..t.len]) |*f| {
        if (f.* == .open_obj and f.open_obj.phase == .key_str) return true;
    }
    return false;
}

fn hasCombFrame(t: *const parser.Thread) bool {
    for (t.frames[0..t.len]) |*f| {
        if (f.* == .comb) return true;
    }
    return false;
}

fn trialNumState(st: parser.NumState) bool {
    return switch (st) {
        .zero_complete, .int_digits, .frac_digits, .dot, .exp, .exp_sign, .exp_digits, .start, .minus => true,
    };
}

/// Trial emission for a number-family frame; consumes the one trial slot
/// of the candidate. Returns false when the trial does not apply here.
/// The dot/exp variants exist to split oneOf{number, integer} votes: the
/// minimal completion stays integral (both branches accept, doomed), while
/// "5" after a dot / "-1" after an exponent marker force a non-integral
/// value (number branch only).
fn numTrial(ctx: *Ctx, st: parser.NumState) bool {
    if (ctx.trial == 0 or ctx.trial_spent) return false;
    switch (st) {
        .zero_complete, .int_digits => {
            ctx.trial_spent = true;
            return appendStr(ctx, if (ctx.trial == 1) ".5" else "e1");
        },
        .start, .minus => {
            if (ctx.trial != 1) return false;
            ctx.trial_spent = true;
            return appendStr(ctx, "0.5");
        },
        .dot => {
            if (ctx.trial != 1) return false;
            ctx.trial_spent = true;
            return appendStr(ctx, "5");
        },
        .frac_digits => {
            ctx.trial_spent = true;
            return appendStr(ctx, if (ctx.trial == 1) "5" else "e1");
        },
        .exp => {
            if (ctx.trial != 1) return false;
            ctx.trial_spent = true;
            return appendStr(ctx, "-1");
        },
        .exp_sign => {
            if (ctx.trial != 1) return false;
            ctx.trial_spent = true;
            return appendStr(ctx, "1");
        },
        .exp_digits => {
            ctx.trial_spent = true;
            return appendStr(ctx, "1");
        },
    }
}

fn synthThread(ctx: *Ctx, t: *const parser.Thread) bool {
    // Value boundary: the deepest open_obj awaiting a value whose machine
    // above is still pristine. Its residue is the whole pending language,
    // but merged allof siblings dispatch different value schemas here, so
    // per-frame emission of one sibling's machine kills the others. Instead
    // the open_obj emission spells a witness of the pending intersection
    // and the walk skips the (byteless for us) frames above.
    var top: usize = t.len;
    var pristine_above = true;
    var noncomb_above = false;
    var i: usize = t.len;
    while (i > 0) {
        i -= 1;
        const f = &t.frames[i];
        // All-comb frames above a .value open_obj mean the value machine
        // has COMPLETED (branch frames popped, combs await the boundary
        // verdict) - nothing to emit there; not a boundary.
        if (f.* == .open_obj and f.open_obj.phase == .value and pristine_above and noncomb_above) {
            ctx.value_boundary = i;
            top = i + 1;
            break;
        }
        if (!parser.framePristine(f)) pristine_above = false;
        if (f.* != .comb) noncomb_above = true;
    }
    ctx.walk_top = top;
    var d: usize = top;
    while (d > 0) {
        d -= 1;
        if (!emitFrame(ctx, t, d, 0)) return false;
        if (ctx.out.items.len > MAX_SYNTH_BYTES) return false;
    }
    return true;
}

fn emitFrame(ctx: *Ctx, t: *const parser.Thread, d: usize, depth: u32) bool {
    if (depth > MAX_EMIT_DEPTH) return false;
    const g = ctx.g;
    const f = &t.frames[d];
    switch (f.*) {
        .comb => return true, // consumes no bytes; votes are sim-verified
        .literal => |fr| {
            const bytes = g.literalBytes(g.node(fr.node).literal);
            return appendStr(ctx, bytes[fr.off..]);
        },
        .lit_trie => |fr| return emitTrieSuffix(ctx, fr.gnode, fr.node),
        .choice => |fr| return emitValue(ctx, fr.node, depth), // undispatched
        .str => |fr| {
            const sn = &g.node(fr.node).str;
            var count = fr.count;
            if (fr.state == .open) {
                if (!appendStr(ctx, "\"")) return false;
            } else {
                const esc = escapeBytes(ctx, fr.state, fr.rem, fr.lo) orelse return false;
                if (esc.len > 0) {
                    if (!appendStr(ctx, esc)) return false;
                    count += 1;
                }
            }
            var pad: u32 = if (sn.min_len > count) sn.min_len - count else 0;
            if (fr.state != .open and ctx.trial != 0 and !ctx.trial_spent) {
                ctx.trial_spent = true;
                pad += 1;
            }
            if (@as(u64, count) + pad > sn.max_len) return false;
            while (pad > 0) : (pad -= 1) if (!appendStr(ctx, "a")) return false;
            return appendStr(ctx, "\"");
        },
        // Mid-string content is not tracked in these frames: pristine only.
        .str_excl => |fr| {
            if (fr.state == .open and fr.count == 0) return emitValue(ctx, fr.node, depth);
            return false;
        },
        .str_pat => |fr| {
            if (fr.state == .open and fr.count == 0) return emitValue(ctx, fr.node, depth);
            return emitPatSuffixMerged(ctx, t, d, &fr);
        },
        .int_v => |fr| {
            switch (fr.st) {
                .start, .minus => return appendStr(ctx, "0"),
                else => return true,
            }
        },
        .num_v => |fr| {
            if (numTrial(ctx, fr.st)) return true;
            return emitNumMinimal(ctx, fr.st);
        },
        .int_num => |fr| return emitIntNum(ctx, &fr),
        .not_int_num => |fr| return emitNotIntNum(ctx, &fr),
        .num_const => |fr| {
            if (fr.st != .start) return false; // mid-value residue: v1 bails
            return emitValue(ctx, fr.node, depth);
        },
        .num_excl => |fr| {
            if (numTrial(ctx, fr.st)) return true;
            return emitNumMinimal(ctx, fr.st);
        },
        .num_range => |fr| {
            if (numTrial(ctx, fr.st)) return true;
            return emitNumMinimal(ctx, fr.st);
        },
        .num_mult => |fr| {
            if (numTrial(ctx, fr.st)) return true;
            return emitNumMult(ctx, &fr);
        },
        .seq => |fr| {
            const children = g.node(fr.node).seq;
            // Quiescent invariant: the frames above are the machine of
            // children[idx]. A top-of-walk seq should not occur; if it
            // does, emit the current child too (the sim filters mistakes).
            const from: usize = if (d + 1 == ctx.walk_top) fr.idx else fr.idx + 1;
            var i: usize = from;
            while (i < children.len) : (i += 1) {
                if (!emitValue(ctx, children[i], depth + 1)) return false;
            }
            return true;
        },
        .object => return emitClosedObjMerged(ctx, t, d, depth),
        .open_obj => return emitOpenObj(ctx, t, d, depth),
        .repeat => return emitRepeat(ctx, &f.repeat, d + 1 < ctx.walk_top, depth),
    }
}

fn emitValue(ctx: *Ctx, node: grammar.NodeId, depth: u32) bool {
    if (depth > MAX_EMIT_DEPTH) return false;
    const v = witset.witness(witset.viewOf(ctx.g), ctx.a, node) orelse return false;
    witset.spell(ctx.out, ctx.a, v) catch return false;
    return true;
}

/// DFS from the current trie node to any terminal, emitting edge bytes.
fn emitTrieSuffix(ctx: *Ctx, gnode: grammar.NodeId, start: u32) bool {
    const tr = &ctx.g.node(gnode).lit_trie;
    return trieDfs(ctx, tr, start, 64);
}

fn trieDfs(ctx: *Ctx, tr: *const grammar.LitTrie, n: u32, depth: u32) bool {
    if (depth == 0) return false;
    if (tr.nodes[n].terminal) return true;
    const base = tr.nodes[n].edge_off;
    for (0..tr.nodes[n].edge_len) |k| {
        const e = tr.edges[base + k];
        if (!appendStr(ctx, &[1]u8{e.byte})) return false;
        if (trieDfs(ctx, tr, e.child, depth - 1)) return true;
        ctx.out.items.len -= 1;
    }
    return false;
}

fn emitNumMinimal(ctx: *Ctx, st: parser.NumState) bool {
    switch (st) {
        .start, .minus => return appendStr(ctx, "0"),
        .dot, .exp, .exp_sign => return appendStr(ctx, "0"),
        else => return true,
    }
}

/// multipleOf mid-value completion. A residue-free prefix closes as-is;
/// otherwise, for integer divisors (div_exp10 <= 0, so the scaled exponent
/// s = tz - div_exp10 is always >= 0), append k = digits10(div) digits
/// spelling x = (-rem * 10^k) mod div, zero-padded to k digits: the value
/// becomes P * 10^k + x == 0 (mod div), an exact multiple (parser's
/// divisibility rule, grammar.NumMult). Fraction/exponent states and
/// divisors with div_exp10 > 0 bail to the search (documented limitation).
fn emitNumMult(ctx: *Ctx, fr: *const parser.NumMultFrame) bool {
    const nm = &ctx.g.node(fr.node).num_mult;
    switch (fr.st) {
        .int_digits => {
            if (fr.rem == 0) return true;
            if (nm.div_exp10 > 0) return false;
            const div: u64 = nm.div;
            var k: u32 = 0;
            var pow: u64 = 1;
            var d10 = div;
            while (d10 > 0) : (d10 /= 10) {
                k += 1;
                pow = @intCast((@as(u128, pow) * 10) % div);
            }
            const rem_part: u64 = @intCast((@as(u128, fr.rem) * pow) % div);
            const x: u64 = if (rem_part == 0) 0 else div - rem_part;
            var buf: [20]u8 = undefined;
            const digits = std.fmt.bufPrint(&buf, "{d}", .{x}) catch return false;
            var pad: u32 = k - @as(u32, @intCast(digits.len));
            while (pad > 0) : (pad -= 1) if (!appendStr(ctx, "0")) return false;
            return appendStr(ctx, digits);
        },
        else => return emitNumMinimal(ctx, fr.st),
    }
}

/// int_num (integer by value): complete so the value is integral. A mid
/// frac with a significant digit needs an exponent rescue (e >= fl - tz).
fn emitIntNum(ctx: *Ctx, fr: *const parser.IntNumFrame) bool {
    if (numTrial(ctx, fr.st)) return true;
    const need: u32 = fr.frac_len -| fr.tz;
    switch (fr.st) {
        .start, .minus => return appendStr(ctx, "0"),
        .zero_complete, .int_digits => return true,
        .dot => return appendStr(ctx, "0"),
        .frac_digits => {
            if (!fr.nz or need == 0) return true;
            if (!appendStr(ctx, "e")) return false;
            return appendUInt(ctx, need);
        },
        .exp => {
            if (fr.exp_neg) return need == 0;
            return appendUInt(ctx, need);
        },
        .exp_sign => {
            if (fr.exp_neg) return need == 0;
            return appendUInt(ctx, need);
        },
        .exp_digits => {
            if (fr.exp_neg) return need == 0;
            var e: u64 = fr.exp;
            var i: u32 = 0;
            while (e < need and i < 12) : (i += 1) {
                if (!appendStr(ctx, "9")) return false;
                e = e * 10 + 9;
            }
            return e >= need;
        },
    }
}

fn emitNotIntNum(ctx: *Ctx, fr: *const parser.IntNumFrame) bool {
    if (numTrial(ctx, fr.st)) return true;
    switch (fr.st) {
        .start, .minus => return appendStr(ctx, "0.5"),
        .zero_complete, .int_digits => return appendStr(ctx, ".5"),
        .dot => return appendStr(ctx, "5"),
        // A nonzero frac digit keeps the value non-integral.
        .frac_digits => return appendStr(ctx, "5"),
        // Exponent already typed: the residue is unclear, bail.
        else => return false,
    }
}

/// Canonically-spelled UTF-8 of one codepoint (control chars and the quote
/// / backslash go through the canonical escape table).
fn appendCp(ctx: *Ctx, cp: u21) bool {
    var buf: [4]u8 = undefined;
    const len = std.unicode.utf8Encode(cp, &buf) catch return false;
    witset.appendEscaped(ctx.out, ctx.a, buf[0..len]) catch return false;
    return true;
}

/// BFS from a mid-string DFA run state to an accept state, returning the
/// witness codepoints (lowest of every taken range). Null when no
/// continuation can match. Levels are bounded by the state count (a
/// shortest path is simple).
fn patBfs(ctx: *Ctx, dfa: *const pattern.Dfa, start: pattern.RunState) ?[]u21 {
    const n: u32 = @intCast(dfa.states.len);
    if (start >= n or n == 0) return null;
    const unvisited = std.math.maxInt(u32);
    const prev = ctx.a.alloc(u32, n) catch return null;
    const via = ctx.a.alloc(u21, n) catch return null;
    @memset(prev, unvisited);
    prev[start] = start;
    var frontier: std.ArrayListUnmanaged(u32) = .{};
    var upcoming: std.ArrayListUnmanaged(u32) = .{};
    frontier.append(ctx.a, start) catch return null;
    var depth: u32 = 0;
    while (frontier.items.len > 0 and depth < n) : (depth += 1) {
        for (frontier.items) |s| {
            for (dfa.states[s].trans) |t| {
                if (prev[t.to] != unvisited) continue;
                if (dfa.dead(t.to)) continue;
                prev[t.to] = s;
                via[t.to] = @intCast(t.lo);
                if (dfa.isAccept(t.to)) {
                    var path: std.ArrayListUnmanaged(u21) = .{};
                    var cur = t.to;
                    while (cur != start) {
                        path.append(ctx.a, via[cur]) catch return null;
                        cur = prev[cur];
                    }
                    std.mem.reverse(u21, path.items);
                    return path.items;
                }
                upcoming.append(ctx.a, t.to) catch return null;
            }
        }
        const tmp = frontier;
        frontier = upcoming;
        upcoming = tmp;
        upcoming.clearRetainingCapacity();
    }
    return null;
}

/// Mid-string str_pat completion: finish any in-flight escape / UTF-8
/// character (feeding its codepoint to the pattern DFA), then drive the
/// DFA to an accept state by BFS, pad to min_len (an accept of an
/// unanchored-search DFA is sticky, so 'a' preserves it) and close the
/// quote. Length-bound overflow or an unreachable accept fails the
/// candidate, never the state: the sim has the final word.
fn emitPatSuffix(ctx: *Ctx, fr: *const parser.StrPatFrame) bool {
    const sn = &ctx.g.node(fr.node).str_pat;
    const dfa = sn.dfa;
    var count: u32 = fr.count;
    var ds = fr.dfa;
    if (fr.state != .normal or fr.rem != 0) {
        const esc = escapeBytes(ctx, fr.state, fr.rem, fr.lo) orelse return false;
        if (esc.len == 0) return false; // .open past the first byte: malformed
        if (!appendStr(ctx, esc)) return false;
        const cp: u21 = escapeCp(fr.state, fr.lo) orelse blk: {
            // Mid-UTF-8: fold the pending partial codepoint with the
            // emitted continuation bytes.
            var v: u32 = (fr.cp << 6) | (fr.lo & 0x3F);
            var r: u8 = fr.rem - 1;
            while (r > 0) : (r -= 1) v <<= 6;
            break :blk @intCast(v);
        };
        ds = dfa.feed(ds, cp);
        if (dfa.dead(ds)) return false;
        count += 1;
    }
    const min_pad: u32 = if (sn.min_len > count) sn.min_len - count else 0;
    if (dfa.isAccept(ds)) {
        if (@as(u64, count) + min_pad > sn.max_len) return false;
        var pad = min_pad;
        while (pad > 0) : (pad -= 1) if (!appendStr(ctx, "a")) return false;
        return appendStr(ctx, "\"");
    }
    const path = patBfs(ctx, dfa, ds) orelse return false;
    const add: u32 = @max(@as(u32, @intCast(path.len)), min_pad);
    if (@as(u64, count) + add > sn.max_len) return false;
    for (path) |cp| if (!appendCp(ctx, cp)) return false;
    var pad: u32 = add - @as(u32, @intCast(path.len));
    while (pad > 0) : (pad -= 1) if (!appendStr(ctx, "a")) return false;
    return appendStr(ctx, "\"");
}

const MAX_PAT_SET = 6;
const MAX_PAT_PRODUCT = 4096;

/// The comb instance whose branch the frame at index `fi` belongs to: the
/// nearest comb frame below it on the thread, returned only when that comb
/// is an allOf (the only verdict whose branches must be completed jointly).
fn innerAllOfInst(g: *const grammar.Grammar, t: *const parser.Thread, fi: usize) ?u32 {
    var i = fi;
    while (i > 0) {
        i -= 1;
        const f = t.frames[i];
        if (f == .comb) {
            if (g.node(f.comb.node).comb.kind == .allof) return f.comb.inst;
            return null;
        }
    }
    return null;
}

/// Mid-string str_pat completion shared across sibling threads: when an
/// allOf keeps several pattern machines alive on the SAME string, a
/// per-frame BFS suffix satisfies one pattern and dooms the rest, so the
/// emission must drive every live DFA to a joint accept. Siblings are the
/// in-flight pattern machines of the SAME allOf instance at the same string
/// position (same count, clean content state): frames coupled by a oneOf /
/// anyOf verdict or by no comb at all must NOT be completed jointly (a joint
/// suffix ties the oneOf vote, and independent forks are alternatives), so
/// those fall back to the per-frame path. The product DFA is BFS'd with
/// candidate codepoints taken from the transition-range lows of all
/// components (the minimal element of every joint-feasible interval is one
/// of them), capped at MAX_PAT_PRODUCT states. Larger products, pending
/// escapes, and length-bound mismatches bail to the per-frame path, which
/// fails the candidate, never the state: the sim has the final word.
fn emitPatSuffixMerged(ctx: *Ctx, t: *const parser.Thread, d: usize, fr: *const parser.StrPatFrame) bool {
    const inner = innerAllOfInst(ctx.g, t, d);
    var runs: [MAX_PAT_SET]pattern.RunState = undefined;
    var dfas: [MAX_PAT_SET]*const pattern.Dfa = undefined;
    var min_lens: [MAX_PAT_SET]u32 = undefined;
    var max_lens: [MAX_PAT_SET]u32 = undefined;
    var n: usize = 1;
    runs[0] = fr.dfa;
    dfas[0] = ctx.g.node(fr.node).str_pat.dfa;
    min_lens[0] = ctx.g.node(fr.node).str_pat.min_len;
    max_lens[0] = ctx.g.node(fr.node).str_pat.max_len;
    for (ctx.st.threads[0..ctx.st.n]) |*t2| {
        if (t2 == t) continue;
        for (t2.frames[0..t2.len], 0..) |*f2, fi2| {
            if (f2.* != .str_pat) continue;
            const s2 = &f2.str_pat;
            if (s2.state == .open) continue;
            if (s2.count != fr.count) continue;
            // A pending escape / partial character completes with the same
            // bytes for every sibling only if their machines agree exactly.
            if (s2.state != fr.state or s2.rem != fr.rem or s2.lo != fr.lo or s2.cp != fr.cp) continue;
            const inst2 = innerAllOfInst(ctx.g, t2, fi2) orelse continue;
            if (inner == null or inst2 != inner.?) continue;
            if (n >= MAX_PAT_SET) return emitPatSuffix(ctx, fr);
            const sn2 = &ctx.g.node(s2.node).str_pat;
            runs[n] = s2.dfa;
            dfas[n] = sn2.dfa;
            min_lens[n] = sn2.min_len;
            max_lens[n] = sn2.max_len;
            n += 1;
        }
    }
    if (n == 1) return emitPatSuffix(ctx, fr);
    var count: u32 = fr.count;
    // Finish an in-flight escape / UTF-8 character, feeding its codepoint
    // to every sibling DFA.
    if (fr.state != .normal or fr.rem != 0) {
        const esc = escapeBytes(ctx, fr.state, fr.rem, fr.lo) orelse return false;
        if (esc.len == 0) return false;
        if (!appendStr(ctx, esc)) return false;
        const cp: u21 = escapeCp(fr.state, fr.lo) orelse blk: {
            var v: u32 = (fr.cp << 6) | (fr.lo & 0x3F);
            var r: u8 = fr.rem - 1;
            while (r > 0) : (r -= 1) v <<= 6;
            break :blk @intCast(v);
        };
        for (0..n) |i| {
            runs[i] = dfas[i].feed(runs[i], cp);
            if (dfas[i].dead(runs[i])) return false;
        }
        count += 1;
    }
    var radix: [MAX_PAT_SET]u64 = undefined;
    var product: u64 = 1;
    for (0..n) |i| {
        radix[i] = product;
        product *= dfas[i].stateCount();
        if (product > MAX_PAT_PRODUCT) return emitPatSuffix(ctx, fr);
    }
    const unvisited = std.math.maxInt(u32);
    const prev = ctx.a.alloc(u32, @intCast(product)) catch return false;
    const via = ctx.a.alloc(u21, @intCast(product)) catch return false;
    @memset(prev, unvisited);
    var start_idx: u64 = 0;
    for (0..n) |i| start_idx += runs[i] * radix[i];
    var frontier: std.ArrayListUnmanaged(u32) = .{};
    var upcoming: std.ArrayListUnmanaged(u32) = .{};
    frontier.append(ctx.a, @intCast(start_idx)) catch return false;
    prev[@intCast(start_idx)] = @intCast(start_idx);
    var depth: u32 = 0;
    var goal: u32 = unvisited;
    while (frontier.items.len > 0 and depth < product) : (depth += 1) {
        for (frontier.items) |idx| {
            // Decode the tuple.
            var comp: [MAX_PAT_SET]u32 = undefined;
            var rest: u64 = idx;
            var ci: usize = n;
            while (ci > 0) {
                ci -= 1;
                comp[ci] = @intCast(rest / radix[ci]);
                rest %= radix[ci];
            }
            var all_accept = true;
            for (0..n) |i| {
                if (!dfas[i].isAccept(comp[i])) {
                    all_accept = false;
                    break;
                }
            }
            if (all_accept) {
                goal = idx;
                break;
            }
            // Candidate codepoints: the lows of every component transition.
            var cps: [64]u21 = undefined;
            var ncp: usize = 0;
            for (0..n) |i| {
                for (dfas[i].states[comp[i]].trans) |tr| {
                    if (tr.lo > 0x10FFFF) continue;
                    const c: u21 = @intCast(tr.lo);
                    var seen_cp = false;
                    for (cps[0..ncp]) |c2| {
                        if (c2 == c) {
                            seen_cp = true;
                            break;
                        }
                    }
                    if (seen_cp) continue;
                    if (ncp >= cps.len) continue;
                    cps[ncp] = c;
                    ncp += 1;
                }
            }
            for (cps[0..ncp]) |c| {
                var nidx: u64 = 0;
                var ok = true;
                for (0..n) |i| {
                    const ns = dfas[i].feed(comp[i], c);
                    if (dfas[i].dead(ns)) {
                        ok = false;
                        break;
                    }
                    nidx += ns * radix[i];
                }
                if (!ok) continue;
                if (prev[@intCast(nidx)] != unvisited) continue;
                prev[@intCast(nidx)] = idx;
                via[@intCast(nidx)] = c;
                upcoming.append(ctx.a, @intCast(nidx)) catch return false;
            }
        }
        if (goal != unvisited) break;
        const tmp = frontier;
        frontier = upcoming;
        upcoming = tmp;
        upcoming.clearRetainingCapacity();
    }
    if (goal == unvisited) return emitPatSuffix(ctx, fr);
    // Reconstruct (reversed).
    var path: std.ArrayListUnmanaged(u21) = .{};
    var cur = goal;
    while (cur != @as(u32, @intCast(start_idx))) {
        path.append(ctx.a, via[cur]) catch return false;
        cur = prev[cur];
    }
    std.mem.reverse(u21, path.items);
    var min_pad: u32 = 0;
    for (0..n) |i| {
        if (min_lens[i] > count) min_pad = @max(min_pad, min_lens[i] - count);
    }
    const add: u32 = @max(@as(u32, @intCast(path.items.len)), min_pad);
    for (0..n) |i| {
        if (@as(u64, count) + add > max_lens[i]) return false;
    }
    for (path.items) |cp| if (!appendCp(ctx, cp)) return false;
    var pad: u32 = add - @as(u32, @intCast(path.items.len));
    while (pad > 0) : (pad -= 1) if (!appendStr(ctx, "a")) return false;
    return appendStr(ctx, "\"");
}

fn appendStr(ctx: *Ctx, s: []const u8) bool {
    ctx.out.appendSlice(ctx.a, s) catch return false;
    return true;
}

fn appendUInt(ctx: *Ctx, x: u32) bool {
    var buf: [16]u8 = undefined;
    const s = std.fmt.bufPrint(&buf, "{d}", .{x}) catch return false;
    return appendStr(ctx, s);
}

// ---- closed objects --------------------------------------------------------

fn emitClosedObj(ctx: *Ctx, fr: anytype, depth: u32) bool {
    const g = ctx.g;
    const on = &g.node(fr.node).object;
    switch (fr.phase) {
        .open => return emitValue(ctx, fr.node, depth),
        .key => {
            if (!emitClosedProps(ctx, on, fr.idx, false, depth)) return false;
            return appendStr(ctx, "}");
        },
        .key_after_comma => {
            // A key is mandatory here. Emit the remaining required props;
            // if none, the first optional one; if none at all, dead.
            const had = !allOptionalFrom(on.props, fr.idx);
            if (!emitClosedProps(ctx, on, fr.idx, false, depth)) return false;
            if (!had) {
                if (fr.idx >= on.props.len) return false;
                const kl = g.literalBytes(on.props[fr.idx].key);
                if (!appendStr(ctx, kl)) return false;
                if (!emitValue(ctx, on.props[fr.idx].value, depth + 1)) return false;
            }
            return appendStr(ctx, "}");
        },
        .key_lit => {
            // idx is the byte offset into the `"name":` literal of cur.
            const kl = g.literalBytes(on.props[fr.cur].key);
            if (!appendStr(ctx, kl[fr.idx..])) return false;
            if (!emitValue(ctx, on.props[fr.cur].value, depth + 1)) return false;
            if (!emitClosedProps(ctx, on, fr.cur + 1, true, depth)) return false;
            return appendStr(ctx, "}");
        },
        .value => {
            // The value frames above were already emitted; idx is restored
            // to cur+1 on entering sep.
            if (!emitClosedProps(ctx, on, fr.cur + 1, true, depth)) return false;
            return appendStr(ctx, "}");
        },
        .sep => {
            if (!emitClosedProps(ctx, on, fr.idx, true, depth)) return false;
            return appendStr(ctx, "}");
        },
    }
}

fn allOptionalFrom(props: []const grammar.Prop, idx: u32) bool {
    for (props[@min(idx, @as(u32, @intCast(props.len)))..]) |p| {
        if (p.required) return false;
    }
    return true;
}

/// Emit the remaining required props of a closed object, each preceded by
/// ',' when lead_comma or not first. Prop key literals carry the `"name":`
/// spelling including the colon.
fn emitClosedProps(ctx: *Ctx, on: *const grammar.ObjectNode, from: u32, lead_comma: bool, depth: u32) bool {
    var first = true;
    for (on.props[@min(from, @as(u32, @intCast(on.props.len)))..]) |p| {
        if (!p.required) continue;
        if (!first or lead_comma) if (!appendStr(ctx, ",")) return false;
        first = false;
        if (!appendStr(ctx, ctx.g.literalBytes(p.key))) return false;
        if (!emitValue(ctx, p.value, depth + 1)) return false;
    }
    return true;
}

fn objDeclaredIdx(g: *const grammar.Grammar, on: *const grammar.ObjectNode, name: []const u8) ?u32 {
    for (on.props, 0..) |p, i| {
        if (std.mem.eql(u8, propName(g, p), name)) return @intCast(i);
    }
    return null;
}

/// Mid-flight closed object directly above a comb chain: allof sibling
/// facets at the same value position run on sibling threads, and their
/// remaining obligations are obligations of THIS object too (a closed
/// object rejects undeclared keys, so a per-facet completion kills the
/// siblings). Merge them: emit the in-flight key (fresh body) or continue
/// after the in-flight value, then the union of every facet's remaining
/// required props, provided every emitted key is declared in and
/// order-compatible with every closed facet and dispatchable on every
/// open_obj facet. Sibling facets may be closed objects or open_objs; a
/// oneOf-nested sibling contributes its first live alternative (the sim
/// checks the others reject). Shapes outside the covered phases fall back
/// to per-node emission, and any incompatibility fails the candidate - the
/// sim would reject the bytes anyway.
fn emitClosedObjMerged(ctx: *Ctx, t: *const parser.Thread, d: usize, depth: u32) bool {
    const fr = &t.frames[d].object;
    var self_from: u32 = 0;
    var first_key = false;
    var lead_comma = false;
    switch (fr.phase) {
        .open => return emitClosedObj(ctx, fr, depth),
        .key => {
            if (fr.idx != 0) return emitClosedObj(ctx, fr, depth);
            first_key = true;
        },
        .key_lit => {
            if (fr.idx == 0) return emitClosedObj(ctx, fr, depth);
            first_key = true;
        },
        .key_after_comma => self_from = fr.idx,
        .value => {
            self_from = fr.cur + 1;
            lead_comma = true;
        },
        .sep => {
            self_from = fr.idx;
            lead_comma = true;
        },
    }
    if (d == 0 or t.frames[d - 1] != .comb)
        return emitClosedObj(ctx, fr, depth);
    const g = ctx.g;
    const on0 = &g.node(fr.node).object;
    const kl0 = g.literalBytes(on0.props[fr.cur].key);
    const name0 = propName(g, on0.props[fr.cur]);
    var nodes: [MAX_SET]grammar.NodeId = undefined; // closed facets
    var froms: [MAX_SET]u32 = undefined;
    nodes[0] = fr.node;
    froms[0] = self_from;
    var nn: usize = 1;
    var openrefs: std.ArrayListUnmanaged(ObjRef) = .{};
    var visited: [MAX_SET]u32 = undefined;
    var nv: usize = 0;
    var j = d;
    while (j > 0 and t.frames[j - 1] == .comb) {
        j -= 1;
        const cf = &t.frames[j].comb;
        const cn = &g.node(cf.node).comb;
        switch (cn.kind) {
            .ifelse => return false,
            .oneof => continue,
            .allof => {},
        }
        var known = false;
        for (visited[0..nv]) |vi| {
            if (vi == cf.inst) {
                known = true;
                break;
            }
        }
        if (known) continue;
        visited[nv] = cf.inst;
        nv += 1;
        for (cn.branches, 0..) |_, bi| {
            if (cn.branches[bi] == grammar.COMB_NONE or bi == cf.branch) continue;
            // Live alternatives of the sibling branch (a oneOf nested under
            // it spawns one thread per sub-branch); ctx.alt picks whose
            // obligations merge, the sim checks the others reject.
            var alts: [MAX_SET]*const parser.Frame = undefined;
            var na: usize = 0;
            for (ctx.st.threads[0..ctx.st.n]) |*t2| {
                const cidx = combIdxIn(t2, cf.inst) orelse continue;
                if (t2.frames[cidx].comb.branch != bi) continue;
                var k = cidx + 1;
                while (k < t2.len and t2.frames[k] == .comb) k += 1;
                if (k >= t2.len) continue;
                const f2 = &t2.frames[k];
                if (f2.* != .object and f2.* != .open_obj) continue;
                const id = frameNode(f2);
                var dup = false;
                for (alts[0..na]) |af| {
                    if (frameNode(af) == id) {
                        dup = true;
                        break;
                    }
                }
                if (dup) continue;
                if (na >= MAX_SET) break;
                alts[na] = f2;
                na += 1;
            }
            if (na == 0) return false; // dead sibling: the group is doomed
            if (na > 1) ctx.alt_seen = true;
            if (ctx.alt >= na) return false;
            const f2 = alts[ctx.alt];
            switch (f2.*) {
                .object => |*of2| {
                    const from2: u32 = switch (of2.phase) {
                        .key => blk: {
                            if (!first_key or of2.idx != 0) return false;
                            break :blk 0;
                        },
                        .key_lit => blk: {
                            if (!first_key or of2.idx == 0) return false;
                            break :blk 0;
                        },
                        .key_after_comma => blk: {
                            if (fr.phase != .key_after_comma) return false;
                            break :blk of2.idx;
                        },
                        .value => blk: {
                            if (fr.phase != .value) return false;
                            const on2 = &g.node(of2.node).object;
                            if (!std.mem.eql(u8, propName(g, on2.props[of2.cur]), name0)) return false;
                            break :blk of2.cur + 1;
                        },
                        .sep => blk: {
                            if (fr.phase != .sep) return false;
                            break :blk of2.idx;
                        },
                        .open => return false,
                    };
                    var dupn = false;
                    for (nodes[0..nn]) |ex| {
                        if (ex == of2.node) {
                            dupn = true;
                            break;
                        }
                    }
                    if (!dupn) {
                        if (nn >= MAX_SET) return false;
                        nodes[nn] = of2.node;
                        froms[nn] = from2;
                        nn += 1;
                    }
                },
                .open_obj => |*oo2| {
                    const okp = switch (oo2.phase) {
                        .key => fr.phase == .key,
                        .key_str => blk: {
                            if (fr.phase != .key_lit) break :blk false;
                            const kb = ctx.side.chunkAt(oo2.key).payload.items;
                            break :blk kb.len <= name0.len and std.mem.eql(u8, name0[0..kb.len], kb);
                        },
                        .key_after_comma => fr.phase == .key_after_comma,
                        // Sibling completed the key; self is mid-literal
                        // awaiting the colon of the same key.
                        .colon => fr.phase == .key_lit,
                        .value => fr.phase == .value,
                        .sep => fr.phase == .sep,
                        else => false,
                    };
                    if (!okp) return false;
                    var dupn = false;
                    for (openrefs.items) |ex| {
                        if (ex.node == oo2.node) {
                            dupn = true;
                            break;
                        }
                    }
                    if (!dupn) openrefs.append(ctx.a, objRefOf(oo2)) catch return false;
                },
                else => return false,
            }
        }
    }
    if (nn == 1 and openrefs.items.len == 0) return emitClosedObj(ctx, fr, depth);
    // Emitted key sequence: the in-flight key (fresh body), then the union
    // of every facet's remaining obligations.
    var seq: [MAX_OBL][]const u8 = undefined;
    var ns: usize = 0;
    if (first_key) {
        seq[0] = name0;
        ns = 1;
    }
    for (nodes[0..nn], 0..) |nd, ni| {
        const ond = &g.node(nd).object;
        for (ond.props[@min(froms[ni], @as(u32, @intCast(ond.props.len)))..]) |p| {
            if (!p.required) continue;
            const nm = propName(g, p);
            if (nameIn(seq[0..ns], nm)) continue;
            if (ns >= MAX_OBL) return false;
            seq[ns] = nm;
            ns += 1;
        }
    }
    var onames: std.ArrayListUnmanaged([]const u8) = .{};
    if (openrefs.items.len > 0) {
        if (!collectObligations(ctx, openrefs.items, &.{}, &onames)) return false;
        for (onames.items) |nm| {
            if (nameIn(seq[0..ns], nm)) continue;
            if (ns >= MAX_OBL) return false;
            seq[ns] = nm;
            ns += 1;
        }
    }
    if (ns == 0) return emitClosedObj(ctx, fr, depth);
    // Every emitted key must be declared in every closed facet, with
    // positions increasing past the in-flight key; every open_obj facet
    // must dispatch it.
    for (nodes[0..nn], 0..) |nd, ni| {
        const ond = &g.node(nd).object;
        var have_prev = froms[ni] > 0;
        var prev: u32 = froms[ni] -| 1;
        for (seq[0..ns]) |nm| {
            const pi = objDeclaredIdx(g, ond, nm) orelse return false;
            if (have_prev and pi <= prev) return false;
            prev = pi;
            have_prev = true;
        }
    }
    // Spell: in-flight literal continuation (fresh body), then `"name":`
    // per key with ',' separators, each value a witness of the per-facet
    // value-schema intersection.
    if (first_key) {
        const kl_tail: []const u8 = if (fr.phase == .key_lit) kl0[fr.idx..] else kl0;
        if (!appendStr(ctx, kl_tail)) return false;
    }
    for (seq[0..ns], 0..) |nm, si| {
        if (!first_key) {
            if (si > 0 or lead_comma) if (!appendStr(ctx, ",")) return false;
            if (!appendStr(ctx, "\"")) return false;
            if (!appendStr(ctx, nm)) return false;
            if (!appendStr(ctx, "\":")) return false;
        } else if (si > 0) {
            if (!appendStr(ctx, ",\"")) return false;
            if (!appendStr(ctx, nm)) return false;
            if (!appendStr(ctx, "\":")) return false;
        }
        var schemas: [MAX_SET * 2]grammar.NodeId = undefined;
        var nsc: usize = 0;
        for (nodes[0..nn]) |nd| {
            const ond = &g.node(nd).object;
            schemas[nsc] = ond.props[objDeclaredIdx(g, ond, nm).?].value;
            nsc += 1;
        }
        for (openrefs.items) |orf| {
            const on = &g.node(orf.node).open_obj;
            const s = if (declaredIdx(g, on, nm)) |pi| on.props[pi].value else (dispatchUndeclared(ctx, on, nm) orelse return false);
            if (nsc >= schemas.len) return false;
            schemas[nsc] = s;
            nsc += 1;
        }
        const v = intersectWitness(ctx, schemas[0..nsc]) orelse return false;
        witset.spell(ctx.out, ctx.a, v) catch return false;
    }
    return appendStr(ctx, "}");
}

// ---- arrays ----------------------------------------------------------------

/// in_flight: an element machine sits above the frame (the walk emitted
/// it) - it counts toward min/max and separates any further element with a
/// comma. After a comma with no element started yet (repeat on top), one
/// element is mandatory and needs no separator.
fn emitRepeat(ctx: *Ctx, fr: anytype, in_flight: bool, depth: u32) bool {
    const g = ctx.g;
    const rn = &g.node(fr.node).repeat;
    switch (fr.phase) {
        .open => return emitValue(ctx, fr.node, depth),
        .body, .body_after_comma, .sep => {
            const pending: u32 = if (in_flight) 1 else 0;
            var emitted: u32 = 0;
            var matched_extra: u32 = 0;
            // Trial variant: the in-flight element matches contains (the
            // sim judges). Without it a contains deficit always owes one
            // more element, which is wrong when the in-flight one covers
            // it - and impossible when max leaves no room.
            if (in_flight and rn.contains != null and fr.matched < rn.min_contains and
                ctx.trial != 0 and !ctx.trial_spent)
            {
                ctx.trial_spent = true;
                matched_extra = 1;
            }
            while (true) {
                const total = fr.count + pending + emitted;
                const need_count = total < rn.min;
                const need_contains = rn.contains != null and fr.matched + matched_extra < rn.min_contains;
                // After a comma an element is mandatory.
                const forced = emitted == 0 and fr.phase == .body_after_comma and !in_flight;
                if (!need_count and !need_contains and !forced) break;
                if (total >= rn.max) return false;
                if (emitted > 0 or fr.phase == .sep or in_flight) {
                    if (!appendStr(ctx, ",")) return false;
                }
                const schema = if (total < rn.prefix.len) rn.prefix[total] else rn.item;
                if (need_contains) {
                    // The element must satisfy the item schema AND match
                    // contains; the sim verifies both (and max_contains).
                    const v = intersectWitness(ctx, &.{ schema, rn.contains.? }) orelse return false;
                    witset.spell(ctx.out, ctx.a, v) catch return false;
                    matched_extra += 1;
                } else {
                    if (!emitValue(ctx, schema, depth + 1)) return false;
                }
                emitted += 1;
            }
            return appendStr(ctx, "]");
        },
    }
}

// ---- seen-set helpers (chunk format: u32 count, then [u32 len, bytes]*) ----

fn seenContains(side: *const parser.Side, h: u32, key: []const u8) bool {
    if (h == 0) return false;
    const items = side.chunkAt(h).payload.items;
    if (items.len < 4) return false;
    const count = std.mem.readInt(u32, items[0..4], .little);
    var off: usize = 4;
    var i: u32 = 0;
    while (i < count) : (i += 1) {
        if (off + 4 > items.len) return false;
        const len = std.mem.readInt(u32, items[off..][0..4], .little);
        off += 4;
        if (off + len > items.len) return false;
        if (len == key.len and std.mem.eql(u8, items[off .. off + len], key)) return true;
        off += len;
    }
    return false;
}

fn seenCount(side: *const parser.Side, h: u32) u32 {
    if (h == 0) return 0;
    const items = side.chunkAt(h).payload.items;
    if (items.len < 4) return 0;
    return std.mem.readInt(u32, items[0..4], .little);
}

// ---- open objects ----------------------------------------------------------

/// Snapshot of an open_obj frame's emission-relevant fields.
const ObjRef = struct {
    node: grammar.NodeId,
    next_idx: u32,
    seen: u32,
    phase: parser.OpenObjPhase,
    key: u32, // chunk handle of the in-flight key (key_str phase)
    key_st: parser.StrState, // key string machine state (key_str phase)
    key_rem: u8, // continuation bytes left on a mid-UTF-8 key char
    key_lo: u8, // next-byte range: UTF-8 continuation or uhex2 high nibble
    key_hi: u8,
    // Dispatched value node. u.pending is the active union field from key
    // completion through colon/value/sep (rewritten only on key_str entry),
    // so it still names the in-flight value schema at .value.
    pending: grammar.NodeId,
    // A schema-kind dependency facet (dependentSchemas in force): not a
    // real frame, so it has no in-flight value; the pending-based
    // boundary/colon intersections skip it (the sim's close-time dep
    // reparse judges the in-flight value instead).
    virtual: bool = false,
};

/// Snapshot of a closed-object sibling facet merged into an open_obj
/// emission: remaining required props start at `from`; when the facet is
/// mid-value (inflight_value), `cur` names the in-flight key whose value
/// schema also constrains a boundary/colon intersection.
const ClosedRef = struct {
    node: grammar.NodeId,
    from: u32,
    cur: u32,
    inflight_value: bool,
};

fn objRefOf(f: *const parser.OpenObjFrame) ObjRef {
    return .{
        .node = f.node,
        .next_idx = f.next_idx,
        .seen = f.seen,
        .phase = f.phase,
        // The key chunk lives through the key's value (parser dispatch no
        // longer releases it), so colon/value/sep can still name the
        // in-flight key.
        .key = f.key,
        .key_st = if (f.phase == .key_str) f.u.key.st else .normal,
        .key_rem = if (f.phase == .key_str) f.u.key.rem else 0,
        .key_lo = if (f.phase == .key_str) f.u.key.lo else 0,
        .key_hi = if (f.phase == .key_str) f.u.key.hi else 0,
        .pending = switch (f.phase) {
            .colon, .value, .sep => f.u.pending,
            else => 0,
        },
    };
}

/// The grammar node a frame's machine parses (lit_trie's node field is the
/// trie cursor; the grammar node is gnode).
fn frameNode(f: *const parser.Frame) grammar.NodeId {
    return switch (f.*) {
        .lit_trie => |fr| fr.gnode,
        inline else => |fr| fr.node,
    };
}

/// Comb-group sibling constraints at a value boundary: the emitted value is
/// parsed in parallel by every live thread sharing a comb instance with the
/// synthesizing thread (an allOf sibling facet must accept the same bytes,
/// else the group dooms the whole state). Each such thread contributes the
/// root node of its pristine top run - a comb frame contributes its group
/// node, whose static acceptance is exactly the group verdict. A sibling
/// mid-value (no pristine run) has a residue no static node describes, so
/// the candidate fails instead of guessing. oneOf sibling branches that
/// also accept are not filtered here; the sim rejects the over-vote.
fn groupSiblingConstraints(ctx: *Ctx, t: *const parser.Thread, out: *[MAX_GROUP]grammar.NodeId) ?usize {
    var n: usize = 0;
    var has_comb = false;
    for (t.frames[0..t.len]) |*f| {
        if (f.* == .comb) has_comb = true;
    }
    if (!has_comb) return 0;
    for (ctx.st.threads[0..ctx.st.n]) |*t2| {
        if (t2 == t or t2.len == 0) continue;
        var shares = false;
        for (t2.frames[0..t2.len]) |*f2| {
            if (f2.* != .comb) continue;
            for (t.frames[0..t.len]) |*ft| {
                if (ft.* == .comb and ft.comb.inst == f2.comb.inst) {
                    shares = true;
                    break;
                }
            }
            if (shares) break;
        }
        if (!shares) continue;
        var k: usize = t2.len;
        while (k > 0 and parser.framePristine(&t2.frames[k - 1])) k -= 1;
        if (k == t2.len) return null; // mid-value group sibling
        const node = frameNode(&t2.frames[k]);
        var dup = false;
        for (out[0..n]) |e| {
            if (e == node) dup = true;
        }
        if (dup) continue;
        if (n >= MAX_GROUP) return null;
        out[n] = node;
        n += 1;
    }
    return n;
}

fn findBranchThread(st: *const parser.State, inst: u32, branch: u8) ?*const parser.Thread {
    for (st.threads[0..st.n]) |*t| {
        for (t.frames[0..t.len]) |*f| {
            if (f.* == .comb and f.comb.inst == inst and f.comb.branch == branch) return t;
        }
    }
    return null;
}

fn combIdxIn(t: *const parser.Thread, inst: u32) ?usize {
    for (t.frames[0..t.len], 0..) |*f, i| {
        if (f.* == .comb and f.comb.inst == inst) return i;
    }
    return null;
}

/// Collect the open_obj frames whose obligations a completion of the leaf
/// value at (t, d) must satisfy: t's own frame plus, walking down the
/// contiguous comb chain below d, every allof sibling branch's leaf object
/// (recursively through nested comb chains). oneOf siblings must REJECT
/// the value, so they are excluded (the sim checks that). Returns false on
/// any shape outside the covered family (non-object leaves, ifelse combs,
/// phase misalignment). At a value boundary (boundary=true, phase .value)
/// every collected frame's value machine above must be pristine: the
/// boundary emission spells the whole pending value, so any typed value
/// byte would falsify it.
fn collectObjSet(ctx: *Ctx, t: *const parser.Thread, d: usize, lo: usize, boundary: bool, list: *std.ArrayListUnmanaged(ObjRef), closed: *std.ArrayListUnmanaged(ClosedRef), visited: *std.ArrayListUnmanaged(u32)) bool {
    if (list.items.len >= MAX_SET) return false;
    const f = &t.frames[d];
    if (f.* != .open_obj) return false;
    const of = &f.open_obj;
    if (of.phase == .open) return false; // pristine handled by emitValue
    if (list.items.len > 0 and of.phase != list.items[0].phase) return false;
    if (boundary) {
        var k: usize = d + 1;
        while (k < t.len) : (k += 1) {
            if (!parser.framePristine(&t.frames[k])) return false;
        }
    }
    list.append(ctx.a, objRefOf(of)) catch return false;
    var j: usize = d;
    while (j > lo) {
        const cf = &t.frames[j - 1];
        if (cf.* != .comb) break;
        j -= 1;
        const inst = cf.comb.inst;
        var known = false;
        for (visited.items) |vi| {
            if (vi == inst) {
                known = true;
                break;
            }
        }
        if (known) continue;
        visited.append(ctx.a, inst) catch return false;
        const cn = &ctx.g.node(cf.comb.node).comb;
        switch (cn.kind) {
            .oneof => {},
            .ifelse => return false,
            .allof => {
                for (cn.branches, 0..) |br, bi| {
                    if (br == grammar.COMB_NONE) continue;
                    if (bi == cf.comb.branch) continue;
                    const t2 = findBranchThread(ctx.st, inst, @intCast(bi)) orelse return false;
                    const cidx = combIdxIn(t2, inst) orelse return false;
                    var k = cidx + 1;
                    while (k < t2.len and t2.frames[k] == .comb) k += 1;
                    if (k >= t2.len) return false; // branch completed early
                    const f2 = &t2.frames[k];
                    if (f2.* == .object) {
                        // Closed facet at the same value position: its
                        // remaining required props are obligations and its
                        // declared-key order constrains the emission.
                        if (boundary) {
                            var kk: usize = k + 1;
                            while (kk < t2.len) : (kk += 1) {
                                if (!parser.framePristine(&t2.frames[kk])) return false;
                            }
                        }
                        const of2 = &f2.object;
                        var from2: u32 = 0;
                        var cur2: u32 = 0;
                        var inflight = false;
                        switch (list.items[0].phase) {
                            .key, .key_after_comma => {
                                if (of2.phase != .key and of2.phase != .key_after_comma) return false;
                                from2 = of2.idx;
                            },
                            .key_str => {
                                if (of2.phase != .key_lit) return false;
                                from2 = 0;
                            },
                            .colon, .value => {
                                if (of2.phase != .value) return false;
                                from2 = of2.cur + 1;
                                cur2 = of2.cur;
                                inflight = true;
                            },
                            .sep => {
                                if (of2.phase == .sep) {
                                    from2 = of2.idx;
                                } else if (of2.phase == .value) {
                                    // Value complete, verdict pending: the
                                    // frames above are all combs.
                                    var kk: usize = k + 1;
                                    while (kk < t2.len) : (kk += 1) {
                                        if (t2.frames[kk] != .comb) return false;
                                    }
                                    from2 = of2.cur + 1;
                                } else return false;
                            },
                            else => return false,
                        }
                        var dupn = false;
                        for (closed.items) |ex| {
                            if (ex.node == of2.node) {
                                dupn = true;
                                break;
                            }
                        }
                        if (!dupn) closed.append(ctx.a, .{ .node = of2.node, .from = from2, .cur = cur2, .inflight_value = inflight }) catch return false;
                        continue;
                    }
                    if (!collectObjSet(ctx, t2, k, cidx + 1, boundary, list, closed, visited)) return false;
                }
            },
        }
    }
    return true;
}

fn propName(g: *const grammar.Grammar, p: grammar.Prop) []const u8 {
    const kl = g.literalBytes(p.key); // `"name":` form
    return kl[1 .. kl.len - 2];
}

fn declaredIdx(g: *const grammar.Grammar, on: *const grammar.OpenObjNode, name: []const u8) ?usize {
    for (on.props, 0..) |p, i| {
        if (std.mem.eql(u8, propName(g, p), name)) return i;
    }
    return null;
}

fn nameIn(names: []const []const u8, nm: []const u8) bool {
    for (names) |x| {
        if (std.mem.eql(u8, x, nm)) return true;
    }
    return false;
}

fn addName(ctx: *Ctx, names: *std.ArrayListUnmanaged([]const u8), nm: []const u8) bool {
    if (nameIn(names.items, nm)) return true;
    if (names.items.len >= MAX_OBL) return false;
    names.append(ctx.a, nm) catch return false;
    return true;
}

/// Gather the obligation keys of every frame in the set: remaining required
/// declared props, unseen extra_required, and required-dep names (to a
/// fixpoint over dep triggers present or planned), plus the remaining
/// required props of merged closed-object facets from their `from` index.
/// Ban deps in force bail the candidate. Schema-kind deps are merged as
/// virtual facets by emitOpenObj before this runs (any residual .schema
/// obligation a candidate misses is caught by the sim's close-time dep
/// reparse, failing the candidate, never the state).
fn collectObligations(ctx: *Ctx, set: []const ObjRef, closed: []const ClosedRef, names: *std.ArrayListUnmanaged([]const u8)) bool {
    for (set) |fr| {
        const on = &ctx.g.node(fr.node).open_obj;
        for (on.props[@min(fr.next_idx, @as(u32, @intCast(on.props.len)))..]) |p| {
            if (!p.required) continue;
            if (seenContains(ctx.side, fr.seen, propName(ctx.g, p))) continue;
            if (!addName(ctx, names, propName(ctx.g, p))) return false;
        }
        for (on.extra_required) |lit| {
            const nm = ctx.g.literalBytes(lit);
            if (seenContains(ctx.side, fr.seen, nm)) continue;
            if (!addName(ctx, names, nm)) return false;
        }
    }
    for (closed) |cr| {
        const on = &ctx.g.node(cr.node).object;
        for (on.props[@min(cr.from, @as(u32, @intCast(on.props.len)))..]) |p| {
            if (!p.required) continue;
            if (!addName(ctx, names, propName(ctx.g, p))) return false;
        }
    }
    var round: u32 = 0;
    while (round < 8) : (round += 1) {
        var changed = false;
        for (set) |fr| {
            const on = &ctx.g.node(fr.node).open_obj;
            for (on.deps) |dep| {
                const trig = ctx.g.literalBytes(dep.trigger);
                if (!seenContains(ctx.side, fr.seen, trig) and !nameIn(names.items, trig)) continue;
                switch (dep.kind) {
                    .ban => return false, // a banned trigger is present: doomed
                    .schema => {}, // merged as a virtual facet by emitOpenObj
                    .required => {
                        for (dep.names) |nl| {
                            const nm = ctx.g.literalBytes(nl);
                            if (seenContains(ctx.side, fr.seen, nm)) continue;
                            if (!nameIn(names.items, nm)) {
                                if (!addName(ctx, names, nm)) return false;
                                changed = true;
                            }
                        }
                    },
                }
            }
        }
        if (!changed) break;
    }
    // names_forbidden (propertyNames false) admits only the empty object.
    for (set) |fr| {
        const on = &ctx.g.node(fr.node).open_obj;
        if (on.names_forbidden and names.items.len > 0) return false;
    }
    return true;
}

/// Bytes that complete an in-flight escape / partial UTF-8 character under
/// the canonical escape table (the openKeyFeed/strFeed/strPatFeed machine):
/// `\` -> "n", `\u` -> "0000", `\u0` -> "000", `\u00` -> "00", and after
/// `\u00X` a single "0" when the high nibble X keeps the code below 0x20
/// (lo > 1 is a dead thread: every continuation errs). Mid-UTF-8 takes the
/// lowest permitted continuation then 0x80s. Null: no completion exists.
fn escapeBytes(ctx: *Ctx, st: parser.StrState, rem: u8, lo: u8) ?[]const u8 {
    switch (st) {
        .escape => return appendAlloc(ctx, "n"),
        .u0 => return appendAlloc(ctx, "0000"),
        .u00 => return appendAlloc(ctx, "000"),
        .uhex1 => return appendAlloc(ctx, "00"),
        .uhex2 => {
            if (lo > 1) return null;
            return appendAlloc(ctx, "0");
        },
        .normal => {
            if (rem == 0) return appendAlloc(ctx, "");
            var buf: [4]u8 = undefined;
            buf[0] = lo;
            for (1..rem) |i| buf[i] = 0x80;
            return appendAlloc(ctx, buf[0..rem]);
        },
        .open => return null,
    }
}

/// The decoded codepoint an escape completion produces, for DFA feeding:
/// "n" -> 0x0A, the \u0000 forms -> 0, `.uhex2` "0" -> lo*16. Null when
/// mid-UTF-8 (the caller folds the pending partial codepoint instead).
fn escapeCp(st: parser.StrState, lo: u8) ?u21 {
    return switch (st) {
        .escape => 0x0A,
        .u0, .u00, .uhex1 => 0,
        .uhex2 => if (lo <= 1) @as(u21, lo) * 16 else null,
        .normal => null, // mid-UTF-8 handled by caller; rem == 0 no char
        .open => null,
    };
}

/// Bytes completing the string-machine state of an in-flight key.
fn escapeCompletion(ctx: *Ctx, self: ObjRef) ?[]const u8 {
    return escapeBytes(ctx, self.key_st, self.key_rem, self.key_lo);
}

fn appendAlloc(ctx: *Ctx, s: []const u8) ?[]const u8 {
    return ctx.a.dupe(u8, s) catch null;
}

/// Pick completions of the in-flight key, best first (up to 3): an exact
/// declared match of the typed prefix, then a required declared extension,
/// an optional declared extension, an exact/extended extra_required name,
/// the typed prefix (plus its escape completion) as an undeclared key, and
/// finally a one-char undeclared extension of it (the prefix may spell an
/// already-dispatched - hence unavailable - declared key, while the real
/// key is a longer undeclared one, e.g. "evidence " next to a declared
/// "evidence"). The alternates let the sim try each when the prefix is
/// simultaneously a full key and a strict prefix of a longer one.
fn chooseKeyCompletions(ctx: *Ctx, self: ObjRef, kb: []const u8, esc: []const u8, out: *[3][]const u8) usize {
    var n: usize = 0;
    const on = &ctx.g.node(self.node).open_obj;
    const from = @min(self.next_idx, @as(u32, @intCast(on.props.len)));
    if (kb.len > 0) {
        for (on.props[from..]) |p| {
            const nm = propName(ctx.g, p);
            if (std.mem.eql(u8, nm, kb)) {
                out[n] = nm;
                n += 1;
                break;
            }
        }
    }
    for (on.props[from..]) |p| {
        if (!p.required) continue;
        const nm = propName(ctx.g, p);
        if (nm.len > kb.len and std.mem.startsWith(u8, nm, kb) and (n == 0 or !std.mem.eql(u8, out[0], nm))) {
            out[n] = nm;
            n += 1;
            break;
        }
    }
    if (n < 3) {
        for (on.props[from..]) |p| {
            const nm = propName(ctx.g, p);
            if (nm.len > kb.len and std.mem.startsWith(u8, nm, kb) and (n == 0 or !std.mem.eql(u8, out[0], nm))) {
                out[n] = nm;
                n += 1;
                break;
            }
        }
    }
    if (n < 3) {
        for (on.extra_required) |lit| {
            const nm = ctx.g.literalBytes(lit);
            if (seenContains(ctx.side, self.seen, nm)) continue;
            if (nm.len >= kb.len and std.mem.startsWith(u8, nm, kb) and (n == 0 or !std.mem.eql(u8, out[0], nm))) {
                out[n] = nm;
                n += 1;
                break;
            }
        }
    }
    if (n < 3 and kb.len > 0) {
        // An undeclared key spelled by the typed prefix itself, completed
        // out of any in-flight escape / partial UTF-8 character.
        if (esc.len == 0) {
            out[n] = kb;
        } else {
            const full = ctx.a.alloc(u8, kb.len + esc.len) catch return n;
            @memcpy(full[0..kb.len], kb);
            @memcpy(full[kb.len..], esc);
            out[n] = full;
        }
        n += 1;
    }
    if (n < 3 and kb.len > 0 and esc.len == 0) {
        // A one-char undeclared extension: the prefix may spell an
        // already-dispatched (hence unavailable) declared key while the
        // real key is longer. 'a' only collides with validateSeq-legit
        // spellings; the sim arbitrates.
        const ext = ctx.a.alloc(u8, kb.len + 1) catch return n;
        @memcpy(ext[0..kb.len], kb);
        ext[kb.len] = 'a';
        if (n == 0 or !std.mem.eql(u8, out[0], ext)) {
            if (n < 2 or !std.mem.eql(u8, out[1], ext)) {
                out[n] = ext;
                n += 1;
            }
        }
    }
    return n;
}

/// Per-frame dispatch validation over the planned key sequence: declared
/// keys must arrive in schema order from next_idx, undeclared keys must be
/// fresh and pass prop_names / key length bounds, no key may be a banned
/// dep trigger, and names_forbidden frames admit no key at all.
fn validateSeq(ctx: *Ctx, set: []const ObjRef, names: []const []const u8) bool {
    for (set) |fr| {
        const on = &ctx.g.node(fr.node).open_obj;
        var cur: u32 = fr.next_idx;
        for (names) |nm| {
            if (on.names_forbidden) return false;
            for (on.deps) |dep| {
                if (dep.kind == .ban and std.mem.eql(u8, ctx.g.literalBytes(dep.trigger), nm)) return false;
            }
            if (declaredIdx(ctx.g, on, nm)) |i| {
                if (i < cur) return false;
                cur = @intCast(i + 1);
            } else {
                if (seenContains(ctx.side, fr.seen, nm)) return false;
                if (!keyBoundsOk(ctx, on, nm)) return false;
                if (on.prop_names) |pn| {
                    const dec = witset.decodeEscaped(ctx.a, nm) catch return false;
                    if (witset.acceptsValue(witset.viewOf(ctx.g), ctx.a, pn, .{ .str = dec }) != .yes) return false;
                }
            }
        }
    }
    return true;
}

/// Key length bounds apply to undeclared keys only, counted in decoded
/// codepoints.
fn keyBoundsOk(ctx: *Ctx, on: *const grammar.OpenObjNode, nm: []const u8) bool {
    if (on.key_min_len == 0 and on.key_max_len == grammar.UNBOUNDED) return true;
    const dec = witset.decodeEscaped(ctx.a, nm) catch return false;
    const cp = std.unicode.utf8CountCodepoints(dec) catch return false;
    if (cp < on.key_min_len) return false;
    if (on.key_max_len != grammar.UNBOUNDED and cp > on.key_max_len) return false;
    return true;
}

/// A fresh undeclared key name for min_props padding / mandatory-key
/// positions: not declared, unseen, unplanned, not a dep trigger or name,
/// passing every frame's prop_names and key bounds.
fn freshName(ctx: *Ctx, set: []const ObjRef, names: []const []const u8) ?[]const u8 {
    var buf: [8]u8 = undefined;
    var n: u32 = 0;
    while (n < 64) : (n += 1) {
        const nm = nameCandidate(ctx, &buf, n) orelse return null;
        if (nameOkEverywhere(ctx, set, names, nm)) return nm;
    }
    return null;
}

fn nameCandidate(ctx: *Ctx, buf: *[8]u8, n: u32) ?[]const u8 {
    if (n < 26) {
        buf[0] = @intCast('a' + n);
        const s = ctx.a.dupe(u8, buf[0..1]) catch return null;
        return s;
    }
    const s = std.fmt.allocPrint(ctx.a, "k{d}", .{n}) catch return null;
    return s;
}

fn nameOkEverywhere(ctx: *Ctx, set: []const ObjRef, names: []const []const u8, nm: []const u8) bool {
    if (nameIn(names, nm)) return false;
    for (set) |fr| {
        const on = &ctx.g.node(fr.node).open_obj;
        if (on.names_forbidden) return false;
        if (declaredIdx(ctx.g, on, nm) != null) return false;
        if (seenContains(ctx.side, fr.seen, nm)) return false;
        for (on.deps) |dep| {
            if (std.mem.eql(u8, ctx.g.literalBytes(dep.trigger), nm)) return false;
            for (dep.names) |nl| {
                if (std.mem.eql(u8, ctx.g.literalBytes(nl), nm)) return false;
            }
        }
        if (!keyBoundsOk(ctx, on, nm)) return false;
        if (on.prop_names) |pn| {
            const dec = witset.decodeEscaped(ctx.a, nm) catch return false;
            if (witset.acceptsValue(witset.viewOf(ctx.g), ctx.a, pn, .{ .str = dec }) != .yes) return false;
        }
        if (on.pattern_lit) |pl| {
            const dec = witset.decodeEscaped(ctx.a, nm) catch return false;
            if (std.mem.indexOf(u8, dec, ctx.g.literalBytes(pl)) != null) return false;
        }
    }
    return true;
}

/// min_props padding with fresh undeclared keys; max_props caps the total.
fn padMinProps(ctx: *Ctx, set: []const ObjRef, names: *std.ArrayListUnmanaged([]const u8)) bool {
    var need: u32 = 0;
    for (set) |fr| {
        const on = &ctx.g.node(fr.node).open_obj;
        if (on.min_props == 0) continue;
        if (!on.track_keys) return false;
        const typed = seenCount(ctx.side, fr.seen);
        const total = typed + @as(u32, @intCast(names.items.len));
        if (on.min_props > total) need = @max(need, on.min_props - total);
    }
    for (set) |fr| {
        const on = &ctx.g.node(fr.node).open_obj;
        if (on.max_props == grammar.UNBOUNDED) continue;
        if (!on.track_keys) return false;
        const typed = seenCount(ctx.side, fr.seen);
        if (typed + @as(u32, @intCast(names.items.len)) + need > on.max_props) return false;
    }
    while (need > 0) : (need -= 1) {
        const nm = freshName(ctx, set, names.items) orelse return false;
        names.append(ctx.a, nm) catch return false;
    }
    return true;
}

/// Value schema of an undeclared key dispatch: patternProperties wins over
/// the default value schema.
fn dispatchUndeclared(ctx: *Ctx, on: *const grammar.OpenObjNode, nm: []const u8) ?grammar.NodeId {
    const dec = witset.decodeEscaped(ctx.a, nm) catch return null;
    if (on.pattern_lit) |pl| {
        if (std.mem.indexOf(u8, dec, ctx.g.literalBytes(pl)) != null) return on.pattern_value;
    }
    if (on.pattern_dfa) |dfa| {
        const m = dfa.matchesUtf8(dec) catch return null;
        if (m) return on.pattern_value;
    }
    return on.value;
}

/// A value accepted by every schema of the list, proven by construction
/// plus acceptsValue cross-checks (exact per-node validation).
fn intersectWitness(ctx: *Ctx, schemas: []const grammar.NodeId) ?witset.Val {
    const view = witset.viewOf(ctx.g);
    outer: for (schemas) |s| {
        const v = witset.witness(view, ctx.a, s) orelse continue;
        for (schemas) |s2| {
            if (s2 == s) continue;
            if (witset.acceptsValue(view, ctx.a, s2, v) != .yes) continue :outer;
        }
        return v;
    }
    return null;
}

/// Emit the value of key `nm`, dispatching per frame (declared prop value,
/// pattern value, or the default value schema) and spelling a witness of
/// the intersection. Merged closed facets must declare `nm` after the
/// previously emitted key (curc tracks their prop cursor).
fn emitKeyValue(ctx: *Ctx, set: []const ObjRef, cur: *[MAX_SET]u32, closed: []const ClosedRef, curc: *[MAX_SET]u32, nm: []const u8, depth: u32) bool {
    if (depth > MAX_EMIT_DEPTH) return false;
    var schemas: [MAX_SET * 2]grammar.NodeId = undefined;
    var nsc: usize = 0;
    for (set, 0..) |fr, k| {
        const on = &ctx.g.node(fr.node).open_obj;
        if (declaredIdx(ctx.g, on, nm)) |i| {
            schemas[nsc] = on.props[i].value;
            cur[k] = @intCast(i + 1);
        } else {
            schemas[nsc] = dispatchUndeclared(ctx, on, nm) orelse return false;
        }
        nsc += 1;
    }
    for (closed, 0..) |cr, k| {
        const on = &ctx.g.node(cr.node).object;
        const pi = objDeclaredIdx(ctx.g, on, nm) orelse return false;
        if (pi < curc[k]) return false;
        curc[k] = pi + 1;
        schemas[nsc] = on.props[pi].value;
        nsc += 1;
    }
    const v = intersectWitness(ctx, schemas[0..nsc]) orelse return false;
    witset.spell(ctx.out, ctx.a, v) catch return false;
    return true;
}

fn emitBody(ctx: *Ctx, set: []const ObjRef, closed: []const ClosedRef, names: []const []const u8, kb: []const u8, boundary: bool, extra: []const grammar.NodeId, depth: u32) bool {
    const self = set[0];
    var cur: [MAX_SET]u32 = undefined;
    for (set, 0..) |fr, k| cur[k] = fr.next_idx;
    var curc: [MAX_SET]u32 = undefined;
    for (closed, 0..) |cr, k| curc[k] = cr.from;
    var need_comma = false;
    var start: usize = 0;
    switch (self.phase) {
        .key, .key_after_comma => {},
        .sep => need_comma = true,
        .value => {
            if (boundary) {
                // The pristine value machines above were skipped by the
                // walk; spell a value every merged sibling and every comb-
                // group sibling thread accepts. Virtual dep facets dispatch
                // the in-flight key's name under their own schema.
                var schemas: [MAX_SET * 2 + MAX_GROUP]grammar.NodeId = undefined;
                var nsc: usize = 0;
                for (set) |fr| {
                    if (fr.virtual) {
                        if (self.key != 0) {
                            schemas[nsc] = virtualPending(ctx, fr, ctx.side.chunkAt(self.key).payload.items) orelse return false;
                            nsc += 1;
                        }
                        continue;
                    }
                    schemas[nsc] = fr.pending;
                    nsc += 1;
                }
                for (extra) |e| {
                    schemas[nsc] = e;
                    nsc += 1;
                }
                for (closed) |cr| {
                    if (!cr.inflight_value) return false;
                    schemas[nsc] = ctx.g.node(cr.node).object.props[cr.cur].value;
                    nsc += 1;
                }
                const v = intersectWitness(ctx, schemas[0..nsc]) orelse return false;
                witset.spell(ctx.out, ctx.a, v) catch return false;
            }
            need_comma = true;
        },
        .colon => {
            if (!appendStr(ctx, ":")) return false;
            var schemas: [MAX_SET * 2]grammar.NodeId = undefined;
            var nsc: usize = 0;
            for (set) |fr| {
                if (fr.virtual) {
                    if (self.key != 0) {
                        schemas[nsc] = virtualPending(ctx, fr, ctx.side.chunkAt(self.key).payload.items) orelse return false;
                        nsc += 1;
                    }
                    continue;
                }
                schemas[nsc] = fr.pending;
                nsc += 1;
            }
            for (closed) |cr| {
                if (!cr.inflight_value) return false;
                schemas[nsc] = ctx.g.node(cr.node).object.props[cr.cur].value;
                nsc += 1;
            }
            const v = intersectWitness(ctx, schemas[0..nsc]) orelse return false;
            witset.spell(ctx.out, ctx.a, v) catch return false;
            need_comma = true;
        },
        .key_str => {
            const nm = names[0];
            if (!appendStr(ctx, nm[kb.len..])) return false;
            if (!appendStr(ctx, "\":")) return false;
            if (!emitKeyValue(ctx, set, &cur, closed, &curc, nm, depth + 1)) return false;
            need_comma = true;
            start = 1;
        },
        else => return false,
    }
    for (names[start..]) |nm| {
        if (need_comma) if (!appendStr(ctx, ",")) return false;
        need_comma = true;
        if (!appendStr(ctx, "\"")) return false;
        const dec = witset.decodeEscaped(ctx.a, nm) catch return false;
        witset.appendEscaped(ctx.out, ctx.a, dec) catch return false;
        if (!appendStr(ctx, "\":")) return false;
        if (!emitKeyValue(ctx, set, &cur, closed, &curc, nm, depth + 1)) return false;
    }
    return appendStr(ctx, "}");
}

/// A schema-kind dep's target can merge as a virtual sibling facet when it
/// constrains objects through exactly one plain open_obj: the keyword
/// semantics wrap object constraints in a choice with non-object alts
/// (strings/numbers/arrays/scalar literals pass freely), and the object
/// bytes of the host can only ever take the open_obj alt. Nested combs,
/// closed-object alts, tries that might hold object literals, and open_objs
/// with their own deps/capture/name/key/count bounds are out of scope.
/// Returns the open_obj node to merge, or null (unmergable; when no alt
/// accepts objects at all the dep is unsatisfiable for this host, which is
/// also a candidate-doomed bail).
fn virtualizableDep(ctx: *Ctx, node: grammar.NodeId) ?grammar.NodeId {
    const oo_ok = struct {
        fn f(on2: *const grammar.OpenObjNode) bool {
            return !on2.names_forbidden and !on2.capture and on2.deps.len == 0 and
                on2.prop_names == null and on2.key_min_len == 0 and
                on2.key_max_len == grammar.UNBOUNDED and on2.pattern_lit == null and
                on2.pattern_dfa == null and on2.min_props == 0 and
                on2.max_props == grammar.UNBOUNDED;
        }
    }.f;
    switch (ctx.g.node(node).*) {
        .open_obj => |*o| return if (oo_ok(o)) node else null,
        .choice => |alts| {
            var found: ?grammar.NodeId = null;
            for (alts) |alt| {
                switch (ctx.g.node(alt).*) {
                    .open_obj => |*o| {
                        if (!oo_ok(o) or found != null) return null;
                        found = alt;
                    },
                    // Clearly non-object languages: disjoint from the host.
                    .repeat, .str, .str_pat, .str_excl, .int_v, .int_num, .num_v, .num_const, .num_excl, .num_range, .num_mult, .not_int_num => {},
                    .literal => |lit| {
                        if (ctx.g.literalBytes(lit)[0] == '{') return null;
                    },
                    .lit_trie => |lt| {
                        // Scalar enums are fine; an object literal member
                        // would constrain the host, which is unanalyzable.
                        for (lt.literals) |lit| {
                            if (ctx.g.literalBytes(lit)[0] == '{') return null;
                        }
                    },
                    else => return null,
                }
            }
            return found; // null: no object alt -> dep unsatisfiable here
        },
        else => return null,
    }
}

/// Build the virtual facet for dep schema `node` sharing the host frame's
/// seen set. next_idx advances past every seen key's declared position
/// (dispatch order is monotonic); a required prop left behind that cursor
/// and absent from the seen set can never be discharged by the dep's
/// close-time reparse, so the candidate is doomed (null).
fn mkVirtualRef(ctx: *Ctx, node: grammar.NodeId, host: ObjRef) ?ObjRef {
    const on2 = &ctx.g.node(node).open_obj;
    var next_idx: u32 = 0;
    if (host.seen != 0) {
        const items = ctx.side.chunkAt(host.seen).payload.items;
        if (items.len >= 4) {
            const count = std.mem.readInt(u32, items[0..4], .little);
            var off: usize = 4;
            var i: u32 = 0;
            while (i < count) : (i += 1) {
                if (off + 4 > items.len) break;
                const len = std.mem.readInt(u32, items[off..][0..4], .little);
                off += 4;
                if (off + len > items.len) break;
                if (declaredIdx(ctx.g, on2, items[off .. off + len])) |pi|
                    next_idx = @max(next_idx, @as(u32, @intCast(pi + 1)));
                off += len;
            }
        }
    }
    for (on2.props[0..@min(next_idx, @as(u32, @intCast(on2.props.len)))]) |p| {
        if (!p.required) continue;
        if (!seenContains(ctx.side, host.seen, propName(ctx.g, p))) return null;
    }
    return .{
        .node = node,
        .next_idx = next_idx,
        .seen = host.seen,
        .phase = host.phase,
        .key = 0,
        .key_st = .normal,
        .key_rem = 0,
        .key_lo = 0,
        .key_hi = 0,
        .pending = 0,
        .virtual = true,
    };
}

/// Value schema a virtual dep facet assigns to the in-flight key `nm`
/// (declared prop value, or the undeclared-key dispatch).
fn virtualPending(ctx: *Ctx, fr: ObjRef, nm: []const u8) ?grammar.NodeId {
    const on2 = &ctx.g.node(fr.node).open_obj;
    if (declaredIdx(ctx.g, on2, nm)) |pi| return on2.props[pi].value;
    return dispatchUndeclared(ctx, on2, nm);
}

fn emitOpenObj(ctx: *Ctx, t: *const parser.Thread, d: usize, depth: u32) bool {
    const of = &t.frames[d].open_obj;
    if (of.phase == .open) return emitValue(ctx, of.node, depth);
    const boundary = ctx.value_boundary != null and ctx.value_boundary.? == d and of.phase == .value;
    var set: std.ArrayListUnmanaged(ObjRef) = .{};
    var closed: std.ArrayListUnmanaged(ClosedRef) = .{};
    var visited: std.ArrayListUnmanaged(u32) = .{};
    if (!collectObjSet(ctx, t, d, 0, boundary, &set, &closed, &visited)) return false;
    var names: std.ArrayListUnmanaged([]const u8) = .{};
    // Schema-kind deps in force merge their target open_obj as a virtual
    // facet; its obligations may themselves plan dep triggers, so facet
    // creation and obligation collection iterate to a bounded fixpoint.
    var vround: u32 = 0;
    while (true) {
        var added = false;
        // Index-based: appending a virtual facet reallocates set.items.
        var si: usize = 0;
        while (si < set.items.len) : (si += 1) {
            const fr = set.items[si];
            const on = &ctx.g.node(fr.node).open_obj;
            for (on.deps) |dep| {
                if (dep.kind != .schema) continue;
                const trig = ctx.g.literalBytes(dep.trigger);
                if (!seenContains(ctx.side, fr.seen, trig) and !nameIn(names.items, trig)) continue;
                const vnode = virtualizableDep(ctx, dep.schema) orelse return false;
                var have = false;
                for (set.items) |fr2| {
                    if (fr2.virtual and fr2.node == vnode) {
                        have = true;
                        break;
                    }
                }
                if (have) continue;
                if (set.items.len >= MAX_SET) return false;
                const vr = mkVirtualRef(ctx, vnode, fr) orelse return false;
                set.append(ctx.a, vr) catch return false;
                added = true;
            }
        }
        names.clearRetainingCapacity();
        if (!collectObligations(ctx, set.items, closed.items, &names)) return false;
        if (!added) break;
        vround += 1;
        if (vround >= 8) return false;
    }
    var kb: []const u8 = "";
    if (of.phase == .key_str) {
        kb = ctx.side.chunkAt(of.key).payload.items;
        const esc = escapeCompletion(ctx, set.items[0]) orelse return false;
        var kc: [3][]const u8 = undefined;
        var nkc = chooseKeyCompletions(ctx, set.items[0], kb, esc, &kc);
        if (nkc == 0) {
            // Nothing typed and no declared/extra match: a fresh undeclared
            // key (free-form map bodies dispatch every key to on.value).
            const nm = freshName(ctx, set.items, names.items) orelse return false;
            kc[0] = nm;
            nkc = 1;
        }
        if (ctx.key_choice >= nkc) return false;
        const k = kc[ctx.key_choice];
        // The in-flight key completes first; the sim validates its cross-
        // branch dispatch.
        if (!nameIn(names.items, k)) {
            if (names.items.len >= MAX_OBL) return false;
            names.insert(ctx.a, 0, k) catch return false;
        } else {
            const pos = blk: {
                for (names.items, 0..) |x, i| {
                    if (std.mem.eql(u8, x, k)) break :blk i;
                }
                unreachable;
            };
            const tmp = names.items[pos];
            std.mem.copyBackwards([]const u8, names.items[1 .. pos + 1], names.items[0..pos]);
            names.items[0] = tmp;
        }
        // Sibling in-flight prefixes must spell the same bytes.
        for (set.items[1..]) |fr| {
            if (fr.virtual) continue; // no in-flight key on a dep facet
            const skb = ctx.side.chunkAt(fr.key).payload.items;
            if (!std.mem.eql(u8, skb, kb)) return false;
        }
    }
    // A key is mandatory after a comma: emit an optional declared prop or a
    // fresh undeclared key when nothing is outstanding.
    if (of.phase == .key_after_comma and names.items.len == 0) {
        const on = &ctx.g.node(of.node).open_obj;
        const from = @min(of.next_idx, @as(u32, @intCast(on.props.len)));
        var added = false;
        for (on.props[from..]) |p| {
            if (p.required) continue;
            if (!addName(ctx, &names, propName(ctx.g, p))) return false;
            added = true;
            break;
        }
        if (!added) {
            const nm = freshName(ctx, set.items, names.items) orelse return false;
            names.append(ctx.a, nm) catch return false;
        }
    }
    if (!validateSeq(ctx, set.items, names.items)) {
        return false;
    }
    if (!padMinProps(ctx, set.items, &names)) {
        return false;
    }
    var extra: [MAX_GROUP]grammar.NodeId = undefined;
    var nextra: usize = 0;
    if (boundary and ctx.group_mode == 0) {
        nextra = groupSiblingConstraints(ctx, t, &extra) orelse return false;
    }
    return emitBody(ctx, set.items, closed.items, names.items, kb, boundary, extra[0..nextra], depth);
}
