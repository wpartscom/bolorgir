#!/usr/bin/env python3
"""make_items.py FACTS OUT: the R11-A2 items - one per incomplete
transition result of the candidate 9a5baf3e - from the recorded facts
(facts-9a5baf3e.json, facts.py). Item fields: snapshot, dataset, file,
tokenizer, d4 (the entry the review matched), against (the lines),
c_star (the step of the budget check at which the candidate's pass 1
recorded its time limit; the check the transition hinges on - the
elapsed time at it is what R11-A2 measures). Writes the list to OUT;
exit 1 unless every incomplete result is present (25) and every pass 1
recorded a time limit at a budget check."""
import json
import sys

CAND = "t3-9a5baf3e"


def c_star(p1):
    """The step of pass 1's recorded time limit ('tl@16' -> 16)."""
    outcome, _, rest = p1.partition("@")
    if outcome != "tl":
        raise SystemExit(f"pass 1 is not a time limit: {p1}")
    return int(rest.split("/")[0])


def main(facts_path, out_path):
    facts = json.load(open(facts_path, encoding="utf-8"))
    if CAND not in facts["builds"]:
        raise SystemExit(f"{CAND} is not a build of {facts['set']}")
    items = []
    for it in facts["items"]:
        if it["outcome"] != "incomplete":
            continue
        items.append({"snapshot": it["corpus"], "dataset": it["dataset"], "file": it["file"],
                      "tokenizer": it["tokenizer"], "d4": it["d4"],
                      "against": sorted(it["against"]),
                      "c_star": c_star(it["passes"][CAND][0])})
    items.sort(key=lambda x: (x["snapshot"], x["file"], x["tokenizer"]))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"{len(items)} incomplete results -> {out_path}")
    c = {"16": 0, "32": 0, "48": 0}
    for it in items:
        c[str(it["c_star"])] += 1
    print("c* distribution:", c)
    return 0 if len(items) == 25 else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
