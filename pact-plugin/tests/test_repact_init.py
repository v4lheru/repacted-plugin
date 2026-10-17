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
    assert any(line.startswith("+") and TEAMS_FLAG in line for line in dry.stdout.splitlines())
    assert json.loads(target.read_text()) == {"model": "opus"} and not backups(target)
    unconfirmed = run(env)
    assert unconfirmed.returncode == 3 and json.loads(target.read_text()) == {"model": "opus"}
    applied = run(env, "--yes")
    assert applied.returncode == 0, applied.stderr
    data = json.loads(target.read_text())
    assert data["env"][TEAMS_FLAG] == "1" and data["model"] == "opus"
    assert [json.loads(b.read_text()) for b in backups(target)] == [{"model": "opus"}]


def test_second_run_nothing_to_do(env):
    assert run(env, "--yes").returncode == 0
    before = settings(env).read_text()
    second = run(env, "--yes")
    assert second.returncode == 0 and "nothing to do" in second.stdout
    assert settings(env).read_text() == before and not backups(settings(env))


def test_config_dir_honoured(env, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert run({**env, "HOME": str(home)}, "--yes").returncode == 0
    assert settings(env).is_file() and not (home / ".claude").exists()
    assert settings(env).stat().st_mode & 0o777 == 0o600


def test_merge_keeps_user_keys_and_values(env):
    mine = {"hooks": {"Stop": []}, "env": {TEAMS_FLAG: "0", "OTHER": "x"},
            "permissions": {"allow": ["Bash(ls)", SEED["permissions"]["allow"][0]],
                            "deny": ["Read(./secrets)"]}}
    settings(env).write_text(json.dumps(mine, indent=4))
    result = run(env, "--yes")
    assert result.returncode == 0 and f'settings.json: env.{TEAMS_FLAG} = "0"' in result.stdout
    data = json.loads(settings(env).read_text())
    assert data["env"] == {TEAMS_FLAG: "0", "OTHER": "x"} and data["hooks"] == {"Stop": []}
    allow = data["permissions"]["allow"]
    assert allow[:2] == mine["permissions"]["allow"] and len(allow) == len(set(allow))
    assert set(SEED["permissions"]["allow"]) <= set(allow)
    assert data["permissions"]["deny"] == ["Read(./secrets)"]


def test_repo_part_idempotent(env, repo):
    (repo / ".gitattributes").write_text("*.png binary")   # no trailing newline
    assert run(env, "--yes", "--repo", cwd=repo).returncode == 0
    assert json.loads((repo / ".claude" / "settings.json").read_text()) == {
        "agent": "repacted:pact-orchestrator"}
    assert (repo / ".repacted").is_dir()
    assert (repo / ".gitattributes").read_text() == f"*.png binary\n{ATTR_LINE}\n"
    second = run(env, "--yes", "--repo", cwd=repo)
    assert second.returncode == 0 and "nothing to do" in second.stdout
    assert (repo / ".gitattributes").read_text().count(ATTR_LINE) == 1


def test_repo_keeps_another_agent(env, repo):
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text('{"agent": "my-agent"}')
    result = run(env, "--yes", "--repo", str(repo))
    assert result.returncode == 0 and 'agent = "my-agent"' in result.stdout
    assert (repo / ".repacted").is_dir()
    assert json.loads((repo / ".claude" / "settings.json").read_text()) == {"agent": "my-agent"}


# ---- boundary: a settings file a merge could damage is refused, nothing written ----------------

@pytest.mark.parametrize("text", [
    '{"env": {',                              # truncated
    '[1, 2]',                                 # array at the top level
    '"just a string"',
    'null',
    '{"env": {}, "env": {"A": "1"}}',         # a repeated key the rewrite would silently drop
    "\ufeff{\"env\": {}}",                  # a byte-order mark
])
def test_hostile_settings_refused(env, text):
    settings(env).write_text(text, encoding="utf-8")
    result = run(env, "--yes")
    assert result.returncode == 1 and "refused" in result.stderr and "Way through" in result.stderr
    assert settings(env).read_text(encoding="utf-8") == text and not backups(settings(env))


def test_symlinked_settings_refused(env, tmp_path):
    real = tmp_path / "dotfiles-settings.json"
    real.write_text('{"model": "opus"}')
    settings(env).symlink_to(real)
    result = run(env, "--yes")
    assert result.returncode == 1 and "symbolic link" in result.stderr
    assert settings(env).is_symlink() and real.read_text() == '{"model": "opus"}'


def test_dangling_symlink_refused(env, tmp_path):
    settings(env).symlink_to(tmp_path / "missing.json")
    assert run(env, "--yes").returncode == 1
    assert not (tmp_path / "missing.json").exists()


def test_odd_value_types_kept_not_crashed(env):
    mine = {"permissions": "allow-all", "env": ["x"], "big": 10 ** 30, "név": "ünïcode"}
    settings(env).write_text(json.dumps(mine, ensure_ascii=False))
    result = run(env, "--yes")
    assert result.returncode == 0, result.stderr
    data = json.loads(settings(env).read_text(encoding="utf-8"))
    assert data == mine   # every clash keeps the user's value; nothing new could be added
    assert 'settings.json: permissions = "allow-all"' in result.stdout


def test_settings_is_a_directory_refused(env):
    settings(env).mkdir()
    assert run(env, "--yes").returncode == 1


def test_repo_outside_git_refused(env, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = run(env, "--yes", "--repo", cwd=plain)
    assert result.returncode == 1 and "--repo <path>" in result.stderr
    assert not settings(env).exists()
