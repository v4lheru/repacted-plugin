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
from concurrent.futures import ThreadPoolExecutor

import pytest

from project_tasks_support import PLUGIN_ROOT, load, run_cli
from test_project_tasks_bridge import TASK_B, TEAM, World, lead_accepts, run_hook

RULES = PLUGIN_ROOT / "hooks" / "shared" / "ref_dispositions.py"
PTASK = f"{TEAM}/{TASK_B}"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def cli(world, *args):
    """repact-task in the world's repo, with the world's plugin data (the run ledger)."""
    return run_cli(world.repo, *args, env={"CLAUDE_PLUGIN_DATA": str(world.data)})


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


# ---------------------------------------------------------------------------------------------
# 2. the disposition grammar of carried refs
# ---------------------------------------------------------------------------------------------

class TestRefGate:
    """The HANDOFF check on carried refs moved into `repact submit` in 2.1.0 (shared/ops/submit.py);
    tests/test_ops_flow.py proves it there. The grammar it reads stays here."""

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


# ---------------------------------------------------------------------------------------------
# 4. evidence that survives the log and the parser
# ---------------------------------------------------------------------------------------------

class TestEvidenceSurvival:
    @pytest.mark.slow
    def test_a_verified_run_records_its_digest_and_a_rotated_log_is_not_rot(self, world):
        name = world.plant_run(passed=12)
        assert cli(world, "done", world.ref, "--evidence", f"run:{name}").returncode == 0
        item = world.project_task()["evidence"][0]
        assert item["verified"] and len(item["sha256"]) == 64 and item["counts"] == {
            "collected": 12, "passed": 12, "failed": 0, "errors": 0, "skipped": 0}
        assert "digest recorded" in cli(world, "show", world.ref).stdout
        (world.data / "runs" / name).unlink()                       # the runs dir was rotated
        assert cli(world, "fsck").stdout.strip() == "fsck: clean"


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


