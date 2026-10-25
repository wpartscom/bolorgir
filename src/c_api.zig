const std = @import("std");
const grammar = @import("grammar.zig");
const tokenizer = @import("tokenizer.zig");
const schema = @import("schema.zig");
const literals = @import("literals.zig");
const parser = @import("parser.zig");
const mask = @import("mask.zig");
const complete = @import("complete.zig");
const alloc = @import("alloc.zig");
const stats = @import("stats.zig");
const cache = @import("cache.zig");
const coverage = @import("coverage.zig");
const work_mod = @import("work.zig");
const precompute = @import("precompute.zig");
const probe = @import("probe.zig");
const io_mod = @import("io.zig");
const regex = @import("regex.zig");
const structtag = @import("structtag.zig");
const mask_diag = @import("diag.zig");

pub const ABI_VERSION: u32 = 1;
pub const ERROR_MESSAGE_CAP = 256;
pub const JSON_POINTER_CAP = 256;
pub const STATS_CATEGORY_COUNT = 8;
pub const NO_OFFSET: u32 = std.math.maxInt(u32);

const DEFAULT_MAX_DEPTH: u32 = 64;
const DEFAULT_MAX_THREADS: u32 = 128;
const DEFAULT_MEMORY_LIMIT: u64 = 256 << 20;
pub const DEFAULT_CACHE_LIMIT: u64 = 64 << 20;
const DEFAULT_SESSION_LIMIT: u64 = 8 << 20;
const DEFAULT_SCHEMA_LIMIT: u64 = 1 << 20;
const DEFAULT_ADAPTIVE_MIN_HITS: u64 = 2;
const DEFAULT_ADAPTIVE_MIN_COST_NS: u64 = 50_000;
const DEFAULT_PRECOMPUTE_MAX_STATES: u64 = 4096;
const MASK_FAST_PATH_OFF: u64 = 2;
const MAX_WORKERS_CAP: u64 = 64;

const root_allocator: std.mem.Allocator = std.heap.smp_allocator;

pub const CACHE_DEFAULT: u64 = std.math.maxInt(u64);

pub const Status = enum(c_int) {
    ok = 0,
    invalid_argument = 1,
    invalid_schema = 2,
    unsupported_feature = 3,
    unsatisfiable_constraint = 4,
    unsupported_tokenizer = 5,
    invalid_token = 6,
    dead_end = 7,
    resource_limit = 8,
    cancelled = 9,
    busy = 10,
    wrong_state = 11,
    buffer_too_small = 12,
    internal = 13,
};

pub const ZgError = extern struct {
    struct_size: u32,
    code: i32,
    schema_offset: u32,
    message: [ERROR_MESSAGE_CAP]u8,
    json_pointer: [JSON_POINTER_CAP]u8,
};

const LEGACY_ERROR_SIZE: u32 = 268;

comptime {
    std.debug.assert(@sizeOf(ZgError) == 524);
    std.debug.assert(@offsetOf(ZgError, "json_pointer") == LEGACY_ERROR_SIZE);
}

pub const ContextConfig = extern struct {
    struct_size: u32,
    version: u32,
    mode: u32,
    max_depth: u32,
    max_threads_per_state: u32,
    reserved0: u32,
    memory_limit_bytes: u64,
    cache_limit_bytes: u64,
    session_limit_bytes: u64,
    schema_limit_bytes: u64,
    work_limit_ops: u64,
    adaptive_min_hits: u64,
    adaptive_min_cost_ns: u64,
    precompute_max_states: u64,
    mask_fast_path: u64,
    max_workers: u64,
    probe_max_states: u64 = 0,
    probe_max_work: u64 = 0,
};

const LEGACY_CONFIG_SIZE: u32 = 56;

comptime {
    std.debug.assert(@sizeOf(ContextConfig) == 120);
    std.debug.assert(@offsetOf(ContextConfig, "probe_max_states") == 104);
    std.debug.assert(@offsetOf(ContextConfig, "work_limit_ops") == 56);
    std.debug.assert(@offsetOf(ContextConfig, "adaptive_min_hits") == 64);
    std.debug.assert(@offsetOf(ContextConfig, "adaptive_min_cost_ns") == 72);
    std.debug.assert(@offsetOf(ContextConfig, "precompute_max_states") == 80);
    std.debug.assert(@offsetOf(ContextConfig, "mask_fast_path") == 88);
    std.debug.assert(@offsetOf(ContextConfig, "max_workers") == 96);
}

pub const TokenEntry = extern struct {
    id: u32,
    reserved: u32,
    offset: u64,
    length: u64,
};

pub const TokenizerDesc = extern struct {
    struct_size: u32,
    vocab_size: u32,
    entries: ?[*]const TokenEntry,
    entry_count: usize,
    blob: ?[*]const u8,
    blob_len: usize,
    eos_ids: ?[*]const u32,
    eos_count: usize,
    special_ids: ?[*]const u32,
    special_count: usize,
    /// Tail-grown field: legacy structs (without it) are accepted with
    /// flags = 0; see BLG_TOKENIZER_* in the public header.
    flags: u32 = 0,
    reserved1: u32 = 0,
};

pub const CompileRequest = extern struct {
    struct_size: u32,
    kind: u32,
    profile: ?[*:0]const u8,
    data: ?[*]const u8,
    data_len: usize,
    // Tail-grown (P5, ADR-0006 D5 / ADR-0008): external-$ref registry
    // snapshot bytes (see include/bolorgir.h). Absent in legacy requests.
    registry_data: ?[*]const u8 = null,
    registry_data_len: usize = 0,
    // Tail-grown (ADR-0012): BLG_COMPILE_PROBE* bits.
    flags: u32 = 0,
    reserved1: u32 = 0,
};

/// Size of the pre-P5 blg_compile_request layout (no registry tail): a
/// caller built against the old header passes it and gets "no registry".
const LEGACY_COMPILE_REQUEST_SIZE: u32 = 32;
/// Size of the layout with the registry tail and without flags.
const REGISTRY_COMPILE_REQUEST_SIZE: u32 = 48;

pub const COMPILE_PROBE: u32 = 1;
pub const COMPILE_PROBE_STRICT: u32 = 2;
const DEFAULT_PROBE_MAX_STATES: u64 = 256;
const DEFAULT_PROBE_MAX_WORK: u64 = 1 << 24;

comptime {
    std.debug.assert(@offsetOf(CompileRequest, "flags") == REGISTRY_COMPILE_REQUEST_SIZE);
    std.debug.assert(@sizeOf(CompileRequest) == 56);
}

pub const StatsC = extern struct {
    struct_size: u32,
    version: u32,
    compile_ns: u64,
    tokenizer_prepare_ns: u64,
    accept_ns_total: u64,
    mask_ns_total: u64,
    mask_calls: u64,
    tokens_accepted: u64,
    cache_hits: u64,
    cache_misses: u64,
    cache_evictions: u64,
    mem_used: [STATS_CATEGORY_COUNT]u64,
    mem_peak: [STATS_CATEGORY_COUNT]u64,
    mode: u32,
    reserved0: u32,
    errors_total: u64,
    errors_resource_limit: u64,
    errors_cancelled: u64,
    cache_adaptive_skips: u64,
    precompute_states: u64,
    work_ops_total: u64,
    probe_runs: u64,
};

const LEGACY_STATS_SIZE: u32 = 208;
/// Layout before probe_runs.
const PRE_PROBE_STATS_SIZE: u32 = 264;

comptime {
    std.debug.assert(@sizeOf(StatsC) == 272);
    std.debug.assert(@offsetOf(StatsC, "probe_runs") == PRE_PROBE_STATS_SIZE);
    std.debug.assert(@offsetOf(StatsC, "mode") == LEGACY_STATS_SIZE);
    std.debug.assert(@offsetOf(StatsC, "errors_total") == 216);
    std.debug.assert(@offsetOf(StatsC, "errors_resource_limit") == 224);
    std.debug.assert(@offsetOf(StatsC, "errors_cancelled") == 232);
    std.debug.assert(@offsetOf(StatsC, "cache_adaptive_skips") == 240);
    std.debug.assert(@offsetOf(StatsC, "precompute_states") == 248);
    std.debug.assert(@offsetOf(StatsC, "work_ops_total") == 256);
}

const Mode = enum { lazy, adaptive, precompute };
const SessionStatus = enum { active, finished, aborted };
const ConstraintKind = enum { json_schema, literal_set, regex, structural_tag };

/// Counter of memory actually retained by a grammar.
/// Every arena allocation of the grammar goes through this allocator, so
/// meter.used = bytes (accounting headers included) that Grammar.deinit()
/// will free. Needed for an honest artifact cache budget: grammars are
/// retained by the cache and must count toward its limit.
const GrammarMeter = struct {
    child: std.mem.Allocator,
    used: usize = 0,

    fn allocator(self: *GrammarMeter) std.mem.Allocator {
        return .{ .ptr = self, .vtable = &vtable };
    }

    const vtable: std.mem.Allocator.VTable = .{
        .alloc = allocFn,
        .resize = resizeFn,
        .remap = std.mem.Allocator.noRemap,
        .free = freeFn,
    };

    fn allocFn(p: *anyopaque, len: usize, alignment: std.mem.Alignment, ret_addr: usize) ?[*]u8 {
        const m: *GrammarMeter = @ptrCast(@alignCast(p));
        const raw = m.child.rawAlloc(len, alignment, ret_addr) orelse return null;
        m.used += alloc.chargedBytes(len, alignment);
        return raw;
    }

    fn resizeFn(p: *anyopaque, memory: []u8, alignment: std.mem.Alignment, new_len: usize, ret_addr: usize) bool {
        const m: *GrammarMeter = @ptrCast(@alignCast(p));
        const old_total = alloc.chargedBytes(memory.len, alignment);
        const new_total = alloc.chargedBytes(new_len, alignment);
        if (!m.child.rawResize(memory, alignment, new_len, ret_addr)) return false;
        if (new_total >= old_total) {
            m.used += new_total - old_total;
        } else {
            m.used -= old_total - new_total;
        }
        return true;
    }

    fn freeFn(p: *anyopaque, memory: []u8, alignment: std.mem.Alignment, ret_addr: usize) void {
        const m: *GrammarMeter = @ptrCast(@alignCast(p));
        m.used -= alloc.chargedBytes(memory.len, alignment);
        m.child.rawFree(memory, alignment, ret_addr);
    }
};

pub const GrammarHandle = struct {
    ctx: *Context,
    g: grammar.Grammar,
    refcount: std.atomic.Value(u32),
    meter: GrammarMeter,
    /// Last probe verdict kept with the artifact and the budgets it ran
    /// under (ADR-0012): a cache hit reuses it only for equal budgets.
    probe_status: probe.Status = .not_run,
    probe_limits: probe.Limits = .{ .max_states = 0, .max_work = 0 },
};

/// Bounded cache of immutable compile artifacts (warm compile):
/// the same kind + exact schema bytes + profile inside one context produces
/// the same grammar; repeated blg_compile returns a new reference to the
/// cached handle instead of compiling again. Hash is only an index - a hit is
/// verified by comparing the stored bytes (schema is immutable by contract).
/// Entries are charged to the grammar category and capped by a fraction of
/// the context cache budget; eviction drops the cache reference and never
/// touches live sessions (they hold their own reference).
///
/// The budget counts actually retained bytes:
/// the whole grammar (GrammarMeter), GrammarHandle, the schema-bytes copy,
/// accounting headers and the entry list capacity. Metadata alone is not
/// enough: at artifact_limit=16 KiB a false "fits" retained a quarter
/// megabyte of grammars and ate into the global memory_limit.
const GrammarArtifactCache = struct {
    const Entry = struct {
        kind: ConstraintKind,
        profile_key: u8, // 0 = no profile, 1 = canonical-v1
        hash: u64,
        bytes: []u8,
        registry: []u8, // P5: retained registry snapshot bytes (may be empty)
        gh: *GrammarHandle,
        cost: usize, // full retained cost of the entry, bytes
    };

    entries: std.ArrayListUnmanaged(Entry) = .empty,
    total: usize = 0, // struct_bytes (capacity) + Σ cost
    struct_bytes: usize = 0, // entries list capacity, accounting bytes
    limit: usize = 0,
    refs: u32 = 0,

    fn init(limit: usize) GrammarArtifactCache {
        return .{ .limit = limit };
    }
};

/// Full artifact cost: everything retained while a cache entry is alive,
/// beyond the already charged list capacity.
fn artifactCost(gh: *GrammarHandle, bytes_len: usize, registry_len: usize) usize {
    const grammar_bytes = gh.meter.used;
    const handle_bytes = alloc.chargedBytes(
        @sizeOf(GrammarHandle),
        std.mem.Alignment.fromByteUnits(@alignOf(GrammarHandle)),
    );
    const copy_bytes = alloc.chargedBytes(
        bytes_len +| registry_len,
        std.mem.Alignment.fromByteUnits(@alignOf(u8)),
    );
    return std.math.add(usize, grammar_bytes, handle_bytes +| copy_bytes) catch std.math.maxInt(usize);
}

fn artifactHash(kind: ConstraintKind, profile_key: u8, bytes: []const u8, registry: []const u8) u64 {
    var h = std.hash.Wyhash.init(0x9E3779B97F4A7C15);
    h.update(&[_]u8{ @backingInt(kind), profile_key });
    var lenbuf: [8]u8 = undefined;
    std.mem.writeInt(u64, &lenbuf, bytes.len, .little);
    h.update(&lenbuf);
    h.update(bytes);
    // P5 (ADR-0006 D5, ADR-0008): the registry snapshot bytes join the
    // artifact key; a registry swap is a new snapshot and misses the cache.
    std.mem.writeInt(u64, &lenbuf, registry.len, .little);
    h.update(&lenbuf);
    h.update(registry);
    return h.final();
}

fn artifactLookup(ctx: *Context, kind: ConstraintKind, profile_key: u8, hash: u64, bytes: []const u8, registry: []const u8) ?*GrammarHandle {
    const c = &ctx.grammar_cache;
    if (c.limit == 0) return null;
    ctx.mutex.lock();
    defer ctx.mutex.unlock();
    for (c.entries.items) |*e| {
        if (e.kind == kind and e.profile_key == profile_key and e.hash == hash and
            std.mem.eql(u8, e.bytes, bytes) and std.mem.eql(u8, e.registry, registry))
        {
            _ = e.gh.refcount.fetchAdd(1, .monotonic);
            return e.gh;
        }
    }
    return null;
}

/// Destroys a handle that nobody references anymore; caller holds ctx.mutex.
fn destroyHandleLocked(ctx: *Context, gh: *GrammarHandle) void {
    const ga = ctx.accounting.allocator(.grammar);
    gh.g.deinit();
    ga.destroy(gh);
    ctx.live_grammars -= 1;
}

/// Drops one reference; caller holds ctx.mutex. The handle dies only when
/// the dropped reference was the last one (live sessions keep their own).
fn releaseGrammarRefLocked(ctx: *Context, gh: *GrammarHandle) void {
    if (gh.refcount.fetchSub(1, .acq_rel) == 1) destroyHandleLocked(ctx, gh);
}

/// Bytes charged by the entries array at a given capacity.
fn entryCapacityBytes(capacity: usize) usize {
    if (capacity == 0) return 0;
    return alloc.chargedBytes(
        capacity * @sizeOf(GrammarArtifactCache.Entry),
        std.mem.Alignment.fromByteUnits(@alignOf(GrammarArtifactCache.Entry)),
    );
}

fn artifactInsert(ctx: *Context, kind: ConstraintKind, profile_key: u8, hash: u64, bytes: []const u8, registry: []const u8, gh: *GrammarHandle) void {
    const c = &ctx.grammar_cache;
    if (c.limit == 0) return;
    const cost = artifactCost(gh, bytes.len, registry.len);
    if (cost > c.limit) return; // does not fit - simply skip caching
    const ga = ctx.accounting.allocator(.grammar);
    const copy = ga.dupe(u8, bytes) catch return;
    const reg_copy = ga.dupe(u8, registry) catch {
        ga.free(copy);
        return;
    };
    ctx.mutex.lock();
    defer ctx.mutex.unlock();
    // List capacity growth is retained memory too: reserve space up front
    // and count it in the budget.
    c.entries.ensureUnusedCapacity(ga, 1) catch {
        ga.free(copy);
        ga.free(reg_copy);
        return;
    };
    const struct_now = entryCapacityBytes(c.entries.capacity);
    c.total += struct_now - c.struct_bytes;
    c.struct_bytes = struct_now;
    while (c.entries.items.len > 0 and c.total + cost > c.limit) {
        const old = c.entries.orderedRemove(0);
        c.total -= old.cost;
        c.refs -= 1;
        releaseGrammarRefLocked(ctx, old.gh);
        ga.free(old.bytes);
        ga.free(old.registry);
    }
    if (c.total + cost > c.limit) {
        ga.free(copy);
        ga.free(reg_copy);
        return;
    }
    c.entries.appendAssumeCapacity(.{
        .kind = kind,
        .profile_key = profile_key,
        .hash = hash,
        .bytes = copy,
        .registry = reg_copy,
        .gh = gh,
        .cost = cost,
    });
    c.total += cost;
    c.refs += 1;
    _ = gh.refcount.fetchAdd(1, .monotonic);
}

/// Drops all cache references; caller must hold no user handles/sessions.
fn artifactDeinit(ctx: *Context) void {
    const c = &ctx.grammar_cache;
    const ga = ctx.accounting.allocator(.grammar);
    ctx.mutex.lock();
    defer ctx.mutex.unlock();
    for (c.entries.items) |e| {
        releaseGrammarRefLocked(ctx, e.gh);
        ga.free(e.bytes);
        ga.free(e.registry);
    }
    c.entries.deinit(ga);
    c.total = 0;
    c.struct_bytes = 0;
    c.refs = 0;
}

/// Drops all cache references but keeps the cache usable (blg_context_reset_cache).
/// Live user handles/sessions keep their own references and stay valid; the
/// freed memory (including the entries array) returns to the context accounting.
fn artifactReset(ctx: *Context) void {
    const c = &ctx.grammar_cache;
    const ga = ctx.accounting.allocator(.grammar);
    ctx.mutex.lock();
    defer ctx.mutex.unlock();
    for (c.entries.items) |e| {
        releaseGrammarRefLocked(ctx, e.gh);
        ga.free(e.bytes);
        ga.free(e.registry);
    }
    c.entries.deinit(ga);
    c.entries = .empty;
    c.total = 0;
    c.struct_bytes = 0;
    c.refs = 0;
}

pub const Context = struct {
    accounting: alloc.Accounting,
    tok: tokenizer.Tokenizer,
    cache: cache.Cache,
    mode: Mode,
    max_depth: u32,
    max_threads: u16,
    session_limit: u64,
    schema_limit: u64,
    work_limit_ops: u64,
    adaptive_min_hits: u64,
    adaptive_min_cost_ns: u64,
    precompute_max_states: u64,
    probe_limits: probe.Limits,
    /// ADR-0007: fast-path tables of the tokenizer (null when the fast
    /// path is disabled or the tables could not be built - the exact walk
    /// is always a correct fallback).
    fast: ?precompute.FastData,
    max_workers: u32,
    cancel_flag: ?*align(1) const u8,
    live_grammars: u32,
    live_sessions: u32,
    /// Live external grammar references: one ref per successful blg_compile
    /// not yet released by blg_grammar_release. Artifact cache references do
    /// not count here.
    external_grammar_refs: u32,
    /// Number of in-flight calls (blg_compile / blg_context_reset_cache):
    /// destroy during a compile must return BUSY instead of freeing the
    /// context under a running call.
    active_calls: u32,
    grammar_cache: GrammarArtifactCache,
    stats: stats.Stats,
    mutex: io_mod.Mutex,
};

pub const SessionConfig = extern struct {
    struct_size: u32,
    max_rollback_tokens: u32,
};

pub const MAX_ROLLBACK_TOKENS: u32 = 65535;

/// ADR-0014: one record per accept: the parser state it left (JSON
/// grammars), the regex state (BLG_CONSTRAINT_REGEX) or the structural-tag
/// small state plus its JSON content snapshot (BLG_CONSTRAINT_STRUCTURAL_TAG).
const Record = struct {
    snap: parser.Snapshot = .{},
    rx: regex.State = .{},
    tag: structtag.SmallState = .{},
    has_snap: bool = false,
};

/// ADR-0014: records of the states the last accepts left, oldest at
/// `head`; `buf` grows on demand up to `cap` records.
const History = struct {
    cap: usize = 0,
    buf: []Record = &.{},
    head: usize = 0,
    len: usize = 0,

    fn at(h: *History, i: usize) *Record {
        return &h.buf[(h.head + i) % h.buf.len];
    }

    /// Frees a slot for one more record: grows `buf` while below `cap`,
    /// otherwise drops the oldest record.
    fn makeRoom(h: *History, a: std.mem.Allocator, side: *parser.Side) error{OutOfMemory}!void {
        if (h.len < h.buf.len) return;
        if (h.buf.len == h.cap) {
            parser.dropSnapshot(a, side, &h.at(0).snap);
            h.head = (h.head + 1) % h.buf.len;
            h.len -= 1;
            return;
        }
        const nb = try a.alloc(Record, @min(h.cap, @max(4, h.buf.len * 2)));
        for (0..h.len) |i| nb[i] = h.at(i).*;
        a.free(h.buf);
        h.buf = nb;
        h.head = 0;
    }

    fn popNewest(h: *History) Record {
        h.len -= 1;
        return h.at(h.len).*;
    }

    fn deinit(h: *History, a: std.mem.Allocator, side: *parser.Side) void {
        for (0..h.len) |i| parser.dropSnapshot(a, side, &h.at(i).snap);
        a.free(h.buf);
        h.* = .{};
    }
};

pub const Session = struct {
    ctx: *Context,
    gh: *GrammarHandle,
    account: alloc.SessionAccount,
    // Ping-pong parser states: accept feeds cur into cur^1 and flips the
    // index. Swapping whole State structs instead would memcpy ~768 KiB per
    // token (State is ~256 KiB).
    states: [2]parser.State,
    cur: u1,
    status: SessionStatus,
    stats: stats.Stats,
    mask_buf: mask.MaskBuf,
    side: parser.Side,
    history: History,
    /// Session state of a BLG_CONSTRAINT_REGEX grammar (`states` unused).
    rx: regex.State,
    /// Slot states of a BLG_CONSTRAINT_STRUCTURAL_TAG grammar: the current
    /// state plus two chain scratches; the current slot is never overwritten
    /// while a token is fed, so a rejected token leaves the session as it
    /// was (ADR-0016). `states[0..1]` serve as JSON feed scratches.
    tag: ?[*]structtag.State = null,
    tag_cur: u8 = 0,
    tag_pool: structtag.Pool = .{},
};

inline fn curState(s: anytype) @TypeOf(&s.states[0]) {
    return &s.states[s.cur];
}

inline fn scratchState(s: *Session) *parser.State {
    return &s.states[s.cur ^ 1];
}

fn errBufValid(err: ?*ZgError) bool {
    const e = err orelse return false;
    return e.struct_size == @sizeOf(ZgError) or e.struct_size == LEGACY_ERROR_SIZE;
}

// A passed but malformed diagnostics buffer is a caller-side error.
fn badErrBuf(err: ?*ZgError) bool {
    return err != null and !errBufValid(err);
}

fn writeError(err: ?*ZgError, code: Status, offset: u32, msg: []const u8, pointer: []const u8) void {
    const e = err orelse return;
    if (!errBufValid(e)) return;
    e.code = @backingInt(code);
    e.schema_offset = offset;
    const n = @min(msg.len, ERROR_MESSAGE_CAP - 1);
    @memcpy(e.message[0..n], msg[0..n]);
    e.message[n] = 0;
    // The pointer field exists only in the current layout; a legacy-size
    // buffer gets the prefix fields only.
    if (e.struct_size == @sizeOf(ZgError)) {
        const m = @min(pointer.len, JSON_POINTER_CAP - 1);
        @memcpy(e.json_pointer[0..m], pointer[0..m]);
        e.json_pointer[m] = 0;
    }
}

fn clearError(err: ?*ZgError) void {
    writeError(err, .ok, NO_OFFSET, "", "");
}

fn fail(err: ?*ZgError, code: Status, msg: []const u8) Status {
    writeError(err, code, NO_OFFSET, msg, "");
    return code;
}

// Copies src into a possibly legacy-size caller buffer (batch row errors).
fn copyError(dst: *ZgError, src: *const ZgError) void {
    if (dst.struct_size == @sizeOf(ZgError)) {
        dst.* = src.*;
        return;
    }
    dst.code = src.code;
    dst.schema_offset = src.schema_offset;
    @memcpy(&dst.message, &src.message);
}

fn recordCtxErr(ctx: *Context, code: Status) void {
    ctx.mutex.lock();
    ctx.stats.recordError(code == .resource_limit, code == .cancelled);
    ctx.mutex.unlock();
}

fn startTimer() ?std.Io.Clock.Timestamp {
    return std.Io.Clock.Timestamp.now(io_mod.io(), .awake);
}

// Reads a tail u64 field only when the caller's struct_size covers it.
fn cfgU64(c: *const ContextConfig, comptime field: []const u8) ?u64 {
    if (c.struct_size >= @offsetOf(ContextConfig, field) + @sizeOf(u64))
        return @field(c, field);
    return null;
}

fn elapsedNs(timer: *?std.Io.Clock.Timestamp) u64 {
    const t = timer.* orelse return 0;
    const d = t.untilNow(io_mod.io());
    if (d.raw.nanoseconds <= 0) return 0;
    return @intCast(d.raw.nanoseconds);
}

/// BLG_MASK_FAST_PATH=0 in the process environment forces the mask fast
/// path off (ADR-0007 kill switch) regardless of the config field. The
/// library is statically linked (no libc environ pointer), so the block is
/// read from /proc/self/environ; any read failure keeps the config value.
fn envDisablesFastPath() bool {
    const io = io_mod.io();
    const f = std.Io.Dir.openFileAbsolute(io, "/proc/self/environ", .{}) catch return false;
    defer f.close(io);
    var rbuf: [4096]u8 = undefined;
    var r = f.readerStreaming(io, &rbuf);
    var buf: [16384]u8 = undefined;
    const n = r.interface.readSliceShort(&buf) catch return false;
    const data = buf[0..n];
    const needle = "BLG_MASK_FAST_PATH=";
    var i: usize = 0;
    while (std.mem.indexOfPos(u8, data, i, needle)) |pos| {
        if (pos == 0 or data[pos - 1] == 0) {
            const v = data[pos + needle.len ..];
            return v.len > 0 and v[0] == '0' and (v.len == 1 or v[1] == 0);
        }
        i = pos + needle.len;
    }
    return false;
}

fn releaseGrammarRef(gh: *GrammarHandle) void {
    if (gh.refcount.fetchSub(1, .acq_rel) == 1) {
        const ctx = gh.ctx;
        const ga = ctx.accounting.allocator(.grammar);
        gh.g.deinit();
        ga.destroy(gh);
        ctx.mutex.lock();
        ctx.live_grammars -= 1;
        ctx.mutex.unlock();
    }
}

const CompileError = error{
    InvalidSchema,
    UnsupportedFeature,
    UnsatisfiableConstraint,
    ResourceLimit,
    UnsupportedTokenizer,
    UnprovenPartialVocab,
    Cancelled,
    OutOfMemory,
};

fn compileGrammar(ctx: *Context, kind: ConstraintKind, bytes: []const u8, registry: []const u8, diag: *schema.Diagnostic, w: *work_mod.Work, ga: std.mem.Allocator, profile: schema.Profile, record_pointers: bool) CompileError!grammar.Grammar {
    const strip = ctx.tok.strip_lead_space;
    var g: grammar.Grammar = switch (kind) {
        .json_schema => try schema.compileWith(ga, bytes, ctx.max_depth, diag, w, strip, profile, registry, .{ .record_pointers = record_pointers }),
        .literal_set => try literals.compileLiterals(ga, bytes, ctx.max_depth, diag, w, strip),
        .regex => return compileRegexGrammar(ctx, bytes, diag, ga),
        .structural_tag => return compileStructuralTagGrammar(ctx, bytes, diag, w, ga),
    };
    errdefer g.deinit();
    // Reject constraints the tokenizer cannot fully express instead of
    // allowing dead-end tokens at generation time.
    const covered = coverage.checkCoverage(ctx.accounting.allocator(.temp), &g, &ctx.tok, w) catch |e| switch (e) {
        error.OutOfMemory => return error.OutOfMemory,
        error.Cancelled => return error.Cancelled,
        error.ResourceLimit => return error.ResourceLimit,
    };
    if (!covered) return error.UnsupportedTokenizer;
    // Completion reachability: with a partial
    // vocabulary every allowed state must have a token path to a completed
    // document. That is proven exactly for byte-complete vocabularies (any
    // byte continuation is feedable one byte at a time) and for finite
    // literal languages (exact completion filter in mask/accept). For any
    // other combination completion is not guaranteed, so the pair is
    // refused before generation instead of risking dead-end tokens.
    if (!ctx.tok.byte_complete and !g.finite_literal) return error.UnprovenPartialVocab;
    return g;
}

/// BLG_CONSTRAINT_REGEX (ADR-0015): the program is exact only on a
/// byte-complete vocabulary (every live DFA state completes byte by byte),
/// so other vocabularies are refused instead of risking dead-end tokens.
fn compileRegexGrammar(ctx: *Context, bytes: []const u8, diag: *schema.Diagnostic, ga: std.mem.Allocator) CompileError!grammar.Grammar {
    if (!ctx.tok.byte_complete) {
        schema.report(diag, .unsupported_feature, NO_OFFSET, "a regex constraint needs a byte-complete vocabulary", .{});
        return error.UnsupportedTokenizer;
    }
    var arena = alloc.Arena.init(ga);
    errdefer arena.deinit();
    const prog = arena.allocator().create(regex.Program) catch return error.OutOfMemory;
    var off: usize = 0;
    prog.* = regex.compile(ga, bytes, ctx.tok.strip_lead_space, &off) catch |e| {
        const at: u32 = @intCast(@min(off, NO_OFFSET - 1));
        return switch (e) {
            error.Invalid => blk: {
                schema.report(diag, .invalid_schema, at, "invalid regex at byte {d}", .{off});
                break :blk error.InvalidSchema;
            },
            error.Unsupported => blk: {
                schema.report(diag, .unsupported_feature, at, "unsupported regex construct or size limit at byte {d}", .{off});
                break :blk error.UnsupportedFeature;
            },
            error.Unsatisfiable => blk: {
                schema.report(diag, .unsatisfiable_constraint, NO_OFFSET, "the regex matches no string", .{});
                break :blk error.UnsatisfiableConstraint;
            },
            error.OutOfMemory => error.OutOfMemory,
        };
    };
    const ident = grammar.identityOf(.regex, bytes);
    return .{
        .kind = .regex,
        .nodes = &.{},
        .literal_pool = &.{},
        .root = 0,
        .id = ident.lo,
        .id_hi = ident.hi,
        .finite_literal = false,
        .regex = prog,
        .arena = arena,
    };
}

/// BLG_CONSTRAINT_STRUCTURAL_TAG (ADR-0016): tool-call framing (see
/// src/structtag.zig). Like the regex constraint it needs a byte-complete
/// vocabulary: the begin/end literals and the tag contents span the exact
/// byte table. The accepted spec subset and the explicit refusals are
/// documented in ADR-0016 and docs/supported_features.md.
fn compileStructuralTagGrammar(ctx: *Context, bytes: []const u8, diag: *schema.Diagnostic, w: *work_mod.Work, ga: std.mem.Allocator) CompileError!grammar.Grammar {
    if (!ctx.tok.byte_complete) {
        schema.report(diag, .unsupported_feature, NO_OFFSET, "a structural-tag constraint needs a byte-complete vocabulary", .{});
        return error.UnsupportedTokenizer;
    }
    var arena = alloc.Arena.init(ga);
    errdefer arena.deinit();
    const prog = arena.allocator().create(structtag.Program) catch return error.OutOfMemory;
    prog.* = structtag.compile(ga, bytes, ctx.max_depth, ctx.tok.strip_lead_space, diag, w) catch |e| return switch (e) {
        error.InvalidSchema => error.InvalidSchema,
        error.UnsupportedFeature => error.UnsupportedFeature,
        error.Unsatisfiable => error.UnsatisfiableConstraint,
        error.ResourceLimit => error.ResourceLimit,
        error.Cancelled => error.Cancelled,
        error.OutOfMemory => error.OutOfMemory,
    };
    const ident = grammar.identityOf(.structural_tag, bytes);
    return .{
        .kind = .structural_tag,
        .nodes = &.{},
        .literal_pool = &.{},
        .root = 0,
        .id = ident.lo,
        .id_hi = ident.hi,
        .finite_literal = false,
        .structtag = prog,
        .arena = arena,
    };
}

fn mapCompileError(e: CompileError, diag: *const schema.Diagnostic, err: ?*ZgError) Status {
    const msg: []const u8 = if (diag.message_len > 0) diag.text() else switch (e) {
        error.InvalidSchema => "invalid schema",
        error.UnsupportedFeature => "unsupported schema feature",
        error.UnsatisfiableConstraint => "unsatisfiable constraint",
        error.ResourceLimit => "schema resource limit exceeded",
        error.UnsupportedTokenizer => "tokenizer does not cover the constraint",
        error.UnprovenPartialVocab => "partial byte coverage with an infinite language: completion reachability is not guaranteed (use a byte-complete vocabulary or a finite literal constraint)",
        error.Cancelled => "cancelled",
        error.OutOfMemory => "out of memory during compile",
    };
    const code: Status = switch (e) {
        error.InvalidSchema => .invalid_schema,
        error.UnsupportedFeature => .unsupported_feature,
        error.UnsatisfiableConstraint => .unsatisfiable_constraint,
        error.ResourceLimit, error.OutOfMemory => .resource_limit,
        error.UnsupportedTokenizer, error.UnprovenPartialVocab => .unsupported_tokenizer,
        error.Cancelled => .cancelled,
    };
    writeError(err, code, diag.offset, msg, diag.pointerText());
    return code;
}

fn maskFail(err: ?*ZgError, e: mask.Error) Status {
    return switch (e) {
        error.DeadEnd => fail(err, .dead_end, "no token can continue the document"),
        error.Cancelled => fail(err, .cancelled, "operation cancelled"),
        error.ResourceLimit => fail(err, .resource_limit, "mask computation limit exceeded"),
        error.OutOfMemory => fail(err, .resource_limit, "out of memory during mask computation"),
    };
}

fn fillMaskImpl(s: *Session, mask_raw: [*]align(1) u32, mask_words: usize, err: ?*ZgError) Status {
    const rc = fillMaskInner(s, mask_raw, mask_words, err);
    if (rc != .ok) recordCtxErr(s.ctx, rc);
    return rc;
}

fn fillMaskInner(s: *Session, mask_raw: [*]align(1) u32, mask_words: usize, err: ?*ZgError) Status {
    if (s.status != .active) return fail(err, .wrong_state, "session not active");
    if (@intFromPtr(mask_raw) % @alignOf(u32) != 0)
        return fail(err, .invalid_argument, "mask pointer misaligned");
    const ctx = s.ctx;
    const needed = ctx.tok.maskWords();
    if (mask_words < needed)
        return fail(err, .buffer_too_small, "mask buffer too small");
    const out = @as([*]u32, @alignCast(mask_raw))[0..needed];
    const g = &s.gh.g;
    ctx.mutex.lock();
    const cancel = ctx.cancel_flag;
    ctx.mutex.unlock();
    var w = work_mod.Work.init(ctx.work_limit_ops, cancel);
    // A registered cancellation must win even when the answer would be a
    // pure cache hit: charge(0) checks the flag only.
    w.charge(0) catch |e| return maskFail(err, e);
    var timer = startTimer();
    if (g.regex) |rp| {
        regex.fillMask(rp, &ctx.tok, s.rx, out, &w) catch |e| return maskFail(err, e);
        const ns = elapsedNs(&timer);
        s.stats.recordMaskCall(ns);
        ctx.mutex.lock();
        ctx.stats.recordMaskCall(ns);
        ctx.stats.addWorkOps(w.ops);
        ctx.mutex.unlock();
        clearError(err);
        return .ok;
    }
    if (g.structtag) |sp| {
        structtag.fillMask(sp, &ctx.tok, &s.tag.?[s.tag_cur], out, &w, &s.tag_pool, &s.states[1], &s.mask_buf.spare, &s.side) catch |e| return maskFail(err, e);
        const ns = elapsedNs(&timer);
        s.stats.recordMaskCall(ns);
        ctx.mutex.lock();
        ctx.stats.recordMaskCall(ns);
        ctx.stats.addWorkOps(w.ops);
        ctx.mutex.unlock();
        clearError(err);
        return .ok;
    }
    const st = curState(s);
    if (ctx.mode != .lazy) {
        // ADR-0007 D2+: a uniform string-content state with enough residual
        // length budget is looked up and stored under its normal form
        // (str counts clamped at the node's minLength), so the string-content
        // positions of a schema share one cache entry per close-ability
        // class instead of one entry per count. The lemma: at R >= r_cap
        // the mask depends on count only through the minLength close gate
        // (see mask.normalizeUniformString). The guard mirrors
        // fillMaskFast's completion-filter precondition.
        var key_state = st;
        if (ctx.fast) |*fd| {
            if ((ctx.tok.byte_complete or !g.finite_literal) and !g.needs_reachability) {
                if (mask.uniformStringResidual(g, st)) |r| {
                    if (r >= fd.r_cap) {
                        // The scratch slot is free during fill_mask (accept
                        // releases it before reuse, destroy releases it
                        // like any state); reusing it avoids a 128 KiB
                        // allocation per fill.
                        const scr = scratchState(s);
                        parser.releaseState(&s.side, scr);
                        mask.normalizeUniformString(g, st, &s.side, scr);
                        key_state = scr;
                    }
                }
            }
        }
        const sh = parser.hashState(key_state, &s.side);
        const key: cache.Key = .{
            .grammar_id = g.id,
            .grammar_hi = g.id_hi,
            .tokenizer_id = ctx.tok.identity,
            .state_hash = sh.hash,
        };
        // Chunk-content blob of the state (ADR-0006 D3). The common case
        // references no chunks at all (hashState reports it from the same
        // state walk, matching the empty-blob condition exactly): serve it
        // from a stack constant instead of the session allocator, so a
        // cache-hit fill of a chunk-free state never allocates and never
        // walks the side store (the per-session first allocation through
        // the accounting allocator costs ~2.7 us in Debug builds and
        // dominated the warm path).
        var blob_empty: [4]u8 = undefined;
        const blob: []const u8 = if (!sh.has_chunks) blk: {
            std.mem.writeInt(u32, &blob_empty, 0, .little);
            break :blk &blob_empty;
        } else blk: {
            const blen = parser.chunkBlobLen(key_state, &s.side);
            s.mask_buf.blob.clearRetainingCapacity();
            s.mask_buf.blob.resize(s.mask_buf.a, blen) catch |e| return maskFail(err, e);
            parser.writeChunkBlob(key_state, &s.side, s.mask_buf.blob.items);
            break :blk s.mask_buf.blob.items;
        };
        ctx.mutex.lock();
        if (ctx.cache.get(key, key_state, blob, out)) {
            // Hit: finish under the same lock instead of a second
            // unlock/lock pair for the stats tail.
            const ns = elapsedNs(&timer);
            s.stats.recordMaskCall(ns);
            ctx.stats.recordMaskCall(ns);
            ctx.stats.addWorkOps(w.ops);
            ctx.mutex.unlock();
            clearError(err);
            return .ok;
        }
        const seen = ctx.cache.bumpSeen(key);
        ctx.mutex.unlock();
        {
            // ADR-0007: try the fast path first (equivalence optimization,
            // bit-for-bit identical); a false result means the state is not
            // covered by a proven lemma and the exact walk runs. The state
            // here may be the string-content normal form (D2+): its mask
            // equals the live state's mask by the same lemma.
            var done = false;
            if (ctx.fast) |*fd| {
                done = mask.fillMaskFast(g, &ctx.tok, key_state, out, &w, &s.mask_buf, &s.side, fd, &ctx.cache, &ctx.mutex, ctx.max_workers) catch |e| return maskFail(err, e);
            }
            if (!done) {
                mask.fillMask(g, &ctx.tok, key_state, out, &w, &s.mask_buf, &s.side) catch |e| return maskFail(err, e);
            }
            const compute_ns = elapsedNs(&timer);
            // Adaptive admission: cache masks of frequently hit or
            // expensive states; cheap one-shot masks stay out of the cache.
            if (ctx.cache.budget > 0) {
                ctx.mutex.lock();
                if (seen >= ctx.adaptive_min_hits or compute_ns >= ctx.adaptive_min_cost_ns) {
                    ctx.cache.put(key, key_state, blob, out);
                } else {
                    ctx.stats.recordAdaptiveSkip();
                }
                ctx.mutex.unlock();
            }
        }
    } else {
        var done = false;
        if (ctx.fast) |*fd| {
            done = mask.fillMaskFast(g, &ctx.tok, st, out, &w, &s.mask_buf, &s.side, fd, &ctx.cache, &ctx.mutex, ctx.max_workers) catch |e| return maskFail(err, e);
        }
        if (!done) {
            mask.fillMask(g, &ctx.tok, st, out, &w, &s.mask_buf, &s.side) catch |e| return maskFail(err, e);
        }
    }
    const ns = elapsedNs(&timer);
    s.stats.recordMaskCall(ns);
    ctx.mutex.lock();
    ctx.stats.recordMaskCall(ns);
    ctx.stats.addWorkOps(w.ops);
    ctx.mutex.unlock();
    clearError(err);
    return .ok;
}

pub export fn blg_abi_version() callconv(.c) u32 {
    return ABI_VERSION;
}

pub export fn blg_context_create(
    config: ?*const ContextConfig,
    tdesc: ?*const TokenizerDesc,
    out_context: ?*?*Context,
    err: ?*ZgError,
) callconv(.c) Status {
    if (badErrBuf(err)) return .invalid_argument;
    const outp = out_context orelse return fail(err, .invalid_argument, "null out_context");
    outp.* = null;

    var mode: Mode = .lazy;
    var max_depth: u32 = DEFAULT_MAX_DEPTH;
    var max_threads: u32 = DEFAULT_MAX_THREADS;
    var memory_limit: u64 = DEFAULT_MEMORY_LIMIT;
    var cache_limit: u64 = DEFAULT_CACHE_LIMIT;
    var session_limit: u64 = DEFAULT_SESSION_LIMIT;
    var schema_limit: u64 = DEFAULT_SCHEMA_LIMIT;
    var work_limit_ops: u64 = 0;
    var adaptive_min_hits: u64 = DEFAULT_ADAPTIVE_MIN_HITS;
    var adaptive_min_cost_ns: u64 = DEFAULT_ADAPTIVE_MIN_COST_NS;
    var precompute_max_states: u64 = DEFAULT_PRECOMPUTE_MAX_STATES;
    var mask_fast_path: u64 = 0;
    var max_workers: u64 = 0;
    var probe_limits: probe.Limits = .{ .max_states = DEFAULT_PROBE_MAX_STATES, .max_work = DEFAULT_PROBE_MAX_WORK };
    if (config) |c| {
        // Tail-grown versioning: a smaller struct_size of a known legacy
        // layout is accepted, the missing fields take defaults.
        if (c.struct_size < LEGACY_CONFIG_SIZE or c.struct_size > @sizeOf(ContextConfig) or c.version != ABI_VERSION)
            return fail(err, .invalid_argument, "bad config struct_size or version");
        if (c.mode > 2)
            return fail(err, .invalid_argument, "unknown mode");
        mode = @fromBackingInt(@intCast(c.mode));
        if (c.max_depth != 0) max_depth = c.max_depth;
        if (c.max_threads_per_state != 0) max_threads = c.max_threads_per_state;
        if (c.memory_limit_bytes != 0) memory_limit = c.memory_limit_bytes;
        if (c.cache_limit_bytes == CACHE_DEFAULT) {
            cache_limit = DEFAULT_CACHE_LIMIT;
        } else {
            cache_limit = c.cache_limit_bytes;
        }
        if (c.session_limit_bytes != 0) session_limit = c.session_limit_bytes;
        if (c.schema_limit_bytes != 0) schema_limit = c.schema_limit_bytes;
        if (cfgU64(c, "work_limit_ops")) |v| work_limit_ops = v;
        if (cfgU64(c, "adaptive_min_hits")) |v| {
            if (v != 0) adaptive_min_hits = v;
        }
        if (cfgU64(c, "adaptive_min_cost_ns")) |v| {
            if (v != 0) adaptive_min_cost_ns = v;
        }
        if (cfgU64(c, "precompute_max_states")) |v| {
            if (v != 0) precompute_max_states = v;
        }
        if (cfgU64(c, "mask_fast_path")) |v| mask_fast_path = v;
        if (cfgU64(c, "max_workers")) |v| max_workers = v;
        if (cfgU64(c, "probe_max_states")) |v| {
            if (v != 0) probe_limits.max_states = v;
        }
        if (cfgU64(c, "probe_max_work")) |v| {
            if (v != 0) probe_limits.max_work = v;
        }
    }
    if (mask_fast_path > MASK_FAST_PATH_OFF)
        return fail(err, .invalid_argument, "unknown mask_fast_path value");
    if (max_workers > MAX_WORKERS_CAP)
        return fail(err, .invalid_argument, "max_workers out of supported range");
    if (max_depth == 0 or max_depth > parser.MAX_DEPTH_CAP)
        return fail(err, .invalid_argument, "max_depth out of supported range");
    if (max_threads == 0 or max_threads > parser.MAX_THREADS_CAP)
        return fail(err, .invalid_argument, "max_threads_per_state out of supported range");
    if (cache_limit > memory_limit)
        return fail(err, .invalid_argument, "cache_limit_bytes exceeds memory_limit_bytes");

    const td = tdesc orelse return fail(err, .invalid_argument, "null tokenizer desc");
    // Tail-grown struct: legacy descriptors (without `flags`) keep working;
    // any other size is malformed.
    const desc_flags_off = @offsetOf(TokenizerDesc, "flags");
    if (td.struct_size != desc_flags_off and td.struct_size != @sizeOf(TokenizerDesc))
        return fail(err, .invalid_argument, "bad tokenizer struct_size");
    const tok_flags: u32 = if (td.struct_size == @sizeOf(TokenizerDesc)) td.flags else 0;
    if (td.entry_count > 0 and td.entries == null)
        return fail(err, .invalid_argument, "null tokenizer entries");
    if (td.blob_len > 0 and td.blob == null)
        return fail(err, .invalid_argument, "null tokenizer blob");
    if (td.eos_count > 0 and td.eos_ids == null)
        return fail(err, .invalid_argument, "null eos_ids");
    if (td.special_count > 0 and td.special_ids == null)
        return fail(err, .invalid_argument, "null special_ids");

    const root_alloc = root_allocator;
    const ctx = root_alloc.create(Context) catch
        return fail(err, .resource_limit, "context allocation failed");
    var tok_ready = false;
    var cache_ready = false;
    var ctx_charged = false;
    defer {
        if (outp.* == null) {
            if (cache_ready) ctx.cache.deinit();
            if (tok_ready) ctx.tok.deinit();
            if (ctx_charged) ctx.accounting.freeExternal(.temp, @sizeOf(Context));
            root_alloc.destroy(ctx);
        }
    }

    ctx.mutex = .{};
    ctx.accounting = alloc.Accounting.init(root_alloc, memory_limit);
    // The Context struct itself is kernel memory and must count towards
    // memory_limit; it is charged under the temp category.
    if (!ctx.accounting.chargeExternal(.temp, @sizeOf(Context)))
        return fail(err, .resource_limit, "context allocation exceeds memory limit");
    ctx_charged = true;
    ctx.mode = mode;
    ctx.max_depth = max_depth;
    ctx.max_threads = @intCast(max_threads);
    ctx.session_limit = session_limit;
    ctx.schema_limit = schema_limit;
    ctx.work_limit_ops = work_limit_ops;
    ctx.adaptive_min_hits = adaptive_min_hits;
    ctx.adaptive_min_cost_ns = adaptive_min_cost_ns;
    ctx.precompute_max_states = precompute_max_states;
    ctx.probe_limits = probe_limits;
    ctx.max_workers = if (max_workers == 0) 1 else @intCast(max_workers);
    ctx.fast = null;
    ctx.cancel_flag = null;
    ctx.live_grammars = 0;
    ctx.live_sessions = 0;
    ctx.external_grammar_refs = 0;
    ctx.active_calls = 0;
    ctx.stats = .{};
    const entries = td.entries.?[0..td.entry_count];
    if (td.blob_len == 0) {
        for (entries) |e| {
            if (e.length != 0) return fail(err, .invalid_argument, "token entry outside blob");
        }
    }
    if (td.blob) |blob| {
        _ = blob;
        for (entries) |e| {
            if (e.offset > td.blob_len or e.length > td.blob_len - e.offset)
                return fail(err, .invalid_argument, "token entry outside blob");
        }
    }

    const tmp = ctx.accounting.allocator(.temp);
    const zig_entries = tmp.alloc(tokenizer.Entry, td.entry_count) catch
        return fail(err, .resource_limit, "tokenizer staging allocation failed");
    defer tmp.free(zig_entries);
    const blob_bytes: []const u8 = if (td.blob) |b| b[0..td.blob_len] else &.{};
    for (entries, 0..) |e, i| {
        zig_entries[i] = .{
            .id = e.id,
            .bytes = blob_bytes[@intCast(e.offset)..@intCast(e.offset + e.length)],
        };
    }
    const eos: []const u32 = if (td.eos_ids) |p| p[0..td.eos_count] else &.{};
    const special: []const u32 = if (td.special_ids) |p| p[0..td.special_count] else &.{};

    var timer = startTimer();
    ctx.tok = tokenizer.Tokenizer.create(
        ctx.accounting.allocator(.tokenizer),
        td.vocab_size,
        zig_entries,
        eos,
        special,
    ) catch |e| switch (e) {
        error.UnsupportedTokenizer => return fail(err, .unsupported_tokenizer, "unsupported tokenizer table"),
        error.OutOfMemory => return fail(err, .resource_limit, "tokenizer memory limit exceeded"),
    };
    // The decoder mode is part of the tokenizer identity: a strip decoder
    // accepts one extra leading space in the byte stream, so two contexts
    // with and without the flag must never share cached grammar.
    ctx.tok.strip_lead_space = (tok_flags & 1) != 0;
    if (ctx.tok.strip_lead_space)
        ctx.tok.identity = grammar.fnv1a64Update(ctx.tok.identity, "strip-lead-space");
    tok_ready = true;
    ctx.stats.addTokenizerPrepareNs(elapsedNs(&timer));

    const cache_budget: u64 = if (mode == .lazy) 0 else cache_limit;
    // One layered allocator for the cache: the same full cost (header
    // included) is charged to the cache budget and to the .cache category,
    // so the accounting can never exceed the hard limit.
    ctx.cache = cache.Cache.initLim(alloc.Limited.initAccounting(
        ctx.accounting.parent,
        cache_budget,
        &ctx.accounting,
        .cache,
    ));
    cache_ready = true;
    // Compile artifacts: a quarter of the cache budget,
    // charged to the grammar category; with cache_limit_bytes=0 (and in
    // lazy) the cache is off.
    const artifact_budget: usize = if (cache_budget == 0)
        0
    else
        @intCast(@min(cache_budget / 4, std.math.maxInt(usize)));
    ctx.grammar_cache = GrammarArtifactCache.init(artifact_budget);

    // ADR-0007: build the fast-path tables once per tokenizer (charged to
    // the tokenizer category). The kill switch: mask_fast_path == 2 in the
    // config, or BLG_MASK_FAST_PATH=0 in the environment. A build failure
    // (budget pressure) simply leaves the fast path off; the exact walk is
    // always a correct fallback.
    if (mask_fast_path != MASK_FAST_PATH_OFF and !envDisablesFastPath()) {
        ctx.fast = precompute.FastData.build(ctx.accounting.allocator(.tokenizer), &ctx.tok) catch null;
    }

    outp.* = ctx;
    clearError(err);
    return .ok;
}

pub export fn blg_context_destroy(ctx_opt: ?*Context) callconv(.c) Status {
    const ctx = ctx_opt orelse return .invalid_argument;
    ctx.mutex.lock();
    // Busy = at least one external reference (user handle or in-flight
    // call) or a live session. Artifact cache references are not external:
    // destroy may drop them itself. Comparing the number of grammar objects
    // with the number of cache refs is wrong: one
    // object with refcount = cache + user gave a false equality.
    const busy = ctx.external_grammar_refs != 0 or ctx.live_sessions != 0 or
        ctx.active_calls != 0;
    ctx.mutex.unlock();
    if (busy) return .busy;
    artifactDeinit(ctx);
    if (ctx.fast) |*fd| fd.deinit();
    ctx.cache.deinit();
    ctx.tok.deinit();
    ctx.accounting.freeExternal(.temp, @sizeOf(Context));
    root_allocator.destroy(ctx);
    return .ok;
}

/// Test hook (compiled only in test builds): when set, blg_compile pauses
/// right after registering the in-flight call (active_calls) and before any
/// work, until the release flag is set. Lets a test deterministically
/// exercise the active_calls branch of destroy during an UNFINISHED
/// compile, not just while another thread holds a completed handle.
const CompileGate = struct {
    entered: std.atomic.Value(bool) = std.atomic.Value(bool).init(false),
    release: std.atomic.Value(bool) = std.atomic.Value(bool).init(false),
};

var compile_gate: ?*CompileGate = null;

pub export fn blg_compile(
    ctx_opt: ?*Context,
    request: ?*const CompileRequest,
    out_grammar: ?*?*GrammarHandle,
    err: ?*ZgError,
) callconv(.c) Status {
    if (badErrBuf(err)) return .invalid_argument;
    const ctx = ctx_opt orelse return fail(err, .invalid_argument, "null context");
    const req = request orelse return fail(err, .invalid_argument, "null request");
    const outp = out_grammar orelse return fail(err, .invalid_argument, "null out_grammar");
    outp.* = null;
    // In-flight call: destroy must return BUSY while the compile holds the
    // context.
    ctx.mutex.lock();
    ctx.active_calls += 1;
    ctx.mutex.unlock();
    defer {
        ctx.mutex.lock();
        ctx.active_calls -= 1;
        ctx.mutex.unlock();
    }
    if (@import("builtin").is_test) {
        if (compile_gate) |gate| {
            gate.entered.store(true, .release);
            while (!gate.release.load(.acquire)) std.Thread.yield() catch {};
        }
    }
    const legacy_req = req.struct_size == LEGACY_COMPILE_REQUEST_SIZE;
    if (!legacy_req and req.struct_size != REGISTRY_COMPILE_REQUEST_SIZE and req.struct_size != @sizeOf(CompileRequest))
        return fail(err, .invalid_argument, "bad compile request struct_size");
    // The tails must not be read from a shorter caller buffer: snapshot the
    // scalars before any validation.
    var registry_ptr: ?[*]const u8 = null;
    var registry_len: usize = 0;
    if (!legacy_req) {
        registry_ptr = req.registry_data;
        registry_len = req.registry_data_len;
    }
    const flags: u32 = if (req.struct_size == @sizeOf(CompileRequest)) req.flags else 0;
    if (flags & ~(COMPILE_PROBE | COMPILE_PROBE_STRICT) != 0)
        return fail(err, .invalid_argument, "unknown compile request flags");
    if (req.kind > 3)
        return fail(err, .invalid_argument, "unknown constraint kind");
    const kind: ConstraintKind = @fromBackingInt(@intCast(req.kind));
    if (kind == .regex and req.profile != null)
        return fail(err, .invalid_argument, "profile must be NULL for a regex constraint");
    if (kind == .regex and flags != 0)
        return fail(err, .unsupported_feature, "the probe applies to JSON Schema constraints");
    if (kind == .structural_tag and req.profile != null)
        return fail(err, .invalid_argument, "profile must be NULL for a structural-tag constraint");
    if (kind == .structural_tag and flags != 0)
        return fail(err, .unsupported_feature, "the probe applies to JSON Schema constraints");
    var profile: schema.Profile = .canonical_v1;
    if (req.profile) |p| {
        const ps = std.mem.span(p);
        if (std.mem.eql(u8, ps, "canonical-v1")) {
            profile = .canonical_v1;
        } else if (std.mem.eql(u8, ps, "spec-v1")) {
            profile = .spec_v1;
        } else {
            return fail(err, .unsupported_feature, "unsupported profile");
        }
    }
    if (req.data_len > 0 and req.data == null)
        return fail(err, .invalid_argument, "null schema data");
    if (req.data_len > ctx.schema_limit)
        return fail(err, .resource_limit, "schema exceeds schema_limit_bytes");
    const bytes: []const u8 = if (req.data) |d| d[0..req.data_len] else &.{};
    if (registry_len > 0 and registry_ptr == null)
        return fail(err, .invalid_argument, "null registry data");
    if (registry_len > ctx.schema_limit)
        return fail(err, .resource_limit, "registry exceeds schema_limit_bytes");
    const registry_bytes: []const u8 = if (registry_ptr) |r| r[0..registry_len] else &.{};
    if (registry_bytes.len > 0 and (profile != .spec_v1 or kind != .json_schema))
        return fail(err, .unsupported_feature, "a registry snapshot requires the spec-v1 JSON Schema profile");
    const profile_key: u8 = switch (profile) {
        .canonical_v1 => if (req.profile == null) 0 else 1,
        .spec_v1 => 2,
    };

    var diag: schema.Diagnostic = .{};
    ctx.mutex.lock();
    const cancel = ctx.cancel_flag;
    ctx.mutex.unlock();
    var w = work_mod.Work.init(ctx.work_limit_ops, cancel);
    // Cancellation is checked on a cache hit too (as in masks).
    w.charge(0) catch |e| switch (e) {
        error.Cancelled => {
            recordCtxErr(ctx, .cancelled);
            return fail(err, .cancelled, "cancelled");
        },
        error.ResourceLimit => {
            recordCtxErr(ctx, .resource_limit);
            return fail(err, .resource_limit, "work limit exceeded");
        },
    };
    var timer = startTimer();
    // Cache of immutable artifacts: exact bytes are compared on a hash
    // match, so a collision cannot return a foreign grammar.
    const artifact_hash = artifactHash(kind, profile_key, bytes, registry_bytes);
    if (artifactLookup(ctx, kind, profile_key, artifact_hash, bytes, registry_bytes)) |gh_hit| {
        const ns = elapsedNs(&timer);
        ctx.mutex.lock();
        ctx.stats.addCompileNs(ns);
        ctx.stats.addWorkOps(w.ops);
        ctx.external_grammar_refs += 1;
        ctx.mutex.unlock();
        if (flags != 0) {
            const rc = probeHandle(ctx, gh_hit, flags, kind, profile, bytes, registry_bytes, cancel, err);
            if (rc != .ok) {
                dropExternalRef(gh_hit);
                recordCtxErr(ctx, rc);
                return rc;
            }
        }
        outp.* = gh_hit;
        clearError(err);
        return .ok;
    }
    const ga = ctx.accounting.allocator(.grammar);
    // The handle carries the grammar meter: the compile arena allocates
    // through it, so the actual artifact cost is known exactly.
    const h = ga.create(GrammarHandle) catch {
        recordCtxErr(ctx, .resource_limit);
        return fail(err, .resource_limit, "grammar handle allocation failed");
    };
    h.* = .{
        .ctx = ctx,
        .g = undefined,
        .refcount = std.atomic.Value(u32).init(1),
        .meter = .{ .child = ga },
    };
    const g = compileGrammar(ctx, kind, bytes, registry_bytes, &diag, &w, h.meter.allocator(), profile, flags != 0) catch |e| {
        ga.destroy(h);
        const ns = elapsedNs(&timer);
        ctx.mutex.lock();
        ctx.stats.addCompileNs(ns);
        ctx.stats.addWorkOps(w.ops);
        ctx.mutex.unlock();
        const rc = mapCompileError(e, &diag, err);
        recordCtxErr(ctx, rc);
        return rc;
    };
    h.g = g;
    const ns = elapsedNs(&timer);

    ctx.mutex.lock();
    ctx.live_grammars += 1;
    ctx.stats.addCompileNs(ns);
    ctx.mutex.unlock();

    // Experimental precompute: bounded BFS mask warm-up. The
    // warm-up has its OWN work account: it must not consume the
    // call's work_limit_ops; its bounds are precompute_max_states, the temp
    // memory budget and the cancel flag. Only cancel propagates.
    if (ctx.mode == .precompute and ctx.cache.budget > 0 and kind != .regex and kind != .structural_tag) {
        var warm_w = work_mod.Work.init(0, cancel);
        const n = precompute.run(
            ctx.accounting.allocator(.temp),
            &h.g,
            &ctx.tok,
            &ctx.cache,
            &ctx.mutex,
            ctx.max_threads,
            ctx.precompute_max_states,
            &warm_w,
        ) catch |e| switch (e) {
            error.Cancelled => {
                releaseGrammarRef(h);
                ctx.mutex.lock();
                ctx.stats.addWorkOps(w.ops);
                ctx.mutex.unlock();
                recordCtxErr(ctx, .cancelled);
                return fail(err, .cancelled, "operation cancelled");
            },
        };
        ctx.mutex.lock();
        ctx.stats.addPrecomputeStates(n);
        ctx.mutex.unlock();
    }

    ctx.mutex.lock();
    ctx.stats.addWorkOps(w.ops);
    ctx.external_grammar_refs += 1;
    ctx.mutex.unlock();
    // The artifact enters the cache only after a successful warm-up: a
    // cancelled or failed compile must not leave an entry behind.
    artifactInsert(ctx, kind, profile_key, artifact_hash, bytes, registry_bytes, h);
    if (flags != 0) {
        const rc = probeHandle(ctx, h, flags, kind, profile, bytes, registry_bytes, cancel, err);
        if (rc != .ok) {
            dropExternalRef(h);
            recordCtxErr(ctx, rc);
            return rc;
        }
    }
    outp.* = h;
    clearError(err);
    return .ok;
}

/// Undoes the external reference a compile took before failing late.
fn dropExternalRef(h: *GrammarHandle) void {
    const ctx = h.ctx;
    ctx.mutex.lock();
    ctx.external_grammar_refs -= 1;
    ctx.mutex.unlock();
    releaseGrammarRef(h);
}

/// ADR-0012: runs the probe on the artifact unless it already holds a
/// verdict for the context's probe budgets. `found` fails the compile with
/// RESOURCE_LIMIT, the witness prefix in the message and the subschema
/// pointer; `inconclusive` fails it only under BLG_COMPILE_PROBE_STRICT.
fn probeHandle(ctx: *Context, h: *GrammarHandle, flags: u32, kind: ConstraintKind, profile: schema.Profile, bytes: []const u8, registry: []const u8, cancel: ?*align(1) const u8, err: ?*ZgError) Status {
    ctx.mutex.lock();
    var status = h.probe_status;
    const known = status != .not_run and std.meta.eql(h.probe_limits, ctx.probe_limits);
    ctx.mutex.unlock();
    if (!known) {
        const tmp = ctx.accounting.allocator(.temp);
        const out = probe.run(tmp, tmp, &h.g, &ctx.tok, ctx.max_threads, ctx.probe_limits, cancel) catch |e| switch (e) {
            error.Cancelled => return fail(err, .cancelled, "operation cancelled"),
            error.OutOfMemory => return fail(err, .resource_limit, "out of memory during probe"),
        };
        ctx.mutex.lock();
        ctx.stats.probe_runs += 1;
        ctx.mutex.unlock();
        switch (out) {
            .found => |f| {
                defer tmp.free(f.prefix);
                var ptr_buf: [JSON_POINTER_CAP]u8 = undefined;
                const ptr = probePointer(ctx, h, f.node, kind, profile, bytes, registry, &ptr_buf);
                var msg_buf: [ERROR_MESSAGE_CAP]u8 = undefined;
                writeError(err, .resource_limit, NO_OFFSET, probeMessage(&msg_buf, f.prefix), ptr);
                return .resource_limit;
            },
            .inconclusive => status = .inconclusive,
            .proven => status = .proven,
        }
        ctx.mutex.lock();
        h.probe_status = status;
        h.probe_limits = ctx.probe_limits;
        ctx.mutex.unlock();
    }
    if (flags & COMPILE_PROBE_STRICT != 0 and status == .inconclusive)
        return fail(err, .resource_limit, "probe inconclusive");
    return .ok;
}

/// "probe found ... prefix: <bytes>", non-printable bytes as \xNN,
/// truncated to the message capacity.
fn probeMessage(buf: *[ERROR_MESSAGE_CAP]u8, prefix: []const u8) []const u8 {
    const head = "probe found a state whose mask is undecided after the prefix: ";
    @memcpy(buf[0..head.len], head);
    var n: usize = head.len;
    for (prefix) |b| {
        const printable = b >= 0x20 and b < 0x7f and b != '\\';
        const need: usize = if (printable) 1 else 4;
        if (n + need > ERROR_MESSAGE_CAP - 1) break;
        if (printable) {
            buf[n] = b;
        } else {
            _ = std.fmt.bufPrint(buf[n..][0..4], "\\x{x:0>2}", .{b}) catch unreachable;
        }
        n += need;
    }
    return buf[0..n];
}

/// Pointer of the probe's undecided node: the nearest preceding node with
/// a recorded pointer. A cached artifact compiled without pointers is
/// compiled once more to recover them (compiles are deterministic).
fn probePointer(ctx: *Context, h: *const GrammarHandle, node: ?grammar.NodeId, kind: ConstraintKind, profile: schema.Profile, bytes: []const u8, registry: []const u8, out: *[JSON_POINTER_CAP]u8) []const u8 {
    const id = node orelse return "";
    if (kind != .json_schema) return "";
    var tmp_g: ?grammar.Grammar = null;
    defer if (tmp_g) |*tg| tg.deinit();
    var ptrs = h.g.node_ptrs;
    if (ptrs.len == 0) {
        var diag: schema.Diagnostic = .{};
        var w = work_mod.Work.init(0, null);
        tmp_g = schema.compileWith(ctx.accounting.allocator(.temp), bytes, ctx.max_depth, &diag, &w, ctx.tok.strip_lead_space, profile, registry, .{ .record_pointers = true }) catch return "";
        ptrs = tmp_g.?.node_ptrs;
    }
    if (ptrs.len == 0) return "";
    var i: usize = @min(id, ptrs.len - 1);
    while (true) : (i -= 1) {
        if (ptrs[i].len != 0) {
            const n = @min(ptrs[i].len, out.len - 1);
            @memcpy(out[0..n], ptrs[i][0..n]);
            return out[0..n];
        }
        if (i == 0) return "";
    }
}

/// ADR-0012 probe status of the artifact behind a grammar handle:
/// 0 not run, 1 inconclusive, 2 proven (a found counterexample fails the
/// compile, so no handle carries it).
pub export fn blg_grammar_probe_status(gh_opt: ?*const GrammarHandle, out_status: ?*u32) callconv(.c) Status {
    const gh = gh_opt orelse return .invalid_argument;
    const o = out_status orelse return .invalid_argument;
    gh.ctx.mutex.lock();
    defer gh.ctx.mutex.unlock();
    o.* = @backingInt(gh.probe_status);
    return .ok;
}

pub export fn blg_grammar_release(gh_opt: ?*GrammarHandle) callconv(.c) void {
    const gh = gh_opt orelse return;
    const ctx = gh.ctx;
    ctx.mutex.lock();
    // External (user) reference: sessions hold the handle through their own
    // refcount and are not counted here.
    if (ctx.external_grammar_refs > 0) ctx.external_grammar_refs -= 1;
    releaseGrammarRefLocked(ctx, gh);
    ctx.mutex.unlock();
}

/// Resets the compile artifact cache without touching live user handles
/// and sessions: the cache drops its references, memory returns to the
/// accounting. Needed for the testable "memory returns" invariant and for
/// long-lived contexts under memory pressure.
pub export fn blg_context_reset_cache(ctx_opt: ?*Context) callconv(.c) Status {
    const ctx = ctx_opt orelse return .invalid_argument;
    ctx.mutex.lock();
    ctx.active_calls += 1;
    ctx.mutex.unlock();
    defer {
        ctx.mutex.lock();
        ctx.active_calls -= 1;
        ctx.mutex.unlock();
    }
    artifactReset(ctx);
    return .ok;
}

pub export fn blg_session_create(
    ctx_opt: ?*Context,
    gh_opt: ?*GrammarHandle,
    out_session: ?*?*Session,
    err: ?*ZgError,
) callconv(.c) Status {
    return sessionCreate(ctx_opt, gh_opt, 0, out_session, err);
}

pub export fn blg_session_create_ex(
    ctx_opt: ?*Context,
    gh_opt: ?*GrammarHandle,
    config: ?*const SessionConfig,
    out_session: ?*?*Session,
    err: ?*ZgError,
) callconv(.c) Status {
    if (badErrBuf(err)) return .invalid_argument;
    var depth: u32 = 0;
    if (config) |c| {
        if (c.struct_size != @sizeOf(SessionConfig))
            return fail(err, .invalid_argument, "blg_session_config.struct_size mismatch");
        if (c.max_rollback_tokens > MAX_ROLLBACK_TOKENS)
            return fail(err, .invalid_argument, "max_rollback_tokens above BLG_MAX_ROLLBACK_TOKENS");
        depth = c.max_rollback_tokens;
    }
    return sessionCreate(ctx_opt, gh_opt, depth, out_session, err);
}

fn sessionCreate(
    ctx_opt: ?*Context,
    gh_opt: ?*GrammarHandle,
    max_rollback_tokens: u32,
    out_session: ?*?*Session,
    err: ?*ZgError,
) Status {
    if (badErrBuf(err)) return .invalid_argument;
    const ctx = ctx_opt orelse return fail(err, .invalid_argument, "null context");
    const gh = gh_opt orelse return fail(err, .invalid_argument, "null grammar");
    const outp = out_session orelse return fail(err, .invalid_argument, "null out_session");
    outp.* = null;
    if (gh.ctx != ctx)
        return fail(err, .invalid_argument, "grammar belongs to another context");

    var account = alloc.SessionAccount.init(&ctx.accounting, ctx.session_limit);
    const sa = account.allocator(.session);
    const s = sa.create(Session) catch
        return fail(err, .resource_limit, "session allocation failed");
    s.* = .{
        .ctx = ctx,
        .gh = gh,
        .account = account,
        .states = .{ undefined, undefined },
        .cur = 0,
        .status = .active,
        .stats = .{},
        .mask_buf = undefined,
        .side = undefined,
        .history = .{ .cap = @intCast(max_rollback_tokens) },
        .rx = .{},
        .tag = null,
        .tag_cur = 0,
        .tag_pool = .{},
    };
    // The whole mask scratch goes to the context temp budget: at
    // MAX_THREADS_CAP 128 one inline parser.State is ~256 KiB and the
    // trie-walk pool holds max_stack of them (~8 MiB for the byte
    // tokenizer), which alone would exhaust the 8 MiB session budget
    // before any search chunk fits (measured on maskbench o77317).
    s.mask_buf = mask.MaskBuf.init(ctx.accounting.allocator(.temp), ctx.accounting.allocator(.temp));
    s.tag_pool = structtag.Pool.init(ctx.accounting.allocator(.temp));
    // The allocator must wrap the heap-resident account: `sa` above points
    // at the stack local, which dies when this function returns (release
    // builds reuse the slot; the first side allocation then reads garbage).
    s.side = parser.Side.init(s.account.allocator(.session));
    s.states[1].n = 0;
    if (gh.g.regex) |rp| {
        s.states[0].n = 0;
        s.rx = regex.initial(rp);
    } else if (gh.g.structtag) |sp| {
        // Three slot states: the current one and two chain scratches; the
        // current slot is never overwritten while a token is fed, so a
        // rejected token leaves the session untouched (ADR-0016).
        s.states[0].n = 0;
        const slots = sa.alloc(structtag.State, 3) catch {
            parser.releaseState(&s.side, &s.states[0]);
            parser.releaseState(&s.side, &s.states[1]);
            s.side.deinit();
            sa.destroy(s);
            return fail(err, .resource_limit, "session allocation failed");
        };
        slots[0] = structtag.initState(sp, ctx.max_threads);
        slots[1].json.n = 0;
        slots[2].json.n = 0;
        s.tag = slots.ptr;
        s.tag_cur = 0;
    } else {
        s.states[0] = parser.initState(&gh.g, ctx.max_threads, &s.side) catch {
            s.side.deinit();
            sa.destroy(s);
            return fail(err, .resource_limit, "parser state init limit exceeded");
        };
    }
    _ = gh.refcount.fetchAdd(1, .monotonic);
    ctx.mutex.lock();
    ctx.live_sessions += 1;
    ctx.mutex.unlock();
    outp.* = s;
    clearError(err);
    return .ok;
}

pub export fn blg_session_destroy(s_opt: ?*Session) callconv(.c) void {
    const s = s_opt orelse return;
    const ctx = s.ctx;
    releaseGrammarRef(s.gh);
    parser.releaseState(&s.side, &s.states[0]);
    parser.releaseState(&s.side, &s.states[1]);
    if (s.tag) |slots| {
        for (slots[0..3]) |*st| parser.releaseState(&s.side, &st.json);
        s.account.allocator(.session).free(slots[0..3]);
        s.tag = null;
    }
    s.tag_pool.deinit(&s.side);
    s.history.deinit(s.account.allocator(.session), &s.side);
    // mask_buf's completion-search scratch states may hold chunk references
    // into the side store (spec-v1 open objects/repeat taps): it must be
    // torn down while the store is still alive.
    s.mask_buf.deinit();
    s.side.deinit();
    const sa = s.account.allocator(.session);
    sa.destroy(s);
    ctx.mutex.lock();
    ctx.live_sessions -= 1;
    ctx.mutex.unlock();
}

pub export fn blg_fill_mask(
    s_opt: ?*Session,
    mask_buf: ?[*]align(1) u32,
    mask_words: usize,
    err: ?*ZgError,
) callconv(.c) Status {
    if (badErrBuf(err)) return .invalid_argument;
    const s = s_opt orelse return fail(err, .invalid_argument, "null session");
    const m = mask_buf orelse return fail(err, .invalid_argument, "null mask");
    return fillMaskImpl(s, m, mask_words, err);
}

pub export fn blg_fill_masks_batch(
    sessions: ?[*]const ?*Session,
    masks: ?[*]const ?[*]align(1) u32,
    mask_words_each: usize,
    statuses: ?[*]c_int,
    count: usize,
    err: ?*ZgError,
) callconv(.c) Status {
    if (badErrBuf(err)) return .invalid_argument;
    if (count == 0) {
        clearError(err);
        return .ok;
    }
    if (sessions == null or masks == null or statuses == null)
        return fail(err, .invalid_argument, "null batch pointer");
    const ss = sessions.?;
    const ms = masks.?;
    const sts = statuses.?;
    var first: Status = .ok;
    var first_diag: ZgError = undefined;
    for (0..count) |i| {
        var row_err: ZgError = .{
            .struct_size = @sizeOf(ZgError),
            .code = 0,
            .schema_offset = NO_OFFSET,
            .message = undefined,
            .json_pointer = undefined,
        };
        row_err.message[0] = 0;
        row_err.json_pointer[0] = 0;
        const rc: Status = blk: {
            const s = ss[i] orelse break :blk fail(&row_err, .invalid_argument, "null session in batch");
            const m = ms[i] orelse break :blk fail(&row_err, .invalid_argument, "null mask in batch");
            break :blk fillMaskImpl(s, m, mask_words_each, &row_err);
        };
        sts[i] = @backingInt(rc);
        if (rc != .ok and first == .ok) {
            first = rc;
            first_diag = row_err;
        }
    }
    if (first == .ok) {
        clearError(err);
    } else if (errBufValid(err)) {
        copyError(err.?, &first_diag);
    }
    return first;
}

pub export fn blg_accept_token(
    s_opt: ?*Session,
    token_id: u32,
    err: ?*ZgError,
) callconv(.c) Status {
    if (badErrBuf(err)) return .invalid_argument;
    const s = s_opt orelse return fail(err, .invalid_argument, "null session");
    const rc = acceptTokenInner(s, token_id, err);
    if (rc != .ok) recordCtxErr(s.ctx, rc);
    return rc;
}

fn acceptTokenInner(s: *Session, token_id: u32, err: ?*ZgError) Status {
    if (s.status != .active)
        return fail(err, .wrong_state, "session not active");
    const ctx = s.ctx;
    const tok = &ctx.tok;
    if (token_id >= tok.vocab_size)
        return fail(err, .invalid_token, "token id out of range");
    if (tok.is_special[token_id])
        return fail(err, .invalid_token, "special token not allowed in document");
    if (s.gh.g.regex) |rp| return acceptRegex(s, rp, token_id, err);
    if (s.gh.g.structtag) |sp| return acceptStructTag(s, sp, token_id, err);

    // The certificate cache is optional memory and lives for a mask fill:
    // the feed below copies name content and can need the same budget, and
    // nothing before that allocation would release the cache.
    s.mask_buf.completion.releaseOptional();
    var timer = startTimer();
    if (tok.is_eos[token_id]) {
        if (!parser.canEnd(&s.gh.g, curState(s)))
            return fail(err, .invalid_token, "EOS not allowed before document end");
        if (s.history.cap != 0 and !recordHistory(s))
            return fail(err, .resource_limit, "rollback history exceeds the session budget");
    } else {
        parser.releaseState(&s.side, scratchState(s));
        parser.feedBytes(&s.gh.g, &s.side, curState(s), tok.bytes[token_id], scratchState(s), &s.mask_buf.spare) catch |e| switch (e) {
            error.Parse => return fail(err, .invalid_token, "token rejected by grammar"),
            error.ResourceLimit => return fail(err, .resource_limit, "parser thread limit exceeded"),
            error.OutOfMemory => return fail(err, .resource_limit, "out of memory"),
        };
        // An accepted token must keep a completed answer
        // reachable - exactly the mask bit. Only the completion-filtered
        // path (finite literal grammar without full byte coverage, or a
        // grammar with deferred value verdicts - ADR-0005) needs the
        // extra check; byte-legal already implies alive otherwise.
        if ((!tok.byte_complete and s.gh.g.finite_literal) or s.gh.g.needs_reachability) {
            var w = work_mod.Work{};
            // Fresh search budget per accept (the fill's stamps persist in
            // the memo, so a token the mask just proved dead is refused
            // here without a re-search). A budget-exhausted search is
            // UNKNOWN, not alive (ADR-0005 D3): refuse with RESOURCE_LIMIT,
            // exactly as the mask call would have failed - admitting an
            // unproven token can dead-end the session irrecoverably.
            s.mask_buf.completion.fill_budget = complete.fillBudget();
            const alive = complete.stateAlive(&s.gh.g, tok, scratchState(s), &s.mask_buf.completion, &w, &s.side) catch |e| switch (e) {
                error.ResourceLimit => return fail(err, .resource_limit, "completion check limit exceeded"),
                error.OutOfMemory => return fail(err, .resource_limit, "completion check limit exceeded"),
                error.Cancelled => return fail(err, .cancelled, "cancelled"),
            };
            if (!alive) return fail(err, .invalid_token, "token would lead to a dead end");
        }
        if (s.history.cap != 0 and !recordHistory(s))
            return fail(err, .resource_limit, "rollback history exceeds the session budget");
        s.cur ^= 1;
    }
    const ns = elapsedNs(&timer);
    s.stats.recordAccept(ns);
    ctx.mutex.lock();
    ctx.stats.recordAccept(ns);
    ctx.mutex.unlock();
    clearError(err);
    return .ok;
}

fn acceptRegex(s: *Session, rp: *const regex.Program, token_id: u32, err: ?*ZgError) Status {
    const ctx = s.ctx;
    var timer = startTimer();
    var next = s.rx;
    if (ctx.tok.is_eos[token_id]) {
        if (!regex.canEnd(rp, s.rx))
            return fail(err, .invalid_token, "EOS not allowed before the text matches");
    } else {
        next = regex.feed(rp, s.rx, ctx.tok.bytes[token_id]) orelse
            return fail(err, .invalid_token, "token rejected by the regex");
    }
    if (s.history.cap != 0 and !recordHistory(s))
        return fail(err, .resource_limit, "rollback history exceeds the session budget");
    s.rx = next;
    const ns = elapsedNs(&timer);
    s.stats.recordAccept(ns);
    ctx.mutex.lock();
    ctx.stats.recordAccept(ns);
    ctx.mutex.unlock();
    clearError(err);
    return .ok;
}

/// Feeds one token through the structural-tag chain (ADR-0016): the
/// current slot is never overwritten, so a rejected token leaves the
/// session byte-identical; the fed state becomes current only after the
/// whole token succeeded and the pre-accept state was recorded for
/// rollback (ADR-0014).
fn acceptStructTag(s: *Session, sp: *const structtag.Program, token_id: u32, err: ?*ZgError) Status {
    const ctx = s.ctx;
    var timer = startTimer();
    const slots = s.tag.?;
    // Same rule as the JSON accept: the tag feed allocates, the certificate
    // cache is optional, so it is given back before the feed.
    s.tag_pool.completion.releaseOptional();
    if (ctx.tok.is_eos[token_id]) {
        if (!structtag.canEnd(&slots[s.tag_cur]))
            return fail(err, .invalid_token, "EOS not allowed before the structural tag can end");
        if (s.history.cap != 0 and !recordHistory(s))
            return fail(err, .resource_limit, "rollback history exceeds the session budget");
    } else {
        const orig = s.tag_cur;
        const o1: u8 = (orig + 1) % 3;
        const o2: u8 = (orig + 2) % 3;
        var src: u8 = orig;
        var src_is_orig = true;
        var dst: u8 = o1;
        for (ctx.tok.bytes[token_id]) |b| {
            structtag.feedByteInto(sp, &s.side, &slots[src], b, &s.states[1], &s.mask_buf.spare, &slots[dst]) catch |e| switch (e) {
                error.Parse => {
                    if (!src_is_orig) parser.releaseState(&s.side, &slots[src].json);
                    return fail(err, .invalid_token, "token rejected by the structural tag");
                },
                error.ResourceLimit, error.OutOfMemory => {
                    if (!src_is_orig) parser.releaseState(&s.side, &slots[src].json);
                    return fail(err, .resource_limit, "structural-tag feed limit exceeded");
                },
            };
            if (!src_is_orig) parser.releaseState(&s.side, &slots[src].json);
            src = dst;
            src_is_orig = false;
            dst = if (dst == o1) o2 else o1;
        }
        if (!src_is_orig) {
            // Completion filter (ADR-0005), mirroring the standalone JSON
            // accept: a token the tag content can never finish from (a
            // dead-end prefix like `-` under `minimum: 10`) is refused
            // before any observable change.
            {
                var w = work_mod.Work{};
                s.tag_pool.completion.fill_budget = complete.fillBudget();
                const alive = structtag.stateAlive(sp, &ctx.tok, &slots[src], &s.tag_pool, &w, &s.side) catch |e| switch (e) {
                    error.ResourceLimit, error.OutOfMemory => {
                        parser.releaseState(&s.side, &slots[src].json);
                        return fail(err, .resource_limit, "completion check limit exceeded");
                    },
                    error.Cancelled => {
                        parser.releaseState(&s.side, &slots[src].json);
                        return fail(err, .cancelled, "cancelled");
                    },
                };
                if (!alive) {
                    parser.releaseState(&s.side, &slots[src].json);
                    return fail(err, .invalid_token, "token would lead to a dead end");
                }
            }
            // The current slot still holds the pre-accept state: record it
            // before the commit, then make the fed state current.
            if (s.history.cap != 0 and !recordHistory(s)) {
                parser.releaseState(&s.side, &slots[src].json);
                return fail(err, .resource_limit, "rollback history exceeds the session budget");
            }
            parser.releaseState(&s.side, &slots[orig].json);
            s.tag_cur = src;
        }
    }
    const ns = elapsedNs(&timer);
    s.stats.recordAccept(ns);
    ctx.mutex.lock();
    ctx.stats.recordAccept(ns);
    ctx.mutex.unlock();
    clearError(err);
    return .ok;
}

fn sessionCanEnd(s: *const Session) bool {
    if (s.gh.g.regex) |rp| return regex.canEnd(rp, s.rx);
    if (s.gh.g.structtag != null) return structtag.canEnd(&s.tag.?[s.tag_cur]);
    return parser.canEnd(&s.gh.g, curState(s));
}

/// Records the current state before an accept commits (ADR-0014). False
/// when the record does not fit the session budget; state and history
/// are then unchanged.
fn recordHistory(s: *Session) bool {
    if (s.history.cap == 0) return true;
    const a = s.account.allocator(.session);
    var rec: Record = .{ .rx = s.rx };
    if (s.gh.g.structtag != null) {
        const st = &s.tag.?[s.tag_cur];
        rec.tag = st.small;
        if (st.small.phase == .json and !st.small.json_dead) {
            rec.snap = parser.snapshotState(a, &s.side, &st.json) catch return false;
            rec.has_snap = true;
        }
    } else if (s.gh.g.regex == null) {
        rec.snap = parser.snapshotState(a, &s.side, curState(s)) catch return false;
        rec.has_snap = true;
    }
    s.history.makeRoom(a, &s.side) catch {
        parser.dropSnapshot(a, &s.side, &rec.snap);
        return false;
    };
    s.history.at(s.history.len).* = rec;
    s.history.len += 1;
    return true;
}

/// Rolls the session back by the last `n_tokens` successful accepts
/// (ADR-0014). Needs a session created with max_rollback_tokens >= the
/// records held; n_tokens above the records held is INVALID_ARGUMENT with
/// no change, 0 is a no-op. WRONG_STATE unless the session is active.
pub export fn blg_session_rollback(s_opt: ?*Session, n_tokens: u32) callconv(.c) Status {
    const s = s_opt orelse return .invalid_argument;
    if (s.status != .active) return .wrong_state;
    if (n_tokens > s.history.len) return .invalid_argument;
    if (n_tokens == 0) return .ok;
    const a = s.account.allocator(.session);
    for (1..n_tokens) |_| {
        var newer = s.history.popNewest();
        parser.dropSnapshot(a, &s.side, &newer.snap);
    }
    var target = s.history.popNewest();
    if (s.gh.g.structtag != null) {
        const st = &s.tag.?[s.tag_cur];
        if (target.has_snap) {
            parser.restoreSnapshot(a, &s.side, &target.snap, &st.json);
        } else {
            parser.releaseState(&s.side, &st.json);
            st.json.n = 0;
        }
        st.small = target.tag;
    } else if (s.gh.g.regex != null) {
        s.rx = target.rx;
    } else {
        parser.restoreSnapshot(a, &s.side, &target.snap, curState(s));
    }
    return .ok;
}

/// Length of the prefix of ids[0..n] that consecutive blg_accept_token
/// calls would accept, without changing the session (ADR-0014). On
/// RESOURCE_LIMIT or CANCELLED *out_accepted_prefix holds the tokens
/// decided before the failing one.
pub export fn blg_validate_tokens(
    s_opt: ?*const Session,
    ids: ?[*]const u32,
    n: usize,
    out_accepted_prefix: ?*usize,
) callconv(.c) Status {
    const sc = s_opt orelse return .invalid_argument;
    const out = out_accepted_prefix orelse return .invalid_argument;
    out.* = 0;
    if (n != 0 and ids == null) return .invalid_argument;
    // The logical state stays as it is; the scratch state, the side store
    // and the completion memo are working storage of the session.
    const s: *Session = @constCast(sc);
    if (s.status != .active) return .wrong_state;
    if (n == 0) return .ok;
    const ctx = s.ctx;
    const tok = &ctx.tok;
    const g = &s.gh.g;
    if (g.regex) |rp| {
        var rx = s.rx;
        for (ids.?[0..n], 0..) |id, i| {
            if (id >= tok.vocab_size or tok.is_special[id]) return .ok;
            if (tok.is_eos[id]) {
                if (!regex.canEnd(rp, rx)) return .ok;
            } else {
                rx = regex.feed(rp, rx, tok.bytes[id]) orelse return .ok;
            }
            out.* = i + 1;
        }
        return .ok;
    }
    if (g.structtag) |sp| {
        // Two temp states ping-pong through the token chain; the session
        // slots are not touched and the logical state stays as it is.
        const ta = ctx.accounting.allocator(.temp);
        s.tag_pool.completion.releaseOptional();
        const tmp = ta.alloc(structtag.State, 2) catch return .resource_limit;
        tmp[0] = s.tag.?[s.tag_cur];
        if (tmp[0].small.phase == .json and !tmp[0].small.json_dead)
            parser.retainState(&s.side, &tmp[0].json);
        tmp[1].json.n = 0;
        var cur: u1 = 0;
        defer {
            parser.releaseState(&s.side, &tmp[0].json);
            parser.releaseState(&s.side, &tmp[1].json);
            ta.free(tmp);
        }
        for (ids.?[0..n], 0..) |id, i| {
            if (id >= tok.vocab_size or tok.is_special[id]) return .ok;
            if (tok.is_eos[id]) {
                if (!structtag.canEnd(&tmp[cur])) return .ok;
                out.* = i + 1;
                continue;
            }
            var rejected = false;
            for (tok.bytes[id]) |b| {
                structtag.feedByteInto(sp, &s.side, &tmp[cur], b, &s.states[1], &s.mask_buf.spare, &tmp[cur ^ 1]) catch |e| switch (e) {
                    error.Parse => rejected = true,
                    error.ResourceLimit, error.OutOfMemory => return .resource_limit,
                };
                if (rejected) break;
                cur ^= 1;
            }
            if (rejected) return .ok;
            // Completion filter (ADR-0005), mirroring the standalone JSON
            // branch: a byte-legal token the content can never finish from
            // cuts the prefix like any other rejection.
            {
                var w = work_mod.Work{};
                s.tag_pool.completion.fill_budget = complete.fillBudget();
                const alive = structtag.stateAlive(sp, tok, &tmp[cur], &s.tag_pool, &w, &s.side) catch |e| switch (e) {
                    error.ResourceLimit, error.OutOfMemory => return .resource_limit,
                    error.Cancelled => return .cancelled,
                };
                if (!alive) return .ok;
            }
            out.* = i + 1;
        }
        return .ok;
    }
    const ta = ctx.accounting.allocator(.temp);
    // Before the first required allocation of this call: the cache is
    // optional memory and the states below are not.
    s.mask_buf.completion.releaseOptional();
    const extra = ta.create(parser.State) catch return .resource_limit;
    extra.n = 0;
    const bufs = [2]*parser.State{ scratchState(s), extra };
    defer {
        parser.releaseState(&s.side, bufs[0]);
        parser.releaseState(&s.side, extra);
        ta.destroy(extra);
    }
    const check = (!tok.byte_complete and g.finite_literal) or g.needs_reachability;
    var src: *const parser.State = curState(s);
    var which: u1 = 0;
    for (ids.?[0..n], 0..) |id, i| {
        if (id >= tok.vocab_size or tok.is_special[id]) return .ok;
        if (tok.is_eos[id]) {
            if (!parser.canEnd(g, src)) return .ok;
            out.* = i + 1;
            continue;
        }
        const dst = bufs[which];
        parser.releaseState(&s.side, dst);
        parser.feedBytes(g, &s.side, src, tok.bytes[id], dst, &s.mask_buf.spare) catch |e| switch (e) {
            error.Parse => return .ok,
            error.ResourceLimit, error.OutOfMemory => return .resource_limit,
        };
        if (check) {
            var w = work_mod.Work{};
            s.mask_buf.completion.fill_budget = complete.fillBudget();
            const alive = complete.stateAlive(g, tok, dst, &s.mask_buf.completion, &w, &s.side) catch |e| switch (e) {
                error.ResourceLimit, error.OutOfMemory => return .resource_limit,
                error.Cancelled => return .cancelled,
            };
            if (!alive) return .ok;
        }
        src = dst;
        which ^= 1;
        out.* = i + 1;
    }
    return .ok;
}

pub export fn blg_can_end(s_opt: ?*const Session, out_can_end: ?*bool) callconv(.c) Status {
    const s = s_opt orelse return .invalid_argument;
    const o = out_can_end orelse return .invalid_argument;
    switch (s.status) {
        .active => {
            o.* = sessionCanEnd(s);
            return .ok;
        },
        .finished => {
            o.* = true;
            return .ok;
        },
        .aborted => return .wrong_state,
    }
}

/// Active segment of a structural-tag session (BLG_CONSTRAINT_STRUCTURAL_TAG,
/// ADR-0016 diagnostics for the backend trace): out_phase 0 = free text,
/// 1 = begin literal, 2 = JSON content or end literal, 3 = closed
/// (stop_after_first); out_variant = the active tag variant index, or 0xFF
/// when no single variant is active (free text, or a begin prefix shared by
/// several variants). Read-only. BLG_ERR_WRONG_STATE for other grammar
/// kinds or an inactive session; NULL out pointers are
/// BLG_ERR_INVALID_ARGUMENT.
pub export fn blg_session_struct_segment(s_opt: ?*const Session, out_phase: ?*u8, out_variant: ?*u8) callconv(.c) Status {
    const s = s_opt orelse return .invalid_argument;
    const op = out_phase orelse return .invalid_argument;
    const ov = out_variant orelse return .invalid_argument;
    if (s.status != .active) return .wrong_state;
    _ = s.gh.g.structtag orelse return .wrong_state;
    const active = structtag.activeSegment(&s.tag.?[s.tag_cur]);
    op.* = @backingInt(active.phase);
    ov.* = active.variant;
    return .ok;
}

pub export fn blg_finish(s_opt: ?*Session, err: ?*ZgError) callconv(.c) Status {
    if (badErrBuf(err)) return .invalid_argument;
    const s = s_opt orelse return fail(err, .invalid_argument, "null session");
    switch (s.status) {
        .finished => {
            clearError(err);
            return .ok;
        },
        .aborted => return fail(err, .wrong_state, "session aborted"),
        .active => {
            if (!sessionCanEnd(s))
                return fail(err, .wrong_state, "document incomplete, can_end is false");
            s.status = .finished;
            clearError(err);
            return .ok;
        },
    }
}

pub export fn blg_abort(s_opt: ?*Session) callconv(.c) Status {
    const s = s_opt orelse return .invalid_argument;
    if (s.status == .active) s.status = .aborted;
    return .ok;
}

// Number of bytes of StatsC the caller can hold: struct_size 0 (legacy
// "unset" convention) means the full current layout.
fn statsCapacity(o: *const StatsC) ?usize {
    return switch (o.struct_size) {
        0 => @sizeOf(StatsC),
        LEGACY_STATS_SIZE => LEGACY_STATS_SIZE,
        PRE_PROBE_STATS_SIZE => PRE_PROBE_STATS_SIZE,
        @sizeOf(StatsC) => @sizeOf(StatsC),
        else => null,
    };
}

fn writeStats(o: *StatsC, full: *const StatsC) void {
    const n = statsCapacity(o).?;
    @memcpy(@as([*]u8, @ptrCast(o))[0..n], @as([*]const u8, @ptrCast(full))[0..n]);
    o.struct_size = @intCast(n);
}

pub export fn blg_get_stats(ctx_c: ?*const Context, out_stats: ?*StatsC) callconv(.c) Status {
    const ctx: *Context = @constCast(ctx_c orelse return .invalid_argument);
    const o = out_stats orelse return .invalid_argument;
    if (statsCapacity(o) == null)
        return .invalid_argument;
    ctx.mutex.lock();
    defer ctx.mutex.unlock();
    var full = std.mem.zeroes(StatsC);
    full.struct_size = @sizeOf(StatsC);
    full.version = ABI_VERSION;
    full.compile_ns = ctx.stats.compile_ns;
    full.tokenizer_prepare_ns = ctx.stats.tokenizer_prepare_ns;
    full.accept_ns_total = ctx.stats.accept_ns_total;
    full.mask_ns_total = ctx.stats.mask_ns_total;
    full.mask_calls = ctx.stats.mask_calls;
    full.tokens_accepted = ctx.stats.tokens_accepted;
    full.cache_hits = ctx.cache.hits();
    full.cache_misses = ctx.cache.misses();
    full.cache_evictions = ctx.cache.evictions();
    stats.fillMemory(&ctx.accounting, full.mem_used[0..], full.mem_peak[0..]);
    full.mode = @backingInt(ctx.mode);
    full.errors_total = ctx.stats.errors_total;
    full.errors_resource_limit = ctx.stats.errors_resource_limit;
    full.errors_cancelled = ctx.stats.errors_cancelled;
    full.cache_adaptive_skips = ctx.stats.cache_adaptive_skips;
    full.precompute_states = ctx.stats.precompute_states;
    full.probe_runs = ctx.stats.probe_runs;
    full.work_ops_total = ctx.stats.work_ops_total;
    writeStats(o, &full);
    return .ok;
}

pub export fn blg_get_stats_session(s_opt: ?*const Session, out_stats: ?*StatsC) callconv(.c) Status {
    const s = s_opt orelse return .invalid_argument;
    const o = out_stats orelse return .invalid_argument;
    if (statsCapacity(o) == null)
        return .invalid_argument;
    var full = std.mem.zeroes(StatsC);
    full.struct_size = @sizeOf(StatsC);
    full.version = ABI_VERSION;
    full.accept_ns_total = s.stats.accept_ns_total;
    full.mask_ns_total = s.stats.mask_ns_total;
    full.mask_calls = s.stats.mask_calls;
    full.tokens_accepted = s.stats.tokens_accepted;
    full.mem_used[2] = s.account.usedBytes();
    full.mode = @backingInt(s.ctx.mode);
    writeStats(o, &full);
    return .ok;
}

pub export fn blg_cancel_flag_set(ctx_opt: ?*Context, flag: ?*align(1) const u8) callconv(.c) Status {
    const ctx = ctx_opt orelse return .invalid_argument;
    ctx.mutex.lock();
    ctx.cancel_flag = flag;
    ctx.mutex.unlock();
    return .ok;
}

/// Diagnostic, not part of the stable semantics: copies the calling
/// thread's ADR-0005 D3 undecided-reason histogram (complete.UndReason,
/// indexed by enum order) into `out` (up to `cap` entries); returns the
/// full histogram length. The histogram counts, per state stateAlive
/// could not settle (RESOURCE_LIMIT), why its frames stayed uncertified;
/// it exists to prioritize certificate families by measured impact. The
/// last entry (search_entered) counts every state handed to the budgeted
/// search, whatever its outcome.
pub export fn blg_cert_stats(out: ?[*]u64, cap: usize) callconv(.c) usize {
    if (out) |o| {
        const m = @min(cap, complete.UND_REASONS);
        @memcpy(o[0..m], complete.und_hist[0..m]);
    }
    return complete.UND_REASONS;
}

/// Diagnostic counterpart of blg_cert_stats: zeroes the calling thread's
/// histogram.
pub export fn blg_cert_stats_reset() callconv(.c) void {
    complete.und_hist = @splat(0);
}

fn makeTestError() ZgError {
    var e: ZgError = .{
        .struct_size = @sizeOf(ZgError),
        .code = 0,
        .schema_offset = NO_OFFSET,
        .message = undefined,
        .json_pointer = undefined,
    };
    e.message[0] = 0;
    e.json_pointer[0] = 0;
    return e;
}

fn buildDesc(
    tokens: []const []const u8,
    eos: []const u32,
    special: []const u32,
    entries: []TokenEntry,
    blob: []u8,
) TokenizerDesc {
    var off: u64 = 0;
    for (tokens, 0..) |t, i| {
        @memcpy(blob[@intCast(off)..][0..t.len], t);
        entries[i] = .{ .id = @intCast(i), .reserved = 0, .offset = off, .length = t.len };
        off += t.len;
    }
    return .{
        .struct_size = @sizeOf(TokenizerDesc),
        .vocab_size = @intCast(tokens.len),
        .entries = entries.ptr,
        .entry_count = entries.len,
        .blob = blob.ptr,
        .blob_len = off,
        .eos_ids = eos.ptr,
        .eos_count = eos.len,
        .special_ids = special.ptr,
        .special_count = special.len,
    };
}

fn makeConfig(mode: u32) ContextConfig {
    var c = std.mem.zeroes(ContextConfig);
    c.struct_size = @sizeOf(ContextConfig);
    c.version = ABI_VERSION;
    c.mode = mode;
    return c;
}

fn maskBit(words: []const u32, t: u32) bool {
    return (words[t / 32] >> @as(u5, @intCast(t % 32))) & 1 == 1;
}

/// Diagnostic counters of the mask paths (src/diag.zig). Not part of the
/// ABI contract: they exist for the diagnosis of the mask cost and carry no
/// semantic meaning.
pub export fn blg_diag_reset() callconv(.c) void {
    mask_diag.reset();
}

/// Copies the counters in `mask_diag.order` order; returns the number written.
pub export fn blg_diag_counters(out: [*]u64, cap: usize) callconv(.c) usize {
    const v = mask_diag.values();
    const n = @min(cap, v.len);
    for (v[0..n], 0..) |x, i| out[i] = x;
    return n;
}

test "abi: version, context lifecycle, stats" {
    try std.testing.expectEqual(@as(u32, 1), blg_abi_version());
    const tokens = [_][]const u8{ "a", "1", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(1);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u32, @sizeOf(StatsC)), st.struct_size);
    try std.testing.expectEqual(@as(u32, 0), st.tokens_accepted);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

fn countSelfMaps() !usize {
    const io = io_mod.io();
    const f = try std.Io.Dir.openFileAbsolute(io, "/proc/self/maps", .{});
    defer f.close(io);
    var fbuf: [4096]u8 = undefined;
    var r = f.readerStreaming(io, &fbuf);
    const data = try r.interface.allocRemaining(std.testing.allocator, .limited(64 << 20));
    defer std.testing.allocator.free(data);
    return std.mem.count(u8, data, "\n");
}

test "abi: root allocator packs small allocations into slabs (no per-alloc VMA)" {
    if (@import("builtin").os.tag != .linux) return error.SkipZigTest;
    // Regression for the munmap ENOMEM panic: page_allocator maps one VMA
    // per allocation, and thousands of them push a long-lived process to
    // vm.max_map_count, where releasing a page inside a kernel-merged VMA
    // needs a split, fails with ENOMEM, and std.posix.munmap panics on it.
    const maps_before = try countSelfMaps();
    const N = 4096;
    var blocks: [N][]u8 = undefined;
    for (&blocks) |*b| b.* = try root_allocator.alloc(u8, 100);
    // Free every other block: page_allocator must split the kernel-merged
    // VMA around each hole, so the map count explodes (thousands); slab
    // frees go to freelists and the map count stays flat.
    var i: usize = 1;
    while (i < N) : (i += 2) {
        root_allocator.free(blocks[i]);
        blocks[i] = &.{};
    }
    const maps_live = try countSelfMaps();
    for (blocks) |b| if (b.len != 0) root_allocator.free(b);
    try std.testing.expect(maps_live - maps_before < 256);
}

test "abi: invalid config rejected" {
    const tokens = [_][]const u8{ "a", "" };
    const eos = [_]u32{1};
    const special = [_]u32{};
    var entries: [2]TokenEntry = undefined;
    var blob: [16]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var err = makeTestError();
    var ctx: ?*Context = null;

    var bad_size = makeConfig(0);
    bad_size.struct_size = 4;
    try std.testing.expectEqual(Status.invalid_argument, blg_context_create(&bad_size, &desc, &ctx, &err));
    try std.testing.expect(ctx == null);

    var bad_ver = makeConfig(0);
    bad_ver.version = 999;
    try std.testing.expectEqual(Status.invalid_argument, blg_context_create(&bad_ver, &desc, &ctx, &err));

    var bad_mode = makeConfig(7);
    try std.testing.expectEqual(Status.invalid_argument, blg_context_create(&bad_mode, &desc, &ctx, &err));

    var bad_cache = makeConfig(0);
    bad_cache.memory_limit_bytes = 100 << 20;
    bad_cache.cache_limit_bytes = 200 << 20;
    try std.testing.expectEqual(Status.invalid_argument, blg_context_create(&bad_cache, &desc, &ctx, &err));

    var bad_threads = makeConfig(0);
    bad_threads.max_threads_per_state = 1000;
    try std.testing.expectEqual(Status.invalid_argument, blg_context_create(&bad_threads, &desc, &ctx, &err));
}

const TestCtx = struct {
    ctx: *Context,
    gh: *GrammarHandle,
};

fn setupEnumCtx(mode: u32) !TestCtx {
    const tokens = [_][]const u8{ "\"a\"", "\"b\"", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(mode);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    errdefer _ = blg_context_destroy(ctx);
    const schema_json = "{\"type\":\"string\",\"enum\":[\"a\",\"b\"]}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    return .{ .ctx = ctx.?, .gh = gh.? };
}

test "abi: enum session flow, mask bits, errors" {
    for ([_]u32{ 0, 1, 2 }) |mode| {
        const tc = try setupEnumCtx(mode);
        const ctx = tc.ctx;
        var err = makeTestError();
        var s: ?*Session = null;
        try std.testing.expectEqual(Status.ok, blg_session_create(ctx, tc.gh, &s, &err));

        var words = [_]u32{0};
        try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
        try std.testing.expect(maskBit(&words, 0));
        try std.testing.expect(maskBit(&words, 1));
        try std.testing.expect(!maskBit(&words, 2));
        try std.testing.expect(!maskBit(&words, 3));

        try std.testing.expectEqual(Status.buffer_too_small, blg_fill_mask(s, &words, 0, &err));
        try std.testing.expectEqual(Status.invalid_token, blg_accept_token(s, 3, &err));
        try std.testing.expectEqual(Status.invalid_token, blg_accept_token(s, 99, &err));
        try std.testing.expectEqual(Status.wrong_state, blg_finish(s, &err));

        var ce = false;
        try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
        try std.testing.expect(!ce);

        try std.testing.expectEqual(Status.ok, blg_accept_token(s, 0, &err));
        try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
        try std.testing.expect(ce);

        try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
        try std.testing.expect(maskBit(&words, 2));

        try std.testing.expectEqual(Status.invalid_token, blg_accept_token(s, 3, &err));
        try std.testing.expectEqual(Status.ok, blg_accept_token(s, 2, &err));
        try std.testing.expectEqual(Status.ok, blg_finish(s, &err));
        try std.testing.expectEqual(Status.ok, blg_finish(s, &err));
        try std.testing.expectEqual(Status.wrong_state, blg_accept_token(s, 0, &err));
        try std.testing.expectEqual(Status.wrong_state, blg_fill_mask(s, &words, 1, &err));
        try std.testing.expectEqual(Status.ok, blg_abort(s));

        var sst = std.mem.zeroes(StatsC);
        try std.testing.expectEqual(Status.ok, blg_get_stats_session(s, &sst));
        try std.testing.expectEqual(@as(u64, 2), sst.tokens_accepted);
        try std.testing.expectEqual(@as(u64, 2), sst.mask_calls);

        blg_session_destroy(s);
        blg_grammar_release(tc.gh);
        try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
    }
}

test "abi: busy context" {
    const tc = try setupEnumCtx(0);
    const ctx = tc.ctx;
    var err = makeTestError();
    try std.testing.expectEqual(Status.busy, blg_context_destroy(ctx));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, tc.gh, &s, &err));
    blg_grammar_release(tc.gh);
    try std.testing.expectEqual(Status.busy, blg_context_destroy(ctx));
    blg_session_destroy(s);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

// Regression (fuzz seed 0x5EED0001): concurrent compile/session/destroy
// corrupted alloc.Accounting counters; with atomic counters the totals must
// return to zero.
test "abi: concurrent compile/session accounting stays exact" {
    // Byte-complete vocabulary: an infinite-language schema with
    // a partial vocabulary is refused at compile, so the accounting test
    // needs every single byte as an ordinary token plus the structural
    // tokens at ids 0..3 that the test accepts below.
    var byte_tokens: [261][]const u8 = undefined;
    byte_tokens[0] = "{";
    byte_tokens[1] = "}";
    byte_tokens[2] = "\"a\":";
    byte_tokens[3] = "1";
    var byte_bufs: [256][1]u8 = undefined;
    for (0..256) |b| {
        byte_bufs[b][0] = @intCast(b);
        byte_tokens[4 + b] = byte_bufs[b][0..1];
    }
    byte_tokens[260] = "";
    const eos = [_]u32{260};
    const special = [_]u32{};
    var entries: [261]TokenEntry = undefined;
    var blob: [263]u8 = undefined;
    const desc = buildDesc(&byte_tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(1);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));

    const Worker = struct {
        fn run(c: *Context, n: usize) void {
            const schema_json = "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"integer\"}},\"required\":[\"a\"],\"additionalProperties\":false}";
            var i: usize = 0;
            while (i < n) : (i += 1) {
                var e = makeTestError();
                var req: CompileRequest = .{
                    .struct_size = @sizeOf(CompileRequest),
                    .kind = 0,
                    .profile = null,
                    .data = schema_json.ptr,
                    .data_len = schema_json.len,
                };
                var gh: ?*GrammarHandle = null;
                std.debug.assert(blg_compile(c, &req, &gh, &e) == .ok);
                var s: ?*Session = null;
                std.debug.assert(blg_session_create(c, gh, &s, &e) == .ok);
                var words: [9]u32 = @splat(0); // 261 tokens -> 9 words
                std.debug.assert(blg_fill_mask(s, &words, words.len, &e) == .ok);
                std.debug.assert(blg_accept_token(s, 0, &e) == .ok);
                blg_session_destroy(s);
                blg_grammar_release(gh);
            }
        }
    };
    var threads: [4]std.Thread = undefined;
    for (&threads) |*t| t.* = try std.Thread.spawn(.{}, Worker.run, .{ ctx.?, 100 });
    for (threads) |t| t.join();

    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 0), st.mem_used[1]); // grammar
    try std.testing.expectEqual(@as(u64, 0), st.mem_used[2]); // session
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

test "abi: integer schema and batch" {
    // Byte-complete vocabulary: the integer language is infinite,
    // so a partial vocabulary would be refused at compile.
    var tokens: [260][]const u8 = undefined;
    tokens[0] = "1";
    tokens[1] = "2";
    tokens[2] = "-";
    tokens[3] = "";
    var byte_bufs: [256][1]u8 = undefined;
    for (0..256) |b| {
        byte_bufs[b][0] = @intCast(b);
        tokens[4 + b] = byte_bufs[b][0..1];
    }
    const eos = [_]u32{3};
    const special = [_]u32{};
    var entries: [260]TokenEntry = undefined;
    var blob: [259]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(1);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    const schema_json = "{\"type\":\"integer\"}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = "canonical-v1",
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));

    var s1: ?*Session = null;
    var s2: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s1, &err));
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s2, &err));

    var w1: [9]u32 = @splat(0); // 260 tokens -> 9 words
    var w2: [9]u32 = @splat(0);
    const ss = [_]?*Session{ s1, s2 };
    const ms = [_]?[*]align(1) u32{ &w1, &w2 };
    var sts = [_]c_int{ -1, -1 };
    try std.testing.expectEqual(Status.ok, blg_fill_masks_batch(&ss, &ms, 9, &sts, 2, &err));
    try std.testing.expectEqual(@as(c_int, 0), sts[0]);
    try std.testing.expectEqual(@as(c_int, 0), sts[1]);
    try std.testing.expect(maskBit(&w1, 0));
    try std.testing.expect(maskBit(&w2, 1));

    try std.testing.expectEqual(Status.ok, blg_fill_masks_batch(null, null, 1, null, 0, &err));
    try std.testing.expectEqual(Status.invalid_argument, blg_fill_masks_batch(null, null, 1, null, 2, &err));

    try std.testing.expectEqual(Status.ok, blg_accept_token(s1, 0, &err));
    try std.testing.expectEqual(Status.ok, blg_finish(s1, &err));
    try std.testing.expectEqual(Status.wrong_state, blg_fill_masks_batch(&ss, &ms, 9, &sts, 2, &err));
    try std.testing.expectEqual(@as(c_int, @backingInt(Status.wrong_state)), sts[0]);
    try std.testing.expectEqual(@as(c_int, 0), sts[1]);

    blg_session_destroy(s1);
    blg_session_destroy(s2);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

test "abi: literal set" {
    const tokens = [_][]const u8{ "a", "b", "ab", "c", "" };
    const eos = [_]u32{4};
    const special = [_]u32{};
    var entries: [5]TokenEntry = undefined;
    var blob: [32]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    const lit_json = "[\"ab\",\"c\"]";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 1,
        .profile = null,
        .data = lit_json.ptr,
        .data_len = lit_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    try std.testing.expectEqual(Status.ok, blg_accept_token(s, 0, &err));
    try std.testing.expectEqual(Status.ok, blg_accept_token(s, 1, &err));
    var ce = false;
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(ce);
    try std.testing.expectEqual(Status.ok, blg_finish(s, &err));
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

// With a partial vocabulary the completion filter is exact only for finite
// literal languages. An infinite language over a partial vocabulary
// (counterexamples: `{"type":"string"}` with [`""`, `"a`, EOS], and a
// one-element array of an enum) must be refused before generation instead
// of allowing dead-end prefixes.
test "abi: partial vocabulary with an infinite language is refused" {
    const str_tokens = [_][]const u8{ "\"\"", "\"a", "" };
    const str_eos = [_]u32{2};
    var str_entries: [3]TokenEntry = undefined;
    var str_blob: [16]u8 = undefined;
    const str_desc = buildDesc(&str_tokens, &str_eos, &[_]u32{}, &str_entries, &str_blob);

    const arr_tokens = [_][]const u8{ "[", "]", "ab", "a", "\"", "" };
    const arr_eos = [_]u32{5};
    var arr_entries: [6]TokenEntry = undefined;
    var arr_blob: [16]u8 = undefined;
    const arr_desc = buildDesc(&arr_tokens, &arr_eos, &[_]u32{}, &arr_entries, &arr_blob);

    const str_schema = "{\"type\":\"string\"}";
    const arr_schema = "{\"type\":\"array\",\"items\":{\"enum\":[\"ab\"]},\"minItems\":1,\"maxItems\":1}";
    const cases = [_]struct { desc: *const TokenizerDesc, schema: []const u8 }{
        .{ .desc = &str_desc, .schema = str_schema },
        .{ .desc = &arr_desc, .schema = arr_schema },
    };
    for (cases) |cs| {
        var cfg = makeConfig(0);
        var err = makeTestError();
        var ctx: ?*Context = null;
        try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, cs.desc, &ctx, &err));
        var req: CompileRequest = .{
            .struct_size = @sizeOf(CompileRequest),
            .kind = 0,
            .profile = "canonical-v1",
            .data = cs.schema.ptr,
            .data_len = cs.schema.len,
        };
        var gh: ?*GrammarHandle = null;
        try std.testing.expectEqual(Status.unsupported_tokenizer, blg_compile(ctx, &req, &gh, &err));
        try std.testing.expect(gh == null);
        try std.testing.expect(std.mem.indexOf(u8, std.mem.span(@as([*:0]const u8, @ptrCast(&err.message))), "partial byte coverage") != null);
        try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
    }

    // Control: the same finite language with a partial vocabulary is
    // allowed - the exact completion filter covers it (test above), and an
    // infinite language with a byte-complete vocabulary is allowed through
    // single-byte tokens.
    var bytes_desc_blob: [257]u8 = undefined;
    var byte_tokens: [258][]const u8 = undefined;
    byte_tokens[0] = "\"";
    byte_tokens[1] = "";
    var byte_bufs: [256][1]u8 = undefined;
    for (0..256) |b| {
        byte_bufs[b][0] = @intCast(b);
        byte_tokens[2 + b] = byte_bufs[b][0..1];
    }
    const byte_eos = [_]u32{1};
    var byte_entries: [258]TokenEntry = undefined;
    const byte_desc = buildDesc(&byte_tokens, &byte_eos, &[_]u32{}, &byte_entries, &bytes_desc_blob);
    var cfg2 = makeConfig(0);
    var err2 = makeTestError();
    var ctx2: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg2, &byte_desc, &ctx2, &err2));
    var req2: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = "canonical-v1",
        .data = str_schema.ptr,
        .data_len = str_schema.len,
    };
    var gh2: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx2, &req2, &gh2, &err2));
    blg_grammar_release(gh2);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx2));
}

// Vocab [ab, a, <eos>] with literal "ab" - "a" is byte-legal but leads
// to a dead end, so it must be neither in the mask nor accepted
// (invalid_token, state unchanged). Covers all modes, including precompute.
test "abi: dead-end token is neither masked nor accepted" {
    const tokens = [_][]const u8{ "ab", "a", "" };
    const eos = [_]u32{2};
    const special = [_]u32{};
    var entries: [3]TokenEntry = undefined;
    var blob: [8]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var err = makeTestError();
    const lit_json = "[\"ab\"]";
    for ([_]u32{ 0, 1, 2 }) |mode| {
        var cfg = makeConfig(mode);
        var ctx: ?*Context = null;
        try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
        var req: CompileRequest = .{
            .struct_size = @sizeOf(CompileRequest),
            .kind = 1,
            .profile = null,
            .data = lit_json.ptr,
            .data_len = lit_json.len,
        };
        var gh: ?*GrammarHandle = null;
        try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
        var s: ?*Session = null;
        try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
        var words = [_]u32{0};
        try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
        try std.testing.expect(maskBit(&words, 0));
        try std.testing.expect(!maskBit(&words, 1));
        try std.testing.expect(!maskBit(&words, 2));
        try std.testing.expectEqual(Status.invalid_token, blg_accept_token(s, 1, &err));
        try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
        try std.testing.expect(maskBit(&words, 0)); // state unchanged
        try std.testing.expectEqual(Status.ok, blg_accept_token(s, 0, &err));
        try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
        try std.testing.expect(maskBit(&words, 2)); // EOS now allowed
        try std.testing.expectEqual(Status.ok, blg_accept_token(s, 2, &err));
        try std.testing.expectEqual(Status.ok, blg_finish(s, &err));
        blg_session_destroy(s);
        blg_grammar_release(gh);
        try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
    }
}

// HF SentencePiece decoders with Strip(" ", start=1, stop=0)
// drop one leading space of the whole text. With the strip flag the kernel
// models the decoded text: literal " hello" is producible only as the
// stream "  hello" (two spaces), so the token " hello" (one space) is not
// allowed at the start. Without the flag the byte semantics stay as
// before; a legacy descriptor without `flags` is also accepted.
test "abi: strip-lead-space flag models the SP decoder" {
    const tokens = [_][]const u8{ " ", " hello", "" };
    const eos = [_]u32{2};
    const special = [_]u32{};
    var entries: [3]TokenEntry = undefined;
    var blob: [16]u8 = undefined;
    const lit_json = "[\" hello\"]";
    var err = makeTestError();

    var desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    desc.flags = 1;
    var cfg = makeConfig(0);

    var legacy = desc;
    legacy.struct_size = @offsetOf(TokenizerDesc, "flags");
    var ctx_legacy: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &legacy, &ctx_legacy, &err));
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx_legacy));

    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 1,
        .profile = null,
        .data = lit_json.ptr,
        .data_len = lit_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    var words = [_]u32{0};
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 0)); // one space, then...
    try std.testing.expect(!maskBit(&words, 1)); // " hello" would leave "hello"
    try std.testing.expect(!maskBit(&words, 2));
    try std.testing.expectEqual(Status.ok, blg_accept_token(s, 0, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 1)); // ...the full " hello" piece
    try std.testing.expectEqual(Status.ok, blg_accept_token(s, 1, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 2));
    try std.testing.expectEqual(Status.ok, blg_accept_token(s, 2, &err));
    try std.testing.expectEqual(Status.ok, blg_finish(s, &err));
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));

    // Without the flag the byte-level semantics stay as before: the piece
    // " hello" is a valid stream for the literal " hello".
    var desc_plain = desc;
    desc_plain.flags = 0;
    var ctx2: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc_plain, &ctx2, &err));
    var gh2: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx2, &req, &gh2, &err));
    var s2: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx2, gh2, &s2, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s2, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 1));
    blg_session_destroy(s2);
    blg_grammar_release(gh2);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx2));
}

// With a single layered allocator the cache hard budget and the
// .cache accounting counters charge the same full cost, so mem_used can
// never exceed cache_limit_bytes (the old two-layer stack added a second
// header per allocation that the local limit did not see).
test "abi: cache budget and accounting agree" {
    const tokens = [_][]const u8{ "a", "" };
    const eos = [_]u32{1};
    const special = [_]u32{};
    var entries: [2]TokenEntry = undefined;
    var blob: [8]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    const budget: u64 = 83040;
    var cfg = makeConfig(1); // adaptive: the cache is active
    cfg.cache_limit_bytes = budget;
    cfg.adaptive_min_hits = 1;
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    const lit_json = "[\"a\"]";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 1,
        .profile = null,
        .data = lit_json.ptr,
        .data_len = lit_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));

    var words = [_]u32{0};
    var i: usize = 0;
    while (i < 3) : (i += 1)
        try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));

    var st: StatsC = std.mem.zeroes(StatsC);
    st.struct_size = @sizeOf(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    const cache_cat = @backingInt(alloc.Category.cache);
    try std.testing.expect(st.mem_used[cache_cat] <= budget);
    try std.testing.expect(st.mem_peak[cache_cat] <= budget);
    try std.testing.expectEqual(ctx.?.cache.usedBytes(), st.mem_used[cache_cat]);

    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

test "abi: bad compile requests" {
    const tokens = [_][]const u8{ "a", "" };
    const eos = [_]u32{1};
    const special = [_]u32{};
    var entries: [2]TokenEntry = undefined;
    var blob: [8]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    var gh: ?*GrammarHandle = null;

    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = "exotic-v9",
        .data = null,
        .data_len = 0,
    };
    try std.testing.expectEqual(Status.unsupported_feature, blg_compile(ctx, &req, &gh, &err));

    req.profile = null;
    req.kind = 5;
    try std.testing.expectEqual(Status.invalid_argument, blg_compile(ctx, &req, &gh, &err));

    req.kind = 0;
    req.struct_size = 8;
    try std.testing.expectEqual(Status.invalid_argument, blg_compile(ctx, &req, &gh, &err));

    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

// Regression: vocab [a, <eos>] with literal "ab" must be refused
// at compile time (unsupported_tokenizer), not allowed to dead-end after
// accepting "a".
test "abi: compile rejects constraint the tokenizer cannot cover" {
    const tokens = [_][]const u8{ "a", "" };
    const eos = [_]u32{1};
    const special = [_]u32{};
    var entries: [2]TokenEntry = undefined;
    var blob: [8]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));

    const lit_json = "[\"ab\"]";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 1,
        .profile = null,
        .data = lit_json.ptr,
        .data_len = lit_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.unsupported_tokenizer, blg_compile(ctx, &req, &gh, &err));
    try std.testing.expect(gh == null);

    // Same grammar with a covering vocabulary compiles fine.
    const tokens2 = [_][]const u8{ "a", "b", "" };
    const eos2 = [_]u32{2};
    var entries2: [3]TokenEntry = undefined;
    var blob2: [8]u8 = undefined;
    const desc2 = buildDesc(&tokens2, &eos2, &special, &entries2, &blob2);
    var ctx2: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc2, &ctx2, &err));
    try std.testing.expectEqual(Status.ok, blg_compile(ctx2, &req, &gh, &err));
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx2));
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

// Warm compile: repeated blg_compile of the same schema
// bytes must return a new reference to the cached artifact; eviction must
// not touch live sessions; cache=0 keeps the old behavior.
test "abi: compile artifacts are cached and eviction keeps live sessions" {
    const tokens = [_][]const u8{ "\"a\"", "\"b\"", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    const schema_a = "{\"type\":\"string\",\"enum\":[\"a\",\"b\"]}";
    const schema_b = "{\"type\":\"string\",\"enum\":[\"b\",\"a\"]}";
    var err = makeTestError();

    // Cache off: two calls give different handles.
    {
        var cfg = makeConfig(1);
        cfg.cache_limit_bytes = 0;
        var ctx: ?*Context = null;
        try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
        var req_a: CompileRequest = .{
            .struct_size = @sizeOf(CompileRequest),
            .kind = 0,
            .profile = "canonical-v1",
            .data = schema_a.ptr,
            .data_len = schema_a.len,
        };
        var h1: ?*GrammarHandle = null;
        var h2: ?*GrammarHandle = null;
        try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req_a, &h1, &err));
        try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req_a, &h2, &err));
        try std.testing.expect(h1 != h2);
        blg_grammar_release(h1);
        blg_grammar_release(h2);
        try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
    }

    // Small cache: the same artifact is reused; another schema evicts it,
    // but a session on the evicted grammar stays alive.
    // The cache budget counts actual retained cost, so
    // the size is measured first: after the user reference is dropped the
    // grammar category = cost of the cache entry (grammar + handle + schema
    // copy + list capacity).
    var cost: usize = 0;
    {
        var cfg_probe = makeConfig(1);
        cfg_probe.cache_limit_bytes = CACHE_DEFAULT;
        var ctx_probe: ?*Context = null;
        try std.testing.expectEqual(Status.ok, blg_context_create(&cfg_probe, &desc, &ctx_probe, &err));
        var req_probe: CompileRequest = .{
            .struct_size = @sizeOf(CompileRequest),
            .kind = 0,
            .profile = "canonical-v1",
            .data = schema_a.ptr,
            .data_len = schema_a.len,
        };
        var h_probe: ?*GrammarHandle = null;
        try std.testing.expectEqual(Status.ok, blg_compile(ctx_probe, &req_probe, &h_probe, &err));
        blg_grammar_release(h_probe);
        var st_probe = std.mem.zeroes(StatsC);
        try std.testing.expectEqual(Status.ok, blg_get_stats(ctx_probe, &st_probe));
        cost = @intCast(st_probe.mem_used[1]);
        try std.testing.expect(cost > 0);
        try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx_probe));
    }
    var cfg = makeConfig(1);
    // Artifact budget = a quarter of cache_limit: one entry (cost) fits
    // with a 1.5x margin, two entries of the same size do not.
    cfg.cache_limit_bytes = 6 * @as(u64, cost);
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    var req_a: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = "canonical-v1",
        .data = schema_a.ptr,
        .data_len = schema_a.len,
    };
    var req_b: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = "canonical-v1",
        .data = schema_b.ptr,
        .data_len = schema_b.len,
    };
    var h_a: ?*GrammarHandle = null;
    var h_a2: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req_a, &h_a, &err));
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req_a, &h_a2, &err));
    try std.testing.expectEqual(h_a, h_a2); // same artifact
    blg_grammar_release(h_a2);

    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, h_a, &s, &err));
    blg_grammar_release(h_a); // session and cache hold their own references
    var words = [_]u32{0};
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 1)); // schema A: "b" allowed

    var h_b: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req_b, &h_b, &err));
    var h_a3: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req_a, &h_a3, &err));
    // A was evicted (two full-cost entries do not fit the budget) -
    // a new handle.
    try std.testing.expect(h_a3 != h_a);
    blg_grammar_release(h_a3);
    blg_grammar_release(h_b);

    // The live session survived the eviction: the mask still computes.
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 1));
    blg_session_destroy(s);

    // Once user references are dropped, destroy resets the cache itself.
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

// A live user handle must keep the context busy even with the cache
// enabled: under a "refcount = cache + user" scheme live_grammars would
// equal cache.refs while a user handle is still live, destroy would free
// the context under that handle, and the next release would crash.
test "abi: live grammar refs keep the context busy" {
    const tokens = [_][]const u8{ "\"a\"", "\"b\"", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    const schema_a = "{\"type\":\"string\",\"enum\":[\"a\",\"b\"]}";
    var err = makeTestError();

    for ([_]u64{ 0, CACHE_DEFAULT }) |cache_limit| {
        var cfg = makeConfig(1);
        cfg.cache_limit_bytes = cache_limit;
        var ctx: ?*Context = null;
        try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
        var req: CompileRequest = .{
            .struct_size = @sizeOf(CompileRequest),
            .kind = 0,
            .profile = "canonical-v1",
            .data = schema_a.ptr,
            .data_len = schema_a.len,
        };
        var h1: ?*GrammarHandle = null;
        var h2: ?*GrammarHandle = null;
        try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &h1, &err));
        // A second hand-out of the same artifact is a second external
        // reference (with the cache it is a hit on the same handle).
        try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &h2, &err));
        if (cache_limit != 0) try std.testing.expectEqual(h1, h2);
        try std.testing.expectEqual(Status.busy, blg_context_destroy(ctx));
        // The session holds the handle after user references are dropped.
        var s: ?*Session = null;
        try std.testing.expectEqual(Status.ok, blg_session_create(ctx, h1, &s, &err));
        blg_grammar_release(h1);
        blg_grammar_release(h2);
        try std.testing.expectEqual(Status.busy, blg_context_destroy(ctx));
        blg_session_destroy(s);
        // Closing after a BUSY refusal succeeds: destroy drops cache refs itself.
        try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
    }
}

// The artifact cache budget counts actually retained
// bytes (grammar + handle + schema copy + capacity), not metadata.
// Otherwise the cache eats the global memory_limit and the next request
// gets RESOURCE_LIMIT even though it passes without the cache.
test "abi: artifact cache budget counts retained grammars" {
    const tokens = [_][]const u8{ "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "" };
    const eos = [_]u32{10};
    const special = [_]u32{};
    var entries: [11]TokenEntry = undefined;
    var blob: [32]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var err = makeTestError();

    var cfg = makeConfig(1);
    // memory_limit_bytes must clear the live Session size: a Session holds
    // 3 inline States of 262660 B (ping-pong pair + mask spare; the 128
    // lazy-alternation thread cap dominates the inline arrays), 788504 B
    // total, so it does not fit under 512 KiB. The cache-budget invariant
    // this test guards is driven by cache_limit_bytes (budget stays 16384).
    cfg.memory_limit_bytes = 1048576;
    cfg.cache_limit_bytes = 65536; // artifact budget = 16384
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));

    // 600 unique small schemas: the cache must stay within its budget.
    var i: u32 = 0;
    while (i < 600) : (i += 1) {
        var json_buf: [16]u8 = undefined;
        const bytes = try std.fmt.bufPrint(&json_buf, "[\"{d}\"]", .{i});
        var req: CompileRequest = .{
            .struct_size = @sizeOf(CompileRequest),
            .kind = 1,
            .profile = null,
            .data = bytes.ptr,
            .data_len = bytes.len,
        };
        var gh: ?*GrammarHandle = null;
        try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
        blg_grammar_release(gh);
    }
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expect(st.mem_used[1] <= 16384);

    // A large literal with the cache enabled passes the same limits as
    // without it (the cache does not retain almost the whole memory_limit).
    var big_buf: [1024]u8 = undefined;
    big_buf[0] = '[';
    big_buf[1] = '"';
    @memset(big_buf[2..1002], '1');
    big_buf[1002] = '"';
    big_buf[1003] = ']';
    var big_req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 1,
        .profile = null,
        .data = &big_buf,
        .data_len = 1004,
    };
    var big: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &big_req, &big, &err));
    blg_grammar_release(big);

    // Cache reset: live handles keep working, memory returns.
    var json_buf2: [16]u8 = undefined;
    const small2 = try std.fmt.bufPrint(&json_buf2, "[\"7\"]", .{});
    var req2: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 1,
        .profile = null,
        .data = small2.ptr,
        .data_len = small2.len,
    };
    var gh2: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req2, &gh2, &err));
    var st_before = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st_before));
    try std.testing.expect(st_before.mem_used[1] > 0); // cache entries
    try std.testing.expectEqual(Status.ok, blg_context_reset_cache(ctx));
    var st_mid = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st_mid));
    try std.testing.expect(st_mid.mem_used[1] < st_before.mem_used[1]); // the cache released bytes
    try std.testing.expect(st_mid.mem_used[1] > 0); // live gh2 remains
    try std.testing.expectEqual(Status.busy, blg_context_destroy(ctx)); // gh2 alive
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh2, &s, &err));
    blg_session_destroy(s);
    blg_grammar_release(gh2);
    var st_end = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st_end));
    try std.testing.expectEqual(@as(u64, 0), st_end.mem_used[1]);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

// Destroy must not free the context while another thread
// holds its objects. The guarantee is deterministic: while the compiling
// call is registered (active_calls) and/or the thread holds a handle,
// destroy must return BUSY; after the references are dropped it must
// succeed. The window "destroy started before a new call registered" is the
// caller's responsibility (serialize destroy with new calls); see the
// public header.
const ConcurrentCompileCtx = struct {
    ctx: *Context,
    phase1: std.atomic.Value(bool) = std.atomic.Value(bool).init(false),
    release: std.atomic.Value(bool) = std.atomic.Value(bool).init(false),
    compile_ok: std.atomic.Value(bool) = std.atomic.Value(bool).init(false),

    fn run(self: *ConcurrentCompileCtx) void {
        var err = makeTestError();
        // Large literal: the compile takes noticeable time.
        var json_buf: [4200]u8 = undefined;
        json_buf[0] = '[';
        json_buf[1] = '"';
        @memset(json_buf[2..4198], 'x');
        json_buf[4198] = '"';
        json_buf[4199] = ']';
        var req: CompileRequest = .{
            .struct_size = @sizeOf(CompileRequest),
            .kind = 1,
            .profile = null,
            .data = &json_buf,
            .data_len = json_buf.len,
        };
        var gh: ?*GrammarHandle = null;
        self.compile_ok.store(blg_compile(self.ctx, &req, &gh, &err) == .ok, .release);
        self.phase1.store(true, .release);
        while (!self.release.load(.acquire)) std.Thread.yield() catch {};
        if (gh) |h| blg_grammar_release(h);
    }
};

test "abi: destroy during concurrent compile stays busy" {
    const tokens = [_][]const u8{ "x", "\"", "[", "]", "", "<pad>" };
    const eos = [_]u32{4};
    const special = [_]u32{5};
    var entries: [6]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(1);
    cfg.cache_limit_bytes = 0; // external refs are visible without the cache
    var ctx: ?*Context = null;
    var err = makeTestError();
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));

    var job = ConcurrentCompileCtx{ .ctx = ctx.? };
    const th = try std.Thread.spawn(.{}, ConcurrentCompileCtx.run, .{&job});
    // Wait for compile completion and the thread's signal: the handle is
    // held by the compiling thread - destroy must be busy from the main thread.
    while (!job.phase1.load(.acquire)) std.Thread.yield() catch {};
    try std.testing.expect(job.compile_ok.load(.acquire));
    try std.testing.expectEqual(Status.busy, blg_context_destroy(ctx));
    job.release.store(true, .release);
    th.join();
    // After the external reference is dropped, closing succeeds.
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

test "abi: destroy during in-flight compile stays busy" {
    // The previous test waited for compile COMPLETION and checked retaining
    // a finished handle. Here the active_calls branch is checked: destroy
    // from the main thread must return BUSY while the blg_compile call is
    // NOT yet finished. CompileGate deterministically pauses the compile
    // right after the in-flight call is registered.
    const tokens = [_][]const u8{ "x", "\"", "[", "]", "", "<pad>" };
    const eos = [_]u32{4};
    const special = [_]u32{5};
    var entries: [6]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(1);
    cfg.cache_limit_bytes = 0;
    var ctx: ?*Context = null;
    var err = makeTestError();
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));

    var gate = CompileGate{};
    compile_gate = &gate;
    defer compile_gate = null;

    var job = ConcurrentCompileCtx{ .ctx = ctx.? };
    const th = try std.Thread.spawn(.{}, ConcurrentCompileCtx.run, .{&job});
    // The compile is registered (active_calls == 1) but not finished:
    // destroy must return BUSY because of the in-flight call.
    while (!gate.entered.load(.acquire)) std.Thread.yield() catch {};
    try std.testing.expectEqual(Status.busy, blg_context_destroy(ctx));

    gate.release.store(true, .release);
    // The compile finishes; the thread holds the handle until job.release.
    while (!job.phase1.load(.acquire)) std.Thread.yield() catch {};
    try std.testing.expect(job.compile_ok.load(.acquire));
    // Now busy is held by the live external reference (the thread's handle).
    try std.testing.expectEqual(Status.busy, blg_context_destroy(ctx));

    job.release.store(true, .release);
    th.join();
    // After the external reference is dropped, closing succeeds.
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

test "abi: cache_limit_bytes 0 disables the cache, CACHE_DEFAULT restores it" {
    const tokens = [_][]const u8{ "\"a\"", "\"b\"", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    const schema_json = "{\"type\":\"string\",\"enum\":[\"a\",\"b\"]}";

    // 0 => cache off: masks still computed, no hits/misses recorded.
    var cfg = makeConfig(1);
    cfg.cache_limit_bytes = 0;
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    var words = [_]u32{0};
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 0));
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 0), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 0), st.cache_misses);
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));

    // CACHE_DEFAULT => default 64 MiB budget: with adaptive_min_hits=1 the
    // second fill hits the cache.
    cfg.cache_limit_bytes = CACHE_DEFAULT;
    cfg.adaptive_min_hits = 1;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 1), st.cache_misses);
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

// Regression: with cache_limit_bytes=82400 the bytes actually
// charged to the cache category must never exceed the budget.
test "abi: cache budget is a hard limit" {
    const tokens = [_][]const u8{ "\"a\"", "\"b\"", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(1);
    cfg.cache_limit_bytes = 82400;
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    const schema_json = "{\"type\":\"string\",\"enum\":[\"a\",\"b\"]}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    var words = [_]u32{0};
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 0));
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expect(st.mem_used[3] <= 82400); // cache category
    try std.testing.expect(st.mem_peak[3] <= 82400);
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

const LegacyConfig = extern struct {
    struct_size: u32,
    version: u32,
    mode: u32,
    max_depth: u32,
    max_threads_per_state: u32,
    reserved0: u32,
    memory_limit_bytes: u64,
    cache_limit_bytes: u64,
    session_limit_bytes: u64,
    schema_limit_bytes: u64,
};

const LegacyError = extern struct {
    struct_size: u32,
    code: i32,
    schema_offset: u32,
    message: [ERROR_MESSAGE_CAP]u8,
};

const LegacyStats = extern struct {
    struct_size: u32,
    version: u32,
    compile_ns: u64,
    tokenizer_prepare_ns: u64,
    accept_ns_total: u64,
    mask_ns_total: u64,
    mask_calls: u64,
    tokens_accepted: u64,
    cache_hits: u64,
    cache_misses: u64,
    cache_evictions: u64,
    mem_used: [STATS_CATEGORY_COUNT]u64,
    mem_peak: [STATS_CATEGORY_COUNT]u64,
};

comptime {
    std.debug.assert(@sizeOf(LegacyConfig) == LEGACY_CONFIG_SIZE);
    std.debug.assert(@sizeOf(LegacyError) == LEGACY_ERROR_SIZE);
    std.debug.assert(@sizeOf(LegacyStats) == LEGACY_STATS_SIZE);
}

test "abi: legacy struct sizes are accepted with defaults" {
    const tokens = [_][]const u8{ "\"a\"", "\"b\"", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);

    var lc = std.mem.zeroes(LegacyConfig);
    lc.struct_size = @sizeOf(LegacyConfig);
    lc.version = ABI_VERSION;
    lc.mode = 1;
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(@ptrCast(&lc), &desc, &ctx, &err));
    try std.testing.expectEqual(@as(u64, 0), ctx.?.work_limit_ops);
    try std.testing.expectEqual(DEFAULT_ADAPTIVE_MIN_HITS, ctx.?.adaptive_min_hits);

    // Legacy-size error buffer: compile diagnostics write the prefix only.
    var le: LegacyError = undefined;
    le.struct_size = @sizeOf(LegacyError);
    const bad_schema = "{\"type\":\"strung\"}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = bad_schema.ptr,
        .data_len = bad_schema.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.invalid_schema, blg_compile(ctx, &req, &gh, @ptrCast(&le)));
    try std.testing.expectEqual(@as(i32, @backingInt(Status.invalid_schema)), le.code);

    // Legacy-size stats buffer: prefix is filled, struct_size preserved.
    var lst = std.mem.zeroes(LegacyStats);
    lst.struct_size = @sizeOf(LegacyStats);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, @ptrCast(&lst)));
    try std.testing.expectEqual(@as(u32, @sizeOf(LegacyStats)), lst.struct_size);
    try std.testing.expectEqual(@as(u32, ABI_VERSION), lst.version);

    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u32, 1), st.mode);
    try std.testing.expectEqual(@as(u64, 1), st.errors_total);
    try std.testing.expectEqual(@as(u64, 0), st.errors_cancelled);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

test "abi: adaptive policy skips cheap one-shot masks, keeps frequent and expensive" {
    const tokens = [_][]const u8{ "\"a\"", "\"b\"", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    const schema_json = "{\"type\":\"string\",\"enum\":[\"a\",\"b\"]}";

    // Hits-based admission: min_hits=2 (default), cost threshold disabled so
    // the result does not depend on timing. The state seen twice is admitted,
    // the third fill hits; the one-shot first compute is skipped.
    var cfg = makeConfig(1);
    cfg.cache_limit_bytes = CACHE_DEFAULT;
    cfg.adaptive_min_cost_ns = std.math.maxInt(u64);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    var words = [_]u32{0};
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 2), st.cache_misses);
    try std.testing.expectEqual(@as(u64, 1), st.cache_adaptive_skips);
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));

    // Configurable thresholds: min_hits=1 admits on the first compute.
    cfg.adaptive_min_hits = 1;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 1), st.cache_misses);
    try std.testing.expectEqual(@as(u64, 0), st.cache_adaptive_skips);
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));

    // min_cost_ns=1: any measured compute is expensive enough to admit.
    cfg = makeConfig(1);
    cfg.cache_limit_bytes = CACHE_DEFAULT;
    cfg.adaptive_min_cost_ns = 1;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 1), st.cache_misses);
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

test "abi: precompute mode warms the mask cache at compile" {
    const tokens = [_][]const u8{ "\"a\"", "\"b\"", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    const schema_json = "{\"type\":\"string\",\"enum\":[\"a\",\"b\"]}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var err = makeTestError();
    var gh: ?*GrammarHandle = null;
    var s: ?*Session = null;
    var words = [_]u32{0};

    // Precompute: states are cached by compile; the first fill is a hit.
    var cfg = makeConfig(2);
    cfg.cache_limit_bytes = CACHE_DEFAULT;
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expect(st.precompute_states >= 1);
    try std.testing.expectEqual(@as(u32, 2), st.mode);
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 0));
    try std.testing.expect(maskBit(&words, 1));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 0), st.cache_misses);
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));

    // Lazy: no warm-up, no cache activity at all.
    cfg = makeConfig(0);
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 0), st.precompute_states);
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 0), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 0), st.cache_misses);
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

test "abi: cancel flag aborts fill_mask and compile" {
    const tokens = [_][]const u8{ "\"a\"", "\"b\"", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    const schema_json = "{\"type\":\"string\",\"enum\":[\"a\",\"b\"]}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var err = makeTestError();
    var flag: u8 = 0;

    var cfg = makeConfig(0);
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    try std.testing.expectEqual(Status.ok, blg_cancel_flag_set(ctx, &flag));

    // Compile with the flag set is cancelled before any grammar work.
    @atomicStore(u8, &flag, 1, .release);
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.cancelled, blg_compile(ctx, &req, &gh, &err));
    try std.testing.expect(gh == null);
    @atomicStore(u8, &flag, 0, .release);
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));

    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    var words = [_]u32{0};
    @atomicStore(u8, &flag, 1, .release);
    try std.testing.expectEqual(Status.cancelled, blg_fill_mask(s, &words, 1, &err));
    @atomicStore(u8, &flag, 0, .release);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 0));

    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 2), st.errors_cancelled);
    try std.testing.expect(st.errors_total >= 2);

    // Detaching the flag restores plain behavior.
    try std.testing.expectEqual(Status.ok, blg_cancel_flag_set(ctx, null));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, 1, &err));

    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
    try std.testing.expectEqual(Status.invalid_argument, blg_cancel_flag_set(null, &flag));
}

test "abi: work_limit_ops bounds fill_mask and compile, zero is unlimited" {
    const tokens = [_][]const u8{ "\"a\"", "\"b\"", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    const schema_json = "{\"type\":\"string\",\"enum\":[\"a\",\"b\"]}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var err = makeTestError();

    // Tiny limit: compile itself runs out of work.
    var cfg = makeConfig(0);
    cfg.work_limit_ops = 1;
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.resource_limit, blg_compile(ctx, &req, &gh, &err));
    try std.testing.expect(gh == null);
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.errors_resource_limit);
    try std.testing.expect(st.work_ops_total > 0);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));

    // A limit that survives compile trips inside fill_mask. The schema is a
    // free-form string, so full byte coverage is required: the
    // vocabulary carries the opening-quote fillers plus all 256 single-byte
    // tokens. The exact charge split (compile vs one mask) is measured on a
    // first unlimited context, so the test does not depend on magic values.
    var toks: [288][]const u8 = undefined;
    var filler: [30][8]u8 = undefined;
    toks[0] = "\"";
    for (0..30) |i| {
        @memset(&filler[i], 100 + @as(u8, @intCast(i)));
        filler[i][0] = '"';
        toks[1 + i] = &filler[i];
    }
    toks[31] = "";
    var byte_bufs2: [256][1]u8 = undefined;
    for (0..256) |b| {
        byte_bufs2[b][0] = @intCast(b);
        toks[32 + b] = byte_bufs2[b][0..1];
    }
    const eos2 = [_]u32{31};
    var entries2: [288]TokenEntry = undefined;
    var blob2: [576]u8 = undefined;
    const desc2 = buildDesc(&toks, &eos2, &[_]u32{}, &entries2, &blob2);
    const str_schema = "{\"type\":\"string\"}";
    req.data = str_schema.ptr;
    req.data_len = str_schema.len;
    const mask_words: usize = 9; // 288 tokens

    // First context with a deliberately large limit: accounting is on,
    // nothing hits the cap; measure the cost of compile and one mask.
    cfg = makeConfig(0);
    cfg.work_limit_ops = 1_000_000;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc2, &ctx, &err));
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    const compile_ops = st.work_ops_total;
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    var words: [9]u32 = @splat(0);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &words, mask_words, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    const mask_ops = st.work_ops_total - compile_ops;
    try std.testing.expect(mask_ops >= 2); // otherwise a limit below the mask cannot be set

    // Per-call limit: the compile has already passed; the next mask hits a
    // limit slightly below its own cost.
    ctx.?.work_limit_ops = mask_ops - 1;
    var s2: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s2, &err));
    var words2: [9]u32 = @splat(0);
    try std.testing.expectEqual(Status.resource_limit, blg_fill_mask(s2, &words2, mask_words, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.errors_resource_limit);

    // Zero (unset) means unlimited.
    ctx.?.work_limit_ops = 0;
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s2, &words2, mask_words, &err));
    try std.testing.expect(maskBit(&words2, 0));
    blg_session_destroy(s2);
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

test "abi: compile diagnostics carry a JSON Pointer" {
    const tokens = [_][]const u8{ "\"a\"", "\"b\"", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));

    const schema_json = "{\"type\":\"object\",\"properties\":{\"k0\":{\"type\":\"array\",\"items\":{\"type\":\"string\",\"pattern\":\"x\"}}},\"required\":[\"k0\"],\"additionalProperties\":false}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.unsupported_feature, blg_compile(ctx, &req, &gh, &err));
    try std.testing.expectEqualStrings("/properties/k0/items", std.mem.span(@as([*:0]const u8, @ptrCast(&err.json_pointer))));

    const lit_json = "[\"ok\",1]";
    req.kind = 1;
    req.data = lit_json.ptr;
    req.data_len = lit_json.len;
    try std.testing.expectEqual(Status.invalid_schema, blg_compile(ctx, &req, &gh, &err));
    try std.testing.expectEqualStrings("/1", std.mem.span(@as([*:0]const u8, @ptrCast(&err.json_pointer))));

    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

test "abi: spec-v1 open object session (side allocator outlives create)" {
    // Regression: the Side store must wrap the heap-resident session
    // account; a stack-local allocator died with blg_session_create's frame
    // and the first open-object key allocation crashed/release-only.
    var byte_bufs: [256][1]u8 = undefined;
    var tokens: [258][]const u8 = undefined;
    for (0..256) |b| {
        byte_bufs[b][0] = @intCast(b);
        tokens[b] = byte_bufs[b][0..1];
    }
    tokens[256] = "";
    tokens[257] = "";
    const eos = [_]u32{256};
    const special = [_]u32{257};
    var entries: [258]TokenEntry = undefined;
    var blob: [256]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    const schema_json = "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"string\"}},\"required\":[\"a\"]}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = "spec-v1",
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    const doc = "{\"a\":\"s\"}";
    for (doc, 0..) |b, i| {
        const st = blg_accept_token(s, b, &err);
        if (st != .ok) std.debug.print("REPRO byte {d} '{c}' -> {s}\n", .{ i, b, @tagName(st) });
        try std.testing.expectEqual(Status.ok, st);
    }
    var ce = false;
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(ce);
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

// --- ADR-0007 fast-path ABI tests ---

fn buildByteCompleteDesc(entries: *[261]TokenEntry, blob: *[1024]u8, bufs: *[256][1]u8) TokenizerDesc {
    // All 256 single bytes (byte-complete) + multi-byte content pieces +
    // eos. ids: byte b -> id b; 256 "ab"; 257 "\xc3\xa9" (é); 258 "a\xc3\xa9";
    // 259 eos ""; 260 special "<pad>".
    var tokens: [261][]const u8 = undefined;
    for (0..256) |b| {
        bufs[b][0] = @intCast(b);
        tokens[b] = bufs[b][0..1];
    }
    tokens[256] = "ab";
    tokens[257] = "\xc3\xa9";
    tokens[258] = "a\xc3\xa9";
    tokens[259] = "";
    tokens[260] = "<pad>";
    const eos = [_]u32{259};
    const special = [_]u32{260};
    return buildDesc(&tokens, &eos, &special, entries, blob);
}

fn buildReviewDesc(entries: *[267]TokenEntry, blob: *[1024]u8, bufs: *[256][1]u8) TokenizerDesc {
    // Boundary-crossing vocabulary. ids: byte b -> id b (byte-complete);
    // 256 "ab"; 257 "aa"; 258 "bb";
    // 259 "é"; 260 "aé"; 261 "a\","; 262 "\",\""; 263 "ab\""; 264 "aa\",";
    // 265 eos ""; 266 special "<pad>".
    var tokens: [267][]const u8 = undefined;
    for (0..256) |b| {
        bufs[b][0] = @intCast(b);
        tokens[b] = bufs[b][0..1];
    }
    tokens[256] = "ab";
    tokens[257] = "aa";
    tokens[258] = "bb";
    tokens[259] = "\xc3\xa9";
    tokens[260] = "a\xc3\xa9";
    tokens[261] = "a\",";
    tokens[262] = "\",\"";
    tokens[263] = "ab\"";
    tokens[264] = "aa\",";
    tokens[265] = "";
    tokens[266] = "<pad>";
    const eos = [_]u32{265};
    const special = [_]u32{266};
    return buildDesc(&tokens, &eos, &special, entries, blob);
}

fn buildNameDesc(entries: *[267]TokenEntry, blob: *[1024]u8, bufs: *[256][1]u8) TokenizerDesc {
    // Property-name vocabulary: byte-complete, plus name-content pieces and
    // tokens that cross the closing quote and the colon.
    // ids: byte b -> id b; 256 "ab"; 257 "aa"; 258 "bb"; 259 "é";
    // 260 "a\":"; 261 "\":"; 262 "x\":"; 263 "a\""; 264 "ab\"";
    // 265 eos ""; 266 special "<pad>".
    var tokens: [267][]const u8 = undefined;
    for (0..256) |b| {
        bufs[b][0] = @intCast(b);
        tokens[b] = bufs[b][0..1];
    }
    tokens[256] = "ab";
    tokens[257] = "aa";
    tokens[258] = "bb";
    tokens[259] = "\xc3\xa9";
    tokens[260] = "a\":";
    tokens[261] = "\":";
    tokens[262] = "x\":";
    tokens[263] = "a\"";
    tokens[264] = "ab\"";
    tokens[265] = "";
    tokens[266] = "<pad>";
    const eos = [_]u32{265};
    const special = [_]u32{266};
    return buildDesc(&tokens, &eos, &special, entries, blob);
}

test "abi: mask_fast_path / max_workers config validation and legacy sizes" {
    var entries: [261]TokenEntry = undefined;
    var blob: [1024]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildByteCompleteDesc(&entries, &blob, &bufs);
    var err = makeTestError();
    var ctx: ?*Context = null;

    var bad_fp = makeConfig(0);
    bad_fp.mask_fast_path = 3;
    try std.testing.expectEqual(Status.invalid_argument, blg_context_create(&bad_fp, &desc, &ctx, &err));
    try std.testing.expect(ctx == null);

    var bad_workers = makeConfig(0);
    bad_workers.max_workers = MAX_WORKERS_CAP + 1;
    try std.testing.expectEqual(Status.invalid_argument, blg_context_create(&bad_workers, &desc, &ctx, &err));

    // The previous tail size (88) stays valid; new fields take defaults
    // (fast path on, single worker).
    var legacy88 = makeConfig(0);
    legacy88.struct_size = 88;
    try std.testing.expectEqual(Status.ok, blg_context_create(&legacy88, &desc, &ctx, &err));
    try std.testing.expect(ctx.?.fast != null);
    try std.testing.expectEqual(@as(u32, 1), ctx.?.max_workers);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));

    // Explicit off: no tables are built.
    var off = makeConfig(0);
    off.mask_fast_path = MASK_FAST_PATH_OFF;
    try std.testing.expectEqual(Status.ok, blg_context_create(&off, &desc, &ctx, &err));
    try std.testing.expect(ctx.?.fast == null);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

// Fill the mask of the string-content state (after feeding prefix) with the
// given config flags; returns the session too so the caller can continue.
fn maskAtStringState(cfg: *ContextConfig, desc: *const TokenizerDesc, schema_json: []const u8, prefix: []const u8, words: []u32, work_ops: *u64) !void {
    return maskAtPrefixProfile(cfg, desc, "canonical-v1", schema_json, prefix, words, work_ops);
}

fn maskAtPrefixProfile(cfg: *ContextConfig, desc: *const TokenizerDesc, profile: [*:0]const u8, schema_json: []const u8, prefix: []const u8, words: []u32, work_ops: *u64) !void {
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(cfg, desc, &ctx, &err));
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = profile,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    for (prefix) |b| {
        try std.testing.expectEqual(Status.ok, blg_accept_token(s, b, &err));
    }
    var st0 = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st0));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, words.ptr, words.len, &err));
    var st1 = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st1));
    work_ops.* = st1.work_ops_total - st0.work_ops_total;
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

test "abi: fast path on/off bit-for-bit, fewer charged ops" {
    var entries: [261]TokenEntry = undefined;
    var blob: [1024]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildByteCompleteDesc(&entries, &blob, &bufs);
    const schema_json = "{\"type\":\"string\"}";

    for ([_]u32{ 0, 1 }) |mode| {
        var on = makeConfig(mode);
        on.work_limit_ops = 1 << 60;
        var off = makeConfig(mode);
        off.work_limit_ops = 1 << 60;
        off.mask_fast_path = MASK_FAST_PATH_OFF;

        for ([_][]const u8{ "\"", "\"ab", "\"a\xc3\xa9" }) |prefix| {
            var w_on: [9]u32 = @splat(0);
            var w_off: [9]u32 = @splat(0);
            var ops_on: u64 = 0;
            var ops_off: u64 = 0;
            try maskAtStringState(&on, &desc, schema_json, prefix, &w_on, &ops_on);
            try maskAtStringState(&off, &desc, schema_json, prefix, &w_off, &ops_off);
            try std.testing.expectEqualSlices(u32, &w_off, &w_on);
            // The fast path charges the classification once plus the pruned
            // walk: strictly fewer ops than the full trie DFS.
            try std.testing.expect(ops_on < ops_off);
            // Sanity: single content byte allowed, control byte not, eos not.
            try std.testing.expect(maskBit(&w_on, 'a'));
            try std.testing.expect(!maskBit(&w_on, 0x01));
            try std.testing.expect(!maskBit(&w_on, 259));
        }
    }
}

test "abi: fast path on/off bit-for-bit under needs_reachability" {
    // The relaxation under test: a uniform string-content state is decided
    // by the lemma even when the grammar needs the completion filter. The
    // schema forces needs_reachability through the bounded number sibling
    // while property "a" stays a plain (uniform) string.
    var entries: [261]TokenEntry = undefined;
    var blob: [1024]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildByteCompleteDesc(&entries, &blob, &bufs);
    const schema_json =
        "{\"type\":\"object\",\"properties\":{" ++
        "\"a\":{\"type\":\"string\"}," ++
        "\"b\":{\"type\":\"integer\",\"minimum\":1,\"maximum\":5}}," ++
        "\"required\":[\"a\"]}";

    for ([_]u32{ 0, 1 }) |mode| {
        var on = makeConfig(mode);
        on.work_limit_ops = 1 << 60;
        var off = makeConfig(mode);
        off.work_limit_ops = 1 << 60;
        off.mask_fast_path = MASK_FAST_PATH_OFF;

        for ([_][]const u8{ "{\"a\":\"", "{\"a\":\"ab" }) |prefix| {
            var w_on: [9]u32 = @splat(0);
            var w_off: [9]u32 = @splat(0);
            var ops_on: u64 = 0;
            var ops_off: u64 = 0;
            mask_diag.reset();
            try maskAtPrefixProfile(&on, &desc, "spec-v1", schema_json, prefix, &w_on, &ops_on);
            try std.testing.expect(mask_diag.c.fast_used.load(.monotonic) > 0); // the fast path ran
            try maskAtPrefixProfile(&off, &desc, "spec-v1", schema_json, prefix, &w_off, &ops_off);
            try std.testing.expectEqualSlices(u32, &w_off, &w_on);
            // Sanity on the shared bits: content allowed, control byte not,
            // the closing quote allowed, eos never (a str frame cannot end).
            try std.testing.expect(maskBit(&w_on, 'a'));
            try std.testing.expect(!maskBit(&w_on, 0x01));
            try std.testing.expect(maskBit(&w_on, '"'));
            try std.testing.expect(!maskBit(&w_on, 259));
        }
    }
}

/// One schema/prefix probe of the fast-path scope test: bit-for-bit mask
/// equality between the fast and exact paths, plus the expected path
/// selection. `forbidden` ids are ground truth the exact path must refuse.
const FastScopeCase = struct {
    name: []const u8,
    schema: []const u8,
    prefix: []const u8,
    /// The enclosure makes the typed content part of a parent verdict
    /// (combinator branches or an armed element/object capture): the fast
    /// path must refuse and the exact walk must decide.
    content_dep: bool,
    forbidden: []const u32 = &.{},
    must_allow: []const u32 = &.{},
};

test "abi: fast path scope - content-dependent enclosures and boundary tokens" {
    var entries: [267]TokenEntry = undefined;
    var blob: [1024]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildReviewDesc(&entries, &blob, &bufs);

    const cases = [_]FastScopeCase{
        // uniqueItems compares the element content at the element close;
        // allOf is a conjunction, so the maxLength-1 branch refuses any
        // token of two codepoints even though the maxLength-2 branch
        // accepts it.
        .{
            .name = "uniqueItems-duplicate-element",
            .schema = "{\"type\":\"array\",\"items\":{\"type\":\"string\",\"maxLength\":1}," ++
                "\"uniqueItems\":true,\"minItems\":2,\"maxItems\":2}",
            .prefix = "[\"a\",\"",
            .content_dep = true,
            .forbidden = &.{'a'},
            .must_allow = &.{'b'},
        },
        .{
            .name = "allOf-length-conjunction",
            .schema = "{\"allOf\":[{\"type\":\"string\",\"maxLength\":1}," ++
                "{\"type\":\"string\",\"maxLength\":2}]}",
            .prefix = "\"",
            .content_dep = true,
            .forbidden = &.{ 256, 257, 258 },
            .must_allow = &.{'a'},
        },
        // Nested: uniqueItems inside uniqueItems (two armed taps).
        .{
            .name = "uniqueItems-nested",
            .schema = "{\"type\":\"array\",\"items\":{\"type\":\"array\",\"items\":" ++
                "{\"type\":\"string\",\"maxLength\":1},\"uniqueItems\":true," ++
                "\"minItems\":2,\"maxItems\":2},\"minItems\":1,\"maxItems\":1}",
            .prefix = "[[\"a\",\"",
            .content_dep = true,
            .forbidden = &.{'a'},
        },
        // uniqueItems over objects: the string is nested below the captured
        // element, so the tap is still armed when it is typed.
        .{
            .name = "uniqueItems-over-object",
            .schema = "{\"type\":\"array\",\"items\":{\"type\":\"object\",\"properties\":" ++
                "{\"k\":{\"type\":\"string\",\"maxLength\":1}},\"required\":[\"k\"]," ++
                "\"additionalProperties\":false},\"uniqueItems\":true,\"minItems\":2,\"maxItems\":2}",
            .prefix = "[{\"k\":\"a\"},{\"k\":\"",
            .content_dep = true,
            .forbidden = &.{'a'},
        },
        // oneOf votes exactly-one at the boundary: the empty string is
        // accepted by both length branches, so the closing quote is refused
        // right after the opening one (while 'a' stays legal: the longer
        // branch can still take a second codepoint and win the vote alone).
        .{
            .name = "oneOf-overlapping-lengths",
            .schema = "{\"oneOf\":[{\"type\":\"string\",\"maxLength\":1}," ++
                "{\"type\":\"string\",\"maxLength\":2}]}",
            .prefix = "\"",
            .content_dep = true,
            .forbidden = &.{'"'},
        },
        // The provable oneOf shape: only the dog branch is alive after the
        // key dispatch, so the group verdict is that branch's acceptance and
        // the string content can only decide its own language. Tokens that
        // close the value and add a comma are refused by the object itself.
        .{
            .name = "oneOf-single-live-branch",
            .schema = "{\"oneOf\":[{\"type\":\"object\",\"properties\":{\"dog\":" ++
                "{\"type\":\"string\"}},\"required\":[\"dog\"],\"additionalProperties\":false}," ++
                "{\"type\":\"object\",\"properties\":{\"cat\":{\"type\":\"string\"}}," ++
                "\"required\":[\"cat\"],\"additionalProperties\":false}]}",
            .prefix = "{\"dog\":\"",
            .content_dep = false,
            .forbidden = &.{ 261, 262, 264 }, // close + comma: the dog object has no next key
            .must_allow = &.{ '"', 'a', 263 }, // close, content, and "ab\"" (close at token end)
        },
        // The same shape with both branches still live: the content can make
        // both accept (two votes) or kill one, so the exact path decides.
        .{
            .name = "oneOf-two-live-branches",
            .schema = "{\"oneOf\":[{\"type\":\"object\",\"properties\":{\"k\":" ++
                "{\"type\":\"string\",\"maxLength\":1}},\"required\":[\"k\"]," ++
                "\"additionalProperties\":false},{\"type\":\"object\",\"properties\":{\"k\":" ++
                "{\"type\":\"string\",\"maxLength\":2}},\"required\":[\"k\"]," ++
                "\"additionalProperties\":false}]}",
            .prefix = "{\"k\":\"",
            .content_dep = true,
            .forbidden = &.{'"'},
            .must_allow = &.{'a'},
        },
        // Outer oneOf has one live branch, the inner oneOf has two: the
        // inner conjunction keeps the state on the exact path.
        .{
            .name = "oneOf-inner-two-live",
            .schema = "{\"oneOf\":[{\"type\":\"object\",\"properties\":{\"dog\":" ++
                "{\"oneOf\":[{\"type\":\"string\",\"maxLength\":1}," ++
                "{\"type\":\"string\",\"maxLength\":2}]}},\"required\":[\"dog\"]," ++
                "\"additionalProperties\":false},{\"type\":\"object\",\"properties\":{\"cat\":" ++
                "{\"type\":\"string\"}},\"required\":[\"cat\"],\"additionalProperties\":false}]}",
            .prefix = "{\"dog\":\"",
            .content_dep = true,
            .forbidden = &.{'"'},
            .must_allow = &.{'a'},
        },
        // Outer oneOf has one live branch, the branch value is an allOf: a
        // conjunction is never accelerated, one live branch or not.
        .{
            .name = "oneOf-with-allOf-branch",
            .schema = "{\"oneOf\":[{\"type\":\"object\",\"properties\":{\"dog\":" ++
                "{\"allOf\":[{\"type\":\"string\",\"maxLength\":1}," ++
                "{\"type\":\"string\",\"maxLength\":2}]}},\"required\":[\"dog\"]," ++
                "\"additionalProperties\":false},{\"type\":\"object\",\"properties\":{\"cat\":" ++
                "{\"type\":\"string\"}},\"required\":[\"cat\"],\"additionalProperties\":false}]}",
            .prefix = "{\"dog\":\"",
            .content_dep = true,
            .forbidden = &.{ 256, 257, 258 },
            .must_allow = &.{'a'},
        },
        // Length boundaries on a content-independent state: the fast path
        // stays on; the close gate and the residual decide the bits.
        .{
            .name = "length-min-boundary",
            .schema = "{\"type\":\"string\",\"minLength\":2,\"maxLength\":3}",
            .prefix = "\"",
            .content_dep = false,
            .forbidden = &.{'"'},
            .must_allow = &.{'a'},
        },
        // Two alternative strings; the ASCII prefix of an exact token must
        // advance each thread on its own and drop the maxLength-0 thread.
        // "ab\"" would otherwise close through that thread (the close checks
        // minLength, not maxLength) and be allowed; the minLength-3 thread
        // refuses it, so the token must be absent.
        .{
            .name = "anyof-prefix-maxlength",
            .schema = "{\"anyOf\":[{\"type\":\"string\",\"maxLength\":0}," ++
                "{\"type\":\"string\",\"minLength\":3,\"maxLength\":5}]}",
            .prefix = "\"",
            .content_dep = false,
            .forbidden = &.{263}, // "ab\""
            .must_allow = &.{ '"', 'a' },
        },
        .{
            .name = "length-max-boundary",
            .schema = "{\"type\":\"string\",\"minLength\":2,\"maxLength\":3}",
            .prefix = "\"abc",
            .content_dep = false,
            .forbidden = &.{ 'a', 256, 257, 258, 259, 260 },
        },
        // Unicode: é is one codepoint, aé two; the residual counts
        // codepoints, not bytes.
        .{
            .name = "unicode-max-length-2",
            .schema = "{\"type\":\"string\",\"minLength\":1,\"maxLength\":2}",
            .prefix = "\"\xc3\xa9",
            .content_dep = false,
            .forbidden = &.{256}, // "ab" is two codepoints, R = 1
            .must_allow = &.{259}, // é is one codepoint
        },
        // Tokens crossing the string/separator boundary: decided by the
        // pruned exact walk, bit-for-bit.
        .{
            .name = "boundary-crossing-tokens",
            .schema = "{\"type\":\"array\",\"items\":{\"type\":\"string\",\"maxLength\":1}," ++
                "\"minItems\":1,\"maxItems\":3}",
            .prefix = "[\"",
            .content_dep = false,
            .forbidden = &.{ 263, 264 }, // ab" / aa", exceed the element bound
            .must_allow = &.{ 261, 262 }, // a", / "," finish the element and separate
        },
        .{
            .name = "boundary-close-reopen-token",
            .schema = "{\"type\":\"array\",\"items\":{\"type\":\"string\",\"maxLength\":1}," ++
                "\"minItems\":1,\"maxItems\":3}",
            .prefix = "[\"a",
            .content_dep = false,
            .forbidden = &.{261}, // a", would extend the element past its bound
            .must_allow = &.{262}, // "," closes the element and opens the next one
        },
    };

    for ([_]u32{ 0, 1 }) |mode| {
        for (cases) |case| {
            var on = makeConfig(mode);
            on.work_limit_ops = 1 << 60;
            var off = makeConfig(mode);
            off.work_limit_ops = 1 << 60;
            off.mask_fast_path = MASK_FAST_PATH_OFF;
            var w_on: [9]u32 = @splat(0);
            var w_off: [9]u32 = @splat(0);
            var ops_on: u64 = 0;
            var ops_off: u64 = 0;
            mask_diag.reset();
            try maskAtPrefixProfile(&on, &desc, "spec-v1", case.schema, case.prefix, &w_on, &ops_on);
            const used = mask_diag.c.fast_used.load(.monotonic);
            const refused = mask_diag.c.refuse_content_dep.load(.monotonic);
            try maskAtPrefixProfile(&off, &desc, "spec-v1", case.schema, case.prefix, &w_off, &ops_off);
            try std.testing.expectEqualSlices(u32, &w_off, &w_on);
            if (case.content_dep) {
                try std.testing.expectEqual(@as(u64, 0), used);
                try std.testing.expect(refused > 0);
            } else {
                try std.testing.expect(used > 0);
            }
            for (case.forbidden) |id| {
                if (maskBit(&w_on, id)) {
                    std.debug.print("case {s}: id {d} allowed, expected refused\n", .{ case.name, id });
                    return error.TestUnexpectedResult;
                }
            }
            for (case.must_allow) |id| {
                if (!maskBit(&w_on, id)) {
                    std.debug.print("case {s}: id {d} refused, expected allowed\n", .{ case.name, id });
                    return error.TestUnexpectedResult;
                }
            }
        }
    }
}

/// One name-state probe of lemma K1: bit-for-bit mask equality plus the
/// expected path and refusal counter.
const NameScopeCase = struct {
    name: []const u8,
    schema: []const u8,
    prefix: []const u8,
    expect: enum { name_fast, refuse_k1, refuse_enclosure },
    forbidden: []const u32 = &.{},
    must_allow: []const u32 = &.{},
};

test "abi: name-state lemma K1 - scope, boundaries and counters" {
    var entries: [267]TokenEntry = undefined;
    var blob: [1024]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildNameDesc(&entries, &blob, &bufs);

    const open = "{\"type\":\"object\"}";
    // A required declared key plus a count cap makes a free name a dead end
    // even though the object is open.
    const cap = "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"integer\"}}," ++
        "\"required\":[\"a\"],\"additionalProperties\":true,\"maxProperties\":1}";
    const no_cap = "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"integer\"}}," ++
        "\"required\":[\"a\"],\"additionalProperties\":true}";
    const closed = "{\"type\":\"object\",\"properties\":{\"alpha\":{\"type\":\"integer\"}," ++
        "\"beta\":{\"type\":\"string\"}},\"additionalProperties\":false}";

    const cases = [_]NameScopeCase{
        // K1 in scope: an open object with the compiler's unconstrained
        // undeclared-value node, at the start and mid-name.
        .{
            .name = "open-at-start",
            .schema = open,
            .prefix = "{\"",
            .expect = .name_fast,
            .forbidden = &.{ 0x01, 265 },
            .must_allow = &.{ 'a', '"', 260, 261, 257 },
        },
        .{
            .name = "open-mid-name",
            .schema = open,
            .prefix = "{\"ab",
            .expect = .name_fast,
            .forbidden = &.{ 0x01, 265 },
            .must_allow = &.{ 'a', '"', 256, 260 },
        },
        .{
            .name = "open-required-no-cap",
            .schema = no_cap,
            .prefix = "{\"",
            .expect = .name_fast,
            .must_allow = &.{ 'a', 'x', '"' },
        },
        // Counts and dependencies: a minimum is harmless for a free name
        // (more keys help), a dependency is not and refuses K1.
        .{
            .name = "minProperties",
            .schema = "{\"type\":\"object\",\"minProperties\":2}",
            .prefix = "{\"",
            .expect = .name_fast,
            .must_allow = &.{ 'a', 'x' },
        },
        .{
            .name = "dependentRequired",
            .schema = "{\"type\":\"object\",\"dependentRequired\":{\"a\":[\"b\"]}}",
            .prefix = "{\"",
            .expect = .refuse_k1,
        },
        .{
            .name = "dependentSchemas-capture",
            .schema = "{\"type\":\"object\",\"dependentSchemas\":{\"a\":{\"required\":[\"b\"]}}}",
            .prefix = "{\"",
            .expect = .refuse_k1,
        },
        // Capacity: the exact mask forbids a free name, K1 must stay off.
        .{
            .name = "maxProperties-at-start",
            .schema = cap,
            .prefix = "{\"",
            .expect = .refuse_k1,
            .forbidden = &.{'x'},
            .must_allow = &.{'a'},
        },
        .{
            .name = "maxProperties-inside",
            .schema = cap,
            .prefix = "{\"a",
            .expect = .refuse_k1,
            .forbidden = &.{'x'},
            .must_allow = &.{'"'},
        },
        // Closed object: the next declared key only, in order.
        .{
            .name = "closed-object",
            .schema = closed,
            .prefix = "{\"",
            .expect = .refuse_k1,
            .forbidden = &.{'x'},
            .must_allow = &.{'a'},
        },
        // propertyNames and patternProperties constrain the name; the
        // undeclared value must be read from the compiled node, so a
        // constrained additionalProperties or unevaluatedProperties refuses
        // K1 even though nothing else about the frame changes.
        .{
            .name = "propertyNames",
            .schema = "{\"type\":\"object\",\"propertyNames\":{\"maxLength\":3}}",
            .prefix = "{\"",
            .expect = .refuse_k1,
        },
        .{
            .name = "patternProperties",
            .schema = "{\"type\":\"object\",\"patternProperties\":{\"^x$\":" ++
                "{\"type\":\"integer\"}},\"additionalProperties\":true}",
            .prefix = "{\"",
            .expect = .refuse_k1,
        },
        .{
            .name = "additionalProperties-schema",
            .schema = "{\"type\":\"object\",\"additionalProperties\":{\"type\":\"integer\"}}",
            .prefix = "{\"",
            .expect = .refuse_k1,
            .must_allow = &.{'a'},
        },
        .{
            .name = "unevaluatedProperties",
            .schema = "{\"type\":\"object\",\"unevaluatedProperties\":{\"type\":\"integer\"}}",
            .prefix = "{\"",
            .expect = .refuse_k1,
            .must_allow = &.{'a'},
        },
        // Enclosure boundaries: a parent element capture (uniqueItems) and a
        // combinator with two live branches keep the exact path even though
        // every frame would satisfy K1 on its own.
        .{
            .name = "parent-capture",
            .schema = "{\"type\":\"array\",\"items\":{\"type\":\"object\"}," ++
                "\"uniqueItems\":true,\"minItems\":2,\"maxItems\":2}",
            .prefix = "[{\"",
            .expect = .refuse_enclosure,
        },
        .{
            .name = "oneOf-two-live-branches",
            .schema = "{\"oneOf\":[{\"type\":\"object\",\"properties\":{\"a\":" ++
                "{\"type\":\"integer\"}},\"required\":[\"a\"]},{\"type\":\"object\"," ++
                "\"properties\":{\"b\":{\"type\":\"integer\"}},\"required\":[\"b\"]}]}",
            .prefix = "{\"",
            .expect = .refuse_enclosure,
        },
        // Key machine boundaries: inside an escape and inside a UTF-8
        // sequence the content class does not apply.
        .{
            .name = "inside-escape",
            .schema = open,
            .prefix = "{\"a\\",
            .expect = .refuse_k1,
        },
        .{
            .name = "mid-utf8-2byte",
            .schema = open,
            .prefix = "{\"\xc3",
            .expect = .refuse_k1,
        },
        .{
            .name = "mid-utf8-4byte",
            .schema = open,
            .prefix = "{\"\xf0\x9f",
            .expect = .refuse_k1,
        },
    };

    for ([_]u32{ 0, 1 }) |mode| {
        for (cases) |case| {
            var on = makeConfig(mode);
            on.work_limit_ops = 1 << 60;
            var off = makeConfig(mode);
            off.work_limit_ops = 1 << 60;
            off.mask_fast_path = MASK_FAST_PATH_OFF;
            var w_on: [9]u32 = @splat(0);
            var w_off: [9]u32 = @splat(0);
            var ops_on: u64 = 0;
            var ops_off: u64 = 0;
            mask_diag.reset();
            try maskAtPrefixProfile(&on, &desc, "spec-v1", case.schema, case.prefix, &w_on, &ops_on);
            const name_used = mask_diag.c.name_used.load(.monotonic);
            const refuse_k1 = mask_diag.c.refuse_name_state.load(.monotonic);
            const refuse_enc = mask_diag.c.refuse_content_dep.load(.monotonic);
            const fast_used = mask_diag.c.fast_used.load(.monotonic);
            try maskAtPrefixProfile(&off, &desc, "spec-v1", case.schema, case.prefix, &w_off, &ops_off);
            try std.testing.expectEqualSlices(u32, &w_off, &w_on);
            switch (case.expect) {
                .name_fast => {
                    try std.testing.expect(name_used > 0);
                    try std.testing.expect(refuse_k1 == 0);
                },
                .refuse_k1 => {
                    try std.testing.expectEqual(@as(u64, 0), name_used);
                    try std.testing.expect(refuse_k1 > 0);
                    try std.testing.expectEqual(@as(u64, 0), fast_used);
                },
                .refuse_enclosure => {
                    try std.testing.expectEqual(@as(u64, 0), name_used);
                    try std.testing.expectEqual(@as(u64, 0), refuse_k1);
                    try std.testing.expect(refuse_enc > 0);
                    try std.testing.expectEqual(@as(u64, 0), fast_used);
                },
            }
            for (case.forbidden) |id| {
                if (maskBit(&w_on, id)) {
                    std.debug.print("case {s}: id {d} allowed, expected refused\n", .{ case.name, id });
                    return error.TestUnexpectedResult;
                }
            }
            for (case.must_allow) |id| {
                if (!maskBit(&w_on, id)) {
                    std.debug.print("case {s}: id {d} refused, expected allowed\n", .{ case.name, id });
                    return error.TestUnexpectedResult;
                }
            }
        }
    }
}

test "abi: max_workers does not change the mask (1 vs 8)" {
    var entries: [261]TokenEntry = undefined;
    var blob: [1024]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildByteCompleteDesc(&entries, &blob, &bufs);
    // maxLength 3 exercises the word-loop classification (R < CP_MAX is
    // impossible here: CP_MAX is 2, so bound R via maxLength 1).
    const schema_json = "{\"type\":\"string\",\"maxLength\":1}";
    var w1: [9]u32 = @splat(0);
    var w8: [9]u32 = @splat(0);
    var ops1: u64 = 0;
    var ops8: u64 = 0;
    var c1 = makeConfig(1);
    c1.work_limit_ops = 1 << 60;
    var c8 = makeConfig(1);
    c8.work_limit_ops = 1 << 60;
    c8.max_workers = 8;
    try maskAtStringState(&c1, &desc, schema_json, "\"", &w1, &ops1);
    try maskAtStringState(&c8, &desc, schema_json, "\"", &w8, &ops8);
    try std.testing.expectEqualSlices(u32, &w1, &w8);
    try std.testing.expectEqual(ops1, ops8); // worker-independent accounting
    // R = 1: "ab" (cp2) refused, "é" (cp1) allowed.
    try std.testing.expect(!maskBit(&w1, 256));
    try std.testing.expect(maskBit(&w1, 257));
}

// --- ADR-0014 rollback and draft validation ---

fn compileByteSchema(ctx: ?*Context, schema_json: []const u8, profile: [*:0]const u8) !*GrammarHandle {
    var err = makeTestError();
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = profile,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, blg_compile(ctx, &req, &gh, &err));
    return gh.?;
}

fn acceptBytes(s: ?*Session, bytes: []const u8) !void {
    var err = makeTestError();
    for (bytes) |b| try std.testing.expectEqual(Status.ok, blg_accept_token(s, b, &err));
}

test "abi: session rollback restores the mask and keeps max_rollback_tokens records" {
    var entries: [261]TokenEntry = undefined;
    var blob: [1024]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildByteCompleteDesc(&entries, &blob, &bufs);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    // An open object keeps its seen keys in side chunks: records must hold
    // chunk references, not copies.
    const gh = try compileByteSchema(ctx, "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"integer\"}},\"additionalProperties\":{\"type\":\"string\"}}", "spec-v1");

    var bad: SessionConfig = .{ .struct_size = 4, .max_rollback_tokens = 1 };
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.invalid_argument, blg_session_create_ex(ctx, gh, &bad, &s, &err));
    bad = .{ .struct_size = @sizeOf(SessionConfig), .max_rollback_tokens = MAX_ROLLBACK_TOKENS + 1 };
    try std.testing.expectEqual(Status.invalid_argument, blg_session_create_ex(ctx, gh, &bad, &s, &err));

    const sc: SessionConfig = .{ .struct_size = @sizeOf(SessionConfig), .max_rollback_tokens = 3 };
    try std.testing.expectEqual(Status.ok, blg_session_create_ex(ctx, gh, &sc, &s, &err));
    var m0: [9]u32 = @splat(0);
    var m2: [9]u32 = @splat(0);
    var m: [9]u32 = @splat(0);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m0, 9, &err));

    try acceptBytes(s, "{\"");
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m2, 9, &err));
    try acceptBytes(s, "k");
    try std.testing.expectEqual(Status.ok, blg_session_rollback(s, 3));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expectEqualSlices(u32, &m0, &m);
    try std.testing.expectEqual(Status.invalid_argument, blg_session_rollback(s, 1));

    // Five accepts with depth 3: the two oldest records are dropped.
    try acceptBytes(s, "{\"k\":");
    try std.testing.expectEqual(Status.invalid_argument, blg_session_rollback(s, 4));
    try std.testing.expectEqual(Status.ok, blg_session_rollback(s, 3));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expectEqualSlices(u32, &m2, &m);
    try std.testing.expectEqual(Status.ok, blg_session_rollback(s, 0));

    // Draft validation leaves the session where it is.
    const draft = [_]u32{ 'k', '"', ':', '"', 'v', '"', '}', 259 };
    var prefix: usize = 99;
    try std.testing.expectEqual(Status.ok, blg_validate_tokens(s, &draft, draft.len, &prefix));
    try std.testing.expectEqual(draft.len, prefix);
    const broken = [_]u32{ 'k', '"', 'x' };
    try std.testing.expectEqual(Status.ok, blg_validate_tokens(s, &broken, broken.len, &prefix));
    try std.testing.expectEqual(@as(usize, 2), prefix);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expectEqualSlices(u32, &m2, &m);

    try acceptBytes(s, "k\":\"v\"}");
    try std.testing.expectEqual(Status.ok, blg_accept_token(s, 259, &err));
    try std.testing.expectEqual(Status.ok, blg_session_rollback(s, 1));
    var ce = false;
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(ce);
    try std.testing.expectEqual(Status.ok, blg_finish(s, &err));
    try std.testing.expectEqual(Status.wrong_state, blg_session_rollback(s, 1));
    try std.testing.expectEqual(Status.wrong_state, blg_validate_tokens(s, &draft, draft.len, &prefix));
    blg_session_destroy(s);

    // Without a depth every rollback past zero is refused.
    try std.testing.expectEqual(Status.ok, blg_session_create(ctx, gh, &s, &err));
    try acceptBytes(s, "{");
    try std.testing.expectEqual(Status.invalid_argument, blg_session_rollback(s, 1));
    try std.testing.expectEqual(Status.ok, blg_session_rollback(s, 0));
    blg_session_destroy(s);

    var st = std.mem.zeroes(StatsC);
    st.struct_size = @sizeOf(StatsC);
    try std.testing.expectEqual(Status.ok, blg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 0), st.mem_used[2]);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

test "abi: a rollback record that does not fit the session budget fails the accept unchanged" {
    var entries: [261]TokenEntry = undefined;
    var blob: [1024]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildByteCompleteDesc(&entries, &blob, &bufs);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    const gh = try compileByteSchema(ctx, "{\"type\":\"array\",\"items\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}}}", "spec-v1");
    const sc: SessionConfig = .{ .struct_size = @sizeOf(SessionConfig), .max_rollback_tokens = MAX_ROLLBACK_TOKENS };
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create_ex(ctx, gh, &sc, &s, &err));
    var m0: [9]u32 = @splat(0);
    var m: [9]u32 = @splat(0);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m0, 9, &err));
    // Shrink the session budget to what is in use now.
    s.?.account.limit = s.?.account.usedBytes();
    try std.testing.expectEqual(Status.resource_limit, blg_accept_token(s, '[', &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expectEqualSlices(u32, &m0, &m);
    try std.testing.expectEqual(Status.invalid_argument, blg_session_rollback(s, 1));
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

// --- ADR-0015 standalone regex constraint ---

fn compileRegexReq(ctx: ?*Context, src: []const u8, profile: ?[*:0]const u8, flags: u32, gh: *?*GrammarHandle, err: *ZgError) Status {
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 2,
        .profile = profile,
        .data = src.ptr,
        .data_len = src.len,
        .flags = flags,
    };
    return blg_compile(ctx, &req, gh, err);
}

test "abi: regex constraint masks, refusals, rollback and validation" {
    var entries: [261]TokenEntry = undefined;
    var blob: [1024]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildByteCompleteDesc(&entries, &blob, &bufs);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));

    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.invalid_argument, compileRegexReq(ctx, "a+", "spec-v1", 0, &gh, &err));
    try std.testing.expectEqual(Status.unsupported_feature, compileRegexReq(ctx, "a+", null, COMPILE_PROBE, &gh, &err));
    try std.testing.expectEqual(Status.unsupported_feature, compileRegexReq(ctx, "a(?<=a)b", null, 0, &gh, &err));
    try std.testing.expectEqual(@as(u32, 1), err.schema_offset);
    try std.testing.expectEqual(Status.invalid_schema, compileRegexReq(ctx, "a(", null, 0, &gh, &err));
    try std.testing.expectEqual(Status.unsatisfiable_constraint, compileRegexReq(ctx, "[]", null, 0, &gh, &err));

    // Ids: byte b -> b, 256 "ab", 257 "\xc3\xa9", 258 "a\xc3\xa9", 259 eos.
    try std.testing.expectEqual(Status.ok, compileRegexReq(ctx, "a(?:b|\\u{e9})*", null, 0, &gh, &err));
    const sc: SessionConfig = .{ .struct_size = @sizeOf(SessionConfig), .max_rollback_tokens = 4 };
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create_ex(ctx, gh, &sc, &s, &err));
    var m0: [9]u32 = @splat(0);
    var m: [9]u32 = @splat(0);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m0, 9, &err));
    try std.testing.expect(maskBit(&m0, 'a'));
    try std.testing.expect(maskBit(&m0, 256));
    try std.testing.expect(maskBit(&m0, 258));
    try std.testing.expect(!maskBit(&m0, 'b'));
    try std.testing.expect(!maskBit(&m0, 259));
    try std.testing.expectEqual(Status.ok, blg_accept_token(s, 258, &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expect(maskBit(&m, 'b'));
    try std.testing.expect(maskBit(&m, 0xc3));
    try std.testing.expect(!maskBit(&m, 0xc4));
    try std.testing.expect(maskBit(&m, 259));
    try std.testing.expectEqual(Status.invalid_token, blg_accept_token(s, 'a', &err));
    try std.testing.expectEqual(Status.ok, blg_accept_token(s, 0xc3, &err));
    var ce = true;
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(!ce);
    try std.testing.expectEqual(Status.ok, blg_session_rollback(s, 2));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expectEqualSlices(u32, &m0, &m);

    const draft = [_]u32{ 'a', 'b', 257, 259 };
    var prefix: usize = 0;
    try std.testing.expectEqual(Status.ok, blg_validate_tokens(s, &draft, draft.len, &prefix));
    try std.testing.expectEqual(draft.len, prefix);
    const bad = [_]u32{ 'a', 'c' };
    try std.testing.expectEqual(Status.ok, blg_validate_tokens(s, &bad, bad.len, &prefix));
    try std.testing.expectEqual(@as(usize, 1), prefix);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expectEqualSlices(u32, &m0, &m);

    try std.testing.expectEqual(Status.ok, blg_accept_token(s, 'a', &err));
    try std.testing.expectEqual(Status.ok, blg_finish(s, &err));
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));

    // A vocabulary without every single byte is refused at compile time.
    const tc = try setupEnumCtx(0);
    var gh2: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.unsupported_tokenizer, compileRegexReq(tc.ctx, "a", null, 0, &gh2, &err));
    blg_grammar_release(tc.gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(tc.ctx));
}

// --- ADR-0016 structural-tag constraint ---

fn compileStructTagReq(ctx: ?*Context, spec: []const u8, profile: ?[*:0]const u8, flags: u32, gh: *?*GrammarHandle, err: *ZgError) Status {
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 3,
        .profile = profile,
        .data = spec.ptr,
        .data_len = spec.len,
        .flags = flags,
    };
    return blg_compile(ctx, &req, gh, err);
}

const structtag_abi_spec =
    "{\"type\":\"structural_tag\",\"format\":{\"type\":\"tags_with_separator\",\"separator\":\"\",\"at_least_one\":true,\"tags\":[{\"type\":\"tag\",\"begin\":\"<c>\",\"content\":{\"type\":\"json_schema\",\"json_schema\":{\"type\":\"integer\"}},\"end\":\"</c>\"}]}}";

test "abi: structural-tag constraint masks, rollback and validation" {
    var entries: [261]TokenEntry = undefined;
    var blob: [1024]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildByteCompleteDesc(&entries, &blob, &bufs);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));

    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.invalid_argument, compileStructTagReq(ctx, structtag_abi_spec, "spec-v1", 0, &gh, &err));
    try std.testing.expectEqual(Status.unsupported_feature, compileStructTagReq(ctx, structtag_abi_spec, null, COMPILE_PROBE, &gh, &err));
    try std.testing.expectEqual(Status.unsupported_feature, compileStructTagReq(ctx, "{\"type\":\"structural_tag\",\"format\":{\"type\":\"sequence\"}}", null, 0, &gh, &err));
    try std.testing.expectEqual(Status.invalid_schema, compileStructTagReq(ctx, "{", null, 0, &gh, &err));

    try std.testing.expectEqual(Status.ok, compileStructTagReq(ctx, structtag_abi_spec, null, 0, &gh, &err));
    const sc: SessionConfig = .{ .struct_size = @sizeOf(SessionConfig), .max_rollback_tokens = 8 };
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create_ex(ctx, gh, &sc, &s, &err));
    var m0: [9]u32 = @splat(0);
    var m: [9]u32 = @splat(0);
    var ce = true;
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(!ce); // at least one tag required
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m0, 9, &err));
    try std.testing.expect(maskBit(&m0, '<'));
    try std.testing.expect(!maskBit(&m0, 'x'));
    try std.testing.expect(!maskBit(&m0, 259));
    // A plain-text start is rejected without touching the session.
    try std.testing.expectEqual(Status.invalid_token, blg_accept_token(s, 'x', &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expectEqualSlices(u32, &m0, &m);
    // Feed "<c>" then the content; a wrong byte inside the content is
    // rejected with the state intact.
    try acceptBytes(s, "<c>");
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expect(maskBit(&m, '4'));
    try std.testing.expect(!maskBit(&m, 'a'));
    try std.testing.expect(!maskBit(&m, 259));
    try acceptBytes(s, "4");
    try std.testing.expectEqual(Status.invalid_token, blg_accept_token(s, 'a', &err));
    // Rollback inside the content restores the mask.
    try std.testing.expectEqual(Status.ok, blg_session_rollback(s, 1));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expect(maskBit(&m, '5'));
    try std.testing.expect(!maskBit(&m, 'a'));
    // Draft validation leaves the session where it is; the draft completes
    // the tag and may take the EOS, and a wrong byte cuts the draft.
    const draft = [_]u32{ '5', '<', '/', 'c', '>', 259 };
    var prefix: usize = 0;
    try std.testing.expectEqual(Status.ok, blg_validate_tokens(s, &draft, draft.len, &prefix));
    try std.testing.expectEqual(@as(usize, 6), prefix);
    const bad = [_]u32{ '5', 'x' };
    try std.testing.expectEqual(Status.ok, blg_validate_tokens(s, &bad, bad.len, &prefix));
    try std.testing.expectEqual(@as(usize, 1), prefix);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expect(maskBit(&m, '6'));
    // Complete the tag, then roll the whole session back to the start:
    // the mask matches the initial one bit for bit. Eight accepts were
    // committed ("<c>" 3, "4" 1, then five more); one was undone above.
    try acceptBytes(s, "5</c>");
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(ce);
    try std.testing.expectEqual(Status.ok, blg_session_rollback(s, 8));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expectEqualSlices(u32, &m0, &m);
    // Roll forward again and finish the session.
    try acceptBytes(s, "<c>6</c>");
    try std.testing.expectEqual(Status.ok, blg_finish(s, &err));
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));

    // A vocabulary without every single byte is refused at compile time.
    const tc = try setupEnumCtx(0);
    var gh3: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.unsupported_tokenizer, compileStructTagReq(tc.ctx, structtag_abi_spec, null, 0, &gh3, &err));
    blg_grammar_release(tc.gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(tc.ctx));
}

const structtag_abi_range_spec =
    "{\"type\":\"structural_tag\",\"format\":{\"type\":\"tags_with_separator\",\"separator\":\"\",\"at_least_one\":true,\"tags\":[{\"type\":\"tag\",\"begin\":\"<c>\",\"content\":{\"type\":\"json_schema\",\"json_schema\":{\"type\":\"integer\",\"minimum\":10,\"maximum\":42}},\"end\":\"</c>\"}]}}";

test "abi: structural-tag content completion filter, endable and the active segment" {
    var entries: [261]TokenEntry = undefined;
    var blob: [1024]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildByteCompleteDesc(&entries, &blob, &bufs);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));

    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, compileStructTagReq(ctx, structtag_abi_range_spec, null, 0, &gh, &err));
    const sc: SessionConfig = .{ .struct_size = @sizeOf(SessionConfig), .max_rollback_tokens = 8 };
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create_ex(ctx, gh, &sc, &s, &err));
    var m: [9]u32 = @splat(0);
    var ce = true;
    var phase: u8 = 0;
    var variant: u8 = 0;
    // The single tag variant is the only continuation: the segment query
    // reports it even at the boundary.
    try std.testing.expectEqual(Status.ok, blg_session_struct_segment(s, &phase, &variant));
    try std.testing.expectEqual(@as(u8, 1), phase); // begin
    try std.testing.expectEqual(@as(u8, 0), variant);
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(!ce);
    // NULL arguments are invalid; the segment query is read-only.
    try std.testing.expectEqual(Status.invalid_argument, blg_session_struct_segment(null, &phase, &variant));
    try std.testing.expectEqual(Status.invalid_argument, blg_session_struct_segment(s, null, &variant));
    try std.testing.expectEqual(Status.invalid_argument, blg_session_struct_segment(s, &phase, null));

    try acceptBytes(s, "<c>");
    try std.testing.expectEqual(Status.ok, blg_session_struct_segment(s, &phase, &variant));
    try std.testing.expectEqual(@as(u8, 2), phase); // JSON content
    try std.testing.expectEqual(@as(u8, 0), variant);

    // Dead-end prefixes of the content are filtered exactly like the
    // standalone JSON constraint: '-' and '9' can never satisfy
    // minimum 10 and maximum 42, so the mask withholds them, the draft
    // check cuts at them, and accept refuses them with the state intact.
    var m0: [9]u32 = @splat(0);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m0, 9, &err));
    try std.testing.expect(maskBit(&m0, '4'));
    try std.testing.expect(maskBit(&m0, '1'));
    try std.testing.expect(maskBit(&m0, '0')); // e.g. 0.42e2 = 42 stays reachable
    try std.testing.expect(!maskBit(&m0, '-'));
    try std.testing.expect(!maskBit(&m0, '9'));
    try std.testing.expect(!maskBit(&m0, 259)); // EOS: content open
    var prefix: usize = 0;
    const dead = [_]u32{ '-', '4' };
    try std.testing.expectEqual(Status.ok, blg_validate_tokens(s, &dead, dead.len, &prefix));
    try std.testing.expectEqual(@as(usize, 0), prefix);
    try std.testing.expectEqual(Status.invalid_token, blg_accept_token(s, '-', &err));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expectEqualSlices(u32, &m0, &m);

    // '4' is alive (40..42 remain reachable); after it '3' would make 43.
    try acceptBytes(s, "4");
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expect(maskBit(&m, '2'));
    try std.testing.expect(!maskBit(&m, '3'));
    const tail = [_]u32{ '2', '3' };
    try std.testing.expectEqual(Status.ok, blg_validate_tokens(s, &tail, tail.len, &prefix));
    try std.testing.expectEqual(@as(usize, 1), prefix);
    try acceptBytes(s, "2</c>");

    // A completed tag can end; a partial begin of the next one cannot
    // (EOS leaves the mask, accept refuses it, and rollback re-opens).
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(ce);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expect(maskBit(&m, 259));
    try std.testing.expect(maskBit(&m, '<'));
    try acceptBytes(s, "<");
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(!ce);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expect(!maskBit(&m, 259));
    try std.testing.expect(maskBit(&m, 'c'));
    try std.testing.expectEqual(Status.invalid_token, blg_accept_token(s, 259, &err));
    const draft_eos = [_]u32{ 'c', 259 };
    try std.testing.expectEqual(Status.ok, blg_validate_tokens(s, &draft_eos, draft_eos.len, &prefix));
    try std.testing.expectEqual(@as(usize, 1), prefix);
    try std.testing.expectEqual(Status.ok, blg_session_rollback(s, 1));
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(ce);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expect(maskBit(&m, 259));
    try std.testing.expectEqual(Status.ok, blg_session_struct_segment(s, &phase, &variant));
    try std.testing.expectEqual(@as(u8, 1), phase); // back to the tag boundary

    try acceptBytes(s, "<c>42</c>");
    try std.testing.expectEqual(Status.ok, blg_finish(s, &err));
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

const structtag_abi_digit_end_spec =
    "{\"type\":\"structural_tag\",\"format\":{\"type\":\"tags_with_separator\",\"separator\":\"\",\"at_least_one\":true,\"tags\":[{\"type\":\"tag\",\"begin\":\"<c>\",\"content\":{\"type\":\"json_schema\",\"json_schema\":{\"type\":\"integer\",\"minimum\":10,\"maximum\":42}},\"end\":\"99\"}]}}";

fn buildDigitEndDesc(entries: *[259]TokenEntry, blob: *[1024]u8, bufs: *[256][1]u8) TokenizerDesc {
    // All 256 single bytes + the two-byte piece "99" (id 256) + eos 257 +
    // special 258: the same closing bytes can be sampled as two tokens
    // (`9`, `9`) or as one (`99`).
    var tokens: [259][]const u8 = undefined;
    for (0..256) |b| {
        bufs[b][0] = @intCast(b);
        tokens[b] = bufs[b][0..1];
    }
    tokens[256] = "99";
    tokens[257] = "";
    tokens[258] = "<pad>";
    const eos = [_]u32{257};
    const special = [_]u32{258};
    return buildDesc(&tokens, &eos, &special, entries, blob);
}

test "abi: an end-literal prefix is offered by the completion filter (same bytes, two splits)" {
    // The digit starting the end literal is a dead-end JSON prefix when
    // taken alone (`42` + `9` = 429 is outside [10, 42]) but alive as the
    // end literal's first byte; the mask must offer it, accept and the
    // draft check must take it, rollback must re-open the partial literal,
    // and both token splits of the same bytes must reach the same state.
    var entries: [259]TokenEntry = undefined;
    var blob: [1024]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildDigitEndDesc(&entries, &blob, &bufs);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));

    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, compileStructTagReq(ctx, structtag_abi_digit_end_spec, null, 0, &gh, &err));
    const sc: SessionConfig = .{ .struct_size = @sizeOf(SessionConfig), .max_rollback_tokens = 8 };
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create_ex(ctx, gh, &sc, &s, &err));
    var m: [9]u32 = @splat(0);
    var m2: [9]u32 = @splat(0);
    var mc: [9]u32 = @splat(0);
    var ce = true;
    var prefix: usize = 0;
    const eos: u32 = 257;
    const t99: u32 = 256;

    // Before the content is completable the end literal cannot start:
    // after `<c>4` the byte `9` is a dead end (`4` alone is not a valid
    // integer, so no end-literal spawn) and stays out of the mask.
    try acceptBytes(s, "<c>4");
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expect(!maskBit(&m, '9'));
    try std.testing.expectEqual(Status.invalid_token, blg_accept_token(s, '9', &err));

    // `42` completes the content: now `9` is the end literal starting.
    try acceptBytes(s, "2");
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    try std.testing.expect(maskBit(&m, '9'));
    try std.testing.expect(maskBit(&m, t99));
    try std.testing.expect(maskBit(&m, '.'));
    try std.testing.expect(!maskBit(&m, eos)); // the tag is still open
    try std.testing.expectEqual(Status.invalid_token, blg_accept_token(s, '<', &err));
    // The draft check takes both end-literal bytes and cuts on a stray one.
    const tail = [_]u32{ '9', '9' };
    try std.testing.expectEqual(Status.ok, blg_validate_tokens(s, &tail, tail.len, &prefix));
    try std.testing.expectEqual(@as(usize, 2), prefix);
    const stray = [_]u32{ '9', 'x' };
    try std.testing.expectEqual(Status.ok, blg_validate_tokens(s, &stray, stray.len, &prefix));
    try std.testing.expectEqual(@as(usize, 1), prefix);

    // A partial literal cannot end the document and stays maskable; a
    // rollback re-opens the state byte for byte.
    try std.testing.expectEqual(Status.ok, blg_accept_token(s, '9', &err));
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(!ce);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m2, 9, &err));
    try std.testing.expect(maskBit(&m2, '9'));
    // `99` would overshoot the partial literal: its first byte closes the
    // tag and its second would have to start a new one.
    try std.testing.expect(!maskBit(&m2, t99));
    try std.testing.expect(!maskBit(&m2, eos));
    try std.testing.expectEqual(Status.ok, blg_session_rollback(s, 1));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m2, 9, &err));
    try std.testing.expectEqualSlices(u32, &m, &m2);

    // Split one: two single-byte tokens close the tag.
    try std.testing.expectEqual(Status.ok, blg_accept_token(s, '9', &err));
    try std.testing.expectEqual(Status.ok, blg_accept_token(s, '9', &err));
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(ce);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &mc, 9, &err));
    try std.testing.expect(maskBit(&mc, eos));
    try std.testing.expectEqual(Status.ok, blg_session_rollback(s, 2));
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m2, 9, &err));
    try std.testing.expectEqualSlices(u32, &m, &m2);

    // Split two: the same bytes as one token reach the same state.
    try std.testing.expectEqual(Status.ok, blg_accept_token(s, t99, &err));
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(ce);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m2, 9, &err));
    try std.testing.expect(maskBit(&m2, eos));
    try std.testing.expectEqualSlices(u32, &mc, &m2);
    try std.testing.expectEqual(Status.ok, blg_session_rollback(s, 1));
    try std.testing.expectEqual(Status.ok, blg_can_end(s, &ce));
    try std.testing.expect(!ce);

    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}

const structtag_abi_free_text_spec =
    "{\"type\":\"structural_tag\",\"format\":{\"type\":\"triggered_tags\"," ++
    "\"triggers\":[\"<c>\"],\"tags\":[{\"type\":\"tag\",\"begin\":\"<c>\"," ++
    "\"content\":{\"type\":\"json_schema\",\"json_schema\":{\"type\":\"integer\"}}," ++
    "\"end\":\"</c>\"}]}}";

fn buildLongTokenDesc(entries: *[259]TokenEntry, blob: *[2048]u8, bufs: *[256][1]u8) TokenizerDesc {
    // 256 single bytes + an 80-byte token "aa..a" (id 256) + eos 257 +
    // special 258. A free-text walk reaches the deepest token; frames
    // exist only at branching nodes, so the pool sized by max_stack must
    // survive it (the real Qwen tokenizer: 61-byte tokens, max_stack 59).
    var tokens: [259][]const u8 = undefined;
    for (0..256) |b| {
        bufs[b][0] = @intCast(b);
        tokens[b] = bufs[b][0..1];
    }
    var long: [80]u8 = undefined;
    @memset(long[0..], 'a');
    tokens[256] = long[0..];
    tokens[257] = "";
    tokens[258] = "<pad>";
    const eos = [_]u32{257};
    const special = [_]u32{258};
    return buildDesc(&tokens, &eos, &special, entries, blob);
}

test "abi: the free-text walk tolerates tokens deeper than the frame budget" {
    // A triggered tag's free text accepts every byte, so the mask walk
    // descends to the deepest vocabulary token. With one frame per byte
    // the walk outgrew the pool on the real Qwen tokenizer and the
    // release build aborted; frames exist only at branching nodes and
    // the single-child chains descend in place.
    var entries: [259]TokenEntry = undefined;
    var blob: [2048]u8 = undefined;
    var bufs: [256][1]u8 = undefined;
    const desc = buildLongTokenDesc(&entries, &blob, &bufs);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, blg_context_create(&cfg, &desc, &ctx, &err));
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, compileStructTagReq(ctx, structtag_abi_free_text_spec, null, 0, &gh, &err));
    const sc: SessionConfig = .{ .struct_size = @sizeOf(SessionConfig), .max_rollback_tokens = 0 };
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, blg_session_create_ex(ctx, gh, &sc, &s, &err));
    var m: [9]u32 = @splat(0);
    try std.testing.expectEqual(Status.ok, blg_fill_mask(s, &m, 9, &err));
    // Free text offers every non-special byte and the deep token.
    try std.testing.expect(maskBit(&m, 'a'));
    try std.testing.expect(maskBit(&m, 256));
    blg_session_destroy(s);
    blg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, blg_context_destroy(ctx));
}
