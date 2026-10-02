r"""
Value-preserving serializer for the spec-v1 byte language.

Implements the serializer contract of docs/semantics-spec-v1 section 6
(ROADMAP P1, feature map "value-preserving serializer"): reorder object
keys to the schema-driven order of section 4.3 while preserving every
value exactly, and emit the compact whitespace-free form of sections
4.1-4.2. It exists because instance key order is data-determined and
the MaskBench `--compact` flag keeps it (section 5), so the ordinary
compact serializer is not a canonicalizer.

Guarantees:

- Declared keys (schema `properties`) are emitted first, in schema
  declaration order; optional keys may be absent; a key occurs once.
- Undeclared keys of open objects keep their data order (section 4.3);
  undeclared keys of closed objects are kept too (data order, after the
  declared ones) - dropping them would change the value, and rejecting
  them is the validator's job, not the serializer's.
- Merged objects (`allOf`): first appearance across the merged
  subschemas in document order (section 4.3); the subschema's own
  `properties` count as the first branch. Nested `allOf` conjunctions
  flatten depth-first in document order. The order is then refined into
  a linearization consistent with every subschema's relative key order
  (the engine rejects a shared key emitted against a sibling conjunct's
  relative order, and accepts any consistent linearization); the
  refinement only deviates from first appearance when a subschema's
  relative order forces it. Contradictory subschema orders have no
  linearization the engine accepts - the serializer keeps the
  first-appearance order rather than guessing.
- Alternative branches (`oneOf`/`anyOf`): the structurally compatible
  branch joins the key order - the single fitting branch for `oneOf`,
  the first fitting branch in document order for `anyOf`. The order is
  merged, not replaced. Without `unevaluated*` on the outer schema the
  branch is the scenario's leading conjunct: its `properties` order
  decides the keys it declares (shared keys included), outer-only
  declared keys follow in outer order. With `unevaluatedProperties`/
  `unevaluatedItems` the ADR-0009 guard hoisting leads with the outer
  schema instead: outer `properties` first, then the branch's (first
  appearance wins, as with `allOf`).
  Structural fit is checked with the keywords that fix the object
  context (type/enum/const/pattern, required/properties/
  patternProperties/additionalProperties, recursively); an ambiguous
  choice (zero or several fitting `oneOf` branches, an unresolvable
  branch `$ref`) keeps the data order - no order is ever guessed.
- Values are never altered. Instance text is parsed with
  parse_int/parse_float hooks that keep the original numeric lexeme, so
  `1.0` stays `1.0`, `1e2` stays `1e2`, `-0` stays `-0` and big
  integers never pass through binary64. An already-parsed Python object
  carries no lexemes; ints emit exactly, floats emit via repr (the
  section 5 policy: `1e2` arrives as 100.0 and emits as `100.0`).
- Escapes follow the canonical-v1 table (section 4.2): `\"` `\\` `\b`
  `\f` `\n` `\r` `\t`, other control characters as lowercase `\u00xx`,
  `/` unescaped, non-ASCII raw in UTF-8 (ensure_ascii=False behavior).

Graceful fallback (never reorder when the schema gives no order): a
node whose subschema is missing, not an object schema, a boolean
schema, behind `not`, behind a non-local or unresolvable `$ref`, or
behind `oneOf`/`anyOf` without a single unambiguous fitting branch
keeps the full data key order; values are still emitted unchanged.
Local `#/...` `$ref` chains are resolved for key order.

Input kinds (explicit, never guessed):

- `serialize_json_text(instance, schema)`: `instance` is JSON text
  (str/bytes/bytearray); numeric lexemes are preserved verbatim.
- `serialize_value(value, schema)`: `value` is an already-parsed Python
  value. A `str` is a JSON string VALUE and is never parsed as JSON
  text, so "1" stays the string "1" and "hello" serializes instead of
  failing. Use this entry point when the caller hands over a decoded
  instance (e.g. the MaskBench runner's `test["data"]`).
- `serialize_for_schema(instance, schema)`: the legacy dual-mode entry
  point kept for backward compatibility; str/bytes inputs are treated
  as JSON text. New code should call one of the explicit entry points
  above.

Section 7 error separation: the serialize_* functions never raise for
content; they return a status that separates OK from
serialization-incompatible (with a reason and a JSON pointer into the
instance), so callers can bucket protocol artifacts apart from
assertion errors. Incompatible cases: invalid JSON input text,
duplicate object keys, non-string object keys (object input only),
non-finite floats (object input only), lone surrogates in strings
(unrepresentable in the section 4.2 table), nesting beyond the
implementation depth limit. Schema assertion violations are NOT
incompatibilities: negative examples round-trip like any other value.

The module is pure stdlib and imports nothing from the bolorgir
package, so it is usable standalone (benchmark harnesses, oracles).
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple, Union

__all__ = [
    "SerializationStatus",
    "SerializationIncompatibleError",
    "serialize_json_text",
    "serialize_value",
    "serialize_for_schema",
]

_MAX_REF_CHAIN = 64
_MAX_DEPTH = 500


class SerializationStatus(NamedTuple):
    """Result classification of serialize_for_schema (section 7)."""

    ok: bool
    reason: str = ""  # why incompatible; empty when ok
    pointer: str = ""  # JSON pointer into the instance ("" = root)


class SerializationIncompatibleError(ValueError):
    """Raised by callers that prefer exceptions; carries the section 7
    bucket marker for harness duck-typing (`serialization_incompatible`).
    """

    serialization_incompatible = True

    def __init__(self, reason: str, pointer: str = "") -> None:
        super().__init__(
            f"serialization-incompatible at {pointer or '/'!r}: {reason}"
        )
        self.reason = reason
        self.pointer = pointer


class _Lexeme(str):
    """A numeric lexeme kept verbatim from the input text."""


class _Pairs(list):
    """Object marker from object_pairs_hook: pairs in input order."""


_ESCAPES = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


def _escape_pointer(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _emit_string(s: str, pointer: str, out: List[str]) -> None:
    # Section 4.2 table; anything else (lone surrogates) is outside the
    # byte language and cannot be written, only refused.
    out.append('"')
    for ch in s:
        code = ord(ch)
        esc = _ESCAPES.get(code)
        if esc is not None:
            out.append(esc)
        elif code < 0x20:
            out.append("\\u%04x" % code)
        elif 0xD800 <= code <= 0xDFFF:
            raise SerializationIncompatibleError(
                f"lone surrogate U+{code:04X} in string", pointer
            )
        else:
            out.append(ch)
    out.append('"')


def _resolve_ref(schema: Any, root: Any) -> Optional[Any]:
    """Follow local `#/...` $ref chains; None when not resolvable.

    None means "no order information from this subschema" (external
    refs, anchors, broken pointers, ref cycles): the caller falls back
    to data order, never to a guessed order.
    """
    for _ in range(_MAX_REF_CHAIN):
        if not isinstance(schema, dict):
            return schema
        ref = schema.get("$ref")
        if not isinstance(ref, str):
            return schema
        if ref == "#":
            node = root
        elif ref.startswith("#/"):
            node = root
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(node, dict) or part not in node:
                    return None
                node = node[part]
        else:
            return None
        schema = node
    return None  # ref cycle


def _merged_branches(schema: Dict[str, Any], root: Any) -> List[Dict[str, Any]]:
    """The schema itself plus resolved allOf subschemas, document order.

    Nested allOf conjunctions flatten depth-first in document order (an
    allOf subschema's own allOf contributes its subschemas right after
    it); an allOf $ref cycle stops at the first repeated subschema.
    """
    if not isinstance(schema, dict):
        return [schema]
    branches: List[Dict[str, Any]] = [schema]
    seen = {id(schema)}

    def walk(node: Dict[str, Any]) -> None:
        allof = node.get("allOf")
        if isinstance(allof, list):
            for sub in allof:
                sub = _resolve_ref(sub, root)
                if isinstance(sub, dict) and id(sub) not in seen:
                    seen.add(id(sub))
                    branches.append(sub)
                    walk(sub)

    walk(schema)
    return branches


def _declared_order(schema: Dict[str, Any], root: Any,
                    topological: bool = True) -> List[str]:
    """Declared key order: first appearance across the merged subschemas
    in document order (section 4.3); the own `properties` come first.

    With `topological` (the plain-`allOf` path) the order is refined into
    a linearization consistent with EVERY merged subschema's relative key
    order: the engine accepts any such linearization and rejects shared
    keys emitted against a sibling conjunct's relative order. The
    linearization keeps the first-appearance order whenever that already
    satisfies every subschema; contradictory subschema orders (no
    linearization exists) keep the first-appearance order unchanged.
    `topological=False` is used for combinator-resolved schemas, whose
    merged `properties` are already in the canonical branch-led order.
    """
    lists: List[List[str]] = []
    order: List[str] = []
    seen = set()
    for branch in _merged_branches(schema, root):
        props = branch.get("properties")
        if isinstance(props, dict):
            keys = list(props)
            lists.append(keys)
            for key in keys:
                if key not in seen:
                    seen.add(key)
                    order.append(key)
    if not topological or len(lists) < 2:
        return order
    preds: Dict[str, set] = {key: set() for key in order}
    for keys in lists:
        for i, key in enumerate(keys):
            preds[key].update(keys[:i])
    rank = {key: i for i, key in enumerate(order)}
    placed: set = set()
    remaining = set(order)
    merged: List[str] = []
    while remaining:
        ready = [k for k in remaining if preds[k] <= placed]
        if not ready:
            return order  # contradictory conjunct orders: never guess
        ready.sort(key=rank.__getitem__)
        key = ready[0]
        merged.append(key)
        placed.add(key)
        remaining.discard(key)
    return merged


def _subschema_for_key(schema: Any, key: str, root: Any) -> Optional[Any]:
    if not isinstance(schema, dict):
        return None
    for branch in _merged_branches(schema, root):
        props = branch.get("properties")
        if isinstance(props, dict) and key in props:
            return props[key]
    patterns = schema.get("patternProperties")
    if isinstance(patterns, dict):
        for pattern, sub in patterns.items():
            try:
                if re.search(pattern, key):
                    return sub
            except re.error:
                continue
    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        return additional
    return None


def _subschema_for_index(schema: Any, index: int, root: Any) -> Optional[Any]:
    if not isinstance(schema, dict):
        return None
    for branch in _merged_branches(schema, root):
        sub = _index_subschema_of(branch, index)
        if sub is not None:
            return sub
    return None


def _index_subschema_of(schema: Dict[str, Any], index: int) -> Optional[Any]:
    prefix = schema.get("prefixItems")
    if isinstance(prefix, list):
        if index < len(prefix):
            return prefix[index]
        items = schema.get("items")
        return items if isinstance(items, (dict, bool)) else None
    items = schema.get("items")
    if isinstance(items, list):  # draft-04..2019-09 tuple form
        if index < len(items):
            return items[index]
        additional = schema.get("additionalItems")
        return additional if isinstance(additional, dict) else None
    return items if isinstance(items, (dict, bool)) else None


def _orderable(schema: Any) -> bool:
    """A dict schema reached outside `not` gives key order."""
    return isinstance(schema, dict)


_INT_LEXEME = re.compile(r"-?[0-9]+$")


def _as_number(value: Any) -> Optional[Union[int, float]]:
    """Numeric view for enum/const comparison; None when not numeric.

    Booleans are not numbers in JSON equality (true != 1).
    """
    if isinstance(value, _Lexeme):
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _json_equal(a: Any, b: Any) -> bool:
    """JSON Schema equality: numbers compare numerically across int/float
    spellings, true/false are distinct from 1/0, objects ignore key order.
    """
    na, nb = _as_number(a), _as_number(b)
    if na is not None or nb is not None:
        return na is not None and nb is not None and na == nb
    if isinstance(a, _Pairs):
        a = dict(a)
    if isinstance(b, _Pairs):
        b = dict(b)
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_json_equal(a[k], b[k]) for k in a)
    if (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))):
        return (len(a) == len(b)
                and all(_json_equal(x, y) for x, y in zip(a, b)))
    return bool(a == b)


def _type_accepts(type_name: str, value: Any) -> bool:
    if type_name == "null":
        return value is None
    if type_name == "boolean":
        return value is True or value is False
    if type_name == "integer":
        if isinstance(value, _Lexeme):
            return _INT_LEXEME.match(value) is not None
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        # A _Lexeme is a json.loads-validated numeric lexeme already.
        if isinstance(value, _Lexeme):
            return True
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "string":
        return isinstance(value, str) and not isinstance(value, _Lexeme)
    if type_name == "array":
        return isinstance(value, (list, tuple)) and not isinstance(value, _Pairs)
    if type_name == "object":
        return isinstance(value, (_Pairs, dict))
    return True  # unknown type name: not disqualifying


def _value_fits(value: Any, schema: Any, root: Any, depth: int) -> bool:
    """Structural compatibility of a parsed value with a (sub)schema.

    Only the keywords that fix the object key-order context are checked -
    type/enum/const/allOf, and for objects required/properties/
    patternProperties/additionalProperties, for arrays prefixItems/items
    (recursively). Anything unknown or unresolvable never disqualifies:
    the answer is used to PICK a branch, never to reject a value.
    """
    if depth > _MAX_DEPTH:
        return True
    schema = _resolve_ref(schema, root)
    if schema is None:
        return True
    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, dict):
        return True
    enum = schema.get("enum")
    if isinstance(enum, list) and not any(
            _json_equal(value, option) for option in enum):
        return False
    if "const" in schema and not _json_equal(value, schema["const"]):
        return False
    t = schema.get("type")
    if isinstance(t, str) and not _type_accepts(t, value):
        return False
    if isinstance(t, list) and not any(
            isinstance(name, str) and _type_accepts(name, value) for name in t):
        return False
    allof = schema.get("allOf")
    if isinstance(allof, list) and any(
            not _value_fits(value, sub, root, depth + 1) for sub in allof):
        return False
    pattern = schema.get("pattern")
    if (isinstance(pattern, str) and isinstance(value, str)
            and not isinstance(value, _Lexeme)):
        try:
            if re.search(pattern, value) is None:
                return False
        except re.error:
            pass  # an uncompilable pattern never disqualifies
    if isinstance(value, _Pairs):
        value = dict(value)
    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list) and any(
                isinstance(k, str) and k not in value for k in required):
            return False
        props = schema.get("properties")
        props = props if isinstance(props, dict) else {}
        patterns = schema.get("patternProperties")
        patterns = patterns if isinstance(patterns, dict) else {}
        additional = schema.get("additionalProperties")
        for key, member in value.items():
            if not isinstance(key, str):
                continue  # reported as incompatible by the emitter
            if key in props:
                sub: Any = props[key]
            else:
                sub = None
                for pattern, psub in patterns.items():
                    try:
                        if re.search(pattern, key):
                            sub = psub
                            break
                    except re.error:
                        continue
                if sub is None:
                    if additional is False:
                        return False
                    if isinstance(additional, dict):
                        sub = additional
            if sub is not None and not _value_fits(member, sub, root, depth + 1):
                return False
    elif isinstance(value, (list, tuple)):
        prefix = schema.get("prefixItems")
        items = schema.get("items")
        for i, item in enumerate(value):
            sub = None
            if isinstance(prefix, list) and i < len(prefix):
                sub = prefix[i]
            elif isinstance(items, list):  # draft-04..2019-09 tuple form
                if i < len(items):
                    sub = items[i]
                else:
                    extra = schema.get("additionalItems")
                    if extra is False:
                        return False
                    if isinstance(extra, dict):
                        sub = extra
            elif isinstance(items, (dict, bool)):
                sub = items
            if sub is not None and not _value_fits(item, sub, root, depth + 1):
                return False
    return True


def _select_branch(schema: Dict[str, Any], members: Dict[str, Any],
                   root: Any) -> Optional[Dict[str, Any]]:
    """The oneOf/anyOf branch that owns the key order for `members`.

    oneOf: the single structurally fitting branch (zero or several fits
    are ambiguous -> no order). anyOf: the first fitting branch in
    document order. A branch that does not resolve to a dict schema
    carries no order; an unresolvable branch $ref makes the choice
    unverifiable -> None (data order) in both cases.
    """
    for kw in ("oneOf", "anyOf"):
        branches = schema.get(kw)
        if not isinstance(branches, list):
            continue
        matches: List[Any] = []
        saw_unresolved = False
        for branch in branches:
            resolved = _resolve_ref(branch, root)
            if resolved is None:
                saw_unresolved = True
                continue
            if _value_fits(members, resolved, root, 0):
                matches.append(resolved)
        if saw_unresolved:
            return None
        if kw == "oneOf" and len(matches) != 1:
            return None  # no fit or ambiguous: never guess an order
        if matches and isinstance(matches[0], dict):
            return matches[0]
        return None
    return None


def _merged_properties(schema: Any, root: Any) -> Dict[str, Any]:
    """Ordered key -> subschema map across the allOf conjunction, first
    appearance in document order (the property view of section 4.3).
    """
    out: Dict[str, Any] = {}
    for br in _merged_branches(schema, root):
        br = _resolve_ref(br, root)
        if isinstance(br, dict):
            p = br.get("properties")
            if isinstance(p, dict):
                for k, v in p.items():
                    if k not in out:
                        out[k] = v
    return out


def _effective_schema(schema: Any, value: Any, root: Any,
                      depth: int = 0) -> Any:
    """Resolve combinator selection for `value` across the whole allOf
    conjunction of `schema` -> a flat dict whose merged `properties` are
    in the engine's canonical key order and whose items/prefixItems come
    from the selected branches.

    The conjunction is walked in document order (the own schema first,
    then allOf subs); a conjunct's oneOf/anyOf selects its branch via
    `_select_branch` and the branch's effective order leads that
    conjunct's contribution - unless the conjunct carries
    `unevaluatedProperties`/`unevaluatedItems`, where the ADR-0009 guard
    hoisting puts the conjunct's own properties first. Conjuncts without
    a combinator (or without an unambiguous fit) contribute their own
    properties as declared. The total order is first appearance across
    the contributions (section 4.3). Schemas with no combinator anywhere
    in the conjunction are returned unchanged.
    """
    schema = _resolve_ref(schema, root)
    if not isinstance(schema, dict) or depth > _MAX_REF_CHAIN:
        return schema
    conjuncts: List[Dict[str, Any]] = []
    for conj in _merged_branches(schema, root):
        conj = _resolve_ref(conj, root)
        if isinstance(conj, dict):
            conjuncts.append(conj)
    if not any("oneOf" in c or "anyOf" in c for c in conjuncts):
        return schema
    props: Dict[str, Any] = {}
    allof: List[Any] = []

    def absorb(prop_map: Dict[str, Any], conj: Any) -> None:
        for k, v in prop_map.items():
            if k not in props:
                props[k] = v
        allof.append(conj)

    for conj in conjuncts:
        own = conj.get("properties")
        own = own if isinstance(own, dict) else {}
        if "oneOf" in conj or "anyOf" in conj:
            branch = _select_branch(conj, value, root)
            if branch is not None:
                eff = _effective_schema(branch, value, root, depth + 1)
                # The branch contributes its whole merged conjunction
                # (its properties may live in its own allOf, e.g. a
                # $ref'd "common" base), not just its own `properties`.
                eff_props = (_merged_properties(eff, root)
                             if isinstance(eff, dict) else {})
                if ("unevaluatedProperties" in conj
                        or "unevaluatedItems" in conj):
                    absorb(own, conj)
                    absorb(eff_props, eff)
                else:
                    absorb(eff_props, eff)
                    absorb({k: v for k, v in own.items() if k not in eff_props},
                           conj)
                continue
        absorb(own, conj)

    merged: Dict[str, Any] = {}
    if props:
        merged["properties"] = props
    merged["allOf"] = allof
    for kw in ("patternProperties", "additionalProperties",
               "prefixItems", "items", "additionalItems"):
        for conj in allof:
            if isinstance(conj, dict) and kw in conj:
                merged[kw] = conj[kw]
                break
    return merged



def _emit(value: Any, schema: Any, root: Any, pointer: str,
          depth: int, out: List[str]) -> None:
    if depth > _MAX_DEPTH:
        raise SerializationIncompatibleError(
            f"nesting beyond {_MAX_DEPTH} levels", pointer
        )
    schema = _resolve_ref(schema, root)

    if isinstance(value, (_Pairs, dict)):
        pairs = value if isinstance(value, _Pairs) else list(value.items())
        members: Dict[str, Any] = {}
        data_order: List[str] = []
        for key, member in pairs:
            if not isinstance(key, str):
                raise SerializationIncompatibleError(
                    f"non-string object key {key!r}", pointer
                )
            if key in members:
                raise SerializationIncompatibleError(
                    f"duplicate object key {key!r}", pointer
                )
            members[key] = member
            data_order.append(key)
        resolved = schema
        schema = _effective_schema(schema, members, root)
        if _orderable(schema):
            declared = _declared_order(
                schema, root, topological=schema is resolved)
            undeclared = [k for k in data_order if k not in set(declared)]
            order = [k for k in declared if k in members] + undeclared
        else:
            order = data_order  # fallback: the schema gives no order
        out.append("{")
        for i, key in enumerate(order):
            if i:
                out.append(",")
            _emit_string(key, pointer, out)
            out.append(":")
            _emit(members[key], _subschema_for_key(schema, key, root),
                  root, pointer + "/" + _escape_pointer(key), depth + 1, out)
        out.append("}")
        return

    if isinstance(value, (list, tuple)):
        schema = _effective_schema(schema, value, root)
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _emit(item, _subschema_for_index(schema, i, root),
                  root, f"{pointer}/{i}", depth + 1, out)
        out.append("]")
        return

    if isinstance(value, _Lexeme):
        out.append(value)  # verbatim; json.loads already validated it
        return
    if value is None:
        out.append("null")
        return
    if value is True:
        out.append("true")
        return
    if value is False:
        out.append("false")
        return
    if isinstance(value, str):
        _emit_string(value, pointer, out)
        return
    if isinstance(value, int):
        out.append(str(value))
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SerializationIncompatibleError(
                f"non-finite number {value!r} (NaN/Infinity are not JSON)",
                pointer,
            )
        out.append(repr(value))  # shortest round-trip; JSON-valid spelling
        return
    raise SerializationIncompatibleError(
        f"unsupported value type {type(value).__name__}", pointer
    )


def _parse_json_text(instance: Union[str, bytes, bytearray]) -> Any:
    """Parse JSON text, keeping numeric lexemes and pair order verbatim."""
    if isinstance(instance, (bytes, bytearray)):
        try:
            instance = bytes(instance).decode("utf-8")
        except UnicodeDecodeError as e:
            raise SerializationIncompatibleError(
                f"input is not UTF-8: {e}", ""
            )
    try:
        return json.loads(
            instance,
            object_pairs_hook=_Pairs,
            parse_int=_Lexeme,
            parse_float=_Lexeme,
        )
    except json.JSONDecodeError as e:
        raise SerializationIncompatibleError(f"invalid JSON: {e.msg}", "")


def _coerce_schema(schema: Any) -> Any:
    if isinstance(schema, (bytes, bytearray)):
        schema = schema.decode("utf-8", "replace")
    if isinstance(schema, str):
        try:
            return json.loads(schema)
        except json.JSONDecodeError:
            return None  # no order information -> data order everywhere
    return schema


def _serialize_parsed(
    data: Any, schema: Any
) -> Tuple[Optional[bytes], SerializationStatus]:
    root = _coerce_schema(schema)
    try:
        out: List[str] = []
        _emit(data, root, root, "", 0, out)
        return "".join(out).encode("utf-8"), SerializationStatus(True)
    except SerializationIncompatibleError as e:
        return None, SerializationStatus(False, e.reason, e.pointer)


def serialize_json_text(
    instance: Union[str, bytes, bytearray], schema: Any
) -> Tuple[Optional[bytes], SerializationStatus]:
    """Serialize JSON text `instance` for `schema` (JSON-text entry point).

    `instance` MUST be JSON text (str/bytes/bytearray); numeric lexemes
    are preserved verbatim. An already-parsed Python value is a caller
    bug here - use `serialize_value` for it. `schema` is a dict/bool or
    JSON text.

    Returns (payload, status): payload is the compact UTF-8 bytes when
    status.ok, else None with status.reason/status.pointer classifying
    the serialization-incompatible case (section 7).
    """
    if not isinstance(instance, (str, bytes, bytearray)):
        raise TypeError(
            f"serialize_json_text expects JSON text (str/bytes), got "
            f"{type(instance).__name__}; use serialize_value for parsed values"
        )
    try:
        data = _parse_json_text(instance)
    except SerializationIncompatibleError as e:
        return None, SerializationStatus(False, e.reason, e.pointer)
    return _serialize_parsed(data, schema)


def serialize_value(
    value: Any, schema: Any
) -> Tuple[Optional[bytes], SerializationStatus]:
    """Serialize an already-parsed Python `value` for `schema`.

    The value is used exactly as given: a `str` is a JSON string value
    and is NEVER parsed as JSON text ("1" stays the string "1", "hello"
    serializes to '"hello"'); floats spell as repr per the section 5
    policy (`1e2` arrives as 100.0 and emits as `100.0`). bytes and
    bytearray are not JSON values and report as incompatible.
    `schema` is a dict/bool or JSON text.

    Returns (payload, status), same contract as `serialize_json_text`.
    """
    return _serialize_parsed(value, schema)


def serialize_for_schema(
    instance: Union[str, bytes, bytearray, Any], schema: Any
) -> Tuple[Optional[bytes], SerializationStatus]:
    """Serialize `instance` into the spec-v1 byte language for `schema`.

    Legacy dual-mode entry point kept for backward compatibility:
    str/bytes `instance` is JSON text (numeric lexemes preserved
    verbatim), anything else is an already-parsed Python value (floats
    spell as repr, per the section 5 policy). Because the two readings
    of a `str` are indistinguishable here, new code should call
    `serialize_json_text` or `serialize_value` explicitly instead.
    `schema` is a dict/bool or JSON text.

    Returns (payload, status): payload is the compact UTF-8 bytes when
    status.ok, else None with status.reason/status.pointer classifying
    the serialization-incompatible case (section 7).
    """
    if isinstance(instance, (str, bytes, bytearray)):
        return serialize_json_text(instance, schema)
    return serialize_value(instance, schema)
