#!/usr/bin/env python3
"""t8a_load.py: a static load estimate of the Bolorgir participant of a
T8a run, without the engine. A scenario estimate, not a count of
guaranteed timeouts.

  python -I t8a_load.py --run-dir RUN --data DIR --tokenizer SNAPSHOT \\
      --t3 COVERAGE_JSON_GZ --out OUT_DIR [--calib DIR ...] [--files LIST]

  RUN        the T8a work dir: identity/manifest.json and streams.json.gz
             (read only); the corpus files, the tokenizer files and the
             serializer of this interpreter are checked against its
             manifest, a difference refuses
  DIR        the MaskBench data dir of the run
  SNAPSHOT   the pinned Llama 3.1 snapshot
  COVERAGE   T3 coverage_maskbench.json.gz of the same core (its
             meta.engine.library_sha256 must be the manifest's core): the
             compile status per file, and per instance the byte where the
             byte tokenizer was rejected ("byte N (0xHH) not in mask") or
             where fill_mask ended with RESOURCE_LIMIT or CANCELLED
  --calib    a MaskBench output dir of the Bolorgir participant of this
             protocol (per-step all_mask_us and t8a_streams/ records):
             the measured step times per position class
  --files    only these corpus files (one per line)

Per instance: the serializer of the run (bolorgir.serializer.serialize_value
against the file's schema) or its refusal, the token ids of the pinned
tokenizer (encode(text, add_special_tokens=False)), checked against the
reference streams (bytes and ids digests, lengths). Every step k (the mask
for the prefix of k tokens) gets a position class from the serialized
bytes: outside a string, or inside a key or a string value; a key by
whether its object is closed (additionalProperties false or bounded
propertyNames), a string value by the strongest bound the schema gives it
(enum/const, pattern, format, maxLength <= 16) or none. The schema walk
follows properties, patternProperties, additionalProperties, items,
prefixItems, local $ref, allOf (any member bounds), anyOf/oneOf and
if/then/else (every branch must bound); anything it cannot resolve counts
as unbounded. The steps an instance consumes: all its tokens for a valid
instance; for an invalid one up to the token holding the byte where T3
rejected it (the byte value is checked), all tokens without such a byte.
An instance where T3 ended with RESOURCE_LIMIT or CANCELLED marks its file
as a risk file (Github_easy---o89710: RESOURCE_LIMIT at byte 0 in T3, no
return of the first mask within 900 s in T8a). Taking the rejection byte
and the compile status of T3 (byte tokenizer) for the Llama tokenizer is
an assumption; the calibration files test it (the measured steps of
every calibration instance, the compile outcome of every file with a T8a
output), nothing more.

Writes OUT_DIR/files.jsonl.gz (per file), calib.json (per calibration
file: every measured step with its instance, index, class, microseconds
and whether its prefix repeats one an earlier instance measured),
problems.json, inputs.json (the command and the sha256 of every input) and
compile_checks.json (the T8a outcome of every calibration dir file against
its T3 compile status).
Exit 0, 1 when a stream differs from the reference, 2 refused.
"""

import argparse
import bisect
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time

PATTERN_CACHE = {}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def refuse(msg):
    print(f"refused: {msg}", file=sys.stderr)
    sys.exit(2)


def bytes_to_unicode():
    """The byte-level BPE alphabet (GPT-2, also Llama 3): byte -> char. The
    token bytes it gives are checked against every serialized instance."""
    bs = (list(range(ord("!"), ord("~") + 1)) + list(range(ord("\u00a1"), ord("\u00ac") + 1))
          + list(range(ord("\u00ae"), ord("\u00ff") + 1)))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, map(chr, cs)))


def ids_digest(ids):
    h = hashlib.sha256()
    for t in ids:
        h.update(int(t).to_bytes(4, "little"))
    return h.hexdigest()


# ---- the serialized bytes: string spans and their paths

def string_spans(b):
    """(open, close, kind, path) of every string of the JSON bytes b: open
    and close are the offsets of the quotes, kind "key" (path of its
    object) or "value" (path of the value)."""
    spans = []
    n = len(b)

    def ws(i):
        while i < n and b[i] in b" \t\r\n":
            i += 1
        return i

    def str_end(i):
        j = i + 1
        while b[j] != 0x22:
            j += 2 if b[j] == 0x5C else 1
        return j

    def value(i, path):
        i = ws(i)
        c = b[i]
        if c == 0x7B:
            i = ws(i + 1)
            if b[i] == 0x7D:
                return i + 1
            while True:
                i = ws(i)
                e = str_end(i)
                spans.append((i, e, "key", path))
                key = json.loads(b[i:e + 1].decode("utf-8"))
                i = ws(e + 1) + 1
                i = ws(value(i, path + (key,)))
                if b[i] == 0x2C:
                    i += 1
                    continue
                return i + 1
        if c == 0x5B:
            i = ws(i + 1)
            if b[i] == 0x5D:
                return i + 1
            k = 0
            while True:
                i = ws(value(i, path + (k,)))
                k += 1
                if b[i] == 0x2C:
                    i += 1
                    continue
                return i + 1
        if c == 0x22:
            e = str_end(i)
            spans.append((i, e, "value", path))
            return e + 1
        while i < n and b[i] not in b",]} \t\r\n":
            i += 1
        return i

    value(0, ())
    spans.sort()
    return spans


# ---- the schema walk

def resolve(root, ref):
    if not isinstance(ref, str):
        return None
    if ref in ("#", ""):
        return root
    if not ref.startswith("#/"):
        return None
    node = root
    from urllib.parse import unquote
    for part in ref[2:].split("/"):
        part = unquote(part).replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def pattern_matches(p, key):
    r = PATTERN_CACHE.get(p)
    if r is None:
        try:
            r = re.compile(p)
        except (re.error, TypeError):
            r = False
        PATTERN_CACHE[p] = r
    return True if r is False else bool(r.search(key))


def any_of(branches):
    """Alternatives; one unconstrained branch leaves the value unconstrained
    for every question asked here (bound, closed, child)."""
    if any(b is True for b in branches):
        return True
    return branches[0] if len(branches) == 1 else {"anyOf": branches}


class Walk:
    """Schema views of the instance paths of one file (memoized)."""

    BUDGET = 200_000

    def __init__(self, root):
        self.root = root
        self.budget = self.BUDGET
        self.views = {(): root}
        self.vlabels = {}
        self.klabels = {}

    def child(self, s, step, depth=0):
        """The view of the value at step (a key or an index) under the view
        s: {"allOf": [...]} of what applies, an anyOf for alternatives, True
        when nothing constrains it (also past the walk budget)."""
        self.budget -= 1
        if depth > 40 or self.budget <= 0 or not isinstance(s, dict):
            return s if isinstance(s, bool) and self.budget > 0 else True
        parts = []
        if isinstance(step, str):
            own = False
            props = s.get("properties")
            if isinstance(props, dict) and step in props:
                parts.append(props[step])
                own = True
            pp = s.get("patternProperties")
            if isinstance(pp, dict):
                for p, sub in pp.items():
                    if pattern_matches(p, step):
                        parts.append(sub)
                        own = True
            ap = s.get("additionalProperties")
            if not own and isinstance(ap, dict):
                parts.append(ap)
        else:
            pre = s.get("prefixItems")
            items = s.get("items")
            if isinstance(pre, list) and step < len(pre):
                parts.append(pre[step])
            elif isinstance(items, list):
                if step < len(items):
                    parts.append(items[step])
                elif isinstance(s.get("additionalItems"), dict):
                    parts.append(s["additionalItems"])
            elif isinstance(items, dict):
                parts.append(items)
        if "$ref" in s:
            t = resolve(self.root, s["$ref"])
            if t is not None:
                parts.append(self.child(t, step, depth + 1))
        for m in s.get("allOf") or []:
            parts.append(self.child(m, step, depth + 1))
        for kw in ("anyOf", "oneOf"):
            if isinstance(s.get(kw), list) and s[kw]:
                parts.append(any_of([self.child(m, step, depth + 1) for m in s[kw]]))
        if "then" in s or "else" in s:
            parts.append(any_of([self.child(s.get("then", True), step, depth + 1),
                                 self.child(s.get("else", True), step, depth + 1)]))
        parts = [p for p in parts if p is not True]
        if not parts:
            return True
        return parts[0] if len(parts) == 1 else {"allOf": parts}

    def view(self, path):
        v = self.views.get(path)
        if v is None and path not in self.views:
            v = self.child(self.view(path[:-1]), path[-1])
            self.views[path] = v
        return v

    RANK = {None: 0, "maxlen": 1, "format": 2, "pattern": 3, "enum": 4}

    def bound(self, s, depth=0):
        """The strongest string bound of the view s, or None."""
        if depth > 40 or s is True or not isinstance(s, dict):
            return "enum" if s is False else None
        got = []
        if "enum" in s or "const" in s:
            got.append("enum")
        if isinstance(s.get("pattern"), str):
            got.append("pattern")
        if isinstance(s.get("format"), str):
            got.append("format")
        ml = s.get("maxLength")
        if isinstance(ml, int) and ml <= 16:
            got.append("maxlen")
        if "$ref" in s:
            t = resolve(self.root, s["$ref"])
            if t is not None:
                got.append(self.bound(t, depth + 1))
        for m in s.get("allOf") or []:
            got.append(self.bound(m, depth + 1))
        for kw in ("anyOf", "oneOf"):
            br = s.get(kw)
            if isinstance(br, list) and br:
                bs = [self.bound(m, depth + 1) for m in br]
                got.append(None if None in bs else min(bs, key=self.RANK.get))
        return max(got, key=self.RANK.get) if got else None

    def closed(self, s, depth=0):
        """Whether the view s closes the keys of its object."""
        if depth > 40 or not isinstance(s, dict):
            return s is False
        ap = s.get("additionalProperties")
        if (ap is False or s.get("unevaluatedProperties") is False) \
                and not s.get("patternProperties"):
            return True
        pn = s.get("propertyNames")
        if isinstance(pn, dict) and self.bound(pn, depth + 1) in ("enum", "pattern"):
            return True
        if "$ref" in s:
            t = resolve(self.root, s["$ref"])
            if t is not None and self.closed(t, depth + 1):
                return True
        if any(self.closed(m, depth + 1) for m in s.get("allOf") or []):
            return True
        for kw in ("anyOf", "oneOf"):
            br = s.get(kw)
            if isinstance(br, list) and br and all(self.closed(m, depth + 1) for m in br):
                return True
        return False

    def value_class(self, path):
        c = self.vlabels.get(path)
        if c is None:
            b = self.bound(self.view(path))
            c = "str_" + (b or "free")
            self.vlabels[path] = c
        return c

    def key_class(self, path):
        c = self.klabels.get(path)
        if c is None:
            c = "key_closed" if self.closed(self.view(path)) else "key_open"
            self.klabels[path] = c
        return c


CLASSES = ("outside", "key_open", "key_closed", "str_free", "str_maxlen", "str_format",
           "str_pattern", "str_enum")
T3_REJECT = re.compile(r"^byte (\d+) \(0x([0-9a-f]{2})\) not in mask$")
T3_ERROR = re.compile(r"^fill_mask at byte (\d+)$")


def measured_steps(rec):
    """MaskBench steps of an instance in a t8a_streams record: the accepted
    tokens, plus the rejected one (its mask is measured too)."""
    return rec["consumed"] + (rec.get("rejected_at") is not None)


def step_classes(payload, ends, walk):
    """The position class of every step: steps[k] is the mask for the
    prefix payload[:ends[k-1]] (ends[-1] = 0 for k = 0)."""
    spans = string_spans(payload)
    opens = [s[0] for s in spans]
    out = []
    prev = 0
    for e in ends:
        o = prev
        j = bisect.bisect_left(opens, o) - 1
        if j >= 0 and spans[j][0] < o <= spans[j][1]:
            sp = spans[j]
            out.append(walk.key_class(sp[3]) if sp[2] == "key" else walk.value_class(sp[3]))
        else:
            out.append("outside")
        prev = e
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--tokenizer", required=True)
    ap.add_argument("--t3", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--calib", action="append", default=[])
    ap.add_argument("--files", default="")
    a = ap.parse_args()
    if os.path.exists(a.out):
        refuse(f"{a.out} exists")
    m = json.load(open(os.path.join(a.run_dir, "identity", "manifest.json"), encoding="utf-8"))
    from bolorgir import serializer as ser_mod
    from bolorgir.serializer import serialize_value
    from transformers import AutoTokenizer
    want = m["venvs"]["venv-pinned"]["serializer_sha256"]
    if sha(ser_mod.__file__) != want:
        refuse("the serializer of this interpreter is not the run's")
    if os.path.basename(os.path.normpath(a.tokenizer)) != m["tokenizer"]["revision"]:
        refuse("the tokenizer snapshot is not the run's revision")
    for fn, h in m["tokenizer"]["files"].items():
        if sha(os.path.join(a.tokenizer, fn)) != h:
            refuse(f"tokenizer {fn} differs from the run's")
    idx = m["corpus"]["index"]
    files = sorted(idx)
    if a.files:
        sub = [ln.strip() for ln in open(a.files, encoding="utf-8") if ln.strip()]
        bad = [f for f in sub if f not in idx]
        if bad:
            refuse(f"not in the corpus index: {bad[:3]}")
        files = sorted(set(sub))
    for f in files:
        h = idx[f] if isinstance(idx[f], str) else idx[f].get("sha256")
        if sha(os.path.join(a.data, f)) != h:
            refuse(f"{f} differs from the run's corpus file")
    streams_path = os.path.join(a.run_dir, "identity", "streams.json.gz")
    ref = json.load(gzip.open(streams_path))["files"]
    t3doc = json.load(gzip.open(a.t3))
    t3_core = ((t3doc.get("meta") or {}).get("engine") or {}).get("library_sha256")
    if t3_core != m["wheel"]["core_sha256"]:
        refuse(f"the T3 coverage is of the core {str(t3_core)[:16]}, the run's is "
               f"{m['wheel']['core_sha256'][:16]}")
    t3 = {r["file"]: r for r in t3doc["per_file"]}
    del t3doc

    tok = AutoTokenizer.from_pretrained(a.tokenizer)
    dec = {v: k for k, v in bytes_to_unicode().items()}
    pieces = tok.convert_ids_to_tokens(list(range(len(tok))))
    tbytes = []
    for p in pieces:
        try:
            tbytes.append(bytes(dec[c] for c in p) if p is not None else None)
        except KeyError:
            tbytes.append(None)

    calib_need = {}
    for d in a.calib:
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json") or fn not in idx:
                continue
            o = json.load(open(os.path.join(d, fn), encoding="utf-8"))
            sp = os.path.join(d, "t8a_streams", fn)
            if "all_mask_us" not in o or o.get("compile_error") or not os.path.exists(sp):
                continue
            calib_need.setdefault(fn, []).append(
                {"dir": d, "out": o, "rec": json.load(open(sp, encoding="utf-8"))})
    if a.files:
        for fn in list(calib_need):
            if fn not in files:
                del calib_need[fn]
    compile_checks = []
    for d in a.calib:
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json") or fn not in files:
                continue
            o = json.load(open(os.path.join(d, fn), encoding="utf-8"))
            compile_checks.append({
                "file": fn, "dir": os.path.relpath(d, a.run_dir),
                "t8a": ("pending" if "pending_file" in o else
                        "compile_error" if o.get("compile_error") else "compiled"),
                "t3_status": (t3.get(fn) or {}).get("status")})

    os.makedirs(a.out)
    with open(os.path.join(a.out, "inputs.json"), "w", encoding="utf-8") as f:
        json.dump({
            "argv": sys.argv, "cwd": os.getcwd(), "python": sys.executable,
            "script_sha256": sha(os.path.abspath(__file__)),
            "manifest_sha256": sha(os.path.join(a.run_dir, "identity", "manifest.json")),
            "streams_sha256": sha(streams_path), "t3_sha256": sha(a.t3),
            "core_sha256": m["wheel"]["core_sha256"], "t3_library_sha256": t3_core,
            "wheel_sha256": m["wheel"]["sha256"], "serializer_sha256": want,
            "tokenizer_revision": m["tokenizer"]["revision"],
            "corpus_files_checked": len(files), "corpus_files_without_t3": sum(
                f not in t3 for f in files),
            "calibration": [{"output": os.path.relpath(os.path.join(c["dir"], fn), a.run_dir),
                             "sha256": sha(os.path.join(c["dir"], fn)),
                             "streams_record_sha256": sha(os.path.join(
                                 c["dir"], "t8a_streams", fn))}
                            for fn in sorted(calib_need) for c in calib_need[fn]]},
            f, indent=1)
    problems = []
    calib = []
    t0 = time.monotonic()
    with open(os.path.join(a.out, "files.jsonl.gz"), "wb") as raw, \
            gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz, \
            io.TextIOWrapper(gz, encoding="utf-8") as fo:
        for n_f, f in enumerate(files):
            d = json.load(open(os.path.join(a.data, f), encoding="utf-8"))
            schema = d["schema"]
            walk = Walk(schema)
            tr = t3.get(f) or {}
            t3_inst = ((tr.get("semantic") or {}).get("instances")) or []
            if t3_inst and len(t3_inst) != len(d["tests"]):
                problems.append({"file": f, "t3": f"{len(t3_inst)} T3 instances, "
                                 f"{len(d['tests'])} tests"})
                t3_inst = []
            refs = {r["i"]: r for r in ref.get(f) or []}
            rec = {"file": f, "dataset": f.split("---")[0] if "---" in f else f.split("_")[0],
                   "t3_status": tr.get("status"), "instances": []}
            per_inst_classes = {}
            earlier = []
            for i, test in enumerate(d["tests"]):
                r = refs.get(i) or {}
                payload, st = serialize_value(test["data"], schema)
                ir = {"i": i, "valid": bool(test.get("valid"))}
                if not st.ok:
                    ir["serialization_incompatible"] = True
                    if "serialization_incompatible" not in r:
                        problems.append({"file": f, "i": i, "stream": "refusal not in reference"})
                    rec["instances"].append(ir)
                    continue
                if "serialization_incompatible" in r:
                    problems.append({"file": f, "i": i, "stream": "reference has a refusal"})
                text = payload.decode("utf-8")
                ids = tok.encode(text, add_special_tokens=False)
                if (len(ids) != r.get("n_ids") or ids_digest(ids) != r.get("ids_sha256")
                        or hashlib.sha256(payload).hexdigest() != r.get("instance_sha256")):
                    problems.append({"file": f, "i": i, "stream": "differs from the reference"})
                ends, acc = [], 0
                ok_bytes = True
                for t in ids:
                    tb = tbytes[t]
                    if tb is None:
                        ok_bytes = False
                        break
                    acc += len(tb)
                    ends.append(acc)
                if not ok_bytes or acc != len(payload) or \
                        b"".join(tbytes[t] for t in ids) != payload:
                    problems.append({"file": f, "i": i, "stream": "token bytes != payload"})
                    rec["instances"].append(dict(ir, n_ids=len(ids), unusable=True))
                    continue
                cls = step_classes(payload, ends, walk)
                per_inst_classes[i] = cls
                n = len(ids)
                steps, src = n, "all tokens"
                ti = t3_inst[i] if i < len(t3_inst) else None
                risk = None
                if ti is not None:
                    mr = T3_REJECT.match(ti.get("detail") or "")
                    me = T3_ERROR.match(ti.get("detail") or "")
                    if ti["outcome"] == "reject" and mr:
                        nb, hx = int(mr.group(1)), int(mr.group(2), 16)
                        if nb < len(payload) and payload[nb] == hx:
                            steps = bisect.bisect_right(ends, nb) + 1
                            src = "t3 reject byte"
                        else:
                            src = "t3 reject byte not in these bytes"
                    elif ti["outcome"].startswith("error:") and me:
                        risk = {"outcome": ti["outcome"], "byte": int(me.group(1)),
                                "step": bisect.bisect_right(ends, int(me.group(1)))}
                        steps = risk["step"]
                        src = "t3 error"
                    elif ti["outcome"] == "accept":
                        src = "t3 accept"
                    else:
                        src = f"t3 {ti['outcome']}"
                else:
                    src = "no t3 instance"
                rep_k = -1
                for ids_j, steps_j in earlier:
                    lcp = 0
                    for x, y in zip(ids, ids_j):
                        if x != y:
                            break
                        lcp += 1
                    rep_k = max(rep_k, min(lcp, steps_j - 1))
                earlier.append((ids, steps))
                ir["repeat_k"] = rep_k
                counts, full, repeat = {}, {}, {}
                for k, c in enumerate(cls):
                    full[c] = full.get(c, 0) + 1
                    if k < steps:
                        counts[c] = counts.get(c, 0) + 1
                        if k <= rep_k:
                            repeat[c] = repeat.get(c, 0) + 1
                ir.update(n_ids=n, bytes=len(payload), steps=steps, steps_from=src,
                          counts=counts, counts_all=full, counts_repeat=repeat)
                if risk:
                    ir["risk"] = risk
                rec["instances"].append(ir)
            fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
            rep_ks = {ir_["i"]: ir_.get("repeat_k", -1) for ir_ in rec["instances"]}
            for c in calib_need.get(f, []):
                o, srec = c["out"], c["rec"]
                times = o["all_mask_us"]
                steps_us = []
                bad = None
                for tr_ in srec["tests"]:
                    cls = per_inst_classes.get(tr_["i"])
                    if cls is None or tr_.get("ids_sha256") != (refs.get(tr_["i"]) or {}).get(
                            "ids_sha256"):
                        bad = f"instance {tr_['i']} not classified or not the reference"
                        break
                    for k in range(measured_steps(tr_)):
                        if len(steps_us) >= len(times):
                            bad = "more classified steps than measured"
                            break
                        steps_us.append([tr_["i"], k, cls[k], times[len(steps_us)],
                                         k <= rep_ks[tr_["i"]]])
                if bad is None and len(steps_us) != len(times):
                    bad = f"{len(steps_us)} classified steps, {len(times)} measured"
                calib.append({"file": f, "dir": os.path.relpath(c["dir"], a.run_dir),
                              "problem": bad, "masks_us": o.get("masks_us"),
                              "ttfm_us": o.get("ttfm_us"),
                              "steps": None if bad else steps_us})
            if n_f % 1000 == 0:
                print(f"{n_f}/{len(files)} files, {time.monotonic() - t0:.0f} s", flush=True)
    with open(os.path.join(a.out, "calib.json"), "w", encoding="utf-8") as f:
        json.dump(calib, f)
    with open(os.path.join(a.out, "compile_checks.json"), "w", encoding="utf-8") as f:
        json.dump(compile_checks, f, indent=1)
    with open(os.path.join(a.out, "problems.json"), "w", encoding="utf-8") as f:
        json.dump(problems, f, indent=1)
    stream_bad = [p for p in problems if "stream" in p]
    print(f"done: {len(files)} files, {len(calib)} calibration outputs, "
          f"{len(problems)} problems ({len(stream_bad)} stream), "
          f"{time.monotonic() - t0:.0f} s", flush=True)
    sys.exit(1 if stream_bad else 0)


if __name__ == "__main__":
    main()
