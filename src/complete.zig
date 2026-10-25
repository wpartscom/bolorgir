//! Exact token-level completion reachability for masks.
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
const Arena = @import("alloc.zig").Arena;
const grammar = @import("grammar.zig");
const parser = @import("parser.zig");
const witset = @import("witset.zig");
const synth = @import("synth.zig");
const numcert = @import("numcert.zig");
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

const test_hooks_enabled = @import("build_options").test_hooks;

/// Test builds only (-Dtest_hooks=true, src/test_hooks.zig): the per-fill
/// search budget, the first search tier budget, and a forced UNKNOWN
/// verdict for states with a top frame of the given kind or with the given
/// state key hash (stateKeyHash). Zero / null keeps the normal behavior.
pub const TestOverrides = struct {
    fill_budget: u64 = 0,
    tier_budget: u64 = 0,
    unknown_kind: ?std.meta.Tag(parser.Frame) = null,
    unknown_key: u64 = 0,
    /// Test builds only: bypass the per-fill certificate cache so a
    /// differential can compare masks with the cache on and off.
    cert_cache_off: bool = false,
};
pub var test_overrides: TestOverrides = .{};

pub fn certCacheOff() bool {
    return test_hooks_enabled and test_overrides.cert_cache_off;
}

pub fn fillBudget() u64 {
    if (test_hooks_enabled and test_overrides.fill_budget != 0) return test_overrides.fill_budget;
    return FILL_SEARCH_BUDGET;
}

/// Grammar node of the first thread's top frame of the last state the
/// reachability check left undecided on this thread (probe diagnostics).
pub threadlocal var last_unknown_node: ?grammar.NodeId = null;

fn frameNode(f: *const parser.Frame) ?grammar.NodeId {
    return switch (f.*) {
        .lit_trie => |x| x.gnode,
        inline else => |x| if (@hasField(@TypeOf(x), "node")) x.node else null,
    };
}

fn noteUnknown(st: *const parser.State) error{ResourceLimit} {
    last_unknown_node = null;
    if (st.n > 0 and st.threads[0].len > 0) last_unknown_node = frameNode(&st.threads[0].frames[st.threads[0].len - 1]);
    return error.ResourceLimit;
}

/// Wyhash of the compact state image (parser.writeStateKey).
pub fn stateKeyHash(st: *const parser.State, a: std.mem.Allocator) ?u64 {
    const buf = a.alloc(u8, parser.stateKeyLen(st)) catch return null;
    defer a.free(buf);
    parser.writeStateKey(st, buf);
    return std.hash.Wyhash.hash(0, buf);
}

fn forcedUnknown(st: *const parser.State, a: std.mem.Allocator) bool {
    const o = test_overrides;
    if (o.unknown_kind) |k| {
        for (st.threads[0..st.n]) |*t| {
            if (t.len > 0 and std.meta.activeTag(t.frames[t.len - 1]) == k) return true;
        }
    }
    if (o.unknown_key != 0) {
        if (stateKeyHash(st, a)) |h| return h == o.unknown_key;
    }
    return false;
}

const Status = enum(u8) { in_progress, dead, alive };

/// Memo entry. `alive` and `in_progress` are absolute; `dead` carries the
/// remaining-search-depth budget under which deadness was proven
/// (maxInt(u32) for an absolute proof: certification, or a finite state
/// space fully drained). A dead entry answers only searches whose
/// remaining depth does not exceed `rem`; deeper searches re-prove it.
const Entry = struct { status: Status, rem: u32 };

/// Proven verdict of the top-level certification (`Cert`), stored in the
/// per-fill certificate cache. `undecided` is never stored.
pub const CertVerdict = enum { alive, dead };

/// Session-owned memo: state key -> alive/dead, plus reused scratch states
/// for the depth-first search (one state per depth, reused across
/// sibling branches).
pub const Cache = struct {
    a: std.mem.Allocator,
    map: std.StringHashMapUnmanaged(Entry) = .{},
    states: std.ArrayListUnmanaged(*parser.State) = .empty,
    key_buf: std.ArrayListUnmanaged(u8) = .empty,
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

    /// Proven top-level certifications of the current fill, keyed by the
    /// full state key (`keyOf`: state image plus the chunk-content blob).
    /// Only `alive`/`dead` verdicts of the top-level certification enter;
    /// `undecided`, errors and bounded-search results keep the ordinary
    /// path, so a hit answers exactly as the certificate would. The cache
    /// lives for one fill and is cleared by `reset`. The cache is optional
    /// memory: it is released (`dropCerts`) before the first search of the
    /// fill and whenever a certification had to degrade on an allocation
    /// failure, so its bytes never press required scratch out of the
    /// budget and therefore never change an outcome.
    cert_map: std.StringHashMapUnmanaged(CertVerdict) = .{},
    /// Entry bound of `cert_map`; keys are owned copies and charge the
    /// context temp budget.
    cert_cap: usize = 1 << 12,
    /// Hits served from `cert_map` (diagnostics and tests).
    cert_hits: u64 = 0,
    /// Lookups attempted against `cert_map` (diagnostics and tests: a
    /// nonzero count proves the cache path ran at all).
    cert_lookups: u64 = 0,
    /// Set once the fill has released the cache (see `dropCerts`): the
    /// rest of the fill runs exactly as with the cache off.
    cert_muted: bool = false,

    pub fn init(a: std.mem.Allocator) Cache {
        return .{ .a = a };
    }

    pub fn deinit(self: *Cache) void {
        var it = self.map.keyIterator();
        while (it.next()) |k| self.a.free(k.*);
        self.map.deinit(self.a);
        var cit = self.cert_map.keyIterator();
        while (cit.next()) |k| self.a.free(k.*);
        self.cert_map.deinit(self.a);
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
        self.clearEntries();
        self.cert_muted = false;
    }

    /// Clears the memo and the cached verdicts but keeps a release in
    /// force: a fill that gave the optional certificate memory back keeps
    /// it off until the next top-level `reset`, even when it switches to
    /// another content variant mid-walk. Cleared entries are recomputed;
    /// the retained buffers stay charged, so a later required allocation
    /// can still trigger the caller's release-and-retry rule.
    pub fn resetForVariant(self: *Cache) void {
        self.clearEntries();
    }

    fn clearEntries(self: *Cache) void {
        var it = self.map.keyIterator();
        while (it.next()) |k| self.a.free(k.*);
        self.map.clearRetainingCapacity();
        // Entries go, capacity stays: the table is reused like the memo,
        // and `dropCerts` gives the same bytes back if a proof or a search
        // needs them inside the fill (the release rule keys on capacity,
        // not on the entry count).
        var cit = self.cert_map.keyIterator();
        while (cit.next()) |k| self.a.free(k.*);
        self.cert_map.clearRetainingCapacity();
        if (self.side) |sd| for (self.states.items) |sp| parser.releaseState(sd, sp);
    }

    /// True while any optional certificate memory is held: stored keys, the
    /// map table (also when every entry has been removed) or the buffer
    /// `keyOf` builds the lookup key in. The release rule keys on this, not
    /// on the entry count: an empty table still charges the budget.
    fn certHoldsMemory(self: *const Cache) bool {
        return self.cert_map.capacity() != 0 or self.key_buf.capacity != 0;
    }

    /// Public view of `certHoldsMemory` for callers whose own allocations
    /// can fail outside the filter (the mask walk and its parser feeds).
    pub fn holdsOptional(self: *const Cache) bool {
        return self.certHoldsMemory();
    }

    /// Releases every optional byte and keeps the cache off until the next
    /// `reset`. The filter's release rules cannot see an allocation failure
    /// that happens between certifications; callers that hit one call this
    /// and retry.
    pub fn releaseOptional(self: *Cache) void {
        self.dropCerts();
    }

    /// Gives every byte the certificate cache holds back and keeps it off
    /// for the rest of the fill. The cache is optional memory: whatever
    /// the allocator could not grant to a proof or to the search scratch
    /// must not be sitting in `cert_map` or `key_buf`.
    fn dropCerts(self: *Cache) void {
        var it = self.cert_map.keyIterator();
        while (it.next()) |k| self.a.free(k.*);
        self.cert_map.deinit(self.a);
        self.cert_map = .{};
        self.key_buf.deinit(self.a);
        self.key_buf = .empty;
        self.cert_muted = true;
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

    /// Stores a proven top-level certificate under an owned copy of the
    /// full key. A full cache or a failed allocation is not an error: the
    /// caller falls back to a normal certification next time.
    fn certPut(self: *Cache, key: []const u8, v: CertVerdict) void {
        if (self.cert_muted) return;
        if (self.cert_map.count() >= self.cert_cap) return;
        const gop = self.cert_map.getOrPut(self.a, key) catch return;
        if (gop.found_existing) {
            gop.value_ptr.* = v;
            return;
        }
        gop.key_ptr.* = self.a.dupe(u8, key) catch {
            _ = self.cert_map.remove(key);
            return;
        };
        gop.value_ptr.* = v;
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

/// Top-level certification with the optional-memory rule: while the
/// certificate cache holds bytes, a proof that had to degrade to
/// `undecided` on an allocation failure (`cert_oom`) gets the cache
/// released and is re-proven once. Certification is deterministic and the
/// cache only changes what the allocator has to give, so the second attempt
/// is exactly the one a cache-off caller would have run.
fn certifyTop(g: *const grammar.Grammar, st: *const parser.State, cache: *Cache, side: *parser.Side) Cert {
    cert_oom = false;
    var cert = certifyState(g, st, side);
    if (cert == .undecided and cert_oom and !cache.cert_muted and cache.certHoldsMemory()) {
        cache.dropCerts();
        cert_oom = false;
        cert = certifyState(g, st, side);
    }
    return cert;
}

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
    if (test_hooks_enabled and forcedUnknown(st, side.a)) return noteUnknown(st);
    if (!g.needs_reachability) return explore(g, tok, st, 0, cache, w, side);
    if (cache.fill_budget == 0) {
        // The shared per-fill budget is spent (an earlier unprovable state
        // of this fill drained it). Certification is exact and
        // budget-independent (search() below re-certifies the same way), so
        // settle what it can for free and reserve ResourceLimit - which the
        // callers now propagate, failing the mask call (ADR-0005 D3) - for
        // the genuinely undecided.
        return switch (certifyTop(g, st, cache, side)) {
            .alive => true,
            .dead => false,
            .undecided => {
                recordFailure(g, st, side, .fail_budget0);
                return noteUnknown(st);
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
    var budget: u64 = if (test_hooks_enabled and test_overrides.tier_budget != 0) test_overrides.tier_budget else 1 << 5;
    // Top-level certification with the simulation certificate enabled
    // (in_search is false here): a concrete completing suffix settles the
    // state before any search tier runs. Proven verdicts are reused from
    // the per-fill certificate cache; `Cache.keyOf` is built once, a hit
    // answers directly and a miss stores an owned copy.
    var cert_key: ?[]const u8 = null;
    if (!cache.cert_muted and !certCacheOff()) {
        if (cache.keyOf(st)) |kb| {
            cache.cert_lookups += 1;
            if (cache.cert_map.get(kb)) |v| {
                cache.cert_hits += 1;
                return v == .alive;
            }
            cert_key = kb;
        } else |_| {}
    }
    const cert = certifyTop(g, st, cache, side);
    if (cache.cert_muted) cert_key = null; // the key buffer is gone
    switch (cert) {
        .alive => {
            if (cert_key) |kb| cache.certPut(kb, .alive);
            return true;
        },
        .dead => {
            if (cert_key) |kb| cache.certPut(kb, .dead);
            return false;
        },
        .undecided => {},
    }
    // The state needs the search: its scratch pool and memo are required
    // memory, the certificate cache is not, so the cache is given back
    // before the first search allocation can compete with it. The rest of
    // the fill then runs exactly as with the cache off - in particular the
    // slice, where certification settles every filtered call and the search
    // never runs, keeps the whole benefit.
    cache.dropCerts();
    und_hist[@backingInt(UndReason.search_entered)] += 1;
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
                return noteUnknown(st);
            },
            else => |e| return e,
        };
        switch (v) {
            .alive => return true,
            .dead => return false,
            .capped => {
                if (cap >= MAX_DEPTH_REACH) {
                    recordFailure(g, st, side, .fail_search);
                    return noteUnknown(st);
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
        // A branch past the frame or thread cap is not exhausted, only
        // out of reach of this context: it stays undecided like a budget
        // cut. Counting it dead would drop the token from the mask with no
        // ResourceLimit, an incomplete mask the thread cap must never
        // cause.
        error.ResourceLimit => {
            cache.budget_out = true;
            return .capped;
        },
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
    var req_hint: [256]bool = @splat(false);
    var any_hint: [256]bool = @splat(false);
    keyHints(g, side, st, &req_hint, &any_hint);
    var pruned: [256]bool = @splat(false);
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
            for (on.props[of.next_idx..], of.next_idx..) |p, pi| {
                if (!propPending(g, side, of, on, pi)) continue;
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
    // Not a failure: counts every state stateAlive hands to the budgeted
    // search after certification left it undecided.
    search_entered,
};

pub const UND_REASONS = @typeInfo(UndReason).@"enum".field_names.len;
pub threadlocal var und_hist: [UND_REASONS]u64 = @splat(0);

threadlocal var und_rec: bool = false;
threadlocal var und_pend: [UND_REASONS]u64 = @splat(0);

/// True while the budgeted search is running: the simulation certificate
/// (synth.aliveBySim) runs only for top-level certifications, not per
/// search node.
threadlocal var in_search: bool = false;

/// Set when a certification scratch allocation fails and the proof has to
/// degrade to `undecided` (mid-escape scratch, pattern work arrays, doom
/// marks, the nameable-prefix buffer). The top-level certification
/// consumes it: while the optional certificate cache holds memory, a proof
/// degraded this way is retried once with the cache released, so cached
/// bytes can never turn a decided verdict into an undecided one.
threadlocal var cert_oom: bool = false;

fn note(r: UndReason) void {
    if (und_rec) und_pend[@backingInt(r)] += 1;
}

/// Re-certify the failed state with recording on and commit the pending
/// counts to the histogram. Certification is budget-independent, so this
/// replays exactly the proof attempt that just failed.
fn recordFailure(g: *const grammar.Grammar, st: *const parser.State, side: *parser.Side, r: UndReason) void {
    und_pend = @splat(0);
    und_rec = true;
    _ = certifyState(g, st, side);
    und_rec = false;
    und_pend[@backingInt(r)] += 1;
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
    if (escapeOffset(st)) |k| return certifyMidEscape(g, st, side, k);
    return certifyStateCore(g, st, side);
}

/// What may follow the backslash of a string escape under the canonical
/// escape table: the short escapes, and \u00XX for the other code points
/// below U+0020 (lowercase hex).
const ESCAPE_TAILS = blk: {
    var tails: [34][]const u8 = undefined;
    var n: usize = 0;
    for ("\"\\bfnrt") |c| {
        tails[n] = &[_]u8{c};
        n += 1;
    }
    const hex = "0123456789abcdef";
    for (0..0x20) |code| {
        if (code == 0x08 or code == 0x09 or code == 0x0A or code == 0x0C or code == 0x0D) continue;
        tails[n] = &[_]u8{ 'u', '0', '0', hex[code >> 4], hex[code & 15] };
        n += 1;
    }
    break :blk tails;
};

/// Bytes of the escape already read after the backslash when the threads
/// are mid escape. All threads read the same bytes, so they agree on it;
/// the first string machine on a thread top answers.
fn escapeOffset(st: *const parser.State) ?usize {
    for (st.threads[0..st.n]) |*t| {
        if (t.len == 0) continue;
        const ss: parser.StrState = switch (t.frames[t.len - 1]) {
            .str => |f| f.state,
            .str_excl => |f| f.state,
            .str_pat => |f| f.state,
            .open_obj => |f| if (f.phase == .key_str) f.u.key.st else continue,
            else => continue,
        };
        return switch (ss) {
            .open, .normal => null,
            .escape => 0,
            .u0 => 1,
            .u00 => 2,
            .uhex1 => 3,
            .uhex2 => 4,
        };
    }
    return null;
}

/// Mid escape the state is alive iff the state after one of the finitely
/// many escape endings is, and dead iff every ending is refused or dead.
fn certifyMidEscape(g: *const grammar.Grammar, st: *const parser.State, side: *parser.Side, k: usize) Cert {
    const bufs = side.a.alloc(parser.State, 2) catch {
        cert_oom = true;
        return .undecided;
    };
    defer side.a.free(bufs);
    var undecided = false;
    for (ESCAPE_TAILS, 0..) |tail, i| {
        if (tail.len <= k or (k > 0 and tail[0] != 'u')) continue;
        // Endings of the \u forms share suffixes past their first bytes.
        const rest = tail[k..];
        var dup = false;
        for (ESCAPE_TAILS[0..i]) |prev| {
            if (prev.len > k and std.mem.eql(u8, prev[k..], rest)) dup = true;
        }
        if (dup) continue;
        parser.feedBytes(g, side, st, rest, &bufs[0], &bufs[1]) catch |e| switch (e) {
            error.Parse => continue,
            else => {
                undecided = true;
                continue;
            },
        };
        defer parser.releaseState(side, &bufs[0]);
        switch (certifyStateCore(g, &bufs[0], side)) {
            .alive => return .alive,
            .dead => {},
            .undecided => undecided = true,
        }
    }
    return if (undecided) .undecided else .dead;
}

fn certifyStateCore(g: *const grammar.Grammar, st: *const parser.State, side: *parser.Side) Cert {
    var tc: [parser.MAX_THREADS_CAP]Cert = undefined;
    // base[i]: aggregate certificate of thread i's NON-comb frames.
    var base: [parser.MAX_THREADS_CAP]Cert = undefined;
    var n_dead: usize = 0;
    // Numeric allOf groups decided jointly (ADR-0011): the top leaf frame
    // of each member thread takes the group verdict instead of its own.
    var ng: NumGroups = .{};
    numGroups(g, st, side.a, &ng, null, null);
    for (st.threads[0..st.n], 0..) |*t, i| {
        var c: Cert = .alive;
        var has_comb = false;
        for (t.frames[0..t.len], 0..) |*f, d| {
            if (f.* == .comb) {
                has_comb = true;
                continue;
            }
            var fc = if (d + 1 == t.len and ng.ov[i] != null) ng.ov[i].? else certifyFrame(g, f, side);
            if (fc == .alive and f.* == .repeat and d + 1 < t.len and repeatInFlightUnproven(g, t, d)) fc = .undecided;
            switch (fc) {
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
        if (tc[i] != .dead and (repeatContainsDoom(g, t) or openObjDepValueDoom(g, side, t) or openObjDepPrefixDoom(g, side, t))) {
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
                        // if/then/else shadow doom: with the then path dead, the
                        // else path needs a completion the if branch rejects;
                        // when every live else thread is linked to or
                        // subsumed by a live if thread there is none.
                        const doomed = switch (cb.kind) {
                            .oneof => oneOfLinkedDoom(g, side.a, st, tc[0..st.n], inst),
                            .ifelse => ifElseShadowDoom(g, side.a, st, tc[0..st.n], inst, cb, live),
                            .allof => false,
                        };
                        if (!doomed) continue;
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
    for (insts[0..n_inst], 0..) |inst, k| {
        for (ng.ok[0..ng.n_ok]) |ok_inst| {
            if (ok_inst == inst) inst_ok[k] = true;
        }
    }
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
            var ok_branch: [3]bool = @splat(false);
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
    var arena = Arena.init(side.a);
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
/// live thread t of branch b0 provably owes a future dispatch of some raw
/// key spelling K_t: it carries, above its comb frame, an open_obj frame
/// with K_t still outstanding (an unseen extra_required name or a required
/// declared prop at/after next_idx; a mid-key frame counts only when the
/// typed raw prefix can no longer spell K_t). Every joint completion of the
/// group is a continuation in which some b0 thread t survives end-to-end,
/// so the byte run `"K_t"` (raw) is fed at some point with that thread at
/// a key boundary. If every live thread of every OTHER branch carries,
/// above the same comb instance, exactly one container frame and it is a
/// closed .object whose remaining key candidates (idx, or cur when
/// mid-key/value) exclude K_t -
/// with only escape-free value machines (literal/number family) above it -
/// then feeding `"K_t"` kills every such thread: the closed machine accepts
/// only its remaining declared keys at a key position, a number/literal
/// value machine dies at the closing quote or the following colon, and the
/// object completing before K_t fails the allOf verdict because no obligated
/// b0 thread can vote. (String-family frames above the object could absorb
/// the key bytes as escaped content, so they defer.) A thread of another
/// branch that sits in a nested allOf also dies on `"K_t"` when some branch
/// of that allOf consists of such threads (threadDiesOnKey): the nested
/// allOf fails with that branch. The group is dead.
/// Narrow on purpose: any other shape defers to the budgeted search.
fn combDeadAllOf(g: *const grammar.Grammar, side: *parser.Side, st: *const parser.State, tc: []const Cert, inst: u32) bool {
    // The argument holds per thread: a branch is doomed when each of its
    // live threads (its alternatives, e.g. the branches of a nested anyOf)
    // owes some key that every live thread of the other branches blocks.
    var branches: u64 = 0;
    for (st.threads[0..st.n], 0..) |*t, i| {
        if (tc[i] == .dead) continue;
        const d0 = combFrameIdx(t, inst) orelse continue;
        branches |= @as(u64, 1) << @intCast(t.frames[d0].comb.branch);
    }
    var b0: u6 = 0;
    while (true) : (b0 += 1) {
        if ((branches >> b0) & 1 != 0 and branchKeyDoomed(g, side, st, tc, inst, b0)) return true;
        if (b0 == 63) break;
    }
    return false;
}

fn branchKeyDoomed(g: *const grammar.Grammar, side: *parser.Side, st: *const parser.State, tc: []const Cert, inst: u32, b0: u6) bool {
    for (st.threads[0..st.n], 0..) |*t, i| {
        if (tc[i] == .dead) continue;
        const d0 = combFrameIdx(t, inst) orelse continue;
        if (t.frames[d0].comb.branch != b0) continue;
        var keys: [8][]const u8 = undefined;
        const n = owedKeys(g, side, t, d0 + 1, &keys) orelse return false;
        var found = false;
        for (keys[0..n]) |k| {
            if (blockedElsewhere(g, side, st, tc, inst, b0, k)) {
                found = true;
                break;
            }
        }
        if (!found) return false;
    }
    return true;
}

/// Every live thread of the instance outside branch b0 dies on raw key K.
fn blockedElsewhere(g: *const grammar.Grammar, side: *const parser.Side, st: *const parser.State, tc: []const Cert, inst: u32, b0: u6, k: []const u8) bool {
    var any = false;
    for (st.threads[0..st.n], 0..) |*t, i| {
        if (tc[i] == .dead) continue;
        const d0 = combFrameIdx(t, inst) orelse continue;
        if (t.frames[d0].comb.branch == b0) continue;
        if (!threadDiesOnKey(g, side, st, tc, t, d0 + 1, k, 3)) return false;
        any = true;
    }
    return any;
}

/// The thread dies on a fed raw key `"K"`: it blocks K itself
/// (threadBlocksKey), or the frame at `from` is a branch of a nested allOf
/// one of whose branches has every live thread dying on K, which fails
/// that allOf and with it every thread inside it.
fn threadDiesOnKey(g: *const grammar.Grammar, side: *const parser.Side, st: *const parser.State, tc: []const Cert, t: *const parser.Thread, from: usize, k: []const u8, depth: u8) bool {
    if (threadBlocksKey(g, side, t, from, k)) return true;
    if (depth == 0 or from >= t.len) return false;
    const f = &t.frames[from];
    if (f.* != .comb or g.node(f.comb.node).comb.kind != .allof) return false;
    const inner = f.comb.inst;
    var branches: u64 = 0;
    for (st.threads[0..st.n], 0..) |*u, i| {
        if (tc[i] == .dead) continue;
        const d = combFrameIdx(u, inner) orelse continue;
        branches |= @as(u64, 1) << @intCast(u.frames[d].comb.branch);
    }
    var c: u6 = 0;
    while (true) : (c += 1) {
        if ((branches >> c) & 1 != 0 and branchDiesOnKey(g, side, st, tc, inner, c, k, depth - 1)) return true;
        if (c == 63) break;
    }
    return false;
}

fn branchDiesOnKey(g: *const grammar.Grammar, side: *const parser.Side, st: *const parser.State, tc: []const Cert, inst: u32, c: u6, k: []const u8, depth: u8) bool {
    var any = false;
    for (st.threads[0..st.n], 0..) |*t, i| {
        if (tc[i] == .dead) continue;
        const d = combFrameIdx(t, inst) orelse continue;
        if (t.frames[d].comb.branch != c) continue;
        if (!threadDiesOnKey(g, side, st, tc, t, d + 1, k, depth)) return false;
        any = true;
    }
    return any;
}

/// Raw keys the thread provably still owes (open_obj frames at/above
/// `from`: unseen extra_required names and pending required declared
/// props; a mid-key frame only for names its clean typed prefix can no
/// longer spell). Null when there are more than out.len.
fn owedKeys(g: *const grammar.Grammar, side: *parser.Side, t: *const parser.Thread, from: usize, out: *[8][]const u8) ?usize {
    var n: usize = 0;
    for (t.frames[from..t.len]) |*f| {
        if (f.* != .open_obj) continue;
        const of = &f.open_obj;
        if (of.phase == .key_str and (of.u.key.st != .normal or of.u.key.rem != 0)) continue;
        const on = &g.node(of.node).open_obj;
        const kb: []const u8 = if (of.phase == .key_str) side.chunkAt(of.key).payload.items else "";
        for (on.extra_required) |lit| {
            const name = g.literalBytes(lit);
            if (seenContainsC(side, of.seen, name)) continue;
            if (of.phase == .key_str and std.mem.startsWith(u8, name, kb)) continue;
            if (n >= out.len) return null;
            out[n] = name;
            n += 1;
        }
        for (on.props[of.next_idx..], of.next_idx..) |p, pi| {
            if (!p.required or !propPending(g, side, of, on, pi)) continue;
            const kl = g.literalBytes(p.key);
            const name = kl[1 .. kl.len - 2];
            if (of.phase == .key_str and std.mem.startsWith(u8, name, kb)) continue;
            if (n >= out.len) return null;
            out[n] = name;
            n += 1;
        }
    }
    return n;
}

fn combFrameIdx(t: *const parser.Thread, inst: u32) ?usize {
    for (t.frames[0..t.len], 0..) |*f, d| {
        if (f.* == .comb and f.comb.inst == inst) return d;
    }
    return null;
}

/// Thread provably dies on any fed raw key spelling `"K"` (see
/// combDeadAllOf): its only container frame at/above `from` is an object
/// that refuses K from now on, with only escape-free value machines above
/// it. A closed object refuses K when its remaining candidates exclude K;
/// an open object (openObjBlocksKey) when the dispatch of K fails for
/// reasons that only accumulate.
fn threadBlocksKey(g: *const grammar.Grammar, side: *const parser.Side, t: *const parser.Thread, from: usize, k: []const u8) bool {
    var containers: usize = 0;
    var obj: ?*const parser.Frame = null;
    for (t.frames[from..t.len]) |*f| {
        switch (f.*) {
            .object, .open_obj => {
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
    if (f.* == .open_obj) return openObjBlocksKey(g, side, &f.open_obj, k);
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

/// The open object refuses key K at every later dispatch
/// (parser.openObjDispatch): propertyNames false; a declared K already
/// passed in declared order (next_idx only grows), or seen in an order-free
/// copy; an undeclared K already seen (the seen set only grows).
fn openObjBlocksKey(g: *const grammar.Grammar, side: *const parser.Side, of: *const parser.OpenObjFrame, k: []const u8) bool {
    const on = &g.node(of.node).open_obj;
    if (on.names_forbidden) return true;
    for (on.props, 0..) |p, i| {
        const kl = g.literalBytes(p.key);
        if (!std.mem.eql(u8, kl[1 .. kl.len - 2], k)) continue;
        if (on.any_order) return seenContainsC(side, of.seen, k);
        return i < of.next_idx;
    }
    return seenContainsC(side, of.seen, k);
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
        if (!framesLinked(g, alloc, inst, fa, fb)) return false;
    }
    return true;
}

/// Thread a of a oneOf branch accepts no continuation that thread b of the
/// same branch does not: the stacks are linked below the top, and on top
/// an integer-by-value machine sits beside a number machine in the same
/// automaton state. The branch votes as b does whatever a does.
fn subsumedThread(g: *const grammar.Grammar, alloc: std.mem.Allocator, inst: u32, a: *const parser.Thread, b: *const parser.Thread) bool {
    if (a.len != b.len or a.len == 0) return false;
    const top = a.len - 1;
    for (a.frames[0..top], b.frames[0..top]) |*fa, *fb| {
        if (!framesLinked(g, alloc, inst, fa, fb)) return false;
    }
    const fa = &a.frames[top];
    const fb = &b.frames[top];
    return fa.* == .int_num and fb.* == .num_v and fa.int_num.st == fb.num_v.st;
}

fn framesLinked(g: *const grammar.Grammar, alloc: std.mem.Allocator, inst: u32, fa: *const parser.Frame, fb: *const parser.Frame) bool {
    {
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
            return numExpLockstep(fa, fb);
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
        } else if (fa.* == .repeat or fa.* == .choice or fa.* == .str or fa.* == .num_v or
            fa.* == .int_v or fa.* == .int_num or fa.* == .not_int_num or fa.* == .object or
            fa.* == .open_obj)
        {
            // Interchangeable subschemas compiled twice (oneOf branches over
            // the same "any" tower, or `true` branches): frames whose state
            // bytes agree and whose nodes are structurally equal accept
            // identical byte runs from here, so they vote in lockstep like
            // identical frames. Each of these frames starts with its node;
            // side-store handles in the rest compare by identity.
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
    var pairs: std.ArrayListUnmanaged([2]u32) = .empty;
    defer pairs.deinit(alloc);
    var idx: std.AutoHashMapUnmanaged([2]u32, u32) = .{};
    defer idx.deinit(alloc);
    var kids: std.ArrayListUnmanaged(std.ArrayListUnmanaged([2]u32)) = .empty;
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
    try kids.append(alloc, .empty);
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
                try kids.append(alloc, .empty);
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

fn ifElseShadowDoom(g: *const grammar.Grammar, alloc: std.mem.Allocator, st: *const parser.State, tc: []const Cert, inst: u32, cb: *const grammar.Comb, live: u64) bool {
    const then_ok = (live & 1) != 0 and (cb.branches[1] == grammar.COMB_NONE or (live & 2) != 0);
    if (then_ok or cb.branches[2] == grammar.COMB_NONE) return false;
    for (st.threads[0..st.n], 0..) |*e, j| {
        if (tc[j] == .dead or combBranchOf(e, inst) != 2) continue;
        var shadowed = false;
        for (st.threads[0..st.n], 0..) |*i, k| {
            if (tc[k] == .dead or combBranchOf(i, inst) != 0) continue;
            if (linkedThreads(g, alloc, inst, e, i) or subsumedThread(g, alloc, inst, e, i)) {
                shadowed = true;
                break;
            }
        }
        if (!shadowed) return false;
    }
    return true;
}

fn combBranchOf(t: *const parser.Thread, inst: u32) ?u8 {
    for (t.frames[0..t.len]) |*f| {
        if (f.* == .comb and f.comb.inst == inst) return f.comb.branch;
    }
    return null;
}

/// See the vote-link rule at the call site: after dropping threads another
/// thread of their branch subsumes, every live branch thread of the oneOf
/// instance is linked to a live thread of another branch.
fn oneOfLinkedDoom(g: *const grammar.Grammar, alloc: std.mem.Allocator, st: *const parser.State, tc: []const Cert, inst: u32) bool {
    var threads: [parser.MAX_THREADS_CAP]usize = undefined;
    var branch: [parser.MAX_THREADS_CAP]u8 = undefined;
    var nb: usize = 0;
    for (st.threads[0..st.n], 0..) |*t, j| {
        if (tc[j] == .dead) continue;
        for (t.frames[0..t.len]) |*f| {
            if (f.* == .comb and f.comb.inst == inst) {
                threads[nb] = j;
                branch[nb] = f.comb.branch;
                nb += 1;
                break;
            }
        }
    }
    if (nb < 2) return false;
    // A branch votes as the OR of its threads: a thread whose accepted
    // continuations another thread of the same branch also accepts does
    // not change the vote.
    var keep: [parser.MAX_THREADS_CAP]bool = @splat(true);
    for (0..nb) |k| {
        for (0..nb) |m| {
            if (m == k or !keep[m] or branch[m] != branch[k]) continue;
            if (subsumedThread(g, alloc, inst, &st.threads[threads[k]], &st.threads[threads[m]])) {
                keep[k] = false;
                break;
            }
        }
    }
    // Linked threads accept in lockstep; a group spanning two branches
    // gives every boundary it accepts at least two votes. With every group
    // spanning two branches the verdict is never "exactly one".
    var gid: [parser.MAX_THREADS_CAP]usize = undefined;
    for (0..nb) |k| {
        gid[k] = k;
        if (!keep[k]) continue;
        for (0..k) |p| {
            if (keep[p] and linkedThreads(g, alloc, inst, &st.threads[threads[k]], &st.threads[threads[p]])) {
                gid[k] = gid[p];
                break;
            }
        }
    }
    for (0..nb) |k| {
        if (!keep[k] or gid[k] != k) continue;
        var spans = false;
        for (0..nb) |m| {
            if (keep[m] and gid[m] == k and branch[m] != branch[k]) spans = true;
        }
        if (!spans) return false;
    }
    return true;
}

// ---- numeric conjunction groups (ADR-0011) --------------------------------

/// Result of the numeric group pass over one state: ov[i] is the joint
/// verdict that replaces thread i's top leaf certificate (null: no decided
/// group), ok lists the comb instances of the groups proven alive.
const NumGroups = struct {
    ov: [parser.MAX_THREADS_CAP]?Cert = @splat(null),
    ok: [64]u32 = undefined,
    n_ok: usize = 0,
    /// Verdict of the last evaluated group (test hooks).
    last: ?numcert.Verdict = null,
};

const MAX_GROUP_TERMS = 64;
const MAX_GROUP_NEST = 8;
const ThreadMask = u128;

comptime {
    std.debug.assert(parser.MAX_THREADS_CAP <= @bitSizeOf(ThreadMask));
}

fn isNumLeaf(f: *const parser.Frame) bool {
    return switch (f.*) {
        .num_v, .int_num, .num_range, .num_mult, .num_excl, .num_const => true,
        else => false,
    };
}

/// Lowest frame of the run of comb frames directly below a numeric leaf
/// on top of the thread; null when there is no such run.
fn numRunRoot(t: *const parser.Thread) ?usize {
    if (t.len < 2) return null;
    if (!isNumLeaf(&t.frames[t.len - 1])) return null;
    var d: usize = t.len - 1;
    while (d > 0 and t.frames[d - 1] == .comb) d -= 1;
    if (d == t.len - 1) return null;
    return d;
}

/// A conjunction of leaf threads: accepted (pos) and rejected (neg).
const Term = struct { pos: ThreadMask = 0, neg: ThreadMask = 0 };

const Dnf = struct {
    terms: [MAX_GROUP_TERMS]Term = undefined,
    n: usize = 0,

    fn add(self: *Dnf, t: Term) error{TooLarge}!void {
        if (t.pos & t.neg != 0) return; // a leaf both accepts and rejects
        if (self.n >= MAX_GROUP_TERMS) return error.TooLarge;
        self.terms[self.n] = t;
        self.n += 1;
    }

    fn top() Dnf {
        var d: Dnf = .{};
        d.terms[0] = .{};
        d.n = 1;
        return d;
    }

    fn andWith(x: Dnf, y: Dnf) error{TooLarge}!Dnf {
        var out: Dnf = .{};
        for (x.terms[0..x.n]) |a| {
            for (y.terms[0..y.n]) |b| try out.add(.{ .pos = a.pos | b.pos, .neg = a.neg | b.neg });
        }
        return out;
    }

    fn orWith(x: *Dnf, y: Dnf) error{TooLarge}!void {
        for (y.terms[0..y.n]) |t| try x.add(t);
    }
};

/// A comb instance (or one branch of it) within a numeric group: its DNF
/// and the DNF of its negation; no is null where the negation is not
/// modeled (a oneOf).
const Pair = struct { yes: Dnf, no: ?Dnf };

/// DNF of a comb instance whose branch parses are numeric leaves or nested
/// instances (each thread = one leaf): allOf is the conjunction of its
/// branches, oneOf the union over k of branch k with every other branch
/// negated, if/then/else (if and then) or (not if and else); an absent
/// branch is true, a branch with no live parse false. An empty DNF means
/// the instance can never accept.
fn groupDnf(g: *const grammar.Grammar, st: *const parser.State, members: ThreadMask, d: usize, nest: usize) error{TooLarge}!Pair {
    if (nest > MAX_GROUP_NEST) return error.TooLarge;
    var first: usize = 0;
    while ((members >> @intCast(first)) & 1 == 0) first += 1;
    const cf = st.threads[first].frames[d].comb;
    const cb = &g.node(cf.node).comb;
    var br: [16]Pair = undefined;
    if (cb.branches.len > br.len) return error.TooLarge;
    for (0..cb.branches.len) |b| {
        if (cb.branches[b] == grammar.COMB_NONE) {
            br[b] = .{ .yes = Dnf.top(), .no = .{} };
            continue;
        }
        // A branch accepts when one of its alternatives does.
        var yes: Dnf = .{};
        var no: ?Dnf = Dnf.top();
        var seen: ThreadMask = 0;
        for (0..st.n) |j| {
            const bit = @as(ThreadMask, 1) << @intCast(j);
            if (members & bit == 0 or seen & bit != 0) continue;
            const t = &st.threads[j];
            if (t.frames[d].comb.branch != b) continue;
            if (t.len == d + 2) {
                try yes.add(.{ .pos = bit });
                if (no) |*n| {
                    var leaf_no: Dnf = .{};
                    try leaf_no.add(.{ .neg = bit });
                    n.* = try Dnf.andWith(n.*, leaf_no);
                }
                seen |= bit;
                continue;
            }
            const inner = t.frames[d + 1].comb.inst;
            var sub: ThreadMask = 0;
            for (0..st.n) |k| {
                const kb = @as(ThreadMask, 1) << @intCast(k);
                if (members & kb == 0) continue;
                const tk = &st.threads[k];
                if (tk.len > d + 2 and tk.frames[d].comb.branch == b and tk.frames[d + 1].comb.inst == inner) sub |= kb;
            }
            seen |= sub;
            const ip = try groupDnf(g, st, sub, d + 1, nest + 1);
            try yes.orWith(ip.yes);
            if (no) |*n| {
                n.* = if (ip.no) |inn| try Dnf.andWith(n.*, inn) else {
                    no = null;
                    continue;
                };
            }
        }
        br[b] = .{ .yes = yes, .no = no };
    }
    const nb = cb.branches.len;
    switch (cb.kind) {
        .allof => {
            var yes = Dnf.top();
            var no: ?Dnf = .{};
            for (br[0..nb]) |x| {
                yes = try Dnf.andWith(yes, x.yes);
                if (no) |*n| {
                    if (x.no) |xn| try n.orWith(xn) else no = null;
                }
            }
            return .{ .yes = yes, .no = no };
        },
        .oneof => {
            var yes: Dnf = .{};
            for (0..nb) |k| {
                var term = br[k].yes;
                for (0..nb) |j| {
                    if (j == k) continue;
                    term = try Dnf.andWith(term, br[j].no orelse return error.TooLarge);
                }
                try yes.orWith(term);
            }
            return .{ .yes = yes, .no = null };
        },
        .ifelse => {
            const if_no = br[0].no orelse return error.TooLarge;
            var yes = try Dnf.andWith(br[0].yes, br[1].yes);
            try yes.orWith(try Dnf.andWith(if_no, br[2].yes));
            var no: ?Dnf = null;
            if (br[1].no != null and br[2].no != null) {
                var n = try Dnf.andWith(br[0].yes, br[1].no.?);
                try n.orWith(try Dnf.andWith(if_no, br[2].no.?));
                no = n;
            }
            return .{ .yes = yes, .no = no };
        },
    }
}

/// Finds the maximal numeric allOf groups of the state (every thread of
/// the instance is an allOf run ending in a numeric leaf) and decides each
/// jointly (numcert.termVerdict over the DNF terms). `wit` receives the
/// witness of the last alive group (test hooks).
fn numGroups(g: *const grammar.Grammar, st: *const parser.State, a: std.mem.Allocator, out: *NumGroups, wit: ?*numcert.Witness, n_hint: ?i64) void {
    var roots: [parser.MAX_THREADS_CAP]?usize = undefined;
    var any = false;
    for (st.threads[0..st.n], 0..) |*t, i| {
        roots[i] = numRunRoot(t);
        if (roots[i] != null) any = true;
    }
    if (!any) return;
    var done: ThreadMask = 0;
    for (st.threads[0..st.n], 0..) |*t, i| {
        const d0 = roots[i] orelse continue;
        if ((done >> @intCast(i)) & 1 != 0) continue;
        const inst = t.frames[d0].comb.inst;
        var members: ThreadMask = 0;
        var valid = true;
        for (st.threads[0..st.n], 0..) |*t2, j| {
            const idx = combFrameIdx(t2, inst) orelse continue;
            if (roots[j] == null or roots[j].? != idx) {
                valid = false;
                break;
            }
            members |= @as(ThreadMask, 1) << @intCast(j);
        }
        done |= members;
        if (!valid) continue;
        const dnf = (groupDnf(g, st, members, d0, 0) catch continue).yes;
        var verdict: numcert.Verdict = .dead;
        var leaves: [64]numcert.Lit = undefined;
        for (dnf.terms[0..dnf.n]) |term| {
            var nl: usize = 0;
            var over = false;
            for (0..st.n) |j| {
                const bit = @as(ThreadMask, 1) << @intCast(j);
                if ((term.pos | term.neg) & bit == 0) continue;
                if (nl >= leaves.len) {
                    over = true;
                    break;
                }
                const tj = &st.threads[j];
                leaves[nl] = .{ .f = &tj.frames[tj.len - 1], .code = if (term.pos & bit != 0) .pos else .neg };
                nl += 1;
            }
            // No literal: every number completion qualifies.
            const v: numcert.Verdict = if (over) .undecided else if (nl == 0) .alive else numcert.termVerdictLits(.{ .g = g, .a = a, .wit = wit, .n_hint = n_hint }, leaves[0..nl]);
            if (v == .alive) {
                verdict = .alive;
                break;
            }
            if (v == .undecided) verdict = .undecided;
        }
        out.last = verdict;
        const c: Cert = switch (verdict) {
            .alive => .alive,
            .dead => .dead,
            .undecided => continue,
        };
        for (0..st.n) |j| {
            if ((members >> @intCast(j)) & 1 != 0) out.ov[j] = c;
        }
        if (c != .alive) continue;
        for (0..st.n) |j| {
            if ((members >> @intCast(j)) & 1 == 0) continue;
            const tj = &st.threads[j];
            for (tj.frames[d0 .. tj.len - 1]) |*f| {
                const in = f.comb.inst;
                var known = false;
                for (out.ok[0..out.n_ok]) |o| {
                    if (o == in) known = true;
                }
                if (!known and out.n_ok < out.ok.len) {
                    out.ok[out.n_ok] = in;
                    out.n_ok += 1;
                }
            }
        }
    }
}

/// Test hook (src/test_hooks.zig): the joint verdict of the numeric group
/// covering the state, with its witness; null when the state is not a
/// single numeric allOf group or a plain numeric leaf.
pub fn numGroupVerdictHinted(g: *const grammar.Grammar, st: *const parser.State, a: std.mem.Allocator, wit: *numcert.Witness, n_hint: ?i64) ?numcert.Verdict {
    wit.* = .none;
    if (st.n == 1 and st.threads[0].len >= 1 and isNumLeaf(&st.threads[0].frames[st.threads[0].len - 1]) and numRunRoot(&st.threads[0]) == null) {
        const t = &st.threads[0];
        const leaf: [1]*const parser.Frame = .{&t.frames[t.len - 1]};
        return numcert.termVerdict(.{ .g = g, .a = a, .wit = wit, .n_hint = n_hint }, &leaf);
    }
    var ng: NumGroups = .{};
    numGroups(g, st, a, &ng, wit, n_hint);
    return ng.last;
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
        .repeat => certRepeat(g, f, side),
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
/// next candidate index in idx; key_lit owes one of the props still
/// possible for the key bytes read, value owes the current prop cur (its
/// value is not pushed yet in key_lit and certifies itself in the frames
/// above in value); both continue from cur+1 (idx is clobbered with
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
            // Any property still possible for the key bytes read; those
            // after cur are optional up to the first required one, which
            // the obligations from cur + 1 include.
            var own: u8 = grammar.NE_NO;
            var c: ?u32 = f.object.cur;
            while (c) |j| : (c = parser.nextKeyCandidate(g, props, j, f.object.idx, null)) {
                switch (g.nonempty[props[j].value]) {
                    grammar.NE_YES => {
                        own = grammar.NE_YES;
                        break;
                    },
                    grammar.NE_NO => {},
                    else => own = grammar.NE_UNKNOWN,
                }
            }
            switch (own) {
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
/// unmet minContains whose in-flight element started with a byte no value
/// of the contains schema starts with (the element's root machine bounds
/// that byte; comb frames between the repeat and the root are skipped,
/// since the element value is the same on every branch thread). The
/// element then never matches, and the remaining slots after it must cover
/// the deficit on their own; when they cannot, no completion of this
/// thread exists (the parser taps the contains reparse at element close and
/// rejects the final ']').
fn repeatContainsDoom(g: *const grammar.Grammar, t: *const parser.Thread) bool {
    for (t.frames[0..t.len], 0..) |*f, d| {
        if (f.* != .repeat) continue;
        const rep = &g.node(f.repeat.node).repeat;
        const cn = rep.contains orelse continue;
        if (f.repeat.matched >= rep.min_contains) continue;
        if (rep.max == grammar.UNBOUNDED) continue;
        // Quiescent repeat: certRepeat's deficit rule applies instead.
        const r = elemRoot(t, d) orelse continue;
        const started = frameFirstBytes(g, &t.frames[r]) orelse continue;
        var common = g.firstBytes(cn, 0);
        common.setIntersection(started);
        if (common.count() != 0) continue;
        const left = rep.max -| (f.repeat.count + 1);
        if (f.repeat.matched + left < rep.min_contains) return true;
    }
    return false;
}

/// Index of the first non-comb frame above frame `d`: the root machine of
/// the value in flight there. Null when nothing but comb frames sits above
/// (no value in flight, or a completed branch awaiting its boundary).
fn elemRoot(t: *const parser.Thread, d: usize) ?usize {
    var r = d + 1;
    while (r < t.len and t.frames[r] == .comb) r += 1;
    return if (r < t.len) r else null;
}

/// First bytes of the value machine rooted at frame `f` (a superset of the
/// byte it already consumed); null for frames of other kinds.
fn frameFirstBytes(g: *const grammar.Grammar, f: *const parser.Frame) ?grammar.ByteSet {
    var out = grammar.ByteSet.empty;
    switch (f.*) {
        .str, .str_pat, .str_excl => out.set('"'),
        .repeat => out.set('['),
        .object, .open_obj => out.set('{'),
        .int_v, .num_v, .int_num, .num_const, .not_int_num, .num_excl, .num_range, .num_mult => return grammar.number_first,
        .literal => |lf| return g.firstBytes(lf.node, 0),
        .lit_trie => |tf| return g.firstBytes(tf.gnode, 0),
        else => return null,
    }
    return out;
}

/// A closable repeat frame with an element in flight still needs that
/// element accepted when it closes: uniqueItems wants a value not seen yet
/// and a reached maxContains a value that does not match `contains`.
/// Proven only for a plain string element that can still grow (it then has
/// more completions than the seen set holds).
fn repeatInFlightUnproven(g: *const grammar.Grammar, t: *const parser.Thread, d: usize) bool {
    const f = &t.frames[d].repeat;
    const rep = &g.node(f.node).repeat;
    const at_max_contains = rep.contains != null and rep.max_contains != grammar.UNBOUNDED and
        f.matched >= rep.max_contains;
    if (!rep.unique and !at_max_contains) return false;
    if (at_max_contains or d + 2 != t.len) return true;
    return switch (t.frames[d + 1]) {
        .str => |sf| !((sf.state == .open or sf.state == .normal) and sf.count < g.node(sf.node).str.max_len),
        else => true,
    };
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
/// A triggered schema-kind dependency must accept the whole object; when
/// its parser already refuses the captured object prefix, no completion
/// can close the object.
fn openObjDepPrefixDoom(g: *const grammar.Grammar, side: *parser.Side, t: *const parser.Thread) bool {
    for (t.frames[0..t.len]) |*f| {
        if (f.* != .open_obj) continue;
        const of = &f.open_obj;
        const on = &g.node(of.node).open_obj;
        if (!on.capture or of.ann == 0) continue;
        const bytes = side.chunkAt(of.ann).payload.items;
        for (on.deps) |dep| {
            if (dep.kind != .schema) continue;
            if (!seenContainsC(side, of.seen, g.literalBytes(dep.trigger))) continue;
            const ok = parser.nodeAcceptsPrefix(g, side, dep.schema, bytes) catch continue;
            if (!ok) return true;
        }
    }
    return false;
}

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
/// element start); only exact impossibility is certified dead here. A
/// finite uniqueItems element language (uniq_finite) is exhausted when the
/// seen set plus the owed slots outnumber its distinct values.
fn certRepeat(g: *const grammar.Grammar, f: *const parser.Frame, side: *parser.Side) Cert {
    const rep = g.node(f.repeat.node).repeat;
    const closable = f.repeat.count >= rep.min and
        (rep.contains == null or f.repeat.matched >= rep.min_contains);
    if (closable and f.repeat.phase != .body_after_comma) return .alive;
    // At least one more element is owed (deficit or post-comma mandate).
    if (f.repeat.count >= rep.max) return .dead; // no room for it
    // Slots count..upto-1 must each accept a value. The slot in progress
    // (count, when element frames sit above) is included: an empty-language
    // slot can never complete, and a non-empty one composes with its own
    // frames' certificates.
    const upto: usize = if (f.repeat.phase == .body_after_comma)
        @max(rep.min, f.repeat.count + 1)
    else
        rep.min;
    if (rep.unique and rep.uniq_finite > 0) {
        const owed: u64 = upto -| f.repeat.count;
        if (@as(u64, seenCountC(side, f.repeat.seen)) + owed > rep.uniq_finite) return .dead;
    }
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
    // content-dependent.
    if (rep.unique) return .undecided;
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

/// Declared prop i of an open-object frame can still be dispatched: at or
/// after next_idx and, on an order-free node (next_idx stays 0 there),
/// absent from the seen set.
fn propPending(g: *const grammar.Grammar, side: *const parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, i: usize) bool {
    if (i < of.next_idx) return false;
    if (!on.any_order) return true;
    const kl = g.literalBytes(on.props[i].key);
    return !seenContainsC(side, of.seen, kl[1 .. kl.len - 2]);
}

/// The dispatch window of a frame ends at the first pending required prop:
/// in schema order the props beyond it are reachable only over a skip that
/// kills the thread. An order-free node has no such barrier.
fn windowEnds(on: *const grammar.OpenObjNode, p: grammar.Prop) bool {
    return p.required and !on.any_order;
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
    return declaredPropValue(g, on, name) != null;
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

/// Merge one undeclared dispatch side (freshUndeclaredKey) of value
/// non-emptiness `ne` into a witness pair.
fn mergeKeyPath(kp: KeyPath, ne: u8, witness: *bool, witness_unknown: *bool) void {
    switch (kp) {
        .yes => mergePath(ne, witness, witness_unknown),
        .unknown => witness_unknown.* = true,
        .no => {},
    }
}

/// Can a FRESH undeclared key (no typed prefix) dispatch to a non-empty
/// value schema? With a key pattern DFA both dispatch sides are counted
/// exactly (freshUndeclaredKey): the matching keys and the non-matching
/// keys may each be finite and taken (`^line[1-3]$`; the remnant of a
/// near-universal pattern). A literal pattern (a fixed non-empty
/// substring) leaves infinitely many keys on both sides - appending the
/// needle always matches, repeating a code point other than its first
/// never does - and the taken keys are finite.
fn freshUndeclaredPath(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode) KeyPath {
    var witness = false;
    var unknown = false;
    if (on.pattern_dfa) |pd| {
        const descr = g.pat_len[of.node] orelse return .unknown;
        if (g.nonempty[on.pattern_value] != grammar.NE_NO) {
            mergeKeyPath(freshUndeclaredKey(g, side, of, on, pd, descr, "", pd.start(), null, .hit), g.nonempty[on.pattern_value], &witness, &unknown);
        }
        if (g.nonempty[on.value] != grammar.NE_NO) {
            mergeKeyPath(freshUndeclaredKey(g, side, of, on, pd, descr, "", pd.start(), null, .miss), g.nonempty[on.value], &witness, &unknown);
        }
    } else if (on.pattern_lit) |pl| {
        mergePath(g.nonempty[on.pattern_value], &witness, &unknown);
        if (g.literalBytes(pl).len != 0) mergePath(g.nonempty[on.value], &witness, &unknown);
    } else {
        mergePath(g.nonempty[on.value], &witness, &unknown);
    }
    if (witness) return .yes;
    return if (unknown) .unknown else .no;
}

/// Which dispatch side of a key pattern: the keys it matches (pattern
/// value) or the keys it does not match (fallback value).
const KeySide = enum { hit, miss };

/// Is there a key among the completions of the raw prefix kb (DFA state q;
/// with `window`, the completions whose next code point lies in it - a key
/// cut inside a UTF-8 sequence) on the dispatch side `want` that is fresh?
/// Not fresh are the seen keys, the declared names (they dispatch to their
/// declared prop) and the undeclared dependency triggers whose dispatch
/// leads nowhere (undeclaredTriggerCert: a dead one is taken, an
/// undecided one may be). In spec-v1 every decoded key has exactly one raw
/// spelling (canonical escapes), so the completions on that side and the
/// taken keys among them compare by count (dfaKeyCount, exact): more
/// completions than possibly-taken keys leaves a fresh one, no more than
/// the surely-taken ones leaves none, anything between is unknown - as is
/// a key that does not decode or a count without memory. An empty or
/// exhausted analysis is never read as "no key".
fn freshUndeclaredKey(g: *const grammar.Grammar, side: *const parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, pd: *const pattern.Dfa, descr: *const pattern.PatLenDescr, kb: []const u8, q: pattern.RunState, window: ?CpWindow, want: KeySide) KeyPath {
    var taken: u64 = 0;
    var maybe: u64 = 0;
    // Seen keys (distinct by construction).
    if (of.seen != 0) {
        const items = side.chunkAt(of.seen).payload.items;
        if (items.len >= 4) {
            const count = std.mem.readInt(u32, items[0..4], .little);
            var off: usize = 4;
            var n: u32 = 0;
            while (n < count) : (n += 1) {
                if (off + 4 > items.len) return .unknown;
                const len = std.mem.readInt(u32, items[off..][0..4], .little);
                off += 4;
                if (off + len > items.len) return .unknown;
                switch (takenCompletion(pd, items[off .. off + len], kb, window, want)) {
                    .yes => taken += 1,
                    .unknown => return .unknown,
                    .no => {},
                }
                off += len;
            }
        }
    }
    for (on.props) |p| {
        const kl = g.literalBytes(p.key);
        const name = kl[1 .. kl.len - 2];
        if (seenContainsC(side, of.seen, name)) continue;
        switch (takenCompletion(pd, name, kb, window, want)) {
            .yes => taken += 1,
            .unknown => return .unknown,
            .no => {},
        }
    }
    for (on.deps, 0..) |dep, di| {
        const trigger = g.literalBytes(dep.trigger);
        if (declaredName(on, g, trigger) or seenContainsC(side, of.seen, trigger)) continue;
        // One count per trigger name (several entries may share it).
        var dup = false;
        for (on.deps[0..di]) |prev| {
            if (std.mem.eql(u8, g.literalBytes(prev.trigger), trigger)) dup = true;
        }
        if (dup) continue;
        const fate = undeclaredTriggerCert(g, side, of, on, trigger);
        if (fate == .alive) continue;
        switch (takenCompletion(pd, trigger, kb, window, want)) {
            .yes => if (fate == .dead) {
                taken += 1;
            } else {
                maybe += 1;
            },
            .unknown => return .unknown,
            .no => {},
        }
    }
    const total = dfaKeyCount(side.a, pd, descr, q, window, want, taken + maybe + 1) orelse return .unknown;
    if (total > taken + maybe) return .yes;
    if (total <= taken) return .no;
    return .unknown;
}

const CpWindow = struct { lo: u32, hi: u32 };

/// Is the raw key k a completion of the raw prefix kb (with `window`: its
/// next code point lies in the window) on the dispatch side `want`?
fn takenCompletion(pd: *const pattern.Dfa, k: []const u8, kb: []const u8, window: ?CpWindow, want: KeySide) KeyPath {
    if (!std.mem.startsWith(u8, k, kb)) return .no;
    if (window) |w| {
        if (k.len == kb.len) return .no;
        var dec = KeyDecoder{ .kb = k[kb.len..] };
        const cp = dec.next() orelse return .unknown;
        if (cp < w.lo or cp > w.hi) return .no;
    }
    const m = dfaKeyMatches(pd, k) orelse return .unknown;
    return if (m == (want == .hit)) .yes else .no;
}

/// Unicode scalar values in [lo, hi] (no surrogates, at most U+10FFFF):
/// each is one key code point with one canonical raw spelling.
fn scalarWidth(lo: u32, hi: u32) u64 {
    const top = @min(hi, 0x10FFFF);
    if (lo > top) return 0;
    var w: u64 = @as(u64, top - lo) + 1;
    const s_lo = @max(lo, 0xD800);
    const s_hi = @min(top, 0xDFFF);
    if (s_lo <= s_hi) w -= @as(u64, s_hi - s_lo) + 1;
    return w;
}

fn satMulAdd(acc: u64, w: u64, n: u64, cap: u64) u64 {
    const prod = std.math.mul(u64, w, n) catch return cap;
    const sum = std.math.add(u64, acc, prod) catch return cap;
    return @min(sum, cap);
}

/// A DFA state some continuation still takes to an accept state.
fn liveKeyState(pd: *const pattern.Dfa, descr: *const pattern.PatLenDescr, s: pattern.RunState) bool {
    return !pd.dead(s) and descr.reachableIn(s, 0, null);
}

/// Number of decoded strings c (code point sequences) with q --c--> a
/// state that accepts (`hit`) or does not accept (`miss`), saturated at
/// `cap`; with `window`, only the strings whose first code point lies in
/// it (the empty completion excluded). Exact, no search budget:
/// - a code point without a live transition (none at all, or into a state
///   no accept state is reachable from) leaves the pattern for good: on
///   the miss side it starts infinitely many strings (cap), on the hit
///   side none;
/// - otherwise the strings follow live transitions over the states R
///   reachable from q; the states of R that can still reach a counted
///   state (P) carry the count, a cycle inside P makes it infinite (cap),
///   and without one the count sums the weighted paths into the counted
///   states of P (topological order).
/// Null when the work arrays cannot be allocated.
fn dfaKeyCount(a: std.mem.Allocator, pd: *const pattern.Dfa, descr: *const pattern.PatLenDescr, q: pattern.RunState, window: ?CpWindow, want: KeySide, cap: u64) ?u64 {
    const miss = want == .miss;
    if (!liveKeyState(pd, descr, q)) {
        // Every completion misses.
        if (!miss) return 0;
        if (window) |w| return if (scalarWidth(w.lo, w.hi) > 0) cap else 0;
        return cap;
    }
    const n = pd.states.len;
    const IN_R: u8 = 1;
    const IN_P: u8 = 2;
    const flags = a.alloc(u8, n) catch {
        cert_oom = true;
        return null;
    };
    defer a.free(flags);
    @memset(flags, 0);
    const order = a.alloc(u32, n) catch {
        cert_oom = true;
        return null;
    };
    defer a.free(order);
    const paths = a.alloc(u64, n) catch {
        cert_oom = true;
        return null;
    };
    defer a.free(paths);
    @memset(paths, 0);
    var len: usize = 0;
    // Roots: q itself, or the targets of its transitions inside the window
    // weighted by the code points of the overlap.
    if (window) |w| {
        var next: u32 = w.lo;
        for (pd.states[q].trans) |t| {
            if (t.hi < w.lo or t.lo > w.hi) continue;
            const lo = @max(t.lo, w.lo);
            const hi = @min(t.hi, w.hi);
            if (miss and lo > next and scalarWidth(next, lo - 1) > 0) return cap;
            next = hi +| 1;
            const width = scalarWidth(lo, hi);
            if (width == 0) continue;
            if (!liveKeyState(pd, descr, t.to)) {
                if (miss) return cap;
                continue;
            }
            paths[t.to] = satMulAdd(paths[t.to], width, 1, cap);
            if (flags[t.to] & IN_R == 0) {
                flags[t.to] |= IN_R;
                order[len] = t.to;
                len += 1;
            }
        }
        if (miss and next <= w.hi and scalarWidth(next, w.hi) > 0) return cap;
    } else {
        paths[q] = 1;
        flags[q] |= IN_R;
        order[0] = q;
        len = 1;
    }
    // R, breadth first over live transitions; reverse edge counts.
    const rstart = a.alloc(u32, n + 1) catch {
        cert_oom = true;
        return null;
    };
    defer a.free(rstart);
    @memset(rstart, 0);
    var i: usize = 0;
    while (i < len) : (i += 1) {
        const s = order[i];
        var next: u32 = 0;
        for (pd.states[s].trans) |t| {
            if (miss and t.lo > next and scalarWidth(next, t.lo - 1) > 0) return cap;
            next = t.hi +| 1;
            if (scalarWidth(t.lo, t.hi) == 0) continue;
            if (!liveKeyState(pd, descr, t.to)) {
                if (miss) return cap;
                continue;
            }
            rstart[t.to + 1] += 1;
            if (flags[t.to] & IN_R == 0) {
                flags[t.to] |= IN_R;
                order[len] = t.to;
                len += 1;
            }
        }
        if (miss and next <= 0x10FFFF and scalarWidth(next, 0x10FFFF) > 0) return cap;
    }
    for (0..n) |k| rstart[k + 1] += rstart[k];
    const redge = a.alloc(u32, rstart[n]) catch {
        cert_oom = true;
        return null;
    };
    defer a.free(redge);
    const fill = a.alloc(u32, n) catch {
        cert_oom = true;
        return null;
    };
    defer a.free(fill);
    @memcpy(fill, rstart[0..n]);
    for (order[0..len]) |s| {
        for (pd.states[s].trans) |t| {
            if (scalarWidth(t.lo, t.hi) == 0 or !liveKeyState(pd, descr, t.to)) continue;
            redge[fill[t.to]] = s;
            fill[t.to] += 1;
        }
    }
    // P: backwards from the counted states of R (fill reused as a stack).
    var top: usize = 0;
    for (order[0..len]) |s| {
        if (pd.isAccept(s) == miss) continue;
        flags[s] |= IN_P;
        fill[top] = s;
        top += 1;
    }
    while (top > 0) {
        top -= 1;
        const s = fill[top];
        for (redge[rstart[s]..rstart[s + 1]]) |p| {
            if (flags[p] & IN_P != 0) continue;
            flags[p] |= IN_P;
            fill[top] = p;
            top += 1;
        }
    }
    // Topological order of P (Kahn; the in-degrees reuse rstart, every
    // predecessor of a P state is in P). A state left over sits on a cycle.
    var p_count: usize = 0;
    for (order[0..len]) |s| {
        if (flags[s] & IN_P != 0) p_count += 1;
    }
    for (order[0..len]) |s| {
        if (flags[s] & IN_P == 0) continue;
        rstart[s] = 0;
    }
    for (order[0..len]) |s| {
        if (flags[s] & IN_P == 0) continue;
        for (pd.states[s].trans) |t| {
            if (scalarWidth(t.lo, t.hi) == 0 or !liveKeyState(pd, descr, t.to)) continue;
            if (flags[t.to] & IN_P != 0) rstart[t.to] += 1;
        }
    }
    top = 0;
    for (order[0..len]) |s| {
        if (flags[s] & IN_P != 0 and rstart[s] == 0) {
            fill[top] = s;
            top += 1;
        }
    }
    var done: usize = 0;
    var total: u64 = 0;
    while (top > 0) {
        top -= 1;
        const s = fill[top];
        done += 1;
        if (pd.isAccept(s) != miss) total = satMulAdd(total, 1, paths[s], cap);
        for (pd.states[s].trans) |t| {
            const width = scalarWidth(t.lo, t.hi);
            if (width == 0 or !liveKeyState(pd, descr, t.to)) continue;
            if (flags[t.to] & IN_P == 0) continue;
            paths[t.to] = satMulAdd(paths[t.to], width, paths[s], cap);
            rstart[t.to] -= 1;
            if (rstart[t.to] == 0) {
                fill[top] = t.to;
                top += 1;
            }
        }
    }
    if (done < p_count) return cap;
    return @min(total, cap);
}

/// Dependency doom of missing names (the parser.depsResidual check looked
/// ahead). A name that is neither seen nor `x` (the key a witness check
/// dispatches first) is doomed when no completion can dispatch it any
/// more: a declared name behind the skip `k` of an ordered node, a banned
/// trigger, an empty value schema, a forbidden undeclared name or an
/// exhausted property count - or when, as a trigger, it fires a
/// dependentRequired entry with a doomed missing name: its dispatch fails
/// or leaves that name undischargeable. A least fixpoint over the triggers
/// (marks per entry), so a dependency cycle alone dooms nothing. Every
/// condition only grows as keys are dispatched and the skip advances, and
/// a doomed name never gets dispatched, so a doomed name stays doomed. The
/// `loose` variant also counts a value schema that may be empty (or a name
/// that does not decode); doomed only loosely is undecided.
const Doom = struct {
    g: *const grammar.Grammar,
    side: *const parser.Side,
    of: *const parser.OpenObjFrame,
    on: *const grammar.OpenObjNode,
    k: usize,
    x: ?[]const u8,
    loose: bool,
    marks: []bool = &.{},
    oom: bool = false,

    fn init(g: *const grammar.Grammar, side: *const parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, k: usize, x: ?[]const u8, loose: bool) Doom {
        var d: Doom = .{ .g = g, .side = side, .of = of, .on = on, .k = k, .x = x, .loose = loose };
        d.marks = side.a.alloc(bool, on.deps.len) catch {
            d.oom = true;
            cert_oom = true;
            return d;
        };
        @memset(d.marks, false);
        var changed = true;
        while (changed) {
            changed = false;
            for (on.deps, 0..) |dep, di| {
                if (d.marks[di] or dep.kind != .required) continue;
                const t = g.literalBytes(dep.trigger);
                if (d.seen(t)) continue;
                for (dep.names) |nl| {
                    const m = g.literalBytes(nl);
                    if (std.mem.eql(u8, m, t) or d.seen(m)) continue;
                    if (!d.base(m) and !d.marked(m)) continue;
                    for (on.deps, 0..) |dep2, dj| {
                        if (std.mem.eql(u8, g.literalBytes(dep2.trigger), t)) d.marks[dj] = true;
                    }
                    changed = true;
                    break;
                }
            }
        }
        return d;
    }

    fn deinit(d: *Doom) void {
        if (!d.oom) d.side.a.free(d.marks);
    }

    fn seen(d: *const Doom, name: []const u8) bool {
        if (d.x) |x| {
            if (std.mem.eql(u8, x, name)) return true;
        }
        return seenContainsC(d.side, d.of.seen, name);
    }

    fn emptyValue(d: *const Doom, v: grammar.NodeId) bool {
        return switch (d.g.nonempty[v]) {
            grammar.NE_NO => true,
            grammar.NE_UNKNOWN => d.loose,
            else => false,
        };
    }

    /// Doomed on its own (no dependency entry involved but a ban).
    fn base(d: *const Doom, name: []const u8) bool {
        for (d.on.deps) |dep| {
            if (dep.kind == .ban and std.mem.eql(u8, d.g.literalBytes(dep.trigger), name)) return true;
        }
        if (declaredIndex(d.g, d.on, name)) |idx| {
            if (!d.on.any_order and idx < d.k) return true;
            return d.emptyValue(d.on.props[idx].value);
        }
        if (d.on.names_forbidden) return true;
        if (seenCountC(d.side, d.of.seen) >= d.on.max_props) return true;
        const v = extraKeyValue(d.g, d.on, name) orelse return d.loose;
        return d.emptyValue(v);
    }

    fn marked(d: *const Doom, name: []const u8) bool {
        for (d.on.deps, 0..) |dep, di| {
            if (d.marks[di] and std.mem.eql(u8, d.g.literalBytes(dep.trigger), name)) return true;
        }
        return false;
    }

    /// Is the missing name doomed? Without memory for the marks the answer
    /// is the safe one: never doomed strictly, always loosely.
    fn doomed(d: *const Doom, name: []const u8) bool {
        if (d.oom) return d.loose;
        return d.base(name) or d.marked(name);
    }
};

/// Fate of missing names in one dispatch state (skip k, `x` dispatched
/// first): dead when doomed, undecided when doomed only loosely, alive
/// otherwise.
const DoomPair = struct {
    strict: Doom,
    loose: Doom,

    fn init(g: *const grammar.Grammar, side: *const parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, k: usize, x: ?[]const u8) DoomPair {
        return .{
            .strict = Doom.init(g, side, of, on, k, x, false),
            .loose = Doom.init(g, side, of, on, k, x, true),
        };
    }

    fn deinit(p: *DoomPair) void {
        p.strict.deinit();
        p.loose.deinit();
    }

    fn cert(p: *const DoomPair, name: []const u8) Cert {
        if (p.strict.doomed(name)) return .dead;
        if (p.loose.doomed(name)) return .undecided;
        return .alive;
    }
};

/// Fate of dispatching the undeclared key `trigger` now: dead when a ban
/// refuses it or a name its dependentRequired entries fire is doomed once
/// it is seen (Doom: the dispatch fails at parser.depsResidual, or the
/// name can never be discharged); undecided when such a name is doomed
/// only loosely or a schema-kind entry fires; alive otherwise.
fn undeclaredTriggerCert(g: *const grammar.Grammar, side: *const parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, trigger: []const u8) Cert {
    var fires = false;
    for (on.deps) |dep| {
        if (std.mem.eql(u8, g.literalBytes(dep.trigger), trigger)) fires = true;
    }
    if (!fires) return .alive;
    var fate = DoomPair.init(g, side, of, on, of.next_idx, trigger);
    defer fate.deinit();
    var unknown = false;
    for (on.deps) |dep| {
        if (!std.mem.eql(u8, g.literalBytes(dep.trigger), trigger)) continue;
        switch (dep.kind) {
            .ban => return .dead,
            .schema => {
                unknown = true;
                continue;
            },
            .required => {},
        }
        for (dep.names) |nl| {
            const name = g.literalBytes(nl);
            if (fate.strict.seen(name)) continue;
            switch (fate.cert(name)) {
                .alive => {},
                .dead => return .dead,
                .undecided => unknown = true,
            }
        }
    }
    return if (unknown) .undecided else .alive;
}

/// A declared prop at index i is a completion witness when its value may
/// be non-empty, no ban refuses its name and no missing name is doomed
/// once it is dispatched (Doom with the skip past i and its own name seen:
/// a skipped optional name before it never comes back): the names of the
/// entries fired already and of those its own name fires
/// (parser.depsResidual checks both at its dispatch), and the obligations
/// still pending after it.
fn mergeDeclaredWitness(g: *const grammar.Grammar, side: *const parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, i: usize, witness: *bool, witness_unknown: *bool) void {
    var unknown = false;
    switch (g.nonempty[on.props[i].value]) {
        grammar.NE_NO => return,
        grammar.NE_UNKNOWN => unknown = true,
        else => {},
    }
    if (on.deps.len != 0) {
        const kl = g.literalBytes(on.props[i].key);
        const own = kl[1 .. kl.len - 2];
        var fate = DoomPair.init(g, side, of, on, i + 1, own);
        defer fate.deinit();
        for (on.deps) |dep| {
            const trigger = g.literalBytes(dep.trigger);
            const by_own = std.mem.eql(u8, trigger, own);
            if (dep.kind == .ban and by_own) return;
            if (dep.kind != .required) continue;
            if (!by_own and !seenContainsC(side, of.seen, trigger)) continue;
            for (dep.names) |nl| {
                const name = g.literalBytes(nl);
                if (fate.strict.seen(name)) continue;
                switch (fate.cert(name)) {
                    .alive => {},
                    .dead => return,
                    .undecided => unknown = true,
                }
            }
        }
        // The obligations still pending after the dispatch: a required prop
        // or a missing extra_required name that the dispatch dooms (a
        // dependency name it skips) can never be discharged.
        const from = if (on.any_order) 0 else i + 1;
        for (on.props[from..], from..) |p, j| {
            if (j == i or !p.required or !propPending(g, side, of, on, j)) continue;
            const kj = g.literalBytes(p.key);
            switch (fate.cert(kj[1 .. kj.len - 2])) {
                .alive => {},
                .dead => return,
                .undecided => unknown = true,
            }
        }
        for (on.extra_required) |lit| {
            const name = g.literalBytes(lit);
            if (fate.strict.seen(name)) continue;
            switch (fate.cert(name)) {
                .alive => {},
                .dead => return,
                .undecided => unknown = true,
            }
        }
    }
    if (unknown) witness_unknown.* = true else witness.* = true;
}

/// A missing extra_required name (an undeclared key) with value node v as
/// a dispatch witness: its value schema may be non-empty and dispatching
/// it does not lead nowhere (undeclaredTriggerCert).
fn mergeExtraWitness(g: *const grammar.Grammar, side: *const parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, name: []const u8, v: grammar.NodeId, witness: *bool, witness_unknown: *bool) void {
    if (g.nonempty[v] == grammar.NE_NO) return;
    switch (undeclaredTriggerCert(g, side, of, on, name)) {
        .dead => {},
        .undecided => witness_unknown.* = true,
        .alive => mergePath(g.nonempty[v], witness, witness_unknown),
    }
}

/// Fate of a pending obligation name (a required declared prop, a missing
/// extra_required name) in the current state, `fate` built for it (skip
/// next_idx, nothing dispatched first): a doomed name can never be
/// dispatched, so the object can never close. Only nodes with dependency
/// entries add anything to the value checks of the callers.
fn obligationCert(on: *const grammar.OpenObjNode, fate: *const DoomPair, name: []const u8) Cert {
    if (on.deps.len == 0) return .alive;
    return fate.cert(name);
}

/// Unseen names of fired dependentRequired entries: a completion has to
/// dispatch them before the object closes. `outstanding` marks at least
/// one unseen name, `dead` a doomed one, `unknown` one doomed only
/// loosely (`fate`: skip next_idx, nothing dispatched first).
const DepObligations = struct {
    outstanding: bool = false,
    unknown: bool = false,
    dead: bool = false,
};

fn depObligations(g: *const grammar.Grammar, side: *const parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, fate: *const DoomPair) DepObligations {
    var res = DepObligations{};
    for (on.deps) |dep| {
        if (dep.kind != .required) continue;
        if (!seenContainsC(side, of.seen, g.literalBytes(dep.trigger))) continue;
        for (dep.names) |nl| {
            const name = g.literalBytes(nl);
            if (seenContainsC(side, of.seen, name)) continue;
            res.outstanding = true;
            switch (fate.cert(name)) {
                .alive => {},
                .dead => {
                    res.dead = true;
                    return res;
                },
                .undecided => res.unknown = true,
            }
        }
    }
    return res;
}

/// The obligation half of a mid-key residual: the pending required
/// declared props, the missing extra_required names and the names of the
/// fired dependentRequired entries. An empty value schema or a doomed name
/// (obligationCert, depObligations) can never be discharged - whatever the
/// typed key turns out to be - so the frame is dead; a maybe-empty one
/// blocks an alive verdict.
const KeyObligations = struct { dead: bool = false, unknown: bool = false };

fn keyObligations(g: *const grammar.Grammar, side: *const parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, fate: *const DoomPair) KeyObligations {
    var res: KeyObligations = .{};
    for (on.props[of.next_idx..], of.next_idx..) |p, pi| {
        if (!p.required or !propPending(g, side, of, on, pi)) continue;
        switch (g.nonempty[p.value]) {
            grammar.NE_NO => return .{ .dead = true },
            grammar.NE_UNKNOWN => res.unknown = true,
            else => {},
        }
        const kl = g.literalBytes(p.key);
        switch (obligationCert(on, fate, kl[1 .. kl.len - 2])) {
            .dead => return .{ .dead = true },
            .undecided => res.unknown = true,
            .alive => {},
        }
    }
    for (on.extra_required) |lit| {
        const name = g.literalBytes(lit);
        if (seenContainsC(side, of.seen, name)) continue;
        const v = extraKeyValue(g, on, name) orelse {
            res.unknown = true;
            continue;
        };
        switch (g.nonempty[v]) {
            grammar.NE_NO => return .{ .dead = true },
            grammar.NE_UNKNOWN => res.unknown = true,
            else => {},
        }
        switch (obligationCert(on, fate, name)) {
            .dead => return .{ .dead = true },
            .undecided => res.unknown = true,
            .alive => {},
        }
    }
    const deps = depObligations(g, side, of, on, fate);
    if (deps.dead) return .{ .dead = true };
    if (deps.unknown) res.unknown = true;
    return res;
}

/// Index of the declared property spelled exactly `name`, null when no
/// declared property has that spelling.
fn declaredIndex(g: *const grammar.Grammar, on: *const grammar.OpenObjNode, name: []const u8) ?usize {
    for (on.props, 0..) |p, i| {
        const kl = g.literalBytes(p.key);
        if (std.mem.eql(u8, kl[1 .. kl.len - 2], name)) return i;
    }
    return null;
}

/// Value node of the declared property spelled exactly `name` (raw key
/// bytes), null when no declared property has that spelling.
fn declaredPropValue(g: *const grammar.Grammar, on: *const grammar.OpenObjNode, name: []const u8) ?grammar.NodeId {
    const i = declaredIndex(g, on, name) orelse return null;
    return on.props[i].value;
}

/// Open-object residual. The frame is certified only for nodes without the
/// features this rule does not analyze (propertyNames, schema-kind
/// dependencies, key/count bounds, capture); those defer to the search.
/// dependentRequired entries are analyzed (depObligations). The key
/// constraint of a patternProperties entry (pattern_dfa/pattern_lit) lands
/// only at dispatch (parser.openObjDispatch), so the search must never
/// enumerate key spellings: the certifier reasons about the dispatch
/// outcomes directly (certOpenObjKeyStr for a mid-key frame).
///
/// For a frame not inside a key the residual obligation is exact: a live
/// frame never skipped a required declared prop (dispatch kills such
/// threads, parser openObjDispatch), so what remains is props[next_idx..]
/// required values (on an order-free node: the required props not yet in
/// the seen set), the extra_required names not yet in the seen set, and
/// the unseen names of every fired dependentRequired entry;
/// a comma makes one more key mandatory even with no other obligation. An
/// outstanding obligation needs a dispatch witness: a remaining declared
/// prop inside the dispatch window (up to and including the first required
/// one), a missing extra_required name, or a fresh undeclared key. An
/// obligation with no witness is dead; each witness needs a non-empty
/// value schema (the certified bitset).
fn certOpenObj(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame) Cert {
    const on = &g.node(of.node).open_obj;
    // The count bounds hold whatever else the node constrains, so their
    // dead rules apply to every counted node before the routes below.
    const counted = on.track_keys and (on.min_props != 0 or on.max_props != grammar.UNBOUNDED);
    if (counted and certOpenObjCount(g, side, of, on) == .dead) return .dead;
    var deps_required_only = true;
    for (on.deps) |dep| {
        if (dep.kind != .required) deps_required_only = false;
    }
    // dependentRequired on an otherwise gate-free node is certified here,
    // with or without count bounds; schema-kind deps (capture) and the
    // remaining gate features still defer to the search.
    const dep_route = on.deps.len != 0 and deps_required_only and !on.capture and
        !on.names_forbidden and on.prop_names == null and
        on.key_min_len == 0 and on.key_max_len == grammar.UNBOUNDED;
    if (!dep_route and (on.names_forbidden or on.track_keys or on.capture or on.deps.len != 0 or
        on.prop_names != null or on.key_min_len != 0 or on.key_max_len != grammar.UNBOUNDED or
        on.min_props != 0 or on.max_props != grammar.UNBOUNDED))
    {
        note(.openobj_gate);
        if (on.names_forbidden or on.track_keys or on.capture) note(.openobj_track);
        if (on.deps.len != 0) note(.openobj_deps);
        if (on.prop_names != null) note(.openobj_prop_names);
        if (on.key_min_len != 0 or on.key_max_len != grammar.UNBOUNDED) note(.openobj_key_bounds);
        if (on.min_props != 0) note(.openobj_min_props);
        if (on.max_props != grammar.UNBOUNDED) note(.openobj_max_props);
        // The gates only add constraints (dispatch takes no key they do not
        // allow), so a key no completion can dispatch is dead under them.
        if (of.phase == .key_str and g.nonempty.len != 0 and certOpenObjKeyStr(g, side, of, on) == .dead) return .dead;
        if (on.names_forbidden and (of.phase == .key_str or of.phase == .key_after_comma)) return .dead;
        if (of.phase == .key_str) {
            if (on.prop_names) |pn| {
                if (!keyPrefixNameable(g, side, pn, side.chunkAt(of.key).payload.items)) return .dead;
            }
        }
        // Pure count-bounded nodes (min/maxProperties only): the count-free
        // analyses still prove dead exactly; their alive verdict ignores
        // the count bounds and holds under them only with a completion of
        // a fitting key count (countAlive), else the simulation
        // certificate decides.
        if (!on.names_forbidden and !on.capture and on.deps.len == 0 and
            on.prop_names == null and on.key_min_len == 0 and
            on.key_max_len == grammar.UNBOUNDED and on.track_keys)
        {
            return switch (certOpenObjInner(g, side, of, on)) {
                .dead => .dead,
                .alive => if (countAlive(g, side, of, on)) .alive else .undecided,
                .undecided => .undecided,
            };
        }
        return .undecided;
    }
    const c = certOpenObjInner(g, side, of, on);
    if (counted and c == .alive and !countAlive(g, side, of, on)) {
        if (on.min_props != 0) note(.openobj_min_props);
        if (on.max_props != grammar.UNBOUNDED) note(.openobj_max_props);
        return .undecided;
    }
    // The colon-pending check and the closing witness scan note themselves
    // inside; here only the mid-key phases need classifying.
    if (c == .undecided and of.phase == .key_str) note(if (of.u.key.st != .normal or of.u.key.rem != 0) UndReason.openobj_keyesc else UndReason.openobj_keystr);
    return c;
}

/// Some completion of the raw key prefix kb may pass propertyNames: its
/// string parser accepts the quoted prefix (unknown counts as yes).
fn keyPrefixNameable(g: *const grammar.Grammar, side: *parser.Side, pn: grammar.NodeId, kb: []const u8) bool {
    const buf = side.a.alloc(u8, kb.len + 1) catch {
        cert_oom = true;
        return true;
    };
    defer side.a.free(buf);
    buf[0] = '"';
    @memcpy(buf[1..], kb);
    return parser.nodeAcceptsPrefix(g, side, pn, buf) catch true;
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

/// Upper bound of the owed-name set (owedNames); a larger set gives no
/// count verdict.
const MAX_OWED: usize = 64;

fn owedHas(owed: []const []const u8, name: []const u8) bool {
    for (owed) |x| {
        if (std.mem.eql(u8, x, name)) return true;
    }
    return false;
}

/// Names a completion of the frame still has to dispatch: the pending
/// required declared props, the missing extra_required names and the
/// unseen names of the fired dependentRequired entries, closed under the
/// entries an owed name fires once it is dispatched (parser.depsResidual,
/// openObjClose). All are unseen raw spellings, pairwise distinct, so each
/// takes one more key: the set size is a lower bound on the keys still to
/// come. Null when the set outgrows the buffer.
fn owedNames(g: *const grammar.Grammar, side: *const parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, buf: *[MAX_OWED][]const u8) ?usize {
    var n: usize = 0;
    for (on.props, 0..) |p, i| {
        if (!p.required or !propPending(g, side, of, on, i)) continue;
        const kl = g.literalBytes(p.key);
        const name = kl[1 .. kl.len - 2];
        if (owedHas(buf[0..n], name)) continue;
        if (n == MAX_OWED) return null;
        buf[n] = name;
        n += 1;
    }
    for (on.extra_required) |lit| {
        const name = g.literalBytes(lit);
        if (seenContainsC(side, of.seen, name) or owedHas(buf[0..n], name)) continue;
        if (n == MAX_OWED) return null;
        buf[n] = name;
        n += 1;
    }
    var changed = true;
    while (changed) {
        changed = false;
        for (on.deps) |dep| {
            if (dep.kind != .required) continue;
            const t = g.literalBytes(dep.trigger);
            if (!seenContainsC(side, of.seen, t) and !owedHas(buf[0..n], t)) continue;
            for (dep.names) |nl| {
                const name = g.literalBytes(nl);
                if (seenContainsC(side, of.seen, name) or owedHas(buf[0..n], name)) continue;
                if (n == MAX_OWED) return null;
                buf[n] = name;
                n += 1;
                changed = true;
            }
        }
    }
    return n;
}

/// Can the in-flight key (phase key_str) still complete to the owed name
/// `name` and be dispatched as it: the raw spelling extends the typed
/// prefix (mid-character: continues into the pending code point range),
/// and the dispatch passes - a declared prop not skipped yet whose
/// dispatch skips no pending required prop, or an undeclared name the
/// node may take - with a possibly non-empty value and no doomed name
/// after it (mergeDeclaredWitness, mergeExtraWitness). What the analysis
/// cannot settle counts as possible.
fn owedKeyPossible(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, name: []const u8) bool {
    const kb = side.chunkAt(of.key).payload.items;
    if (of.u.key.st != .normal) return true;
    if (of.u.key.rem != 0) {
        const pc = pendingKeyCp(kb, of.u.key) orelse return true;
        if (!nameContinuesInto(name, pc)) return false;
    } else if (!std.mem.startsWith(u8, name, kb)) return false;
    if (g.nonempty.len == 0) return true;
    var witness = false;
    var witness_unknown = false;
    if (declaredIndex(g, on, name)) |i| {
        if (!propPending(g, side, of, on, i)) return false;
        if (!on.any_order) {
            for (on.props[of.next_idx..i]) |p| {
                if (p.required) return false;
            }
        }
        mergeDeclaredWitness(g, side, of, on, i, &witness, &witness_unknown);
    } else {
        if (on.names_forbidden) return false;
        const v = extraKeyValue(g, on, name) orelse return true;
        mergeExtraWitness(g, side, of, on, name, v, &witness, &witness_unknown);
    }
    return witness or witness_unknown;
}

/// The in-flight key (phase key_str, clean key state) completes to the
/// owed name `name` for certain: its raw spelling extends the typed
/// prefix, the dispatch passes (a declared prop not skipped yet whose
/// dispatch skips no pending required prop, or an undeclared name) and
/// the value is non-empty with no doomed name after it - the certain half
/// of owedKeyPossible.
fn owedKeyCertain(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, name: []const u8) bool {
    if (of.u.key.st != .normal or of.u.key.rem != 0 or g.nonempty.len == 0) return false;
    if (!std.mem.startsWith(u8, name, side.chunkAt(of.key).payload.items)) return false;
    var witness = false;
    var witness_unknown = false;
    if (declaredIndex(g, on, name)) |i| {
        if (!propPending(g, side, of, on, i)) return false;
        if (!on.any_order) {
            for (on.props[of.next_idx..i]) |p| {
                if (p.required) return false;
            }
        }
        mergeDeclaredWitness(g, side, of, on, i, &witness, &witness_unknown);
    } else {
        if (on.names_forbidden) return false;
        const v = extraKeyValue(g, on, name) orelse return false;
        mergeExtraWitness(g, side, of, on, name, v, &witness, &witness_unknown);
    }
    return witness;
}

/// Alive under the count bounds, for a frame (dependentRequired or pure
/// count route) whose count-free analysis is alive: that analysis proved
/// every owed name dischargeable, and here one completion of a fitting
/// key count is exhibited. With n keys seen and m owed names
/// (owedNames), it dispatches exactly the owed names - declared ones in
/// schema order - plus one fresh undeclared key where a key is mandatory
/// and no owed name takes the place (after a comma with nothing owed, or
/// an in-flight key that spells no owed name for certain, owedKeyCertain),
/// plus fresh keys up to min_props. A fresh key fires nothing and is
/// always available, with any typed prefix, only on a node whose
/// undeclared keys all take a non-empty fallback value (no key pattern);
/// otherwise such a completion is not claimed. The total must lie within
/// [min_props, max_props]; anything else stays undecided.
fn countAlive(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode) bool {
    if (g.nonempty.len == 0) return false;
    var buf: [MAX_OWED][]const u8 = undefined;
    const m = owedNames(g, side, of, on, &buf) orelse return false;
    const fresh = on.pattern_lit == null and on.pattern_dfa == null and !on.names_forbidden and
        on.prop_names == null and on.key_min_len == 0 and on.key_max_len == grammar.UNBOUNDED and
        g.nonempty[on.value] == grammar.NE_YES;
    var extra: u64 = 0;
    switch (of.phase) {
        .key_str => {
            for (buf[0..m]) |name| {
                if (owedKeyCertain(g, side, of, on, name)) break;
            } else {
                if (!fresh) return false;
                extra = 1;
            }
        },
        .key_after_comma => if (m == 0) {
            if (!fresh) return false;
            extra = 1;
        },
        else => {},
    }
    const total = @as(u64, seenCountC(side, of.seen)) + m + extra;
    if (total > on.max_props) return false;
    if (total < on.min_props and (!fresh or on.min_props > on.max_props)) return false;
    return true;
}

/// Count bounds (min/maxProperties; node.track_keys, so the seen set holds
/// every dispatched key and seenCountC is the property count). Only dead
/// verdicts are derived, each exact whatever else the node constrains
/// (the other features only refuse more keys); aliveness goes to the
/// simulation certificate, which pads min_props and caps max_props
/// (synth.padMinProps). With n keys seen and `owed` the names a completion
/// still has to dispatch (owedNames):
///
/// 1. A pending (key_str) or comma-mandatory (key_after_comma) key with
///    n == max_props can never dispatch (parser.openObjDispatch), and a
///    comma cannot be closed over: dead.
/// 2. Every owed name is one more distinct key: n + |owed| > max_props is
///    dead.
/// 3. The in-flight key (key_str) is one more key unless it completes to an
///    owed name. With n + 1 + |owed| > max_props it has to, so when no
///    owed name extends the typed prefix and can be dispatched now
///    (owedKeyPossible) the frame is dead. (Dispatched, an owed name
///    leaves n + |owed| keys: the set is closed under the entries it
///    fires.)
/// 4. With no fresh undeclared dispatch (freshUndeclaredPath == .no, never
///    .unknown) the achievable key count is bounded by n + remaining
///    declared props + missing extra_required names + an in-flight key;
///    below min_props: dead.
fn certOpenObjCount(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode) Cert {
    const n = seenCountC(side, of.seen);
    const in_key = of.phase == .key_str;
    if ((of.phase == .key_after_comma or in_key) and n >= on.max_props) return .dead;
    if (on.max_props != grammar.UNBOUNDED) {
        var buf: [MAX_OWED][]const u8 = undefined;
        if (owedNames(g, side, of, on, &buf)) |m| {
            if (@as(u64, n) + m > on.max_props) return .dead;
            if (in_key and @as(u64, n) + 1 + m > on.max_props) {
                for (buf[0..m]) |name| {
                    if (owedKeyPossible(g, side, of, on, name)) break;
                } else return .dead;
            }
        }
    }
    // Rule 4. Missing extra_required names, deduplicated against each
    // other (a shared name is one key, not two).
    if (on.min_props != 0 and freshUndeclaredPath(g, side, of, on) == .no) {
        var extra_missing: u64 = 0;
        for (on.extra_required, 0..) |lit, j| {
            const name = g.literalBytes(lit);
            if (seenContainsC(side, of.seen, name)) continue;
            for (on.extra_required[0..j]) |lit2| {
                if (std.mem.eql(u8, g.literalBytes(lit2), name)) break;
            } else extra_missing += 1;
        }
        var pending: u64 = 0;
        for (of.next_idx..on.props.len) |pi| {
            if (propPending(g, side, of, on, pi)) pending += 1;
        }
        const inflight: u64 = if (in_key) 1 else 0;
        if (@as(u64, n) + pending + extra_missing + inflight < on.min_props) return .dead;
    }
    return .undecided;
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
    // Doom scan: an outstanding obligation whose value schema is empty, or
    // whose dispatch a dependency dooms (obligationCert), can never be
    // discharged, so no completion exists.
    var fate = DoomPair.init(g, side, of, on, of.next_idx, null);
    defer fate.deinit();
    var unknown = false;
    var has_required = false;
    for (on.props[of.next_idx..], of.next_idx..) |p, pi| {
        if (!p.required or !propPending(g, side, of, on, pi)) continue;
        has_required = true;
        switch (g.nonempty[p.value]) {
            grammar.NE_NO => return .dead,
            grammar.NE_UNKNOWN => unknown = true,
            else => {},
        }
        const kl = g.literalBytes(p.key);
        switch (obligationCert(on, &fate, kl[1 .. kl.len - 2])) {
            .dead => return .dead,
            .undecided => unknown = true,
            .alive => {},
        }
    }
    var missing_extra = false;
    var witness = false;
    var witness_unknown = false;
    for (on.extra_required) |lit| {
        const name = g.literalBytes(lit);
        if (seenContainsC(side, of.seen, name)) continue;
        missing_extra = true;
        // The name is an obligation (an empty or maybe-empty value blocks
        // dead resp. alive) AND a dispatch candidate.
        const v = extraKeyValue(g, on, name) orelse {
            unknown = true;
            witness_unknown = true;
            continue;
        };
        switch (g.nonempty[v]) {
            grammar.NE_NO => return .dead,
            grammar.NE_UNKNOWN => unknown = true,
            else => {},
        }
        switch (obligationCert(on, &fate, name)) {
            .dead => return .dead,
            .undecided => unknown = true,
            .alive => {},
        }
        mergeExtraWitness(g, side, of, on, name, v, &witness, &witness_unknown);
    }
    const deps = depObligations(g, side, of, on, &fate);
    if (deps.dead) return .dead;
    if (deps.outstanding) missing_extra = true;
    if (deps.unknown) unknown = true;
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
        if (!propPending(g, side, of, on, i)) continue;
        mergeDeclaredWitness(g, side, of, on, i, &witness, &witness_unknown);
        if (windowEnds(on, p)) break;
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
    var fate = DoomPair.init(g, side, of, on, of.next_idx, null);
    defer fate.deinit();
    const obl = keyObligations(g, side, of, on, &fate);
    if (obl.dead) return .dead;
    const unknown = obl.unknown;
    var witness = false;
    var witness_unknown = false;
    // Declared names extending the typed prefix, in dispatchable order.
    var i: usize = of.next_idx;
    while (i < on.props.len) : (i += 1) {
        const p = on.props[i];
        if (!propPending(g, side, of, on, i)) continue;
        const kl = g.literalBytes(p.key);
        if (std.mem.startsWith(u8, kl[1 .. kl.len - 2], kb)) {
            mergeDeclaredWitness(g, side, of, on, i, &witness, &witness_unknown);
        }
        if (windowEnds(on, p)) break;
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
        mergeExtraWitness(g, side, of, on, name, v, &witness, &witness_unknown);
    }
    // Undeclared completions.
    if (on.pattern_dfa) |pd| {
        const q = dfaFeedKeyBytes(pd, kb) orelse return .undecided;
        if (g.pat_len[of.node]) |descr| {
            // Both dispatch sides are counted against the taken keys:
            // either may be finite - a finite pattern language
            // (`^line[1-3]$`) runs out once its keys are seen (dispatch
            // refuses a duplicate), and so may the non-matching remnant of
            // a near-universal pattern.
            if (g.nonempty[on.pattern_value] != grammar.NE_NO) {
                mergeKeyPath(freshUndeclaredKey(g, side, of, on, pd, descr, kb, q, null, .hit), g.nonempty[on.pattern_value], &witness, &witness_unknown);
            }
            if (g.nonempty[on.value] != grammar.NE_NO) {
                mergeKeyPath(freshUndeclaredKey(g, side, of, on, pd, descr, kb, q, null, .miss), g.nonempty[on.value], &witness, &witness_unknown);
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
    if (of.u.key.st == .normal) {
        if (pendingKeyCp(kb, of.u.key)) |pc| return certOpenObjKeyMidCp(g, side, of, on, pc);
    }
    if (on.pattern_lit != null or on.pattern_dfa != null) return .undecided;
    var i: usize = of.next_idx;
    while (i < on.props.len) : (i += 1) {
        const p = on.props[i];
        if (!propPending(g, side, of, on, i)) continue;
        const kl = g.literalBytes(p.key);
        if (std.mem.startsWith(u8, kl[1 .. kl.len - 2], kb)) return .undecided;
        if (windowEnds(on, p)) break;
    }
    for (on.extra_required) |lit| {
        const name = g.literalBytes(lit);
        if (seenContainsC(side, of.seen, name)) continue;
        if (std.mem.startsWith(u8, name, kb)) return .undecided;
    }
    if (g.nonempty[on.value] != grammar.NE_NO) return .undecided;
    return .dead;
}
/// A raw key cut inside a UTF-8 sequence: the bytes before the sequence and
/// the code points its pending bytes can still complete to (the first
/// pending byte within [k.lo, k.hi], the others free continuation bytes).
const PendingCp = struct { prefix: []const u8, lo: u32, hi: u32 };

fn pendingKeyCp(kb: []const u8, k: parser.OpenObjKeyState) ?PendingCp {
    if (k.rem == 0) return null;
    var j = kb.len;
    while (j > 0 and kb.len - j < 4) {
        j -= 1;
        if (kb[j] >= 0xC0) break;
    }
    if (j == kb.len or kb[j] < 0xC0) return null;
    const lead = kb[j];
    const total: usize = if (lead < 0xE0) 2 else if (lead < 0xF0) 3 else 4;
    if (kb.len - j + k.rem != total) return null;
    var bits: u32 = if (lead < 0xE0) lead & 0x1F else if (lead < 0xF0) lead & 0x0F else lead & 0x07;
    for (kb[j + 1 ..]) |c| bits = (bits << 6) | (c & 0x3F);
    const shift: u5 = @intCast(6 * @as(u32, k.rem - 1));
    const base = bits << @intCast(6 * @as(u32, k.rem));
    return .{
        .prefix = kb[0..j],
        .lo = base | (@as(u32, k.lo & 0x3F) << shift),
        .hi = base | (@as(u32, k.hi & 0x3F) << shift) | ((@as(u32, 1) << shift) - 1),
    };
}

/// Raw name spelling continues the complete prefix with a code point in
/// the pending range.
fn nameContinuesInto(name: []const u8, pc: PendingCp) bool {
    if (!std.mem.startsWith(u8, name, pc.prefix) or name.len == pc.prefix.len) return false;
    const rest = name[pc.prefix.len..];
    const n = std.unicode.utf8ByteSequenceLength(rest[0]) catch return false;
    if (rest.len < n) return false;
    const cp = std.unicode.utf8Decode(rest[0..n]) catch return false;
    return cp >= pc.lo and cp <= pc.hi;
}

/// Mid-codepoint mid-key residual: certOpenObjKeyStr with the next code
/// point ranging over the pending range. The key pattern DFA answers per
/// transition range from the state after the complete prefix; a code
/// point no live transition covers never matches.
fn certOpenObjKeyMidCp(g: *const grammar.Grammar, side: *parser.Side, of: *const parser.OpenObjFrame, on: *const grammar.OpenObjNode, pc: PendingCp) Cert {
    if (on.pattern_lit != null) return .undecided;
    var fate = DoomPair.init(g, side, of, on, of.next_idx, null);
    defer fate.deinit();
    const obl = keyObligations(g, side, of, on, &fate);
    if (obl.dead) return .dead;
    const unknown = obl.unknown;
    var witness = false;
    var witness_unknown = false;
    var i: usize = of.next_idx;
    while (i < on.props.len) : (i += 1) {
        const p = on.props[i];
        if (!propPending(g, side, of, on, i)) continue;
        const kl = g.literalBytes(p.key);
        if (nameContinuesInto(kl[1 .. kl.len - 2], pc)) mergeDeclaredWitness(g, side, of, on, i, &witness, &witness_unknown);
        if (windowEnds(on, p)) break;
    }
    for (on.extra_required) |lit| {
        const name = g.literalBytes(lit);
        if (seenContainsC(side, of.seen, name)) continue;
        if (!nameContinuesInto(name, pc)) continue;
        const v = extraKeyValue(g, on, name) orelse {
            witness_unknown = true;
            continue;
        };
        mergeExtraWitness(g, side, of, on, name, v, &witness, &witness_unknown);
    }
    if (on.pattern_dfa) |pd| {
        const q = dfaFeedKeyBytes(pd, pc.prefix) orelse return .undecided;
        const descr = g.pat_len[of.node] orelse return .undecided;
        // Both dispatch sides counted against the taken keys, over the
        // completions whose next code point lies in the pending range.
        const window: CpWindow = .{ .lo = pc.lo, .hi = pc.hi };
        if (g.nonempty[on.pattern_value] != grammar.NE_NO) {
            mergeKeyPath(freshUndeclaredKey(g, side, of, on, pd, descr, pc.prefix, q, window, .hit), g.nonempty[on.pattern_value], &witness, &witness_unknown);
        }
        if (g.nonempty[on.value] != grammar.NE_NO) {
            mergeKeyPath(freshUndeclaredKey(g, side, of, on, pd, descr, pc.prefix, q, window, .miss), g.nonempty[on.value], &witness, &witness_unknown);
        }
    } else {
        mergePath(g.nonempty[on.value], &witness, &witness_unknown);
    }
    if (witness and !unknown) return .alive;
    if (!witness and !witness_unknown) return .dead;
    return .undecided;
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
/// single value D x 10^(t - d + 1). cmp 1 is below the bound, 2 above; cmp 0
/// is equal unless D's digits are a proper prefix of the bound's, whose
/// canonical tail then ends in a nonzero digit, so D is below.
fn rangePtOrder(nf: *const parser.NumRangeFrame, b: MagBound) std.math.Order {
    return switch (magCmp(nf, b)) {
        1 => .lt,
        2 => .gt,
        else => if (nf.j[b.slot] < b.dlen) .lt else .eq,
    };
}

fn rangePtLo(nf: *const parser.NumRangeFrame, b: MagBound, t: i64) bool {
    if (t > b.om) return true;
    if (t < b.om) return false;
    return switch (rangePtOrder(nf, b)) {
        .gt => true,
        .eq => !b.excl,
        .lt => false,
    };
}

fn rangePtHi(nf: *const parser.NumRangeFrame, b: MagBound, t: i64) bool {
    if (t < b.om) return true;
    if (t > b.om) return false;
    return switch (rangePtOrder(nf, b)) {
        .lt => true,
        .eq => !b.excl,
        .gt => false,
    };
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
        .escape, .u0, .u00, .uhex1, .uhex2 => {
            // Mid escape: the canonical escape table fixes the codepoints
            // the escape can still decode to (\" \\ and U+0000..U+001F,
            // whose \u00XX forms exclude the five with a short escape).
            const descr = g.pat_len[sf.node] orelse return .undecided;
            const count1 = sf.count + 1; // a character starts only below max_len
            const lo = sp.min_len -| count1;
            const hi: ?u32 = if (sp.max_len == grammar.UNBOUNDED) null else sp.max_len - count1;
            const R = struct { lo: u32, hi: u32 };
            const ranges: []const R = switch (sf.state) {
                .escape => &.{ .{ .lo = 0x00, .hi = 0x1F }, .{ .lo = 0x22, .hi = 0x22 }, .{ .lo = 0x5C, .hi = 0x5C } },
                .uhex2 => if (sf.lo == 0)
                    &.{ .{ .lo = 0x00, .hi = 0x07 }, .{ .lo = 0x0B, .hi = 0x0B }, .{ .lo = 0x0E, .hi = 0x0F } }
                else
                    &.{.{ .lo = 0x10, .hi = 0x1F }},
                else => &.{ .{ .lo = 0x00, .hi = 0x07 }, .{ .lo = 0x0B, .hi = 0x0B }, .{ .lo = 0x0E, .hi = 0x1F } },
            };
            for (ranges) |r| {
                for (sp.dfa.states[sf.dfa].trans) |t| {
                    if (t.hi < r.lo or t.lo > r.hi) continue;
                    if (sp.dfa.dead(t.to)) continue;
                    if (descr.reachableIn(t.to, lo, hi)) return .alive;
                }
            }
            return .dead;
        },
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
        // Mid escape the character in flight is counted at its end; with
        // room for one more character the spelling can still leave the trie.
        else => return if (sf.count + 1 < se.max_len) .alive else .undecided,
    }
}

const testing = std.testing;

fn litGrammar(a: std.mem.Allocator, s: []const u8) !grammar.Grammar {
    var arena = Arena.init(a);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addLiteralNode(s);
    return b.finish(arena, .literal_set, root, .{});
}

fn objGrammar(a: std.mem.Allocator) !grammar.Grammar {
    var arena = Arena.init(a);
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

fn specGrammar(schema_json: []const u8) !grammar.Grammar {
    const schema_mod = @import("schema.zig");
    var diag: schema_mod.Diagnostic = .{};
    var w = work_mod.Work{};
    return schema_mod.compile(testing.allocator, schema_json, 64, &diag, &w, false, .spec_v1, &.{});
}

test "complete: certificate cache hits, resets per fill, falls back when full" {
    const alloc = testing.allocator;
    var g = try specGrammar("{\"type\":\"string\",\"pattern\":\"^ab*$\"}");
    defer g.deinit();
    try testing.expect(g.needs_reachability);
    var tk = try makeTok(alloc, 4, &.{ "\"a", "b", "\"", "" }, &.{3}, &.{});
    defer tk.deinit();
    var cache = Cache.init(alloc);
    defer cache.deinit();
    var w = work_mod.Work{};
    var side = parser.Side{ .a = undefined };
    // The string-content state after the opening quote certifies alive.
    var st0 = try parser.initState(&g, 4, &side);
    var st: parser.State = undefined;
    var work: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st0, "\"", &st, &work);
    const v1 = try stateAlive(&g, &tk, &st, &cache, &w, &side);
    try testing.expect(v1);
    const hits1 = cache.cert_hits;
    try testing.expectEqual(v1, try stateAlive(&g, &tk, &st, &cache, &w, &side));
    try testing.expect(cache.cert_hits > hits1); // second call served by the cache
    // reset() clears the per-fill cache: the next call certifies again.
    cache.reset();
    const hits2 = cache.cert_hits;
    _ = try stateAlive(&g, &tk, &st, &cache, &w, &side);
    try testing.expectEqual(hits2, cache.cert_hits);
    // A full cache is not an error: certification continues normally.
    cache.cert_cap = 0;
    cache.reset();
    try testing.expectEqual(v1, try stateAlive(&g, &tk, &st, &cache, &w, &side));
    // A grammar change after reset must not answer from the old map.
    cache.cert_cap = 1 << 12;
    cache.reset();
    var g2 = try specGrammar("{\"type\":\"string\",\"pattern\":\"^xy*$\"}");
    defer g2.deinit();
    try testing.expect(g2.needs_reachability);
    var st0b = try parser.initState(&g2, 4, &side);
    var stb: parser.State = undefined;
    try parser.feedBytes(&g2, &side, &st0b, "\"", &stb, &work);
    const hits3 = cache.cert_hits;
    try testing.expect(try stateAlive(&g2, &tk, &stb, &cache, &w, &side));
    try testing.expectEqual(hits3, cache.cert_hits); // no hit from the old grammar
}

const ReviewLeg = struct { ok: bool, failed: bool, peak: u64, hits: u64, entries: usize };

/// One fill's worth of `stateAlive` calls on a oneOf residual fixture
/// under an `Accounting` byte limit, cache off and on. The first call
/// certifies the `"c` state alive (the cache stores it), the second one is
/// undecided and needs the search scratch.
fn reviewCert(limit: u64, off: bool) !ReviewLeg {
    const alloc_mod = @import("alloc.zig");
    const alloc = testing.allocator;
    var arena = Arena.init(alloc);
    var b = grammar.Builder.init(arena.allocator());
    var lits1 = [_]grammar.Literal{ try b.addLiteral("\"ab\""), try b.addLiteral("\"c\"") };
    const n1 = try b.addNode(.{ .lit_trie = try b.buildTrie(&lits1) });
    var lits2 = [_]grammar.Literal{ try b.addLiteral("\"ab\""), try b.addLiteral("\"d\"") };
    const n2 = try b.addNode(.{ .lit_trie = try b.buildTrie(&lits2) });
    const root = try b.addNode(.{ .comb = .{ .kind = .oneof, .branches = try b.copyNodeIds(&.{ n1, n2 }) } });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var tk = try makeTok(alloc, 4, &.{ "\"ab\"", "\"c\"", "\"d\"", "" }, &.{3}, &.{});
    defer tk.deinit();
    var acc = alloc_mod.Accounting.init(alloc, limit);
    var side = parser.Side.init(acc.allocator(.session));
    defer side.deinit();
    var cache = Cache.init(acc.allocator(.temp));
    defer cache.deinit();
    var w = work_mod.Work{};
    test_overrides.cert_cache_off = off;
    defer test_overrides.cert_cache_off = false;
    var st = try parser.initState(&g, 8, &side);
    defer parser.releaseState(&side, &st);
    try testing.expectEqual(Cert.undecided, certifyState(&g, &st, &side));
    var sc: parser.State = undefined;
    var work: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st, "\"c", &sc, &work);
    defer parser.releaseState(&side, &sc);
    try testing.expect(try stateAlive(&g, &tk, &sc, &cache, &w, &side));
    const outcome = stateAlive(&g, &tk, &st, &cache, &w, &side);
    if (outcome) |ok| {
        // The fill needed the search, so the cache must be empty for the
        // rest of it: re-certifying the alive state stores nothing.
        try testing.expect(try stateAlive(&g, &tk, &sc, &cache, &w, &side));
        return .{ .ok = ok, .failed = false, .peak = acc.totalPeak(), .hits = cache.cert_hits, .entries = cache.cert_map.count() };
    } else |_| {
        return .{ .ok = false, .failed = true, .peak = acc.totalPeak(), .hits = cache.cert_hits, .entries = cache.cert_map.count() };
    }
}

test "complete: certificate cache never takes memory from the search" {
    const unlimited_off = try reviewCert(std.math.maxInt(u64), true);
    const unlimited_on = try reviewCert(std.math.maxInt(u64), false);
    try testing.expect(unlimited_off.ok and !unlimited_off.failed);
    try testing.expect(unlimited_on.ok and !unlimited_on.failed);
    // The limit is the cache-off leg's own peak: the cache-on leg must
    // settle the same way, because the optional cache memory is given back
    // before the search allocates (it used to end in ResourceLimit here).
    const limited_off = try reviewCert(unlimited_off.peak, true);
    const limited_on = try reviewCert(unlimited_off.peak, false);
    try testing.expect(limited_off.ok and !limited_off.failed);
    try testing.expect(limited_on.ok and !limited_on.failed);
    // Once the fill has searched, the cache holds nothing at all.
    try testing.expectEqual(@as(usize, 0), limited_on.entries);
    try testing.expectEqual(@as(usize, 0), unlimited_on.entries);
}

/// Mid-escape certification of a pattern string: proves the second release
/// rule, which the budget-0 branch shares through `certifyTop`. The proof
/// needs a two-state scratch; with a certificate key held at the proof's
/// own peak the scratch does not fit, so the proof degrades - and must be
/// re-proven with the cache released instead of answering `undecided`.
fn escapeCert(limit: u64, key_bytes: ?[]const u8) !struct { cert: Cert, peak: u64, muted: bool, entries: usize } {
    const alloc_mod = @import("alloc.zig");
    const alloc = testing.allocator;
    var g = try specGrammar("{\"type\":\"string\",\"pattern\":\"^[\\\\s\\\\S]*$\"}");
    defer g.deinit();
    try testing.expect(g.needs_reachability);
    var acc = alloc_mod.Accounting.init(alloc, limit);
    var side = parser.Side.init(acc.allocator(.session));
    defer side.deinit();
    var cache = Cache.init(acc.allocator(.temp));
    defer cache.deinit();
    if (key_bytes) |kb| cache.certPut(kb, .alive);
    var st0 = try parser.initState(&g, 4, &side);
    var st: parser.State = undefined;
    var work: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st0, "\"\\", &st, &work);
    defer parser.releaseState(&side, &st);
    const cert = certifyTop(&g, &st, &cache, &side);
    return .{ .cert = cert, .peak = acc.totalPeak(), .muted = cache.cert_muted, .entries = cache.cert_map.count() };
}

test "complete: a proof degraded by cache memory is re-proven without it" {
    const plain = try escapeCert(std.math.maxInt(u64), null);
    try testing.expectEqual(Cert.alive, plain.cert);
    const with_key = try escapeCert(std.math.maxInt(u64), "certificate-key");
    try testing.expectEqual(Cert.alive, with_key.cert);
    try testing.expect(with_key.peak > plain.peak); // the key really occupies room
    // At the proof's own peak the key leaves no room for the two-state
    // scratch: the degraded proof must drop the cache and answer as a
    // cache-off caller would.
    const tight = try escapeCert(plain.peak, "certificate-key");
    try testing.expectEqual(Cert.alive, tight.cert); // undecided before the fix
    try testing.expect(tight.muted); // the release happened
    try testing.expectEqual(@as(usize, 0), tight.entries);
}

const FirstLeg = struct { ok: bool, failed: bool, peak: u64, key_cap: usize, muted: bool };

/// First case: the very first `stateAlive` on a reachable mid-escape
/// state. `keyOf` grows the key buffer before the proof runs, so the map is
/// still empty when the proof degrades.
fn firstCert(limit: u64, off: bool) !FirstLeg {
    const alloc_mod = @import("alloc.zig");
    const alloc = testing.allocator;
    var g = try specGrammar("{\"type\":\"string\",\"pattern\":\"^[\\\\s\\\\S]*$\"}");
    defer g.deinit();
    var tk = try makeTok(alloc, 3, &.{ "n\"", "\"", "" }, &.{2}, &.{});
    defer tk.deinit();
    var acc = alloc_mod.Accounting.init(alloc, limit);
    var side = parser.Side.init(acc.allocator(.session));
    defer side.deinit();
    var cache = Cache.init(acc.allocator(.temp));
    defer cache.deinit();
    var st0 = try parser.initState(&g, 4, &side);
    defer parser.releaseState(&side, &st0);
    var st: parser.State = undefined;
    var work: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st0, "\"\\", &st, &work);
    defer parser.releaseState(&side, &st);
    test_overrides.cert_cache_off = off;
    defer test_overrides.cert_cache_off = false;
    var w = work_mod.Work{};
    const result = stateAlive(&g, &tk, &st, &cache, &w, &side);
    if (result) |ok| {
        return .{ .ok = ok, .failed = false, .peak = acc.totalPeak(), .key_cap = cache.key_buf.capacity, .muted = cache.cert_muted };
    } else |_| {
        return .{ .ok = false, .failed = true, .peak = acc.totalPeak(), .key_cap = cache.key_buf.capacity, .muted = cache.cert_muted };
    }
}

test "complete: the certificate key buffer does not starve the first proof" {
    const plain = try firstCert(std.math.maxInt(u64), true);
    const on = try firstCert(std.math.maxInt(u64), false);
    try testing.expect(plain.ok and !plain.failed);
    try testing.expect(on.ok and !on.failed);
    try testing.expect(on.peak > plain.peak); // keyOf really allocated
    // Same limit as the cache-off peak: the key buffer must be given back
    // with the (still empty) map when the proof asks for its scratch.
    const tight_off = try firstCert(plain.peak, true);
    const tight_on = try firstCert(plain.peak, false);
    try testing.expect(tight_off.ok and !tight_off.failed);
    try testing.expect(tight_on.ok and !tight_on.failed); // ResourceLimit before the fix
    try testing.expectEqual(@as(usize, 0), tight_on.key_cap); // released
}

const ResetLeg = struct { cert: Cert, peak: u64, table_cap: usize, table_count: usize, key_cap: usize, muted: bool };

/// Second case: `certPut` then `reset` leaves an empty table behind; the
/// proof that follows must not pay for it.
fn resetProof(limit: u64, retain: bool) !ResetLeg {
    const alloc_mod = @import("alloc.zig");
    const alloc = testing.allocator;
    var g = try specGrammar("{\"type\":\"string\",\"pattern\":\"^[\\\\s\\\\S]*$\"}");
    defer g.deinit();
    var acc = alloc_mod.Accounting.init(alloc, limit);
    var side = parser.Side.init(acc.allocator(.session));
    defer side.deinit();
    var cache = Cache.init(acc.allocator(.temp));
    defer cache.deinit();
    if (retain) {
        cache.certPut("certificate-key", .alive);
        cache.reset();
    }
    var st0 = try parser.initState(&g, 4, &side);
    defer parser.releaseState(&side, &st0);
    var st: parser.State = undefined;
    var work: parser.State = undefined;
    try parser.feedBytes(&g, &side, &st0, "\"\\", &st, &work);
    defer parser.releaseState(&side, &st);
    const cert = certifyTop(&g, &st, &cache, &side);
    return .{
        .cert = cert,
        .peak = acc.totalPeak(),
        .table_cap = cache.cert_map.capacity(),
        .table_count = cache.cert_map.count(),
        .key_cap = cache.key_buf.capacity,
        .muted = cache.cert_muted,
    };
}

test "complete: reset frees the certificate table instead of keeping it" {
    const plain = try resetProof(std.math.maxInt(u64), false);
    const retained = try resetProof(std.math.maxInt(u64), true);
    try testing.expectEqual(Cert.alive, plain.cert);
    try testing.expectEqual(Cert.alive, retained.cert);
    // `reset` drops the entries but reuses the table, so the capacity is
    // still charged when the next proof runs - the release rule must see
    // it (an entry-count test would not).
    try testing.expect(retained.table_cap > 0);
    try testing.expectEqual(@as(usize, 0), retained.table_count);
    // At the plain proof's peak that capacity leaves no room for the
    // two-state scratch: the proof releases the table and re-proves.
    const tight = try resetProof(plain.peak, true);
    try testing.expectEqual(Cert.alive, tight.cert); // undecided before the fix
    try testing.expect(tight.muted); // the release happened
    try testing.expectEqual(@as(usize, 0), tight.table_cap);
    try testing.expectEqual(@as(usize, 0), tight.key_cap);
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
    var arena = Arena.init(alloc);
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
        var arena = Arena.init(alloc);
        var b = grammar.Builder.init(arena.allocator());
        const root = try b.addNode(.{ .num_range = .{ .min = one, .max = hundred } });
        gs[0] = try b.finish(arena, .json_schema, root, .{});
        built += 1;
    }
    {
        var arena = Arena.init(alloc);
        var b = grammar.Builder.init(arena.allocator());
        const root = try b.addNode(.{ .int_num = {} });
        gs[1] = try b.finish(arena, .json_schema, root, .{});
        built += 1;
    }
    {
        var arena = Arena.init(alloc);
        var b = grammar.Builder.init(arena.allocator());
        const root = try b.addNode(.{ .not_int_num = {} });
        gs[2] = try b.finish(arena, .json_schema, root, .{});
        built += 1;
    }
    {
        var arena = Arena.init(alloc);
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

fn rangeCertAfter(r: grammar.NumRange, prefix: []const u8) !Cert {
    const alloc = testing.allocator;
    var arena = Arena.init(alloc);
    var b = grammar.Builder.init(arena.allocator());
    const root = try b.addNode(.{ .num_range = r });
    var g = try b.finish(arena, .json_schema, root, .{});
    defer g.deinit();
    var side = parser.Side.init(alloc);
    defer side.deinit();
    var st = try parser.initState(&g, 4, &side);
    var bufs: [2]parser.State = undefined;
    for (&bufs) |*s| s.n = 0;
    try parser.feedBytes(&g, &side, &st, prefix, &bufs[0], &bufs[1]);
    return certifyState(&g, &bufs[0], &side);
}

test "complete r1: frozen mantissa shorter than the bound's digits" {
    const c15: grammar.NumConst = .{ .neg = false, .digits = "15", .exp10 = -1 };
    const c16: grammar.NumConst = .{ .neg = false, .digits = "16", .exp10 = -1 };
    const one: grammar.NumConst = .{ .neg = false, .digits = "1", .exp10 = 0 };
    const n15: grammar.NumConst = .{ .neg = true, .digits = "15", .exp10 = -1 };
    const n16: grammar.NumConst = .{ .neg = true, .digits = "16", .exp10 = -1 };
    const Case = struct { r: grammar.NumRange, p: []const u8, want: Cert };
    const cases = [_]Case{
        // 1e0 = 1 is below 1.5 although "1" matches the bound's first digit.
        .{ .r = .{ .min = c15, .max = c16 }, .p = "1e", .want = .dead },
        .{ .r = .{ .min = c15 }, .p = "1e-", .want = .dead },
        .{ .r = .{ .min = n16, .max = n15 }, .p = "-1e", .want = .dead },
        // 1e0 = 1 is below an exclusive 1.6, not equal to it.
        .{ .r = .{ .min = one, .max = c16, .max_excl = true }, .p = "1e", .want = .alive },
        .{ .r = .{ .min = c15 }, .p = "1e", .want = .alive },
        .{ .r = .{ .min = c15, .max = c16 }, .p = "15e", .want = .alive },
        .{ .r = .{ .min = c15, .max = c16 }, .p = "16e", .want = .alive },
        .{ .r = .{ .min = c15, .max = c16, .max_excl = true }, .p = "16e", .want = .dead },
    };
    for (cases) |c| {
        const got = try rangeCertAfter(c.r, c.p);
        if (got != c.want) std.debug.print("prefix {s}: got {s}\n", .{ c.p, @tagName(got) });
        try testing.expectEqual(c.want, got);
    }
}

fn specCertAfter(schema_json: []const u8, prefix: []const u8) !Cert {
    const schema_mod = @import("schema.zig");
    const alloc = testing.allocator;
    var diag: schema_mod.Diagnostic = .{};
    var w = work_mod.Work{};
    var g = try schema_mod.compile(alloc, schema_json, 64, &diag, &w, false, .spec_v1, &.{});
    defer g.deinit();
    var side = parser.Side.init(alloc);
    defer side.deinit();
    var st = try parser.initState(&g, parser.MAX_THREADS_CAP, &side);
    if (prefix.len == 0) return certifyState(&g, &st, &side);
    var bufs: [2]parser.State = undefined;
    for (&bufs) |*s| s.n = 0;
    try parser.feedBytes(&g, &side, &st, prefix, &bufs[0], &bufs[1]);
    return certifyState(&g, &bufs[0], &side);
}

test "complete r1: numeric conjunctions certify jointly (ADR-0011)" {
    const Case = struct { schema: []const u8, p: []const u8, want: Cert };
    const cases = [_]Case{
        // -51...: an integer needs a nonnegative exponent, so <= -51 < -9.
        .{ .schema = "{\"type\":\"integer\",\"minimum\":-9}", .p = "-51", .want = .dead },
        .{ .schema = "{\"type\":\"integer\",\"minimum\":-9}", .p = "-50", .want = .alive },
        .{ .schema = "{\"type\":\"integer\",\"minimum\":-9}", .p = "-5", .want = .alive },
        // Leading digits 12: 1.2, 0.12, 12, ... none is a multiple of 0.5 in (0, 2].
        .{ .schema = "{\"type\":\"number\",\"multipleOf\":0.5,\"maximum\":2}", .p = "12", .want = .dead },
        .{ .schema = "{\"type\":\"number\",\"multipleOf\":0.5,\"maximum\":2}", .p = "15", .want = .alive },
        .{ .schema = "{\"type\":\"number\",\"multipleOf\":0.5,\"maximum\":2}", .p = "1", .want = .alive },
        .{ .schema = "{\"type\":\"integer\",\"not\":{\"const\":5},\"minimum\":1,\"maximum\":9}", .p = "", .want = .alive },
        .{ .schema = "{\"type\":\"integer\",\"not\":{\"const\":5},\"minimum\":1,\"maximum\":9}", .p = "5", .want = .dead },
        .{ .schema = "{\"type\":\"integer\",\"not\":{\"const\":5},\"minimum\":1,\"maximum\":9}", .p = "0.6e", .want = .alive },
        .{ .schema = "{\"type\":\"integer\",\"maximum\":9}", .p = "51", .want = .dead },
        .{ .schema = "{\"type\":\"integer\",\"maximum\":9}", .p = "-", .want = .alive },
        .{ .schema = "{\"type\":\"integer\",\"minimum\":0,\"maximum\":1}", .p = "", .want = .alive },
        .{ .schema = "{\"type\":\"integer\",\"minimum\":0,\"maximum\":1}", .p = "2", .want = .dead },
        .{ .schema = "{\"type\":\"integer\",\"minimum\":0,\"maximum\":1}", .p = "10", .want = .alive },
        .{ .schema = "{\"type\":\"integer\",\"minimum\":1,\"maximum\":9}", .p = "61", .want = .dead },
        .{ .schema = "{\"type\":\"integer\",\"minimum\":1}", .p = "0.0", .want = .alive },
        .{ .schema = "{\"type\":\"integer\",\"exclusiveMinimum\":0}", .p = "", .want = .alive },
    };
    for (cases) |c| {
        const got = try specCertAfter(c.schema, c.p);
        if (got != c.want) std.debug.print("{s} after \"{s}\": got {s}\n", .{ c.schema, c.p, @tagName(got) });
        try testing.expectEqual(c.want, got);
    }
}

test "complete: contains and uniqueItems element residuals" {
    const Case = struct { schema: []const u8, p: []const u8, dead: bool };
    const one_false = "{\"type\":\"array\",\"maxItems\":1,\"contains\":{\"enum\":[false]}";
    const cases = [_]Case{
        // The only slot must match contains: a string element never does.
        .{ .schema = one_false ++ "}", .p = "[\"", .dead = true },
        .{ .schema = one_false ++ "}", .p = "[1", .dead = true },
        .{ .schema = one_false ++ "}", .p = "[f", .dead = false },
        .{ .schema = one_false ++ ",\"unevaluatedItems\":{\"type\":\"string\"}}", .p = "[\"", .dead = true },
        .{ .schema = one_false ++ ",\"unevaluatedItems\":{\"type\":\"string\"}}", .p = "[f", .dead = false },
        // Two distinct values exist; a third element can never be fresh.
        .{ .schema = "{\"type\":\"array\",\"items\":{\"enum\":[\"a\",\"b\"]},\"uniqueItems\":true}", .p = "[\"a\",\"b\",", .dead = true },
        .{ .schema = "{\"type\":\"array\",\"items\":{\"enum\":[\"a\",\"b\"]},\"uniqueItems\":true}", .p = "[\"a\",", .dead = false },
    };
    for (cases) |c| {
        const got = try specCertAfter(c.schema, c.p);
        if ((got == .dead) != c.dead) std.debug.print("{s} after \"{s}\": got {s}\n", .{ c.schema, c.p, @tagName(got) });
        try testing.expectEqual(c.dead, got == .dead);
    }
}

test "complete r1: open_obj residual certifies remaining required keys" {
    const alloc = testing.allocator;
    var arena = Arena.init(alloc);
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
    var arena2 = Arena.init(alloc);
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

/// An open object with integer values, the given extra_required names and
/// maxProperties (count tracked), as schema.zig compiles one.
fn countedObjGrammar(alloc: std.mem.Allocator, extra: []const []const u8, max: u32) !grammar.Grammar {
    var arena = Arena.init(alloc);
    const aa = arena.allocator();
    var b = grammar.Builder.init(aa);
    const int_node = try b.addNode(.{ .int_v = {} });
    var lits: [4]grammar.Literal = undefined;
    for (extra, 0..) |e, i| lits[i] = try b.addLiteral(e);
    const root = try b.addNode(.{ .open_obj = .{
        .props = &.{},
        .extra_required = try b.copyLiterals(lits[0..extra.len]),
        .value = int_node,
        .max_props = max,
        .track_keys = true,
    } });
    return b.finish(arena, .json_schema, root, .{});
}

test "complete: maxProperties counts the owed names and the in-flight key" {
    // With maxProperties keys seen, the comma was admitted and the next key
    // could never close; a key typed while every remaining slot belongs to
    // an owed name had no dead rule (RESOURCE_LIMIT). `closed`: alive in
    // closed form (countAlive); every token of a key or nested value is
    // checked on such a node.
    const alloc = testing.allocator;
    var side = parser.Side.init(alloc);
    defer side.deinit();
    const Case = struct { extra: []const []const u8, max: u32, prefix: []const u8, cert: Cert, closed: bool = false };
    const cases = [_]Case{
        // Comma after the only allowed member.
        .{ .extra = &.{}, .max = 1, .prefix = "{\"x\":1", .cert = .alive, .closed = true },
        .{ .extra = &.{}, .max = 1, .prefix = "{\"x\":1,", .cert = .dead },
        .{ .extra = &.{}, .max = 1, .prefix = "{\"x\":1}", .cert = .alive, .closed = true },
        .{ .extra = &.{}, .max = 0, .prefix = "{\"", .cert = .dead },
        .{ .extra = &.{}, .max = 2, .prefix = "{\"x\":1,", .cert = .alive, .closed = true },
        .{ .extra = &.{}, .max = 2, .prefix = "{\"x", .cert = .alive, .closed = true },
        // Every slot owed: the in-flight key must spell an owed name.
        .{ .extra = &.{"a"}, .max = 1, .prefix = "{\"", .cert = .alive, .closed = true },
        .{ .extra = &.{"a"}, .max = 1, .prefix = "{\"a", .cert = .alive, .closed = true },
        .{ .extra = &.{"a"}, .max = 1, .prefix = "{\"b", .cert = .dead },
        .{ .extra = &.{"a"}, .max = 1, .prefix = "{\"ab", .cert = .dead },
        .{ .extra = &.{"a"}, .max = 2, .prefix = "{\"b", .cert = .alive, .closed = true },
        .{ .extra = &.{ "a", "b" }, .max = 1, .prefix = "{", .cert = .dead },
        .{ .extra = &.{ "a", "b" }, .max = 2, .prefix = "{\"a\":1,\"", .cert = .alive, .closed = true },
        .{ .extra = &.{ "a", "b" }, .max = 2, .prefix = "{\"a\":1,\"c", .cert = .dead },
        .{ .extra = &.{ "a", "b" }, .max = 2, .prefix = "{\"a\":1,\"b", .cert = .alive, .closed = true },
        // Mid code point: the owed name must continue into the pending range.
        .{ .extra = &.{"\u{e9}"}, .max = 1, .prefix = "{\"\xc3", .cert = .alive },
        .{ .extra = &.{"\u{e9}"}, .max = 1, .prefix = "{\"\xc4", .cert = .dead },
    };
    for (cases) |c| {
        var g = try countedObjGrammar(alloc, c.extra, c.max);
        defer g.deinit();
        try testing.expect(g.needs_reachability);
        var st = try parser.initState(&g, 8, &side);
        var cur: parser.State = undefined;
        var work: parser.State = undefined;
        try parser.feedBytes(&g, &side, &st, c.prefix, &cur, &work);
        testing.expectEqual(c.cert, certifyState(&g, &cur, &side)) catch |e| {
            std.debug.print("max {d} prefix {s}\n", .{ c.max, c.prefix });
            return e;
        };
        if (c.closed) {
            in_search = true;
            defer in_search = false;
            testing.expectEqual(Cert.alive, certifyState(&g, &cur, &side)) catch |e| {
                std.debug.print("closed form: max {d} prefix {s}\n", .{ c.max, c.prefix });
                return e;
            };
        }
    }
}

test "complete r1: oneof vote-link residual doom (lit_trie overlap)" {
    const alloc = testing.allocator;
    var arena = Arena.init(alloc);
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
    var arena = Arena.init(alloc);
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
    var arena2 = Arena.init(alloc);
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

test "complete: pattern key completions are counted exactly on both dispatch sides" {
    // A finite patternProperties language was taken for an infinite one, so
    // a duplicate key was admitted and died at its closing quote (DEAD_END).
    // The non-matching side can be finite too: the remnant of a
    // near-universal pattern.
    var arena = std.heap.ArenaAllocator.init(testing.allocator);
    defer arena.deinit();
    const aa = arena.allocator();
    const cap: u64 = 1 << 40;
    const Case = struct { pat: []const u8, prefix: []const u8, window: ?CpWindow = null, hit: u64, miss: u64 };
    const near = "^([\\s\\S]{2,}|[^a])$";
    const cases = [_]Case{
        .{ .pat = "^k[12]$", .prefix = "", .hit = 2, .miss = cap },
        .{ .pat = "^k[12]$", .prefix = "k", .hit = 2, .miss = cap },
        .{ .pat = "^k[12]$", .prefix = "k1", .hit = 1, .miss = cap },
        .{ .pat = "^line[1-3]$", .prefix = "line", .hit = 3, .miss = cap },
        .{ .pat = "^ключ[12]$", .prefix = "ключ", .hit = 2, .miss = cap },
        .{ .pat = "^[ab]{3}$", .prefix = "", .hit = 8, .miss = cap },
        .{ .pat = "^a{2,3}$", .prefix = "a", .hit = 2, .miss = cap },
        .{ .pat = "^$", .prefix = "", .hit = 1, .miss = cap },
        // No end anchor: every extension of a match matches.
        .{ .pat = "^k[12]", .prefix = "k1", .hit = cap, .miss = 0 },
        // One code point other than a line terminator: every Unicode
        // scalar value but the four terminators, surrogates excluded.
        .{ .pat = "^.$", .prefix = "", .hit = 0x110000 - 0x800 - 4, .miss = cap },
        // Only "" and "a" miss; "b" matches with every extension (a cycle
        // among accepting states alone counts nothing on the miss side).
        .{ .pat = near, .prefix = "", .hit = cap, .miss = 2 },
        .{ .pat = near, .prefix = "a", .hit = cap, .miss = 1 },
        .{ .pat = near, .prefix = "b", .hit = cap, .miss = 0 },
        // Odd lengths miss forever: a cycle through a non-accepting state.
        .{ .pat = "^([\\s\\S][\\s\\S])*$", .prefix = "", .hit = cap, .miss = cap },
        // Unanchored search: the DFA never leaves the pattern, yet the
        // keys avoiding the needle are infinite.
        .{ .pat = "abc", .prefix = "", .hit = cap, .miss = cap },
        .{ .pat = "abc", .prefix = "xabc", .hit = cap, .miss = 0 },
        // Windows on the next code point (a key cut inside UTF-8).
        .{ .pat = "^k[12]$", .prefix = "k", .window = .{ .lo = '1', .hi = '1' }, .hit = 1, .miss = cap },
        .{ .pat = near, .prefix = "", .window = .{ .lo = 'b', .hi = 'b' }, .hit = cap, .miss = 0 },
        .{ .pat = "^([\\s\\S]{2,}|[^é])$", .prefix = "", .window = .{ .lo = 0xC0, .hi = 0xFF }, .hit = cap, .miss = 1 },
    };
    for (cases) |c| {
        const dfa = try aa.create(pattern.Dfa);
        dfa.* = try pattern.compileJsonSchema(aa, c.pat);
        var lb: pattern.LenBudget = .{};
        const descr = (try pattern.computeLenDescr(aa, dfa, &lb)) orelse return error.TestUnexpectedResult;
        const q = dfaFeedKeyBytes(dfa, c.prefix) orelse return error.TestUnexpectedResult;
        const hit = dfaKeyCount(testing.allocator, dfa, descr, q, c.window, .hit, cap) orelse return error.TestUnexpectedResult;
        const miss = dfaKeyCount(testing.allocator, dfa, descr, q, c.window, .miss, cap) orelse return error.TestUnexpectedResult;
        testing.expectEqual(c.hit, hit) catch |e| {
            std.debug.print("hit: pattern {s} prefix {s}\n", .{ c.pat, c.prefix });
            return e;
        };
        testing.expectEqual(c.miss, miss) catch |e| {
            std.debug.print("miss: pattern {s} prefix {s}\n", .{ c.pat, c.prefix });
            return e;
        };
        // A small cap saturates.
        try testing.expectEqual(@min(c.hit, 1), dfaKeyCount(testing.allocator, dfa, descr, q, c.window, .hit, 1).?);
    }
}
