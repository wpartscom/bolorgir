# benchmarks/ — инфраструктура измерений zig-constraints

Методика по ТЗ §10. Окружение зафиксировано в `manifest.json` (создан до
оптимизаций; железо и версии меняются только с обновлением manifest).

## Состав

```
manifest.json            факты машины: CPU/RAM/ОС/GPU, версии, правила прогона
bench_common.py          общие утилиты: SKIP-защита, таймеры, перцентили, корпус
bench_prepare.py         B1: холодный/тёплый compile, tokenizer prepare, первая маска, peak RSS
bench_masks.py           B2: p50/p95/p99/max fill_mask и accept на тёплой схеме (>=10k наблюдений)
bench_batch.py           B4: батчи 1/8/32/128 сессий
bench_memory.py          B5/B8: лимиты 64/128/256 MiB, плато RSS на 10k циклов create/destroy
bench_compare_uniform.py одинаковая нагрузка: один токенизатор/схема/трасса для zig/xgrammar/llguidance
bench_coldproc.py        B1: 30 независимых процессов на движок (аудит п.3)
bench_e2e_constrained.py B7: GPU end-to-end zig vs constrained-baseline (xgrammar), аудит п.4
bench_gpu_mask_path.py   GPU-интервалы маски: build/H2D/apply/full (аудит п.5)
bench_longrun.py         B5/B8: честная длительная генерация, ~100k шагов/лимит (аудит п.6)
bench_schemas_tokenizers.py B3 смена схем + B6 матрица токенизаторов (аудит п.7)
summarize_control.py     сводка контрольного прогона + пересчёт Go-правила §10.6
run_all.py               прогон всех скриптов, сырые результаты в results/<timestamp>/
compare/xgrammar_runner.py    те же B1/B2 через XGrammar
compare/llguidance_runner.py  те же B1/B2 через llguidance
corpus/                  собственный MVP-корпус схем + index.json
external/jsonschemabench/     внешний корпус JSONSchemaBench (см. ниже)
results/                 сырые результаты прогонов (не коммитятся)
```

## Методика (ТЗ 10.5)

- Таймер `time.perf_counter_ns`; RSS — `resource.getrusage(RUSAGE_SELF).ru_maxrss`.
- CPU-таймер измеряет вызовы ядра отдельно от обвязки: fill_mask и accept
  замеряются раздельными интервалами, перекрывающиеся интервалы не суммируются.
- Прогрев: 3 итерации до замера (в скриптах — прогон генерации трассы).
- Повторы: cold-start >= 30 независимых повторов; тёплые классы нагрузки —
  >= 10k наблюдений для p99 (`--min-observations`).
- Маски сравниваются на заранее заданных валидных трасах токенов:
  `bench_common.gen_trace` детерминированно (seed=42) строит трассу, все
  измерения переигрывают одну и ту же.
- Публикуем p50/p95/p99/max/mean/count и peak RSS; сырые наблюдения и трассы
  сохраняются в `results/<timestamp>/`; таймауты и ошибки записываются в
  результаты, молча не исключаются.
- Отдельно показывается распределение по схемам (каждая схема — свой прогон),
  чтобы длинные простые ответы не скрывали сложные схемы.
- GPU-микробенчмарки (когда появятся): `torch.cuda.synchronize` вокруг
  измеряемой операции; end-to-end без лишней синхронизации на каждом токене.

## Воспроизведение

```sh
# из корня репозитория
python3 benchmarks/run_all.py                      # все кейсы, схема по умолчанию
python3 benchmarks/run_all.py --schema mixed_records
python3 benchmarks/bench_prepare.py --schema big_enum_64 --repeats 30
python3 benchmarks/bench_masks.py --schema enum_common_prefixes --mode lazy
python3 benchmarks/bench_batch.py --batches 1,8,32,128
python3 benchmarks/bench_memory.py --limits-mb 64,128,256 --cycles 10000
```

Скрипты ядра при отсутствии собранного пакета `zig_constraints` печатают
`{"status": "SKIP", ...}` и завершаются с кодом 0 — прогон `run_all.py` не
падает, статус фиксируется в `results/<timestamp>/summary.json`.

## Внешние сравнения: статус

| Участник | Статус | Закрепление |
|---|---|---|
| XGrammar | установлен, раннер рабочий | 0.2.6 (pip --user, 2026-09-14) |
| llguidance | установлен, раннер рабочий | 1.8.0 (pip --user, 2026-09-14) |
| torch / transformers | установлены для compare-раннеров | 2.14.0 / 5.17.0 |
| Токенизатор compare | закреплён | openai-community/gpt2 @ 607a30d783dfa663caf39e06633721c8d4cfcd7e (byte-level BPE, vocab 50257) |

Установка внешних движков (уже выполнена на стенде):

```sh
pip3 install --user xgrammar llguidance
pip3 install --user --ignore-installed numpy   # системный numpy 1.21.5 сломан (нет libblas.so.3)
```

Профили JSON у XGrammar/llguidance отличаются от canonical-v1: раннеры
измеряют задержку на эквивалентной схеме и помечают это в выводе; побитовое
сравнение масок проводится только на явно размеченном пересечении языков
(ТЗ 10.2), эта работа — после сборки ядра.

Реальный пользовательский Python-callback (участник 10.2.6) не найден —
статус pending в manifest; искусственно медленный callback базой не считается.

## JSONSchemaBench

Статус: **скачан и закреплён**. Источник:
https://github.com/guidance-ai/jsonschemabench, commit
`ba103c73756198dd9b149ddc7db7867da7a077f6` (main на 2026-09-14).
Локально: `external/jsonschemabench/data/` (9558 файлов схем, 10 категорий).

Команда воспроизведения:

```sh
curl -sL -o /tmp/jsonschemabench.tar.gz \
  https://github.com/guidance-ai/jsonschemabench/archive/refs/heads/main.tar.gz
tar xzf /tmp/jsonschemabench.tar.gz -C /tmp
mkdir -p benchmarks/external/jsonschemabench
cp -r /tmp/jsonschemabench-main/data benchmarks/external/jsonschemabench/
cp /tmp/jsonschemabench-main/README.md benchmarks/external/jsonschemabench/
```

Следующие шаги по корпусу:

- отчёт поддержки/отказов по полному перечню JSONSchemaBench (9558 схем):
  пока измерено подмножество первых 100 (поддержка MVP 1/100,
  bench_schemas_tokenizers.py);
- разделение tuning/holdout **зафиксировано** в manifest 2026-09-15
  (чёт/нечет по sorted-именам; контрольная схема big_enum_64 из holdout);
- MaskBench как воспроизводимый ориентир плотности масок (не выполнен).

## Контрольный прогон §10.6 / §13.2

Первый прогон: 2026-09-15, `results/20260915T024409/REPORT.md` (вердикт NO-GO).

Повторный прогон: **2026-09-17, `results/20260917T184826_perf-fix2/REPORT.md`**
(+ сырые JSON, `environment.txt`). Проведён после устранения двух дефектов
производительности, найденных при разборе первого вердикта:

- `parser.feedBytes`: 0xAA-заливка локального `var work: State = undefined`
  (~80 КиБ memset на вызов и на итерацию цикла) — заменено на threadlocal
  скретч; accept на многоthread-состояниях 100 µs → 1–2 µs;
- `parser.hashState`: байтовый FNV-1a → Wyhash; кэш-хит маски на 64-поточном
  состоянии ~3.1 µs → ~1.5 µs.

Одновременно впервые измерена оптимизация адаптера `MaskGpuUnpacker`
(H2D+распаковка 577 µs → 24.9 µs; полный GPU-путь 49.2 µs — быстрее xg/lg),
закрыта сертификация второй семьи (TinyLlama SP, `certified: true`), и
исправлена передача закреплённых аргументов контрольного сценария в
`run_all.py` (ранее отбрасывались).

Итог: **главная метрика пройдена** — p99 маски zig_adaptive 1.60 µs против
3.20 µs у xgrammar (**−49.9%**, порог ≥20%); e2e-гард пройден (регрессия
≤4.4% при пороге 5%). Вердикт по формальному правилу — **NO-GO**: не пройдены
accept p95/p99 (+143%/+126%; структурная разница: у zig работа в accept, у
xgrammar — в fill) и cold compile (+85.9%). Формулировка для ТЗ 10.6:
«корректный прототип; гипотеза производительности подтверждена по основной
метрике, но не подтверждена по двум дополнительным условиям».

Smoke всего стенда после правок: `results/20260915T025723_smoke/` (10/10 OK,
первый прогон); полные наборы тестов перед повторным прогоном: ядро 102/102
(Debug/ReleaseSafe), Python 296/296 на двух бэкендах.
