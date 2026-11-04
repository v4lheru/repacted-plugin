"""Integrity + fail-safe edge-DEPTH tests for the self-registration registry
(hooks/shared/session_registry.py) — #885.

The registry value is SELF-ASSERTED by the teammate and therefore FORGEABLE; the
registry is LABELING-ONLY. These tests harden the boundary against the inputs an
adversary or a corrupt disk would actually produce, beyond the happy-path +
basic-fail-safe rows in test_session_registry.py:

  * FORGED CROSS-TEAM: a value whose name is a real member of SOME team but NOT of
    the @team it claims -> members[]-validation rejects on read.
  * REAL O_NOFOLLOW SYMLINK DEFEAT (by construction, not via the _is_under helper):
    a planted symlink AT the registry path -> register is a no-op AND resolve
    returns None, both via the O_NOFOLLOW open (ELOOP), never following the link.
  * SANITIZE-ON-READ: a value written RAW (bypassing register's write-side
    sanitizer) with an embedded control char -> resolve neutralizes it on read.
  * MALFORMED TEAM CONFIG: members null / non-list / non-JSON / list-of-junk ->
    _name_is_team_member returns False, resolve returns None, never raises.
  * NON-@ + corrupt stored values on the READ path.

EVERY assertion is fail-safe BY CONSTRUCTION: the function under test must RETURN
None / no-op for the bad input. None of these tests wrap the call in try/except to
"prove no raise" — if a corrupt input raises, that is a fail-safe BREACH and the
test must FAIL loudly (a try/except would paper over the very defect being tested).

Also exercises the production register CLI (``python session_registry.py register
--name ...``) end-to-end as a real subprocess — the exact invocation the agent-def
first-action imperative runs, which no in-process test otherwise covers.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared import session_registry
from shared.session_registry import register, resolve

_REGISTRY_MODULE = Path(session_registry.__file__)


@pytest.fixture
def registry_env(tmp_path, monkeypatch):
    """Isolated ~/.claude tree (mirrors test_session_registry.py's fixture)."""
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
            d = fake_home / ".claude" / "teams" / team
            d.mkdir(parents=True, exist_ok=True)
            config = {"members": [{"name": n} for n in member_names]}
            if lead_session_id is not None:
                # The field register()'s in-process self-guard reads; omitted by
                # default so existing callers stay byte-identical (and fail-OPEN).
                config["leadSessionId"] = lead_session_id
            (d / "config.json").write_text(
                json.dumps(config),
                encoding="utf-8",
            )

        @staticmethod
        def write_team_raw(team, config_obj):
            """Write a team config that is NOT the canonical members-list shape
            (for the malformed-config rejections)."""
            d = fake_home / ".claude" / "teams" / team
            d.mkdir(parents=True, exist_ok=True)
            (d / "config.json").write_text(
                config_obj if isinstance(config_obj, str) else json.dumps(config_obj),
                encoding="utf-8",
            )

        @staticmethod
        def write_raw_line(session_id, value):
            """Append a registry line DIRECTLY (bypassing register's write-side
            sanitizer/guards) so the READ path can be tested against a value the
            writer would never have produced — e.g. an embedded control char."""
            reg_path.parent.mkdir(parents=True, exist_ok=True)
            with open(reg_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"session_id": session_id, "value": value}) + "\n")

    return _Env


# ===========================================================================
# FORGED CROSS-TEAM — name valid somewhere, NOT in the @team it claims
# ===========================================================================

class TestForgedCrossTeam:
    def test_name_member_of_other_team_but_not_claimed_team_rejected(self, registry_env):
        """alice is a real member of pact-real, but the forged value claims
        pact-victim (where alice is NOT a member) -> rejected on read. The
        members[]-validation keys on the value's OWN claimed @team, so being a
        member of a DIFFERENT team does not grant entry."""
        registry_env.set_session("sess-forge")
        registry_env.write_team("pact-real", ["alice"])
        registry_env.write_team("pact-victim", ["bob"])  # alice NOT here
        register("alice@pact-victim")  # forged: claims a team alice isn't in
        assert resolve("sess-forge") is None


# ===========================================================================
# REAL O_NOFOLLOW SYMLINK DEFEAT — by construction (the actual open, not _is_under)
# ===========================================================================

class TestSymlinkDefeatByConstruction:
    def test_register_does_not_follow_symlink_at_registry_path(self, registry_env, tmp_path):
        """A symlink planted AT the registry path -> register is a no-op via
        O_NOFOLLOW (the open raises ELOOP, swallowed) and the link TARGET is never
        written through. fail-safe by construction: register returns, never
        raises."""
        target = tmp_path / "attacker_target.jsonl"
        target.write_text("", encoding="utf-8")
        registry_env.registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_env.registry_path.symlink_to(target)

        registry_env.set_session("sess-sym")
        registry_env.write_team("pact-team1", ["alice"])
        register("alice@pact-team1")  # must not raise, must not write the target

        # The attacker-controlled target was NOT written through the link.
        assert target.read_text(encoding="utf-8") == ""


# ===========================================================================
# SANITIZE-ON-READ — a raw value with an embedded control char is neutralized
# ===========================================================================

class TestSanitizeOnRead:
    def test_control_char_in_stored_value_neutralized_on_read(self, registry_env):
        """A value written RAW with an embedded newline-class control char (e.g.
        U+0085 NEL, which str.splitlines() and tokenizers treat as a line break)
        is sanitized on READ -> the control char is replaced before the resolved
        name reaches a caller / a role marker. Proves read-side sanitization is
        not skipped just because the writer would normally have sanitized."""
        registry_env.set_session("sess-ctl")
        registry_env.write_team("pact-team1", ["a_b"])  # the sanitized form is a member
        registry_env.write_raw_line("sess-ctl", "a\x85b@pact-team1")  # NEL injected
        resolved = resolve("sess-ctl")
        assert resolved == "a_b@pact-team1"
        # No raw control char survives into the resolved value.
        assert "\x85" not in resolved


# ===========================================================================
# MALFORMED TEAM CONFIG — members null / non-list / junk -> reject, never raise
# ===========================================================================

class TestMalformedTeamConfig:
    @pytest.mark.parametrize("config_obj", [
        {"members": None},])
    def test_malformed_config_rejects_resolve_never_raises(self, registry_env, config_obj):
        """Every malformed team-config shape -> resolve returns None (the name
        cannot be validated as a member) and NEVER raises. fail-safe by
        construction: no try/except here — a raise fails the test."""
        registry_env.set_session("sess-malformed")
        registry_env.write_team_raw("pact-malformed", config_obj)
        register("alice@pact-malformed")
        assert resolve("sess-malformed") is None


# ===========================================================================
# CORRUPT READ-PATH VALUES — non-@, non-str, non-dict lines on resolve
# ===========================================================================

class TestCorruptReadPath:
    def test_stored_value_without_at_returns_none(self, registry_env):
        registry_env.write_team("pact-team1", ["alice"])
        registry_env.write_raw_line("sess-noat", "alice-no-team")  # no @ in value
        assert resolve("sess-noat") is None


# ===========================================================================
# REGISTER CLI ROUND-TRIP — the production __main__ invocation as a subprocess
# ===========================================================================

class TestRegisterCliRoundTrip:
    """The agent-def first-action imperative runs
    ``python3 <plugin_root>/hooks/shared/session_registry.py register --name 'n@t'``
    in SCRIPT MODE by direct path. No in-process test exercises that __main__
    path; this runs the REAL module as a subprocess from an UNRELATED cwd (proving
    the self-containment claim: zero shared.* deps, works from any cwd) and then
    resolves the result IN-PROCESS to confirm the round-trip."""

    def _run_cli(self, name_at_team, *, home, session_id, cwd):
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["CLAUDE_CODE_SESSION_ID"] = session_id
        return subprocess.run(
            [sys.executable, str(_REGISTRY_MODULE), "register", "--name", name_at_team],
            capture_output=True, text=True, env=env, cwd=str(cwd), timeout=30,
        )

    def test_cli_register_then_in_process_resolve(self, registry_env, tmp_path):
        """register via the real CLI subprocess (from a foreign cwd) -> the line
        lands -> in-process resolve recovers it. The fixture's REGISTRY_PATH +
        Path.home point into tmp_path, and the CLI inherits HOME, so both sides
        agree on the file location."""
        # The CLI child uses Path.home()/.claude/... so set HOME to the fixture home.
        registry_env.write_team("pact-cli", ["devops"])
        foreign_cwd = tmp_path / "some" / "unrelated" / "place"
        foreign_cwd.mkdir(parents=True)

        proc = self._run_cli(
            "devops@pact-cli", home=registry_env.home,
            session_id="sess-cli", cwd=foreign_cwd,
        )
        assert proc.returncode == 0, (
            f"register CLI exited {proc.returncode}; stderr=\n{proc.stderr}"
        )
        # In-process resolve (fixture REGISTRY_PATH == HOME/.claude/...) recovers it.
        assert resolve("sess-cli") == "devops@pact-cli"


    # --- in-process self-guard, exercised through the REAL script-mode CLI ----
    # The guard calls the new ``_read_lead_session_id`` helper. In-process tests
    # can't catch a script-mode-only regression in that helper (e.g. a stray
    # ``shared.*`` import that would ImportError as a subprocess but resolve fine
    # in-process). These two run the actual ``python session_registry.py register``
    # subprocess under both topologies, so the guard is proven from the same entry
    # point production uses.


