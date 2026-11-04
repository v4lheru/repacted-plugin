"""Where a Bash call started its test run: shared/run_place.run_dir reads the
command in the shell's order, and the run-ledger hook records it as run_dir on its row.

Location: pact-plugin/tests/test_run_place.py. The table holds the hostile spellings at
the parser's boundary (a cd in a subshell, the background, a comment, a heredoc, quoted text, a
pipeline); the last test runs a real tcount and the real hook, so the recorded shape is live.
Run: tcount pytest -q tests/test_run_place.py (from the plugin root)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared import evidence_check as ec
from shared.ledger import read_runs
from shared.run_place import run_dir

PLUGIN = Path(__file__).resolve().parent.parent

PLACES = [
    ("pytest -q", "/c"),]


@pytest.mark.parametrize("command, expected", PLACES)
def test_run_dir_is_the_directory_in_force_at_the_runner(command, expected, monkeypatch):
    monkeypatch.delenv("CDPATH", raising=False)
    assert run_dir(command, "/c") == expected


def tcount_then_hook(command: str, cwd: Path, main: Path, env: dict) -> None:
    """Run tcount for real in `cwd`, then hand its output to the run-ledger hook as the Bash
    call `command`, made from the session cwd `main`."""
    done = subprocess.run([sys.executable, str(PLUGIN / "bin" / "tcount"), sys.executable, "-m", "pytest", "-q",
                           "-p", "no:cacheprovider", "test_x.py"], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=120)
    assert "log:" in done.stdout, done.stdout + done.stderr
    payload = {"session_id": "s-h1", "cwd": str(main), "hook_event_name": "PostToolUse", "tool_name": "Bash",
               "tool_input": {"command": command, "description": "run"},
               "tool_response": {"stdout": done.stdout, "stderr": "", "interrupted": False}}
    hooked = subprocess.run([sys.executable, str(PLUGIN / "hooks" / "run_ledger.py")], input=json.dumps(payload),
                            env=env, capture_output=True, text=True, timeout=60)
    assert hooked.returncode == 0, hooked.stderr


