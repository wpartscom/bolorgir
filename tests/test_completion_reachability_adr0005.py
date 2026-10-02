"""Prototype of the ADR-0005 completion-reachability contract (ROADMAP 4.1).

Standalone reference model of the budgeted common-completion search over
product states: finite languages as prefix automata, products for
intersection (allOf), unions for anyOf. Pins the regression counterexample
`{ab, xy} ∩ {ac, xy}`: the JSON prefix `"a` is live in every branch, no
common completion exists, all 256 single-byte tokens dead-end - yet the
schema is satisfiable (`"xy"`), so a dead prefix is never the
`empty language` bucket. No engine dependency; when P3 lands allOf the
same case is re-encoded against the real engine.
"""

from __future__ import annotations

from collections import deque

# Search verdicts (ADR-0005 Decision 2).
REACHABLE = "reachable"
EMPTY = "empty"
BUDGET_EXHAUSTED = "budget_exhausted"

# Outcome buckets (ADR-0005 Decision 3, ROADMAP section 1).
SATISFIABLE = "satisfiable"
EMPTY_LANGUAGE = "empty language"
UNSUPPORTED_FEATURE = "unsupported_feature"
RESOURCE_LIMIT = "resource_limit"

ALL_BYTES = tuple(bytes((b,)) for b in range(256))


class FiniteLanguage:
    """Prefix automaton of a finite set of documents; state = bytes fed."""

    def __init__(self, words):
        self.words = frozenset(words)
        assert self.words

    def initial(self):
        return b""

    def feed(self, state, byte):
        nxt = state + bytes((byte,))
        return nxt if any(w.startswith(nxt) for w in self.words) else None

    def accepts(self, state):
        return state in self.words


class Product:
    """Intersection (allOf): alive iff every component is alive."""

    def __init__(self, *components):
        self.components = components

    def initial(self):
        return tuple(c.initial() for c in self.components)

    def feed(self, state, byte):
        nxt = tuple(c.feed(s, byte) for c, s in zip(self.components, state))
        return None if any(s is None for s in nxt) else nxt

    def accepts(self, state):
        return all(c.accepts(s) for c, s in zip(self.components, state))


class Union:
    """anyOf: alive iff some branch is alive; disjunctive completion."""

    def __init__(self, *branches):
        self.branches = branches

    def initial(self):
        return tuple(b.initial() for b in self.branches)

    def feed(self, state, byte):
        nxt = tuple(
            None if s is None else b.feed(s, byte)
            for b, s in zip(self.branches, state)
        )
        return None if all(s is None for s in nxt) else nxt

    def accepts(self, state):
        return any(
            s is not None and b.accepts(s)
            for b, s in zip(self.branches, state)
        )


def common_completion(sys, state, continuations, *, budget=1 << 16, memo=None):
    """Budgeted BFS for a finite token continuation to an accepting state.

    Termination: the component state spaces are finite (prefixes of finite
    literals), so the product is finite and the visited set bounds the
    search; `budget` caps enqueued successor states and turns an overrun
    into BUDGET_EXHAUSTED instead of a partial answer. `memo` models the
    per-session verdict cache; it must never change a verdict.
    """
    if memo is not None and state in memo:
        return memo[state]
    verdict = _search(sys, state, continuations, budget)
    if memo is not None:
        memo[state] = verdict
    return verdict


def _search(sys, state, continuations, budget):
    if sys.accepts(state):
        return REACHABLE
    seen = {state}
    queue = deque((state,))
    while queue:
        cur = queue.popleft()
        for tok in continuations:
            nxt = cur
            for byte in tok:
                nxt = sys.feed(nxt, byte)
                if nxt is None:
                    break
            if nxt is None or nxt in seen:
                continue
            if sys.accepts(nxt):
                return REACHABLE
            if budget <= 0:
                return BUDGET_EXHAUSTED
            budget -= 1
            seen.add(nxt)
            queue.append(nxt)
    return EMPTY


def classify_schema(sys, *, budget=1 << 16, decidable=True):
    """Outcome bucket of a whole schema (ADR-0005 Decision 3).

    EMPTY maps to `empty language` only as a whole-schema proof from the
    initial state; undecided is a refusal, budget exhaustion a limit.
    """
    if not decidable:
        return UNSUPPORTED_FEATURE
    verdict = common_completion(sys, sys.initial(), ALL_BYTES, budget=budget)
    if verdict == BUDGET_EXHAUSTED:
        return RESOURCE_LIMIT
    if verdict == EMPTY:
        return EMPTY_LANGUAGE
    return SATISFIABLE


def reachable_states(sys, continuations):
    """Every component-live state reachable from the initial state."""
    seen = {sys.initial()}
    queue = deque((sys.initial(),))
    while queue:
        cur = queue.popleft()
        for tok in continuations:
            nxt = cur
            for byte in tok:
                nxt = sys.feed(nxt, byte)
                if nxt is None:
                    break
            if nxt is not None and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


# ROADMAP 4.1: L1 = {"ab", "xy"}, L2 = {"ac", "xy"} as JSON documents.
L1 = FiniteLanguage((b'"ab"', b'"xy"'))
L2 = FiniteLanguage((b'"ac"', b'"xy"'))
PRODUCT = Product(L1, L2)


def feed_bytes(sys, state, data):
    for byte in data:
        state = sys.feed(state, byte)
        if state is None:
            return None
    return state


def test_prefix_live_in_every_branch_but_no_common_completion():
    st = feed_bytes(PRODUCT, PRODUCT.initial(), b'"a')
    assert st is not None  # component-live: prefix of "ab" in L1, "ac" in L2
    assert common_completion(PRODUCT, st, ALL_BYTES) == EMPTY
    # The schema itself is satisfiable: "xy" is a common completion.
    assert common_completion(PRODUCT, PRODUCT.initial(), ALL_BYTES) == REACHABLE
    assert classify_schema(PRODUCT) == SATISFIABLE


def test_all_256_single_byte_tokens_dead_end():
    st = feed_bytes(PRODUCT, PRODUCT.initial(), b'"a')
    assert not PRODUCT.accepts(st)  # EOS not admitted either
    for tok in ALL_BYTES:
        nxt = feed_bytes(PRODUCT, st, tok)
        if nxt is None:
            continue  # byte-illegal, pruned by the mask walk
        assert common_completion(PRODUCT, nxt, ALL_BYTES) == EMPTY


def test_dead_prefix_is_not_the_empty_language_bucket():
    # EMPTY from a non-initial state is a runtime DeadEnd, never a
    # schema-level classification; the bucket requires the root proof.
    assert classify_schema(PRODUCT) == SATISFIABLE
    empty_product = Product(
        FiniteLanguage((b'"ab"',)), FiniteLanguage((b'"cd"',))
    )
    assert classify_schema(empty_product) == EMPTY_LANGUAGE


def test_budget_exhaustion_and_undecided_are_never_empty_language():
    assert common_completion(PRODUCT, PRODUCT.initial(), ALL_BYTES, budget=0) == BUDGET_EXHAUSTED
    assert classify_schema(PRODUCT, budget=0) == RESOURCE_LIMIT
    assert classify_schema(PRODUCT, decidable=False) == UNSUPPORTED_FEATURE


def test_union_completion_is_disjunctive():
    union = Union(L1, L2)
    st = feed_bytes(union, union.initial(), b'"a')
    # The same prefix dead-ends the product but completes via branch L1.
    assert common_completion(union, st, ALL_BYTES) == REACHABLE


def test_token_level_every_allowed_token_and_eos():
    # L1 ∩ L3 = {"ab"} exactly: exhaustive token-level verification.
    l3 = FiniteLanguage((b'"ab"', b'"zz"'))
    prod = Product(L1, l3)
    vocab = (b'"', b"a", b"b", b"c", b"z", b'"a', b'ab"', b'"ab"', b"zz")
    memo = {}
    documents = set()
    for st in reachable_states(prod, vocab):
        fresh = common_completion(prod, st, vocab)
        cached = common_completion(prod, st, vocab, memo=memo)
        assert cached == fresh  # cache on/off equality of verdicts
        eos_allowed = prod.accepts(st)
        if eos_allowed:
            documents.add(st[0])
        allowed = 0
        for tok in vocab:
            nxt = feed_bytes(prod, st, tok)
            if nxt is None:
                continue
            if common_completion(prod, nxt, vocab) != REACHABLE:
                continue  # dead-end token stays out of the mask
            allowed += 1
            # Every allowed token participates in a real completion.
            assert common_completion(prod, nxt, ALL_BYTES) == REACHABLE
        # A component-live state either completes or is a dead end; it is
        # never silently half-open.
        assert (allowed > 0 or eos_allowed) == (fresh == REACHABLE)
    assert documents == {b'"ab"'}
