#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/teams_turn_end.py
Summary: SubagentStop check, Agent Teams only: refuses an in-process teammate's turn end, once
    per job, over background work it started and left with nothing to follow it up
    (shared/turn_end_gate.py, the decision stop_background_gate.py makes on Stop). The
    HANDOFF backstop is subagent_stop.py.
Used by: hooks/dispatch_registry.py (native check `teams_turn_end:dispatch_check`,
    teams_only); `main` runs it alone when the dispatcher runs a deeply nested input apart.

The gate is imported only when a job of a counted type is running, so an ordinary turn end
pays nothing for it. Under stop_hook_active the gate itself lets the stop land
(allow_loop_guard). Any error decides nothing: a turn-end nudge is no security check.
"""

from __future__ import annotations

import json
import sys


def verdict_of(payload: dict):
    """The turn-end gate's verdict, or None when no counted job runs or the gate fails."""
    try:
        from shared.turn_end_jobs import running_jobs

        if not running_jobs(payload):
            return None
        from shared import turn_end_gate

        return turn_end_gate.evaluate(payload)
    except Exception:  # noqa: BLE001 - a nudge that cannot run decides nothing
        return None


def _settle(verdict) -> None:
    """Mark the named jobs told (a blocking verdict only) and write the trace row."""
    try:
        from shared import turn_end_gate

        turn_end_gate.mark_told(verdict)
        turn_end_gate.write_trace(verdict)
    except Exception:  # noqa: BLE001 - telemetry never changes the decision
        pass


def dispatch_check(payload: dict, ctx=None):
    """The dispatcher entry: a block naming the unfollowed jobs, or None."""
    import dispatch_registry as registry

    verdict = verdict_of(payload)
    if verdict is None:
        return None
    _settle(verdict)
    if verdict.blocks and verdict.reason:
        return registry.Result(decision="block", reason=verdict.reason)
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (ValueError, RecursionError):
        payload = None
    verdict = verdict_of(payload) if isinstance(payload, dict) else None
    if verdict is not None:
        _settle(verdict)
    if verdict is not None and verdict.blocks and verdict.reason:
        print(json.dumps({"decision": "block", "reason": verdict.reason}))
    else:
        print(json.dumps({"suppressOutput": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
