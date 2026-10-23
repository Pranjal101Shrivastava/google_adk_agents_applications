"""Tests for the cart arithmetic.

Money is the one place in a commerce agent where "nearly right" is a defect,
so these are exhaustive about rounding, float contamination and the threshold
rules.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from ecommerce_agent.config import config
from ecommerce_agent.constants import StateKeys
from ecommerce_agent.tools.cart import (
    calculate_cart_totals,
    compute_cart_totals,
    format_money,
    validate_quantity,
)


# --- Basic arithmetic -------------------------------------------------
def test_empty_cart_totals_zero():
    totals = compute_cart_totals([])
    assert totals["subtotal"] == "0.00"
    assert totals["total"] == "0.00"
    assert totals["item_count"] == 0


def test_single_line_subtotal():
    totals = compute_cart_totals([{"price": "169.99", "quantity": 1}])
    assert totals["subtotal"] == "169.99"


def test_quantity_multiplies_the_line():
    totals = compute_cart_totals([{"price": "12.50", "quantity": 3}])
    assert totals["items"][0]["line_total"] == "37.50"
    assert totals["subtotal"] == "37.50"


def test_multiple_lines_sum():
    totals = compute_cart_totals(
        [{"price": "169.99", "quantity": 1}, {"price": "12.50", "quantity": 3}]
    )
    assert totals["subtotal"] == "207.49"


def test_unit_count_counts_units_not_lines():
    totals = compute_cart_totals(
        [{"price": "10.00", "quantity": 2}, {"price": "5.00", "quantity": 3}]
    )
    assert totals["item_count"] == 2
    assert totals["unit_count"] == 5


# --- Float contamination ---------------------------------------------
def test_classic_float_error_does_not_appear():
    """0.1 + 0.2 must be 0.30, not 0.30000000000000004."""
    totals = compute_cart_totals(
        [{"price": "0.10", "quantity": 1}, {"price": "0.20", "quantity": 1}]
    )
    assert totals["subtotal"] == "0.30"


def test_repeated_cents_do_not_drift():
    totals = compute_cart_totals([{"price": "0.07", "quantity": 100}])
    assert totals["subtotal"] == "7.00"


def test_float_input_is_still_exact():
    """Callers will pass floats from JSON; the result must not inherit the error."""
    totals = compute_cart_totals([{"price": 19.99, "quantity": 3}])
    assert totals["subtotal"] == "59.97"


def test_every_amount_is_a_two_decimal_string():
    """A float total serialised to JSON can render differently in two places."""
    totals = compute_cart_totals([{"price": "10.005", "quantity": 1}])
    for key in ("subtotal", "discount", "shipping", "tax", "total"):
        value = totals[key]
        assert isinstance(value, str)
        assert len(value.split(".")[1]) == 2, f"{key} is {value!r}"


def test_rounding_is_half_up():
    """Retail rounds 0.005 up; banker's rounding would give 10.00 here."""
    totals = compute_cart_totals([{"price": "10.005", "quantity": 1}])
    assert totals["subtotal"] == "10.01"


# --- Free shipping ----------------------------------------------------
def test_shipping_charged_below_the_threshold():
    totals = compute_cart_totals([{"price": "20.00", "quantity": 1}], shipping_cost="4.99")
    assert totals["shipping"] == "4.99"
    assert totals["free_shipping_applied"] is False


def test_shipping_free_above_the_threshold():
    totals = compute_cart_totals([{"price": "200.00", "quantity": 1}], shipping_cost="4.99")
    assert totals["shipping"] == "0.00"
    assert totals["free_shipping_applied"] is True
    assert totals["shipping_was"] == "4.99"


def test_threshold_is_inclusive():
    """Exactly at the threshold must qualify, or customers will be annoyed."""
    totals = compute_cart_totals(
        [{"price": str(config.free_shipping_threshold), "quantity": 1}],
        shipping_cost="4.99",
    )
    assert totals["free_shipping_applied"] is True


def test_shipping_note_states_the_gap():
    """Customers act on "add €12 more"; the figure has to be right."""
    totals = compute_cart_totals([{"price": "60.00", "quantity": 1}], shipping_cost="4.99")
    assert "15.00" in totals["shipping_note"]


def test_discount_dropping_below_the_threshold_reinstates_shipping():
    """Otherwise a discount silently gives away shipping too."""
    totals = compute_cart_totals(
        [{"price": "80.00", "quantity": 1}], shipping_cost="4.99", discount_amount="20.00"
    )
    assert totals["free_shipping_applied"] is False
    assert totals["shipping"] == "4.99"


# --- Discounts --------------------------------------------------------
def test_discount_reduces_the_total():
    plain = compute_cart_totals([{"price": "100.00", "quantity": 1}])
    discounted = compute_cart_totals(
        [{"price": "100.00", "quantity": 1}], discount_amount="10.00"
    )
    assert Decimal(discounted["total"]) < Decimal(plain["total"])
    assert discounted["discount"] == "10.00"


def test_discount_cannot_exceed_the_subtotal():
    """A €500 voucher on a €50 basket must not produce a negative total."""
    totals = compute_cart_totals(
        [{"price": "50.00", "quantity": 1}], discount_amount="500.00"
    )
    assert totals["discount"] == "50.00"
    assert Decimal(totals["total"]) >= Decimal("0")


# --- Tax --------------------------------------------------------------
def test_tax_inclusive_prices_do_not_inflate_the_total(monkeypatch):
    monkeypatch.setattr(config, "prices_include_tax", True)
    totals = compute_cart_totals([{"price": "121.00", "quantity": 1}])
    # The total equals the shelf price; the tax figure is the portion of it.
    assert totals["total"] == "121.00"
    assert Decimal(totals["tax"]) > Decimal("0")


def test_tax_exclusive_prices_add_tax(monkeypatch):
    monkeypatch.setattr(config, "prices_include_tax", False)
    monkeypatch.setattr(config, "tax_rate_percent", Decimal("21.0"))
    totals = compute_cart_totals([{"price": "100.00", "quantity": 1}])
    assert totals["tax"] == "21.00"
    assert totals["total"] == "121.00"


def test_zero_tax_rate_is_handled(monkeypatch):
    monkeypatch.setattr(config, "tax_rate_percent", Decimal("0"))
    totals = compute_cart_totals([{"price": "100.00", "quantity": 1}])
    assert totals["tax"] == "0.00"


def test_tax_treatment_is_reported():
    """A consumer of this output must not have to guess which convention applies."""
    totals = compute_cart_totals([{"price": "100.00", "quantity": 1}])
    assert totals["tax_included_in_prices"] is config.prices_include_tax
    assert totals["tax_rate_percent"]


# --- Rejected input ---------------------------------------------------
@pytest.mark.parametrize(
    "items",
    [
        [{"price": "-5.00", "quantity": 1}],
        [{"price": "10.00", "quantity": -1}],
        [{"price": "not-a-price", "quantity": 1}],
        [{"price": "10.00", "quantity": "three"}],
    ],
)
def test_invalid_lines_are_rejected(items):
    with pytest.raises(ValueError):
        compute_cart_totals(items)


def test_negative_shipping_is_rejected():
    with pytest.raises(ValueError):
        compute_cart_totals([{"price": "10.00", "quantity": 1}], shipping_cost="-1.00")


def test_negative_discount_is_rejected():
    with pytest.raises(ValueError):
        compute_cart_totals([{"price": "10.00", "quantity": 1}], discount_amount="-1.00")


# --- Formatting -------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", "€0.00"), ("1234.5", "€1234.50"), ("1234.567", "€1234.57"), (9.9, "€9.90")],
)
def test_format_money(value, expected):
    assert format_money(value) == expected


# --- The tool wrapper -------------------------------------------------
async def test_tool_stores_totals_in_state(tool_context, basket):
    result = await calculate_cart_totals(basket, tool_context)
    assert result["status"] == "success"
    assert tool_context.state[StateKeys.LAST_CART_TOTALS]["total"] == result["total"]


async def test_tool_rejects_an_empty_cart(tool_context):
    result = await calculate_cart_totals([], tool_context)
    assert result["status"] == "error"
    assert "empty" in result["message"]


async def test_tool_reports_bad_data_without_raising(tool_context):
    result = await calculate_cart_totals([{"price": "oops", "quantity": 1}], tool_context)
    assert result["status"] == "error"
    assert "Re-fetch the cart rather than estimating" in result["message"]


async def test_tool_is_deterministic(tool_context, basket):
    first = await calculate_cart_totals(basket, tool_context)
    second = await calculate_cart_totals(basket, tool_context)
    assert first["total"] == second["total"]


# --- Quantity limits --------------------------------------------------
async def test_quantity_within_the_limit_is_allowed(tool_context):
    result = await validate_quantity(2, tool_context)
    assert result["allowed"] is True


async def test_quantity_above_the_limit_is_refused_with_the_limit(tool_context):
    result = await validate_quantity(config.max_cart_quantity + 1, tool_context)
    assert result["allowed"] is False
    assert str(config.max_cart_quantity) in result["message"]


@pytest.mark.parametrize("quantity", [0, -1])
async def test_non_positive_quantity_is_refused(tool_context, quantity):
    assert (await validate_quantity(quantity, tool_context))["allowed"] is False


async def test_non_numeric_quantity_is_refused(tool_context):
    assert (await validate_quantity("many", tool_context))["allowed"] is False
