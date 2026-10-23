"""Shared constants for the bug assistant."""

from __future__ import annotations

# Ticket priorities, highest first. These strings match the `priority` column
# in the tickets table and the values the Toolbox SQL tools expect.
PRIORITIES = (
    "P0 - Critical",
    "P1 - High",
    "P2 - Medium",
    "P3 - Low",
)

# Ticket statuses, matching the `status` column.
STATUSES = ("Open", "In Progress", "Blocked", "Resolved", "Closed")


class StateKeys:
    """Session-state keys."""

    LAST_STACK_TRACE = "last_stack_trace"
    LAST_TRIAGE = "last_triage"
    TOOL_INVENTORY = "tool_inventory"
