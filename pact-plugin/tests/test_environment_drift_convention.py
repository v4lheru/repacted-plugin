"""
Tests for environment drift convention in pact-s2-coordination.md.

Location: pact-plugin/tests/test_environment_drift_convention.py
Purpose: Validates that the S2 coordination protocol includes the
         environment drift detection convention for parallel agent work.
Related: pact-plugin/protocols/pact-s2-coordination.md

Tests cover:
1. Environment Drift Detection section exists
2. References file_tracker
3. Dispatch convention documented
"""
from pathlib import Path

import pytest


# the protocol consolidation: the S2 coordination text moved to coordination.md §Concurrent Dispatch.
S2_PATH = Path(__file__).parent.parent / "protocols" / "coordination.md"


class TestEnvironmentDriftConvention:
    """Tests for environment drift convention in S2 coordination."""

    @pytest.fixture
    def s2_content(self):
        return S2_PATH.read_text(encoding="utf-8")


    def test_references_file_tracker(self, s2_content):
        assert "file_tracker" in s2_content or "file-edits.json" in s2_content

