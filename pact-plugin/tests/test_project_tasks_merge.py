"""
D10 P1, test plan item 4 (the design notes §12.1): the git union
merge. This test DECIDES the storage layout (§3.1). Two clones of one repo each append 10 events
to `.repacted/tasks.jsonl` and commit; merging A into B, B into A, and rebasing B onto A must all
finish without a conflict and replay to the same state, and a concurrent `set` of one field on
both sides must carry the concurrent-edit flag. If any leg fails, P1 ships per-clone logs.
"""
from __future__ import annotations

import pytest

from project_tasks_support import git, new_repo, reader, writer


def _clone(src, dst):
    git(src.parent, "clone", "-q", str(src), str(dst))
    return dst


def _append_ten(store, tag, shared_ref, status):
    refs = [store.add(f"{tag} task {i}", priority="P1") for i in range(4)]
    store.set(refs[0], "note", f"{tag} note")
    store.dep(refs[1], refs[2])
    store.set(refs[3], "rank", 3)
    store.link(refs[2], "reference", f"branch:{tag}-work")
    store.set(shared_ref, "status", status)        # concurrent with the other side's set
    store.set(refs[1], "owner", tag)               # 10 events in all


@pytest.fixture()
def two_clones(tmp_path):
    W = writer()
    origin = new_repo(tmp_path / "origin")
    store = W.init(origin)
    shared = store.add("shared base task")
    picked = store.add("cherry-picked task")
    git(origin, "add", "-A")
    git(origin, "commit", "-q", "-m", "start the task log")
    sha = git(origin, "rev-parse", "HEAD").stdout.strip()
    a, b = _clone(origin, tmp_path / "a"), _clone(origin, tmp_path / "b")
    _append_ten(W.Store.locate(a), "alpha", shared, "active")
    # One event both sides carry byte for byte (a cherry-pick), at DIFFERENT positions: git's
    # merge collapses an identical line both sides append last, but keeps it twice here
    # (measured), which is exactly the duplicate the eid dedupe exists for.
    W.Store.locate(a).done(picked, ["commit:" + sha])
    line = (a / ".repacted" / "tasks.jsonl").read_text().splitlines()[-1]
    with (b / ".repacted" / "tasks.jsonl").open("a") as log:
        log.write(line + "\n")
    _append_ten(W.Store.locate(b), "beta", shared, "blocked")
    for clone in (a, b):
        git(clone, "add", ".repacted/tasks.jsonl")
        git(clone, "commit", "-q", "-m", "work")
    return tmp_path, a, b, shared, picked


def _combine(tmp_path, name, base, other, how):
    work = _clone(base, tmp_path / name)
    git(work, "fetch", "-q", str(other), "HEAD")
    result = git(work, how, "FETCH_HEAD", *(["-q", "--no-edit"] if how == "merge" else ["-q"]), check=False)
    return work, result


def test_union_merge_replays_the_same_in_both_directions_and_under_rebase(two_clones):
    tmp_path, a, b, shared, picked = two_clones
    R = reader()
    states = {}
    for name, base, other, how in (("a_into_b", b, a, "merge"), ("b_into_a", a, b, "merge"),
                                   ("b_onto_a", b, a, "rebase")):
        work, result = _combine(tmp_path, name, base, other, how)
        assert result.returncode == 0, f"{name}: {result.stdout}{result.stderr}"
        text = (work / ".repacted" / "tasks.jsonl").read_text()
        assert "<<<<<<<" not in text and ">>>>>>>" not in text, name
        parsed = R.read_log(work / ".repacted" / "tasks.jsonl")
        assert parsed.corrupt == [], (name, parsed.corrupt)
        # two base creates, 10 per side, and the cherry-picked line on both sides
        assert len(parsed.events) == 2 + 10 + 10 + 2, name
        state = R.replay(parsed.events)
        assert len(state.tasks[picked]["evidence"]) == 1, name       # the duplicate counts once
        states[name] = R.state_digest(state)
    assert states["a_into_b"] == states["b_into_a"] == states["b_onto_a"]


def test_union_merge_flags_a_concurrent_edit_of_one_field(two_clones):
    tmp_path, a, b, shared, picked = two_clones
    R = reader()
    work, result = _combine(tmp_path, "flagged", b, a, "merge")
    assert result.returncode == 0
    state = R.replay(R.read_log(work / ".repacted" / "tasks.jsonl").events)
    flags = [c for c in state.concurrent if c["ref"] == shared and c["field"] == "status"]
    assert flags and sorted(flags[0]["values"]) == ["active", "blocked"]


def test_the_union_line_is_what_makes_the_merge_clean(two_clones):
    """Control: without the merge=union attribute the same histories conflict, so the passing
    rows above are owed to the attribute `init` writes, not to luck in the line layout."""
    tmp_path, a, b, shared, picked = two_clones
    work = _clone(b, tmp_path / "no_union")
    (work / ".gitattributes").write_text("")
    git(work, "commit", "-q", "-am", "drop the union attribute")
    git(work, "fetch", "-q", str(a), "HEAD")
    result = git(work, "merge", "-q", "--no-edit", "FETCH_HEAD", check=False)
    assert result.returncode != 0
