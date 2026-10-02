"""T2: trace parity on the dict tokenizer and a larger corpus.

The reference generates valid documents; each document is tokenized several
ways (greedy cover + random covers with a fixed seed), including tokens
crossing field boundaries, whole/half escapes and byte-split UTF-8.
At each step:
  - the kernel mask contains the next trace token;
  - the kernel mask == reference oracle (computed over the byte prefix -
    the concatenation of accepted token bytes).
After the final token: can_end == True, eos in the mask, finish() OK,
the document is valid per the mini-validator and jsonschema (if installed).

The whole module is skipped until the core/package is built.
"""

import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpora
import reference as ref
from conftest import assert_masks_equal, backend_available  # noqa: E402

_ok, _why = backend_available(os.environ.get("BLG_TEST_BACKEND", "auto"))
if not _ok:
    pytest.skip(f"core unavailable: {_why}", allow_module_level=True)

SEED = 20260914
DOCS_PER_SCHEMA = 6
RANDOM_COVERS = 3


def sample_documents(lang, schema, n, seed):
    """Valid documents from the reference enumeration (deterministic sample)."""
    caps = dict(max_abs_int=20, max_string_len=2, max_items_extra=2)
    docs = sorted(ref.enumerate_language(lang, **caps))
    rng = random.Random(seed)
    rng.shuffle(docs)
    return docs[:n]


def greedy_cover(doc: bytes, spec) -> list[int] | None:
    """Greedy (longest) token cover; None if uncoverable."""
    by_first = {}
    for i, t in enumerate(spec.tokens):
        if t and i not in spec.eos_ids and i not in spec.special_ids:
            by_first.setdefault(t[0], []).append((t, i))
    out = []
    pos = 0
    while pos < len(doc):
        best = None
        for t, i in by_first.get(doc[pos], []):
            if doc.startswith(t, pos) and (best is None or len(t) > len(best[0])):
                best = (t, i)
        if best is None:
            return None
        out.append(best[1])
        pos += len(best[0])
    return out


def random_cover(doc: bytes, spec, rng) -> list[int] | None:
    by_first = {}
    for i, t in enumerate(spec.tokens):
        if t and i not in spec.eos_ids and i not in spec.special_ids:
            by_first.setdefault(t[0], []).append((t, i))
    out = []
    pos = 0
    while pos < len(doc):
        cands = [(t, i) for t, i in by_first.get(doc[pos], [])
                 if doc.startswith(t, pos)]
        if not cands:
            return None
        t, i = rng.choice(cands)
        out.append(i)
        pos += len(t)
    return out


def traces_for(doc: bytes, spec, seed):
    """Several traces per document: greedy + random (deterministic)."""
    traces = []
    g = greedy_cover(doc, spec)
    if g:
        traces.append(("greedy", g))
    rng = random.Random(seed)
    for k in range(RANDOM_COVERS):
        r = random_cover(doc, spec, rng)
        if r and r not in [t for _, t in traces]:
            traces.append((f"random{k}", r))
    return traces


def run_trace(backend, lang, schema, schema_name, doc, trace_name, seq, spec):
    constraint = backend.compile(schema)
    session = constraint.create_session()
    matcher = ref.Matcher(lang)
    prefix = b""
    try:
        for step, tid in enumerate(seq):
            kernel_mask = session.mask()
            oracle_mask = matcher.mask(b"", spec)
            assert tid in kernel_mask, (
                f"trace token not allowed by kernel\n"
                f"  schema: {schema_name}\n  doc: {doc!r}\n  trace: {trace_name}\n"
                f"  step {step}: tid={tid} bytes={spec.tokens[tid]!r}\n"
                f"  prefix: {prefix!r}")
            assert_masks_equal(kernel_mask, oracle_mask,
                               schema_name=schema_name, prefix=prefix,
                               seq=seq[:step], spec=spec)
            status = session.accept(tid)
            assert status == 0, (
                f"accept failed: schema={schema_name} trace={trace_name} "
                f"step={step} tid={tid} status={status}")
            prefix += spec.tokens[tid]
            assert matcher.feed(spec.tokens[tid]), (
                f"oracle rejected trace: schema={schema_name} doc={doc!r} "
                f"trace={trace_name} step={step} tid={tid}")

        assert prefix == doc, (
            f"trace decode != doc: {prefix!r} != {doc!r} ({trace_name})")
        assert session.can_end(), (
            f"can_end false after full doc: schema={schema_name} doc={doc!r}")
        kernel_mask = session.mask()
        assert set(spec.eos_ids) <= kernel_mask, (
            f"eos not allowed after full doc: schema={schema_name} doc={doc!r}")
        assert session.can_end() == matcher.can_end()
        assert session.finish() == 0, f"finish failed: {schema_name} {doc!r}"

        # final document: mini-validator + (if available) jsonschema
        assert ref.validate_document(doc, lang), (
            f"mini-validator rejected completed doc: {doc!r}")
        try:
            import jsonschema
        except ImportError:
            pass
        else:
            jsonschema.Draft202012Validator(schema).validate(
                json.loads(doc.decode("utf-8")))
    finally:
        session.close()
        constraint.release()


@pytest.mark.parametrize("case", corpora.TRACE,
                         ids=[c["name"] for c in corpora.TRACE])
def test_parity_traces(backend_factory, dict_tok, case, any_mode):
    backend = backend_factory(dict_tok, any_mode)
    lang = ref.compile_schema(case["schema"])
    docs = sample_documents(lang, case["schema"], DOCS_PER_SCHEMA,
                            SEED + sum(case["name"].encode("utf-8")))
    assert docs, case["name"]
    n_traces = 0
    for doc in docs:
        for trace_name, seq in traces_for(doc, dict_tok, SEED):
            n_traces += 1
            run_trace(backend, lang, case["schema"], case["name"],
                      doc, trace_name, seq, dict_tok)
    assert n_traces >= len(docs)
    print(f"\n{case['name']} [{any_mode}]: docs={len(docs)} traces={n_traces}")


def test_traces_escape_split_and_whole(backend_factory, dict_tok):
    """Escape sequence as one whole token and split in half."""
    schema = {"type": "string", "minLength": 1, "maxLength": 1}
    lang = ref.compile_schema(schema)
    doc = b'"\\n"'
    esc_whole = dict_tok.tokens.index(b"\\n")
    q, bs, n = 0x22, 0x5C, 0x6E
    backend = backend_factory(dict_tok, "lazy")
    run_trace(backend, lang, schema, "esc_whole", doc, "whole", [q, esc_whole, q], dict_tok)
    run_trace(backend, lang, schema, "esc_halves", doc, "halves", [q, bs, n, q], dict_tok)
    # xx: the \u00 piece as one token + hex byte by byte
    doc2 = b'"\\u001b"'
    u00 = dict_tok.tokens.index(b"\\u00")
    run_trace(backend, lang, schema, "u00_piece", doc2, "piece",
              [q, u00, 0x31, 0x62, q], dict_tok)
    run_trace(backend, lang, schema, "u00_bytes", doc2, "bytes",
              [q, bs, 0x75, 0x30, 0x30, 0x31, 0x62, q], dict_tok)


def test_traces_utf8_whole_and_split(backend_factory, dict_tok):
    schema = {"type": "string", "minLength": 1, "maxLength": 1}
    lang = ref.compile_schema(schema)
    doc = b'"' + "é".encode("utf-8") + b'"'
    whole = dict_tok.tokens.index("é".encode("utf-8"))
    q = 0x22
    backend = backend_factory(dict_tok, "lazy")
    run_trace(backend, lang, schema, "utf8_whole", doc, "whole", [q, whole, q], dict_tok)
    run_trace(backend, lang, schema, "utf8_split", doc, "split",
              [q, 0xC3, 0xA9, q], dict_tok)


def test_traces_cross_field_tokens(backend_factory, dict_tok):
    """Tokens crossing field boundaries (`{"`, `":"`, `",`)."""
    schema = corpora.ACTION_AMOUNT
    lang = ref.compile_schema(schema)
    doc = b'{"action":"buy","amount":10}'
    t_open = dict_tok.tokens.index(b'{"')
    t_act = None
    # manual cover: {" + action + ":" + "buy" + ", + "amount": + 10 + }
    seq = [t_open]
    pos = 2  # '{"' consumed
    rest = doc[pos:]
    # finish greedily by bytes, preferring dict pieces
    manual = []
    i = 0
    while i < len(rest):
        matched = False
        for piece in (b'":"', b'",', b'"buy"', b"10"):
            if rest.startswith(piece, i):
                manual.append(dict_tok.tokens.index(piece))
                i += len(piece)
                matched = True
                break
        if not matched:
            manual.append(rest[i])
            i += 1
    seq += manual
    assert b"".join(dict_tok.tokens[t] for t in seq) == doc
    backend = backend_factory(dict_tok, "lazy")
    run_trace(backend, lang, schema, "cross_field", doc, "manual", seq, dict_tok)


def test_traces_literals(backend_factory, dict_tok, any_mode):
    for case in corpora.LITERALS:
        lang = ref.compile_literals(case["strings"])
        backend = backend_factory(dict_tok, any_mode)
        for s in case["strings"]:
            doc = s.encode("utf-8")
            if not doc:
                # empty document: eos right away
                constraint = backend.compile_literals(case["strings"])
                session = constraint.create_session()
                try:
                    assert session.can_end()
                    assert set(dict_tok.eos_ids) <= session.mask()
                    assert session.finish() == 0
                finally:
                    session.close()
                    constraint.release()
                continue
            for trace_name, seq in traces_for(doc, dict_tok, SEED):
                constraint = backend.compile_literals(case["strings"])
                session = constraint.create_session()
                matcher = ref.Matcher(lang)
                try:
                    for tid in seq:
                        assert tid in session.mask(), (
                            f"{case['name']} {s!r} trace={trace_name} tid={tid}")
                        assert session.accept(tid) == 0
                        assert matcher.feed(dict_tok.tokens[tid])
                    assert session.can_end() == matcher.can_end()
                    assert session.finish() == 0
                finally:
                    session.close()
                    constraint.release()
