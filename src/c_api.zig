const std = @import("std");
const grammar = @import("grammar.zig");
const tokenizer = @import("tokenizer.zig");
const schema = @import("schema.zig");
const literals = @import("literals.zig");
const parser = @import("parser.zig");
const mask = @import("mask.zig");
const alloc = @import("alloc.zig");
const stats = @import("stats.zig");
const cache = @import("cache.zig");
const coverage = @import("coverage.zig");
const work_mod = @import("work.zig");
const precompute = @import("precompute.zig");

pub const ABI_VERSION: u32 = 1;
pub const ERROR_MESSAGE_CAP = 256;
pub const JSON_POINTER_CAP = 256;
pub const STATS_CATEGORY_COUNT = 8;
pub const NO_OFFSET: u32 = std.math.maxInt(u32);

const DEFAULT_MAX_DEPTH: u32 = 64;
const DEFAULT_MAX_THREADS: u32 = 64;
const DEFAULT_MEMORY_LIMIT: u64 = 256 << 20;
const DEFAULT_CACHE_LIMIT: u64 = 64 << 20;
const DEFAULT_SESSION_LIMIT: u64 = 8 << 20;
const DEFAULT_SCHEMA_LIMIT: u64 = 1 << 20;
const DEFAULT_ADAPTIVE_MIN_HITS: u64 = 2;
const DEFAULT_ADAPTIVE_MIN_COST_NS: u64 = 50_000;
const DEFAULT_PRECOMPUTE_MAX_STATES: u64 = 4096;

/// Sentinel for zg_context_config.cache_limit_bytes: use the default
/// (64 MiB). The value 0 disables the cache instead.
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

// Layout sizes of the v1 structs (before the tail fields were appended).
// Callers built against the old header pass these in struct_size and get
// defaults for everything they do not know about.
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
};

const LEGACY_CONFIG_SIZE: u32 = 56;

comptime {
    std.debug.assert(@sizeOf(ContextConfig) == 88);
    std.debug.assert(@offsetOf(ContextConfig, "work_limit_ops") == 56);
    std.debug.assert(@offsetOf(ContextConfig, "adaptive_min_hits") == 64);
    std.debug.assert(@offsetOf(ContextConfig, "adaptive_min_cost_ns") == 72);
    std.debug.assert(@offsetOf(ContextConfig, "precompute_max_states") == 80);
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
};

pub const CompileRequest = extern struct {
    struct_size: u32,
    kind: u32,
    profile: ?[*:0]const u8,
    data: ?[*]const u8,
    data_len: usize,
};

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
};

const LEGACY_STATS_SIZE: u32 = 208;

comptime {
    std.debug.assert(@sizeOf(StatsC) == 264);
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
const ConstraintKind = enum { json_schema, literal_set };

pub const GrammarHandle = struct {
    ctx: *Context,
    g: grammar.Grammar,
    refcount: std.atomic.Value(u32),
};

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
    cancel_flag: ?*align(1) const u8,
    live_grammars: u32,
    live_sessions: u32,
    stats: stats.Stats,
    mutex: std.Thread.Mutex,
};

pub const Session = struct {
    ctx: *Context,
    gh: *GrammarHandle,
    account: alloc.SessionAccount,
    // Ping-pong parser states: accept feeds cur into cur^1 and flips the
    // index. Swapping whole State structs instead would memcpy ~240 KiB per
    // token (State is ~80 KiB).
    states: [2]parser.State,
    cur: u1,
    status: SessionStatus,
    stats: stats.Stats,
    mask_buf: mask.MaskBuf,
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
    e.code = @intFromEnum(code);
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

fn startTimer() ?std.time.Timer {
    return std.time.Timer.start() catch null;
}

// Reads a tail u64 field only when the caller's struct_size covers it.
fn cfgU64(c: *const ContextConfig, comptime field: []const u8) ?u64 {
    if (c.struct_size >= @offsetOf(ContextConfig, field) + @sizeOf(u64))
        return @field(c, field);
    return null;
}

fn elapsedNs(timer: *?std.time.Timer) u64 {
    if (timer.*) |*t| return t.read();
    return 0;
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
    Cancelled,
    OutOfMemory,
};

fn compileGrammar(ctx: *Context, kind: ConstraintKind, bytes: []const u8, diag: *schema.Diagnostic, w: *work_mod.Work) CompileError!grammar.Grammar {
    const ga = ctx.accounting.allocator(.grammar);
    var g: grammar.Grammar = switch (kind) {
        .json_schema => try schema.compile(ga, bytes, ctx.max_depth, diag, w),
        .literal_set => try literals.compileLiterals(ga, bytes, ctx.max_depth, diag, w),
    };
    errdefer g.deinit();
    // Coverage gate (TZ 3.1, FR-6): refuse constraints the tokenizer cannot
    // fully express instead of allowing dead-end tokens at generation time.
    const covered = coverage.checkCoverage(ctx.accounting.allocator(.temp), &g, &ctx.tok, w) catch |e| switch (e) {
        error.OutOfMemory => return error.OutOfMemory,
        error.Cancelled => return error.Cancelled,
        error.ResourceLimit => return error.ResourceLimit,
    };
    if (!covered) return error.UnsupportedTokenizer;
    return g;
}

fn mapCompileError(e: CompileError, diag: *const schema.Diagnostic, err: ?*ZgError) Status {
    const msg: []const u8 = if (diag.message_len > 0) diag.text() else switch (e) {
        error.InvalidSchema => "invalid schema",
        error.UnsupportedFeature => "unsupported schema feature",
        error.UnsatisfiableConstraint => "unsatisfiable constraint",
        error.ResourceLimit => "schema resource limit exceeded",
        error.UnsupportedTokenizer => "tokenizer does not cover the constraint",
        error.Cancelled => "cancelled",
        error.OutOfMemory => "out of memory during compile",
    };
    const code: Status = switch (e) {
        error.InvalidSchema => .invalid_schema,
        error.UnsupportedFeature => .unsupported_feature,
        error.UnsatisfiableConstraint => .unsatisfiable_constraint,
        error.ResourceLimit, error.OutOfMemory => .resource_limit,
        error.UnsupportedTokenizer => .unsupported_tokenizer,
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
    var timer = startTimer();
    const st = curState(s);
    if (ctx.mode != .lazy) {
        const key: cache.Key = .{
            .grammar_id = g.id,
            .grammar_hi = g.id_hi,
            .tokenizer_id = ctx.tok.identity,
            .state_hash = parser.hashState(st),
        };
        ctx.mutex.lock();
        const hit = ctx.cache.get(key, st, out);
        const seen: u64 = if (hit) 0 else ctx.cache.bumpSeen(key);
        ctx.mutex.unlock();
        if (!hit) {
            mask.fillMask(g, &ctx.tok, st, out, &w, &s.mask_buf) catch |e| return maskFail(err, e);
            const compute_ns = elapsedNs(&timer);
            // Adaptive admission (FR-9): cache masks of frequently hit or
            // expensive states; cheap one-shot masks stay out of the cache.
            if (ctx.cache.budget > 0) {
                ctx.mutex.lock();
                if (seen >= ctx.adaptive_min_hits or compute_ns >= ctx.adaptive_min_cost_ns) {
                    ctx.cache.put(key, st, out);
                } else {
                    ctx.stats.recordAdaptiveSkip();
                }
                ctx.mutex.unlock();
            }
        }
    } else {
        mask.fillMask(g, &ctx.tok, st, out, &w, &s.mask_buf) catch |e| return maskFail(err, e);
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

pub export fn zg_abi_version() callconv(.c) u32 {
    return ABI_VERSION;
}

pub export fn zg_context_create(
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
    if (config) |c| {
        // Tail-grown versioning: a smaller struct_size of a known legacy
        // layout is accepted, the missing fields take defaults.
        if (c.struct_size < LEGACY_CONFIG_SIZE or c.struct_size > @sizeOf(ContextConfig) or c.version != ABI_VERSION)
            return fail(err, .invalid_argument, "bad config struct_size or version");
        if (c.mode > 2)
            return fail(err, .invalid_argument, "unknown mode");
        mode = @enumFromInt(c.mode);
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
    }
    if (max_depth == 0 or max_depth > parser.MAX_DEPTH_CAP)
        return fail(err, .invalid_argument, "max_depth out of supported range");
    if (max_threads == 0 or max_threads > parser.MAX_THREADS_CAP)
        return fail(err, .invalid_argument, "max_threads_per_state out of supported range");
    if (cache_limit > memory_limit)
        return fail(err, .invalid_argument, "cache_limit_bytes exceeds memory_limit_bytes");

    const td = tdesc orelse return fail(err, .invalid_argument, "null tokenizer desc");
    if (td.struct_size != @sizeOf(TokenizerDesc))
        return fail(err, .invalid_argument, "bad tokenizer struct_size");
    if (td.entry_count > 0 and td.entries == null)
        return fail(err, .invalid_argument, "null tokenizer entries");
    if (td.blob_len > 0 and td.blob == null)
        return fail(err, .invalid_argument, "null tokenizer blob");
    if (td.eos_count > 0 and td.eos_ids == null)
        return fail(err, .invalid_argument, "null eos_ids");
    if (td.special_count > 0 and td.special_ids == null)
        return fail(err, .invalid_argument, "null special_ids");

    const root_alloc = std.heap.page_allocator;
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
    // memory_limit (FR-10); it is charged under the temp category.
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
    ctx.cancel_flag = null;
    ctx.live_grammars = 0;
    ctx.live_sessions = 0;
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
    tok_ready = true;
    ctx.stats.addTokenizerPrepareNs(elapsedNs(&timer));

    const cache_budget: u64 = if (mode == .lazy) 0 else cache_limit;
    ctx.cache = cache.Cache.init(ctx.accounting.allocator(.cache), cache_budget);
    cache_ready = true;

    outp.* = ctx;
    clearError(err);
    return .ok;
}

pub export fn zg_context_destroy(ctx_opt: ?*Context) callconv(.c) Status {
    const ctx = ctx_opt orelse return .invalid_argument;
    ctx.mutex.lock();
    const busy = ctx.live_grammars != 0 or ctx.live_sessions != 0;
    ctx.mutex.unlock();
    if (busy) return .busy;
    ctx.cache.deinit();
    ctx.tok.deinit();
    ctx.accounting.freeExternal(.temp, @sizeOf(Context));
    std.heap.page_allocator.destroy(ctx);
    return .ok;
}

pub export fn zg_compile(
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
    if (req.struct_size != @sizeOf(CompileRequest))
        return fail(err, .invalid_argument, "bad compile request struct_size");
    if (req.kind > 1)
        return fail(err, .invalid_argument, "unknown constraint kind");
    const kind: ConstraintKind = @enumFromInt(req.kind);
    if (req.profile) |p| {
        if (!std.mem.eql(u8, std.mem.span(p), "canonical-v1"))
            return fail(err, .unsupported_feature, "unsupported profile");
    }
    if (req.data_len > 0 and req.data == null)
        return fail(err, .invalid_argument, "null schema data");
    if (req.data_len > ctx.schema_limit)
        return fail(err, .resource_limit, "schema exceeds schema_limit_bytes");
    const bytes: []const u8 = if (req.data) |d| d[0..req.data_len] else &.{};

    var diag: schema.Diagnostic = .{};
    ctx.mutex.lock();
    const cancel = ctx.cancel_flag;
    ctx.mutex.unlock();
    var w = work_mod.Work.init(ctx.work_limit_ops, cancel);
    var timer = startTimer();
    const g = compileGrammar(ctx, kind, bytes, &diag, &w) catch |e| {
        const ns = elapsedNs(&timer);
        ctx.mutex.lock();
        ctx.stats.addCompileNs(ns);
        ctx.stats.addWorkOps(w.ops);
        ctx.mutex.unlock();
        const rc = mapCompileError(e, &diag, err);
        recordCtxErr(ctx, rc);
        return rc;
    };
    const ns = elapsedNs(&timer);

    const ga = ctx.accounting.allocator(.grammar);
    const h = ga.create(GrammarHandle) catch {
        var gg = g;
        gg.deinit();
        recordCtxErr(ctx, .resource_limit);
        return fail(err, .resource_limit, "grammar handle allocation failed");
    };
    h.* = .{ .ctx = ctx, .g = g, .refcount = std.atomic.Value(u32).init(1) };

    ctx.mutex.lock();
    ctx.live_grammars += 1;
    ctx.stats.addCompileNs(ns);
    ctx.mutex.unlock();

    // Experimental precompute (FR-9): bounded BFS mask warm-up. Budget or
    // memory exhaustion degrades to lazy silently; only cancel propagates.
    if (ctx.mode == .precompute and ctx.cache.budget > 0) {
        const n = precompute.run(
            ctx.accounting.allocator(.temp),
            &h.g,
            &ctx.tok,
            &ctx.cache,
            &ctx.mutex,
            ctx.max_threads,
            ctx.precompute_max_states,
            &w,
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
    ctx.mutex.unlock();
    outp.* = h;
    clearError(err);
    return .ok;
}

pub export fn zg_grammar_release(gh_opt: ?*GrammarHandle) callconv(.c) void {
    const gh = gh_opt orelse return;
    releaseGrammarRef(gh);
}

pub export fn zg_session_create(
    ctx_opt: ?*Context,
    gh_opt: ?*GrammarHandle,
    out_session: ?*?*Session,
    err: ?*ZgError,
) callconv(.c) Status {
    if (badErrBuf(err)) return .invalid_argument;
    const ctx = ctx_opt orelse return fail(err, .invalid_argument, "null context");
    const gh = gh_opt orelse return fail(err, .invalid_argument, "null grammar");
    const outp = out_session orelse return fail(err, .invalid_argument, "null out_session");
    outp.* = null;
    if (gh.ctx != ctx)
        return fail(err, .invalid_argument, "grammar belongs to another context");

    const st = parser.initState(&gh.g, ctx.max_threads) catch
        return fail(err, .resource_limit, "parser state init limit exceeded");

    var account = alloc.SessionAccount.init(&ctx.accounting, ctx.session_limit);
    const sa = account.allocator(.session);
    const s = sa.create(Session) catch
        return fail(err, .resource_limit, "session allocation failed");
    s.* = .{
        .ctx = ctx,
        .gh = gh,
        .account = account,
        .states = .{ st, undefined },
        .cur = 0,
        .status = .active,
        .stats = .{},
        .mask_buf = undefined,
    };
    s.mask_buf = mask.MaskBuf.init(s.account.allocator(.session));
    _ = gh.refcount.fetchAdd(1, .monotonic);
    ctx.mutex.lock();
    ctx.live_sessions += 1;
    ctx.mutex.unlock();
    outp.* = s;
    clearError(err);
    return .ok;
}

pub export fn zg_session_destroy(s_opt: ?*Session) callconv(.c) void {
    const s = s_opt orelse return;
    const ctx = s.ctx;
    releaseGrammarRef(s.gh);
    s.mask_buf.deinit();
    const sa = s.account.allocator(.session);
    sa.destroy(s);
    ctx.mutex.lock();
    ctx.live_sessions -= 1;
    ctx.mutex.unlock();
}

pub export fn zg_fill_mask(
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

pub export fn zg_fill_masks_batch(
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
        sts[i] = @intFromEnum(rc);
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

pub export fn zg_accept_token(
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

    var timer = startTimer();
    if (tok.is_eos[token_id]) {
        if (!parser.canEnd(&s.gh.g, curState(s)))
            return fail(err, .invalid_token, "EOS not allowed before document end");
    } else {
        parser.feedBytes(&s.gh.g, curState(s), tok.bytes[token_id], scratchState(s)) catch |e| switch (e) {
            error.Parse => return fail(err, .invalid_token, "token rejected by grammar"),
            error.ResourceLimit => return fail(err, .resource_limit, "parser thread limit exceeded"),
        };
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

pub export fn zg_can_end(s_opt: ?*const Session, out_can_end: ?*bool) callconv(.c) Status {
    const s = s_opt orelse return .invalid_argument;
    const o = out_can_end orelse return .invalid_argument;
    switch (s.status) {
        .active => {
            o.* = parser.canEnd(&s.gh.g, curState(s));
            return .ok;
        },
        .finished => {
            o.* = true;
            return .ok;
        },
        .aborted => return .wrong_state,
    }
}

pub export fn zg_finish(s_opt: ?*Session, err: ?*ZgError) callconv(.c) Status {
    if (badErrBuf(err)) return .invalid_argument;
    const s = s_opt orelse return fail(err, .invalid_argument, "null session");
    switch (s.status) {
        .finished => {
            clearError(err);
            return .ok;
        },
        .aborted => return fail(err, .wrong_state, "session aborted"),
        .active => {
            if (!parser.canEnd(&s.gh.g, curState(s)))
                return fail(err, .wrong_state, "document incomplete, can_end is false");
            s.status = .finished;
            clearError(err);
            return .ok;
        },
    }
}

pub export fn zg_abort(s_opt: ?*Session) callconv(.c) Status {
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
        @sizeOf(StatsC) => @sizeOf(StatsC),
        else => null,
    };
}

fn writeStats(o: *StatsC, full: *const StatsC) void {
    const n = statsCapacity(o).?;
    @memcpy(@as([*]u8, @ptrCast(o))[0..n], @as([*]const u8, @ptrCast(full))[0..n]);
    o.struct_size = @intCast(n);
}

pub export fn zg_get_stats(ctx_c: ?*const Context, out_stats: ?*StatsC) callconv(.c) Status {
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
    full.mode = @intFromEnum(ctx.mode);
    full.errors_total = ctx.stats.errors_total;
    full.errors_resource_limit = ctx.stats.errors_resource_limit;
    full.errors_cancelled = ctx.stats.errors_cancelled;
    full.cache_adaptive_skips = ctx.stats.cache_adaptive_skips;
    full.precompute_states = ctx.stats.precompute_states;
    full.work_ops_total = ctx.stats.work_ops_total;
    writeStats(o, &full);
    return .ok;
}

pub export fn zg_get_stats_session(s_opt: ?*const Session, out_stats: ?*StatsC) callconv(.c) Status {
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
    full.mode = @intFromEnum(s.ctx.mode);
    writeStats(o, &full);
    return .ok;
}

pub export fn zg_cancel_flag_set(ctx_opt: ?*Context, flag: ?*align(1) const u8) callconv(.c) Status {
    const ctx = ctx_opt orelse return .invalid_argument;
    ctx.mutex.lock();
    ctx.cancel_flag = flag;
    ctx.mutex.unlock();
    return .ok;
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

test "abi: version, context lifecycle, stats" {
    try std.testing.expectEqual(@as(u32, 1), zg_abi_version());
    const tokens = [_][]const u8{ "a", "1", "", "<pad>" };
    const eos = [_]u32{2};
    const special = [_]u32{3};
    var entries: [4]TokenEntry = undefined;
    var blob: [64]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(1);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u32, @sizeOf(StatsC)), st.struct_size);
    try std.testing.expectEqual(@as(u32, 0), st.tokens_accepted);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
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
    try std.testing.expectEqual(Status.invalid_argument, zg_context_create(&bad_size, &desc, &ctx, &err));
    try std.testing.expect(ctx == null);

    var bad_ver = makeConfig(0);
    bad_ver.version = 999;
    try std.testing.expectEqual(Status.invalid_argument, zg_context_create(&bad_ver, &desc, &ctx, &err));

    var bad_mode = makeConfig(7);
    try std.testing.expectEqual(Status.invalid_argument, zg_context_create(&bad_mode, &desc, &ctx, &err));

    var bad_cache = makeConfig(0);
    bad_cache.memory_limit_bytes = 100 << 20;
    bad_cache.cache_limit_bytes = 200 << 20;
    try std.testing.expectEqual(Status.invalid_argument, zg_context_create(&bad_cache, &desc, &ctx, &err));

    var bad_threads = makeConfig(0);
    bad_threads.max_threads_per_state = 1000;
    try std.testing.expectEqual(Status.invalid_argument, zg_context_create(&bad_threads, &desc, &ctx, &err));
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
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    errdefer _ = zg_context_destroy(ctx);
    const schema_json = "{\"type\":\"string\",\"enum\":[\"a\",\"b\"]}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));
    return .{ .ctx = ctx.?, .gh = gh.? };
}

test "abi: enum session flow, mask bits, errors" {
    for ([_]u32{ 0, 1, 2 }) |mode| {
        const tc = try setupEnumCtx(mode);
        const ctx = tc.ctx;
        var err = makeTestError();
        var s: ?*Session = null;
        try std.testing.expectEqual(Status.ok, zg_session_create(ctx, tc.gh, &s, &err));

        var words = [_]u32{0};
        try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
        try std.testing.expect(maskBit(&words, 0));
        try std.testing.expect(maskBit(&words, 1));
        try std.testing.expect(!maskBit(&words, 2));
        try std.testing.expect(!maskBit(&words, 3));

        try std.testing.expectEqual(Status.buffer_too_small, zg_fill_mask(s, &words, 0, &err));
        try std.testing.expectEqual(Status.invalid_token, zg_accept_token(s, 3, &err));
        try std.testing.expectEqual(Status.invalid_token, zg_accept_token(s, 99, &err));
        try std.testing.expectEqual(Status.wrong_state, zg_finish(s, &err));

        var ce = false;
        try std.testing.expectEqual(Status.ok, zg_can_end(s, &ce));
        try std.testing.expect(!ce);

        try std.testing.expectEqual(Status.ok, zg_accept_token(s, 0, &err));
        try std.testing.expectEqual(Status.ok, zg_can_end(s, &ce));
        try std.testing.expect(ce);

        try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
        try std.testing.expect(maskBit(&words, 2));

        try std.testing.expectEqual(Status.invalid_token, zg_accept_token(s, 3, &err));
        try std.testing.expectEqual(Status.ok, zg_accept_token(s, 2, &err));
        try std.testing.expectEqual(Status.ok, zg_finish(s, &err));
        try std.testing.expectEqual(Status.ok, zg_finish(s, &err));
        try std.testing.expectEqual(Status.wrong_state, zg_accept_token(s, 0, &err));
        try std.testing.expectEqual(Status.wrong_state, zg_fill_mask(s, &words, 1, &err));
        try std.testing.expectEqual(Status.ok, zg_abort(s));

        var sst = std.mem.zeroes(StatsC);
        try std.testing.expectEqual(Status.ok, zg_get_stats_session(s, &sst));
        try std.testing.expectEqual(@as(u64, 2), sst.tokens_accepted);
        try std.testing.expectEqual(@as(u64, 2), sst.mask_calls);

        zg_session_destroy(s);
        zg_grammar_release(tc.gh);
        try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
    }
}

test "abi: busy context" {
    const tc = try setupEnumCtx(0);
    const ctx = tc.ctx;
    var err = makeTestError();
    try std.testing.expectEqual(Status.busy, zg_context_destroy(ctx));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, tc.gh, &s, &err));
    zg_grammar_release(tc.gh);
    try std.testing.expectEqual(Status.busy, zg_context_destroy(ctx));
    zg_session_destroy(s);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
}

// Regression T4-fuzz (seed 0x5EED0001): concurrent compile/session/destroy
// corrupted alloc.Accounting counters; with atomic counters the totals must
// return to zero.
test "abi: concurrent compile/session accounting stays exact" {
    const tokens = [_][]const u8{ "{", "}", "\"a\":", "1", "" };
    const eos = [_]u32{4};
    const special = [_]u32{};
    var entries: [5]TokenEntry = undefined;
    var blob: [32]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(1);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));

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
                std.debug.assert(zg_compile(c, &req, &gh, &e) == .ok);
                var s: ?*Session = null;
                std.debug.assert(zg_session_create(c, gh, &s, &e) == .ok);
                var words = [_]u32{0};
                std.debug.assert(zg_fill_mask(s, &words, 1, &e) == .ok);
                std.debug.assert(zg_accept_token(s, 0, &e) == .ok);
                zg_session_destroy(s);
                zg_grammar_release(gh);
            }
        }
    };
    var threads: [4]std.Thread = undefined;
    for (&threads) |*t| t.* = try std.Thread.spawn(.{}, Worker.run, .{ ctx.?, 100 });
    for (threads) |t| t.join();

    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 0), st.mem_used[1]); // grammar
    try std.testing.expectEqual(@as(u64, 0), st.mem_used[2]); // session
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
}

test "abi: integer schema and batch" {
    const tokens = [_][]const u8{ "1", "2", "-", "" };
    const eos = [_]u32{3};
    const special = [_]u32{};
    var entries: [4]TokenEntry = undefined;
    var blob: [16]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(1);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    const schema_json = "{\"type\":\"integer\"}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = "canonical-v1",
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));

    var s1: ?*Session = null;
    var s2: ?*Session = null;
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s1, &err));
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s2, &err));

    var w1 = [_]u32{0};
    var w2 = [_]u32{0};
    const ss = [_]?*Session{ s1, s2 };
    const ms = [_]?[*]align(1) u32{ &w1, &w2 };
    var sts = [_]c_int{ -1, -1 };
    try std.testing.expectEqual(Status.ok, zg_fill_masks_batch(&ss, &ms, 1, &sts, 2, &err));
    try std.testing.expectEqual(@as(c_int, 0), sts[0]);
    try std.testing.expectEqual(@as(c_int, 0), sts[1]);
    try std.testing.expect(maskBit(&w1, 0));
    try std.testing.expect(maskBit(&w2, 1));

    try std.testing.expectEqual(Status.ok, zg_fill_masks_batch(null, null, 1, null, 0, &err));
    try std.testing.expectEqual(Status.invalid_argument, zg_fill_masks_batch(null, null, 1, null, 2, &err));

    try std.testing.expectEqual(Status.ok, zg_accept_token(s1, 0, &err));
    try std.testing.expectEqual(Status.ok, zg_finish(s1, &err));
    try std.testing.expectEqual(Status.wrong_state, zg_fill_masks_batch(&ss, &ms, 1, &sts, 2, &err));
    try std.testing.expectEqual(@as(c_int, @intFromEnum(Status.wrong_state)), sts[0]);
    try std.testing.expectEqual(@as(c_int, 0), sts[1]);

    zg_session_destroy(s1);
    zg_session_destroy(s2);
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
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
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    const lit_json = "[\"ab\",\"c\"]";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 1,
        .profile = null,
        .data = lit_json.ptr,
        .data_len = lit_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s, &err));
    try std.testing.expectEqual(Status.ok, zg_accept_token(s, 0, &err));
    try std.testing.expectEqual(Status.ok, zg_accept_token(s, 1, &err));
    var ce = false;
    try std.testing.expectEqual(Status.ok, zg_can_end(s, &ce));
    try std.testing.expect(ce);
    try std.testing.expectEqual(Status.ok, zg_finish(s, &err));
    zg_session_destroy(s);
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
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
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    var gh: ?*GrammarHandle = null;

    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = "exotic-v9",
        .data = null,
        .data_len = 0,
    };
    try std.testing.expectEqual(Status.unsupported_feature, zg_compile(ctx, &req, &gh, &err));

    req.profile = null;
    req.kind = 5;
    try std.testing.expectEqual(Status.invalid_argument, zg_compile(ctx, &req, &gh, &err));

    req.kind = 0;
    req.struct_size = 8;
    try std.testing.expectEqual(Status.invalid_argument, zg_compile(ctx, &req, &gh, &err));

    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
}

// Regression A1 (audit): vocab [a, <eos>] with literal "ab" must be refused
// at compile time (unsupported_tokenizer), not allowed to dead-end after
// accepting "a".
test "abi: compile rejects constraint the tokenizer cannot cover (audit A1)" {
    const tokens = [_][]const u8{ "a", "" };
    const eos = [_]u32{1};
    const special = [_]u32{};
    var entries: [2]TokenEntry = undefined;
    var blob: [8]u8 = undefined;
    const desc = buildDesc(&tokens, &eos, &special, &entries, &blob);
    var cfg = makeConfig(0);
    var err = makeTestError();
    var ctx: ?*Context = null;
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));

    const lit_json = "[\"ab\"]";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 1,
        .profile = null,
        .data = lit_json.ptr,
        .data_len = lit_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.unsupported_tokenizer, zg_compile(ctx, &req, &gh, &err));
    try std.testing.expect(gh == null);

    // Same grammar with a covering vocabulary compiles fine.
    const tokens2 = [_][]const u8{ "a", "b", "" };
    const eos2 = [_]u32{2};
    var entries2: [3]TokenEntry = undefined;
    var blob2: [8]u8 = undefined;
    const desc2 = buildDesc(&tokens2, &eos2, &special, &entries2, &blob2);
    var ctx2: ?*Context = null;
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc2, &ctx2, &err));
    try std.testing.expectEqual(Status.ok, zg_compile(ctx2, &req, &gh, &err));
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx2));
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
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
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s, &err));
    var words = [_]u32{0};
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 0));
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 0), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 0), st.cache_misses);
    zg_session_destroy(s);
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));

    // CACHE_DEFAULT => default 64 MiB budget: with adaptive_min_hits=1 the
    // second fill hits the cache.
    cfg.cache_limit_bytes = CACHE_DEFAULT;
    cfg.adaptive_min_hits = 1;
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s, &err));
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 1), st.cache_misses);
    zg_session_destroy(s);
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
}

// Regression A6 (audit): with cache_limit_bytes=82400 the bytes actually
// charged to the cache category must never exceed the budget.
test "abi: cache budget is a hard limit (audit A6)" {
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
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    const schema_json = "{\"type\":\"string\",\"enum\":[\"a\",\"b\"]}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s, &err));
    var words = [_]u32{0};
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 0));
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expect(st.mem_used[3] <= 82400); // cache category
    try std.testing.expect(st.mem_peak[3] <= 82400);
    zg_session_destroy(s);
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
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
    try std.testing.expectEqual(Status.ok, zg_context_create(@ptrCast(&lc), &desc, &ctx, &err));
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
    try std.testing.expectEqual(Status.invalid_schema, zg_compile(ctx, &req, &gh, @ptrCast(&le)));
    try std.testing.expectEqual(@as(i32, @intFromEnum(Status.invalid_schema)), le.code);

    // Legacy-size stats buffer: prefix is filled, struct_size preserved.
    var lst = std.mem.zeroes(LegacyStats);
    lst.struct_size = @sizeOf(LegacyStats);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, @ptrCast(&lst)));
    try std.testing.expectEqual(@as(u32, @sizeOf(LegacyStats)), lst.struct_size);
    try std.testing.expectEqual(@as(u32, ABI_VERSION), lst.version);

    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u32, 1), st.mode);
    try std.testing.expectEqual(@as(u64, 1), st.errors_total);
    try std.testing.expectEqual(@as(u64, 0), st.errors_cancelled);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
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
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s, &err));
    var words = [_]u32{0};
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 2), st.cache_misses);
    try std.testing.expectEqual(@as(u64, 1), st.cache_adaptive_skips);
    zg_session_destroy(s);
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));

    // Configurable thresholds: min_hits=1 admits on the first compute.
    cfg.adaptive_min_hits = 1;
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s, &err));
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 1), st.cache_misses);
    try std.testing.expectEqual(@as(u64, 0), st.cache_adaptive_skips);
    zg_session_destroy(s);
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));

    // min_cost_ns=1: any measured compute is expensive enough to admit.
    cfg = makeConfig(1);
    cfg.cache_limit_bytes = CACHE_DEFAULT;
    cfg.adaptive_min_cost_ns = 1;
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s, &err));
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 1), st.cache_misses);
    zg_session_destroy(s);
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
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
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expect(st.precompute_states >= 1);
    try std.testing.expectEqual(@as(u32, 2), st.mode);
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s, &err));
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 0));
    try std.testing.expect(maskBit(&words, 1));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 0), st.cache_misses);
    zg_session_destroy(s);
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));

    // Lazy: no warm-up, no cache activity at all.
    cfg = makeConfig(0);
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 0), st.precompute_states);
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s, &err));
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 0), st.cache_hits);
    try std.testing.expectEqual(@as(u64, 0), st.cache_misses);
    zg_session_destroy(s);
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
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
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    try std.testing.expectEqual(Status.ok, zg_cancel_flag_set(ctx, &flag));

    // Compile with the flag set is cancelled before any grammar work.
    @atomicStore(u8, &flag, 1, .release);
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.cancelled, zg_compile(ctx, &req, &gh, &err));
    try std.testing.expect(gh == null);
    @atomicStore(u8, &flag, 0, .release);
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));

    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s, &err));
    var words = [_]u32{0};
    @atomicStore(u8, &flag, 1, .release);
    try std.testing.expectEqual(Status.cancelled, zg_fill_mask(s, &words, 1, &err));
    @atomicStore(u8, &flag, 0, .release);
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 0));

    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 2), st.errors_cancelled);
    try std.testing.expect(st.errors_total >= 2);

    // Detaching the flag restores plain behavior.
    try std.testing.expectEqual(Status.ok, zg_cancel_flag_set(ctx, null));
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));

    zg_session_destroy(s);
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
    try std.testing.expectEqual(Status.invalid_argument, zg_cancel_flag_set(null, &flag));
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
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.resource_limit, zg_compile(ctx, &req, &gh, &err));
    try std.testing.expect(gh == null);
    var st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.errors_resource_limit);
    try std.testing.expect(st.work_ops_total > 0);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));

    // A limit that survives compile trips inside fill_mask. The schema is a
    // free-form string and the fillers are 30 eight-byte tokens sharing the
    // opening quote, so the trie walk explores ~210 nodes while
    // compile+coverage charges ~35; 128 lies in between.
    var toks: [32][]const u8 = undefined;
    var filler: [30][8]u8 = undefined;
    toks[0] = "\"";
    for (0..30) |i| {
        @memset(&filler[i], 100 + @as(u8, @intCast(i)));
        filler[i][0] = '"';
        toks[1 + i] = &filler[i];
    }
    toks[31] = "";
    const eos2 = [_]u32{31};
    var entries2: [32]TokenEntry = undefined;
    var blob2: [320]u8 = undefined;
    const desc2 = buildDesc(&toks, &eos2, &[_]u32{}, &entries2, &blob2);
    const str_schema = "{\"type\":\"string\"}";
    req.data = str_schema.ptr;
    req.data_len = str_schema.len;

    cfg = makeConfig(0);
    cfg.work_limit_ops = 128;
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc2, &ctx, &err));
    try std.testing.expectEqual(Status.ok, zg_compile(ctx, &req, &gh, &err));
    var s: ?*Session = null;
    try std.testing.expectEqual(Status.ok, zg_session_create(ctx, gh, &s, &err));
    var words: [1]u32 = .{0};
    try std.testing.expectEqual(Status.resource_limit, zg_fill_mask(s, &words, 1, &err));
    st = std.mem.zeroes(StatsC);
    try std.testing.expectEqual(Status.ok, zg_get_stats(ctx, &st));
    try std.testing.expectEqual(@as(u64, 1), st.errors_resource_limit);

    // Zero (unset) means unlimited.
    ctx.?.work_limit_ops = 0;
    try std.testing.expectEqual(Status.ok, zg_fill_mask(s, &words, 1, &err));
    try std.testing.expect(maskBit(&words, 0));
    zg_session_destroy(s);
    zg_grammar_release(gh);
    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
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
    try std.testing.expectEqual(Status.ok, zg_context_create(&cfg, &desc, &ctx, &err));

    const schema_json = "{\"type\":\"object\",\"properties\":{\"k0\":{\"type\":\"array\",\"items\":{\"type\":\"string\",\"pattern\":\"x\"}}},\"required\":[\"k0\"],\"additionalProperties\":false}";
    var req: CompileRequest = .{
        .struct_size = @sizeOf(CompileRequest),
        .kind = 0,
        .profile = null,
        .data = schema_json.ptr,
        .data_len = schema_json.len,
    };
    var gh: ?*GrammarHandle = null;
    try std.testing.expectEqual(Status.unsupported_feature, zg_compile(ctx, &req, &gh, &err));
    try std.testing.expectEqualStrings("/properties/k0/items", std.mem.span(@as([*:0]const u8, @ptrCast(&err.json_pointer))));

    const lit_json = "[\"ok\",1]";
    req.kind = 1;
    req.data = lit_json.ptr;
    req.data_len = lit_json.len;
    try std.testing.expectEqual(Status.invalid_schema, zg_compile(ctx, &req, &gh, &err));
    try std.testing.expectEqualStrings("/1", std.mem.span(@as([*:0]const u8, @ptrCast(&err.json_pointer))));

    try std.testing.expectEqual(Status.ok, zg_context_destroy(ctx));
}
