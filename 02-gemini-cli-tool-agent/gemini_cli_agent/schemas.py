"""Typed contracts for the Gemini CLI tool agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TaskKind = Literal[
    "explain_architecture",
    "generate_test_plan",
    "generate_unit_tests",
    "find_implementation",
    "security_review",
    "dependency_audit",
    "migration_assessment",
    "custom",
]


class RepoRequest(BaseModel):
    """What the user asked, normalised into something the pipeline can run."""

    repo_url: str | None = Field(
        default=None,
        description="The git URL to analyse, if the user supplied one.",
    )
    ref: str | None = Field(
        default=None, description="Branch, tag or commit to check out."
    )
    task_kind: TaskKind = Field(description="The category of analysis requested.")
    task_description: str = Field(
        description="The user's request restated as a concrete analysis goal."
    )
    focus_paths: list[str] = Field(
        default_factory=list,
        description="Directories or files the user asked to focus on, if any.",
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Required information still missing, e.g. ['repo_url'].",
    )


class CliStep(BaseModel):
    """One Gemini CLI invocation in the plan."""

    step_id: int = Field(ge=1, description="1-based execution order.")
    purpose: str = Field(description="What this step is meant to establish.")
    task: str = Field(
        description=(
            "The complete instruction to pass to the CLI. Self-contained: the "
            "CLI sees no conversation history."
        )
    )
    subdirectory: str = Field(
        default="",
        description="Repository-relative directory to run in. Empty means the root.",
    )
    depends_on_previous: bool = Field(
        default=False,
        description="True when this step needs an earlier step's findings.",
    )


class CliPlan(BaseModel):
    """The ordered set of CLI invocations that answers the request."""

    objective: str = Field(description="The analysis goal in one sentence.")
    steps: list[CliStep] = Field(
        description="2-5 CLI invocations. Fewer, better-scoped steps beat many vague ones."
    )
    expected_deliverable: str = Field(
        description="What the final answer should look like for this request."
    )


class CliRunRecord(BaseModel):
    """Audit record of one CLI invocation."""

    task: str
    workspace_id: str
    subdirectory: str = "."
    duration_seconds: float = Field(ge=0.0)
    output_chars: int = Field(ge=0)
    truncated: bool = False
    write_enabled: bool = False
