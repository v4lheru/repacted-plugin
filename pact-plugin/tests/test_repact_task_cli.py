"""
bin/repact-task (D10 P1, the design notes §6): every P1 subcommand,
one line per task (show excepted), the exit codes 0/64/65/66/74, and init's repo files. Each test
runs the CLI as a subprocess inside a temp git repo.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import importlib.util
from importlib.machinery import SourceFileLoader

import pytest

from project_tasks_support import CLI, INERT_MARKER, git, kb_profile, new_repo, reader, run_cli

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

    def test_outside_a_checkout_init_is_refused_and_writes_nothing(self, tmp_path):
        (tmp_path / "plain").mkdir()
        result = run_cli(tmp_path / "plain", "init")
        assert result.returncode == 66
        assert list((tmp_path / "plain").iterdir()) == []


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

    def test_refused_is_65_and_writes_nothing(self, repo):
        ref = _ref(run_cli(repo, "add", "a", "--priority", "P2"))
        before = (repo / ".repacted" / "tasks.jsonl").read_bytes()
        result = run_cli(repo, "set", ref, "--status", "done")
        assert result.returncode == 65
        assert (repo / ".repacted" / "tasks.jsonl").read_bytes() == before

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
    def test_an_unreadable_log_is_74(self, repo):
        log = repo / ".repacted" / "tasks.jsonl"
        log.chmod(0)
        try:
            assert run_cli(repo, "next").returncode == 74
        finally:
            log.chmod(0o644)

    def test_a_credential_is_refused_without_echoing_it(self, repo):
        result = run_cli(repo, "add", f"call {INERT_MARKER}", "--priority", "P2")
        assert result.returncode == 65
        assert INERT_MARKER not in result.stdout + result.stderr
        assert "test-secret" in result.stderr


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

    def test_add_refuses_the_title_of_an_open_task_unless_forced(self, repo):
        log = repo / ".repacted" / "tasks.jsonl"
        first = _ref(run_cli(repo, "add", "Fix the  Gate", "--priority", "P2"))
        before = log.read_bytes()
        twin = run_cli(repo, "add", "  fix THE gate ", "--priority", "P2")
        assert twin.returncode == 65 and first in twin.stderr and twin.stdout == ""
        assert log.read_bytes() == before
        forced = run_cli(repo, "add", "fix the gate", "--priority", "P2", "--force")
        assert forced.returncode == 0 and _ref(forced) != first
        for ref in (first, _ref(forced)):
            assert run_cli(repo, "drop", ref).returncode == 0
        assert run_cli(repo, "add", "Fix the gate", "--priority", "P2").returncode == 0     # a settled twin does not count

    def test_dep_blocked_next_and_tree(self, repo):
        a = _ref(run_cli(repo, "add", "first", "--priority", "P0"))
        b = _ref(run_cli(repo, "add", "second", "--priority", "P2"))
        c = _ref(run_cli(repo, "add", "third", "--priority", "P2", "--depends", a))
        assert run_cli(repo, "dep", b, "--on", a).returncode == 0
        refused = run_cli(repo, "dep", a, "--on", c)
        assert refused.returncode == 65 and "cycle" in refused.stderr
        nxt = run_cli(repo, "next").stdout.splitlines()
        assert nxt[0].startswith(a) and all(LINE.match(l) for l in nxt[:-1])
        assert nxt[-1] == "active: 0"
        blocked = run_cli(repo, "blocked").stdout
        assert b in blocked and c in blocked and "waiting" in blocked
        tree = run_cli(repo, "tree", a).stdout
        assert a in tree and b in tree and c in tree

    def test_link_start_done_reopen_drop(self, repo):
        a = _ref(run_cli(repo, "add", "ship it", "--priority", "P2"))
        assert run_cli(repo, "link", a, "--output", "doc:docs/out.md").returncode == 0
        assert run_cli(repo, "start", a, "--ptask", "session-1/42").returncode == 0
        task = reader().load(repo).tasks[a]
        assert task["status"] == "active"
        assert {"kind": "reference", "target": "ptask:session-1/42"} in task["links"]
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        refused = run_cli(repo, "done", a, "--evidence", "commit:" + "f" * 40)
        assert refused.returncode == 65 and "unverified" in refused.stderr
        assert run_cli(repo, "done", a, "--evidence", "commit:" + sha).returncode == 0
        reopened = run_cli(repo, "reopen", a, "--note", "found a bug")
        assert reopened.returncode == 0 and "  active  " in reopened.stdout
        assert reader().load(repo).tasks[a]["status"] == "active"
        assert run_cli(repo, "drop", a).returncode == 0
        assert reader().load(repo).tasks[a]["status"] == "dropped"

    def test_summary_and_fsck(self, repo):
        assert run_cli(repo, "summary").stdout == ""        # silent when there is nothing
        a = _ref(run_cli(repo, "add", "only task", "--priority", "P2"))
        line = run_cli(repo, "summary").stdout.strip()
        assert line.startswith("tasks: next " + a)
        with (repo / ".repacted" / "tasks.jsonl").open("a") as log:
            log.write("{not json\n")
        fsck = run_cli(repo, "fsck")
        assert fsck.returncode == 0 and "line" in fsck.stdout and "corrupt" in fsck.stdout
        assert "corrupt 1" in run_cli(repo, "summary", "--refresh").stdout
        assert (repo / ".repacted" / "cache" / "summary.json").exists()

    def test_fsck_reverifies_every_verified_evidence_claim(self, repo):
        """S1: a claim that no longer holds (a deleted tag) or never did (a hand-written line)
        is reported; a claim that still holds is not."""
        git(repo, "tag", "v1")
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        a = _ref(run_cli(repo, "add", "tagged", "--priority", "P2"))
        b = _ref(run_cli(repo, "add", "committed", "--priority", "P2"))
        assert run_cli(repo, "done", a, "--evidence", "tag:v1").returncode == 0
        assert run_cli(repo, "done", b, "--evidence", "commit:" + sha).returncode == 0
        git(repo, "tag", "-d", "v1")
        c = _ref(run_cli(repo, "add", "forged", "--priority", "P2"))
        forged = {"v": 1, "eid": "20990101T000000000Z-0badc0de", "ts": "2099-01-01T00:00:00.000Z",
                  "by": "user", "op": "done", "t": c,
                  "evidence": [{"target": "commit:" + "e" * 40, "verified": True}]}
        with (repo / ".repacted" / "tasks.jsonl").open("a") as log:
            log.write(__import__("json").dumps(forged) + "\n")
        out = run_cli(repo, "fsck").stdout
        assert f"{a}: evidence tag:v1" in out and f"{c}: evidence commit:" in out
        assert f"{b}: evidence" not in out

    def test_add_without_a_priority_is_refused_in_one_line_naming_the_choices(self, repo):
        log = repo / ".repacted" / "tasks.jsonl"
        before = log.read_bytes()
        result = run_cli(repo, "add", "no priority given")
        assert result.returncode == 64 and result.stdout == ""
        assert result.stderr.strip().splitlines() == ["repact-task: add needs --priority, one of P0 P1 P2 P3"]
        assert log.read_bytes() == before

    def test_next_marks_tasks_unchanged_for_over_14_days_and_counts_them(self, repo):
        """Age is by the last change (`updated`), not `created`: 15 days since the last `set` is
        stale, 13 is not, exactly 14 is not; a fresh `set` re-decides a task and clears its flag."""
        old = _ref(run_cli(repo, "add", "old work", "--priority", "P1"))
        young = _ref(run_cli(repo, "add", "young work", "--priority", "P2"))
        for ref in (old, young):
            assert run_cli(repo, "set", ref, "--note", "still wanted").returncode == 0
        now = datetime.datetime.now(datetime.timezone.utc)
        stamp = lambda days: (now - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        log = repo / ".repacted" / "tasks.jsonl"
        events = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
        for event in events:
            if event.get("t") in (old, young):
                event["ts"] = stamp(30 if event["op"] == "create" else 15 if event["t"] == old else 13)
        log.write_text("".join(json.dumps(event) + "\n" for event in events))
        lines = run_cli(repo, "next").stdout.splitlines()
        assert lines[0].startswith(old) and lines[0].endswith("  [stale 15d]")
        assert lines[1].startswith(young) and "stale" not in lines[1]
        assert lines[-1] == "1 stale (no change in 14 days): close, drop, or re-decide with set"
        assert len(lines) == 4                              # both tasks still shown, plus the two counts
        loader = SourceFileLoader("repact_task_cli_under_test", str(CLI))
        cli = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(cli)
        state = reader().load(repo)
        updated = datetime.datetime.strptime(state.tasks[young]["updated"], "%Y-%m-%dT%H:%M:%S.%fZ") \
            .replace(tzinfo=datetime.timezone.utc)
        assert young not in cli.stale_open(state, now=updated + datetime.timedelta(days=14))
        assert young in cli.stale_open(state, now=updated + datetime.timedelta(days=14, milliseconds=1))
        assert run_cli(repo, "set", old, "--rank", "1").returncode == 0
        assert "stale" not in run_cli(repo, "next").stdout

    def test_help_names_every_subcommand_with_an_example(self, repo):
        text = run_cli(repo, "--help").stdout
        for name in SUBCOMMANDS:
            assert re.search(rf"repact-task {name}\b", text), name
