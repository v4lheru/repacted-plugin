#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/distill_enqueue.py (KB full)
Summary: SessionEnd: when the profile sets distill.transcripts to true, queue the ended lead
session's id for repact-distill (shared/distill_queue.py), so the next `repact-distill run` or
dry-run takes that session's transcript first. It reads the hook stdin only: the transcript is
never opened or read, no subprocess starts, no model is called and the distiller does not run.
Used by: hooks/dispatch_registry.py (native check `distill_enqueue:dispatch_check` on
SessionEnd); `python3 hooks/distill_enqueue.py` runs the same check alone.

Lead or not: queue when `agent_type` is absent or a lead spelling
(pact_context.is_lead), so a plain solo session queues too. `agent_id` is not read: no hook keys
a role on it (tests/test_lead_discriminator_invariant.py; it is absent under tmux). A
separate-process teammate's SessionEnd carries its `pact-<specialist>` agent_type (inferred:
HOOK_STDIN_DISCRIMINATORS.md); a transcript under `subagents/` is skipped as session_ledger
skips it. A misread frame costs order only: the distiller's R0 view reads lead transcripts only.
Fail-open: any error is counted in queue-errors.json (its type only) and the session ends.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path, PurePath
from typing import Optional

if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

_SUPPRESS = json.dumps({"suppressOutput": True})
MAX_STDIN = 1 << 20


def transcripts_on(profile: dict) -> bool:
    distill = profile.get("distill") if isinstance(profile, dict) else None
    return isinstance(distill, dict) and distill.get("transcripts") is True


def is_lead_end(payload: dict) -> bool:
    """True when the frame is a lead's (or a solo session's) SessionEnd."""
    from shared.pact_context import is_lead

    if payload.get("agent_type") not in (None, "") and not is_lead(payload):
        return False
    transcript = payload.get("transcript_path")
    return not (isinstance(transcript, str) and PurePath(transcript).parent.name == "subagents")


def enqueue_ended(payload: dict, profile: Optional[dict] = None) -> str:
    """The outcome: off, skipped, invalid, queued or duplicate. Raises on an I/O failure."""
    from shared import distill_queue as queue
    from shared.plugin_paths import profile as read_profile

    if not transcripts_on(read_profile() if profile is None else profile):
        return "off"
    if not is_lead_end(payload):
        return "skipped"
    session_id = payload.get("session_id")
    if not queue.valid_id(session_id):
        return "invalid"
    return "queued" if queue.enqueue(session_id) else "duplicate"


def dispatch_check(payload: dict, ctx=None):
    """The dispatcher entry. Decides nothing: returns None whatever happens."""
    try:
        if isinstance(payload, dict):
            enqueue_ended(payload)
    except Exception as error:  # noqa: BLE001 - never block or slow a session end
        from shared.distill_queue import note_error

        note_error(type(error).__name__)
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read(MAX_STDIN) or "{}")
    except ValueError:
        payload = None
    dispatch_check(payload if isinstance(payload, dict) else {})
    print(_SUPPRESS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
