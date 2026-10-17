"""
D10 P4a amend_2 (lead ruling on #186): `render` and `commit` read the same state as the read
commands, so they run the bridge's quiet sync first (commit: sync -> render -> commit); a
write-free mode (an importer's dry run or --json, `sync --check`) never syncs. The world, the
platform task files and the journal come from the P2 bridge tests, so the sync is the real one.
"""
from __future__ import annotations

import json

from project_tasks_support import git, run_cli
from test_project_tasks_bridge import _journal_accept, world  # noqa: F401  # re-export: pytest fixture

VIEWS = {"backlog": "docs/backlog.md", "progress": "docs/progress.md",
         "release_gates": "docs/release-gates.json"}


def _accepted_but_unsynced(world):
    """A lead acceptance the bridge has recorded in the journal but not yet in the log."""
    world.task(project_task=world.ref, handoff={"evidence": {"head": world.head}})
    _journal_accept(world)
    (world.repo / ".repacted" / "config.json").write_text(json.dumps({"views": VIEWS}))
    assert world.project_task()["status"] != "done"


def _settled(text: str) -> str:
    return text.split("## Done or dropped", 1)[1]


def test_render_syncs_before_it_renders(world):
    _accepted_but_unsynced(world)
    result = run_cli(world.repo, "render")
    assert result.returncode == 0, result.stderr
    assert world.project_task()["status"] == "done"
    assert world.ref in _settled((world.repo / VIEWS["backlog"]).read_text())


def test_commit_syncs_then_renders_then_commits(world):
    _accepted_but_unsynced(world)
    result = run_cli(world.repo, "commit")
    assert result.returncode == 0, result.stderr
    log = git(world.repo, "show", "HEAD:.repacted/tasks.jsonl").stdout
    assert any(json.loads(line)["op"] == "accept" for line in log.splitlines())
    assert world.ref in _settled(git(world.repo, "show", f"HEAD:{VIEWS['backlog']}").stdout)
    left = git(world.repo, "status", "--porcelain", "--", ".repacted/tasks.jsonl", *VIEWS.values()).stdout
    assert left == ""


def test_a_write_free_mode_never_syncs(world):
    _accepted_but_unsynced(world)
    (world.repo / "docs").mkdir(exist_ok=True)
    (world.repo / "docs" / "notes.md").write_text("- **An idea.** worth a task.\n")
    (world.repo / "docs" / "gates.json").write_text(json.dumps({"gates": {"repacted-x": []}}))
    log = world.repo / ".repacted" / "tasks.jsonl"
    before = log.read_bytes()
    for args in (["import-md", "docs/notes.md"], ["import-md", "docs/notes.md", "--json"],
                 ["import-gates", "docs/gates.json"], ["import-pact-backlog", "--json", "none.json"],
                 ["sync", "--check"]):
        run_cli(world.repo, *args)
        assert log.read_bytes() == before, args
    assert world.project_task()["status"] != "done"
