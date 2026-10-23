"""Root agent for the MCP Bug Assistant.

Run locally with::

    adk web .                      # from 03-mcp-bug-assistant/
    adk run software_bug_assistant

The agent is a single ``LlmAgent`` holding five kinds of tool, which is the
point of the sample: it shows every tool integration path ADK offers converging
on one assistant.

===========================  ==========================================
Tool kind                    What it is here
===========================  ==========================================
Function tools               parse_stack_trace, triage_priority,
                             get_current_date - local, deterministic
Built-in tool                google_search, wrapped in an AgentTool
Third-party API tool         search_stackoverflow (Stack Exchange API)
Google Cloud tool            MCP Toolbox for Databases -> Postgres
MCP server tool              GitHub MCP over streamable HTTP, read-only
===========================  ==========================================

Any of the last four can be absent. ``tools/registry.py`` builds each group
independently and writes the resulting inventory into the instruction, so the
agent always knows what it can actually do.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from google.adk.agents import LlmAgent

from .config import config
from .prompts import AGENT_INSTRUCTION_TEMPLATE
from .tools.registry import build_tool_groups, collect_tools, describe_inventory

load_dotenv()

logger = logging.getLogger(__name__)

# Build the tool groups once, at import, and report what came up.
tool_groups = build_tool_groups()
tools = collect_tools(tool_groups)
tool_inventory = describe_inventory(tool_groups)

_enabled = [g.name for g in tool_groups if g.enabled]
_disabled = [g.name for g in tool_groups if not g.enabled]
logger.info("Bug assistant tool groups enabled: %s", ", ".join(_enabled) or "none")
if _disabled:
    logger.warning(
        "Bug assistant tool groups unavailable: %s", ", ".join(_disabled)
    )

root_agent = LlmAgent(
    name="software_bug_assistant",
    model=config.agent_model,
    description=(
        "On-call engineering assistant that triages bug reports, finds known "
        "duplicates in the ticket database, searches Stack Overflow, GitHub and "
        "the web for causes, and manages tickets."
    ),
    # The inventory is substituted at build time rather than resolved from
    # session state: it is a property of the deployment, not of the
    # conversation, and it must not change mid-session.
    instruction=AGENT_INSTRUCTION_TEMPLATE.format(tool_inventory=tool_inventory),
    tools=tools,
)

__all__ = ["root_agent", "tool_groups", "tool_inventory"]
