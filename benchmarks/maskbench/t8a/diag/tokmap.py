"""Map the journal's per-token mask times to the instance text.
usage: tokmap.py CORPUS_FILE TOKDIR JOURNAL"""
import json, sys
from transformers import AutoTokenizer
from bolorgir.serializer import serialize_value
corpus, tokdir, journal = sys.argv[1:4]
d = json.load(open(corpus))
tok = AutoTokenizer.from_pretrained(tokdir)
ev = [json.loads(l) for l in open(journal)]
enc = {e["i"]: e for e in ev if e["e"] == "encode"}
times = {}
for e in ev:
    if e["e"] == "tok":
        times[(e["i"], e["k"])] = e["mask_us"] / 1e3
inst = sorted(enc)
for i in inst:
    payload, st = serialize_value(d["tests"][i]["data"], d["schema"])
    text = payload.decode()
    ids = tok.encode(text, add_special_tokens=False)
    print(f"== instance {i} valid={d['tests'][i].get('valid')} n={len(ids)} (journal n={enc[i]['n']}), bytes={len(text.encode())} (journal {enc[i]['bytes']})")
    pos = 0
    line = []
    for k, t in enumerate(ids):
        piece = tok.decode([t])
        ms = times.get((i, k))
        tag = "  -" if ms is None else (f"{ms:7.1f}")
        line.append(f"{k:4d} {tag} {piece!r}")
    print("\n".join(line))
