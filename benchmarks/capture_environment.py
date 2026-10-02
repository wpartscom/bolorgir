#!/usr/bin/env python3
"""Capture the environment of an acceptance collection (protocol v3).

Writes environment.txt: git revision (commit + tree cleanliness), name and
sha256 of the active pinned acceptance protocol, combined sha256 of the
sources (cross-checked by the gate between collections), machine conditions
(loadavg, MemAvailable, uptime, uname), GPU (nvidia-smi), top processes by
CPU, package versions.

Usage from the project root:
    PYTHONPATH=benchmarks python3 benchmarks/capture_environment.py \
        --tag v3-run1 --out benchmarks/results/<timestamp>_v3-run1/environment.txt
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import summarize_control as sc  # noqa: E402

# Sources whose immutability between collections is confirmed by the gate
# (sources_sha256); manifest.json is NOT included here: it is updated after
# the run (control_run/verdict), while the protocol spec is pinned by a
# separate protocol_sha256 hash.
SOURCE_FILES = [
    "build.zig",
    "include/bolorgir.h",
    "python/bolorgir/transformers.py",
    "python/bolorgir/tokenizers.py",
    "python/bolorgir/__init__.py",
    "benchmarks/summarize_control.py",
    "benchmarks/bench_compare_uniform.py",
    "benchmarks/bench_e2e_constrained.py",
    "benchmarks/bench_coldproc.py",
    "benchmarks/run_all.py",
    "benchmarks/capture_environment.py",
]
ARTIFACT_FILES = [
    "zig-out/lib/libbolorgir.so.0.1.0",
    "python/bolorgir/_core.abi3.so",
]
PACKAGES = ["torch", "transformers", "tokenizers", "xgrammar", "llguidance",
            "jsonschema", "numpy", "pytest"]


def _run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return (p.stdout or "").strip() or (p.stderr or "").strip()
    except Exception as e:  # noqa: BLE001
        return f"unavailable ({type(e).__name__})"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def source_paths():
    paths = list(SOURCE_FILES)
    src_dir = os.path.join(ROOT, "src")
    paths += [f"src/{n}" for n in sorted(os.listdir(src_dir))
              if n.endswith(".zig")]
    return sorted(set(paths))


def _pkg_version(name):
    try:
        import importlib.metadata as md
        return md.version(name)
    except Exception:  # noqa: BLE001
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", required=True, help="collection id (run_tag)")
    ap.add_argument("--out", required=True, help="path to environment.txt")
    args = ap.parse_args()

    man = sc.load_manifest()
    proto_name = sc.active_protocol_name(man)
    spec = (man.get("control_scenario", {}).get(proto_name) or {}) \
        if proto_name else {}

    lines = []
    lines.append(f"# environment ({args.tag}, acceptance collection)")
    lines.append("date: " + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    lines.append("collection_tag: " + args.tag)
    lines.append("commit: " + _run(["git", "-C", ROOT, "rev-parse", "HEAD"]))
    dirty = _run(["git", "-C", ROOT, "status", "--porcelain"])
    if dirty.startswith("unavailable"):
        lines.append("tree_status: unknown (" + dirty + ")")
    elif dirty:
        lines.append("tree_status: dirty")
        lines.append("tree_dirty_files: "
                     + "; ".join(dirty.splitlines()[:10]))
    else:
        lines.append("tree_status: clean")
    lines.append("protocol: " + str(proto_name))
    lines.append("protocol_fixed_at: " + str(spec.get("fixed_at")))
    lines.append("protocol_sha256: " + (sc.protocol_sha256(spec) if spec
                                        else "none"))
    lines.append("uname: " + " ".join(platform.uname()))
    cpu = "unknown"
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    lines.append("cpu: " + cpu)
    lines.append("cores: " + str(os.cpu_count()))
    gpu = _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    lines.append("gpu: " + gpu)
    lines.append("zig: " + _run(["zig", "version"]))
    lines.append("python: " + platform.python_version())

    lines.append("")
    lines.append("# machine conditions at collection time (acceptance_protocol)")
    try:
        with open("/proc/loadavg", encoding="ascii") as f:
            lines.append("loadavg_1_5_15: " + f.read().strip())
    except OSError:
        lines.append("loadavg_1_5_15: unavailable")
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable"):
                    lines.append("mem_available: " + line.strip())
                    break
    except OSError:
        lines.append("mem_available: unavailable")
    try:
        with open("/proc/uptime", encoding="ascii") as f:
            lines.append("uptime_seconds: " + f.read().split()[0])
    except OSError:
        lines.append("uptime_seconds: unavailable")

    lines.append("")
    lines.append("# GPU state (nvidia-smi)")
    lines.append("gpu_smi: " + _run([
        "nvidia-smi",
        "--query-gpu=name,driver_version,clocks.sm,clocks.mem,temperature.gpu,"
        "utilization.gpu,power.draw,memory.used",
        "--format=csv,noheader"]))

    lines.append("")
    lines.append("# top processes by CPU at collection time")
    ps = _run(["ps", "-eo", "pcpu,comm", "--sort=-pcpu"])
    if ps and not ps.startswith("unavailable"):
        for row in ps.splitlines()[1:7]:
            lines.append("top_proc: " + " ".join(row.split()))
    else:
        lines.append("top_proc: " + ps)

    lines.append("")
    lines.append("# package versions (importlib.metadata)")
    for pkg in PACKAGES:
        lines.append(f"{pkg}: {_pkg_version(pkg)}")

    lines.append("")
    lines.append("# artifact sha256")
    for rel in ARTIFACT_FILES:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            lines.append(f"{sha256_file(p)}  {rel}")

    lines.append("")
    lines.append("# engine source sha256")
    zig_src = [rel for rel in source_paths() if rel.startswith("src/")
               or rel.startswith("include/")]
    for rel in zig_src:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            lines.append(f"{sha256_file(p)}  {rel}")

    lines.append("")
    lines.append("# sha256 of the Python adapter, the gate and the benchmarks")
    other = [rel for rel in source_paths() if rel not in zig_src]
    hashes = {}
    for rel in other:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            hashes[rel] = sha256_file(p)
            lines.append(f"{hashes[rel]}  {rel}")

    combined = hashlib.sha256()
    for rel in sorted(set(zig_src) | set(other)):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            combined.update(f"{sha256_file(p)}  {rel}\n".encode("utf-8"))
    lines.append("")
    lines.append("# combined source hash (cross-checked by the gate between collections)")
    lines.append("sources_sha256: " + combined.hexdigest())

    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
