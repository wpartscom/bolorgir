#!/usr/bin/env python3
"""Enter the oracle reports of a reference line into
benchmarks/t3/oracle_lines.json of one or more trees (the R6 review
compares the candidate's oracle PASS with every line that has data).

  r6_register_line_oracle.py --line NAME --library-sha256 SHA \\
      --source TREE --prefix PREFIX --identity TEXT \\
      [--replace DIALECT=SHA256 ... --reason TEXT] TREE...

The five reports are SOURCE/tests/oracle/results/PREFIX-<dialect>-spec.json
(benchmarks/t3/r6_oracle.sh writes them, e.g. PREFIX
oracle-line-dbc5eeb-8ff970d4). Each must be a tests/oracle/run_oracle.py
report of profile spec-v1 and of its one dialect whose header names
SHA as library_sha256, and evidence about that library
(oracle_content_problems of this tree's tools/acceptance_compare.py: rows,
compiled schemas, engine verdicts, no compile refusal of the call itself,
a summary that counts the rows). A report missing in a TREE is copied
there (same bytes). owner_decision stays as it is - it is the owner's
field.

A first registration needs the line listed once in every TREE, with that
library and no reports yet; its missing_reason gives way to the identity
TEXT (how the reports were made).

--replace replaces reports already registered, for all five dialects:
DIALECT=SHA256 is the expected sha256 of the registered report of that
dialect, and in every TREE the line must list exactly these reports, each
file present with those bytes. The new reports must have other paths: the
old files stay. The old identity and reports move into the line's
`history` list with the time and --reason; the line then names the new
identity and reports.

All checks run before the first write, over every TREE: the sources,
each oracle_lines.json and each destination report (absent, or the same
bytes). Only then are the missing reports copied and the JSON files
replaced, each through a temporary file, fsync and os.replace. Exit: 0
registered in every TREE, 2 refused (nothing written).
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
import shutil
import sys

DIALECTS = ("draft4", "draft6", "draft7", "draft2019-09", "draft2020-12")
ORACLE_LINES = "benchmarks/t3/oracle_lines.json"
COMPARE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "acceptance_compare.py")


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def die(msg):
    print(f"r6_register_line_oracle: refused, nothing written: {msg}", file=sys.stderr)
    sys.exit(2)


def replace_file(dst, data):
    tmp = f"{dst}.tmp-{os.getpid()}"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(dst):
        shutil.copymode(dst, tmp)
    os.replace(tmp, dst)


def is_sha256(text):
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--line", required=True)
    ap.add_argument("--library-sha256", required=True)
    ap.add_argument("--source", required=True, help="tree holding the reports")
    ap.add_argument("--prefix", required=True, help="report name before -<dialect>-spec.json")
    ap.add_argument("--identity", required=True, help="how the reports were made")
    ap.add_argument("--replace", action="append", default=[], metavar="DIALECT=SHA256",
                    help="expected sha256 of a registered report to replace (all five)")
    ap.add_argument("--reason", default="", help="why the registered reports are replaced")
    ap.add_argument("trees", nargs="+")
    a = ap.parse_args(argv)
    if not is_sha256(a.library_sha256):
        die(f"--library-sha256 {a.library_sha256!r} is not a sha256")
    if not a.identity.strip():
        die("empty --identity")
    expected = {}
    for spec in a.replace:
        d, _, h = spec.partition("=")
        if d not in DIALECTS or not is_sha256(h) or d in expected:
            die(f"--replace {spec!r}: expected DIALECT=SHA256, each dialect once")
        expected[d] = h
    if expected and sorted(expected) != sorted(DIALECTS):
        die(f"--replace names {', '.join(sorted(expected))}; all five dialects are replaced together")
    if expected and not a.reason.strip():
        die("--replace needs --reason")
    spec = importlib.util.spec_from_file_location("acceptance_compare", COMPARE)
    compare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compare)
    # 1. the sources
    reports, data = {}, {}
    for d in DIALECTS:
        rel = f"tests/oracle/results/{a.prefix}-{d}-spec.json"
        src = os.path.join(a.source, rel)
        if not os.path.isfile(src):
            die(f"{src} missing")
        with open(src, "rb") as f:
            data[rel] = f.read()
        try:
            doc = json.loads(data[rel])
            h = doc["header"]
        except (ValueError, KeyError, TypeError) as e:
            die(f"{src}: not a run_oracle.py report ({e})")
        if (h.get("library_sha256") != a.library_sha256 or h.get("profile") != "spec-v1"
                or h.get("drafts") != [d]):
            die(f"{rel}: header {h.get('library_sha256')} {h.get('profile')} {h.get('drafts')}")
        problems = compare.oracle_content_problems(doc)
        if problems:
            die(f"{rel} is no evidence about the library: {'; '.join(problems)}")
        reports[d] = {"path": rel, "sha256": hashlib.sha256(data[rel]).hexdigest()}
    # 2. every tree, every destination - before any write
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    plans = []
    for tree in a.trees:
        p = os.path.join(tree, ORACLE_LINES)
        if not os.path.isfile(p):
            die(f"{p} missing")
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        found = [i for i, line in enumerate(doc.get("lines", [])) if line.get("name") == a.line]
        if len(found) != 1:
            die(f"{p}: {a.line} listed {len(found)} times")
        entry = doc["lines"][found[0]]
        if entry.get("library_sha256") != a.library_sha256:
            die(f"{p}: {a.line} names {entry.get('library_sha256')}")
        old = entry.get("reports") or {}
        if not expected and old:
            die(f"{p}: {a.line} already lists reports (--replace replaces them)")
        if expected:
            if sorted(old) != sorted(DIALECTS):
                die(f"{p}: {a.line} lists {', '.join(sorted(old)) or 'no reports'}, not the five "
                    "to replace")
            for d, r in sorted(old.items()):
                if r.get("sha256") != expected[d]:
                    die(f"{p}: {a.line} {d} is {r.get('sha256')}, not the expected {expected[d]}")
                f_old = os.path.join(tree, r["path"])
                if not os.path.isfile(f_old) or sha(f_old) != expected[d]:
                    die(f"{f_old}: not the registered bytes {expected[d]}")
                if r["path"] == reports[d]["path"]:
                    die(f"{r['path']}: the new report has the old one's path")
        copies = []
        for r in reports.values():
            dst = os.path.join(tree, r["path"])
            if os.path.lexists(dst):
                if not os.path.isfile(dst) or sha(dst) != r["sha256"]:
                    die(f"{dst} exists with other bytes")
            else:
                copies.append((dst, r["path"]))
        new = {}
        for k, v in entry.items():  # key order kept; missing_reason -> identity
            if k == "reports":
                new["identity"] = a.identity
                new["reports"] = reports
            elif k not in ("missing_reason", "identity", "history"):
                new[k] = v
        if expected:
            new["history"] = list(entry.get("history") or []) + [
                {"replaced_utc": now, "reason": a.reason, "identity": entry.get("identity"),
                 "reports": old}]
        doc["lines"][found[0]] = new
        text = (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
        plans.append((p, text, copies))
    # 3. the writes: report copies first, then each JSON
    for _p, _text, copies in plans:
        for dst, rel in copies:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            replace_file(dst, data[rel])
            print(f"copied {dst}")
    for p, text, _copies in plans:
        replace_file(p, text)
        print(f"{'replaced' if expected else 'registered'} {a.line}: {len(reports)} reports in {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
