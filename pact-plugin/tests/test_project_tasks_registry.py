"""
D10 P3: bootstrap's ONE-TIME `repact-task init` offer (design section 13 P3; lead ruling on #200: ask
once per project, never re-ask, and a 'no' is remembered too). The answer lives in P6's machine
registry (bin/_tasks/registry.py, ${CLAUDE_PLUGIN_DATA}/projects.json), which P6 owns and tests.

`repact-task init --status` says whether the offer was answered for this checkout (unanswered,
accepted, declined, present, no-repo) and writes nothing; `repact-task init` creates the log and
records `accepted`; `repact-task init --decline` records `declined` and creates nothing.
"""
from __future__ import annotations

import os

import pytest

from project_tasks_support import PLUGIN_ROOT, git, load, new_repo, run_cli



@pytest.fixture
def data(tmp_path, monkeypatch):
    path = tmp_path / "plugin-data"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(path))
    return path


def status(repo):
    result = run_cli(repo, "init", "--status", env={"CLAUDE_PLUGIN_DATA": os.environ["CLAUDE_PLUGIN_DATA"]})
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def cli(repo, *args):
    return run_cli(repo, *args, env={"CLAUDE_PLUGIN_DATA": os.environ["CLAUDE_PLUGIN_DATA"]})


class TestTheOffer:
    def test_an_unasked_repo_is_unanswered_and_status_writes_nothing(self, data, tmp_path):
        repo = new_repo(tmp_path / "repo")
        assert status(repo) == "init: unanswered"
        assert not (repo / ".repacted").exists() and not (data / "projects.json").exists()

    def test_init_records_accepted(self, data, tmp_path):
        repo = new_repo(tmp_path / "repo")
        assert cli(repo, "init").returncode == 0
        assert (repo / ".repacted" / "tasks.jsonl").exists()
        assert status(repo) == "init: accepted"

    def test_decline_is_remembered_and_creates_nothing(self, data, tmp_path):
        repo = new_repo(tmp_path / "repo")
        result = cli(repo, "init", "--decline")
        assert result.returncode == 0 and "declined" in result.stdout
        assert not (repo / ".repacted").exists()
        assert status(repo) == "init: declined"
        assert status(repo) == "init: declined"              # still remembered on the next ask

    def test_the_answer_is_per_project(self, data, tmp_path):
        first, second = new_repo(tmp_path / "first"), new_repo(tmp_path / "second")
        cli(first, "init", "--decline")
        assert status(second) == "init: unanswered"

    def test_a_worktree_shares_its_main_checkouts_answer(self, data, tmp_path):
        repo = new_repo(tmp_path / "repo")
        cli(repo, "init", "--decline")
        git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "side")
        assert status(tmp_path / "wt") == "init: declined"

    def test_a_log_made_without_the_offer_counts_as_present(self, data, tmp_path):
        repo = new_repo(tmp_path / "repo")
        load("registry_test_writer", PLUGIN_ROOT / "hooks" / "shared" / "task_log" / "write.py").init(repo)
        assert status(repo) == "init: present"

    def test_outside_a_repo_there_is_nothing_to_offer(self, data, tmp_path):
        (tmp_path / "plain").mkdir()
        assert status(tmp_path / "plain") == "init: no-repo"

    def test_a_declined_project_can_still_init_later(self, data, tmp_path):
        repo = new_repo(tmp_path / "repo")
        cli(repo, "init", "--decline")
        assert cli(repo, "init").returncode == 0
        assert status(repo) == "init: accepted"

    def test_status_and_decline_together_is_a_usage_error(self, data, tmp_path):
        repo = new_repo(tmp_path / "repo")
        assert cli(repo, "init", "--status", "--decline").returncode == 64
