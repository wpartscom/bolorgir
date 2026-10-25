# Property-name states on the prefix-skip artifact

`name-states-after.json` is the output of
`benchmarks/maskbench/t8a/diag/name_states.py --expect-fast after` on the
artifact `642ba1aea60e54766eace4120cbed41e7d788d24c326a065a9741a03247a7da4`
(built with `zig build -Drelease=true -Dstrip=true`, counters on). This is
the K1 artifact plus the ASCII content prefix skip in the exact walk; the
skip is enabled only for the string lemma, so the name path is unchanged.

All 18 cases pass. K1 fires on the nine marked cases; the other nine stay on
the exact path and the pairs are still bit for bit equal:

| case | allowed (real vocab) | mask | stateAlive | trie nodes |
|---|---:|---:|---:|---:|
| `o34343`, open, at `{"` | 123 189 | 16.9 ms | 1 364 | 16 782 |
| `o34343`, `{"options` | 123 178 | 17.2 ms | 1 353 | 16 758 |
| `o34343`, `{"x` | 123 189 | 17.0 ms | 1 364 | 16 782 |
| `o64963`, open, at `{"` | 123 189 | 17.0 ms | 1 364 | 16 782 |
| `o64963`, `{"class` | 123 178 | 16.9 ms | 1 356 | 16 762 |
| `o21292`, closed, at `{"` | 6 | 4.2 ms | 9 | 328 |
| `o21292`, closed, `{"a` | 3 | 4.1 ms | 3 | 307 |
| `o21292`, closed, `{"analytics_` | 6 | 4.0 ms | 10 | 342 |
| synthetic open object | 123 189 | 9.3 ms | 0 | 16 782 |
| synthetic closed, `alpha`/`beta` | 3 | 3.3 ms | 3 | 301 |
| synthetic `propertyNames` with a pattern | - | `ResourceLimitError` after 66 465 ms | 1 | 33 |
| synthetic `patternProperties`, closed | 3 | 857.6 ms | 265 884 | 383 170 |
| `alpha` used once, closed | 4 | 3.3 ms | 4 | 308 |
| `alpha` used once, open | 123 189 | 17.2 ms | 1 364 | 16 782 |
| `maxProperties: 1`, at `{"` | 1 | 829.8 ms | 266 094 | 383 693 |
| `maxProperties: 1`, `{"a` | 3 | 802.9 ms | 266 080 | 383 666 |
| the same without the cap, at `{"` | 123 189 | 16.9 ms | 1 364 | 16 782 |
| the same without the cap, `{"a` | 123 178 | 16.8 ms | 1 353 | 16 755 |

The `propertyNames` case reports `ResourceLimitError` in all four legs
(`byte/0`, `byte/1`, `real/0`, `real/1`). The K1 masks are unchanged from
`7eb8ed01`; the small differences are run noise. The nine exact cases keep
the exact path: capacity and name constraints still make a free name a dead
end.
