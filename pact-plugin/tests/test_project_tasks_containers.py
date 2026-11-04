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


