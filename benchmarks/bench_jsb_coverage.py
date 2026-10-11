#!/usr/bin/env python3
"""JSONSchemaBench coverage scanner (ROADMAP rev 2 section 5, P0 items 1-3, 5).

Compiles every schema of a corpus snapshot with the canonical-v1 profile
and records, per file:

  {dataset, file, status, error_class, keyword, detail, source_pointer,
   error_stage}

with status the raw blg_status name and error_class the outcome bucket of
ROADMAP section 1 (compiled / invalid_schema / unsupported_feature /
resource_limit; unsatisfiable_constraint and harness_error are reported
separately when they occur). Aggregates: outcome buckets, per-dataset
counts, the unsupported-keyword histogram (first blocker only),
invalid-schema subclasses and positive/negative example counts. Output is
a JSON report plus a short markdown summary; the header carries corpus
revision and content hash, engine commit, profile, limits, tokenizer and
date, so the counts are reproducible (A8.2).

Snapshots:

  repo      benchmarks/external/jsonschemabench/data/ (9,558 raw schema
            files, one dataset per subdirectory)
  maskbench benchmarks/external/jsonschemabench-maskbench/maskbench/data/
            (11,306 files at commit ba103c7, wrapper format
            {description, meta, schema, tests}; dataset from the
            "<Dataset>---<file>.json" name, BFCL_*/JME_* families mapped
            to BFCL/JME). Fetch:
              git clone https://github.com/guidance-ai/jsonschemabench \
                  benchmarks/external/jsonschemabench-maskbench
              git -C benchmarks/external/jsonschemabench-maskbench \
                  checkout ba103c7
            (benchmarks/external/ is gitignored.)

The engine is driven through tests/blg_ctypes.py (not the bolorgir
package) because the C ABI error struct carries the JSON pointer that the
Python exceptions drop. Compile-time limits mirror the bolorgir.Engine
defaults (256 MiB memory, 64 MiB cache, 8 MiB session, 1 MiB schema,
depth 64, 64 parse branches). Compile classification does not depend on
the tokenizer; the pinned gpt2 vocab of bench_jsb_support.py is used so
the header names a real tokenizer, with a recorded synthetic fallback.

Semantic mode (--semantic): compile coverage alone is
NOT the exact coverage of ROADMAP section 1, so every schema that
compiles additionally goes through execution checks, and the report
publishes them as split metrics (never one blended number):

  - session success: a session can be created for the compiled grammar;
  - generation success: a completion-biased greedy driver (byte-level
    synthetic tokenizer, token id = byte value, eos=256; the same
    approach as tests/oracle/run_oracle.py) produces at least one full
    document accepted by can_end+finish within a step budget;
  - generated-document validity: the produced document parses as JSON
    and validates against the schema with the pinned python jsonschema
    (validator_for by the schema's $schema dialect);
  - valid acceptance / invalid rejection: where the corpus ships
    instances (the MaskBench wrapper "tests"), each instance is encoded
    with the value-preserving serializer python/bolorgir/serializer.py
    serialize_value (string values are never re-parsed as JSON text) and fed byte-exactly
    (mask bit check + accept + finish); valid instances must be
    accepted, invalid ones rejected. jsonschema cross-checks every
    corpus label: disagreements between the corpus and the validator
    are reported apart from engine errors, and a serialization-incompatible
    outcome is a protocol bucket of its own, never a validation error.

Semantic checks compile the schema a second time in a dedicated context
with the byte tokenizer (the compile-coverage context keeps the pinned
gpt2 vocab, so the compile numbers are unchanged); a divergence between
the two compiles is reported as compile_mismatch. Schemas that compile
but fail a semantic check are listed per failure kind with reasons.

Generatable mode (--generatable): every compiled schema is compiled again
in the context of each tokenizer (compile_check), then gets K passes
(--passes, default 20) per tokenizer: byte-level synthetic, Llama 3.1
(the MaskBench default) and gpt2, both HF tokenizers pinned. Pass 1 is
the greedy generate_document policy over single-byte tokens, passes
2..K the biased random policy of tests/generation_passes.py with seeds
derived from the file. Each pass is one outcome record (outcome, stage,
code, step, prefix length, seed; tests/semantic_outcomes.py), validated
by jsonschema over exact numbers with ECMA-262 `pattern` semantics, and
every compiled schema gets exactly one aggregated outcome per tokenizer.
All K passes are attempted unless --schema-budget-seconds caps them.
All pass records go to coverage_<snapshot>.passes.json.gz, with the whole
document of every invalid_document and validator_error pass. --jobs N
runs chunks of --chunk-size files as subprocesses and merges them; a
finished chunk is reused only when its identity record (engine library,
harness sources, parameters, corpus, packages) matches the current run,
chunks of different engines or parameters are never merged, and a crash
or stall reruns the chunk with the (file, tokenizer) in flight recorded
as engine_defect / CRASH.

--complete-from DIR completes a generatable report of the same engine
library and parameters (coverage_<snapshot>.json[.gz] and its sidecar in
DIR): it runs the passes recorded there as skipped:schema_budget, replays
every invalid_document and validator_error pass that has no document by
its seed to keep the document (the replay must reproduce the recorded
outcome, stage, code, step and prefix length; it runs with
REPLAY_TIME_FACTOR times the per-pass time limit, which only cuts a pass
short; one that ends in engine_unavailable again on a fresh context runs
alone in a child process), and keeps every other record, the compile and
the semantic results unchanged. A completed report can be completed
again: only its replays that were not reproduced run.

Usage:
  python3 benchmarks/bench_jsb_coverage.py --snapshot both
  python3 benchmarks/bench_jsb_coverage.py --snapshot both --semantic \
      --out-dir benchmarks/coverage/spec-v1
  BOLORGIR_COVERAGE_PROFILE=spec-v1 python3 benchmarks/bench_jsb_coverage.py \
      --snapshot both --generatable --hf-sample-every 20 --jobs 8 \
      --work-dir benchmarks/local/chunks --out-dir benchmarks/coverage/spec-v1
  BOLORGIR_COVERAGE_PROFILE=spec-v1 python3 benchmarks/bench_jsb_coverage.py \
      --snapshot both --generatable --hf-sample-every 20 --jobs 8 \
      --complete-from benchmarks/local/run-a --work-dir benchmarks/local/chunks-b \
      --out-dir benchmarks/local/run-b
  BOLORGIR_COVERAGE_PROFILE=spec-v1 python3 benchmarks/bench_jsb_coverage.py \
      --snapshot repo --semantic --max-files 200   # slicing for validation
"""

import argparse
import base64
import ctypes
import gzip
import hashlib
import importlib.util
import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time

_BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_BENCH_DIR)
sys.path.insert(0, os.path.join(_ROOT, "tests"))
sys.path.insert(0, os.path.join(_ROOT, "python"))

import blg_ctypes as blg  # noqa: E402
from reference import TokenizerSpec, canonical_string  # noqa: E402

JSB_REPO = "https://github.com/guidance-ai/jsonschemabench"
JSB_COMMIT = "ba103c73756198dd9b149ddc7db7867da7a077f6"

TOKENIZER_NAME = "openai-community/gpt2"
TOKENIZER_REVISION = "607a30d783dfa663caf39e06633721c8d4cfcd7e"
# MaskBench default tokenizer (maskbench/runner.py), pinned.
LLAMA_NAME = "unsloth/Meta-Llama-3.1-8B-Instruct"
LLAMA_REVISION = "a2856192dd7c25b842431f39c179a6c2c2f627d1"

PROFILE = os.environ.get("BOLORGIR_COVERAGE_PROFILE", "canonical-v1")

# Limits: bolorgir.Engine defaults (python/bolorgir/__init__.py).
LIMITS = {
    "mode": "adaptive",
    "memory_limit_bytes": 256 * 1024 * 1024,
    "cache_limit_bytes": 64 * 1024 * 1024,
    "session_limit_bytes": 8 * 1024 * 1024,
    "schema_limit_bytes": 1024 * 1024,
    "max_depth": 64,
    "max_threads_per_state": 64,
    "work_limit_ops": 0,
}

SNAPSHOTS = {
    "repo": {
        "data_dir": os.path.join(_BENCH_DIR, "external", "jsonschemabench", "data"),
        "layout": "raw",
        "corpus_name": "JSONSchemaBench repository snapshot (data/)",
        "corpus_path": "data/",
        "revision": JSB_COMMIT,
    },
    "maskbench": {
        "data_dir": os.path.join(
            _BENCH_DIR, "external", "jsonschemabench-maskbench", "maskbench", "data"
        ),
        "layout": "maskbench",
        "corpus_name": "JSONSchemaBench MaskBench snapshot (maskbench/data/)",
        "corpus_path": "maskbench/data/",
        "revision": JSB_COMMIT,
    },
}

BUCKET_OF_STATUS = {
    blg.BLG_ERR_INVALID_SCHEMA: "invalid_schema",
    blg.BLG_ERR_UNSUPPORTED_FEATURE: "unsupported_feature",
    blg.BLG_ERR_UNSATISFIABLE_CONSTRAINT: "unsatisfiable_constraint",
    blg.BLG_ERR_RESOURCE_LIMIT: "resource_limit",
    blg.BLG_ERR_UNSUPPORTED_TOKENIZER: "unsupported_tokenizer",
}

# ROADMAP section 1 outcome buckets in report order.
BUCKET_ORDER = [
    "compiled",
    "invalid_schema",
    "unsupported_feature",
    "resource_limit",
    "unsatisfiable_constraint",
    "unsupported_tokenizer",
    "harness_error",
]

# Semantic mode: serializer path and generation defaults.
SERIALIZER_PATH = os.path.join(_ROOT, "python", "bolorgir", "serializer.py")
DEFAULT_MAX_STEPS = 8192
DEFAULT_MAX_GEN_SECONDS = 10.0
# --complete-from: the time limit of a replay, in per-pass time limits.
REPLAY_TIME_FACTOR = 6
# Repeat-run guard of the greedy generator: after this many identical
# byte choices in a row the byte is excluded once (breaks pattern cycles
# like "a*b" where the smallest viable byte never reaches an accept state).
GENERATION_RUN_GUARD = 64
# Cap on stored failure examples per kind (counts stay exact).
FAILURE_EXAMPLES_CAP = 200
# Semantic pass: recycle the byte-tokenizer context after this many
# semantic checks. The engine retains generation-side memory in a
# Context across session destroy/grammar release (R5 finding: after
# ~500 heavy spec-v1 schemas the 256MB context memory limit trips and
# every later compile/session_create fails with RESOURCE_LIMIT), so a
# long measurement must not run in one context. The reactive retry in
# scan() covers windows where the limit is hit before this count.
BYTE_CTX_RECYCLE_EVERY = 150

# Generatable mode (--generatable): K passes per schema and tokenizer.
# Pass 1 runs the generate_document policy, passes 2..K the biased random
# policy of tests/generation_passes.py with seeds derived from GEN_SEED,
# the file and the pass index. With a nonzero --schema-budget-seconds a
# schema whose passes on one tokenizer exceed that wall budget gets its
# remaining passes recorded as skipped:schema_budget (fewer than K
# attempted passes; the report counts such results).
GEN_TOKENIZERS = {
    "byte": None,
    "llama-3.1": (LLAMA_NAME, LLAMA_REVISION),
    "gpt2": (TOKENIZER_NAME, TOKENIZER_REVISION),
}
DEFAULT_PASSES = 20
DEFAULT_SCHEMA_BUDGET_SECONDS = 0.0
GEN_SEED = 20261002
# Non-success pass records kept per schema and tokenizer in the main
# report (all records go to the passes sidecar).
GEN_RECORDS_IN_REPORT = 5
# Bytes of an invalid_document / validator_error document kept with its
# record in the main report; the sidecar keeps the whole document.
GEN_DOCUMENT_CAP = 8192
# Pass outcomes whose document is kept (the triage checks every one).
GEN_DOCUMENT_OUTCOMES = ("invalid_document", "validator_error")


def draft_name(uri):
    """Metaschema URI -> draft label ('draft-04', '2020-12', ...)."""
    m = re.search(r"draft[-/]0?(\d+)", uri)
    if m:
        return f"draft-{int(m.group(1)):02d}"
    m = re.search(r"(20\d\d-\d\d)", uri)
    if m:
        return m.group(1)
    return uri.rstrip("#").rsplit("/", 1)[-1] or uri


def extract_keyword(msg):
    """First-blocker keyword/group label from an UNSUPPORTED_FEATURE message."""
    m = re.match(r"keyword '([^']+)' is outside the MVP profile", msg)
    if m:
        return m.group(1)
    m = re.match(r"unknown keyword '([^']+)'", msg)
    if m:
        return m.group(1)
    m = re.match(r"unsupported \$schema dialect '([^']+)'", msg)
    if m:
        return f"{draft_name(m.group(1))} $schema"
    if msg.startswith("boolean schemas are not supported"):
        return "(boolean schema)"
    if msg.startswith("type as a list is not supported"):
        return "type"
    if msg.startswith("non-scalar enum/const values are not supported"):
        return "enum/const"
    if msg.startswith("mixed-type enum is not supported"):
        return "enum"
    if "$ref" in msg:
        return "$ref"
    return "(other)"


def invalid_subclass(msg):
    """INVALID_SCHEMA message -> baseline subclass (ROADMAP section 3)."""
    if msg == "object requires 'additionalProperties: false'":
        return "missing additionalProperties: false"
    if msg == "object requires 'required'":
        return "missing required"
    if msg == "schema must declare 'type' or be a pure '$ref'":
        return "missing type"
    return "other"


def error_stage_of(msg):
    if msg.startswith("invalid JSON at byte") or msg.startswith(
        "JSON nesting too deep"
    ):
        return "parse"
    return "compile"


def dataset_of_maskbench(fname):
    stem = fname[:-5] if fname.endswith(".json") else fname
    if "---" in stem:
        return stem.split("---", 1)[0]
    if stem.startswith("BFCL_"):
        return "BFCL"
    if stem.startswith("JME_"):
        return "JME"
    return stem


def iter_files(snapshot):
    """-> (dataset, file, path) in deterministic order."""
    data_dir = snapshot["data_dir"]
    if snapshot["layout"] == "raw":
        for dataset in sorted(os.listdir(data_dir)):
            dpath = os.path.join(data_dir, dataset)
            if not os.path.isdir(dpath):
                continue
            for fname in sorted(os.listdir(dpath)):
                if fname.endswith(".json"):
                    yield dataset, fname, os.path.join(dpath, fname)
    else:
        for fname in sorted(os.listdir(data_dir)):
            if fname.endswith(".json"):
                yield dataset_of_maskbench(fname), fname, os.path.join(data_dir, fname)


def corpus_sha256(snapshot):
    """Order-independent content hash: sha256 over sorted
    (relpath, sha256(file bytes)) pairs."""
    entries = []
    for dataset, fname, path in iter_files(snapshot):
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        entries.append((f"{dataset}/{fname}", digest))
    h = hashlib.sha256()
    for rel, digest in sorted(entries):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(digest.encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def load_schema_bytes(snapshot, path):
    """-> (schema_bytes, schema_doc, tests).

    Raw layout passes the file bytes through unchanged (duplicate keys are
    caught by the compiler itself); schema_doc stays None there and is
    parsed lazily in semantic mode. The MaskBench wrapper re-serializes
    the "schema" member in compact form and hands over the parsed schema
    and the "tests" instance list unchanged.
    """
    with open(path, "rb") as f:
        data = f.read()
    if snapshot["layout"] == "raw":
        return data, None, []
    doc = json.loads(data.decode("utf-8"))
    schema = doc.get("schema")
    tests = doc.get("tests") or []
    out = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return out, schema, tests


def hf_tokenizer_spec(name, revision):
    """Pinned HF tokenizer -> TokenizerSpec (offline unless HF_HUB_OFFLINE
    is set otherwise; raises when it cannot be loaded)."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import bolorgir as zc
    from transformers import AutoTokenizer

    hf_tok = AutoTokenizer.from_pretrained(name, revision=revision)
    bundle = zc.TokenizerBundle.from_hf(hf_tok)
    return TokenizerSpec(
        tokens=tuple(bundle.token_bytes(i) for i in range(bundle.vocab_size)),
        eos_ids=bundle.eos_ids,
        special_ids=bundle.special_ids,
    )


def report_path(path):
    """A local path as a report records it: relative to the repository
    root, or the bare file name outside it (reports carry no absolute
    local paths)."""
    if not path:
        return None
    rel = os.path.relpath(os.path.abspath(path), _ROOT)
    return os.path.basename(path) if rel.startswith("..") else rel


def make_tokenizer():
    """Pinned HF gpt2 -> TokenizerSpec; recorded synthetic fallback."""
    try:
        spec = hf_tokenizer_spec(TOKENIZER_NAME, TOKENIZER_REVISION)
        info = {
            "name": TOKENIZER_NAME,
            "revision": TOKENIZER_REVISION,
            "vocab_size": spec.vocab_size,
        }
        return spec, info
    except Exception as e:  # noqa: BLE001 - offline fallback, recorded
        tokens = tuple(bytes([b]) for b in range(256)) + (b"<eos>",)
        spec = TokenizerSpec(tokens=tokens, eos_ids=(256,), special_ids=())
        info = {
            "name": "synthetic-byte-fallback",
            "revision": "-",
            "vocab_size": len(tokens),
            "note": f"pinned HF tokenizer unavailable: {type(e).__name__}: {e}",
        }
        return spec, info


def make_context(spec):
    return blg.Context(
        spec,
        mode=blg.BLG_MODE_ADAPTIVE,
        memory_limit_bytes=LIMITS["memory_limit_bytes"],
        cache_limit_bytes=LIMITS["cache_limit_bytes"],
        session_limit_bytes=LIMITS["session_limit_bytes"],
        schema_limit_bytes=LIMITS["schema_limit_bytes"],
        max_depth=LIMITS["max_depth"],
        max_threads_per_state=LIMITS["max_threads_per_state"],
        work_limit_ops=LIMITS["work_limit_ops"],
    )


def compile_schema(ctx, data):
    """blg_compile with the full error struct (the bolorgir package drops
    the JSON pointer). -> (status, message, json_pointer, schema_offset)."""
    req = blg.ZgCompileRequest()
    req.struct_size = ctypes.sizeof(blg.ZgCompileRequest)
    req.kind = blg.BLG_CONSTRAINT_JSON_SCHEMA
    req.profile = PROFILE.encode("ascii")
    buf = (ctypes.c_uint8 * max(len(data), 1)).from_buffer_copy(data or b"\0")
    req.data = buf
    req.data_len = len(data)
    out = ctypes.c_void_p()
    err = blg.new_error()
    status = blg.LIB.blg_compile(
        ctx.handle, ctypes.byref(req), ctypes.byref(out), ctypes.byref(err)
    )
    if status == blg.BLG_OK:
        blg.grammar_release(out)
        return status, "", "", None
    message = blg.err_text(err)
    pointer = err.json_pointer.split(b"\0", 1)[0].decode("utf-8", "replace")
    return status, message, pointer, err.schema_offset


def engine_commit():
    """HEAD, with "+dirty" when tracked files differ from it (the library
    then need not be the HEAD build: meta.engine.library_sha256 names it;
    BLG_ENGINE_LABEL overrides this label)."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return head + ("+dirty" if dirty else "")
    except Exception:  # noqa: BLE001
        return "unknown"


# ---------------------------------------------------------------------------
# Semantic mode: session/generation/instance execution checks
# ---------------------------------------------------------------------------

def byte_tokenizer_spec():
    """Byte-level tokenizer, same as tests/oracle/run_oracle.py
    byte_tokenizer_spec (and tests/conftest.py make_byte_tokenizer): one
    token per byte (id 0..255), eos=256, pad=257. Feeding byte-by-byte
    exercises the engine independently of any real vocabulary."""
    tokens = tuple(bytes([i]) for i in range(256)) + (b"", b"")
    return TokenizerSpec(tokens=tokens, eos_ids=(256,), special_ids=(257,))


class ContextRecycler:
    """Owns the context of one tokenizer for the semantic checks.

    Engine finding (R5): mask/generation work retains memory in the
    Context that session.destroy() and grammar_release() do not reclaim
    (sessions without fill_mask do not grow it; growth scales with the
    number of generation steps). A long semantic run in one context
    therefore ends with every compile/session_create failing with
    RESOURCE_LIMIT - an artifact of accumulation, not of the schema
    being checked. get() recycles the context every
    BYTE_CTX_RECYCLE_EVERY checks; scan() additionally recycles and
    retries once when a check fails with RESOURCE_LIMIT, so only
    genuine per-schema failures are recorded.
    """

    def __init__(self, spec):
        self.spec = spec
        self.ctx = make_context(spec)
        self.since_recycle = 0
        self.recycles = 0
        self.destroy_failures = 0

    def get(self):
        if self.since_recycle >= BYTE_CTX_RECYCLE_EVERY:
            self.recycle_now()
        return self.ctx

    def recycle_now(self):
        self._checked_destroy()
        self.ctx = make_context(self.spec)
        self.since_recycle = 0
        self.recycles += 1

    def note_use(self):
        self.since_recycle += 1

    def destroy(self):
        self._checked_destroy()

    def _checked_destroy(self):
        # ctx.destroy() may fail (e.g. BUSY while a session is alive); an
        # unchecked failure would silently keep the old context alive and
        # defeat the recycling. Count and report instead of ignoring.
        status = self.ctx.destroy()
        if status != 0:
            self.destroy_failures += 1
            print(f"[ctx] destroy returned status {status} "
                  f"(failures: {self.destroy_failures})", file=sys.stderr,
                  flush=True)


def is_resource_limit_rec(srec):
    """True when a semantic record failed with RESOURCE_LIMIT at any
    stage - the signature of the accumulated-context artifact (see
    ContextRecycler), retried once on a fresh context by scan()."""
    if srec is None:
        return False
    if "RESOURCE_LIMIT" in (srec.get("compile_mismatch") or ""):
        return True
    if not srec.get("session_ok") and \
            "RESOURCE_LIMIT" in (srec.get("session_error") or ""):
        return True
    gen = srec.get("generation") or {}
    return "RESOURCE_LIMIT" in (
        f"{gen.get('status') or ''} {gen.get('detail') or ''}")


def load_value_serializer():
    """Load python/bolorgir/serializer.py standalone (pure stdlib; the
    oracle harness convention) -> module with serialize_value."""
    spec = importlib.util.spec_from_file_location(
        "blg_value_serializer", SERIALIZER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_jsonschema_validator(schema_doc):
    """-> (validator, error). validator answers is_valid(value) with
    True/False/None (None = the validator itself raised, e.g. an
    unresolvable $ref); error is the check_schema/validator_for failure
    that made the schema unusable for the validator (then validator is
    None and jsonschema comparisons are skipped, never counted against
    the engine)."""
    from jsonschema import validators as _validators

    try:
        cls = _validators.validator_for(schema_doc)
        cls.check_schema(schema_doc)
        validator = cls(schema_doc)
    except Exception as e:  # noqa: BLE001 - validator-side, recorded
        return None, f"{type(e).__name__}: {e}"

    def is_valid(value):
        try:
            return bool(validator.is_valid(value))
        except Exception:  # noqa: BLE001 - e.g. unresolvable $ref
            return None

    return is_valid, None


def feed_document(session, doc):
    """Feed doc byte-exactly: mask bit check + accept per byte, then
    can_end + finish (the driver of tests/oracle/run_oracle.py:637).
    -> (verdict, detail) with verdict in {"accept", "reject",
    "error:<STATUS>"}."""
    for i, b in enumerate(doc):
        status, words = session.fill_mask_words()
        if status != blg.BLG_OK:
            # DESIGN §1.9: an empty mask is reported as DEAD_END; the
            # document then has no completion - a reject, not an engine
            # error (a data-dependent doom can empty the mask
            # mid-document under spec-v1).
            if status == blg.BLG_ERR_DEAD_END:
                return "reject", f"fill_mask dead end at byte {i}"
            return (f"error:{blg.STATUS_NAMES.get(status, status)}",
                    f"fill_mask at byte {i}")
        if not (words[b // 32] >> (b % 32)) & 1:
            return "reject", f"byte {i} (0x{b:02x}) not in mask"
        status = session.accept(b)
        if status == blg.BLG_OK:
            continue
        name = blg.STATUS_NAMES.get(status, f"status={status}")
        if status in (blg.BLG_ERR_INVALID_TOKEN, blg.BLG_ERR_DEAD_END):
            return "reject", f"accept at byte {i}: {name}"
        return f"error:{name}", f"accept at byte {i}"
    if not session.can_end():
        return "reject", "can_end false after full document"
    status = session.finish()
    if status == blg.BLG_OK:
        return "accept", ""
    name = blg.STATUS_NAMES.get(status, f"status={status}")
    if status in (blg.BLG_ERR_INVALID_TOKEN, blg.BLG_ERR_DEAD_END):
        return "reject", f"finish: {name}"
    return f"error:{name}", "finish"


def collect_key_candidates(schema_doc, max_nodes=10000):
    """Ordered unique list of object key candidates of a schema: the
    required keys of every object node in properties declaration order
    (first appearance across the merged allOf branches in document
    order, like the serializer's section 4.3 declared order), with
    required keys not declared in any properties appended. Local $refs
    are followed (cycle-capped like the serializer).

    The spec-v1 byte language fixes the declared key order, so the mask
    refuses to close a key while an earlier-declared required key is
    still pending; a byte-greedy driver over an open object would never
    spell the required keys in the right order by chance. Candidates are
    only hints - every byte still goes through the engine mask, and a
    candidate that the mask refuses mid-key is dropped for the plain
    greedy string."""
    keys = []
    seen = set()
    budget = [max_nodes]

    def add(k):
        if isinstance(k, str) and k not in seen:
            seen.add(k)
            keys.append(k)

    def resolve(s, root):
        for _ in range(64):
            if not isinstance(s, dict):
                return s
            ref = s.get("$ref")
            if not isinstance(ref, str):
                return s
            if ref == "#":
                s = root
            elif ref.startswith("#/"):
                node = root
                for part in ref[2:].split("/"):
                    part = part.replace("~1", "/").replace("~0", "~")
                    if not isinstance(node, dict) or part not in node:
                        return None
                    node = node[part]
                s = node
            else:
                return None
        return None

    def add_declared(s, root):
        """Required keys of the (allOf-merged) object node, in declared
        properties order; required keys declared nowhere, in required
        order. Optional declared keys are skipped: the byte language
        allows closing a key only when no earlier-declared REQUIRED key
        is pending, so the required-in-declared-order sequence is always
        emittable."""
        declared = []
        required = []

        def merged(sd):
            props = sd.get("properties")
            if isinstance(props, dict):
                declared.extend(props)
            req = sd.get("required")
            if isinstance(req, list):
                required.extend(k for k in req if isinstance(k, str))
            allof = sd.get("allOf")
            if isinstance(allof, list):
                for sub in allof:
                    sub = resolve(sub, root)
                    if isinstance(sub, dict):
                        merged(sub)

        merged(s)
        req_set = set(required)
        decl_set = set(declared)
        for k in declared:
            if k in req_set:
                add(k)
        for k in required:
            if k not in decl_set:
                add(k)

    def walk(s, root, depth):
        if depth > 64 or budget[0] <= 0:
            return
        budget[0] -= 1
        s = resolve(s, root)
        if not isinstance(s, dict):
            return
        add_declared(s, root)
        req = s.get("required")
        if isinstance(req, list):
            for k in req:
                add(k)
        for kw in ("properties", "patternProperties", "$defs", "definitions",
                   "dependentSchemas"):
            sub = s.get(kw)
            if isinstance(sub, dict):
                for v in sub.values():
                    walk(v, root, depth + 1)
        for kw in ("items", "additionalProperties", "contains",
                   "additionalItems", "unevaluatedProperties",
                   "unevaluatedItems", "then", "else", "if", "not"):
            sub = s.get(kw)
            if isinstance(sub, (dict, bool)):
                walk(sub, root, depth + 1)
        for kw in ("allOf", "anyOf", "oneOf", "prefixItems"):
            sub = s.get(kw)
            if isinstance(sub, list):
                for v in sub:
                    walk(v, root, depth + 1)

    walk(schema_doc, schema_doc, 0)
    return keys


def pattern_literal(pattern):
    """Best-effort literal approximation of an ECMA-262-subset pattern
    source: a plain string that a pattern like "\\d{4}-\\d{2}" ("0000-00")
    usually matches itself. Escapes map to representatives (\d -> "0",
    \w -> "a", \s -> " ", \. -> "."), classes take their first member
    ([0-9] -> "0", [a-z] -> "a"), {n}/{n,m} repeat the previous atom,
    '+'/'?' keep it, '*' drops it, anchors vanish, '|' keeps the first
    alternative. Only a generation hint: the engine mask validates every
    byte, and a wrong guess is dropped mid-string for the next
    candidate. -> str, or None when nothing usable remains."""
    out = []

    def class_member(p, i):
        # p[i] == '[' -> (representative char, index past ']')
        j = i + 1
        negated = j < len(p) and p[j] == "^"
        if negated:
            j += 1
        content = []
        while j < len(p) and p[j] != "]":
            if p[j] == "\\" and j + 1 < len(p):
                content.append(escape_value(p[j + 1]))
                j += 2
            else:
                content.append(p[j])
                j += 1
        if negated:
            return "a", j + 1
        digits = [c for c in content if c and c.isdigit()]
        if digits:
            return digits[0], j + 1
        letters = [c for c in content if c and c.isalpha()]
        if letters:
            return letters[0], j + 1
        for c in content:
            if c:
                return c, j + 1
        return "a", j + 1

    def escape_value(c):
        return {"d": "0", "D": "0", "w": "a", "W": "a", "s": " ",
                "S": " ", "n": "", "r": "", "t": "", "b": ""}.get(c, c)

    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            v = escape_value(pattern[i + 1])
            if v:
                out.append(v)
            i += 2
        elif c == "[":
            v, i = class_member(pattern, i)
            out.append(v)
        elif c in "(^$)":
            i += 1
        elif c == "|":
            break  # first alternative only
        elif c == "*":
            if out:
                out.pop()
            i += 1
        elif c in "+?":
            i += 1  # the atom is already emitted once
        elif c == ".":
            out.append("a")
            i += 1
        elif c == "{":
            m = re.match(r"\{(\d+)(?:,(\d*))?\}", pattern[i:])
            if m:
                n = int(m.group(1))
                if out and n > 1:
                    out.append(out[-1] * (n - 1))
                i += m.end()
            else:
                i += 1
        else:
            out.append(c)
            i += 1
    text = "".join(out)
    return text if text else None


def collect_string_candidates(schema_doc, max_nodes=10000):
    """Ordered unique list of string-content candidates (raw UTF-8 bytes,
    no quoting): literal approximations of the "pattern" sources
    (pattern_literal) and the string enum/const values of the schema,
    then the generic "a". A byte-greedy driver cannot guess the content a
    pattern DFA needs (pattern "wifi" never admits the closing quote for
    a space-filled string); candidates are only hints that the engine
    mask validates byte by byte - a candidate the mask refuses
    mid-string is dropped for the next one."""
    out = []
    seen = set()
    budget = [max_nodes]

    def add(text):
        if isinstance(text, str) and text and text not in seen:
            seen.add(text)
            out.append(text.encode("utf-8")[:256])

    def walk(s, depth):
        if depth > 64 or budget[0] <= 0 or len(out) >= 64:
            return
        budget[0] -= 1
        if isinstance(s, dict):
            pattern = s.get("pattern")
            if isinstance(pattern, str):
                add(pattern_literal(pattern))
            const = s.get("const")
            if isinstance(const, str):
                add(const)
            enum = s.get("enum")
            if isinstance(enum, list):
                for v in enum:
                    if isinstance(v, str):
                        add(v)
            for v in s.values():
                walk(v, depth + 1)
        elif isinstance(s, list):
            for v in s:
                walk(v, depth + 1)

    walk(schema_doc, 0)
    if "a" not in seen:
        out.append(b"a")
    return out


def estimate_min_document_size(schema_doc, cap=10_000_000):
    """Lower bound (bytes) of the smallest document of a schema:
    object = braces + required members, array = brackets + minItems
    items, string = quotes + minLength, scalar ~ 1; anyOf/oneOf take the
    cheapest branch, allOf the most expensive, local $refs are followed
    with a cycle cap. Only a skip heuristic for the generator
    (underestimates are the norm), never a verdict."""
    def resolve(s, root, depth):
        for _ in range(8):
            if not isinstance(s, dict) or depth > 32:
                return s
            ref = s.get("$ref")
            if not isinstance(ref, str):
                return s
            if ref == "#":
                return root
            if not ref.startswith("#/"):
                return None
            node = root
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(node, dict) or part not in node:
                    return None
                node = node[part]
            s = node
            depth += 1
        return None

    def est(s, root, depth, visiting, memo):
        if depth > 32:
            return 1
        s = resolve(s, root, 0)
        if not isinstance(s, dict):
            return 1
        key = id(s)
        if key in visiting:
            return 1  # recursive $ref: cut the cycle at the bottom
        cached = memo.get(key)
        if cached is not None:
            return cached
        visiting.add(key)
        r = _est_node(s, root, depth, visiting, memo)
        visiting.discard(key)
        memo[key] = r
        return r

    def _est_node(s, root, depth, visiting, memo):
        for kw in ("anyOf", "oneOf"):
            sub = s.get(kw)
            if isinstance(sub, list) and sub:
                return min(est(b, root, depth + 1, visiting, memo) for b in sub)
        sub = s.get("allOf")
        if isinstance(sub, list) and sub:
            return max(est(b, root, depth + 1, visiting, memo) for b in sub)
        t = s.get("type")
        if t == "object" or "properties" in s or "required" in s:
            props = s.get("properties")
            props = props if isinstance(props, dict) else {}
            req = s.get("required")
            req = [k for k in req if isinstance(k, str)] \
                if isinstance(req, list) else []
            total = 2
            for k in req:
                sub = props.get(k)
                total += len(k) + 3 + (est(sub, root, depth + 1, visiting, memo)
                                       if sub is not None else 1)
            return total
        if t == "array" or "items" in s:
            mn = s.get("minItems")
            mn = mn if isinstance(mn, int) and not isinstance(mn, bool) else 0
            item = s.get("items")
            per = (est(item, root, depth + 1, visiting, memo)
                   if isinstance(item, (dict, bool)) else 1)
            return 2 + mn * (per + 1)
        if t == "string" or "minLength" in s:
            mn = s.get("minLength")
            mn = mn if isinstance(mn, int) and not isinstance(mn, bool) else 0
            return 2 + mn
        return 1

    return min(est(schema_doc, schema_doc, 0, set(), {}), cap)


def start_cancel_watchdog(ctx_handle, seconds):
    """Cooperative-cancel backstop for a single engine call that exceeds
    the time budget on its own (a mask computation on a pathological
    grammar can take minutes, so an in-driver time check can never
    fire). Registers a cancel flag on the context
    (blg_cancel_flag_set); a daemon thread raises it after `seconds`.
    Returns done(): resets the flag and DEREGISTERS it (the engine holds
    the pointer, so the buffer must not be freed while registered).
    done.fired() tells whether the watchdog raised the flag.
    seconds <= 0 disables the watchdog."""
    if not seconds or seconds <= 0:
        def noop():
            return None
        noop.fired = lambda: False
        return noop
    cancel = ctypes.c_uint8(0)
    blg.LIB.blg_cancel_flag_set(ctx_handle, ctypes.byref(cancel))
    stop = threading.Event()
    state = {"fired": False}

    def watch():
        if not stop.wait(seconds):
            state["fired"] = True
            cancel.value = 1

    t = threading.Thread(target=watch, daemon=True)
    t.start()

    def done():
        stop.set()
        cancel.value = 0
        blg.LIB.blg_cancel_flag_set(ctx_handle, None)

    done.fired = lambda: state["fired"]
    return done


def generate_document(session, max_steps, key_candidates=(),
                      string_candidates=(), max_seconds=0.0, byte_ids=None,
                      where=None):
    """Completion-biased greedy generation over single-byte tokens.

    byte_ids maps a byte to the token id that spells exactly that byte
    (None: the byte tokenizer, token id = byte value); a vocabulary with
    every single byte as a token (gpt2, Llama 3) runs the same policy.
    where (a dict) receives the stage ("fill_mask", "accept", "finish"),
    the step and, for "budget", the exhausted limit ("step" or "time").

    Per step: fill the mask; can_end -> finish (a complete document);
    otherwise pick one allowed byte. The driver tracks the JSON context
    of its own output (object/array frames, string state, scalar runs):

    - inside a string the closing quote is preferred once allowed
      (minLength/pattern reached); at an object key position a schema
      "required" candidate key (collect_key_candidates) is spelled byte by
      byte while the mask allows it - an open object otherwise never
      terminates (the greedy driver would emit dynamic keys forever and
      never hit the required one);
    - outside a string the structural closers '}' and ']' are preferred
      (so open objects and arrays terminate), then '"', then the rest
      ascending.

    After GENERATION_RUN_GUARD identical byte choices in a row the byte
    is excluded once (pattern cycles like "a*b" would otherwise spin
    forever). A DEAD_END mask mid-document (a spec-v1 data-dependent
    doom) is its own outcome, not an error. ->
    (status, doc, detail) with status in {"ok", "dead_end", "budget",
    "error:<STATUS>"}."""
    candidates = [canonical_string(k)[1:] for k in key_candidates]
    doc = bytearray()
    stack = []  # {"kind": "obj"|"arr", "phase": ..., "keys": set()}
    in_string = False
    string_is_key = False
    escaped = False
    key_bytes = bytearray()   # raw spelling of the current key
    active = None             # remaining bytes of the active key candidate
    active_key = None
    content_queue = []        # string-content candidates not tried yet
    active_content = None     # remaining bytes of the active one
    in_scalar = False
    last_tok = None
    run_len = 0
    ids = byte_ids if byte_ids is not None else range(256)
    words = None

    def allowed_byte(b):
        t = ids[b]
        return t is not None and (words[t // 32] >> (t % 32)) & 1

    def out(status, detail, stage, step, limit=None):
        if where is not None:
            where.update(stage=stage, step=step, limit=limit)
        return status, bytes(doc), detail

    def value_done():
        # the top frame's pending value (or the whole document) completed
        if stack:
            stack[-1]["phase"] = "sep"

    t_start = time.time()
    for step in range(max_steps):
        if max_seconds and step and step % 16 == 0 \
                and time.time() - t_start > max_seconds:
            return out("budget",
                       f"time budget {max_seconds:.0f}s exhausted at step {step}",
                       "fill_mask", step, "time")
        status, words = session.fill_mask_words()
        if status != blg.BLG_OK:
            if status == blg.BLG_ERR_DEAD_END:
                return out("dead_end", f"fill_mask dead end at step {step}",
                           "fill_mask", step)
            if status == blg.BLG_ERR_CANCELLED:
                return out("budget",
                           "mask computation cancelled after exceeding the "
                           f"time budget at step {step}",
                           "fill_mask", step, "time")
            return out(f"error:{blg.STATUS_NAMES.get(status, status)}",
                       f"fill_mask at step {step}", "fill_mask", step)
        if session.can_end():
            status = session.finish()
            if status == blg.BLG_OK:
                return out("ok", "", "finish", step)
            return out(f"error:{blg.STATUS_NAMES.get(status, status)}",
                       "finish", "finish", step)
        allowed = [b for b in range(256) if allowed_byte(b)]
        if not allowed:
            if byte_ids is not None and any(words):
                # On a vocabulary with every single byte as a token, a
                # live multi-byte token implies its first byte is live.
                return out("error:MASK_NO_SINGLE_BYTE",
                           f"only multi-byte tokens allowed at step {step}",
                           "fill_mask", step)
            return out("dead_end", f"empty mask at step {step}",
                       "fill_mask", step)

        def rank(t):
            if in_string:
                return (0, 0) if t == 0x22 else (1, t)
            if t in (0x7D, 0x5D):  # '}' ']'
                return (0, t)
            if t == 0x22:  # '"' opens a string
                return (1, 0)
            return (2, t)

        allowed.sort(key=rank)
        tok = None
        if in_string and string_is_key and active:
            if allowed_byte(active[0]):
                tok = active[0]
                active = active[1:]
            else:
                active = None  # the mask refuses the candidate: greedy rest
        if (tok is None and in_string
                and not allowed_byte(0x22)):
            # the closing quote is refused: the string needs content the
            # greedy fill cannot guess (pattern/minLength) - spell the
            # schema-derived content candidates while the mask allows
            while True:
                if active_content is None:
                    if not content_queue:
                        break
                    active_content = content_queue.pop(0)
                if allowed_byte(active_content[0]):
                    tok = active_content[0]
                    active_content = active_content[1:]
                    if not active_content:
                        active_content = None
                    break
                active_content = None  # refused: try the next candidate
        if tok is None:
            tok = allowed[0]
            if (tok == last_tok and run_len >= GENERATION_RUN_GUARD
                    and len(allowed) > 1):
                tok = allowed[1]  # break a repeat cycle
        run_len = run_len + 1 if tok == last_tok else 1
        last_tok = tok
        status = session.accept(ids[tok])
        if status != blg.BLG_OK:
            return out(f"error:{blg.STATUS_NAMES.get(status, status)}",
                       f"accept at step {step}", "accept", step)
        doc.append(tok)

        # the token came from the engine mask, so it is valid by
        # construction; track the JSON context of the output
        if in_string:
            if escaped:
                escaped = False
            elif tok == 0x5C:
                escaped = True
            elif tok == 0x22:  # closing quote
                in_string = False
                if string_is_key:
                    if active_key is not None:
                        stack[-1]["keys"].add(active_key)
                        active_key = None
                    else:
                        try:
                            stack[-1]["keys"].add(
                                json.loads(b'"' + bytes(key_bytes) + b'"'))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            pass
                    stack[-1]["phase"] = "colon"
                else:
                    value_done()
            elif string_is_key:
                key_bytes.append(tok)
            continue
        if in_scalar:
            if tok not in (0x2C, 0x7D, 0x5D):  # not a delimiter: scalar goes on
                continue
            in_scalar = False
            value_done()
            # fall through: the delimiter byte is structural
        if tok == 0x7B:  # '{'
            stack.append({"kind": "obj", "phase": "key", "keys": set()})
        elif tok == 0x5B:  # '['
            stack.append({"kind": "arr", "phase": "value", "keys": set()})
        elif tok == 0x22:  # '"' opens a string
            in_string = True
            escaped = False
            string_is_key = (bool(stack) and stack[-1]["kind"] == "obj"
                             and stack[-1]["phase"] == "key")
            key_bytes = bytearray()
            active = None
            active_key = None
            content_queue = list(string_candidates)
            active_content = None
            if string_is_key:
                for k, cand in zip(key_candidates, candidates):
                    if k not in stack[-1]["keys"]:
                        active = cand
                        active_key = k
                        break
        elif tok == 0x7D:  # '}'
            if stack and stack[-1]["kind"] == "obj":
                stack.pop()
                value_done()
        elif tok == 0x5D:  # ']'
            if stack and stack[-1]["kind"] == "arr":
                stack.pop()
                value_done()
        elif tok == 0x3A:  # ':'
            stack[-1]["phase"] = "value"
        elif tok == 0x2C:  # ','
            stack[-1]["phase"] = (
                "key" if stack[-1]["kind"] == "obj" else "value")
        else:
            in_scalar = True  # number / true / false / null
    return out("budget", f"step budget {max_steps} exhausted", "fill_mask",
               max_steps, "step")


def semantic_check(byte_ctx, data, schema_doc, tests, max_steps, serializer,
                   max_seconds=0.0, beat=None):
    """Run the R5 execution checks for one compiled schema.

    -> per-file record:
    {compile_mismatch, session_ok, session_error,
     generation: {status, detail, bytes, json_parse, schema_valid},
     validator_error, instances: [{valid, outcome, detail, serializer,
     validator}]}

    generation.status is "ok" | "dead_end" | "budget" | "oversize" |
    "error:<STATUS>": "oversize" means the static minimum-document-size
    estimate (estimate_min_document_size) already exceeds the step
    budget, so no generation was attempted - a documented skip with a
    reason, counted in its own bucket, never as success or failure."""
    rec = {
        "compile_mismatch": None,
        "session_ok": False,
        "session_error": None,
        "generation": None,
        "validator_error": None,
        "instances": [],
    }
    try:
        grammar = blg.compile_schema(byte_ctx, data,
                                     profile=PROFILE.encode("ascii"))
    except blg.CoreFailure as e:
        rec["compile_mismatch"] = (
            f"{blg.STATUS_NAMES.get(e.status, e.status)}: {e}"
        )
        return rec
    try:
        try:
            session = blg.Session(byte_ctx, grammar)
        except blg.CoreFailure as e:
            rec["session_error"] = (
                f"{blg.STATUS_NAMES.get(e.status, e.status)}: {e}"
            )
            return rec
        rec["session_ok"] = True
        session.destroy()

        if schema_doc is None:
            # raw layout: parse for jsonschema/serializer use; duplicate
            # keys collapse here exactly like json.load (the compiler's
            # own parse is authoritative for the engine side).
            try:
                schema_doc = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                schema_doc = None
                rec["validator_error"] = f"schema JSON parse: {e}"
        validator = None
        if schema_doc is not None:
            validator, verr = make_jsonschema_validator(schema_doc)
            if verr is not None:
                rec["validator_error"] = verr

        req_keys = (collect_key_candidates(schema_doc)
                    if schema_doc is not None else ())
        str_cands = (collect_string_candidates(schema_doc)
                     if schema_doc is not None else ())
        min_size = (estimate_min_document_size(schema_doc)
                    if schema_doc is not None else 0)
        if min_size > max_steps:
            gen = {"status": "oversize",
                   "detail": f"estimated minimum document size {min_size} "
                             f"bytes > step budget {max_steps}; "
                             "generation not attempted",
                   "bytes": 0, "json_parse": None, "schema_valid": None,
                   "doc_preview": ""}
        else:
            gs = blg.Session(byte_ctx, grammar)
            # the in-driver cap covers many cheap steps; the watchdog
            # covers a single mask computation that is slow on its own
            # (grace over max_seconds: the kernel polls the flag
            # cooperatively, not at instruction granularity)
            wd = start_cancel_watchdog(
                byte_ctx.handle, max_seconds + 15.0 if max_seconds else 0.0)
            try:
                gstatus, doc, gdetail = generate_document(
                    gs, max_steps, req_keys, str_cands, max_seconds)
            finally:
                wd()
            gs.destroy()
            gen = {"status": gstatus, "detail": gdetail, "bytes": len(doc),
                   "json_parse": None, "schema_valid": None,
                   "doc_preview": doc[:120].decode("utf-8", "replace")}
        if gen["status"] == "ok":
            value = None
            try:
                value = json.loads(doc.decode("utf-8"))
                gen["json_parse"] = True
            except (UnicodeDecodeError, json.JSONDecodeError):
                gen["json_parse"] = False  # finish accepted non-JSON
            if gen["json_parse"] and validator is not None:
                gen["schema_valid"] = validator(value)
        rec["generation"] = gen

        for i, t in enumerate(tests):
            if beat is not None:
                beat(i)
            row = {"valid": bool(t.get("valid")), "outcome": None,
                   "detail": "", "serializer": "ok", "validator": None}
            payload, sstat = serializer.serialize_value(t.get("data"),
                                                        schema_doc)
            if not sstat.ok:
                row["serializer"] = "incompatible"
                row["outcome"] = "serialization_incompatible"
                row["detail"] = f"{sstat.reason} at {sstat.pointer or '/'}"
            else:
                s = blg.Session(byte_ctx, grammar)
                # same backstop as generation: an instance feed can hit a
                # mask computation that never returns (R5 finding:
                # Github_easy---o37786.json hangs in fill_mask at byte
                # 141); the cancel flag lands cooperatively and the feed
                # then ends as error:CANCELLED
                wd = start_cancel_watchdog(
                    byte_ctx.handle, max_seconds + 15.0 if max_seconds else 0.0)
                try:
                    verdict, detail = feed_document(s, payload)
                finally:
                    wd()
                s.destroy()
                row["outcome"] = verdict
                row["detail"] = detail
            if validator is not None:
                row["validator"] = validator(t.get("data"))
            rec["instances"].append(row)
        return rec
    finally:
        blg.grammar_release(grammar)


# ---------------------------------------------------------------------------
# Generatable mode: K passes per schema and tokenizer, outcome records
# ---------------------------------------------------------------------------

def _gen_mods():
    """The shared pass driver and outcome rules (tests/generation_passes.py,
    tests/semantic_outcomes.py); imported on first use so the compile-only
    scan does not need numpy or regress."""
    import generation_passes
    import semantic_outcomes
    return generation_passes, semantic_outcomes


class Progress:
    """Last engine step started by this process, written atomically to a
    file: after a crash the chunk driver reads which (file, tokenizer,
    stage) was in flight."""

    def __init__(self, path=None):
        self.path = path

    def mark(self, **kw):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(kw, f, ensure_ascii=False)
        os.replace(tmp, self.path)


PROGRESS = Progress()


class GenTokenizer:
    """One tokenizer of the generatable mode: its recycled context, the
    mask vocabulary and the single-byte token map of the pass-1 policy.
    spec is None when a pinned HF tokenizer cannot be loaded; every
    schema is then skipped for it with the reason recorded."""

    def __init__(self, key):
        gp, _so = _gen_mods()
        self.key = key
        self.spec = None
        self.recycler = None
        self.sample_every = 1
        pin = GEN_TOKENIZERS[key]
        if pin is None:
            self.spec = byte_tokenizer_spec()
            self.info = {"name": "byte-level synthetic (token id = byte "
                                 "value, eos=256, pad=257)", "revision": "-"}
        else:
            self.info = {"name": pin[0], "revision": pin[1]}
            try:
                self.spec = hf_tokenizer_spec(*pin)
            except Exception as e:  # noqa: BLE001 - recorded skip reason
                self.info["unavailable"] = f"{type(e).__name__}: {e}"
        if self.spec is not None:
            self.recycler = ContextRecycler(self.spec)
            self.vocab = gp.Vocab(self.spec)
            self.byte_ids = [self.vocab.by_bytes.get(bytes([b]))
                             for b in range(256)]
            self.info["vocab_size"] = self.spec.vocab_size
            self.info["single_byte_tokens"] = sum(
                t is not None for t in self.byte_ids)

    def destroy(self):
        if self.recycler is not None:
            self.recycler.destroy()


def in_sample(dataset, fname, every):
    """Deterministic 1-in-`every` schema sample (independent of chunking
    and file order)."""
    if every <= 1:
        return True
    h = hashlib.sha256(f"{dataset}/{fname}".encode("utf-8"))
    return int(h.hexdigest()[:12], 16) % every == 0


def pass_seed(dataset, fname, tok_key, k):
    h = hashlib.sha256(f"{GEN_SEED}|{dataset}/{fname}|{tok_key}|{k}".encode("utf-8"))
    return int(h.hexdigest()[:12], 16)


def policy_pass_record(gstatus, doc, where, validator, seed, fired):
    """generate_document result -> outcome record (the same table as
    generation_passes.run_pass)."""
    _gp, so = _gen_mods()
    stage = where.get("stage") or "fill_mask"
    step = where.get("step")
    plen = len(doc)
    if gstatus == "ok":
        json_ok, verdict, error = validator.validate(doc)
        outcome = so.validation_outcome(json_ok, verdict)
        code = None if outcome == "completed_valid" else (
            "NOT_JSON" if not json_ok else "REJECTED" if verdict is False else error)
        return so.make_record(outcome, "validate", code, step, plen, seed)
    if gstatus == "dead_end":
        return so.make_record("engine_defect", "fill_mask", "BLG_ERR_DEAD_END",
                              step, plen, seed)
    if gstatus == "budget":
        if where.get("limit") == "step":
            return so.make_record("incomplete:step_limit", stage, "STEP_LIMIT",
                                  step, plen, seed)
        return so.make_record("incomplete:time_limit", stage, "TIME_LIMIT",
                              step, plen, seed)
    name = gstatus.split(":", 1)[1]
    code = f"BLG_ERR_{name}" if name in blg.STATUS_NAMES.values() else name
    if code == "BLG_ERR_CANCELLED" and fired():
        return so.make_record("incomplete:time_limit", stage, "TIME_LIMIT",
                              step, plen, seed)
    return so.make_record(so.classify_engine_failure(stage, code), stage,
                          code, step, plen, seed)


def gen_schema_level(outcome, stage, code, detail=""):
    """Outcome decided before any pass (compile_check, skip reasons,
    a crash recorded by the chunk driver)."""
    return {"outcome": outcome, "stage": stage, "code": code,
            "detail": detail[:300], "incomplete": 0, "passes_run": 0,
            "counts": {}, "records": []}


def gen_summary(records):
    """Pass records of one schema and tokenizer -> aggregated result.
    stage/code/step come from the first pass with the aggregated
    outcome."""
    _gp, so = _gen_mods()
    outcomes = [r["outcome"] for r in records]
    agg = so.aggregate_schema_outcome(outcomes)
    if agg == "generatable":
        match = lambda o: o == "completed_valid"  # noqa: E731
    elif agg == "incomplete":
        match = so.is_incomplete
    elif agg == "skipped":
        match = so.is_skipped
    else:
        match = lambda o: o == agg  # noqa: E731
    first = next(r for r in records if match(r["outcome"]))
    counts = {}
    for o in outcomes:
        counts[o] = counts.get(o, 0) + 1
    return {"outcome": agg, "stage": first["stage"], "code": first["code"],
            "step": first["step"], "prefix_length": first["prefix_length"],
            "seed": first["seed"], "detail": "",
            "incomplete": so.incomplete_count(outcomes),
            "passes_run": sum(1 for o in outcomes if not so.is_skipped(o)),
            "counts": counts, "records": records}


def document_field(doc):
    """A pass document for the reports: its text, or {"base64": ...}
    when the bytes are not UTF-8."""
    try:
        return doc.decode("utf-8")
    except UnicodeDecodeError:
        return {"base64": base64.b64encode(doc).decode("ascii")}


def compile_for(gt, ctx, data, dataset, fname):
    """compile_check: the schema compiled again in the tokenizer's context
    -> (grammar, None), or (None, schema-level result). The main compile
    succeeded, so any other status is a mismatch."""
    gp, so = _gen_mods()
    PROGRESS.mark(dataset=dataset, file=fname, phase="generatable",
                  tokenizer=gt.key, stage="compile_check")
    try:
        return blg.compile_schema(ctx, data, profile=PROFILE.encode("ascii")), None
    except blg.CoreFailure as e:
        if e.status == blg.BLG_ERR_UNSUPPORTED_TOKENIZER:
            return None, gen_schema_level("skipped", None, "unsupported_tokenizer", str(e))
        code = gp.status_name(e.status)
        return None, gen_schema_level(so.classify_engine_failure("compile_check", code),
                                      "compile_check", code, str(e))


def pass_plan(schema_doc, validator):
    """What the passes of one schema share: the reference validator, the
    key and string candidates of the pass-1 policy, the hints of the
    random passes."""
    gp, _so = _gen_mods()
    known = schema_doc is not None
    return {"validator": validator,
            "req_keys": collect_key_candidates(schema_doc) if known else (),
            "str_cands": collect_string_candidates(schema_doc) if known else (),
            "hints": gp.schema_hints(schema_doc) if known else []}


def gen_pass(gt, ctx, grammar, k, seed, plan, opts):
    """Pass k of one schema on one tokenizer -> (outcome record, document
    bytes). The record of an invalid_document or validator_error pass
    carries the whole document."""
    gp, so = _gen_mods()
    max_seconds = opts["max_seconds"]
    wd = start_cancel_watchdog(ctx.handle, max_seconds + 15.0 if max_seconds else 0.0)
    try:
        if k == 1:
            try:
                session = blg.Session(ctx, grammar)
            except blg.CoreFailure as e:
                code = gp.status_name(e.status)
                return so.make_record(so.classify_engine_failure("session_create", code),
                                      "session_create", code, 0, 0, seed), b""
            where = {}
            try:
                gstatus, doc, _detail = generate_document(
                    session, opts["max_steps"], plan["req_keys"], plan["str_cands"],
                    max_seconds, byte_ids=gt.byte_ids, where=where)
            finally:
                session.destroy()
            rec = policy_pass_record(gstatus, doc, where, plan["validator"], seed, wd.fired)
        else:
            rec, doc = gp.run_pass(
                ctx, grammar, gt.vocab, random.Random(seed), seed=seed,
                validator=plan["validator"], policy="biased", hints=plan["hints"],
                max_steps=opts["max_steps"], max_seconds=max_seconds,
                cancelled=wd.fired)
    finally:
        wd()
    if rec["outcome"] in GEN_DOCUMENT_OUTCOMES:
        rec["document"] = document_field(doc)
    return rec, doc


def generatable_check(gt, data, schema_doc, validator, vreason, dataset, fname,
                      opts, crash=None):
    """K passes of one compiled schema on one tokenizer -> result dict
    (gen_summary / gen_schema_level), after compile_check."""
    _gp, so = _gen_mods()
    if gt.spec is None:
        return gen_schema_level("skipped", None, "tokenizer_unavailable",
                                gt.info.get("unavailable", ""))
    if crash is not None:
        return gen_schema_level("engine_defect", crash.get("stage"), "CRASH",
                                crash.get("reason", ""))
    ctx = gt.recycler.get()
    grammar, failure = compile_for(gt, ctx, data, dataset, fname)
    if failure is not None:
        return failure
    try:
        if validator is None:
            return gen_schema_level("skipped", None, "validator_unusable", vreason)
        min_size = estimate_min_document_size(schema_doc) if schema_doc is not None else 0
        if min_size > opts["max_steps"]:
            return gen_schema_level(
                "skipped", None, "oversize",
                f"estimated minimum document size {min_size} bytes > step "
                f"budget {opts['max_steps']}")
        plan = pass_plan(schema_doc, validator)
        records = []
        t0 = time.monotonic()
        for k in range(1, opts["passes"] + 1):
            seed = pass_seed(dataset, fname, gt.key, k)
            if (k > 1 and opts["schema_budget"]
                    and time.monotonic() - t0 > opts["schema_budget"]):
                records.append(so.make_record("skipped:schema_budget", seed=seed))
                continue
            PROGRESS.mark(dataset=dataset, file=fname, phase="generatable",
                          tokenizer=gt.key, stage=f"pass {k}")
            records.append(gen_pass(gt, ctx, grammar, k, seed, plan, opts)[0])
        return gen_summary(records)
    finally:
        blg.grammar_release(grammar)


REPLAY_FIELDS = ("outcome", "stage", "code", "step", "prefix_length")


def complete_check(gt, data, schema_doc, validator, dataset, fname, opts,
                   prev, prev_rows, crash=None):
    """--complete-from for one schema on one tokenizer: prev is the
    earlier result (report form), prev_rows its sidecar pass records.
    Runs the passes recorded as skipped:schema_budget and replays every
    invalid_document / validator_error pass without a document by its
    seed (time limit opts["replay_max_seconds"]) to keep its document.
    A replay must reproduce the recorded outcome, stage, code, step,
    prefix length and, where the report kept one, the document;
    otherwise the record stays without a document and the replay is
    listed under completion.replay_mismatches. A pass or replay that
    ends in engine_unavailable reruns the completion once on a fresh
    context (as generatable_all does); a replay that ends in it again
    runs alone in a child process (replay_alone). Every other record is
    kept. The
    completion counters of an earlier completion add up; a pass it
    could not reproduce and that is replayed again counts under
    rereplayed."""
    _gp, so = _gen_mods()
    if not prev_rows:
        res = {k: v for k, v in prev.items() if k != "failed_passes"}
        res["records"] = []
        return res
    if crash is not None:
        return gen_schema_level("engine_defect", crash.get("stage"), "CRASH",
                                crash.get("reason", ""))
    if gt.spec is None or validator is None:
        raise SystemExit(f"--complete-from: {dataset}/{fname} ({gt.key}) has pass "
                         "records, but its tokenizer or validator is unavailable now")
    records = []
    for k, row in enumerate(prev_rows, 1):
        r = dict(zip(PASS_FIELDS, row))
        r.setdefault("document", None)
        if r["seed"] != pass_seed(dataset, fname, gt.key, k):
            raise SystemExit(f"--complete-from: pass {k} of {dataset}/{fname} "
                             f"({gt.key}) has another seed")
        records.append(r)
    kept = {p["seed"]: p.get("document") for p in prev.get("failed_passes") or []
            if p.get("document") is not None}
    todo = [k for k, r in enumerate(records, 1)
            if r["outcome"] == "skipped:schema_budget"
            or (r["outcome"] in GEN_DOCUMENT_OUTCOMES and r["document"] is None)]
    replay_opts = dict(opts, max_seconds=opts.get("replay_max_seconds", opts["max_seconds"]))
    info = {"passes_run": 0, "replayed": 0, "rereplayed": 0, "replayed_alone": 0,
            "replay_mismatches": [], "seconds": 0.0}
    t0 = time.monotonic()
    plan = pass_plan(schema_doc, validator) if todo else None
    new = {}
    for attempt in range(2 if todo else 0):
        ctx = gt.recycler.get()
        grammar, failure = compile_for(gt, ctx, data, dataset, fname)
        if failure is not None:
            if attempt == 0 and failure["outcome"] == "engine_unavailable":
                gt.recycler.recycle_now()
                continue
            failure["detail"] = ("--complete-from: the compile_check that succeeded "
                                 "in the completed run failed: " + failure["detail"])[:300]
            return failure
        try:
            for k in todo:
                PROGRESS.mark(dataset=dataset, file=fname, phase="generatable",
                              tokenizer=gt.key, stage=f"pass {k}")
                skipped = records[k - 1]["outcome"] == "skipped:schema_budget"
                new[k] = gen_pass(gt, ctx, grammar, k, records[k - 1]["seed"], plan,
                                  opts if skipped else replay_opts)
        finally:
            blg.grammar_release(grammar)
        if attempt == 0 and any(new[k][0]["outcome"] == "engine_unavailable" for k in todo):
            gt.recycler.recycle_now()
            info["retried_on_fresh_context"] = True
            continue
        break
    for k in todo:
        if (records[k - 1]["outcome"] in GEN_DOCUMENT_OUTCOMES
                and new[k][0]["outcome"] == "engine_unavailable"):
            new[k] = replay_alone(gt, data, dataset, fname, k, records[k - 1]["seed"],
                                  replay_opts)
            info["replayed_alone"] += 1
    for k in todo:
        old = records[k - 1]
        rec, doc = new[k]
        if old["outcome"] == "skipped:schema_budget":
            records[k - 1] = rec
            info["passes_run"] += 1
            continue
        info["replayed"] += 1
        same = all(old[f] == rec[f] for f in REPLAY_FIELDS)
        if same and old["seed"] in kept:
            got = rec.get("document")
            shown = (got.encode("utf-8")[:GEN_DOCUMENT_CAP].decode("utf-8", "replace")
                     if isinstance(got, str) else got)
            same = kept[old["seed"]] in (shown, doc[:GEN_DOCUMENT_CAP].decode("utf-8", "replace"))
        if same:
            old["document"] = rec["document"]
        else:
            info["replay_mismatches"].append({
                "pass": k, "replay": {f: rec[f] for f in REPLAY_FIELDS},
                "document": rec.get("document")})
    info["seconds"] = round(time.monotonic() - t0, 3)
    earlier = prev.get("completion")
    if earlier:
        again = {m["pass"] for m in earlier["replay_mismatches"]}
        n_again = sum(1 for k in todo if k in again)
        info["passes_run"] += earlier["passes_run"]
        info["replayed"] += earlier["replayed"] - n_again
        info["rereplayed"] += earlier.get("rereplayed", 0) + n_again
        info["replayed_alone"] += earlier.get("replayed_alone", 0)
        info["seconds"] = round(info["seconds"] + earlier["seconds"], 3)
        if earlier.get("retried_on_fresh_context"):
            info["retried_on_fresh_context"] = True
    out = gen_summary(records)
    for key in ("retried_on_fresh_context", "seconds"):
        if key in prev:
            out[key] = prev[key]
    out["completion"] = info
    return out


def replay_alone(gt, data, dataset, fname, k, seed, opts):
    """Pass k of one schema replayed in a child process (--replay-pass)
    -> (outcome record, document bytes). Near the context memory limit
    the outcome of a pass depends on what the process ran before it; a
    child process starts from a fresh engine state."""
    _gp, so = _gen_mods()
    payload = {"profile": PROFILE, "tokenizer": gt.key, "dataset": dataset, "file": fname,
               "pass": k, "seed": seed, "opts": opts,
               "schema": base64.b64encode(data).decode("ascii")}
    try:
        proc = subprocess.run([sys.executable, os.path.abspath(__file__), "--replay-pass"],
                              input=json.dumps(payload), capture_output=True, text=True,
                              timeout=opts["max_seconds"] + 600)
        out = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.returncode == 0 else None
    except (subprocess.TimeoutExpired, IndexError, json.JSONDecodeError):
        out = None
    if out is None:
        return so.make_record("engine_defect", "replay_alone", "CRASH", 0, 0, seed), b""
    rec, doc = out["record"], base64.b64decode(out["document"])
    if rec["outcome"] in GEN_DOCUMENT_OUTCOMES:
        rec["document"] = document_field(doc)
    return rec, doc


def replay_pass_main():
    """--replay-pass: the child of replay_alone; the payload on stdin,
    the record and the document on stdout."""
    global PROFILE
    p = json.loads(sys.stdin.read())
    PROFILE = p["profile"]
    gp, so = _gen_mods()
    data = base64.b64decode(p["schema"])
    validator, _vreason = gp.make_reference_validator(data)
    gt = GenTokenizer(p["tokenizer"])
    try:
        ctx = gt.recycler.get()
        grammar, failure = compile_for(gt, ctx, data, p["dataset"], p["file"])
        if failure is not None:
            rec, doc = so.make_record(failure["outcome"], failure["stage"], failure["code"],
                                      0, 0, p["seed"]), b""
        else:
            try:
                rec, doc = gen_pass(gt, ctx, grammar, p["pass"], p["seed"],
                                    pass_plan(parsed_schema(data, None), validator), p["opts"])
            finally:
                blg.grammar_release(grammar)
    finally:
        gt.destroy()
    print(json.dumps({"record": {f: rec.get(f) for f in REPLAY_FIELDS + ("seed",)},
                      "document": base64.b64encode(doc).decode("ascii")}))


def parsed_schema(data, schema_doc):
    """The schema document the passes plan with: schema_doc, or data
    parsed when the snapshot loader gives none (the repo snapshot)."""
    if schema_doc is not None:
        return schema_doc
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def generatable_all(gen_toks, data, schema_doc, dataset, fname, opts,
                    crash_list=None):
    """All tokenizers for one compiled schema -> {tokenizer: result}.
    An engine_unavailable result is retried once on a fresh context (the
    accumulated-context artifact of ContextRecycler); only a result that
    reproduces there is kept."""
    gp, _so = _gen_mods()
    schema_doc = parsed_schema(data, schema_doc)
    validator, vreason = gp.make_reference_validator(data)
    out = {}
    for gt in gen_toks:
        if not in_sample(dataset, fname, gt.sample_every):
            out[gt.key] = gen_schema_level("skipped", None, "not_sampled")
            continue
        crash = (crash_list or {}).get(f"{fname}\t{gt.key}")
        t0 = time.monotonic()
        res = generatable_check(gt, data, schema_doc, validator, vreason,
                                dataset, fname, opts, crash)
        if res["outcome"] == "engine_unavailable" and gt.recycler is not None:
            gt.recycler.recycle_now()
            res = generatable_check(gt, data, schema_doc, validator, vreason,
                                    dataset, fname, opts, crash)
            res["retried_on_fresh_context"] = True
        if gt.recycler is not None:
            gt.recycler.note_use()
        res["seconds"] = round(time.monotonic() - t0, 3)
        out[gt.key] = res
    return out


def generatable_complete(gen_toks, data, schema_doc, dataset, fname, opts,
                         prev_gen, prev_rows, crash_list=None):
    """--complete-from for one compiled schema -> {tokenizer: result}
    (complete_check per tokenizer of the earlier result)."""
    gp, _so = _gen_mods()
    schema_doc = parsed_schema(data, schema_doc)
    validator, _vreason = gp.make_reference_validator(data)
    out = {}
    for gt in gen_toks:
        prev = prev_gen.get(gt.key)
        if prev is None:
            raise SystemExit(f"--complete-from: {dataset}/{fname} has no {gt.key} result")
        crash = (crash_list or {}).get(f"{fname}\t{gt.key}")
        out[gt.key] = complete_check(gt, data, schema_doc, validator, dataset, fname,
                                     opts, prev, prev_rows.get(gt.key) or [], crash)
        if gt.recycler is not None:
            gt.recycler.note_use()
    return out


def gen_split_records(gen):
    """Per-tokenizer results -> (report form, sidecar rows). The report
    keeps the aggregate and up to GEN_RECORDS_IN_REPORT non-success pass
    records, most severe first, with documents cut at GEN_DOCUMENT_CAP;
    the sidecar keeps every record as PASS_FIELDS, the whole document of
    an invalid_document / validator_error pass included."""
    report, rows = {}, {}
    for tok, res in gen.items():
        recs = res.get("records") or []
        r = {k: v for k, v in res.items() if k != "records"}
        order = ("invalid_document", "engine_defect", "validator_error",
                 "engine_unavailable")
        failed = [x for x in recs if x["outcome"] != "completed_valid"]
        failed.sort(key=lambda x: order.index(x["outcome"])
                    if x["outcome"] in order else len(order))
        shown = []
        for x in failed[:GEN_RECORDS_IN_REPORT]:
            x = dict(x)
            d = x.get("document")
            if isinstance(d, str) and len(d.encode("utf-8")) > GEN_DOCUMENT_CAP:
                x["document"] = d.encode("utf-8")[:GEN_DOCUMENT_CAP].decode("utf-8", "replace")
                x["document_truncated"] = True
            elif d is None:
                x.pop("document", None)
            shown.append(x)
        r["failed_passes"] = shown
        report[tok] = r
        rows[tok] = [[x.get(f) for f in PASS_FIELDS] for x in recs]
    return report, rows


# Semantic counters of the run itself (context recycling), not of the
# per-file records: summed over chunks, carried over by --complete-from.
SEMANTIC_RUN_COUNTERS = ("byte_ctx_recycles", "byte_ctx_destroy_failures")


def new_sem_aggregate():
    """Empty semantic aggregate (shared by scan() and --merge-chunks so
    the two aggregation paths cannot drift apart)."""
    return {
        "schemas_compiled": 0,
        "compile_mismatch": 0,
        "session_ok": 0,
        "session_failed": 0,
        "generation": {"ok": 0, "dead_end": 0, "budget": 0,
                       "oversize": 0, "error": 0},
        "oversize_files": [],
        "generated_json_parse_failed": 0,
        "generated_schema_valid": 0,
        "generated_schema_invalid": 0,
        "generated_validator_error": 0,
        "validator_schema_error": 0,
        "valid_instances": {
            "total": 0, "accepted": 0, "rejected": 0,
            "serialization_incompatible": 0, "engine_error": 0,
            "validator_invalid": 0,
        },
        "invalid_instances": {
            "total": 0, "rejected": 0, "accepted": 0,
            "accepted_validator_invalid": 0,
            "accepted_validator_valid": 0,
            "serialization_incompatible": 0, "engine_error": 0,
        },
        "failure_kinds": {},
        "failures": {},
        "engine_fault_skips": 0,
        "engine_fault_files": [],
        "byte_ctx_recycles": 0,
    }


def record_failure(sem, kind, dataset, fname, detail):
    """Exact per-kind counts; stored examples capped at
    FAILURE_EXAMPLES_CAP (the markdown lists the first ones)."""
    sem["failure_kinds"][kind] = sem["failure_kinds"].get(kind, 0) + 1
    lst = sem["failures"].setdefault(kind, [])
    if len(lst) < FAILURE_EXAMPLES_CAP:
        lst.append({"dataset": dataset, "file": fname,
                    "detail": (detail or "")[:300]})


def sem_aggregate_update(sem, dataset, fname, rec):
    """Replay the semantic aggregation for one per-file record (shared
    by scan() and --merge-chunks). rec["semantic"] is the semantic_check
    record, None (harness error / fault skip - see the branches), or a
    {"engine_fault_skip": reason} marker."""
    if rec["error_class"] != "compiled":
        return
    sem["schemas_compiled"] += 1
    srec = rec.get("semantic")
    if srec is None:
        return  # harness_error_semantic was already counted by the scan
    if "engine_fault_skip" in srec:
        # the engine dies unrecoverably on this file (e.g. a Zig panic
        # in a pathological mask computation), so the in-process
        # semantic phase cannot run; compile coverage above still
        # counts it and the skip is its own bucket
        sem["engine_fault_skips"] += 1
        sem["engine_fault_files"].append(
            {"dataset": dataset, "file": fname,
             "reason": srec["engine_fault_skip"]})
        return
    if srec["compile_mismatch"] is not None:
        sem["compile_mismatch"] += 1
        record_failure(sem, "compile_mismatch", dataset, fname,
                       srec["compile_mismatch"])
        return
    if not srec["session_ok"]:
        sem["session_failed"] += 1
        record_failure(sem, "session_create_failed", dataset, fname,
                       srec["session_error"])
        return
    sem["session_ok"] += 1
    gen = srec["generation"]
    gstatus = gen["status"]
    gkey = gstatus if gstatus in sem["generation"] else "error"
    sem["generation"][gkey] += 1
    if gstatus == "ok":
        if not gen["json_parse"]:
            sem["generated_json_parse_failed"] += 1
            record_failure(
                sem, "generated_not_json", dataset, fname,
                "finish accepted a document that does not parse as JSON: "
                + gen["doc_preview"])
        elif gen["schema_valid"] is False:
            sem["generated_schema_invalid"] += 1
            record_failure(
                sem, "generated_schema_invalid", dataset, fname,
                "jsonschema rejects the generated document: "
                + gen["doc_preview"])
        elif gen["schema_valid"] is None:
            sem["generated_validator_error"] += 1
        else:
            sem["generated_schema_valid"] += 1
    elif gstatus == "dead_end":
        record_failure(sem, "generation_dead_end", dataset, fname,
                       gen["detail"])
    elif gstatus == "budget":
        record_failure(sem, "generation_budget", dataset, fname,
                       gen["detail"])
    elif gstatus == "oversize":
        if len(sem["oversize_files"]) < FAILURE_EXAMPLES_CAP:
            sem["oversize_files"].append(
                {"dataset": dataset, "file": fname, "detail": gen["detail"]})
    else:
        record_failure(sem, "generation_error", dataset, fname,
                       f"{gstatus}: {gen['detail']}")
    if srec["validator_error"] is not None:
        sem["validator_schema_error"] += 1
    for row in srec["instances"]:
        bucket = (sem["valid_instances"] if row["valid"]
                  else sem["invalid_instances"])
        bucket["total"] += 1
        outcome = row["outcome"]
        if outcome == "serialization_incompatible":
            bucket["serialization_incompatible"] += 1
        elif outcome.startswith("error:"):
            bucket["engine_error"] += 1
            record_failure(
                sem, "engine_error_instance", dataset, fname,
                f"{'valid' if row['valid'] else 'invalid'} "
                f"instance: {outcome} {row['detail']}")
        elif row["valid"]:
            if row["validator"] is False:
                bucket["validator_invalid"] += 1
            if outcome == "accept":
                bucket["accepted"] += 1
            else:
                bucket["rejected"] += 1
                record_failure(sem, "valid_instance_rejected", dataset,
                               fname, row["detail"])
        else:
            if outcome == "reject":
                bucket["rejected"] += 1
            else:
                bucket["accepted"] += 1
                if row["validator"] is False:
                    bucket["accepted_validator_invalid"] += 1
                    record_failure(
                        sem, "invalid_instance_accepted", dataset, fname,
                        "engine and serializer accept an instance "
                        "jsonschema rejects")
                elif row["validator"] is True:
                    bucket["accepted_validator_valid"] += 1


def gen_aggregate(records):
    """Per-tokenizer schema outcome counts of the generatable mode, or
    None when no record carries generatable results. Every compiled
    schema has exactly one aggregated outcome per tokenizer; `missing`
    counts compiled schemas without one (a harness error, expected 0)."""
    if not any("generatable" in r for r in records):
        return None
    _gp, so = _gen_mods()
    out = {}
    compiled = 0
    for rec in records:
        if rec["error_class"] != "compiled":
            continue
        compiled += 1
        for tok, r in (rec.get("generatable") or {}).items():
            a = out.setdefault(tok, {
                "schemas": 0,
                "outcomes": {o: 0 for o in so.SCHEMA_OUTCOMES},
                "pass_outcomes": {},
                "incomplete_passes_in_generatable": 0,
                "retried_on_fresh_context": 0,
                "results_with_budget_skips": 0,
                "documented_passes": {"total": 0, "with_document": 0},
                "completion": {"passes_run": 0, "replayed": 0, "rereplayed": 0,
                               "replayed_alone": 0, "replay_mismatches": 0},
                "by_stage_code": {},
                "examples": {},
            })
            a["schemas"] += 1
            a["outcomes"][r["outcome"]] = a["outcomes"].get(r["outcome"], 0) + 1
            for o, n in r["counts"].items():
                a["pass_outcomes"][o] = a["pass_outcomes"].get(o, 0) + n
            if r["counts"].get("skipped:schema_budget"):
                a["results_with_budget_skips"] += 1
            for row in (rec.get("_gen_passes") or {}).get(tok) or []:
                if row[0] in GEN_DOCUMENT_OUTCOMES:
                    a["documented_passes"]["total"] += 1
                    if len(row) > 6 and row[6] is not None:
                        a["documented_passes"]["with_document"] += 1
            c = r.get("completion")
            if c:
                a["completion"]["passes_run"] += c["passes_run"]
                a["completion"]["replayed"] += c["replayed"]
                a["completion"]["rereplayed"] += c.get("rereplayed", 0)
                a["completion"]["replayed_alone"] += c.get("replayed_alone", 0)
                a["completion"]["replay_mismatches"] += len(c["replay_mismatches"])
            if r["outcome"] == "generatable":
                a["incomplete_passes_in_generatable"] += r["incomplete"]
                continue
            if r.get("retried_on_fresh_context"):
                a["retried_on_fresh_context"] += 1
            key = f"{r['outcome']} / {r['stage']} / {r['code']}"
            a["by_stage_code"][key] = a["by_stage_code"].get(key, 0) + 1
            ex = a["examples"].setdefault(r["outcome"], [])
            if len(ex) < FAILURE_EXAMPLES_CAP:
                ex.append({"dataset": rec["dataset"], "file": rec["file"],
                           "stage": r["stage"], "code": r["code"],
                           "step": r.get("step"),
                           "prefix_length": r.get("prefix_length"),
                           "seed": r.get("seed"), "detail": r.get("detail", "")})
    for a in out.values():
        a["missing"] = compiled - a["schemas"]
        a["by_stage_code"] = dict(sorted(a["by_stage_code"].items(),
                                         key=lambda kv: (-kv[1], kv[0])))
        a["base"], pct_by = so.outcome_percentages(a["outcomes"])
        a["percent"] = pct_by
    return out


def aggregate_records(records, sem_enabled):
    """Compute the report aggregate from per-file records. Used by
    scan() directly and by --merge-chunks on the concatenation of chunk
    records, so a chunked isolation run and a monolithic run publish
    exactly the same metrics."""
    by_bucket = {}
    by_dataset = {}
    keyword_hist = {}
    invalid_hist = {}
    resource_limit_files = []
    n_valid_total = 0
    n_invalid_total = 0
    sem = new_sem_aggregate() if sem_enabled else None
    for rec in records:
        dataset = rec["dataset"]
        fname = rec["file"]
        by_bucket[rec["error_class"]] = by_bucket.get(rec["error_class"], 0) + 1
        d = by_dataset.setdefault(
            dataset, {"total": 0, "examples_valid": 0, "examples_invalid": 0}
        )
        d["total"] += 1
        d[rec["error_class"]] = d.get(rec["error_class"], 0) + 1
        d["examples_valid"] += rec["examples_valid"]
        d["examples_invalid"] += rec["examples_invalid"]
        n_valid_total += rec["examples_valid"]
        n_invalid_total += rec["examples_invalid"]
        if rec["error_class"] == "unsupported_feature":
            keyword_hist[rec["keyword"]] = keyword_hist.get(rec["keyword"], 0) + 1
        elif rec["error_class"] == "invalid_schema":
            sub = invalid_subclass(rec["detail"])
            invalid_hist[sub] = invalid_hist.get(sub, 0) + 1
        elif rec["error_class"] == "resource_limit":
            resource_limit_files.append(
                {"dataset": dataset, "file": fname, "detail": rec["detail"]}
            )
        if sem is not None:
            sem_aggregate_update(sem, dataset, fname, rec)
    aggregate = {
        "total": len(records),
        "by_outcome": {k: by_bucket[k] for k in BUCKET_ORDER if k in by_bucket},
        "by_dataset": {
            ds: by_dataset[ds] for ds in sorted(by_dataset)
        },
        "unsupported_keyword_histogram": dict(
            sorted(keyword_hist.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "invalid_schema_subclasses": dict(
            sorted(invalid_hist.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "resource_limit_files": resource_limit_files,
        "examples": {"valid": n_valid_total, "invalid": n_invalid_total},
    }
    if sem is not None:
        sem["failure_kinds"] = dict(
            sorted(sem["failure_kinds"].items(), key=lambda kv: (-kv[1], kv[0]))
        )
        sem["failures_capped_at"] = FAILURE_EXAMPLES_CAP
        aggregate["semantic"] = sem
    gen = gen_aggregate(records)
    if gen is not None:
        aggregate["generatable"] = gen
    return aggregate


def scan(snapshot_key, snapshot, ctx, progress_every=1000, byte_ctx=None,
         serializer=None, max_steps=DEFAULT_MAX_STEPS, max_files=0,
         max_seconds=DEFAULT_MAX_GEN_SECONDS, skip_files=0, fault_list=None,
         gen_toks=None, gen_opts=None, crash_list=None):
    """Compile every file; semantic checks (byte_ctx) and generatable
    passes (gen_toks) for the compiled ones. Generatable pass records
    are kept on each record under "_gen_passes" until the report is
    written (write_report_files moves them to the sidecar)."""
    records = []
    sem_enabled = byte_ctx is not None
    t_start = time.time()
    n_seen = 0
    for dataset, fname, path in iter_files(snapshot):
        if n_seen < skip_files:
            n_seen += 1
            continue
        n_seen += 1
        if max_files and len(records) >= max_files:
            break
        rec = {
            "dataset": dataset,
            "file": fname,
            "status": "OK",
            "error_class": "compiled",
            "keyword": None,
            "detail": "",
            "source_pointer": "",
            "error_stage": None,
            "examples_valid": 0,
            "examples_invalid": 0,
        }
        try:
            data, schema_doc, tests = load_schema_bytes(snapshot, path)
            n_valid = sum(1 for t in tests if t.get("valid") is True)
            n_invalid = sum(1 for t in tests if t.get("valid") is False)
            rec["examples_valid"] = n_valid
            rec["examples_invalid"] = n_invalid
        except Exception as e:  # noqa: BLE001 - record everything
            rec["status"] = "HARNESS_ERROR"
            rec["error_class"] = "harness_error"
            rec["detail"] = f"{type(e).__name__}: {e}"
            rec["error_stage"] = "read"
            data = None
        compile_fault = (fault_list or {}).get(fname)
        if data is not None and isinstance(compile_fault, dict) \
                and compile_fault.get("phase") == "compile":
            # the engine dies on this file's compile (recorded by the
            # --jobs driver): listed, never retried in-process
            rec["status"] = "CRASH"
            rec["error_class"] = "harness_error"
            rec["detail"] = f"engine crash during compile: {compile_fault.get('reason')}"
            rec["error_stage"] = "compile"
            data = None
        if data is not None:
            PROGRESS.mark(dataset=dataset, file=fname, phase="compile")
            status, message, pointer, _offset = compile_schema(ctx, data)
            status_name = blg.STATUS_NAMES.get(status, f"status={status}")
            rec["status"] = status_name
            if status != blg.BLG_OK:
                rec["error_class"] = BUCKET_OF_STATUS.get(status, "harness_error")
                rec["detail"] = message
                rec["source_pointer"] = pointer
                rec["error_stage"] = error_stage_of(message)
                if rec["error_class"] == "unsupported_feature":
                    rec["keyword"] = extract_keyword(message)
            elif sem_enabled:
                if fault_list and fname in fault_list:
                    # the engine dies unrecoverably on this file (e.g.
                    # a Zig panic in a pathological mask computation):
                    # the semantic phase cannot run in-process. Compile
                    # coverage above still counts the file; the skip is
                    # its own bucket, never a success or a failure.
                    rec["semantic"] = {
                        "engine_fault_skip": fault_list[fname]}
                else:
                    PROGRESS.mark(dataset=dataset, file=fname,
                                  phase="semantic")

                    def beat(i, dataset=dataset, fname=fname):
                        PROGRESS.mark(dataset=dataset, file=fname,
                                      phase="semantic", instance=i)
                    try:
                        srec = semantic_check(byte_ctx.get(), data,
                                              schema_doc, tests, max_steps,
                                              serializer, max_seconds, beat)
                        if is_resource_limit_rec(srec):
                            # accumulated-context artifact (see
                            # ContextRecycler): retry once on a fresh
                            # context; only a failure that reproduces
                            # there is genuine
                            byte_ctx.recycle_now()
                            srec = semantic_check(
                                byte_ctx.get(), data, schema_doc, tests,
                                max_steps, serializer, max_seconds, beat)
                        byte_ctx.note_use()
                        rec["semantic"] = srec
                    except Exception as e:  # noqa: BLE001 - record all
                        rec["semantic"] = None
                        rec["semantic_error"] = f"{type(e).__name__}: {e}"
            if status == blg.BLG_OK and gen_toks:
                try:
                    gen = generatable_all(gen_toks, data, schema_doc, dataset,
                                          fname, gen_opts, crash_list)
                except Exception as e:  # noqa: BLE001 - harness defect, listed
                    gen = {gt.key: gen_schema_level(
                        "skipped", None, "HARNESS_ERROR",
                        f"{type(e).__name__}: {e}") for gt in gen_toks}
                rec["generatable"], rec["_gen_passes"] = gen_split_records(gen)
        records.append(rec)
        if progress_every and len(records) % progress_every == 0:
            print(
                f"... [{snapshot_key}] {len(records)} files, "
                f"{time.time() - t_start:.1f} s",
                file=sys.stderr, flush=True,
            )
    aggregate = aggregate_records(records, sem_enabled)
    if sem_enabled:
        aggregate["semantic"]["byte_ctx_recycles"] = byte_ctx.recycles
        aggregate["semantic"]["byte_ctx_destroy_failures"] = byte_ctx.destroy_failures
    return records, aggregate


def load_report(path):
    """A coverage report (.json or .json.gz) and its sidecar rows."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        report = json.load(f)
    base = path[: -len(".gz")] if path.endswith(".gz") else path
    return report, load_sidecar_rows(base)


def completed_report_path(directory, snapshot_key):
    for name in (f"coverage_{snapshot_key}.json", f"coverage_{snapshot_key}.json.gz"):
        p = os.path.join(directory, name)
        if os.path.exists(p):
            return p
    raise SystemExit(f"--complete-from: no coverage_{snapshot_key}.json[.gz] in {directory}")


GEN_IDENTITY_KEYS = ("profile", "passes", "seed", "max_steps", "max_gen_seconds",
                     "context_recycle_every", "validator")


def check_completable(report, snapshot, gen_meta):
    """The earlier report was made by this engine library, on this corpus,
    with these generatable parameters and tokenizers."""
    meta = report["meta"]
    problems = []
    if meta["engine"]["library_sha256"] != library_sha256():
        problems.append("engine library")
    if meta["corpus"]["sha256"] != corpus_sha256(snapshot):
        problems.append("corpus")
    old = meta.get("generatable") or {}
    for k in GEN_IDENTITY_KEYS:
        if old.get(k) != gen_meta.get(k):
            problems.append(f"generatable.{k}")
    for t, info in gen_meta["tokenizers"].items():
        o = (old.get("tokenizers") or {}).get(t) or {}
        for k in ("name", "revision", "vocab_size", "sample_every", "unavailable"):
            if o.get(k) != info.get(k):
                problems.append(f"tokenizers.{t}.{k}")
    if set(old.get("tokenizers") or {}) != set(gen_meta["tokenizers"]):
        problems.append("tokenizer set")
    if problems:
        raise SystemExit("--complete-from: the report differs from this run in "
                         + ", ".join(problems))


def complete_scan(snapshot_key, snapshot, prev, prev_rows, progress_every=1000,
                  max_files=0, skip_files=0, gen_toks=None, gen_opts=None,
                  crash_list=None):
    """--complete-from: the per-file records of the earlier report for the
    same file range, compile and semantic results unchanged, generatable
    results completed (generatable_complete)."""
    by_key = {(r["dataset"], r["file"]): r for r in prev["per_file"]}
    sem_enabled = "semantic" in prev["aggregate"]
    records = []
    t_start = time.time()
    n_seen = 0
    for dataset, fname, path in iter_files(snapshot):
        if n_seen < skip_files:
            n_seen += 1
            continue
        n_seen += 1
        if max_files and len(records) >= max_files:
            break
        old = by_key.get((dataset, fname))
        if old is None:
            raise SystemExit(f"--complete-from: no record of {dataset}/{fname}")
        rec = {k: v for k, v in old.items() if k != "generatable"}
        PROGRESS.mark(dataset=dataset, file=fname, phase="complete")
        if "generatable" in old:
            data, schema_doc, _tests = load_schema_bytes(snapshot, path)
            gen = generatable_complete(gen_toks, data, schema_doc, dataset, fname,
                                       gen_opts, old["generatable"],
                                       prev_rows.get((dataset, fname)) or {}, crash_list)
            rec["generatable"], rec["_gen_passes"] = gen_split_records(gen)
        records.append(rec)
        if progress_every and len(records) % progress_every == 0:
            print(f"... [{snapshot_key}] {len(records)} files, "
                  f"{time.time() - t_start:.1f} s", file=sys.stderr, flush=True)
    aggregate = aggregate_records(records, sem_enabled)
    if sem_enabled:
        for counter in SEMANTIC_RUN_COUNTERS:
            aggregate["semantic"][counter] = 0
    return records, aggregate


def library_sha256():
    with open(blg.LIB._path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def build_report(snapshot_key, snapshot, records, aggregate, tokenizer_info,
                 semantic_meta=None, gen_meta=None):
    import bolorgir as zc

    meta = {
        "report": "bench_jsb_coverage",
        "corpus": {
            "name": snapshot["corpus_name"],
            "repo": JSB_REPO,
            "revision": snapshot["revision"],
            "path": snapshot["corpus_path"],
            "sha256": corpus_sha256(snapshot),
            "files": aggregate["total"],
        },
        "engine": {
            "commit": os.environ.get("BLG_ENGINE_LABEL") or engine_commit(),
            "library_sha256": library_sha256(),
            "package_version": zc.__version__,
            "abi_version": zc.abi_version(),
            "profile": PROFILE,
        },
        "limits": LIMITS,
        "tokenizer": tokenizer_info,
        "date": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if semantic_meta is not None:
        meta["semantic"] = semantic_meta
    if gen_meta is not None:
        meta["generatable"] = gen_meta
    return {
        "meta": meta,
        "aggregate": aggregate,
        "per_file": records,
    }


def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def pct(part, base):
    if not base:
        return "n/a"
    return f"{100.0 * part / base:.2f}%"


def render_semantic_markdown(report):
    """Split metrics of the R5 semantic mode: one row per metric with its
    own base, never a blended number; failures grouped by kind."""
    meta = report["meta"]
    agg = report["aggregate"]
    sem = agg["semantic"]
    compiled = agg["by_outcome"].get("compiled", 0)
    total = agg["total"]
    gen = sem["generation"]
    vi = sem["valid_instances"]
    ii = sem["invalid_instances"]
    gen_ok = gen["ok"]
    gen_attempted = compiled - gen["oversize"] - sem["compile_mismatch"] \
        - sem["session_failed"]
    inst_total = vi["total"] + ii["total"]
    ser_incompat = (vi["serialization_incompatible"]
                    + ii["serialization_incompatible"])
    sm = meta["semantic"]
    lines = [
        "## Semantic coverage (split metrics)",
        "",
        "Each metric has its own base; compile coverage alone is not exact",
        "coverage. Semantic checks run on a byte-level synthetic tokenizer",
        f"(token id = byte value, eos=256) with the `{sm['profile']}` profile;",
        f"instances are encoded with {sm['serializer']} and cross-checked",
        f"with {sm['validator']}; generation budgets: {sm['max_steps']} steps "
        f"and {sm['max_gen_seconds']:.0f}s wall time per schema. The byte"
        f" context is recycled every {sm['byte_ctx_recycle_every']} checks"
        f" (plus a retry on RESOURCE_LIMIT): {sem['byte_ctx_recycles']}"
        " recycles this run - engine retains generation memory in a Context"
        " across session/grammar free (R5 finding). Context destroy()"
        f" failures: {sem['byte_ctx_destroy_failures']}.",
        "",
        md_table(
            ["metric", "n", "base", "%"],
            [
                ["compile coverage", compiled, total, pct(compiled, total)],
                ["explicit refusals (engine declines the schema)",
                 total - compiled, total, pct(total - compiled, total)],
                ["session success", sem["session_ok"], compiled,
                 pct(sem["session_ok"], compiled)],
                ["generation success", gen_ok, compiled, pct(gen_ok, compiled)],
                ["generation success (of attempted: compiled minus "
                 "oversize skips)", gen_ok, gen_attempted,
                 pct(gen_ok, gen_attempted)],
                ["generated-doc schema validity (jsonschema)",
                 sem["generated_schema_valid"], gen_ok,
                 pct(sem["generated_schema_valid"], gen_ok)],
                ["valid-instance acceptance", vi["accepted"], vi["total"],
                 pct(vi["accepted"], vi["total"])],
                ["invalid-instance rejection", ii["rejected"], ii["total"],
                 pct(ii["rejected"], ii["total"])],
                ["serialization incompatibility", ser_incompat, inst_total,
                 pct(ser_incompat, inst_total)],
            ],
        ),
        "",
        "Detail counters:",
        "",
        md_table(
            ["counter", "n"],
            [
                ["compile mismatch (byte-context compile differs)",
                 sem["compile_mismatch"]],
                ["session create failed", sem["session_failed"]],
                ["generation: dead end", gen["dead_end"]],
                ["generation: step/time budget exhausted", gen["budget"]],
                ["generation: oversize skip (min document size estimate "
                 "> step budget; not attempted)", gen["oversize"]],
                ["generation: engine error", gen["error"]],
                ["generated doc not JSON (finish accepted it)",
                 sem["generated_json_parse_failed"]],
                ["generated doc rejected by jsonschema",
                 sem["generated_schema_invalid"]],
                ["generated doc validator error (no verdict)",
                 sem["generated_validator_error"]],
                ["schema unusable for jsonschema (validator_error)",
                 sem["validator_schema_error"]],
                ["valid instances: rejected", vi["rejected"]],
                ["valid instances: engine error", vi["engine_error"]],
                ["valid instances: corpus label vs jsonschema disagree",
                 vi["validator_invalid"]],
                ["invalid instances: accepted (jsonschema agrees invalid)",
                 ii["accepted_validator_invalid"]],
                ["invalid instances: accepted (jsonschema says valid)",
                 ii["accepted_validator_valid"]],
                ["invalid instances: engine error", ii["engine_error"]],
            ],
        ),
        "",
    ]
    if sem["failure_kinds"]:
        lines += [
            "## Semantic failure kinds (schemas that compile but fail a check)",
            "",
            md_table(
                ["failure kind", "count"],
                [[k, v] for k, v in sem["failure_kinds"].items()],
            ),
            "",
        ]
        for kind, rows in sem["failures"].items():
            lines += [
                f"### {kind} ({sem['failure_kinds'][kind]}"
                f"{', first ' + str(len(rows)) if sem['failure_kinds'][kind] > len(rows) else ''})",
                "",
                md_table(
                    ["dataset", "file", "detail"],
                    [[r["dataset"], r["file"], r["detail"]] for r in rows[:15]],
                ),
                "",
            ]
    if sem["oversize_files"]:
        lines += [
            f"## Oversize generation skips ({gen['oversize']}"
            f"{', first ' + str(len(sem['oversize_files'])) if gen['oversize'] > len(sem['oversize_files']) else ''})",
            "",
            "Static minimum-document-size estimate above the step budget;",
            "generation not attempted (a documented skip, not a failure).",
            "",
            md_table(
                ["dataset", "file", "detail"],
                [[r["dataset"], r["file"], r["detail"]]
                 for r in sem["oversize_files"][:15]],
            ),
            "",
        ]
    if sem["engine_fault_files"]:
        lines += [
            f"## Engine-fault semantic skips ({sem['engine_fault_skips']})",
            "",
            "The engine dies unrecoverably on these files (e.g. a Zig",
            "panic in a pathological mask computation), so the in-process",
            "semantic phase cannot run on them. Compile coverage above",
            "still counts them; the skip is its own bucket, never a",
            "success or a failure. Passed via --engine-fault-list.",
            "",
            md_table(
                ["dataset", "file", "reason"],
                [[r["dataset"], r["file"], r["reason"]]
                 for r in sem["engine_fault_files"]],
            ),
            "",
        ]
    return lines


def completion_text(gm):
    """The --complete-from history of a generatable report, earliest run
    first ("" for a report that was not completed)."""
    chain = []
    cf = gm.get("completed_from")
    while cf:
        chain.append(cf)
        cf = cf.get("previous")
    chain.reverse()

    def limit(c):
        sec = c.get("replay_max_gen_seconds")
        return ((f" with {sec:.0f} s per pass"
                 if sec is not None and sec != gm["max_gen_seconds"] else "")
                + (" (a replay that ended in engine_unavailable again on a fresh"
                   " context ran alone in a child process)" if c.get("replay_alone") else ""))

    def ref(c):
        return f"`{c['report']}`, sha256 `{c['report_sha256'][:16]}...`"

    text = ""
    for i, c in enumerate(chain):
        if i == 0:
            text += (f" The run of {c['date']} ({ref(c)}) is completed with"
                     " `--complete-from`: its passes recorded as"
                     " `skipped:schema_budget` ran, its invalid_document and"
                     " validator_error passes were replayed by seed for their"
                     f" documents{limit(c)}, and every other record is that run's.")
        else:
            text += (f" The completed report of {c['date']} ({ref(c)}) is completed"
                     " again: its invalid_document and validator_error passes"
                     f" without a document were replayed{limit(c)}.")
    return text


def render_generatable_markdown(report):
    """Schema outcomes of the K-pass generatable mode per tokenizer, the
    pass outcome histogram and the unsuccessful schemas by stage and
    code."""
    _gp, so = _gen_mods()
    meta = report["meta"]
    agg = report["aggregate"]
    gen = agg["generatable"]
    gm = meta["generatable"]
    compiled = agg["by_outcome"].get("compiled", 0)
    toks = list(gen)
    lines = [
        "## Generatable (K passes per schema and tokenizer)",
        "",
        f"K = {gm['passes']}. Pass 1 runs the greedy `generate_document`"
        " policy over single-byte tokens; passes 2..K run the biased random"
        " policy of `tests/generation_passes.py` (seed = sha256 of the base"
        f" seed {gm['seed']}, file, tokenizer and pass index). Budgets per"
        f" pass: {gm['max_steps']} steps and {gm['max_gen_seconds']:.0f} s."
        + (f" Per schema and tokenizer: {gm['schema_budget_seconds']:.0f} s,"
           " after which the remaining passes are recorded as"
           " `skipped:schema_budget`." if gm["schema_budget_seconds"] else
           " Every pass of every schema is attempted.")
        + completion_text(gm)
        + " Every document is validated by"
        f" {gm['validator']}. Aggregation per schema: the first matching"
        " rule of `tests/semantic_outcomes.py` `aggregate_schema_outcome`;"
        " `skipped` is outside the percentage base. Every pass record is in"
        f" `{gm.get('passes_sidecar', '-')}`.",
        "",
        md_table(["tokenizer", "name", "revision", "vocab",
                  "single-byte tokens", "schema sample"],
                 [[t, gm["tokenizers"][t]["name"], gm["tokenizers"][t]["revision"],
                   gm["tokenizers"][t].get("vocab_size", "-"),
                   gm["tokenizers"][t].get("single_byte_tokens",
                                           gm["tokenizers"][t].get("unavailable", "-")),
                   "all" if gm["tokenizers"][t].get("sample_every", 1) <= 1
                   else f"1 in {gm['tokenizers'][t]['sample_every']}"]
                  for t in toks]),
        "",
        "A sampled tokenizer runs on the compiled schemas whose"
        " sha256(dataset/file) is 0 modulo the sample size; the others are"
        " `skipped` with code `not_sampled`.",
        "",
    ]
    rows = []
    for o in so.SCHEMA_OUTCOMES:
        row = [o]
        for t in toks:
            n = gen[t]["outcomes"].get(o, 0)
            p = gen[t]["percent"].get(o)
            row.append(str(n) if o == "skipped" or p is None else f"{n} ({p:.2f}%)")
        rows.append(row)
    rows.append(["schemas with an outcome / compiled"]
                + [f"{gen[t]['schemas']} / {compiled}" for t in toks])
    rows.append(["percentage base (compiled minus skipped)"]
                + [str(gen[t]["base"]) for t in toks])
    rows.append(["incomplete passes inside generatable schemas"]
                + [str(gen[t]["incomplete_passes_in_generatable"]) for t in toks])
    rows.append(["unsuccessful results retried on a fresh context"]
                + [str(gen[t]["retried_on_fresh_context"]) for t in toks])
    rows.append(["results with passes skipped by the schema budget"]
                + [str(gen[t].get("results_with_budget_skips", "-")) for t in toks])
    rows.append(["invalid_document / validator_error passes with their document"]
                + [f"{gen[t]['documented_passes']['with_document']} / "
                   f"{gen[t]['documented_passes']['total']}"
                   if "documented_passes" in gen[t] else "-" for t in toks])
    if gm.get("completed_from"):
        rows.append(["passes run / replayed / replays not reproduced by --complete-from"]
                    + ["{passes_run} / {replayed} / {replay_mismatches}".format(
                        **gen[t]["completion"]) for t in toks])
        if (gm["completed_from"].get("previous")
                or any(gen[t]["completion"].get("rereplayed") for t in toks)):
            rows.append(["replays not reproduced earlier and replayed again"]
                        + [str(gen[t]["completion"].get("rereplayed", 0)) for t in toks])
        if any(gen[t]["completion"].get("replayed_alone") for t in toks):
            rows.append(["replays run alone in a child process"]
                        + [str(gen[t]["completion"].get("replayed_alone", 0)) for t in toks])
    lines += [md_table(["schema outcome"] + toks, rows), ""]
    pass_keys = sorted(set().union(*(gen[t]["pass_outcomes"] for t in toks)))
    lines += [
        "Pass outcomes:",
        "",
        md_table(["pass outcome"] + toks,
                 [[k] + [gen[t]["pass_outcomes"].get(k, 0) for t in toks]
                  for k in pass_keys]),
        "",
    ]
    for t in toks:
        g = gen[t]
        if not g["by_stage_code"]:
            continue
        lines += [
            f"### {t}: schemas that are not generatable, by outcome / stage / code",
            "",
            md_table(["outcome / stage / code", "schemas"],
                     [[k, v] for k, v in g["by_stage_code"].items()]),
            "",
        ]
        for o, ex in g["examples"].items():
            shown = ex[:15]
            lines += [
                f"#### {t}: {o} ({g['outcomes'].get(o, 0)}"
                f"{', first ' + str(len(shown)) if g['outcomes'].get(o, 0) > len(shown) else ''})",
                "",
                md_table(["dataset", "file", "stage", "code", "step", "prefix", "seed"],
                         [[r["dataset"], r["file"], r["stage"], r["code"], r["step"],
                           r["prefix_length"], r["seed"]] for r in shown]),
                "",
            ]
    return lines


def render_markdown(report):
    meta = report["meta"]
    agg = report["aggregate"]
    lines = [
        f"# Coverage report - {meta['corpus']['name']}",
        "",
        f"- corpus: {meta['corpus']['repo']} revision `{meta['corpus']['revision']}`"
        f" (`{meta['corpus']['path']}`), sha256 `{meta['corpus']['sha256']}`",
        f"- engine commit: `{meta['engine']['commit']}`"
        f" (bolorgir {meta['engine']['package_version']},"
        f" ABI {meta['engine']['abi_version']}), profile `{meta['engine']['profile']}`",
        f"- limits: " + ", ".join(f"{k}={v}" for k, v in meta["limits"].items()),
        f"- tokenizer: {meta['tokenizer']['name']} @ {meta['tokenizer']['revision']}"
        f" ({meta['tokenizer']['vocab_size']} tokens)",
        f"- date: {meta['date']}",
        f"- library sha256: `{meta['engine'].get('library_sha256', '-')}`",
        "",
    ]
    if "generatable" in agg:
        _gp, so = _gen_mods()
        lines += [
            f"> {so.GENERATABLE_DISCLAIMER}",
            "",
            f"K = {meta['generatable']['passes']}; tokenizers: "
            + ", ".join(meta["generatable"]["tokenizers"]) + ".",
            "",
        ]
    lines += [
        "First blocker per file; a refusal is never coverage.",
        "",
        "## Outcome buckets",
        "",
        md_table(
            ["bucket", "files"],
            [[k, v] for k, v in agg["by_outcome"].items()]
            + [["total", agg["total"]]],
        ),
        "",
        "## Invalid-schema subclasses",
        "",
        md_table(
            ["subclass", "files"],
            [[k, v] for k, v in agg["invalid_schema_subclasses"].items()],
        ),
        "",
        "## Unsupported-feature keyword histogram (first blocker)",
        "",
        md_table(
            ["keyword", "files"],
            [[k, v] for k, v in agg["unsupported_keyword_histogram"].items()],
        ),
        "",
        "## Per-dataset counts",
        "",
        md_table(
            ["dataset", "total", "compiled", "invalid_schema",
             "unsupported_feature", "resource_limit", "valid ex.", "invalid ex."],
            [
                [
                    ds,
                    d["total"],
                    d.get("compiled", 0),
                    d.get("invalid_schema", 0),
                    d.get("unsupported_feature", 0),
                    d.get("resource_limit", 0),
                    d["examples_valid"],
                    d["examples_invalid"],
                ]
                for ds, d in agg["by_dataset"].items()
            ],
        ),
        "",
        f"Examples: {agg['examples']['valid']} valid,"
        f" {agg['examples']['invalid']} invalid.",
        "",
    ]
    if "semantic" in agg:
        lines += render_semantic_markdown(report)
    if "generatable" in agg:
        lines += render_generatable_markdown(report)
    if agg["resource_limit_files"]:
        lines += [
            "## Resource-limit files",
            "",
            md_table(
                ["dataset", "file", "detail"],
                [
                    [r["dataset"], r["file"], r["detail"]]
                    for r in agg["resource_limit_files"]
                ],
            ),
            "",
        ]
    return "\n".join(lines)


def summary_entry(out_json, out_md, report, aggregate):
    entry = {
        "out_json": out_json,
        "out_md": out_md,
        "by_outcome": aggregate["by_outcome"],
        "corpus_sha256": report["meta"]["corpus"]["sha256"],
    }
    if "semantic" in aggregate:
        sem = aggregate["semantic"]
        compiled = aggregate["by_outcome"].get("compiled", 0)
        entry["semantic"] = {
            "session_ok": sem["session_ok"],
            "generation_ok": sem["generation"]["ok"],
            "generated_schema_valid": sem["generated_schema_valid"],
            "valid_instances": sem["valid_instances"],
            "invalid_instances": sem["invalid_instances"],
            "failure_kinds": sem["failure_kinds"],
            "byte_ctx_recycles": sem["byte_ctx_recycles"],
            "engine_fault_skips": sem["engine_fault_skips"],
            "compiled": compiled,
        }
    if "generatable" in aggregate:
        entry["generatable"] = {
            t: {"outcomes": g["outcomes"], "base": g["base"],
                "missing": g["missing"]}
            for t, g in aggregate["generatable"].items()}
    return entry


PASS_FIELDS = ["outcome", "stage", "code", "step", "prefix_length", "seed", "document"]


def sidecar_path(out_json):
    return out_json[: -len(".json")] + ".passes.json.gz"


def write_report_files(out_json, out_md, report):
    """Publish the report atomically: an interrupted write leaves the
    previous complete file (or nothing) instead of a truncated JSON that
    a chunked driver's resume check would mistake for a finished chunk.
    Generatable pass records ("_gen_passes" on the per-file records) go
    to the gzipped sidecar next to the JSON report."""
    rows = []
    for rec in report["per_file"]:
        passes = rec.pop("_gen_passes", None)
        if passes is not None:
            rows.append([rec["dataset"], rec["file"], passes])
    tmp_side = None
    if rows:
        side = sidecar_path(out_json)
        report["meta"].setdefault("generatable", {})["passes_sidecar"] = \
            os.path.basename(side)
        tmp_side = side + ".tmp"
        with gzip.open(tmp_side, "wt", encoding="utf-8") as f:
            json.dump({"fields": PASS_FIELDS,
                       "rows_format": "[dataset, file, {tokenizer: [pass record]}]",
                       "rows": rows}, f, ensure_ascii=False, separators=(",", ":"))
    tmp_json = out_json + ".tmp"
    tmp_md = out_md + ".tmp"
    # ASCII escapes: corpus strings and generated documents hold any
    # character, including ones the repository text rules forbid.
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=True, indent=1)
        f.write("\n")
    with open(tmp_md, "w", encoding="utf-8") as f:
        f.write(render_markdown(report))
    if tmp_side is not None:
        os.replace(tmp_side, sidecar_path(out_json))
    os.replace(tmp_json, out_json)
    os.replace(tmp_md, out_md)


def load_sidecar_rows(out_json):
    """{(dataset, file): {tokenizer: [pass record]}} of a report's
    sidecar, {} when there is none."""
    side = sidecar_path(out_json)
    if not os.path.exists(side):
        return {}
    with gzip.open(side, "rt", encoding="utf-8") as f:
        doc = json.load(f)
    return {(d, fn): p for d, fn, p in doc["rows"]}


def merge_identity(report):
    """The part of a chunk report's header that must be equal across the
    chunks of one merge: engine, corpus and measurement parameters."""
    m = report["meta"]
    return {
        "engine": {k: m["engine"].get(k) for k in ("commit", "library_sha256", "profile")},
        "corpus": m["corpus"]["sha256"],
        "semantic": {k: v for k, v in (m.get("semantic") or {}).items()
                     if k not in ("max_files", "skip_files", "engine_fault_list")},
        "generatable": {k: v for k, v in (m.get("generatable") or {}).items()
                        if k not in ("passes_sidecar", "crashes", "merged_from_chunks")},
    }


def check_merge_parts(paths, parts):
    """All chunk reports come from one engine library (the one loaded now,
    which build_report records), one corpus and one set of parameters."""
    first = merge_identity(parts[0])
    for p, rep in zip(paths[1:], parts[1:]):
        ident = merge_identity(rep)
        if ident != first:
            diff = sorted(k for k in first if first[k] != ident[k])
            raise SystemExit(f"merge refused: {p} differs from {paths[0]} in "
                             + ", ".join(diff))
    if first["engine"]["library_sha256"] != library_sha256():
        raise SystemExit("merge refused: the chunks were made by another engine "
                         "library than the one loaded now")


def strip_generatable(aggregate):
    """An aggregate without its generatable part and run counters."""
    out = {k: v for k, v in aggregate.items() if k != "generatable"}
    if "semantic" in out:
        out["semantic"] = {k: v for k, v in out["semantic"].items()
                           if k not in SEMANTIC_RUN_COUNTERS}
    return out


def merge_chunks(snapshot_key, paths, out_dir, completed=None):
    """Merge per-chunk coverage reports of one snapshot into a single
    report. Counter aggregation is replayed from the per-file records
    (aggregate_records). Chunk boundaries still change cache/recycler
    history, so generation outcomes under the time budgets are not
    guaranteed bit-identical to a monolithic run; used by chunked
    isolation drivers that skip engine-fault files. Duplicate file
    records across chunks are a hard error, and so are chunks of
    different engines or parameters (check_merge_parts). completed: the
    report of --complete-from, whose run counters are carried over and
    whose compile and semantic aggregates must come out unchanged."""
    snapshot = SNAPSHOTS[snapshot_key]
    records = []
    parts = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            rep = json.load(f)
        side = load_sidecar_rows(p)
        for rec in rep["per_file"]:
            passes = side.get((rec["dataset"], rec["file"]))
            if passes is not None:
                rec["_gen_passes"] = passes
        records.extend(rep["per_file"])
        parts.append(rep)
    check_merge_parts(paths, parts)
    seen = set()
    for rec in records:
        fkey = (rec.get("dataset"), rec.get("file"))
        if fkey in seen:
            raise SystemExit(
                f"merge refused: duplicate file record {fkey} across chunks")
        seen.add(fkey)
    sem_enabled = any("semantic" in r["aggregate"] for r in parts)
    aggregate = aggregate_records(records, sem_enabled)
    semantic_meta = None
    if sem_enabled:
        for counter in SEMANTIC_RUN_COUNTERS:
            aggregate["semantic"][counter] = (
                completed["aggregate"]["semantic"].get(counter, 0) if completed
                else sum(r["aggregate"].get("semantic", {}).get(counter, 0)
                         for r in parts))
        semantic_meta = dict(parts[0]["meta"].get("semantic") or {})
        semantic_meta["engine_fault_list"] = report_path(
            semantic_meta.get("engine_fault_list"))
        if completed is None:
            semantic_meta["merged_from_chunks"] = len(parts)
    if completed is not None:
        same_files = [r for r in completed["per_file"] if (r["dataset"], r["file"]) in seen]
        want = aggregate_records(same_files, sem_enabled)
        if strip_generatable(aggregate) != strip_generatable(want):
            raise SystemExit("merge refused: the compile or semantic aggregate of "
                             "the completed report changed")
    gen_meta = None
    if "generatable" in aggregate:
        gen_meta = dict(parts[0]["meta"].get("generatable") or {})
        gen_meta.pop("passes_sidecar", None)
        gen_meta["merged_from_chunks"] = len(parts)
        gen_meta["crashes"] = (
            list((gen_meta.get("completed_from") or {}).get("crashes", []))
            + [c for r in parts
               for c in r["meta"].get("generatable", {}).get("crashes", [])])
    _spec, tokenizer_info = make_tokenizer()
    report = build_report(snapshot_key, snapshot, records, aggregate,
                          tokenizer_info, semantic_meta, gen_meta)
    os.makedirs(out_dir, exist_ok=True)
    out_json = os.path.join(out_dir, f"coverage_{snapshot_key}.json")
    out_md = os.path.join(out_dir, f"coverage_{snapshot_key}.md")
    write_report_files(out_json, out_md, report)
    summary = {"status": "OK",
               "reports": {snapshot_key: summary_entry(
                   out_json, out_md, report, aggregate)}}
    print(json.dumps(summary, ensure_ascii=False, indent=1))


CHUNK_STALL_SECONDS = 300.0
CHUNK_ATTEMPTS = 8


def _die_with_parent():
    """Chunk subprocess setup (Linux): SIGTERM when the driver dies, so an
    interrupted run leaves no workers behind."""
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except OSError:
        pass


def chunk_argv(args, key, start, count, cdir, files):
    """Command line of one chunk subprocess (same measurement flags)."""
    argv = [sys.executable, os.path.abspath(__file__), "--snapshot", key,
            "--skip-files", str(start), "--max-files", str(count),
            "--out-dir", cdir, "--progress-every", "0",
            "--progress-file", files["progress"],
            "--max-steps", str(args.max_steps),
            "--max-gen-seconds", str(args.max_gen_seconds),
            "--engine-fault-list", files["faults"]]
    if args.semantic:
        argv += ["--semantic"]
    if args.generatable:
        argv += ["--generatable", "--passes", str(args.passes),
                 "--tokenizers", args.tokenizers,
                 "--hf-sample-every", str(args.hf_sample_every),
                 "--schema-budget-seconds", str(args.schema_budget_seconds),
                 "--crash-list", files["crashes"]]
    if args.complete_from:
        argv += ["--complete-from", args.complete_from]
    return argv


HARNESS_SOURCES = (
    "benchmarks/bench_jsb_coverage.py",
    "tests/blg_ctypes.py",
    "tests/exact_numbers.py",
    "tests/generation_passes.py",
    "tests/reference.py",
    "tests/semantic_outcomes.py",
    "python/bolorgir/serializer.py",
)


def run_identity(args, key):
    """What the results of a chunk depend on besides its file range: engine
    library, harness sources, measurement parameters, corpus, the report
    being completed and the validator and tokenizer packages."""
    import importlib.metadata

    def sha(path):
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def version(name):
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return None

    complete = None
    if args.complete_from:
        rp = completed_report_path(args.complete_from, key)
        base = rp[: -len(".gz")] if rp.endswith(".gz") else rp
        complete = {"report": sha(rp), "sidecar": sha(sidecar_path(base))}
    return {
        "library_sha256": library_sha256(),
        "engine_label": os.environ.get("BLG_ENGINE_LABEL", ""),
        "profile": PROFILE,
        "corpus_sha256": corpus_sha256(SNAPSHOTS[key]),
        "harness": {p: sha(os.path.join(_ROOT, p)) for p in HARNESS_SOURCES},
        "packages": {n: version(n) for n in ("jsonschema", "regress", "numpy",
                                              "tokenizers", "transformers")},
        "params": {k: getattr(args, k) for k in (
            "semantic", "generatable", "passes", "tokenizers", "hf_sample_every",
            "schema_budget_seconds", "max_steps", "max_gen_seconds", "chunk_size")},
        "complete_from": complete,
    }


def check_chunk_identity(cdir, ident):
    """A chunk directory is reused only when its identity record equals
    the current run's; a new one gets the record before its first run."""
    path = os.path.join(cdir, "identity.json")
    if os.path.isdir(cdir) and os.listdir(cdir):
        try:
            with open(path, encoding="utf-8") as f:
                old = json.load(f)
        except FileNotFoundError:
            raise SystemExit(f"chunk {cdir} has no identity record; "
                             "use a new --work-dir")
        if old != ident:
            diff = sorted(k for k in set(old) | set(ident) if old.get(k) != ident.get(k))
            raise SystemExit(f"chunk {cdir} was made with other inputs ("
                             + ", ".join(diff) + "); use a new --work-dir")
        return
    os.makedirs(cdir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ident, f, indent=1, sort_keys=True)


def run_chunk(args, key, start, count, work, ident):
    """Run one chunk to completion -> its report path. A finished chunk
    (its report exists) is reused when its identity record matches
    `ident` (check_chunk_identity), so an interrupted run resumes. A
    process that dies, or whose progress file stalls for
    CHUNK_STALL_SECONDS, is killed; the (file, tokenizer) in flight is
    recorded as a crash (generatable) or an engine fault (semantic) and
    the chunk reruns."""
    cdir = os.path.join(work, f"c{start:06d}")
    check_chunk_identity(cdir, ident)
    out_json = os.path.join(cdir, f"coverage_{key}.json")
    if os.path.exists(out_json):
        return out_json
    files = {n: os.path.join(cdir, f"{n}.json") for n in ("progress", "faults", "crashes")}
    for n in ("faults", "crashes"):
        if not os.path.exists(files[n]):
            with open(files[n], "w", encoding="utf-8") as f:
                json.dump({}, f)
    log_path = os.path.join(cdir, "log.txt")
    for _attempt in range(CHUNK_ATTEMPTS):
        if os.path.exists(files["progress"]):
            os.remove(files["progress"])
        with open(log_path, "a", encoding="utf-8") as log:
            proc = subprocess.Popen(chunk_argv(args, key, start, count, cdir, files),
                                    stdout=log, stderr=log,
                                    preexec_fn=_die_with_parent)
            last = time.monotonic()
            last_mtime = None
            reason = None
            while proc.poll() is None:
                time.sleep(2.0)
                try:
                    m = os.path.getmtime(files["progress"])
                except OSError:
                    m = None
                if m != last_mtime:
                    last_mtime, last = m, time.monotonic()
                elif time.monotonic() - last > CHUNK_STALL_SECONDS:
                    proc.kill()
                    proc.wait()
                    reason = f"no progress for {CHUNK_STALL_SECONDS:.0f} s (killed)"
                    break
        if reason is None and proc.returncode == 0 and os.path.exists(out_json):
            return out_json
        if reason is None:
            reason = f"process exit status {proc.returncode}"
        try:
            with open(files["progress"], encoding="utf-8") as f:
                mark = json.load(f)
        except (OSError, json.JSONDecodeError):
            raise SystemExit(f"chunk {cdir} failed ({reason}) with no progress mark")
        if mark.get("phase") == "generatable":
            path, entry_key = files["crashes"], f"{mark['file']}\t{mark['tokenizer']}"
            entry = {"stage": "pass" if str(mark.get("stage", "")).startswith("pass")
                     else mark.get("stage"),
                     "reason": f"{reason} during {mark.get('stage')}"}
        elif mark.get("phase") == "compile":
            path, entry_key = files["faults"], mark["file"]
            entry = {"phase": "compile", "reason": reason}
        else:
            path, entry_key = files["faults"], mark["file"]
            entry = reason
        with open(path, encoding="utf-8") as f:
            listed = json.load(f)
        if entry_key in listed:
            raise SystemExit(f"chunk {cdir}: {entry_key!r} failed again after being listed")
        listed[entry_key] = entry
        with open(path, "w", encoding="utf-8") as f:
            json.dump(listed, f, ensure_ascii=False, indent=1)
        print(f"[chunk {key} {start}] {entry_key!r}: {reason}; rerunning",
              file=sys.stderr, flush=True)
    raise SystemExit(f"chunk {cdir}: gave up after {CHUNK_ATTEMPTS} attempts")


def run_chunked(args, keys):
    """--jobs: every snapshot split into chunks of --chunk-size files, run
    as subprocesses --jobs at a time, then merged (merge_chunks)."""
    from concurrent.futures import ThreadPoolExecutor

    work_root = args.work_dir or os.path.join(args.out_dir, "chunks")
    for key in keys:
        snapshot = SNAPSHOTS[key]
        if not os.path.isdir(snapshot["data_dir"]):
            continue
        n = sum(1 for _ in iter_files(snapshot)) - args.skip_files
        if args.max_files:
            n = min(n, args.max_files)
        chunks = [(args.skip_files + i, min(args.chunk_size, n - i))
                  for i in range(0, max(n, 0), args.chunk_size)]
        work = os.path.join(work_root, key)
        os.makedirs(work, exist_ok=True)
        ident = json.loads(json.dumps(run_identity(args, key)))
        t0 = time.time()
        done = []
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = [pool.submit(run_chunk, args, key, s, c, work, ident) for s, c in chunks]
            for i, fut in enumerate(futs):
                done.append(fut.result())
                print(f"... [{key}] chunk {i + 1}/{len(chunks)} done, "
                      f"{time.time() - t0:.0f} s", file=sys.stderr, flush=True)
        crashes = []
        for p in done:
            cdir = os.path.dirname(p)
            for name in ("crashes", "faults"):
                fp = os.path.join(cdir, f"{name}.json")
                if os.path.exists(fp):
                    with open(fp, encoding="utf-8") as f:
                        for k, v in json.load(f).items():
                            crashes.append({"kind": name, "key": k, "entry": v})
        if crashes:
            print(json.dumps({"crashes": crashes}, ensure_ascii=False, indent=1),
                  file=sys.stderr)
        completed = None
        if args.complete_from:
            completed, _rows = load_report(completed_report_path(args.complete_from, key))
        merge_chunks(key, done, args.out_dir, completed)


def main():
    global PROGRESS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", choices=["repo", "maskbench", "both"],
                    default="both")
    ap.add_argument("--out-dir",
                    default=os.path.join(_BENCH_DIR, "coverage"),
                    help="directory for the committed report snapshots")
    ap.add_argument("--progress-every", type=int, default=1000)
    ap.add_argument("--semantic", action="store_true",
                    help="run session/generation/instance "
                         "execution checks for every compiled schema and "
                         "publish split metrics")
    ap.add_argument("--generatable", action="store_true",
                    help="run K generation passes per compiled schema on "
                         "each --tokenizers entry and publish the "
                         "aggregated schema outcomes")
    ap.add_argument("--passes", type=int, default=DEFAULT_PASSES,
                    help="passes per schema and tokenizer (generatable mode)")
    ap.add_argument("--tokenizers", default=",".join(GEN_TOKENIZERS),
                    help="comma-separated subset of "
                         + ", ".join(GEN_TOKENIZERS) + " (generatable mode)")
    ap.add_argument("--hf-sample-every", type=int, default=1,
                    help="run the HF tokenizers (gpt2, llama-3.1) on a "
                         "deterministic 1-in-N sample of the compiled "
                         "schemas; the others are skipped:not_sampled "
                         "(their masks cost far more than the byte "
                         "tokenizer's)")
    ap.add_argument("--schema-budget-seconds", type=float,
                    default=DEFAULT_SCHEMA_BUDGET_SECONDS,
                    help="wall budget per schema and tokenizer; the passes "
                         "left are recorded as skipped:schema_budget "
                         "(0 = none)")
    ap.add_argument("--max-files", type=int, default=0,
                    help="scan at most N files per snapshot (0 = all; "
                         "deterministic order, for sliced validation runs)")
    ap.add_argument("--skip-files", type=int, default=0,
                    help="skip the first N files of the deterministic "
                         "order per snapshot (0 = none; chunked drivers)")
    ap.add_argument("--engine-fault-list", default="",
                    help="JSON object {file: reason}: skip the semantic "
                         "phase for these files (the engine crashes or "
                         "hangs unrecoverably on them, e.g. a Zig panic); "
                         "the compile classification still runs and the "
                         "skip is reported in its own bucket")
    ap.add_argument("--crash-list", default="",
                    help="JSON object {\"file<TAB>tokenizer\": {stage, "
                         "reason}}: generatable results recorded as "
                         "engine_defect / CRASH without running (written "
                         "by the --jobs driver)")
    ap.add_argument("--progress-file", default="",
                    help="file that receives the engine step in flight "
                         "(read by the --jobs driver after a crash)")
    ap.add_argument("--jobs", type=int, default=1,
                    help="> 1: run chunks of --chunk-size files as that "
                         "many parallel subprocesses and merge them")
    ap.add_argument("--chunk-size", type=int, default=200)
    ap.add_argument("--work-dir", default="",
                    help="chunk directory of --jobs (default: "
                         "<out-dir>/chunks); finished chunks are reused")
    ap.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS,
                    help="generation step budget per schema (semantic mode)")
    ap.add_argument("--max-gen-seconds", type=float,
                    default=DEFAULT_MAX_GEN_SECONDS,
                    help="generation wall-time budget per schema in seconds "
                         "(semantic mode; 0 = no time cap)")
    ap.add_argument("--complete-from", default="", metavar="DIR",
                    help="complete the generatable report of the same engine "
                         "and parameters in DIR (coverage_<snapshot>.json[.gz] "
                         "and its sidecar): run the passes it records as "
                         "skipped:schema_budget, replay its invalid_document / "
                         "validator_error passes without a document to keep "
                         "their documents, keep everything else")
    ap.add_argument("--replay-pass", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--merge-chunks", nargs="+", default=None, metavar="JSON",
                    help="merge per-chunk coverage_<snapshot>.json reports "
                         "into one report and exit (chunked isolation "
                         "drivers; needs --snapshot repo|maskbench)")
    args = ap.parse_args()

    if args.replay_pass:
        replay_pass_main()
        return

    if args.merge_chunks:
        if args.snapshot == "both":
            ap.error("--merge-chunks needs --snapshot repo or maskbench")
        merge_chunks(args.snapshot, args.merge_chunks, args.out_dir)
        return

    keys = ["repo", "maskbench"] if args.snapshot == "both" else [args.snapshot]
    for key in keys:
        if not os.path.isdir(SNAPSHOTS[key]["data_dir"]):
            print(f"SKIP [{key}]: {SNAPSHOTS[key]['data_dir']} is missing",
                  file=sys.stderr)
            continue
    if not any(os.path.isdir(SNAPSHOTS[k]["data_dir"]) for k in keys):
        print(json.dumps({"status": "SKIP", "reason": "no corpus snapshot"}))
        return
    tok_keys = [t.strip() for t in args.tokenizers.split(",") if t.strip()]
    for t in tok_keys:
        if t not in GEN_TOKENIZERS:
            ap.error(f"unknown tokenizer {t!r}")
    if args.complete_from and (args.semantic or not args.generatable):
        ap.error("--complete-from needs --generatable and keeps the semantic "
                 "results of the completed report (no --semantic)")

    if args.jobs > 1:
        run_chunked(args, keys)
        return

    PROGRESS = Progress(args.progress_file or None)
    spec, tokenizer_info = make_tokenizer()
    ctx = make_context(spec)
    byte_ctx = None
    serializer = None
    semantic_meta = None
    fault_list = None
    gen_toks = None
    gen_opts = None
    gen_meta = None
    crash_list = None
    if args.engine_fault_list:
        with open(args.engine_fault_list, encoding="utf-8") as f:
            fault_list = json.load(f)
    if args.semantic:
        import importlib.metadata

        byte_ctx = ContextRecycler(byte_tokenizer_spec())
        serializer = load_value_serializer()
        semantic_meta = {
            "profile": PROFILE,
            "tokenizer": "byte-level synthetic (token id = byte value, "
                         "eos=256, pad=257)",
            "byte_ctx_recycle_every": BYTE_CTX_RECYCLE_EVERY,
            "max_steps": args.max_steps,
            "max_gen_seconds": args.max_gen_seconds,
            "max_files": args.max_files or None,
            "skip_files": args.skip_files or None,
            "engine_fault_list": report_path(args.engine_fault_list),
            "serializer": "python/bolorgir/serializer.py serialize_value",
            "validator": "jsonschema=="
                         + importlib.metadata.version("jsonschema"),
        }
    if args.generatable:
        import importlib.metadata

        gen_toks = [GenTokenizer(t) for t in tok_keys]
        for gt in gen_toks:
            if GEN_TOKENIZERS[gt.key] is not None:
                gt.sample_every = max(1, args.hf_sample_every)
            gt.info["sample_every"] = gt.sample_every
        gen_opts = {"passes": args.passes, "max_steps": args.max_steps,
                    "max_seconds": args.max_gen_seconds,
                    "replay_max_seconds": args.max_gen_seconds * REPLAY_TIME_FACTOR,
                    "schema_budget": args.schema_budget_seconds}
        if args.crash_list:
            with open(args.crash_list, encoding="utf-8") as f:
                crash_list = json.load(f)
        gen_meta = {
            "profile": PROFILE,
            "passes": args.passes,
            "tokenizers": {gt.key: gt.info for gt in gen_toks},
            "seed": GEN_SEED,
            "max_steps": args.max_steps,
            "max_gen_seconds": args.max_gen_seconds,
            "schema_budget_seconds": args.schema_budget_seconds,
            "context_recycle_every": BYTE_CTX_RECYCLE_EVERY,
            "validator": "jsonschema=="
                         + importlib.metadata.version("jsonschema")
                         + " over exact numbers (tests/exact_numbers.py),"
                         " `pattern` by regress=="
                         + importlib.metadata.version("regress")
                         + " (ECMA-262, unicode mode)",
            "crashes": [{"key": k, **v} for k, v in (crash_list or {}).items()],
        }
    os.makedirs(args.out_dir, exist_ok=True)
    summary = {"status": "OK", "reports": {}}
    try:
        for key in keys:
            snapshot = SNAPSHOTS[key]
            if not os.path.isdir(snapshot["data_dir"]):
                continue
            if args.complete_from:
                rp = completed_report_path(args.complete_from, key)
                prev, prev_rows = load_report(rp)
                check_completable(prev, snapshot, gen_meta)
                os.environ["BLG_ENGINE_LABEL"] = prev["meta"]["engine"]["commit"]
                with open(rp, "rb") as f:
                    rp_sha = hashlib.sha256(f.read()).hexdigest()
                old_gen = prev["meta"]["generatable"]
                key_meta = dict(old_gen)
                key_meta.pop("passes_sidecar", None)
                key_meta.pop("merged_from_chunks", None)
                key_meta["crashes"] = gen_meta["crashes"]
                key_meta["completed_from"] = {
                    "report": os.path.basename(rp),
                    "report_sha256": rp_sha,
                    "date": prev["meta"]["date"],
                    "crashes": old_gen.get("crashes", []),
                    "replay_max_gen_seconds": gen_opts["replay_max_seconds"],
                    "replay_alone": True,
                }
                if old_gen.get("completed_from"):
                    key_meta["completed_from"]["previous"] = old_gen["completed_from"]
                records, aggregate = complete_scan(
                    key, snapshot, prev, prev_rows, args.progress_every,
                    max_files=args.max_files, skip_files=args.skip_files,
                    gen_toks=gen_toks, gen_opts=gen_opts, crash_list=crash_list)
                report = build_report(key, snapshot, records, aggregate, tokenizer_info,
                                      prev["meta"].get("semantic"), key_meta)
            else:
                records, aggregate = scan(
                    key, snapshot, ctx, args.progress_every,
                    byte_ctx=byte_ctx, serializer=serializer,
                    max_steps=args.max_steps, max_files=args.max_files,
                    max_seconds=args.max_gen_seconds,
                    skip_files=args.skip_files, fault_list=fault_list,
                    gen_toks=gen_toks, gen_opts=gen_opts, crash_list=crash_list)
                report = build_report(key, snapshot, records, aggregate,
                                      tokenizer_info, semantic_meta, gen_meta)
            out_json = os.path.join(args.out_dir, f"coverage_{key}.json")
            out_md = os.path.join(args.out_dir, f"coverage_{key}.md")
            write_report_files(out_json, out_md, report)
            summary["reports"][key] = summary_entry(
                out_json, out_md, report, aggregate)
    finally:
        ctx.destroy()
        if byte_ctx is not None:
            byte_ctx.destroy()
        for gt in gen_toks or ():
            gt.destroy()
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
