//! Exact token-level completion reachability for masks (TZ 3.1, FR-6).
//!
//! A token is allowed only when, after accepting it, a finite continuation
//! of allowed tokens still reaches a completed answer. The mask walk
//! already prunes byte-illegal tokens; this module answers the remaining
//! question for vocabularies without full byte coverage: can the state
//! after the token reach can_end by feeding whole tokens?
//!
//! The filter runs for grammars whose language is a finite literal
//! set (grammar.finite_literal) on vocabularies that are not
//! byte-complete - then the state space is finite and every fed token
//! consumes at least one byte of it, so the search terminates.
//!
//! It also runs for grammars with deferred value verdicts
//! (grammar.needs_reachability, spec-v1: comb groups, pattern strings,
//! bounded/excluded strings, value-constrained numbers) on ANY vocabulary:
//! there a live prefix can dead-end even with full byte coverage
//! (ADR-0005), so "byte-legal" no longer implies "completable". Those states are
//! first settled exactly by a per-frame residual certification
//! (certifyState: closed-form answers for the value machines, a comb group
//! doom rule mirroring parser.combSweep on certified-dead branches); only
//! undecided states fall back to the budgeted token search. When the search
//! cannot settle a state within its budget (ResourceLimit), that is UNKNOWN,
//! not alive: the callers (mask.zig, c_api.zig) propagate it, failing the
//! whole mask call / refusing the token with RESOURCE_LIMIT (ADR-0005
//! Decision 3) instead of silently allowing a token that may dead-end.
//! The certification also covers plain open-object frames (certOpenObj):
//! the remaining required-key obligation is decided against the exact
//! per-node emptiness bitset of the grammar (grammar.nonempty), so merged
//! allOf objects never reach the search.
//!
//! A byte-complete vocabulary over a grammar without deferred verdicts
//! never has dead-end tokens: any byte-wise completion can be fed one byte
//! at a time, so canonical-v1 production tokenizers (GPT-2, Llama byte
//! fallback) keep the unfiltered fast path.
//!
//! Results are memoized per session (Cache); cancelled searches fail the
//! mask call explicitly, and failed searches leave no in-progress entries
//! behind. A search that exhausts its budget reports ResourceLimit, which
//! is UNKNOWN, not alive: the mask call sites propagate it and the whole
//! mask call fails (ADR-0005 Decision 3).

const std = @import("std");
const grammar = @import("grammar.zig");
const parser = @import("parser.zig");
const witset = @import("witset.zig");
const synth = @import("synth.zig");
const pattern = @import("pattern.zig");
const tokenizer = @import("tokenizer.zig");
const work_mod = @import("work.zig");

pub const Error = error{ OutOfMemory, ResourceLimit, Cancelled };

/// Depth guard: a completion of a finite literal language is bounded by
/// its remaining bytes; the cap only turns a hypothetical bug into an
/// explicit error instead of an unbounded walk. The needs_reachability
/// path searches unbounded machines, where a completion may legitimately
/// sit far away (a long minLength under a comb group). The deeper cap
/// still converts a truly unbounded walk into an explicit ResourceLimit.
/// MAX_DEPTH_REACH is also the memory bound of the search: the scratch
/// pool holds one parser.State (~256 KB of inline thread/frame arrays)
/// per depth, so 512 depths cap the pool near 128 MB; deeper witnesses are
/// reported as ResourceLimit, which fails the mask call (ADR-0005 D3).
const MAX_DEPTH: usize = 1024;
const MAX_DEPTH_REACH: usize = 512;

/// Expansion budget shared by all reachability searches of one fill_mask
/// call (and of one accept check): 65536 expansions bounds the worst-case
/// filter cost of a single mask; beyond it the remaining undecided tokens
/// fail the mask call with ResourceLimit (ADR-0005 D3).
pub const FILL_SEARCH_BUDGET: u64 = 1 << 16;

const Status = enum(u8) { in_progress, dead, alive };

/// Memo entry. `alive` and `in_progress` are absolute; `dead` carries the
/// remaining-search-depth budget under which deadness was proven
/// (maxInt(u32) for an absolute proof: certification, or a finite state
/// space fully drained). A dead entry answers only searches whose
/// remaining depth does not exceed `rem`; deeper searches re-prove it.
const Entry = struct { status: Status, rem: u32 };

/// Session-owned memo: state key -> alive/dead, plus reused scratch states
/// for the depth-first search (one state per depth, reused across
/// sibling branches).
pub const Cache = struct {
    a: std.mem.Allocator,
    map: std.StringHashMapUnmanaged(Entry) = .{},
    states: std.ArrayListUnmanaged(*parser.State) = .{},
    key_buf: std.ArrayListUnmanaged(u8) = .{},
    side: ?*parser.Side = null,
    /// Thread cap of the state under test, propagated to the pooled scratch
    /// states so feedBytes inside the search enforces the same engine limit
    /// the accept path would.
    max_threads: u16 = parser.MAX_THREADS_CAP,
    /// Per-tier expansion budget of the budgeted search (set by stateAlive;
    /// consumed only on the needs_reachability path, where unbounded
    /// machines make the state space infinite). Exhaustion caps the current
    /// tier (the tier is retried deeper with a fresh, larger budget); only a
    /// capped tier at the depth ceiling raises ResourceLimit, which fails
    /// the whole mask call (ADR-0005 D3).
    budget: u64 = 0,
    /// Per-mask-call expansion budget shared by all reachability checks of
    /// one fill_mask (set by fillMask; the accept path sets its own). One
    /// fill can face hundreds of search-needing tokens (a comb/pattern
    /// object at a key position makes nearly every junk-byte state
    /// uncertifiable), and each unprovable state burns its whole tier
    /// ladder - without a shared cap a single mask runs for minutes.
    /// Exhaustion makes the remaining undecided states report ResourceLimit,
    /// which the mask call propagates (ADR-0005 D3); per-token dead proofs
    /// already stamped in the memo stay exact. Defaults to one fill's worth
    /// so direct stateAlive callers (tests) get the normal behavior without
    /// an explicit setup.
    fill_budget: u64 = FILL_SEARCH_BUDGET,
    /// Sticky within one tier: some branch was cut by the expansion budget
    /// rather than the depth cap. A budget-cut branch is not exhaustive, so
    /// no dead stamp proven this tier is horizon-valid; suppressing the
    /// stamps keeps the memo sound.
    budget_out: bool = false,
    /// Memo entry cap (same path): state images can run to tens of KB each
    /// (deep any-value towers), so an unbounded memo exhausts the context
    /// temp budget long before the node budget. Entries beyond the cap
    /// are simply not stored - the memo is an optimization; the in_progress
    /// marks that carry the cycle argument are path-local and unaffected.
    memo_cap: usize = 1 << 12,
    /// Scratch-pool bound: the pool holds one parser.State per search depth
    /// and a State is ~256 KB of inline thread/frame arrays; the pool is
    /// charged to the context temp budget (see MaskBuf.init), so 256 depths
    /// cap it near 64 MB. A branch that would go deeper is cut like a
    /// budget cut (capped, never dead): the needs_reachability path then
    /// ends in ResourceLimit, failing the mask call (ADR-0005 D3), and the
    /// finite-literal path fails loudly exactly as for its depth cap.
    max_states: usize = 256,

    pub fn init(a: std.mem.Allocator) Cache {
        return .{ .a = a };
    }

    pub fn deinit(self: *Cache) void {
        var it = self.map.keyIterator();
        while (it.next()) |k| self.a.free(k.*);
        self.map.deinit(self.a);
        if (self.side) |sd| for (self.states.items) |sp| parser.releaseState(sd, sp);
        for (self.states.items) |s| self.a.destroy(s);
        self.states.deinit(self.a);
        self.key_buf.deinit(self.a);
        self.* = undefined;
    }

    /// Clears memoized verdicts and drops the scratch states' side-chunk
    /// references. The pool keeps the State allocations (the expensive
    /// part) for reuse, but a pooled state's content is dead once its
    /// search concluded; holding its open-object/repeat chunk refs pins
    /// the chunk payloads in the session account across fills (measured:
    /// ~7 MB pinned after a few hundred fills of a 13-way anyOf schema at
    /// MAX_THREADS_CAP 128, spilling the 8 MiB session budget).
    /// The key is the state byte image plus the chunk-content blob, so the
    /// cache must not be shared between different (grammar, tokenizer)
    /// pairs; callers that do (tests) reset it first.
    pub fn reset(self: *Cache) void {
        var it = self.map.keyIterator();
        while (it.next()) |k| self.a.free(k.*);
        self.map.clearRetainingCapacity();
        if (self.side) |sd| for (self.states.items) |sp| parser.releaseState(sd, sp);
    }

    /// Drops every pooled scratch state's chunk references. A pooled
    /// state's content is dead once its search concludes, but until the
    /// refs are dropped the session store keeps the chunks pinned: the
    /// next accept then COW-copies every chunk it mutates (refs > 1) while
    /// the pool still pins the original, leaking ~one chunk payload per
    /// accepted byte into the session account (measured on maskbench
    /// o77317: ~10 KB/byte, spilling the 8 MiB session budget after a few
    /// hundred bytes). Called on every stateAlive exit; the memo is
    /// unaffected (its keys are byte images plus chunk-content blobs).
    pub fn releaseScratch(self: *Cache) void {
        if (self.side) |sd| for (self.states.items) |sp| parser.releaseState(sd, sp);
    }

    const StateAtError = error{ OutOfMemory, PoolFull };

    fn stateAt(self: *Cache, depth: usize) StateAtError!*parser.State {
        if (depth >= self.max_states) return error.PoolFull;
        try self.states.ensureTotalCapacity(self.a, depth + 1);
        while (self.states.items.len <= depth) {
            const s = try self.a.create(parser.State);
            s.n = 0;
            s.max_threads = self.max_threads;
            self.states.appendAssumeCapacity(s);
        }
        return self.states.items[depth];
    }

    fn keyOf(self: *Cache, st: *const parser.State) ![]const u8 {
        const len = parser.stateKeyLen(st);
        // The state image stores chunk handles as raw ids and Side.create
        // reuses freed slots, so two states equal by image can differ in
        // seen-set content; the chunk-content blob (>= 4 bytes, the count)
        // is appended to keep the memo key exact.
        const blob_len = if (self.side) |sd| parser.chunkBlobLen(st, sd) else 4;
        self.key_buf.clearRetainingCapacity();
        try self.key_buf.ensureTotalCapacity(self.a, len + blob_len);
        self.key_buf.items.len = len + blob_len;
        parser.writeStateKey(st, self.key_buf.items[0..len]);
        if (self.side) |sd| {
            parser.writeChunkBlob(st, sd, self.key_buf.items[len..]);
        } else {
            std.mem.writeInt(u32, self.key_buf.items[len..][0..4], 0, .little);
        }
        return self.key_buf.items;
    }
};

/// True when some sequence of ordinary (non-EOS, non-special, non-empty)
/// tokens from `st` reaches a state where can_end holds, i.e. a completed
/// answer is reachable.
///
/// The needs_reachability state space is infinite (unbounded machines), so
/// a plain depth-first search can diverge down an open-ended content run
/// (a string/array/key that grows forever) without ever trying the shallow
/// completion one branch over. The search therefore runs in
/// iterative-deepening tiers: a tier proves dead only for paths within its
/// depth budget (memoized with that stamp), a live answer or a fully
/// drained tier settles the call, and a capped tier retries deeper. When
/// every tier up to MAX_DEPTH_REACH caps, the call gives up with
/// ResourceLimit. ResourceLimit is UNKNOWN, not alive (ADR-0005 Decision 3):
/// the mask call sites (mask.zig) propagate it, failing the whole fill, and
/// the accept path (c_api.zig) refuses the token with RESOURCE_LIMIT.
pub fn stateAlive(
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    st: *const parser.State,
    cache: *Cache,
    w: *work_mod.Work,
    side: *parser.Side,
) Error!bool {
    cache.side = side;
    cache.max_threads = st.max_threads;
    defer cache.releaseScratch();
    if (!g.needs_reachability) return explore(g, tok, st, 0, cache, w, side);
    if (cache.fill_budget == 0) {
        // The shared per-fill budget is spent (an earlier unprovable state
        // of this fill drained it). Certification is exact and
        // budget-independent (search() below re-certifies the same way), so
        // settle what it can for free and reserve ResourceLimit - which the
        // callers now propagate, failing the mask call (ADR-0005 D3) - for
        // the genuinely undecided.
        return switch (certifyState(g, st, side)) {
            .alive => true,
            .dead => false,
            .undecided => {
                recordFailure(g, st, side, .fail_budget0);
                return error.ResourceLimit;
            },
        };
    }
    var cap: usize = 32;
    // Shallow tiers get a small budget: their job is the quick win. A
    // deeper witness makes every tier-1 branch capped, and the DFS then
    // drains junk spellings - the budget caps that waste and the deeper
    // tier (with the completion-directed ordering) finds the witness in a
    // few expansions instead. A state the budget cannot settle comes back
    // as ResourceLimit, which fails the mask call (ADR-0005 D3).
    var budget: u64 = 1 << 5;
    // Top-level certification with the simulation certificate enabled
    // (in_search is false here): a concrete completing suffix settles the
    // state before any search tier runs.
    switch (certifyState(g, st, side)) {
        .alive => return true,
        .dead => return false,
        .undecided => {},
    }
    in_search = true;
    defer in_search = false;
    while (true) {
        cache.budget = @min(budget, cache.fill_budget);
        cache.budget_out = false;
        // An allocation failure inside the search (metered session memory,
        // e.g. the scratch pool growing to the depth cap) is a proof
        // failure, not a dead proof: report it as ResourceLimit, which the
        // call sites treat exactly like budget exhaustion.
        const v = search(g, tok, st, 0, cap, cache, w, side) catch |err| switch (err) {
            error.OutOfMemory => {
                recordFailure(g, st, side, .fail_oom);
                return error.ResourceLimit;
            },
            else => |e| return e,
        };
        switch (v) {
            .alive => return true,
            .dead => return false,
            .capped => {
                if (cap >= MAX_DEPTH_REACH) {
                    recordFailure(g, st, side, .fail_search);
                    return error.ResourceLimit;
                }
                cap = @min(cap * 8, MAX_DEPTH_REACH);
                budget = @min(budget * 16, cache.fill_budget);
            },
        }
    }
}

/// Feeds one token and recurses; a byte-illegal token is pruned (false).
fn feedAndExplore(
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    st: *const parser.State,
    bytes: []const u8,
    depth: usize,
    cache: *Cache,
    w: *work_mod.Work,
    side: *parser.Side,
) Error!bool {
    try w.charge(1);
    // Past the scratch pool the proof cannot complete: same loud failure
    // as the depth cap below (finite-literal path, no fallback).
    const dst = cache.stateAt(depth) catch |err| switch (err) {
        error.PoolFull => return error.ResourceLimit,
        error.OutOfMemory => return error.OutOfMemory,
    };
    // The next pool slot doubles as feed scratch: it is only claimed as
    // the destination by the recursive call, after the feed is done.
    const work = cache.stateAt(depth + 1) catch |err| switch (err) {
        error.PoolFull => return error.ResourceLimit,
        error.OutOfMemory => return error.OutOfMemory,
    };
    parser.releaseState(side, dst);
    parser.releaseState(side, work);
    parser.feedBytes(g, side, st, bytes, dst, work) catch |err| switch (err) {
        error.Parse => return false,
        else => |e| return e,
    };
    return explore(g, tok, dst, depth + 1, cache, w, side);
}

/// Depth-first search for the finite-literal path: the state space is
/// finite (every fed token consumes bytes of a finite language), so a
/// fully drained state is absolutely dead and the depth cap only turns a
/// hypothetical bug into an explicit error.
fn explore(
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    st: *const parser.State,
    depth: usize,
    cache: *Cache,
    w: *work_mod.Work,
    side: *parser.Side,
) Error!bool {
    if (parser.canEnd(g, st)) return true;
    if (depth >= MAX_DEPTH) return error.ResourceLimit;

    const kb = try cache.keyOf(st);
    if (cache.map.get(kb)) |e| switch (e.status) {
        .alive => return true,
        .dead, .in_progress => return false,
    };
    const owned = try cache.a.dupe(u8, kb);
    cache.map.put(cache.a, owned, .{ .status = .in_progress, .rem = 0 }) catch |e| {
        cache.a.free(owned);
        return e;
    };
    var clean = false;
    defer if (!clean) {
        if (cache.map.fetchRemove(owned)) |kv| cache.a.free(kv.key);
    };

    var id: u32 = 0;
    while (id < tok.vocab_size) : (id += 1) {
        if (tok.is_eos[id] or tok.is_special[id]) continue;
        const bytes = tok.bytes[id];
        if (bytes.len == 0) continue;
        if (try feedAndExplore(g, tok, st, bytes, depth, cache, w, side)) {
            cache.map.getPtr(owned).?.* = .{ .status = .alive, .rem = std.math.maxInt(u32) };
            clean = true;
            return true;
        }
    }
    cache.map.getPtr(owned).?.* = .{ .status = .dead, .rem = std.math.maxInt(u32) };
    clean = true;
    return false;
}

/// Search order class of a token's first byte (see search): closers, then
/// bytes that change a value machine's state, then nonzero digits (a zero
/// run like "0.000..." is open-ended while a nonzero digit forces the
/// machine towards a verdict), then everything else.
fn searchPhase(b: u8) u2 {
    return switch (b) {
        '"', '}', ']' => 0,
        'e', 'E', '.', '+', '-', ',', ':', ' ', '\t', '\n', '\r' => 1,
        '1'...'9' => 2,
        else => 3,
    };
}

/// Verdict of one bounded search tier: `alive` and `dead` settle the call
/// (`dead` from the top level means the whole space up to the cap drained
/// without a completion and without hitting the cap, i.e. absolutely
/// dead); `capped` means the depth cap cut off at least one open-ended
/// branch before it resolved, so the caller retries with a deeper cap.
const SearchVerdict = enum { alive, dead, capped };

fn memoPut(cache: *Cache, kb: []const u8, status: Status, rem: u32) Error!void {
    if (cache.map.count() >= cache.memo_cap) return;
    const gop = try cache.map.getOrPut(cache.a, kb);
    if (gop.found_existing) {
        // put() keeps the existing key; duping a fresh one would leak it.
        gop.value_ptr.* = .{ .status = status, .rem = rem };
        return;
    }
    errdefer std.debug.assert(cache.map.remove(kb));
    gop.key_ptr.* = try cache.a.dupe(u8, kb);
    gop.value_ptr.* = .{ .status = status, .rem = rem };
}

/// Feeds one token and recurses into search; a byte-illegal token is a
/// dead branch.
fn feedAndSearch(
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    st: *const parser.State,
    bytes: []const u8,
    depth: usize,
    cap: usize,
    cache: *Cache,
    w: *work_mod.Work,
    side: *parser.Side,
) Error!SearchVerdict {
    try w.charge(1);
    // A branch deeper than the scratch pool is cut like a budget cut: not
    // exhausted, so flag the tier and report capped (the top level ends in
    // ResourceLimit, which fails the mask call - ADR-0005 D3).
    const dst = cache.stateAt(depth) catch |err| switch (err) {
        error.PoolFull => {
            cache.budget_out = true;
            return .capped;
        },
        error.OutOfMemory => return error.OutOfMemory,
    };
    // The next pool slot doubles as feed scratch: it is only claimed as
    // the destination by the recursive call, after the feed is done.
    const work = cache.stateAt(depth + 1) catch |err| switch (err) {
        error.PoolFull => {
            cache.budget_out = true;
            return .capped;
        },
        error.OutOfMemory => return error.OutOfMemory,
    };
    parser.releaseState(side, dst);
    parser.releaseState(side, work);
    parser.feedBytes(g, side, st, bytes, dst, work) catch |err| switch (err) {
        error.Parse => return .dead,
        // A branch the engine cannot even represent (frame/thread cap -
        // the same ResourceLimit accept would raise on this feed) cannot
        // complete under this engine, so for reachability it is a proven
        // dead branch, not an undecided one.
        error.ResourceLimit => return .dead,
        else => |e| return e,
    };
    return search(g, tok, dst, depth + 1, cap, cache, w, side);
}

/// Bounded depth-first search over token sequences, asking "is a
/// completion reachable within cap - depth more tokens". A memo entry
/// stamped dead carries the horizon it was proven under (Entry.rem); a
/// stale stamp (proven under a shallower horizon than now needed) is
/// dropped and re-proven. A cycle back to an in-progress ancestor counts
/// as dead: if any completion exists, a cycle-free one does, and the
/// search covers all of those. States that neither resolve nor close
/// within the cap report .capped so stateAlive retries with a deeper cap
/// (iterative deepening) instead of diverging down an open-ended run.
fn search(
    g: *const grammar.Grammar,
    tok: *const tokenizer.Tokenizer,
    st: *const parser.State,
    depth: usize,
    cap: usize,
    cache: *Cache,
    w: *work_mod.Work,
    side: *parser.Side,
) Error!SearchVerdict {
    if (parser.canEnd(g, st)) return .alive;
    if (depth >= cap) return .capped;
    // A budget-cut branch is not exhausted: report capped (the tier is
    // retried deeper with a fresh budget), and flag the tier so no dead
    // stamp proven in it is kept (such a stamp would not be horizon-valid).
    if (cache.budget == 0) {
        cache.budget_out = true;
        return .capped;
    }
    cache.budget -= 1;
    cache.fill_budget -|= 1;
    const rem: u32 = @intCast(cap - depth);

    const kb = try cache.keyOf(st);
    if (cache.map.get(kb)) |e| {
        switch (e.status) {
            .alive => return .alive,
            .in_progress => return .dead,
            .dead => {
                if (e.rem >= rem) return .dead;
                // Stale stamp: deadness was proven only under a shallower
                // horizon. Drop the entry and re-prove deeper. The error
                // unwind below removes only keys this call inserted, and
                // losing the old entry on an error path costs a
                // recomputation, never soundness (the error fails the
                // whole mask call).
                const kv = cache.map.fetchRemove(kb).?;
                cache.a.free(kv.key);
            },
        }
    }

    // Exact residual certification first: most spec-v1 states are settled
    // without any search, and the verdict is horizon-independent.
    switch (certifyState(g, st, side)) {
        .alive => {
            try memoPut(cache, kb, .alive, std.math.maxInt(u32));
            return .alive;
        },
        .dead => {
            try memoPut(cache, kb, .dead, std.math.maxInt(u32));
            return .dead;
        },
        .undecided => {},
    }

    // Over the memo cap the state runs unmemoized: the in_progress mark is
    // a cycle shortcut only (termination comes from the depth cap), so
    // skipping it stays sound and bounds the session memory footprint.
    const memo_ok = cache.map.count() < cache.memo_cap;
    var owned: []u8 = &.{};
    var clean = true;
    if (memo_ok) {
        owned = try cache.a.dupe(u8, kb);
        cache.map.put(cache.a, owned, .{ .status = .in_progress, .rem = 0 }) catch |e| {
            cache.a.free(owned);
            return e;
        };
        clean = false;
    }
    defer if (!clean) {
        if (cache.map.fetchRemove(owned)) |kv| cache.a.free(kv.key);
    };

    var any_capped = false;
    // Completion-directed hints first (see keyHints): a plain depth-first
    // pass over open-ended key/content spellings diverges into unbounded
    // junk-key runs before it ever tries the bytes that spell a declared
    // required key, so those bytes are tried ahead of the phase classes.
    var req_hint = [_]bool{false} ** 256;
    var any_hint = [_]bool{false} ** 256;
    keyHints(g, side, st, &req_hint, &any_hint);
    var pruned = [_]bool{false} ** 256;
    const prune = keyByteClasses(g, side, st, &req_hint, &any_hint, &pruned);
    var ord: u8 = 0;
    while (ord < 6) : (ord += 1) {
        var id: u32 = 0;
        while (id < tok.vocab_size) : (id += 1) {
            if (tok.is_eos[id] or tok.is_special[id]) continue;
            const bytes = tok.bytes[id];
            if (bytes.len == 0) continue;
            const b0 = bytes[0];
            if (prune and bytes.len == 1 and !pruned[b0]) continue;
            const cls: u8 = if (req_hint[b0]) 0 else if (any_hint[b0]) 1 else 2 + @as(u8, searchPhase(b0));
            if (cls != ord) continue;
            const verdict = try feedAndSearch(g, tok, st, bytes, depth, cap, cache, w, side);
            switch (verdict) {
                .alive => {
                    if (memo_ok) cache.map.getPtr(owned).?.* = .{ .status = .alive, .rem = std.math.maxInt(u32) };
                    clean = true;
                    return .alive;
                },
                .capped => any_capped = true,
                .dead => {},
            }
        }
    }
    // Every branch resolved without a completion: dead for paths of up to
    // rem more tokens. A capped branch only failed to resolve beyond that
    // horizon, so the stamp stays valid; the .capped return drives the
    // deeper tier that re-proves under a wider horizon. Budget-cut tiers
    // keep no dead stamps (a budget-cut branch is not exhaustive); with
    // the stamp suppressed the in_progress mark must come out too (clean
    // stays false so the defer removes it) - a leaked in_progress entry
    // would read as "dead" (cycle rule) to every later lookup.
    if (memo_ok and !cache.budget_out) {
        cache.map.getPtr(owned).?.* = .{ .status = .dead, .rem = rem };
        clean = true;
    }
    return if (any_capped) .capped else .dead;
}

/// Completion-directed ordering hints for search: bytes that extend the
/// key currently being typed (an open_obj frame in key_str phase) towards
/// a declared property, an extra_required name, or a dependency-mandated
/// name. Required obligations come first (req set), optional declared
/// keys second (any set). Purely heuristic: every hinted candidate is
/// still verified by feeding, so a wrong hint costs a pruned branch,
/// never soundness.
fn keyHints(g: *const grammar.Grammar, side: *parser.Side, st: *const parser.State, req: *[256]bool, any: *[256]bool) void {
    @memset(req, false);
    @memset(any, false);
    for (st.threads[0..st.n]) |*t| {
        for (t.frames[0..t.len]) |*f| {
            if (f.* != .open_obj) continue;
            const of = &f.open_obj;
            if (of.phase != .key_str or of.key == 0) continue;
            // Mid-escape or mid-codepoint the raw bytes no longer map to
            // key bytes one-to-one; no hint.
            if (of.u.key.st != .normal or of.u.key.rem != 0) continue;
            const on = &g.node(of.node).open_obj;
            const kb = side.chunkAt(of.key).payload.items;
            for (on.props[of.next_idx..]) |p| {
                const kl = g.literalBytes(p.key);
                const inner = kl[1 .. kl.len - 2]; // strip quotes and ':'
                if (inner.len <= kb.len) continue;
                if (!std.mem.eql(u8, inner[0..kb.len], kb)) continue;
                (if (p.required) req else any)[inner[kb.len]] = true;
            }
            for (on.extra_required) |lit| {
                hintName(side, of, kb, g.literalBytes(lit), req);
            }
            for (on.deps) |dep| {
                if (dep.kind != .required) continue;
                if (!seenContainsC(side, of.seen, g.literalBytes(dep.trigger))) continue;
                for (dep.names) |nl| hintName(side, of, kb, g.literalBytes(nl), req);
            }
        }
    }
}

fn hintName(side: *parser.Side, of: *const parser.OpenObjFrame, kb: []const u8, raw: []const u8, req: *[256]bool) void {
    if (raw.len <= kb.len) return;
    if (!std.mem.eql(u8, raw[0..kb.len], kb)) return;
    if (seenContainsC(side, of.seen, raw)) return;
    req[raw[kb.len]] = true;
}

/// Marks bytes continuing a seen-set key that extends the prefix kb (the
/// duplicate-collision dispatch class).
fn seenPrefixBytes(side: *parser.Side, h: u32, kb: []const u8, cand: *[256]bool) void {
    if (h == 0) return;
    const items = side.chunkAt(h).payload.items;
    if (items.len < 4) return;
    const count = std.mem.readInt(u32, items[0..4], .little);
    var off: usize = 4;
    var i: u32 = 0;
    while (i < count) : (i += 1) {
        if (off + 4 > items.len) return;
        const len = std.mem.readInt(u32, items[off..][0..4], .little);
        off += 4;
        if (off + len > items.len) return;
        const key = items[off .. off + len];
        off += len;
        if (len > kb.len and std.mem.eql(u8, key[0..kb.len], kb)) cand[key[kb.len]] = true;
    }
}

/// Spelling-equivalence pruning for mid-key search states. When every
/// thread's top frame is an open_obj key machine in the clean content
/// state, and every such node is "dispatch-only" (the key constraint lands
/// at parser.openObjDispatch: no pattern, no propertyNames, no key/count
/// bounds, no deps, no capture tap), two content bytes that both leave the
/// typed prefix undeclared-unprefixed and seen-unprefixed in every frame
/// are dispatch-equivalent: junk keys impose only freshness constraints
/// (dispatch rejects duplicates and undeclared keys under an empty fallback
/// the same way for either spelling), so their completion sets correspond
/// under junk renaming. The search then feeds one representative per
/// dispatch class - the hint bytes (declared/extra continuations), '"' and
/// '\\' (key end and escapes), the seen-key continuations, and a single
/// fresh junk representative - instead of the whole alphabet. Returns
/// false (no pruning) as soon as any frame is of another shape.
fn keyByteClasses(g: *const grammar.Grammar, side: *parser.Side, st: *const parser.State, req: *const [256]bool, any: *const [256]bool, cand: *[256]bool) bool {
    if (st.n == 0) return false;
    for (st.threads[0..st.n]) |*t| {
        if (t.len == 0) return false;
        const f = &t.frames[t.len - 1];
        if (f.* != .open_obj) return false;
        const of = &f.open_obj;
        if (of.phase != .key_str or of.key == 0 or of.u.key.st != .normal or of.u.key.rem != 0) return false;
        const on = &g.node(of.node).open_obj;
        if (on.pattern_lit != null or on.pattern_dfa != null or on.prop_names != null or
            on.names_forbidden or on.deps.len != 0 or on.capture or on.ann_slots != 0 or
            on.key_min_len != 0 or on.key_max_len != grammar.UNBOUNDED or
            on.min_props != 0 or on.max_props != grammar.UNBOUNDED) return false;
    }
    for (0..256) |b| cand[b] = req[b] or any[b];
    cand['"'] = true;
    cand['\\'] = true;
    for (st.threads[0..st.n]) |*t| {
        const of = &t.frames[t.len - 1].open_obj;
        const kb = side.chunkAt(of.key).payload.items;
        seenPrefixBytes(side, of.seen, kb, cand);
    }
    // One fresh junk representative: equivalent to every other junk
    // spelling (fresh undeclared names are interchangeable).
    var b: u16 = 0x20;
    while (b < 0x7F) : (b += 1) {
        if (b == '"' or b == '\\') continue;
        if (!cand[b]) {
            cand[b] = true;
            break;
        }
    }
    return true;
}

// ---- residual certification (ADR-0005) -----------------------------------
//
// certifyState proves "a completion is reachable from this state" (alive)
// or "no completion is reachable" (dead) without feeding anything; it
// returns undecided when neither proof is available and the budgeted
// search must answer. Soundness requirements:
//   - alive: some continuation of allowed bytes completes (the frame
//     residuals below construct one, closed-form);
//   - dead: no continuation can complete (a frame whose value verdict can
//     never pass, or a comb group whose verdict no live branch can satisfy);
//   - undecided: no claim.
// A thread is certified alive only when EVERY frame is certified alive.
// Value machines and plain open objects certify their own residual; the
// remaining structural frames (seq/repeat/object/comb) stay undecided, so
// cross-frame composition is limited to the open-object rule, whose
// obligation is exactly the remaining required keys.
// A thread is certified dead when ANY frame is: that frame's completion
// is necessary for the thread's.

pub const Cert = enum { alive, dead, undecided };

/// ADR-0005 D3 certificate-coverage instrumentation: when stateAlive
/// cannot settle a state (ResourceLimit), the undecided frames of that
/// state are classified into a thread-local histogram so certificate
/// families are prioritized by measured impact. The counters are touched
/// only on the failure path (one extra certification of the failed
/// state); settled states pay nothing.
pub const UndReason = enum(u8) {
    fail_budget0, // shared per-fill search budget already spent
    fail_search, // every iterative-deepening tier capped
    fail_oom, // search allocation failure (metered memory)
    comb_group, // an undecided thread still carries an unproven comb frame
    openobj_track, // names_forbidden / track_keys / capture gate
    openobj_deps, // dependentSchemas/dependencies gate
    openobj_prop_names, // propertyNames gate
    openobj_key_bounds, // key min/max length gate
    openobj_min_props, // minProperties gate
    openobj_max_props, // maxProperties gate
    openobj_colon, // key completed, dispatched value of unknown emptiness
    openobj_witness, // no dispatch witness with a known-non-empty value
    openobj_keystr, // mid-key, no certified dispatch completion
    openobj_keyesc, // mid-key in an escape / mid-codepoint state
    choice, // lazy alternation with all alternatives of unknown emptiness
    seq,
    object,
    repeat,
    num_range_sat, // saturated exponent
    num_const_sat,
    int_num_sat,
    no_nonempty, // per-node emptiness bitset not computed
    openobj_gate, // gated open_obj (OR of the gate sub-reasons above)
    num_und, // num_mult / num_excl / str_pat / str_excl undecided
};

pub const UND_REASONS = @typeInfo(UndReason).@"enum".fields.len;
pub threadlocal var und_hist: [UND_REASONS]u64 = @splat(0);

threadlocal var und_rec: bool = false;
threadlocal var und_pend: [UND_REASONS]u64 = @splat(0);

/// True while the budgeted search is running: the simulation certificate
/// (synth.aliveBySim) runs only for top-level certifications, not per
/// search node.
threadlocal var in_search: bool = false;

fn note(r: UndReason) void {
    if (und_rec) und_pend[@intFromEnum(r)] += 1;
}

/// Re-certify the failed state with recording on and commit the pending
/// counts to the histogram. Certification is budget-independent, so this
/// replays exactly the proof attempt that just failed.
fn recordFailure(g: *const grammar.Grammar, st: *const parser.State, side: *parser.Side, r: UndReason) void {
    und_pend = @splat(0);
    und_rec = true;
    _ = certifyState(g, st, side);
    und_rec = false;
    und_pend[@intFromEnum(r)] += 1;
    for (&und_hist, und_pend) |*h, p| h.* += p;
}

fn combFullMaskC(n: usize) u64 {
    if (n >= 64) return std.math.maxInt(u64);
    return (@as(u64, 1) << @intCast(n)) - 1;
}

/// Local copy of parser.combBornOk (private there): is any verdict still
/// reachable over the set of not-yet-dead branches.
fn combBornOkC(cb: *const grammar.Comb, live: u64) bool {
    switch (cb.kind) {
        .oneof => return live != 0,
        .allof => return live == combFullMaskC(cb.branches.len),
        .ifelse => {
            const i_live = (live & 1) != 0;
            const t_ok = cb.branches[1] == grammar.COMB_NONE or (live & 2) != 0;
            const e_ok = cb.branches[2] == grammar.COMB_NONE or (live & 4) != 0;
            return (i_live and t_ok) or e_ok;
        },
    }
}

/// The state as a whole: alive when some thread is certified alive, dead
/// when every thread is certified dead (after propagating comb group doom
/// over certified-dead branches to a fixpoint), undecided otherwise.
///
/// Comb frames are certified as a group, not per frame (the verdict couples
/// the parallel branch threads of one instance). After the doom fixpoint, a
/// least-fixpoint aliveness pass proves groups from already-proven threads:
/// a group is alive only via a thread whose OTHER groups are proven, so the
/// proof bottoms out on comb-free threads and can never talk itself into
/// existence. See combGroupAlive for the per-kind rules.
pub fn certifyState(g: *const grammar.Grammar, st: *const parser.State, side: *parser.Side) Cert {
    var tc: [parser.MAX_THREADS_CAP]Cert = undefined;
    // base[i]: aggregate certificate of thread i's NON-comb frames.
    var base: [parser.MAX_THREADS_CAP]Cert = undefined;
    var n_dead: usize = 0;
    for (st.threads[0..st.n], 0..) |*t, i| {
        var c: Cert = .alive;
        var has_comb = false;
        for (t.frames[0..t.len]) |*f| {
            if (f.* == .comb) {
                has_comb = true;
                continue;
            }
            switch (certifyFrame(g, f, side)) {
                .dead => {
                    c = .dead;
                    break;
                },
                .undecided => c = .undecided,
                .alive => {},
            }
        }
        base[i] = c;
        tc[i] = if (has_comb and c == .alive) .undecided else c;
        if (tc[i] != .dead and (repeatContainsDoom(g, t) or openObjDepValueDoom(g, side, t))) {
            base[i] = .dead;
            tc[i] = .dead;
        }
        if (tc[i] == .dead) n_dead += 1;
    }
    // Comb group doom: a group whose born-ok check fails over the branches
    // not yet certified dead can never accept at the boundary (certified
    // dead branches never come back), so every thread of the instance is
    // dead. Mirrors parser.combSweep; killing may doom nested groups. The
    // allOf key-obligation doom (combDeadAllOfPass) is another kill source;
    // both run to a joint fixpoint.
    var progress = true;
    while (progress) {
        progress = false;
        var changed = true;
        while (changed) {
            changed = false;
            for (st.threads[0..st.n], 0..) |*t, i| {
                if (tc[i] == .dead) continue;
                for (t.frames[0..t.len]) |*f| {
                    if (f.* != .comb) continue;
                    const cb = &g.node(f.comb.node).comb;
                    const inst = f.comb.inst;
                    var live: u64 = 0;
                    for (st.threads[0..st.n], 0..) |*t2, j| {
                        if (tc[j] == .dead) continue;
                        for (t2.frames[0..t2.len]) |*f2| {
                            if (f2.* == .comb and f2.comb.inst == inst) {
                                live |= @as(u64, 1) << @intCast(f2.comb.branch);
                                break;
                            }
                        }
                    }
                    if (combBornOkC(cb, live)) {
                        // oneOf vote-link doom: two branch threads of the same
                        // instance whose remaining behaviors are equal receive
                        // identical bytes, so they survive, complete and vote
                        // in lockstep at every future boundary. If every live
                        // branch belongs to a linked group of size >= 2, the
                        // verdict can never be "exactly one" again: a boundary
                        // either sees a surviving linked group (>= 2 votes) or
                        // none at all. Linking is by frame identity, by equal
                        // canonical value, by structural node equality, or by
                        // exact residual equality for the leaf value machines
                        // (linkedThreads; e.g. oneOf{const 1, const 1.0} whose
                        // branches canonicalize to the same num_const node, or
                        // oneOf{"^(ab|c)$","^(ab|d)$"} after `"a`, where both
                        // branch residuals are {`b"`}).
                        if (cb.kind != .oneof or !oneOfLinkedDoom(g, side.a, st, tc[0..st.n], inst)) continue;
                    }
                    for (st.threads[0..st.n], 0..) |*t2, j| {
                        if (tc[j] == .dead) continue;
                        for (t2.frames[0..t2.len]) |*f2| {
                            if (f2.* == .comb and f2.comb.inst == inst) {
                                tc[j] = .dead;
                                n_dead += 1;
                                changed = true;
                                progress = true;
                                break;
                            }
                        }
                    }
                    break;
                }
            }
        }
        if (combDeadAllOfPass(g, side, st, tc[0..st.n], &n_dead)) progress = true;
    }
    // Comb group aliveness, least fixpoint: group proofs must be founded on
    // comb-free threads (via threadOkEx, which ignores only the group under
    // test), so mutually referencing groups cannot both prove themselves
    // out of thin air.
    var insts: [64]u32 = undefined;
    var inst_ok: [64]bool = @splat(false);
    var n_inst: usize = 0;
    for (st.threads[0..st.n], 0..) |*t, i| {
        if (tc[i] == .dead) continue;
        for (t.frames[0..t.len]) |*f| {
            if (f.* != .comb) continue;
            var known = false;
            for (insts[0..n_inst]) |in| {
                if (in == f.comb.inst) {
                    known = true;
                    break;
                }
            }
            if (!known and n_inst < insts.len) {
                insts[n_inst] = f.comb.inst;
                n_inst += 1;
            }
        }
    }
    // thread_ok[i]: every frame of thread i is certified alive (non-comb
    // frames by base, comb frames via a proven group). Initially only
    // comb-free threads qualify.
    var thread_ok: [parser.MAX_THREADS_CAP]bool = undefined;
    for (0..st.n) |i| thread_ok[i] = threadOkEx(st, tc[0..st.n], base[0..st.n], insts[0..n_inst], inst_ok[0..n_inst], i, NONE_INST);
    var grown = true;
    while (grown) {
        grown = false;
        for (insts[0..n_inst], 0..) |inst, k| {
            if (inst_ok[k]) continue;
            if (combGroupAlive(g, st, tc[0..st.n], base[0..st.n], insts[0..n_inst], inst_ok[0..n_inst], inst, side)) {
                inst_ok[k] = true;
                grown = true;
            }
        }
        for (0..st.n) |i| {
            if (thread_ok[i]) continue;
            if (threadOkEx(st, tc[0..st.n], base[0..st.n], insts[0..n_inst], inst_ok[0..n_inst], i, NONE_INST)) {
                thread_ok[i] = true;
                grown = true;
            }
        }
    }
    for (0..st.n) |i| {
        if (thread_ok[i]) tc[i] = .alive;
    }
    for (tc[0..st.n]) |c| {
        if (c == .alive) return .alive;
    }
    if (n_dead != st.n) {
        // Simulation certificate (synth.zig): a synthesized completing
        // suffix verified by the real parser is a concrete reachability
        // witness. Top-level calls only (not per search node).
        if (!in_search and synth.aliveBySim(g, st, side)) return .alive;
        // Attribute the undecided verdict: an undecided thread still
        // carrying a comb frame means a group proof was missing.
        for (st.threads[0..st.n], 0..) |*t, i| {
            if (tc[i] != .undecided) continue;
            for (t.frames[0..t.len]) |*f| {
                if (f.* == .comb) {
                    note(.comb_group);
                    break;
                }
            }
        }
        return .undecided;
    }
    return .dead;
}

/// Sentinel "instance" for threadOkEx when no group is exempted.
const NONE_INST: u32 = std.math.maxInt(u32);

/// Thread i is fully alive-certified, treating any comb frame of instance
/// `ex` as already proven (used by combGroupAlive to break the circularity
/// between a group and the threads carrying its frame). A comb frame of an
/// instance beyond the instance table cap stays unproven - conservative.
fn threadOkEx(
    st: *const parser.State,
    tc: []const Cert,
    base: []const Cert,
    insts: []const u32,
    inst_ok: []const bool,
    i: usize,
    ex: u32,
) bool {
    if (tc[i] == .dead or base[i] != .alive) return false;
    for (st.threads[i].frames[0..st.threads[i].len]) |*f| {
        if (f.* != .comb) continue;
        if (f.comb.inst == ex) continue;
        var ok = false;
        for (insts, 0..) |in, k| {
            if (in == f.comb.inst) {
                ok = inst_ok[k];
                break;
            }
        }
        if (!ok) return false;
    }
    return true;
}

/// Cheap exact aliveness certificates for one comb instance (spec-v1).
/// `tc`/`base` are the post-doom per-thread certs (dead threads never come
/// back) and the non-comb aggregates. Only rules with a SINGLE-witness
/// proof are offered: the branches of an instance consume the same bytes,
/// so branch-wise aliveness does not compose into a group witness
/// (allOf{"ab","xy"}/{"ac","xy"}: each branch is alive after `"a`, yet the
/// intersection is {"xy"} and the state is dead - see the "allof enum
/// intersection" test). The rules:
///  - oneOf: exactly one branch still has live threads (the others are
///    certified dead, hence reject under every continuation) and that
///    branch has a fully-proven thread. Its witness completes exactly one
///    branch, so the boundary verdict accepts. Sound.
///  - if/then/else: if the `if` branch is fully dead, the verdict reduces
///    to the else applicator (true when absent); if the then applicator is
///    absent, a proven `if` thread suffices. Both avoid the cross-branch
///    witness problem.
///  - allOf and the remaining if/then/else shapes get no cheap rule here;
///    the wrapper combGroupAlive adds the pristine-residue witness rule.
fn combGroupAliveCheap(
    g: *const grammar.Grammar,
    st: *const parser.State,
    tc: []const Cert,
    base: []const Cert,
    insts: []const u32,
    inst_ok: []const bool,
    target: u32,
) bool {
    var cb: ?*const grammar.Comb = null;
    for (st.threads[0..st.n]) |*t| {
        for (t.frames[0..t.len]) |*f| {
            if (f.* == .comb and f.comb.inst == target) {
                cb = &g.node(f.comb.node).comb;
                break;
            }
        }
        if (cb != null) break;
    }
    const c = cb orelse return false;
    switch (c.kind) {
        .allof => return false,
        .oneof => {
            var live_branch: ?u32 = null;
            for (st.threads[0..st.n], 0..) |*t, i| {
                if (tc[i] == .dead) continue;
                for (t.frames[0..t.len]) |*f| {
                    if (f.* == .comb and f.comb.inst == target) {
                        if (live_branch == null) {
                            live_branch = f.comb.branch;
                        } else if (live_branch.? != f.comb.branch) {
                            return false;
                        }
                        break;
                    }
                }
            }
            const b = live_branch orelse return false;
            for (st.threads[0..st.n], 0..) |*t, i| {
                if (!threadOkEx(st, tc, base, insts, inst_ok, i, target)) continue;
                for (t.frames[0..t.len]) |*f| {
                    if (f.* == .comb and f.comb.inst == target and f.comb.branch == b) return true;
                }
            }
            return false;
        },
        .ifelse => {
            const then_none = c.branches[1] == grammar.COMB_NONE;
            const else_none = c.branches[2] == grammar.COMB_NONE;
            var if_live = false;
            var ok_branch = [_]bool{false} ** 3;
            for (st.threads[0..st.n], 0..) |*t, i| {
                var br: ?u32 = null;
                for (t.frames[0..t.len]) |*f| {
                    if (f.* == .comb and f.comb.inst == target) {
                        br = f.comb.branch;
                        break;
                    }
                }
                const b = br orelse continue;
                if (tc[i] != .dead and b == 0) if_live = true;
                if (b < 3 and threadOkEx(st, tc, base, insts, inst_ok, i, target)) ok_branch[b] = true;
            }
            if (!if_live) {
                // ~if is certain: the verdict is the else applicator.
                if (else_none) return ok_branch[0] or ok_branch[1] or ok_branch[2];
                return ok_branch[2];
            }
            if (then_none) return ok_branch[0];
            return false;
        },
    }
}

/// Frame is in its birth state (see parser.framePristine, which owns the
/// invariant argument).
fn framePristine(f: *const parser.Frame) bool {
    return parser.framePristine(f);
}

/// True when every thread carrying comb instance `inst` still has its
/// branch parse in the birth configuration: the comb frame is not on top
/// (a completed branch votes with its already-typed value, not with a
/// fresh witness) and every frame above it is pristine. Certified-dead
/// threads are NOT exempted: a branch whose live threads are all pristine
/// but whose doomed threads consumed bytes would vote per its residue, not
/// per its node language, and witness() verifies votes against the node
/// languages only. See framePristine.
fn combResiduesPristine(st: *const parser.State, inst: u32) bool {
    for (st.threads[0..st.n]) |*t| {
        const d = combFrameIdx(t, inst) orelse continue;
        if (t.len <= d + 1) return false;
        for (t.frames[d + 1 .. t.len]) |*f| {
            if (!framePristine(f)) return false;
        }
    }
    return true;
}

/// Comb group aliveness (spec-v1): the cheap single-witness rules of
/// combGroupAliveCheap, plus the pristine-residue witness rule: when every
/// thread of the instance has consumed no value bytes since birth
/// (combResiduesPristine), each branch's residual equals its node language
/// exactly, so the boundary votes on any completion are the per-branch
/// acceptance votes of the node languages. A self-verified witness of the
/// comb node (witset.witness constructs a value and re-checks the full
/// vote vector over ALL branches with acceptsValue - never pairwise, so
/// the 3+-branch exclusivity trap does not apply) then completes the
/// group: feeding its spelling makes every live branch vote exactly as
/// verified, and the verdict accepts. This covers allOf and multi-live-
/// branch oneOf at value start; mid-value states fall through to the
/// budgeted search (ADR-0005 D3). OOM inside witness leaves the group
/// unproven - conservative.
fn combGroupAlive(
    g: *const grammar.Grammar,
    st: *const parser.State,
    tc: []const Cert,
    base: []const Cert,
    insts: []const u32,
    inst_ok: []const bool,
    target: u32,
    side: *parser.Side,
) bool {
    if (combGroupAliveCheap(g, st, tc, base, insts, inst_ok, target)) return true;
    var cb_node: ?grammar.NodeId = null;
    for (st.threads[0..st.n]) |*t| {
        for (t.frames[0..t.len]) |*f| {
            if (f.* == .comb and f.comb.inst == target) {
                cb_node = f.comb.node;
                break;
            }
        }
        if (cb_node != null) break;
    }
    const node = cb_node orelse return false;
    if (!combResiduesPristine(st, target)) return false;
    var arena = std.heap.ArenaAllocator.init(side.a);
    defer arena.deinit();
    return witset.witness(witset.viewOf(g), arena.allocator(), node) != null;
}

/// One pass of the allOf key-obligation doom over every allOf instance
/// present in the state; kills (certifies dead) every thread of a doomed
/// instance and returns whether anything changed. See combDeadAllOf.
fn combDeadAllOfPass(g: *const grammar.Grammar, side: *parser.Side, st: *const parser.State, tc: []Cert, n_dead: *usize) bool {
    var killed_any = false;
    var insts: [64]u32 = undefined;
    var n_inst: usize = 0;
    for (st.threads[0..st.n], 0..) |*t, i| {
        if (tc[i] == .dead) continue;
        for (t.frames[0..t.len]) |*f| {
            if (f.* != .comb) continue;
            const cb = &g.node(f.comb.node).comb;
            if (cb.kind != .allof) continue;
            const inst = f.comb.inst;
            var known = false;
            for (insts[0..n_inst]) |in| {
                if (in == inst) {
                    known = true;
                    break;
                }
            }
            if (!known and n_inst < insts.len) {
                insts[n_inst] = inst;
                n_inst += 1;
            }
        }
    }
    for (insts[0..n_inst]) |inst| {
        if (!combDeadAllOf(g, side, st, tc, inst)) continue;
        for (st.threads[0..st.n], 0..) |*t, i| {
            if (tc[i] == .dead) continue;
            for (t.frames[0..t.len]) |*f| {
                if (f.* == .comb and f.comb.inst == inst) {
                    tc[i] = .dead;
                    n_dead.* += 1;
                    killed_any = true;
                    break;
                }
            }
        }
    }
    return killed_any;
}

/// allOf key-obligation doom (spec-v1 cheap certificate). Suppose every
/// live thread of branch b0 provably owes a future dispatch of the raw key
/// spelling K: it carries, above its comb frame, an open_obj frame with K
/// still outstanding (an unseen extra_required name or a required declared
/// prop at/after next_idx; a mid-key frame counts only when the typed raw
/// prefix can no longer spell K). Every joint completion of the group is a
/// continuation in which some b0 thread survives end-to-end, so the byte
/// run `"K"` (raw) is fed at some point with that thread at a key boundary.
/// If every live thread of every OTHER branch carries, above the same comb
/// instance, exactly one container frame and it is a closed .object whose
/// remaining key candidates (idx, or cur when mid-key/value) exclude K -
/// with only escape-free value machines (literal/number family) above it -
/// then feeding `"K"` kills every such thread: the closed machine accepts
/// only its remaining declared keys at a key position, a number/literal
/// value machine dies at the closing quote or the following colon, and the
/// object completing before K fails the allOf verdict because no obligated
/// b0 thread can vote. (String-family frames above the object could absorb
/// the key bytes as escaped content, so they defer.) The group is dead.
/// Narrow on purpose: any other shape defers to the budgeted search.
fn combDeadAllOf(g: *const grammar.Grammar, side: *parser.Side, st: *const parser.State, tc: []const Cert, inst: u32) bool {
    // Candidate obligations (branch b0, key K), capped; more defers.
    var cand_branch: [8]u32 = undefined;
    var cand_key: [8][]const u8 = undefined;
    var n_cand: usize = 0;
    for (st.threads[0..st.n], 0..) |*t, i| {
        if (tc[i] == .dead) continue;
        const d0 = combFrameIdx(t, inst) orelse continue;
        const br = t.frames[d0].comb.branch;
        for (t.frames[d0 + 1 .. t.len]) |*f| {
            if (f.* != .open_obj) continue;
            const of = &f.open_obj;
            if (of.phase == .key_str) {
                // The key in progress may be spelling K right now; only a
                // clean prefix that can no longer extend to K keeps the
                // obligation outstanding.
                if (of.u.key.st != .normal or of.u.key.rem != 0) continue;
            }
            const on = &g.node(of.node).open_obj;
            const kb: []const u8 = if (of.phase == .key_str) side.chunkAt(of.key).payload.items else "";
            for (on.extra_required) |lit| {
                const name = g.literalBytes(lit);
                if (seenContainsC(side, of.seen, name)) continue;
                if (of.phase == .key_str and std.mem.startsWith(u8, name, kb)) continue;
                if (n_cand >= cand_key.len) return false;
                cand_branch[n_cand] = br;
                cand_key[n_cand] = name;
                n_cand += 1;
            }
            for (on.props[of.next_idx..]) |p| {
                if (!p.required) continue;
                const kl = g.literalBytes(p.key);
                const name = kl[1 .. kl.len - 2];
                if (of.phase == .key_str and std.mem.startsWith(u8, name, kb)) continue;
                if (n_cand >= cand_key.len) return false;
                cand_branch[n_cand] = br;
                cand_key[n_cand] = name;
                n_cand += 1;
            }
        }
    }
    for (cand_branch[0..n_cand], cand_key[0..n_cand]) |b0, k| {
        // (i) every live thread of branch b0 owes K;
        // (ii) every live thread of every other branch blocks K.
        var ok = true;
        for (st.threads[0..st.n], 0..) |*t, i| {
            if (tc[i] == .dead) continue;
            const d0 = combFrameIdx(t, inst) orelse continue;
            const br = t.frames[d0].comb.branch;
            if (br == b0) {
                if (!threadOwesKey(g, side, t, d0 + 1, k)) {
                    ok = false;
                    break;
                }
            } else {
                if (!threadBlocksKey(g, t, d0 + 1, k)) {
                    ok = false;
                    break;
                }
            }
        }
        if (ok) return true;
    }
    return false;
}

fn combFrameIdx(t: *const parser.Thread, inst: u32) ?usize {
    for (t.frames[0..t.len], 0..) |*f, d| {
        if (f.* == .comb and f.comb.inst == inst) return d;
    }
    return null;
}

/// Thread owes a future dispatch of raw key K: some open_obj frame at/above
/// `from` still has K outstanding (see combDeadAllOf).
fn threadOwesKey(g: *const grammar.Grammar, side: *parser.Side, t: *const parser.Thread, from: usize, k: []const u8) bool {
    for (t.frames[from..t.len]) |*f| {
        if (f.* != .open_obj) continue;
        const of = &f.open_obj;
        const on = &g.node(of.node).open_obj;
        var kb: []const u8 = "";
        if (of.phase == .key_str) {
            if (of.u.key.st != .normal or of.u.key.rem != 0) continue;
            kb = side.chunkAt(of.key).payload.items;
        }
        for (on.extra_required) |lit| {
            const name = g.literalBytes(lit);
            if (seenContainsC(side, of.seen, name)) continue;
            if (of.phase == .key_str and std.mem.startsWith(u8, name, kb)) continue;
            if (std.mem.eql(u8, name, k)) return true;
        }
        for (on.props[of.next_idx..]) |p| {
            if (!p.required) continue;
            const kl = g.literalBytes(p.key);
            const name = kl[1 .. kl.len - 2];
            if (of.phase == .key_str and std.mem.startsWith(u8, name, kb)) continue;
            if (std.mem.eql(u8, name, k)) return true;
        }
    }
    return false;
}

/// Thread provably dies on any fed raw key spelling `"K"` (see
/// combDeadAllOf): its only container frame at/above `from` is a closed
/// object whose remaining candidates exclude K, with only escape-free value
/// machines above it.
fn threadBlocksKey(g: *const grammar.Grammar, t: *const parser.Thread, from: usize, k: []const u8) bool {
    var containers: usize = 0;
    var obj: ?*const parser.Frame = null;
    for (t.frames[from..t.len]) |*f| {
        switch (f.*) {
            .object => {
                containers += 1;
                obj = f;
            },
            // Escape-free value machines: a '"' either mismatches the fixed
            // spelling or closes the number at a boundary, and the following
            // bytes then die in the object frame below.
            .literal, .lit_trie, .int_v, .num_v, .int_num, .not_int_num, .num_const, .num_excl, .num_range, .num_mult => {},
            // String machines (escape desync), lazy choice (may expand into
            // a container), and nested containers defeat the argument.
            else => return false,
        }
    }
    const f = obj orelse return false;
    if (containers != 1) return false;
    const props = g.node(f.object.node).object.props;
    const start: usize = switch (f.object.phase) {
        // idx is clobbered with the key-literal offset in key_lit and
        // restored to cur + 1 on entering sep; include the current prop
        // (its dispatch may be in progress).
        .key_lit, .value => f.object.cur,
        else => f.object.idx,
    };
    if (start > props.len) return false;
    for (props[start..]) |p| {
        const kl = g.literalBytes(p.key);
        if (std.mem.eql(u8, kl[1 .. kl.len - 2], k)) return false;
    }
    return true;
}

/// Lockstep linking of two branch threads of the oneOf instance `inst`
/// (spec-v1): true when equal remaining behavior is PROVEN, so the
/// threads accept identically under every future continuation and vote in
/// lockstep. The stacks must agree on every frame except the branch slot of
/// `inst` itself. Frame equality is exact byte equality of the frame image
/// (frames are zero-filled on push, so the byte image compares exactly),
/// relaxed per kind where a cheaper equality already implies equal
/// remaining behavior: for num_const frames, where equal canonical values
/// suffice (const 1 and const 1.0 compile to distinct nodes with the same
/// canonical decimal), for the node id of repeat and choice frames, where
/// structural node equality (grammar.nodesEquiv: equal structure, same
/// language) suffices, and for the leaf value machines (literal, lit_trie,
/// str_pat), where exact residual equality of the remaining byte language
/// suffices (residualLinked*). Anything else stays byte-exact.
fn linkedThreads(g: *const grammar.Grammar, alloc: std.mem.Allocator, inst: u32, a: *const parser.Thread, b: *const parser.Thread) bool {
    if (a.len != b.len) return false;
    for (a.frames[0..a.len], b.frames[0..b.len]) |*fa, *fb| {
        const ta: std.meta.Tag(parser.Frame) = fa.*;
        const tb: std.meta.Tag(parser.Frame) = fb.*;
        if (ta != tb) {
            // Cross-kind lockstep: a num_v frame and an int_num frame whose
            // mantissa is all zeros, both in the same exponent state, accept
            // exactly the same continuations (the automata share the exponent
            // transitions byte-for-byte, and a zero mantissa times any
            // exponent is integral, so intNumValueOk never rejects). This is
            // the oneOf{number, integer} `-0e` overlap: both branches vote
            // accept together or die together, so the verdict is never
            // "exactly one".
            if (!numExpLockstep(fa, fb)) return false;
            continue;
        }
        if (fa.* == .comb) {
            if (fa.comb.node != fb.comb.node or fa.comb.inst != fb.comb.inst) return false;
            if (fa.comb.inst != inst and fa.comb.branch != fb.comb.branch) return false;
        } else if (fa.* == .num_const) {
            const na = g.node(fa.num_const.node).num_const;
            const nb = g.node(fb.num_const.node).num_const;
            if (na.neg != nb.neg or na.exp10 != nb.exp10 or
                !std.mem.eql(u8, na.digits, nb.digits)) return false;
            var ca = fa.num_const;
            var cb = fb.num_const;
            ca.node = 0;
            cb.node = 0;
            if (!std.mem.eql(u8, std.mem.asBytes(&ca), std.mem.asBytes(&cb))) return false;
        } else if (fa.* == .repeat or fa.* == .choice) {
            // Interchangeable subschemas compiled twice (oneOf branches over
            // the same "any" tower): frames whose state bytes agree and
            // whose nodes are structurally equal accept identical byte runs
            // from here, so they vote in lockstep like identical frames.
            var ba = std.mem.asBytes(fa).*;
            var bb = std.mem.asBytes(fb).*;
            std.mem.writeInt(u32, ba[0..4], 0, .little);
            std.mem.writeInt(u32, bb[0..4], 0, .little);
            if (!std.mem.eql(u8, &ba, &bb)) return false;
            const na = std.mem.readInt(u32, std.mem.asBytes(fa)[0..4], .little);
            const nb = std.mem.readInt(u32, std.mem.asBytes(fb)[0..4], .little);
            if (!g.nodesEquiv(alloc, na, nb)) return false;
        } else if (fa.* == .literal) {
            // Residual linking (spec-v1): the residual of a literal frame
            // is the remaining suffix of its spelling; equal suffixes accept
            // identically under every continuation. This is what settles
            // overlapping oneOf branches byte-identically compiled from
            // different nodes (enum literals sharing a prefix).
            if (!residualLinkedLiteral(g, fa, fb)) return false;
        } else if (fa.* == .lit_trie) {
            // Residual linking: equal subtrie languages at the two positions
            // (structural comparison of the remaining prefix trees) accept
            // identically from here.
            if (!residualLinkedTrie(g, alloc, fa, fb)) return false;
        } else if (fa.* == .str_pat) {
            // Residual linking: equal length windows plus right-language
            // equality of the two DFA states (bounded greatest-fixpoint
            // check) accept identically from here - the
            // oneOf{"^(ab|c)$","^(ab|d)$} overlap after `"a` dies here
            // without touching the search budget.
            if (!residualLinkedStrPat(g, alloc, fa, fb)) return false;
        } else if (!std.mem.eql(u8, std.mem.asBytes(fa), std.mem.asBytes(fb))) return false;
    }
    return true;
}

/// Cross-kind lockstep pair (spec-v1): one frame num_v, the other int_num
/// with an all-zero mantissa (nz == false), both in the same exponent state.
fn numExpLockstep(fa: *const parser.Frame, fb: *const parser.Frame) bool {
    const nv = if (fa.* == .num_v) fa.num_v else if (fb.* == .num_v) fb.num_v else return false;
    const in = if (fa.* == .int_num) fa.int_num else if (fb.* == .int_num) fb.int_num else return false;
    if (nv.st != in.st or in.nz) return false;
    return switch (nv.st) {
        .exp, .exp_sign, .exp_digits => true,
        else => false,
    };
}

/// literal frames: residual-equal iff the remaining byte suffixes of the two
/// spellings are equal.
fn residualLinkedLiteral(g: *const grammar.Grammar, fa: *const parser.Frame, fb: *const parser.Frame) bool {
    const a = fa.literal;
    const b = fb.literal;
    const ba = g.literalBytes(g.node(a.node).literal);
    const bb = g.literalBytes(g.node(b.node).literal);
    if (a.off > ba.len or b.off > bb.len) return false;
    return std.mem.eql(u8, ba[a.off..], bb[b.off..]);
}

/// Cap on the memo size of one residual-equality proof; past the cap the
/// proof gives up (not linked), deferring to the budgeted search.
const RESIDUAL_EQ_CAP = 2048;

/// lit_trie frames: residual-equal iff the subtries at the two positions
/// admit the same completions. A prefix trie is a tree, so language equality
/// of subtries is exactly structural equality (terminal flag plus the sorted
/// edge lists, recursively); the memo keeps shared subtrees linear.
fn residualLinkedTrie(g: *const grammar.Grammar, alloc: std.mem.Allocator, fa: *const parser.Frame, fb: *const parser.Frame) bool {
    const a = fa.lit_trie;
    const b = fb.lit_trie;
    if (a.gnode == b.gnode and a.node == b.node) return true;
    const ta = g.node(a.gnode).lit_trie;
    const tb = g.node(b.gnode).lit_trie;
    var memo: std.AutoHashMapUnmanaged([4]u32, void) = .{};
    defer memo.deinit(alloc);
    return trieSubEquiv(&ta, a.node, a.gnode, &tb, b.node, b.gnode, alloc, &memo) catch false;
}

fn trieSubEquiv(ta: *const grammar.LitTrie, na: u32, ga: u32, tb: *const grammar.LitTrie, nb: u32, gb: u32, alloc: std.mem.Allocator, memo: *std.AutoHashMapUnmanaged([4]u32, void)) error{ OutOfMemory, Overflow }!bool {
    if (na >= ta.nodes.len or nb >= tb.nodes.len) return false;
    if (na == nb and ga == gb) return true;
    const key = [4]u32{ ga, na, gb, nb };
    if (memo.contains(key)) return true; // tree: a re-visited pair is proven
    if (memo.count() >= RESIDUAL_EQ_CAP) return error.Overflow;
    try memo.put(alloc, key, {});
    const xa = ta.nodes[na];
    const xb = tb.nodes[nb];
    if (xa.terminal != xb.terminal or xa.edge_len != xb.edge_len) return false;
    var i: u32 = 0;
    while (i < xa.edge_len) : (i += 1) {
        const ea = ta.edges[xa.edge_off + i];
        const eb = tb.edges[xb.edge_off + i];
        if (ea.byte != eb.byte) return false;
        if (!try trieSubEquiv(ta, ea.child, ga, tb, eb.child, gb, alloc, memo)) return false;
    }
    return true;
}

/// str_pat frames: link only quiescent frames (state open/normal, no pending
/// UTF-8 continuation - mid-character and mid-escape states keep their exact
/// byte-pending ranges, which the generic frame-byte compare covers).
/// Residual-equal iff the codepoint windows agree (same count and node
/// bounds) and the two DFA states have equal right languages: the str
/// machine layer (quotes, escapes, UTF-8 spelling) is identical for both
/// frames, so equal codepoint-level residuals give equal byte-level ones.
fn residualLinkedStrPat(g: *const grammar.Grammar, alloc: std.mem.Allocator, fa: *const parser.Frame, fb: *const parser.Frame) bool {
    const a = fa.str_pat;
    const b = fb.str_pat;
    if (a.state != b.state) return false;
    switch (a.state) {
        .open, .normal => {},
        else => return false,
    }
    if (a.rem != 0 or b.rem != 0) return false;
    if (a.count != b.count) return false;
    const na = g.node(a.node).str_pat;
    const nb = g.node(b.node).str_pat;
    if (na.min_len != nb.min_len or na.max_len != nb.max_len) return false;
    if (na.dfa == nb.dfa and a.dfa == b.dfa) return true;
    return dfaResidualEquiv(na.dfa, a.dfa, nb.dfa, b.dfa, alloc) catch false;
}

/// Right-language equality of DFA states sa of da and sb of db (the two DFAs
/// may differ): codepoints not covered by a transition read as the dead
/// state 0 (matching Dfa.feed), out-of-range states normalize to 0. The
/// equivalence is the greatest fixpoint over the reachable state pairs: BFS
/// collects the pair graph (capped at RESIDUAL_EQ_CAP pairs - past the cap
/// the proof gives up, deferring to the budgeted search), pairs with
/// mismatching acceptance are unequal, and unequality propagates to every
/// pair with an unequal child until quiescence. Fixpoint propagation (not a
/// recursive assume-equal) keeps cycles sound: a cyclic pair is equal only
/// when no unequal pair is reachable from it.
fn dfaResidualEquiv(da: *const pattern.Dfa, sa_in: u32, db: *const pattern.Dfa, sb_in: u32, alloc: std.mem.Allocator) error{ OutOfMemory, Overflow }!bool {
    if (da.states.len == 0 or db.states.len == 0)
        return da.isAccept(sa_in) == db.isAccept(sb_in);
    var pairs: std.ArrayListUnmanaged([2]u32) = .{};
    defer pairs.deinit(alloc);
    var idx: std.AutoHashMapUnmanaged([2]u32, u32) = .{};
    defer idx.deinit(alloc);
    var kids: std.ArrayListUnmanaged(std.ArrayListUnmanaged([2]u32)) = .{};
    defer {
        for (kids.items) |*k| k.deinit(alloc);
        kids.deinit(alloc);
    }
    const norm = struct {
        fn f(d: *const pattern.Dfa, s: u32) u32 {
            return if (s < d.states.len) s else 0;
        }
    }.f;
    const root = [2]u32{ norm(da, sa_in), norm(db, sb_in) };
    try pairs.append(alloc, root);
    try idx.put(alloc, root, 0);
    try kids.append(alloc, .{});
    // BFS over the pair graph.
    var head: usize = 0;
    while (head < pairs.items.len) : (head += 1) {
        const p = pairs.items[head];
        const ta = da.states[p[0]].trans;
        const tb = db.states[p[1]].trans;
        // Sweep the codepoint domain: at every segment the pair of target
        // states is a child pair. Segment boundaries are the interval edges
        // of both transition lists; each iteration advances pos strictly.
        const MAXCP: u32 = 0x110000;
        var i: usize = 0;
        var j: usize = 0;
        var pos: u32 = 0;
        while (pos < MAXCP) {
            while (i < ta.len and ta[i].hi < pos) i += 1;
            while (j < tb.len and tb[j].hi < pos) j += 1;
            const x = if (i < ta.len) ta[i] else null;
            const y = if (j < tb.len) tb[j] else null;
            if (x == null and y == null) break;
            const in_x = x != null and x.?.lo <= pos;
            const in_y = y != null and y.?.lo <= pos;
            const child = [2]u32{ if (in_x) x.?.to else 0, if (in_y) y.?.to else 0 };
            const gop = try idx.getOrPut(alloc, child);
            if (!gop.found_existing) {
                if (pairs.items.len >= RESIDUAL_EQ_CAP) return error.Overflow;
                gop.value_ptr.* = @intCast(pairs.items.len);
                try pairs.append(alloc, child);
                try kids.append(alloc, .{});
            }
            try kids.items[head].append(alloc, child);
            var next: u32 = MAXCP;
            if (in_x) next = @min(next, x.?.hi + 1);
            if (in_y) next = @min(next, y.?.hi + 1);
            if (!in_x and x != null) next = @min(next, x.?.lo);
            if (!in_y and y != null) next = @min(next, y.?.lo);
            if (next <= pos) return error.Overflow; // unreachable: sorted disjoint ranges
            pos = next;
        }
    }
    // Seeds: acceptance mismatch. Then propagate to a fixpoint.
    var bad = try alloc.alloc(bool, pairs.items.len);
    defer alloc.free(bad);
    for (pairs.items, 0..) |p, k| {
        bad[k] = da.isAccept(p[0]) != db.isAccept(p[1]);
    }
    var changed = true;
    while (changed) {
        changed = false;
        for (pairs.items, 0..) |_, k| {
            if (bad[k]) continue;
            for (kids.items[k].items) |ch| {
                if (bad[idx.get(ch).?]) {
                    bad[k] = true;
                    changed = true;
                    break;
                }
            }
        }
    }
    return !bad[0];
}

/// See the vote-link rule at the call site: every live branch thread of
/// the oneOf instance is frame-linked to at least one other live branch
/// thread of the instance.
fn oneOfLinkedDoom(g: *const grammar.Grammar, alloc: std.mem.Allocator, st: *const parser.State, tc: []const Cert, inst: u32) bool {
    var branches: [parser.MAX_THREADS_CAP]usize = undefined;
    var nb: usize = 0;
    for (st.threads[0..st.n], 0..) |*t, j| {
        if (tc[j] == .dead) continue;
        for (t.frames[0..t.len]) |*f| {
            if (f.* == .comb and f.comb.inst == inst) {
                branches[nb] = j;
                nb += 1;
                break;
            }
        }
    }
    if (nb < 2) return false;
    var gid: [parser.MAX_THREADS_CAP]usize = undefined;
    for (branches[0..nb], 0..) |tj, k| {
        gid[k] = k;
        for (branches[0..k], 0..) |tp, p| {
            if (linkedThreads(g, alloc, inst, &st.threads[tj], &st.threads[tp])) {
                gid[k] = p;
                break;
            }
        }
    }
    for (0..nb) |k| {
        var cnt: usize = 0;
        for (0..nb) |m| {
            if (gid[m] == gid[k]) cnt += 1;
        }
        if (cnt < 2) return false;
    }
    return true;
}

fn certifyFrame(g: *const grammar.Grammar, f: *const parser.Frame, side: *parser.Side) Cert {
    const c = certifyFrameInner(g, f, side);
    if (c == .undecided) switch (f.*) {
        // comb frames are always undecided here (certified as a group in
        // certifyState); open_obj notes its own sub-reasons. Both are
        // attributed at the certifyState level instead.
        .comb, .open_obj => {},
        .choice => note(.choice),
        .seq => note(.seq),
        .object => note(.object),
        .repeat => note(.repeat),
        .int_num, .not_int_num => note(.int_num_sat),
        .num_const => note(.num_const_sat),
        .num_range => note(.num_range_sat),
        .num_mult, .num_excl, .str_pat, .str_excl => note(.num_und),
        .literal, .lit_trie, .str, .int_v, .num_v => {},
    };
    return c;
}

fn certifyFrameInner(g: *const grammar.Grammar, f: *const parser.Frame, side: *parser.Side) Cert {
    return switch (f.*) {
        // Canonical value machines: a live frame always has a completion
        // (literal/trie frames only exist on a matching spelling; the str
        // machine refuses to start a character that would cross max_len, so
        // mid-character states keep count < max_len; int/num are free).
        .literal, .lit_trie, .str, .int_v, .num_v => .alive,
        .int_num => |nf| certIntNum(&nf, true),
        .not_int_num => |nf| certIntNum(&nf, false),
        .num_const => |nf| certNumConst(g, &nf),
        .num_excl => |nf| certNumExcl(g, &nf),
        .num_range => |nf| certNumRange(g, &nf),
        .num_mult => |nf| certNumMult(g, &nf),
        .str_pat => |sf| certStrPat(g, &sf),
        .str_excl => |sf| certStrExcl(g, &sf),
        .open_obj => |of| certOpenObj(g, side, &of),
        // Structural frames: the residual obligation is a sequence of
        // child values (children consume consecutive byte ranges), so the
        // per-node non-emptiness bitset settles it exactly. Comb frames are
        // the exception: their verdict couples parallel threads, so they
        // are certified as a group in certifyState (combGroupAlive).
        .comb => .undecided,
        .seq => certSeq(g, f),
        .object => certObject(g, f),
        .repeat => certRepeat(g, f),
        // Lazy alternation: the deferred (non-nullable) alternatives are
        // this frame's only futures; nullable ones were forked at push
        // time. Alive when some deferred alternative is known non-empty,
        // dead when every one is known empty.
        .choice => |cf| blk: {
            if (g.nonempty.len == 0) break :blk .undecided;
            const alts = g.node(cf.node).choice;
            var saw_unknown = false;
            for (alts) |alt| {
                if (parser.choiceAltMaybeEmpty(g, alt, parser.ALT_ANALYSIS_DEPTH)) continue;
                switch (g.nonempty[alt]) {
                    grammar.NE_YES => break :blk .alive,
                    grammar.NE_NO => {},
                    else => saw_unknown = true,
                }
            }
            break :blk if (saw_unknown) .undecided else .dead;
        },
    };
}

/// seq frame: children[0..idx] are complete, children[idx] is in progress
/// in the frames above (pushNode keeps it so at every quiescent state), and
/// children[idx+1..] are fully owed. Dead when any owed-or-in-progress
/// child's language is empty (an empty language has no completion from any
/// prefix); alive when every fully-owed child is known non-empty - the
/// in-progress child's residual is certified by its own frames above.
fn certSeq(g: *const grammar.Grammar, f: *const parser.Frame) Cert {
    if (g.nonempty.len == 0) return .undecided;
    const children = g.node(f.seq.node).seq;
    if (f.seq.idx >= children.len) return .alive; // unreachable at rest
    if (g.nonempty[children[f.seq.idx]] == grammar.NE_NO) return .dead;
    var saw_unknown = false;
    var i: usize = f.seq.idx + 1;
    while (i < children.len) : (i += 1) {
        switch (g.nonempty[children[i]]) {
            grammar.NE_YES => {},
            grammar.NE_NO => return .dead,
            else => saw_unknown = true,
        }
    }
    return if (saw_unknown) .undecided else .alive;
}

/// Closed-object frame. The frame's own obligation is the remaining
/// REQUIRED properties (optional ones can always be skipped; keys are
/// literals and always emittable, so only the value languages matter).
/// Phase layout (stepThread .object): key/key_after_comma/sep carry the
/// next candidate index in idx; key_lit/value owe the current prop cur
/// (its value is not pushed yet in key_lit and certifies itself in the
/// frames above in value) and continue from cur+1 (idx is clobbered with
/// the key-literal offset in key_lit and restored to cur+1 on entering
/// sep). key_after_comma additionally owes one more key: some candidate
/// (props up to and including the first required one) must be emittable.
fn certObject(g: *const grammar.Grammar, f: *const parser.Frame) Cert {
    if (g.nonempty.len == 0) return .undecided;
    const props = g.node(f.object.node).object.props;
    var start: usize = f.object.idx;
    var saw_unknown = false;
    switch (f.object.phase) {
        .open, .key, .key_after_comma, .sep => {},
        .key_lit => {
            switch (g.nonempty[props[f.object.cur].value]) {
                grammar.NE_YES => {},
                grammar.NE_NO => return .dead,
                else => saw_unknown = true,
            }
            start = f.object.cur + 1;
        },
        .value => start = f.object.cur + 1,
    }
    var i: usize = start;
    while (i < props.len) : (i += 1) {
        if (!props[i].required) continue;
        switch (g.nonempty[props[i].value]) {
            grammar.NE_YES => {},
            grammar.NE_NO => return .dead,
            else => saw_unknown = true,
        }
    }
    if (f.object.phase == .key_after_comma) {
        // A key is mandatory (a trailing comma is never allowed): the
        // emitted prop is one of the candidates, i.e. props[start..] up to
        // and including the first required one.
        var emit: u8 = grammar.NE_NO;
        var k: usize = start;
        while (k < props.len) : (k += 1) {
            const ne = g.nonempty[props[k].value];
            if (ne == grammar.NE_YES) {
                emit = grammar.NE_YES;
                break;
            }
            if (ne != grammar.NE_NO) emit = grammar.NE_UNKNOWN;
            if (props[k].required) break;
        }
        if (emit == grammar.NE_NO) return .dead;
        if (emit == grammar.NE_UNKNOWN) saw_unknown = true;
    }
    return if (saw_unknown) .undecided else .alive;
}

/// Contains-deficit doom from the in-flight element: a repeat frame with an
/// unmet minContains whose in-flight element is rooted in a machine whose
/// language is disjoint from a numeric-only contains schema
/// (int_v/num_v/int_num/num_const/num_excl/num_range/num_mult accept only
/// JSON numbers; strings, arrays, objects and true/false/null literals
/// never match). The remaining slots after the in-flight element must then
/// cover the deficit on their own; when they cannot, no completion of this
/// thread exists (the parser taps the contains reparse at element close and
/// rejects the final ']').
fn repeatContainsDoom(g: *const grammar.Grammar, t: *const parser.Thread) bool {
    for (t.frames[0..t.len], 0..) |*f, d| {
        if (f.* != .repeat) continue;
        const rep = &g.node(f.repeat.node).repeat;
        const cn = rep.contains orelse continue;
        if (f.repeat.matched >= rep.min_contains) continue;
        if (rep.max == grammar.UNBOUNDED) continue;
        if (d + 1 >= t.len) continue; // quiescent: certRepeat's deficit rule
        if (!numericOnlyNode(g, cn)) continue;
        if (!rootDisjointFromNumbers(g, &t.frames[d + 1])) continue;
        const left = rep.max -| (f.repeat.count + 1);
        if (f.repeat.matched + left < rep.min_contains) return true;
    }
    return false;
}

/// Node accepts only JSON numbers.
fn numericOnlyNode(g: *const grammar.Grammar, n: grammar.NodeId) bool {
    return switch (g.node(n).*) {
        .int_v, .num_v, .int_num, .num_const, .num_excl, .num_range, .num_mult => true,
        else => false,
    };
}

/// The value machine rooted at frame `f` never produces a JSON number
/// (strings, arrays, objects and true/false/null/string literals).
fn rootDisjointFromNumbers(g: *const grammar.Grammar, f: *const parser.Frame) bool {
    return switch (f.*) {
        .str, .str_pat, .str_excl, .repeat, .object, .open_obj => true,
        .literal => |lf| switch (g.literalBytes(g.node(lf.node).literal)[0]) {
            '"', 't', 'f', 'n' => true,
            else => false,
        },
        .lit_trie => |tf| blk: {
            for (g.node(tf.gnode).lit_trie.literals) |lit| {
                switch (g.literalBytes(lit)[0]) {
                    '"', 't', 'f', 'n' => {},
                    else => break :blk false,
                }
            }
            break :blk true;
        },
        else => false,
    };
}

/// A schema-kind dep's object constraint: the keyword semantics wrap object
/// constraints in a choice whose non-object alts the host object's bytes
/// can never take, so exactly-one-open_obj resolves the operative schema.
/// Null: another shape (un analyzable here, never a doom proof).
fn depObjectSchema(g: *const grammar.Grammar, node: grammar.NodeId) ?grammar.NodeId {
    switch (g.node(node).*) {
        .open_obj => return node,
        .choice => |alts| {
            var found: ?grammar.NodeId = null;
            for (alts) |alt| {
                switch (g.node(alt).*) {
                    .open_obj => {
                        if (found != null) return null;
                        found = alt;
                    },
                    .repeat, .str, .str_pat, .str_excl, .int_v, .int_num, .num_v, .num_const, .num_excl, .num_range, .num_mult, .not_int_num => {},
                    .literal => |lit| {
                        if (g.literalBytes(lit)[0] == '{') return null;
                    },
                    .lit_trie => |lt| {
                        for (lt.literals) |lit| {
                            if (g.literalBytes(lit)[0] == '{') return null;
                        }
                    },
                    else => return null,
                }
            }
            return found;
        },
        else => return null,
    }
}

/// Dep-value doom: an open_obj mid-value (phase .value, the value machine
/// of the in-flight key above it) whose schema-kind dep in force constrains
/// that key to numbers, while the value's root machine can never produce
/// one. The close-time dep reparse (ADR-0006 D2 capture) then always fails:
/// the thread is dead. Key name comes from the retained key chunk.
fn openObjDepValueDoom(g: *const grammar.Grammar, side: *parser.Side, t: *const parser.Thread) bool {
    for (t.frames[0..t.len], 0..) |*f, d| {
        if (f.* != .open_obj) continue;
        const of = &f.open_obj;
        if (of.phase != .value or d + 1 >= t.len) continue;
        const on = &g.node(of.node).open_obj;
        if (on.deps.len == 0 or of.key == 0) continue;
        const nm = side.chunkAt(of.key).payload.items;
        for (on.deps) |dep| {
            if (dep.kind != .schema) continue;
            if (!seenContainsC(side, of.seen, g.literalBytes(dep.trigger))) continue;
            const oo = depObjectSchema(g, dep.schema) orelse continue;
            const on2 = &g.node(oo).open_obj;
            if (on2.pattern_lit != null or on2.pattern_dfa != null) continue;
            var v: grammar.NodeId = on2.value;
            for (on2.props) |p| {
                const kl = g.literalBytes(p.key);
                if (std.mem.eql(u8, kl[1 .. kl.len - 2], nm)) {
                    v = p.value;
                    break;
                }
            }
            if (g.nonempty.len != 0 and g.nonempty[v] == grammar.NE_NO) return true;
            if (numericOnlyNode(g, v) and rootDisjointFromNumbers(g, &t.frames[d + 1])) return true;
        }
    }
    return false;
}

/// repeat frame. A closable frame (min count met, minContains met)
/// completes with ']' in every quiescent phase except body_after_comma (a
/// consumed comma owes one more element). Otherwise more elements are
/// owed; their slot schemas must be known non-empty. uniqueItems freshness
/// and contains-matching of future elements are content-dependent, so
/// those stay undecided (the parser already kills hopeless deficits at
/// element start); only exact impossibility is certified dead here.
fn certRepeat(g: *const grammar.Grammar, f: *const parser.Frame) Cert {
    const rep = g.node(f.repeat.node).repeat;
    const closable = f.repeat.count >= rep.min and
        (rep.contains == null or f.repeat.matched >= rep.min_contains);
    if (closable and f.repeat.phase != .body_after_comma) return .alive;
    // At least one more element is owed (deficit or post-comma mandate).
    if (f.repeat.count >= rep.max) return .dead; // no room for it
    if (g.nonempty.len == 0) return .undecided;
    if (rep.contains) |cn| {
        if (f.repeat.matched < rep.min_contains) {
            if (rep.max != grammar.UNBOUNDED and
                f.repeat.matched + (rep.max - f.repeat.count) < rep.min_contains) return .dead;
            if (g.nonempty[cn] == grammar.NE_NO) return .dead;
            // An element matching BOTH its slot schema and contains cannot
            // be certified from the bitsets alone.
            return .undecided;
        }
    }
    // uniqueItems: a new element must be fresh; the seen set is
    // content-dependent (exhaustion is enforced at element start).
    if (rep.unique) return .undecided;
    // Slots count..upto-1 must each accept a value. The slot in progress
    // (count, when element frames sit above) is included: an empty-language
    // slot can never complete, and a non-empty one composes with its own
    // frames' certificates.
    const upto: usize = if (f.repeat.phase == .body_after_comma)
        @max(rep.min, f.repeat.count + 1)
    else
        rep.min;
    var saw_unknown = false;
    var i: usize = f.repeat.count;
    while (i < upto) : (i += 1) {
        const slot = if (i < rep.prefix.len) rep.prefix[i] else rep.item;
        switch (g.nonempty[slot]) {
            grammar.NE_YES => {},
            grammar.NE_NO => return .dead,
            else => saw_unknown = true,
        }
    }
    return if (saw_unknown) .undecided else .alive;
}
/// the payload format is [count u32] then entries [len u32][raw key bytes]
/// sorted by (len, bytes).
fn seenContainsC(side: *const parser.Side, h: u32, key: []const u8) bool {
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

/// Decoder of raw key bytes under the canonical escape table (the key
/// machine admits no other spelling, so raw bytes decode 1:1). Escapes
/// yield their single codepoint; other bytes pass through as UTF-8.
const KeyDecoder = struct {
    kb: []const u8,
    i: usize = 0,
    bad: bool = false,

    /// Next decoded codepoint, null at end of input; on malformed bytes
    /// (never produced by the key machine) sets bad and returns null.
    fn next(self: *KeyDecoder) ?u21 {
        if (self.i >= self.kb.len) return null;
        const b = self.kb[self.i];
        if (b == '\\') {
            if (self.i + 1 >= self.kb.len) return self.fail();
            const e = self.kb[self.i + 1];
            switch (e) {
                '"', '\\' => {
                    self.i += 2;
                    return e;
                },
                'b' => {
                    self.i += 2;
                    return 8;
                },
                'f' => {
                    self.i += 2;
                    return 12;
                },
                'n' => {
                    self.i += 2;
                    return 10;
                },
                'r' => {
                    self.i += 2;
                    return 13;
                },
                't' => {
                    self.i += 2;
                    return 9;
                },
                'u' => {
                    if (self.i + 6 > self.kb.len) return self.fail();
                    if (self.kb[self.i + 2] != '0' or self.kb[self.i + 3] != '0') return self.fail();
                    const hi = hexValC(self.kb[self.i + 4]) orelse return self.fail();
                    const lo = hexValC(self.kb[self.i + 5]) orelse return self.fail();
                    self.i += 6;
                    return @as(u21, hi) * 16 + lo;
                },
                else => return self.fail(),
            }
        }
        if (b < 0x80) {
            self.i += 1;
            return b;
        }
        const n = std.unicode.utf8ByteSequenceLength(b) catch return self.fail();
        if (self.i + n > self.kb.len) return self.fail();
        const cp = std.unicode.utf8Decode(self.kb[self.i..][0..n]) catch return self.fail();
        self.i += n;
        return cp;
    }

    fn fail(self: *KeyDecoder) ?u21 {
        self.bad = true;
        return null;
    }
};

fn hexValC(b: u8) ?u8 {
    return switch (b) {
        '0'...'9' => b - '0',
        'a'...'f' => b - 'a' + 10,
        'A'...'F' => b - 'A' + 10,
        else => null,
    };
}

/// DFA state after the decoded codepoints of raw key bytes, mirroring
/// parser.keyMatchesRegex (unanchored search, decoded input). Null when
/// the bytes do not decode (unreachable for engine-validated keys).
fn dfaFeedKeyBytes(pd: *const pattern.Dfa, kb: []const u8) ?pattern.RunState {
    var dec = KeyDecoder{ .kb = kb };
    var s = pd.start();
    while (dec.next()) |cp| s = pd.feed(s, cp);
    if (dec.bad) return null;
    return s;
}

fn dfaKeyMatches(pd: *const pattern.Dfa, kb: []const u8) ?bool {
    const s = dfaFeedKeyBytes(pd, kb) orelse return null;
    return pd.isAccept(s);
}

/// Does the decoded key spelled by raw bytes `kb` contain the literal
/// `needle` (parser.keyMatchesPattern semantics)? Null when kb does not
/// decode. Byte-exact without materializing the decoded form: candidate
/// start positions are the raw offsets outside escape sequences, and the
/// comparison decodes on the fly.
fn rawKeyContains(kb: []const u8, needle: []const u8) ?bool {
    if (needle.len == 0) return true;
    var start: usize = 0;
    while (start < kb.len) {
        var dec = KeyDecoder{ .kb = kb[start..] };
        var j: usize = 0;
        var mism = false;
        var buf: [4]u8 = undefined;
        while (j < needle.len and !mism) {
            const cp = dec.next() orelse break;
            const n = std.unicode.utf8Encode(cp, &buf) catch return null;
            var k: usize = 0;
            while (k < n and j < needle.len) : (k += 1) {
                if (buf[k] != needle[j]) {
                    mism = true;
                    break;
                }
                j += 1;
            }
        }
        if (dec.bad) return null;
        if (!mism and j == needle.len) return true;
        var adv = KeyDecoder{ .kb = kb[start..] };
        _ = adv.next();
        if (adv.bad) return null;
        start += adv.i;
    }
    return false;
}

/// Is `name` (raw bytes) one of the declared property spellings?
fn declaredName(on: *const grammar.OpenObjNode, g: *const grammar.Grammar, name: []const u8) bool {
    for (on.props) |p| {
        const kl = g.literalBytes(p.key);
        if (std.mem.eql(u8, kl[1 .. kl.len - 2], name)) return true;
    }
    return false;
}

/// Dispatch value node of an undeclared key spelled `name` (raw bytes):
/// the pattern value when the pattern (regex DFA or literal substring)
/// matches the decoded key, else the fallback value. Null when the name
/// does not decode (the combination is undecidable here, never guessed).
fn extraKeyValue(g: *const grammar.Grammar, on: *const grammar.OpenObjNode, name: []const u8) ?grammar.NodeId {
    if (on.pattern_dfa) |pd| {
        const m = dfaKeyMatches(pd, name) orelse return null;
        return if (m) on.pattern_value else on.value;
    }
    if (on.pattern_lit) |pl| {
        const m = rawKeyContains(name, g.literalBytes(pl)) orelse return null;
        return if (m) on.pattern_value else on.value;
    }
    return on.value;
}

const KeyPath = enum { yes, no, unknown };

fn mergePath(ne: u8, witness: *bool, witness_unknown: *bool) void {
    switch (ne) {
        grammar.NE_YES => witness.* = true,
        grammar.NE_UNKNOWN => witness_unknown.* = true,
        else => {},
    }
}

/// Can a FRESH undeclared key (no typed prefix) dispatch to a non-empty
/// value schema? A pattern-matched key exists iff the pattern language is
/// non-empty (sticky accept then makes the matching spellings infinite, so
/// one avoids the declared names and the seen set); a non-matching key
/// exists iff the empty key does not match - pathological near-universal
/// patterns whose non-matching remnant is finite and taken are left
/// undecided.
fn freshUndeclaredPath(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode) KeyPath {
    var witness = false;
    var unknown = false;
    if (on.pattern_dfa) |pd| {
        const descr = g.pat_len[of.node] orelse return .unknown;
        if (descr.reachableIn(pd.start(), 0, null)) mergePath(g.nonempty[on.pattern_value], &witness, &unknown);
        if (!pd.isAccept(pd.start()) and !declaredName(on, g, "") and
            !seenContainsC(side, of.seen, ""))
        {
            mergePath(g.nonempty[on.value], &witness, &unknown);
        } else if (!pd.isAccept(pd.start()) and g.nonempty[on.value] == grammar.NE_UNKNOWN) {
            unknown = true;
        }
    } else if (on.pattern_lit) |pl| {
        mergePath(g.nonempty[on.pattern_value], &witness, &unknown);
        if (g.literalBytes(pl).len != 0) {
            if (!declaredName(on, g, "") and !seenContainsC(side, of.seen, "")) {
                mergePath(g.nonempty[on.value], &witness, &unknown);
            } else if (g.nonempty[on.value] == grammar.NE_UNKNOWN) {
                unknown = true;
            }
        }
    } else {
        mergePath(g.nonempty[on.value], &witness, &unknown);
    }
    if (witness) return .yes;
    return if (unknown) .unknown else .no;
}

/// Open-object residual. The frame is certified only for nodes without the
/// features this rule does not analyze (propertyNames, dependencies,
/// key/count bounds, capture); those defer to the search. The key
/// constraint of a patternProperties entry (pattern_dfa/pattern_lit) lands
/// only at dispatch (parser.openObjDispatch), so the search must never
/// enumerate key spellings: the certifier reasons about the dispatch
/// outcomes directly (certOpenObjKeyStr for a mid-key frame).
///
/// For a frame not inside a key the residual obligation is exact: a live
/// frame never skipped a required declared prop (dispatch kills such
/// threads, parser openObjDispatch), so what remains is props[next_idx..]
/// required values plus the extra_required names not yet in the seen set;
/// a comma makes one more key mandatory even with no other obligation. An
/// outstanding obligation needs a dispatch witness: a remaining declared
/// prop inside the dispatch window (up to and including the first required
/// one), a missing extra_required name, or a fresh undeclared key. An
/// obligation with no witness is dead; each witness needs a non-empty
/// value schema (the certified bitset).
fn certOpenObj(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame) Cert {
    const on = &g.node(of.node).open_obj;
    if (on.names_forbidden or on.track_keys or on.capture or on.deps.len != 0 or
        on.prop_names != null or on.key_min_len != 0 or on.key_max_len != grammar.UNBOUNDED or
        on.min_props != 0 or on.max_props != grammar.UNBOUNDED)
    {
        note(.openobj_gate);
        if (on.names_forbidden or on.track_keys or on.capture) note(.openobj_track);
        if (on.deps.len != 0) note(.openobj_deps);
        if (on.prop_names != null) note(.openobj_prop_names);
        if (on.key_min_len != 0 or on.key_max_len != grammar.UNBOUNDED) note(.openobj_key_bounds);
        if (on.min_props != 0) note(.openobj_min_props);
        if (on.max_props != grammar.UNBOUNDED) note(.openobj_max_props);
        // Pure count-bounded nodes (min/maxProperties only) admit exact
        // dead rules; aliveness is left to the simulation certificate.
        if (!on.names_forbidden and !on.capture and on.deps.len == 0 and
            on.prop_names == null and on.key_min_len == 0 and
            on.key_max_len == grammar.UNBOUNDED and on.track_keys)
        {
            return certOpenObjCounted(g, side, of, on);
        }
        return .undecided;
    }
    const c = certOpenObjInner(g, side, of, on);
    // The colon-pending check and the closing witness scan note themselves
    // inside; here only the mid-key phases need classifying.
    if (c == .undecided and of.phase == .key_str) note(if (of.u.key.st != .normal or of.u.key.rem != 0) UndReason.openobj_keyesc else UndReason.openobj_keystr);
    return c;
}

/// Entry count of a seen-keys chunk (0 when there is no chunk). With
/// node.track_keys the set holds every dispatched key, so this is the
/// object's exact property count (parser.openObjDispatch).
fn seenCountC(side: *const parser.Side, h: u32) u32 {
    if (h == 0) return 0;
    const items = side.chunkAt(h).payload.items;
    if (items.len < 4) return 0;
    return std.mem.readInt(u32, items[0..4], .little);
}

/// Count-bounded open_object residual (min/maxProperties with track_keys,
/// no other gated feature). Only dead verdicts are derived; aliveness goes
/// to the simulation certificate, which already pads min_props and caps
/// max_props (synth.padMinProps). Three exact rules, all mirroring the
/// parser's own count enforcement (dispatch rejects a key when
/// seenCount == max_props; close rejects seenCount < min_props):
///
/// 1. A pending (key_str) or comma-mandatory (key_after_comma) key with
///    seenCount == max_props can never dispatch, and a comma cannot be
///    closed over: dead.
/// 2. Every unsatisfied required obligation (declared required props from
///    next_idx, missing extra_required names, name-duplicates merged) is a
///    distinct further key at close; n + that count > max_props: dead.
/// 3. With no fresh undeclared dispatch (freshUndeclaredPath == .no, never
///    .unknown) the achievable key count is bounded by n + remaining
///    declared props + missing extra_required names + an in-flight key;
///    below min_props: dead.
fn certOpenObjCounted(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode) Cert {
    const n = seenCountC(side, of.seen);
    if ((of.phase == .key_after_comma or of.phase == .key_str) and
        n >= on.max_props) return .dead;
    // Missing extra_required names, deduplicated against each other and
    // against declared props (a shared name is one key, not two).
    var extra_missing: u32 = 0;
    for (on.extra_required, 0..) |lit, j| {
        const name = g.literalBytes(lit);
        if (seenContainsC(side, of.seen, name)) continue;
        var dup = false;
        for (on.extra_required[0..j]) |lit2| {
            if (std.mem.eql(u8, g.literalBytes(lit2), name)) {
                dup = true;
                break;
            }
        }
        if (!dup) extra_missing += 1;
    }
    // Rule 2: a required prop beyond next_idx whose name is not also an
    // already-counted extra_required name is one more mandatory key.
    var req_missing = extra_missing;
    for (on.props[of.next_idx..]) |p| {
        if (!p.required) continue;
        const kl = g.literalBytes(p.key);
        const name = kl[1 .. kl.len - 2];
        var counted = false;
        for (on.extra_required) |lit| {
            if (std.mem.eql(u8, g.literalBytes(lit), name)) {
                counted = true;
                break;
            }
        }
        if (!counted) req_missing += 1;
    }
    if (@as(u64, n) + req_missing > on.max_props) return .dead;
    // Rule 3.
    if (on.min_props != 0 and freshUndeclaredPath(g, side, of, on) == .no) {
        const inflight: u64 = if (of.phase == .key_str) 1 else 0;
        const achievable = @as(u64, n) + (on.props.len - @min(of.next_idx, @as(u32, @intCast(on.props.len)))) + extra_missing + inflight;
        if (achievable < on.min_props) return .dead;
    }
    // The count-free analyses still prove dead exactly (undispatchable
    // keys, empty obligation values); their alive verdicts ignore the
    // count bounds, so they degrade to undecided here.
    const base: Cert = switch (of.phase) {
        .key_str => certOpenObjKeyStr(g, side, of, on),
        else => certOpenObjInner(g, side, of, on),
    };
    return if (base == .dead) .dead else .undecided;
}

fn certOpenObjInner(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode) Cert {
    if (g.nonempty.len == 0) {
        note(.no_nonempty);
        return .undecided;
    }
    switch (of.phase) {
        // Mid-key: dispatch outcomes are analyzed directly.
        .key_str => return certOpenObjKeyStr(g, side, of, on),
        // Key completed: the value node is dispatched already.
        .colon => switch (g.nonempty[of.u.pending]) {
            grammar.NE_YES => {},
            grammar.NE_NO => return .dead,
            else => {
                note(.openobj_colon);
                return .undecided;
            },
        },
        else => {},
    }
    // Doom scan: an outstanding obligation whose value schema is empty can
    // never dispatch, so no completion exists.
    var unknown = false;
    var has_required = false;
    for (on.props[of.next_idx..]) |p| {
        if (!p.required) continue;
        has_required = true;
        switch (g.nonempty[p.value]) {
            grammar.NE_NO => return .dead,
            grammar.NE_UNKNOWN => unknown = true,
            else => {},
        }
    }
    var missing_extra = false;
    var witness = false;
    var witness_unknown = false;
    for (on.extra_required) |lit| {
        const name = g.literalBytes(lit);
        if (seenContainsC(side, of.seen, name)) continue;
        missing_extra = true;
        const v = extraKeyValue(g, on, name) orelse {
            unknown = true;
            continue;
        };
        switch (g.nonempty[v]) {
            grammar.NE_YES => witness = true, // the name itself dispatches
            grammar.NE_NO => return .dead,
            // The name is an obligation whose value may be empty (blocks
            // alive) AND a dispatch candidate (blocks dead).
            else => {
                unknown = true;
                witness_unknown = true;
            },
        }
    }
    const obligated = has_required or missing_extra or of.phase == .key_after_comma;
    if (!obligated) {
        if (unknown) note(.openobj_witness);
        return if (unknown) .undecided else .alive;
    }
    // Declared-prop witnesses, in dispatchable order: props beyond the
    // first remaining required prop are unreachable over the skip.
    var i: usize = of.next_idx;
    while (i < on.props.len) : (i += 1) {
        const p = on.props[i];
        mergePath(g.nonempty[p.value], &witness, &witness_unknown);
        if (p.required) break;
    }
    if (!witness) {
        switch (freshUndeclaredPath(g, side, of, on)) {
            .yes => witness = true,
            .unknown => witness_unknown = true,
            .no => {},
        }
    }
    if (witness and !unknown) return .alive;
    // No dispatchable key with a possibly-non-empty value: every future
    // dispatch dies, so the obligations can never be discharged - dead no
    // matter what the deeper obligation values are.
    if (!witness and !witness_unknown) return .dead;
    note(.openobj_witness);
    return .undecided;
}

/// Mid-key residual (phase key_str). The completed key k extends the typed
/// raw prefix kb and dispatches to: a declared prop (k spells its name, in
/// dispatchable order), the pattern value (k undeclared, pattern matches
/// the decoded key), or the fallback value (k undeclared, no match); a
/// duplicate or an empty value schema dies at dispatch. Every candidate
/// with a non-empty value schema is a completion witness: fresh continuations
/// always exist because the key bounds are gate-free. Escape-pending and
/// mid-character states defer to the search (a bounded suffix).
fn certOpenObjKeyStr(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode) Cert {
    if (of.u.key.st != .normal or of.u.key.rem != 0) return certOpenObjKeyEsc(g, side, of, on);
    const kb = side.chunkAt(of.key).payload.items;
    var unknown = false;
    for (on.props[of.next_idx..]) |p| {
        if (!p.required) continue;
        switch (g.nonempty[p.value]) {
            grammar.NE_NO => return .dead,
            grammar.NE_UNKNOWN => unknown = true,
            else => {},
        }
    }
    for (on.extra_required) |lit| {
        const name = g.literalBytes(lit);
        if (seenContainsC(side, of.seen, name)) continue;
        const v = extraKeyValue(g, on, name) orelse {
            unknown = true;
            continue;
        };
        switch (g.nonempty[v]) {
            grammar.NE_NO => return .dead,
            grammar.NE_UNKNOWN => unknown = true,
            else => {},
        }
    }
    var witness = false;
    var witness_unknown = false;
    // Declared names extending the typed prefix, in dispatchable order.
    var i: usize = of.next_idx;
    while (i < on.props.len) : (i += 1) {
        const p = on.props[i];
        const kl = g.literalBytes(p.key);
        if (std.mem.startsWith(u8, kl[1 .. kl.len - 2], kb)) {
            mergePath(g.nonempty[p.value], &witness, &witness_unknown);
        }
        if (p.required) break;
    }
    // Missing extra_required names extending the typed prefix.
    for (on.extra_required) |lit| {
        const name = g.literalBytes(lit);
        if (seenContainsC(side, of.seen, name)) continue;
        if (!std.mem.startsWith(u8, name, kb)) continue;
        const v = extraKeyValue(g, on, name) orelse {
            witness_unknown = true;
            continue;
        };
        mergePath(g.nonempty[v], &witness, &witness_unknown);
    }
    // Undeclared completions.
    if (on.pattern_dfa) |pd| {
        const q = dfaFeedKeyBytes(pd, kb) orelse return .undecided;
        if (g.pat_len[of.node]) |descr| {
            const can_match = descr.reachableIn(q, 0, null);
            if (can_match) {
                // Sticky accept: the matching continuations are infinite,
                // so a fresh undeclared spelling always exists.
                mergePath(g.nonempty[on.pattern_value], &witness, &witness_unknown);
            }
            if (g.nonempty[on.value] != grammar.NE_NO) {
                if (!can_match) {
                    // No continuation ever matches: every extension takes
                    // the fallback (an infinite, dodgeable set).
                    mergePath(g.nonempty[on.value], &witness, &witness_unknown);
                } else if (!pd.isAccept(q) and !seenContainsC(side, of.seen, kb)) {
                    // kb as completed so far does not match and is fresh.
                    mergePath(g.nonempty[on.value], &witness, &witness_unknown);
                } else if (!pd.isAccept(q)) {
                    // A non-matching extension may exist (kb is taken); a
                    // non-accept-cycle analysis is out of scope here.
                    witness_unknown = true;
                }
            }
        } else {
            witness_unknown = true;
        }
    } else if (on.pattern_lit) |pl| {
        const needle = g.literalBytes(pl);
        const contains = rawKeyContains(kb, needle) orelse return .undecided;
        // A matching completion always exists unless the key already
        // contains the needle (then every completion matches); appending
        // the needle always works. A non-matching completion exists iff
        // the key does not contain the needle yet (a fixed needle is
        // dodgeable forever over the codepoint alphabet).
        mergePath(g.nonempty[on.pattern_value], &witness, &witness_unknown);
        if (!contains) mergePath(g.nonempty[on.value], &witness, &witness_unknown);
    } else {
        // No pattern: every undeclared key takes the fallback; fresh
        // undeclared spellings always exist (declared names and the seen
        // set are finite).
        mergePath(g.nonempty[on.value], &witness, &witness_unknown);
    }
    if (witness and !unknown) return .alive;
    // The current key must be closed and dispatched to complete the frame;
    // with no dispatchable completion at all the state is dead even when
    // the remaining obligations are only partially analyzed (they can
    // never be reached).
    if (!witness and !witness_unknown) return .dead;
    return .undecided;
}

/// Escape-pending or mid-codepoint mid-key residual (phase key_str, key
/// machine not in the clean content state). The pending bytes only extend
/// the raw key prefix kb, and dispatch compares raw spellings
/// (parser.openObjDispatch), so the completed key can dispatch only where a
/// clean key with prefix kb already could: a declared prop or extra_required
/// name whose raw spelling starts with kb, a key pattern (whose match the
/// pending bytes can still change), or the undeclared fallback. Only a dead
/// verdict is derived: with no prefix-matching name, no pattern, and an
/// empty fallback value schema no completion of the pending key can
/// dispatch, and the frame has no other exit. Anything else defers to the
/// search.
fn certOpenObjKeyEsc(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode) Cert {
    const kb = side.chunkAt(of.key).payload.items;
    if (on.pattern_lit != null or on.pattern_dfa != null) return .undecided;
    var i: usize = of.next_idx;
    while (i < on.props.len) : (i += 1) {
        const p = on.props[i];
        const kl = g.literalBytes(p.key);
        if (std.mem.startsWith(u8, kl[1 .. kl.len - 2], kb)) return .undecided;
        if (p.required) break;
    }
    for (on.extra_required) |lit| {
        const name = g.literalBytes(lit);
        if (seenContainsC(side, of.seen, name)) continue;
        if (std.mem.startsWith(u8, name, kb)) return .undecided;
    }
    if (g.nonempty[on.value] != grammar.NE_NO) return .undecided;
    return .dead;
}
/// x == p, or the decimal digits of p are a prefix of those of x. (A typed
/// prefix of value 0 - all zeros - extends to every exponent and is handled
/// by the callers.)
fn decExtHas(p: u32, x: i64) bool {
    if (x < 0) return false;
    var v: u64 = @intCast(x);
    const pv: u64 = p;
    while (v > pv) v /= 10;
    return v == pv;
}

/// Smallest element of decExt(p) strictly greater than a (p > 0).
fn smallestDecExtAbove(p: u32, a: i128) i128 {
    if (@as(i128, p) > a) return p;
    var px: i128 = p;
    while (px <= a) px *= 10;
    return px;
}

/// Exists x in decExt(p) intersect [lo, hi] (x >= 0; p == 0 = all of N).
fn decExtHasIn(p: u32, lo: i128, hi: i128) bool {
    if (hi < 0 or hi < lo) return false;
    if (p == 0) return true;
    const x = smallestDecExtAbove(p, lo - 1);
    return x <= hi;
}

/// Achievable order-of-magnitude set of a frozen-mantissa number frame:
/// value = D x 10^(om - d + 1), om = om_m + e with e constrained by the
/// exponent machine state.
const OmSet = union(enum) {
    any, // exponent not started or free sign: every integer
    ray: struct { pivot: i64, neg: bool }, // [pivot, +inf) or (-inf, pivot]
    dec: struct { pivot: i64, p: u32, neg: bool }, // pivot +- decExt(p)
};

fn omHas(s: OmSet, t: i64) bool {
    switch (s) {
        .any => return true,
        .ray => |r| return if (r.neg) t <= r.pivot else t >= r.pivot,
        .dec => |d| {
            const x: i64 = if (d.neg) d.pivot - t else t - d.pivot;
            if (x < 0) return false;
            return d.p == 0 or decExtHas(d.p, x);
        },
    }
}

/// Exists an achievable om strictly between lo_om and hi_om (open).
fn omInterior(s: OmSet, lo_om: i64, hi_om: i64) bool {
    const a: i128 = @as(i128, lo_om) + 1;
    const b: i128 = @as(i128, hi_om) - 1;
    if (a > b) return false;
    switch (s) {
        .any => return true,
        .ray => |r| return if (r.neg) r.pivot >= a else r.pivot <= b,
        .dec => |d| {
            if (d.neg) return decExtHasIn(d.p, @as(i128, d.pivot) - b, @as(i128, d.pivot) - a);
            return decExtHasIn(d.p, a - d.pivot, b - d.pivot);
        },
    }
}

/// A one-sided magnitude bound of a num_range residual, in the frame's
/// comparison slot coordinates.
const MagBound = struct { slot: usize, om: i64, excl: bool, dlen: u32 };

fn magCmp(nf: *const parser.NumRangeFrame, b: MagBound) u8 {
    return (nf.cmp >> @intCast(2 * b.slot)) & 3;
}

/// Interval test (mantissa still extensible): the achievable magnitudes at
/// om t form an interval dense in [D x 10^m, (D+1) x 10^m); is it not
/// entirely below the lower bound.
fn rangeLoOkI(nf: *const parser.NumRangeFrame, b: MagBound, t: i64) bool {
    if (t > b.om) return true;
    if (t < b.om) return false;
    // cmp 1 (instance digits diverged below the bound's) places the whole
    // interval below the bound; 0/2 always leave values >= the bound
    // (cmp 0: the bound itself lies in the interval; exclusivity still
    // leaves the values above it).
    return magCmp(nf, b) != 1;
}

/// Interval test: is the achievable interval at om t not entirely above
/// the upper bound.
fn rangeHiOkI(nf: *const parser.NumRangeFrame, b: MagBound, t: i64) bool {
    if (t < b.om) return true;
    if (t > b.om) return false;
    const c = magCmp(nf, b);
    if (c == 2) return false;
    // cmp 0 with the bound's digits exhausted: the bound equals the
    // interval's lower endpoint, so an exclusive bound leaves nothing.
    if (c == 0 and b.excl and nf.j[b.slot] >= b.dlen) return false;
    return true;
}

/// Point test (mantissa frozen): the achievable magnitude at om t is the
/// single value D x 10^(t - d + 1); the digit comparison against the bound
/// is exact (padded streams), so cmp 0 means equal, 1 below, 2 above.
fn rangePtLo(nf: *const parser.NumRangeFrame, b: MagBound, t: i64) bool {
    if (t > b.om) return true;
    if (t < b.om) return false;
    const c = magCmp(nf, b);
    return c == 2 or (c == 0 and !b.excl);
}

fn rangePtHi(nf: *const parser.NumRangeFrame, b: MagBound, t: i64) bool {
    if (t < b.om) return true;
    if (t > b.om) return false;
    const c = magCmp(nf, b);
    return c == 1 or (c == 0 and !b.excl);
}

fn rangePointOk(nf: *const parser.NumRangeFrame, lo: ?MagBound, hi: ?MagBound, t: i64) bool {
    if (lo) |b| {
        if (!rangePtLo(nf, b, t)) return false;
    }
    if (hi) |b| {
        if (!rangePtHi(nf, b, t)) return false;
    }
    return true;
}

/// num_range residual: is a value within the node's bounds still reachable.
/// The sign and (once a significant digit was typed) the digit-comparison
/// state against each bound are fixed; the order of magnitude stays free
/// until the exponent is typed.
fn certNumRange(g: *const grammar.Grammar, nf: *const parser.NumRangeFrame) Cert {
    const nr = &g.node(nf.node).num_range;
    const zero: grammar.NumConst = .{ .neg = false, .digits = "", .exp10 = 0 };
    if (nf.flags & parser.RANGE_LEAD == 0) {
        // No significant digit yet: the value is still zero, and zero
        // always remains reachable (more zeros). A nonzero value needs the
        // fixed sign to be compatible with the bounds; the magnitude is
        // still fully free (fraction digits plus exponent), except in the
        // exponent states where the all-zero mantissa is frozen.
        switch (nf.st) {
            .exp, .exp_sign, .exp_digits => {
                return if (grammar.numConstInRange(zero, nr.*)) .alive else .dead;
            },
            .start => return .alive, // compile guarantees a non-empty range
            .minus => {
                const neg_ok = nr.min == null or grammar.cmpNumConst(nr.min.?, zero) == .lt;
                return if (grammar.numConstInRange(zero, nr.*) or neg_ok) .alive else .dead;
            },
            else => {
                // zero_complete / dot / frac_digits with an all-zero mantissa.
                const side_ok = if (nf.flags & parser.RANGE_NEG != 0)
                    (nr.min == null or grammar.cmpNumConst(nr.min.?, zero) == .lt)
                else
                    (nr.max == null or grammar.cmpNumConst(nr.max.?, zero) == .gt);
                return if (grammar.numConstInRange(zero, nr.*) or side_ok) .alive else .dead;
            },
        }
    }
    // Sign fixed, a significant digit typed. Work with magnitude bounds:
    // value = s x y, y > 0 must lie within (lo_mag, hi_mag).
    var lo: ?MagBound = null;
    var hi: ?MagBound = null;
    if (nf.flags & parser.RANGE_NEG != 0) {
        // value = -y: min <= -y <= max. A non-negative min forbids y > 0.
        if (nr.min) |mn| {
            if (!mn.neg) return .dead; // min >= 0 (canonical zero has no sign)
            hi = .{ .slot = 0, .om = boundOm(mn), .excl = nr.min_excl, .dlen = @intCast(mn.digits.len) };
        }
        if (nr.max) |mx| {
            if (mx.neg) lo = .{ .slot = 1, .om = boundOm(mx), .excl = nr.max_excl, .dlen = @intCast(mx.digits.len) };
            // max >= 0: no lower magnitude bound (value < 0 <= max).
        }
    } else {
        // value = y: a non-positive max forbids y > 0.
        if (nr.max) |mx| {
            if (mx.neg or mx.digits.len == 0) return .dead;
            hi = .{ .slot = 1, .om = boundOm(mx), .excl = nr.max_excl, .dlen = @intCast(mx.digits.len) };
        }
        if (nr.min) |mn| {
            if (!mn.neg and mn.digits.len != 0)
                lo = .{ .slot = 0, .om = boundOm(mn), .excl = nr.min_excl, .dlen = @intCast(mn.digits.len) };
            // min <= 0: no lower magnitude bound.
        }
    }
    switch (nf.st) {
        .int_digits, .dot, .frac_digits => {
            // Mantissa extensible, exponent still free: every om is
            // achievable with a dense interval of magnitudes.
            if (lo == null or hi == null) return .alive; // one side free: far om works
            if (hi.?.om - lo.?.om >= 2) return .alive;
            if (lo.?.om == hi.?.om)
                return if (rangeLoOkI(nf, lo.?, lo.?.om) and rangeHiOkI(nf, hi.?, hi.?.om)) .alive else .dead;
            if (rangeLoOkI(nf, lo.?, lo.?.om)) return .alive; // t < hi.om passes hi
            return if (rangeHiOkI(nf, hi.?, hi.?.om)) .alive else .dead;
        },
        .exp => return certRangeFrozen(nf, lo, hi, .any),
        .exp_sign => return certRangeFrozen(nf, lo, hi, .{ .ray = .{
            .pivot = numRangeOmM(nf),
            .neg = nf.flags & parser.RANGE_EXP_NEG != 0,
        } }),
        .exp_digits => {
            if (nf.exp == std.math.maxInt(u32)) return .undecided; // saturated
            return certRangeFrozen(nf, lo, hi, .{ .dec = .{
                .pivot = numRangeOmM(nf),
                .p = nf.exp,
                .neg = nf.flags & parser.RANGE_EXP_NEG != 0,
            } });
        },
        else => return .undecided, // unreachable with LEAD set
    }
}

fn boundOm(nc: grammar.NumConst) i64 {
    return nc.exp10 + @as(i64, @intCast(nc.digits.len)) - 1;
}

fn numRangeOmM(nf: *const parser.NumRangeFrame) i64 {
    return if (nf.flags & parser.RANGE_OM_INT != 0)
        @as(i64, nf.int_len) - 1
    else
        -(@as(i64, nf.fz) + 1);
}

/// Frozen-mantissa num_range: a single magnitude per om; interior om values
/// always pass both bounds, boundary om values need the exact point tests.
fn certRangeFrozen(nf: *const parser.NumRangeFrame, lo: ?MagBound, hi: ?MagBound, s: OmSet) Cert {
    const lo_om: i64 = if (lo) |b| b.om else std.math.minInt(i64) + 1;
    const hi_om: i64 = if (hi) |b| b.om else std.math.maxInt(i64) - 1;
    if (omInterior(s, lo_om, hi_om)) return .alive;
    if (lo != null and omHas(s, lo_om) and rangePointOk(nf, lo, hi, lo_om)) return .alive;
    if (hi != null and hi_om != lo_om and omHas(s, hi_om) and rangePointOk(nf, lo, hi, hi_om)) return .alive;
    return .dead;
}

/// num_mult residual (grammar.NumMult semantics: V is a multiple iff V == 0
/// or, with V = D x 10^p canonical and s = p - div_exp10 >= 0,
/// D x 10^s == 0 mod div).
fn certNumMult(g: *const grammar.Grammar, nf: *const parser.NumMultFrame) Cert {
    const nm = &g.node(nf.node).num_mult;
    if (nf.flags & parser.MULT_NZ == 0) return .alive; // zero is a multiple
    switch (nf.st) {
        // Mantissa extensible: appending <= 12 digits forces the canonical
        // residue rem_d == 0 mod co (10 is invertible mod co), then a large
        // positive exponent covers the divisor's 2/5 valuations.
        .int_digits, .dot, .frac_digits => return .alive,
        .start, .minus, .zero_complete => return .alive, // unreachable with NZ
        .exp => return if (nf.rem_d % nm.co == 0) .alive else .dead,
        .exp_sign, .exp_digits => {
            if (nf.flags & parser.MULT_EXP_SAT != 0) {
                // Saturated exponent: the boundary verdict is fixed.
                if (nf.flags & parser.MULT_EXP_NEG != 0) return .dead;
                return if (nf.rem % nm.co == 0) .alive else .dead;
            }
            if (nf.flags & parser.MULT_EXP_NEG == 0) {
                // Positive exponent: s is unbounded, divisibility reduces to
                // the 2/5-stripped residue class.
                return if (nf.rem_d % nm.co == 0) .alive else .dead;
            }
            // Negative exponent: s = -E - frac_len + tz - div_exp10 decreases
            // with E, and a smaller s cannot help (fewer 2/5 factors, same
            // residue class), so the smallest achievable E decides exactly.
            const min_e: i64 = if (nf.st == .exp_sign or nf.exp == 0) 0 else nf.exp;
            const s: i64 = -min_e - @as(i64, nf.frac_len) + @as(i64, nf.tz) - nm.div_exp10;
            if (s < 0) return .dead;
            const p = grammar.powmodU32(10 % nm.div, @intCast(s), nm.div);
            return if ((@as(u64, nf.rem_d) * p) % nm.div == 0) .alive else .dead;
        },
    }
}

/// int_num / not_int_num residual: the value is an integer iff the mantissa
/// is zero or e >= frac_len - tz; the complement otherwise.
fn certIntNum(nf: *const parser.IntNumFrame, comptime want_int: bool) Cert {
    const sat = std.math.maxInt(u32);
    const gap: i64 = @as(i64, nf.frac_len) - @as(i64, nf.tz);
    if (!nf.nz) {
        // Mantissa still zero (an integer). In the exponent states it is
        // frozen at zero; elsewhere a significant digit can still be typed.
        if (want_int) return .alive;
        return switch (nf.st) {
            .exp, .exp_sign, .exp_digits => .dead,
            else => .alive,
        };
    }
    if (want_int) {
        return switch (nf.st) {
            // e <= 0: integer iff e = 0 suffices, i.e. tz >= frac_len.
            .exp_sign => if (!nf.exp_neg or gap <= 0) .alive else .dead,
            .exp_digits => blk: {
                if (nf.exp == sat) break :blk .undecided;
                if (!nf.exp_neg) break :blk .alive; // e unbounded above
                // Need E <= tz - frac_len; the smallest achievable E decides.
                const min_e: i64 = if (nf.exp == 0) 0 else nf.exp;
                break :blk if (min_e <= -gap) .alive else .dead;
            },
            else => .alive, // a large positive exponent is still available
        };
    }
    return switch (nf.st) {
        // e >= 0: non-integer iff e = 0 works, i.e. frac_len > tz.
        .exp_sign => if (nf.exp_neg or gap > 0) .alive else .dead,
        .exp_digits => blk: {
            if (nf.exp == sat) break :blk .undecided;
            if (nf.exp_neg) break :blk .alive; // e unbounded below
            // Need E < frac_len - tz; the smallest achievable E decides.
            const min_e: i64 = if (nf.exp == 0) 0 else nf.exp;
            break :blk if (min_e < gap) .alive else .dead;
        },
        else => .alive, // a large negative exponent is still available
    };
}

/// num_const residual: the frozen parts of the spelling must still admit
/// the target value (grammar.NumConst equality: zero iff target zero, else
/// sign equal and e - frac_len + tail_z == exp10).
fn certNumConst(g: *const grammar.Grammar, nf: *const parser.NumConstFrame) Cert {
    const nc = &g.node(nf.node).num_const;
    switch (nf.st) {
        .start => return .alive,
        .minus => return if (nc.neg or nc.digits.len == 0) .alive else .dead,
        .zero_complete, .int_digits, .dot, .frac_digits => {
            // The exponent is still free, so any exponent target is
            // reachable as long as the digits/sign stay compatible.
            return switch (nf.m) {
                .leading => if (nc.digits.len == 0 or nc.neg == nf.neg) .alive else .dead,
                .matching, .tail => if (nc.neg == nf.neg) .alive else .dead,
            };
        },
        .exp, .exp_sign, .exp_digits => {
            switch (nf.m) {
                .matching => return .dead, // significant digits frozen incomplete
                .leading => return if (nc.digits.len == 0) .alive else .dead,
                .tail => {
                    if (nc.neg != nf.neg) return .dead;
                    const e_need: i64 = nc.exp10 + @as(i64, nf.frac_len) - @as(i64, nf.tail_z);
                    switch (nf.st) {
                        .exp => return .alive, // sign and digits still free
                        .exp_sign => return if (e_need == 0 or (e_need > 0) != nf.exp_neg) .alive else .dead,
                        else => {
                            if (nf.exp == std.math.maxInt(u32)) return .undecided;
                            if (e_need == 0) return if (nf.exp == 0) .alive else .dead;
                            if ((e_need > 0) == nf.exp_neg) return .dead;
                            const x: i64 = if (e_need > 0) e_need else -e_need;
                            return if (nf.exp == 0 or decExtHas(nf.exp, x)) .alive else .dead;
                        },
                    }
                },
            }
        },
    }
}

/// num_excl residual: the number language minus <= 2 constants. In the
/// exponent states the mantissa is frozen: a frozen zero mantissa spells a
/// forbidden zero constant under every exponent (dead); anything else has
/// infinitely many exponent choices against finitely many forbidden values.
/// Outside the exponent states the achievable set is infinite.
fn certNumExcl(g: *const grammar.Grammar, nf: *const parser.NumExclFrame) Cert {
    switch (nf.st) {
        .exp, .exp_sign, .exp_digits => {
            const ne = &g.node(nf.node).num_excl;
            for (ne.consts, 0..) |*nc, i| {
                const shift: u3 = @intCast(2 * i);
                if (nc.digits.len == 0 and ((nf.m >> shift) & 3) == 0) return .dead;
            }
            return .alive;
        },
        else => return .alive,
    }
}

/// str_pat residual via the exact length-acceptance descriptor of the
/// pattern DFA (grammar.pat_len): the string is completable iff an accept
/// state is reachable from the current DFA state in exactly L more
/// codepoints for some L within the remaining length bounds. A missing
/// descriptor (analysis budget exceeded) is undecided, never a guess.
fn certStrPat(g: *const grammar.Grammar, sf: *const parser.StrPatFrame) Cert {
    const sp = &g.node(sf.node).str_pat;
    switch (sf.state) {
        .open, .normal => {
            const descr = g.pat_len[sf.node] orelse return .undecided;
            if (sf.state == .open or sf.rem == 0) {
                const lo = sp.min_len -| sf.count;
                const hi: ?u32 = if (sp.max_len == grammar.UNBOUNDED) null else sp.max_len - sf.count;
                return if (descr.reachableIn(sf.dfa, lo, hi)) .alive else .dead;
            }
            // Mid UTF-8 sequence: the pending continuation bytes complete to
            // exactly the contiguous codepoint range [cp_lo, cp_hi] (the
            // first pending byte is bounded by [lo, hi], the rest span
            // [0x80, 0xBF]). The DFA only sees the completed codepoint, so
            // answering per transition range - not per codepoint - keeps
            // this exact and small. If no live transition covers the range
            // the character can never complete acceptably: dead.
            const shift: u5 = @intCast(6 * @as(u32, sf.rem - 1));
            const base: u32 = sf.cp << @intCast(6 * @as(u32, sf.rem));
            const cp_lo = base | (@as(u32, sf.lo & 0x3F) << shift);
            const cp_hi = base | (@as(u32, sf.hi & 0x3F) << shift) | ((@as(u32, 1) << shift) - 1);
            const count1 = sf.count + 1; // the lead byte was admitted, so count < max_len
            const lo = sp.min_len -| count1;
            const hi: ?u32 = if (sp.max_len == grammar.UNBOUNDED) null else sp.max_len - count1;
            for (sp.dfa.states[sf.dfa].trans) |t| {
                if (t.hi < cp_lo or t.lo > cp_hi) continue;
                if (sp.dfa.dead(t.to)) continue;
                if (descr.reachableIn(t.to, lo, hi)) return .alive;
            }
            return .dead;
        },
        else => return .undecided, // mid escape: a bounded suffix, left to the search
    }
}

/// str_excl residual: the forbidden set is a finite trie of raw quoted
/// spellings, and any length within bounds below max_len has a spelling
/// that steps off the trie (an off-edge byte; escape spellings dodge the
/// forbidden values). Only at count == max_len is the closing quote the
/// sole move, refused on a forbidden spelling.
fn certStrExcl(g: *const grammar.Grammar, sf: *const parser.StrExclFrame) Cert {
    const se = &g.node(sf.node).str_excl;
    switch (sf.state) {
        .open => return if (se.max_len == 0) .undecided else .alive,
        .normal => {
            if (sf.rem != 0) return .undecided;
            if (sf.count < se.max_len) return .alive;
            if (sf.count < se.min_len) return .dead; // unreachable (min <= max)
            if (sf.trie_dead) return .alive;
            return if (se.trie.nodes[sf.trie].terminal) .dead else .alive;
        },
        else => return .undecided,
    }
}

const testing = std.testing;

fn litGrammar(a: std.mem.Allocator, s: []const u8) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addLiteralNode(s);
    return b.finish(arena, .literal_set, root, .{});
}

fn objGrammar(a: std.mem.Allocator) !grammar.Grammar {
    var arena = std.heap.ArenaAllocator.init(a);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const int_node = try b.addNode(.{ .int_v = {} });
    const props = try b.copyProps(&[_]grammar.Prop{
        .{ .key = try b.addLiteral("\"a\":"), .value = int_node, .required = true },
    });
    const root = try b.addNode(.{ .object = .{ .props = props } });
    return b.finish(arena, .json_schema, root, .{});
}

fn makeTok(a: std.mem.Allocator, vocab: u32, toks: []const []const u8, eos: []const u32, special: []const u32) !tokenizer.Tokenizer {
    const entries = try a.alloc(tokenizer.Entry, vocab);
    defer a.free(entries);
    for (toks, 0..) |t, i| entries[i] = .{ .id = @intCast(i), .bytes = t };
    return tokenizer.Tokenizer.create(a, vocab, entries, eos, special);
}

test "complete: dead-end state is not alive, [ab, a, eos] vs literal ab" {
    const alloc = testing.allocator;
    var g = try litGrammar(alloc, "ab");
    defer g.deinit();
    try testing.expect(g.finite_literal);
    var tk = try makeTok(alloc, 3, &.{ "ab", "a", "" }, &.{2}, &.{});
    defer tk.deinit();
    try testing.expect(!tk.byte_complete);

    var cache = Cache.init(alloc);
    defer cache.deinit();
    var w = work_mod.Work{};
    var side = parser.Side{ .a = undefined };

    var st = try parser.initState(&g, 4, &side);
    try testing.expect(try stateAlive(&g, &tk, &st, &cache, &w, &side)); // via "ab"

    var st_a: parser.State = undefined;
    var work: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st, "a", &st_a, &work);
    try testing.expect(!try stateAlive(&g, &tk, &st_a, &cache, &w, &side)); // nothing feeds "b"

    var st_ab: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st, "ab", &st_ab, &work);
    try testing.expect(try stateAlive(&g, &tk, &st_ab, &cache, &w, &side)); // interrupted: can_end
}

test "complete: byte-complete vocabulary is detected" {
    const alloc = testing.allocator;
    const entries = try alloc.alloc(tokenizer.Entry, 257);
    defer alloc.free(entries);
    const bufs = try alloc.alloc([1]u8, 256);
    defer alloc.free(bufs);
    for (0..256) |i| {
        bufs[i][0] = @intCast(i);
        entries[i] = .{ .id = @intCast(i), .bytes = &bufs[i] };
    }
    entries[256] = .{ .id = 256, .bytes = "" };
    var tk = try tokenizer.Tokenizer.create(alloc, 257, entries, &.{256}, &.{});
    defer tk.deinit();
    try testing.expect(tk.byte_complete);

    var tk2 = try makeTok(alloc, 3, &.{ "ab", "a", "" }, &.{2}, &.{});
    defer tk2.deinit();
    try testing.expect(!tk2.byte_complete);
}

test "complete: object with an open class is not a finite literal language" {
    const alloc = testing.allocator;
    var g = try objGrammar(alloc);
    defer g.deinit();
    try testing.expect(!g.finite_literal);
}

// ---- spec-v1 residual certification ----

fn byteTok(a: std.mem.Allocator) !tokenizer.Tokenizer {
    const entries = try a.alloc(tokenizer.Entry, 257);
    defer a.free(entries);
    const bufs = try a.alloc([1]u8, 256);
    defer a.free(bufs);
    for (0..256) |i| {
        bufs[i][0] = @intCast(i);
        entries[i] = .{ .id = @intCast(i), .bytes = &bufs[i] };
    }
    entries[256] = .{ .id = 256, .bytes = "" };
    return tokenizer.Tokenizer.create(a, 257, entries, &.{256}, &.{});
}

test "complete r1: allof enum intersection kills dead prefixes" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    var b = grammar.Builder.init(arena.allocator());
    var lits1 = [_]grammar.Literal{ try b.addLiteral("\"ab\""), try b.addLiteral("\"xy\"") };
    const n1 = try b.addNode(.{ .lit_trie = try b.buildTrie(&lits1) });
    var lits2 = [_]grammar.Literal{ try b.addLiteral("\"ac\""), try b.addLiteral("\"xy\"") };
    const n2 = try b.addNode(.{ .lit_trie = try b.buildTrie(&lits2) });
    const root = try b.addNode(.{ .comb = .{ .kind = .allof, .branches = try b.copyNodeIds(&.{ n1, n2 }) } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    try testing.expect(g.needs_reachability);

    var tk = try byteTok(alloc);
    defer tk.deinit();
    var cache = Cache.init(alloc);
    defer cache.deinit();
    var w = work_mod.Work{};
    var side = parser.Side.init(alloc);
    defer side.deinit();

    // The intersection of {"ab","xy"} and {"ac","xy"} is {"xy"}: after `"a`
    // no completion exists, after `"x` the spelling `"xy"` completes.
    var st = try parser.initState(&g, 8, &side);
    try testing.expect(try stateAlive(&g, &tk, &st, &cache, &w, &side));

    var st_a: parser.State = undefined;
    var work: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st, "\"a", &st_a, &work);
    try testing.expect(!try stateAlive(&g, &tk, &st_a, &cache, &w, &side));

    var st_x: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st, "\"x", &st_x, &work);
    try testing.expect(try stateAlive(&g, &tk, &st_x, &cache, &w, &side));
    var st_xy: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st_x, "y\"", &st_xy, &work);
    try testing.expect(parser.canEnd(&g, &st_xy));
}

/// Bounded exhaustive completion check (the brute-force oracle): feeds
/// every alphabet word of length <= budget. `buf` needs budget + 2 states.
fn reachBrute(g: *const grammar.Grammar, side: *parser.Side, st: *const parser.State, alphabet: []const u8, budget: usize, buf: []parser.State) !bool {
    if (parser.canEnd(g, st)) return true;
    if (budget == 0) return false;
    const dst = &buf[0];
    const work = &buf[1];
    for (alphabet) |ch| {
        parser.releaseState(side, dst);
        parser.releaseState(side, work);
        parser.feedBytes(g, side, st, &[1]u8{ch}, dst, work) catch |err| switch (err) {
            error.Parse => continue,
            else => |e| return e,
        };
        if (try reachBrute(g, side, dst, alphabet, budget - 1, buf[2..])) return true;
    }
    return false;
}

/// Walks every live prefix over `alphabet` up to depth_left and asserts the
/// closed-form certification agrees with the bounded exhaustive oracle:
/// alive must come with a completion within 4 bytes, dead with none. (For
/// these value machines a dead verdict is depth-independent, so the bounded
/// check is exact evidence, not a sampling artifact.)
fn certWalk(g: *const grammar.Grammar, side: *parser.Side, st: *const parser.State, alphabet: []const u8, depth_left: usize, wit_buf: []parser.State, buf: []parser.State, pfx: []u8) !void {
    const cert = certifyState(g, st, side);
    const rb = try reachBrute(g, side, st, alphabet, 4, wit_buf);
    if ((cert == .alive) != rb and cert != .undecided) {
        std.debug.print("MISMATCH cert={s} brute={} prefix={s}\n", .{ @tagName(cert), rb, pfx });
    }
    switch (cert) {
        .alive => try testing.expect(rb),
        .dead => try testing.expect(!rb),
        .undecided => {},
    }
    if (depth_left == 0) return;
    const dst = &buf[0];
    const work = &buf[1];
    for (alphabet) |ch| {
        parser.releaseState(side, dst);
        parser.releaseState(side, work);
        parser.feedBytes(g, side, st, &[1]u8{ch}, dst, work) catch |err| switch (err) {
            error.Parse => continue,
            else => |e| return e,
        };
        pfx[pfx.len - depth_left] = ch;
        try certWalk(g, side, dst, alphabet, depth_left - 1, wit_buf, buf[2..], pfx);
    }
}

test "complete r1: certification agrees with exhaustive search (number machines)" {
    const alloc = testing.allocator;
    const alphabet = "012.e-";
    const one: grammar.NumConst = .{ .neg = false, .digits = "1", .exp10 = 0 };
    const hundred: grammar.NumConst = .{ .neg = false, .digits = "1", .exp10 = 2 };

    var gs: [4]grammar.Grammar = undefined;
    var built: usize = 0;
    defer for (0..built) |i| gs[i].deinit();
    {
        var arena = std.heap.ArenaAllocator.init(alloc);
        var b = grammar.Builder.init(arena.allocator());
        const root = try b.addNode(.{ .num_range = .{ .min = one, .max = hundred } });
        gs[0] = try b.finish(arena, .json_schema, root, .{});
        built += 1;
    }
    {
        var arena = std.heap.ArenaAllocator.init(alloc);
        var b = grammar.Builder.init(arena.allocator());
        const root = try b.addNode(.{ .int_num = {} });
        gs[1] = try b.finish(arena, .json_schema, root, .{});
        built += 1;
    }
    {
        var arena = std.heap.ArenaAllocator.init(alloc);
        var b = grammar.Builder.init(arena.allocator());
        const root = try b.addNode(.{ .not_int_num = {} });
        gs[2] = try b.finish(arena, .json_schema, root, .{});
        built += 1;
    }
    {
        var arena = std.heap.ArenaAllocator.init(alloc);
        var b = grammar.Builder.init(arena.allocator());
        const root = try b.addNode(.{ .num_mult = .{ .div = 2, .div_exp10 = 0, .co = 1 } });
        gs[3] = try b.finish(arena, .json_schema, root, .{});
        built += 1;
    }

    for (0..built) |i| {
        const g = &gs[i];
        try testing.expect(g.needs_reachability);
        var side = parser.Side.init(alloc);
        defer side.deinit();
        var wit_buf: [8]parser.State = undefined;
        var buf: [6]parser.State = undefined;
        for (&wit_buf) |*s| s.n = 0;
        for (&buf) |*s| s.n = 0;
        var st = try parser.initState(g, 4, &side);
        var pfx: [3]u8 = undefined;
        try certWalk(g, &side, &st, alphabet, 3, &wit_buf, &buf, &pfx);
    }
}

test "complete r1: open_obj residual certifies remaining required keys" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const int_node = try b.addNode(.{ .int_v = {} });
    // A comb wrapper that certifies non-empty (identical branches): the
    // property values below are allof{int, int}.
    const branches = try b.copyNodeIds(&.{ int_node, int_node });
    const comb_node = try b.addNode(.{ .comb = .{ .kind = .allof, .branches = branches } });
    const props = try b.copyProps(&[_]grammar.Prop{
        .{ .key = try b.addLiteral("\"a\":"), .value = comb_node, .required = true },
        .{ .key = try b.addLiteral("\"b\":"), .value = int_node, .required = true },
    });
    const root = try b.addNode(.{ .open_obj = .{ .props = props, .extra_required = &.{}, .value = int_node } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    try testing.expect(g.needs_reachability);
    try testing.expectEqual(grammar.NE_YES, g.nonempty[root]);

    var tk = try byteTok(alloc);
    defer tk.deinit();
    var cache = Cache.init(alloc);
    defer cache.deinit();
    var w = work_mod.Work{};
    var side = parser.Side.init(alloc);
    defer side.deinit();

    // The whole document completes through every mask step (no search
    // explosion on the open object; every intermediate prefix is certified
    // alive in closed form).
    var st = try parser.initState(&g, 8, &side);
    const doc = "{\"a\":1,\"b\":2}";
    var cur: parser.State = undefined;
    var work: parser.State = undefined;
    var src = &st;
    var dst = &cur;
    for (doc, 0..) |_, i| {
        try testing.expect(try stateAlive(&g, &tk, src, &cache, &w, &side));
        try parser.feedBytes(&g, &side, src, doc[i .. i + 1], dst, &work);
        const t = src;
        src = dst;
        dst = t;
    }
    try testing.expect(parser.canEnd(&g, src));

    // A required key whose value language is empty (oneOf over two
    // identical branches accepts nothing) makes the whole object dead from
    // the start - certified, not searched.
    var arena2 = std.heap.ArenaAllocator.init(alloc);
    const aa2 = arena2.allocator();
    var b2 = grammar.Builder.init(aa2);
    const ii = try b2.addNode(.{ .int_v = {} });
    const oneof_node = try b2.addNode(.{ .comb = .{ .kind = .oneof, .branches = try b2.copyNodeIds(&.{ ii, ii }) } });
    const props2 = try b2.copyProps(&[_]grammar.Prop{
        .{ .key = try b2.addLiteral("\"a\":"), .value = oneof_node, .required = true },
    });
    const root2 = try b2.addNode(.{ .open_obj = .{ .props = props2, .extra_required = &.{}, .value = ii } });
    var g2 = try b2.finish(arena2, .json_schema, root2, .{});
    defer g2.deinit();
    try testing.expectEqual(grammar.NE_NO, g2.nonempty[root2]);
    cache.reset(); // the memo is per-(grammar, tokenizer): drop g's verdicts
    var st2 = try parser.initState(&g2, 8, &side);
    try testing.expectEqual(Cert.dead, certifyState(&g2, &st2, &side));
    try testing.expect(!try stateAlive(&g2, &tk, &st2, &cache, &w, &side));
}

test "complete r1: oneof vote-link residual doom (lit_trie overlap)" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    var b = grammar.Builder.init(arena.allocator());
    // oneOf{"ab","c"} / {"ab","d"}: after `"a` both branches hold the same
    // residual {`b"`}, so exactly-one is unreachable (certified dead by
    // subtrie residual equality, no search); after `"c` branch 2 is dead
    // and branch 1 completes.
    var lits1 = [_]grammar.Literal{ try b.addLiteral("\"ab\""), try b.addLiteral("\"c\"") };
    const n1 = try b.addNode(.{ .lit_trie = try b.buildTrie(&lits1) });
    var lits2 = [_]grammar.Literal{ try b.addLiteral("\"ab\""), try b.addLiteral("\"d\"") };
    const n2 = try b.addNode(.{ .lit_trie = try b.buildTrie(&lits2) });
    const root = try b.addNode(.{ .comb = .{ .kind = .oneof, .branches = try b.copyNodeIds(&.{ n1, n2 }) } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    try testing.expect(g.needs_reachability);

    var tk = try byteTok(alloc);
    defer tk.deinit();
    var cache = Cache.init(alloc);
    defer cache.deinit();
    var w = work_mod.Work{};
    var side = parser.Side.init(alloc);
    defer side.deinit();

    var st = try parser.initState(&g, 8, &side);
    try testing.expectEqual(Cert.undecided, certifyState(&g, &st, &side));

    var work: parser.State = undefined;
    var st_q: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st, "\"", &st_q, &work);
    // Both branches live with different residuals: honestly undecided.
    try testing.expectEqual(Cert.undecided, certifyState(&g, &st_q, &side));

    var st_c: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st_q, "c", &st_c, &work);
    try testing.expectEqual(Cert.alive, certifyState(&g, &st_c, &side));

    var st_a: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st_q, "a", &st_a, &work);
    try testing.expectEqual(Cert.dead, certifyState(&g, &st_a, &side));
    // No search involved: with a single unit of fill budget the certificate
    // still settles the state (the budgeted path would come back capped and
    // fail loudly with ResourceLimit).
    cache.fill_budget = 1;
    try testing.expect(!try stateAlive(&g, &tk, &st_a, &cache, &w, &side));

    var st_ab: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st_a, "b", &st_ab, &work);
    try testing.expectEqual(Cert.dead, certifyState(&g, &st_ab, &side));
}

test "complete r1: oneof vote-link residual doom (pattern overlap)" {
    const alloc = testing.allocator;
    var arena = std.heap.ArenaAllocator.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    // Overlapping oneOf branches violate strict ADR-0005 D3 exclusive
    // completion: "ab" matches BOTH branches, so after `"a` no continuation
    // completes exactly one branch and no oneOf completion exists. The two
    // DFA states then have equal right languages ({`b`} to acceptance), which
    // links the branches: certified dead, no search. Right after `"` the
    // state is still alive (`c"` completes branch 1 alone); the sim-verified
    // certificate proves this exactly.
    const d1 = try aa.create(pattern.Dfa);
    d1.* = try pattern.compileJsonSchema(aa, "^(ab|c)$");
    const d2 = try aa.create(pattern.Dfa);
    d2.* = try pattern.compileJsonSchema(aa, "^(ab|d)$");
    const n1 = try b.addNode(.{ .str_pat = .{ .min_len = 0, .max_len = grammar.UNBOUNDED, .dfa = d1 } });
    const n2 = try b.addNode(.{ .str_pat = .{ .min_len = 0, .max_len = grammar.UNBOUNDED, .dfa = d2 } });
    const root = try b.addNode(.{ .comb = .{ .kind = .oneof, .branches = try b.copyNodeIds(&.{ n1, n2 }) } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    try testing.expect(g.needs_reachability);

    var tk = try byteTok(alloc);
    defer tk.deinit();
    var cache = Cache.init(alloc);
    defer cache.deinit();
    var w = work_mod.Work{};
    var side = parser.Side.init(alloc);
    defer side.deinit();

    var st = try parser.initState(&g, 8, &side);
    var work: parser.State = undefined;
    var st_q: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st, "\"", &st_q, &work);
    try testing.expectEqual(Cert.alive, certifyState(&g, &st_q, &side));

    var st_c: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st_q, "c", &st_c, &work);
    try testing.expectEqual(Cert.alive, certifyState(&g, &st_c, &side));
    var st_d: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st_q, "d", &st_d, &work);
    try testing.expectEqual(Cert.alive, certifyState(&g, &st_d, &side));

    var st_a: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st_q, "a", &st_a, &work);
    try testing.expectEqual(Cert.dead, certifyState(&g, &st_a, &side));
    cache.fill_budget = 1;
    try testing.expect(!try stateAlive(&g, &tk, &st_a, &cache, &w, &side));

    var st_ab: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st_a, "b", &st_ab, &work);
    try testing.expectEqual(Cert.dead, certifyState(&g, &st_ab, &side));

    // Narrowness control: ^(ab|c)$ vs ^(ax|d)$ after `"a` has DIFFERENT
    // residuals ({`b"`} vs {`x"`}): no link. The simulation-verified
    // certificate proves the state alive exactly (`b"` completes branch 1
    // alone); stateAlive agrees.
    var arena2 = std.heap.ArenaAllocator.init(alloc);
    const aa2 = arena2.allocator();
    var b2 = grammar.Builder.init(aa2);
    const d3 = try aa2.create(pattern.Dfa);
    d3.* = try pattern.compileJsonSchema(aa2, "^(ab|c)$");
    const d4 = try aa2.create(pattern.Dfa);
    d4.* = try pattern.compileJsonSchema(aa2, "^(ax|d)$");
    const m1 = try b2.addNode(.{ .str_pat = .{ .min_len = 0, .max_len = grammar.UNBOUNDED, .dfa = d3 } });
    const m2 = try b2.addNode(.{ .str_pat = .{ .min_len = 0, .max_len = grammar.UNBOUNDED, .dfa = d4 } });
    const root2 = try b2.addNode(.{ .comb = .{ .kind = .oneof, .branches = try b2.copyNodeIds(&.{ m1, m2 }) } });
    var g2 = try b2.finish(arena2, .json_schema, root2, .{});
    defer g2.deinit();

    cache.reset(); // the memo is per-(grammar, tokenizer): drop g's verdicts
    cache.fill_budget = FILL_SEARCH_BUDGET;
    var st2 = try parser.initState(&g2, 8, &side);
    var st2_a: parser.State = undefined;
    try parser.feedBytes(&g2, &side, &st2, "\"a", &st2_a, &work);
    try testing.expectEqual(Cert.alive, certifyState(&g2, &st2_a, &side));
    try testing.expect(try stateAlive(&g2, &tk, &st2_a, &cache, &w, &side));
}
