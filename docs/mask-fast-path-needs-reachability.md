# Mask fast path under `needs_reachability` (ADR-0007 Decision 1 relaxation)

Status: candidate, not released. Core change in `src/mask.zig`, diagnostics in
`src/diag.zig`, tests in `src/mask.zig` and `src/c_api.zig`.

Current status (2026-10-26): the sections below are stage records with their
own dates and artifacts. The Decision 4 evidence of the `a87e2f71` artifact
(2026-10-24) covers the full functional matrix and the stored warm acceptance
(`benchmarks/maskbench/t8a/evidence/a87e2f71/full-functional-c770469-2026-10-24/CONCLUSION.md`);
the CI confirmation of that suite and the formal closure decision are still
open, and the current tree has no Decision 4 evidence of its own.

## 1. What the measurement showed

The T8a run's mask cost is not schema-shaped: it is the exact trie walk. With
the counter build (`src/diag.zig`, read via `blg_diag_counters`) on the three
reproducers, one file at a time, spec-v1 profile, one thread:

| file | masks | fast-path refusals | trie nodes | completion checks | dead | mask time |
|---|---:|---|---:|---:|---:|---:|
| `BFCL_java_0` | 16 | 10 non-uniform + 6 `needs_reachability` | 2 305 597 | 1 596 682 | 0 | up to 877 ms/step |
| `Github_medium---o30212` | 6 | 3 non-uniform + 3 `needs_reachability` | 1 918 876 | 1 330 585 | 0 | 3.3–3.9 s/step |
| `Github_easy---o90249` | 9 | 9 non-uniform (pattern strings) | 1 151 503 | 797 699 | 797 621 | 0.96 s/step |

Two conclusions that changed the earlier diagnosis:

* the completion filter is **not** the cost. Ablating it (`BLG_MASK_NO_FILTER=1`,
  diagnostic only) on `BFCL_java_0` removed 1.6M `stateAlive` calls and changed
  the time from 861–877 ms to 839–870 ms per step, about 2.5 %;
* on `BFCL_java_0` the six slow steps are **exactly** the six states refused
  only by `g.needs_reachability`. Those states are uniform string-content
  states: the lemma applies, but the fast path was refused wholesale.

The user-side experiment (properties typed `{}` vs `type: string`, same
document) is the same effect from the other side: `type: string` removes the
deferred value verdict, `needs_reachability` clears, and the existing fast path
(which copies precomputed `content_bits`) takes over at ~0.03 ms.

## 2. The change

`fillMaskFast` no longer refuses on `g.needs_reachability`. Instead:

1. the enclosure is checked first (`contentDependentEnclosure`): a live thread
   with an armed byte tap (`tap_count != 0`) or a `.comb` frame below its top
   `.str` frame keeps the exact path (section 8);
2. the state itself is checked once per mask with `complete.stateAlive`; a dead
   state returns `false`, and the caller runs the exact walk, which returns
   `DeadEnd` - the previous outcome, unchanged;
3. the content class is still decided by the lemma (precomputed `content_bits`
   or the word loop, plus the widened residual cache);
4. the exact class keeps the completion filter: `fillMaskExactPruned` now takes
   `filtered` and `setExactTokens` calls `stateAlive` once per node whose chain
   actually carries an exact-class id (closing quote, escapes, truncations).

Soundness argument: in a uniform string-content state every live thread has a
`.str` top frame in `.normal` state with `rem == 0`, so feeding a pure content
token of codepoints `cp(t) <= R` leaves the string closable. Whether the
*enclosing* frames stay untouched depends on the enclosure, and that is the
premise the review of 2026-10-22 falsified for two shapes:

* a combinator frame votes on the completed value (`oneOf` = exactly one
  branch, `allOf` = every branch, `if/then/else`), so a content token must
  keep every bound branch inside its own budget; `R` is the maximum over the
  branch threads - a union bound - and the intersection bound is not the
  maximum;
* an armed byte tap re-parses or compares the captured bytes at the element
  or object close (`uniqueItems`, `contains`, schema-kind dependencies), so
  the content itself decides the parent verdict.

Those states are refused before the lemma is applied. For the remaining
enclosures (object, sequence, repeat without a capture, plain `.choice`
unions) the content is ignored, aliveness is preserved for the content class
and cannot change between the state and its successor; only the exact class
can leave the string and meet a deferred verdict, so only it is filtered. `R`
already carries the `maxLength` budget, and `tok.byte_complete` guarantees the
`minLength` gate stays reachable.

## 3. Measured effect (same machine, one thread, other repeats running)

| file | before | after |
|---|---:|---:|
| `BFCL_java_0` | 877 ms slowest step, 5.18 s of mask over 16 calls | 15.4 ms slowest step, 0.096 s (54×) |
| `Github_medium---o30212` | 3.43 s median step | 27.5 ms median step; two property-name steps unchanged at 3.6 s |
| `Github_easy---o90249` | 0.96 s per slow step | unchanged - pattern strings are not uniform states |

On `BFCL_java_0` the widened content cache now hits (5 of 6 fast masks), and
`stateAlive` calls drop from 1 596 682 to 8 320.

`Github_hard---o10421` (a 900 s timeout) was reported here as moving from
3.7 s per step to 31.8 ms with 3 of 10 masks through the fast path. That
measurement is withdrawn: the file's strings sit under `contains` and `oneOf`,
so the acceleration was out of scope and the masks were not exact. After the
correction of section 8 the file is back to 3.7 s per step (probe median
3.716 s over 60 calls) with 0 of 60 masks through the fast path and 34
content-dependence refusals.

### Equivalence on a fixed slice

43 files, 684 calls (recorded mask time 32.9 s), compared per instance against
the run's streams (`consumed` / `rejected_at` / `n_ids`). Only 18 of the 43
files carry instances (51 documents in total); the other 25 have zero test
instances, so the outcome comparison covers 51 documents, not 43 files' worth:

| core | mask time | p90 | p99 | mismatched files | calls ≥ 1 s | time in ≥ 1 s calls |
|---|---:|---:|---:|---:|---:|---:|
| original wheel | 25.7 s | 5.9 ms | 852 ms | 0 | 0.15 % | 4.16 % |
| candidate before the scope correction | 13.6 s | 6.0 ms | 853 ms | 0 | 0 % | 0 % |
| candidate after the scope correction | 15.9 s | 6.0 ms | 842 ms | 0 | 0 % | 0 % |

What this gate proves is narrower than "correctness confirmed": the **outcomes
of the checked documents matched** the run. It compares consumed / rejected /
instance-id counts, not mask bits, so an extra allowed token stays invisible
unless a recorded document happens to take it. The review of 2026-10-22 found
two such bits precisely that way. Mask-bit equality is now covered by the ABI
test `fast path scope` (section 5), and this table says only that the
accelerated states did not change any recorded outcome on the slice.

Zero mismatches on the original core also validates the harness itself: it
reproduces the recorded streams exactly before claiming anything about the
candidate.

The scope correction costs 2.5 s on this slice, all of it in one file:
`Github_trivial---o41679` (18 calls) goes from 50 ms to 2.5 s because its
strings are values of a `oneOf` of two closed objects. The two branches are
already separated by the key, so the vote cannot actually depend on the string
content there; proving that shape is the next optimization (section 9), not a
reason to keep an unproven acceleration.

### Resource refusals

Six files that hit `mask computation limit exceeded` in the run, replayed with
the corrected core: **0 mismatched files** - the refusal lands at the same
token (`consumed` matches the recorded stream, `rejected_at` stays null). Three
of them refuse before the first token. So the correction does not turn a
resource refusal into a different outcome, but it does make the work before the
refusal more expensive: over the same 1515 calls the mask time rises from
293.4 s to 497.7 s. The old candidate accelerated content-dependent string
states (out of scope, wrong masks); the corrected core walks them exactly. The
increase is concentrated in `o74466` (35.0 -> 146.6 s), `codeship-services`
(19.3 -> 68.4 s) and `o54553` (164.3 -> 211.7 s); `o12278` and `o90904` are
slightly faster. The two compile-limit files in the same list still fail to
compile (`grammar exceeds node budget of 100000`), as in the run.

### Timeouts

Twenty sampled timeouts replayed at four concurrent processes: 17 still hit
the 900 s limit, 3 finish (401, 654 and 303 ms per step). The cost is in the
masks - 82.9 % to 100 % of the wall time of the files that time out again - and
the families are listed below. That settles the question of what the timeouts
are: not an artifact of 12-way contention, but the mask cost of specific
states.

### Where the timeouts sit

| file | refusals | family |
|---|---|---|
| `o10421` | 0 of 60 fast (34 content-dependence refusals) | strings under `contains`/`oneOf` - needs its own argument |
| `o34343` | 25 of 25 non-uniform | property-name positions in an open object |
| `o64963` | 10 of 10 non-uniform | property names; 783 917 dead terminals |
| `o21292` | 10 of 10 non-uniform | wide states, 162 987 dead |
| `o90249` | 9 of 9 non-uniform | `pattern` strings |

The dominant remaining family is **member-name positions**, not patterns: the
next narrow class should cover it, and the pattern family needs its own
argument because its completion filter is load-bearing.

## 4. What is still slow, and why it is a different fix

* `o30212` type: property-name positions in an object with an open
  `additionalProperties`. Not a uniform string state, so the lemma does not
  apply; the walk feeds every candidate name through the parser.
* `o90249` type: strings under `pattern`. The `.str` frame is not `.normal`, the
  DFA state is live past the last completable prefix, and the completion filter
  kills 797 621 of 797 699 terminals. Removing the filter here would be
  unsound; this family needs its own argument (or a different mask
  construction) and must be profiled separately.

## 5. Tests

* `src/mask.zig`: existing fast-path suite runs unchanged (uniform states,
  escapes, mid-codepoint, parents, widened cache, worker count, three-way
  differential on random string grammars).
* `src/c_api.zig`: `abi: fast path on/off bit-for-bit under
  needs_reachability` - a schema with a plain string property and a bounded
  number sibling (`minimum`/`maximum`) compiles with `needs_reachability`, and
  the masks with `mask_fast_path = 1` and `mask_fast_path = 2` are identical
  bit for bit at `{"a":"` and `{"a":"ab`; the test also asserts that the fast
  path actually ran (`blg_diag_counters` → `fast_used > 0`).
* `src/c_api.zig`: `abi: fast path scope - content-dependent enclosures and
  boundary tokens` - fifteen schema/prefix probes over the reviewer's
  vocabulary (all 256 byte tokens plus `ab`, `aa`, `bb`, `é`, `aé` and tokens
  that cross the string/separator boundary), in `lazy` and `adaptive`:
  * the two reproducers of section 8, uniqueItems nested in uniqueItems,
    uniqueItems over objects, and the `oneOf` vote: masks identical bit for
    bit, `fast_used == 0`, `refuse_content_dep > 0`, and the ground-truth
    forbidden ids (the duplicate `a`, `ab`/`aa`/`bb` under the allOf bound,
    the closing quote of the empty `oneOf` value) absent from both masks;
  * a `oneOf` with exactly one live branch is accelerated: the dog/cat
    objects case runs the fast path, the closing quote, content and a
    closing token are allowed, while tokens that close the value and add a
    comma are refused (`a",` / `","` / `aa",`); the same schema with both
    branches live, or with an inner `oneOf` or `allOf` below the string, is
    refused (`fast_used == 0`);
  * length boundaries (`minLength` 2: quote refused at 0, allowed at 2;
    `maxLength` 3: content refused at R = 0), Unicode (`é` is one codepoint,
    `ab` is two) and tokens crossing the separator (`a",` / `","` / `ab"`):
    masks identical, `fast_used > 0`.
* `src/c_api.zig`: `abi: name-state lemma K1 - scope, boundaries and
  counters` - eighteen name-state probes over the name vocabulary (all 256
  bytes plus `ab`, `aa`, `bb`, `é`, `a":`, `":`, `x":`, `a"`, `ab"`), in
  `lazy` and `adaptive`: masks identical bit for bit; the in-scope states
  run K1 (`name_used > 0`), the boundary states keep the exact path with
  `name_used == 0` - capacity (`maxProperties: 1`), closed object,
  `propertyNames`, `patternProperties`, schema-valued
  `additionalProperties`, `unevaluatedProperties`, a `dependentRequired`
  entry, a `dependentSchemas` capture, a parent element capture
  (`uniqueItems`), a two-live-branch `oneOf`, inside-escape and inside
  2-byte/4-byte UTF-8 states, with `refuse_name_state` or
  `refuse_content_dep` non-zero.
* `src/schema.zig`: `spec-v1: any_nodes is allocated before finish and does
  not alias pat_len` - compiles `{"type":"object"}` at depths 1, 4 and 64
  and requires every `pat_len` entry to stay null, the AnyJSON mark to be
  non-empty and the two arena tables to be disjoint (regression for the
  defect described below).
* `src/mask.zig`: `fast path: ASCII prefix advances each thread and drops
  it at maxLength` - two alternative strings (`maxLength: 0` and
  `minLength: 2`, `maxLength: 5`): `a"` must be refused because the prefix
  kills the `maxLength: 0` thread and the other reaches the quote with
  count 1 and fails `minLength: 2`; `"` and one content code point stay
  allowed, and the fast mask equals the trie walk.
* `src/c_api.zig`: the scope table gains `anyof-prefix-maxlength` on the
  reviewer vocabulary: `anyOf` of `maxLength: 0` and `minLength: 3` /
  `maxLength: 5`, prefix `"`; `ab"` (id 263) must stay absent because the
  prefix must drop the `maxLength: 0` thread, while `"` and `a` stay
  allowed; masks equal, `fast_used > 0`.
* `zig build test`: 319/319 pass.

## 6. Candidate artifact and how to re-verify

```
zig build -Drelease=true -Dstrip=true
zig-out/lib/libbolorgir.so.0.1.0
sha256 a4b10658eb393e347bc08762db5d0247e608be21f0a1aee078887b5d336e60aa
```

This is the corrected candidate (section 8). The earlier core
`17294af1…488c` is superseded: it allowed the two masks the reviewer
reproduced.

Local gate before a new wheel (does not touch the frozen run):

```
diag/mask_probe.py FILE --diag            # counters per reproducer
diag/mask_equiv.py --files LIST --out J   # streams vs out/blg-spec-v1/t8a_streams
```

`mask_equiv` compares `consumed` / `rejected_at` / `n_ids` per instance with the
recorded streams and prints TBM percentiles for the ADR-0007 targets (cold
permissive state ≤ 50 ms, TBM p90 ≤ 1 ms on the fixed slice).

## 7. Failure records in the T8a runner

The abort-phase fix is applied to the working
`benchmarks/maskbench/t8a/paired_runner.py` (sha256
`09cc2c7e6679a258c5da2409aaccd43a1a77216d6187457900d2f4e5566c168d`); the
separate `.patch` file is gone. `_install_recorders` wraps `reset`,
`compute_mask` and `commit_token`; a raising call records
`aborted: {phase, instance, at, error}` on the instance record and on
`engine.t8a_abort`. `process_file` catches an exception escaping the file
processing and rewrites the pending marker with the same `aborted` object,
so a crash names its cause while the file keeps its `crash_or_timeout`
class.

Verified:

* `ui5.yaml.json` (the parser-state crash) replayed through `diag_runner`
  with the run's wheel: exit 1 and the marker
  `{"pending_file": 1, "aborted": {"phase": "reset", "at": 0,
  "error": "ResourceLimitError", "instance": 0}}`;
* the tracked test
  `test_abort_marker_names_the_phase_and_keeps_the_pending_status` raises in
  each of the three phases and asserts the same marker shape and the
  instance-level copy; a compute_mask or commit_token abort therefore
  reaches the stream entry, where `wrong_instances_enum.py` classifies it
  as `aborted` instead of `corrupt`.

Consequence for the old work dir: `diag.py` and `diag_repeat.py` compare the
current tool hashes with the run's manifest and now refuse; the old tool is
in git history (`25084c73…`). New runs record the new hash through
`t8a_identity.py`.

## 8. Correction after the reviewer's reproduction (2026-10-22)

The reviewer checked the core `17294af1…488c` with the fast path on and off on
a byte-complete vocabulary (all 256 byte tokens plus `ab`, `aa`, `bb`) and
found two masks that differ:

| case | state | exact path | fast path (old core) |
|---|---|---|---|
| array of two unique strings, `maxLength: 1` | prefix `["a","`, token `a` | refuses | admits |
| `allOf` of `maxLength: 1` and `maxLength: 2` | after the opening quote, token `ab` | refuses | admits |

Both reproduced in `lazy` and `adaptive` with the default work budget. The
admits are wrong: they allow a continuation from which no valid document can
be completed.

Cause: the lemma was applied on a state condition alone (`uniformStringResidual`
non-null). Its proof also needs the enclosure to ignore the string's content,
and that fails in two ways:

* **uniqueItems** (and `contains`, and schema-kind dependencies): the parent
  re-parses or compares the captured element/object bytes at the close, so the
  content of the string being typed decides the parent verdict. In
  `["a","` the next `a` closes a duplicate element;
* **allOf** (and `oneOf`, `if/then/else`): the branch threads are a
  conjunction, not a union. `R` is the maximum residual over the threads;
  `ab` fits the `maxLength: 2` branch but not the `maxLength: 1` branch, and
  the longer branch alone is not enough for the group to accept.

`tap_count` alone would catch the first shape (the element/object capture is
armed) but not the second (no tap; the comb frame is the signal), so the check
is `contentDependentEnclosure`: refuse when any live thread has `tap_count != 0`
or a `.comb` frame below its top `.str` frame. The counter `refuse_needs_reach`
is retired; its slot now reports `refuse_content_dep`.

Verification after the fix:

* the reviewer's reproducer, unmodified except for the expected core hash, on
  the new core: 4/4 cases `equal`, `fast_used == 0` in every case
  (`result_after_fix.json` in the review directory);
* the ABI test of section 5 reproduces both cases plus nested, boundary,
  Unicode and separator-crossing tokens and asserts the exact-path ground
  truth;
* the 43-file slice still matches the recorded streams (0 mismatched files,
  15.9 s of mask, p90 6.0 ms, p99 842 ms).

## 9. Diagnostics separated from the release build

The reviewer asked to close two doors before a new wheel:

* `BLG_MASK_NO_FILTER=1` is removed (`src/diag.zig`, `src/mask.zig`): a
  semantics-changing ablation no longer exists, so a diagnostic build cannot
  produce a mask the release build would not;
* the counters are process-global and were incremented with a plain `+=` from
  several threads - a data race. They are `std.atomic.Value(u64)` with relaxed
  RMW/loads now (`diag.incr`), and `reset` stores atomically. The cost is one
  locked add per event in every build that carries the counters (they stay
  exported by default so the reviewer and the diagnostics can read the path
  taken on the same artifact). `-Ddiag_counters=false` compiles the
  increments out; section 11 reports the measured difference. The correctness
  requirement is that diagnostics cannot change masks, and that is enforced
  by removing the last semantics-changing knob.

## 10. Next: optimization after correctness

The scope correction costs 2.5 s on the slice, concentrated in
`Github_trivial---o41679` (`oneOf` of two closed objects; 18 calls, 50 ms →
2.5 s). That shape is provable: the branches are separated by the object key
before the string starts, so the vote cannot depend on the string content.
The general refinement is to refuse only when two or more branches of the same
comb instance are still live at the string state (and no branch can accept a
different set of contents), instead of refusing every comb ancestor.

The dominant families from section 4 (`o34343`/`o64963`/`o21292` property-name
positions, `o90249` pattern strings) are untouched by this correction and
still need their own arguments. The measured targets remain ADR-0007: cold
permissive state ≤ 50 ms, TBM p90 ≤ 1 ms on the fixed slice -- currently
p90 6.0 ms, p99 842 ms.

## 11. oneOf with one live branch (after the checkpoint)

The provable refinement of section 10 is implemented in
`contentDependentEnclosure`. The scope check accepts a `oneOf` combinator
frame when exactly one branch of that activation has live threads: a branch
whose threads all died can never accept the value (threads never revive), so
the group verdict is that branch's acceptance, and the content can only
decide the branch's own string language, which the lemma covers. The branch
set is counted over distinct `(inst, branch)` pairs across all live threads,
not over threads: alternatives inside one branch and nested activations are
not separate branches. `allOf`, `if/then/else`, a `oneOf` with another live
branch and any armed tap still keep the exact path.

Measured on `Github_trivial---o41679` (the 2.5 s regression of section 3):

| metric | before | after |
|---|---:|---:|
| masks through the fast path | 0 of 18 | 3 of 18 |
| slowest step | 910.9 ms | 15.6 ms |
| trie nodes (probe) | 1 152 336 | 51 603 |
| completion checks (probe) | 798 244 | 4 063 |
| mask per call | 150.1 ms | 3.0 ms |

The ABI table of section 5 covers the shape: the dog/cat `oneOf` is
accelerated and its boundary tokens are refused correctly; the same schema
with both branches live, an inner `oneOf` with two live branches, and an
`allOf` branch stay on the exact path. The 43-file slice and the reviewer
reproducer are re-run on the new artifact before it is checkpointed.

The heavy families are unchanged: property-name positions and `pattern`
strings still dominate the timeouts; this section recovers the cost of the
scope correction, it does not touch them.

## 12. Counter overhead: how it is measured

`zig build -Ddiag_counters=<bool>` builds the same source twice; the two
artifacts are pinned by checksum in `benchmarks/local/t8a-diag-9a5baf3e/`
(`count-on/`, `count-off/`), and the diagnostic packages point at those copies
rather than at a build directory that the next build can overwrite.

`measure_counters.sh` runs one corpus file per process with `mask_probe.py`
and repeats the on/off pair three times per file, alternating the order inside
the pair (on-off, off-on, on-off), so a drift of the machine cannot favour one
variant. Every run keeps its full log and its exit code; parsing happens
afterwards in `counter_aggregate.py`, so a traceback or a non-zero exit is
visible instead of being filtered away. The reported numbers are the median of
the three per-call values, their min-max spread, and the on/off ratio.

The same rule applies to the VE comparison of section 13: the FIX leg is
pinned to the saved checkpoint library, the hash of the resolved file is
recorded before and after each leg, and the comparator refuses to print a
timing table unless both legs cover the same files, instances and calls,
refuse the same number of times, run no capped file, report no error outside
the two expected compile-limit cases, and carry different library hashes.

Results of 2026-10-22, three repeats per file, order inside the pair
alternating; `ms per call` is the engine's `mask_ns_total / mask_calls`:

| file | on median | off median | on spread | off spread | paired on-off |
|---|---:|---:|---:|---:|---|
| `BFCL_java_0` (16 steps) | 5.755 ms | 5.615 ms | 5.0 % | 1.0 % | +0.41, +0.14, +0.17 ms |
| `Github_medium---o30212` (60 steps) | 1 785.9 ms | 1 759.4 ms | 3.7 % | 2.3 % | -39.2, +36.7, +56.6 ms |
| `Github_trivial---o41679` (30 steps) | 2.874 ms | 2.847 ms | 0.3 % | 5.0 % | +0.02, -0.02, +0.11 ms |

On the cheap family all three pairs are positive: +0.14 to +0.41 ms, 2-7 %
of the call. On the heavy 1.8 s family and on the 2.9 ms family the paired
difference changes sign and stays inside the run-to-run spread of the same
variant (about +-40 ms and +-0.1 ms), so no separate cost is claimed there.
The release artifact is byte-identical to the `on` variant
(`4b567de3...ebbd3`), so this is what the shipped default carries;
`-Ddiag_counters=false` is `2e903216...a9f6`, 1 120 bytes smaller.

## 13. VE comparison under the corrected meter, and the oneOf candidate

Two boundaries have to be stated before the numbers. The ORIG leg is the
run's wheel `9a5baf3e...0c41`; that library was in use while the leg ran, so
its hash was recorded *during* the leg and again after it, not before the
start - both records agree. The FIX leg is `a4b1`
(`a4b10658...60aa`), the previous checkpoint, loaded from the saved copy
rather than from `zig-out`, with its hash recorded before and after the leg.
The candidate that carries the `oneOf` rule is a different artifact
(`4b567de3...ebbd3`); it is not part of the VE table and is reported on its
own below.

The six files that hit `mask computation limit exceeded` in the run, the
same list on both sides: 1 515 calls, 19 refusals, refusal counts equal per
file, no capped file, no stream mismatch, and the same two compile-limit
files (`o58839`, `o90895` exceed the 100 000 node budget in both legs). All
check rows of `equiv/ve_meter_compare.md` are `ok`.

| metric | ORIG `9a5baf3e` | FIX `a4b1` | delta |
|---|---:|---:|---:|
| mask time | 1 876.1 s | 1 621.3 s | -254.8 s (-13.6 %) |
| of it refusals | 1 161.9 s in 19 | 1 119.7 s in 19 | -42.2 s |
| of it calls without a refusal | 714.2 s | 501.6 s | -212.6 s (-29.8 %) |
| share of mask time in refusals | 61.9 % | 69.1 % | +7.2 pp |
| wall | 1 876.4 s | 1 621.6 s | -254.8 s |
| p50 | 0.007 ms | 0.008 ms | |
| p75 | 830.8 ms | 13.1 ms | -817.7 ms |
| p90 | 946.7 ms | 836.1 ms | -110.5 ms |
| p99 | 57 398 ms | 56 578 ms | -820 ms |
| calls >= 1 s | 9.3 % | 7.2 % | |
| share of time in them | 86.5 % | 91.3 % | |

The largest per-file gains are `o90904` (-81.3 s), `o12278` (-71.2 s),
`o74466` (-57.0 s) and `o54553` (-30.2 s); `o13129` and `codeship` move by
-9.1 s and -6.0 s. The refusal cost itself is nearly unchanged; the gain is
in the calls that do not refuse, 714.2 s -> 501.6 s (-29.8 %).

The 19 refusals are 1.24 % of the 1 534 measured calls (1 515 completed plus
19 refusals), but they are 69.1 % of the mask time in the FIX leg against
61.9 % in ORIG: the fast path made the rest cheaper, so the refusal share had
to grow. They set p99 (about 57 s) and the extreme tail. They do not explain
p90: at 836 ms, 10 % of the calls - about 153 - are at least that slow,
while only 19 raise. The p90 band is therefore still made of slow calls that
do not raise, and the refusal count is unchanged (19 in both legs), so this
speed-up is not an availability gain: the refusals still cost the same and
still end the document at the same token.

### The oneOf candidate `4b567de3...ebbd3`

- build `zig build -Drelease=true -Dstrip=true`, 1 521 440 bytes, sha256
  `4b567de3b3d42ae5434c22a486ed3eb283edbc1a24965a944f073e900d5ebbd3`; a
  second build into a separate prefix produced the same bytes, and the result
  is byte-identical to the counter `on` variant of section 12;
- `zig build test`: 316/316, `All 316 tests passed`, including the ABI table
  with the `oneOf` cases (accelerated single-branch shape, boundary tokens
  refused, two-live-branch and `allOf` shapes kept on the exact path);
- the reviewer's reproducer on this artifact: 4/4 masks bit for bit equal,
  `fast_used == 0` in every case (`equiv/repro_oneof.json`);
- the 43-file slice: 0 mismatched files, 684 calls, mask 13.5 s against
  15.9 s on `a4b1`, p50 0.008 ms, p90 6.07 ms, p99 841.8 ms, no call >= 1 s
  (`equiv/slice_oneof.json`). The 2.4 s recovered is `o41679`; the tail is
  the property-name and `pattern` families and does not move;
- ADR-0007 is still not met: p90 6.07 ms against the 1 ms target. The next
  target is the property-name family, which needs its own lemma.

## 14. Property-name states: what the masks are and what they cost

A key is not a `.str` frame. In an open object the name is read by the
`.open_obj` frame itself, in phase `.key_str` (`openKeyFeed`,
`src/parser.zig`): the frame holds the key machine state `u.key` (`st`,
`rem`, `lo`, `hi`), the raw key bytes and the dispatch that follows. A closed
object is a different node (`.object`) with its declared keys in schema
order. `refuse_not_uniform` therefore means "the top frame is not a `.str`",
not a mix of threads; the lemma has to be stated over `.open_obj` fields.

Measured on `4b567de3...ebbd3` with
`benchmarks/maskbench/t8a/diag/name_states.py`. A case is a schema, a prefix
and its expectations; the prefix must be accepted in every leg, an expected
`ResourceLimitError` must actually occur, an unexpected missing mask fails
the case, and both vocabularies compare the full masks with the fast path
off and on. All 18 cases pass with `--expect-fast before`, that is with the
exact path everywhere.

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

What the numbers say:

* an open name state costs a full-vocabulary walk with one `stateAlive` per
  terminal: about 266 000 aliveness checks and 384 000 trie nodes for one
  mask, 0.83-0.90 s, while the mask allows almost the whole vocabulary
  (123 189 of 128 256 tokens). The cost does not buy a selective mask;
* the cost is not explained by the size of the allowed set: with
  `maxProperties: 1` a single token is allowed and the mask still costs
  867 ms; `patternProperties` with 3 allowed tokens costs 878 ms;
* a closed object is cheap at a name state (4-5 ms): the language there is
  the next declared key, in declaration order (`semantics-spec-v1.md` 4.3);
* a free name is not always free. With `maxProperties: 1` and a required
  key `a`, the exact mask forbids `x` at `{"` and forbids `x` after `{"a`
  (the name would become `ax`): capacity is a name constraint;
* `propertyNames` with a pattern does not fit the budget at all.

Scope of lemma K1, as implemented in `uniformNameState` (`src/mask.zig`): an
open name state where every live thread's top frame is `.open_obj` in phase
`.key_str`, and that frame has `max_props == UNBOUNDED`, `deps.len == 0`,
`pattern_lit == null`, `pattern_dfa == null`, `prop_names == null`,
`names_forbidden == false`, `capture == false`, no key length bounds
(`key_min_len == 0`, `key_max_len == UNBOUNDED`), and the key machine is in
its normal content state (`u.key.st == .normal`, `rem == 0`), so the content
class is the existing strict one. The preconditions of the value lemma stay
in force over the whole stack, not only on the object frame: the enclosure
check rejects an armed tap or a combinator frame *above* the `.open_obj`
frame, because the object's own `capture` field says nothing about a parent
capture.

The undeclared value is checked on the compiled node, and the check is a
compiler fact, not a shape: `compileAnyJSON` marks every unconstrained value
node it builds in `Grammar.any_nodes`, and K1 requires
`isAnyNode(on.value)`. A schema without `additionalProperties` can still
attach a value constraint - a guard merged for `unevaluatedProperties`, or a
schema-valued `additionalProperties` - and those nodes are not marked, so
the state stays on the exact path even though the object looks the same.

Under these conditions a content token (no closing quote, no backslash, valid
UTF-8 continuation) is always allowed: `openKeyFeed` appends it to the free
name, and the name stays closable, because it can be extended past every
declared and seen name and dispatched to the unconstrained value, while the
remaining required names and the close do not depend on the prefix (an
undeclared key blocks neither a declared one nor a `required` set,
`semantics-spec-v1.md` 4.3). Capacity, dependencies, `patternProperties`,
`propertyNames`, closed objects and a constrained additional value keep the
exact path until each has its own argument. Tokens that contain the closing
quote, including those that also carry the colon and the escape/UTF-8
boundary states, stay in the exact class and are decided per token as today.

The two new counters are appended after `exact_tokens` so the positional
layout of every recorded log stays valid: `name_used` counts masks built by
K1, `refuse_name_state` counts states that failed the string lemma and did
not satisfy K1.

Measured on `7eb8ed01...3be0` with `name_states.py --expect-fast after`: all
18 cases pass, K1 fires on the nine marked cases and the other nine stay
exact; every pair is bit for bit equal in both vocabularies. The marked
cases:

| case | before | after | stateAlive | trie nodes |
|---|---:|---:|---:|---:|
| `o34343`, open, at `{"` | 881.0 ms | 19.3 ms | 1 364 | 16 782 |
| `o34343`, `{"options` | 896.7 ms | 18.4 ms | 1 353 | 16 758 |
| `o34343`, `{"x` | 872.0 ms | 18.6 ms | 1 364 | 16 782 |
| `o64963`, open, at `{"` | 901.5 ms | 20.2 ms | 1 364 | 16 782 |
| `o64963`, `{"class` | 888.9 ms | 18.6 ms | 1 356 | 16 762 |
| synthetic open object | 834.3 ms | 10.9 ms | 0 | 16 782 |
| `alpha` used once, open | 882.7 ms | 19.1 ms | 1 364 | 16 782 |
| the same without the cap, at `{"` | 878.3 ms | 18.8 ms | 1 364 | 16 782 |
| the same without the cap, `{"a` | 889.2 ms | 18.9 ms | 1 353 | 16 755 |

The remaining 18 ms is the boundary class: every token that carries the
closing quote, a backslash or an incomplete UTF-8 sequence is still walked
exactly, and the completion filter still certifies it. The nine refused
cases do not move beyond run-to-run noise (closed object 4-5 ms,
`maxProperties: 1` 849-866 ms, `patternProperties` 876 ms, `propertyNames`
still refuses after 66.9 s).

The ABI test `abi: name-state lemma K1 - scope, boundaries and counters`
pins both sides with equal masks: `minProperties` is accelerated; capacity,
closed object, `propertyNames`, `patternProperties`, a schema-valued
`additionalProperties`, `unevaluatedProperties`, `dependentRequired`, a
`dependentSchemas` capture, a parent element capture (`uniqueItems`), a
`oneOf` with two live branches, a state inside an escape and states inside
2-byte and 4-byte UTF-8 sequences keep the exact path (`name_used == 0`,
with `refuse_name_state` or `refuse_content_dep` non-zero).

### The `any_nodes` arena defect in the first K1 candidate

The first K1 build `7a4dcbd2...dc80` filled the AnyJSON mark *after*
`Builder.finish`. `finish` takes the arena by value and allocates its own
tables (`pat_len`, `nonempty`) through that copy, so the original allocator
was still at the pre-finish position: the bitset was handed the same memory
as `pat_len` and the bit writes landed in the pattern-length table. The
review reproduced the overlap at depths 1, 4 and 64 on `{"type":"object"}`,
a schema with no pattern at all.

The defect is not only metadata. The VE replay of `7a4dcbd2` failed on
`Github_hard---o74466`: 1 147 calls instead of 1 515, 21 aborts instead of
19, and 12 field-mismatch records across 5 instances - `InvalidTokenError`
in `commit_token` at token 69, `consumed` 69 against 148. A mask allowed a
token the parser then refused, so the corrupted descriptor was read for a
node without a pattern. The 43-file slice and the 18 name-state cases do not
contain that shape and passed; the VE caught it.

Fix: the bitset is allocated and filled before `Builder.finish`, and the
call only installs the finished slice in the grammar. The regression test
`spec-v1: any_nodes is allocated before finish and does not alias pat_len`
compiles `{"type":"object"}` at depths 1, 4 and 64 and requires every
`pat_len` entry to stay null, the mark to be non-empty and the two tables to
be disjoint. `zig build test`: 318/318.

The `7a4dcbd2` artifact is withdrawn. Its name-state and slice numbers
describe that artifact on the inputs that were checked; the VE input that
exposed the defect is recorded in `evidence/7a4dcbd2/`.

### K1 candidate `7eb8ed01...3be0`

- build `zig build -Drelease=true -Dstrip=true`, 1 522 800 bytes, sha256
  `7eb8ed0160e210e9cf6f797537c305557f984096bc2fc96d450bf1a00f0e3be0`;
- `zig build test` with a fresh cache: 318/318, including the arena
  regression and the K1 ABI table;
- name states on the tree build: 18/18 with `--expect-fast after`, K1 on
  the nine marked cases, masks bit for bit equal (table above);
- the 43-file slice on the pinned library: 0 mismatched files, 684 calls,
  mask 0.9 s against 13.5 s on `4b567de3` (15.9 s on `a4b1`), p50 0.011 ms,
  p90 5.933 ms, p99 14.181 ms (841.8 ms before), no call >= 1 s. The
  recovered 12.6 s is the property-name family: `o58863` -3.46 s,
  `ListPromptsRequest` -3.30 s, `kb_359` -2.49 s, `JME_17` -1.75 s,
  `kb_337` -1.66 s;
- VE on the pinned library: the six mask-limit files, 1 515 calls, 19
  refusals on both sides, 0 stream mismatches, the two corpus compile
  limits in both legs; mask 1 876.1 s -> 1 545.7 s (-330.4 s, -17.6 %),
  calls without a refusal 714.2 s -> 390.1 s (-324.1 s, -45.4 %), refusals
  1 161.9 s -> 1 155.6 s in 19 (74.8 % of the mask time), p75 830.8 ->
  12.97 ms, p90 946.7 -> 89.3 ms, p99 57.4 -> 58.4 s (the refusals); all
  15 checks of `compare_ve_meters.py` pass
  (`equiv/ve_meter_compare_k1b.md`);
- ADR-0007 is still not met: p90 5.93 ms on the 43-file slice against 1 ms.
  The VE list is a different, mask-limit-heavy set (p90 89.3 ms there);
  the refusal count and p99 do not move, so availability is unchanged. The
  remaining tail is no longer the name family; it has to be re-read from
  the new profile before the next lemma is chosen.

### Where the remaining p90 comes from

`slice_calls.py` records every mask of the slice with its counters
(`evidence/7eb8ed01/diag-2026-10-22/profile/`). The p90 position (the 69th
slowest of 684 calls) is a full-vocabulary string mask at 5.31 ms
(`Snowplow---sp_349`, instance 0, k 22). The counter extent is about 16.8k
trie nodes and 1.39k exact-class terminals; `trie_nodes` counts frame-loop
iterations, including exits and iterations before the subtree check, so it
is an upper bound on the nodes examined, not the number of `feedBytes`
calls. K1 name masks of the same shape cost 6.16 ms.

A temporary probe build (not shipped) measured the walk directly: a string
mask makes about 5 227 one-byte feeds (5 344 for K1) and the walk is about
96 % of the call; 85 % of the feeds are pure ASCII-content prefix edges,
about 1.16 us per feed. The 14 calls on grammars that need the completion
filter take 12.70 and 13.83 ms against 4.93 and 6.16 ms in the unfiltered
groups; that is a between-state comparison, not an isolated measurement of
the filter, and the probe shows the `stateAlive` checks run inside the
walk. The 193 exact name states that K1 refuses have small masks (median
4-7 allowed tokens), mean 0.58-0.71 ms, and are not a target.

The next optimization is therefore the boundary-class walk, starting with
**ASCII prefixes of `.str` values**: advance the code-point count of every
live thread, drop the threads that pass their own `maxLength`, and hand the
exact path the state at the first special byte (quote, backslash, control,
invalid UTF-8); the suffix keeps the normal parse and completion check.
Names (K1) and UTF-8 prefixes are separate later steps, because names need
the full key bytes and buffer ownership and an incomplete UTF-8 prefix must
start the suffix at the sequence lead. The per-terminal completion check on
filtered grammars remains a separate, p99-level item.

## 15. ASCII content prefix skip in the exact walk

The profile above put the p90 in the exact-class walk: a full-vocabulary
string mask makes about 5 227 one-byte feeds, 85 % of them pure ASCII
content before the first quote, backslash, control or non-ASCII byte. The
walk now skips those prefixes for `.str` states.

### State-equivalence lemma

Let S be a uniform string-content state (every live top frame `.str`,
`.normal`, `rem == 0`) with a content-independent enclosure, and let p be a
non-empty byte string over C = 0x20..0x7F minus `"` and `\`. Feeding p
yields S_p with:

* each live thread's top `.str` count increased by `|p|`; state, rem,
  lo/hi and every parent frame unchanged (the enclosure check rejects an
  armed tap or a conjoining combinator, so no parent verdict observes the
  content);
* threads with `count + |p| > max_len` removed at the byte that crosses
  `maxLength` (`strFeed` refuses new content at `count >= maxLength`);
  `minLength` gates only the closing quote, so it removes no thread;
* the surviving threads in order, with the same chunk handles, tap counts
  and comb frames. If none survives, feeding p would Parse.

So S_p depends on `|p|`, not on the bytes. A non-ASCII byte ends the
skipped prefix: the exact suffix starts at that byte (a UTF-8 lead, a
continuation without a lead, or an invalid byte), so the string machine
sees the right `rem/lo/hi`. A token whose first byte is non-ASCII has no
skipped prefix.

### Implementation

* `parser.stringContentPrefix` builds S_p from the root state, per-thread
  maxLength included; it retains the chunk references of the surviving
  threads.
* `StackFrame` carries `pure`, `prefix_len` and `slot_valid`. On a pure
  content edge the walk does not feed; at the first special byte it
  materializes S_p into the frame slot (once per prefix length per node)
  and feeds the byte and the suffix exactly, with the usual Parse pruning
  and the completion filter at terminals.
* Only the string lemma passes `prefix_skip = true`. K1 keeps the plain
  walk: the key chunk must hold the full name bytes, and the dispatch
  depends on them.
* The charge per frame iteration is unchanged. The walk can visit more
  nodes than before, because a prefix that would have failed early at a
  content byte is now pruned at the first special byte instead; the total
  work charge is therefore not necessarily lower.

### Tests and measurements

* `zig build test`: 319/319, including the per-thread prefix unit test and
  the ABI `anyof-prefix-maxlength` case (section 5).
* boundary coverage for the skip, by test:
  * `fast path: three-way differential on random string grammars` compares
    fast, trie walk and the brute scan bit for bit over 40 random
    vocabularies built from quotes, backslashes, controls (`0x01`,
    `0x1f`, `0x7f`), valid UTF-8, a truncated lead and trapped sequences,
    with random `minLength`/`maxLength` (R = 0, min, min+1, min+2,
    CP_MAX-1, CP_MAX+1, unbounded) at five successive states each: every
    prefix length, every first-special-byte kind and a prefix followed by a
    branching edge are covered, and DeadEnd must match exactly;
  * `fast path: uniform string state equals the trie walk, R edges` checks
    the full mixed vocabulary at every prefix length up to and past
    `maxLength`, including the shared `a` edge of the content token
    `a` + U+00E9 and the exact token `a"`;
  * the per-thread drop is exercised by the unit test (`maxLength: 0` thread
    dies on the prefix, the `minLength: 2` thread survives and refuses the
    quote) and, with the real reviewer vocabulary, by the ABI case
    `anyof-prefix-maxlength` (id 263 `ab"` absent, `"` and `a` present,
    masks equal);
  * K1 stays out of the skip: `names/name-states-after.json` (18/18, K1 on
    9) and the K1 ABI table keep name masks bit for bit equal.
* name states on `642ba1ae...7da4`: 18/18 with K1 on 9, masks bit for bit
  equal; name masks unchanged (the skip is off for K1).
* the 43-file slice on the pinned library, mask_equiv: 0 mismatched files,
  684 calls, mask 0.4 s against 0.9 s on `7eb8ed01`, p50 0.005 ms against
  0.011 ms, p90 **1.416 ms** against 5.933 ms, p99 **8.522 ms** against
  14.181 ms. The pinned per-call profile of the same slice moves from
  0.730 s / p90 5.312 / p99 12.836 on `7eb8ed01` to 0.451 s / p90 1.627 /
  p99 8.793. In the pinned profile the `string` group falls from 4.93 to
  1.35 ms and `string+alive` from 12.70 to 8.81 ms (the per-terminal
  `stateAlive` remains); K1 name masks stay at 6.16 against 6.42 ms (run
  noise).
* VE on the pinned prefix artifact: 1 515 completed calls plus 19
  refusing calls, 0 outcome mismatches, no capped run, no unexpected error;
  the same two corpus compile limits as ORIG. The replay compares outcomes
  (`n_ids`, `consumed`, `rejected_at`), not full masks: bit-for-bit mask
  equality rests on the differential and ABI tests above and on the
  name-state harness. All 15 strict checks pass (comparator exit 0). Mask
  time falls from 1 876.1 to 1 490.4 s (-20.6 %); the calls without a
  refusal from 714.2 to 386.5 s (-45.9 %), the refusals from 1 161.9 to
  1 103.9 s (they are 74.1 % of the mask time). p75 830.8 -> 8.6 ms, p90
  946.7 -> 83.7 ms, p99 stays in the refusals (57.4 -> 55.4 s). The largest
  per-file gains: `o90904` -117.1 s, `o74466` -104.6 s, `o12278` -80.1 s,
  `o54553` -60.2 s.
* ADR-0007 is not met yet: slice p90 1.42 ms against 1 ms, VE p90 83.7 ms
  against the refusals at p99. The remaining p90 is the frame loop plus the
  suffix feeds of the string mask; the tail is the filtered string/name
  calls (8.8-14 ms), the K1 name masks and the mask-limit refusals.

## 16. Where the p90 string mask goes; where the heavy refusals go

Two temporary probes (instrumented builds, source restored; package
`benchmarks/maskbench/t8a/evidence/probe-p90-2026-10-23/`) separate the
remaining p90 cost and locate the completion-filter cost of the VE tail.
The profiler now also records fill and commit separately: the commit is
negligible (slice p90 0.003 ms), so the target lives entirely in the fill.

### The p90 string mask

Means over the 69 unfiltered string calls of the slice:

| component | time | share of fill |
|---|---:|---:|
| fill | 1.491 ms | 100 % |
| `fillMaskFast` total | 1.395 ms | 94 % |
| pruned walk | 1.238 ms | 83 % |
| content class, charges, checks | 0.157 ms | 11 % |
| engine/ctypes | 0.096 ms | 6 % |
| commit | 0.002 ms | 0.1 % |

Inside the walk, the measured sub-work is small: 3 434 one-byte feeds cost
65 us (18.9 ns each), 945 materializations 12 us (2 082 thread frames),
1 364 exact-bit nodes 43 us, 2 687 chunk releases 7.4 us (2.8 ns each).
Together about 127 us. The bulk is an unmeasured full-struct assignment
the probe then found: `buf.states.items[top] = buf.spare` copies the
fixed-size `State` (128 threads x 64 frames, **262 660 bytes**) after every
single-edge feed. Measured: 148 copies per string mask, 5.3 us each,
**787 us = 57 % of the 1.376 ms fill**; the initial
`appendAssumeCapacity(st.*)` adds 15 us per call. The compact live-part
copy that fixes it is section 17.

The iteration count is not the lever. A temporary exact-class children
index (`exact_off`/`exact_edges` in `FastData`) removed 8 893 of the 16 780
frame iterations (62 % of edges are pruned siblings) while keeping the work
charge and `trie_nodes` identical and masks bit for bit equal (319/319
tests, slice 0 mismatched files). The walk time changed by at most ~0.15 ms
inside the different probe overheads, the paired A/B slice p90 medians were
1.738 vs 1.614 ms with overlapping ranges, and `mask_equiv` totals stayed
at 0.4-0.5 s. A pruned edge costs a few nanoseconds, so the experiment was
withdrawn.

### The heavy refusals

With the completion filter instrumented as initial check vs terminal
check:

* `Github_easy---o13129.json`: all four instances abort on the first mask,
  which is computed on the **empty prefix** (k 0, tail empty; the offending
  token is the document's first one). The fast path is refused, the plain
  walk visits 124 nodes, and **one** terminal `stateAlive` call consumes
  57.7-57.9 s before `ResourceLimit`; the mask-level initial check never
  runs.
* `Github_hard---o74466.json` (2 aborts at k 148): 33 walk nodes, one
  terminal `stateAlive` of 113.9 s / 114.8 s, initial check not run.
* The normal slow calls of the same file: 266 077 terminal `stateAlive`
  calls take 3.31 s of a 4.50 s call (74 %) over 383 675 walk nodes.

So the refusals and the filtered tail are a completion-filter problem, not
a walk problem, and they are separate from the p90 target. The p90 fix is
the compact state copy of section 17; chain feeding and diagnostic
batching remain later, separate steps, and neither trie traversal
reductions nor `stateAlive` work move the p90.

## 17. Compact state copies in the exact walk

### Defect

The walks keep one parser state per branching trie level in a pool of
`State` values. Three places assigned the whole fixed-size struct: the
walk entry (`appendAssumeCapacity(st.*)`), the prefix materialization
(`items[top] = st.*`) and - on every successful single-edge feed - the
spare swap `items[top] = spare`. `State` is `MAX_THREADS_CAP x
MAX_DEPTH_CAP` frames, **262 660 bytes**, while a mask state carries a few
live threads of a few frames. The probe in section 16 measured the swap:
148 copies per string mask, 5.3 us each, 787 us of a 1.376 ms fill.

### Fix

`parser.copyState` copies only the live part (thread count, bound, and for
each live thread its length, tap count and `frames[0..len]`); it is a
move for the spare swap (`moveState`, the source is left empty, chunk
references travel with the threads) and a retaining copy for the walk
entry and the materialization (`copyStateRetain`). Ownership is unchanged:
the moved state keeps the references it already owned, and the retaining
copy takes one per chunk handle exactly as the old full assignment plus
`retainState` did. `parser.initStateInto` initializes a state in place for
the structural-tag variant entry, and the structural-tag walk uses the same
three helpers (`feedByteInto`'s `out.json = jout.*`, its variant init and
the pool entry were the same defect).

### Results

* `zig build test`: 319/319.
* name states: 18 cases, no failures, K1 on 9, all 17 comparable masks bit
  for bit equal.
* 43-file slice on the pinned artifact `dc415942...5b31` (1 262 688 bytes,
  263 KB smaller than `642ba1ae`): 684 calls, 0 errors, mask 0.273 s, fill
  p50 0.004 / p75 0.090 / **p90 0.608** / p99 7.923 ms; paired against
  `642ba1ae` the p90 medians are 0.632 vs 1.598 ms and `mask_equiv` totals
  0.3 vs 0.4-0.5 s, 0 mismatched files.
* **ADR-0007 TBM p90 <= 1 ms is met on the 43-file slice: 0.608 ms**
  (fill; commit p90 0.002 ms). On the six mask-limit files the VE p90
  falls from 946.7 ms (run wheel) and 83.7 ms (`642ba1ae`) to 49.6 ms and
  stays above 1 ms: that corpus's p90 is the filtered string and name
  states, whose cost is the completion filter, not the state copy.
* VE on the pinned artifact: 1 515 completed plus 19 refusing calls, 0
  outcome mismatches, no capped run, no unexpected error, the same two
  corpus compile limits; all 15 strict checks pass (comparator exit 0).
  Mask time 1 490.4 -> 1 375.4 s against `642ba1ae` (1 876.1 s on the run
  wheel); calls without a refusal 386.5 -> 266.2 s; refusals 1 103.9 ->
  1 109.2 s. p75 8.6 -> 7.5 ms, p90 83.7 -> 49.6 ms, p99 stays in the
  refusals (55.4 -> 56.0 s). Per-file gains against the wheel:
  `o74466` -133.1 s, `o90904` -133.0 s, `o54553` -105.0 s, `o12278`
  -79.2 s.
* ADR-0007 warm guard, measured after the change with `bench_adr0007.py`
  and bound to the artifact: the parent preloads the exact file through a
  space-free temporary directory (a directory holding only
  `libbolorgir.so.0.1.0` does not satisfy the `libbolorgir.so.0` SONAME the
  extension asks for), passes its SHA256 in `BLG_EXPECT_LIB_SHA256`, each
  child reads the mapped `libbolorgir` from `/proc/self/maps` and hashes
  it, and the parent rejects a leg whose `loaded.sha256` differs. All legs
  report `dc415942...5b31`. The C-extension cache hit (`on`) takes p50
  1.061-1.326 us and mean 1.112-1.363 us over the full run and three
  repeats. Seven of the eight on-legs pass the `<= 1.3 us` guard on both
  the median and the mean; the in-object probe of the full run is at p50
  1.326 and mean 1.363 and exceeds it. The result is at the threshold with
  one exceedance, and a hard-guard verdict needs an aggregation rule over
  repeats fixed in advance, which is not established here. The historical
  0.85 us baseline is from the original measurement machine. The same run
  measures the cold permissive probe at 16.2 / 17.5 ms off against 4.37 /
  4.31 ms on (stretch target `<= 10 ms`), work ops 383 647 -> 20 744.

## 18. Where the completion filter goes

A temporary probe inside `stateAlive` (instrumented build on top of
`dc415942`, source restored; package
`benchmarks/maskbench/t8a/evidence/probe-search-2026-10-23/`) separates the
two workloads that make the remaining tail. The counters show `stateAlive`
ns, `search` entries, memo hits, stale drops, `certifyState` calls and ns,
feed attempts, Parse prunes, unmemoized nodes and the tier budget spent.

### The heavy refusal: tiers, then a doomed enumeration

One aborting call of `Github_easy---o13129.json` (first mask on the empty
prefix) takes 58.4 s and all of it is `stateAlive`:

* three tiers ran with budgets 32, 512 and 8 192 (8 736 spent) and depth
  caps 32, 256, 512; the third hits `MAX_DEPTH_REACH` and returns
  `ResourceLimit`;
* 8 738 `certifyState` calls, all undecided, cost 7 ms in total;
* **52 712 083 `search` entries**, of which **52 703 342 (99.98 %) returned
  immediately because the tier budget was already 0**, and **69 275 302
  token feeds**, of which **69 219 288 (99.92 %) were attempted at budget
  0** (16.6 M pruned by Parse);
* capped reasons counted apart: budget 52 703 342, depth 5, **scratch pool
  full 1 046 777 395**. The pool is bounded at `max_states = 256`, below
  the last tier's cap of 512, so most of the post-budget enumeration
  bounces off the pool boundary: `feedAndSearch` returns `capped` before
  feeding, which is cheap per attempt but happens a billion times;
* the earlier run of the same file counted memo hits 1, stale drops 2 and
  unmemoized nodes 0.

The time is neither certification nor memoization. Once a tier's budget is
gone, the DFS keeps enumerating the vocabulary at every remaining branch
and only then asks `search`, which returns `capped` immediately; each
budgeted state also re-scans the vocabulary once per ordering class (six
passes over 128 256 tokens). Cutting the post-budget continuation would
remove tens of millions of feeds, but it also removes the free `canEnd`
witnesses found after exhaustion, so it can turn a currently completing
call into `ResourceLimit`: that is a semantics decision and needs a VE
comparison of the refusal counts, not only a timing run. The order-
preserving next experiment is a token index that removes the repeated
vocabulary passes while keeping the candidate sequence, the `feedAndSearch`
calls and the work charges.

### The slice: certification, not search

On the 83 filtered calls of the 43-file slice the filter takes 114.4 ms of
a 162.3 ms step (70 %), `search` never runs (0 entries), and
`certifyState` is called **45 514** times (about 548 per call, 2.495 us
each) and always settles without search. A second probe with the full key
at the top-level certification (per-row corrected against the ordinary
profile; saved as `probe_certificate_keys.patch`) shows the states repeat:
**41 896 (92.05 %) of the certifications hit a key already certified in
that fill**, only **3 618** keys are distinct, and building `Cache.keyOf`
(state image plus the chunk-content blob) costs **0.051 us** against
2.495 us for the certificate. A certificate cache keyed by that full key
would remove about 92 % of the 113.6 ms certification time on this
workload; the key is 49x cheaper than the verdict it guards. The refusal
workload needs the budget-continuation decision first.

## 19. A certificate cache inside one fill

Section 18 measured that the slice's certifications repeat within a fill:
92.05 % of the top-level certifications ask about a state whose full key
was already certified, and the key costs 0.051 us against 2.495 us for the
verdict. The cache added here is deliberately narrow. Only the top-level
`stateAlive` certification consults it; the recursive DFS memo stays a
separate structure, as does the budget-0 branch. Only proved verdicts are
stored (`alive`/`dead`); `undecided`, refusals and anything the bounded
search touched keep their old path, so a cached hit is the same verdict a
fresh certification would have returned. The map lives for exactly one
`fill` and is cleared with the DFS memo; its keys are owned copies of the
full `Cache.keyOf` string, never a borrowed state image or chunk pointer.
The cap is 4 096 entries and a full map (or a failed insert) falls through
to ordinary certification. `complete.test_overrides.cert_cache_off` lets
tests and profiling runs A/B the same artifact.

A cap alone does not make the cache harmless, and an earlier version of
this section claimed more than that: stored bytes charged to the same
budget the search needs can turn a completing call into `ResourceLimit`.
Three reproductions, all with `Accounting` byte limits set to the peak of
the cache-off leg, found three separate pieces of optional memory. A cap
of 4 096 entries does not cover any of them.

- A stored certificate on a `oneOf` residual fixture (limit 526 339 B): one
  173-byte key takes the room the scratch pool needs, and the call that
  settles alive without the cache retires with `ResourceLimit`.
- The key buffer on the very first `stateAlive` call: `Cache.keyOf` grows
  `key_buf` (188 B) before the proof runs, so on a reachable mid-escape
  state (limit 525 336 B) the two-state proof scratch no longer fits.
- The map table kept by `reset`: entries are freed per fill but
  `clearRetainingCapacity` keeps the table, and an entry-count release
  rule cannot see it (limit 525 336 B, `undecided` instead of `alive`).

The cache is therefore treated as optional memory with two release rules:

- before the first search of a fill, `dropCerts` frees every key, the map
  table and the key buffer, and keeps the cache off for the rest of that
  fill - the branch that needs the scratch runs with exactly the memory the
  cache-off build would have;
- a certification that had to degrade to `undecided` on an allocation
  failure (mid-escape scratch, pattern work arrays, doom marks, the
  nameable-prefix buffer) sets `cert_oom`; the top-level certification
  sees it, drops the cache and re-proves once, so cached bytes can never
  turn a decided verdict into an undecided one. The rule keys on held
  capacity (`certHoldsMemory`), not on the entry count: a table that
  `reset` left empty but allocated is reclaimed like a full one.

Four regression tests pin the boundary, each failing if its release is
removed: the `oneOf` fixture at the cache-off peak, the mid-escape state
with a pre-inserted key, the first `stateAlive` with the key buffer only,
and `certifyTop` after `certPut` + `reset`.

A fifth reproduction showed that the filter's rules are not the whole
story. An open object whose undeclared values are `^a*$` strings, with the
key `{"` already fed, fails inside `fillMask` itself: feeding a 1 024-byte
name token copies the name (`parser.openKeyAppend`) before the filter
reaches another certification, so at a 1 317 199-byte limit - the
cache-off leg's own peak - the cache-on leg raised `OutOfMemory` while the
cache-off leg produced the full mask. Three releases close that class: the
mask walk wraps the walk and, on `OutOfMemory` with any optional memory
held, releases the cache, restores the work counter and the per-fill
search budget and retries the fill once (the walk rebuilds `out` from the
initial state, so the retry is exactly the cache-off run); the accept and
draft-validation paths release the cache before they feed the parser, so
optional bytes never sit in front of `feedBytes` there; and the cache is
still off for the rest of that fill. The regression test `fast path: the
certificate cache cannot fail a fill's walk` runs the whole `fillMask` at
the cache-off peak and requires a full, equal mask, with the cache empty
afterwards; removing the retry turns it back into the `OutOfMemory` the
review found.

A sixth reproduction moved the same boundary outside that wrapper. The
fast path certifies its initial state and stores the certificate before
`fillMaskExactPruned` asks for its state pool, and `c_api.zig` calls
`fillMaskFast` directly, so the walk wrapper never ran. On the owner's
K1-eligible open-object fixture (`properties.x` with pattern `^a*$`, the
prefix `{"`, a 1 024-byte name token) the cache-off leg peaks at
1 313 938 B; at that same limit the cache-on leg returned `OutOfMemory`
with one certificate held (cache-on peak 1 314 402 B). The structural-tag
builder has the same shape: it owns a separate `Pool` and content
completion cache, is called directly from `c_api.zig`, and its
`pool.ensure` and parser feeds can fail while a retained certificate
table is charged. Both builders now use the walk's release rule: on
`OutOfMemory` with optional memory held, drop the cache, restore the
per-fill search budget and the work counter, and retry once. The
structural-tag cache clears entries per content variant but keeps a
release in force (`Cache.resetForVariant`), so a mid-walk variant switch
cannot re-enable the memory the retry just freed. Two regressions pin
this: `fast path: the certificate cache cannot fail a fast fill` (the
owner fixture, exact word 7, 9 charged operations on both arms) and
`structtag: the certificate cache cannot fail a mask fill` (64 certified
content states retained, cache-off peak boundary); removing either retry
turns them back into `OutOfMemory`.

The acceptance harness had a matching hole: a failed warm protocol wrote
`warm_acceptance.pass=false` and exited 0, the matrix marked the row
`pass` and printed Overall PASS, and the `bench-gate` row passed
`--skip-maskbench` at every scale. The harness now marks the run `FAIL`
and exits 1; the matrix re-reads `warm_acceptance.pass` and
`gate.maskbench_states` from the written JSON and fails a row on a
missing or false verdict; the gate row runs the pinned MaskBench slice
(24 compiling files, 425 recorded states on the pin of that stage; the
later `a87e2f71` gate run records 564 masks) with no
`--skip-maskbench`, while the separate warm row keeps its own skip. The
warm verdict itself is unchanged; the next paragraph reports it as it
was measured.

What remains outside the release rules is the boundary every allocation
has: these are completion-scratch limits inside the session accounting, not
measurements through the public ABI, and a passing VE replay proves the
tested inputs, not every path. The warm guard is a separate acceptance,
and its first run under the protocol fixed in ADR-0007 (five repeats,
alternating leg order, all ten means <= 1.3 us) recorded five means between
1.302 and 1.327 us with p50 <= 1.283 us and the cache-off leg in the same
band - the guard is not met on that run, which is the stored `2d982b60`
FAIL, reported as it happened rather than re-rolled. A later stored run
of the `a87e2f71` artifact passed the same protocol on 2026-10-24 (ten
`on` means 1085-1267 ns, p50 1052-1236 ns) and was re-verified offline;
that pass names its own artifact, and the CI confirmation and the formal
closure remain separate steps.

The cost is paid only where the fill actually searches. On the 43-file
slice certification settles every filtered call and `search` never runs,
so the win below is unchanged: an interleaved A/B against the pre-fix
cache artifact `69a24712` gives 0.213-0.233 s with p90 0.684-0.905 ms
there and 0.212-0.218 s with p90 0.656-0.764 ms here. The refusal
workload gives the cache up at the first search of each fill, which costs
nothing and saves a little: the six mask-limit files go 1 876.1 s ->
1 234.3 s (refusals 1 161.9 s -> 1 105.9 s), better than the pre-fix
cache build's 1 273.1 s (1 147.4 s) and close to the compact baseline,
with the same 19 refusals and 0 mismatched files.

On the 43-file slice the mask step falls from 0.273 s to 0.212-0.220 s,
the filtered group from 167.4 ms to 73.6-74.2 ms, and an interleaved A/B of
`dc415942` against the cache artifact (three runs each) keeps p90 where it
was (0.739/0.729/0.787 ms vs 0.736/0.709/0.747 ms) while halving p99
(8.29/8.18/9.07 ms -> 4.25/4.11/4.27 ms) and the total mask step
(0.3 s -> 0.2 s). The p90 is no longer a filtered call: it is an unfiltered
string mask, so the cache cannot move it. Correctness: `mask_equiv` on the
slice reports 0 mismatched files and 0 errors, the property-name states
keep `failures: []` with K1 on all nine cases, and `zig build test` passes
(including the cache's hit/reset/cap/grammar tests, the seven
memory-boundary regressions and the mask-level "certificate cache does not
change a filtered mask" differential). The VE replay, the strict
comparison and the warm-cache runs are in the artifact's evidence package.
What is left of the filtered 73.6 ms is the part the cache cannot serve:
states that stay `undecided`, the per-call setup, and the `canEnd`
witnesses the search still has to find.
