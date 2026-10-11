#!/usr/bin/env python3
"""Release line checks, read from
docs/release/v<version>-composition.json.

  abi      every blg_* function python/bolorgir/_core.c calls is exported
           by the built core (--lib) and, with --base-only, belongs to the
           base ABI of the composition: a package of the base core needs
           no later-line symbol
  sources  the core and the bindings of the checked-out tree equal the
           composition manifest: every file of core.manifest and
           bindings.manifest is present with the pinned git blob id, no
           extra file sits under the core paths of core.paths, and no
           path of later_line_absent is present. Git history is not read.
  gate     the final-tag gate: the composition names the committed R6
           review verdict (admitted) and the T8a report (accepted), both
           bound to one core, and that core is the one this run built
           (--core-sha). An rc or manual build needs no gate; it is
           reported as preliminary.
  preview  the GitHub pre-release of an rc tag: the composition's
           preview block names the committed R6 review verdict, which
           must be admitted and bound to the core this run built
           (--core-sha). No T8a report is asked for: a preview ships
           the R6-admitted core before the final gate and says so in
           its notes.

Exit status 0 when the check holds, 1 with the reasons on stderr.
"""

import argparse
import json
import os
import re
import subprocess
import sys

CALL = re.compile(r"\bblg_[a-z0-9_]+(?=\s*\()")


def functions_called(text):
    return sorted(set(CALL.findall(text)))


def exported(lib):
    out = subprocess.run(["nm", "-D", "--defined-only", lib], capture_output=True, text=True,
                         check=True).stdout
    return {line.split()[-1] for line in out.splitlines()
            if len(line.split()) >= 3 and line.split()[1] in ("T", "t", "W")
            and line.split()[-1].startswith("blg_")}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_abi(comp, source_text, exports=None, base_only=False):
    reasons = []
    called = functions_called(source_text)
    if exports is not None:
        missing = [f for f in called if f not in exports]
        if missing:
            reasons.append(f"the core does not export {', '.join(missing)}")
    if base_only:
        base = set(comp["abi"]["base_functions"])
        later = [f for f in called if f not in base]
        if later:
            reasons.append(f"the bindings call functions outside the base ABI: {', '.join(later)}")
    return reasons


def check_sources(comp, tree):
    """The checked-out tree equals the composition manifest: every file of
    core.manifest and bindings.manifest is present with the pinned git blob
    id, no extra file sits under the core paths, and no path of
    later_line_absent is present. Git history is not read."""
    reasons = []
    if subprocess.run(["git", "-C", tree, "rev-parse", "--git-dir"],
                      capture_output=True).returncode != 0:
        return ["not a git tree"]
    manifest = dict(comp["core"]["manifest"])
    manifest.update(comp["bindings"]["manifest"])
    roots = comp["core"]["paths"]

    def under_roots(p):
        return any(p == r or p.startswith(r + "/") for r in roots)

    listed = subprocess.run(
        ["git", "-C", tree, "ls-files", "--cached", "--others", "--exclude-standard",
         "--", *roots],
        capture_output=True, text=True)
    if listed.returncode != 0:
        return [f"git ls-files failed ({listed.returncode})"]
    expected = {p for p in manifest if under_roots(p)}
    extra = sorted(set(listed.stdout.split()) - expected)
    if extra:
        reasons.append(f"extra files under the core paths: {', '.join(extra[:10])}")
    for p, blob in sorted(manifest.items()):
        full = os.path.join(tree, p)
        if not os.path.exists(full):
            reasons.append(f"missing file: {p}")
            continue
        got = subprocess.run(["git", "-C", tree, "hash-object", "--", p],
                             capture_output=True, text=True).stdout.strip()
        if got != blob:
            reasons.append(f"{p}: blob {got[:12] or '?'} differs from the pinned {blob[:12]}")
    for p in comp.get("later_line_absent", []):
        hit = subprocess.run(
            ["git", "-C", tree, "ls-files", "--cached", "--others", "--exclude-standard",
             "--", p],
            capture_output=True, text=True).stdout.split()
        if hit:
            reasons.append(f"later-line path present: {p}")
    return reasons


def r6_reasons(acc, core_sha):
    """An R6 review verdict that is not admitted or not bound to core_sha."""
    reasons = []
    verdict = (acc.get("acceptance") or {}).get("verdict")
    if verdict != "admitted":
        reasons.append(f"R6 verdict is {verdict!r}")
    cand = ((acc.get("inputs") or {}).get("candidate") or {}).get("library_sha256")
    if cand != core_sha:
        reasons.append(f"R6 candidate {str(cand)[:16]} is not the built core {core_sha[:16]}")
    return reasons


def check_preview(comp, root, core_sha):
    p = (comp.get("preview") or {}).get("r6_acceptance")
    if not p:
        return ["preview.r6_acceptance is not set in the composition"]
    full = os.path.join(root, p)
    if not os.path.exists(full):
        return [f"preview.r6_acceptance: {p} is not in the tree"]
    return r6_reasons(load(full), core_sha)


def check_gate(comp, root, core_sha):
    gate = comp.get("gate") or {}
    reasons = []
    paths = {k: gate.get(k) for k in ("r6_acceptance", "t8a_report")}
    docs = {}
    for k, p in paths.items():
        if not p:
            reasons.append(f"gate.{k} is not set in the composition")
            continue
        full = os.path.join(root, p)
        if not os.path.exists(full):
            reasons.append(f"gate.{k}: {p} is not in the tree")
            continue
        docs[k] = load(full)
    acc, t8a = docs.get("r6_acceptance"), docs.get("t8a_report")
    if acc is not None:
        reasons += r6_reasons(acc, core_sha)
    if t8a is not None:
        if not (t8a.get("t8a") or {}).get("accepted"):
            reasons.append("the T8a report is not accepted")
        if t8a.get("core_sha256") != core_sha:
            reasons.append(f"T8a core {str(t8a.get('core_sha256'))[:16]} is not the built core "
                           f"{core_sha[:16]}")
    return reasons


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("check", choices=("abi", "sources", "gate", "preview"))
    ap.add_argument("--composition", required=True)
    ap.add_argument("--tree", default=".")
    ap.add_argument("--lib", default="")
    ap.add_argument("--base-only", action="store_true")
    ap.add_argument("--core-sha", default="")
    args = ap.parse_args(argv)
    comp = load(args.composition)
    if args.check == "abi":
        with open(os.path.join(args.tree, "python", "bolorgir", "_core.c"), encoding="utf-8") as f:
            text = f.read()
        reasons = check_abi(comp, text, exported(args.lib) if args.lib else None, args.base_only)
    elif args.check == "sources":
        reasons = check_sources(comp, args.tree)
    elif not re.fullmatch(r"[0-9a-f]{64}", args.core_sha or ""):
        reasons = ["--core-sha must be the sha256 of the built core"]
    elif args.check == "gate":
        reasons = check_gate(comp, args.tree, args.core_sha)
    else:
        reasons = check_preview(comp, args.tree, args.core_sha)
    for r in reasons:
        print(f"release_gate {args.check}: {r}", file=sys.stderr)
    print(f"release_gate {args.check}: {'ok' if not reasons else 'FAILED'}")
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
