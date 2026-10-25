//! Mask-path diagnostics (not part of the ABI contract).
//!
//! Counters for the diagnosis of the mask cost: which mask path ran, why
//! the string-content and open-object-name fast paths were refused, how much
//! trie the exact walk visited, and how many per-terminal completion checks
//! it ran.
//!
//! Read through `blg_diag_counters` (c_api.zig); reset through
//! `blg_diag_reset`. Nothing here changes mask semantics: the counters only
//! observe, and there is deliberately no semantics-changing knob - the
//! `BLG_MASK_NO_FILTER` ablation was retired because a diagnostic build must
//! not be able to produce masks the release build would not.
//!
//! The library is called from several threads (parallel sessions, worker
//! pools), so a plain `+=` on a process-global here would be a data race.
//! Every increment is a relaxed atomic RMW and every read a relaxed atomic
//! load; the cost is one locked add per event. The counters stay compiled in
//! so diagnostics report the path taken on the same artifact;
//! `-Ddiag_counters=false` compiles the increments out. Measured cost:
//! benchmarks/maskbench/t8a/evidence/9a5baf3e/diag-2026-10-22/counters/counter-overhead.md.

const std = @import("std");

/// Build option `-Ddiag_counters=false` compiles every increment out: the
/// symbols and the read/reset path stay, the counters report zero. The
/// release default keeps them (diagnostics run on the same artifact);
/// packaging for the fastest mask can turn them off.
pub const enabled: bool = @import("build_options").diag_counters;

pub const Counter = std.atomic.Value(u64);

pub const Counters = struct {
    /// Every fill call that reached a mask path.
    fill_calls: Counter = .init(0),
    /// fillMaskFast attempted / used.
    fast_calls: Counter = .init(0),
    fast_used: Counter = .init(0),
    /// Refusal reasons, in the order the fast path checks them.
    refuse_not_uniform: Counter = .init(0),
    refuse_byte_incomplete: Counter = .init(0),
    refuse_content_dep: Counter = .init(0),
    /// Exact walk activity.
    exact_walk_calls: Counter = .init(0),
    trie_nodes: Counter = .init(0),
    trie_terminals: Counter = .init(0),
    /// Completion-filter calls (one per trie terminal when filtered).
    state_alive_calls: Counter = .init(0),
    alive_dead: Counter = .init(0),
    /// Content class construction.
    content_masks: Counter = .init(0),
    content_memcpy: Counter = .init(0),
    content_word_loop: Counter = .init(0),
    content_cache_hits: Counter = .init(0),
    /// Tokens decided by the exact (pruned) class.
    exact_tokens: Counter = .init(0),
    /// Open-object name lemma (K1): masks built by it, and states that
    /// failed the string lemma and did not satisfy K1's preconditions.
    /// Appended after `exact_tokens` so the positional layout of the
    /// earlier counters (and every recorded diag log) stays valid.
    name_used: Counter = .init(0),
    refuse_name_state: Counter = .init(0),
};

pub var c: Counters = .{};

/// Field order of `blg_diag_counters`.
pub const order = [_][]const u8{
    "fill_calls",         "fast_calls",             "fast_used",
    "refuse_not_uniform", "refuse_byte_incomplete", "refuse_content_dep",
    "exact_walk_calls",   "trie_nodes",             "trie_terminals",
    "state_alive_calls",  "alive_dead",             "content_masks",
    "content_memcpy",     "content_word_loop",      "content_cache_hits",
    "exact_tokens",       "name_used",              "refuse_name_state",
};

pub inline fn incr(p: *Counter, n: u64) void {
    if (comptime enabled) _ = p.fetchAdd(n, .monotonic);
}

pub fn values() [order.len]u64 {
    return .{
        c.fill_calls.load(.monotonic),
        c.fast_calls.load(.monotonic),
        c.fast_used.load(.monotonic),
        c.refuse_not_uniform.load(.monotonic),
        c.refuse_byte_incomplete.load(.monotonic),
        c.refuse_content_dep.load(.monotonic),
        c.exact_walk_calls.load(.monotonic),
        c.trie_nodes.load(.monotonic),
        c.trie_terminals.load(.monotonic),
        c.state_alive_calls.load(.monotonic),
        c.alive_dead.load(.monotonic),
        c.content_masks.load(.monotonic),
        c.content_memcpy.load(.monotonic),
        c.content_word_loop.load(.monotonic),
        c.content_cache_hits.load(.monotonic),
        c.exact_tokens.load(.monotonic),
        c.name_used.load(.monotonic),
        c.refuse_name_state.load(.monotonic),
    };
}

/// Zeroes every counter with a relaxed store: safe to call while another
/// thread is still incrementing (the reset wins or the increment does, but
/// neither is a data race).
pub fn reset() void {
    inline for (@typeInfo(Counters).@"struct".field_names) |name| {
        @field(c, name[0..name.len]).store(0, .monotonic);
    }
}
