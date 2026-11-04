"""tests/test_env_guard_captured_search.py

A recursive search inside a command substitution gets the checks it gets at top level
whenever its captured text can still reach the transcript, a file or the network: printed
through a variable, an argument, a here-string, a nested substitution, a function, `read`,
`eval`, or run as the program word. A capture that is never printed passes, as
`X=$(cat .env)` does: shell state does not outlive one Bash call.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import env_guard as guard

MARKER = "inert-marker-5d1e"


@pytest.fixture
def tree(tmp_path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("K = 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text(f"K={MARKER}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def denied(command: str, cwd: Path) -> bool:
    output = guard.decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    if not output or output["hookSpecificOutput"].get("permissionDecision") != "deny":
        return False
    assert MARKER not in output["hookSpecificOutput"]["permissionDecisionReason"]
    return True


@pytest.mark.parametrize("command", [
    'X=$(grep -rn K .); echo "$X"',
    'X="seen: $(grep -rn K .)"; printf %s "$X"',
    'cat <<<"$(grep -r K .)"',
    'readonly X=$(grep -rn K .); echo "$X"',
    "$(grep -rn K .)",
    'read X <<<"$(grep -rn K .)"; echo "$X"',
    "eval 'X=$(grep -rn K .)'; echo \"$X\"",
    'X=$(cat .env); Y="$X"; echo "$Y"',
])
def test_a_captured_search_that_can_be_printed_is_refused(tree, command):
    assert denied(command, tree)


@pytest.mark.parametrize("command", [
    'X=$(git rev-parse HEAD); echo "$X"',
    "eval 'X=$(date +%s)'; echo \"$X\"",
    'X=$(grep -rln K .); echo "$X"',
])
def test_a_captured_search_that_prints_nothing_protected_passes(tree, command):
    assert not denied(command, tree)
