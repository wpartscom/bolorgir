# ADR-0011: joint completion certificate for numeric conjunctions (spec-v1)

- Status: **accepted** (2026-10-04, technical approval by the owner;
  proposed 2026-10-02, audit-fix spec rev 2.2, task T1).
- Context: spec-v1 compiles the numeric keywords of one schema into
  separate value machines over the same number lexeme: `num_range`
  (minimum/maximum and their exclusive forms), `num_mult` (multipleOf),
  `int_num` (`"type": "integer"`, which behaves as multipleOf 1),
  `num_excl` (`not` of `const`/`enum` numbers) and `num_const` (numeric
  `const`), joined by an `allOf` comb (`src/schema.zig`,
  `numConstrainedNode`). The ADR-0005 certificates in `src/complete.zig`
  (`certNumRange`, `certNumMult`, `certNumExcl`, ...) decide each machine
  alone. A conjunction can be dead while every machine is alive: for
  `{"type":"integer","maximum":9}` after `51` the range machine accepts
  `5.1` and the integer machine accepts `51`, but no integer of at most 9
  starts with the digits `51`. ADR-0005 D3 then hands the state to the
  budgeted token search, which cannot prove a dead end (the state has
  infinitely many continuations: `510e-2`, `51.0e-1`, ...), spends
  `FILL_SEARCH_BUDGET` and reports `RESOURCE_LIMIT`. Measured on a
  byte vocabulary: 27 of 30 random walks failed for
  `{"type":"integer","minimum":1,"maximum":9}`, 30 of 30 for
  `{"type":"integer","minimum":0,"maximum":1}`.

## Definitions

A *lexeme state* is the part of a JSON number typed so far. It fixes:

- the sign `s` once `-` or a first digit is typed;
- `P`, the typed significant digits (from the first nonzero digit on,
  trailing zeros included), `n = len(P)`; `P' = P` without its `tz`
  trailing zeros;
- `om_m`, the order of magnitude of the first significant digit as typed
  (`int_len - 1` in the integer part, `-(fz + 1)` after `fz` leading
  fraction zeros); `frac_len`, the number of fraction digits;
- the phase: no significant digit yet, extensible mantissa (integer or
  fraction digits, dot), or frozen mantissa (`e`, exponent sign, exponent
  digits with typed value `E`).

A *conjunction* `C` over one lexeme has ranges (lower and upper bounds,
each inclusive or exclusive), divisors (`multipleOf d`, integer =
`multipleOf 1`), excluded constants `X` (finite) and required constants
`K`. `Reach(state)` is the set of values of all complete numbers that
extend the typed bytes. The certificate decides
`Reach(state) ∩ Sat(C) ≠ ∅` exactly, with decimal arithmetic only
(no binary floating point).

Every machine keeps a compressed summary of the lexeme, and the
certificate reads only these summaries: `num_range` holds the sign,
`om_m`, the exponent, and per bound a comparison state (`cmp`: 0 equal so
far, 1 below, 2 above, with zero padding past the bound's digits) and the
number `j` of bound digits matched; `num_mult` holds `P mod a`,
`P' mod a`, `frac_len`, `tz` and the exponent; `num_excl` holds per
constant a match state (leading zeros, matching, tail of zeros after all
digits, ruled out) and its tail length.

## Decisions

**D1. One closed-form verdict per conjunction (`src/numcert.zig`,
`termVerdict`).** `certifyState` groups the parser threads of one `allOf`
comb whose top frames are numeric leaves over the same lexeme, expands
nested `allOf`/`choice` shapes to a disjunctive normal form over the
threads, and decides each term jointly. The group is alive iff some term
is alive; the verdict replaces the per-frame verdicts of the group's
threads and proves the comb instances on an alive verdict. Only an
`undecided` verdict (D9) reaches the budgeted search. Merging the
machines into one node at compile time was rejected: the comb already
runs them in lockstep over the same bytes, so their summaries are
available together at every state, and a merged node would duplicate
five lexers.

**D2. Combined divisor.** All divisors of a term must be equal, or one is
integer: `lcm(1, a x 10^b)` is `a x 10^b` for `b >= 0` and
`a / gcd(a, 10^-b)` for `b < 0` (for reduced fractions,
`lcm(p/q, r/t) = lcm(p, r) / gcd(q, t)`; `a x 10^b` with `b < 0`
reduces to `(a / gcd(a, 10^-b)) / (10^-b / gcd(a, 10^-b))`). Write the
combined divisor as `D = A x 10^B`. Two different `multipleOf` values in
one term are not combined (their lcm needs the full rational lcm and new
residues): the term is `undecided` (D9).

**D3. Zero.** Zero is reachable iff no significant digit has been typed
(more zeros, a frozen all-zero mantissa, or an empty exponent all spell
0). It satisfies `C` iff it lies in every range and is not excluded; it is
a multiple of every divisor. A zero witness ends the verdict.

**D4. Magnitude bounds on the sign's side.** For a nonzero value with
sign `s`, write `V = s y`, `y > 0`. For `V > 0` the upper bound of `y`
is the tightest `maximum` (an upper bound `<= 0` forbids the sign) and
the lower bound is the tightest positive `minimum`. For `V < 0` the upper
bound is `|minimum|` (a `minimum >= 0` forbids the sign) and the lower
bound is `|maximum|` for a negative `maximum`. With equal values the
exclusive one is tighter. `L > H`, or `L = H` with an exclusive end,
forbids the sign. A divisor bounds `y` from below by `D`, so
`om(y) >= B + digits(A) - 1`. The claim of spec revision 1 that one
finite bound makes the set of orders of magnitude finite is false
(`{"type":"number","maximum":9}` after `5` reaches `5 x 10^t` for every
`t <= 0`) and is not used.

**D5. No significant digit yet.** Lemma A: from a state with an all-zero
mantissa in a non-exponent phase, every nonzero finite decimal of the
fixed sign (both signs at the start) is reachable. Proof: write
`y = M x 10^q` with digit string `M`; append (after a dot when needed) the
digits of `M`, then the exponent `e` that places the leading digit at
`om(y)`; the exponent is still free, so every integer `e` is available.
The question becomes: is there `y` in `(L, H)` (ends as in D4) that is a
multiple of `D` and not in `X`?

- No `H`: alive. Witness: `A x 10^m` with `m` above `L`, above every
  excluded constant and `>= B`.
- No `L` and no divisor: alive. Witness: a power of ten below `H` and
  below every excluded constant.
- Otherwise `[L', H]` with `L' = L` or `L' = 0` (exclusive) is a finite
  interval with decimal ends. Without a divisor and with positive length
  it holds infinitely many decimals and finitely many excluded ones:
  alive. With a single point, alive iff it is not excluded. With a
  divisor, the multiples in the interval are counted exactly with big
  integers on the common grid `10^g0` (`g0` the least exponent among the
  ends, `B` and the excluded constants), and the verdict is alive iff the
  count exceeds the number of excluded multiples in the interval.

**D6. Extensible mantissa with a significant digit.** Lemma B: let `t` be
any integer and `k = t - n + 1`. The reachable values at order of
magnitude `t` are exactly the finite decimals of
`J_t = [P x 10^k, (P + 1) x 10^k)` (with the sign). Proof: appending
`w` digits `x` (`0 <= x < 10^w`) and an exponent gives
`(P x 10^w + x) x 10^(k - w)`; the exponent compensates the shift of the
leading digit (`om_m + w` in the integer phase, `om_m` otherwise), so
every `t` is available. Conversely every decimal `y` in `J_t` has this
form for `w >= k - exp(y)`. The sets `J_t` are disjoint and
`J_t ⊂ [10^t, 10^(t+1))`.

Lemma C (monotonicity): the number of multiples of `D` in `J_(t-1)` is at
most the number in `J_t`. Proof: `J_(t-1) = J_t / 10`, and `v -> 10 v`
maps multiples of `D` in `J_(t-1)` injectively to multiples of `10 D`,
hence of `D`, in `J_t`.

Count in an uncut `J_t` (`src/numcert.zig`, `countUncut`): for `k >= B`,
`J_t / 10^B` is the integer interval `[P 10^w, P 10^w + 10^w)` with
`w = k - B`; its multiples of `A` start at offset
`d0 = (-P 10^w) mod A`, so the count is 0 when `d0 >= 10^w` and
`floor((10^w - 1 - d0) / A) + 1` otherwise; only `P mod A` is needed. For
`k < B` a multiple `m x 10^B` in `J_t` satisfies
`m x 10^(B-k) ∈ [P, P + 1)`, an integer, so it equals `P x 10^k`: the
count is 1 iff `tz >= B - k` and `P' x 10^(tz - (B - k)) ≡ 0 (mod A)`,
else 0.

Decision:

- No `H`: alive. Witness: append `digits(A) + 1` digits that make the
  mantissa `≡ 0 (mod A)` and place the value above every constant of the
  schema; the large exponent also covers `10^B`.
- No `L` and no divisor: alive. Witness: place the typed digits at an
  order of magnitude below `H` and below every excluded constant.
- Otherwise only `t` in `[t_lo, t_hi]` can meet `(L, H)`, with
  `t_hi = om(H)` and `t_lo = om(L)` (or `B + digits(A) - 1`). For `t`
  strictly between the bounds' orders, `J_t ⊂ (L, H)` (uncut). At
  `t = om(L)` the comparison state decides: `cmp = 1` puts all of `J_t`
  below `L`, `cmp = 2` all of it above, `cmp = 0` means `P` equals the
  first `n` digits of `L` (zero-padded), so `L ∈ J_t` and the cut interval
  `[L, (P + 1) x 10^k)` has exact decimal ends. Symmetrically at
  `t = om(H)`. Cut intervals are decided as in D5 (exact count, with the
  excluded constants inside). With a divisor the right end of a cut
  interval moves to `left + 10^max(k, g - 1)` (`g` the least exponent of
  the bounds, `B` and the excluded constants): every point that matters
  lies on the `10^g` grid, the left end is a truncation of a bound and is
  on it, so both intervals hold the same grid points; this keeps the big
  integers at the size of the schema constants. Without a divisor and
  exclusions a cut interval is empty only for an exclusive `H` equal to
  its left end (all digits of `H` matched); a cutting `L` always leaves
  positive length.
- Excluded constants matter at `t` only when their order of magnitude is
  `t` and their digits extend `P` (`num_excl` states 1 and 2); an uncut
  `t` is alive iff its count exceeds the number of such constants that
  are multiples of `D`.
- The scan runs from `t_hi` down to `t_lo`. By Lemma C an uncut `t` with
  count 0 makes every lower `t` (cut intervals are subsets) count 0, so
  the scan stops with `dead`. The scan length is bounded by the orders of
  magnitude of the schema constants.

**D7. Frozen mantissa.** The value is `V = s P' x 10^(e - frac_len + tz)`
with order of magnitude `om_m + e`, for `e` in the exponent set of the
phase: all integers after `e`; `e >= 0` or `e <= 0` after the exponent
sign; `±N` with `N ∈ ext(E) = {E} ∪ {E x 10^j + y : j >= 1,
0 <= y < 10^j}` after exponent digits (every `N >= 0` when `E = 0`,
leading zeros being allowed in the exponent). The constraints on `e`:

- ranges: `om(L) - om_m <= e <= om(H) - om_m`; interior values pass, the
  two boundary values need the point test: `cmp = 1` below, `cmp = 2`
  above, `cmp = 0` equal when all bound digits are matched and below
  when `j < len(bound digits)` (the bound's canonical tail ends in a
  nonzero digit);
- divisor: `V` is a multiple of `A x 10^B` iff
  `s' = e - frac_len + tz - B >= s_min`, where `s_min` is the least
  `s' >= 0` with `P' x 10^s' ≡ 0 (mod A)` (none exists when the part of
  `A` coprime to 10 does not divide `P'`: dead). A negative `s'` cannot
  work because `P'` has no trailing zero. Since `A < 2^32`, its 2- and
  5-adic valuations are at most 32, so testing `s' <= 64` finds `s_min`
  when it exists;
- exclusions: an excluded constant whose digits equal `P'` (`num_excl`
  tail state) equals `V` for exactly one `e`; others never do.

The feasible set is the exponent set within the range limits, minus at
most two boundary values and at most `|X|` excluded values. Enumerating
its first `|X| + 3` elements in ascending order (descending from the
upper limit when there is no lower one) either finds a feasible `e` or
exhausts the set; `ext(E)` is enumerated block by block
(`E x 10^j + [0, 10^j)`), which is ascending. A typed exponent at or
above `2^31` is saturated in the machines: the value is then far beyond
every constant, so the verdict follows from the side of the bounds, and
it is `undecided` when a limit itself lies beyond `2^30`.

**D8. Constants.** All required constants of a term must be equal
(otherwise dead); the typed spelling must still reach that value
(`num_const` state, the ADR-0005 rule), and the value must lie in every
range, be a multiple of `D` and not be excluded.

**D9. Undecided, never wrong.** The certificate answers `undecided` for
terms it does not model: two different `multipleOf` divisors, more than
32 leaves or excluded constants, machines in different lexer phases,
scaled integers above 20000 digits, a DNF above 64 terms, or a
saturated exponent next to a limit beyond `2^30`. These states go to the
budgeted search as before (and may still end in `RESOURCE_LIMIT`, which
task T3 treats).

**D10. Compile-time emptiness.** `nodeVerdict` applies the same
certificate to the start state of every DNF term of a numeric-only node
(arms that never spell a number, such as strings, containers and
literals that do not start with `-` or a digit, drop out). When every term
is dead, the subschema is empty (ADR-0006 D1, `emptySubschema`): the
whole schema compiles to `BLG_ERR_UNSATISFIABLE_CONSTRAINT`, a nested one
to the empty language. Before this decision such schemas compiled and
failed at the first mask with `DEAD_END`.

**D11. Byte level and token level.** The certificate proves reachability
at the language level (bytes). It equals reachability at the token level
only for byte-complete vocabularies (ADR-0005 D1), where every byte
continuation is served by single-byte tokens. With partial byte coverage
the core refuses every infinite language at compile time
(`UnprovenPartialVocab`, `BLG_ERR_UNSUPPORTED_TOKENIZER`); spec-v1
numbers have infinitely many spellings, so the certificate is never used
as a token-level proof for such vocabularies.

**D12. Single range machines.** The frozen-mantissa point test of
`certNumRange` (`rangePtLo`/`rangePtHi`) treated `cmp = 0` as equality
even when the typed digits were a proper prefix of the bound's digits
(`{"minimum":1.5,"maximum":1.6}` after `1e` was certified alive although
`1e0 = 1`). It now uses the D7 rule.

## Verification

- Zig unit tests (`src/complete.zig`): the prefix table of the spec
  (joint verdicts after `-51`, `12`, `5`, `61`, ...) and the D12 cases.
- Cross-check against an independent reference (`tests/numconj_cert_check.py`
  with a core built by `zig build -Dtest_hooks=true`): for every prefix up
  to a given length over a reduced alphabet that covers every lexer
  transition (`0159-.e+`), an `alive` verdict must come with a witness
  continuation whose value satisfies the conjunction in exact rational
  arithmetic (`fractions.Fraction`), and `tests/reference.py`
  (`numconj_reachable`, an independent implementation of the same
  mathematics) must agree; a `dead` verdict must agree with the reference
  and, on a sample, a bounded parser search without certificate (up to
  12 more bytes) must find no completion. The release build has no
  `blg_test_*` symbols (checked in CI).
- `tests/test_spec_v1_p4_numbers_reachability.py`: random walks to EOS
  for every schema of the spec's tables on three byte-complete
  vocabularies, both modes, fast path on and off (240 walks per schema),
  validated with an exact-number `jsonschema` validator; the masks over
  the number bytes after the table prefixes equal the reference; the
  fallback search is never entered (`blg_cert_stats`, `search_entered`).

## Consequences

- The table schemas of T1 generate without `RESOURCE_LIMIT` and without
  entering the fallback search on byte-complete vocabularies.
- Numeric conjunctions with an empty language are refused at compile time
  (`UNSATISFIABLE_CONSTRAINT`) instead of failing at the first mask.
- canonical-v1 is unchanged (the certificate runs for spec-v1 grammars
  only; canonical-v1 has no comb groups of numeric machines).
- `blg_cert_stats` gains a last entry, `search_entered` (diagnostics,
  additive; `BLG_ABI_VERSION` stays 1).
