"""Tool assembly for the e-commerce agent.

Same pattern as the bug assistant: the Toolbox connection is optional and its
absence is *reported* rather than silently producing an agent that invents
product names and order numbers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..config import config
from .cart import calculate_cart_totals, validate_quantity
from .catalog import normalize_size, remember_product, size_conversion_table

logger = logging.getLogger(__name__)


@dataclass
class ToolGroup:
    """One logical group of tools and whether it is available."""

    name: str
    kind: str
    enabled: bool
    reason: str
    tools: list[Any] = field(default_factory=list)

    def describe(self) -> str:
        mark = "available" if self.enabled else "NOT available"
        return f"- **{self.name}** ({self.kind}): {mark} - {self.reason}"


def _build_local_tools() -> ToolGroup:
    """Deterministic local tools. Always present."""
    return ToolGroup(
        name="Pricing and sizing",
        kind="function tools",
        enabled=True,
        reason=(
            "calculate_cart_totals (exact money arithmetic - never do this "
            "yourself), validate_quantity, normalize_size, remember_product, "
            "size_conversion_table"
        ),
        tools=[
            calculate_cart_totals,
            validate_quantity,
            normalize_size,
            remember_product,
            size_conversion_table,
        ],
    )


def _build_storefront(auth_token_getter: Any = None) -> ToolGroup:
    """The storefront database, via MCP Toolbox.

    Args:
        auth_token_getter: Optional callable returning the caller's Google ID
            token. Toolbox uses it to resolve the ``google_signin`` auth
            service and inject the verified ``email`` claim into the SQL that
            needs it. This is the mechanism that keeps the customer's identity
            out of the model's hands entirely.
    """
    if not config.enable_toolbox:
        return ToolGroup(
            name="Storefront database",
            kind="MCP Toolbox for Databases",
            enabled=False,
            reason=(
                "disabled by ENABLE_TOOLBOX=false - you cannot look up products, "
                "carts, orders or stores, and must say so rather than inventing "
                "any of them"
            ),
        )

    try:
        from google.adk.tools.toolbox_toolset import ToolboxToolset

        kwargs: dict[str, Any] = {
            "server_url": config.toolbox_url,
            "toolset_name": config.toolbox_toolset,
        }
        if auth_token_getter is not None:
            kwargs["auth_token_getters"] = {"google_signin": auth_token_getter}

        toolset = ToolboxToolset(**kwargs)
        return ToolGroup(
            name="Storefront database",
            kind="MCP Toolbox for Databases",
            enabled=True,
            reason=(
                f"toolset {config.toolbox_toolset!r} at {config.toolbox_url} - "
                "product search, details, stock, cart, orders, stores and "
                "delivery methods"
            ),
            tools=[toolset],
        )
    except Exception as exc:  # noqa: BLE001 - never break startup over a backend
        logger.warning("Could not build the storefront toolset: %s", exc)
        return ToolGroup(
            name="Storefront database",
            kind="MCP Toolbox for Databases",
            enabled=False,
            reason=(
                f"could not be initialised: {exc}. Install the extra with "
                "`pip install 'google-adk[toolbox]'` and check TOOLBOX_URL"
            ),
        )


def build_tool_groups(auth_token_getter: Any = None) -> list[ToolGroup]:
    """Assemble every tool group independently of each other's success."""
    builders = (
        _build_local_tools,
        lambda: _build_storefront(auth_token_getter),
    )
    groups: list[ToolGroup] = []
    for builder in builders:
        try:
            groups.append(builder())
        except Exception as exc:  # noqa: BLE001
            logger.exception("A tool group builder failed")
            groups.append(
                ToolGroup(
                    name="unknown",
                    kind="unknown",
                    enabled=False,
                    reason=f"builder raised: {exc}",
                )
            )
    return groups


def collect_tools(groups: list[ToolGroup]) -> list[Any]:
    """Flatten the enabled groups into the agent's tool list."""
    tools: list[Any] = []
    for group in groups:
        if group.enabled:
            tools.extend(group.tools)
    return tools


def describe_inventory(groups: list[ToolGroup]) -> str:
    """Render the inventory for the agent's instruction."""
    lines = [group.describe() for group in groups]
    missing = [g.name for g in groups if not g.enabled]
    if missing:
        lines.append(
            "\n**You do not have: "
            + ", ".join(missing)
            + ".** Tell the customer the storefront is unavailable right now. "
            "Never invent a product, a price, a stock level or an order number."
        )
    return "\n".join(lines)
