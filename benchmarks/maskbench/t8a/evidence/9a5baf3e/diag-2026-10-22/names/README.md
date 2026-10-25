# Property-name states: baseline before the next change

`name-states.json` is the output of
`benchmarks/maskbench/t8a/diag/name_states.py` on the artifact
`4b567de3b3d42ae5434c22a486ed3eb283edbc1a24965a944f073e900d5ebbd3`
(the current candidate, the counter `on` variant). It is a baseline: no
change to the name path is part of this package.

A case is a schema, a prefix and its expectations. The prefix is fed through
the run's tokenizer and through a byte-complete vocabulary; the mask is taken
with the fast path off and on in both, and each pair is compared bit for bit.
A case fails when the prefix is not accepted in every leg, when an expected
`ResourceLimitError` does not occur in every leg - the fast path may fall
back, never change the outcome -, when a mask is missing unexpectedly, when
the two settings disagree, or when the fast path does not meet
`--expect-fast`. All 18 cases pass with `--expect-fast before`: the fast path
fires nowhere, so the equality confirms the starting point only. The
`propertyNames` case reports `ResourceLimitError` in all four legs
(`byte/0`, `byte/1`, `real/0`, `real/1`).

| case | allowed (real vocab) | mask | stateAlive | trie nodes |
|---|---:|---:|---:|---:|
| `o34343`, open, at `{"` | 123 189 | 881.0 ms | 266 094 | 383 693 |
| `o34343`, `{"options` | 123 178 | 896.7 ms | 266 081 | 383 669 |
| `o34343`, `{"x` | 123 189 | 872.0 ms | 266 094 | 383 693 |
| `o64963`, open, at `{"` | 123 189 | 901.5 ms | 266 094 | 383 693 |
| `o64963`, `{"class` | 123 178 | 888.9 ms | 266 083 | 383 673 |
| `o21292`, closed, at `{"` | 6 | 4.3 ms | 9 | 328 |
| `o21292`, closed, `{"a` | 3 | 5.3 ms | 3 | 307 |
| `o21292`, closed, `{"analytics_` | 6 | 4.3 ms | 10 | 342 |
| synthetic open object | 123 189 | 834.3 ms | 0 | 383 693 |
| synthetic closed, `alpha`/`beta` | 3 | 3.8 ms | 3 | 301 |
| synthetic `propertyNames` with a pattern | - | `ResourceLimitError` after 65 979 ms | 1 | 33 |
| synthetic `patternProperties`, closed | 3 | 877.5 ms | 265 884 | 383 170 |
| `alpha` used once, closed | 4 | 3.8 ms | 4 | 308 |
| `alpha` used once, open | 123 189 | 882.7 ms | 266 094 | 383 693 |
| `maxProperties: 1`, at `{"` | 1 | 867.3 ms | 266 094 | 383 693 |
| `maxProperties: 1`, `{"a` | 3 | 855.2 ms | 266 080 | 383 666 |
| the same without the cap, at `{"` | 123 189 | 878.3 ms | 266 094 | 383 693 |
| the same without the cap, `{"a` | 123 178 | 889.2 ms | 266 080 | 383 666 |

Reading and the scope of lemma K1 are in section 14 of
`mask-fast-path-needs-reachability.md` next to this directory. K1 excludes
capacity, dependencies, `patternProperties`, `propertyNames` and closed
objects; it requires the key machine in its normal content state
(`u.key.st == .normal`, `rem == 0`), keeps the value lemma's enclosure
preconditions over the whole stack (an armed tap or a combinator above the
object refuses K1 as well), and reads the additional value from the compiled
node, because `unevaluatedProperties` can constrain it even when
`additionalProperties` is absent.

