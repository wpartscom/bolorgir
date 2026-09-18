#!/usr/bin/env python3
"""Сводные отчёты по прогону JSONSchemaBench (support + parity).

Читает support_report.json и parity_report.json из каталога результатов,
пишет рядом support_summary.md, parity_summary.md и summary.json
(конвенции benchmarks/results/<timestamp>/ из предыдущих прогонов).
"""

import argparse
import json
import os

DATASET_ORDER = [
    "Glaiveai2K", "Github_trivial", "Github_easy", "Snowplow",
    "Github_medium", "Kubernetes", "WashingtonPost", "Github_hard",
    "JsonSchemaStore", "Github_ultra",
]
OUTCOME_RU = {
    "compiled": "скомпилировано",
    "unsupported_feature": "UNSUPPORTED_FEATURE",
    "invalid_schema": "INVALID_SCHEMA",
    "unsatisfiable_constraint": "UNSATISFIABLE_CONSTRAINT",
    "resource_limit": "RESOURCE_LIMIT",
    "unsupported_tokenizer": "UNSUPPORTED_TOKENIZER",
    "other_engine_error": "другая ошибка ядра",
    "harness_error": "ошибка стенда",
}


def support_md(rep):
    agg = rep["aggregate"]
    meta = rep["meta"]
    total = agg["total"]
    by_out = agg["by_outcome"]
    lines = []
    a = lines.append
    a("# JSONSchemaBench — отчёт поддержки схем zig-constraints")
    a("")
    a(f"- Корпус: JSONSchemaBench `{meta['corpus']['commit']}` "
      f"({meta['corpus']['path']}), {total} схем, 10 датасетов.")
    a(f"- Токенизатор: `{meta['tokenizer']['name']}` "
      f"rev `{meta['tokenizer']['revision']}` "
      f"(vocab {meta['tokenizer']['vocab_size']}).")
    a(f"- Движок: zig_constraints {meta['engine']['package_version']}, "
      f"режим {meta['engine']['mode']}, профиль "
      f"`{meta['engine']['profile']}`.")
    a(f"- Дата прогона: {meta['date']}; wall-clock {meta['wall_seconds']} с.")
    a("")
    a("## Итог")
    a("")
    a("| Исход | Схем | Доля |")
    a("|---|---:|---:|")
    for k in ("compiled", "unsupported_feature", "invalid_schema",
              "unsatisfiable_constraint", "resource_limit",
              "unsupported_tokenizer", "other_engine_error", "harness_error"):
        n = by_out.get(k, 0)
        if n:
            a(f"| {OUTCOME_RU[k]} | {n} | {100.0 * n / total:.2f}% |")
    a(f"| **всего** | **{total}** | 100% |")
    a("")
    a("## По датасетам")
    a("")
    a("| Датасет | Всего | Compiled | UNSUPPORTED | INVALID_SCHEMA | RESOURCE_LIMIT |")
    a("|---|---:|---:|---:|---:|---:|")
    for ds in DATASET_ORDER:
        d = agg["by_dataset"].get(ds)
        if not d:
            continue
        tot = sum(d.values())
        a(f"| {ds} | {tot} | {d.get('compiled', 0)} | "
          f"{d.get('unsupported_feature', 0)} | {d.get('invalid_schema', 0)} | "
          f"{d.get('resource_limit', 0)} |")
    a("")
    a("## Причины отказов")
    a("")
    a("`INVALID_SCHEMA` — почти всё это правило «у каждого объектного узла "
      "обязателен `additionalProperties: false`» и «обязательны `properties`/"
      "`required`» (docs/supported_features.md §1, DESIGN §1.7); 2 файла "
      "корпуса — невалидный JSON. `RESOURCE_LIMIT` — 3 схемы крупнее "
      "schema_limit_bytes = 1 MiB.")
    a("")
    a("Топ ключевых слов `UNSUPPORTED_FEATURE` (первое сообщённое компилятором "
      "ключевое слово на схему):")
    a("")
    a("| Ключевое слово | Схем |")
    a("|---|---:|")
    for kw, n in list(agg["unsupported_keywords"].items())[:20]:
        a(f"| `{kw}` | {n} |")
    a("")
    a("Полный per-schema список — `support_report.json` (`per_schema`, "
      "9558 записей с detail и compile_ns).")
    a("")
    cs = agg["compile_ns"]
    a(f"Время компиляции на схему, нс: p50={cs['p50']}, p95={cs['p95']}, "
      f"p99={cs['p99']}, max={cs['max']}.")
    a("")
    return "\n".join(lines)


def parity_md(rep):
    meta = rep["meta"]
    lines = []
    a = lines.append
    a("# JSONSchemaBench — паритет масок (ТЗ T2)")
    a("")
    a(f"- Токенизатор: `{meta['tokenizer']['name']}` rev "
      f"`{meta['tokenizer']['revision']}`; профиль `{meta['profile']}`; "
      f"seed {meta['seed']}.")
    a(f"- Версии: zig_constraints {meta['engine_versions']['zig_constraints']}, "
      f"xgrammar {meta['engine_versions']['xgrammar']}, "
      f"llguidance {meta['engine_versions']['llguidance']}.")
    a(f"- Выборка: {'; '.join(meta['sampling_rule'])}.")
    a(f"- Трасс на схему: {meta['traces_per_schema']}, max шагов "
      f"{meta['max_steps']}, бюджет rollout-классификации "
      f"{meta['classify_budget_per_schema']} на движок.")
    a(f"- Дата: {meta['date']}; wall-clock {meta['wall_seconds']} с.")
    a("")
    a("Сравнение побитовое по маске без бита EOS; завершение документа "
      "сверяется отдельным флагом (can_end / is_terminated / is_stopped). "
      "Oracle каноничности префиксов — независимый эталон "
      "tests/reference.py (Matcher).")
    a("")
    for eng, agg in rep["aggregate"].items():
        a(f"## {eng}")
        a("")
        a(f"- Схем сравнено: {agg['schemas_compared']}; ошибок компиляции "
          f"конкурента на нашем подмножестве: "
          f"{agg['compile_errors_on_our_subset']}.")
        a(f"- Префиксов: {agg['prefixes']}; побитовое совпадение масок: "
          f"{agg['raw_exact_prefixes']} "
          f"({100.0 * (agg['raw_match_rate'] or 0):.1f}%).")
        a(f"- Расхождений флага завершения: {agg['termination_mismatches']}.")
        a("")
        a("| Класс расхождения | Случаев |")
        a("|---|---:|")
        for cls, n in agg["divergence_classes"].items():
            a(f"| `{cls}` | {n} |")
        a("")
        bugs = agg["schemas_with_real_bug_candidates"]
        if bugs:
            a(f"**Кандидаты в реальные баги нашего движка: {len(bugs)} "
              f"схем** — см. reproducers/.")
            for b in bugs:
                a(f"- {b['dataset']}/{b['name']}: {b['classes']}")
        else:
            a("Кандидатов в реальные баги нашего движка не обнаружено "
              "(классы REAL-BUG:* отсутствуют).")
        a("")
        an = agg["schemas_with_competitor_anomalies"]
        if an:
            a(f"Схемы с расхождениями на стороне конкурента "
              f"({len(an)}):")
            for x in an:
                a(f"- {x['dataset']}/{x['name']}: {x['classes']}")
            a("")
    a("## Ключевые наблюдения (ручной разбор)")
    a("")
    a("1. **Низкая сырая побитовая совместимость ожидаема**: конкуренты "
      "разрешают произвольные пробелы вне строк, canonical-v1 — нет "
      "(semantics.md §1, §11). Классы expected:whitespace* и "
      "expected:profile* — это ровно разница профилей сериализации, "
      "подтверждённая oracle tests/reference.py и/или rollout-валидацией "
      "по исходной схеме (jsonschema).")
    a("2. **xgrammar competitor-undergeneration** (21 734 случая) — два "
      "системных сужения языка xgrammar 0.2.6 относительно canonical-v1: "
      "(а) в строках с minLength/maxLength запрещены ВСЕ escape-последовательности "
      "(нет ни одного токена с `\\\\` в маске; воспроизведено изолированно: "
      "`{\"type\":\"string\",\"minLength\":8,\"maxLength\":64}` — `accept_token(\"\\\\\") == False`, "
      "тогда как в неограниченной строке escape разрешён); "
      "(б) запрещён `-0` для integer (после `-` в маске нет `0`). "
      "Оба — ограничения конкурента, не нашего движка (semantics.md §2.2, §4.1).")
    a("3. **xgrammar competitor-overgeneration** (9 rollout-подтверждённых "
      "случаев + аналогичные по семейству в expected:profile-unverified): "
      "xgrammar пропускает в строках токены с невалидными UTF-8 lead-байтами "
      "(0xC0, 0xC1, 0xF5-0xFF) — rollout даёт документ, не являющийся "
      "валидным UTF-8 JSON. Наш движок валидирует UTF-8 DFA (semantics.md §3).")
    a("4. **llguidance competitor-undergeneration** (7 375) — почти целиком "
      "токен `\\x7f` (DEL): char-class строки llguidance исключает 0x7F, "
      "canonical-v1 разрешает его raw (semantics.md §2.1, RFC 8259 тоже). "
      "Ещё 999 расхождений — артефакт fast-forward аппроксимации битмаски "
      "llguidance (validate_tokens токен принимает, битмаска неполна; "
      "класс expected:llguidance-ff-approximation).")
    a("5. **Флаги завершения** (41/2 «mismatch») — разница интерфейсной "
      "семантики: xgrammar/llguidance выставляют is_terminated/is_stopped "
      "только после фактического consume EOS; в точке завершённого документа "
      "их маска ровно {EOS} и совпадает с нашей (EOS-бит проверен отдельно: "
      "расхождений EOS-бита в финальном прогоне нет).")
    a("6. **xgrammar 0.2.6: перекрёстное загрязнение кэша GrammarCompiler** "
      "(competitor-баг): при cache_enabled=True грамматика o21459, "
      "скомпилированная после o10014/o13837/o21458, разрешала EOS в середине "
      "строки (5 случаев eos-policy-divergence). С cache_enabled=False не "
      "воспроизводится; итоговый прогон выполнен с отключённым кэшем "
      "компилятора. Reproducer: reproducers/competitor_xgrammar_cache_pollution.json.")
    a("7. **Github_trivial/o48280** (enum из 255 строк): компилируется, но "
      "создание сессии — RESOURCE_LIMIT «parser state init limit exceeded» "
      "при любых лимитах памяти: 255 альтернатив enum > max_threads_per_state=64, "
      "а поднять значение выше 64 нельзя (MAX_THREADS_CAP=64, "
      "src/parser.zig). Задокументированное поведение лимитов "
      "(supported_features.md §6), не молчаливое ослабление.")
    a("")
    a("Сырые записи расхождений (схема, профиль, revision токенизатора, "
      "token IDs префикса, различающиеся биты) — `parity_report.json`, "
      "per_schema[].parity.divergences.")
    a("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results-dir", required=True)
    args = ap.parse_args()
    rd = args.results_dir

    with open(os.path.join(rd, "support_report.json"), encoding="utf-8") as f:
        support = json.load(f)
    with open(os.path.join(rd, "support_summary.md"), "w",
              encoding="utf-8") as f:
        f.write(support_md(support))

    summary = {
        "timestamp": os.path.basename(rd.rstrip("/")),
        "cases": {},
        "support": support["aggregate"]["by_outcome"],
        "support_total": support["aggregate"]["total"],
    }
    summary["cases"]["bench_jsb_support"] = "OK"

    parity_path = os.path.join(rd, "parity_report.json")
    if os.path.exists(parity_path):
        with open(parity_path, encoding="utf-8") as f:
            parity = json.load(f)
        with open(os.path.join(rd, "parity_summary.md"), "w",
                  encoding="utf-8") as f:
            f.write(parity_md(parity))
        summary["cases"]["mask_parity"] = "OK"
        for eng, agg in parity["aggregate"].items():
            summary[f"parity_{eng}"] = {
                "schemas_compared": agg["schemas_compared"],
                "prefixes": agg["prefixes"],
                "raw_match_rate": agg["raw_match_rate"],
                "divergence_classes": agg["divergence_classes"],
                "real_bug_schemas": len(
                    agg["schemas_with_real_bug_candidates"]),
            }
        summary["reproducers"] = parity["meta"]["reproducers_written"]
    with open(os.path.join(rd, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(json.dumps({"status": "OK", "dir": rd}, ensure_ascii=False))


if __name__ == "__main__":
    main()
