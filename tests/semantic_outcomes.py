"""Outcome classification and schema aggregation for the K-pass
generatable metric of the semantic coverage harness.

Shared by benchmarks/bench_jsb_coverage.py and the pytest suite so the
two cannot drift apart. Stdlib only; imports neither the harness nor
the engine bindings.

Every outcome is a record (outcome, stage, code, step, prefix_length,
seed). Stages: compile_check, session_create, fill_mask, accept,
finish, validate. Code: a blg_status name (BLG_ERR_*), a validator
exception type, CRASH, or a budget marker (STEP_LIMIT / TIME_LIMIT).
Stage and code are mandatory for unsuccessful outcomes.
"""

STAGES = (
    "compile_check",
    "session_create",
    "fill_mask",
    "accept",
    "finish",
    "validate",
)

PASS_OUTCOMES = (
    "completed_valid",
    "invalid_document",
    "validator_error",
    "engine_defect",
    "engine_unavailable",
    "incomplete:step_limit",
    "incomplete:time_limit",
)

SCHEMA_OUTCOMES = (
    "generatable",
    "invalid_document",
    "engine_defect",
    "validator_error",
    "engine_unavailable",
    "incomplete",
    "skipped",
)

UNAVAILABLE_CODES = frozenset({"BLG_ERR_RESOURCE_LIMIT", "BLG_ERR_CANCELLED"})

# Verbatim report-header disclaimer; the rendered report must carry
# this exact sentence.
GENERATABLE_DISCLAIMER = (
    "`generatable` is the result of K generation passes on the listed "
    "tokenizers, not a proof for every continuation"
)


def make_record(outcome, stage=None, code=None, step=None,
                prefix_length=None, seed=None):
    """One outcome record. Stage and code are mandatory for every
    outcome that is not completed_valid and not skipped."""
    return {
        "outcome": outcome,
        "stage": stage,
        "code": code,
        "step": step,
        "prefix_length": prefix_length,
        "seed": seed,
    }


def is_skipped(outcome):
    return outcome == "skipped" or outcome.startswith("skipped:")


def is_incomplete(outcome):
    return outcome.startswith("incomplete:")


def classify_engine_failure(stage, code):
    """Failed engine call -> pass outcome.

    RESOURCE_LIMIT or CANCELLED at any stage (including session_create)
    is an availability event. Everything else is an engine defect: the
    stage-specific rows (accept of a masked token, DEAD_END empty mask
    at fill_mask, blg_finish failing after EOS was allowed,
    session_create with any other code) and the default row
    (BLG_ERR_INTERNAL, process CRASH, any code not listed anywhere).
    """
    if code in UNAVAILABLE_CODES:
        return "engine_unavailable"
    return "engine_defect"


def validation_outcome(json_ok, verdict):
    """Validate-stage outcome. verdict: True (reference accepted),
    False (reference rejected), None (validator raised, no verdict).
    A document that does not parse as JSON is invalid_document."""
    if not json_ok or verdict is False:
        return "invalid_document"
    if verdict is None:
        return "validator_error"
    return "completed_valid"


def incomplete_count(outcomes):
    return sum(1 for o in outcomes if is_incomplete(o))


def aggregate_schema_outcome(outcomes):
    """Aggregate the pass outcomes of one schema, first matching rule:

    1. any invalid_document -> invalid_document
    2. any engine_defect -> engine_defect
    3. any validator_error -> validator_error
    4. any engine_unavailable -> engine_unavailable
    5. all passes skipped -> skipped (counted in the summary table but
       out of the percentage base)
    6. no completed_valid -> incomplete (incomplete passes are never a
       success)
    7. otherwise -> generatable
    """
    outcomes = list(outcomes)
    for target in ("invalid_document", "engine_defect",
                   "validator_error", "engine_unavailable"):
        if target in outcomes:
            return target
    if all(is_skipped(o) for o in outcomes):
        return "skipped"
    if "completed_valid" not in outcomes:
        return "incomplete"
    return "generatable"


def outcome_percentages(counts):
    """counts {schema outcome: n} -> (base, {outcome: percent|None}).
    The base excludes skipped; percent is None when the base is 0."""
    base = sum(n for outcome, n in counts.items() if outcome != "skipped")
    pct = {}
    for outcome, n in counts.items():
        pct[outcome] = (100.0 * n / base) if base else None
    return base, pct
