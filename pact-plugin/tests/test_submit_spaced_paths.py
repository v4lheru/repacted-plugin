"""`repact submit` keeps a produced path that holds spaces whole (hooks/shared/ops/submit.py).

Location: pact-plugin/tests/test_submit_spaced_paths.py. A worktree under a folder whose
name holds a space (as in "Work Folder" or "Demo Org"): an absolute path into it, a
relative path with spaces and a description, and a quoted path to a file not yet on disk are each
kept whole; a spaced path outside the worktree is still refused.
Run: tcount pytest -q tests/test_submit_spaced_paths.py (from the plugin root)
"""
from __future__ import annotations

import os

import pytest

from shared.ops.submit import repair_produced


@pytest.fixture
def root(tmp_path):
    tree = tmp_path / "Demo Org" / "wt"
    (tree / "src dir").mkdir(parents=True)
    (tree / "src dir" / "a b.py").write_text("x = 1\n")
    return os.path.realpath(tree)


def test_an_absolute_path_with_spaces_inside_the_worktree_is_kept_whole(root):
    path = os.path.join(root, "src dir", "a b.py")
    assert repair_produced([f"{path} - the fix"], root) == ([f"{path} - the fix"], [])


def test_a_relative_path_with_spaces_is_made_absolute_whole(root):
    items, problems = repair_produced(["src dir/a b.py: the fix"], root)
    assert problems == [] and items == [os.path.join(root, "src dir", "a b.py") + ": the fix"]


def test_a_quoted_path_with_spaces_not_on_disk_is_kept_whole(root):
    items, problems = repair_produced(["`src dir/new file.py` the new test"], root)
    assert problems == [] and items == ["`" + os.path.join(root, "src dir", "new file.py") + "` the new test"]


def test_a_path_with_spaces_outside_the_worktree_is_still_refused(root, tmp_path):
    outside = tmp_path / "Other Place"
    outside.mkdir()
    (outside / "x y.py").write_text("")
    for item in (f"{outside}/x y.py: elsewhere", "../../Other Place/x y.py", f"`{outside}/x y.py`"):
        _items, problems = repair_produced([item], root)
        assert len(problems) == 1 and "resolves outside the worktree" in problems[0], item
