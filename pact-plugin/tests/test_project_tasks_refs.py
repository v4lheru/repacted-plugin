"""
The 1.5.0 task-system review (task #135): the drift the 1.4.1 session showed, closed by mechanism.

  1. A team task carries SEVERAL project tasks; the acceptance closes the ones the HANDOFF says
     are done and reports the rest (hooks/shared/task_log/bridge.py to_close, acceptance_line).
  2. A HANDOFF on a task that carries project tasks is returned until handoff.refs disposes of
     each (hooks/amendment_gate.py, rules in hooks/shared/ref_dispositions.py); a ref the task
     only names in its text is a note.
  3. `next` never recommends an open task whose platform task is completed; `fsck` and `show`
     flag it (bridge.completion_view).
  4. A verified run records the log's digest and counts, so fsck never re-parses a log and a
     rotated log is not rot (hooks/shared/task_log/evidence.py); a wrapper log the runner-hinted parser
     refuses (T-1f03553e) verifies under the generic parser.
  5. `done --remaining "<title>"` is the split shape for a partly done item.
Boundaries: a corrupt partial line, a missing team task file, a ref carried twice, a unicode
title, two writers at once. Everything runs in temp dirs through the World of the bridge tests.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from project_tasks_support import GIT_ENV, PLUGIN_ROOT, load, run_cli
from test_project_tasks_bridge import SID, TASK_B, TEAM, World, lead_accepts, run_hook

GATE = PLUGIN_ROOT / "hooks" / "amendment_gate.py"
RULES = PLUGIN_ROOT / "hooks" / "shared" / "ref_dispositions.py"
PTASK = f"{TEAM}/{TASK_B}"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def cli(world, *args):
    """repact-task in the world's repo, with the world's plugin data (the run ledger)."""
    return run_cli(world.repo, *args, env={"CLAUDE_PLUGIN_DATA": str(world.data)})


def gate_write(world, handoff, task_id=TASK_B, cwd=None):
    """A teammate's HANDOFF write through the real PreToolUse gate."""
    payload = {"tool_name": "TaskUpdate", "session_id": SID, "cwd": str(world.repo) if cwd is None else cwd,
               "agent_type": "coder", "agent_id": "a0123456789abcdef",
               "tool_input": {"taskId": task_id, "metadata": {"handoff": handoff}}}
    return subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload), capture_output=True,
                          text=True, env={**os.environ, **GIT_ENV}, timeout=60)


def denied(result) -> bool:
    out = json.loads(result.stdout) if result.stdout.strip() else {}
    return result.returncode == 2 and out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def note_of(result) -> str:
    out = json.loads(result.stdout) if result.stdout.strip() else {}
    return out.get("hookSpecificOutput", {}).get("additionalContext") or ""


HANDOFF = {"rev": 1, "amendments": {}, "produced": ["x"], "decisions": [], "uncertainty": [],
           "integration": [], "open_questions": []}


# ---------------------------------------------------------------------------------------------
# 1. several project tasks on one team task
# ---------------------------------------------------------------------------------------------

class TestMultiRefAcceptance:
    def two_refs(self, world):
        second = world.store.add("Second part: the owner's config")
        for ref in (world.ref, second):
            world.store.start(ref, ptask=PTASK)
        return world.ref, second

    def test_the_acceptance_closes_the_done_refs_and_reports_the_rest(self, world):
        first, second = self.two_refs(world)
        world.task(handoff={**HANDOFF, "evidence": {"head": world.head},
                            "refs": {first: "done: the fix", second: "partial: the owner part is tracked elsewhere"}})
        result = run_hook(lead_accepts(world))
        state = world.state()
        assert state.tasks[first]["status"] == "done"
        assert state.tasks[second]["status"] == "active"
        line = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert line.startswith(f"Project tasks on #{TASK_B}: closed {first}; still open: {second} "
                               "(partial: the owner part is tracked elsewhere).")
        assert "--remaining" in line

    def test_several_refs_and_no_dispositions_close_nothing_and_say_so(self, world):
        """The silent drop of the 1.4.1 session: the old bridge saw two refs on one ptask and
        wrote nothing, silently. Now it writes nothing and tells the lead which refs wait."""
        first, second = self.two_refs(world)
        world.task(handoff={**HANDOFF, "evidence": {"head": world.head}})
        result = run_hook(lead_accepts(world))
        state = world.state()
        assert state.tasks[first]["status"] == "active" and state.tasks[second]["status"] == "active"
        line = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "closed" not in line and "still open: " in line          # refs are listed by ref, so either order
        assert f"{first} (no disposition in handoff.refs)" in line and f"{second} (no disposition in handoff.refs)" in line

    def test_a_ref_carried_twice_is_accepted_once(self, world):
        world.store.start(world.ref, ptask=PTASK)
        world.task(project_task=world.ref, refs=[world.ref, world.ref],
                   handoff={**HANDOFF, "evidence": {"head": world.head}, "refs": {world.ref: "done"}})
        run_hook(lead_accepts(world))
        task = world.project_task()
        assert task["status"] == "done" and len(task["accepts"]) == 1

    def test_fsck_and_show_flag_an_open_ref_whose_platform_task_is_completed(self, world):
        first, second = self.two_refs(world)
        world.task(handoff={**HANDOFF, "evidence": {"head": world.head},
                            "refs": {first: "done: the fix", second: "deferred: next release"}})
        run_hook(lead_accepts(world))
        fsck = cli(world, "fsck")
        assert fsck.returncode == 0
        assert f"{second}: open, but its platform task is completed: #{TASK_B} (deferred: next release)" in fsck.stdout
        assert first not in fsck.stdout
        show = cli(world, "show", second)
        assert f"link reference: ptask:{PTASK} (completed)" in show.stdout
        assert "flag: open, but its platform task is completed" in show.stdout


# ---------------------------------------------------------------------------------------------
# 2. the HANDOFF gate on carried refs
# ---------------------------------------------------------------------------------------------

class TestRefGate:
    def test_a_handoff_without_dispositions_for_a_carried_ref_is_returned(self, world):
        world.store.start(world.ref, ptask=PTASK)
        world.task(status="in_progress")
        result = gate_write(world, HANDOFF)
        assert denied(result)
        reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        assert f"{world.ref}: Build the acceptance bridge" in reason
        assert "handoff.refs" in reason and "NOT STORED" in reason

    def test_a_handoff_that_disposes_of_each_carried_ref_passes(self, world):
        world.store.start(world.ref, ptask=PTASK)
        world.task(status="in_progress")
        result = gate_write(world, {**HANDOFF, "refs": {world.ref: "done: commit abc1234"}})
        assert result.returncode == 0 and not denied(result)

    def test_a_malformed_disposition_is_named_and_returned(self, world):
        world.store.start(world.ref, ptask=PTASK)
        world.task(status="in_progress")
        result = gate_write(world, {**HANDOFF, "refs": {world.ref: "finished", "T-zz": "done"}})
        assert denied(result)
        reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        assert "not a disposition" in reason and world.ref in reason

    def test_a_ref_only_named_in_the_task_text_is_a_note_not_a_return(self, world):
        body = world.task(status="in_progress")
        body["description"] = f"Your scope: {world.ref} and the plan section."
        (world.home / "tasks" / TEAM / f"{TASK_B}.json").write_text(json.dumps(body))
        result = gate_write(world, HANDOFF)
        assert result.returncode == 0 and not denied(result)
        assert world.ref in note_of(result) and "not disposed of in handoff.refs" in note_of(result)

    def test_metadata_refs_count_even_without_a_task_log(self, world, tmp_path):
        """The machine-readable carrier is the metadata too: a lead that writes refs at dispatch
        gets the gate even where the repo keeps no log."""
        plain = tmp_path / "plain"
        plain.mkdir()
        world.task(status="in_progress", refs=["T-0123abcd"])
        result = gate_write(world, HANDOFF, cwd=str(plain))
        assert denied(result) and "T-0123abcd" in result.stdout

    def test_a_missing_team_task_file_allows_the_write(self, world):
        world.store.start(world.ref, ptask=PTASK)
        result = gate_write(world, HANDOFF)
        assert result.returncode == 0 and not denied(result)

    def test_the_shipped_mode_is_enforce_and_an_absent_key_reads_as_enforce(self):
        gate = load("amendment_gate_under_test", GATE)
        constants = json.loads((PLUGIN_ROOT / "bin" / "checks.json").read_text())
        assert constants["modes"]["ref_dispositions"] == "enforce"

        class Ev:
            @staticmethod
            def load_constants():
                return {"modes": {}}
        assert gate.ref_mode(Ev) == "enforce"

    def test_the_disposition_grammar(self):
        rules = load("ref_dispositions_under_test", RULES)
        assert rules.verb("done") == "done" and rules.verb("done: commit abc") == "done"
        assert rules.verb("partial: the owner part") == "partial"
        assert rules.verb("partial") is None and rules.verb("partial: ") is None
        assert rules.verb("finished: yes") is None and rules.verb(7) is None and rules.verb(None) is None
        assert rules.dispositions({"refs": {"T-0123abcd": "dropped: superseded", "x": "done", "T-0123abce": 1}}) \
            == {"T-0123abcd": "dropped: superseded"}
        assert rules.metadata_refs({"project_task": "T-0123abcd", "refs": ["T-0123abcd", "T-ffffffff", "bad", None]}) \
            == ["T-0123abcd", "T-ffffffff"]
        assert rules.mentioned_refs({"subject": "u5: fix", "description": "Refs: T-e7fc3577, T-5a77cb08;"
                                     " see T-e7fc3577 again"}) == ["T-e7fc3577", "T-5a77cb08"]


# ---------------------------------------------------------------------------------------------
# 3. next never recommends finished work
# ---------------------------------------------------------------------------------------------

class TestNext:
    def test_a_planned_task_whose_platform_task_is_completed_is_listed_apart_not_recommended(self, world):
        other = world.store.add("Ünïcode title · still planned ✓")
        world.store.link(other, "reference", f"ptask:{PTASK}")       # linked, never started
        world.task(status="completed", handoff={**HANDOFF, "refs": {other: "deferred: not this release"}})
        result = cli(world, "next")
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        recommended = [line for line in lines if line.startswith("T-") and not line.startswith("  ")]
        assert [line.split()[0] for line in recommended] == [world.ref]
        assert any(line.startswith("1 open with a completed platform task (not recommended)") for line in lines)
        tail = [line for line in lines if line.startswith(f"  {other}")]
        assert tail and "Ünïcode title · still planned ✓" in tail[0] and f"#{TASK_B} (deferred: not this release)" in tail[0]

    def test_a_task_named_by_a_completed_platform_task_is_marked(self, world):
        body = world.task(status="completed")
        body["description"] = f"Refs: {world.ref}"
        (world.home / "tasks" / TEAM / f"{TASK_B}.json").write_text(json.dumps(body))
        result = cli(world, "next")
        assert f"{world.ref}  planned" in result.stdout and f"[named by completed #{TASK_B}]" in result.stdout

    def test_a_corrupt_partial_line_and_a_missing_task_file_do_not_stop_next_or_fsck(self, world):
        world.store.link(world.ref, "reference", f"ptask:{PTASK}")
        world.task(status="completed")
        log = world.repo / ".repacted" / "tasks.jsonl"
        with log.open("ab") as handle:
            handle.write(b'{"v":1,"eid":"20261003T22')          # a writer died mid-line
        result = cli(world, "next")
        assert result.returncode == 0 and "1 open with a completed platform task" in result.stdout
        fsck = cli(world, "fsck")
        assert "corrupt (not JSON)" in fsck.stdout and f"{world.ref}: open, but its platform task is completed" in fsck.stdout
        world.drop_task_file()
        result = cli(world, "next")
        assert result.returncode == 0 and "completed platform task" not in result.stdout
        assert result.stdout.splitlines()[0].startswith(world.ref)


# ---------------------------------------------------------------------------------------------
# 4. evidence that survives the log and the parser
# ---------------------------------------------------------------------------------------------

class TestEvidenceSurvival:
    def test_a_verified_run_records_its_digest_and_a_rotated_log_is_not_rot(self, world):
        name = world.plant_run(passed=12)
        assert cli(world, "done", world.ref, "--evidence", f"run:{name}").returncode == 0
        item = world.project_task()["evidence"][0]
        assert item["verified"] and len(item["sha256"]) == 64 and item["counts"] == {
            "collected": 12, "passed": 12, "failed": 0, "errors": 0, "skipped": 0}
        assert "digest recorded" in cli(world, "show", world.ref).stdout
        (world.data / "runs" / name).unlink()                       # the runs dir was rotated
        assert cli(world, "fsck").stdout.strip() == "fsck: clean"

    def test_a_log_that_changed_since_the_close_is_rot(self, world):
        name = world.plant_run(passed=12)
        cli(world, "done", world.ref, "--evidence", f"run:{name}")
        (world.data / "runs" / name).write_text("============ 12 passed, 0 failed in 0.50s ====\nedited\n")
        assert "the log changed since it was recorded" in cli(world, "fsck").stdout

    def test_a_wrapper_log_the_runner_hinted_parser_refuses_still_verifies(self, world):
        """T-1f03553e: the ledger row says runner pytest (source tcount) and the log is run-tests'
        wrapper output; the pytest parser returns None on it since 1.4.1 U3, the generic one reads
        it. Verification tries the recorded runner, then the generic parsers."""
        text = ("$ nice -n 19 run-tests\n"
                "pytest: collected 4991 · passed 4981 · failed 0 · errors 0 · skipped 10 · exit 0\n"
                "log: /tmp/repacted-test-runs/runs/2026-10-01T112524Z-45856-pytest.log\n"
                "run-tests: plugin     pytest: collected 4991 · passed 4981 · failed 0 · errors 0 · skipped 10 · exit 0\n")
        name = world.plant_run(name="2026-10-01T112043Z-37667-tcount.log", log_text=text,
                               ledger_counts={"collected": 4991, "passed": 4981, "failed": 0, "errors": 0, "skipped": 10})
        evidence = load("project_tasks_evidence_refs_under_test", PLUGIN_ROOT / "hooks" / "shared" / "task_log" / "evidence.py")
        from shared.runcounts import parse_counts
        assert parse_counts(text, runner_hint="pytest") is None       # the premise: the hinted parser refuses it
        assert evidence.verify_run(name) == (True, "")
        assert cli(world, "done", world.ref, "--evidence", f"run:{name}").returncode == 0


# ---------------------------------------------------------------------------------------------
# 5. the split shape
# ---------------------------------------------------------------------------------------------

class TestSplit:
    def test_done_with_remaining_closes_on_evidence_and_carries_the_rest(self, world):
        world.store.set(world.ref, "tags", ["security"])
        world.store.set(world.ref, "owner", "lead")
        result = cli(world, "done", world.ref, "--evidence", f"commit:{world.head}",
                     "--remaining", "Owner part: move the PATs out of argv")
        assert result.returncode == 0, result.stderr
        first, second = [line.split()[0] for line in result.stdout.splitlines()]
        state = world.state()
        assert first == world.ref and state.tasks[first]["status"] == "done"
        rest = state.tasks[second]
        assert rest["status"] == "planned" and rest["priority"] == state.tasks[first]["priority"]
        assert rest["tags"] == ["security"] and rest["owner"] == "lead" and rest["note"] == f"remainder of {first}"
        assert {"kind": "reference", "target": f"task:{first}"} in rest["links"]
        assert cli(world, "next").stdout.startswith(second)

    def test_a_remainder_titled_like_an_open_task_is_refused_whole(self, world):
        world.store.add("Already planned")
        result = cli(world, "done", world.ref, "--evidence", f"commit:{world.head}", "--remaining", "already  PLANNED")
        assert result.returncode == 65 and "same title as the remainder" in result.stderr
        assert world.project_task()["status"] == "planned"

    def test_two_splits_at_once_both_land(self, world):
        other = world.store.add("Second task")
        env = {"CLAUDE_PLUGIN_DATA": str(world.data), "REPACT_TASKS_TEST_HOLD_S": "0.5"}
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(
                lambda ref: run_cli(world.repo, "done", ref, "--evidence", f"commit:{world.head}",
                                    "--remaining", f"rest of {ref}", env=env), [world.ref, other]))
        assert [r.returncode for r in results] == [0, 0]
        state = world.state()
        assert sorted(t["title"] for t in state.tasks.values() if t["status"] == "planned") == \
            sorted([f"rest of {world.ref}", f"rest of {other}"])
        assert not state.corrupt and cli(world, "fsck").stdout.strip() == "fsck: clean"
