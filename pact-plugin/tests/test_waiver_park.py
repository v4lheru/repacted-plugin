"""
Location: pact-plugin/tests/test_waiver_park.py
Summary: Waivers and park. Unattended,
`repact accept --waive` never completes: it writes metadata.parked, leaves the task and its
task-log row open, and the inbox shows one item. Attended, it completes with
metadata.evidence_waiver and the task-log row closes with a `waiver` field. The third failed
accept of an unattended task parks instead of rejecting again. No refusal text offers --waive
as a way to close when unattended, and an evidence_hold is never logged for a closed row.
Used by: pytest (the ops world of tests/fixtures/ops_env.py).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from fixtures.ops_env import TEAM, World, handoff, ops_env, teachback  # noqa: F401  # fixture
from shared import autopilot_run as R
from shared import rules
from shared.ops import accept, submit
from shared.ops import evidence as EV
from shared.task_log import bridge
from shared.task_log import write as W

BIN = Path(__file__).resolve().parent.parent / "bin"
WHY = "the teammate died; I read the diff by hand"
FAILING = {"problems": ["no passing tcount run of the task since dispatch"], "notes": []}


@pytest.fixture
def logged(ops_env: World, monkeypatch, tmp_path):
    """The ops world with a task log, one row bound to a dispatched task with a HANDOFF in, and
    an evidence check that fails."""
    monkeypatch.delenv("REPACT_UNATTENDED", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))       # no KB: the inbox lists ours only
    store = W.init(ops_env.repo)
    ref = store.add("Fix the empty-input parser bug")
    task_id = ops_env.dispatch(refs=[ref])
    submit.teachback(ops_env.coder(), task_id, teachback())
    submit.handoff(ops_env.coder(), task_id, handoff(ops_env.worktree(task_id), refs={ref: "done: parser.py"}))
    monkeypatch.setattr(EV, "verdict", lambda *a, **k: dict(FAILING))
    return ops_env, W.Store.locate(ops_env.repo), ref, task_id


def row(store, ref) -> dict:
    return store.state().tasks[ref]


def inbox_items(repo: Path, session_dir: Path) -> list:
    done = subprocess.run(["bash", str(BIN / "repact"), "decide", "--json", "--session-dir", str(session_dir)],
                          cwd=str(repo), capture_output=True, text=True, timeout=60, env=dict(os.environ))
    assert done.returncode == 0, done.stderr
    return [item for item in json.loads(done.stdout)["items"] if item["source"] != "kb"]


def test_unattended_waive_parks_the_task_and_its_row_and_the_inbox_has_one_item(logged, monkeypatch):
    """The unattended lead took --waive at the first refusal and the task closed."""
    world, store, ref, task_id = logged
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    result = accept.run(world.ctx(), task_id, waive=WHY)
    task = world.task(task_id)
    assert result.exit_code == 0 and "parked" in result.lines[0]
    assert task["status"] == "in_progress"
    assert task["metadata"]["parked"]["why"] == WHY and "evidence_waiver" not in task["metadata"]
    assert row(store, ref)["status"] == "blocked"
    items = inbox_items(world.repo, world.session_dir)
    assert len(items) == 1 and items[0]["task"] == ref
    assert f"#{task_id} without evidence" in items[0]["question"] and "no passing tcount run" in items[0]["question"]


def test_attended_waive_completes_and_the_row_closes_with_the_waiver(logged):
    world, store, ref, task_id = logged
    result = accept.run(world.ctx(), task_id, waive=WHY)
    task = world.task(task_id)
    assert result.exit_code == 0 and result.lines[0].startswith("WAIVED: ")
    assert task["status"] == "completed"
    assert task["metadata"]["evidence_waiver"]["why"] == WHY
    assert task["metadata"]["evidence_waiver"]["attended"] is True
    closed = row(store, ref)
    assert closed["status"] == "done" and closed["waiver"]["why"] == WHY


def test_the_third_failed_accept_of_an_unattended_task_parks_it(logged, monkeypatch):
    world, store, ref, task_id = logged
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    for rev in (1, 2):
        assert accept.run(world.ctx(), task_id).exit_code == 1
        submit.handoff(world.coder(), task_id, handoff(world.worktree(task_id), decisions=[f"try {rev + 1}"],
                                                           refs={ref: "done: parser.py"}))
    parked = accept.run(world.ctx(), task_id)
    meta = world.task(task_id)["metadata"]
    assert parked.exit_code == 0 and "parked" in parked.lines[0]
    assert meta["parked"]["why"].startswith("rejection limit") and meta["handoff_rejection"]["rev"] == 2
    assert row(store, ref)["status"] == "blocked"


def test_no_refusal_text_offers_the_waiver_as_a_close_when_unattended():
    for rule_id in rules.RULES:
        text = rules.way_through(rule_id, unattended=True)
        assert "--waive" not in text or "park" in text, rule_id


def test_an_evidence_hold_is_never_logged_for_a_row_already_closed(logged, monkeypatch):
    """The hold row came after the row's close in autopilot.jsonl."""
    world, store, ref, task_id = logged
    (world.session_dir / "pact-session-context.json").write_text(json.dumps({"team_name": TEAM}))
    R.append(str(world.session_dir), {"kind": "start", "id": "run-1"})
    store.done(ref, [f"commit:{subprocess.run(['git', '-C', str(world.repo), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()}"])
    monkeypatch.setattr(bridge, "session_dir_of", lambda _sid: world.session_dir)
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    bridge.apply(store, TEAM, task_id, world.task(task_id), {"acceptance": {"rev": 1}}, ["accept"], "sid")
    assert not [r for r in R.read_rows(str(world.session_dir)) if r.get("kind") == "evidence_hold"]
