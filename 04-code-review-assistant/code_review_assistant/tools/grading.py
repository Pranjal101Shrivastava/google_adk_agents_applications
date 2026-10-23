"""Deterministic weighted grading.

The grade is arithmetic over the four analysis reports, not a model's opinion.
That buys three things: the same submission always scores the same, a reviewer
can argue with the weights rather than with the model, and "why is this a 62?"
has an answer you can check.

Two rules override the arithmetic, both deliberately:

* a **syntax error** caps the grade at 0 — there is nothing to grade;
* a **critical security finding** caps the grade at 49, so a hardcoded
  credential cannot be averaged away by tidy formatting and passing tests.
"""

from __future__ import annotations

import logging
from typing import Any

from google.adk.tools import ToolContext

from ..config import config
from ..constants import StateKeys

logger = logging.getLogger(__name__)

# Ceiling applied when any critical security finding is present.
CRITICAL_SECURITY_CAP = 49

GRADE_BANDS: tuple[tuple[int, str], ...] = (
    (90, "A"),
    (80, "B"),
    (70, "C"),
    (60, "D"),
    (0, "F"),
)


def _band(score: int) -> str:
    for threshold, letter in GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


def _structure_score(analysis: dict[str, Any]) -> int:
    """Score structural quality out of 100.

    Built from three things a reviewer actually looks at: are the functions
    documented, is anything too complex to test, and are there smells like a
    mutable default or a bare except.
    """
    metrics = analysis.get("metrics") or {}
    issues = analysis.get("issues") or []

    # Docstring coverage contributes up to 40 points.
    coverage = float(metrics.get("docstring_coverage") or 0.0)
    documentation = coverage * 40
    if metrics.get("module_has_docstring"):
        documentation = min(40.0, documentation + 5)

    # Complexity contributes up to 35. At or under the limit scores full marks;
    # beyond it, points fall away linearly to zero at twice the limit.
    max_complexity = int(metrics.get("max_complexity") or 0)
    limit = config.max_complexity
    if max_complexity <= limit:
        complexity = 35.0
    else:
        overshoot = min(1.0, (max_complexity - limit) / limit)
        complexity = 35.0 * (1.0 - overshoot)

    # The remaining 25 points are lost to structural issues by severity.
    cost = {"high": 10, "medium": 5, "low": 2}
    penalty = sum(cost.get(issue.get("severity", "low"), 2) for issue in issues)
    cleanliness = max(0.0, 25.0 - penalty)

    return int(round(documentation + complexity + cleanliness))


def _correctness_score(test_report: dict[str, Any]) -> tuple[int | None, str]:
    """Score correctness from the test run.

    Returns ``(score, note)``. ``None`` means correctness could not be
    assessed, which must be reported rather than scored as zero — untested is
    not the same as broken.
    """
    status = test_report.get("status")

    if status != "success":
        error_type = test_report.get("error_type")
        if error_type == "disabled":
            return None, "not assessed: code execution is disabled in this deployment"
        if error_type == "timeout":
            return 0, "tests timed out, which indicates a loop or a blocking call"
        if error_type == "resource_limit":
            return 0, "tests were killed by a resource limit (CPU or memory)"
        if error_type == "no_tests":
            return None, "not assessed: the generated test file collected no tests"
        return None, f"not assessed: tests could not run ({error_type or 'unknown'})"

    total = int(test_report.get("total") or 0)
    if total == 0:
        return None, "not assessed: no tests ran"

    passed = int(test_report.get("passed") or 0)
    score = int(round(100 * passed / total))
    return score, f"{passed}/{total} generated tests passed"


async def compute_grade(
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Combine the analysis reports into one weighted 0-100 grade.

    Reads the structure, style, security and test reports from session state,
    so run the other review tools first. Call this exactly once per review —
    it is arithmetic, and calling it twice cannot improve the answer.

    Args:
        tool_context: ADK tool context (supplied by the runtime).

    Returns:
        The weighted ``score``, its letter ``band``, the per-dimension
        ``breakdown`` with the weights used, whether the submission
        ``passed``, and any ``caps_applied``. When a dimension could not be
        assessed it is listed in ``not_assessed`` and its weight is
        redistributed across the rest rather than scored as zero.
    """
    state = tool_context.state

    if state.get(StateKeys.SYNTAX_ERROR):
        grade = {
            "status": "success",
            "score": 0,
            "band": "F",
            "passed": False,
            "breakdown": {},
            "not_assessed": ["style", "security", "structure", "correctness"],
            "caps_applied": ["syntax error: the code does not parse, so there is nothing to grade"],
            "summary": (
                "Not graded: the submission does not parse. Fix the syntax error "
                "and resubmit."
            ),
        }
        state[StateKeys.GRADE] = grade
        return grade

    structure_analysis = state.get(StateKeys.STRUCTURE_ANALYSIS)
    style_report = state.get(StateKeys.STYLE_REPORT) or {}
    security_report = state.get(StateKeys.SECURITY_REPORT) or {}
    test_report = state.get(StateKeys.TEST_REPORT) or {}

    if not structure_analysis:
        return {
            "status": "error",
            "message": (
                "No structure analysis in state. Run analyze_code_structure, "
                "check_code_style and review_security before grading."
            ),
        }

    dimension_scores: dict[str, int] = {}
    notes: dict[str, str] = {}
    not_assessed: list[str] = []

    dimension_scores["structure"] = _structure_score(structure_analysis)
    notes["structure"] = (
        f"docstring coverage "
        f"{int(float(structure_analysis['metrics'].get('docstring_coverage', 0)) * 100)}%, "
        f"max complexity {structure_analysis['metrics'].get('max_complexity')}, "
        f"{len(structure_analysis.get('issues') or [])} structural issue(s)"
    )

    style_score = style_report.get("score")
    if isinstance(style_score, int):
        dimension_scores["style"] = style_score
        notes["style"] = f"{style_report.get('violation_count', 0)} PEP 8 violation(s)"
    else:
        not_assessed.append("style")
        notes["style"] = "not assessed: the style check did not run"

    security_score = security_report.get("score")
    if isinstance(security_score, int):
        dimension_scores["security"] = security_score
        notes["security"] = (
            f"{security_report.get('finding_count', 0)} finding(s), "
            f"{(security_report.get('by_severity') or {}).get('critical', 0)} critical"
        )
    else:
        not_assessed.append("security")
        notes["security"] = "not assessed: the security scan did not run"

    correctness_score, correctness_note = _correctness_score(test_report)
    notes["correctness"] = correctness_note
    if correctness_score is None:
        not_assessed.append("correctness")
    else:
        dimension_scores["correctness"] = correctness_score

    # Redistribute the weight of any unassessed dimension over the rest, so an
    # absent signal neither helps nor hurts. Scoring it zero would punish the
    # author for the deployment's configuration.
    weights = config.weights
    available_weight = sum(weights[d] for d in dimension_scores)
    if available_weight <= 0:
        grade = {
            "status": "error",
            "message": "No dimension could be assessed, so no grade can be produced.",
        }
        state[StateKeys.GRADE] = grade
        return grade

    effective_weights = {
        dimension: weights[dimension] / available_weight for dimension in dimension_scores
    }
    raw_score = sum(
        dimension_scores[d] * effective_weights[d] for d in dimension_scores
    )
    score = int(round(raw_score))

    caps_applied: list[str] = []
    if security_report.get("has_critical") and score > CRITICAL_SECURITY_CAP:
        caps_applied.append(
            f"critical security finding: capped at {CRITICAL_SECURITY_CAP} "
            "regardless of the other dimensions"
        )
        score = CRITICAL_SECURITY_CAP

    verdict = (
        "pass" if score >= config.passing_score
        else f"below the {config.passing_score}-point bar"
    )
    redistribution_note = (
        f" Weight for {', '.join(not_assessed)} was redistributed because those "
        "dimensions could not be assessed."
        if not_assessed
        else ""
    )

    grade = {
        "status": "success",
        "score": score,
        "band": _band(score),
        "passed": score >= config.passing_score,
        "passing_score": config.passing_score,
        "breakdown": {
            dimension: {
                "score": dimension_scores[dimension],
                "configured_weight": weights[dimension],
                "effective_weight": round(effective_weights[dimension], 3),
                "note": notes[dimension],
            }
            for dimension in dimension_scores
        },
        "not_assessed": not_assessed,
        "not_assessed_notes": {d: notes[d] for d in not_assessed},
        "caps_applied": caps_applied,
        "summary": f"{score}/100 ({_band(score)}), {verdict}.{redistribution_note}",
    }

    state[StateKeys.GRADE] = grade
    logger.info("Grade: %d (%s), caps=%s", score, grade["band"], caps_applied)
    return grade


async def compare_grades(
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Compare the post-fix grade against the original, to verify a fix helped.

    Re-grades using the fix-phase test results and reports the delta. Use this
    in the fix pipeline rather than asserting that the fix worked — a fix that
    lowers the grade is a fix that should not be offered.

    Args:
        tool_context: ADK tool context (supplied by the runtime).

    Returns:
        The ``before`` and ``after`` scores, the ``delta``, and
        ``improved``/``regressed`` flags.
    """
    state = tool_context.state
    before = state.get(StateKeys.GRADE) or {}
    before_score = before.get("score")

    if before_score is None:
        return {
            "status": "error",
            "message": "No original grade in state; run the review pipeline first.",
        }

    fix_test_report = state.get(StateKeys.FIX_TEST_REPORT) or {}
    after_correctness, after_note = _correctness_score(fix_test_report)

    original_correctness = (before.get("breakdown") or {}).get("correctness", {}).get("score")

    result = {
        "status": "success",
        "before": {
            "score": before_score,
            "band": before.get("band"),
            "correctness": original_correctness,
        },
        "after": {
            "correctness": after_correctness,
            "correctness_note": after_note,
        },
        "tests_now_pass": bool(fix_test_report.get("all_passed")),
    }

    if after_correctness is None or original_correctness is None:
        result["verdict"] = "unverified"
        result["message"] = (
            "The fix could not be verified by tests. Present it as an unverified "
            "suggestion, not as a working fix."
        )
    else:
        delta = after_correctness - original_correctness
        result["delta"] = delta
        result["improved"] = delta > 0
        result["regressed"] = delta < 0
        result["verdict"] = (
            "improved" if delta > 0 else "regressed" if delta < 0 else "unchanged"
        )
        result["message"] = (
            f"Correctness moved from {original_correctness} to {after_correctness} "
            f"({delta:+d})."
        )

    state[StateKeys.FIX_GRADE] = result
    return result
