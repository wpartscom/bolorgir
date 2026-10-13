# Note: the interpretation block of r11a2_summary.py (2026-10-13)

TZ section 3.3 asked to separate the numeric result of R11-A2 from its
interpretation. The change was made to `r11a2_summary.py` on
2026-10-13 **after all 18 R11-A2 measurement processes had finished and
before the summary ran**; no measurement code was changed during the
series.

What was and was not touched:

- Unchanged and computed exactly as pre-registered (README_PLAN.md, and
  as in R11-A): the per-result M values at c*, the medians over the
  three rounds, the noise (median max/min - 1), the threshold
  `max(2 %, noise)`, every ratio (target, code, cand/6e62e0d1,
  cand/7345490f, cand/dbc5eeb, 50cbd80e/7345490f) and the per-round
  completion counts. The raw JSONL files and logs of all 18 processes
  are untouched.
- Changed presentation only: the JSON field `attribution` was replaced
  by `heuristic_reading` plus `interpretation_limits`, and the Markdown
  gained a `Reading (heuristic, not proof)` section. The text now states
  that the rule is a diagnostic heuristic, that the threshold is not a
  confidence interval (inside: no equivalence proof; outside: no cause
  proof), that the rule cannot attribute the residual to run conditions,
  and that equal document hashes prove equal documents only (token paths
  and masks were not recorded and are not compared).

The former wording ("attribution: the release CPU target ..." and "the
code since 50cbd80e is not [slower]") is the wording used in the
6e62e0d1 D4 revision and in the 2026-10-09 R11-A summary; it is not used
for 9a5baf3e.

Observable differences in the numbers against R11-A (2026-10-09): the
round-to-round noise of this series is higher (4.53 % against 2.9 %,
the machine carries the editor session; amendment A1/A1b in
README_PLAN.md), so the target ratio (1.071, 25/25 above 1) is above
the threshold but the code ratio (1.027, 23/25 above 1) is inside it;
the rule therefore names the release CPU target
(`-Dcpu=baseline`) as the leading hypothesis and does not separate the
code. This is a weaker statement than R11-A's and is presented as such.
