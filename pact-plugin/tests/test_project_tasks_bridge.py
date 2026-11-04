"""
D10 P2 BRIDGE (the design notes §5; test plan §12.1 item 7, the §12.2
bridge rows; rulings amend_1, amend_2 and amend_4 on #180).

The lead's acceptance of a platform Task B the lead bound to a project task (start --ptask, or
project_task/refs in the lead's own write; 1.5.0 F2) closes it on verified evidence; a rejection records the revision and closes nothing; nothing a teammate does
closes anything. Since 2.1.0 the bridge runs inside the lead's own `repact accept`, `reject`
and `dispatch` (shared/ops/sides.task_log), which only the lead can call; `run_hook` drives that
call as a real subprocess, from a payload that names the task and the act (a completion, a
handoff_rejection, a project_task/refs link).

Everything runs in temp dirs: a temp git repo, a temp CLAUDE_CONFIG_DIR (task files, team config,
session dirs), a temp CLAUDE_PLUGIN_DATA (run ledger, logs) and a temp KB profile.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from project_tasks_support import (GIT_ENV, PLUGIN_ROOT, git, kb_profile, load, new_repo, reader,
                                   run_cli, writer)

SESSION_LEDGER = PLUGIN_ROOT / "hooks" / "session_ledger.py"
BRIDGE_PATH = PLUGIN_ROOT / "hooks" / "shared" / "task_log" / "bridge.py"
EVIDENCE_PATH = PLUGIN_ROOT / "hooks" / "shared" / "task_log" / "evidence.py"
TEAM = "session-bridge"
SID = "5e551011-0000-4000-8000-000000000001"
TASK_B = "7"
LEAD_TYPE = load("plugin_identity_under_test",
                 PLUGIN_ROOT / "hooks" / "shared" / "plugin_identity.py").NAMESPACE + "pact-orchestrator"
JOURNAL_TYPE = "project_task_acceptance"


# ---------------------------------------------------------------------------------------------
# the world: a repo with a task log, a team, a session dir and a platform Task B
# ---------------------------------------------------------------------------------------------

class World:
    def __init__(self, tmp_path: Path, monkeypatch):
        kb_profile(tmp_path, monkeypatch)
        self.home = tmp_path / "claude"
        self.data = tmp_path / "plugin-data"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(self.home))
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(self.data))
        monkeypatch.setenv("REPACT_SCRUB_DENYLIST", str(tmp_path / "no-denylist.txt"))
        monkeypatch.setenv("REPACT_SCRUB_DENYLIST_WORDS", str(tmp_path / "no-denylist-words.txt"))
        monkeypatch.setenv("TMPDIR", str(tmp_path / "tmpdir"))
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmpdir"))   # gettempdir() caches
        (tmp_path / "tmpdir").mkdir()
        self.repo = new_repo(tmp_path / "repo")
        (self.repo / "impl.py").write_text("x = 1\n")
        git(self.repo, "add", "impl.py")
        git(self.repo, "commit", "-q", "-m", "impl")
        self.head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.W, self.R = writer(), reader()
        self.store = self.W.init(self.repo)
        self.ref = self.store.add("Build the acceptance bridge")
        self.session_dir = self.home / "pact-sessions" / self.repo.name / SID
        self.session_dir.mkdir(parents=True)
        (self.session_dir / "pact-session-context.json").write_text(
            json.dumps({"team_name": TEAM, "session_id": SID, "project_dir": str(self.repo)}))
        (self.home / "teams" / TEAM).mkdir(parents=True)
        (self.home / "teams" / TEAM / "config.json").write_text(json.dumps({
            "name": TEAM, "leadSessionId": SID,
            "members": [{"name": "team-lead", "agentType": LEAD_TYPE},
                        {"name": "coder", "agentType": "pact-backend-coder"},
                        {"name": "secretary", "agentType": "pact-secretary"}]}))
        (self.home / "tasks" / TEAM).mkdir(parents=True)

    # -- platform task files -----------------------------------------------------------------------

    def task(self, task_id=TASK_B, owner="coder", status="completed", archive=False, **metadata):
        body = {"id": task_id, "subject": f"coder: build #{task_id}", "description": "", "status": status,
                "owner": owner, "blocks": [], "blockedBy": [], "metadata": metadata}
        where = (self.session_dir / "tasks-archive") if archive else (self.home / "tasks" / TEAM)
        where.mkdir(parents=True, exist_ok=True)
        (where / f"{task_id}.json").write_text(json.dumps(body))
        return body

    def drop_task_file(self, task_id=TASK_B):
        (self.home / "tasks" / TEAM / f"{task_id}.json").unlink()

    # -- the run ledger ------------------------------------------------------------------------------

    def plant_run(self, name="2026-09-27T200000Z-1-pytest.log", passed=12, failed=0, task_id=TASK_B,
                  log_text=None, ledger_counts=None, runs=None):
        runs = runs or (self.data / "runs")
        runs.mkdir(parents=True, exist_ok=True)
        log = runs / name
        log.write_text(log_text if log_text is not None else
                       f"============ {passed} passed, {failed} failed in 0.50s ============\n")
        counts = ledger_counts or {"collected": passed + failed, "passed": passed, "failed": failed,
                                   "errors": 0, "skipped": 0}
        row = {"v": 1, "ts": "2026-09-27T20:00:00+00:00", "team": TEAM, "task_id": task_id,
               "runner": "pytest", "count_parsed": True, "log_path": str(log), "git_head": self.head,
               "git_branch": "main", "exit_code": 1 if failed else 0, "source": "hook", **counts}
        with open(runs / "ledger.jsonl", "a") as handle:
            handle.write(json.dumps(row) + "\n")
        return name

    # -- reading back ----------------------------------------------------------------------------------

    def state(self):
        return self.store.state()

    def project_task(self):
        return self.state().tasks[self.ref]

    def log_lines(self):
        return (self.repo / ".repacted" / "tasks.jsonl").read_text().splitlines()

    def journal(self):
        path = self.session_dir / "session-journal.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()
                if json.loads(line).get("type") == JOURNAL_TYPE]


@pytest.fixture()
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


# ---------------------------------------------------------------------------------------------
# payloads, keyed on the 2.1.283 capture
# ---------------------------------------------------------------------------------------------

def frame(world, agent_type=LEAD_TYPE, agent_id=None, task_id=TASK_B, status=None, metadata=None,
          status_change=True):
    tool_input = {"taskId": task_id}
    if status:
        tool_input["status"] = status
    if metadata is not None:
        tool_input["metadata"] = metadata
    response = {"success": True, "taskId": task_id, "updatedFields": sorted(tool_input)}
    if status and status_change:
        response["statusChange"] = {"from": "in_progress", "to": status}
    payload = {"cwd": str(world.repo), "duration_ms": 12, "effort": {"level": "high"},
               "hook_event_name": "PostToolUse", "permission_mode": "bypassPermissions",
               "prompt_id": "p-1", "scratchpad_dir": "/tmp/s", "session_id": SID,
               "tool_input": tool_input, "tool_name": "TaskUpdate", "tool_response": response,
               "tool_use_id": "toolu_1", "transcript_path": f"/tmp/{SID}.jsonl"}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    if agent_id is not None:
        payload["agent_id"] = agent_id
    return payload


def bind_ref(world, ref=None):
    """The lead's dispatch bind (`repact-task start <ref> --ptask`): since 1.5.0 F2 only a bound
    ref closes or records a rejection."""
    world.store.start(ref or world.ref, ptask=f"{TEAM}/{TASK_B}")


def lead_accepts(world, **kwargs):
    return frame(world, status="completed", **kwargs)


# The lead's op through the bridge, as shared/ops/sides.task_log runs it: the act's own metadata
# is `written`, the task file is the task, and a store error stays inside the op (fail open).
_DRIVER = r"""
import json, os, sys
from shared import ref_dispositions as RD
from shared.task_log import bridge, write as W
payload, team = json.load(sys.stdin), sys.argv[1]
tool_input = payload.get("tool_input") or {}
written = tool_input.get("metadata") if isinstance(tool_input.get("metadata"), dict) else {}
if payload.get("tool_name") == "TaskCreate":
    task_id = str(payload["tool_response"]["task"]["id"])
else:
    task_id = str(tool_input.get("taskId"))
actions = ((["link"] if RD.metadata_refs(written) else [])
           + (["reject"] if written.get("handoff_rejection") else [])
           + (["accept"] if tool_input.get("status") == "completed" else []))
path = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "tasks", team, task_id + ".json")
line = None
try:
    store = W.Store.locate(payload["cwd"], by=W.with_team("bridge", payload.get("session_id")))
    with open(path, encoding="utf-8") as handle:
        task = json.load(handle)
    bridge.apply(store, team, task_id, task, written, actions, payload.get("session_id"))
    line = bridge.acceptance_line(bridge.last_report)
except W.NoLog:
    sys.exit(0)
except Exception:
    pass
print(json.dumps({"hookSpecificOutput": {"additionalContext": line}} if line else {"suppressOutput": True}))
"""


def run_hook(payload) -> subprocess.CompletedProcess:
    env = {**os.environ, **GIT_ENV, "PYTHONPATH": str(PLUGIN_ROOT / "hooks")}
    return subprocess.run([sys.executable, "-c", _DRIVER, TEAM],
                          input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60)


def bridge():
    return load("project_tasks_bridge_under_test", BRIDGE_PATH)


def evidence_module():
    return load("project_tasks_evidence_under_test", EVIDENCE_PATH)


# ---------------------------------------------------------------------------------------------
# §12.1 item 7: the lead's acceptance, through the real hook
# ---------------------------------------------------------------------------------------------

class TestAcceptance:
    def test_a_secretary_owned_task_never_closes_or_links_a_project_task(self, world):
        # The deleted TaskUpdate adapter skipped a teachback-exempt owner; apply() keeps that rule.
        bind_ref(world)
        world.task(owner="secretary", project_task=world.ref, handoff={"evidence": {"head": world.head}})
        before = world.log_lines()
        result = run_hook(lead_accepts(world, metadata={"project_task": world.ref}))
        assert result.returncode == 0
        assert world.log_lines() == before and world.project_task()["status"] != "done"

    def test_a_handoff_evidence_object_closes_the_task_on_a_verified_head(self, world):
        bind_ref(world)
        world.task(project_task=world.ref, handoff={"produced": ["x"], "evidence": {"head": world.head}})
        result = run_hook(lead_accepts(world))
        assert result.returncode == 0
        # 1.5.0: the lead hears what the acceptance closed in the task log (one line, nothing else)
        assert json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"] == \
            f"Project tasks on #{TASK_B}: closed {world.ref}."
        task = world.project_task()
        assert task["status"] == "done"
        assert task["accepts"] == [{"ptask": f"{TEAM}/{TASK_B}", "rev": 1, "verified": True,
                                    "ts": task["accepts"][0]["ts"]}]
        assert {"target": f"commit:{world.head}", "verified": True} in task["evidence"]


class TestRejection:
    def test_a_rejection_records_the_revision_and_closes_nothing(self, world):
        bind_ref(world)
        world.task(status="in_progress", project_task=world.ref,
                   handoff={"evidence": {"head": world.head}},
                   handoff_rejection={"reason": "r", "corrections": [], "since": "s", "revision_number": 1})
        run_hook(frame(world, metadata={"handoff_rejection": {"reason": "r", "revision_number": 1}}))
        task = world.project_task()
        assert task["status"] != "done"
        assert [(r["ptask"], r["rev"]) for r in task["rejects"]] == [(f"{TEAM}/{TASK_B}", 1)]
        assert task["accepts"] == []

    def test_a_teachback_rejection_is_ignored(self, world):
        world.task(status="in_progress", project_task=world.ref)
        before = world.log_lines()
        run_hook(frame(world, metadata={"teachback_rejection": {"reason": "r", "revision_number": 1}}))
        assert world.log_lines() == before


class TestDispatchLink:
    def test_setting_project_task_links_the_task_and_marks_it_active(self, world):
        world.task(status="pending", project_task=world.ref)
        run_hook(frame(world, metadata={"project_task": world.ref}))
        task = world.project_task()
        assert task["status"] == "active"
        assert {"kind": "reference", "target": f"ptask:{TEAM}/{TASK_B}"} in task["links"]

    def test_the_link_is_read_from_the_update_when_the_task_file_lags(self, world):
        """amend_5 S2: the task file can persist after the hook runs, so project_task is read from
        tool_input.metadata merged over the file."""
        world.task(status="pending")                         # the file does not have the key yet
        run_hook(frame(world, metadata={"project_task": world.ref}))
        task = world.project_task()
        assert task["status"] == "active"
        assert {"kind": "reference", "target": f"ptask:{TEAM}/{TASK_B}"} in task["links"]


# ---------------------------------------------------------------------------------------------
# the lead-acceptance journal record: written BEFORE the store
# ---------------------------------------------------------------------------------------------

class TestAttributionRecord:
    def test_the_lead_acceptance_is_journalled_with_its_ptask_op_and_rev(self, world):
        bind_ref(world)
        world.task(project_task=world.ref, handoff={"evidence": {"head": world.head}})
        run_hook(lead_accepts(world))
        events = world.journal()
        assert [(e["ptask"], e["op"], e["rev"], e["t"]) for e in events] == [
            (f"{TEAM}/{TASK_B}", "accept", 1, world.ref)]
        assert Path(events[0]["main"]).resolve() == world.repo.resolve()

    def test_the_record_survives_a_store_write_that_fails(self, world):
        bind_ref(world)
        world.task(project_task=world.ref, handoff={"evidence": {"head": world.head}})
        log = world.repo / ".repacted" / "tasks.jsonl"
        log.chmod(0o400)
        try:
            result = run_hook(lead_accepts(world))
        finally:
            log.chmod(0o600)
        assert result.returncode == 0
        assert [e["op"] for e in world.journal()] == ["accept"]
        assert world.project_task()["accepts"] == []


# ---------------------------------------------------------------------------------------------
# sync (§5.4): only lead-attributed completions are written
# ---------------------------------------------------------------------------------------------

def _journal_accept(world, rev=1):
    sj = load("session_journal_under_test", PLUGIN_ROOT / "hooks" / "shared" / "session_journal.py")
    event = sj.make_event(JOURNAL_TYPE, ptask=f"{TEAM}/{TASK_B}", op="accept", rev=rev, t=world.ref,
                          main=str(world.repo.resolve()))
    assert sj.append_event(event, session_dir=str(world.session_dir))


class TestSync:
    def test_sync_writes_a_missed_lead_acceptance_from_the_task_file(self, world):
        world.task(project_task=world.ref, handoff={"evidence": {"head": world.head}})
        _journal_accept(world)
        report = bridge().sync(world.repo)
        assert report["written"] == [f"accept {TEAM}/{TASK_B} rev 1"]
        assert world.project_task()["status"] == "done"

    def test_sync_recovers_from_the_d6_archive_when_the_task_file_is_gone(self, world):
        world.task(project_task=world.ref, handoff={"evidence": {"head": world.head}}, archive=True)
        _journal_accept(world)
        bridge().sync(world.repo)
        assert world.project_task()["status"] == "done"


# ---------------------------------------------------------------------------------------------
# the run: verifier (evidence.verify, bare log name matched to the ledger by basename)
# ---------------------------------------------------------------------------------------------

class TestRunVerifier:
    def test_a_logged_run_whose_counts_agree_verifies(self, world):
        name = world.plant_run(passed=3, failed=1)
        assert evidence_module().verify(world.repo, f"run:{name}") == (True, "")

    def test_counts_that_disagree_do_not_verify(self, world):
        name = world.plant_run(passed=3, failed=1,
                               ledger_counts={"collected": 4, "passed": 4, "failed": 0, "errors": 0,
                                              "skipped": 0})
        verified, reason = evidence_module().verify(world.repo, f"run:{name}")
        assert not verified and "counts" in reason


# ---------------------------------------------------------------------------------------------
# the reader's ptask lookup
# ---------------------------------------------------------------------------------------------

class TestTaskForPtask:
    def test_the_linked_task_is_found(self, world):
        world.store.start(world.ref, ptask=f"{TEAM}/{TASK_B}")
        assert world.R.task_for_ptask(world.state(), TEAM, TASK_B)["ref"] == world.ref

    def test_an_unlinked_ptask_finds_nothing(self, world):
        assert world.R.task_for_ptask(world.state(), TEAM, TASK_B) is None


# ---------------------------------------------------------------------------------------------
# fail open; the SessionEnd child syncs
# ---------------------------------------------------------------------------------------------

class TestFailOpen:
    def test_a_repo_without_a_task_log_is_a_quiet_no_op(self, world):
        (world.repo / ".repacted" / "tasks.jsonl").unlink()
        world.task(project_task=world.ref)
        result = run_hook(lead_accepts(world))
        assert result.returncode == 0 and result.stdout == ""


