"""
bin/repact-task (D10 P1, the design notes §6): every P1 subcommand,
one line per task (show excepted), the exit codes 0/64/65/66/74, and init's repo files. Each test
runs the CLI as a subprocess inside a temp git repo.
"""
from __future__ import annotations

import re

import pytest

from project_tasks_support import git, kb_profile, new_repo, run_cli

LINE = re.compile(r"^T-[0-9a-f]{8}  \S+  P[0-3]  \S+  .+$")
SUBCOMMANDS = ("init", "add", "set", "dep", "link", "start", "done", "reopen", "drop", "next",
               "blocked", "tree", "show", "fsck", "summary", "sync")


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    kb_profile(tmp_path, monkeypatch)
    repo = new_repo(tmp_path / "repo")
    assert run_cli(repo, "init").returncode == 0
    return repo


def _ref(result):
    return result.stdout.split()[0]


class TestInit:
    def test_init_writes_the_log_the_union_line_and_the_ignore_lines_once(self, tmp_path):
        repo = new_repo(tmp_path / "repo")
        for _ in range(2):
            assert run_cli(repo, "init").returncode == 0
        assert (repo / ".repacted" / "tasks.jsonl").exists()
        attrs = (repo / ".gitattributes").read_text().splitlines()
        assert attrs.count(".repacted/tasks.jsonl merge=union") == 1
        ignore = (repo / ".gitignore").read_text().splitlines()
        assert ignore.count(".repacted/tasks.lock") == 1 and ignore.count(".repacted/cache/") == 1

    def test_init_keeps_existing_attribute_and_ignore_lines(self, tmp_path):
        repo = new_repo(tmp_path / "repo")
        (repo / ".gitattributes").write_text("*.png binary")
        (repo / ".gitignore").write_text("node_modules/\n")
        assert run_cli(repo, "init").returncode == 0
        attrs = (repo / ".gitattributes").read_text().splitlines()
        ignore = (repo / ".gitignore").read_text().splitlines()
        assert attrs[0] == "*.png binary" and ".repacted/tasks.jsonl merge=union" in attrs
        assert ignore[0] == "node_modules/" and ".repacted/tasks.lock" in ignore


@pytest.mark.slow
def test_a_parent_folder_uses_its_one_child_repo_and_asks_for_repo_when_there_are_several(
        tmp_path, monkeypatch):
    kb_profile(tmp_path, monkeypatch)
    session = tmp_path / "session"
    repo = new_repo(session / "app")
    assert run_cli(repo, "init").returncode == 0
    ref = _ref(run_cli(session, "add", "From the parent folder", "--priority", "P2"))
    assert ref.startswith("T-")
    assert "using" in run_cli(session, "next").stderr
    new_repo(session / "other")
    several = run_cli(session, "next")
    assert several.returncode == 64 and "--repo" in several.stderr
    assert ref in run_cli(session, "--repo", str(repo), "next").stdout


class TestExitCodes:
    def test_no_log_is_66(self, tmp_path):
        repo = new_repo(tmp_path / "repo")
        assert run_cli(repo, "next").returncode == 66
        assert run_cli(repo, "add", "x").returncode == 66

    def test_usage_is_64(self, repo):
        assert run_cli(repo, "frobnicate").returncode == 64
        assert run_cli(repo, "set", "T-deadbeef").returncode == 64
        assert run_cli(repo, "set", "T-nope", "--status", "active").returncode == 64


@pytest.mark.slow
class TestCommands:
    def test_add_set_and_show(self, repo):
        added = run_cli(repo, "add", "Merge D3 before D5", "--priority", "P1", "--owner", "lead",
                        "--tag", "gate", "--note", "needs the doctor")
        assert added.returncode == 0 and LINE.match(added.stdout.strip()), added.stdout
        ref = _ref(added)
        updated = run_cli(repo, "set", ref[:6], "--status", "active", "--rank", "2")
        assert updated.returncode == 0 and " active " in updated.stdout
        shown = run_cli(repo, "show", ref).stdout
        for needle in ("Merge D3 before D5", "P1", "lead", "gate", "needs the doctor", "rank"):
            assert needle in shown


@pytest.mark.slow
def test_a_done_line_whose_commit_does_not_resolve_shows_unverified_and_fails_fsck(repo):
    """A hand-written or union-merged `done` line that claims verified:true for a commit this repo
    does not hold: `show` re-verifies and says unverified, and fsck exits non-zero naming it."""
    ref = _ref(run_cli(repo, "add", "Fix the pager", "--priority", "P2"))
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert run_cli(repo, "done", ref, "--evidence", f"commit:{head}").returncode == 0
    assert run_cli(repo, "fsck").returncode == 0
    log = repo / ".repacted" / "tasks.jsonl"
    log.write_text(log.read_text().replace(head, "d" * 40))
    show = run_cli(repo, "show", ref).stdout
    assert f"evidence: commit:{'d' * 40} (unverified: no such commit in this repo)" in show, show
    fsck = run_cli(repo, "fsck")
    assert fsck.returncode == 1 and f"{ref}: evidence commit:{'d' * 40} no longer verifies" in fsck.stdout
