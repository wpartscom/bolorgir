#!/usr/bin/env python3
"""r11bc2_summary.py DIR: the summary of R11-B2 and R11-C2 (r11bc2.sh)
from DIR/r11c2/*.cand.jsonl and DIR/r11b2/*.jsonl.

C: per replayed pass of the candidate 9a5baf3e - reproduced
(as_recorded), the engine message and the undecided-reason histogram.
B: per line library and pass of MaskBench bigquery-table - its end
(outcome, step, seconds) and whether it is the candidate's state:
engine_unavailable at the same step with the same prefix. Writes
DIR/r11bc2_summary.json and .md; exit 0, 1 when a candidate pass did not
reproduce its record or a file is missing."""
import glob
import json
import os
import sys


def rows(p):
    with open(p, encoding="utf-8") as f:
        lines = [json.loads(x) for x in f if x.strip()]
    return lines[0], [x for x in lines[1:] if "k" in x]


def main(d):
    out = {"C": [], "B": [], "C50": []}
    bad = []
    cand_prefix = {}
    for p in sorted(glob.glob(os.path.join(d, "r11c2", "*.cand.jsonl"))):
        head, body = rows(p)
        if not body:
            bad.append(f"{os.path.basename(p)}: no pass")
        for x in body:
            e = {"file": head["file"], "snapshot": head.get("snapshot"), "pass": x["k"],
                 "outcome": x["outcome"], "step": x["step"], "seconds": x["seconds"],
                 "as_recorded": x["as_recorded"], "msg": x.get("msg", ""), "tail": x.get("tail", "")}
            out["C"].append(e)
            if not x["as_recorded"]:
                bad.append(f"{head['file']} pass {x['k']}: not as recorded ({x['outcome']} at {x['step']}, "
                           f"recorded {x.get('recorded')})")
            if x.get("prefix_hex"):
                cand_prefix[(head["file"], x["k"])] = (x["step"], x["prefix_hex"])
    for p in sorted(glob.glob(os.path.join(d, "r11c2", "*.50cbd80e.jsonl"))):
        head, body = rows(p)
        if not body:
            bad.append(f"{os.path.basename(p)}: no pass")
        for x in body:
            c = cand_prefix.get((head["file"], x["k"]))
            same = (c is not None and x["outcome"] == "engine_unavailable" and x["step"] == c[0])
            out["C50"].append({"file": head["file"], "pass": x["k"], "outcome": x["outcome"],
                               "step": x["step"], "seconds": x["seconds"],
                               "same_as_candidate": same, "msg": x.get("msg", "")})
    for p in sorted(glob.glob(os.path.join(d, "r11b2", "*.jsonl"))):
        head, body = rows(p)
        if not body:
            bad.append(f"{os.path.basename(p)}: no pass")
        for x in body:
            c = cand_prefix.get((head["file"], x["k"]))
            same = (c is not None and x["outcome"] == "engine_unavailable" and x["step"] == c[0]
                    and x.get("prefix_hex") == c[1])
            out["B"].append({"label": head["label"], "budget": head["max_seconds"], "file": head["file"],
                             "pass": x["k"], "outcome": x["outcome"], "step": x["step"],
                             "seconds": x["seconds"], "candidate_state": same,
                             "msg": x.get("msg", "")})
    out["problems"] = bad
    with open(os.path.join(d, "r11bc2_summary.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    md = ["# R11-C2: the engine_unavailable passes of 9a5baf3e, replayed", "",
          "| file | pass | end | seconds | as recorded | engine message, undecided reasons |",
          "|---|---|---|---|---|---|"]
    for e in out["C"]:
        md.append(f"| {e['file']} | {e['pass']} | {e['outcome']}@{e['step']} | {e['seconds']} | "
                  f"{'yes' if e['as_recorded'] else 'NO'} | {e['msg'][:160]} |")
    md += ["", "# R11-C2: the same passes on 50cbd80e (host CPU, before stages C and D)", "",
           "| file | pass | end | seconds | the candidate's state |", "|---|---|---|---|---|---|"]
    for e in out["C50"]:
        md.append(f"| {e['file']} | {e['pass']} | {e['outcome']}@{e['step']} | {e['seconds']} | "
                  f"{'yes' if e['same_as_candidate'] else 'no'} |")
    md += ["", "# R11-B2: MaskBench bigquery-table on the lines and 50cbd80e", "",
           "| library | budget | pass | end | seconds | the candidate's state |", "|---|---|---|---|---|---|"]
    for e in out["B"]:
        md.append(f"| {e['label']} | {e['budget']} | {e['pass']} | {e['outcome']}@{e['step']} | "
                  f"{e['seconds']} | {'yes' if e['candidate_state'] else 'no'} |")
    if bad:
        md += ["", "Problems:", ""] + [f"- {b}" for b in bad]
    with open(os.path.join(d, "r11bc2_summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
