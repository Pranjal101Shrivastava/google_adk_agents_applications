"""Structural tests over the review and fix pipelines."""

from __future__ import annotations

import pytest

from code_review_assistant import root_agent
from code_review_assistant.config import config
from code_review_assistant.sub_agents import code_fix_pipeline, code_review_pipeline


def squashed(text: str) -> str:
    return " ".join(text.lower().split())


def tool_names(agent) -> list[str]:
    return [
        getattr(t, "name", None) or getattr(t, "__name__", None) or type(t).__name__
        for t in (getattr(agent, "tools", None) or [])
    ]


# --- Root -------------------------------------------------------------
def test_root_delegates_to_both_pipelines():
    assert [a.name for a in root_agent.sub_agents] == [
        "CodeReviewPipeline",
        "CodeFixPipeline",
    ]


def test_root_is_told_not_to_reformat_the_submission():
    """Reviewing code you silently cleaned up first reviews the wrong code."""
    instruction = squashed(root_agent.instruction)
    assert "pass the code **exactly** as given" in instruction
    assert "do not reformat it" in instruction


def test_root_may_not_alter_the_grade():
    assert "never change the grade" in squashed(root_agent.instruction)


def test_root_forbids_claiming_untested_code_works():
    instruction = squashed(root_agent.instruction)
    assert "never claim the tests pass unless a test run said so" in instruction


def test_root_forbids_implying_a_security_sign_off():
    assert "never present a clean security scan as a security sign-off" in squashed(
        root_agent.instruction
    )


# --- Review pipeline --------------------------------------------------
def test_review_pipeline_order():
    assert [a.name for a in code_review_pipeline.sub_agents] == [
        "CodeAnalyzer",
        "StyleChecker",
        "SecurityReviewer",
        "TestRunner",
        "FeedbackSynthesizer",
    ]


def test_analyzer_runs_first_because_it_populates_state():
    """Every later step reads the code from state; the analyzer puts it there."""
    first = code_review_pipeline.sub_agents[0]
    assert first.name == "CodeAnalyzer"
    assert "analyze_code_structure" in tool_names(first)


def test_each_review_stage_holds_exactly_its_own_tool():
    expected = {
        "CodeAnalyzer": "analyze_code_structure",
        "StyleChecker": "check_code_style",
        "SecurityReviewer": "review_security",
        "TestRunner": "run_tests",
        "FeedbackSynthesizer": "compute_grade",
    }
    for agent in code_review_pipeline.sub_agents:
        assert tool_names(agent) == [expected[agent.name]], (
            f"{agent.name} holds {tool_names(agent)}"
        )


def test_only_the_synthesizer_grades():
    """Grading from any earlier stage would run before the evidence exists."""
    for agent in code_review_pipeline.sub_agents[:-1]:
        assert "compute_grade" not in tool_names(agent)


def test_test_runner_uses_the_reasoning_model():
    """Writing tests that probe boundaries is the hardest call in the pipeline."""
    by_name = {a.name: a for a in code_review_pipeline.sub_agents}
    assert by_name["TestRunner"].model == config.critic_model
    assert by_name["FeedbackSynthesizer"].model == config.critic_model
    assert by_name["StyleChecker"].model == config.worker_model


# --- Fix pipeline -----------------------------------------------------
def test_fix_pipeline_is_a_loop_then_a_synthesiser():
    assert [a.name for a in code_fix_pipeline.sub_agents] == [
        "FixAttemptLoop",
        "FixSynthesizer",
    ]


def test_fix_loop_order_is_fix_verify_validate():
    loop = code_fix_pipeline.sub_agents[0]
    assert [a.name for a in loop.sub_agents] == [
        "CodeFixer",
        "FixTestRunner",
        "FixValidator",
    ]


def test_fix_loop_is_bounded():
    loop = code_fix_pipeline.sub_agents[0]
    assert loop.max_iterations == config.max_fix_attempts
    assert 1 <= loop.max_iterations <= 10


def test_only_the_validator_can_release_the_loop():
    loop = code_fix_pipeline.sub_agents[0]
    for agent in loop.sub_agents:
        names = tool_names(agent)
        if agent.name == "FixValidator":
            assert "exit_loop" in names
            assert "compare_grades" in names
        else:
            assert "exit_loop" not in names


def test_fix_runner_reuses_the_original_tests():
    """run_fix_tests takes no test argument; it reads the review's tests."""
    loop = code_fix_pipeline.sub_agents[0]
    runner = next(a for a in loop.sub_agents if a.name == "FixTestRunner")
    assert tool_names(runner) == ["run_fix_tests"]


def test_fixer_is_told_not_to_rewrite_the_tests():
    """Changing the tests to match the code is how a bug becomes permanent."""
    loop = code_fix_pipeline.sub_agents[0]
    fixer = next(a for a in loop.sub_agents if a.name == "CodeFixer")
    assert "do **not** change the tests to match the code" in squashed(fixer.instruction)


def test_fixer_is_told_not_to_refactor_unasked():
    loop = code_fix_pipeline.sub_agents[0]
    fixer = next(a for a in loop.sub_agents if a.name == "CodeFixer")
    assert "change only what the review flagged" in squashed(fixer.instruction)


def test_validator_must_not_exit_on_an_unverified_fix():
    loop = code_fix_pipeline.sub_agents[0]
    validator = next(a for a in loop.sub_agents if a.name == "FixValidator")
    instruction = squashed(validator.instruction)
    assert "do **not** call `exit_loop` on the assumption it is fine" in instruction
    assert "regression" in instruction


def test_synthesizer_must_not_present_a_regression_as_success():
    synthesizer = code_fix_pipeline.sub_agents[-1]
    instruction = squashed(synthesizer.instruction)
    assert "never present a regression as a success" in instruction
    assert "never claim a fix works unless the tests say so" in instruction


# --- Descriptions and preconditions ----------------------------------
def test_fix_pipeline_description_names_its_precondition():
    assert "CodeReviewPipeline" in code_fix_pipeline.description


@pytest.mark.parametrize("pipeline", [code_review_pipeline, code_fix_pipeline])
def test_every_stage_has_a_description(pipeline):
    for agent in pipeline.sub_agents:
        assert agent.description, f"{agent.name} has no description"


def test_every_stage_prompt_names_the_tool_it_holds():
    """A stage whose prompt never names its tool tends not to call it."""
    for agent in code_review_pipeline.sub_agents:
        names = tool_names(agent)
        assert names, f"{agent.name} has no tools"
        for name in names:
            assert name in agent.instruction, (
                f"{agent.name}'s prompt never names its tool {name!r}"
            )


def test_reporting_stages_require_line_numbers():
    """A finding without a line number is not a checkable finding."""
    by_name = {a.name: a for a in code_review_pipeline.sub_agents}
    for stage in ("CodeAnalyzer", "SecurityReviewer", "FeedbackSynthesizer"):
        assert "line number" in squashed(by_name[stage].instruction), stage


def test_test_runner_is_warned_against_cementing_the_bug():
    """A test asserting the current buggy output makes the bug permanent."""
    by_name = {a.name: a for a in code_review_pipeline.sub_agents}
    instruction = squashed(by_name["TestRunner"].instruction)
    assert "test the behaviour the code should have, not the behaviour it has" in instruction
    assert "never describe code as working because you did not manage to test it" in instruction
