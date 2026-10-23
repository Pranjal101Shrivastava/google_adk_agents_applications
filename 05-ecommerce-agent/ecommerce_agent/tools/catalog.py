"""Deterministic catalogue helpers.

Small, boring normalisation jobs that a model gets right most of the time —
which is the problem. "Most of the time" on size conversion means a customer
occasionally receives the wrong shoe.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from google.adk.tools import ToolContext

from ..constants import StateKeys

logger = logging.getLogger(__name__)

# EU <-> US/UK running shoe sizes. Approximate by nature, which is exactly why
# it belongs in a reviewable table rather than in a model's head.
_EU_TO_US_MENS: dict[str, str] = {
    "39": "6.5", "40": "7", "41": "8", "42": "8.5", "43": "9.5",
    "44": "10", "45": "11", "46": "11.5", "47": "12.5", "48": "13",
}
_US_MENS_TO_EU = {v: k for k, v in _EU_TO_US_MENS.items()}

_APPAREL_SIZES = ("XS", "S", "M", "L", "XL", "XXL", "3XL")

_SIZE_PATTERN = re.compile(
    r"^\s*(?:(?P<system>eu|us|uk)\s*)?(?P<value>\d{1,2}(?:\.5)?|[a-zA-Z]{1,3})\s*(?:(?P<trailing>eu|us|uk))?\s*$",
    re.IGNORECASE,
)


async def normalize_size(
    size_text: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Convert a size the customer typed into the catalogue's EU sizing.

    The catalogue stores footwear in EU sizes and apparel as XS-3XL. Customers
    say "US 9.5", "size nine and a half", "medium". Call this before searching
    by size, so the query uses a value that exists in the database.

    Args:
        size_text: Whatever the customer said, e.g. ``"US 10"``, ``"43"``,
            ``"medium"``.
        tool_context: ADK tool context (supplied by the runtime).

    Returns:
        The ``catalog_size`` to query with, what it was ``interpreted_as``, and
        a ``note`` to relay when a conversion was involved. On an ambiguous
        input, a question to ask instead of a guess.
    """
    raw = (size_text or "").strip()
    if not raw:
        return {"status": "error", "message": "No size was given."}

    upper = raw.upper()

    # Apparel words and letters.
    word_sizes = {
        "EXTRA SMALL": "XS", "SMALL": "S", "MEDIUM": "M", "LARGE": "L",
        "EXTRA LARGE": "XL", "XXL": "XXL", "XL": "XL",
    }
    if upper in word_sizes or upper in _APPAREL_SIZES:
        catalog = word_sizes.get(upper, upper)
        return {
            "status": "success",
            "catalog_size": catalog,
            "interpreted_as": f"apparel size {catalog}",
            "note": "",
        }

    match = _SIZE_PATTERN.match(raw)
    if not match:
        return {
            "status": "error",
            "message": (
                f"Could not interpret {raw!r} as a size. Ask the customer "
                "whether they mean EU or US, and for the number."
            ),
        }

    system = (match.group("system") or match.group("trailing") or "").upper()
    value = match.group("value").upper()

    if value in _APPAREL_SIZES:
        return {
            "status": "success",
            "catalog_size": value,
            "interpreted_as": f"apparel size {value}",
            "note": "",
        }

    if system == "EU" or (not system and value in _EU_TO_US_MENS):
        if value not in _EU_TO_US_MENS:
            return {
                "status": "error",
                "message": (
                    f"EU {value} is outside the stocked range "
                    f"({min(_EU_TO_US_MENS)}-{max(_EU_TO_US_MENS)})."
                ),
            }
        note = ""
        if not system:
            note = (
                f"Read {value} as EU {value}, which is the catalogue's sizing. "
                f"Confirm with the customer if they may have meant US "
                f"{_EU_TO_US_MENS[value]}."
            )
        return {
            "status": "success",
            "catalog_size": value,
            "interpreted_as": f"EU {value}",
            "note": note,
        }

    if system in ("US", "UK"):
        catalog = _US_MENS_TO_EU.get(value)
        if not catalog:
            return {
                "status": "error",
                "message": (
                    f"{system} {value} does not map to a stocked EU size. Ask "
                    "for the nearest whole size."
                ),
            }
        return {
            "status": "success",
            "catalog_size": catalog,
            "interpreted_as": f"{system} {value}",
            "note": (
                f"{system} {value} is EU {catalog} in our sizing. Sizing varies "
                "by model, so mention that if fit is a concern."
            ),
        }

    return {
        "status": "error",
        "message": (
            f"{raw!r} is ambiguous. Ask whether it is EU or US sizing before "
            "searching - guessing risks sending the wrong size."
        ),
    }


async def remember_product(
    product_name: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Record which product the customer is currently discussing.

    Call this after showing a product's details, so a follow-up like "add it
    in a 43" resolves without asking the customer to repeat themselves.

    Args:
        product_name: The product's exact catalogue name.
        tool_context: ADK tool context (supplied by the runtime).

    Returns:
        Confirmation of what was recorded.
    """
    name = (product_name or "").strip()
    if not name:
        return {"status": "error", "message": "No product name was given."}

    tool_context.state[StateKeys.LAST_VIEWED_PRODUCT] = name
    return {
        "status": "success",
        "remembered": name,
        "note": f"'{name}' is now the product 'it' refers to.",
    }


def size_conversion_table() -> dict[str, Any]:
    """Return the full EU/US size table.

    Call this when a customer asks about sizing in general rather than about
    one size.

    Returns:
        The EU-to-US footwear mapping and the apparel sizes stocked.
    """
    return {
        "footwear_eu_to_us_mens": dict(_EU_TO_US_MENS),
        "apparel_sizes": list(_APPAREL_SIZES),
        "note": (
            "Footwear conversions are approximate and vary by model. The "
            "catalogue is stored in EU sizes."
        ),
    }
