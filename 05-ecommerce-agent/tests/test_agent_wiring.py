"""Structural tests over the e-commerce agent and its tool surface."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ecommerce_agent import root_agent
from ecommerce_agent.config import config
from ecommerce_agent.constants import CANCELLABLE_STATUSES, ORDER_STATUSES
from ecommerce_agent.tools import registry

TOOLS_YAML = Path(__file__).resolve().parent.parent / "deployment/toolbox/tools.yaml"


def squashed(text: str) -> str:
    return " ".join(text.lower().split())


def tool_names(agent) -> list[str]:
    return [
        getattr(t, "name", None) or getattr(t, "__name__", None) or type(t).__name__
        for t in agent.tools
    ]


@pytest.fixture(scope="module")
def tools_config() -> dict:
    return yaml.safe_load(TOOLS_YAML.read_text())


# --- Agent ------------------------------------------------------------
def test_local_deterministic_tools_are_present():
    names = set(tool_names(root_agent))
    assert {
        "calculate_cart_totals",
        "validate_quantity",
        "normalize_size",
        "remember_product",
        "size_conversion_table",
    } <= names


def test_instruction_carries_the_substituted_inventory():
    assert "{tool_inventory}" not in root_agent.instruction
    assert "Pricing and sizing" in root_agent.instruction


def test_adk_state_placeholders_survive_substitution():
    """These are resolved by ADK at invocation time, not at build time."""
    assert "{is_authenticated?}" in root_agent.instruction
    assert "{verified_name?}" in root_agent.instruction


def test_instruction_forbids_accepting_a_typed_email():
    instruction = squashed(root_agent.instruction)
    assert "never ask a customer to type their email address, and never accept one" in instruction
    assert "a typed address is not an identity" in instruction


def test_instruction_forbids_identity_as_a_tool_argument():
    assert "never pass an email, user id or account identifier as a tool argument" in squashed(
        root_agent.instruction
    )


def test_instruction_forbids_doing_money_arithmetic():
    instruction = squashed(root_agent.instruction)
    assert "you do not do arithmetic" in instruction
    assert "always call `calculate_cart_totals` before quoting any total" in instruction


def test_instruction_requires_confirmation_before_writes():
    assert "confirm before you change anything" in squashed(root_agent.instruction)


def test_instruction_forbids_inventing_catalogue_data():
    assert (
        "never invent a product, price, stock level, store, order number or delivery date"
        in squashed(root_agent.instruction)
    )


def test_instruction_lists_what_a_guest_can_and_cannot_do():
    instruction = squashed(root_agent.instruction)
    assert "if the customer is **not** signed in" in instruction


def test_instruction_states_the_currency_and_threshold():
    assert config.currency in root_agent.instruction
    assert config.currency_symbol in root_agent.instruction


# --- Registry degradation ---------------------------------------------
def test_local_tools_are_always_available():
    group = registry._build_local_tools()
    assert group.enabled is True
    assert len(group.tools) == 5


def test_storefront_can_be_disabled(monkeypatch):
    monkeypatch.setattr(config, "enable_toolbox", False)
    group = registry._build_storefront()
    assert group.enabled is False
    assert "must say so rather than inventing" in group.reason


def test_inventory_warns_when_the_storefront_is_missing():
    groups = [
        registry.ToolGroup("Pricing and sizing", "function tools", True, "local"),
        registry.ToolGroup("Storefront database", "Toolbox", False, "server down"),
    ]
    inventory = registry.describe_inventory(groups)
    assert "You do not have: Storefront database" in inventory
    assert "Never invent a product" in inventory


def test_collect_tools_skips_disabled_groups():
    groups = [
        registry.ToolGroup("A", "k", True, "r", tools=["t1"]),
        registry.ToolGroup("B", "k", False, "r", tools=["t2"]),
    ]
    assert registry.collect_tools(groups) == ["t1"]


# --- tools.yaml: the authorization boundary ---------------------------
PERSONAL_TOOLS = {
    "show-shopping-list",
    "add-to-shopping-list",
    "remove-from-shopping-list",
    "update-shopping-list-quantity",
    "clear-shopping-list",
    "show-orders",
    "get-order-status",
    "place-order",
    "cancel-order",
}


def test_every_personal_tool_binds_identity_to_a_verified_claim(tools_config):
    """This is the control that stops cross-account access."""
    for name in PERSONAL_TOOLS:
        tool = tools_config["tools"][name]
        bound = [
            p
            for p in tool["parameters"]
            if any(a["name"] == "google_signin" and a["field"] == "email"
                   for a in (p.get("authServices") or []))
        ]
        assert bound, f"{name} does not bind identity to the verified email claim"


def test_personal_tool_sql_filters_on_the_bound_email(tools_config):
    """A bound parameter that the SQL ignores is decoration, not a control."""
    for name in PERSONAL_TOOLS:
        tool = tools_config["tools"][name]
        index = next(
            i for i, p in enumerate(tool["parameters"], start=1)
            if p.get("authServices")
        )
        assert f"${index}" in tool["statement"], (
            f"{name} binds the email to ${index} but never uses it in the SQL"
        )


def test_no_hardcoded_credentials_or_client_ids():
    """The reference commits a database password and a real OAuth client ID."""
    for line in TOOLS_YAML.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith(("password:", "clientId:", "user:")):
            assert "${" in stripped, f"hardcoded value: {stripped}"


def test_browse_only_toolset_serves_no_personal_tools(tools_config):
    """The restriction is enforced by the server, not by the prompt."""
    browse = set(tools_config["toolsets"]["storefront_browse_only"])
    assert not browse & PERSONAL_TOOLS


def test_every_toolset_reference_resolves(tools_config):
    declared = set(tools_config["tools"])
    for name, entries in tools_config["toolsets"].items():
        missing = set(entries) - declared
        assert not missing, f"toolset {name} references undeclared tools: {missing}"


def test_the_configured_toolset_exists(tools_config):
    assert config.toolbox_toolset in tools_config["toolsets"]


def test_every_tool_and_parameter_is_documented(tools_config):
    """An undocumented parameter is one the model will fill in wrongly."""
    for name, tool in tools_config["tools"].items():
        assert tool.get("description"), f"{name} has no description"
        for parameter in tool.get("parameters") or []:
            assert parameter.get("description"), (
                f"{name}.{parameter['name']} has no description"
            )


def test_identity_parameters_tell_the_model_not_to_set_them(tools_config):
    for name in PERSONAL_TOOLS:
        for parameter in tools_config["tools"][name]["parameters"]:
            if parameter.get("authServices"):
                assert "Never set this yourself" in parameter["description"], name


def test_cancel_order_sql_restricts_to_cancellable_statuses(tools_config):
    """A shipped order must not be cancellable from the chat window."""
    statement = tools_config["tools"]["cancel-order"]["statement"]
    for status in CANCELLABLE_STATUSES:
        assert f"'{status}'" in statement
    assert "'Shipped'" not in statement
    assert "'Delivered'" not in statement


def test_place_order_refuses_an_empty_basket(tools_config):
    """Otherwise the customer gets a zero-line order they have to phone about."""
    statement = tools_config["tools"]["place-order"]["statement"]
    assert "WHERE EXISTS (SELECT 1 FROM basket)" in statement


def test_place_order_snapshots_the_price(tools_config):
    """Joining for the price later would let a sale rewrite order history."""
    statement = tools_config["tools"]["place-order"]["statement"]
    assert "unit_price" in statement
    assert "b.price" in statement


def test_order_statuses_match_the_schema_constraint():
    schema = (Path(__file__).resolve().parent.parent / "data/schema.sql").read_text()
    for status in ORDER_STATUSES:
        assert f"'{status}'" in schema, f"{status} is not in the CHECK constraint"
