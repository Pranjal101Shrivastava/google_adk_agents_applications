"""Root agent for the Deep Research Lead Generation Agent.

Run locally with::

    adk web .          # from 01-lead-generation-deep-research/
    adk run lead_generation_agent

The graph::

    Scout (root, LlmAgent)
      |- get_user_choice                      ask the user to pick
      |- AgentTool(intent_extractor_agent)    structure the brief
      |- AgentTool(pattern_discovery_agent)   plan -> search -> loop -> synthesise
      `- AgentTool(lead_generation_agent)     find -> verify -> score -> report
"""

from __future__ import annotations

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.tools import get_user_choice

from .callbacks import after_tool_run, before_agent_run
from .config import config
from .prompts import ROOT_AGENT_INSTRUCTION
from .tools.agent_tools import agent_tools

# Load a local .env when present; in Cloud Run / Agent Engine the real
# environment already carries these values and load_dotenv is a no-op.
load_dotenv()

root_agent = LlmAgent(
    name="scout_lead_research_agent",
    model=config.advanced_model,
    description=(
        "Deep-research B2B lead generation analyst. Learns the signals that "
        "preceded comparable deals, then finds and scores companies showing the "
        "same signals today."
    ),
    instruction=ROOT_AGENT_INSTRUCTION,
    tools=[get_user_choice, *agent_tools],
    before_agent_callback=before_agent_run,
    after_tool_callback=after_tool_run,
)

__all__ = ["root_agent"]
