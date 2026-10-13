#!/usr/bin/env python3
"""r11a2_summary.py OUT: the summary of R11-A2 (r11a2.sh) with the
decision rule fixed before the run (d4f/README_PLAN.md):

  M(result, library, round)  elapsed time of pass 1 when it reaches the
      budget check at which the candidate's acceptance run recorded its
      time limit (step c*, the item's c_star: 16 for Llama 3.1, 32 or 48
      for GPT-2) - the check that decides the transition; the end time E
      when the pass ends before c*.
  M(result, library)         the median over the rounds.
  noise                      the median over results and libraries of
      max/min of M over the rounds, minus 1; threshold th = max(0.02,
      noise).
  ratios                     per result, then their median over the 25
      results: target = M(cand)/M(native) (the CPU target, same source),
      code = M(native)/M(50cbd80e) (stages C and D, host CPU),
      cand/6e62e0d1 (the two candidates against each other: context, not
      attribution), M(cand)/M(7345490f), M(cand)/M(dbc5eeb),
      M(50cbd80e)/M(7345490f). A ratio is a difference when its median
      is beyond 1 +- th.
  attribution                NO such claim: the pre-registered reading rule
      (target beyond the threshold and code not: the leading hypothesis is
      the release CPU target; code beyond: the code; neither: not
      separable) is kept as a diagnostic heuristic and reported under
      `heuristic_reading`, separate from the numbers. The threshold
      max(2 %, noise) is not a confidence interval: medians inside it do
      not prove equivalence, medians outside it do not prove a cause.
  path                       pass 1 is deterministic: its document must
      be the same on every library and round, else the result is listed
      as "masks differ" and left out of the timing conclusion. A same
      document hash proves the documents equal; it does not prove equal
      token paths or equal masks (neither was recorded here).

Also per library and round: how many of the 25 complete under the run's
10 s budget on these times (descriptive: a quiet machine is not the
run's load), and the mean fill time before c*. Writes OUT/summary.json
and OUT/summary.md; exit 0, or 1 when a file is missing or inconsistent.
"""
import json
import os
import statistics
import sys

LABELS = ("cand", "native", "prev", "50cbd80e", "7345490f", "dbc5eeb")
RATIOS = (("target", "cand", "native"), ("code", "native", "50cbd80e"),
          ("cand/6e62e0d1", "cand", "prev"),
          ("cand/7345490f", "cand", "7345490f"), ("cand/dbc5eeb", "cand", "dbc5eeb"),
          ("50cbd80e/7345490f", "50cbd80e", "7345490f"))


def main(out):
    man = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
    items = man["items"]
    rounds = man["rounds"]
    cstar = {(it["snapshot"], it["file"], it["tokenizer"]): it["c_star"] for it in items}
    rows, problems = {}, []
    for r in range(1, rounds + 1):
        for lab in LABELS:
            p = os.path.join(out, f"r{r}-{lab}.jsonl")
            if not os.path.exists(p):
                problems.append(f"missing {os.path.basename(p)}")
                continue
            lines = [json.loads(x) for x in open(p, encoding="utf-8")]
            head, body = lines[0], [x for x in lines[1:] if "file" in x]
            if head["library_sha256"] != man["libraries"][lab]["sha256"]:
                problems.append(f"{os.path.basename(p)}: library {head['library_sha256'][:12]}")
            if len(body) != len(items):
                problems.append(f"{os.path.basename(p)}: {len(body)} results")
            for x in body:
                rows[(x["snapshot"], x["file"], x["tokenizer"], lab, r)] = x
    res = []
    for it in items:
        k = (it["snapshot"], it["file"], it["tokenizer"])
        c = cstar[k]
        per = {}
        docs = set()
        for lab in LABELS:
            ms, done, fill, ends = [], [], [], []
            for r in range(1, rounds + 1):
                x = rows.get(k + (lab, r))
                if not x or "error" in x:
                    problems.append(f"{k} {lab} round {r}: {x and x.get('error')}")
                    continue
                docs.add(x["document_sha256"])
                at = dict((s, t) for s, t in x["checks"])
                ms.append(at.get(c, x["seconds"]))
                ends.append(x["seconds"])
                done.append(x["at_run_budget"]["outcome"] == "completed")
                fill.append(statistics.mean(x["fill_ms"][:c]) if x["fill_ms"] else None)
            per[lab] = {"M": statistics.median(ms) if ms else None, "M_rounds": ms,
                        "end_median": statistics.median(ends) if ends else None,
                        "spread": (max(ms) / min(ms) - 1) if ms and min(ms) > 0 else None,
                        "completes_at_10s": done,
                        "fill_ms_before_c": statistics.median([f for f in fill if f is not None]) if fill else None}
        ratios = {name: (per[a]["M"] / per[b]["M"] if per[a]["M"] and per[b]["M"] else None)
                  for name, a, b in RATIOS}
        res.append({"snapshot": k[0], "file": k[1], "tokenizer": k[2], "d4": it.get("d4"),
                    "c_star": c, "same_path": len(docs) == 1, "documents": len(docs),
                    "per_library": per, "ratios": ratios})
    timed = [x for x in res if x["same_path"]]
    spreads = [x["per_library"][lab]["spread"] for x in timed for lab in LABELS
               if x["per_library"][lab]["spread"] is not None]
    noise = statistics.median(spreads) if spreads else None
    th = max(0.02, noise or 0.0)
    agg = {}
    for name, _a, _b in RATIOS:
        v = [x["ratios"][name] for x in timed if x["ratios"][name]]
        med = statistics.median(v) if v else None
        agg[name] = {"median": med, "min": min(v) if v else None, "max": max(v) if v else None,
                     "above_1": sum(1 for y in v if y > 1), "n": len(v),
                     "difference": med is not None and abs(med - 1) > th}
    t, c = agg["target"]["difference"], agg["code"]["difference"]
    reading = ("the release CPU target (-Dcpu=baseline) as the leading hypothesis for the "
               "target ratio" if t and not c else
               "the code of stages C/D and the CPU target as the leading hypothesis" if t and c else
               "the code of stages C/D as the leading hypothesis" if c else
               "neither separable by this rule")
    limits = ("Pre-registered reading rule, kept as a diagnostic heuristic: target = M(cand)/M(native) "
              "and code = M(native)/M(50cbd80e) medians against the threshold max(2 %, noise). The "
              "threshold is not a confidence interval; a median inside it does not prove equivalence, "
              "one outside it does not prove a cause, and the rule cannot attribute the residual to "
              "run conditions - it only says the measured ratio difference is not separable from the "
              "round-to-round noise by this design. Same document = equal documents; token paths and "
              "masks were not recorded.")
    completes = {lab: [sum(1 for x in res if x["per_library"][lab]["completes_at_10s"][r - 1:r] == [True])
                       for r in range(1, rounds + 1)] for lab in LABELS}
    doc = {"rounds": rounds, "results": len(res), "same_path": len(timed), "noise": noise,
           "threshold": th, "ratios": agg, "heuristic_reading": reading,
           "interpretation_limits": limits,
           "completes_at_10s": completes, "problems": problems, "items": res}
    with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
    md = ["# R11-A2: pass 1 of the incomplete transition results of 9a5baf3e, six libraries", "",
          "## Numbers", "",
          f"{len(res)} results, {rounds} rounds; same pass-1 document on every library: "
          f"{len(timed)}. Noise (median max/min - 1 over rounds): "
          f"{noise if noise is None else round(noise, 4)}; threshold {th:.3f}.", "",
          "| ratio (median over results) | median | min | max | results > 1 | difference |",
          "|---|---|---|---|---|---|"]
    for name, v in agg.items():
        md.append(f"| {name} | {v['median']:.3f} | {v['min']:.3f} | {v['max']:.3f} | "
                  f"{v['above_1']}/{v['n']} | {'yes' if v['difference'] else 'no'} |"
                  if v["median"] is not None else f"| {name} | - | - | - | - | - |")
    md += ["", "## Reading (heuristic, not proof)", "",
           f"Reading rule (pre-registered): {reading}.", "",
           f"Limits: {limits}", "",
           "Complete under the 10 s budget on these times (per round): "
           + "; ".join(f"{lab} {v}" for lab, v in completes.items()), "",
           "| result | tok | c* | " + " | ".join(f"M {lab}" for lab in LABELS)
           + " | target | code | path |", "|---|---|---|" + "---|" * (len(LABELS) + 3)]
    for x in res:
        pl = x["per_library"]
        md.append(f"| {x['snapshot']}/{x['file']} | {x['tokenizer']} | {x['c_star']} | "
                  + " | ".join(f"{pl[lab]['M']:.2f}" if pl[lab]["M"] else "-" for lab in LABELS)
                  + f" | {x['ratios']['target'] or 0:.3f} | {x['ratios']['code'] or 0:.3f} | "
                  + ("same" if x["same_path"] else f"{x['documents']} documents") + " |")
    if problems:
        md += ["", "Problems:", ""] + [f"- {p}" for p in problems]
    with open(os.path.join(out, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md[:16]))
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
