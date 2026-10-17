"""The protocol target names are a contract: every H2 anchor of build plan §5 exists exactly once.

the protocol consolidation collapsed the protocol files into these targets, and every later package points at
their H2 headings by name (links, hook messages, persona pointers). A renamed or duplicated
heading silently breaks those pointers, so each anchor must appear as a whole line exactly once
in its file. lead-reference.md is PERSONA's (m5).
"""
from __future__ import annotations

import pytest

from tests.contract._surfaces import PLUGIN_ROOT, read

ANCHORS = {
    "protocols/algedonic.md": ["## Signals", "## Lead-Side HALT Fan-Out", "## S5 Escalation"],
    "protocols/completion.md": ["## Completion Authority", "## Teachback Review", "## Rejection Flow",
                                "## Crossed-Wake Idles", "## Directive-Reflection Check",
                                "## Read-Trigger Precondition"],
    "protocols/workflows.md": ["## Workflow Selection", "## Variety", "## Phase Transitions",
                               "## Scope Detection", "## Scoped Phases", "## S3/S4 Tension",
                               "## Specialist Autonomy"],
    "protocols/coordination.md": ["## Concurrent Dispatch", "## Task Hierarchy", "## Stall Detection",
                                  "## Completeness Signals", "## Concurrent Audit"],
    "protocols/recovery.md": ["## Recovery Steps"],
    "protocols/lead-reference.md": ["## Wait Instruments", "## Dispatch Tables",
                                    "## Method Reconstruction Examples", "## Harvest Triggers",
                                    "## Working Memory Propagation", "## Message Delivery"],
}
CASES = [(path, anchor) for path, anchors in ANCHORS.items() for anchor in anchors]


@pytest.mark.parametrize("path, anchor", CASES, ids=[f"{p}|{a}" for p, a in CASES])
def test_anchor_is_one_whole_line(path, anchor):
    lines = [line.rstrip() for line in read(PLUGIN_ROOT / path).splitlines()]
    assert lines.count(anchor) == 1, f"{path}: {anchor!r} appears {lines.count(anchor)}x as a line"


def test_each_target_has_only_its_plan_anchors_at_h2():
    """A new H2 in a target file is a new public name; add it to the plan first."""
    for path, anchors in ANCHORS.items():
        h2 = [line.rstrip() for line in read(PLUGIN_ROOT / path).splitlines() if line.startswith("## ")]
        assert h2 == anchors, f"{path}: H2 headings {h2} differ from plan §5 {anchors}"
