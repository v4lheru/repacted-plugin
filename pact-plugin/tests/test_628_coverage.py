"""
Coverage gap-fills for #628 restore-startup-ritual TEST phase.

Pins the plan §Test Phase scenarios that backend's TDD coverage did not
land tests for, plus the auditor YELLOW signal resolutions:

Read-Filesystem-Only-Raw-Read-Metadata: /repacted:bootstrap command structural pins
       (frontmatter, ritual-only-content, plugin.json registration via
       EXPECTED_COMMANDS update is in test_commands_structure.py)
Race-Window-Skip: bootstrap marker clear-on-clear-source AND not-clear-on-resume-source
Emit-Nothing-If-Empty: orchestrator persona references Skill("repacted:bootstrap")
G6: orchestrator persona includes pin-memory mid-session directive (F2)
G7: Lead-Side HALT Fan-Out byte-equal at two sites

Y1 (auditor YELLOW-1): _TEACHBACK_REMINDER cross-file consistency with
                       skills/pact-teachback/SKILL.md — both reference
                       metadata.teachback_submit, drift on either side
                       fails the test.
Y2 (auditor YELLOW-2): TestMarkerNameConsistency parametrized variant —
                       accepts encoding alternatives (f-string-like,
                       assignment style, multi-line with comments) for
                       the marker-write invocation in commands/bootstrap.md;
                       tests cross-file consistency of marker SEMANTICS
                       rather than byte-equal syntax.
"""

import re
from pathlib import Path

import pytest

from shared.constants import BOOTSTRAP_MARKER_NAME

PLUGIN_ROOT = Path(__file__).parent.parent
COMMANDS_DIR = PLUGIN_ROOT / "commands"
AGENTS_DIR = PLUGIN_ROOT / "agents"
PROTOCOLS_DIR = PLUGIN_ROOT / "protocols"


# =============================================================================
# Read-Filesystem-Only-No-Narration: /repacted:bootstrap command structural pins
# =============================================================================


class TestBootstrapCommandStructure:
    """Plan scenarios bootstrap_command_file_exists_with_required_frontmatter
    + bootstrap_command_contains_only_ritual_content. The scaled-down
    bootstrap command owns ritual mechanics ONLY; governance / mission /
    motto / SACROSANCT / FINAL MANDATE belong to the persona body
    delivered via the --agent flag.
    """

    BOOTSTRAP_PATH = COMMANDS_DIR / "bootstrap.md"

    def test_bootstrap_command_file_exists(self):
        assert self.BOOTSTRAP_PATH.is_file(), (
            f"{self.BOOTSTRAP_PATH} must exist. /repacted:bootstrap is the "
            "scaled-down session-start ritual command registered in "
            "plugin.json; absent file → slash command resolves to nothing."
        )





# =============================================================================
# Race-Window-Skip: Bootstrap marker clear behavior across sources
# =============================================================================


class TestBootstrapMarkerClearAcrossSources:
    """Plan scenario bootstrap_marker_clear_on_resume — pins the rule that
    the bootstrap-complete marker is cleared ONLY on source='clear', NOT
    on source='resume'/'startup'/'compact'. A regression that cleared the
    marker on resume would force the per-session ritual to re-fire on
    every context-retain resume — defeating the per-session-not-per-turn
    invariant.

    Source-level pin (not main()-runner): exercising main() proved
    isolation-fragile because session_init.init() writes a session
    context file via pact_context's module-level cache, polluting
    downstream CLAUDE.md path-resolution under certain
    intermediate test orders. This source-level test asserts the same
    invariant by inspecting the gating predicate in session_init.py
    directly — single-line guard that the marker-unlink is gated by
    `source == 'clear'`."""

    SESSION_INIT_PATH = (
        Path(__file__).parent.parent / "hooks" / "session_init.py"
    )

    @pytest.fixture
    def session_init_text(self):
        # main() and the frame modules it calls (A4b split), entry point first.
        paths = [self.SESSION_INIT_PATH] + sorted(
            self.SESSION_INIT_PATH.parent.glob("shared/session_init_*.py")
        )
        return "\n".join(p.read_text(encoding="utf-8") for p in paths)

    def test_marker_unlink_gated_by_clear_source(self, session_init_text):
        """The marker-unlink branch must be entered only when
        `is_marker_reset` is true, and `is_marker_reset` must be
        defined as `source == "clear"`."""
        # Find the is_marker_reset assignment
        match = re.search(
            r'is_marker_reset\s*=\s*(.+)$',
            session_init_text,
            re.MULTILINE,
        )
        assert match is not None, (
            "session_init.py must define is_marker_reset; the bootstrap "
            "marker unlink branch is gated by it. Lost gating means the "
            "marker would be unlinked unconditionally on every session "
            "start (defeating the per-session ritual invariant)."
        )
        gating_expr = match.group(1).strip()
        assert gating_expr == 'source == "clear"', (
            f"is_marker_reset gating must be exactly `source == \"clear\"` "
            f"to ensure marker-unlink fires only on /clear (not on resume, "
            f"startup, or compact). Found: {gating_expr!r}"
        )

    def test_marker_unlink_call_is_inside_clear_branch(
        self, session_init_text
    ):
        """The bootstrap-marker cleanup must be invoked only inside the
        `if is_marker_reset:` branch, and the helper that performs the
        cleanup must do exactly the marker unlink (no broader scope).
        A drift in either direction breaks the persona §2 contract:
        - Unconditional invocation defeats the per-session ritual
          invariant (marker zapped on every session).
        - Helper that touches more than the marker (e.g., team config)
          contradicts the persona §2 self-healing claim.
        """
        # The clear branch must call the named cleanup helper.
        helper_call_match = re.search(
            r'_clear_bootstrap_marker\(\s*session_path\s*\)',
            session_init_text,
        )
        assert helper_call_match is not None, (
            "session_init.py must invoke `_clear_bootstrap_marker(session_path)` "
            "— the named helper that performs the /clear marker cleanup."
        )

        # The invocation must sit inside an `if is_marker_reset:` branch.
        before = session_init_text[: helper_call_match.start()]
        guard_match = re.search(
            r'^(\s*)if\s+is_marker_reset:\s*$',
            before,
            re.MULTILINE,
        )
        assert guard_match is not None, (
            "_clear_bootstrap_marker is not invoked inside an "
            "`if is_marker_reset:` branch. Without this guard, the "
            "marker would be unlinked on every session source — "
            "defeating the per-session ritual invariant."
        )

        # The helper itself must perform the marker unlink (and nothing
        # broader). Pin both halves of the persona §2 contract.
        helper_def_match = re.search(
            r'def\s+_clear_bootstrap_marker\s*\([^)]*\)\s*->\s*None\s*:\s*'
            r'(?P<body>(?:.|\n)+?)(?=\n\ndef\s|\nclass\s|\Z)',
            session_init_text,
        )
        assert helper_def_match is not None, (
            "session_init.py must define `def _clear_bootstrap_marker(...)`."
        )
        body = helper_def_match.group("body")
        # Strip the leading triple-quoted docstring before scanning for
        # operations — docstrings legitimately reference team-config /
        # config.json by name to explain the scope.
        body_no_docstring = re.sub(
            r'^\s*("""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\')',
            "",
            body,
            count=1,
        )
        assert "BOOTSTRAP_MARKER_NAME" in body_no_docstring and ".unlink(" in body_no_docstring, (
            "_clear_bootstrap_marker must perform the bootstrap-marker "
            "unlink — the body lost the cleanup it was extracted to own."
        )
        # Persona §2 self-healing claim: the helper must NOT touch the
        # team config. Pin operationally — any new filesystem reference
        # to teams/ or config.json in the implementation would broaden
        # the helper's scope and break the persona claim.
        assert "teams" not in body_no_docstring and "config.json" not in body_no_docstring, (
            "_clear_bootstrap_marker must NOT touch team config; the "
            "persona §2 self-healing claim depends on team config "
            "persisting across /clear."
        )


# =============================================================================
# Emit-Nothing-If-Empty-G6: Orchestrator persona body invariants
# =============================================================================




# =============================================================================
# G7: Lead-Side HALT Fan-Out byte-equal at two sites
# =============================================================================




# =============================================================================
# Y1 (auditor YELLOW-1): _TEACHBACK_REMINDER cross-file consistency
# =============================================================================


class TestTeachbackReminderCrossFileConsistency:
    """Auditor YELLOW-1 resolution. The peer_inject _TEACHBACK_REMINDER
    constant directs spawned teammates at metadata.teachback_submit and
    the teammate skill, whose rendered region teaches the same shape
    (tests/contract/test_protocol_rendered.py). If the constant's wording
    drifts and stops mentioning the canonical metadata key, teammates
    will store their teachback under a non-canonical key (or stop
    storing it) and the team-lead's teachback-validation harness will
    silently miss the payload."""

    CANONICAL_KEY = "metadata.teachback_submit"

    def test_peer_inject_teachback_reminder_mentions_canonical_key(self):
        """Import the private constant from peer_inject and assert it
        contains the canonical metadata key. Private-name import is
        acceptable in tests (per lead's Q3 confirmation)."""
        from peer_inject import _TEACHBACK_REMINDER  # pyright: ignore[reportMissingImports]
        assert self.CANONICAL_KEY in _TEACHBACK_REMINDER, (
            f"peer_inject._TEACHBACK_REMINDER must mention "
            f"{self.CANONICAL_KEY!r} so spawned teammates know where "
            f"to write their teachback. Current value:\n"
            f"{_TEACHBACK_REMINDER!r}"
        )




class TestMarkerNameConsistencyEncodingTolerant:
    """Cross-file consistency check: the marker name literal
    `bootstrap-complete` must appear in commands/bootstrap.md.

    Post-#664 the marker is written by the
    `bootstrap_marker_writer.py` UserPromptSubmit hook rather than an
    LLM-executed heredoc; commands/bootstrap.md retains a brief
    "Marker (hook-managed)" acknowledgment paragraph that mentions
    the marker by name so the LLM reading bootstrap.md end-to-end
    has an in-context pointer to the producer. The shell-encoding
    siblings of this test (which pinned the heredoc shape) were
    deleted with the heredoc itself.
    """

    BOOTSTRAP_MD = COMMANDS_DIR / "bootstrap.md"

    @pytest.fixture
    def bootstrap_text(self):
        return self.BOOTSTRAP_MD.read_text(encoding="utf-8")

    def test_marker_name_appears_in_bootstrap_md(self, bootstrap_text):
        """The marker name itself must appear textually somewhere in
        the body — preserved by the post-#664 acknowledgment paragraph."""
        assert BOOTSTRAP_MARKER_NAME in bootstrap_text, (
            f"commands/bootstrap.md must reference the marker name "
            f"{BOOTSTRAP_MARKER_NAME!r} verbatim; without it, the "
            f"acknowledgment paragraph is silently nameless."
        )
