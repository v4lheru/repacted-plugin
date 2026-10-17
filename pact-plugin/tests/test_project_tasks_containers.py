"""
D10 P4a rev 3 (architect-pact S1/S2, adopted by the lead on #186): a parent is a container.
S1: a parent with an open child is not ready (it waits on its children, and `blocked` names them),
so a release leaves `next` until its last gate closes. S2: a settled (done or dropped) task cannot
take a child, at add --parent or set parent, and fsck flags an existing settled parent that still
has an open child.
"""
from __future__ import annotations

import json

import pytest

from project_tasks_support import git, new_repo, run_cli, writer


@pytest.fixture
def world(tmp_path):
    W = writer()
    repo = new_repo(tmp_path / "repo")
    store = W.init(repo)
    return W, W.PT, store, repo, git(repo, "rev-parse", "HEAD").stdout.strip()


class TestAParentWaitsForItsChildren:
    def test_a_parent_with_an_open_child_is_not_ready_until_the_child_settles(self, world):
        W, PT, store, repo, sha = world
        parent = store.add("parent")
        child = store.add("child", parent=parent)
        state = store.state()
        assert [t["ref"] for t in PT.ready(state)] == [child]
        assert PT.classify(state, parent) == "waiting"
        assert [(t["ref"], kind, who) for t, kind, who in PT.blocked(state)] == [(parent, "waiting", [child])]
        store.done(child, ["commit:" + sha])
        assert [t["ref"] for t in PT.ready(store.state())] == [parent]

    def test_a_dropped_child_does_not_hold_its_parent(self, world):
        W, PT, store, repo, sha = world
        parent = store.add("parent")
        child = store.add("child", parent=parent)
        store.drop(child)
        assert [t["ref"] for t in PT.ready(store.state())] == [parent]

    def test_next_on_the_command_line_skips_a_waiting_parent(self, world):
        W, PT, store, repo, sha = world
        parent = store.add("the container")
        store.add("the work", parent=parent)
        out = run_cli(repo, "next").stdout
        assert "the work" in out and "the container" not in out


class TestASettledParentTakesNoChild:
    def test_add_under_a_done_parent_is_refused(self, world):
        W, PT, store, repo, sha = world
        parent = store.add("parent")
        store.done(parent, ["commit:" + sha])
        before = (repo / ".repacted" / "tasks.jsonl").read_bytes()
        with pytest.raises(W.Refused):
            store.add("late child", parent=parent)
        assert run_cli(repo, "add", "late child", "--priority", "P2", "--parent", parent).returncode == 65
        assert (repo / ".repacted" / "tasks.jsonl").read_bytes() == before

    def test_set_parent_to_a_dropped_task_is_refused(self, world):
        W, PT, store, repo, sha = world
        parent = store.add("parent")
        store.drop(parent)
        orphan = store.add("orphan")
        with pytest.raises(W.Refused):
            store.set(orphan, "parent", parent)
        assert run_cli(repo, "set", orphan, "--parent", parent).returncode == 65

    def test_fsck_flags_an_open_child_under_a_settled_parent(self, world):
        """Raw lines (a merge, a hand edit) can still produce one; fsck names it."""
        W, PT, store, repo, sha = world
        lines = [
            {"v": 1, "eid": "20260101T000000000Z-0000000a", "ts": "2026-01-01T00:00:00.000Z", "by": "lead",
             "op": "create", "t": "T-0000000a", "title": "parent", "status": "planned", "priority": "P2"},
            {"v": 1, "eid": "20260101T000000001Z-0000000b", "ts": "2026-01-01T00:00:00.001Z", "by": "lead",
             "op": "done", "t": "T-0000000a", "evidence": [{"target": "commit:" + sha, "verified": True}]},
            {"v": 1, "eid": "20260101T000000002Z-0000000c", "ts": "2026-01-01T00:00:00.002Z", "by": "lead",
             "op": "create", "t": "T-0000000c", "title": "child", "status": "planned", "priority": "P2",
             "parent": "T-0000000a"}]
        with (repo / ".repacted" / "tasks.jsonl").open("a") as handle:
            handle.writelines(json.dumps(line) + "\n" for line in lines)
        out = run_cli(repo, "fsck").stdout
        assert "T-0000000c: open child of settled parent T-0000000a (done)" in out
