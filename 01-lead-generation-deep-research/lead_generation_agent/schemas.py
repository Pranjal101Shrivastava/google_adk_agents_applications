"""Pydantic contracts exchanged between the lead generation sub-agents.

Every structured hand-off in the pipeline is typed. That keeps the LLM steps
honest (ADK enforces ``output_schema``) and lets the deterministic tools and
the unit tests operate on real objects instead of free-form text.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Stage = Literal[
    "clarify",
    "pattern_discovery",
    "lead_generation",
    "follow_up",
    "chitchat",
]

Intent = Literal[
    "find_leads",
    "find_patterns",
    "company_research",
    "general_chat",
]


# --------------------------------------------------------------------------
# Phase 0 - intent extraction
# --------------------------------------------------------------------------
class IntentExtraction(BaseModel):
    """Normalised view of what the user asked for."""

    country: str | None = Field(
        default=None,
        description="Target market/country, e.g. 'Thailand', 'Germany', 'United States'.",
    )
    industry: str | None = Field(
        default=None,
        description="Target industry or sector, e.g. 'fintech', 'medtech', 'logistics SaaS'.",
    )
    buyer_persona: str | None = Field(
        default=None,
        description="Who we sell to inside the target account, e.g. 'VP of Engineering'.",
    )
    value_proposition: str | None = Field(
        default=None,
        description="What the user sells, used to judge account fit.",
    )
    stage: Stage = Field(description="Which phase of the workflow should run next.")
    intent: Intent = Field(description="The user's primary goal.")
    confidence: float = Field(ge=0.0, le=1.0, description="Extraction confidence 0-1.")
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Required fields that are still unknown and must be asked for.",
    )
    reasoning: str = Field(description="One or two sentences explaining the decision.")


# --------------------------------------------------------------------------
# Phase 1 - research planning
# --------------------------------------------------------------------------
class ResearchQuestion(BaseModel):
    """A single answerable sub-question of the overall research brief."""

    question: str = Field(description="A specific, searchable research question.")
    rationale: str = Field(description="Why answering this advances the brief.")
    search_queries: list[str] = Field(
        default_factory=list,
        description="2-4 concrete web search queries that would answer the question.",
    )


class ResearchPlan(BaseModel):
    """The decomposition produced before any searching happens."""

    objective: str = Field(description="The research objective in one sentence.")
    questions: list[ResearchQuestion] = Field(
        description="The decomposed sub-questions, ordered by importance."
    )
    success_criteria: list[str] = Field(
        default_factory=list,
        description="Observable conditions that mean the research is complete.",
    )


# --------------------------------------------------------------------------
# Phase 2 - pattern discovery over reference accounts
# --------------------------------------------------------------------------
class ReferenceCompany(BaseModel):
    """A company that already did the thing we want to predict."""

    company_name: str = Field(description="Legal or commonly used company name.")
    country_of_origin: str = Field(description="Headquarters country.")
    expansion_type: str = Field(
        description="Nature of the move, e.g. 'new office', 'acquisition', 'joint venture'."
    )
    expansion_date: str = Field(description="When it happened, e.g. '2024-Q3' or '2023-11'.")
    business_description: str = Field(description="One sentence on what the company does.")
    source_url: str = Field(description="URL backing the claim.")


class ReferenceCompanySet(BaseModel):
    """Structured output of the reference-company finder."""

    companies: list[ReferenceCompany] = Field(
        default_factory=list, description="Reference companies matching the brief."
    )


class ValidationVerdict(BaseModel):
    """Whether a candidate really belongs in the reference set."""

    company_name: str = Field(description="The company being judged.")
    is_valid: bool = Field(description="True when every validation criterion holds.")
    reasoning: str = Field(description="Short justification for the verdict.")
    corrected_country_of_origin: str | None = Field(
        default=None, description="Corrected HQ country when the original was wrong."
    )


class SignalFinding(BaseModel):
    """Pre-move signals observed for one reference company."""

    company_name: str = Field(description="The company researched.")
    summary: str = Field(
        description="What observable signals preceded the expansion, with dates."
    )
    signals: list[str] = Field(
        default_factory=list,
        description="Discrete signal statements, e.g. 'opened 4 local job reqs 6 months prior'.",
    )
    sources: list[str] = Field(default_factory=list, description="Supporting URLs.")


class PatternSignal(BaseModel):
    """One recurring, checkable predictor distilled from the reference set."""

    name: str = Field(description="Short label for the signal.")
    description: str = Field(description="What the signal looks like in the wild.")
    evidence_count: int = Field(
        ge=0, description="How many reference companies exhibited this signal."
    )
    lead_time: str = Field(
        description="Typical gap between the signal and the move, e.g. '4-8 months'."
    )
    how_to_detect: str = Field(
        description="A concrete way to check the signal for a new company."
    )
    weight: int = Field(
        ge=0, le=100, description="Relative predictive weight 0-100 for scoring."
    )


class PatternReport(BaseModel):
    """Structured result of the pattern discovery phase."""

    market: str = Field(description="The market the patterns apply to.")
    industry: str = Field(description="The industry the patterns apply to.")
    companies_studied: int = Field(ge=0, description="Size of the validated reference set.")
    patterns: list[PatternSignal] = Field(
        default_factory=list, description="Recurring predictive signals."
    )
    caveats: list[str] = Field(
        default_factory=list, description="Known limits of this analysis."
    )


# --------------------------------------------------------------------------
# Phase 3 - lead generation and qualification
# --------------------------------------------------------------------------
class LeadCandidate(BaseModel):
    """A company that has not moved yet but looks like it is about to."""

    company_name: str = Field(description="Legal or commonly used company name.")
    country_of_origin: str = Field(description="Headquarters country.")
    business_description: str = Field(description="One sentence on what the company does.")
    website: str | None = Field(default=None, description="Primary website if known.")


class LeadCandidateSet(BaseModel):
    """Structured output of the lead finder."""

    candidates: list[LeadCandidate] = Field(default_factory=list)


class LeadEvidence(BaseModel):
    """Evidence gathered for one candidate against the discovered patterns."""

    company_name: str = Field(description="The candidate company.")
    matched_patterns: list[str] = Field(
        default_factory=list,
        description="Names of PatternSignal entries this candidate exhibits.",
    )
    summary: str = Field(description="Narrative of what was found, with dates.")
    sources: list[str] = Field(default_factory=list, description="Supporting URLs.")
    disqualifiers: list[str] = Field(
        default_factory=list,
        description="Facts that argue against this being a live opportunity.",
    )


class ScoredLead(BaseModel):
    """A candidate after deterministic scoring."""

    company_name: str
    fit_score: int = Field(ge=0, le=100, description="Weighted pattern-match score 0-100.")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Evidence confidence, driven by source count."
    )
    tier: Literal["A", "B", "C", "D"] = Field(description="Priority tier, A is hottest.")
    matched_patterns: list[str] = Field(default_factory=list)
    unmatched_patterns: list[str] = Field(default_factory=list)
    disqualifiers: list[str] = Field(default_factory=list)
    rationale: str = Field(description="Why the lead landed in this tier.")
    sources: list[str] = Field(default_factory=list)


class QualifiedLeadReport(BaseModel):
    """The deliverable handed back to the user."""

    market: str
    industry: str
    generated_at: str = Field(description="ISO-8601 timestamp of generation.")
    leads: list[ScoredLead] = Field(default_factory=list)
    patterns_used: list[str] = Field(default_factory=list)
    methodology: str = Field(description="How the leads were found and scored.")
    next_actions: list[str] = Field(
        default_factory=list, description="Recommended outreach steps."
    )


# --------------------------------------------------------------------------
# Research critic (loop control)
# --------------------------------------------------------------------------
class ResearchCritique(BaseModel):
    """Gate that decides whether another research pass is worthwhile."""

    is_sufficient: bool = Field(
        description="True when the evidence answers the plan's success criteria."
    )
    gaps: list[str] = Field(
        default_factory=list, description="What is still missing or unsupported."
    )
    follow_up_queries: list[str] = Field(
        default_factory=list, description="Searches that would close the gaps."
    )
    reasoning: str = Field(description="Short justification for the gate decision.")
