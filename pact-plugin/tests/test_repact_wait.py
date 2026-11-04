"""
Location: pact-plugin/tests/test_repact_wait.py (1.5.0)
Summary: Tests for bin/repact-wait: one line on a task-file change with metadata keys and no
         values, a next: line naming the reader when the task holds a rejection or an
         undisposed amendment (the design notes CH-3),
         exit 3 on timeout, exit 4 on a missing or vanished file, exit 2 when the team
         is ambiguous; and the teammate text names it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
CLI = PLUGIN / "bin" / "repact-wait"
TEAM = "session-test0116"
OTHER = "session-test0117"
SESSION = "sid-wait"


@pytest.fixture
def home(tmp_path, monkeypatch):
    config = tmp_path / "config"
    for team in (TEAM, OTHER):
        (config / "tasks" / team).mkdir(parents=True)
    context = config / "pact-sessions" / "proj" / SESSION / "pact-session-context.json"
    context.parent.mkdir(parents=True)
    context.write_text(json.dumps({"team_name": TEAM}))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    return config


def write_task(config, task_id, metadata, status="in_progress", team=TEAM):
    path = config / "tasks" / team / f"{task_id}.json"
    path.write_text(json.dumps({"id": task_id, "owner": "wu", "status": status, "blockedBy": [],
                                "metadata": metadata}))
    return path


def start(*args):
    return subprocess.Popen([sys.executable, str(CLI), *args, "--interval", "0.05"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=dict(os.environ))


def run(*args):
    proc = start(*args)
    out, err = proc.communicate(timeout=30)
    return proc.returncode, out, err


def test_a_change_prints_one_line_of_keys_and_never_a_value(home):
    path = write_task(home, "5", {"intentional_wait": {"reason": "r"}, "handoff": {"rev": 1},
                                  "old": 1})
    proc = start("5", "--timeout", "20")
    time.sleep(0.5)
    path.write_text(json.dumps({"id": "5", "status": "completed", "blockedBy": ["4"], "metadata": {
        "handoff": {"rev": 1}, "intentional_wait": {"reason": "SECRET-VALUE-1"},
        "handoff_rejection": {"reason": "SECRET-VALUE-2"}}}))
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    lines = out.splitlines()
    # the reader, never the value
    assert lines[1:] == [f"next: repact inspect 5 --team {TEAM} --key rejection"]
    assert (f"#5 changed: status completed (was in_progress); blockedBy 4; metadata added: "
            "handoff_rejection; changed: intentional_wait; removed: old") in lines[0]
    assert "SECRET-VALUE" not in out + err


def test_timeout_exits_3(home):
    write_task(home, "5", {})
    code, out, _ = run("5", "--timeout", "0.3")
    assert code == 3 and "no change to #5" in out


def test_a_missing_or_vanished_file_exits_4(home):
    code, out, _ = run("9", "--team", TEAM, "--timeout", "1")
    assert code == 4 and "missing" in out
    path = write_task(home, "5", {})
    proc = start("5", "--timeout", "20")
    time.sleep(0.5)
    path.unlink()
    out, _ = proc.communicate(timeout=30)
    assert proc.returncode == 4 and "gone" in out


def test_an_ambiguous_team_exits_2_naming_the_teams(home, monkeypatch):
    write_task(home, "5", {})
    write_task(home, "5", {}, team=OTHER)
    registry = home / "pact-sessions" / ".teammate-registry.jsonl"
    registry.write_text(json.dumps({"session_id": SESSION, "value": f"wu@{OTHER}"}) + "\n")
    code, _, err = run("5", "--timeout", "1")
    assert code == 2 and TEAM in err and OTHER in err
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")             # no session: never guessed from a task file
    code, _, err = run("5", "--timeout", "1")
    assert code == 2 and f"teams holding #5: {TEAM}, {OTHER}" in err
    assert run("5", "--team", TEAM, "--timeout", "0.2")[0] == 3


REJECT = {"reason": "r", "revision_number": 1}


WAITING = PLUGIN / "skills" / "teammate" / "references" / "teammate-waiting.md"


# ─── CH-3: the next: line ────────────────────────────────────────────────────
READ = f"next: repact inspect 5 --team {TEAM} --key "
MARKER = "MARKER-VALUE-91c7"
OPEN = {"reason": MARKER, "corrections": [MARKER], "revision_number": 1}


