"""Root agent for the Code Review Assistant.

Run locally with::

    adk web .                      # from 04-code-review-assistant/
    adk run code_review_assistant

The graph::

    Lint (root, LlmAgent)
      |- CodeReviewPipeline   SequentialAgent, 5 stages
      `- CodeFixPipeline      LoopAgent(fix -> verify -> validate) + synthesiser

These are ``sub_agents`` rather than ``AgentTool``s, deliberately: a review is
a hand-off, not a lookup. Once the user has pasted code, the pipeline owns the
turn and its output *is* the answer; the root agent has nothing to add on top.
"""

from __future__ import annotations

from dotenv import load_dotenv
from google.adk.agents import LlmAgent

from .config import config
from .prompts import ROOT_AGENT_INSTRUCTION
from .sub_agents import code_fix_pipeline, code_review_pipeline

load_dotenv()

root_agent = LlmAgent(
    name="code_review_assistant",
    model=config.worker_model,
    description=(
        "Senior-engineer code reviewer for Python. Analyses structure and "
        "complexity, checks PEP 8, scans for security problems, generates and "
        "runs tests, grades deterministically, and can fix what it found."
    ),
    instruction=ROOT_AGENT_INSTRUCTION,
    sub_agents=[code_review_pipeline, code_fix_pipeline],
    output_key="assistant_response",
)

__all__ = ["root_agent"]
