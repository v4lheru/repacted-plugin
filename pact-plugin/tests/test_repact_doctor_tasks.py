"""
D10 P3: the doctor's `tasks` check (design section 13 P3; bin/_doctor.py check_tasks).

It looks at the task log of the repo under test and of every project registered with init
run in ${CLAUDE_PLUGIN_DATA}/projects.json (P6's registry), and reports:
  red    a corrupt line in a log;
  amber  events left uncommitted for more than 24 h, a generated view edited by hand, or a legacy
         ~/.claude/pact-backlog store still present (with the importer to run);
  skip   when there is no task log to look at and no legacy store;
  green  otherwise.
Runs bin/repact-doctor as a subprocess against test_repact_doctor's scratch world.
"""
from __future__ import annotations

import datetime
import json

import pytest

from project_tasks_support import PLUGIN_ROOT, load, writer
from test_repact_doctor import World, git


def old_ts(hours: float) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def append_raw_event(repo, hours_ago: float, n: int = 1):
    ts = old_ts(hours_ago)
    eid = ts.replace("-", "").replace(":", "").replace(".", "") + f"-{n:08x}"
    event = {"v": 1, "eid": eid, "ts": ts, "by": "lead", "op": "create", "t": f"T-{n:08x}",
             "title": f"an old uncommitted task {n}", "status": "planned", "priority": "P2"}
    with open(repo / ".repacted" / "tasks.jsonl", "a") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")


def registry_doc(main, project_id, bootstrap=None):
    """A projects.json in P6's registry shape (bin/_tasks/registry.py owns it)."""
    key = str(main.resolve())
    return {"version": 1, "checkouts": {key: {
        "repo_path": key, "project_id": project_id, "slug": "other" if project_id else None,
        "init_at": old_ts(1) if project_id else None, "bootstrap": bootstrap, "last_known": None}},
        "atlas": {}}


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def tasks_state(world, *args):
    return world.states(*args)["tasks"]


def test_a_committed_clean_log_is_green(world):
    state = tasks_state(world)
    assert state["state"] == "green", state


def test_the_check_is_part_of_quick(world):
    assert tasks_state(world, "--quick")["state"] == "green"


def test_fresh_uncommitted_events_are_still_green(world):
    append_raw_event(world.repo, hours_ago=1)
    assert tasks_state(world)["state"] == "green"


def test_events_uncommitted_for_over_a_day_are_amber(world):
    append_raw_event(world.repo, hours_ago=30)
    state = tasks_state(world)
    assert state["state"] == "amber" and "uncommitted" in state["summary"]
    assert any("repact-task commit" in line for line in state["detail"])


def test_committing_clears_it(world):
    append_raw_event(world.repo, hours_ago=30)
    git(world.repo, "add", ".repacted/tasks.jsonl")
    git(world.repo, "commit", "-q", "-m", "log")
    assert tasks_state(world)["state"] == "green"


def test_a_corrupt_line_is_red(world):
    with open(world.repo / ".repacted" / "tasks.jsonl", "a") as handle:
        handle.write("{this is not json\n")
    state = tasks_state(world)
    assert state["state"] == "red" and "corrupt" in state["summary"]
    assert any("line " in line for line in state["detail"])


def test_a_hand_edited_view_is_amber(world):
    V = load("views_for_doctor_test", PLUGIN_ROOT / "bin" / "_tasks" / "views.py")
    writer().Store(world.repo).add("A task for the view")
    V.configure(world.repo)
    V.render(world.repo)
    git(world.repo, "add", "-A")
    git(world.repo, "commit", "-q", "-m", "views")
    assert tasks_state(world)["state"] == "green"
    backlog = world.repo / "docs" / "backlog.md"
    backlog.write_text(backlog.read_text().replace("A task for the view", "A task EDITED by hand"))
    state = tasks_state(world)
    assert state["state"] == "amber" and "hand" in state["summary"]
    assert any("docs/backlog.md" in line for line in state["detail"])


def test_a_legacy_backlog_store_is_amber_and_names_the_importer(world):
    legacy = world.home / ".claude" / "pact-backlog"
    legacy.mkdir(parents=True)
    (legacy / "someproject.json").write_text("{}")
    state = tasks_state(world)
    assert state["state"] == "amber"
    assert any("import-pact-backlog" in line for line in state["detail"])


def test_without_any_log_the_check_skips(world):
    (world.repo / ".repacted" / "tasks.jsonl").unlink()
    assert tasks_state(world)["state"] == "skip"


def test_a_registered_project_is_checked_too(world, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    writer().init(other)
    with open(other / ".repacted" / "tasks.jsonl", "a") as handle:
        handle.write("garbage\n")
    world.data.mkdir(parents=True, exist_ok=True)
    (world.data / "projects.json").write_text(json.dumps(registry_doc(other, project_id="p-1")))
    state = tasks_state(world)
    assert state["state"] == "red"
    assert any(str(other.resolve()) in line or other.name in line for line in state["detail"])


def test_a_declined_offer_is_not_checked(world, tmp_path):
    other = tmp_path / "declined"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    writer().init(other)
    with open(other / ".repacted" / "tasks.jsonl", "a") as handle:
        handle.write("garbage\n")
    world.data.mkdir(parents=True, exist_ok=True)
    (world.data / "projects.json").write_text(json.dumps(
        registry_doc(other, project_id=None, bootstrap={"offered": old_ts(1), "answer": "declined"})))
    assert tasks_state(world)["state"] == "green"
