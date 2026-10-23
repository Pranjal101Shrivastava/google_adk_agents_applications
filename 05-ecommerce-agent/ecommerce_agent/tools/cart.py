"""Cart arithmetic.

**Language models must not do money arithmetic.** They are good at explaining a
price and bad at computing one, and the failure is silent: a plausible wrong
total looks exactly like a right one. A customer shown a total that does not
match what their card is charged is a support ticket at best.

So every figure a customer sees comes from this module: ``Decimal``
throughout, explicit ``ROUND_HALF_UP`` quantisation at the last step, and the
tax treatment (inclusive or exclusive) taken from configuration rather than
assumed. The model's job is to say *why* shipping is free, not to work out
whether it is.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from google.adk.tools import ToolContext

from ..config import config
from ..constants import StateKeys

logger = logging.getLogger(__name__)

CENTS = Decimal("0.01")


def _money(value: Any) -> Decimal:
    """Coerce a value to Decimal money, rejecting anything unparseable.

    Goes via ``str`` deliberately: ``Decimal(0.1)`` captures the binary float's
    error, while ``Decimal("0.1")`` does not.
    """
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{value!r} is not a valid monetary amount") from exc


def _round(value: Decimal) -> Decimal:
    """Quantise to cents, rounding half away from zero as retail expects."""
    return value.quantize(CENTS, rounding=ROUND_HALF_UP)


def format_money(amount: Any) -> str:
    """Format an amount for display, e.g. ``"€75.00"``.

    Args:
        amount: A number, string or Decimal.

    Returns:
        The amount with the configured currency symbol and exactly two
        decimal places.
    """
    return f"{config.currency_symbol}{_round(_money(amount)):.2f}"


def compute_cart_totals(
    items: list[dict[str, Any]],
    shipping_cost: Any = 0,
    discount_amount: Any = 0,
) -> dict[str, Any]:
    """Compute exact cart totals.

    Pure function - no I/O, no state - so the arithmetic is directly testable.

    Args:
        items: Line items, each with ``price`` and ``quantity``. Extra keys
            are carried through to the returned lines.
        shipping_cost: Shipping before the free-shipping threshold is applied.
        discount_amount: A fixed discount off the subtotal.

    Returns:
        The line items with computed line totals, plus ``subtotal``,
        ``discount``, ``shipping``, ``tax`` and ``total`` as strings with two
        decimal places, and the threshold explanation the agent should relay.

    Raises:
        ValueError: If a price or quantity is unparseable or negative.
    """
    lines: list[dict[str, Any]] = []
    subtotal = Decimal("0")

    for index, item in enumerate(items or []):
        price = _money(item.get("price", 0))
        if price < 0:
            raise ValueError(f"Line {index + 1} has a negative price.")

        try:
            quantity = int(item.get("quantity", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Line {index + 1} has a non-integer quantity "
                f"{item.get('quantity')!r}."
            ) from exc
        if quantity < 0:
            raise ValueError(f"Line {index + 1} has a negative quantity.")

        line_total = _round(price * quantity)
        subtotal += line_total
        lines.append(
            {
                **item,
                "unit_price": f"{_round(price):.2f}",
                "quantity": quantity,
                "line_total": f"{line_total:.2f}",
            }
        )

    subtotal = _round(subtotal)

    discount = _round(_money(discount_amount))
    if discount < 0:
        raise ValueError("A discount cannot be negative.")
    # A discount never makes the subtotal negative, however large it is.
    discount = min(discount, subtotal)
    discounted = _round(subtotal - discount)

    requested_shipping = _round(_money(shipping_cost))
    if requested_shipping < 0:
        raise ValueError("Shipping cannot be negative.")

    # The threshold is tested against the post-discount subtotal: a discount
    # that drops the order under the threshold does reinstate shipping, which
    # is both the common retail rule and the one that avoids giving away
    # shipping on a discounted order.
    threshold = _round(_money(config.free_shipping_threshold))
    free_shipping = discounted >= threshold and threshold > 0
    shipping = Decimal("0.00") if free_shipping else requested_shipping

    rate = _money(config.tax_rate_percent) / Decimal("100")
    taxable = _round(discounted + shipping)

    if config.prices_include_tax:
        # Tax is already inside the prices; report the portion, do not add it.
        tax = _round(taxable - (taxable / (Decimal("1") + rate))) if rate else Decimal("0.00")
        total = taxable
    else:
        tax = _round(taxable * rate)
        total = _round(taxable + tax)

    if free_shipping:
        shipping_note = (
            f"Shipping is free: the order is at or above the "
            f"{format_money(threshold)} threshold."
        )
    elif threshold > 0:
        remaining = _round(threshold - discounted)
        shipping_note = (
            f"Add {format_money(remaining)} more to qualify for free shipping "
            f"(threshold {format_money(threshold)})."
        )
    else:
        shipping_note = "No free-shipping threshold is configured."

    return {
        "currency": config.currency,
        "currency_symbol": config.currency_symbol,
        "items": lines,
        "item_count": len(lines),
        "unit_count": sum(line["quantity"] for line in lines),
        "subtotal": f"{subtotal:.2f}",
        "discount": f"{discount:.2f}",
        "shipping": f"{shipping:.2f}",
        "shipping_was": f"{requested_shipping:.2f}",
        "free_shipping_applied": free_shipping,
        "free_shipping_threshold": f"{threshold:.2f}",
        "tax": f"{tax:.2f}",
        "tax_rate_percent": f"{_money(config.tax_rate_percent):.1f}",
        "tax_included_in_prices": config.prices_include_tax,
        "total": f"{total:.2f}",
        "display_total": format_money(total),
        "shipping_note": shipping_note,
    }


async def calculate_cart_totals(
    items: list[dict[str, Any]],
    tool_context: ToolContext,
    shipping_cost: float = 0.0,
    discount_amount: float = 0.0,
) -> dict[str, Any]:
    """Compute the exact subtotal, discount, shipping, tax and total for a cart.

    **Always call this before quoting any total.** Never add prices up
    yourself: a total you computed and a total the checkout computes will
    eventually disagree, and the customer will believe yours.

    Args:
        items: The cart lines. Each needs ``price`` and ``quantity``; include
            ``product_name``, ``size`` and ``color`` so the breakdown is
            readable.
        tool_context: ADK tool context (supplied by the runtime).
        shipping_cost: The chosen delivery method's cost, before the
            free-shipping threshold is applied.
        discount_amount: Any fixed discount to apply to the subtotal.

    Returns:
        Every figure as a two-decimal string, plus ``shipping_note``
        explaining the free-shipping outcome. Quote these verbatim.
    """
    if not items:
        return {
            "status": "error",
            "message": (
                "The cart is empty, so there is nothing to total. Fetch the "
                "shopping list first."
            ),
        }

    try:
        totals = compute_cart_totals(items, shipping_cost, discount_amount)
    except ValueError as exc:
        logger.warning("Cart totals rejected: %s", exc)
        return {
            "status": "error",
            "message": (
                f"Could not compute the total: {exc}. Re-fetch the cart rather "
                "than estimating the figures."
            ),
        }

    tool_context.state[StateKeys.LAST_CART_TOTALS] = totals
    logger.info(
        "Cart: %d line(s), subtotal %s, total %s",
        totals["item_count"],
        totals["subtotal"],
        totals["total"],
    )
    return {"status": "success", **totals}


async def validate_quantity(
    quantity: int,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Check a requested quantity against the per-line limit.

    Call this before adding to the cart, so an over-limit request is refused
    with a clear reason rather than failing in the database.

    Args:
        quantity: The quantity the customer asked for.
        tool_context: ADK tool context (supplied by the runtime).

    Returns:
        Whether the quantity is ``allowed``, the ``limit``, and a message to
        relay when it is not.
    """
    limit = config.max_cart_quantity

    try:
        requested = int(quantity)
    except (TypeError, ValueError):
        return {
            "status": "error",
            "allowed": False,
            "message": f"{quantity!r} is not a whole number of items.",
        }

    if requested < 1:
        return {
            "status": "error",
            "allowed": False,
            "limit": limit,
            "message": "The quantity must be at least 1.",
        }
    if requested > limit:
        return {
            "status": "error",
            "allowed": False,
            "limit": limit,
            "requested": requested,
            "message": (
                f"The maximum is {limit} per line. Offer {limit}, or suggest "
                "contacting sales for a bulk order."
            ),
        }

    return {"status": "success", "allowed": True, "quantity": requested, "limit": limit}
