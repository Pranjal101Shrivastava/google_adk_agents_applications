"""Lifecycle callbacks that keep session state coherent.

ADK gives each agent a shared ``state`` dict. Without discipline that dict
turns into a junk drawer, so all cross-phase state transitions happen here, in
one reviewable place, rather than being scattered through prompts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import ToolContext

from .config import config
from .constants import StateKeys

logger = logging.getLogger(__name__)

_REQUIRED_BRIEF_FIELDS = (StateKeys.COUNTRY, StateKeys.INDUSTRY)


def before_agent_run(callback_context: CallbackContext) -> None:
    """Seed the session with defaults and a fresh timestamp.

    Runs before every turn of the root agent, so prompts can always rely on
    ``{current_time}`` and the brief keys existing.
    """
    state = callback_context.state
    now = datetime.now(timezone.utc)
    state[StateKeys.CURRENT_TIME] = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    state[StateKeys.CURRENT_YEAR] = now.year

    defaults: dict[str, Any] = {
        StateKeys.COUNTRY: "",
        StateKeys.INDUSTRY: "",
        StateKeys.BUYER_PERSONA: "",
        StateKeys.VALUE_PROPOSITION: "",
        StateKeys.STAGE: "clarify",
        StateKeys.PATTERN_COMPANY_COUNT: config.default_pattern_companies,
        StateKeys.LEAD_CANDIDATE_COUNT: config.default_lead_candidates,
        StateKeys.PATTERNS_AVAILABLE: False,
    }
    for key, value in defaults.items():
        state.setdefault(key, value)

    return None


def after_tool_run(
    tool: Any,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: Any,
) -> None:
    """Promote sub-agent results into the top-level brief.

    Each phase writes its own namespaced output key; this callback lifts the
    few values the *next* phase needs into stable, flat state keys.
    """
    state = tool_context.state
    tool_name = getattr(tool, "name", "") or ""

    if tool_name == "intent_extractor_agent":
        _apply_intent(state)
    elif tool_name == "pattern_discovery_agent":
        _apply_patterns(state)
    elif tool_name == "lead_generation_agent":
        state[StateKeys.STAGE] = "follow_up"
    elif tool_name == "get_user_choice":
        _apply_user_choice(state, args, tool_response)

    return None


def _apply_intent(state: Any) -> None:
    """Copy the extracted brief into flat state keys, keeping known values."""
    extraction = state.get(StateKeys.INTENT_EXTRACTION)
    if not isinstance(extraction, dict):
        # ``output_schema`` normally lands a dict here; a string means the model
        # drifted off-schema and there is nothing safe to promote.
        if extraction is not None:
            logger.warning(
                "intent_extraction_result was %s, not a dict; skipping promotion.",
                type(extraction).__name__,
            )
        return

    for key in (
        StateKeys.COUNTRY,
        StateKeys.INDUSTRY,
        StateKeys.BUYER_PERSONA,
        StateKeys.VALUE_PROPOSITION,
    ):
        value = extraction.get(key)
        if value:
            state[key] = value

    stage = extraction.get(StateKeys.STAGE)
    if stage:
        state[StateKeys.STAGE] = stage

    # Never advance past clarification while a required field is blank, no
    # matter what the extractor claimed.
    if any(not state.get(field) for field in _REQUIRED_BRIEF_FIELDS):
        state[StateKeys.STAGE] = "clarify"


def _apply_patterns(state: Any) -> None:
    """Record that patterns exist so later turns can skip rediscovery."""
    report = state.get(StateKeys.PATTERN_REPORT)
    patterns = report.get("patterns") if isinstance(report, dict) else None

    if patterns:
        state[StateKeys.PATTERNS_AVAILABLE] = True
        state[StateKeys.STAGE] = "lead_generation"
        if isinstance(report, dict):
            state[StateKeys.COMPANIES_STUDIED] = report.get("companies_studied", 0)
        logger.info("Pattern discovery produced %d pattern(s).", len(patterns))
    else:
        state[StateKeys.PATTERNS_AVAILABLE] = False
        state[StateKeys.STAGE] = "pattern_discovery"
        logger.warning("Pattern discovery returned no patterns; staying in that stage.")


def _apply_user_choice(state: Any, args: dict[str, Any], response: Any) -> None:
    """Interpret a ``get_user_choice`` answer in light of why we asked."""
    if not response:
        return

    reason = (args or {}).get("context") or ""
    answer = str(response)

    if reason == "confirm_lead_generation":
        if answer.lower().startswith("yes"):
            state[StateKeys.STAGE] = "lead_generation"
        else:
            # The user wants to change the brief; drop the derived brief but keep
            # the patterns, which are market-level and still valid.
            state[StateKeys.STAGE] = "clarify"
            state[StateKeys.COUNTRY] = ""
            state[StateKeys.INDUSTRY] = ""
    elif reason == "set_pattern_company_count":
        state[StateKeys.PATTERN_COMPANY_COUNT] = _as_int(
            answer, config.default_pattern_companies
        )
    elif reason == "set_lead_candidate_count":
        state[StateKeys.LEAD_CANDIDATE_COUNT] = _as_int(
            answer, config.default_lead_candidates
        )


def _as_int(value: str, fallback: int) -> int:
    """Parse a user-supplied count, falling back on anything unparseable."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        logger.warning("Could not parse %r as a count; using %d.", value, fallback)
        return fallback
