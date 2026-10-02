#!/usr/bin/env python3
"""GPU mask intervals: build / H2D transfer / apply.

SPEC 10.5: publish build, transfer, apply and full-path times separately
(overlapping intervals are not summed as independent latency); GPU
microbenchmarks synchronize the measured operations
(torch.cuda.synchronize around every measured stage).

Participants: Bolorgir (adaptive), XGrammar, llguidance - on one
tokenizer (Qwen2.5-1.5B-Instruct by default, as in B7), one schema and
one trace (canonical BPE tokenization of --document, acceptance confirmed
by accept/consume of every engine).

Run from the project root:
    PYTHONPATH=python:benchmarks python3 benchmarks/bench_gpu_mask_path.py \
        --document '{"action":"buy","amount":42}'
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_common as bc


def pct(samples):
    return bc.percentile_stats(samples)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--schema", default="closed_object_action_amount")
    ap.add_argument("--corpus-dir", default=bc.CORPUS_DIR)
    ap.add_argument("--document", default='{"action":"buy","amount":42}')
    ap.add_argument("--tokenizer", default="Qwen/Qwen2.5-1.5B-Instruct")
    ap.add_argument("--tokenizer-revision", default=None)
    ap.add_argument("--steps", type=int, default=200,
                    help="number of measured steps per engine (the trace is looped)")
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--seed", type=int, default=bc.SEED)
    args = ap.parse_args()

    torch = bc.import_or_skip("torch")
    np = bc.import_or_skip("numpy")
    tf = bc.import_or_skip("transformers")
    zc = bc.import_or_skip("bolorgir")
    xg = bc.import_or_skip("xgrammar")
    lg = bc.import_or_skip("llguidance")
    if None in (torch, np, tf, zc, xg, lg):
        bc.skip("needs torch, numpy, transformers, bolorgir, xgrammar, llguidance")
    if not torch.cuda.is_available():
        bc.skip("CUDA is not available: the GPU matrix is not closed in this configuration")
    import llguidance.hf as lghf

    entry, schema_bytes = bc.load_schema(args.schema, args.corpus_dir)
    if entry["kind"] != "json_schema" or not entry["expect_support"]:
        bc.skip(f"schema {args.schema} is not a supported json_schema")
    schema = json.loads(schema_bytes)
    schema_str = schema_bytes.decode("utf-8")

    hf_tok = tf.AutoTokenizer.from_pretrained(args.tokenizer,
                                              revision=args.tokenizer_revision)
    trace = hf_tok.encode(args.document)
    vocab_pad = 151936  # Qwen2.5-1.5B lm_head width; logits are sized as needed below

    total = args.warmup + args.steps
    gpu_name = torch.cuda.get_device_name(0)
    dev = torch.device("cuda")
    out = {"status": "OK", "case": "gpu_mask_path", "gpu": gpu_name,
           "tokenizer": args.tokenizer, "schema": args.schema,
           "document": args.document, "trace": trace, "seed": args.seed,
           "sync_rule": "torch.cuda.synchronize around every measured stage",
           "engines": {}}

    def sync():
        torch.cuda.synchronize()

    # ---------- zig (stock path transformers.py: MaskGpuUnpacker) ----------
    import bolorgir.transformers as zct
    bundle = zc.TokenizerBundle.from_hf(hf_tok)
    engine = zc.Engine(mode="adaptive", memory_limit_mb=256, tokenizer=bundle)
    constraint = engine.compile(schema)
    vocab = engine.vocab_size
    unpacker = zct.MaskGpuUnpacker()
    logits = torch.randn(1, max(vocab, vocab_pad), device=dev)
    build, h2d, apply, full = [], [], [], []
    session = None
    for i in range(total):
        if i % len(trace) == 0:
            if session is not None:
                session.close()
            session = constraint.create_session()
        t = trace[i % len(trace)]
        sync(); t0 = bc.now_ns()
        mask = session.fill_mask()
        sync(); t1 = bc.now_ns()
        allowed = unpacker.unpack(mask, vocab, dev)
        sync(); t2 = bc.now_ns()
        logits[:, :vocab].masked_fill_(torch.logical_not(allowed), float("-inf"))
        logits[:, vocab:] = float("-inf")
        sync(); t3 = bc.now_ns()
        session.accept_token(t)
        if i >= args.warmup:
            build.append(t1 - t0)
            h2d.append(t2 - t1)
            apply.append(t3 - t2)
            full.append(t3 - t0)
    session.close(); constraint.close(); engine.close()
    out["engines"]["zig_adaptive"] = {
        "vocab_size": vocab,
        "note": "stages as in the stock ConstraintLogitsProcessor "
                "(transformers.py, MaskGpuUnpacker): only compact uint32 words "
                "(vocab/32) are transferred to GPU, bit unpacking happens "
                "on device, pinned staging and buffers are cached",
        "build_ns": pct(build), "h2d_ns": pct(h2d), "apply_ns": pct(apply),
        "full_ns": pct(full)}

    # ---------- xgrammar ----------
    info = xg.TokenizerInfo.from_huggingface(hf_tok)
    compiler = xg.GrammarCompiler(info)
    compiled = compiler.compile_json_schema(schema_str)
    bitmask = xg.allocate_token_bitmask(1, info.vocab_size)
    logits = torch.randn(1, max(info.vocab_size, vocab_pad), device=dev)
    build, h2d, apply, full = [], [], [], []
    matcher = None
    for i in range(total):
        if i % len(trace) == 0:
            matcher = xg.GrammarMatcher(compiled)
        t = trace[i % len(trace)]
        sync(); t0 = bc.now_ns()
        matcher.fill_next_token_bitmask(bitmask)
        sync(); t1 = bc.now_ns()
        bm_gpu = bitmask.to(dev, non_blocking=False)
        sync(); t2 = bc.now_ns()
        xg.apply_token_bitmask_inplace(logits, bm_gpu)
        sync(); t3 = bc.now_ns()
        matcher.accept_token(t)
        if i >= args.warmup:
            build.append(t1 - t0)
            h2d.append(t2 - t1)
            apply.append(t3 - t2)
            full.append(t3 - t0)
    out["engines"]["xgrammar"] = {
        "vocab_size": info.vocab_size,
        "note": "apply via xgrammar.apply_token_bitmask_inplace; "
                "logits are wider than the bitmask (padded lm_head) - the "
                "tail is not masked (stock behavior of xgrammar.contrib.hf)",
        "build_ns": pct(build), "h2d_ns": pct(h2d), "apply_ns": pct(apply),
        "full_ns": pct(full)}

    # ---------- llguidance ----------
    import llguidance.torch as lgt
    lt = lghf.from_tokenizer(hf_tok)
    grm = lg.grammar_from("json_schema", schema_str)
    bm = lgt.allocate_token_bitmask(1, lt.vocab_size)
    logits = torch.randn(1, max(lt.vocab_size, vocab_pad), device=dev)
    build, h2d, apply, full = [], [], [], []
    m = None
    for i in range(total):
        if i % len(trace) == 0:
            m = lg.LLMatcher(lt, grm)
        t = trace[i % len(trace)]
        sync(); t0 = bc.now_ns()
        lgt.fill_next_token_bitmask(m, bm)
        sync(); t1 = bc.now_ns()
        bm_gpu = bm.to(dev)
        sync(); t2 = bc.now_ns()
        lgt.apply_token_bitmask_inplace(logits, bm_gpu)
        sync(); t3 = bc.now_ns()
        m.consume_token(t)
        if i >= args.warmup:
            build.append(t1 - t0)
            h2d.append(t2 - t1)
            apply.append(t3 - t2)
            full.append(t3 - t0)
    out["engines"]["llguidance"] = {
        "vocab_size": lt.vocab_size,
        "build_ns": pct(build), "h2d_ns": pct(h2d), "apply_ns": pct(apply),
        "full_ns": pct(full)}

    out["peak_rss_bytes"] = bc.peak_rss_bytes()
    bc.emit(out)


if __name__ == "__main__":
    main()
