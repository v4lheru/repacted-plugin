"""
Location: pact-plugin/tests/test_brief_live_path.py
Summary: The coder's path through a dispatched brief, run against the real PreToolUse dispatcher
(hooks/dispatch.py: worktree_guard, the settings check with settings.task_state, the env guard):
the Write of the brief's teachback draft is allowed (the drafts once sat under the
protected <config>/pact-sessions/ and every dispatch died), and the brief's own `repact submit`
line passes the guards, names this plugin's launcher (a bare `repact` once resolved on PATH to
another install) and stores the teachback.
Used by: pytest.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

from fixtures.ops_env import SESSION_ID, World, ops_env, teachback  # noqa: F401  # fixture
from shared.ops import brief

PLUGIN = Path(__file__).resolve().parent.parent
CODER_FRAME = {"agent_id": "a1", "agent_type": "pact-backend-coder"}
STEP_ONE = re.compile(r"1\. Teachback: fill .*?, then run `(?P<command>[^`]+)`")


def _env(world: World) -> dict:
    env = {key: value for key, value in os.environ.items()
           if key not in ("REPACT_AGENT_NAME", "CLAUDE_SESSION_ID", "REPACT_UNATTENDED")}
    env.update(CLAUDE_CONFIG_DIR=str(world.root / "config"), CLAUDE_CODE_SESSION_ID=SESSION_ID,
               CLAUDE_PLUGIN_ROOT=str(PLUGIN), CLAUDE_PROJECT_DIR=str(world.repo))
    return env


def _hook(world: World, tool: str, tool_input: dict, cwd: str) -> str:
    payload = dict({"session_id": SESSION_ID, "hook_event_name": "PreToolUse", "cwd": cwd,
                    "tool_name": tool, "tool_input": tool_input}, **CODER_FRAME)
    matcher = "Bash" if tool == "Bash" else "Edit,Write,MultiEdit,NotebookEdit"
    done = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "dispatch.py"), "PreToolUse", matcher],
                          input=json.dumps(payload), capture_output=True, text=True, env=_env(world),
                          timeout=60)
    return done.stdout + done.stderr


def test_the_coder_fills_the_draft_and_submits_through_the_real_guards(ops_env: World):
    task_id = ops_env.dispatch()
    where = brief.paths(ops_env.session_dir, task_id)
    worktree = ops_env.worktree(task_id)
    draft = str(where["teachback"])
    out = _hook(ops_env, "Write", {"file_path": draft, "content": json.dumps(teachback())}, worktree)
    assert '"deny"' not in out, out
    # outside the config dir (never protected session state) and outside the worktree (never committed)
    assert not draft.startswith(str(ops_env.root / "config")) and not draft.startswith(worktree), draft
    where["teachback"].write_text(json.dumps(teachback()), encoding="utf-8")

    command = STEP_ONE.search(where["brief"].read_text(encoding="utf-8"))["command"]
    out = _hook(ops_env, "Bash", {"command": command}, worktree)
    assert '"deny"' not in out, out
    done = subprocess.run(["bash", "-c", command], capture_output=True, text=True, env=_env(ops_env),
                          cwd=worktree, timeout=120)
    assert done.returncode == 0 and "Accepted automatically" in done.stdout, (done.stdout, done.stderr)
    assert ops_env.task(task_id)["metadata"]["teachback"]["accepted"]["by"] == "auto"


def test_the_brief_names_this_plugins_launcher(ops_env: World):
    task_id = ops_env.dispatch()
    text = brief.paths(ops_env.session_dir, task_id)["brief"].read_text(encoding="utf-8")
    launcher = shlex.quote(str(PLUGIN / "bin" / "repact"))
    submits = re.findall(r"`([^`]*\bsubmit {}[^`]*)`".format(task_id), text)
    assert len(submits) == 2, text
    assert all(line.startswith(f"{launcher} submit ") for line in submits), submits


def test_the_brief_names_this_plugins_tcount_and_a_named_red_run(ops_env: World):
    """A bare `tcount` ran another install on PATH and its runs went to that
    install's ledger, and a whole-suite red named no test file, so accept found neither."""
    task_id = ops_env.dispatch()
    text = brief.paths(ops_env.session_dir, task_id)["brief"].read_text(encoding="utf-8")
    tests = text.split("## Tests", 1)[1].split("## Steps", 1)[0]
    tcount = shlex.quote(str(PLUGIN / "bin" / "tcount"))
    assert f"`{tcount} <cmd>`" in tests and "`tcount " not in tests, tests
    assert "NAME the test file" in tests and "before any passing run" in tests, tests


def test_the_dispatch_next_line_names_the_plugin_agent_type(ops_env: World):
    """Under a plugin install with no user-layer agents only the plugin-prefixed
    agent type exists; a project's own agent keeps its bare name."""
    from fixtures.ops_env import CODER
    from shared.ops import dispatch as dispatch_op

    def next_line(**overrides) -> str:
        args = dict(name=CODER, agent="pact-backend-coder", subject="Fix the parser",
                    mission="Fix the empty-input case in parser.py.", variety=[1, 1, 1, 1],
                    kind="fix", repo=str(ops_env.repo), branch="fix/parser")
        args.update(overrides)
        return [line for line in dispatch_op.run(ops_env.ctx(), **args).lines if line.startswith("Next:")][0]

    assert 'subagent_type="repacted:pact-backend-coder"' in next_line()
    assert 'subagent_type="acme-billing-coder"' in next_line(agent="acme-billing-coder", name="c2",
                                                             branch="fix/p2")
