# API.md — точные внутренние сигнатуры Zig (обязательны к соблюдению)

Дополняет DESIGN.md. Если нужно отступление — меняется этот файл, затем код.

## src/parser.zig (agent B)

```zig
pub const MAX_DEPTH_CAP = 64;    // comptime ёмкость стека
pub const MAX_THREADS_CAP = 64;  // comptime ёмкость набора threads

pub const Frame = union(enum) {
    literal: struct { node: grammar.NodeId, off: u32 },
    str: StrFrame,
    int_v: NumFrame,
    num_v: NumFrame,
    seq: struct { node: grammar.NodeId, idx: u32 },
    repeat: struct { node: grammar.NodeId, count: u32, phase: RepeatPhase },
    object: struct { node: grammar.NodeId, idx: u32, cur: u32, phase: ObjPhase },
};
// StrFrame/NumFrame/RepeatPhase/ObjPhase — по DESIGN §1/§3.

pub const Thread = struct { frames: [MAX_DEPTH_CAP]Frame, len: u16 };
pub const State = struct {
    threads: [MAX_THREADS_CAP]Thread,
    n: u16,
    max_threads: u16,
};

pub fn initState(g: *const grammar.Grammar, max_threads: u16) error{ResourceLimit}!State;
pub fn feedBytes(g: *const grammar.Grammar, in: *const State, bytes: []const u8,
                 out: *State) error{ Parse, ResourceLimit }!void;
pub fn canEnd(g: *const grammar.Grammar, st: *const State) bool;
pub fn hashState(st: *const State) u64;
pub fn eqlStates(a: *const State, b: *const State) bool;
```

- Копирование State — только живые threads[0..n]; Frame/Thread — POD.
- initState раскрывает root (choice → несколько threads; literal len=0 — мгновенное
  завершение). Превышение max_threads — error.ResourceLimit.
- feedBytes не модифицирует in; out перезаписывается полностью.
- Ошибка Parse = ни один thread не принял байты.

## src/mask.zig (agent B)

```zig
pub const MaskBuf = struct {
    // Session-owned scratch обхода trie: по одному parser.State на ветвящийся
    // уровень + spare; выделяется лениво при первом fill, переиспользуется.
    pub fn init(a: std.mem.Allocator) MaskBuf;
    pub fn deinit(self: *MaskBuf) void;
};

pub fn fillMask(g: *const grammar.Grammar, tok: *const tokenizer.Tokenizer,
                st: *const parser.State, out: []u32, w: *work_mod.Work,
                buf: *MaskBuf) error{ DeadEnd, ResourceLimit, Cancelled, OutOfMemory }!void;
```

Семантика — DESIGN §4: итеративный DFS по trie токенов; каждое ребро — один
байт в feedBytes; Parse отсекает поддерево; терминал выставляет биты всех id
цепочки token_chain. out.len >= tok.maskWords() (лишние слова не трогаем).
Шаги обхода взимаются в `w` (отмена → Cancelled, лимит → ResourceLimit).
ResourceLimit/OutOfMemory в любом месте обхода — ошибка всей маски.
Детерминировано; повторный вызов на том же состоянии — побитово та же маска.

## src/alloc.zig (agent C)

```zig
pub const Category = enum(u8) { tokenizer, grammar, session, cache, temp };

pub const Accounting = struct {
    pub fn init(parent: std.mem.Allocator, total_limit: u64) Accounting;
    // allocator(cat) — учитывающий аллокатор категории; превышение total_limit
    // (с учётом всех категорий и дочерних SessionAccount) -> error.OutOfMemory.
    pub fn allocator(self: *Accounting, cat: Category) std.mem.Allocator;
    pub fn used(self: *const Accounting, cat: Category) u64;
    pub fn peak(self: *const Accounting, cat: Category) u64;
    pub fn totalUsed(self: *const Accounting) u64;
};

pub const SessionAccount = struct {
    pub fn init(parent: *Accounting, limit: u64) SessionAccount;
    // allocator(cat) — учитывающий аллокатор с собственным лимитом used <= limit;
    // каждое выделение также проходит через parent (учёт в общем лимите ядра).
    pub fn allocator(self: *SessionAccount, cat: Category) std.mem.Allocator;
    pub fn usedBytes(self: *const SessionAccount) u64;
};
```

## src/stats.zig (agent C)

```zig
pub const Stats = struct {
    compile_ns: u64 = 0,
    tokenizer_prepare_ns: u64 = 0,
    accept_ns_total: u64 = 0,
    mask_ns_total: u64 = 0,
    mask_calls: u64 = 0,
    tokens_accepted: u64 = 0,
    cache_hits: u64 = 0,
    cache_misses: u64 = 0,
    cache_evictions: u64 = 0,
    errors_total: u64 = 0,           // failed public calls (context paths)
    errors_resource_limit: u64 = 0,
    errors_cancelled: u64 = 0,
    cache_adaptive_skips: u64 = 0,   // маски, не принятые admission-политикой
    precompute_states: u64 = 0,      // маски, вычисленные прогревом при compile
    work_ops_total: u64 = 0,         // ops, взимённые против work_limit_ops
};
```

`fillMemory` раскладывает used/peak по индексам zg_stats
(tokenizer=0, grammar=1, session=2, cache=3, temp=4, total=5).

## src/cache.zig (agent C)

```zig
pub const Key = struct { grammar_id: u64, grammar_hi: u64, tokenizer_id: u64, state_hash: u64 };

pub const Cache = struct {
    pub fn init(a: std.mem.Allocator, byte_budget: u64) Cache; // budget==0 => выключен
    pub fn deinit(self: *Cache) void;
    // Совпадение: key равен И parser.eqlStates(state, сохранённое).
    // При попадании копирует маску в mask_out и возвращает true.
    pub fn get(self: *Cache, key: Key, state: *const parser.State, mask_out: []u32) bool;
    pub fn put(self: *Cache, key: Key, state: *const parser.State, mask: []const u32) void;
    // Счётчик обращений к ключу для admission-политики adaptive (FR-9);
    // 0 — бюджет выключен/переполнен (трактовать как «редко встречается»).
    pub fn bumpSeen(self: *Cache, key: Key) u64;
    pub fn usedBytes(self: *const Cache) u64;
    pub fn hits(self: *const Cache) u64;
    pub fn misses(self: *const Cache) u64;
    pub fn evictions(self: *const Cache) u64;
};
```

LRU; бюджет жёсткий: все выделения (Entry, состояние, маска, таблицы HashMap,
счётчики seen) идут через ограничивающий allocator (alloc.Limited), учтённые
байты никогда его не превышают. При нехватке вставка вытесняет хвост и
повторяется; если вытеснять нечего — запись не хранится (маска остаётся
вычислимой через lazy-путь). Решение о put принимает вызывающий (c_api):
admission при seen >= adaptive_min_hits ИЛИ compute_ns >= adaptive_min_cost_ns.
put/get детерминированы; кэш никогда не меняет содержимое маски.

## src/work.zig (agent C)

```zig
pub const Work = struct {
    limit: u64 = 0,                          // 0 = без лимита
    cancel: ?*align(1) const u8 = null,      // вызывательский атомарный байт
    ops: u64 = 0,
    pub fn init(limit: u64, cancel: ?*align(1) const u8) Work;
    pub fn charge(self: *Work, n: u64) error{ Cancelled, ResourceLimit }!void;
};
```

Per-call бюджет и отмена (NFR-2): один Work на публичный вызов compile/
fill_mask(s); флаг читается атомарно (acquire) при каждом charge; ненулевое
значение — Cancelled; превышение limit — ResourceLimit.

## src/coverage.zig (agent C)

```zig
pub fn checkCoverage(a: std.mem.Allocator, g: *const grammar.Grammar,
                     tok: *const tokenizer.Tokenizer,
                     w: *work_mod.Work) error{ OutOfMemory, Cancelled, ResourceLimit }!bool;
```

Compile-time проверка покрытия (ТЗ 3.1, FR-6): каждый литерал сегментируется
в токены словаря (DP по байтам), открытые классы (str/int_v/num_v) и
структурный синтаксис (`{}[]`, `,`) имеют порождаемое завершение. false —
ограничение не покрывается (c_api маппит в ZG_ERR_UNSUPPORTED_TOKENIZER).
Консервативно: границы токенов обязаны совпадать с границами узлов грамматики;
byte-полный словарь проходит всегда. Шаги взимаются в `w`.

## src/precompute.zig (agent C)

```zig
pub fn run(tmp_a: std.mem.Allocator, g: *const grammar.Grammar,
           tok: *const tokenizer.Tokenizer, c: *cache.Cache,
           mu: *std.Thread.Mutex, max_threads: u16, max_states: u64,
           w: *work_mod.Work) error{Cancelled}!u64;
```

Экспериментальный прогрев (FR-9): BFS по достижимым состояниям парсера с
вычислением масок и put в кэш, не более max_states состояний (возвращает число
обработанных). Исчерпание бюджета состояний, temp-памяти или работы — тихая
остановка (генерация продолжается лениво); наружу пробрасывается только
Cancelled. `mu` сериализует доступ к кэшу.

## src/c_api.zig + src/root.zig (agent D)

- root.zig: `pub const ... = @import(...)` всех модулей + экспорты c_api.
- tests регистрируются отдельно (src/tests.zig обновляет интеграция).
- Контекст/сессии/грамматики — по DESIGN §7 и include/zig_constraints.h.
- Дефолты конфига (поле == 0): memory 256 MiB, session 8 MiB,
  schema 1 MiB, max_depth 64, max_threads 64, work_limit_ops 0 (без лимита),
  adaptive_min_hits 2, adaptive_min_cost_ns 50000, precompute_max_states 4096.
  Особый случай — cache_limit_bytes: 0 выключает кэш, ZG_CACHE_DEFAULT
  (UINT64_MAX) — умолчание 64 MiB. cache_limit > memory_limit ->
  ZG_ERR_INVALID_ARGUMENT. struct_size/version невалидны -> ZG_ERR_INVALID_ARGUMENT.
  Хвостовые поля конфига читаются только при struct_size, их покрывающем
  (tail-grown versioning); legacy struct_size 56 принимается.
- Grammar handle: refcount; session держит ref; контекст считает живые
  грамматики и сессии; destroy занятого -> ZG_ERR_BUSY (и контекст не портится).
- zg_compile: после построения грамматики — coverage gate (coverage.zig);
  непокрываемое ограничение -> ZG_ERR_UNSUPPORTED_TOKENIZER. Ошибки компиляции
  несут json_pointer (RFC 6901) в zg_error.
- Режим lazy: mask всегда через mask.fillMask. adaptive: get Cache, при промахе
  fillMask + bumpSeen; put только при seen >= adaptive_min_hits ИЛИ
  compute_ns >= adaptive_min_cost_ns (иначе cache_adaptive_skips). Ключ кэша:
  {grammar.id, grammar.id_hi, tokenizer.identity, parser.hashState}; проверка
  eqlStates. precompute: adaptive + прогрев precompute.run при zg_compile
  (только при cache_budget > 0); исчерпание бюджета — тихая деградация в lazy,
  наружу -> только ZG_ERR_CANCELLED.
- Отмена и лимит работы: zg_cancel_flag_set(ctx, flag) (NULL — отсоединить);
  zg_compile/zg_fill_mask(s) создают Work(work_limit_ops, cancel). Отмена ->
  ZG_ERR_CANCELLED; превышение work_limit_ops -> ZG_ERR_RESOURCE_LIMIT
  (частичная маска не возвращается).
- zg_fill_mask: session active; mask != null; mask_words >= maskWords() иначе
  BUFFER_TOO_SMALL; выравнивание указателя проверить (@intFromPtr % 4).
- zg_accept_token: session active; id < vocab_size; EOS при canEnd ->
  tokens_accepted++, состояние неизменно, OK; special non-EOS -> INVALID_TOKEN;
  иначе feedBytes; Parse -> INVALID_TOKEN (состояние не меняется); успех ->
  состояние обновлено, tokens_accepted++.
- zg_finish: can_end иначе WRONG_STATE; finish/aborted сессии: accept/fill_mask
  -> WRONG_STATE; finish/abort идемпотентны по OK.
- zg_fill_masks_batch: последовательно; statuses[i] всегда записывается;
  count==0 -> ZG_OK; sessions==null при count>0 -> INVALID_ARGUMENT; строки
  независимы (статус одной не прерывает остальные); возвращается код первой
  ошибшейся строки, её диагностика копируется в zg_error вызывающего.
- zg_get_stats: zg_stats расширена аддитивно (mode, errors_total,
  errors_resource_limit, errors_cancelled, cache_adaptive_skips,
  precompute_states, work_ops_total); legacy struct_size 208 принимается,
  ядро не пишет за struct_size вызывающего.
- Любая ошибка Zig маппится; OutOfMemory -> RESOURCE_LIMIT; непредвиденное ->
  INTERNAL + сессия aborted (если ошибка в session-операции).
- Экспорт: `export fn zg_...(...) callconv(.c) ...` согласно заголовку.
- examples/c_client.c: создаёт контекст с крошечным ручным токенизатором,
  компилирует JSON Schema из примера ТЗ (action/amount), прогоняет несколько
  шагов маски/accept, печатает результат. Сборка: zig build example-c.

## Python-пакет (контракты поверх ABI)

- `TokenizerBundle.from_hf`: порядок декодирования токена — byte fallback
  `<0xNN>` (один байт) до byte-level таблицы; затем byte-level BPE
  (GPT-2/Qwen/Llama-3); затем added tokens (литеральный UTF-8); затем, при
  `byte_fallback=True`, SentencePiece (▁ (U+2581) → пробел, остальное —
  литеральный UTF-8). SentencePiece без `byte_fallback` и неизвестные схемы
  -> UnsupportedTokenizerError (молчаливой подстановки UTF-8 текста нет).
  Per-process кэш bundle: ключ (name_or_path, revision, отпечаток словаря и
  параметров декодирования) — разные словари с одним именем не делят bundle.
- `fill_masks_batch(sessions)`: дубликаты сессий и пересекающиеся батчи
  безопасны — _core дедуплицирует сессии по объекту и захватывает per-session
  блокировки без GIL в едином глобальном порядке (по указателю нативной
  сессии), с повторной валидацией под локами; батч с закрытой сессией ->
  WrongStateError. Строки независимы; при ошибке строки поднимается
  типизированное исключение первой ошибшейся строки.
- `zig_constraints.transformers.constrained_generate`: `completed[i]` True
  только при принятом разрешённом EOS и успешном `session.finish()`; остановка
  по длине без EOS -> completed=False, stop_reason="length". NaN/+inf среди
  разрешённых маской логитов -> ZigConstraintsError (не молчаливый сэмпл).
  Конфликты генерации проверяются по ЭФФЕКТИВНОМУ конфигу
  (model.generation_config + gen_kwargs, kwargs выигрывают, явный None
  снимает значение): num_beams, num_return_sequences, forced_eos_token_id,
  forced_decoder_ids -> UnsupportedModeError до генерации. Множество EOS —
  объединение tokenizer.eos_token_id и эффективного eos_token_id (int|list).

## Тесты в файлах

Каждый agent добавляет `test` блоки в свои файлы. src/tests.zig и src/root.zig
(кроме agent D) НЕ редактировать — регистрация на интеграции.
