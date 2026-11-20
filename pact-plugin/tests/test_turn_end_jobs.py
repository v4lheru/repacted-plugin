"""
Location: pact-plugin/tests/test_turn_end_jobs.py
Summary: Which running background_tasks entries count as jobs for each role
         (hooks/shared/turn_end_jobs.py).
Used by: the pact-plugin test suite.
"""

from pathlib import Path

import pytest

from shared import turn_end_jobs as jobs

SOURCE = Path(__file__).resolve().parent.parent / "hooks" / "shared" / "turn_end_jobs.py"

# Every label the platform's background_tasks builder produces.
EVERY_LABEL = (
    "shell", "subagent", "monitor", "workflow", "MCP task",
    "teammate", "dream", "auto-mode scan", "cloud session",
)
NEVER_COUNTED = frozenset({"teammate", "dream", "auto-mode scan", "cloud session"})

# Labels captured live in the probe window's background_tasks frames.
CAPTURED_LABELS = ("shell", "subagent", "teammate")

EXPECTED = {
    "lead": {"shell"},
    "separate-process teammate": {"shell", "subagent", "monitor", "workflow", "MCP task"},
    "in-process teammate": {"shell"},
}
ROLE_SETS = {
    "lead": jobs.LEAD_JOB_TYPES,
    "separate-process teammate": jobs.OWN_PROCESS_JOB_TYPES,
    "in-process teammate": jobs.IN_PROCESS_TEAMMATE_JOB_TYPES,
}


def frame_with(labels) -> dict:
    return {"background_tasks": [
        {"id": f"b-{label}", "type": label, "status": "running"} for label in labels
    ]}


@pytest.mark.parametrize("role", list(sorted(EXPECTED))[:1])
def test_each_role_counts_exactly_its_allowlist(role):
    frame = frame_with(EVERY_LABEL + ("future-type", "local_bash"))
    counted = {e["type"] for e in jobs.running_jobs(frame, ROLE_SETS[role])}
    assert counted == EXPECTED[role]


def test_the_union_is_every_role_set():
    assert jobs.ANY_JOB_TYPES == set().union(*EXPECTED.values())
    frame = frame_with(EVERY_LABEL)
    assert {e["type"] for e in jobs.running_jobs(frame)} == jobs.ANY_JOB_TYPES


