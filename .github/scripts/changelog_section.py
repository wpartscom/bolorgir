#!/usr/bin/env python3
"""Print the CHANGELOG.md section of one version (the GitHub Release text).

Usage: changelog_section.py 0.2.0. A release-notes file
docs/release/v<version>.md, when present, comes first; the CHANGELOG
section follows. Exits 1 when the CHANGELOG has no `## [<version>]`
section."""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def section(text, version):
    head = re.compile(r"^## \[" + re.escape(version) + r"\][^\n]*\n", re.M)
    m = head.search(text)
    if not m:
        return None
    nxt = re.compile(r"^## \[", re.M).search(text, m.end())
    return text[m.end(): nxt.start() if nxt else len(text)].strip()


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: changelog_section.py <version>")
    version = sys.argv[1]
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        body = section(f.read(), version)
    if body is None:
        sys.exit(f"CHANGELOG.md has no section [{version}]")
    notes = os.path.join(ROOT, "docs", "release", f"v{version}.md")
    if os.path.exists(notes):
        with open(notes, encoding="utf-8") as f:
            print(f.read().strip())
        print()
        print("## Changes")
        print()
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
