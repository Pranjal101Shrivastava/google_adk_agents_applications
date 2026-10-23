"""Session-state keys used across the lead generation pipeline.

Centralising the keys avoids the classic multi-agent failure mode where one
agent writes ``"patterns"`` and the next reads ``"discovered_patterns"``.
"""

from __future__ import annotations


class StateKeys:
    """Canonical session-state key names."""

    # Brief
    COUNTRY = "country"
    INDUSTRY = "industry"
    BUYER_PERSONA = "buyer_persona"
    VALUE_PROPOSITION = "value_proposition"
    STAGE = "stage"
    CURRENT_TIME = "current_time"
    CURRENT_YEAR = "current_year"

    # Knobs surfaced to prompts
    PATTERN_COMPANY_COUNT = "pattern_company_count"
    LEAD_CANDIDATE_COUNT = "lead_candidate_count"

    # Phase 0
    INTENT_EXTRACTION = "intent_extraction_result"

    # Phase 1
    RESEARCH_PLAN = "research_plan"

    # Phase 2
    REFERENCE_FINDER_OUTPUT = "reference_finder_output"
    REFERENCE_COMPANIES = "reference_companies"
    ALL_RESEARCH_FINDINGS = "all_research_findings"
    RESEARCH_CRITIQUE = "research_critique"
    PATTERN_REPORT = "pattern_report"
    DISCOVERED_PATTERNS = "discovered_patterns"
    PATTERNS_AVAILABLE = "patterns_available"
    COMPANIES_STUDIED = "companies_studied"

    # Phase 3
    LEAD_FINDER_OUTPUT = "lead_finder_output"
    LEAD_CANDIDATES = "lead_candidates"
    SCORED_LEADS = "scored_leads"
    SCORING_TIMESTAMP = "scoring_timestamp"
    FINAL_REPORT = "final_report"

    # Per-index fan-out keys -------------------------------------------
    @staticmethod
    def validation(index: int) -> str:
        """State key holding the validation verdict for reference company *index*."""
        return f"validation_result_{index}"

    @staticmethod
    def signal_output(index: int) -> str:
        """State key holding raw signal research text for reference company *index*."""
        return f"signal_researcher_output_{index}"

    @staticmethod
    def signal_finding(index: int) -> str:
        """State key holding the structured ``SignalFinding`` for company *index*."""
        return f"signal_finding_{index}"

    @staticmethod
    def lead_evidence_output(index: int) -> str:
        """State key holding raw verification text for candidate *index*."""
        return f"lead_evidence_output_{index}"

    @staticmethod
    def lead_evidence(index: int) -> str:
        """State key holding the structured ``LeadEvidence`` for candidate *index*."""
        return f"lead_evidence_{index}"
