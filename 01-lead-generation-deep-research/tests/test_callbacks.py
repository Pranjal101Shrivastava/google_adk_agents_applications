"""Tests for the session-state callbacks."""

from __future__ import annotations

from lead_generation_agent.callbacks import after_tool_run, before_agent_run
from lead_generation_agent.config import config
from lead_generation_agent.constants import StateKeys

from .conftest import FakeTool


def test_before_agent_run_seeds_defaults(callback_context):
    before_agent_run(callback_context)
    state = callback_context.state

    assert state[StateKeys.STAGE] == "clarify"
    assert state[StateKeys.COUNTRY] == ""
    assert state[StateKeys.PATTERNS_AVAILABLE] is False
    assert state[StateKeys.PATTERN_COMPANY_COUNT] == config.default_pattern_companies
    assert state[StateKeys.LEAD_CANDIDATE_COUNT] == config.default_lead_candidates
    assert "UTC" in state[StateKeys.CURRENT_TIME]


def test_before_agent_run_preserves_existing_brief(callback_context):
    callback_context.state[StateKeys.COUNTRY] = "Thailand"
    callback_context.state[StateKeys.STAGE] = "lead_generation"

    before_agent_run(callback_context)

    assert callback_context.state[StateKeys.COUNTRY] == "Thailand"
    assert callback_context.state[StateKeys.STAGE] == "lead_generation"


def test_before_agent_run_refreshes_the_timestamp(callback_context):
    callback_context.state[StateKeys.CURRENT_TIME] = "1999-01-01 00:00:00 UTC"
    before_agent_run(callback_context)
    assert callback_context.state[StateKeys.CURRENT_TIME] != "1999-01-01 00:00:00 UTC"


def test_intent_extraction_is_promoted_to_flat_state(tool_context):
    tool_context.state[StateKeys.INTENT_EXTRACTION] = {
        "country": "Thailand",
        "industry": "fintech",
        "buyer_persona": "Head of Payments",
        "value_proposition": "cross-border payment rails",
        "stage": "pattern_discovery",
        "intent": "find_patterns",
        "confidence": 0.9,
        "missing_fields": [],
        "reasoning": "both fields present",
    }

    after_tool_run(FakeTool("intent_extractor_agent"), {}, tool_context, None)

    assert tool_context.state[StateKeys.COUNTRY] == "Thailand"
    assert tool_context.state[StateKeys.INDUSTRY] == "fintech"
    assert tool_context.state[StateKeys.BUYER_PERSONA] == "Head of Payments"
    assert tool_context.state[StateKeys.STAGE] == "pattern_discovery"


def test_blank_required_field_forces_clarify_stage(tool_context):
    """The extractor may over-claim; the callback is the backstop."""
    tool_context.state[StateKeys.INTENT_EXTRACTION] = {
        "country": "Thailand",
        "industry": None,
        "stage": "lead_generation",
        "intent": "find_leads",
        "confidence": 0.4,
        "missing_fields": ["industry"],
        "reasoning": "industry unknown",
    }

    after_tool_run(FakeTool("intent_extractor_agent"), {}, tool_context, None)

    assert tool_context.state[StateKeys.STAGE] == "clarify"


def test_intent_extraction_does_not_blank_known_values(tool_context):
    tool_context.state[StateKeys.COUNTRY] = "Thailand"
    tool_context.state[StateKeys.INDUSTRY] = "fintech"
    tool_context.state[StateKeys.INTENT_EXTRACTION] = {
        "country": None,
        "industry": None,
        "stage": "follow_up",
        "intent": "find_leads",
        "confidence": 0.8,
        "missing_fields": [],
        "reasoning": "carry forward",
    }

    after_tool_run(FakeTool("intent_extractor_agent"), {}, tool_context, None)

    assert tool_context.state[StateKeys.COUNTRY] == "Thailand"
    assert tool_context.state[StateKeys.INDUSTRY] == "fintech"
    assert tool_context.state[StateKeys.STAGE] == "follow_up"


def test_off_schema_intent_output_is_ignored_not_crashed(tool_context):
    tool_context.state[StateKeys.INTENT_EXTRACTION] = "I think you want Thailand"

    after_tool_run(FakeTool("intent_extractor_agent"), {}, tool_context, None)

    assert tool_context.state.get(StateKeys.COUNTRY) is None


def test_pattern_discovery_success_unlocks_lead_generation(tool_context, patterns):
    tool_context.state[StateKeys.PATTERN_REPORT] = {
        "market": "Thailand",
        "industry": "fintech",
        "companies_studied": 5,
        "patterns": patterns,
        "caveats": [],
    }

    after_tool_run(FakeTool("pattern_discovery_agent"), {}, tool_context, None)

    assert tool_context.state[StateKeys.PATTERNS_AVAILABLE] is True
    assert tool_context.state[StateKeys.STAGE] == "lead_generation"
    assert tool_context.state[StateKeys.COMPANIES_STUDIED] == 5


def test_empty_pattern_report_does_not_unlock_lead_generation(tool_context):
    tool_context.state[StateKeys.PATTERN_REPORT] = {
        "market": "Thailand",
        "industry": "fintech",
        "companies_studied": 0,
        "patterns": [],
        "caveats": ["no reference companies found"],
    }

    after_tool_run(FakeTool("pattern_discovery_agent"), {}, tool_context, None)

    assert tool_context.state[StateKeys.PATTERNS_AVAILABLE] is False
    assert tool_context.state[StateKeys.STAGE] == "pattern_discovery"


def test_lead_generation_moves_to_follow_up(tool_context):
    after_tool_run(FakeTool("lead_generation_agent"), {}, tool_context, None)
    assert tool_context.state[StateKeys.STAGE] == "follow_up"


def test_user_confirmation_advances_to_lead_generation(tool_context):
    after_tool_run(
        FakeTool("get_user_choice"),
        {"context": "confirm_lead_generation"},
        tool_context,
        "Yes, find leads",
    )
    assert tool_context.state[StateKeys.STAGE] == "lead_generation"


def test_user_declining_resets_the_brief(tool_context):
    tool_context.state[StateKeys.COUNTRY] = "Thailand"
    tool_context.state[StateKeys.INDUSTRY] = "fintech"

    after_tool_run(
        FakeTool("get_user_choice"),
        {"context": "confirm_lead_generation"},
        tool_context,
        "No, refine the brief",
    )

    assert tool_context.state[StateKeys.STAGE] == "clarify"
    assert tool_context.state[StateKeys.COUNTRY] == ""


def test_user_choice_sets_research_breadth(tool_context):
    after_tool_run(
        FakeTool("get_user_choice"),
        {"context": "set_pattern_company_count"},
        tool_context,
        "7",
    )
    assert tool_context.state[StateKeys.PATTERN_COMPANY_COUNT] == 7


def test_unparseable_count_falls_back_to_the_default(tool_context):
    after_tool_run(
        FakeTool("get_user_choice"),
        {"context": "set_lead_candidate_count"},
        tool_context,
        "lots please",
    )
    assert (
        tool_context.state[StateKeys.LEAD_CANDIDATE_COUNT]
        == config.default_lead_candidates
    )


def test_unknown_tool_leaves_state_untouched(tool_context):
    after_tool_run(FakeTool("some_other_tool"), {}, tool_context, "whatever")
    assert tool_context.state == {}
