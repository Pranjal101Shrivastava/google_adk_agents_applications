"""Typed contracts for the e-commerce agent."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CartLine(BaseModel):
    """One line in a basket."""

    product_name: str
    size: str | None = None
    color: str | None = None
    price: Decimal = Field(ge=Decimal("0"), description="Unit price.")
    quantity: int = Field(ge=0, description="Units of this variant.")

    @field_validator("price", mode="before")
    @classmethod
    def _coerce_price(cls, value: object) -> object:
        """Route prices through str, so float error never enters the Decimal."""
        if isinstance(value, float):
            return str(value)
        return value


class CartTotals(BaseModel):
    """The computed totals. Every amount is a two-decimal string, not a float.

    Strings rather than floats deliberately: a float total serialised to JSON
    can come back as ``207.48999999999998``, and money that renders differently
    in two places is money a customer will query.
    """

    currency: str = Field(min_length=3, max_length=3)
    currency_symbol: str
    item_count: int = Field(ge=0)
    unit_count: int = Field(ge=0)
    subtotal: str
    discount: str
    shipping: str
    tax: str
    total: str
    free_shipping_applied: bool
    shipping_note: str


class ProductSummary(BaseModel):
    """A product as shown in a search result."""

    name: str
    brand: str | None = None
    category: str | None = None
    description: str | None = None
    price: str | None = Field(default=None, description="Formatted display price.")


class OrderSummary(BaseModel):
    """An order as shown to the customer."""

    order_id: int
    status: Literal[
        "Pending", "Confirmed", "Packed", "Shipped", "Delivered", "Cancelled", "Returned"
    ]
    total_amount: str
    store_name: str | None = None
    shipping_address: str | None = None
    delivery_method: str | None = None
