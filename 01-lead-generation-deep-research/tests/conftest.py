"""Shared offline test fixtures.

The agent graph is exercised without a model, a network or GCP credentials.
``FakeToolContext`` and ``FakeCallbackContext`` stand in for the ADK contexts,
which only need to expose a mutable ``state`` mapping for the code under test.
"""

from __future__ import annotations

from typing import Any

import pytest


class FakeToolContext:
    """Minimal stand-in for ``google.adk.tools.ToolContext``."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(state or {})


class FakeCallbackContext:
    """Minimal stand-in for ``google.adk.agents.CallbackContext``."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(state or {})


class FakeTool:
    """Stand-in for an ADK tool, which callbacks identify by ``name``."""

    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture
def tool_context() -> FakeToolContext:
    return FakeToolContext()


@pytest.fixture
def callback_context() -> FakeCallbackContext:
    return FakeCallbackContext()


@pytest.fixture
def patterns() -> list[dict[str, Any]]:
    """A realistic four-signal pattern set whose weights sum to 100."""
    return [
        {
            "name": "Local leadership hire",
            "description": "A country manager or regional GM is hired.",
            "evidence_count": 4,
            "lead_time": "4-8 months",
            "how_to_detect": "LinkedIn job postings for country-scoped roles.",
            "weight": 40,
        },
        {
            "name": "Regional job postings",
            "description": "Multiple local-market roles open at once.",
            "evidence_count": 3,
            "lead_time": "3-6 months",
            "how_to_detect": "Company careers page filtered by location.",
            "weight": 30,
        },
        {
            "name": "Trademark filing",
            "description": "A local trademark or entity registration appears.",
            "evidence_count": 2,
            "lead_time": "6-12 months",
            "how_to_detect": "National trademark registry search.",
            "weight": 20,
        },
        {
            "name": "Conference appearance",
            "description": "The company exhibits at a local industry event.",
            "evidence_count": 2,
            "lead_time": "2-5 months",
            "how_to_detect": "Event exhibitor lists.",
            "weight": 10,
        },
    ]


def tool_names(agent) -> list[str]:
    """Return the tool names declared on *agent*.

    ADK accepts plain functions in ``tools=[...]`` and only wraps them in a
    ``FunctionTool`` when the agent runs, so the attribute holding the name
    differs by entry: ``name`` for tool objects, ``__name__`` for functions.
    """
    names = []
    for tool in agent.tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        names.append(name or type(tool).__name__)
    return names
