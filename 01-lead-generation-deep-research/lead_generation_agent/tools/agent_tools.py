"""The sub-agent pipelines, exposed to the root agent as callable tools.

Wrapping each phase in an ``AgentTool`` (rather than listing them as
``sub_agents``) keeps the root agent in control: it decides when a phase runs,
sees the phase's output, and can ask the user a question in between. Plain
delegation would hand the conversation away.
"""

from __future__ import annotations

from google.adk.tools.agent_tool import AgentTool

from ..sub_agents import (
    intent_extractor_agent,
    lead_generation_agent,
    pattern_discovery_agent,
)

agent_tools = [
    AgentTool(agent=intent_extractor_agent),
    AgentTool(agent=pattern_discovery_agent),
    AgentTool(agent=lead_generation_agent),
]

__all__ = ["agent_tools"]
