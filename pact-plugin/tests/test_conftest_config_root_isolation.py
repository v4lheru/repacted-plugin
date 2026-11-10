"""
Location: pact-plugin/tests/test_conftest_config_root_isolation.py
Summary: Positive regression pin for the autouse conftest fixture
         ``_isolate_config_root_to_tmp`` (PR #1189 / #1186). The fixture scrubs
         ``CLAUDE_CONFIG_DIR`` and redirects ``Path.home()`` -> ``tmp_path`` so
         every PACT state writer resolves under the per-test tmp, NEVER the
         operator's real ``~/.claude``. The real-writer suites
         (test_agent_handoff_marker, test_snapshot_marker_root_fallback, ...)
         prove the closure MECHANISM — but each SELF-PATCHES ``Path.home``, so
         they stay green even if the fixture were disabled: a future edit
         removing or breaking the fixture would reopen #1186 SILENTLY (suite
         green, destructive leak resumed). This file is the delete-the-fix
         counter-test that pins the FIXTURE's own closure.
Used by: pytest.
"""
import os


from shared.paths import get_claude_config_dir

# Read at IMPORT time — collection runs before any function-scoped autouse
# fixture, so this is the ambient value the scrub will later remove. Capturing
# it is what lets the session-id cell below tell "the scrub fired" apart from
# "there was nothing to scrub", which absence alone cannot distinguish.
_AMBIENT_SESSION_ID = os.environ.get("CLAUDE_CODE_SESSION_ID")

# Same import-time capture discipline for the CLAUDE_PROJECT_DIR scrub pin
# below: on a machine whose shell exports the var (a Claude Code hook process
# or a developer shell that sourced one) the ambient-removal cell
# discriminates; on CI it skips by the same logic as the session-id cell.
_AMBIENT_PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR")


class TestAutouseConfigRootIsolationPinned:
    """Pin the autouse ``_isolate_config_root_to_tmp`` fixture's closure.

    DELIBERATELY does NOT self-patch ``Path.home`` and does NOT set
    ``CLAUDE_CONFIG_DIR`` in-body: it relies SOLELY on the autouse fixture. That
    is what makes it a delete-the-fix counter-test for the fixture itself,
    rather than a redundant mechanism assertion (the closure mechanism is
    already covered by the real-writer suites, which self-patch).
    """

    def test_resolver_lands_under_tmp_not_real_home(self, tmp_path):
        """The SSOT resolver ``get_claude_config_dir()`` — every PACT state
        writer resolves through it — must land under the autouse fixture's
        ``tmp_path``, NOT the operator's real ``~/.claude``.

        Under the fixture: ``CLAUDE_CONFIG_DIR`` is scrubbed (unset) at setup
        and ``Path.home()`` -> ``tmp_path``, so the SSOT resolves to
        ``tmp_path / ".claude"`` (the HOME fallthrough — precedence-1 is empty).

        COUNTER-TEST (the pinning property this file exists for): if the
        fixture's ``monkeypatch.setattr(Path, "home", ...)`` is removed,
        ``Path.home()`` is the operator's real home -> ``get_claude_config_dir``
        resolves to real ``~/.claude`` -> the ``startswith(tmp_path)``
        assertion FAILS. If the scrub is removed AND an ambient
        ``CLAUDE_CONFIG_DIR`` is exported, precedence-1 resolves to that
        ambient value -> the assertion also FAILS. Verified by local
        fixture-disable (setattr line removed), reverted before staging.
        """
        resolved = get_claude_config_dir()

        # (1) Lands under the autouse fixture's tmp, NOT the real home.
        assert str(resolved).startswith(str(tmp_path)), (
            f"get_claude_config_dir() resolved to {resolved!r}, NOT under the "
            f"autouse fixture's tmp_path {str(tmp_path)!r}. The autouse "
            "_isolate_config_root_to_tmp closure has REGRESSED (Path.home "
            "setattr removed, or CLAUDE_CONFIG_DIR scrub removed with an "
            "ambient value leaking through) — #1186's destructive leak would "
            "resume silently under a green suite."
        )
        # (2) Specifically the .claude leaf (the HOME-fallthrough shape), not
        # some other tmp-rooted path — pins the exact resolution contract.
        assert resolved == tmp_path / ".claude", (
            f"expected tmp_path / '.claude', got {resolved!r}"
        )


