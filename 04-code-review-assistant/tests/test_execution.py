"""Tests for the sandboxed test runner.

These actually execute code, in subprocesses, under the real resource limits.
That is the point: the limits are the control, and a control nobody tests is a
control nobody has.
"""

from __future__ import annotations

import sys

import pytest

from code_review_assistant.config import config
from code_review_assistant.constants import StateKeys
from code_review_assistant.tools.execution import (
    _parse_pytest_output,
    execution_environment_report,
    run_fix_tests,
    run_tests,
)

ADD = "def add(a, b):\n    return a + b\n"

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="resource limits and setsid are enforced on Linux",
)


# --- Happy path -------------------------------------------------------
async def test_passing_tests_are_reported_as_passing(tool_context):
    tests = "from submission import add\n\ndef test_add():\n    assert add(2, 2) == 4\n"
    result = await run_tests(tests, tool_context, code_under_test=ADD)
    assert result["status"] == "success"
    assert result["all_passed"] is True
    assert result["passed"] == 1
    assert result["failed"] == 0


async def test_failing_tests_are_counted_separately(tool_context):
    tests = (
        "from submission import add\n\n"
        "def test_ok():\n    assert add(1, 1) == 2\n\n"
        "def test_broken():\n    assert add(1, 1) == 3\n"
    )
    result = await run_tests(tests, tool_context, code_under_test=ADD)
    assert result["status"] == "success"
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["all_passed"] is False


async def test_results_are_stored_in_state(tool_context):
    tests = "from submission import add\n\ndef test_add():\n    assert add(1, 1) == 2\n"
    await run_tests(tests, tool_context, code_under_test=ADD)
    assert tool_context.state[StateKeys.TEST_REPORT]["all_passed"] is True
    # The tests are kept so the fix phase can re-run the same ones.
    assert tool_context.state[StateKeys.GENERATED_TESTS] == tests


async def test_submission_is_importable_as_a_module(tool_context):
    """The prompt promises `from submission import ...` works; prove it does."""
    tests = (
        "import submission\n\n"
        "def test_module_import():\n    assert submission.add(3, 4) == 7\n"
    )
    result = await run_tests(tests, tool_context, code_under_test=ADD)
    assert result["all_passed"] is True


# --- Resource limits --------------------------------------------------
async def test_infinite_loop_is_killed_by_the_cpu_limit(tool_context, monkeypatch):
    monkeypatch.setattr(config, "execution_cpu_seconds", 2)
    monkeypatch.setattr(config, "execution_timeout_seconds", 30)
    tests = "from submission import spin\n\ndef test_spin():\n    spin()\n"
    result = await run_tests(
        tests, tool_context, code_under_test="def spin():\n    while True:\n        pass\n"
    )
    assert result["status"] == "error"
    assert result["error_type"] == "resource_limit"
    assert "not a passing test run" in result["message"]


async def test_wall_clock_timeout_is_reported(tool_context, monkeypatch):
    """A sleeping process burns no CPU, so only the wall clock catches it."""
    monkeypatch.setattr(config, "execution_timeout_seconds", 2)
    tests = "from submission import nap\n\ndef test_nap():\n    nap()\n"
    result = await run_tests(
        tests,
        tool_context,
        code_under_test="import time\n\ndef nap():\n    time.sleep(120)\n",
    )
    assert result["status"] == "error"
    assert result["error_type"] in ("timeout", "resource_limit")


async def test_memory_bomb_does_not_take_down_the_agent(tool_context):
    """A MemoryError inside the child is a test failure, not an agent crash."""
    tests = "from submission import grab\n\ndef test_grab():\n    assert grab() > 0\n"
    result = await run_tests(
        tests,
        tool_context,
        code_under_test="def grab():\n    x = [0] * (10 ** 10)\n    return len(x)\n",
    )
    # Either the allocation fails inside pytest (a reported failure) or the
    # process is killed (a resource_limit error). Both are acceptable; a
    # successful pass is not.
    assert not result.get("all_passed")


# --- Error classification ---------------------------------------------
async def test_no_tests_collected_is_distinguished(tool_context):
    result = await run_tests("x = 1\n", tool_context, code_under_test=ADD)
    assert result["error_type"] == "no_tests"


async def test_unrunnable_test_file_is_distinguished(tool_context):
    """A broken test file is the test author's problem, not the submission's."""
    result = await run_tests("this is not python(((", tool_context, code_under_test=ADD)
    assert result["error_type"] == "pytest_error"


async def test_empty_test_code_is_rejected(tool_context):
    assert (await run_tests("  ", tool_context, code_under_test=ADD))["status"] == "error"


async def test_missing_code_under_test_is_rejected(tool_context):
    result = await run_tests("def test_x():\n    pass\n", tool_context)
    assert result["status"] == "error"
    assert "analyze_code_structure" in result["message"]


async def test_execution_can_be_disabled(tool_context, monkeypatch):
    monkeypatch.setattr(config, "enable_code_execution", False)
    result = await run_tests("def test_x():\n    pass\n", tool_context, code_under_test=ADD)
    assert result["error_type"] == "disabled"
    assert "do not describe untested code as working" in result["message"]


# --- Environment scrubbing -------------------------------------------
async def test_child_cannot_see_the_agents_secrets(tool_context, monkeypatch):
    """Submitted code must not be able to read the agent's credentials."""
    monkeypatch.setenv("GOOGLE_API_KEY", "super-secret-key")
    monkeypatch.setenv("DATABASE_PASSWORD", "hunter2")

    tests = (
        "import os\n\n"
        "def test_no_leaked_credentials():\n"
        "    assert os.environ.get('GOOGLE_API_KEY') is None\n"
        "    assert os.environ.get('DATABASE_PASSWORD') is None\n"
    )
    result = await run_tests(tests, tool_context, code_under_test=ADD)
    assert result["all_passed"] is True, result.get("stdout")


# --- Fix verification -------------------------------------------------
async def test_fix_tests_reuse_the_original_tests(tool_context):
    """Re-running the same tests is what makes the fix verified rather than claimed."""
    tests = "from submission import add\n\ndef test_add():\n    assert add(2, 2) == 4\n"
    tool_context.state[StateKeys.GENERATED_TESTS] = tests
    tool_context.state[StateKeys.FIXED_CODE] = ADD

    result = await run_fix_tests(tool_context)

    assert result["all_passed"] is True
    assert tool_context.state[StateKeys.FIX_TEST_REPORT]["all_passed"] is True


async def test_fix_tests_refuse_to_run_without_a_fix(tool_context):
    tool_context.state[StateKeys.GENERATED_TESTS] = "def test_x():\n    pass\n"
    result = await run_fix_tests(tool_context)
    assert result["status"] == "error"
    assert "fixer must run" in result["message"]


async def test_fix_without_original_tests_is_reported_as_unverifiable(tool_context):
    tool_context.state[StateKeys.FIXED_CODE] = ADD
    result = await run_fix_tests(tool_context)
    assert result["status"] == "error"
    assert "unverified" in result["message"]


# --- Output parsing and reporting ------------------------------------
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("2 passed in 0.01s", {"passed": 2, "failed": 0}),
        ("1 failed, 3 passed in 0.12s", {"passed": 3, "failed": 1}),
        ("1 error in 0.03s", {"errors": 1}),
        ("2 failed, 1 passed, 3 skipped in 0.2s", {"failed": 2, "passed": 1, "skipped": 3}),
    ],
)
def test_pytest_summary_parsing(line, expected):
    counts = _parse_pytest_output(f"some output\n{line}\n")
    for key, value in expected.items():
        assert counts[key] == value


async def test_isolation_note_is_always_attached(tool_context):
    """The agent must be able to tell the user what the boundary actually is."""
    tests = "from submission import add\n\ndef test_add():\n    assert add(1, 1) == 2\n"
    result = await run_tests(tests, tool_context, code_under_test=ADD)
    assert "not a true sandbox" in result["isolation_note"]
    assert "gVisor" in result["isolation_note"]


def test_environment_report_is_honest_about_the_boundary():
    report = execution_environment_report()
    assert report["enabled"] is config.enable_code_execution
    assert "RLIMIT_CPU" in report["limits_applied"]
    assert "no seccomp filter" in report["isolation_boundary"]
