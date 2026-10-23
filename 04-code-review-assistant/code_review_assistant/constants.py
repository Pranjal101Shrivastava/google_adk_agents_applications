"""Session-state keys for the code review pipelines.

Every cross-agent hand-off goes through one of these. The review pipeline
writes them; the fix pipeline reads them and writes its own.
"""

from __future__ import annotations


class StateKeys:
    """Canonical session-state key names."""

    # --- Submission ----------------------------------------------------
    CODE_TO_REVIEW = "code_to_review"
    CODE_LINE_COUNT = "code_line_count"
    SYNTAX_ERROR = "syntax_error"

    # --- Review pipeline findings --------------------------------------
    STRUCTURE_ANALYSIS = "structure_analysis"
    STYLE_REPORT = "style_report"
    SECURITY_REPORT = "security_report"
    TEST_REPORT = "test_report"
    GENERATED_TESTS = "generated_tests"
    GRADE = "grade"
    REVIEW_FEEDBACK = "review_feedback"

    # --- Fix pipeline --------------------------------------------------
    FIXED_CODE = "fixed_code"
    FIX_ATTEMPT = "fix_attempt"
    FIX_TEST_REPORT = "fix_test_report"
    FIX_GRADE = "fix_grade"
    FIX_VALIDATION = "fix_validation"
    FIX_SUMMARY = "fix_summary"
