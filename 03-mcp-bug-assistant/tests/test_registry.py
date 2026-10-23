"""Tests for the tool registry's graceful degradation.

The behaviour under test is the one the reference samples get wrong: a backend
being absent must produce a *reported* absence, not a silent empty toolset and
not an import-time crash.
"""

from __future__ import annotations

from software_bug_assistant.config import config
from software_bug_assistant.tools import registry


def test_diagnostics_are_always_available():
    """No external dependency, so this group must never be missing."""
    group = registry._build_diagnostics()
    assert group.enabled is True
    assert len(group.tools) == 3


def test_github_group_is_disabled_without_a_token(monkeypatch):
    """The reference crashes here with TypeError from "Bearer " + None."""
    monkeypatch.setattr(config, "github_token", None)
    group = registry._build_github_mcp()
    assert group.enabled is False
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in group.reason
    assert group.tools == []


def test_github_group_builds_with_a_token(monkeypatch):
    monkeypatch.setattr(config, "github_token", "ghp_fake_token_for_tests")
    group = registry._build_github_mcp()
    assert group.enabled is True
    assert len(group.tools) == 1


def test_github_filter_is_an_allowlist_without_write_tools():
    """A bug assistant must not be able to close issues or push branches."""
    forbidden = {
        "create_issue",
        "update_issue",
        "create_pull_request",
        "merge_pull_request",
        "create_or_update_file",
        "delete_file",
        "push_files",
        "add_issue_comment",
    }
    assert not forbidden & set(config.github_tool_filter)
    assert "get_issue" in config.github_tool_filter


def test_toolbox_group_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "enable_toolbox", False)
    group = registry._build_toolbox()
    assert group.enabled is False
    assert "must not pretend otherwise" in group.reason


def test_toolbox_builds_without_a_running_server():
    """ToolboxToolset resolves lazily, which is what makes local dev possible."""
    group = registry._build_toolbox()
    # Either it builds (toolbox extra installed) or it reports why not.
    # What it must never do is raise.
    assert isinstance(group.enabled, bool)
    if not group.enabled:
        assert "pip install" in group.reason or "Toolbox" in group.reason


def test_stackoverflow_group_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "enable_stackoverflow", False)
    group = registry._build_stackoverflow()
    assert group.enabled is False


def test_stackoverflow_reason_mentions_the_quota(monkeypatch):
    """Anonymous quota is 300/day, which runs out fast; say so up front."""
    monkeypatch.setattr(config, "stackexchange_key", None)
    group = registry._build_stackoverflow()
    assert "300 requests/day" in group.reason

    monkeypatch.setattr(config, "stackexchange_key", "fake-key")
    group = registry._build_stackoverflow()
    assert "10,000" in group.reason


def test_google_search_group_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "enable_google_search", False)
    group = registry._build_google_search()
    assert group.enabled is False


def test_google_search_is_wrapped_in_an_agent_tool():
    """Gemini rejects a built-in tool alongside function tools on one agent."""
    group = registry._build_google_search()
    assert group.enabled is True
    assert type(group.tools[0]).__name__ == "AgentTool"


def test_build_tool_groups_returns_every_group():
    groups = registry.build_tool_groups()
    names = {g.name for g in groups}
    assert names == {
        "Diagnostics",
        "Ticket database",
        "GitHub",
        "Stack Overflow",
        "Google Search",
    }


def test_a_failing_builder_does_not_break_startup(monkeypatch):
    def exploding_builder():
        raise RuntimeError("backend on fire")

    monkeypatch.setattr(
        registry, "_BUILDERS", (registry._build_diagnostics, exploding_builder)
    )
    groups = registry.build_tool_groups()

    assert len(groups) == 2
    assert groups[0].enabled is True
    assert groups[1].enabled is False
    assert "backend on fire" in groups[1].reason


def test_collect_tools_skips_disabled_groups():
    groups = [
        registry.ToolGroup("A", "k", True, "r", tools=["t1", "t2"]),
        registry.ToolGroup("B", "k", False, "missing", tools=["t3"]),
    ]
    assert registry.collect_tools(groups) == ["t1", "t2"]


def test_inventory_names_what_is_missing():
    """This string goes into the instruction; it is how the agent knows."""
    groups = [
        registry.ToolGroup("Diagnostics", "function tools", True, "local"),
        registry.ToolGroup("Ticket database", "Toolbox", False, "server down"),
        registry.ToolGroup("GitHub", "MCP", False, "no token"),
    ]
    inventory = registry.describe_inventory(groups)

    assert "**Diagnostics**" in inventory
    assert "NOT available" in inventory
    assert "You do not have: Ticket database, GitHub" in inventory
    assert "Never substitute a guess" in inventory


def test_inventory_omits_the_warning_when_everything_is_available():
    groups = [registry.ToolGroup("Diagnostics", "function tools", True, "local")]
    inventory = registry.describe_inventory(groups)
    assert "You do not have" not in inventory
