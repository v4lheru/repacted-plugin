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
    assert lines[1:] == [f"next: repact-payload 5 --team {TEAM} --key rejection"]
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


def test_the_teammate_text_names_repact_wait():
    skill = (PLUGIN / "skills" / "teammate" / "SKILL.md").read_text(encoding="utf-8")
    waiting = (PLUGIN / "skills" / "teammate" / "references" / "teammate-waiting.md").read_text(
        encoding="utf-8")
    assert "repact-wait" in skill and "repact-wait" in waiting


REJECT = {"reason": "r", "revision_number": 1}


@pytest.mark.parametrize("status, metadata, until, holds", [
    ("completed", {}, "completed,rejection", True),               # accepted before the wait began
    ("completed", {}, None, False),                               # no flag: waits as before
    ("in_progress", {"teachback_rejection": REJECT}, "completed,rejection", True),
    ("in_progress", {"teachback_rejection": REJECT, "revision_number": 2}, "rejection", False),
    ("in_progress", {"handoff_rejection": {**REJECT, "rev": 1}, "handoff": {"rev": 1}},
     "rejection", True),
    ("in_progress", {"handoff_rejection": {**REJECT, "rev": 1}, "handoff": {"rev": 2},
                     "revision_number": 2}, "rejection", False),  # answered with rev 2
    ("in_progress", {"teachback_rejection": {"revision_number": True}, "revision_number": 9},
     "rejection", True),                                          # unreadable: wake early
    ("in_progress", {"amend_1": {}, "handoff": {"amendments": {"amend_1": "ack"}}}, "amend", False),
    ("in_progress", {"amend_1": {}, "amend_2": {}, "handoff": {"amendments": {"amend_1": "ack"}}},
     "amend", True),
    ("in_progress", {"teachback_rejection": REJECT}, "completed", False),
])
def test_until_returns_at_once_only_when_a_condition_already_holds(home, status, metadata, until,
                                                                   holds):
    write_task(home, "5", metadata, status=status)
    code, out, _ = run("5", "--timeout", "0.3", *(("--until", until) if until else ()))
    assert (code, "already holds" in out) == ((0, True) if holds else (3, False)), out


@pytest.mark.parametrize("value", ["", "bogus", "completed,bogus", ",", "Completed"])
def test_an_unknown_until_condition_is_a_usage_error(home, value):
    write_task(home, "5", {}, status="completed")
    assert run("5", "--until", value, "--timeout", "0.3")[0] == 2


SKILL = PLUGIN / "skills" / "teammate" / "SKILL.md"


def test_the_post_accept_run_drops_until_so_it_cannot_spin():
    text = SKILL.read_text(encoding="utf-8")
    assert "HANDOFF accepted: run plain `repact-wait <task id>` (no `--until`) once more" in text


def test_both_rejection_steps_bump_revision_number_so_until_rejection_goes_stale():
    text = SKILL.read_text(encoding="utf-8")
    assert text.count("`metadata.revision_number` +1") == 2


# ─── CH-3: the next: line ────────────────────────────────────────────────────
READ = f"next: repact-payload 5 --team {TEAM} --key "
MARKER = "MARKER-VALUE-91c7"
OPEN = {"reason": MARKER, "corrections": [MARKER], "revision_number": 1}


@pytest.mark.parametrize("metadata, until, following", [
    ({"teachback_rejection": OPEN}, "rejection", [READ + "rejection"]),
    ({"amend_2": {"text": MARKER}}, "amend", [READ + "amend"]),
    # whatever --until named: completed returns it, the open keys still get their reader
    ({"handoff_rejection": OPEN, "amend_1": MARKER}, "completed",
     [f"{READ}rejection; {READ[6:]}amend"]),
    ({}, "completed", []),
    ({"teachback_rejection": OPEN, "revision_number": 2}, "completed", []),            # answered
    ({"amend_1": MARKER, "handoff": {"amendments": {"amend_1": "ack"}}}, "completed", []),
    ({"amend_x": MARKER, "amend_": MARKER, "amend_1 ": MARKER, "xamend_1": MARKER,
      "handoff_rejection": None, "handoff_rejections": MARKER}, "completed", []),      # not keys
    ({"teachback_rejection": MARKER}, "completed", [READ + "rejection"]),
    ({"handoff_rejection": 7, "amend_3": None}, "completed",
     [f"{READ}rejection; {READ[6:]}amend"]),
], ids=["rejection", "amend", "both", "nothing", "stale-rejection", "disposed-amend",
        "near-miss-keys", "string-rejection", "number-rejection-null-amend"])
def test_the_next_line_appears_only_for_a_rejection_or_an_amendment(home, metadata, until,
                                                                   following):
    write_task(home, "5", metadata, status="completed")
    code, out, err = run("5", "--until", until, "--timeout", "0.3")
    assert code == 0, err
    assert out.splitlines()[1:] == following
    assert MARKER not in out + err


def test_the_next_line_names_a_team_the_caller_passed(home):
    write_task(home, "5", {"amend_1": {"text": MARKER}}, status="completed", team=OTHER)
    code, out, _ = run("5", "--team", OTHER, "--until", "completed")
    assert code == 0
    assert out.splitlines()[1:] == [f"next: repact-payload 5 --team {OTHER} --key amend"]


def test_the_help_example_shows_the_three_condition_form():
    code, out, _ = run("--help")
    assert code == 0 and "repact-wait 34 --until completed,rejection,amend" in out


def test_the_waiter_holds_on_the_shared_rejection_predicate():
    """One predicate for the waiter and the reader: no private copy left to drift."""
    from importlib.machinery import SourceFileLoader
    from importlib.util import module_from_spec, spec_from_loader
    from shared import amendments
    loader = SourceFileLoader("_repact_wait_u2", str(CLI))
    waiter = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(waiter)
    assert waiter.open_rejection is amendments.open_rejection
    assert not hasattr(waiter, "_open_rejection")
