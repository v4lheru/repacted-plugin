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


def test_it_still_refuses_a_team_a_peer_session_names_and_a_dark_store(home):
    config, session_dir = home
    peer = session_dir.parent / "sid-peer"
    peer.mkdir()
    (peer / "pact-session-context.json").write_text(json.dumps({"team_name": OLD}))
    result = run(OLD)
    assert result.returncode == 1 and "another session of this project" in result.stdout
    assert "repact-migrate" not in result.stdout
    (peer / "pact-session-context.json").unlink()
    (session_dir / "task-store-probe.json").write_text(json.dumps({"halt": True}))
    result = run(OLD)
    assert result.returncode == 1 and "not writing task files" in result.stdout
    assert names(config, OLD) == ["1.json", "2.json", "3.json"] and names(config, NEW) == []


@pytest.mark.parametrize("args", [(), ("../" + OLD,), ("a/b",), ("team.dotted",), ("-x",), (OLD, NEW), ("",)])
def test_a_bad_team_argument_is_a_usage_error_with_one_example(home, args):
    config, _ = home
    result = run(*args)
    assert result.returncode == 2
    assert result.stderr.count("bin/repact-migrate\" session-0a1b2c3d") == 1
    assert names(config, OLD) == ["1.json", "2.json", "3.json"] and names(config, NEW) == []


def test_no_session_context_or_the_sessions_own_team_moves_nothing(home):
    config, _ = home
    assert run(OLD, CLAUDE_CODE_SESSION_ID="sid-unknown").returncode == 1
    assert run(OLD, CLAUDE_CODE_SESSION_ID="../x").returncode == 1
    own = run(NEW)
    assert own.returncode == 0 and "own team" in own.stdout
    assert names(config, OLD) == ["1.json", "2.json", "3.json"] and names(config, NEW) == []


@pytest.mark.parametrize("journal", ["other-team-only", "no-journal", "name-in-another-row"])
def test_a_team_this_session_never_started_on_is_refused_and_untouched(home, journal):
    """Only a team a session_start row of this session's own journal names is its earlier
    team: any other name could be the store of a live session elsewhere."""
    config, session_dir = home
    other = "session-other003"
    (config / "tasks" / other).mkdir()
    (config / "tasks" / other / "1.json").write_text(json.dumps({
        "id": "1", "subject": "s", "description": "d", "status": "pending", "owner": "coder-a",
        "blocks": [], "blockedBy": [], "metadata": {}}))
    if journal == "no-journal":
        (session_dir / "session-journal.jsonl").unlink()
    elif journal == "name-in-another-row":
        with (session_dir / "session-journal.jsonl").open("a") as handle:
            handle.write(json.dumps({"v": 1, "type": "task_event", "ts": "2026-10-11T09:01:00Z",
                                     "team": other, "source": other}) + "\n")
    result = run(other)
    assert result.returncode == 1 and "was never this session's team" in result.stdout
    assert names(config, other) == ["1.json"] and names(config, NEW) == []
