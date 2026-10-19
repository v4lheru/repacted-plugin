"""
The ref gate and the multi-ref bridge at their boundary: who may close a ref, how a disposition
reads, and one ref's refusal beside another's close.
(The HANDOFF gate rows were removed in 2.1.0: the carried-refs check is `repact submit`'s,
tests/test_ops_flow.py.)

  - only refs the LEAD bound close: the log's ptask: links, written by `start --ptask` or by the
      lead's own dispatch metadata (bridge.bind). A ref a teammate writes into
      metadata.refs is reported at acceptance and never closed.
  - a disposition is read in any case after NFKC, with `:`, a spaced dash or a full stop;
      `complete` reads as `done`.
  - one ref's refusal does not stop the next ref closing, and the lead is told both.
Boundaries: lookalike letters, zero-width joins, fullwidth, mixed-type ref lists, null, empty, huge.
"""
from __future__ import annotations

import json

import pytest

from project_tasks_support import load
from test_project_tasks_bridge import (LEAD_TYPE, TASK_B, World, frame, lead_accepts, run_hook)
from test_project_tasks_refs import HANDOFF, PTASK, RULES

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
        run_hook(create_frame(world, {"project_task": world.ref, "refs": [world.ref]}))
        assert bound(world, world.ref) and world.project_task()["status"] == "active"

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
