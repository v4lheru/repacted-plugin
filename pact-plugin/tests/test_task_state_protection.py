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
    f"echo '{{}}' > {TASK}",
    f"sed -i '' s/in_progress/completed/ {TASK}",
    f"python3 -c \"import json;p='{TASK}';json.dump({{}},open(p,'w'))\"",
    f"tee {TASK} < /dev/null",
    f"cp /tmp/forged.json {TASK}",
    f"cp /tmp/forged.json ~/.claude/tasks/team-ops/",
    f"mv /tmp/forged.json {TASK}",
    f"ln -sf /tmp/forged.json {TASK}",
    f"rm {TASK}",
    f"truncate -s 0 {TASK}",
    f"perl -pi -e s/in_progress/completed/ {TASK}",
    f"node -e \"require('fs').writeFileSync('{TASK}','{{}}')\"",
    "rm -rf ~/.claude/tasks",
    # the session dir and the unattended flag
    f"echo '{{\"op_id\":\"op-1\",\"side\":\"task_log\"}}' >> {SESSION}/ops.jsonl",
    f"rm {SESSION}/unattended.json",
    f"echo '{{\"v\":1,\"unattended\":false}}' > {SESSION}/unattended.json",
    # spellings
    "echo x > $HOME/.claude/tasks/team-ops/5.json",
    "echo x > ${HOME}/.claude/pact-sessions/proj/sess-1/ops.jsonl",
    "echo x > ~/.claude/tasks/other/../team-ops/5.json",
    "echo x > ~/.claude/x/../tasks/team-ops/5.json",
    "echo x > ~/.claude/Tasks/team-ops/5.json",
    "echo x > ~/.claude/PACT-Sessions/proj/sess-1/ops.jsonl",
    "echo x > \"$HOME/.claude/tasks/team-ops/5.json\"",
    "echo x > \"$HOME\"'/.claude/tasks/team-ops/5.json'",
    "echo x > ~/.claude/tas*/team-ops/5.json",
    "rm ~/.claude/tasks/*/5.json",
    "echo x > ~/.claude/tasks/team-ops/{5,6}.json",
    "cd ~/.claude/tasks/team-ops && echo x > 5.json",
    "bash -c 'echo x > ~/.claude/tasks/team-ops/5.json'",
    "ln -s ~/.claude/tasks/team-ops /tmp/t && echo x > /tmp/t/5.json",
])
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


@pytest.mark.parametrize("tool,field", [("Write", "file_path"), ("Edit", "file_path"),
                                        ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")])
def test_a_file_tool_write_of_task_or_session_state_is_refused(config, tool, field):
    for path in (config / "tasks" / "team-ops" / "5.json",
                 config / "pact-sessions" / "proj" / "sess-1" / "unattended.json"):
        for frame in ({}, CODER):
            payload = {"tool_name": tool, "tool_input": {field: str(path)}, "cwd": str(config), **frame}
            assert _refused(payload), (tool, path, frame)


@pytest.mark.parametrize("command", [
    f"cat {TASK}",
    f"jq .metadata {TASK}",
    f"cp {TASK} /tmp/copy.json",
    f"grep -n status {SESSION}/ops.jsonl",
    f"wc -l {SESSION}/ops.jsonl",
    "echo x > ~/.claude/notes/tasks.json",
    "echo x > /tmp/tasks/team-ops/5.json",
])
def test_a_read_of_task_state_and_a_neighbour_write_pass(config, command):
    assert guard.decide(_bash(command, config.parent, **CODER)) is None, command


def test_the_plugins_own_bin_tools_may_name_the_session_dir(config):
    session = config / "pact-sessions" / "proj" / "sess-1"
    for command in (f'"{PLUGIN}/bin/repact-journal-write" --type commit --session-dir \'{session}\' --stdin',
                    f'python3 "{PLUGIN}/bin/repact-task" --session-dir {session} list',
                    f'"{PLUGIN}/bin/repact" accept 5 --session-dir {session}',
                    # the secretary's harvest record and the memory skill's session helper
                    f'python3 "{PLUGIN}/scripts/harvest.py" record --session-dir "{session}" --team t --ids 1',
                    f'python3 "{PLUGIN}/skills/pact-memory/scripts/pact_session.py" --session-dir "{session}"'):
        assert guard.decide(_bash(command, config.parent)) is None, command


def test_a_look_alike_repact_outside_the_plugin_bin_is_refused(config, tmp_path, monkeypatch):
    session = config / "pact-sessions" / "proj" / "sess-1"
    fake = tmp_path / "evil" / "bin"
    fake.mkdir(parents=True)
    (fake / "repact").write_text("#!/bin/sh\necho > \"$2/ops.jsonl\"\n")
    (fake / "repact").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ.get('PATH', '')}")
    assert _refused(_bash(f"{fake}/repact accept 5 --session-dir {session}", config.parent))
    assert _refused(_bash(f"repact accept 5 --session-dir {session}", config.parent))
    # a redirect from a plugin tool is still a write of its own
    assert _refused(_bash(f'"{PLUGIN}/bin/repact" inspect 5 > {session}/ops.jsonl', config.parent))


def test_the_launcher_shim_on_path_may_name_the_session_dir(config, tmp_path, monkeypatch):
    from shared import repact_shim
    session = config / "pact-sessions" / "proj" / "sess-1"
    shim_dir = tmp_path / "local-bin"
    shim_dir.mkdir()
    (shim_dir / "repact").write_text(repact_shim.shim_text())
    (shim_dir / "repact").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim_dir}:{os.environ.get('PATH', '')}")
    assert guard.decide(_bash(f"repact accept 5 --session-dir {session}", config.parent)) is None
    (shim_dir / "repact").write_text(repact_shim.shim_text() + "\necho pwned\n")
    assert _refused(_bash(f"repact accept 5 --session-dir {session}", config.parent))


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


@pytest.mark.parametrize("name", sorted(HOOK_ROUTES))
def test_the_real_dispatcher_refuses_each_h1_route(config, name):
    tool, tool_input, frame = HOOK_ROUTES[name]
    task = config / "tasks" / "team-ops" / "5.json"
    tool_input = {key: value.replace("{T}", str(task)).replace("{{}}", "{}") if key != "content" else value
                  for key, value in tool_input.items()}
    payload = dict({"session_id": "sess-1", "hook_event_name": "PreToolUse", "cwd": str(config.parent),
                    "tool_name": tool, "tool_input": tool_input}, **frame)
    matcher = "Bash" if tool == "Bash" else "Edit,Write,MultiEdit,NotebookEdit"
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(PLUGIN), CLAUDE_PROJECT_DIR=str(config.parent))
    done = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "dispatch.py"), "PreToolUse", matcher],
                          input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60)
    assert "settings.task_state" in done.stdout + done.stderr, (done.stdout, done.stderr)
    assert '"deny"' in done.stdout, done.stdout
    assert json.loads(task.read_text())["status"] == "in_progress"


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


def test_a_plain_completed_task_with_a_handoff_does_not_close_its_row(ops_env, monkeypatch, tmp_path):
    w = ops_env
    store, ref = _linked_world(w, monkeypatch, tmp_path)
    path = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "tasks" / TEAM / "77.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": "77", "subject": "todo", "status": "completed",
                                "metadata": {"handoff": {"produced": ["x"], "evidence": {"head": _head(w)}}}}))
    store.start(ref, ptask=f"{TEAM}/77")
    row = W.Store.locate(w.repo).state().tasks[ref]
    assert CG.linked_ptasks(row)
    found = CG.problems(store.main, row, str(w.session_dir))
    assert any("repact dispatch" in problem for problem in found), found


def test_a_hand_completed_dispatched_task_does_not_close_its_row(ops_env, monkeypatch, tmp_path):
    w = ops_env
    _store, ref = _linked_world(w, monkeypatch, tmp_path)
    task_id = w.dispatch(refs=[ref])
    submit.teachback(w.coder(), task_id, teachback())
    submit.handoff(w.coder(), task_id, handoff(w.worktree(task_id), refs={ref: "done: parser.py"},
                                               evidence={"head": _head(w)}))
    _complete_by_hand(w, task_id)
    row = W.Store.locate(w.repo).state().tasks[ref]
    found = CG.problems(W.Store.locate(w.repo).main, row, str(w.session_dir))
    assert any("repact accept" in problem for problem in found), found
    # a forged acceptance naming an op id the ops log never ran
    _complete_by_hand(w, task_id, acceptance={"op_id": "op-0123456789abcdef", "by": "lead"})
    found = CG.problems(W.Store.locate(w.repo).main, row, str(w.session_dir))
    assert any("ops log" in problem for problem in found), found


def test_an_acceptance_whose_op_never_finished_does_not_close_its_row(ops_env, monkeypatch, tmp_path):
    w = ops_env
    _store, ref = _linked_world(w, monkeypatch, tmp_path)
    task_id = w.dispatch(refs=[ref])
    submit.teachback(w.coder(), task_id, teachback())
    submit.handoff(w.coder(), task_id, handoff(w.worktree(task_id), refs={ref: "done: parser.py"},
                                               evidence={"head": _head(w)}))
    op_id = records.op_id_of("accept", task_id, "0" * 16)
    with open(w.session_dir / records.OPS_LOG, "a") as handle:      # the intent, and no done row
        handle.write(json.dumps({"op": "accept", "op_id": op_id, "task": task_id}) + "\n")
    _complete_by_hand(w, task_id, acceptance={"op_id": op_id, "by": "lead"})
    row = W.Store.locate(w.repo).state().tasks[ref]
    assert CG.problems(W.Store.locate(w.repo).main, row, str(w.session_dir))
    assert CG.problems(W.Store.locate(w.repo).main, row, "")         # no session dir: fail closed


def test_repact_accept_still_lets_a_linked_row_close(ops_env, monkeypatch, tmp_path):
    w = ops_env
    _store, ref = _linked_world(w, monkeypatch, tmp_path)
    task_id = w.dispatch(refs=[ref])
    submit.teachback(w.coder(), task_id, teachback())
    submit.handoff(w.coder(), task_id, handoff(w.worktree(task_id), refs={ref: "done: parser.py"},
                                               evidence={"head": _head(w)}))
    result = accept.run(w.ctx(), task_id)
    assert result.exit_code == 0, result.lines
    row = W.Store.locate(w.repo).state().tasks[ref]
    assert CG.problems(W.Store.locate(w.repo).main, row, str(w.session_dir)) == []


def _update(task_id: str, **tool_input) -> dict:
    return {"tool_name": "TaskUpdate", "tool_input": dict(tool_input, taskId=task_id)}


def _plain_task(task_id: str) -> None:
    path = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "tasks" / TEAM / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": task_id, "subject": "todo", "status": "in_progress", "metadata": {}}))


@pytest.mark.parametrize("metadata", [
    {"dispatch": {"name": "coder-1"}},
    {"acceptance": {"by": "lead"}},
    {"teachback": {"accepted": {"by": "lead"}}},
    {"last_op": "op-x"},
    {"handoff": {"produced": ["x"]}},
    {"evidence_waiver": {"attended": True}},
    {"parked": {}},
    {"revision_number": 1},
    {"Revision_Number": 1},
])
def test_an_op_owned_key_is_refused_on_a_plain_task(ops_env, metadata):
    _plain_task("78")
    payload = _update("78", metadata=metadata)
    reason, rule = TWG.decide(payload, json.dumps(payload), TEAM)
    assert rule == TWG.RULE_RESERVED, (metadata, reason)


def test_status_completed_on_a_plain_task_passes_and_on_a_dispatched_one_is_refused(ops_env):
    _plain_task("79")
    payload = _update("79", status="completed")
    assert TWG.decide(payload, json.dumps(payload), TEAM) == (None, None)
    task_id = ops_env.dispatch()
    payload = _update(task_id, status="completed")
    assert TWG.decide(payload, json.dumps(payload), TEAM)[1] == TWG.RULE_COMPLETED


# -- the rejection count -----------------------------------------------------------------------

@pytest.mark.parametrize("value", [0, 1, "3"])
def test_revision_number_is_reserved(ops_env, value):
    task_id = ops_env.dispatch()
    payload = _update(task_id, metadata={"revision_number": value})
    assert TWG.decide(payload, json.dumps(payload), TEAM)[1] == TWG.RULE_RESERVED


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


@pytest.mark.parametrize("value", [1, 0, "3", None])
def test_a_reset_revision_number_still_parks_the_third_rejection(rejected_twice, value):
    w, task_id = rejected_twice

    def reset(task):
        if value is None:
            task["metadata"].pop("revision_number", None)
        else:
            task["metadata"]["revision_number"] = value
        return True
    w.store.update(TaskRef(TEAM, task_id), reset)
    third = accept.run(w.ctx(), task_id)
    assert "parked" in " ".join(third.lines), third.lines
    assert "parked" in w.task(task_id)["metadata"]
