"""tests/test_workflow_state.py: the lead's workflow state on disk and the resume screen.

One row per behaviour (hooks/shared/workflow_state.py): dispatch, accept, reject and amend
each update workflow-state.json through workflow_state.update, the call every `repact` op
makes (hooks/shared/ops/sides.py); the screen stays within 40 lines; --rebuild from the task files and the journal equals the
incremental file; a missing or stale file leaves the post-compaction checkpoint in place,
a fresh one replaces it with the screen.
"""

from __future__ import annotations

import importlib.util
import sys
import json
from pathlib import Path

import pytest

import shared.pact_context as ctx
from shared import session_init_resume, workflow_state

TEAM = "session-ws"
BIN = Path(__file__).resolve().parent.parent / "bin"


def _load_front():
    """bin/_front as a package; its __init__ puts bin/ and hooks/ on the import path."""
    if "_front" not in sys.modules:
        init = BIN / "_front" / "__init__.py"
        spec = importlib.util.spec_from_file_location(
            "_front", init, submodule_search_locations=[str(init.parent)])
        module = importlib.util.module_from_spec(spec)
        sys.modules["_front"] = module
        spec.loader.exec_module(module)
    return sys.modules["_front"]


@pytest.fixture
def world(tmp_path, monkeypatch):
    config = tmp_path / "config"
    (config / "tasks" / TEAM).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    session_dir = config / "pact-sessions" / "proj" / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(
        json.dumps({"team_name": TEAM, "project_dir": str(tmp_path / "proj"), "session_id": "s1"}))
    (session_dir / "session-journal.jsonl").write_text("")
    monkeypatch.setattr(ctx, "init", lambda payload: None)
    monkeypatch.setattr(ctx, "get_team_name", lambda: TEAM)
    monkeypatch.setattr(ctx, "get_session_dir", lambda: str(session_dir))
    monkeypatch.setattr(ctx, "get_project_dir", lambda: "")
    return {"tasks": config / "tasks" / TEAM, "session_dir": session_dir}


def _task(world, task_id, subject, status="pending", owner="", **metadata):
    body = {"id": task_id, "subject": subject, "status": status, "owner": owner,
            "blocks": [], "blockedBy": [], "metadata": metadata}
    (world["tasks"] / f"{task_id}.json").write_text(json.dumps(body))


def _event(world, tool, task_id):
    """The update a `repact` op makes after it wrote the task file (ops/sides.workflow)."""
    workflow_state.update(str(world["session_dir"]), TEAM, "", [task_id])
    return workflow_state.load(str(world["session_dir"]))


def _journal(world, **event):
    with open(world["session_dir"] / "session-journal.jsonl", "a") as handle:
        handle.write(json.dumps(dict({"v": 1, "ts": "2026-10-18T12:00:00+00:00"}, **event)) + "\n")


HANDOFF = {"rev": 1, "amendments": {}, "produced": ["x"], "decisions": [], "uncertainty": [],
           "integration": "none", "open_questions": []}


def test_dispatch_records_the_teachback_and_work_tasks_with_their_wait_state(world):
    _task(world, "2", "coder: TEACHBACK for the thing")
    _task(world, "3", "coder: build the thing")
    _event(world, "TaskCreate", "2")
    state = _event(world, "TaskCreate", "3")
    assert state["tasks"]["2"]["wait"] == "teachback not claimed"
    assert state["tasks"]["3"]["wait"] == "pending"

    _task(world, "2", "coder: TEACHBACK for the thing", "in_progress", "coder",
          teachback_submit={"understanding": "u"})
    state = _event(world, "TaskUpdate", "2")
    assert state["tasks"]["2"]["wait"] == "teachback awaiting your acceptance"
    assert state["tasks"]["2"]["digest"]


def test_accept_moves_the_task_to_the_last_accepted_with_rev_and_digest(world):
    _task(world, "3", "coder: build the thing", "in_progress", "coder", handoff=HANDOFF)
    state = _event(world, "TaskUpdate", "3")
    assert state["tasks"]["3"]["wait"] == "HANDOFF stored, awaiting your acceptance"

    _task(world, "3", "coder: build the thing", "completed", "coder", handoff=HANDOFF)
    state = _event(world, "TaskCompleted", "3")
    assert "3" not in state["tasks"]
    assert state["accepted"] == [{"id": "3", "subject": "coder: build the thing", "owner": "coder",
                                  "rev": 1, "digest": state["accepted"][0]["digest"]}]
    assert len(state["accepted"][0]["digest"]) == 12


def test_reject_shows_rework_until_the_owner_writes_a_newer_rev(world):
    rejection = {"reason": "r", "corrections": ["c"], "since": "2026-10-18T12:00:00+00:00",
                 "revision_number": 1, "rev": 1}
    _task(world, "3", "coder: build", "in_progress", "coder", handoff=HANDOFF,
          handoff_rejection=rejection)
    assert _event(world, "TaskUpdate", "3")["tasks"]["3"]["wait"] == "HANDOFF rejected, rework"

    _task(world, "3", "coder: build", "in_progress", "coder", handoff=dict(HANDOFF, rev=2),
          handoff_rejection=rejection, revision_number=2)
    assert _event(world, "TaskUpdate", "3")["tasks"]["3"]["wait"] == \
        "HANDOFF stored, awaiting your acceptance"


def test_an_amendment_is_listed_until_the_handoff_disposes_of_it(world):
    amend = {"text": "use X", "at": "2026-10-18T12:00:00+00:00", "from": "team-lead", "kind": "ruling"}
    _task(world, "3", "coder: build", "in_progress", "coder", amend_1=amend)
    assert _event(world, "TaskUpdate", "3")["tasks"]["3"]["amends"] == ["amend_1"]

    _task(world, "3", "coder: build", "in_progress", "coder", amend_1=amend,
          handoff=dict(HANDOFF, amendments={"amend_1": "applied: x.py"}))
    assert "amends" not in _event(world, "TaskUpdate", "3")["tasks"]["3"]




def test_the_screen_stays_within_40_lines_and_names_what_waits_on_the_lead(world):
    for n in range(10, 110):
        _task(world, str(n), f"coder-{n}: build part {n}", "in_progress", f"coder-{n}")
    _task(world, "200", "coder: build the last", "in_progress", "coder", handoff=HANDOFF)
    state = workflow_state.rebuild(str(world["session_dir"]), TEAM, "")
    screen = workflow_state.render(state).splitlines()
    assert len(screen) <= workflow_state.SCREEN_LINES
    assert any(line.startswith("  ... ") and "run TaskList" in line for line in screen)
    assert screen[-1] == "Next: accept or reject #200 (read each task file first)."
    assert len(state["tasks"]) == workflow_state.MAX_TASKS


def test_rebuild_from_the_task_files_and_journal_equals_the_incremental_file(world):
    _journal(world, type="variety_assessed", task_id="1", variety={"total": 9})
    _task(world, "1", "Build the feature", "in_progress", "team-lead")
    _event(world, "TaskCreate", "1")
    _journal(world, type="phase_transition", phase="CODE", status="started")
    _task(world, "2", "coder: TEACHBACK for build")
    _task(world, "3", "coder: build")
    _event(world, "TaskCreate", "2")
    _event(world, "TaskCreate", "3")
    _journal(world, type="variety_assessed", task_id="3", scope="dispatch", variety={"total": 9})
    _task(world, "2", "coder: TEACHBACK for build", "completed", "coder")
    _event(world, "TaskUpdate", "2")
    _task(world, "3", "coder: build", "in_progress", "coder", handoff=HANDOFF)
    _event(world, "TaskUpdate", "3")
    _task(world, "4", "ALERT: halt", "pending", "", type="algedonic", level="HALT")
    _event(world, "TaskCreate", "4")
    _task(world, "3", "coder: build", "completed", "coder", handoff=HANDOFF)
    incremental = _event(world, "TaskUpdate", "3")

    rebuilt = workflow_state.rebuild(str(world["session_dir"]), TEAM, "")
    assert workflow_state.comparable(rebuilt) == workflow_state.comparable(incremental)
    assert incremental["phase"] == "CODE"
    assert incremental["feature"] == {"id": "1", "subject": "Build the feature"}
    assert list(incremental["signals"]) == ["4"]

    _load_front()
    status = importlib.import_module("_front.status")
    (world["session_dir"] / workflow_state.STATE_NAME).unlink()
    assert status.show(None, str(world["session_dir"]), rebuild=True)[0] == 0
    assert workflow_state.comparable(workflow_state.load(str(world["session_dir"]))) == \
        workflow_state.comparable(incremental)


def _compact_context(world, tasks):
    parts = []
    session_init_resume.compact_directive(
        parts, tasks, True, str(world["session_dir"]), "/plugin", _role_line="", _ran_sentence="",
        _team_line="", _substitutions="", _team_directive="DIRECTIVE")
    return "\n".join(parts)


def test_a_missing_or_stale_file_keeps_the_checkpoint_and_a_fresh_one_shows_the_screen(world):
    _task(world, "3", "coder: build", "in_progress", "coder")
    tasks = [json.loads((world["tasks"] / "3.json").read_text())]

    missing = _compact_context(world, tasks)
    assert "[POST-COMPACTION CHECKPOINT]" in missing and "[RESUME]" not in missing

    _event(world, "TaskUpdate", "3")
    fresh = _compact_context(world, tasks)
    assert "[RESUME]" in fresh and "[POST-COMPACTION CHECKPOINT]" not in fresh
    assert "#3 coder: working" in fresh

    _journal(world, type="metadata_write", task_id="3", key="handoff", agent="coder", bytes=10)
    stale = _compact_context(world, tasks)
    assert "[POST-COMPACTION CHECKPOINT]" in stale and "[RESUME]" not in stale
