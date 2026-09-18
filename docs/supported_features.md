# supported_features.md — точная таблица поддержки MVP

Статус документа: нормативная таблица поддержки по ТЗ FR-1 (§5) и правилам
приёма схем из docs/DESIGN.md §1.7. «Отклоняется» означает штатную ошибку
компиляции до первого forward-прохода; движок никогда не продолжает без
ограничений незаметно (ТЗ FR-16).

## 1. Профиль JSON Schema (FR-1), профиль сериализации canonical-v1

Вход: UTF-8 JSON, профиль Draft 2020-12. Неизвестные проверочные ключевые слова
вызывают `ZG_ERR_UNSUPPORTED_FEATURE` с JSON Pointer в `zg_error.json_pointer`
и byte offset в `zg_error.schema_offset`.

| Элемент ТЗ FR-1 | Статус MVP | Поведение |
|---|---|---|
| `type` | Поддержано | Ровно одна строка: `object`, `array`, `string`, `integer`, `number`, `boolean`, `null`. Массив типов — `UNSUPPORTED_FEATURE`. |
| `properties`, `required` | Поддержано | Оба обязательны у каждого объектного узла (`properties` может быть `{}`, `required` может быть `[]`). Имена в `required` — только из `properties`, без дубликатов, иначе `INVALID_SCHEMA`. |
| `additionalProperties` | Поддержано | Для `object` обязателен явно `false`. Иное значение/отсутствие — `INVALID_SCHEMA`. |
| `items` | Поддержано | Обязателен для `array`; одна поддерживаемая схема для всех элементов. |
| `minItems`, `maxItems` | Поддержано | Неотрицательные целые, иначе `INVALID_SCHEMA`; `min > max` — `UNSATISFIABLE_CONSTRAINT`. Отсутствие `maxItems` — без семантического верхнего предела. |
| `minLength`, `maxLength` | Поддержано | Длина декодированного значения в Unicode scalar values (см. semantics.md §2.3); `min > max` — `UNSATISFIABLE_CONSTRAINT`. |
| `enum`, `const` | Поддержано | Скалярные значения; проверяются совместно с `type` и длиной; несовместимые значения отбрасываются, пустой остаток — `UNSATISFIABLE_CONSTRAINT`. `enum` и `const` одновременно в одной схеме — `INVALID_SCHEMA`. Числа сравниваются точно, без binary64; нормализация — semantics.md §6 (научная форма нецелых с экспонентой сохраняется, `-0` → `0`). Смешанный enum без `type` — `UNSUPPORTED_FEATURE`. |
| `$defs`, `$ref` | Поддержано | Только локальные ссылки `#`, `#/$defs/<name>` (pointer с `~0`/`~1`). Ациклический граф: цикл — `INVALID_SCHEMA` с pointer. Рядом с `$ref` допустимы только аннотации; проверочные ключевые слова рядом — `UNSUPPORTED_FEATURE`. |
| `$schema` | Поддержано | Допускается идентификатор Draft 2020-12 (`https://json-schema.org/draft/2020-12/schema` с необязательным `#`); другой диалект — `INVALID_SCHEMA`. |
| `title`, `description`, `$comment`, `examples`, `default` | Поддержано (аннотации) | Не влияют на маску; `default` не вставляет значение. |
| `anyOf`, `oneOf`, `allOf`, `not`, `if`/`then`/`else` | Вне MVP | `UNSUPPORTED_FEATURE` с указателем. |
| `minimum`, `maximum`, `multipleOf`, `exclusiveMinimum`, `exclusiveMaximum` | Вне MVP | `UNSUPPORTED_FEATURE` (не игнорируются). |
| `pattern`, `format`, `patternProperties` | Вне MVP | `UNSUPPORTED_FEATURE`. |
| `uniqueItems`, `contains`, `dependentSchemas`, `unevaluatedProperties` | Вне MVP | `UNSUPPORTED_FEATURE`. |
| `dependentRequired`, `propertyNames`, `minContains`, `maxContains`, `prefixItems`, `additionalItems`, `$id`, `$anchor`, `$dynamicRef`, `$recursiveRef`, `definitions`, `dependencies`, `contentEncoding`, `contentMediaType`, `contentSchema`, `readOnly`, `writeOnly`, `deprecated` | Вне MVP | `UNSUPPORTED_FEATURE`. |
| Любое другое неизвестное ключевое слово | Вне MVP | `UNSUPPORTED_FEATURE`. |
| Внешние ссылки и загрузка схем по сети | Не поддерживается | `UNSUPPORTED_FEATURE`. |
| Boolean-схемы (`true`/`false` как значение схемы) | Вне MVP | `UNSUPPORTED_FEATURE`. |

Дополнительные правила приёма (DESIGN §1.7):

- Каждое значение схемы — объект с поддерживаемым `type` либо чистой локальной
  ссылкой `$ref`.
- Дубликаты ключей в JSON схемы — `INVALID_SCHEMA`.
- Размер схемы > `schema_limit_bytes` — `RESOURCE_LIMIT`.
- Глубина схемы/раскрытой грамматики > `max_depth` — `RESOURCE_LIMIT`.
- Десятичные литералы enum/const с > 400 значащими цифрами или |экспонента|
  > 400 — `INVALID_SCHEMA` (semantics.md §6).

## 2. Профили сериализации

| Профиль | Статус |
|---|---|
| `canonical-v1` | Единственный профиль MVP; значение по умолчанию (`profile = NULL` == canonical-v1). Нормативная семантика — docs/semantics.md. |
| Прочие значения `profile` | `UNSUPPORTED_FEATURE`. |

## 3. Форматы ограничений (zg_constraint_kind)

| Формат | Статус |
|---|---|
| `ZG_CONSTRAINT_JSON_SCHEMA` | Поддержано (таблица §1). |
| `ZG_CONSTRAINT_LITERAL_SET` | Поддержано (FR-3): непустой JSON-массив строк UTF-8; общие префиксы, пустая строка, Unicode; дубликаты удаляются. Не смешивается с regex. |
| Произвольная CFG/EBNF, пользовательские regex | Вне MVP (план 1.1) | `UNSUPPORTED_FEATURE`. |

## 4. Режимы вычисления (zg_mode)

| Режим | Статус | Семантика |
|---|---|---|
| `ZG_MODE_LAZY` | Поддержано | Вычисление маски по текущему состоянию, без кэша. Эталонный точный путь. |
| `ZG_MODE_ADAPTIVE` | Поддержано | Тот же точный алгоритм + LRU-кэш масок с жёстким байтовым бюджетом и политикой admission: вычисленная маска кэшируется, только если состояние встречалось >= `adaptive_min_hits` раз (по умолчанию 2) ИЛИ её вычисление заняло >= `adaptive_min_cost_ns` нс (по умолчанию 50000); дешёвые одноразовые маски в кэш не попадают (учёт — `zg_stats.cache_adaptive_skips`). Маски побитово совпадают с lazy (паритет — ТЗ T2). |
| `ZG_MODE_PRECOMPUTE` | Экспериментальный | Adaptive + прогрев при `zg_compile`: BFS по достижимым состояниям парсера с вычислением и кэшированием масок, не более `precompute_max_states` состояний (по умолчанию 4096; учёт — `zg_stats.precompute_states`). Исчерпание бюджета состояний/памяти/работы — не ошибка: прогрев останавливается, генерация продолжается лениво; наружу из прогрева пробрасывается только отмена флагом (`ZG_ERR_CANCELLED`). Прогрев выполняется только при включённом кэше (`cache_limit_bytes != 0`). |

Отключение кэша (`cache_limit_bytes = 0` в adaptive) меняет производительность,
но не язык и не маски.

## 5. Платформы и токенизаторы

| Элемент | Статус MVP |
|---|---|
| Платформа | Linux x86_64 (glibc). Остальные платформы — после отдельных проверок; дизайн без заведомой привязки к x86. |
| Python | CPython 3.10+, обычный GIL; расширение через Limited API (abi3, цель `Py_LIMITED_API=0x030A0000`). |
| Токенизаторы | Две семьи: byte-level BPE (GPT-2/Qwen/Llama-3 стиль; строки словаря отображаются в байты обратной таблицей bytes_to_unicode) и токенизатор с byte fallback (`<0xNN>` декодируется в один байт и проверяется до byte-level таблицы; включая SentencePiece с `byte_fallback=True`, где ▁ (U+2581) → пробел). Added tokens декодируются в литеральный UTF-8. SentencePiece без `byte_fallback` и неизвестные/контекстно-зависимые схемы — `UNSUPPORTED_TOKENIZER` без молчаливой подстановки UTF-8 текста. Конкретные публичные artifacts/revisions фиксируются в benchmarks/manifest.json. Точное байтовое представление; одиночный `decode(token_id)` не используется. Ключ per-process кэша `TokenizerBundle.from_hf` включает отпечаток словаря и параметров декодирования: разные словари с одним именем получают разные bundle. |
| Покрытие токенизатора (coverage gate) | При `zg_compile` проверяется, что каждый литерал грамматики сегментируется в последовательность токенов словаря, а открытые классы (строка/целое/число) и структурный синтаксис имеют порождаемое завершение. Непокрываемое ограничение — `ZG_ERR_UNSUPPORTED_TOKENIZER` на компиляции; тупиковые токены в маске невозможны (каждый разрешённый токен имеет допустимое продолжение в пределах проверки). Проверка консервативна: границы токенов должны совпадать с границами узлов грамматики; byte-полный словарь (все 256 однобайтовых токенов) проходит всегда. |
| Построение маски | Обходом префиксного дерева токенов (trie в `Tokenizer`), а не перебором словаря; EOS/special/пустые токены в trie не входят (EOS разрешаются по `can_end`, special non-EOS запрещены). |
| Служебные токены | BOS/PAD и прочие special non-EOS запрещены внутри активного документа (`INVALID_TOKEN`). Обычный токен с пустым представлением — `UNSUPPORTED_TOKENIZER` при подготовке. EOS обрабатывается по semantics.md §8. |

## 6. Лимиты по умолчанию (FR-10, zg_context_config)

Все лимиты публичны и настраиваются. Для всех полей, кроме
`cache_limit_bytes`, значение 0 означает значение по умолчанию. Особый случай
— `cache_limit_bytes`: 0 полностью выключает кэш (маски вычисляются как в
lazy), а значение по умолчанию 64 MiB запрашивается явной константой
`ZG_CACHE_DEFAULT` (`UINT64_MAX`). Бюджет кэша жёсткий: фактически учтённые
байты категории cache (записи, состояния, маски, таблица HashMap и накладные
расходы allocator) никогда его не превышают; при заполнении работа продолжается
через lazy-вычисление маски.
Несовместимая комбинация (`cache_limit > memory_limit`) — `INVALID_ARGUMENT`
при создании контекста. При нехватке бюджета для токенизатора контекст не
создаётся.

| Лимит | Значение по умолчанию |
|---|---|
| `memory_limit_bytes` — общий бюджет ядра | 256 MiB |
| `cache_limit_bytes` — жёсткий бюджет LRU-кэша | 0 = выключен; `ZG_CACHE_DEFAULT` = 64 MiB |
| `session_limit_bytes` — состояние и рабочие данные одной сессии | 8 MiB |
| `schema_limit_bytes` — входная схема | 1 MiB |
| `max_depth` — структурная глубина | 64 |
| `max_threads_per_state` — ветвей разбора в состоянии | 64 |
| `work_limit_ops` — бюджет работы на вызов `zg_compile`/`zg_fill_mask(s)` в абстрактных операциях | 0 = без лимита |
| `adaptive_min_hits` — admission: минимум обращений к состоянию | 2 |
| `adaptive_min_cost_ns` — admission: минимальная стоимость вычисления маски, нс | 50000 |
| `precompute_max_states` — потолок состояний прогрева при compile | 4096 |

Достижение общего лимита — `RESOURCE_LIMIT`; схема не упрощается, маска не
ослабляется. Превышение `max_threads_per_state` — `RESOURCE_LIMIT` (не
молчаливое отбрасывание ветвей: полнота важнее).

Отмена и лимит работы: `zg_cancel_flag_set(ctx, flag)` регистрирует
вызывательский атомарный байт; `zg_compile` и `zg_fill_mask(s)` (включая
проверку покрытия и прогрев precompute) периодически читают его и при
ненулевом значении возвращают `ZG_ERR_CANCELLED`. Превышение `work_limit_ops`
в compile/fill_mask — `ZG_ERR_RESOURCE_LIMIT` (частичная маска не
возвращается). В прогреве precompute исчерпание бюджета работы — тихая
остановка прогрева с деградацией в lazy; наружу как `CANCELLED` идёт только
отмена флагом.

У `max_depth` и `max_threads_per_state` есть жёсткие потолки реализации
(`MAX_DEPTH_CAP = 64`, `MAX_THREADS_CAP = 64`, src/parser.zig): лимиты можно
только понижать; значение выше 64 отклоняется `INVALID_ARGUMENT` при создании
контекста. Следствие, измеренное на JSONSchemaBench: enum/const с более чем
64 альтернативами компилируется, но создание сессии даёт `RESOURCE_LIMIT`
(«parser state init limit exceeded») при любых бюджетах памяти — например,
Github_trivial/o48280 (enum из 255 строк).

## 8. Измеренная поддержка на JSONSchemaBench

Прогон полного корпуса JSONSchemaBench (repo guidance-ai/jsonschemabench,
commit `ba103c73756198dd9b149ddc7db7867da7a077f6`, 9558 схем, 10 датасетов;
закреплён в benchmarks/manifest.json) выполнен 2026-09-14 движком 0.1.0
(режим adaptive, профиль canonical-v1, токенизатор gpt2 rev 607a30d7).
Результаты — benchmarks/results/20260914T213241/ (support_report.json —
per-schema список, support_summary.md — сводка).

| Исход компиляции | Схем | Доля |
|---|---:|---:|
| Скомпилировано | 38 | 0.40% |
| `UNSUPPORTED_FEATURE` | 6703 | 70.13% |
| `INVALID_SCHEMA` | 2814 | 29.44% |
| `RESOURCE_LIMIT` | 3 | 0.03% |

Измеренные исходы соответствуют таблице §1: доминирующие причины отказов —
правила приёма объектных узлов (`additionalProperties: false` обязателен,
`properties`/`required` обязательны) и ключевые слова вне MVP (`definitions`,
`$id`/draft-4 `id`, `oneOf`/`anyOf`/`allOf`, `patternProperties` и др.).
3 `RESOURCE_LIMIT` — схемы крупнее `schema_limit_bytes` = 1 MiB.
Поведенческих расхождений с таблицей §1 на корпусе не выявлено; единственная
документируемая тонкость — взаимодействие enum > 64 альтернатив с потолком
`max_threads_per_state` (см. §6 выше).

Паритет масок с xgrammar 0.2.6 и llguidance 1.8.0 на пересечении языков
(52 схемы: 37 рабочих из 38 скомпилированных + 15 контрольного корпуса;
13 740 префиксов, gpt2) — benchmarks/results/20260914T213241/parity_summary.md.
Расхождений масок нашего движка с собственной семантикой canonical-v1
(oracle tests/reference.py) не обнаружено; все расхождения с конкурентами
классифицированы как разница профилей либо как ограничения/дефекты на
стороне конкурентов.

## 7. Коды ошибок (include/zig_constraints.h)

| Код | Имя | Типичные причины |
|---|---|---|
| 0 | `ZG_OK` | Успех. |
| 1 | `ZG_ERR_INVALID_ARGUMENT` | Невалидные struct_size/version, cache_limit > memory_limit, null-указатели, невыровненный буфер маски. |
| 2 | `ZG_ERR_INVALID_SCHEMA` | Нарушение правил приёма схемы (§1): дубликаты ключей, цикл `$ref`, чужой `$schema`, нецелые min/max. |
| 3 | `ZG_ERR_UNSUPPORTED_FEATURE` | Ключевое слово/конструкция вне MVP; неизвестный профиль. |
| 4 | `ZG_ERR_UNSATISFIABLE_CONSTRAINT` | min > max; enum после фильтрации пуст. |
| 5 | `ZG_ERR_UNSUPPORTED_TOKENIZER` | Пустое представление обычного токена; непокрытые id; несертифицированная схема декодирования; ограничение не покрывается токенизатором (coverage gate на compile, §5). |
| 6 | `ZG_ERR_INVALID_TOKEN` | Токен вне vocab; запрещённый токен при accept; special non-EOS в документе. Состояние не меняется. |
| 7 | `ZG_ERR_DEAD_END` | Ни один токен и ни один EOS не разрешён состоянием; маска недействительна. |
| 8 | `ZG_ERR_RESOURCE_LIMIT` | Превышение лимитов памяти/глубины/threads/размера схемы/`work_limit_ops`; отказ allocator. |
| 9 | `ZG_ERR_CANCELLED` | Отмена по флагу `zg_cancel_flag_set` во время compile/fill_mask(s), проверки покрытия или прогрева precompute. |
| 10 | `ZG_ERR_BUSY` | Штатное уничтожение контекста с живыми грамматиками/сессиями. |
| 11 | `ZG_ERR_WRONG_STATE` | accept/fill_mask на finished/aborted сессии; finish без can_end. |
| 12 | `ZG_ERR_BUFFER_TOO_SMALL` | Буфер маски меньше `ceil(vocab_size/32)` слов. |
| 13 | `ZG_ERR_INTERNAL` | Непредвиденная внутренняя ошибка; сессия помечается aborted. |

Диагностика передаётся через вызывательский `zg_error` (code, schema_offset,
message UTF-8 до 256 байт, json_pointer — RFC 6901 JSON Pointer к узлу схемы
при ошибках компиляции, "" когда неприменимо); глобального last_error нет. Ни
одна ошибка не пересекает ABI как Zig error union или panic.
