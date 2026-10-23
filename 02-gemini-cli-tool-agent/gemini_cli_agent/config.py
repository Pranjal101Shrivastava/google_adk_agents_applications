"""Configuration for the Gemini CLI Tool Agent.

The settings here are mostly *limits*. This agent shells out to an external
binary against cloned third-party source, so every knob that bounds what that
subprocess can do (where it runs, for how long, how much it may clone, which
hosts it may clone from) is configuration rather than a hardcoded constant.
"""

from __future__ import annotations

import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Hosts we are willing to clone from unless the operator overrides the list.
DEFAULT_ALLOWED_HOSTS = ("github.com", "gitlab.com", "bitbucket.org")


class GeminiCliConfig(BaseSettings):
    """Validated settings for the Gemini CLI tool agent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Model routing -------------------------------------------------
    orchestrator_model: str = Field(
        default="gemini-2.5-pro", alias="ORCHESTRATOR_MODEL"
    )
    worker_model: str = Field(default="gemini-2.5-flash", alias="WORKER_MODEL")

    # --- Google Cloud --------------------------------------------------
    google_cloud_project: str | None = Field(default=None, alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(
        default="us-central1", alias="GOOGLE_CLOUD_LOCATION"
    )

    # --- Gemini CLI ----------------------------------------------------
    # Resolved on PATH unless an absolute path is given.
    gemini_cli_binary: str = Field(default="gemini", alias="GEMINI_CLI_BINARY")
    # The model the CLI itself uses, passed through as `-m`.
    gemini_cli_model: str = Field(default="gemini-2.5-pro", alias="GEMINI_CLI_MODEL")
    # Hard wall-clock ceiling for one CLI invocation.
    gemini_cli_timeout_seconds: int = Field(
        default=600, ge=10, le=3600, alias="GEMINI_CLI_TIMEOUT_SECONDS"
    )
    # CLI stdout is fed back into a model prompt, so it must be bounded.
    gemini_cli_max_output_chars: int = Field(
        default=60_000, ge=1_000, alias="GEMINI_CLI_MAX_OUTPUT_CHARS"
    )
    # Whether the CLI may apply edits. Off by default: read-only analysis is the
    # safe default for a service pointed at arbitrary repositories.
    gemini_cli_allow_write: bool = Field(
        default=False, alias="GEMINI_CLI_ALLOW_WRITE"
    )

    # --- Workspace / checkout limits -----------------------------------
    workspace_root: Path = Field(
        default_factory=lambda: Path(tempfile.gettempdir()) / "gemini-cli-agent",
        alias="WORKSPACE_ROOT",
    )
    clone_timeout_seconds: int = Field(
        default=300, ge=10, le=1800, alias="CLONE_TIMEOUT_SECONDS"
    )
    clone_depth: int = Field(default=1, ge=1, le=1000, alias="CLONE_DEPTH")
    max_workspace_mb: int = Field(default=512, ge=1, alias="MAX_WORKSPACE_MB")
    max_concurrent_workspaces: int = Field(
        default=5, ge=1, le=50, alias="MAX_CONCURRENT_WORKSPACES"
    )
    allowed_clone_hosts: tuple[str, ...] = Field(
        default=DEFAULT_ALLOWED_HOSTS, alias="ALLOWED_CLONE_HOSTS"
    )

    # --- Observability -------------------------------------------------
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("allowed_clone_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        """Accept a comma-separated string from the environment."""
        if isinstance(value, str):
            return tuple(h.strip().lower() for h in value.split(",") if h.strip())
        if isinstance(value, (list, tuple)):
            return tuple(str(h).strip().lower() for h in value if str(h).strip())
        return value

    @field_validator("allowed_clone_hosts")
    @classmethod
    def _require_at_least_one_host(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError(
                "ALLOWED_CLONE_HOSTS must list at least one host; an empty allowlist "
                "would either block everything or, worse, be read as 'allow all'."
            )
        return value

    @field_validator("workspace_root", mode="before")
    @classmethod
    def _expand_workspace_root(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}, got {value!r}")
        return upper

    @model_validator(mode="after")
    def _propagate_to_environment(self) -> GeminiCliConfig:
        """Mirror project settings into os.environ for the CLI subprocess.

        The Gemini CLI reads its own credentials from the environment, so the
        values the agent was configured with have to be visible to the child
        process too.
        """
        if self.google_cloud_project:
            os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.google_cloud_project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", self.google_cloud_location)
        return self


@lru_cache(maxsize=1)
def get_config() -> GeminiCliConfig:
    """Return the process-wide configuration singleton."""
    cfg = GeminiCliConfig()
    logging.getLogger().setLevel(cfg.log_level)
    return cfg


config = get_config()
