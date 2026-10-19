"""tests/test_env_guard_computed_paths.py

Before refusing a computed path the env guard resolves what it safely
can, and refuses only when the resolved set reaches the .env class or cannot be resolved.
Resolved: a `for` loop over plain words (judged once per word), a brace list and a `**`
glob in a search root, $HOME / $CLAUDE_PROJECT_DIR / $CLAUDE_CONFIG_DIR from the hook's own
environment. The first block is the refusals the audit counted as retries (and
two more hit in daily use); the second is the adversarial list, each one refused.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

import env_guard as guard

TAIL = re.compile(r" \[rule: ([a-z_]+(?:\.[a-z_]+)+)\]$")
MARKER = "inert-marker-7c2a"


@pytest.fixture
def tree(tmp_path, monkeypatch) -> Path:
    for folder in ("src/sub", "lib", "docs", "w1", "w2", "nested/deep", "home/proj"):
        (tmp_path / folder).mkdir(parents=True)
    for name in ("src/a.py", "src/sub/b.py", "lib/c.py", "docs/team-policy.md", "w1/a.txt"):
        (tmp_path / name).write_text("TODO\n", encoding="utf-8")
    for name in (".env", "nested/deep/.env.local", "home/proj/.env"):
        (tmp_path / name).write_text(f"DEMO_KEY={MARKER}\n", encoding="utf-8")
    (tmp_path / "notes").symlink_to(".env")
    for repo in ("w1", "w2"):
        subprocess.run(["git", "init", "-q", str(tmp_path / repo)], check=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "src"))
    return tmp_path


def rule(command: str, cwd: Path) -> str | None:
    output = guard.decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    if not output or output["hookSpecificOutput"].get("permissionDecision") != "deny":
        return None
    text = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert MARKER not in text
    return TAIL.search(text).group(1)


@pytest.mark.parametrize("command", [
    'for d in src lib; do grep -rn TODO "$d"; done',                   # a loop variable
    "grep -rn TODO {src,lib}",                                          # probe 2
    "grep -rn TODO ./src/**/*.py",                                      # probe 3
    'for d in w1 w2; do git -C "$d" add -A; done',                      # its git -C form
    'for d in w1 w2\ndo\n  git -C $d commit -m x\ndone',
    'for a in src lib; do for b in sub x; do grep -rn TODO "$a/$b"; done; done',
    "grep -rn TODO docs/*policy* src",
    "F=$(grep -rln --exclude='.env*' --exclude='*.env' TODO src); echo $F",  # not a load
    'grep -rn TODO "$CLAUDE_PROJECT_DIR"',
    'git -C "$HOME" add -A',
])
def test_a_known_harmless_expansion_passes(tree, command):
    assert rule(command, tree) is None


@pytest.mark.parametrize("command, expected", [
    ('for d in src .env; do grep -rn K "$d"; done', "env.read.operand"),
    ('for d in src config/../.env; do grep -rn K "$d"; done', "env.read.operand"),
    ("grep -rn K {src,.env}", "env.read.operand"),
    ("grep -rn K ./**/*.local", "env.search.reaches_class"),           # matches .env.local
    ("grep -rn K nested/**", "env.search.reaches_class"),
    ("grep -rn K ~/**", "env.search.reaches_class"),                   # ** from ~
    ('for d in src lib; do for f in a .envrc; do cat "$d/$f"; done; done', "env.read.operand"),
    ('for d in $(ls); do grep -rn K "$d"; done', "env.search.unknown_root"),
    ("for d in " + " ".join(f"w{i}" for i in range(17)) + '; do grep -rn K "$d"; done',
     "env.search.unknown_root"),                                        # 17 words: over the cap
    ('for d in src .ｅnv; do grep -rn K "$d"; done', "env.search.unknown_root"),
    ("grep -rn K {src,.еnv}", "env.search.unknown_root"),          # Cyrillic e
    ('d=$(echo w1); git -C "$d" add -A', "env.git.unknown_dir"),
    ('for d in "src/*"; do grep -rn K "$d"; done', "env.search.unknown_root"),  # quoted glob
    ('for d in src; do d=$(cat list); grep -rn K "$d"; done', "env.search.unknown_root"),
    ('for d in src; do grep -rn K "${d:-.}"; done', "env.search.unknown_root"),
    ('for d in src lib; do :; done; grep -rn K "$d"', "env.search.unknown_root"),  # after done
    ('echo for d in src; grep -rn K "$d"', "env.search.unknown_root"),  # not a loop
    ("git -C {w1,w2} add -A", "env.git.unknown_dir"),                   # -C w1 w2: not two dirs
])
def test_an_expansion_that_reaches_the_class_or_stays_unknown_is_refused(tree, command, expected):
    assert rule(command, tree) == expected


@pytest.mark.parametrize("command", [
    'for f in notes; do cat "$f"; done',                                # notes -> .env
    "grep -rn K {src,notes}",
])
def test_an_innocent_name_linked_to_the_class_is_refused(tree, command):
    assert rule(command, tree) is not None


def test_a_glob_walk_past_the_limit_is_refused(tree, monkeypatch):
    import shell.env.search as search
    monkeypatch.setattr(search, "WALK_LIMIT", 3)
    assert rule("grep -rn K ./src/**/*.py", tree) == "env.search.too_wide"
