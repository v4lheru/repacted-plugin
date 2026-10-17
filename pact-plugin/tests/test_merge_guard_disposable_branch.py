"""
Location: pact-plugin/tests/test_merge_guard_disposable_branch.py
Summary: A forced delete of an empty, never-pushed local branch needs no approval (it loses
         nothing); a branch with a commit of its own, or one that was pushed, still does.
         Reported by the acme session, 2026-09-29.
"""
import subprocess

import pytest

import merge_guard_pre as guard
from project_tasks_support import GIT_ENV

FORCED = "-" + "D"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "root")
    git("branch", "empty")
    git("branch", "pushed")
    git("config", "branch.pushed.remote", "origin")
    git("checkout", "-q", "-b", "own")
    git("commit", "-q", "--allow-empty", "-m", "unique")
    git("checkout", "-q", "main")
    return tmp_path


@pytest.mark.parametrize("branch, allowed", [("empty", True), ("own", False), ("pushed", False)])
def test_only_an_empty_never_pushed_branch_is_deleted_without_approval(repo, tmp_path, branch, allowed):
    reason = guard.check_merge_authorization(f"git branch {FORCED} {branch}",
                                             token_dir=tmp_path / "no-tokens", cwd=str(repo))
    assert (reason is None) is allowed
