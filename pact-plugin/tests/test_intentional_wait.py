"""
Tests for shared.intentional_wait — validate_wait, wait_stale, canonical_since,
SELF_COMPLETE_EXEMPT_AGENT_TYPES, _is_exempt_agent_type, is_self_complete_exempt.

Coverage targets:
- validate_wait: non-dict inputs, missing/empty required keys, malformed since,
  tz-naive since (must reject), unknown keys (forward-compat), trailing Z vs +00:00,
  non-UTC offsets.
- wait_stale: fresh / stale / boundary / missing / malformed / future-dated,
  custom threshold, non-UTC offset age parity.
- canonical_since: shape and round-trip through validate_wait + wait_stale.
- SELF_COMPLETE_EXEMPT_AGENT_TYPES: shape, immutability, expected membership.
- _is_exempt_agent_type: positive match by team-config agentType lookup,
  fail-closed on every error path.
- is_self_complete_exempt: surface 1 (team-config agentType, requires
  team_name) and surface 2 (signal-task pattern, independent of team_name).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


# --- helpers ---------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _fresh_wait(**overrides):
    payload = {
        "reason": "awaiting_teachback_approved",
        "expected_resolver": "lead",
        "since": _iso(datetime.now(timezone.utc)),
    }
    payload.update(overrides)
    return payload


def _write_team_config(teams_dir, team_name, members):
    """Writes a minimal team config with the given members[] list and
    returns the teams_dir path as a string for passing to
    _is_exempt_agent_type / is_self_complete_exempt.
    """
    team_dir = Path(teams_dir) / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"team_name": team_name, "members": members}),
        encoding="utf-8",
    )
    return str(teams_dir)


@pytest.fixture
def teams_dir(tmp_path):
    """Per-test temp teams directory."""
    d = tmp_path / "teams"
    d.mkdir()
    return str(d)


# --- validate_wait ---------------------------------------------------------

class TestValidateWait:
    def test_fresh_payload_accepted(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(_fresh_wait()) is True

    def test_none_rejected(self):
        from shared.intentional_wait import validate_wait

        assert validate_wait(None) is False


# --- wait_stale ------------------------------------------------------------

class TestWaitStale:
    def test_fresh_is_not_stale(self):
        from shared.intentional_wait import wait_stale

        assert wait_stale(_fresh_wait()) is False

    def test_none_is_stale(self):
        from shared.intentional_wait import wait_stale

        assert wait_stale(None) is True


# --- canonical_since -------------------------------------------------------

class TestCanonicalSince:
    def test_returns_string(self):
        from shared.intentional_wait import canonical_since

        assert isinstance(canonical_since(), str)

    def test_is_tz_aware_iso(self):
        from shared.intentional_wait import canonical_since

        value = canonical_since()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None


# --- module constants ------------------------------------------------------

class TestModuleConstants:
    def test_default_threshold_is_30(self):
        from shared.intentional_wait import DEFAULT_THRESHOLD_MINUTES

        assert DEFAULT_THRESHOLD_MINUTES == 30

    def test_known_reasons_is_frozenset(self):
        from shared.intentional_wait import KNOWN_REASONS

        assert isinstance(KNOWN_REASONS, frozenset)
        assert "awaiting_teachback_approved" in KNOWN_REASONS


# --- prose-vs-code drift pin ----------------------------------------------

class TestSkillMdProseSnippetConformance:
    """Drift-pin: the SKILL.md prose snippet for `since` must stay in lockstep
    with code semantics. If prose says "use datetime.now(timezone.utc).isoformat(
    timespec='seconds')" but validate_wait later rejects that output (or
    wait_stale classifies it stale), teammates will follow the prose and hit
    silent nag-resume. Execute the prose snippet verbatim and assert.
    """

    def test_prose_snippet_output_is_fresh_and_valid(self):
        from shared.intentional_wait import validate_wait, wait_stale

        # Verbatim the SKILL.md "Intentional Waiting" prose snippet:
        since_value = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = {
            "reason": "awaiting_teachback_approved",
            "expected_resolver": "lead",
            "since": since_value,
        }
        assert validate_wait(payload) is True
        assert wait_stale(payload) is False


# --- self-complete exemption -----------------------------------------------

class TestKnownReasonsCompletionAddition:
    def test_awaiting_lead_completion_in_known_reasons(self):
        from shared.intentional_wait import KNOWN_REASONS

        assert "awaiting_lead_completion" in KNOWN_REASONS

    def test_awaiting_lead_completion_passes_validate_wait(self):
        from shared.intentional_wait import validate_wait

        payload = _fresh_wait(reason="awaiting_lead_completion")
        assert validate_wait(payload) is True


class TestSelfCompleteExemptAgentTypesConstant:
    def test_is_frozenset(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENT_TYPES

        assert isinstance(SELF_COMPLETE_EXEMPT_AGENT_TYPES, frozenset)

    def test_contains_pact_secretary(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENT_TYPES

        # Single canonical agentType — `secretary` was a name-alias and is
        # no longer canonical; the carve-out keys on team-config agentType.
        assert "pact-secretary" in SELF_COMPLETE_EXEMPT_AGENT_TYPES


class TestIsExemptAgentType:
    """Direct unit tests on _is_exempt_agent_type — the shared helper that
    backs is_self_complete_exempt.
    """

    def test_owner_with_pact_secretary_agenttype_is_exempt(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        assert _is_exempt_agent_type("session-secretary", "test-team", teams_dir) is True

    def test_owner_with_arbitrary_spawn_name_is_exempt(self, teams_dir):
        # Spawn-name freedom: any name reaches the carve-out as long as
        # the team config records its agentType. This is the central
        # behavioral change of #682.
        from shared.intentional_wait import _is_exempt_agent_type

        _write_team_config(teams_dir, "test-team", [
            {"name": "secretary-from-mars", "agentType": "pact-secretary"},
        ])
        assert _is_exempt_agent_type("secretary-from-mars", "test-team", teams_dir) is True


class TestIsSelfCompleteExempt:
    def test_owner_with_pact_secretary_agenttype_is_exempt(self, teams_dir):
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        task = {"owner": "session-secretary", "metadata": {}}
        assert is_self_complete_exempt(task, "test-team", teams_dir) is True

    def test_secretary_spawn_name_freedom(self, teams_dir):
        # The behavioral change of #682: spawn name no longer determines
        # exemption — agentType in team config does.
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "team-secretary", "agentType": "pact-secretary"},
        ])
        task = {"owner": "team-secretary", "metadata": {}}
        assert is_self_complete_exempt(task, "test-team", teams_dir) is True


class TestIsSelfCompleteExemptMalformedTaskShapes:
    """Edge cases for malformed task dicts — defensive defaults must hold."""

    def test_no_metadata_key_returns_false(self):
        from shared.intentional_wait import is_self_complete_exempt

        # `metadata` key absent entirely. owner is non-exempt.
        assert is_self_complete_exempt({"owner": "backend-coder"}) is False

    def test_metadata_explicit_none_returns_false(self):
        from shared.intentional_wait import is_self_complete_exempt

        # metadata=None coalesces to {} via `metadata = task.get("metadata") or {}`.
        # Owner is non-exempt → returns False.
        assert is_self_complete_exempt({"owner": "backend-coder", "metadata": None}) is False


class TestIsSelfCompleteExemptDualCarveOutIndependence:
    """Both exemption surfaces must work independently AND together.

    Surface 1: SELF_COMPLETE_EXEMPT_AGENT_TYPES membership via team-config
               agentType lookup (requires team_name).
    Surface 2: signal-task pattern (completion_type=signal + type in
               {blocker, algedonic}). Independent of team_name.

    Reverting EITHER surface in production must surface as independent test failures.
    """

    def test_only_signal_task_path_no_exempt_agenttype(self, teams_dir):
        # Auditor signal-task: agentType NOT exempt, but signal pattern exempts.
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "pact-auditor", "agentType": "pact-auditor"},
        ])
        task = {
            "owner": "pact-auditor",
            "metadata": {"completion_type": "signal", "type": "algedonic"},
        }
        assert is_self_complete_exempt(task, "test-team", teams_dir) is True

    def test_only_exempt_agenttype_path_no_signal_task(self, teams_dir):
        # Secretary memory-save: agentType in exempt set, no signal-task metadata.
        from shared.intentional_wait import is_self_complete_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
        ])
        task = {"owner": "session-secretary", "metadata": {"completion_type": "regular"}}
        assert is_self_complete_exempt(task, "test-team", teams_dir) is True


class TestSelfCompleteExemptAgentTypesImmutability:
    """frozenset chosen specifically to prevent accidental mutation; pin that."""

    def test_add_raises_attribute_error(self):
        from shared.intentional_wait import SELF_COMPLETE_EXEMPT_AGENT_TYPES

        with pytest.raises(AttributeError):
            SELF_COMPLETE_EXEMPT_AGENT_TYPES.add("new-agent-type")


class TestTeachbackExemptAgentTypesConstant:
    """Second agentType-keyed carve-out frozenset, parallel structure to
    SELF_COMPLETE_EXEMPT_AGENT_TYPES. Membership:
    {pact-secretary}. Surface: teachback-gated dispatch (Task A skipped).
    """

    def test_is_frozenset(self):
        from shared.intentional_wait import TEACHBACK_EXEMPT_AGENT_TYPES

        assert isinstance(TEACHBACK_EXEMPT_AGENT_TYPES, frozenset)


class TestTeachbackExemptAgentTypesImmutability:
    """frozenset chosen specifically to prevent accidental mutation; pin that."""

    def test_add_raises_attribute_error(self):
        from shared.intentional_wait import TEACHBACK_EXEMPT_AGENT_TYPES

        with pytest.raises(AttributeError):
            TEACHBACK_EXEMPT_AGENT_TYPES.add("new-agent-type")


class TestTwoFrozensetsAreSeparateObjects:
    """Phantom-green guard: the module docstring forbids aliasing the two
    agentType-keyed carve-out frozensets (`SELF_COMPLETE_EXEMPT_AGENT_TYPES`,
    `TEACHBACK_EXEMPT_AGENT_TYPES`) because each governs an independent
    operational surface (self-completion, dispatch). Same-contents aliasing
    — e.g. `TEACHBACK_EXEMPT_AGENT_TYPES = SELF_COMPLETE_EXEMPT_AGENT_TYPES`
    — is a silent-recouple defect: membership and immutability tests stay
    GREEN because the alias preserves contents; the docstring prohibition
    has no CI teeth. This test pins the two as separate objects via `is not`
    identity checks so a future aliasing refactor fails loudly.

    When divergence lands (one frozenset gains a member the other doesn't),
    contents-based tests will catch it. But during the equal-contents
    window — which is the current state — only identity checks discriminate.
    """

    def test_two_frozensets_are_separate_objects(self):
        from shared.intentional_wait import (
            SELF_COMPLETE_EXEMPT_AGENT_TYPES,
            TEACHBACK_EXEMPT_AGENT_TYPES,
        )

        assert TEACHBACK_EXEMPT_AGENT_TYPES is not SELF_COMPLETE_EXEMPT_AGENT_TYPES


class TestIsTeachbackExempt:
    """Direct unit tests on is_teachback_exempt — the public predicate that
    backs task_lifecycle_gate.work_addblockedby_missing carve-out. Parallel
    structure to TestIsExemptAgentType; fail-closed on every error path.
    """

    def test_secretary_owner_is_exempt(self, teams_dir):
        from shared.intentional_wait import is_teachback_exempt

        _write_team_config(teams_dir, "test-team", [
            {"name": "secretary", "agentType": "pact-secretary"},
        ])
        assert is_teachback_exempt("secretary", "test-team", teams_dir) is True


class TestKnownReasonsLiteralRegressionGuard:
    """Pin the exact set of known reasons. Any silent removal/rename must fail loudly.

    This is a documentation-in-code test: the contract published to teammates
    via the pact-agent-teams skill names these strings. Silent renaming would
    break in-flight teammate metadata writes without surfacing a build error.
    """

    EXPECTED_REASONS = {
        "awaiting_teachback_approved",
        "awaiting_lead_commit",
        "awaiting_amendment_review",
        "awaiting_post_handoff_decision",
        "awaiting_peer_response",
        "awaiting_user_decision",
        "awaiting_blocker_resolution",
        "awaiting_lead_completion",
        "awaiting_lead_takeover",
    }

    def test_exact_set(self):
        from shared.intentional_wait import KNOWN_REASONS

        assert set(KNOWN_REASONS) == self.EXPECTED_REASONS


class TestIsExemptAgentTypeDefaultTeamsDir:
    """teams_dir=None default-path coverage: when the override is omitted,
    `_iter_members` must resolve via Path.home()/.claude/teams/. The 12-case
    TestIsExemptAgentType class above passes teams_dir explicitly; this
    class exercises the production default path so a future regression that
    bypasses `_iter_members` (e.g. inlines its own path) fails loudly.
    """

    def test_default_teams_dir_resolves_via_path_home(self, tmp_path, monkeypatch):
        from shared.intentional_wait import _is_exempt_agent_type

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team_dir = tmp_path / ".claude" / "teams" / "default-path-team"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"team_name": "default-path-team", "members": [
                {"name": "session-secretary", "agentType": "pact-secretary"},
            ]}),
            encoding="utf-8",
        )
        # teams_dir omitted → exercises _iter_members default branch.
        assert _is_exempt_agent_type(
            "session-secretary", "default-path-team"
        ) is True


class TestIsTeachbackExemptDefaultTeamsDir:
    """teams_dir=None default-path coverage for is_teachback_exempt: when
    the override is omitted, the underlying `_iter_members` must resolve
    via Path.home()/.claude/teams/. TestIsTeachbackExempt above passes
    teams_dir explicitly; this class exercises the production default path
    so a future regression that bypasses `_iter_members` (e.g. inlines its
    own path) fails loudly.

    Mirror of TestIsExemptAgentTypeDefaultTeamsDir for the self-complete
    surface — same shape, different predicate.
    """

    def test_default_teams_dir_resolves_via_path_home(self, tmp_path, monkeypatch):
        from shared.intentional_wait import is_teachback_exempt

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        team_dir = tmp_path / ".claude" / "teams" / "default-path-team"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"team_name": "default-path-team", "members": [
                {"name": "session-secretary", "agentType": "pact-secretary"},
            ]}),
            encoding="utf-8",
        )
        # teams_dir omitted → exercises _iter_members default branch.
        assert is_teachback_exempt(
            "session-secretary", "default-path-team"
        ) is True


class TestIsExemptAgentTypeMixedTeamConfig:
    """Multi-member team config: the predicate must match BY name AND verify
    THAT member's agentType. A team with both secretary and non-secretary
    members must not exempt the non-secretary owner just because the team
    config records an exempt agentType somewhere.
    """

    def test_only_matching_member_drives_exemption(self, teams_dir):
        from shared.intentional_wait import _is_exempt_agent_type

        _write_team_config(teams_dir, "mixed-team", [
            {"name": "session-secretary", "agentType": "pact-secretary"},
            {"name": "backend-coder-1", "agentType": "pact-backend-coder"},
            {"name": "test-engineer-1", "agentType": "pact-test-engineer"},
        ])
        assert _is_exempt_agent_type(
            "session-secretary", "mixed-team", teams_dir
        ) is True
        assert _is_exempt_agent_type(
            "backend-coder-1", "mixed-team", teams_dir
        ) is False
        assert _is_exempt_agent_type(
            "test-engineer-1", "mixed-team", teams_dir
        ) is False


