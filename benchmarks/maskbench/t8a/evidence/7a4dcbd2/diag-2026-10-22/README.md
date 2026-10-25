# K1 candidate `7a4dcbd2`: withdrawn after review

This package is the record of the first K1 build, not a candidate. Review
found that the AnyJSON mark was allocated after `Builder.finish` and aliased
the `pat_len` table; the VE replay then failed on `Github_hard---o74466`
with 12 field-mismatch records across 5 instances (`InvalidTokenError` in
`commit_token` at token 69, `consumed` 69 against 148), 1 147 calls instead
of 1 515 and 21 aborts instead of 19. The 43-file slice and the name-state
cases do not contain that shape and passed; the VE caught it.

What the package records is this build, on the inputs that were checked,
before the defect was found (the JSON keeps the tool's own `why` string
"12 instance mismatches"; the records are 12 field mismatches on the 5
instances, indices 0-4):

- `names/`: 18/18 name-state cases with `--expect-fast after`, 9 of them
  through K1, masks bit for bit equal; open name states 18.4-19.0 ms
  instead of 872-901 ms.
- `equiv/slice_k1.json`: the 43-file slice, 684 calls, 0 mismatches,
  mask 0.8 s, p90 5.914 ms, p99 14.331 ms.
- `equiv/ve_meter_k1_defect.json`: the failed VE replay, with the mismatch
  details; `equiv/ve_console.txt` holds its console summary and exit code 1.
- `equiv/k1_ve_lib.jsonl`: the resolved library before and after the leg
  (`7a4dcbd2...dc80`, both records agree).

The fixed artifact is
`7eb8ed0160e210e9cf6f797537c305557f984096bc2fc96d450bf1a00f0e3be0`, with its
own package under `evidence/7eb8ed01/`.
