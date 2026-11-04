"""Unit tests for the self-registration registry (hooks/shared/session_registry.py).

Commit-A coverage: register/resolve round-trip, self-lookup-only, members[]-
validation (integrity), fail-safe everywhere, $CLAUDE_CODE_SESSION_ID-absent
no-op, the <=512B single-write bound, last-wins-per-session_id, the global
fixed team-agnostic path, sanitize-on-write-and-read, and the symlink/path
guards. (The both-modes matrix, AST no-import, and sanitizer-parity structural
tests live in commit G's test files; this file is the module's own unit suite.)
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared import session_registry
from shared.session_registry import register, resolve


@pytest.fixture
def registry_env(tmp_path, monkeypatch):
    """Redirect REGISTRY_PATH + Path.home() into tmp_path so register/resolve
    operate on an isolated fake ~/.claude tree, and patch the
    $CLAUDE_CODE_SESSION_ID env var. Returns a small helper namespace.

    The path-containment check resolves against ``Path.home()/.claude/
    pact-sessions``; we point Path.home() at tmp_path so the fixed registry
    path passes the containment guard inside the sandbox.
    """
    fake_home = tmp_path
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    reg_path = fake_home / ".claude" / "pact-sessions" / ".teammate-registry.jsonl"
    monkeypatch.setattr(session_registry, "get_registry_path", lambda: reg_path)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    class _Env:
        home = fake_home
        registry_path = reg_path

        @staticmethod
        def set_session(sid):
            monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", sid)

        @staticmethod
        def write_team(team, member_names, lead_session_id=None):
            """Write teams/<team>/config.json with a members[] list, and
            OPTIONALLY a top-level leadSessionId (the field register()'s
            in-process self-guard reads). Default lead_session_id=None omits the
            key — byte-identical to the pre-guard config shape, so every existing
            caller is unchanged AND its register() write fails-OPEN through the
            guard (no leadSessionId -> _read_lead_session_id == "" -> never an
            in-process match -> WRITE), preserving prior behavior exactly."""
            team_dir = fake_home / ".claude" / "teams" / team
            team_dir.mkdir(parents=True, exist_ok=True)
            config = {"members": [{"name": n} for n in member_names]}
            if lead_session_id is not None:
                config["leadSessionId"] = lead_session_id
            (team_dir / "config.json").write_text(
                json.dumps(config),
                encoding="utf-8",
            )

    return _Env


# ---------------------------------------------------------------------------
# register/resolve round-trip + self-lookup
# ---------------------------------------------------------------------------

def test_register_then_resolve_roundtrip(registry_env):
    registry_env.set_session("sess-abc")
    registry_env.write_team("pact-team1", ["alice", "bob"])

    register("alice@pact-team1")
    assert resolve("sess-abc") == "alice@pact-team1"


def test_resolve_self_lookup_only_other_session_misses(registry_env):
    """resolve keys on the passed session_id; another agent's session_id does
    NOT return this agent's value (self-lookup-only — no cross-team forge)."""
    registry_env.set_session("sess-alice")
    registry_env.write_team("pact-team1", ["alice"])
    register("alice@pact-team1")

    assert resolve("sess-alice") == "alice@pact-team1"
    assert resolve("sess-someone-else") is None


def test_no_name_keyed_lookup_api_exists(registry_env):
    """Structural: the module exposes resolve(session_id) and register only —
    NO name-keyed lookup function (a name-keyed scan would enable forging)."""
    public = {n for n in dir(session_registry) if not n.startswith("_")}
    # Allowed public surface: the two operations + the path/bound constants.
    assert "resolve" in public
    assert "register" in public
    forbidden = {"resolve_by_name", "lookup_name", "find_by_name", "scan"}
    assert not (forbidden & public), f"name-keyed lookup leaked: {forbidden & public}"


# ---------------------------------------------------------------------------
# last-wins-per-session_id
# ---------------------------------------------------------------------------

def test_last_wins_per_session_id(registry_env):
    registry_env.set_session("sess-1")
    registry_env.write_team("pact-team1", ["alice"])
    registry_env.write_team("pact-team2", ["alice"])

    register("alice@pact-team1")
    register("alice@pact-team2")  # later write wins on read
    assert resolve("sess-1") == "alice@pact-team2"


# ---------------------------------------------------------------------------
# members[]-validation (integrity)
# ---------------------------------------------------------------------------

def test_resolve_rejects_name_not_in_team_members(registry_env):
    """A self-supplied name that is NOT a member of its @team config is
    rejected on read (returns None) — the forge-blunting integrity check."""
    registry_env.set_session("sess-x")
    registry_env.write_team("pact-team1", ["alice", "bob"])

    register("mallory@pact-team1")  # mallory not a member
    assert resolve("sess-x") is None


def test_resolve_rejects_when_team_config_missing(registry_env):
    registry_env.set_session("sess-y")
    # no team config written for pact-ghost
    register("alice@pact-ghost")
    assert resolve("sess-y") is None


def test_resolve_accepts_member_after_sanitization(registry_env):
    """members[]-validation compares SANITIZED names on both sides, so a member
    stored with a sanitizable char still validates."""
    registry_env.set_session("sess-z")
    registry_env.write_team("pact-team1", ["weird)name"])  # ) sanitizes to _

    register("weird)name@pact-team1")
    assert resolve("sess-z") == "weird_name@pact-team1"


# ---------------------------------------------------------------------------
# sanitize on write and read
# ---------------------------------------------------------------------------

def test_name_sanitized_on_write(registry_env):
    registry_env.set_session("sess-s")
    registry_env.write_team("pact-team1", ["a_b"])  # ) and newline strip to _

    register("a)b@pact-team1")
    # the stored + resolved value is sanitized
    assert resolve("sess-s") == "a_b@pact-team1"


# ---------------------------------------------------------------------------
# $CLAUDE_CODE_SESSION_ID absent → no-op
# ---------------------------------------------------------------------------

def test_register_noop_when_session_id_absent(registry_env):
    """No $CLAUDE_CODE_SESSION_ID → register is a no-op, writes nothing, never
    raises."""
    # registry_env deletes the env var by default
    register("alice@pact-team1")
    assert not registry_env.registry_path.exists()


def test_register_noop_when_value_has_no_at(registry_env):
    registry_env.set_session("sess-1")
    register("alice-no-team")  # no @ → nothing resolvable → no-op
    assert not registry_env.registry_path.exists()


# ---------------------------------------------------------------------------
# <=512B single-write bound
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# fail-safe: corrupt / missing → resolve returns None, never raises
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# global fixed team-agnostic path
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# C4b — inline config-root resolver parity (session_registry is a standalone-
# script leaf that can't import shared.paths, so it inlines the resolver). These
# parity tests enforce the inline copy never drifts from the canonical resolver,
# and guard the silent-bail trap. Mirrors the _is_safe_team_segment dual-copy
# parity precedent (test_session_registry_trust_partition.py).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CLI teach examples (main block via subprocess)
# ---------------------------------------------------------------------------

_SR_SCRIPT = str(
    Path(__file__).parent.parent / "hooks" / "shared" / "session_registry.py"
)


