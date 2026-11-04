"""tests/test_repact_init.py: bin/repact-init, the one setup command behind /repacted:init.

Each test drives the real script as a subprocess against a scratch config dir and a scratch
repository (CLAUDE_CONFIG_DIR always points into tmp_path; the real ~/.claude is never read).
The boundary rows feed it hostile settings files: each must be refused with nothing written.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
CLI = PLUGIN / "bin" / "repact-init"
SEED = json.loads((PLUGIN / "templates" / "user-settings.example.json").read_text())
TEAMS_FLAG = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"
TODO_FLAG = "CLAUDE_CODE_ENABLE_TODO_TOOLS"
EXECUTION = "REPACT_EXECUTION"
ATTR_LINE = ".repacted/tasks.jsonl merge=union"


@pytest.fixture
def env(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    return {**os.environ, "CLAUDE_CONFIG_DIR": str(config),
            "CLAUDE_PLUGIN_DATA": str(tmp_path / "data")}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def run(env, *args, cwd=None):
    return subprocess.run([sys.executable, str(CLI), "--no-doctor", *args], capture_output=True,
                          text=True, env=env, cwd=cwd, timeout=60, stdin=subprocess.DEVNULL)


def settings(env) -> Path:
    return Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json"


def backups(path: Path) -> list:
    return sorted(path.parent.glob(path.name + ".bak-*"))


def test_diff_backup_and_apply(env):
    target = settings(env)
    target.write_text('{"model": "opus"}\n')
    dry = run(env, "--dry-run")
    assert dry.returncode == 0 and "dry run, nothing written" in dry.stdout
    assert any(line.startswith("+") and TODO_FLAG in line for line in dry.stdout.splitlines())
    assert json.loads(target.read_text()) == {"model": "opus"} and not backups(target)
    unconfirmed = run(env)
    assert unconfirmed.returncode == 3 and json.loads(target.read_text()) == {"model": "opus"}
    applied = run(env, "--yes")
    assert applied.returncode == 0, applied.stderr
    data = json.loads(target.read_text())
    assert data["env"][TODO_FLAG] == "1" and data["model"] == "opus"
    assert [json.loads(b.read_text()) for b in backups(target)] == [{"model": "opus"}]


def test_second_run_nothing_to_do(env):
    assert run(env, "--yes").returncode == 0
    before = settings(env).read_text()
    second = run(env, "--yes")
    assert second.returncode == 0 and "nothing to do" in second.stdout
    assert settings(env).read_text() == before and not backups(settings(env))


def test_task_tools_flag_added_and_kept_when_set(env):
    """The diff adds CLAUDE_CODE_ENABLE_TODO_TOOLS, and no longer the Agent
    Teams flag by default; a value the user already set is left alone, so a run with it set has
    nothing to do."""
    dry = run(env, "--dry-run")
    added = [line for line in dry.stdout.splitlines() if line.startswith("+")]
    assert any(TODO_FLAG in line for line in added)
    assert not any(TEAMS_FLAG in line or EXECUTION in line for line in added)
    settings(env).write_text(json.dumps({"env": {TODO_FLAG: "0"},
                                         "permissions": SEED["permissions"]}))
    again = run(env, "--yes")
    assert again.returncode == 0 and "nothing to do" in again.stdout
    assert json.loads(settings(env).read_text())["env"][TODO_FLAG] == "0"


def test_teams_writes_the_flag_and_the_mode_beside_each_other(env):
    """/repacted:init --teams: the Agent Teams flag and REPACT_EXECUTION=teams in the user env."""
    assert run(env, "--yes", "--teams").returncode == 0
    data = json.loads(settings(env).read_text())
    assert data["env"][TEAMS_FLAG] == "1" and data["env"][EXECUTION] == "teams"
    assert data["env"][TODO_FLAG] == "1"
    assert "nothing to do" in run(env, "--yes", "--teams").stdout


def test_doctor_line_names_the_state_not_counts(tmp_path, monkeypatch, capsys):
    """F7: the last line reads as one exit code and its meaning, never as three counts."""
    import importlib.util
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("repact_init_under_test", str(CLI))
    init = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    monkeypatch.setitem(sys.modules, loader.name, init)  # its dataclass looks itself up there
    loader.exec_module(init)
    fake = tmp_path / "fake-doctor"
    fake.write_text("import sys\nprint('AMBER 1. something')\nsys.exit(1)\n")
    monkeypatch.setattr(init, "DOCTOR", fake)
    init.run_doctor(tmp_path)
    assert capsys.readouterr().out.rstrip().splitlines()[-1] == (
        "doctor exit 1 = amber (codes: 0 green, 1 amber, 2 red)")


# ---- boundary: a settings file a merge could damage is refused, nothing written ----------------


