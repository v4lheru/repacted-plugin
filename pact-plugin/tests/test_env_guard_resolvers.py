"""tests/test_env_guard_resolvers.py

Step 16 (refactor §3.7 part 1): three path-valued substitutions are spelled as the path they
name before the env guard judges a command, so `grep -rn X "$(command -v tool)"` is a search
of one known file and `git -C "$(git rev-parse --show-toplevel)"` runs in a known directory.
A span after a `cd`, after `PATH=`, or whose path the shell would split stays unknown.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

import env_guard as guard
from shell.env.places import resolve_path_substitutions

TAIL = re.compile(r" \[rule: ([a-z_]+(?:\.[a-z_]+)+)\]$")
MARKER = "inert-marker-5e1d"


@pytest.fixture
def tree(tmp_path, monkeypatch) -> Path:
    (tmp_path / ".env").write_text(f"DEMO_KEY={MARKER}\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x\n", encoding="utf-8")
    tool = tmp_path / "bin" / "probe-tool"
    tool.parent.mkdir()
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tool.parent) + os.pathsep + os.environ["PATH"])
    return tmp_path


def rule(command: str, cwd: Path) -> str | None:
    output = guard.decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    if not output or output["hookSpecificOutput"].get("permissionDecision") != "deny":
        return None
    text = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert MARKER not in text
    return TAIL.search(text).group(1)


@pytest.mark.parametrize("command, expected", [
    ('grep -rn KEY "$(command -v probe-tool)"', None),
    ("grep -rn KEY $(which probe-tool)", None),
    ('grep -rn KEY "$(pwd)/src"', None),
    ('grep -rn KEY "$(pwd)"', "env.search.reaches_class"),          # a real root, judged
    ('cd src && grep -rn KEY "$(pwd)"', "env.search.unknown_root"),  # moved: unknown
    ('PATH=/nowhere; grep -rn KEY "$(command -v probe-tool)"', "env.search.unknown_root"),
    ('grep -rn KEY "$(command -v no-such-tool-5e1d)"', "env.search.unknown_root"),
    ('grep -rn KEY "$(command -v ../probe-tool)"', "env.search.unknown_root"),
])
def test_a_resolved_substitution_is_a_known_place(tree, command, expected):
    assert rule(command, tree) == expected


def test_a_path_with_a_space_resolves_only_inside_double_quotes(tmp_path):
    spaced = tmp_path / "a b"
    spaced.mkdir()
    assert resolve_path_substitutions('ls "$(pwd)"', str(spaced)) == f'ls "{spaced}"'
    assert resolve_path_substitutions("ls $(pwd)", str(spaced)) == "ls $(pwd)"


def test_the_toplevel_resolves_to_the_nearest_git_ancestor(tmp_path):
    repo = tmp_path / "repo"
    (repo / "deep" / "er").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    here = str(repo / "deep" / "er")
    got = resolve_path_substitutions('git -C "$(git rev-parse --show-toplevel)" status', here)
    assert got == f'git -C "{os.path.realpath(repo)}" status'
