"""Structural tests over the agent graph.

Wiring bugs - a renamed state key, a pipeline step dropped during a refactor,
a sub-agent that lost its description - are the expensive failures in a
multi-agent system, because they surface as bad answers rather than crashes.
These tests pin the graph shape without calling a model.
"""

from __future__ import annotations

import asyncio

import pytest

from lead_generation_agent import root_agent
from lead_generation_agent.constants import StateKeys
from lead_generation_agent.sub_agents.lead_generation import lead_generation_agent
from lead_generation_agent.sub_agents.pattern_discovery import pattern_discovery_agent
from lead_generation_agent.tools.scoring import score_leads

from .conftest import FakeToolContext, tool_names


# --- Root agent ---------------------------------------------------------
def test_root_agent_exposes_the_three_phases_plus_user_choice():
    assert tool_names(root_agent) == [
        "get_user_choice",
        "intent_extractor_agent",
        "pattern_discovery_agent",
        "lead_generation_agent",
    ]


def test_root_agent_has_the_state_callbacks_attached():
    assert root_agent.before_agent_callback, "state seeding callback is not attached"
    assert root_agent.after_tool_callback, "state promotion callback is not attached"


def test_root_agent_instruction_forbids_fabrication():
    instruction = root_agent.instruction.lower()
    assert "never fabricate" in instruction
    assert "never skip pattern discovery" in instruction


# --- Pipelines ----------------------------------------------------------
def test_pattern_discovery_pipeline_order():
    assert [a.name for a in pattern_discovery_agent.sub_agents] == [
        "ResearchPlannerAgent",
        "ReferenceFinderAgent",
        "ReferenceFormatterAgent",
        "DeepResearchLoop",
        "PatternSynthesizerAgent",
        "PatternFormatterAgent",
    ]


def test_deep_research_loop_is_bounded_and_can_exit_early():
    loop = next(
        a for a in pattern_discovery_agent.sub_agents if a.name == "DeepResearchLoop"
    )
    assert loop.max_iterations and loop.max_iterations >= 1

    critic = loop.sub_agents[-1]
    assert critic.name == "ResearchCriticAgent"
    assert "exit_loop" in tool_names(critic), (
        "the critic cannot release the loop without exit_loop"
    )


def test_lead_generation_pipeline_order():
    assert [a.name for a in lead_generation_agent.sub_agents] == [
        "LeadFinderAgent",
        "LeadFormatterAgent",
        "LeadResearchOrchestrator",
        "LeadScorerAgent",
        "ReportCompilerAgent",
    ]


def test_scoring_happens_before_report_compilation():
    """The report must never be written from unscored candidates."""
    names = [a.name for a in lead_generation_agent.sub_agents]
    assert names.index("LeadScorerAgent") < names.index("ReportCompilerAgent")


def test_scorer_uses_the_deterministic_tool_not_a_prompt():
    scorer = next(
        a for a in lead_generation_agent.sub_agents if a.name == "LeadScorerAgent"
    )
    assert "score_leads" in tool_names(scorer)


# --- Model routing ------------------------------------------------------
@pytest.mark.parametrize(
    "pipeline", [pattern_discovery_agent, lead_generation_agent]
)
def test_every_llm_step_declares_a_model(pipeline):
    for agent in pipeline.sub_agents:
        if hasattr(agent, "model") and agent.__class__.__name__ == "LlmAgent":
            assert agent.model, f"{agent.name} has no model configured"


def test_formatter_steps_enforce_an_output_schema():
    """Structured hand-offs must be schema-enforced, not best-effort."""
    formatters = {
        "ReferenceFormatterAgent": pattern_discovery_agent,
        "PatternFormatterAgent": pattern_discovery_agent,
        "LeadFormatterAgent": lead_generation_agent,
    }
    for name, pipeline in formatters.items():
        agent = next(a for a in pipeline.sub_agents if a.name == name)
        assert agent.output_schema is not None, f"{name} has no output_schema"


def test_every_agent_tool_target_has_a_description():
    """Without a description the root model cannot choose the tool correctly."""
    for tool in root_agent.tools:
        agent = getattr(tool, "agent", None)
        if agent is not None:
            assert agent.description, f"{agent.name} has no description"


# --- score_leads against session state ---------------------------------
def test_score_leads_reads_state_and_writes_sorted_results(patterns):
    ctx = FakeToolContext(
        {
            StateKeys.PATTERN_REPORT: {"patterns": patterns},
            StateKeys.LEAD_CANDIDATES: {
                "candidates": [
                    {"company_name": "WeakCo"},
                    {"company_name": "StrongCo"},
                ]
            },
            StateKeys.lead_evidence(0): {
                "matched_patterns": ["Conference appearance"],
                "sources": ["https://example.com/weak"],
            },
            StateKeys.lead_evidence(1): {
                "matched_patterns": [
                    "Local leadership hire",
                    "Regional job postings",
                    "Trademark filing",
                ],
                "sources": [f"https://example.com/s{i}" for i in range(4)],
            },
        }
    )

    result = asyncio.run(score_leads(ctx))

    assert result["status"] == "success"
    assert result["total_count"] == 2
    # Sorted best-first.
    assert [lead["company_name"] for lead in result["scored_leads"]] == [
        "StrongCo",
        "WeakCo",
    ]
    assert result["scored_leads"][0]["tier"] == "A"
    assert ctx.state[StateKeys.SCORED_LEADS] == result["scored_leads"]
    assert ctx.state[StateKeys.SCORING_TIMESTAMP]


def test_score_leads_refuses_to_run_without_patterns():
    ctx = FakeToolContext(
        {StateKeys.LEAD_CANDIDATES: {"candidates": [{"company_name": "A"}]}}
    )
    result = asyncio.run(score_leads(ctx))
    assert result["status"] == "error"
    assert "pattern discovery" in result["message"].lower()


def test_score_leads_reports_missing_candidates(patterns):
    ctx = FakeToolContext({StateKeys.PATTERN_REPORT: {"patterns": patterns}})
    result = asyncio.run(score_leads(ctx))
    assert result["status"] == "error"
    assert "candidate" in result["message"].lower()


def test_candidate_without_evidence_scores_zero(patterns):
    ctx = FakeToolContext(
        {
            StateKeys.PATTERN_REPORT: {"patterns": patterns},
            StateKeys.LEAD_CANDIDATES: {"candidates": [{"company_name": "GhostCo"}]},
        }
    )
    result = asyncio.run(score_leads(ctx))
    assert result["scored_leads"][0]["fit_score"] == 0
    assert result["qualified_count"] == 0
