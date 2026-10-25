# Certificate cache: release on the mask walk and the parser feeds: artifact `2d982b60`

Artifact: `2d982b60cec55f3328206570659c29307e2050e3453b97b956649aa48e45b94e`,
1 267 776 bytes. Built with `zig build -Drelease=true -Dstrip=true`
(counters on). Source: the working tree on top of `6f3c125`.

## What this artifact adds

The previous artifact released the optional certificate cache inside the
completion filter only. A whole-`fillMask` reproduction showed that is not
enough: an open object whose undeclared values are `^a*$` strings, prefix
`{"`, and a 1 024-byte name token failed with `OutOfMemory` inside
`parser.openKeyAppend` at a 1 317 199-byte limit (the cache-off leg's own
peak) - the walk's required allocation came before the filter's next
certification, so neither release rule could fire.

Now:

- `fillMask` wraps the walk: on `OutOfMemory` with any optional memory
  held it releases the cache, restores the work counter and the per-fill
  search budget, and retries the walk once. The walk rebuilds `out` from
  the initial state, so the retry is the cache-off run;
- the accept and draft-validation paths release the cache before feeding
  the parser (`acceptTokenInner`, `blg_validate_tokens`, structural-tag
  accept and validation), so optional bytes never sit in front of
  `feedBytes` there either;
- the cache stays off for the rest of the fill after a release.

The regression `fast path: the certificate cache cannot fail a fill's
walk` runs the reviewer's fixture at the cache-off peak and requires a
full, equal mask with an empty cache afterwards; without the retry it
fails with the reviewer's `OutOfMemory`. `zig build test`: 326/326.

## Measurements

| file | what it is |
|---|---|
| `names/name-states-after.json`, `names/README.md` | `name_states.py --expect-fast after`: 18 cases, `failures: []`, K1 on 9 |
| `equiv/slice_fix.json` | the 43-file slice, `mask_equiv`: 684 calls, 0 mismatched files, 0 errors, mask 0.2 s, p50 0.006 ms, p90 **0.725 ms**, p99 3.984 ms |
| `equiv/fix_slice_lib.jsonl` | resolved library before and after the slice (both `2d982b60...b94e`) |
| `equiv/ve_meter_fix.json` | VE replay of the six mask-limit files: 1 515 completed calls plus the same 19 refusing calls as ORIG, mask **1 229.6 s** (refusals 1 099.6 s), p50 0.009 ms, p75 1.278 ms, p90 38.4 ms, p99 55.5 s |
| `equiv/ve_meter_compare_fix.md`, `.json` | strict comparison against ORIG `9a5baf3e...0c41`: all 15 checks `ok`, exit 0; 1 876.1 s -> 1 229.6 s, 0 mismatched files |
| `equiv/fix_ve_lib.jsonl` | resolved library before and after the VE leg (both `2d982b60...b94e`) |
| `equiv/ve_console.txt` | console summary of the VE leg and its exit code |
| `equiv/warm_acceptance.json` | the fixed ADR-0007 warm protocol (five repeats, alternating leg order, 2 000 hits per leg): **fail** - five of ten `warm_ext.on` means are above 1 300 ns (1 302.3, 1 305.4, 1 309.8, 1 314.5, 1 327.0 ns), every p50 <= 1 283 ns, the cache-off leg in the same band (1 130-1 316 ns), all legs on `2d982b60...b94e` with 2 000/2 000 hits. Reported as measured; not re-rolled |
| `profile/slice_calls_run{1,2,3}.json`, `profile/README.md` | per-call profile with the fill/commit split: mask 0.239/0.216/0.219 s, fill p90 0.766/0.734/0.742 ms, p99 5.23/4.06/4.11 ms, 0 errors |
| `matrix-quick/` | `matrix_adr0007.py --scale quick` on this artifact: 6/6 jobs pass (326 Zig tests executed through `zig test`, 13 parametrised curated ABI cases, the three property-based suites, the benchmark gate), see `matrix-quick/matrix.md`. The Zig job runs the working tree; the Python jobs run the pinned artifact above. The first attempt failed before running any suite because the pinned venv had no `pytest`; `pytest 9.1.1` was installed into that local (git-ignored) venv and the matrix rerun. The warm-acceptance row is excluded on purpose: its fixed-protocol run above is not re-rolled |

`zig build test`: 326/326.

Design and the full argument: section 19 of
`mask-fast-path-needs-reachability.md` next to this directory; the warm
acceptance protocol is fixed in ADR-0007 ("Warm acceptance protocol").

## Scope

The memory reproductions are unit tests of the scratch available to
completion (grammar and tokenizer allocations sit outside the limited
account), not public-ABI limit measurements. The VE replay covers the six
mask-limit files of the ADR-0007 protocol and passes strictly; it proves
those inputs, not every allocation path. The quick matrix bounds the
property-based campaigns; the full-scale matrix (600 differential
schemas) is prepared via `--scale full` and still has to be run.
