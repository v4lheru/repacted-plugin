"""tests/test_task_state_protection.py

The workflow state the repact commands own may not be written around them.
  - <config>/tasks/ and <config>/pact-sessions/ are protected places of shell/settings.py,
    so settings_guard denies a Write, Edit, NotebookEdit or Bash write of them for every frame,
    the lead included (rule settings.task_state); reads pass, and the plugin's own bin tools may
    name them (resolved by path, never by name).
  - The task-log close gate passes a linked task only with metadata.dispatch and an
    acceptance whose op_id has an accept row and a done row in the session's ops.jsonl;
    task_write_guard refuses the op-owned keys on any task, and status completed on a
    dispatched one.
  - revision_number is reserved, and the third-rejection park counts the rejections the ops
    log keeps, so a reset of the task's own field changes nothing.
Each bypass row was a passing probe in a security review.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

import settings_guard as guard
import task_write_guard as TWG
from fixtures.ops_env import TEAM, World, handoff, ops_env, teachback  # noqa: F401 - fixture
from shared.ops import accept, submit
from shared.ops import evidence as EV
from shared.ops import records
from shared.task_log import close_gate as CG
from shared.task_log import write as W
from shared.task_store import TaskRef

PLUGIN = Path(__file__).resolve().parent.parent
RULE = "[rule: settings.task_state]"
CODER = {"agent_id": "a1", "agent_type": "pact-backend-coder"}
FAILING = {"problems": ["no passing tcount run of the task since dispatch"], "notes": []}


# -- the guard -----------------------------------------------------------------------

@pytest.fixture
def config(tmp_path, monkeypatch) -> Path:
    """A config dir at ~/.claude (HOME under tmp_path) holding one task and one session dir."""
    home = tmp_path / "home"
    config = home / ".claude"
    (config / "tasks" / "team-ops").mkdir(parents=True)
    (config / "tasks" / "team-ops" / "5.json").write_text('{"id": "5", "status": "in_progress"}')
    session = config / "pact-sessions" / "proj" / "sess-1"
    session.mkdir(parents=True)
    (session / "unattended.json").write_text('{"v": 1, "unattended": true}')
    (session / "ops.jsonl").write_text("")
    monkeypatch.setenv("HOME", str(home))
    for name in ("CLAUDE_CONFIG_DIR", "REPACT_UNATTENDED", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(name, raising=False)
    return config


def _bash(command: str, cwd: Path, **frame) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd), **frame}


def _refused(payload: dict) -> bool:
    output = guard.decide(payload)
    if not output:
        return False
    hso = output["hookSpecificOutput"]
    return hso["permissionDecision"] == "deny" and RULE in hso["permissionDecisionReason"]


TASK = "~/.claude/tasks/team-ops/5.json"
SESSION = "~/.claude/pact-sessions/proj/sess-1"


@pytest.mark.parametrize("command", [
    # write routes into a task file
    f"echo '{{}}' > {TASK}",])
def test_a_write_of_task_or_session_state_is_refused_for_the_lead_too(config, command):
    assert _refused(_bash(command, config.parent)), command          # the lead (no marker)
    assert _refused(_bash(command, config.parent, **CODER)), command


def test_the_config_dir_named_by_claude_config_dir_is_protected(tmp_path, monkeypatch):
    config = tmp_path / "cfg"
    (config / "tasks" / "team-ops").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for command in ("echo x > $CLAUDE_CONFIG_DIR/tasks/team-ops/5.json",
                    "echo x > ${CLAUDE_CONFIG_DIR}/pact-sessions/p/s/ops.jsonl",
                    f"echo x > {config}/tasks/team-ops/5.json"):
        assert _refused(_bash(command, tmp_path)), command


def test_a_unicode_spelling_of_the_config_dir_is_protected(tmp_path, monkeypatch):
    config = tmp_path / unicodedata.normalize("NFC", "cfgé")
    (config / "tasks" / "team-ops").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    decomposed = unicodedata.normalize("NFD", str(config))
    assert _refused(_bash(f"echo x > '{decomposed}/tasks/team-ops/5.json'", tmp_path))


def test_a_symlink_into_tasks_is_followed(config, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(config / "tasks" / "team-ops")
    assert _refused(_bash(f"echo x > {link}/5.json", tmp_path))
    assert _refused({"tool_name": "Write", "tool_input": {"file_path": f"{link}/5.json", "content": "{}"},
                     "cwd": str(tmp_path)})


@pytest.mark.parametrize("tool,field", [("Write", "file_path"),])
def test_a_file_tool_write_of_task_or_session_state_is_refused(config, tool, field):
    for path in (config / "tasks" / "team-ops" / "5.json",
                 config / "pact-sessions" / "proj" / "sess-1" / "unattended.json"):
        for frame in ({}, CODER):
            payload = {"tool_name": tool, "tool_input": {field: str(path)}, "cwd": str(config), **frame}
            assert _refused(payload), (tool, path, frame)


@pytest.mark.parametrize("command", [
    f"cat {TASK}",])
def test_a_read_of_task_state_and_a_neighbour_write_pass(config, command):
    assert guard.decide(_bash(command, config.parent, **CODER)) is None, command


HOOK_ROUTES = {
    "write_task_json_lead": ("Write", {"file_path": "{T}", "content": "{}"}, {}),
    "write_task_json_coder": ("Write", {"file_path": "{T}", "content": "{}"}, CODER),
    "edit_task_json_coder": ("Edit", {"file_path": "{T}", "old_string": "in_progress",
                                      "new_string": "completed"}, CODER),
    "bash_py_write_task": ("Bash", {"command": "python3 -c \"import json;p='{T}';"
                                               "json.dump({{}},open(p,'w'))\""}, CODER),
    "bash_redirect_task": ("Bash", {"command": "echo '{{}}' > {T}"}, {}),
    "bash_sed_task": ("Bash", {"command": "sed -i '' s/in_progress/completed/ {T}"}, {}),
}


# -- the close gate and the task write guard ---------------------------------------------------

def _linked_world(w: World, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (w.session_dir / "pact-session-context.json").write_text(json.dumps({"team_name": TEAM}))
    store = W.init(w.repo)
    ref = store.add("Fix the parser")
    return store, ref


def _head(w: World) -> str:
    return subprocess.run(["git", "-C", str(w.repo), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()


def _complete_by_hand(w: World, task_id: str, **metadata) -> None:
    def write(task):
        task["status"] = "completed"
        task["metadata"].update(metadata)
        return True
    w.store.update(TaskRef(TEAM, task_id), write)


def _update(task_id: str, **tool_input) -> dict:
    return {"tool_name": "TaskUpdate", "tool_input": dict(tool_input, taskId=task_id)}


def _plain_task(task_id: str) -> None:
    path = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "tasks" / TEAM / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": task_id, "subject": "todo", "status": "in_progress", "metadata": {}}))


# -- the rejection count -----------------------------------------------------------------------


@pytest.fixture
def rejected_twice(ops_env, monkeypatch, tmp_path):
    w = ops_env
    monkeypatch.delenv("REPACT_UNATTENDED", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    store = W.init(w.repo)
    ref = store.add("Fix the empty-input parser bug")
    task_id = w.dispatch(refs=[ref])
    submit.teachback(w.coder(), task_id, teachback())
    submit.handoff(w.coder(), task_id, handoff(w.worktree(task_id), refs={ref: "done: parser.py"}))
    monkeypatch.setattr(EV, "verdict", lambda *a, **k: dict(FAILING))
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    for rev in (1, 2):
        assert accept.run(w.ctx(), task_id).exit_code == 1
        submit.handoff(w.coder(), task_id, handoff(w.worktree(task_id), decisions=[f"try {rev + 1}"],
                                                   refs={ref: "done: parser.py"}))
    return w, task_id


