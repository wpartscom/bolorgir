#!/usr/bin/env python3
"""diag.py ROOT RUN_DIR FILE OUT_DIR: one corpus file of a T8a run again,
alone, under the run's protocol, with a phase journal and the memory of
the process - the diagnosis of a file that timed out (reviewer,
2026-10-10: phases, instance and token, mask times, peak memory; the
historical outcomes are not changed).

  ROOT     the tree the run was started from (benchmarks/maskbench/t8a
           and the MaskBench checkout under benchmarks/external)
  RUN_DIR  the T8a work dir: identity/manifest.json and venv-pinned, used
           read only; its fingerprint is compared before and after
  FILE     a corpus file name, e.g. Github_medium---o65503.json
  OUT_DIR  new directory: out/ (the MaskBench output of the file),
           journal.jsonl (diag_runner.py), mem.jsonl (VmRSS and VmHWM of
           the process every 0.5 s, read from /proc by this process),
           stacks.txt (the Python stacks at the runner's SIGALRM),
           runner.log, summary.json and summary.md

Before the run every input is checked against the run's manifest: the
tools paired_runner.py and t8a_streams.py, MaskBench's code files, the
corpus file, the tokenizer files of the pinned revision, the core of
the bolorgir package in venv-pinned, and the parameters (time limit,
memory limit, profile, serializer, compact); a difference refuses. The
command is the run's chunk command for the one file (blg-spec-v1:
BOLORGIR_PROFILE=spec-v1, BOLORGIR_SERIALIZER=1, T8A_SERIALIZER=bolorgir,
nice -n 19, --multi --blg --compact, one thread, the runner's 900 s per
file and its own 40 GB RLIMIT_AS) with diag_runner in place of
paired_runner. Writes only OUT_DIR. Exit: 0 when the diagnosis ran (the
file's own outcome is in the summary), 1 when RUN_DIR changed, 2 refused.
"""
import glob
import hashlib
import json
import os
import resource
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = "libbolorgir.so.0.1.0"


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def refuse(msg):
    print(f"refused: {msg}", file=sys.stderr)
    sys.exit(2)


def fingerprint(d):
    h = hashlib.sha256()
    n = 0
    for dp, dns, fns in os.walk(d):
        dns.sort()
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            st = os.lstat(p)
            h.update(f"{os.path.relpath(p, d)} {st.st_size} {st.st_mtime_ns}\n".encode())
            n += 1
    return {"files": n, "digest": h.hexdigest()}


def proc_mem(pid):
    out = {}
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                k = line.split(":")[0]
                if k in ("VmRSS", "VmHWM", "VmSize", "VmPeak"):
                    out[k] = int(line.split()[1])
    except OSError:
        return None
    return out


def summarize(journal, mem, rc, wall, maxrss_kb, stacks, marker):
    evs = [json.loads(line) for line in open(journal, encoding="utf-8")]
    t_file = next((e["t"] for e in evs if e["e"] == "file_start"), None)
    rel = (lambda t: None if t_file is None else round(t - t_file, 3))
    comp = next((e for e in evs if e["e"] == "compile_end"), None)
    inst = {}
    for e in evs:
        i = e.get("i")
        if e["e"] == "reset":
            inst[i] = {"i": i, "start_s": rel(e["t"]), "n_tokens": None, "steps": 0,
                       "mask_us": 0, "commit_us": 0, "max_mask_us": 0, "max_mask_k": None,
                       "masks_over_100ms": 0, "masks_over_900ms": 0, "rejected_at": None,
                       "serialize": None}
        elif e["e"] == "ser_end" and i in inst:
            inst[i]["serialize"] = {k: e[k] for k in ("us", "bytes", "error") if k in e}
        elif e["e"] == "encode" and i in inst:
            inst[i]["n_tokens"] = e["n"]
        elif e["e"] == "tok" and i in inst:
            r = inst[i]
            r["steps"] += 1
            r["mask_us"] += e["mask_us"] or 0
            r["commit_us"] += e["commit_us"]
            if (e["mask_us"] or 0) > r["max_mask_us"]:
                r["max_mask_us"], r["max_mask_k"] = e["mask_us"], e["k"]
            r["masks_over_100ms"] += (e["mask_us"] or 0) > 100_000
            r["masks_over_900ms"] += (e["mask_us"] or 0) > 900_000
            if not e["ok"] and r["rejected_at"] is None:
                r["rejected_at"] = e["k"]
    toks = [e for e in evs if e["e"] == "tok"]
    slow = sorted(toks, key=lambda e: -(e["mask_us"] or 0))[:15]
    last = evs[-1] if evs else None
    ended = any(e["e"] == "file_end" for e in evs)
    in_progress = None
    if not ended and last is not None:
        if last["e"] == "compile_start":
            in_progress = "compile_grammar"
        elif last["e"] == "ser_start":
            in_progress = f"serialize_instance of instance {last['i']}"
        elif last["e"] in ("encode", "tok", "reset", "ser_end"):
            i = last.get("i")
            k = last["k"] + 1 if last["e"] == "tok" else 0
            in_progress = (f"instance {i}, token {k}: compute_mask or commit_token"
                           if last["e"] in ("encode", "tok") else
                           f"instance {i}: before its token ids ({last['e']})")
        else:
            in_progress = f"after {last['e']}"
    hwm = max((m.get("VmHWM", 0) for m in mem), default=None)
    return {
        "exit_code": rc, "timeout": rc == -14, "wall_s": round(wall, 3),
        "file_outcome": marker,
        "compile": None if comp is None else {k: comp[k] for k in ("us", "error") if k in comp},
        "instances": [inst[i] for i in sorted(inst)],
        "steps": len(toks),
        "mask_us_total": sum(e["mask_us"] or 0 for e in toks),
        "commit_us_total": sum(e["commit_us"] for e in toks),
        "slowest_masks": [{"i": e["i"], "k": e["k"], "mask_us": e["mask_us"],
                           "commit_us": e["commit_us"], "at_s": rel(e["t"])} for e in slow],
        "last_event": last, "file_ended": ended, "in_progress_at_end": in_progress,
        "seconds_from_file_start_to_last_event": None if last is None else rel(last["t"]),
        "memory": {"ru_maxrss_children_kb": maxrss_kb, "sampled_vmhwm_kb_max": hwm,
                   "samples": len(mem)},
        "stacks_at_alarm": stacks[:4000] if stacks else None,
    }


def main():
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(2)
    root, run_dir, name, out_dir = (sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
    root, run_dir, out_dir = (os.path.abspath(p) for p in (root, run_dir, out_dir))
    if os.path.exists(out_dir):
        refuse(f"{out_dir} exists")
    m = json.load(open(os.path.join(run_dir, "identity", "manifest.json"), encoding="utf-8"))
    t8a = os.path.join(root, "benchmarks", "maskbench", "t8a")
    mb = os.path.join(root, "benchmarks", "external", "jsonschemabench-maskbench", "maskbench")
    venv = os.path.join(run_dir, "venv-pinned")
    p = m["parameters"]
    want = {"time_limit": 900, "mem_limit": 40, "profile": "spec-v1", "serializer": "bolorgir",
            "compact": True, "threads": 1}
    for k, v in want.items():
        if p.get(k) != v:
            refuse(f"run parameter {k} is {p.get(k)!r}, the diagnosis runs {v!r}")
    checks = {}
    for t in ("paired_runner.py", "t8a_streams.py"):
        got = sha(os.path.join(t8a, t))
        if got != m["tools"][t]:
            refuse(f"{t} differs from the run's ({got[:16]} vs {m['tools'][t][:16]})")
        checks[t] = got
    for rel_p, h in m["maskbench"]["code_files"].items():
        if sha(os.path.join(mb, rel_p)) != h:
            refuse(f"MaskBench {rel_p} differs from the run's")
    checks["maskbench_code_files"] = len(m["maskbench"]["code_files"])
    idx = m["corpus"]["index"]
    if name not in idx:
        refuse(f"{name} is not in the run's corpus index")
    fpath = os.path.join(mb, m["corpus"]["data_dir"], name)
    want_f = idx[name] if isinstance(idx[name], str) else idx[name].get("sha256")
    if sha(fpath) != want_f:
        refuse(f"{name} differs from the run's corpus file")
    checks["corpus_file"] = want_f
    hf = os.environ.get("HF_HOME") or refuse("HF_HOME is not set")
    tokdir = os.path.join(hf, "hub", "models--unsloth--Meta-Llama-3.1-8B-Instruct", "snapshots",
                          m["tokenizer"]["revision"])
    for fn, h in m["tokenizer"]["files"].items():
        if sha(os.path.join(tokdir, fn)) != h:
            refuse(f"tokenizer {fn} differs from the run's")
    checks["tokenizer_revision"] = m["tokenizer"]["revision"]
    cores = glob.glob(os.path.join(venv, "lib", "python*", "site-packages", "bolorgir", "_lib",
                                   CORE))
    if len(cores) != 1 or sha(cores[0]) != m["wheel"]["core_sha256"]:
        refuse("the core of venv-pinned is not the run's")
    checks["core_sha256"] = m["wheel"]["core_sha256"]
    checks["wheel_sha256"] = m["wheel"]["sha256"]
    checks["diag_runner.py"] = sha(os.path.join(HERE, "diag_runner.py"))
    checks["diag.py"] = sha(os.path.abspath(__file__))

    os.makedirs(os.path.join(out_dir, "out"))
    before = fingerprint(run_dir)
    journal = os.path.join(out_dir, "journal.jsonl")
    stacks = os.path.join(out_dir, "stacks.txt")
    env = dict(os.environ, PYTHONPATH=f"{t8a}:{mb}:{HERE}", T8A_SERIALIZER="bolorgir",
               BOLORGIR_PROFILE="spec-v1", BOLORGIR_SERIALIZER="1", HF_HUB_OFFLINE="1",
               PYTHONDONTWRITEBYTECODE="1", DIAG_JOURNAL=journal, DIAG_TRACE=stacks)
    cmd = ["nice", "-n", "19", os.path.join(venv, "bin", "python"), "-u", "-m", "diag_runner",
           "--multi", "--blg", "--compact", "--tokenizer", tokdir, "--num-threads", "1",
           "--output", os.path.join(out_dir, "out"), os.path.join(m["corpus"]["data_dir"], name)]
    with open(os.path.join(out_dir, "command.json"), "w", encoding="utf-8") as f:
        json.dump({"cwd": mb, "cmd": cmd, "env": {k: env[k] for k in (
            "PYTHONPATH", "T8A_SERIALIZER", "BOLORGIR_PROFILE", "BOLORGIR_SERIALIZER",
            "HF_HOME", "HF_HUB_OFFLINE", "PYTHONDONTWRITEBYTECODE")},
            "checks": checks, "run_dir": run_dir, "run_dir_before": before}, f, indent=1)
    print(f"diag {name}: start {time.strftime('%FT%TZ', time.gmtime())}", flush=True)
    mem = []
    t0 = time.monotonic()
    with open(os.path.join(out_dir, "runner.log"), "w") as log, \
            open(os.path.join(out_dir, "mem.jsonl"), "w") as mf:
        child = subprocess.Popen(cmd, cwd=mb, env=env, stdout=log, stderr=subprocess.STDOUT)
        while child.poll() is None:
            s = proc_mem(child.pid)
            if s:
                s["t"] = round(time.monotonic() - t0, 3)
                mem.append(s)
                mf.write(json.dumps(s) + "\n")
                mf.flush()
            time.sleep(0.5)
    wall = time.monotonic() - t0
    rc = child.returncode
    maxrss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    out_f = os.path.join(out_dir, "out", name)
    marker = None
    if os.path.exists(out_f):
        d = json.load(open(out_f, encoding="utf-8"))
        marker = d if "pending_file" in d else {k: d.get(k) for k in (
            "num_tests", "num_tokens", "masks_us", "max_mask_us", "ttfm_us",
            "compile_error", "validation_error") if k in d}
    st = open(stacks, encoding="utf-8").read() if os.path.exists(stacks) else ""
    s = summarize(journal, mem, rc, wall, maxrss, st, marker)
    s["file"] = name
    s["checks"] = checks
    after = fingerprint(run_dir)
    s["run_dir_unchanged"] = after == before
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write(f"# T8a timeout diagnosis: {name}\n\n")
        f.write(f"- exit {rc} ({'timeout, SIGALRM' if rc == -14 else 'no timeout'}), "
                f"wall {s['wall_s']} s; outcome {json.dumps(marker)}\n")
        f.write(f"- compile: {json.dumps(s['compile'])}\n")
        f.write(f"- steps {s['steps']}, compute_mask {s['mask_us_total'] / 1e6:.1f} s, "
                f"commit_token {s['commit_us_total'] / 1e6:.1f} s\n")
        f.write(f"- at the end: {s['in_progress_at_end'] or 'the file finished'} "
                f"({s['seconds_from_file_start_to_last_event']} s after the file started)\n")
        f.write(f"- peak memory: ru_maxrss {maxrss} kB, sampled VmHWM "
                f"{s['memory']['sampled_vmhwm_kb_max']} kB\n")
        f.write(f"- run dir unchanged: {s['run_dir_unchanged']}\n\n")
        f.write("| instance | tokens | steps | rejected at | mask s | max mask ms (token) | "
                ">100 ms | >900 ms | serialize |\n|---:|---:|---:|---:|---:|---|---:|---:|---|\n")
        for r in s["instances"]:
            f.write(f"| {r['i']} | {r['n_tokens']} | {r['steps']} | {r['rejected_at']} | "
                    f"{r['mask_us'] / 1e6:.1f} | {r['max_mask_us'] / 1e3:.0f} ({r['max_mask_k']}) | "
                    f"{r['masks_over_100ms']} | {r['masks_over_900ms']} | "
                    f"{json.dumps(r['serialize'])} |\n")
    print(open(os.path.join(out_dir, "summary.md"), encoding="utf-8").read(), flush=True)
    sys.exit(0 if s["run_dir_unchanged"] else 1)


if __name__ == "__main__":
    main()
