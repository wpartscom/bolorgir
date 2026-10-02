#!/usr/bin/env python3
"""Fixed-baseline-slice perf measurement (ROADMAP rev 2 section 6, A8.5).

Reproduces the pinned baseline numbers of
benchmarks/reports/<date>_perf-baseline/REPORT.md against the slice pinned
in benchmarks/baseline_slice.json:

1. cold_permissive: first visit of the permissive string-content state at
   the 128k-token Llama vocabulary; every observation is a fresh Engine +
   compile + new session (no warmup possible), N observations, sequential.
2. warm_permissive: the same state served by the adaptive cache; one
   populating fill (discarded), then N timed observations, each a verified
   cache hit.
3. dfs_cost_model: mask DFS work of the cold state via the C ABI
   (tests/blg_ctypes.py): work_ops_total and mask_ns_total diffs around one
   blg_fill_mask with work_limit_ops set, plus the pure-Python trie node
   count of the vocabulary.
4. corpus_warm: fill_mask distributions on trace replay over the fixed
   corpus slice and over the full available corpus set, reported separately
   (synthetic byte tokenizer, the B2/B3 convention).

Run from the project root (tokenizer must be in the local HF cache):
    HF_HUB_OFFLINE=1 PYTHONPATH=python:benchmarks \
        python3 benchmarks/bench_baseline_slice.py
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_common as bc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_MANIFEST = os.path.join(HERE, "baseline_slice.json")


def environment():
    cpu = "unknown"
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass

    def run(cmd):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return p.stdout.strip()
        except Exception:
            return "unavailable"

    mem_kb = None
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    mem_kb = int(line.split()[1])
                    break
    except OSError:
        pass
    loadavg = "unavailable"
    try:
        with open("/proc/loadavg", encoding="ascii") as f:
            loadavg = f.read().strip()
    except OSError:
        pass
    return {
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "commit": run(["git", "-C", ROOT, "rev-parse", "HEAD"]),
        "tree_dirty_src_include_python": bool(run(
            ["git", "-C", ROOT, "status", "--porcelain",
             "src", "include", "python"])),
        "cpu": cpu,
        "cores": os.cpu_count(),
        "mem_total_kb": mem_kb,
        "loadavg_1_5_15": loadavg,
        "uname": " ".join(platform.uname()),
        "python": platform.python_version(),
        "zig": run(["zig", "version"]),
    }


def prefix_token_ids(bundle, prefix_bytes):
    """Greedy longest-match tokenization of the probe prefix into token ids.

    The exact spelling matters (it is the compact JSON prefix); fail loudly
    if the vocabulary cannot cover it.
    """
    by_bytes = {}
    for i in range(bundle.vocab_size):
        by_bytes.setdefault(bundle.token_bytes(i), i)
    ids = []
    pos = 0
    while pos < len(prefix_bytes):
        for end in range(len(prefix_bytes), pos, -1):
            tid = by_bytes.get(prefix_bytes[pos:end])
            if tid is not None:
                ids.append(tid)
                pos = end
                break
        else:
            raise SystemExit(f"vocabulary cannot spell the probe prefix at "
                             f"offset {pos}: {prefix_bytes!r}")
    cat = b"".join(bundle.token_bytes(i) for i in ids)
    assert cat == prefix_bytes, f"prefix does not assemble: {cat!r}"
    return ids


def bench_probe_cold(zc, bundle, probe, repeats):
    """Fresh engine per observation: the state is always a first visit."""
    ids = prefix_token_ids(bundle, probe["prefix_bytes"].encode("utf-8"))
    fill_ns, compile_ns = [], []
    for _ in range(repeats):
        eng = zc.Engine(mode="adaptive", memory_limit_mb=256, tokenizer=bundle)
        t0 = bc.now_ns()
        constraint = eng.compile(probe["schema"])
        compile_ns.append(bc.now_ns() - t0)
        session = constraint.create_session()
        for tid in ids:
            session.accept_token(tid)
        t0 = bc.now_ns()
        session.fill_mask()
        fill_ns.append(bc.now_ns() - t0)
        session.close()
        constraint.close()
        eng.close()
    return {"repeats": repeats,
            "compile_ns": bc.percentile_stats(compile_ns),
            "cold_fill_ns": bc.percentile_stats(fill_ns)}


def bench_probe_warm(zc, bundle, probe, observations):
    """One populating fill (discarded); every timed fill is a cache hit."""
    ids = prefix_token_ids(bundle, probe["prefix_bytes"].encode("utf-8"))
    eng = zc.Engine(mode="adaptive", memory_limit_mb=256, tokenizer=bundle)
    constraint = eng.compile(probe["schema"])
    session = constraint.create_session()
    for tid in ids:
        session.accept_token(tid)
    session.fill_mask()  # cold, admitted to the cache; not measured
    session.close()
    hits0 = eng.stats()["cache_hits"]
    fill_ns = []
    for _ in range(observations):
        session = constraint.create_session()
        for tid in ids:
            session.accept_token(tid)
        t0 = bc.now_ns()
        session.fill_mask()
        fill_ns.append(bc.now_ns() - t0)
        session.close()
    hits = eng.stats()["cache_hits"] - hits0
    constraint.close()
    eng.close()
    return {"observations": observations, "cache_hits": hits,
            "all_hits": hits == observations,
            "warm_fill_ns": bc.percentile_stats(fill_ns)}


def trie_stats(bundle):
    """Pure-Python byte-trie node/edge count (mirrors src/tokenizer.zig:
    eos and special token bytes have no trie nodes)."""
    trie = [{}]
    token_bytes_total = 0
    excluded = set(bundle.eos_ids) | set(bundle.special_ids)
    for i in range(bundle.vocab_size):
        if i in excluded:
            continue
        data = bundle.token_bytes(i)
        token_bytes_total += len(data)
        node = 0
        for byte in data:
            nxt = trie[node].get(byte)
            if nxt is None:
                nxt = len(trie)
                trie[node][byte] = nxt
                trie.append({})
            node = nxt
    return {"trie_nodes": len(trie),
            "trie_edges": sum(len(d) for d in trie),
            "token_bytes_total": token_bytes_total}


def bench_dfs_cost_model(bundle, probe):
    """work_ops_total / mask_ns_total diffs around blg_fill_mask (C ABI).

    Uses tests/blg_ctypes.py because the high-level package does not export
    work_ops_total. work_limit_ops is set high so Work.charge() counts
    (src/work.zig: ops are only accumulated against a nonzero limit).
    """
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    try:
        import types
        import blg_ctypes as blg
    except ImportError as e:
        return {"status": "SKIP", "reason": str(e)}
    vocab = bundle.vocab_size
    spec = types.SimpleNamespace(
        tokens=[bundle.token_bytes(i) for i in range(vocab)],
        vocab_size=vocab,
        eos_ids=list(bundle.eos_ids),
        special_ids=list(bundle.special_ids),
        mask_words=(vocab + 31) // 32)
    ctx = blg.Context(spec, mode=blg.BLG_MODE_ADAPTIVE,
                      memory_limit_bytes=512 << 20,
                      cache_limit_bytes=blg.BLG_CACHE_DEFAULT,
                      work_limit_ops=1 << 60)
    try:
        grammar = blg.compile_schema(ctx, probe["schema"])
        ids = prefix_token_ids(bundle, probe["prefix_bytes"].encode("utf-8"))

        session = blg.Session(ctx, grammar)
        for tid in ids:
            assert session.accept(tid) == blg.BLG_OK
        s0 = ctx.get_stats()
        t0 = bc.now_ns()
        status, _words = session.fill_mask_words()
        wall_ns = bc.now_ns() - t0
        s1 = ctx.get_stats()
        session.destroy()
        if status != blg.BLG_OK:
            return {"status": "ERROR", "fill_status": status}
        ops = s1.work_ops_total - s0.work_ops_total
        core_ns = s1.mask_ns_total - s0.mask_ns_total

        session = blg.Session(ctx, grammar)
        for tid in ids:
            assert session.accept(tid) == blg.BLG_OK
        s0 = ctx.get_stats()
        status, _words = session.fill_mask_words()
        s1 = ctx.get_stats()
        session.destroy()
        warm = {"ops": s1.work_ops_total - s0.work_ops_total,
                "core_ns": s1.mask_ns_total - s0.mask_ns_total,
                "cache_hit": (s1.cache_hits - s0.cache_hits) == 1}
        blg.grammar_release(grammar)
        out = {"status": "OK",
               "cold": {"work_ops": ops, "core_ns": core_ns,
                        "wall_ns": wall_ns,
                        "ns_per_op": core_ns / max(ops, 1)},
               "warm": warm}
        out.update(trie_stats(bundle))
        return out
    finally:
        ctx.destroy()


def bench_corpus_warm(zc, names, corpus_dir, observations, seed):
    """B2-style trace replay per schema (synthetic byte tokenizer)."""
    import random
    bundle = bc.make_byte_tokenizer(zc)
    engine = zc.Engine(mode="adaptive", memory_limit_mb=256, tokenizer=bundle)
    per_schema, pooled = {}, []
    for name in names:
        entry, raw = bc.load_schema(name, corpus_dir)
        schema = json.loads(raw)
        constraint = engine.compile(schema)
        probe = constraint.create_session()
        vocab = bundle.vocab_size
        try:
            trace, completed = bc.gen_trace(probe, vocab, random.Random(seed),
                                            max_steps=8192)
        except Exception as e:
            # e.g. long_string: the seed-42 random walk reaches a DeadEnd
            # state at step 1073 (canonical-v1, recorded in the baseline
            # report); the schema stays in the set with an ERROR entry.
            per_schema[name] = {"status": "ERROR",
                                "reason": f"trace raised {type(e).__name__}"}
            probe.abort()
            probe.close()
            constraint.close()
            continue
        probe.abort()
        probe.close()
        if not completed or not trace:
            per_schema[name] = {"status": "ERROR",
                                "reason": "no valid trace",
                                "completed": completed}
            constraint.close()
            continue
        # warmup replay: populates the adaptive cache, discarded
        session = constraint.create_session()
        for tid in trace:
            session.fill_mask()
            session.accept_token(tid)
        session.abort()
        session.close()
        mask_ns = []
        got = 0
        while got < observations:
            session = constraint.create_session()
            for tid in trace:
                t0 = bc.now_ns()
                session.fill_mask()
                mask_ns.append(bc.now_ns() - t0)
                session.accept_token(tid)
            got += len(trace)
            session.abort()
            session.close()
        per_schema[name] = {"status": "OK", "trace_len": len(trace),
                            "fill_mask_ns": bc.percentile_stats(mask_ns)}
        pooled.extend(mask_ns)
        constraint.close()
    engine.close()
    return {"observations_per_schema": observations,
            "per_schema": per_schema,
            "pooled_fill_mask_ns": bc.percentile_stats(pooled)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--corpus-dir", default=bc.CORPUS_DIR)
    ap.add_argument("--cold-repeats", type=int, default=None)
    ap.add_argument("--warm-observations", type=int, default=None)
    ap.add_argument("--corpus-observations", type=int, default=None)
    ap.add_argument("--seed", type=int, default=bc.SEED)
    ap.add_argument("--skip-corpus", action="store_true")
    ap.add_argument("--skip-probes", action="store_true")
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)
    proto = manifest["protocol"]
    obs = proto["observations"]
    cold_repeats = args.cold_repeats or obs["cold_permissive_fill_ns"]
    warm_obs = args.warm_observations or obs["warm_permissive_fill_ns"]
    corpus_obs = args.corpus_observations or obs["corpus_warm_fill_ns"]

    zc = bc.require_core()
    tf = bc.import_or_skip("transformers")
    out = {"status": "OK", "case": "perf_baseline_slice",
           "manifest": os.path.relpath(args.manifest),
           "seed": args.seed, "environment": environment(),
           "protocol": proto}

    # ---- 128k parts: cold / warm permissive + DFS cost model ----
    tok_spec = manifest["tokenizer_128k"]
    if args.skip_probes:
        pass
    elif tf is None:
        out["tokenizer_128k"] = {"status": "SKIP",
                                 "reason": "transformers is not available"}
    else:
        try:
            hf_tok = tf.AutoTokenizer.from_pretrained(
                tok_spec["name"], revision=tok_spec["revision"])
            bundle = zc.TokenizerBundle.from_hf(hf_tok, use_cache=False)
            out["tokenizer_128k"] = {**tok_spec, "status": "OK",
                                     "vocab_size": bundle.vocab_size}
            for probe in manifest["probes"]:
                print(f"cold probe {probe['id']} ...", file=sys.stderr,
                      flush=True)
                cold = bench_probe_cold(zc, bundle, probe, cold_repeats)
                print(f"warm probe {probe['id']} ...", file=sys.stderr,
                      flush=True)
                warm = bench_probe_warm(zc, bundle, probe, warm_obs)
                cost = bench_dfs_cost_model(bundle, probe)
                out.setdefault("probes", {})[probe["id"]] = {
                    "cold": cold, "warm": warm, "dfs_cost_model": cost}
        except Exception as e:
            out["tokenizer_128k"] = {"status": "ERROR",
                                     "error": f"{type(e).__name__}: {e}"[:300]}

    # ---- corpus warm path: fixed slice vs full available set (A8.5) ----
    if not args.skip_corpus:
        slice_names = [e["name"] for e in manifest["corpus_slice"]]
        full_names = sorted(
            name for name, kind, expect, _err, _raw in bc.load_corpus(
                args.corpus_dir)
            if kind == "json_schema" and expect
            and not bc.load_schema(name, args.corpus_dir)[0].get("secondary"))
        print(f"corpus warm: slice {len(slice_names)} schemas, "
              f"full set {len(full_names)}", file=sys.stderr, flush=True)
        out["corpus_warm"] = {
            "tokenizer": manifest["tokenizer_synthetic"]["name"],
            "baseline_slice": bench_corpus_warm(zc, slice_names,
                                                args.corpus_dir, corpus_obs,
                                                args.seed),
            "full_available_set": bench_corpus_warm(zc, full_names,
                                                    args.corpus_dir,
                                                    corpus_obs, args.seed)}

    bc.emit(out)


if __name__ == "__main__":
    main()
