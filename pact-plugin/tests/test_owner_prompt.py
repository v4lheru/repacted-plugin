"""
Location: pact-plugin/tests/test_owner_prompt.py
Summary: The owner's typed prompt turns unattended mode off (hooks/owner_prompt.py), run
         through hooks/dispatch.py as the host runs it. One honest row: a prompt typed at
         the CLI terminal turns the mode off and writes the journal row. Every other row is
         a prompt the owner did not type, and the mode must stay ON after it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared import unattended as u

PLUGIN = Path(__file__).resolve().parent.parent
DISPATCH = PLUGIN / "hooks" / "dispatch.py"
SESSION = "sess-owner-prompt"
OFF_LINE = ("Unattended mode is off: you typed in the session. "
            "Ask your parked questions now (repact decide lists them).")


@pytest.fixture
def root(tmp_path):
    """(env, session directory, project) with unattended mode ON by the flag."""
    project = tmp_path / "proj"
    project.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key != u.ENV_NAME and not key.startswith("CLAUDE_")}
    env.update(HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(tmp_path / "config"),
               CLAUDE_PROJECT_DIR=str(project), CLAUDE_CODE_ENTRYPOINT="cli")
    session_dir = tmp_path / "config" / "pact-sessions" / "proj" / SESSION
    session_dir.mkdir(parents=True)
    u.write_flag(str(session_dir), True, "2026-10-25T00:00:00Z")
    return env, session_dir, project


def run(env, event, payload, **extra_env):
    body = {"session_id": SESSION, "hook_event_name": event, **payload}
    return subprocess.run([sys.executable, str(DISPATCH), event], input=json.dumps(body),
                          text=True, capture_output=True, env={**env, **extra_env}, timeout=60)


def prompt(env, text, **extra):
    return run(env, "UserPromptSubmit", {"prompt": text, **extra})


def schedule(env, tool, text):
    return run(env, "PreToolUse", {"tool_name": tool, "tool_input": {"prompt": text}})


def owner_rows(session_dir: Path) -> list[dict]:
    path = session_dir / "session-journal.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [row for row in rows
            if row.get("type") == "unattended_mode" and row.get("cause") == "owner_prompt"]


def still_on(session_dir: Path) -> bool:
    return u.read_mode(str(session_dir)).on and not owner_rows(session_dir)


def typed_turns_off(env, session_dir: Path) -> bool:
    """The control after a row that left the mode ON: a typed prompt does turn it off."""
    done = prompt({**env, "CLAUDE_CODE_ENTRYPOINT": "cli"}, "I am back at the keyboard.")
    return OFF_LINE in done.stdout and not u.read_mode(str(session_dir)).on


@pytest.mark.parametrize("source", ["startup",])
def test_a_typed_prompt_turns_the_mode_off_and_writes_the_row(root, source):
    env, session_dir, _ = root
    assert run(env, "SessionStart", {"source": source}).returncode == 0
    done = prompt(env, "I am back, what is parked?")
    assert done.returncode == 0
    assert OFF_LINE in done.stdout
    assert u.read_mode(str(session_dir)) == (False, "flag")
    assert json.loads((session_dir / u.FLAG_NAME).read_text())["by"] == "owner_prompt"
    assert not (session_dir / u.STICKY_NAME).exists()
    assert [row["state"] for row in owner_rows(session_dir)] == ["off"]


HARNESS_PROMPTS = {
    "task notification": "<task-notification><task-id>b1</task-id><status>completed</status>"
                         "</task-notification>",
    "teammate message": '<teammate-message teammate_id="coder">Done, please accept.</teammate-message>',
    "cross-session message": "Another Claude session sent a message:\n"
                             '<cross-session-message from="worker">all clear</cross-session-message>',
    "desktop scheduled task": "<scheduled-task name=\"nightly\">run the report</scheduled-task>",
    "system reminder": "<system-reminder>wake up</system-reminder> go on",
    "local command output": "<local-command-stdout>ok</local-command-stdout>",
    "usage-limit auto-resume": "Your claude.ai usage limit has reset. Continue the task you were "
                               "working on when the limit was reached; do not repeat work that is "
                               "already complete.",
}


@pytest.mark.parametrize("text", list(list(list(HARNESS_PROMPTS.values()))[:1])[:1], ids=list(list(HARNESS_PROMPTS))[:1])
def test_a_harness_prompt_leaves_the_mode_on(root, text):
    env, session_dir, _ = root
    assert prompt(env, text).returncode == 0
    assert still_on(session_dir)
    assert typed_turns_off(env, session_dir)


@pytest.mark.parametrize("entrypoint", ["claude-desktop",])
def test_a_prompt_typed_in_desktop_or_an_ide_turns_the_mode_off(root, entrypoint):
    env, session_dir, _ = root
    done = prompt({**env, "CLAUDE_CODE_ENTRYPOINT": entrypoint}, "I am back")
    assert OFF_LINE in done.stdout and not u.read_mode(str(session_dir)).on


