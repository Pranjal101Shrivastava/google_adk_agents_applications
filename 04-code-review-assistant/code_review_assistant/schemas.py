"""Typed contracts for the code review assistant.

The review pipeline's own hand-offs go through session state rather than
``output_schema``, because each step needs tools and the structured data is
produced by the *tools* rather than by the models. These schemas document the
shapes those tools return, and are used by the tests to pin them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low"]
Dimension = Literal["correctness", "style", "security", "structure"]


class StructuralIssue(BaseModel):
    """One structural problem found by the AST analysis."""

    kind: str = Field(description="Issue category, e.g. 'mutable_default'.")
    severity: Severity
    location: str = Field(description="Function name and/or line number.")
    message: str = Field(description="What is wrong and why it matters.")


class FunctionInfo(BaseModel):
    """Per-function metrics from the AST walk."""

    name: str
    line: int = Field(ge=1)
    args: list[str] = Field(default_factory=list)
    is_async: bool = False
    has_docstring: bool = False
    complexity: int = Field(ge=1, description="Approximate cyclomatic complexity.")
    length: int = Field(ge=0, description="Source lines spanned.")
    mutable_defaults: list[str] = Field(default_factory=list)
    too_complex: bool = False
    too_long: bool = False


class StyleViolation(BaseModel):
    """One pycodestyle violation."""

    line: int = Field(ge=1)
    column: int = Field(ge=1)
    code: str = Field(description="pycodestyle code, e.g. 'E225'.")
    message: str


class SecurityFinding(BaseModel):
    """One security finding."""

    kind: str
    severity: Severity
    line: int = Field(ge=1)
    message: str
    snippet: str = ""


class DimensionScore(BaseModel):
    """One dimension's contribution to the grade."""

    score: int = Field(ge=0, le=100)
    configured_weight: float = Field(ge=0.0, le=1.0)
    effective_weight: float = Field(ge=0.0, le=1.0)
    note: str


class Grade(BaseModel):
    """The weighted grade."""

    score: int = Field(ge=0, le=100)
    band: Literal["A", "B", "C", "D", "F"]
    passed: bool
    breakdown: dict[str, DimensionScore] = Field(default_factory=dict)
    not_assessed: list[str] = Field(
        default_factory=list,
        description="Dimensions that could not be assessed; their weight is redistributed.",
    )
    caps_applied: list[str] = Field(
        default_factory=list,
        description="Rules that overrode the arithmetic, e.g. a critical security cap.",
    )
    summary: str
