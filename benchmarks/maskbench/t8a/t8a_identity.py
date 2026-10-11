#!/usr/bin/env python3
"""Identity manifest of a T8a run.

Records every input that decides the measured result, by content:

- the Bolorgir wheel and the core library inside it, and in every venv
  the digest of each installed file of the `bolorgir` package (a file
  replaced under the same version is caught, which `pip freeze` is not),
  the serializer module, `pip freeze` and the Python version;
- the T8a tools and the MaskBench code (every *.py file of the checkout,
  tracked or not) and the MaskBench head;
- the corpus: sha256 of every data/*.json file, or of the smoke subset;
- the tokenizer: the pinned revision and the sha256 of every file of its
  snapshot directory (symlinks resolved);
- the run parameters (threads, time and memory limits, profile, the
  serializer, compact mode, smoke size) and, when given, the T3
  acceptance verdict the core is bound to;
- the five participants of a T8a run: run name, venv,
  module, the engine id MaskBench writes into meta.txt and the module
  version installed in that venv. The pinned engines must have the
  MaskBench pins (PINNED); t8a_report.py compares every --run and its
  meta.txt with this list.

  record  write the manifest (--out)
  check   compute it again and compare with --manifest; exit 1 with the
          differing fields when an input changed (a resume needs a new
          work directory then)

Machine data are recorded and not compared.
"""

import argparse
import glob
import hashlib
import json
import os
import platform
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = ("paired_runner.py", "paired_driver.py", "t8a_queue.py", "t8a_report.py",
         "t8a_streams.py", "t8a_identity.py", "t8a_preflight.py", "run_t8a.sh")
NOT_COMPARED = ("machine", "created")
# (run name, venv, module, engine id in meta.txt): the five variants of
# run_t8a.sh. The Bolorgir id is the spec-v1 profile with the serializer
# (maskbench/blg_engine.py get_id) plus the -t8a suffix of paired_runner.
PARTICIPANTS = (
    ("blg-spec-v1", "venv-pinned", "bolorgir", "blg-spec-v1-ser-t8a"),
    ("llg-pinned", "venv-pinned", "llguidance", "llg-t8a"),
    ("xgr-pinned", "venv-pinned", "xgrammar", "xgr-t8a"),
    ("llg-current", "venv-current", "llguidance", "llg-t8a"),
    ("xgr-current", "venv-current", "xgrammar", "xgr-t8a"),
)
# The MaskBench pins (run_t8a.sh installs the same versions into venv-pinned).
PINNED = {"llguidance": "0.7.10", "xgrammar": "0.1.17"}
MODULES = ("bolorgir", "llguidance", "xgrammar")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tree_files(root, patterns):
    """{relative path: sha256} of the files under root matching any of
    the glob patterns (recursive), symlinks followed."""
    out = {}
    for pat in patterns:
        for p in glob.glob(os.path.join(root, pat), recursive=True):
            if os.path.isfile(p) and "__pycache__" not in p.split(os.sep):
                out[os.path.relpath(p, root)] = sha256_file(p)
    return dict(sorted(out.items()))


def digest(mapping):
    return sha256_bytes(json.dumps(mapping, sort_keys=True).encode("utf-8"))


def wheel_core(wheel):
    """sha256 of the core library inside the wheel."""
    with zipfile.ZipFile(wheel) as z:
        names = sorted(n for n in z.namelist()
                       if n.startswith("bolorgir/_lib/libbolorgir") and not n.endswith("/"))
        if not names:
            raise SystemExit(f"{wheel}: no bolorgir/_lib/libbolorgir* inside")
        cores = {n: sha256_bytes(z.read(n)) for n in names}
    if len(set(cores.values())) != 1:
        raise SystemExit(f"{wheel}: the core files differ: {cores}")
    return next(iter(cores.values())), sorted(cores)


def pip_freeze(py):
    return subprocess.run([py, "-m", "pip", "freeze"], capture_output=True, text=True,
                          check=True).stdout.splitlines()


def python_version(py):
    return subprocess.run([py, "--version"], capture_output=True, text=True,
                          check=True).stdout.strip()


def module_versions(py):
    """{module: installed distribution version or None} in a venv, as
    MaskBench's Engine.get_version reads it (importlib.metadata)."""
    code = ("import importlib.metadata as m, json, sys\n"
            "out = {}\n"
            "for n in sys.argv[1:]:\n"
            "    try:\n"
            "        out[n] = m.version(n)\n"
            "    except m.PackageNotFoundError:\n"
            "        out[n] = None\n"
            "print(json.dumps(out))\n")
    return json.loads(subprocess.run([py, "-c", code, *MODULES], capture_output=True, text=True,
                                     check=True).stdout)


def venv_record(venv):
    """Installed bolorgir files, serializer, core, freeze and Python of a venv."""
    sps = glob.glob(os.path.join(venv, "lib", "python*", "site-packages"))
    if len(sps) != 1:
        raise SystemExit(f"{venv}: expected one site-packages, found {sps}")
    sp = sps[0]
    pkg = os.path.join(sp, "bolorgir")
    files = tree_files(pkg, ["**/*"])
    if not files:
        raise SystemExit(f"{venv}: bolorgir is not installed")
    cores = {k: v for k, v in files.items() if k.startswith(os.path.join("_lib", "libbolorgir"))}
    py = os.path.join(venv, "bin", "python")
    freeze = pip_freeze(py)
    version = python_version(py)
    return {"package_digest": digest(files), "package_files": len(files),
            "serializer_sha256": files.get("serializer.py"),
            "core_sha256": sorted(set(cores.values())),
            "modules": module_versions(py),
            "pip_freeze": sorted(freeze), "python": version}


def participants(venvs):
    """The expected variants with the versions installed in their venvs."""
    return {name: {"venv": venv, "module": module, "engine_id": engine_id,
                   "version": ((venvs.get(venv) or {}).get("modules") or {}).get(module)}
            for name, venv, module, engine_id in PARTICIPANTS}


def build(args):
    m = {"created": None, "parameters": {
        "threads": args.threads, "time_limit": args.time_limit, "mem_limit": args.mem_limit,
        "profile": "spec-v1", "serializer": "bolorgir", "compact": True,
        "smoke_files": args.smoke_files, "tokenizer_revision": args.tokenizer_revision}}
    core, names = wheel_core(args.wheel)
    m["wheel"] = {"sha256": sha256_file(args.wheel), "name": os.path.basename(args.wheel),
                  "core_sha256": core, "core_files": names}
    m["venvs"] = {os.path.basename(v.rstrip("/")): venv_record(v) for v in args.venv}
    m["participants"] = participants(m["venvs"])
    m["tools"] = {t: sha256_file(os.path.join(HERE, t)) for t in TOOLS}
    mb = args.maskbench
    head = subprocess.run(["git", "-C", mb, "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip() or None
    code = tree_files(mb, ["maskbench/**/*.py", "scripts/**/*.py"])
    m["maskbench"] = {"head": head, "code_digest": digest(code), "code_files": code}
    if args.files_list:
        with open(args.files_list, encoding="utf-8") as f:
            names = [line.strip() for line in f if line.strip()]
        corpus = {n: sha256_file(os.path.join(args.data, n)) for n in sorted(names)}
    else:
        corpus = tree_files(args.data, ["*.json"])
    m["corpus"] = {"data_dir": os.path.relpath(args.data, mb), "files": len(corpus),
                   "digest": digest(corpus), "index": corpus}
    tok = tree_files(args.tokenizer_dir, ["*"])
    m["tokenizer"] = {"revision": os.path.basename(os.path.normpath(args.tokenizer_dir)),
                      "files": tok}
    if args.acceptance:
        with open(args.acceptance, encoding="utf-8") as f:
            acc = json.load(f)
        m["t3_acceptance"] = {"path": args.acceptance, "sha256": sha256_file(args.acceptance),
                              "verdict": acc["acceptance"]["verdict"],
                              "candidate_library_sha256":
                                  acc["inputs"]["candidate"]["library_sha256"]}
    else:
        m["t3_acceptance"] = None
    m["machine"] = {"platform": platform.platform(), "cpus": os.cpu_count(),
                    "processor": platform.processor()}
    return m


def problems(m):
    """Inconsistencies inside one manifest."""
    out = []
    core = m["wheel"]["core_sha256"]
    for name, v in m["venvs"].items():
        if v["core_sha256"] != [core]:
            out.append(f"{name}: installed core {v['core_sha256']} is not the wheel's {core[:16]}")
    serial = {v["serializer_sha256"] for v in m["venvs"].values()}
    if len(serial) != 1 or None in serial:
        out.append(f"the serializer differs between the venvs or is missing: {sorted(map(str, serial))}")
    digests = {v["package_digest"] for v in m["venvs"].values()}
    if len(digests) != 1:
        out.append("the installed bolorgir package differs between the venvs")
    if m["tokenizer"]["revision"] != m["parameters"]["tokenizer_revision"]:
        out.append(f"tokenizer snapshot {m['tokenizer']['revision']} is not the pinned "
                   f"{m['parameters']['tokenizer_revision']}")
    if not m["corpus"]["files"]:
        out.append("empty corpus")
    parts = m.get("participants") or {}
    if sorted(parts) != sorted(p[0] for p in PARTICIPANTS):
        out.append(f"participants {sorted(parts)} are not the five T8a variants")
    for name, venv, module, engine_id in PARTICIPANTS:
        p = parts.get(name) or {}
        if (p.get("venv"), p.get("module"), p.get("engine_id")) != (venv, module, engine_id):
            out.append(f"participant {name}: {p.get('venv')}/{p.get('module')}/"
                       f"{p.get('engine_id')}, expected {venv}/{module}/{engine_id}")
        if venv not in m["venvs"]:
            out.append(f"participant {name}: venv {venv} not recorded")
        elif not p.get("version"):
            out.append(f"participant {name}: {module} is not installed in {venv}")
        elif venv == "venv-pinned" and module in PINNED and p["version"] != PINNED[module]:
            out.append(f"participant {name}: {module} {p['version']}, the pin is {PINNED[module]}")
    return out


def compare(old, new):
    """Fields that differ between two manifests, with what changed."""
    out = []

    def walk(a, b, path):
        if path.split(".")[0] in NOT_COMPARED:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                walk(a.get(k), b.get(k), f"{path}.{k}" if path else k)
            return
        if a != b:
            out.append(f"{path}: {str(a)[:80]} -> {str(b)[:80]}")

    walk(old, new, "")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("record", "check"))
    ap.add_argument("--wheel", required=True)
    ap.add_argument("--venv", action="append", required=True)
    ap.add_argument("--maskbench", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--files-list", default="")
    ap.add_argument("--tokenizer-dir", required=True)
    ap.add_argument("--tokenizer-revision", required=True)
    ap.add_argument("--threads", type=int, required=True)
    ap.add_argument("--time-limit", type=int, default=900)
    ap.add_argument("--mem-limit", type=int, default=40)
    ap.add_argument("--smoke-files", type=int, default=0)
    ap.add_argument("--acceptance", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--manifest", default="")
    args = ap.parse_args(argv)
    m = build(args)
    bad = problems(m)
    if args.mode == "record":
        import datetime
        m["created"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=1)
            f.write("\n")
    else:
        with open(args.manifest, encoding="utf-8") as f:
            old = json.load(f)
        bad += [f"changed {d}" for d in compare(old, m)]
    for b in bad:
        print(f"t8a_identity: {b}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
