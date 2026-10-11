#!/usr/bin/env python3
"""Identity of a T3 acceptance run directory (run_t3_accept.sh; R6
acceptance). Sources, library, harness, Python code, dependencies, corpora,
tokenizers and parameters are pinned in a separate directory; the identity
is checked on every resume. Standard library only: it runs before the run's
own venv exists, under `python3 -I`.

Every input is recorded by content, file by file:

- tree      the source copy (git archive of the candidate commit) without
            the corpora below; harness, Python code, pins and requirements
            are part of it;
- corpora   the two corpus snapshots copied into the tree
            (benchmarks/external/...), each pinned in acceptance_pins.json
            (file count and digest; the MaskBench checkout also by
            revision, clean);
- hf        the HF cache copy (the pinned tokenizer snapshots, symlinks
            recorded as links, blobs by content); the snapshot files of
            the pinned revisions are pinned by content;
- lib       the candidate library and the links of its directory (the
            package of the tree is built against it: python/bolorgir/_lib
            and _core are part of the tree);
- venv      every file of the run's venv except __pycache__ and its
            readiness marker; its `pip freeze` must equal the pinned
            requirements; the base interpreter (executable and standard
            library, by content) it runs on;
- params    the measurement parameters of the run.

  sources   check the source corpora and HF snapshots against the pins
            before anything is copied
  record    write identity/manifest.json and the per-input file lists
            identity/files/*.list of a prepared run directory (the copies
            must match the pins, the venv freeze the requirements)
  check     compute everything again and compare with the manifest; the
            differing fields, and up to ten paths of each, are printed

Exit status: 0 ok, 2 an input differs from its pin or from the manifest,
1 the tool itself failed. Machine data and the creation time are recorded
and not compared.
"""

import argparse
import datetime
import hashlib
import json
import os
import platform
import subprocess
import sys

NOT_COMPARED = ("created", "machine")
SKIP_NAMES = ("__pycache__",)
VENV_MARKER = ".complete"
LIB = os.path.join("lib", "libbolorgir.so.0.1.0")


class Mismatch(Exception):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def file_list(root, skip=(), skip_names=SKIP_NAMES):
    """{relative path: sha256 of the content, or "link:<target>" for a
    symlink} of every file under root, without the relative paths in
    `skip` and the directories named in skip_names. A symlinked directory
    is recorded as a link and not entered."""
    out = {}
    if not os.path.isdir(root):
        raise Mismatch(f"{root} is missing")
    for d, dirs, files in os.walk(root):
        rel_d = os.path.relpath(d, root)
        keep = []
        for name in sorted(dirs):
            rel = os.path.normpath(os.path.join(rel_d, name))
            if name in skip_names or rel in skip:
                continue
            p = os.path.join(d, name)
            if os.path.islink(p):
                out[rel] = "link:" + os.readlink(p)
            else:
                keep.append(name)
        dirs[:] = keep
        for name in files:
            rel = os.path.normpath(os.path.join(rel_d, name))
            if rel in skip:
                continue
            p = os.path.join(d, name)
            out[rel] = "link:" + os.readlink(p) if os.path.islink(p) else sha256_file(p)
    return dict(sorted(out.items()))


def digest(files):
    h = hashlib.sha256()
    for rel, val in sorted(files.items()):
        h.update(rel.encode("utf-8", "surrogateescape"))
        h.update(b"\0")
        h.update(val.encode("utf-8", "surrogateescape"))
        h.update(b"\n")
    return h.hexdigest()


def summary(files):
    return {"files": len(files), "digest": digest(files)}


def write_list(path, files):
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
        for rel, val in files.items():
            f.write(f"{val}  {rel}\n")


def read_list(path):
    out = {}
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        for line in f:
            val, rel = line.rstrip("\n").split("  ", 1)
            out[rel] = val
    return out


def list_diff(old, new, limit=10):
    lines = []
    for rel in sorted(set(old) | set(new)):
        if old.get(rel) == new.get(rel):
            continue
        what = "added" if rel not in old else "removed" if rel not in new else "changed"
        lines.append(f"{what} {rel}")
        if len(lines) == limit:
            lines.append("...")
            break
    return lines


def load_pins(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def requirements(path):
    """Pinned `name==version` lines (comments and blank lines dropped),
    names normalized as pip freeze prints them for comparison."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(norm_req(line))
    return sorted(out)


def norm_req(line):
    name, sep, version = line.partition("==")
    if not sep or not version:
        raise Mismatch(f"requirement {line!r} is not pinned as name==version")
    return name.strip().lower().replace("_", "-").replace(".", "-") + "==" + version.strip()


def git(*args):
    """Read-only git: no optional locks, so a status never rewrites the
    index of the checkout it reads."""
    return subprocess.run(["git", "--no-optional-locks", *args], capture_output=True, text=True,
                          check=True).stdout


# -- the inputs ----------------------------------------------------------------
def corpus_files(root, pins):
    return {key: file_list(os.path.join(root, c["path"])) for key, c in sorted(pins["corpora"].items())}


def hf_snapshot_files(hf_home, pins):
    """Content sha256 of the pinned snapshot files (links followed)."""
    out = {}
    for rel in sorted(pins["hf"]["files"]):
        p = os.path.join(hf_home, rel)
        out[rel] = sha256_file(p) if os.path.isfile(p) else "missing"
    return out


def check_pins(corpora, hf_files, pins, where):
    problems = []
    for key, files in corpora.items():
        want = pins["corpora"][key]
        got = summary(files)
        if got != {"files": want["files"], "digest": want["digest"]}:
            problems.append(f"{where}: corpus {key} {got} differs from the pin "
                            f"{ {'files': want['files'], 'digest': want['digest']} }")
    for rel, want in sorted(pins["hf"]["files"].items()):
        if hf_files.get(rel) != want:
            problems.append(f"{where}: HF file {rel} is {hf_files.get(rel)}, pinned {want}")
    if problems:
        raise Mismatch("\n".join(problems))


def python_record(py):
    """The interpreter a venv runs on: version, base executable and the
    standard library (without site-packages) by content."""
    code = ("import json, os, sys, sysconfig\n"
            "p = sysconfig.get_paths()\n"
            "exe = getattr(sys, '_base_executable', sys.executable)\n"
            "print(json.dumps({'version': sys.version, 'base_prefix': sys.base_prefix,\n"
            "  'executable': os.path.realpath(exe), 'stdlib': p['stdlib'],\n"
            "  'platstdlib': p['platstdlib']}))\n")
    info = json.loads(subprocess.run([py, "-I", "-c", code], capture_output=True, text=True,
                                     check=True).stdout)
    libs = {}
    for key in ("stdlib", "platstdlib"):
        root = info[key]
        if any(os.path.commonpath([root, r]) == r for r in libs):
            continue
        libs[root] = file_list(root, skip_names=SKIP_NAMES + ("site-packages", "dist-packages"))
    stdlib = {}
    for root, files in libs.items():
        stdlib.update({os.path.join(root, rel): v for rel, v in files.items()})
    return {"version": info["version"], "base_prefix": info["base_prefix"],
            "executable": info["executable"],
            "executable_sha256": sha256_file(info["executable"])}, dict(sorted(stdlib.items()))


def freeze(py):
    """Every distribution of the venv (`pip freeze --all`: the default
    output skips a version-dependent set of packaging tools)."""
    out = subprocess.run([py, "-I", "-m", "pip", "freeze", "--all", "--disable-pip-version-check"],
                         capture_output=True, text=True, check=True).stdout
    return sorted(norm_req(line) for line in out.splitlines() if line.strip())


def measure(run, pins, params, git_head, revision):
    """(manifest, {list name: files}) of a prepared run directory."""
    tree = os.path.join(run, "tree")
    corp = corpus_files(tree, pins)
    skip = {os.path.normpath(c["path"]) for c in pins["corpora"].values()}
    # The corpora sit under benchmarks/external, which git never holds.
    skip.add(os.path.normpath(os.path.join("benchmarks", "external")))
    tree_files = file_list(tree, skip=skip)
    hf_root = os.path.join(run, "hf-cache")
    hf_files = file_list(hf_root)
    venv = os.path.join(run, "venv")
    venv_files = file_list(venv, skip={VENV_MARKER})
    py = os.path.join(venv, "bin", "python")
    py_rec, stdlib = python_record(py)
    lists = {"tree": tree_files, "hf": hf_files, "venv": venv_files, "stdlib": stdlib}
    lists.update({f"corpus-{k}": v for k, v in corp.items()})
    m = {
        "format": 1,
        "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "machine": {"node": platform.node(), "platform": platform.platform(),
                    "libc": " ".join(platform.libc_ver())},
        "git_head": git_head,
        "params": params,
        "tree": summary(tree_files),
        "lib": {"sha256": sha256_file(os.path.join(run, LIB)),
                "dir": summary(file_list(os.path.join(run, "lib")))},
        "corpora": {k: dict(summary(v), **({"revision": revision} if k == "maskbench" else {}))
                    for k, v in corp.items()},
        "hf": dict(summary(hf_files), snapshots=hf_snapshot_files(hf_root, pins)),
        "venv": dict(summary(venv_files), freeze=freeze(py), python=py_rec,
                     stdlib=summary(stdlib)),
    }
    return m, lists


def compare(old, new, path=""):
    out = []
    keys = set(old) | set(new)
    for k in sorted(keys):
        if not path and k in NOT_COMPARED:
            continue
        a, b = old.get(k), new.get(k)
        if isinstance(a, dict) and isinstance(b, dict):
            out += compare(a, b, f"{path}{k}.")
        elif a != b:
            out.append((f"{path}{k}", a, b))
    return out


# -- commands ------------------------------------------------------------------
def cmd_sources(args):
    pins = load_pins(args.pins)
    corp = {key: file_list(os.path.join(args.root, c["path"])) for key, c in pins["corpora"].items()}
    hf_files = hf_snapshot_files(args.hf, pins)
    check_pins(corp, hf_files, pins, "source")
    mb = pins["corpora"]["maskbench"]
    repo = os.path.join(args.root, mb["repository"])
    head = git("-C", repo, "rev-parse", "HEAD").strip()
    if head != mb["revision"]:
        raise Mismatch(f"source: {repo} is at {head}, pinned {mb['revision']}")
    dirty = git("-C", repo, "status", "--porcelain", "--", os.path.relpath(
        os.path.join(args.root, mb["path"]), repo))
    if dirty.strip():
        raise Mismatch(f"source: {mb['path']} has local changes:\n{dirty.strip()}")
    counts = ", ".join(f"{k} {len(v)}" for k, v in sorted(corp.items()))
    print(f"T3_SOURCES_OK corpora {counts}; HF files {len(hf_files)}")


def cmd_record(args):
    run = args.run
    tree = os.path.join(run, "tree")
    pins = load_pins(os.path.join(tree, args.pins))
    m, lists = measure(run, pins, json.loads(args.params), args.git_head, args.revision)
    check_pins({k[len("corpus-"):]: v for k, v in lists.items() if k.startswith("corpus-")},
               m["hf"]["snapshots"], pins, "copy")
    want = requirements(os.path.join(tree, pins["requirements"]))
    got = [x for x in m["venv"]["freeze"] if not x.startswith("pip==")]
    pip = [x for x in m["venv"]["freeze"] if x.startswith("pip==")]
    if got != want:
        raise Mismatch("venv: pip freeze --all differs from the pinned requirements:\n"
                       + "\n".join(f"  {x}" for x in sorted(set(want) ^ set(got))))
    if pins.get("pip") and pip != [f"pip=={pins['pip']}"]:
        raise Mismatch(f"venv: {pip} installed, pip=={pins['pip']} pinned")
    ident = os.path.join(run, "identity")
    os.makedirs(os.path.join(ident, "files"), exist_ok=True)
    for name, files in lists.items():
        write_list(os.path.join(ident, "files", f"{name}.list"), files)
    tmp = os.path.join(ident, "manifest.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(ident, "manifest.json"))
    print(f"T3_IDENTITY_RECORDED tree {m['tree']['files']} files, venv {m['venv']['files']}, "
          f"lib {m['lib']['sha256'][:16]}")


def cmd_check(args):
    run = args.run
    ident = os.path.join(run, "identity")
    with open(os.path.join(ident, "manifest.json"), encoding="utf-8") as f:
        old = json.load(f)
    pins = load_pins(os.path.join(run, "tree", args.pins))
    new, lists = measure(run, pins, json.loads(args.params), old.get("git_head"),
                         (old.get("corpora", {}).get("maskbench") or {}).get("revision"))
    diffs = compare(old, new)
    if not diffs:
        print("T3_IDENTITY_OK")
        return
    lines = []
    for field, a, b in diffs:
        lines.append(f"{field}: recorded {json.dumps(a)[:200]}, now {json.dumps(b)[:200]}")
        name = {"tree.digest": "tree", "hf.digest": "hf", "venv.digest": "venv",
                "venv.stdlib.digest": "stdlib"}.get(field)
        if field.startswith("corpora.") and field.endswith(".digest"):
            name = "corpus-" + field.split(".")[1]
        if name:
            stored = os.path.join(ident, "files", f"{name}.list")
            if os.path.exists(stored):
                lines += [f"  {x}" for x in list_diff(read_list(stored), lists[name])]
    raise Mismatch("inputs differ from identity/manifest.json:\n" + "\n".join(lines))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sources")
    s.add_argument("--pins", required=True)
    s.add_argument("--root", required=True, help="source tree the pinned paths are relative to")
    s.add_argument("--hf", required=True, help="source HF_HOME")
    for name in ("record", "check"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True)
        p.add_argument("--pins", default=os.path.join("benchmarks", "t3", "acceptance_pins.json"),
                       help="pins file, relative to the run's tree")
        p.add_argument("--params", required=True, help="JSON object of the run parameters")
        if name == "record":
            p.add_argument("--git-head", required=True)
            p.add_argument("--revision", required=True, help="revision of the MaskBench checkout")
    args = ap.parse_args(argv)
    try:
        {"sources": cmd_sources, "record": cmd_record, "check": cmd_check}[args.cmd](args)
    except Mismatch as e:
        print(f"T3_IDENTITY_MISMATCH {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
