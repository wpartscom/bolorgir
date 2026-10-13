#!/usr/bin/env python3
"""make_transitions_table.py: the coverage matrix of every transition row
of the candidate 9a5baf3e (TZ section 3.1) - the reproducible table of
all 81 rows and the deduplication to 67 results, with the comparison
line, the result key, the candidate outcome, the outcome on the previous
candidate 6e62e0d1, the D4 entry of the updated list and the evidence
pointer. Writes JSON + Markdown and checks its own counters against the
review's transition list. Nothing is run.

Usage: make_transitions_table.py SET OLD_SET D4 OUT_JSON OUT_MD

  SET      benchmarks/coverage/spec-v1/t3-9a5baf3e
  OLD_SET  benchmarks/coverage/spec-v1/t3-6e62e0d1
  D4       the updated benchmarks/t3/d4_proposals.json revision
  OUT_*    files to write

Exit 0 written; 4 a counter mismatch (nothing written); 2 usage.
"""
import gzip
import hashlib
import json
import os
import sys


def load(p):
    op = gzip.open if p.endswith(".gz") else open
    with op(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def outcomes(set_dir):
    """(corpus, file, tokenizer) -> outcome of the candidate of that set."""
    out = {}
    for snap in ("repo", "maskbench"):
        for suffix in (".json", ".json.gz"):
            p = os.path.join(set_dir, f"coverage_{snap}{suffix}")
            if os.path.exists(p):
                break
        doc = load(p)
        for r in doc["per_file"]:
            for tok, g in (r.get("generatable") or {}).items():
                out[(snap, r["file"], tok)] = g["outcome"]
    return out


def d4_entry(entries, snap, fname, tok, state):
    for e in entries:
        if e.get("corpus") not in (snap, "*") or e.get("file") not in (fname, "*"):
            continue
        if tok not in (e.get("tokenizers") or [tok]):
            continue
        if e.get("match_outcome") not in (None, state):
            continue
        return e
    return None


def main(set_dir, old_dir, d4_path, out_json, out_md):
    acc = load(os.path.join(set_dir, "review", "acceptance.json"))
    tri = load(os.path.join(set_dir, "review", "triage.json"))
    d4 = load(d4_path)
    entries = sorted(d4.get("entries", []), key=lambda e: e.get("file") == "*")
    old = outcomes(old_dir)
    tri_rows = {(r["corpus"], r["file"], r["tokenizer"]): r for r in tri["generatable_rows"]}
    trans = acc["transitions"]

    rows, results = [], {}
    for t in trans:
        key = (t["corpus"], t["file"], t["tokenizer"])
        e = d4_entry(entries, t["corpus"], t["file"], t["tokenizer"], t["candidate"])
        old_outcome = old.get(key, "missing")
        row = {
            "line": t["line"], "corpus": t["corpus"], "file": t["file"],
            "tokenizer": t["tokenizer"], "outcome": t["candidate"],
            "stage": t["stage"], "code": t["code"],
            "line_passes": t["line_passes"], "candidate_passes": t["candidate_passes"],
            "entry": e["id"] if e else None,
            "on_6e62e0d1": old_outcome,
            "transition_on_6e62e0d1": old_outcome not in ("generatable", "missing"),
        }
        rows.append(row)
        r = results.setdefault(key + (t["candidate"],), {
            "corpus": t["corpus"], "file": t["file"], "tokenizer": t["tokenizer"],
            "outcome": t["candidate"], "entry": e["id"] if e else None,
            "lines": [], "rows": 0, "on_6e62e0d1": old_outcome,
            "transition_on_6e62e0d1": old_outcome not in ("generatable", "missing")})
        r["lines"].append(t["line"])
        r["rows"] += 1
    for r in results.values():
        r["lines"].sort()
        if r["outcome"] == "invalid_document":
            tr = tri_rows.get((r["corpus"], r["file"], r["tokenizer"]), {})
            r["triage"] = {"class": tr.get("class"),
                           "documents_checked": (tr.get("invalid_documents") or 0)
                           - (tr.get("documents_missing") or 0),
                           "documents_total": tr.get("invalid_documents"),
                           "documents_missing": tr.get("documents_missing") or 0}

    by_out = {}
    for r in results.values():
        c = by_out.setdefault(r["outcome"], {"rows": 0, "results": 0, "documents": 0})
        c["rows"] += r["rows"]
        c["results"] += 1
        c["documents"] += (r.get("triage") or {}).get("documents_checked", 0)
    counters = {"rows": len(rows), "results": len(results), "by_outcome": by_out,
                "by_line": {ln: sum(1 for r in rows if r["line"] == ln)
                            for ln in sorted({r["line"] for r in rows})},
                "undescribed": sum(1 for r in rows if r["entry"] is None)}
    expect = {"rows": 81, "results": 67, "undescribed": 0,
              "invalid_document": {"rows": 42, "results": 34, "documents": 68},
              "incomplete": {"rows": 27, "results": 25},
              "engine_unavailable": {"rows": 12, "results": 8}}
    if counters["rows"] != expect["rows"] or counters["results"] != expect["results"] \
            or counters["undescribed"] != 0:
        raise SystemExit(f"FAIL: counters {counters}")
    for k, v in expect.items():
        if k in ("rows", "results", "undescribed"):
            continue
        got = counters["by_outcome"].get(k, {})
        for kk, vv in v.items():
            if got.get(kk) != vv:
                raise SystemExit(f"FAIL: {k}.{kk} = {got.get(kk)}, expected {vv}")

    doc = {"about": "Coverage matrix of the transition rows of the candidate 9a5baf3e "
                    "(TZ section 3.1); the D4 entry column is the updated list",
           "candidate_library_sha256": d4.get("candidate_library_sha256"),
           "inputs": {"acceptance": {"path": os.path.join(set_dir, "review", "acceptance.json"),
                                     "sha256": sha256(os.path.join(set_dir, "review", "acceptance.json"))},
                      "triage": {"path": os.path.join(set_dir, "review", "triage.json"),
                                 "sha256": sha256(os.path.join(set_dir, "review", "triage.json"))},
                      "d4": {"path": d4_path, "sha256": sha256(d4_path)},
                      "counters": counters},
           "rows": rows, "results": sorted(results.values(), key=lambda r: (
               r["outcome"], r["corpus"], r["tokenizer"], r["file"]))}
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)
        f.write("\n")

    c = counters
    md = ["# Transition coverage matrix (candidate 9a5baf3e, TZ section 3.1)", "",
          f"{c['rows']} rows, {c['results']} results; lines: "
          + ", ".join(f"{k} {v}" for k, v in c["by_line"].items())
          + f". Outcomes: invalid_document {c['by_outcome']['invalid_document']['rows']} rows / "
          f"{c['by_outcome']['invalid_document']['results']} results / "
          f"{c['by_outcome']['invalid_document']['documents']} invalid documents checked; "
          f"incomplete {c['by_outcome']['incomplete']['rows']} / {c['by_outcome']['incomplete']['results']}; "
          f"engine_unavailable {c['by_outcome']['engine_unavailable']['rows']} / "
          f"{c['by_outcome']['engine_unavailable']['results']}. Undescribed rows: {c['undescribed']}. "
          f"The `on 6e62e0d1` column is the outcome of the previous candidate in its own run "
          "(sidecar records).", "",
          "## Results (deduplicated)", "",
          "| corpus | file | tok | outcome | entry | lines | on 6e62e0d1 | rows | triage |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in doc["results"]:
        tr = r.get("triage")
        md.append(f"| {r['corpus']} | {r['file']} | {r['tokenizer']} | {r['outcome']} | "
                  f"{r['entry'] or '-'} | {','.join(r['lines'])} | {r['on_6e62e0d1']}"
                  f"{'' if r['transition_on_6e62e0d1'] else ' (no transition there)'} | {r['rows']} | "
                  + (f"class {tr['class']}, {tr['documents_checked']}/{tr['documents_total']} checked"
                     if tr else "-") + " |")
    md += ["", "## All rows", "",
           "| line | corpus | file | tok | outcome | entry | on 6e62e0d1 | line passes | candidate passes |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['line']} | {r['corpus']} | {r['file']} | {r['tokenizer']} | {r['outcome']} | "
                  f"{r['entry'] or '-'} | {r['on_6e62e0d1']} | {r['line_passes']} | "
                  f"{r['candidate_passes']} |")
    md += ["", "Evidence per outcome class: invalid_document - `review/triage.json` of the set "
           "(class D rule, documents checked) and `docs/supported_features.md` section 9; "
           "incomplete - `benchmarks/t3/evidence/r11a2_pass1_summary.md`; engine_unavailable - "
           "`benchmarks/t3/evidence/r11bc2_summary.md`; the pass records of both candidate runs - "
           "the sets' `coverage_*.passes.json.gz` sidecars."]
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"{c['rows']} rows, {c['results']} results -> {out_json}, {out_md}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 6:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
