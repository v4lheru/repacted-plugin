"""
Location: pact-plugin/tests/test_stop_background_gate.py
Summary: Behaviour of the Stop turn-end gate (hooks/stop_background_gate.py
         over hooks/shared/turn_end_gate.py), driven through the production
         entry point as a subprocess against a temporary config root.
Used by: the pact-plugin test suite.

Every arm runs the real hook with CLAUDE_CONFIG_DIR and CLAUDE_PROJECT_DIR
pointed at tmp_path. Nothing in the resolution chain is stubbed: the team
config, the task store, the background-work registry, the session registry and
the session context file are written where production writes them, and only
for the sessions production writes them for. No context file is ever written
for a separate-process teammate's session, because session_init writes that
file for lead frames only; a fixture that wrote one would test a topology no
teammate is in.

REVERT CARDINALITY — MEASURED BEFORE THE LEAD STOPPED BEING REFUSED, AND NOT
MEASURED AGAIN SINCE. The lead arms now assert an allowed stop and its journal
row, and the block and told-once arms run on a separate-process teammate, so
the counts below no longer describe this file; the rows still name the
behaviours the arms cover. Each row names the behaviour one edit removes. That
edit was applied to its own copy of the tree, proved applied, and run against
this whole file with a fresh bytecode cache; the unmutated copy passed every
arm:
  hook file removed ................................. 33 failed (all but the six
                                                      arms that never run the
                                                      Stop hook: five call gate
                                                      functions in-process, and
                                                      the recorded subagent row
                                                      arm runs track_files.py
                                                      and the registry selector)
  block never printed ............................... 10 failed
  told-once read ignored ............................ 3 failed
  stop_hook_active guard removed .................... 2 failed
  cron check removed ................................ 4 failed
  lead branch returns every running job ............. 3 failed
  lead's recorded job-id set left empty ............. 3 failed
  leadSessionId branch removed ...................... 1 failed
  registry teammate branch removed .................. 6 failed
  unresolved frame given a session folder ........... 1 failed
  fast path removed ................................. 3 failed here, none in
                                                      test_validate_handoff_turn_end.py
  job-list cap removed .............................. 1 failed
  any agent_id treated as a teammate ................ 2 failed
  Layer 1 writer stops recording the job id ......... 3 failed, at the writer checks
  teammate check ignores the joined record .......... 1 failed
Re-measure every row whenever an arm here is added or parametrized, and restate
the table in the same commit. A parametrized arm counts once per case.
NOT RE-MEASURED since the lead's candidates became the jobs recorded as its own
launch: the two "lead" rows above describe the earlier subtract-the-recorded
rule, and every count predates the arms added with that change.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from helpers import effective_hooks_config  # noqa: E402

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
HOOK = HOOKS_DIR / "stop_background_gate.py"
TRACK_FILES = HOOKS_DIR / "track_files.py"

TEAM = "session-stopgate"
LEAD_SID = "aaaaaaaa-0000-4000-8000-000000000001"
MATE_SID = "bbbbbbbb-0000-4000-8000-000000000002"
PLAIN_SID = "cccccccc-0000-4000-8000-000000000003"
MATE = "mate"
LEAD_TYPE = "repacted:pact-orchestrator"
TOLD_FILENAME = "background-stop-told.json"
SUPPRESS = {"suppressOutput": True}


def _iso(minutes_ago: int = 0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.isoformat(timespec="seconds")


def job(job_id: str, **extra) -> dict:
    entry = {
        "id": job_id,
        "type": "shell",
        "status": "running",
        "description": f"Sleep in the background ({job_id})",
        "command": "sleep 300",
    }
    entry.update(extra)
    return entry


def stop_frame(session_id: str = LEAD_SID, jobs=(), **fields) -> dict:
    """The captured Stop key set, with running jobs and role fields added."""
    frame = {
        "hook_event_name": "Stop",
        "session_id": session_id,
        "transcript_path": "<transcript_path>",
        "cwd": "<cwd>",
        "permission_mode": "bypassPermissions",
        "last_assistant_message": "done",
        "background_tasks": list(jobs),
        "session_crons": [],
        "stop_hook_active": False,
    }
    frame.update(fields)
    return frame


def valid_wait() -> dict:
    now = _iso()
    return {
        "reason": "awaiting_peer_response",
        "expected_resolver": "peer",
        "since": now,
        "covers_since": now,
    }


class World:
    """A temporary config root laid out the way a live PACT team leaves it."""

    def __init__(self, tmp_path: Path):
        self.config = tmp_path / "config"
        self.project = tmp_path / "proj"
        self.project.mkdir()
        team_dir = self.config / "teams" / TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(json.dumps({
            "leadSessionId": LEAD_SID,
            "members": [
                {"name": "team-lead", "agentType": "pact-orchestrator"},
                {"name": MATE, "agentType": "pact-backend-coder"},
            ],
        }))
        (self.config / "tasks" / TEAM).mkdir(parents=True)
        self.write_lead_context()

    def session_dir(self, session_id: str) -> Path:
        return self.config / "pact-sessions" / self.project.resolve().name / session_id

    def write_lead_context(self) -> None:
        directory = self.session_dir(LEAD_SID)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "pact-session-context.json").write_text(json.dumps({
            "team_name": TEAM,
            "session_id": LEAD_SID,
            "project_dir": str(self.project),
            "plugin_root": "",
            "started_at": "2026-09-13T00:00:00Z",
        }))

    def add_task(self, task_id: int, owner: str, status: str = "in_progress", wait=None) -> None:
        task = {
            "id": str(task_id), "subject": "work", "status": status, "owner": owner,
            "blocks": [], "blockedBy": [], "metadata": {},
        }
        if wait is not None:
            task["metadata"]["intentional_wait"] = wait
        (self.config / "tasks" / TEAM / f"{task_id}.json").write_text(json.dumps(task))

    def add_record(self, agent_name: str, job_id: str, task_ids: list, minutes_ago: int = 5) -> None:
        path = self.config / "teams" / TEAM / "background_work.json"
        data = json.loads(path.read_text()) if path.exists() else {"records": []}
        data["records"].append({
            "agent_name": agent_name,
            "session_id": MATE_SID,
            "task_ids": task_ids,
            "registered_at": _iso(minutes_ago),
            "harness_task_id": job_id,
        })
        path.write_text(json.dumps(data))

    def register_teammate(self, session_id: str, name: str) -> None:
        path = self.config / "pact-sessions" / ".teammate-registry.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as registry:
            registry.write(json.dumps({"session_id": session_id, "value": f"{name}@{TEAM}"}) + "\n")

    def env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        env["CLAUDE_CONFIG_DIR"] = str(self.config)
        env["CLAUDE_PROJECT_DIR"] = str(self.project)
        return env

    def records(self) -> list:
        path = self.config / "teams" / TEAM / "background_work.json"
        return json.loads(path.read_text())["records"] if path.exists() else []

    def run(self, frame: dict, script: Path = HOOK) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(frame), capture_output=True, text=True,
            env=self.env(), timeout=60, check=False,
        )

    def events(self, session_id: str) -> list:
        journal = self.session_dir(session_id) / "session-journal.jsonl"
        if not journal.exists():
            return []
        return [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]

    def traces(self, session_id: str) -> list:
        return [e for e in self.events(session_id) if e.get("type") == "background_stop_gate"]

    def told(self, session_id: str):
        path = self.session_dir(session_id) / TOLD_FILENAME
        return json.loads(path.read_text())["ids"] if path.exists() else None


def output(proc: subprocess.CompletedProcess):
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def lead_launch_frame(job_id: str, **fields) -> dict:
    """The lead's own background Bash launch, as PostToolUse sees it: no agent_id."""
    frame = {
        "hook_event_name": "PostToolUse",
        "session_id": LEAD_SID,
        "agent_type": LEAD_TYPE,
        "transcript_path": "<transcript_path>",
        "cwd": "<cwd>",
        "tool_name": "Bash",
        "tool_input": {"command": "sleep 300", "description": "sleep", "run_in_background": True},
        "tool_response": {
            "backgroundTaskId": job_id, "interrupted": False, "isImage": False,
            "noOutputExpected": False, "stderr": "", "stdout": "",
        },
    }
    frame.update(fields)
    return frame


def lead_launches(world: World, *job_ids: str) -> None:
    """Record the lead's own launches through the production writer."""
    for job_id in job_ids:
        proc = world.run(lead_launch_frame(job_id), script=TRACK_FILES)
        assert proc.returncode == 0, proc.stderr
    lead_rows = [r.get("harness_task_id") for r in world.records() if r.get("owner_role") == "lead"]
    assert lead_rows == list(job_ids), world.records()


# --------------------------------------------------------------------------
# Lead
# --------------------------------------------------------------------------


def test_a_shell_the_lead_did_not_launch_never_blocks_the_lead(tmp_path):
    """The lead counts only the jobs recorded as its own launch.

    A teammate's shell whose launch went unrecorded sits in the lead's job
    list with no owner. It must not refuse the lead a turn end, alone or
    beside a job the lead did start.
    """
    world = World(tmp_path)
    unowned = stop_frame(jobs=[job("bmate1")], agent_type=LEAD_TYPE)

    assert output(world.run(unowned)) == SUPPRESS
    assert [(t["role"], t["verdict"]) for t in world.traces(LEAD_SID)] == [("lead", "allow_flagged")]
    assert world.told(LEAD_SID) is None

    lead_launches(world, "blead1")
    out = output(world.run(stop_frame(jobs=[job("bmate1"), job("blead1")], agent_type=LEAD_TYPE)))
    assert out == SUPPRESS
    assert world.traces(LEAD_SID)[-1]["ids"] == ["blead1"]


@pytest.mark.parametrize("fields", [
    {"agent_id": "ad2b1261fdd77c958"},], ids=["subagent-id",])
def test_only_the_leads_own_frame_writes_a_lead_row(tmp_path, fields):
    """A frame that is not positively the lead's, or names no job, writes no lead row."""
    world = World(tmp_path)
    proc = world.run(lead_launch_frame("bodd1", **fields), script=TRACK_FILES)

    assert proc.returncode == 0, proc.stderr
    assert [r for r in world.records() if r.get("owner_role") == "lead"] == []
    assert output(world.run(stop_frame(jobs=[job("bodd1")], agent_type=LEAD_TYPE))) == SUPPRESS


def test_a_lead_ending_its_turn_over_its_own_job_is_allowed_and_journaled(tmp_path):
    """REGRESSION (1.3.6 field report, section F): the lead was refused its turn end until it
    armed a CronCreate fallback, three extra tool calls per wait. Measured 2026-10-02 over the
    44 blocks in the session journals: all 27 finished jobs the lead had launched itself
    delivered their own notice. The job is its own wake; the stop is journaled with its id."""
    world = World(tmp_path)
    lead_launches(world, "bjob1")
    proc = world.run(stop_frame(jobs=[job("bjob1")], agent_type=LEAD_TYPE))

    assert output(proc) == SUPPRESS
    assert [(t["role"], t["verdict"], t["cause"], t["ids"]) for t in world.traces(LEAD_SID)] == [
        ("lead", "allow_flagged", "harness_tracked", ["bjob1"])
    ]
    assert "ended" not in world.traces(LEAD_SID)[0]
    assert world.told(LEAD_SID) is None
    assert "journal emit dropped" not in proc.stderr


def test_a_lead_job_the_frame_lists_as_ended_is_journaled_with_its_status(tmp_path):
    world = World(tmp_path)
    lead_launches(world, "bdone1", "bjob2")
    frame = stop_frame(
        jobs=[job("bdone1", status="completed"), job("bmate9", status="failed"), job("bjob2")],
        agent_type=LEAD_TYPE,
    )

    assert output(world.run(frame)) == SUPPRESS
    trace = world.traces(LEAD_SID)[0]
    assert (trace["ids"], trace["ended"], trace["running"]) == (["bjob2"], {"bdone1": "completed"}, 1)


@pytest.mark.parametrize("entries", [
    "not-a-list",], ids=["text",])
def test_a_malformed_job_list_never_blocks_the_lead_or_breaks_the_journal(tmp_path, entries):
    """The frame is the harness's, but the gate must end the turn whatever it holds."""
    world = World(tmp_path)
    lead_launches(world, "bjob1")
    running = [job("bjob1")] if isinstance(entries, list) else []
    frame = stop_frame(agent_type=LEAD_TYPE)
    frame["background_tasks"] = entries + running if isinstance(entries, list) else entries
    proc = world.run(frame)

    assert proc.returncode == 0 and output(proc) in (SUPPRESS, None), proc.stderr
    traces = world.traces(LEAD_SID)
    assert [t["verdict"] for t in traces] == (["allow_flagged"] if running else [])
    for trace in traces:
        assert all(len(v) <= 32 and "\n" not in v for v in trace.get("ended", {}).values())
    assert not [e for e in world.events(LEAD_SID) if e.get("type") == "journal_emit_skipped"]


def test_a_lead_call_the_harness_moved_to_the_background_is_counted(tmp_path):
    """A foreground lead call that times out is moved to the background by the harness: no
    run_in_background flag and no `&`, only a job id in tool_response. Main counted every
    running job at the lead's turn end, so it blocked; with lead rows only, this call wrote
    none and the job was never counted. The same frame from a teammate writes no row."""
    world = World(tmp_path)
    no_flag = {"command": "sleep 900", "description": "sleep"}
    proc = world.run(lead_launch_frame("bmate2", tool_input=no_flag, agent_type="pact-backend-coder"),
                     script=TRACK_FILES)
    assert proc.returncode == 0 and world.records() == [], proc.stderr

    assert world.run(lead_launch_frame("bmoved1", tool_input=no_flag), script=TRACK_FILES).returncode == 0
    assert [(r.get("owner_role"), r.get("harness_task_id")) for r in world.records()] == [("lead", "bmoved1")]
    assert output(world.run(stop_frame(jobs=[job("bmoved1")], agent_type=LEAD_TYPE))) == SUPPRESS
    assert [t["ids"] for t in world.traces(LEAD_SID)] == [["bmoved1"]]


def test_a_second_stop_is_not_blocked_for_the_same_job(tmp_path):
    """The livelock bound, behaviourally: one block per job id, then the stop lands."""
    world = World(tmp_path)
    world.register_teammate(MATE_SID, MATE)
    world.add_task(7, MATE)
    frame = stop_frame(session_id=MATE_SID, jobs=[job("bjob1")], agent_type="pact-backend-coder")

    assert output(world.run(frame))["decision"] == "block"
    assert output(world.run(frame)) == SUPPRESS

    # A job that was never reported still is: the bound is per job, not per session.
    third = output(world.run(stop_frame(
        session_id=MATE_SID, jobs=[job("bjob1"), job("bjob2")], agent_type="pact-backend-coder")))
    assert third["decision"] == "block"
    assert "`bjob2`" in third["reason"] and "`bjob1`" not in third["reason"]
    assert [t["verdict"] for t in world.traces(MATE_SID)] == [
        "block", "allow_already_told", "block"
    ]
    assert world.told(MATE_SID) == ["bjob1", "bjob2"]


def test_stop_hook_active_allows(tmp_path):
    world = World(tmp_path)
    lead_launches(world, "bjob1")
    proc = world.run(stop_frame(jobs=[job("bjob1")], agent_type=LEAD_TYPE, stop_hook_active=True))

    assert output(proc) == SUPPRESS
    assert [t["verdict"] for t in world.traces(LEAD_SID)] == ["allow_loop_guard"]
    assert world.told(LEAD_SID) is None


def test_a_scheduled_cron_allows(tmp_path):
    world = World(tmp_path)
    crons = [{"id": "c1", "cron": "*/5 * * * *", "prompt": "check the job"}]
    proc = world.run(stop_frame(jobs=[job("bjob1")], agent_type=LEAD_TYPE, session_crons=crons))

    assert output(proc) == SUPPRESS
    assert [(t["verdict"], t.get("cause")) for t in world.traces(LEAD_SID)] == [
        ("allow_flagged", "session_cron")
    ]


def test_a_job_recorded_by_a_teammate_does_not_block_the_lead(tmp_path):
    world = World(tmp_path)
    world.add_task(7, MATE)
    world.add_record(MATE, "bjob1", ["7"])
    proc = world.run(stop_frame(jobs=[job("bjob1")], agent_type=LEAD_TYPE))

    assert output(proc) == SUPPRESS
    assert [(t["role"], t["verdict"]) for t in world.traces(LEAD_SID)] == [("lead", "allow_flagged")]


def test_a_stop_from_the_lead_session_is_the_lead_whatever_its_spelling(tmp_path):
    world = World(tmp_path)
    lead_launches(world, "bjob1")
    proc = world.run(stop_frame(jobs=[job("bjob1")], agent_type="some-other-orchestrator"))

    assert output(proc) == SUPPRESS
    assert [(t["role"], t["ids"]) for t in world.traces(LEAD_SID)] == [("lead", ["bjob1"])]


# --------------------------------------------------------------------------
# Frames that must never block
# --------------------------------------------------------------------------


def test_a_plain_session_frame_never_blocks(tmp_path):
    """The captured plain Stop shape: no agent_type, no agent_id, no context."""
    world = World(tmp_path)
    proc = world.run(stop_frame(session_id=PLAIN_SID, jobs=[job("bjob1")]))

    assert output(proc) == SUPPRESS
    assert not world.session_dir(PLAIN_SID).exists(), (
        "an unidentified session must not get a session folder created for it"
    )


def test_an_agent_id_that_is_not_a_member_at_this_team_is_unresolved(tmp_path):
    world = World(tmp_path)
    frame = stop_frame(jobs=[job("bjob1")], agent_type="general-purpose", agent_id="ab997f00c7cd48288")
    proc = world.run(frame)

    assert output(proc) == SUPPRESS
    assert [t["verdict"] for t in world.traces(LEAD_SID)] == ["allow_role_unresolved"]


# --------------------------------------------------------------------------
# Separate-process teammate: real type, no agent_id, its own session, a
# session-registry entry, and NO session context file.
# --------------------------------------------------------------------------


def teammate_frame(jobs) -> dict:
    return stop_frame(session_id=MATE_SID, jobs=jobs, agent_type="pact-backend-coder")


def _lead_session_in_the_registry(world: World) -> None:
    """The lead's session with no context file and a registry entry naming a member."""
    (world.session_dir(LEAD_SID) / "pact-session-context.json").unlink()
    world.register_teammate(LEAD_SID, MATE)


# --------------------------------------------------------------------------
# The join with records the production Layer 1 writer produces. The records
# above are written by hand; these are written by track_files.py from a real
# PostToolUse launch frame, so the harness_task_id they carry is the writer's.
# --------------------------------------------------------------------------


def launch_frame(job_id: str) -> dict:
    """A separate-process teammate's background Bash launch, as PostToolUse sees it."""
    return {
        "hook_event_name": "PostToolUse",
        "session_id": MATE_SID,
        "agent_type": "pact-backend-coder",
        "transcript_path": "<transcript_path>",
        "cwd": "<cwd>",
        "tool_name": "Bash",
        "tool_input": {"command": "sleep 300", "description": "sleep", "run_in_background": True},
        "tool_response": {
            "backgroundTaskId": job_id, "interrupted": False, "isImage": False,
            "noOutputExpected": False, "stderr": "", "stdout": "",
        },
    }


def launch_through_layer_1(world: World, job_id: str) -> None:
    proc = world.run(launch_frame(job_id), script=TRACK_FILES)
    assert proc.returncode == 0, proc.stderr
    # Positive control: the writer ran and recorded this job's id.
    assert [r.get("harness_task_id") for r in world.records()] == [job_id], world.records()


def subagent_shell_launch_frame(job_id: str) -> dict:
    """A background shell launched INSIDE an Agent-tool subagent.

    The frame fires in the LEAD's process and carries the subagent's own
    `agent_id` ("a" + 16 hex), which is what separates it from the lead's own
    frame (no agent_id) and from an in-process teammate's (whose agent_type
    carries a member name).
    """
    return {
        "hook_event_name": "PostToolUse",
        "session_id": LEAD_SID,
        "agent_type": "general-purpose",
        "agent_id": "ad2b1261fdd77c958",
        "transcript_path": "<transcript_path>",
        "cwd": "<cwd>",
        "tool_name": "Bash",
        "tool_input": {
            "command": "sleep 300", "description": "sleep", "run_in_background": True,
        },
        "tool_response": {
            "backgroundTaskId": job_id, "interrupted": False, "isImage": False,
            "noOutputExpected": False, "stderr": "", "stdout": "",
        },
    }


# --------------------------------------------------------------------------
# Which entries count as jobs, per role. background_tasks lists live
# teammates and subagents as running entries beside real work.
# --------------------------------------------------------------------------


def live_teammates(count: int = 14) -> list:
    return [job(f"bmate{i}", type="teammate", description=f"member {i}") for i in range(count)]


# A fresh lead's first Stop frame from the live pre-fix baseline, refused over
# exactly these two entries (fields as captured).
LIVE_BASELINE_ENTRIES = [
    {"id": "tbbgxyxjp", "status": "running", "type": "teammate"},
    {"id": "tztw4cwv5", "status": "running", "type": "teammate"},
]


# --------------------------------------------------------------------------
# Trace
# --------------------------------------------------------------------------


_MATE_FIELDS = {"session_id": MATE_SID, "agent_type": "pact-backend-coder"}


def _prepare_mate(world: World) -> None:
    world.register_teammate(MATE_SID, MATE)
    world.add_task(7, MATE)


def _prepare_told(world: World, as_directory: bool = False) -> None:
    _prepare_mate(world)
    path = world.session_dir(MATE_SID) / TOLD_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if as_directory:
        path.mkdir()
    else:
        path.write_text(json.dumps({"ids": ["bjob1"]}))


# The lead is never refused, so the block and the told-once verdicts are a teammate's.
_VERDICT_CASES = {
    "block": (_prepare_mate, _MATE_FIELDS),
    "allow_flagged": (lambda w: None, {"agent_type": LEAD_TYPE, "session_crons": [{"id": "c"}]}),
    "allow_already_told": (_prepare_told, _MATE_FIELDS),
    "allow_loop_guard": (lambda w: None, {"agent_type": LEAD_TYPE, "stop_hook_active": True}),
    "allow_role_unresolved": (
        lambda w: None, {"agent_type": "general-purpose", "agent_id": "ab997f00c7cd48288"}
    ),
    # A directory where the told-once file belongs cannot be read as a file.
    "allow_error": (lambda w: _prepare_told(w, as_directory=True), _MATE_FIELDS),
    # A monitor passes the fast path's union but is not a job the lead counts.
    "allow_no_job": (
        lambda w: None,
        {"agent_type": LEAD_TYPE, "background_tasks": [job("bjob1", type="monitor")]},
    ),
}


# --------------------------------------------------------------------------
# Cost path and registration
# --------------------------------------------------------------------------

_MODULE_PROBE = (
    "import json, runpy, sys\n"
    "script = sys.argv[1]\n"
    "sys.argv = [script]\n"
    "runpy.run_path(script, run_name='__main__')\n"
    "loaded = sorted(m for m in sys.modules if m == 'shared' or m.startswith('shared.'))\n"
    "sys.stderr.write('\\nLOADED=' + json.dumps(loaded) + '\\n')\n"
)


def probe_env(world: World) -> dict:
    """The world's environment with the hooks directory importable, as running
    a hook script makes it: runpy.run_path does not add the script's folder."""
    env = world.env()
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(HOOKS_DIR), env.get("PYTHONPATH", "")) if p
    )
    return env


def _loaded_plugin_modules(world: World, frame: dict) -> "tuple[str, list]":
    proc = subprocess.run(
        [sys.executable, "-c", _MODULE_PROBE, str(HOOK)],
        input=json.dumps(frame), capture_output=True, text=True,
        env=probe_env(world), timeout=60, check=False,
    )
    marker = [line for line in proc.stderr.splitlines() if line.startswith("LOADED=")]
    assert marker, f"the probe did not report its modules: {proc.stderr}"
    return proc.stdout, json.loads(marker[-1][len("LOADED="):])


