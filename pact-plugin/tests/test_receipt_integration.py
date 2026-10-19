"""
Location: pact-plugin/tests/test_receipt_integration.py
Summary: The change receipt (`repact inspect`) and integration acceptance
(`repact accept --feature`), on a temp repo with real git worktrees and tcount ledger
rows built here. The receipt puts a waiver at the top, lists a stale run with the files that
changed after it, and shows a change to an acceptance test. Integration: two branches of one
feature, each accepted, keep their task-log rows open; merged with one acceptance criterion
failing in the combined tree, `accept --feature` is refused and writes nothing; once fixed, it
writes the feature record and closes both rows.
Used by: pytest (the ops world of tests/fixtures/ops_env.py).
"""

from __future__ import annotations

import datetime
import itertools
import json
from pathlib import Path

import pytest

from fixtures.ops_env import World, git, handoff, ops_env, teachback  # noqa: F401  # fixture
from shared import content_tree as ct
from shared import evidence_check as ec
from shared.ops import accept, frame, integrate, receipt, submit
from shared.ops import evidence as EV
from shared.task_log import features
from shared.task_log import write as W
from shared.task_store import TaskRef

_pid = itertools.count(1000)
WHY = "the teammate died; I read the diff by hand"


@pytest.fixture(autouse=True)
def gate_mode(monkeypatch):
    """Judge in the shipped 'gate' mode whatever the local checks.json says."""
    monkeypatch.setattr(ec, "use_mode", lambda use: "gate")


def stamp(minutes_ago: int = 0) -> str:
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def run_row(tree: str, cmd: str, passed: int = 3, failed: int = 0, minutes_ago: int = 0) -> dict:
    """One tcount ledger row taken in `tree`, with the content tree it saw now."""
    return {"source": "tcount", "cmd": cmd, "cwd": tree, "ts": stamp(minutes_ago),
            "collected": passed + failed, "passed": passed, "failed": failed, "errors": 0,
            "skipped": 0, "exit_code": 1 if failed else 0, "count_parsed": True,
            "log_path": f"runs/2026-10-19T{next(_pid):06d}Z-{next(_pid)}-pytest.log",
            "tree": ct.content_tree(tree)}


def commit_file(tree: str, rel: str, text: str, message: str = "change") -> None:
    path = Path(tree) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(Path(tree), "add", "-A")
    git(Path(tree), "commit", "-q", "-m", message)


def pin(world: World, task_id: str, pins: dict) -> None:
    """Pin acceptance commands on the dispatch record, as `--acceptance` would."""
    def mutate(task: dict) -> bool:
        task["metadata"]["dispatch"]["acceptance_pin"] = pins
        return True
    world.store.update(TaskRef("team-ops", task_id), mutate)


def started(world: World, **dispatch) -> str:
    """A task dispatched to coder-1 with its teachback in (auto-accepted at this variety)."""
    task_id = world.dispatch(**dispatch)
    submit.teachback(world.coder(), task_id, teachback())
    return task_id


# --- the receipt ---------------------------------------------------------------------------

def test_receipt_shows_the_waiver_first_a_stale_run_with_its_files_and_a_changed_acceptance_test(
        ops_env: World, monkeypatch):
    world = ops_env
    commit_file(str(world.repo), "tests/test_parser.py", "def test_empty():\n    pass\n", "test")
    task_id = started(world)
    tree = world.worktree(task_id)
    pin(world, task_id, {"AC-1": "pytest -q tests/test_parser.py"})
    commit_file(tree, "tests/test_parser.py", "def test_empty():\n    assert True\n", "tighten the test")
    stale = run_row(tree, "pytest -q tests/test_parser.py")
    commit_file(tree, "parser.py", "def parse(text):\n    return text or ''\n", "fix")
    submit.handoff(world.coder(), task_id, handoff(tree, refs={"AC-1": "done: run"},
                   uncertainty=["known-limit: the old flag is not tested"]))
    monkeypatch.setattr(EV, "verdict", lambda *a, **k: {"problems": ["no passing run on the current files"],
                                                        "notes": []})
    assert accept.run(world.ctx(), task_id, waive=WHY).exit_code == 0

    lines = receipt.render(receipt.task_receipt(world.ctx(), task_id, runs=[stale]))
    text = "\n".join(lines)
    assert lines[0].startswith("WAIVED: ") and WHY in lines[0]
    stale_line = next(line for line in lines if "`pytest -q tests/test_parser.py`: 3 passed" in line)
    assert "parser.py" in stale_line and "changed after this run" in stale_line
    tests_part = text.split("Acceptance tests changed", 1)[1]
    assert "tests/test_parser.py" in tests_part.splitlines()[1]
    assert "the old flag is not tested" in text
    assert len(lines) <= 40


# --- integration -----------------------------------------------------------------------------

def test_a_feature_integrates_only_on_the_merged_tree_and_then_closes_its_rows(ops_env: World):
    world = ops_env
    store = W.init(world.repo)
    ref_a, ref_b = store.add("Search: the index"), store.add("Search: the query")
    second = world.ctx(frame.Caller(frame.CODER, "coder-2"))
    task_a = world.dispatch(feature="search", refs=[ref_a], branch="search/index")
    task_b = world.dispatch(name="coder-2", feature="search", refs=[ref_b], branch="search/query")
    submit.teachback(world.coder(), task_a, teachback())
    submit.teachback(second, task_b, teachback())
    tree_a, tree_b = world.worktree(task_a), world.worktree(task_b)
    commit_file(tree_a, "search/index.py", "def index(x):\n    return [x]\n", "index")
    commit_file(tree_a, "tests/test_search.py", "def test_search():\n    pass\n", "test")
    commit_file(tree_b, "search/query.py", "def query(x):\n    return x\n", "query")
    pin(world, task_a, {"AC-1": "pytest -q tests/test_search.py"})
    submit.handoff(world.coder(), task_a, handoff(tree_a, refs={ref_a: "done: index", "AC-1": "done: run"}))
    submit.handoff(second, task_b, handoff(tree_b, refs={ref_b: "done: query"}))
    for task_id in (task_a, task_b):
        assert accept.run(world.ctx(), task_id).exit_code == 0
        assert world.task(task_id)["metadata"]["acceptance"]["statuses"]["integrated"]["state"] == "waiting"
    rows = W.Store.locate(world.repo).state().tasks
    assert rows[ref_a]["status"] != "done" and rows[ref_b]["status"] != "done"

    merged = str(world.repo / ".worktrees" / "search")
    git(world.repo, "worktree", "add", "-q", "-b", "feature-search", merged, "main")
    git(Path(merged), "merge", "-q", "--no-edit", "search/index")
    git(Path(merged), "merge", "-q", "--no-edit", "search/query")
    red = run_row(merged, "pytest -q tests/test_search.py", passed=0, failed=1)
    refused = integrate.run(world.ctx(), "search", runs=[red])
    assert refused.exit_code == 1 and any("AC-1" in line for line in refused.lines)
    assert features.read(world.repo) == []
    rows = W.Store.locate(world.repo).state().tasks
    assert rows[ref_a]["status"] != "done" and rows[ref_b]["status"] != "done"

    commit_file(merged, "search/index.py", "def index(x):\n    return [x, x]\n", "fix the merged index")
    green = run_row(merged, "pytest -q tests/test_search.py")
    passed = integrate.run(world.ctx(), "search", runs=[red, green])
    assert passed.exit_code == 0, passed.lines
    record = features.read(world.repo)[-1]
    assert record["feature"] == "search" and record["tree"] == ct.content_tree(merged)
    assert sorted(record["tasks"]) == sorted([task_a, task_b])
    rows = W.Store.locate(world.repo).state().tasks
    assert rows[ref_a]["status"] == "done" and rows[ref_b]["status"] == "done"
    feature_text = "\n".join(receipt.render_feature(receipt.feature_receipt(world.ctx(), "search", runs=[red, green])))
    assert "integrated" in feature_text and f"#{task_a}" in feature_text and f"#{task_b}" in feature_text


def test_a_feature_with_one_task_is_integrated_at_accept_and_its_row_closes(ops_env: World):
    world = ops_env
    store = W.init(world.repo)
    ref = store.add("Search: alone")
    task_id = started(world, feature="solo", refs=[ref])
    submit.handoff(world.coder(), task_id, handoff(world.worktree(task_id), refs={ref: "done: it"}))
    head = git(Path(world.worktree(task_id)), "rev-parse", "HEAD")
    submit_meta = world.task(task_id)["metadata"]
    assert submit_meta["dispatch"]["feature"] == "solo"
    world.store.update(TaskRef("team-ops", task_id), lambda task: task["metadata"]["handoff"].update(
        evidence={"head": head}) or True)
    assert accept.run(world.ctx(), task_id).exit_code == 0
    assert world.task(task_id)["metadata"]["acceptance"]["statuses"]["integrated"]["state"] == "pass"
    assert W.Store.locate(world.repo).state().tasks[ref]["status"] == "done"
    assert json.dumps(features.read(world.repo)) == "[]"


def test_bin_repact_prints_the_whole_receipt_and_takes_a_task_or_a_feature(ops_env: World):
    from test_ops_cli import _repact
    started(ops_env)
    shown = _repact(ops_env, "inspect", "1")
    assert shown.returncode == 0, shown.stderr
    assert shown.stdout.startswith("#1 Fix the parser") and len(shown.stdout.splitlines()) > 6
    assert json.loads(_repact(ops_env, "inspect", "1", "--json").stdout)["task_id"] == "1"
    both = _repact(ops_env, "accept", "1", "--feature", "search")
    assert both.returncode == 2 and "a task id or --feature" in both.stderr
