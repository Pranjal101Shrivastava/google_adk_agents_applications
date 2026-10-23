"""Tests for the deterministic weighted grade."""

from __future__ import annotations

import pytest

from code_review_assistant.config import config
from code_review_assistant.constants import StateKeys
from code_review_assistant.tools.grading import (
    CRITICAL_SECURITY_CAP,
    compare_grades,
    compute_grade,
)

from .conftest import FakeToolContext


def _state(
    *,
    docstring_coverage: float = 1.0,
    max_complexity: int = 3,
    issues: list | None = None,
    module_docstring: bool = True,
    style_score: int | None = 100,
    security_score: int | None = 100,
    has_critical: bool = False,
    test_report: dict | None = None,
) -> dict:
    return {
        StateKeys.STRUCTURE_ANALYSIS: {
            "issues": issues or [],
            "metrics": {
                "docstring_coverage": docstring_coverage,
                "max_complexity": max_complexity,
                "module_has_docstring": module_docstring,
            },
        },
        StateKeys.STYLE_REPORT: (
            {"score": style_score, "violation_count": 0} if style_score is not None else {}
        ),
        StateKeys.SECURITY_REPORT: (
            {
                "score": security_score,
                "finding_count": 1 if has_critical else 0,
                "by_severity": {"critical": 1 if has_critical else 0},
                "has_critical": has_critical,
            }
            if security_score is not None
            else {}
        ),
        StateKeys.TEST_REPORT: test_report
        if test_report is not None
        else {"status": "success", "passed": 4, "failed": 0, "total": 4},
    }


# --- Happy path -------------------------------------------------------
async def test_clean_submission_scores_highly():
    ctx = FakeToolContext(_state())
    grade = await compute_grade(ctx)
    assert grade["status"] == "success"
    assert grade["score"] >= 90
    assert grade["band"] == "A"
    assert grade["passed"] is True


async def test_grade_is_stored_in_state():
    ctx = FakeToolContext(_state())
    grade = await compute_grade(ctx)
    assert ctx.state[StateKeys.GRADE] == grade


async def test_grading_is_deterministic():
    first = await compute_grade(FakeToolContext(_state()))
    second = await compute_grade(FakeToolContext(_state()))
    assert first["score"] == second["score"]


async def test_breakdown_reports_every_assessed_dimension():
    grade = await compute_grade(FakeToolContext(_state()))
    assert set(grade["breakdown"]) == {"correctness", "style", "security", "structure"}
    for dimension in grade["breakdown"].values():
        assert "note" in dimension
        assert dimension["configured_weight"] > 0


# --- Caps -------------------------------------------------------------
async def test_syntax_error_caps_the_grade_at_zero():
    ctx = FakeToolContext({StateKeys.SYNTAX_ERROR: "line 1: invalid syntax"})
    grade = await compute_grade(ctx)
    assert grade["score"] == 0
    assert grade["band"] == "F"
    assert grade["passed"] is False
    assert "does not parse" in grade["caps_applied"][0]


async def test_critical_security_finding_caps_the_grade():
    """A hardcoded credential must not be averaged away by tidy formatting."""
    ctx = FakeToolContext(_state(security_score=60, has_critical=True))
    grade = await compute_grade(ctx)
    assert grade["score"] == CRITICAL_SECURITY_CAP
    assert grade["passed"] is False
    assert grade["caps_applied"]


async def test_cap_does_not_raise_an_already_low_score():
    ctx = FakeToolContext(
        _state(
            security_score=0,
            has_critical=True,
            style_score=10,
            docstring_coverage=0.0,
            module_docstring=False,
            test_report={"status": "success", "passed": 0, "failed": 4, "total": 4},
        )
    )
    grade = await compute_grade(ctx)
    assert grade["score"] < CRITICAL_SECURITY_CAP


# --- Dimension sensitivity -------------------------------------------
async def test_failing_tests_lower_the_grade():
    passing = await compute_grade(FakeToolContext(_state()))
    failing = await compute_grade(
        FakeToolContext(
            _state(test_report={"status": "success", "passed": 1, "failed": 3, "total": 4})
        )
    )
    assert failing["score"] < passing["score"]


async def test_missing_docstrings_lower_the_structure_score():
    documented = await compute_grade(FakeToolContext(_state(docstring_coverage=1.0)))
    undocumented = await compute_grade(
        FakeToolContext(_state(docstring_coverage=0.0, module_docstring=False))
    )
    assert (
        undocumented["breakdown"]["structure"]["score"]
        < documented["breakdown"]["structure"]["score"]
    )


async def test_excess_complexity_lowers_the_structure_score():
    simple = await compute_grade(FakeToolContext(_state(max_complexity=3)))
    gnarly = await compute_grade(
        FakeToolContext(_state(max_complexity=config.max_complexity * 3))
    )
    assert (
        gnarly["breakdown"]["structure"]["score"]
        < simple["breakdown"]["structure"]["score"]
    )


async def test_structural_issues_lower_the_structure_score():
    clean = await compute_grade(FakeToolContext(_state()))
    dirty = await compute_grade(
        FakeToolContext(
            _state(
                issues=[
                    {"kind": "mutable_default", "severity": "high"},
                    {"kind": "bare_except", "severity": "high"},
                ]
            )
        )
    )
    assert (
        dirty["breakdown"]["structure"]["score"]
        < clean["breakdown"]["structure"]["score"]
    )


@pytest.mark.parametrize(
    ("score_inputs", "expected_band"),
    [
        ({}, "A"),
        ({"style_score": 40, "docstring_coverage": 0.5}, "B"),
    ],
)
async def test_bands_track_the_score(score_inputs, expected_band):
    grade = await compute_grade(FakeToolContext(_state(**score_inputs)))
    assert grade["band"] == expected_band


# --- Unassessed dimensions -------------------------------------------
async def test_disabled_execution_is_not_scored_as_zero():
    """Punishing the author for the deployment's configuration would be wrong."""
    ctx = FakeToolContext(
        _state(test_report={"status": "error", "error_type": "disabled"})
    )
    grade = await compute_grade(ctx)
    assert "correctness" in grade["not_assessed"]
    assert "correctness" not in grade["breakdown"]
    assert grade["score"] >= 90


async def test_unassessed_weight_is_redistributed():
    ctx = FakeToolContext(
        _state(test_report={"status": "error", "error_type": "disabled"})
    )
    grade = await compute_grade(ctx)
    total = sum(d["effective_weight"] for d in grade["breakdown"].values())
    assert total == pytest.approx(1.0, abs=0.01)


async def test_redistribution_is_stated_in_the_summary():
    ctx = FakeToolContext(
        _state(test_report={"status": "error", "error_type": "disabled"})
    )
    grade = await compute_grade(ctx)
    assert "redistributed" in grade["summary"]


async def test_timeout_does_score_zero_for_correctness():
    """A timeout is information about the code, not a missing signal."""
    ctx = FakeToolContext(
        _state(test_report={"status": "error", "error_type": "timeout"})
    )
    grade = await compute_grade(ctx)
    assert grade["breakdown"]["correctness"]["score"] == 0


async def test_no_tests_collected_is_unassessed_not_zero():
    ctx = FakeToolContext(
        _state(test_report={"status": "error", "error_type": "no_tests"})
    )
    grade = await compute_grade(ctx)
    assert "correctness" in grade["not_assessed"]


async def test_missing_structure_analysis_is_an_error():
    grade = await compute_grade(FakeToolContext({}))
    assert grade["status"] == "error"
    assert "analyze_code_structure" in grade["message"]


# --- Fix comparison ---------------------------------------------------
async def test_improvement_is_detected():
    ctx = FakeToolContext(
        {
            StateKeys.GRADE: {
                "score": 50,
                "band": "F",
                "breakdown": {"correctness": {"score": 25}},
            },
            StateKeys.FIX_TEST_REPORT: {
                "status": "success",
                "passed": 4,
                "failed": 0,
                "total": 4,
                "all_passed": True,
            },
        }
    )
    result = await compare_grades(ctx)
    assert result["verdict"] == "improved"
    assert result["delta"] == 75
    assert result["tests_now_pass"] is True


async def test_regression_is_detected_and_named():
    """A fix that breaks something that worked must not be presented as a win."""
    ctx = FakeToolContext(
        {
            StateKeys.GRADE: {"score": 80, "breakdown": {"correctness": {"score": 100}}},
            StateKeys.FIX_TEST_REPORT: {
                "status": "success",
                "passed": 2,
                "failed": 2,
                "total": 4,
                "all_passed": False,
            },
        }
    )
    result = await compare_grades(ctx)
    assert result["verdict"] == "regressed"
    assert result["regressed"] is True
    assert result["delta"] < 0


async def test_unverifiable_fix_is_marked_unverified():
    ctx = FakeToolContext(
        {
            StateKeys.GRADE: {"score": 50, "breakdown": {"correctness": {"score": 25}}},
            StateKeys.FIX_TEST_REPORT: {"status": "error", "error_type": "disabled"},
        }
    )
    result = await compare_grades(ctx)
    assert result["verdict"] == "unverified"
    assert "not as a working fix" in result["message"]


async def test_comparison_without_an_original_grade_is_an_error():
    result = await compare_grades(FakeToolContext({}))
    assert result["status"] == "error"


# --- Weight validation ------------------------------------------------
def test_configured_weights_sum_to_one():
    """pydantic enforces this at import; this pins the contract."""
    assert sum(config.weights.values()) == pytest.approx(1.0, abs=0.001)


def test_bad_weights_fail_fast():
    from pydantic import ValidationError

    from code_review_assistant.config import ReviewConfig

    with pytest.raises(ValidationError):
        ReviewConfig(
            CORRECTNESS_WEIGHT=0.9,
            STYLE_WEIGHT=0.9,
            SECURITY_WEIGHT=0.9,
            STRUCTURE_WEIGHT=0.9,
        )
