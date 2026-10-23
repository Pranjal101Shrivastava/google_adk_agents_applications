"""Phase 3: find candidates that have not moved yet, verify, score, report.

Pipeline shape::

    LeadFinder (google_search) -> LeadFormatter
      -> LeadResearchOrchestrator (parallel per-candidate verification)
      -> LeadScorer (deterministic function tool)
      -> ReportCompiler

Scoring deliberately sits in a function tool rather than in a prompt, so the
same evidence always produces the same tier.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.tools import google_search

from ..config import config
from ..constants import StateKeys
from ..prompts import (
    LEAD_FINDER_PROMPT,
    LEAD_FORMATTER_PROMPT,
    REPORT_COMPILER_PROMPT,
)
from ..schemas import LeadCandidateSet
from ..tools.scoring import score_leads
from .orchestrators import LeadResearchOrchestrator

lead_finder_agent = LlmAgent(
    name="LeadFinderAgent",
    model=config.advanced_model,
    description="Finds foreign companies showing the discovered signals but no announcement yet.",
    instruction=LEAD_FINDER_PROMPT,
    tools=[google_search],
    output_key=StateKeys.LEAD_FINDER_OUTPUT,
)

lead_formatter_agent = LlmAgent(
    name="LeadFormatterAgent",
    model=config.fast_model,
    description="Structures the candidate notes into the LeadCandidateSet schema.",
    instruction=LEAD_FORMATTER_PROMPT,
    output_schema=LeadCandidateSet,
    output_key=StateKeys.LEAD_CANDIDATES,
)

lead_scorer_agent = LlmAgent(
    name="LeadScorerAgent",
    model=config.fast_model,
    description="Runs the deterministic scorer over the verified candidates.",
    instruction=(
        "Call the `score_leads` tool exactly once. It reads the pattern report and "
        "the per-candidate evidence from session state and writes the scored leads "
        "back to state.\n\n"
        "Then report the tool's `summary` verbatim and stop. Do not re-score, "
        "re-rank, or comment on individual leads - the next agent writes the "
        "report. If the tool returns an error, report the error message as-is."
    ),
    tools=[score_leads],
)

report_compiler_agent = LlmAgent(
    name="ReportCompilerAgent",
    model=config.advanced_model,
    description="Writes the final tiered lead report from the scored leads.",
    instruction=REPORT_COMPILER_PROMPT,
    output_key=StateKeys.FINAL_REPORT,
)

lead_generation_agent = SequentialAgent(
    name="lead_generation_agent",
    description=(
        "Finds candidate companies that match the discovered patterns but have not "
        "announced a move yet, verifies each one against the patterns with sourced "
        "evidence, scores them deterministically and returns a tiered report. "
        "Requires pattern discovery to have run first."
    ),
    sub_agents=[
        lead_finder_agent,
        lead_formatter_agent,
        LeadResearchOrchestrator(
            name="LeadResearchOrchestrator",
            description="Fans out pattern verification over the lead candidates.",
        ),
        lead_scorer_agent,
        report_compiler_agent,
    ],
)
