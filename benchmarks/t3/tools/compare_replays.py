#!/usr/bin/env python3
"""Compare two sets of replay jsonl files: per item outcome and counts.

Usage: compare_replays.py <old dir or file> <new dir or file> [glob]
Directories are walked for *.jsonl; rows are keyed by dataset/file.
"""
import glob
import json
import os
import sys


def load(target, pattern):
    rows = {}
    paths = []
    if os.path.isdir(target):
        paths = sorted(glob.glob(os.path.join(target, pattern)))
    else:
        paths = [target]
    for p in paths:
        for line in open(p):
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            row = json.loads(line)
            if "dataset" not in row:
                continue
            rows[(row["dataset"], row["file"])] = row
    return rows


old = load(sys.argv[1], sys.argv[3] if len(sys.argv) > 3 else "*.jsonl")
new = load(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "*.jsonl")
print(f"old items {len(old)}, new items {len(new)}")
changed = 0
for key in sorted(set(old) | set(new)):
    o, n = old.get(key), new.get(key)
    if o is None or n is None:
        print("only", "new" if o is None else "old", key, o or n)
        changed += 1
        continue
    if o.get("outcome") != n.get("outcome") or o.get("counts") != n.get("counts"):
        changed += 1
        print(f"CHANGED {key[0]}/{key[1]}")
        print(f"  old: {o.get('outcome')} {o.get('counts')}")
        print(f"  new: {n.get('outcome')} {n.get('counts')}")
print("changed items:", changed)
