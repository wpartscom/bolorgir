#!/usr/bin/env python3
"""Bring the release paths of the composition from the working tree MAIN
into the release worktree W (docs/release/v<version>-composition.md
"How the split is done").

  compose_release.py MAIN W [--composition PATH] [--dry-run]

For each path of `release_paths`: every file git would see under it in
MAIN (tracked, or untracked and not ignored) is copied into W with its
mode when W's copy differs; a file of W under that path that MAIN no
longer has is removed. Paths outside `release_paths` are not touched -
the core of the base revision with its patch, the published R6 sets, the
later-line paths. The `partial_paths` are transferred by hand under the
rule the composition writes for each; their differences between MAIN and
W are listed. Files of MAIN under a release path that git ignores are
listed, not copied. --dry-run prints the same lines and writes nothing.
The composition is MAIN's newest docs/release/v*-composition.json unless
given. Exit 0, 2 unusable arguments or a release path without files.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys


def git(tree, *args):
    return subprocess.run(["git", "-C", tree, *args], check=True, capture_output=True,
                          text=True).stdout


def files(tree, path):
    tracked = git(tree, "ls-files", "-z", "--", path).split("\0")
    others = git(tree, "ls-files", "-z", "--others", "--exclude-standard", "--", path).split("\0")
    return sorted({f for f in tracked + others if f and os.path.isfile(os.path.join(tree, f))})


def same(a, b):
    if not os.path.isfile(b) or os.stat(a).st_mode != os.stat(b).st_mode:
        return False
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def newest_composition(tree):
    found = glob.glob(os.path.join(tree, "docs/release/v*-composition.json"))
    key = lambda p: [int(x) for x in re.findall(r"\d+", os.path.basename(p))]  # noqa: E731
    return max(found, key=key) if found else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("main")
    ap.add_argument("w")
    ap.add_argument("--composition", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    main_tree, w = os.path.abspath(a.main), os.path.abspath(a.w)
    if main_tree == w:
        print("compose_release: MAIN and W are the same tree", file=sys.stderr)
        return 2
    comp_path = a.composition or newest_composition(main_tree)
    if not comp_path or not os.path.isfile(comp_path):
        print("compose_release: no composition", file=sys.stderr)
        return 2
    with open(comp_path, encoding="utf-8") as f:
        comp = json.load(f)
    tag = "would " if a.dry_run else ""
    copied = removed = unchanged = 0
    for p in comp["release_paths"]:
        src = files(main_tree, p)
        if not src:
            print(f"compose_release: release path {p}: no files in {main_tree}", file=sys.stderr)
            return 2
        for f in src:
            s, d = os.path.join(main_tree, f), os.path.join(w, f)
            if same(s, d):
                unchanged += 1
                continue
            print(f"{tag}copy {f}")
            copied += 1
            if not a.dry_run:
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
        for f in sorted(set(files(w, p)) - set(src)):
            print(f"{tag}remove {f}")
            removed += 1
            if not a.dry_run:
                os.remove(os.path.join(w, f))
        ignored = git(main_tree, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", p)
        for f in filter(None, ignored.split("\0")):
            print(f"ignored, not copied: {f}")
    for p, rule in (comp.get("partial_paths") or {}).items():
        s, d = os.path.join(main_tree, p), os.path.join(w, p)
        if os.path.isfile(s) and not same(s, d):
            text = rule if isinstance(rule, str) else json.dumps(rule, ensure_ascii=False)
            print(f"partial, by hand: {p} differs - rule: {text[:160]}")
    if a.dry_run:
        print(f"dry run: {copied} to copy, {removed} to remove, {unchanged} unchanged")
    else:
        print(f"done: {copied} copied, {removed} removed, {unchanged} unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
