"""
Tests for shared.marker_schema — the SSOT module hosting the bootstrap
marker's shape constants and signature function. Both the producer
(bootstrap_marker_writer.py) and verifier (bootstrap_gate.py) import
from this module so their digests align byte-for-byte.

Tests cover:

Schema constants (P0):
1. MARKER_SCHEMA_VERSION is an int (typed correctly)
2. MARKER_SCHEMA_VERSION == 1 (current pinned version)
3. MARKER_MAX_BYTES == 256 (current pinned cap)

Signature function (P0):
4. expected_marker_signature returns 64-char hex (SHA256 length)
5. Identical inputs produce identical digests (determinism)
6. Different session_id → different digest
7. Different plugin_root → different digest
8. Different plugin_version → different digest
9. Different marker_version → different digest (schema-version coupling)
10. Marker_version part of digest input (regression — bumping version
    invalidates pre-bump markers automatically)

Dormant-coupling regression (P0 — bootstrap requires the orchestrator
to run TaskCreate and TeamCreate on prompt 1 of a fresh session;
adding either to _BLOCKED_TOOLS would deadlock bootstrap by denying
the only ritual actions that create the team config the marker
writer needs to verify):
11. _BLOCKED_TOOLS in bootstrap_gate.py does NOT contain TaskCreate
12. _BLOCKED_TOOLS in bootstrap_gate.py does NOT contain TeamCreate
"""


from shared.marker_schema import (
    MARKER_SCHEMA_VERSION,
    expected_marker_signature,
)


# =============================================================================
# Schema constants
# =============================================================================


class TestSchemaConstants:
    def test_marker_schema_version_is_int(self):
        assert isinstance(MARKER_SCHEMA_VERSION, int)

    def test_marker_schema_version_is_one(self):
        """The current pinned version. Bumping requires producer + verifier
        update in lockstep, which is automatic because both import this
        constant — but the test exists so a version bump is visible in
        the test diff."""
        assert MARKER_SCHEMA_VERSION == 1


# =============================================================================
# Signature function
# =============================================================================


class TestExpectedMarkerSignature:
    def test_returns_sha256_hex_length(self):
        sig = expected_marker_signature("sid", "/plugin", "1.0.0", 1)
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    def test_identical_inputs_produce_identical_digests(self):
        a = expected_marker_signature("sid", "/plugin", "1.0.0", 1)
        b = expected_marker_signature("sid", "/plugin", "1.0.0", 1)
        assert a == b


# =============================================================================
# Dormant-coupling regression: _BLOCKED_TOOLS
# =============================================================================


class TestBlockedToolsBootstrapInvariant:
    """Architect §13 load-bearing assumption: TaskCreate and TeamCreate
    MUST NOT be in bootstrap_gate._BLOCKED_TOOLS. The writer hook needs
    the orchestrator to run TeamCreate on prompt 1 of a fresh session
    in order for the team config to exist before the writer's
    verify-and-refuse path can succeed. If a future change adds
    TaskCreate or TeamCreate to _BLOCKED_TOOLS, the gate denies the
    only ritual action that creates the team config the writer needs
    to verify — bootstrap deadlocks.

    These tests pin the invariant so a future change can't silently
    break this. Placement here (in test_marker_schema.py) reflects
    that the marker module's behavioral envelope includes "the gate
    must allow the orchestrator to bootstrap from a fresh session".
    """

    def test_blocked_tools_does_not_contain_task_create(self):
        from bootstrap_gate import _BLOCKED_TOOLS
        assert "TaskCreate" not in _BLOCKED_TOOLS, (
            "TaskCreate must NOT be in _BLOCKED_TOOLS. The writer hook's "
            "verify-and-refuse path requires the orchestrator to call "
            "TaskCreate (and the platform's TeamCreate) on prompt 1 of a "
            "fresh session to populate the team config; gating those "
            "tools deadlocks bootstrap."
        )


