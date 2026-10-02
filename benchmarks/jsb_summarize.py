#!/usr/bin/env python3
"""Summary reports for the JSONSchemaBench run (support + parity).

Reads support_report.json and parity_report.json from the results directory
and writes support_summary.md, parity_summary.md and summary.json next to
them (conventions of benchmarks/results/<timestamp>/ from previous runs).
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
    "compiled": "compiled",
    "unsupported_feature": "UNSUPPORTED_FEATURE",
    "invalid_schema": "INVALID_SCHEMA",
    "unsatisfiable_constraint": "UNSATISFIABLE_CONSTRAINT",
    "resource_limit": "RESOURCE_LIMIT",
    "unsupported_tokenizer": "UNSUPPORTED_TOKENIZER",
    "other_engine_error": "other engine error",
    "harness_error": "harness error",
}


def support_md(rep):
    agg = rep["aggregate"]
    meta = rep["meta"]
    total = agg["total"]
    by_out = agg["by_outcome"]
    lines = []
    a = lines.append
    a("# JSONSchemaBench - Bolorgir schema support report")
    a("")
    a(f"- Corpus: JSONSchemaBench `{meta['corpus']['commit']}` "
      f"({meta['corpus']['path']}), {total} schemas, 10 datasets.")
    a(f"- Tokenizer: `{meta['tokenizer']['name']}` "
      f"rev `{meta['tokenizer']['revision']}` "
      f"(vocab {meta['tokenizer']['vocab_size']}).")
    a(f"- Engine: bolorgir {meta['engine']['package_version']}, "
      f"mode {meta['engine']['mode']}, profile "
      f"`{meta['engine']['profile']}`.")
    a(f"- Run date: {meta['date']}; wall-clock {meta['wall_seconds']} s.")
    a("")
    a("## Result")
    a("")
    a("| Outcome | Schemas | Share |")
    a("|---|---:|---:|")
    for k in ("compiled", "unsupported_feature", "invalid_schema",
              "unsatisfiable_constraint", "resource_limit",
              "unsupported_tokenizer", "other_engine_error", "harness_error"):
        n = by_out.get(k, 0)
        if n:
            a(f"| {OUTCOME_RU[k]} | {n} | {100.0 * n / total:.2f}% |")
    a(f"| **total** | **{total}** | 100% |")
    a("")
    a("## By dataset")
    a("")
    a("| Dataset | Total | Compiled | UNSUPPORTED | INVALID_SCHEMA | RESOURCE_LIMIT |")
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
    a("## Refusal reasons")
    a("")
    a("`INVALID_SCHEMA` - almost all of this is the rule \"every object node "
      "must have `additionalProperties: false`\" and \"`properties`/"
      "`required` are mandatory\" (docs/supported_features.md §1, DESIGN §1.7); "
      "2 corpus files are invalid JSON. `RESOURCE_LIMIT` - 3 schemas larger "
      "than schema_limit_bytes = 1 MiB.")
    a("")
    a("Top `UNSUPPORTED_FEATURE` keywords (the first keyword reported by the "
      "compiler per schema):")
    a("")
    a("| Keyword | Schemas |")
    a("|---|---:|")
    for kw, n in list(agg["unsupported_keywords"].items())[:20]:
        a(f"| `{kw}` | {n} |")
    a("")
    a("The full per-schema list is in `support_report.json` (`per_schema`, "
      "9558 records with detail and compile_ns).")
    a("")
    cs = agg["compile_ns"]
    a(f"Compile time per schema, ns: p50={cs['p50']}, p95={cs['p95']}, "
      f"p99={cs['p99']}, max={cs['max']}.")
    a("")
    return "\n".join(lines)


def parity_md(rep):
    meta = rep["meta"]
    lines = []
    a = lines.append
    a("# JSONSchemaBench - mask parity (SPEC T2)")
    a("")
    a(f"- Tokenizer: `{meta['tokenizer']['name']}` rev "
      f"`{meta['tokenizer']['revision']}`; profile `{meta['profile']}`; "
      f"seed {meta['seed']}.")
    a(f"- Versions: bolorgir {meta['engine_versions']['bolorgir']}, "
      f"xgrammar {meta['engine_versions']['xgrammar']}, "
      f"llguidance {meta['engine_versions']['llguidance']}.")
    a(f"- Sample: {'; '.join(meta['sampling_rule'])}.")
    a(f"- Traces per schema: {meta['traces_per_schema']}, max steps "
      f"{meta['max_steps']}, rollout classification budget "
      f"{meta['classify_budget_per_schema']} per engine.")
    a(f"- Date: {meta['date']}; wall-clock {meta['wall_seconds']} s.")
    a("")
    a("The comparison is bitwise over the mask without the EOS bit; document "
      "completion is checked by a separate flag (can_end / is_terminated / "
      "is_stopped). The canonicality oracle for prefixes is the independent "
      "reference tests/reference.py (Matcher).")
    a("")
    for eng, agg in rep["aggregate"].items():
        a(f"## {eng}")
        a("")
        a(f"- Schemas compared: {agg['schemas_compared']}; competitor compile "
          f"errors on our subset: "
          f"{agg['compile_errors_on_our_subset']}.")
        a(f"- Prefixes: {agg['prefixes']}; bitwise mask match: "
          f"{agg['raw_exact_prefixes']} "
          f"({100.0 * (agg['raw_match_rate'] or 0):.1f}%).")
        a(f"- Completion flag mismatches: {agg['termination_mismatches']}.")
        a("")
        a("| Divergence class | Cases |")
        a("|---|---:|")
        for cls, n in agg["divergence_classes"].items():
            a(f"| `{cls}` | {n} |")
        a("")
        bugs = agg["schemas_with_real_bug_candidates"]
        if bugs:
            a(f"**Candidates for real bugs in our engine: {len(bugs)} "
              f"schemas** - see reproducers/.")
            for b in bugs:
                a(f"- {b['dataset']}/{b['name']}: {b['classes']}")
        else:
            a("No candidates for real bugs in our engine found "
              "(REAL-BUG:* classes absent).")
        a("")
        an = agg["schemas_with_competitor_anomalies"]
        if an:
            a(f"Schemas with divergences on the competitor side "
              f"({len(an)}):")
            for x in an:
                a(f"- {x['dataset']}/{x['name']}: {x['classes']}")
            a("")
    a("## Key observations (manual review)")
    a("")
    a("1. **Low raw bitwise compatibility is expected**: competitors allow "
      "arbitrary whitespace outside strings, canonical-v1 does not "
      "(semantics.md §1, §11). The classes expected:whitespace* and "
      "expected:profile* are exactly the difference of serialization "
      "profiles, confirmed by the oracle tests/reference.py and/or rollout "
      "validation against the source schema (jsonschema).")
    a("2. **xgrammar competitor-undergeneration** (21 734 cases) - two "
      "systemic narrowings of the xgrammar 0.2.6 language relative to "
      "canonical-v1: (a) in strings with minLength/maxLength ALL escape "
      "sequences are forbidden (there is not a single token with `\\\\` in "
      "the mask; reproduced in isolation: "
      "`{\"type\":\"string\",\"minLength\":8,\"maxLength\":64}` - `accept_token(\"\\\\\") == False`, "
      "while in an unconstrained string the escape is allowed); "
      "(b) `-0` is forbidden for integer (no `0` after `-` in the mask). "
      "Both are competitor limitations, not our engine's (semantics.md §2.2, §4.1).")
    a("3. **xgrammar competitor-overgeneration** (9 rollout-confirmed cases "
      "+ family-wise similar ones in expected:profile-unverified): "
      "xgrammar lets through tokens with invalid UTF-8 lead bytes in strings "
      "(0xC0, 0xC1, 0xF5-0xFF) - the rollout produces a document that is not "
      "valid UTF-8 JSON. Our engine validates UTF-8 with a DFA (semantics.md §3).")
    a("4. **llguidance competitor-undergeneration** (7 375) - almost entirely "
      "the token `\\x7f` (DEL): the llguidance string char-class excludes "
      "0x7F, canonical-v1 allows it raw (semantics.md §2.1, RFC 8259 too). "
      "Another 999 divergences are an artifact of the llguidance bitmask "
      "fast-forward approximation (validate_tokens accepts the token, the "
      "bitmask is incomplete; class expected:llguidance-ff-approximation).")
    a("5. **Completion flags** (41/2 \"mismatch\") - a difference of interface "
      "semantics: xgrammar/llguidance set is_terminated/is_stopped only "
      "after an actual consume of EOS; at the point of a completed document "
      "their mask is exactly {EOS} and matches ours (the EOS bit was checked "
      "separately: there are no EOS-bit divergences in the final run).")
    a("6. **xgrammar 0.2.6: GrammarCompiler cache cross-pollution** "
      "(competitor bug): with cache_enabled=True the grammar o21459, "
      "compiled after o10014/o13837/o21458, allowed EOS in the middle of a "
      "string (5 cases of eos-policy-divergence). It does not reproduce with "
      "cache_enabled=False; the final run was executed with the compiler "
      "cache disabled. Reproducer: reproducers/competitor_xgrammar_cache_pollution.json.")
    a("7. **Github_trivial/o48280** (enum of 255 strings): compiles, but "
      "session creation gives RESOURCE_LIMIT \"parser state init limit exceeded\" "
      "at any memory limit: 255 enum alternatives > max_threads_per_state=64, "
      "and the value cannot be raised above 64 (MAX_THREADS_CAP=64, "
      "src/parser.zig). Documented limit behavior "
      "(supported_features.md §6), not a silent weakening.")
    a("")
    a("Raw divergence records (schema, profile, tokenizer revision, "
      "prefix token IDs, differing bits) - `parity_report.json`, "
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
