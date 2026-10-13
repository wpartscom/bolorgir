#!/usr/bin/env python3
"""make_d4_update.py: the D4 revision bound to the candidate 9a5baf3e.

Reads the committed revision of benchmarks/t3/d4_proposals.json (the
6e62e0d1 list, sha 8016b0f9...), the R11-A2/R11-C2 summaries of the
2026-10-13 replays, the recorded facts of the t3-9a5baf3e review and the
pass sidecars of both candidate runs, and writes the re-checked list:
updated causes and measurements per entry, the D4-6 text against the
triage of the candidate, the retired entries (no transition on
9a5baf3e), two new entries for the GPT-2 results without one, and the
new derived_from chain. owner_decision stays null everywhere.

Usage: make_d4_update.py BASE R11A_JSON R11BC_JSON FACTS SET OLD_SET OUT

  BASE        benchmarks/t3/d4_proposals.json (read-only)
  R11A_JSON   r11a2/summary.json (R11-A2)
  R11BC_JSON  r11bc2_summary.json (R11-C2/B2)
  FACTS       facts-9a5baf3e.json
  SET         benchmarks/coverage/spec-v1/t3-9a5baf3e
  OLD_SET     benchmarks/coverage/spec-v1/t3-6e62e0d1
  OUT         the new list (write it, review the diff, then copy over)

Exit 0 written; 4 an assertion failed (the summary and the recorded data
disagree - nothing is written); 2 usage.
"""
import gzip
import json
import os
import statistics
import sys

CAND_SHA = "9a5baf3e373ac9a7a048b6a2b9bffda2c1e1e3d2f62b4cc62e70785cc3490c41"
BASE_SHA = "8016b0f9f685470b8096fd54ad0b37e04b72dac85e4a81ced48ffaa1e6a9bdee"
TAG = "t3-9a5baf3e"
OLD_TAG = "t3-6e62e0d1"
TOK_TEXT = {"llama-3.1": "Llama 3.1 (128256 tokens)", "gpt2": "GPT-2 (50257 tokens)",
            "byte": "byte"}


class Fail(Exception):
    pass


def load(p):
    op = gzip.open if p.endswith(".gz") else open
    with op(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def sidecar(set_dir, snap):
    """{(file, token): [pass records]} of one corpus of a published set."""
    doc = load(os.path.join(set_dir, f"coverage_{snap}.passes.json.gz"))
    fields = doc["fields"]
    out = {}
    for _ds, f, toks in doc["rows"]:
        for tok, rows in toks.items():
            out[(f, tok)] = [dict(zip(fields, x)) for x in rows]
    return out


def step_ratio(new_passes, old_passes):
    """median of new/old steps over the passes both end in the time limit."""
    rs = []
    for a, b in zip(new_passes, old_passes):
        if (a["outcome"] == b["outcome"] == "incomplete:time_limit" and b["step"] > 0):
            rs.append(a["step"] / b["step"])
    return (statistics.median(rs), len(rs)) if rs else (None, 0)


def short(p):
    o = {"completed_valid": "cv", "incomplete:time_limit": "tl", "incomplete:step_limit": "sl",
         "engine_unavailable": "eu", "invalid_document": "inv"}.get(p["outcome"], p["outcome"])
    return f"{o}@{p['step']}"


def f2(x):
    return f"{x:.2f}"


def main(base_path, r11a_path, r11bc_path, facts_path, set_dir, old_dir, out_path):
    base = load(base_path)
    S = load(r11a_path)
    BC = load(r11bc_path)
    F = load(facts_path)
    acc = load(os.path.join(set_dir, "review", "acceptance.json"))
    tri = load(os.path.join(set_dir, "review", "triage.json"))
    new_side = {s: sidecar(set_dir, s) for s in ("repo", "maskbench")}
    old_side = {s: sidecar(old_dir, s) for s in ("repo", "maskbench")}
    # the 7345490f line's reports live one level up (benchmarks/coverage/spec-v1)
    line_side = {s: sidecar(os.path.dirname(os.path.abspath(set_dir)), s)
                 for s in ("repo", "maskbench")}

    def topic(snap, f, tok):
        return (new_side[snap].get((f, tok)), old_side[snap].get((f, tok)))

    # ---- recorded data of the candidate run
    items = {(it["snapshot"], it["file"], it["tokenizer"]): it for it in S["items"]}
    facts = {(it["corpus"], it["file"], it["tokenizer"]): it for it in F["items"]}
    if len(items) != 25:
        raise Fail(f"R11-A2 summary lists {len(items)} items, not 25")
    for k, it in items.items():
        if k not in facts:
            raise Fail(f"{k} is not a transition result of the facts")
    noise = S["noise"]
    th = S["threshold"]
    agg = S["ratios"]

    # ---- transitions and their entries
    trans = acc["transitions"]
    keys_by_entry = {}
    for t in trans:
        keys_by_entry.setdefault(t["d4"], []).append((t["corpus"], t["file"], t["tokenizer"], t["candidate"]))
    undescribed = [t for t in trans if t["d4"] is None]
    und_files = sorted(t["file"] for t in undescribed)
    if und_files != ["Github_easy---o64825.json", "Github_easy---o68314.json"]:
        raise Fail(f"expected the two GPT-2 rows undescribed, got {und_files}")

    # ---- D4-6 against the triage of the candidate
    uniq_inv = set()
    for t in trans:
        if t["candidate"] == "invalid_document":
            uniq_inv.add((t["corpus"], t["file"], t["tokenizer"]))
    tri_rows = {(r["corpus"], r["file"], r["tokenizer"]): r for r in tri["generatable_rows"]}
    docs = 0
    for k in uniq_inv:
        r = tri_rows.get(k)
        if r is None or r.get("class") != "D" or r.get("documents_missing"):
            raise Fail(f"D4-6: {k} triage {None if r is None else (r.get('class'), r.get('documents_missing'))}")
        docs += r.get("invalid_documents") or 0
    if (len(uniq_inv), docs) != (34, 68):
        raise Fail(f"D4-6: {len(uniq_inv)} results / {docs} documents, expected 34 / 68")
    inv_rows = [t for t in trans if t["candidate"] == "invalid_document"]
    n73 = sum(1 for t in inv_rows if t["line"] == "7345490f")
    gate = next((c["detail"] for c in acc["checks"]
                 if c.get("check") == "triage" and c.get("detail")), "")
    gate = gate.split("(")[-1].rstrip(")") if "(" in gate else ""

    # ---- engine_unavailable re-check
    if BC["problems"]:
        raise Fail(f"R11-C2 problems: {BC['problems']}")
    if not all(e["as_recorded"] for e in BC["C"]):
        raise Fail("R11-C2: a pass did not reproduce its record")

    # ---- builders

    def six_numbers(it):
        pl = it["per_library"]
        return (f"candidate {f2(pl['cand']['M'])} s, the same source built for the host CPU "
                f"{f2(pl['native']['M'])} s, 6e62e0d1 (the previous candidate) {f2(pl['prev']['M'])} s, "
                f"50cbd80e {f2(pl['50cbd80e']['M'])} s, 7345490f {f2(pl['7345490f']['M'])} s, "
                f"dbc5eeb {f2(pl['dbc5eeb']['M'])} s")

    def completes_text(it):
        fires = []
        total = 0
        for lab in ("cand", "native", "prev", "50cbd80e", "7345490f", "dbc5eeb"):
            for r, done in enumerate(it["per_library"][lab]["completes_at_10s"], 1):
                total += 1
                if not done:
                    fires.append(f"{lab} round {r}")
        if not fires:
            return f"pass 1 completes under the 10 s budget on every one of the {total} library-rounds"
        return (f"pass 1 exceeds the 10 s budget on {len(fires)} of {total} library-rounds "
                f"({', '.join(fires)})")

    def cause_pass1(it, facts_item):
        k = (it["snapshot"], it["file"], it["tokenizer"])
        fi = facts_item
        steps = sorted({x["line"].split("@")[1] for x in fi["line_completed"].get("7345490f", [])})
        s_txt = (f"the line's cv@{steps[0]}" if len(steps) == 1 else
                 f"the line's cv@{', '.join(steps)}")
        c = it["c_star"]
        tgt = it["ratios"]["target"]
        code = it["ratios"]["code"]
        prev = it["ratios"]["cand/6e62e0d1"]
        end = it["per_library"]["cand"]["end_median"]
        tail = ", longer than 10 s and ending between two checks" if end and end > 10.0 else ""
        two_c = (f"the two candidates differ by the ratio {prev:.3f} at this check" if prev else
                 "the previous candidate was not measured")
        what = ("the release recipe -Dcpu=baseline (no AVX2/POPCNT/BMI) is the leading "
                "hypothesis for that cost (pre-registered rule)" if tgt - 1 > th else
                "no build difference is separable beyond the noise by this rule")
        return (f"pass 1 at the edge of its 10 s budget: pass 1 ends at step {steps[-1]} ({s_txt}); "
                f"at its budget check at step {c} it needs, on a quiet core (R11-A2, CPU 2, median of "
                f"3 rounds): {six_numbers(it)}; in the T3 run of 9a5baf3e (8 jobs) the candidate "
                f"recorded the time limit at step {c}, the first check it failed. The candidate needs "
                f"{(tgt - 1) * 100:.1f} % more time than its own source built for the host CPU at "
                f"this check (heuristic threshold {th * 100:.1f} %, the round-to-round noise; not a "
                f"confidence interval), {two_c}, and the source's code against 50cbd80e is "
                f"{code:.3f}: {what} on the "
                f"{TOK_TEXT[it['tokenizer']]} mask fill. The pass-1 document, and with it the greedy "
                f"path, is the same on all six libraries (equal documents; token paths and masks were "
                f"not recorded and are not compared); on this quiet "
                f"machine {completes_text(it)}; the whole pass takes {f2(end)} s on the candidate "
                f"(median of 3 rounds){tail}")

    def evidence_a2(it):
        return (f"benchmarks/t3/evidence/r11a2_pass1_summary.md ({it['file']}); "
                f"benchmarks/t3/evidence/r11a2_pass1_summary.json")

    def retired_text(k3):
        snap, f, tok = k3
        new_p, old_p = topic(snap, f, tok)
        e = new_p[0]
        steps = sorted({str(p["step"]) for p in line_side[snap][(f, tok)]
                        if p["outcome"] == "completed_valid"})
        ratio, n = step_ratio(new_p, old_p)
        oc = old_p[0]["step"]
        return {
            "summary": (f"no transition on 9a5baf3e: the candidate completed pass 1 "
                        f"(cv@{e['step']}) in its T3 run of 2026-10-13, generatable. The row "
                        f"transitioned on 6e62e0d1 (the candidate recorded a time limit at step {oc}; "
                        f"the line completed pass 1 at step {steps[-1]})"),
            "cause": ("the pass sat at the edge of its 10 s budget on 6e62e0d1 (R11-A, CPU 2, median of "
                      "3 rounds, at the check the candidate failed); on 9a5baf3e the same deterministic "
                      f"pass 1 completed in the run, and in the recorded runs the time-limited passes "
                      f"2-20 of the two candidates reach the same steps (median ratio {ratio:.3f} over "
                      f"{n} passes; compatible with, not proof of, equal speed). The changed recorded "
                      "outcome is consistent with the run's conditions at the check (hypothesis; a "
                      "small timing difference is not excluded by these records). No replay covers "
                      "this row in R11-A2 (no transition); the 6e62e0d1 "
                      "measurements stay under R11 (benchmarks/t3/evidence.md) and in the entry's "
                      "previous text"),
            "proposal": ("no owner decision: no transition on the current candidate; kept for the record "
                         "and as a witness of the budget-edge behaviour of the group. A transition on a "
                         "later candidate reopens the row"),
        }

    # ---- transform
    out = json.loads(json.dumps(base))  # deep copy, order kept
    out["candidate_library_sha256"] = CAND_SHA
    out["about"] = (base["about"]
                    + " This revision is bound to the candidate 9a5baf3e (the T3 acceptance run of "
                      "2026-10-13): every entry was re-checked against that candidate's reports, its "
                      "triage and the fresh replays of 2026-10-13 (R11-A2, R11-C2/B2), not carried "
                      "over from the 6e62e0d1 revision; the earlier text of substantially changed "
                      "entries is kept under `previous`")
    hist = dict(base["derived_from"])
    out["derived_from"] = {
        "revision": ("the D4 revision of the candidate 9a5baf3e (T3 2026-10-13): every entry "
                     "re-checked on 9a5baf3e; substantially changed entries keep the preceding "
                     "text under `previous`; new entries D4-39 and D4-40 for the two GPT-2 "
                     "results that transitioned without one"),
        "revises_sha256": BASE_SHA,
        "revises": ("the revision of the list for 6e62e0d1 (used by its third review and review-3 "
                    "verdict), committed as benchmarks/t3/d4_proposals.json at 83ca2e2"),
        "history": hist,
        "measurements": ("R11-A2 and R11-C2/B2 of 2026-10-13 on the candidate 9a5baf3e: "
                         "benchmarks/t3/evidence/r11a2_pass1_summary.md, r11bc2_summary.md; "
                         "the runs: benchmarks/local/t3/de8/d4f/"),
    }

    groups = out["groups"]
    cpl = S["completes_at_10s"]["cand"]
    groups["pass1_budget_edge"] = (
        "24 incomplete results (D4-13, D4-14, D4-17 to D4-23, D4-25 to D4-32, D4-34 to D4-38, "
        "D4-39, D4-40) whose only line pass that completed is "
        "pass 1, the greedy policy pass, which checks its 10 s budget at steps 16, 32, 48, ...; the "
        "candidate records the time limit at the first check it failed. R11-A2 re-ran pass 1 of all 25 "
        "incomplete results on six libraries - candidate 9a5baf3e (release recipe -Dcpu=baseline), the "
        "same source built for the host CPU, the previous candidate 6e62e0d1, 50cbd80e, 7345490f, "
        "dbc5eeb - with the run's harness, tokenizers and schemas on one quiet P-core (3 rounds, "
        "rotated order; the rule fixed before the run in benchmarks/local/t3/de8/d4f/README_PLAN.md): "
        f"the pass-1 document, and with it the greedy path, is the same on every library in "
        f"{S['same_path']} of {S['results']} results (equal documents; token paths and masks were "
        f"not recorded and are not compared). The candidate needs "
        f"{(agg['target']['median'] - 1) * 100:.1f} % more time than its own source built for the host "
        f"CPU (median over results at the deciding check, range {agg['target']['min']:.3f}-"
        f"{agg['target']['max']:.3f}; pre-registered heuristic threshold {th * 100:.1f} %, the "
        f"round-to-round noise; not a confidence interval; {agg['target']['above_1']}/"
        f"{agg['target']['n']} results above 1), the code against 50cbd80e is "
        f"{agg['code']['median']:.3f}. Leading hypothesis by the rule: {S['heuristic_reading']}. "
        f"Limits: {S['interpretation_limits']} The release recipe builds "
        "with -Dcpu=baseline (the release workflow did so already at dbc5eeb: "
        ".github/workflows/release.yml); the lines' libraries were host builds. In the recorded runs "
        "the time-limited passes 2-20 of 6e62e0d1 and 9a5baf3e reach the same steps (median ratio ~1; "
        "compatible with, not proof of, equal speed), and the pass-1 flips between the two runs go "
        "both ways: four rows completed on 9a5baf3e that "
        "recorded a limit on 6e62e0d1 (D4-15, D4-16, D4-24, D4-33, now retired), and the two GPT-2 "
        "rows recorded a limit that completed on 6e62e0d1 and in the line (D4-39, D4-40). Hypothesis "
        "(stated as such): the differing recorded pass-1 outcomes follow the run's conditions at the "
        "10 s check; the cause of the flips is not established beyond this. Amendment "
        "A1 (README_PLAN.md): the editor session keeps the machine's 1-minute load average at "
        "1.7-3.0; R11-A's gate of 1.5 could not pass, the round ran with R11_LOAD_MAX=2.5, and every "
        "replay process recorded its loadavg_start in its JSONL head")
    groups["pass1_series_slowdown"] = (
        "retired on 9a5baf3e: the result (D4-15) completes pass 1 and no longer transitions; the "
        "recorded facts of the 6e62e0d1 round are kept in the entry's previous text and under R11 "
        "(benchmarks/t3/evidence.md)")
    kb = items[("maskbench", "Kubernetes---kb_1154_Normalized.json", "llama-3.1")]
    pl = kb["per_library"]
    kb_min = min(pl[l]["M"] for l in ("cand", "native", "prev", "50cbd80e", "7345490f", "dbc5eeb"))
    groups["expensive_pass1"] = (
        f"kb_1154: pass 1 costs about 1 s per fill on every library and exceeds 10 s at step 16 on "
        f"every library with the run's harness (R11-A2, CPU 2, median of 3 rounds: {six_numbers(kb)}; "
        f"the smallest exceeds the budget; {completes_text(kb)}); the pass-1 document is the same on "
        f"all six libraries. The cause of 7345490f's completion of this pass 1 in its own run is not "
        f"established (R2b, R11-A; no library reproduces it with the run's harness)")
    groups["engine_unavailable"] = (
        "byte-level RESOURCE_LIMIT passes: R11-C2 re-ran every recorded pass on the candidate "
        "9a5baf3e with the recorded seed and 10 s: outcome, stage, code, step, prefix length and seed "
        "equal the T3 record of every pass (as recorded); the same passes on 50cbd80e (host CPU, "
        "before stages C and D) and the R11-B2 300 s runs of the lines on MaskBench bigquery-table "
        "are in benchmarks/t3/evidence/r11bc2_summary.md. If the prefix bytes were compared between "
        "replays only: the lines' state on MaskBench bigquery-table (R11-B2) against the candidate's "
        "(R11-C2). The undecided reasons are listed per entry")
    groups["invalid_document"] = "D4-6, the T2c class D rule of the candidate 9a5baf3e."
    groups["no_transition"] = ("D4-1, D4-2r, D4-7, D4-8, D4-9: kept for the record. D4-15, D4-16, "
                               "D4-24, D4-33: no transition on 9a5baf3e (the candidate completed "
                               "pass 1); the rows transitioned on 6e62e0d1 and are kept for the record")

    entries = out["entries"]
    by_id = {e["id"]: e for e in entries}
    changes = []

    def set_changed(e, fields):
        """previous := the immediately preceding revision's text of the
        changed fields (the base file itself keeps the deeper chain)."""
        prev = {}
        for f in fields:
            if f in e:
                prev[f] = e[f]
        e["previous"] = prev

    # pass1 group entries with an item: regenerate cause and evidence
    for e in entries:
        eid = e["id"]
        if e.get("group") == "pass1_budget_edge" and eid not in ("D4-15", "D4-16", "D4-24", "D4-33"):
            key = (e["corpus"], e["file"], e["tokenizers"][0])
            it = items.get(key)
            if it is None:
                raise Fail(f"{eid} has no R11-A2 item {key}")
            set_changed(e, ["cause", "evidence"])
            e["cause"] = cause_pass1(it, facts[key])
            e["evidence"] = evidence_a2(it) + "; 50cbd80e and the lines: the recorded runs' sidecars"
            changes.append(f"{eid}: cause/evidence regenerated (R11-A2)")

    # retired entries
    for eid, key in (("D4-15", ("repo", "calculate_area_4c8e9fd1.json", "byte")),
                     ("D4-16", ("repo", "kb_1008_Normalized.json", "gpt2")),
                     ("D4-24", ("repo", "o43083.json", "llama-3.1")),
                     ("D4-33", ("maskbench", "Glaiveai2K---generate_invoice_1c946d60.json", "gpt2"))):
        e = by_id[eid]
        set_changed(e, ["summary", "cause", "proposal", "evidence"])
        new = retired_text(key)
        e["summary"], e["cause"], e["proposal"] = new["summary"], new["cause"], new["proposal"]
        e["evidence"] = (f"benchmarks/t3/evidence/r11a_pass1_summary.md ({key[1]}) - the 6e62e0d1 "
                         f"round; the recorded pass sidecars of t3-6e62e0d1 and t3-9a5baf3e")
        changes.append(f"{eid}: retired (no transition on 9a5baf3e)")

    # D4-2 (kb_1154, expensive pass 1)
    e = by_id["D4-2"]
    set_changed(e, ["cause", "evidence"])
    e["cause"] = (f"an expensive pass 1 on every library: pass 1 ends at step 31 (the line's cv@31); "
                  f"at its budget check at step 16 it needs, on a quiet core (R11-A2, CPU 2, median of "
                  f"3 rounds): {six_numbers(kb)}; every library exceeds 10 s at step 16, the candidate "
                  f"in less time than both lines. The pass-1 document, and with it the greedy path, is "
                  f"the same on all six libraries (full masks were not compared). 7345490f completed "
                  f"pass 1 (cv@31) in its run of 2026-10-03 with that day's harness (dbc5eeb+dirty, no "
                  f"source hash recorded); no library reproduces that with the run's harness (R2b: "
                  f"7345490f 17.5 s in R11-A; R11-A2: {completes_text(kb)}), and the cause of the "
                  f"historical completion is not established")
    e["evidence"] = (f"benchmarks/t3/evidence/r11a2_pass1_summary.md ({e['file']}); "
                     f"benchmarks/t3/evidence.md, section R11 and R2b; "
                     f"benchmarks/t3/evidence/kb1154_slow_prefix.hex")
    changes.append("D4-2: measurements replaced by R11-A2")

    # D4-6
    e = by_id["D4-6"]
    set_changed(e, ["summary", "evidence"])
    e["summary"] = (f"results that end in an invalid_document explained by the draft-04 integer "
                    f"spelling: on 9a5baf3e {len(inv_rows)} rows ({n73} against 7345490f, "
                    f"{len(inv_rows) - n73} against dbc5eeb), {len(uniq_inv)} results, every one T2c "
                    f"class D with all {docs} invalid documents checked (review/triage.json of the "
                    f"t3-9a5baf3e set{', gate ' + gate if gate else ''})")
    e["evidence"] = (f"benchmarks/coverage/spec-v1/t3-9a5baf3e/review/triage.md and triage.json "
                     f"(T2c of the candidate); docs/supported_features.md section 9")
    changes.append("D4-6: bound to the 9a5baf3e triage")

    # engine_unavailable entries: refresh the replay references
    eu_ids = ("D4-3", "D4-3m", "D4-4", "D4-5", "D4-10", "D4-11", "D4-11m", "D4-12")
    for eid in eu_ids:
        e = by_id[eid]
        if "cause" in e:
            e["cause"] = e["cause"].replace("R11-C: ", "R11-C2 (re-run on 9a5baf3e): ")
        ev = e.get("evidence", "")
        ev = ev.replace("benchmarks/t3/evidence/r11bc_summary.md",
                        "benchmarks/t3/evidence/r11bc2_summary.md")
        if "r11bc2_summary" not in ev:
            ev = ev + "; benchmarks/t3/evidence/r11bc2_summary.md"
        if "R11-C2" not in ev:
            ev = ev + "; R11-C2: every pass re-run on 9a5baf3e as recorded"
        e["evidence"] = ev
        changes.append(f"{eid}: R11-C2 referenced")

    # B2 numbers for D4-11m
    e = by_id["D4-11m"]
    b = {(x["label"], x["pass"]): x for x in BC["B"]}
    if ("dbc5eeb", 5) in b:
        parts = []
        for lab, budget in (("50cbd80e", 10), ("7345490f", 300), ("dbc5eeb", 300)):
            for p in (5, 12):
                x = b.get((lab, p))
                if x:
                    parts.append(f"{lab} pass {p} {x['outcome']}@{x['step']} in {x['seconds']} s "
                                 f"(budget {budget} s)")
        set_changed(e, ["cause"])
        e["cause"] = ("as D4-11: the MaskBench copy of the repository schema (identical JSON) under "
                      "other seeds; R11-C2: every pass on 9a5baf3e as recorded; R11-B2: " +
                      "; ".join(parts) +
                      ("; every re-run reaches the candidate's state (RESOURCE_LIMIT at the same "
                       "step); the prefixes are byte-equal where the replay records them"
                       if all(x["candidate_state"] for x in BC["B"]) else
                       "; rows that do not reach the candidate's state are named above"))
        changes.append("D4-11m: R11-B2 numbers")

    # record entries: light note
    for eid in ("D4-1", "D4-2r", "D4-7", "D4-8", "D4-9"):
        e = by_id[eid]
        if "summary" in e:
            set_changed(e, ["summary"])
            if "on 6e62e0d1" in e["summary"]:
                e["summary"] = e["summary"].replace("no transition on 6e62e0d1:",
                                                    "no transition on 9a5baf3e (as on 6e62e0d1):", 1)
            changes.append(f"{eid}: re-checked note")

    # two new entries
    new_entries = []
    for fname, pos in (("Github_easy---o64825.json", 39), ("Github_easy---o68314.json", 40)):
        key = ("maskbench", fname, "gpt2")
        it = items[key]
        fi = facts[key]
        new_p, old_p = topic(*key)
        ratio, n = step_ratio(new_p, old_p)
        rounds_txt = "; ".join(
            f"{lab} {[round(x, 2) for x in it['per_library'][lab]['M_rounds']]} s (spread "
            f"{100 * (it['per_library'][lab]['spread'] or 0):.1f} %)"
            for lab in ("cand", "native", "prev", "50cbd80e", "7345490f", "dbc5eeb"))
        e = {
            "id": f"D4-{pos}",
            "corpus": "maskbench",
            "file": fname,
            "tokenizers": ["gpt2"],
            "match_outcome": "incomplete",
            "group": "pass1_budget_edge",
            "summary": (f"incomplete (incomplete:time_limit 20); the line's one completed pass is "
                        f"pass 1 (7345490f cv@{fi['passes']['7345490f'][0].split('@')[1]}, first "
                        f"run), the candidate recorded pass 1 as a time limit at step {it['c_star']}, "
                        f"the first budget check it failed; on 6e62e0d1 the same pass 1 completed "
                        f"(cv@{old_p[0]['step']}) in its run"),
            "cause": (cause_pass1(it, fi) +
                      f". Per-round check times on the quiet core (R11-A2): {rounds_txt}. In the "
                      f"recorded runs the time-limited passes 2-20 of the two candidates reach the "
                      f"same steps (same seeds; median step ratio {ratio:.3f} over {n} passes; "
                      f"compatible with, but not proof of, equal speed). The deterministic pass-1 "
                      f"document is the same on every library, so a difference in the document is "
                      f"excluded; a small timing difference between the two candidates is not "
                      f"excluded by these data. Hypothesis (stated as such): the differing recorded "
                      f"pass-1 outcomes follow the run's conditions at the 10 s check; the cause is "
                      f"not established beyond this. The historical record keeps the prefix length "
                      f"only, not its bytes; byte equality is not claimed"),
            "proposal": ("accept as a timing transition of the portable release target at the pass-1 "
                         "budget edge, as the group (the outcome flips between recorded runs in both "
                         "directions; the quiet-core margins above carry the same pass on every "
                         "library with over 2 s of headroom). The alternative is a protocol decision "
                         "(a pass-1 rule the loaded run's timing can meet reproducibly, e.g. a budget "
                         "or retry rule checked against these replays), not a candidate fix; the "
                         "owner chooses"),
            "evidence": (evidence_a2(it) +
                         f"; the recorded pass sidecars of t3-6e62e0d1 and t3-9a5baf3e "
                         f"(coverage_maskbench.passes.json.gz), the pass-1 records, the passes 2-20 "
                         f"step comparison and the per-round times above"),
            "candidate_rows": [{"corpus": "maskbench", "file": fname, "tokenizer": "gpt2",
                                "outcome": "incomplete"}],
            "owner_decision": None,
        }
        new_entries.append(e)
        changes.append(f"D4-{pos}: new entry for {fname}")
    out["entries"] = entries + new_entries

    # ---- re-simulate the tool's matching on the new file
    def matches(doc, snap, fname, tok, state):
        for e in sorted(doc["entries"], key=lambda e: e.get("file") == "*"):
            if e.get("corpus") not in (snap, "*") or e.get("file") not in (fname, "*"):
                continue
            if tok not in (e.get("tokenizers") or [tok]):
                continue
            if e.get("match_outcome") not in (None, state):
                continue
            return e
        return None
    for t in trans:
        if matches(out, t["corpus"], t["file"], t["tokenizer"], t["candidate"]) is None:
            raise Fail(f"still undescribed: {t['file']} {t['tokenizer']} {t['candidate']}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"written {out_path}")
    for c in changes:
        print(" -", c)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 8:
        print(__doc__)
        sys.exit(2)
    try:
        sys.exit(main(*sys.argv[1:]))
    except Fail as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(4)
