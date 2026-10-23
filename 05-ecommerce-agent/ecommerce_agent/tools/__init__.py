"""Tools for the e-commerce agent."""

from .cart import (
    calculate_cart_totals,
    compute_cart_totals,
    format_money,
    validate_quantity,
)
from .catalog import normalize_size, remember_product, size_conversion_table
from .registry import (
    ToolGroup,
    build_tool_groups,
    collect_tools,
    describe_inventory,
)

__all__ = [
    "ToolGroup",
    "build_tool_groups",
    "calculate_cart_totals",
    "collect_tools",
    "compute_cart_totals",
    "describe_inventory",
    "format_money",
    "normalize_size",
    "remember_product",
    "size_conversion_table",
    "validate_quantity",
]
