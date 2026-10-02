"""Schema corpus for parity/edge tests.

SMALL - small schemas for exhaustive prefix enumeration (byte-level
tokenizer; depth bounds the trace length in tokens).
TRACE - larger schemas for the trace test (dict tokenizer).
LITERALS - sets for FR-3.
"""


def obj(props, required):
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


ACTION_AMOUNT = obj(
    {"action": {"type": "string", "enum": ["buy", "sell"]},
     "amount": {"type": "integer"}},
    ["action", "amount"])

OPT_KEYS = obj(
    {"a": {"type": "integer"},
     "b": {"type": "string", "maxLength": 1},
     "c": {"type": "boolean"}},
    ["a"])

ARR_INT = {"type": "array", "items": {"type": "integer"},
           "minItems": 1, "maxItems": 3}

STR_BOUNDED = {"type": "string", "maxLength": 1}

STR_MIN_MAX = {"type": "string", "minLength": 1, "maxLength": 2}

ENUM_PREFIX = {"type": "string", "enum": ["a", "ab", "abc"]}

NUMBER = {"type": "number"}

BOOL = {"type": "boolean"}

NULL = {"type": "null"}

EMPTY_OBJ = obj({}, [])

NESTED = obj(
    {"items": {"type": "array",
               "items": obj({"id": {"type": "integer"}}, ["id"]),
               "minItems": 0, "maxItems": 2}},
    ["items"])

ENUM_NUMBERS = {"type": "number", "enum": [1, 2.5, 100]}

ENUM_UNICODE = {"type": "string", "enum": ["héllo", "日本"]}

REF_SCHEMA = {
    "$defs": {"pos": {"type": "integer"}},
    "type": "object",
    "properties": {"x": {"$ref": "#/$defs/pos"}, "y": {"$ref": "#/$defs/pos"}},
    "required": ["x", "y"],
    "additionalProperties": False,
}

STR_UNBOUNDED = {"type": "string", "minLength": 0}

ARR_STR = {"type": "array", "items": {"type": "string", "maxLength": 2},
           "minItems": 0, "maxItems": 2}


SMALL = [
    {"name": "action_amount", "schema": ACTION_AMOUNT, "depth": 28},
    {"name": "opt_keys", "schema": OPT_KEYS, "depth": 20},
    {"name": "arr_int", "schema": ARR_INT, "depth": 12},
    {"name": "str_bounded", "schema": STR_BOUNDED, "depth": 8},
    {"name": "enum_prefix", "schema": ENUM_PREFIX, "depth": 8},
    {"name": "number", "schema": NUMBER, "depth": 4},
    {"name": "bool", "schema": BOOL, "depth": 6},
    {"name": "null", "schema": NULL, "depth": 5},
    {"name": "empty_obj", "schema": EMPTY_OBJ, "depth": 3},
    {"name": "nested", "schema": NESTED, "depth": 30},
]

# BFS state cap per schema (capped; FIFO order gives breadth-fair coverage:
# all shallow prefixes are checked before the cap)
SMALL_STATE_CAP = 8_000

TRACE = [
    {"name": "action_amount", "schema": ACTION_AMOUNT, "bounded_int": False},
    {"name": "opt_keys", "schema": OPT_KEYS, "bounded_int": False},
    {"name": "arr_int", "schema": ARR_INT, "bounded_int": True},
    {"name": "str_min_max", "schema": STR_MIN_MAX, "bounded_int": True},
    {"name": "enum_prefix", "schema": ENUM_PREFIX, "bounded_int": True},
    {"name": "nested", "schema": NESTED, "bounded_int": False},
    {"name": "enum_numbers", "schema": ENUM_NUMBERS, "bounded_int": True},
    {"name": "enum_unicode", "schema": ENUM_UNICODE, "bounded_int": True},
    {"name": "arr_str", "schema": ARR_STR, "bounded_int": True},
    {"name": "ref_schema", "schema": REF_SCHEMA, "bounded_int": False},
]

LITERALS = [
    {"name": "lits_basic", "strings": ["", "a", "ab", "b"]},
    {"name": "lits_prefix", "strings": ["buy", "buyer", "sell"]},
    {"name": "lits_unicode", "strings": ["héllo", "日本", ""]},
]
