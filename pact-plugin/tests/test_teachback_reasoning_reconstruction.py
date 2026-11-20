"""Tests for the optional reasoning_reconstruction field on metadata.teachback_submit.

Pins the schema and variety-band gate for the L1.5 method-level
extension. Coverage:

  - Schema: optional parent key (absent / null both valid)
  - Schema: when present, must be dict with exactly 3 string keys
  - Schema: each sub-key must be non-empty string
  - Variety trigger: route_workflow(score) -> required / recommended / skipped
  - Variety trigger: constants imported from variety_scorer.py (NOT literals)
  - Variety None-default: treated as ROUTE_ORCHESTRATE band (Recommended)
  - Exemption: TEACHBACK_EXEMPT_AGENT_TYPES bypass also bypasses reasoning_reconstruction
  - Backward-compat: 4-field shape (without reasoning_reconstruction) validates
  - Internal consistency: lead-side check accepts well-formed triangle;
    rejects partial / empty.

The schema sub-key validator now lives in shared/teachback_schema.py
(SSOT). This module's `_validate_reasoning_reconstruction` wraps the
shared validator with the lead-side band-routing logic that combines
sub-key validation with variety-band-driven missing-reconstruction
rejection. TEACHBACK_REQUIRED_SUBKEYS is imported from shared (canonical
3-tuple).
NOTE: the AST-scan in TestVarietyTrigger.test_constants_imported_not_literal
walks Path(__file__) only — if band thresholds (6 / 10 / 14) are migrated
out of variety_scorer.py into another module in a future PR, the scan
path must be extended to cover that module.
"""

from __future__ import annotations

import json

from shared.intentional_wait import TEACHBACK_EXEMPT_AGENT_TYPES
from shared.teachback_schema import (
    TEACHBACK_REQUIRED_SUBKEYS,
    validate_reasoning_reconstruction as _validate_subkeys,
)
from shared.variety_scorer import (
    COMPACT_MAX,
    ORCHESTRATE_MAX,
    PLAN_MODE_MAX,
    ROUTE_COMPACT,
    ROUTE_ORCHESTRATE,
    ROUTE_PLAN_MODE,
    ROUTE_RESEARCH_SPIKE,
    route_workflow,
)


# ============================================================================
# Schema validator under test
# ============================================================================


def _validate_reasoning_reconstruction(
    teachback_submit: dict, dispatching_variety_score: int | None
) -> tuple[str, str | None]:
    """Lead-side schema gate for reasoning_reconstruction.

    Returns ("accept", None) on pass, or ("reject", reason) on fail.
    Combines band-routing (missing-at-required-band rejection) with the
    pure sub-key validator from shared/teachback_schema.py.
    """
    reconstruction = teachback_submit.get("reasoning_reconstruction")
    band = _band_for_variety(dispatching_variety_score)

    if reconstruction is None:
        if band in (ROUTE_PLAN_MODE, ROUTE_RESEARCH_SPIKE):
            return "reject", "missing_reasoning_reconstruction"
        return "accept", None

    subkey_problem = _validate_subkeys(reconstruction)
    if subkey_problem:
        return "reject", subkey_problem
    return "accept", None


def _band_for_variety(score: int | None) -> str:
    """Route a variety score to its workflow band, treating None / non-int
    as the Recommended (ROUTE_ORCHESTRATE) band per architect §2.3.
    """
    if not isinstance(score, int):
        return ROUTE_ORCHESTRATE
    return route_workflow(score)


# ============================================================================
# Fixtures
# ============================================================================


def _payload(reconstruction: dict | None = None) -> dict:
    """Build a 4-field teachback payload; optionally include a 5th
    reasoning_reconstruction sub-object. Used across tests so the four
    canonical fields stay byte-equal across test cases.
    """
    payload: dict = {
        "understanding": "build the X with constraint Y",
        "most_likely_wrong": "interface Z naming",
        "least_confident_item": "Z must accept either A or B",
        "first_action": "read docs/architecture/X.md in full",
    }
    if reconstruction is not None:
        payload["reasoning_reconstruction"] = reconstruction
    return payload


def _well_formed_triangle() -> dict:
    return {
        "decision_attribution": (
            "I understand the architect chose nested 5th field "
            "because it preserves the whitelist invariant"
        ),
        "assumption_trace": (
            "This depends on (a) teachback_submit being whitelisted, "
            "and (b) variety_scorer constants being stable"
        ),
        "contingency_clause": (
            "If teachback_submit is NOT whitelisted, scope expands to "
            "include the wake-lifecycle whitelist edit"
        ),
    }


# ============================================================================
# Test classes (architect §4.2 catalog)
# ============================================================================


class TestSchemaShape:
    """Optional parent key + 3-key triangle schema gate."""

    def test_field_absent_is_valid(self):
        verdict, reason = _validate_reasoning_reconstruction(_payload(), dispatching_variety_score=5)
        assert verdict == "accept"
        assert reason is None

    def test_field_null_is_valid(self):
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(reconstruction=None), dispatching_variety_score=5
        )
        assert verdict == "accept"
        assert reason is None


class TestVarietyTrigger:
    """Variety-band gate: required / recommended / skipped per band."""

    def test_compact_band_skipped(self):
        # variety in COMPACT band — absence is accepted
        score = COMPACT_MAX  # boundary: last score still in the COMPACT band
        assert route_workflow(score) == ROUTE_COMPACT
        verdict, _ = _validate_reasoning_reconstruction(
            _payload(), dispatching_variety_score=score
        )
        assert verdict == "accept"

    def test_orchestrate_band_recommended(self):
        # variety in ORCHESTRATE band — absence is accepted (recommended-not-required)
        score = ORCHESTRATE_MAX
        assert route_workflow(score) == ROUTE_ORCHESTRATE
        verdict, _ = _validate_reasoning_reconstruction(
            _payload(), dispatching_variety_score=score
        )
        assert verdict == "accept"


class TestExemption:
    """TEACHBACK_EXEMPT_AGENT_TYPES bypass covers method reconstruction too.

    Architect §2.4: the existing exemption (currently `{pact-secretary}`)
    covers method reconstruction transparently — no new carve-out logic
    is introduced. These tests pin the exemption set membership; the
    predicate `is_teachback_exempt` itself is exercised by
    test_intentional_wait.py against the full team-config lookup surface.
    """

    def test_pact_secretary_is_exempt_agent_type(self):
        assert "pact-secretary" in TEACHBACK_EXEMPT_AGENT_TYPES

    def test_non_exempt_agent_types_excluded(self):
        # Sanity counterpart: typical PACT specialist agentTypes are NOT
        # in the exemption set; they remain subject to the teachback gate
        # (including the L1.5 sub-field at variety ≥ 11).
        for agent_type in ("backend-coder", "pact-devops-engineer", "pact-orchestrator"):
            assert agent_type not in TEACHBACK_EXEMPT_AGENT_TYPES


class TestBackwardCompat:
    """4-field shape (no reasoning_reconstruction) continues to validate."""

    def test_4_field_payload_validates_without_reconstruction(self):
        # No 5th field; any variety band at or below ORCHESTRATE accepts.
        for score in (COMPACT_MAX, ORCHESTRATE_MAX, None):
            verdict, _ = _validate_reasoning_reconstruction(
                _payload(), dispatching_variety_score=score
            )
            assert verdict == "accept", f"4-field payload rejected at variety {score}"


class TestLeadSideConsistencyCheck:
    """Schema-gate happy paths + targeted reject cases.

    Architect §3.2 (a)(b)(c) internal-consistency prompts are
    judgment-only — NOT exercised by automated tests in this PR; per
    §7.4 and the coder-discretion lean accepted during teachback gate review.
    Future PostToolUse hook escalation MAY add semantic probes.
    """

    def test_well_formed_triangle_accepted_at_required_band(self):
        verdict, reason = _validate_reasoning_reconstruction(
            _payload(reconstruction=_well_formed_triangle()),
            dispatching_variety_score=PLAN_MODE_MAX,
        )
        assert verdict == "accept"
        assert reason is None


# ============================================================================
# JSON round-trip smoke (the payload is real metadata that lands on disk
# via TaskUpdate(metadata=...) — confirm the well-formed triangle survives
# a JSON serialize/deserialize without shape loss).
# ============================================================================


def test_payload_json_round_trip_preserves_triangle():
    payload = _payload(reconstruction=_well_formed_triangle())
    serialized = json.dumps({"teachback_submit": payload})
    restored = json.loads(serialized)["teachback_submit"]
    assert set(restored["reasoning_reconstruction"].keys()) == set(TEACHBACK_REQUIRED_SUBKEYS)
    verdict, _ = _validate_reasoning_reconstruction(
        restored, dispatching_variety_score=PLAN_MODE_MAX
    )
    assert verdict == "accept"
