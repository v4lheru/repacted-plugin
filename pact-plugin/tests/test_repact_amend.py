"""
Location: pact-plugin/tests/test_repact_amend.py
Summary: Tests for bin/repact-amend: the next free amend_<n>, the paste-ready metadata
         object, the one-line wake (id and digest, no text), the commit kind and its sha, read-only
         behaviour, and its exit codes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parent.parent / "bin" / "repact-amend"
TEAM = "session-test0003"
SESSION = "sid-amend"


@pytest.fixture
def home(tmp_path, monkeypatch):
    config = tmp_path / "config"
    (config / "tasks" / TEAM).mkdir(parents=True)
    context = config / "pact-sessions" / "proj" / SESSION / "pact-session-context.json"
    context.parent.mkdir(parents=True)
    context.write_text(json.dumps({"team_name": TEAM}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    return config


def write_task(config, task_id, metadata):
    path = config / "tasks" / TEAM / f"{task_id}.json"
    path.write_text(json.dumps({"id": task_id, "owner": "wu-c", "status": "in_progress",
                                "metadata": metadata}))
    return path


def run(*args):
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                          env=dict(os.environ), timeout=30)


def test_next_prints_the_next_key_metadata_and_notify(home):
    write_task(home, "34", {"amend_1": {"text": "a", "kind": "ruling"},
                            "amend_3": {"text": "b", "kind": "note"}})
    result = run("next", "34", "--kind", "premise", "--text", "The data says X.")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"next: amend_4 on #34 (team {TEAM}, owner wu-c, status in_progress)"
    assert lines[1] == "existing: amend_1 (ruling), amend_3 (note)"
    metadata = json.loads(lines[3])
    assert list(metadata) == ["amend_4"]
    value = metadata["amend_4"]
    assert (value["text"], value["kind"], value["from"]) == ("The data says X.", "premise", "team-lead")
    assert value["at"]
    assert lines[4] == 'wake (send after the line "Amendment amend_4 stored on #34"; its digest must match):'
    from shared.payload_digest import payload_digest
    assert lines[5] == f"[team-lead→wu-c] Task #34: amended on disk, digest {payload_digest(value)}."
    assert "The data says X." not in lines[5] and len(lines) == 6


def test_a_commit_confirmation_carries_its_sha(home):
    write_task(home, "34", {})
    result = run("next", "34", "--kind", "commit", "--sha", "5C3AE4D7", "--text", "committed Add the gate.")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout.splitlines()[3])["amend_1"]
    assert (value["kind"], value["sha"], value["text"]) == ("commit", "5c3ae4d7", "committed Add the gate.")
    assert run("next", "34", "--kind", "halt", "--text", "Stop: HALT SECURITY.").returncode == 0


@pytest.mark.parametrize("args", [
    ("--kind", "commit", "--text", "committed x"),
    ("--kind", "ruling", "--sha", "5c3ae4d7", "--text", "x"), ("--sha", "5c3ae4d7"),
    ("--kind", "halt", "--sha", "5c3ae4d7", "--text", "x"),
    ("--kind", "commit", "--sha", "5c3ae4", "--text", "x"), ("--kind", "commit", "--sha", "a" * 41, "--text", "x"),
    ("--kind", "commit", "--sha", "5c3ae4dz", "--text", "x"), ("--kind", "commit", "--sha", "", "--text", "x"),
    ("--kind", "commit", "--sha", "5c3ae4d7\n", "--text", "x"), ("--kind", "commit", "--sha", "5c3ae4d7"),
])
def test_a_sha_that_does_not_fit_the_kind_is_a_usage_error(home, args):
    write_task(home, "12", {})
    result = run("next", "12", *args)
    if args == ("--kind", "commit", "--sha", "5c3ae4d7"):      # a placeholder text with a good sha prints
        assert result.returncode == 0 and '"sha": "5c3ae4d7"' in result.stdout
        assert "digest <digest from the stored line>." in result.stdout
        return
    assert result.returncode == 2 and "sha" in result.stderr
    assert result.stderr.count("repact-amend next") == 1


def test_the_team_is_this_sessions_and_never_guessed_from_a_task_file(home, monkeypatch):
    write_task(home, "12", {})
    result = run("next", "12")
    assert result.returncode == 0 and result.stdout.startswith(f"next: amend_1 on #12 (team {TEAM},")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")            # T-d71d2961: the one team holding #12 is no answer
    result = run("next", "12")
    assert result.returncode == 4 and "task 12 not found" in result.stderr
    assert run("next", "12", "--team", TEAM).returncode == 0


def test_a_sibling_teams_task_of_the_same_id_is_never_numbered_from(home):
    write_task(home, "34", {"amend_1": {"text": "ours", "kind": "note"}})
    sibling = home / "tasks" / "session-sibling"
    sibling.mkdir()
    (sibling / "34.json").write_text(json.dumps({"id": "34", "owner": "other", "metadata": {}}))
    os.utime(sibling / "34.json", (4e9, 4e9))               # the sibling's file is the freshest
    result = run("next", "34")
    assert result.returncode == 0 and result.stdout.startswith(f"next: amend_2 on #34 (team {TEAM},")


def test_text_file_and_placeholder(home, tmp_path):
    write_task(home, "12", {})
    note = tmp_path / "note.txt"
    note.write_text("From a file.\n")
    assert '"text": "From a file."' in run("next", "12", "--text-file", str(note)).stdout
    assert '"text": "<text>"' in run("next", "12").stdout


def test_it_never_writes_the_task(home):
    path = write_task(home, "12", {"amend_1": {"text": "a"}})
    before = (path.read_bytes(), os.stat(path).st_mtime_ns)
    assert run("next", "12", "--text", "x").returncode == 0
    assert (path.read_bytes(), os.stat(path).st_mtime_ns) == before


def test_unknown_task_exits_4(home):
    result = run("next", "999", "--team", TEAM)
    assert result.returncode == 4 and "not found" in result.stderr


@pytest.mark.parametrize("args", [(), ("next",), ("bogus", "12"), ("next", "12", "--kind", "decree"),
                                  ("next", "12", "--text", "a", "--text-file", "b")])
def test_usage_errors_exit_2_with_one_example(home, args):
    write_task(home, "12", {})
    result = run(*args)
    assert result.returncode == 2
    assert result.stderr.rstrip().endswith('repact-amend next 34 --kind ruling --text "Use the placeholder class, not an exemption."')
    assert result.stderr.count("repact-amend next") == 1


def test_help_lists_examples(home):
    result = run("--help")
    assert result.returncode == 0 and "examples:" in result.stdout and "exit: 0 printed" in result.stdout
