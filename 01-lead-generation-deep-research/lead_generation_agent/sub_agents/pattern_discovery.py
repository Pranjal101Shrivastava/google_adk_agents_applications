"""Phase 1+2: plan the research, then discover the recurring buying signals.

Pipeline shape::

    ResearchPlanner
      -> ReferenceFinder (google_search)  -> ReferenceFormatter
      -> [ LoopAgent: ReferenceResearchOrchestrator
                      -> ReferenceFindingsConsolidator
                      -> ResearchCritic (exit_loop when sufficient) ]
      -> PatternSynthesizer -> PatternFormatter

The ``LoopAgent`` is what makes this *deep* research rather than one-shot
search: the critic inspects the evidence against the plan's success criteria
and only releases the loop when the evidence actually supports synthesis.
"""

from __future__ import annotations

from google.adk.agents import LlmAgent, LoopAgent, SequentialAgent
from google.adk.tools import exit_loop, google_search

from ..config import config
from ..constants import StateKeys
from ..prompts import (
    PATTERN_SYNTHESIZER_PROMPT,
    REFERENCE_FINDER_PROMPT,
    REFERENCE_FORMATTER_PROMPT,
    RESEARCH_CRITIC_PROMPT,
    RESEARCH_PLANNER_PROMPT,
)
from ..schemas import PatternReport, ReferenceCompanySet, ResearchPlan
from .orchestrators import (
    ReferenceFindingsConsolidator,
    ReferenceResearchOrchestrator,
)

# --- Phase 1: decompose the brief before searching -----------------------
research_planner_agent = LlmAgent(
    name="ResearchPlannerAgent",
    model=config.advanced_model,
    description="Decomposes the brief into searchable sub-questions and success criteria.",
    instruction=RESEARCH_PLANNER_PROMPT,
    output_schema=ResearchPlan,
    output_key=StateKeys.RESEARCH_PLAN,
)

# --- Phase 2a: find reference companies ----------------------------------
reference_finder_agent = LlmAgent(
    name="ReferenceFinderAgent",
    model=config.advanced_model,
    description="Finds companies that already made the move we want to predict.",
    instruction=REFERENCE_FINDER_PROMPT,
    tools=[google_search],
    output_key=StateKeys.REFERENCE_FINDER_OUTPUT,
)

reference_formatter_agent = LlmAgent(
    name="ReferenceFormatterAgent",
    model=config.fast_model,
    description="Structures the reference-company research notes into JSON.",
    instruction=REFERENCE_FORMATTER_PROMPT,
    output_schema=ReferenceCompanySet,
    output_key=StateKeys.REFERENCE_COMPANIES,
)

# --- Phase 2b: iterative deepening --------------------------------------
research_critic_agent = LlmAgent(
    name="ResearchCriticAgent",
    model=config.advanced_model,
    description=(
        "Gates the research loop: calls exit_loop once the evidence satisfies the "
        "plan's success criteria."
    ),
    instruction=(
        RESEARCH_CRITIC_PROMPT
        + "\n\nAfter writing your JSON verdict, if `is_sufficient` is true you MUST "
        "call the `exit_loop` tool to release the research loop. If it is false, do "
        "not call any tool."
    ),
    tools=[exit_loop],
    output_key=StateKeys.RESEARCH_CRITIQUE,
)

deep_research_loop = LoopAgent(
    name="DeepResearchLoop",
    description="Researches reference companies and repeats until the evidence is sufficient.",
    max_iterations=config.max_research_iterations,
    sub_agents=[
        ReferenceResearchOrchestrator(
            name="ReferenceResearchOrchestrator",
            description="Fans out validation and signal research over reference companies.",
        ),
        ReferenceFindingsConsolidator(
            name="ReferenceFindingsConsolidator",
            description="Collapses parallel research branches into one evidence blob.",
        ),
        research_critic_agent,
    ],
)

# --- Phase 2c: synthesise patterns --------------------------------------
pattern_synthesizer_agent = LlmAgent(
    name="PatternSynthesizerAgent",
    model=config.advanced_model,
    description="Distils per-company evidence into weighted, checkable predictors.",
    instruction=PATTERN_SYNTHESIZER_PROMPT,
    output_key=StateKeys.DISCOVERED_PATTERNS,
)

pattern_formatter_agent = LlmAgent(
    name="PatternFormatterAgent",
    model=config.fast_model,
    description="Structures the synthesised patterns into the PatternReport schema.",
    instruction=(
        "Convert the pattern analysis below into the `PatternReport` JSON schema. "
        "Copy the numbers verbatim - do not re-derive `evidence_count` or `weight`. "
        "Use market={country?} and industry={industry?}.\n\n"
        "Pattern analysis:\n{" + StateKeys.DISCOVERED_PATTERNS + "}\n\n"
        "Respond with JSON matching the schema and nothing else."
    ),
    output_schema=PatternReport,
    output_key=StateKeys.PATTERN_REPORT,
)

# --- The phase, assembled ------------------------------------------------
pattern_discovery_agent = SequentialAgent(
    name="pattern_discovery_agent",
    description=(
        "Deep-research pipeline that studies companies which already entered the "
        "target market and distils the recurring signals that preceded the move. "
        "Call this once the target country and industry are known."
    ),
    sub_agents=[
        research_planner_agent,
        reference_finder_agent,
        reference_formatter_agent,
        deep_research_loop,
        pattern_synthesizer_agent,
        pattern_formatter_agent,
    ],
)
