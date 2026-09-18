#!/usr/bin/env python3
"""Сводка контрольного прогона: ключевые числа + проверка Go-правила §10.6.

Читает JSON-результаты из каталога results/<timestamp> и печатает:
- таблицу uniform-нагрузки (mask/accept/compile по движкам);
- оценку Go-правила из manifest.control_scenario;
- сводку B1 cold-process, B7 e2e constrained (медианы по повторам),
  B5/B8 плато, B3/B6.

Использование:
    python3 benchmarks/summarize_control.py benchmarks/results/<timestamp>
"""

import json
import os
import sys
from statistics import median


def load(d, name):
    p = os.path.join(d, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        raw = f.read()
    return json.loads(raw[raw.index("{"):])  # пропуск возможного warning-префикса


def us(ns):
    return None if ns is None else round(ns / 1000.0, 2)


def main():
    d = sys.argv[1]
    print(f"# сводка {d}\n")

    uni = load(d, "bench_compare_uniform.json")
    if uni and uni.get("status") == "OK":
        print("## uniform load (одинаковые токенизатор/схема/трасса)")
        print(f"schema={uni['schema']} trace={uni['trace']['text']!r} "
              f"len={uni['trace']['len']}")
        hdr = ["engine", "mask p50 us", "mask p95 us", "mask p99 us",
               "accept p50 us", "accept p99 us", "cold compile p50 us",
               "first mask p50 us", "warm compile p50 us"]
        print("| " + " | ".join(hdr) + " |")
        print("|" + "---|" * len(hdr))
        for name, e in uni["engines"].items():
            print("| " + " | ".join(map(str, [
                name,
                us(e["fill_mask_ns"]["p50"]), us(e["fill_mask_ns"]["p95"]),
                us(e["fill_mask_ns"]["p99"]),
                us(e["accept_ns"]["p50"]), us(e["accept_ns"]["p99"]),
                us(e["cold_compile_ns"]["p50"]), us(e["first_mask_ns"]["p50"]),
                us(e["warm_compile_ns"]["p50"]),
            ])) + " |")
        # Go-правило
        za = uni["engines"]["zig_adaptive"]
        comp = {k: v for k, v in uni["engines"].items()
                if k in ("xgrammar", "llguidance")}
        best = min(comp, key=lambda k: comp[k]["fill_mask_ns"]["p99"])
        bp99 = comp[best]["fill_mask_ns"]["p99"]
        zp99 = za["fill_mask_ns"]["p99"]
        gain = (bp99 - zp99) / bp99 * 100
        print(f"\nлучший конкурент по p99 маски: {best} ({us(bp99)} us); "
              f"zig_adaptive {us(zp99)} us; снижение {gain:.1f}% "
              f"(нужно >= 20%)")
        checks = [
            ("mask p50", za["fill_mask_ns"]["p50"], comp[best]["fill_mask_ns"]["p50"]),
            ("mask p95", za["fill_mask_ns"]["p95"], comp[best]["fill_mask_ns"]["p95"]),
            ("accept p50", za["accept_ns"]["p50"], comp[best]["accept_ns"]["p50"]),
            ("accept p95", za["accept_ns"]["p95"], comp[best]["accept_ns"]["p95"]),
            ("accept p99", za["accept_ns"]["p99"], comp[best]["accept_ns"]["p99"]),
            ("cold compile p50", za["cold_compile_ns"]["p50"], comp[best]["cold_compile_ns"]["p50"]),
            ("first mask p50", za["first_mask_ns"]["p50"], comp[best]["first_mask_ns"]["p50"]),
        ]
        ok_rest = True
        for label, z, c in checks:
            deg = (z - c) / c * 100 if c else float("inf")
            flag = "OK" if deg <= 10 else "FAIL(>10%)"
            if deg > 10:
                ok_rest = False
            print(f"  {label}: zig {us(z)} us vs {best} {us(c)} us "
                  f"({deg:+.1f}%) {flag}")
        go = gain >= 20 and ok_rest
        print(f"\nVERDICT control scenario: {'GO' if go else 'NO-GO'}")
        # расхождения со вторым конкурентом тоже публикуются
        other = [k for k in comp if k != best][0]
        op99 = comp[other]["fill_mask_ns"]["p99"]
        print(f"(второй конкурент {other}: mask p99 {us(op99)} us; "
              f"zig {'ниже' if zp99 < op99 else 'выше'} на "
              f"{abs(op99 - zp99) / op99 * 100:.1f}%)")

    cp = load(d, "bench_coldproc.json")
    if cp and cp.get("status") == "OK":
        print("\n## B1: 30 независимых процессов")
        print("| engine | ok | compile p50 ms | first mask p50 ms | total cold p50 s | peak RSS p50 MiB |")
        print("|---|---|---|---|---|---|")
        for name, e in cp["engines"].items():
            if e.get("compile_ns", {}).get("count", 0) == 0:
                # движок недоступен в этой среде: замеров нет (не выдумываем)
                print(f"| {name} | {e['repeats_ok']} | — | — | — | — |")
                continue
            print(f"| {name} | {e['repeats_ok']} | "
                  f"{e['compile_ns']['p50'] / 1e6:.2f} | "
                  f"{e['first_mask_ns']['p50'] / 1e6:.2f} | "
                  f"{e['total_cold_ns']['p50'] / 1e9:.2f} | "
                  f"{e['peak_rss_bytes']['p50'] / 2**20:.0f} |")

    e2e = load(d, "bench_e2e_constrained.json")
    if e2e:
        print("\n## B7: constrained vs constrained-baseline (медианы повторов)")
        print("| schema | batch | mode | total s | tok/s | ITL p50 ms | valid | lens |")
        print("|---|---|---|---|---|---|---|")
        groups = {}
        for r in e2e["runs"]:
            if r["summary"].get("status") != "OK":
                continue
            k = (r["schema"], r["batch"], r["mode"])
            groups.setdefault(k, []).append(r["summary"])
        for (schema, batch, mode), sums in sorted(groups.items()):
            tot = median(s["total_s"] for s in sums)
            tps = median(s["tokens_per_s"] for s in sums)
            itl = median(s["itl_p50_ms"] for s in sums if s["itl_p50_ms"])
            vr = sums[0].get("valid_rows")
            lens = sorted({l for s in sums for l in s["row_lengths"]})
            lens_s = f"{lens[0]}..{lens[-1]}" if len(lens) > 1 else str(lens[0])
            print(f"| {schema} | {batch} | {mode} | {tot:.2f} | {tps:.1f} | "
                  f"{itl:.1f} | {vr}/{batch} | {lens_s} |")
        # регрессия по каждой конфигурации
        print("\nрегрессия zig относительно xgrammar (по медианам tok/s):")
        for (schema, batch), by_mode in {}.items():
            pass
        cfg = {}
        for (schema, batch, mode), sums in groups.items():
            cfg.setdefault((schema, batch), {})[mode] = median(
                s["tokens_per_s"] for s in sums)
        for (schema, batch), m in sorted(cfg.items()):
            if "zig_constrained" in m and "xgrammar_constrained" in m:
                z, x = m["zig_constrained"], m["xgrammar_constrained"]
                reg = (x - z) / x * 100
                print(f"  {schema} b{batch}: zig {z:.1f} vs xg {x:.1f} tok/s "
                      f"(регрессия {reg:+.1f}%, порог 5%)")

    lr = load(d, "bench_longrun.json")
    if lr and lr.get("status") == "OK":
        print("\n## B5/B8: длительная генерация")
        for lim, r in lr["limits"].items():
            if r.get("status") != "OK":
                print(f"  {lim} MiB: {r.get('status')} {r.get('errors')}")
                continue
            p = r["mask_p99_plateau"]
            rss = r["rss_plateau"]
            st = r["core_stats_after"]
            print(f"  {lim} MiB: steps={r['mask_steps']} cycles={r['session_cycles']} "
                  f"errors={r['errors']} p99 plateau ratio={p and round(p['ratio'], 3)} "
                  f"rss ratio={rss and round(rss['ratio'], 3)} "
                  f"cache hits={st['cache_hits']} evictions={st['cache_evictions']}")

    st = load(d, "bench_schemas_tokenizers.json")
    if st and st.get("status") == "OK":
        print("\n## B3: смена схем")
        b3 = st["B3"]
        jsb = b3.get("jsb_support", {})
        print(f"  JSB-100: ok {jsb.get('ok')}, unsupported {jsb.get('unsupported')}, "
              f"invalid {jsb.get('error')}; поддержано схем корпуса: "
              f"{b3.get('corpus_supported_schemas')}")
        for mode, ph in b3.get("phases", {}).items():
            c = ph.get("cache", {})
            print(f"  {mode}: unique compile p50 {us(ph['unique_compile_ns']['p50'])} us, "
                  f"repeat p50 {us(ph['repeat_compile_ns']['p50'])} us, "
                  f"cache hits/misses/evictions {c.get('hits')}/{c.get('misses')}/{c.get('evictions')}")
        sp = b3.get("session_phase", {})
        fm = sp.get("fill_mask_ns", {})
        if fm:
            print(f"  фаза сессий: схем {sp.get('schemas')}, раундов {sp.get('rounds')}, "
                  f"fill_mask p50 {us(fm.get('p50'))} us, p99 {us(fm.get('p99'))} us")
        print("\n## B6: токенизаторы")
        for tid, r in st["B6"].items():
            if not isinstance(r, dict) or r.get("status") != "OK":
                print(f"  {tid}: {r.get('status') if isinstance(r, dict) else r}")
                continue
            print(f"  {tid} ({r['family']}): vocab={r['vocab_size']} "
                  f"prepare={r.get('bundle_prepare_ns', 0) / 1e6:.1f} ms "
                  f"compile={r['compile_ns'] / 1e6:.1f} ms "
                  f"mask p50={us(r['fill_mask_ns']['p50'])} us "
                  f"core tok mem={r['core_mem_tokenizer_bytes'] / 2**20:.1f} MiB")
            if "sp_shim_verify" in r:
                print(f"    sp_shim_verify: {r['sp_shim_verify']}")

    gp = load(d, "bench_gpu_mask_path.json")
    if gp and gp.get("status") == "OK":
        print("\n## GPU-интервалы маски (build / H2D / apply / full), p50 us")
        print("| engine | build | H2D+unpack | apply | full |")
        print("|---|---|---|---|---|")
        for name, e in gp["engines"].items():
            print(f"| {name} | {us(e['build_ns']['p50'])} | "
                  f"{us(e['h2d_ns']['p50'])} | {us(e['apply_ns']['p50'])} | "
                  f"{us(e['full_ns']['p50'])} |")


if __name__ == "__main__":
    main()
