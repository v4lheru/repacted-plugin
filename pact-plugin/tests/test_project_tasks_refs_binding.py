"""
The second high-risk review (task #139) of the 1.5.0 ref gate and the multi-ref bridge: F2, F12-F15.

  F2  only refs the LEAD bound close: the log's ptask: links, written by `start --ptask` or by the
      lead's own TaskUpdate/TaskCreate metadata (bridge.bind). A ref a teammate writes into
      metadata.refs is reported at acceptance and never closed.
  F12 a disposition is read in any case after NFKC, with `:`, a spaced dash or a full stop;
      `complete` reads as `done`.
  F13 a ref the log already settled is not demanded.
  F14 clearing refs in the HANDOFF write itself does not shed them.
  F15 one ref's refusal does not stop the next ref closing, and the lead is told both.
Boundaries: lookalike letters, zero-width joins, fullwidth, mixed-type ref lists, null, empty, huge.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from project_tasks_support import GIT_ENV, load
from test_project_tasks_bridge import (LEAD_TYPE, SID, TASK_B, World, frame, lead_accepts, run_hook,
                                       teammate_frame)
from test_project_tasks_refs import GATE, HANDOFF, PTASK, RULES, denied, gate_write

RD = load("ref_dispositions_binding_under_test", RULES)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def line_of(result) -> str:
    out = json.loads(result.stdout) if result.stdout.strip() else {}
    return out.get("hookSpecificOutput", {}).get("additionalContext") or ""


def create_frame(world, metadata, agent_type=LEAD_TYPE, agent_id=None, task_id=TASK_B):
    """A PostToolUse(TaskCreate): tool_response.task.id names the new task."""
    payload = frame(world, agent_type=agent_type, agent_id=agent_id, task_id=task_id)
    payload["tool_name"] = "TaskCreate"
    payload["tool_input"] = {"subject": "coder: build", "description": "", "metadata": metadata}
    payload["tool_response"] = {"task": {"id": task_id, "subject": "coder: build"}}
    return payload


def bound(world, ref) -> bool:
    return {"kind": "reference", "target": f"ptask:{PTASK}"} in world.state().tasks[ref]["links"]


# ---------------------------------------------------------------------------------------------
# F2: who binds a ref
# ---------------------------------------------------------------------------------------------

class TestBinding:
    def test_teammate_metadata_refs_never_close_an_unrelated_item(self, world):
        unrelated = world.store.add("Somebody else's item")
        world.store.start(world.ref, ptask=PTASK)
        world.task(refs=[unrelated], handoff={**HANDOFF, "evidence": {"head": world.head},
                                              "refs": {world.ref: "done: x", unrelated: "done"}})
        result = run_hook(lead_accepts(world))
        state = world.state()
        assert state.tasks[unrelated]["status"] != "done" and not state.tasks[unrelated]["accepts"]
        assert state.tasks[world.ref]["status"] == "done"
        assert f"not bound by the lead, left as is: {unrelated}" in line_of(result)

    def test_a_teammate_project_task_write_does_not_bind(self, world):
        run_hook(teammate_frame(world, metadata={"project_task": world.ref, "refs": [world.ref]}))
        assert not bound(world, world.ref)

    def test_the_leads_refs_write_binds_and_the_acceptance_closes_them(self, world):
        second = world.store.add("Second part")
        world.task(status="in_progress")
        run_hook(frame(world, metadata={"refs": [world.ref, second]}))
        assert bound(world, world.ref) and bound(world, second)
        assert world.state().tasks[second]["status"] == "planned"        # refs link; only project_task starts
        world.task(handoff={**HANDOFF, "evidence": {"head": world.head},
                            "refs": {world.ref: "done: a", second: "Done - b"}})
        run_hook(lead_accepts(world))
        assert {world.state().tasks[r]["status"] for r in (world.ref, second)} == {"done"}

    def test_the_leads_task_create_binds_its_dispatch_refs(self, world):
        world.task(status="pending")
        result = run_hook(create_frame(world, {"project_task": world.ref, "refs": [world.ref]}))
        assert bound(world, world.ref) and world.project_task()["status"] == "active"
        assert result.stdout.strip() == ""

    def test_a_teammate_task_create_binds_nothing(self, world):
        world.task(status="pending", owner="")
        run_hook(create_frame(world, {"refs": [world.ref]}, agent_type="coder", agent_id="a0123456789abcdef"))
        assert not bound(world, world.ref)

    def test_a_mixed_type_ref_list_binds_only_well_formed_refs_the_log_holds(self, world):
        world.task(status="in_progress")
        odd = [123, None, {"r": 1}, ["x"], world.ref + "\n", "T-ABCDEF01", "T-00000000", world.ref]
        run_hook(frame(world, metadata={"refs": odd}))
        assert bound(world, world.ref)
        assert [link["target"] for link in world.project_task()["links"]].count(f"ptask:{PTASK}") == 1
        assert RD.metadata_refs({"refs": odd}) == ["T-00000000", world.ref]

    def test_a_task_with_no_refs_is_untouched(self, world):
        before = world.log_lines()
        world.task(handoff={**HANDOFF, "evidence": {"head": world.head}})
        result = run_hook(lead_accepts(world))
        assert world.log_lines() == before and line_of(result) == ""


# ---------------------------------------------------------------------------------------------
# F15: one refusal never costs the next ref
# ---------------------------------------------------------------------------------------------

def test_one_refused_ref_does_not_stop_the_next_and_the_lead_hears_both(world):
    first = world.ref
    world.store.add("child of first", parent=first)
    second = world.store.add("Second part")
    for ref in (first, second):
        world.store.start(ref, ptask=PTASK)
    world.task(handoff={**HANDOFF, "evidence": {"head": world.head},
                        "refs": {first: "done: x", second: "done: y"}})
    result = run_hook(lead_accepts(world))
    state = world.state()
    assert state.tasks[second]["status"] == "done" and state.tasks[first]["status"] == "active"
    line = line_of(result)
    assert f"closed {second}" in line and f"refused: {first} ({first} has open children" in line


# ---------------------------------------------------------------------------------------------
# F12: the disposition grammar
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("done", "done"), ("DONE", "done"), ("done.", "done"), (" Done: tests pass ", "done"),
    ("done - tests pass", "done"), ("done — x", "done"), ("done. x", "done"), ("done:", "done"),
    ("\uff44\uff4f\uff4e\uff45", "done"), ("Partial: the rest", "partial"), ("DEFERRED - later", "deferred"),
    ("dropped: dup", "dropped"),
    ("partial", None), ("partial:", None), ("dropped.", None), ("deferred -", None),
    ("d\u043ene", None),                     # Cyrillic o
    ("d\u200bone", None),                   # zero-width space inside the verb
    ("done-ish", None), ("done done", None), ("not done", None), ("complete: x", "done"), ("Completed", "done"),
    ("donex", None), ("", None), ("   ", None), (None, None), (1, None), (["done"], None),
    ("done: " + "x" * 200_000, "done"),
])
def test_the_disposition_grammar(value, expected):
    assert RD.verb(value) == expected


@pytest.mark.parametrize("value", ["Done: tests pass", "DONE", "done.", "done - tests pass", "complete: x"])
def test_case_and_punctuation_variants_pass_the_gate(world, value):
    world.store.start(world.ref, ptask=PTASK)
    world.task(status="in_progress")
    assert not denied(gate_write(world, {**HANDOFF, "refs": {world.ref: value}}))


def test_a_lookalike_done_is_returned(world):
    world.store.start(world.ref, ptask=PTASK)
    world.task(status="in_progress")
    assert denied(gate_write(world, {**HANDOFF, "refs": {world.ref: "d\u043ene"}}))


# ---------------------------------------------------------------------------------------------
# F13, F14: what the gate demands
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("settle", ["drop", "done"])
def test_a_settled_ref_is_not_demanded(world, settle):
    world.store.start(world.ref, ptask=PTASK)
    if settle == "drop":
        world.store.drop(world.ref)
    else:
        world.store.done(world.ref, [f"commit:{world.head}"])
    world.task(status="in_progress")
    assert not denied(gate_write(world, dict(HANDOFF)))


@pytest.mark.parametrize("cleared", [None, [], "", {}])
def test_clearing_refs_in_the_handoff_write_does_not_escape(world, cleared):
    other = world.store.add("Carried by metadata only")
    world.task(status="in_progress", refs=[other])
    payload = {"tool_name": "TaskUpdate", "session_id": SID, "cwd": str(world.repo), "agent_type": "coder",
               "agent_id": "a0123456789abcdef",
               "tool_input": {"taskId": TASK_B, "metadata": {"handoff": dict(HANDOFF), "refs": cleared,
                                                             "project_task": cleared}}}
    result = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload), capture_output=True,
                            text=True, env={**os.environ, **GIT_ENV}, timeout=60)
    assert denied(result)


def test_a_correct_multi_ref_handoff_passes(world):
    second = world.store.add("Second part")
    for ref in (world.ref, second):
        world.store.start(ref, ptask=PTASK)
    world.task(status="in_progress", refs=[second])
    assert not denied(gate_write(world, {**HANDOFF, "refs": {world.ref: "done", second: "deferred: owner"}}))


def test_a_huge_ref_list_is_returned_with_a_capped_reason(world):
    many = [f"T-{n:08x}" for n in range(500)]
    world.task(status="in_progress", refs=many)
    result = gate_write(world, dict(HANDOFF))
    assert denied(result)
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "... and 480 more" in reason and reason.count("\n") < 40
