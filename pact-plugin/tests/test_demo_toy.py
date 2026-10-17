"""The /repacted:demo pieces: the toy fails before its fix, the setup stays out of the user's
repository, and the command names only files that exist.

Location: pact-plugin/tests/test_demo_toy.py. Covers examples/toy/ (one failing test, fixed by
one line), examples/demo_setup.py (the scratch copy and its refusal) and commands/demo.md (every
plugin path, command, agent and link it names resolves; plugin.json lists it). The toy is
skipped by the suite (examples/conftest.py), so its tests run here in a copy, in a subprocess.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
TOY = PLUGIN / "examples" / "toy"
SETUP = PLUGIN / "examples" / "demo_setup.py"
DEMO = PLUGIN / "commands" / "demo.md"
BUG, FIX = 'len(text.split(" "))', "len(words(text))"


def _toy_tests(where):
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=where,
                         capture_output=True, text=True, timeout=120)
    return run.returncode, run.stdout.strip().splitlines()[-1]


def test_toy_has_one_failing_test_that_one_line_fixes(tmp_path):
    copy = tmp_path / "toy"
    shutil.copytree(TOY, copy)
    code, summary = _toy_tests(copy)
    assert code == 1 and re.search(r"\b1 failed, 4 passed\b", summary), summary
    source = (copy / "wordcount.py").read_text(encoding="utf-8")
    assert source.count(BUG) == 1
    (copy / "wordcount.py").write_text(source.replace(BUG, FIX), encoding="utf-8")
    code, summary = _toy_tests(copy)
    assert code == 0 and re.search(r"\b5 passed\b", summary), summary


def _setup(cwd, tmpdir):
    env = {**os.environ, "TMPDIR": str(tmpdir)}
    return subprocess.run([sys.executable, str(SETUP)], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=60)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_setup_refuses_a_scratch_dir_inside_the_repository(repo):
    inside = repo / "tmp"
    inside.mkdir()
    run = _setup(repo, inside)
    assert run.returncode == 1 and run.stdout.startswith("refused:"), run.stdout + run.stderr
    assert list(inside.iterdir()) == []


def test_setup_copies_the_toy_outside_the_repository(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    run = _setup(repo, outside)
    assert run.returncode == 0, run.stdout + run.stderr
    demo = Path(run.stdout.strip().splitlines()[-1].removeprefix("demo dir: "))
    assert demo.parent == outside.resolve()
    assert sorted(p.name for p in demo.iterdir() if p.name != ".git") == [".gitignore",
        "README.md", "test_wordcount.py", "wordcount.py"]
    log = subprocess.run(["git", "-C", str(demo), "log", "--oneline"], capture_output=True, text=True)
    assert len(log.stdout.splitlines()) == 1
    assert not (repo / "wordcount.py").exists()


def test_demo_command_names_only_files_that_exist():
    text = DEMO.read_text(encoding="utf-8")
    paths = re.findall(r"\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)", text)
    commands = set(re.findall(r"/repacted:([\w-]+)", text))
    agents = set(re.findall(r"`(pact-[\w-]+)`", text))
    links = re.findall(r"\]\(([^)#]+)\)", text)
    assert paths and commands and agents and links
    missing = ([p for p in paths if not (PLUGIN / p).is_file()]
               + [c for c in commands if not (PLUGIN / "commands" / f"{c}.md").is_file()]
               + [a for a in agents if not (PLUGIN / "agents" / f"{a}.md").is_file()]
               + [ln for ln in links if not (DEMO.parent / ln).is_file()])
    assert missing == []
    listed = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "./commands/demo.md" in listed["commands"]
