"""In an autopilot run or unattended mode a task-log row closes only
on an accepted HANDOFF, the evidence controls read unattended mode as ON when either switch says
so, an honestly relayed headless task closes on the runs its acceptance record names,
`claude -p` sessions use the team name the platform keeps on disk, and the lead can store a
relayed HANDOFF from the teammate's saved output.

Location: pact-plugin/tests/test_close_gate.py. Reuses the bridge world of
tests/test_project_tasks_bridge.py.
Run: tcount pytest -q tests/test_close_gate.py (from the plugin root)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from shared import autopilot_run as R
from shared import unattended
from shared.team_name import session_team_name
from test_project_tasks_bridge import (  # noqa: F401 - fixture
    GIT_ENV, PLUGIN_ROOT, SID, TASK_B, TEAM, bind_ref, frame, lead_accepts, run_hook, world,
)

BIN = PLUGIN_ROOT / "bin"
RULE = "[rule: task_log.close_unaccepted]"
HANDOFF = {"rev": 1, "produced": ["/abs/impl.py: the fix"], "decisions": [], "uncertainty": [],
           "integration": [], "open_questions": [], "amendments": {}}


@pytest.fixture(autouse=True)
def quiet_env(monkeypatch):
    for name in ("REPACT_UNATTENDED", "CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT"):
        monkeypatch.delenv(name, raising=False)


def cli(world, name, *args, session=True, env=None):
    """A bin tool run from the repo; `session` gives it this session's id and project dir."""
    extra = {"CLAUDE_CODE_SESSION_ID": SID, "CLAUDE_PROJECT_DIR": str(world.repo)} if session else {}
    return subprocess.run([sys.executable, str(BIN / name), *args], cwd=str(world.repo), capture_output=True,
                          text=True, timeout=60, env={**os.environ, **GIT_ENV, **extra, **(env or {})})


def done(world, *extra, **kwargs):
    return cli(world, "repact-task", "done", world.ref, "--evidence", f"commit:{world.head}", *extra, **kwargs)


def is_open(world):
    return world.project_task()["status"] != "done"


def autopilot(world, *args):
    return cli(world, "repact-autopilot", *args, "--session-dir", str(world.session_dir))


def accepted(world, task_id=TASK_B, dispatch=None, **acceptance):
    """The metadata `repact dispatch` and `repact accept` leave: a dispatch record and an acceptance
    whose op_id has an accept row and a done row in the session's ops.jsonl."""
    op_id = f"acc{task_id}".ljust(16, "0")
    with (world.session_dir / "ops.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"op": "accept", "op_id": op_id, "task": task_id}) + "\n")
        log.write(json.dumps({"op_id": op_id, "done": True}) + "\n")
    return {"dispatch": dispatch or {"name": "coder"}, "acceptance": {"op_id": op_id, **acceptance}}


# ---------------------------------------------------------------------------------------------
# the close gate on `repact-task done` and `repact-autopilot record done`
# ---------------------------------------------------------------------------------------------

def test_unattended_done_on_a_row_whose_task_is_pending_is_refused_with_the_way_through(world, monkeypatch):
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    bind_ref(world)
    world.task(status="in_progress")
    refused = done(world)
    assert refused.returncode == 65 and RULE in refused.stderr
    assert "not completed" in refused.stderr and "--waive" in refused.stderr
    assert is_open(world)


def test_a_task_completed_with_no_handoff_does_not_close_the_row(world, monkeypatch):
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    bind_ref(world)
    world.task(project_task=world.ref, **accepted(world))
    refused = done(world)
    assert refused.returncode == 65 and "no stored HANDOFF" in refused.stderr and is_open(world)


def test_a_link_to_another_teams_task_is_refused(world, monkeypatch):
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    other = world.home / "tasks" / "session-other000"
    other.mkdir(parents=True)
    (other / "7.json").write_text(json.dumps({"id": "7", "status": "completed", "owner": "coder",
                                              "metadata": {"handoff": HANDOFF}}))
    world.plant_run()
    world.store.start(world.ref, ptask="session-other000/7")
    refused = done(world)
    assert refused.returncode == 65 and "not a task of this session's team" in refused.stderr and is_open(world)


def test_a_row_with_no_linked_task_is_refused(world, monkeypatch):
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    refused = done(world)
    assert refused.returncode == 65 and "no platform task of this session is linked" in refused.stderr


@pytest.mark.parametrize("reason", ["",])
def test_a_waiver_with_no_reason_is_refused(world, monkeypatch, reason):
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    bind_ref(world)
    world.task(status="in_progress")
    refused = done(world, "--waive", reason)
    assert refused.returncode == 65 and "a waiver needs a reason" in refused.stderr and is_open(world)


def test_an_unattended_waiver_parks_the_row_and_is_logged(world, monkeypatch):
    """Unattended, a waiver never closes the row."""
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    bind_ref(world)
    world.task(status="in_progress")
    parked = done(world, "--waive", "the teammate died; I verified the commit by hand")
    assert parked.returncode == 0, parked.stderr
    assert "parked for a human" in parked.stdout and is_open(world)
    assert world.project_task()["status"] == "blocked" and "parked" in world.project_task()["tags"]
    rows = [row for row in R.read_rows(str(world.session_dir)) if row.get("kind") == "park"]
    assert rows and rows[0]["ref"] == world.ref and "verified the commit" in rows[0]["why"]


def test_an_unattended_refusal_offers_the_waiver_only_as_a_park(world, monkeypatch):
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    bind_ref(world)
    world.task(status="in_progress")
    refused = done(world)
    assert refused.returncode == 65 and "park the row for a human with --waive" in refused.stderr


# ---------------------------------------------------------------------------------------------
# the evidence controls' reading of unattended mode, and a relayed headless task
# ---------------------------------------------------------------------------------------------


def held(world):
    return world.ref in R.evidence_holds(R.read_rows(str(world.session_dir)))


# ---------------------------------------------------------------------------------------------
# the team name under `claude -p`, and the dead twin link
# ---------------------------------------------------------------------------------------------

LONG_SID = "beef0001-1111-4222-8333-444444444444"
MINTED = "session-beef0001"


# ---------------------------------------------------------------------------------------------
# the lead stores a relayed HANDOFF from the teammate's saved output
# ---------------------------------------------------------------------------------------------

