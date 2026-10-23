"""Configuration for the MCP Bug Assistant.

This agent is defined mostly by what it connects *to*: a ticket database behind
MCP Toolbox, a GitHub MCP server, a Q&A search API. Each of those is optional,
and the settings below are what decide whether a tool group is wired in. An
agent that refuses to start because one backend is unconfigured is useless in
development, so every integration is independently switchable.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Read-only GitHub MCP tools. Deliberately an allowlist: the GitHub MCP server
# also exposes write tools, and a bug assistant has no business closing issues
# or pushing branches on its own.
DEFAULT_GITHUB_TOOL_FILTER = (
    "search_repositories",
    "search_issues",
    "list_issues",
    "get_issue",
    "list_pull_requests",
    "get_pull_request",
    "get_file_contents",
    "search_code",
)


class BugAssistantConfig(BaseSettings):
    """Validated settings for the bug assistant."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Model routing -------------------------------------------------
    agent_model: str = Field(default="gemini-2.5-flash", alias="AGENT_MODEL")
    search_model: str = Field(default="gemini-2.5-flash", alias="SEARCH_MODEL")

    # --- Google Cloud --------------------------------------------------
    google_cloud_project: str | None = Field(default=None, alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(
        default="us-central1", alias="GOOGLE_CLOUD_LOCATION"
    )

    # --- MCP Toolbox for Databases (the ticket database) ---------------
    mcp_toolbox_url: str = Field(
        default="http://127.0.0.1:5000", alias="MCP_TOOLBOX_URL"
    )
    mcp_toolbox_toolset: str = Field(
        default="tickets_toolset", alias="MCP_TOOLBOX_TOOLSET"
    )
    enable_toolbox: bool = Field(default=True, alias="ENABLE_TOOLBOX")

    # --- GitHub MCP server ---------------------------------------------
    github_mcp_url: str = Field(
        default="https://api.githubcopilot.com/mcp/", alias="GITHUB_MCP_URL"
    )
    github_token: str | None = Field(
        default=None, alias="GITHUB_PERSONAL_ACCESS_TOKEN"
    )
    github_tool_filter: tuple[str, ...] = Field(
        default=DEFAULT_GITHUB_TOOL_FILTER, alias="GITHUB_TOOL_FILTER"
    )

    # --- Stack Overflow / Stack Exchange -------------------------------
    enable_stackoverflow: bool = Field(default=True, alias="ENABLE_STACKOVERFLOW")
    stackexchange_site: str = Field(default="stackoverflow", alias="STACKEXCHANGE_SITE")
    # Optional; raises the anonymous quota from 300/day to 10,000/day.
    stackexchange_key: str | None = Field(default=None, alias="STACKEXCHANGE_KEY")
    stackexchange_timeout_seconds: int = Field(
        default=15, ge=1, le=120, alias="STACKEXCHANGE_TIMEOUT_SECONDS"
    )
    stackexchange_max_results: int = Field(
        default=5, ge=1, le=20, alias="STACKEXCHANGE_MAX_RESULTS"
    )

    # --- Built-in Google Search ----------------------------------------
    enable_google_search: bool = Field(default=True, alias="ENABLE_GOOGLE_SEARCH")

    # --- Observability -------------------------------------------------
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("github_tool_filter", mode="before")
    @classmethod
    def _split_filter(cls, value: object) -> object:
        """Accept a comma-separated list from the environment."""
        if isinstance(value, str):
            return tuple(t.strip() for t in value.split(",") if t.strip())
        if isinstance(value, (list, tuple)):
            return tuple(str(t).strip() for t in value if str(t).strip())
        return value

    @field_validator("mcp_toolbox_url", "github_mcp_url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError(f"{value!r} must be an http:// or https:// URL")
        return value.rstrip("/") + ("/" if value.endswith("/") else "")

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}, got {value!r}")
        return upper

    @model_validator(mode="after")
    def _propagate_to_environment(self) -> BugAssistantConfig:
        if self.google_cloud_project:
            os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.google_cloud_project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", self.google_cloud_location)
        return self

    @property
    def github_enabled(self) -> bool:
        """GitHub MCP needs a token; without one the toolset is left out."""
        return bool(self.github_token)


@lru_cache(maxsize=1)
def get_config() -> BugAssistantConfig:
    """Return the process-wide configuration singleton."""
    cfg = BugAssistantConfig()
    logging.getLogger().setLevel(cfg.log_level)
    return cfg


config = get_config()
