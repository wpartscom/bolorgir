#!/usr/bin/env python3
"""Record which library a diagnostic package actually resolves.

A package points at a library through a symlink; the target can be replaced
between the start of a leg and its end. The tool snapshots the resolved file
(path, sha256, size, inode, mtime) before and after a leg and refuses to
annotate the result when the two snapshots disagree.

usage:
  ve_lib_record.py record PKG --phase before|after --log RECORDS.jsonl
  ve_lib_record.py annotate PKG --log RECORDS.jsonl --result RESULT.json
"""
import argparse
import hashlib
import json
import os
import time

REL = os.path.join("bolorgir", "_lib", "libbolorgir.so.0.1.0")


def snapshot(pkg):
    link = os.path.abspath(os.path.join(pkg, REL))
    real = os.path.realpath(link)
    st = os.stat(real)
    digest = hashlib.sha256()
    with open(real, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return {"pkg": os.path.abspath(pkg), "link": link, "realpath": real,
            "sha256": digest.hexdigest(), "size": st.st_size,
            "inode": st.st_ino, "mtime_ns": st.st_mtime_ns}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("pkg")
    rec.add_argument("--phase", required=True)
    rec.add_argument("--log", required=True)
    ann = sub.add_parser("annotate")
    ann.add_argument("pkg")
    ann.add_argument("--log", required=True)
    ann.add_argument("--result", required=True)
    args = ap.parse_args()

    if args.cmd == "record":
        snap = snapshot(args.pkg)
        snap["phase"] = args.phase
        snap["utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(args.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(snap, sort_keys=True) + "\n")
        print(json.dumps(snap, sort_keys=True))
        return 0

    with open(args.log, encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]
    if not entries:
        print(f"no records in {args.log}")
        return 1
    hashes = {e["sha256"] for e in entries}
    if len(hashes) != 1:
        print(f"library changed between the records: {sorted(hashes)}")
        return 1
    with open(args.result, encoding="utf-8") as f:
        result = json.load(f)
    result["library"] = {
        "realpath": entries[0]["realpath"],
        "sha256": entries[0]["sha256"],
        "records": len(entries),
        "phases": [e["phase"] for e in entries],
        "first_utc": entries[0]["utc"],
        "last_utc": entries[-1]["utc"],
    }
    with open(args.result, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    print(f"annotated {args.result} with {entries[0]['sha256']} "
          f"({len(entries)} record(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
