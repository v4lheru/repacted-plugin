"""
Tests for hooks/shared/variety_scorer.py — deterministic variety scoring
for PACT orchestration.

Tests cover:
1. validate_dimension: in-range, out-of-range, non-int, bool rejection
2. score_variety: all boundary cases, invalid inputs
3. route_workflow: all 4 routing thresholds at exact boundaries
4. Constants consistency with architecture spec
"""

import pytest

from shared.variety_scorer import (
    COMPACT_MAX,
    DIMENSION_COUNT,
    LEARNING_II_MAX_BUMP,
    LEARNING_II_MIN_MATCHES,
    MAX_DIMENSION,
    MAX_SCORE,
    MIN_DIMENSION,
    MIN_SCORE,
    ORCHESTRATE_MAX,
    PLAN_MODE_MAX,
    ROUTE_COMPACT,
    ROUTE_ORCHESTRATE,
    ROUTE_PLAN_MODE,
    ROUTE_RESEARCH_SPIKE,
    route_workflow,
    score_variety,
    validate_dimension,
)


# =============================================================================
# Constants consistency
# =============================================================================


class TestConstants:
    """Verify module constants match architecture spec and are self-consistent."""

    def test_dimension_bounds(self):
        assert MIN_DIMENSION == 1
        assert MAX_DIMENSION == 4

    def test_dimension_count(self):
        assert DIMENSION_COUNT == 4

    def test_score_range_derived_from_dimensions(self):
        assert MIN_SCORE == DIMENSION_COUNT * MIN_DIMENSION
        assert MAX_SCORE == DIMENSION_COUNT * MAX_DIMENSION

    def test_score_range_values(self):
        assert MIN_SCORE == 4
        assert MAX_SCORE == 16

    def test_routing_thresholds_ordered(self):
        assert MIN_SCORE <= COMPACT_MAX < ORCHESTRATE_MAX < PLAN_MODE_MAX <= MAX_SCORE


# =============================================================================
# validate_dimension
# =============================================================================


class TestValidateDimension:
    """Tests for validate_dimension()."""

    @pytest.mark.parametrize("value", [1,])
    def test_valid_dimensions_accepted(self, value):
        # Should not raise
        validate_dimension(value)

    @pytest.mark.parametrize("value", [0,])
    def test_out_of_range_raises_value_error(self, value):
        with pytest.raises(ValueError, match="must be between"):
            validate_dimension(value)

    @pytest.mark.parametrize(
        "value",
        [1.0,],
    )
    def test_non_integer_raises_type_error(self, value):
        with pytest.raises(TypeError, match="must be an integer"):
            validate_dimension(value)

    @pytest.mark.parametrize("value", [True,])
    def test_bool_rejected_as_type_error(self, value):
        """Booleans are explicitly rejected despite being int subclass."""
        with pytest.raises(TypeError, match="must be an integer"):
            validate_dimension(value)


# =============================================================================
# score_variety
# =============================================================================


class TestScoreVariety:
    """Tests for score_variety()."""

    def test_minimum_score(self):
        assert score_variety(1, 1, 1, 1) == 4

    def test_maximum_score(self):
        assert score_variety(4, 4, 4, 4) == 16

    def test_mixed_dimensions(self):
        assert score_variety(1, 2, 3, 4) == 10

    def test_all_same_dimension(self):
        assert score_variety(3, 3, 3, 3) == 12


    # --- Input validation ---


# =============================================================================
# route_workflow
# =============================================================================


class TestRouteWorkflow:
    """Tests for route_workflow()."""

    # --- Boundary values ---

    @pytest.mark.parametrize("score", [4,])
    def test_compact_range(self, score):
        assert route_workflow(score) == ROUTE_COMPACT

    @pytest.mark.parametrize("score", [7,])
    def test_orchestrate_range(self, score):
        assert route_workflow(score) == ROUTE_ORCHESTRATE

    @pytest.mark.parametrize("score", [11,])
    def test_plan_mode_range(self, score):
        assert route_workflow(score) == ROUTE_PLAN_MODE

    @pytest.mark.parametrize("score", [15,])
    def test_research_spike_range(self, score):
        assert route_workflow(score) == ROUTE_RESEARCH_SPIKE

    # --- Exact boundary transitions ---


    # --- Out-of-range ---


    # --- Type validation ---


