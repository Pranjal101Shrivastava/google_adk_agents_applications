"""Session-state keys for the Gemini CLI tool agent."""

from __future__ import annotations


class StateKeys:
    """Canonical session-state key names."""

    # Intake
    REPO_REQUEST = "repo_request"
    ACTIVE_WORKSPACE = "active_workspace"

    # Planning and execution
    CLI_PLAN = "cli_plan"
    CLI_RUNS = "cli_runs"
    LAST_CLI_OUTPUT = "last_cli_output"

    # Output
    ANALYSIS_REPORT = "analysis_report"
