"""Configuration for the Deep Research Lead Generation Agent.

All runtime knobs are read from the environment (or a local ``.env``) so the
same package can run unchanged on a laptop, on Cloud Run, or on Vertex AI
Agent Engine.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class LeadGenConfig(BaseSettings):
    """Validated settings for the lead generation agent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Model routing -------------------------------------------------
    # Cheap model for extraction / formatting / validation steps.
    fast_model: str = Field(default="gemini-2.5-flash", alias="GEN_FAST_MODEL")
    # Reasoning model for planning, research and synthesis.
    advanced_model: str = Field(default="gemini-2.5-pro", alias="GEN_ADVANCED_MODEL")

    # --- Google Cloud / Vertex AI -------------------------------------
    google_cloud_project: str | None = Field(default=None, alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(
        default="us-central1", alias="GOOGLE_CLOUD_LOCATION"
    )
    google_cloud_storage_bucket: str | None = Field(
        default=None, alias="GOOGLE_CLOUD_STORAGE_BUCKET"
    )

    # --- Research breadth / depth -------------------------------------
    # How many reference companies the pattern-discovery phase studies.
    default_pattern_companies: int = Field(
        default=5, ge=1, le=15, alias="DEFAULT_PATTERN_COMPANIES"
    )
    # How many candidate leads the lead-generation phase qualifies.
    default_lead_candidates: int = Field(
        default=8, ge=1, le=25, alias="DEFAULT_LEAD_CANDIDATES"
    )
    # Max refinement passes of the research critic loop.
    max_research_iterations: int = Field(
        default=2, ge=1, le=5, alias="MAX_RESEARCH_ITERATIONS"
    )
    # Minimum fit score (0-100) for a lead to be reported as qualified.
    min_qualified_score: int = Field(
        default=55, ge=0, le=100, alias="MIN_QUALIFIED_SCORE"
    )

    # --- Observability -------------------------------------------------
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}, got {value!r}")
        return upper

    @model_validator(mode="after")
    def _propagate_to_environment(self) -> LeadGenConfig:
        """Mirror settings into os.environ for the google-genai client."""
        if self.google_cloud_project:
            os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.google_cloud_project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", self.google_cloud_location)
        return self


@lru_cache(maxsize=1)
def get_config() -> LeadGenConfig:
    """Return the process-wide configuration singleton."""
    cfg = LeadGenConfig()
    logging.getLogger().setLevel(cfg.log_level)
    return cfg


config = get_config()
