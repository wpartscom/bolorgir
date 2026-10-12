#!/usr/bin/env python3
"""Accept-path overhead of the rollback history (ADR-0014).

Compares the kernel accept time per token (`blg_stats.accept_ns_total` of
the session over `tokens_accepted`) of library A (a build without the
history) and library B (the new build). Every round runs A and B in fresh
child processes in alternating order (AB, BA, ...); each child replays
the same seeded random walks over a fixed set of spec-v1 schemas on the
byte-level tokenizer and reports the sha256 of the token stream it
accepted (schema, walk and token ids in order). A round whose two streams
differ, or a stream that changes between rounds, makes the run unusable:
the comparison is only valid on identical token streams.

Output: the per-round ratios B/A, their median and a bootstrap 95%
interval of the median over rounds (10,000 resamples, seed fixed), the
sha256 of both libraries and the stream, and the build flags given for
both libraries.

Modes:
  --check-bound   the T9 bound for sessions created with
                  max_rollback_tokens = 0 (needs --depth 0, --flags-a equal
                  to --flags-b, two different libraries). Verdict
                  `within` when the upper end of the 95% interval is at
                  most 1.02 (exit 0), `exceeds` when the median is above
                  1.02 (exit 1), `inconclusive` otherwise (exit 3; run
                  more rounds).
  default         a diagnostic measurement (any --depth); it never checks
                  the bound and exits 0 when the run is usable.

Exit status 2: unusable run (streams differ, a child failed, equal
libraries or flags that differ in --check-bound).

Usage (a flags value starts with "-", so it is given with "=": argparse
reads `--flags-a -Drelease=true` as an option without a value):
  python3 benchmarks/bench_rollback_overhead.py --lib-a OLD.so --lib-b NEW.so \\
      --flags-a="-Drelease=true" --flags-b="-Drelease=true" --check-bound \\
      [--rounds 20] [--out result.json]

--check-args parses and checks the arguments (the --check-bound
conditions that need no library) and exits 0, or 2 like an unusable run;
no library is loaded and nothing is measured.
"""

import argparse
import hashlib
import json
import os
import random
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SCHEMAS = [
    {"type": "object",
     "properties": {"id": {"type": "integer", "minimum": 0},
                    "name": {"type": "string", "maxLength": 32},
                    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 4}},
     "required": ["id", "name"], "additionalProperties": False},
    {"type": "array", "items": {"anyOf": [{"type": "number"}, {"type": "boolean"}]}, "maxItems": 16},
    {"type": "object", "additionalProperties": {"type": "string", "pattern": "^[a-z0-9]+$"},
     "maxProperties": 6},
]
WALKS = 40
MAX_STEPS = 200
SEED = 20261007
BOUND = 1.02
EXIT_WITHIN, EXIT_EXCEEDS, EXIT_UNUSABLE, EXIT_INCONCLUSIVE = 0, 1, 2, 3


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def child(lib, depth):
    os.environ["BLG_LIB_PATH"] = lib
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import blg_ctypes as blg
    from conftest import make_byte_tokenizer

    if blg.LIB._path != lib:
        raise SystemExit(f"loaded {blg.LIB._path}, expected {lib}")
    if depth and not blg.has_rollback():
        raise SystemExit(f"{lib} has no rollback history; --depth needs one")
    spec = make_byte_tokenizer()
    eos = set(spec.eos_ids)
    ctx = blg.Context(spec, blg.BLG_MODE_ADAPTIVE)
    rng = random.Random(SEED)
    stream = hashlib.sha256()
    ns = tokens = 0
    for si, schema in enumerate(SCHEMAS):
        g = blg.compile_schema(ctx, schema, profile=b"spec-v1")
        for wi in range(WALKS):
            s = blg.Session(ctx, g, max_rollback_tokens=depth) if depth else blg.Session(ctx, g)
            stream.update(f"|{si}:{wi}:".encode())
            for _step in range(MAX_STEPS):
                body = sorted(s.fill_mask_ids() - eos)
                if not body:
                    break
                t = rng.choice(body)
                if s.accept(t) != blg.BLG_OK:
                    stream.update(b"!")
                    break
                stream.update(t.to_bytes(4, "little"))
            st = s.get_stats()
            ns += st.accept_ns_total
            tokens += st.tokens_accepted
            s.destroy()
        blg.grammar_release(g)
    ctx.destroy()
    print(json.dumps({"ns_per_token": ns / max(1, tokens), "tokens": tokens,
                      "stream_sha256": stream.hexdigest()}))


def run_child(lib, depth):
    proc = subprocess.run([sys.executable, __file__, "--child", lib, "--depth", str(depth)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"child for {lib} exited {proc.returncode}: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def verdict(median, hi):
    if hi <= BOUND:
        return "within", EXIT_WITHIN
    if median > BOUND:
        return "exceeds", EXIT_EXCEEDS
    return "inconclusive", EXIT_INCONCLUSIVE


def parser():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lib-a")
    ap.add_argument("--lib-b")
    ap.add_argument("--flags-a", default="", help="zig build flags of library A (--flags-a=...)")
    ap.add_argument("--flags-b", default="", help="zig build flags of library B (--flags-b=...)")
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--depth", type=int, default=0)
    ap.add_argument("--check-bound", action="store_true")
    ap.add_argument("--check-args", action="store_true",
                    help="parse and check the arguments only; no library is loaded")
    ap.add_argument("--child")
    ap.add_argument("--out")
    return ap


def argument_problems(args):
    """The problems of the arguments that need no library."""
    problems = []
    if not args.child and not (args.lib_a and args.lib_b):
        problems.append("--lib-a and --lib-b are required")
    if args.check_bound:
        if args.depth != 0:
            problems.append("--check-bound measures max_rollback_tokens = 0; --depth must be 0")
        if not args.flags_a or args.flags_a != args.flags_b:
            problems.append(f"build flags differ or are not given: A {args.flags_a!r}, "
                            f"B {args.flags_b!r}")
        if args.rounds < 10:
            problems.append("--check-bound needs at least 10 rounds")
    return problems


def main(argv=None):
    ap = parser()
    args = ap.parse_args(argv)
    if args.child:
        child(args.child, args.depth)
        return 0
    problems = argument_problems(args)
    if args.check_args:
        print(json.dumps({"flags_a": args.flags_a, "flags_b": args.flags_b,
                          "rounds": args.rounds, "depth": args.depth,
                          "check_bound": args.check_bound, "problems": problems}))
        return EXIT_UNUSABLE if problems else 0
    if not (args.lib_a and args.lib_b):
        ap.error("--lib-a and --lib-b are required")
    lib_a, lib_b = os.path.abspath(args.lib_a), os.path.abspath(args.lib_b)
    sha_a, sha_b = sha256_file(lib_a), sha256_file(lib_b)
    if sha_a == sha_b:
        problems.append("library A and B are the same file content")
    if problems:
        print("bench_rollback_overhead: unusable: " + "; ".join(problems), file=sys.stderr)
        return EXIT_UNUSABLE

    ratios, rows, streams = [], [], set()
    try:
        for r in range(args.rounds):
            order = ("a", "b") if r % 2 == 0 else ("b", "a")
            res = {}
            for which in order:
                lib = lib_a if which == "a" else lib_b
                res[which] = run_child(lib, 0 if which == "a" else args.depth)
            if res["a"]["stream_sha256"] != res["b"]["stream_sha256"]:
                raise RuntimeError(f"round {r}: token streams differ (A "
                                   f"{res['a']['stream_sha256'][:16]}, B "
                                   f"{res['b']['stream_sha256'][:16]})")
            streams.add(res["a"]["stream_sha256"])
            if len(streams) != 1:
                raise RuntimeError(f"round {r}: the token stream changed between rounds")
            ratio = res["b"]["ns_per_token"] / res["a"]["ns_per_token"]
            ratios.append(ratio)
            rows.append({"round": r, "order": "".join(order),
                         **{f"{k}_ns_per_token": v["ns_per_token"] for k, v in res.items()},
                         "tokens": res["a"]["tokens"], "ratio": ratio})
            print(f"round {r:2d} {''.join(order)}: A {res['a']['ns_per_token']:.1f} ns, "
                  f"B {res['b']['ns_per_token']:.1f} ns, B/A {ratio:.4f}", flush=True)
    except (RuntimeError, ValueError) as e:
        print(f"bench_rollback_overhead: unusable: {e}", file=sys.stderr)
        return EXIT_UNUSABLE
    rng = random.Random(SEED)
    boots = sorted(statistics.median(rng.choices(ratios, k=len(ratios))) for _ in range(10_000))
    median = statistics.median(ratios)
    ci = [boots[249], boots[9749]]
    summary = {"lib_a": lib_a, "lib_a_sha256": sha_a, "flags_a": args.flags_a,
               "lib_b": lib_b, "lib_b_sha256": sha_b, "flags_b": args.flags_b,
               "depth_b": args.depth, "rounds": args.rounds,
               "stream_sha256": streams.pop(), "tokens": rows[0]["tokens"],
               "median_ratio": median, "ci95": ci, "bound": BOUND,
               "mode": "check-bound" if args.check_bound else "diagnostic", "rows": rows}
    status = 0
    if args.check_bound:
        summary["verdict"], status = verdict(median, ci[1])
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=1))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=1)
            f.write("\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
