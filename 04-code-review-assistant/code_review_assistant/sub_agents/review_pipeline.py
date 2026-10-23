"""The review pipeline: analyse, check, scan, test, synthesise.

::

    CodeAnalyzer    -> AST structure, complexity, smells
    StyleChecker    -> pycodestyle
    SecurityReviewer-> AST security patterns
    TestRunner      -> generates pytest tests and runs them sandboxed
    FeedbackSynthesizer -> computes the grade and writes the review

Fixed order, and the order matters: the analyzer is the step that stores the
submission in session state and detects a syntax error, so nothing downstream
has to re-handle unparseable input.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent, SequentialAgent

from ..config import config
from ..constants import StateKeys
from ..prompts import (
    CODE_ANALYZER_PROMPT,
    FEEDBACK_SYNTHESIZER_PROMPT,
    SECURITY_REVIEWER_PROMPT,
    STYLE_CHECKER_PROMPT,
    TEST_RUNNER_PROMPT,
)
from ..tools.analysis import analyze_code_structure
from ..tools.execution import run_tests
from ..tools.grading import compute_grade
from ..tools.security import review_security
from ..tools.style import check_code_style

code_analyzer_agent = LlmAgent(
    name="CodeAnalyzer",
    model=config.worker_model,
    description="Parses the submission and reports structure, complexity and smells.",
    instruction=CODE_ANALYZER_PROMPT,
    tools=[analyze_code_structure],
    output_key="analysis_summary",
)

style_checker_agent = LlmAgent(
    name="StyleChecker",
    model=config.worker_model,
    description="Checks PEP 8 compliance with pycodestyle and reports a style score.",
    instruction=STYLE_CHECKER_PROMPT,
    tools=[check_code_style],
    output_key="style_summary",
)

security_reviewer_agent = LlmAgent(
    name="SecurityReviewer",
    model=config.worker_model,
    description="Scans for high-confidence security problems and reports them by severity.",
    instruction=SECURITY_REVIEWER_PROMPT,
    tools=[review_security],
    output_key="security_summary",
)

test_runner_agent = LlmAgent(
    name="TestRunner",
    # The reasoning model: writing tests that probe boundaries rather than
    # restating the happy path is the hardest judgement call in the pipeline.
    model=config.critic_model,
    description="Writes pytest tests for the submission and runs them in isolation.",
    instruction=TEST_RUNNER_PROMPT,
    tools=[run_tests],
    output_key="test_summary",
)

feedback_synthesizer_agent = LlmAgent(
    name="FeedbackSynthesizer",
    model=config.critic_model,
    description="Computes the weighted grade and writes the final review.",
    instruction=FEEDBACK_SYNTHESIZER_PROMPT,
    tools=[compute_grade],
    output_key=StateKeys.REVIEW_FEEDBACK,
)

code_review_pipeline = SequentialAgent(
    name="CodeReviewPipeline",
    description=(
        "Full Python code review: AST structure and complexity analysis, PEP 8 "
        "style checking, security scanning, generated tests run in an isolated "
        "subprocess, and a weighted grade with educational feedback."
    ),
    sub_agents=[
        code_analyzer_agent,
        style_checker_agent,
        security_reviewer_agent,
        test_runner_agent,
        feedback_synthesizer_agent,
    ],
)
