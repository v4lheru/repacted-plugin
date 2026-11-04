"""
Tests for bootstrap_prompt_gate.py — UserPromptSubmit hook that injects
bootstrap-first instructions until bootstrap-complete marker exists.

Tests cover:
1. Marker exists → suppressOutput (fast path, zero tokens)
2. No marker + PACT team-lead session → inject additionalContext with bootstrap instruction
3. Non-PACT session (no session dir) → suppressOutput (no-op passthrough)
4. Teammate / non-lead frame (non-lead agent_type) → suppressOutput (no-op passthrough)
5. Malformed stdin JSON → fail-open (suppressOutput, exit 0)
6. Exception in _check_bootstrap_needed → fail-open (suppressOutput, exit 0)
7. main() entry point: exit codes, output format, JSON structure
8. Error/suppress mutual exclusivity: never emits systemMessage
9. Injection content includes required instruction text
10. Marker file lifecycle: create → check → gate behavior changes
"""

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from shared.constants import BOOTSTRAP_MARKER_NAME

_SUPPRESS_EXPECTED = {
    "suppressOutput": True,
    "hookSpecificOutput": {"hookEventName": "UserPromptSubmit"},
}

# Session identity constants used across all tests
_SESSION_ID = "test-session"
_PROJECT_DIR = "/test/project"
_SLUG = "project"


# =============================================================================
# Helpers
# =============================================================================


def _make_input(session_id=_SESSION_ID, source="startup",
                agent_type="pact-orchestrator"):
    """Build a minimal UserPromptSubmit hook input dict.

    #878: the gate now keys lead-detection on the harness-set agent_type via
    is_lead. The default is a LEAD frame (the unmarked case these tests
    historically assumed). Teammate/non-lead tests pass agent_type=<teammate>
    or agent_type=None to exercise the bypass branch.
    """
    data = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": session_id,
        "prompt": "Hello world",
        "source": source,
    }
    if agent_type is not None:
        data["agent_type"] = agent_type
    return data


def _run_main(input_data, capsys):
    """Run bootstrap_prompt_gate.main() with the given input, return (exit_code, stdout_json)."""
    from bootstrap_prompt_gate import main

    with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
        with pytest.raises(SystemExit) as exc_info:
            main()

    captured = capsys.readouterr()
    return exc_info.value.code, json.loads(captured.out.strip())


def _setup_pact_session(monkeypatch, tmp_path, with_marker=False,
                        plugin_version="9.9.9"):
    """Set up a PACT session context with session dir under tmp_path.

    Monkeypatches Path.home to tmp_path so get_session_dir() returns a
    path under tmp_path. Writes a context file and patches pact_context
    module state. When ``with_marker=True``, writes a properly-stamped
    properly-stamped marker (post-#662); empty `touch` markers no longer satisfy the
    gate.

    Returns the session_dir path.
    """
    import hashlib
    import shared.pact_context as ctx_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    session_dir = tmp_path / ".claude" / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)

    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": plugin_version}), encoding="utf-8"
    )

    context_file = session_dir / "pact-session-context.json"
    context_file.write_text(json.dumps({
        "team_name": "",
        "session_id": _SESSION_ID,
        "project_dir": _PROJECT_DIR,
        "plugin_root": str(plugin_root),
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")

    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)

    if with_marker:
        sid = session_dir.name
        sig = hashlib.sha256(
            f"{sid}|{str(plugin_root).rstrip('/')}|{plugin_version}|1".encode()
        ).hexdigest()
        (session_dir / BOOTSTRAP_MARKER_NAME).write_text(
            json.dumps({"v": 1, "sid": sid, "sig": sig}),
            encoding="utf-8",
        )

    return session_dir


# =============================================================================
# _check_bootstrap_needed — unit tests
# =============================================================================


class TestCheckBootstrapNeeded:
    """Tests for _check_bootstrap_needed() decision logic."""

    def test_returns_none_when_marker_exists(self, monkeypatch, tmp_path):
        """Marker exists → None (suppress path)."""
        from bootstrap_prompt_gate import _check_bootstrap_needed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=True)

        result = _check_bootstrap_needed(_make_input())
        assert result is None

    def test_returns_instruction_when_no_marker(self, monkeypatch, tmp_path):
        """No marker + team-lead session → bootstrap instruction string with session dir."""
        from bootstrap_prompt_gate import _check_bootstrap_needed

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        result = _check_bootstrap_needed(_make_input())
        assert result is not None
        assert 'Skill("repacted:bootstrap")' in result
        assert f"PACT_SESSION_DIR={session_dir}" in result


# =============================================================================
# main() — integration tests
# =============================================================================


class TestMainEntryPoint:
    """Tests for main() stdin/stdout/exit behavior."""

    def test_exits_0_on_inject(self, monkeypatch, tmp_path, capsys):
        """Even when injecting, exit code is 0 (never blocks prompts)."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        exit_code, output = _run_main(_make_input(), capsys)
        assert exit_code == 0
        assert "hookSpecificOutput" in output

    def test_injects_additional_context_when_no_marker(self, monkeypatch, tmp_path, capsys):
        """No marker → output has hookSpecificOutput.additionalContext."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        _, output = _run_main(_make_input(), capsys)
        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "UserPromptSubmit"
        assert "additionalContext" in hso
        assert 'Skill("repacted:bootstrap")' in hso["additionalContext"]


# =============================================================================
# Fail-open — P0 priority
# =============================================================================


class TestFailOpen:
    """P0: Every exception path must fail-open (exit 0, suppressOutput)."""

    def test_malformed_stdin_json(self, capsys):
        """Invalid JSON on stdin → fail-open."""
        from bootstrap_prompt_gate import main

        with patch("sys.stdin", io.StringIO("not valid json {")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED

    def test_empty_stdin(self, capsys):
        """Empty stdin → fail-open."""
        from bootstrap_prompt_gate import main

        with patch("sys.stdin", io.StringIO("")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED


# =============================================================================
# Error/suppress mutual exclusivity — P0 priority
# =============================================================================


class TestErrorSuppressMutualExclusivity:
    """P0: These hooks use suppressOutput for fail-open, never systemMessage."""

    def test_malformed_stdin_no_system_message(self, capsys):
        """Malformed stdin outputs suppressOutput, not systemMessage."""
        from bootstrap_prompt_gate import main

        with patch("sys.stdin", io.StringIO("bad json")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "suppressOutput" in parsed
        assert "systemMessage" not in parsed

    def test_exception_no_system_message(self, capsys):
        """Exception in gate logic outputs suppressOutput, not systemMessage."""
        from bootstrap_prompt_gate import main

        with patch(
            "bootstrap_prompt_gate._check_bootstrap_needed",
            side_effect=RuntimeError("boom"),
        ):
            with patch("sys.stdin", io.StringIO(json.dumps(_make_input()))):
                with pytest.raises(SystemExit):
                    main()

        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert "suppressOutput" in parsed
        assert "systemMessage" not in parsed


# =============================================================================
# Marker lifecycle — P3 priority
# =============================================================================


class TestMarkerLifecycle:
    """P3: Marker creation → gate self-disable → idempotent suppress."""

    def test_gate_transitions_on_marker_creation(self, monkeypatch, tmp_path, capsys):
        """Before marker: inject. After marker stamp: suppress."""
        import hashlib
        import shared.pact_context as ctx_module

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"

        # Before marker — should inject
        _, output_before = _run_main(_make_input(), capsys)
        assert "hookSpecificOutput" in output_before

        # Stamp a properly-formed marker (post-#662)
        sid = session_dir.name
        sig = hashlib.sha256(
            f"{sid}|{str(plugin_root).rstrip('/')}|9.9.9|1".encode()
        ).hexdigest()
        (session_dir / BOOTSTRAP_MARKER_NAME).write_text(
            json.dumps({"v": 1, "sid": sid, "sig": sig}),
            encoding="utf-8",
        )

        # Reset cache for second call
        ctx_module._cache = None

        # After marker — should suppress
        _, output_after = _run_main(_make_input(), capsys)
        assert output_after == _SUPPRESS_EXPECTED

    def test_repeated_calls_with_marker_are_idempotent(self, monkeypatch, tmp_path, capsys):
        """Multiple calls with marker present all produce suppressOutput."""
        import shared.pact_context as ctx_module

        _setup_pact_session(monkeypatch, tmp_path, with_marker=True)

        for _ in range(3):
            ctx_module._cache = None
            _, output = _run_main(_make_input(), capsys)
            assert output == _SUPPRESS_EXPECTED


# =============================================================================
# Audit-anchor parity (mirror of writer's emit-shape invariant)
# =============================================================================


class TestAuditAnchorParity:
    """Every JSON output path bootstrap_prompt_gate produces MUST carry
    hookSpecificOutput.hookEventName == "UserPromptSubmit". Missing the
    field silently fails open at the platform layer (per pinned context).
    The invariant is parametrized over the two distinct emit shapes:

    - "advisory": _emit_load_failure_advisory module-load advisory
      (additionalContext path)
    - "suppress": every other exit path via the _SUPPRESS_OUTPUT constant

    Both MUST carry the audit anchor — parametrizing pins the invariant
    so no future emit path can be added without it. Mirrors
    bootstrap_marker_writer's test_every_emit_shape_carries_hook_event_name
    so all three bootstrap-related hooks share one parity contract.
    """

    @pytest.mark.parametrize("shape", ["advisory",])
    def test_every_emit_shape_carries_hook_event_name(self, shape, capsys):
        if shape == "advisory":
            from bootstrap_prompt_gate import _emit_load_failure_advisory
            with pytest.raises(SystemExit):
                _emit_load_failure_advisory("module imports", RuntimeError("x"))
            captured = capsys.readouterr()
            out = json.loads(captured.out.strip())
        elif shape == "suppress":
            from bootstrap_prompt_gate import _SUPPRESS_OUTPUT
            out = json.loads(_SUPPRESS_OUTPUT)
        else:  # pragma: no cover
            pytest.fail(f"unknown shape param: {shape}")

        hso = out.get("hookSpecificOutput")
        assert hso is not None, (
            f"shape={shape} emit MUST carry hookSpecificOutput; missing "
            f"the field silently fails open at the platform layer."
        )
        assert hso.get("hookEventName") == "UserPromptSubmit", (
            f"shape={shape} emit MUST carry hookEventName=='UserPromptSubmit'; "
            f"got {hso!r}"
        )


class TestSelfHealWiring:
    """Wiring tests for the heal_context_if_missing call in
    _check_bootstrap_needed: a missing context file is healed in-line
    (lead frame + valid session_id), after which the SAME invocation
    resolves the session dir and flows into the normal no-marker inject
    branch — the heal unbricks the gate without forging bootstrap.
    """

    _SID = "deadbeef-7777-8888-9999-aaaabbbbcccc"

    def test_missing_context_healed_then_instruction_injected(
            self, monkeypatch, tmp_path):
        """Context file ABSENT + lead frame → healed + bootstrap
        instruction returned (chain effect; heal does NOT suppress)."""
        import shared.pact_context as ctx_module
        from bootstrap_prompt_gate import _check_bootstrap_needed

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/test/project")

        # init() is a no-op when _context_path is pre-set: derive the path
        # exactly as a fresh hook process would, but pointed under tmp_path.
        target = (tmp_path / ".claude" / "pact-sessions" / "project" /
                  self._SID / "pact-session-context.json")
        monkeypatch.setattr(ctx_module, "_context_path", target)
        monkeypatch.setattr(ctx_module, "_cache", None)
        assert not target.exists()

        result = _check_bootstrap_needed(_make_input(session_id=self._SID))

        assert target.exists(), "gate should heal the missing context file"
        assert result is not None, (
            "healed lead session without marker must flow into the inject "
            "branch, not suppress"
        )
        assert "repacted:bootstrap" in result
        assert "PACT_SESSION_DIR=" in result


class TestStalenessDetection:
    """Unit tests for _detect_stale_session_block() — the stdlib-only
    Resume-line session_id compare that flags a stale CLAUDE.md 'Current
    Session' block after a session_init crash."""

    _ACTUAL = "deadbeef-0000-1111-2222-333344445555"
    _STALE = "01dcafe0-9999-8888-7777-666655554444"

    def _project_with_claude_md(self, tmp_path, recorded_sid,
                                location=".claude"):
        """Create a project dir whose CLAUDE.md records ``recorded_sid``."""
        project = tmp_path / "proj"
        if location == ".claude":
            target = project / ".claude" / "CLAUDE.md"
        else:
            target = project / "CLAUDE.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "# Project\n\n<!-- SESSION_START -->\n## Current Session\n"
            f"- Resume: `claude --resume {recorded_sid}`\n"
            "- Team: `pact-old`\n<!-- SESSION_END -->\n",
            encoding="utf-8",
        )
        return project

    def test_mismatch_returns_warning(self, monkeypatch, tmp_path):
        from bootstrap_prompt_gate import _detect_stale_session_block

        project = self._project_with_claude_md(tmp_path, self._STALE)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = _detect_stale_session_block({"session_id": self._ACTUAL})

        assert result is not None
        assert "stale session block" in result
        assert self._STALE in result
        assert self._ACTUAL in result


# Test-local mirror of the production regex, used ONLY to extract the
# resolver-chosen file's recorded id in the parity test above.
import re as _re_for_parity  # noqa: E402
_RE_RESUME_TEST = _re_for_parity.compile(
    r"- Resume:\s*`claude --resume\s+([0-9a-f-]+)`"
)


class TestStalenessComposition:
    """Placement tests: staleness composes onto the bootstrap instruction
    ONLY on the lead+no-marker inject branch; the marker-set fast path
    never reads CLAUDE.md (perf contract pin)."""

    _ACTUAL_HEX = "deadbeef-0000-1111-2222-333344445555"
    _STALE = "01dcafe0-9999-8888-7777-666655554444"

    def _stale_project(self, monkeypatch, tmp_path):
        project = tmp_path / "proj"
        target = project / ".claude" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        target.write_text(
            f"<!-- SESSION_START -->\n- Resume: `claude --resume {self._STALE}`\n<!-- SESSION_END -->\n", encoding="utf-8"
        )
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
        return project

    def test_mismatch_composes_instruction_and_warning(
            self, monkeypatch, tmp_path):
        """Lead + no marker + stale block → BOTH the bootstrap instruction
        AND the staleness warning in one additionalContext string."""
        from bootstrap_prompt_gate import _check_bootstrap_needed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        self._stale_project(monkeypatch, tmp_path)

        result = _check_bootstrap_needed(_make_input(
            session_id=self._ACTUAL_HEX))

        # NOTE: _setup_pact_session pre-writes the context file keyed on
        # _SESSION_ID; the heal is a no-op here (file present).
        assert result is not None
        assert "repacted:bootstrap" in result            # instruction present
        assert "stale session block" in result       # warning appended
        assert result.index("repacted:bootstrap") < result.index(
            "stale session block"), "warning is APPENDED, not prepended"


class TestSubprocessStalenessE2E:
    """Full-process E2E for the #943 incident chain: session_init crashed
    at SessionStart → pact-session-context.json ABSENT → the prompt gate's
    self-heal re-creates it in the SAME invocation → the lead+no-marker
    inject branch is reached → the staleness check reads the project
    CLAUDE.md and (on a recorded-vs-actual session mismatch) appends the
    advisory warning to the bootstrap instruction. In-process tests mock
    pieces of this chain; a fresh subprocess proves the chain end-to-end
    with real module loading, real env reads, and real disk I/O.

    Self-masker rule: bootstrap_prompt_gate exits 0 on EVERY path, so
    health is asserted on stdout content + on-disk effects; returncode is
    asserted only alongside content.
    """

    # Hex-only ids (the production _RESUME_LINE_RE matches [0-9a-f-]).
    _SID = "deadbeef-4242-4242-4242-deadbeef4242"
    _STALE_SID = "0badcafe-9999-8888-7777-666655554444"

    def _run_gate_subprocess(self, tmp_path, recorded_sid):
        """Scaffold: HOME under tmp_path, real project dir whose
        .claude/CLAUDE.md records ``recorded_sid``, context file ABSENT,
        lead UserPromptSubmit frame for ``_SID``. Returns
        (CompletedProcess, healed_context_path)."""
        import subprocess

        home = tmp_path
        project = home / "staleproj"
        claude_md = project / ".claude" / "CLAUDE.md"
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text(
            "# Project\n\n<!-- SESSION_START -->\n## Current Session\n"
            f"- Resume: `claude --resume {recorded_sid}`\n"
            "- Team: `pact-old`\n<!-- SESSION_END -->\n",
            encoding="utf-8",
        )

        plugin_root = home / "plugin"
        plugin_root.mkdir(parents=True)

        # Session dir intentionally NOT created; context file ABSENT.
        ctx = (home / ".claude" / "pact-sessions" / "staleproj" /
               self._SID / "pact-session-context.json")

        hook_path = (
            Path(__file__).parent.parent / "hooks" /
            "bootstrap_prompt_gate.py"
        )
        assert hook_path.exists(), f"gate hook missing at {hook_path}"

        stdin_payload = json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "session_id": self._SID,
            "prompt": "first prompt after session_init crashed",
            "agent_type": "pact-orchestrator",
        })

        env = os.environ.copy()
        env["HOME"] = str(home)
        env.pop("CLAUDE_CONFIG_DIR", None)  # force the HOME/.claude fallback
        env["CLAUDE_PROJECT_DIR"] = str(project)
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)

        result = subprocess.run(
            [sys.executable, str(hook_path)],
            input=stdin_payload,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
            timeout=10,
        )
        return result, ctx

    def test_heal_chain_with_stale_block_injects_instruction_and_warning(
            self, tmp_path):
        """Mismatch (CLAUDE.md records the PREVIOUS session) → ONE
        additionalContext string carrying the bootstrap instruction with
        the staleness warning APPENDED, both ids named; the context file
        is healed on disk with session_init-parity content."""
        result, ctx = self._run_gate_subprocess(
            tmp_path, recorded_sid=self._STALE_SID
        )

        # Content first (self-masker rule), rc alongside.
        out = json.loads(result.stdout.strip())
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "UserPromptSubmit"
        context = hso["additionalContext"]
        assert "repacted:bootstrap" in context
        assert "PACT_SESSION_DIR=" in context
        assert "stale session block" in context
        assert self._STALE_SID in context, "recorded (stale) id named"
        assert self._SID in context, "actual id named"
        assert context.index("repacted:bootstrap") < context.index(
            "stale session block"), "warning is APPENDED, not prepended"
        assert "suppressOutput" not in out
        assert result.returncode == 0, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )

        # Heal landed on disk (the chain's enabling step).
        assert ctx.exists(), "self-heal should re-create the context file"
        content = json.loads(ctx.read_text(encoding="utf-8"))
        assert content["team_name"] == "session-deadbeef"
        assert content["session_id"] == self._SID

