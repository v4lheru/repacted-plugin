"""
Location: pact-plugin/tests/test_autopilot_prose_guards.py
Summary: Every command commands/autopilot.md tells the lead to run passes the real PreToolUse
dispatcher (hooks/dispatch.py: worktree_guard, the settings check, env_guard) in the lead frame.
In a live run the old prose offered a shell helper `A() { python3 .../repact-autopilot
--session-dir '<session dir>' "$@"; }`, which the guards refuse (they cannot read through a
function body), so the lead lost a turn on every run. The prose now writes each program call out
in full with its plugin-absolute path.
Used by: pytest.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
PROSE = PLUGIN / "commands" / "autopilot.md"
SESSION = "11111111-2222-3333-4444-555555555555"
CODE = re.compile(r"`([^`]*\{plugin_root\}/bin/[^`]*)`")
FILLS = {"$ARGUMENTS": "--max-tasks 3 --max-usd 6", "[--pr <url>]": "", "<ref>": "T-1234abcd",
         "<branch>": "feat/x", "<why>": "needs a credential", "<question>": "which token?",
         "<id>": "7", "<file>": "/tmp/handoff.txt", "<url>": "https://example.com/pr/1"}


def _world(tmp_path: Path):
    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    config = tmp_path / "config"
    session_dir = config / "pact-sessions" / "proj" / SESSION
    session_dir.mkdir(parents=True)
    env = {key: value for key, value in os.environ.items()
           if key not in ("REPACT_AGENT_NAME", "CLAUDE_SESSION_ID", "REPACT_UNATTENDED")}
    env.update(CLAUDE_CONFIG_DIR=str(config), CLAUDE_CODE_SESSION_ID=SESSION,
               CLAUDE_PLUGIN_ROOT=str(PLUGIN), CLAUDE_PROJECT_DIR=str(repo))
    return repo, session_dir, env


def _verdict(command: str, repo: Path, env: dict) -> str:
    payload = {"session_id": SESSION, "hook_event_name": "PreToolUse", "cwd": str(repo),
               "tool_name": "Bash", "tool_input": {"command": command}}
    done = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "dispatch.py"), "PreToolUse", "Bash"],
                          input=json.dumps(payload), capture_output=True, text=True, env=env,
                          timeout=60)
    return done.stdout + done.stderr


def _lines(session_dir: Path):
    text = PROSE.read_text(encoding="utf-8")
    for raw in CODE.findall(text):
        line = raw.replace("{plugin_root}", str(PLUGIN)).replace("{session_dir}", str(session_dir))
        for hole, value in FILLS.items():
            line = line.replace(hole, value)
        assert "<" not in line and "{" not in line, f"unfilled placeholder in {raw!r}"
        yield raw, line


def test_every_autopilot_command_passes_the_guards_in_the_lead_frame(tmp_path):
    repo, session_dir, env = _world(tmp_path)
    lines = list(_lines(session_dir))
    assert len(lines) >= 10, lines                      # the prose still names its commands
    denied = [(raw, out) for raw, line in lines
              if '"deny"' in (out := _verdict(line, repo, env))]
    assert not denied, denied


def test_the_prose_offers_no_shell_helper(tmp_path):
    text = PROSE.read_text(encoding="utf-8")
    assert not re.search(r"\w\(\)\s*\{", text) and "`A " not in text, "a shell helper is back"
    # why: the guards refuse the helper form the old prose taught
    repo, session_dir, env = _world(tmp_path)
    helper = (f'A() {{ python3 "{PLUGIN}/bin/repact-autopilot" --session-dir \'{session_dir}\' '
              f'"$@"; }}; A next')
    assert '"deny"' in _verdict(helper, repo, env)
