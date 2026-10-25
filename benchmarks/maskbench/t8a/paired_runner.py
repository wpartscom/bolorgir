"""MaskBench runner with one instance serialization for every engine.

T8a: llguidance and XGrammar run on the token streams Bolorgir
consumes. With T8A_SERIALIZER=bolorgir every engine gets
the serialize_instance hook of the Bolorgir adapter
(`bolorgir.serializer.serialize_value` against the file's schema: compact,
declared keys in schema order, values preserved exactly); Bolorgir runs
with BOLORGIR_SERIALIZER=1 and its own hook. With --compact, XGrammar
compiles with the compact separators, the language of those streams.
The engine id gets the suffix `-t8a`, so the output directory and the
results row record the protocol.

Stream records: for every file the runner finishes, the
sidecar `<output>/t8a_streams/<file>` lists per test instance the sha256
of the serialized bytes, the sha256 and length of the token ids given to
the engine, the number of tokens the engine consumed and the step where
it rejected, or the serializer's refusal. t8a_report.py compares them
with the reference streams of t8a_streams.py.

Run as the runner module of paired_driver.py, from the MaskBench
directory with this directory on PYTHONPATH and the bolorgir package
installed in the venv:

    python3 -m paired_runner --multi --llg --compact ... files
"""

import json
import os
import sys

from maskbench import runner

from t8a_streams import ids_digest

SERIALIZER = os.environ.get("T8A_SERIALIZER", "")


class SharedSerializationIncompatible(ValueError):
    serialization_incompatible = True


def _shared_serializer(engine):
    from bolorgir.serializer import serialize_value

    compile_grammar = engine.compile_grammar

    def compile_and_keep(schema):
        engine.t8a_schema = schema
        return compile_grammar(schema)

    def serialize_instance(data):
        payload, status = serialize_value(data, engine.t8a_schema)
        if not status.ok:
            raise SharedSerializationIncompatible(f"{status.reason} at {status.pointer}")
        return payload.decode("utf-8")

    engine.compile_grammar = compile_and_keep
    engine.serialize_instance = serialize_instance


def _compact_xgrammar(engine):
    # Nothing else of xgrammar is read here: 0.1.17, the MaskBench pin, has
    # no __version__ (smoke t8a-smoke-2); meta.txt records the version.
    import json as _json

    def compile_grammar(schema):
        engine.compiled_grammar = engine.xgr_compiler.compile_json_schema(
            _json.dumps(schema), any_whitespace=False, separators=(",", ":"),
            strict_mode=True)

    engine.compile_grammar = compile_grammar


class _RecordingTokenizer:
    """The runner's tokenizer with encode() recorded; every other
    attribute is the tokenizer's own. Installed after engine.init(), so
    the engines build their tables from the real tokenizer."""

    def __init__(self, tok, sink):
        self._t8a_tok = tok
        self._t8a_sink = sink

    def encode(self, text, **kw):
        ids = self._t8a_tok.encode(text, **kw)
        self._t8a_sink(text, ids)
        return ids

    def __getattr__(self, name):
        return getattr(self._t8a_tok, name)


def _install_recorders(engine):
    """Wrap reset, serialize_instance, compute_mask and commit_token of the
    engine; the current file's records go to engine.t8a_tests.

    A call that raises records the phase, the instance and the token index on
    the current instance record (`aborted`) before the exception continues.
    The file-level wrapper `process_file` copies the same marker into the
    result file when the exception escapes, so a crash names its cause
    instead of leaving a bare pending marker. The marker stays a marker:
    status_of still classifies the file as crash_or_timeout."""
    engine.t8a_tests = []
    engine.t8a_abort = None

    def cur():
        return engine.t8a_tests[-1] if engine.t8a_tests else None

    def note_abort(phase, at):
        def wrap(fn):
            def call(*a, **kw):
                try:
                    return fn(*a, **kw)
                except Exception as e:
                    c = cur()
                    marker = {"phase": phase, "at": at(c), "error": type(e).__name__,
                              "instance": None if c is None else c.get("i")}
                    if c is not None and "aborted" not in c:
                        c["aborted"] = marker
                    if engine.t8a_abort is None:
                        engine.t8a_abort = marker
                    raise
            return call
        return wrap

    reset = engine.reset
    engine.reset = note_abort("reset", lambda c: 0)(reset)

    reset_rec = engine.reset

    def reset_then_record():
        engine.t8a_tests.append({"i": len(engine.t8a_tests), "consumed": 0,
                                 "rejected_at": None})
        return reset_rec()

    engine.reset = reset_then_record

    compute = engine.compute_mask
    engine.compute_mask = note_abort(
        "compute_mask", lambda c: 0 if c is None else c["consumed"])(compute)

    ser = getattr(engine, "serialize_instance", None)
    if ser is not None:
        def serialize_rec(data):
            try:
                return ser(data)
            except Exception as e:
                if getattr(e, "serialization_incompatible", False) and cur() is not None:
                    cur()["serialization_incompatible"] = str(e)
                raise
        engine.serialize_instance = serialize_rec

    commit = engine.commit_token
    commit_rec = note_abort(
        "commit_token", lambda c: 0 if c is None else c["consumed"])(commit)

    def commit_then_record(t):
        ok = commit_rec(t)
        c = cur()
        if c is not None:
            if ok:
                c["consumed"] += 1
            elif c["rejected_at"] is None:
                c["rejected_at"] = c["consumed"]
        return ok

    engine.commit_token = commit_then_record

    def sink(text, ids):
        c = cur()
        if c is not None:
            import hashlib
            c["instance_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
            c["ids_sha256"] = ids_digest(ids)
            c["n_ids"] = len(ids)

    engine.t8a_sink = sink


def _write_abort_marker(engine, file):
    """Name the cause in the pending marker of a file whose processing
    raised. The marker keeps its `pending_file` key, so the report still
    counts the file as crash_or_timeout; `aborted` records the phase, the
    instance and the token index. Best effort: a missing or unreadable
    marker leaves the file as it was."""
    if not getattr(engine, "multi", False):
        return
    path = os.path.join(runner.output_path, os.path.basename(file))
    try:
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return
    if "pending_file" not in rec or "aborted" in rec:
        return
    rec["aborted"] = getattr(engine, "t8a_abort", None) or {
        "phase": "unknown", "at": None, "error": "unknown"}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    os.replace(tmp, path)


def process_file(engine, file, compact=False):
    if not isinstance(engine.tokenizer, _RecordingTokenizer):
        engine.tokenizer = _RecordingTokenizer(engine.tokenizer, engine.t8a_sink)
    engine.t8a_tests = []
    engine.t8a_abort = None
    try:
        status = _process_file(engine, file, compact)
    except Exception:
        _write_abort_marker(engine, file)
        raise
    if status is not None:
        d = os.path.join(runner.output_path, "t8a_streams")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, os.path.basename(file)), "w", encoding="utf-8") as f:
            json.dump({"id": os.path.basename(file), "compile_error": "compile_error" in status,
                       "tests": engine.t8a_tests}, f)
    return status


def get_engine(args):
    engine = _get_engine(args)
    if args.compact and engine.get_module() == "xgrammar" and not (
            getattr(engine, "llama_cpp", False) or getattr(engine, "compliant", False)):
        _compact_xgrammar(engine)
    if SERIALIZER == "bolorgir" and engine.get_module() != "bolorgir":
        _shared_serializer(engine)
    _install_recorders(engine)
    base_id = engine.get_id
    engine.get_id = lambda: base_id() + "-t8a"
    return engine


_get_engine = runner.get_engine
runner.get_engine = get_engine
_process_file = runner.process_file
runner.process_file = process_file

if __name__ == "__main__":
    if SERIALIZER != "bolorgir":
        sys.exit(f"T8A_SERIALIZER={SERIALIZER!r}: a T8a run needs 'bolorgir'")
    runner.main()
