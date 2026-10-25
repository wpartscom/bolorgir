# Property-name states on `2d982b60`

`name_states.py --expect-fast after`: 18 cases, `failures: []`, K1
(`name_used_real`) on 9 of them, comparable masks bit for bit equal.

The certificate cache and its release rules live behind the completion
filter, and the new walk-level retry runs inside the same mask call; the
equal masks on all 18 cases, including the K1 ones that decide the value
schema from the name bytes, are the check that none of them changes a mask.
