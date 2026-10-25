# Property-name states on the compact-copy artifact

`name-states-after.json` is `name_states.py --expect-fast after` on the
artifact `dc415942c5f3c673bd73fe8756f8bd0623d93878179c02c1685d3b31ebbd2330`
(source `3bb4fe2`: prefix skip plus compact live-part state copies; the skip
is enabled only for the string lemma). All 18 cases pass, K1 fires on the
nine marked cases, and every comparable pair is bit for bit equal
(`failures: []`, 17/17 masks; the `propertyNames` case keeps its expected
`ResourceLimitError`).

| case | allowed | mask ms | alive | trie nodes | K1 |
|---|---:|---:|---:|---:|---|
| `o34343-open-at-start` | 123189 | 12.6 | 1364 | 16782 | yes |
| `o34343-open-declared-options` | 123178 | 11.4 | 1353 | 16758 | yes |
| `o34343-open-free-x` | 123189 | 11.7 | 1364 | 16782 | yes |
| `o64963-open-at-start` | 123189 | 11.6 | 1364 | 16782 | yes |
| `o64963-open-declared-class` | 123178 | 11.6 | 1356 | 16762 | yes |
| `o21292-closed-at-start` | 6 | 4.1 | 9 | 328 | no |
| `o21292-closed-declared-a` | 3 | 4.2 | 3 | 307 | no |
| `o21292-closed-declared-analytics` | 6 | 4.1 | 10 | 342 | no |
| `synthetic-any-name` | 123189 | 4.3 | 0 | 16782 | yes |
| `synthetic-closed-names` | 3 | 3.4 | 3 | 301 | no |
| `synthetic-propertyNames` | - | 62971.7 | 1 | 33 | no |
| `synthetic-patternProperties` | 3 | 81.2 | 265884 | 383170 | no |
| `synthetic-reuse-closed` | 4 | 3.4 | 4 | 308 | no |
| `synthetic-reuse-open` | 123189 | 11.3 | 1364 | 16782 | yes |
| `synthetic-maxProperties-at-start` | 1 | 64.1 | 266094 | 383693 | no |
| `synthetic-maxProperties-inside` | 3 | 63.7 | 266080 | 383666 | no |
| `synthetic-no-cap-at-start` | 123189 | 11.4 | 1364 | 16782 | yes |
| `synthetic-no-cap-inside` | 123178 | 11.4 | 1353 | 16755 | yes |
