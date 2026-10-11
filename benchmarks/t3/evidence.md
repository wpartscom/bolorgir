# T3 evidence summary (2026-10-06)

Versioned companion to the audit-fix spec rev 2.5, tasks R1-R6. Raw run
logs and work directories stay under `benchmarks/local/t3/` (ignored);
this directory carries the selections, tools and the compact evidence
referenced by `docs/RUN_HISTORY.md`.

## Builds

| label | sha256 (first 16 hex) | role |
|---|---|---|
| dbc5eeb | 8ff970d46cce8b47 | baseline of the accepted T2b/T2c line |
| 7345490f | 7345490f7ddf5710 | accepted build of the T2b K=20 line |
| d4e70303 | d4e703033df6ab93 | T3 diagnostic candidate |
| 50cbd80e | 50cbd80e874eac5d | current series after the R2a fix |
| f5726dd6 | f5726dd69e9520aa | the R7 fix (diagnostic build, superseded by R9) |
| ea54be1b | ea54be1b497feefa | R9 release-line candidate: c288703 plus `de7/release-line-c.patch`, stripped release recipe, two identical builds; not committed |
| 6e62e0d1 | 6e62e0d1526c6e53 | R10 release-line candidate: c288703 plus `de7/release-line-d.patch` (maxProperties count certificate), stripped release recipe, two identical builds; not committed |

Local copies: `benchmarks/local/libs/dbc5eeb/`, `benchmarks/local/libs/final/`
(7345490f), `benchmarks/local/t3/lib-d4e70303/`; the current library is
built with `zig build -Drelease=true`.

## R1: CI and run accounting

- `.github/workflows/ci.yml`, job `hf-integration`: builds the test-hook
  library (`zig build -Drelease=true -Dcpu=baseline -Dtest_hooks=true
  --prefix zig-out/test-hooks`), exports `BLG_TEST_HOOKS_LIB` to the pytest
  steps and runs `tests/test_probe.py::test_probe_found_and_inconclusive_with_hooks`
  and `python/tests/test_hf_adapters.py::test_fail_row_with_unknown_forced_by_the_test_hook`
  with a junit `skipped == 0` assertion. The release library stays
  hook-free (checked in the `num-cert` job).
- `benchmarks/t3/full_tests.sh` (tracked) fixes the rule 9 accounting: the
  first failing command exits with its status and no success line is
  printed; the local wrapper `benchmarks/local/t3/full_tests.sh` points it
  at the torch venv and the local toolchain.
- Verified locally 2026-10-06: injected failure gives exit 1 with
  `FAILED: fmt` and no `done`; both hook tests run (`2 passed`,
  `skipped=0`). The CI run on the published commit is checked in T16.

## R2a: `Github_medium---o32437.json` RESOURCE_LIMIT (fixed)

- Diagnostic on d4e70303 (Llama, 20 passes): 14 completed_valid, 5
  `RESOURCE_LIMIT`, 1 time_limit; the failing states are mid-key inside
  the object that carries `dependencies` (draft-04
  `{"isTest": ["results", "tiebreak", "counts"]}`); counters
  `fail_search 1, openobj_track 1, openobj_deps 1, openobj_gate 1`.
- Cause: the open-object certificate treated every node with dependencies
  as undecided and deferred to the bounded search; on the Llama vocabulary
  the search exceeds its tier caps (its result is vocabulary dependent; a
  huge tier budget resolves the same state after 14.9 s), and a key that
  skips a still-missing dependency name was decided wrongly.
- Fix (`src/complete.zig`): dependentRequired entries are analyzed by the
  certificate (`depObligations`, `depNameCert`, `mergeDeclaredWitness`).
  A missing declared name behind the skip is unreachable (the parser
  refuses `i < next_idx` for the rest of the frame), a witness counts only
  when every missing name stays dispatchable after it.
- Verification on 50cbd80e: the mask at `{"isTest":true,` is status 0 in
  0.01 s; the recorded 20 passes give 19 completed_valid + 1 time_limit
  (`generatable`; evidence `evidence/r2_o32437_kb1154_50cbd80e.jsonl`).
- Regression: `tests/test_residual_certificates.py::test_dependent_required_keys_follow_the_dispatch_order`
  (fails on d4e70303 through `search_entered 1 != 0`, passes on 50cbd80e;
  the whole file: 22 passed). Zig suite 283/283, `zig fmt` clean.

## R2b: `Kubernetes---kb_1154_Normalized.json` time limit (not build-attributable)

- Accepted line 7345490f (Llama, sidecar `coverage_maskbench.passes.json.gz`):
  pass 1 completed_valid, passes 2-20 already `incomplete:time_limit`.
- Targeted repeat today (single job, 10 s per pass) reaches the same step
  16 at `{"conversionRevi` and times out on every build tested:
  dbc5eeb 17.6 s, 7345490f 17.5 s, d4e70303 14.1 s, 50cbd80e 14.1 s.
- A sampled slow state (`evidence/kb1154_slow_prefix.hex`, mid-key of a
  large open object) costs about 1 s per fill with no search and no
  failure counters on all builds: dbc5eeb 1.28 s, 7345490f 1.24 s,
  50cbd80e 0.98 s (`tools/time_mask.py`). The cost is pre-existing.
- Disposal proposal: accept as an environment-timing residual of an
  expensive path (D4 entry, section "D4 proposals").

## R3: five invalid schemas (Llama, d4e70303 library)

Recovered with `tools/save_documents.py` on the frozen d4e70303 library;
21 of the 22 diagnostic invalid passes reproduced with identical
outcome/stage/code/step/prefix_length (evidence
`evidence/r3_d4e70303_llama.jsonl`):

| schema | invalid passes | record match | classification |
|---|---:|---|---|
| Github_easy---o36463.json | 16 | 16 of 16 | draft-04 integer spelling (D) |
| Github_hard---o56003.json | 2 | 2 of 2 | draft-04 integer spelling (D) |
| Github_medium---o56014.json | 1 | 1 of 1 | draft-04 integer spelling (D) |
| Github_medium---o56020.json | 2 | 2 of 2 | draft-04 integer spelling (D) |
| Github_hard---o17700.json | 1 (diagnostic) | not reproduced | open |

- `tools/classify_docs.py` decodes every recovered document and the
  documented `draft04_integer_spelling` check (`generation_passes.is_known_deviation`)
  confirms it explains each one; no A/B/E case remains.
- o17700: the diagnostic recorded 19 time_limit passes and one invalid
  pass; the targeted repeat on the same frozen library produced two
  invalid passes at different steps (5 and 11), both draft-04 documents,
  and did not reproduce the recorded pass. Per the spec the original
  record stays explicitly open; the documents obtained are not claimed
  as the recorded one.
- The same five schemas on 50cbd80e produce the same invalid passes and
  steps, so the R2a fix does not change them.

## R4: four class C residuals (byte tokenizer, 50cbd80e)

Single-job repeats with recorded seeds, 10 s per pass; evidence
`evidence/r4_repo_byte_50cbd80e.jsonl` and
`evidence/r4_maskbench_byte_50cbd80e.jsonl`. Profiles equal the d4e70303
diagnostic (c26 files identical, see below).

| schema | dbc5eeb | 7345490f | 50cbd80e | cause | proposal |
|---|---|---|---|---|---|
| tye-schema (repo) | cv 1, tl 19 | cv 2, tl 16, eu 2 | cv 10, tl 2, sl 1, eu 7 | `oneOf` of eight open-object branches; a vote needs a key witness that fails every rival, the candidate has value witnesses only | documented limitation, D4; future group-certificate work |
| tye-schema (maskbench) | cv 3, tl 17 | cv 3, tl 15, eu 2 | cv 10, tl 2, sl 1, eu 7 | same | same |
| Github_hard---o32934.json | cv 19, tl 1 | cv 10, sl 1, eu 9 | cv 17, sl 1, eu 2 | key start in an array element under five `if`/`then` groups with closed `then` branches; `comb_group` plus dependency-gated object states stay undecided | documented limitation, D4; future group-certificate work |
| JsonSchemaStore---cryproj.53.schema.json | cv 1, sl 15, tl 4 | cv 1, tl 12, sl 6, eu 1 | cv 1, sl 18, eu 1 | documented limit: in a `uniqueItems` element a number may not exceed |exp| 400 (semantics-spec-v1 section 1); one pass typed `744345424e+97` | documented limitation, D4 |

The tye-schema RESOURCE_LIMIT count grows from 2 to 7 against 7345490f
while completed_valid grows from 2 to 10: the passes now get past states
that previously ended in the time limit and reach the same undecided
`oneOf` states. The change is explained, not hidden.

## c26 check (26 class C transitions)

`tools/compare_replays.py` over `benchmarks/local/t3/out/d4e70303/c26`
and `.../50cbd80e/c26` (run with `run_c26.sh`, 10 s per pass, 6 jobs):
0 of 26 items changed outcome or counts. The recovered 22 are unchanged;
the four above remain.

## 535-sample check (Llama, fixed 1-in-20 sample)

Selection: `selections/mb_llama_sample.list` (535 compiled schemas of the
published report). Run on 50cbd80e with 6 jobs, 10 s per pass; comparison
against the d4e70303 diagnostic (`benchmarks/local/t3/out/d4e70303/mb_llama_all.jsonl`):

- Totals on 50cbd80e: 375 generatable, 151 incomplete, 5 invalid_document,
  4 engine_unavailable (d4e70303: 373 / 152 / 5 / 5). The
  `engine_unavailable` share is 4/535 = 0.75%, inside the 1% goal.
- Class changes against d4e70303: `Github_medium---o32437.json` from
  engine_unavailable to generatable (14 to 19 completed_valid, the R2a
  fix) and `Glaiveai2K---calculate_area_c686757a.json` from incomplete to
  generatable (timing-limited before, 1 completed_valid now). 27 further
  items changed only their completed_valid/time_limit counts inside the
  same outcome class.
- Against the recorded 7345490f outcomes: 42 engine_unavailable to
  generatable, 10 engine_unavailable to incomplete, 9 incomplete to
  generatable, 324 generatable to generatable, 140 incomplete to
  incomplete, 4 invalid to invalid, 4 engine_unavailable to
  engine_unavailable, 1 engine_unavailable to invalid
  (`Github_hard---o17700.json`), 1 generatable to incomplete
  (`Kubernetes---kb_1154_Normalized.json`, R2b). No
  generatable-to-engine_unavailable transition and no new engine_defect
  row: the R2a certificate removes the only new regression of the
  d4e70303 diagnostic without adding others.

## ADR-0012 review notes (implementation side, 2026-10-06)

Checked against the implementation and tests before the decision on the
`proposed` status:

- Outcomes: `found` fails the compile with `RESOURCE_LIMIT`, the witness
  prefix in the message and the subschema pointer
  (`src/c_api.zig::probeHandle`, `probeMessage`); `inconclusive` succeeds
  and fails under `BLG_COMPILE_PROBE_STRICT`; `proven` is returned only
  by the search-exhausted path. Covered by `tests/test_probe.py`
  (proven on a finite boolean object; found/inconclusive with hooks via
  `tests/probe_hooks_check.py`) and by `test_path` neighbours.
- Residual schemas: `tests/test_probe.py::test_residual_schemas_are_never_proven`
  compiles every schema of `engine_error_residual_spec_v1.json` and
  accepts only `found` or `inconclusive`; it skips when the oracle suite
  snapshot is absent (the snapshot is not versioned), so the check runs
  locally and with the fetched snapshot only.
- Caching: the verdict is stored on the grammar with the probe budgets it
  was obtained under; a request whose context budgets differ re-runs the
  probe (`std.meta.eql(h.probe_limits, ctx.probe_limits)`), and
  `blg_stats.probe_runs` counts the searches. Budgets are fixed per
  context, so the differing-budget branch is defensive; the same-budget
  reuse and the `probe_runs` counter are test-covered.
- Budgets: `probe_max_states` defaults to 256 and the probe work budget
  to 16M (`DEFAULT_PROBE_MAX_STATES`, `DEFAULT_PROBE_MAX_WORK`); probe
  memory is charged to the context temp category.

No contradiction with ADR-0012 was found; the status change stays with
the owner/reviewer decision.

## R7: seven DEAD_END schemas (byte, 50cbd80e; engine defect, fixed)

The repository phase of the T3 acceptance of 50cbd80e
(`benchmarks/local/t3/review-50cbd80e-repo`) left seven unclassified T2c
rows, `engine_defect` / `fill_mask` / `BLG_ERR_DEAD_END` on the byte
tokenizer: 52 of their 140 passes. Every one ends inside an object key,
before its closing quote: the mask admitted a key that dispatch then
refused.

| schema | DEAD_END passes | class | the key that dead-ends |
|---|---|---|---|
| Github_easy/o81581.json | 17 | A | a second `line1`..`line3` (`^line[1-3]$`, closed object) |
| Github_medium/o5988.json | 12 | A | a second `$schema` (`^\$schema$`) |
| Github_easy/o83697.json | 8 | A | a second `comment1`..`comment9` (`^comment[1-9]$`) |
| Github_easy/o81526.json | 5 | B1 | `favoriteTopic` (requires `tags`) before the required `firstName` |
| Github_hard/o61772.json | 5 | B1 | `default_locale` (requires `locales`) before the required `description` |
| Github_easy/o81583.json | 3 | B2 | `favoriteTopic` after `tags` was skipped in schema order |
| Github_hard/o57433.json | 2 | B2 | `memswap_limit` before `mem_limit`, which is declared earlier |

Replay with the harness of the candidate tree on the frozen library and
the recorded seeds: 140 of 140 passes as recorded, and every DEAD_END
prefix dead-ends again from the prefix alone in a fresh session
(`evidence/r7_dead_ends_50cbd80e.jsonl`). Minimal reproducers (spec-v1,
byte tokenizer): `{"k1":"","k1` under
`{"patternProperties":{"^k[12]$":{}},"additionalProperties":false}`;
`{"a` under draft-04 `properties` a, r, b with `required` [r] and
`dependencies` {a: [b]}; `{"a` with b declared before a.

Cause, all three in the exact open-object certificates of the completion
filter (`src/complete.zig`, ADR-0005), not in the parser:

- A: a `patternProperties` key counted as a fresh completion whenever the
  pattern could still match ("the matching continuations are infinite").
  A finite pattern language runs out once its keys are seen, and dispatch
  refuses a duplicate (`certOpenObjKeyStr`, `certOpenObjKeyMidCp`,
  `freshUndeclaredPath`).
- B1: `depNameCert` declared a missing dependency name unreachable when a
  pending required prop came before it (the dispatch window of the next
  key used for eventual reachability). The parser
  (`depsResidual`) only kills a name behind the skip.
- B2: `mergeDeclaredWitness` checked the dependencies already fired, not
  those the spelled key fires by its own dispatch.

B1 and B2 came with the dependentRequired certificate of R2a (in
c288703); before it these nodes went to the bounded search. On the lines
the seven schemas end differently (byte, `coverage_repo.json` of
7345490f and dbc5eeb): o81581, o81583 and o83697 generatable with 1
completed and 19 step-limited passes, o81526, o61772 and o5988
`engine_unavailable` in 20 of 20 passes, o57433 in 12. Class A predates
the series: dbc5eeb already has `engine_defect` passes on o81581 (1) and
o83697 (2).

Fix (release line, patch `benchmarks/local/t3/de7/open-object-dead-ends.patch`
against c288703, the same `src/complete.zig` change applies to the wave-2
tree): the matching completions of a key prefix are counted exactly from
the DFA state (saturated, budgeted) and compared with the taken keys -
seen keys, declared names and undeclared dependency triggers whose
dispatch fails (one raw spelling per key under the canonical escapes);
the non-matching completions are infinite when some extension leaves the
pattern for good; `depNameCert` kills only a name behind the skip (an
order-free node never skips); `mergeDeclaredWitness` also checks the
entries its own name fires.

Check by an independent oracle (`evidence/r7_walks.json`): 200 random
walks per schema over the mask of S, compared at every step with S plus
`maxProperties: 1000000` on every object schema - the same language,
settled by the bounded search instead of the certificates. On 50cbd80e
13 of 14 schemas (the 7 above and 6 minimal variants) show findings: a
DEAD_END or a byte the certificate admits and the search refuses, never
the reverse. On the fixed library 2,800 walks show none: no mismatch, no
dead end, every finished document valid; the same on the prepared
candidate build with the release flags (ac915c9d, see the handoff).
Regression tests:
`tests/test_open_object_dead_ends.py` and the unit test "complete:
pattern key completions are counted exactly".

Superseded in part by R9: the fallback rule above ("infinite when some
extension leaves the pattern for good", otherwise none) gave a false
dead end, the dependency checks were one step deep, and the
`maxProperties` variant is a consistency check, not an independent
oracle. The release-line patch is now `de7/release-line-c.patch`.

## R8: five more transitions without a D4 entry (50cbd80e, repo)

`review-50cbd80e-repo` lists 35 transition rows (27 results); eight
results had no D4 entry: the three DEAD_END results of R7 that are
transitions (o81581, o81583, o83697) and the five below. Replays with the
harness of the candidate tree and the recorded seeds, one job
(`evidence/r8_d4_replays.jsonl`); budget is the pass time limit (the
acceptance run: 10 s). "fix" is the release-line library with the R7 fix.

| result | 50cbd80e (acceptance) | replay | lines | cause | proposal |
|---|---|---|---|---|---|
| o31351 (byte) | cv 2, sl 12, tl 5, eu 1 | pass 8: RESOURCE_LIMIT at step 6225 in 2.5 s on 50cbd80e and the fix, `comb_group` 3 | dbc5eeb, 7345490f: pass 8 hits the 10 s limit near step 1600; with 120 s the same state at step 6225 after 40 s | `oneOf` of two branches that differ only by a required key (`bids` / `config_id`) on an open object: the vote needs a key witness against the rival, the certificate has value witnesses only | documented limitation, as D4-3 |
| bigquery-table (byte) | cv 16, tl 3, eu 1 | pass 19: RESOURCE_LIMIT at step 1582 in 5.6 s on 50cbd80e and the fix, `comb_group` 3 | pass 19 hits the 10 s limit near step 168; with 120 s the same state at step 1582 after 81-85 s | `if`/`then` on the recursive field definition (STRUCT/RECORD requires `fields`), undecided at the value of `"type"` | documented limitation, as D4-4 |
| cryproj.dev.schema (byte) | cv 1, sl 18, eu 1 | pass 10: RESOURCE_LIMIT at step 5725 in 3.4 s, `fail_search` | 7345490f: the same state in 4.7 s; dbc5eeb: another path to the step limit | `256E165` in a `uniqueItems` element; one more exponent digit exceeds the documented |exp| 400 | documented limitation, as D4-5 |
| o41283 (gpt2) | tl 20 | pass 1 completes at step 59 in 9.3-9.9 s on every build (0.16 s per step) | 7345490f completed pass 1 in its run | pass 1 sits at the edge of the 10 s budget on every build; the outcome follows machine load | environment-timing residual, as D4-2 |
| kb_1088_Normalized (llama-3.1) | tl 20 | pass 1 completes at step 21 in 8.1-9.2 s on every build (0.4 s per step) | 7345490f completed pass 1 in its run | the kb_1154 family (R2b): a large normalized object at about 0.4 s per fill on every build | environment-timing residual, as D4-2 |

In the two RESOURCE_LIMIT rows with `comb_group` the state is
pre-existing: the line libraries reach it at the same step once the pass
has the time; 50cbd80e is faster and reaches it within 10 s (the
tye-schema pattern of R4). The R7 fix changes none of the five (same
pass, step and state, or the same time).

## R9: review of the R7 fix - fallback keys, dependency fates, checks

The review of the R7 fix (2026-10-08) confirmed the analysis of the seven
dead ends and found the candidate not ready: a false dead end in the
fallback count, an oracle that shares code with what it checks,
`cand_checks.sh` hiding exit codes, and a release recipe pinned to the
unpatched sources. Reproducers on the frozen libraries 50cbd80e and
f5726dd6 (the R7 fix) and on the R9 candidate ea54be1b
(`evidence/r9_reproducers.txt`: `de7/probe_c.py` masks and accepts every
byte of the prefix, `de7/probe_end.py` accepts it unmasked and masks its
end; spec-v1, byte tokenizer; I = `{"type":"integer"}`):

| case | schema | prefix | 50cbd80e | R7 fix | R9 |
|---|---|---|---|---|---|
| the review's counterexample | `""`: I declared, `^([\s\S]{2,}\|[^a])$`: false, fallback I | `{"":0` (unmasked) | `,` masked (`.Ee}`), accept refuses it | same | `,.Ee}`, then only `a` |
| the same, masked from the start | as above | `{"` | RESOURCE_LIMIT | RESOURCE_LIMIT | `"a`; `b` masked |
| multi-byte remnant | `[^é]` for `[^a]` | `{"":0,"` | `,` refused | same | only `é` (lead byte C3) |
| nothing left | `""` and `a` both declared | `{"":0,"a":0` | RESOURCE_LIMIT at `{"` | same | `.Ee}` (no `,`) |
| literal pattern | `""`: I declared, `abc`: false, fallback I | `{"":0` | `.Ee}` (`,` masked) | same | `,.Ee}` |
| two-step chain | X, y, T, D1, closed, T -> D1 -> X | `{"` | `DTXy` | `TXy`; `{"y":"","T` DEAD_END | `Xy`; after `{"y":""` only `}` |
| cycle | a, b, closed, a <-> b | `{"` | `ab` | `a` | `a` |
| own dependency, mid code point | b, x, é1, z, closed, é1 -> b | `{"x":"","` | `z` and the lead byte of `é` | the same, then DEAD_END after it | `z` |
| required, own dependency skipped | b, x, r, closed, required r, r -> b | `{"` | `brx` | `bx`; `{"x` DEAD_END | `b` |
| the same, open | b, x, r, required r, r -> b | `{"x` | `"` admitted, then fresh keys forever | same | `"` masked |
| undeclared required, own dependency skipped | b, x, open, required u, u -> b | `{"x` | `"` admitted, then fresh keys forever | same | `"` masked |

C1, fallback keys (`freshUndeclaredPath`, `certOpenObjKeyStr`,
`certOpenObjKeyMidCp`). The R7 fix read "no extension leaves the
pattern for good" as "no key on the fallback side": a finite set of
non-matching keys was treated as empty, and a count without memory as
proof of absence. In the review's counterexample the pattern leaves
exactly `""` and `a` to the fallback; after `{"":0` the key `a`
continues the document (`{"":0,"a":0}` is valid), yet `,` was masked. A
literal pattern (`abc`) went the same way. Now both dispatch sides are
counted exactly from the DFA (`dfaKeyCount`: the paths to a counted
state over live transitions, the miss side infinite on a gap, a
transition out of the live states or a cycle, Kahn's path sum otherwise;
code-point windows for a key cut inside a UTF-8 sequence) and compared
with the taken keys: seen keys, declared names and the dependency
triggers whose dispatch is dead (undecided ones only "maybe"). More
completions than possibly-taken keys is a fresh key, no more than the
surely-taken ones is none, anything between - and a count without
memory - is unknown, never "no key".

C2, dependency fates (`Doom`, `mergeDeclaredWitness`,
`certOpenObjInner`, `grammar.needsReachability`). The R7 fix checked a
dependency name one step deep: a key whose dependency's own dependency
was already skipped (T -> D1 -> X, X behind the skip) passed as alive
and dead-ended at dispatch; a multi-byte declared name typed mid code
point skipped the check of its own dependency; required names (declared
and undeclared) whose own dependency was skipped were not checked after
dispatch. And an open object with only dependentRequired entries never
reached the completion filter (`needsReachability` looked at patterns
and property names): a closed object then admitted keys whose
obligations could not be met, an open one typed fresh keys forever. Now
a fixpoint marks a dependency name as doomed when it is banned, a
declared name behind the skip or with an empty value, an undeclared name
the object can no longer take (name constraints, `maxProperties`
reached, an empty value), or fired by a doomed trigger (a strict variant
for dead verdicts, a loose one - undecodable or undecided names - for
unknown); a declared key is a witness only when nothing it fires
and nothing still owed after its dispatch is doomed, and the open-object
certificate applies the same to the required names. `needsReachability`
routes an open object with a dependentRequired entry to the filter.

Not fixed - maxProperties on open objects: once `maxProperties` keys
are seen, `,` is still admitted and the next key can never close (false
alive: typing goes on without a dead end, the document is never valid);
`{"type":"object","maxProperties":1}` after `{"x":1,"y` masks `"` on
50cbd80e, the R7 fix and R9 alike, also with required names. Routing
such objects to the filter gives RESOURCE_LIMIT at `{"x":1,"`: the
in-flight key needs an exact count certificate. An owner decision
(scope of the release line). Superseded by R10: fixed in the release
line; the RESOURCE_LIMIT needs owed names (`required`, dependencies),
the example above becomes exact by the routing alone.

Checks. The `maxProperties: 1000000` variant of R7 settles alive
verdicts by the bounded search, but still asks the certificates for
dead ones, so it is a consistency check, not an independent one.
`de7/diff_walk.py` now reports every mask status of S other than OK, a
status of the gated variant other than OK and RESOURCE_LIMIT
(RESOURCE_LIMIT and lost steps are counted, not compared), an accept
refusal on either side, and counts unverified steps. The independent
check is `tests/completion_reference.py`: no engine code; a bounded,
schema-directed set of completions judged by the jsonschema validator
over exact numbers plus the spec-v1 order, unique-key and
canonical-string rules. A witness for a byte the mask refuses is a false
dead end; no witness proves nothing and counts as unverified. Its
coverage needed a directed close (exactly the members an object still
owes), string closings filtered by the value's string keywords,
exponent closings of integral numbers and an alphabet collected from the
schema; without them most steps of the address schema stayed
unverified. In the regression module every walk schema runs six
reference walks (`verified >= 3 * unverified`, at least 30 verified);
`de7/diff_walk.py --reference` runs 30 walks of up to 200 steps on each
of the 17 small schemas (the minimal reproducers of R7 and R9).

Release recipe: `build.zig` gains `-Dstrip=true` (no debug information;
the R7 builds differed only in absolute paths inside it). The candidate
is built twice from `git archive c288703` plus the patch in different
directories with their own caches,
`-Drelease=true -Dstrip=true -Dtarget=x86_64-linux-gnu.2.28 -Dcpu=baseline`,
and the two libraries compared by `cmp` (`de7/cand_build.sh`); acceptance
runs on those bytes. `release.yml` `CORE_FLAGS` and
`docs/release/v0.2.0-composition.{json,md}` carry the same flags;
`release_gate.py sources` refuses any changed pinned file and any extra
file in the core paths; at acceptance time it compared against the base
revision and the `core.patched` pins, and the composition now carries the
same per-file pins as `core.manifest` (no base revision needed).

Short checks of the release line (c288703 plus `de7/release-line-c.patch`,
sha256 `0636415544fea855c465896b6017a08177db78d11e40a5bc6d77813943d78352`;
logs in `benchmarks/local/t3/de7/logs`):

- `rel_tests.sh relc` (the patched c288703 tree): zig tests, fmt, release
  build, package identity; the regression module 229 passed; the full
  pytest 1290 passed, 18 skipped (the skips of R7: 15 oracle
  prerequisites, `test_probe` twice and `num_conj_cert` without the
  test-hooks core).
- `w2_tests.sh w2c` (the wave-2 tree with the same core change): 383
  targeted tests passed, one known warning.
- `cand_build.sh c`: the two builds are identical,
  `ea54be1b497feefa9ea4c001d8d028e7b3adf21c672e49f69d42c78421630d63`,
  stripped; the git blob ids of the three patched files equal the pins.
- `cand_checks.sh c` on those bytes: the regression module 229 passed;
  gated walks on 24 schemas, 200 each: 863,268 steps, 863,256 compared,
  12 unsettled (RESOURCE_LIMIT of the gated variant on the two open
  own-dependency schemas), no finding (`evidence/r9_walks_gated.jsonl`);
  reference walks on 17 schemas, 30 each: 39,989 steps; 80,117 witness
  searches for a byte the mask refused found none (1,074 of them ran out
  of budget, unproven); 39,538 of the 39,591 accepted bytes have a
  witness, 53 have none within the budget (13, 13, 12 and 8 on the four
  fallback schemas, 5 and 2 on the two open own-dependency schemas);
  every finished document valid and in the language, every walk at the
  step cap with a witness for its prefix; no finding
  (`evidence/r9_walks_reference.jsonl`). The first attempt of this step
  ended with the agent session after its first schema (no error was
  recorded; its console output was lost with `/tmp`); the step was rerun
  alone with the same command: exit 0 in 11 minutes, 2.1 GB peak
  (`logs/c-walks-reference.log`, `.mem.log`; the cut log kept as
  `.interrupted.log`).

## R10: maxProperties in the release line, isolation of the R6 runner

The third review (2026-10-08) accepted R9 and blocked the candidate on
two points: the maxProperties false alive must be fixed in the release
line (ADR-0005: completable after every admitted token; R6: no open
defects), with masks checked from the document start and no
RESOURCE_LIMIT standing in for a mask; and the R6 runner must not use
the shared `.venv` or links to `benchmarks/external`, must pin the
corpora and check every input by content on resume, with harness checks
for changed inputs and an interrupted preparation. Work copy
`benchmarks/local/t3/de7/rel-d` (c288703 plus the new patch; `de7/rel`
stays as the R9 copy); main-tree core sources unchanged.

### maxProperties (fixed)

Reproducers (`de7/probe_d.sh`: every fill_mask from the document start
while the prefix is fed byte by byte, then the mask at its end;
`evidence/r10_reproducers.txt`). Integer values make the grammar run the
filter already (spec-v1 integers are value integers, a deferred
verdict), so the table shows both earlier failure modes:

| schema | prefix | 50cbd80e, ea54be1b | 6e62e0d1 (D) |
|---|---|---|---|
| `maxProperties: 1` (the review's example) | `{"x":1` | `,.0123456789Ee}`; after the comma `"` and any key, never closable | `.0123456789Ee}` |
| `maxProperties: 0` | `{` | `"}` | `}` |
| `maxProperties: 3`, integer values | `{"a":1,"b":2,"c":3` | `.0123456789Ee}` (filtered) | same |
| `maxProperties: 1`, `required: [a]`, integer values | `{"` | RESOURCE_LIMIT | `a`; `{"a":1` -> `.0123456789Ee}` |
| `maxProperties: 2`, `required: [a, b]` | `{"`, `{"b":1,"` | RESOURCE_LIMIT | `ab`, then `a` |
| `maxProperties: 2`, declared a, b, c, `required: [c]` | `{"a":1,"` | RESOURCE_LIMIT | `c` |
| `maxProperties: 2`, `required: [r]`, `r -> s` | `{"`, `{"s":1,"` | RESOURCE_LIMIT | `rs`, then `r` |
| `maxProperties: 2`, `a -> b`, `b -> c` | `{"a` | RESOURCE_LIMIT | 146 bytes, `"` refused (a needs b and c) |
| `minProperties = maxProperties = 2` | `{"a":1`, `{"a":1,"b":2` | `,.0123456789Ee`, `.0123456789Ee}` | same |

Causes. (1) `grammar.needsReachability` did not route a count cap: the
parser takes the comma past the cap and refuses only the next dispatch,
so with values that need no filter the prefix stayed typable forever.
(2) On a grammar that runs the filter, the count certificate
(`certOpenObjCounted`) had dead rules for a key at the cap and for
`n + required > max`, but none for a key typed while every remaining
slot is owed: `{"b` under `maxProperties: 1`, `required: [a]` could
become no owed name, and the search cannot exhaust key spellings, so the
mask failed with RESOURCE_LIMIT already at `{"` - every pass of such a
schema stopped at its first key. Counted objects with dependencies were
not certified at all. R9 said that routing gives RESOURCE_LIMIT at
`{"x":1,"`; that was imprecise - the review's example becomes exact by
the routing alone (the comma is dead at the cap); the RESOURCE_LIMIT
needs owed names.

Fix (`src/grammar.zig`, `src/complete.zig`, `src/synth.zig`):
- routing: an open object with `maxProperties` runs the filter;
- dead rules for every counted node, whatever else it constrains
  (`certOpenObjCount`): the owed names - pending required declared props,
  missing extra required names, unseen names of fired dependencies,
  closed under the dependencies they fire (`owedNames`) - each take one
  more key; `n + owed > max` is dead; a key at the cap is dead; a key
  typed while `n + 1 + owed > max` must spell an owed name that can be
  dispatched now (`owedKeyPossible`: raw prefix, mid-character into the
  pending code point range, schema order, value and doom), else dead;
  the `minProperties` rule as before;
- alive in closed form (`countAlive`) where the count-free analysis is
  alive and one completion of a fitting key count exists: exactly the
  owed names, one fresh undeclared key where a key is mandatory (plain
  fallback with a non-empty value), fresh keys up to `minProperties`;
  otherwise the simulation certificate, which now also completes an
  in-flight key to an owed dependency name;
- counted objects with dependentRequired take the dependency route.

Without the closed-form alive the filter ran the simulation for every
token of a key or a nested value: Llama masks inside such an object took
3.9 s. Measured (`de7/mask_time_d.py`, Llama 3.1, one fill_mask in a
fresh context, median of 3; `logs/d-mask-times.txt`):

| state | 50cbd80e | ea54be1b | 6e62e0d1 |
|---|---|---|---|
| `maxProperties: 3`, `{"` / `{"ab":1,"` (key) | 799 / 829 ms | 927 / 896 ms | 1075 / 1049 ms |
| same, `{"ab":"x` (string value) | 8.5 ms (unfiltered) | 8.7 ms (unfiltered) | 1026 ms |
| integer property, `{"b":"x` (filtered on every build) | 928 ms | 1014 ms | 1021 ms |

A count-capped object now costs what every filtered grammar costs (one
integer property is enough to filter a schema).

Regressions (`tests/test_open_object_dead_ends.py`, group D): exact masks
after prefixes for `maxProperties` 0, 1 and N, at the cap and below it,
with required, declared and extra names, dependency chains and required
names that fire dependencies, `minProperties`, nesting, arrays,
`patternProperties` and `propertyNames`; every mask on the way computed
(status OK); refused bytes on open objects; walks on 13 count schemas
(none dead-ends, every mask OK, every finished document valid) and the
independent reference on the same walks; the unit test "complete:
maxProperties counts the owed names and the in-flight key" (dead, alive,
and alive in closed form without the simulation). The independent
reference builds no candidate over `maxProperties` (the validator would
refuse it); without that its witness search took 60-170 s per schema.

### R6 runner isolation

`benchmarks/t3/run_t3_accept.sh` is rewritten (with
`benchmarks/t3/t3_identity.py`, `acceptance_pins.json`,
`acceptance-requirements.txt`, `tests/test_t3_accept_runner.py`; the same
files in the working tree):

- the run directory holds every input: `git archive` of the candidate
  commit (the checkout must be clean), the library (with the links of a
  zig-out/lib), both corpus snapshots, the tokenizer snapshots with their
  links resolved, and a venv created at its final path;
- corpora pinned in `acceptance_pins.json` by file count and content
  digest (repo 9,558 files; MaskBench 11,306 files at ba103c7, the
  checkout clean); the harness corpus_sha256 of both equals the dbc5eeb
  baseline reports; the snapshot files of both tokenizer revisions pinned
  by content; checked on the sources before anything is copied and on
  the copies;
- the venv: every package pinned as name==version, installed with
  `--no-deps`, `pip check`, `pip freeze --all` equal to the list, pip
  pinned, the import check; the package of the copy gets its own `_core`
  built against the run's library (`_lib` must hold the same bytes);
- `identity/manifest.json` with per-input file lists: tree, library and
  its directory, both corpora, the HF copy, every file of the venv, the
  base interpreter and its standard library by content, the parameters
  (jobs, passes, sample, chunk size, memory limit); every resume
  recomputes all of it and refuses, naming each differing field and up
  to ten paths;
- `identity/.complete` comes last; a directory with content and without
  it is an interrupted preparation and is refused; a resume runs the
  run's own copy of the runner;
- exit status: 0 finished; the harness status when it failed
  (`RUN_DONE exit=N`); 2 refused; 3 a failed preparation step.

Checks on stand-in inputs (`tests/test_t3_accept_runner.py`, 26 tests):
an unchanged run resumes and runs the harness with the copy's library,
cache and venv only; harness statuses 1 and 7 pass through; a changed
tree file, an added tree file, the library, a venv file, an added `.pth`,
a corpus file changed or removed, a tokenizer snapshot or blob, the jobs
and the memory limit are each refused by name; another library argument
and a runner other than the copy are refused; a preparation stopped by
SIGTERM in the venv step leaves no marker and the next start refuses
it; partial directories are refused; a dirty checkout, a source corpus
off its pin and a MaskBench checkout at another revision are refused
before any copy; a package built on another library fails the
preparation.

Dry run on the real inputs (`de7/prep-clone`: a throwaway clone at
c288703 plus the patch, committed only there; `--prepare-only` with the
stage-D candidate library): the first attempt stopped at `pip check` -
`huggingface_hub` needs `tomli` on Python 3.10, which the list had
dropped with the test runner (exit 3, no marker; the next start refused
the directory, exit 2); the second stopped at the tokenizer check - the
harness converts HF tokenizers with `bolorgir.TokenizerBundle`, whose
`_core` extension `git archive` does not hold (the old runner copied the
working tree's built extension and its `_lib` unchecked; in
`acc-50cbd80e` it happened to hold the run's library); with `tomli` and
the package build
the third prepared in 12 s: sources and copies on their pins, package
identity (core 6e62e0d1), 331 tree files, 6,033 venv files, both
tokenizers loaded from the copy (gpt2 50,257 tokens, Llama 128,256),
`RUN_READY`; a resume passed, a changed corpus file in the copy was
refused (`corpora.repo.digest`, `changed Github_easy/o10008.json`, exit
2), and passed again once restored. The acceptance itself was not run.

T8a: the embedded smoke of a full run now uses the run's `[threads]`
(no fixed 4; `tests/test_t8a_tools.py`).

### Checks

Release line: c288703 plus `de7/release-line-d.patch` (sha256
`2cab03484c52436ea48cf5b990e39ab1c5de017d8ee6fec531cf16d1a66a9ef0`);
logs in `benchmarks/local/t3/de7/logs`.

- `rel_tests.sh relD rel-d`: fmt (now with `src/synth.zig`), zig build
  test (285 unit tests), release build, package identity; the regression
  module 379 passed; the full pytest 1466 passed, 18 skipped (the skips
  of R9).
- `w2_tests.sh w2d` (the wave-2 copy moved from patch C to D, no rejects;
  offsets in CHANGELOG and `src/grammar.zig`): 818 targeted tests passed
  (now with `test_spec_v1_r2_r3`, `tests/fuzz`, `test_t3_accept_runner`),
  one known warning (the ECMA reference worker ended with -6 on two
  cases).
- `cand_build.sh d2`: the two builds are identical,
  `6e62e0d1526c6e5369457d2f7205675b7cb163e3374b96d8cb5115c28873e914`,
  stripped; the git blob ids of the four patched files equal the pins of
  `core.patched` (now carried as `core.manifest`); `release_gate.py
  sources` passed on a throwaway clone at c288703 with the patch.
- `cand_checks.sh d2` on those bytes: the regression module 379 passed;
  gated walks on 37 schemas (the 24 of R9 and the 13 count schemas of
  `de7/schemas-d`), 200 each: 986,690 steps, all compared, none
  unsettled (R9: 12 - the gated variant of the two open own-dependency
  schemas now takes the dependency route), no finding
  (`evidence/r10_walks_gated.jsonl`); reference walks on 30 schemas, 30
  each: 61,298 steps; 118,704 witness searches for a byte the mask
  refused found none (7,533 of them at the budget, unproven); 60,117 of
  the 60,529 accepted bytes have a witness, 412 none within the budget
  (the 53 of R9 and 359 on the count schemas, at most 6% of a schema's
  steps); every finished document valid; no finding
  (`evidence/r10_walks_reference.jsonl`).
- Tools of the working tree (`test_t3_accept_runner`, `test_t8a_tools`,
  `test_release_manifest`, `test_t3_acceptance_tools`): 104 passed.
- Corpus effect of the routing (`de7/impact_maxprops.py`, the byte
  passes of the acceptance - 20 per schema, its seeds, 10 s and 8,192
  steps - on the 210 corpus schemas whose file mentions maxProperties, 85
  repo and 125 MaskBench, with the harness of the release-line copy;
  `evidence/r10_maxprops_impact.txt`, per-schema rows
  `r10_maxprops_impact_*.jsonl.gz`): ea54be1b - 77 generatable, 82
  engine_unavailable, 8 invalid_document; 6e62e0d1 - 85, 70, 12 (43
  schemas on both do not compile, UNSUPPORTED_FEATURE). No schema leaves
  generatable. Eight go from engine_unavailable (RESOURCE_LIMIT) to
  generatable (o83690, o42217, sp_347, sp_348, each in both snapshots);
  four go from engine_unavailable to invalid_document (o38619 and
  o48619 in both snapshots): their passes now get past the first key and
  finish, and every rejected document (17 of o38619, 4 of o48619 in the
  repo) is valid with value integers - integers spelled with an exponent
  or a fraction under draft-04, the documented class D
  (`docs/supported_features.md` section 9; `de7/invalid_docs_d.py`).
  Ten more schemas changed only their pass counts inside the same
  outcome (more completed passes; `3.0.schema.json` trades time limits
  for step limits).

## R11: the transitions of 6e62e0d1 (2026-10-10)

The first review of the published set (`benchmarks/coverage/spec-v1/t3-6e62e0d1/review`,
R6 acc-6e62e0d1, second oracle) lists 83 transition rows, 69 results:
42 `invalid_document` rows (34 results), 29 `incomplete` rows (27
results, 24 without a D4 entry), 12 `engine_unavailable` rows (8
results, MaskBench bigquery-table without an entry). Tools and outputs in
`benchmarks/local/t3/de7/d4e/` (plan with the decision rule, written
before the replays: `R11_PLAN.md`); the saved data first (`facts.py`,
`pass1_edge.py`, `slowdown_keywords.py`), then three replays approved by
the owner, one at a time, nothing else running (guard log `r11-2`).

### Saved data

- `invalid_document`: every one of the 34 results is T2c class D of the
  candidate (draft-04 integer spelling) with all 68 invalid documents
  checked (`review/triage.json`); the D4-6 rule applies as written.
- `incomplete`: in every result the line's one completed pass is pass 1,
  the greedy policy pass (`generate_document`), which checks its 10 s
  budget only at steps 16, 32, 48, ...; the candidate recorded the time
  limit at the first check it failed (step 16 on all 22 Llama results).
  The flips of pass 1 against 7345490f are one-sided (Llama 24 lost / 4
  gained), against dbc5eeb the other way (2 / 35): not symmetric noise.
  Against 50cbd80e (the same harness and protocol, 8 jobs) the candidate
  reaches about 6 % fewer steps in 10 s on GPT-2 and Llama, none fewer on
  the byte tokenizer, with or without the open-object constructs of
  stages C/D. The candidate is built by the release recipe
  (`-Dcpu=baseline`: no AVX2, POPCNT or BMI); the libraries of the lines
  and 50cbd80e are host builds.
- `engine_unavailable`: on the repository the candidate ends these
  passes exactly as 50cbd80e (same pass, step and outcome).

### Replays

- Build N: the candidate's source (`git archive fc44c37`) with the
  release recipe and `-Dcpu=native` (`de7/cand-e-native`, sha256
  16a0264bb9d8d3a2, 3,568 vector instructions, the same exports;
  diagnostic only, never a candidate).
- R11-A: pass 1 of the 27 incomplete results on five libraries (cand,
  native, 50cbd80e, 7345490f, dbc5eeb) with the run's harness (fc44c37),
  Python environment, schemas and tokenizers (the run's HF snapshot,
  exported once: `r11a_specs.json`, so that the library under test is
  the only libbolorgir in a process); 60 s cap, every fill timed; one
  process per library and round on CPU 2 (P-core) at nice 0 after the
  load average fell below 1.5; 3 rounds, the order rotated. Decision
  rule (fixed before the run): M = elapsed time at the check where the
  candidate recorded its limit, median over rounds; threshold = max(2 %,
  median round-to-round spread).
- R11-C: every `engine_unavailable` pass of the candidate (19 passes, 8
  results), recorded seeds, 10 s.
- R11-B: MaskBench bigquery-table passes 5 and 12 on dbc5eeb and
  7345490f with 300 s, on 50cbd80e with 10 s.

### Results

R11-A (`evidence/r11a_pass1_summary.md`, `.json`): the pass-1 document,
and with it the greedy path of the pass, is the same on all five
libraries in 27 of 27 results. The full masks were not compared: the
replay records the document and the time of each fill. Noise 2.9 %.

| ratio of M, median over 27 results | median | range | results above 1 |
|---|---|---|---|
| candidate / native (release CPU target) | 1.096 | 1.024-1.126 | 27 |
| native / 50cbd80e (code since 50cbd80e) | 0.993 | 0.954-1.025 | 10 |
| candidate / 7345490f | 1.095 | 0.848-1.173 | 26 |
| candidate / dbc5eeb | 1.096 | 0.860-1.244 | 26 |
| 50cbd80e / 7345490f | 1.009 | 0.769-1.189 | 23 |

Attribution by the rule: the release CPU target. On a quiet core the
periodic budget check (steps 16, 32, 48, ...) fires on no library in 26
of 27 results and on every library in kb_1154 (the summary's "complete
under the 10 s budget"). That is the budget rule, not a pass under 10 s:
129 of the 390 replays without a fired check took longer than 10 s in
all and ended between two checks (16 results, up to 13.8 s; candidate 43,
native 21, 50cbd80e 28, 7345490f 17, dbc5eeb 20). So these transitions
are the budget edge under load: in the R6 run (8 jobs) the candidate
exceeded 10 s at a budget check where the lines' runs had not, and
50cbd80e, a host build run with the same harness and protocol, completed
pass 1 in 12 of the 14 repository results where the candidate did not
(the other two are D4-13 and D4-14, transitions already on 50cbd80e).
The release workflow has built with `-Dcpu=baseline` since dbc5eeb
(`.github/workflows/release.yml`); the libraries of the coverage lines
were local host builds. R11-A measures the effect of the target on the
current revision only: a released wheel or an earlier revision built
the same way was not measured, so nothing here compares the candidate
with the released product.

Two results differ from the group:

- MaskBench kb_1154 (Llama, D4-2): every library exceeds 10 s at step 16
  (candidate 14.84 s, native 13.63 s, 50cbd80e 13.46 s, 7345490f 17.50 s,
  dbc5eeb 17.26 s; as R2b), the candidate faster than both lines.
  7345490f completed this pass 1 (step 31) in its run of 2026-10-03 with
  that day's harness (`dbc5eeb+dirty`, no source hash recorded); no
  library reproduces it with the run's harness, and the cause of that
  historical completion is not established.
- repository calculate_area (byte, D4-15): no target effect beyond the
  noise (candidate/native 1.024); the candidate is as fast as 50cbd80e
  (0.986) and 17 % slower than 7345490f (1.173); 50cbd80e is slower by as
  much (50cbd80e/7345490f 1.189), so the slowdown lies in the code
  between 7345490f and 50cbd80e, both host builds (which change is not
  established). 50cbd80e completed pass 1 in its R6 run, the candidate
  did not; on a quiet core the two are equally fast, so R11-A does not
  explain the difference between their R6 outcomes. Proposed separately
  (below).

R11-C (`evidence/r11bc_summary.md`, `.json`): all 19 passes match their
R6 record in outcome, stage, code, step, prefix length and seed (the
record holds no prefix bytes); engine message
`mask computation limit exceeded`, undecided reasons `fail_search` with
`comb_group` (tye-schema, bigquery-table, o31351), with `comb_group`,
`openobj_deps` and `openobj_gate` (o32934), or `fail_search` alone at a
number exponent in a `uniqueItems` element (cryproj: `...e+97`,
`256E165`).

R11-B: MaskBench bigquery-table is not new behaviour of the candidate:
dbc5eeb and 7345490f reach the same `RESOURCE_LIMIT` state given the
time (pass 5 at step 1109 after 52-54 s, pass 12 at step 2125 after
148-153 s), 50cbd80e under its 10 s budget (5.6 s, 8.5 s); the same step,
and the prefix byte-equal to the candidate's R11-C replay (the only
byte comparison of prefixes in R11); the lines
recorded these passes as time limits at steps 88-153.

Dry check of the list (`acceptance_compare.py` on the published set with
the first review's inputs, output outside the set), for both versions of
the list: `transitions_described` ok (0 results without an entry),
`d4_candidate` ok, the only failing check `transitions_approved` (83 rows
pending the owner); every other check as in the first review. review-2
(the first version) confirmed it on the set. With the draft decisions
below applied as a simulation (outside the repository, `by` "simulation,
not a decision"), the same check gives `admitted`, 83 rows approved.

## D4 proposals for 6e62e0d1 (for the owner)

`benchmarks/t3/d4_proposals.json` (sha256 8016b0f9f685470b), written for
the candidate 6e62e0d1 by `de7/d4e/make_d4_list.py` from the saved data
and the R11 summaries; it supersedes the list for 50cbd80e (233d5fd4),
and every changed entry keeps its earlier text under `previous`. This is
the second version: the first (9557b4bd, the list of review-2) was
reworded after the reviewer's notes of 2026-10-10 (the full masks were
not compared; D4-15 a group of its own; kb_1154; no statement about the
released product; the budget check that did not fire; the fields R11-C
compared); the entries, their match keys and `candidate_rows` are
unchanged. Each entry has cause, evidence, proposal and `candidate_rows`,
the exact rows an `accept` decision lists; `owner_decision` is null in
all 41.

1. The CPU target at the pass-1 budget edge (D4-13, D4-14, D4-16 to
   D4-38; 25 results): the candidate's mask fill is 10.0 % slower
   (median, 5.5-12.6 %, every result above the noise) than its own
   source built for the host CPU, on the GPT-2 and Llama vocabularies;
   the pass-1 documents are the same on every library (the full masks
   were not compared). Proposal: accept as a timing transition of the
   portable release target, measured on the current revision. The
   alternative, a release target with vector instructions (`x86_64_v3` or
   per-CPU dispatch), is a new candidate and a new R6 and a portability
   decision: the owner's choice.
2. Repository calculate_area, byte (D4-15), decided separately: pass 1 at
   the same budget edge, no CPU-target effect beyond the noise; the
   candidate is as fast as 50cbd80e and about 17 % slower than 7345490f
   on this schema, a slowdown in the code between 7345490f and 50cbd80e.
   Proposal: accept as a timing transition with this recorded slowdown;
   the alternative, finding and removing it, is a new candidate and a new
   R6.
3. MaskBench kb_1154 (D4-2): an expensive pass 1 on every library.
   Proposal: as before, accept as a documented availability residual;
   the cause of 7345490f's historical completion of this pass 1 is not
   established (above).
4. `engine_unavailable` (D4-3, D4-3m, D4-4, D4-5, D4-10, D4-11, D4-11m,
   D4-12): pre-existing undecided states and the documented |exp| 400
   limit; every pass matches its R6 record in R11-C; MaskBench
   bigquery-table (D4-11m, new) is the repository schema of D4-11 and
   the lines reach its state given the time (R11-B). Proposal: accept as
   documented limitations; group-certificate work is future scope.
5. `invalid_document` (D4-6): 42 rows, 34 results, all class D with every
   invalid document checked. Proposal: accept by the class D rule.
6. No transition on 6e62e0d1 (D4-1, D4-2r, D4-7, D4-8, D4-9): o32437 is
   generatable on Llama, the three DEAD_END schemas complete 20 of 20,
   the repository kb_1154 is outside the HF sample. Kept for the record.

The decisions themselves stay outside the tracked list (its
`owner_decision` stays null: `tests/test_t3_acceptance_tools.py`). A
draft with one `accept` per entry, the candidate's sha256 and the
entry's `candidate_rows` (36 entries, 69 rows, covering the 83
transition rows) is `de7/d4e/owner_decisions_draft.6e62e0d1.json`; its
`by` and `date` are empty and the executor does not fill them.
`de7/d4e/owner_decisions.py apply` writes the list for the next review
from the owner's decisions and refuses a decision without `by` and
`date` or with rows other than `candidate_rows`.

| entry | result | tok | line pass 1 | candidate pass 1 (R6) | 50cbd80e pass 1 (R6) | M cand / native / 50cbd80e / 7345490f (s) | cand/native |
|---|---|---|---|---|---|---|---|
| D4-2 | maskbench/Kubernetes---kb_1154_Normalized.json | llama-3.1 | 7345490f cv@31 | tl@16 | - | 14.84 / 13.63 / 13.46 / 17.50 | 1.089 |
| D4-13 | repo/o41283.json | gpt2 | 7345490f cv@59 | tl@48 | tl@32 | 8.34 / 7.62 / 7.66 / 7.61 | 1.094 |
| D4-14 | repo/kb_1088_Normalized.json | llama-3.1 | 7345490f cv@21 | tl@16 | tl@16 | 8.09 / 7.55 / 7.53 / 7.44 | 1.071 |
| D4-15 (separate) | repo/calculate_area_4c8e9fd1.json | byte | 7345490f cv@60 | tl@48 | cv@60 | 7.62 / 7.44 / 7.72 / 6.49 | 1.024 |
| D4-16 | repo/kb_1008_Normalized.json | gpt2 | 7345490f cv@46 | tl@32 | cv@46 | 7.93 / 7.52 / 7.34 / 7.43 | 1.055 |
| D4-17 | repo/kb_1057_Normalized.json | llama-3.1 | 7345490f cv@22 | tl@16 | cv@22 | 8.14 / 7.33 / 7.68 / 7.34 | 1.111 |
| D4-18 | repo/kb_1123_Normalized.json | llama-3.1 | 7345490f cv@18 | tl@16 | cv@18 | 8.08 / 7.19 / 7.54 / 7.38 | 1.123 |
| D4-19 | repo/kb_157_Normalized.json | llama-3.1 | 7345490f cv@21 | tl@16 | cv@21 | 8.17 / 7.46 / 7.34 / 7.38 | 1.096 |
| D4-20 | repo/kb_199_Normalized.json | llama-3.1 | 7345490f cv@23 | tl@16 | cv@23 | 8.10 / 7.49 / 7.63 / 7.42 | 1.081 |
| D4-21 | repo/kb_396_Normalized.json | llama-3.1 | 7345490f cv@23 | tl@16 | cv@23 | 8.13 / 7.46 / 7.45 / 7.43 | 1.090 |
| D4-22 | repo/kb_480_Normalized.json | llama-3.1 | 7345490f cv@23 | tl@16 | cv@23 | 8.32 / 7.46 / 7.62 / 7.30 | 1.115 |
| D4-23 | repo/kb_576_Normalized.json | llama-3.1 | 7345490f cv@23 | tl@16 | cv@23 | 8.14 / 7.47 / 7.53 / 7.46 | 1.089 |
| D4-24 | repo/o43083.json | llama-3.1 | 7345490f cv@20 | tl@16 | cv@20 | 8.09 / 7.33 / 7.54 / 7.38 | 1.103 |
| D4-25 | repo/o44130.json | llama-3.1 | dbc5eeb cv@22, 7345490f cv@22 | tl@16 | cv@22 | 8.16 / 7.33 / 7.52 / 7.41 | 1.113 |
| D4-26 | repo/search_product_67f0c7b7.json | llama-3.1 | 7345490f cv@25 | tl@16 | cv@25 | 8.18 / 7.43 / 7.42 / 7.39 | 1.101 |
| D4-27 | maskbench/Github_easy---o1008.json | llama-3.1 | 7345490f cv@21 | tl@16 | - | 8.14 / 7.37 / 7.53 / 7.45 | 1.106 |
| D4-28 | maskbench/Github_easy---o13688.json | llama-3.1 | 7345490f cv@22 | tl@16 | - | 8.07 / 7.56 / 7.41 / 7.39 | 1.067 |
| D4-29 | maskbench/Github_easy---o48800.json | llama-3.1 | 7345490f cv@20 | tl@16 | - | 8.21 / 7.47 / 7.63 / 7.40 | 1.100 |
| D4-30 | maskbench/Github_medium---o32493.json | llama-3.1 | 7345490f cv@23 | tl@16 | - | 8.10 / 7.30 / 7.47 / 7.40 | 1.110 |
| D4-31 | maskbench/Github_trivial---o84263.json | llama-3.1 | 7345490f cv@25 | tl@16 | - | 8.30 / 7.43 / 7.43 / 7.38 | 1.117 |
| D4-32 | maskbench/Glaiveai2K---calculate_distance_51b4a417.json | llama-3.1 | 7345490f cv@25 | tl@16 | - | 8.36 / 7.51 / 7.49 / 7.42 | 1.112 |
| D4-33 | maskbench/Glaiveai2K---generate_invoice_1c946d60.json | gpt2 | 7345490f cv@32 | tl@32 | - | 7.93 / 7.49 / 7.36 / 7.45 | 1.059 |
| D4-34 | maskbench/Glaiveai2K---generate_resume_d9ba16a3.json | gpt2 | 7345490f cv@61 | tl@48 | - | 8.57 / 8.08 / 7.89 / 7.86 | 1.060 |
| D4-35 | maskbench/Kubernetes---kb_231_Normalized.json | llama-3.1 | dbc5eeb cv@23, 7345490f cv@23 | tl@16 | - | 8.23 / 7.31 / 7.46 / 7.45 | 1.126 |
| D4-36 | maskbench/Kubernetes---kb_379_Normalized.json | llama-3.1 | 7345490f cv@26 | tl@16 | - | 9.11 / 8.39 / 8.40 / 8.21 | 1.085 |
| D4-37 | maskbench/Kubernetes---kb_576_Normalized.json | llama-3.1 | 7345490f cv@23 | tl@16 | - | 8.20 / 7.31 / 7.67 / 7.31 | 1.122 |
| D4-38 | maskbench/Kubernetes---kb_613_Normalized.json | llama-3.1 | 7345490f cv@23 | tl@16 | - | 7.89 / 7.41 / 7.48 / 7.29 | 1.065 |

M: elapsed time at the budget check where the candidate recorded its
limit (step 16, 32 or 48), quiet core, median of 3 rounds (R11-A).
