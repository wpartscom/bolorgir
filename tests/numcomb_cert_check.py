"""Cross-check of the numeric group certificate on oneOf, if/then/else and
not over numeric constraints (src/complete.zig groupDnf, numcert negated
literals).

For random schemas and every number prefix up to MAX_LEN bytes over a
reduced alphabet, the mask of the byte-level engine is compared with an
exact bounded search: a byte the search completes to a valid number
(exact decimals, at most --search-len more bytes) must be allowed, and a
byte the engine forbids must have no such completion. A RESOURCE_LIMIT mask
is counted separately; both other mismatches fail.

Prints a JSON summary; exit status 1 on any mismatch.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from decimal import Decimal, InvalidOperation
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import blg_ctypes as blg  # noqa: E402
import reference as ref  # noqa: E402

CONSTS = ["0", "1", "2", "5", "10", "0.5", "1.5", "-1", "-2", "-10", "2.5", "100"]
DIVISORS = ["2", "3", "0.5", "5", "10"]
STEP = "0125.e-"  # prefix alphabet
SEARCH = "0125.e-"  # completion alphabet (a subset: its witnesses are real)


def atom(rng):
    k = rng.randrange(9)
    if k == 0:
        return {"type": "integer"}
    if k == 1:
        return {"type": "number"}
    key = ["minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"][k % 4]
    if k < 6:
        return {key: json.loads(rng.choice(CONSTS))}
    if k == 6:
        return {"multipleOf": json.loads(rng.choice(DIVISORS))}
    if k == 7:
        return {"not": atom(rng)}
    return {"minimum": json.loads(rng.choice(CONSTS)), "maximum": json.loads(rng.choice(CONSTS))}


def schema(rng):
    k = rng.randrange(5)
    if k == 0:
        return {"oneOf": [atom(rng), atom(rng)]}
    if k == 1:
        return {"oneOf": [atom(rng), atom(rng), atom(rng)]}
    if k == 2:
        return {"if": atom(rng), "then": atom(rng), "else": atom(rng)}
    if k == 3:
        return {"if": atom(rng), "then": atom(rng)}
    return {"type": "number", "allOf": [atom(rng), {"not": atom(rng)}]}


def num_value(text: str):
    try:
        json.loads(text)
    except ValueError:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def valid(s, v: Decimal) -> bool:
    if s is True or s == {}:
        return True
    ok = True
    for key, c in s.items():
        if key == "type":
            ok &= c == "number" or (c == "integer" and v == v.to_integral_value())
        elif key == "minimum":
            ok &= v >= Decimal(str(c))
        elif key == "maximum":
            ok &= v <= Decimal(str(c))
        elif key == "exclusiveMinimum":
            ok &= v > Decimal(str(c))
        elif key == "exclusiveMaximum":
            ok &= v < Decimal(str(c))
        elif key == "multipleOf":
            ok &= (Fraction(v) / Fraction(str(c))).denominator == 1
        elif key == "not":
            ok &= not valid(c, v)
        elif key == "oneOf":
            ok &= sum(valid(b, v) for b in c) == 1
        elif key == "allOf":
            ok &= all(valid(b, v) for b in c)
        elif key == "if":
            if valid(c, v):
                ok &= valid(s.get("then", True), v)
            else:
                ok &= valid(s.get("else", True), v)
        elif key in ("then", "else"):
            pass
        else:
            raise ValueError(key)
    return ok


def completable(s, text: str, budget: int, memo: dict) -> bool:
    key = (text, budget)
    if key in memo:
        return memo[key]
    v = num_value(text)
    res = v is not None and valid(s, v)
    if not res and budget > 0:
        res = any(completable(s, text + ch, budget - 1, memo) for ch in SEARCH)
    memo[key] = res
    return res


def check(sch, max_len: int, search_len: int, stats: dict, fails: list, unverified: list):
    spec = ref.TokenizerSpec(tokens=tuple(bytes([i]) for i in range(256)) + (b"",),
                             eos_ids=(256,), special_ids=()).validate()
    ctx = blg.Context(spec)
    try:
        g = blg.compile_schema(ctx, json.dumps(sch).encode(), profile=b"spec-v1")
    except blg.CoreFailure:
        stats["refused"] += 1
        ctx.destroy()
        return
    memo: dict = {}
    frontier = [""]
    try:
        while frontier:
            p = frontier.pop()
            s = blg.Session(ctx, g)
            try:
                if not all(s.accept(ord(ch)) == blg.BLG_OK for ch in p):
                    continue
                status, words = s.fill_mask_words()
            finally:
                s.destroy()
            stats["prefixes"] += 1
            if status == blg.BLG_ERR_RESOURCE_LIMIT:
                stats["resource_limit"] += 1
                continue
            for ch in STEP:
                if p == "" and ch not in "0125-":
                    continue
                allowed = status == blg.BLG_OK and bool(words[ord(ch) // 32] >> (ord(ch) % 32) & 1)
                found = completable(sch, p + ch, search_len, memo)
                if found and not allowed:
                    fails.append({"schema": sch, "prefix": p, "byte": ch, "kind": "refused_completable"})
                elif allowed and not found:
                    stats["allowed_unverified"] += 1
                    unverified.append({"schema": sch, "prefix": p, "byte": ch})
                if not allowed and found is False:
                    stats["dead_checked"] += 1
                if allowed and len(p) + 1 < max_len:
                    frontier.append(p + ch)
    finally:
        blg.grammar_release(g)
        ctx.destroy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schemas", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-len", type=int, default=4)
    ap.add_argument("--search-len", type=int, default=4)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    stats = {"schemas": 0, "refused": 0, "prefixes": 0, "resource_limit": 0,
             "dead_checked": 0, "allowed_unverified": 0}
    fails: list = []
    unverified: list = []
    for _ in range(args.schemas):
        stats["schemas"] += 1
        check(schema(rng), args.max_len, args.search_len, stats, fails, unverified)
    print(json.dumps({"stats": stats, "failures": fails[:20], "n_failures": len(fails),
                      "unverified": unverified[:200]}, indent=1))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
