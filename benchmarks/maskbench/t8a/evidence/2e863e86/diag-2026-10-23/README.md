# Certificate cache with a memory boundary: artifact `2e863e86`

Artifact: `2e863e860038f17d052c8ff46bf1a9f486198c2abdb6eb36634077bd31ac01b2`,
1 267 360 bytes. Built with `zig build -Drelease=true -Dstrip=true`
(counters on) from the working tree on top of `baebafb`. Source: the
per-fill certificate cache plus the release rules that make it optional
memory.

## What the artifact adds over `69a24712`

The cache stores proved top-level certifications for one fill. Its bytes
are charged to the same session budget the completion search needs, and
three separate pieces of it could starve a call that settles alive without
the cache (all reproduced by the reviewer with `Accounting` limits set to
the cache-off peak):

| optional memory | reproduction | limit |
|---|---|---|
| one stored key (173 B) | `oneOf` residual fixture, cache-on leg | 526 339 B |
| the key buffer `key_buf` (188 B), grown by `keyOf` before the first proof | mid-escape string state, first `stateAlive` | 525 336 B |
| the map table kept by `reset` (entries freed, capacity retained) | `certPut` + `reset`, then `certifyTop` | 525 336 B |

Release rules now in the artifact:

- `dropCerts` runs before the first search of a fill and frees every key,
  the map table **and** the key buffer, keeping the cache off for the rest
  of that fill;
- a certification that degraded to `undecided` on an allocation failure
  (`cert_oom`) is re-proven once with the cache released, in the main path
  and in the budget-0 branch (`certifyTop`);
- the release rule keys on held capacity (`certHoldsMemory`), so an
  empty-but-allocated table is reclaimed like a full one.

Four regression tests pin those cases: `complete: certificate cache never
takes memory from the search`, `complete: a proof degraded by cache memory
is re-proven without it`, `complete: the certificate key buffer does not
starve the first proof`, `complete: reset frees the certificate table
instead of keeping it`. Each fails when its release is removed; the
reviewer's two new fixtures fail on `baebafb`.

The test build also compiles `test_hooks` in unconditionally (`build.zig`
gives the test module its own `build_options`), and the mask-level
differential now asserts that the hook is live, that the cache path ran in
the on leg (`cert_lookups > 0`) and not at all in the off leg
(`cert_lookups == 0`), and that the masks are equal.

## Measurements

| file | what it is |
|---|---|
| `names/name-states-after.json`, `names/README.md` | `name_states.py --expect-fast after`: 18 cases, `failures: []`, K1 on 9 |
| `equiv/slice_fix.json` | the 43-file slice, `mask_equiv`: 684 calls, 0 mismatched files, 0 errors, mask 0.2 s, p50 0.006 ms, p90 **0.708 ms**, p99 4.158 ms |
| `equiv/fix_slice_lib.jsonl` | resolved library before and after the slice (both `2e863e86...01b2`) |
| `equiv/ab/cert_run{1,2,3}.json`, `equiv/ab/fix_run{1,2,3}.json` | interleaved A/B against the pre-fix cache artifact `69a24712`: mask 0.216/0.213/0.233 s vs **0.215/0.212/0.218 s**, p90 0.684/0.707/0.905 vs **0.656/0.691/0.764 ms**, p99 3.93/3.97/4.05 vs 4.01/4.03/4.10 ms - the memory rules cost nothing on the slice |
| `equiv/ve_meter_fix.json` | VE replay of the six mask-limit files: 1 515 completed calls plus the same 19 refusing calls as the ORIG leg, mask **1 234.3 s** (refusals 1 105.9 s), p50 0.010 ms, p75 1.296 ms, p90 33.513 ms, p99 56.1 s |
| `equiv/ve_meter_compare_fix.md`, `.json` | strict comparison against the ORIG leg `9a5baf3e...0c41`: all 15 checks `ok`, exit code 0; orig 1 876.1 s (1 161.9 s in refusals) -> fix 1 234.3 s (1 105.9 s), 0 mismatched files |
| `equiv/fix_ve_lib.jsonl` | resolved library before and after the VE leg (both `2e863e86...01b2`) |
| `equiv/ve_console.txt` | console summary of the VE leg and its exit code |
| `equiv/warm_repeats/run{1,2,3}.json` | `bench_adr0007.py --cold-repeats 3 --skip-maskbench` on the pinned library, every child reporting `loaded.sha256` = `2e863e86...01b2` (12 records, all verified). C-extension hit p50 1.159-1.266 us, mean 1.178-1.296 us; cache-off legs p50 1.113-1.302 us, mean 1.139-1.328 us - the same envelope, so no warm regression is visible, and the artifact's aggregation rule for the hard guard is still open by the owner's decision |
| `profile/slice_calls_run{1,2,3}.json`, `profile/README.md` | per-call profile with the fill/commit split: mask 0.216/0.262/0.212 s, fill p90 0.693/0.865/0.761 ms, 0 errors |

`zig build test`: 325/325.

Design and the full argument: section 19 of
`mask-fast-path-needs-reachability.md` next to this directory.

## Scope

The memory reproductions are unit tests of the scratch available to
completion (grammar and tokenizer allocations sit outside the limited
account); they are not public-ABI limit measurements. The VE replay covers
the six mask-limit files of the ADR-0007 protocol and passes strictly, but
it proves those inputs, not every allocation path of the session.
