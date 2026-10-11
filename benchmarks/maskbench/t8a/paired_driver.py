"""scripts/run_maskbench.py with paired_runner as the per-chunk runner.

Same arguments, chunk command and log.txt as the MaskBench script; the
chunks run `python -m paired_runner`, and meta.txt records the -t8a engine
id. Run from the MaskBench directory with this directory on PYTHONPATH.

Rework spec C4: before any chunk starts, a preflight in this process
builds the engine as a chunk does (tokenizer, init, compile, serialize,
one mask and one token) on a trivial schema that every engine compiles;
a failure exits with status 3. The files are handed out by t8a_queue
instead of the script's own loop, which gives files still running in one
worker to the others once its list runs empty (smoke t8a-smoke-1): a
file runs in one chunk at a time and goes back to the queue only after
its chunk ended without an output for it (a chunk cut by a timeout gives
back the files it had not reached). A chunk without any output for its
files stops the run (an import or start-up failure would otherwise be
retried for ever), status 4. MaskBench's own outcomes (a pending marker
after a timeout, a compile error) are outputs and are not retried.
"""

import importlib.util
import json
import os
import sys

import paired_runner  # noqa: F401  (patches maskbench.runner)
from maskbench.runner import get_engine, get_files, get_output, setup_argparse
from t8a_queue import FileQueue, run_queue


PREFLIGHT_SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}},
                    "required": ["a"], "additionalProperties": False}


def preflight(args):
    from transformers import AutoTokenizer

    engine = get_engine(args)
    engine.tokenizer = AutoTokenizer.from_pretrained(engine.tokenizer_model_id)
    engine.init()
    engine.compile_grammar(PREFLIGHT_SCHEMA)
    engine.reset()
    data = {"a": 1}
    text = None
    ser = getattr(engine, "serialize_instance", None)
    if ser is not None:
        try:
            text = ser(data)
        except Exception as e:
            if not getattr(e, "serialization_incompatible", False):
                raise
    if text is None:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    ids = engine.tokenizer.encode(text, add_special_tokens=False)
    engine.compute_mask()
    if not ids or not engine.commit_token(ids[0]):
        raise RuntimeError(f"the first token of {text!r} is refused")
    return engine


def main():
    spec = importlib.util.spec_from_file_location(
        "run_maskbench", os.path.join("scripts", "run_maskbench.py"))
    rm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rm)

    args = setup_argparse().parse_args()
    files = sorted(get_files(args))
    if not files:
        raise SystemExit("no files")
    try:
        engine = preflight(args)
    except Exception as e:  # noqa: BLE001
        print(f"T8A_PREFLIGHT_FAILED {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(3)
    print(f"T8A_PREFLIGHT_OK {engine.get_id()}", file=sys.stderr)
    rm.output_path = get_output(args)
    rm.cmd = [sys.executable, "-m", "paired_runner", "--multi"] + [
        a for a in sys.argv[1:] if a not in args.files]
    info = (f"{len(files)} files, timeout {args.time_limit}s, memory {args.mem_limit}GB, "
            f"output {rm.output_path}; {args.num_threads} threads; "
            f"T8A_SERIALIZER={paired_runner.SERIALIZER or '-'}; cmd: {' '.join(rm.cmd)}")
    print(info, file=sys.stderr)
    os.makedirs(rm.output_path, exist_ok=True)
    with open(os.path.join(rm.output_path, "meta.txt"), "w") as meta:
        meta.write(json.dumps({
            "id": engine.get_id(), "name": engine.get_name(), "module": engine.get_module(),
            "module_version": engine.get_version(), "cmd": rm.cmd, "info": info,
        }, indent=2))

    def has_output(f):
        # The test of scripts/run_maskbench.py (missing_files).
        return os.path.exists(os.path.join(rm.output_path, os.path.basename(f)))

    queue = FileQueue(files, has_output)
    failure = run_queue(queue, rm.run_cmd, args.num_threads, chunk_size=100)
    if failure:
        print(f"T8A_RUN_STOPPED {failure}; see {os.path.join(rm.output_path, 'log.txt')}",
              file=sys.stderr)
        sys.exit(4)


if __name__ == "__main__":
    main()
