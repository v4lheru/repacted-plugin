"""Contract, ONE LEAN RULE: the persona (§11) and comPACT state the same single-task rule.

A dispatch whose complexity total is 6 or less gets the work task alone. comPACT used to ask for a
teachback pair for every specialist, so the lead had to reconcile two procedures; this pins the
same phrase in both files so they cannot drift apart again.
"""
from __future__ import annotations

from tests.contract._surfaces import PLUGIN_ROOT, read

LEAN_RULE = ("a dispatch whose complexity total is 6 or less is single-task dispatch "
             "(the work task alone: no teachback task, no `addBlockedBy`)")


def test_persona_and_compact_state_the_same_lean_rule():
    for rel_path in ("agents/pact-orchestrator.md", "commands/comPACT.md"):
        assert LEAN_RULE in read(PLUGIN_ROOT / rel_path), rel_path
