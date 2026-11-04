"""tests/test_repact_migrate.py: bin/repact-migrate, the lead's explicit migration after a crash.

It runs the function SessionStart runs and skips the standing-config check only. Each test
drives the real script as a subprocess against a scratch config dir.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parent.parent / "bin" / "repact-migrate"
OLD, NEW, SESSION = "session-old00001", "session-new00002", "sid-migrate"


@pytest.fixture
def home(tmp_path, monkeypatch):
    config, project = tmp_path / "config", tmp_path / "project"
    project.mkdir()
    for team in (OLD, NEW):
        (config / "tasks" / team).mkdir(parents=True)
    for n, status in (("1", "in_progress"), ("2", "pending"), ("3", "completed")):
        (config / "tasks" / OLD / f"{n}.json").write_text(json.dumps({
            "id": n, "subject": f"s{n}", "description": "d", "status": status, "owner": "coder-a",
            "blocks": [], "blockedBy": [], "metadata": {}}))
    (config / "teams" / OLD).mkdir(parents=True)
    (config / "teams" / OLD / "config.json").write_text("{}")   # the earlier life crashed
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    from shared.pact_context import reconstruct_session_dir
    session_dir = Path(reconstruct_session_dir(str(project), SESSION))
    session_dir.mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(json.dumps({
        "team_name": NEW, "session_id": SESSION, "project_dir": str(project),
        "plugin_root": "", "started_at": "2026-10-11T10:00:00Z"}))
    (session_dir / "session-journal.jsonl").write_text(json.dumps({   # the earlier life's start
        "v": 1, "type": "session_start", "ts": "2026-10-11T09:00:00Z", "team": OLD}) + "\n")
    return config, session_dir


def run(*args, **env):
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                          env={**os.environ, **env}, timeout=60)


def names(config, team):
    return sorted(p.name for p in (config / "tasks" / team).iterdir() if p.name != ".highwatermark")


def test_it_moves_with_a_standing_config_writes_the_rows_and_a_second_run_is_a_no_op(home, monkeypatch):
    config, session_dir = home
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")   # a Bash call may not carry it
    result = run(OLD)
    assert result.returncode == 0, result.stderr
    assert f"Resume: moved 2 open task(s) from team `{OLD}` into this team. Ids kept." in result.stdout
    assert names(config, OLD) == ["1.json.migrated", "2.json.migrated", "3.json"]
    assert names(config, NEW) == ["1.json", "2.json"]
    rows = [json.loads(line) for line in (session_dir / "session-journal.jsonl").read_text().splitlines()]
    assert [(r["type"], r["kind"], r["task_id"]) for r in rows if r["type"] != "session_start"] == [
        ("task_event", "migrated", "1"), ("task_event", "migrated", "2")]
    again = run(OLD)
    assert again.returncode == 0 and "Nothing to move" in again.stdout
    assert names(config, NEW) == ["1.json", "2.json"]


def test_a_source_left_standing_is_renamed_only_when_this_sessions_journal_row_names_its_copy(home):
    """The journal row is the proof of a copy: with it an edited copy is recognised and the
    source is only renamed; with the row gone the task is copied again, never just retired."""
    config, session_dir = home
    assert run(OLD).returncode == 0
    old, new, journal = config / "tasks" / OLD, config / "tasks" / NEW, session_dir / "session-journal.jsonl"
    copy = json.loads((new / "1.json").read_text())
    (new / "1.json").write_text(json.dumps({**copy, "description": "amended after the copy"}))
    (old / "1.json.migrated").rename(old / "1.json")            # the rename that never landed
    again = run(OLD)
    assert again.returncode == 0 and "finished an earlier move" in again.stdout, again.stdout
    assert names(config, NEW) == ["1.json", "2.json"] and names(config, OLD)[0] == "1.json.migrated"
    (old / "1.json.migrated").rename(old / "1.json")
    journal.write_text("".join(line + "\n" for line in journal.read_text().splitlines()
                               if json.loads(line)["type"] == "session_start"))
    third = run(OLD)
    assert third.returncode == 0 and "moved 1 open task(s)" in third.stdout, third.stdout
    assert names(config, NEW) == ["1.json", "2.json", "3.json"]


