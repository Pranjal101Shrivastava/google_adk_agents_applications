"""Offline fixtures for the e-commerce agent tests."""

from __future__ import annotations

from typing import Any

import pytest


class FakeToolContext:
    """Minimal stand-in for ``google.adk.tools.ToolContext``."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(state or {})


@pytest.fixture
def tool_context() -> FakeToolContext:
    return FakeToolContext()


@pytest.fixture
def signed_in_state() -> dict[str, Any]:
    """Session state as the backend writes it after verifying a token."""
    return {
        "is_authenticated": True,
        "verified_email": "alex.morgan@example.com",
        "verified_name": "Alex Morgan",
    }


@pytest.fixture
def guest_state() -> dict[str, Any]:
    return {"is_authenticated": False, "verified_email": None, "verified_name": None}


@pytest.fixture
def basket() -> list[dict[str, Any]]:
    """A two-line basket, under the free-shipping threshold."""
    return [
        {"product_name": "Peregrine 13", "size": "43", "color": "Black/Citron",
         "price": "129.00", "quantity": 1},
        {"product_name": "Thermal Base Layer", "size": "M", "color": "Charcoal",
         "price": "89.99", "quantity": 1},
    ]
