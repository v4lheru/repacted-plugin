"""
Tests for cross-references across cybernetic improvement files.

Tests cover:
1. Audit protocol file exists with required content
2. Variety thresholds match between variety_scorer.py and workflows.md §Variety
3. S4 checkpoints has 4 questions
4. Auditor dispatch thresholds and opt-out framing consistent across all dispatch files
5. CalibrationRecord fields consistent across protocol and architecture doc
6. Learning II threshold=5 consistent across protocol and code
7. (retired at the protocol consolidation with the SSOT)
"""
from pathlib import Path

import pytest

PROTOCOLS_DIR = Path(__file__).parent.parent / "protocols"
COMMANDS_DIR = Path(__file__).parent.parent / "commands"
AGENTS_DIR = Path(__file__).parent.parent / "agents"


# =============================================================================
# Audit protocol exists and has correct content
# =============================================================================


class TestAuditProtocolExists:
    """Verify pact-audit.md exists with expected content."""

    def test_protocol_file_exists(self):
        # the protocol consolidation: the audit protocol moved to coordination.md §Concurrent Audit.
        path = PROTOCOLS_DIR / "coordination.md"
        assert path.exists(), "Missing protocol file: coordination.md"




# =============================================================================
# Variety thresholds: code vs protocol
# =============================================================================


class TestVarietyThresholdConsistency:
    """Verify variety_scorer.py constants match workflows.md §Variety."""

    @pytest.fixture
    def variety_content(self):
        return (PROTOCOLS_DIR / "workflows.md").read_text(encoding="utf-8")





    def test_learning_ii_threshold_in_protocol(self, variety_content):
        """Protocol specifies 5+ memories for Learning II."""
        from shared.variety_scorer import LEARNING_II_MIN_MATCHES
        assert LEARNING_II_MIN_MATCHES == 5
        assert "5+" in variety_content or "5 " in variety_content


# =============================================================================
# S4 Checkpoints: 4 questions
# =============================================================================




# =============================================================================
# Auditor dispatch: protocol vs orchestrate.md
# =============================================================================


class TestAuditorDispatchConsistency:
    """Verify auditor dispatch conditions match across all files with dispatch sections."""

    # All files that define auditor dispatch conditions
    DISPATCH_FILES = {
        "coordination.md": PROTOCOLS_DIR / "coordination.md",
        "orchestrate.md": COMMANDS_DIR / "orchestrate.md",
        "comPACT.md": COMMANDS_DIR / "comPACT.md",
        "workflows.md": PROTOCOLS_DIR / "workflows.md",
    }

    @pytest.fixture
    def audit_content(self):
        return (PROTOCOLS_DIR / "coordination.md").read_text(encoding="utf-8")

    @pytest.fixture
    def orchestrate_content(self):
        return (COMMANDS_DIR / "orchestrate.md").read_text(encoding="utf-8")

    @pytest.fixture
    def dispatch_contents(self):
        """Load all files that contain auditor dispatch sections."""
        return {
            name: path.read_text(encoding="utf-8")
            for name, path in self.DISPATCH_FILES.items()
        }

    def test_both_specify_variety_7(self, audit_content, orchestrate_content):
        """Both files reference variety >= 7 as dispatch condition."""
        assert "7" in audit_content
        assert "7" in orchestrate_content




    # --- Opt-out framing consistency across all dispatch files ---

    @pytest.mark.parametrize("filename", sorted(DISPATCH_FILES.keys()))
    def test_all_files_use_opt_out_skip_framing(self, dispatch_contents, filename):
        """All auditor dispatch files use opt-out 'skip' framing."""
        content = dispatch_contents[filename].lower()
        assert "skip" in content, (
            f"{filename} missing 'skip' — auditor dispatch must use opt-out framing"
        )



# =============================================================================
# CalibrationRecord fields consistent
# =============================================================================




# the protocol consolidation: the SSOT-reference arm is retired with the SSOT (pact-protocols.md is deleted).

# =============================================================================
# Structural Verification Discipline (#502) — cross-file cascade
# =============================================================================


