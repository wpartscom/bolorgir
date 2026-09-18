# DESIGN.md — внутренние контракты zig-constraints (MVP)

Этот документ — обязательный контракт для всех модулей. Отклонения согласуются
через изменение этого файла, а не молча.

- Toolchain: **Zig 0.15.2** (`/home/gm/.local/bin/zig`), Python 3.10, gcc.
- Все исходники ядра — в `src/`, C ABI заголовок — `include/zig_constraints.h`.
- Стиль: без комментариев, объясняющих очевидное; snake_case; ошибки — через
  `error{...}` внутри Zig, через `zg_status` на ABI-границе. Ни один panic не
  должен пересекать C ABI: все экспортируемые функции оборачивают ошибки в коды.

## 0. Дерево файлов и владение

```
build.zig                     (написан, не трогать без необходимости)
include/zig_constraints.h     (написан; изменения только согласованно)
docs/DESIGN.md                (этот файл)
src/grammar.zig               (написан — IR грамматики; расширять, не ломая)
src/tokenizer.zig             (написан — тип Tokenizer + create; доработка: agent C)
src/json.zig                  (agent A) JSON-парсер схемы (свое дерево значений)
src/schema.zig                (agent A) компилятор JSON Schema -> Grammar
src/literals.zig              (agent A) компилятор FR-3 (список буквальных строк) -> Grammar
src/parser.zig                (agent B) инкрементный парсер (Thread/State)
src/mask.zig                  (agent B) построитель маски (обход trie токенов)
src/coverage.zig              (agent C) проверка покрытия токенизатора на compile
src/work.zig                  (agent C) per-call бюджет работы и флаг отмены
src/precompute.zig            (agent C) BFS-прогрев масок при compile (экспериментальный)
src/alloc.zig                 (agent C) учитывающий аллокатор
src/stats.zig                 (agent C) статистика
src/cache.zig                 (agent C) LRU-кэш масок с жёстким байтовым бюджетом и adaptive admission
src/root.zig                  (agent D) корень библиотеки, экспорт C ABI
src/c_api.zig                 (agent D) реализация C ABI
examples/c_client.c           (agent D) пример C-клиента без Python
python/...                    (agent E) Python-пакет и расширение
tests/...                     (agent F) эталон + parity/edge тесты
docs/*.md, benchmarks/...     (agent G) документация и бенчмарки
```

## 1. Семантика canonical-v1 (нормативно)

### 1.1. Строки

- Строка: `"` содержимое `"`. Содержимое: raw-байты >= 0x20, кроме `"` (0x22)
  и `\` (0x5C); UTF-8 multibyte — raw, валидируется DFA из п.1.3.
- Таблица экранирования (единственная допустимая):
  - `\"` `\\` `\/`?? — НЕТ: `/` экранировать запрещено. Допустимые escapes:
    `\"`, `\\`, `\b` (0x08), `\f` (0x0C), `\n` (0x0A), `\r` (0x0D), `\t` (0x09).
  - Прочие управляющие символы (0x00–0x07, 0x0B, 0x0E–0x1F): только `\u00xx`,
    xx — две **строчные** hex-цифры (0-9a-f), образующие код < 0x20, не имеющий
    короткого escape. Ровно форма `\u00xx`: первые две цифры обязаны быть `00`.
  - Любой другой escape (`\'`, `\u0041`, `\U`, hex верхнего регистра и т.п.) — ошибка разбора.
- Raw управляющие байты < 0x20 внутри строки — ошибка. 0x7F допустим raw.
- minLength/maxLength считаются в Unicode scalar values: ASCII-байт = 1;
  завершённый multibyte-символ = 1; завершённый escape (короткий или \u00xx) = 1.
- Превышение maxLength при инкременте счётчика — ошибка разбора (токен отклонён).

### 1.2. UTF-8 DFA (внутри строк)

Состояние: `rem` (0..3) + диапазон `[lo,hi]` для следующего байта.
- rem=0: байт 0x00–0x7F — символ завершён; 0xC2–0xDF → rem=1, [80,BF];
  0xE0 → rem=2, [A0,BF]; 0xE1–0xEC,0xEE,0xEF → rem=2, [80,BF];
  0xED → rem=2, [80,9F]; 0xF0 → rem=3, [90,BF]; 0xF1–0xF3 → rem=3, [80,BF];
  0xF4 → rem=3, [80,8F]. Прочие (0x80–0xC1, 0xF5–0xFF) — ошибка.
- rem>0: байт в [lo,hi] → rem-=1; если rem>0 — диапазон сбрасывается в [80,BF];
  если rem==0 — символ завершён. Иначе ошибка.
- Незавершённая последовательность между токенами допустима (состояние хранится);
  завершённый документ с rem>0 — недопустим (строка не закроется корректно:
  закрывающая кавычка при rem>0 — ошибка).

### 1.3. Числа

- integer: `-?(0|[1-9][0-9]*)`. Состояния: start → ('-' → minus; '0' → zero_complete;
  [1-9] → digits_complete). В zero_complete и digits_complete число может
  завершиться; цифра в zero_complete — НЕ продолжение (ведущие нули запрещены).
- number: integer-часть, затем опционально `.` [0-9]+ , затем опционально
  [eE][+-]?[0-9]+. Complete-состояния: после цифр int-части, после цифры
  дробной части, после цифры экспоненты.
- Завершение числа — «ленивое»: байт, не являющийся продолжением числа в
  complete-состоянии, завершает число (pop) и переобрабатывается родителем.
  В не-complete состоянии такой байт — ошибка.

### 1.4. Объекты (порядок ключей и исключение тупиков)

- Ключи идут в порядке объявления в `properties`; optional-ключи можно
  пропускать; пропущенный ключ вернуть нельзя.
- Frame объекта: `idx` — индекс первого неразрешённого свойства, `cur` —
  индекс выбранного свойства, фаза: open | key | value | sep.
  - open: ожидает `{` → фаза key.
  - key: при байте `"` кандидаты = { i >= idx : все props[j], idx<=j<i — optional }.
    На каждого кандидата — отдельный thread: cur=i, push literal `"key_i":`.
    При байте `}`: допустимо, только если все props[j], j>=idx — optional
    (т.е. не осталось required) → complete. Иной байт — ошибка.
  - value: после завершения key-литерала (childDone в фазе key) — push value-узла.
  - sep (после childDone в фазе value): idx=cur+1; байт `,` допустим, только
    если idx < len(props) (иначе это гарантированный trailing comma) → фаза
    key_after_comma; байт `}` → complete, если нет required j>=idx; иное — ошибка.
  - key_after_comma: как key, но байт `}` — ошибка: после запятой ключ
    обязателен. Trailing comma запрещён ВСЕГДА — и в объектах, и в массивах
    (п.1.5).
- Это правило конструктивно исключает тупик «пропущен required-ключ».

### 1.5. Массивы

- repeat-узел {item, min, max}; max может быть UNBOUNDED (maxInt(u32)).
- Фазы: open (`[`) → body: если count>=max — только `]`; если `]` — complete
  при count>=min, иначе ошибка; иначе push item (байт переобрабатывается item'ом).
- После childDone item'а: count+=1, фаза sep: `,` → body_after_comma (если
  count>=max — ошибка); `]` → complete при count>=min иначе ошибка; иное — ошибка.
- body_after_comma: как body, но `]` — ошибка: после запятой элемент
  обязателен. Trailing comma запрещён ВСЕГДА (как и в объектах, п.1.4).

### 1.6. Компиляция enum/const

- enum/const сериализуются в literal-альтернативы (choice из literal-узлов):
  - string → канонический JSON-литерал по таблице п.1.1;
  - boolean/null → `true`/`false`/`null`;
  - числа → точная десятичная нормализация lexeme из схемы БЕЗ binary64:
    парсинг в десятичную дробь (мантисса+экспонента как big-рациональное,
    допустимо ограничение: <= 400 значащих цифр, |записанная экспонента| <= 400 —
    превышение → InvalidSchema). Ноль в любой записи (включая `-0`, `-0.0`,
    `-0e7`) → `0`. Если значение целое → десятичная целая запись. Нецелое без
    экспоненты → запись int.frac: убрать ведущие нули int-части до одной цифры,
    убрать хвостовые нули дробной части. Нецелое С экспонентой → научная форма
    СОХРАНЯЕТСЯ: мантисса — как в схеме с той же нормализацией нулей (дробная
    часть, опустевшая после удаления хвостовых нулей, опускается вместе с
    точкой), экспонента нормализуется: 'E'→'e', без '+', без ведущих нулей.
    Примеры: `1e2`→`100`, `2.500e2`→`250`, `1.50`→`1.5`, `0.10`→`0.1`,
    `1.5e-3`→`1.5e-3`, `1.50E-3`→`1.5e-3`, `10e-3`→`10e-3`, `12.340e0`→`12.34e0`.
    Запись всегда соответствует грамматике number из п.1.3.
- Каждое значение enum проверяется против type и minLength/maxLength
  (несовместимые значения отбрасываются); если не осталось ни одного —
  `UnsatisfiableConstraint`. Дубликаты значений после нормализации удаляются.
- const эквивалентен enum из одного значения; enum и const одновременно в
  одной схеме → InvalidSchema (НЕ пересечение множеств).
- enum без явного type: выводится из значений (все одного типа; смешанный —
  UnsupportedFeature в MVP).

### 1.7. Схема: правила приёма (FR-1)

- Значение схемы — объект с поддерживаемым `type` либо чистой ссылкой `$ref`.
  Boolean-схемы → UnsupportedFeature.
- Поддерживаемые ключевые слова: type (ровно одна строка из 7), properties,
  required, additionalProperties (обязателен, ровно false; любое другое
  значение или отсутствие → InvalidSchema), items (обязателен
  для array, одна схема), minItems/maxItems, minLength/maxLength, enum, const,
  $defs, $ref, $schema (только идентификатор Draft 2020-12
  `https://json-schema.org/draft/2020-12/schema` с необязательным '#'),
  аннотации title/description/$comment/examples/default (игнорируются).
- Известные ключевые слова вне MVP (anyOf, oneOf, allOf, not, if, then, else,
  minimum, maximum, multipleOf, exclusiveMinimum, exclusiveMaximum, pattern,
  format, patternProperties, uniqueItems, contains, dependentSchemas,
  dependentRequired, unevaluatedProperties, propertyNames, minContains,
  maxContains, prefixItems, additionalItems, $id, $anchor, $dynamicRef,
  $recursiveRef, definitions, dependencies, contentEncoding, contentMediaType,
  contentSchema, readOnly, writeOnly, deprecated, const-рядом-с-$ref…) —
  UnsupportedFeature с JSON Pointer (byte offset в сообщении/поле ошибки).
  Любое другое неизвестное ключевое слово — тоже UnsupportedFeature.
- required: обязателен у object (может быть []), все имена — из properties,
  без дубликатов; properties обязателен (может быть {}).
- min>max (items/length) → UnsatisfiableConstraint. Неотрицательные целые,
  иначе InvalidSchema.
- $ref: только локальный `#` или `#/$defs/<name>` (pointer с ~0/~1
  экранированием); внешние ссылки и сетевые — UnsupportedFeature. Рядом с $ref
  допустимы только аннотации. Цикл ссылок → InvalidSchema (указать pointer).
- Глубина схемы/раскрытой грамматики > max_depth (по умолчанию 64) → ResourceLimit.
- Дубликаты ключей в JSON схемы → InvalidSchema. Размер схемы > лимита → ResourceLimit.

### 1.8. FR-3: буквальные альтернативы

Вход: JSON-массив строк UTF-8 (не JSON-экранированные значения — сами строки,
уже декодированные из JSON). Непустой. Дубликаты удаляются. Пустая строка
допустима (literal len=0). Компилируется в choice из raw-literal узлов;
одна альтернатива — просто literal. Полнота: ответ = ровно одна строка целиком.

### 1.9. Завершённость и тупики

В canonical-v1 после перечисленных compile-time проверок всякое состояние,
не являющееся ошибкой разбора, допускает завершение документа (строка всегда
может закрыться или добрать символы, число завершиться, скобки закрыться;
required-ключи нельзя пропустить благодаря п.1.4). Поэтому runtime-проверка
допустимости токена = «байты токена принимаются хотя бы одним thread'ом».
Если маска пуста (ни один токен и ни один EOS не разрешён) — ZG_ERR_DEAD_END.
Доказательство отсутствия тупиков фиксируется в docs/semantics.md.

## 2. IR грамматики (src/grammar.zig — уже написан)

См. файл. Узлы: literal / str / int_v / num_v / choice / seq / repeat / object.
Память грамматики — арена внутри Grammar; Grammar неизменяема после компиляции
и разделяется сессиями через refcount (в c_api). Identity — два независимых
64-битных хэша исходных байт схемы + байта kind (FNV-1a в `id`, Wyhash в
`id_hi`; заполняет компилятор): оба входят в ключ кэша, одновременная коллизия
(~2^-128) — принятый остаточный риск.

## 3. Парсер (src/parser.zig)

```zig
pub const MAX_DEPTH_DEFAULT = 64;
pub const Frame = union(enum) { ... };  // см. п.1.4/1.5/3.x
pub const Thread = struct { frames: []Frame (inline array cap max_depth), len: u16 };
pub const State = struct { threads: inline array of Thread, n: u16, max_threads: u16 };
```

Контракт:

- `State.init(g, max_threads, max_depth)` — один thread, корневой узел раскрыт
  (push_node: choice раскрывается в несколько threads сразу; literal len=0
  завершается мгновенно через after_child_done-цепочку).
- `feedBytes(g, in: *const State, bytes: []const u8, out: *State) error{Parse,ResourceLimit}!void`
  — out становится новым состоянием; in не меняется. Для каждого thread'а in —
  копия, посимвольная обработка; ошибка → thread отбрасывается; если ни один
  thread не выжил — error.Parse. Превышение max_threads при спавне →
  error.ResourceLimit (НЕ молчаливое отбрасывание — полнота важнее).
- Посимвольный цикл thread'а: см. псевдокод ниже. Ключевые правила:
  - число в complete-состоянии при «чужом» байте: pop + переобработка байта;
  - str завершается только байтом `"` в normal-состоянии (при count>=min);
  - after_child_done каскадно поднимается по стеку (seq idx++, repeat count/sep,
    object key→value / value→sep; завершившийся родитель — pop и продолжить каскад);
  - байт при пустом стеке — ошибка (контент после конца документа).
- `canEnd(g, state) bool` — существует thread, завершимый без байтов:
  стек пуст, ЛИБО каскад «вершина — число в complete-состоянии» виртуально
  схлопывается до пустого стека (через after_child_done-логику без байт).
- `hash(state) u64` (FNV-1a по всем живым frames всех threads, порядок threads
  значим и детерминирован) и `eql(a,b) bool` — для кэша и тестов.
- Thread/Frame — POD, копируются побитово; вся глубина <= max_depth.

Псевдокод посимвольного шага thread'а:

```
fn step(t, b):  // error.Parse при отказе; спавн threads — у feedBytes
  loop {
    if t.len == 0: return error.Parse
    f = top(t)
    switch (f.kind) {
      .literal => { bytes=...; if b!=bytes[f.off] return error.Parse;
                    f.off+=1; if f.off==len { pop; afterChild(t); } return; }
      .str     => { r=strFeed(f,b); if r==.err return error.Parse;
                    if r==.done { pop; afterChild(t); } return; }
      .int_v,.num_v => { r=numFeed(f,b);
                    if r==.consumed return;
                    if r==.complete_pop { pop; afterChild(t); continue; } // retry b
                    return error.Parse; }
      .repeat  => { switch phase: .open expect '['; .body — ']'? complete-or-err :
                    push item (continue, байт переобработается); .sep — ','→body_after_comma /
                    ']'→complete / else err; .body_after_comma — ']'→err, иначе push item;
                    complete: pop+afterChild; return }
      .object  => { open expect '{'; key — '"' → spawn candidates (см. 1.4),
                    '}' → complete-if-no-required; sep — ','→key_after_comma
                    (err, если свойств не осталось) / '}' аналогично;
                    key_after_comma — как key, но '}'→err; return }
      .seq,.choice => unreachable // seq существует как frame: см. ниже
    }
  }
```

seq-frame: при push_node(seq) — push frame {idx:0}, затем push_node(child[0]).
afterChild(seq): idx+=1; idx==n → pop+afterChild; иначе push_node(child[idx]).
push_node(choice): для каждой альтернативы — новый thread (копия стека) с
push_node(alt); первый остаётся в исходном thread'е. push_node(literal len=0):
считать мгновенно завершённым → afterChild.

strFeed состояния: normal | escape | u0 (ждём '0') | u00 (ждём второй '0') |
uhex1 | uhex2. В normal: `"`→done(проверка min), `\`→escape, байт<0x20→err,
иначе UTF-8 DFA (счётчик символов инкрементируется при завершении символа;
max-проверка при инкременте: count>max → err). escape: один из `"\/bfnrt`…
стоп: `/` НЕ экранируется — допустимы `"` `\` `b` `f` `n` `r` `t` `u`.
uhex: ровно `\u00xx`, xx — строчные hex, значение <0x20 и не из {08,09,0A,0C,0D}.

## 4. Маска (src/mask.zig)

```zig
pub fn fillMask(g: *const Grammar, tok: *const Tokenizer, st: *const parser.State,
                out: []u32, w: *work.Work, buf: *MaskBuf) !void
```

- out.len == (vocab_size+31)/32; бит t: слово t/32, бит t%32; 1 = разрешён.
- Хвостовые неиспользуемые биты последнего слова = 0.
- Алгоритм: обнулить; special non-EOS — пропуск; EOS-ids — бит при canEnd;
  обычные токены — итеративный DFS по префиксному дереву словаря (trie, п.5):
  каждое ребро trie скармливает один байт парсеру (feedBytes в стеке
  scratch-состояний MaskBuf), ошибка Parse отсекает всё поддерево, терминальный
  узел выставляет биты всех id с совпадающим байтовым образом (цепочка
  token_chain). Узел с одним ребром обходится без роста стека: глубина стека —
  ветвящаяся глубина trie, а не длина токена. Токен разрешён ⟺ feedBytes
  успешен на его байтах. Линейный перебор словаря сохранён в mask.zig только
  как эталон для тестов эквивалентности. Шаги обхода взимаются в `w`
  (отмена/лимит работы, п.7).
- Если ни один бит не установлен — error.DeadEnd (вызывающий получает код и
  состояние; автоматически ничего не разрешаем).
- Повторный fillMask на том же состоянии — побитово тот же результат.

## 5. Токенизатор (src/tokenizer.zig — тип уже написан)

- create(allocator_cat_tokenizer, desc) — копирует таблицу во владение
  контекста; валидация: id < vocab_size, id уникальны и покрывают
  0..vocab_size-1; обычный токен с len==0 → error.UnsupportedTokenizer;
  eos_ids ⊆ [0,vocab); special ∩ eos = ∅; байты токена — любые (включая
  незавершённый UTF-8 — решение принимает парсер).
- identity: FNV-1a-64 над: строкой тега адаптера (пока "zg-bbpe-v1"), всеми
  байтами всех токенов в порядке id, списком eos, списком special, vocab_size.
- trie: плоское префиксное дерево словаря (без поузловых выделений: рёбра узла —
  непрерывный срез общих массивов); строится в create. EOS, special и пустые
  токены в trie не входят (EOS — по canEnd, special запрещены). Разные id с
  одинаковым байтовым образом связываются в цепочку token_chain — маска
  выставляет биты всех совпадающих id. max_stack — ветвящаяся глубина дерева
  (один фрейм на предка с >= 2 рёбрами) для обхода в fillMask.
- maskWords() = (vocab_size+31)/32.

## 6. Память, статистика, кэш (agent C)

- alloc.zig: `Accounting` — над `std.mem.Allocator`: счётчики used/peak по
  категориям {tokenizer, grammar, session, cache, temp} + total; лимит total;
  `allocator(cat) std.mem.Allocator` (реализация через rawAlloc/resize/free
  обёртку со служебным заголовком категории). При превышении лимита выделение
  возвращает ошибку → ResourceLimit. После destroy контекста used[все]==0
  (проверяется тестами).
- stats.zig: `Stats` — поля как zg_stats в заголовке (ns-таймеры
  std.time.Timer, счётчики кэша, память из Accounting, tokens_accepted,
  счётчики ошибок по классам, cache_adaptive_skips, precompute_states,
  work_ops_total).
- cache.zig: LRU масок. Ключ: {grammar_id u64, grammar_hi u64, tokenizer_id u64,
  state_hash u64} (identity грамматики — 128 бит, п.2); значение: копия
  состояния (для проверки коллизий: сравнение parser.eql) + копия маски.
  Бюджет жёсткий: все выделения (Entry, состояние, маска, таблицы HashMap)
  идут через ограничивающий allocator (alloc.Limited), учтённые байты никогда
  его не превышают; при нехватке вытесняется LRU-хвост, запись больше бюджета
  не хранится. Adaptive admission (FR-9): обращения к ключу считаются
  bumpSeen (ограниченные счётчики в том же бюджете); put выполняет c_api
  только когда seen >= adaptive_min_hits ИЛИ compute_ns >=
  adaptive_min_cost_ns, иначе — cache_adaptive_skips. Промах/попадание/
  вытеснение — счётчики в Stats. Кэш не меняет маски (ТЗ T2: побитовый
  паритет). При cache_limit==0 кэш отключён — режим lazy-эквивалент.
- coverage.zig: проверка на zg_compile: каждый литерал грамматики сегментируется
  в токены словаря (DP по байтам), открытые классы (str/int/num) и структурный
  синтаксис имеют порождаемое завершение; иначе — UnsupportedTokenizer.
  Консервативно: границы токенов обязаны совпадать с границами узлов
  грамматики; byte-полный словарь проходит всегда.
- work.zig: `Work` — per-call бюджет (ops, 0 = без лимита) + указатель на
  вызывательский флаг отмены (атомарный байт, чтение acquire); charge()
  возвращает Cancelled/ResourceLimit. Один Work на публичный вызов
  compile/fill_mask(s); циклы парсера/trie/coverage/precompute взимают ops.
- precompute.zig: экспериментальный прогрев при zg_compile (режим precompute):
  BFS по достижимым состояниям парсера с вычислением масок и put в кэш, не
  более precompute_max_states состояний. Исчерпание бюджета состояний/памяти/
  работы — тихая остановка (генерация продолжается лениво); наружу
  пробрасывается только Cancelled.

## 7. C ABI (agent D) — соответствие include/zig_constraints.h

- Context: владеет Accounting, Tokenizer, Cache, mode, limits; реестр живых
  grammar/session (счётчики) — destroy занятого → ZG_ERR_BUSY.
- Grammar handle: refcount + *Grammar; release уменьшает; сессия держит ref.
- Session: parser.State + scratch State + статус active/finished/aborted +
  per-session accounting (лимит session_limit_bytes) + stats (accept/mask ns).
- zg_compile: после построения грамматики — coverage gate (п.6): непокрываемое
  токенизатором ограничение отклоняется ZG_ERR_UNSUPPORTED_TOKENIZER, тупиковые
  токены в маске невозможны. В режиме precompute (и включённом кэше) — прогрев
  масок (precompute.zig): ограниченный BFS до precompute_max_states состояний;
  исчерпание бюджета состояний/памяти/работы — не ошибка, генерация продолжается
  лениво; наружу пробрасывается только отмена флагом (ZG_ERR_CANCELLED).
- zg_fill_mask: проверки (session active, mask_words >= нужного, указатели
  выравнивания достаточного для u32); заполняет; DeadEnd → код ошибки, маска
  недействительна. В режимах adaptive/precompute — через cache (ключ по hash
  состояния, проверка eql); вычисленная маска попадает в кэш по политике
  admission (seen >= adaptive_min_hits ИЛИ compute_ns >= adaptive_min_cost_ns,
  иначе — cache_adaptive_skips).
- Отмена и лимит работы: zg_cancel_flag_set(ctx, flag) регистрирует
  вызывательский атомарный байт (NULL — отсоединить); zg_compile и
  zg_fill_mask(s) создают per-call Work(work_limit_ops, cancel). Отмена →
  ZG_ERR_CANCELLED; превышение work_limit_ops → ZG_ERR_RESOURCE_LIMIT
  (частичная маска не возвращается); в прогреве precompute превышение лимита
  работы — тихая остановка прогрева.
- zg_accept_token: token_id < vocab_size, иначе ZG_ERR_INVALID_TOKEN;
  бит должен быть разрешён: полная проверка feedBytes (НЕ доверяем ранее
  выданной маске — состояние могли не обновить); при отказе — InvalidToken,
  состояние не меняется. EOS при can_end → состояние остаётся active, can_end
  остаётся true; finish закрывает сессию. Специальный non-EOS → InvalidToken.
- zg_can_end, zg_finish (требует can_end иначе WRONG_STATE), zg_abort,
  zg_fill_masks_batch (последовательно; на строку — свой status; пустой батч OK;
  завершённые/абортированные сессии → их статус WRONG_STATE, остальные
  обрабатываются).
- Все строки диагностики — в вызывательский zg_error (struct_size валидируется;
  поле json_pointer — RFC 6901 указатель на узел схемы при ошибках компиляции,
  в буферах legacy-размера отсутствует). Глобального состояния ошибок нет.
- Ошибки Zig ловятся и маппятся; unreachable/panic в экспортах запрещены —
  любая непредвиденная ошибка → ZG_ERR_INTERNAL и (если состояние не гарант.)
  сессия помечается aborted.

## 8. Python (agent E)

- Мост: CPython C-расширение `zig_constraints._core` с **Limited API**
  (`Py_LIMITED_API=0x030A0000`), abi3. Python.h есть в /usr/include/python3.10.
  GIL освобождается вокруг нативных вызовов. ADR-0002 о выборе моста — agent E.
- Сборка: pyproject.toml (setuptools); build-шаг вызывает
  `/home/gm/.local/bin/zig build -Drelease=true` и копирует
  libzig_constraints.so в пакет; расширение линкуется с ней (rpath $ORIGIN).
- API: `Engine(mode=..., memory_limit_mb=..., cache_limit_mb=...)`,
  `engine.compile(schema: dict|str, tokenizer=None, profile="canonical-v1")`,
  `engine.compile_literals(list[str])`; Constraint.create_session();
  Session.fill_mask()->bytes, accept_token, can_end, finish/abort,
  контекстные менеджеры. Токенизатор: `TokenizerBundle.from_hf(hf_tokenizer)`
  (извлечение байтов: byte-level BPE через обратную таблицу bytes_to_unicode;
  byte fallback `<0xNN>` — до byte-level; SentencePiece с byte_fallback=True:
  ▁ → пробел; added tokens — литеральный UTF-8; SentencePiece без
  byte_fallback и неизвестная схема → UnsupportedTokenizerError без
  приближения). Исключения: по одному классу на zg_status.
- transformers.py (extra, импорт torch только внутри): LogitsProcessor +
  constrained_generate-обёртка (greedy/sampling, batch, left padding;
  completed=True только при принятом разрешённом EOS, без EOS — completed=False
  со stop_reason="length"; NaN/+inf среди разрешённых маской логитов —
  ZigConstraintsError; финальный accept последних токенов; конфликтующие
  настройки по эффективному generation_config (num_beams>1 и т.п.) →
  UnsupportedModeError до генерации).

## 9. Тесты (agent F)

- tests/reference.py — независимый эталон: свой компилятор схемы в генератор
  языка; для ограниченных схем (все длины/элементы ограничены) — полное
  перечисление документов (cap ~200k); oracle: строка-prefix допустима ⟺
  является префиксом некоторого документа языка; can_end ⟺ prefix ∈ язык.
- tests/conftest.py — мини-токенизаторы (побайтовый, словарный с
  многосимвольными токенами, vocab не кратный 32) и сборка C ABI через
  python-пакет (или ctypes напрямую к .so, если пакет не готов — но
  предпочтительно через пакет; координация через include/zig_constraints.h).
- parity: исчерпывающий перебор последовательностей токенов до глубины N на
  малых словарях — маска ядра vs oracle эталона (побитово); traces на больших
  схемах; режимы lazy/adaptive — побитовое совпадение.
- edge-кейсы: список из ТЗ T3 (vocab%32, пустой батч, невалидный ID, малый
  буфер, повторный EOS, PAD в активной сессии, мультисимвольные токены,
  незавершённый UTF-8, escapes на границах токенов, пустая строка, enum с
  общими префиксами, optional-ключи, число перед разделителем, min/max длины,
  пустое множество продолжений/DeadEnd).
- Финальные документы completed=true валидируются независимым валидатором
  (jsonschema, если pip-пакет доступен; иначе мини-валидатор в reference.py).

## 10. Коды ошибок (== include/zig_constraints.h)

OK, INVALID_ARGUMENT, INVALID_SCHEMA, UNSUPPORTED_FEATURE,
UNSATISFIABLE_CONSTRAINT, UNSUPPORTED_TOKENIZER, INVALID_TOKEN, DEAD_END,
RESOURCE_LIMIT, CANCELLED, BUSY, WRONG_STATE, BUFFER_TOO_SMALL, INTERNAL.
Маппинг Zig errors: Parse→INVALID_TOKEN/DEAD_END по месту, OutOfMemory→
RESOURCE_LIMIT, остальное явно.

## 11. Команды

```
/home/gm/.local/bin/zig build                 # libzig_constraints.so (zig-out/lib)
/home/gm/.local/bin/zig build test            # unit-тесты ядра
/home/gm/.local/bin/zig build example-c       # C-пример
python3 -m pytest tests/                      # интеграционные тесты
```

Режим сборки по умолчанию: Debug для разработки; распространение — ReleaseSafe
(фиксируется в benchmarks/manifest.json).
