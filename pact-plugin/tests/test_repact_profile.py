"""`repact-profile build`: the repo profile that feeds every brief.

Location: pact-plugin/tests/test_repact_profile.py. Covers bin/repact-profile: the test
commands of this repo's run-ledger rows with their typical duration, the "briefs went wrong when"
list from this repo's rejection and amendment reasons (scrubbed), and the task-log opt-in.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
PROFILE = PLUGIN / "bin" / "repact-profile"
TOKEN = "ghp_" + "A1b2C3d4" * 5          # built at run time: a GitHub-token shape the scrubber must hide
TESTS = "uv run --no-project --with pytest python -m pytest -q tests/test_a.py"


def _repo(root: Path, enabled: bool = True) -> Path:
    (root / ".git").mkdir(parents=True)
    if enabled:
        (root / ".repacted").mkdir()
        (root / ".repacted" / "tasks.jsonl").write_text("", encoding="utf-8")
    return root


def _ledger(data: Path, rows: list) -> None:
    (data / "runs").mkdir(parents=True)
    base = {"v": 1, "ts": "2026-10-18T10:00:00Z", "runner": "pytest", "source": "hook"}
    lines = [json.dumps({**base, "log_path": f"/l/{n}", **row}) for n, row in enumerate(rows)]
    (data / "runs" / "ledger.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _session(config: Path, folder: str, session: str, project_dir: Path, metadata: dict) -> None:
    where = config / "pact-sessions" / folder / session
    where.mkdir(parents=True)
    (where / "pact-session-context.json").write_text(
        json.dumps({"project_dir": str(project_dir), "session_id": session}), encoding="utf-8")
    event = {"type": "task_metadata_snapshot", "ts": "2026-10-18T10:00:00Z", "task_id": "7",
             "metadata": metadata}
    (where / "session-journal.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")


def _build(tmp_path: Path, repo: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(tmp_path / "config"),
               CLAUDE_PLUGIN_DATA=str(tmp_path / "data"))
    return subprocess.run([sys.executable, str(PROFILE), "build", "--repo", str(repo)],
                          capture_output=True, text=True, env=env, timeout=60)


def _fixture(tmp_path: Path, enabled: bool = True) -> Path:
    repo = _repo(tmp_path / "app", enabled)
    other = _repo(tmp_path / "other")
    _ledger(tmp_path / "data", [
        {"cwd": str(repo), "cmd": TESTS, "duration_s": 10},
        {"cwd": str(repo / "src"), "cmd": TESTS.replace("test_a", "test_b"), "duration_s": 30},
        {"cwd": str(repo), "cmd": TESTS, "duration_s": 20},
        {"cwd": str(other), "cmd": "npm test -- other-only", "duration_s": 99},
        {"cwd": str(repo), "cmd": f'cd "{repo}" && nice -n 19 tcount python3 -m pytest -q tests/t.py 2>&1 | tail -3'},
    ])
    config = tmp_path / "config"
    _session(config, "app", "s1", repo, {
        "handoff_rejection": {"reason": f"The brief named the wrong test file; token {TOKEN} leaked."},
        "amend_1": {"kind": "correction", "text": "Scope missed the CLI entry point.", "from": "team-lead"},
        "amend_2": {"kind": "ruling", "text": "Approved as proposed.", "from": "team-lead"},
    })
    _session(config, "app", "s2", repo, {
        "teachback_rejection": {"reason": "The brief named the wrong test file; token x leaked."},
    })
    _session(config, "other", "s3", other, {
        "handoff_rejection": {"reason": "OTHER-PROJECT reason must not appear."},
    })
    return repo


def test_build_writes_test_commands_and_reasons_from_this_repo_only(tmp_path):
    repo = _fixture(tmp_path)
    result = _build(tmp_path, repo)
    assert result.returncode == 0, result.stderr
    text = (repo / ".repacted" / "profile.md").read_text(encoding="utf-8")
    assert "uv run --no-project --with pytest python -m pytest -q" in text
    assert "3 runs" in text and "typical 20 s" in text
    assert "npm test" not in text
    assert "- `python3 -m pytest -q`: 1 run, duration not recorded" in text   # a cd chain
    assert "`cd" not in text
    assert "Scope missed the CLI entry point." in text
    assert "The brief named the wrong test file" in text
    assert "Approved as proposed." not in text          # a ruling is an answer, not a cause
    assert "OTHER-PROJECT" not in text


def test_reasons_are_scrubbed(tmp_path):
    repo = _fixture(tmp_path)
    assert _build(tmp_path, repo).returncode == 0
    text = (repo / ".repacted" / "profile.md").read_text(encoding="utf-8")
    assert TOKEN not in text
    assert TOKEN[:12] not in text


def test_no_task_log_writes_nothing(tmp_path):
    repo = _fixture(tmp_path, enabled=False)
    result = _build(tmp_path, repo)
    assert result.returncode == 0, result.stderr
    assert not (repo / ".repacted").exists()
    assert "not written" in result.stdout
