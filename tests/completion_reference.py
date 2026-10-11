"""Independent completion witnesses for small spec-v1 object schemas.

No engine code is used here. A byte prefix is completed from a bounded,
schema-directed candidate set, and every candidate document is judged by
the jsonschema reference validator over exact numbers (exact_numbers) plus
the rules of the spec-v1 language that a validator does not see
(docs/semantics-spec-v1.md): declared properties appear in schema
declaration order and a skipped one never comes back (4.3), a key occurs
at most once, strings use the canonical escape table (4.2), and a
document carries no whitespace (the byte masks admit none).

A witness - a document of the language that starts with the prefix -
proves the prefix completable, so a next byte the engine masks although
it has a witness is a false dead end. No witness proves nothing: the
candidate set is bounded. Callers count such bytes and steps as
unverified, never as agreement. The schema only directs the candidates
to keep the set small - an object is first closed with exactly the
members it owes (required names and the dependency closure, declared
ones in schema order), a string value only with closings its own string
keywords admit, and never with more members than `maxProperties`
allows (the validator refuses those anyway) - the validator and the order
rules alone decide.

Supported schemas: objects (properties, patternProperties,
additionalProperties, required, array-form dependencies,
dependentRequired, minProperties and maxProperties), arrays (items), scalar types with const, enum,
pattern and length bounds, local $ref. Combinators and the keywords that
relax the order (oneOf, anyOf, allOf, if, not, propertyNames,
dependentSchemas, schema-form dependencies) raise Unsupported: there the
order rule of the language is not the plain declaration order.
Patterns are evaluated by Python `re` (the reference validator does the
same); the schemas used with this module keep to the common subset of
ECMA-262 and Python syntax.
"""

from __future__ import annotations

import json
import re

import jsonschema

from exact_numbers import exact_dialects, exact_validator
from exact_numbers import loads as exact_loads

D4 = "http://json-schema.org/draft-04/schema#"

UNSUPPORTED_KEYWORDS = ("oneOf", "anyOf", "allOf", "if", "then", "else", "not",
                        "propertyNames", "dependentSchemas", "unevaluatedProperties",
                        "unevaluatedItems", "contains", "prefixItems", "$dynamicRef")

GENERIC = [b"0", b'""', b"true", b"null", b"{}", b"[]", b'"a"', b"1"]
GENERIC_KEYS = ["", "a", "b", "z", "aa", "ab", "a1"]
# Canonical escapes (semantics-spec-v1 4.2), as raw text after the backslash.
ESCAPES = ['"', "\\", "b", "f", "n", "r", "t"] + [
    "u00%02x" % c for c in range(0x20) if c not in (8, 9, 10, 12, 13)]
NUM_RE = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?\Z")
NUM_PREFIX_RE = re.compile(r"-?(0|[1-9][0-9]*)?(\.[0-9]*)?([eE][+-]?[0-9]*)?\Z")


class Unsupported(Exception):
    pass


class Budget(Exception):
    pass


def canonical(text: str) -> bytes:
    return json.dumps(text, ensure_ascii=False).encode()


def dumps(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def _utf8_need(raw: bytes):
    """Bytes still missing from a trailing UTF-8 sequence, its lead byte
    and the bytes after the lead (0, None, b"" when none is open)."""
    j = len(raw)
    while j > 0 and len(raw) - j < 4:
        j -= 1
        b = raw[j]
        if b < 0x80:
            return 0, None, b""
        if b >= 0xC0:
            total = 2 if b < 0xE0 else 3 if b < 0xF0 else 4
            have = len(raw) - j
            return (total - have if have < total else 0), b, raw[j + 1:]
    return 0, None, b""


def _utf8_finish(lead: int, tail: bytes, need: int) -> bytes:
    """A valid ending of a UTF-8 sequence started by lead + tail (b"" when
    none exists): the first missing byte picks the range the lead allows
    (E0: A0-BF, ED: 80-9F, F0: 90-BF, F4: 80-8F), the others are free."""
    for first in (0x80, 0x90, 0xA0, 0xA9):
        end = bytes([first] + [0x80] * (need - 1))
        try:
            (bytes([lead]) + tail + end).decode("utf-8")
            return end
        except UnicodeDecodeError:
            pass
    return b""


class Reference:
    """Completion witnesses for one schema. `budget` caps the documents
    judged per witness() call, `extra` the members a completion adds to
    one object."""

    def __init__(self, schema, budget=3000, extra=2):
        self.root = schema
        self.budget = budget
        self.extra = extra
        self._check_supported(schema)
        self.draft4 = isinstance(schema, dict) and schema.get("$schema") == D4
        cls = jsonschema.Draft4Validator if self.draft4 else jsonschema.Draft202012Validator
        self.validator = exact_validator(schema, cls)
        self.alphabet = self._alphabet(schema)
        self.spent = 0
        self._inst = {}

    # -- schema access ------------------------------------------------------
    def _check_supported(self, node):
        if isinstance(node, dict):
            for k in UNSUPPORTED_KEYWORDS:
                if k in node:
                    raise Unsupported(k)
            deps = node.get("dependencies", {})
            if any(not isinstance(v, list) for v in deps.values()):
                raise Unsupported("schema-form dependencies")
            if isinstance(node.get("items"), list):
                raise Unsupported("tuple items")
            for v in node.values():
                self._check_supported(v)
        elif isinstance(node, list):
            for v in node:
                self._check_supported(v)

    def resolve(self, s):
        seen = 0
        while isinstance(s, dict) and "$ref" in s:
            ref = s["$ref"]
            if not ref.startswith("#"):
                raise Unsupported(ref)
            node = self.root
            for part in [p for p in ref[1:].split("/") if p]:
                node = node[part.replace("~1", "/").replace("~0", "~")]
            s = node
            seen += 1
            if seen > 32:
                raise Unsupported("ref loop")
        return s

    def max_members(self, s):
        """The member cap of object schema s (maxProperties), for the
        candidates only: a document over it is invalid, so none is built."""
        s = self.resolve(s)
        m = s.get("maxProperties") if isinstance(s, dict) else None
        return m if isinstance(m, int) else 1 << 30

    def sub_for_key(self, s, key: str):
        s = self.resolve(s)
        if not isinstance(s, dict):
            return s
        props = s.get("properties", {})
        if key in props:
            return props[key]
        for pat, sub in s.get("patternProperties", {}).items():
            if re.search(pat, key):
                return sub
        return s.get("additionalProperties", True)

    def sub_for_item(self, s):
        s = self.resolve(s)
        if not isinstance(s, dict):
            return s
        return s.get("items", True)

    @staticmethod
    def _alphabet(schema):
        # The characters of the schema's short strings, keys and patterns
        # alike (a pattern's `é` may be the only key it leaves out).
        chars = set("az0")
        todo = [schema]
        while todo:
            n = todo.pop()
            if isinstance(n, dict):
                todo += [k for k in n if len(k) <= 40] + list(n.values())
            elif isinstance(n, list):
                todo += n
            elif isinstance(n, str) and len(n) <= 40:
                chars.update(n)
        return sorted(c for c in chars if c.isprintable() and c not in '"\\')

    # -- candidate values ---------------------------------------------------
    @staticmethod
    def _class_chars(body: str):
        """A few members of a character class body (a negated class: a few
        common characters outside it)."""
        if body.startswith("^"):
            return [c for c in "bz0_ " if not re.match("[" + body + "]", c)][:3]
        out = []
        for a, b in re.findall(r"([^\\])-([^\\])", body):
            out += [a, chr(ord(a) + 1) if ord(a) < ord(b) else a, b]
        rest = re.sub(r"[^\\]-[^\\]", "", body)
        for m in re.finditer(r"\\(.)|(.)", rest):
            if m.group(1):
                out += {"s": [" "], "S": ["x"], "d": ["0", "1"], "w": ["a", "_"]}.get(
                    m.group(1), [m.group(1)])
            else:
                out.append(m.group(2))
        return list(dict.fromkeys(out))

    def _pattern_samples(self, pat: str):
        """Short strings the pattern matches: its text with the anchors,
        groups and quantifiers dropped, escapes unescaped and every class
        replaced by a few of its members (filtered by re.search)."""
        parts = []
        for m in re.finditer(r"\[(\^?(?:\\.|[^\]])*)\]|\\(.)|([()|^$?*+]|\{[0-9,]*\})|(.)", pat):
            q = re.fullmatch(r"\{([0-9]+)(,[0-9]*)?\}", m.group(3) or "")
            if q and parts:
                parts += [parts[-1]] * (int(q.group(1)) - 1)
            elif m.group(1) is not None:
                parts.append(self._class_chars(m.group(1)) or [""])
            elif m.group(2) is not None:
                parts.append({"s": [" "], "S": ["x"], "d": ["0", "1"], "w": ["a"]}.get(
                    m.group(2), [m.group(2)]))
            elif m.group(4) is not None:
                parts.append(["x"] if m.group(4) == "." else [m.group(4)])
        outs = [""]
        for opts in parts:
            outs = [o + c for o in outs for c in opts][:64]
        out = set(outs)
        for o in outs:
            out.add(o + o)
        return [x for x in sorted(out) if re.search(pat, x)]

    def instances(self, s, depth=0):
        key = (json.dumps(s, sort_keys=True), depth)
        if key not in self._inst:
            self._inst[key] = self._instances(s, depth)
        return self._inst[key]

    def _instances(self, s, depth):
        s = self.resolve(s)
        if s is True or s == {}:
            return list(GENERIC)
        if s is False:
            return []
        if "const" in s:
            return [dumps(s["const"])]
        if "enum" in s:
            return [dumps(v) for v in s["enum"]][:6]
        types = s.get("type")
        if types is None:
            if "properties" in s or "required" in s or "additionalProperties" in s or \
                    "patternProperties" in s:
                types = ["object"]
            elif "items" in s:
                types = ["array"]
            elif "pattern" in s or "minLength" in s or "maxLength" in s:
                types = ["string"]
            else:
                return list(GENERIC)
        if isinstance(types, str):
            types = [types]
        out = []
        for t in types:
            if t == "string":
                cands = ["", "a", "ab", "x1", "a@b.cc"]
                if "pattern" in s:
                    cands += self._pattern_samples(s["pattern"])
                lo = s.get("minLength", 0)
                cands += ["a" * lo]
                cands = [c for c in cands if len(c) >= lo and len(c) <= s.get("maxLength", 1 << 30)]
                if "pattern" in s:
                    cands = [c for c in cands if re.search(s["pattern"], c)]
                out += [canonical(c) for c in cands]
            elif t == "integer":
                out += [b"0", b"1", b"-1", b"2"]
            elif t == "number":
                out += [b"0", b"1.5", b"-1"]
            elif t == "boolean":
                out += [b"true", b"false"]
            elif t == "null":
                out += [b"null"]
            elif t == "object" and depth < 3:
                out += self._object_instances(s, depth)
            elif t == "array" and depth < 3:
                out.append(b"[]")
                for x in self.instances(self.sub_for_item(s), depth + 1)[:2]:
                    out.append(b"[" + x + b"]")
        seen, uniq = set(), []
        for x in out:
            if x not in seen:
                seen.add(x)
                uniq.append(x)
        return uniq[:8]

    def _object_instances(self, s, depth):
        props = list(s.get("properties", {}).items())
        req = list(s.get("required", []))
        deps = dict(s.get("dependencies", {}))
        deps.update(s.get("dependentRequired", {}))
        variants = []
        for opt in [None] + [k for k, _ in props if k not in req]:
            want = set(req) | ({opt} if opt else set())
            for _ in range(4):  # dependency closure
                for t in list(want):
                    want.update(deps.get(t, []))
            members = []
            ok = True
            for k, sub in props:
                if k in want:
                    vals = self.instances(sub, depth + 1)
                    if not vals:
                        ok = False
                        break
                    members.append(canonical(k) + b":" + vals[0])
            for k in sorted(want - {k for k, _ in props}):
                vals = self.instances(self.sub_for_key(s, k), depth + 1)
                if not vals:
                    ok = False
                    break
                members.append(canonical(k) + b":" + vals[0])
            if ok:
                variants.append(b"{" + b",".join(members) + b"}")
        return variants

    def key_universe(self, s):
        """Keys a completion may add to an object of schema s, besides its
        declared names: required and dependency names, pattern samples,
        generic short keys."""
        s = self.resolve(s)
        if not isinstance(s, dict):
            return list(GENERIC_KEYS)
        keys = list(GENERIC_KEYS)
        keys += s.get("required", [])
        for t, names in list(s.get("dependencies", {}).items()) + \
                list(s.get("dependentRequired", {}).items()):
            keys.append(t)
            keys += names
        for pat in s.get("patternProperties", {}):
            keys += self._pattern_samples(pat)
        keys += [c for c in self.alphabet]
        out, seen = [], set()
        for k in keys:
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out

    # -- prefix parsing -----------------------------------------------------
    def parse(self, prefix: bytes):
        """Frames of a compact JSON prefix, innermost last; None when the
        prefix is no viable prefix of a compact JSON text."""
        stack = [{"t": "top", "s": self.root, "st": "start"}]
        i = 0
        n = len(prefix)
        while i < n:
            f = stack[-1]
            b = prefix[i]
            t = f["t"]
            if t in ("str", "key"):
                if b == 0x22 and self._open_escape(f["raw"]) is None:
                    # An unescaped quote ends the string, which must be
                    # complete and canonical.
                    if not self._str_closed_ok(f["raw"]):
                        return None
                    i += 1
                    if t == "key":
                        stack.pop()
                        o = stack[-1]
                        try:
                            o["cur"] = json.loads(b'"' + f["raw"] + b'"')
                        except ValueError:
                            return None
                        o["st"] = "colon"
                    else:
                        stack.pop()
                        if not self._value_done(stack):
                            return None
                    continue
                if b < 0x20:
                    return None
                f["raw"] += bytes([b])
                if not self._str_prefix_ok(f["raw"]):
                    return None
                i += 1
                continue
            if t == "num":
                if NUM_PREFIX_RE.match((f["raw"] + bytes([b])).decode("latin-1")):
                    f["raw"] += bytes([b])
                    i += 1
                    continue
                if not NUM_RE.match(f["raw"].decode("latin-1")):
                    return None
                stack.pop()
                if not self._value_done(stack):
                    return None
                continue  # re-read b in the parent
            if t == "lit":
                f["raw"] += bytes([b])
                if not any(w.startswith(f["raw"]) for w in (b"true", b"false", b"null")):
                    return None
                i += 1
                if f["raw"] in (b"true", b"false", b"null"):
                    stack.pop()
                    if not self._value_done(stack):
                        return None
                continue
            if t == "obj":
                st = f["st"]
                if st in ("open", "comma") and b == 0x22:
                    stack.append({"t": "key", "raw": b""})
                    f["st"] = "key"
                elif st in ("open", "after") and b == 0x7D:
                    stack.pop()
                    if not self._value_done(stack):
                        return None
                elif st == "after" and b == 0x2C:
                    f["st"] = "comma"
                elif st == "colon" and b == 0x3A:
                    f["st"] = "value"
                elif st == "value":
                    if not self._start_value(stack, self.sub_for_key(f["s"], f["cur"]), b):
                        return None
                    f["st"] = "val"
                else:
                    return None
                i += 1
                continue
            if t == "arr":
                st = f["st"]
                if st in ("open", "after") and b == 0x5D:
                    stack.pop()
                    if not self._value_done(stack):
                        return None
                elif st == "after" and b == 0x2C:
                    f["st"] = "value"
                elif st in ("open", "value"):
                    if not self._start_value(stack, self.sub_for_item(f["s"]), b):
                        return None
                    f["st"] = "val"
                else:
                    return None
                i += 1
                continue
            if t == "top":
                if f["st"] != "start":
                    return None
                if not self._start_value(stack, f["s"], b):
                    return None
                f["st"] = "val"
                i += 1
                continue
            return None
        return stack

    def _start_value(self, stack, s, b):
        if b == 0x7B:
            stack.append({"t": "obj", "s": s, "st": "open", "keys": [], "added": 0})
        elif b == 0x5B:
            stack.append({"t": "arr", "s": s, "st": "open", "added": 0})
        elif b == 0x22:
            stack.append({"t": "str", "s": s, "raw": b""})
        elif b in b"-0123456789":
            stack.append({"t": "num", "s": s, "raw": bytes([b])})
        elif b in b"tfn":
            stack.append({"t": "lit", "s": s, "raw": bytes([b])})
        else:
            return False
        return True

    def _value_done(self, stack):
        f = stack[-1]
        if f["t"] == "obj":
            if f["st"] != "val":
                return False
            f["keys"].append(f.pop("cur"))
            f["st"] = "after"
        elif f["t"] == "arr":
            f["st"] = "after"
        elif f["t"] == "top":
            f["st"] = "done"
        return True

    @staticmethod
    def _str_prefix_ok(raw: bytes) -> bool:
        need, lead, tail = _utf8_need(raw)
        body = raw[: len(raw) - (len(tail) + 1)] if need else raw
        try:
            body.decode("utf-8")
        except UnicodeDecodeError:
            return False
        if need and lead is not None:
            try:
                (bytes([lead]) + tail + _utf8_finish(lead, tail, need)).decode("utf-8")
            except UnicodeDecodeError:
                return False
            if not _utf8_finish(lead, tail, need):
                return False
        esc = Reference._open_escape(raw)
        if esc is not None and not any(e.startswith(esc) for e in ESCAPES):
            return False
        return True

    @staticmethod
    def _open_escape(raw: bytes):
        """Text after the backslash of an unfinished escape, None when no
        escape is open (an even run of backslashes is escaped backslashes;
        a finished escape is followed by ordinary content)."""
        k = raw.rfind(b"\\")
        if k < 0:
            return None
        run = 0
        m = k
        while m >= 0 and raw[m] == 0x5C:
            run += 1
            m -= 1
        if run % 2 == 0:
            return None
        tail = raw[k + 1:].decode("latin-1")
        if any(tail.startswith(e) for e in ESCAPES):
            return None
        return tail

    def _str_closed_ok(self, raw: bytes) -> bool:
        if _utf8_need(raw)[0] or self._open_escape(raw) is not None:
            return False
        try:
            text = json.loads(b'"' + raw + b'"')
        except ValueError:
            return False
        return canonical(text) == b'"' + raw + b'"'

    # -- completions --------------------------------------------------------
    def _complete_str(self, raw: bytes, cands, s=None):
        """Suffixes closing a string whose raw content so far is raw: the
        candidate texts that extend it, the end of an open escape or UTF-8
        sequence, then nothing, one alphabet character or a tail of a
        candidate text (`qwxz` + `@b.cc`), then the quote. With the value
        schema s, only the strings it admits (`_string_ok`)."""
        need, lead, tail = _utf8_need(raw)
        esc = self._open_escape(raw)
        if need:
            stems = [_utf8_finish(lead, tail, need)]
        elif esc is not None:
            stems = [e[len(esc):].encode() for e in ESCAPES if e.startswith(esc)][:3]
        else:
            stems = [b""]
        bodies = [canonical(c)[1:-1] for c in cands]
        res = []
        for body in bodies:
            if body.startswith(raw):
                res.append(body[len(raw):] + b'"')
        for o in stems:
            res.append(o + b'"')
            for ch in self.alphabet[:12]:
                res.append(o + canonical(ch)[1:-1] + b'"')
            for body in bodies:
                for i in range(1, len(body)):
                    res.append(o + body[i:] + b'"')
        seen, out = set(), []
        for r in res:
            if r in seen or not self._str_closed_ok(raw + r[:-1]):
                continue
            seen.add(r)
            if s is None or self._string_ok(s, json.loads(b'"' + raw + r)):
                out.append(r)
        return out

    def _string_ok(self, s, text: str) -> bool:
        """Whether value schema s admits the string text by its string
        keywords (pattern by `re.search` as the validator, lengths in code
        points, const except in draft-04, enum); the validator still
        judges the document."""
        s = self.resolve(s)
        if not isinstance(s, dict):
            return s is not False
        if "const" in s and not self.draft4 and s["const"] != text:
            return False
        if "enum" in s and text not in s["enum"]:
            return False
        if len(text) < s.get("minLength", 0) or len(text) > s.get("maxLength", len(text)):
            return False
        return "pattern" not in s or re.search(s["pattern"], text) is not None

    def _members(self, f):
        """New members (key, value text) for object frame f: declared names
        past the last declared key present (schema order), then undeclared
        keys of the universe."""
        s = self.resolve(f["s"])
        props = list(s.get("properties", {}).keys()) if isinstance(s, dict) else []
        present = f["keys"]
        last = max([props.index(k) for k in present if k in props], default=-1)
        keys = [k for k in props[last + 1:] if k not in present]
        keys += [k for k in self.key_universe(s) if k not in props and k not in present]
        return keys

    def _gen(self, stack):
        """Suffixes completing every frame of `stack` (innermost last)."""
        f = stack[-1]
        t = f["t"]
        if t == "top":
            if f["st"] == "done":
                yield b""
            elif f["st"] == "start":
                for v in self.instances(f["s"]):
                    yield v
            return
        parent = stack[:-1]
        if t == "str":
            cands = [json.loads(v) for v in self.instances(f["s"]) if v.startswith(b'"')]
            for suf in self._complete_str(f["raw"], cands, f["s"]):
                for rest in self._after_value(parent):
                    yield suf + rest
            return
        if t == "key":
            o = parent[-1]
            if len(o["keys"]) + 1 > self.max_members(o["s"]):
                return
            cands = self._members(o)
            for suf in self._complete_str(f["raw"], cands):
                try:
                    key = json.loads(b'"' + f["raw"] + suf)
                except ValueError:
                    continue
                if key in o["keys"]:
                    continue
                for rest in self._member_value(parent, key):
                    yield suf + rest
            return
        if t == "num":
            raw = f["raw"].decode("latin-1")
            for add in ("", "0", "1", ".5", "5", "E9", "0E9"):
                if NUM_RE.match(raw + add):
                    for rest in self._after_value(parent):
                        yield add.encode() + rest
            return
        if t == "lit":
            for w in (b"true", b"false", b"null"):
                if w.startswith(f["raw"]):
                    for rest in self._after_value(parent):
                        yield w[len(f["raw"]):] + rest
            return
        if t == "obj":
            yield from self._gen_obj(stack)
            return
        if t == "arr":
            yield from self._gen_arr(stack)
            return

    def _member_value(self, stack, key):
        """`":"` + a value for `key` in the object on top of stack, then the
        rest of the object and its parents."""
        o = dict(stack[-1])
        o["keys"] = o["keys"] + [key]
        o["st"] = "after"
        for v in self.instances(self.sub_for_key(o["s"], key), 1):
            for rest in self._gen(stack[:-1] + [o]):
                yield b":" + v + rest

    def _owed(self, f):
        """Keys object frame f still owes before it may close: the required
        names and the dependencies of the keys present or owed, missing
        from f - declared ones in schema order, then the others sorted.
        None when a declared one lies behind the last declared key present
        (a skipped declared name never comes back)."""
        s = self.resolve(f["s"])
        if not isinstance(s, dict):
            return []
        props = list(s.get("properties", {}).keys())
        deps = dict(s.get("dependencies", {}))
        deps.update(s.get("dependentRequired", {}))
        present = set(f["keys"])
        want = set(s.get("required", []))
        size = -1
        while size != len(want):
            size = len(want)
            for t in list(present | want):
                want.update(deps.get(t, []))
        want -= present
        last = max([props.index(k) for k in present if k in props], default=-1)
        if any(k in props and props.index(k) < last for k in want):
            return None
        return sorted((k for k in want if k in props), key=props.index) + \
            sorted(k for k in want if k not in props)

    def _gen_owed(self, stack, keys):
        """The members `keys` (a few values each), then the close of the
        object on top of stack and the rest of its parents."""
        f = stack[-1]
        if not keys:
            for rest in self._after_value(stack[:-1]):
                yield b"}" + rest
            return
        sep = b"," if f["st"] == "after" else b""
        o = dict(f)
        o["keys"] = f["keys"] + [keys[0]]
        o["st"] = "after"
        for v in self.instances(self.sub_for_key(f["s"], keys[0]), 1)[:3]:
            for rest in self._gen_owed(stack[:-1] + [o], keys[1:]):
                yield sep + canonical(keys[0]) + b":" + v + rest

    def _gen_obj(self, stack):
        f = stack[-1]
        st = f["st"]
        if st in ("open", "after"):
            for rest in self._after_value(stack[:-1]):
                yield b"}" + rest
        if st in ("open", "after", "comma"):
            # The directed completion first: exactly the members the object
            # owes (the generic members below add at most `extra`).
            owed = self._owed(f)
            if owed and len(f["keys"]) + len(owed) <= self.max_members(f["s"]):
                yield from self._gen_owed(stack, owed)
        if st == "colon":
            for rest in self._member_value(stack, f["cur"]):
                yield rest
            return
        if st == "value":
            o = dict(f)
            key = o.pop("cur")
            o["keys"] = o["keys"] + [key]
            o["st"] = "after"
            for v in self.instances(self.sub_for_key(f["s"], key), 1):
                for rest in self._gen(stack[:-1] + [o]):
                    yield v + rest
            return
        if st in ("open", "after", "comma") and f["added"] < self.extra and \
                len(f["keys"]) < self.max_members(f["s"]):
            sep = b"," if st == "after" else b""
            g = dict(f)
            g["added"] = f["added"] + 1
            for key in self._members(f):
                for rest in self._member_value(stack[:-1] + [g], key):
                    yield sep + canonical(key) + rest

    def _gen_arr(self, stack):
        f = stack[-1]
        st = f["st"]
        if st in ("open", "after"):
            for rest in self._after_value(stack[:-1]):
                yield b"]" + rest
        if st in ("open", "after", "value") and f["added"] < self.extra:
            sep = b"," if st == "after" else b""
            g = dict(f)
            g["added"] = f["added"] + 1
            g["st"] = "after"
            for v in self.instances(self.sub_for_item(f["s"]), 1):
                for rest in self._gen(stack[:-1] + [g]):
                    yield sep + v + rest

    def _after_value(self, stack):
        """The rest after a value completed inside the frame on top of
        stack."""
        f = dict(stack[-1])
        if f["t"] == "obj":
            f["keys"] = f["keys"] + [f.pop("cur")]
            f["st"] = "after"
        elif f["t"] == "arr":
            f["st"] = "after"
        elif f["t"] == "top":
            f["st"] = "done"
        yield from self._gen(stack[:-1] + [f])

    # -- judging ------------------------------------------------------------
    def in_language(self, doc: bytes) -> bool:
        """The spec-v1 language over this schema: the reference validator
        plus declaration order, unique keys, canonical strings."""
        try:
            text = doc.decode("utf-8")
        except UnicodeDecodeError:
            return False
        if any(c in text for c in " \t\r\n") and not self._ws_only_in_strings(doc):
            return False
        dup = []

        def pairs(items):
            keys = [k for k, _ in items]
            if len(set(keys)) != len(keys):
                dup.append(keys)
            return dict(items)

        try:
            value = json.loads(text, object_pairs_hook=pairs)
        except ValueError:
            return False
        if dup or not self._canonical_strings(doc):
            return False
        if not self._ordered(self.root, value):
            return False
        with exact_dialects():
            return self.validator.is_valid(exact_loads(text))

    @staticmethod
    def _ws_only_in_strings(doc: bytes) -> bool:
        inside = False
        esc = False
        for b in doc:
            if inside:
                if esc:
                    esc = False
                elif b == 0x5C:
                    esc = True
                elif b == 0x22:
                    inside = False
            elif b == 0x22:
                inside = True
            elif b in b" \t\r\n":
                return False
        return True

    @staticmethod
    def _canonical_strings(doc: bytes) -> bool:
        i = 0
        while i < len(doc):
            if doc[i] != 0x22:
                i += 1
                continue
            j = i + 1
            while doc[j] != 0x22:
                j += 2 if doc[j] == 0x5C else 1
            tok = doc[i:j + 1]
            if canonical(json.loads(tok)) != tok:
                return False
            i = j + 1
        return True

    def _ordered(self, s, value) -> bool:
        s = self.resolve(s)
        if isinstance(value, dict):
            props = list(s.get("properties", {}).keys()) if isinstance(s, dict) else []
            idx = [props.index(k) for k in value if k in props]
            if idx != sorted(idx):
                return False
            return all(self._ordered(self.sub_for_key(s, k), v) for k, v in value.items())
        if isinstance(value, list):
            return all(self._ordered(self.sub_for_item(s), v) for v in value)
        return True

    def witness(self, prefix: bytes):
        """(status, doc): "found" with a document of the language starting
        with prefix; "none" when the bounded candidate set has none;
        "budget" when the budget ran out first; "unparsable" when prefix is
        no compact JSON prefix."""
        stack = self.parse(prefix)
        if stack is None:
            return "unparsable", None
        self.spent = 0
        try:
            for suf in self._gen(stack):
                self.spent += 1
                if self.spent > self.budget:
                    raise Budget
                doc = prefix + suf
                if self.in_language(doc):
                    return "found", doc
        except Budget:
            return "budget", None
        return "none", None

    def candidates(self, prefix: bytes):
        """Next bytes worth a witness search after prefix: the bytes that
        keep it a compact JSON prefix among the structural bytes, the
        schema's alphabet (and its UTF-8 bytes), one representative per
        character class, and every continuation byte mid-sequence."""
        stack = self.parse(prefix)
        if stack is None:
            return []
        pool = set(b'",:{}[]-0123456789.eE+tfnrul')
        for c in self.alphabet + ["Z", "_", "~", "é", "ж"]:
            pool.update(c.encode())
        if stack[-1]["t"] in ("str", "key") and _utf8_need(stack[-1]["raw"])[0]:
            pool.update(range(0x80, 0xC0))
        if stack[-1]["t"] in ("str", "key") and self._open_escape(stack[-1]["raw"]) is not None:
            pool.update(b"\\\"bfnrtu0123456789abcdef")
        out = []
        for b in sorted(pool):
            if self.parse(prefix + bytes([b])) is not None:
                out.append(b)
        return out
