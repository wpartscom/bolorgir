# Read-only review of 1b99c3f, 2026-10-23

Repository sources, pinned libraries and historical measurements were not modified. No fresh warm benchmark or full matrix/R6/T8a was run.

## Memory retry coverage

`src/` is an isolated source snapshot with `fast-memory-test.zig` appended to mask.zig. Zig 0.17.0, ReleaseSafe, hooks and counters enabled. `checks.txt` records commands and output.

The submitted `fast path: the certificate cache cannot fail a fill's walk` passes.

New fixture calls fillMaskFast directly on a K1-eligible reachable state:

- schema: `{"type":"object","properties":{"x":{"type":"string","pattern":"^a*$"}}}` (additionalProperties absent, hence unrestricted);
- prefix: `{"`;
- tokens: `a`, `a` repeated 1024 times, `":""}`, EOS;
- same grammar, tokenizer, state, work limit (100000) and Accounting budget in both arms;
- only cert_cache_off toggles.

Unlimited memory: both arms succeed with mask word 7 and 9 charged operations. Cache-off peak 1313938 bytes; cache-on peak 1314402 bytes.

At limit 1313938 bytes:
- cache off: success, word 7, 9 operations;
- cache on: OutOfMemory, one certificate entry held, cert_muted=false, 1 operation charged.

The output word on error is partial and must not be used as a valid mask. This is an availability regression, not proof of an accepted incorrect mask. Grammar, tokenizer and FastData allocations are outside the limited account. Parser Side and mask/completion scratch share it. This is a unit-level complete fast-mask test, not a public ABI measurement or a test through the pinned binary.

The fast path certifies the initial state and stores a key before fillMaskExactPruned requests scratch. The new retry exists in fillMask only. c_api returns the error from fillMaskFast directly, so it does not enter the new wrapper. Structural-tag fillMask also has a separate walk without the new retry; it was inspected, not reproduced in this test.

## Warm failure can become a matrix PASS

`warm_exit_replay.py` calls the real bench_adr0007.main verdict/output code with expensive measurement functions replaced by saved data from evidence/2d982b60/equiv/warm_acceptance.json. Probe/cold setup is stubbed; no new timing is taken. All ten original on means, hashes, hit counts and alternating order are replayed.

The real harness returns normally with `warm_acceptance.pass=false`. The real matrix run_job therefore marks its warm-acceptance row `pass`, and write_report emits Overall PASS (1/1), while showing warm pass False underneath.

See `matrix-warm-replay/warm-acceptance.log`, `warm_acceptance.json`, `matrix.json`, and `matrix.md`. This is an isolated report/exit-code reproduction, not a completed acceptance matrix.

The full-scale jobs list also still passes --skip-maskbench to bench-gate. Neither the quick nor full command runs the pinned MaskBench trace equality gate through this row.

## Independently verified evidence

Pinned library: 2d982b60cec55f3328206570659c29307e2050e3453b97b956649aa48e45b94e, 1267776 bytes.

Manifests all match: 2d982b60 29/29, 2e863e86 24/24, 69a24712 23/23, dc415942 19/19, 642ba1ae 14/14. Strict VE comparator rerun: all 15 checks pass. All 20 warm legs have matching library hashes and 2000/2000 hits. The ten on means contain five threshold violations, correctly listed by the warm acceptance JSON.
