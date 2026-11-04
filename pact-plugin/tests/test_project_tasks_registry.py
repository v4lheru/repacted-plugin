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


