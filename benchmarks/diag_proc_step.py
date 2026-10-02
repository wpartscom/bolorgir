#!/usr/bin/env python3
"""Per-step tail of the HF processor diagnostics.

Localizes the ~1.75 ms/step zig vs xgrammar difference on s1 b32: phase
timers inside ConstraintLogitsProcessor (token sync, fill_mask, unpack+H2D,
the rest = mask application and checks), total xgrammar processor time,
D2H-pattern microbenchmarks and torch.profiler on one repeat per engine.
NOT an acceptance tool, does not go into REPORT.

    PYTHONPATH=python:benchmarks HF_HUB_OFFLINE=1 \
        python3 -u benchmarks/diag_proc_step.py --batch 32 --reps 3
"""

from __future__ import annotations

import argparse
import json
import time

import torch

import bolorgir as zc
from bolorgir import Engine
from bolorgir import transformers as zt

from bench_e2e import SCHEMAS, build_inputs, summarize_run
from hf_common import close_result, constrained_generate_safe, load_model


def microbench():
    x = torch.arange(32, device="cuda", dtype=torch.long)
    torch.cuda.synchronize()
    t0 = time.perf_counter_ns()
    s = 0
    for i in range(32):
        s += int(x[i])
    torch.cuda.synchronize()
    t1 = time.perf_counter_ns()
    t2 = time.perf_counter_ns()
    col = x.reshape(32, 1).tolist()
    torch.cuda.synchronize()
    t3 = time.perf_counter_ns()
    print(f"micro: 32x int(scalar)={((t1-t0)/1e3):.0f}us  "
          f"1x [32,1].tolist()={((t3-t2)/1e3):.0f}us  (s={s}, {len(col)})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default="s1_flat_enum")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--engines", default="zig,xgrammar")
    ap.add_argument("--profiler", action="store_true", default=True)
    ap.add_argument("--no-profiler", dest="profiler", action="store_false")
    args = ap.parse_args()

    microbench()
    model, tokenizer = load_model()
    engine = Engine(mode="adaptive", tokenizer=tokenizer)
    spec = SCHEMAS[args.schema]
    constraint = engine.compile(spec["schema"])
    schema_str = json.dumps(spec["schema"])
    inputs = build_inputs(tokenizer, spec["prompts"], args.batch)
    prompt_len = inputs["input_ids"].shape[1]

    engines = [e.strip() for e in args.engines.split(",")]

    # --- zig phases ---
    ph = {"fill_us": 0.0, "unpack_us": 0.0, "sync_us": 0.0, "fillbatch_us": 0.0}
    state = {"call_us": 0.0, "calls": 0, "fill_calls": 0}

    orig_fill = zc.Session.fill_mask

    def fill_timed(self):
        t = time.perf_counter_ns()
        r = orig_fill(self)
        ph["fill_us"] += (time.perf_counter_ns() - t) / 1e3
        state["fill_calls"] += 1
        return r

    zc.Session.fill_mask = fill_timed

    orig_unpack = zt.MaskGpuUnpacker.unpack

    def unpack_timed(self, masks, v, device):
        t = time.perf_counter_ns()
        r = orig_unpack(self, masks, v, device)
        ph["unpack_us"] += (time.perf_counter_ns() - t) / 1e3
        return r

    zt.MaskGpuUnpacker.unpack = unpack_timed

    orig_sync = zt.ConstraintLogitsProcessor._advance

    def sync_timed(self, i, upto):
        t = time.perf_counter_ns()
        r = orig_sync(self, i, upto)
        ph["sync_us"] += (time.perf_counter_ns() - t) / 1e3
        return r

    zt.ConstraintLogitsProcessor._advance = sync_timed

    orig_fillbatch = zt.ConstraintLogitsProcessor._fill_masks

    def fillbatch_timed(self, rows):
        t = time.perf_counter_ns()
        r = orig_fillbatch(self, rows)
        ph["fillbatch_us"] += (time.perf_counter_ns() - t) / 1e3
        return r

    zt.ConstraintLogitsProcessor._fill_masks = fillbatch_timed

    orig_call = zt.ConstraintLogitsProcessor.__call__

    def call_timed(self, ids, scores):
        t = time.perf_counter_ns()
        r = orig_call(self, ids, scores)
        state["call_us"] += (time.perf_counter_ns() - t) / 1e3
        state["calls"] += 1
        return r

    zt.ConstraintLogitsProcessor.__call__ = call_timed

    # --- sections: generate / finish_rows / create_session / ctor ---
    sec = {"gen_us": 0.0, "finish_us": 0.0, "sess_us": 0.0, "ctor_us": 0.0,
           "xg_gen_us": 0.0}

    orig_finishrows = zt.ConstraintLogitsProcessor.finish_rows

    def finishrows_timed(self, sequences):
        t = time.perf_counter_ns()
        r = orig_finishrows(self, sequences)
        sec["finish_us"] += (time.perf_counter_ns() - t) / 1e3
        return r

    zt.ConstraintLogitsProcessor.finish_rows = finishrows_timed

    orig_ctor = zt.ConstraintLogitsProcessor.__init__

    def ctor_timed(self, *a, **k):
        t = time.perf_counter_ns()
        orig_ctor(self, *a, **k)
        sec["ctor_us"] += (time.perf_counter_ns() - t) / 1e3

    zt.ConstraintLogitsProcessor.__init__ = ctor_timed

    orig_createsession = zc.Constraint.create_session

    def createsession_timed(self, *a, **k):
        t = time.perf_counter_ns()
        r = orig_createsession(self, *a, **k)
        sec["sess_us"] += (time.perf_counter_ns() - t) / 1e3
        return r

    zc.Constraint.create_session = createsession_timed

    orig_generate = model.generate

    def generate_timed(*a, **k):
        t = time.perf_counter_ns()
        r = orig_generate(*a, **k)
        dt = (time.perf_counter_ns() - t) / 1e3
        sec["gen_us"] += dt
        sec["xg_gen_us"] += dt  # reset before the xg rep
        return r

    model.generate = generate_timed

    # --- total xgrammar time ---
    xg_state = {"call_us": 0.0, "calls": 0}
    xg_factory = None
    if "xgrammar" in engines:
        import xgrammar as xg
        from xgrammar.contrib.hf import LogitsProcessor as XgProcessor

        info = xg.TokenizerInfo.from_huggingface(tokenizer)
        compiler = xg.GrammarCompiler(info)

        def xg_factory():  # noqa: F811
            compiled = compiler.compile_json_schema(schema_str)
            return XgProcessor(compiled)

        orig_xcall = XgProcessor.__call__

        def xcall_timed(self, ids, scores):
            t = time.perf_counter_ns()
            r = orig_xcall(self, ids, scores)
            xg_state["call_us"] += (time.perf_counter_ns() - t) / 1e3
            xg_state["calls"] += 1
            return r

        XgProcessor.__call__ = xcall_timed

    def run_zig():
        torch.manual_seed(42)
        res = constrained_generate_safe(
            model, tokenizer, constraint, inputs=inputs,
            max_new_tokens=512, do_sample=False)
        close_result(res)
        return res.sequences.shape[1]

    def run_xg():
        torch.manual_seed(42)
        proc = xg_factory()
        out = model.generate(**inputs, max_new_tokens=512, do_sample=False,
                             logits_processor=[proc])
        seq = out.sequences if hasattr(out, "sequences") else out
        return seq.shape[1]

    def phases_reset():
        for k in ph:
            ph[k] = 0.0
        for k in sec:
            sec[k] = 0.0
        state["call_us"] = 0.0
        state["calls"] = 0
        state["fill_calls"] = 0
        xg_state["call_us"] = 0.0
        xg_state["calls"] = 0

    # Warmup: model/engine/compiler.
    for mode in engines:
        phases_reset()
        (run_zig if mode == "zig" else run_xg)()
        torch.cuda.synchronize()

    for rep in range(args.reps):
        for mode in engines:
            phases_reset()
            torch.cuda.synchronize()
            t0 = time.perf_counter_ns()
            (run_zig if mode == "zig" else run_xg)()
            torch.cuda.synchronize()
            dt = (time.perf_counter_ns() - t0) / 1e6
            if mode == "zig":
                rest = (
                    state["call_us"] - ph["fill_us"] - ph["unpack_us"]
                    - ph["sync_us"] - ph["fillbatch_us"]
                )
                post = dt * 1e3 - sec["gen_us"] - sec["finish_us"]
                print(
                    f"rep{rep} zig: total={dt:.0f}ms proc={state['call_us']/1e3:.2f}ms/"
                    f"{state['calls']} steps fill_batch={ph['fillbatch_us']/1e3:.2f}ms "
                    f"advance={ph['sync_us']/1e3:.2f}ms unpack={ph['unpack_us']/1e3:.2f}ms "
                    f"apply+check={rest/1e3:.2f}ms | gen={sec['gen_us']/1e3:.2f}ms "
                    f"finish={sec['finish_us']/1e3:.2f}ms sess={sec['sess_us']/1e3:.2f}ms "
                    f"ctor={sec['ctor_us']/1e3:.2f}ms post-gen/finish={post/1e3:.2f}ms",
                    flush=True)
            else:
                print(f"rep{rep} xgrammar: total={dt:.0f}ms "
                      f"proc={xg_state['call_us']/1e3:.2f}ms/{xg_state['calls']} steps "
                      f"gen={sec['xg_gen_us']/1e3:.2f}ms",
                      flush=True)

    if args.profiler:
        from torch.profiler import ProfilerActivity, profile

        for mode in engines:
            fn = run_zig if mode == "zig" else run_xg
            with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA]) as prof:
                fn()
                torch.cuda.synchronize()
            print(f"=== profiler {mode} (sorted by cuda_time_total) ===")
            print(prof.key_averages().table(sort_by="cuda_time_total", row_limit=16))
            print(f"=== profiler {mode} (sorted by self_cpu_time_total) ===")
            print(prof.key_averages().table(sort_by="self_cpu_time_total", row_limit=16))

    constraint.close()
    engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
