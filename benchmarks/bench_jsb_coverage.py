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

Usage:
  python3 benchmarks/bench_jsb_coverage.py --snapshot both
  python3 benchmarks/bench_jsb_coverage.py --snapshot both --semantic \
      --out-dir benchmarks/coverage/spec-v1
  BOLORGIR_COVERAGE_PROFILE=spec-v1 python3 benchmarks/bench_jsb_coverage.py \
      --snapshot repo --semantic --max-files 200   # slicing for validation
"""

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import re
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


def make_tokenizer():
    """Pinned HF gpt2 -> TokenizerSpec; recorded synthetic fallback."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        import bolorgir as zc
        from transformers import AutoTokenizer

        hf_tok = AutoTokenizer.from_pretrained(
            TOKENIZER_NAME, revision=TOKENIZER_REVISION
        )
        bundle = zc.TokenizerBundle.from_hf(hf_tok)
        spec = TokenizerSpec(
            tokens=tuple(bundle.token_bytes(i) for i in range(bundle.vocab_size)),
            eos_ids=bundle.eos_ids,
            special_ids=bundle.special_ids,
        )
        info = {
            "name": TOKENIZER_NAME,
            "revision": TOKENIZER_REVISION,
            "vocab_size": bundle.vocab_size,
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
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
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


class ByteContextRecycler:
    """Owns the byte-tokenizer context of the semantic pass.

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

    def __init__(self):
        self.ctx = make_context(byte_tokenizer_spec())
        self.since_recycle = 0
        self.recycles = 0
        self.destroy_failures = 0

    def get(self):
        if self.since_recycle >= BYTE_CTX_RECYCLE_EVERY:
            self.recycle_now()
        return self.ctx

    def recycle_now(self):
        self._checked_destroy()
        self.ctx = make_context(byte_tokenizer_spec())
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
            print(f"[byte-ctx] destroy returned status {status} "
                  f"(failures: {self.destroy_failures})", file=sys.stderr,
                  flush=True)


def is_resource_limit_rec(srec):
    """True when a semantic record failed with RESOURCE_LIMIT at any
    stage - the signature of the accumulated-context artifact (see
    ByteContextRecycler), retried once on a fresh context by scan()."""
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
    fire). Registers a cancel flag on the context (NFR-2,
    blg_cancel_flag_set); a daemon thread raises it after `seconds`.
    Returns done(): resets the flag and DEREGISTERS it (the engine holds
    the pointer, so the buffer must not be freed while registered).
    seconds <= 0 disables the watchdog."""
    if not seconds or seconds <= 0:
        return lambda: None
    cancel = ctypes.c_uint8(0)
    blg.LIB.blg_cancel_flag_set(ctx_handle, ctypes.byref(cancel))
    stop = threading.Event()

    def watch():
        if not stop.wait(seconds):
            cancel.value = 1

    t = threading.Thread(target=watch, daemon=True)
    t.start()

    def done():
        stop.set()
        cancel.value = 0
        blg.LIB.blg_cancel_flag_set(ctx_handle, None)

    return done


def generate_document(session, max_steps, key_candidates=(),
                      string_candidates=(), max_seconds=0.0):
    """Completion-biased greedy generation over the byte tokenizer.

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

    def value_done():
        # the top frame's pending value (or the whole document) completed
        if stack:
            stack[-1]["phase"] = "sep"

    t_start = time.time()
    for step in range(max_steps):
        if max_seconds and step and step % 16 == 0 \
                and time.time() - t_start > max_seconds:
            return ("budget", bytes(doc),
                    f"time budget {max_seconds:.0f}s exhausted at step {step}")
        status, words = session.fill_mask_words()
        if status != blg.BLG_OK:
            if status == blg.BLG_ERR_DEAD_END:
                return "dead_end", bytes(doc), f"fill_mask dead end at step {step}"
            if status == blg.BLG_ERR_CANCELLED:
                return ("budget", bytes(doc),
                        "mask computation cancelled after exceeding the "
                        f"time budget at step {step}")
            return (f"error:{blg.STATUS_NAMES.get(status, status)}",
                    bytes(doc), f"fill_mask at step {step}")
        if session.can_end():
            status = session.finish()
            if status == blg.BLG_OK:
                return "ok", bytes(doc), ""
            return (f"error:{blg.STATUS_NAMES.get(status, status)}",
                    bytes(doc), "finish")
        allowed = [i for i in range(256) if (words[i // 32] >> (i % 32)) & 1]
        if not allowed:
            return "dead_end", bytes(doc), f"empty mask at step {step}"

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
            if (words[active[0] // 32] >> (active[0] % 32)) & 1:
                tok = active[0]
                active = active[1:]
            else:
                active = None  # the mask refuses the candidate: greedy rest
        if (tok is None and in_string
                and not (words[0x22 // 32] >> (0x22 % 32)) & 1):
            # the closing quote is refused: the string needs content the
            # greedy fill cannot guess (pattern/minLength) - spell the
            # schema-derived content candidates while the mask allows
            while True:
                if active_content is None:
                    if not content_queue:
                        break
                    active_content = content_queue.pop(0)
                if (words[active_content[0] // 32]
                        >> (active_content[0] % 32)) & 1:
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
        status = session.accept(tok)
        if status != blg.BLG_OK:
            return (f"error:{blg.STATUS_NAMES.get(status, status)}",
                    bytes(doc), f"accept at step {step}")
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
    return "budget", bytes(doc), f"step budget {max_steps} exhausted"


def semantic_check(byte_ctx, data, schema_doc, tests, max_steps, serializer,
                   max_seconds=0.0):
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

        for t in tests:
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
    return aggregate


def scan(snapshot_key, snapshot, ctx, progress_every=1000, byte_ctx=None,
         serializer=None, max_steps=DEFAULT_MAX_STEPS, max_files=0,
         max_seconds=DEFAULT_MAX_GEN_SECONDS, skip_files=0, fault_list=None):
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
        if data is not None:
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
                    try:
                        srec = semantic_check(byte_ctx.get(), data,
                                              schema_doc, tests, max_steps,
                                              serializer, max_seconds)
                        if is_resource_limit_rec(srec):
                            # accumulated-context artifact (see
                            # ByteContextRecycler): retry once on a fresh
                            # context; only a failure that reproduces
                            # there is genuine
                            byte_ctx.recycle_now()
                            srec = semantic_check(
                                byte_ctx.get(), data, schema_doc, tests,
                                max_steps, serializer, max_seconds)
                        byte_ctx.note_use()
                        rec["semantic"] = srec
                    except Exception as e:  # noqa: BLE001 - record all
                        rec["semantic"] = None
                        rec["semantic_error"] = f"{type(e).__name__}: {e}"
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


def build_report(snapshot_key, snapshot, records, aggregate, tokenizer_info,
                 semantic_meta=None):
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
            "commit": engine_commit(),
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
        "",
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
    return entry


def write_report_files(out_json, out_md, report):
    """Publish the report atomically: an interrupted write leaves the
    previous complete file (or nothing) instead of a truncated JSON that
    a chunked driver's resume check would mistake for a finished chunk."""
    tmp_json = out_json + ".tmp"
    tmp_md = out_md + ".tmp"
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
        f.write("\n")
    with open(tmp_md, "w", encoding="utf-8") as f:
        f.write(render_markdown(report))
    os.replace(tmp_json, out_json)
    os.replace(tmp_md, out_md)


def merge_chunks(snapshot_key, paths, out_dir):
    """Merge per-chunk coverage reports of one snapshot into a single
    report. Counter aggregation is replayed from the per-file records
    (aggregate_records). Chunk boundaries still change cache/recycler
    history, so generation outcomes under the time budgets are not
    guaranteed bit-identical to a monolithic run; used by chunked
    isolation drivers that skip engine-fault files. Duplicate file
    records across chunks are a hard error."""
    snapshot = SNAPSHOTS[snapshot_key]
    records = []
    parts = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            rep = json.load(f)
        records.extend(rep["per_file"])
        parts.append(rep)
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
        for counter in ("byte_ctx_recycles", "byte_ctx_destroy_failures"):
            aggregate["semantic"][counter] = sum(
                r["aggregate"].get("semantic", {}).get(counter, 0)
                for r in parts)
        semantic_meta = dict(parts[0]["meta"].get("semantic") or {})
        semantic_meta["merged_from_chunks"] = len(parts)
    _spec, tokenizer_info = make_tokenizer()
    report = build_report(snapshot_key, snapshot, records, aggregate,
                          tokenizer_info, semantic_meta)
    os.makedirs(out_dir, exist_ok=True)
    out_json = os.path.join(out_dir, f"coverage_{snapshot_key}.json")
    out_md = os.path.join(out_dir, f"coverage_{snapshot_key}.md")
    write_report_files(out_json, out_md, report)
    summary = {"status": "OK",
               "reports": {snapshot_key: summary_entry(
                   out_json, out_md, report, aggregate)}}
    print(json.dumps(summary, ensure_ascii=False, indent=1))


def main():
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
    ap.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS,
                    help="generation step budget per schema (semantic mode)")
    ap.add_argument("--max-gen-seconds", type=float,
                    default=DEFAULT_MAX_GEN_SECONDS,
                    help="generation wall-time budget per schema in seconds "
                         "(semantic mode; 0 = no time cap)")
    ap.add_argument("--merge-chunks", nargs="+", default=None, metavar="JSON",
                    help="merge per-chunk coverage_<snapshot>.json reports "
                         "into one report and exit (chunked isolation "
                         "drivers; needs --snapshot repo|maskbench)")
    args = ap.parse_args()

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

    spec, tokenizer_info = make_tokenizer()
    ctx = make_context(spec)
    byte_ctx = None
    serializer = None
    semantic_meta = None
    fault_list = None
    if args.semantic:
        import importlib.metadata

        byte_ctx = ByteContextRecycler()
        serializer = load_value_serializer()
        if args.engine_fault_list:
            with open(args.engine_fault_list, encoding="utf-8") as f:
                fault_list = json.load(f)
        semantic_meta = {
            "profile": PROFILE,
            "tokenizer": "byte-level synthetic (token id = byte value, "
                         "eos=256, pad=257)",
            "byte_ctx_recycle_every": BYTE_CTX_RECYCLE_EVERY,
            "max_steps": args.max_steps,
            "max_gen_seconds": args.max_gen_seconds,
            "max_files": args.max_files or None,
            "skip_files": args.skip_files or None,
            "engine_fault_list": args.engine_fault_list or None,
            "serializer": "python/bolorgir/serializer.py serialize_value",
            "validator": "jsonschema=="
                         + importlib.metadata.version("jsonschema"),
        }
    os.makedirs(args.out_dir, exist_ok=True)
    summary = {"status": "OK", "reports": {}}
    try:
        for key in keys:
            snapshot = SNAPSHOTS[key]
            if not os.path.isdir(snapshot["data_dir"]):
                continue
            records, aggregate = scan(
                key, snapshot, ctx, args.progress_every,
                byte_ctx=byte_ctx, serializer=serializer,
                max_steps=args.max_steps, max_files=args.max_files,
                max_seconds=args.max_gen_seconds,
                skip_files=args.skip_files, fault_list=fault_list)
            report = build_report(key, snapshot, records, aggregate,
                                  tokenizer_info, semantic_meta)
            out_json = os.path.join(args.out_dir, f"coverage_{key}.json")
            out_md = os.path.join(args.out_dir, f"coverage_{key}.md")
            write_report_files(out_json, out_md, report)
            summary["reports"][key] = summary_entry(
                out_json, out_md, report, aggregate)
    finally:
        ctx.destroy()
        if byte_ctx is not None:
            byte_ctx.destroy()
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
