"""Deterministic lead scoring.

Scoring is intentionally **not** done by an LLM. Given the same patterns and
the same evidence, this module always returns the same score, which makes the
output auditable, unit-testable and comparable across runs. The LLM's job is to
establish *which* patterns a company exhibits; the arithmetic lives here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from google.adk.tools import ToolContext

from ..config import config
from ..constants import StateKeys

logger = logging.getLogger(__name__)

# Tier boundaries on the 0-100 fit score.
TIER_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (80, "A"),
    (60, "B"),
    (40, "C"),
    (0, "D"),
)

# A lead needs at least this many distinct source URLs to reach full confidence.
SOURCES_FOR_FULL_CONFIDENCE = 4

# Each disqualifier removes this many points, capped by DISQUALIFIER_MAX_PENALTY.
DISQUALIFIER_PENALTY = 25
DISQUALIFIER_MAX_PENALTY = 60


def _normalise(name: str) -> str:
    """Fold a pattern name so LLM casing/spacing drift still matches."""
    return " ".join(name.lower().split())


def _tier_for(score: int) -> str:
    for threshold, tier in TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return "D"


def score_lead(
    patterns: list[dict[str, Any]],
    matched_patterns: list[str],
    sources: list[str],
    disqualifiers: list[str] | None = None,
) -> dict[str, Any]:
    """Score one lead against the discovered patterns.

    Pure function - no I/O, no state, no model calls - so it can be exercised
    directly by the unit tests.

    Args:
        patterns: ``PatternSignal``-shaped dicts, each with ``name`` and
            ``weight``.
        matched_patterns: Names of the patterns the researcher found evidence
            for. Matching is case- and whitespace-insensitive.
        sources: Distinct source URLs backing the evidence.
        disqualifiers: Facts arguing against a live opportunity.

    Returns:
        A ``ScoredLead``-shaped dict without the company name, which the caller
        fills in.
    """
    disqualifiers = disqualifiers or []

    weight_by_name = {
        _normalise(str(p.get("name", ""))): max(0, int(p.get("weight", 0) or 0))
        for p in patterns
        if p.get("name")
    }
    total_weight = sum(weight_by_name.values())

    matched_keys = {_normalise(m) for m in matched_patterns if m}
    # Only count matches that correspond to a real pattern; a hallucinated
    # pattern name must not earn points.
    recognised = {k for k in matched_keys if k in weight_by_name}
    unrecognised = sorted(matched_keys - recognised)
    if unrecognised:
        logger.warning(
            "Ignoring %d unrecognised pattern name(s) during scoring: %s",
            len(unrecognised),
            unrecognised,
        )

    matched_weight = sum(weight_by_name[k] for k in recognised)

    if total_weight > 0:
        raw_score = round(100 * matched_weight / total_weight)
    else:
        # No weights available: fall back to the share of patterns matched.
        raw_score = (
            round(100 * len(recognised) / len(weight_by_name)) if weight_by_name else 0
        )

    penalty = min(len(disqualifiers) * DISQUALIFIER_PENALTY, DISQUALIFIER_MAX_PENALTY)
    fit_score = max(0, min(100, raw_score - penalty))

    distinct_sources = {s.strip() for s in sources if s and s.strip()}
    source_confidence = min(1.0, len(distinct_sources) / SOURCES_FOR_FULL_CONFIDENCE)
    # Evidence with no matched patterns is not confident evidence, whatever the
    # URL count.
    confidence = round(source_confidence * (1.0 if recognised else 0.25), 2)

    # Report unmatched/matched using the original pattern spelling.
    original_by_key = {_normalise(str(p["name"])): str(p["name"]) for p in patterns if p.get("name")}
    matched_display = sorted(original_by_key[k] for k in recognised)
    unmatched_display = sorted(
        original_by_key[k] for k in weight_by_name if k not in recognised
    )

    if disqualifiers:
        rationale = (
            f"Matched {len(recognised)}/{len(weight_by_name)} patterns "
            f"({matched_weight}/{total_weight} weight) but carries "
            f"{len(disqualifiers)} disqualifier(s), costing {penalty} points."
        )
    elif recognised:
        rationale = (
            f"Matched {len(recognised)}/{len(weight_by_name)} patterns "
            f"({matched_weight}/{total_weight} weight) across "
            f"{len(distinct_sources)} source(s)."
        )
    else:
        rationale = "No discovered pattern was evidenced for this company."

    return {
        "fit_score": fit_score,
        "confidence": confidence,
        "tier": _tier_for(fit_score),
        "matched_patterns": matched_display,
        "unmatched_patterns": unmatched_display,
        "disqualifiers": list(disqualifiers),
        "rationale": rationale,
        "sources": sorted(distinct_sources),
    }


async def score_leads(tool_context: ToolContext) -> dict[str, Any]:
    """Score every researched lead in session state.

    Reads the pattern report and the per-candidate evidence that the research
    fan-out wrote to state, scores each candidate, sorts by fit score and
    writes the result back to ``StateKeys.SCORED_LEADS``.

    Args:
        tool_context: ADK tool context, used for session state access.

    Returns:
        A status dict with the scored leads and a short summary.
    """
    state = tool_context.state

    pattern_report = state.get(StateKeys.PATTERN_REPORT) or {}
    patterns = pattern_report.get("patterns") or []
    if not patterns:
        return {
            "status": "error",
            "message": (
                "No discovered patterns in session state. Run pattern discovery "
                "before scoring leads."
            ),
        }

    candidate_set = state.get(StateKeys.LEAD_CANDIDATES) or {}
    candidates = candidate_set.get("candidates") or []
    if not candidates:
        return {"status": "error", "message": "No lead candidates in session state."}

    scored: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        evidence = state.get(StateKeys.lead_evidence(index)) or {}
        result = score_lead(
            patterns=patterns,
            matched_patterns=evidence.get("matched_patterns") or [],
            sources=evidence.get("sources") or [],
            disqualifiers=evidence.get("disqualifiers") or [],
        )
        result["company_name"] = candidate.get("company_name", f"candidate-{index}")
        scored.append(result)

    scored.sort(key=lambda lead: (-lead["fit_score"], lead["company_name"]))

    qualified = [s for s in scored if s["fit_score"] >= config.min_qualified_score]

    state[StateKeys.SCORED_LEADS] = scored
    state[StateKeys.SCORING_TIMESTAMP] = datetime.now(timezone.utc).isoformat()

    logger.info(
        "Scored %d candidate(s); %d cleared the %d-point bar.",
        len(scored),
        len(qualified),
        config.min_qualified_score,
    )

    return {
        "status": "success",
        "scored_leads": scored,
        "qualified_count": len(qualified),
        "total_count": len(scored),
        "min_qualified_score": config.min_qualified_score,
        "summary": (
            f"Scored {len(scored)} candidates; {len(qualified)} scored at or above "
            f"{config.min_qualified_score}."
        ),
    }
