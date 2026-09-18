# zig-constraints

Адаптивный движок структурированной генерации для LLM на Zig.

Библиотека на каждом шаге генерации определяет множество допустимых следующих
токенов согласно заданным ограничениям (профиль JSON Schema Draft 2020-12 либо
набор буквальных строк), хранит состояние разбора между шагами и обновляет его
после принятия токена. Завершённый ответ гарантированно соответствует заданной
структуре; выбор содержательно правильного значения остаётся задачей модели.

Основные применения: JSON для программной обработки, аргументы вызова
инструментов, извлечение структурированных данных, выбор из фиксированного
набора значений. Проект — самостоятельная реализация идей constrained decoding;
XGrammar и llguidance участвуют только как обязательные участники сравнения.

## Статус

Прототип. По плану этапов ТЗ (§12) в работе этапы 1–3: точное ядро с C ABI,
кэш/бюджеты памяти и batch API, Python-пакет и адаптер Transformers.
Семантика canonical-v1 зафиксирована в `docs/semantics.md`, таблица поддержки —
в `docs/supported_features.md`. Контрольный прогон §10.6 выполнен повторно
2026-09-17 (`benchmarks/results/20260917T184826_perf-fix2/REPORT.md`): главная
метрика пройдена (p99 маски −49.9% к лучшему конкуренту, порог −20%), e2e-гард
пройден (регрессия ≤4.4% при пороге 5%); вердикт остаётся **NO-GO** по двум
допусловиям (accept на многоthread-состояниях и холодная компиляция), обещаний
ускорения не даётся.

Ядро также включает: проверку покрытия токенизатора при компиляции
(непокрываемое ограничение отклоняется до генерации), кэш масок с
adaptive-политикой admission и жёстким байтовым бюджетом, экспериментальный
прогрев масок при компиляции (режим `precompute`), отмену по флагу
(`zg_cancel_flag_set`) и лимит работы на вызов (`work_limit_ops`).

## Быстрый старт

Требования: Zig 0.15.2 (`/home/gm/.local/bin/zig`), Python 3.10+, gcc.

```sh
# Сборка ядра (Debug): zig-out/lib/libzig_constraints.so
/home/gm/.local/bin/zig build

# Сборка ядра (ReleaseSafe; у этой конфигурации нет -Doptimize)
/home/gm/.local/bin/zig build -Drelease=true

# Unit-тесты ядра
/home/gm/.local/bin/zig build test

# Пример C-клиента без Python (examples/c_client.c)
/home/gm/.local/bin/zig build example-c
LD_LIBRARY_PATH=zig-out/lib zig-out/bin/c_client

# Python-пакет (каталог python/, собирает ядро через zig build -Drelease=true)
cd python && pip3 install --user .

# Колесо: pip3 wheel . --no-build-isolation -w dist
# (на pip 22.x изолированная сборка даёт имя UNKNOWN-0.0.0 — дефект старого
# pip+setuptools окружения, не репозитория; свежие pip в CI собирают штатно)

# Интеграционные тесты (эталон, паритет режимов, граничные случаи).
# Два бэкенда: ctypes напрямую к .so и Python-пакет (как в CI)
PYTHONPATH=python ZG_TEST_BACKEND=ctypes python3 -m pytest tests/ python/tests/ -q
PYTHONPATH=python ZG_TEST_BACKEND=package python3 -m pytest tests/ python/tests/ -q
```

## CI

Workflow `.github/workflows/ci.yml` (push/PR + еженедельно): unit-тесты ядра в
Debug и ReleaseSafe, `zig fmt --check`, сборка и запуск C-примера; pytest на
Python 3.10/3.11/3.12 в обоих бэкендах (ctypes и package); сборка wheel и
smoke-установка в чистом venv без torch.

Минимальный пример Python API:

```python
from zig_constraints import Engine

schema = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["buy", "sell"]},
        "amount": {"type": "integer"},
    },
    "required": ["action", "amount"],
    "additionalProperties": False,
}

with Engine(mode="adaptive", memory_limit_mb=256) as engine:
    constraint = engine.compile(schema, tokenizer, profile="canonical-v1")
    session = constraint.create_session()
    mask = session.fill_mask()   # битовая маска допустимых token IDs
    session.accept_token(token_id)
```

## Структура репозитория

```
build.zig                  сборка ядра, тестов и C-примера
include/zig_constraints.h  версионированный C ABI (opaque handles, zg_status)
src/                       ядро: компилятор схем, IR грамматики, парсер, маски,
                           учитывающий аллокатор, кэш LRU, C ABI
examples/c_client.c        пример C-клиента без Python
python/                    Python-пакет (CPython-расширение, abi3) и адаптер
tests/                     независимый эталон, parity/edge/fuzz-тесты
docs/                      DESIGN.md, API.md, semantics.md, supported_features.md,
                           architecture.md, ADR
benchmarks/                manifest.json, корпус схем, скрипты измерений,
                           сравнение с XGrammar/llguidance
```

## Документация

- `docs/semantics.md` — нормативная семантика профиля сериализации canonical-v1.
- `docs/supported_features.md` — точная таблица поддержки FR-1, лимиты, коды ошибок.
- `docs/architecture.md` — архитектура модулей и границы ответственности.
- `benchmarks/README.md` — методика измерений и команды воспроизведения.

Полное техническое задание: `ТЗ_Zig_движок_структурированной_генерации.md`.
