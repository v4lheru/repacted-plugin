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
    "f=~/.claude/tasks/team-ops/5.json; echo '{}' > $f",
    "f=$HOME/.claude/tasks/team-ops/5.json; cp /tmp/forged.json $f",
    "f=.claude/tasks/team-ops/5.json; cp /tmp/forged.json \"$f\"",
    "export d=tasks && echo '{}' > .claude/$d/team-ops/5.json",
    "d=tasks; echo '{}' > .claude/${d}/team-ops/5.json",
    "d=TASKS; echo x > .claude/$d/team-ops/5.json",
    "a=.claude; b=$a/tasks; echo x > $b/team-ops/5.json",
    "d=pact-sessions; rm .claude/$d/proj/sess-1/unattended.sticky.json",
    # the sticky marker and the flag through a variable
    f"s={SESSION}; rm $s/unattended.sticky.json; printf off > $s/unattended.json",
    f"s=~/{SESSION}; cat /tmp/rows.jsonl >> $s/ops.jsonl",
    # not resolvable, and the command names the config dir or pact-sessions
    "cd /tmp && f=~/.claude/tasks/team-ops/5.json; echo x > $f",
    "echo x > ~/.claude/$NOPE/team-ops/5.json",
    "f=$(ls -d ~/.claude/tasks/team-ops)/5.json; echo x > $f",
    "cd ~/.claude && cp /tmp/forged.json $TARGET",
    "s=$(cat /tmp/where); rm $s/pact-sessions/proj/sess-1/unattended.sticky.json",
    # a link into the task or session folders
    "echo '{}' > l7/5.json",
    "cp /tmp/forged.json l7/5.json",
    "rm l7/5.json",
    "mv lnk3/unattended.sticky.json /tmp/x",
    "rm lnk3/unattended.sticky.json",
    "echo row >> lnk3/ops.jsonl",
    "d=tasks; ln -s .claude/$d/team-ops l8 && echo x > l8/5.json",
    # find and xargs writers over a root that holds the folders
    "find .claude -name 5.json -exec sed -i '' s/in_progress/completed/ {} +",
    "find .claude -name 5.json -exec sed -i '' s/in_progress/completed/ {} \\;",
    "find . -name 5.json -exec cp /tmp/forged.json {} \\;",
    "find .claude -name unattended.sticky.json -delete",
    "find ~/.claude/pact-sessions -name 'unattended*' -delete",
    "find . -name 5.json | xargs sed -i '' s/in_progress/completed/",
    "find ~/.claude/tasks -name '*.json' -print0 | xargs -0 rm",
    "echo ~/.claude/tasks/team-ops/5.json | xargs rm",
    "d=tasks; find .claude/$d -name 5.json -delete",
    # code or a path computed by a command substitution, in a command that names the config dir
    "eval \"$(echo rm ~/.claude/tasks/team-ops/5.json)\"",
    "bash -c \"$(printf 'rm %s' ~/.claude/tasks/team-ops/5.json)\"",
    "eval \"$(echo \"$(echo rm ~/.claude/tasks/team-ops/5.json)\")\"",
    "rm \"$(echo ~/.claude/tasks)/team-ops/5.json\"",
])
def test_a_resolved_write_of_task_or_session_state_is_refused(home, command):
    assert _refused(_bash(command, home)), command                  # the lead (no marker)
    assert _refused(_bash(command, home, **CODER)), command


@pytest.mark.parametrize("command", [
    # the session's ops log emptied or truncated: plainly, through a variable, through a link
    f": > {SESSION}/ops.jsonl",
    f"> {SESSION}/ops.jsonl",
    f"truncate -s0 {SESSION}/ops.jsonl",
    f"cp /dev/null {SESSION}/ops.jsonl",
    f"s=~/{SESSION}; : > $s/ops.jsonl",
    f"s=~/{SESSION}; > $s/ops.jsonl",
    f"s=~/{SESSION}; truncate -s0 \"$s/ops.jsonl\"",
    f"s=$HOME/{SESSION}; cp /dev/null ${{s}}/ops.jsonl",
    ": > lnk3/ops.jsonl",
    "> lnk3/ops.jsonl",
    "truncate -s0 lnk3/ops.jsonl",
    "cp /dev/null lnk3/ops.jsonl",
    "s=lnk3; : > $s/ops.jsonl",
    "s=lnk3; truncate -s0 $s/ops.jsonl",
])
def test_emptying_the_sessions_ops_log_is_refused(home, command):
    assert _refused(_bash(command, home)), command
    assert _refused(_bash(command, home, **CODER)), command


@pytest.mark.parametrize("command", [
    # a command substitution next to the write
    "x=$(date); echo x > ~/.claude/tasks/team-ops/5.json",
    "echo \"$(date)\" > .claude/tasks/team-ops/5.json",
    f"n=$(wc -l < {SESSION}/ops.jsonl); : > {SESSION}/ops.jsonl",
    # a nested substitution
    "x=$(echo $(date)); rm .claude/pact-sessions/proj/sess-1/unattended.sticky.json",
    "echo \"$(echo \"$(date)\")\" > l7/5.json",
    f"s=$(echo $(echo ~/{SESSION})); : > $s/ops.jsonl",
    # a link made and used in one command
    "ln -s ~/.claude/tasks/team-ops l9 && echo x > l9/5.json",
    f"ln -sf ~/{SESSION} l9; cp /dev/null l9/ops.jsonl",
    f"s=~/{SESSION}; ln -s $s l9 && truncate -s0 l9/ops.jsonl",
])
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
    "f=~/.claude/tasks/team-ops/5.json; cat $f",
    "find .claude -name 5.json -exec cat {} \\;",
    "find .claude/tasks -name '*.json'",
    "find .claude -name '*.json' | xargs grep status",
    "cat ~/.claude/tasks/team-ops/5.json > /tmp/copy.json",
    "jq .status l7/5.json",
    "ls -la lnk3/",
    "jq .status ~/.claude/tasks/team-ops/5.json",
    "jq -r '.metadata.handoff' \"$HOME/.claude/tasks/team-ops/5.json\"",
    "cat ~/.claude/tasks/team-ops/5.json",
    "f=~/.claude/tasks/team-ops/5.json; jq '.metadata' \"$f\" | head -5",
    f"tail -n 20 ~/{SESSION}/ops.jsonl",
    f"wc -l < {SESSION}/ops.jsonl",
])
def test_reads_of_task_state_pass(home, command):
    assert guard.decide(_bash(command, home)) is None, command
    assert guard.decide(_bash(command, home, **CODER)) is None, command


@pytest.mark.parametrize("command", [
    # the repo's own work: a tasks/ folder of its own, find and xargs writers, unknown variables
    "find . -name '*.pyc' -delete",
    "find . -name x -exec rm {} +",
    "git ls-files | xargs sed -i '' s/a/b/",
    "out=build/x.json; mkdir -p build && echo x > $out",
    "cp a.txt $OUT/x.txt",
    "d=tasks; echo x > $d/1.json",
    "echo x > tasks/team-ops/5.json",
    "for f in a b; do echo x > $f.txt; done",
    "rm -rf \"$(mktemp -d)\"",
    "eval \"$(ssh-agent -s)\" && git push",
    "x=$(date); echo \"$x\" > build/stamp.txt",
    # the repo's own tasks/ folder through a variable
    "t=./tasks; cp a.json $t/1.json",
    "t=$PWD/tasks; echo x > \"$t/1.json\"",
    "t=tasks/team-ops; : > $t/5.json && truncate -s0 $t/5.json",
])
def test_the_repos_own_writes_pass(home, command):
    repo = home.parent / "repo"
    assert guard.decide(_bash(command, repo, **CODER)) is None, command


def test_the_repos_own_tasks_folder_named_by_an_absolute_variable_passes(home):
    tasks = home.parent / "repo" / "tasks"
    for command in (f"t={tasks}; echo x > $t/1.json", f"t={tasks}; cp /dev/null \"$t/2.json\""):
        assert guard.decide(_bash(command, home, **CODER)) is None, command


@pytest.mark.parametrize("verb", ["status", "add -A", "commit -m 'Fix the parser.'", "log --oneline -3"])
def test_git_in_a_worktree_passes(home, verb):
    repo = home.parent / "repo"
    command = f"git -C \"{repo}\" {verb}"
    assert guard.decide(_bash(command, home)) is None, command
    assert guard.decide(_bash(command, home, **CODER)) is None, command


def test_plugin_tools_may_name_the_session_dir_through_a_variable(home):
    session = home / ".claude" / "pact-sessions" / "proj" / "sess-1"
    for command in (f's={session}; "{PLUGIN}/bin/repact-journal-write" --type commit --session-dir "$s" --stdin',
                    f'python3 "{PLUGIN}/bin/repact-task" --session-dir "$SESSION_DIR" list',
                    f's={session}; python3 "{PLUGIN}/scripts/harvest.py" record --session-dir "$s" --team t'):
        assert guard.decide(_bash(command, home)) is None, command
    # the session dir spelled out, in the lead's and a coder's frame
    for command in (f'"{PLUGIN}/bin/repact-journal-write" --type commit --session-dir "{session}" --stdin',
                    f'python3 "{PLUGIN}/bin/repact-task" --session-dir {session} list',
                    f'"{PLUGIN}/bin/repact" inspect 5 --session-dir "{session}"'):
        assert guard.decide(_bash(command, home)) is None, command
        assert guard.decide(_bash(command, home, **CODER)) is None, command
    # an --out value of a plugin tool is still judged
    assert _refused(_bash(f's={session}; "{PLUGIN}/bin/repact" inspect 5 --out $s/ops.jsonl', home))


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


def test_a_coder_cannot_forge_an_acceptance_through_variables(row_world):
    w, _ref, task_id = row_world
    task_file = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "tasks" / TEAM / f"{task_id}.json"
    assert _hook(w, f"f={task_file}; cp forged.json $f", CODER) == "deny"
    assert _hook(w, f"s={w.session_dir}; cat rows.jsonl >> $s/ops.jsonl", CODER) == "deny"
    assert w.task(task_id)["status"] == "in_progress"


def test_the_lead_cannot_remove_the_sticky_marker_through_a_variable(row_world):
    w, _ref, _task_id = row_world
    command = (f"s={w.session_dir}; rm $s/unattended.sticky.json; "
               "printf '{\"v\":1,\"unattended\":false}' > $s/unattended.json")
    assert _hook(w, command, {}) == "deny"
    assert unattended.controls_on(str(w.session_dir)).on


def test_a_coder_cannot_write_its_task_through_find_exec(row_world):
    w, _ref, task_id = row_world
    command = (f"find {Path(os.environ['CLAUDE_CONFIG_DIR'])} -name {task_id}.json "
               "-exec sed -i '' s/in_progress/completed/ {} +")
    assert _hook(w, command, CODER) == "deny"


def test_control_env_zero_after_an_unattended_launch_still_parks(row_world, monkeypatch):
    w, _ref, task_id = row_world
    monkeypatch.setattr(EV, "verdict", lambda *a, **k: {"problems": ["no passing run"], "notes": []})
    (w.session_dir / "unattended.json").unlink()
    assert unattended.was_unattended(str(w.session_dir))
    done = subprocess.run([sys.executable, str(PLUGIN / "bin" / "_repact_ops.py"), "accept", task_id,
                           "--waive", WHY], cwd=str(w.repo), capture_output=True, text=True, timeout=120,
                          env=dict(os.environ, REPACT_UNATTENDED="0"))
    assert w.task(task_id)["status"] == "in_progress" and "parked" in w.task(task_id)["metadata"], done.stdout


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


@pytest.mark.parametrize("command", [
    "echo accept 5 | xargs repact",
    "xargs repact <<< 'accept 5'",
    "xargs repact < args.txt",
    "xargs -a args.txt repact",
    "echo 'accept 5' | xargs repact --session-dir /x",
    "echo accept | xargs -I{} repact {} 5",
    "echo accept | xargs -I VERB repact VERB 5",
    "printf 'accept\\n5\\n' | xargs python3 bin/_repact_ops.py",
    "xargs -n2 ./bin/_repact_ops.py < args",
    "find . -name accept -exec repact {} 5 \\;",
    "echo accept | xargs -I {} repact {} 5",
    "echo accept | xargs -I {} repact --session-dir /x {} 5",
    "echo accept | xargs -J % repact % 5",
    "echo accept | xargs -J % python3 bin/_repact_ops.py % 5",
])
def test_the_launcher_fed_its_verb_on_stdin_is_refused_in_every_frame(command):
    for actor in (LEAD, CODER_FRAME):
        reason = ops_frame_guard.decide(_cmd(command), actor)
        assert reason and reason.endswith(UNSEEN), (command, actor.name, reason)


@pytest.mark.parametrize("command,actor", [
    ("echo 5 | xargs repact accept", LEAD),
    ("echo 5 | xargs repact inspect", CODER_FRAME),
    ("echo 5 | xargs repact inspect", LEAD),
    ("xargs repact inspect < ids.txt", CODER_FRAME),
    ("echo 5 | xargs -I {} repact inspect {}", CODER_FRAME),
    ("echo 5 | xargs repact submit --handoff h.json", CODER_FRAME),
    ("repact accept 5", LEAD),
    ("echo repact | xargs echo", CODER_FRAME),
    ("xargs grep repact < files.txt", CODER_FRAME),
    ("find . -name '*.md' -exec grep -l 'repact accept' {} +", CODER_FRAME),
])
def test_a_visible_verb_under_xargs_is_judged_as_before(command, actor):
    assert ops_frame_guard.decide(_cmd(command), actor) is None, command
