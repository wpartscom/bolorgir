#!/usr/bin/env python3
"""Release version check: python/pyproject.toml, bolorgir.__version__ and the
tag must agree. Prints `version=<v>` and `prerelease=true|false` lines for
$GITHUB_OUTPUT; exits 1 on a mismatch."""

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRE = re.compile(r"(a|b|rc)\d+$")


def read(rel, pattern):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        m = re.search(pattern, f.read(), re.M)
    if not m:
        sys.exit(f"no version in {rel}")
    return m.group(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="")
    args = ap.parse_args()
    project = read("python/pyproject.toml", r'^version\s*=\s*"([^"]+)"')
    package = read("python/bolorgir/__init__.py", r'^__version__\s*=\s*"([^"]+)"')
    if project != package:
        sys.exit(f"pyproject.toml {project} != bolorgir.__version__ {package}")
    if args.ref.startswith("refs/tags/"):
        tag = args.ref[len("refs/tags/"):]
        if tag != f"v{project}":
            sys.exit(f"tag {tag} != v{project}")
    print(f"version={project}")
    print(f"prerelease={'true' if PRE.search(project) else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
