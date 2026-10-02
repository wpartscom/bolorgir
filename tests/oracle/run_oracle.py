"""Oracle harness: bolorgir vs the pinned JSON Schema Test Suite + jsonschema.

P0 infrastructure (ROADMAP section 5, P0 item 4; A8.3). No engine behavior
change. Loads the vendored Test Suite snapshot (tests/oracle/external/,
pinned in tests/oracle/pin.json), compiles each schema with bolorgir, feeds
each instance byte-exactly, and compares the accept/reject verdict with both
the suite expectation and the pinned `jsonschema` validator.

Dialect coverage: all
five dialects of the dialect matrix (draft-04, draft-06, draft-07, 2019-09,
2020-12) are wired in DRAFTS. The suite tags the dialect by directory; the
engine selects the dialect by the root `$schema` (docs/dialect-matrix.md
section 1), so the harness injects the draft's identifier into root object
schemas that do not carry one (`dialect_source` is recorded per row).
Suite remotes for every dialect are vendored under remotes/ (draft-specific
subdirectories included) and form one immutable registry snapshot keyed by
retrieval URI.

Serialization (R6): under the canonical-v1 profile the harness keeps the
frozen path - json.load/json.dumps canonical compact form (numeric lexemes
normalized by binary64, key order = data order). Under spec-v1 the harness
uses the value-preserving serializer python/bolorgir/serializer.py
(`serialize_value`): schema-driven key order per semantics-spec-v1 4.3 and
numeric lexemes kept verbatim (the suite file is re-parsed with
parse_int/parse_float hooks), so minimum/maximum/multipleOf boundaries are
fed to the engine as exact decimal lexemes, not as a binary64
reserialization. The `jsonschema` validator oracle still works on binary64
floats; rows that exercise a decimal boundary whose lexemes are not exactly
representable in binary64 are marked `decimal.sensitive` and counted
separately - a binary64 reserialization is never presented as an
independent exact-decimal check. For simple root-level numeric-bound cases
an independent Decimal oracle verdict is recorded as `decimal.oracle`.

Outcomes per test row:
- PASS        compiled; engine verdict == suite expectation (validator
              agreement recorded; a validator exception does not fail a row);
- SKIPPED     schema refused at compile time (UNSUPPORTED_FEATURE,
              INVALID_SCHEMA, RESOURCE_LIMIT, ...) - the refusal status,
              message and JSON pointer are recorded, plus a classification
              against the documented refusal list of
              docs/supported_features.md section 1a
              (by_design_*/documented_refusal/budget_guard/needs_review);
              a refusal is never coverage and never a failure;
- ENGINE_ERROR compiled, but the feed hit a non-verdict status (a runtime
              resource limit); deterministic, listed separately;
- SERIALIZATION_INCOMPATIBLE
              the value-preserving serializer cannot express the instance
              in the byte language (lone surrogates, duplicate keys, ...);
              a protocol artifact bucketed apart from verdicts (section 7
              of the serializer contract); expected to be empty on the
              non-optional suite;
- MISMATCH    compiled and fed, but the verdicts disagree (class recorded);
- KNOWN_GAP   a MISMATCH/ENGINE_ERROR row matching tests/oracle/known_gaps.json
              (documented canonical-v1 gaps, each with a reason).

Growth path (P1 item 7): new profiles/keywords only add data - a new draft
directory in DRAFTS, a new --profile value, or fewer known-gaps entries.
The comparison and reporting code does not change.

Usage: python3 tests/oracle/run_oracle.py [--drafts draft2020-12]
       [--include glob] [--optional] [--profile canonical-v1] [--strict]
"""

from __future__ import annotations

import argparse
import ctypes
import datetime
import fnmatch
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
from decimal import Decimal, InvalidOperation

_HERE = os.path.dirname(os.path.abspath(__file__))
_TESTS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TESTS)
sys.path.insert(0, _TESTS)

import reference  # noqa: E402

SUITE_ROOT = os.path.join(_HERE, "external", "JSON-Schema-Test-Suite")
PIN_PATH = os.path.join(_HERE, "pin.json")
KNOWN_GAPS_PATH = os.path.join(_HERE, "known_gaps.json")
RESULTS_DIR = os.path.join(_HERE, "results")
SERIALIZER_PATH = os.path.join(_ROOT, "python", "bolorgir", "serializer.py")

# Draft key -> (suite subdirectory, jsonschema validator class name,
# root $schema identifier). The suite tags the dialect by directory; the
# engine selects it by the root $schema value (dialect-matrix section 1).
DRAFTS = {
    "draft4": ("tests/draft4", "Draft4Validator",
               "http://json-schema.org/draft-04/schema#"),
    "draft6": ("tests/draft6", "Draft6Validator",
               "http://json-schema.org/draft-06/schema#"),
    "draft7": ("tests/draft7", "Draft7Validator",
               "http://json-schema.org/draft-07/schema#"),
    "draft2019-09": ("tests/draft2019-09", "Draft201909Validator",
                     "https://json-schema.org/draft/2019-09/schema#"),
    "draft2020-12": ("tests/draft2020-12", "Draft202012Validator",
                     "https://json-schema.org/draft/2020-12/schema#"),
}
DEFAULT_DRAFTS = ("draft2020-12",)
# Engine-side default dialect when the root $schema is absent
# (dialect-matrix section 1); schemas of this draft need no injection.
DEFAULT_DIALECT = "draft2020-12"

# Compile refusals with these statuses produce SKIPPED rows.
REFUSAL_STATUSES = {
    "UNSUPPORTED_FEATURE", "INVALID_SCHEMA", "UNSATISFIABLE_CONSTRAINT",
    "RESOURCE_LIMIT", "UNSUPPORTED_TOKENIZER",
}

# Keywords whose refusal is documented in docs/supported_features.md
# section 1a ("Refused with UNSUPPORTED_FEATURE and a pointer") or in
# docs/dialect-matrix.md section 1 (custom metaschemas / $schema). A
# SKIPPED row whose refusal pointer contains one of these segments is a
# documented_refusal; anything else is needs_review (never silent).
DOCUMENTED_REFUSAL_KEYWORDS = frozenset({
    "$schema",
    "$ref", "$dynamicRef", "$recursiveRef", "$anchor", "$dynamicAnchor",
    "$vocabulary", "$id", "id",
    "unevaluatedProperties", "unevaluatedItems",
    "patternProperties", "propertyNames", "not", "oneOf", "allOf", "anyOf",
    "if", "then", "else", "enum", "const", "contains",
    "items", "prefixItems", "additionalItems",
    "dependentRequired", "dependentSchemas", "dependencies",
    "multipleOf", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum",
})

# Numeric boundary keywords (exact-decimal layer, semantics-spec-v1 4.4).
NUMERIC_BOUND_KEYWORDS = frozenset({
    "minimum", "maximum", "multipleOf",
    "exclusiveMinimum", "exclusiveMaximum",
})


def load_pin() -> dict:
    with open(PIN_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def suite_available() -> bool:
    return all(os.path.isdir(os.path.join(SUITE_ROOT, sub))
               for sub, _, _ in DRAFTS.values())


def engine_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_ROOT,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:
        return None


def canonical_serialize(value) -> bytes:
    """Canonical compact serialization (whitespace-free UTF-8; key order and
    values preserved, number lexemes normalized by json - same convention as
    MaskBench --compact and tests/blg_ctypes.py). Frozen for canonical-v1."""
    return json.dumps(value, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def byte_tokenizer_spec() -> "reference.TokenizerSpec":
    """Byte-level tokenizer, same as tests/conftest.py make_byte_tokenizer:
    one token per byte (id 0..255), eos=256, pad=257. Feeding a document
    byte-by-byte exercises the engine independently of any real vocabulary."""
    tokens = tuple(bytes([i]) for i in range(256)) + (b"", b"")
    return reference.TokenizerSpec(tokens=tokens, eos_ids=(256,),
                                   special_ids=(257,)).validate()


# ---------------------------------------------------------------------------
# Value-preserving serializer (spec-v1 feed path; R6)
# ---------------------------------------------------------------------------

def load_value_serializer():
    """Load python/bolorgir/serializer.py standalone (pure stdlib, imports
    nothing from the bolorgir package). None when unavailable - the harness
    then keeps the canonical compact path and says so in the report."""
    if not os.path.exists(SERIALIZER_PATH):
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "blg_value_serializer", SERIALIZER_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # the lexeme/pair hooks are module-level implementation details the
        # harness relies on to re-parse the suite with numeric lexemes kept
        mod._Lexeme, mod._Pairs
        return mod
    except Exception:
        return None


def make_lexeme_parser(serializer_mod):
    """-> parse(text) like json.loads but with every number kept as the
    serializer's verbatim lexeme type. Objects are converted to plain dicts
    (insertion order preserved, duplicates collapsed exactly like json.load)
    so the structures support both dict access and serialize_value."""
    Pairs = serializer_mod._Pairs
    Lexeme = serializer_mod._Lexeme

    def convert(node):
        if isinstance(node, Pairs):
            return {k: convert(v) for k, v in node}
        if isinstance(node, list):
            return [convert(v) for v in node]
        return node

    def parse(text):
        raw = json.loads(text, object_pairs_hook=Pairs,
                         parse_int=Lexeme, parse_float=Lexeme)
        return convert(raw)

    return parse, Lexeme


def serializer_order_risk(schema, draft: str) -> bool:
    """True when the schema carries a local reference with in-place
    applicator siblings under the 2019-09/2020-12 sibling rule (P6b).
    serializer.py resolves a $ref for the key order and drops the
    siblings' properties, so its order can diverge from the compiled comb
    node's order (semantics-spec-v1 4.3); the harness feeds such cases in
    data order (canonical compact) and marks the rows instead of guessing
    an order it cannot derive. In draft-04..07 siblings next to $ref are
    ignored (dialect-matrix section 3), so the resolved reference IS the
    node's order and there is no divergence."""
    if draft not in ("draft2019-09", "draft2020-12"):
        return False
    if isinstance(schema, dict):
        if any(r in schema
               for r in ("$ref", "$dynamicRef", "$recursiveRef")) and any(
                   k in schema for k in ("properties", "patternProperties",
                                         "allOf")):
            return True
        return any(serializer_order_risk(v, draft)
                   for v in schema.values())
    if isinstance(schema, list):
        return any(serializer_order_risk(v, draft) for v in schema)
    return False


def inject_dialect(schema, draft: str):
    """-> (schema, dialect_source). The suite tags the dialect by directory;
    the engine selects it by the root $schema (dialect-matrix section 1), so
    the harness injects the draft's identifier into root object schemas that
    do not carry one. A schema with its own $schema keeps it ("schema" -
    e.g. the suite's 2019-09/2020-12 files). Boolean root schemas (draft-06+)
    cannot carry $schema and compile under the profile default dialect
    ("default"); true/false semantics are dialect-independent."""
    if isinstance(schema, dict):
        if "$schema" in schema:
            return schema, "schema"
        if draft == DEFAULT_DIALECT:
            return schema, "default"
        out = dict(schema)
        out["$schema"] = DRAFTS[draft][2]
        return out, "harness"
    return schema, "default"


# ---------------------------------------------------------------------------
# Exact-decimal layer (R6): sensitivity marking + independent Decimal oracle
# ---------------------------------------------------------------------------

def _is_number(v, lexeme_cls) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    return lexeme_cls is not None and isinstance(v, lexeme_cls)


def _as_decimal(v, lexeme_cls) -> Decimal | None:
    if not _is_number(v, lexeme_cls):
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _binary64_exact(v, lexeme_cls) -> bool:
    """True when the numeric value survives the json.load binary64 round trip
    unchanged; False when float parsing loses decimal information (the case
    where a reserialization must not count as an exact-decimal check)."""
    d = _as_decimal(v, lexeme_cls)
    if d is None:
        return True
    try:
        return Decimal(float(d)) == d
    except (OverflowError, ValueError):
        return False


def _walk_values(node, lexeme_cls):
    """Yield every scalar number in a parsed JSON value (dict/list walk)."""
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk_values(v, lexeme_cls)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_values(v, lexeme_cls)
    elif _is_number(node, lexeme_cls):
        yield node


def _walk_bound_keywords(node, lexeme_cls):
    """Yield (keyword, value) for numeric bound keywords anywhere in the
    schema. draft-04 boolean exclusiveMinimum/Maximum carry no number and
    are skipped (their bound is the sibling minimum/maximum)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in NUMERIC_BOUND_KEYWORDS and _is_number(v, lexeme_cls):
                yield k, v
            yield from _walk_bound_keywords(v, lexeme_cls)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_bound_keywords(v, lexeme_cls)


def decimal_bound_verdict(schema, data, draft: str, lexeme_cls):
    """Independent exact-decimal verdict for the simple root-level shape:
    a numeric instance against root minimum/maximum/exclusive*/multipleOf.
    -> True/False, or None for anything outside that shape (nested bounds,
    non-number instance, non-numeric values)."""
    if not isinstance(schema, dict):
        return None
    if not any(k in schema for k in NUMERIC_BOUND_KEYWORDS):
        return None
    d = _as_decimal(data, lexeme_cls)
    if d is None:
        return None
    lo = _as_decimal(schema.get("minimum"), lexeme_cls)
    lo_strict = False
    hi = _as_decimal(schema.get("maximum"), lexeme_cls)
    hi_strict = False
    if draft == "draft4":
        # draft-04: exclusiveMinimum/Maximum are boolean modifiers of the
        # sibling bound (dialect-matrix section 8 normalization).
        if schema.get("exclusiveMinimum") is True and lo is not None:
            lo_strict = True
        if schema.get("exclusiveMaximum") is True and hi is not None:
            hi_strict = True
    else:
        emin = _as_decimal(schema.get("exclusiveMinimum"), lexeme_cls)
        if emin is not None and (lo is None or emin >= lo):
            lo, lo_strict = emin, True
        emax = _as_decimal(schema.get("exclusiveMaximum"), lexeme_cls)
        if emax is not None and (hi is None or emax <= hi):
            hi, hi_strict = emax, True
    mult = _as_decimal(schema.get("multipleOf"), lexeme_cls)
    try:
        if lo is not None and not (d > lo if lo_strict else d >= lo):
            return False
        if hi is not None and not (d < hi if hi_strict else d <= hi):
            return False
        if mult is not None:
            if mult == 0:
                return None
            # exact integer arithmetic: scale both decimals to integers and
            # divide with Python big ints (a fixed-precision Decimal division
            # rounds long quotients, e.g. 1e308 / 0.123456789).
            scale = max(-d.as_tuple().exponent,
                        -mult.as_tuple().exponent, 0)

            def scaled_int(x):
                t = x.as_tuple()
                coeff = 0
                for dig in t.digits:
                    coeff = coeff * 10 + dig
                coeff = -coeff if t.sign else coeff
                return coeff * 10 ** (t.exponent + scale)

            if scaled_int(d) % scaled_int(mult) != 0:
                return False
    except (InvalidOperation, OverflowError, ZeroDivisionError):
        return None
    return True


def decimal_row_info(schema, data, draft: str, lexeme_cls,
                     engine_exact: bool) -> dict | None:
    """-> decimal classification for a fed row, or None when the row does
    not exercise a numeric boundary (no bound keyword, or no numeric value
    in the instance)."""
    bounds = sorted({k for k, _ in _walk_bound_keywords(schema, lexeme_cls)})
    if not bounds:
        return None
    bound_values = [v for _, v in _walk_bound_keywords(schema, lexeme_cls)]
    data_numbers = list(_walk_values(data, lexeme_cls))
    if not data_numbers:
        return None
    inexact = sorted({str(v) for v in bound_values + data_numbers
                      if not _binary64_exact(v, lexeme_cls)})
    info = {
        "bounds": bounds,
        "sensitive": bool(inexact),
        "binary64_inexact_lexemes": inexact[:8],
        "engine_check": ("exact decimal (lexeme-preserving schema and feed)"
                         if engine_exact else
                         "binary64 reserialization (canonical compact)"),
        "validator_check": "binary64 floats (jsonschema)",
    }
    oracle = decimal_bound_verdict(schema, data, draft, lexeme_cls)
    if oracle is not None:
        info["oracle"] = oracle
    return info


# ---------------------------------------------------------------------------
# Suite loading
# ---------------------------------------------------------------------------

def iter_cases(drafts, include=None, exclude=None, include_optional=False,
               lexeme_parse=None):
    """Yield {draft, file, case, schema, tests, schema_lex, tests_lex} in
    deterministic order. The _lex fields carry verbatim numeric lexemes and
    are None when lexeme_parse is None."""
    for draft in drafts:
        sub, _, _ = DRAFTS[draft]
        base = os.path.join(SUITE_ROOT, sub)
        names = sorted(os.listdir(base))
        if include_optional and os.path.isdir(os.path.join(base, "optional")):
            names += ["optional/" + n for n in
                      sorted(os.listdir(os.path.join(base, "optional")))]
        for name in names:
            if not name.endswith(".json"):
                continue
            if include and not any(fnmatch.fnmatch(name, p) for p in include):
                continue
            if exclude and any(fnmatch.fnmatch(name, p) for p in exclude):
                continue
            path = os.path.join(base, name)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            groups = json.loads(text)
            groups_lex = lexeme_parse(text) if lexeme_parse else None
            for gi, group in enumerate(groups):
                yield {
                    "draft": draft,
                    "file": name,
                    "case": group.get("description", ""),
                    "schema": group["schema"],
                    "tests": group["tests"],
                    "schema_lex": (groups_lex[gi]["schema"]
                                   if groups_lex is not None else None),
                    "tests_lex": (groups_lex[gi]["tests"]
                                  if groups_lex is not None else None),
                }


# ---------------------------------------------------------------------------
# Engine interaction (ctypes backend; only zig-out/lib/libbolorgir.so needed)
# ---------------------------------------------------------------------------

def pointer_for_offset(data: bytes, offset: int) -> str | None:
    """Best-effort RFC 6901 JSON pointer of the value containing byte
    `offset` in compact JSON text. canonical-v1 compile errors carry
    schema_offset but no json_pointer; the harness derives the pointer so
    that every refusal is recorded with one ("" is the root)."""
    if offset < 0 or offset >= len(data):
        return None

    def skip_string(i):
        i += 1
        while i < len(data):
            b = data[i]
            if b == 0x5C:
                i += 2
            elif b == 0x22:
                return i + 1
            else:
                i += 1
        return i

    def read_string(i):
        j = skip_string(i)
        return json.loads(data[i:j].decode("utf-8")), j

    def walk(i, path):
        # -> (end_index, pointer or None); compact text: no whitespace
        b = data[i]
        if b == 0x7B:  # object
            i += 1
            first = True
            while True:
                if data[i] == 0x7D:
                    return i + 1, path
                if not first:
                    i += 1  # comma
                first = False
                key, kend = read_string(i)
                if i <= offset < kend:
                    return kend, path + [key]
                j, found = walk(kend + 1, path + [key])  # skip ':'
                if found is not None:
                    return j, found
                i = j
        if b == 0x5B:  # array
            i += 1
            n = 0
            first = True
            while True:
                if data[i] == 0x5D:
                    return i + 1, path
                if not first:
                    i += 1  # comma
                first = False
                j, found = walk(i, path + [str(n)])
                if found is not None:
                    return j, found
                i = j
                n += 1
        if b == 0x22:  # string
            j = skip_string(i)
            return j, path if i <= offset < j else None
        j = i  # number / true / false / null
        while j < len(data) and data[j] not in b",}]":
            j += 1
        return j, path if i <= offset < j else None

    _, found = walk(0, [])
    if found is None:
        return None
    return "/" + "/".join(
        seg.replace("~", "~0").replace("/", "~1") for seg in found
    ) if found else ""


def build_remote_registry():
    """P5 (ADR-0006 D5, ADR-0008): the suite remotes as an immutable
    registry snapshot for the engine's external-$ref resolution. Every
    remotes/**/*.json file is keyed by its retrieval URI
    "http://localhost:1234/<relpath>" (the suite's remote base); the
    draft-specific subdirectories (remotes/draft6/, draft7/,
    remotes/draft2019-09/, remotes/draft2020-12/, ...) are part of the walk,
    so one snapshot serves every dialect. Returns the snapshot bytes, or
    None when the remotes are not vendored (fetch_suite.sh checks out only
    tests/; check out remotes/ at the pinned commit to enable refRemote
    coverage)."""
    remotes = os.path.join(SUITE_ROOT, "remotes")
    if not os.path.isdir(remotes):
        return None
    docs = {}
    for dirpath, _dirnames, filenames in os.walk(remotes):
        for name in sorted(filenames):
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, remotes).replace(os.sep, "/")
            with open(path, "r", encoding="utf-8") as f:
                docs["http://localhost:1234/" + rel] = json.load(f)
    if not docs:
        return None
    snap = {"version": "suite-remotes-23.2.0", "documents": docs}
    return json.dumps(snap, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def compile_schema(zg, ctx, data: bytes, profile: bytes,
                   registry: bytes | None = None):
    """-> (grammar, None) | (None, refusal). Mirrors blg_ctypes.compile_schema
    but takes pre-serialized schema bytes and the profile as parameters
    (canonical-v1 today, spec-v1 later) and exposes the refusal JSON
    pointer."""
    req = zg.ZgCompileRequest()
    req.struct_size = ctypes.sizeof(zg.ZgCompileRequest)
    req.kind = zg.BLG_CONSTRAINT_JSON_SCHEMA
    req.profile = profile
    buf = (ctypes.c_uint8 * max(len(data), 1)).from_buffer_copy(data or b"\0")
    req.data = buf
    req.data_len = len(data)
    rbuf = None
    if registry is not None:
        rbuf = (ctypes.c_uint8 * len(registry)).from_buffer_copy(registry)
        req.registry_data = rbuf
        req.registry_data_len = len(registry)
    out = ctypes.c_void_p()
    err = zg.new_error()
    status = zg.LIB.blg_compile(ctx.handle, ctypes.byref(req),
                                ctypes.byref(out), ctypes.byref(err))
    if status == zg.BLG_OK:
        return out, None
    pointer = err.json_pointer.split(b"\0", 1)[0].decode("utf-8", "replace")
    source = "core"
    if not pointer:
        pointer = pointer_for_offset(data, err.schema_offset)
        source = "derived_from_schema_offset"
    refusal = {
        "status": zg.STATUS_NAMES.get(status, f"status={status}"),
        "pointer": pointer,
        "pointer_source": source,
        "message": zg.err_text(err),
        "schema_offset": err.schema_offset,
    }
    refusal["classification"] = classify_refusal(refusal)
    return None, refusal


def classify_refusal(refusal: dict) -> str:
    """Bucket a compile refusal against the documented rules (R6 task 2 -
    a SKIP always carries a reason, never a silent divergence):
    - by_design_unsatisfiable / by_design_invalid_schema: the suite's
      unsatisfiable and invalid schemas, and dialect value-form rules
      (dialect-matrix section 2 rule 1);
    - budget_guard: RESOURCE_LIMIT;
    - documented_refusal: UNSUPPORTED_FEATURE whose pointer names a
      keyword of the documented refusal list (supported_features 1a,
      dialect-matrix section 1) - the pointer is matched segment by
      segment, innermost first, so a refusal at
      /patternProperties/<pattern> classifies by its keyword;
    - needs_review: anything else - surfaced separately in the report."""
    st = refusal["status"]
    if st == "UNSATISFIABLE_CONSTRAINT":
        return "by_design_unsatisfiable"
    if st == "INVALID_SCHEMA":
        return "by_design_invalid_schema"
    if st in ("RESOURCE_LIMIT", "UNSUPPORTED_TOKENIZER"):
        return "budget_guard"
    if st == "UNSUPPORTED_FEATURE":
        pointer = refusal.get("pointer") or ""
        segments = [s.replace("~1", "/").replace("~0", "~")
                    for s in pointer.split("/") if s]
        for kw in reversed(segments):
            if kw in DOCUMENTED_REFUSAL_KEYWORDS:
                return "documented_refusal"
        return "needs_review"
    return "needs_review"


def feed_document(zg, session, doc: bytes):
    """Feed doc byte-exactly: mask bit check + accept per byte, then
    can_end + finish. -> (verdict, detail) with verdict in
    {"accept", "reject", "error:<STATUS>"}."""
    for i, b in enumerate(doc):
        status, words = session.fill_mask_words()
        if status != zg.BLG_OK:
            # DESIGN §1.9: an empty mask is reported as DEAD_END. Under
            # canonical-v1 dead-end-freedom makes it unreachable; under
            # spec-v1 a data-dependent doom (e.g. a uniqueItems duplicate
            # pinned by a literal tail) can empty the mask mid-document.
            # Either way the document has no completion: it is a reject,
            # not an engine error.
            if status == zg.BLG_ERR_DEAD_END:
                return "reject", f"fill_mask dead end at byte {i}"
            return (f"error:{zg.STATUS_NAMES.get(status, status)}",
                    f"fill_mask at byte {i}")
        if not (words[b // 32] >> (b % 32)) & 1:
            return "reject", f"byte {i} (0x{b:02x}) not in mask"
        status = session.accept(b)
        if status == zg.BLG_OK:
            continue
        name = zg.STATUS_NAMES.get(status, f"status={status}")
        if status in (zg.BLG_ERR_INVALID_TOKEN, zg.BLG_ERR_DEAD_END):
            return "reject", f"accept at byte {i}: {name}"
        return f"error:{name}", f"accept at byte {i}"
    if not session.can_end():
        return "reject", "can_end false after full document"
    status = session.finish()
    if status == zg.BLG_OK:
        return "accept", ""
    name = zg.STATUS_NAMES.get(status, f"status={status}")
    if status in (zg.BLG_ERR_INVALID_TOKEN, zg.BLG_ERR_DEAD_END):
        return "reject", f"finish: {name}"
    return f"error:{name}", "finish"


def validator_verdict(validator_cls, schema, data):
    """-> True | False | None (validator raised, e.g. unresolvable $ref)."""
    try:
        return bool(validator_cls(schema).is_valid(data))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def classify(engine: bool, suite: bool, validator) -> tuple[str, str | None]:
    """-> (outcome, mismatch_class) for a fed test."""
    if engine == suite:
        if validator is None:
            return "PASS", "validator_error"
        if validator == engine:
            return "PASS", None
        return "MISMATCH", "validator_vs_suite"
    if validator is None:
        return "MISMATCH", "engine_vs_suite_unverified"
    if validator == suite:
        return "MISMATCH", "engine_vs_both"
    return "MISMATCH", "fed_path_vs_suite"


def load_known_gaps() -> list[dict]:
    if not os.path.exists(KNOWN_GAPS_PATH):
        return []
    with open(KNOWN_GAPS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gaps"]


def match_known_gap(gaps, row) -> dict | None:
    for g in gaps:
        if g["file"] != row["suite_file"] or g["case"] != row["case"]:
            continue
        if g.get("draft") is not None and g["draft"] != row["draft"]:
            continue
        if g.get("test") is not None and g["test"] != row["test_index"]:
            continue
        if g.get("mismatch_class") and \
                g["mismatch_class"] != row.get("mismatch_class"):
            continue
        g["_used"] = True
        return g
    return None


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(drafts=DEFAULT_DRAFTS, include=None, exclude=None,
        include_optional=False, profile="canonical-v1", use_known_gaps=True,
        limit=None) -> dict:
    import blg_ctypes as zg
    from jsonschema import validators as _validators

    pin = load_pin()
    gaps = load_known_gaps() if use_known_gaps else []
    # Gap entries may be scoped to a profile ("profile" field); unscoped
    # entries apply to every profile. A gap of another profile is neither
    # matched nor reported stale (the canonical gaps are fixed under
    # spec-v1; a spec-v1-only deviation never matches under canonical).
    gaps = [g for g in gaps if g.get("profile") in (None, profile)]

    # R6: the value-preserving serializer is the spec-v1 feed path (schema-
    # driven key order per semantics-spec-v1 4.3, numeric lexemes verbatim).
    # canonical-v1 stays on the frozen json.load/json.dumps compact path.
    value_serializer = None
    lexeme_parse = None
    lexeme_cls = None
    serializer_name = ("canonical-compact (json.load/json.dumps; numeric "
                       "lexemes normalized through binary64)")
    if profile == "spec-v1":
        value_serializer = load_value_serializer()
        if value_serializer is not None:
            lexeme_parse, lexeme_cls = make_lexeme_parser(value_serializer)
            serializer_name = ("value-preserving "
                               "python/bolorgir/serializer.py serialize_value "
                               "(schema-driven key order, verbatim numeric "
                               "lexemes)")

    spec = byte_tokenizer_spec()
    ctx = zg.Context(spec, zg.BLG_MODE_LAZY)
    # P5: external-$ref registry snapshot from the vendored suite remotes
    # (spec-v1 only; canonical-v1 never takes a registry). One snapshot
    # covers every dialect (remotes/draft*/ subdirectories included).
    registry = build_remote_registry() if profile == "spec-v1" else None

    multi_draft = len(drafts) > 1
    rows = []
    summary = {
        "rows": 0, "cases": 0,
        "by_outcome": {},
        "by_refusal_status": {},
        "by_refusal_classification": {},
        "by_mismatch_class": {},
        "by_file": {},
        "by_draft": {},
        "decimal_sensitive_rows": 0,
        "decimal_oracle_disagreements": 0,
        "serializer_order_fallbacks": 0,
    }
    try:
        for case in iter_cases(drafts, include, exclude, include_optional,
                               lexeme_parse=lexeme_parse):
            if limit is not None and summary["cases"] >= limit:
                break
            summary["cases"] += 1
            draft = case["draft"]
            fkey = f"{draft}:{case['file']}" if multi_draft else case["file"]
            fstats = summary["by_file"].setdefault(
                fkey, {"cases": 0, "compiled": 0, "refused": 0,
                       "pass": 0, "mismatch": 0, "known_gap": 0,
                       "engine_error": 0, "serialization_incompatible": 0})
            fstats["cases"] += 1
            dstats = summary["by_draft"].setdefault(
                draft, {"rows": 0, "by_outcome": {}, "by_refusal_status": {},
                        "by_refusal_classification": {}})
            validator_cls = getattr(_validators, DRAFTS[draft][1])

            schema_eng, dialect_source = inject_dialect(case["schema"], draft)
            schema_lex = None
            if lexeme_parse is not None and isinstance(case["schema_lex"],
                                                       dict):
                schema_lex, _ = inject_dialect(case["schema_lex"], draft)
            order_risk = (lexeme_parse is not None
                          and serializer_order_risk(case["schema"], draft))

            if schema_lex is not None:
                data, sstat = value_serializer.serialize_value(schema_lex,
                                                               None)
                if not sstat.ok:
                    # never silently weaken: note the fallback on every row
                    data = canonical_serialize(schema_eng)
                    schema_ser = ("canonical-fallback "
                                  f"({sstat.reason} at {sstat.pointer!r})")
                else:
                    schema_ser = "value-preserving"
            else:
                data = canonical_serialize(schema_eng)
                schema_ser = "canonical-compact"

            grammar, refusal = compile_schema(
                zg, ctx, data, profile.encode("ascii"), registry=registry)
            for ti, test in enumerate(case["tests"]):
                row = {
                    "draft": draft,
                    "suite_file": case["file"],
                    "case": case["case"],
                    "test_index": ti,
                    "test": test.get("description", ""),
                    "schema_compiled": refusal is None,
                    "suite": bool(test["valid"]),
                    "profile": profile,
                    "dialect_source": dialect_source,
                }
                if schema_ser not in ("value-preserving",
                                      "canonical-compact"):
                    row["schema_serialization"] = schema_ser
                if refusal is not None:
                    row.update(outcome="SKIPPED", engine=None, validator=None,
                               mismatch_class=None, refusal=refusal)
                else:
                    doc = None
                    incompatible = None
                    if lexeme_parse is not None and not order_risk:
                        # value-preserving feed; schema_lex may be None (a
                        # boolean root schema) - the serializer then keeps
                        # the data key order, values still verbatim.
                        doc, sstat = value_serializer.serialize_value(
                            case["tests_lex"][ti]["data"], schema_lex)
                        if not sstat.ok:
                            incompatible = (f"{sstat.reason} "
                                            f"(pointer {sstat.pointer!r})")
                            doc = None
                    else:
                        doc = canonical_serialize(test["data"])
                    if lexeme_parse is not None and order_risk:
                        row["serializer"] = (
                            "canonical-compact fallback: local reference "
                            "with applicator siblings - serializer key "
                            "order not derivable")
                        summary["serializer_order_fallbacks"] += 1
                    row["validator"] = validator_verdict(
                        validator_cls, case["schema"], test["data"])
                    if incompatible is not None:
                        row.update(outcome="SERIALIZATION_INCOMPATIBLE",
                                   engine=None, mismatch_class=None,
                                   detail="serialization-incompatible: "
                                          + incompatible)
                    else:
                        session = zg.Session(ctx, grammar)
                        try:
                            verdict, detail = feed_document(zg, session, doc)
                        finally:
                            session.destroy()
                        row["engine"] = verdict
                        if verdict.startswith("error:"):
                            row.update(outcome="ENGINE_ERROR",
                                       mismatch_class=None, detail=detail)
                        else:
                            outcome, mclass = classify(
                                verdict == "accept", row["suite"],
                                row["validator"])
                            row.update(outcome=outcome, mismatch_class=mclass)
                            if detail:
                                row["detail"] = detail
                    # R6 exact-decimal layer: never present a binary64
                    # reserialization as an independent decimal check.
                    dec = decimal_row_info(
                        schema_lex if schema_lex is not None
                        else case["schema"],
                        case["tests_lex"][ti]["data"]
                        if lexeme_parse is not None else test["data"],
                        draft, lexeme_cls,
                        engine_exact=(lexeme_parse is not None
                                      and not order_risk))
                    if dec is not None:
                        oracle = dec.pop("oracle", None)
                        if oracle is not None:
                            dec["oracle"] = oracle
                            dec["oracle_vs_suite"] = (
                                "agree" if oracle == row["suite"]
                                else "disagree")
                            if oracle != row["suite"]:
                                summary["decimal_oracle_disagreements"] += 1
                        row["decimal"] = dec
                        if dec["sensitive"]:
                            summary["decimal_sensitive_rows"] += 1
                gap = match_known_gap(gaps, row) if (
                    row["outcome"] in ("MISMATCH", "ENGINE_ERROR")) else None
                if gap is not None:
                    row["outcome"] = "KNOWN_GAP"
                    row["known_gap_reason"] = gap["reason"]
                rows.append(row)

                summary["rows"] += 1
                dstats["rows"] += 1
                oc = row["outcome"]
                summary["by_outcome"][oc] = summary["by_outcome"].get(oc, 0) + 1
                dstats["by_outcome"][oc] = dstats["by_outcome"].get(oc, 0) + 1
                if oc == "SKIPPED":
                    fstats["refused"] += 1
                    st = refusal["status"]
                    summary["by_refusal_status"][st] = \
                        summary["by_refusal_status"].get(st, 0) + 1
                    dstats["by_refusal_status"][st] = \
                        dstats["by_refusal_status"].get(st, 0) + 1
                    cl = refusal["classification"]
                    summary["by_refusal_classification"][cl] = \
                        summary["by_refusal_classification"].get(cl, 0) + 1
                    dstats["by_refusal_classification"][cl] = \
                        dstats["by_refusal_classification"].get(cl, 0) + 1
                else:
                    fstats["compiled"] += 1
                    key = {"PASS": "pass", "MISMATCH": "mismatch",
                           "KNOWN_GAP": "known_gap",
                           "ENGINE_ERROR": "engine_error",
                           "SERIALIZATION_INCOMPATIBLE":
                           "serialization_incompatible"}[oc]
                    fstats[key] += 1
                mc = row.get("mismatch_class")
                if mc:
                    summary["by_mismatch_class"][mc] = \
                        summary["by_mismatch_class"].get(mc, 0) + 1
            if grammar is not None:
                zg.grammar_release(grammar)

    finally:
        ctx.destroy()

    summary["known_gaps_unused"] = [
        {"file": g["file"], "case": g["case"], "test": g.get("test"),
         "draft": g.get("draft")}
        for g in gaps if not g.get("_used")
    ]
    for g in gaps:
        g.pop("_used", None)

    report = {
        "header": {
            "date_utc": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "engine_commit": engine_commit(),
            "library": zg.LIB._path,
            "profile": profile,
            "drafts": list(drafts),
            "dialect_selection": ("root $schema per suite directory "
                                  "(harness injection; absent $schema "
                                  "defaults to draft2020-12, dialect-matrix "
                                  "section 1)"),
            "serializer": serializer_name,
            "include_optional": include_optional,
            "suite": pin["suite"],
            "validator": {
                "package": "jsonschema",
                "pinned": pin["validator"]["version"],
                "actual": importlib.metadata.version("jsonschema"),
            },
            "known_gaps": len(gaps),
            "tokenizer": "byte-level (256 byte tokens + eos/special)",
        },
        "summary": summary,
        "rows": rows,
    }
    return report


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def markdown_summary(report: dict) -> str:
    h, s = report["header"], report["summary"]
    lines = [
        f"# Oracle report {h['date_utc']}",
        "",
        f"- suite: {h['suite']['repository']} @ {h['suite']['commit']}"
        f" ({h['suite']['release']})",
        f"- validator: jsonschema=={h['validator']['actual']}"
        f" (pinned {h['validator']['pinned']})",
        f"- engine: {h['engine_commit']} profile={h['profile']}"
        f" drafts={','.join(h['drafts'])}",
        f"- serializer: {h['serializer']}",
        f"- serializer order fallbacks (local ref + applicator siblings,"
        f" fed canonical-compact): {s['serializer_order_fallbacks']}",
        f"- decimal-sensitive rows (binary64-inexact lexemes at numeric"
        f" boundaries): {s['decimal_sensitive_rows']}",
        "",
        "| Outcome | Rows |",
        "|---|---:|",
    ]
    for k in ("PASS", "SKIPPED", "KNOWN_GAP", "ENGINE_ERROR",
              "SERIALIZATION_INCOMPATIBLE", "MISMATCH"):
        if s["by_outcome"].get(k):
            lines.append(f"| {k} | {s['by_outcome'][k]} |")
    if s.get("known_gaps_unused"):
        lines.append(f"| stale known-gaps entries | {len(s['known_gaps_unused'])} |")
    if len(h["drafts"]) > 1:
        lines += [
            "",
            "| Draft | Rows | PASS | SKIPPED | KNOWN_GAP | ENGINE_ERROR"
            " | SER_INCOMPAT | MISMATCH |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for d in h["drafts"]:
            st = s["by_draft"].get(d)
            if st is None:
                continue
            oc = st["by_outcome"]
            lines.append(
                f"| {d} | {st['rows']} | {oc.get('PASS', 0)}"
                f" | {oc.get('SKIPPED', 0)} | {oc.get('KNOWN_GAP', 0)}"
                f" | {oc.get('ENGINE_ERROR', 0)}"
                f" | {oc.get('SERIALIZATION_INCOMPATIBLE', 0)}"
                f" | {oc.get('MISMATCH', 0)} |")
    lines += ["", "| Refusal status | Rows |", "|---|---:|"]
    for k, v in sorted(s["by_refusal_status"].items(),
                       key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v} |")
    if s["by_refusal_classification"]:
        lines += ["", "| Refusal classification | Rows |", "|---|---:|"]
        for k, v in sorted(s["by_refusal_classification"].items(),
                           key=lambda kv: -kv[1]):
            lines.append(f"| {k} | {v} |")
    if s["by_mismatch_class"]:
        lines += ["", "| Mismatch class | Rows |", "|---|---:|"]
        for k, v in sorted(s["by_mismatch_class"].items(),
                           key=lambda kv: -kv[1]):
            lines.append(f"| {k} | {v} |")
    lines += [
        "",
        "| Suite file | Cases | Compiled | Refused | Pass | Known gap"
        " | Engine error | Ser incompat | Mismatch |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, st in sorted(s["by_file"].items()):
        lines.append(
            f"| {name} | {st['cases']} | {st['compiled']} | {st['refused']}"
            f" | {st['pass']} | {st['known_gap']} | {st['engine_error']}"
            f" | {st['serialization_incompatible']} | {st['mismatch']} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drafts", default=",".join(DEFAULT_DRAFTS),
                    help="comma-separated draft keys (see DRAFTS: "
                         + ",".join(DRAFTS) + ")")
    ap.add_argument("--include", action="append", default=None,
                    help="glob over suite file names (repeatable)")
    ap.add_argument("--exclude", action="append", default=None)
    ap.add_argument("--optional", action="store_true",
                    help="include the suite optional/ directory")
    ap.add_argument("--profile", default="canonical-v1")
    ap.add_argument("--limit", type=int, default=None,
                    help="stop after N schema groups (debugging)")
    ap.add_argument("--no-known-gaps", action="store_true")
    ap.add_argument("--json", default=None, help="report path "
                    "(default tests/oracle/results/oracle-<timestamp>.json)")
    ap.add_argument("--md", default=None, help="markdown summary path")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when any MISMATCH row remains")
    args = ap.parse_args(argv)

    for d in args.drafts.split(","):
        if d not in DRAFTS:
            print(f"ERROR: unknown draft key {d!r} (see DRAFTS)",
                  file=sys.stderr)
            return 2
    if not suite_available():
        print(f"SKIP: suite snapshot missing under {SUITE_ROOT}; "
              f"run tests/oracle/fetch_suite.sh", file=sys.stderr)
        return 2
    try:
        import blg_ctypes  # noqa: F401
    except ModuleNotFoundError as e:
        print(f"SKIP: {e}", file=sys.stderr)
        return 2

    report = run(drafts=tuple(args.drafts.split(",")),
                 include=args.include, exclude=args.exclude,
                 include_optional=args.optional, profile=args.profile,
                 use_known_gaps=not args.no_known_gaps, limit=args.limit)

    if args.json is None:
        os.makedirs(RESULTS_DIR, exist_ok=True)
        stamp = report["header"]["date_utc"].replace(":", "").replace("-", "")
        args.json = os.path.join(RESULTS_DIR, f"oracle-{stamp}.json")
    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    md = markdown_summary(report)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(md)
    print(md, end="")
    print(f"report: {args.json}")
    mismatches = report["summary"]["by_outcome"].get("MISMATCH", 0)
    return 1 if (args.strict and mismatches) else 0


if __name__ == "__main__":
    sys.exit(main())
