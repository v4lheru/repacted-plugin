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


@pytest.mark.parametrize("reason", ["", "   ", "\t\n", "- !", "_"])
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


def test_an_attended_waiver_closes_the_row_with_the_waiver_on_it(world):
    """Attended (unattended mode off) on an autopilot-tagged row: the gate applies, the waiver closes."""
    assert cli(world, "repact-task", "set", world.ref, "--tag", "autopilot").returncode == 0
    bind_ref(world)
    world.task(status="in_progress")
    closed = done(world, "--waive", "the teammate died; I verified the commit by hand")
    assert closed.returncode == 0, closed.stderr
    assert "waived by the lead" in closed.stderr and not is_open(world)
    assert world.project_task()["waiver"]["why"].startswith("the teammate died")
    rows = [row for row in R.read_rows(str(world.session_dir)) if row.get("kind") == "close_waiver"]
    assert rows and rows[0]["ref"] == world.ref


def test_a_completed_task_with_a_handoff_and_a_recorded_run_closes(world, monkeypatch):
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    bind_ref(world)
    world.task(project_task=world.ref, handoff=HANDOFF, **accepted(world))
    world.plant_run()
    closed = done(world)
    assert closed.returncode == 0, closed.stderr
    assert not is_open(world)


def test_a_task_of_a_feature_waiting_for_integration_keeps_its_row_open_until_it_is_recorded(world, monkeypatch):
    """A row of a multi-task feature closes only on integrated."""
    from shared.task_log import features
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    bind_ref(world)
    waiting = {"statuses": {"integrated": {"state": "waiting", "line": "waits for the merged tree"}}}
    world.task(project_task=world.ref, handoff=HANDOFF, **accepted(world, dispatch={"feature": "search"}, **waiting))
    world.plant_run()
    refused = done(world)
    assert refused.returncode != 0 and "repact accept --feature search" in refused.stderr
    features.append(world.repo, {"feature": "search", "tree": "t"})
    assert done(world).returncode == 0 and not is_open(world)


def test_outside_autopilot_and_unattended_mode_the_row_closes_as_before(world):
    bind_ref(world)
    world.task(status="in_progress")
    closed = done(world)
    assert closed.returncode == 0, closed.stderr
    assert not is_open(world)


def test_an_autopilot_row_closed_from_a_shell_with_no_session_fails_closed(world):
    unattended.write_flag(str(world.session_dir), True, "2026-10-18T10:00:00Z")
    assert autopilot(world, "start").returncode == 0
    assert autopilot(world, "begin", world.ref).returncode == 0
    assert "autopilot" in world.project_task()["tags"]
    bind_ref(world)
    world.task(project_task=world.ref, handoff=HANDOFF)
    world.plant_run()
    refused = done(world, session=False)
    assert refused.returncode == 65 and "no session directory is known" in refused.stderr and is_open(world)


def test_a_row_retagged_after_autopilot_began_it_still_fails_closed(world):
    """`set --tag chore` once dropped the autopilot tag, then a stripped-env done closed it."""
    unattended.write_flag(str(world.session_dir), True, "2026-10-18T10:00:00Z")
    assert autopilot(world, "start").returncode == 0
    assert autopilot(world, "begin", world.ref).returncode == 0
    bind_ref(world)
    world.task(status="in_progress")
    assert cli(world, "repact-task", "set", world.ref, "--tag", "chore").returncode == 0
    assert "autopilot" not in world.project_task()["tags"]
    refused = done(world, session=False)
    assert refused.returncode == 65 and RULE in refused.stderr and is_open(world)


def test_record_done_leaves_the_row_open_and_a_second_start_does_not_open_the_way(world):
    unattended.write_flag(str(world.session_dir), True, "2026-10-18T10:00:00Z")
    assert autopilot(world, "start").returncode == 0
    bind_ref(world)
    world.task(status="in_progress")
    first = autopilot(world, "record", world.ref, "done", "--branch", "main")
    assert first.returncode == 0 and "not completed" in first.stdout and is_open(world)
    assert autopilot(world, "start").returncode == 0
    again = autopilot(world, "record", world.ref, "done", "--branch", "main")
    assert again.returncode == 0 and is_open(world)
    assert done(world).returncode == 65 and is_open(world)


# ---------------------------------------------------------------------------------------------
# the evidence controls' reading of unattended mode, and a relayed headless task
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("env, flag, on", [("1", "off", True), (None, "garbage", True), (None, "on", True),
                                           (None, None, False), (None, "off", False)])
def test_the_controls_read_unattended_on_when_either_switch_says_so(tmp_path, env, flag, on):
    if flag == "garbage":
        (tmp_path / "unattended.json").write_text("garbage")
    elif flag:
        unattended.write_flag(str(tmp_path), flag == "on", "2026-10-18T10:00:00Z")
    environ = {unattended.ENV_NAME: env} if env else {}
    assert unattended.controls_on(str(tmp_path), environ).on is on


def held(world):
    return world.ref in R.evidence_holds(R.read_rows(str(world.session_dir)))


def test_a_garbled_flag_keeps_the_hold_under_env_on(world, monkeypatch):
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    R.append(str(world.session_dir), {"kind": "start", "id": "run-1"})
    bind_ref(world)
    (world.session_dir / "unattended.json").write_text("garbage")
    world.task(project_task=world.ref, handoff={"produced": ["x"]})
    run_hook(lead_accepts(world))
    assert held(world) and is_open(world)


def test_an_honest_headless_task_closes_on_the_runs_its_acceptance_names(world, monkeypatch):
    """A `claude -p` teammate's runs carry no team or task, so the row closes on
    the runs `repact accept` wrote into metadata.acceptance.runs, and stays open without them."""
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    bind_ref(world)
    name = world.plant_run(name="2026-09-27T200000Z-4711-pytest.log", task_id=None)
    world.task(project_task=world.ref, handoff={**HANDOFF, "relayed": True}, **accepted(world))
    refused = done(world)
    assert refused.returncode == 65 and "no evidence" in refused.stderr and is_open(world)
    world.task(project_task=world.ref, handoff={**HANDOFF, "relayed": True},
               **accepted(world, runs=[name[:-len("-pytest.log")]]))
    closed = done(world)
    assert closed.returncode == 0, closed.stderr
    assert not is_open(world)


# ---------------------------------------------------------------------------------------------
# the team name under `claude -p`, and the dead twin link
# ---------------------------------------------------------------------------------------------

LONG_SID = "beef0001-1111-4222-8333-444444444444"
MINTED = "session-beef0001"


@pytest.mark.parametrize("entrypoint, dirs, expected", [
    ("sdk-cli", [], LONG_SID),
    ("cli", [], MINTED),
    ("sdk-cli", [f"teams/{MINTED}"], MINTED),
    ("cli", [f"teams/{LONG_SID}"], LONG_SID),
    ("cli", [f"teams/{MINTED}", f"teams/{LONG_SID}"], MINTED),
])
def test_the_session_team_name_is_the_one_the_platform_keeps(tmp_path, entrypoint, dirs, expected):
    for name in dirs:
        (tmp_path / name).mkdir(parents=True)
    got = session_team_name({"session_id": LONG_SID}, teams_root=tmp_path,
                            environ={"CLAUDE_CODE_ENTRYPOINT": entrypoint})
    assert got == expected


def test_binding_a_live_task_drops_the_dead_twin_link(world):
    world.store.start(world.ref, ptask=f"session-dead0000/{TASK_B}")
    world.task(status="in_progress")
    run_hook(frame(world, metadata={"project_task": world.ref}))
    targets = [link["target"] for link in world.project_task()["links"]]
    assert targets == [f"ptask:{TEAM}/{TASK_B}"]


# ---------------------------------------------------------------------------------------------
# the lead stores a relayed HANDOFF from the teammate's saved output
# ---------------------------------------------------------------------------------------------

def test_the_relayed_handoff_is_the_last_fenced_json_object_of_the_output(tmp_path):
    out = tmp_path / "out.txt"
    out.write_text("Done.\n```json\n{\"draft\": 1}\n```\nprose\n```json\n" + json.dumps(HANDOFF) + "\n```\n")
    got = subprocess.run(["bash", str(BIN / "repact"), "inspect", "--relayed-handoff", str(out)],
                         capture_output=True, text=True, timeout=30)
    assert got.returncode == 0, got.stderr
    assert json.loads(got.stdout) == {"handoff": {**HANDOFF, "relayed": True}}
    out.write_text("no block here\n```json\n[1, 2]\n```\n")
    missing = subprocess.run(["bash", str(BIN / "repact"), "inspect", "--relayed-handoff", str(out)],
                             capture_output=True, text=True, timeout=30)
    assert missing.returncode == 4 and missing.stdout == ""
