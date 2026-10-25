# Property-name states on `a87e2f71`

`name_states.py --expect-fast after`: 18 cases, `failures: []`, K1
(`name_used_real`) on 9 of them, comparable masks bit for bit equal.

The fills use the same certificate cache and the new per-builder release
rules; the equal masks, including the K1 cases that choose the value schema
from the name bytes, are the check that neither the release/retry nor the
structural-tag variant reset changes a mask.
