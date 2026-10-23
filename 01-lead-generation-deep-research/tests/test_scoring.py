"""Unit tests for the deterministic lead scorer.

These run offline: no model calls, no network, no credentials.
"""

from __future__ import annotations

import pytest

from lead_generation_agent.tools.scoring import (
    DISQUALIFIER_MAX_PENALTY,
    SOURCES_FOR_FULL_CONFIDENCE,
    score_lead,
)

PATTERNS = [
    {"name": "Local leadership hire", "weight": 40},
    {"name": "Regional job postings", "weight": 30},
    {"name": "Trademark filing", "weight": 20},
    {"name": "Conference appearance", "weight": 10},
]

FOUR_SOURCES = [f"https://example.com/{i}" for i in range(SOURCES_FOR_FULL_CONFIDENCE)]


def test_no_matches_scores_zero():
    result = score_lead(PATTERNS, matched_patterns=[], sources=FOUR_SOURCES)
    assert result["fit_score"] == 0
    assert result["tier"] == "D"
    assert result["matched_patterns"] == []
    assert len(result["unmatched_patterns"]) == len(PATTERNS)


def test_all_matches_scores_one_hundred():
    result = score_lead(
        PATTERNS,
        matched_patterns=[p["name"] for p in PATTERNS],
        sources=FOUR_SOURCES,
    )
    assert result["fit_score"] == 100
    assert result["tier"] == "A"
    assert result["unmatched_patterns"] == []


def test_score_is_weight_proportional_not_count_proportional():
    """One heavy pattern must outscore two light ones."""
    heavy = score_lead(PATTERNS, ["Local leadership hire"], FOUR_SOURCES)
    light = score_lead(
        PATTERNS, ["Trademark filing", "Conference appearance"], FOUR_SOURCES
    )
    assert heavy["fit_score"] == 40
    assert light["fit_score"] == 30
    assert heavy["fit_score"] > light["fit_score"]


@pytest.mark.parametrize(
    ("matched", "expected_tier"),
    [
        (["Local leadership hire", "Regional job postings", "Trademark filing"], "A"),
        (["Local leadership hire", "Regional job postings"], "B"),
        (["Local leadership hire", "Conference appearance"], "C"),
        (["Conference appearance"], "D"),
    ],
)
def test_tier_boundaries(matched, expected_tier):
    assert score_lead(PATTERNS, matched, FOUR_SOURCES)["tier"] == expected_tier


def test_pattern_name_matching_is_case_and_space_insensitive():
    """The formatter model drifts on casing; scoring must not care."""
    result = score_lead(
        PATTERNS, ["  local   LEADERSHIP hire "], FOUR_SOURCES
    )
    assert result["fit_score"] == 40
    assert result["matched_patterns"] == ["Local leadership hire"]


def test_hallucinated_pattern_earns_no_points():
    result = score_lead(
        PATTERNS, ["Local leadership hire", "Astrological alignment"], FOUR_SOURCES
    )
    assert result["fit_score"] == 40
    assert "Astrological alignment" not in result["matched_patterns"]


def test_disqualifiers_reduce_the_score():
    clean = score_lead(PATTERNS, [p["name"] for p in PATTERNS], FOUR_SOURCES)
    flagged = score_lead(
        PATTERNS,
        [p["name"] for p in PATTERNS],
        FOUR_SOURCES,
        disqualifiers=["Already announced a local entity"],
    )
    assert flagged["fit_score"] < clean["fit_score"]
    assert flagged["disqualifiers"] == ["Already announced a local entity"]


def test_disqualifier_penalty_is_capped_and_score_never_negative():
    result = score_lead(
        PATTERNS,
        ["Conference appearance"],
        FOUR_SOURCES,
        disqualifiers=[f"problem {i}" for i in range(10)],
    )
    assert result["fit_score"] == 0
    # Cap applies to the penalty, not just to the clamped score.
    assert DISQUALIFIER_MAX_PENALTY == 60


def test_confidence_scales_with_distinct_sources():
    one = score_lead(PATTERNS, ["Local leadership hire"], ["https://example.com/a"])
    many = score_lead(PATTERNS, ["Local leadership hire"], FOUR_SOURCES)
    assert one["confidence"] < many["confidence"]
    assert many["confidence"] == 1.0


def test_duplicate_sources_are_deduplicated():
    dupes = ["https://example.com/a"] * 8
    result = score_lead(PATTERNS, ["Local leadership hire"], dupes)
    assert result["sources"] == ["https://example.com/a"]
    assert result["confidence"] == pytest.approx(0.25, abs=0.01)


def test_confidence_is_discounted_without_any_match():
    """Plenty of URLs about nothing relevant is not confident evidence."""
    result = score_lead(PATTERNS, [], FOUR_SOURCES)
    assert result["confidence"] == 0.25


def test_unweighted_patterns_fall_back_to_match_ratio():
    unweighted = [{"name": "A", "weight": 0}, {"name": "B", "weight": 0}]
    result = score_lead(unweighted, ["A"], FOUR_SOURCES)
    assert result["fit_score"] == 50


def test_empty_pattern_set_is_handled():
    result = score_lead([], ["anything"], FOUR_SOURCES)
    assert result["fit_score"] == 0
    assert result["tier"] == "D"


def test_scoring_is_deterministic():
    args = (PATTERNS, ["Local leadership hire", "Trademark filing"], FOUR_SOURCES)
    assert score_lead(*args) == score_lead(*args)
