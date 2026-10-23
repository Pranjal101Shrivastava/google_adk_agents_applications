"""Configuration for the E-Commerce Agent.

Nothing in this file holds a database credential. The agent reaches the
database only through MCP Toolbox, which holds the credentials itself, so the
agent process cannot leak what it never had. See ``deployment/toolbox/tools.yaml``.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class EcommerceConfig(BaseSettings):
    """Validated settings for the e-commerce agent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Model ---------------------------------------------------------
    agent_model: str = Field(default="gemini-2.5-flash", alias="AGENT_MODEL")

    # --- Google Cloud --------------------------------------------------
    google_cloud_project: str | None = Field(default=None, alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(
        default="us-central1", alias="GOOGLE_CLOUD_LOCATION"
    )

    # --- MCP Toolbox (the only path to the database) -------------------
    toolbox_url: str = Field(default="http://127.0.0.1:5000", alias="TOOLBOX_URL")
    toolbox_toolset: str = Field(default="storefront", alias="TOOLBOX_TOOLSET")
    enable_toolbox: bool = Field(default=True, alias="ENABLE_TOOLBOX")

    # --- Identity ------------------------------------------------------
    # Google OAuth client ID. Used to verify the audience of the ID token the
    # frontend sends. Without it, tokens are not trusted and the agent runs
    # in guest mode.
    google_client_id: str | None = Field(default=None, alias="GOOGLE_CLIENT_ID")
    # Allow unauthenticated browsing. Writes always require a verified identity
    # regardless of this setting.
    allow_guest_browsing: bool = Field(default=True, alias="ALLOW_GUEST_BROWSING")

    # --- Session persistence -------------------------------------------
    # A SQLAlchemy URL for DatabaseSessionService. Unset means in-memory
    # sessions, which are lost on restart and not shared between replicas -
    # fine locally, not fine in production.
    session_db_url: str | None = Field(default=None, alias="SESSION_DB_URL")

    # --- Commerce rules (all money maths is deterministic) -------------
    currency: str = Field(default="EUR", alias="CURRENCY")
    currency_symbol: str = Field(default="€", alias="CURRENCY_SYMBOL")
    tax_rate_percent: Decimal = Field(
        default=Decimal("21.0"), ge=Decimal("0"), le=Decimal("100"), alias="TAX_RATE_PERCENT"
    )
    # Whether catalogue prices already include tax. Getting this wrong is a
    # real-money bug, so it is explicit rather than assumed.
    prices_include_tax: bool = Field(default=True, alias="PRICES_INCLUDE_TAX")
    free_shipping_threshold: Decimal = Field(
        default=Decimal("75.00"), ge=Decimal("0"), alias="FREE_SHIPPING_THRESHOLD"
    )
    max_cart_quantity: int = Field(default=10, ge=1, le=100, alias="MAX_CART_QUANTITY")

    # --- Observability -------------------------------------------------
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("toolbox_url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"TOOLBOX_URL must be an http:// or https:// URL, got {value!r}")
        return value.rstrip("/")

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        if len(value) != 3 or not value.isalpha():
            raise ValueError(f"CURRENCY must be a 3-letter ISO code, got {value!r}")
        return value.upper()

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}, got {value!r}")
        return upper

    @model_validator(mode="after")
    def _propagate_to_environment(self) -> EcommerceConfig:
        if self.google_cloud_project:
            os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.google_cloud_project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", self.google_cloud_location)
        return self

    @property
    def identity_verification_enabled(self) -> bool:
        """Whether ID tokens can be verified at all in this deployment."""
        return bool(self.google_client_id)


@lru_cache(maxsize=1)
def get_config() -> EcommerceConfig:
    """Return the process-wide configuration singleton."""
    cfg = EcommerceConfig()
    logging.getLogger().setLevel(cfg.log_level)
    return cfg


config = get_config()
