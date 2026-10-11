# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - not yet released (preview 0.2.0rc1 on GitHub)

### Changed

- Toolchain: the core, `build.zig` and CI moved to Zig 0.17.0 (from
  0.15.2); the C ABI, Python API, mask semantics, error codes, cancellation
  and limits are unchanged. The new core is prepared for the next
  acceptance: the R6 verdict on record names the earlier core `6e62e0d1`
  only, and the release gate stays closed.
- Python: the abi3 floor is CPython 3.12 (`Py_LIMITED_API=0x030C0000`,
  wheel tag `cp312-abi3`). CPython 3.10 (EOL October 2026) and 3.11 are
  dropped; CPython 3.15 joins the range. CI and the release smoke jobs
  cover 3.12-3.15.

### Documentation

- README and release notes for the public preview 0.2.0rc1
  ([docs/release/v0.2.0rc1.md](docs/release/v0.2.0rc1.md)): installation of
  the wheel from the GitHub pre-release with `SHA256SUMS` and the core hash,
  a build from the tag with the release flags, a quickstart without a model
  download (`examples/quickstart.py`, run from the built wheel in the release
  workflow) and a separate Hugging Face example; the preview status
  (`canonical-v1` default, `spec-v1` experimental, T3/R6 admitted for core
  `6e62e0d1`, full T8a not complete, slow masks and `RESOURCE_LIMIT` on some
  `spec-v1` schemas, memory limits are not time limits); historical
  measurements carry engine version, profile and conditions; how to report
  a problem (also in the issue template). The 0.2.0 notes no longer claim
  that the availability findings of the audit are closed.

### Fixed

- spec-v1: the mask admitted an object key whose closing quote dispatch
  then refused, so the document ended in `DEAD_END` (T3 acceptance of
  50cbd80e: 52 of 140 byte-tokenizer passes of seven repository schemas).
  The exact open-object certificates of the completion filter
  (`src/complete.zig`) were wrong in three places. A `patternProperties`
  key counted as a fresh completion whenever the pattern could still
  match, but a finite pattern language (`^line[1-3]$`) runs out once its
  keys are seen; the certificate now counts the pattern's completions
  from the DFA state exactly and compares them with the taken keys (seen,
  declared, and undeclared dependency triggers whose dispatch fails). A
  missing dependency name declared after a pending required prop was
  taken for unreachable (`favoriteTopic` requiring `tags` behind the
  required `firstName`). And the dependencies a declared key fires by its
  own dispatch were not checked while the key was spelled, so a trigger
  whose dependency was already skipped in schema order was admitted.
  Regression tests: `tests/test_open_object_dead_ends.py` (exact masks,
  walks, agreement with the bounded search) and the unit test
  "complete: pattern key completions are counted exactly on both dispatch
  sides".
- spec-v1: the same certificates, reviewed, gave false verdicts on the
  fallback side of a key pattern and along dependencies. A key the pattern
  does not match was a fallback witness only when the empty key was fresh
  or an extension provably left the pattern, so a finite non-matching
  remnant (`""` and `"a"` beside `^([\s\S]{2,}|[^a])$`) answered
  `RESOURCE_LIMIT` and a literal pattern beside a declared `""` masked the
  comma of `{"":0`; both dispatch sides are now counted exactly from the
  DFA state (linear in its size, no search budget). A name that a
  dependency entry can never satisfy any more (a declared name behind the
  skip, an empty value, recursively along a chain of triggers) now dooms
  every key that fires it and every obligation that needs it, also on the
  mid-code-point key path; before, a two-step chain or a multi-byte key
  could still end in `DEAD_END`, and a required key whose own dependency
  was skipped left the object unclosable. An open object with a
  `dependentRequired` entry now runs the completion filter
  (`src/grammar.zig`): fresh undeclared keys kept such prefixes typable
  forever. Independent check: `tests/completion_reference.py` searches a
  bounded set of validator-judged completions; a byte the mask refuses
  must have none.
- spec-v1: an open object with `maxProperties` did not run the completion
  filter, so once the cap was reached the comma was still admitted and
  the next key could never close (`{"type":"object","maxProperties":1}`
  after `{"x":1`: false alive, the document never valid). Such objects
  now run the filter (`src/grammar.zig`), and the count certificate
  (`src/complete.zig`) is exact for them whatever else they constrain:
  the names a completion still owes (`required` names and the dependency
  names they fire, closed under the dependencies) each take one more key,
  and a key typed while every remaining slot is owed must spell an owed
  name, else it is dead. Before, such a key had no dead rule and the
  search, unable to exhaust the key spellings, failed the mask with
  `RESOURCE_LIMIT` (`maxProperties: 1` with `required: ["a"]` at `{"`).
  Aliveness is exact in closed form where one completion of a fitting key
  count exists (the owed names, a fresh key where one is mandatory, fresh
  keys up to `minProperties`), so the masks of keys and nested values of
  such objects do not run the simulation per token; the simulation
  certificate (`src/synth.zig`) now also completes an in-flight key to an
  owed dependency name. Regression tests: masks from
  the document start for `maxProperties` 0, 1 and N, at the cap and below
  it, with required, declared and dependency names, `minProperties`,
  nesting and arrays (`tests/test_open_object_dead_ends.py`) and the unit
  test "complete: maxProperties counts the owed names and the in-flight
  key".
- Build: `-Dstrip=true` omits debug information from the library. The
  release recipe uses it: the debug sections embed absolute source and
  cache paths, so only stripped builds are byte-identical across build
  directories.
- T3 acceptance runner (`benchmarks/t3/run_t3_accept.sh`): the run
  directory now holds every input by content - the source tree of the
  candidate commit, the library, both corpus snapshots and the tokenizer
  snapshots, pinned in `benchmarks/t3/acceptance_pins.json`, and a venv
  of its own created at its final path from
  `benchmarks/t3/acceptance-requirements.txt`, and the package of the
  copy with its `_core` built against the candidate library; before, it
  linked the shared `.venv` and `benchmarks/external`, took the package's
  built extension from the working tree and compared only `pip freeze`. `benchmarks/t3/t3_identity.py` records every file and checks
  all of them, with the run parameters, on every resume; an interrupted
  preparation is refused, and the harness exit status is passed through
  (`tests/test_t3_accept_runner.py`).

- spec-v1: an object with `dependentRequired` (a draft-04 `dependencies`
  arrays entry) left its mid-key states to the bounded search. On a
  vocabulary whose tokens end inside such a state the search could exceed
  its tier caps, and the mask failed with `RESOURCE_LIMIT` mid document
  (`Github_medium/o32437.json` with Llama-3.1, 5 of 20 passes). A state
  that skipped a declared name the dependency still requires was also
  decided wrongly, since the parser refuses a prop before `next_idx` for
  the rest of the frame. The open-object certificate now analyzes
  dependentRequired entries: a skipped or unreachable name is dead, and
  a witness is accepted only when every missing name stays dispatchable
  after it. Regression test:
  `tests/test_residual_certificates.py::test_dependent_required_keys_follow_the_dispatch_order`.
- The memory a compile or a mask computation is charged no longer depends
  on the process history. The grammar arena and the engine's scratch
  arenas grew their chunks in place when the address range after a chunk
  happened to be free, and took a new chunk 1.5 times the previous size
  otherwise, so one compile of `Github_hard/o21363.json` was charged 146
  to 190 MiB depending on earlier allocations; a request for such a chunk
  could exceed the 256 MiB limit while 117 MiB were in use, and the
  compile failed with `out of memory during compile` in a process where
  it had succeeded before (docs/RUN_HISTORY.md). Arenas now never grow a
  chunk in place (`alloc.Arena`: doubling chunks up to 1 MiB, large
  requests in their own chunk returned on free), and the accounting
  allocator refuses growth past the pages a block spans. A pattern used
  by several `$ref` copies is compiled to one DFA, and the scratch of DFA
  construction and of the emptiness analysis is released after the
  compile: o21363 now takes 28.5 MiB in every cycle. Regression tests:
  the Zig test "accounting and arena: charges do not depend on in-place
  growth" and `tests/test_memory_history.py`.
- Parser (both profiles): threads that read the same value with the same
  machines over different but equal nodes (the "any value" of each
  branch of an untyped `anyOf`) were not merged, and
  `max_threads_per_state` was checked while a byte forked threads, before
  the threads that byte killed were removed. Seven untyped closed objects
  under `anyOf`, for example, read a number with 14 threads; in
  `Github_ultra/o6366.json` (skaffold) four such arrays exceeded the 64
  threads of the coverage harness at the first byte of a number element,
  and the mask failed with `RESOURCE_LIMIT`. Threads whose frames differ
  only in leaf value machines over nodes of equal content are now
  merged, and the limit counts the distinct live threads after each byte
  (within a byte the parser may fork up to `MAX_THREADS_CAP`). Regression
  cases: `tests/test_thread_merge.py`.
- Closed objects (both profiles) failed with `RESOURCE_LIMIT` right after
  `{` when more optional properties preceded the first required one than
  `max_threads_per_state` allows (128 by default, 64 in the coverage
  harness): the parser spawned one thread per candidate property. A key is
  now read on one thread that narrows the candidates byte by byte.
  Regression case: `tests/test_closed_object_keys.py`.
- spec-v1: a key that no property can take stayed open forever. With
  `additionalProperties: false` next to a key pattern (or with
  `propertyNames`, or `propertyNames: false`), the parser refuses only the
  closing quote of such a key, and grammars whose values need no
  completion filter had none for keys either: under
  `{"type":"object","patternProperties":{"^[a-z]+$":{"type":"string"}},
  "additionalProperties":false}` the mask after `{"` admitted `Z`, and
  every later key byte stayed allowed. Such objects now use the completion
  filter; key prefixes that cannot complete to a dispatchable key are
  refused (also the lead byte of a code point the key pattern cannot take,
  and keys longer than a `propertyNames` maxLength). Present since 0.1.0.
  Regression cases in `tests/test_residual_certificates.py`.

- spec-v1 accepted documents that `oneOf` or `if`/`then`/`else` rejects
  when a branch's declared keys arrived out of schema order. A branch
  thread died on the out-of-order key, so the branch did not vote even
  though the value matched it. Example: under
  `{"type":"object","oneOf":[{"properties":{"error":{"type":"string"},"reason":{"type":"string"}},"required":["error","reason"]},{"properties":{"id":{"type":"string"},"ok":{"type":"boolean"}},"required":["id","ok"]}]}`
  the document `{"reason":"","id":"","error":"","ok":true}` matches
  both branches and was accepted as matching only the second. The same
  held for the `if` condition (the `else` applicator replaced `then`)
  and for a `contains` match under a finite `maxContains`. These
  positions now evaluate a copy of their subgraph in which every object
  accepts its declared keys in any order, each at most once, with the
  required ones checked at close (semantics-spec-v1 section 4.3). Such
  documents are rejected, and documents valid by value with keys out of
  schema order inside these positions are now accepted. Found by the
  generatable coverage run on `Github_trivial/o60144.json`,
  `Github_trivial/o60145.json` and `Github_medium/o85194.json`;
  regression cases in
  `tests/test_spec_v1_p3.py::test_value_verdicts_ignore_key_order`. The
  spec-v1 generative test now also builds `oneOf` branches over the same
  names in opposite declared orders (`tests/fuzz/schema_gen.py`), and the
  completion witness of a mandatory key after a comma tries the next
  optional property when the first one would complete a second `oneOf`
  branch.
- Parser (both profiles): threads spawned while one byte was being fed
  were stepped by index arithmetic that assumed they sat right after the
  thread that spawned them, but new threads are appended at the end. When
  one byte spawned threads from several sources, one thread never saw the
  byte and another saw it twice. Both directions followed. Under
  `{"anyOf":[{},{},{}]}` the document `8""` was accepted, since a thread
  that missed the `8` read `""` as the whole value: a soundness defect
  present since 0.1.0, found by the generatable coverage run of the 0.1.0
  build on `Snowplow/sp_343_Normalized.json` (a field generated as
  `"error":8""`). On the first byte of an array element whose `allOf`
  holds two unions (`not` of a `required` list), the mask after `[`
  refused `{` and `[{}]` was rejected, so generation ended in `DEAD_END`
  (`Github_ultra/o62895.json`, `o62897.json`, `o62899.json`). Each spawn
  site now feeds exactly the threads it spawned. Regression cases: the Zig
  tests "comb siblings spawned on an array element's first byte see it
  once" and "untyped anyOf branches spawned on a number's first byte all
  see it", and
  `tests/test_spec_v1_r1_residual.py::test_r1_spawned_threads_and_closed_min_properties`.
- spec-v1: a closed object whose `minProperties` can only be met by
  optional declared properties was judged empty, because the emptiness
  analysis counted on undeclared keys, which a closed object refuses. A
  required property holding such an object made its parent dead at the
  first byte (`Github_ultra/o84874.json`). The analysis now counts the
  declared properties, and the completion witness pads with them.
- spec-v1 mask: the residual-reachability search counted a branch that
  outgrew the parser thread or frame cap as dead, so the mask could drop
  a token with no `RESOURCE_LIMIT`, against the documented rule that the
  thread cap never drops branches silently (docs/supported_features.md
  section 6). With `max_threads_per_state` 64, the limit of the coverage
  harness, eight valid MaskBench instances of the `Github_ultra/o6354.json`
  family (skaffold configs) were rejected at the `{` of their first array
  element. Such a branch is now undecided like a budget cut: the mask
  keeps the token when another continuation proves it and fails with
  `RESOURCE_LIMIT` otherwise. Present since 0.1.0. Regression case:
  `tests/test_spec_v1_r1_residual.py::test_r1_thread_cap_never_drops_a_token`.
- Python serializer (`bolorgir.serializer`): under `oneOf`/`anyOf` the
  keys declared by the selected branch were emitted before the outer
  object's own declared keys. When the outer object declares a branch key
  after another key, that order broke the outer declared order, which the
  engine enforces (semantics-spec-v1 section 4.3), and a valid instance
  was rejected after serialization: under an object declaring `name`
  before `type` with a `oneOf` branch declaring `type` and `uom`, the
  value `{"type":"q","uom":"C","name":"n"}` became
  `{"type":"q","uom":"C","name":"n"}` instead of
  `{"name":"n","type":"q","uom":"C"}`. The serializer now emits a
  linearization consistent with the branch's and the outer declared
  orders; the branch's keys still come first where neither order
  decides. Present since 0.1.0; found by the generatable coverage runs
  (12 valid MaskBench instances, among them `Github_easy/o75096.json`,
  `JsonSchemaStore/asmdef.json` and the `Github_medium/o55595.json`
  instance listed before as a serializer defect). Regression cases in
  `python/tests/test_serializer_combinators.py`.
- spec-v1 numeric conjunctions no longer fail at generation time with
  `RESOURCE_LIMIT` (ADR-0011). The keywords of one number
  (`minimum`/`maximum` and their exclusive forms, `multipleOf`,
  `"type": "integer"`, `not` of numeric `const`/`enum`, numeric `const`)
  run as separate machines over the same lexeme; each was certified
  alone, so a dead conjunction (`{"type":"integer","maximum":9}` after
  `51`) went to the budgeted token search, which cannot prove a dead end
  for a state with infinitely many continuations. A closed-form joint
  certificate (`src/numcert.zig`, wired into `certifyState`) decides
  every conjunction of these machines exactly; only shapes outside its
  model (two different `multipleOf` divisors in one conjunction) still
  reach the search. The schemas of the audit tables generate on
  byte-complete vocabularies in both modes with the fast path on and off
  without entering the search (`tests/test_spec_v1_p4_numbers_reachability.py`).
- spec-v1 single numeric ranges: after an exponent marker the range
  certificate treated typed digits that are a proper prefix of a bound's
  digits as equal to the bound (`{"minimum":1.5,"maximum":1.6}` after
  `1e` was certified alive although `1e0 = 1`); fixed in `certNumRange`.
- spec-v1 arrays with `contains` or `uniqueItems`: the mask admitted a
  byte that starts an element the array can no longer accept, so
  generation could end in `DEAD_END`. Examples: `"` after `[` for
  `{"maxItems":1,"contains":{"enum":[false]}}` (the only slot must be
  `false`), `a` after `["a","` and `,` after `["a","b"` for
  `{"items":{"enum":["a","b"]},"uniqueItems":true}`. These arrays now run
  the completion-reachability filter (ADR-0005 Decision 4). The
  `minContains` deficit is checked against the first byte of the element
  in flight, also when a combinator wraps the element. A finite
  `uniqueItems` element language counts as exhausted when the values
  already seen plus the elements still owed outnumber its distinct
  values. Regression cases are in `tests/test_spec_v1_array_residuals.py`.
- spec-v1: `fill_mask` aborted the host process (an out-of-bounds index
  in the witness synthesis of the reachability check) on a closed object
  without declared properties next to object branches of a combinator,
  for example `{"type":"object","additionalProperties":false,"anyOf":
  [{"properties":{"a":{}}},{"properties":{"b":{}}}]}` at the first
  mask. Such an object now gets the plain witness `{}`. Regression case in
  `tests/test_spec_v1_r1_residual.py`.
- String machines (both profiles) admitted `\u002`..`\u00f`, dead
  prefixes that no fourth hex digit completes: the escape table spells
  only U+0000..U+001F with `\u` (semantics.md 2.2). The third hex digit
  is now limited to `0` and `1`. Masks change only at these dead
  prefixes.

### Changed

- spec-v1 completion certificates settle more states instead of failing
  the mask with `RESOURCE_LIMIT` (ADR-0005 D3): strings with `pattern`
  and object keys in the middle of an escape or of a UTF-8 sequence
  (exact over the code points the pending bytes can still decode to);
  `oneOf` branches whose threads are equal up to structurally equal nodes
  (`oneOf [true, true]`, untyped object keywords on a scalar), with an
  integer reading beside a number reading in one branch counted once;
  `if`/`then`/`else` whose else threads the if branch shadows; a triggered
  dependent schema that already refuses the object prefix; numeric
  `oneOf` and `if`/`then`/`else` groups, decided jointly with negated
  bounds and a non-integer condition (ADR-0011 extended); combinator
  alternatives whose required keys the object can no longer take (per
  thread, also under `anyOf` and nested `allOf`): a closed object or an
  open one that has passed the key in declared order or seen it already;
  and
  closed objects with a key in flight, whose completion witness now
  continues from that key (not from the first declared property), adds
  the keys its dependencies require, and emits the names it adds in
  declared order. The completion witness of an open object also extends
  an in-flight key past keys the object already has (the shortest
  completion of a pending UTF-8 character, or the key plus one letter,
  may be such a key), and under `oneOf` and `if`/`then`/`else` it also
  tries values of other JSON types, which can fail a rival branch that
  the default witness value would satisfy. On the oracle the
  pinned spec-v1 residual shrank from 181 to 33 rows (2020-12: 46 to 10),
  with no new row, no MISMATCH change and canonical-v1 unchanged.
  Cross-check of the numeric groups: `tests/numcomb_cert_check.py`.
- spec-v1 completion check: the completion witness gave up on objects
  under `if`/`then`/`else`, so every token state of such an object went
  to the budgeted search (147 searches for one mask of
  `{"if":{"properties":{"type":{"enum":["a"]}}},"then":{"required":["id"]}}`
  after `{"`): slow on large vocabularies, and `RESOURCE_LIMIT` when the
  search runs out of budget on a large schema (`Github_hard/o32934.json`,
  five `if`/`then` on one object). The witness now carries the
  obligations of the thread's own branch under `if`/`then`/`else` as
  under `oneOf`, may leave out an `allOf` sibling that sits under either,
  and the simulation decides the verdict as before. These candidates,
  like the values of other JSON types of the entry above, are tried after
  the earlier ones and with a budget of their own, so a state the earlier
  candidates settled is settled the same way. The value witness of a
  closed object also tries its optional properties one at a time, which
  completes a closed object next to an `anyOf` of required names.

- spec-v1: a numeric conjunction with no satisfying number
  (`{"type":"integer","minimum":0.25,"maximum":0.25}`) is now refused at
  compile time with `UNSATISFIABLE_CONSTRAINT` (an empty subschema when
  nested, ADR-0006 D1) instead of compiling and failing at the first mask
  with `DEAD_END`.
- spec-v1: a conjunction whose branches share no JSON type
  (`{"allOf":[{"type":"object"},{"type":"array"}]}`, or `"type":"string"`
  next to an `anyOf` of integers and `null`) is the empty language. It is
  refused at the root with `UNSATISFIABLE_CONSTRAINT` and becomes an
  empty subschema when nested.
- README and `docs/supported_features.md`: both schema profiles are
  described (canonical-v1 frozen; spec-v1 alpha with its keyword list and
  known limitations); installation is from source only (the package is
  not on PyPI), in a venv with an upgraded pip and the `transformers`
  extra that the Quickstart needs; every performance number cites its
  report (MaskBench
  compile time, control-series warm-mask p99, end-to-end parity), and the
  general "lowest measured mask latencies" claim is removed.
- The spec-v1 coverage reports
  (`benchmarks/coverage/spec-v1/coverage_{repo,maskbench}.{json,md}`) are
  regenerated in the generatable format on the current build: K = 20
  passes per compiled schema on the byte-level tokenizer, Llama 3.1 and
  GPT-2 on a 1-in-20 sample, every pass run and every pass record in
  `*.passes.json.gz` with the document of each invalid pass (command in
  `benchmarks/README.md`).
  Byte-level `generatable`: 87.72% of the repository snapshot and 89.16%
  of the MaskBench snapshot (83.75% and 85.72% on 0.1.0, whose run is
  kept in `baseline-dbc5eeb/`), with no `engine_defect` outcome.
  `benchmarks/coverage/spec-v1/triage.md` classifies every correctness
  signal of both runs and of the previous reports; on the current build
  none is class A or B or unclassified (one 0.1.0 result stays
  unclassified as an accepted gap in the evidence), and every one of its
  10,350 invalid documents is the draft-04 integer spelling listed in
  `tests/fuzz/known_deviations_spec_v1.json`.

### Added

- Release packaging (audit-fix spec T7a): `release.yml` builds the core
  once with the release flags (`-Drelease=true -Dstrip=true
  -Dtarget=x86_64-linux-gnu.2.28 -Dcpu=baseline`), an abi3
  `manylinux_2_28_x86_64` wheel with cibuildwheel and `auditwheel`, and
  an sdist with the bundled core; it checks that the wheel carries that
  core and that the bindings call only functions the core exports. A tag
  or a TestPyPI upload needs the core and the bindings pinned by the
  composition manifest (`docs/release/v0.2.0-composition.json`,
  `.github/scripts/release_gate.py`; no git history is read);
  a final tag also needs the committed R6 verdict and T8a report of the
  built core; rc and manual builds are marked preliminary. A tag
  `vX.Y.ZrcN` creates a draft GitHub pre-release (the public preview)
  and uploads nothing to a package index: job `preview`
  (`release_gate.py preview`) requires the built core to be the
  candidate of the committed, admitted R6 verdict named in the
  composition's `preview` block; job `wheel-smoke` installs the built
  wheel in `python:3.10-slim` to `python:3.13-slim` and runs
  `tests/release/smoke_release.py`, `examples/quickstart.py` and a check
  of the installed core; job `github-prerelease` refuses a tag that
  already has a release, checks the core inside the wheel and attaches
  the wheel, the sdist and `SHA256SUMS` with notes from
  `docs/release/v<version>.md` plus the commit and the core sha256.
  TestPyPI takes only a manual run (`publish: testpypi`). A final tag
  publishes to PyPI (environment `pypi`) and creates the GitHub Release
  from this file and `docs/release/v<version>.md`. CI job `sdist-no-zig`
  installs the sdist without Zig. Version `0.2.0rc1`.
- T3 acceptance processing: `benchmarks/t3/tools/acceptance_compare.py`
  (goals, the transition list against `dbc5eeb` and `7345490f` including
  new main-compile refusals, identity, protocol and HF-sample checks, the
  five oracle dialects against the line oracle, owner approvals of D4
  bound to the candidate, and the acceptance verdict with the T2c triage),
  `benchmarks/t3/r6_review.sh` and `benchmarks/t3/r6_publish.sh` with an
  evidence bundle; `benchmarks/triage_semantic.py` lists results whose
  main compile fails on the final build, runs without `--counters` and
  writes `--out-json`; `tests/oracle/run_oracle.py` records the library
  sha256; a line without oracle data keeps the verdict closed until an
  owner exception is recorded, and the oracle reports of each line are
  bound to its library (header hash or archive binding in
  `benchmarks/t3/oracle_lines.json`). `benchmarks/t3/isolated_tests.sh`
  runs the integration checks in a separate work directory without
  touching `zig-out`, `.venv` or the package of the tree; it checks the
  dependencies and builds the wheel with a separate build backend
  (setuptools>=77) before and after the Zig builds. The runners
  (`isolated_tests.sh`, `r6_review.sh`, `t8a/run_t8a.sh`) set an
  address-space limit per process (`ISO_MEM_LIMIT_MB`, `R6_MEM_LIMIT_MB`:
  16384 MB; `T8A_MEM_LIMIT_MB`: 40960 MB, the MaskBench `--mem-limit`
  its runner sets itself, a lower value is refused), so a runaway process
  fails its step instead of freezing the machine.
- T8a: `benchmarks/maskbench/t8a/` runs MaskBench for Bolorgir (spec-v1,
  serializer) and for llguidance and XGrammar, MaskBench pins and current
  releases, on the same serialized instances: an identity manifest of
  every input, reference token streams per instance compared with each
  engine's records, the five participants fixed in the manifest and
  compared with each run's `meta.txt`, a strict smoke (no crash or timeout
  of any participant) before the full run, venvs built in place with a
  readiness marker, and a report that separates a written report from an
  accepted one. The driver hands the files out itself (`t8a_queue.py`):
  a file runs in one chunk at a time and is retried only after that chunk
  ended without its output, at most twice; the loop of
  `scripts/run_maskbench.py` gave files still running in one worker to
  the others.
  `benchmarks/t5b_readme_numbers.py` gives release numbers only for an
  admitted R6 and an accepted T8a of one core.
- Compile-time generatability probe (ADR-0012): `BLG_COMPILE_PROBE` and
  `BLG_COMPILE_PROBE_STRICT` in the new tail field
  `blg_compile_request.flags`, budgets `probe_max_states` /
  `probe_max_work` in `blg_context_config`, `blg_grammar_probe_status`,
  `blg_stats.probe_runs`; Python `Engine.compile(..., probe="probe" |
  "strict")` and `Constraint.probe_status`. Additive: the earlier struct
  sizes are accepted and `BLG_ABI_VERSION` stays 1. The ABI evolution
  rules are written down in `docs/API.md` section 2.1.
- Transformers adapter: `on_constraint_error="fail_row"` in
  `constrained_generate` and `ConstraintLogitsProcessor` ends only the row
  whose mask or accept failed (EOS only from then on) and reports
  `stop_reason="constraint_error"` and `errors[i]`; the default `"raise"`
  keeps the earlier behavior. Python error messages carry the JSON
  pointer of the failing schema node.
- Test hooks (`-Dtest_hooks=true`): `blg_test_set_overrides` (per-fill
  search budget, first tier budget, a forced undecided verdict for a frame
  kind or a state key), `blg_test_frame_tag`, `blg_test_state_key`,
  `blg_test_dump_state`.
- `benchmarks/diag_process_history.py`: repeated compiles of one schema in
  fresh contexts of one process with the context memory per round.

- `blg_cert_stats` has a last entry `search_entered`: the number of states
  handed to the budgeted reachability search (diagnostics, additive;
  `BLG_ABI_VERSION` stays 1).
- Build option `-Dtest_hooks=true` exports test-only `blg_test_*` hooks
  (joint numeric verdict with a witness continuation, bounded parser
  search); release builds do not contain them (CI checks with `nm`).
  `tests/numconj_cert_check.py` cross-checks the certificate against an
  independent exact reference (`tests/reference.py`) and the bounded
  search; CI runs it in the `num-cert` job.
- `tests/exact_numbers.py`: exact JSON numbers (`Num`) and a `jsonschema`
  validator over them, for checks that `float` parsing would distort
  (`1e400000`, `7e-900`). `integer` follows the dialect: draft-04 accepts
  only an integer literal, later drafts any integral value. A subschema
  with its own `$schema` keeps exact numbers (`exact_dialects`), where
  plain jsonschema would switch to its stock validator class.
- CI job `text-rules`: no U+2014 in tracked files, no Cyrillic in code
  files.
- `tests/fuzz/test_fuzz_generate_spec_v1.py`: seeded generative test of
  spec-v1. Each run covers random schemas and the ADR-0011 audit tables,
  with K mask-guided passes per schema on three byte-complete
  vocabularies. Every document is checked by `jsonschema` over exact
  numbers, with ECMA-262 `pattern` semantics from `regress`. An engine
  defect or a validator error fails the test, and so does an invalid
  document unless `tests/fuzz/known_deviations_spec_v1.json` lists its
  schema and a deviation listed there explains that document
  (`generation_passes.is_known_deviation`). The default size
  runs in the CI pytest jobs; `BLG_FUZZ_LONG=1` runs 400 schemas x 20
  passes. The pass driver (`tests/generation_passes.py`) and the outcome
  records (`tests/semantic_outcomes.py`) are shared with the coverage
  harness.
- `benchmarks/bench_jsb_coverage.py --generatable`: each compiled schema
  is compiled again per tokenizer and then gets K generation passes
  (default 20), all of them attempted unless `--schema-budget-seconds`
  caps the wall time per schema. The tokenizers are byte-level
  synthetic, Llama 3.1 and gpt2, the last two pinned; the HF tokenizers
  can run on a deterministic schema sample. Every pass is an outcome
  record; a failing `blg_finish` is classified like any other engine
  call (`RESOURCE_LIMIT` and `CANCELLED` are `engine_unavailable`). Every
  compiled schema gets exactly one aggregated outcome per tokenizer, and
  the pass records go to a gzipped sidecar of the report, with the whole
  document of every `invalid_document` and `validator_error` pass.
  `--jobs` runs resumable chunks in parallel and records an engine crash
  as `engine_defect` / `CRASH`; a finished chunk is reused only when its
  identity record (engine library, harness sources, parameters, corpus,
  packages) matches the current run, and chunks of different engines or
  parameters are never merged. `--complete-from` completes an earlier
  report of the same engine and parameters: it runs the passes recorded
  there as `skipped:schema_budget` and replays each invalid pass without
  a document by its seed to keep the document; the replay must reproduce
  the recorded outcome, step and prefix length, runs with 6 times the
  per-pass time limit and, when it ends in `engine_unavailable` again on
  a fresh context, alone in a child process. A completed report can be
  completed again. Report headers now carry the library
  sha256 and mark a dirty worktree; reports and recheck outputs record
  repository-relative paths, never absolute local ones, and the JSON
  reports write non-ASCII characters as escapes.
- `benchmarks/recheck_semantic_counters.py` replays the correctness
  signals of a semantic coverage report on a given library and writes
  one evidence row per case. `benchmarks/triage_semantic.py` turns
  rechecked evidence, the generatable outcomes of several builds and the
  corpus-instance signals of the same runs into the class A-E table and
  the class-D entries of `tests/fuzz/known_deviations_spec_v1.json`,
  each naming its deviation; hand-made decisions live in
  `benchmarks/coverage/spec-v1/triage_manual.json`. An `invalid_document`
  result is class D only when the sidecar holds the document of each of
  its invalid passes and the draft-04 integer spelling explains every
  one; on an earlier build it is class A (fixed) when the final build
  accepts none of its other documents
  (`benchmarks/recheck_invalid_documents.py`). The table also lists each
  schema result that is `generatable` on the first build and not on the
  final one, with the pass counts of both. A manual `exception` entry
  keeps a row unclassified with a recorded decision; `--gate-build final`
  exits 0 when the final build has no unclassified row and every open
  row of an earlier build is such an exception (one on 0.1.0: a pass
  whose document could not be reproduced).
  A published report can be given as a git object
  (`dbc5eeb:benchmarks/...`) once the working tree has replaced it.
- `benchmarks/recheck_cert_residuals.py` reads its valid-rejects file set
  from the pinned `benchmarks/recheck_valid_rejects_targets.json`
  (74 files, 109 rejected valid instances of build `c1cc2cb`) instead of
  the current coverage report, so regenerating the reports does not move
  the target set. Its valid-rejects pin is now 87 accept /
  39 RESOURCE_LIMIT of 126 instances (75 / 50 / 1 before): three
  instances with a bounded integer inside an array or an object now pass
  (ADR-0011), with the value verdicts of `oneOf` ten more pass while two
  (`Github_hard---o84330.json`) end in `RESOURCE_LIMIT`, and the former
  single reject (`Github_medium---o55595.json`) passes with the
  serializer fix.
  The 26-case suite is now 15 accept / 10 `RESOURCE_LIMIT` / 1 reject
  (13 / 12 / 1 before). The saved evidence is in
  `benchmarks/reports/20261002T_audit_fixes/`.

## [0.1.0] - 2026-10-02

Initial public release.

### Added

- Zig core (ReleaseSafe) compiling a JSON Schema subset (canonical-v1 profile)
  and literal sets into a DFA-like grammar; token bitmasks; parser state kept
  across generation steps.
- Strict input policy: unsupported keywords and unproven schema/tokenizer pairs
  are rejected at compile time with a JSON Pointer; the engine never silently
  continues unconstrained.
- Exact completion reachability for incomplete vocabularies on finite languages
  (`src/complete.zig`); dead-end tokens are neither masked nor accepted.
- Modes `lazy` / `adaptive` (LRU mask cache with adaptive admission) /
  `precompute` (experimental warm-up at compile time).
- Hard memory budgets (context, cache, session, schema), cancellation flags,
  per-call work limits, immutable compile-artifact cache (ADR-0004).
- Versioned C ABI (`include/bolorgir.h`, `blg_*`) with opaque handles and
  `blg_status` error codes.
- Python package (CPython abi3, 3.10+) with tokenizer validation by the actual
  backend decoder, Hugging Face Transformers adapter, batched mask filling,
  and a GPU mask-unpacking path.
- Benchmark harness and the control-series acceptance protocol with published
  reports (`benchmarks/README.md`, `benchmarks/reports/`).

- spec-v1 P6b (ADR-0010): `contentEncoding`/`contentMediaType`/
  `contentSchema` are annotations in every dialect (2020-12 Validation
  section 8 - they never assert). RFC 3986 relative-URI resolution with
  an in-document `$id` resource index: a reference's URI part resolves
  against the enclosing base URI (dot-segment removal included) and
  jumps into an in-document resource or the registry snapshot by the
  resolved absolute URI. `$dynamicRef`/`$dynamicAnchor` (2020-12) and
  `$recursiveRef`/`$recursiveAnchor` (2019-09) resolve over the
  compile-time dynamic scope (bookending requirement, outermost scope
  first) on top of the ADR-0008 bounded unrolling. Keywords next to
  `$ref`/`$dynamicRef`/`$recursiveRef` in 2019-09/2020-12 conjoin with
  the reference expansion (sibling rule) instead of refusing.
  `$dynamicRef`/`$recursiveRef` next to unevaluated* stay a documented
  `UNSUPPORTED_FEATURE` refusal with a pointer. Oracle spec-v1:
  1015 → 1118 PASS, 0 mismatches, 0 engine errors; canonical-v1
  bit-for-bit unchanged.

- spec-v1 P6a: `unevaluatedProperties`/`unevaluatedItems` (dialects
  2019-09 and 2020-12) via compile-time scenario synthesis (ADR-0009).
  Every acceptance hypothesis of the enclosing in-place applicators
  (`allOf`, `anyOf`, `oneOf`, `if`/`then`/`else`, `dependentSchemas`,
  local `$ref`) carries the set of keys/indices it evaluates; the
  unevaluated* guard conjoins every scenario. Includes the inert
  reductions, container masking with an outside-language analysis, and
  open-object conjunct merges. Shapes that exceed the parser thread
  budget refuse at compile time with `UNSUPPORTED_FEATURE` and a JSON
  pointer: a combinator nested in a `oneOf`/`anyOf` branch, several
  conjunctive `contains` next to `unevaluatedItems`, an external `$ref`
  next to unevaluated*, and the branch/scenario caps (5 live `anyOf`
  branches, 32 scenarios, 4 `dependentSchemas` entries, 8 guard
  patterns). Oracle spec-v1: 861 → 1015 PASS, 0 mismatches, 0 engine
  errors; canonical-v1 unchanged.

- spec-v1 P5 (ADR-0008): recursive local `$ref` (`#`, `#/...` pointers,
  plain-name fragments, all five dialects; mutual recursion, anchors,
  combinators) by bounded unrolling with a per-path budget of 8
  (`REF_UNROLL_CAP`): the bottom position behaves as `false` and the
  limit is mask-visible. Unproductive cycles compile to
  `UNSATISFIABLE_CONSTRAINT`. `$ref` fragments percent-decode (`%XX`)
  before `~0`/`~1` unescaping. External `$ref` resolves through the
  immutable registry snapshot (ADR-0006 D5); without a registry it stays
  refused. Oracle spec-v1: 833 → 861 PASS, 0 mismatches; canonical-v1
  unchanged.

- spec-v1 P4: the `pattern` keyword as an ECMA-262-subset regex compiled
  to a DFA over Unicode scalar values, evaluated as an unanchored search
  and conjoined with the codepoint length bounds; unblocks regex
  `patternProperties` (exactly one pattern). Numeric
  `minimum`/`maximum`/`exclusiveMinimum`/`exclusiveMaximum` and
  `multipleOf` over `integer`/`number` in exact decimal arithmetic
  (never binary64); a `multipleOf` divisor with more than 10 significant
  decimal digits refuses `UNSUPPORTED_FEATURE`. Oracle spec-v1:
  726 → 751 (pattern) → 833 (numbers) PASS, 0 mismatches.

- spec-v1 P3: `anyOf` general union; `oneOf` with exact subtype-aware
  exclusivity; `allOf` as the exact intersection (comb node, no unsafe
  merges); `if`/`then`/`else` lowering; `not` over the supported
  complement forms (`{type}`, scalar `{enum}`/`{const}`, `{required}`,
  nested `not`, De Morgan over `anyOf`/`allOf`). Oracle spec-v1:
  631 → 726 PASS, 0 mismatches.

- Perf ADR-0007 round 2: normalized cache state for uniform
  string-content positions (all positions of a long string share one
  cache entry). MaskBench TBM p90 609,222 → 481 µs (1266x; the ≤ 1 ms
  target met); cold permissive fill p50 ~5.1 ms; warm cache-hit
  4.33 → 1.90 µs (the ≤ 1.3 µs guard formally not met - structural
  remainder documented in
  `benchmarks/reports/20260921T122823_adr0007_r2/REPORT.md`);
  differential gate PASS.

- `benchmarks/bench_jsb_coverage.py` accepts the env flag
  `BOLORGIR_COVERAGE_PROFILE` to select the compile profile; the spec-v1
  corpus coverage measured with it (release build): repo snapshot
  9,251 / 9,558 (96.8%), MaskBench snapshot 10,794 / 11,306 (95.5%)
  compiled-exact - the ROADMAP ≥ 95% target met on both
  (`benchmarks/coverage/spec-v1/`).

### Fixed

- Release packaging: the sdist is now self-contained. The `sdist` command
  prepares `bolorgir/_lib` before archiving (zig build, or a ready library
  via `BLG_SKIP_ZIG=1`/`BLG_CORE_LIB`), and the C header ships inside the
  package as `bolorgir/include/bolorgir.h` (synced from the repository
  root by `setup.py`, git-ignored). The release workflow's
  `BLG_SKIP_ZIG=1 python -m build --no-isolation` previously failed:
  first with `libbolorgir.so* not found` (the git-ignored `_lib` binary
  never entered the archive), then with `bolorgir.h: No such file or
  directory` (the extension looked for the header outside the unpacked
  sdist). A wheel built from the unpacked sdist now needs neither zig
  nor the repository checkout (`python/setup.py`, `python/MANIFEST.in`,
  `.gitignore`).
- Release packaging: a direct wheel build (`pip wheel ./python`) now also
  ships the core library. setuptools runs `build_py` before `build_ext`,
  so the `_lib` payload used to be copied before the zig step populated
  it; the resulting wheel installed but failed at import with
  `libbolorgir.so.0: cannot open shared object file`. `setup.py` now
  prepares `_lib` in `build_py` as well (once per setup invocation)
  (`python/setup.py`).
- Reachability strictness wave (2026-09-29, follow-up review of the audit
  resolution):
  - Strict ADR-0005 Decision 3: a state the bounded reachability search
    cannot settle is UNKNOWN, not alive - `fill_mask` now fails and
    `blg_accept_token` refuses the token with `RESOURCE_LIMIT` instead of
    silently admitting a token that may dead-end (`src/mask.zig`
    `setNodeTokens`/`fillMaskBrute`, `src/complete.zig` `stateAlive`,
    `src/c_api.zig` accept path). Previously a budget-exhausted search
    kept the token admitted, which let dead bytes through (e.g.
    `oneOf` over `^(ab|c)$`/`^(ab|d)$` admitted `a` after the prefix `"`).
    Regression pinned across lazy/adaptive x fast-path on/off
    (`tests/test_spec_v1_r1_residual.py`).
  - `oneOf` vote-link doom now links branch threads by exact residual
    equality, not only frame identity (`src/complete.zig`
    `linkedThreads`): literal frames by remaining suffix, `lit_trie`
    frames by subtrie language equality (structural), `str_pat` frames
    by equal codepoint windows plus right-language equality of the DFA
    states (bounded greatest-fixpoint check over the reachable state
    pairs, capped at `RESIDUAL_EQ_CAP`). Overlapping oneOf branches such
    as `^(ab|c)$`/`^(ab|d)$` after `"a` are certified dead in closed
    form instead of draining the per-fill search budget.
- Audit wave (2026-09-22 audit findings, fixes verified 2026-09-26 on the
  `final4` build; regressions pinned in `tests/test_spec_v1_r1_residual.py`,
  `tests/test_spec_v1_r2_r3.py`, `tests/test_audit_regressions.py` and
  `tests/test_maskbench_serializer_hook.py`):
  - R1: token masks no longer offer dead-end tokens. A residual-language
    reachability analysis (ADR-0005; new `src/pattern.zig`) filters tokens
    from which no valid document can be completed (e.g. `^a$` with
    `maxLength: 1` no longer allows starting with `b`; the `{ab,xy}` ∩
    `{ac,xy}` intersection keeps only completable continuations). On
    search-budget exhaustion the strict D3 rule applies (see the
    2026-09-29 wave above: the call fails with `RESOURCE_LIMIT` rather
    than admitting an unproven token); budgets `FILL_SEARCH_BUDGET=512`,
    `MAX_DEPTH_REACH=512`, `Cache.max_states=256`
    (docs/semantics-spec-v1.md §4.4/4.5).
  - The reachability rollout no longer fails with `RESOURCE_LIMIT`
    storms at feed time (memo `in_progress` leak fixed, per-fill search
    budget, parser state pool): oracle ENGINE_ERROR rows went from
    63-145 per dialect to 0 on all five dialects.
  - Parser: duplicate threads spawned by the AnyJSON integer/number
    choice are merged after every byte (`dedupThreads`, content-aware
    chunk comparison) - an array of integers under an unconstrained item
    schema no longer hits the 64-thread budget at the fifth element;
    `uniqueItems` verdicts preserved.
  - R2: the static enum/const validator respects adjacent constraints
    (`{"const":[1,1],"uniqueItems":true}` and `required` next to `const`
    objects are enforced at compile time).
  - R3: `oneOf`/`allOf` branch value filters compare object values
    key-order invariantly (branches differing only in key order behave
    per JSON Schema value semantics).
  - R4: the MaskBench serializer never re-parses string instances -
    `"1"` stays the string `"1"`, arbitrary text like `"hello"`
    serializes instead of being reported serialization-incompatible
    (`python/bolorgir/serializer.py` `serialize_value`; full-corpus
    `blg-ser` re-measure: 0 serialization incompat, outcome counts
    identical to the published serializer-off run).
  - Mask cache normal form (ADR-0007 D2+): the uniform string-content
    counter is clamped at `min_len` instead of zeroed, so quote-led
    close-and-continue tokens (e.g. the merged `","`) are no longer
    wrongly pruned from the mask after `minLength`-bounded string values
    (3 MaskBench files; mask differential vs the previous build: tokens
    only gained, none lost, dead-end exclusions bit-identical).
  - Engine no longer crashes on long-running hosts: the root allocator
    was switched from `page_allocator` to `smp_allocator`
    (`src/c_api.zig` `root_allocator`). The per-allocation mmap/munmap
    of the page allocator drove the process VMA count to
    `vm.max_map_count` (65530) over ~12k schema compilations and
    generations, after which a `munmap` splitting a merged VMA failed
    with ENOMEM and `std.posix.munmap` panicked on its `unreachable`
    branch (`blg_session_destroy` -> `parser.Side.deinit`). Small
    allocations are now packed into per-thread slabs (O(slabs) VMAs
    instead of O(allocations)); verified on the previously crashing
    full-corpus coverage run, and warm-path metrics slightly improved
    (`benchmarks/reports/20260927T115548_adr0007_fix1/REPORT.md`).
  - Completion memo no longer leaks the duplicated key when a state is
    memoized twice: `memoPut` (`src/complete.zig`) used dupe-then-`put`,
    and `put` keeps the existing key on a collision, so the fresh copy
    was lost (4466 bytes per fill_mask cycle, charged to the context
    TEMP budget - the growth behind the "Context retains generation
    memory" finding). Fixed via `getOrPut` with an update-in-place on
    existing keys; the per-cycle TEMP accounting is now flat under the
    audit workload.

### Notes

- Schema profiles: `canonical-v1` (default, frozen) and `spec-v1`
  (alpha). canonical-v1 rejects `anyOf`/`oneOf`/`allOf`, `pattern`,
  `format`, numeric bounds and boolean schemas at compile time; spec-v1
  implements them as listed above, with the known limitations in
  `docs/supported_features.md` (runtime `RESOURCE_LIMIT` residual, compact
  JSON only, fixed key order). No standalone regex or arbitrary CFG.
  Linux x86_64 (glibc), CPython 3.10-3.13 (abi3).
- The project was previously developed under the working name `zig-constraints`;
  commit ids changed during the rename - see `benchmarks/reports/README.md`.
