# Property-name states under lemma K1

> The artifact of this package, `7a4dcbd2...dc80`, is withdrawn: its
> `any_nodes` bitset aliased the `pat_len` table (see the package README).
> The VE replay failed on `Github_hard---o74466` with 12 field-mismatch
> records across 5 instances. The name-state numbers below describe this
> build on the checked name-state inputs; the fixed artifact is
> `7eb8ed01...3be0`.

`name-states-after.json` is the output of
`benchmarks/maskbench/t8a/diag/name_states.py --expect-fast after` on the
artifact `7a4dcbd2e9212f02f19f9e2e09dade39502955111422b7b5712605c373a0dc80`
(built with `zig build -Drelease=true -Dstrip=true`, counters on). The
library was pinned in a diagnostic package whose `_lib` symlinks point at the
saved copy; `ve_lib_record.py` recorded the resolved hash before and after
the slice and the VE leg.

A case is a schema, a prefix and its expectations. The prefix is fed through
the run's tokenizer and through a byte-complete vocabulary; the mask is taken
with the fast path off and on in both, and each pair is compared bit for bit.
A case fails when the prefix is not accepted in every leg, when an expected
`ResourceLimitError` does not occur in every leg - the fast path may fall
back, never change the outcome -, when a mask is missing unexpectedly, when
the two settings disagree, or when the fast path does not meet
`--expect-fast`. `--expect-fast after` checks the `name_used` counter, so a
string-lemma hit cannot satisfy it.

All 18 cases pass. K1 fires on the nine marked cases; the other nine stay on
the exact path and the pairs are still bit for bit equal:

| case | allowed (real vocab) | mask | stateAlive | trie nodes |
|---|---:|---:|---:|---:|
| `o34343`, open, at `{"` | 123 189 | 18.9 ms | 1 364 | 16 782 |
| `o34343`, `{"options` | 123 178 | 19.0 ms | 1 353 | 16 758 |
| `o34343`, `{"x` | 123 189 | 18.5 ms | 1 364 | 16 782 |
| `o64963`, open, at `{"` | 123 189 | 18.6 ms | 1 364 | 16 782 |
| `o64963`, `{"class` | 123 178 | 18.7 ms | 1 356 | 16 762 |
| `o21292`, closed, at `{"` | 6 | 5.9 ms | 9 | 328 |
| `o21292`, closed, `{"a` | 3 | 6.0 ms | 3 | 307 |
| `o21292`, closed, `{"analytics_` | 6 | 4.4 ms | 10 | 342 |
| synthetic open object | 123 189 | 10.5 ms | 0 | 16 782 |
| synthetic closed, `alpha`/`beta` | 3 | 5.0 ms | 3 | 301 |
| synthetic `propertyNames` with a pattern | - | `ResourceLimitError` after 68 661 ms | 1 | 33 |
| synthetic `patternProperties`, closed | 3 | 884.6 ms | 265 884 | 383 170 |
| `alpha` used once, closed | 4 | 3.8 ms | 4 | 308 |
| `alpha` used once, open | 123 189 | 18.7 ms | 1 364 | 16 782 |
| `maxProperties: 1`, at `{"` | 1 | 865.1 ms | 266 094 | 383 693 |
| `maxProperties: 1`, `{"a` | 3 | 866.3 ms | 266 080 | 383 666 |
| the same without the cap, at `{"` | 123 189 | 18.4 ms | 1 364 | 16 782 |
| the same without the cap, `{"a` | 123 178 | 18.4 ms | 1 353 | 16 755 |

The `propertyNames` case reports `ResourceLimitError` in all four legs
(`byte/0`, `byte/1`, `real/0`, `real/1`). The nine exact cases do not move
beyond run-to-run noise: the completion filter still walks the boundary
tokens of a name state, and a capacity or name constraint still makes the
free name a dead end.

Lemma and scope are in section 14 of
`mask-fast-path-needs-reachability.md` next to this directory. K1 excludes
capacity, dependencies, `patternProperties`, `propertyNames`, closed objects
and a constrained undeclared value; it requires the key machine in its
normal content state (`u.key.st == .normal`, `rem == 0`), keeps the value
lemma's enclosure preconditions over the whole stack (an armed tap or a
combinator above the object refuses K1 as well), and requires the compiled
undeclared-value node to be one the compiler marked as unconstrained
(`Grammar.isAnyNode`), because `unevaluatedProperties` or a schema-valued
`additionalProperties` can attach a constraint while the object shape stays
the same.
