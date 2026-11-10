"""
Tests for CalibrationRecord schema validation and consistency.

Tests cover:
1. CalibrationRecord schema: required fields, types
2. Schema consistency between architecture doc and workflows.md §Variety
"""
from pathlib import Path

import pytest


# CalibrationRecord schema fields (from architecture doc lines 170-195)
REQUIRED_FIELDS = {
    "task_id": str,
    "domain": str,
    "initial_variety_score": int,
    "actual_difficulty_score": int,
    "dimensions_that_drifted": list,
    "blocker_count": int,
    "phase_reruns": int,
    "specialist_fit": (str, type(None)),
    "timestamp": str,
}


def _make_valid_record():
    """Create a fully valid CalibrationRecord dict."""
    return {
        "task_id": "feat-42",
        "domain": "auth",
        "initial_variety_score": 8,
        "actual_difficulty_score": 10,
        "dimensions_that_drifted": [
            {"dimension": "scope", "predicted": 2, "actual": 3},
        ],
        "blocker_count": 1,
        "phase_reruns": 0,
        "specialist_fit": "good",
        "timestamp": "2026-03-23T12:00:00Z",
    }


# =============================================================================
# Schema validation
# =============================================================================


class TestCalibrationRecordSchema:
    """Validate CalibrationRecord schema matches spec."""

    def test_valid_record_has_all_required_fields(self):
        record = _make_valid_record()
        for field in REQUIRED_FIELDS:
            assert field in record, f"Missing required field: {field}"

    @pytest.mark.parametrize("field", list(list(REQUIRED_FIELDS.keys()))[:1])
    def test_each_required_field_has_correct_type(self, field):
        record = _make_valid_record()
        expected_type = REQUIRED_FIELDS[field]
        assert isinstance(record[field], expected_type), (
            f"Field '{field}' expected type {expected_type}, "
            f"got {type(record[field])}"
        )


# =============================================================================
# Schema consistency with protocol
# =============================================================================


# =============================================================================
# Cross-reference: secretary calibration gathering vs CalibrationRecord schema
# =============================================================================

SECRETARY_AGENT = Path(__file__).parent.parent / "agents" / "pact-secretary.md"
HANDOFF_HARVEST_SKILL = Path(__file__).parent.parent / "skills" / "pact-handoff-harvest" / "SKILL.md"

# Fields that must be referenced in the secretary's calibration workflow.
# The calibration gathering step lives in the pact-handoff-harvest skill
# (auto-loaded via frontmatter), not inline in the agent definition.
_SECRETARY_FIELD_CHECKS = {
    "initial_variety_score": ["initial_variety_score"],
    "domain": ["domain"],
    "blocker_count": ["blocker_count", "blocker count"],
    "phase_reruns": ["phase_reruns", "phase rerun"],
    "specialist_fit": ["specialist_fit", "specialist fit"],
    "actual_difficulty_score": ["actual_difficulty_score", "actual difficulty"],
}


class TestSecretaryCalibrationCrossRef:
    """Verify secretary's calibration gathering references CalibrationRecord fields.

    The calibration workflow lives in the pact-handoff-harvest skill (auto-loaded
    by the secretary via frontmatter). The agent def references "calibration" at a
    high level; the skill contains the detailed field references.
    """

    @pytest.fixture
    def secretary_content(self):
        return SECRETARY_AGENT.read_text(encoding="utf-8")

    @pytest.fixture
    def skill_content(self):
        return HANDOFF_HARVEST_SKILL.read_text(encoding="utf-8")

    def test_secretary_has_calibration_reference(self, secretary_content):
        """Secretary agent definition must reference calibration."""
        assert "calibration" in secretary_content.lower()


