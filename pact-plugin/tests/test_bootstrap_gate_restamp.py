"""A marker stamped under an earlier plugin version is re-stamped at the gate.

A plugin root that is advanced in place (a release worktree) keeps its path and
changes its version, so the marker a session wrote under version X stops
verifying under version Y. Only the UserPromptSubmit writer re-stamped it, and a
teammate message does not fire that event, so an unattended lead stayed denied
Agent until a human typed (T-90757db7). The gate now re-checks the writer's
preconditions for a marker this session already holds and re-stamps it; every
other marker state keeps the deny.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SESSION_ID = "11111111-2222-4333-8444-555555555555"
_OTHER_SESSION_ID = "99999999-2222-4333-8444-555555555555"
_TEAM = "session-11111111"
_SLUG = "restamp-project"
_OLD, _NEW = "1.3.4", "1.3.5"
_AGENT_INPUT = {"name": "coder", "subagent_type": "pact-backend-coder"}
_INVENTED_SIG = hashlib.sha256(b"invented").hexdigest()


def _session(monkeypatch, tmp_path, *, members=({"name": "team-lead"},)):
    """A session that bootstrapped under _OLD: context, plugin root, team config."""
    import shared.pact_context as ctx_module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    config = tmp_path / ".claude"
    session_dir = config / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True)
    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    _set_version(plugin_root, _OLD)
    context_file = session_dir / "pact-session-context.json"
    context_file.write_text(json.dumps({
        "team_name": _TEAM,
        "session_id": _SESSION_ID,
        "project_dir": str(tmp_path / _SLUG),
        "plugin_root": str(plugin_root),
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")
    team_dir = config / "teams" / _TEAM
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(json.dumps({
        "leadSessionId": _SESSION_ID, "members": list(members),
    }), encoding="utf-8")
    monkeypatch.setattr(ctx_module, "_context_path", context_file)
    monkeypatch.setattr(ctx_module, "_cache", None)
    return session_dir, plugin_root


def _set_version(plugin_root, version):
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": version}), encoding="utf-8")


def _stamp(session_dir, plugin_root, version=_OLD, sid=None):
    """The marker the real writer leaves for `version`."""
    from bootstrap_marker_writer import _write_marker

    _write_marker(session_dir, sid or session_dir.name, str(plugin_root), version)
    return session_dir / "bootstrap-complete"


def _frame(tool_name="Agent", agent_type="pact-orchestrator", tool_input=None):
    data = {"hook_event_name": "PreToolUse", "session_id": _SESSION_ID,
            "tool_name": tool_name,
            "tool_input": _AGENT_INPUT if tool_input is None else tool_input}
    if agent_type is not None:
        data["agent_type"] = agent_type
    return data


def _restamps(session_dir):
    journal = session_dir / "session-journal.jsonl"
    if not journal.exists():
        return []
    events = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
    return [e for e in events if e.get("type") == "bootstrap_marker_restamp"]


def _assert_denied_and_untouched(session_dir, marker, before):
    from bootstrap_gate import _check_tool_allowed, is_marker_set

    assert _check_tool_allowed(_frame())
    assert not is_marker_set(session_dir)
    if before is None:
        assert not os.path.lexists(marker)
    else:
        assert marker.read_bytes() == before
    assert _restamps(session_dir) == []


def test_marker_from_the_previous_version_is_restamped_without_a_prompt(monkeypatch, tmp_path):
    """REPRODUCTION. Stamped under X, gate evaluated under Y, no prompt between."""
    from bootstrap_gate import _check_tool_allowed, is_marker_set

    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    _stamp(session_dir, plugin_root)
    assert is_marker_set(session_dir)
    _set_version(plugin_root, _NEW)
    assert not is_marker_set(session_dir)

    assert _check_tool_allowed(_frame()) is None
    assert is_marker_set(session_dir)
    # The gate writes what the writer writes: same bytes, same mode, no leftovers.
    marker = session_dir / "bootstrap-complete"
    restamped = marker.read_bytes()
    assert _stamp(session_dir, plugin_root, _NEW).read_bytes() == restamped
    assert marker.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in session_dir.glob(".bootstrap-complete-*")] == []
    # Never silent: one audit record naming the session and the new version.
    records = _restamps(session_dir)
    assert [(r["session_id"], r["plugin_version"]) for r in records] == [(_SESSION_ID, _NEW)]
    # The fresh marker verifies, so the next call takes the fast path.
    assert _check_tool_allowed(_frame()) is None
    assert len(_restamps(session_dir)) == 1


def test_restamp_through_the_real_hook_process(tmp_path):
    """The same advance through `python3 hooks/bootstrap_gate.py`: exit 0, no deny."""
    config = tmp_path / ".claude"
    session_dir = config / "pact-sessions" / _SLUG / _SESSION_ID
    session_dir.mkdir(parents=True)
    plugin_root = tmp_path / "plugin"
    (plugin_root / ".claude-plugin").mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(json.dumps({
        "team_name": _TEAM, "session_id": _SESSION_ID,
        "project_dir": str(tmp_path / _SLUG), "plugin_root": str(plugin_root),
        "started_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")
    (config / "teams" / _TEAM).mkdir(parents=True)
    (config / "teams" / _TEAM / "config.json").write_text(json.dumps({
        "leadSessionId": _SESSION_ID, "members": [{"name": "team-lead"}],
    }), encoding="utf-8")
    _stamp(session_dir, plugin_root)
    _set_version(plugin_root, _NEW)

    hook = Path(__file__).resolve().parents[1] / "hooks" / "bootstrap_gate.py"
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT",
                        "CLAUDE_CODE_SESSION_ID")}
    env.update(HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(config),
               CLAUDE_PROJECT_DIR=str(tmp_path / _SLUG))

    def run():
        return subprocess.run([sys.executable, str(hook)], input=json.dumps(_frame()),
                              capture_output=True, text=True, timeout=30, env=env)

    proc = run()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "permissionDecision" not in proc.stdout
    assert _NEW in proc.stderr and _SESSION_ID in proc.stderr
    assert len(_restamps(session_dir)) == 1
    assert run().returncode == 0
    assert len(_restamps(session_dir)) == 1


# --- the rows that stay denied --------------------------------------------


@pytest.mark.parametrize("members, inboxes", [([], False), ([{"name": "  "}], False), (None, False)])
def test_version_change_with_no_identifiable_team_stays_denied(monkeypatch, tmp_path, members, inboxes):
    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    marker = _stamp(session_dir, plugin_root)
    config = tmp_path / ".claude" / "teams" / _TEAM / "config.json"
    if members is None:
        config.unlink()
    else:
        config.write_text(json.dumps({"leadSessionId": _SESSION_ID, "members": members}))
    _set_version(plugin_root, _NEW)
    _assert_denied_and_untouched(session_dir, marker, marker.read_bytes())


def test_version_change_with_an_unresolved_team_name_stays_denied(monkeypatch, tmp_path):
    import shared.pact_context as ctx_module

    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    marker = _stamp(session_dir, plugin_root)
    context_file = session_dir / "pact-session-context.json"
    context = json.loads(context_file.read_text())
    context["team_name"] = ""
    context_file.write_text(json.dumps(context))
    monkeypatch.setattr(ctx_module, "_cache", None)
    _set_version(plugin_root, _NEW)
    _assert_denied_and_untouched(session_dir, marker, marker.read_bytes())


@pytest.mark.parametrize("body", [
    b"",                                                            # touch
    b"bootstrap-complete",                                          # echo
    b"[]",
    b"null",
    json.dumps({"v": 1, "sid": _SESSION_ID}).encode(),              # no sig
    json.dumps({"v": 1, "sid": _SESSION_ID, "sig": _INVENTED_SIG, "pv": _OLD}).encode(),
    json.dumps({"v": 2, "sid": _SESSION_ID, "sig": _INVENTED_SIG}).encode(),
    json.dumps({"v": True, "sid": _SESSION_ID, "sig": _INVENTED_SIG}).encode(),
    json.dumps({"v": "1", "sid": _SESSION_ID, "sig": _INVENTED_SIG}).encode(),
    json.dumps({"v": 1, "sid": _SESSION_ID, "sig": ""}).encode(),
    json.dumps({"v": 1, "sid": _SESSION_ID, "sig": "stale"}).encode(),
    json.dumps({"v": 1, "sid": _SESSION_ID, "sig": _INVENTED_SIG.upper()}).encode(),
    json.dumps({"v": 1, "sid": _SESSION_ID, "sig": _INVENTED_SIG + "0"}).encode(),
    json.dumps({"v": 1, "sid": _SESSION_ID, "sig": "١" * 64}).encode(),  # non-ASCII digits
    json.dumps({"v": 1, "sid": _SESSION_ID, "sig": None}).encode(),
    json.dumps({"v": 1, "sid": _SESSION_ID, "sig": 12345}).encode(),
    json.dumps({"v": 1, "sid": _SESSION_ID, "sig": [_INVENTED_SIG]}).encode(),
    json.dumps({"v": 1, "sid": [_SESSION_ID], "sig": _INVENTED_SIG}).encode(),
    json.dumps({"v": 10 ** 40, "sid": _SESSION_ID, "sig": _INVENTED_SIG}).encode(),
    json.dumps({"v": 1, "sid": _SESSION_ID, "sig": _INVENTED_SIG}).encode() + b" " * 300,
    b"\xff\xfe not utf-8",
], ids=lambda b: repr(b[:40]))
def test_hand_written_marker_of_the_wrong_shape_stays_denied(monkeypatch, tmp_path, body):
    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    _set_version(plugin_root, _NEW)
    marker = session_dir / "bootstrap-complete"
    marker.write_bytes(body)
    _assert_denied_and_untouched(session_dir, marker, body)


def test_no_marker_at_all_stays_denied(monkeypatch, tmp_path):
    """A first stamp is still the writer's; the gate only re-stamps."""
    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    _set_version(plugin_root, _NEW)
    _assert_denied_and_untouched(session_dir, session_dir / "bootstrap-complete", None)


def test_marker_from_another_session_id_stays_denied(monkeypatch, tmp_path):
    """A genuine stamp for another session, copied into this session's dir."""
    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    marker = _stamp(session_dir, plugin_root, sid=_OTHER_SESSION_ID)
    _set_version(plugin_root, _NEW)
    _assert_denied_and_untouched(session_dir, marker, marker.read_bytes())


def test_marker_in_a_different_session_dir_stays_denied(monkeypatch, tmp_path):
    """This session's genuine stamp does not unlock a session dir of another name."""
    import shared.pact_context as ctx_module
    from bootstrap_gate import _check_tool_allowed, is_marker_set

    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    stamped = _stamp(session_dir, plugin_root).read_bytes()
    other_dir = session_dir.parent / _OTHER_SESSION_ID
    other_dir.mkdir()
    (other_dir / "bootstrap-complete").write_bytes(stamped)
    context = json.loads((session_dir / "pact-session-context.json").read_text())
    context["session_id"] = _OTHER_SESSION_ID
    other_context = other_dir / "pact-session-context.json"
    other_context.write_text(json.dumps(context))
    monkeypatch.setattr(ctx_module, "_context_path", other_context)
    monkeypatch.setattr(ctx_module, "_cache", None)
    _set_version(plugin_root, _NEW)

    frame = _frame()
    frame["session_id"] = _OTHER_SESSION_ID
    assert ctx_module.get_session_dir() == str(other_dir)
    assert _check_tool_allowed(frame)
    assert not is_marker_set(other_dir)
    assert (other_dir / "bootstrap-complete").read_bytes() == stamped
    assert _restamps(other_dir) == []


def test_symlinked_marker_stays_denied(monkeypatch, tmp_path):
    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    real = _stamp(session_dir, plugin_root)
    elsewhere = tmp_path / "elsewhere-marker"
    real.rename(elsewhere)
    real.symlink_to(elsewhere)
    _set_version(plugin_root, _NEW)
    before = elsewhere.read_bytes()
    _assert_denied_and_untouched(session_dir, elsewhere, before)
    assert real.is_symlink()


@pytest.mark.parametrize("agent_type", [None, "pact-backend-coder", "repacted:pact-backend-coder",
                                        "PACT-ORCHESTRATOR", ["pact-orchestrator"]])
def test_non_lead_frame_never_restamps(monkeypatch, tmp_path, agent_type):
    """A teammate is not gated here, and it must not be the one that re-stamps."""
    from bootstrap_gate import _check_tool_allowed, is_marker_set

    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    marker = _stamp(session_dir, plugin_root)
    before = marker.read_bytes()
    _set_version(plugin_root, _NEW)
    assert _check_tool_allowed(_frame(agent_type=agent_type)) is None
    assert marker.read_bytes() == before
    assert not is_marker_set(session_dir)
    assert _restamps(session_dir) == []


@pytest.mark.parametrize("plugin_json", [
    None,                                   # missing
    "unreadable",                           # a directory where the file should be
    "{not json",
    json.dumps({}),
    json.dumps({"version": ""}),
    json.dumps({"version": None}),
    json.dumps({"version": 135}),
    json.dumps({"version": ["1.3.5"]}),
    json.dumps({"version": {"major": 1}}),
])
def test_unreadable_or_empty_version_stays_denied(monkeypatch, tmp_path, plugin_json):
    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    marker = _stamp(session_dir, plugin_root)
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    manifest.unlink()
    if plugin_json == "unreadable":
        manifest.mkdir()
    elif plugin_json is not None:
        manifest.write_text(plugin_json, encoding="utf-8")
    _assert_denied_and_untouched(session_dir, marker, marker.read_bytes())


def test_plugin_json_that_is_not_an_object_is_denied_through_main(monkeypatch, tmp_path, capsys):
    """The verifier itself raises on this manifest (as it did before the
    re-stamp existed), so the row is pinned on main()'s outcome: the runtime
    fail-closed deny, with the marker left alone."""
    import io
    from unittest.mock import patch

    from bootstrap_gate import main

    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    marker = _stamp(session_dir, plugin_root)
    before = marker.read_bytes()
    (plugin_root / ".claude-plugin" / "plugin.json").write_text("[]", encoding="utf-8")
    with patch("sys.stdin", io.StringIO(json.dumps(_frame()))):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 2
    assert '"permissionDecision": "deny"' in capsys.readouterr().out
    assert marker.read_bytes() == before
    assert _restamps(session_dir) == []


def test_a_failed_write_stays_denied(monkeypatch, tmp_path):
    """The re-stamp is believed only when the verifier accepts what is on disk."""
    import bootstrap_gate

    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    marker = _stamp(session_dir, plugin_root)
    _set_version(plugin_root, _NEW)

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bootstrap_gate.os, "replace", boom)
    _assert_denied_and_untouched(session_dir, marker, marker.read_bytes())
    monkeypatch.undo()
    assert [p.name for p in session_dir.glob(".bootstrap-complete-*")] == []


def test_config_less_team_with_an_inboxes_dir_is_restamped(monkeypatch, tmp_path):
    """The writer's config-less witness (teams/<team>/inboxes/) counts here too."""
    from bootstrap_gate import _check_tool_allowed, _team_on_disk
    from bootstrap_marker_writer import _team_is_identified

    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    _stamp(session_dir, plugin_root)
    team_dir = tmp_path / ".claude" / "teams" / _TEAM
    (team_dir / "config.json").unlink()
    _set_version(plugin_root, _NEW)
    assert not _team_on_disk(_TEAM) and not _team_is_identified(_TEAM)
    assert _check_tool_allowed(_frame())
    (team_dir / "inboxes").mkdir()
    assert _team_on_disk(_TEAM) and _team_is_identified(_TEAM)
    assert _check_tool_allowed(_frame()) is None
    assert len(_restamps(session_dir)) == 1


def test_known_limit_invented_signature_is_restamped_only_with_the_writer_preconditions(
        monkeypatch, tmp_path):
    """KNOWN LIMIT (lead ruling on #47). The gate cannot verify the old
    signature, because it does not know the version it was computed under. So a
    hand-written marker of the right shape and session id is re-stamped when
    the frame is the lead and the team is on disk: the state in which the
    writer stamps on the next prompt with no marker at all. The stamp is a
    fingerprint, not a MAC. Without the team the same marker is denied."""
    from bootstrap_gate import _check_tool_allowed

    session_dir, plugin_root = _session(monkeypatch, tmp_path)
    _set_version(plugin_root, _NEW)
    marker = session_dir / "bootstrap-complete"
    body = json.dumps({"v": 1, "sid": _SESSION_ID, "sig": _INVENTED_SIG}).encode()
    marker.write_bytes(body)
    config = tmp_path / ".claude" / "teams" / _TEAM / "config.json"
    roster = config.read_text()
    config.write_text(json.dumps({"leadSessionId": _SESSION_ID, "members": []}))
    _assert_denied_and_untouched(session_dir, marker, body)

    config.write_text(roster)
    assert _check_tool_allowed(_frame()) is None
    assert len(_restamps(session_dir)) == 1
