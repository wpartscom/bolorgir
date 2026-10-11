"""diag_runner.py: the T8a chunk runner (paired_runner, the T8a protocol)
with a phase journal, for the diagnosis of a file that timed out in a T8a
run. Same arguments as paired_runner; run by diag.py.

DIAG_JOURNAL names the JSONL journal, DIAG_TRACE the file of the Python
stacks. Every journal line is written through (line buffering), so the
journal of a process that the runner's SIGALRM ends holds every event
before the end. Events (t: seconds since this module was imported,
monotonic):

  file_start, file_end            the file (process_file of paired_runner)
  compile_start, compile_end      compile_grammar (us; error if it raised)
  reset                           a test instance begins (i)
  ser_start, ser_end              serialize_instance (us, bytes or error)
  encode                          the token ids of the instance (n)
  tok                             one step: i, k (token index), mask_us
                                  (compute_mask), commit_us, ok

MaskBench's timer is the runner's: signal.alarm(time_limit) per file with
the default action. faulthandler is registered for SIGALRM with chain:
the stacks of every thread go to DIAG_TRACE, then the default action ends
the process as in the run (exit -14).
"""

import faulthandler
import json
import os
import signal
import time

import paired_runner  # noqa: F401  (patches maskbench.runner: the T8a protocol)
from maskbench import runner

T0 = time.monotonic()
_journal = open(os.environ["DIAG_JOURNAL"], "a", buffering=1, encoding="utf-8")
_trace = open(os.environ["DIAG_TRACE"], "w", encoding="utf-8")
faulthandler.register(signal.SIGALRM, file=_trace, all_threads=True, chain=True)


def ev(e, **kw):
    _journal.write(json.dumps({"e": e, "t": round(time.monotonic() - T0, 6), **kw}) + "\n")


def _us(t):
    return int((time.monotonic() - t) * 1e6)


def _wrap(engine):
    state = {"i": -1, "k": 0}

    compile_grammar = engine.compile_grammar

    def compile_d(schema):
        ev("compile_start")
        t = time.monotonic()
        try:
            r = compile_grammar(schema)
        except Exception as e:
            ev("compile_end", us=_us(t), error=repr(e)[:500])
            raise
        ev("compile_end", us=_us(t))
        return r

    engine.compile_grammar = compile_d

    reset = engine.reset

    def reset_d():
        state["i"] += 1
        state["k"] = 0
        ev("reset", i=state["i"])
        return reset()

    engine.reset = reset_d

    ser = getattr(engine, "serialize_instance", None)
    if ser is not None:
        def ser_d(data):
            ev("ser_start", i=state["i"])
            t = time.monotonic()
            try:
                s = ser(data)
            except Exception as e:
                ev("ser_end", i=state["i"], us=_us(t), error=repr(e)[:500])
                raise
            ev("ser_end", i=state["i"], us=_us(t),
               bytes=None if s is None else len(s.encode("utf-8")))
            return s
        engine.serialize_instance = ser_d

    sink = engine.t8a_sink

    def sink_d(text, ids):
        ev("encode", i=state["i"], n=len(ids), bytes=len(text.encode("utf-8")))
        return sink(text, ids)

    engine.t8a_sink = sink_d

    compute_mask = engine.compute_mask
    commit_token = engine.commit_token
    last = {"mask_us": None}

    def mask_d():
        t = time.monotonic()
        r = compute_mask()
        last["mask_us"] = _us(t)
        return r

    def commit_d(tok):
        t = time.monotonic()
        ok = commit_token(tok)
        ev("tok", i=state["i"], k=state["k"], mask_us=last["mask_us"], commit_us=_us(t),
           ok=bool(ok))
        state["k"] += 1
        return ok

    engine.compute_mask = mask_d
    engine.commit_token = commit_d
    return state


_get_engine = runner.get_engine


def get_engine(args):
    engine = _get_engine(args)
    engine.t8a_diag = _wrap(engine)
    ev("engine", id=engine.get_id(), version=engine.get_version())
    return engine


_process_file = runner.process_file


def process_file(engine, file, compact=False):
    engine.t8a_diag.update(i=-1, k=0)
    ev("file_start", file=os.path.basename(file))
    status = _process_file(engine, file, compact)
    keys = ("ttfm_us", "masks_us", "max_mask_us", "num_tokens", "num_tests",
            "compile_error", "validation_error")
    ev("file_end", status=None if status is None else
       {k: status[k] for k in keys if k in status})
    return status


runner.get_engine = get_engine
runner.process_file = process_file

if __name__ == "__main__":
    if paired_runner.SERIALIZER != "bolorgir":
        raise SystemExit("T8A_SERIALIZER must be 'bolorgir' (the T8a protocol)")
    ev("runner_start", pid=os.getpid())
    runner.main()
    ev("runner_end")
