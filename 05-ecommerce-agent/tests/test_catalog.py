"""Tests for the catalogue helpers."""

from __future__ import annotations

import pytest

from ecommerce_agent.constants import StateKeys
from ecommerce_agent.tools.catalog import (
    normalize_size,
    remember_product,
    size_conversion_table,
)


# --- Footwear sizing --------------------------------------------------
@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("US 10", "44"),
        ("us 10", "44"),
        ("9.5 US", "43"),
        ("UK 8", "41"),
        ("43", "43"),
        ("EU 42", "42"),
        ("  44  ", "44"),
    ],
)
async def test_footwear_sizes_resolve_to_eu(tool_context, given, expected):
    result = await normalize_size(given, tool_context)
    assert result["status"] == "success"
    assert result["catalog_size"] == expected


async def test_a_conversion_is_explained(tool_context):
    """The customer should know their US 10 became an EU 44."""
    result = await normalize_size("US 10", tool_context)
    assert "EU 44" in result["note"]
    assert result["interpreted_as"] == "US 10"


async def test_a_bare_number_is_read_as_eu_but_flagged(tool_context):
    """"43" is ambiguous; the catalogue reading is stated so it can be corrected."""
    result = await normalize_size("43", tool_context)
    assert result["catalog_size"] == "43"
    assert "Confirm with the customer" in result["note"]


async def test_an_out_of_range_size_is_refused(tool_context):
    result = await normalize_size("EU 50", tool_context)
    assert result["status"] == "error"
    assert "outside the stocked range" in result["message"]


async def test_an_unmappable_us_size_is_refused(tool_context):
    result = await normalize_size("US 20", tool_context)
    assert result["status"] == "error"


# --- Apparel sizing ---------------------------------------------------
@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("medium", "M"),
        ("MEDIUM", "M"),
        ("small", "S"),
        ("large", "L"),
        ("extra large", "XL"),
        ("XL", "XL"),
        ("xxl", "XXL"),
        ("M", "M"),
    ],
)
async def test_apparel_sizes_resolve(tool_context, given, expected):
    result = await normalize_size(given, tool_context)
    assert result["status"] == "success"
    assert result["catalog_size"] == expected


async def test_apparel_sizes_need_no_conversion_note(tool_context):
    result = await normalize_size("medium", tool_context)
    assert result["note"] == ""


# --- Rejected input ---------------------------------------------------
@pytest.mark.parametrize("given", ["", "   ", "banana", "size of a house"])
async def test_nonsense_is_refused_rather_than_guessed(tool_context, given):
    """Guessing a size risks sending the wrong shoe."""
    result = await normalize_size(given, tool_context)
    assert result["status"] == "error"


async def test_the_refusal_suggests_asking(tool_context):
    result = await normalize_size("banana", tool_context)
    assert "Ask the customer" in result["message"]


# --- Product memory ---------------------------------------------------
async def test_a_product_is_remembered(tool_context):
    result = await remember_product("Nimbus 25", tool_context)
    assert result["status"] == "success"
    assert tool_context.state[StateKeys.LAST_VIEWED_PRODUCT] == "Nimbus 25"


async def test_an_empty_product_name_is_refused(tool_context):
    assert (await remember_product("  ", tool_context))["status"] == "error"


# --- The size table --------------------------------------------------
def test_the_size_table_is_complete_and_honest():
    table = size_conversion_table()
    assert "44" in table["footwear_eu_to_us_mens"]
    assert "XL" in table["apparel_sizes"]
    assert "approximate" in table["note"]


def test_the_conversion_table_is_internally_consistent():
    """Every EU size must map to a distinct US size, or the inverse is lossy."""
    mapping = size_conversion_table()["footwear_eu_to_us_mens"]
    assert len(set(mapping.values())) == len(mapping)
