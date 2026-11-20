"""
Location: pact-plugin/tests/test_front_door.py
Summary: The front door's readers through the real launcher: bare
`repact inspect` prints one status screen (open tasks, an op a crash left unfinished, a parked
task); `repact inspect --rule` explains one rule; `repact amend <id> --next` names the next key
and an amendment's next line names its reader; repact-status and pact-which-team refuse under
subagents. `repact decide` is in test_repact_decide.py, `work` and `resume` in
test_repact_launcher.py.
Used by: pytest.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from fixtures.ops_env import TEAM, World, ops_env  # noqa: F401  # fixture
from shared.ops import records

# Slow tier: deselected by the default full run, included by `run-tests --full --all`.
pytestmark = pytest.mark.slow

BIN = Path(__file__).resolve().parent.parent / "bin"
SCRUBBED = ("REPACT_AGENT_NAME", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDECODE",
            "REPACT_EXECUTION", "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS")


def _env(world: World, **extra) -> dict:
    env = {key: value for key, value in os.environ.items() if key not in SCRUBBED}
    env["CLAUDE_CONFIG_DIR"] = str(world.root / "config")
    return {**env, **extra}


def _repact(world: World, *args: str) -> subprocess.CompletedProcess:
    where = ["--team", TEAM, "--session-dir", str(world.session_dir)]
    return subprocess.run(["bash", str(BIN / "repact"), *args, *where], capture_output=True, text=True,
                          env=_env(world), cwd=str(world.repo), timeout=120)


def test_bare_inspect_prints_open_tasks_unfinished_ops_and_parked_tasks(ops_env: World):
    task_id = ops_env.dispatch()
    (ops_env.session_dir / "pact-session-context.json").write_text(
        json.dumps({"team_name": TEAM, "project_dir": str(ops_env.repo)}))
    records.begin(ops_env.ctx(), "accept", task_id, "0" * 12)            # a crash before the task write
    path = ops_env.root / "config" / "tasks" / TEAM / f"{task_id}.json"
    task = json.loads(path.read_text())
    task["metadata"]["parked"] = {"question": "Ship it without the waiver?"}
    path.write_text(json.dumps(task))

    done = _repact(ops_env, "inspect")
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert any(f"#{task_id}" in line and "coder-1" in line for line in lines), done.stdout
    assert any(line.startswith(f"Unfinished: accept #{task_id}") for line in lines), done.stdout
    assert f"Parked: #{task_id}: Ship it without the waiver? -> repact decide" in lines, done.stdout


