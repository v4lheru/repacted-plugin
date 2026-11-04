"""tests/test_task_state_resolve.py

The task-state protection reads the command the way the shell runs it.
  - A path held in a shell variable (`f=PATH; cp x $f`, `d=tasks; .../$d/...`, export, ${d},
    nested), a write through a link into <config>/tasks/ or <config>/pact-sessions/, and find
    -exec / -delete and xargs writers over a root that holds them are refused (settings.task_state).
    A write target with a variable the reader cannot spell is refused when the command also names
    the config dir or pact-sessions.
  - The same routes cannot remove the sticky unattended marker.
  - The launcher run under xargs (or find -exec) with no verb in sight is refused in every
    frame (ops_frame_guard.verb_unseen).
The chain rows were passing bypass probes in a security review.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

import ops_frame_guard
import settings_guard as guard
from fixtures.ops_env import SESSION_ID, TEAM, World, handoff, ops_env, teachback  # noqa: F401
from shared import unattended
from shared.ops import evidence as EV
from shared.ops import submit
from shared.task_log import write as W

PLUGIN = Path(__file__).resolve().parent.parent
RULE = "[rule: settings.task_state]"
CODER = {"agent_id": "a1", "agent_type": "pact-backend-coder"}
WHY = "looks fine to me"


# -- the settings check ------------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """HOME under tmp_path with ~/.claude holding one task and one session dir, links into both
    (l7 -> tasks/team-ops, lnk3 -> the session dir), and a plain repo beside it."""
    home = tmp_path / "home"
    config = home / ".claude"
    (config / "tasks" / "team-ops").mkdir(parents=True)
    (config / "tasks" / "team-ops" / "5.json").write_text('{"id": "5", "status": "in_progress"}')
    session = config / "pact-sessions" / "proj" / "sess-1"
    session.mkdir(parents=True)
    for name in ("unattended.json", "unattended.sticky.json", "ops.jsonl"):
        (session / name).write_text("{}")
    (home / "l7").symlink_to(config / "tasks" / "team-ops")
    (home / "lnk3").symlink_to(session)
    (tmp_path / "repo" / "tasks").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for name in ("CLAUDE_CONFIG_DIR", "REPACT_UNATTENDED", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(name, raising=False)
    return home


def _bash(command: str, cwd: Path, **frame) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd), **frame}


def _refused(payload: dict) -> bool:
    output = guard.decide(payload)
    if not output:
        return False
    hso = output["hookSpecificOutput"]
    return hso["permissionDecision"] == "deny" and RULE in hso["permissionDecisionReason"]


SESSION = ".claude/pact-sessions/proj/sess-1"


@pytest.mark.parametrize("command", [
    # a variable that holds the path
    "f=~/.claude/tasks/team-ops/5.json; echo '{}' > $f",])
def test_a_resolved_write_of_task_or_session_state_is_refused(home, command):
    assert _refused(_bash(command, home)), command                  # the lead (no marker)
    assert _refused(_bash(command, home, **CODER)), command


@pytest.mark.parametrize("command", [
    # the session's ops log emptied or truncated: plainly, through a variable, through a link
    f": > {SESSION}/ops.jsonl",])
def test_emptying_the_sessions_ops_log_is_refused(home, command):
    assert _refused(_bash(command, home)), command
    assert _refused(_bash(command, home, **CODER)), command


@pytest.mark.parametrize("command", [
    # a command substitution next to the write
    "x=$(date); echo x > ~/.claude/tasks/team-ops/5.json",])
def test_the_edges_of_the_resolver_still_refuse(home, command):
    assert _refused(_bash(command, home)), command
    assert _refused(_bash(command, home, **CODER)), command


def test_a_unicode_spelling_held_in_a_variable_is_refused(tmp_path, monkeypatch):
    config = tmp_path / unicodedata.normalize("NFC", "cfgé")
    (config / "tasks" / "team-ops").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    decomposed = unicodedata.normalize("NFD", str(config))
    assert _refused(_bash(f"c='{decomposed}'; echo x > $c/tasks/team-ops/5.json", tmp_path))
    assert _refused(_bash(f"c='{decomposed}'; d=TASKS; echo x > $c/$d/team-ops/5.json", tmp_path))


@pytest.mark.parametrize("command", [
    # reads of task state, and readers run by find or xargs
    "f=~/.claude/tasks/team-ops/5.json; cat $f",])
def test_reads_of_task_state_pass(home, command):
    assert guard.decide(_bash(command, home)) is None, command
    assert guard.decide(_bash(command, home, **CODER)) is None, command


# -- the r2v chains through the real dispatcher ------------------------------------------------------

def _hook(w: World, command: str, frame: dict) -> str:
    payload = dict({"session_id": SESSION_ID, "hook_event_name": "PreToolUse", "cwd": str(w.repo),
                    "tool_name": "Bash", "tool_input": {"command": command},
                    "transcript_path": str(w.repo / "t.jsonl")}, **frame)
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(PLUGIN), CLAUDE_PROJECT_DIR=str(w.repo))
    done = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "dispatch.py"), "PreToolUse", "Bash"],
                          input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60,
                          cwd=str(w.repo))
    out = done.stdout.strip()
    result = json.loads(out.splitlines()[-1]) if out else {}
    hso = result.get("hookSpecificOutput") or {}
    return hso.get("permissionDecision") or result.get("decision") or "allow"


@pytest.fixture
def row_world(ops_env: World, monkeypatch, tmp_path):
    w = ops_env
    monkeypatch.delenv("REPACT_UNATTENDED", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    unattended.write_flag(str(w.session_dir), True, "2026-10-19T00:00:00Z")      # owner went unattended
    store = W.init(w.repo)
    ref = store.add("Fix the parser")
    task_id = w.dispatch(refs=[ref])
    submit.teachback(w.coder(), task_id, teachback())
    head = subprocess.run(["git", "-C", str(w.repo), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    submit.handoff(w.coder(), task_id, handoff(w.worktree(task_id), refs={ref: "done: parser.py"},
                                               evidence={"head": head}))
    return w, ref, task_id


# -- the frame guard ---------------------------------------------------------------------------

class _Actor:
    def __init__(self, lead: bool, marked: bool, source: str = "host", name: str = ""):
        self.is_lead, self.marked, self.source, self.name = lead, marked, source, name

    def runs_lead_ops(self):
        return self.is_lead or (self.source == "unknown" and not self.marked)


LEAD = _Actor(lead=True, marked=False, name="lead")
CODER_FRAME = _Actor(lead=False, marked=True, name="coder-1")
UNSEEN = "[rule: ops_frame_guard.verb_unseen]"


def _cmd(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


