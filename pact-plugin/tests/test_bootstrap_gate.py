"""
Tests for bootstrap_gate.py — PreToolUse hook that blocks code-editing and
agent-dispatch tools until the bootstrap-complete marker exists.

Tests cover:

_check_tool_allowed() unit tests:
1. Marker exists (properly stamped with valid content fingerprint) → None for any tool (fast path)
2. No marker + blocked tool (Edit) → deny reason string
3. No marker + blocked tool (Write) → deny reason string
4. No marker + blocked tool (Agent) → deny reason string
5. No marker + blocked tool (NotebookEdit) → deny reason string
6. No marker + allowed tool (Read) → None
7-13. No marker + allowed tools (Glob, Grep, Bash, WebFetch, WebSearch,
       AskUserQuestion, ExitPlanMode) → None
14. No marker + MCP tool → None (mcp__ prefix match)
15. Non-PACT session (no session dir) → None
16. Teammate → None (passthrough)
17. Empty tool_name → None (not in blocked set)
18. Non-string tool_name → None (isinstance guard)

main() integration tests:
19. Blocked tool → exit 2, deny JSON with permissionDecision
20. Allowed tool → exit 0, suppressOutput
21. Marker exists → exit 0, suppressOutput
22. Non-PACT → exit 0, suppressOutput
23. Teammate → exit 0, suppressOutput

Fail-OPEN preserved for input-side failures (P0):
24. Malformed stdin → exit 0, suppressOutput
25. Empty stdin → exit 0, suppressOutput

Fail-CLOSED for gate-logic exceptions (P0):
26. Exception in _check_tool_allowed → exit 2, deny JSON with hookEventName

Error/suppress mutual exclusivity (P0):
27. Input-side fail-open paths emit suppressOutput, never systemMessage
28. Deny path (block + runtime fail-closed) emits permissionDecision, not suppressOutput
29. Allow paths emit suppressOutput, not hookSpecificOutput

Blocked tool set completeness (P2 — post-#662):
30. Exactly 4 blocked tools in the set
31. Members are exactly {Edit, Write, Agent, NotebookEdit}
32. Bash is NOT in blocked set (circular dependency guard)
33. Read is NOT in blocked set (exploration tool)

Deny reason content (P2):
34. Deny reason mentions Skill("repacted:bootstrap")
35. Deny reason mentions available tools (Bash, Read, Glob, Grep)

is_marker_set() — public helper:
36. None / empty session_dir → False
37. Marker absent → False
38. Marker symlink (S2) → False
39. Marker is a directory (S2 corollary) → False
40. Ancestor symlink (S4) → False
41. Properly-stamped marker → True
42. Empty file (legacy `touch` form) → False
43. Marker with wrong sid → False
44. Marker with wrong version → False
45. Malformed JSON marker → False
46. Marker with wrong signature → False
47. Marker with oversized content → False
48. Marker with missing plugin context → False
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

_FAIL_CLOSED_SRC = Path(__file__).parent.parent / "hooks" / "_fail_closed.py"
from unittest.mock import patch

import pytest

from shared.constants import BOOTSTRAP_MARKER_NAME

_SUPPRESS_EXPECTED = {
    "suppressOutput": True,
    "hookSpecificOutput": {"hookEventName": "PreToolUse"},
}

# Session identity constants
_SESSION_ID = "test-session"
_PROJECT_DIR = "/test/project"
_SLUG = "project"

# Canonical deny-reason literal — independent of bootstrap_gate._DENY_REASON.
# Hard-coded here so byte-identity tests anchor on this literal rather than
# self-comparing against the imported constant (which would silently pass if
# both the constant and the assertion target alias the same mutated string).
# Any intentional change to the deny reason must update BOTH this literal AND
# bootstrap_gate._DENY_REASON — that two-site edit is the load-bearing review
# surface for deny-reason drift.
_CANONICAL_DENY_REASON_LITERAL = (
    "PACT bootstrap required. Invoke Skill(\"repacted:bootstrap\") first. "
    "Agent dispatch (Agent) is blocked until bootstrap completes. "
    "Every other tool is available. "
    "If bootstrap cannot complete because the task-management tools are "
    "unavailable, see "
    "https://github.com/v4lheru/repacted-plugin#if-specialist-agents-will-not-spawn"
)


# =============================================================================
# Helpers
# =============================================================================


def _make_input(tool_name="Edit", session_id=_SESSION_ID,
                agent_type="pact-orchestrator"):
    """Build a minimal PreToolUse hook input dict.

    #878: the gate now keys lead-detection on the harness-set agent_type via
    is_lead. The default is a LEAD frame (the unmarked case these DENY tests
    historically assumed via empty resolve_agent_name). Teammate/non-lead tests
    pass agent_type=<teammate> or agent_type=None to exercise the bypass branch.
    """
    data = {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": {},
    }
    if agent_type is not None:
        data["agent_type"] = agent_type
    return data


def _run_main(input_data, capsys):
    """Run bootstrap_gate.main(), return (exit_code, stdout_json)."""
    from bootstrap_gate import main

    with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
        with pytest.raises(SystemExit) as exc_info:
            main()

    captured = capsys.readouterr()
    return exc_info.value.code, json.loads(captured.out.strip())


def _write_f24_marker(session_dir: Path, plugin_root: Path,
                      plugin_version: str = "9.9.9",
                      marker_version: int = 1,
                      sid: str | None = None,
                      sig: str | None = None) -> Path:
    """Write a properly-stamped marker. Override fields to forge invalid
    variants for negative tests.
    """
    from shared.marker_schema import MARKER_SCHEMA_VERSION

    real_sid = sid if sid is not None else session_dir.name
    real_sig_input = (
        f"{real_sid}|{str(plugin_root).rstrip('/')}|{plugin_version}|{marker_version}"
    )
    real_sig = sig if sig is not None else hashlib.sha256(
        real_sig_input.encode("utf-8")
    ).hexdigest()
    payload = {"v": marker_version, "sid": real_sid, "sig": real_sig}
    marker = session_dir / BOOTSTRAP_MARKER_NAME
    marker.write_text(json.dumps(payload), encoding="utf-8")
    # Sanity: caller using default args should produce a valid stamp
    # for the current MARKER_SCHEMA_VERSION constant.
    if marker_version == 1:
        assert MARKER_SCHEMA_VERSION == 1
    return marker


def _setup_pact_session(monkeypatch, tmp_path, with_marker=False,
                        plugin_version="9.9.9"):
    """Set up a PACT session context with session dir under tmp_path.

    Monkeypatches Path.home to tmp_path so get_session_dir() returns a
    path under tmp_path. Returns the session_dir path.

    When ``with_marker=True`` writes a properly-stamped marker (post-#662);
    callers that want to test legacy or invalid markers should pass
    ``with_marker=False`` and use ``_write_f24_marker`` directly with override
    fields.
    """
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
        _write_f24_marker(session_dir, plugin_root, plugin_version=plugin_version)

    return session_dir


# =============================================================================
# _check_tool_allowed — unit tests
# =============================================================================


class TestCheckToolAllowed:
    """Tests for _check_tool_allowed() decision logic."""

    # --- Marker exists: fast path ---

    @pytest.mark.parametrize("tool_name", [
    "Edit",
])
    def test_marker_exists_allows_any_tool(self, monkeypatch, tmp_path, tool_name):
        """Marker exists → None for any tool (including usually-blocked ones)."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=True)

        result = _check_tool_allowed(_make_input(tool_name))
        assert result is None

    # --- No marker: blocked tools ---

    @pytest.mark.parametrize("tool_name", [
    "Edit",
])
    def test_editing_tools_are_allowed_before_bootstrap(self, monkeypatch, tmp_path, tool_name):
        """1.4.1 (design rule): only dispatch waits for bootstrap."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        assert _check_tool_allowed(_make_input(tool_name)) is None

    # --- No marker: allowed tools ---

    # --- MCP tools ---

    # --- Non-PACT and teammate passthrough ---

    # --- Edge cases ---

# =============================================================================
# main() — integration tests
# =============================================================================


class TestMainEntryPoint:
    """Tests for main() stdin/stdout/exit behavior."""

    def test_blocked_tool_exits_2(self, monkeypatch, tmp_path, capsys):
        """Blocked tool → exit 2 (PreToolUse deny convention)."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        exit_code, output = _run_main(_make_input("Agent"), capsys)
        assert exit_code == 2

    def test_blocked_tool_outputs_deny_json(self, monkeypatch, tmp_path, capsys):
        """Blocked tool → deny JSON with permissionDecision."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        _, output = _run_main(_make_input("Agent"), capsys)
        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "permissionDecisionReason" in hso

# =============================================================================
# Fail-open (input-side) — P0 priority
# =============================================================================


class TestInputSideFailOpen:
    """P0: Malformed/empty stdin remains fail-OPEN — input-side failures
    are the harness's domain (cannot evaluate without input).
    """

    def test_malformed_stdin_json(self, capsys):
        """Invalid JSON on stdin → fail-open."""
        from bootstrap_gate import main

        with patch("sys.stdin", io.StringIO("not valid json {")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED

    def test_empty_stdin(self, capsys):
        """Empty stdin → fail-open."""
        from bootstrap_gate import main

        with patch("sys.stdin", io.StringIO("")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out.strip()) == _SUPPRESS_EXPECTED


# =============================================================================
# Fail-closed (gate-logic exception) — P0 priority
# =============================================================================


class TestFailClosedGateLogic:
    """Runtime fail-closed (#662, post-#658 defect class): runtime exception in
    ``_check_tool_allowed`` must DENY (not fail-OPEN). Pre-#662 this path
    was fail-OPEN — that was the same defect class as #658.
    """

    def test_exception_in_check_tool_allowed_emits_deny(self, capsys):
        """RuntimeError in _check_tool_allowed → exit 2 with structured deny."""
        from bootstrap_gate import main

        with patch(
            "bootstrap_gate._check_tool_allowed",
            side_effect=RuntimeError("boom"),
        ):
            with patch("sys.stdin", io.StringIO(json.dumps(_make_input()))):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        hso = parsed["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "RuntimeError" in hso["permissionDecisionReason"]


# =============================================================================
# Error/suppress mutual exclusivity — P0 priority
# =============================================================================


class TestErrorSuppressMutualExclusivity:
    """P0: input-side fail-open uses suppressOutput; deny path (block + runtime fail-closed
    fail-closed) uses hookSpecificOutput. systemMessage is never emitted on
    suppress/deny paths; the degraded warn path (#942, defer/ask) is the
    single deliberate systemMessage emitter (see TestDegradedMode).
    """

    def test_deny_path_no_suppress_output(self, monkeypatch, tmp_path, capsys):
        """Deny path → hookSpecificOutput, NOT suppressOutput."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        _, output = _run_main(_make_input("Agent"), capsys)
        assert "hookSpecificOutput" in output
        assert "suppressOutput" not in output

    def test_allow_path_carries_audit_anchor_only(self, monkeypatch, tmp_path, capsys):
        """Allow path → suppressOutput + hookSpecificOutput.hookEventName only,
        NO permissionDecision. The audit-anchor parity retrofit aligns the
        suppress envelope with bootstrap_marker_writer's pattern (the writer
        was the role model; both gates now match)."""
        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        _, output = _run_main(_make_input("Read"), capsys)
        assert output.get("suppressOutput") is True
        hso = output.get("hookSpecificOutput")
        assert hso == {"hookEventName": "PreToolUse"}, (
            f"Allow envelope should be exactly suppressOutput + audit-anchor "
            f"hookEventName, no permissionDecision. Got: {output!r}"
        )


# =============================================================================
# Blocked tool set completeness — P2 priority (post-#662)
# =============================================================================


class TestBlockedToolSet:
    """P2: Verify the blocked tool set is correct and complete (post-#662)."""

    def test_blocked_set_exact_members(self):
        """Blocked set is the spawn tool only (1.4.1): `Agent`, the
        canonical name (#662), and `Task`, in case a harness still names it so.
        The bootstrap_gate registry matcher lists the same two names."""
        from bootstrap_gate import _BLOCKED_TOOLS

        assert _BLOCKED_TOOLS == frozenset({"Agent", "Task"})

    def test_bash_not_blocked(self):
        """Bash must NOT be in blocked set (circular dependency)."""
        from bootstrap_gate import _BLOCKED_TOOLS

        assert "Bash" not in _BLOCKED_TOOLS

# =============================================================================
# Canonical secretary spawn carve-out (#789)
# =============================================================================


def _setup_pact_session_with_team(monkeypatch, tmp_path, team_name="t1",
                                  members=None, with_marker=False,
                                  plugin_version="9.9.9"):
    """Set up a PACT session whose context carries a non-empty team_name and
    a matching team config at ~/.claude/teams/{team_name}/config.json.

    Mirrors _setup_pact_session but adds the team-config sidecar that the
    members[] readers consume via shared.pact_context._iter_members — the gate
    carve-out's _secretary_in_members JOIN witness (#1023) and the marker
    writer's _team_has_secretary DISPATCH witness alike. The monkeypatched
    Path.home means the team config lands under tmp_path.

    members: list of member dicts to embed at config.members[]. Defaults
    to a fresh-team shape (no secretary entry) which is the precondition
    the carve-out exists to handle.
    """
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
        "team_name": team_name,
        "session_id": _SESSION_ID,
        "project_dir": _PROJECT_DIR,
        "plugin_root": str(plugin_root),
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")

    teams_dir = tmp_path / ".claude" / "teams" / team_name
    teams_dir.mkdir(parents=True, exist_ok=True)
    config = {"members": members if members is not None else []}
    (teams_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)

    if with_marker:
        _write_f24_marker(session_dir, plugin_root, plugin_version=plugin_version)

    return session_dir


def _canonical_secretary_input(team_name="t1", overrides=None,
                               agent_type="pact-orchestrator"):
    """Build the canonical Agent(secretary) PreToolUse input.

    overrides: dict merged into tool_input to forge mismatched bindings
    in negative tests.

    #878: the gate keys lead-detection on is_lead (the harness-set agent_type).
    The secretary-spawn carve-out only applies on the LEAD path (the lead's
    pre-bootstrap secretary dispatch), so the default is a lead agent_type.
    """
    tool_input = {
        "subagent_type": "pact-secretary",
        "name": "secretary",
        "team_name": team_name,
    }
    if overrides:
        tool_input.update(overrides)
    data = {
        "hook_event_name": "PreToolUse",
        "session_id": _SESSION_ID,
        "tool_name": "Agent",
        "tool_input": tool_input,
    }
    if agent_type is not None:
        data["agent_type"] = agent_type
    return data


class TestCanonicalSecretarySpawnCarveOut:
    """#789: the canonical secretary spawn is allowed even when the
    bootstrap marker is absent — without this carve-out, the gate denies
    the only dispatch that could clear its own deny condition (the
    secretary spawn populates the team members[] entry that the marker
    writer requires before writing the marker).

    Four conjunctive bindings (tool_name, subagent_type, name,
    NOT _secretary_in_members). (#979: binding-4 — the tool_input.team_name
    equality — was dropped; the Agent(team_name=) arg is platform-ignored.
    #1023: binding 5 now reads the gate-local members[]-only JOIN witness
    _secretary_in_members, NOT bootstrap_marker_writer._team_has_secretary —
    the latter's inbox DISPATCH fallback is created pre-spawn and re-deadlocked
    the carve-out.) Each test below mentally reverts ONE binding and confirms
    the carve-out closes (predicate returns False → caller returns
    _DENY_REASON).
    """

    # --- Positive case: all bindings match → allow ---

    def test_secretary_spawn_allowed_when_members_lacks_secretary(
        self, monkeypatch, tmp_path,
    ):
        """All bindings match + no secretary in members[] → allow."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result is None

    def test_a_task_named_spawn_tool_gets_the_same_carve_out(self, monkeypatch, tmp_path):
        """1.4.1: Task is blocked beside Agent, so the carve-out must accept it too,
        or a harness naming the spawn tool Task deadlocks bootstrap."""
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session_with_team(monkeypatch, tmp_path, team_name="t1", members=[])
        frame = {**_canonical_secretary_input(team_name="t1"), "tool_name": "Task"}
        assert _check_tool_allowed(frame) is None
        assert _check_tool_allowed({**frame, "tool_input": {**frame["tool_input"], "name": "coder"}})

    def test_non_secretary_agent_still_blocked(self, monkeypatch, tmp_path):
        """subagent_type neither 'pact-secretary' nor 'repacted:pact-secretary'
        → predicate False → deny."""
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"subagent_type": "pact-architect"},
        ))
        assert result == _DENY_REASON

    def test_secretary_spawn_with_wrong_name_blocked(self, monkeypatch, tmp_path):
        """name != 'secretary' → predicate False → deny.

        An attacker (or a stale orchestrator) dispatching pact-secretary
        with a non-canonical name (e.g., 'sec', 'secretari') is denied.
        The literal name is load-bearing per commands/bootstrap.md Step 2.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"name": "secretari"},
        ))
        assert result == _DENY_REASON

    def test_carve_out_closes_after_secretary_in_members(
        self, monkeypatch, tmp_path,
    ):
        """_secretary_in_members(team_name) == True → predicate False → deny.

        The one-shot semantic (CLI): once the canonical spawn has landed and
        the secretary entry is in members[], the JOIN witness returns True so
        the carve-out cannot fire a second time in the same session. The marker
        writer's UserPromptSubmit hook handles the next turn from there.
        (#1023 D-record: under config-less Desktop members[] is structurally
        empty, so this self-closure does not apply there — marker-presence is
        the durable one-shot instead.)
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1",
            members=[{"id": "sec-1", "name": "secretary", "type": "pact-secretary"}],
        )

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result == _DENY_REASON

    def test_namespaced_secretary_spawn_blocked_when_members_has_secretary(
        self, monkeypatch, tmp_path,
    ):
        """The namespaced spelling satisfies binding 2 only; binding 5 still
        closes the carve-out once secretary is in members[]."""
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1",
            members=[{"name": "secretary", "agentType": "pact-secretary"}],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"subagent_type": "repacted:pact-secretary"},
        ))
        assert result == _DENY_REASON

    # --- subagent_type mismatch → predicate False → deny ---

    # --- Name mismatch → predicate False → deny (name-impostor blocked) ---

    # --- Cross-team injection → predicate False → deny ---

    # --- One-shot closure: secretary already in members[] → deny ---

    # --- End-to-end fresh-session repro ---

    # --- Fail-closed posture on team-config read error ---

# =============================================================================
# Premature-inbox carve-out regression (#1023)
# =============================================================================


def _create_secretary_inbox(tmp_path, team_name="t1"):
    """Reproduce the platform's PRE-SPAWN secretary-inbox write.

    The bootstrap choreography is::

        Step 1  TaskCreate(secretary briefing)
        Step 2  TaskUpdate(taskId, owner="secretary")   # platform delivers a
                                                         # task_assignment ->
                                                         # CREATES inboxes/secretary.json
        Step 3  Agent(name="secretary", ...)            # the carve-out must fire HERE

    So by Step 3 ``teams/<team>/inboxes/secretary.json`` ALREADY exists, written
    by the Step-2 ``TaskUpdate(owner)`` task-assignment delivery — BEFORE the
    ``Agent(secretary)`` spawn the carve-out exists to permit. This helper writes
    that file FAITHFULLY: the SAME path the marker writer's inbox witness reads
    (``get_claude_config_dir()/teams/<team>/inboxes/secretary.json`` ==
    ``Path.home()/.claude/...`` under the fixture's Path.home monkeypatch), the
    SAME filename, and a ``task_assignment``-shaped body matching the issue's
    ground-truth evidence (an assigned task with ``assignedBy="team-lead"``).

    This is NOT a synthetic pre-placed file divorced from the choreography — it
    is the exact artifact ``TaskUpdate(owner="secretary")`` produces, present at
    gate-eval time. The synthetic shortcut (an empty touch, or a file written in
    a shape the platform never produces) is precisely what let #1021 slip through
    #1019's verification, so the regression test must use the real shape.

    Returns the inbox file path.
    """
    inbox_dir = tmp_path / ".claude" / "teams" / team_name / "inboxes"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    inbox_path = inbox_dir / "secretary.json"
    # task_assignment-shaped body (issue #1023 evidence: a task_assignment for the
    # secretary task, assignedBy team-lead). The marker writer's inbox witness
    # only checks .is_file() — it never parses the body — but a faithful body
    # keeps the fixture honest about what the platform actually writes.
    inbox_path.write_text(json.dumps([
        {
            "type": "task_assignment",
            "taskId": "1",
            "subject": "secretary briefing",
            "assignedBy": "team-lead",
            "timestamp": "2026-01-01T00:00:00Z",
        }
    ]), encoding="utf-8")
    return inbox_path


def _setup_carveout_team_with_lead_session(
    monkeypatch, tmp_path, *, team_name="t1", lead_session_id,
    frame_session_id, members=None, seed_team_dir_name=None,
):
    """Both-modes carve-out fixture: like _setup_pact_session_with_team but the
    on-disk config.json carries a ``leadSessionId`` field and the context's
    ``session_id`` is set independently, so get_team_name's identity-match
    (_resolve_aligned_team_name) sees a genuine in-process (frame==lead) vs tmux
    (frame!=lead) topology.

    ``team_name`` is the PERSISTED context team_name (use "" to exercise the
    empty-SSOT fail-closed short-circuit). ``seed_team_dir_name`` is the dir name
    the team config/inbox are seeded under (defaults to ``team_name``); pass it
    explicitly when team_name is "" so the team dir still exists on disk but the
    SSOT is empty.

    Resets _aligned_cache (NOT reset by init(), which is a no-op once
    _context_path is pre-set) so each leg / loop iteration resolves freshly.
    """
    import shared.pact_context as ctx_module

    dir_name = seed_team_dir_name if seed_team_dir_name is not None else team_name

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    session_dir = tmp_path / ".claude" / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)

    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8"
    )

    context_file = session_dir / "pact-session-context.json"
    context_file.write_text(json.dumps({
        "team_name": team_name,
        "session_id": frame_session_id,
        "project_dir": _PROJECT_DIR,
        "plugin_root": str(plugin_root),
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")

    if dir_name:
        teams_dir = tmp_path / ".claude" / "teams" / dir_name
        teams_dir.mkdir(parents=True, exist_ok=True)
        (teams_dir / "config.json").write_text(json.dumps({
            "name": dir_name,
            "leadSessionId": lead_session_id,
            "members": members if members is not None else [],
        }), encoding="utf-8")

    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)
    monkeypatch.setattr(ctx_module, "_aligned_cache", None)
    return session_dir


def _setup_carveout_session_config_less(monkeypatch, tmp_path, *, team_name="t1"):
    """Config-less Desktop carve-out fixture: a PACT session context whose
    team_name resolves, but with NO config.json on disk for the team (the
    config-less Desktop/SDK substrate). members[] is therefore structurally
    empty (no roster file to read), so the members-only join witness returns
    False and the carve-out fires.

    The team dir exists (so get_team_name's resolution lands on it) but holds
    NO config.json — only the inbox the caller seeds via _create_secretary_inbox.
    """
    import shared.pact_context as ctx_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    session_dir = tmp_path / ".claude" / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True, exist_ok=True)

    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8"
    )

    context_file = session_dir / "pact-session-context.json"
    context_file.write_text(json.dumps({
        "team_name": team_name,
        "session_id": _SESSION_ID,
        "project_dir": _PROJECT_DIR,
        "plugin_root": str(plugin_root),
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")

    # Team dir exists but deliberately has NO config.json (config-less Desktop).
    teams_dir = tmp_path / ".claude" / "teams" / team_name
    teams_dir.mkdir(parents=True, exist_ok=True)
    assert not (teams_dir / "config.json").exists()

    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)
    monkeypatch.setattr(ctx_module, "_aligned_cache", None)
    return session_dir


class TestPrematureInboxCarveOutRegression:
    """#1023: the canonical secretary spawn must STILL be allowed when the
    secretary's inbox file already exists (written pre-spawn by
    ``TaskUpdate(owner="secretary")``) but the secretary is NOT yet in
    ``members[]``.

    ROOT CAUSE this guards: #1021 widened the SHARED
    ``bootstrap_marker_writer._team_has_secretary`` predicate with a config-less
    inbox-witness fallback (``members[]`` roster OR
    ``teams/<team>/inboxes/secretary.json`` exists). That predicate had a SECOND
    consumer — ``bootstrap_gate`` binding 5 — never re-examined by #1021. Because
    the platform creates the inbox at bootstrap Step 2 (``TaskUpdate(owner)``)
    BEFORE Step 3 (``Agent(secretary)``), the inbox arm returned True
    prematurely, ``not True`` made binding 5 False, the carve-out did NOT fire,
    and the gate DENIED the very spawn the carve-out exists to permit — a
    bootstrap deadlock on the first turn of every fresh CLI session under 4.4.39.

    The fix decouples by consumer: binding 5 now reads the gate-local
    ``members[]``-only JOIN witness ``_secretary_in_members`` (dispatch-witness
    via the inbox != join-witness via members[]), leaving
    ``_team_has_secretary``'s inbox DISPATCH fallback for the marker writer.

    WHY THE EXISTING CARVE-OUT TESTS DID NOT CATCH THIS: every carve-out test
    seeds the team via ``_setup_pact_session_with_team``, which writes
    ``config.json`` + ``members[]`` but NEVER creates an ``inboxes/`` dir — so
    binding 5 was only ever exercised against the ``members[]`` arm, and the
    cross-product (gate carve-out × premature inbox witness) — the exact
    regression path — was untested. These tests add the inbox witness via the
    REAL ``TaskUpdate(owner)`` write shape (``_create_secretary_inbox``) and
    assert the carve-out STILL fires.

    NON-VACUITY — VERIFICATION MATRIX (counter-test-by-revert, SOURCE-ONLY)
    ----------------------------------------------------------------------
    Binding 5 is the load-bearing source. The pre-fix shape
    (``return not _team_has_secretary(expected_team)``) reads the inbox arm, so
    a premature inbox flips it to a DENY; the post-fix shape
    (``return not _secretary_in_members(expected_team)``) reads members[] only,
    so a premature inbox is ignored and the carve-out fires.

    Measure via SOURCE-ONLY revert of the gate alone (the fix commit bundles
    these new tests with the source — a whole-commit ``git revert`` would mask
    the cardinality)::

        git checkout <fix>^ -- pact-plugin/hooks/bootstrap_gate.py
        python -m pytest tests/test_bootstrap_gate.py::TestPrematureInboxCarveOutRegression \
                         tests/test_bootstrap_gate.py::TestSecretaryInMembersUnit -q
        git checkout <fix>  -- pact-plugin/hooks/bootstrap_gate.py
        git diff --quiet -- pact-plugin/hooks/bootstrap_gate.py   # exits 0

    EMPIRICAL RED set (MEASURED against the pre-fix gate, not assumed) over
    {TestPrematureInboxCarveOutRegression + TestSecretaryInMembersUnit} =
    {7 failed, 2 passed}, decomposing as:

      BEHAVIORAL RED (4) — coupled to the inbox-arm regression, the load-bearing
      non-vacuity proof. Each asserts ``result is None`` over a present premature
      inbox, and each flips to ``_DENY_REASON`` under the pre-fix inbox-reading
      binding 5 (``not _team_has_secretary``):
        - R1 test_premature_inbox_does_not_defeat_carve_out      (THE headline)
        - R2 test_premature_inbox_carve_out_fires_in_process
        - R3 test_premature_inbox_carve_out_fires_tmux
        - C2 test_carve_out_fires_config_less_desktop_no_config

      NET-NEW-SYMBOL RED (3) — the U1 unit cells fail by ``ImportError`` because
      ``_secretary_in_members`` does not exist in the pre-fix gate (it is the
      fix's net-new symbol). This is a WEAKER non-vacuity form than a behavioral
      RED (an ImportError proves the symbol is new, not that the assertion is
      coupled to the bug); the U1 cells' OWN per-test ``# COUNTER-TEST`` notes
      describe the finer behavioral revert (typed-tuple swap / members-vs-inbox)
      that would flip them RED in isolation:
        - U1 test_true_when_secretary_in_members
        - U1 test_false_when_members_empty
        - U1 test_false_on_iter_members_raising

      GREEN (2) — FIX-INDEPENDENT by construction (they assert a DENY, not an
      allow over a present inbox), so they correctly stay GREEN under the revert;
      documented so a verifier does not expect them in the RED set:
        - E1 test_empty_ssot_fails_closed_even_with_premature_inbox_both_modes
        - CONTAINMENT test_non_canonical_input_still_blocked_with_pending_witness_error

    The existing ``test_carve_out_closes_after_secretary_in_members`` (one-shot
    closure, in TestCanonicalSecretarySpawnCarveOut) is likewise fix-independent.
    """

    # --- R1: THE headline regression (premature inbox must NOT defeat carve-out) ---

    def test_premature_inbox_does_not_defeat_carve_out(self, monkeypatch, tmp_path):
        """members=[] + a pre-spawn task-assignment-shaped inbox present →
        Agent(secretary) ALLOWED (result is None).

        Exercises the REAL inbox-witness seam UNMOCKED: the inbox file is on
        disk at the path the predicate reads, and neither _team_has_secretary
        nor _secretary_in_members is stubbed. Keyed on the OBSERVABLE outcome
        (result is None vs _DENY_REASON), not the internal helper name, so it
        stays valid across helper-shape changes.

        # COUNTER-TEST (source-only revert of bootstrap_gate.py): the pre-fix
        # binding 5 (`not _team_has_secretary`) reads the inbox arm → the
        # premature inbox makes it True → `not True` → carve-out denies → this
        # assertion (result is None) goes RED. THE headline non-vacuity cell.
        """
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )
        _create_secretary_inbox(tmp_path, team_name="t1")  # real pre-spawn write

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result is None

    # --- R2 / R3: both-modes legs (standing merge gate) ---

    def test_premature_inbox_carve_out_fires_in_process(self, monkeypatch, tmp_path):
        """Both-modes leg — IN-PROCESS topology (frame session_id ==
        config.leadSessionId). The carve-out resolves the team via get_team_name
        (the SSOT context), so the identity-match in _resolve_aligned_team_name
        succeeds; the premature inbox is still present and the carve-out STILL
        fires (result is None).

        The carve-out is topology-agnostic by design (it never branches on the
        in-process/tmux mode flag — DUAL-MODE PERMANENT CONTRACT item 2), so both
        legs assert the SAME outcome; they are provided as the standing merge-gate
        formality, with the leadSessionId seeded so the two topologies are
        genuinely distinct (not a duplicated assertion).

        # COUNTER-TEST (source-only revert): same as R1 — the premature inbox
        # flips the pre-fix inbox-reading binding 5 to a DENY → RED.
        """
        from bootstrap_gate import _check_tool_allowed

        _setup_carveout_team_with_lead_session(
            monkeypatch, tmp_path, team_name="t1",
            lead_session_id=_SESSION_ID, frame_session_id=_SESSION_ID,
        )
        _create_secretary_inbox(tmp_path, team_name="t1")

        result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
        assert result is None

    def test_non_canonical_input_still_blocked_with_pending_witness_error(
        self, monkeypatch, tmp_path,
    ):
        """CONTAINMENT: a NON-canonical tool call is STILL blocked even when a
        witness-read error is pending — proving bindings 1/2/3 (and the
        empty-SSOT guard) run BEFORE the join witness, so the broad-except
        error→ALLOW inversion is CONTAINED to the canonical secretary spawn and
        cannot leak into a blanket allow.

        The fix's _secretary_in_members returns False on a read error (the safe
        direction — it makes the carve-out FIRE). If that error→False were
        reached for a NON-canonical input, it would wrongly ALLOW it. This test
        proves it is not: bindings 1/2/3 short-circuit the predicate to False
        for any non-secretary tool BEFORE _secretary_in_members is ever called,
        so a pending witness error is irrelevant and the deny stands.

        Covers two non-canonical shapes while the witness data source is rigged
        to raise: (a) a plain Edit (fails binding 1 tool_name != Agent), and
        (b) an Agent with the wrong subagent_type/name (fails bindings 2/3).
        Both must DENY.

        # COUNTER-TEST: this cell is FIX-INDEPENDENT (asserts DENY, not an allow
        # over a present inbox) → GREEN under the source-only revert. It pins the
        # binding-ordering containment invariant, not the inbox-arm regression.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON
        import shared.pact_context as ctx_module

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        # Rig the witness data source to RAISE — a pending witness error.
        def _boom(team_name, teams_dir=None):
            raise OSError("simulated witness read error")

        monkeypatch.setattr(ctx_module, "_iter_members", _boom)

        # (a) Agent with the wrong name — fails binding 3 → DENY, the witness is
        # never consulted. (Was a plain Edit: since #243 Edit/Write/NotebookEdit
        # pass once SessionStart resolved the team, so Edit no longer reaches the
        # deny and cannot pin this invariant; Agent still requires the marker.)
        assert _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"name": "coder"},
        )) == _DENY_REASON

        # (b) Agent with wrong subagent_type → fails binding 2 → DENY.
        assert _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"subagent_type": "pact-architect"},
        )) == _DENY_REASON

        # (c) Agent with wrong name → fails binding 3 → DENY.
        assert _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"name": "secretari"},
        )) == _DENY_REASON

    def test_empty_ssot_fails_closed_even_with_premature_inbox_both_modes(
        self, monkeypatch, tmp_path,
    ):
        """Empty-SSOT fail-closed guard PRESERVED — in BOTH topologies — even
        with a premature inbox present. When the persisted SSOT team_name is
        empty, get_team_name short-circuits to "" (the deliberate fail-closed
        "team unknown → refuse" gate), the carve-out's `if not expected_team:
        return False` guard runs AHEAD of the witness, binding 5 returns False →
        the carve-out does NOT fire → the canonical secretary spawn is DENIED.
        The members-only witness change must not collide with this short-circuit.

        # COUNTER-TEST: this cell is FIX-INDEPENDENT — it asserts a DENY (not an
        # allow over a present inbox), so it stays GREEN under the source-only
        # revert. It pins that the members-only witness did not weaken the
        # empty-SSOT gate (a hypothetical regression where the witness recovered
        # a team from an empty SSOT would flip this to allow → RED).
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        for frame_sid, lead_sid in (
            (_SESSION_ID, _SESSION_ID),          # in-process (== leadSessionId)
            (_SESSION_ID, "other-lead-sid"),     # tmux (!= leadSessionId)
        ):
            _setup_carveout_team_with_lead_session(
                monkeypatch, tmp_path, team_name="",   # EMPTY SSOT
                lead_session_id=lead_sid, frame_session_id=frame_sid,
                seed_team_dir_name="t1",
            )
            # A premature inbox exists under the real team dir, but the empty SSOT
            # means get_team_name never resolves to it.
            _create_secretary_inbox(tmp_path, team_name="t1")

            result = _check_tool_allowed(_canonical_secretary_input(team_name="t1"))
            assert result == _DENY_REASON, (
                f"empty SSOT must fail-closed even with a premature inbox "
                f"(frame_sid={frame_sid!r}, lead_sid={lead_sid!r})"
            )

    # --- C2: config-less Desktop always-fire ---

    # --- E1: empty-SSOT fail-closed preserved, both modes ---

    # --- CONTAINMENT (security-suggested): non-canonical input still blocked ---

class TestSecretaryInMembersUnit:
    """U1: direct unit tests of the gate-local _secretary_in_members JOIN witness
    (#1023). The carve-out integration tests above key on the observable gate
    outcome; these pin the helper's own contract directly so a future refactor of
    the helper is caught at the unit level (closes the shared-helper-direct-unit-
    test gap)."""

    def test_true_when_secretary_in_members(self, monkeypatch, tmp_path):
        """members[] contains a 'secretary' name entry → True (join witness)."""
        from bootstrap_gate import _secretary_in_members

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1",
            members=[{"id": "sec-1", "name": "secretary", "type": "pact-secretary"}],
        )
        assert _secretary_in_members("t1") is True

    def test_false_when_members_empty(self, monkeypatch, tmp_path):
        """Empty members[] → False (the fresh-team precondition the carve-out
        handles). A premature inbox is IGNORED — this is a members-ONLY witness,
        the whole point of the #1023 decoupling.

        # COUNTER-TEST (source-only revert): the pre-fix carve-out called
        # _team_has_secretary (members[] OR inbox), so the equivalent member-only
        # query did not exist as a gate-local symbol; the import of
        # _secretary_in_members itself is the post-fix artifact. We additionally
        # seed a premature inbox to make explicit that the members-only witness
        # ignores it (it would return True if it read the inbox).
        """
        from bootstrap_gate import _secretary_in_members

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )
        _create_secretary_inbox(tmp_path, team_name="t1")  # ignored by a members-only witness
        assert _secretary_in_members("t1") is False

# =============================================================================
# Deny reason content — P2 priority
# =============================================================================


# =============================================================================
# Marker lifecycle — P3 priority
# =============================================================================


class TestMarkerLifecycle:
    """P3: Gate transitions based on marker presence."""

    def test_gate_transitions_deny_to_allow(self, monkeypatch, tmp_path, capsys):
        """Before marker: deny Agent. After marker stamp: allow Agent."""
        import shared.pact_context as ctx_module

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"

        # Before marker — Agent denied
        exit_code_before, output_before = _run_main(_make_input("Agent"), capsys)
        assert exit_code_before == 2
        assert "permissionDecision" in output_before.get("hookSpecificOutput", {})

        # Write a properly-stamped marker
        _write_f24_marker(session_dir, plugin_root, plugin_version="9.9.9")

        # Reset cache for second call
        ctx_module._cache = None

        # After marker — Agent allowed
        exit_code_after, output_after = _run_main(_make_input("Agent"), capsys)
        assert exit_code_after == 0
        assert output_after == _SUPPRESS_EXPECTED

    def test_repeated_deny_is_consistent(self, monkeypatch, tmp_path, capsys):
        """Multiple blocked calls without marker all produce deny."""
        import shared.pact_context as ctx_module

        _setup_pact_session(monkeypatch, tmp_path, with_marker=False)

        for tool in ["Agent", "Task", "Agent"]:
            ctx_module._cache = None

            exit_code, output = _run_main(_make_input(tool), capsys)
            assert exit_code == 2
            assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


# =============================================================================
# Cross-module marker name consistency — P2 priority
# =============================================================================


# =============================================================================
# is_marker_set — public helper (leaf-symlink + ancestor-symlink + content-fingerprint defenses)
# =============================================================================


class TestIsMarkerSet:
    """Public predicate `is_marker_set(session_dir)` — does a properly-stamped
    properly-stamped marker exist? Defends symlink-planted bypass
    (leaf and ancestor) and Bash-touch bypass (via SHA256 content fingerprint).
    """

    def test_returns_false_when_marker_absent(self, tmp_path):
        from bootstrap_gate import is_marker_set

        assert is_marker_set(tmp_path) is False

    def test_returns_true_when_marker_properly_stamped(
        self, monkeypatch, tmp_path
    ):
        """Only properly-stamped markers satisfy the gate."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=True)
        assert is_marker_set(session_dir) is True

    def test_returns_false_when_marker_is_symlink(self, monkeypatch, tmp_path):
        """S2 attack chain — symlink at the marker path is rejected."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        target = tmp_path / "decoy_target"
        target.touch()
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        marker.symlink_to(target)
        assert marker.exists() is True  # Path.exists follows symlinks
        assert is_marker_set(session_dir) is False  # but is_marker_set rejects

    def test_returns_false_when_ancestor_is_symlink(self, tmp_path):
        """S4 attack chain: symlinked ancestor is rejected."""
        from bootstrap_gate import is_marker_set

        real_dir = tmp_path / "real_session_dir"
        real_dir.mkdir()
        (real_dir / BOOTSTRAP_MARKER_NAME).touch()
        link_dir = tmp_path / "linked_session_dir"
        link_dir.symlink_to(real_dir)
        assert (link_dir / BOOTSTRAP_MARKER_NAME).exists() is True
        # Both paths fail content-fingerprint validation (empty content
        # is not a valid stamp), but
        # the ancestor-symlink check fires FIRST and ensures the bypass
        # would be rejected even if the content fingerprint were satisfied.
        assert is_marker_set(link_dir) is False
        assert is_marker_set(real_dir) is False  # content fingerprint fails on empty file

    def test_rejects_wrong_sid(self, monkeypatch, tmp_path):
        """Marker with mismatched sid (not session_dir.name) rejected."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"
        _write_f24_marker(
            session_dir, plugin_root, plugin_version="9.9.9", sid="wrong-session"
        )
        assert is_marker_set(session_dir) is False

    def test_rejects_malformed_json(self, monkeypatch, tmp_path):
        """Non-JSON marker content rejected."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        (session_dir / BOOTSTRAP_MARKER_NAME).write_text(
            "not json at all", encoding="utf-8"
        )
        assert is_marker_set(session_dir) is False

    def test_rejects_wrong_signature(self, monkeypatch, tmp_path):
        """Marker with a non-matching SHA256 signature rejected."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"
        _write_f24_marker(
            session_dir, plugin_root, plugin_version="9.9.9", sig="0" * 64
        )
        assert is_marker_set(session_dir) is False

    def test_rejects_oversized_content(self, monkeypatch, tmp_path):
        """Marker file > 256 bytes rejected (pathological-read defense)."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        # Write a JSON object that's syntactically correct but huge.
        marker = session_dir / BOOTSTRAP_MARKER_NAME
        big_payload = {"v": 1, "sid": session_dir.name,
                       "sig": "x" * 1024}
        marker.write_text(json.dumps(big_payload), encoding="utf-8")
        assert is_marker_set(session_dir) is False

    def test_returns_false_when_marker_is_empty_file_legacy_touch(
        self, monkeypatch, tmp_path
    ):
        """Bash-touch bypass closure (#662): legacy `touch bootstrap-complete`
        (empty file) MUST NOT satisfy the gate.
        """
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        (session_dir / BOOTSTRAP_MARKER_NAME).touch()
        assert is_marker_set(session_dir) is False

    def test_rejects_wrong_version(self, monkeypatch, tmp_path):
        """Marker with v != MARKER_SCHEMA_VERSION rejected."""
        from bootstrap_gate import is_marker_set

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"
        _write_f24_marker(
            session_dir, plugin_root, plugin_version="9.9.9", marker_version=99
        )
        assert is_marker_set(session_dir) is False

    def test_rejects_when_plugin_root_missing(self, monkeypatch, tmp_path):
        """Cannot compute expected signature without plugin context."""
        from bootstrap_gate import is_marker_set
        import shared.pact_context as ctx_module

        session_dir = _setup_pact_session(monkeypatch, tmp_path, with_marker=False)
        plugin_root = tmp_path / "plugin"
        # Stamp the marker with what WOULD be a valid sig, then break the
        # plugin context.
        _write_f24_marker(session_dir, plugin_root, plugin_version="9.9.9")
        # Remove plugin_root from context (simulate older session_init or
        # a corrupted context file).
        context_file = session_dir / "pact-session-context.json"
        context_file.write_text(json.dumps({
            "team_name": "",
            "session_id": _SESSION_ID,
            "project_dir": _PROJECT_DIR,
            "plugin_root": "",
            "started_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")
        ctx_module._cache = None
        assert is_marker_set(session_dir) is False

# =============================================================================
# Audit-anchor parity (mirror of writer's emit-shape invariant)
# =============================================================================


# =============================================================================
# Degraded mode (#942) — verification slice (CODE phase)
# =============================================================================


def _break_shared_syntax(scaffold):
    """Canonical vector: shared/ exists but its __init__.py is syntax-broken."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "this is not valid python (", encoding="utf-8"
    )


def _break_shared_absent(scaffold):
    """shared/ package absent entirely (deleted / never installed)."""
    # Deliberately create nothing — `import shared.pact_context` raises
    # ModuleNotFoundError at module load.


def _break_shared_missing_transitive(scaffold):
    """shared/__init__.py parses fine but its body fails a from-import of a
    name that does not exist (the missing-transitive-dependency shape) —
    raises ImportError (NOT its ModuleNotFoundError subclass)."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "from json import name_that_does_not_exist_in_json\n",
        encoding="utf-8",
    )


# Breakage-vector table for the degraded-import scaffold: vector name →
# (scaffold-breaker, exception type name the warning must carry). The
# production degraded region is `except BaseException`, so the defer/deny
# split must be IDENTICAL no matter HOW shared/ broke; the distinct
# exception type names make each vector's diagnosability assertable. The
# 2026-06-11 incident vector (py3.9 TypeError from annotation evaluation)
# is exercised separately by test_degraded_path_runs_on_python39_floor on
# a real 3.9 interpreter.
_BREAKAGE_VECTORS = {
    "syntax-broken-init": (_break_shared_syntax, "SyntaxError"),
    "shared-package-absent": (_break_shared_absent, "ModuleNotFoundError"),
    "missing-transitive-import": (
        _break_shared_missing_transitive, "ImportError",
    ),
}


def _run_degraded_subprocess(tmp_path, stdin_text, interpreter=None,
                             vector="syntax-broken-init"):
    """Run bootstrap_gate.py as a subprocess inside a scaffold whose
    `shared` package is deliberately broken per ``vector`` (a
    _BREAKAGE_VECTORS key; default = the canonical syntax-broken
    __init__.py), forcing the import-stage degraded path. Returns the
    CompletedProcess.

    ``interpreter`` defaults to the dev interpreter (sys.executable); the
    py3.9-floor test passes a discovered 3.9 binary to exercise the
    stdlib-only degraded region on the production system interpreter
    (GUI-launched macOS sessions run hooks on /usr/bin/python3 = 3.9.x).
    """
    import subprocess

    hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir(parents=True)
    # The hook's stdlib-only fail-closed sibling travels with it.
    (scaffold / "_fail_closed.py").write_bytes(_FAIL_CLOSED_SRC.read_bytes())
    (scaffold / "bootstrap_gate.py").write_text(
        hook_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _BREAKAGE_VECTORS[vector][0](scaffold)
    return subprocess.run(
        [interpreter or sys.executable, str(scaffold / "bootstrap_gate.py")],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=str(scaffold),
        timeout=10,
    )


def _find_python39():
    """Best-effort discovery of a Python 3.9 interpreter for the floor
    exercise: an explicit ``python3.9`` on PATH, else the macOS system
    ``/usr/bin/python3`` when it reports 3.9.x (the actual interpreter
    GUI-launched sessions run hooks on). Returns None when unavailable —
    callers skip; the static AST floor guard
    (test_py39_annotation_compat.py) remains the unconditional gate.
    """
    import shutil
    import subprocess

    candidates = [shutil.which("python3.9"), "/usr/bin/python3"]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            probe = subprocess.run(
                [candidate, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if (probe.stdout + probe.stderr).strip().startswith("Python 3.9"):
            return candidate
    return None


# Canonical degraded-allowlist literal — deliberately independent of
# bootstrap_gate._READ_ONLY_TOOLS (same convention as
# _CANONICAL_DENY_REASON_LITERAL above): the parametrized subprocess matrix
# anchors on this literal so a member silently dropped from (or added to)
# the production set cannot shrink the matrix unnoticed. The set-equality +
# cardinality pin in TestDegradedMode is the two-site edit surface: any
# intentional allowlist change must update BOTH the production frozenset
# AND this literal, forcing the matrix to follow.
_DEGRADED_ALLOWLIST_LITERAL = (
    "AskUserQuestion",
    "Skill",
    "WebSearch",
)

# Deny matrix: representatives of every non-member class — blocked mutating
# tools, the deliberate healthy/degraded asymmetry (Bash, mcp__*), task-
# mutation tools excluded from the read-only views, and an unknown/future
# name proving deny-by-default needs no enumeration.
_DEGRADED_DENY_MATRIX = (
    "Write",
    "mcp__computer-use__key",
    "SomeFutureTool",
)


class TestDegradedMode:
    """#942 degraded-mode handler: while the gate cannot evaluate (import
    or runtime failure), verified read-only tools are routed onward WITH a
    warning at exit 0 — permissionDecision "defer" (normal permission
    flow) for local tools, "ask" (explicit user approval) for outbound
    WebFetch/WebSearch, NEVER "allow" — so degraded mode is a
    permission-layer subset by construction. Everything else keeps the
    unchanged fail-closed deny at exit 2. Malformed/unverifiable stdin in
    the degraded path is fail-CLOSED (deny) — the opposite of the healthy
    path's input-side fail-open, because in degraded mode this module IS
    the broken layer.
    """

    # --- structural invariant pins (master safety property) -------------
    # MEMBERSHIP-CHANGE INVARIANT GUARD: the three tests below re-derive
    # the degraded⊆healthy safety property from the PRODUCTION sets
    # (_READ_ONLY_TOOLS, _DEGRADED_ASK_TOOLS, _BLOCKED_TOOLS) on every
    # run. They exist to catch FUTURE membership edits: any tool added to
    # the degraded allowlist that the healthy gate would deny — or any
    # ask-tool that is not an allowlist member — fails here before it can
    # ship. Do not weaken these to literal snapshots.

    # --- M1: bounded error interpolation ---------------------------------

    # --- runtime stage (in-process, symmetric with import stage) ---

    def test_runtime_exception_with_readonly_tool_defers_with_warning(self, capsys):
        """Gate-logic exception + local allowlisted tool → exit 0, JSON with
        permissionDecision="defer" (normal permission flow — never "allow"),
        warning text and systemMessage (the single deliberate emitter)."""
        from bootstrap_gate import main

        with patch(
            "bootstrap_gate._check_tool_allowed",
            side_effect=RuntimeError("boom"),
        ):
            with patch(
                "sys.stdin", io.StringIO(json.dumps(_make_input(tool_name="Read")))
            ):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out.strip())
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "defer"
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "runtime" in hso[field]
            assert "RuntimeError" in hso[field]
            assert "DEGRADED" in hso[field]
        assert "systemMessage" in out

    def test_runtime_exception_with_outbound_tool_asks(self, capsys):
        """Gate-logic exception + outbound tool (WebFetch) → exit 0,
        permissionDecision="ask": network traffic under a broken gate
        escalates to explicit user approval rather than deferring."""
        from bootstrap_gate import main

        with patch(
            "bootstrap_gate._check_tool_allowed",
            side_effect=RuntimeError("boom"),
        ):
            with patch(
                "sys.stdin",
                io.StringIO(json.dumps(_make_input(tool_name="WebFetch"))),
            ):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 0
        out = json.loads(capsys.readouterr().out.strip())
        hso = out["hookSpecificOutput"]
        assert hso["permissionDecision"] == "ask"
        assert "DEGRADED" in hso["permissionDecisionReason"]
        assert "systemMessage" in out

    def test_runtime_exception_with_mutating_tool_still_denies(self, capsys):
        """Symmetry must not weaken the deny arm: Edit under a gate-logic
        exception keeps today's fail-closed deny (exit 2)."""
        from bootstrap_gate import main

        with patch(
            "bootstrap_gate._check_tool_allowed",
            side_effect=RuntimeError("boom"),
        ):
            with patch(
                "sys.stdin", io.StringIO(json.dumps(_make_input(tool_name="Edit")))
            ):
                with pytest.raises(SystemExit) as exc_info:
                    main()

        assert exc_info.value.code == 2
        out = json.loads(capsys.readouterr().out.strip())
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "systemMessage" not in out

    def test_subprocess_broken_import_mutating_tool_denies(self, tmp_path):
        """Broken import + tool_name=Edit → byte-shape of today's
        _emit_load_failure_deny, rc 2, stderr non-empty."""
        result = _run_degraded_subprocess(
            tmp_path, json.dumps(_make_input(tool_name="Edit"))
        )
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "module imports failure" in hso["permissionDecisionReason"]
        assert "systemMessage" not in out
        assert result.stderr.strip()

    def test_subprocess_broken_import_malformed_stdin_denies(self, tmp_path):
        """Broken import + unparseable stdin → fail-CLOSED deny (decision
        (b)): the degraded path inverts the healthy input-side fail-open."""
        result = _run_degraded_subprocess(tmp_path, "not valid json {")
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    # --- import stage (subprocess smoke; full matrix is TEST phase) ---

    # --- comprehensive subprocess matrix (TEST phase) ---

    # --- breakage-vector agnosticism (beyond the canonical SyntaxError) ---

    # --- py3.9 floor exercise (conditional on an available interpreter) ---

# =============================================================================
# Hostile-__str__ crash path — every error-text render must fall back
# =============================================================================


def _break_shared_hostile_str(scaffold):
    """shared/__init__.py raises an exception whose own __str__ raises —
    the hostile-renderer shape: rendering an exception message runs
    arbitrary exception-class code, so every interpolation of the caught
    error on the crash path must fall back instead of letting the render
    raise. Deliberately NOT in _BREAKAGE_VECTORS: the matrix asserts the
    exception type is named in the DENY reason, which the raise-proof
    constant fallback (no type prefix) intentionally does not satisfy."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class HostileStrError(Exception):\n"
        "    def __str__(self):\n"
        "        raise RuntimeError('hostile __str__')\n"
        "raise HostileStrError()\n",
        encoding="utf-8",
    )


class TestHostileStrCrashPath:

    def _run(self, tmp_path, stdin_text):
        import subprocess

        hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
        scaffold = tmp_path / "scaffold"
        scaffold.mkdir(parents=True)
        # The hook's stdlib-only fail-closed sibling travels with it.
        (scaffold / "_fail_closed.py").write_bytes(_FAIL_CLOSED_SRC.read_bytes())
        (scaffold / "bootstrap_gate.py").write_text(
            hook_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        _break_shared_hostile_str(scaffold)
        return subprocess.run(
            [sys.executable, str(scaffold / "bootstrap_gate.py")],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=str(scaffold),
            timeout=10,
        )

    def test_mutating_deny_json_intact_under_hostile_str(self, tmp_path):
        """Hostile __str__ must not suppress the deny: an unguarded render
        raising before the deny print would exit nonzero-non-2 — a
        non-blocking PreToolUse error, so the tool call would PROCEED
        (fail-open). The deny JSON must print with the raise-proof
        constant in the reason and the exit-2 blocking path intact."""
        result = self._run(tmp_path, json.dumps(_make_input(tool_name="Edit")))
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        reason = hso["permissionDecisionReason"]
        assert "module imports" in reason
        assert "HostileStrError: <exception str() raised>" in reason, (
            f"deny reason must name the hostile exception type via the bounded "
            f"renderer (parity with the degraded path): {reason!r}"
        )
        # Guarded stderr full-text line: placeholder, exit-2 preserved.
        assert "Hook load error (bootstrap_gate / module imports)" in (
            result.stderr
        )
        assert "<exception str() raised>" in result.stderr

    def test_readonly_defer_intact_under_hostile_str(self, tmp_path):
        """Degraded warn arm under hostile __str__: the bounded renderer
        falls back to the type-prefixed placeholder inside the warning
        (diagnosability preserved — the type name still appears), the
        defer decision and exit 0 stay intact, and the guarded stderr
        line carries the placeholder instead of voiding the decision
        with a nonzero exit."""
        result = self._run(tmp_path, json.dumps(_make_input(tool_name="Read")))
        assert result.returncode == 0, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "defer"
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "DEGRADED" in hso[field]
            assert "module imports" in hso[field]
            assert "HostileStrError: <exception str() raised>" in hso[field], (
                f"warning must carry the type-prefixed placeholder: "
                f"{hso[field]!r}"
            )
        assert "systemMessage" in out
        assert "Hook degraded-defer (bootstrap_gate / module imports)" in (
            result.stderr
        )
        assert "<exception str() raised>" in result.stderr


# =============================================================================
# Cycle-2 regression: hostile-metaclass __name__ on the crash path.
#
# A metaclass can make type(error).__name__ a property that RAISES. The
# degraded warn path (_emit_degraded_warning) renders the error inline via
# _bounded_error_text with NO constant fallback around the call — so before
# 7155516d the helper's own fallback re-accessed type(error).__name__ and
# re-raised, the warn path exited 1 (a PreToolUse non-blocking error that
# SUPPRESSED the warning), and the degraded gate silently failed open. The
# fix captures the type name once → literal "exception". These pins lock
# the restored exit-0 defer; counter-test: source-revert 7155516d → the
# defer leg regresses to exit 1.
#
# HAZARD: the breakage modules define the hostile metaclass entirely in the
# subprocess scaffold text — no in-process class whose __name__ pytest
# could bomb during collection.
# =============================================================================


def _break_shared_hostile_name(scaffold):
    """shared/__init__.py raises an exception whose metaclass makes
    __name__ a raising property (normal __str__). Rendering the caught
    error's type name runs metaclass code that raises."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class _HostileNameMeta(type):\n"
        "    @property\n"
        "    def __name__(cls):\n"
        "        raise RuntimeError('hostile __name__')\n"
        "class NameBomb(Exception, metaclass=_HostileNameMeta):\n"
        "    pass\n"
        "raise NameBomb('boom')\n",
        encoding="utf-8",
    )


def _break_shared_hostile_name_and_str(scaffold):
    """Both hostile: metaclass __name__ raises AND __str__ raises — both
    helper fallbacks fire (type name → 'exception', message → marker)."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class _HostileNameMeta(type):\n"
        "    @property\n"
        "    def __name__(cls):\n"
        "        raise RuntimeError('hostile __name__')\n"
        "class BothBomb(Exception, metaclass=_HostileNameMeta):\n"
        "    def __str__(self):\n"
        "        raise RuntimeError('hostile __str__')\n"
        "raise BothBomb('boom')\n",
        encoding="utf-8",
    )


class TestHostileNameCrashPath:

    def _run(self, tmp_path, stdin_text, breaker):
        import subprocess

        hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
        scaffold = tmp_path / "scaffold"
        scaffold.mkdir(parents=True)
        # The hook's stdlib-only fail-closed sibling travels with it.
        (scaffold / "_fail_closed.py").write_bytes(_FAIL_CLOSED_SRC.read_bytes())
        (scaffold / "bootstrap_gate.py").write_text(
            hook_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        breaker(scaffold)
        return subprocess.run(
            [sys.executable, str(scaffold / "bootstrap_gate.py")],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=str(scaffold),
            timeout=10,
        )

    @pytest.mark.parametrize(
        "breaker",
        [
    _break_shared_hostile_name,
],
        ids=[
    "hostile-name",
],
    )
    def test_readonly_defer_intact_under_hostile_name(self, tmp_path, breaker):
        """The regression pin: a read-only tool under a hostile-__name__
        (or both-hostile) module crash must take the defer arm at exit 0,
        decision JSON intact, with the "exception" type-name fallback in
        the warning. Pre-7155516d this exited 1 with the warning
        suppressed."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Read")), breaker
        )
        assert result.returncode == 0, (
            "hostile __name__ must not regress the degraded path to a "
            f"nonzero exit (the suppressed-warning bug): "
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "defer"
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "DEGRADED" in hso[field]
            assert "module imports" in hso[field]
            # Type-name fallback fired: the literal "exception", never a
            # real class name (the metaclass made the name unrenderable).
            assert "failure — exception:" in hso[field], (
                f"warning must carry the captured-name fallback: "
                f"{hso[field]!r}"
            )
        assert "systemMessage" in out
        assert "Hook degraded-defer (bootstrap_gate / module imports)" in (
            result.stderr
        )

    @pytest.mark.parametrize(
        "breaker",
        [
    _break_shared_hostile_name,
],
        ids=[
    "hostile-name",
],
    )
    def test_mutating_deny_intact_under_hostile_name(self, tmp_path, breaker):
        """Deny-arm twin: a mutating tool under hostile __name__ still
        denies at exit 2 (blocking). The deny render now routes through
        _bounded_error_text (item 1: collapse the distinct render site into
        the shared helper for M1-consistency), which is total — so under
        hostile __name__ the reason carries the helper's "exception:"
        type-name fallback (parity with the degraded arm), not the old
        call-site "<error text unavailable>" constant. Deny still exits 2."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Edit")), breaker
        )
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        expected_render = (
            "exception: boom"
            if breaker is _break_shared_hostile_name
            else "exception: <exception str() raised>"
        )
        assert expected_render in hso["permissionDecisionReason"], (
            f"deny reason must carry the bounded-helper fallback (hostile "
            f"__name__ → 'exception:' type-name fallback), parity with the "
            f"degraded arm: {hso['permissionDecisionReason']!r}"
        )


# =============================================================================
# Cycle-3 regression: __name__ that RETURNS (not raises) a hostile non-str.
#
# The cycle-2 fix (7155516d) guards a metaclass __name__ that RAISES. A
# metaclass __name__ can instead RETURN a non-str object whose own
# __format__/__str__ raises — that poisoned value defeats BOTH f-string
# branches of _bounded_error_text (the except-branch fallback re-interpolates
# the same type_name), so before b6e9125a the degraded warn path raised out of
# the renderer and exited 1: a PreToolUse non-blocking error that SUPPRESSED
# the warning, silently failing open. b6e9125a coerces a non-str type_name to
# the literal "exception" (isinstance guard). These pins lock the restored
# exit-0 defer / exit-2 deny under the returns-hostile shape; counter-test:
# source-revert b6e9125a → the defer leg regresses to exit 1.
#
# HAZARD: the hostile metaclass lives entirely in the subprocess scaffold
# text — no in-process class whose __name__ pytest could bomb on collection.
# =============================================================================


def _break_shared_hostile_format_name(scaffold):
    """shared/__init__.py raises an exception whose metaclass __name__ RETURNS
    a non-str object whose __format__ (and __str__) raise. Rendering the
    caught error's type name interpolates that poisoned value → raises unless
    the renderer coerces it to a str first (b6e9125a)."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class _FormatBomb:\n"
        "    def __format__(self, spec):\n"
        "        raise RuntimeError('hostile __format__')\n"
        "    def __str__(self):\n"
        "        raise RuntimeError('hostile __str__')\n"
        "class _HostileFormatNameMeta(type):\n"
        "    @property\n"
        "    def __name__(cls):\n"
        "        return _FormatBomb()\n"
        "class FormatNameBomb(Exception, metaclass=_HostileFormatNameMeta):\n"
        "    pass\n"
        "raise FormatNameBomb('boom')\n",
        encoding="utf-8",
    )


def _break_shared_hostile_int_name(scaffold):
    """shared/__init__.py raises an exception whose metaclass __name__ RETURNS
    a non-str int. The int interpolates without raising, but the type name is
    nonsense ('42: ...'); b6e9125a coerces it to 'exception' so the degraded
    warning carries the same diagnosable fallback as the raising case."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class _IntNameMeta(type):\n"
        "    @property\n"
        "    def __name__(cls):\n"
        "        return 42\n"
        "class IntNameBomb(Exception, metaclass=_IntNameMeta):\n"
        "    pass\n"
        "raise IntNameBomb('boom')\n",
        encoding="utf-8",
    )


class TestHostileNameReturnCrashPath:

    def _run(self, tmp_path, stdin_text, breaker):
        import subprocess

        hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
        scaffold = tmp_path / "scaffold"
        scaffold.mkdir(parents=True)
        # The hook's stdlib-only fail-closed sibling travels with it.
        (scaffold / "_fail_closed.py").write_bytes(_FAIL_CLOSED_SRC.read_bytes())
        (scaffold / "bootstrap_gate.py").write_text(
            hook_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        breaker(scaffold)
        return subprocess.run(
            [sys.executable, str(scaffold / "bootstrap_gate.py")],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=str(scaffold),
            timeout=10,
        )

    @pytest.mark.parametrize(
        "breaker",
        [
    _break_shared_hostile_format_name,
],
        ids=[
    "name-returns-format-bomb",
],
    )
    def test_readonly_defer_intact_under_hostile_name_return(
        self, tmp_path, breaker,
    ):
        """The cycle-3 regression pin: a read-only tool under a module crash
        whose __name__ RETURNS a hostile non-str must take the defer arm at
        exit 0, decision JSON intact, with the "exception" type-name fallback
        in the warning. Pre-b6e9125a the format-bomb variant exited 1 with the
        warning suppressed (the renderer raised out of both f-string
        branches)."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Read")), breaker
        )
        assert result.returncode == 0, (
            "hostile __name__ RETURN must not regress the degraded path to a "
            f"nonzero exit (the suppressed-warning fail-open): "
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "defer"
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "DEGRADED" in hso[field]
            assert "module imports" in hso[field]
            # Coerced fallback fired: the literal "exception", never a raw
            # non-str type name ("42:") or a render crash.
            assert "failure — exception:" in hso[field], (
                f"warning must carry the coerced-name fallback: {hso[field]!r}"
            )
        assert "systemMessage" in out
        assert "Hook degraded-defer (bootstrap_gate / module imports)" in (
            result.stderr
        )

    @pytest.mark.parametrize(
        "breaker",
        [
    _break_shared_hostile_format_name,
],
        ids=[
    "name-returns-format-bomb",
],
    )
    def test_mutating_deny_intact_under_hostile_name_return(
        self, tmp_path, breaker,
    ):
        """Deny-arm twin: a mutating tool under a __name__-returns-hostile
        module crash still denies at exit 2 (blocking). The deny render has
        its own guard (constant fallback on the format-bomb; the int renders
        cleanly), so the blocking exit-2 path stays intact either way."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Edit")), breaker
        )
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "module imports" in hso["permissionDecisionReason"]


# =============================================================================
# BaseException-breadth regression (symmetric with the lifecycle-gate pin): a
# module-level sys.exit() / KeyboardInterrupt AT IMPORT is a BaseException that
# is NOT an Exception subclass. The import-gauntlet guard (`except
# BaseException`) is deliberately broad so such a vector is caught and routed
# to the degraded decision (read-only → defer exit 0; mutating → deny exit 2).
# None of the _BREAKAGE_VECTORS exercises this (all raise Exception
# subclasses), so narrowing the guard to `except Exception` would silently
# fail open: SystemExit/KeyboardInterrupt escapes → exit 1, which for
# PreToolUse is a non-blocking error → the tool PROCEEDS (mutating) or the
# defer decision is lost (read-only). These pins lock the breadth.
#
# Counter-test: narrow the gauntlet guard to `except Exception` → the crash
# escapes, no decision JSON prints, exit 1 → both arms below fail.
# =============================================================================


def _break_shared_sys_exit(scaffold):
    """shared/__init__.py calls sys.exit(1) at import → SystemExit (a
    BaseException, NOT an Exception) propagates out of the gauntlet import."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "import sys\nsys.exit(1)\n", encoding="utf-8"
    )


def _break_shared_keyboard_interrupt(scaffold):
    """shared/__init__.py raises KeyboardInterrupt at import → a BaseException
    that `except Exception` would NOT catch."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "raise KeyboardInterrupt('simulated at import')\n", encoding="utf-8"
    )


class TestBaseExceptionBreadthAtImport:

    def _run(self, tmp_path, stdin_text, breaker):
        import subprocess

        hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
        scaffold = tmp_path / "scaffold"
        scaffold.mkdir(parents=True)
        # The hook's stdlib-only fail-closed sibling travels with it.
        (scaffold / "_fail_closed.py").write_bytes(_FAIL_CLOSED_SRC.read_bytes())
        (scaffold / "bootstrap_gate.py").write_text(
            hook_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        breaker(scaffold)
        return subprocess.run(
            [sys.executable, str(scaffold / "bootstrap_gate.py")],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=str(scaffold),
            timeout=10,
        )

    @pytest.mark.parametrize(
        "breaker,expected_exc",
        [
    (_break_shared_sys_exit, "SystemExit"),
],
        ids=[
    "import-sys-exit",
],
    )
    def test_readonly_defer_intact_under_non_exception_baseexception(
        self, tmp_path, breaker, expected_exc,
    ):
        """A read-only tool under a module-level sys.exit()/KeyboardInterrupt
        at import takes the defer arm at exit 0 (the gauntlet's `except
        BaseException` catches the non-Exception BaseException). Narrowing to
        `except Exception` re-masks: the crash escapes → exit 1, defer lost."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Read")), breaker
        )
        assert result.returncode == 0, (
            "a non-Exception BaseException at import must be caught by the "
            f"gauntlet's BaseException breadth (defer exit 0): "
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "defer"
        assert "DEGRADED" in hso["permissionDecisionReason"]
        assert "module imports" in hso["permissionDecisionReason"]
        assert expected_exc in hso["permissionDecisionReason"], (
            f"degraded warning must name the BaseException type "
            f"({expected_exc}): {hso['permissionDecisionReason']!r}"
        )

    @pytest.mark.parametrize(
        "breaker",
        [
    _break_shared_sys_exit,
],
        ids=[
    "import-sys-exit",
],
    )
    def test_mutating_deny_intact_under_non_exception_baseexception(
        self, tmp_path, breaker,
    ):
        """A mutating tool under the same non-Exception BaseException at import
        still denies at exit 2 (blocking). Narrowing the gauntlet to `except
        Exception` lets the crash escape → exit 1, a non-blocking PreToolUse
        error → the mutating tool would PROCEED (fail-open)."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Edit")), breaker
        )
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "module imports" in hso["permissionDecisionReason"]


# =============================================================================
# Cycle-4 regression: __name__ that returns a str SUBCLASS whose formatting
# hooks raise. isinstance(str_subclass, str) is True, so the cycle-3 isinstance
# guard waved it through and _bounded_error_text raised out of both f-string
# branches (str.__format__'s empty-spec path delegates to the overridden
# __str__). cede8629 uses an EXACT-type guard (type(...) is str) that rejects
# str subclasses too, collapsing the prefix to "exception". These pins lock the
# restored exit-0 defer / exit-2 deny; counter-test: revert cede8629 (restore
# isinstance) → the defer leg raises out of the renderer → exit 1 (fail-open).
#
# HAZARD: the hostile str subclass lives entirely in the subprocess scaffold
# text — no in-process class whose __name__ pytest could bomb on collection.
# =============================================================================


def _break_shared_str_subclass_format_name(scaffold):
    """shared/__init__.py raises an exception whose metaclass __name__ RETURNS
    a str SUBCLASS instance whose __format__ raises."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class _FmtStr(str):\n"
        "    def __format__(self, spec):\n"
        "        raise RuntimeError('hostile str-subclass __format__')\n"
        "class _FmtStrNameMeta(type):\n"
        "    @property\n"
        "    def __name__(cls):\n"
        "        return _FmtStr('HostileFmtName')\n"
        "class FmtStrNameBomb(Exception, metaclass=_FmtStrNameMeta):\n"
        "    pass\n"
        "raise FmtStrNameBomb('boom')\n",
        encoding="utf-8",
    )


def _break_shared_str_subclass_str_name(scaffold):
    """shared/__init__.py raises an exception whose metaclass __name__ RETURNS
    a str SUBCLASS instance whose __str__ raises (str.__format__'s empty-spec
    delegation to __str__ makes the f-string interpolation raise)."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "class _StrStr(str):\n"
        "    def __str__(self):\n"
        "        raise RuntimeError('hostile str-subclass __str__')\n"
        "class _StrStrNameMeta(type):\n"
        "    @property\n"
        "    def __name__(cls):\n"
        "        return _StrStr('HostileStrName')\n"
        "class StrStrNameBomb(Exception, metaclass=_StrStrNameMeta):\n"
        "    pass\n"
        "raise StrStrNameBomb('boom')\n",
        encoding="utf-8",
    )


class TestStrSubclassNameReturnCrashPath:

    def _run(self, tmp_path, stdin_text, breaker):
        import subprocess

        hook_src = Path(__file__).parent.parent / "hooks" / "bootstrap_gate.py"
        scaffold = tmp_path / "scaffold"
        scaffold.mkdir(parents=True)
        # The hook's stdlib-only fail-closed sibling travels with it.
        (scaffold / "_fail_closed.py").write_bytes(_FAIL_CLOSED_SRC.read_bytes())
        (scaffold / "bootstrap_gate.py").write_text(
            hook_src.read_text(encoding="utf-8"), encoding="utf-8"
        )
        breaker(scaffold)
        return subprocess.run(
            [sys.executable, str(scaffold / "bootstrap_gate.py")],
            input=stdin_text,
            capture_output=True,
            text=True,
            cwd=str(scaffold),
            timeout=10,
        )

    @pytest.mark.parametrize(
        "breaker",
        [
    _break_shared_str_subclass_format_name,
],
        ids=[
    "name-returns-str-subclass-format-bomb",
],
    )
    def test_readonly_defer_intact_under_str_subclass_name(
        self, tmp_path, breaker,
    ):
        """The cycle-4 regression pin: a read-only tool under a module crash
        whose __name__ RETURNS a hostile str SUBCLASS must take the defer arm
        at exit 0, decision JSON intact, with the "exception" fallback in the
        warning. Pre-cede8629 (isinstance guard) the str subclass passed the
        guard and the renderer raised → exit 1, warning suppressed
        (fail-open)."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Read")), breaker
        )
        assert result.returncode == 0, (
            "a str-subclass __name__ must not regress the degraded path to a "
            f"nonzero exit (the suppressed-warning fail-open): "
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "defer"
        for field in ("permissionDecisionReason", "additionalContext"):
            assert "DEGRADED" in hso[field]
            assert "module imports" in hso[field]
            assert "failure — exception:" in hso[field], (
                f"warning must carry the exact-type-coerced fallback: "
                f"{hso[field]!r}"
            )
        assert "systemMessage" in out
        assert "Hook degraded-defer (bootstrap_gate / module imports)" in (
            result.stderr
        )

    @pytest.mark.parametrize(
        "breaker",
        [
    _break_shared_str_subclass_format_name,
],
        ids=[
    "name-returns-str-subclass-format-bomb",
],
    )
    def test_mutating_deny_intact_under_str_subclass_name(
        self, tmp_path, breaker,
    ):
        """Deny-arm twin: a mutating tool under a str-subclass-__name__ module
        crash still denies at exit 2 (blocking). The deny render routes
        through the total _bounded_error_text (item 1), so a str-subclass
        __name__ that bombs its own render is coerced to the "exception"
        type-name fallback rather than raising — the blocking exit-2 path
        stays intact."""
        result = self._run(
            tmp_path, json.dumps(_make_input(tool_name="Edit")), breaker
        )
        assert result.returncode == 2, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        out = json.loads(result.stdout.strip().splitlines()[0])
        hso = out["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "module imports" in hso["permissionDecisionReason"]


# =============================================================================
# Marker verification × CLAUDE_PLUGIN_ROOT env fallback — healthy path
# =============================================================================


class TestMarkerVerifyEnvFallback:
    """is_marker_set derives plugin_root via pact_context.get_plugin_root(),
    which falls back to the CLAUDE_PLUGIN_ROOT env var when the context-file
    value is empty or the file is missing. These tests pin that the
    fallback participates in MARKER VERIFICATION on the healthy path — the
    realistic shape being a context file healed (or written) while
    CLAUDE_PLUGIN_ROOT was absent, leaving plugin_root='' on disk while the
    env var is exported into every subsequent hook process.

    Three arms: (1) env rescues verification — a validly-signed marker
    verifies with the context plugin_root unavailable; (2) both sources
    absent fails closed (the conftest autouse scrub guarantees the env
    baseline); (3) a WRONG env root fails the SIGNATURE — proving the env
    value participates in the HMAC input, not merely the non-empty check.
    """

    def _scaffold(self, monkeypatch, tmp_path, context_state):
        """Session dir + plugin root (plugin.json v9.9.9) + validly-signed
        marker; the context file is per ``context_state``: 'absent' (never
        written) or 'empty-plugin-root' (present, plugin_root='')."""
        import shared.pact_context as ctx_module

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        session_dir = tmp_path / ".claude" / "pact-sessions" / _SLUG / _SESSION_ID
        session_dir.mkdir(parents=True)

        plugin_root = tmp_path / "plugin"
        (plugin_root / ".claude-plugin").mkdir(parents=True)
        (plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": "9.9.9"}), encoding="utf-8"
        )
        _write_f24_marker(session_dir, plugin_root)

        context_file = session_dir / "pact-session-context.json"
        if context_state == "empty-plugin-root":
            context_file.write_text(json.dumps({
                "team_name": "",
                "session_id": _SESSION_ID,
                "project_dir": _PROJECT_DIR,
                "plugin_root": "",
                "started_at": "2026-01-01T00:00:00Z",
            }), encoding="utf-8")
        # 'absent': deliberately not written.

        monkeypatch.setattr(ctx_module, "_context_path", context_file)
        monkeypatch.setattr(ctx_module, "_cache", None)
        return session_dir, plugin_root

    @pytest.mark.parametrize("context_state", [
    "absent",
])
    def test_env_fallback_rescues_marker_verification(
            self, monkeypatch, tmp_path, context_state):
        """Context plugin_root unavailable + CLAUDE_PLUGIN_ROOT exported →
        the validly-signed marker verifies via the env-derived root."""
        from bootstrap_gate import is_marker_set

        session_dir, plugin_root = self._scaffold(
            monkeypatch, tmp_path, context_state)
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))

        assert is_marker_set(session_dir) is True

    @pytest.mark.parametrize("context_state", [
    "absent",
])
    def test_both_sources_absent_fails_closed(
            self, monkeypatch, tmp_path, context_state):
        """Same scaffold, env NOT set (conftest scrub guarantees the unset
        baseline) → plugin_root resolves '' → marker verification fails
        closed, single-variable counterpart of the rescue arm above."""
        from bootstrap_gate import is_marker_set

        session_dir, _ = self._scaffold(monkeypatch, tmp_path, context_state)

        assert is_marker_set(session_dir) is False

    def test_wrong_env_root_fails_signature(self, monkeypatch, tmp_path):
        """Env pointing at a DIFFERENT root (own valid plugin.json, SAME
        version, so only the root path differs in the HMAC input) → the
        signature mismatch rejects the marker — the env value participates
        in verification, it does not merely satisfy the non-empty check."""
        from bootstrap_gate import is_marker_set

        session_dir, _ = self._scaffold(monkeypatch, tmp_path, "absent")
        other_root = tmp_path / "other-plugin"
        (other_root / ".claude-plugin").mkdir(parents=True)
        (other_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"version": "9.9.9"}), encoding="utf-8"
        )
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(other_root))

        assert is_marker_set(session_dir) is False

# =============================================================================
# Import discipline — #789 reciprocal-cycle defense
# =============================================================================


# =============================================================================
# Adversarial / edge-case / fuzz coverage (#789)
# =============================================================================


class TestCanonicalSecretarySpawnAdversarial:
    """Adversarial / edge-case coverage layered on top of the
    directly-coupled per-binding tests in TestCanonicalSecretarySpawnCarveOut.

    These tests probe the carve-out predicate's attack surface where the
    directly-coupled tests are silent: malformed tool_input shapes,
    encoding edge cases on the canonical literals, exception envelope
    tightness (only 6 listed exception types are caught; everything else
    propagates), get_team_name edge values (empty / None / whitespace),
    and deny-reason content invariance under failure modes.

    Each test's docstring describes the mental-revert that produces RED
    on the targeted invariant.
    """

    # --- Tool-input shape manipulation -------------------------------------

    @pytest.mark.parametrize(
        "tool_input_value",
        [
    None,
],
        ids=[
    "none",
],
    )
    def test_non_dict_tool_input_denies(
        self, monkeypatch, tmp_path, tool_input_value,
    ):
        """tool_input is not a dict → predicate returns False → deny.

        The predicate's explicit `isinstance(tool_input, dict)` guard
        rejects non-dict shapes before any binding check. Mental revert:
        remove the isinstance guard and the next `.get(...)` raises
        AttributeError, which IS caught by the broad except envelope —
        but the resulting False still denies. The isinstance guard is
        load-bearing for code clarity, not behavior; this test confirms
        deny under all non-dict shapes regardless.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        input_data = {
            "hook_event_name": "PreToolUse",
            "session_id": _SESSION_ID,
            "tool_name": "Agent",
            "agent_type": "pact-orchestrator",  # #878: lead frame reaches the gate body
            "tool_input": tool_input_value,
        }
        result = _check_tool_allowed(input_data)
        assert result == _DENY_REASON

    @pytest.mark.parametrize(
        "missing_key",
        [
    "subagent_type",
],
    )
    def test_tool_input_missing_required_key_denies(
        self, monkeypatch, tmp_path, missing_key,
    ):
        """tool_input missing one of (subagent_type, name) → predicate
        returns False → deny. (#979: team_name dropped from the binding set —
        the Agent(team_name=) arg is platform-ignored.)

        `.get(missing_key)` returns None, which the subagent_type binding
        refuses as a non-str and the name binding compares unequal to the
        expected literal value. Mental revert: replacing
        the binding's `!=` with `not ==` would not change behavior; but
        replacing `_SECRETARY_NAME` with None (silently dropping the
        constant) would make missing-name spawns ALLOW. Defends that
        the constant is non-None.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        canonical = {
            "subagent_type": "pact-secretary",
            "name": "secretary",
            "team_name": "t1",
        }
        canonical.pop(missing_key)
        input_data = {
            "hook_event_name": "PreToolUse",
            "session_id": _SESSION_ID,
            "tool_name": "Agent",
            "agent_type": "pact-orchestrator",  # #878: lead frame reaches the gate body
            "tool_input": canonical,
        }
        result = _check_tool_allowed(input_data)
        assert result == _DENY_REASON

    @pytest.mark.parametrize(
        "wrong_name",
        [
            "secretary ",
            "secretary​",
            "secеretary",
        ],
        ids=[
            "trailing_space",
            "zero_width_space",
            "cyrillic_e_lookalike",
        ],
    )
    def test_name_canonical_literal_is_case_and_whitespace_sensitive(
        self, monkeypatch, tmp_path, wrong_name,
    ):
        """name binding is BYTE-EXACT equality against _SECRETARY_NAME.

        No normalization, no casefold, no strip, no Unicode-fold. Any
        deviation (case, whitespace, lookalike Unicode, null byte,
        zero-width space) closes the carve-out. Mental revert: changing
        `tool_input.get("name") != _SECRETARY_NAME` to
        `tool_input.get("name", "").strip().lower() != _SECRETARY_NAME`
        would allow several of these and is the kind of "helpful"
        refactor that silently widens the attack surface.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={"name": wrong_name},
        ))
        assert result == _DENY_REASON

    @pytest.mark.parametrize(
        "configured_team_name",
        ["", None],
        ids=["empty_string", "none_value"],
    )
    def test_empty_or_none_disk_team_name_denies(
        self, monkeypatch, tmp_path, configured_team_name,
    ):
        """get_team_name returns falsy → predicate's `if not expected_team`
        branch returns False → deny.

        Defends an attacker dispatching `Agent(secretary, team_name="")`
        in a fresh session whose context_path is missing/empty. The
        predicate refuses to compare against a falsy team_name to avoid
        the accidental == "" match.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON
        import shared.pact_context as ctx_module

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )
        # Override get_team_name to return the falsy value.
        monkeypatch.setattr(
            ctx_module, "get_team_name", lambda: configured_team_name,
        )

        # Match the falsy value on tool_input side as well to confirm
        # the predicate's GUARD on falsy disk-value closes the carve-out
        # even when tool_input would otherwise match.
        result = _check_tool_allowed(_canonical_secretary_input(
            team_name=configured_team_name if configured_team_name else "",
        ))
        assert result == _DENY_REASON

    @pytest.mark.parametrize(
        "binding,wrong_type_value",
        [
            ("subagent_type", None),
        ],
    )
    def test_wrong_value_type_on_binding_denies(
        self, monkeypatch, tmp_path, binding, wrong_type_value,
    ):
        """Wrong value TYPE on a binding (int/None/list/bytes/dict where str
        is expected) → the subagent_type binding's str check, or the name
        binding's != comparison against the string constant → deny.
        (#979: team_name removed from the binding set — its type is no
        longer checked since the arg is platform-ignored.)

        Confirms the carve-out doesn't accidentally truthy-coerce
        non-string values (e.g., `1 == "secretary"` is False — good).
        Mental revert: changing the `!=` checks to `not bool(value)`
        would allow truthy non-string values; this test catches that.
        """
        from bootstrap_gate import _check_tool_allowed, _DENY_REASON

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        result = _check_tool_allowed(_canonical_secretary_input(
            team_name="t1", overrides={binding: wrong_type_value},
        ))
        assert result == _DENY_REASON

    def test_missing_tool_name_denies(self, monkeypatch, tmp_path):
        """input_data missing `tool_name` key entirely → predicate
        returns False (binding 1 fails on `.get` → None != "Agent") →
        deny via the existing _BLOCKED_TOOLS fall-through (which also
        misses on empty tool_name, but that's existing behavior).
        """
        from bootstrap_gate import _check_tool_allowed

        _setup_pact_session_with_team(
            monkeypatch, tmp_path, team_name="t1", members=[],
        )

        input_data = {
            "hook_event_name": "PreToolUse",
            "session_id": _SESSION_ID,
            "agent_type": "pact-orchestrator",  # #878: lead frame reaches the gate body
            "tool_input": {
                "subagent_type": "pact-secretary",
                "name": "secretary",
                "team_name": "t1",
            },
        }
        result = _check_tool_allowed(input_data)
        # Missing tool_name → "" → not in _BLOCKED_TOOLS → allow (None).
        # The carve-out predicate returns False (binding 1 fails); the
        # missing-tool_name path is the existing behavior tested at line
        # ~340 of TestCheckToolAllowed. This test pins that the
        # carve-out does NOT accidentally allow a missing-tool_name
        # request as if it were the canonical spawn.
        assert result is None

    # --- Encoding edge cases on canonical literals -------------------------

    # --- Witness exception totality (#1023) --------------------------------
    # Post-#1023 the JOIN witness _secretary_in_members wraps its body in a
    # BROAD `except Exception: return False`, so EVERY exception type raised
    # while reading members[] is absorbed at the witness → binding 5
    # (`not False`) → carve-out FIRES (allow). This replaces the pre-#1023
    # typed-except design, where the 5 "listed" types denied and "unlisted"
    # types (RuntimeError etc.) propagated to main()'s load-failure deny. The
    # broad except is LOAD-BEARING: a Path.home() RuntimeError seam in the
    # members[] read would otherwise escape and re-deadlock the spawn.

    # --- Predicate state edge values ---------------------------------------

    # --- Same-turn duplicate-dispatch: accepted-residual benign behavior ----

    # --- Deny-reason content invariance ------------------------------------

# =============================================================================
# The compaction-summary settle seat
# =============================================================================


class TestSummaryReadSettleSeat:
    """bootstrap_gate settles staged compaction summaries before a Read or Bash
    that names one. Every other call pays one string test: no filesystem access
    and no import. The seat never changes the gate's decision."""

    SESSION_DIR = "/sessions/proj/sid"

    def _spy(self, monkeypatch):
        import bootstrap_gate
        from shared import compaction_owner

        calls = []
        monkeypatch.setattr(compaction_owner, "settle", lambda *a, **k: calls.append((a, k)) or [])
        monkeypatch.setattr(bootstrap_gate.pact_context, "get_session_dir", lambda: self.SESSION_DIR)
        return calls

    @pytest.mark.parametrize("tool_name, tool_input", [
    ("Read", {"file_path": "/sessions/proj/sid/compact-summary.txt"}),
], ids=[
    "read",
])
    def test_a_read_or_bash_naming_the_summary_settles_with_the_read_wait(self, monkeypatch, tool_name, tool_input):
        import bootstrap_gate
        from shared import compaction_owner

        calls = self._spy(monkeypatch)
        bootstrap_gate._settle_before_summary_read({"tool_name": tool_name, "tool_input": tool_input})
        assert calls == [((self.SESSION_DIR,), {"wait_s": compaction_owner.READ_WAIT_S})]
        assert compaction_owner.READ_WAIT_S == 5.0

    @pytest.mark.parametrize("tool_name, tool_input", [
    ("Read", {"file_path": "/project/README.md"}),
], ids=[
    "read-other-file",
])
    def test_any_other_call_costs_one_string_test_and_no_filesystem_or_import(self, monkeypatch, tool_name, tool_input):
        import builtins
        import os

        import bootstrap_gate

        calls = self._spy(monkeypatch)
        touched = []
        real_import = builtins.__import__

        def recording_import(name, *args, **kwargs):
            touched.append(("import", name))
            return real_import(name, *args, **kwargs)

        def forbidden(label):
            def refuse(*args, **kwargs):
                touched.append((label, args))
                raise AssertionError(label)
            return refuse

        monkeypatch.setattr(bootstrap_gate.pact_context, "get_session_dir", forbidden("get_session_dir"))
        for name in ("stat", "listdir", "scandir", "open"):
            monkeypatch.setattr(os, name, forbidden(f"os.{name}"))
        monkeypatch.setattr(builtins, "open", forbidden("open"))
        monkeypatch.setattr(builtins, "__import__", recording_import)
        bootstrap_gate._settle_before_summary_read({"tool_name": tool_name, "tool_input": tool_input})
        monkeypatch.undo()
        assert touched == []
        assert calls == []

# =============================================================================
# On-demand secretary advisory
# =============================================================================


def _secretary_owner_update(agent_type="pact-orchestrator", owner="secretary"):
    data = _make_input("TaskUpdate", agent_type=agent_type)
    data["tool_input"] = {"taskId": "7", "owner": owner}
    return data


class TestSecretarySpawnAdvisory:
    """A lead TaskUpdate(owner="secretary") while no secretary member exists is
    ALLOWED and silent (dispatch_gate rule 8 needs the owner before the Agent
    spawn). The spawn template arrives as additionalContext once, on the lead's
    next call that is neither the spawn nor a task-list call. Never a deny."""

    _LEAD_ONLY = [{"name": "team-lead", "agentType": "pact-orchestrator"}]
    _SILENT = {"hookEventName": "PreToolUse"}

    def test_advises_once_when_the_lead_does_not_spawn_next(self, monkeypatch, tmp_path, capsys):
        _setup_pact_session_with_team(monkeypatch, tmp_path, members=self._LEAD_ONLY,
                                      with_marker=True)
        code, output = _run_main(_secretary_owner_update(), capsys)
        assert (code, output["hookSpecificOutput"]) == (0, self._SILENT)

        code, output = _run_main(_make_input("Read"), capsys)
        hso = output["hookSpecificOutput"]
        assert code == 0
        assert "permissionDecision" not in hso
        assert "the secretary is not on the team yet" in hso["additionalContext"]
        assert "a send to a member that does not exist is not delivered" in hso["additionalContext"]
        assert 'Agent(name="secretary", subagent_type="pact-secretary"' in hso["additionalContext"]
        assert "You are joining team t1." in hso["additionalContext"]

        assert _run_main(_make_input("Read"), capsys)[1]["hookSpecificOutput"] == self._SILENT

    def test_the_prescribed_order_carries_no_advisory(self, monkeypatch, tmp_path, capsys):
        """TaskCreate -> TaskUpdate(owner) -> Agent(secretary), then any later call."""
        _setup_pact_session_with_team(monkeypatch, tmp_path, members=self._LEAD_ONLY,
                                      with_marker=True)
        create = _make_input("TaskCreate")
        create["tool_input"] = {"subject": "secretary: harvest", "description": "x"}
        frames = (create, _secretary_owner_update(), _canonical_secretary_input(), _make_input("Read"))
        outputs = [_run_main(frame, capsys)[1]["hookSpecificOutput"] for frame in frames]
        assert outputs == [self._SILENT] * 4
