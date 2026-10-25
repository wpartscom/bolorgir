# Property-name states on `2e863e86`

`name_states.py --expect-fast after`: 18 cases, `failures: []`, K1
(`name_used_real`) on 9 of them, comparable masks bit for bit equal.

The certificate cache sits behind the completion filter, so it can change
the masks only by returning a wrong verdict; the equal masks on all 18
cases, including the K1 ones that decide the value schema from the name
bytes, are the check that neither the cache nor its release rules (which
run inside the same filter) do.
