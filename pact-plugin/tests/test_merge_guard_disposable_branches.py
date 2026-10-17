"""
Location: pact-plugin/tests/test_merge_guard_disposable_branches.py
Summary: Tests for merge_guard_pre._deletes_only_disposable_branches and its git helper
         _git_quiet, the path that lets a bare `git branch -D name` through without an
         approval when the branch loses nothing. Run against a real scratch repository.
Used by: the plugin test suite (pytest).

The helper's return annotation named `subprocess` without a module-level binding (ruff
F821). It never failed at run time: the annotation is a string under
`from __future__ import annotations` and the call imports subprocess lazily, which keeps
it off every Bash call's import cost, so the binding is a TYPE_CHECKING import. The first
test pins that binding statically; the others drive the path end to end.
"""

from __future__ import annotations

import ast
import builtins
import subprocess
from pathlib import Path

import pytest

import merge_guard_pre as pre

_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


def _git(cwd, *args: str) -> None:
    subprocess.run([*_GIT, *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """main with one commit; `merged` at main (loses nothing); `ahead` one commit past it."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "base")
    _git(tmp_path, "branch", "merged")
    _git(tmp_path, "checkout", "-q", "-b", "ahead")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "only here")
    _git(tmp_path, "checkout", "-q", "main")
    return tmp_path


def test_every_name_in_the_git_helper_annotation_is_bound_at_module_level():
    tree = ast.parse(Path(pre.__file__).read_text(encoding="utf-8"))
    bound = set()
    for node in ast.walk(ast.Module(body=[n for n in tree.body if not isinstance(
            n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))], type_ignores=[])):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_git_quiet")
    names = {n.id for n in ast.walk(ast.parse(helper.returns.value, mode="eval"))
             if isinstance(n, ast.Name)}
    names -= set(dir(builtins))
    assert names == {"subprocess"} and names <= bound, names - bound


def test_a_branch_that_loses_nothing_is_disposable(repo):
    assert pre._git_quiet(str(repo), "rev-parse", "HEAD").returncode == 0
    assert pre._deletes_only_disposable_branches("git branch -D merged", str(repo)) is True


def test_a_branch_with_its_own_commit_is_not_disposable(repo):
    assert pre._deletes_only_disposable_branches("git branch -D ahead", str(repo)) is False
    assert pre._deletes_only_disposable_branches("git branch -D merged ahead", str(repo)) is False


@pytest.mark.parametrize("command", [
    "git branch -D missing",              # no such branch: rev-parse fails
    "git branch -D 'merged'\"; true\"",   # quoting that joins a shell word onto the name
    "git branch -D merged --remotes",     # a flag outside the delete set
])
def test_odd_names_and_flags_are_not_disposable(repo, command):
    assert pre._deletes_only_disposable_branches(command, str(repo)) is False


def test_a_cwd_that_is_not_a_repository_is_not_disposable(tmp_path):
    """Every git call fails there: the helper returns a failed result, never True."""
    assert pre._deletes_only_disposable_branches("git branch -D merged", str(tmp_path)) is False
