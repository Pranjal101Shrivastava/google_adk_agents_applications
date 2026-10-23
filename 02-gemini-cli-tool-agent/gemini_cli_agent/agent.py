"""Root agent for the Gemini CLI Tool Agent.

Run locally with::

    adk web .                  # from 02-gemini-cli-tool-agent/
    adk run gemini_cli_agent

Requires the Gemini CLI on PATH::

    npm install -g @google/gemini-cli

The graph::

    Hatch (root, LlmAgent)
      |- prepare_workspace / cleanup_workspace / list_active_workspaces
      |- list_workspace_files / read_workspace_file
      |- run_gemini_cli                            <- the CLI, as one tool call
      |- check_gemini_cli
      `- AgentTool(codebase_analysis_pipeline)     plan -> execute -> synthesise
"""

from __future__ import annotations

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool

from .config import config
from .prompts import ROOT_AGENT_INSTRUCTION
from .sub_agents import codebase_analysis_pipeline
from .tools import (
    check_gemini_cli,
    cleanup_workspace,
    list_active_workspaces,
    list_workspace_files,
    prepare_workspace,
    read_workspace_file,
    run_gemini_cli,
)

load_dotenv()

root_agent = LlmAgent(
    name="hatch_gemini_cli_agent",
    model=config.orchestrator_model,
    description=(
        "Staff-level software engineer that answers questions about unfamiliar "
        "codebases by driving the Gemini CLI against a sandboxed checkout."
    ),
    instruction=ROOT_AGENT_INSTRUCTION,
    tools=[
        prepare_workspace,
        list_workspace_files,
        read_workspace_file,
        run_gemini_cli,
        check_gemini_cli,
        list_active_workspaces,
        cleanup_workspace,
        AgentTool(agent=codebase_analysis_pipeline),
    ],
)

__all__ = ["root_agent"]
