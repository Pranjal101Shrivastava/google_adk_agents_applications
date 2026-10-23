"""Structural tests over the bug assistant."""

from __future__ import annotations

from software_bug_assistant import agent as agent_module
from software_bug_assistant import root_agent
from software_bug_assistant.constants import PRIORITIES, STATUSES
from software_bug_assistant.tools.diagnostics import TRIAGE_SIGNALS


def squashed(text: str) -> str:
    return " ".join(text.lower().split())


def test_root_agent_has_the_local_diagnostic_tools():
    names = {
        getattr(t, "name", None) or getattr(t, "__name__", None)
        for t in root_agent.tools
    }
    assert {"parse_stack_trace", "triage_priority", "get_current_date"} <= names


def test_instruction_carries_the_tool_inventory():
    """Substituted at build time: the inventory is a deployment property."""
    assert "{tool_inventory}" not in root_agent.instruction
    assert "Your tools, right now" in root_agent.instruction
    assert "**Diagnostics**" in root_agent.instruction


def test_instruction_forbids_fabricating_ticket_data():
    instruction = squashed(root_agent.instruction)
    assert "never invent a ticket id" in instruction
    assert "never claim a ticket was created or updated unless the tool returned success" in instruction


def test_instruction_requires_confirmation_before_writes():
    instruction = squashed(root_agent.instruction)
    assert "ask before you write" in instruction


def test_instruction_separates_no_results_from_search_failure():
    """These lead to opposite advice, so the distinction must be explicit."""
    instruction = squashed(root_agent.instruction)
    assert 'distinguish "no results" from "could not search"' in instruction


def test_instruction_orders_parsing_before_searching():
    instruction = squashed(root_agent.instruction)
    assert "parse first, search second" in instruction


def test_instruction_lists_the_exact_enum_values():
    """Prompt and database CHECK constraints must agree or writes fail."""
    for priority in PRIORITIES:
        assert priority in root_agent.instruction
    for status in STATUSES:
        assert status in root_agent.instruction


def test_tool_groups_are_exported_for_diagnostics():
    assert agent_module.tool_groups
    assert isinstance(agent_module.tool_inventory, str)


def test_every_tool_group_explains_itself():
    """A group with no reason cannot tell the user why it is missing."""
    for group in agent_module.tool_groups:
        assert group.reason, f"{group.name} has no reason string"
        assert group.kind


def test_triage_rubric_weights_are_sane():
    """Every signal must move the score, and none may single-handedly decide it."""
    for name, (weight, description) in TRIAGE_SIGNALS.items():
        assert 0 < weight <= 50, f"{name} has an out-of-range weight"
        assert description, f"{name} has no description"


def test_agent_model_is_configured():
    from software_bug_assistant.config import config

    assert root_agent.model == config.agent_model
