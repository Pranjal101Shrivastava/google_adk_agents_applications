"""Root agent for the E-Commerce Agent.

Run locally with::

    adk web .                  # from 05-ecommerce-agent/
    adk run ecommerce_agent

For the authenticated flows, run the FastAPI backend instead
(``backend/app.py``): it verifies the Google ID token and writes the verified
identity into session state, which is what the authenticated tools bind to.
"""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from google.adk.agents import LlmAgent

from .config import config
from .prompts import AGENT_INSTRUCTION_TEMPLATE
from .tools.cart import format_money
from .tools.registry import build_tool_groups, collect_tools, describe_inventory

load_dotenv()

logger = logging.getLogger(__name__)


def _substitute(template: str, **values: object) -> str:
    """Fill the build-time placeholders, leaving ADK's state ones alone.

    The instruction contains two kinds of placeholder: ``{currency}``, which is
    a deployment fact we substitute here, and ``{is_authenticated?}``, which
    ADK resolves from session state at invocation time. ``str.format`` would
    raise ``KeyError`` on the second kind, so we replace only our own keys.
    """
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result


def build_agent(auth_token_getter=None) -> LlmAgent:
    """Build the root agent.

    Args:
        auth_token_getter: Optional callable returning the caller's Google ID
            token. Passed through to the Toolbox toolset, which uses it to
            inject the verified ``email`` claim into the SQL that needs it.
            The backend supplies this; ``adk web`` does not, so the dev UI runs
            in guest mode and the authenticated tools correctly refuse.

    Returns:
        The configured ``LlmAgent``.
    """
    groups = build_tool_groups(auth_token_getter)
    tools = collect_tools(groups)
    inventory = describe_inventory(groups)

    disabled = [g.name for g in groups if not g.enabled]
    if disabled:
        logger.warning("Storefront tool groups unavailable: %s", ", ".join(disabled))

    instruction = _substitute(
        AGENT_INSTRUCTION_TEMPLATE,
        tool_inventory=inventory,
        currency=config.currency,
        free_shipping_threshold=format_money(config.free_shipping_threshold),
    )

    return LlmAgent(
        name="ecommerce_agent",
        model=config.agent_model,
        description=(
            "Sports retail shopping assistant. Finds products, checks stock, "
            "manages the basket and tracks orders, with exact pricing and "
            "identity bound to a verified sign-in."
        ),
        instruction=instruction,
        tools=tools,
    )


# Module-level agent for `adk web` / `adk run`. Guest mode: no token getter, so
# the authenticated tools refuse rather than silently acting as nobody.
root_agent = build_agent()

__all__ = ["root_agent", "build_agent"]
