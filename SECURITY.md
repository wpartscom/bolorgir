# Security policy

## Supported versions

Security fixes are provided for the latest release line (`0.1.x`).

## Reporting a vulnerability

Please report vulnerabilities privately via GitHub Security Advisories
(**Security** tab → **Report a vulnerability**). Do not open a public issue.

Include:

- affected version or commit;
- a minimal reproduction (schema or literal set, tokenizer description, mode,
  limits);
- expected vs actual behavior (crash, hang, memory-budget overrun, wrong mask).

## Scope notes

- Schemas, tokenizer definitions, and model outputs are untrusted inputs.
  Crashes, memory errors, infinite loops, or budget overruns caused by
  malformed input are security-relevant - please report them.
- The core is fuzzed in CI (`src/fuzz_main.zig`, weekly schedule); independent
  reproductions are still valuable.
