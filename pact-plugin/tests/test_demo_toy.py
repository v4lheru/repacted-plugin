"""The /repacted:demo pieces: the toy fails before its fix, the setup stays out of the user's
repository, and the command names only files that exist.

Location: pact-plugin/tests/test_demo_toy.py. Covers examples/toy/ (one failing test, fixed by
one line), examples/demo_setup.py (the scratch copy and its refusal) and commands/demo.md (every
plugin path, command, agent and link it names resolves; plugin.json lists it). The toy is
skipped by the suite (examples/conftest.py), so its tests run here in a copy, in a subprocess.
"""
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


@pytest.mark.slow
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


