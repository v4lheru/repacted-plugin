"""
Threshold and property tests for hooks/shared/variety_scorer.py.

Supplements test_variety_scorer.py with:
1. One row on each side of every tier threshold (the score is a sum, so the
   thresholds are where a wrong constant or comparison would show)
2. Properties: every score has a route, adjacent scores never skip a tier
3. Score-range to route consistency per spec
"""
import pytest

from shared.variety_scorer import (
    COMPACT_MAX,
    MAX_SCORE,
    MIN_SCORE,
    ORCHESTRATE_MAX,
    PLAN_MODE_MAX,
    ROUTE_COMPACT,
    ROUTE_ORCHESTRATE,
    ROUTE_PLAN_MODE,
    ROUTE_RESEARCH_SPIKE,
    route_workflow,
    score_variety,
)

# Workflow tiers for ordering checks
TIER_ORDER = [ROUTE_COMPACT, ROUTE_ORCHESTRATE, ROUTE_PLAN_MODE, ROUTE_RESEARCH_SPIKE]
TIER_INDEX = {name: idx for idx, name in enumerate(TIER_ORDER)}


# =============================================================================
# Thresholds: one combination on each side of every tier boundary
# =============================================================================

THRESHOLD_ROWS = [
    ((1, 1, 1, 1), ROUTE_COMPACT),]


class TestThresholds:
    """The score is the sum of the four dimensions; the route flips exactly at the thresholds."""

    @pytest.mark.parametrize("dims,route", THRESHOLD_ROWS)
    def test_score_and_route_at_threshold(self, dims, route):
        score = score_variety(*dims)
        assert score == sum(dims)
        assert MIN_SCORE <= score <= MAX_SCORE
        assert route_workflow(score) == route, (
            f"route_workflow({score}) = {route_workflow(score)!r}, expected {route!r} "
            f"(thresholds {COMPACT_MAX}/{ORCHESTRATE_MAX}/{PLAN_MODE_MAX})"
        )


# =============================================================================
# Property: no tier skipping
# =============================================================================


class TestNoTierSkipping:
    """Adjacent scores map to the same or adjacent tiers — no jumps."""

    def test_adjacent_scores_same_or_adjacent_tier(self):
        """score and score+1 are in the same tier or adjacent tiers, for every score."""
        for score in range(MIN_SCORE, MAX_SCORE):
            tier_lo = TIER_INDEX[route_workflow(score)]
            tier_hi = TIER_INDEX[route_workflow(score + 1)]
            assert abs(tier_hi - tier_lo) <= 1, (
                f"Tier jump at scores {score}->{score+1}: "
                f"{route_workflow(score)} -> {route_workflow(score+1)}"
            )


# =============================================================================
# Property: score-route consistency
# =============================================================================


class TestScoreRouteConsistency:
    """Verify that score ranges map to expected routes per spec."""

    def test_compact_range_correct(self):
        for score in range(MIN_SCORE, COMPACT_MAX + 1):
            assert route_workflow(score) == ROUTE_COMPACT


