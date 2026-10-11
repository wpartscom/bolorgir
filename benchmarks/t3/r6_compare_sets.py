#!/usr/bin/env python3
"""r6_compare_sets.py A B --a-root RA --b-root RB: two published R6 sets
(r6_publish.sh output: reports, identity, review/ with the acceptance
verdict, the T2c triage and the evidence) must say the same thing.

Every file of A and of B is compared (the SHA256SUMS lists excepted: they
hash files that hold paths; r6_publish_layout.sh checks each list against
its own directory). A .gz file is compared decompressed; a JSON file is
parsed and compared as data, so a difference is named by its key path
(transitions[3].file, acceptance.failing, inputs.d4.sha256, ...); any
other file is compared as text. The only normalization: in A every
occurrence of the root RA, and in B of RB, becomes "<root>" (the raw
string and its JSON \\u escape form) - the two sets are published from
two trees, so their absolute paths differ by the tree root and in
nothing else. A path of the other tree, a reordered list, a changed
count, outcome, D4 binding, T2c class or input hash is a difference.

The summary names the parts the review rests on - in acceptance.json the
verdict and check statuses, the transitions, the D4 rows, the T2c rows,
the totals and the input hashes; in triage.json the T2c results - each
equal or not. Exit 0 equal, 1 different, 2 unusable arguments.
"""
import argparse
import gzip
import json
import os
import sys

TOKEN = "<root>"
SUMMARY = {
    "review/acceptance.json": ("acceptance", "checks", "transitions", "d4_pending_rows",
                               "triage_rows", "totals", "gains", "inputs", "oracle_counts",
                               "oracle_pass_lost", "d4_entries_without_transition",
                               "protocol_differences"),
    "review/triage.json": ("summary", "exit_status", "transition_rows", "generatable_rows",
                           "instance_rows", "counter_rows", "gate_build", "final_build",
                           "inputs"),
}


def forms(root):
    """The spellings of a root in a file: raw, and JSON-escaped (ensure_ascii)."""
    root = root.rstrip("/")
    return sorted({root, json.dumps(root)[1:-1]}, key=len, reverse=True)


def norm_text(s, roots):
    for r in roots:
        s = s.replace(r, TOKEN)
    return s


def norm(v, roots):
    if isinstance(v, str):
        return norm_text(v, roots)
    if isinstance(v, list):
        return [norm(x, roots) for x in v]
    if isinstance(v, dict):
        return {norm_text(k, roots): norm(x, roots) for k, x in v.items()}
    return v


def files(top):
    out = set()
    for d, _dirs, names in os.walk(top):
        for n in names:
            if n != "SHA256SUMS":
                out.add(os.path.relpath(os.path.join(d, n), top))
    return out


def load(path, roots):
    with open(path, "rb") as f:
        data = f.read()
    name = path[:-3] if path.endswith(".gz") else path
    if path.endswith(".gz"):
        data = gzip.decompress(data)
    text = data.decode("utf-8", errors="surrogateescape")
    if name.endswith(".json"):
        try:
            return "json", norm(json.loads(text), roots)
        except ValueError:
            pass
    return "text", norm_text(text, roots)


def json_diffs(a, b, path, out, limit=30):
    if len(out) >= limit:
        return
    if type(a) is not type(b):
        out.append(f"{path or '.'}: {short(a)} | {short(b)}")
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: {'missing in A' if k not in a else 'missing in B'}")
            else:
                json_diffs(a[k], b[k], f"{path}.{k}" if path else k, out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} items | {len(b)} items")
        for i, (x, y) in enumerate(zip(a, b)):
            json_diffs(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path or '.'}: {short(a)} | {short(b)}")


def short(v):
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 100 else s[:97] + "..."


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--a-root", required=True, help="tree root of A, replaced in A only")
    ap.add_argument("--b-root", required=True, help="tree root of B, replaced in B only")
    args = ap.parse_args(argv)
    for d in (args.a, args.b):
        if not os.path.isdir(os.path.join(d, "review")):
            print(f"r6_compare_sets: {d} is not a published set (no review/)", file=sys.stderr)
            return 2
    ra, rb = forms(args.a_root), forms(args.b_root)
    fa, fb = files(args.a), files(args.b)
    bad = []
    for f in sorted(fa - fb):
        bad.append(f"{f}: only in A")
    for f in sorted(fb - fa):
        bad.append(f"{f}: only in B")
    parsed = {}
    for f in sorted(fa & fb):
        ka, va = load(os.path.join(args.a, f), ra)
        kb, vb = load(os.path.join(args.b, f), rb)
        parsed[f] = (va, vb)
        if (ka, va) == (kb, vb):
            continue
        if ka == kb == "json":
            d = []
            json_diffs(va, vb, "", d)
            bad += [f"{f}: {x}" for x in d]
        else:
            la, lb = va.splitlines(), vb.splitlines()
            i = next((i for i, (x, y) in enumerate(zip(la, lb)) if x != y), min(len(la), len(lb)))
            bad.append(f"{f}: line {i + 1}: {(la[i:i + 1] or ['<end>'])[0][:100]!r} | "
                       f"{(lb[i:i + 1] or ['<end>'])[0][:100]!r}")
    for f, keys in SUMMARY.items():
        if f not in parsed:  # one side only: reported above
            if f not in fa and f not in fb:
                bad.append(f"{f}: in neither set")
            continue
        va, vb = parsed[f]
        parts = []
        for k in keys:
            if k in va or k in vb:
                x, y = va.get(k), vb.get(k)
                n = f" ({len(x)})" if isinstance(x, (list, dict)) else ""
                parts.append(f"{k}{n} {'equal' if x == y else 'DIFFERENT'}")
        print(f"{f}: " + ", ".join(parts))
    print(f"compared {len(fa & fb)} files (SHA256SUMS lists excepted)")
    for b in bad:
        print(f"r6_compare_sets: {b}", file=sys.stderr)
    print("r6_compare_sets: " + ("same content" if not bad else f"{len(bad)} differences"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
