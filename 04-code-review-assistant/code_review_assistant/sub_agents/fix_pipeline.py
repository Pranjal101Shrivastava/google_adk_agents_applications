"""The fix pipeline: attempt, verify, validate, repeat, then report.

::

    LoopAgent (max_fix_attempts):
        CodeFixer     -> rewrite the module
        FixTestRunner -> re-run the ORIGINAL tests against it
        FixValidator  -> gate; calls exit_loop only when genuinely fixed
    FixSynthesizer    -> present the result honestly

The loop exists because a first fix often breaks something else, and the
validator is what makes the loop meaningful: it compares against the original
tests, so a fix cannot pass by moving the goalposts.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from google.adk.tools import exit_loop

from ..config import config
from ..constants import StateKeys
from ..prompts import (
    CODE_FIXER_PROMPT,
    FIX_SYNTHESIZER_PROMPT,
    FIX_TEST_RUNNER_PROMPT,
    FIX_VALIDATOR_PROMPT,
)
from ..tools.execution import run_fix_tests
from ..tools.grading import compare_grades

code_fixer_agent = LlmAgent(
    name="CodeFixer",
    model=config.critic_model,
    description="Rewrites the submission to address the review's findings.",
    instruction=CODE_FIXER_PROMPT,
    output_key=StateKeys.FIXED_CODE,
)

fix_test_runner_agent = LlmAgent(
    name="FixTestRunner",
    model=config.worker_model,
    description="Re-runs the review phase's tests, unchanged, against the fixed code.",
    instruction=FIX_TEST_RUNNER_PROMPT,
    tools=[run_fix_tests],
    output_key="fix_test_summary",
)

fix_validator_agent = LlmAgent(
    name="FixValidator",
    model=config.critic_model,
    description=(
        "Gates the fix loop: calls exit_loop only when the fix is verified and "
        "nothing regressed."
    ),
    instruction=FIX_VALIDATOR_PROMPT,
    tools=[compare_grades, exit_loop],
    output_key=StateKeys.FIX_VALIDATION,
)

fix_attempt_loop = LoopAgent(
    name="FixAttemptLoop",
    description="Fix, verify against the original tests, validate; repeat until clean.",
    max_iterations=config.max_fix_attempts,
    sub_agents=[
        code_fixer_agent,
        fix_test_runner_agent,
        fix_validator_agent,
    ],
)

fix_synthesizer_agent = LlmAgent(
    name="FixSynthesizer",
    model=config.critic_model,
    description="Presents the fixed code, what changed, and what was verified.",
    instruction=FIX_SYNTHESIZER_PROMPT,
    output_key=StateKeys.FIX_SUMMARY,
)

code_fix_pipeline = SequentialAgent(
    name="CodeFixPipeline",
    description=(
        "Iteratively fixes the issues a review found, re-verifying against the "
        "original generated tests after each attempt, then reports what changed "
        "and what was actually verified. Requires CodeReviewPipeline to have run."
    ),
    sub_agents=[fix_attempt_loop, fix_synthesizer_agent],
)
