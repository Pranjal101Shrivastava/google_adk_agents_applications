"""Tool registry with graceful degradation.

This is the production concern that the reference samples skip. They build
every toolset at import time inside a bare ``try/except``, so:

* a Toolbox server that is down turns into a silently empty toolset, and the
  agent then invents ticket data because it has no tool to look it up with;
* a missing ``GITHUB_PERSONAL_ACCESS_TOKEN`` raises ``TypeError`` from
  ``"Bearer " + None`` *during import*, which fails as an unrelated-looking
  crash rather than a configuration message.

Instead, each tool group is assembled independently and the result is an
**inventory**: which groups are enabled, which are not, and why. The agent is
given that inventory in its instruction, so it can tell the user "I have no
ticket database configured" instead of guessing at ticket numbers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool

from ..config import config
from .diagnostics import get_current_date, parse_stack_trace, triage_priority
from .stackoverflow import search_stackoverflow

logger = logging.getLogger(__name__)


@dataclass
class ToolGroup:
    """One logical group of tools and whether it is available."""

    name: str
    kind: str
    enabled: bool
    reason: str
    tools: list[Any] = field(default_factory=list)

    def describe(self) -> str:
        """One line for the agent's instruction."""
        mark = "available" if self.enabled else "NOT available"
        return f"- **{self.name}** ({self.kind}): {mark} - {self.reason}"


# --------------------------------------------------------------------------
# Group builders. Each returns a ToolGroup and never raises.
# --------------------------------------------------------------------------
def _build_diagnostics() -> ToolGroup:
    """Local function tools. Always available - no external dependency."""
    return ToolGroup(
        name="Diagnostics",
        kind="function tools",
        enabled=True,
        reason=(
            "parse_stack_trace, triage_priority and get_current_date run locally "
            "and are always available"
        ),
        tools=[parse_stack_trace, triage_priority, get_current_date],
    )


def _build_google_search() -> ToolGroup:
    """Built-in Google Search, wrapped in an AgentTool.

    Gemini will not accept a built-in tool alongside arbitrary function tools
    on the same agent, so the search tool lives on its own single-purpose agent
    and is exposed here as an ``AgentTool``. That is the supported pattern, not
    a workaround.
    """
    if not config.enable_google_search:
        return ToolGroup(
            name="Google Search",
            kind="built-in tool",
            enabled=False,
            reason="disabled by ENABLE_GOOGLE_SEARCH=false",
        )

    try:
        from google.adk.tools import google_search

        search_agent = LlmAgent(
            name="web_search_agent",
            model=config.search_model,
            description=(
                "Searches the public web for error messages, release notes, "
                "advisories and library documentation."
            ),
            instruction=(
                "You are a web search specialist supporting a software bug "
                "assistant.\n\n"
                "Search for what you were asked and report what you actually "
                "found: the finding, its source URL, and its date when the page "
                "shows one. Dates matter here - a fix described for version 2.1 "
                "is irrelevant to someone on 4.0.\n\n"
                "Prefer primary sources: project changelogs, GitHub issues, "
                "security advisories, official documentation. Prefer a blog post "
                "over nothing, but say that is what it is.\n\n"
                "If you find nothing relevant, say so plainly. Do not pad the "
                "answer with general advice about the error class."
            ),
            tools=[google_search],
        )
        return ToolGroup(
            name="Google Search",
            kind="built-in tool",
            enabled=True,
            reason="web search for error messages, advisories and release notes",
            tools=[AgentTool(agent=search_agent)],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not build the Google Search tool: %s", exc)
        return ToolGroup(
            name="Google Search",
            kind="built-in tool",
            enabled=False,
            reason=f"could not be initialised: {exc}",
        )


def _build_stackoverflow() -> ToolGroup:
    """Third-party API tool."""
    if not config.enable_stackoverflow:
        return ToolGroup(
            name="Stack Overflow",
            kind="third-party API tool",
            enabled=False,
            reason="disabled by ENABLE_STACKOVERFLOW=false",
        )

    quota_note = (
        "authenticated, 10,000 requests/day"
        if config.stackexchange_key
        else "anonymous, 300 requests/day - set STACKEXCHANGE_KEY to raise it"
    )
    return ToolGroup(
        name="Stack Overflow",
        kind="third-party API tool",
        enabled=True,
        reason=f"search_stackoverflow against the Stack Exchange API ({quota_note})",
        tools=[search_stackoverflow],
    )


def _build_toolbox() -> ToolGroup:
    """MCP Toolbox for Databases - the ticket database.

    ``ToolboxToolset`` resolves its toolset lazily, on first use, so
    constructing it does not require the Toolbox server to be up. That is what
    makes a local run possible with no database at all.
    """
    if not config.enable_toolbox:
        return ToolGroup(
            name="Ticket database",
            kind="MCP Toolbox for Databases",
            enabled=False,
            reason=(
                "disabled by ENABLE_TOOLBOX=false - you cannot read or write "
                "tickets, and must not pretend otherwise"
            ),
        )

    try:
        from google.adk.tools.toolbox_toolset import ToolboxToolset

        toolset = ToolboxToolset(
            server_url=config.mcp_toolbox_url,
            toolset_name=config.mcp_toolbox_toolset,
        )
        return ToolGroup(
            name="Ticket database",
            kind="MCP Toolbox for Databases",
            enabled=True,
            reason=(
                f"toolset {config.mcp_toolbox_toolset!r} from the Toolbox server at "
                f"{config.mcp_toolbox_url} - semantic ticket search, lookup by id, "
                "assignee, status and priority, plus create and update"
            ),
            tools=[toolset],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not build the Toolbox toolset: %s", exc)
        return ToolGroup(
            name="Ticket database",
            kind="MCP Toolbox for Databases",
            enabled=False,
            reason=(
                f"could not be initialised: {exc}. Install the extra with "
                "`pip install 'google-adk[toolbox]'`, then check MCP_TOOLBOX_URL "
                "and that the Toolbox server is running"
            ),
        )


def _build_github_mcp() -> ToolGroup:
    """GitHub MCP server over streamable HTTP, filtered to read-only tools."""
    if not config.github_enabled:
        return ToolGroup(
            name="GitHub",
            kind="MCP server (streamable HTTP)",
            enabled=False,
            reason=(
                "GITHUB_PERSONAL_ACCESS_TOKEN is not set - you cannot read issues, "
                "pull requests or code from GitHub"
            ),
        )

    try:
        from google.adk.tools import McpToolset
        from google.adk.tools.mcp_tool import StreamableHTTPConnectionParams

        toolset = McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=config.github_mcp_url,
                headers={"Authorization": f"Bearer {config.github_token}"},
            ),
            # Allowlist, not a denylist: the GitHub MCP server also exposes
            # write tools, and a bug assistant has no business closing issues
            # or pushing branches on its own.
            tool_filter=list(config.github_tool_filter),
        )
        return ToolGroup(
            name="GitHub",
            kind="MCP server (streamable HTTP)",
            enabled=True,
            reason=(
                "read-only GitHub tools: "
                + ", ".join(config.github_tool_filter)
            ),
            tools=[toolset],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not build the GitHub MCP toolset: %s", exc)
        return ToolGroup(
            name="GitHub",
            kind="MCP server (streamable HTTP)",
            enabled=False,
            reason=(
                f"could not be initialised: {exc}. The `mcp` package is required: "
                "pip install mcp"
            ),
        )


_BUILDERS = (
    _build_diagnostics,
    _build_toolbox,
    _build_github_mcp,
    _build_stackoverflow,
    _build_google_search,
)


def build_tool_groups() -> list[ToolGroup]:
    """Assemble every tool group, independently of each other's success."""
    groups = []
    for builder in _BUILDERS:
        try:
            groups.append(builder())
        except Exception as exc:  # noqa: BLE001 - a builder must never break startup
            logger.exception("Tool group builder %s failed", builder.__name__)
            groups.append(
                ToolGroup(
                    name=builder.__name__,
                    kind="unknown",
                    enabled=False,
                    reason=f"builder raised: {exc}",
                )
            )
    return groups


def collect_tools(groups: list[ToolGroup]) -> list[Any]:
    """Flatten the enabled groups into the agent's tool list."""
    tools: list[Any] = []
    for group in groups:
        if group.enabled:
            tools.extend(group.tools)
    return tools


def describe_inventory(groups: list[ToolGroup]) -> str:
    """Render the inventory for injection into the agent's instruction."""
    lines = [group.describe() for group in groups]
    missing = [g.name for g in groups if not g.enabled]
    if missing:
        lines.append(
            "\n**You do not have: "
            + ", ".join(missing)
            + ".** When a request needs one of these, say which integration is "
            "missing and what would enable it. Never substitute a guess for a "
            "tool you do not have."
        )
    return "\n".join(lines)
