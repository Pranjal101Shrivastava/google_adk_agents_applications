"""Configuration for the Code Review Assistant.

Two groups of settings matter here. The **grading weights** decide what the
review optimises for, and they are validated to sum to 1.0 so a typo cannot
silently skew every score. The **execution limits** bound what generated test
code may do; see ``tools/execution.py`` for what they can and cannot promise.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ReviewConfig(BaseSettings):
    """Validated settings for the code review assistant."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Model routing -------------------------------------------------
    # Analysis and mechanical steps.
    worker_model: str = Field(default="gemini-2.5-flash", alias="WORKER_MODEL")
    # Feedback synthesis and code fixing, where judgement matters.
    critic_model: str = Field(default="gemini-2.5-pro", alias="CRITIC_MODEL")

    # --- Google Cloud --------------------------------------------------
    google_cloud_project: str | None = Field(default=None, alias="GOOGLE_CLOUD_PROJECT")
    google_cloud_location: str = Field(
        default="us-central1", alias="GOOGLE_CLOUD_LOCATION"
    )
    artifact_bucket: str | None = Field(default=None, alias="ARTIFACT_BUCKET")
    staging_bucket: str | None = Field(default=None, alias="STAGING_BUCKET")

    # --- Grading weights (must sum to 1.0) -----------------------------
    correctness_weight: float = Field(default=0.40, ge=0.0, le=1.0, alias="CORRECTNESS_WEIGHT")
    style_weight: float = Field(default=0.20, ge=0.0, le=1.0, alias="STYLE_WEIGHT")
    security_weight: float = Field(default=0.25, ge=0.0, le=1.0, alias="SECURITY_WEIGHT")
    structure_weight: float = Field(default=0.15, ge=0.0, le=1.0, alias="STRUCTURE_WEIGHT")

    # --- Review thresholds ---------------------------------------------
    passing_score: int = Field(default=70, ge=0, le=100, alias="PASSING_SCORE")
    max_line_length: int = Field(default=100, ge=60, le=200, alias="MAX_LINE_LENGTH")
    # Functions above this cyclomatic complexity are flagged.
    max_complexity: int = Field(default=10, ge=1, le=50, alias="MAX_COMPLEXITY")
    # Functions longer than this many lines are flagged.
    max_function_lines: int = Field(default=50, ge=5, le=500, alias="MAX_FUNCTION_LINES")

    # --- Code execution limits -----------------------------------------
    enable_code_execution: bool = Field(default=True, alias="ENABLE_CODE_EXECUTION")
    execution_timeout_seconds: int = Field(
        default=30, ge=1, le=300, alias="EXECUTION_TIMEOUT_SECONDS"
    )
    execution_memory_mb: int = Field(
        default=512, ge=64, le=4096, alias="EXECUTION_MEMORY_MB"
    )
    execution_cpu_seconds: int = Field(
        default=20, ge=1, le=120, alias="EXECUTION_CPU_SECONDS"
    )
    # Largest submission accepted, in characters. Guards both the model context
    # and the AST walk.
    max_code_chars: int = Field(default=200_000, ge=1_000, alias="MAX_CODE_CHARS")

    # --- Fix loop ------------------------------------------------------
    max_fix_attempts: int = Field(default=3, ge=1, le=10, alias="MAX_FIX_ATTEMPTS")

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
    def _check_weights(self) -> ReviewConfig:
        """Weights must sum to 1.0, or every score is quietly wrong."""
        total = (
            self.correctness_weight
            + self.style_weight
            + self.security_weight
            + self.structure_weight
        )
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                "Grading weights must sum to 1.0, got "
                f"{total:.3f} (correctness={self.correctness_weight}, "
                f"style={self.style_weight}, security={self.security_weight}, "
                f"structure={self.structure_weight})"
            )
        return self

    @model_validator(mode="after")
    def _propagate_to_environment(self) -> ReviewConfig:
        if self.google_cloud_project:
            os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.google_cloud_project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", self.google_cloud_location)
        return self

    @property
    def weights(self) -> dict[str, float]:
        """The grading weights, keyed by dimension."""
        return {
            "correctness": self.correctness_weight,
            "style": self.style_weight,
            "security": self.security_weight,
            "structure": self.structure_weight,
        }


@lru_cache(maxsize=1)
def get_config() -> ReviewConfig:
    """Return the process-wide configuration singleton."""
    cfg = ReviewConfig()
    logging.getLogger().setLevel(cfg.log_level)
    return cfg


config = get_config()
