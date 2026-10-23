"""Tests for the structured contracts between sub-agents."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lead_generation_agent.schemas import (
    IntentExtraction,
    LeadEvidence,
    PatternReport,
    PatternSignal,
    ReferenceCompany,
    ResearchCritique,
    ScoredLead,
)


def test_intent_extraction_minimal_payload():
    extraction = IntentExtraction(
        stage="clarify",
        intent="find_leads",
        confidence=0.5,
        reasoning="nothing specified yet",
    )
    assert extraction.country is None
    assert extraction.missing_fields == []


def test_intent_extraction_rejects_unknown_stage():
    with pytest.raises(ValidationError):
        IntentExtraction(
            stage="world_domination",
            intent="find_leads",
            confidence=0.5,
            reasoning="nope",
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_intent_extraction_confidence_is_bounded(confidence):
    with pytest.raises(ValidationError):
        IntentExtraction(
            stage="clarify",
            intent="find_leads",
            confidence=confidence,
            reasoning="out of range",
        )


def test_pattern_signal_weight_is_bounded():
    with pytest.raises(ValidationError):
        PatternSignal(
            name="Over-weighted",
            description="d",
            evidence_count=1,
            lead_time="1 month",
            how_to_detect="h",
            weight=101,
        )


def test_scored_lead_rejects_an_invalid_tier():
    with pytest.raises(ValidationError):
        ScoredLead(
            company_name="A",
            fit_score=90,
            confidence=1.0,
            tier="S",
            rationale="not a real tier",
        )


def test_scored_lead_round_trips_through_json():
    lead = ScoredLead(
        company_name="StrongCo",
        fit_score=88,
        confidence=1.0,
        tier="A",
        matched_patterns=["Local leadership hire"],
        rationale="strong signals",
        sources=["https://example.com/a"],
    )
    assert ScoredLead.model_validate_json(lead.model_dump_json()) == lead


def test_reference_company_requires_a_source():
    with pytest.raises(ValidationError):
        ReferenceCompany(
            company_name="A",
            country_of_origin="Japan",
            expansion_type="new office",
            expansion_date="2024-Q1",
            business_description="d",
        )


def test_lead_evidence_defaults_to_empty_lists():
    evidence = LeadEvidence(company_name="A", summary="nothing found")
    assert evidence.matched_patterns == []
    assert evidence.sources == []
    assert evidence.disqualifiers == []


def test_pattern_report_accepts_an_empty_pattern_list():
    """A failed research run is a valid, representable outcome."""
    report = PatternReport(
        market="Thailand",
        industry="fintech",
        companies_studied=0,
        caveats=["no reference companies found"],
    )
    assert report.patterns == []


def test_research_critique_gate_shape():
    critique = ResearchCritique(
        is_sufficient=False,
        gaps=["no hiring data for Company X"],
        follow_up_queries=["Company X Thailand job postings 2024"],
        reasoning="only one company has dated signals",
    )
    assert critique.is_sufficient is False
    assert critique.follow_up_queries
