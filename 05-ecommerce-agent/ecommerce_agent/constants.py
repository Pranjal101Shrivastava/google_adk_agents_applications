"""Shared constants for the e-commerce agent."""

from __future__ import annotations

# Order lifecycle. These match the `status` column's CHECK constraint.
ORDER_STATUSES = (
    "Pending",
    "Confirmed",
    "Packed",
    "Shipped",
    "Delivered",
    "Cancelled",
    "Returned",
)

# Statuses from which a customer may still cancel without support.
CANCELLABLE_STATUSES = ("Pending", "Confirmed", "Packed")


class StateKeys:
    """Session-state keys.

    ``VERIFIED_EMAIL`` is the important one: it is written by the backend from
    a cryptographically verified ID token, never by the model, and it is what
    every authenticated tool is bound to.
    """

    # Identity - set by the backend, read-only to the agent.
    VERIFIED_EMAIL = "verified_email"
    VERIFIED_NAME = "verified_name"
    IS_AUTHENTICATED = "is_authenticated"

    # Conversation
    LAST_CART_TOTALS = "last_cart_totals"
    PENDING_CONFIRMATION = "pending_confirmation"
    LAST_VIEWED_PRODUCT = "last_viewed_product"
