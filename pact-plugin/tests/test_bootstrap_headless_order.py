"""
Location: pact-plugin/tests/test_bootstrap_headless_order.py
Summary: The `claude -p` order of events reaches a bootstrap marker before the
         first Agent call: one UserPromptSubmit (no team on disk yet), then
         TaskCreate makes the platform provision teams/<team>/inboxes, then
         PostToolUse(TaskCreate) must stamp the marker so bootstrap_gate lets
         Agent through. Adversarial rows: a team dir that belongs to another
         session, no session dir at all, a symlinked team dir, config.json or
         inboxes/, and a config dir reached through a linked ancestor (allowed).
Used by: pytest. Drives the real hooks/dispatch.py and hooks/bootstrap_gate.py
         as subprocesses under a temporary HOME and CLAUDE_CONFIG_DIR.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = PLUGIN_ROOT / "hooks"

_SID = "c0ffee00-2222-4333-8444-555555555555"
_OTHER_SID = "0a0a0a0a-1111-2222-3333-444444444444"
_LEAD = "repacted:pact-orchestrator"


@pytest.fixture
def home(tmp_path):
    """A lead session as session_init leaves it under -p: context file, no team."""
    config = tmp_path / ".claude"
    project = tmp_path / "toy"
    project.mkdir()
    session_dir = config / "pact-sessions" / "toy" / _SID
    session_dir.mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(json.dumps({
        "team_name": _SID,
        "session_id": _SID,
        "project_dir": str(project),
        "plugin_root": str(PLUGIN_ROOT),
        "started_at": "2026-10-18T00:00:00Z",
    }), encoding="utf-8")
    (config / "teams").mkdir()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
           "CLAUDE_CONFIG_DIR": str(config), "CLAUDE_PROJECT_DIR": str(project),
           "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT),
           # The bootstrap gate and marker writer are Teams-only (shared/execution.py).
           "REPACT_EXECUTION": "teams", "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}
    return {"tmp": tmp_path, "config": config, "session_dir": session_dir, "env": env}


def _run(env, argv, frame):
    return subprocess.run([sys.executable, *argv], input=json.dumps(frame), text=True,
                          capture_output=True, env=env, cwd=env["CLAUDE_PROJECT_DIR"],
                          timeout=120)


def _prompt(env):
    return _run(env, [str(HOOKS_DIR / "dispatch.py"), "UserPromptSubmit"],
                {"hook_event_name": "UserPromptSubmit", "session_id": _SID,
                 "prompt": "/repacted:autopilot", "agent_type": _LEAD})


def _post_task_create(env):
    return _run(env, [str(HOOKS_DIR / "dispatch.py"), "PostToolUse", "TaskCreate"],
                {"hook_event_name": "PostToolUse", "session_id": _SID, "agent_type": _LEAD,
                 "tool_name": "TaskCreate", "tool_input": {"subject": "work"},
                 "tool_response": {"task": {"id": "1"}}})


def _agent_denied(env) -> bool:
    done = _run(env, [str(HOOKS_DIR / "bootstrap_gate.py")],
                {"hook_event_name": "PreToolUse", "session_id": _SID, "agent_type": _LEAD,
                 "tool_name": "Agent",
                 "tool_input": {"subagent_type": "pact-backend-coder", "name": "coder"}})
    # The gate denies with exit 2 and a permissionDecision "deny" payload.
    assert done.returncode in (0, 2), done.stderr
    return done.returncode == 2 and '"deny"' in done.stdout


def _marker(home) -> Path:
    return home["session_dir"] / "bootstrap-complete"


def test_print_mode_order_stamps_marker_after_task_create(home):
    env = home["env"]
    assert _prompt(env).returncode == 0
    assert not _marker(home).exists(), "no team on disk yet: the prompt must not stamp"
    assert _agent_denied(env)

    # The platform provisions the team on the first TaskCreate.
    (home["config"] / "teams" / _SID / "inboxes").mkdir(parents=True)
    done = _post_task_create(env)
    assert done.returncode == 0, done.stderr

    assert _marker(home).is_file(), "PostToolUse(TaskCreate) must stamp the marker"
    assert not _agent_denied(env)


def test_team_dir_of_another_session_does_not_stamp(home):
    (home["config"] / "teams" / _OTHER_SID / "inboxes").mkdir(parents=True)
    assert _post_task_create(home["env"]).returncode == 0
    assert not _marker(home).exists()
    assert _agent_denied(home["env"])


@pytest.mark.parametrize("team", [None, _OTHER_SID, _SID])
def test_missing_session_dir(home, team):
    """session_init never ran: the lead-frame self-heal recreates this session's context, and a
    marker follows only when THIS session's team is on disk, and only in its own dir."""
    import shutil

    shutil.rmtree(home["session_dir"])
    if team:
        (home["config"] / "teams" / team / "inboxes").mkdir(parents=True)
    assert _post_task_create(home["env"]).returncode == 0
    markers = list((home["config"] / "pact-sessions").rglob("bootstrap-complete"))
    assert markers == ([_marker(home)] if team == _SID else [])


def _link_team(home, link):
    """teams/<sid> as a link to a team dir outside, or a real dir whose config.json or
    inboxes/ is a link there (the linked config.json case)."""
    outside = home["tmp"] / "outside"
    (outside / "inboxes").mkdir(parents=True)
    (outside / "config.json").write_text(json.dumps({"members": [{"name": "team-lead"}]}),
                                         encoding="utf-8")
    team_dir = home["config"] / "teams" / _SID
    if link == "team":
        os.symlink(outside, team_dir)
    else:
        team_dir.mkdir()
        name = "config.json" if link == "config" else "inboxes"
        os.symlink(outside / name, team_dir / name)


@pytest.mark.parametrize("link", ["team", "inboxes", "config"])
def test_symlinked_team_dir_does_not_stamp(home, link):
    _link_team(home, link)
    assert _post_task_create(home["env"]).returncode == 0
    assert not _marker(home).exists()
    assert _agent_denied(home["env"])


@pytest.mark.parametrize("link", ["team", "inboxes", "config"])
def test_gate_mirror_refuses_symlinked_team_dir(home, monkeypatch, link):
    """bootstrap_gate._team_on_disk (the restamp precondition) refuses the same shapes."""
    import bootstrap_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home["config"]))
    _link_team(home, link)
    assert bootstrap_gate._team_on_disk(_SID) is False

    real = home["config"] / "teams" / "real-team" / "inboxes"
    real.mkdir(parents=True)
    assert bootstrap_gate._team_on_disk("real-team") is True


def test_a_config_dir_reached_through_a_linked_ancestor_stamps_and_opens_the_gate(home):
    """CLAUDE_CONFIG_DIR spelled through a link above it (macOS /var ->
    /private/var). The writer and the gate ask the same question, so the stamped marker opens
    the gate; a link below the config dir is still refused (the rows above)."""
    alias = home["tmp"] / "alias"              # alias/.claude is home's own .claude
    os.symlink(home["tmp"], alias)
    env = {**home["env"], "HOME": str(alias), "CLAUDE_CONFIG_DIR": str(alias / ".claude"),
           "CLAUDE_PROJECT_DIR": str(alias / "toy")}
    (home["config"] / "teams" / _SID / "inboxes").mkdir(parents=True)
    assert _post_task_create(env).returncode == 0
    assert _marker(home).is_file()
    assert not _agent_denied(env)
